"""Independent, family-aware verification for Batch M2 shards and merges."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping, Sequence

from .batch_m2_replay import ReplayContractError, ReplayInputUnavailable, validate_accepted_schema13_baseline
from .family_projection_protocol import FamilyShardSpec, TableSpec, canonical_row, rowset_digest, schema_digest, spec_digest
from .shard_builder import ShardBuildError


class M2VerificationError(RuntimeError):
    """A shard or merge candidate failed its independent oracle."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _table_rows(connection: sqlite3.Connection, table: TableSpec, prefix: str = "main") -> tuple[tuple[Any, ...], ...]:
    columns = ", ".join('"' + column.replace('"', '""') + '"' for column in table.columns)
    rows = connection.execute(f'SELECT {columns} FROM "{prefix}"."{table.name}"').fetchall()
    key = table.sort_key or (lambda row: tuple(row))
    return tuple(sorted((tuple(row) for row in rows), key=lambda row: (key(row), canonical_row(row))))


def _fail(message: str) -> None:
    raise M2VerificationError(message)


def _read_metadata(connection: sqlite3.Connection, table: str) -> dict[str, Any]:
    try:
        return {
            key: json.loads(raw)
            for key, raw in connection.execute(f'SELECT key,value_json FROM "{table}" ORDER BY key')
        }
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        _fail(f"invalid {table} metadata: {exc}")


def reconstruct_expected_specs(
    source_root: str | Path,
    repo_root: str | Path,
    families: Sequence[str],
    replay_sources: Mapping[str, str | Path],
) -> tuple[FamilyShardSpec, ...]:
    """Re-read source/manifests for a verifier-owned M2 spec set.

    The builder's in-memory specs are deliberately not accepted as the merge
    oracle. Each family adapter is invoked again after shard construction.
    Replay adapters additionally validate the exact Schema 13 baseline.
    """

    from .batch_m2_avatar import load_avatar_codex_spec
    from .batch_m2_replay import FAMILY_TABLES, load_replay_spec

    requested = tuple(families)
    if not requested:
        _fail("at least one family is required for verifier reconstruction")
    if len(set(requested)) != len(requested):
        _fail("duplicate family in verifier reconstruction request")
    result: list[FamilyShardSpec] = []
    for family in requested:
        try:
            if family == "avatar_codex_archive":
                spec = load_avatar_codex_spec(source_root, repo_root)
            elif family in FAMILY_TABLES:
                source_db = replay_sources.get(family)
                if source_db is None:
                    _fail(f"missing explicit replay source for {family}")
                spec = load_replay_spec(family, source_db)
            else:
                _fail(f"unsupported M2 verifier family: {family}")
        except (ReplayInputUnavailable, ReplayContractError) as exc:
            _fail(f"independent {family} source reconstruction failed: {exc}")
        result.append(spec)
    return tuple(result)


def verify_shard(path: str | Path, expected: FamilyShardSpec | None = None) -> dict[str, Any]:
    """Recompute all shard rowsets from SQLite and optionally a family spec.

    The verifier does not call the builder and does not accept a builder count
    or manifest digest as an oracle.  When ``expected`` is supplied, the
    family adapter has independently reconstructed the source boundary.
    """

    target = Path(path).absolute()
    if not target.is_file() or target.is_symlink():
        _fail(f"shard is not a regular file: {target}")
    connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            _fail("shard verifier could not enable foreign-key enforcement")
        metadata = _read_metadata(connection, "shard_metadata")
        if metadata.get("product_accepted") is not False:
            _fail("M2 shard is product accepted")
        if metadata.get("protocol") != "family-projection-shard-v1":
            _fail("shard protocol marker mismatch")
        digest_rows = {
            row[0]: {
                "schema_sql_sha256": row[1],
                "row_count": int(row[2]),
                "rowset_sha256": row[3],
                "ordering": json.loads(row[4]),
            }
            for row in connection.execute(
                "SELECT table_name,schema_sql_sha256,row_count,rowset_sha256,ordering_json FROM shard_table_digest ORDER BY table_name"
            )
        }
        actual_table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            if row[0] not in {"shard_metadata", "shard_table_digest"}
        }
        if actual_table_names != set(digest_rows):
            _fail(f"shard table digest closure mismatch: {sorted(actual_table_names ^ set(digest_rows))}")
        expected_tables = {} if expected is None else {table.name: table for table in expected.tables}
        if expected is not None:
            for key in ("family", "family_contract_version", "ingest_run_id", "snapshot_id", "parser_build_id", "coverage_level", "corpus_complete", "product_accepted", "read_set", "write_set"):
                want = getattr(expected, key)
                if metadata.get(key) != (list(want) if isinstance(want, tuple) else want):
                    _fail(f"shard metadata mismatch for {key}")
            if metadata.get("spec_digest") != spec_digest(expected):
                _fail("shard spec digest mismatch")
            if actual_table_names != set(expected.table_names):
                _fail("family table closure mismatch")
        results: list[dict[str, Any]] = []
        for table_name in sorted(actual_table_names):
            if expected is None:
                # The external metadata still supplies the column order; this
                # path is purely mechanical and is never the Avatar business
                # oracle.
                columns = tuple(row[1] for row in connection.execute(f'PRAGMA table_info("{table_name}")'))
                table = TableSpec(
                    name=table_name,
                    create_sql=connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (table_name,)).fetchone()[0],
                    columns=columns,
                    order_columns=columns,
                )
            else:
                table = expected_tables[table_name]
                sql = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (table_name,)).fetchone()[0]
                if schema_digest(sql) != schema_digest(table.create_sql):
                    _fail(f"table schema mismatch: {table_name}")
            rows = _table_rows(connection, table)
            digest = rowset_digest(rows)
            recorded = digest_rows[table_name]
            if len(rows) != recorded["row_count"] or digest != recorded["rowset_sha256"]:
                _fail(f"table rowset digest mismatch: {table_name}")
            if expected is not None:
                expected_rows = table.ordered_rows()
                if rows != expected_rows:
                    _fail(f"family source rowset mismatch: {table_name}")
            results.append({"table_name": table_name, "row_count": len(rows), "rowset_sha256": digest})
        # G2 data is never a legal source of the M2 Avatar pilot.
        if expected is not None and expected.family == "avatar_codex_archive":
            for table_name in actual_table_names:
                columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table_name}")')]
                for column in columns:
                    try:
                        if connection.execute(
                            f'SELECT 1 FROM "{table_name}" WHERE CAST("{column}" AS TEXT) LIKE ? LIMIT 1',
                            ("%phase4g2%",),
                        ).fetchone():
                            _fail("Schema 14/G2 marker found in Avatar M2 shard")
                    except sqlite3.Error:
                        # Non-text/blob columns may reject a LIKE operation;
                        # the explicit run IDs are checked by the metadata and
                        # identity columns below.
                        continue
        foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_rows:
            _fail(f"shard foreign-key check failed: {foreign_key_rows[:3]}")
    except sqlite3.Error as exc:
        _fail(f"cannot inspect shard: {exc}")
    finally:
        connection.close()
    return {
        "status": "verified",
        "path": str(target),
        "size_bytes": target.stat().st_size,
        "sha256": _sha256(target),
        "family": metadata.get("family"),
        "ingest_run_id": metadata.get("ingest_run_id"),
        "coverage_level": metadata.get("coverage_level"),
        "corpus_complete": metadata.get("corpus_complete"),
        "product_accepted": metadata.get("product_accepted"),
        "tables": results,
    }


def _verify_expected_shard(
    path: str | Path,
    spec: FamilyShardSpec,
) -> tuple[dict[str, Any], dict[str, tuple[tuple[Any, ...], ...]], dict[str, Any]]:
    """Verify one expected shard and return its physical rows and identity.

    The source shard is opened read-only and checked independently of the
    merge candidate.  In particular, the merge verifier never treats the
    builder's report as an oracle for source rows, metadata, or file identity.
    """

    target = Path(path).absolute()
    verification = verify_shard(target, spec)
    connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    try:
        metadata = _read_metadata(connection, "shard_metadata")
        rows = {
            table.name: _table_rows(connection, table)
            for table in spec.tables
        }
        expected_metadata = {
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
            "table_digests": [
                {
                    "table_name": table.name,
                    "schema_sql_sha256": schema_digest(table.create_sql),
                    "row_count": len(rows[table.name]),
                    "rowset_sha256": rowset_digest(rows[table.name]),
                    "ordering": list(range(len(table.columns))),
                    "write_set_member": table.name in spec.write_set,
                }
                for table in spec.tables
            ],
            "family_metadata": dict(spec.metadata),
        }
        if metadata != expected_metadata:
            _fail(f"expected shard metadata mismatch: {target}")
    except sqlite3.Error as exc:
        _fail(f"cannot inspect expected shard {target}: {exc}")
    finally:
        connection.close()

    # Recompute the file digest after the independent read.  A changed source
    # inode cannot silently pair rows from one version with metadata from
    # another version.
    if _sha256(target) != verification["sha256"]:
        _fail(f"expected shard changed during verification: {target}")
    record = {
        "family": spec.family,
        "family_contract_version": spec.family_contract_version,
        "ingest_run_id": spec.ingest_run_id,
        "path": str(target),
        "sha256": verification["sha256"],
        "spec_digest": spec_digest(spec),
        "read_set": list(spec.read_set),
        "write_set": list(spec.write_set),
    }
    return verification, rows, record


def verify_merge(
    path: str | Path,
    expected_shards: Sequence[tuple[FamilyShardSpec, str | Path]],
    *,
    baseline_path: str | Path | None = None,
    require_accepted_schema13_baseline: bool = True,
) -> dict[str, Any]:
    """Verify a merged candidate against independently verified shard rows.

    The merge candidate is not self-authenticating: every expected shard is
    opened read-only, its rows and metadata are recomputed, and candidate rows
    must match those physical source rowsets exactly.  Merge metadata and
    table closure are then compared to the same independently derived values.
    """

    target = Path(path).absolute()
    if not target.is_file() or target.is_symlink():
        _fail(f"merge candidate is not a regular file: {target}")

    ordered = sorted(
        expected_shards,
        key=lambda item: (
            item[0].family,
            item[0].family_contract_version,
            item[0].ingest_run_id,
        ),
    )
    if not ordered:
        _fail("at least one expected shard is required")
    seen_families: set[str] = set()
    expected_tables: set[str] = set()
    for spec, _ in ordered:
        if spec.family in seen_families:
            _fail(f"duplicate expected family shard: {spec.family}")
        seen_families.add(spec.family)
        overlap = expected_tables.intersection(spec.table_names)
        if overlap:
            _fail(f"duplicate expected family table: {sorted(overlap)}")
        expected_tables.update(spec.table_names)

    # Verify every source shard before inspecting candidate metadata.  These
    # rows are the physical source oracle for the candidate comparison.
    shard_rows: dict[str, tuple[tuple[Any, ...], ...]] = {}
    shard_records: list[dict[str, Any]] = []
    for spec, raw_path in ordered:
        _, rows, record = _verify_expected_shard(raw_path, spec)
        shard_rows.update(rows)
        shard_records.append(record)

    baseline = None if baseline_path is None else Path(baseline_path).absolute()
    baseline_table_names: set[str] = set()
    baseline_sha256: str | None = None
    if baseline is not None:
        if not baseline.is_file() or baseline.is_symlink():
            _fail("baseline is unavailable for preservation oracle")
        if require_accepted_schema13_baseline:
            try:
                _, accepted_sha256 = validate_accepted_schema13_baseline(baseline)
            except (ReplayInputUnavailable, ReplayContractError) as exc:
                _fail(f"baseline is not the accepted Schema 13 source: {exc}")
            if accepted_sha256 != _sha256(baseline):
                _fail("accepted Schema 13 baseline changed during verifier preflight")
        baseline_sha256 = _sha256(baseline)
        try:
            baseline_connection = sqlite3.connect(f"file:{baseline}?mode=ro", uri=True)
            try:
                baseline_table_names = {
                    row[0]
                    for row in baseline_connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    )
                }
            finally:
                baseline_connection.close()
        except sqlite3.Error as exc:
            _fail(f"cannot inspect baseline: {exc}")

    connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    try:
        metadata = _read_metadata(connection, "batch_m2_merge_metadata")
        if metadata.get("protocol") != "deterministic-single-writer-merge-v1":
            _fail("merge protocol marker mismatch")
        if (
            metadata.get("coverage_level") != "R1_structured_family"
            or metadata.get("corpus_complete") is not False
            or metadata.get("product_accepted") is not False
        ):
            _fail("merge candidate overclaims product coverage")
        expected_family_order = [spec.family for spec, _ in ordered]
        if metadata.get("family_order") != expected_family_order:
            _fail("merge family order is not deterministic")

        table_results: list[dict[str, Any]] = []
        expected_table_digests: list[dict[str, Any]] = []
        for spec, _ in ordered:
            for table in sorted(spec.tables, key=lambda item: item.name):
                sql_row = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table.name,),
                ).fetchone()
                if sql_row is None or schema_digest(sql_row[0]) != schema_digest(table.create_sql):
                    _fail(f"merged schema mismatch: {table.name}")
                rows = _table_rows(connection, table)
                source_rows = shard_rows[table.name]
                if rows != source_rows:
                    _fail(f"merged source rowset mismatch: {table.name}")
                digest = rowset_digest(rows)
                table_results.append(
                    {
                        "table_name": table.name,
                        "row_count": len(rows),
                        "rowset_sha256": digest,
                    }
                )
                expected_table_digests.append(
                    {
                        "table_name": table.name,
                        "schema_sql_sha256": schema_digest(table.create_sql),
                        "row_count": len(rows),
                        "rowset_sha256": digest,
                    }
                )

        # A no-baseline candidate may contain only the expected family tables
        # and merge metadata.  With a baseline, its untouched user tables are
        # also part of the explicitly expected closure; anything else fails.
        present = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        expected_present = expected_tables | baseline_table_names | {"batch_m2_merge_metadata"}
        if present != expected_present:
            _fail(f"merged table closure mismatch: {sorted(present ^ expected_present)}")

        expected_merge_metadata = {
            "protocol": "deterministic-single-writer-merge-v1",
            "coverage_level": "R1_structured_family",
            "corpus_complete": False,
            "product_accepted": False,
            "baseline_path": None if baseline is None else str(baseline),
            "baseline_sha256": baseline_sha256,
            "family_order": expected_family_order,
            "shards": shard_records,
            "table_digests": expected_table_digests,
        }
        if metadata != expected_merge_metadata:
            _fail("merge metadata mismatch")

        if connection.execute("PRAGMA foreign_key_check").fetchall():
            _fail("merged candidate foreign-key check failed")
        baseline_result = {"checked": False}
        if baseline is not None:
            baseline_result = _verify_baseline_tables(baseline, target, expected_tables)
    except sqlite3.Error as exc:
        _fail(f"cannot inspect merge candidate: {exc}")
    finally:
        connection.close()
    return {
        "status": "verified",
        "path": str(target),
        "size_bytes": target.stat().st_size,
        "sha256": _sha256(target),
        "family_order": metadata.get("family_order"),
        "tables": table_results,
        "baseline_preservation": baseline_result,
    }


def _verify_baseline_tables(baseline: Path, candidate: Path, added_tables: set[str]) -> dict[str, Any]:
    """Compare untouched baseline tables for the optional combined candidate."""

    base = sqlite3.connect(f"file:{baseline}?mode=ro", uri=True)
    cand = sqlite3.connect(f"file:{candidate}?mode=ro", uri=True)
    checked = 0
    try:
        names = [row[0] for row in base.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for name in names:
            if name in added_tables or name == "batch_m2_merge_metadata":
                continue
            base_sql = base.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
            cand_sql = cand.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
            if cand_sql is None or schema_digest(base_sql[0]) != schema_digest(cand_sql[0]):
                _fail(f"baseline table schema changed: {name}")
            cols = tuple(row[1] for row in base.execute(f'PRAGMA table_info("{name}")'))
            rows_a = sorted((tuple(row) for row in base.execute(f'SELECT * FROM "{name}"')), key=canonical_row)
            rows_b = sorted((tuple(row) for row in cand.execute(f'SELECT * FROM "{name}"')), key=canonical_row)
            if rows_a != rows_b:
                _fail(f"baseline table rows changed: {name}")
            checked += 1
    finally:
        base.close()
        cand.close()
    return {"checked": True, "table_count": checked, "baseline_sha256": _sha256(baseline)}


__all__ = [
    "M2VerificationError",
    "reconstruct_expected_specs",
    "verify_merge",
    "verify_shard",
]
