"""R5 bounded Quest 71008/Talk evidence provider for the routed MCP facade."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import sqlite3
import stat
import threading
from pathlib import Path
from collections.abc import Iterable, Sequence
from typing import Any, Mapping

from .family_evidence import FamilyEvidenceResult
from .util import canonical_json

FAMILY = "quest_talk_pilot"
FAMILY_CONTRACT_VERSION = "quest-talk-evidence-v1"
RUNTIME_CONTRACT = "r5-quest-talk-runtime-v1"
COVERAGE = "bounded_quest_71008_talk_candidate_closure"
MEDIUM_FAMILY_CONTRACT_VERSION = "quest-talk-evidence-v2"
MEDIUM_RUNTIME_CONTRACT = "r6-medium-quest-talk-runtime-v1"
MEDIUM_COVERAGE = "bounded_r6_medium_quest_talk_candidate_closure"
MEDIUM_QUEST_IDS = (393, 3027, 6027, 71008, 73000, 74025)


class R5QuestTalkError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: Path) -> tuple[int, int, int, int]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise R5QuestTalkError(f"R5 bound file is unavailable: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise R5QuestTalkError(f"R5 bound file must be a regular non-symlink: {path}")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _fingerprint(path: Path) -> tuple[int, int, int, int, str]:
    before = _identity(path)
    digest = _sha256(path)
    if _identity(path) != before:
        raise R5QuestTalkError("R5 bound file changed during qualification")
    return (*before, digest)


def _cursor(scope: Mapping[str, Any], last: str) -> str:
    return base64.urlsafe_b64encode(
        canonical_json({"version": 1, "scope": dict(scope), "last": last}).encode()
    ).decode().rstrip("=")


def _decode(value: str | None, scope: Mapping[str, Any]) -> str | None:
    if value is None:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode()))
    except (ValueError, TypeError, binascii.Error, json.JSONDecodeError) as exc:
        raise ValueError("cursor is invalid") from exc
    if payload.get("version") != 1 or payload.get("scope") != dict(scope):
        raise ValueError("cursor scope does not match request")
    last = payload.get("last")
    if not isinstance(last, str) or not last:
        raise ValueError("cursor position is invalid")
    return last


def _filters(value: dict[str, Any] | None, allowed: set[str]) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError(f"filters support only {sorted(allowed)}")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(item, str) or not item:
            raise ValueError(f"filters.{key} must be a non-empty string")
        result[key] = item
    return result


class R5QuestTalkProvider:
    family = FAMILY
    family_contract_version = FAMILY_CONTRACT_VERSION

    def __init__(self, artifact_path: str | Path, runtime_manifest_path: str | Path) -> None:
        self.artifact_path = Path(artifact_path).absolute()
        self.runtime_manifest_path = Path(runtime_manifest_path).absolute()
        self._manifest_fingerprint = _fingerprint(self.runtime_manifest_path)
        try:
            manifest = json.loads(self.runtime_manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise R5QuestTalkError("R5 runtime manifest is unreadable") from exc
        if not isinstance(manifest, dict):
            raise R5QuestTalkError("R5 runtime manifest contract mismatch")
        runtime_contract = manifest.get("contract")
        if runtime_contract == RUNTIME_CONTRACT:
            expected_family_contract = FAMILY_CONTRACT_VERSION
            expected_scope = (71008,)
            self.coverage = COVERAGE
        elif runtime_contract == MEDIUM_RUNTIME_CONTRACT:
            expected_family_contract = MEDIUM_FAMILY_CONTRACT_VERSION
            expected_scope = MEDIUM_QUEST_IDS
            self.coverage = MEDIUM_COVERAGE
        elif runtime_contract == "quest-talk-batch-runtime-v1":
            from .quest_talk_scope import validate_scope
            scalable_scope = validate_scope(manifest.get("scalable_scope"))
            if scalable_scope["snapshot_id"] != manifest.get("snapshot_id"):
                raise R5QuestTalkError("scope snapshot identity mismatch")
            expected_family_contract = scalable_scope["family_contract_version"]
            expected_scope = tuple(scalable_scope["quest_selection"])
            self.coverage = scalable_scope["coverage_level"]
        else:
            raise R5QuestTalkError("R5 runtime manifest contract mismatch")
        if manifest.get("family") != FAMILY or manifest.get("family_contract_version") != expected_family_contract:
            raise R5QuestTalkError("R5 runtime manifest family contract mismatch")
        self._scope_path = None
        if runtime_contract == "quest-talk-batch-runtime-v1":
            binding = manifest.get("corpus_manifest", {})
            self._scope_path = self.runtime_manifest_path.parent / Path(binding["path"]).name
            self._scope_fingerprint = _fingerprint(self._scope_path)
            if self._scope_fingerprint[4] != binding.get("sha256"):
                raise R5QuestTalkError("scope manifest SHA mismatch")
            if json.loads(self._scope_path.read_text())["scalable_scope"] != scalable_scope:
                raise R5QuestTalkError("artifact/runtime scope manifest binding mismatch")
        self.runtime_contract = str(runtime_contract)
        self.family_contract_version = str(expected_family_contract)
        scope = manifest.get("scope")
        if scope is None and self.runtime_contract == RUNTIME_CONTRACT:
            scope = {"family": "quest_corpus_evidence", "quest_ids": [71008]}
        if not isinstance(scope, dict) or scope.get("family") != "quest_corpus_evidence":
            raise R5QuestTalkError("R5 runtime manifest scope is absent or malformed")
        quest_ids = scope.get("quest_ids")
        if quest_ids != list(expected_scope):
            raise R5QuestTalkError("R5 runtime manifest quest allowlist mismatch")
        text_status = manifest.get("text_status")
        if self.runtime_contract == MEDIUM_RUNTIME_CONTRACT and (
            not isinstance(text_status, dict) or type(text_status.get("missing")) is not int
        ):
            raise R5QuestTalkError("R6 medium runtime manifest text_status.missing is absent")
        self.legal_missing_text_slots = (
            int(text_status["missing"])
            if isinstance(text_status, dict) and type(text_status.get("missing")) is int
            else 22
        )
        self.scope = scope
        self.quest_ids = tuple(expected_scope)
        artifact = manifest.get("artifact")
        if not isinstance(artifact, dict):
            raise R5QuestTalkError("R5 artifact is unavailable")
        self._artifact_fingerprint = _fingerprint(self.artifact_path)
        if artifact.get("size_bytes") != self._artifact_fingerprint[2] or artifact.get("sha256") != self._artifact_fingerprint[4]:
            raise R5QuestTalkError("R5 artifact size/SHA-256 seal mismatch")
        self._assert_sidecars_absent()
        self.manifest = manifest
        self.ingest_run_id = str(manifest["ingest_run_id"])
        self.parser_build_id = str(manifest["parser_build_id"])
        self.snapshot_id = str(manifest["snapshot_id"])
        self._conn = sqlite3.connect(
            f"file:{self.artifact_path.as_posix()}?mode=ro&immutable=1",
            uri=True,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA query_only=ON")
        self._lock = threading.RLock()
        self._invalidated = False
        self._qualify()
        self._assert_stable()

    def _assert_sidecars_absent(self) -> None:
        for suffix in ("-journal", "-shm", "-wal"):
            if Path(f"{self.artifact_path}{suffix}").exists():
                raise R5QuestTalkError(f"R5 artifact sidecar is present: {suffix}")

    def _assert_stable(self) -> None:
        if self._invalidated:
            raise R5QuestTalkError("R5 qualification was invalidated; restart with explicit inputs")
        try:
            self._assert_sidecars_absent()
            if _identity(self.artifact_path) != self._artifact_fingerprint[:4]:
                raise R5QuestTalkError("R5 artifact identity changed after qualification")
            if self._scope_path is not None and _identity(self._scope_path) != self._scope_fingerprint[:4]:
                raise R5QuestTalkError("scope manifest identity changed after qualification")
            if _identity(self.runtime_manifest_path) != self._manifest_fingerprint[:4]:
                raise R5QuestTalkError("R5 runtime manifest identity changed after qualification")
        except R5QuestTalkError:
            self._invalidated = True
            raise

    def _finish(self, result: FamilyEvidenceResult) -> FamilyEvidenceResult:
        self._assert_stable()
        return result

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def _qualify(self) -> None:
        versions = tuple(int(row[0]) for row in self._conn.execute(
            "SELECT version FROM schema_migration ORDER BY version"
        ))
        if versions != tuple(range(1, 8)):
            raise R5QuestTalkError("R5 artifact migration ledger mismatch")
        run = self._conn.execute(
            "SELECT snapshot_id,parser_build_id,status,scope_json FROM ingest_run WHERE id=?",
            (self.ingest_run_id,),
        ).fetchone()
        if run is None or tuple(run[:3]) != (self.snapshot_id, self.parser_build_id, "complete"):
            raise R5QuestTalkError("R5 exact run binding mismatch")
        scope = json.loads(run[3])
        if scope.get("quest_ids") != list(self.quest_ids) or scope.get("family") != "quest_corpus_evidence":
            raise R5QuestTalkError("R5 run scope mismatch")
        if self.runtime_contract == "quest-talk-batch-runtime-v1" and scope.get("selected_paths") != self.scope.get("selected_paths"):
            raise R5QuestTalkError("scalable run selected physical paths mismatch")
        counts = self.manifest.get("counts")
        if not isinstance(counts, dict):
            raise R5QuestTalkError("R5 count seal is absent")
        actual = {
            "evidence_containers": self._conn.execute(
                "SELECT COUNT(*) FROM evidence_container WHERE ingest_run_id=?", (self.ingest_run_id,)
            ).fetchone()[0],
            "passage_occurrences": self._conn.execute(
                "SELECT COUNT(*) FROM passage_occurrence WHERE ingest_run_id=?", (self.ingest_run_id,)
            ).fetchone()[0],
            "occurrence_edges": self._conn.execute(
                "SELECT COUNT(*) FROM occurrence_edge WHERE ingest_run_id=?", (self.ingest_run_id,)
            ).fetchone()[0],
            "occurrence_texts": self._conn.execute(
                "SELECT COUNT(*) FROM passage_occurrence_text WHERE ingest_run_id=?", (self.ingest_run_id,)
            ).fetchone()[0],
        }
        if actual != counts:
            raise R5QuestTalkError(f"R5 row-count seal mismatch: {actual}")
        if self._conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise R5QuestTalkError("R5 artifact foreign-key violation")

    def capability(self) -> dict[str, Any]:
        self._assert_stable()
        result = {
            "family": FAMILY,
            "family_contract_version": self.family_contract_version,
            "evidence_available": True,
            "eligible_runs": [{
                "ingest_run_id": self.ingest_run_id,
                "snapshot_id": self.snapshot_id,
                "parser_build_id": self.parser_build_id,
                "family_contract_version": self.family_contract_version,
                "coverage": self.coverage,
                "corpus_complete": False,
                "product_accepted": False,
                "artifact": {
                    "size_bytes": self._artifact_fingerprint[2],
                    "sha256": self._artifact_fingerprint[4],
                },
                "runtime_manifest": {
                    "size_bytes": self._manifest_fingerprint[2],
                    "sha256": self._manifest_fingerprint[4],
                },
            }],
            "supported_operations": [
                "list_family_evidence", "get_family_evidence", "search_family_text"
            ],
            "supported_filters": {
                "list_family_evidence": ["talk_id"],
                "get_family_evidence": [],
                "search_family_text": ["locale", "talk_id"],
            },
            "pagination_contract": {
                "kind": "deterministic_keyset",
                "cursor": "opaque_r5_quest_talk_cursor_v1",
                "limit": {"default": 50, "minimum": 1, "maximum": 100},
            },
            "coverage_semantics": {
                "coverage": self.coverage,
                "corpus_complete": False,
                "raw_rows_complete": True,
                "declared_text_fields_processed": True,
                "declared_relations_processed": True,
                "query_available": True,
                "product_accepted": False,
                "full_7_0_claimed": False,
                "legal_missing_text_slots": self.legal_missing_text_slots,
            },
            "container_scope": "single_physical_talk_root",
            "selection": {
                "ingest_run_id": "required_exact", "default_run": False,
                "latest_run": False, "sibling_fallback": False,
            },
            "evidence_only": True,
        }
        if self.runtime_contract == "quest-talk-batch-runtime-v1":
            binding = self.manifest.get("corpus_manifest")
            if not isinstance(binding, dict) or not isinstance(binding.get("sha256"), str):
                raise R5QuestTalkError("R5 batch manifest identity is absent")
            result["eligible_runs"][0]["batch_manifest"] = {
                "path": binding.get("path"),
                "sha256": binding["sha256"],
            }
            result["supported_filters"]["list_family_evidence"] = ["talk_id", "kind"]
            result["reference_delivery"] = {"list_filter": {"kind":"quest_reference"}, "get":"single physical reference; candidate proof only; no target context"}
        self._assert_stable()
        return result

    def _result(self, status: str, data: dict[str, Any], run: str | None = None) -> FamilyEvidenceResult:
        return FamilyEvidenceResult(
            status, FAMILY, run or self.ingest_run_id, self.family_contract_version,
            self.coverage if status in {"ok", "not_found"} else "unavailable", False, data,
        )

    def _scope(self, run: str, contract: str) -> FamilyEvidenceResult | None:
        if run != self.ingest_run_id or contract != self.family_contract_version:
            return FamilyEvidenceResult(
                "not_found", FAMILY, run, contract, "unavailable", False,
                {"status": "NotFound", "reason": "exact_run_or_contract_mismatch"},
            )
        return None

    @staticmethod
    def _limit(limit: int) -> None:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")

    def list_evidence(self, *, ingest_run_id: str, family_contract_version: str,
                      filters: dict[str, Any] | None = None, cursor: str | None = None,
                      limit: int = 50) -> FamilyEvidenceResult:
        self._assert_stable()
        error = self._scope(ingest_run_id, family_contract_version)
        if error: return self._finish(error)
        self._limit(limit)
        scalable = self.runtime_contract == "quest-talk-batch-runtime-v1"
        selected = _filters(filters, {"talk_id", "kind"} if scalable else {"talk_id"})
        if scalable and "kind" in selected and selected["kind"] not in {"talk_root", "quest_reference"}:
            raise ValueError("filters.kind must be talk_root or quest_reference")
        if selected.get("kind") == "quest_reference":
            scope = {"operation": "list_references", "run": ingest_run_id, "filters": selected}
            last = _decode(cursor, scope)
            where = ["ingest_run_id=?"]; params = [ingest_run_id]
            if "talk_id" in selected: where.append("talk_id=?"); params.append(selected["talk_id"])
            if last is not None: where.append("id>?"); params.append(last)
            rows = self._rows("SELECT * FROM document_evidence WHERE " + " AND ".join(where) + " ORDER BY id LIMIT ?", (*params,limit+1))
            return self._finish(self._result("ok", {"items": [dict(r) for r in rows[:limit]], "next_cursor": _cursor(scope,rows[limit-1]["id"]) if len(rows)>limit else None}))
        scope = {"operation": "list", "run": ingest_run_id, "filters": selected}
        last = _decode(cursor, scope)
        where = ["ingest_run_id=?"]; params: list[Any] = [ingest_run_id]
        if "talk_id" in selected: where.append("talk_id=?"); params.append(selected["talk_id"])
        if last is not None: where.append("id>?"); params.append(last)
        rows = self._rows(
            "SELECT id,talk_id,namespace,kind,source_record_id,raw_checksum,normalized_checksum "
            "FROM evidence_container WHERE " + " AND ".join(where) + " ORDER BY id LIMIT ?",
            (*params, limit + 1),
        )
        items = [dict(row) for row in rows[:limit]]
        next_cursor = _cursor(scope, str(rows[limit - 1]["id"])) if len(rows) > limit else None
        return self._finish(self._result("ok", {"items": items, "next_cursor": next_cursor}))

    def get_evidence(self, *, ingest_run_id: str, family_contract_version: str,
                     occurrence_id: str) -> FamilyEvidenceResult:
        self._assert_stable()
        error = self._scope(ingest_run_id, family_contract_version)
        if error: return self._finish(error)
        if not isinstance(occurrence_id, str) or not occurrence_id:
            raise ValueError("occurrence_id must be a non-empty string")
        if self.runtime_contract == "quest-talk-batch-runtime-v1" and occurrence_id.startswith("de:"):
            relation = self._one(
                "SELECT de.*,sr.pointer,sr.raw_json,sf.relative_path,sf.sha256 AS source_file_sha256 "
                "FROM document_evidence de JOIN source_record sr ON sr.id=de.source_record_id "
                "JOIN source_file sf ON sf.id=sr.source_file_id WHERE de.id=? AND de.ingest_run_id=?",
                (occurrence_id,ingest_run_id))
            if relation is None:
                return self._finish(self._result("not_found", {"status":"NotFound","reason":"occurrence_not_found"}))
            proof = self._one("SELECT * FROM candidate_scope_proof WHERE id=? AND ingest_run_id=?", (relation["candidate_scope_proof_id"],ingest_run_id))
            candidates = self._rows("SELECT ec.id,ec.source_record_id,sr.pointer,sr.raw_sha256,sf.relative_path,sf.sha256,sf.size_bytes FROM evidence_container ec JOIN source_record sr ON sr.id=ec.source_record_id JOIN source_file sf ON sf.id=sr.source_file_id WHERE ec.ingest_run_id=? AND ec.namespace=? AND ec.talk_id=? ORDER BY sf.relative_path,ec.id", (ingest_run_id,relation["namespace"],relation["talk_id"]))
            return self._finish(self._result("ok", {"physical_container":"single_quest_talk_reference", "reference":dict(relation), "candidate_scope":dict(proof), "physical_candidates":[dict(c) for c in candidates]}))
        row = self._one(
            """SELECT ec.*,sr.pointer AS source_pointer,sr.raw_sha256 AS source_raw_sha256,
                      sf.relative_path,sf.sha256 AS source_file_sha256
               FROM evidence_container ec JOIN source_record sr ON sr.id=ec.source_record_id
               JOIN source_file sf ON sf.id=sr.source_file_id
               WHERE ec.id=? AND ec.ingest_run_id=?""",
            (occurrence_id, ingest_run_id),
        )
        if row is None:
            return self._finish(self._result("not_found", {"status": "NotFound", "reason": "occurrence_not_found"}))
        occurrences = [dict(item) for item in self._rows(
            "SELECT * FROM passage_occurrence WHERE evidence_container_id=? AND ingest_run_id=? "
            "ORDER BY sequence_key,passage_occurrence_id", (occurrence_id, ingest_run_id)
        )]
        texts_by_occurrence: dict[str, list[dict[str, Any]]] = {}
        for text in self._rows(
            "SELECT * FROM passage_occurrence_text WHERE ingest_run_id=? "
            "AND evidence_container_id=? ORDER BY language,id",
            (ingest_run_id, occurrence_id),
        ):
            texts_by_occurrence.setdefault(text["passage_occurrence_id"], []).append(dict(text))
        for item in occurrences:
            item["metadata"] = json.loads(item.pop("metadata_json"))
            item["texts"] = texts_by_occurrence.get(item["passage_occurrence_id"], [])
        edges = [dict(item) for item in self._rows(
            "SELECT * FROM occurrence_edge WHERE evidence_container_id=? AND ingest_run_id=? "
            "ORDER BY from_passage_occurrence_id,to_passage_occurrence_id,id",
            (occurrence_id, ingest_run_id),
        )]
        container = dict(row); container["metadata"] = json.loads(container.pop("metadata_json"))
        data = {"container": container, "occurrences": occurrences, "edges": edges}
        if self.runtime_contract in (MEDIUM_RUNTIME_CONTRACT, "quest-talk-batch-runtime-v1"):
            graph = self.manifest.get("graph_status", {})
            data["pointer_observations"] = [
                dict(item)
                for key in ("external_targets", "dangling_targets", "ambiguous_targets")
                for item in graph.get(key, [])
                if item["source_talk_path"] == row["relative_path"]
            ]
        return self._finish(self._result("ok", data))

    def search_text(self, *, ingest_run_id: str, family_contract_version: str,
                    query: str, filters: dict[str, Any] | None = None,
                    cursor: str | None = None, limit: int = 50) -> FamilyEvidenceResult:
        self._assert_stable()
        error = self._scope(ingest_run_id, family_contract_version)
        if error: return self._finish(error)
        if not isinstance(query, str) or not query:
            raise ValueError("query must be a non-empty string")
        self._limit(limit); selected = _filters(filters, {"locale", "talk_id"})
        if "locale" in selected and selected["locale"] not in {"CHS", "EN", "JP"}:
            raise ValueError("filters.locale must be CHS, EN, or JP")
        scope = {"operation": "search", "run": ingest_run_id, "query": query, "filters": selected}
        last = _decode(cursor, scope)
        where = ["pot.ingest_run_id=?", "instr(pot.raw_text,?)>0"]
        params: list[Any] = [ingest_run_id, query]
        if "locale" in selected: where.append("pot.language=?"); params.append(selected["locale"])
        if "talk_id" in selected: where.append("po.talk_id=?"); params.append(selected["talk_id"])
        if last is not None: where.append("pot.id>?"); params.append(last)
        rows = self._rows(
            "SELECT pot.*,po.talk_id,po.node_id,po.source_pointer FROM passage_occurrence_text pot "
            "JOIN passage_occurrence po ON po.passage_occurrence_id=pot.passage_occurrence_id "
            "AND po.ingest_run_id=pot.ingest_run_id AND po.evidence_container_id=pot.evidence_container_id "
            "WHERE " + " AND ".join(where) + " ORDER BY pot.id LIMIT ?", (*params, limit + 1)
        )
        items = [dict(row) for row in rows[:limit]]
        next_cursor = _cursor(scope, str(rows[limit - 1]["id"])) if len(rows) > limit else None
        return self._finish(self._result("ok", {"items": items, "next_cursor": next_cursor, "literal_case_sensitive": True}))


class QuestTalkMultiRunProvider:
    """Family-local dispatcher for several independently qualified runs of one version.

    The transport registry still receives one provider for the one
    ``family + family_contract_version`` pair.  This object keeps the run
    selection explicit and delegates every operation to exactly one immutable
    child provider.  It deliberately does not merge rows, search across runs,
    or infer a run from an occurrence id.
    """

    family = FAMILY

    def __init__(self, providers: Iterable[R5QuestTalkProvider]) -> None:
        children = tuple(providers)
        if not children:
            raise ValueError("Quest/Talk multi-run provider requires at least one batch")
        contracts = {provider.family_contract_version for provider in children}
        if len(contracts) != 1 or not contracts <= {"quest-talk-evidence-v3", "quest-talk-evidence-v4", "quest-talk-evidence-v5", "quest-talk-evidence-v6", "quest-talk-evidence-v7"}:
            raise ValueError("Quest/Talk multi-run provider requires one supported v3 or v4 contract")
        runs = [provider.ingest_run_id for provider in children]
        if len(set(runs)) != len(runs):
            raise ValueError("Quest/Talk multi-run provider requires unique ingest_run_id values")
        self.family_contract_version = next(iter(contracts))
        self._providers = {provider.ingest_run_id: provider for provider in children}
        self._run_order = tuple(sorted(self._providers))
        # Qualification and capability snapshots are completed at startup.
        # Request dispatch only performs the child provider's low-cost
        # identity/sidecar stability checks.
        self._capability_snapshots = {
            run: self._providers[run].capability() for run in self._run_order
        }

    @property
    def providers(self) -> tuple[R5QuestTalkProvider, ...]:
        """Expose children for focused runtime instrumentation tests."""
        return tuple(self._providers[run] for run in self._run_order)

    def _active_runs(self) -> tuple[str, ...]:
        active: list[str] = []
        for run in self._run_order:
            provider = self._providers[run]
            try:
                provider._assert_stable()
            except R5QuestTalkError:
                # A drifted child is permanently invalidated by the child
                # provider.  Other exact runs remain available.
                continue
            active.append(run)
        return tuple(active)

    @staticmethod
    def _not_found(run: str, contract: str, reason: str = "exact_run_or_contract_mismatch") -> FamilyEvidenceResult:
        return FamilyEvidenceResult(
            "not_found", FAMILY, run, contract, "unavailable", False,
            {"status": "NotFound", "reason": reason},
        )

    def _select(self, run: str, contract: str) -> R5QuestTalkProvider | FamilyEvidenceResult:
        if contract != self.family_contract_version:
            return self._not_found(run, contract)
        provider = self._providers.get(run)
        if provider is None:
            return self._not_found(run, contract)
        # This check has no whole-artifact hash.  A drifted child remains
        # permanently invalid for this process and is surfaced as an exact-run
        # NotFound result; dispatch never falls through to a sibling run.
        try:
            provider._assert_stable()
        except R5QuestTalkError:
            return self._not_found(run, contract, "qualified_run_invalidated")
        return provider

    def capability(self) -> dict[str, Any]:
        active = self._active_runs()
        # Only contract-wide semantics belong at this level. Never inherit
        # coverage observations from the first live child.
        from copy import deepcopy
        shared_keys = (
            "family", "family_contract_version", "supported_operations",
            "supported_filters", "pagination_contract", "container_scope",
            "reference_delivery", "evidence_only",
        )
        snapshot = self._capability_snapshots[self._run_order[0]]
        base = {key: deepcopy(snapshot[key]) for key in shared_keys if key in snapshot}
        base["coverage_semantics"] = {
            "coverage": "manifest_selected_quest_talk_physical_closure",
            "scope": "per_exact_run",
            "corpus_complete": False,
            "product_accepted": False,
            "full_7_0_claimed": False,
        }
        if self.family_contract_version in ("quest-talk-evidence-v5", "quest-talk-evidence-v6", "quest-talk-evidence-v7"):
            base["coverage_semantics"].pop("product_accepted")
            base["coverage_semantics"]["product_acceptance"] = "per_exact_run"
        eligible_runs = []
        for run in active:
            snapshot = self._capability_snapshots[run]
            entry = deepcopy(next(item for item in snapshot["eligible_runs"] if item["ingest_run_id"] == run))
            entry["coverage_semantics"] = deepcopy(snapshot["coverage_semantics"])
            entry["counts"] = deepcopy(self._providers[run].manifest["counts"])
            eligible_runs.append(entry)
        base["evidence_available"] = bool(active)
        base["eligible_runs"] = eligible_runs
        base["multi_run"] = True
        base["run_dispatch"] = "exact_ingest_run_id"
        base["selection"] = {
            "ingest_run_id": "required_exact",
            "default_run": False,
            "latest_run": False,
            "sibling_fallback": False,
            "search_all_runs": False,
        }
        return base

    def list_evidence(self, *, ingest_run_id: str, family_contract_version: str,
                      filters: dict[str, Any] | None = None, cursor: str | None = None,
                      limit: int = 50) -> FamilyEvidenceResult:
        selected = self._select(ingest_run_id, family_contract_version)
        if isinstance(selected, FamilyEvidenceResult):
            return selected
        return selected.list_evidence(
            ingest_run_id=ingest_run_id,
            family_contract_version=family_contract_version,
            filters=filters,
            cursor=cursor,
            limit=limit,
        )

    def get_evidence(self, *, ingest_run_id: str, family_contract_version: str,
                     occurrence_id: str) -> FamilyEvidenceResult:
        selected = self._select(ingest_run_id, family_contract_version)
        if isinstance(selected, FamilyEvidenceResult):
            return selected
        return selected.get_evidence(
            ingest_run_id=ingest_run_id,
            family_contract_version=family_contract_version,
            occurrence_id=occurrence_id,
        )

    def search_text(self, *, ingest_run_id: str, family_contract_version: str,
                    query: str, filters: dict[str, Any] | None = None,
                    cursor: str | None = None, limit: int = 50) -> FamilyEvidenceResult:
        selected = self._select(ingest_run_id, family_contract_version)
        if isinstance(selected, FamilyEvidenceResult):
            return selected
        return selected.search_text(
            ingest_run_id=ingest_run_id,
            family_contract_version=family_contract_version,
            query=query,
            filters=filters,
            cursor=cursor,
            limit=limit,
        )


def build_r5_quest_talk_provider(
    artifact_path: str | Path, runtime_manifest_path: str | Path
) -> R5QuestTalkProvider:
    if json.loads(Path(runtime_manifest_path).read_text()).get("contract") == "quest-talk-bounded-runtime-v1":
        from .quest_talk_bounded_retrieval import QuestTalkBoundedProvider
        return QuestTalkBoundedProvider(artifact_path, runtime_manifest_path)
    if json.loads(Path(runtime_manifest_path).read_text()).get("contract") == "quest-talk-immutable-runtime-v1":
        from .quest_talk_immutable_retrieval import QuestTalkImmutableProvider
        return QuestTalkImmutableProvider(artifact_path, runtime_manifest_path)
    if json.loads(Path(runtime_manifest_path).read_text()).get("contract") == "quest-talk-completeness-runtime-v1":
        from .quest_talk_completeness_retrieval import QuestTalkCompleteProvider
        return QuestTalkCompleteProvider(artifact_path, runtime_manifest_path)
    if json.loads(Path(runtime_manifest_path).read_text()).get("contract") == "quest-talk-physical-runtime-v1":
        from .quest_talk_physical_retrieval import QuestTalkPhysicalProvider
        return QuestTalkPhysicalProvider(artifact_path, runtime_manifest_path)
    return R5QuestTalkProvider(artifact_path, runtime_manifest_path)


def build_quest_talk_multi_run_provider(
    bundles: Sequence[tuple[str | Path, str | Path]],
) -> QuestTalkMultiRunProvider:
    """Build one family-local v3 dispatcher from explicit artifact/manifest pairs."""
    if not bundles:
        raise ValueError("Quest/Talk multi-run bundles must not be empty")
    providers = [
        build_r5_quest_talk_provider(artifact_path, runtime_manifest_path)
        for artifact_path, runtime_manifest_path in bundles
    ]
    return QuestTalkMultiRunProvider(providers)


__all__ = [
    "COVERAGE", "FAMILY", "FAMILY_CONTRACT_VERSION", "RUNTIME_CONTRACT",
    "MEDIUM_COVERAGE", "MEDIUM_FAMILY_CONTRACT_VERSION", "MEDIUM_RUNTIME_CONTRACT",
    "MEDIUM_QUEST_IDS",
    "R5QuestTalkError", "R5QuestTalkProvider", "QuestTalkMultiRunProvider",
    "build_r5_quest_talk_provider", "build_quest_talk_multi_run_provider",
]
