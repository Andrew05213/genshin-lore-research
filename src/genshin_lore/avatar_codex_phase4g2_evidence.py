"""Atomic family-specific Schema 14 Avatar Codex bounded-canary builder."""

from __future__ import annotations

from .platform_io import lock_exclusive, unlock
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .avatar_codex_phase4g2_contract import (
    EXPECTED_MANIFEST_SHA256,
    EXPECTED_MIGRATION_APPLIED_AT,
    EXPECTED_OBSERVED_AT,
    FAMILY,
    FAMILY_CONTRACT_VERSION,
    MIGRATION_SHA256,
    RUN_FAMILY,
    derive_source_contract,
    expected_identity,
    load_manifest,
    occurrence_id,
)
from .avatar_codex_phase4g2_schema import verify_schema_14
from .db import Database
from .phase4e5_combined_gate import assert_path_matrix
from .quest_codex_phase4f2_schema import verify_schema_13
from .util import canonical_json, digest_id
from .world_archive_phase4e2_promotion import assert_sidecars_absent, sync_directory


class AvatarCodexPhase4G2EvidenceError(RuntimeError):
    """The input, stage, or atomic promotion was outside the 4G2 contract."""


_PROMOTION_TEST_HOOK = None
_POST_REPLACE_TEST_HOOK = None


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


AVATAR_TABLES = frozenset(
    {
        "avatar_codex_row_occurrence",
        "avatar_codex_raw_field_occurrence",
        "avatar_codex_relationship_occurrence",
        "avatar_codex_relationship_candidate_occurrence",
        "avatar_codex_opaque_occurrence",
    }
)

SCHEMA13_USER_TABLES_DIGEST = "c6bf726ccb1c408811c1a9466a258fd99ceb0d3b42fb361b7d9beedee5211ad8"
SCHEMA14_USER_TABLES_DIGEST = "a1b308e2b5b0a4f5d042a032f82eae2780335493daae20ec7ecbee88d4dd8ccb"


def _fail(message: str) -> None:
    raise AvatarCodexPhase4G2EvidenceError(message)


def preflight(source_root: str | Path, repo_root: str | Path, manifest_path: str | Path) -> Preflight:
    paths = assert_path_matrix(
        {"source_root": source_root, "repo_root": repo_root, "manifest": manifest_path},
        protected_database_names=(), input_files=("manifest",), input_directories=("source_root", "repo_root"),
    )
    manifest = load_manifest(paths["manifest"])
    derived = derive_source_contract(paths["source_root"], paths["repo_root"], manifest)
    return Preflight(paths["source_root"], paths["repo_root"], manifest, derived, **expected_identity(manifest))


def _scope(p: Preflight) -> str:
    return canonical_json(
        {
            "family": RUN_FAMILY,
            "provider_family": FAMILY,
            "family_contract_version": FAMILY_CONTRACT_VERSION,
            "manifest_id": p.manifest["manifest_id"],
            "selected_rows": list(p.manifest["boundary"]["selected_rows"]),
            "selected_ids": list(p.manifest["boundary"]["selected_ids"]),
            "exact_ingest_run_required": True,
        }
    )


def _config(p: Preflight) -> str:
    return canonical_json(
        {
            "manifest_raw_sha256": EXPECTED_MANIFEST_SHA256,
            "versions": p.manifest["versions"],
            "logical_identity_created": False,
            "full_avatar_codex_scan": False,
            "corpus_complete": False,
            "mcp_provider_added": False,
        }
    )


def _fingerprint(path: str | Path) -> dict[str, Any]:
    target = Path(os.path.abspath(os.fspath(path)))
    assert_sidecars_absent(target, "during Avatar Codex fingerprint")
    before = os.lstat(target)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        _fail("database must be a regular non-symlink file")
    descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != identity:
            _fail("database descriptor identity changed while fingerprinting")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after_open = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after = os.lstat(target)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity != after_identity or identity != (after_open.st_dev, after_open.st_ino, after_open.st_size, after_open.st_mtime_ns):
        _fail("database changed while fingerprinting")
    assert_sidecars_absent(target, "after Avatar Codex fingerprint")
    return {"sha256": digest.hexdigest(), "size_bytes": after.st_size, "mtime_ns": after.st_mtime_ns, "st_dev": after.st_dev, "st_ino": after.st_ino, "st_mode": stat.S_IMODE(after.st_mode)}


def _migration_0014(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT version,applied_at FROM schema_migration WHERE version=14").fetchone()
    if row is None or int(row[0]) != 14 or row[1] != EXPECTED_MIGRATION_APPLIED_AT:
        _fail("migration 0014 ledger tuple mismatch")
    return {"version": 14, "applied_at": str(row[1])}


def _family_run_ids(conn: sqlite3.Connection) -> list[str]:
    result: list[str] = []
    for run_id, scope_json in conn.execute("SELECT id,scope_json FROM ingest_run ORDER BY id"):
        try:
            scope = json.loads(scope_json)
        except (TypeError, json.JSONDecodeError) as exc:
            _fail("invalid ingest_run scope JSON")
        if isinstance(scope, dict) and (
            scope.get("provider_family") == FAMILY
            or str(scope.get("family", "")) == FAMILY
            or str(scope.get("family", "")).startswith(f"{FAMILY}_")
        ):
            result.append(str(run_id))
    return result


def _user_tables(conn: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    )


def _assert_schema13_stage(conn: sqlite3.Connection, *, allow_exact14: bool = False) -> list[int]:
    versions = [int(row[0]) for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")]
    if versions == list(range(1, 14)):
        verify_schema_13(conn)
        tables = _user_tables(conn)
        if len(tables) != 83 or hashlib.sha256("\n".join(tables).encode()).hexdigest() != SCHEMA13_USER_TABLES_DIGEST:
            raise sqlite3.IntegrityError("Schema 13 user-table inventory is not exact")
        return versions
    if allow_exact14 and versions == list(range(1, 15)):
        verify_schema_14(conn)
        tables = _user_tables(conn)
        if len(tables) != 88 or hashlib.sha256("\n".join(tables).encode()).hexdigest() != SCHEMA14_USER_TABLES_DIGEST:
            raise sqlite3.IntegrityError("Schema 14 user-table inventory is not exact")
        return versions
    raise sqlite3.IntegrityError("input schema must be exact Schema 13 or exact Schema 14")


def _assert_no_foreign_stage(conn: sqlite3.Connection, *, expected_run: str | None = None) -> None:
    runs = _family_run_ids(conn)
    if expected_run is None:
        if runs:
            _fail("foreign Avatar Codex stage present before mutation")
    elif runs != [expected_run]:
        _fail("Avatar Codex stage run set mismatch")


def _ids(p: Preflight) -> dict[str, dict[Any, str]]:
    ident = {"snapshot_id": p.snapshot_id, "parser_build_id": p.parser_build_id, "ingest_run_id": p.ingest_run_id}
    return {
        "rows": {(item["table"], item["row_index"]): occurrence_id("row", ident, item["table"], item["row_index"], item["pointer"]) for item in p.derived["rows"]},
        "relationships": {item["relationship_key"]: occurrence_id("relationship", ident, item["relationship_key"]) for item in p.derived["relationships"]},
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
    ident = {"snapshot_id": p.snapshot_id, "parser_build_id": p.parser_build_id, "ingest_run_id": p.ingest_run_id}
    file_ids = {item["relative_path"]: digest_id("sf", p.snapshot_id, item["relative_path"]) for item in p.derived["files"]}
    record_ids = {(item["relative_path"], item["source_pointer"]): digest_id("sr", file_ids[item["relative_path"]], item["source_pointer"]) for item in p.derived["source_records"]}
    row_records = {(item["table"], item["row_index"]): record_ids[(item["relative_path"], item["pointer"])] for item in p.derived["rows"]}
    for item in p.derived["rows"]:
        key = (item["table"], item["row_index"])
        _ensure(conn, "avatar_codex_row_occurrence", ("id", "ingest_run_id", "snapshot_id", "parser_build_id", "source_file_id", "source_record_id", "source_pointer", "evidence_kind", "table_name", "row_index", "native_locator_field", "native_locator_value", "raw_checksum", "metadata_json"), (ids["rows"][key], p.ingest_run_id, p.snapshot_id, p.parser_build_id, file_ids[item["relative_path"]], row_records[key], item["pointer"], item["evidence_kind"], item["table"], item["row_index"], item["native_locator_field"], item["native_locator_value"], item["raw_checksum"], canonical_json({"physical_occurrence": True, "logical_identity_created": False})), insert)
    for item in p.derived["raw_fields"]:
        key = (item["table"], item["row_index"])
        oid = occurrence_id("raw-field", ident, item["table"], item["row_index"], item["source_pointer"])
        _ensure(conn, "avatar_codex_raw_field_occurrence", ("id", "ingest_run_id", "snapshot_id", "parser_build_id", "row_occurrence_id", "source_record_id", "source_pointer", "field_name", "field_presence", "raw_json", "metadata_json"), (oid, p.ingest_run_id, p.snapshot_id, p.parser_build_id, ids["rows"][key], row_records[key], item["source_pointer"], item["field_name"], item["field_presence"], canonical_json(item["raw_value"]), canonical_json({"absence_is_evidence": False, "identity_effect": "none"})), insert)
    for item in p.derived["relationships"]:
        key = (item["table"], item["row_index"])
        _ensure(conn, "avatar_codex_relationship_occurrence", ("id", "ingest_run_id", "snapshot_id", "parser_build_id", "source_row_occurrence_id", "source_record_id", "source_pointer", "field_name", "raw_value_json", "target_table", "target_field", "resolution_status", "metadata_json"), (ids["relationships"][item["relationship_key"]], p.ingest_run_id, p.snapshot_id, p.parser_build_id, ids["rows"][key], row_records[key], item["source_pointer"], item["field_name"], canonical_json(item["raw_value"]), item["target_table"], item["target_field"], item["resolution_status"], canonical_json({"candidate_identity_effect": "none", "missing_is_legal": True})), insert)
    for item in p.derived["relationship_candidates"]:
        rid = ids["relationships"][item["relationship_key"]]
        oid = occurrence_id("relationship-candidate", ident, item["relationship_key"], item["candidate_ordinal"], item["source_relative_path"], item["source_pointer"])
        _ensure(conn, "avatar_codex_relationship_candidate_occurrence", ("id", "ingest_run_id", "snapshot_id", "parser_build_id", "relationship_occurrence_id", "source_file_id", "source_record_id", "source_pointer", "candidate_ordinal", "target_table", "target_field", "target_raw_value_json", "raw_checksum", "metadata_json"), (oid, p.ingest_run_id, p.snapshot_id, p.parser_build_id, rid, file_ids[item["source_relative_path"]], record_ids[(item["source_relative_path"], item["source_pointer"])], item["source_pointer"], item["candidate_ordinal"], item["target_table"], item["target_field"], canonical_json(item["target_raw_value"]), item["raw_checksum"], canonical_json({"physical_lookup_candidate": True, "logical_parent_created": False})), insert)
    for item in p.derived["opaque"]:
        key = (item["table"], item["row_index"])
        oid = occurrence_id("opaque", ident, item["table"], item["row_index"], item["source_pointer"])
        _ensure(conn, "avatar_codex_opaque_occurrence", ("id", "ingest_run_id", "snapshot_id", "parser_build_id", "source_row_occurrence_id", "source_record_id", "source_pointer", "field_name", "array_index", "raw_value_json", "resolution_status", "metadata_json"), (oid, p.ingest_run_id, p.snapshot_id, p.parser_build_id, ids["rows"][key], row_records[key], item["source_pointer"], item["field_name"], item["array_index"], canonical_json(item["raw_value"]), "opaque_unresolved", canonical_json({"inference_performed": False, "identity_effect": "none"})), insert)


def _write_bundle(conn: sqlite3.Connection, p: Preflight) -> None:
    source = p.manifest["source"]
    catalog = conn.execute("SELECT kind,url,license_note FROM source_catalog WHERE id=?", (source["source_id"],)).fetchone()
    if catalog is None:
        conn.execute("INSERT INTO source_catalog(id,kind,url,license_note) VALUES(?,?,?,NULL)", (source["source_id"], "primary", source["upstream"]))
    elif tuple(catalog) != ("primary", source["upstream"], None):
        raise sqlite3.IntegrityError("source_catalog mismatch")
    snapshot = conn.execute('SELECT source_id,release_label,build,"commit",tree,observed_at,status,metadata_json FROM source_snapshot WHERE id=?', (p.snapshot_id,)).fetchone()
    wanted_snapshot = (source["source_id"], source["release_label"], source["build"], source["commit"], source["tree"], source["observed_at"], "active")
    if snapshot is None:
        conn.execute('INSERT INTO source_snapshot(id,source_id,release_label,build,"commit",tree,observed_at,parser_version,mapping_version,status,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)', (p.snapshot_id, *wanted_snapshot[:6], "legacy-unscoped", "legacy-unscoped", wanted_snapshot[6], canonical_json({"sealed_source_manifest": True})))
    elif tuple(snapshot)[:7] != wanted_snapshot or not isinstance(json.loads(str(snapshot[7])), dict):
        raise sqlite3.IntegrityError("parser-independent source_snapshot mismatch")
    file_ids: dict[str, str] = {}
    for item in p.derived["files"]:
        file_id = digest_id("sf", p.snapshot_id, item["relative_path"])
        file_ids[item["relative_path"]] = file_id
        wanted = (p.snapshot_id, item["relative_path"], item["blob_oid"], item["sha256"], item["size_bytes"], item["format"], str(p.source_root))
        row = conn.execute("SELECT snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path FROM source_file WHERE id=?", (file_id,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO source_file(id,snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path) VALUES(?,?,?,?,?,?,?,?)", (file_id, *wanted))
        elif tuple(row) != wanted:
            raise sqlite3.IntegrityError(f"source_file mismatch: {item['relative_path']}")
    for item in p.derived["source_records"]:
        file_id = file_ids[item["relative_path"]]
        record_id = digest_id("sr", file_id, item["source_pointer"])
        wanted = (file_id, item["source_pointer"], item["record_kind"], item["native_id"], item["raw_checksum"], canonical_json(item["raw_value"]))
        row = conn.execute("SELECT source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json FROM source_record WHERE id=?", (record_id,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO source_record(id,source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json) VALUES(?,?,?,?,?,?,?)", (record_id, *wanted))
        elif tuple(row) != wanted:
            raise sqlite3.IntegrityError("source_record mismatch")
    parser = (p.manifest["versions"]["parser"], p.manifest["versions"]["projection"], "avatar-codex-phase4g2-sealed-canary", p.config_checksum, EXPECTED_OBSERVED_AT)
    row = conn.execute("SELECT parser_version,mapping_version,fingerprint,config_checksum,created_at FROM parser_build WHERE id=?", (p.parser_build_id,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO parser_build(id,parser_version,mapping_version,fingerprint,config_checksum,created_at) VALUES(?,?,?,?,?,?)", (p.parser_build_id, *parser))
    elif tuple(row) != parser:
        raise sqlite3.IntegrityError("parser_build mismatch")
    if conn.execute("SELECT 1 FROM ingest_run WHERE id=?", (p.ingest_run_id,)).fetchone() is not None:
        raise sqlite3.IntegrityError("Avatar Codex ingest run already exists")
    conn.execute("INSERT INTO ingest_run(id,snapshot_id,parser_build_id,scope_json,config_json,status,created_at) VALUES(?,?,?,?,?,'running',?)", (p.ingest_run_id, p.snapshot_id, p.parser_build_id, _scope(p), _config(p), EXPECTED_OBSERVED_AT))
    _write_occurrences(conn, p, True)
    conn.execute("UPDATE ingest_run SET status='complete' WHERE id=?", (p.ingest_run_id,))


def verify_open_connection(conn: sqlite3.Connection, p: Preflight) -> None:
    verify_schema_14(conn)
    _migration_0014(conn)
    _assert_no_foreign_stage(conn, expected_run=p.ingest_run_id)
    run = conn.execute("SELECT snapshot_id,parser_build_id,scope_json,config_json,status,created_at FROM ingest_run WHERE id=?", (p.ingest_run_id,)).fetchone()
    expected_run = (p.snapshot_id, p.parser_build_id, _scope(p), _config(p), "complete", EXPECTED_OBSERVED_AT)
    if run is None or tuple(run) != expected_run:
        raise sqlite3.IntegrityError("exact Avatar Codex ingest run mismatch")
    _write_occurrences(conn, p, False)
    table_names = {
        "rows": "avatar_codex_row_occurrence",
        "raw_fields": "avatar_codex_raw_field_occurrence",
        "relationships": "avatar_codex_relationship_occurrence",
        "relationship_candidates": "avatar_codex_relationship_candidate_occurrence",
        "opaque": "avatar_codex_opaque_occurrence",
    }
    for key, table in table_names.items():
        total = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        scoped = int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE ingest_run_id=?", (p.ingest_run_id,)).fetchone()[0])
        if total != scoped or scoped != p.derived["counts"][key]:
            raise sqlite3.IntegrityError(f"Avatar Codex occurrence scope/count mismatch: {table}")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise sqlite3.IntegrityError("Avatar Codex foreign key check failed")


def _report(path: Path, p: Preflight) -> dict[str, Any]:
    with sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
        migration = _migration_0014(conn)
    fingerprint = _fingerprint(path)
    return {
        "schema_migration_0014": migration,
        "status": "complete",
        "schema_version": 14,
        "family": FAMILY,
        "family_contract_version": FAMILY_CONTRACT_VERSION,
        "snapshot_id": p.snapshot_id,
        "parser_build_id": p.parser_build_id,
        "ingest_run_id": p.ingest_run_id,
        "config_checksum": p.config_checksum,
        "counts": p.derived["counts"],
        "status_counts": p.derived["status_counts"],
        "occurrence_set_digests": p.derived["occurrence_set_digests"],
        "coverage": "bounded_canary",
        "corpus_complete": False,
        "evidence_only": True,
        "mcp_provider_added": False,
        "db_path": str(path.resolve()),
        "database_sha256": fingerprint["sha256"],
        "database_size_bytes": fingerprint["size_bytes"],
    }


def _copy_fixed(source: Path, destination: Path) -> None:
    source_fp = _fingerprint(source)
    source_descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    destination_descriptor = os.open(destination, os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0))
    try:
        copied = 0
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(destination_descriptor, view)
                if written <= 0:
                    _fail("private staging copy made no progress")
                view = view[written:]
                copied += written
        os.fsync(destination_descriptor)
    finally:
        os.close(source_descriptor)
        os.close(destination_descriptor)
    if _fingerprint(source)["sha256"] != source_fp["sha256"] or copied != source_fp["size_bytes"]:
        _fail("Schema 13 baseline changed during private copy")


def _private_database(parent: Path, prefix: str) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=".sqlite", dir=parent)
    os.close(descriptor)
    path = Path(name)
    os.chmod(path, 0o600)
    return path


def _write_json_stage(parent: Path, prefix: str, value: Mapping[str, Any]) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=".json", dir=parent)
    path = Path(name)
    os.chmod(path, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
    return path


def _cleanup(path: Path | None) -> None:
    if path is None:
        return
    for item in (path, Path(f"{path}-journal"), Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            item.unlink()
        except FileNotFoundError:
            pass


def _promote_absent_pair(database: Path, report: Path, database_stage: Path, report_stage: Path) -> None:
    lock_path = Path(f"{database}.lock")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    locked = False
    published_db = False
    published_report = False
    db_identity: tuple[int, int] | None = None
    report_identity: tuple[int, int] | None = None
    try:
        lock_exclusive(descriptor)
        locked = True
        lock_info = os.fstat(descriptor)
        lock_path_info = os.lstat(lock_path)
        if stat.S_IMODE(lock_info.st_mode) != 0o600 or stat.S_ISLNK(lock_path_info.st_mode) or not stat.S_ISREG(lock_path_info.st_mode) or (lock_info.st_dev, lock_info.st_ino) != (lock_path_info.st_dev, lock_path_info.st_ino):
            _fail("final stable lock identity/mode mismatch")
        if _PROMOTION_TEST_HOOK is not None:
            _PROMOTION_TEST_HOOK(database, database_stage)
        if os.path.lexists(database) or os.path.lexists(report):
            _fail("final output appeared before promotion")
        assert_sidecars_absent(database, "under Avatar Codex stable lock")
        assert_sidecars_absent(database_stage, "under Avatar Codex stable lock")
        for stage in (database_stage, report_stage):
            info = os.lstat(stage)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                _fail("private staging output is not a 0600 regular file")
            stage_descriptor = os.open(stage, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fsync(stage_descriptor)
            finally:
                os.close(stage_descriptor)
        db_info = os.lstat(database_stage)
        report_info = os.lstat(report_stage)
        db_identity = (db_info.st_dev, db_info.st_ino)
        report_identity = (report_info.st_dev, report_info.st_ino)
        os.replace(database_stage, database)
        published_db = True
        sync_directory(database.parent)
        os.replace(report_stage, report)
        published_report = True
        sync_directory(report.parent)
        if _POST_REPLACE_TEST_HOOK is not None:
            _POST_REPLACE_TEST_HOOK(database, lock_path)
    except BaseException:
        if published_report and report_identity is not None:
            try:
                current = os.lstat(report)
                if not stat.S_ISLNK(current.st_mode) and (current.st_dev, current.st_ino) == report_identity:
                    report.unlink()
                    sync_directory(report.parent)
            except FileNotFoundError:
                pass
        if published_db and db_identity is not None:
            try:
                current = os.lstat(database)
                if not stat.S_ISLNK(current.st_mode) and (current.st_dev, current.st_ino) == db_identity:
                    database.unlink()
                    sync_directory(database.parent)
            except FileNotFoundError:
                pass
        raise
    finally:
        if locked:
            unlock(descriptor)
        os.close(descriptor)


def _append_to_stage(staging: Path, p: Preflight, versions: list[int]) -> None:
    with Database(staging) as database:
        database.initialize_through(14)
        database.conn.execute("UPDATE schema_migration SET applied_at=? WHERE version=14", (EXPECTED_MIGRATION_APPLIED_AT,))
        database.conn.commit()
        verify_schema_14(database.conn)
        database.conn.execute("BEGIN IMMEDIATE")
        _write_bundle(database.conn, p)
        database.conn.commit()
    with sqlite3.connect(staging.as_uri() + "?mode=ro&immutable=1", uri=True) as readonly:
        readonly.execute("PRAGMA query_only=ON")
        verify_open_connection(readonly, p)


def ingest(db_path: str | Path, source_root: str | Path, repo_root: str | Path, manifest_path: str | Path) -> dict[str, Any]:
    """Append 4G2 to an existing exact Schema13 copy or replay exact Schema14."""
    target = Path(os.path.abspath(os.fspath(db_path)))
    paths = assert_path_matrix(
        {"database": target, "source_root": source_root, "repo_root": repo_root, "manifest": manifest_path},
        protected_database_names=("database",), input_files=("database", "manifest"), input_directories=("source_root", "repo_root"),
    )
    target = paths["database"]
    original = _fingerprint(target)
    p = preflight(paths["source_root"], paths["repo_root"], paths["manifest"])
    with sqlite3.connect(target.as_uri() + "?mode=ro&immutable=1", uri=True) as readonly:
        versions = _assert_schema13_stage(readonly, allow_exact14=True)
        existing = readonly.execute("SELECT 1 FROM ingest_run WHERE id=?", (p.ingest_run_id,)).fetchone() is not None
        if versions == list(range(1, 15)):
            verify_schema_14(readonly)
            if existing:
                verify_open_connection(readonly, p)
            else:
                _assert_no_foreign_stage(readonly)
                if any(int(readonly.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in AVATAR_TABLES):
                    _fail("Schema 14 input contains foreign Avatar Codex rows")
        elif existing:
            _fail("Schema 13 input contains the 4G2 ingest run")
        else:
            _assert_no_foreign_stage(readonly)
    if existing:
        if _fingerprint(target) != original:
            _fail("database changed during idempotent replay")
        return _report(target, p)
    staging = _private_database(target.parent, f".{target.name}.phase4g2-")
    try:
        _copy_fixed(target, staging)
        _append_to_stage(staging, p, versions)
        if _fingerprint(target) != original:
            _fail("database changed before Avatar Codex promotion")
        # Existing-target append uses the shared stable sibling-lock protocol
        # semantics.  A private absent-target build below is the release path.
        from .world_archive_phase4e2_stable_lock import promote_database

        promote_database(target, staging, (original["st_dev"], original["st_ino"], original["size_bytes"], original["mtime_ns"], original["sha256"]), _PROMOTION_TEST_HOOK, _POST_REPLACE_TEST_HOOK)
        assert_sidecars_absent(target, "after Avatar Codex promotion")
    except AvatarCodexPhase4G2EvidenceError:
        raise
    except Exception as exc:
        _fail(f"Avatar Codex Phase 4G2 atomic append failed: {exc}")
    finally:
        _cleanup(staging)
    return _report(target, p)


def build(
    baseline_path: str | Path,
    database_path: str | Path,
    source_root: str | Path,
    repo_root: str | Path,
    manifest_path: str | Path,
    report_path: str | Path,
) -> dict[str, Any]:
    """Build a new Schema14 artifact from the exact accepted Schema13 file."""
    paths = assert_path_matrix(
        {
            "baseline": baseline_path,
            "database": database_path,
            "source_root": source_root,
            "repo_root": repo_root,
            "manifest": manifest_path,
            "report": report_path,
        },
        protected_database_names=("baseline", "database"),
        input_files=("baseline", "manifest"), input_directories=("source_root", "repo_root"),
        absent_outputs=("database", "report"),
    )
    baseline = paths["baseline"]
    target = paths["database"]
    report = paths["report"]
    baseline_fp = _fingerprint(baseline)
    if (baseline_fp["size_bytes"], baseline_fp["sha256"]) != (1723596800, "7ff8163b6007a41b0827af05fbcf996353617c52f354a131431da912712e58be"):
        _fail("accepted Schema13 baseline byte seal mismatch")
    p = preflight(paths["source_root"], paths["repo_root"], paths["manifest"])
    with sqlite3.connect(baseline.as_uri() + "?mode=ro&immutable=1", uri=True) as readonly:
        versions = _assert_schema13_stage(readonly)
        if _family_run_ids(readonly):
            _fail("accepted Schema13 baseline contains a foreign Avatar Codex stage")
        if any(name in _user_tables(readonly) for name in AVATAR_TABLES):
            _fail("accepted Schema13 baseline contains Avatar Codex tables")
    staging = _private_database(target.parent, f".{target.name}.phase4g2-")
    report_stage: Path | None = None
    try:
        _copy_fixed(baseline, staging)
        _append_to_stage(staging, p, versions)
        staging_fp = _fingerprint(staging)
        if _fingerprint(baseline) != baseline_fp:
            _fail("accepted Schema13 baseline changed during Avatar Codex build")
        result = _report(staging, p)
        result["db_path"] = str(target.resolve())
        result["database_sha256"] = staging_fp["sha256"]
        result["database_size_bytes"] = staging_fp["size_bytes"]
        report_stage = _write_json_stage(report.parent, f".{report.name}.phase4g2-", result)
        _promote_absent_pair(target, report, staging, report_stage)
        report_stage = None
        assert_sidecars_absent(target, "after Avatar Codex build promotion")
        return result
    except AvatarCodexPhase4G2EvidenceError:
        raise
    except Exception as exc:
        _fail(f"Avatar Codex Phase 4G2 atomic build failed: {exc}")
    finally:
        _cleanup(staging)
        _cleanup(report_stage)


__all__ = ["AvatarCodexPhase4G2EvidenceError", "Preflight", "build", "ingest", "preflight", "verify_open_connection"]
