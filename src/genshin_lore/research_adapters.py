"""Explicit adapters for the frozen research corpus.

The registry names every authoritative source table.  It deliberately does
not discover adapters from suffixes or column names.  SQL construction is
mechanical; identity and provenance rules remain adapter-specific.
"""
from __future__ import annotations

from typing import Any


ROW_FAMILIES: dict[str, dict[str, Any]] = {
    "loading_tips_archive": dict(parent="loading_tip_row", slot="loading_tip_slot", text="loading_tip_text", parent_fk="row_id", owner="field", kind="loading_tip"),
    "gcg_card_archive": dict(parent="gcg_card_row", slot="gcg_card_slot", text="gcg_card_text", parent_fk="row_id", owner="field", kind="gcg_card"),
    "document_archive": dict(parent="document_row", slot="document_slot", text="document_text", parent_fk="row_id", owner="field", kind="document"),
    "character_lore_archive": dict(parent="character_lore_row", slot="character_lore_slot", text="character_lore_text", parent_fk="row_id", owner="field", kind="character_lore"),
    "quest_narrative_archive": dict(parent="quest_narrative_container", slot="quest_narrative_text_slot", text="quest_narrative_text_candidate", parent_fk="container_id", owner="owner_field", kind="quest_narrative"),
    "quest_review_archive": dict(parent="quest_review_row", slot="quest_review_text_slot", text="quest_review_text_candidate", parent_fk="row_id", owner="owner_field", kind="quest_review"),
    "mail_narrative_archive": dict(parent="mail_row", slot="mail_slot", text="mail_text", parent_fk="row_id", owner="owner_field", kind="mail"),
    "event_narrative_archive": dict(parent="event_narrative_row", slot="event_narrative_slot", text="event_narrative_text", parent_fk="row_id", owner="owner_field", kind="event_narrative"),
    "native_archive": dict(parent="native_archive_container", slot="native_archive_slot", text="native_archive_text", parent_fk="container_id", owner="owner_field", kind="native_archive"),
}

CONTAINER_ONLY: dict[str, dict[str, Any]] = {
    "npc_dialogue_archive": dict(parent="npc_dialogue_container", kind="npc_dialogue"),
    "talk_physical_archive": dict(parent="talk_physical_file", kind="talk_physical_file"),
}

DIRECT_TEXT: dict[str, dict[str, Any]] = {
    "readable_archive": dict(parent="readable_file", text="readable_line", fk="file_id", kind="readable_file", child_kind="line"),
    "subtitle_archive": dict(parent="subtitle_file", text="subtitle_record", fk="file_id", kind="subtitle_file", child_kind="cue"),
}

RELATIONS: dict[str, list[dict[str, Any]]] = {
    "gcg_card_archive": [dict(table="gcg_card_relation", candidate="gcg_card_candidate", parent="row_id", kind="gcg_card_reference", pointer="pointer", raw="raw_value")],
    "document_archive": [dict(table="document_relation", candidate="document_candidate", parent="row_id", kind_col="kind", pointer="origin_pointer", raw="raw_value", namespace="boundary", target_mode="readable_path")],
    "npc_dialogue_archive": [dict(table="npc_dialogue_relation", candidate="npc_dialogue_candidate", parent="container_id", kind_col="kind", pointer="pointer", raw="raw_locator", complete_literal=0, namespace_literal="npc_id_namespace_unproven", target_mode="raw_npc_id")],
    "character_lore_archive": [
        dict(table="character_lore_relation", candidate="character_lore_candidate", parent="row_id", kind_col="kind", pointer="source_pointer", raw="raw_value", namespace="target_namespace", target_mode="exact"),
        dict(table="character_lore_reuse_relation", candidate="character_lore_reuse_candidate", parent="reuse_id", kind_col="kind", pointer="source_pointer", raw="raw_value", namespace="target_namespace", target_mode="exact"),
    ],
    "quest_narrative_archive": [dict(table="quest_narrative_relation", candidate="quest_narrative_candidate", parent="container_id", kind_col="kind", pointer="source_pointer", raw="raw_locator_json", namespace="candidate_namespace", target_mode="exact")],
    "quest_review_archive": [dict(table="quest_review_relation", candidate="quest_review_candidate", parent="row_id", kind_col="kind", pointer="source_pointer", raw="raw_value_json", namespace="candidate_namespace", target_mode="exact")],
    "codexquest_archive": [dict(table="codexquest_relation", candidate="codexquest_candidate", parent="container_id", kind_col="kind", pointer="source_pointer", raw="raw_value_json", namespace="candidate_namespace", target_mode="exact")],
    "mail_narrative_archive": [dict(table="mail_relation", candidate="mail_candidate", parent="row_id", kind_col="kind", pointer="source_pointer", raw="raw_value", namespace="candidate_namespace", target_mode="local_row")],
    "event_narrative_archive": [dict(table="event_narrative_relation", candidate="event_narrative_relation_candidate", parent="row_id", kind_col="relation_kind", pointer="source_pointer", raw="raw_locator", namespace="candidate_namespace", target_mode="exact")],
    "native_archive": [dict(table="native_archive_relation", candidate="native_archive_candidate", parent="container_id", kind_col="relation_kind", pointer="source_pointer", raw="raw_locator", namespace="candidate_namespace", target_mode="exact")],
}

# Frozen accepted relation-like tables for which v2 deliberately has no safe
# coordinate adapter.  Listing these is part of the contract: they must be
# reported as unsupported/partial, never silently treated as an empty scope.
UNSUPPORTED_RELATION_TABLES: dict[str, tuple[str, ...]] = {
    "character_lore_archive": ("character_lore_navigation_edge",),
    "talk_physical_archive": ("talk_physical_reference", "talk_physical_candidate"),
    "readable_archive": ("readable_relation", "readable_candidate"),
    "subtitle_archive": ("subtitle_relation", "subtitle_candidate"),
    "quest_talk_pilot": (
        "document_relation", "passage_edge", "occurrence_edge",
        "candidate_scope_proof", "comparison_scope_proof", "comparison_match_candidate",
        "quest_talk_physical_reference",
    ),
    "schema20_core": (
        "document_relation", "passage_edge", "occurrence_edge",
        "candidate_scope_proof", "comparison_scope_proof", "comparison_match_candidate",
        "static_relationship_occurrence", "character_relation_occurrence",
        "tutorial_detail_relation_occurrence", "tutorial_image_reference_occurrence",
        "tutorial_push_tips_candidate_occurrence",
        "world_archive_relationship_occurrence", "world_archive_asset_reference_occurrence",
        "quest_codex_relationship_occurrence", "quest_codex_relationship_candidate_occurrence",
        "quest_codex_asset_reference_occurrence",
        "avatar_codex_relationship_occurrence", "avatar_codex_relationship_candidate_occurrence",
        "cook_recipe_archive_relationship_occurrence", "cook_recipe_archive_relationship_candidate_occurrence",
        "cook_bonus_archive_relationship_occurrence", "cook_bonus_archive_relationship_candidate_occurrence",
        "homeworld_furniture_archive_relationship_occurrence", "homeworld_furniture_archive_relationship_candidate_occurrence",
        "furniture_make_archive_relationship_occurrence", "furniture_make_archive_relationship_candidate_occurrence",
        "furniture_suite_archive_relationship_occurrence", "furniture_suite_archive_relationship_candidate_occurrence",
        "profile_picture_archive_relationship_occurrence", "profile_picture_archive_relationship_candidate_occurrence",
        "gadget_title_archive_relationship_occurrence", "gadget_title_archive_relationship_candidate_occurrence",
    ),
}

RELATION_NAME_MARKERS = ("relation", "candidate", "edge", "reference", "scope_proof")


def relation_like_tables(db: Any, family: str) -> set[str]:
    """Inventory possible relation scopes; used only to fail on registry drift."""
    tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
    text_tables = set(declared_tables(family)["texts"])
    return {
        t for t in tables
        if t not in text_tables
        and "text_candidate" not in t
        and any(marker in t for marker in RELATION_NAME_MARKERS)
        and t not in {"schema_migration", "artifact_release"}
    }


def declared_relation_tables(family: str) -> set[str]:
    out = set(UNSUPPORTED_RELATION_TABLES.get(family, ()))
    for spec in RELATIONS.get(family, ()):
        out.add(spec["table"])
        out.add(spec["candidate"])
    return out

# Exact accepted Schema20 tables.  Each prefix is an independent routed family;
# the row table, rather than the aggregate artifact, supplies evidence identity.
CORE_PROJECTED_PREFIXES = (
    "achievement_archive", "activity_archive", "avatar_config_archive",
    "dungeon_archive", "material_config_archive", "monster_archive",
    "npc_archive", "reliquary_config_archive", "scene_archive",
    "weapon_config_archive", "cook_recipe_archive", "cook_bonus_archive",
    "furniture_make_archive", "furniture_suite_archive", "gadget_title_archive",
    "homeworld_bgm_archive", "homeworld_furniture_archive",
    "homeworld_furniture_type_archive", "profile_frame_archive",
    "profile_picture_archive",
)

CORE_SPECIAL = {
    "avatar_codex_archive": dict(row="avatar_codex_row_occurrence", text=None),
    "quest_codex_archive": dict(row="quest_codex_row_occurrence", text="quest_codex_text_candidate_occurrence"),
    "world_archive": dict(row="world_archive_row_occurrence", text="world_archive_text_candidate_occurrence"),
    "core_tutorial_archive": dict(row="tutorial_row_occurrence", text="tutorial_text_candidate_occurrence"),
    "character_archive": dict(row="character_row_occurrence", text="character_text_candidate_occurrence"),
}

CORE_DIRECT = {
    "quest_corpus_evidence": dict(parent="passage_occurrence", text="passage_occurrence_text", fk="passage_occurrence_id", kind="passage"),
    "static_archive": dict(parent="static_evidence_container", text="static_segment_occurrence", fk="evidence_container_id", kind="static_container"),
}

QUEST_TALK = dict(
    parent="quest_talk_physical_container",
    text="quest_talk_physical_text",
    node="quest_talk_physical_node",
    parent_fk="container_id",
    kind="quest_talk",
)


def declared_tables(family: str) -> dict[str, list[str]]:
    """Return the explicit tables declared by an adapter."""
    containers: list[str] = []
    texts: list[str] = []
    relations: list[str] = []
    candidates: list[str] = []
    if family in ROW_FAMILIES:
        x = ROW_FAMILIES[family]; containers.append(x["parent"]); texts.append(x["text"])
    if family in CONTAINER_ONLY: containers.append(CONTAINER_ONLY[family]["parent"])
    if family in DIRECT_TEXT:
        x = DIRECT_TEXT[family]; containers.append(x["parent"]); texts.append(x["text"])
    if family == "codexquest_archive":
        containers.append("codexquest_container"); texts.append("codexquest_text_candidate")
    if family == "quest_talk_pilot":
        containers.append(QUEST_TALK["parent"]); texts.append(QUEST_TALK["text"])
    for x in RELATIONS.get(family, ()):
        relations.append(x["table"]); candidates.append(x["candidate"])
    return {"containers": containers, "texts": texts, "relations": relations, "candidates": candidates}
