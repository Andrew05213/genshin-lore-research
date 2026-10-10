"""Independent SQL rowset digests for the sealed World Archive release."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from pathlib import Path
from typing import Any, Iterable, Sequence

from .util import canonical_json


RUNTIME_SEAL_ALGORITHM = "canonical-sql-rowset-sha256-v1"
OCCURRENCE_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "world_archive_row_occurrence": (
        "id", "ingest_run_id", "snapshot_id", "parser_build_id",
        "source_file_id", "source_record_id", "source_pointer", "table_name",
        "row_index", "native_locator_field", "native_locator_value",
        "raw_checksum", "metadata_json",
    ),
    "world_archive_raw_field_occurrence": (
        "id", "ingest_run_id", "snapshot_id", "parser_build_id",
        "row_occurrence_id", "source_record_id", "source_pointer", "field_name",
        "field_kind", "raw_json", "metadata_json",
    ),
    "world_archive_relationship_occurrence": (
        "id", "ingest_run_id", "snapshot_id", "parser_build_id",
        "source_row_occurrence_id", "source_record_id", "source_pointer",
        "field_name", "relation_kind", "raw_value_json", "candidate_key_json",
        "resolution_status", "target_table", "target_field",
        "candidate_scopes_json", "metadata_json",
    ),
    "world_archive_opaque_occurrence": (
        "id", "ingest_run_id", "snapshot_id", "parser_build_id",
        "source_row_occurrence_id", "source_record_id", "source_pointer",
        "field_name", "array_index", "raw_value_json", "resolution_status",
        "reason", "metadata_json",
    ),
    "world_archive_text_owner_occurrence": (
        "id", "ingest_run_id", "snapshot_id", "parser_build_id",
        "source_row_occurrence_id", "source_record_id", "source_pointer",
        "field_name", "field_role", "textmap_hash", "raw_value_json",
        "metadata_json",
    ),
    "world_archive_text_resolution_occurrence": (
        "id", "ingest_run_id", "snapshot_id", "parser_build_id",
        "owner_occurrence_id", "source_row_occurrence_id", "source_record_id",
        "source_pointer", "locale", "textmap_hash", "resolution_status",
        "raw_text", "normalized_text", "raw_checksum", "normalized_checksum",
        "candidate_scopes_json", "metadata_json",
    ),
    "world_archive_text_candidate_occurrence": (
        "id", "ingest_run_id", "snapshot_id", "parser_build_id",
        "resolution_occurrence_id", "source_file_id", "source_record_id",
        "source_pointer", "shard_kind", "locale", "raw_text", "normalized_text",
        "raw_checksum", "normalized_checksum", "metadata_json",
    ),
    "world_archive_asset_reference_occurrence": (
        "id", "ingest_run_id", "snapshot_id", "parser_build_id",
        "source_row_occurrence_id", "source_record_id", "source_pointer",
        "field_name", "asset_kind", "raw_reference", "reference_status",
        "metadata_json",
    ),
}
SOURCE_CATALOG_COLUMNS = ("id", "kind", "url", "license_note")
SOURCE_SNAPSHOT_COLUMNS = (
    "id", "source_id", "release_label", "build", "commit", "tree",
    "observed_at", "parser_version", "mapping_version", "status", "metadata_json",
)
SOURCE_FILE_COLUMNS = (
    "id", "snapshot_id", "relative_path", "upstream_blob_oid", "sha256",
    "size_bytes", "format",
)
SOURCE_RECORD_COLUMNS = (
    "id", "source_file_id", "pointer", "record_kind", "native_id",
    "raw_sha256", "raw_json",
)


def digest_rows(rows: Iterable[Sequence[Any]]) -> tuple[int, str]:
    """Stream the canonical JSON array digest without materializing the rowset."""
    digest = hashlib.sha256()
    digest.update(b"[")
    count = 0
    for row in rows:
        if count:
            digest.update(b",")
        digest.update(canonical_json(list(row)).encode("utf-8"))
        count += 1
    digest.update(b"]")
    return count, digest.hexdigest()


def rowset_seal(rows: Iterable[Sequence[Any]]) -> dict[str, Any]:
    count, sha256 = digest_rows(rows)
    return {"row_count": count, "sha256": sha256}


def _query_seal(
    conn: sqlite3.Connection,
    table: str,
    columns: Sequence[str],
    where: str,
    params: Sequence[Any],
) -> dict[str, Any]:
    selected = ",".join(f'"{item}"' for item in columns)
    sql = f'SELECT {selected} FROM "{table}" WHERE {where} ORDER BY "{columns[0]}"'
    return rowset_seal(conn.execute(sql, tuple(params)))


def database_identity(path: Path) -> tuple[int, int, int, int]:
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise OSError("database must be a regular non-symlink file")
    if any(os.path.lexists(f"{path}{suffix}") for suffix in ("-journal", "-wal", "-shm")):
        raise OSError("database sidecars are forbidden")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def scan_runtime_business_seals(
    conn: sqlite3.Connection,
    *,
    ingest_run_id: str,
    snapshot_id: str,
    source_id: str,
    selected_source_file_ids: Sequence[str],
    selected_source_record_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Digest persisted business rows without consulting sources or builders."""
    occurrences = {
        table: _query_seal(
            conn, table, columns, "ingest_run_id=?", (ingest_run_id,)
        )
        for table, columns in OCCURRENCE_TABLE_COLUMNS.items()
    }
    placeholders = ",".join("?" for _ in selected_source_file_ids)
    source_files = _query_seal(
        conn, "source_file", SOURCE_FILE_COLUMNS,
        f"id IN ({placeholders})", tuple(selected_source_file_ids),
    )
    if selected_source_record_ids is None:
        record_where = f"sr.source_file_id IN ({placeholders})"
        record_params = tuple(selected_source_file_ids)
    else:
        record_placeholders = ",".join("?" for _ in selected_source_record_ids)
        record_where = f"sr.id IN ({record_placeholders})"
        record_params = tuple(selected_source_record_ids)
    source_records = rowset_seal(conn.execute(
        f"""SELECT {','.join('sr.' + item for item in SOURCE_RECORD_COLUMNS)}
              FROM source_record sr
              WHERE {record_where}
              ORDER BY sr.id""",
        record_params,
    ))
    roots = {
        str(row[0]) for row in conn.execute(
            f"SELECT root_path FROM source_file WHERE id IN ({placeholders})",
            tuple(selected_source_file_ids),
        )
    }
    if len(roots) != 1 or not next(iter(roots)) or not Path(next(iter(roots))).is_absolute():
        raise ValueError("selected source files do not share one absolute root_path")
    return {
        "algorithm": RUNTIME_SEAL_ALGORITHM,
        "occurrence_tables": occurrences,
        "source_catalog": _query_seal(
            conn, "source_catalog", SOURCE_CATALOG_COLUMNS, "id=?", (source_id,)
        ),
        "source_snapshot": _query_seal(
            conn, "source_snapshot", SOURCE_SNAPSHOT_COLUMNS, "id=?", (snapshot_id,)
        ),
        "source_files": source_files,
        "source_records": source_records,
        "source_file_root_path_policy": "single_absolute_environment_path_excluded_from_digest",
    }


__all__ = [
    "OCCURRENCE_TABLE_COLUMNS",
    "RUNTIME_SEAL_ALGORITHM",
    "SOURCE_CATALOG_COLUMNS",
    "SOURCE_FILE_COLUMNS",
    "SOURCE_RECORD_COLUMNS",
    "SOURCE_SNAPSHOT_COLUMNS",
    "database_identity",
    "digest_rows",
    "rowset_seal",
    "scan_runtime_business_seals",
]
