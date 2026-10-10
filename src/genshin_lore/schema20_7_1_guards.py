"""Candidate-only Schema20 guard revision; historical migrations stay frozen.

Protect the independent owner/run/locale identity from REPLACE with recursive
triggers disabled. Distinct identities remain additive.
"""
from __future__ import annotations

import hashlib
import json

from .db import Database, _SCHEMA_STRUCTURAL_SIGNATURES

CONTRACT = "schema20-7.1-identity-insert-guards-v2"
FAMILIES = (
    "achievement_archive", "activity_archive", "avatar_config_archive",
    "dungeon_archive", "material_config_archive", "monster_archive",
    "npc_archive", "reliquary_config_archive", "scene_archive", "weapon_config_archive",
)


def definitions(conn=None):
    result = {
        f"trg_{family}_candidate_resolution_unique_guard": f"""CREATE TRIGGER
        trg_{family}_candidate_resolution_unique_guard
        BEFORE INSERT ON {family}_text_resolution_occurrence
        WHEN EXISTS (SELECT 1 FROM {family}_text_resolution_occurrence
          WHERE owner_id=NEW.owner_id AND ingest_run_id=NEW.ingest_run_id
            AND locale=NEW.locale)
        BEGIN SELECT RAISE(ABORT, 'immutable resolution owner/run/locale'); END"""
        for family in FAMILIES
    }
    if conn is not None:
        # These two newly populated historical namespaces lack INSERT gates.
        # Guard their actual declared identities, not speculative semantic keys.
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE 'world_archive_%_occurrence' OR name LIKE 'quest_codex_%_occurrence') ORDER BY name"):
            for index in conn.execute(f'PRAGMA index_list("{table}")').fetchall():
                if not index[2]:
                    continue
                columns = [r[2] for r in conn.execute(f'PRAGMA index_info("{index[1]}")')]
                name = 'trg_candidate_' + table + '_' + '_'.join(columns)
                predicate = ' AND '.join(f'"{c}" IS NEW."{c}"' for c in columns)
                result[name] = f'CREATE TRIGGER "{name}" BEFORE INSERT ON "{table}" WHEN EXISTS (SELECT 1 FROM "{table}" WHERE {predicate}) BEGIN SELECT RAISE(ABORT, \'immutable declared identity\'); END'
    return result


def install(conn):
    for ddl in definitions(conn).values():
        conn.execute(ddl)


def verify(conn):
    """Validate exact overlay AND the complete unchanged Schema20 substrate."""
    state = Database._stage_structural_snapshot(conn)
    overlay = definitions(conn)
    for name, ddl in overlay.items():
        key = "trigger:" + name
        if state["sql_semantics"].get(key) != list(Database._normalized_sql_semantics(ddl)):
            raise ValueError("candidate guard definition drift: " + name)
        state["objects"].remove(("trigger", name))
        del state["sql_semantics"][key]
    raw = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(raw).hexdigest() != _SCHEMA_STRUCTURAL_SIGNATURES[20][state["fts"]["mode"]]:
        raise ValueError("candidate Schema20 substrate drift")
    return {"contract": CONTRACT, "additional_triggers": len(overlay),
            "schema_version": 20, "historical_migrations_changed": False}
