"""Phase 4D2A bounded Core Tutorial raw-evidence canary builder.

All source parsing and manifest checks happen before a write-capable database
connection is opened.  The append is prepared on a consistent SQLite copy and
installed with a same-directory CAS rename, so migration and occurrence writes
roll back as one unit if any check fails.
"""

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
from .tutorial_phase4d2a_contract import (
    RUN_FAMILY,
    TutorialPhase4D2AContractError,
    derive_tutorial_phase4d2a_source_contract,
    expected_identity,
    occurrence_id,
)
from .tutorial_phase4d2a_manifest import (
    EXPECTED_MANIFEST_ID,
    EXPECTED_OBSERVED_AT,
    EXPECTED_SOURCE_ID,
    EXPECTED_VERSIONS,
    TutorialPhase4D2AManifest,
    TutorialPhase4D2AManifestError,
    load_tutorial_phase4d2a_manifest,
)
from .tutorial_phase4d2a_schema import (
    TUTORIAL_MIGRATION_0011_SQL_SHA256,
    TutorialPhase4D2ASchemaError,
    verify_tutorial_phase4d2a_schema_signature,
)
from .util import canonical_json, sha256_bytes


class TutorialPhase4D2AEvidenceError(RuntimeError):
    """The source, manifest, database, or atomic append failed closed."""


@dataclass(frozen=True)
class TutorialPhase4D2APreflightResult:
    manifest: TutorialPhase4D2AManifest
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
    raise TutorialPhase4D2AEvidenceError(message)


def _require_posix_builder_runtime() -> None:
    if os.name != "posix" or not callable(getattr(os, "replace", None)) or not callable(getattr(sqlite3.Connection, "backup", None)):
        _fail("Tutorial Phase 4D2A builder requires POSIX atomic rename and SQLite backup support")


def _manifest_raw_sha(manifest: TutorialPhase4D2AManifest) -> str:
    return manifest.raw_sha256


def _expected_run_scope(preflight: TutorialPhase4D2APreflightResult) -> str:
    return canonical_json({
        "family": RUN_FAMILY,
        "manifest_id": preflight.manifest.manifest_id,
        "selected_paths": [item.relative_path for item in preflight.manifest.files],
        "candidate_scan_scope": "full_TutorialDetail_and_PushTips_native_locator_inventory",
    })


def _expected_run_config(preflight: TutorialPhase4D2APreflightResult) -> str:
    return canonical_json({
        "manifest_raw_sha256": _manifest_raw_sha(preflight.manifest),
        "versions": preflight.manifest.versions,
        "logical_identity_created": False,
        "mcp_capability_added": False,
        "full_core_tutorial_scan": False,
    })


def _accepted_snapshot_metadata(value: Any) -> bool:
    # A shared parser-independent snapshot may have been sealed by an earlier
    # family.  Never rewrite that row merely to mark this canary; newly
    # created snapshots carry the more specific marker below.
    return value in {
        canonical_json({"sealed_source_manifest": True}),
        canonical_json({"sealed_source_manifest": True, "phase4d2a_canary": True}),
    }


def preflight_tutorial_phase4d2a_evidence_bundle(
    source_root: str | Path,
    manifest_path: str | Path,
) -> TutorialPhase4D2APreflightResult:
    if not isinstance(manifest_path, (str, Path)):
        _fail("Tutorial Phase 4D2A API requires a filesystem manifest path")
    try:
        manifest = load_tutorial_phase4d2a_manifest(manifest_path)
        derived = derive_tutorial_phase4d2a_source_contract(source_root, manifest)
    except (TutorialPhase4D2AManifestError, TutorialPhase4D2AContractError) as exc:
        raise TutorialPhase4D2AEvidenceError(str(exc)) from exc
    expected = manifest.expected
    for key in ("counts", "status_counts", "selected_table_counts", "text_owner_field_counts", "occurrence_set_digests"):
        if derived[key] != expected[key]:
            _fail(f"Tutorial Phase 4D2A {key} mismatch against sealed manifest")
    identity = expected_identity(manifest)
    # Manifest fields intentionally carry these exact source identities.  The
    # parser/run IDs are distinct from the parser-independent snapshot.
    if identity["snapshot_id"] != manifest.source["snapshot_id"]:
        _fail("Tutorial Phase 4D2A snapshot identity mismatch")
    return TutorialPhase4D2APreflightResult(
        manifest,
        Path(source_root).resolve(),
        derived=derived,
        **identity,
    )


def _ensure_row(
    conn: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    values: tuple[Any, ...],
    *,
    insert_missing: bool,
) -> None:
    row = conn.execute(
        f"SELECT {','.join(columns)} FROM {table} WHERE id=?",
        (values[0],),
    ).fetchone()
    if row is None:
        if not insert_missing:
            raise sqlite3.IntegrityError(f"missing Tutorial {table} occurrence {values[0]}")
        conn.execute(
            f"INSERT INTO {table}({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            values,
        )
        return
    if tuple(row[column] for column in columns) != values:
        raise sqlite3.IntegrityError(f"immutable Tutorial {table} occurrence mismatch: {values[0]}")


def _ids(preflight: TutorialPhase4D2APreflightResult) -> dict[str, dict[Any, str]]:
    identity = {
        "snapshot_id": preflight.snapshot_id,
        "parser_build_id": preflight.parser_build_id,
        "ingest_run_id": preflight.ingest_run_id,
    }
    rows = {
        (item["table"], item["row_index"]): occurrence_id("row", identity, item["table"], item["relative_path"], item["pointer"])
        for item in preflight.derived["rows"]
    }
    raw_fields = {
        (item["source_table"], item["source_row_index"], item["source_pointer"]): occurrence_id("raw-field", identity, item["source_table"], item["source_row_index"], item["source_pointer"])
        for item in preflight.derived["raw_fields"]
    }
    detail = {
        item["source_pointer"]: occurrence_id("detail-relation", identity, item["source_table"], item["source_row_index"], item["source_pointer"])
        for item in preflight.derived["detail_relations"]
    }
    push = {
        item["source_pointer"]: occurrence_id("push-tips", identity, item["source_table"], item["source_row_index"], item["source_pointer"])
        for item in preflight.derived["push_relations"]
    }
    owners = {
        item["owner_key"]: occurrence_id("text-owner", identity, item["owner_key"])
        for item in preflight.derived["text_owners"]
    }
    resolutions = {
        (item["owner_key"], item["locale"]): occurrence_id("text-resolution", identity, item["owner_key"], item["locale"])
        for item in preflight.derived["text_resolutions"]
    }
    images = {
        item["source_pointer"]: occurrence_id("image-reference", identity, item["source_table"], item["source_row_index"], item["source_pointer"])
        for item in preflight.derived["image_references"]
    }
    return {"rows": rows, "raw_fields": raw_fields, "detail": detail, "push": push, "owners": owners, "resolutions": resolutions, "images": images}


def _row_id(ids: dict[str, dict[Any, str]], row_key: str) -> str:
    """Resolve the contract's stable ``TableName:index`` row key."""
    try:
        table, raw_index = row_key.split(":", 1)
        index = int(raw_index)
    except (AttributeError, ValueError) as exc:
        raise sqlite3.IntegrityError(f"invalid Tutorial target row key: {row_key!r}") from exc
    try:
        return ids["rows"][(table, index)]
    except KeyError as exc:
        raise sqlite3.IntegrityError(f"Tutorial target row is outside selected canary: {row_key!r}") from exc


def _write_occurrence_rows(
    conn: sqlite3.Connection,
    preflight: TutorialPhase4D2APreflightResult,
    *,
    insert_missing: bool,
) -> None:
    ids = _ids(preflight)
    # Source/provenance IDs use the project's canonical digest implementation.
    from .util import digest_id
    file_ids = {
        item.relative_path: digest_id("sf", preflight.snapshot_id, item.relative_path)
        for item in preflight.manifest.files
    }
    record_ids = {
        (item["relative_path"], item["source_pointer"]): digest_id("sr", file_ids[item["relative_path"]], item["source_pointer"])
        for item in preflight.derived["source_records"]
    }
    row_records = {
        (item["table"], item["row_index"]): record_ids[(item["relative_path"], item["pointer"])]
        for item in preflight.derived["rows"]
    }
    for item in preflight.derived["rows"]:
        key = (item["table"], item["row_index"])
        _ensure_row(
            conn,
            "tutorial_row_occurrence",
            (
                "id", "ingest_run_id", "snapshot_id", "parser_build_id", "source_file_id", "source_record_id", "source_pointer", "table_name", "family_role", "row_index", "native_locator_field", "native_locator_value", "raw_checksum", "metadata_json",
            ),
            (
                ids["rows"][key], preflight.ingest_run_id, preflight.snapshot_id, preflight.parser_build_id,
                file_ids[item["relative_path"]], row_records[key], item["pointer"], item["table"], item["family_role"], item["row_index"], item["native_locator_field"], item["native_locator_value"], item["raw_checksum"],
                canonical_json({"logical_identity_created": False, "raw_evidence_identity": True}),
            ),
            insert_missing=insert_missing,
        )
    for item in preflight.derived["raw_fields"]:
        key = (item["source_table"], item["source_row_index"])
        raw_metadata = {"identity_effect": "none"}
        if item.get("missing_field") is True:
            # Full-boundary scans represent an absent relationship/asset field
            # as an explicit raw coordinate rather than silently dropping it.
            raw_metadata["missing_field"] = True
        _ensure_row(
            conn,
            "tutorial_raw_field_occurrence",
            (
                "id", "ingest_run_id", "snapshot_id", "parser_build_id", "row_occurrence_id", "source_record_id", "source_pointer", "field_name", "array_index", "field_kind", "raw_json", "metadata_json",
            ),
            (
                ids["raw_fields"][(item["source_table"], item["source_row_index"], item["source_pointer"])], preflight.ingest_run_id, preflight.snapshot_id, preflight.parser_build_id, ids["rows"][key], row_records[key], item["source_pointer"], item["field_name"], item["array_index"], item["field_kind"], canonical_json(item["raw_value"]), canonical_json(raw_metadata),
            ),
            insert_missing=insert_missing,
        )
    for item in preflight.derived["detail_relations"]:
        key = (item["source_table"], item["source_row_index"])
        target = None if item["target_row_key"] is None else _row_id(ids, item["target_row_key"])
        relation_id = ids["detail"][item["source_pointer"]]
        _ensure_row(
            conn,
            "tutorial_detail_relation_occurrence",
            (
                "id", "ingest_run_id", "snapshot_id", "parser_build_id", "source_row_occurrence_id", "source_record_id", "source_pointer", "field_name", "array_index", "raw_value_json", "candidate_key_json", "resolution_status", "target_row_occurrence_id", "candidate_scopes_json", "metadata_json",
            ),
            (
                relation_id, preflight.ingest_run_id, preflight.snapshot_id, preflight.parser_build_id, ids["rows"][key], row_records[key], item["source_pointer"], item["field_name"], item["array_index"], None if item["raw_value"] is None else canonical_json(item["raw_value"]), canonical_json(item["candidate_key"]), item["resolution_status"], target, canonical_json(item["candidate_scopes"]), canonical_json({"raw_value_preserved": True, "logical_identity_created": False}),
            ),
            insert_missing=insert_missing,
        )
    for item in preflight.derived["push_relations"]:
        key = (item["source_table"], item["source_row_index"])
        target = None if item["target_row_key"] is None else _row_id(ids, item["target_row_key"])
        _ensure_row(
            conn,
            "tutorial_push_tips_candidate_occurrence",
            (
                "id", "ingest_run_id", "snapshot_id", "parser_build_id", "source_row_occurrence_id", "source_record_id", "source_pointer", "field_name", "raw_value_json", "candidate_key_json", "resolution_status", "target_row_occurrence_id", "candidate_scopes_json", "metadata_json",
            ),
            (
                ids["push"][item["source_pointer"]], preflight.ingest_run_id, preflight.snapshot_id, preflight.parser_build_id, ids["rows"][key], row_records[key], item["source_pointer"], item["field_name"], None if item["raw_value"] is None else canonical_json(item["raw_value"]), canonical_json(item["candidate_key"]), item["resolution_status"], target, canonical_json(item["candidate_scopes"]), canonical_json({"lookup_only": True, "tutorial_merge": False}),
            ),
            insert_missing=insert_missing,
        )
    for item in preflight.derived["text_owners"]:
        key = (item["table"], item["row_index"])
        _ensure_row(
            conn,
            "tutorial_text_owner_occurrence",
            (
                "id", "ingest_run_id", "snapshot_id", "parser_build_id", "source_row_occurrence_id", "source_record_id", "source_pointer", "field_name", "field_role", "textmap_hash", "raw_value_json", "metadata_json",
            ),
            (
                ids["owners"][item["owner_key"]], preflight.ingest_run_id, preflight.snapshot_id, preflight.parser_build_id, ids["rows"][key], row_records[key], item["source_pointer"], item["field_name"], item["field_role"], str(item["textmap_hash"]), canonical_json(item["raw_value"]), canonical_json({"owner_identity_effect": "independent_owner_occurrence_no_hash_or_text_merge"}),
            ),
            insert_missing=insert_missing,
        )
    for item in preflight.derived["text_resolutions"]:
        key = (item["owner_table"], item["owner_row_index"])
        owner_key = item["owner_key"]
        owner = next(value for value in preflight.derived["text_owners"] if value["owner_key"] == owner_key)
        _ensure_row(
            conn,
            "tutorial_text_resolution_occurrence",
            (
                "id", "ingest_run_id", "snapshot_id", "parser_build_id", "owner_occurrence_id", "source_row_occurrence_id", "source_record_id", "source_pointer", "locale", "textmap_hash", "resolution_status", "raw_text", "normalized_text", "raw_checksum", "normalized_checksum", "candidate_scopes_json", "metadata_json",
            ),
            (
                ids["resolutions"][(owner_key, item["locale"])], preflight.ingest_run_id, preflight.snapshot_id, preflight.parser_build_id, ids["owners"][owner_key], ids["rows"][key], row_records[key], owner["source_pointer"], item["locale"], str(item["textmap_hash"]), item["resolution_status"], item["raw_text"], item["normalized_text"], item["raw_checksum"], item["normalized_checksum"], canonical_json(item["candidate_scopes"]), canonical_json({"missing_is_legal": True, "hash_identity_effect": "none"}),
            ),
            insert_missing=insert_missing,
        )
    for item in preflight.derived["text_candidates"]:
        owner_key = item["owner_key"]
        resolution_id = ids["resolutions"][(owner_key, item["locale"])]
        candidate_id = occurrence_id("text-candidate", {"snapshot_id": preflight.snapshot_id, "parser_build_id": preflight.parser_build_id, "ingest_run_id": preflight.ingest_run_id}, owner_key, item["locale"], item["shard_kind"], item["source_pointer"])
        _ensure_row(
            conn,
            "tutorial_text_candidate_occurrence",
            (
                "id", "ingest_run_id", "snapshot_id", "parser_build_id", "resolution_occurrence_id", "source_file_id", "source_record_id", "source_pointer", "shard_kind", "locale", "resolution_status", "raw_text", "normalized_text", "raw_checksum", "normalized_checksum", "metadata_json",
            ),
            (
                candidate_id, preflight.ingest_run_id, preflight.snapshot_id, preflight.parser_build_id, resolution_id, file_ids[item["source_relative_path"]], record_ids[(item["source_relative_path"], item["source_pointer"])], item["source_pointer"], item["shard_kind"], item["locale"], item["resolution_status"], item["raw_text"], item["normalized_text"], item["raw_checksum"], item["normalized_checksum"], canonical_json({"candidate_identity_effect": "none", "owner_key": owner_key}),
            ),
            insert_missing=insert_missing,
        )
    for item in preflight.derived["image_references"]:
        key = (item["source_table"], item["source_row_index"])
        _ensure_row(
            conn,
            "tutorial_image_reference_occurrence",
            (
                "id", "ingest_run_id", "snapshot_id", "parser_build_id", "source_row_occurrence_id", "source_record_id", "source_pointer", "field_name", "array_index", "raw_image_name", "reference_status", "metadata_json",
            ),
            (
                ids["images"][item["source_pointer"]], preflight.ingest_run_id, preflight.snapshot_id, preflight.parser_build_id, ids["rows"][key], row_records[key], item["source_pointer"], item["field_name"], item["array_index"], item["raw_image_name"] if isinstance(item["raw_image_name"], str) else None, item["reference_status"], canonical_json({"image_identity_created": False}),
            ),
            insert_missing=insert_missing,
        )


def _schema_versions(conn: sqlite3.Connection) -> list[int]:
    return [int(row[0]) for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")]


_COUNT_TABLES = {
    "rows": "tutorial_row_occurrence",
    "raw_fields": "tutorial_raw_field_occurrence",
    "detail_relations": "tutorial_detail_relation_occurrence",
    "push_tips_relations": "tutorial_push_tips_candidate_occurrence",
    "text_owners": "tutorial_text_owner_occurrence",
    "text_resolutions": "tutorial_text_resolution_occurrence",
    "text_candidates": "tutorial_text_candidate_occurrence",
    "image_references": "tutorial_image_reference_occurrence",
}


def _assert_table_scope(conn: sqlite3.Connection, preflight: TutorialPhase4D2APreflightResult, *, expect_complete: bool) -> None:
    for key, table in _COUNT_TABLES.items():
        total = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        scoped = int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE ingest_run_id=?", (preflight.ingest_run_id,)).fetchone()[0])
        expected = int(preflight.derived["counts"][key]) if expect_complete else 0
        if total != expected or scoped != expected:
            raise sqlite3.IntegrityError(f"Tutorial {table} expected-set pollution: total={total} scoped={scoped} expected={expected}")


def _assert_source_run_contract(conn: sqlite3.Connection, preflight: TutorialPhase4D2APreflightResult) -> None:
    source = preflight.manifest.source
    catalog = conn.execute("SELECT kind,url,license_note FROM source_catalog WHERE id=?", (EXPECTED_SOURCE_ID,)).fetchone()
    if catalog is None or tuple(catalog) != ("primary", "https://github.com/DimbreathBot/AnimeGameData", None):
        raise sqlite3.IntegrityError("Tutorial source_catalog mismatch")
    snapshot = conn.execute('SELECT source_id,release_label,build,"commit",tree,observed_at,status,metadata_json FROM source_snapshot WHERE id=?', (preflight.snapshot_id,)).fetchone()
    expected_snapshot_identity = (source["source_id"], source["release_label"], source["build"], source["commit"], source["tree"], source["observed_at"], "active")
    if snapshot is None or tuple(snapshot)[:7] != expected_snapshot_identity or not _accepted_snapshot_metadata(tuple(snapshot)[7]):
        raise sqlite3.IntegrityError("Tutorial source_snapshot mismatch")
    parser = conn.execute("SELECT parser_version,mapping_version,fingerprint,config_checksum,created_at FROM parser_build WHERE id=?", (preflight.parser_build_id,)).fetchone()
    if parser is None or tuple(parser) != (EXPECTED_VERSIONS["parser"], EXPECTED_VERSIONS["projection"], "tutorial-phase4d2a-sealed-core-canary", preflight.config_checksum, source["observed_at"]):
        raise sqlite3.IntegrityError("Tutorial parser_build mismatch")
    run = conn.execute("SELECT snapshot_id,parser_build_id,scope_json,config_json,status,created_at FROM ingest_run WHERE id=?", (preflight.ingest_run_id,)).fetchone()
    if run is None or tuple(run) != (preflight.snapshot_id, preflight.parser_build_id, _expected_run_scope(preflight), _expected_run_config(preflight), "complete", source["observed_at"]):
        raise sqlite3.IntegrityError("Tutorial ingest_run mismatch")
    family_ids = []
    for row in conn.execute("SELECT id,scope_json FROM ingest_run ORDER BY id"):
        try:
            scope = json.loads(row[1])
        except (TypeError, json.JSONDecodeError) as exc:
            raise sqlite3.IntegrityError("Tutorial ingest_run scope is invalid") from exc
        if isinstance(scope, dict) and scope.get("family") == RUN_FAMILY:
            family_ids.append(str(row[0]))
    if family_ids != [preflight.ingest_run_id]:
        raise sqlite3.IntegrityError(f"Tutorial same-family run set mismatch: {family_ids}")
    for item in preflight.manifest.files:
        file_id = __import__("genshin_lore.util", fromlist=["digest_id"]).digest_id("sf", preflight.snapshot_id, item.relative_path)
        row = conn.execute("SELECT snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path FROM source_file WHERE id=?", (file_id,)).fetchone()
        actual = None if row is None else tuple(row)
        oid_ok = actual is not None and (actual[2] == item.blob_oid or (item.relative_path.startswith("TextMap/") and actual[2] is None))
        if actual is None or actual[0:2] != (preflight.snapshot_id, item.relative_path) or actual[3:6] != (item.sha256, item.size_bytes, item.format) or not oid_ok or actual[6] != str(preflight.source_root):
            raise sqlite3.IntegrityError(f"Tutorial source_file mismatch: {item.relative_path}")
    from .util import digest_id
    for item in preflight.derived["source_records"]:
        file_id = digest_id("sf", preflight.snapshot_id, item["relative_path"])
        record_id = digest_id("sr", file_id, item["source_pointer"])
        row = conn.execute("SELECT source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json FROM source_record WHERE id=?", (record_id,)).fetchone()
        if row is None or tuple(row) != (file_id, item["source_pointer"], item["record_kind"], item["native_id"], item["raw_checksum"], canonical_json(item["raw_value"])):
            raise sqlite3.IntegrityError(f"Tutorial source_record mismatch: {item['relative_path']}#{item['source_pointer']}")


def verify_tutorial_phase4d2a_occurrence_rows(conn: sqlite3.Connection, preflight: TutorialPhase4D2APreflightResult, *, insert_missing: bool) -> None:
    """Insert/verify deterministic rows; used inside the builder transaction."""
    _write_occurrence_rows(conn, preflight, insert_missing=insert_missing)


def _verify_existing_complete_bundle(conn: sqlite3.Connection, preflight: TutorialPhase4D2APreflightResult) -> None:
    if _schema_versions(conn) != list(range(1, 12)):
        raise sqlite3.IntegrityError("existing Tutorial canary schema is not exactly 1..11")
    verify_tutorial_phase4d2a_schema_signature(conn)
    _assert_source_run_contract(conn, preflight)
    verify_tutorial_phase4d2a_occurrence_rows(conn, preflight, insert_missing=False)
    _assert_table_scope(conn, preflight, expect_complete=True)
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise sqlite3.IntegrityError("Tutorial foreign-key check failed")


def _absolute_input_path(value: str | Path) -> Path:
    """Return an absolute lexical path without following its final symlink."""
    try:
        return Path(os.path.abspath(os.fspath(value)))
    except (TypeError, ValueError, OSError) as exc:
        _fail(f"Tutorial database path is invalid: {value!r}")
        raise AssertionError from exc


def _require_regular_file(value: str | Path, stage: str) -> Path:
    """lstat the caller-supplied path before any target read/open operation."""
    path = _absolute_input_path(value)
    try:
        info = os.lstat(path)
    except OSError as exc:
        _fail(f"Tutorial evidence database is not readable at {stage}: {path}: {exc}")
        raise AssertionError from exc
    if stat.S_ISLNK(info.st_mode):
        _fail(f"Tutorial evidence database symlink is forbidden at {stage}: {path}")
    if not stat.S_ISREG(info.st_mode):
        _fail(f"Tutorial evidence database must be a regular file at {stage}: {path}")
    return path


def _fingerprint(target: Path) -> dict[str, Any]:
    identity = _capture_identity(target)
    return {"sha256": identity.sha256, "size_bytes": identity.size_bytes, "mtime_ns": identity.mtime_ns}


def _capture_identity(target: Path) -> _DatabaseIdentity:
    try:
        before = os.lstat(target)
    except OSError as exc:
        _fail(f"Tutorial evidence database cannot be lstat'ed: {target}: {exc}")
        raise AssertionError from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        _fail(f"Tutorial evidence database must remain a regular non-symlink file: {target}")
    raw = target.read_bytes()
    try:
        after = os.lstat(target)
    except OSError as exc:
        _fail(f"Tutorial evidence database disappeared while capturing CAS identity: {target}: {exc}")
        raise AssertionError from exc
    if stat.S_ISLNK(after.st_mode) or not stat.S_ISREG(after.st_mode):
        _fail(f"Tutorial evidence database changed to a non-regular path while capturing CAS identity: {target}")
    if (before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns) or len(raw) != after.st_size:
        _fail("Tutorial database changed while capturing CAS identity")
    return _DatabaseIdentity(after.st_dev, after.st_ino, after.st_mode, sha256_bytes(raw), after.st_size, after.st_mtime_ns)


def _assert_identity(target: Path, expected: _DatabaseIdentity, stage: str) -> None:
    actual = _capture_identity(target)
    if actual != expected:
        _fail(f"Tutorial database CAS mismatch {stage}: expected={expected} actual={actual}")


def _assert_no_sidecars(target: Path, stage: str) -> None:
    present = [str(Path(str(target) + suffix)) for suffix in ("-journal", "-wal", "-shm") if Path(str(target) + suffix).exists()]
    if present:
        _fail(f"Tutorial builder requires sidecar-free database {stage}: {present}")


def _assert_delete_journal(conn: sqlite3.Connection) -> None:
    if str(conn.execute("PRAGMA journal_mode").fetchone()[0]).casefold() != "delete":
        _fail("Tutorial builder requires DELETE journal mode")


def _temporary_path(target: Path) -> Path:
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.phase4d2a-", suffix=".sqlite", dir=target.parent)
    os.close(fd)
    return Path(name)


def _cleanup(path: Path) -> None:
    for item in (path, *(Path(str(path) + suffix) for suffix in ("-journal", "-wal", "-shm"))):
        try:
            item.unlink()
        except FileNotFoundError:
            pass


def _backup(target: Path, expected: _DatabaseIdentity) -> Path:
    _assert_no_sidecars(target, "before backup")
    _assert_identity(target, expected, "before backup")
    temporary = _temporary_path(target)
    try:
        source_uri = target.as_uri() + "?mode=ro"
        with sqlite3.connect(source_uri, uri=True, timeout=0) as source, sqlite3.connect(temporary) as destination:
            source.execute("PRAGMA query_only=ON")
            source.execute("PRAGMA busy_timeout=0")
            _assert_delete_journal(source)
            source.backup(destination)
            destination.commit()
        _assert_no_sidecars(target, "after backup")
        _assert_identity(target, expected, "after backup")
        return temporary
    except Exception:
        _cleanup(temporary)
        raise


def _finalize(target: Path, temporary: Path, expected: _DatabaseIdentity) -> None:
    _assert_no_sidecars(target, "before finalization")
    _assert_identity(target, expected, "before finalization")
    uri = target.as_uri() + "?mode=rw"
    lock = sqlite3.connect(uri, uri=True, timeout=0, isolation_level=None)
    locked = False
    try:
        lock.execute("PRAGMA busy_timeout=0")
        _assert_delete_journal(lock)
        lock.execute("BEGIN EXCLUSIVE")
        locked = True
        _assert_no_sidecars(target, "under finalization lock")
        _assert_identity(target, expected, "under finalization lock")
        os.replace(temporary, target)
    finally:
        if locked:
            try:
                lock.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        lock.close()


def _execute_migration_0011(conn: sqlite3.Connection) -> None:
    paths = sorted(MIGRATIONS_DIR.glob("0011_phase4d2a_tutorial_canary_evidence.sql"))
    if len(paths) != 1:
        raise sqlite3.IntegrityError(f"expected exactly one Tutorial Phase 4D2A migration, found {len(paths)}")
    path = paths[0]
    raw_script = path.read_bytes()
    actual_sha256 = hashlib.sha256(raw_script).hexdigest()
    if actual_sha256 != TUTORIAL_MIGRATION_0011_SQL_SHA256:
        raise sqlite3.IntegrityError(
            f"Tutorial Phase 4D2A migration SQL seal mismatch: "
            f"expected {TUTORIAL_MIGRATION_0011_SQL_SHA256}, got {actual_sha256}"
        )
    for statement in Database._sql_statements(raw_script.decode("utf-8-sig"), path):
        Database._validate_migration_statement(statement, path)
        conn.execute(statement)


def _write_source_and_occurrences(db: Database, preflight: TutorialPhase4D2APreflightResult) -> None:
    conn = db.conn
    source = preflight.manifest.source
    from .util import digest_id
    catalog = conn.execute("SELECT kind,url,license_note FROM source_catalog WHERE id=?", (EXPECTED_SOURCE_ID,)).fetchone()
    if catalog is None:
        conn.execute("INSERT INTO source_catalog(id,kind,url,license_note) VALUES(?,?,?,?)", (EXPECTED_SOURCE_ID, "primary", "https://github.com/DimbreathBot/AnimeGameData", None))
    elif tuple(catalog) != ("primary", "https://github.com/DimbreathBot/AnimeGameData", None):
        raise sqlite3.IntegrityError("Tutorial source_catalog identity mismatch")
    expected_snapshot_identity = (source["source_id"], source["release_label"], source["build"], source["commit"], source["tree"], source["observed_at"], "active")
    expected_snapshot_metadata = canonical_json({"sealed_source_manifest": True, "phase4d2a_canary": True})
    snapshot = conn.execute('SELECT source_id,release_label,build,"commit",tree,observed_at,status,metadata_json FROM source_snapshot WHERE id=?', (preflight.snapshot_id,)).fetchone()
    if snapshot is None:
        conn.execute('INSERT INTO source_snapshot(id,source_id,release_label,build,"commit",tree,observed_at,parser_version,mapping_version,status,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)', (preflight.snapshot_id, *expected_snapshot_identity[:6], "legacy-unscoped", "legacy-unscoped", expected_snapshot_identity[6], expected_snapshot_metadata))
    elif tuple(snapshot)[:7] != expected_snapshot_identity or not _accepted_snapshot_metadata(tuple(snapshot)[7]):
        raise sqlite3.IntegrityError("Tutorial source_snapshot identity mismatch")
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
            if actual[0:2] != expected_file[0:2] or actual[3:6] != expected_file[3:] or not oid_ok or actual[6] != str(preflight.source_root):
                raise sqlite3.IntegrityError(f"Tutorial source_file identity mismatch: {item.relative_path}")
    record_ids: dict[tuple[str, str], str] = {}
    for item in preflight.derived["source_records"]:
        file_id = file_ids[item["relative_path"]]
        record_id = digest_id("sr", file_id, item["source_pointer"])
        record_ids[(item["relative_path"], item["source_pointer"])] = record_id
        values = (record_id, file_id, item["source_pointer"], item["record_kind"], item["native_id"], item["raw_checksum"], canonical_json(item["raw_value"]))
        row = conn.execute("SELECT source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json FROM source_record WHERE id=?", (record_id,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO source_record(id,source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json) VALUES(?,?,?,?,?,?,?)", values)
        elif tuple(row) != values[1:]:
            raise sqlite3.IntegrityError(f"Tutorial source_record identity mismatch: {item['relative_path']}#{item['source_pointer']}")
    parser_values = (preflight.parser_build_id, EXPECTED_VERSIONS["parser"], EXPECTED_VERSIONS["projection"], "tutorial-phase4d2a-sealed-core-canary", preflight.config_checksum, source["observed_at"])
    row = conn.execute("SELECT parser_version,mapping_version,fingerprint,config_checksum,created_at FROM parser_build WHERE id=?", (preflight.parser_build_id,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO parser_build(id,parser_version,mapping_version,fingerprint,config_checksum,created_at) VALUES(?,?,?,?,?,?)", parser_values)
    elif tuple(row) != parser_values[1:]:
        raise sqlite3.IntegrityError("Tutorial parser_build identity mismatch")
    if conn.execute("SELECT 1 FROM ingest_run WHERE id=?", (preflight.ingest_run_id,)).fetchone() is not None:
        raise sqlite3.IntegrityError("Tutorial ingest run appeared after read-only preflight")
    conn.execute("INSERT INTO ingest_run(id,snapshot_id,parser_build_id,scope_json,config_json,status,created_at) VALUES(?,?,?,?,?,?,?)", (preflight.ingest_run_id, preflight.snapshot_id, preflight.parser_build_id, _expected_run_scope(preflight), _expected_run_config(preflight), "running", source["observed_at"]))
    verify_tutorial_phase4d2a_occurrence_rows(conn, preflight, insert_missing=True)
    if conn.execute("UPDATE ingest_run SET status='complete' WHERE id=? AND status='running'", (preflight.ingest_run_id,)).rowcount != 1:
        raise sqlite3.IntegrityError("Tutorial ingest completion transition failed")
    _verify_existing_complete_bundle(conn, preflight)


def _inspect_target(target: Path, preflight: TutorialPhase4D2APreflightResult) -> tuple[bool, list[int]]:
    # Re-lstat immediately before the first SQLite open; ``resolve()`` must
    # never turn a caller-supplied symlink into an accepted target.
    _require_regular_file(target, "read-only inspection")
    _assert_no_sidecars(target, "read-only inspection")
    uri = target.as_uri() + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=0) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            _assert_delete_journal(conn)
            versions = _schema_versions(conn)
            if versions not in (list(range(1, 11)), list(range(1, 12))):
                raise sqlite3.IntegrityError(f"Tutorial input schema must be exactly 1..10 or 1..11: {versions}")
            family_ids = []
            for row in conn.execute("SELECT id,scope_json FROM ingest_run ORDER BY id"):
                try:
                    scope = json.loads(row[1])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise sqlite3.IntegrityError("Tutorial ingest_run scope is invalid") from exc
                if isinstance(scope, dict) and scope.get("family") == RUN_FAMILY:
                    family_ids.append(str(row[0]))
            expected = conn.execute("SELECT 1 FROM ingest_run WHERE id=?", (preflight.ingest_run_id,)).fetchone() is not None
            if family_ids not in ([], [preflight.ingest_run_id]):
                raise sqlite3.IntegrityError(f"Tutorial same-family ingest runs mismatch: {family_ids}")
            if versions[-1] == 11:
                verify_tutorial_phase4d2a_schema_signature(conn)
                if expected:
                    _verify_existing_complete_bundle(conn, preflight)
                else:
                    _assert_table_scope(conn, preflight, expect_complete=False)
            else:
                premature = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name GLOB 'tutorial_*' OR name GLOB 'trg_tutorial_*' OR name GLOB 'idx_tutorial_*' OR name GLOB 'uq_tutorial_*'").fetchone()[0]
                if premature:
                    raise sqlite3.IntegrityError("Schema 10 input contains premature Tutorial objects")
            return expected, versions
    except (sqlite3.Error, TutorialPhase4D2ASchemaError, OSError, ValueError, TypeError, KeyError) as exc:
        raise TutorialPhase4D2AEvidenceError(str(exc)) from exc


def _append_atomically(target: Path, preflight: TutorialPhase4D2APreflightResult, original_versions: list[int], original_identity: _DatabaseIdentity) -> None:
    temporary = _backup(target, original_identity)
    replaced = False
    try:
        with Database(temporary) as db:
            conn = db.conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                if original_versions == list(range(1, 11)):
                    _execute_migration_0011(conn)
                    conn.execute("INSERT INTO schema_migration(version,applied_at) VALUES(?,?)", (11, EXPECTED_OBSERVED_AT))
                if _schema_versions(conn) != list(range(1, 12)):
                    raise sqlite3.IntegrityError("Tutorial migration did not produce exact Schema 11")
                verify_tutorial_phase4d2a_schema_signature(conn)
                _write_source_and_occurrences(db, preflight)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        with sqlite3.connect(temporary.resolve().as_uri() + "?mode=ro", uri=True) as readonly:
            readonly.row_factory = sqlite3.Row
            readonly.execute("PRAGMA query_only=ON")
            _verify_existing_complete_bundle(readonly, preflight)
        os.chmod(temporary, stat.S_IMODE(original_identity.mode))
        _finalize(target, temporary, original_identity)
        replaced = True
    finally:
        if not replaced:
            _cleanup(temporary)


def _build_report(target: Path, preflight: TutorialPhase4D2APreflightResult) -> dict[str, Any]:
    return {
        "status": "complete",
        "db_path": str(target.resolve()),
        "snapshot_id": preflight.snapshot_id,
        "parser_build_id": preflight.parser_build_id,
        "ingest_run_id": preflight.ingest_run_id,
        "config_checksum": preflight.config_checksum,
        "manifest": {"manifest_id": preflight.manifest.manifest_id, "raw_sha256": preflight.manifest.raw_sha256},
        "database_fingerprint": _fingerprint(target),
        "sqlite_storage": {"journal_mode": "delete", "sidecars_absent": True},
        "counts": preflight.derived["counts"],
        "status_counts": preflight.derived["status_counts"],
        "selected_table_counts": preflight.derived["selected_table_counts"],
        "text_owner_field_counts": preflight.derived["text_owner_field_counts"],
        "occurrence_set_digests": preflight.derived["occurrence_set_digests"],
        "scope": {
            "selected_paths": [item.relative_path for item in preflight.manifest.files],
            "full_core_tutorial_scan": False,
            "candidate_scan_scope": "full_TutorialDetail_and_PushTips_native_locator_inventory",
            "logical_identity_created": False,
            "logical_passage_created": False,
            "mcp_capability_added": False,
            "context_scope": "single_physical_tutorial_row_container_only",
        },
    }


def ingest_tutorial_phase4d2a_evidence_bundle(
    db_path: str | Path,
    source_root: str | Path,
    manifest_path: str | Path,
) -> dict[str, Any]:
    _require_posix_builder_runtime()
    # lstat the caller-supplied target before any target read/open (including
    # preflight work), and preserve its lexical path through CAS replacement.
    target = _require_regular_file(db_path, "initial validation")
    # Source/manifest parsing and all bounded count checks finish before any
    # write-capable database connection or temporary backup is created.
    preflight = preflight_tutorial_phase4d2a_evidence_bundle(source_root, manifest_path)
    try:
        original_identity = _capture_identity(target)
        existing, versions = _inspect_target(target, preflight)
        _assert_identity(target, original_identity, "after read-only inspection")
        if existing:
            return _build_report(target, preflight)
        _append_atomically(target, preflight, versions, original_identity)
    except TutorialPhase4D2AEvidenceError:
        raise
    except (TutorialPhase4D2ASchemaError, sqlite3.Error, OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        raise TutorialPhase4D2AEvidenceError(str(exc)) from exc
    return _build_report(target, preflight)


# Short aliases make the family-specific API discoverable without introducing
# a generic Tutorial adapter.
preflight_phase4d2a_tutorial_evidence = preflight_tutorial_phase4d2a_evidence_bundle
ingest_phase4d2a_tutorial_evidence = ingest_tutorial_phase4d2a_evidence_bundle


__all__ = [
    "RUN_FAMILY",
    "TutorialPhase4D2AEvidenceError",
    "TutorialPhase4D2APreflightResult",
    "derive_tutorial_phase4d2a_source_contract",
    "ingest_phase4d2a_tutorial_evidence",
    "ingest_tutorial_phase4d2a_evidence_bundle",
    "preflight_phase4d2a_tutorial_evidence",
    "preflight_tutorial_phase4d2a_evidence_bundle",
    "verify_tutorial_phase4d2a_occurrence_rows",
]
