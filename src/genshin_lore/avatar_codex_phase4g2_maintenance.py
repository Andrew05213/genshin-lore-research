"""Independent read-only release verifier for the Phase 4G2 canary.

The builder and this module deliberately have separate preservation and
business-tuple oracles.  A build report is only an index into the checks; it
is never trusted as the source of expected rows, IDs, or counts.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .avatar_codex_phase4g2_contract import (
    EXPECTED_MANIFEST_SHA256,
    EXPECTED_MIGRATION_APPLIED_AT,
    EXPECTED_OBSERVED_AT,
    FAMILY,
    FAMILY_CONTRACT_VERSION,
    RUN_FAMILY,
    derive_source_contract,
    expected_identity,
    load_manifest,
)
from .avatar_codex_phase4g2_release_oracle import exact_occurrence_tables
from .avatar_codex_phase4g2_schema import verify_schema_14
from .avatar_codex_phase4g2_paths import assert_path_matrix
from .quest_codex_phase4f2_schema import verify_schema_13
from .util import canonical_json, digest_id
from .world_archive_phase4e2_promotion import assert_sidecars_absent


class AvatarCodexPhase4G2VerificationError(RuntimeError):
    """The Schema14 Avatar Codex canary is not release-valid."""


def _fail(message: str) -> None:
    raise AvatarCodexPhase4G2VerificationError(message)


AVATAR_TABLES = frozenset(
    {
        "avatar_codex_row_occurrence",
        "avatar_codex_raw_field_occurrence",
        "avatar_codex_relationship_occurrence",
        "avatar_codex_relationship_candidate_occurrence",
        "avatar_codex_opaque_occurrence",
    }
)
ADDITIVE_TABLES = frozenset(
    {"schema_migration", "parser_build", "ingest_run", "source_file", "source_record"}
)

# This is the reviewed, exact Schema13 user-table inventory.  It is repeated
# here instead of importing a builder gate so the release oracle owns its
# baseline contract independently.
SCHEMA13_USER_TABLES = (
    "candidate_scope_proof", "character_audio_variant_occurrence",
    "character_raw_slot_occurrence", "character_relation_occurrence",
    "character_row_occurrence", "character_text_candidate_occurrence",
    "character_text_resolution_occurrence", "character_voice_container_occurrence",
    "compare_run", "comparison_absence_assertion", "comparison_change_label",
    "comparison_change_observation", "comparison_match_anchor",
    "comparison_match_candidate", "comparison_match_decision", "comparison_match_set",
    "comparison_scope_proof", "comparison_side", "document", "document_evidence",
    "document_relation", "document_version", "evidence_container", "fts_evidence",
    "fts_evidence_config", "fts_evidence_content", "fts_evidence_data",
    "fts_evidence_docsize", "fts_evidence_idx", "identity_group",
    "identity_group_container", "ingest_issue", "ingest_run", "localized_text",
    "logical_talk", "logical_talk_evidence", "matcher_build", "metadata_fact",
    "occurrence_edge", "parser_build", "passage", "passage_edge",
    "passage_occurrence", "passage_occurrence_text", "passage_text",
    "passage_version", "quest_codex_asset_reference_occurrence",
    "quest_codex_opaque_occurrence", "quest_codex_raw_field_occurrence",
    "quest_codex_relationship_candidate_occurrence", "quest_codex_relationship_occurrence",
    "quest_codex_row_occurrence", "quest_codex_text_candidate_occurrence",
    "quest_codex_text_owner_occurrence", "quest_codex_text_resolution_occurrence",
    "schema_migration", "source_catalog", "source_file", "source_record",
    "source_snapshot", "static_evidence_container", "static_relationship_occurrence",
    "static_segment_occurrence", "static_text_candidate_occurrence",
    "static_text_resolution_occurrence", "talk_evidence",
    "textmap_conflict", "tutorial_detail_relation_occurrence",
    "tutorial_image_reference_occurrence", "tutorial_push_tips_candidate_occurrence",
    "tutorial_raw_field_occurrence", "tutorial_row_occurrence",
    "tutorial_text_candidate_occurrence", "tutorial_text_owner_occurrence",
    "tutorial_text_resolution_occurrence", "world_archive_asset_reference_occurrence",
    "world_archive_opaque_occurrence", "world_archive_raw_field_occurrence",
    "world_archive_relationship_occurrence", "world_archive_row_occurrence",
    "world_archive_text_candidate_occurrence", "world_archive_text_owner_occurrence",
    "world_archive_text_resolution_occurrence",
)
PROTECTED_TABLES = tuple(item for item in SCHEMA13_USER_TABLES if item not in ADDITIVE_TABLES)
EXPECTED_BASELINE_SIZE = 1723596800
EXPECTED_BASELINE_SHA256 = "7ff8163b6007a41b0827af05fbcf996353617c52f354a131431da912712e58be"


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _fingerprint(path_value: str | Path) -> dict[str, Any]:
    path = Path(os.path.abspath(os.fspath(path_value)))
    assert_sidecars_absent(path, "during Avatar Codex verification fingerprint")
    try:
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            _fail("Avatar Codex artifact must be a regular non-symlink file")
        before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != before_identity:
                _fail("Avatar Codex artifact descriptor identity changed")
            digest = hashlib.sha256()
            while chunk := os.read(descriptor, 1024 * 1024):
                digest.update(chunk)
            descriptor_after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after = os.lstat(path)
    except OSError as exc:
        _fail(f"cannot fingerprint Avatar Codex artifact: {exc}")
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    descriptor_identity = (
        descriptor_after.st_dev, descriptor_after.st_ino,
        descriptor_after.st_size, descriptor_after.st_mtime_ns,
    )
    if before_identity != after_identity or before_identity != descriptor_identity:
        _fail("Avatar Codex artifact changed while fingerprinting")
    assert_sidecars_absent(path, "after Avatar Codex verification fingerprint")
    return {
        "sha256": digest.hexdigest(),
        "size_bytes": after.st_size,
        "mtime_ns": after.st_mtime_ns,
        "st_dev": after.st_dev,
        "st_ino": after.st_ino,
        "st_mode": stat.S_IMODE(after.st_mode),
    }


def _strict_json(raw: str | bytes, label: str) -> Any:
    def reject(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail(f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        _fail(f"non-standard JSON constant in {label}: {value}")

    try:
        return json.loads(raw, object_pairs_hook=reject, parse_constant=reject_constant)
    except AvatarCodexPhase4G2VerificationError:
        raise
    except (UnicodeDecodeError, TypeError, json.JSONDecodeError) as exc:
        _fail(f"invalid JSON in {label}: {exc}")


def _migration_0014(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT version,applied_at FROM schema_migration WHERE version=14").fetchone()
    expected = (14, EXPECTED_MIGRATION_APPLIED_AT)
    if row is None or tuple(row) != expected:
        _fail("migration 0014 ledger tuple mismatch")
    return {"version": 14, "applied_at": EXPECTED_MIGRATION_APPLIED_AT}


def _scope(manifest: Mapping[str, Any]) -> str:
    return canonical_json(
        {
            "family": RUN_FAMILY,
            "provider_family": FAMILY,
            "family_contract_version": FAMILY_CONTRACT_VERSION,
            "manifest_id": manifest["manifest_id"],
            "selected_rows": list(manifest["boundary"]["selected_rows"]),
            "selected_ids": list(manifest["boundary"]["selected_ids"]),
            "exact_ingest_run_required": True,
        }
    )


def _config(manifest: Mapping[str, Any]) -> str:
    return canonical_json(
        {
            "manifest_raw_sha256": EXPECTED_MANIFEST_SHA256,
            "versions": manifest["versions"],
            "logical_identity_created": False,
            "full_avatar_codex_scan": False,
            "corpus_complete": False,
            "mcp_provider_added": False,
        }
    )


def _user_tables(conn: sqlite3.Connection, schema: str) -> tuple[str, ...]:
    return tuple(
        str(row[0]) for row in conn.execute(
            f"SELECT name FROM {schema}.sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    )


def _table_columns(conn: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f"PRAGMA {schema}.table_info({_quote(table)})")]


def _assert_bidirectional_equal(conn: sqlite3.Connection, table: str) -> None:
    columns = _table_columns(conn, "main", table)
    if columns != _table_columns(conn, "baseline", table):
        _fail(f"Schema13 protected table shape changed: {table}")
    quoted = ",".join(_quote(column) for column in columns)
    for left, right in (("baseline", "main"), ("main", "baseline")):
        if conn.execute(
            f"SELECT 1 FROM (SELECT {quoted} FROM {left}.{_quote(table)} "
            f"EXCEPT SELECT {quoted} FROM {right}.{_quote(table)}) LIMIT 1"
        ).fetchone() is not None:
            _fail(f"Schema13 protected table rows changed: {table}")


def _assert_baseline_subset(conn: sqlite3.Connection, table: str) -> None:
    columns = _table_columns(conn, "main", table)
    if columns != _table_columns(conn, "baseline", table):
        _fail(f"Schema13 additive table shape changed: {table}")
    quoted = ",".join(_quote(column) for column in columns)
    if conn.execute(
        f"SELECT 1 FROM (SELECT {quoted} FROM baseline.{_quote(table)} "
        f"EXCEPT SELECT {quoted} FROM main.{_quote(table)}) LIMIT 1"
    ).fetchone() is not None:
        _fail(f"Schema13 additive table lost baseline rows: {table}")


def _extra_ids(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[0]) for row in conn.execute(
            f"SELECT id FROM main.{_quote(table)} EXCEPT SELECT id FROM baseline.{_quote(table)}"
        )
    }


def _assert_baseline_preservation(
    conn: sqlite3.Connection,
    baseline: Path,
    identity: Mapping[str, str],
    derived: Mapping[str, Any],
    migration: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        conn.execute("ATTACH DATABASE ? AS baseline", (baseline.as_uri() + "?mode=ro&immutable=1",))
    except sqlite3.Error as exc:
        _fail(f"cannot attach immutable Schema13 baseline: {exc}")
    try:
        baseline_tables = _user_tables(conn, "baseline")
        if baseline_tables != SCHEMA13_USER_TABLES:
            _fail("Schema13 baseline user-table inventory mismatch")
        final_tables = set(_user_tables(conn, "main"))
        if final_tables != set(SCHEMA13_USER_TABLES) | set(AVATAR_TABLES):
            _fail("Schema14 user-table inventory is not exact Schema13 plus five Avatar tables")
        for table in PROTECTED_TABLES:
            _assert_bidirectional_equal(conn, table)
        for table in ADDITIVE_TABLES:
            _assert_baseline_subset(conn, table)

        if [tuple(row) for row in conn.execute(
            "SELECT version,applied_at FROM main.schema_migration "
            "EXCEPT SELECT version,applied_at FROM baseline.schema_migration "
            "ORDER BY version,applied_at"
        )] != [(int(migration["version"]), str(migration["applied_at"]))]:
            _fail("unapproved schema_migration additions")

        expected_parser = {identity["parser_build_id"]}
        expected_run = {identity["ingest_run_id"]}
        expected_files = {
            digest_id("sf", identity["snapshot_id"], item["relative_path"])
            for item in derived["files"]
        }
        expected_records = {
            digest_id(
                "sr",
                digest_id("sf", identity["snapshot_id"], item["relative_path"]),
                item["source_pointer"],
            )
            for item in derived["source_records"]
        }
        if _extra_ids(conn, "parser_build") != expected_parser:
            _fail("unapproved parser_build additions")
        if _extra_ids(conn, "ingest_run") != expected_run:
            _fail("unapproved ingest_run additions")
        if _extra_ids(conn, "source_file") != expected_files:
            _fail("unapproved source_file additions")
        if _extra_ids(conn, "source_record") != expected_records:
            _fail("unapproved source_record additions")
        return {
            "baseline_user_tables": len(SCHEMA13_USER_TABLES),
            "protected_exact_tables": len(PROTECTED_TABLES),
            "additive_subset_tables": len(ADDITIVE_TABLES),
            "avatar_additive_tables": len(AVATAR_TABLES),
            "added_parser_build_rows": len(expected_parser),
            "added_ingest_run_rows": len(expected_run),
            "added_source_file_rows": len(expected_files),
            "added_source_record_rows": len(expected_records),
        }
    finally:
        conn.execute("DETACH DATABASE baseline")


def _family_run_ids(conn: sqlite3.Connection) -> list[str]:
    result: list[str] = []
    for run_id, scope_json in conn.execute("SELECT id,scope_json FROM ingest_run ORDER BY id"):
        scope = _strict_json(scope_json, f"ingest_run {run_id} scope_json")
        if isinstance(scope, dict) and (
            scope.get("provider_family") == FAMILY
            or str(scope.get("family", "")) == FAMILY
            or str(scope.get("family", "")).startswith(f"{FAMILY}_")
        ):
            result.append(str(run_id))
    return result


def _assert_no_avatar_object_pollution(conn: sqlite3.Connection) -> None:
    expected = set(AVATAR_TABLES)
    for object_type, name in conn.execute(
        "SELECT type,name FROM sqlite_master WHERE type IN ('table','view','index','trigger')"
    ):
        object_name = str(name)
        if object_name.startswith("avatar_codex_") and object_name not in expected and not object_name.startswith((
            "idx_avatar_codex_", "trg_avatar_codex_", "uq_avatar_codex_"
        )):
            _fail(f"Avatar Codex logical/derived object pollution: {object_type} {object_name}")


def assert_exact_provenance(
    conn: sqlite3.Connection,
    source_root: str | Path,
    manifest: Mapping[str, Any],
    derived: Mapping[str, Any],
    identity: Mapping[str, str],
) -> None:
    """Verify every shared/projection provenance tuple independently."""
    wanted_run = (
        identity["snapshot_id"], identity["parser_build_id"], _scope(manifest),
        _config(manifest), "complete", EXPECTED_OBSERVED_AT,
    )
    actual_run = conn.execute(
        "SELECT snapshot_id,parser_build_id,scope_json,config_json,status,created_at "
        "FROM ingest_run WHERE id=?", (identity["ingest_run_id"],)
    ).fetchone()
    if actual_run is None or tuple(actual_run) != wanted_run:
        _fail("Avatar Codex exact ingest_run provenance mismatch")
    if _family_run_ids(conn) != [identity["ingest_run_id"]]:
        _fail("Avatar Codex same-family run set mismatch")

    parser = (
        manifest["versions"]["parser"], manifest["versions"]["projection"],
        "avatar-codex-phase4g2-sealed-canary", identity["config_checksum"],
        EXPECTED_OBSERVED_AT,
    )
    actual_parser = conn.execute(
        "SELECT parser_version,mapping_version,fingerprint,config_checksum,created_at "
        "FROM parser_build WHERE id=?", (identity["parser_build_id"],)
    ).fetchone()
    if actual_parser is None or tuple(actual_parser) != parser:
        _fail("Avatar Codex exact parser_build provenance mismatch")

    source = manifest["source"]
    catalog = conn.execute(
        "SELECT kind,url,license_note FROM source_catalog WHERE id=?", (source["source_id"],)
    ).fetchone()
    if catalog is None or tuple(catalog) != ("primary", source["upstream"], None):
        _fail("Avatar Codex source_catalog provenance mismatch")
    snapshot = conn.execute(
        'SELECT source_id,release_label,build,"commit",tree,observed_at,status,metadata_json '
        "FROM source_snapshot WHERE id=?", (identity["snapshot_id"],)
    ).fetchone()
    wanted_snapshot = (
        source["source_id"], source["release_label"], source["build"], source["commit"],
        source["tree"], source["observed_at"], "active",
    )
    if snapshot is None or tuple(snapshot)[:7] != wanted_snapshot:
        _fail("Avatar Codex parser-independent source_snapshot mismatch")
    metadata = _strict_json(str(snapshot[7]), "Avatar Codex source_snapshot metadata_json")
    if not isinstance(metadata, dict):
        _fail("Avatar Codex source_snapshot metadata must be an object")

    source_root = Path(source_root).resolve()
    file_ids: set[str] = set()
    for item in derived["files"]:
        file_id = digest_id("sf", identity["snapshot_id"], item["relative_path"])
        file_ids.add(file_id)
        wanted_file = (
            identity["snapshot_id"], item["relative_path"], item["blob_oid"], item["sha256"],
            item["size_bytes"], item["format"], str(source_root),
        )
        actual_file = conn.execute(
            "SELECT snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path "
            "FROM source_file WHERE id=?", (file_id,)
        ).fetchone()
        if actual_file is None or tuple(actual_file) != wanted_file:
            _fail(f"Avatar Codex source_file provenance mismatch: {item['relative_path']}")
    if len(file_ids) != len(derived["files"]):
        _fail("Avatar Codex source_file identity collision")

    record_ids: set[str] = set()
    for item in derived["source_records"]:
        file_id = digest_id("sf", identity["snapshot_id"], item["relative_path"])
        record_id = digest_id("sr", file_id, item["source_pointer"])
        record_ids.add(record_id)
        wanted_record = (
            file_id, item["source_pointer"], item["record_kind"], item["native_id"],
            item["raw_checksum"], canonical_json(item["raw_value"]),
        )
        actual_record = conn.execute(
            "SELECT source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json "
            "FROM source_record WHERE id=?", (record_id,)
        ).fetchone()
        if actual_record is None or tuple(actual_record) != wanted_record:
            _fail("Avatar Codex source_record provenance mismatch")
    if len(record_ids) != len(derived["source_records"]):
        _fail("Avatar Codex source_record identity collision")


def _assert_statuses(conn: sqlite3.Connection, derived: Mapping[str, Any], run_id: str) -> None:
    checks = (
        ("avatar_codex_row_occurrence", "evidence_kind", derived["status_counts"]["row_kinds"]),
        ("avatar_codex_raw_field_occurrence", "field_presence", derived["status_counts"]["field_presence"]),
        ("avatar_codex_relationship_occurrence", "resolution_status", derived["status_counts"]["relationships"]),
        ("avatar_codex_opaque_occurrence", "resolution_status", derived["status_counts"]["opaque"]),
    )
    for table, column, expected in checks:
        actual = dict(sorted(Counter(
            str(row[0]) for row in conn.execute(
                f"SELECT {_quote(column)} FROM {_quote(table)} WHERE ingest_run_id=?", (run_id,)
            )
        ).items()))
        if actual != expected:
            _fail(f"Avatar Codex status matrix mismatch: {table}")


def verify(
    db_path: str | Path,
    baseline_path: str | Path,
    report_path: str | Path,
    source_root: str | Path,
    repo_root: str | Path,
    manifest_path: str | Path,
) -> dict[str, Any]:
    """Verify a sealed artifact without importing or invoking the builder."""
    paths = assert_path_matrix(
        {
            "database": db_path, "baseline": baseline_path, "report": report_path,
            "source_root": source_root, "repo_root": repo_root, "manifest": manifest_path,
        },
        protected_database_names=("database", "baseline"),
        input_files=("database", "baseline", "report", "manifest"),
        input_directories=("source_root", "repo_root"),
    )
    path, baseline, report_path = paths["database"], paths["baseline"], paths["report"]
    before = _fingerprint(path)
    baseline_before = _fingerprint(baseline)
    if (baseline_before["size_bytes"], baseline_before["sha256"]) != (
        EXPECTED_BASELINE_SIZE, EXPECTED_BASELINE_SHA256
    ):
        _fail("accepted Schema13 baseline byte seal mismatch")
    manifest = load_manifest(paths["manifest"])
    derived = derive_source_contract(paths["source_root"], paths["repo_root"], manifest)
    identity = expected_identity(manifest)
    try:
        with sqlite3.connect(baseline.as_uri() + "?mode=ro&immutable=1", uri=True) as baseline_conn:
            baseline_conn.execute("PRAGMA query_only=ON")
            verify_schema_13(baseline_conn)
            if _user_tables(baseline_conn, "main") != SCHEMA13_USER_TABLES:
                _fail("accepted Schema13 baseline user-table inventory mismatch")
    except sqlite3.Error as exc:
        _fail(f"invalid accepted Schema13 baseline: {exc}")

    try:
        report = _strict_json(report_path.read_bytes(), "Avatar Codex build report")
    except OSError as exc:
        _fail(f"cannot read Avatar Codex build report: {exc}")
    expected_report = {
        "schema_migration_0014": {"version": 14, "applied_at": EXPECTED_MIGRATION_APPLIED_AT},
        "status": "complete", "schema_version": 14, "family": FAMILY,
        "family_contract_version": FAMILY_CONTRACT_VERSION,
        "snapshot_id": identity["snapshot_id"], "parser_build_id": identity["parser_build_id"],
        "ingest_run_id": identity["ingest_run_id"], "config_checksum": identity["config_checksum"],
        "counts": derived["counts"], "status_counts": derived["status_counts"],
        "occurrence_set_digests": derived["occurrence_set_digests"],
        "coverage": "bounded_canary", "corpus_complete": False, "evidence_only": True,
        "mcp_provider_added": False, "db_path": str(path.resolve()),
        "database_sha256": before["sha256"], "database_size_bytes": before["size_bytes"],
    }
    if report != expected_report:
        _fail("Avatar Codex build report mismatch")

    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
            conn.execute("PRAGMA query_only=ON")
            verify_schema_14(conn)
            migration = _migration_0014(conn)
            if migration != report["schema_migration_0014"]:
                _fail("Avatar Codex report/migration ledger mismatch")
            _assert_no_avatar_object_pollution(conn)
            assert_exact_provenance(conn, paths["source_root"], manifest, derived, identity)
            tables = exact_occurrence_tables(derived, identity)
            for table, (columns, wanted_rows) in tables.items():
                actual = [
                    tuple(row) for row in conn.execute(
                        f"SELECT {','.join(_quote(column) for column in columns)} "
                        f"FROM {_quote(table)} ORDER BY id"
                    )
                ]
                if actual != sorted(wanted_rows, key=lambda value: str(value[0])):
                    _fail(f"Avatar Codex exact business tuple mismatch: {table}")
            _assert_statuses(conn, derived, identity["ingest_run_id"])
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                _fail("Avatar Codex foreign-key check failed")
            preservation = _assert_baseline_preservation(
                conn, baseline, identity, derived, migration
            )
    except AvatarCodexPhase4G2VerificationError:
        raise
    except sqlite3.Error as exc:
        _fail(f"Avatar Codex read-only verification failed: {exc}")
    after = _fingerprint(path)
    baseline_after = _fingerprint(baseline)
    if after != before or baseline_after != baseline_before:
        _fail("Avatar Codex verifier modified database or baseline")
    return {
        "status": "pass", "oracle": "frozen_phase4g2_source_coordinate_and_exact_sql_v1",
        "schema_version": 14, "family": FAMILY,
        "family_contract_version": FAMILY_CONTRACT_VERSION, "coverage": "bounded_canary",
        "corpus_complete": False, "evidence_only": True,
        "ingest_run_id": identity["ingest_run_id"], "counts": derived["counts"],
        "status_counts": derived["status_counts"], "baseline_preservation": preservation,
        "database_fingerprint": after, "baseline_fingerprint": baseline_after,
    }


__all__ = [
    "AvatarCodexPhase4G2VerificationError", "SCHEMA13_USER_TABLES", "PROTECTED_TABLES",
    "verify", "assert_exact_provenance",
]
