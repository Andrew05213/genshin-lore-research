from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def raw_json_checksum(value: Any) -> str:
    return sha256_text(canonical_json(value))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def normalize_text(value: str) -> str:
    """Only deterministic Unicode/line-ending normalization; no semantic cleanup."""
    value = unicodedata.normalize("NFC", value)
    return value.replace("\r\n", "\n").replace("\r", "\n")


def stable_id(kind: str, native_id: str | int) -> str:
    return f"agd:{kind}:{native_id}"


def logical_talk_id(namespace: str, talk_id: str | int) -> str:
    """Snapshot-independent family identifier; it may map to many evidences."""
    return stable_id("talk", f"{namespace}:{talk_id}")


def talk_evidence_id(snapshot_id: str, source_record_id: str) -> str:
    """Identity of one concrete raw Talk occurrence in one snapshot."""
    return digest_id("talk-evidence", snapshot_id, source_record_id)


def dialog_evidence_id(evidence_id: str, node_id: str | int) -> str:
    """Passage identity derived from its raw Talk evidence parent."""
    return digest_id("dialog", evidence_id, node_id)


def digest_id(prefix: str, *parts: object, length: int = 40) -> str:
    payload = "\x1f".join(str(p) for p in parts)
    return f"{prefix}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:length]}"


def json_pointer(*parts: object) -> str:
    escaped = []
    for part in parts:
        text = str(part).replace("~", "~0").replace("/", "~1")
        escaped.append(text)
    return "/" + "/".join(escaped)


def as_hash(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int) and value:
        return str(value)
    if isinstance(value, str) and value.isdigit() and int(value):
        return value
    return None


def text_occurrences(text: str, query: str) -> list[tuple[int, int]]:
    if not query:
        return []
    result: list[tuple[int, int]] = []
    start = 0
    while True:
        pos = text.find(query, start)
        if pos < 0:
            return result
        result.append((pos, pos + len(query)))
        start = pos + max(1, len(query))
