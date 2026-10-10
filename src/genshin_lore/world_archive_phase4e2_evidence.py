"""Atomic family-specific Schema 12 World Archive bounded canary builder."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import Database, MIGRATIONS_DIR
from .util import canonical_json, digest_id
from .world_archive_phase4e2_contract import (
    EXPECTED_OBSERVED_AT,
    EXPECTED_MANIFEST_SHA256,
    EXPECTED_SNAPSHOT_ID,
    FAMILY_CONTRACT_VERSION,
    RUN_FAMILY,
    VERSIONS,
    derive_source_contract,
    expected_identity,
    load_manifest,
    occurrence_id,
)
from .world_archive_phase4e2_schema import MIGRATION_0012_SHA256, verify_schema_12
from .world_archive_phase4e2_promotion import write_report_atomically, assert_report_path_safe
from .world_archive_phase4e2_stable_lock import promote_database


class WorldArchivePhase4E2EvidenceError(RuntimeError):
    pass

_POST_REPLACE_TEST_HOOK = None
_PROMOTION_TEST_HOOK = None

@dataclass(frozen=True)
class Preflight:
    source_root: Path
    repo_root: Path
    manifest: dict[str, Any]
    derived: dict[str, Any]
    snapshot_id: str
    parser_build_id: str
    ingest_run_id: str
    config_checksum: str


def _fail(message: str) -> None:
    raise WorldArchivePhase4E2EvidenceError(message)


def preflight(source_root: str | Path, repo_root: str | Path, manifest_path: str | Path) -> Preflight:
    manifest = load_manifest(manifest_path)
    derived = derive_source_contract(source_root, repo_root, manifest)
    identity = expected_identity(manifest)
    return Preflight(Path(source_root).resolve(), Path(repo_root).resolve(), manifest, derived, **identity)


def _scope(p: Preflight) -> str:
    return canonical_json({"family": RUN_FAMILY, "provider_family": "world_archive", "family_contract_version": FAMILY_CONTRACT_VERSION, "manifest_id": p.manifest["manifest_id"], "selected_rows": p.manifest["boundary"]["selected_rows"], "exact_ingest_run_required": True})


def _config(p: Preflight) -> str:
    return canonical_json({"manifest_raw_sha256": EXPECTED_MANIFEST_SHA256, "versions": p.manifest["versions"], "logical_identity_created": False, "full_world_archive_scan": False, "corpus_complete": False, "mcp_provider_added": False})


def _ids(p: Preflight) -> dict[str, dict[Any, str]]:
    ident = {"snapshot_id": p.snapshot_id, "parser_build_id": p.parser_build_id, "ingest_run_id": p.ingest_run_id}
    return {
        "rows": {(x["table"], x["row_index"]): occurrence_id("row", ident, x["table"], x["row_index"], x["pointer"]) for x in p.derived["rows"]},
        "raw_fields": {(x["table"], x["row_index"], x["source_pointer"]): occurrence_id("raw-field", ident, x["table"], x["row_index"], x["source_pointer"]) for x in p.derived["raw_fields"]},
        "relationships": {x["source_pointer"]: occurrence_id("relationship", ident, x["table"], x["row_index"], x["source_pointer"]) for x in p.derived["relationships"]},
        "opaque": {x["source_pointer"]: occurrence_id("opaque", ident, x["table"], x["row_index"], x["source_pointer"]) for x in p.derived["opaque"]},
        "owners": {x["owner_key"]: occurrence_id("text-owner", ident, x["owner_key"]) for x in p.derived["text_owners"]},
        "resolutions": {(x["owner_key"], x["locale"]): occurrence_id("text-resolution", ident, x["owner_key"], x["locale"]) for x in p.derived["text_resolutions"]},
        "assets": {x["source_pointer"]: occurrence_id("asset", ident, x["table"], x["row_index"], x["source_pointer"]) for x in p.derived["assets"]},
    }


def _ensure(conn: sqlite3.Connection, table: str, columns: tuple[str, ...], values: tuple[Any, ...], insert: bool) -> None:
    row = conn.execute(f"SELECT {','.join(columns)} FROM {table} WHERE id=?", (values[0],)).fetchone()
    if row is None:
        if not insert:
            raise sqlite3.IntegrityError(f"missing {table} occurrence")
        conn.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", values)
    elif tuple(row) != values:
        raise sqlite3.IntegrityError(f"immutable {table} occurrence mismatch")


def _write_occurrences(conn: sqlite3.Connection, p: Preflight, insert: bool) -> None:
    ids = _ids(p)
    file_ids = {x["relative_path"]: digest_id("sf", p.snapshot_id, x["relative_path"]) for x in p.derived["files"]}
    record_ids = {(x["relative_path"], x["source_pointer"]): digest_id("sr", file_ids[x["relative_path"]], x["source_pointer"]) for x in p.derived["source_records"]}
    row_records = {(x["table"], x["row_index"]): record_ids[(x["relative_path"], x["pointer"])] for x in p.derived["rows"]}
    for x in p.derived["rows"]:
        key = (x["table"], x["row_index"])
        _ensure(conn, "world_archive_row_occurrence", ("id","ingest_run_id","snapshot_id","parser_build_id","source_file_id","source_record_id","source_pointer","table_name","row_index","native_locator_field","native_locator_value","raw_checksum","metadata_json"), (ids["rows"][key],p.ingest_run_id,p.snapshot_id,p.parser_build_id,file_ids[x["relative_path"]],row_records[key],x["pointer"],x["table"],x["row_index"],"id",x["native_locator_value"],x["raw_checksum"],canonical_json({"physical_occurrence":True,"logical_identity_created":False})), insert)
    for x in p.derived["raw_fields"]:
        key=(x["table"],x["row_index"])
        _ensure(conn,"world_archive_raw_field_occurrence",("id","ingest_run_id","snapshot_id","parser_build_id","row_occurrence_id","source_record_id","source_pointer","field_name","field_kind","raw_json","metadata_json"),(ids["raw_fields"][(x["table"],x["row_index"],x["source_pointer"])],p.ingest_run_id,p.snapshot_id,p.parser_build_id,ids["rows"][key],row_records[key],x["source_pointer"],x["field_name"],x["field_kind"],canonical_json(x["raw_value"]),canonical_json({"identity_effect":"none"})),insert)
    for x in p.derived["relationships"]:
        key=(x["table"],x["row_index"])
        _ensure(conn,"world_archive_relationship_occurrence",("id","ingest_run_id","snapshot_id","parser_build_id","source_row_occurrence_id","source_record_id","source_pointer","field_name","relation_kind","raw_value_json","candidate_key_json","resolution_status","target_table","target_field","candidate_scopes_json","metadata_json"),(ids["relationships"][x["source_pointer"]],p.ingest_run_id,p.snapshot_id,p.parser_build_id,ids["rows"][key],row_records[key],x["source_pointer"],x["field_name"],x["relation_kind"],None if x["raw_value"] is None else canonical_json(x["raw_value"]),None if x["candidate_key"] is None else canonical_json(x["candidate_key"]),x["resolution_status"],x["target_table"],x["target_field"],canonical_json(x["candidate_scopes"]),canonical_json({"mapping_basis":x["mapping_basis"],"target_is_candidate_only":True})),insert)
    for x in p.derived["opaque"]:
        key=(x["table"],x["row_index"])
        _ensure(conn,"world_archive_opaque_occurrence",("id","ingest_run_id","snapshot_id","parser_build_id","source_row_occurrence_id","source_record_id","source_pointer","field_name","array_index","raw_value_json","resolution_status","reason","metadata_json"),(ids["opaque"][x["source_pointer"]],p.ingest_run_id,p.snapshot_id,p.parser_build_id,ids["rows"][key],row_records[key],x["source_pointer"],x["field_name"],x["array_index"],canonical_json(x["raw_value"]),"opaque_unresolved",x["reason"],canonical_json({"identity_effect":"none","inference_performed":False})),insert)
    for x in p.derived["text_owners"]:
        key=(x["table"],x["row_index"])
        _ensure(conn,"world_archive_text_owner_occurrence",("id","ingest_run_id","snapshot_id","parser_build_id","source_row_occurrence_id","source_record_id","source_pointer","field_name","field_role","textmap_hash","raw_value_json","metadata_json"),(ids["owners"][x["owner_key"]],p.ingest_run_id,p.snapshot_id,p.parser_build_id,ids["rows"][key],row_records[key],x["source_pointer"],x["field_name"],x["field_role"],str(x["textmap_hash"]),canonical_json(x["raw_value"]),canonical_json({"hash_identity_effect":"none","independent_owner_occurrence":True})),insert)
    owner_by_key={x["owner_key"]:x for x in p.derived["text_owners"]}
    for x in p.derived["text_resolutions"]:
        key=(x["table"],x["row_index"]); owner=owner_by_key[x["owner_key"]]
        _ensure(conn,"world_archive_text_resolution_occurrence",("id","ingest_run_id","snapshot_id","parser_build_id","owner_occurrence_id","source_row_occurrence_id","source_record_id","source_pointer","locale","textmap_hash","resolution_status","raw_text","normalized_text","raw_checksum","normalized_checksum","candidate_scopes_json","metadata_json"),(ids["resolutions"][(x["owner_key"],x["locale"])],p.ingest_run_id,p.snapshot_id,p.parser_build_id,ids["owners"][x["owner_key"]],ids["rows"][key],row_records[key],owner["source_pointer"],x["locale"],str(x["textmap_hash"]),x["resolution_status"],x["raw_text"],x["normalized_text"],x["raw_checksum"],x["normalized_checksum"],canonical_json(x["candidate_scopes"]),canonical_json({"missing_is_legal":True,"text_identity_effect":"none"})),insert)
    for x in p.derived["text_candidates"]:
        rid=ids["resolutions"][(x["owner_key"],x["locale"])]
        cid=occurrence_id("text-candidate",{"snapshot_id":p.snapshot_id,"parser_build_id":p.parser_build_id,"ingest_run_id":p.ingest_run_id},x["owner_key"],x["locale"],x["shard_kind"],x["source_pointer"])
        _ensure(conn,"world_archive_text_candidate_occurrence",("id","ingest_run_id","snapshot_id","parser_build_id","resolution_occurrence_id","source_file_id","source_record_id","source_pointer","shard_kind","locale","raw_text","normalized_text","raw_checksum","normalized_checksum","metadata_json"),(cid,p.ingest_run_id,p.snapshot_id,p.parser_build_id,rid,file_ids[x["source_relative_path"]],record_ids[(x["source_relative_path"],x["source_pointer"])],x["source_pointer"],x["shard_kind"],x["locale"],x["raw_text"],x["normalized_text"],x["raw_checksum"],x["normalized_checksum"],canonical_json({"candidate_identity_effect":"none","owner_key":x["owner_key"]})),insert)
    for x in p.derived["assets"]:
        key=(x["table"],x["row_index"])
        _ensure(conn,"world_archive_asset_reference_occurrence",("id","ingest_run_id","snapshot_id","parser_build_id","source_row_occurrence_id","source_record_id","source_pointer","field_name","asset_kind","raw_reference","reference_status","metadata_json"),(ids["assets"][x["source_pointer"]],p.ingest_run_id,p.snapshot_id,p.parser_build_id,ids["rows"][key],row_records[key],x["source_pointer"],x["field_name"],x["asset_kind"],x["raw_reference"],x["reference_status"],canonical_json({"asset_identity_created":False,"file_existence_claimed":False})),insert)


TABLES = {"rows":"world_archive_row_occurrence","raw_fields":"world_archive_raw_field_occurrence","relationships":"world_archive_relationship_occurrence","opaque":"world_archive_opaque_occurrence","text_owners":"world_archive_text_owner_occurrence","text_resolutions":"world_archive_text_resolution_occurrence","text_candidates":"world_archive_text_candidate_occurrence","assets":"world_archive_asset_reference_occurrence"}


def verify_open_connection(conn: sqlite3.Connection, p: Preflight) -> None:
    verify_schema_12(conn)
    run=conn.execute("SELECT snapshot_id,parser_build_id,scope_json,config_json,status,created_at FROM ingest_run WHERE id=?",(p.ingest_run_id,)).fetchone()
    if run is None or tuple(run)!=(p.snapshot_id,p.parser_build_id,_scope(p),_config(p),"complete",EXPECTED_OBSERVED_AT):
        raise sqlite3.IntegrityError("World Archive exact ingest run binding mismatch")
    _write_occurrences(conn,p,False)
    for key,table in TABLES.items():
        total=int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]); scoped=int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE ingest_run_id=?",(p.ingest_run_id,)).fetchone()[0])
        if total != p.derived["counts"][key] or scoped != total:
            raise sqlite3.IntegrityError(f"World Archive occurrence scope/count mismatch: {table}")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise sqlite3.IntegrityError("World Archive foreign key check failed")


def _write_bundle(conn: sqlite3.Connection,p:Preflight) -> None:
    source=p.manifest["source"]
    catalog=conn.execute("SELECT kind,url,license_note FROM source_catalog WHERE id='anime-game-data'").fetchone()
    if catalog is None: conn.execute("INSERT INTO source_catalog(id,kind,url,license_note) VALUES('anime-game-data','primary',?,NULL)",(source["upstream"],))
    elif tuple(catalog)!=("primary",source["upstream"],None): raise sqlite3.IntegrityError("source_catalog mismatch")
    snap=conn.execute('SELECT source_id,release_label,build,"commit",tree,observed_at,status,metadata_json FROM source_snapshot WHERE id=?',(p.snapshot_id,)).fetchone()
    expected=("anime-game-data","7.0",source["build"],source["commit"],source["tree"],source["observed_at"],"active")
    accepted={canonical_json({"sealed_source_manifest":True}),canonical_json({"sealed_source_manifest":True,"phase4e2_world_archive_canary":True})}
    if snap is None: conn.execute('INSERT INTO source_snapshot(id,source_id,release_label,build,"commit",tree,observed_at,parser_version,mapping_version,status,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(p.snapshot_id,*expected[:6],"legacy-unscoped","legacy-unscoped",expected[6],canonical_json({"sealed_source_manifest":True,"phase4e2_world_archive_canary":True})))
    elif tuple(snap)[:7]!=expected or tuple(snap)[7] not in accepted: raise sqlite3.IntegrityError("parser-independent source_snapshot mismatch")
    file_ids={}
    for x in p.derived["files"]:
        fid=digest_id("sf",p.snapshot_id,x["relative_path"]); file_ids[x["relative_path"]]=fid
        row=conn.execute("SELECT snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path FROM source_file WHERE id=?",(fid,)).fetchone(); wanted=(p.snapshot_id,x["relative_path"],x["blob_oid"],x["sha256"],x["size_bytes"],x["format"],str(p.source_root))
        if row is None: conn.execute("INSERT INTO source_file(id,snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path) VALUES(?,?,?,?,?,?,?,?)",(fid,*wanted))
        elif tuple(row)!=wanted: raise sqlite3.IntegrityError(f"source_file mismatch: {x['relative_path']}")
    for x in p.derived["source_records"]:
        fid=file_ids[x["relative_path"]]; rid=digest_id("sr",fid,x["source_pointer"]); wanted=(fid,x["source_pointer"],x["record_kind"],x["native_id"],x["raw_checksum"],canonical_json(x["raw_value"])); row=conn.execute("SELECT source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json FROM source_record WHERE id=?",(rid,)).fetchone()
        if row is None: conn.execute("INSERT INTO source_record(id,source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json) VALUES(?,?,?,?,?,?,?)",(rid,*wanted))
        elif tuple(row)!=wanted: raise sqlite3.IntegrityError("source_record mismatch")
    parser=(VERSIONS["parser"],VERSIONS["projection"],"world-archive-phase4e2-sealed-canary",p.config_checksum,EXPECTED_OBSERVED_AT); row=conn.execute("SELECT parser_version,mapping_version,fingerprint,config_checksum,created_at FROM parser_build WHERE id=?",(p.parser_build_id,)).fetchone()
    if row is None: conn.execute("INSERT INTO parser_build(id,parser_version,mapping_version,fingerprint,config_checksum,created_at) VALUES(?,?,?,?,?,?)",(p.parser_build_id,*parser))
    elif tuple(row)!=parser: raise sqlite3.IntegrityError("parser_build mismatch")
    if conn.execute("SELECT 1 FROM ingest_run WHERE id=?",(p.ingest_run_id,)).fetchone(): raise sqlite3.IntegrityError("ingest run already exists unexpectedly")
    conn.execute("INSERT INTO ingest_run(id,snapshot_id,parser_build_id,scope_json,config_json,status,created_at) VALUES(?,?,?,?,?,'running',?)",(p.ingest_run_id,p.snapshot_id,p.parser_build_id,_scope(p),_config(p),EXPECTED_OBSERVED_AT))
    _write_occurrences(conn,p,True)
    conn.execute("UPDATE ingest_run SET status='complete' WHERE id=?",(p.ingest_run_id,))


def _fingerprint(path:Path)->tuple[int,int,int,int,str]:
    info=os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode): _fail("database must be a regular non-symlink file")
    return info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,hashlib.sha256(path.read_bytes()).hexdigest()


def ingest(db_path:str|Path,source_root:str|Path,repo_root:str|Path,manifest_path:str|Path)->dict[str,Any]:
    target=Path(os.path.abspath(os.fspath(db_path))); original=_fingerprint(target)
    for suffix in ("-journal","-wal","-shm"):
        if Path(str(target)+suffix).exists(): _fail("database sidecars are forbidden")
    p=preflight(source_root,repo_root,manifest_path)
    with sqlite3.connect(target.as_uri()+"?mode=ro",uri=True) as ro:
        versions=[int(r[0]) for r in ro.execute("SELECT version FROM schema_migration ORDER BY version")]
        if versions not in (list(range(1,12)),list(range(1,13))): _fail("input schema must be exact 11 or 12")
        existing=ro.execute("SELECT 1 FROM ingest_run WHERE id=?",(p.ingest_run_id,)).fetchone() is not None
        if existing: verify_open_connection(ro,p)
    if existing: return _report(target,p)
    fd,name=tempfile.mkstemp(prefix=f".{target.name}.phase4e2-",suffix=".sqlite",dir=target.parent); os.close(fd); temporary=Path(name)
    try:
        with sqlite3.connect(target.as_uri()+"?mode=ro",uri=True) as source, sqlite3.connect(temporary) as destination: source.backup(destination)
        with Database(temporary) as db:
            conn=db.conn; conn.execute("BEGIN IMMEDIATE")
            if versions[-1]==11:
                migration=MIGRATIONS_DIR/"0012_phase4e2_world_archive_canary_evidence.sql"; raw=migration.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=MIGRATION_0012_SHA256: raise sqlite3.IntegrityError("migration 0012 seal mismatch")
                for statement in Database._sql_statements(raw.decode("utf-8-sig"),migration): Database._validate_migration_statement(statement,migration); conn.execute(statement)
                conn.execute("INSERT INTO schema_migration(version,applied_at) VALUES(12,?)",(EXPECTED_OBSERVED_AT,))
            verify_schema_12(conn); _write_bundle(conn,p); conn.commit()
        with sqlite3.connect(temporary.as_uri()+"?mode=ro",uri=True) as ro: verify_open_connection(ro,p)
        if _fingerprint(target)!=original: _fail("database changed before promotion")
        os.chmod(temporary,stat.S_IMODE(os.lstat(target).st_mode))
        promote_database(target,temporary,original,_PROMOTION_TEST_HOOK,_POST_REPLACE_TEST_HOOK)
    except Exception as exc:
        if temporary.exists(): temporary.unlink()
        if isinstance(exc,WorldArchivePhase4E2EvidenceError): raise
        raise WorldArchivePhase4E2EvidenceError(str(exc)) from exc
    return _report(target,p)


def _report(target:Path,p:Preflight)->dict[str,Any]:
    return {"status":"complete","schema_version":12,"family":"world_archive","family_contract_version":FAMILY_CONTRACT_VERSION,"snapshot_id":p.snapshot_id,"parser_build_id":p.parser_build_id,"ingest_run_id":p.ingest_run_id,"config_checksum":p.config_checksum,"counts":p.derived["counts"],"status_counts":p.derived["status_counts"],"occurrence_set_digests":p.derived["occurrence_set_digests"],"coverage":"bounded_canary","corpus_complete":False,"evidence_only":True,"db_path":str(target.resolve()),"database_sha256":_fingerprint(target)[4]}
