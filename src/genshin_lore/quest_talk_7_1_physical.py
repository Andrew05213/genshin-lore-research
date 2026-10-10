"""Strict 7.1 physical Quest/Talk decoder candidate.

It never mutates the accepted 7.0 shape parser. Unknown shapes fail closed;
these source occurrences are not cross-snapshot logical identities.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from .quest_talk_physical_shapes import (
    DIALOGUES, classify as classify_7_0, integer, nodes as nodes_7_0,
    quest as quest_7_0, references as references_7_0,
)

TALK_LIST = "PCIAMAFDDAA"
WRAPPER_LIST = "DLLABGGCEBM"
QUEST_CHILDREN = "JIJKODHIEED"
DIRECT_REFERENCE_MARKER = "DGAIPHFGBOD"
NEW_TALK_ROOT_KEYS = {TALK_LIST, "talkId", "type"}
NEW_WRAPPER_ROOT_KEYS = {
    WRAPPER_LIST, "GCNCAGHDDOJ", "NJLNCONMABL", "groupId",
    "JNNALCLEEPD", "activityId",
}
NEW_DIRECT_REFERENCE_KEYS = {
    DIRECT_REFERENCE_MARKER, "FAPCNCGCEBJ", "GCNCAGHDDOJ", "GIFBNGBODIM",
    "JEDNDGCOMGC", "KJHEKKNGOOG", "NAGBOCFAGLL", "NBKACBDNMMJ",
    "NCBFPFHGHOH", "NNEHBCLEGHG", "PNNEOGAMPLE", "activeMode",
    "id", "priority", "questId",
}
NEW_NO_CHILD_QUEST_KEYS = {
    "AKEAJELNNEN", "DLLABGGCEBM", "DOOCLIPFECE", "IMGDIONBDMG",
    "INFDFLBGLPD", "JNHHOJAPDPP", "KJBJDKALING", "KJOHNNANDBK",
    "NBOJMAHCCGM", "activeMode", "activityId", "id", "luaPath",
    "mainQuestTag", "series", "showType", "type",
}

SIGNATURE_MANIFEST = Path(__file__).resolve().parents[2] / "manifests/quest-talk-7.1/root-signature-digests.json"


def root_signature_digest(doc: dict) -> str:
    return hashlib.sha256(json.dumps(sorted(doc), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@lru_cache(maxsize=2)
def _registered(kind: str) -> frozenset[str]:
    manifest = json.loads(SIGNATURE_MANIFEST.read_text())
    if manifest.get("contract") != "quest-talk-7.1-observed-root-signatures-v1":
        raise ValueError("unknown 7.1 root signature contract")
    return frozenset(manifest[kind]["signature_digests"])


def _require_registered(doc: dict, kind: str, provided=None) -> None:
    accepted = _registered(kind) if provided is None else provided
    if root_signature_digest(doc) not in accepted:
        raise ValueError(f"unregistered 7.1 {kind} physical root signature")


def _physical_list(value, label):
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a physical list")
    return value


def _new_nodes(values, field):
    entries = []
    for ordinal, child in enumerate(_physical_list(values, field)):
        if not isinstance(child, dict):
            raise ValueError("Talk child must be an object")
        child_id = integer(child["id"], "Talk child id")
        successors = _physical_list(child.get("GLJCECCOEDP", []), "Talk successors")
        for successor in successors:
            integer(successor, "Talk successor ID")
        text_hash = child.get("LKECPJIFFEE")
        if text_hash is not None:
            integer(text_hash, "Talk text hash")
        entries.append({
            "ordinal": ordinal,
            "pointer": f"/{field}/{ordinal}",
            "native_id": child_id,
            "next": successors,
            "text_hash": text_hash,
            "text_mapping": "literal_key",
        })
    return entries


def _quest_embedded(doc):
    fields = [field for field in ("PCIAMAFDDAA", "PFALHAKIILD", "GALIDJOEHOC") if field in doc]
    if len(fields) > 1:
        raise ValueError("multiple Quest embedded physical dialogue lists")
    if not fields:
        return []
    field = fields[0]
    if field == "PCIAMAFDDAA":
        entries = _new_nodes(doc[field], field)
        mapping = "7.1-direct"
    else:
        mapping = "rc" if field == "PFALHAKIILD" else "alt"
        entries = [
            {key: node[key] for key in ("ordinal", "pointer", "native_id", "next", "text_hash", "text_mapping")}
            for node in nodes_7_0(doc[field], mapping, embedded=True)
        ]
    return [{"field": field, "pointer": f"/{field}", "mapping": mapping, "nodes": entries}]


def decode_talk(doc: dict, old_shapes: list[dict], *, registered_signatures=None) -> dict:
    if not isinstance(doc, dict):
        raise ValueError("Talk physical root must be an object")
    _require_registered(doc, "talk", registered_signatures)
    keys = set(doc)
    if TALK_LIST in keys:
        if not keys <= NEW_TALK_ROOT_KEYS or WRAPPER_LIST in keys:
            raise ValueError("unregistered 7.1 Talk root field combination")
        native = integer(doc["talkId"], "talkId") if "talkId" in doc else None
        if native is None and keys != {TALK_LIST, "type"}:
            raise ValueError("Talk root native ID missing outside registered no-ID shape")
        entries = _new_nodes(doc[TALK_LIST], TALK_LIST)
        return {"kind": "talk_root" if native is not None else "talk_root_no_id",
                "mapping": "7.1-direct", "native_id": native, "nodes": entries, "references": []}
    if WRAPPER_LIST in keys:
        if not keys <= NEW_WRAPPER_ROOT_KEYS:
            raise ValueError("unregistered 7.1 Talk wrapper field combination")
        refs = []
        for ordinal, entry in enumerate(_physical_list(doc[WRAPPER_LIST], WRAPPER_LIST)):
            if not isinstance(entry, dict):
                raise ValueError("Talk wrapper child must be object")
            ref_id = integer(entry["id"], "Talk wrapper child id")
            init = entry.get("NNEHBCLEGHG")
            if init is not None:
                integer(init, "Talk wrapper init dialogue")
            refs.append({"pointer": f"/{WRAPPER_LIST}/{ordinal}", "native_id": ref_id,
                         "init_dialogue": init})
        return {"kind": "reference_wrapper", "mapping": "7.1-wrapper", "native_id": None,
                "nodes": [], "references": refs}
    if DIRECT_REFERENCE_MARKER in keys:
        if not keys <= NEW_DIRECT_REFERENCE_KEYS or "id" not in doc:
            raise ValueError("unregistered 7.1 direct Talk reference fields")
        ref_id = integer(doc["id"], "direct Talk reference id")
        init = doc.get("NNEHBCLEGHG")
        if init is not None:
            integer(init, "direct Talk init dialogue")
        return {"kind": "reference_wrapper", "mapping": "7.1-direct-reference", "native_id": None, "nodes": [],
                "references": [{"pointer": "/", "native_id": ref_id, "init_dialogue": init}]}
    if keys == {"talkId"}:
        integer(doc["talkId"], "terminal Talk ID")
        return {"kind": "mapping_unresolved", "mapping": "7.1-terminal", "native_id": doc["talkId"],
                "nodes": [], "references": []}
    # This is an exact registered 7.0 root signature still physically present
    # in 7.1, not a fallback to another snapshot or a guessed mapping.
    old = classify_7_0(doc, old_shapes)
    if old["kind"] == "talk_root":
        mapping = old["mapping"]
        native = integer(doc[DIALOGUES[mapping][0]], "registered legacy Talk ID")
        entries = [
            {key: node[key] for key in ("ordinal", "pointer", "native_id", "next", "text_hash", "text_mapping")}
            for node in nodes_7_0(doc, mapping)
        ]
        return {"kind": "talk_root", "mapping": mapping, "native_id": native, "nodes": entries, "references": []}
    if old["kind"] == "reference_wrapper":
        refs = references_7_0(doc, old["reference_field"], old["mapping"])
        return {"kind": "reference_wrapper", "mapping": old["mapping"], "native_id": None, "nodes": [],
                "references": [{"pointer": ref["pointer"], "native_id": ref["native_id"],
                                "init_dialogue": None} for ref in refs]}
    return {"kind": "mapping_unresolved", "mapping": old["mapping"] or "legacy-terminal-unresolved", "native_id": None,
            "nodes": [], "references": []}


def decode_quest(doc: dict, *, registered_signatures=None) -> dict:
    if not isinstance(doc, dict):
        raise ValueError("Quest physical root must be object")
    _require_registered(doc, "quest", registered_signatures)
    new_regular = {"id", QUEST_CHILDREN} <= set(doc)
    new_no_child = (
        {"id", "luaPath", "KJBJDKALING", "KJOHNNANDBK"} <= set(doc)
        and QUEST_CHILDREN not in doc and set(doc) <= NEW_NO_CHILD_QUEST_KEYS
    )
    if new_regular or new_no_child:
        native = integer(doc["id"], "Quest id")
        children = _physical_list(doc[QUEST_CHILDREN], "Quest children") if new_regular else []
        if any(not isinstance(child, dict) for child in children):
            raise ValueError("Quest child must be physical object")
        reference_field_status = "present" if WRAPPER_LIST in doc else "source_field_absent"
        refs = []
        for ordinal, entry in enumerate(_physical_list(doc[WRAPPER_LIST], "Quest Talk references") if WRAPPER_LIST in doc else []):
            if not isinstance(entry, dict):
                raise ValueError("Quest Talk reference child must be object")
            ref_id = integer(entry["id"], "Quest Talk reference id")
            init = entry.get("NNEHBCLEGHG")
            if init is not None:
                integer(init, "Quest Talk init dialogue")
            refs.append({"pointer": f"/{WRAPPER_LIST}/{ordinal}", "native_id": ref_id,
                         "init_dialogue": init})
        return {"kind": "quest_root", "native_id": native,
                "children_pointer": f"/{QUEST_CHILDREN}" if new_regular else None,
                "children_field_status": "present" if new_regular else "source_field_absent",
                "child_count": len(children),
                "child_pointers": [f"/{QUEST_CHILDREN}/{ordinal}" for ordinal in range(len(children))],
                "reference_field_status": reference_field_status,
                "references": refs,
                "embedded": _quest_embedded(doc)}
    # Only the exact old required-key groups are eligible for the historical
    # parser. Its disjoint-shape check still rejects unknown/conflicting roots.
    if not any(all(key in doc for key in fields[1:]) for fields in (
        ("OIFGMOHKPOI", "OJACLOOEAMG", "EBNBLBEIFFJ"),
        ("NFIEHACCECI", "NFFIGDHFAJG", "MEGJPCLADOG"),
    )):
        raise ValueError("unregistered 7.1 Quest physical shape")
    mapping, native, refs = quest_7_0(doc)
    child_key = "EBNBLBEIFFJ" if mapping == "rc" else "MEGJPCLADOG"
    return {"kind": "quest_root", "native_id": native,
            "children_pointer": f"/{child_key}", "child_count": len(doc[child_key]),
            "children_field_status": "present",
            "child_pointers": [f"/{child_key}/{ordinal}" for ordinal in range(len(doc[child_key]))],
            "reference_field_status": "present",
            "references": [{"pointer": ref["pointer"], "native_id": ref["native_id"],
                            "init_dialogue": None} for ref in refs],
            "embedded": _quest_embedded(doc)}
