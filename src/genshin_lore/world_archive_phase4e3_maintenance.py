"""Read-only release verifier for the Phase 4E3 World Archive full gate."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from collections import Counter
from pathlib import Path
from typing import Any

from .util import canonical_json, digest_id
from .world_archive_phase4e2_schema import verify_schema_12
from .quest_codex_phase4f2_schema import verify_schema_13
from .world_archive_phase4e3_contract import (
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
    occurrence_id,
)
from .world_archive_phase4e3_release_oracle import exact_occurrence_tables
from .world_archive_phase4e3_receipt import (
    assert_release_receipt_path_safe,
    release_receipt,
    write_release_receipt_atomically,
)


class WorldArchivePhase4E3VerificationError(RuntimeError):
    """The full-boundary database or receipt is not release-valid."""


def _fail(message: str) -> None:
    raise WorldArchivePhase4E3VerificationError(message)


def _fingerprint(path: Path) -> dict[str, Any]:
    def sidecars_absent() -> bool:
        return not any(
            os.path.lexists(f"{path}{suffix}")
            for suffix in ("-journal", "-wal", "-shm")
        )

    descriptor = -1
    try:
        before = os.lstat(path)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or not sidecars_absent()
        ):
            _fail("World Archive Phase 4E3 database must be a regular non-symlink file")
        before_identity = (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns
        )
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            != before_identity
        ):
            _fail("World Archive Phase 4E3 database descriptor identity mismatch")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        descriptor_after = os.fstat(descriptor)
        after = os.lstat(path)
    except OSError as exc:
        _fail(f"cannot fingerprint World Archive Phase 4E3 database: {exc}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    after_identity = (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    )
    descriptor_identity = (
        descriptor_after.st_dev, descriptor_after.st_ino,
        descriptor_after.st_size, descriptor_after.st_mtime_ns,
    )
    if (
        stat.S_ISLNK(after.st_mode)
        or not stat.S_ISREG(after.st_mode)
        or before_identity != after_identity
        or before_identity != descriptor_identity
        or not sidecars_absent()
    ):
        _fail("World Archive Phase 4E3 database changed while fingerprinting")
    return {
        "sha256": digest.hexdigest(),
        "size_bytes": after.st_size,
        "mtime_ns": after.st_mtime_ns,
    }


def _strict_json(value: Any, label: str) -> Any:
    def reject(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                _fail(f"duplicate JSON key in {label}: {key}")
            result[key] = item
        return result

    def reject_constant(item: str) -> Any:
        _fail(f"non-standard JSON constant in {label}: {item}")

    try:
        return json.loads(str(value), object_pairs_hook=reject, parse_constant=reject_constant)
    except WorldArchivePhase4E3VerificationError:
        raise
    except (TypeError, json.JSONDecodeError) as exc:
        _fail(f"invalid JSON in {label}: {exc}")


def _scope(manifest: dict[str, Any], derived: dict[str, Any]) -> str:
    return canonical_json({
        "family": RUN_FAMILY,
        "provider_family": FAMILY,
        "family_contract_version": FAMILY_CONTRACT_VERSION,
        "manifest_id": manifest["manifest_id"],
        "snapshot_id": manifest["source"]["snapshot_id"],
        "coverage": EXPECTED_COVERAGE,
        "corpus_complete": True,
        "selected_rows": manifest["boundary"]["selected_rows"],
        "closure_counts": derived["counts"],
        "occurrence_set_digests": derived["occurrence_set_digests"],
        "full_world_archive_scan": True,
        "adjacent_inventory_only": True,
        "logical_identity_created": False,
        "mcp_provider_added": False,
        "exact_ingest_run_required": True,
    })


def _config(manifest: dict[str, Any], derived: dict[str, Any]) -> str:
    return canonical_json({
        "manifest_raw_sha256": EXPECTED_MANIFEST_SHA256,
        "versions": manifest["versions"],
        "source_commit": manifest["source"]["commit"],
        "source_tree": manifest["source"]["tree"],
        "coverage": EXPECTED_COVERAGE,
        "corpus_complete": True,
        "schema_contract": "schema12-migration0012-exact",
        "occurrence_set_digests": derived["occurrence_set_digests"],
        "logical_identity_created": False,
        "mcp_provider_added": False,
    })


def _assert_no_world_archive_object_pollution(conn: sqlite3.Connection) -> None:
    expected_tables = {
        "world_archive_row_occurrence", "world_archive_raw_field_occurrence",
        "world_archive_relationship_occurrence", "world_archive_opaque_occurrence",
        "world_archive_text_owner_occurrence", "world_archive_text_resolution_occurrence",
        "world_archive_text_candidate_occurrence", "world_archive_asset_reference_occurrence",
    }
    for object_type, name in conn.execute("SELECT type,name FROM sqlite_master WHERE type IN ('table','view','index','trigger')"):
        name = str(name)
        if name.startswith("world_archive_") and name not in expected_tables and not name.startswith(("idx_world_archive_", "uq_world_archive_", "trg_world_archive_")):
            _fail(f"World Archive logical/derived object pollution: {object_type} {name}")


def verify(
    db_path: str | Path,
    report_path: str | Path,
    source_root: str | Path,
    repo_root: str | Path,
    manifest_path: str | Path,
    receipt_path: str | Path | None = None,
) -> dict[str, Any]:
    path = Path(os.path.abspath(os.fspath(db_path)))
    if receipt_path is not None:
        assert_release_receipt_path_safe(receipt_path, path, report_path)
    for suffix in ("-journal", "-wal", "-shm"):
        if Path(f"{path}{suffix}").exists():
            _fail("World Archive Phase 4E3 database sidecars are forbidden")
    before = _fingerprint(path)
    try:
        manifest = load_manifest(manifest_path)
        derived = derive_source_contract(source_root, repo_root, manifest)
        identity = expected_identity(manifest)
    except WorldArchivePhase4E3VerificationError:
        raise
    except Exception as exc:
        _fail(str(exc))
    try:
        report = _strict_json(Path(report_path).read_text(encoding="utf-8"), "World Archive Phase 4E3 report")
    except OSError as exc:
        _fail(f"cannot read World Archive Phase 4E3 report: {exc}")
    reported_schema_version = report.get("schema_version")
    if reported_schema_version not in (12, 13):
        _fail("World Archive report schema_version must be 12 or 13")
    expected_report = {
        "status": "complete", "schema_version": reported_schema_version, "family": FAMILY,
        "family_contract_version": FAMILY_CONTRACT_VERSION,
        "snapshot_id": identity["snapshot_id"], "parser_build_id": identity["parser_build_id"],
        "ingest_run_id": identity["ingest_run_id"], "config_checksum": identity["config_checksum"],
        "counts": derived["counts"], "status_counts": derived["status_counts"],
        "occurrence_set_digests": derived["occurrence_set_digests"],
        "coverage": EXPECTED_COVERAGE, "corpus_complete": True, "evidence_only": True,
        "adjacent_inventory_only": True, "db_path": str(path.resolve()), "database_sha256": before["sha256"],
    }
    if report != expected_report:
        _fail("World Archive Phase 4E3 build report mismatch")
    uri = path.as_uri() + "?mode=ro&immutable=1"
    verified_schema_version: int | None = None
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            conn.execute("PRAGMA query_only=ON")
            versions = [int(row[0]) for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")]
            if versions == list(range(1, 13)):
                verify_schema_12(conn)
                verified_schema_version = 12
            elif versions == list(range(1, 14)):
                verify_schema_13(conn)
                verified_schema_version = 13
            else:
                _fail("World Archive database schema ledger is not exact Schema 12 or 13")
            if verified_schema_version != reported_schema_version:
                _fail("World Archive report/database schema version mismatch")
            _assert_no_world_archive_object_pollution(conn)
            run = conn.execute("SELECT snapshot_id,parser_build_id,scope_json,config_json,status,created_at FROM ingest_run WHERE id=?", (identity["ingest_run_id"],)).fetchone()
            wanted_run = (identity["snapshot_id"], identity["parser_build_id"], _scope(manifest, derived), _config(manifest, derived), "complete", EXPECTED_OBSERVED_AT)
            if run is None or tuple(run) != wanted_run:
                _fail("World Archive Phase 4E3 exact ingest run mismatch")
            family_runs = []
            for run_id, scope_json in conn.execute("SELECT id,scope_json FROM ingest_run ORDER BY id"):
                scope = _strict_json(scope_json, f"ingest_run {run_id} scope_json")
                if isinstance(scope, dict) and scope.get("family") == RUN_FAMILY:
                    family_runs.append(str(run_id))
            if family_runs != [identity["ingest_run_id"]]:
                _fail("World Archive Phase 4E3 same-family run set mismatch")
            source = manifest["source"]
            catalog = conn.execute("SELECT kind,url,license_note FROM source_catalog WHERE id=?", (source["source_id"],)).fetchone()
            if catalog is None or tuple(catalog) != ("primary", source["upstream"], None):
                _fail("World Archive Phase 4E3 source_catalog mismatch")
            snapshot = conn.execute('SELECT source_id,release_label,build,"commit",tree,observed_at,status,metadata_json FROM source_snapshot WHERE id=?', (identity["snapshot_id"],)).fetchone()
            allowed_metadata = {
                canonical_json({"sealed_source_manifest": True}),
                canonical_json({"sealed_source_manifest": True, "phase4e2_world_archive_canary": True}),
                canonical_json({"sealed_source_manifest": True, "phase4e3_world_archive_full": True}),
                canonical_json({"phase4d2b_full_boundary": True, "sealed_source_manifest": True}),
            }
            wanted_snapshot = (source["source_id"], source["release_label"], source["build"], source["commit"], source["tree"], source["observed_at"], "active")
            if snapshot is None or tuple(snapshot)[:7] != wanted_snapshot or tuple(snapshot)[7] not in allowed_metadata:
                _fail("World Archive Phase 4E3 parser-independent source_snapshot mismatch")
            parser = conn.execute("SELECT parser_version,mapping_version,fingerprint,config_checksum,created_at FROM parser_build WHERE id=?", (identity["parser_build_id"],)).fetchone()
            if parser is None or tuple(parser) != (VERSIONS["parser"], VERSIONS["projection"], "world-archive-phase4e3-sealed-full", identity["config_checksum"], EXPECTED_OBSERVED_AT):
                _fail("World Archive Phase 4E3 parser_build mismatch")
            file_ids = set()
            for item in derived["files"]:
                file_id = digest_id("sf", identity["snapshot_id"], item["relative_path"])
                file_ids.add(file_id)
                row = conn.execute("SELECT snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path FROM source_file WHERE id=?", (file_id,)).fetchone()
                wanted = (identity["snapshot_id"], item["relative_path"], item["blob_oid"], item["sha256"], item["size_bytes"], item["format"], str(Path(source_root).resolve()))
                if row is None or tuple(row) != wanted:
                    _fail(f"World Archive Phase 4E3 source_file mismatch: {item['relative_path']}")
            expected_source_record_ids = set()
            for item in derived["source_records"]:
                file_id = digest_id("sf", identity["snapshot_id"], item["relative_path"])
                record_id = digest_id("sr", file_id, item["source_pointer"])
                expected_source_record_ids.add(record_id)
                row = conn.execute("SELECT source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json FROM source_record WHERE id=?", (record_id,)).fetchone()
                wanted = (file_id, item["source_pointer"], item["record_kind"], item["native_id"], item["raw_checksum"], canonical_json(item["raw_value"]))
                if row is None or tuple(row) != wanted:
                    _fail("World Archive Phase 4E3 source_record mismatch")
            actual_source_record_ids = {str(row[0]) for row in conn.execute("SELECT id FROM source_record WHERE source_file_id IN ({})".format(",".join("?" for _ in file_ids)), tuple(file_ids))}
            if not expected_source_record_ids.issubset(actual_source_record_ids):
                _fail("World Archive Phase 4E3 selected source_record set is incomplete")
            extra_source_record_ids = actual_source_record_ids - expected_source_record_ids
            if extra_source_record_ids:
                placeholders = ",".join("?" for _ in extra_source_record_ids)
                extra_paths = {str(row[0]) for row in conn.execute(
                    "SELECT DISTINCT sf.relative_path FROM source_record sr "
                    "JOIN source_file sf ON sf.id=sr.source_file_id "
                    f"WHERE sr.id IN ({placeholders})", tuple(extra_source_record_ids),
                )}
                if not extra_paths or any(not path.startswith("TextMap/") for path in extra_paths):
                    _fail("World Archive Phase 4E3 non-TextMap source_record pollution")
            ident = {"snapshot_id": identity["snapshot_id"], "parser_build_id": identity["parser_build_id"], "ingest_run_id": identity["ingest_run_id"]}
            expected_ids = {
                "world_archive_row_occurrence": {occurrence_id("row", ident, x["table"], x["row_index"], x["pointer"]) for x in derived["rows"]},
                "world_archive_raw_field_occurrence": {occurrence_id("raw-field", ident, x["table"], x["row_index"], x["source_pointer"]) for x in derived["raw_fields"]},
                "world_archive_relationship_occurrence": {occurrence_id("relationship", ident, x["table"], x["row_index"], x["source_pointer"]) for x in derived["relationships"]},
                "world_archive_opaque_occurrence": {occurrence_id("opaque", ident, x["table"], x["row_index"], x["source_pointer"]) for x in derived["opaque"]},
                "world_archive_text_owner_occurrence": {occurrence_id("text-owner", ident, x["owner_key"]) for x in derived["text_owners"]},
                "world_archive_text_resolution_occurrence": {occurrence_id("text-resolution", ident, x["owner_key"], x["locale"]) for x in derived["text_resolutions"]},
                "world_archive_text_candidate_occurrence": {occurrence_id("text-candidate", ident, x["owner_key"], x["locale"], x["shard_kind"], x["source_pointer"]) for x in derived["text_candidates"]},
                "world_archive_asset_reference_occurrence": {occurrence_id("asset", ident, x["table"], x["row_index"], x["source_pointer"]) for x in derived["assets"]},
            }
            for table, wanted_ids in expected_ids.items():
                rows = conn.execute(f"SELECT id,ingest_run_id,snapshot_id,parser_build_id FROM {table} WHERE ingest_run_id=?", (identity["ingest_run_id"],)).fetchall()
                if {str(row[0]) for row in rows} != wanted_ids or any(tuple(row[1:]) != (identity["ingest_run_id"], identity["snapshot_id"], identity["parser_build_id"]) for row in rows):
                    _fail(f"World Archive Phase 4E3 physical occurrence set mismatch: {table}")
            for table, column, wanted in (
                ("world_archive_relationship_occurrence", "resolution_status", derived["status_counts"]["relationships"]),
                ("world_archive_opaque_occurrence", "resolution_status", derived["status_counts"]["opaque"]),
                ("world_archive_text_resolution_occurrence", "resolution_status", derived["status_counts"]["text_resolutions"]),
                ("world_archive_asset_reference_occurrence", "reference_status", derived["status_counts"]["assets"]),
            ):
                actual = dict(sorted(Counter(str(row[0]) for row in conn.execute(f"SELECT {column} FROM {table} WHERE ingest_run_id=?", (identity["ingest_run_id"],))).items()))
                if actual != wanted:
                    _fail(f"World Archive Phase 4E3 status matrix mismatch: {table}")
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                _fail("World Archive Phase 4E3 foreign key check failed")
            for table, (columns, wanted_rows) in exact_occurrence_tables(derived, identity).items():
                actual = [tuple(row) for row in conn.execute(f"SELECT {','.join(columns)} FROM {table} WHERE ingest_run_id=? ORDER BY id", (identity["ingest_run_id"],))]
                if actual != sorted(wanted_rows, key=lambda row: str(row[0])):
                    _fail(f"World Archive Phase 4E3 exact business tuple mismatch: {table}")
    except WorldArchivePhase4E3VerificationError:
        raise
    except sqlite3.Error as exc:
        _fail(f"World Archive Phase 4E3 read-only verification failed: {exc}")
    after = _fingerprint(path)
    if after != before:
        _fail("World Archive Phase 4E3 verifier modified database")
    result = {
        "status": "pass",
        "oracle": "frozen_phase4e1_source_coordinate_and_exact_sql_v1",
        "schema_version": verified_schema_version,
        "family": FAMILY,
        "family_contract_version": FAMILY_CONTRACT_VERSION,
        "coverage": EXPECTED_COVERAGE,
        "corpus_complete": True,
        "ingest_run_id": identity["ingest_run_id"],
        "counts": derived["counts"],
        "status_counts": derived["status_counts"],
        "database_fingerprint": after,
    }
    if receipt_path is not None:
        receipt = release_receipt(manifest, after, schema_version=verified_schema_version or 0)
        write_release_receipt_atomically(
            receipt_path, path, report_path, receipt
        )
        result["release_receipt"] = {
            "path": str(Path(receipt_path).resolve()),
            "receipt_contract": receipt["receipt_contract"],
            "receipt_version": receipt["receipt_version"],
            "database": receipt["database"],
        }
    return result


__all__ = ["WorldArchivePhase4E3VerificationError", "verify"]
