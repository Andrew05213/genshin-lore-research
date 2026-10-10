from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from .util import normalize_text


LANGUAGES = ("CHS", "EN", "JP")

TEXTMAP_SHARD_PATHS = {
    "CHS": {
        "main": "TextMap/TextMapCHS.json",
        "medium": "TextMap/TextMap_MediumCHS.json",
    },
    "EN": {
        "main": "TextMap/TextMapEN.json",
        "medium": "TextMap/TextMap_MediumEN.json",
    },
    "JP": {
        "main": "TextMap/TextMapJP.json",
        "medium": "TextMap/TextMap_MediumJP.json",
    },
}

ALL_TEXTMAP_PATHS = [
    "TextMap/TextMapCHS.json",
    "TextMap/TextMap_MediumCHS.json",
    "TextMap/TextMapEN.json",
    "TextMap/TextMap_MediumEN.json",
    "TextMap/TextMapJP.json",
    "TextMap/TextMap_MediumJP.json",
]


def parse_textmap_json_with_duplicates(
    data: str | bytes,
) -> tuple[dict[str, str], set[str]]:
    """Parse TextMap JSON and track duplicate keys."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    mapping: dict[str, str] = {}

    def _pairs_hook(pairs: list[tuple[Any, Any]]) -> dict[str, str]:
        for k, v in pairs:
            key_str = str(k)
            if key_str in seen:
                duplicates.add(key_str)
            seen.add(key_str)
            mapping[key_str] = str(v) if v is not None else ""
        return mapping

    if isinstance(data, bytes):
        data_str = data.decode("utf-8")
    else:
        data_str = data
    json.loads(data_str, object_pairs_hook=_pairs_hook)
    return mapping, duplicates


@dataclass(frozen=True)
class TextMapSourceCandidate:
    shard: str
    relative_path: str
    pointer: str
    raw_text: str
    normalized_text: str
    source_file_id: str | None = None
    source_record_id: str | None = None


@dataclass(frozen=True)
class ResolvedNodeText:
    dialog_index: int
    node_id: str
    language: str
    textmap_hash: str
    status: str  # "unique_main", "unique_medium", "both_equal", "both_conflict", "missing", "duplicate"
    raw_text: str
    normalized_text: str
    shard: str | None
    relative_path: str | None
    pointer: str | None
    source_record_id: str | None = None
    candidates: tuple[TextMapSourceCandidate, ...] = ()


@dataclass(frozen=True)
class TalkTextMapResolutionReport:
    languages: tuple[str, ...]
    selected_content_hashes: tuple[str, ...]
    resolved_count: int
    nonempty_count: int
    counts_by_language: dict[str, int]
    missing_count: int
    both_equal_count: int
    both_conflict_count: int
    duplicate_count: int
    status: str
    node_texts: tuple[ResolvedNodeText, ...]


def resolve_talk_textmap(
    dialog_list: list[dict[str, Any]],
    textmap_shards: dict[str, tuple[dict[str, str], set[str]]],
    medium_shards: dict[str, tuple[dict[str, str], set[str]]],
    languages: Iterable[str] = LANGUAGES,
) -> TalkTextMapResolutionReport:
    """Resolve dialog nodes against CHS/EN/JP TextMap shards with provenance."""
    lang_tuple = tuple(languages)
    selected_hashes: list[str] = []
    seen_hashes: set[str] = set()

    for node in dialog_list:
        h = str(node.get("talkContentTextMapHash", 0))
        if h != "0" and h not in seen_hashes:
            seen_hashes.add(h)
            selected_hashes.append(h)

    node_texts: list[ResolvedNodeText] = []
    resolved_count = 0
    nonempty_count = 0
    counts_by_language: dict[str, int] = {lang: 0 for lang in lang_tuple}
    missing_count = 0
    both_equal_count = 0
    both_conflict_count = 0
    duplicate_count = 0

    for dialog_index, node in enumerate(dialog_list):
        node_id = str(node.get("id"))
        content_hash = str(node.get("talkContentTextMapHash", 0))

        for lang in lang_tuple:
            if content_hash == "0" or not content_hash:
                status = "missing"
                raw_text = ""
                normalized = ""
                shard = None
                relative_path = None
                pointer = None
                candidates: tuple[TextMapSourceCandidate, ...] = ()
                missing_count += 1
            else:
                main_dict, main_dups = textmap_shards.get(lang, ({}, set()))
                med_dict, med_dups = medium_shards.get(lang, ({}, set()))

                if content_hash in main_dups or content_hash in med_dups:
                    status = "duplicate"
                    raw_text = ""
                    normalized = ""
                    shard = None
                    relative_path = None
                    pointer = None
                    candidates = ()
                    duplicate_count += 1
                else:
                    main_has = content_hash in main_dict
                    med_has = content_hash in med_dict
                    main_val = main_dict.get(content_hash)
                    med_val = med_dict.get(content_hash)

                    if main_has and not med_has:
                        status = "unique_main"
                        raw_text = str(main_val)
                        normalized = normalize_text(raw_text)
                        shard = "main"
                        relative_path = TEXTMAP_SHARD_PATHS[lang]["main"]
                        pointer = f"/entries/{content_hash}"
                        candidates = (
                            TextMapSourceCandidate(
                                shard="main",
                                relative_path=relative_path,
                                pointer=pointer,
                                raw_text=raw_text,
                                normalized_text=normalized,
                            ),
                        )
                    elif not main_has and med_has:
                        status = "unique_medium"
                        raw_text = str(med_val)
                        normalized = normalize_text(raw_text)
                        shard = "medium"
                        relative_path = TEXTMAP_SHARD_PATHS[lang]["medium"]
                        pointer = f"/entries/{content_hash}"
                        candidates = (
                            TextMapSourceCandidate(
                                shard="medium",
                                relative_path=relative_path,
                                pointer=pointer,
                                raw_text=raw_text,
                                normalized_text=normalized,
                            ),
                        )
                    elif main_has and med_has:
                        cand_main = TextMapSourceCandidate(
                            shard="main",
                            relative_path=TEXTMAP_SHARD_PATHS[lang]["main"],
                            pointer=f"/entries/{content_hash}",
                            raw_text=str(main_val),
                            normalized_text=normalize_text(str(main_val)),
                        )
                        cand_med = TextMapSourceCandidate(
                            shard="medium",
                            relative_path=TEXTMAP_SHARD_PATHS[lang]["medium"],
                            pointer=f"/entries/{content_hash}",
                            raw_text=str(med_val),
                            normalized_text=normalize_text(str(med_val)),
                        )
                        candidates = (cand_main, cand_med)
                        if main_val == med_val:
                            status = "both_equal"
                            raw_text = str(main_val)
                            normalized = normalize_text(raw_text)
                            shard = "main"
                            relative_path = TEXTMAP_SHARD_PATHS[lang]["main"]
                            pointer = f"/entries/{content_hash}"
                            both_equal_count += 1
                        else:
                            status = "both_conflict"
                            raw_text = ""
                            normalized = ""
                            shard = None
                            relative_path = None
                            pointer = None
                            both_conflict_count += 1
                    else:
                        status = "missing"
                        raw_text = ""
                        normalized = ""
                        shard = None
                        relative_path = None
                        pointer = None
                        candidates = ()
                        missing_count += 1

            if status in ("unique_main", "unique_medium", "both_equal"):
                resolved_count += 1
                if raw_text:
                    nonempty_count += 1
                counts_by_language[lang] = counts_by_language.get(lang, 0) + 1

            node_texts.append(
                ResolvedNodeText(
                    dialog_index=dialog_index,
                    node_id=node_id,
                    language=lang,
                    textmap_hash=content_hash,
                    status=status,
                    raw_text=raw_text,
                    normalized_text=normalized,
                    shard=shard,
                    relative_path=relative_path,
                    pointer=pointer,
                    candidates=candidates,
                )
            )

    report_status = (
        "complete"
        if missing_count == 0 and both_conflict_count == 0 and duplicate_count == 0
        else "partial"
    )

    return TalkTextMapResolutionReport(
        languages=lang_tuple,
        selected_content_hashes=tuple(selected_hashes),
        resolved_count=resolved_count,
        nonempty_count=nonempty_count,
        counts_by_language=counts_by_language,
        missing_count=missing_count,
        both_equal_count=both_equal_count,
        both_conflict_count=both_conflict_count,
        duplicate_count=duplicate_count,
        status=report_status,
        node_texts=tuple(node_texts),
    )
