from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .errors import SchemaCanaryError
from .mapping import QUEST_MAP, QUEST_TALK_MAP, TALK_MAP, deobfuscate
from .source import SourceBundle
from .util import as_hash, canonical_json, normalize_text


@dataclass(frozen=True)
class TextMapEntry:
    language: str
    shard: str
    textmap_hash: str
    text: str
    relative_path: str
    source_file_id: str
    source_record_id: str


@dataclass(frozen=True)
class TextMapConflict:
    language: str
    textmap_hash: str
    shard_a: str
    shard_b: str
    value_a: str
    value_b: str
    source_record_a: str
    source_record_b: str


@dataclass(frozen=True)
class SubtitleCue:
    index: int
    start_ms: int
    end_ms: int
    text: str


def deob_quest(raw: Any) -> dict[str, Any]:
    value = deobfuscate(raw, QUEST_MAP)
    if not isinstance(value, dict):
        raise SchemaCanaryError("Quest raw root is not an object")
    required = {"id", "titleTextMapHash", "descTextMapHash", "chapterId", "subQuests", "talks"}
    missing = sorted(required - value.keys())
    if missing:
        raise SchemaCanaryError(f"Quest 7.0 mapping missing required fields: {missing}")
    if value["id"] != 7008:
        raise SchemaCanaryError(f"selected Quest raw canary id is {value['id']}, expected 7008")
    if len(value["subQuests"]) != 19:
        raise SchemaCanaryError(f"Quest 7008 subquest canary is {len(value['subQuests'])}, expected 19")
    if len(value["talks"]) != 15:
        raise SchemaCanaryError(f"Quest 7008 talk reference canary is {len(value['talks'])}, expected 15")
    return value


def deob_quest_generic(raw: Any) -> dict[str, Any]:
    """Decode a 7.0 raw Quest without the 7008-only canaries.

    Full-corpus ingestion treats missing/invalid roots as auditable issues.  A
    valid Quest root must expose the native id and array-shaped subquest/talk
    collections; title/description fields may be absent in older or special
    records and remain nullable in the normalized metadata.
    """
    value = deobfuscate(raw, QUEST_MAP)
    if not isinstance(value, dict):
        raise SchemaCanaryError("Quest raw root is not an object")
    if not isinstance(value.get("id"), int) or isinstance(value.get("id"), bool):
        raise SchemaCanaryError("Quest native ID is missing or is not an integer")
    if not isinstance(value.get("subQuests", []), list):
        raise SchemaCanaryError("Quest subQuests is not an array")
    if not isinstance(value.get("talks", []), list):
        raise SchemaCanaryError("Quest talks is not an array")
    return value


def deob_quest_talk_reference(raw: dict[str, Any]) -> dict[str, Any]:
    value = deobfuscate(raw, QUEST_TALK_MAP)
    if not isinstance(value, dict) or "talkId" not in value:
        raise SchemaCanaryError("Quest talk reference mapping did not recover talkId")
    return value


def deob_talk(raw: Any) -> dict[str, Any]:
    value = deob_talk_generic(raw, expected_talk_id=700814)
    if len(value["dialogList"]) != 14:
        raise SchemaCanaryError(f"Talk 700814 dialog canary is {len(value['dialogList'])}, expected 14")
    ids = [item.get("id") for item in value["dialogList"]]
    expected = {70081401 + i for i in range(14)}
    if set(ids) != expected:
        raise SchemaCanaryError(f"Talk 700814 node IDs differ from verified canary: {ids}")
    return value


def deob_talk_generic(raw: Any, expected_talk_id: int | None = None) -> dict[str, Any]:
    """Decode a Talk raw object without assuming the 700814 canary shape.

    The field mapping is still versioned and the native ID is checked when an
    expected reference is supplied.  This is the parser used by Phase 2 raw
    discovery and ingestion; ``deob_talk`` remains the strict Phase 1 canary.
    """
    value = deobfuscate(raw, TALK_MAP)
    if not isinstance(value, dict):
        raise SchemaCanaryError("Talk raw root is not an object")
    required = {"talkId", "dialogList"}
    missing = sorted(required - value.keys())
    if missing:
        raise SchemaCanaryError(f"Talk 7.0 mapping missing required fields: {missing}")
    if not isinstance(value["talkId"], int) or isinstance(value["talkId"], bool):
        raise SchemaCanaryError(f"Talk native ID is not an integer: {value['talkId']!r}")
    if expected_talk_id is not None and value["talkId"] != expected_talk_id:
        raise SchemaCanaryError(f"Talk native ID {value['talkId']} disagrees with expected {expected_talk_id}")
    if not isinstance(value["dialogList"], list):
        raise SchemaCanaryError("Talk dialogList is not an array")
    node_ids: set[int] = set()
    for node in value["dialogList"]:
        if not isinstance(node, dict) or not isinstance(node.get("id"), int):
            raise SchemaCanaryError("Talk dialogList contains a node without an integer native ID")
        if node["id"] in node_ids:
            raise SchemaCanaryError(f"Talk dialogList contains duplicate node ID {node['id']}")
        node_ids.add(node["id"])
        if not isinstance(node.get("nextDialogs", []), list):
            raise SchemaCanaryError(f"Talk node {node['id']} nextDialogs is not an array")
    return value


def deob_dialog_node(raw: dict[str, Any]) -> dict[str, Any]:
    return deobfuscate(raw, TALK_MAP)


def parse_srt(raw: str) -> list[SubtitleCue]:
    normalized = normalize_text(raw).strip("\n")
    if not normalized:
        return []
    blocks = re.split(r"\n{2,}", normalized)
    result: list[SubtitleCue] = []
    for block in blocks:
        lines = block.split("\n")
        if len(lines) < 3 or not lines[0].strip().isdigit():
            continue
        match = re.match(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2}),(\d{3})", lines[1].strip())
        if not match:
            continue
        values = [int(x) for x in match.groups()]
        start = ((values[0] * 60 + values[1]) * 60 + values[2]) * 1000 + values[3]
        end = ((values[4] * 60 + values[5]) * 60 + values[6]) * 1000 + values[7]
        result.append(SubtitleCue(int(lines[0]), start, end, "\n".join(lines[2:])))
    return result


def readable_stem_from_path(path: str) -> str | None:
    stem = Path(path).stem
    for suffix in ("_CHS", "_EN", "_JP"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return stem or None


def readable_content_type(stem: str) -> str:
    prefix = re.match(r"[A-Za-z]+", stem)
    return f"readable_{(prefix.group(0).lower() if prefix else 'unknown')}"


def split_readable(raw: str) -> list[tuple[int, str]]:
    """Split on blank paragraphs; line-based fallback preserves Book1 context."""
    text = normalize_text(raw)
    paragraphs = [part for part in re.split(r"\n{2,}", text) if part != ""]
    if len(paragraphs) == 1 and "\n" in paragraphs[0]:
        paragraphs = [line for line in paragraphs[0].split("\n") if line != ""]
    return [(index, paragraph) for index, paragraph in enumerate(paragraphs)]


def subtitle_base(stem: str) -> str:
    return re.sub(r"_(?:CHS|EN|JP)$", "", stem)


def subtitle_talk_id(stem: str, known_talk_ids: Iterable[int]) -> int | None:
    known = {int(value) for value in known_talk_ids}
    match = re.search(r"AQ(\d{8})", stem)
    if not match:
        return None
    token = match.group(1)
    candidate = int(token[:-2])
    return candidate if candidate in known else None


def textmap_paths(language: str) -> list[tuple[str, str]]:
    return [
        ("main", f"TextMap/TextMap{language}.json"),
        ("medium", f"TextMap/TextMap_Medium{language}.json"),
    ]


def scan_textmaps(
    bundle: SourceBundle,
    registrar: Any,
    languages: Iterable[str],
    selected_hashes: set[str],
    exact_query: str,
) -> tuple[dict[str, list[TextMapEntry]], list[TextMapConflict], dict[str, int]]:
    """Scan complete main/Medium files, storing only selected evidence rows.

    Scanning all keys is necessary for conflict detection and the `燃珀` gold
    query.  Storage remains slice-scoped: referenced hashes plus CHS matches for
    the gold query are persisted, not the entire TextMap corpus.
    """
    entries: dict[str, list[TextMapEntry]] = {language: [] for language in languages}
    conflicts: list[TextMapConflict] = []
    counts: dict[str, int] = {}
    for language in languages:
        # Keep enough information to materialize both sides of a conflict even
        # when the hash was not otherwise selected by this slice.  A conflict
        # is evidence in its own right and must never be reduced to whichever
        # shard happened to be visited first.
        seen: dict[str, dict[str, Any]] = {}
        materialized: set[tuple[str, str, str]] = set()
        for shard, relative in textmap_paths(language):
            located, raw = bundle.read_bytes(relative)
            data = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(data, dict):
                raise SchemaCanaryError(f"TextMap {relative} root is not an object")
            source_file_id = registrar.source_file(relative, located, raw)
            counts[f"{language}:{shard}"] = len(data)
            for key, value in data.items():
                textmap_hash = str(key)
                if not isinstance(value, str):
                    registrar.issue("warning", "non_string_textmap_value", f"TextMap {relative} key {key} is not a string", source_file_id=source_file_id, details={"hash": textmap_hash})
                    continue
                is_exact_match = language == "CHS" and exact_query in value
                if is_exact_match:
                    selected_hashes.add(textmap_hash)
                previous = seen.get(textmap_hash)
                needs_record = textmap_hash in selected_hashes or is_exact_match
                is_conflict = previous is not None and previous["value"] != value
                if is_conflict:
                    needs_record = True
                    if not previous["source_record_id"]:
                        previous["source_record_id"] = registrar.record(
                            previous["relative"],
                            previous["source_file_id"],
                            previous["pointer"],
                            "textmap_entry",
                            textmap_hash,
                            previous["raw_value"],
                        )
                    if (language, textmap_hash, previous["shard"]) not in materialized:
                        entries[language].append(
                            TextMapEntry(
                                language,
                                previous["shard"],
                                textmap_hash,
                                previous["value"],
                                previous["relative"],
                                previous["source_file_id"],
                                previous["source_record_id"],
                            )
                        )
                        materialized.add((language, textmap_hash, previous["shard"]))
                    conflicts.append(
                        TextMapConflict(
                            language,
                            textmap_hash,
                            previous["shard"],
                            shard,
                            previous["value"],
                            value,
                            previous["source_record_id"],
                            "",
                        )
                    )
                source_record_id = registrar.record(
                    relative,
                    source_file_id,
                    f"/entries/{textmap_hash}",
                    "textmap_entry",
                    textmap_hash,
                    {"hash": textmap_hash, "value": value},
                ) if needs_record else None
                if source_record_id and conflicts and conflicts[-1].language == language and conflicts[-1].textmap_hash == textmap_hash and not conflicts[-1].source_record_b:
                    last = conflicts[-1]
                    conflicts[-1] = TextMapConflict(
                        last.language,
                        last.textmap_hash,
                        last.shard_a,
                        last.shard_b,
                        last.value_a,
                        last.value_b,
                        last.source_record_a,
                        source_record_id,
                    )
                if previous is None:
                    seen[textmap_hash] = {
                        "shard": shard,
                        "value": value,
                        "source_record_id": source_record_id or "",
                        "relative": relative,
                        "source_file_id": source_file_id,
                        "pointer": f"/entries/{textmap_hash}",
                        "raw_value": {"hash": textmap_hash, "value": value},
                    }
                if (textmap_hash in selected_hashes or is_conflict) and source_record_id and (language, textmap_hash, shard) not in materialized:
                    entries[language].append(TextMapEntry(language, shard, textmap_hash, value, relative, source_file_id, source_record_id))
                    materialized.add((language, textmap_hash, shard))
    return entries, conflicts, counts


def selected_text_lookup(entries: dict[str, list[TextMapEntry]]) -> dict[tuple[str, str], TextMapEntry]:
    """Prefer main over Medium while retaining both rows in the database."""
    result: dict[tuple[str, str], TextMapEntry] = {}
    for language, rows in entries.items():
        for row in sorted(rows, key=lambda item: (item.textmap_hash, 0 if item.shard == "main" else 1)):
            result.setdefault((language, row.textmap_hash), row)
    return result


def hash_refs(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if "TextMapHash" in key or key in {"titleTextMapHash", "descTextMapHash", "talkContentTextMapHash", "talkRoleNameTextMapHash"}:
                value_hash = as_hash(item)
                if value_hash:
                    result.add(value_hash)
            result.update(hash_refs(item))
    elif isinstance(value, list):
        for item in value:
            result.update(hash_refs(item))
    return result
