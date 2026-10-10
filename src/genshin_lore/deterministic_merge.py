"""Single-writer deterministic merge for family-owned projection shards."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Sequence

from .batch_release import BatchReleaseError, assert_output_paths, create_private_staging, publish_absent
from .batch_m2_replay import ReplayContractError, ReplayInputUnavailable, validate_accepted_schema13_baseline
from .family_projection_protocol import FamilyShardSpec, TableSpec, canonical_row, rowset_digest, schema_digest, spec_digest
from .shard_builder import ShardBuildError, shard_summary


class MergeError(RuntimeError):
    """A shard set cannot be merged deterministically."""


MERGE_METADATA_DDL = """
CREATE TABLE batch_m2_merge_metadata (
    key TEXT PRIMARY KEY NOT NULL,
    value_json TEXT NOT NULL CHECK (json_valid(value_json))
)
"""


def _q(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _table_rows(connection: sqlite3.Connection, table: TableSpec) -> tuple[tuple[Any, ...], ...]:
    columns = ", ".join(_q(column) for column in table.columns)
    rows = connection.execute(f"SELECT {columns} FROM {_q(table.name)}").fetchall()
    key = table.sort_key or (lambda row: tuple(row))
    return tuple(sorted((tuple(row) for row in rows), key=lambda row: (key(row), canonical_row(row))))


def _candidate_table_digest(connection: sqlite3.Connection, table: TableSpec) -> dict[str, Any]:
    rows = _table_rows(connection, table)
    return {
        "table_name": table.name,
        "schema_sql_sha256": schema_digest(table.create_sql),
        "row_count": len(rows),
        "rowset_sha256": rowset_digest(rows),
    }


def _validate_inputs(shards: Sequence[tuple[FamilyShardSpec, str | Path]]) -> list[tuple[FamilyShardSpec, Path, dict[str, Any]]]:
    if not shards:
        raise MergeError("at least one shard is required")
    result: list[tuple[FamilyShardSpec, Path, dict[str, Any]]] = []
    seen_families: set[str] = set()
    seen_tables: set[str] = set()
    for spec, raw_path in shards:
        if spec.product_accepted:
            raise MergeError(f"product-accepted family cannot enter M2 merge: {spec.family}")
        if spec.family in seen_families:
            raise MergeError(f"duplicate family shard: {spec.family}")
        seen_families.add(spec.family)
        overlap = seen_tables.intersection(spec.table_names)
        if overlap:
            raise MergeError(f"family table collision: {sorted(overlap)}")
        seen_tables.update(spec.table_names)
        path = Path(raw_path).absolute()
        summary = shard_summary(path)
        metadata = summary["metadata"]
        if metadata.get("spec_digest") != spec_digest(spec):
            raise MergeError(f"shard spec digest mismatch: {path}")
        result.append((spec, path, summary))
    return sorted(result, key=lambda item: (item[0].family, item[0].family_contract_version, item[0].ingest_run_id))


def merge_shards(
    shards: Sequence[tuple[FamilyShardSpec, str | Path]],
    output_path: str | Path,
    *,
    baseline_path: str | Path | None = None,
) -> dict[str, Any]:
    """Merge family shards once, in a fixed family/table/row order.

    ``baseline_path`` is optional and, when supplied, is copied exactly once
    for the combined candidate.  It is never copied once per family.  The
    candidate remains a non-product M2 artifact regardless of its baseline.
    """

    validated = _validate_inputs(shards)
    target = Path(output_path).absolute()
    baseline = None if baseline_path is None else Path(baseline_path).absolute()
    semantic_inputs = [(f"shard:{spec.family}", path) for spec, path, _ in validated]
    if baseline is not None:
        if not baseline.is_file() or baseline.is_symlink():
            raise MergeError(f"baseline is not a regular file: {baseline}")
        try:
            validate_accepted_schema13_baseline(baseline)
        except (ReplayInputUnavailable, ReplayContractError) as exc:
            raise MergeError(f"baseline is not the accepted Schema 13 source: {exc}") from exc
        semantic_inputs.append(("baseline", baseline))
    assert_output_paths(target, inputs=semantic_inputs, target_must_be_absent=True)
    descriptor: int | None = None
    staging: Path | None = None
    try:
        descriptor, staging = create_private_staging(target, prefix=".m2-merge-")
        os.close(descriptor)
        descriptor = None
        if baseline is not None:
            shutil.copyfile(baseline, staging)
        connection = sqlite3.connect(staging)
        try:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA defer_foreign_keys=ON")
            connection.executescript(MERGE_METADATA_DDL)
            attached: list[str] = []
            for ordinal, (spec, path, _) in enumerate(validated):
                alias = f"m2_shard_{ordinal}"
                connection.execute(f"ATTACH DATABASE ? AS {_q(alias)}", (str(path),))
                attached.append(alias)
                # The family owns its table dependency order.  Keeping that
                # explicit order lets family-local provenance FKs load their
                # parent rows before children without teaching this shared
                # layer any identity or relationship semantics.
                for table in spec.tables:
                    existing = connection.execute(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table.name,)
                    ).fetchone()
                    if existing is not None:
                        raise MergeError(f"candidate already contains family table: {table.name}")
                    connection.execute(table.create_sql)
            connection.execute("BEGIN")
            for ordinal, (spec, _, _) in enumerate(validated):
                alias = f"m2_shard_{ordinal}"
                for table in spec.tables:
                    columns = ", ".join(_q(column) for column in table.columns)
                    order_columns = table.order_columns or table.columns
                    order_sql = ", ".join(_q(column) for column in order_columns)
                    connection.execute(
                        f"INSERT INTO main.{_q(table.name)} ({columns}) "
                        f"SELECT {columns} FROM {_q(alias)}.{_q(table.name)} ORDER BY {order_sql}"
                    )
            # Secondary indexes are built only after every family table has
            # been bulk-loaded.  Their creation order is deterministic but
            # does not affect family-owned row semantics.
            for spec, _, _ in validated:
                for table in spec.tables:
                    for index_sql in table.index_sql:
                        connection.execute(index_sql)
            digest_rows: list[dict[str, Any]] = []
            for spec, _, _ in validated:
                for table in sorted(spec.tables, key=lambda item: item.name):
                    digest_rows.append(_candidate_table_digest(connection, table))
            metadata: dict[str, Any] = {
                "protocol": "deterministic-single-writer-merge-v1",
                "coverage_level": "R1_structured_family",
                "corpus_complete": False,
                "product_accepted": False,
                "baseline_path": None if baseline is None else str(baseline),
                "baseline_sha256": None if baseline is None else _file_sha256(baseline),
                "family_order": [spec.family for spec, _, _ in validated],
                "shards": [
                    {
                        "family": spec.family,
                        "family_contract_version": spec.family_contract_version,
                        "ingest_run_id": spec.ingest_run_id,
                        "path": str(path),
                        "sha256": summary["sha256"],
                        "spec_digest": spec_digest(spec),
                        "read_set": list(spec.read_set),
                        "write_set": list(spec.write_set),
                    }
                    for spec, path, summary in validated
                ],
                "table_digests": digest_rows,
            }
            connection.executemany(
                "INSERT INTO batch_m2_merge_metadata(key,value_json) VALUES (?,?)",
                [(key, json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))) for key, value in sorted(metadata.items())],
            )
            connection.commit()
            fk = connection.execute("PRAGMA foreign_key_check").fetchall()
            if fk:
                raise MergeError(f"merged candidate foreign-key check failed: {fk[:3]}")
            connection.execute("PRAGMA optimize")
            connection.commit()
            for alias in reversed(attached):
                connection.execute(f"DETACH DATABASE {_q(alias)}")
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        publish_absent(target, staging, inputs=semantic_inputs)
        staging = None
    except (OSError, sqlite3.Error, BatchReleaseError, ShardBuildError, MergeError) as exc:
        raise MergeError(f"failed deterministic M2 merge: {exc}") from exc
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
    return merge_summary(target)


def merge_summary(path: str | Path) -> dict[str, Any]:
    target = Path(path).absolute()
    if not target.is_file() or target.is_symlink():
        raise MergeError(f"merge candidate is not a regular file: {target}")
    connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    try:
        metadata = {
            key: json.loads(raw)
            for key, raw in connection.execute("SELECT key,value_json FROM batch_m2_merge_metadata ORDER BY key")
        }
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        raise MergeError(f"invalid merge metadata: {target}") from exc
    finally:
        connection.close()
    return {"path": str(target), "size_bytes": target.stat().st_size, "sha256": _file_sha256(target), "metadata": metadata}


__all__ = ["MERGE_METADATA_DDL", "MergeError", "merge_shards", "merge_summary"]
