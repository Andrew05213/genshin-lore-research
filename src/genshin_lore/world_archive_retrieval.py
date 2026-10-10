"""Family-specific read-only Repository for sealed World Archive evidence."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from ._readonly_database import ReadonlyDatabase
from .family_evidence import FamilyEvidenceResult
from .util import canonical_json, digest_id, text_occurrences
from .world_archive_phase4e2_schema import verify_schema_12
from .quest_codex_phase4f2_schema import verify_schema_13
from .batch_m4_runtime_oracle import verify_schema16
from .batch_m5d_runtime import BatchM5DError, _verify_schema17 as verify_schema17
from .db import validate_exact_schema_structure
from .world_archive_phase4e3_contract import (
    EXPECTED_BUILD,
    EXPECTED_COMMIT,
    EXPECTED_COVERAGE,
    EXPECTED_MANIFEST_SHA256,
    EXPECTED_OBSERVED_AT,
    EXPECTED_TREE,
    FAMILY,
    FAMILY_CONTRACT_VERSION,
    MANIFEST_ID,
    RUN_FAMILY,
    VERSIONS,
    expected_identity,
    load_manifest,
)
from .world_archive_phase4e3_receipt import (
    RECEIPT_CONTRACT,
    RECEIPT_VERSION,
    SCHEMA16_RECEIPT_SCHEMA_CONTRACT,
    SCHEMA17_RECEIPT_SCHEMA_CONTRACT,
    SCHEMA19_RECEIPT_SCHEMA_CONTRACT,
    SCHEMA20_RECEIPT_SCHEMA_CONTRACT,
    WorldArchiveReleaseReceiptError,
    load_release_receipt,
)
from .world_archive_phase4e3_runtime_seal import (
    database_identity,
    scan_runtime_business_seals,
)
from .runtime_stability import RuntimeStabilitySnapshot, stable_routed_call


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "manifests/world_archive/anime-game-data-7.0-phase4e3-world-archive-full.json"
TABLES = ("AnimalCodexExcelConfigData", "ViewCodexExcelConfigData")
TABLE_ROW_COUNTS = {"AnimalCodexExcelConfigData": 584, "ViewCodexExcelConfigData": 268}
LOCALES = ("CHS", "EN", "JP")
RELATION_STATUSES = ("resolved_unique", "missing", "dangling", "ambiguous", "unresolved", "invalid")
TEXT_STATUSES = ("unique_main", "unique_medium", "both_equal", "both_conflict", "missing", "invalid")
ASSET_STATUSES = ("observed", "missing", "invalid")
LIST_FILTERS = ("source_table", "native_id", "relationship_status", "text_resolution_status", "asset_reference_status")
SEARCH_FILTERS = ("source_table", "native_id", "locale", "field_role", "resolution_status")


def _json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(str(value))


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ValueError("limit must be between 1 and 100")
    return value


@lru_cache(maxsize=1)
def _manifest() -> dict[str, Any]:
    return load_manifest(MANIFEST_PATH)


def _expected_scope(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "family": RUN_FAMILY,
        "provider_family": FAMILY,
        "family_contract_version": FAMILY_CONTRACT_VERSION,
        "manifest_id": manifest["manifest_id"],
        "snapshot_id": manifest["source"]["snapshot_id"],
        "coverage": EXPECTED_COVERAGE,
        "corpus_complete": True,
        "selected_rows": manifest["boundary"]["selected_rows"],
        "closure_counts": manifest["expected"]["counts"],
        "occurrence_set_digests": manifest["expected"]["occurrence_set_digests"],
        "full_world_archive_scan": True,
        "adjacent_inventory_only": True,
        "logical_identity_created": False,
        "mcp_provider_added": False,
        "exact_ingest_run_required": True,
    }


def _expected_config(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "manifest_raw_sha256": EXPECTED_MANIFEST_SHA256,
        "versions": manifest["versions"],
        "source_commit": manifest["source"]["commit"],
        "source_tree": manifest["source"]["tree"],
        "coverage": EXPECTED_COVERAGE,
        "corpus_complete": True,
        "schema_contract": "schema12-migration0012-exact",
        "occurrence_set_digests": manifest["expected"]["occurrence_set_digests"],
        "logical_identity_created": False,
        "mcp_provider_added": False,
    }


@dataclass(frozen=True)
class WorldArchiveCapabilitySnapshot:
    schema_supported: bool
    _eligible_runs_json: str = "[]"
    unavailable_reason: str | None = None
    _stability_json: str = ""


    @property
    def runtime_stability(self) -> RuntimeStabilitySnapshot | None:
        if not self._stability_json:
            return None
        try:
            return RuntimeStabilitySnapshot.from_json(json.loads(self._stability_json))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    @property
    def eligible_runs(self) -> list[dict[str, Any]]:
        value = json.loads(self._eligible_runs_json)
        return value if isinstance(value, list) else []

    @property
    def evidence_available(self) -> bool:
        return bool(self.eligible_runs)
    @property
    def uses_shared_r2_qualification(self) -> bool:
        return any(item.get("qualification_source") in {"shared_exact_r2_release_bundle", "shared_exact_r4_release_bundle"}
                   for item in self.eligible_runs)


    def to_dict(self) -> dict[str, Any]:
        return {
            "family_contract_version": FAMILY_CONTRACT_VERSION,
            "schema_supported": self.schema_supported,
            "evidence_available": self.evidence_available,
            "unavailable_reason": self.unavailable_reason,
            "eligible_runs": self.eligible_runs,
            "supported_operations": [
                "list_family_evidence", "get_family_evidence", "search_family_text"
            ],
            "supported_filters": {
                "list_family_evidence": list(LIST_FILTERS),
                "get_family_evidence": [],
                "search_family_text": list(SEARCH_FILTERS),
            },
            "pagination_contract": {
                "kind": "deterministic_keyset",
                "cursor": "opaque_world_archive_cursor_v1",
                "limit": {"default": 50, "minimum": 1, "maximum": 100},
                "scope_binding": ["family", "ingest_run_id", "family_contract_version", "operation", "filters", "query"],
            },
            "coverage_semantics": {
                "coverage": EXPECTED_COVERAGE,
                "corpus_complete": "true only for the exact verified eight-file 852-row Phase 4E3 closure",
                "adjacent_inventory_included": False,
                "full_7_0_claimed": False,
            },
            "search_contract": {
                "query": "required_nonempty_literal_containing_at_least_one_non_whitespace_character",
                "matching": "case_sensitive_literal_instr_on_resolution_raw_text",
            },
            "selection": {
                "ingest_run_id": "required",
                "explicit_ingest_run_required": True,
                "default_run": False,
                "latest_run": False,
                "sibling_fallback": False,
            },
    "release_qualification": {
                "receipt_required": True,
                "receipt_contract": RECEIPT_CONTRACT,
                "receipt_version": RECEIPT_VERSION,
                "receipt_path": "explicit_server_configuration_only",
                "startup_checks": ([self.eligible_runs[0]["qualification_source"], "exact_accepted_database_seal", "release_evidence_hashes", "database_identity_and_sidecar_stability"] if self.uses_shared_r2_qualification else [
                    "receipt_exact_contract",
                    "database_size_and_streaming_sha256",
                    "schema_structural_seal_bound_to_receipt_contract",
                    "schema16_additive_migration0016_structural_seal_when_selected",
                    "schema17_continuous_ledger_and_migration0017_structural_seal_when_selected",
                    "schema19_continuous_ledger_and_migrations0018_0019_structural_seal_when_selected",
                    "exact_release_run_metadata",
                    "tracked_manifest_business_rowset_digests",
                    "foreign_key_check",
                    "database_identity_and_sidecar_stability",
                ]),
                "receipt_trust": "file_prefilter_not_authentication",
                "schema_contracts": [
                    "schema12-migration0012-exact",
                    "schema13-migration0013-exact",
                    SCHEMA16_RECEIPT_SCHEMA_CONTRACT,
                    SCHEMA17_RECEIPT_SCHEMA_CONTRACT,
                    SCHEMA19_RECEIPT_SCHEMA_CONTRACT,
                    SCHEMA20_RECEIPT_SCHEMA_CONTRACT,
                ],
                "schema16_additive_compatibility": True,
                "schema17_additive_compatibility": True,
                "schema19_additive_compatibility": True,
                "schema20_additive_compatibility": True,
                "occurrence_tables_scanned_at_startup": not self.uses_shared_r2_qualification,
                "business_digest_scope": "all_business_columns_in_eight_world_archive_occurrence_tables_plus_selected_source_catalog_snapshot_files_records",
                "repeated_capability_rehash": False,
                "repeated_capability_redigest": False,
                "call_time_stability": {
                    "database": "identity_only_no_rehash",
                    "receipt": "identity_and_streaming_sha256",
                    "sidecars": "must_remain_absent",
                    "entry_and_exit_checks": True,
                },
            },
            "evidence_only": True,
            "data_contract": "world-archive-evidence-v1",
        }


def _schema12(conn: sqlite3.Connection) -> bool:
    try:
        verify_schema_12(conn)
        return True
    except (sqlite3.Error, RuntimeError, TypeError, ValueError):
        return False


def _schema13(conn: sqlite3.Connection) -> bool:
    try:
        verify_schema_13(conn)
        return True
    except (sqlite3.Error, RuntimeError, TypeError, ValueError):
        return False


def _schema16(conn: sqlite3.Connection) -> bool:
    try:
        verify_schema16(conn)
        return True
    except (sqlite3.Error, RuntimeError, TypeError, ValueError):
        return False


def _schema17(conn: sqlite3.Connection) -> bool:
    try:
        verify_schema17(conn)
        return True
    except (BatchM5DError, OSError, sqlite3.Error, RuntimeError, TypeError, ValueError):
        return False


def _schema19(conn: sqlite3.Connection) -> bool:
    try:
        validate_exact_schema_structure(conn, 19)
        return True
    except (OSError, sqlite3.Error, RuntimeError, TypeError, ValueError):
        return False


def _schema_supported(conn: sqlite3.Connection, expected_schema: int | None = None) -> bool:
    if expected_schema == 12:
        return _schema12(conn)
    if expected_schema == 13:
        return _schema13(conn)
    if expected_schema == 16:
        return _schema16(conn)
    if expected_schema == 17:
        return _schema17(conn)
    if expected_schema == 19:
        return _schema19(conn)
    return _schema12(conn) or _schema13(conn) or _schema16(conn) or _schema17(conn) or _schema19(conn)


def _database_fingerprint(path: Path) -> dict[str, Any]:
    before = database_identity(path)
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != before:
            raise OSError("database identity changed before hashing")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        final = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after = database_identity(path)
    if after != before or (
        final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns
    ) != before:
        raise OSError("database changed while hashing")
    return {"size_bytes": before[2], "sha256": digest.hexdigest()}


def _runtime_business_seals_match(
    actual: Mapping[str, Any], expected: Mapping[str, Any],
    *, accepted_combined_snapshot_seal: Mapping[str, Any] | None = None,
) -> bool:
    actual_value = dict(actual)
    snapshot = actual_value.pop("source_snapshot", None)
    expected_value = dict(expected)
    allowed = expected_value.pop("source_snapshot_allowed", None)
    if isinstance(allowed, list) and accepted_combined_snapshot_seal is not None:
        allowed = [*allowed, dict(accepted_combined_snapshot_seal)]
    return (
        isinstance(allowed, list)
        and snapshot in allowed
        and actual_value == expected_value
    )


def inspect_world_archive_capability(
    db: ReadonlyDatabase, receipt_path: str | Path | None = None
) -> WorldArchiveCapabilitySnapshot:
    """Qualify once from a verifier receipt and cache the immutable result."""
    if receipt_path is None:
        return WorldArchiveCapabilitySnapshot(
            _schema_supported(db.conn), unavailable_reason="release_receipt_missing"
        )
    try:
        receipt = load_release_receipt(receipt_path, MANIFEST_PATH)
        schema_contract = receipt["verification"]["schema_contract"]
        expected_schema = {
            "schema12-migration0012-exact": 12,
            "schema13-migration0013-exact": 13,
            SCHEMA16_RECEIPT_SCHEMA_CONTRACT: 16,
            SCHEMA17_RECEIPT_SCHEMA_CONTRACT: 17,
            SCHEMA19_RECEIPT_SCHEMA_CONTRACT: 19,
        }.get(schema_contract)
        if expected_schema is None:
            return WorldArchiveCapabilitySnapshot(
                _schema_supported(db.conn), unavailable_reason="release_qualification_failed"
            )
        identity_before = database_identity(db.path)
        if db.file_identity != identity_before:
            return WorldArchiveCapabilitySnapshot(
                _schema_supported(db.conn, expected_schema), unavailable_reason="database_changed_before_qualification"
            )
        if _database_fingerprint(db.path) != receipt["database"]:
            return WorldArchiveCapabilitySnapshot(
                _schema_supported(db.conn, expected_schema), unavailable_reason="database_fingerprint_mismatch"
            )
        if not _schema_supported(db.conn, expected_schema):
            return WorldArchiveCapabilitySnapshot(
                False, unavailable_reason=f"schema{expected_schema}_structural_seal_mismatch"
            )
        manifest = _manifest()
        identity = expected_identity(manifest)
        row = db.conn.execute(
            """SELECT ir.*, pb.parser_version, pb.mapping_version, pb.fingerprint,
                      pb.config_checksum, pb.created_at AS parser_created_at,
                      ss.source_id, ss.release_label, ss.build, ss.\"commit\" AS source_commit,
                      ss.tree AS source_tree, ss.observed_at, ss.status AS snapshot_status,
                      ss.parser_version AS snapshot_parser_version,
                      ss.mapping_version AS snapshot_mapping_version,
                      ss.metadata_json AS snapshot_metadata, sc.kind AS source_kind, sc.url,
                      sc.license_note
                 FROM ingest_run ir JOIN parser_build pb ON pb.id=ir.parser_build_id
                 JOIN source_snapshot ss ON ss.id=ir.snapshot_id
                 JOIN source_catalog sc ON sc.id=ss.source_id WHERE ir.id=?""",
            (identity["ingest_run_id"],),
        ).fetchone()
        if row is None:
            return WorldArchiveCapabilitySnapshot(
                True, unavailable_reason="exact_release_run_metadata_mismatch"
            )
        scope, config = _json(row["scope_json"]), _json(row["config_json"])
        same_family = []
        for item in db.conn.execute("SELECT id,scope_json FROM ingest_run ORDER BY id"):
            value = _json(item["scope_json"], {})
            if isinstance(value, dict) and value.get("family") == RUN_FAMILY:
                same_family.append(str(item["id"]))
        exact = (
            row["id"] == identity["ingest_run_id"]
            and row["snapshot_id"] == identity["snapshot_id"]
            and row["parser_build_id"] == identity["parser_build_id"]
            and row["status"] == "complete" and row["created_at"] == EXPECTED_OBSERVED_AT
            and scope == _expected_scope(manifest) and config == _expected_config(manifest)
            and row["parser_version"] == VERSIONS["parser"]
            and row["mapping_version"] == VERSIONS["projection"]
            and row["fingerprint"] == "world-archive-phase4e3-sealed-full"
            and row["config_checksum"] == identity["config_checksum"]
            and row["parser_created_at"] == EXPECTED_OBSERVED_AT
            and row["source_id"] == "anime-game-data" and row["release_label"] == "7.0"
            and row["build"] == EXPECTED_BUILD and row["source_commit"] == EXPECTED_COMMIT
            and row["source_tree"] == EXPECTED_TREE and row["observed_at"] == EXPECTED_OBSERVED_AT
            and row["snapshot_status"] == "active"
            and row["snapshot_parser_version"] == "legacy-unscoped"
            and row["snapshot_mapping_version"] == "legacy-unscoped"
            and row["source_kind"] == "primary"
            and row["url"] == "https://github.com/DimbreathBot/AnimeGameData"
            and row["license_note"] is None
            and _json(row["snapshot_metadata"]) in (
                {"sealed_source_manifest": True},
                {"sealed_source_manifest": True, "phase4e2_world_archive_canary": True},
                {"sealed_source_manifest": True, "phase4e3_world_archive_full": True},
                {"phase4d2b_full_boundary": True, "sealed_source_manifest": True},
            )
            and same_family == [identity["ingest_run_id"]]
        )
        if not exact:
            return WorldArchiveCapabilitySnapshot(
                True, unavailable_reason="exact_release_run_metadata_mismatch"
            )
        selected_file_ids = sorted(
            digest_id("sf", identity["snapshot_id"], item["relative_path"])
            for item in manifest["files"]
        )
        selected_record_ids = sorted({
            str(row[0])
            for table in (
                "world_archive_row_occurrence", "world_archive_raw_field_occurrence",
                "world_archive_relationship_occurrence", "world_archive_opaque_occurrence",
                "world_archive_text_owner_occurrence", "world_archive_text_resolution_occurrence",
                "world_archive_text_candidate_occurrence", "world_archive_asset_reference_occurrence",
            )
            for row in db.conn.execute(
                f"SELECT source_record_id FROM {table} WHERE ingest_run_id=?",
                (identity["ingest_run_id"],),
            )
        })
        actual_seals = scan_runtime_business_seals(
            db.conn,
            ingest_run_id=identity["ingest_run_id"],
            snapshot_id=identity["snapshot_id"],
            source_id=manifest["source"]["source_id"],
            selected_source_file_ids=selected_file_ids,
            selected_source_record_ids=selected_record_ids,
        )
        business_seals_match = _runtime_business_seals_match(
            actual_seals, manifest["expected"]["runtime_business_seals"],
            accepted_combined_snapshot_seal=(
                actual_seals["source_snapshot"]
                if _json(row["snapshot_metadata"]) == {
                    "phase4d2b_full_boundary": True, "sealed_source_manifest": True,
                }
                else None
            ),
        )
        foreign_keys_clean = not db.conn.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if database_identity(db.path) != identity_before:
            return WorldArchiveCapabilitySnapshot(
                True, unavailable_reason="database_changed_during_qualification"
            )
        if not business_seals_match:
            return WorldArchiveCapabilitySnapshot(
                True, unavailable_reason="runtime_business_digest_mismatch"
            )
        if not foreign_keys_clean:
            return WorldArchiveCapabilitySnapshot(
                True, unavailable_reason="foreign_key_check_failed"
            )
        binding = {
            "schema_version": expected_schema,
            "receipt_schema_contract": schema_contract,
            "ingest_run_id": identity["ingest_run_id"],
            "snapshot_id": identity["snapshot_id"],
            "parser_build_id": identity["parser_build_id"],
            "manifest_id": MANIFEST_ID,
            "manifest_raw_sha256": EXPECTED_MANIFEST_SHA256,
            "coverage": EXPECTED_COVERAGE,
            "corpus_complete": True,
            "selected_primary_rows": manifest["expected"]["counts"]["rows"],
            "selected_files": manifest["expected"]["counts"]["files"],
            "release_receipt_contract": RECEIPT_CONTRACT,
            "release_receipt_version": RECEIPT_VERSION,
            "schema_contract": schema_contract,
            "database_sha256": receipt["database"]["sha256"],
        }
        stability = RuntimeStabilitySnapshot.capture(db.path, receipt_path)
        return WorldArchiveCapabilitySnapshot(
            True, canonical_json([binding]), None,
            canonical_json(stability.to_json()),
        )
    except (
        OSError, RuntimeError, sqlite3.Error, TypeError, ValueError, KeyError,
        json.JSONDecodeError, WorldArchiveReleaseReceiptError,
    ):
        return WorldArchiveCapabilitySnapshot(
            _schema_supported(db.conn), unavailable_reason="release_qualification_failed"
        )


def qualify_world_archive_r2_compatibility(
    db: ReadonlyDatabase, receipt_path: str | Path | None, qualification: Any,
) -> WorldArchiveCapabilitySnapshot:
    """Derive Schema19 compatibility from one already exact R2 qualification."""
    if receipt_path is None:
        return WorldArchiveCapabilitySnapshot(True, unavailable_reason="release_receipt_missing")
    try:
        qualification.check()
        manifest = load_manifest(MANIFEST_PATH)
        receipt = load_release_receipt(receipt_path, MANIFEST_PATH)
        if receipt["verification"]["schema_contract"] != qualification.schema_contract:
            raise WorldArchiveReleaseReceiptError("Schema19 receipt required")
        expected_database = {"size_bytes": qualification.candidate_size_bytes, "sha256": qualification.candidate_sha256}
        if receipt["database"] != expected_database:
            raise WorldArchiveReleaseReceiptError("R2 database binding mismatch")
        identity = expected_identity(manifest)
        binding = {
            "schema_version": qualification.schema_version, "receipt_schema_contract": qualification.schema_contract,
            "ingest_run_id": identity["ingest_run_id"], "snapshot_id": identity["snapshot_id"],
            "parser_build_id": identity["parser_build_id"], "manifest_id": MANIFEST_ID,
            "manifest_raw_sha256": EXPECTED_MANIFEST_SHA256, "coverage": EXPECTED_COVERAGE,
            "corpus_complete": True, "selected_primary_rows": manifest["expected"]["counts"]["rows"],
            "selected_files": manifest["expected"]["counts"]["files"],
            "release_receipt_contract": RECEIPT_CONTRACT, "release_receipt_version": RECEIPT_VERSION,
            "schema_contract": qualification.schema_contract, "database_sha256": qualification.candidate_sha256,
            "qualification_source": ("shared_exact_r4_release_bundle" if qualification.schema_version == 20 else "shared_exact_r2_release_bundle"),
        }
        stability = RuntimeStabilitySnapshot.capture(db.path, receipt_path)
        qualification.check()
        return WorldArchiveCapabilitySnapshot(True, canonical_json([binding]), None, canonical_json(stability.to_json()))
    except (OSError, RuntimeError, TypeError, ValueError, KeyError, WorldArchiveReleaseReceiptError):
        return WorldArchiveCapabilitySnapshot(True, unavailable_reason="r2_compatibility_qualification_failed")


def _cursor(scope: dict[str, Any], last: list[Any]) -> str:
    body = {"v": 1, "scope": scope, "last": last}
    raw_body = canonical_json(body)
    payload = {**body, "check": hashlib.sha256(raw_body.encode()).hexdigest()}
    return base64.urlsafe_b64encode(canonical_json(payload).encode()).decode().rstrip("=")


def _decode_cursor(value: str | None, scope: dict[str, Any], kind: str) -> list[Any] | None:
    if value is None:
        return None
    _text(value, "cursor")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        payload = json.loads(raw.decode())
        body = {key: payload[key] for key in ("v", "scope", "last")}
        valid = payload.get("check") == hashlib.sha256(canonical_json(body).encode()).hexdigest()
        last = body["last"]
        if (not isinstance(body["v"], int) or isinstance(body["v"], bool)
                or body["v"] != 1 or body["scope"] != scope
                or not isinstance(last, list) or not valid):
            raise ValueError
        if kind == "list":
            good = (len(last) == 3 and isinstance(last[0], str) and last[0] in TABLES
                    and isinstance(last[1], int) and not isinstance(last[1], bool)
                    and 0 <= last[1] < TABLE_ROW_COUNTS[last[0]]
                    and isinstance(last[2], str) and bool(last[2]))
        elif kind == "search":
            good = (len(last) == 5 and isinstance(last[0], str) and last[0] in LOCALES
                    and isinstance(last[1], str) and last[1] in TABLES
                    and isinstance(last[2], int) and not isinstance(last[2], bool)
                    and 0 <= last[2] < TABLE_ROW_COUNTS[last[1]]
                    and isinstance(last[3], str) and last[3].startswith("/")
                    and isinstance(last[4], str) and bool(last[4]))
        else:
            good = False
        if not good:
            raise ValueError
        return last
    except (binascii.Error, KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("cursor is invalid or belongs to another evidence scope") from exc


def _filters(value: Any, allowed: tuple[str, ...]) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("filters must be an object")
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ValueError(f"unsupported filters: {', '.join(unknown)}")
    result: dict[str, str] = {}
    for key, item in value.items():
        result[key] = _text(item, f"filters.{key}")
    if result.get("source_table") not in (None, *TABLES):
        raise ValueError("filters.source_table is not supported by world-archive-evidence-v1")
    checks = (("relationship_status", RELATION_STATUSES), ("text_resolution_status", TEXT_STATUSES),
              ("asset_reference_status", ASSET_STATUSES), ("resolution_status", TEXT_STATUSES),
              ("locale", LOCALES), ("field_role", ("description", "name")))
    for key, choices in checks:
        if key in result and result[key] not in choices:
            raise ValueError(f"filters.{key} has an unsupported value")
    return result


def _source(db: ReadonlyDatabase, source_record_id: str) -> dict[str, Any] | None:
    row = db.conn.execute(
        """SELECT sr.*, sf.relative_path, sf.sha256 AS file_sha256,
                  sf.upstream_blob_oid, sf.format, sf.root_path
             FROM source_record sr JOIN source_file sf
                    ON sf.id=sr.source_file_id WHERE sr.id=?""", (source_record_id,)
    ).fetchone()
    if row is None:
        return None
    return {"source_record_id": row["id"], "source_file_id": row["source_file_id"],
            "relative_path": row["relative_path"], "source_pointer": row["pointer"],
            "record_kind": row["record_kind"], "native_id": row["native_id"],
            "raw_checksum": row["raw_sha256"], "raw_payload": _json(row["raw_json"]),
            "source_file": {"sha256": row["file_sha256"],
                            "upstream_blob_oid": row["upstream_blob_oid"],
                            "format": row["format"], "root_path": row["root_path"]}}


def _row_item(db: ReadonlyDatabase, row: Mapping[str, Any], provenance: bool = False) -> dict[str, Any]:
    item = {"occurrence_id": row["id"], "source_table": row["table_name"],
            "row_index": row["row_index"], "native_locator": {"field": row["native_locator_field"], "value": row["native_locator_value"]},
            "source_pointer": row["source_pointer"], "source_record_id": row["source_record_id"],
            "raw_checksum": row["raw_checksum"], "metadata": _json(row["metadata_json"], {})}
    if provenance:
        item["provenance"] = _source(db, str(row["source_record_id"]))
    return item


def _result(status: str, run: str, data: dict[str, Any], *, eligible: bool = True) -> FamilyEvidenceResult:
    return FamilyEvidenceResult(status, FAMILY, run, FAMILY_CONTRACT_VERSION,
                                EXPECTED_COVERAGE if eligible else "unavailable",
                                eligible, data)


class WorldArchiveRepository:
    family = FAMILY
    family_contract_version = FAMILY_CONTRACT_VERSION

    def __init__(self, db: ReadonlyDatabase, capability: WorldArchiveCapabilitySnapshot | None = None) -> None:
        self.db = db
        self._capability = capability or inspect_world_archive_capability(db)
        self._eligible = frozenset(str(item["ingest_run_id"]) for item in self._capability.eligible_runs)
        self._stability = self._capability.runtime_stability

    def capability(self) -> dict[str, Any]:
        return self._capability.to_dict()

    def _run(self, run: Any, operation: str) -> FamilyEvidenceResult | None:
        requested = _text(run, "ingest_run_id")
        if requested in self._eligible:
            return None
        exists = self.db.conn.execute("SELECT 1 FROM ingest_run WHERE id=?", (requested,)).fetchone() is not None
        return _result("not_found", requested,
                       {"status": "NotFound", "reason": "ingest_run_ineligible" if exists else "ingest_run_not_found",
                        "lookup": {"operation": operation, "ingest_run_id": requested}}, eligible=False)

    @staticmethod
    def _contract(run: str, version: Any, operation: str) -> FamilyEvidenceResult | None:
        if version == FAMILY_CONTRACT_VERSION:
            return None
        return _result(
            "not_found", run,
            {"status": "NotFound", "reason": "family_contract_version_mismatch",
             "lookup": {"operation": operation, "requested": version,
                        "supported": FAMILY_CONTRACT_VERSION}},
            eligible=False,
        )

    @stable_routed_call("list_family_evidence")
    def list_evidence(self, *, ingest_run_id: str, family_contract_version: str,
                      filters: dict[str, Any] | None = None, cursor: str | None = None,
                      limit: int = 50) -> FamilyEvidenceResult:
        error = self._contract(ingest_run_id, family_contract_version, "list_family_evidence")
        if error: return error
        error = self._run(ingest_run_id, "list_family_evidence")
        if error: return error
        limit, selected = _limit(limit), _filters(filters, LIST_FILTERS)
        scope = {"family": FAMILY, "run": ingest_run_id, "contract": family_contract_version,
                 "operation": "list", "filters": selected, "query": None}
        last = _decode_cursor(cursor, scope, "list")
        where, params = ["r.ingest_run_id=?"], [ingest_run_id]
        if "source_table" in selected: where.append("r.table_name=?"); params.append(selected["source_table"])
        if "native_id" in selected: where.append("r.native_locator_value=?"); params.append(selected["native_id"])
        status_tables = (("relationship_status", "world_archive_relationship_occurrence", "source_row_occurrence_id", "resolution_status"),
                         ("text_resolution_status", "world_archive_text_resolution_occurrence", "source_row_occurrence_id", "resolution_status"),
                         ("asset_reference_status", "world_archive_asset_reference_occurrence", "source_row_occurrence_id", "reference_status"))
        for key, table, fk, column in status_tables:
            if key in selected:
                where.append(f"EXISTS (SELECT 1 FROM {table} x WHERE x.ingest_run_id=r.ingest_run_id AND x.{fk}=r.id AND x.{column}=?)")
                params.append(selected[key])
        if last is not None:
            where.append("(r.table_name>? OR (r.table_name=? AND (r.row_index>? OR (r.row_index=? AND r.id>?))))")
            params.extend([last[0], last[0], last[1], last[1], last[2]])
        rows = self.db.conn.execute(f"SELECT r.* FROM world_archive_row_occurrence r WHERE {' AND '.join(where)} ORDER BY r.table_name,r.row_index,r.id LIMIT ?", (*params, limit + 1)).fetchall()
        page, more = rows[:limit], len(rows) > limit
        next_cursor = _cursor(scope, [page[-1]["table_name"], page[-1]["row_index"], page[-1]["id"]]) if more and page else None
        return _result("ok", ingest_run_id, {"operation": "list", "filters": selected,
            "items": [_row_item(self.db, row) for row in page],
            "pagination": {"kind": "keyset", "limit": limit, "next_cursor": next_cursor}})

    @stable_routed_call("get_family_evidence")
    def get_evidence(self, *, ingest_run_id: str, family_contract_version: str,
                     occurrence_id: str) -> FamilyEvidenceResult:
        error = self._contract(ingest_run_id, family_contract_version, "get_family_evidence")
        if error: return error
        error = self._run(ingest_run_id, "get_family_evidence")
        if error: return error
        requested = _text(occurrence_id, "occurrence_id")
        row = self.db.conn.execute("SELECT * FROM world_archive_row_occurrence WHERE ingest_run_id=? AND id=?", (ingest_run_id, requested)).fetchone()
        if row is None:
            return _result("not_found", ingest_run_id, {"status": "NotFound", "reason": "occurrence_not_found", "lookup": {"operation": "get", "occurrence_id": requested}})
        children: dict[str, Any] = {}
        specs = {
            "raw_fields": ("world_archive_raw_field_occurrence", "row_occurrence_id", "source_pointer,id"),
            "relationships": ("world_archive_relationship_occurrence", "source_row_occurrence_id", "source_pointer,id"),
            "opaque": ("world_archive_opaque_occurrence", "source_row_occurrence_id", "source_pointer,id"),
            "assets": ("world_archive_asset_reference_occurrence", "source_row_occurrence_id", "source_pointer,id"),
        }
        for name, (table, field, order) in specs.items():
            values = self.db.conn.execute(f"SELECT * FROM {table} WHERE ingest_run_id=? AND {field}=? ORDER BY {order}", (ingest_run_id, requested)).fetchall()
            children[name] = [{key: (_json(item[key]) if key.endswith("_json") else item[key]) for key in item.keys() if key not in {"snapshot_id", "parser_build_id", "ingest_run_id", "source_record_id"}} for item in values]
        owners = self.db.conn.execute("SELECT * FROM world_archive_text_owner_occurrence WHERE ingest_run_id=? AND source_row_occurrence_id=? ORDER BY source_pointer,id", (ingest_run_id, requested)).fetchall()
        owner_items = []
        for owner in owners:
            resolutions = self.db.conn.execute("SELECT * FROM world_archive_text_resolution_occurrence WHERE ingest_run_id=? AND owner_occurrence_id=? ORDER BY locale,id", (ingest_run_id, owner["id"])).fetchall()
            resolution_items = []
            for resolution in resolutions:
                candidates = self.db.conn.execute("SELECT * FROM world_archive_text_candidate_occurrence WHERE ingest_run_id=? AND resolution_occurrence_id=? ORDER BY shard_kind,source_pointer,id", (ingest_run_id, resolution["id"])).fetchall()
                resolution_items.append({"occurrence_id": resolution["id"], "locale": resolution["locale"], "textmap_hash": resolution["textmap_hash"], "resolution_status": resolution["resolution_status"], "raw_text": resolution["raw_text"], "normalized_text": resolution["normalized_text"], "candidate_scopes": _json(resolution["candidate_scopes_json"], []), "candidates": [{"occurrence_id": c["id"], "source_file_id": c["source_file_id"], "source_record_id": c["source_record_id"], "source_pointer": c["source_pointer"], "shard_kind": c["shard_kind"], "locale": c["locale"], "raw_text": c["raw_text"], "normalized_text": c["normalized_text"], "raw_checksum": c["raw_checksum"]} for c in candidates]})
            owner_items.append({"occurrence_id": owner["id"], "source_pointer": owner["source_pointer"], "field_name": owner["field_name"], "field_role": owner["field_role"], "textmap_hash": owner["textmap_hash"], "resolutions": resolution_items})
        children["text_owners"] = owner_items
        return _result("ok", ingest_run_id, {"operation": "get", "container_scope": "single_physical_world_archive_row_occurrence", "occurrence": {"row": _row_item(self.db, row, provenance=True), **children}})

    @stable_routed_call("search_family_text")
    def search_text(self, *, ingest_run_id: str, family_contract_version: str,
                    query: str, filters: dict[str, Any] | None = None,
                    cursor: str | None = None, limit: int = 50) -> FamilyEvidenceResult:
        error = self._contract(ingest_run_id, family_contract_version, "search_family_text")
        if error: return error
        error = self._run(ingest_run_id, "search_family_text")
        if error: return error
        literal = _text(query, "query")
        if not literal.strip(): raise ValueError("query must contain a non-whitespace character")
        limit, selected = _limit(limit), _filters(filters, SEARCH_FILTERS)
        scope = {"family": FAMILY, "run": ingest_run_id, "contract": family_contract_version,
                 "operation": "search_text", "filters": selected, "query": literal}
        last = _decode_cursor(cursor, scope, "search")
        where, params = ["tr.ingest_run_id=?", "tr.raw_text IS NOT NULL", "instr(tr.raw_text,?)>0"], [ingest_run_id, literal]
        mapping = {"source_table": "r.table_name", "native_id": "r.native_locator_value", "locale": "tr.locale", "field_role": "o.field_role", "resolution_status": "tr.resolution_status"}
        for key, column in mapping.items():
            if key in selected: where.append(f"{column}=?"); params.append(selected[key])
        if last is not None:
            where.append("(tr.locale,r.table_name,r.row_index,tr.source_pointer,tr.id)>(?,?,?,?,?)"); params.extend(last)
        rows = self.db.conn.execute(f"""SELECT tr.*,o.field_name,o.field_role,r.table_name,r.row_index,
                    r.native_locator_field,r.native_locator_value,r.raw_checksum AS row_checksum,
                    r.metadata_json AS row_metadata,r.source_file_id,r.source_record_id AS row_record_id,
                    r.source_pointer AS row_pointer,r.id AS row_id
               FROM world_archive_text_resolution_occurrence tr
               JOIN world_archive_text_owner_occurrence o ON o.id=tr.owner_occurrence_id AND o.ingest_run_id=tr.ingest_run_id
               JOIN world_archive_row_occurrence r ON r.id=tr.source_row_occurrence_id AND r.ingest_run_id=tr.ingest_run_id
              WHERE {' AND '.join(where)} ORDER BY tr.locale,r.table_name,r.row_index,tr.source_pointer,tr.id LIMIT ?""", (*params, limit + 1)).fetchall()
        page, more, items = rows[:limit], len(rows) > limit, []
        for row in page:
            candidates = self.db.conn.execute("SELECT id,source_file_id,source_record_id,source_pointer,shard_kind,locale,raw_text,normalized_text,raw_checksum FROM world_archive_text_candidate_occurrence WHERE ingest_run_id=? AND resolution_occurrence_id=? ORDER BY shard_kind,source_pointer,id", (ingest_run_id, row["id"])).fetchall()
            items.append({"occurrence_id": row["id"], "owner_occurrence_id": row["owner_occurrence_id"], "source_pointer": row["source_pointer"], "field_name": row["field_name"], "field_role": row["field_role"], "locale": row["locale"], "textmap_hash": row["textmap_hash"], "resolution_status": row["resolution_status"], "raw_text": row["raw_text"], "normalized_text": row["normalized_text"], "matches": [{"start": a, "end": b} for a,b in text_occurrences(row["raw_text"], literal)], "candidate_scopes": _json(row["candidate_scopes_json"], []), "candidates": [dict(c) for c in candidates], "row": {"occurrence_id": row["row_id"], "source_table": row["table_name"], "row_index": row["row_index"], "native_locator": {"field": row["native_locator_field"], "value": row["native_locator_value"]}, "source_pointer": row["row_pointer"], "source_record_id": row["row_record_id"], "raw_checksum": row["row_checksum"], "metadata": _json(row["row_metadata"], {})}})
        next_cursor = _cursor(scope, [page[-1]["locale"],page[-1]["table_name"],page[-1]["row_index"],page[-1]["source_pointer"],page[-1]["id"]]) if more and page else None
        return _result("ok", ingest_run_id, {"operation": "search_text", "query": {"literal": literal, "semantics": "case_sensitive_literal_instr_on_resolution_raw_text"}, "filters": selected, "items": items, "pagination": {"kind": "keyset", "limit": limit, "next_cursor": next_cursor}})


__all__ = ["WorldArchiveCapabilitySnapshot", "WorldArchiveRepository", "inspect_world_archive_capability", "qualify_world_archive_r2_compatibility"]
