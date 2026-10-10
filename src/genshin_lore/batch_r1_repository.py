"""Candidate-only family-specific Repository/provider for R1 Schema 19."""
from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from .batch_m0_baseline import SOURCE_SNAPSHOT_ID
from .batch_r1_schema19 import (
    Q2_MANIFEST_SHA256, R1_CONTRACT, R1_COVERAGE, R1_FAMILY_ORDER,
    family_item, load_manifest, sha256_file,
)
from .db import validate_exact_schema_structure
from .family_evidence import FamilyEvidenceResult
from .util import canonical_json

CANDIDATE_SIZE = 5_456_945_152
CANDIDATE_SHA256 = "000bb61aea4825d895a7b0a4b51f9f3965e45f94a4c54069b3c0947ca9f1039b"


class R1RepositoryError(RuntimeError):
    pass


def _q(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _cursor_decode(value: str | None, scope: Mapping[str, Any]) -> list[Any] | None:
    if value is None:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode()))
    except Exception as exc:
        raise ValueError("cursor is invalid") from exc
    if payload.get("scope") != dict(scope) or not isinstance(payload.get("position"), list):
        raise ValueError("cursor scope mismatch")
    return list(payload["position"])


def _cursor_encode(scope: Mapping[str, Any], position: list[Any]) -> str:
    return base64.urlsafe_b64encode(canonical_json({"scope": dict(scope), "position": position}).encode()).decode().rstrip("=")


class R1FamilyProvider:
    def __init__(self, database: str | Path, item: Mapping[str, Any]):
        self.database = Path(database).resolve()
        self.item = dict(item)
        self.family = str(item["family"])
        self.family_contract_version = str(item["family_contract_version"])
        self.run = str(item["ingest_run_id"])
        uri = self.database.as_uri() + "?mode=ro&immutable=1"
        self.conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA query_only=ON")
        row = self.conn.execute("SELECT snapshot_id,parser_build_id,status FROM ingest_run WHERE id=?", (self.run,)).fetchone()
        if row is None or tuple(row) != (SOURCE_SNAPSHOT_ID, item["parser_build_id"], "complete"):
            self.conn.close()
            raise R1RepositoryError("exact R1 run is not qualified")
    def close(self) -> None:
        self.conn.close()
    def capability(self) -> dict[str, Any]:
        operations = ["list_family_evidence", "get_family_evidence"]
        if self.item["search_available"]:
            operations.append("search_family_text")
        return {"family": self.family, "family_contract_version": self.family_contract_version,
                "evidence_available": True, "eligible_runs": [{"ingest_run_id": self.run,
                "parser_build_id": self.item["parser_build_id"], "snapshot_id": SOURCE_SNAPSHOT_ID,
                "family_contract_version": self.family_contract_version, "coverage": R1_COVERAGE,
                "corpus_complete": False}], "supported_operations": operations,
                "supported_filters": {"list_family_evidence": ["source_ordinal"],
                "get_family_evidence": [], "search_family_text": ["locale", "field_name"]},
                "pagination_contract": {"kind": "deterministic_keyset", "limit": {"default": 50, "minimum": 1, "maximum": 100}},
                "coverage_semantics": {"raw_rows_complete": True, "declared_text_fields_processed": True,
                "declared_relations_processed": True, "query_available": True, "product_accepted": False},
                "corpus_complete": False, "evidence_only": True}
    def _result(self, status: str, data: dict[str, Any]) -> FamilyEvidenceResult:
        return FamilyEvidenceResult(status, self.family, self.run, self.family_contract_version, R1_COVERAGE, False, data)
    def _scope(self, run: str, contract: str) -> FamilyEvidenceResult | None:
        if run != self.run or contract != self.family_contract_version:
            return FamilyEvidenceResult("not_found", self.family, run, contract, "unavailable", False,
                                        {"status": "NotFound", "reason": "exact_run_or_contract_mismatch"})
        return None
    def list_evidence(self, *, ingest_run_id: str, family_contract_version: str,
                      filters: dict[str, Any] | None = None, cursor: str | None = None,
                      limit: int = 50) -> FamilyEvidenceResult:
        error = self._scope(ingest_run_id, family_contract_version)
        if error is not None:
            return error
        filters = filters or {}
        if set(filters) - {"source_ordinal"}:
            raise ValueError("unsupported filters")
        scope = {"family": self.family, "run": self.run, "filters": filters}
        position = _cursor_decode(cursor, scope)
        sql = f"SELECT * FROM {_q(self.family + '_row_occurrence')} WHERE ingest_run_id=? AND source_ordinal>?"
        args: list[Any] = [self.run, int(position[0]) if position else -1]
        if "source_ordinal" in filters:
            sql += " AND source_ordinal=?"
            args.append(filters["source_ordinal"])
        rows = self.conn.execute(sql + " ORDER BY source_ordinal,occurrence_id LIMIT ?", (*args, limit + 1)).fetchall()
        items = [dict(row) for row in rows[:limit]]
        next_cursor = _cursor_encode(scope, [int(rows[limit - 1]["source_ordinal"])]) if len(rows) > limit else None
        return self._result("ok", {"items": items, "next_cursor": next_cursor})
    def get_evidence(self, *, ingest_run_id: str, family_contract_version: str,
                     occurrence_id: str) -> FamilyEvidenceResult:
        error = self._scope(ingest_run_id, family_contract_version)
        if error is not None:
            return error
        row = self.conn.execute(f"SELECT * FROM {_q(self.family + '_row_occurrence')} WHERE occurrence_id=? AND ingest_run_id=?", (occurrence_id, self.run)).fetchone()
        if row is None:
            return self._result("not_found", {"status": "NotFound", "reason": "occurrence_not_found"})
        owners = [dict(value) for value in self.conn.execute(
            f"SELECT * FROM {_q(self.family + '_text_owner_occurrence')} WHERE row_occurrence_id=? AND ingest_run_id=? ORDER BY field_pair_ordinal,owner_id", (occurrence_id, self.run))]
        for owner in owners:
            owner["resolutions"] = [dict(value) for value in self.conn.execute(
                f"SELECT * FROM {_q(self.family + '_text_resolution_occurrence')} WHERE owner_id=? AND ingest_run_id=? ORDER BY locale", (owner["owner_id"], self.run))]
            owner["candidates"] = [dict(value) for value in self.conn.execute(
                f"SELECT * FROM {_q(self.family + '_text_candidate_occurrence')} WHERE owner_id=? AND ingest_run_id=? ORDER BY locale,relative_path,pair_ordinal,candidate_id", (owner["owner_id"], self.run))]
        return self._result("ok", {"item": dict(row), "text_owners": owners})
    def search_text(self, *, ingest_run_id: str, family_contract_version: str, query: str,
                    filters: dict[str, Any] | None = None, cursor: str | None = None,
                    limit: int = 50) -> FamilyEvidenceResult:
        error = self._scope(ingest_run_id, family_contract_version)
        if error is not None:
            return error
        if not self.item["search_available"]:
            raise ValueError("search_family_text unsupported for contract")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must contain non-whitespace text")
        filters = filters or {}
        if set(filters) - {"locale", "field_name"}:
            raise ValueError("unsupported filters")
        scope = {"family": self.family, "run": self.run, "query": query, "filters": filters}
        position = _cursor_decode(cursor, scope)
        sql = (f"SELECT c.*,o.row_occurrence_id,o.field_name,r.source_ordinal FROM {_q(self.family + '_text_candidate_occurrence')} c "
               f"JOIN {_q(self.family + '_text_owner_occurrence')} o ON o.owner_id=c.owner_id AND o.ingest_run_id=c.ingest_run_id "
               f"JOIN {_q(self.family + '_row_occurrence')} r ON r.occurrence_id=o.row_occurrence_id AND r.ingest_run_id=o.ingest_run_id "
               "WHERE c.ingest_run_id=? AND instr(c.raw_text,?)>0")
        args: list[Any] = [self.run, query]
        if position:
            sql += " AND (r.source_ordinal,c.relative_path,c.pair_ordinal,c.candidate_id)>(?,?,?,?)"
            args.extend(position)
        if "locale" in filters:
            sql += " AND c.locale=?"
            args.append(filters["locale"])
        if "field_name" in filters:
            sql += " AND o.field_name=?"
            args.append(filters["field_name"])
        rows = self.conn.execute(sql + " ORDER BY r.source_ordinal,c.relative_path,c.pair_ordinal,c.candidate_id LIMIT ?", (*args, limit + 1)).fetchall()
        items = [dict(row) for row in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = _cursor_encode(scope, [int(last["source_ordinal"]), str(last["relative_path"]), int(last["pair_ordinal"]), str(last["candidate_id"])])
        return self._result("ok", {"items": items, "next_cursor": next_cursor})


def qualify_candidate_repository(database: str | Path) -> Path:
    path = Path(database).resolve()
    if not path.is_file() or path.is_symlink() or path.stat().st_size != CANDIDATE_SIZE:
        raise R1RepositoryError("exact R1 candidate is unavailable")
    if sha256_file(path) != CANDIDATE_SHA256:
        raise R1RepositoryError("R1 candidate SHA mismatch")
    for suffix in ("-journal", "-wal", "-shm"):
        if Path(str(path) + suffix).exists():
            raise R1RepositoryError("R1 candidate sidecar is present")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        validate_exact_schema_structure(conn, 19)
        versions = tuple(row[0] for row in conn.execute("SELECT version FROM schema_migration ORDER BY version"))
        if versions != tuple(range(1, 20)):
            raise R1RepositoryError("R1 candidate ledger mismatch")
        wave = conn.execute("SELECT COUNT(*) FROM batch_wave WHERE contract_version=? AND manifest_sha256=? AND target_schema_version=19", (R1_CONTRACT, Q2_MANIFEST_SHA256)).fetchone()[0]
        if wave != 1:
            raise R1RepositoryError("R1 candidate wave binding mismatch")
    finally:
        conn.close()
    return path


def build_candidate_providers(database: str | Path, manifest_path: str | Path) -> tuple[R1FamilyProvider, ...]:
    database = qualify_candidate_repository(database)
    manifest = load_manifest(manifest_path)
    return tuple(R1FamilyProvider(database, family_item(manifest, family)) for family in R1_FAMILY_ORDER)


__all__ = ["R1FamilyProvider", "R1RepositoryError", "build_candidate_providers", "qualify_candidate_repository"]
