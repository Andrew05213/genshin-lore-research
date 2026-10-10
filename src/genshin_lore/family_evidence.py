"""Stable MCP transport facade for family-specific evidence providers.

This module routes requests.  It deliberately defines no shared entity,
identity, row, text, or filter model; each provider owns the contents of its
``data`` member and all evidence semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class FamilyEvidenceResult:
    status: str
    family: str
    ingest_run_id: str
    family_contract_version: str
    coverage: str
    corpus_complete: bool
    data: dict[str, Any]

    def envelope(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "ingest_run_id": self.ingest_run_id,
            "evidence_only": True,
            "coverage": self.coverage,
            "corpus_complete": self.corpus_complete,
            "family_contract_version": self.family_contract_version,
            "data": self.data,
        }


class FamilyEvidenceProvider(Protocol):
    family: str
    family_contract_version: str

    def capability(self) -> dict[str, Any]: ...
    def list_evidence(self, *, ingest_run_id: str, family_contract_version: str,
                      filters: dict[str, Any] | None, cursor: str | None,
                      limit: int) -> FamilyEvidenceResult: ...
    def get_evidence(self, *, ingest_run_id: str, family_contract_version: str,
                     occurrence_id: str) -> FamilyEvidenceResult: ...
    def search_text(self, *, ingest_run_id: str, family_contract_version: str,
                    query: str, filters: dict[str, Any] | None,
                    cursor: str | None, limit: int) -> FamilyEvidenceResult: ...


class FamilyEvidenceRouter:
    """Immutable provider registry used by the three public MCP verbs."""

    def __init__(self, providers: list[FamilyEvidenceProvider]) -> None:
        registry: dict[str, dict[str, FamilyEvidenceProvider]] = {}
        operations: dict[tuple[str, str], frozenset[str]] = {}
        for provider in providers:
            contracts = registry.setdefault(provider.family, {})
            if provider.family_contract_version in contracts:
                raise ValueError(
                    f"duplicate family evidence provider contract: {provider.family} "
                    f"{provider.family_contract_version}"
                )
            contracts[provider.family_contract_version] = provider
            operations[(provider.family, provider.family_contract_version)] = frozenset(
                provider.capability().get("supported_operations", [])
            )
        self._providers = registry
        self._operations = operations

    def capabilities(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for family in sorted(self._providers):
            contracts = self._providers[family]
            values = [contracts[name].capability() for name in sorted(contracts)]
            result[family] = values[0] if len(values) == 1 else {
                **values[0],
                "contract_variants": values,
                "family_contract_versions": sorted(contracts),
            }
        return result

    @staticmethod
    def _invalid(kwargs: dict[str, Any], exc: ValueError) -> FamilyEvidenceResult:
        family = kwargs.get("family") if isinstance(kwargs.get("family"), str) else "invalid"
        run = kwargs.get("ingest_run_id") if isinstance(kwargs.get("ingest_run_id"), str) else "invalid"
        contract = kwargs.get("family_contract_version") if isinstance(kwargs.get("family_contract_version"), str) else "invalid"
        return FamilyEvidenceResult(
            "invalid_argument", family, run, contract, "unavailable", False,
            {"status": "InvalidArgument", "reason": "invalid_argument", "detail": str(exc)},
        )

    @staticmethod
    def _text(value: Any, label: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{label} must be a non-empty string")
        return value

    def _provider(self, family: Any, ingest_run_id: Any,
                  family_contract_version: Any, operation: str) -> tuple[FamilyEvidenceProvider | None, FamilyEvidenceResult | None]:
        family_value = self._text(family, "family")
        run_value = self._text(ingest_run_id, "ingest_run_id")
        contract_value = self._text(family_contract_version, "family_contract_version")
        contracts = self._providers.get(family_value)
        if contracts is None:
            return None, FamilyEvidenceResult(
                "not_found", family_value, run_value, contract_value,
                "unavailable", False,
                {"status": "NotFound", "reason": "unknown_family", "lookup": {"family": family_value}},
            )
        provider = contracts.get(contract_value)
        if provider is None:
            return None, FamilyEvidenceResult(
                "not_found", family_value, run_value, contract_value,
                "unavailable", False,
                {"status": "NotFound", "reason": "family_contract_version_mismatch",
                 "lookup": {"requested": contract_value,
                            "supported": sorted(contracts)}},
            )
        if operation not in self._operations[(family_value, contract_value)]:
            return None, FamilyEvidenceResult(
                "not_found", family_value, run_value, contract_value,
                "unavailable", False,
                {"status": "NotFound", "reason": "operation_not_supported",
                 "lookup": {"operation": operation, "family": family_value}},
            )
        return provider, None

    def list_evidence(self, **kwargs: Any) -> FamilyEvidenceResult:
        try:
            provider, error = self._provider(kwargs["family"], kwargs["ingest_run_id"], kwargs["family_contract_version"], "list_family_evidence")
            if error is not None:
                return error
            assert provider is not None
            return provider.list_evidence(**{key: kwargs[key] for key in ("ingest_run_id", "family_contract_version", "filters", "cursor", "limit")})
        except ValueError as exc:
            return self._invalid(kwargs, exc)

    def get_evidence(self, **kwargs: Any) -> FamilyEvidenceResult:
        try:
            provider, error = self._provider(kwargs["family"], kwargs["ingest_run_id"], kwargs["family_contract_version"], "get_family_evidence")
            if error is not None:
                return error
            assert provider is not None
            return provider.get_evidence(**{key: kwargs[key] for key in ("ingest_run_id", "family_contract_version", "occurrence_id")})
        except ValueError as exc:
            return self._invalid(kwargs, exc)

    def search_text(self, **kwargs: Any) -> FamilyEvidenceResult:
        try:
            provider, error = self._provider(kwargs["family"], kwargs["ingest_run_id"], kwargs["family_contract_version"], "search_family_text")
            if error is not None:
                return error
            assert provider is not None
            return provider.search_text(**{key: kwargs[key] for key in ("ingest_run_id", "family_contract_version", "query", "filters", "cursor", "limit")})
        except ValueError as exc:
            return self._invalid(kwargs, exc)


__all__ = ["FamilyEvidenceProvider", "FamilyEvidenceResult", "FamilyEvidenceRouter"]
