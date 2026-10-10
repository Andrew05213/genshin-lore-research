"""Family-routed read-only retrieval for an accepted Batch M4 wave.

The mechanical row shape is shared by the first ten batch families, but the
family contract remains explicit for every provider instance.  This module
does not infer identifiers, relations, text ownership, or cross-family
identity.  A provider returns one physical row occurrence at a time and only
searches the lexical ``raw_record_json`` stored by that family projection.

An M4 report is an explicit release qualification input.  A pending/non-
product report is deliberately represented as an ineligible provider; it is
never silently promoted by the runtime.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ._readonly_database import ReadonlyDatabase
from .batch_m4_release import M4_COVERAGE, M4_FAMILY_ORDER, M4_REPORT_VERSION, SCHEMA_VERSION
from .family_evidence import FamilyEvidenceResult
from .runtime_stability import RuntimeStabilitySnapshot, stable_routed_call
from .util import canonical_json, text_occurrences


BATCH_LIST_FILTERS = (
    "source_file_path",
    "source_pointer",
    "source_ordinal",
    "record_status",
    "raw_record_sha256",
)
BATCH_SEARCH_FILTERS = (
    "source_file_path",
    "source_pointer",
    "source_ordinal",
    "record_status",
)
_ROW_COLUMNS = (
    "occurrence_id",
    "family",
    "family_contract_version",
    "ingest_run_id",
    "snapshot_id",
    "parser_build_id",
    "source_file_path",
    "source_record_id",
    "source_pointer",
    "source_ordinal",
    "raw_record_json",
    "raw_record_sha256",
    "raw_key_observations_json",
    "record_status",
    "metadata_json",
)
_ORDER_COLUMNS = (
    "source_file_path",
    "source_ordinal",
    "source_pointer",
    "occurrence_id",
)
_ACCEPTED_REPORT_STATUSES = frozenset({"accepted", "pass"})


@dataclass(frozen=True)
class BatchFamilyContract:
    """The explicit transport/query contract for one M4 family table."""

    family: str
    family_contract_version: str
    table_name: str
    list_filters: tuple[str, ...] = BATCH_LIST_FILTERS
    search_filters: tuple[str, ...] = BATCH_SEARCH_FILTERS


# Keep this table explicit rather than deriving family names or contracts from
# table names.  The row fields are the M3 family contract's raw observations;
# no field is interpreted as a cross-family logical identity.
BATCH_FAMILY_CONTRACTS: tuple[BatchFamilyContract, ...] = tuple(
    BatchFamilyContract(
        family,
        f"{family.replace('_', '-')}-evidence-v1",
        f"{family}_row_occurrence",
    )
    for family in M4_FAMILY_ORDER
)
BATCH_CONTRACT_BY_FAMILY = {
    contract.family: contract for contract in BATCH_FAMILY_CONTRACTS
}


class BatchWaveCapabilityError(ValueError):
    """The explicit M4 release qualification cannot be used."""


@dataclass(frozen=True)
class BatchWaveCapabilitySnapshot:
    family: str
    family_contract_version: str
    table_name: str
    schema_supported: bool
    coverage: str = "unavailable"
    corpus_complete: bool = False
    _eligible_runs_json: str = "[]"
    unavailable_reason: str | None = None
    release_report_path: str | None = None
    release_report_sha256: str | None = None
    candidate_sha256: str | None = None
    wave_id: str | None = None
    _stability_json: str = ""
    _release_metadata_json: str = "{}"

    @property
    def eligible_runs(self) -> list[dict[str, Any]]:
        try:
            value = json.loads(self._eligible_runs_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        return value if isinstance(value, list) else []

    @property
    def evidence_available(self) -> bool:
        return bool(self.eligible_runs)

    @property
    def runtime_stability(self) -> RuntimeStabilitySnapshot | None:
        if not self._stability_json:
            return None
        try:
            return RuntimeStabilitySnapshot.from_json(json.loads(self._stability_json))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_contract_version": self.family_contract_version,
            "schema_supported": self.schema_supported,
            "evidence_available": self.evidence_available,
            "unavailable_reason": self.unavailable_reason,
            "eligible_runs": self.eligible_runs,
            "supported_operations": [
                "list_family_evidence",
                "get_family_evidence",
                "search_family_text",
            ],
            "supported_filters": {
                "list_family_evidence": list(BATCH_CONTRACT_BY_FAMILY[self.family].list_filters),
                "get_family_evidence": [],
                "search_family_text": list(BATCH_CONTRACT_BY_FAMILY[self.family].search_filters),
            },
            "pagination_contract": {
                "kind": "deterministic_keyset",
                "cursor": "opaque_batch_wave_cursor_v1",
                "limit": {"default": 50, "minimum": 1, "maximum": 100},
                "scope_binding": [
                    "family", "ingest_run_id", "family_contract_version",
                    "operation", "filters", "query",
                ],
            },
            "coverage_semantics": {
                "coverage": self.coverage,
                "corpus_complete": (
                    "copied from explicit M4 release qualification; false does not claim full 7.0"
                ),
                "structured_family": True,
                "full_7_0_claimed": False,
            },
            "search_contract": {
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
                "required": True,
                "report_contract": M4_REPORT_VERSION,
                "report_path": "explicit_server_configuration_only",
                "accepted_statuses": sorted(_ACCEPTED_REPORT_STATUSES),
                "product_accepted_required": True,
                "candidate_path_must_equal_database": True,
                "candidate_sha256_verified": True,
                "wave_metadata_bound": True,
                "family_table_digest_bound": True,
                "receipt_trust": "release-report-is-explicit-gate-output-not-authentication",
                "call_time_stability": {
                    "database": "identity_only_no_rehash",
                    "release_report": "identity_and_streaming_sha256",
                    "sidecars": "must_remain_absent",
                    "entry_and_exit_checks": True,
                },
            },
            "evidence_only": True,
            "data_contract": self.family_contract_version,
            "release": {
                "report_path": self.release_report_path,
                "report_sha256": self.release_report_sha256,
                "candidate_sha256": self.candidate_sha256,
                "wave_id": self.wave_id,
            },
        }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BatchWaveCapabilityError(f"M4 release report is unavailable or invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise BatchWaveCapabilityError("M4 release report must be a JSON object")
    return value, raw


def _path_identity(path: Path) -> tuple[int, int, int, int]:
    info = os.stat(path)
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ValueError("limit must be between 1 and 100")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _filters(value: Any, allowed: Sequence[str]) -> dict[str, str]:
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
    if "source_ordinal" in result and not result["source_ordinal"].isdigit():
        raise ValueError("filters.source_ordinal must be an exact decimal string")
    return result


def _cursor(scope: Mapping[str, Any], last: Sequence[Any]) -> str:
    body = {"v": 1, "scope": dict(scope), "last": list(last)}
    check = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
    return base64.urlsafe_b64encode(
        canonical_json({**body, "check": check}).encode("utf-8")
    ).decode("ascii").rstrip("=")


def _decode_cursor(value: str | None, scope: Mapping[str, Any]) -> list[Any] | None:
    if value is None:
        return None
    _text(value, "cursor")
    try:
        payload = json.loads(
            base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")
        )
        body = {key: payload[key] for key in ("v", "scope", "last")}
        valid = payload.get("check") == hashlib.sha256(
            canonical_json(body).encode("utf-8")
        ).hexdigest()
        last = body["last"]
        if body["v"] != 1 or body["scope"] != dict(scope) or not valid:
            raise ValueError
        if (
            not isinstance(last, list)
            or len(last) != len(_ORDER_COLUMNS)
            or not isinstance(last[0], str)
            or not isinstance(last[1], int)
            or isinstance(last[1], bool)
            or last[1] < 0
            or not isinstance(last[2], str)
            or not isinstance(last[3], str)
            or not last[3]
        ):
            raise ValueError
        return last
    except (binascii.Error, KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("cursor is invalid or belongs to another evidence scope") from exc


def _json_value(raw: Any, default: Any = None) -> Any:
    if raw is None:
        return default
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("stored family JSON field is invalid") from exc


def _row_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: (_json_value(row[key]) if key.endswith("_json") else row[key])
        for key in _ROW_COLUMNS
    }


def _qualified_family(
    db: ReadonlyDatabase,
    report_path: Path,
    report: Mapping[str, Any],
    report_sha256: str,
    contract: BatchFamilyContract,
) -> BatchWaveCapabilitySnapshot:
    try:
        candidate = report["candidate"]
        if not isinstance(candidate, Mapping):
            raise BatchWaveCapabilityError("M4 report candidate binding is missing")
        candidate_path = Path(str(candidate["path"])).resolve()
        if candidate_path != db.path.resolve():
            raise BatchWaveCapabilityError("M4 candidate path does not equal the runtime database")
        size = int(candidate["size_bytes"])
        candidate_sha = str(candidate["sha256"])
        if db.path.stat().st_size != size or _sha256_file(db.path) != candidate_sha:
            raise BatchWaveCapabilityError("M4 candidate size/SHA does not match the runtime database")
        if report.get("report_version") != M4_REPORT_VERSION:
            raise BatchWaveCapabilityError("unsupported M4 release report version")
        if report.get("status") not in _ACCEPTED_REPORT_STATUSES:
            raise BatchWaveCapabilityError("M4 release report is not accepted")
        if report.get("product_accepted") is not True:
            raise BatchWaveCapabilityError("M4 release report is not product accepted")
        if report.get("coverage_level") != M4_COVERAGE:
            raise BatchWaveCapabilityError("M4 release coverage contract mismatch")
        if int(report.get("schema_version")) != SCHEMA_VERSION:
            raise BatchWaveCapabilityError("M4 schema version mismatch")
        if report.get("mcp_tool_count") != 25:
            raise BatchWaveCapabilityError("M4 runtime tool count is not the fixed 25-tool contract")
        wave_id = _text(report.get("wave_id"), "wave_id")
        wave = db.conn.execute(
            "SELECT snapshot_id,contract_version,manifest_sha256,target_schema_version,status,metadata_json FROM batch_wave WHERE id=?",
            (wave_id,),
        ).fetchone()
        if wave is None or tuple(wave[:5]) != (
            report["source"]["snapshot_id"],
            "batch-wave-v3",
            report["wave_manifest_sha256"],
            SCHEMA_VERSION,
            "complete",
        ):
            raise BatchWaveCapabilityError("M4 batch wave metadata binding mismatch")
        family_items = report.get("families")
        if not isinstance(family_items, list) or len(family_items) != len(M4_FAMILY_ORDER):
            raise BatchWaveCapabilityError("M4 family boundary is not the fixed ten-family wave")
        family_names = tuple(item.get("family") for item in family_items if isinstance(item, Mapping))
        if family_names != M4_FAMILY_ORDER:
            raise BatchWaveCapabilityError("M4 family order/boundary mismatch")
        item = next(item for item in family_items if item["family"] == contract.family)
        if item.get("family_contract_version") != contract.family_contract_version:
            raise BatchWaveCapabilityError("M4 family contract mismatch")
        if item.get("ingest_run_id") is None:
            raise BatchWaveCapabilityError("M4 family exact run is missing")
        table_digest = item.get("table")
        if not isinstance(table_digest, Mapping) or table_digest.get("table_name") != contract.table_name:
            raise BatchWaveCapabilityError("M4 family table digest binding is missing")
        shard = item.get("shard")
        if not isinstance(shard, Mapping):
            raise BatchWaveCapabilityError("M4 family shard binding is missing")
        shard_id = _text(shard.get("id"), "shard.id")
        shard_row = db.conn.execute(
            "SELECT family,family_contract_version,ingest_run_id,manifest_sha256,status,rowset_sha256 FROM batch_shard WHERE id=? AND wave_id=?",
            (shard_id, wave_id),
        ).fetchone()
        if shard_row is None or tuple(shard_row[:5]) != (
            contract.family,
            contract.family_contract_version,
            item["ingest_run_id"],
            report["wave_manifest_sha256"],
            "imported",
        ) or shard_row[5] != table_digest.get("logical_row_sha256"):
            raise BatchWaveCapabilityError("M4 family shard metadata binding mismatch")
        digest_row = db.conn.execute(
            "SELECT row_count,logical_row_sha256 FROM batch_table_digest WHERE wave_id=? AND shard_id=? AND table_name=?",
            (wave_id, shard_id, contract.table_name),
        ).fetchone()
        if digest_row is None or tuple(digest_row) != (
            table_digest.get("row_count"),
            table_digest.get("logical_row_sha256"),
        ):
            raise BatchWaveCapabilityError("M4 family table digest metadata mismatch")
        table_exists = db.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (contract.table_name,),
        ).fetchone()
        if table_exists is None:
            raise BatchWaveCapabilityError("M4 family table is unavailable")
        run = str(item["ingest_run_id"])
        parser = db.conn.execute(
            "SELECT parser_build_id,snapshot_id,status FROM ingest_run WHERE id=?",
            (run,),
        ).fetchone()
        if parser is None or parser[1] != report["source"]["snapshot_id"] or parser[2] != "complete":
            raise BatchWaveCapabilityError("M4 exact family ingest run is unavailable")
        eligible = [{
            "ingest_run_id": run,
            "snapshot_id": report["source"]["snapshot_id"],
            "parser_build_id": str(parser[0]),
            "family_contract_version": contract.family_contract_version,
            "coverage": report["coverage_level"],
            "corpus_complete": bool(report.get("corpus_complete", False)),
            "wave_id": wave_id,
            "release_report_sha256": report_sha256,
            "candidate_sha256": candidate_sha,
        }]
        release_metadata = {
            "status": report["status"],
            "product_accepted": True,
            "coverage_level": report["coverage_level"],
            "corpus_complete": bool(report.get("corpus_complete", False)),
            "wave_id": wave_id,
            "table_name": contract.table_name,
            "row_count": table_digest["row_count"],
            "logical_row_sha256": table_digest["logical_row_sha256"],
        }
        stability = RuntimeStabilitySnapshot.capture(db.path, report_path)
        return BatchWaveCapabilitySnapshot(
            contract.family, contract.family_contract_version, contract.table_name,
            True, str(report["coverage_level"]), bool(report.get("corpus_complete", False)),
            canonical_json(eligible), None, str(report_path), report_sha256,
            candidate_sha, wave_id, canonical_json(stability.to_json()),
            canonical_json(release_metadata),
        )
    except (KeyError, OSError, sqlite3.Error, TypeError, ValueError, RuntimeError) as exc:
        if isinstance(exc, BatchWaveCapabilityError):
            raise
        raise BatchWaveCapabilityError(str(exc)) from exc


def inspect_batch_wave_capability(
    db: ReadonlyDatabase,
    release_report_path: str | Path | None,
    family: str,
) -> BatchWaveCapabilitySnapshot:
    """Qualify one M4 family from an explicitly supplied accepted report."""

    contract = BATCH_CONTRACT_BY_FAMILY.get(family)
    if contract is None:
        raise ValueError(f"unknown batch family: {family}")
    base = BatchWaveCapabilitySnapshot(
        contract.family, contract.family_contract_version, contract.table_name,
        False, unavailable_reason="release_report_missing",
    )
    if release_report_path is None:
        return base
    report_path = Path(release_report_path).resolve()
    try:
        if not report_path.is_file() or report_path.is_symlink():
            raise BatchWaveCapabilityError("M4 release report is not a regular file")
        report, raw = _read_json(report_path)
        report_sha256 = hashlib.sha256(raw).hexdigest()
        return _qualified_family(db, report_path, report, report_sha256, contract)
    except (BatchWaveCapabilityError, OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
        return BatchWaveCapabilitySnapshot(
            contract.family, contract.family_contract_version, contract.table_name,
            False, unavailable_reason=str(exc), release_report_path=str(report_path),
        )


def create_batch_wave_providers(
    db: ReadonlyDatabase,
    release_report_path: str | Path | None,
) -> list["BatchWaveProvider"]:
    """Create one explicit provider per M4 family.

    If qualification is absent or pending, all ten provider names remain
    discoverable with no eligible run.  The router therefore reports a legal
    NotFound/ineligible result instead of treating an unqualified candidate as
    product evidence.
    """

    return [
        BatchWaveProvider(
            db,
            BATCH_CONTRACT_BY_FAMILY[family],
            inspect_batch_wave_capability(db, release_report_path, family),
        )
        for family in M4_FAMILY_ORDER
    ]


def _result(
    capability: BatchWaveCapabilitySnapshot,
    status: str,
    run: str,
    data: dict[str, Any],
) -> FamilyEvidenceResult:
    eligible = capability.evidence_available
    return FamilyEvidenceResult(
        status,
        capability.family,
        run,
        capability.family_contract_version,
        capability.coverage if eligible else "unavailable",
        capability.corpus_complete if eligible else False,
        data,
    )


class BatchWaveProvider:
    """Read-only provider for exactly one M4 family physical-row table."""

    def __init__(
        self,
        db: ReadonlyDatabase,
        contract: BatchFamilyContract,
        capability: BatchWaveCapabilitySnapshot,
    ) -> None:
        if contract.family != capability.family or contract.family_contract_version != capability.family_contract_version:
            raise ValueError("batch family contract/capability mismatch")
        self.db = db
        self.contract = contract
        self.family = contract.family
        self.family_contract_version = contract.family_contract_version
        self._capability = capability
        self._eligible = frozenset(
            str(item["ingest_run_id"]) for item in capability.eligible_runs
        )
        self._stability = capability.runtime_stability

    def capability(self) -> dict[str, Any]:
        return self._capability.to_dict()

    def _contract(self, run: Any, version: Any, operation: str) -> FamilyEvidenceResult | None:
        requested_run = run if isinstance(run, str) and run else "invalid"
        if version == self.family_contract_version:
            return None
        return _result(
            self._capability,
            "not_found",
            requested_run,
            {
                "status": "NotFound",
                "reason": "family_contract_version_mismatch",
                "lookup": {
                    "operation": operation,
                    "requested": version,
                    "supported": self.family_contract_version,
                },
            },
        )

    def _run(self, run: Any, operation: str) -> FamilyEvidenceResult | None:
        requested = _text(run, "ingest_run_id")
        if requested in self._eligible:
            return None
        exists = self.db.conn.execute(
            "SELECT 1 FROM ingest_run WHERE id=?", (requested,)
        ).fetchone() is not None
        return _result(
            self._capability,
            "not_found",
            requested,
            {
                "status": "NotFound",
                "reason": "ingest_run_ineligible" if exists else "ingest_run_not_found",
                "lookup": {"operation": operation, "ingest_run_id": requested},
            },
        )

    def _scope(
        self,
        operation: str,
        run: str,
        filters: Mapping[str, str] | None,
        query: str | None,
    ) -> dict[str, Any]:
        return {
            "family": self.family,
            "ingest_run_id": run,
            "family_contract_version": self.family_contract_version,
            "operation": operation,
            "filters": dict(filters or {}),
            "query": query,
        }

    def _where(
        self,
        run: str,
        filters: Mapping[str, str],
    ) -> tuple[list[str], list[Any]]:
        where = ["family=?", "family_contract_version=?", "ingest_run_id=?"]
        params: list[Any] = [self.family, self.family_contract_version, run]
        for key, value in filters.items():
            column = key
            if key == "source_ordinal":
                params.append(int(value))
            else:
                params.append(value)
            where.append(f"{column}=?")
        return where, params

    @stable_routed_call("list_family_evidence")
    def list_evidence(
        self,
        *,
        ingest_run_id: str,
        family_contract_version: str,
        filters: dict[str, Any] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> FamilyEvidenceResult:
        error = self._contract(ingest_run_id, family_contract_version, "list_family_evidence")
        if error:
            return error
        error = self._run(ingest_run_id, "list_family_evidence")
        if error:
            return error
        selected = _filters(filters, self.contract.list_filters)
        page_limit = _limit(limit)
        scope = self._scope("list", ingest_run_id, selected, None)
        last = _decode_cursor(cursor, scope)
        where, params = self._where(ingest_run_id, selected)
        if last is not None:
            where.append(
                "(source_file_path,source_ordinal,source_pointer,occurrence_id)>(?,?,?,?)"
            )
            params.extend(last)
        columns = ",".join(_ROW_COLUMNS)
        rows = self.db.conn.execute(
            f"SELECT {columns} FROM {self.contract.table_name} WHERE {' AND '.join(where)} "
            "ORDER BY source_file_path,source_ordinal,source_pointer,occurrence_id LIMIT ?",
            (*params, page_limit + 1),
        ).fetchall()
        page = rows[:page_limit]
        more = len(rows) > page_limit
        next_cursor = (
            _cursor(scope, [page[-1][column] for column in _ORDER_COLUMNS])
            if more and page else None
        )
        return _result(
            self._capability,
            "ok",
            ingest_run_id,
            {
                "operation": "list",
                "filters": selected,
                "items": [_row_payload(row) for row in page],
                "pagination": {
                    "kind": "deterministic_keyset",
                    "limit": page_limit,
                    "next_cursor": next_cursor,
                },
            },
        )

    @stable_routed_call("get_family_evidence")
    def get_evidence(
        self,
        *,
        ingest_run_id: str,
        family_contract_version: str,
        occurrence_id: str,
    ) -> FamilyEvidenceResult:
        error = self._contract(ingest_run_id, family_contract_version, "get_family_evidence")
        if error:
            return error
        error = self._run(ingest_run_id, "get_family_evidence")
        if error:
            return error
        requested = _text(occurrence_id, "occurrence_id")
        columns = ",".join(_ROW_COLUMNS)
        row = self.db.conn.execute(
            f"SELECT {columns} FROM {self.contract.table_name} "
            "WHERE family=? AND family_contract_version=? AND ingest_run_id=? AND occurrence_id=?",
            (self.family, self.family_contract_version, ingest_run_id, requested),
        ).fetchone()
        if row is None:
            return _result(
                self._capability,
                "not_found",
                ingest_run_id,
                {
                    "status": "NotFound",
                    "reason": "occurrence_not_found",
                    "lookup": {"operation": "get", "occurrence_id": requested},
                },
            )
        return _result(
            self._capability,
            "ok",
            ingest_run_id,
            {
                "operation": "get",
                "container_scope": f"single_{self.family}_row_occurrence",
                "occurrence": _row_payload(row),
            },
        )

    @stable_routed_call("search_family_text")
    def search_text(
        self,
        *,
        ingest_run_id: str,
        family_contract_version: str,
        query: str,
        filters: dict[str, Any] | None = None,
        cursor: str | None = None,
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
        selected = _filters(filters, self.contract.search_filters)
        page_limit = _limit(limit)
        scope = self._scope("search_text", ingest_run_id, selected, literal)
        last = _decode_cursor(cursor, scope)
        where, params = self._where(ingest_run_id, selected)
        where.append("instr(raw_record_json,?)>0")
        params.append(literal)
        if last is not None:
            where.append(
                "(source_file_path,source_ordinal,source_pointer,occurrence_id)>(?,?,?,?)"
            )
            params.extend(last)
        columns = ",".join(_ROW_COLUMNS)
        rows = self.db.conn.execute(
            f"SELECT {columns} FROM {self.contract.table_name} WHERE {' AND '.join(where)} "
            "ORDER BY source_file_path,source_ordinal,source_pointer,occurrence_id LIMIT ?",
            (*params, page_limit + 1),
        ).fetchall()
        page = rows[:page_limit]
        more = len(rows) > page_limit
        items = []
        for row in page:
            payload = _row_payload(row)
            items.append({
                "occurrence_id": payload["occurrence_id"],
                "source_pointer": payload["source_pointer"],
                "raw_record_json": payload["raw_record_json"],
                "matches": [
                    {"start": start, "end": end}
                    for start, end in text_occurrences(payload["raw_record_json"], literal)
                ],
                "row": payload,
            })
        next_cursor = (
            _cursor(scope, [page[-1][column] for column in _ORDER_COLUMNS])
            if more and page else None
        )
        return _result(
            self._capability,
            "ok",
            ingest_run_id,
            {
                "operation": "search_text",
                "query": {
                    "literal": literal,
                    "semantics": "case_sensitive_literal_instr_on_raw_record_json",
                },
                "filters": selected,
                "items": items,
                "pagination": {
                    "kind": "deterministic_keyset",
                    "limit": page_limit,
                    "next_cursor": next_cursor,
                },
            },
        )


__all__ = [
    "BATCH_CONTRACT_BY_FAMILY",
    "BATCH_FAMILY_CONTRACTS",
    "BatchFamilyContract",
    "BatchWaveCapabilitySnapshot",
    "BatchWaveProvider",
    "create_batch_wave_providers",
    "inspect_batch_wave_capability",
]
