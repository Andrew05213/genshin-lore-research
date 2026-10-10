"""Explicit, read-through navigation adapters for the frozen research corpus.

These adapters expose relations already present in accepted evidence artifacts.
They never infer identity from an equal number or equal text.  Each lookup is
anchored by an exact accepted occurrence or an exact physical coordinate and
is re-run against the accepted source database for every request.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def _target(query: Any, *, family: str | None, contract: str | None,
            parser: str | None, run: str | None, occurrence: str | None,
            source_path: str | None = None, pointer: str | None = None,
            raw_target: str | None = None, row_family_alias: bool = False) -> dict[str, Any]:
    projected = {
        "target_family": family,
        "target_contract": contract,
        "target_parser": parser,
        "target_run": run,
        "target_occurrence_id": occurrence,
        "target_source_path": source_path,
        "target_pointer": pointer,
        "raw_target": raw_target,
    }
    internal = dict(projected)
    if row_family_alias:internal["_row_family_alias"] = True
    projected["target_evidence_ref"] = query._target_ref(internal)
    return projected


def _relation(key: str, kind: str, status: str, complete: bool,
              namespace: str, pointer: str, raw: str | None,
              candidates: list[dict[str, Any]], direction: str = "outbound") -> dict[str, Any]:
    return {
        "key": key,
        "relation_kind": kind,
        "direction": direction,
        "status": status,
        "search_complete": complete,
        "candidate_namespace": namespace,
        "proof_source_pointer": pointer,
        "raw_locator": raw,
        "candidates": candidates,
    }


def _component_key(query: Any, family: str) -> str | None:
    rows = [k for k, c in query.components.items() if c["family"] == family]
    return rows[0] if len(rows) == 1 else None


def _character_navigation(query: Any, container: Any, relation_kind: str | None = None) -> list[dict[str, Any]]:
    """Character exact row -> accepted Character Lore navigation edges."""
    if container["source_table"] != "character_row_occurrence":
        return []
    key = _component_key(query, "character_lore_archive")
    if key is None:
        return []
    src = query._source(key)
    anchors = src.execute(
        "select * from character_lore_navigation_anchor "
        "where target_run=? and target_occurrence_id=? order by id",
        (container["evidence_run"], container["occurrence_id"]),
    ).fetchall()
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for anchor in anchors:
        sql="select * from character_lore_navigation_edge where anchor_id=?";args=[anchor["id"]]
        if relation_kind is not None:sql+=" and kind=?";args.append(relation_kind)
        for edge in src.execute(sql+" order by kind,id",args):
            candidate = _target(
                query,
                family=edge["target_family"],
                contract=edge["target_contract"],
                parser=None,
                run=edge["target_run"],
                occurrence=edge["target_occurrence_id"],
                row_family_alias=edge["target_family"] == "characters",
            )
            candidate["proof_source_pointer"] = anchor["source_pointer"]
            candidate["raw_target"] = edge["source_relation_id"]
            grouped[(edge["kind"], anchor["source_pointer"], anchor["id"])].append(candidate)
    out = []
    for (kind, pointer, anchor_id), candidates in sorted(grouped.items()):
        complete=all(x["target_evidence_ref"] is not None for x in candidates)
        status=("resolved_unique" if len(candidates)==1 else "ambiguous") if complete else "mapping_unresolved"
        out.append(_relation(
            "character-lore:" + anchor_id + ":" + kind,
            kind,
            status,
            complete,
            "accepted Character Lore navigation edges for one exact Avatar row occurrence",
            pointer,
            anchor_id,
            candidates,
        ))
    return out


def _npc_reverse_navigation(query: Any, container: Any, relation_kind: str | None = None) -> list[dict[str, Any]]:
    """Exact NPC physical occurrence -> accepted dialogue observations."""
    if container["evidence_family"] != "npc_archive" or relation_kind not in (None,"npc_dialogue_reverse_candidate"):
        return []
    key = _component_key(query, "npc_dialogue_archive")
    if key is None:
        return []
    src = query._source(key)
    rows = src.execute(
        "select c.id candidate_id,r.id relation_id,r.pointer,r.kind,r.status,r.candidate_count,"
        "n.id container_id,n.source_path,n.pointer container_pointer "
        "from npc_dialogue_candidate c "
        "join npc_dialogue_relation r on r.id=c.relation_id "
        "join npc_dialogue_container n on n.id=r.container_id "
        "where c.npc_id=? order by n.source_path,r.pointer,c.id",
        (container["occurrence_id"],),
    ).fetchall()
    if not rows:
        return []
    component = query.components[key]
    candidates = []
    for row in rows:
        candidate = _target(
            query,
            family="npc_dialogue_archive",
            contract=component["contract"],
            parser=component["parser"],
            run=component["run"],
            occurrence=row["container_id"],
            source_path=row["source_path"],
            pointer=row["container_pointer"],
            raw_target=row["candidate_id"],
        )
        candidate["proof_source_pointer"] = row["pointer"]
        candidates.append(candidate)
    source_states = {row["status"] for row in rows}
    complete = source_states == {"resolved"} and all(row["candidate_count"] == 1 for row in rows) and all(x["target_evidence_ref"] is not None for x in candidates)
    status = ("resolved_unique" if len(candidates) == 1 else "ambiguous") if complete else "mapping_unresolved"
    return [_relation(
        "npc-reverse:" + container["occurrence_id"],
        "npc_dialogue_reverse_candidate",
        status,
        complete,
        "all accepted NPC-dialogue candidates naming this exact NPC physical occurrence",
        container["source_pointer"],
        container["occurrence_id"],
        candidates,
        "inbound",
    )]


def _npc_talk_navigation(query: Any, container: Any, relation_kind: str | None = None) -> list[dict[str, Any]]:
    """NPC-dialogue observation -> its exact accepted Talk physical container."""
    if container["evidence_family"] != "npc_dialogue_archive" or relation_kind not in (None,"talk_physical_container"):
        return []
    src = query._source(container["source_key"])
    row = src.execute(
        "select * from npc_dialogue_container where id=?", (container["occurrence_id"],)
    ).fetchone()
    if row is None:
        return []
    targets = query.db.execute(
        "select * from research_container where evidence_run=? and occurrence_id=? order by id",
        (row["runtime_run"], row["runtime_id"]),
    ).fetchall()
    candidates = [
        _target(
            query,
            family=t["evidence_family"], contract=t["evidence_contract"],
            parser=t["evidence_parser"], run=t["evidence_run"],
            occurrence=t["occurrence_id"], source_path=t["source_path"],
            pointer=t["source_pointer"], raw_target=row["runtime_id"],
        )
        for t in targets
    ]
    status = "mapping_unresolved" if not candidates else "resolved_unique" if len(candidates) == 1 else "ambiguous"
    return [_relation(
        "npc-talk:" + container["occurrence_id"],
        "talk_physical_container",
        status,
        bool(candidates),
        "frozen research corpus containers at the accepted runtime_run/runtime_id locator",
        row["pointer"],
        row["runtime_id"],
        candidates,
    )]


def relations_for(query: Any, container: Any, relation_kind: str | None = None) -> list[dict[str, Any]]:
    """Return only explicitly adapted relations for one exact container."""
    rows = []
    rows.extend(_character_navigation(query, container, relation_kind))
    rows.extend(_npc_reverse_navigation(query, container, relation_kind))
    rows.extend(_npc_talk_navigation(query, container, relation_kind))
    return sorted(rows, key=lambda x: (x["relation_kind"], x["key"]))
