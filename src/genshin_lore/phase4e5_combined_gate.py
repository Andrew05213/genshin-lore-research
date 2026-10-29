"""Atomic Schema 12 combined build and baseline-wide preservation gate."""

from __future__ import annotations

from .platform_io import lock_exclusive, unlock
import hashlib
import os
import shutil
import sqlite3
import stat
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

from .phase4d5_combined_gate import (
    EXPECTED_RUN_IDS,
    FINAL_ARTIFACT_SHA256 as SCHEMA11_SHA256,
    FINAL_ARTIFACT_SIZE_BYTES as SCHEMA11_SIZE_BYTES,
    verify_phase4d5_combined_database,
)
from .util import digest_id
from .world_archive_phase4e2_promotion import (
    assert_sidecars_absent,
    sync_directory,
    write_report_atomically,
)
from .world_archive_phase4e2_stable_lock import promotion_lock_path
from .world_archive_phase4e3_contract import (
    EXPECTED_OBSERVED_AT,
    PRIMARY_PATHS,
    expected_identity,
    load_manifest,
)
from .world_archive_phase4e3_evidence import ingest
from .world_archive_phase4e3_maintenance import verify as verify_world_archive
from .world_archive_phase4e3_receipt import (
    RECEIPT_CONTRACT,
    RECEIPT_VERSION,
    release_receipt,
    write_release_receipt_atomically,
)


class Phase4E5CombinedGateError(RuntimeError):
    """The input, output, or combined database is outside the M5 contract."""


ADDITIVE_TABLES = frozenset({
    "schema_migration", "parser_build", "ingest_run", "source_file", "source_record",
})
WORLD_ARCHIVE_TABLES = frozenset({
    "world_archive_row_occurrence", "world_archive_raw_field_occurrence",
    "world_archive_relationship_occurrence", "world_archive_opaque_occurrence",
    "world_archive_text_owner_occurrence", "world_archive_text_resolution_occurrence",
    "world_archive_text_candidate_occurrence", "world_archive_asset_reference_occurrence",
})

# Exact reviewed Schema 11 user-table inventory.  The verifier also derives the
# actual baseline inventory and requires equality with this tracked contract.
SCHEMA11_USER_TABLES = (
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
    "passage_version", "schema_migration", "source_catalog", "source_file",
    "source_record", "source_snapshot", "static_evidence_container",
    "static_relationship_occurrence", "static_segment_occurrence",
    "static_text_candidate_occurrence", "static_text_resolution_occurrence",
    "talk_evidence", "textmap_conflict", "tutorial_detail_relation_occurrence",
    "tutorial_image_reference_occurrence", "tutorial_push_tips_candidate_occurrence",
    "tutorial_raw_field_occurrence", "tutorial_row_occurrence",
    "tutorial_text_candidate_occurrence", "tutorial_text_owner_occurrence",
    "tutorial_text_resolution_occurrence",
)
PROTECTED_TABLES = tuple(item for item in SCHEMA11_USER_TABLES if item not in ADDITIVE_TABLES)

_BEFORE_FINAL_LOCK_TEST_HOOK: Callable[[Path, Path], None] | None = None
_UNDER_FINAL_LOCK_TEST_HOOK: Callable[[Path, Path], None] | None = None


def _absolute(value: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(value)))


def _derived_danger_paths(path: Path) -> tuple[Path, ...]:
    return (
        promotion_lock_path(path),
        Path(f"{path}-journal"), Path(f"{path}-wal"), Path(f"{path}-shm"),
    )


def _aliases(left: Path, right: Path) -> bool:
    try:
        if left.resolve(strict=False) == right.resolve(strict=False):
            return True
    except (OSError, RuntimeError):
        pass
    try:
        return os.path.samefile(left, right)
    except (FileNotFoundError, OSError):
        return False


def _assert_no_symlink_components(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise Phase4E5CombinedGateError(f"{label} contains a symlink component")


def assert_path_matrix(
    paths: Mapping[str, str | Path],
    *,
    protected_database_names: tuple[str, ...] = ("baseline", "database"),
    input_files: tuple[str, ...] = (),
    input_directories: tuple[str, ...] = (),
    absent_outputs: tuple[str, ...] = (),
) -> dict[str, Path]:
    """Reject every lexical/resolved/samefile alias before any mutation."""
    normalized = {name: _absolute(value) for name, value in paths.items()}
    for name, path in normalized.items():
        _assert_no_symlink_components(path, name)
    items = list(normalized.items())
    for index, (left_name, left) in enumerate(items):
        for right_name, right in items[index + 1:]:
            if _aliases(left, right):
                raise Phase4E5CombinedGateError(
                    f"path alias between {left_name} and {right_name}"
                )
    hazards: list[tuple[str, Path]] = []
    for database_name in protected_database_names:
        database = normalized[database_name]
        hazards.extend(
            (f"{database_name}:{candidate.name}", candidate)
            for candidate in _derived_danger_paths(database)
        )
    for name, path in items:
        for hazard_name, hazard in hazards:
            if _aliases(path, hazard):
                raise Phase4E5CombinedGateError(
                    f"{name} aliases protected derived path {hazard_name}"
                )
    for name in input_files:
        path = normalized[name]
        try:
            info = os.lstat(path)
        except FileNotFoundError as exc:
            raise Phase4E5CombinedGateError(f"required input file is absent: {name}") from exc
        if not stat.S_ISREG(info.st_mode):
            raise Phase4E5CombinedGateError(f"required input is not a regular file: {name}")
    for name in input_directories:
        path = normalized[name]
        try:
            info = os.lstat(path)
        except FileNotFoundError as exc:
            raise Phase4E5CombinedGateError(f"required input directory is absent: {name}") from exc
        if not stat.S_ISDIR(info.st_mode):
            raise Phase4E5CombinedGateError(f"required input is not a directory: {name}")
    for name in absent_outputs:
        path = normalized[name]
        if os.path.lexists(path):
            raise Phase4E5CombinedGateError(f"output must not already exist: {name}")
        parent = path.parent
        _assert_no_symlink_components(parent, f"{name} parent")
        if not parent.is_dir():
            raise Phase4E5CombinedGateError(f"output parent must already exist: {name}")
    return normalized


def fingerprint(path_value: str | Path) -> dict[str, Any]:
    path = _absolute(path_value)
    assert_sidecars_absent(path, "during Phase 4E5 fingerprint")
    before = os.lstat(path)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise Phase4E5CombinedGateError("fingerprinted path must be a regular non-symlink file")
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != identity:
            raise Phase4E5CombinedGateError("file descriptor identity mismatch")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after_open = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after = os.lstat(path)
    if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or identity != (
        after_open.st_dev, after_open.st_ino, after_open.st_size, after_open.st_mtime_ns
    ):
        raise Phase4E5CombinedGateError("file changed while fingerprinting")
    assert_sidecars_absent(path, "after Phase 4E5 fingerprint")
    return {"sha256": digest.hexdigest(), "size_bytes": after.st_size, "mtime_ns": after.st_mtime_ns}


def assert_schema11_baseline(path_value: str | Path) -> dict[str, Any]:
    path = _absolute(path_value)
    result = fingerprint(path)
    if (result["sha256"], result["size_bytes"]) != (SCHEMA11_SHA256, SCHEMA11_SIZE_BYTES):
        raise Phase4E5CombinedGateError("Schema 11 baseline byte seal mismatch")
    verify_phase4d5_combined_database(path)
    return result


def _private_path(parent: Path, prefix: str, suffix: str) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=parent)
    os.close(descriptor)
    return Path(name)


def _cleanup_private_database(path: Path) -> None:
    for candidate in (path, *_derived_danger_paths(path)):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass


def _promote_absent_target(target: Path, staging: Path) -> None:
    lock_path = promotion_lock_path(target)
    lock_descriptor = os.open(
        lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    locked = False
    try:
        lock_exclusive(lock_descriptor)
        locked = True
        lock_info, lock_path_info = os.fstat(lock_descriptor), os.lstat(lock_path)
        if (
            not stat.S_ISREG(lock_info.st_mode)
            or stat.S_ISLNK(lock_path_info.st_mode)
            or (lock_info.st_dev, lock_info.st_ino) != (lock_path_info.st_dev, lock_path_info.st_ino)
        ):
            raise Phase4E5CombinedGateError("final stable lock identity mismatch")
        if _UNDER_FINAL_LOCK_TEST_HOOK is not None:
            _UNDER_FINAL_LOCK_TEST_HOOK(target, staging)
        if os.path.lexists(target):
            raise Phase4E5CombinedGateError("final target appeared before promotion")
        assert_sidecars_absent(target, "under final stable lock")
        assert_sidecars_absent(staging, "under final stable lock")
        descriptor = os.open(staging, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise Phase4E5CombinedGateError("private staging database is not regular")
            staging_identity = (info.st_dev, info.st_ino)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        published = False
        try:
            os.replace(staging, target)
            published = True
            promoted = os.lstat(target)
            if stat.S_ISLNK(promoted.st_mode) or (
                promoted.st_dev, promoted.st_ino
            ) != staging_identity:
                raise Phase4E5CombinedGateError("promoted final identity mismatch")
            sync_directory(target.parent)
        except Exception:
            if published:
                try:
                    current = os.lstat(target)
                    if not stat.S_ISLNK(current.st_mode) and (
                        current.st_dev, current.st_ino
                    ) == staging_identity:
                        target.unlink()
                        try:
                            sync_directory(target.parent)
                        except OSError:
                            pass
                except FileNotFoundError:
                    pass
            raise
    finally:
        if locked:
            unlock(lock_descriptor)
        os.close(lock_descriptor)


def build_phase4e5_combined(
    baseline_path: str | Path,
    database_path: str | Path,
    source_root: str | Path,
    repo_root: str | Path,
    manifest_path: str | Path,
    report_path: str | Path,
) -> dict[str, Any]:
    paths = assert_path_matrix(
        {
            "baseline": baseline_path, "database": database_path,
            "source_root": source_root, "repo_root": repo_root,
            "manifest": manifest_path, "build_report": report_path,
        },
        input_files=("baseline", "manifest"),
        input_directories=("source_root", "repo_root"),
        absent_outputs=("database", "build_report"),
    )
    baseline, target = paths["baseline"], paths["database"]
    baseline_fp = assert_schema11_baseline(baseline)
    staging = _private_path(target.parent, f".{target.name}.phase4e5-", ".sqlite")
    internal_report = _private_path(target.parent, f".{target.name}.phase4e5-", ".build.json")
    try:
        shutil.copyfile(baseline, staging)
        os.chmod(staging, 0o600)
        with staging.open("rb") as stream:
            os.fsync(stream.fileno())
        m3_report = ingest(staging, paths["source_root"], paths["repo_root"], paths["manifest"])
        write_report_atomically(
            internal_report, staging,
            {key: m3_report[key] for key in (
                "status", "schema_version", "family", "family_contract_version",
                "snapshot_id", "parser_build_id", "ingest_run_id", "config_checksum",
                "counts", "status_counts", "occurrence_set_digests", "coverage",
                "corpus_complete", "evidence_only", "adjacent_inventory_only",
                "db_path", "database_sha256",
            )},
        )
        verify_world_archive(
            staging, internal_report, paths["source_root"], paths["repo_root"],
            paths["manifest"], receipt_path=None,
        )
        staging_fp = fingerprint(staging)
        if fingerprint(baseline) != baseline_fp:
            raise Phase4E5CombinedGateError("Schema 11 baseline changed during build")
        try:
            promotion_lock_path(staging).unlink()
        except FileNotFoundError:
            pass
        result = {
            **m3_report,
            "db_path": str(target),
            "database_sha256": staging_fp["sha256"],
            "phase": "4E5",
            "combined_boundary": "AQ7000-7014+sealed-static+character+tutorial+world-archive-animal-view",
            "schema11_baseline": {
                "sha256": baseline_fp["sha256"], "size_bytes": baseline_fp["size_bytes"],
            },
            "database_size_bytes": staging_fp["size_bytes"],
            "whole_build_atomic": True,
        }
        if _BEFORE_FINAL_LOCK_TEST_HOOK is not None:
            _BEFORE_FINAL_LOCK_TEST_HOOK(target, staging)
        _promote_absent_target(target, staging)
        return result
    except Exception:
        if fingerprint(baseline) != baseline_fp:
            raise Phase4E5CombinedGateError("Schema 11 baseline changed during failed build")
        raise
    finally:
        _cleanup_private_database(staging)
        try:
            internal_report.unlink()
        except FileNotFoundError:
            pass


def _user_tables(conn: sqlite3.Connection, schema: str) -> tuple[str, ...]:
    return tuple(str(row[0]) for row in conn.execute(
        f"SELECT name FROM {schema}.sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ))


def _table_columns(conn: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f'PRAGMA {schema}.table_info("{table}")')]


def _quoted_columns(columns: list[str]) -> str:
    return ",".join('"' + item.replace('"', '""') + '"' for item in columns)


def _assert_bidirectional_equal(
    conn: sqlite3.Connection, table: str, baseline_schema: str = "baseline"
) -> tuple[int, int]:
    columns = _table_columns(conn, "main", table)
    if columns != _table_columns(conn, baseline_schema, table):
        raise Phase4E5CombinedGateError(f"protected table shape changed: {table}")
    quoted = _quoted_columns(columns)
    baseline_count = int(conn.execute(f'SELECT COUNT(*) FROM {baseline_schema}."{table}"').fetchone()[0])
    final_count = int(conn.execute(f'SELECT COUNT(*) FROM main."{table}"').fetchone()[0])
    if baseline_count != final_count:
        raise Phase4E5CombinedGateError(f"protected table count changed: {table}")
    for left, right in (("main", baseline_schema), (baseline_schema, "main")):
        if conn.execute(
            f'SELECT 1 FROM (SELECT {quoted} FROM {left}."{table}" '
            f'EXCEPT SELECT {quoted} FROM {right}."{table}") LIMIT 1'
        ).fetchone() is not None:
            raise Phase4E5CombinedGateError(f"protected table rows changed: {table}")
    return baseline_count, final_count


def _extra_ids(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[0]) for row in conn.execute(
        f'SELECT id FROM main."{table}" EXCEPT SELECT id FROM baseline."{table}"'
    )}


def _assert_baseline_subset(conn: sqlite3.Connection, table: str) -> dict[str, int]:
    columns = _table_columns(conn, "main", table)
    if columns != _table_columns(conn, "baseline", table):
        raise Phase4E5CombinedGateError(f"additive table shape changed: {table}")
    quoted = _quoted_columns(columns)
    if conn.execute(
        f'SELECT 1 FROM (SELECT {quoted} FROM baseline."{table}" '
        f'EXCEPT SELECT {quoted} FROM main."{table}") LIMIT 1'
    ).fetchone() is not None:
        raise Phase4E5CombinedGateError(f"baseline rows missing from additive table: {table}")
    before = int(conn.execute(f'SELECT COUNT(*) FROM baseline."{table}"').fetchone()[0])
    after = int(conn.execute(f'SELECT COUNT(*) FROM main."{table}"').fetchone()[0])
    return {"baseline_rows": before, "final_rows": after, "added_rows": after - before}


def _assert_baseline_wide_preservation(
    conn: sqlite3.Connection, identity: Mapping[str, str]
) -> dict[str, Any]:
    baseline_tables = _user_tables(conn, "baseline")
    if baseline_tables != SCHEMA11_USER_TABLES:
        raise Phase4E5CombinedGateError("Schema 11 user-table inventory mismatch")
    final_tables = set(_user_tables(conn, "main"))
    if final_tables != set(SCHEMA11_USER_TABLES) | set(WORLD_ARCHIVE_TABLES):
        raise Phase4E5CombinedGateError("Schema 12 combined user-table inventory mismatch")
    protected_counts: dict[str, dict[str, int]] = {}
    for table in PROTECTED_TABLES:
        before, after = _assert_bidirectional_equal(conn, table)
        protected_counts[table] = {"baseline_rows": before, "final_rows": after}
    additive_counts = {table: _assert_baseline_subset(conn, table) for table in sorted(ADDITIVE_TABLES)}

    if _extra_ids(conn, "parser_build") != {identity["parser_build_id"]}:
        raise Phase4E5CombinedGateError("unexpected parser_build additions")
    if _extra_ids(conn, "ingest_run") != {identity["ingest_run_id"]}:
        raise Phase4E5CombinedGateError("unexpected ingest_run additions")
    migration_extras = conn.execute(
        "SELECT version,applied_at FROM main.schema_migration "
        "EXCEPT SELECT version,applied_at FROM baseline.schema_migration"
    ).fetchall()
    if [tuple(row) for row in migration_extras] != [(12, EXPECTED_OBSERVED_AT)]:
        raise Phase4E5CombinedGateError("unexpected schema_migration additions")

    expected_file_ids = {
        digest_id("sf", identity["snapshot_id"], relative_path)
        for relative_path in PRIMARY_PATHS.values()
    }
    if _extra_ids(conn, "source_file") != expected_file_ids:
        raise Phase4E5CombinedGateError("unexpected source_file additions")
    expected_record_ids = {
        str(row[0]) for row in conn.execute(
            "SELECT DISTINCT source_record_id FROM world_archive_row_occurrence "
            "WHERE ingest_run_id=?", (identity["ingest_run_id"],)
        )
    }
    expected_record_ids.update(
        str(row[0]) for row in conn.execute(
            "SELECT DISTINCT source_record_id FROM world_archive_text_candidate_occurrence "
            "WHERE ingest_run_id=?", (identity["ingest_run_id"],)
        )
    )
    if len(expected_record_ids) != 4196 or _extra_ids(conn, "source_record") != expected_record_ids:
        raise Phase4E5CombinedGateError("unexpected source_record additions")
    expected_added = {
        "schema_migration": 1, "parser_build": 1, "ingest_run": 1,
        "source_file": 2, "source_record": 4196,
    }
    if {table: value["added_rows"] for table, value in additive_counts.items()} != expected_added:
        raise Phase4E5CombinedGateError("additive table count contract mismatch")
    empty_tables = sorted(
        table for table, counts in protected_counts.items() if counts["final_rows"] == 0
    )
    return {
        "protected_exact_table_count": len(protected_counts),
        "protected_exact_tables": protected_counts,
        "additive_tables": additive_counts,
        "empty_protected_tables": empty_tables,
    }


def verify_phase4e5_combined(
    baseline_path: str | Path,
    database_path: str | Path,
    build_report_path: str | Path,
    source_root: str | Path,
    repo_root: str | Path,
    manifest_path: str | Path,
    receipt_path: str | Path,
    aggregate_report_path: str | Path,
) -> dict[str, Any]:
    paths = assert_path_matrix(
        {
            "baseline": baseline_path, "database": database_path,
            "build_report": build_report_path, "source_root": source_root,
            "repo_root": repo_root, "manifest": manifest_path,
            "receipt": receipt_path, "aggregate_report": aggregate_report_path,
        },
        input_files=("baseline", "database", "build_report", "manifest"),
        input_directories=("source_root", "repo_root"),
        absent_outputs=("receipt", "aggregate_report"),
    )
    baseline_fp = assert_schema11_baseline(paths["baseline"])
    before = fingerprint(paths["database"])
    world = verify_world_archive(
        paths["database"], paths["build_report"], paths["source_root"],
        paths["repo_root"], paths["manifest"], receipt_path=None,
    )
    manifest = load_manifest(paths["manifest"])
    identity = expected_identity(manifest)
    uri = paths["database"].as_uri() + "?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute(
            "ATTACH DATABASE ? AS baseline",
            (paths["baseline"].as_uri() + "?mode=ro&immutable=1",),
        )
        preservation = _assert_baseline_wide_preservation(conn, identity)
        run_ids = [str(row[0]) for row in conn.execute("SELECT id FROM ingest_run ORDER BY id")]
        if run_ids != sorted((*EXPECTED_RUN_IDS, identity["ingest_run_id"])):
            raise Phase4E5CombinedGateError("combined ingest_run set mismatch")
        ledger = [int(row[0]) for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")]
        if ledger != list(range(1, 13)):
            raise Phase4E5CombinedGateError("combined schema ledger is not exactly 1..12")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise Phase4E5CombinedGateError("combined foreign key check failed")
        snapshots = int(conn.execute("SELECT COUNT(*) FROM source_snapshot").fetchone()[0])
        parser_builds = int(conn.execute("SELECT COUNT(*) FROM parser_build").fetchone()[0])
    if fingerprint(paths["database"]) != before or fingerprint(paths["baseline"]) != baseline_fp:
        raise Phase4E5CombinedGateError("database or baseline changed during verification")
    receipt_body = release_receipt(manifest, before)
    write_release_receipt_atomically(
        paths["receipt"], paths["database"], paths["build_report"], receipt_body
    )
    receipt_fp = fingerprint(paths["receipt"])
    world["release_receipt"] = {
        "path": str(paths["receipt"]), "receipt_contract": RECEIPT_CONTRACT,
        "receipt_version": RECEIPT_VERSION, "database": receipt_body["database"],
    }
    return {
        "status": "pass", "phase": "4E5", "schema_version": 12,
        "schema_ledger": list(range(1, 13)), "database": before,
        "schema11_baseline": baseline_fp,
        "world_archive_ingest_run_id": identity["ingest_run_id"],
        "world_archive_release": world,
        "baseline_wide_preservation": preservation,
        "ingest_runs": len(run_ids), "parser_builds": parser_builds,
        "source_snapshots": snapshots, "foreign_key_check": [],
        "release_receipt": receipt_fp, "logical_identity_created": False,
        "cross_family_merges": False,
    }


__all__ = [
    "ADDITIVE_TABLES", "PROTECTED_TABLES", "SCHEMA11_USER_TABLES",
    "WORLD_ARCHIVE_TABLES", "Phase4E5CombinedGateError", "assert_path_matrix",
    "assert_schema11_baseline", "build_phase4e5_combined", "fingerprint",
    "verify_phase4e5_combined",
]
