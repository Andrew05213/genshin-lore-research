"""Tracked runtime seals for the accepted Phase 4F3 QuestCodex release.

The external receipt is only an exact-file prefilter.  These seals are the
repository-owned authentication root recomputed from the opened database.
All occurrence scans are restricted to the exact F3 run, so an accepted F2
canary may coexist without entering any F3 digest.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any, Iterable

from .util import canonical_json, digest_id


OCCURRENCE_TABLES = (
    "quest_codex_row_occurrence",
    "quest_codex_raw_field_occurrence",
    "quest_codex_relationship_occurrence",
    "quest_codex_relationship_candidate_occurrence",
    "quest_codex_opaque_occurrence",
    "quest_codex_text_owner_occurrence",
    "quest_codex_text_resolution_occurrence",
    "quest_codex_text_candidate_occurrence",
    "quest_codex_asset_reference_occurrence",
)

# Generated from the independently verified Phase 4F3 schema12-to-full release.
EXPECTED_RUNTIME_SEALS = {
    "ingest_run": {"rows": 1, "sha256": "22ef159f1ffdf4a2df82232225f120467c8f31a5d5aa737826b82ce17dec160c"},
    "parser_build": {"rows": 1, "sha256": "338cfab7640929b7c44d3b78f3d210359f2833245d416486e4b463518d7c08e9"},
    "quest_codex_asset_reference_occurrence": {"rows": 420, "sha256": "b1e6bd83cd9df00166c801000ef530f51f8aeedb79c8af8ff419be60821e4152"},
    "quest_codex_opaque_occurrence": {"rows": 1199, "sha256": "b330177b1ae417022e1706ad33314e065b0eee3964718abf2b1d24622e42f9cb"},
    "quest_codex_raw_field_occurrence": {"rows": 5177, "sha256": "6bda75ea45a926456c5c9444da318bbf7f14cdd5fc41aeb2ec20ed9897c77bfd"},
    "quest_codex_relationship_candidate_occurrence": {"rows": 4003, "sha256": "00b99b49bb103a2683924ce6a34a0a7cadbee8d9a7f9251607d673450c7691c9"},
    "quest_codex_relationship_occurrence": {"rows": 986, "sha256": "af25d20dd54e2ae962972a1d8c17c493ede22037d3b79dbbef79d8e3faca1fdd"},
    "quest_codex_row_occurrence": {"rows": 633, "sha256": "ac64bb9aaa8196405b4fa008b15731e48bb85dfbf50040105cc29cd858b9257f"},
    "quest_codex_text_candidate_occurrence": {"rows": 1206, "sha256": "7c0cec5ca64976913b1c0c20d725a89a2fd8af0b38a44e7300572960beae01a5"},
    "quest_codex_text_owner_occurrence": {"rows": 420, "sha256": "879aa38510086003b51f59789107e43e88fec2be9af2ea65510d7171fb77f18b"},
    "quest_codex_text_resolution_occurrence": {"rows": 1260, "sha256": "5377f92d1f66d45b16414030d5e01114f9e4b599a9cbfd968b6dc98e40f32064"},
    "source_catalog": {"rows": 1, "sha256": "51a56b174cddb57f985874b1cab35013b59313f0e227e8c92fab8c0e08b95bcc"},
    "source_file": {"rows": 9, "sha256": "f8984e8720a7dcb29189498a2b0c9df22533db2177a5a6dfb9c6562445ea6663"},
    "source_record": {"rows": 5349, "sha256": "d42914764f51f937282e1349f71cf7665b54116952169091ab28e30e26fd296e"},
    "source_snapshot": {"rows": 1, "sha256": "7d0fd23e2a9131fca786b61b7cbc440ae4079269e8efbd18f377b9a290c1353a"},
}


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    columns = [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]
    if table == "source_file":
        columns.remove("root_path")  # machine-local location is not evidence
    return columns


def _seal(columns: list[str], rows: Iterable[sqlite3.Row]) -> dict[str, Any]:
    material = [list(row) for row in rows]
    payload = canonical_json({"columns": columns, "rows": material})
    return {"rows": len(material), "sha256": hashlib.sha256(payload.encode()).hexdigest()}


def _scan_ids(conn: sqlite3.Connection, table: str, ids: list[str]) -> dict[str, Any]:
    columns = _columns(conn, table)
    quoted = ",".join(f'"{column}"' for column in columns)
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT {quoted} FROM {table} WHERE id IN ({placeholders}) ORDER BY id", ids
    ).fetchall()
    return _seal(columns, rows)


def scan_runtime_seals(
    conn: sqlite3.Connection, *, ingest_run_id: str, snapshot_id: str,
    parser_build_id: str, source_id: str, manifest_files: list[dict[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for table in OCCURRENCE_TABLES:
        columns = _columns(conn, table)
        quoted = ",".join(f'"{column}"' for column in columns)
        rows = conn.execute(
            f"SELECT {quoted} FROM {table} WHERE ingest_run_id=? ORDER BY id",
            (ingest_run_id,),
        ).fetchall()
        result[table] = _seal(columns, rows)

    file_ids = sorted(
        digest_id("sf", snapshot_id, str(item["relative_path"]))
        for item in manifest_files
    )
    record_ids = sorted({
        str(row[0])
        for table, column in (
            ("quest_codex_row_occurrence", "source_record_id"),
            ("quest_codex_relationship_candidate_occurrence", "source_record_id"),
            ("quest_codex_text_candidate_occurrence", "source_record_id"),
        )
        for row in conn.execute(
            f"SELECT {column} FROM {table} WHERE ingest_run_id=?", (ingest_run_id,)
        )
    })
    result["source_file"] = _scan_ids(conn, "source_file", file_ids)
    result["source_record"] = _scan_ids(conn, "source_record", record_ids)
    for table, identifier in (
        ("source_catalog", source_id),
        ("source_snapshot", snapshot_id),
        ("parser_build", parser_build_id),
        ("ingest_run", ingest_run_id),
    ):
        result[table] = _scan_ids(conn, table, [identifier])
    return result


__all__ = ["EXPECTED_RUNTIME_SEALS", "OCCURRENCE_TABLES", "scan_runtime_seals"]
