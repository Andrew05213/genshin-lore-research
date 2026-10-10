"""Atomic full-boundary Phase 4D2B Core Tutorial evidence builder."""

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

from .db import Database
from .tutorial_phase4d2a_evidence import (
    _execute_migration_0011,
    _write_occurrence_rows,
)
from .tutorial_phase4d2a_schema import (
    TUTORIAL_IMMUTABLE_TRIGGERS,
    TUTORIAL_INDEXES,
    TUTORIAL_TABLES,
    TutorialPhase4D2ASchemaError,
    verify_tutorial_phase4d2a_schema_signature,
)
from .tutorial_phase4d2b_contract import (
    PARSER_FINGERPRINT,
    RUN_FAMILY,
    TutorialPhase4D2BContractError,
    derive_tutorial_phase4d2b_source_contract,
    expected_identity,
    verify_phase4d1_audit_binding,
)
from .tutorial_phase4d2b_manifest import (
    EXPECTED_COMMIT,
    EXPECTED_COVERAGE,
    EXPECTED_OBSERVED_AT,
    EXPECTED_PUSH_TIPS_CORPUS_COMPLETE,
    EXPECTED_SOURCE_ID,
    EXPECTED_VERSIONS,
    TutorialPhase4D2BManifest,
    TutorialPhase4D2BManifestError,
    load_tutorial_phase4d2b_manifest,
)
from .util import canonical_json, digest_id, sha256_bytes


class TutorialPhase4D2BEvidenceError(RuntimeError):
    """The full-boundary source, schema, or atomic append failed closed."""


@dataclass(frozen=True)
class TutorialPhase4D2BPreflightResult:
    manifest: TutorialPhase4D2BManifest
    source_root: Path
    snapshot_id: str
    parser_build_id: str
    ingest_run_id: str
    config_checksum: str
    derived: dict[str, Any]


@dataclass(frozen=True)
class _DatabaseIdentity:
    device: int
    inode: int
    mode: int
    sha256: str
    size_bytes: int
    mtime_ns: int


def _fail(message: str) -> None:
    raise TutorialPhase4D2BEvidenceError(message)


def _require_posix_builder_runtime() -> None:
    if os.name != "posix" or not callable(getattr(os, "replace", None)) or not callable(getattr(sqlite3.Connection, "backup", None)):
        _fail("Tutorial Phase 4D2B builder requires POSIX atomic rename and SQLite backup support")


def _absolute_input_path(value: str | Path) -> Path:
    try:
        return Path(os.path.abspath(os.fspath(value)))
    except (TypeError, ValueError, OSError) as exc:
        _fail(f"Tutorial database path is invalid: {value!r}")
        raise AssertionError from exc


def _require_regular_file(value: str | Path, stage: str) -> Path:
    path = _absolute_input_path(value)
    try:
        info = os.lstat(path)
    except OSError as exc:
        _fail(f"Tutorial full-boundary database is not readable at {stage}: {path}: {exc}")
        raise AssertionError from exc
    if stat.S_ISLNK(info.st_mode):
        _fail(f"Tutorial full-boundary database symlink is forbidden at {stage}: {path}")
    if not stat.S_ISREG(info.st_mode):
        _fail(f"Tutorial full-boundary database must be a regular file at {stage}: {path}")
    return path


def _capture_identity(target: Path) -> _DatabaseIdentity:
    try:
        before = os.lstat(target)
    except OSError as exc:
        _fail(f"Tutorial full-boundary database cannot be lstat'ed: {target}: {exc}")
        raise AssertionError from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        _fail(f"Tutorial full-boundary database must remain a regular non-symlink file: {target}")
    try:
        raw = target.read_bytes()
        after = os.lstat(target)
    except OSError as exc:
        _fail(f"Tutorial full-boundary database changed while capturing identity: {target}: {exc}")
        raise AssertionError from exc
    if stat.S_ISLNK(after.st_mode) or not stat.S_ISREG(after.st_mode):
        _fail(f"Tutorial full-boundary database changed to a non-regular path: {target}")
    before_key = (before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns)
    after_key = (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns)
    if before_key != after_key or len(raw) != after.st_size:
        _fail("Tutorial full-boundary database changed while capturing CAS identity")
    return _DatabaseIdentity(after.st_dev, after.st_ino, after.st_mode, sha256_bytes(raw), after.st_size, after.st_mtime_ns)


def _assert_identity(target: Path, expected: _DatabaseIdentity, stage: str) -> None:
    actual = _capture_identity(target)
    if actual != expected:
        _fail(f"Tutorial full-boundary database CAS mismatch {stage}: expected={expected} actual={actual}")


def _sidecars(target: Path) -> tuple[Path, Path, Path]:
    return tuple(Path(f"{target}{suffix}") for suffix in ("-journal", "-wal", "-shm"))


def _assert_sidecars_absent(target: Path, stage: str) -> None:
    present = [str(path) for path in _sidecars(target) if path.exists()]
    if present:
        _fail(f"Tutorial full-boundary builder requires sidecar-free database {stage}: {present}")


def _assert_delete_journal(conn: sqlite3.Connection) -> None:
    if str(conn.execute("PRAGMA journal_mode").fetchone()[0]).casefold() != "delete":
        _fail("Tutorial full-boundary builder requires DELETE journal mode")


def _temporary_path(target: Path) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.phase4d2b-", suffix=".sqlite", dir=target.parent)
    os.close(descriptor)
    return Path(name)


def _cleanup(path: Path) -> None:
    for item in (path, *_sidecars(path)):
        try:
            item.unlink()
        except FileNotFoundError:
            pass


def _backup(target: Path, expected: _DatabaseIdentity) -> Path:
    _assert_sidecars_absent(target, "before backup")
    _assert_identity(target, expected, "before backup")
    temporary = _temporary_path(target)
    try:
        with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=0) as source, sqlite3.connect(temporary) as destination:
            source.execute("PRAGMA query_only=ON")
            source.execute("PRAGMA busy_timeout=0")
            _assert_delete_journal(source)
            source.backup(destination)
            destination.commit()
        _assert_sidecars_absent(target, "after backup")
        _assert_identity(target, expected, "after backup")
        return temporary
    except Exception:
        _cleanup(temporary)
        raise


def _finalize(target: Path, temporary: Path, expected: _DatabaseIdentity) -> None:
    _assert_sidecars_absent(target, "before finalization")
    _assert_identity(target, expected, "before finalization")
    lock = sqlite3.connect(target.as_uri() + "?mode=rw", uri=True, timeout=0, isolation_level=None)
    locked = False
    try:
        lock.execute("PRAGMA busy_timeout=0")
        _assert_delete_journal(lock)
        lock.execute("BEGIN EXCLUSIVE")
        locked = True
        _assert_sidecars_absent(target, "under finalization lock")
        _assert_identity(target, expected, "under finalization lock")
        os.replace(temporary, target)
    finally:
        if locked:
            try:
                lock.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        lock.close()


def preflight_tutorial_phase4d2b_evidence_bundle(
    source_root: str | Path,
    manifest_path: str | Path,
) -> TutorialPhase4D2BPreflightResult:
    if not isinstance(manifest_path, (str, Path)):
        _fail("Tutorial Phase 4D2B API requires a filesystem manifest path")
    try:
        manifest = load_tutorial_phase4d2b_manifest(manifest_path)
        verify_phase4d1_audit_binding(Path(__file__).resolve().parents[2], manifest)
        derived = derive_tutorial_phase4d2b_source_contract(source_root, manifest)
    except (TutorialPhase4D2BManifestError, TutorialPhase4D2BContractError) as exc:
        raise TutorialPhase4D2BEvidenceError(str(exc)) from exc
    expected = manifest.expected
    for key in (
        "counts", "status_counts", "missing_fields", "selected_table_counts",
        "text_owner_field_counts", "image_reference_stats", "occurrence_set_digests",
    ):
        if derived[key] != expected[key]:
            _fail(f"Tutorial Phase 4D2B {key} mismatch against sealed manifest")
    identity = expected_identity(manifest)
    if identity["snapshot_id"] != manifest.source["snapshot_id"]:
        _fail("Tutorial Phase 4D2B snapshot identity mismatch")
    return TutorialPhase4D2BPreflightResult(
        manifest,
        Path(source_root).resolve(),
        derived=derived,
        **identity,
    )


def _expected_run_scope(preflight: TutorialPhase4D2BPreflightResult) -> str:
    return canonical_json({
        "family": RUN_FAMILY,
        "manifest_id": preflight.manifest.manifest_id,
        "snapshot_id": preflight.snapshot_id,
        "coverage": EXPECTED_COVERAGE,
        "corpus_complete": True,
        "completeness_claim": EXPECTED_COVERAGE,
        "push_tips_corpus_complete": EXPECTED_PUSH_TIPS_CORPUS_COMPLETE,
        "selected_paths": [item.relative_path for item in preflight.manifest.files],
        "closure_counts": preflight.derived["counts"],
        "occurrence_set_digests": preflight.derived["occurrence_set_digests"],
        "full_core_tutorial_scan": True,
        "logical_identity_created": False,
        "mcp_capability_added": False,
    })


def _expected_run_config(preflight: TutorialPhase4D2BPreflightResult) -> str:
    return canonical_json({
        "manifest_raw_sha256": preflight.manifest.raw_sha256,
        "versions": preflight.manifest.versions,
        "source_commit": EXPECTED_COMMIT,
        "source_tree": preflight.manifest.source["tree"],
        "coverage": EXPECTED_COVERAGE,
        "corpus_complete": True,
        "completeness_claim": EXPECTED_COVERAGE,
        "push_tips_corpus_complete": EXPECTED_PUSH_TIPS_CORPUS_COMPLETE,
        "schema_contract": "schema11-migration0011-exact",
        "occurrence_set_digests": preflight.derived["occurrence_set_digests"],
        "logical_identity_created": False,
        "mcp_capability_added": False,
    })


def _strict_json(value: Any, label: str) -> Any:
    def reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise sqlite3.IntegrityError(f"duplicate JSON key in {label}: {key}")
            result[key] = item
        return result
    try:
        return json.loads(str(value), object_pairs_hook=reject_pairs)
    except (TypeError, json.JSONDecodeError) as exc:
        raise sqlite3.IntegrityError(f"{label} is not strict JSON") from exc


def _family_run_ids(conn: sqlite3.Connection, family: str) -> list[str]:
    result: list[str] = []
    for run_id, scope_json in conn.execute("SELECT id,scope_json FROM ingest_run ORDER BY id"):
        value = _strict_json(scope_json, f"ingest_run {run_id} scope_json")
        if isinstance(value, dict) and value.get("family") == family:
            result.append(str(run_id))
    return result


def _assert_no_logical_pollution(conn: sqlite3.Connection) -> None:
    allowed = {
        *TUTORIAL_TABLES, *TUTORIAL_INDEXES, *TUTORIAL_IMMUTABLE_TRIGGERS,
    }
    for object_type, name in conn.execute(
        "SELECT type,name FROM sqlite_master WHERE type IN ('table','view','index','trigger')"
    ):
        lowered = str(name).casefold()
        if lowered.startswith("sqlite_") or lowered in allowed:
            continue
        if "tutorial" in lowered or lowered in {"image", "images", "push_tips", "logical_tutorial", "tutorial_identity"}:
            raise sqlite3.IntegrityError(f"Tutorial full-boundary logical/derived object pollution: {object_type} {name}")


def _assert_run_scope(conn: sqlite3.Connection, run_id: str, *, allow_m1: bool) -> None:
    allowed = {run_id}
    if allow_m1:
        allowed.update(
            str(row[0])
            for row in conn.execute("SELECT id,scope_json FROM ingest_run")
            if isinstance(_strict_json(row[1], f"ingest_run {row[0]} scope_json"), dict)
            and _strict_json(row[1], f"ingest_run {row[0]} scope_json").get("family") == "tutorial_phase4d2a_core_canary"
        )
    for table in (
        "tutorial_row_occurrence", "tutorial_raw_field_occurrence",
        "tutorial_detail_relation_occurrence", "tutorial_text_owner_occurrence",
        "tutorial_text_resolution_occurrence", "tutorial_text_candidate_occurrence",
        "tutorial_image_reference_occurrence", "tutorial_push_tips_candidate_occurrence",
    ):
        runs = {str(row[0]) for row in conn.execute(f"SELECT DISTINCT ingest_run_id FROM {table}")}
        unexpected = runs - allowed
        if unexpected:
            raise sqlite3.IntegrityError(f"Tutorial full-boundary {table} run-scope pollution: {sorted(unexpected)}")


def _assert_table_scope(conn: sqlite3.Connection, preflight: TutorialPhase4D2BPreflightResult) -> None:
    expected = preflight.derived["counts"]
    tables = {
        "rows": "tutorial_row_occurrence",
        "raw_fields": "tutorial_raw_field_occurrence",
        "detail_relations": "tutorial_detail_relation_occurrence",
        "push_tips_relations": "tutorial_push_tips_candidate_occurrence",
        "text_owners": "tutorial_text_owner_occurrence",
        "text_resolutions": "tutorial_text_resolution_occurrence",
        "text_candidates": "tutorial_text_candidate_occurrence",
        "image_references": "tutorial_image_reference_occurrence",
    }
    for key, table in tables.items():
        scoped = int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE ingest_run_id=?", (preflight.ingest_run_id,)).fetchone()[0])
        if scoped != int(expected[key]):
            raise sqlite3.IntegrityError(f"Tutorial full-boundary {table} count mismatch: {scoped} != {expected[key]}")


def _assert_source_run_contract(conn: sqlite3.Connection, preflight: TutorialPhase4D2BPreflightResult) -> None:
    source = preflight.manifest.source
    catalog = conn.execute("SELECT kind,url,license_note FROM source_catalog WHERE id=?", (EXPECTED_SOURCE_ID,)).fetchone()
    if catalog is None or tuple(catalog) != ("primary", source["upstream"], None):
        raise sqlite3.IntegrityError("Tutorial full-boundary source_catalog mismatch")
    snapshot = conn.execute('SELECT source_id,release_label,build,"commit",tree,observed_at,status,metadata_json FROM source_snapshot WHERE id=?', (preflight.snapshot_id,)).fetchone()
    expected_snapshot = (source["source_id"], source["release_label"], source["build"], source["commit"], source["tree"], source["observed_at"], "active")
    allowed_metadata = {
        canonical_json({"sealed_source_manifest": True}),
        canonical_json({"sealed_source_manifest": True, "phase4d2a_canary": True}),
        canonical_json({"sealed_source_manifest": True, "phase4d2b_full_boundary": True}),
    }
    if snapshot is None or tuple(snapshot)[:7] != expected_snapshot or tuple(snapshot)[7] not in allowed_metadata:
        raise sqlite3.IntegrityError("Tutorial full-boundary source_snapshot mismatch")
    parser = conn.execute("SELECT parser_version,mapping_version,fingerprint,config_checksum,created_at FROM parser_build WHERE id=?", (preflight.parser_build_id,)).fetchone()
    if parser is None or tuple(parser) != (EXPECTED_VERSIONS["parser"], EXPECTED_VERSIONS["projection"], PARSER_FINGERPRINT, preflight.config_checksum, source["observed_at"]):
        raise sqlite3.IntegrityError("Tutorial full-boundary parser_build mismatch")
    run = conn.execute("SELECT snapshot_id,parser_build_id,scope_json,config_json,status,created_at FROM ingest_run WHERE id=?", (preflight.ingest_run_id,)).fetchone()
    if run is None or tuple(run) != (preflight.snapshot_id, preflight.parser_build_id, _expected_run_scope(preflight), _expected_run_config(preflight), "complete", source["observed_at"]):
        raise sqlite3.IntegrityError("Tutorial full-boundary ingest_run mismatch")
    family_ids = _family_run_ids(conn, RUN_FAMILY)
    if family_ids != [preflight.ingest_run_id]:
        raise sqlite3.IntegrityError(f"Tutorial full-boundary same-family run set mismatch: {family_ids}")
    expected_root = str(preflight.source_root)
    file_ids: dict[str, str] = {}
    for item in preflight.manifest.files:
        file_id = digest_id("sf", preflight.snapshot_id, item.relative_path)
        file_ids[item.relative_path] = file_id
        row = conn.execute("SELECT snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path FROM source_file WHERE id=?", (file_id,)).fetchone()
        actual = None if row is None else tuple(row)
        oid_ok = actual is not None and (actual[2] == item.blob_oid or (item.relative_path.startswith("TextMap/") and actual[2] is None))
        if actual is None or actual[:2] != (preflight.snapshot_id, item.relative_path) or actual[3:6] != (item.sha256, item.size_bytes, item.format) or not oid_ok or actual[6] != expected_root:
            raise sqlite3.IntegrityError(f"Tutorial full-boundary source_file mismatch: {item.relative_path}")
    for item in preflight.derived["source_records"]:
        file_id = file_ids[item["relative_path"]]
        record_id = digest_id("sr", file_id, item["source_pointer"])
        row = conn.execute("SELECT source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json FROM source_record WHERE id=?", (record_id,)).fetchone()
        wanted = (file_id, item["source_pointer"], item["record_kind"], item["native_id"], item["raw_checksum"], canonical_json(item["raw_value"]))
        if row is None or tuple(row) != wanted:
            raise sqlite3.IntegrityError(f"Tutorial full-boundary source_record mismatch: {item['relative_path']}#{item['source_pointer']}")


def _verify_complete_bundle(conn: sqlite3.Connection, preflight: TutorialPhase4D2BPreflightResult) -> None:
    if [int(row[0]) for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")] != list(range(1, 12)):
        raise sqlite3.IntegrityError("Tutorial full-boundary schema ledger is not exactly 1..11")
    verify_tutorial_phase4d2a_schema_signature(conn)
    _assert_no_logical_pollution(conn)
    _assert_source_run_contract(conn, preflight)
    _assert_run_scope(conn, preflight.ingest_run_id, allow_m1=True)
    _write_occurrence_rows(conn, preflight, insert_missing=False)
    _assert_table_scope(conn, preflight)
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise sqlite3.IntegrityError("Tutorial full-boundary foreign-key check failed")


def _write_source_and_occurrences(db: Database, preflight: TutorialPhase4D2BPreflightResult) -> None:
    conn = db.conn
    source = preflight.manifest.source
    catalog = conn.execute("SELECT kind,url,license_note FROM source_catalog WHERE id=?", (EXPECTED_SOURCE_ID,)).fetchone()
    if catalog is None:
        conn.execute("INSERT INTO source_catalog(id,kind,url,license_note) VALUES(?,?,?,?)", (EXPECTED_SOURCE_ID, "primary", source["upstream"], None))
    elif tuple(catalog) != ("primary", source["upstream"], None):
        raise sqlite3.IntegrityError("Tutorial full-boundary source_catalog identity mismatch")
    expected_snapshot = (source["source_id"], source["release_label"], source["build"], source["commit"], source["tree"], source["observed_at"], "active")
    metadata = canonical_json({"sealed_source_manifest": True, "phase4d2b_full_boundary": True})
    snapshot = conn.execute('SELECT source_id,release_label,build,"commit",tree,observed_at,status,metadata_json FROM source_snapshot WHERE id=?', (preflight.snapshot_id,)).fetchone()
    allowed_metadata = {
        canonical_json({"sealed_source_manifest": True}),
        canonical_json({"sealed_source_manifest": True, "phase4d2a_canary": True}),
        canonical_json({"sealed_source_manifest": True, "phase4d2b_full_boundary": True}),
    }
    if snapshot is None:
        conn.execute('INSERT INTO source_snapshot(id,source_id,release_label,build,"commit",tree,observed_at,parser_version,mapping_version,status,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)', (preflight.snapshot_id, *expected_snapshot[:6], "legacy-unscoped", "legacy-unscoped", expected_snapshot[6], metadata))
    elif tuple(snapshot)[:7] != expected_snapshot or tuple(snapshot)[7] not in allowed_metadata:
        raise sqlite3.IntegrityError("Tutorial full-boundary source_snapshot identity mismatch")
    file_ids: dict[str, str] = {}
    for item in preflight.manifest.files:
        file_id = digest_id("sf", preflight.snapshot_id, item.relative_path)
        file_ids[item.relative_path] = file_id
        expected_file = (preflight.snapshot_id, item.relative_path, item.blob_oid, item.sha256, item.size_bytes, item.format)
        row = conn.execute("SELECT snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path FROM source_file WHERE id=?", (file_id,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO source_file(id,snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path) VALUES(?,?,?,?,?,?,?,?)", (file_id, *expected_file, str(preflight.source_root)))
        else:
            actual = tuple(row)
            oid_ok = actual[2] == item.blob_oid or (item.relative_path.startswith("TextMap/") and actual[2] is None)
            if actual[:2] != expected_file[:2] or actual[3:6] != expected_file[3:] or not oid_ok or actual[6] != str(preflight.source_root):
                raise sqlite3.IntegrityError(f"Tutorial full-boundary source_file identity mismatch: {item.relative_path}")
    for item in preflight.derived["source_records"]:
        file_id = file_ids[item["relative_path"]]
        record_id = digest_id("sr", file_id, item["source_pointer"])
        values = (record_id, file_id, item["source_pointer"], item["record_kind"], item["native_id"], item["raw_checksum"], canonical_json(item["raw_value"]))
        row = conn.execute("SELECT source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json FROM source_record WHERE id=?", (record_id,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO source_record(id,source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json) VALUES(?,?,?,?,?,?,?)", values)
        elif tuple(row) != values[1:]:
            raise sqlite3.IntegrityError(f"Tutorial full-boundary source_record identity mismatch: {item['relative_path']}#{item['source_pointer']}")
    parser_values = (preflight.parser_build_id, EXPECTED_VERSIONS["parser"], EXPECTED_VERSIONS["projection"], PARSER_FINGERPRINT, preflight.config_checksum, source["observed_at"])
    row = conn.execute("SELECT parser_version,mapping_version,fingerprint,config_checksum,created_at FROM parser_build WHERE id=?", (preflight.parser_build_id,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO parser_build(id,parser_version,mapping_version,fingerprint,config_checksum,created_at) VALUES(?,?,?,?,?,?)", parser_values)
    elif tuple(row) != parser_values[1:]:
        raise sqlite3.IntegrityError("Tutorial full-boundary parser_build identity mismatch")
    if conn.execute("SELECT 1 FROM ingest_run WHERE id=?", (preflight.ingest_run_id,)).fetchone() is not None:
        raise sqlite3.IntegrityError("Tutorial full-boundary ingest_run appeared after preflight")
    conn.execute("INSERT INTO ingest_run(id,snapshot_id,parser_build_id,scope_json,config_json,status,created_at) VALUES(?,?,?,?,?,?,?)", (preflight.ingest_run_id, preflight.snapshot_id, preflight.parser_build_id, _expected_run_scope(preflight), _expected_run_config(preflight), "running", source["observed_at"]))
    _write_occurrence_rows(conn, preflight, insert_missing=True)
    if conn.execute("UPDATE ingest_run SET status='complete' WHERE id=? AND status='running'", (preflight.ingest_run_id,)).rowcount != 1:
        raise sqlite3.IntegrityError("Tutorial full-boundary ingest completion transition failed")
    _verify_complete_bundle(conn, preflight)


def _inspect_target(target: Path, preflight: TutorialPhase4D2BPreflightResult) -> tuple[bool, list[int]]:
    _require_regular_file(target, "read-only inspection")
    _assert_sidecars_absent(target, "read-only inspection")
    try:
        with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=0) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            _assert_delete_journal(conn)
            versions = [int(row[0]) for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")]
            if versions not in (list(range(1, 11)), list(range(1, 12))):
                raise sqlite3.IntegrityError(f"Tutorial full-boundary input schema must be exactly 1..10 or 1..11: {versions}")
            m2_runs = _family_run_ids(conn, RUN_FAMILY)
            if len(m2_runs) > 1:
                raise sqlite3.IntegrityError(f"Tutorial full-boundary same-family run mismatch: {m2_runs}")
            if versions[-1] == 11:
                _assert_run_scope(conn, preflight.ingest_run_id, allow_m1=True)
                verify_tutorial_phase4d2a_schema_signature(conn)
                _assert_no_logical_pollution(conn)
                if m2_runs:
                    _verify_complete_bundle(conn, preflight)
                    return True, versions
                return False, versions
            premature = int(conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name GLOB 'tutorial_*' OR name GLOB 'trg_tutorial_*' OR name GLOB 'idx_tutorial_*' OR name GLOB 'uq_tutorial_*'").fetchone()[0])
            if premature:
                raise sqlite3.IntegrityError("Schema 10 input contains premature Tutorial objects")
            return False, versions
    except (sqlite3.Error, TutorialPhase4D2ASchemaError, OSError, ValueError, TypeError, KeyError) as exc:
        raise TutorialPhase4D2BEvidenceError(str(exc)) from exc


def _build_report(
    target: Path,
    preflight: TutorialPhase4D2BPreflightResult,
    identity: _DatabaseIdentity,
) -> dict[str, Any]:
    return {
        "status": "complete",
        "db_path": str(target.resolve()),
        "snapshot_id": preflight.snapshot_id,
        "parser_build_id": preflight.parser_build_id,
        "ingest_run_id": preflight.ingest_run_id,
        "config_checksum": preflight.config_checksum,
        "manifest": {"manifest_id": preflight.manifest.manifest_id, "raw_sha256": preflight.manifest.raw_sha256},
        "database_fingerprint": {
            "sha256": identity.sha256,
            "size_bytes": identity.size_bytes,
            "mtime_ns": identity.mtime_ns,
        },
        "sqlite_storage": {"journal_mode": "delete", "sidecars_absent": True},
        "counts": preflight.derived["counts"],
        "status_counts": preflight.derived["status_counts"],
        "missing_fields": preflight.derived["missing_fields"],
        "selected_table_counts": preflight.derived["selected_table_counts"],
        "text_owner_field_counts": preflight.derived["text_owner_field_counts"],
        "image_reference_stats": preflight.derived["image_reference_stats"],
        "occurrence_set_digests": preflight.derived["occurrence_set_digests"],
        "scope": {
            "coverage": EXPECTED_COVERAGE,
            "corpus_complete": True,
            "completeness_claim": EXPECTED_COVERAGE,
            "push_tips_corpus_complete": EXPECTED_PUSH_TIPS_CORPUS_COMPLETE,
            "selected_paths": [item.relative_path for item in preflight.manifest.files],
            "full_core_tutorial_scan": True,
            "logical_identity_created": False,
            "logical_passage_created": False,
            "mcp_capability_added": False,
            "context_scope": "full_core_tutorial_source_closure",
        },
    }


def ingest_tutorial_phase4d2b_evidence_bundle(
    db_path: str | Path,
    source_root: str | Path,
    manifest_path: str | Path,
) -> dict[str, Any]:
    _require_posix_builder_runtime()
    target = _require_regular_file(db_path, "initial validation")
    preflight = preflight_tutorial_phase4d2b_evidence_bundle(source_root, manifest_path)
    try:
        original_identity = _capture_identity(target)
        existing, versions = _inspect_target(target, preflight)
        _assert_identity(target, original_identity, "after read-only inspection")
        if existing:
            _assert_sidecars_absent(target, "before report")
            final_identity = _capture_identity(target)
            return _build_report(target, preflight, final_identity)
        temporary = _backup(target, original_identity)
        replaced = False
        try:
            with Database(temporary) as db:
                conn = db.conn
                conn.execute("BEGIN IMMEDIATE")
                try:
                    if versions == list(range(1, 11)):
                        _execute_migration_0011(conn)
                        conn.execute("INSERT INTO schema_migration(version,applied_at) VALUES(?,?)", (11, EXPECTED_OBSERVED_AT))
                    if [int(row[0]) for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")] != list(range(1, 12)):
                        raise sqlite3.IntegrityError("Tutorial full-boundary migration did not produce exact Schema 11")
                    verify_tutorial_phase4d2a_schema_signature(conn)
                    _write_source_and_occurrences(db, preflight)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            with sqlite3.connect(temporary.resolve().as_uri() + "?mode=ro", uri=True) as readonly:
                readonly.row_factory = sqlite3.Row
                readonly.execute("PRAGMA query_only=ON")
                _verify_complete_bundle(readonly, preflight)
            os.chmod(temporary, stat.S_IMODE(original_identity.mode))
            _finalize(target, temporary, original_identity)
            replaced = True
        finally:
            if not replaced:
                _cleanup(temporary)
    except TutorialPhase4D2BEvidenceError:
        raise
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, RuntimeError, TutorialPhase4D2ASchemaError) as exc:
        raise TutorialPhase4D2BEvidenceError(str(exc)) from exc
    _assert_sidecars_absent(target, "after finalization")
    final_identity = _capture_identity(target)
    return _build_report(target, preflight, final_identity)


# Family-specific aliases intentionally do not expose a generic full-boundary adapter.
preflight_phase4d2b_tutorial_evidence = preflight_tutorial_phase4d2b_evidence_bundle
ingest_phase4d2b_tutorial_evidence = ingest_tutorial_phase4d2b_evidence_bundle


__all__ = [
    "TutorialPhase4D2BEvidenceError",
    "TutorialPhase4D2BPreflightResult",
    "ingest_phase4d2b_tutorial_evidence",
    "ingest_tutorial_phase4d2b_evidence_bundle",
    "preflight_phase4d2b_tutorial_evidence",
    "preflight_tutorial_phase4d2b_evidence_bundle",
]
