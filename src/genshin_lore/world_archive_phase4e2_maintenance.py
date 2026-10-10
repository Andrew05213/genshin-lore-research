"""Independent read-only release verifier for the Phase 4E2 canary."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from collections import Counter
from pathlib import Path
from typing import Any

from .util import canonical_json, digest_id
from .world_archive_phase4e2_contract import (
    EXPECTED_OBSERVED_AT,
    EXPECTED_MANIFEST_SHA256,
    FAMILY_CONTRACT_VERSION,
    RUN_FAMILY,
    VERSIONS,
    derive_source_contract,
    expected_identity,
    load_manifest,
    occurrence_id,
)
from .world_archive_phase4e2_schema import verify_schema_12
from .world_archive_phase4e2_release_oracle import exact_occurrence_tables


class WorldArchivePhase4E2VerificationError(RuntimeError):
    pass


def _fail(message: str) -> None:
    raise WorldArchivePhase4E2VerificationError(message)


def _fingerprint(path: Path) -> dict[str, Any]:
    before = os.lstat(path)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        _fail("World Archive database must be a regular non-symlink file")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    after = os.lstat(path)
    if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
        _fail("World Archive database changed while fingerprinting")
    return {"sha256":digest,"size_bytes":after.st_size,"mtime_ns":after.st_mtime_ns}


def _scope(manifest: dict[str, Any]) -> str:
    return canonical_json({"family":RUN_FAMILY,"provider_family":"world_archive","family_contract_version":FAMILY_CONTRACT_VERSION,"manifest_id":manifest["manifest_id"],"selected_rows":manifest["boundary"]["selected_rows"],"exact_ingest_run_required":True})


def _config(manifest: dict[str, Any]) -> str:
    return canonical_json({"manifest_raw_sha256":EXPECTED_MANIFEST_SHA256,"versions":manifest["versions"],"logical_identity_created":False,"full_world_archive_scan":False,"corpus_complete":False,"mcp_provider_added":False})


def verify(db_path:str|Path,report_path:str|Path,source_root:str|Path,repo_root:str|Path,manifest_path:str|Path)->dict[str,Any]:
    path=Path(os.path.abspath(os.fspath(db_path)))
    for suffix in ("-journal","-wal","-shm"):
        if Path(str(path)+suffix).exists(): _fail("World Archive database sidecars are forbidden")
    before=_fingerprint(path)
    manifest=load_manifest(manifest_path)
    derived=derive_source_contract(source_root,repo_root,manifest)
    identity=expected_identity(manifest)
    try:
        report=json.loads(Path(report_path).read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc:
        _fail(f"invalid World Archive build report: {exc}")
    expected_report={"status":"complete","schema_version":12,"family":"world_archive","family_contract_version":FAMILY_CONTRACT_VERSION,"snapshot_id":identity["snapshot_id"],"parser_build_id":identity["parser_build_id"],"ingest_run_id":identity["ingest_run_id"],"config_checksum":identity["config_checksum"],"counts":derived["counts"],"status_counts":derived["status_counts"],"occurrence_set_digests":derived["occurrence_set_digests"],"coverage":"bounded_canary","corpus_complete":False,"evidence_only":True,"db_path":str(path.resolve()),"database_sha256":before["sha256"]}
    if report!=expected_report: _fail("World Archive build report mismatch")
    uri=path.as_uri()+"?mode=ro&immutable=1"
    try:
        with sqlite3.connect(uri,uri=True) as conn:
            conn.execute("PRAGMA query_only=ON")
            verify_schema_12(conn)
            run=conn.execute("SELECT snapshot_id,parser_build_id,scope_json,config_json,status,created_at FROM ingest_run WHERE id=?",(identity["ingest_run_id"],)).fetchone()
            if run is None or tuple(run)!=(identity["snapshot_id"],identity["parser_build_id"],_scope(manifest),_config(manifest),"complete",EXPECTED_OBSERVED_AT): _fail("exact World Archive ingest run mismatch")
            family_runs=[]
            for run_id,scope_json in conn.execute("SELECT id,scope_json FROM ingest_run"):
                try: scope=json.loads(scope_json)
                except (TypeError,json.JSONDecodeError): _fail("invalid ingest run scope JSON")
                if isinstance(scope,dict) and scope.get("family")==RUN_FAMILY: family_runs.append(str(run_id))
            if family_runs!=[identity["ingest_run_id"]]: _fail("World Archive same-family run set mismatch")
            parser=conn.execute("SELECT parser_version,mapping_version,fingerprint,config_checksum,created_at FROM parser_build WHERE id=?",(identity["parser_build_id"],)).fetchone()
            source=manifest["source"]
            catalog=conn.execute("SELECT kind,url,license_note FROM source_catalog WHERE id=?",(source["source_id"],)).fetchone()
            if catalog is None or tuple(catalog)!=("primary",source["upstream"],None): _fail("World Archive source_catalog mismatch")
            snapshot=conn.execute('SELECT source_id,release_label,build,"commit",tree,observed_at,status,metadata_json FROM source_snapshot WHERE id=?',(identity["snapshot_id"],)).fetchone()
            accepted_metadata={canonical_json({"sealed_source_manifest":True}),canonical_json({"sealed_source_manifest":True,"phase4e2_world_archive_canary":True})}
            wanted_snapshot=(source["source_id"],source["release_label"],source["build"],source["commit"],source["tree"],source["observed_at"],"active")
            if snapshot is None or tuple(snapshot)[:7]!=wanted_snapshot or tuple(snapshot)[7] not in accepted_metadata: _fail("World Archive parser-independent snapshot mismatch")
            for item in derived["files"]:
                fid=digest_id("sf",identity["snapshot_id"],item["relative_path"])
                row=conn.execute("SELECT snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path FROM source_file WHERE id=?",(fid,)).fetchone()
                wanted=(identity["snapshot_id"],item["relative_path"],item["blob_oid"],item["sha256"],item["size_bytes"],item["format"],str(Path(source_root).resolve()))
                if row is None or tuple(row)!=wanted: _fail(f"World Archive source_file mismatch: {item['relative_path']}")
            if parser is None or tuple(parser)!=(VERSIONS["parser"],VERSIONS["projection"],"world-archive-phase4e2-sealed-canary",identity["config_checksum"],EXPECTED_OBSERVED_AT): _fail("World Archive parser build mismatch")
            ident={"snapshot_id":identity["snapshot_id"],"parser_build_id":identity["parser_build_id"],"ingest_run_id":identity["ingest_run_id"]}
            expected_ids={
                "world_archive_row_occurrence":{occurrence_id("row",ident,x["table"],x["row_index"],x["pointer"]) for x in derived["rows"]},
                "world_archive_raw_field_occurrence":{occurrence_id("raw-field",ident,x["table"],x["row_index"],x["source_pointer"]) for x in derived["raw_fields"]},
                "world_archive_relationship_occurrence":{occurrence_id("relationship",ident,x["table"],x["row_index"],x["source_pointer"]) for x in derived["relationships"]},
                "world_archive_opaque_occurrence":{occurrence_id("opaque",ident,x["table"],x["row_index"],x["source_pointer"]) for x in derived["opaque"]},
                "world_archive_text_owner_occurrence":{occurrence_id("text-owner",ident,x["owner_key"]) for x in derived["text_owners"]},
                "world_archive_text_resolution_occurrence":{occurrence_id("text-resolution",ident,x["owner_key"],x["locale"]) for x in derived["text_resolutions"]},
                "world_archive_text_candidate_occurrence":{occurrence_id("text-candidate",ident,x["owner_key"],x["locale"],x["shard_kind"],x["source_pointer"]) for x in derived["text_candidates"]},
                "world_archive_asset_reference_occurrence":{occurrence_id("asset",ident,x["table"],x["row_index"],x["source_pointer"]) for x in derived["assets"]},
            }
            for table,wanted in expected_ids.items():
                rows=conn.execute(f"SELECT id,ingest_run_id,snapshot_id,parser_build_id FROM {table}").fetchall()
                if {str(row[0]) for row in rows}!=wanted or any(tuple(row[1:])!=(identity["ingest_run_id"],identity["snapshot_id"],identity["parser_build_id"]) for row in rows): _fail(f"World Archive physical occurrence set mismatch: {table}")
            checks=(("world_archive_relationship_occurrence","resolution_status",derived["status_counts"]["relationships"]),("world_archive_opaque_occurrence","resolution_status",derived["status_counts"]["opaque"]),("world_archive_text_resolution_occurrence","resolution_status",derived["status_counts"]["text_resolutions"]),("world_archive_asset_reference_occurrence","reference_status",derived["status_counts"]["assets"]))
            for table,column,wanted in checks:
                actual=dict(sorted(Counter(str(row[0]) for row in conn.execute(f"SELECT {column} FROM {table}")).items()))
                if actual!=wanted: _fail(f"World Archive status matrix mismatch: {table}")
            # Independent provenance coordinates: every physical primary/text
            # record is rederived from source and must exist exactly.
            for item in derived["source_records"]:
                fid=digest_id("sf",identity["snapshot_id"],item["relative_path"]); rid=digest_id("sr",fid,item["source_pointer"])
                row=conn.execute("SELECT source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json FROM source_record WHERE id=?",(rid,)).fetchone()
                wanted=(fid,item["source_pointer"],item["record_kind"],item["native_id"],item["raw_checksum"],canonical_json(item["raw_value"]))
                if row is None or tuple(row)!=wanted: _fail("World Archive source record mismatch")
            if conn.execute("PRAGMA foreign_key_check").fetchall(): _fail("World Archive foreign key check failed")
            for table,(columns,wanted_rows) in exact_occurrence_tables(derived,identity).items():
                actual=[tuple(row) for row in conn.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY id")]
                wanted=sorted(wanted_rows,key=lambda row:str(row[0]))
                if actual!=wanted: _fail(f"World Archive exact business tuple mismatch: {table}")
    except sqlite3.Error as exc:
        _fail(f"World Archive read-only verification failed: {exc}")
    after=_fingerprint(path)
    if after!=before: _fail("World Archive verifier modified database")
    return {"status":"pass","oracle":"frozen_phase4e1_source_coordinate_and_exact_sql_v1","family_contract_version":FAMILY_CONTRACT_VERSION,"ingest_run_id":identity["ingest_run_id"],"counts":derived["counts"],"status_counts":derived["status_counts"],"database_fingerprint":after}
