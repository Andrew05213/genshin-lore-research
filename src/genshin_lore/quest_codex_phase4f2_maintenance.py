"""Independent read-only release verifier for the Phase 4F2 canary."""
from __future__ import annotations
import hashlib,json,os,sqlite3,stat
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from .util import canonical_json,digest_id
from .quest_codex_phase4f2_contract import EXPECTED_MANIFEST_SHA256,EXPECTED_OBSERVED_AT,FAMILY,FAMILY_CONTRACT_VERSION,RUN_FAMILY,VERSIONS,derive_source_contract,expected_identity,load_manifest
from .quest_codex_phase4f2_release_oracle import exact_occurrence_tables
from .quest_codex_phase4f2_schema import TABLE_SQL,verify_schema_13
from .world_archive_phase4e2_schema import verify_schema_12
from .world_archive_phase4e2_promotion import assert_sidecars_absent

class QuestCodexPhase4F2VerificationError(RuntimeError):pass
def _fail(message:str)->None:raise QuestCodexPhase4F2VerificationError(message)
def _fingerprint(path:Path)->dict[str,Any]:
    assert_sidecars_absent(path,"during QuestCodex verification"); before=os.lstat(path)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):_fail("database must be a regular non-symlink file")
    digest=hashlib.sha256(path.read_bytes()).hexdigest(); after=os.lstat(path)
    if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):_fail("database changed while fingerprinting")
    return {"sha256":digest,"size_bytes":after.st_size,"mtime_ns":after.st_mtime_ns}
def _scope(manifest:dict[str,Any])->str:return canonical_json({"family":RUN_FAMILY,"provider_family":FAMILY,"family_contract_version":FAMILY_CONTRACT_VERSION,"manifest_id":manifest["manifest_id"],"selected_ids":list(manifest["boundary"]["selected_ids"]),"exact_ingest_run_required":True})
def _config(manifest:dict[str,Any])->str:return canonical_json({"manifest_raw_sha256":EXPECTED_MANIFEST_SHA256,"versions":manifest["versions"],"logical_identity_created":False,"full_quest_codex_scan":False,"corpus_complete":False,"mcp_provider_added":False})
def _quote(name:str)->str:return '"'+name.replace('"','""')+'"'
def _migration_0013(conn:sqlite3.Connection)->dict[str,Any]:
    row=conn.execute("SELECT version,applied_at FROM schema_migration WHERE version=13").fetchone()
    if row is None or int(row[0])!=13 or not isinstance(row[1],str) or not row[1]:
        _fail("migration 0013 ledger tuple missing")
    try:parsed=datetime.fromisoformat(row[1])
    except ValueError as exc:raise QuestCodexPhase4F2VerificationError("migration 0013 applied_at is not ISO-8601") from exc
    if parsed.tzinfo is None:_fail("migration 0013 applied_at must be timezone-aware")
    return {"version":13,"applied_at":row[1]}
def _quest_codex_run_ids(conn:sqlite3.Connection)->list[str]:
    result=[]
    for run_id,scope_json in conn.execute("SELECT id,scope_json FROM ingest_run ORDER BY id"):
        try:scope=json.loads(scope_json)
        except (TypeError,json.JSONDecodeError):_fail("invalid ingest run scope JSON")
        if isinstance(scope,dict) and (scope.get("provider_family")==FAMILY or (str(scope.get("family",""))==FAMILY or str(scope.get("family","")).startswith(f"{FAMILY}_"))):result.append(str(run_id))
    return result
def assert_exact_provenance(conn:sqlite3.Connection, source_root:str|Path, manifest:dict[str,Any], derived:dict[str,Any], run_identity:dict[str,str])->None:
    """Check the complete sealed F2 provenance tuple, independently of its builder."""
    expected_run=(run_identity["snapshot_id"],run_identity["parser_build_id"],_scope(manifest),_config(manifest),"complete",EXPECTED_OBSERVED_AT)
    actual_run=conn.execute("SELECT snapshot_id,parser_build_id,scope_json,config_json,status,created_at FROM ingest_run WHERE id=?",(run_identity["ingest_run_id"],)).fetchone()
    if actual_run is None or tuple(actual_run)!=expected_run:_fail("F2 exact ingest_run provenance mismatch")
    parser=(VERSIONS["parser"],VERSIONS["projection"],"quest-codex-phase4f2-sealed-canary",run_identity["config_checksum"],EXPECTED_OBSERVED_AT)
    actual_parser=conn.execute("SELECT parser_version,mapping_version,fingerprint,config_checksum,created_at FROM parser_build WHERE id=?",(run_identity["parser_build_id"],)).fetchone()
    if actual_parser is None or tuple(actual_parser)!=parser:_fail("F2 exact parser_build provenance mismatch")
    if len(derived["files"])!=9 or len({x["relative_path"] for x in derived["files"]})!=9:_fail("F2 source_file provenance set mismatch")
    source_root=Path(source_root).resolve()
    for item in derived["files"]:
        file_id=digest_id("sf",run_identity["snapshot_id"],item["relative_path"])
        expected_file=(run_identity["snapshot_id"],item["relative_path"],item["blob_oid"],item["sha256"],item["size_bytes"],item["format"],str(source_root))
        actual_file=conn.execute("SELECT snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path FROM source_file WHERE id=?",(file_id,)).fetchone()
        if actual_file is None or tuple(actual_file)!=expected_file:_fail("F2 exact source_file provenance mismatch")
    if len(derived["source_records"])!=61 or len({(x["relative_path"],x["source_pointer"]) for x in derived["source_records"]})!=61:_fail("F2 source_record provenance set mismatch")
    for item in derived["source_records"]:
        file_id=digest_id("sf",run_identity["snapshot_id"],item["relative_path"]); record_id=digest_id("sr",file_id,item["source_pointer"])
        expected_record=(file_id,item["source_pointer"],item["record_kind"],item["native_id"],item["raw_checksum"],canonical_json(item["raw_value"]))
        actual_record=conn.execute("SELECT source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json FROM source_record WHERE id=?",(record_id,)).fetchone()
        if actual_record is None or tuple(actual_record)!=expected_record:_fail("F2 exact source_record provenance mismatch")
def _baseline_preservation(conn:sqlite3.Connection,baseline:Path,identity:dict[str,str],derived:dict[str,Any],migration:dict[str,Any])->dict[str,int]:
    conn.execute("ATTACH DATABASE ? AS baseline",(f"file:{baseline}?mode=ro&immutable=1",))
    try:
        tables=[str(x[0]) for x in conn.execute("SELECT name FROM baseline.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        actual=[str(x[0]) for x in conn.execute("SELECT name FROM main.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        if set(actual)!=set(tables)|set(TABLE_SQL):_fail("Schema 13 user-table inventory is not exact baseline plus QuestCodex tables")
        additive={"schema_migration","parser_build","ingest_run","source_file","source_record"}; protected=0
        for table in tables:
            if table in additive:continue
            q=_quote(table)
            if conn.execute(f"SELECT 1 FROM (SELECT * FROM baseline.{q} EXCEPT SELECT * FROM main.{q}) LIMIT 1").fetchone() or conn.execute(f"SELECT 1 FROM (SELECT * FROM main.{q} EXCEPT SELECT * FROM baseline.{q}) LIMIT 1").fetchone():_fail(f"baseline protected table changed: {table}")
            protected+=1
        for table in additive:
            q=_quote(table)
            if conn.execute(f"SELECT 1 FROM (SELECT * FROM baseline.{q} EXCEPT SELECT * FROM main.{q}) LIMIT 1").fetchone():_fail(f"baseline additive table lost rows: {table}")
        expected={
          "parser_build":{identity["parser_build_id"]},"ingest_run":{identity["ingest_run_id"]},
          "source_file":{digest_id("sf",identity["snapshot_id"],x["relative_path"]) for x in derived["files"]},
          "source_record":{digest_id("sr",digest_id("sf",identity["snapshot_id"],x["relative_path"]),x["source_pointer"]) for x in derived["source_records"]},
        }
        ledger_extras=[tuple(x) for x in conn.execute("SELECT version,applied_at FROM main.schema_migration EXCEPT SELECT version,applied_at FROM baseline.schema_migration ORDER BY version,applied_at")]
        if ledger_extras!=[(migration["version"],migration["applied_at"])]:_fail("unapproved additive rows in schema_migration")
        key={"parser_build":"id","ingest_run":"id","source_file":"id","source_record":"id"}
        for table in additive-{"schema_migration"}:
            q=_quote(table); column=_quote(key[table]); extras={str(x[0]) for x in conn.execute(f"SELECT {column} FROM main.{q} EXCEPT SELECT {column} FROM baseline.{q}")}
            if not extras<=expected[table]:_fail(f"unapproved additive rows in {table}")
        return {"protected_exact_tables":protected,"additive_subset_tables":len(additive),"baseline_user_tables":len(tables)}
    finally:conn.execute("DETACH DATABASE baseline")

def verify(db_path:str|Path,baseline_path:str|Path,report_path:str|Path,source_root:str|Path,repo_root:str|Path,manifest_path:str|Path)->dict[str,Any]:
    path=Path(os.path.abspath(os.fspath(db_path))); baseline=Path(os.path.abspath(os.fspath(baseline_path)))
    before=_fingerprint(path); baseline_before=_fingerprint(baseline); manifest=load_manifest(manifest_path); derived=derive_source_contract(source_root,repo_root,manifest); identity=expected_identity(manifest)
    try:
      with sqlite3.connect(baseline.as_uri()+"?mode=ro&immutable=1",uri=True) as baseline_conn:
        baseline_conn.execute("PRAGMA query_only=ON"); verify_schema_12(baseline_conn)
    except sqlite3.Error as exc:_fail(f"invalid exact Schema 12 baseline: {exc}")
    try: report=json.loads(Path(report_path).read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc:_fail(f"invalid build report: {exc}")
    migration_report=report.get("schema_migration_0013") if isinstance(report,dict) else None
    if not isinstance(migration_report,dict):_fail("build report migration ledger tuple missing")
    expected_report={"schema_migration_0013":migration_report,"status":"complete","schema_version":13,"family":FAMILY,"family_contract_version":FAMILY_CONTRACT_VERSION,"snapshot_id":identity["snapshot_id"],"parser_build_id":identity["parser_build_id"],"ingest_run_id":identity["ingest_run_id"],"config_checksum":identity["config_checksum"],"counts":derived["counts"],"status_counts":derived["status_counts"],"occurrence_set_digests":derived["occurrence_set_digests"],"coverage":"bounded_canary","corpus_complete":False,"evidence_only":True,"mcp_provider_added":False,"db_path":str(path.resolve()),"database_sha256":before["sha256"]}
    if report!=expected_report:_fail("build report mismatch")
    try:
      with sqlite3.connect(path.as_uri()+"?mode=ro&immutable=1",uri=True) as conn:
        conn.execute("PRAGMA query_only=ON"); verify_schema_13(conn)
        migration=_migration_0013(conn)
        if migration_report!=migration:_fail("build report migration ledger tuple mismatch")
        run=conn.execute("SELECT snapshot_id,parser_build_id,scope_json,config_json,status,created_at FROM ingest_run WHERE id=?",(identity["ingest_run_id"],)).fetchone()
        if run is None or tuple(run)!=(identity["snapshot_id"],identity["parser_build_id"],_scope(manifest),_config(manifest),"complete",EXPECTED_OBSERVED_AT):_fail("exact QuestCodex ingest run mismatch")
        if _quest_codex_run_ids(conn)!=[identity["ingest_run_id"]]:_fail("QuestCodex stage run set mismatch")
        parser=conn.execute("SELECT parser_version,mapping_version,fingerprint,config_checksum,created_at FROM parser_build WHERE id=?",(identity["parser_build_id"],)).fetchone()
        if parser is None or tuple(parser)!=(VERSIONS["parser"],VERSIONS["projection"],"quest-codex-phase4f2-sealed-canary",identity["config_checksum"],EXPECTED_OBSERVED_AT):_fail("parser build mismatch")
        source=manifest["source"]; snapshot=conn.execute('SELECT source_id,release_label,build,"commit",tree,observed_at,status,metadata_json FROM source_snapshot WHERE id=?',(identity["snapshot_id"],)).fetchone(); wanted=(source["source_id"],source["release_label"],source["build"],source["commit"],source["tree"],source["observed_at"],"active")
        if snapshot is None or tuple(snapshot)[:7]!=wanted or not isinstance(json.loads(str(snapshot[7])),dict):_fail("parser-independent snapshot mismatch")
        for item in derived["files"]:
            fid=digest_id("sf",identity["snapshot_id"],item["relative_path"]); row=conn.execute("SELECT snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path FROM source_file WHERE id=?",(fid,)).fetchone(); wanted_file=(identity["snapshot_id"],item["relative_path"],item["blob_oid"],item["sha256"],item["size_bytes"],item["format"],str(Path(source_root).resolve()))
            if row is None or tuple(row)!=wanted_file:_fail("source file mismatch")
        for item in derived["source_records"]:
            fid=digest_id("sf",identity["snapshot_id"],item["relative_path"]); rid=digest_id("sr",fid,item["source_pointer"]); row=conn.execute("SELECT source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json FROM source_record WHERE id=?",(rid,)).fetchone(); wanted_record=(fid,item["source_pointer"],item["record_kind"],item["native_id"],item["raw_checksum"],canonical_json(item["raw_value"]))
            if row is None or tuple(row)!=wanted_record:_fail("source record mismatch")
        for table,(columns,wanted_rows) in exact_occurrence_tables(derived,identity).items():
            actual=[tuple(x) for x in conn.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY id")]
            if actual!=sorted(wanted_rows,key=lambda x:str(x[0])):_fail(f"exact business tuple mismatch: {table}")
        checks=(("quest_codex_relationship_occurrence","resolution_status",derived["status_counts"]["relationships"]),("quest_codex_opaque_occurrence","resolution_status",derived["status_counts"]["opaque"]),("quest_codex_text_resolution_occurrence","resolution_status",derived["status_counts"]["text_resolutions"]),("quest_codex_asset_reference_occurrence","reference_status",derived["status_counts"]["assets"]))
        for table,column,wanted_counts in checks:
            actual_counts=dict(sorted(Counter(str(x[0]) for x in conn.execute(f"SELECT {column} FROM {table}")).items()))
            if actual_counts!=wanted_counts:_fail(f"status matrix mismatch: {table}")
        if conn.execute("PRAGMA foreign_key_check").fetchall():_fail("foreign key check failed")
        preservation=_baseline_preservation(conn,baseline,identity,derived,migration)
    except sqlite3.Error as exc:_fail(f"read-only verification failed: {exc}")
    after=_fingerprint(path); baseline_after=_fingerprint(baseline)
    if after!=before or baseline_after!=baseline_before:_fail("verifier modified database or baseline")
    return {"status":"pass","oracle":"frozen_phase4f1_source_coordinate_and_exact_sql_v1","family_contract_version":FAMILY_CONTRACT_VERSION,"ingest_run_id":identity["ingest_run_id"],"counts":derived["counts"],"status_counts":derived["status_counts"],"baseline_preservation":preservation,"database_fingerprint":after,"baseline_fingerprint":baseline_after}
