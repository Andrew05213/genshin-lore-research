"""Source-bound routed access to the accepted Batch M3 R0 physical index.

Each provider owns one fixed source-root/physical-format contract.  The shared
code only retrieves physical files and top-level occurrences; it never infers
entities, relations, or cross-family identity.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from .batch_m0_baseline import SOURCE_COMMIT, SOURCE_TREE
from .batch_m3_physical import (
    INGEST_RUN_ID, PARSER_BUILD_ID, SCHEMA_VERSION, SNAPSHOT_ID,
    _iter_top_level_lexical_occurrences, _metadata,
)
from .batch_m3_r1_projection import verify_source_pin
from .family_evidence import FamilyEvidenceResult
from .util import canonical_json

R0_FAMILY_CONTRACT_VERSION = "raw-physical-evidence-v1"
INLINE_FILE_LIMIT_BYTES = 1024 * 1024

R0_COVERAGE = "R0_raw_physical_source_bound_query"
R0_EXPECTED_SIZE_BYTES = 3_561_684_992
R0_EXPECTED_SHA256 = "fe179ec528b3766ef587af36564ebc8125dc7fc82108ce16c60010a8626f522a"
R0_FAMILY_ROOTS = {
    "raw_binoutput_json_occurrence": "BinOutput",
    "raw_excel_json_occurrence": "ExcelBinOutput",
    "raw_readable_json_occurrence": "Readable",
    "raw_subtitle_json_occurrence": "Subtitle",
    "raw_textmap_pair_occurrence": "TextMap",
}


class R0RetrievalError(RuntimeError):
    """The explicit R0 artifact/source bundle cannot be exposed."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _encode_cursor(scope: Mapping[str, Any], last: tuple[int, int]) -> str:
    raw = canonical_json({"version": 1, "scope": dict(scope), "last": list(last)}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str | None, scope: Mapping[str, Any]) -> tuple[int, int] | None:
    if value is None:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode()))
    except (ValueError, TypeError, binascii.Error, json.JSONDecodeError) as exc:
        raise ValueError("cursor is invalid") from exc
    if payload.get("version") != 1 or payload.get("scope") != dict(scope):
        raise ValueError("cursor scope does not match request")
    last = payload.get("last")
    if not isinstance(last, list) or len(last) != 2 or any(type(item) is not int for item in last):
        raise ValueError("cursor position is invalid")
    return int(last[0]), int(last[1])


def _validate_filters(value: dict[str, Any] | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("filters must be an object")
    if set(value) - {"relative_path", "record_status", "parse_status"}:
        raise ValueError("unsupported R0 filters")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(item, str) or not item:
            raise ValueError(f"filters.{key} must be a non-empty string")
        result[key] = item
    return result


def _lexical_token(raw: bytes, ordinal: int, expected_pointer: str, expected_sha: str) -> str:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise R0RetrievalError("bound source is no longer UTF-8 JSON") from exc
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(text) and text[pos] in " \t\r\n":
        pos += 1
    opener = text[pos:pos + 1]
    current = 0
    if opener == "[":
        pos += 1
        while True:
            while pos < len(text) and text[pos] in " \t\r\n": pos += 1
            start = pos
            _, end = decoder.raw_decode(text, pos)
            if current == ordinal:
                token = text[start:end]
                break
            current += 1
            pos = end
            while pos < len(text) and text[pos] in " \t\r\n": pos += 1
            if pos >= len(text) or text[pos] != ",": raise R0RetrievalError("source ordinal is unavailable")
            pos += 1
    elif opener == "{":
        pos += 1
        while True:
            while pos < len(text) and text[pos] in " \t\r\n": pos += 1
            start = pos
            key, key_end = decoder.raw_decode(text, pos)
            if not isinstance(key, str): raise R0RetrievalError("source object key is invalid")
            pos = key_end
            while pos < len(text) and text[pos] in " \t\r\n": pos += 1
            if pos >= len(text) or text[pos] != ":": raise R0RetrievalError("source object pair is invalid")
            pos += 1
            while pos < len(text) and text[pos] in " \t\r\n": pos += 1
            _, end = decoder.raw_decode(text, pos)
            if current == ordinal:
                token = text[start:end]
                break
            current += 1
            pos = end
            while pos < len(text) and text[pos] in " \t\r\n": pos += 1
            if pos >= len(text) or text[pos] != ",": raise R0RetrievalError("source ordinal is unavailable")
            pos += 1
    else:
        raise R0RetrievalError("source container has no top-level physical occurrences")
    matches = [item for item in _iter_top_level_lexical_occurrences(raw) if item[0] == ordinal]
    if len(matches) != 1 or matches[0][1] != expected_pointer or matches[0][2] != expected_sha:
        raise R0RetrievalError("source coordinate or lexical occurrence seal mismatch")
    return token


@dataclass
class R0PhysicalProvider:
    artifact_path: Path
    source_root: Path
    family: str
    verify_artifact_hash: bool = True
    expected_size_bytes: int = R0_EXPECTED_SIZE_BYTES
    expected_sha256: str = R0_EXPECTED_SHA256
    verify_source_binding: bool = True

    def __post_init__(self) -> None:
        if self.family not in R0_FAMILY_ROOTS:
            raise R0RetrievalError(f"unknown R0 physical family: {self.family}")
        self.artifact_path = self.artifact_path.resolve()
        self.source_root = self.source_root.resolve()
        if not self.artifact_path.is_file() or self.artifact_path.is_symlink():
            raise R0RetrievalError("explicit R0 artifact is unavailable")
        stat = self.artifact_path.stat()
        if stat.st_size != self.expected_size_bytes:
            raise R0RetrievalError("R0 artifact size seal mismatch")
        if self.verify_artifact_hash and _sha256_file(self.artifact_path) != self.expected_sha256:
            raise R0RetrievalError("R0 artifact SHA-256 seal mismatch")
        if self.verify_source_binding:
            pin = verify_source_pin(self.source_root)
            if pin["commit"] != SOURCE_COMMIT or pin["tree"] != SOURCE_TREE:
                raise R0RetrievalError("R0 source checkout pin mismatch")
        self._conn = sqlite3.connect(
            f"file:{self.artifact_path.as_posix()}?mode=ro&immutable=1", uri=True,
            check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA query_only=ON")
        metadata = _metadata(self._conn)
        required = {
            "schema_version": SCHEMA_VERSION, "snapshot_id": SNAPSHOT_ID,
            "parser_build_id": PARSER_BUILD_ID, "ingest_run_id": INGEST_RUN_ID,
        }
        if any(metadata.get(key) != value for key, value in required.items()):
            raise R0RetrievalError("R0 artifact identity metadata mismatch")
        self.family_contract_version = R0_FAMILY_CONTRACT_VERSION

    @property
    def root(self) -> str:
        return R0_FAMILY_ROOTS[self.family]

    def capability(self) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT COUNT(*),COALESCE(SUM(occurrence_count),0) FROM source_file WHERE root=?", (self.root,)
        ).fetchone()
        return {
            "family": self.family, "family_contract_version": self.family_contract_version,
            "evidence_available": True, "eligible_runs": [{
                "ingest_run_id": INGEST_RUN_ID, "snapshot_id": SNAPSHOT_ID,
                "parser_build_id": PARSER_BUILD_ID, "family_contract_version": self.family_contract_version,
                "coverage": R0_COVERAGE, "corpus_complete": False,
            }],
            "supported_operations": ["list_family_evidence", "get_family_evidence"],
            "supported_filters": {
                "list_family_evidence": ["relative_path", "record_status", "parse_status"],
                "get_family_evidence": [], "search_family_text": [],
            },
            "pagination_contract": {"kind": "deterministic_keyset", "cursor": "opaque_r0_physical_cursor_v1", "limit": {"default": 50, "minimum": 1, "maximum": 100}},
            "coverage_semantics": {
                "coverage": R0_COVERAGE, "corpus_complete": False,
                "raw_rows_complete": True, "declared_text_fields_processed": False,
                "declared_relations_processed": False, "query_available": True,
                "product_accepted": False, "unsupported_files_are_legal": True,
                "file_count": int(row[0]), "addressable_occurrence_count": int(row[1]),
            },
            "file_body_delivery": {
                "inline_limit_bytes": INLINE_FILE_LIMIT_BYTES,
                "file_handle": "source_file_occurrence_id on every list/get item; pass unchanged to get",
                "decoding": "UTF-8 strict; BOM, Unicode and line endings preserved; non-UTF-8 as base64",
                "oversized_status": "exceeds_inline_limit",
                "semantic_parsing": False,
            },
            "container_scope": "single_physical_source_file_or_top_level_occurrence",
            "selection": {"ingest_run_id": "required_exact", "default_run": False, "latest_run": False, "sibling_fallback": False},
            "evidence_only": True,
        }

    def _result(self, status: str, data: dict[str, Any]) -> FamilyEvidenceResult:
        return FamilyEvidenceResult(status, self.family, INGEST_RUN_ID, self.family_contract_version, R0_COVERAGE, False, data)

    def _scope_error(self, ingest_run_id: str, family_contract_version: str) -> FamilyEvidenceResult | None:
        if ingest_run_id != INGEST_RUN_ID or family_contract_version != self.family_contract_version:
            return FamilyEvidenceResult("not_found", self.family, ingest_run_id, family_contract_version, "unavailable", False, {"status": "NotFound", "reason": "exact_run_or_contract_mismatch"})
        return None

    def list_evidence(self, *, ingest_run_id: str, family_contract_version: str, filters: dict[str, Any] | None = None, cursor: str | None = None, limit: int = 50) -> FamilyEvidenceResult:
        scope_error = self._scope_error(ingest_run_id, family_contract_version)
        if scope_error is not None: return scope_error
        filters = _validate_filters(filters)
        if type(limit) is not int or not 1 <= limit <= 100: raise ValueError("limit must be between 1 and 100")
        scope = {"family": self.family, "run": ingest_run_id, "contract": family_contract_version, "filters": filters}
        last = _decode_cursor(cursor, scope)
        where = ["f.root=?"]
        params: list[Any] = [self.root]
        for key, value in filters.items():
            column = {"relative_path": "f.relative_path", "record_status": "f.record_status", "parse_status": "f.parse_status"}[key]
            where.append(f"{column}=?"); params.append(value)
        if last is not None:
            where.append("(f.source_file_id>? OR (f.source_file_id=? AND COALESCE(o.occurrence_id,0)>?))")
            params.extend([last[0], last[0], last[1]])
        rows = self._conn.execute(
            "SELECT f.source_file_id,f.relative_path,f.root,f.source_sha256,f.size_bytes,f.parse_status,f.layout,f.record_status,f.occurrence_count,o.occurrence_id,o.ordinal,o.pointer,o.value_sha256 "
            "FROM source_file f LEFT JOIN physical_occurrence o ON o.source_file_id=f.source_file_id WHERE " + " AND ".join(where) +
            " ORDER BY f.source_file_id,COALESCE(o.occurrence_id,0) LIMIT ?", (*params, limit + 1)
        ).fetchall()
        items = [self._item(row, include_raw=False) for row in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            end = rows[limit - 1]
            next_cursor = _encode_cursor(scope, (int(end["source_file_id"]), int(end["occurrence_id"] or 0)))
        return self._result("ok", {"items": items, "next_cursor": next_cursor})

    def _item(self, row: sqlite3.Row, *, include_raw: bool) -> dict[str, Any]:
        occurrence = row["occurrence_id"]
        item = {
            "occurrence_id": f"record:{occurrence}" if occurrence is not None else f"file:{row['source_file_id']}",
            "source_file_occurrence_id": f"file:{row['source_file_id']}",
            "physical_container": "top_level_occurrence" if occurrence is not None else "source_file",
            "source": {"snapshot_id": SNAPSHOT_ID, "relative_path": row["relative_path"], "root": row["root"], "source_sha256": row["source_sha256"], "size_bytes": row["size_bytes"]},
            "parse_status": row["parse_status"], "layout": row["layout"], "record_status": row["record_status"],
            "ordinal": row["ordinal"], "pointer": row["pointer"], "lexical_sha256": row["value_sha256"],
        }
        if include_raw: item["raw_lexical_json"] = include_raw
        return item

    def get_evidence(self, *, ingest_run_id: str, family_contract_version: str, occurrence_id: str) -> FamilyEvidenceResult:
        scope_error = self._scope_error(ingest_run_id, family_contract_version)
        if scope_error is not None: return scope_error
        if not isinstance(occurrence_id, str) or ":" not in occurrence_id: raise ValueError("occurrence_id must be record:<id> or file:<id>")
        kind, raw_id = occurrence_id.split(":", 1)
        try: identity = int(raw_id)
        except ValueError as exc: raise ValueError("occurrence_id has an invalid numeric identity") from exc
        if kind == "record":
            row = self._conn.execute(
                "SELECT f.source_file_id,f.relative_path,f.root,f.source_sha256,f.size_bytes,f.parse_status,f.layout,f.record_status,f.occurrence_count,o.occurrence_id,o.ordinal,o.pointer,o.value_sha256 FROM physical_occurrence o JOIN source_file f ON f.source_file_id=o.source_file_id WHERE o.occurrence_id=? AND f.root=?", (identity, self.root)
            ).fetchone()
        elif kind == "file":
            row = self._conn.execute(
                "SELECT f.source_file_id,f.relative_path,f.root,f.source_sha256,f.size_bytes,f.parse_status,f.layout,f.record_status,f.occurrence_count,NULL occurrence_id,NULL ordinal,NULL pointer,NULL value_sha256 FROM source_file f WHERE f.source_file_id=? AND f.root=?", (identity, self.root)
            ).fetchone()
        else: raise ValueError("occurrence_id must be record:<id> or file:<id>")
        if row is None: return self._result("not_found", {"status": "NotFound", "reason": "occurrence_not_found", "occurrence_id": occurrence_id})
        path = self.source_root / row["relative_path"]
        raw = path.read_bytes()
        if len(raw) != row["size_bytes"] or hashlib.sha256(raw).hexdigest() != row["source_sha256"]:
            raise R0RetrievalError("bound source file seal mismatch")
        item = self._item(row, include_raw=False)
        if kind == "record": item["raw_lexical_json"] = _lexical_token(raw, int(row["ordinal"]), str(row["pointer"]), str(row["value_sha256"]))
        else:
            item["raw_file_available"] = True
            item["inline_limit_bytes"] = INLINE_FILE_LIMIT_BYTES
            item["content_complete"] = len(raw) <= INLINE_FILE_LIMIT_BYTES
            if len(raw) > INLINE_FILE_LIMIT_BYTES:
                item["body_delivery_status"] = "exceeds_inline_limit"
                item["encoding"] = None
            else:
                item["body_delivery_status"] = "complete"
                try:
                    item["raw_text"] = raw.decode("utf-8", errors="strict")
                    item["encoding"] = "utf-8"
                except UnicodeDecodeError:
                    item["raw_bytes_base64"] = base64.b64encode(raw).decode("ascii")
                    item["encoding"] = "base64"
                item["decoding_contract"] = "utf-8-strict-or-base64-v1; no normalization; BOM retained"
        return self._result("ok", {"item": item})

    def search_text(self, **kwargs: Any) -> FamilyEvidenceResult:
        return self._result("not_found", {"status": "NotFound", "reason": "operation_not_supported", "operation": "search_family_text"})


def build_r0_physical_providers(artifact_path: str | Path, source_root: str | Path, *, verify_artifact_hash: bool = True) -> tuple[R0PhysicalProvider, ...]:
    families = tuple(R0_FAMILY_ROOTS)
    first = R0PhysicalProvider(Path(artifact_path), Path(source_root), families[0], verify_artifact_hash)
    peers = tuple(R0PhysicalProvider(Path(artifact_path), Path(source_root), family, False, R0_EXPECTED_SIZE_BYTES, R0_EXPECTED_SHA256, False) for family in families[1:])
    return (first, *peers)
