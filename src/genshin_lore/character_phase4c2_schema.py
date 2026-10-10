from __future__ import annotations

import hashlib
import sqlite3
from typing import Any


class CharacterPhase4C2SchemaError(RuntimeError):
    """Schema 10 is not the exact reviewed Phase 4C2 Character migration."""


CHARACTER_TABLE_SQL_SHA256 = {
    "character_audio_variant_occurrence": "77f4352f4846ac1ae67157f2001bd2eb0d93a0d76f183a7fe0fdd273027466c8",
    "character_raw_slot_occurrence": "8a007548b54ee26900fbba4ff412082fc7e2de03f6d81d56b8fb4b66621af189",
    "character_relation_occurrence": "879d04f8c9218b36a0e58349d113ccc2e46376c7bd100147cffe024f178beb51",
    "character_row_occurrence": "c81f7c203c3a56cf477a26c04801659d19601d3b81d1e75cfb86e1792e4ce464",
    "character_text_candidate_occurrence": "9a5cb401bf7a39ae60233e948feca89da91273821a0343e5a9615193cbff5fb0",
    "character_text_resolution_occurrence": "11ece03285d6f926d3281b23a40477c1686751765e863265e4a2f5f7efdf407d",
    "character_voice_container_occurrence": "5b3fcfe6a47282d94b377931afdb28c02a749c1a8bfe369127deb1641cb1bdf1",
}
CHARACTER_INDEX_SQL_SHA256 = {
    "idx_character_audio_run_container": "68198fb89389ff41ada7fde8bb76d6039ad93e51e251d2ce06dee6541146052a",
    "idx_character_relation_run_kind": "d9117e6f00a941de379935291d45984efa27096d43c742ff5cc7265607444e8c",
    "idx_character_row_run_table": "3ac18b19eb453ac708511af84165f44bfbca2147b3d6febf339f198c1d880ef9",
    "idx_character_text_resolution_run_locale": "f384dd7701b21900f76da9cea452495a0f20d11050bcbe0ae15c6f8dc02fd5b1",
    "idx_character_voice_run_slot": "64267f910193a49529a1a81ce16db619ebb94c08d66bfaa36cbb92a729e35fd1",
    "uq_character_ingest_run_snapshot": "655263e9e521d232f401b80de151c0a487354334181ab5f1a137559b532df830",
    "uq_character_source_file_snapshot": "675e99ceea02c2830e86156c09f3cbf134c40e1548a0fcc95f19558030842d17",
    "uq_character_source_record_file_pointer": "1ece143c3ab3968b353e766480492a0581ac023abfd53ca0d9eed54ac8546081",
}
CHARACTER_TRIGGER_SQL_SHA256 = {
    "trg_character_audio_variant_occurrence_delete_immutable": "ae327412ca2d3ca764672621974e1ffb9813ee78e424c7840af682b9bba19f82",
    "trg_character_audio_variant_occurrence_immutable": "ba3cf6f4db0e7836ad94cad8648a38a26b9941c5b7d3a998a1c5a0b69d2544d4",
    "trg_character_raw_slot_occurrence_delete_immutable": "bd47a897c6a97b4e87f5f4b33dce29420897681a3378c92856d950b417af6c71",
    "trg_character_raw_slot_occurrence_immutable": "9330986e4a88480b4e17d49c5da17260fd3508411b77337d5b60cdee2fc1a187",
    "trg_character_relation_occurrence_delete_immutable": "8502e54a558e2ddce8e6dd247aff5375419b7b7a370f4c5cd5ea4c43f4202c79",
    "trg_character_relation_occurrence_immutable": "59b9593803cf211924b3b71f80d6e2bda68956f687fc40a91466720b254ded39",
    "trg_character_row_occurrence_delete_immutable": "90b6ed3194881906ee39fdf329192c1d445ebf326fc33060fffa077216142eb1",
    "trg_character_row_occurrence_immutable": "a4aa47734d46baf645e93e817b130d11decceca57e2c78fb60a866c97f28b8b3",
    "trg_character_text_candidate_occurrence_delete_immutable": "2e17df3c8be90317a32c9ff7a867787ed45c693315583d6c5d941b85810f7af1",
    "trg_character_text_candidate_occurrence_immutable": "d0914017a5b0d166cb6ff7a900e3ce02a0aa1155932f86c180890668a05f1ae7",
    "trg_character_text_resolution_occurrence_delete_immutable": "eb79005eb854338d020ff0428e0941f801299013680dec64ca64fb138c18ca84",
    "trg_character_text_resolution_occurrence_immutable": "ab768295dc0454d50df173a576f0391ceab6ac83ffc880c119ce513abcaf7c0d",
    "trg_character_voice_container_occurrence_delete_immutable": "b075e391f3d5c1ba456f488b20c6ed3a034c3662d1a427cf13fa05a779cb6427",
    "trg_character_voice_container_occurrence_immutable": "fb24ca1e9340df45a4d0ed2175fd52058e36ff1719a04532fd05c2d32d5db90a",
}

CHARACTER_TABLES = frozenset(CHARACTER_TABLE_SQL_SHA256)
CHARACTER_INDEXES = frozenset(CHARACTER_INDEX_SQL_SHA256)
CHARACTER_IMMUTABLE_TRIGGERS = frozenset(
    CHARACTER_TRIGGER_SQL_SHA256
)
BASE_PROVENANCE_INDEX_TABLES = {
    "uq_character_ingest_run_snapshot": "ingest_run",
    "uq_character_source_file_snapshot": "source_file",
    "uq_character_source_record_file_pointer": "source_record",
}
CHARACTER_ATTACHED_INDEXES = (
    CHARACTER_INDEXES - frozenset(BASE_PROVENANCE_INDEX_TABLES)
)


def _normalized_sql_sha256(sql: Any) -> str:
    if not isinstance(sql, str) or not sql.strip():
        raise CharacterPhase4C2SchemaError(
            "schema object has no SQL body"
        )
    normalized = " ".join(sql.split())
    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


def _verify_signatures(
    rows: dict[str, str],
    *,
    object_type: str,
    expected_names: frozenset[str],
    signatures: dict[str, str],
) -> None:
    if set(rows) != set(expected_names):
        raise CharacterPhase4C2SchemaError(
            f"known schema 10 {object_type} names mismatch: "
            f"{sorted(rows)}"
        )
    for name in sorted(expected_names):
        if _normalized_sql_sha256(rows[name]) != signatures[name]:
            raise CharacterPhase4C2SchemaError(
                "known schema 10 "
                f"{object_type} SQL signature mismatch: {name}"
            )


def _character_table_rows(
    conn: sqlite3.Connection,
) -> dict[str, str]:
    rows = {
        str(name): sql
        for name, sql in conn.execute(
            "SELECT name,sql FROM sqlite_master "
            "WHERE type='table' AND sql IS NOT NULL"
        )
        if str(name).casefold().startswith("character_")
    }
    return rows


def _attached_rows(
    conn: sqlite3.Connection,
    *,
    object_type: str,
) -> dict[str, str]:
    placeholders = ",".join("?" for _ in CHARACTER_TABLES)
    return {
        str(name): sql
        for name, sql in conn.execute(
            "SELECT name,sql FROM sqlite_master "
            "WHERE type=? AND sql IS NOT NULL "
            f"AND tbl_name IN ({placeholders})",
            (object_type, *sorted(CHARACTER_TABLES)),
        )
    }


def _verify_base_provenance_indexes(
    conn: sqlite3.Connection,
) -> None:
    for name, expected_table in sorted(
        BASE_PROVENANCE_INDEX_TABLES.items()
    ):
        row = conn.execute(
            "SELECT tbl_name,sql FROM sqlite_master "
            "WHERE type='index' AND name=? AND sql IS NOT NULL",
            (name,),
        ).fetchone()
        if (
            row is None
            or str(row[0]) != expected_table
            or _normalized_sql_sha256(row[1])
            != CHARACTER_INDEX_SQL_SHA256[name]
        ):
            raise CharacterPhase4C2SchemaError(
                "known schema 10 base provenance index "
                f"signature mismatch: {name}"
            )


def _reject_character_views(conn: sqlite3.Connection) -> None:
    table_names = tuple(
        table.casefold() for table in CHARACTER_TABLES
    )
    offending: list[str] = []
    for name, sql in conn.execute(
        "SELECT name,sql FROM sqlite_master "
        "WHERE type='view' AND sql IS NOT NULL"
    ):
        folded_name = str(name).casefold()
        folded_sql = str(sql).casefold()
        if (
            folded_name.startswith("character_")
            or any(table in folded_sql for table in table_names)
        ):
            offending.append(str(name))
    if offending:
        raise CharacterPhase4C2SchemaError(
            "known schema 10 has Character-related views: "
            f"{sorted(offending)}"
        )


def verify_character_phase4c2_schema_signature(
    conn: sqlite3.Connection,
) -> dict[str, int]:
    """Fail closed unless migration 0010 is the exact reviewed schema."""

    _verify_signatures(
        _character_table_rows(conn),
        object_type="table",
        expected_names=CHARACTER_TABLES,
        signatures=CHARACTER_TABLE_SQL_SHA256,
    )
    _verify_signatures(
        _attached_rows(conn, object_type="index"),
        object_type="Character-attached index",
        expected_names=CHARACTER_ATTACHED_INDEXES,
        signatures=CHARACTER_INDEX_SQL_SHA256,
    )
    _verify_base_provenance_indexes(conn)
    _verify_signatures(
        _attached_rows(conn, object_type="trigger"),
        object_type="Character-attached trigger",
        expected_names=CHARACTER_IMMUTABLE_TRIGGERS,
        signatures=CHARACTER_TRIGGER_SQL_SHA256,
    )
    _reject_character_views(conn)
    return {
        "tables": len(CHARACTER_TABLES),
        "indexes": len(CHARACTER_INDEXES),
        "immutable_triggers": len(
            CHARACTER_IMMUTABLE_TRIGGERS
        ),
    }


__all__ = [
    "CHARACTER_IMMUTABLE_TRIGGERS",
    "CHARACTER_INDEXES",
    "CHARACTER_TABLES",
    "CharacterPhase4C2SchemaError",
    "verify_character_phase4c2_schema_signature",
]
