"""Family-specific routed retrieval for the Batch M4 wave.

The provider in this module is intentionally small: each M4 family owns one
physical row-occurrence table and the routed facade never turns the raw row
into a shared entity.  Run and contract binding are explicit for every call.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ._readonly_database import ReadonlyDatabase
from .batch_m4_release import M4_FAMILY_ORDER, SCHEMA_VERSION, SNAPSHOT_ID
from .batch_m4_receipt import BatchM4ReceiptError, load_release_receipt
from .batch_m4_runtime_oracle import (
    BatchM4RuntimeOracle,
    BatchM4RuntimeOracleError,
    RUNTIME_COVERAGE,
    RUNTIME_MANIFEST_PATH,
    RUNTIME_SCHEMA_CONTRACT,
    RUNTIME_WAVE_CONTRACT,
    load_runtime_manifest,
    verify_schema16,
)
from .family_evidence import FamilyEvidenceResult
from .runtime_stability import RuntimeStabilityError, RuntimeStabilitySnapshot, stable_routed_call
from .util import canonical_json, text_occurrences


@dataclass(frozen=True)
class FamilyRetrievalSpec:
    """One family-owned routed retrieval contract.

    The ten entries below are intentionally explicit.  The shared router only
    transports these family-owned declarations; it does not infer identity,
    relations, filters, or a cross-family adapter.
    """

    family: str
    table: str
    family_contract_version: str
    list_filters: tuple[str, ...]
    search_fields: tuple[str, ...]
    order: tuple[str, ...]
    container_scope: str


FAMILY_RETRIEVAL_SPECS: dict[str, FamilyRetrievalSpec] = {
    "achievement_archive": FamilyRetrievalSpec(
        "achievement_archive", "achievement_archive_row_occurrence",
        "achievement-archive-evidence-v1",
        ("source_file_path", "record_status", "source_ordinal"),
        ("raw_record_json",),
        ("source_file_path", "source_ordinal", "source_pointer", "occurrence_id"),
        "single_physical_achievement_archive_row_occurrence",
    ),
    "activity_archive": FamilyRetrievalSpec(
        "activity_archive", "activity_archive_row_occurrence",
        "activity-archive-evidence-v1",
        ("source_file_path", "record_status", "source_ordinal"),
        ("raw_record_json",),
        ("source_file_path", "source_ordinal", "source_pointer", "occurrence_id"),
        "single_physical_activity_archive_row_occurrence",
    ),
    "avatar_config_archive": FamilyRetrievalSpec(
        "avatar_config_archive", "avatar_config_archive_row_occurrence",
        "avatar-config-archive-evidence-v1",
        ("source_file_path", "record_status", "source_ordinal"),
        ("raw_record_json",),
        ("source_file_path", "source_ordinal", "source_pointer", "occurrence_id"),
        "single_physical_avatar_config_archive_row_occurrence",
    ),
    "dungeon_archive": FamilyRetrievalSpec(
        "dungeon_archive", "dungeon_archive_row_occurrence",
        "dungeon-archive-evidence-v1",
        ("source_file_path", "record_status", "source_ordinal"),
        ("raw_record_json",),
        ("source_file_path", "source_ordinal", "source_pointer", "occurrence_id"),
        "single_physical_dungeon_archive_row_occurrence",
    ),
    "material_config_archive": FamilyRetrievalSpec(
        "material_config_archive", "material_config_archive_row_occurrence",
        "material-config-archive-evidence-v1",
        ("source_file_path", "record_status", "source_ordinal"),
        ("raw_record_json",),
        ("source_file_path", "source_ordinal", "source_pointer", "occurrence_id"),
        "single_physical_material_config_archive_row_occurrence",
    ),
    "monster_archive": FamilyRetrievalSpec(
        "monster_archive", "monster_archive_row_occurrence",
        "monster-archive-evidence-v1",
        ("source_file_path", "record_status", "source_ordinal"),
        ("raw_record_json",),
        ("source_file_path", "source_ordinal", "source_pointer", "occurrence_id"),
        "single_physical_monster_archive_row_occurrence",
    ),
    "npc_archive": FamilyRetrievalSpec(
        "npc_archive", "npc_archive_row_occurrence",
        "npc-archive-evidence-v1",
        ("source_file_path", "record_status", "source_ordinal"),
        ("raw_record_json",),
        ("source_file_path", "source_ordinal", "source_pointer", "occurrence_id"),
        "single_physical_npc_archive_row_occurrence",
    ),
    "reliquary_config_archive": FamilyRetrievalSpec(
        "reliquary_config_archive", "reliquary_config_archive_row_occurrence",
        "reliquary-config-archive-evidence-v1",
        ("source_file_path", "record_status", "source_ordinal"),
        ("raw_record_json",),
        ("source_file_path", "source_ordinal", "source_pointer", "occurrence_id"),
        "single_physical_reliquary_config_archive_row_occurrence",
    ),
    "scene_archive": FamilyRetrievalSpec(
        "scene_archive", "scene_archive_row_occurrence",
        "scene-archive-evidence-v1",
        ("source_file_path", "record_status", "source_ordinal"),
        ("raw_record_json",),
        ("source_file_path", "source_ordinal", "source_pointer", "occurrence_id"),
        "single_physical_scene_archive_row_occurrence",
    ),
    "weapon_config_archive": FamilyRetrievalSpec(
        "weapon_config_archive", "weapon_config_archive_row_occurrence",
        "weapon-config-archive-evidence-v1",
        ("source_file_path", "record_status", "source_ordinal"),
        ("raw_record_json",),
        ("source_file_path", "source_ordinal", "source_pointer", "occurrence_id"),
        "single_physical_weapon_config_archive_row_occurrence",
    ),
}
FAMILY_CONTRACTS = {
    family: spec.family_contract_version
    for family, spec in FAMILY_RETRIEVAL_SPECS.items()
}
FAMILY_TABLES = {family: spec.table for family, spec in FAMILY_RETRIEVAL_SPECS.items()}
# Compatibility aliases for callers that imported the old constants.  Runtime
# execution below uses the family-owned spec entry, not these shared aliases.
LIST_FILTERS = ("source_file_path", "record_status", "source_ordinal")
SEARCH_FILTERS = LIST_FILTERS
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _q(identifier: str) -> str:
    if not isinstance(identifier, str) or not _IDENTIFIER.fullmatch(identifier):
        raise BatchM4RetrievalError(f"unsafe retrieval identifier: {identifier!r}")
    return '"' + identifier.replace('"', '""') + '"'


def _after_key_clause(order: tuple[str, ...], last: tuple[Any, ...]) -> tuple[str, list[Any]]:
    if len(order) != len(last):
        raise ValueError("cursor position does not match family ordering")
    terms: list[str] = []
    params: list[Any] = []
    for index, column in enumerate(order):
        equal = " AND ".join(f"{_q(previous)}=?" for previous in order[:index])
        terms.append(f"({equal + ' AND ' if equal else ''}{_q(column)}>?)")
        params.extend(last[:index])
        params.append(last[index])
    return "(" + " OR ".join(terms) + ")", params


class BatchM4RetrievalError(RuntimeError):
    """The M4 candidate cannot be exposed through the routed facade."""


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ValueError("limit must be between 1 and 100")
    return value


def _json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(str(value))


def _encode_cursor(scope: Mapping[str, Any], last: tuple[Any, ...]) -> str:
    payload = {"scope": dict(scope), "last": list(last), "version": 1}
    raw = canonical_json(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(
    value: str | None, scope: Mapping[str, Any], *, key_count: int = 4,
) -> tuple[Any, ...] | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("cursor must be a non-empty string")
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, TypeError, UnicodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise ValueError("cursor is invalid") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1 or payload.get("scope") != dict(scope):
        raise ValueError("cursor scope does not match request")
    last = payload.get("last")
    if not isinstance(last, list) or len(last) != key_count:
        raise ValueError("cursor position is invalid")
    return tuple(last)


def _filters(value: dict[str, Any] | None, allowed: tuple[str, ...]) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("filters must be an object")
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ValueError(f"unsupported filters: {', '.join(unknown)}")
    result: dict[str, Any] = {}
    for key, raw in value.items():
        if key == "source_ordinal":
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
                raise ValueError("filters.source_ordinal must be a non-negative integer")
            result[key] = raw
        elif not isinstance(raw, str) or not raw:
            raise ValueError(f"filters.{key} must be a non-empty string")
        else:
            result[key] = raw
    return result


def _row_item(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "occurrence_id": row["occurrence_id"],
        "family": row["family"],
        "family_contract_version": row["family_contract_version"],
        "ingest_run_id": row["ingest_run_id"],
        "snapshot_id": row["snapshot_id"],
        "parser_build_id": row["parser_build_id"],
        "source_file_path": row["source_file_path"],
        "source_record_id": row["source_record_id"],
        "source_pointer": row["source_pointer"],
        "source_ordinal": row["source_ordinal"],
        "raw_record_json": row["raw_record_json"],
        "raw_record_sha256": row["raw_record_sha256"],
        "raw_key_observations": _json(row["raw_key_observations_json"], []),
        "record_status": row["record_status"],
        "metadata": _json(row["metadata_json"], {}),
    }


def _result(family: str, run: str, status: str, data: dict[str, Any], *, eligible: bool) -> FamilyEvidenceResult:
    contract = FAMILY_RETRIEVAL_SPECS[family].family_contract_version
    return FamilyEvidenceResult(
        status,
        family,
        run,
        contract,
        "R2_wave_accepted_product" if eligible else "unavailable",
        False,
        data,
    )


@dataclass(frozen=True)
class BatchM4CapabilitySnapshot:
    family: str
    family_contract_version: str
    schema_supported: bool
    eligible_runs: tuple[dict[str, Any], ...] = ()
    unavailable_reason: str | None = None
    receipt: dict[str, Any] | None = None
    runtime_oracle: dict[str, Any] | None = None
    _stability_json: str = ""

    @property
    def evidence_available(self) -> bool:
        return bool(self.eligible_runs)

    @property
    def runtime_stability(self) -> RuntimeStabilitySnapshot | None:
        if not self._stability_json:
            return None
        try:
            value = json.loads(self._stability_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return RuntimeStabilitySnapshot.from_json(value)

    def to_dict(self) -> dict[str, Any]:
        spec = FAMILY_RETRIEVAL_SPECS[self.family]
        return {
            "family": self.family,
            "family_contract_version": spec.family_contract_version,
            "table": spec.table,
            "ordering": list(spec.order),
            "container_scope": spec.container_scope,
            "schema_supported": self.schema_supported,
            "evidence_available": self.evidence_available,
            "unavailable_reason": self.unavailable_reason,
            "eligible_runs": list(self.eligible_runs),
            "supported_operations": [
                "list_family_evidence", "get_family_evidence", "search_family_text",
            ],
            "supported_filters": {
                "list_family_evidence": list(spec.list_filters),
                "get_family_evidence": [],
                "search_family_text": list(spec.list_filters),
            },
            "pagination_contract": {
                "kind": "deterministic_keyset",
                "cursor": "opaque_batch_m4_family_cursor_v1",
                "limit": {"default": 50, "minimum": 1, "maximum": 100},
                "scope_binding": [
                    "family", "ingest_run_id", "family_contract_version",
                    "operation", "filters", "query",
                ],
            },
            "coverage_semantics": {
                "coverage": RUNTIME_COVERAGE,
                "corpus_complete": "false for this first ten-family wave; each family is complete only for its explicitly selected top-level JSON file",
                "full_7_0_claimed": False,
                "adjacent_inventory_only": True,
            },
            "search_contract": {
                "scope": spec.container_scope,
                "fields": list(spec.search_fields),
                "query": "required_nonempty_literal_containing_at_least_one_non_whitespace_character",
                "matching": "case_sensitive_literal_instr_on_raw_record_json",
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
                "receipt_trust": "file_prefilter_not_authentication",
                "receipt_contract": "genshin-lore-batch-m4-wave-release-receipt",
                "receipt_version": 1,
                "schema_version": SCHEMA_VERSION,
                "schema_contract": RUNTIME_SCHEMA_CONTRACT,
                "wave_contract": RUNTIME_WAVE_CONTRACT,
                "runtime_manifest": "tracked_manifest_canonical_business_oracle",
                "canonical_rowset_validation": True,
                "provenance_table_validation": True,
                "batch_metadata_validation": True,
                "call_time_stability": {
                    "database": "identity_only_no_rehash",
                    "receipt": "identity_and_streaming_sha256",
                    "sidecars": "must_remain_absent",
                    "entry_and_exit_checks": True,
                },
            },
            "runtime_oracle": self.runtime_oracle,
            "runtime_stability": self.runtime_stability.to_json() if self.runtime_stability else None,
            "provider_registry": {
                "family_provider_registry_changed": True,
                "top_level_tool_registry_changed": False,
                "top_level_tool_count": 25,
            },
            "evidence_only": True,
            "data_contract": self.family_contract_version,
        }


def _schema16(conn: sqlite3.Connection) -> bool:
    try:
        verify_schema16(conn)
        return True
    except (BatchM4RuntimeOracleError, sqlite3.Error, RuntimeError, TypeError, ValueError):
        return False


def _receipt_fingerprint(path: Path, receipt_path: str | Path | None) -> tuple[dict[str, Any] | None, str | None]:
    if receipt_path is None:
        return None, "explicit M4 release receipt is required"
    try:
        return load_release_receipt(receipt_path, path, require_accepted=True), None
    except (BatchM4ReceiptError, OSError, ValueError) as exc:
        return None, str(exc)


def _receipt_matches_manifest(
    receipt: Mapping[str, Any], manifest: Mapping[str, Any],
) -> None:
    wave = manifest.get("wave")
    if not isinstance(wave, Mapping):
        raise BatchM4RuntimeOracleError("runtime manifest wave is unavailable")
    if receipt.get("wave_id") != wave.get("id"):
        raise BatchM4ReceiptError("M4 receipt wave id does not match tracked manifest")
    if receipt.get("wave_manifest_sha256") != wave.get("manifest_sha256"):
        raise BatchM4ReceiptError("M4 receipt wave manifest does not match tracked manifest")
    families = manifest.get("families")
    runs = receipt.get("family_runs")
    contracts = receipt.get("family_contracts")
    if not isinstance(families, list) or not isinstance(runs, Mapping) or not isinstance(contracts, Mapping):
        raise BatchM4ReceiptError("M4 receipt family bindings are unavailable")
    for item in families:
        if not isinstance(item, Mapping):
            raise BatchM4ReceiptError("M4 runtime manifest family entry is invalid")
        family = item.get("family")
        if runs.get(family) != item.get("ingest_run_id"):
            raise BatchM4ReceiptError(f"M4 receipt run binding mismatch for {family}")
        if contracts.get(family) != item.get("family_contract_version"):
            raise BatchM4ReceiptError(f"M4 receipt contract binding mismatch for {family}")


def inspect_batch_m4_capabilities(
    db: ReadonlyDatabase,
    receipt_path: str | Path | None = None,
    manifest_path: str | Path = RUNTIME_MANIFEST_PATH,
) -> dict[str, BatchM4CapabilitySnapshot]:
    """Qualify all M4 families from the tracked runtime oracle.

    The receipt is deliberately only an exact-file prefilter.  Eligibility,
    row counts and digests come from the tracked manifest and the independent
    streaming oracle; a receipt cannot promote or substitute another run.
    """

    schema_ok = _schema16(db.conn)
    receipt, receipt_error = _receipt_fingerprint(db.path, receipt_path) if schema_ok else (
        None, "Schema 16 structural contract is not available"
    )
    result: dict[str, BatchM4CapabilitySnapshot] = {}
    manifest: Mapping[str, Any] | None = None
    oracle_summary: dict[str, Any] | None = None
    stability_json = ""
    qualification_error = receipt_error
    if qualification_error is None and receipt is not None:
        try:
            manifest = load_runtime_manifest(manifest_path)
            _receipt_matches_manifest(receipt, manifest)
            oracle_result = BatchM4RuntimeOracle(db, Path(manifest_path).resolve()).verify_startup()
            oracle_summary = {
                "status": oracle_result.get("status"),
                "manifest_sha256": oracle_result.get("manifest_sha256"),
                "schema_version": oracle_result.get("schema_version"),
                "wave_id": oracle_result.get("wave_id"),
                "wave_manifest_sha256": oracle_result.get("wave_manifest_sha256"),
                "family_count": oracle_result.get("family_count"),
                "canonical_rowsets_verified": True,
                "provenance_tables_verified": True,
                "batch_metadata_verified": True,
            }
            stability = RuntimeStabilitySnapshot.capture(db.path, receipt_path)  # type: ignore[arg-type]
            stability_json = canonical_json(stability.to_json())
        except (
            BatchM4RuntimeOracleError, BatchM4ReceiptError, RuntimeStabilityError,
            OSError, RuntimeError, sqlite3.Error, TypeError, ValueError, KeyError,
        ) as exc:
            qualification_error = str(exc)
    for family in M4_FAMILY_ORDER:
        contract = FAMILY_CONTRACTS[family]
        runs: tuple[dict[str, Any], ...] = ()
        reason = qualification_error
        if reason is None and manifest is not None and oracle_summary is not None:
            item = next((item for item in manifest.get("families", ())
                         if isinstance(item, Mapping) and item.get("family") == family), None)
            # Oracle startup returns one summary per family.  The manifest is
            # the source of all bindings; only the independently recomputed
            # row count and canonical digest are copied into capability data.
            if not isinstance(item, Mapping):
                reason = "M4 runtime manifest family is unavailable"
            else:
                run_id = item.get("ingest_run_id")
                family_contract = item.get("family_contract_version")
                if not isinstance(run_id, str) or family_contract != contract:
                    reason = "M4 runtime manifest family binding is invalid"
                else:
                    # The startup oracle checked every family in fixed order;
                    # no independent DB count is trusted here.
                    table = item.get("table")
                    count = table.get("row_count") if isinstance(table, Mapping) else None
                    rowset = table.get("logical_row_sha256") if isinstance(table, Mapping) else None
                    runs = ({
                        "ingest_run_id": run_id,
                        "snapshot_id": SNAPSHOT_ID,
                        "parser_build_id": item.get("parser_build_id"),
                        "family_contract_version": contract,
                        "coverage": RUNTIME_COVERAGE,
                        "corpus_complete": False,
                        "row_count": count,
                        "logical_row_sha256": rowset,
                        "wave_id": oracle_summary["wave_id"],
                        "release_receipt": receipt.get("receipt_id"),
                        "runtime_manifest_sha256": oracle_summary["manifest_sha256"],
                    },)
        result[family] = BatchM4CapabilitySnapshot(
            family, contract, schema_ok, runs, reason, receipt,
            oracle_summary, stability_json,
        )
    return result


class BatchM4FamilyRepository:
    """One family-specific view over one M4 row-occurrence table."""

    def __init__(self, db: ReadonlyDatabase, capability: BatchM4CapabilitySnapshot) -> None:
        self.db = db
        self._capability = capability
        self.family = capability.family
        self._spec = FAMILY_RETRIEVAL_SPECS[self.family]
        if capability.family_contract_version != self._spec.family_contract_version:
            raise ValueError("capability contract does not match the family retrieval spec")
        self.family_contract_version = self._spec.family_contract_version
        self._table = self._spec.table
        self._order = self._spec.order
        self._eligible = frozenset(item["ingest_run_id"] for item in capability.eligible_runs)
        self._stability = capability.runtime_stability

    def capability(self) -> dict[str, Any]:
        return self._capability.to_dict()

    def _check(self, run: Any, version: Any, operation: str) -> FamilyEvidenceResult | None:
        requested = _text(run, "ingest_run_id")
        if version != self._spec.family_contract_version:
            return _result(self.family, requested, "not_found", {
                "status": "NotFound", "reason": "family_contract_version_mismatch",
                "lookup": {"operation": operation, "requested": version, "supported": self._spec.family_contract_version},
            }, eligible=False)
        if requested not in self._eligible:
            return _result(self.family, requested, "not_found", {
                "status": "NotFound", "reason": "ingest_run_ineligible" if self.db.conn.execute("SELECT 1 FROM ingest_run WHERE id=?", (requested,)).fetchone() else "ingest_run_not_found",
                "lookup": {"operation": operation, "ingest_run_id": requested},
            }, eligible=False)
        return None

    @stable_routed_call("list_family_evidence")
    def list_evidence(self, *, ingest_run_id: str, family_contract_version: str, filters: dict[str, Any] | None = None, cursor: str | None = None, limit: int = 50) -> FamilyEvidenceResult:
        error = self._check(ingest_run_id, family_contract_version, "list_family_evidence")
        if error:
            return error
        selected, limit = _filters(filters, self._spec.list_filters), _limit(limit)
        scope = {"family": self.family, "run": ingest_run_id, "contract": family_contract_version, "operation": "list", "filters": selected, "query": None}
        last = _decode_cursor(cursor, scope, key_count=len(self._order))
        where = [f"{_q('ingest_run_id')}=?"]
        params: list[Any] = [ingest_run_id]
        for key in self._spec.list_filters:
            if key in selected:
                where.append(f"{_q(key)}=?")
                params.append(selected[key])
        if last is not None:
            keyset_sql, keyset_params = _after_key_clause(self._order, last)
            where.append(keyset_sql)
            params.extend(keyset_params)
        order_sql = ",".join(_q(column) for column in self._order)
        rows = self.db.conn.execute(f"SELECT * FROM {_q(self._table)} WHERE {' AND '.join(where)} ORDER BY {order_sql} LIMIT ?", (*params, limit + 1)).fetchall()
        page, more = rows[:limit], len(rows) > limit
        next_cursor = _encode_cursor(scope, tuple(page[-1][column] for column in self._order)) if more and page else None
        return _result(self.family, ingest_run_id, "ok", {
            "operation": "list", "filters": selected,
            "items": [_row_item(row) for row in page],
            "pagination": {"kind": "keyset", "limit": limit, "next_cursor": next_cursor},
        }, eligible=True)

    @stable_routed_call("get_family_evidence")
    def get_evidence(self, *, ingest_run_id: str, family_contract_version: str, occurrence_id: str) -> FamilyEvidenceResult:
        error = self._check(ingest_run_id, family_contract_version, "get_family_evidence")
        if error:
            return error
        requested = _text(occurrence_id, "occurrence_id")
        row = self.db.conn.execute(
            f"SELECT * FROM {_q(self._table)} WHERE {_q('ingest_run_id')}=? AND {_q('occurrence_id')}=?",
            (ingest_run_id, requested),
        ).fetchone()
        if row is None:
            return _result(self.family, ingest_run_id, "not_found", {"status": "NotFound", "reason": "occurrence_not_found", "lookup": {"operation": "get", "occurrence_id": requested}}, eligible=True)
        return _result(self.family, ingest_run_id, "ok", {
            "operation": "get",
            "container_scope": self._spec.container_scope,
            "occurrence": {"row": _row_item(row)},
        }, eligible=True)

    @stable_routed_call("search_family_text")
    def search_text(self, *, ingest_run_id: str, family_contract_version: str, query: str, filters: dict[str, Any] | None = None, cursor: str | None = None, limit: int = 50) -> FamilyEvidenceResult:
        error = self._check(ingest_run_id, family_contract_version, "search_family_text")
        if error:
            return error
        literal = _text(query, "query")
        if not literal.strip():
            raise ValueError("query must contain a non-whitespace character")
        selected, limit = _filters(filters, self._spec.list_filters), _limit(limit)
        scope = {"family": self.family, "run": ingest_run_id, "contract": family_contract_version, "operation": "search_text", "filters": selected, "query": literal}
        last = _decode_cursor(cursor, scope, key_count=len(self._order))
        search_sql = " OR ".join(f"instr({_q(field)},?)>0" for field in self._spec.search_fields)
        where = [f"{_q('ingest_run_id')}=?", f"({search_sql})"]
        params: list[Any] = [ingest_run_id, *([literal] * len(self._spec.search_fields))]
        for key in self._spec.list_filters:
            if key in selected:
                where.append(f"{_q(key)}=?")
                params.append(selected[key])
        if last is not None:
            keyset_sql, keyset_params = _after_key_clause(self._order, last)
            where.append(keyset_sql)
            params.extend(keyset_params)
        order_sql = ",".join(_q(column) for column in self._order)
        rows = self.db.conn.execute(f"SELECT * FROM {_q(self._table)} WHERE {' AND '.join(where)} ORDER BY {order_sql} LIMIT ?", (*params, limit + 1)).fetchall()
        page, more = rows[:limit], len(rows) > limit
        items = []
        for row in page:
            item = _row_item(row)
            item["matches"] = [{"start": start, "end": end} for start, end in text_occurrences(str(row["raw_record_json"]), literal)]
            items.append(item)
        next_cursor = _encode_cursor(scope, tuple(page[-1][column] for column in self._order)) if more and page else None
        return _result(self.family, ingest_run_id, "ok", {
            "operation": "search_text",
            "query": {"literal": literal, "semantics": "case_sensitive_literal_instr_on_raw_record_json"},
            "filters": selected, "items": items,
            "pagination": {"kind": "keyset", "limit": limit, "next_cursor": next_cursor},
        }, eligible=True)


def build_batch_m4_repositories(
    db: ReadonlyDatabase,
    receipt_path: str | Path | None = None,
    manifest_path: str | Path = RUNTIME_MANIFEST_PATH,
) -> tuple[BatchM4FamilyRepository, ...]:
    snapshots = inspect_batch_m4_capabilities(db, receipt_path, manifest_path)
    return tuple(BatchM4FamilyRepository(db, snapshots[family]) for family in M4_FAMILY_ORDER)


__all__ = [
    "BatchM4CapabilitySnapshot", "BatchM4FamilyRepository", "BatchM4RetrievalError",
    "FAMILY_CONTRACTS", "FAMILY_RETRIEVAL_SPECS", "FAMILY_TABLES",
    "FamilyRetrievalSpec", "build_batch_m4_repositories",
    "inspect_batch_m4_capabilities",
]
