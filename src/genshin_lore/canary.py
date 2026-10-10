"""Small, reproducible canary-Quest evaluation for Phase 2.

Canaries are selected from the fixed snapshot's native TalkExcel rows.  The
selection is rule-based (performCfg family plus oldest/newest native quest ID)
and is recorded in the report so it cannot silently become a hand-picked
success list.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .discovery import TalkDiscoveryIndex
from .parsers import deob_talk_generic, hash_refs
from .source import SourceBundle


CANARY_KINDS = ("AQ", "WQ", "LQ", "EQ")


def load_talk_excel_groups(bundle: SourceBundle) -> dict[int, list[dict[str, Any]]]:
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for relative in ("ExcelBinOutput/TalkExcelConfigData_0.json", "ExcelBinOutput/TalkExcelConfigData_1.json"):
        located = bundle.try_locate(relative)
        if located is None:
            continue
        value = json.loads(located.path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, list):
            continue
        for row in value:
            if isinstance(row, dict) and isinstance(row.get("questId"), int) and isinstance(row.get("id"), int):
                groups[int(row["questId"])].append(row)
    for rows in groups.values():
        rows.sort(key=lambda row: (int(row["id"]), str(row.get("performCfg", ""))))
    return dict(groups)


def talk_family(rows: list[dict[str, Any]]) -> str:
    for row in rows:
        value = row.get("performCfg")
        if not isinstance(value, str) or not value:
            continue
        parts = value.split("/")
        if len(parts) >= 2 and parts[0] == "QuestDialogue":
            return parts[1]
    return "unknown"


def choose_canary_quests(groups: dict[int, list[dict[str, Any]]], *, exclude: set[int] | None = None) -> list[dict[str, Any]]:
    excluded = exclude or set()
    chosen: list[dict[str, Any]] = []
    used: set[int] = set(excluded)
    for family in CANARY_KINDS:
        candidates = sorted((quest_id for quest_id, rows in groups.items() if quest_id not in used and talk_family(rows) == family))
        if not candidates:
            continue
        for band, quest_id in (("early_native_id", candidates[0]), ("late_native_id", candidates[-1])):
            if quest_id in used:
                continue
            used.add(quest_id)
            chosen.append({"quest_id": quest_id, "family": family, "selection_band": band})
    # Fill any missing category with the first deterministic non-category
    # group, rather than inventing an ID.
    if len(chosen) < 8:
        for quest_id in sorted(groups):
            if quest_id in used or not groups[quest_id]:
                continue
            used.add(quest_id)
            chosen.append({"quest_id": quest_id, "family": talk_family(groups[quest_id]), "selection_band": "fallback_sorted_native_id"})
            if len(chosen) >= 8:
                break
    return chosen[:10]


def _graph_summary(normalized: dict[str, Any]) -> dict[str, Any]:
    nodes = normalized.get("dialogList", [])
    ids = {int(node["id"]) for node in nodes if isinstance(node, dict) and isinstance(node.get("id"), int)}
    next_map: dict[int, list[int]] = {
        int(node["id"]): [int(value) for value in node.get("nextDialogs", []) if isinstance(value, int)]
        for node in nodes
        if isinstance(node, dict) and isinstance(node.get("id"), int)
    }
    incoming: dict[int, int] = {node_id: 0 for node_id in ids}
    for targets in next_map.values():
        for target in targets:
            incoming[target] = incoming.get(target, 0) + 1
    branch_nodes = sorted(node_id for node_id, targets in next_map.items() if len(targets) > 1)
    merge_nodes = sorted(node_id for node_id, count in incoming.items() if count > 1)
    return {
        "node_count": len(nodes),
        "edge_count": sum(len(targets) for targets in next_map.values()),
        "branch_nodes": branch_nodes,
        "merge_nodes": merge_nodes,
        "dangling_edge_count": sum(1 for targets in next_map.values() for target in targets if target not in ids),
    }


def _textmap_sets(bundle: SourceBundle) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for language in ("CHS", "EN", "JP"):
        hashes: set[str] = set()
        for relative in (f"TextMap/TextMap{language}.json", f"TextMap/TextMap_Medium{language}.json"):
            located = bundle.try_locate(relative)
            if located is None:
                continue
            value = json.loads(located.path.read_text(encoding="utf-8-sig"))
            if isinstance(value, dict):
                hashes.update(str(key) for key in value)
        result[language] = hashes
    return result


def evaluate_canaries(
    bundle: SourceBundle,
    index: TalkDiscoveryIndex,
    *,
    exclude: set[int] | None = None,
) -> dict[str, Any]:
    groups = load_talk_excel_groups(bundle)
    selections = choose_canary_quests(groups, exclude=exclude)
    textmaps = _textmap_sets(bundle)
    evaluated: list[dict[str, Any]] = []
    for selection in selections:
        quest_id = int(selection["quest_id"])
        rows = groups.get(quest_id, [])
        talk_ids = sorted({int(row["id"]) for row in rows})
        expected_init_dialogs = {
            int(row["id"]): int(row["initDialog"])
            for row in rows
            if isinstance(row.get("id"), int) and isinstance(row.get("initDialog"), int)
        }
        statuses = {talk_id: index.resolve(talk_id, expected_init_dialogs.get(talk_id)) for talk_id in talk_ids}
        resolved = [talk_id for talk_id, status in statuses.items() if status.status == "resolved"]
        absent = [talk_id for talk_id, status in statuses.items() if status.status == "genuinely_absent"]
        ambiguous = [talk_id for talk_id, status in statuses.items() if status.status == "ambiguous"]
        unresolved = [talk_id for talk_id, status in statuses.items() if status.status == "unresolved"]
        graph_ok = True
        textmap_ok = True
        native_ids_ok = True
        talks: list[dict[str, Any]] = []
        for talk_id in talk_ids:
            status = statuses[talk_id]
            item: dict[str, Any] = {"talk_id": talk_id, "status": status.status, "native_id_verified": status.native_id_verified}
            candidate = status.candidate
            if candidate is None:
                talks.append(item)
                continue
            try:
                located, raw = bundle.read_bytes(candidate.relative_path)
                normalized = deob_talk_generic(json.loads(raw.decode("utf-8-sig")), expected_talk_id=talk_id)
                native_ids_ok = native_ids_ok and normalized.get("talkId") == talk_id
                graph = _graph_summary(normalized)
                graph_ok = graph_ok and graph["dangling_edge_count"] == 0
                hashes = hash_refs(normalized)
                missing_languages = {language: sorted(value for value in hashes if value not in textmaps[language]) for language in ("CHS", "EN", "JP")}
                textmap_ok = textmap_ok and not any(missing_languages.values())
                item.update({"relative_path": candidate.relative_path, "raw_sha256": candidate.raw_sha256, "normalized_checksum": candidate.normalized_checksum, "graph": graph, "textmap_hash_count": len(hashes), "missing_textmap_hashes": missing_languages, "source_root": str(located.root)})
            except Exception as exc:  # audit failure belongs in the report, never a silent fallback
                graph_ok = False
                native_ids_ok = False
                item["error"] = str(exc)
            talks.append(item)
        evaluated.append(
            {
                **selection,
                "snapshot_release": index.release_label,
                "snapshot_id": index.snapshot_id,
                "talk_reference_count": len(rows),
                "unique_talk_count": len(talk_ids),
                "resolved_raw_talk_count": len(resolved),
                "genuinely_absent_count": len(absent),
                "ambiguous_count": len(ambiguous),
                "unresolved_count": len(unresolved),
                "resolved_rate": (len(resolved) / len(talk_ids)) if talk_ids else 0.0,
                "native_id_verification": native_ids_ok,
                "graph_recovery": graph_ok,
                "textmap_linkage": textmap_ok,
                "talks": talks,
            }
        )
    return {
        "selection_rule": "performCfg QuestDialogue family (AQ/WQ/LQ/EQ), earliest/latest native questId per family, deterministic sorted fallback; Quest 7008 excluded",
        "canary_count": len(evaluated),
        "quests": evaluated,
        "summary": {
            "talk_reference_count": sum(item["talk_reference_count"] for item in evaluated),
            "unique_talk_count": sum(item["unique_talk_count"] for item in evaluated),
            "resolved_raw_talk_count": sum(item["resolved_raw_talk_count"] for item in evaluated),
            "genuinely_absent_count": sum(item["genuinely_absent_count"] for item in evaluated),
            "ambiguous_count": sum(item["ambiguous_count"] for item in evaluated),
            "unresolved_count": sum(item["unresolved_count"] for item in evaluated),
            "all_native_ids_verified": all(item["native_id_verification"] for item in evaluated),
            "all_graphs_recovered": all(item["graph_recovery"] for item in evaluated),
            "all_textmap_linkage_recovered": all(item["textmap_linkage"] for item in evaluated),
        },
    }
