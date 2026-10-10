"""Family-specific replay/comparison shard adapters for Batch M2.

These adapters do not re-project or merge existing evidence.  They read the
already accepted family tables and emit a compact, family-named comparison
digest table.  This lets the M2 merge exercise real family boundaries without
copying the 1.7 GiB accepted database once per family.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from .family_projection_protocol import FamilyShardSpec, TableSpec, canonical_row, rowset_digest, schema_digest
from .util import digest_id


SOURCE_COMMIT = "26df1dfbdf05a82bbb1d97506859f3e1c40718d8"
SNAPSHOT_ID = f"anime-game-data:7.0:{SOURCE_COMMIT}"
PARSER_BUILD_ID = f"pb-family-replay-batch-m2:{SOURCE_COMMIT}"
ACCEPTED_SCHEMA13_BASELINE_SHA256 = "7ff8163b6007a41b0827af05fbcf996353617c52f354a131431da912712e58be"
ACCEPTED_SCHEMA13_BASELINE_SIZE = 1723596800
ACCEPTED_SCHEMA13_LEDGER_VERSIONS = tuple(range(1, 14))


class ReplayInputUnavailable(RuntimeError):
    """The local accepted DB needed for a comparison shard is unavailable."""


class ReplayContractError(RuntimeError):
    """A family replay input does not match its existing contract."""


FAMILY_TABLES: Mapping[str, tuple[str, ...]] = {
    "world_archive": (
        "world_archive_row_occurrence",
        "world_archive_raw_field_occurrence",
        "world_archive_relationship_occurrence",
        "world_archive_opaque_occurrence",
        "world_archive_text_owner_occurrence",
        "world_archive_text_resolution_occurrence",
        "world_archive_text_candidate_occurrence",
        "world_archive_asset_reference_occurrence",
    ),
    "quest_codex_archive": (
        "quest_codex_row_occurrence",
        "quest_codex_raw_field_occurrence",
        "quest_codex_relationship_occurrence",
        "quest_codex_relationship_candidate_occurrence",
        "quest_codex_opaque_occurrence",
        "quest_codex_text_owner_occurrence",
        "quest_codex_text_resolution_occurrence",
        "quest_codex_text_candidate_occurrence",
        "quest_codex_asset_reference_occurrence",
    ),
    "core_tutorial": (
        "tutorial_row_occurrence",
        "tutorial_raw_field_occurrence",
        "tutorial_detail_relation_occurrence",
        "tutorial_push_tips_candidate_occurrence",
        "tutorial_image_reference_occurrence",
        "tutorial_text_owner_occurrence",
        "tutorial_text_resolution_occurrence",
        "tutorial_text_candidate_occurrence",
    ),
}

FAMILY_CONTRACTS: Mapping[str, str] = {
    "world_archive": "world-archive-evidence-v1",
    "quest_codex_archive": "quest-codex-archive-evidence-v1",
    "core_tutorial": "core-tutorial-evidence-v1",
}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_accepted_schema13_baseline(path: Path) -> tuple[int, str]:
    """Require the one accepted Schema 13 replay source by bytes and ledger."""

    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ReplayInputUnavailable(f"accepted Schema 13 baseline is unavailable: {path}") from exc
    if size != ACCEPTED_SCHEMA13_BASELINE_SIZE:
        raise ReplayContractError(
            f"replay source size is not accepted Schema 13: {size} != {ACCEPTED_SCHEMA13_BASELINE_SIZE}"
        )
    digest = _file_sha256(path)
    if digest != ACCEPTED_SCHEMA13_BASELINE_SHA256:
        raise ReplayContractError("replay source SHA is not the accepted Schema 13 baseline")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        migration_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='schema_migration'"
        ).fetchone()
        if migration_sql is None or migration_sql[0] is None:
            raise ReplayContractError("replay source has no schema_migration ledger")
        versions = tuple(
            row[0] for row in connection.execute(
                "SELECT version FROM schema_migration ORDER BY version"
            )
        )
        if versions != ACCEPTED_SCHEMA13_LEDGER_VERSIONS:
            raise ReplayContractError(
                f"replay source migration ledger mismatch: {versions!r}"
            )
    except sqlite3.Error as exc:
        raise ReplayContractError(f"cannot inspect accepted Schema 13 ledger: {exc}") from exc
    finally:
        connection.close()
    return size, digest


def validate_accepted_schema13_baseline(path: str | Path) -> tuple[int, str]:
    """Validate and return the exact accepted Schema 13 baseline identity.

    Callers must provide the exact file to bind; this helper never chooses a
    default/latest/sibling database.
    """

    return _validate_accepted_schema13_baseline(Path(path).absolute())


def _table_name(family: str) -> str:
    return f"{family.replace('-', '_')}_replay_table_digest"


def _create_sql(table_name: str) -> str:
    return f"""CREATE TABLE {table_name} (
        id TEXT PRIMARY KEY NOT NULL,
        family TEXT NOT NULL,
        family_contract_version TEXT NOT NULL,
        source_table TEXT NOT NULL,
        source_schema_sha256 TEXT NOT NULL,
        source_column_signature_json TEXT NOT NULL CHECK (json_valid(source_column_signature_json)),
        source_row_count INTEGER NOT NULL CHECK (source_row_count >= 0),
        source_rowset_sha256 TEXT NOT NULL,
        source_database_size_bytes INTEGER NOT NULL CHECK (source_database_size_bytes > 0),
        source_database_sha256 TEXT NOT NULL,
        metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json))
    )"""


def load_replay_spec(family: str, source_db: str | Path) -> FamilyShardSpec:
    """Read one existing family contract's tables into a compact replay shard."""

    if family not in FAMILY_TABLES:
        raise ReplayContractError(f"unsupported M2 replay family: {family}")
    path = Path(source_db).absolute()
    if not path.is_file() or path.is_symlink():
        raise ReplayInputUnavailable(f"accepted family database is unavailable: {path}")
    table_name = _table_name(family)
    contract = FAMILY_CONTRACTS[family]
    run_id = f"ir-{family}-batch-m2-replay:{SOURCE_COMMIT}"
    db_size, db_sha = _validate_accepted_schema13_baseline(path)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows: list[tuple[Any, ...]] = []
    selected_tables: list[str] = []
    try:
        for source_table in FAMILY_TABLES[family]:
            found = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (source_table,)
            ).fetchone()
            if found is None or not found[0]:
                raise ReplayContractError(f"{family} accepted table is missing: {source_table}")
            columns = tuple(row[1] for row in connection.execute(f'PRAGMA table_info("{source_table}")'))
            if not columns:
                raise ReplayContractError(f"{family} accepted table has no columns: {source_table}")
            values = [tuple(row) for row in connection.execute(f'SELECT * FROM "{source_table}"')]
            ordered = tuple(sorted(values, key=lambda row: canonical_row(row)))
            digest = rowset_digest(ordered)
            schema_sha = schema_digest(found[0])
            row_id = digest_id("m2-replay-table", family, run_id, source_table, digest)
            rows.append((
                row_id, family, contract, source_table, schema_sha,
                json.dumps(list(columns), ensure_ascii=False, separators=(",", ":")),
                len(ordered), digest, db_size, db_sha,
                json.dumps({
                    "comparison_only": True,
                    "source_database_identity": f"sha256:{db_sha}:size:{db_size}",
                    "source_rows_are_existing_family_evidence": True,
                    "logical_identity_created": False,
                }, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            ))
            selected_tables.append(source_table)
    except sqlite3.Error as exc:
        raise ReplayContractError(f"cannot read {family} accepted tables: {exc}") from exc
    finally:
        connection.close()
    return FamilyShardSpec(
        family=family,
        family_contract_version=contract,
        ingest_run_id=run_id,
        snapshot_id=SNAPSHOT_ID,
        parser_build_id=PARSER_BUILD_ID,
        coverage_level="R1_family_replay_comparison",
        corpus_complete=False,
        product_accepted=False,
        read_set=(
            f"accepted_schema13_baseline:sha256:{ACCEPTED_SCHEMA13_BASELINE_SHA256}",
            *selected_tables,
        ),
        write_set=(table_name,),
        tables=(
            TableSpec(
                name=table_name,
                create_sql=_create_sql(table_name),
                columns=(
                    "id", "family", "family_contract_version", "source_table",
                    "source_schema_sha256", "source_column_signature_json",
                    "source_row_count", "source_rowset_sha256", "source_database_size_bytes",
                    "source_database_sha256", "metadata_json",
                ),
                rows=tuple(rows),
                order_columns=("source_table", "id"),
                sort_key=lambda row: (str(row[3]), str(row[0])),
                index_sql=(f"CREATE INDEX idx_m2_{family.replace('-', '_')}_replay_table ON {table_name} (source_table)",),
            ),
        ),
        metadata={
            "projection_kind": "family_specific_replay_comparison_digest",
            "source_database": str(path),
            "source_database_sha256": db_sha,
            "source_database_size_bytes": db_size,
            "selected_family_tables": selected_tables,
            "family_table_count": len(selected_tables),
            "textmap_projection": "not_materialized_in_replay_digest",
            "provider_or_registry_changed": False,
        },
    )


__all__ = [
    "FAMILY_CONTRACTS",
    "FAMILY_TABLES",
    "PARSER_BUILD_ID",
    "ReplayContractError",
    "ReplayInputUnavailable",
    "SNAPSHOT_ID",
    "load_replay_spec",
    "validate_accepted_schema13_baseline",
]
