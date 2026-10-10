"""Exact Schema 11 contract for the Phase 4D2A Tutorial canary.

The verifier deliberately knows only the SQL shape of the additive Tutorial
family.  It does not inspect occurrence rows and is safe to call from a
read-only connection.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any


class TutorialPhase4D2ASchemaError(RuntimeError):
    """Schema 11 is not the exact reviewed Tutorial canary migration."""


TUTORIAL_TABLE_SQL_SHA256 = {
    "tutorial_detail_relation_occurrence": "9190011df87b068d84128822f61308fdc725f3f5836316fdd3d1c74fee66c569",
    "tutorial_image_reference_occurrence": "4ec822b6511362fcec07e3d95bdfaf666ba24aa63a5c553b0fb7096db0f0a5cf",
    "tutorial_push_tips_candidate_occurrence": "5d7397bd1853e10dffed8673acc29784344a84bdca8073766c927e803ca11abc",
    "tutorial_raw_field_occurrence": "c0765c5a89400425aa683573f26fa9d55dd335986a82c94b8978305d77c3e3be",
    "tutorial_row_occurrence": "03046a455174b337541385e7c43ae478162303f1990dfb313bbcc9e48259dcac",
    "tutorial_text_candidate_occurrence": "0b50bb0a99e2ae1a47ede38b6feb5e823ec2badf2b14043bf238d887f7dcf40d",
    "tutorial_text_owner_occurrence": "fc59bafea2b2e99b8396cdc351a29480a3dfa97ae727499b3e1843d5b17df553",
    "tutorial_text_resolution_occurrence": "172f4eb034d6cb04ba5780179774048f048285759dd2efebb5a7d4290b8786c0",
}
TUTORIAL_INDEX_SQL_SHA256 = {
    "uq_tutorial_ingest_run_scope": "63926b46b5478d4ed5aabba5b3983db104a1f038c746e4e424b712d73b0eaabf",
    "uq_tutorial_source_file_snapshot": "d388a1dcb737fea6952359903bc9e11a5bd16605f007318d1af5f3e395a1b201",
    "uq_tutorial_source_record_file_pointer": "938253f6cc05c185dc1f0d0cd8d620ae641294612782b968fe09d39cdcb602cf",
    "idx_tutorial_detail_relation_run_status": "d3a9afed0ec4ec2d9032f96133995a5ea6296b1fbfd102f303dc8e02fb26d1fa",
    "idx_tutorial_image_reference_run_name": "9b4e0fadb3c1a374f40e0e301c5eebc07d26a0a8a0b3c67730fe918e828c8738",
    "idx_tutorial_push_tips_run_status": "1788f4e6218c9db604d73740241ab95733de3915eb51522204152d2fe9b328e9",
    "idx_tutorial_raw_field_run_row": "2802ee13236c5e8a70d3b89d6eaa6363098e8969b9cf0db70907cc2464f18dfe",
    "idx_tutorial_row_run_table": "99355d9639dee805d2457a81f0f310e5b63fed2fb98d807554738cf525756bda",
    "idx_tutorial_text_candidate_run_locale": "32f7877b2a240405783f4927013e8ded00dcbe73b49618a768f5a5f420c0e386",
    "idx_tutorial_text_owner_run_field": "ab2da31ea7c5315fe91d11e7d93d3c8ff1cb5ba2615646d795d5191dc61aa554",
    "idx_tutorial_text_resolution_run_locale": "9cdadb0be07471afbdbd4041518ae989b0c5c6a104ab3daa753b256c1d233308",
}

TUTORIAL_TRIGGER_SQL_SHA256 = {
    "trg_tutorial_detail_relation_occurrence_delete_immutable": "3be343fdbae24291dd589016f091fd1b8a4e10c085dd6f4e54cc0f14b0d61501",
    "trg_tutorial_detail_relation_occurrence_immutable": "1440343828935101086beaac685b5770926143064e6fda9d6632f5a2ea6a1eb6",
    "trg_tutorial_image_reference_occurrence_delete_immutable": "d116ec7ef24bd99c24de24e4def2d26ee2b989d71571dfe67f9456fe3fee9be4",
    "trg_tutorial_image_reference_occurrence_immutable": "3d493065c74e9ebbfb80ffc6d49ef9ec623f241d323c644ae826ceb2e14d20e7",
    "trg_tutorial_push_tips_candidate_occurrence_delete_immutable": "9bd644a53ba1501c53b5b1a245686344e17a4ffbc6fa721f1abf8787805a04aa",
    "trg_tutorial_push_tips_candidate_occurrence_immutable": "ed3aea3935fcc85f10aaa7a87d316549fff84ccee472804db39d4f6edbdc9541",
    "trg_tutorial_raw_field_occurrence_delete_immutable": "e4e7d5b3b4c2c7cd3819b3b525a03996c32a67182c1804e931b8c6f20b9ceb55",
    "trg_tutorial_raw_field_occurrence_immutable": "937ab9ecc4d1816b74291306e5e74dfe00c319ecf3e80c0f6f98c63513d4dbe3",
    "trg_tutorial_row_occurrence_delete_immutable": "bf093056501435f93b04ebb40fc022ab9188e8980ec9d7ee97275b9778235654",
    "trg_tutorial_row_occurrence_immutable": "36e0e560f9851e55bcf7f796373992fff37448895aec039df873e6991216db10",
    "trg_tutorial_text_candidate_occurrence_delete_immutable": "1f0bf0672301db5897718db4615750b2bb791ad6eee8a84e8e72e1fe27adb93d",
    "trg_tutorial_text_candidate_occurrence_immutable": "7c80abca1e5adcc2dedc8d497e0657a7ad7d6059f7c8125f13a845c104f0f4a0",
    "trg_tutorial_text_owner_occurrence_delete_immutable": "edde1dcffce3db57a721b79fdd7f7f1fb1d25f4d69c722d67a6b24a11c718381",
    "trg_tutorial_text_owner_occurrence_immutable": "7a481d66901f3f353d181bde176b49b5a7d74f86ba1b6a602c6867e15ca53f1d",
    "trg_tutorial_text_resolution_occurrence_delete_immutable": "cd29ee1d8e502c71b01642400da21c598b258cbbefb07adb75a09c935a987f75",
    "trg_tutorial_text_resolution_occurrence_immutable": "e42b8a62307e737c2dd9b9e297f7b64fbf3d1e5206880a81169957e208bfc9e3",
}

TUTORIAL_TABLES = frozenset(TUTORIAL_TABLE_SQL_SHA256)
TUTORIAL_INDEXES = frozenset(TUTORIAL_INDEX_SQL_SHA256)
TUTORIAL_IMMUTABLE_TRIGGERS = frozenset(TUTORIAL_TRIGGER_SQL_SHA256)

# Full structural seals include the pre-0011 Schema 10 objects and the
# additive Tutorial objects.  This prevents a rogue migration/object from
# passing merely because the eight Tutorial DDL bodies still look correct.
TUTORIAL_SCHEMA11_STRUCTURAL_SIGNATURES = {
    "fts5": "8a7fa67685e01489067765ed294b231b49b8535f2cd6e76261bfa32915cc4ddd",
    "fallback": "16a3fd7e4d984247f4ff4507d24f05922a4ff6c4b9e08f6d6cd8505d3786fe17",
}
TUTORIAL_MIGRATION_0011_SQL_SHA256 = "d852e4646c29d9844d43de1f7e069a9fb4df449182343925acd8147cc7395a91"


def _normalized_sql_sha256(sql: Any) -> str:
    if not isinstance(sql, str) or not sql.strip():
        raise TutorialPhase4D2ASchemaError("schema object has no SQL body")
    return hashlib.sha256(" ".join(sql.split()).encode("utf-8")).hexdigest()


def _objects(conn: sqlite3.Connection, object_type: str, names: frozenset[str]) -> dict[str, str]:
    placeholders = ",".join("?" for _ in names)
    return {
        str(name): str(sql)
        for name, sql in conn.execute(
            f"SELECT name,sql FROM sqlite_master WHERE type=? AND sql IS NOT NULL AND name IN ({placeholders})",
            (object_type, *sorted(names)),
        )
    }


def _verify(rows: dict[str, str], expected: frozenset[str], signatures: dict[str, str], label: str) -> None:
    if set(rows) != set(expected):
        raise TutorialPhase4D2ASchemaError(f"known Schema 11 {label} names mismatch: {sorted(rows)}")
    for name in sorted(expected):
        if _normalized_sql_sha256(rows[name]) != signatures[name]:
            raise TutorialPhase4D2ASchemaError(f"known Schema 11 {label} SQL signature mismatch: {name}")


def _verify_tutorial_attached_objects(conn: sqlite3.Connection) -> None:
    """Reject attached indexes/triggers whose names are not in the seal."""
    for table in sorted(TUTORIAL_TABLES):
        quoted = '"' + table.replace('"', '""') + '"'
        for row in conn.execute(f"PRAGMA index_list({quoted})"):
            name = str(row[1])
            # SQLite creates these for PRIMARY KEY/UNIQUE constraints; they
            # are structural consequences of the sealed table definition.
            if name.startswith("sqlite_autoindex"):
                continue
            if name not in TUTORIAL_INDEXES:
                raise TutorialPhase4D2ASchemaError(
                    f"unknown index attached to Tutorial table {table}: {name}"
                )

    # sqlite_master has no trigger-to-table column.  SQL semantic tokens make
    # the target independent of quoting and reject a rogue trigger regardless
    # of its object name.  Include the connection-local temp catalog because a
    # verifier may be handed an already-open connection.
    from .db import Database
    for catalog in ("sqlite_master", "sqlite_temp_master"):
        for name, sql in conn.execute(
            f"SELECT name,sql FROM {catalog} WHERE type='trigger' AND sql IS NOT NULL"
        ):
            if catalog == "sqlite_master" and str(name) in TUTORIAL_IMMUTABLE_TRIGGERS:
                continue
            try:
                tokens = Database._normalized_sql_semantics(str(sql))
            except Exception as exc:
                raise TutorialPhase4D2ASchemaError(
                    f"cannot inspect trigger SQL: {name}"
                ) from exc

            def targets_tutorial_table(index: int, token: str) -> bool:
                if token != "on" or index + 1 >= len(tokens):
                    return False
                if tokens[index + 1] in TUTORIAL_TABLES:
                    return True
                # SQLite permits an explicit ``main.<table>`` qualifier in
                # the trigger target; normalize that form as attached too.
                return (
                    index + 3 < len(tokens)
                    and tokens[index + 1] == "main"
                    and tokens[index + 2] == "."
                    and tokens[index + 3] in TUTORIAL_TABLES
                )

            if any(targets_tutorial_table(index, token) for index, token in enumerate(tokens)):
                raise TutorialPhase4D2ASchemaError(
                    f"unknown trigger attached to Tutorial table: {name}"
                )


def _verify_full_schema11_structure(conn: sqlite3.Connection) -> None:
    """Seal every Schema 11 object, including all pre-0011 Schema 10 objects."""
    from .db import Database
    try:
        snapshot = Database._stage_structural_snapshot(conn)
        mode = str(snapshot["fts"]["mode"])
        expected = TUTORIAL_SCHEMA11_STRUCTURAL_SIGNATURES.get(mode)
        actual = Database._stage_structural_signature(conn)
    except Exception as exc:
        raise TutorialPhase4D2ASchemaError(
            f"cannot calculate full Schema 11 structural seal: {exc}"
        ) from exc
    if expected is None or actual != expected:
        raise TutorialPhase4D2ASchemaError(
            f"full Schema 11 structural seal mismatch: expected {expected}, got {actual}"
        )


def verify_tutorial_phase4d2a_schema_signature(
    conn: sqlite3.Connection,
    *,
    full_schema: bool = True,
) -> dict[str, int]:
    """Fail closed unless Tutorial's family objects are exact.

    ``full_schema`` remains true for the historical Schema 11 release gate.
    Additive Schema 17 runtime qualification uses the M5D full structural
    seal separately and asks only for this family-local object contract.
    """
    _verify(_objects(conn, "table", TUTORIAL_TABLES), TUTORIAL_TABLES, TUTORIAL_TABLE_SQL_SHA256, "table")
    _verify(_objects(conn, "index", TUTORIAL_INDEXES), TUTORIAL_INDEXES, TUTORIAL_INDEX_SQL_SHA256, "index")
    _verify(_objects(conn, "trigger", TUTORIAL_IMMUTABLE_TRIGGERS), TUTORIAL_IMMUTABLE_TRIGGERS, TUTORIAL_TRIGGER_SQL_SHA256, "trigger")
    _verify_tutorial_attached_objects(conn)
    offending: list[str] = []
    for name, sql in conn.execute("SELECT name,sql FROM sqlite_master WHERE type='view' AND sql IS NOT NULL"):
        folded = str(sql).casefold()
        if str(name).casefold().startswith("tutorial_") or any(table in folded for table in TUTORIAL_TABLES):
            offending.append(str(name))
    if offending:
        raise TutorialPhase4D2ASchemaError(f"known Schema 11 has Tutorial-related views: {sorted(offending)}")
    if full_schema:
        _verify_full_schema11_structure(conn)
    return {"tables": len(TUTORIAL_TABLES), "indexes": len(TUTORIAL_INDEXES), "immutable_triggers": len(TUTORIAL_IMMUTABLE_TRIGGERS)}


def exact_schema_11(conn: sqlite3.Connection) -> bool:
    """Return whether ``conn`` is the exact reviewed Schema 11 prefix.

    Runtime capability inspection needs one shared, read-only predicate for
    the additive M5 database.  The migration ledger, complete structural
    signature, Character Schema 10 seal, and Tutorial Schema 11 seal are all
    checked here.  None of these checks reads an occurrence table or invokes a
    release verifier, so this helper is safe for the MCP startup fast path.
    """

    try:
        versions = [
            int(row[0])
            for row in conn.execute(
                "SELECT version FROM schema_migration ORDER BY version"
            )
        ]
        if versions != list(range(1, 12)):
            return False

        # Import these helpers lazily to keep this schema module out of the
        # normal database initialization dependency graph.
        from .character_phase4c2_schema import verify_character_phase4c2_schema_signature
        from .db import validate_exact_schema_structure

        validate_exact_schema_structure(conn, 11)
        verify_character_phase4c2_schema_signature(conn)
        verify_tutorial_phase4d2a_schema_signature(conn)
        return True
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        sqlite3.Error,
        RuntimeError,
    ):
        return False


__all__ = [
    "TUTORIAL_IMMUTABLE_TRIGGERS",
    "TUTORIAL_INDEXES",
    "TUTORIAL_MIGRATION_0011_SQL_SHA256",
    "TUTORIAL_SCHEMA11_STRUCTURAL_SIGNATURES",
    "TUTORIAL_TABLES",
    "TutorialPhase4D2ASchemaError",
    "exact_schema_11",
    "verify_tutorial_phase4d2a_schema_signature",
]
