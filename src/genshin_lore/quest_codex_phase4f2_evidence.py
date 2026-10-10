"""Atomic family-specific Schema 13 QuestCodex bounded canary builder."""
from __future__ import annotations
import hashlib, json, os, sqlite3, stat, tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .db import Database
from .phase4e5_combined_gate import assert_path_matrix
from .util import canonical_json, digest_id
from .world_archive_phase4e2_promotion import assert_sidecars_absent, write_report_atomically
from .world_archive_phase4e2_stable_lock import promote_database
from .quest_codex_phase4f2_contract import (
    EXPECTED_MANIFEST_SHA256, EXPECTED_OBSERVED_AT, FAMILY, FAMILY_CONTRACT_VERSION,
    RUN_FAMILY, VERSIONS, derive_source_contract, expected_identity, load_manifest,
    occurrence_id,
)
from .quest_codex_phase4f2_schema import verify_schema_13

class QuestCodexPhase4F2EvidenceError(RuntimeError): pass
_PROMOTION_TEST_HOOK=None
_POST_REPLACE_TEST_HOOK=None

@dataclass(frozen=True)
class Preflight:
    source_root:Path; repo_root:Path; manifest:dict[str,Any]; derived:dict[str,Any]
    snapshot_id:str; parser_build_id:str; ingest_run_id:str; config_checksum:str

def _fail(message:str)->None: raise QuestCodexPhase4F2EvidenceError(message)
def preflight(source_root:str|Path,repo_root:str|Path,manifest_path:str|Path)->Preflight:
    paths=assert_path_matrix({"source_root":source_root,"repo_root":repo_root,"manifest":manifest_path},protected_database_names=(),input_files=("manifest",),input_directories=("source_root","repo_root"))
    manifest=load_manifest(paths["manifest"]); derived=derive_source_contract(paths["source_root"],paths["repo_root"],manifest)
    return Preflight(paths["source_root"],paths["repo_root"],manifest,derived,**expected_identity(manifest))
def _scope(p:Preflight)->str:
    return canonical_json({"family":RUN_FAMILY,"provider_family":FAMILY,"family_contract_version":FAMILY_CONTRACT_VERSION,"manifest_id":p.manifest["manifest_id"],"selected_ids":list(p.manifest["boundary"]["selected_ids"]),"exact_ingest_run_required":True})
def _config(p:Preflight)->str:
    return canonical_json({"manifest_raw_sha256":EXPECTED_MANIFEST_SHA256,"versions":p.manifest["versions"],"logical_identity_created":False,"full_quest_codex_scan":False,"corpus_complete":False,"mcp_provider_added":False})
def _fingerprint(path:Path)->tuple[int,int,int,int,str]:
    before=os.lstat(path)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode): _fail("database must be a regular non-symlink file")
    descriptor=os.open(path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
    try:
        opened=os.fstat(descriptor); digest=hashlib.sha256()
        while chunk:=os.read(descriptor,1024*1024): digest.update(chunk)
        after_open=os.fstat(descriptor)
    finally: os.close(descriptor)
    after=os.lstat(path); identity=(before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)
    if identity!=(opened.st_dev,opened.st_ino,opened.st_size,opened.st_mtime_ns) or identity!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns) or identity!=(after_open.st_dev,after_open.st_ino,after_open.st_size,after_open.st_mtime_ns): _fail("database changed while fingerprinting")
    return (*identity,digest.hexdigest())
def _ids(p:Preflight)->dict[str,dict[Any,str]]:
    ident={"snapshot_id":p.snapshot_id,"parser_build_id":p.parser_build_id,"ingest_run_id":p.ingest_run_id}
    return {
      "rows":{(x["table"],x["row_index"]):occurrence_id("row",ident,x["table"],x["row_index"],x["pointer"]) for x in p.derived["rows"]},
      "relationships":{x["relationship_key"]:occurrence_id("relationship",ident,x["relationship_key"]) for x in p.derived["relationships"]},
      "owners":{x["owner_key"]:occurrence_id("text-owner",ident,x["owner_key"]) for x in p.derived["text_owners"]},
      "resolutions":{(x["owner_key"],x["locale"]):occurrence_id("text-resolution",ident,x["owner_key"],x["locale"]) for x in p.derived["text_resolutions"]},
    }
def _ensure(conn:sqlite3.Connection,table:str,columns:tuple[str,...],values:tuple[Any,...],insert:bool)->None:
    row=conn.execute(f"SELECT {','.join(columns)} FROM {table} WHERE id=?",(values[0],)).fetchone()
    if row is None:
        if not insert: raise sqlite3.IntegrityError(f"missing {table} occurrence")
        conn.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",values)
    elif tuple(row)!=values: raise sqlite3.IntegrityError(f"immutable {table} occurrence mismatch")

TABLES={"rows":"quest_codex_row_occurrence","raw_fields":"quest_codex_raw_field_occurrence","relationships":"quest_codex_relationship_occurrence","relationship_candidates":"quest_codex_relationship_candidate_occurrence","opaque":"quest_codex_opaque_occurrence","text_owners":"quest_codex_text_owner_occurrence","text_resolutions":"quest_codex_text_resolution_occurrence","text_candidates":"quest_codex_text_candidate_occurrence","assets":"quest_codex_asset_reference_occurrence"}

def _migration_0013(conn:sqlite3.Connection)->dict[str,Any]:
    row=conn.execute("SELECT version,applied_at FROM schema_migration WHERE version=13").fetchone()
    if row is None or int(row[0])!=13 or not isinstance(row[1],str) or not row[1]: raise sqlite3.IntegrityError("migration 0013 ledger tuple missing")
    try: parsed=datetime.fromisoformat(row[1])
    except ValueError as exc: raise sqlite3.IntegrityError("migration 0013 applied_at is not ISO-8601") from exc
    if parsed.tzinfo is None: raise sqlite3.IntegrityError("migration 0013 applied_at must be timezone-aware")
    return {"version":13,"applied_at":row[1]}

def _quest_codex_run_ids(conn:sqlite3.Connection)->list[str]:
    result=[]
    for run_id,scope_json in conn.execute("SELECT id,scope_json FROM ingest_run ORDER BY id"):
        try: scope=json.loads(scope_json)
        except (TypeError,json.JSONDecodeError) as exc: raise sqlite3.IntegrityError("invalid ingest run scope JSON") from exc
        if isinstance(scope,dict) and (scope.get("provider_family")==FAMILY or (str(scope.get("family",""))==FAMILY or str(scope.get("family","")).startswith(f"{FAMILY}_"))): result.append(str(run_id))
    return result

def _write_occurrences(conn:sqlite3.Connection,p:Preflight,insert:bool)->None:
    ids=_ids(p); ident={"snapshot_id":p.snapshot_id,"parser_build_id":p.parser_build_id,"ingest_run_id":p.ingest_run_id}
    file_ids={x["relative_path"]:digest_id("sf",p.snapshot_id,x["relative_path"]) for x in p.derived["files"]}
    record_ids={(x["relative_path"],x["source_pointer"]):digest_id("sr",file_ids[x["relative_path"]],x["source_pointer"]) for x in p.derived["source_records"]}
    row_records={(x["table"],x["row_index"]):record_ids[(x["relative_path"],x["pointer"])] for x in p.derived["rows"]}
    for x in p.derived["rows"]:
        key=(x["table"],x["row_index"]); _ensure(conn,TABLES["rows"],("id","ingest_run_id","snapshot_id","parser_build_id","source_file_id","source_record_id","source_pointer","evidence_kind","table_name","row_index","native_locator_value","raw_checksum","metadata_json"),(ids["rows"][key],p.ingest_run_id,p.snapshot_id,p.parser_build_id,file_ids[x["relative_path"]],row_records[key],x["pointer"],x["evidence_kind"],x["table"],x["row_index"],x["native_locator_value"],x["raw_checksum"],canonical_json({"physical_occurrence":True,"logical_identity_created":False})),insert)
    for x in p.derived["raw_fields"]:
        key=(x["table"],x["row_index"]); oid=occurrence_id("raw-field",ident,*key,x["source_pointer"])
        _ensure(conn,TABLES["raw_fields"],("id","ingest_run_id","snapshot_id","parser_build_id","row_occurrence_id","source_record_id","source_pointer","field_name","field_presence","raw_json","metadata_json"),(oid,p.ingest_run_id,p.snapshot_id,p.parser_build_id,ids["rows"][key],row_records[key],x["source_pointer"],x["field_name"],x["field_presence"],None if x["raw_value"] is None else canonical_json(x["raw_value"]),canonical_json({"absence_is_evidence":x["field_presence"]=="missing","identity_effect":"none"})),insert)
    for x in p.derived["relationships"]:
        key=(x["table"],x["row_index"]); _ensure(conn,TABLES["relationships"],("id","ingest_run_id","snapshot_id","parser_build_id","source_row_occurrence_id","source_record_id","source_pointer","field_name","raw_value_json","target_table","target_field","resolution_status","metadata_json"),(ids["relationships"][x["relationship_key"]],p.ingest_run_id,p.snapshot_id,p.parser_build_id,ids["rows"][key],row_records[key],x["source_pointer"],x["field_name"],canonical_json(x["raw_value"]),x["target_table"],x["target_field"],x["resolution_status"],canonical_json({"candidate_identity_effect":"none","missing_is_legal":True})),insert)
    for x in p.derived["relationship_candidates"]:
        rid=ids["relationships"][x["relationship_key"]]; oid=occurrence_id("relationship-candidate",ident,x["relationship_key"],x["candidate_ordinal"],x["source_relative_path"],x["source_pointer"])
        _ensure(conn,TABLES["relationship_candidates"],("id","ingest_run_id","snapshot_id","parser_build_id","relationship_occurrence_id","source_file_id","source_record_id","source_pointer","candidate_ordinal","target_table","target_field","target_raw_value_json","raw_checksum","metadata_json"),(oid,p.ingest_run_id,p.snapshot_id,p.parser_build_id,rid,file_ids[x["source_relative_path"]],record_ids[(x["source_relative_path"],x["source_pointer"])],x["source_pointer"],x["candidate_ordinal"],x["target_table"],x["target_field"],canonical_json(x["target_raw_value"]),x["raw_checksum"],canonical_json({"physical_lookup_candidate":True,"logical_parent_created":False})),insert)
    for x in p.derived["opaque"]:
        key=(x["table"],x["row_index"]); oid=occurrence_id("opaque",ident,*key,x["source_pointer"])
        _ensure(conn,TABLES["opaque"],("id","ingest_run_id","snapshot_id","parser_build_id","source_row_occurrence_id","source_record_id","source_pointer","field_name","array_index","raw_value_json","resolution_status","metadata_json"),(oid,p.ingest_run_id,p.snapshot_id,p.parser_build_id,ids["rows"][key],row_records[key],x["source_pointer"],x["field_name"],x["array_index"],canonical_json(x["raw_value"]),"opaque_unresolved",canonical_json({"inference_performed":False,"identity_effect":"none"})),insert)
    for x in p.derived["text_owners"]:
        key=(x["table"],x["row_index"]); _ensure(conn,TABLES["text_owners"],("id","ingest_run_id","snapshot_id","parser_build_id","source_row_occurrence_id","source_record_id","source_pointer","field_name","field_role","textmap_hash","raw_value_json","metadata_json"),(ids["owners"][x["owner_key"]],p.ingest_run_id,p.snapshot_id,p.parser_build_id,ids["rows"][key],row_records[key],x["source_pointer"],x["field_name"],x["field_role"],x["textmap_hash"],canonical_json(x["raw_value"]),canonical_json({"hash_identity_effect":"none","independent_owner_occurrence":True})),insert)
    for x in p.derived["text_resolutions"]:
        key=(x["table"],x["row_index"]); _ensure(conn,TABLES["text_resolutions"],("id","ingest_run_id","snapshot_id","parser_build_id","owner_occurrence_id","source_row_occurrence_id","source_record_id","source_pointer","locale","resolution_status","raw_text","normalized_text","raw_checksum","normalized_checksum","metadata_json"),(ids["resolutions"][(x["owner_key"],x["locale"])],p.ingest_run_id,p.snapshot_id,p.parser_build_id,ids["owners"][x["owner_key"]],ids["rows"][key],row_records[key],x["source_pointer"],x["locale"],x["resolution_status"],x["raw_text"],x["normalized_text"],x["raw_checksum"],x["normalized_checksum"],canonical_json({"missing_is_legal":True,"text_identity_effect":"none"})),insert)
    for x in p.derived["text_candidates"]:
        oid=occurrence_id("text-candidate",ident,x["owner_key"],x["locale"],x["shard_kind"],x["source_pointer"])
        _ensure(conn,TABLES["text_candidates"],("id","ingest_run_id","snapshot_id","parser_build_id","resolution_occurrence_id","source_file_id","source_record_id","source_pointer","shard_kind","locale","raw_text","normalized_text","raw_checksum","normalized_checksum","metadata_json"),(oid,p.ingest_run_id,p.snapshot_id,p.parser_build_id,ids["resolutions"][(x["owner_key"],x["locale"])],file_ids[x["source_relative_path"]],record_ids[(x["source_relative_path"],x["source_pointer"])],x["source_pointer"],x["shard_kind"],x["locale"],x["raw_text"],x["normalized_text"],x["raw_checksum"],x["normalized_checksum"],canonical_json({"candidate_identity_effect":"none","owner_key":x["owner_key"]})),insert)
    for x in p.derived["assets"]:
        key=(x["table"],x["row_index"]); oid=occurrence_id("asset",ident,*key,x["source_pointer"])
        _ensure(conn,TABLES["assets"],("id","ingest_run_id","snapshot_id","parser_build_id","source_row_occurrence_id","source_record_id","source_pointer","field_name","asset_kind","raw_reference","reference_status","metadata_json"),(oid,p.ingest_run_id,p.snapshot_id,p.parser_build_id,ids["rows"][key],row_records[key],x["source_pointer"],x["field_name"],x["asset_kind"],x["raw_reference"],x["reference_status"],canonical_json({"asset_identity_created":False,"file_existence_claimed":False})),insert)

def _write_bundle(conn:sqlite3.Connection,p:Preflight)->None:
    source=p.manifest["source"]
    catalog=conn.execute("SELECT kind,url,license_note FROM source_catalog WHERE id=?",(source["source_id"],)).fetchone()
    if catalog is None: conn.execute("INSERT INTO source_catalog(id,kind,url,license_note) VALUES(?,?,?,NULL)",(source["source_id"],"primary",source["upstream"]))
    elif tuple(catalog)!=("primary",source["upstream"],None): raise sqlite3.IntegrityError("source_catalog mismatch")
    snap=conn.execute('SELECT source_id,release_label,build,"commit",tree,observed_at,status,metadata_json FROM source_snapshot WHERE id=?',(p.snapshot_id,)).fetchone(); wanted=(source["source_id"],source["release_label"],source["build"],source["commit"],source["tree"],source["observed_at"],"active")
    if snap is None: conn.execute('INSERT INTO source_snapshot(id,source_id,release_label,build,"commit",tree,observed_at,parser_version,mapping_version,status,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(p.snapshot_id,*wanted[:6],"legacy-unscoped","legacy-unscoped",wanted[6],canonical_json({"sealed_source_manifest":True})))
    elif tuple(snap)[:7]!=wanted or not isinstance(json.loads(str(snap[7])),dict): raise sqlite3.IntegrityError("parser-independent source_snapshot mismatch")
    file_ids={}
    for x in p.derived["files"]:
        fid=digest_id("sf",p.snapshot_id,x["relative_path"]); file_ids[x["relative_path"]]=fid; wanted_file=(p.snapshot_id,x["relative_path"],x["blob_oid"],x["sha256"],x["size_bytes"],x["format"],str(p.source_root)); row=conn.execute("SELECT snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path FROM source_file WHERE id=?",(fid,)).fetchone()
        if row is None: conn.execute("INSERT INTO source_file(id,snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path) VALUES(?,?,?,?,?,?,?,?)",(fid,*wanted_file))
        elif tuple(row)!=wanted_file: raise sqlite3.IntegrityError("source_file mismatch")
    for x in p.derived["source_records"]:
        fid=file_ids[x["relative_path"]]; rid=digest_id("sr",fid,x["source_pointer"]); wanted_record=(fid,x["source_pointer"],x["record_kind"],x["native_id"],x["raw_checksum"],canonical_json(x["raw_value"])); row=conn.execute("SELECT source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json FROM source_record WHERE id=?",(rid,)).fetchone()
        if row is None: conn.execute("INSERT INTO source_record(id,source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json) VALUES(?,?,?,?,?,?,?)",(rid,fid,x["source_pointer"],x["record_kind"],x["native_id"],x["raw_checksum"],canonical_json(x["raw_value"])))
        elif tuple(row)!=wanted_record: raise sqlite3.IntegrityError("source_record physical coordinate mismatch")
    parser=(VERSIONS["parser"],VERSIONS["projection"],"quest-codex-phase4f2-sealed-canary",p.config_checksum,EXPECTED_OBSERVED_AT); row=conn.execute("SELECT parser_version,mapping_version,fingerprint,config_checksum,created_at FROM parser_build WHERE id=?",(p.parser_build_id,)).fetchone()
    if row is None: conn.execute("INSERT INTO parser_build(id,parser_version,mapping_version,fingerprint,config_checksum,created_at) VALUES(?,?,?,?,?,?)",(p.parser_build_id,*parser))
    elif tuple(row)!=parser: raise sqlite3.IntegrityError("parser_build mismatch")
    if conn.execute("SELECT 1 FROM ingest_run WHERE id=?",(p.ingest_run_id,)).fetchone(): raise sqlite3.IntegrityError("ingest run already exists")
    conn.execute("INSERT INTO ingest_run(id,snapshot_id,parser_build_id,scope_json,config_json,status,created_at) VALUES(?,?,?,?,?,'running',?)",(p.ingest_run_id,p.snapshot_id,p.parser_build_id,_scope(p),_config(p),EXPECTED_OBSERVED_AT)); _write_occurrences(conn,p,True); conn.execute("UPDATE ingest_run SET status='complete' WHERE id=?",(p.ingest_run_id,))

def verify_open_connection(conn:sqlite3.Connection,p:Preflight)->None:
    verify_schema_13(conn)
    _migration_0013(conn)
    if _quest_codex_run_ids(conn)!=[p.ingest_run_id]:
        raise sqlite3.IntegrityError("QuestCodex stage run set mismatch")
    run=conn.execute("SELECT snapshot_id,parser_build_id,scope_json,config_json,status,created_at FROM ingest_run WHERE id=?",(p.ingest_run_id,)).fetchone()
    if run is None or tuple(run)!=(p.snapshot_id,p.parser_build_id,_scope(p),_config(p),"complete",EXPECTED_OBSERVED_AT): raise sqlite3.IntegrityError("exact QuestCodex ingest run mismatch")
    _write_occurrences(conn,p,False)
    for key,table in TABLES.items():
        scoped=int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE ingest_run_id=?",(p.ingest_run_id,)).fetchone()[0])
        total=int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        if total!=scoped: raise sqlite3.IntegrityError(f"foreign QuestCodex rows present: {table}")
        if scoped!=p.derived["counts"][key]: raise sqlite3.IntegrityError(f"occurrence count mismatch: {table}")
    if conn.execute("PRAGMA foreign_key_check").fetchall(): raise sqlite3.IntegrityError("foreign key check failed")

def _report(path:Path,p:Preflight)->dict[str,Any]:
    with sqlite3.connect(path.as_uri()+"?mode=ro",uri=True) as conn: migration=_migration_0013(conn)
    return {"schema_migration_0013":migration,"status":"complete","schema_version":13,"family":FAMILY,"family_contract_version":FAMILY_CONTRACT_VERSION,"snapshot_id":p.snapshot_id,"parser_build_id":p.parser_build_id,"ingest_run_id":p.ingest_run_id,"config_checksum":p.config_checksum,"counts":p.derived["counts"],"status_counts":p.derived["status_counts"],"occurrence_set_digests":p.derived["occurrence_set_digests"],"coverage":"bounded_canary","corpus_complete":False,"evidence_only":True,"mcp_provider_added":False,"db_path":str(path.resolve()),"database_sha256":_fingerprint(path)[4]}

def ingest(db_path:str|Path,source_root:str|Path,repo_root:str|Path,manifest_path:str|Path)->dict[str,Any]:
    target=Path(os.path.abspath(os.fspath(db_path))); assert_sidecars_absent(target,"before QuestCodex build")
    paths=assert_path_matrix({"database":target,"source_root":source_root,"repo_root":repo_root,"manifest":manifest_path},protected_database_names=("database",),input_files=("database","manifest"),input_directories=("source_root","repo_root")); target=paths["database"]
    original=_fingerprint(target); p=preflight(paths["source_root"],paths["repo_root"],paths["manifest"])
    with sqlite3.connect(target.as_uri()+"?mode=ro",uri=True) as ro:
        versions=[int(x[0]) for x in ro.execute("SELECT version FROM schema_migration ORDER BY version")]
        if versions not in (list(range(1,13)),list(range(1,14))): _fail("input schema must be exact 12 or 13")
        existing=ro.execute("SELECT 1 FROM ingest_run WHERE id=?",(p.ingest_run_id,)).fetchone() is not None
        if versions[-1]==13:
            verify_schema_13(ro); _migration_0013(ro)
            if existing: verify_open_connection(ro,p)
            elif _quest_codex_run_ids(ro) or any(int(ro.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in TABLES.values()): _fail("Schema 13 input contains a foreign QuestCodex stage")
        elif existing: _fail("Schema 12 input cannot contain the F2 ingest run")
    if existing: return _report(target,p)
    descriptor,name=tempfile.mkstemp(prefix=f".{target.name}.phase4f2-",suffix=".sqlite",dir=target.parent); os.close(descriptor); staging=Path(name)
    try:
        with sqlite3.connect(target.as_uri()+"?mode=ro",uri=True) as source,sqlite3.connect(staging) as destination: source.backup(destination)
        with Database(staging) as db:
            if versions[-1]==12: db.initialize_through(13)
            verify_schema_13(db.conn); db.conn.execute("BEGIN IMMEDIATE"); _write_bundle(db.conn,p); db.conn.commit()
        with sqlite3.connect(staging.as_uri()+"?mode=ro",uri=True) as ro: verify_open_connection(ro,p)
        if _fingerprint(target)!=original: _fail("database changed before promotion")
        os.chmod(staging,stat.S_IMODE(os.lstat(target).st_mode)); promote_database(target,staging,original,_PROMOTION_TEST_HOOK,_POST_REPLACE_TEST_HOOK)
        assert_sidecars_absent(target,"after QuestCodex promotion")
    except Exception as exc:
        if isinstance(exc,QuestCodexPhase4F2EvidenceError): raise
        _fail(f"QuestCodex Phase 4F2 atomic build failed: {exc}")
    finally:
        for item in (staging,Path(f"{staging}-journal"),Path(f"{staging}-wal"),Path(f"{staging}-shm")):
            try:item.unlink()
            except FileNotFoundError:pass
    return _report(target,p)
