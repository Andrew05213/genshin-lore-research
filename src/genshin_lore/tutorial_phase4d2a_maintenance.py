"""Independent read-only release verification for the Phase 4D2A canary.

The verifier derives its expected tuples from the pinned source/manifest
contract and compares the persisted physical evidence rows directly.  It does
not import or call the canary builder, and it never opens SQLite write-capable.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from pathlib import Path
from typing import Any, Mapping

from .tutorial_phase4d2a_contract import (
    RUN_FAMILY,
    TutorialPhase4D2AContractError,
    derive_tutorial_phase4d2a_source_contract,
    expected_identity,
    occurrence_id,
    strict_json_file,
)
from .tutorial_phase4d2a_manifest import (
    EXPECTED_SOURCE_ID,
    EXPECTED_VERSIONS,
    TutorialPhase4D2AManifest,
    TutorialPhase4D2AManifestError,
    load_tutorial_phase4d2a_manifest,
)
from .tutorial_phase4d2a_schema import (
    TUTORIAL_IMMUTABLE_TRIGGERS,
    TUTORIAL_INDEXES,
    TUTORIAL_TABLES,
    TutorialPhase4D2ASchemaError,
    verify_tutorial_phase4d2a_schema_signature,
)
from .util import canonical_json, digest_id


class TutorialPhase4D2AVerificationError(RuntimeError):
    """The M1 Tutorial canary is not an exact release artifact."""


def _fail(message: str) -> None:
    raise TutorialPhase4D2AVerificationError(message)


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
        _fail(f"Tutorial evidence database is not readable at {stage}: {path}: {exc}")
        raise AssertionError from exc
    if stat.S_ISLNK(info.st_mode):
        _fail(f"Tutorial evidence database symlink is forbidden at {stage}: {path}")
    if not stat.S_ISREG(info.st_mode):
        _fail(f"Tutorial evidence database must be a regular file at {stage}: {path}")
    return path


def _sidecars(path: Path) -> tuple[Path, Path, Path]:
    return tuple(Path(f"{path}{suffix}") for suffix in ("-journal", "-wal", "-shm"))


def _assert_sidecars_absent(path: Path, stage: str) -> None:
    found = [str(item) for item in _sidecars(path) if item.exists()]
    if found:
        _fail(f"Tutorial Phase 4D2A sidecars present {stage}: {found}")


def _fingerprint(path: Path) -> dict[str, Any]:
    try:
        before = os.lstat(path)
    except OSError as exc:
        _fail(f"cannot fingerprint Tutorial evidence database: {exc}")
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        _fail(f"Tutorial evidence database must be a regular non-symlink file: {path}")
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        after = os.lstat(path)
    except OSError as exc:
        _fail(f"cannot fingerprint Tutorial evidence database: {exc}")
    if stat.S_ISLNK(after.st_mode) or not stat.S_ISREG(after.st_mode):
        _fail(f"Tutorial evidence database changed to a non-regular path: {path}")
    first = (before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns)
    second = (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns)
    if first != second:
        _fail("Tutorial database changed while fingerprinting")
    return {
        "sha256": digest.hexdigest(),
        "size_bytes": after.st_size,
        "mtime_ns": after.st_mtime_ns,
    }


def _schema_versions(conn: sqlite3.Connection) -> list[int]:
    try:
        return [int(row[0]) for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")]
    except sqlite3.Error as exc:
        _fail(f"Tutorial schema ledger is unreadable: {exc}")


def _strict_json(value: Any, label: str) -> Any:
    def reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                _fail(f"duplicate JSON key in {label}: {key}")
            result[key] = item
        return result

    try:
        return json.loads(str(value), object_pairs_hook=reject_pairs)
    except (TypeError, json.JSONDecodeError) as exc:
        _fail(f"{label} is not strict JSON: {exc}")


def _strict_report(path: str | Path) -> dict[str, Any]:
    report_path = Path(path).resolve()
    try:
        value = strict_json_file(report_path, "Tutorial Phase 4D2A build report")
    except TutorialPhase4D2AContractError as exc:
        _fail(str(exc))
    if not isinstance(value, dict):
        _fail("Tutorial Phase 4D2A build report must be an object")
    return value


def _expected_scope(manifest: TutorialPhase4D2AManifest) -> str:
    return canonical_json({
        "family": RUN_FAMILY,
        "manifest_id": manifest.manifest_id,
        "selected_paths": [item.relative_path for item in manifest.files],
        "candidate_scan_scope": "full_TutorialDetail_and_PushTips_native_locator_inventory",
    })


def _expected_config(manifest: TutorialPhase4D2AManifest) -> str:
    return canonical_json({
        "manifest_raw_sha256": manifest.raw_sha256,
        "versions": manifest.versions,
        "logical_identity_created": False,
        "mcp_capability_added": False,
        "full_core_tutorial_scan": False,
    })


def _accepted_snapshot_metadata(value: Any) -> bool:
    # Existing Character/static runs may own the same parser-independent
    # snapshot.  Verification accepts that sealed metadata without requiring a
    # family-specific rewrite.
    return value in {
        canonical_json({"sealed_source_manifest": True}),
        canonical_json({"sealed_source_manifest": True, "phase4d2a_canary": True}),
    }


def _verify_no_logical_tutorial_objects(conn: sqlite3.Connection) -> None:
    allowed = {name.casefold() for name in (*TUTORIAL_TABLES, *TUTORIAL_INDEXES, *TUTORIAL_IMMUTABLE_TRIGGERS)}
    forbidden_exact = {
        "tutorial", "tutorials", "logical_tutorial", "tutorial_identity",
        "tutorial_image", "tutorial_images", "logical_image", "image", "images",
        "push_tips", "logical_push_tips",
    }
    for object_type, name in conn.execute(
        "SELECT type,name FROM sqlite_master WHERE type IN ('table','view','index','trigger')"
    ):
        lowered = str(name).casefold()
        if lowered.startswith("sqlite_") or lowered in allowed:
            continue
        if lowered in forbidden_exact or "tutorial" in lowered:
            _fail(f"Tutorial logical/derived object pollution: {object_type} {name}")


def _verify_provenance(
    conn: sqlite3.Connection,
    manifest: TutorialPhase4D2AManifest,
    source_root: Path,
    derived: Mapping[str, Any],
    identity: Mapping[str, str],
) -> None:
    source = manifest.source
    row = conn.execute(
        "SELECT kind,url,license_note FROM source_catalog WHERE id=?",
        (EXPECTED_SOURCE_ID,),
    ).fetchone()
    if row is None or tuple(row) != ("primary", source["upstream"], None):
        _fail("Tutorial source_catalog mismatch")

    row = conn.execute(
        'SELECT source_id,release_label,build,"commit",tree,observed_at,status,metadata_json FROM source_snapshot WHERE id=?',
        (identity["snapshot_id"],),
    ).fetchone()
    snapshot_identity = (
        source["source_id"], source["release_label"], source["build"], source["commit"],
        source["tree"], source["observed_at"], "active",
    )
    if row is None or tuple(row)[:7] != snapshot_identity or not _accepted_snapshot_metadata(tuple(row)[7]):
        _fail("Tutorial source_snapshot mismatch")

    row = conn.execute(
        "SELECT parser_version,mapping_version,fingerprint,config_checksum,created_at FROM parser_build WHERE id=?",
        (identity["parser_build_id"],),
    ).fetchone()
    parser = (
        EXPECTED_VERSIONS["parser"], EXPECTED_VERSIONS["projection"],
        "tutorial-phase4d2a-sealed-core-canary", identity["config_checksum"], source["observed_at"],
    )
    if row is None or tuple(row) != parser:
        _fail("Tutorial parser_build mismatch")

    row = conn.execute(
        "SELECT snapshot_id,parser_build_id,scope_json,config_json,status,created_at FROM ingest_run WHERE id=?",
        (identity["ingest_run_id"],),
    ).fetchone()
    run = (
        identity["snapshot_id"], identity["parser_build_id"], _expected_scope(manifest),
        _expected_config(manifest), "complete", source["observed_at"],
    )
    if row is None or tuple(row) != run:
        _fail("Tutorial ingest_run binding/status mismatch")

    family_runs: list[str] = []
    for run_id, scope_json in conn.execute("SELECT id,scope_json FROM ingest_run ORDER BY id"):
        scope = _strict_json(scope_json, f"ingest_run {run_id} scope_json")
        if isinstance(scope, dict) and scope.get("family") == RUN_FAMILY:
            family_runs.append(str(run_id))
    if family_runs != [identity["ingest_run_id"]]:
        _fail(f"Tutorial same-family run set mismatch: {family_runs}")

    expected_root = str(source_root.resolve())
    file_ids: dict[str, str] = {}
    for item in manifest.files:
        file_id = digest_id("sf", identity["snapshot_id"], item.relative_path)
        file_ids[item.relative_path] = file_id
        row = conn.execute(
            "SELECT snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path FROM source_file WHERE id=?",
            (file_id,),
        ).fetchone()
        wanted = (
            identity["snapshot_id"], item.relative_path, item.blob_oid, item.sha256,
            item.size_bytes, item.format, expected_root,
        )
        actual = None if row is None else tuple(row)
        oid_ok = actual is not None and (actual[2] == item.blob_oid or (item.relative_path.startswith("TextMap/") and actual[2] is None))
        if actual is None or actual[:2] != wanted[:2] or not oid_ok or actual[3:] != wanted[3:]:
            _fail(f"Tutorial source_file mismatch: {item.relative_path}")

    for item in derived["source_records"]:
        file_id = file_ids[item["relative_path"]]
        record_id = digest_id("sr", file_id, item["source_pointer"])
        row = conn.execute(
            "SELECT source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json FROM source_record WHERE id=?",
            (record_id,),
        ).fetchone()
        wanted = (
            file_id, item["source_pointer"], item["record_kind"], item["native_id"],
            item["raw_checksum"], canonical_json(item["raw_value"]),
        )
        if row is None or tuple(row) != wanted:
            _fail(f"Tutorial source_record mismatch: {item['relative_path']}#{item['source_pointer']}")


def _row_ids(
    manifest: TutorialPhase4D2AManifest,
    derived: Mapping[str, Any],
    identity: Mapping[str, str],
) -> dict[str, dict[Any, str]]:
    rows = {
        (item["table"], item["row_index"]): occurrence_id(
            "row", identity, item["table"], item["relative_path"], item["pointer"]
        )
        for item in derived["rows"]
    }
    raw_fields = {
        (item["source_table"], item["source_row_index"], item["source_pointer"]): occurrence_id(
            "raw-field", identity, item["source_table"], item["source_row_index"], item["source_pointer"]
        )
        for item in derived["raw_fields"]
    }
    detail = {
        item["source_pointer"]: occurrence_id(
            "detail-relation", identity, item["source_table"], item["source_row_index"], item["source_pointer"]
        )
        for item in derived["detail_relations"]
    }
    push = {
        item["source_pointer"]: occurrence_id(
            "push-tips", identity, item["source_table"], item["source_row_index"], item["source_pointer"]
        )
        for item in derived["push_relations"]
    }
    owners = {
        item["owner_key"]: occurrence_id("text-owner", identity, item["owner_key"])
        for item in derived["text_owners"]
    }
    resolutions = {
        (item["owner_key"], item["locale"]): occurrence_id(
            "text-resolution", identity, item["owner_key"], item["locale"]
        )
        for item in derived["text_resolutions"]
    }
    images = {
        item["source_pointer"]: occurrence_id(
            "image-reference", identity, item["source_table"], item["source_row_index"], item["source_pointer"]
        )
        for item in derived["image_references"]
    }
    return {
        "rows": rows, "raw_fields": raw_fields, "detail": detail, "push": push,
        "owners": owners, "resolutions": resolutions, "images": images,
    }


def _selected_row_id(ids: Mapping[str, Mapping[Any, str]], key: str | None) -> str | None:
    if key is None:
        return None
    try:
        table, raw_index = key.split(":", 1)
        return ids["rows"][(table, int(raw_index))]
    except (AttributeError, ValueError, KeyError) as exc:
        raise TutorialPhase4D2AVerificationError(f"invalid or unselected Tutorial target row: {key!r}") from exc


def _expected_sql_rows(
    manifest: TutorialPhase4D2AManifest,
    derived: Mapping[str, Any],
    identity: Mapping[str, str],
) -> dict[str, tuple[tuple[str, ...], list[tuple[Any, ...]]]]:
    ids = _row_ids(manifest, derived, identity)
    file_ids = {
        item.relative_path: digest_id("sf", identity["snapshot_id"], item.relative_path)
        for item in manifest.files
    }
    record_ids = {
        (item["relative_path"], item["source_pointer"]): digest_id(
            "sr", file_ids[item["relative_path"]], item["source_pointer"]
        )
        for item in derived["source_records"]
    }
    row_records = {
        (item["table"], item["row_index"]): record_ids[(item["relative_path"], item["pointer"])]
        for item in derived["rows"]
    }
    run_id = identity["ingest_run_id"]
    snap = identity["snapshot_id"]
    parser = identity["parser_build_id"]
    rows: dict[str, tuple[tuple[str, ...], list[tuple[Any, ...]]]] = {}

    rows["tutorial_row_occurrence"] = (
        ("id", "ingest_run_id", "snapshot_id", "parser_build_id", "source_file_id", "source_record_id", "source_pointer", "table_name", "family_role", "row_index", "native_locator_field", "native_locator_value", "raw_checksum", "metadata_json"),
        [(
            ids["rows"][(item["table"], item["row_index"])], run_id, snap, parser,
            file_ids[item["relative_path"]], row_records[(item["table"], item["row_index"])],
            item["pointer"], item["table"], item["family_role"], item["row_index"],
            item["native_locator_field"], item["native_locator_value"], item["raw_checksum"],
            canonical_json({"logical_identity_created": False, "raw_evidence_identity": True}),
        ) for item in derived["rows"]],
    )

    rows["tutorial_raw_field_occurrence"] = (
        ("id", "ingest_run_id", "snapshot_id", "parser_build_id", "row_occurrence_id", "source_record_id", "source_pointer", "field_name", "array_index", "field_kind", "raw_json", "metadata_json"),
        [(
            ids["raw_fields"][(item["source_table"], item["source_row_index"], item["source_pointer"])],
            run_id, snap, parser, ids["rows"][(item["source_table"], item["source_row_index"])],
            row_records[(item["source_table"], item["source_row_index"])], item["source_pointer"],
            item["field_name"], item["array_index"], item["field_kind"], canonical_json(item["raw_value"]),
            canonical_json({"identity_effect": "none"}),
        ) for item in derived["raw_fields"]],
    )

    rows["tutorial_detail_relation_occurrence"] = (
        ("id", "ingest_run_id", "snapshot_id", "parser_build_id", "source_row_occurrence_id", "source_record_id", "source_pointer", "field_name", "array_index", "raw_value_json", "candidate_key_json", "resolution_status", "target_row_occurrence_id", "candidate_scopes_json", "metadata_json"),
        [(
            ids["detail"][item["source_pointer"]], run_id, snap, parser,
            ids["rows"][(item["source_table"], item["source_row_index"])],
            row_records[(item["source_table"], item["source_row_index"])], item["source_pointer"],
            item["field_name"], item["array_index"],
            None if item["raw_value"] is None else canonical_json(item["raw_value"]),
            canonical_json(item["candidate_key"]), item["resolution_status"],
            _selected_row_id(ids, item["target_row_key"]), canonical_json(item["candidate_scopes"]),
            canonical_json({"raw_value_preserved": True, "logical_identity_created": False}),
        ) for item in derived["detail_relations"]],
    )

    rows["tutorial_push_tips_candidate_occurrence"] = (
        ("id", "ingest_run_id", "snapshot_id", "parser_build_id", "source_row_occurrence_id", "source_record_id", "source_pointer", "field_name", "raw_value_json", "candidate_key_json", "resolution_status", "target_row_occurrence_id", "candidate_scopes_json", "metadata_json"),
        [(
            ids["push"][item["source_pointer"]], run_id, snap, parser,
            ids["rows"][(item["source_table"], item["source_row_index"])],
            row_records[(item["source_table"], item["source_row_index"])], item["source_pointer"],
            item["field_name"],
            None if item["raw_value"] is None else canonical_json(item["raw_value"]),
            canonical_json(item["candidate_key"]), item["resolution_status"],
            _selected_row_id(ids, item["target_row_key"]), canonical_json(item["candidate_scopes"]),
            canonical_json({"lookup_only": True, "tutorial_merge": False}),
        ) for item in derived["push_relations"]],
    )

    rows["tutorial_text_owner_occurrence"] = (
        ("id", "ingest_run_id", "snapshot_id", "parser_build_id", "source_row_occurrence_id", "source_record_id", "source_pointer", "field_name", "field_role", "textmap_hash", "raw_value_json", "metadata_json"),
        [(
            ids["owners"][item["owner_key"]], run_id, snap, parser,
            ids["rows"][(item["table"], item["row_index"])],
            row_records[(item["table"], item["row_index"])], item["source_pointer"], item["field_name"],
            item["field_role"], str(item["textmap_hash"]), canonical_json(item["raw_value"]),
            canonical_json({"owner_identity_effect": "independent_owner_occurrence_no_hash_or_text_merge"}),
        ) for item in derived["text_owners"]],
    )

    owner_by_key = {item["owner_key"]: item for item in derived["text_owners"]}
    rows["tutorial_text_resolution_occurrence"] = (
        ("id", "ingest_run_id", "snapshot_id", "parser_build_id", "owner_occurrence_id", "source_row_occurrence_id", "source_record_id", "source_pointer", "locale", "textmap_hash", "resolution_status", "raw_text", "normalized_text", "raw_checksum", "normalized_checksum", "candidate_scopes_json", "metadata_json"),
        [(
            ids["resolutions"][(item["owner_key"], item["locale"])], run_id, snap, parser,
            ids["owners"][item["owner_key"]],
            ids["rows"][(item["owner_table"], item["owner_row_index"])],
            row_records[(item["owner_table"], item["owner_row_index"])],
            owner_by_key[item["owner_key"]]["source_pointer"], item["locale"], str(item["textmap_hash"]),
            item["resolution_status"], item["raw_text"], item["normalized_text"], item["raw_checksum"],
            item["normalized_checksum"], canonical_json(item["candidate_scopes"]),
            canonical_json({"missing_is_legal": True, "hash_identity_effect": "none"}),
        ) for item in derived["text_resolutions"]],
    )

    candidate_values: list[tuple[Any, ...]] = []
    for item in derived["text_candidates"]:
        candidate_id = occurrence_id(
            "text-candidate", identity, item["owner_key"], item["locale"],
            item["shard_kind"], item["source_pointer"],
        )
        candidate_values.append((
            candidate_id, run_id, snap, parser,
            ids["resolutions"][(item["owner_key"], item["locale"])],
            file_ids[item["source_relative_path"]],
            record_ids[(item["source_relative_path"], item["source_pointer"])],
            item["source_pointer"], item["shard_kind"], item["locale"], item["resolution_status"],
            item["raw_text"], item["normalized_text"], item["raw_checksum"], item["normalized_checksum"],
            canonical_json({"candidate_identity_effect": "none", "owner_key": item["owner_key"]}),
        ))
    rows["tutorial_text_candidate_occurrence"] = (
        ("id", "ingest_run_id", "snapshot_id", "parser_build_id", "resolution_occurrence_id", "source_file_id", "source_record_id", "source_pointer", "shard_kind", "locale", "resolution_status", "raw_text", "normalized_text", "raw_checksum", "normalized_checksum", "metadata_json"),
        candidate_values,
    )

    rows["tutorial_image_reference_occurrence"] = (
        ("id", "ingest_run_id", "snapshot_id", "parser_build_id", "source_row_occurrence_id", "source_record_id", "source_pointer", "field_name", "array_index", "raw_image_name", "reference_status", "metadata_json"),
        [(
            ids["images"][item["source_pointer"]], run_id, snap, parser,
            ids["rows"][(item["source_table"], item["source_row_index"])],
            row_records[(item["source_table"], item["source_row_index"])], item["source_pointer"],
            item["field_name"], item["array_index"],
            item["raw_image_name"] if isinstance(item["raw_image_name"], str) else None,
            item["reference_status"], canonical_json({"image_identity_created": False}),
        ) for item in derived["image_references"]],
    )
    return rows


def _canonical_rows(rows: list[tuple[Any, ...]]) -> str:
    return canonical_json(sorted((list(row) for row in rows), key=canonical_json))


def _verify_exact_occurrences(
    conn: sqlite3.Connection,
    manifest: TutorialPhase4D2AManifest,
    derived: Mapping[str, Any],
    identity: Mapping[str, str],
) -> dict[str, Any]:
    expected_rows = _expected_sql_rows(manifest, derived, identity)
    run_id = identity["ingest_run_id"]
    for table, (columns, wanted) in expected_rows.items():
        present_runs = {
            str(row[0]) for row in conn.execute(f"SELECT DISTINCT ingest_run_id FROM {table}")
        }
        if present_runs - {run_id}:
            _fail(f"Tutorial {table} run-scope pollution: {sorted(present_runs - {run_id})}")
        actual = [
            tuple(row) for row in conn.execute(
                f"SELECT {','.join(columns)} FROM {table} WHERE ingest_run_id=? ORDER BY id",
                (run_id,),
            )
        ]
        if len(actual) != len(wanted) or _canonical_rows(actual) != _canonical_rows(wanted):
            _fail(f"Tutorial exact physical occurrence mismatch: {table}")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        _fail("Tutorial foreign-key check failed")
    return {
        "counts": derived["counts"],
        "status_counts": derived["status_counts"],
        "occurrence_set_digests": derived["occurrence_set_digests"],
        "oracle": "independent_source_coordinate_sql_exact_v1",
    }


def verify_tutorial_phase4d2a_database_contract(
    conn: sqlite3.Connection,
    manifest: TutorialPhase4D2AManifest,
    source_root: str | Path,
    derived: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify Schema 11/provenance/occurrence tuples on an open read-only DB."""
    root = Path(source_root).resolve()
    if derived is None:
        try:
            derived = derive_tutorial_phase4d2a_source_contract(root, manifest)
        except TutorialPhase4D2AContractError as exc:
            _fail(str(exc))
    identity = expected_identity(manifest)
    if _schema_versions(conn) != list(range(1, 12)):
        _fail("Tutorial canary requires exact Schema 11 migration ledger 1..11")
    try:
        verify_tutorial_phase4d2a_schema_signature(conn)
    except TutorialPhase4D2ASchemaError as exc:
        _fail(f"Tutorial Schema 11 signature mismatch: {exc}")
    _verify_no_logical_tutorial_objects(conn)
    _verify_provenance(conn, manifest, root, derived, identity)
    return _verify_exact_occurrences(conn, manifest, derived, identity)


def _verify_report(
    report: dict[str, Any],
    manifest: TutorialPhase4D2AManifest,
    derived: Mapping[str, Any],
    identity: Mapping[str, str],
    database: Path,
    fingerprint: dict[str, Any],
) -> None:
    required = {
        "status", "db_path", "snapshot_id", "parser_build_id", "ingest_run_id", "config_checksum",
        "manifest", "database_fingerprint", "sqlite_storage", "counts", "status_counts",
        "selected_table_counts", "text_owner_field_counts", "occurrence_set_digests", "scope",
    }
    if set(report) != required:
        _fail(f"Tutorial build report keys mismatch: {sorted(report)}")
    if report["status"] != "complete" or report["db_path"] != str(database.resolve()):
        _fail("Tutorial build report status/path mismatch")
    if (
        report["snapshot_id"] != identity["snapshot_id"]
        or report["parser_build_id"] != identity["parser_build_id"]
        or report["ingest_run_id"] != identity["ingest_run_id"]
        or report["config_checksum"] != identity["config_checksum"]
    ):
        _fail("Tutorial build report run binding mismatch")
    if report["manifest"] != {"manifest_id": manifest.manifest_id, "raw_sha256": manifest.raw_sha256}:
        _fail("Tutorial build report manifest binding mismatch")
    if report["database_fingerprint"] != fingerprint:
        _fail("Tutorial build report fingerprint mismatch")
    if report["sqlite_storage"] != {"journal_mode": "delete", "sidecars_absent": True}:
        _fail("Tutorial build report SQLite storage binding mismatch")
    if report["counts"] != derived["counts"] or report["status_counts"] != derived["status_counts"]:
        _fail("Tutorial build report count/status contract mismatch")
    if report["selected_table_counts"] != derived["selected_table_counts"] or report["text_owner_field_counts"] != derived["text_owner_field_counts"]:
        _fail("Tutorial build report selected-table/text-owner contract mismatch")
    if report["occurrence_set_digests"] != derived["occurrence_set_digests"]:
        _fail("Tutorial build report occurrence digest mismatch")
    expected_scope = {
        "selected_paths": [item.relative_path for item in manifest.files],
        "full_core_tutorial_scan": False,
        "candidate_scan_scope": "full_TutorialDetail_and_PushTips_native_locator_inventory",
        "logical_identity_created": False,
        "logical_passage_created": False,
        "mcp_capability_added": False,
        "context_scope": "single_physical_tutorial_row_container_only",
    }
    if report["scope"] != expected_scope:
        _fail("Tutorial build report scope mismatch")


def verify_tutorial_phase4d2a_evidence_db(
    db_path: str | Path,
    build_report_path: str | Path,
    manifest_path: str | Path,
    source_root: str | Path,
) -> dict[str, Any]:
    """Read-only M1 release gate; source and SQL tuples are independently checked."""
    database = _require_regular_file(db_path, "initial validation")
    _assert_sidecars_absent(database, "before fingerprint")
    before = _fingerprint(database)
    try:
        manifest = load_tutorial_phase4d2a_manifest(manifest_path)
        derived = derive_tutorial_phase4d2a_source_contract(source_root, manifest)
    except (TutorialPhase4D2AManifestError, TutorialPhase4D2AContractError) as exc:
        _fail(str(exc))
    expected = manifest.expected
    for key in ("counts", "status_counts", "selected_table_counts", "text_owner_field_counts", "occurrence_set_digests"):
        if derived[key] != expected[key]:
            _fail(f"Tutorial {key} source contract mismatch")
    _assert_sidecars_absent(database, "before read-only open")
    try:
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA busy_timeout=0")
            journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).casefold()
            if journal_mode != "delete":
                _fail(f"Tutorial canary requires DELETE journal mode, got {journal_mode}")
            result = verify_tutorial_phase4d2a_database_contract(conn, manifest, source_root, derived)
    except sqlite3.Error as exc:
        _fail(str(exc))
    _assert_sidecars_absent(database, "after read-only close")
    after = _fingerprint(database)
    if before != after:
        _fail("Tutorial database changed during read-only verification")
    report = _strict_report(build_report_path)
    _verify_report(report, manifest, derived, expected_identity(manifest), database, before)
    return {
        **result,
        "database_fingerprint": after,
        "sidecars_absent": True,
        "journal_mode": "delete",
        "report": str(Path(build_report_path).resolve()),
        "release_contract": "phase4d2a-core-tutorial-canary-v1",
    }


# A shorter alias is useful to maintenance callers while retaining the fully
# qualified API used by release scripts.
verify_phase4d2a_tutorial_evidence_db = verify_tutorial_phase4d2a_evidence_db


__all__ = [
    "TutorialPhase4D2AVerificationError",
    "verify_phase4d2a_tutorial_evidence_db",
    "verify_tutorial_phase4d2a_database_contract",
    "verify_tutorial_phase4d2a_evidence_db",
    "_fingerprint",
    "_sidecars",
]
