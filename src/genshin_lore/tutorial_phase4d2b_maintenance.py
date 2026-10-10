"""Independent read-only release gate for the Phase 4D2B Tutorial boundary.

The verifier re-derives the pinned source contract and compares every M2
physical occurrence tuple directly.  It deliberately does not import the M2
builder; all database access is through a read-only URI.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from pathlib import Path
from typing import Any, Mapping

from .db import Database, MIGRATIONS_DIR
from .tutorial_phase4d2a_schema import (
    TUTORIAL_IMMUTABLE_TRIGGERS,
    TUTORIAL_INDEXES,
    TUTORIAL_TABLES,
    TUTORIAL_MIGRATION_0011_SQL_SHA256,
    TutorialPhase4D2ASchemaError,
    verify_tutorial_phase4d2a_schema_signature,
)
from .tutorial_phase4d2b_contract import (
    PARSER_FINGERPRINT,
    RUN_FAMILY,
    TutorialPhase4D2BContractError,
    derive_tutorial_phase4d2b_source_contract,
    expected_identity,
    occurrence_id,
    verify_phase4d1_audit_binding,
)
from .tutorial_phase4d2b_manifest import (
    EXPECTED_COMMIT,
    EXPECTED_COVERAGE,
    EXPECTED_PUSH_TIPS_CORPUS_COMPLETE,
    EXPECTED_SOURCE_ID,
    EXPECTED_VERSIONS,
    TutorialPhase4D2BManifest,
    TutorialPhase4D2BManifestError,
    load_tutorial_phase4d2b_manifest,
)
from .util import canonical_json, digest_id


class TutorialPhase4D2BVerificationError(RuntimeError):
    """The full-boundary Tutorial artifact is not an exact release bundle."""


def _fail(message: str) -> None:
    raise TutorialPhase4D2BVerificationError(message)


def _absolute_input_path(value: str | Path) -> Path:
    try:
        return Path(os.path.abspath(os.fspath(value)))
    except (TypeError, ValueError, OSError) as exc:
        _fail(f"Tutorial Phase 4D2B database path is invalid: {value!r}")
        raise AssertionError from exc


def _require_regular_file(value: str | Path, stage: str) -> Path:
    """Reject links/FIFOs/devices before any path read or SQLite open."""
    path = _absolute_input_path(value)
    try:
        info = os.lstat(path)
    except OSError as exc:
        _fail(f"Tutorial Phase 4D2B database is not readable at {stage}: {path}: {exc}")
        raise AssertionError from exc
    if stat.S_ISLNK(info.st_mode):
        _fail(f"Tutorial Phase 4D2B database symlink is forbidden at {stage}: {path}")
    if not stat.S_ISREG(info.st_mode):
        _fail(f"Tutorial Phase 4D2B database must be a regular file at {stage}: {path}")
    return path


def _sidecars(path: Path) -> tuple[Path, Path, Path]:
    return tuple(Path(f"{path}{suffix}") for suffix in ("-journal", "-wal", "-shm"))


def _assert_sidecars_absent(path: Path, stage: str) -> None:
    present = [str(item) for item in _sidecars(path) if item.exists()]
    if present:
        _fail(f"Tutorial Phase 4D2B SQLite sidecars present {stage}: {present}")


def _fingerprint(path: Path) -> dict[str, Any]:
    """CAS fingerprint a regular file while detecting replacement/mutation."""
    try:
        before = os.lstat(path)
    except OSError as exc:
        _fail(f"cannot fingerprint Tutorial Phase 4D2B database: {exc}")
        raise AssertionError from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        _fail(f"Tutorial Phase 4D2B database must be a regular non-symlink file: {path}")
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        after = os.lstat(path)
    except OSError as exc:
        _fail(f"cannot fingerprint Tutorial Phase 4D2B database: {exc}")
        raise AssertionError from exc
    if stat.S_ISLNK(after.st_mode) or not stat.S_ISREG(after.st_mode):
        _fail(f"Tutorial Phase 4D2B database changed to a non-regular path: {path}")
    first = (before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns)
    second = (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns)
    if first != second:
        _fail("Tutorial Phase 4D2B database changed while fingerprinting")
    return {"sha256": digest.hexdigest(), "size_bytes": after.st_size, "mtime_ns": after.st_mtime_ns}



def _row_ids(
    manifest: TutorialPhase4D2BManifest,
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
        raise TutorialPhase4D2BVerificationError(f"invalid or unselected Tutorial target row: {key!r}") from exc


def _expected_sql_rows(
    manifest: TutorialPhase4D2BManifest,
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
            canonical_json({"identity_effect": "none", **({"missing_field": True} if item.get("missing_field") is True else {})}),
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


def _schema_versions(conn: sqlite3.Connection) -> list[int]:
    try:
        return [int(row[0]) for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")]
    except sqlite3.Error as exc:
        _fail(f"Tutorial Phase 4D2B schema ledger is unreadable: {exc}")
        raise AssertionError from exc


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
        raise AssertionError from exc


def _strict_report(path: str | Path) -> dict[str, Any]:
    report_path = Path(path).resolve()
    try:
        raw = report_path.read_bytes()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=lambda pairs: _reject_report_pairs(pairs, report_path))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot parse Tutorial Phase 4D2B build report: {exc}")
        raise AssertionError from exc
    if not isinstance(value, dict):
        _fail("Tutorial Phase 4D2B build report must be an object")
    return value


def _reject_report_pairs(pairs: list[tuple[str, Any]], path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON key in Tutorial Phase 4D2B build report {path}: {key}")
        result[key] = value
    return result


def _expected_scope(manifest: TutorialPhase4D2BManifest, derived: Mapping[str, Any]) -> str:
    return canonical_json({
        "family": RUN_FAMILY,
        "manifest_id": manifest.manifest_id,
        "snapshot_id": manifest.source["snapshot_id"],
        "coverage": EXPECTED_COVERAGE,
        "corpus_complete": True,
        "completeness_claim": EXPECTED_COVERAGE,
        "push_tips_corpus_complete": EXPECTED_PUSH_TIPS_CORPUS_COMPLETE,
        "selected_paths": [item.relative_path for item in manifest.files],
        "closure_counts": derived["counts"],
        "occurrence_set_digests": derived["occurrence_set_digests"],
        "full_core_tutorial_scan": True,
        "logical_identity_created": False,
        "mcp_capability_added": False,
    })


def _expected_config(manifest: TutorialPhase4D2BManifest, derived: Mapping[str, Any]) -> str:
    return canonical_json({
        "manifest_raw_sha256": manifest.raw_sha256,
        "versions": manifest.versions,
        "source_commit": EXPECTED_COMMIT,
        "source_tree": manifest.source["tree"],
        "coverage": EXPECTED_COVERAGE,
        "corpus_complete": True,
        "completeness_claim": EXPECTED_COVERAGE,
        "push_tips_corpus_complete": EXPECTED_PUSH_TIPS_CORPUS_COMPLETE,
        "schema_contract": "schema11-migration0011-exact",
        "occurrence_set_digests": derived["occurrence_set_digests"],
        "logical_identity_created": False,
        "mcp_capability_added": False,
    })


def _verify_migration_script() -> None:
    paths = sorted(MIGRATIONS_DIR.glob("0011_phase4d2a_tutorial_canary_evidence.sql"))
    if len(paths) != 1:
        _fail(f"expected exactly one sealed Tutorial 0011 migration, found {len(paths)}")
    path = paths[0]
    try:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        statements = list(Database._sql_statements(raw.decode("utf-8-sig"), path))
        for statement in statements:
            Database._validate_migration_statement(statement, path)
    except (OSError, UnicodeError, RuntimeError) as exc:
        _fail(f"Tutorial 0011 migration validation failed: {exc}")
        raise AssertionError from exc
    if digest != TUTORIAL_MIGRATION_0011_SQL_SHA256:
        _fail("Tutorial 0011 migration SQL SHA-256 seal mismatch")
    if not statements:
        _fail("Tutorial 0011 migration has no SQL statements")


def _verify_no_logical_tutorial_objects(conn: sqlite3.Connection) -> None:
    allowed = {*(name.casefold() for name in TUTORIAL_TABLES), *(name.casefold() for name in TUTORIAL_INDEXES), *(name.casefold() for name in TUTORIAL_IMMUTABLE_TRIGGERS)}
    catalogs = ("sqlite_master", "sqlite_temp_master")
    for catalog in catalogs:
        try:
            rows = conn.execute(f"SELECT type,name,sql FROM {catalog} WHERE type IN ('table','view','index','trigger')")
        except sqlite3.Error:
            continue
        for object_type, name, sql in rows:
            lowered = str(name).casefold()
            if lowered.startswith("sqlite_") or lowered in allowed:
                continue
            if "tutorial" in lowered or lowered in {"image", "images", "push_tips", "logical_tutorial", "tutorial_identity"}:
                _fail(f"Tutorial logical/derived object pollution: {catalog} {object_type} {name}")


def _allowed_physical_run_ids(conn: sqlite3.Connection, m2_run_id: str) -> set[str]:
    allowed = {m2_run_id}
    for run_id, scope_json in conn.execute("SELECT id,scope_json FROM ingest_run ORDER BY id"):
        scope = _strict_json(scope_json, f"ingest_run {run_id} scope_json")
        if isinstance(scope, dict) and scope.get("family") == "tutorial_phase4d2a_core_canary":
            allowed.add(str(run_id))
        elif isinstance(scope, dict) and scope.get("family") == RUN_FAMILY and str(run_id) != m2_run_id:
            _fail(f"Tutorial Phase 4D2B duplicate full-boundary run: {run_id}")
    return allowed


def _verify_run_scope(conn: sqlite3.Connection, m2_run_id: str) -> None:
    allowed = _allowed_physical_run_ids(conn, m2_run_id)
    for table in sorted(TUTORIAL_TABLES):
        runs = {str(row[0]) for row in conn.execute(f"SELECT DISTINCT ingest_run_id FROM {table}")}
        unexpected = runs - allowed
        if unexpected:
            _fail(f"Tutorial Phase 4D2B {table} run-scope pollution: {sorted(unexpected)}")


def _verify_provenance(
    conn: sqlite3.Connection,
    manifest: TutorialPhase4D2BManifest,
    source_root: Path,
    derived: Mapping[str, Any],
    identity: Mapping[str, str],
) -> None:
    source = manifest.source
    row = conn.execute("SELECT kind,url,license_note FROM source_catalog WHERE id=?", (EXPECTED_SOURCE_ID,)).fetchone()
    if row is None or tuple(row) != ("primary", source["upstream"], None):
        _fail("Tutorial Phase 4D2B source_catalog mismatch")

    row = conn.execute(
        'SELECT source_id,release_label,build,"commit",tree,observed_at,status,metadata_json FROM source_snapshot WHERE id=?',
        (identity["snapshot_id"],),
    ).fetchone()
    expected_snapshot = (
        source["source_id"], source["release_label"], source["build"], source["commit"],
        source["tree"], source["observed_at"], "active",
    )
    accepted_metadata = {
        canonical_json({"sealed_source_manifest": True}),
        canonical_json({"sealed_source_manifest": True, "phase4d2a_canary": True}),
        canonical_json({"sealed_source_manifest": True, "phase4d2b_full_boundary": True}),
    }
    if row is None or tuple(row)[:7] != expected_snapshot or tuple(row)[7] not in accepted_metadata:
        _fail("Tutorial Phase 4D2B source_snapshot mismatch")

    row = conn.execute(
        "SELECT parser_version,mapping_version,fingerprint,config_checksum,created_at FROM parser_build WHERE id=?",
        (identity["parser_build_id"],),
    ).fetchone()
    expected_parser = (
        EXPECTED_VERSIONS["parser"], EXPECTED_VERSIONS["projection"], PARSER_FINGERPRINT,
        identity["config_checksum"], source["observed_at"],
    )
    if row is None or tuple(row) != expected_parser:
        _fail("Tutorial Phase 4D2B parser_build mismatch")

    row = conn.execute(
        "SELECT snapshot_id,parser_build_id,scope_json,config_json,status,created_at FROM ingest_run WHERE id=?",
        (identity["ingest_run_id"],),
    ).fetchone()
    expected_run = (
        identity["snapshot_id"], identity["parser_build_id"], _expected_scope(manifest, derived),
        _expected_config(manifest, derived), "complete", source["observed_at"],
    )
    if row is None or tuple(row) != expected_run:
        _fail("Tutorial Phase 4D2B ingest_run binding/status mismatch")

    family_runs: list[str] = []
    for run_id, scope_json in conn.execute("SELECT id,scope_json FROM ingest_run ORDER BY id"):
        scope = _strict_json(scope_json, f"ingest_run {run_id} scope_json")
        if isinstance(scope, dict) and scope.get("family") == RUN_FAMILY:
            family_runs.append(str(run_id))
    if family_runs != [identity["ingest_run_id"]]:
        _fail(f"Tutorial Phase 4D2B same-family run set mismatch: {family_runs}")

    expected_root = str(source_root.resolve())
    file_ids: dict[str, str] = {}
    for item in manifest.files:
        file_id = digest_id("sf", identity["snapshot_id"], item.relative_path)
        file_ids[item.relative_path] = file_id
        row = conn.execute(
            "SELECT snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path FROM source_file WHERE id=?",
            (file_id,),
        ).fetchone()
        actual = None if row is None else tuple(row)
        oid_ok = actual is not None and (
            actual[2] == item.blob_oid
            or (item.relative_path.startswith("TextMap/") and actual[2] is None)
        )
        wanted_prefix = (identity["snapshot_id"], item.relative_path)
        wanted_suffix = (item.sha256, item.size_bytes, item.format, expected_root)
        if actual is None or actual[:2] != wanted_prefix or not oid_ok or actual[3:] != wanted_suffix:
            _fail(f"Tutorial Phase 4D2B source_file mismatch: {item.relative_path}")

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
            _fail(f"Tutorial Phase 4D2B source_record mismatch: {item['relative_path']}#{item['source_pointer']}")


def _verify_exact_occurrences(
    conn: sqlite3.Connection,
    manifest: TutorialPhase4D2BManifest,
    derived: Mapping[str, Any],
    identity: Mapping[str, str],
) -> dict[str, Any]:
    expected_rows = _expected_sql_rows(manifest, derived, identity)
    allowed_runs = _allowed_physical_run_ids(conn, identity["ingest_run_id"])
    for table, (columns, wanted) in expected_rows.items():
        present_runs = {str(row[0]) for row in conn.execute(f"SELECT DISTINCT ingest_run_id FROM {table}")}
        unexpected = present_runs - allowed_runs
        if unexpected:
            _fail(f"Tutorial {table} run-scope pollution: {sorted(unexpected)}")
        actual = [
            tuple(row)
            for row in conn.execute(
                f"SELECT {','.join(columns)} FROM {table} WHERE ingest_run_id=? ORDER BY id",
                (identity["ingest_run_id"],),
            )
        ]
        if len(actual) != len(wanted) or _canonical_rows(actual) != _canonical_rows(wanted):
            _fail(f"Tutorial exact physical occurrence mismatch: {table}")
    return {
        "counts": derived["counts"],
        "status_counts": derived["status_counts"],
        "missing_fields": derived["missing_fields"],
        "selected_table_counts": derived["selected_table_counts"],
        "text_owner_field_counts": derived["text_owner_field_counts"],
        "image_reference_stats": derived["image_reference_stats"],
        "occurrence_set_digests": derived["occurrence_set_digests"],
        "oracle": "independent_source_coordinate_sql_exact_phase4d2b_v1",
    }


def verify_tutorial_phase4d2b_database_contract(
    conn: sqlite3.Connection,
    manifest: TutorialPhase4D2BManifest,
    source_root: str | Path,
    derived: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify exact Schema 11, provenance, and all M2 SQL tuples."""
    root = Path(source_root).resolve()
    if derived is None:
        try:
            derived = derive_tutorial_phase4d2b_source_contract(root, manifest)
        except TutorialPhase4D2BContractError as exc:
            _fail(str(exc))
    identity = expected_identity(manifest)
    if _schema_versions(conn) != list(range(1, 12)):
        _fail("Tutorial Phase 4D2B requires exact Schema 11 migration ledger 1..11")
    _verify_migration_script()
    try:
        verify_tutorial_phase4d2a_schema_signature(conn)
    except TutorialPhase4D2ASchemaError as exc:
        _fail(f"Tutorial Phase 4D2B Schema 11 signature mismatch: {exc}")
    _verify_no_logical_tutorial_objects(conn)
    _verify_provenance(conn, manifest, root, derived, identity)
    _verify_run_scope(conn, identity["ingest_run_id"])
    result = _verify_exact_occurrences(conn, manifest, derived, identity)
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        _fail("Tutorial Phase 4D2B foreign-key check failed")
    return result


def _verify_report(
    report: Mapping[str, Any],
    manifest: TutorialPhase4D2BManifest,
    derived: Mapping[str, Any],
    identity: Mapping[str, str],
    database: Path,
    fingerprint: Mapping[str, Any],
) -> None:
    required = {
        "status", "db_path", "snapshot_id", "parser_build_id", "ingest_run_id", "config_checksum",
        "manifest", "database_fingerprint", "sqlite_storage", "counts", "status_counts",
        "missing_fields", "selected_table_counts", "text_owner_field_counts", "image_reference_stats", "occurrence_set_digests", "scope",
    }
    if set(report) != required:
        _fail(f"Tutorial Phase 4D2B build report keys mismatch: {sorted(report)}")
    if report["status"] != "complete" or report["db_path"] != str(database.resolve()):
        _fail("Tutorial Phase 4D2B build report status/path mismatch")
    if (
        report["snapshot_id"] != identity["snapshot_id"]
        or report["parser_build_id"] != identity["parser_build_id"]
        or report["ingest_run_id"] != identity["ingest_run_id"]
        or report["config_checksum"] != identity["config_checksum"]
    ):
        _fail("Tutorial Phase 4D2B build report run binding mismatch")
    if report["manifest"] != {"manifest_id": manifest.manifest_id, "raw_sha256": manifest.raw_sha256}:
        _fail("Tutorial Phase 4D2B build report manifest binding mismatch")
    if report["database_fingerprint"] != dict(fingerprint):
        _fail("Tutorial Phase 4D2B build report fingerprint mismatch")
    if report["sqlite_storage"] != {"journal_mode": "delete", "sidecars_absent": True}:
        _fail("Tutorial Phase 4D2B build report SQLite storage binding mismatch")
    for key in ("counts", "status_counts", "missing_fields", "selected_table_counts", "text_owner_field_counts", "image_reference_stats", "occurrence_set_digests"):
        if report[key] != derived[key]:
            _fail(f"Tutorial Phase 4D2B build report {key} mismatch")
    expected_scope = {
        "coverage": EXPECTED_COVERAGE,
        "corpus_complete": True,
        "completeness_claim": EXPECTED_COVERAGE,
        "push_tips_corpus_complete": EXPECTED_PUSH_TIPS_CORPUS_COMPLETE,
        "selected_paths": [item.relative_path for item in manifest.files],
        "full_core_tutorial_scan": True,
        "logical_identity_created": False,
        "logical_passage_created": False,
        "mcp_capability_added": False,
        "context_scope": "full_core_tutorial_source_closure",
    }
    if report["scope"] != expected_scope:
        _fail("Tutorial Phase 4D2B build report scope mismatch")


def verify_tutorial_phase4d2b_evidence_db(
    db_path: str | Path,
    build_report_path: str | Path,
    manifest_path: str | Path,
    source_root: str | Path,
) -> dict[str, Any]:
    """Read-only M2 release gate; source and physical SQL are independent."""
    database = _require_regular_file(db_path, "initial validation")
    _assert_sidecars_absent(database, "before fingerprint")
    before = _fingerprint(database)
    try:
        manifest = load_tutorial_phase4d2b_manifest(manifest_path)
        verify_phase4d1_audit_binding(Path(__file__).resolve().parents[2], manifest)
        derived = derive_tutorial_phase4d2b_source_contract(source_root, manifest)
    except (TutorialPhase4D2BManifestError, TutorialPhase4D2BContractError) as exc:
        _fail(str(exc))
    for key in (
        "counts", "status_counts", "missing_fields", "selected_table_counts",
        "text_owner_field_counts", "image_reference_stats", "occurrence_set_digests",
    ):
        if derived[key] != manifest.expected[key]:
            _fail(f"Tutorial Phase 4D2B {key} source contract mismatch")
    _assert_sidecars_absent(database, "before read-only open")
    try:
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA busy_timeout=0")
            journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).casefold()
            if journal_mode != "delete":
                _fail(f"Tutorial Phase 4D2B requires DELETE journal mode, got {journal_mode}")
            result = verify_tutorial_phase4d2b_database_contract(conn, manifest, source_root, derived)
    except sqlite3.Error as exc:
        _fail(str(exc))
    _assert_sidecars_absent(database, "after read-only close")
    after = _fingerprint(database)
    if before != after:
        _fail("Tutorial Phase 4D2B database changed during read-only verification")
    report = _strict_report(build_report_path)
    _verify_report(report, manifest, derived, expected_identity(manifest), database, before)
    return {
        "status": "pass",
        **result,
        "database_fingerprint": after,
        "sidecars_absent": True,
        "journal_mode": "delete",
        "report": str(Path(build_report_path).resolve()),
        "release_contract": "phase4d2b-core-tutorial-full-boundary-v1",
    }


verify_phase4d2b_database_contract = verify_tutorial_phase4d2b_database_contract
verify_phase4d2b_evidence_db = verify_tutorial_phase4d2b_evidence_db
verify_phase4d2b_tutorial_evidence_db = verify_tutorial_phase4d2b_evidence_db


__all__ = [
    "TutorialPhase4D2BVerificationError",
    "verify_phase4d2b_database_contract",
    "verify_phase4d2b_evidence_db",
    "verify_phase4d2b_tutorial_evidence_db",
    "verify_tutorial_phase4d2b_database_contract",
    "verify_tutorial_phase4d2b_evidence_db",
    "_fingerprint",
    "_sidecars",
]
