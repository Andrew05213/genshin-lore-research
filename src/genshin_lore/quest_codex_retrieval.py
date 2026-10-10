"""Family-specific read-only Repository for sealed QuestCodex Archive evidence."""

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
from .quest_codex_phase4f2_contract import (
    RUN_FAMILY as CANARY_RUN_FAMILY,
    EXPECTED_MANIFEST_SHA256 as CANARY_MANIFEST_SHA256,
    EXPECTED_OBSERVED_AT as CANARY_OBSERVED_AT,
    VERSIONS as CANARY_VERSIONS,
    expected_identity as expected_canary_identity,
    load_manifest as load_canary_manifest,
)
from .quest_codex_phase4f2_schema import verify_schema_13
from .batch_m4_runtime_oracle import verify_schema16
from .batch_m5d_runtime import BatchM5DError, _verify_schema17 as verify_schema17
from .db import validate_exact_schema_structure
from .quest_codex_phase4f3_contract import (
    EXPECTED_COVERAGE,
    EXPECTED_MANIFEST_SHA256,
    EXPECTED_OBSERVED_AT,
    FAMILY,
    FAMILY_CONTRACT_VERSION,
    MANIFEST_ID,
    RUN_FAMILY,
    VERSIONS,
    expected_identity,
    load_manifest,
)
from .quest_codex_phase4f3_receipt import (
    RECEIPT_CONTRACT,
    RECEIPT_VERSION,
    SCHEMA16_RECEIPT_SCHEMA_CONTRACT,
    SCHEMA17_RECEIPT_SCHEMA_CONTRACT,
    SCHEMA19_RECEIPT_SCHEMA_CONTRACT,
    SCHEMA20_RECEIPT_SCHEMA_CONTRACT,
    QuestCodexPhase4F3ReceiptError,
    load_release_receipt,
)
from .quest_codex_phase4f3_runtime_seal import (
    EXPECTED_RUNTIME_SEALS,
    OCCURRENCE_TABLES,
    scan_runtime_seals,
)
from .util import canonical_json, text_occurrences
from .world_archive_phase4e3_runtime_seal import database_identity
from .runtime_stability import RuntimeStabilitySnapshot, stable_routed_call


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "manifests/quest_codex/anime-game-data-7.0-phase4f3-quest-codex-full.json"
CANARY_MANIFEST_PATH = ROOT / "manifests/quest_codex/anime-game-data-7.0-phase4f2-quest-codex-canary.json"
EVIDENCE_KINDS = ("entry", "chapter_support")
LOCALES = ("CHS", "EN", "JP")
FIELD_ROLES = ("image_title", "chapter_number", "chapter_title")
TEXT_STATUSES = ("unique_medium", "missing")
CHAPTER_STATUSES = ("resolved_unique",)
PARENT_STATUSES = ("resolved_unique", "resolved_candidate_set", "missing")
LIST_FILTERS = (
    "evidence_kind", "chapter_id", "parent_quest_id", "tab_type", "is_disuse",
    "chapter_resolution_status", "parent_quest_resolution_status",
)
SEARCH_FILTERS = ("locale", "field_role", "resolution_status", "chapter_id")


def _json(value: Any, default: Any = None) -> Any:
    return default if value is None else json.loads(str(value))


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
        "primary_rows": 493,
        "referenced_chapter_support_rows": 140,
        "quest_lookup_inventory_only": True,
        "closure_counts": manifest["expected"]["counts"],
        "occurrence_set_digests": manifest["expected"]["occurrence_set_digests"],
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
        "schema_contract": "schema13-migration0013-exact",
        "occurrence_set_digests": manifest["expected"]["occurrence_set_digests"],
        "logical_identity_created": False,
        "mcp_provider_added": False,
    }


@dataclass(frozen=True)
class QuestCodexCapabilitySnapshot:
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
                "list_family_evidence", "get_family_evidence", "search_family_text",
            ],
            "supported_filters": {
                "list_family_evidence": list(LIST_FILTERS),
                "get_family_evidence": [],
                "search_family_text": list(SEARCH_FILTERS),
            },
            "filter_contract": {
                "evidence_kind": list(EVIDENCE_KINDS),
                "chapter_id": "exact_decimal_string",
                "parent_quest_id": "exact_decimal_string_entry_only",
                "tab_type": ["missing", "TRIBAL", "EVENT_ARCHIVE"],
                "is_disuse": ["missing", "true"],
                "chapter_resolution_status": list(CHAPTER_STATUSES),
                "parent_quest_resolution_status": list(PARENT_STATUSES),
                "locale": list(LOCALES),
                "field_role": list(FIELD_ROLES),
                "resolution_status": list(TEXT_STATUSES),
                "chapter_support_applicable_list_filters": ["evidence_kind", "chapter_id"],
            },
            "pagination_contract": {
                "kind": "deterministic_keyset",
                "cursor": "opaque_quest_codex_archive_cursor_v1",
                "limit": {"default": 50, "minimum": 1, "maximum": 100},
                "scope_binding": [
                    "family", "ingest_run_id", "family_contract_version",
                    "operation", "evidence_kind", "filters", "query",
                ],
            },
            "coverage_semantics": {
                "coverage": EXPECTED_COVERAGE,
                "corpus_complete": "true only for the exact verified 493 entry plus 140 referenced Chapter support row Phase 4F3 closure",
                "quest_lookup_inventory_only": True,
                "full_quest_corpus_claimed": False,
                "full_7_0_claimed": False,
            },
            "search_contract": {
                "scope": "chapter_support_explicit_text_resolution_occurrences_only",
                "query": "required_nonempty_literal_containing_at_least_one_non_whitespace_character",
                "matching": "case_sensitive_literal_instr_on_resolution_raw_text",
                "occurrence_preserving": True,
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
                "receipt_trust": "file_prefilter_not_authentication",
                "schema_contracts": [
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
                "startup_checks": ([self.eligible_runs[0]["qualification_source"], "exact_accepted_database_seal", "release_evidence_hashes", "database_identity_and_sidecar_stability"] if self.uses_shared_r2_qualification else [
                    "receipt_exact_contract", "database_size_and_streaming_sha256",
                    "schema13_structural_seal", "exact_release_run_metadata",
                    "schema16_additive_migration0016_structural_seal_when_selected",
                    "schema17_continuous_ledger_and_migration0017_structural_seal_when_selected",
                    "schema19_continuous_ledger_and_migrations0018_0019_structural_seal_when_selected",
                    "tracked_runtime_business_rowset_digests", "foreign_key_check",
                    "database_identity_and_sidecar_stability",
                ]),
                "business_digest_scope": "all_business_columns_in_nine_F3_run_scoped_occurrence_tables_plus_exact_referenced_provenance",
                "canary_rows_excluded_from_full_run_digests": True,
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
            "data_contract": FAMILY_CONTRACT_VERSION,
        }


def _schema13(conn: sqlite3.Connection) -> bool:
    try:
        verify_schema_13(conn)
        return True
    except (sqlite3.Error, RuntimeError, TypeError, ValueError):
        return False


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
    if after != before or (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns) != before:
        raise OSError("database changed while hashing")
    return {"size_bytes": before[2], "sha256": digest.hexdigest()}


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
    if expected_schema == 13:
        return _schema13(conn)
    if expected_schema == 16:
        return _schema16(conn)
    if expected_schema == 17:
        return _schema17(conn)
    if expected_schema == 19:
        return _schema19(conn)
    return _schema13(conn) or _schema16(conn) or _schema17(conn) or _schema19(conn)


def inspect_quest_codex_capability(
    db: ReadonlyDatabase, receipt_path: str | Path | None = None,
) -> QuestCodexCapabilitySnapshot:
    if receipt_path is None:
        return QuestCodexCapabilitySnapshot(
            _schema_supported(db.conn), unavailable_reason="release_receipt_missing"
        )
    try:
        receipt = load_release_receipt(receipt_path, MANIFEST_PATH, db.path)
        schema_contract = receipt["verification"]["schema_contract"]
        expected_schema = {
            "schema13-migration0013-exact": 13,
            SCHEMA16_RECEIPT_SCHEMA_CONTRACT: 16,
            SCHEMA17_RECEIPT_SCHEMA_CONTRACT: 17,
            SCHEMA19_RECEIPT_SCHEMA_CONTRACT: 19,
        }.get(schema_contract)
        if expected_schema is None:
            return QuestCodexCapabilitySnapshot(
                _schema_supported(db.conn), unavailable_reason="release_qualification_failed"
            )
        identity_before = database_identity(db.path)
        if db.file_identity != identity_before:
            return QuestCodexCapabilitySnapshot(
                _schema_supported(db.conn, expected_schema), unavailable_reason="database_changed_before_qualification"
            )
        if _database_fingerprint(db.path) != receipt["database"]:
            return QuestCodexCapabilitySnapshot(
                _schema_supported(db.conn, expected_schema), unavailable_reason="database_fingerprint_mismatch"
            )
        if not _schema_supported(db.conn, expected_schema):
            return QuestCodexCapabilitySnapshot(
                False, unavailable_reason=f"schema{expected_schema}_structural_seal_mismatch"
            )
        manifest = _manifest()
        identity = expected_identity(manifest)
        run = db.conn.execute(
            """SELECT ir.*,pb.parser_version,pb.mapping_version,pb.fingerprint,
                      pb.config_checksum,pb.created_at AS parser_created_at
                 FROM ingest_run ir JOIN parser_build pb ON pb.id=ir.parser_build_id
                WHERE ir.id=?""",
            (identity["ingest_run_id"],),
        ).fetchone()
        if run is None:
            return QuestCodexCapabilitySnapshot(
                True, unavailable_reason="exact_release_run_metadata_mismatch"
            )
        canary_run_id = expected_canary_identity(load_canary_manifest(CANARY_MANIFEST_PATH))["ingest_run_id"]
        family_runs: list[tuple[str, str]] = []
        for item in db.conn.execute("SELECT id,scope_json FROM ingest_run ORDER BY id"):
            scope = _json(item["scope_json"], {})
            family = str(scope.get("family", "")) if isinstance(scope, dict) else ""
            provider = str(scope.get("provider_family", "")) if isinstance(scope, dict) else ""
            if provider == FAMILY or family.startswith(FAMILY):
                family_runs.append((str(item["id"]), family))
        allowed = all(
            item[0] == identity["ingest_run_id"] or (item[0] == canary_run_id and item[1] == CANARY_RUN_FAMILY)
            for item in family_runs
        ) and any(item[0] == identity["ingest_run_id"] for item in family_runs)
        canary_exact = True
        if any(item[0] == canary_run_id for item in family_runs):
            canary_manifest = load_canary_manifest(CANARY_MANIFEST_PATH)
            canary_identity = expected_canary_identity(canary_manifest)
            canary = db.conn.execute(
                "SELECT ir.snapshot_id,ir.parser_build_id,ir.scope_json,ir.config_json,ir.status,ir.created_at,pb.parser_version,pb.mapping_version,pb.fingerprint,pb.config_checksum,pb.created_at FROM ingest_run ir JOIN parser_build pb ON pb.id=ir.parser_build_id WHERE ir.id=?",
                (canary_run_id,),
            ).fetchone()
            expected_canary_scope = canonical_json({"family": CANARY_RUN_FAMILY, "provider_family": FAMILY, "family_contract_version": FAMILY_CONTRACT_VERSION, "manifest_id": canary_manifest["manifest_id"], "selected_ids": list(canary_manifest["boundary"]["selected_ids"]), "exact_ingest_run_required": True})
            expected_canary_config = canonical_json({"manifest_raw_sha256": CANARY_MANIFEST_SHA256, "versions": canary_manifest["versions"], "logical_identity_created": False, "full_quest_codex_scan": False, "corpus_complete": False, "mcp_provider_added": False})
            canary_exact = canary is not None and tuple(canary) == (canary_identity["snapshot_id"], canary_identity["parser_build_id"], expected_canary_scope, expected_canary_config, "complete", CANARY_OBSERVED_AT, CANARY_VERSIONS["parser"], CANARY_VERSIONS["projection"], "quest-codex-phase4f2-sealed-canary", canary_identity["config_checksum"], CANARY_OBSERVED_AT)
        allowed_row_runs = {identity["ingest_run_id"]}
        if any(item[0] == canary_run_id for item in family_runs):
            allowed_row_runs.add(canary_run_id)
        occurrence_run_sets = {
            table: {str(row[0]) for row in db.conn.execute(f"SELECT DISTINCT ingest_run_id FROM {table}")}
            for table in OCCURRENCE_TABLES
        }
        occurrence_runs_exact = all(
            runs <= allowed_row_runs and identity["ingest_run_id"] in runs
            for runs in occurrence_run_sets.values()
        )
        exact = (
            run["snapshot_id"] == identity["snapshot_id"]
            and run["parser_build_id"] == identity["parser_build_id"]
            and _json(run["scope_json"]) == _expected_scope(manifest)
            and _json(run["config_json"]) == _expected_config(manifest)
            and run["status"] == "complete" and run["created_at"] == EXPECTED_OBSERVED_AT
            and run["parser_version"] == VERSIONS["parser"]
            and run["mapping_version"] == VERSIONS["projection"]
            and run["fingerprint"] == "quest-codex-phase4f3-sealed-full"
            and run["config_checksum"] == identity["config_checksum"]
            and run["parser_created_at"] == EXPECTED_OBSERVED_AT
            and allowed and canary_exact and occurrence_runs_exact
        )
        if not exact:
            return QuestCodexCapabilitySnapshot(
                True, unavailable_reason="exact_release_run_metadata_mismatch"
            )
        seals = scan_runtime_seals(
            db.conn,
            ingest_run_id=identity["ingest_run_id"],
            snapshot_id=identity["snapshot_id"],
            parser_build_id=identity["parser_build_id"],
            source_id=manifest["source"]["source_id"],
            manifest_files=manifest["files"],
        )
        if seals != EXPECTED_RUNTIME_SEALS:
            return QuestCodexCapabilitySnapshot(
                True, unavailable_reason="runtime_business_digest_mismatch"
            )
        if db.conn.execute("PRAGMA foreign_key_check").fetchall():
            return QuestCodexCapabilitySnapshot(
                True, unavailable_reason="foreign_key_check_failed"
            )
        if database_identity(db.path) != identity_before:
            return QuestCodexCapabilitySnapshot(
                True, unavailable_reason="database_changed_during_qualification"
            )
        binding = {
            "ingest_run_id": identity["ingest_run_id"],
            "snapshot_id": identity["snapshot_id"],
            "parser_build_id": identity["parser_build_id"],
            "manifest_id": MANIFEST_ID,
            "manifest_raw_sha256": EXPECTED_MANIFEST_SHA256,
            "coverage": EXPECTED_COVERAGE,
            "corpus_complete": True,
            "primary_rows": 493,
            "referenced_chapter_support_rows": 140,
            "release_receipt_contract": RECEIPT_CONTRACT,
            "release_receipt_version": RECEIPT_VERSION,
            "schema_contract": schema_contract,
            "database_sha256": receipt["database"]["sha256"],
        }
        stability = RuntimeStabilitySnapshot.capture(db.path, receipt_path)
        return QuestCodexCapabilitySnapshot(
            True, canonical_json([binding]), None,
            canonical_json(stability.to_json()),
        )
    except (
        OSError, RuntimeError, sqlite3.Error, TypeError, ValueError, KeyError,
        json.JSONDecodeError, QuestCodexPhase4F3ReceiptError,
    ):
        return QuestCodexCapabilitySnapshot(
            _schema_supported(db.conn), unavailable_reason="release_qualification_failed"
        )


def qualify_quest_codex_r2_compatibility(
    db: ReadonlyDatabase, receipt_path: str | Path | None, qualification: Any,
) -> QuestCodexCapabilitySnapshot:
    """Derive Schema19 compatibility from one already exact R2 qualification."""
    if receipt_path is None:
        return QuestCodexCapabilitySnapshot(True, unavailable_reason="release_receipt_missing")
    try:
        qualification.check()
        manifest = load_manifest(MANIFEST_PATH)
        receipt = load_release_receipt(receipt_path, MANIFEST_PATH)
        if receipt["verification"]["schema_contract"] != qualification.schema_contract:
            raise QuestCodexPhase4F3ReceiptError("Schema19 receipt required")
        expected_database = {"size_bytes": qualification.candidate_size_bytes, "sha256": qualification.candidate_sha256}
        if receipt["database"] != expected_database:
            raise QuestCodexPhase4F3ReceiptError("R2 database binding mismatch")
        identity = expected_identity(manifest)
        binding = {
            "ingest_run_id": identity["ingest_run_id"], "snapshot_id": identity["snapshot_id"],
            "parser_build_id": identity["parser_build_id"], "manifest_id": MANIFEST_ID,
            "manifest_raw_sha256": EXPECTED_MANIFEST_SHA256, "coverage": EXPECTED_COVERAGE,
            "corpus_complete": True, "primary_rows": 493, "referenced_chapter_support_rows": 140,
            "release_receipt_contract": RECEIPT_CONTRACT, "release_receipt_version": RECEIPT_VERSION,
            "schema_contract": qualification.schema_contract, "database_sha256": qualification.candidate_sha256,
            "qualification_source": ("shared_exact_r4_release_bundle" if qualification.schema_version == 20 else "shared_exact_r2_release_bundle"),
        }
        stability = RuntimeStabilitySnapshot.capture(db.path, receipt_path)
        qualification.check()
        return QuestCodexCapabilitySnapshot(True, canonical_json([binding]), None, canonical_json(stability.to_json()))
    except (OSError, RuntimeError, TypeError, ValueError, KeyError, QuestCodexPhase4F3ReceiptError):
        return QuestCodexCapabilitySnapshot(True, unavailable_reason="r2_compatibility_qualification_failed")


def _cursor(scope: dict[str, Any], last: list[Any]) -> str:
    body = {"v": 1, "scope": scope, "last": last}
    check = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    return base64.urlsafe_b64encode(
        canonical_json({**body, "check": check}).encode()
    ).decode().rstrip("=")


def _decode_cursor(value: str | None, scope: dict[str, Any], length: int) -> list[Any] | None:
    if value is None:
        return None
    _text(value, "cursor")
    try:
        payload = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode())
        body = {key: payload[key] for key in ("v", "scope", "last")}
        valid = payload.get("check") == hashlib.sha256(canonical_json(body).encode()).hexdigest()
        if body["v"] != 1 or body["scope"] != scope or not valid:
            raise ValueError
        last = body["last"]
        if not isinstance(last, list) or len(last) != length or not all(
            isinstance(item, (str, int)) and not isinstance(item, bool) for item in last
        ):
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
    result = {key: _text(item, f"filters.{key}") for key, item in value.items()}
    validations = {
        "evidence_kind": EVIDENCE_KINDS,
        "tab_type": ("missing", "TRIBAL", "EVENT_ARCHIVE"),
        "is_disuse": ("missing", "true"),
        "chapter_resolution_status": CHAPTER_STATUSES,
        "parent_quest_resolution_status": PARENT_STATUSES,
        "locale": LOCALES,
        "field_role": FIELD_ROLES,
        "resolution_status": TEXT_STATUSES,
    }
    for key, choices in validations.items():
        if key in result and result[key] not in choices:
            raise ValueError(f"filters.{key} has an unsupported value")
    for key in ("chapter_id", "parent_quest_id"):
        if key in result and not result[key].isdigit():
            raise ValueError(f"filters.{key} must be an exact decimal string")
    return result


def _source(db: ReadonlyDatabase, record_id: str) -> dict[str, Any] | None:
    row = db.conn.execute(
        """SELECT sr.*,sf.relative_path,sf.sha256 AS file_sha256,
                  sf.upstream_blob_oid,sf.format,sf.root_path
             FROM source_record sr JOIN source_file sf ON sf.id=sr.source_file_id
            WHERE sr.id=?""", (record_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "source_record_id": row["id"], "source_file_id": row["source_file_id"],
        "relative_path": row["relative_path"], "source_pointer": row["pointer"],
        "record_kind": row["record_kind"], "native_id": row["native_id"],
        "raw_checksum": row["raw_sha256"], "raw_payload": _json(row["raw_json"]),
        "source_file": {
            "sha256": row["file_sha256"], "upstream_blob_oid": row["upstream_blob_oid"],
            "format": row["format"], "root_path": row["root_path"],
        },
    }


def _row_item(db: ReadonlyDatabase, row: Mapping[str, Any], provenance: bool = False) -> dict[str, Any]:
    item = {
        "occurrence_id": row["id"], "evidence_kind": ("entry" if row["evidence_kind"] == "quest_codex_entry" else "chapter_support"),
        "source_table": row["table_name"], "row_index": row["row_index"],
        "native_locator": {"field": "id", "value": row["native_locator_value"]},
        "source_pointer": row["source_pointer"], "source_record_id": row["source_record_id"],
        "raw_checksum": row["raw_checksum"], "metadata": _json(row["metadata_json"], {}),
    }
    if provenance:
        item["provenance"] = _source(db, str(row["source_record_id"]))
    return item


def _result(status: str, run: str, data: dict[str, Any], *, eligible: bool = True) -> FamilyEvidenceResult:
    return FamilyEvidenceResult(
        status, FAMILY, run, FAMILY_CONTRACT_VERSION,
        EXPECTED_COVERAGE if eligible else "unavailable", eligible, data,
    )


def _plain(row: Mapping[str, Any], excluded: set[str] | None = None) -> dict[str, Any]:
    skip = {"snapshot_id", "parser_build_id", "ingest_run_id"}
    skip.update(excluded or set())
    return {
        key: (_json(row[key]) if key.endswith("_json") else row[key])
        for key in row.keys() if key not in skip
    }


class QuestCodexRepository:
    family = FAMILY
    family_contract_version = FAMILY_CONTRACT_VERSION

    def __init__(
        self, db: ReadonlyDatabase, capability: QuestCodexCapabilitySnapshot | None = None,
    ) -> None:
        self.db = db
        self._capability = capability or inspect_quest_codex_capability(db)
        self._eligible = frozenset(
            str(item["ingest_run_id"]) for item in self._capability.eligible_runs
        )
        self._stability = self._capability.runtime_stability

    def capability(self) -> dict[str, Any]:
        return self._capability.to_dict()

    def _run(self, run: Any, operation: str) -> FamilyEvidenceResult | None:
        requested = _text(run, "ingest_run_id")
        if requested in self._eligible:
            return None
        exists = self.db.conn.execute(
            "SELECT 1 FROM ingest_run WHERE id=?", (requested,)
        ).fetchone() is not None
        return _result(
            "not_found", requested,
            {"status": "NotFound", "reason": "ingest_run_ineligible" if exists else "ingest_run_not_found",
             "lookup": {"operation": operation, "ingest_run_id": requested}},
            eligible=False,
        )

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
    def list_evidence(
        self, *, ingest_run_id: str, family_contract_version: str,
        filters: dict[str, Any] | None = None, cursor: str | None = None,
        limit: int = 50,
    ) -> FamilyEvidenceResult:
        error = self._contract(ingest_run_id, family_contract_version, "list_family_evidence")
        if error:
            return error
        error = self._run(ingest_run_id, "list_family_evidence")
        if error:
            return error
        selected = _filters(filters, LIST_FILTERS)
        kind = selected.get("evidence_kind", "entry")
        selected = {**selected, "evidence_kind": kind}
        if kind == "chapter_support":
            invalid = sorted(set(selected) - {"evidence_kind", "chapter_id"})
            if invalid:
                raise ValueError(
                    f"filters not applicable to chapter_support: {', '.join(invalid)}"
                )
        limit = _limit(limit)
        db_kind = "quest_codex_entry" if kind == "entry" else "chapter_support"
        scope = {"family": FAMILY, "run": ingest_run_id, "contract": family_contract_version,
                 "operation": "list", "evidence_kind": kind, "filters": selected, "query": None}
        last = _decode_cursor(cursor, scope, 2)
        where, params = ["r.ingest_run_id=?", "r.evidence_kind=?"], [ingest_run_id, db_kind]
        if "chapter_id" in selected:
            if kind == "chapter_support":
                where.append("r.native_locator_value=?")
                params.append(selected["chapter_id"])
            else:
                where.append("EXISTS (SELECT 1 FROM quest_codex_relationship_occurrence q WHERE q.ingest_run_id=r.ingest_run_id AND q.source_row_occurrence_id=r.id AND q.field_name='chapterId' AND CAST(json_extract(q.raw_value_json,'$') AS TEXT)=?)")
                params.append(selected["chapter_id"])
        entry_relations = (
            ("parent_quest_id", "parentQuestId", None),
            ("chapter_resolution_status", "chapterId", "resolution_status"),
            ("parent_quest_resolution_status", "parentQuestId", "resolution_status"),
        )
        for key, field, column in entry_relations:
            if key in selected:
                condition = "CAST(json_extract(q.raw_value_json,'$') AS TEXT)=?" if column is None else f"q.{column}=?"
                where.append(f"EXISTS (SELECT 1 FROM quest_codex_relationship_occurrence q WHERE q.ingest_run_id=r.ingest_run_id AND q.source_row_occurrence_id=r.id AND q.field_name='{field}' AND {condition})")
                params.append(selected[key])
        for key, field in (("tab_type", "tabType"), ("is_disuse", "isDisuse")):
            if key in selected:
                if selected[key] == "missing":
                    where.append("EXISTS (SELECT 1 FROM quest_codex_raw_field_occurrence f WHERE f.ingest_run_id=r.ingest_run_id AND f.row_occurrence_id=r.id AND f.field_name=? AND f.field_presence='missing')")
                    params.append(field)
                else:
                    if key == "is_disuse":
                        where.append("EXISTS (SELECT 1 FROM quest_codex_raw_field_occurrence f WHERE f.ingest_run_id=r.ingest_run_id AND f.row_occurrence_id=r.id AND f.field_name=? AND f.field_presence='present' AND json_type(f.raw_json)='true')")
                        params.append(field)
                    else:
                        where.append("EXISTS (SELECT 1 FROM quest_codex_raw_field_occurrence f WHERE f.ingest_run_id=r.ingest_run_id AND f.row_occurrence_id=r.id AND f.field_name=? AND f.field_presence='present' AND CAST(json_extract(f.raw_json,'$') AS TEXT)=?)")
                        params.extend([field, selected[key]])
        if last is not None:
            where.append("(r.row_index>? OR (r.row_index=? AND r.id>?))")
            params.extend([last[0], last[0], last[1]])
        rows = self.db.conn.execute(
            f"SELECT r.* FROM quest_codex_row_occurrence r WHERE {' AND '.join(where)} ORDER BY r.row_index,r.id LIMIT ?",
            (*params, limit + 1),
        ).fetchall()
        page, more = rows[:limit], len(rows) > limit
        next_cursor = _cursor(scope, [page[-1]["row_index"], page[-1]["id"]]) if more and page else None
        return _result("ok", ingest_run_id, {
            "operation": "list", "evidence_kind": kind, "filters": selected,
            "items": [_row_item(self.db, row) for row in page],
            "pagination": {"kind": "keyset", "limit": limit, "next_cursor": next_cursor},
        })

    @stable_routed_call("get_family_evidence")
    def get_evidence(
        self, *, ingest_run_id: str, family_contract_version: str,
        occurrence_id: str,
    ) -> FamilyEvidenceResult:
        error = self._contract(ingest_run_id, family_contract_version, "get_family_evidence")
        if error:
            return error
        error = self._run(ingest_run_id, "get_family_evidence")
        if error:
            return error
        requested = _text(occurrence_id, "occurrence_id")
        row = self.db.conn.execute(
            "SELECT * FROM quest_codex_row_occurrence WHERE ingest_run_id=? AND id=?",
            (ingest_run_id, requested),
        ).fetchone()
        if row is None:
            return _result("not_found", ingest_run_id, {
                "status": "NotFound", "reason": "occurrence_not_found",
                "lookup": {"operation": "get", "occurrence_id": requested},
            })
        children: dict[str, Any] = {}
        for name, table, field in (
            ("raw_fields", "quest_codex_raw_field_occurrence", "row_occurrence_id"),
            ("opaque", "quest_codex_opaque_occurrence", "source_row_occurrence_id"),
            ("assets", "quest_codex_asset_reference_occurrence", "source_row_occurrence_id"),
        ):
            values = self.db.conn.execute(
                f"SELECT * FROM {table} WHERE ingest_run_id=? AND {field}=? ORDER BY source_pointer,id",
                (ingest_run_id, requested),
            ).fetchall()
            children[name] = [_plain(item) for item in values]
        relationships = self.db.conn.execute(
            "SELECT * FROM quest_codex_relationship_occurrence WHERE ingest_run_id=? AND source_row_occurrence_id=? ORDER BY source_pointer,id",
            (ingest_run_id, requested),
        ).fetchall()
        relationship_items = []
        for relationship in relationships:
            candidates = self.db.conn.execute(
                "SELECT * FROM quest_codex_relationship_candidate_occurrence WHERE ingest_run_id=? AND relationship_occurrence_id=? ORDER BY candidate_ordinal,id",
                (ingest_run_id, relationship["id"]),
            ).fetchall()
            item = _plain(relationship)
            item["candidates"] = [_plain(candidate) for candidate in candidates]
            relationship_items.append(item)
        children["relationships"] = relationship_items
        owners = self.db.conn.execute(
            "SELECT * FROM quest_codex_text_owner_occurrence WHERE ingest_run_id=? AND source_row_occurrence_id=? ORDER BY source_pointer,id",
            (ingest_run_id, requested),
        ).fetchall()
        owner_items = []
        for owner in owners:
            resolutions = self.db.conn.execute(
                "SELECT * FROM quest_codex_text_resolution_occurrence WHERE ingest_run_id=? AND owner_occurrence_id=? ORDER BY locale,id",
                (ingest_run_id, owner["id"]),
            ).fetchall()
            resolution_items = []
            for resolution in resolutions:
                candidates = self.db.conn.execute(
                    "SELECT * FROM quest_codex_text_candidate_occurrence WHERE ingest_run_id=? AND resolution_occurrence_id=? ORDER BY source_pointer,id",
                    (ingest_run_id, resolution["id"]),
                ).fetchall()
                item = _plain(resolution)
                item["candidates"] = [_plain(candidate) for candidate in candidates]
                resolution_items.append(item)
            item = _plain(owner)
            item["resolutions"] = resolution_items
            owner_items.append(item)
        children["text_owners"] = owner_items
        return _result("ok", ingest_run_id, {
            "operation": "get", "container_scope": "single_physical_quest_codex_archive_row_occurrence",
            "occurrence": {"row": _row_item(self.db, row, provenance=True), **children},
        })

    @stable_routed_call("search_family_text")
    def search_text(
        self, *, ingest_run_id: str, family_contract_version: str, query: str,
        filters: dict[str, Any] | None = None, cursor: str | None = None,
        limit: int = 50,
    ) -> FamilyEvidenceResult:
        error = self._contract(ingest_run_id, family_contract_version, "search_family_text")
        if error:
            return error
        error = self._run(ingest_run_id, "search_family_text")
        if error:
            return error
        literal = _text(query, "query")
        if not literal.strip():
            raise ValueError("query must contain a non-whitespace character")
        selected, limit = _filters(filters, SEARCH_FILTERS), _limit(limit)
        scope = {"family": FAMILY, "run": ingest_run_id, "contract": family_contract_version,
                 "operation": "search_text", "evidence_kind": "chapter_support",
                 "filters": selected, "query": literal}
        last = _decode_cursor(cursor, scope, 4)
        where, params = [
            "tr.ingest_run_id=?", "r.evidence_kind='chapter_support'",
            "tr.raw_text IS NOT NULL", "instr(tr.raw_text,?)>0",
        ], [ingest_run_id, literal]
        mapping = {
            "locale": "tr.locale", "field_role": "o.field_role",
            "resolution_status": "tr.resolution_status", "chapter_id": "r.native_locator_value",
        }
        for key, column in mapping.items():
            if key in selected:
                where.append(f"{column}=?")
                params.append(selected[key])
        if last is not None:
            where.append("(tr.locale,r.row_index,tr.source_pointer,tr.id)>(?,?,?,?)")
            params.extend(last)
        rows = self.db.conn.execute(
            f"""SELECT tr.*,o.field_name,o.field_role,o.textmap_hash,
                       r.table_name,r.evidence_kind,r.row_index,r.native_locator_value,
                       r.raw_checksum AS row_checksum,r.metadata_json AS row_metadata,
                       r.source_record_id AS row_record_id,r.source_pointer AS row_pointer,
                       r.id AS row_id
                  FROM quest_codex_text_resolution_occurrence tr
                  JOIN quest_codex_text_owner_occurrence o
                    ON o.id=tr.owner_occurrence_id AND o.ingest_run_id=tr.ingest_run_id
                  JOIN quest_codex_row_occurrence r
                    ON r.id=tr.source_row_occurrence_id AND r.ingest_run_id=tr.ingest_run_id
                 WHERE {' AND '.join(where)}
                 ORDER BY tr.locale,r.row_index,tr.source_pointer,tr.id LIMIT ?""",
            (*params, limit + 1),
        ).fetchall()
        page, more, items = rows[:limit], len(rows) > limit, []
        for row in page:
            candidates = self.db.conn.execute(
                "SELECT id,source_file_id,source_record_id,source_pointer,shard_kind,locale,raw_text,normalized_text,raw_checksum FROM quest_codex_text_candidate_occurrence WHERE ingest_run_id=? AND resolution_occurrence_id=? ORDER BY source_pointer,id",
                (ingest_run_id, row["id"]),
            ).fetchall()
            items.append({
                "occurrence_id": row["id"], "owner_occurrence_id": row["owner_occurrence_id"],
                "source_pointer": row["source_pointer"], "field_name": row["field_name"],
                "field_role": row["field_role"], "locale": row["locale"],
                "textmap_hash": row["textmap_hash"], "resolution_status": row["resolution_status"],
                "raw_text": row["raw_text"], "normalized_text": row["normalized_text"],
                "matches": [{"start": start, "end": end} for start, end in text_occurrences(row["raw_text"], literal)],
                "candidates": [dict(candidate) for candidate in candidates],
                "row": {
                    "occurrence_id": row["row_id"], "evidence_kind": row["evidence_kind"],
                    "source_table": row["table_name"], "row_index": row["row_index"],
                    "native_locator": {"field": "id", "value": row["native_locator_value"]},
                    "source_pointer": row["row_pointer"], "source_record_id": row["row_record_id"],
                    "raw_checksum": row["row_checksum"], "metadata": _json(row["row_metadata"], {}),
                },
            })
        next_cursor = _cursor(
            scope, [page[-1]["locale"], page[-1]["row_index"], page[-1]["source_pointer"], page[-1]["id"]]
        ) if more and page else None
        return _result("ok", ingest_run_id, {
            "operation": "search_text",
            "query": {"literal": literal, "semantics": "case_sensitive_literal_instr_on_resolution_raw_text"},
            "filters": selected, "items": items,
            "pagination": {"kind": "keyset", "limit": limit, "next_cursor": next_cursor},
        })


__all__ = [
    "QuestCodexCapabilitySnapshot", "QuestCodexRepository",
    "inspect_quest_codex_capability",
    "qualify_quest_codex_r2_compatibility",
]
