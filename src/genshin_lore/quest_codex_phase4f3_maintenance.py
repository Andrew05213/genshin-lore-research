"""Independent read-only Phase 4F3 full release verifier."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .quest_codex_phase4f2_contract import (
    derive_source_contract as derive_f2_contract,
    expected_identity as f2_expected_identity,
    load_manifest as load_f2_manifest,
)
from .quest_codex_phase4f2_maintenance import (
    QuestCodexPhase4F2VerificationError,
    assert_exact_provenance,
)
from .quest_codex_phase4f2_release_oracle import exact_occurrence_tables as exact_f2_tables
from .quest_codex_phase4f2_schema import TABLE_SQL, verify_schema_13
from .quest_codex_phase4f3_receipt import (
    VERIFIER_ORACLE,
    assert_receipt_path_safe,
    release_receipt,
    write_release_receipt_atomically,
)
from .quest_codex_phase4f3_release_oracle import exact_occurrence_tables
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
from .util import canonical_json, digest_id
from .world_archive_phase4e2_schema import verify_schema_12


# Keep the F2 baseline contract local to the independent release verifier.
# Importing the F3 evidence builder here would make the verifier trust the
# mutation path it is intended to check. These names are the sealed Schema 13
# occurrence tables from migration 0013 and are intentionally not identity or
# ontology declarations.
F2_MANIFEST = "manifests/quest_codex/anime-game-data-7.0-phase4f2-quest-codex-canary.json"
TABLES = {
    "rows": "quest_codex_row_occurrence",
    "raw_fields": "quest_codex_raw_field_occurrence",
    "relationships": "quest_codex_relationship_occurrence",
    "relationship_candidates": "quest_codex_relationship_candidate_occurrence",
    "opaque": "quest_codex_opaque_occurrence",
    "text_owners": "quest_codex_text_owner_occurrence",
    "text_resolutions": "quest_codex_text_resolution_occurrence",
    "text_candidates": "quest_codex_text_candidate_occurrence",
    "assets": "quest_codex_asset_reference_occurrence",
}
_FINGERPRINT_TEST_HOOK = None


class QuestCodexPhase4F3VerificationError(RuntimeError):
    pass


def _fail(message: str) -> None:
    raise QuestCodexPhase4F3VerificationError(message)


def _assert_no_sidecars(path: Path) -> None:
    if any(os.path.lexists(f"{path}{suffix}") for suffix in ("-journal", "-wal", "-shm")):
        _fail("database sidecars are forbidden")


def _fingerprint(path: Path) -> dict[str, Any]:
    descriptor = -1
    try:
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            _fail("database must be a regular non-symlink file")
        _assert_no_sidecars(path)
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != identity:
            _fail("database descriptor identity mismatch")
        _assert_no_sidecars(path)
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        opened_after = os.fstat(descriptor)
        if _FINGERPRINT_TEST_HOOK is not None:
            _FINGERPRINT_TEST_HOOK(path)
        _assert_no_sidecars(path)
        after = os.lstat(path)
        _assert_no_sidecars(path)
    except OSError as exc:
        _fail(f"cannot fingerprint database: {exc}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    descriptor_identity = (opened_after.st_dev, opened_after.st_ino, opened_after.st_size, opened_after.st_mtime_ns)
    if identity != after_identity or identity != descriptor_identity:
        _fail("database changed while fingerprinting")
    return {"sha256": digest.hexdigest(), "size_bytes": after.st_size, "mtime_ns": after.st_mtime_ns}


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


def _scope(manifest: dict[str, Any], derived: dict[str, Any]) -> str:
    return canonical_json({
        "family": RUN_FAMILY, "provider_family": FAMILY,
        "family_contract_version": FAMILY_CONTRACT_VERSION,
        "manifest_id": manifest["manifest_id"], "snapshot_id": manifest["source"]["snapshot_id"],
        "coverage": EXPECTED_COVERAGE, "corpus_complete": True,
        "primary_rows": 493, "referenced_chapter_support_rows": 140,
        "quest_lookup_inventory_only": True, "closure_counts": derived["counts"],
        "occurrence_set_digests": derived["occurrence_set_digests"],
        "logical_identity_created": False, "mcp_provider_added": False,
        "exact_ingest_run_required": True,
    })


def _config(manifest: dict[str, Any], derived: dict[str, Any]) -> str:
    return canonical_json({
        "manifest_raw_sha256": EXPECTED_MANIFEST_SHA256,
        "versions": manifest["versions"], "source_commit": manifest["source"]["commit"],
        "source_tree": manifest["source"]["tree"], "coverage": EXPECTED_COVERAGE,
        "corpus_complete": True, "schema_contract": "schema13-migration0013-exact",
        "occurrence_set_digests": derived["occurrence_set_digests"],
        "logical_identity_created": False, "mcp_provider_added": False,
    })


def _migration(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT version,applied_at FROM schema_migration WHERE version=13").fetchone()
    if row is None or int(row[0]) != 13 or not isinstance(row[1], str) or not row[1]:
        _fail("migration 0013 ledger tuple missing")
    try:
        parsed = datetime.fromisoformat(row[1])
    except ValueError as exc:
        raise QuestCodexPhase4F3VerificationError("migration 0013 applied_at is invalid") from exc
    if parsed.tzinfo is None:
        _fail("migration 0013 applied_at must be timezone-aware")
    return {"version": 13, "applied_at": row[1]}


def _exact_run(conn: sqlite3.Connection, derived: dict[str, Any], identity: dict[str, str], oracle: Any) -> None:
    for table, (columns, rows) in oracle(derived, identity).items():
        actual = [tuple(row) for row in conn.execute(
            f"SELECT {','.join(columns)} FROM {table} WHERE ingest_run_id=? ORDER BY id",
            (identity["ingest_run_id"],),
        )]
        if actual != sorted(rows, key=lambda value: str(value[0])):
            _fail(f"exact business tuple mismatch: {table}")


def _assert_f2_provenance(
    conn: sqlite3.Connection, source_root: Path, manifest: dict[str, Any],
    derived: dict[str, Any], identity: dict[str, str],
) -> None:
    try:
        assert_exact_provenance(conn, source_root, manifest, derived, identity)
    except QuestCodexPhase4F2VerificationError as exc:
        _fail(f"F2 provenance mismatch: {exc}")


def _validate_baseline(
    baseline: Path, source_root: Path, repo_root: Path,
) -> tuple[int, dict[str, str] | None, dict[str, Any] | None]:
    with sqlite3.connect(baseline.as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        versions = [int(row[0]) for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")]
        if versions == list(range(1, 13)):
            verify_schema_12(conn)
            return 12, None, None
        if versions != list(range(1, 14)):
            _fail("baseline must be exact Schema 12 or exact F2 Schema 13 canary")
        verify_schema_13(conn)
        f2_manifest = load_f2_manifest(repo_root / F2_MANIFEST)
        f2_identity = f2_expected_identity(f2_manifest)
        f2_derived = derive_f2_contract(source_root, repo_root, f2_manifest)
        family_runs: list[str] = []
        for run_id, scope_json in conn.execute("SELECT id,scope_json FROM ingest_run ORDER BY id"):
            scope = json.loads(scope_json)
            if isinstance(scope, dict) and (scope.get("provider_family") == FAMILY or str(scope.get("family", "")).startswith(FAMILY)):
                family_runs.append(str(run_id))
        if family_runs != [f2_identity["ingest_run_id"]]:
            _fail("Schema 13 baseline is not the exact F2 canary run set")
        _assert_f2_provenance(conn, source_root, f2_manifest, f2_derived, f2_identity)
        _exact_run(conn, f2_derived, f2_identity, exact_f2_tables)
        for key, table in TABLES.items():
            if int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) != f2_derived["counts"][key]:
                _fail("Schema 13 baseline contains foreign QuestCodex rows")
        return 13, f2_identity, f2_derived


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _baseline_preservation(
    conn: sqlite3.Connection, baseline: Path, baseline_schema: int,
    identity: dict[str, str], derived: dict[str, Any], migration: dict[str, Any],
) -> dict[str, int]:
    conn.execute("ATTACH DATABASE ? AS baseline", (f"file:{baseline}?mode=ro&immutable=1",))
    try:
        base_tables = [str(row[0]) for row in conn.execute("SELECT name FROM baseline.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        actual_tables = [str(row[0]) for row in conn.execute("SELECT name FROM main.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        expected_tables = set(base_tables) | (set(TABLE_SQL) if baseline_schema == 12 else set())
        if set(actual_tables) != expected_tables:
            _fail("Schema 13 user-table inventory differs from baseline plus migration 0013")
        additive = {"schema_migration", "parser_build", "ingest_run", "source_file", "source_record"} | set(TABLE_SQL)
        protected = 0
        for table in base_tables:
            if table in additive:
                continue
            quoted = _quote(table)
            if conn.execute(f"SELECT 1 FROM (SELECT * FROM baseline.{quoted} EXCEPT SELECT * FROM main.{quoted}) LIMIT 1").fetchone() or conn.execute(f"SELECT 1 FROM (SELECT * FROM main.{quoted} EXCEPT SELECT * FROM baseline.{quoted}) LIMIT 1").fetchone():
                _fail(f"baseline protected table changed: {table}")
            protected += 1
        for table in set(base_tables) & additive:
            quoted = _quote(table)
            if conn.execute(f"SELECT 1 FROM (SELECT * FROM baseline.{quoted} EXCEPT SELECT * FROM main.{quoted}) LIMIT 1").fetchone():
                _fail(f"baseline additive table lost rows: {table}")
        expected_ids = {
            "parser_build": {identity["parser_build_id"]},
            "ingest_run": {identity["ingest_run_id"]},
            "source_file": {digest_id("sf", identity["snapshot_id"], item["relative_path"]) for item in derived["files"]},
            "source_record": {digest_id("sr", digest_id("sf", identity["snapshot_id"], item["relative_path"]), item["source_pointer"]) for item in derived["source_records"]},
        }
        if baseline_schema == 12:
            ledger = [tuple(row) for row in conn.execute("SELECT version,applied_at FROM main.schema_migration EXCEPT SELECT version,applied_at FROM baseline.schema_migration")]
            if ledger != [(migration["version"], migration["applied_at"])]:
                _fail("migration ledger delta is not exact")
        else:
            if conn.execute("SELECT 1 FROM (SELECT * FROM main.schema_migration EXCEPT SELECT * FROM baseline.schema_migration) LIMIT 1").fetchone():
                _fail("Schema 13 baseline migration ledger changed")
        for table, column in (("parser_build", "id"), ("ingest_run", "id"), ("source_file", "id"), ("source_record", "id")):
            extras = {str(row[0]) for row in conn.execute(f"SELECT {_quote(column)} FROM main.{_quote(table)} EXCEPT SELECT {_quote(column)} FROM baseline.{_quote(table)}")}
            if not extras <= expected_ids[table]:
                _fail(f"unapproved additive rows in {table}")
        return {"protected_exact_tables": protected, "additive_subset_tables": len(additive), "baseline_user_tables": len(base_tables)}
    finally:
        conn.execute("DETACH DATABASE baseline")


def verify(
    db_path: str | Path, baseline_path: str | Path, report_path: str | Path,
    source_root: str | Path, repo_root: str | Path, manifest_path: str | Path,
    receipt_path: str | Path | None = None,
) -> dict[str, Any]:
    path = Path(os.path.abspath(os.fspath(db_path)))
    baseline = Path(os.path.abspath(os.fspath(baseline_path)))
    source = Path(source_root).resolve()
    repo = Path(repo_root).resolve()
    manifest_path = Path(manifest_path).resolve()
    if receipt_path is not None:
        assert_receipt_path_safe(receipt_path, path, baseline, report_path, manifest_path)
    before = _fingerprint(path)
    baseline_before = _fingerprint(baseline)
    manifest = load_manifest(manifest_path)
    derived = derive_source_contract(source, repo, manifest)
    identity = expected_identity(manifest)
    baseline_schema, f2_identity, f2_derived = _validate_baseline(baseline, source, repo)
    report = _strict_json_file(Path(report_path), "Phase 4F3 build report")
    migration_report = report.get("schema_migration_0013") if isinstance(report, dict) else None
    expected_report = {
        "schema_migration_0013": migration_report, "status": "complete", "schema_version": 13,
        "family": FAMILY, "family_contract_version": FAMILY_CONTRACT_VERSION,
        "snapshot_id": identity["snapshot_id"], "parser_build_id": identity["parser_build_id"],
        "ingest_run_id": identity["ingest_run_id"], "config_checksum": identity["config_checksum"],
        "counts": derived["counts"], "status_counts": derived["status_counts"],
        "occurrence_set_digests": derived["occurrence_set_digests"],
        "coverage": EXPECTED_COVERAGE, "corpus_complete": True, "evidence_only": True,
        "quest_lookup_inventory_only": True, "canary_run_preserved": baseline_schema == 13,
        "mcp_provider_added": False, "db_path": str(path.resolve()), "database_sha256": before["sha256"],
    }
    if report != expected_report:
        _fail("Phase 4F3 build report mismatch")
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
            conn.execute("PRAGMA query_only=ON")
            verify_schema_13(conn)
            migration = _migration(conn)
            if migration_report != migration:
                _fail("build report migration ledger tuple mismatch")
            run = conn.execute("SELECT snapshot_id,parser_build_id,scope_json,config_json,status,created_at FROM ingest_run WHERE id=?", (identity["ingest_run_id"],)).fetchone()
            if run is None or tuple(run) != (identity["snapshot_id"], identity["parser_build_id"], _scope(manifest, derived), _config(manifest, derived), "complete", EXPECTED_OBSERVED_AT):
                _fail("exact full QuestCodex ingest run mismatch")
            allowed_runs = {identity["ingest_run_id"]}
            if f2_identity is not None:
                allowed_runs.add(f2_identity["ingest_run_id"])
                _assert_f2_provenance(conn, source, load_f2_manifest(repo / F2_MANIFEST), f2_derived, f2_identity)
                _exact_run(conn, f2_derived, f2_identity, exact_f2_tables)
            family_runs: set[str] = set()
            for run_id, scope_json in conn.execute("SELECT id,scope_json FROM ingest_run ORDER BY id"):
                scope = json.loads(scope_json)
                if isinstance(scope, dict) and (scope.get("provider_family") == FAMILY or str(scope.get("family", "")).startswith(FAMILY)):
                    family_runs.add(str(run_id))
            if family_runs != allowed_runs:
                _fail("unauthorized QuestCodex run set")
            parser = conn.execute("SELECT parser_version,mapping_version,fingerprint,config_checksum,created_at FROM parser_build WHERE id=?", (identity["parser_build_id"],)).fetchone()
            if parser is None or tuple(parser) != (VERSIONS["parser"], VERSIONS["projection"], "quest-codex-phase4f3-sealed-full", identity["config_checksum"], EXPECTED_OBSERVED_AT):
                _fail("parser build mismatch")
            source_meta = manifest["source"]
            snapshot = conn.execute('SELECT source_id,release_label,build,"commit",tree,observed_at,status,metadata_json FROM source_snapshot WHERE id=?', (identity["snapshot_id"],)).fetchone()
            wanted_snapshot = (source_meta["source_id"], source_meta["release_label"], source_meta["build"], source_meta["commit"], source_meta["tree"], source_meta["observed_at"], "active")
            if snapshot is None or tuple(snapshot)[:7] != wanted_snapshot or not isinstance(json.loads(str(snapshot[7])), dict):
                _fail("parser-independent snapshot mismatch")
            for item in derived["files"]:
                file_id = digest_id("sf", identity["snapshot_id"], item["relative_path"])
                row = conn.execute("SELECT snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path FROM source_file WHERE id=?", (file_id,)).fetchone()
                wanted = (identity["snapshot_id"], item["relative_path"], item["blob_oid"], item["sha256"], item["size_bytes"], item["format"], str(source))
                if row is None or tuple(row) != wanted:
                    _fail("source file mismatch")
            for item in derived["source_records"]:
                file_id = digest_id("sf", identity["snapshot_id"], item["relative_path"])
                record_id = digest_id("sr", file_id, item["source_pointer"])
                row = conn.execute("SELECT source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json FROM source_record WHERE id=?", (record_id,)).fetchone()
                wanted = (file_id, item["source_pointer"], item["record_kind"], item["native_id"], item["raw_checksum"], canonical_json(item["raw_value"]))
                if row is None or tuple(row) != wanted:
                    _fail("source record mismatch")
            _exact_run(conn, derived, identity, exact_occurrence_tables)
            for table, column, expected in (
                ("quest_codex_relationship_occurrence", "resolution_status", derived["status_counts"]["relationships"]),
                ("quest_codex_opaque_occurrence", "resolution_status", derived["status_counts"]["opaque"]),
                ("quest_codex_text_resolution_occurrence", "resolution_status", derived["status_counts"]["text_resolutions"]),
                ("quest_codex_asset_reference_occurrence", "reference_status", derived["status_counts"]["assets"]),
            ):
                actual = dict(sorted(Counter(str(row[0]) for row in conn.execute(f"SELECT {column} FROM {table} WHERE ingest_run_id=?", (identity["ingest_run_id"],))).items()))
                if actual != expected:
                    _fail(f"full-run status matrix mismatch: {table}")
            expected_totals = {
                table: derived["counts"][key] + (f2_derived["counts"][key] if f2_derived is not None else 0)
                for key, table in TABLES.items()
            }
            for table, expected in expected_totals.items():
                if int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) != expected:
                    _fail(f"foreign QuestCodex rows present: {table}")
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                _fail("foreign key check failed")
            preservation = _baseline_preservation(conn, baseline, baseline_schema, identity, derived, migration)
    except sqlite3.Error as exc:
        _fail(f"read-only verification failed: {exc}")
    after = _fingerprint(path)
    baseline_after = _fingerprint(baseline)
    if after != before or baseline_after != baseline_before:
        _fail("verifier modified database or baseline")
    receipt = release_receipt(manifest, after)
    result = {
        "status": "pass", "oracle": VERIFIER_ORACLE,
        "family_contract_version": FAMILY_CONTRACT_VERSION,
        "ingest_run_id": identity["ingest_run_id"], "counts": derived["counts"],
        "status_counts": derived["status_counts"], "baseline_schema": baseline_schema,
        "canary_run_preserved": baseline_schema == 13,
        "baseline_preservation": preservation,
        "database_fingerprint": after, "baseline_fingerprint": baseline_after,
        "runtime_canonical_business_digests": derived["occurrence_set_digests"],
        "release_receipt": receipt,
    }
    if receipt_path is not None:
        write_release_receipt_atomically(receipt_path, receipt, path, baseline, report_path, manifest_path)
    return result


__all__ = ["QuestCodexPhase4F3VerificationError", "verify"]
