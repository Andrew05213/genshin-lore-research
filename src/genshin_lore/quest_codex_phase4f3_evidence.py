"""Atomic Schema 13 full QuestCodex Archive projection builder."""
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
from .phase4e5_combined_gate import assert_path_matrix
from .quest_codex_phase4f2_contract import (
    expected_identity as f2_expected_identity,
    load_manifest as load_f2_manifest,
)
from .quest_codex_phase4f2_evidence import (
    TABLES,
    _fingerprint,
    _migration_0013,
    _write_occurrences,
)
from .quest_codex_phase4f2_maintenance import (
    QuestCodexPhase4F2VerificationError,
    assert_exact_provenance,
)
from .quest_codex_phase4f2_release_oracle import exact_occurrence_tables
from .quest_codex_phase4f3_contract import (
    EXPECTED_COVERAGE,
    EXPECTED_MANIFEST_SHA256,
    EXPECTED_OBSERVED_AT,
    FAMILY,
    FAMILY_CONTRACT_VERSION,
    RUN_FAMILY,
    VERSIONS,
    derive_source_contract,
    expected_identity,
    load_manifest,
)
from .quest_codex_phase4f2_schema import verify_schema_13
from .util import canonical_json, digest_id
from .world_archive_phase4e2_promotion import assert_sidecars_absent
from .world_archive_phase4e2_stable_lock import promote_database


class QuestCodexPhase4F3EvidenceError(RuntimeError):
    pass


_PROMOTION_TEST_HOOK = None
_POST_REPLACE_TEST_HOOK = None
F2_MANIFEST = "manifests/quest_codex/anime-game-data-7.0-phase4f2-quest-codex-canary.json"


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
    raise QuestCodexPhase4F3EvidenceError(message)


def preflight(source_root: str | Path, repo_root: str | Path, manifest_path: str | Path) -> Preflight:
    paths = assert_path_matrix(
        {"source_root": source_root, "repo_root": repo_root, "manifest": manifest_path},
        protected_database_names=(), input_files=("manifest",),
        input_directories=("source_root", "repo_root"),
    )
    manifest = load_manifest(paths["manifest"])
    derived = derive_source_contract(paths["source_root"], paths["repo_root"], manifest)
    return Preflight(paths["source_root"], paths["repo_root"], manifest, derived, **expected_identity(manifest))


def _scope(p: Preflight) -> str:
    return canonical_json({
        "family": RUN_FAMILY, "provider_family": FAMILY,
        "family_contract_version": FAMILY_CONTRACT_VERSION,
        "manifest_id": p.manifest["manifest_id"], "snapshot_id": p.snapshot_id,
        "coverage": EXPECTED_COVERAGE, "corpus_complete": True,
        "primary_rows": 493, "referenced_chapter_support_rows": 140,
        "quest_lookup_inventory_only": True,
        "closure_counts": p.derived["counts"],
        "occurrence_set_digests": p.derived["occurrence_set_digests"],
        "logical_identity_created": False, "mcp_provider_added": False,
        "exact_ingest_run_required": True,
    })


def _config(p: Preflight) -> str:
    return canonical_json({
        "manifest_raw_sha256": EXPECTED_MANIFEST_SHA256,
        "versions": p.manifest["versions"], "source_commit": p.manifest["source"]["commit"],
        "source_tree": p.manifest["source"]["tree"], "coverage": EXPECTED_COVERAGE,
        "corpus_complete": True, "schema_contract": "schema13-migration0013-exact",
        "occurrence_set_digests": p.derived["occurrence_set_digests"],
        "logical_identity_created": False, "mcp_provider_added": False,
    })


def _family_run_ids(conn: sqlite3.Connection) -> list[str]:
    result: list[str] = []
    for run_id, scope_json in conn.execute("SELECT id,scope_json FROM ingest_run ORDER BY id"):
        try:
            scope = json.loads(scope_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise sqlite3.IntegrityError("invalid ingest run scope JSON") from exc
        if isinstance(scope, dict) and (
            scope.get("provider_family") == FAMILY
            or str(scope.get("family", "")).startswith(FAMILY)
        ):
            result.append(str(run_id))
    return result


def _f2_contract(p: Preflight) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    manifest = load_f2_manifest(p.repo_root / F2_MANIFEST)
    from .quest_codex_phase4f2_contract import derive_source_contract as derive_f2
    return manifest, f2_expected_identity(manifest), derive_f2(p.source_root, p.repo_root, manifest)


def _assert_exact_run_tables(
    conn: sqlite3.Connection,
    derived: dict[str, Any], identity: dict[str, str],
) -> None:
    for table, (columns, rows) in exact_occurrence_tables(derived, identity).items():
        actual = [tuple(row) for row in conn.execute(
            f"SELECT {','.join(columns)} FROM {table} WHERE ingest_run_id=? ORDER BY id",
            (identity["ingest_run_id"],),
        )]
        if actual != sorted(rows, key=lambda value: str(value[0])):
            raise sqlite3.IntegrityError(f"exact run tuple mismatch: {table}")


def _assert_allowed_stage(conn: sqlite3.Connection, p: Preflight, require_full: bool) -> bool:
    verify_schema_13(conn)
    _migration_0013(conn)
    _f2_manifest, f2_identity, f2_derived = _f2_contract(p)
    full_present = conn.execute("SELECT 1 FROM ingest_run WHERE id=?", (p.ingest_run_id,)).fetchone() is not None
    canary_present = conn.execute("SELECT 1 FROM ingest_run WHERE id=?", (f2_identity["ingest_run_id"],)).fetchone() is not None
    allowed = {p.ingest_run_id} if full_present else set()
    if canary_present:
        allowed.add(f2_identity["ingest_run_id"])
    family_runs = set(_family_run_ids(conn))
    if family_runs != allowed or (require_full and not full_present):
        raise sqlite3.IntegrityError("unauthorized QuestCodex run set")
    if not full_present and any(
        int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in TABLES.values()
    ) and not canary_present:
        raise sqlite3.IntegrityError("foreign QuestCodex rows present")
    if canary_present:
        try:
            assert_exact_provenance(conn, p.source_root, _f2_manifest, f2_derived, f2_identity)
        except QuestCodexPhase4F2VerificationError as exc:
            raise sqlite3.IntegrityError(str(exc)) from exc
        _assert_exact_run_tables(conn, f2_derived, f2_identity)
    if full_present:
        run = conn.execute(
            "SELECT snapshot_id,parser_build_id,scope_json,config_json,status,created_at FROM ingest_run WHERE id=?",
            (p.ingest_run_id,),
        ).fetchone()
        if run is None or tuple(run) != (
            p.snapshot_id, p.parser_build_id, _scope(p), _config(p), "complete", EXPECTED_OBSERVED_AT,
        ):
            raise sqlite3.IntegrityError("exact full QuestCodex ingest run mismatch")
        _write_occurrences(conn, p, False)
        identity = {key: getattr(p, key) for key in ("snapshot_id", "parser_build_id", "ingest_run_id")}
        _assert_exact_run_tables(conn, p.derived, identity)
    expected_by_table = {}
    for key, table in TABLES.items():
        expected_by_table[table] = (
            (p.derived["counts"][key] if full_present else 0)
            + (f2_derived["counts"][key] if canary_present else 0)
        )
    for table, expected in expected_by_table.items():
        if int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) != expected:
            raise sqlite3.IntegrityError(f"QuestCodex table contains unauthorized rows: {table}")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise sqlite3.IntegrityError("foreign key check failed")
    return canary_present


def _write_bundle(conn: sqlite3.Connection, p: Preflight) -> None:
    source = p.manifest["source"]
    catalog = conn.execute("SELECT kind,url,license_note FROM source_catalog WHERE id=?", (source["source_id"],)).fetchone()
    if catalog is None:
        conn.execute("INSERT INTO source_catalog(id,kind,url,license_note) VALUES(?,?,?,NULL)", (source["source_id"], "primary", source["upstream"]))
    elif tuple(catalog) != ("primary", source["upstream"], None):
        raise sqlite3.IntegrityError("source_catalog mismatch")
    snapshot = conn.execute(
        'SELECT source_id,release_label,build,"commit",tree,observed_at,status,metadata_json FROM source_snapshot WHERE id=?',
        (p.snapshot_id,),
    ).fetchone()
    wanted_snapshot = (
        source["source_id"], source["release_label"], source["build"], source["commit"],
        source["tree"], source["observed_at"], "active",
    )
    if snapshot is None:
        conn.execute(
            'INSERT INTO source_snapshot(id,source_id,release_label,build,"commit",tree,observed_at,parser_version,mapping_version,status,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (p.snapshot_id, *wanted_snapshot[:6], "legacy-unscoped", "legacy-unscoped", "active", canonical_json({"sealed_source_manifest": True})),
        )
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
            raise sqlite3.IntegrityError("source_file mismatch")
    for item in p.derived["source_records"]:
        file_id = file_ids[item["relative_path"]]
        record_id = digest_id("sr", file_id, item["source_pointer"])
        wanted = (file_id, item["source_pointer"], item["record_kind"], item["native_id"], item["raw_checksum"], canonical_json(item["raw_value"]))
        row = conn.execute("SELECT source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json FROM source_record WHERE id=?", (record_id,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO source_record(id,source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json) VALUES(?,?,?,?,?,?,?)", (record_id, *wanted))
        elif tuple(row) != wanted:
            raise sqlite3.IntegrityError("source_record physical coordinate mismatch")
    parser = (VERSIONS["parser"], VERSIONS["projection"], "quest-codex-phase4f3-sealed-full", p.config_checksum, EXPECTED_OBSERVED_AT)
    row = conn.execute("SELECT parser_version,mapping_version,fingerprint,config_checksum,created_at FROM parser_build WHERE id=?", (p.parser_build_id,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO parser_build(id,parser_version,mapping_version,fingerprint,config_checksum,created_at) VALUES(?,?,?,?,?,?)", (p.parser_build_id, *parser))
    elif tuple(row) != parser:
        raise sqlite3.IntegrityError("parser_build mismatch")
    if conn.execute("SELECT 1 FROM ingest_run WHERE id=?", (p.ingest_run_id,)).fetchone():
        raise sqlite3.IntegrityError("full ingest run already exists")
    conn.execute(
        "INSERT INTO ingest_run(id,snapshot_id,parser_build_id,scope_json,config_json,status,created_at) VALUES(?,?,?,?,?,'running',?)",
        (p.ingest_run_id, p.snapshot_id, p.parser_build_id, _scope(p), _config(p), EXPECTED_OBSERVED_AT),
    )
    _write_occurrences(conn, p, True)
    conn.execute("UPDATE ingest_run SET status='complete' WHERE id=?", (p.ingest_run_id,))


def _report(path: Path, p: Preflight, canary_preserved: bool) -> dict[str, Any]:
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        migration = _migration_0013(conn)
    return {
        "schema_migration_0013": migration, "status": "complete", "schema_version": 13,
        "family": FAMILY, "family_contract_version": FAMILY_CONTRACT_VERSION,
        "snapshot_id": p.snapshot_id, "parser_build_id": p.parser_build_id,
        "ingest_run_id": p.ingest_run_id, "config_checksum": p.config_checksum,
        "counts": p.derived["counts"], "status_counts": p.derived["status_counts"],
        "occurrence_set_digests": p.derived["occurrence_set_digests"],
        "coverage": EXPECTED_COVERAGE, "corpus_complete": True, "evidence_only": True,
        "quest_lookup_inventory_only": True, "canary_run_preserved": canary_preserved,
        "mcp_provider_added": False, "db_path": str(path.resolve()),
        "database_sha256": _fingerprint(path)[4],
    }


def ingest(db_path: str | Path, source_root: str | Path, repo_root: str | Path, manifest_path: str | Path) -> dict[str, Any]:
    target = Path(os.path.abspath(os.fspath(db_path)))
    assert_sidecars_absent(target, "before QuestCodex full build")
    paths = assert_path_matrix(
        {"database": target, "source_root": source_root, "repo_root": repo_root, "manifest": manifest_path},
        protected_database_names=("database",), input_files=("database", "manifest"),
        input_directories=("source_root", "repo_root"),
    )
    target = paths["database"]
    original = _fingerprint(target)
    p = preflight(paths["source_root"], paths["repo_root"], paths["manifest"])
    try:
        with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True) as readonly:
            versions = [int(row[0]) for row in readonly.execute("SELECT version FROM schema_migration ORDER BY version")]
            if versions not in (list(range(1, 13)), list(range(1, 14))):
                _fail("input schema must be exact 12 or 13")
            existing = readonly.execute("SELECT 1 FROM ingest_run WHERE id=?", (p.ingest_run_id,)).fetchone() is not None
            if versions[-1] == 13:
                canary_preserved = _assert_allowed_stage(readonly, p, existing)
            else:
                if existing:
                    _fail("Schema 12 input cannot contain the F3 ingest run")
                canary_preserved = False
        if existing:
            return _report(target, p, canary_preserved)
    except (sqlite3.Error, OSError, ValueError) as exc:
        _fail(f"QuestCodex Phase 4F3 stage preflight failed: {exc}")

    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.phase4f3-", suffix=".sqlite", dir=target.parent)
    os.close(descriptor)
    staging = Path(name)
    try:
        with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True) as source, sqlite3.connect(staging) as destination:
            source.backup(destination)
        with Database(staging) as database:
            if versions[-1] == 12:
                database.initialize_through(13)
            verify_schema_13(database.conn)
            database.conn.execute("BEGIN IMMEDIATE")
            _write_bundle(database.conn, p)
            database.conn.commit()
        with sqlite3.connect(staging.as_uri() + "?mode=ro", uri=True) as readonly:
            canary_preserved = _assert_allowed_stage(readonly, p, True)
        if _fingerprint(target) != original:
            _fail("database changed before promotion")
        os.chmod(staging, stat.S_IMODE(os.lstat(target).st_mode))
        promote_database(target, staging, original, _PROMOTION_TEST_HOOK, _POST_REPLACE_TEST_HOOK)
        assert_sidecars_absent(target, "after QuestCodex full promotion")
    except Exception as exc:
        if isinstance(exc, QuestCodexPhase4F3EvidenceError):
            raise
        _fail(f"QuestCodex Phase 4F3 atomic build failed: {exc}")
    finally:
        for candidate in (staging, *(Path(f"{staging}{suffix}") for suffix in ("-journal", "-wal", "-shm"))):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass
    return _report(target, p, canary_preserved)


__all__ = ["QuestCodexPhase4F3EvidenceError", "ingest", "preflight"]
