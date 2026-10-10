"""Deterministic SQLite materialisation for family projection shards.

The builder is intentionally a small mechanical layer.  It receives a
``FamilyShardSpec`` from a family adapter, bulk inserts already-interpreted
family rows, then creates indexes after the data load.  It never copies the
accepted database and never resolves a relation or identity itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .batch_release import (
    BatchReleaseError,
    assert_output_paths,
    create_private_staging,
    publish_absent,
)
from .family_projection_protocol import (
    FamilyShardSpec,
    ShardProtocolError,
    TableSpec,
    canonical_row,
    rowset_digest,
    schema_digest,
    spec_digest,
)


class ShardBuildError(RuntimeError):
    """A shard could not be built or published."""


METADATA_DDL = """
CREATE TABLE shard_metadata (
    key TEXT PRIMARY KEY NOT NULL,
    value_json TEXT NOT NULL CHECK (json_valid(value_json))
)
"""
TABLE_DIGEST_DDL = """
CREATE TABLE shard_table_digest (
    table_name TEXT PRIMARY KEY NOT NULL,
    schema_sql_sha256 TEXT NOT NULL,
    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    rowset_sha256 TEXT NOT NULL,
    ordering_json TEXT NOT NULL CHECK (json_valid(ordering_json)),
    write_set_member INTEGER NOT NULL CHECK (write_set_member IN (0, 1))
)
"""


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _quote_identifier(name: str) -> str:
    # TableSpec has already validated identifiers; keeping quoting here makes
    # generated SQL independent of reserved-word changes in SQLite.
    return '"' + name.replace('"', '""') + '"'


def _insert_rows(connection: sqlite3.Connection, table: TableSpec) -> tuple[tuple[Any, ...], ...]:
    ordered = table.ordered_rows()
    columns = ", ".join(_quote_identifier(column) for column in table.columns)
    placeholders = ", ".join("?" for _ in table.columns)
    connection.executemany(
        f"INSERT INTO {_quote_identifier(table.name)} ({columns}) VALUES ({placeholders})",
        ordered,
    )
    return ordered


def _metadata_rows(spec: FamilyShardSpec, table_digests: list[dict[str, Any]]) -> list[tuple[str, str]]:
    values: dict[str, Any] = {
        "protocol": "family-projection-shard-v1",
        "family": spec.family,
        "family_contract_version": spec.family_contract_version,
        "ingest_run_id": spec.ingest_run_id,
        "snapshot_id": spec.snapshot_id,
        "parser_build_id": spec.parser_build_id,
        "coverage_level": spec.coverage_level,
        "corpus_complete": spec.corpus_complete,
        "product_accepted": spec.product_accepted,
        "read_set": list(spec.read_set),
        "write_set": list(spec.write_set),
        "shard_schema_version": spec.shard_schema_version,
        "spec_digest": spec_digest(spec),
        "table_digests": table_digests,
        "family_metadata": dict(spec.metadata),
    }
    return [(key, json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))) for key, value in sorted(values.items())]


def build_shard(spec: FamilyShardSpec, output_path: str | Path) -> dict[str, Any]:
    """Build and atomically publish one family-owned SQLite shard.

    A failed build only removes the private staging inode created by this
    invocation.  Existing targets are never replaced.
    """

    if spec.product_accepted:
        raise ShardBuildError("M2 shard cannot be marked product_accepted")
    target = Path(output_path).absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    staging: Path | None = None
    try:
        assert_output_paths(target, target_must_be_absent=True)
        descriptor, staging = create_private_staging(target, prefix=f".m2-{spec.family}-")
        os.close(descriptor)
        descriptor = None
        connection = sqlite3.connect(staging)
        try:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(METADATA_DDL)
            connection.executescript(TABLE_DIGEST_DDL)
            for table in spec.tables:
                connection.execute(table.create_sql)
            connection.execute("BEGIN")
            table_digests: list[dict[str, Any]] = []
            for table in spec.tables:
                ordered = _insert_rows(connection, table)
                table_digests.append(
                    {
                        "table_name": table.name,
                        "schema_sql_sha256": schema_digest(table.create_sql),
                        "row_count": len(ordered),
                        "rowset_sha256": rowset_digest(ordered),
                        "ordering": list(range(len(table.columns))),
                        "write_set_member": table.name in spec.write_set,
                    }
                )
            # Secondary indexes are deliberately created after bulk inserts.
            for table in spec.tables:
                for statement in table.index_sql:
                    connection.execute(statement)
            connection.executemany(
                "INSERT INTO shard_table_digest(table_name,schema_sql_sha256,row_count,rowset_sha256,ordering_json,write_set_member) VALUES (?,?,?,?,?,?)",
                [
                    (
                        item["table_name"], item["schema_sql_sha256"], item["row_count"],
                        item["rowset_sha256"], json.dumps(item["ordering"]),
                        int(item["write_set_member"]),
                    )
                    for item in table_digests
                ],
            )
            connection.executemany(
                "INSERT INTO shard_metadata(key,value_json) VALUES (?,?)",
                _metadata_rows(spec, table_digests),
            )
            connection.commit()
            foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_rows:
                raise ShardBuildError(
                    f"family shard foreign-key check failed: {foreign_key_rows[:3]}"
                )
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        publish_absent(target, staging)
        staging = None
    except (sqlite3.Error, OSError, BatchReleaseError, ShardProtocolError) as exc:
        raise ShardBuildError(f"failed to build shard {spec.family}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if staging is not None:
            for sidecar in (Path(f"{staging}-journal"), Path(f"{staging}-wal"), Path(f"{staging}-shm")):
                try:
                    sidecar.unlink()
                except FileNotFoundError:
                    pass
            try:
                staging.unlink()
            except FileNotFoundError:
                pass

    return shard_summary(target)


def shard_summary(path: str | Path) -> dict[str, Any]:
    """Read mechanical metadata and recompute file identity for a shard."""

    value = Path(path).absolute()
    if not value.is_file() or value.is_symlink():
        raise ShardBuildError(f"shard is not a regular file: {value}")
    connection = sqlite3.connect(f"file:{value}?mode=ro", uri=True)
    try:
        metadata = {
            key: json.loads(raw)
            for key, raw in connection.execute("SELECT key,value_json FROM shard_metadata ORDER BY key")
        }
        tables = [
            {
                "table_name": name,
                "schema_sql_sha256": schema_hash,
                "row_count": count,
                "rowset_sha256": digest,
                "ordering": json.loads(ordering),
                "write_set_member": bool(member),
            }
            for name, schema_hash, count, digest, ordering, member in connection.execute(
                "SELECT table_name,schema_sql_sha256,row_count,rowset_sha256,ordering_json,write_set_member FROM shard_table_digest ORDER BY table_name"
            )
        ]
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        raise ShardBuildError(f"invalid shard metadata: {value}") from exc
    finally:
        connection.close()
    return {
        "path": str(value),
        "size_bytes": value.stat().st_size,
        "sha256": _hash_file(value),
        "metadata": metadata,
        "tables": tables,
    }


__all__ = ["METADATA_DDL", "TABLE_DIGEST_DDL", "ShardBuildError", "build_shard", "shard_summary"]
