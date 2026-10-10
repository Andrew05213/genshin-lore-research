"""Accepted Schema17 M5E family-specific routed providers.

The expensive M5E runtime qualification is supplied by the caller and shared
by all twenty-one providers.  Wave01 delegates only its family-local query
contract to the M4 repository; Wave02 delegates only its declaration and
family-local query contract to the M5D repository.  Neither delegate performs
a second release qualification, and no cross-family identity is introduced.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from ._readonly_database import ReadonlyDatabase
from .batch_m4_retrieval import (
    BatchM4CapabilitySnapshot,
    BatchM4FamilyRepository,
    FAMILY_RETRIEVAL_SPECS,
)
from .batch_m4_runtime_oracle import M4_FAMILY_ORDER
from .batch_m5d_retrieval import BatchM5DFamilyRepository
from .batch_m5d_runtime import (
    M5D_FAMILY_ORDER,
    M5DFamilyDeclaration,
    M5DQualification,
    load_family_declarations,
)
from .batch_m5e_receipt import M5E_COMPATIBILITY_ORDER
from .batch_m5e_runtime_oracle import (
    BatchM5ERuntimeOracleError,
    M5E_COVERAGE,
    M5E_FAMILY_ORDER,
    M5E_RUNTIME_MANIFEST_PATH,
    M5EStabilitySnapshot,
    M5E_SCHEMA_CONTRACT,
    M5E_SCHEMA_VERSION,
    M5E_PROVIDER_REGISTRY,
    canonical_manifest_sha256,
    M5EQualification,
    load_runtime_manifest,
)
from .family_evidence import FamilyEvidenceResult
from .runtime_stability import RuntimeStabilityError, unavailable_result


class BatchM5ERetrievalError(RuntimeError):
    """The accepted M5E provider bundle is unavailable or inconsistent."""


_IDENTIFIER = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
_SHA256_LENGTH = 64


def _absolute(path: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _manifest_items(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    families = manifest.get("families")
    if not isinstance(families, list) or tuple(
        item.get("family") for item in families if isinstance(item, Mapping)
    ) != M5E_FAMILY_ORDER:
        raise BatchM5ERetrievalError("M5E manifest family order is not the fixed 21-family contract")
    result: dict[str, Mapping[str, Any]] = {}
    for item in families:
        if not isinstance(item, Mapping):
            raise BatchM5ERetrievalError("M5E family declaration is invalid")
        family = item.get("family")
        if not isinstance(family, str) or family in result:
            raise BatchM5ERetrievalError("M5E family declaration is duplicated")
        result[family] = item
    return result


def _validate_manifest_binding(
    manifest: Mapping[str, Any],
    qualification: M5EQualification,
    manifest_path: Path,
) -> dict[str, Mapping[str, Any]]:
    if manifest.get("schema_version") != M5E_SCHEMA_VERSION or manifest.get("schema_contract") != M5E_SCHEMA_CONTRACT:
        raise BatchM5ERetrievalError("M5E provider requires Schema17")
    if canonical_manifest_sha256(manifest) != manifest.get("manifest_sha256"):
        raise BatchM5ERetrievalError("M5E tracked manifest canonical seal mismatch")
    if manifest.get("product_accepted") is not True or manifest.get("acceptance_candidate") is not False:
        raise BatchM5ERetrievalError("M5E provider requires an accepted runtime manifest")
    if manifest.get("corpus_complete") is not False:
        raise BatchM5ERetrievalError("M5E outer corpus completeness must remain false")
    if qualification.status != "verified" or qualification.product_accepted is not True:
        raise BatchM5ERetrievalError("M5E provider requires a verified product qualification")
    if qualification.manifest_sha256 != manifest.get("manifest_sha256"):
        raise BatchM5ERetrievalError("M5E qualification is not bound to the manifest")
    if Path(qualification.manifest_path).resolve() != manifest_path.resolve():
        raise BatchM5ERetrievalError("M5E qualification manifest path is not tracked")
    if not isinstance(qualification.receipt_id, str) or not qualification.receipt_id:
        raise BatchM5ERetrievalError("M5E qualification receipt identity is missing")
    if set(qualification.family_row_counts) != set(M5E_FAMILY_ORDER):
        raise BatchM5ERetrievalError("M5E qualification family set is incomplete")
    return _manifest_items(manifest)


def _m4_delegate(
    db: ReadonlyDatabase,
    item: Mapping[str, Any],
    qualification: M5EQualification,
    manifest: Mapping[str, Any],
) -> BatchM4FamilyRepository:
    family = str(item["family"])
    spec = FAMILY_RETRIEVAL_SPECS.get(family)
    if spec is None or item.get("family_contract_version") != spec.family_contract_version:
        raise BatchM5ERetrievalError(f"M4 retrieval contract mismatch: {family}")
    tables = item.get("tables")
    if not isinstance(tables, list) or tuple(
        table.get("table_name") for table in tables if isinstance(table, Mapping)
    ) != (spec.table,):
        raise BatchM5ERetrievalError(f"M4 table declaration mismatch: {family}")
    table = tables[0]
    run_id = item.get("ingest_run_id")
    if not isinstance(run_id, str) or not run_id:
        raise BatchM5ERetrievalError(f"M4 run declaration is invalid: {family}")
    eligible = {
        "ingest_run_id": run_id,
        "snapshot_id": manifest.get("snapshot_id"),
        "parser_build_id": item.get("parser_build_id"),
        "family_contract_version": spec.family_contract_version,
        "coverage": M5E_COVERAGE,
        "corpus_complete": False,
        "wave_id": manifest["wave"]["id"],
        "row_count": table.get("row_count"),
        "logical_row_sha256": table.get("logical_row_sha256"),
        "release_receipt": qualification.receipt_id,
        "runtime_manifest_sha256": qualification.manifest_sha256,
    }
    snapshot = BatchM4CapabilitySnapshot(
        family=family,
        family_contract_version=spec.family_contract_version,
        schema_supported=True,
        eligible_runs=(eligible,),
        unavailable_reason=None,
        receipt={"receipt_id": qualification.receipt_id},
        runtime_oracle={
            "status": "verified",
            "schema_version": M5E_SCHEMA_VERSION,
            "manifest_sha256": qualification.manifest_sha256,
            "wave_id": manifest["wave"]["id"],
            "family_count": len(M4_FAMILY_ORDER),
        },
        _stability_json="",
    )
    return BatchM4FamilyRepository(db, snapshot)


def _validate_m5d_declaration(
    item: Mapping[str, Any],
    declaration: M5DFamilyDeclaration,
) -> None:
    family = declaration.family
    if item.get("family_contract_version") != declaration.family_contract_version:
        raise BatchM5ERetrievalError(f"M5D family contract mismatch: {family}")
    if item.get("ingest_run_id") != declaration.ingest_run_id:
        raise BatchM5ERetrievalError(f"M5D ingest run mismatch: {family}")
    if item.get("primary_table") != declaration.table_name:
        raise BatchM5ERetrievalError(f"M5D primary table mismatch: {family}")
    if tuple(item.get("operations") or ()) != declaration.operations:
        raise BatchM5ERetrievalError(f"M5D operation declaration mismatch: {family}")
    tables = item.get("tables")
    if not isinstance(tables, list) or tuple(
        table.get("table_name") for table in tables if isinstance(table, Mapping)
    ) != declaration.table_names:
        raise BatchM5ERetrievalError(f"M5D table declaration mismatch: {family}")


def _m5d_delegate(
    db: ReadonlyDatabase,
    item: Mapping[str, Any],
    qualification: M5EQualification,
    manifest: Mapping[str, Any],
    declaration: M5DFamilyDeclaration,
) -> BatchM5DFamilyRepository:
    _validate_m5d_declaration(item, declaration)
    family = declaration.family
    m5d_qualification = M5DQualification(
        status="verified",
        report_path=str(qualification.receipt_path),
        manifest_path=str(qualification.manifest_path),
        verification_path="",
        candidate_path=str(qualification.database_path),
        candidate_sha256=qualification.candidate_sha256 or ("0" * 64),
        candidate_size_bytes=qualification.candidate_size_bytes or 1,
        wave_id=str(manifest["wave"]["id"]),
        snapshot_id=str(manifest["snapshot_id"]),
        parser_build_id=str(item["parser_build_id"]),
        manifest_sha256=qualification.manifest_sha256,
        report_sha256="0" * 64,
        verification_sha256="0" * 64,
        family_row_counts=qualification.family_row_counts,
        stability=None,  # type: ignore[arg-type]
        report={},
        manifest=manifest,
        verification={},
    )
    return BatchM5DFamilyRepository(db, declaration, m5d_qualification)


class BatchM5EAcceptedFamilyProvider:
    """One accepted family provider sharing the M5E qualification snapshot."""

    def __init__(
        self,
        db: ReadonlyDatabase,
        item: Mapping[str, Any],
        qualification: M5EQualification,
        manifest: Mapping[str, Any],
        stability: M5EStabilitySnapshot,
        delegate: BatchM4FamilyRepository | BatchM5DFamilyRepository,
        report_path: Path,
        companion_paths: Mapping[str, Path],
    ) -> None:
        self.db = db
        self.item = item
        self.qualification = qualification
        self.manifest = manifest
        self._stability = stability
        self._delegate = delegate
        self.report_path = report_path
        self.companion_paths = dict(companion_paths)
        self.family = str(item["family"])
        self.family_contract_version = str(item["family_contract_version"])
        self.wave = str(item.get("wave"))
        self.declaration = getattr(delegate, "declaration", None)
        self.spec = FAMILY_RETRIEVAL_SPECS.get(self.family)

    def _unsupported_filters(self) -> dict[str, list[Any]]:
        if self.spec is not None:
            return {
                "list_family_evidence": list(self.spec.list_filters),
                "get_family_evidence": [],
                "search_family_text": list(self.spec.list_filters),
            }
        declaration = self.declaration
        if declaration is None:
            return {
                "list_family_evidence": [],
                "get_family_evidence": [],
                "search_family_text": [],
            }
        return {
            operation: [
                dict(item)
                for item in declaration.filters
                if operation in item.get("supported_operations", ())
            ]
            for operation in (
                "list_family_evidence",
                "get_family_evidence",
                "search_family_text",
            )
        }

    def _capability(self, *, unavailable_reason: str | None = None) -> dict[str, Any]:
        accepted = unavailable_reason is None
        if self.spec is not None:
            table_names = [self.spec.table]
            ordering = list(self.spec.order)
            supported_filters = self._unsupported_filters()
            search_fields = list(self.spec.search_fields)
            cursor = "opaque_batch_m5e_m4_family_cursor_v1"
            source = "batch_m4_retrieval"
        else:
            declaration = self.declaration
            assert declaration is not None
            table_names = list(declaration.table_names)
            ordering = list(declaration.sort)
            supported_filters = self._unsupported_filters()
            search = declaration.search
            search_fields = list(search.get("owner_fields", ())) if isinstance(search, Mapping) else []
            cursor = "opaque_batch_m5e_m5d_family_cursor_v1"
            source = "batch_m5d_retrieval"
        run = {
            "ingest_run_id": self.item["ingest_run_id"],
            "snapshot_id": self.manifest["snapshot_id"],
            "parser_build_id": self.item.get("parser_build_id"),
            "family_contract_version": self.family_contract_version,
            "coverage": M5E_COVERAGE,
            "corpus_complete": False,
            "wave_id": self.manifest["wave"]["id"],
            "row_count": self.qualification.family_row_counts[self.family],
            "business_seal": self.qualification.family_seals.get(self.family),
        }
        return {
            "family": self.family,
            "family_contract_version": self.family_contract_version,
            "schema_version": M5E_SCHEMA_VERSION,
            "schema_contract": M5E_SCHEMA_CONTRACT,
            "table": table_names[0],
            "table_names": table_names,
            "ordering": ordering,
            "container_scope": f"single_physical_{self.family}_row_occurrence",
            "schema_supported": True,
            "evidence_available": accepted,
            "unavailable_reason": unavailable_reason,
            "coverage": M5E_COVERAGE if accepted else "unavailable",
            "corpus_complete": False,
            "eligible_exact_runs": [run] if accepted else [],
            "supported_operations": list(self.item.get("operations") or ()),
            "supported_filters": supported_filters,
            "pagination_contract": {
                "kind": "deterministic_keyset",
                "cursor": cursor,
                "limit": {"default": 50, "minimum": 1, "maximum": 100},
                "scope_binding": [
                    "family", "ingest_run_id", "family_contract_version",
                    "operation", "filters", "query", "stable_full_ordering",
                ],
                "stable_full_ordering": {
                    "list_family_evidence": ordering,
                },
            },
            "coverage_semantics": {
                "coverage": M5E_COVERAGE if accepted else "unavailable",
                "wave": self.wave,
                "outer_corpus_complete": False,
                "corpus_complete": False,
                "full_7_0_claimed": False,
                "profile_picture_coverage": (
                    "bounded_canary"
                    if self.family == "profile_picture_archive" else None
                ),
                "query_contract_source": source,
            },
            "search_contract": {
                "supported": "search_family_text" in self.item.get("operations", ()),
                "scope": f"single_physical_{self.family}_row_occurrence",
                "fields": search_fields,
                "query": "required_nonempty_literal_substring",
                "matching": "family_specific_query_contract",
                "occurrence_preserving": True,
                "cross_family_join": False,
            },
            "selection": {
                "ingest_run_id": "required_exact",
                "explicit_ingest_run_required": True,
                "default_run": False,
                "latest_run": False,
                "sibling_fallback": False,
            },
            "release_qualification": {
                "status": self.qualification.status,
                "product_accepted": True,
                "schema_version": M5E_SCHEMA_VERSION,
                "schema_contract": M5E_SCHEMA_CONTRACT,
                "receipt_id": self.qualification.receipt_id,
                "receipt_path": self.qualification.receipt_path,
                "runtime_manifest": self.qualification.manifest_path,
                "runtime_manifest_sha256": self.qualification.manifest_sha256,
                "release_report": str(self.report_path),
                "compatibility_receipts": {
                    name: str(self.companion_paths[name])
                    for name in M5E_COMPATIBILITY_ORDER
                },
                "qualification_count": 1,
                "candidate_hash_verified_once": self.qualification.candidate_sha256 is not None,
            },
            "runtime_stability": self._stability.to_dict() if accepted else None,
            "provider_registry": dict(M5E_PROVIDER_REGISTRY),
            "evidence_only": True,
            "identity_policy": "raw_evidence_identity_is_not_logical_identity; no cross-family merge",
            "data_contract": self.family_contract_version,
        }

    def capability(self) -> dict[str, Any]:
        try:
            self._stability.check()
        except (RuntimeStabilityError, BatchM5ERuntimeOracleError) as exc:
            return self._capability(unavailable_reason=str(exc))
        return self._capability()

    def _normalize_result(self, result: FamilyEvidenceResult) -> FamilyEvidenceResult:
        if not isinstance(result, FamilyEvidenceResult):
            raise BatchM5ERetrievalError("family delegate returned an invalid result")
        reason = result.data.get("reason") if isinstance(result.data, Mapping) else None
        exact_run_not_found = reason in {
            "occurrence_not_found", "operation_not_supported",
        }
        accepted_scope = result.status == "ok" or exact_run_not_found
        return FamilyEvidenceResult(
            result.status,
            self.family,
            result.ingest_run_id,
            self.family_contract_version,
            M5E_COVERAGE if accepted_scope else "unavailable",
            False,
            result.data,
        )

    def _call(self, method: Any, kwargs: dict[str, Any]) -> FamilyEvidenceResult:
        run = kwargs.get("ingest_run_id")
        run_value = run if isinstance(run, str) else "invalid"
        try:
            self._stability.check()
        except (RuntimeStabilityError, BatchM5ERuntimeOracleError) as exc:
            return unavailable_result(
                self.family, self.family_contract_version, run_value, str(exc)
            )
        try:
            result = method(**kwargs)
        except Exception:
            try:
                self._stability.check()
            except (RuntimeStabilityError, BatchM5ERuntimeOracleError) as exc:
                return unavailable_result(
                    self.family, self.family_contract_version, run_value, str(exc)
                )
            raise
        try:
            self._stability.check()
        except (RuntimeStabilityError, BatchM5ERuntimeOracleError) as exc:
            return unavailable_result(
                self.family, self.family_contract_version, run_value, str(exc)
            )
        return self._normalize_result(result)

    def list_evidence(
        self,
        *,
        ingest_run_id: str,
        family_contract_version: str,
        filters: dict[str, Any] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> FamilyEvidenceResult:
        return self._call(
            self._delegate.list_evidence,
            {
                "ingest_run_id": ingest_run_id,
                "family_contract_version": family_contract_version,
                "filters": filters,
                "cursor": cursor,
                "limit": limit,
            },
        )

    def get_evidence(
        self,
        *,
        ingest_run_id: str,
        family_contract_version: str,
        occurrence_id: str,
    ) -> FamilyEvidenceResult:
        return self._call(
            self._delegate.get_evidence,
            {
                "ingest_run_id": ingest_run_id,
                "family_contract_version": family_contract_version,
                "occurrence_id": occurrence_id,
            },
        )

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
        return self._call(
            self._delegate.search_text,
            {
                "ingest_run_id": ingest_run_id,
                "family_contract_version": family_contract_version,
                "query": query,
                "filters": filters,
                "cursor": cursor,
                "limit": limit,
            },
        )


def build_batch_m5e_providers(
    db: ReadonlyDatabase,
    qualification: M5EQualification,
    manifest_path: str | Path = M5E_RUNTIME_MANIFEST_PATH,
    *,
    manifest: Mapping[str, Any] | None = None,
    receipt_path: str | Path | None = None,
    spec_dir: str | Path | None = None,
) -> tuple[BatchM5EAcceptedFamilyProvider, ...]:
    """Build all twenty-one accepted providers from one qualification."""
    if not isinstance(db, ReadonlyDatabase):
        raise BatchM5ERetrievalError("M5E provider requires an explicit ReadonlyDatabase")
    manifest_file = _absolute(manifest_path)
    if manifest_file.resolve() != Path(M5E_RUNTIME_MANIFEST_PATH).resolve():
        raise BatchM5ERetrievalError("M5E provider requires the tracked runtime manifest")
    if manifest is None:
        try:
            manifest = load_runtime_manifest(
                manifest_file, require_product_accepted=True
            )
        except Exception as exc:
            raise BatchM5ERetrievalError("tracked M5E runtime manifest is unavailable") from exc
    if not isinstance(manifest, Mapping):
        raise BatchM5ERetrievalError("M5E runtime manifest must be an object")
    items = _validate_manifest_binding(manifest, qualification, manifest_file)
    if Path(qualification.database_path).resolve() != Path(db.path).resolve():
        raise BatchM5ERetrievalError("M5E qualification database is not the selected DB")
    receipt_file = _absolute(receipt_path or qualification.receipt_path)
    if Path(qualification.receipt_path).resolve() != receipt_file.resolve():
        raise BatchM5ERetrievalError("M5E qualification receipt is not the selected receipt")
    stability = getattr(qualification, "stability", None)
    if not isinstance(stability, M5EStabilitySnapshot):
        raise BatchM5ERetrievalError(
            "M5E provider requires the qualification stability snapshot"
        )
    try:
        stability.check()
    except (RuntimeStabilityError, BatchM5ERuntimeOracleError) as exc:
        raise BatchM5ERetrievalError(
            "M5E qualification stability cannot be reused"
        ) from exc
    if stability.database.path != str(_absolute(db.path)):
        raise BatchM5ERetrievalError("M5E stability database path is not selected DB")
    if stability.receipt.path != str(receipt_file):
        raise BatchM5ERetrievalError("M5E stability receipt path is not selected receipt")
    if stability.release_report is None:
        raise BatchM5ERetrievalError("M5E qualification has no bound release report")
    companion_stabilities = stability.companion_receipts
    if set(companion_stabilities) != set(M5E_COMPATIBILITY_ORDER):
        raise BatchM5ERetrievalError("M5E qualification has no fixed companion set")
    report_file = Path(stability.release_report.path)
    companion_files = {
        name: Path(companion_stabilities[name].path)
        for name in M5E_COMPATIBILITY_ORDER
    }
    declarations: dict[str, M5DFamilyDeclaration] = {}
    if spec_dir is None:
        loaded = load_family_declarations()
    else:
        loaded = load_family_declarations(spec_dir)
    if tuple(item.family for item in loaded) != M5D_FAMILY_ORDER:
        raise BatchM5ERetrievalError("M5D declaration order is not the fixed Wave02 order")
    declarations = {item.family: item for item in loaded}
    providers: list[BatchM5EAcceptedFamilyProvider] = []
    for family in M5E_FAMILY_ORDER:
        item = items[family]
        if item.get("wave") == "wave01":
            delegate = _m4_delegate(db, item, qualification, manifest)
        elif item.get("wave") == "wave02":
            declaration = declarations.get(family)
            if declaration is None:
                raise BatchM5ERetrievalError(f"M5D declaration is missing: {family}")
            delegate = _m5d_delegate(
                db, item, qualification, manifest, declaration
            )
        else:
            raise BatchM5ERetrievalError(f"M5E family wave is invalid: {family}")
        providers.append(
            BatchM5EAcceptedFamilyProvider(
                db, item, qualification, manifest, stability, delegate,
                report_file, companion_files,
            )
        )
    return tuple(providers)


__all__ = [
    "BatchM5EAcceptedFamilyProvider",
    "BatchM5ERetrievalError",
    "M5EStabilitySnapshot",
    "build_batch_m5e_providers",
]
