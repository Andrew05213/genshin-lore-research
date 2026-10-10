"""Atomic Schema 13 combined QuestCodex release and baseline gate.

This module is deliberately a release-time gate.  The two family verifiers
remain the authorities for their own physical occurrence contracts; this
module adds the combined-artifact preservation and publication contract.
"""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from .phase4e5_combined_gate import (
    SCHEMA11_USER_TABLES,
    WORLD_ARCHIVE_TABLES,
    _quoted_columns,
    _table_columns,
    _user_tables,
    assert_path_matrix,
    fingerprint,
)
from .quest_codex_phase4f3_contract import (
    FAMILY_CONTRACT_VERSION as QUEST_CONTRACT,
    derive_source_contract as derive_quest_contract,
    expected_identity as quest_expected_identity,
    load_manifest as load_quest_manifest,
)
from .quest_codex_phase4f3_evidence import ingest as ingest_quest
from .quest_codex_phase4f3_maintenance import verify as verify_quest
from .quest_codex_phase4f3_receipt import (
    load_release_receipt as load_quest_receipt,
    release_receipt as quest_release_receipt,
)
from .world_archive_phase4e3_contract import (
    FAMILY_CONTRACT_VERSION as WORLD_CONTRACT,
    derive_source_contract as derive_world_contract,
    expected_identity as world_expected_identity,
    load_manifest as load_world_manifest,
)
from .world_archive_phase4e3_maintenance import verify as verify_world
from .world_archive_phase4e3_receipt import (
    load_release_receipt as load_world_receipt,
    release_receipt as world_release_receipt,
)
from .world_archive_phase4e2_stable_lock import promotion_lock_path
from .world_archive_phase4e2_promotion import assert_sidecars_absent, sync_directory
from .quest_codex_phase4f2_schema import verify_schema_13
from .util import digest_id


class Phase4F5CombinedGateError(RuntimeError):
    """The Schema 12 input or Schema 13 combined output is outside F5."""


# ``Database.initialize_through(13)`` creates migration objects using the
# current clock.  F5 replaces only that metadata row with this fixed release
# value after the raw, sealed 0013 SQL has been applied.  It is build metadata,
# not evidence identity, and makes independent fresh database bytes reproducible.
DETERMINISTIC_MIGRATION_APPLIED_AT = "2026-08-31T00:00:00+00:00"
BASELINE_SCHEMA12_SHA256 = "9c74d81d9b56ebd314a0952a7ff3b4a2fe96a46d73020e27f7eaf5ba7554bbda"
BASELINE_SCHEMA12_SIZE_BYTES = 1700741120
OLD_WORLD_RECEIPT_SHA256 = "e2ee149958e41e5d07a487ad904c34bde646923563ebcd99b078de91ecf79eaa"
OLD_WORLD_RECEIPT_SIZE_BYTES = 2845
F5_ARTIFACT_CONTRACT = "genshin-lore-phase4f5-schema13-combined-v1"
SCHEMA12_TABLES = tuple(sorted(set(SCHEMA11_USER_TABLES) | set(WORLD_ARCHIVE_TABLES)))
PROVENANCE_TABLES = frozenset({"schema_migration", "parser_build", "ingest_run", "source_file", "source_record"})
SCHEMA12_PROTECTED_TABLES = tuple(item for item in SCHEMA12_TABLES if item not in PROVENANCE_TABLES)
QUEST_TABLES = frozenset({
    "quest_codex_row_occurrence", "quest_codex_raw_field_occurrence",
    "quest_codex_relationship_occurrence", "quest_codex_relationship_candidate_occurrence",
    "quest_codex_opaque_occurrence", "quest_codex_text_owner_occurrence",
    "quest_codex_text_resolution_occurrence", "quest_codex_text_candidate_occurrence",
    "quest_codex_asset_reference_occurrence",
})
QUEST_MANIFEST = "manifests/quest_codex/anime-game-data-7.0-phase4f3-quest-codex-full.json"
WORLD_MANIFEST = "manifests/world_archive/anime-game-data-7.0-phase4e3-world-archive-full.json"


def _fail(message: str) -> None:
    raise Phase4F5CombinedGateError(message)


def _strict_json_file(path: Path, label: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                _fail(f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"invalid {label}: {exc}")


def _manifest_paths(repo_root: Path, quest_manifest: Path, world_manifest: Path) -> tuple[Path, Path]:
    q = quest_manifest.resolve()
    w = world_manifest.resolve()
    if q != (repo_root / QUEST_MANIFEST).resolve():
        _fail("QuestCodex manifest path is not the tracked F3 manifest")
    if w != (repo_root / WORLD_MANIFEST).resolve():
        _fail("World Archive manifest path is not the tracked E3 manifest")
    return q, w


def _validate_old_world_receipt(path: Path, baseline_fp: Mapping[str, Any], world_manifest: Path) -> None:
    fp = fingerprint(path)
    if (fp["size_bytes"], fp["sha256"]) != (OLD_WORLD_RECEIPT_SIZE_BYTES, OLD_WORLD_RECEIPT_SHA256):
        _fail("old World Archive receipt byte seal mismatch")
    value = load_world_receipt(path, world_manifest)
    if value.get("database") != {"size_bytes": baseline_fp["size_bytes"], "sha256": baseline_fp["sha256"]}:
        _fail("old World Archive receipt does not bind the exact Schema 12 baseline")
    if value.get("verification", {}).get("schema_contract") != "schema12-migration0012-exact":
        _fail("old World Archive receipt is not the Schema 12 receipt")


_BLOCK_SIZE = 1024 * 1024
_PROMOTION_TEST_HOOK = None


def _copy_database(baseline: Path, staging: Path, staging_fd: int | None = None) -> None:
    """Copy into the already-created 0600 inode without unlink/replace.

    The destination descriptor remains the identity observed by the caller for
    the entire copy.  Both streams are fixed-block and the destination is
    fsynced before the descriptor is returned/closed.
    """
    own_destination = staging_fd is None
    destination = (
        os.open(staging, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        if own_destination else staging_fd
    )
    assert destination is not None
    source = os.open(baseline, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before_source = os.fstat(source)
        before_destination = os.fstat(destination)
        if not stat.S_ISREG(before_source.st_mode) or not stat.S_ISREG(before_destination.st_mode):
            _fail("baseline or private staging database is not regular")
        os.fchmod(destination, 0o600)
        os.ftruncate(destination, 0)
        os.lseek(destination, 0, os.SEEK_SET)
        while True:
            chunk = os.read(source, _BLOCK_SIZE)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(destination, view)
                if written <= 0:
                    _fail("private staging database copy made no progress")
                view = view[written:]
        os.fsync(destination)
        after_source = os.fstat(source)
        after_destination = os.fstat(destination)
        if (before_source.st_dev, before_source.st_ino, before_source.st_size, before_source.st_mtime_ns) != (
            after_source.st_dev, after_source.st_ino, after_source.st_size, after_source.st_mtime_ns
        ):
            _fail("Schema 12 baseline changed during private copy")
        if (before_destination.st_dev, before_destination.st_ino) != (
            after_destination.st_dev, after_destination.st_ino
        ) or stat.S_IMODE(after_destination.st_mode) != 0o600:
            _fail("private staging inode or mode changed during copy")
        if after_destination.st_size != after_source.st_size:
            _fail("private staging size differs from Schema 12 baseline")
    finally:
        os.close(source)
        if own_destination:
            os.close(destination)


def _stage_json(parent: Path, prefix: str, value: Mapping[str, Any]) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=".json", dir=parent)
    path = Path(name)
    try:
        payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            _fail("private JSON staging identity or mode mismatch")
        parsed = _strict_json_file(path, "private JSON staging")
        if parsed != dict(value):
            _fail("private JSON staging validation mismatch")
        return path
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _path_identity(path: Path) -> tuple[int, int, int]:
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        _fail("published output is not a regular non-symlink file")
    return info.st_dev, info.st_ino, info.st_ctime_ns


@contextmanager
def _stable_release_lock(anchor: Path):
    lock_path = promotion_lock_path(anchor)
    descriptor = os.open(
        lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    locked = False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        lock_info, path_info = os.fstat(descriptor), os.lstat(lock_path)
        if (
            stat.S_IMODE(lock_info.st_mode) != 0o600
            or stat.S_ISLNK(path_info.st_mode)
            or (lock_info.st_dev, lock_info.st_ino) != (path_info.st_dev, path_info.st_ino)
        ):
            _fail("stable release lock identity mismatch")
        yield
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _rollback_owned_outputs(anchor: Path, published: list[tuple[str, Path, tuple[int, ...]]]) -> None:
    """Reacquire the lock and remove only outputs still owned by this build."""
    if not published:
        return
    with _stable_release_lock(anchor):
        for _label, target, identity in reversed(published):
            try:
                current = os.lstat(target)
            except FileNotFoundError:
                continue
            if (
                stat.S_ISLNK(current.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or (
                    (current.st_dev, current.st_ino) != identity
                    if len(identity) == 2
                    else (current.st_dev, current.st_ino, current.st_ctime_ns) != identity
                )
                or any(os.path.lexists(item) for item in (Path(f"{target}-journal"), Path(f"{target}-wal"), Path(f"{target}-shm")))
            ):
                continue
            target.unlink()
            sync_directory(target.parent)


def _promote_outputs(anchor: Path, entries: list[tuple[str, Path, Path]]) -> list[tuple[str, Path, tuple[int, ...]]]:
    """Atomically publish coordinated outputs, with ownership-checked rollback."""
    published: list[tuple[str, Path, tuple[int, ...]]] = []
    try:
        with _stable_release_lock(anchor):
            parents = {target.parent for _label, target, _stage in entries}
            if len(parents) != 1:
                _fail("coordinated outputs must share a parent directory")
            for label, target, stage in entries:
                if os.path.lexists(target):
                    _fail(f"{label} output appeared before promotion")
                assert_sidecars_absent(target, f"before {label} promotion")
                assert_sidecars_absent(stage, f"before {label} promotion")
                info = os.lstat(stage)
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                    _fail(f"{label} private staging is not a 0600 regular file")
                descriptor = os.open(stage, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                try:
                    os.fsync(descriptor)
                    stage_info = os.fstat(descriptor)
                    stage_identity = (stage_info.st_dev, stage_info.st_ino)
                finally:
                    os.close(descriptor)
                # Register ownership before replace so an injector that raises
                # after performing os.replace still enters rollback.
                published.append((label, target, stage_identity))
                os.replace(stage, target)
                current_identity = _path_identity(target)
                # Keep the pre-replace staging (dev, ino) as ownership proof.
                # A post-replace competitor must therefore never be deleted.
                if current_identity[:2] != stage_identity:
                    _fail(f"{label} promoted identity mismatch")
                if _PROMOTION_TEST_HOOK is not None:
                    _PROMOTION_TEST_HOOK(label, target, stage)
                sync_directory(target.parent)
    except Exception:
        _rollback_owned_outputs(anchor, published)
        raise
    return published


def _set_deterministic_migration(path: Path) -> dict[str, Any]:
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        cursor = conn.execute(
            "UPDATE schema_migration SET applied_at=? WHERE version=13",
            (DETERMINISTIC_MIGRATION_APPLIED_AT,),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            _fail("migration 0013 deterministic ledger row is not unique")
        conn.commit()
        row = conn.execute("SELECT version,applied_at FROM schema_migration WHERE version=13").fetchone()
    if row != (13, DETERMINISTIC_MIGRATION_APPLIED_AT):
        _fail("migration 0013 deterministic ledger timestamp mismatch")
    return {"version": 13, "applied_at": DETERMINISTIC_MIGRATION_APPLIED_AT}


def _world_report(path: Path, derived: Mapping[str, Any], identity: Mapping[str, str], database_fp: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": "complete", "schema_version": 13, "family": "world_archive",
        "family_contract_version": WORLD_CONTRACT,
        "snapshot_id": identity["snapshot_id"], "parser_build_id": identity["parser_build_id"],
        "ingest_run_id": identity["ingest_run_id"], "config_checksum": identity["config_checksum"],
        "counts": derived["counts"], "status_counts": derived["status_counts"],
        "occurrence_set_digests": derived["occurrence_set_digests"],
        "coverage": "declared_world_archive_source_closure_complete", "corpus_complete": True,
        "evidence_only": True, "adjacent_inventory_only": True,
        "db_path": str(path.resolve()), "database_sha256": (database_fp or fingerprint(path))["sha256"],
    }


def _quest_report(path: Path, derived: Mapping[str, Any], identity: Mapping[str, str], migration: Mapping[str, Any], database_fp: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_migration_0013": dict(migration), "status": "complete", "schema_version": 13,
        "family": "quest_codex_archive", "family_contract_version": QUEST_CONTRACT,
        "snapshot_id": identity["snapshot_id"], "parser_build_id": identity["parser_build_id"],
        "ingest_run_id": identity["ingest_run_id"], "config_checksum": identity["config_checksum"],
        "counts": derived["counts"], "status_counts": derived["status_counts"],
        "occurrence_set_digests": derived["occurrence_set_digests"],
        "coverage": "declared_quest_codex_archive_source_closure_complete", "corpus_complete": True,
        "evidence_only": True, "quest_lookup_inventory_only": True, "canary_run_preserved": False,
        "mcp_provider_added": False, "db_path": str(path.resolve()),
        "database_sha256": (database_fp or fingerprint(path))["sha256"],
    }


def _private_report(parent: Path, prefix: str) -> Path:
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=".json", dir=parent)
    os.close(fd)
    return Path(name)


def _baseline_wide_preservation(
    conn: sqlite3.Connection, quest_identity: Mapping[str, str],
    quest_derived: Mapping[str, Any], migration: Mapping[str, Any],
) -> dict[str, Any]:
    base_tables = _user_tables(conn, "baseline")
    if base_tables != SCHEMA12_TABLES:
        _fail("Schema 12 baseline user-table inventory mismatch")
    final_tables = set(_user_tables(conn, "main"))
    if final_tables != set(SCHEMA12_TABLES) | set(QUEST_TABLES):
        _fail("Schema 13 combined user-table inventory mismatch")
    protected: dict[str, dict[str, int]] = {}
    for table in SCHEMA12_PROTECTED_TABLES:
        columns = _table_columns(conn, "main", table)
        if columns != _table_columns(conn, "baseline", table):
            _fail(f"protected table shape changed: {table}")
        quoted = _quoted_columns(columns)
        before = int(conn.execute(f'SELECT COUNT(*) FROM baseline."{table}"').fetchone()[0])
        after = int(conn.execute(f'SELECT COUNT(*) FROM main."{table}"').fetchone()[0])
        if before != after:
            _fail(f"protected table count changed: {table}")
        for left, right in (("main", "baseline"), ("baseline", "main")):
            if conn.execute(f'SELECT 1 FROM (SELECT {quoted} FROM {left}."{table}" EXCEPT SELECT {quoted} FROM {right}."{table}") LIMIT 1').fetchone() is not None:
                _fail(f"protected table rows changed: {table}")
        protected[table] = {"baseline_rows": before, "final_rows": after}
    additive: dict[str, dict[str, int]] = {}
    for table in sorted(PROVENANCE_TABLES):
        columns = _table_columns(conn, "main", table)
        if columns != _table_columns(conn, "baseline", table):
            _fail(f"additive table shape changed: {table}")
        quoted = _quoted_columns(columns)
        if conn.execute(f'SELECT 1 FROM (SELECT {quoted} FROM baseline."{table}" EXCEPT SELECT {quoted} FROM main."{table}") LIMIT 1').fetchone() is not None:
            _fail(f"baseline rows missing from additive table: {table}")
        before = int(conn.execute(f'SELECT COUNT(*) FROM baseline."{table}"').fetchone()[0])
        after = int(conn.execute(f'SELECT COUNT(*) FROM main."{table}"').fetchone()[0])
        additive[table] = {"baseline_rows": before, "final_rows": after, "added_rows": after - before}
    expected_ids: dict[str, set[str]] = {
        "parser_build": {quest_identity["parser_build_id"]},
        "ingest_run": {quest_identity["ingest_run_id"]},
        "source_file": {digest_id("sf", quest_identity["snapshot_id"], item["relative_path"]) for item in quest_derived["files"]},
        "source_record": {digest_id("sr", digest_id("sf", quest_identity["snapshot_id"], item["relative_path"]), item["source_pointer"]) for item in quest_derived["source_records"]},
    }
    added_ids: dict[str, set[str]] = {}
    for table, expected in expected_ids.items():
        actual = {str(row[0]) for row in conn.execute(f'SELECT id FROM main."{table}" EXCEPT SELECT id FROM baseline."{table}"')}
        base_ids = {str(row[0]) for row in conn.execute(f'SELECT id FROM baseline."{table}"')}
        wanted = expected - base_ids
        if actual != wanted:
            _fail(f"unexpected additive rows in {table}")
        added_ids[table] = actual
    migration_delta = [tuple(row) for row in conn.execute("SELECT version,applied_at FROM main.schema_migration EXCEPT SELECT version,applied_at FROM baseline.schema_migration ORDER BY version,applied_at")]
    if migration_delta != [(13, migration["applied_at"])]:
        _fail("schema_migration delta is not the deterministic F5 tuple")
    return {
        "baseline_user_table_count": len(base_tables),
        "protected_exact_table_count": len(protected),
        "protected_exact_tables": protected,
        "additive_tables": additive,
        "quest_tables_added": len(QUEST_TABLES),
        "quest_source_file_rows_added": len(added_ids["source_file"]),
        "quest_source_record_rows_added": len(added_ids["source_record"]),
    }


def _internal_verify(
    database: Path, baseline: Path, source_root: Path, repo_root: Path,
    quest_manifest: Path, world_manifest: Path,
    quest_derived: Mapping[str, Any], quest_identity: Mapping[str, str],
    world_derived: Mapping[str, Any], world_identity: Mapping[str, str],
    migration: Mapping[str, Any],
) -> dict[str, Any]:
    q_path = _private_report(database.parent, f".{database.name}.f5-quest-")
    w_path = _private_report(database.parent, f".{database.name}.f5-world-")
    try:
        database_fp = fingerprint(database)
        _write = lambda path, value: path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        _write(q_path, _quest_report(database, quest_derived, quest_identity, migration, database_fp))
        _write(w_path, _world_report(database, world_derived, world_identity, database_fp))
        quest_result = verify_quest(database, baseline, q_path, source_root, repo_root, quest_manifest, None)
        world_result = verify_world(database, w_path, source_root, repo_root, world_manifest, None)
        with sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("ATTACH DATABASE ? AS baseline", (baseline.as_uri() + "?mode=ro&immutable=1",))
            preservation = _baseline_wide_preservation(conn, quest_identity, quest_derived, migration)
            ledger = [int(row[0]) for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")]
            if ledger != list(range(1, 14)):
                _fail("final Schema 13 ledger is not exact 1..13")
            if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                _fail("combined foreign key check failed")
            conn.execute("DETACH DATABASE baseline")
        return {"quest": quest_result, "world": world_result, "baseline_wide_preservation": preservation}
    finally:
        for item in (q_path, w_path):
            try:
                item.unlink()
            except FileNotFoundError:
                pass


def _build_report_value(
    target: Path, database_fp: Mapping[str, Any], baseline_fp: Mapping[str, Any],
    migration: Mapping[str, Any], quest_identity: Mapping[str, str], quest_derived: Mapping[str, Any],
    world_identity: Mapping[str, str], world_derived: Mapping[str, Any], preservation: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "status": "complete", "phase": "4F5A", "artifact_contract": F5_ARTIFACT_CONTRACT,
        "schema_version": 13, "schema_ledger": list(range(1, 14)),
        "database": {"sha256": database_fp["sha256"], "size_bytes": database_fp["size_bytes"]},
        "database_path": str(target.resolve()),
        "schema_migration_0013": dict(migration),
        "baseline": {"sha256": baseline_fp["sha256"], "size_bytes": baseline_fp["size_bytes"]},
        "quest_codex": {"family": "quest_codex_archive", "family_contract_version": QUEST_CONTRACT, "ingest_run_id": quest_identity["ingest_run_id"], "counts": quest_derived["counts"], "status_counts": quest_derived["status_counts"], "coverage": "declared_quest_codex_archive_source_closure_complete", "corpus_complete": True},
        "world_archive": {"family": "world_archive", "family_contract_version": WORLD_CONTRACT, "ingest_run_id": world_identity["ingest_run_id"], "counts": world_derived["counts"], "status_counts": world_derived["status_counts"], "coverage": "declared_world_archive_source_closure_complete", "corpus_complete": True},
        "baseline_wide_preservation": preservation,
        "fresh_build": True,
        "byte_exact_contract": "same baseline/source/manifests and fixed migration applied_at produce identical database bytes",
    }


def _preflight_common(
    baseline_path: str | Path, database_path: str | Path, source_root: str | Path,
    repo_root: str | Path, quest_manifest_path: str | Path, world_manifest_path: str | Path,
    old_world_receipt_path: str | Path, *outputs: tuple[str, str | Path],
    input_files_extra: tuple[tuple[str, str | Path], ...] = (),
) -> tuple[dict[str, Path], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    named = {
        "baseline": baseline_path, "database": database_path, "source_root": source_root,
        "repo_root": repo_root, "quest_manifest": quest_manifest_path,
        "world_manifest": world_manifest_path, "old_world_receipt": old_world_receipt_path,
    }
    for name, value in outputs:
        named[name] = value
    for name, value in input_files_extra:
        named[name] = value
    path_values = assert_path_matrix(
        named,
        input_files=("baseline", "quest_manifest", "world_manifest", "old_world_receipt", *(name for name, _ in input_files_extra)),
        input_directories=("source_root", "repo_root"),
        absent_outputs=tuple(name for name, _ in outputs),
    )
    baseline_fp = fingerprint(path_values["baseline"])
    if (baseline_fp["sha256"], baseline_fp["size_bytes"]) != (BASELINE_SCHEMA12_SHA256, BASELINE_SCHEMA12_SIZE_BYTES):
        _fail("Schema 12 baseline byte seal mismatch")
    q_path, w_path = _manifest_paths(path_values["repo_root"], path_values["quest_manifest"], path_values["world_manifest"])
    _validate_old_world_receipt(path_values["old_world_receipt"], baseline_fp, w_path)
    q_manifest = load_quest_manifest(q_path); w_manifest = load_world_manifest(w_path)
    q_derived = derive_quest_contract(path_values["source_root"], path_values["repo_root"], q_manifest)
    w_derived = derive_world_contract(path_values["source_root"], path_values["repo_root"], w_manifest)
    identities = {"quest": quest_expected_identity(q_manifest), "world": world_expected_identity(w_manifest)}
    return path_values, baseline_fp, q_derived, w_derived, identities


def build_phase4f5_combined(
    baseline_path: str | Path, database_path: str | Path, source_root: str | Path,
    repo_root: str | Path, quest_manifest_path: str | Path, world_manifest_path: str | Path,
    old_world_receipt_path: str | Path, build_report_path: str | Path,
) -> dict[str, Any]:
    paths, baseline_fp, q_derived, w_derived, identities = _preflight_common(
        baseline_path, database_path, source_root, repo_root, quest_manifest_path,
        world_manifest_path, old_world_receipt_path,
        ("database", database_path), ("build_report", build_report_path),
    )
    baseline, target, report_target = paths["baseline"], paths["database"], paths["build_report"]
    staging_fd, staging_name = tempfile.mkstemp(
        prefix=f".{target.name}.phase4f5-", suffix=".sqlite", dir=target.parent
    )
    staging = Path(staging_name)
    report_stage: Path | None = None
    try:
        # Keep the mkstemp descriptor/inode; never unlink and recreate it.
        _copy_database(baseline, staging, staging_fd)
        os.close(staging_fd)
        staging_fd = -1
        ingest_quest(staging, paths["source_root"], paths["repo_root"], paths["quest_manifest"])
        migration = _set_deterministic_migration(staging)
        if fingerprint(baseline) != baseline_fp:
            _fail("Schema 12 baseline changed during F5 build")
        internal = _internal_verify(
            staging, baseline, paths["source_root"], paths["repo_root"], paths["quest_manifest"], paths["world_manifest"],
            q_derived, identities["quest"], w_derived, identities["world"], migration,
        )
        staging_fp = fingerprint(staging)
        result = _build_report_value(
            target, staging_fp, baseline_fp, migration, identities["quest"], q_derived,
            identities["world"], w_derived, internal["baseline_wide_preservation"],
        )
        report_stage = _stage_json(target.parent, f".{report_target.name}.phase4f5-", result)
        _promote_outputs(
            target,
            [("database", target, staging), ("build_report", report_target, report_stage)],
        )
        report_stage = None
        return result
    except Exception:
        if staging_fd >= 0:
            try:
                os.close(staging_fd)
            except OSError:
                pass
        raise
    finally:
        for item in (staging, report_stage, Path(f"{staging}-journal"), Path(f"{staging}-wal"), Path(f"{staging}-shm")):
            if item is None:
                continue
            try:
                item.unlink()
            except FileNotFoundError:
                pass


def _external_user_tables(conn: sqlite3.Connection, schema: str) -> tuple[str, ...]:
    return tuple(str(row[0]) for row in conn.execute(
        f"SELECT name FROM {schema}.sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ))


def _external_columns(conn: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f'PRAGMA {schema}.table_info("{table}")')]


def _external_quote(columns: list[str]) -> str:
    return ",".join('"' + item.replace('"', '""') + '"' for item in columns)


def _external_preservation(
    conn: sqlite3.Connection, quest_identity: Mapping[str, str],
    quest_derived: Mapping[str, Any], migration: Mapping[str, Any],
) -> dict[str, Any]:
    """Independent combined baseline oracle used by the external verifier."""
    baseline_tables = _external_user_tables(conn, "baseline")
    if baseline_tables != SCHEMA12_TABLES:
        _fail("Schema 12 baseline user-table inventory mismatch")
    final_tables = set(_external_user_tables(conn, "main"))
    if final_tables != set(SCHEMA12_TABLES) | set(QUEST_TABLES):
        _fail("Schema 13 combined user-table inventory mismatch")
    protected: dict[str, dict[str, int]] = {}
    for table in SCHEMA12_PROTECTED_TABLES:
        columns = _external_columns(conn, "main", table)
        if columns != _external_columns(conn, "baseline", table):
            _fail(f"protected table shape changed: {table}")
        quoted = _external_quote(columns)
        before = int(conn.execute(f'SELECT COUNT(*) FROM baseline."{table}"').fetchone()[0])
        after = int(conn.execute(f'SELECT COUNT(*) FROM main."{table}"').fetchone()[0])
        if before != after:
            _fail(f"protected table count changed: {table}")
        for left, right in (("main", "baseline"), ("baseline", "main")):
            changed = conn.execute(
                f'SELECT 1 FROM (SELECT {quoted} FROM {left}."{table}" EXCEPT SELECT {quoted} FROM {right}."{table}") LIMIT 1'
            ).fetchone()
            if changed is not None:
                _fail(f"protected table rows changed: {table}")
        protected[table] = {"baseline_rows": before, "final_rows": after}
    additive: dict[str, dict[str, int]] = {}
    for table in sorted(PROVENANCE_TABLES):
        columns = _external_columns(conn, "main", table)
        if columns != _external_columns(conn, "baseline", table):
            _fail(f"additive table shape changed: {table}")
        quoted = _external_quote(columns)
        if conn.execute(
            f'SELECT 1 FROM (SELECT {quoted} FROM baseline."{table}" EXCEPT SELECT {quoted} FROM main."{table}") LIMIT 1'
        ).fetchone() is not None:
            _fail(f"baseline rows missing from additive table: {table}")
        before = int(conn.execute(f'SELECT COUNT(*) FROM baseline."{table}"').fetchone()[0])
        after = int(conn.execute(f'SELECT COUNT(*) FROM main."{table}"').fetchone()[0])
        additive[table] = {"baseline_rows": before, "final_rows": after, "added_rows": after - before}
    expected_ids: dict[str, set[str]] = {
        "parser_build": {quest_identity["parser_build_id"]},
        "ingest_run": {quest_identity["ingest_run_id"]},
        "source_file": {digest_id("sf", quest_identity["snapshot_id"], item["relative_path"]) for item in quest_derived["files"]},
        "source_record": {digest_id("sr", digest_id("sf", quest_identity["snapshot_id"], item["relative_path"]), item["source_pointer"]) for item in quest_derived["source_records"]},
    }
    added: dict[str, set[str]] = {}
    for table, expected in expected_ids.items():
        actual = {str(row[0]) for row in conn.execute(f'SELECT id FROM main."{table}" EXCEPT SELECT id FROM baseline."{table}"')}
        base = {str(row[0]) for row in conn.execute(f'SELECT id FROM baseline."{table}"')}
        if actual != expected - base:
            _fail(f"unexpected additive rows in {table}")
        added[table] = actual
    delta = [tuple(row) for row in conn.execute(
        "SELECT version,applied_at FROM main.schema_migration "
        "EXCEPT SELECT version,applied_at FROM baseline.schema_migration ORDER BY version,applied_at"
    )]
    if delta != [(13, migration["applied_at"])]:
        _fail("schema_migration delta is not the deterministic F5 tuple")
    return {
        "baseline_user_table_count": len(baseline_tables),
        "protected_exact_table_count": len(protected),
        "protected_exact_tables": protected,
        "additive_tables": additive,
        "quest_tables_added": len(QUEST_TABLES),
        "quest_source_file_rows_added": len(added["source_file"]),
        "quest_source_record_rows_added": len(added["source_record"]),
    }


def _external_verify_database(
    database: Path, baseline: Path, source_root: Path, repo_root: Path,
    quest_manifest: Path, world_manifest: Path, q_derived: Mapping[str, Any],
    q_identity: Mapping[str, str], w_derived: Mapping[str, Any],
    w_identity: Mapping[str, str], migration: Mapping[str, Any],
    database_fp: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify the combined boundary with an oracle independent from builder code."""
    q_path = _private_report(database.parent, f".{database.name}.f5-external-quest-")
    w_path = _private_report(database.parent, f".{database.name}.f5-external-world-")
    try:
        q_path.write_text(json.dumps(_quest_report(database, q_derived, q_identity, migration, database_fp), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        w_path.write_text(json.dumps(_world_report(database, w_derived, w_identity, database_fp), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        quest_result = verify_quest(database, baseline, q_path, source_root, repo_root, quest_manifest, None)
        world_result = verify_world(database, w_path, source_root, repo_root, world_manifest, None)
        with sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
            conn.execute("PRAGMA query_only=ON")
            verify_schema_13(conn)
            conn.execute("ATTACH DATABASE ? AS baseline", (baseline.as_uri() + "?mode=ro&immutable=1",))
            try:
                preservation = _external_preservation(conn, q_identity, q_derived, migration)
                ledger_versions = [int(row[0]) for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")]
                migration_row = conn.execute("SELECT version,applied_at FROM schema_migration WHERE version=13").fetchone()
                if ledger_versions != list(range(1, 14)) or tuple(migration_row or ()) != (13, DETERMINISTIC_MIGRATION_APPLIED_AT):
                    _fail("final Schema 13 ledger or deterministic 0013 tuple is not exact")
                if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    _fail("combined foreign key check failed")
            finally:
                conn.execute("DETACH DATABASE baseline")
        return {"quest": quest_result, "world": world_result, "baseline_wide_preservation": preservation}
    except Phase4F5CombinedGateError:
        raise
    except Exception as exc:
        # A business/provenance mutation must have a stable, machine-readable reason.
        if isinstance(exc, (sqlite3.Error, RuntimeError, ValueError, KeyError, TypeError)):
            _fail("runtime_business_digest_mismatch")
        raise
    finally:
        for item in (q_path, w_path):
            try:
                item.unlink()
            except FileNotFoundError:
                pass


def _validate_staged_receipt(path: Path, family: str, manifest: Path) -> dict[str, Any]:
    if family == "quest_codex_archive":
        return load_quest_receipt(path, manifest)
    return load_world_receipt(path, manifest)


def verify_phase4f5_combined(
    baseline_path: str | Path, database_path: str | Path, build_report_path: str | Path,
    source_root: str | Path, repo_root: str | Path, quest_manifest_path: str | Path,
    world_manifest_path: str | Path, quest_receipt_path: str | Path,
    world_receipt_path: str | Path, aggregate_report_path: str | Path,
    old_world_receipt_path: str | Path,
) -> dict[str, Any]:
    paths, baseline_fp, q_derived, w_derived, identities = _preflight_common(
        baseline_path, database_path, source_root, repo_root, quest_manifest_path,
        world_manifest_path, old_world_receipt_path,
        ("quest_receipt", quest_receipt_path), ("world_receipt", world_receipt_path),
        ("aggregate_report", aggregate_report_path),
        input_files_extra=(("build_report", build_report_path),),
    )
    database = paths["database"]
    database_fp = fingerprint(database)
    report = _strict_json_file(paths["build_report"], "Phase 4F5 build report")
    migration = {"version": 13, "applied_at": DETERMINISTIC_MIGRATION_APPLIED_AT}
    external = _external_verify_database(
        database, paths["baseline"], paths["source_root"], paths["repo_root"],
        paths["quest_manifest"], paths["world_manifest"], q_derived, identities["quest"],
        w_derived, identities["world"], migration, database_fp,
    )
    expected_report = _build_report_value(
        database, database_fp, baseline_fp, migration, identities["quest"], q_derived,
        identities["world"], w_derived, external["baseline_wide_preservation"],
    )
    if report != expected_report:
        _fail("Phase 4F5 build report mismatch")
    if fingerprint(database) != database_fp or fingerprint(paths["baseline"]) != baseline_fp:
        _fail("database or Schema 12 baseline changed during F5 verification")
    q_receipt = quest_release_receipt(load_quest_manifest(paths["quest_manifest"]), database_fp)
    w_receipt = world_release_receipt(load_world_manifest(paths["world_manifest"]), database_fp, schema_version=13)
    q_stage = w_stage = aggregate_stage = None
    try:
        q_stage = _stage_json(database.parent, f".{paths['quest_receipt'].name}.phase4f5-", q_receipt)
        w_stage = _stage_json(database.parent, f".{paths['world_receipt'].name}.phase4f5-", w_receipt)
        _validate_staged_receipt(q_stage, "quest_codex_archive", paths["quest_manifest"])
        _validate_staged_receipt(w_stage, "world_archive", paths["world_manifest"])
        q_receipt_fp = fingerprint(q_stage)
        w_receipt_fp = fingerprint(w_stage)
        result = {
            "status": "pass", "phase": "4F5A", "artifact_contract": F5_ARTIFACT_CONTRACT,
            "schema_version": 13, "schema_ledger": list(range(1, 14)), "database": database_fp,
            "baseline": baseline_fp, "quest_codex": external["quest"], "world_archive": external["world"],
            "baseline_wide_preservation": external["baseline_wide_preservation"],
            "quest_receipt": q_receipt_fp, "world_receipt": w_receipt_fp,
            "logical_identity_created": False, "cross_family_merges": False,
            "byte_exact_second_build_required": True,
        }
        aggregate_stage = _stage_json(database.parent, f".{paths['aggregate_report'].name}.phase4f5-", result)
        _promote_outputs(
            database,
            [("quest_receipt", paths["quest_receipt"], q_stage),
             ("world_receipt", paths["world_receipt"], w_stage),
             ("aggregate_report", paths["aggregate_report"], aggregate_stage)],
        )
        q_stage = w_stage = aggregate_stage = None
        return result
    finally:
        for item in (q_stage, w_stage, aggregate_stage):
            if item is not None:
                try:
                    item.unlink()
                except FileNotFoundError:
                    pass


__all__ = [
    "BASELINE_SCHEMA12_SHA256", "BASELINE_SCHEMA12_SIZE_BYTES",
    "DETERMINISTIC_MIGRATION_APPLIED_AT", "F5_ARTIFACT_CONTRACT",
    "Phase4F5CombinedGateError", "build_phase4f5_combined", "verify_phase4f5_combined",
]
