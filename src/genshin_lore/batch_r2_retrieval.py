"""R2 Schema 19 accepted routed providers.

The wrapper changes only product qualification metadata.  Every read remains
implemented by its existing family-specific repository and every call keeps
the exact family contract/run selected by that repository.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ._readonly_database import ReadonlyDatabase
from .batch_m4_retrieval import (
    FAMILY_RETRIEVAL_SPECS, BatchM4CapabilitySnapshot, BatchM4FamilyRepository,
)
from .batch_m4_runtime_oracle import M4_FAMILY_ORDER
from .batch_m5d_retrieval import BatchM5DFamilyRepository
from .batch_m5d_runtime import (
    M5D_FAMILY_ORDER, M5D_SPEC_DIR, M5DQualification,
    load_family_declarations,
)
from .batch_r1_repository import R1FamilyProvider
from .batch_r1_schema19 import R1_FAMILY_ORDER, family_item
from .batch_r2_runtime import (
    R2_COVERAGE, R2_PROVIDER_ORDER, R2_SCHEMA_CONTRACT, R2_SCHEMA_VERSION,
    BatchR2RuntimeError, R2Qualification,
)
from .family_evidence import FamilyEvidenceResult
from .runtime_stability import unavailable_result


class BatchR2RetrievalError(RuntimeError):
    """A qualified R2 provider could not preserve its family contract."""


def _legacy_items(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    values = manifest.get("families")
    if not isinstance(values, list):
        raise BatchR2RetrievalError("M5E family declarations are absent")
    result = {str(item.get("family")): item for item in values if isinstance(item, Mapping)}
    if tuple(result) != (*M4_FAMILY_ORDER, *M5D_FAMILY_ORDER):
        raise BatchR2RetrievalError("M5E family declaration order mismatch")
    return result


def _m4_delegate(db: ReadonlyDatabase, item: Mapping[str, Any], qualification: R2Qualification) -> BatchM4FamilyRepository:
    family = str(item["family"])
    spec = FAMILY_RETRIEVAL_SPECS[family]
    table = item["tables"][0]
    run = {
        "ingest_run_id": item["ingest_run_id"], "snapshot_id": qualification.legacy_manifest["snapshot_id"],
        "parser_build_id": item["parser_build_id"], "family_contract_version": spec.family_contract_version,
        "coverage": str(item.get("coverage_level") or "R1_structured_family"), "corpus_complete": False,
        "wave_id": qualification.legacy_manifest["wave"]["id"], "row_count": table["row_count"],
        "logical_row_sha256": table["logical_row_sha256"], "release_receipt": qualification.receipt_id,
        "runtime_manifest_sha256": qualification.manifest_sha256,
    }
    snapshot = BatchM4CapabilitySnapshot(
        family, spec.family_contract_version, True, (run,), None,
        {"receipt_id": qualification.receipt_id},
        {"status": "verified", "schema_version": qualification.schema_version,
         "manifest_sha256": qualification.manifest_sha256, "family_count": len(R2_PROVIDER_ORDER)},
        "",
    )
    return BatchM4FamilyRepository(db, snapshot)


def _m5d_delegates(db: ReadonlyDatabase, items: Mapping[str, Mapping[str, Any]], qualification: R2Qualification) -> dict[str, BatchM5DFamilyRepository]:
    declarations = load_family_declarations(M5D_SPEC_DIR)
    if tuple(declaration.family for declaration in declarations) != M5D_FAMILY_ORDER:
        raise BatchR2RetrievalError("Wave02 declaration order mismatch")
    counts = {family: int(items[family]["tables"][0]["row_count"]) for family in M5D_FAMILY_ORDER}
    result: dict[str, BatchM5DFamilyRepository] = {}
    for declaration in declarations:
        item = items[declaration.family]
        if item.get("family_contract_version") != declaration.family_contract_version or item.get("ingest_run_id") != declaration.ingest_run_id:
            raise BatchR2RetrievalError(f"Wave02 contract/run mismatch: {declaration.family}")
        value = M5DQualification(
            status="verified", report_path=qualification.receipt_path,
            manifest_path=qualification.manifest_path, verification_path="",
            candidate_path=qualification.database_path, candidate_sha256=qualification.candidate_sha256,
            candidate_size_bytes=qualification.candidate_size_bytes,
            wave_id=str(qualification.legacy_manifest["wave"]["id"]),
            snapshot_id=str(qualification.legacy_manifest["snapshot_id"]),
            parser_build_id=str(item["parser_build_id"]), manifest_sha256=qualification.manifest_sha256,
            report_sha256="0" * 64, verification_sha256="0" * 64,
            family_row_counts=counts, stability=None, report={},
            manifest=qualification.legacy_manifest, verification={},
        )
        result[declaration.family] = BatchM5DFamilyRepository(db, declaration, value)
    return result


class R2AcceptedFamilyProvider:
    def __init__(self, delegate: Any, qualification: R2Qualification, family_coverage: str):
        self._delegate = delegate
        self.qualification = qualification
        self.family = delegate.family
        self.family_contract_version = delegate.family_contract_version
        self.family_coverage = family_coverage

    def capability(self) -> dict[str, Any]:
        try:
            self.qualification.check()
        except BatchR2RuntimeError as exc:
            return {
                "family": self.family, "family_contract_version": self.family_contract_version,
                "schema_supported": True, "evidence_available": False,
                "unavailable_reason": str(exc), "eligible_runs": [], "evidence_only": True,
            }
        value = dict(self._delegate.capability())
        value.update({
            "schema_version": self.qualification.schema_version, "schema_contract": self.qualification.schema_contract,
            "schema_supported": True, "evidence_available": True, "unavailable_reason": None,
            "coverage": self.family_coverage, "wave_coverage": self.qualification.wave_coverage, "corpus_complete": False, "evidence_only": True,
            "release_qualification": {
                "status": "verified", "product_accepted": True,
                "schema_version": self.qualification.schema_version, "schema_contract": self.qualification.schema_contract,
                "receipt_id": self.qualification.receipt_id,
                "runtime_manifest_sha256": self.qualification.manifest_sha256,
                "qualification_count": 1, "candidate_hash_verified_once": True,
            },
        })
        runs = value.get("eligible_runs", value.get("eligible_exact_runs", []))
        if isinstance(runs, list):
            for run in runs:
                if isinstance(run, dict):
                    run["coverage"] = self.family_coverage
                    run["wave_coverage"] = self.qualification.wave_coverage
                    run["corpus_complete"] = False
        semantics = value.get("coverage_semantics")
        if isinstance(semantics, dict):
            semantics.update({"coverage": self.family_coverage, "wave_coverage": self.qualification.wave_coverage, "outer_corpus_complete": False,
                              "product_accepted": True, "full_7_0_claimed": False})
        return value
    def _call(self, name: str, kwargs: dict[str, Any]) -> FamilyEvidenceResult:
        run = kwargs.get("ingest_run_id")
        run_value = run if isinstance(run, str) else "invalid"
        try:
            self.qualification.check()
        except BatchR2RuntimeError as exc:
            return unavailable_result(self.family, self.family_contract_version, run_value, str(exc))
        try:
            result = getattr(self._delegate, name)(**kwargs)
        except Exception:
            try:
                self.qualification.check()
            except BatchR2RuntimeError as exc:
                return unavailable_result(self.family, self.family_contract_version, run_value, str(exc))
            raise
        try:
            self.qualification.check()
        except BatchR2RuntimeError as exc:
            return unavailable_result(self.family, self.family_contract_version, run_value, str(exc))
        if not isinstance(result, FamilyEvidenceResult):
            raise BatchR2RetrievalError("family delegate returned an invalid result")
        accepted = result.status == "ok" or (isinstance(result.data, Mapping) and result.data.get("reason") in {"occurrence_not_found", "operation_not_supported"})
        coverage = self.family_coverage if accepted else "unavailable"
        return FamilyEvidenceResult(result.status, self.family, result.ingest_run_id, self.family_contract_version, coverage, False, result.data)

    def list_evidence(self, **kwargs: Any) -> FamilyEvidenceResult:
        return self._call("list_evidence", kwargs)

    def get_evidence(self, **kwargs: Any) -> FamilyEvidenceResult:
        return self._call("get_evidence", kwargs)

    def search_text(self, **kwargs: Any) -> FamilyEvidenceResult:
        return self._call("search_text", kwargs)


def build_batch_r2_providers(db: ReadonlyDatabase, qualification: R2Qualification) -> tuple[R2AcceptedFamilyProvider, ...]:
    if Path(db.path).resolve() != Path(qualification.database_path).resolve():
        raise BatchR2RetrievalError("R2 qualification database is not the selected database")
    qualification.check()
    items = _legacy_items(qualification.legacy_manifest)
    m5d = _m5d_delegates(db, items, qualification)
    providers: list[R2AcceptedFamilyProvider] = []
    for family in M4_FAMILY_ORDER:
        coverage = str(items[family].get("coverage_level") or "R1_structured_family")
        providers.append(R2AcceptedFamilyProvider(_m4_delegate(db, items[family], qualification), qualification, coverage))
    for family in M5D_FAMILY_ORDER:
        coverage = str(items[family].get("coverage_level") or "R1_structured_family")
        providers.append(R2AcceptedFamilyProvider(m5d[family], qualification, coverage))
    for family in R1_FAMILY_ORDER:
        providers.append(R2AcceptedFamilyProvider(
            R1FamilyProvider(db.path, family_item(qualification.q2_manifest, family)),
            qualification, "R1_structured_family_release_candidate",
        ))
    qualification.check()
    if tuple(provider.family for provider in providers) != R2_PROVIDER_ORDER:
        raise BatchR2RetrievalError("R2 provider order mismatch")
    return tuple(providers)


__all__ = ["BatchR2RetrievalError", "R2AcceptedFamilyProvider", "build_batch_r2_providers"]
