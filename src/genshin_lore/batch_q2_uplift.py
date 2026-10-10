"""Family-specific Q2 TextMap uplift for four accepted Wave01 families."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .batch_m0_baseline import SOURCE_SNAPSHOT_ID
from .batch_m3_r1_projection import (
    FAMILY_BY_NAME, _iter_array_records, derive_ingest_run_id_v2,
    derive_parser_build_id_v2, verify_source_pin,
)
from .family_evidence import FamilyEvidenceResult
from .util import canonical_json

Q2_ARTIFACT_CONTRACT = "batch-q2-wave01-text-uplift-v1"
Q2_COVERAGE = "R1_declared_text_fields_processed"
TEXTMAP_SHARD_SIZE = 1_468_526_592
TEXTMAP_SHARD_SHA256 = "7a32e4d40a26025162d344e7cf5d4fc7472f21cbe512767182c6df0d71f432fc"
Q2_DEFINITIONS = {
    "achievement_archive": ("titleTextMapHash", "descTextMapHash", "ps5TitleTextMapHash"),
    "dungeon_archive": ("nameTextMapHash", "displayNameTextMapHash", "descTextMapHash", "gearDescTextMapHash"),
    "npc_archive": ("nameTextMapHash",),
    "scene_archive": (),
}

class Q2ProjectionError(RuntimeError): pass

def _sha(raw: bytes) -> str: return hashlib.sha256(raw).hexdigest()
def _id(*parts: str) -> str: return _sha("\0".join(parts).encode("utf-8", "surrogateescape"))
def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""): digest.update(block)
    return digest.hexdigest()

def _identity(source_sha: str, family: str) -> tuple[str, str, str]:
    implementation = _file_sha(Path(__file__))
    config = _sha(canonical_json({"contract": Q2_ARTIFACT_CONTRACT, "family": family, "text_fields": Q2_DEFINITIONS[family], "relations": []}).encode())
    parser = derive_parser_build_id_v2(family, implementation, config)
    source_seal = _sha(canonical_json({"snapshot": SOURCE_SNAPSHOT_ID, "source_sha256": source_sha, "textmap_sha256": TEXTMAP_SHARD_SHA256}).encode())
    return parser, derive_ingest_run_id_v2(family, parser, config, source_seal), config

def build_q2_artifact(source_root: str | Path, textmap_shard: str | Path, output: str | Path) -> dict[str, Any]:
    root, textmap, target = Path(source_root).resolve(), Path(textmap_shard).resolve(), Path(output).resolve()
    verify_source_pin(root)
    if not textmap.is_file() or textmap.stat().st_size != TEXTMAP_SHARD_SIZE or _file_sha(textmap) != TEXTMAP_SHARD_SHA256:
        raise Q2ProjectionError("TextMap shard seal mismatch")
    if target.exists(): raise Q2ProjectionError("Q2 output must be absent")
    target.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(target)
    tm = sqlite3.connect(f"file:{textmap.as_posix()}?mode=ro&immutable=1", uri=True)
    tm.row_factory = sqlite3.Row
    report: dict[str, Any] = {"contract": Q2_ARTIFACT_CONTRACT, "snapshot_id": SOURCE_SNAPSHOT_ID, "corpus_complete": False, "product_accepted": False, "families": []}
    required_hashes: set[str] = set()
    for family, fields in Q2_DEFINITIONS.items():
        definition = FAMILY_BY_NAME[family]
        for _ordinal, _pointer, _token, pairs, _token_sha in _iter_array_records((root / definition.source_file).read_bytes()):
            required_hashes.update(str(value) for key, value in (pairs or []) if key in fields)
    candidates_by_hash: dict[str, list[tuple[Any, ...]]] = {key: [] for key in required_hashes}
    candidate_sql = "SELECT key_text,locale,shard_tier,relative_path,pair_ordinal,pointer,raw_key_json,raw_text_json,raw_text,raw_key_sha256,raw_text_sha256,raw_record_sha256 FROM textmap_pair_occurrence ORDER BY relative_path,pair_ordinal"
    for candidate in tm.execute(candidate_sql):
        key = str(candidate[0])
        if key in candidates_by_hash:
            candidates_by_hash[key].append(tuple(candidate[1:]))
    try:
        db.execute("PRAGMA page_size=65536"); db.execute("PRAGMA journal_mode=OFF"); db.execute("PRAGMA synchronous=OFF")
        db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID")
        for family, fields in Q2_DEFINITIONS.items():
            db.executescript(f"""
CREATE TABLE {family}_row_occurrence(occurrence_id TEXT PRIMARY KEY,ingest_run_id TEXT NOT NULL,parser_build_id TEXT NOT NULL,source_file_path TEXT NOT NULL,source_ordinal INTEGER NOT NULL,source_pointer TEXT NOT NULL,raw_record_json TEXT NOT NULL,raw_record_sha256 TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE {family}_text_owner_occurrence(owner_id TEXT PRIMARY KEY,row_occurrence_id TEXT NOT NULL,field_name TEXT NOT NULL,field_pair_ordinal INTEGER NOT NULL,raw_hash_text TEXT NOT NULL,resolution_status TEXT NOT NULL,candidate_count INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE {family}_text_resolution_occurrence(resolution_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,locale TEXT NOT NULL,resolution_status TEXT NOT NULL,candidate_count INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE {family}_text_candidate_occurrence(candidate_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,locale TEXT NOT NULL,shard_tier TEXT NOT NULL,relative_path TEXT NOT NULL,pair_ordinal INTEGER NOT NULL,pointer TEXT NOT NULL,raw_key_json TEXT NOT NULL,raw_text_json TEXT NOT NULL,raw_text TEXT NOT NULL,raw_key_sha256 TEXT NOT NULL,raw_text_sha256 TEXT NOT NULL,raw_record_sha256 TEXT NOT NULL) WITHOUT ROWID;
CREATE INDEX idx_{family}_row_order ON {family}_row_occurrence(source_ordinal,occurrence_id);
CREATE INDEX idx_{family}_owner_row ON {family}_text_owner_occurrence(row_occurrence_id,field_name,field_pair_ordinal);
CREATE INDEX idx_{family}_text_search ON {family}_text_candidate_occurrence(raw_text,locale,owner_id);
""")
            definition = FAMILY_BY_NAME[family]
            raw = (root / definition.source_file).read_bytes(); source_sha = _sha(raw)
            parser, run, config = _identity(source_sha, family)
            counts = {"raw_rows": 0, "text_owners": 0, "text_resolutions": 0, "text_candidates": 0, "missing_locale_resolutions": 0, "ambiguous_locale_resolutions": 0}
            for ordinal, pointer, token, pairs, token_sha in _iter_array_records(raw):
                row_id = _id(family, run, definition.source_file, pointer, token_sha)
                db.execute(f"INSERT INTO {family}_row_occurrence VALUES(?,?,?,?,?,?,?,?)", (row_id, run, parser, definition.source_file, ordinal, pointer, token, token_sha)); counts["raw_rows"] += 1
                for pair_ordinal, (key, value) in enumerate(pairs or []):
                    if key not in fields: continue
                    owner_id, raw_hash = _id(family, run, row_id, key, str(pair_ordinal)), str(value)
                    candidates = candidates_by_hash.get(raw_hash, [])
                    locale_counts: dict[str, int] = {}
                    for candidate in candidates: locale_counts[str(candidate[0])] = locale_counts.get(str(candidate[0]), 0) + 1
                    locale_statuses = {locale: ("missing" if locale_counts.get(locale, 0) == 0 else "resolved_unique" if locale_counts[locale] == 1 else "ambiguous") for locale in ("CHS", "EN", "JP")}
                    status = "ambiguous" if "ambiguous" in locale_statuses.values() else "partial_missing" if "missing" in locale_statuses.values() else "resolved_all_locales"
                    counts["text_owners"] += 1
                    db.execute(f"INSERT INTO {family}_text_owner_occurrence VALUES(?,?,?,?,?,?,?)", (owner_id, row_id, key, pair_ordinal, raw_hash, status, len(candidates)))
                    for locale in ("CHS", "EN", "JP"):
                        locale_status = locale_statuses[locale]; locale_count = locale_counts.get(locale, 0)
                        resolution_id = _id(family, run, owner_id, locale)
                        db.execute(f"INSERT INTO {family}_text_resolution_occurrence VALUES(?,?,?,?,?)", (resolution_id, owner_id, locale, locale_status, locale_count))
                        counts["text_resolutions"] += 1
                        counts["missing_locale_resolutions"] += locale_status == "missing"
                        counts["ambiguous_locale_resolutions"] += locale_status == "ambiguous"
                    for candidate in candidates:
                        candidate_id = _id(family, run, owner_id, str(candidate[2]), str(candidate[3]))
                        db.execute(f"INSERT INTO {family}_text_candidate_occurrence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (candidate_id, owner_id, *candidate)); counts["text_candidates"] += 1
            report["families"].append({"family": family, "family_contract_version": definition.family_contract_version.replace("-v1", "-v2"), "source_file": definition.source_file, "source_file_sha256": source_sha, "parser_build_id": parser, "ingest_run_id": run, "configuration_sha256": config, "raw_rows_complete": True, "declared_text_fields": list(fields), "declared_text_fields_processed": True, "declared_relations": [], "declared_relations_processed": True, "query_available": True, "search_available": bool(fields), "product_accepted": False, "counts": counts})
        db.execute("INSERT INTO metadata VALUES('contract',?)", (canonical_json(Q2_ARTIFACT_CONTRACT),))
        db.execute("INSERT INTO metadata VALUES('report',?)", (canonical_json(report),)); db.commit()
    finally:
        db.close(); tm.close()
    os.chmod(target, 0o600)
    report["artifact"] = {"path": str(target), "size_bytes": target.stat().st_size, "sha256": _file_sha(target)}
    return report

def load_q2_report(artifact: str | Path) -> dict[str, Any]:
    path = Path(artifact).resolve(); db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        row = db.execute("SELECT value FROM metadata WHERE key='report'").fetchone()
        value = json.loads(row[0]) if row else None
        if not isinstance(value, dict) or value.get("contract") != Q2_ARTIFACT_CONTRACT: raise Q2ProjectionError("Q2 artifact contract mismatch")
        return value
    finally: db.close()

def qualify_q2_artifact(artifact: str | Path, manifest_path: str | Path) -> dict[str, Any]:
    path, manifest_file = Path(artifact).resolve(), Path(manifest_path).resolve()
    try:
        external = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Q2ProjectionError("Q2 tracked manifest is unreadable") from exc
    binding = external.get("artifact") if isinstance(external, dict) else None
    if not isinstance(binding, dict) or binding.get("size_bytes") != path.stat().st_size or binding.get("sha256") != _file_sha(path):
        raise Q2ProjectionError("Q2 artifact size/SHA binding mismatch")
    internal = load_q2_report(path)
    for key in ("contract", "snapshot_id", "corpus_complete", "product_accepted", "families"):
        if external.get(key) != internal.get(key):
            raise Q2ProjectionError(f"Q2 internal/tracked manifest mismatch: {key}")
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        tables = {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        expected = {"metadata"} | {f"{family}_{suffix}" for family in Q2_DEFINITIONS for suffix in ("row_occurrence", "text_owner_occurrence", "text_resolution_occurrence", "text_candidate_occurrence")}
        if tables != expected:
            raise Q2ProjectionError("Q2 table closure mismatch")
        for item in internal["families"]:
            family, run, parser, counts = item["family"], item["ingest_run_id"], item["parser_build_id"], item["counts"]
            row = db.execute(f"SELECT COUNT(*),COUNT(DISTINCT ingest_run_id),MIN(ingest_run_id),COUNT(DISTINCT parser_build_id),MIN(parser_build_id) FROM {family}_row_occurrence").fetchone()
            if tuple(row) != (counts["raw_rows"], 1, run, 1, parser):
                raise Q2ProjectionError(f"Q2 row/run/parser binding mismatch: {family}")
            actual = {
                "text_owners": db.execute(f"SELECT COUNT(*) FROM {family}_text_owner_occurrence").fetchone()[0],
                "text_resolutions": db.execute(f"SELECT COUNT(*) FROM {family}_text_resolution_occurrence").fetchone()[0],
                "text_candidates": db.execute(f"SELECT COUNT(*) FROM {family}_text_candidate_occurrence").fetchone()[0],
                "missing_locale_resolutions": db.execute(f"SELECT COUNT(*) FROM {family}_text_resolution_occurrence WHERE resolution_status='missing'").fetchone()[0],
                "ambiguous_locale_resolutions": db.execute(f"SELECT COUNT(*) FROM {family}_text_resolution_occurrence WHERE resolution_status='ambiguous'").fetchone()[0],
            }
            if any(actual[key] != counts[key] for key in actual):
                raise Q2ProjectionError(f"Q2 family row-count binding mismatch: {family}")
            dangling = db.execute(f"SELECT COUNT(*) FROM {family}_text_candidate_occurrence c LEFT JOIN {family}_text_owner_occurrence o ON o.owner_id=c.owner_id WHERE o.owner_id IS NULL").fetchone()[0]
            if dangling:
                raise Q2ProjectionError(f"Q2 dangling text candidate: {family}")
    finally:
        db.close()
    return internal


def _decode_cursor(value: str | None, scope: Mapping[str, Any]) -> list[Any] | None:
    if value is None: return None
    try: payload = json.loads(base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode()))
    except Exception as exc: raise ValueError("cursor is invalid") from exc
    if payload.get("scope") != dict(scope) or not isinstance(payload.get("position"), list): raise ValueError("cursor scope mismatch")
    return list(payload["position"])
def _encode_cursor(scope: Mapping[str, Any], position: list[Any]) -> str:
    return base64.urlsafe_b64encode(canonical_json({"scope": dict(scope), "position": position}).encode()).decode().rstrip("=")

@dataclass
class Q2TextUpliftProvider:
    artifact: Path
    family: str
    qualification: Mapping[str, Any]
    def __post_init__(self) -> None:
        self.artifact = self.artifact.resolve(); report = dict(self.qualification)
        self.item = next((item for item in report["families"] if item["family"] == self.family), None)
        if self.item is None: raise Q2ProjectionError("Q2 family absent")
        self.family_contract_version = self.item["family_contract_version"]
        self.conn = sqlite3.connect(f"file:{self.artifact.as_posix()}?mode=ro&immutable=1", uri=True); self.conn.row_factory = sqlite3.Row; self.conn.execute("PRAGMA query_only=ON")
    def capability(self) -> dict[str, Any]:
        operations = ["list_family_evidence", "get_family_evidence"] + (["search_family_text"] if self.item["search_available"] else [])
        dimensions = {key: self.item[key] for key in ("raw_rows_complete", "declared_text_fields_processed", "declared_relations_processed", "query_available", "product_accepted")}
        return {"family": self.family, "family_contract_version": self.family_contract_version, "evidence_available": True, "eligible_runs": [{"ingest_run_id": self.item["ingest_run_id"], "parser_build_id": self.item["parser_build_id"], "snapshot_id": SOURCE_SNAPSHOT_ID, "family_contract_version": self.family_contract_version, "coverage": Q2_COVERAGE, "corpus_complete": False}], "supported_operations": operations, "supported_filters": {"list_family_evidence": ["source_ordinal"], "get_family_evidence": [], "search_family_text": ["locale", "field_name"]}, "pagination_contract": {"kind": "deterministic_keyset", "limit": {"default": 50, "minimum": 1, "maximum": 100}}, "coverage_semantics": dimensions, "corpus_complete": False, "evidence_only": True}
    def _result(self, status: str, data: dict[str, Any]) -> FamilyEvidenceResult:
        return FamilyEvidenceResult(status, self.family, self.item["ingest_run_id"], self.family_contract_version, Q2_COVERAGE, False, data)
    def _scope_error(self, ingest_run_id: str, family_contract_version: str) -> FamilyEvidenceResult | None:
        if ingest_run_id != self.item["ingest_run_id"] or family_contract_version != self.family_contract_version:
            return FamilyEvidenceResult("not_found", self.family, ingest_run_id, family_contract_version, "unavailable", False, {"status": "NotFound", "reason": "exact_run_or_contract_mismatch"})
        return None
    def list_evidence(self, *, ingest_run_id: str, family_contract_version: str, filters: dict[str, Any] | None = None, cursor: str | None = None, limit: int = 50) -> FamilyEvidenceResult:
        scope_error = self._scope_error(ingest_run_id, family_contract_version)
        if scope_error is not None: return scope_error
        filters = filters or {}
        if set(filters) - {"source_ordinal"}: raise ValueError("unsupported filters")
        scope = {"family": self.family, "run": ingest_run_id, "filters": filters}; position = _decode_cursor(cursor, scope); after = int(position[0]) if position else -1
        sql = f"SELECT * FROM {self.family}_row_occurrence WHERE source_ordinal>?"; params: list[Any] = [after]
        if "source_ordinal" in filters: sql += " AND source_ordinal=?"; params.append(filters["source_ordinal"])
        rows = self.conn.execute(sql + " ORDER BY source_ordinal LIMIT ?", (*params, limit + 1)).fetchall(); items = [dict(row) for row in rows[:limit]]
        return self._result("ok", {"items": items, "next_cursor": _encode_cursor(scope, [int(rows[limit - 1]["source_ordinal"])]) if len(rows) > limit else None})
    def get_evidence(self, *, ingest_run_id: str, family_contract_version: str, occurrence_id: str) -> FamilyEvidenceResult:
        scope_error = self._scope_error(ingest_run_id, family_contract_version)
        if scope_error is not None: return scope_error
        row = self.conn.execute(f"SELECT * FROM {self.family}_row_occurrence WHERE occurrence_id=?", (occurrence_id,)).fetchone()
        if row is None: return self._result("not_found", {"status": "NotFound", "reason": "occurrence_not_found"})
        owners = [dict(item) for item in self.conn.execute(f"SELECT * FROM {self.family}_text_owner_occurrence WHERE row_occurrence_id=? ORDER BY field_name,field_pair_ordinal", (occurrence_id,))]
        for owner in owners:
            owner["resolutions"] = [dict(item) for item in self.conn.execute(f"SELECT * FROM {self.family}_text_resolution_occurrence WHERE owner_id=? ORDER BY locale", (owner["owner_id"],))]
            owner["candidates"] = [dict(item) for item in self.conn.execute(f"SELECT * FROM {self.family}_text_candidate_occurrence WHERE owner_id=? ORDER BY relative_path,pair_ordinal,candidate_id", (owner["owner_id"],))]
        return self._result("ok", {"item": dict(row), "text_owners": owners})
    def search_text(self, *, ingest_run_id: str, family_contract_version: str, query: str, filters: dict[str, Any] | None = None, cursor: str | None = None, limit: int = 50) -> FamilyEvidenceResult:
        if not isinstance(query, str) or not query.strip(): raise ValueError("query must contain non-whitespace text")
        scope_error = self._scope_error(ingest_run_id, family_contract_version)
        if scope_error is not None: return scope_error
        filters = filters or {}
        if set(filters) - {"locale", "field_name"}: raise ValueError("unsupported filters")
        scope = {"family": self.family, "run": ingest_run_id, "query": query, "filters": filters}; position = _decode_cursor(cursor, scope)
        sql = f"SELECT c.*,o.row_occurrence_id,o.field_name,r.source_ordinal FROM {self.family}_text_candidate_occurrence c JOIN {self.family}_text_owner_occurrence o ON o.owner_id=c.owner_id JOIN {self.family}_row_occurrence r ON r.occurrence_id=o.row_occurrence_id WHERE instr(c.raw_text,?)>0"; params: list[Any] = [query]
        if position is not None:
            if len(position) != 4: raise ValueError("cursor position is invalid")
            sql += " AND (r.source_ordinal,c.relative_path,c.pair_ordinal,c.candidate_id)>(?,?,?,?)"; params.extend(position)
        if "locale" in filters: sql += " AND c.locale=?"; params.append(filters["locale"])
        if "field_name" in filters: sql += " AND o.field_name=?"; params.append(filters["field_name"])
        rows = self.conn.execute(sql + " ORDER BY r.source_ordinal,c.relative_path,c.pair_ordinal,c.candidate_id LIMIT ?", (*params, limit + 1)).fetchall(); items = [dict(row) for row in rows[:limit]]
        return self._result("ok", {"items": items, "next_cursor": _encode_cursor(scope, [int(rows[limit - 1]["source_ordinal"]), str(rows[limit - 1]["relative_path"]), int(rows[limit - 1]["pair_ordinal"]), str(rows[limit - 1]["candidate_id"])]) if len(rows) > limit else None})

def build_q2_providers(artifact: str | Path, manifest_path: str | Path) -> tuple[Q2TextUpliftProvider, ...]:
    qualification = qualify_q2_artifact(artifact, manifest_path)
    return tuple(Q2TextUpliftProvider(Path(artifact), family, qualification) for family in Q2_DEFINITIONS)
