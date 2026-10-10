"""Independent R1 Schema 19 candidate verifier; never calls the builder."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from .batch_m0_baseline import SOURCE_SNAPSHOT_ID
from .batch_q2_uplift import Q2_DEFINITIONS, Q2_COVERAGE, qualify_q2_artifact
from .db import validate_exact_schema_structure
from .util import canonical_json

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = 19
SCHEMA17_SIZE = 5_244_338_176
SCHEMA17_SHA256 = "464a49e77b2ebf7fd7b0ae68d907fdd3307ef01e0d8537065f4b54ad4a53ddab"
Q2_SIZE = 90_963_968
Q2_SHA256 = "888f948ad379bfda3f4e7e071d4ad08b6a4760322cc1a40914b69f7ca06726a5"
Q2_MANIFEST_SHA256 = "09d6bb410851cc0ad1c804e6840e5fe6d0934e4648d575dcad38cd757b850801"
R1_CONTRACT = "batch-r1-schema19-wave01-text-release-candidate-v1"
R1_COVERAGE = "R1_structured_family_release_candidate"
MIGRATION_SHA256 = {18: "685e04f15de3cfc7773f4f17ca52367f723a096b243245773ffd7f8420bad22a",
                    19: "05a36070107dcbb04859cdf4278fe1744712fb4e8e7a43d2ed8a7435d25bf16f"}
MIGRATION_APPLIED_AT = {18: "2026-09-07T00:00:00+00:00", 19: "2026-09-08T00:00:00+00:00"}
CHUNK_SIZE = 50_000
APPEND_TABLES = {"schema_migration", "parser_build", "ingest_run", "batch_wave", "batch_shard",
                 "batch_shard_import", "batch_table_digest", "batch_table_chunk_digest"}
TABLE_ORDER_SUFFIX = {
    "_row_occurrence": ("ingest_run_id", "source_file_path", "source_ordinal", "occurrence_id"),
    "_text_owner_occurrence": ("ingest_run_id", "row_occurrence_id", "field_pair_ordinal", "owner_id"),
    "_text_resolution_occurrence": ("ingest_run_id", "owner_id", "locale", "resolution_id"),
    "_text_candidate_occurrence": ("ingest_run_id", "owner_id", "locale", "relative_path", "pair_ordinal", "candidate_id"),
}


class R1VerificationError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _id(*parts: object) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8", "surrogateescape")); digest.update(b"\0")
    return digest.hexdigest()


def _q(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro&immutable=1"


def _tables(conn: sqlite3.Connection, schema: str) -> set[str]:
    return {str(row[0]) for row in conn.execute(f"SELECT name FROM {_q(schema)}.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def _columns(conn: sqlite3.Connection, schema: str, table: str) -> tuple[str, ...]:
    return tuple(str(row[1]) for row in conn.execute(f"PRAGMA {_q(schema)}.table_info({_q(table)})"))


def _difference(conn: sqlite3.Connection, left: str, right: str, table: str, columns: tuple[str, ...]) -> bool:
    selected = ",".join(_q(column) for column in columns)
    return conn.execute(f"SELECT 1 FROM (SELECT {selected} FROM {_q(left)}.{_q(table)} EXCEPT SELECT {selected} FROM {_q(right)}.{_q(table)}) LIMIT 1").fetchone() is not None


def _extra_rows(conn: sqlite3.Connection, table: str) -> set[tuple[Any, ...]]:
    columns = _columns(conn, "main", table); selected = ",".join(_q(column) for column in columns)
    return {tuple(row) for row in conn.execute(f"SELECT {selected} FROM main.{_q(table)} EXCEPT SELECT {selected} FROM baseline.{_q(table)}")}


def _table_order(table: str) -> tuple[str, ...]:
    for suffix, ordering in TABLE_ORDER_SUFFIX.items():
        if table.endswith(suffix): return ordering
    raise R1VerificationError(f"missing table ordering contract: {table}")


def _ordered_rows(conn: sqlite3.Connection, table: str, run: str) -> tuple[tuple[Any, ...], ...]:
    columns = _columns(conn, "main", table); selected = ",".join(_q(value) for value in columns)
    order = ",".join(_q(value) for value in _table_order(table))
    return tuple(tuple(row) for row in conn.execute(f"SELECT {selected} FROM main.{_q(table)} WHERE ingest_run_id=? ORDER BY {order}", (run,)))


def _rowset_sha(rows: tuple[tuple[Any, ...], ...]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(canonical_json(list(row)).encode()); digest.update(b"\n")
    return digest.hexdigest()


def _assert_extra(conn: sqlite3.Connection, table: str, expected: set[tuple[Any, ...]]) -> None:
    actual = _extra_rows(conn, table)
    if actual != expected:
        raise R1VerificationError(f"exact additive closure mismatch: {table} expected={len(expected)} actual={len(actual)}")


def _item(manifest: Mapping[str, Any], family: str) -> Mapping[str, Any]:
    return next(value for value in manifest["families"] if value["family"] == family)


def verify_candidate(candidate: str | Path, baseline: str | Path, q2_artifact: str | Path,
                     q2_manifest: str | Path) -> dict[str, Any]:
    target, base, q2, manifest_path = (Path(value).resolve() for value in (candidate, baseline, q2_artifact, q2_manifest))
    if not target.is_file() or target.is_symlink(): raise R1VerificationError("R1 candidate unavailable")
    if not base.is_file() or base.is_symlink() or base.stat().st_size != SCHEMA17_SIZE or _sha(base) != SCHEMA17_SHA256: raise R1VerificationError("Schema 17 baseline seal mismatch")
    if not q2.is_file() or q2.is_symlink() or q2.stat().st_size != Q2_SIZE or _sha(q2) != Q2_SHA256: raise R1VerificationError("Q2 artifact seal mismatch")
    if _sha(manifest_path) != Q2_MANIFEST_SHA256: raise R1VerificationError("Q2 tracked manifest seal mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")); qualified = qualify_q2_artifact(q2, manifest_path)
    if qualified.get("families") != manifest.get("families"): raise R1VerificationError("Q2 internal/tracked family mismatch")
    for version, expected in MIGRATION_SHA256.items():
        migration = next((ROOT / "migrations").glob(f"{version:04d}_*.sql"), None)
        if migration is None or _sha(migration) != expected: raise R1VerificationError(f"migration {version} raw seal mismatch")
    conn = sqlite3.connect(_uri(target), uri=True); conn.row_factory = sqlite3.Row; conn.execute("PRAGMA query_only=ON")
    try:
        versions = tuple(int(row[0]) for row in conn.execute("SELECT version FROM schema_migration ORDER BY version"))
        if versions != tuple(range(1, 20)): raise R1VerificationError("migration ledger is not exact 1..19")
        timestamps = {int(row[0]): str(row[1]) for row in conn.execute("SELECT version,applied_at FROM schema_migration WHERE version IN (18,19)")}
        if timestamps != MIGRATION_APPLIED_AT: raise R1VerificationError("migration timestamps are not pinned")
        validate_exact_schema_structure(conn, 19)
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok": raise R1VerificationError("quick_check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchall(): raise R1VerificationError("foreign_key_check failed")
        conn.execute("ATTACH DATABASE ? AS baseline", (_uri(base),)); conn.execute("ATTACH DATABASE ? AS q2", (_uri(q2),))
        baseline_tables, candidate_tables = _tables(conn, "baseline"), _tables(conn, "main")
        new_tables = {family + suffix for family in Q2_DEFINITIONS for suffix in ("_text_owner_occurrence", "_text_resolution_occurrence", "_text_candidate_occurrence")}
        if candidate_tables != baseline_tables | new_tables: raise R1VerificationError("candidate table closure mismatch")
        uplift_rows = {family + "_row_occurrence" for family in Q2_DEFINITIONS}
        for table in sorted(baseline_tables):
            columns = _columns(conn, "baseline", table)
            if columns != _columns(conn, "main", table): raise R1VerificationError(f"baseline table shape changed: {table}")
            if table in APPEND_TABLES or table in uplift_rows:
                if _difference(conn, "baseline", "main", table, columns): raise R1VerificationError(f"baseline rows removed: {table}")
            elif _difference(conn, "baseline", "main", table, columns) or _difference(conn, "main", "baseline", table, columns):
                raise R1VerificationError(f"protected baseline table changed: {table}")

        expected_parsers: set[tuple[Any, ...]] = set(); expected_runs: set[tuple[Any, ...]] = set()
        family_results: list[dict[str, Any]] = []; family_digests: dict[str, list[tuple[str, int, str]]] = {}
        for family in Q2_DEFINITIONS:
            item = _item(manifest, family); run, parser, contract = item["ingest_run_id"], item["parser_build_id"], item["family_contract_version"]
            config, source_file = item["configuration_sha256"], item["source_file"]
            expected_parsers.add((parser, "batch-q2-wave01-text-uplift-v1", contract, _id("parser", parser, config), config, MIGRATION_APPLIED_AT[19]))
            scope = {"family": family, "family_contract_version": contract, "snapshot_id": SOURCE_SNAPSHOT_ID,
                     "source_file": source_file, "exact_ingest_run_required": True,
                     "physical_container_scope": "single_source_row", "logical_identity_created": False}
            config_json = {"configuration_sha256": config, "source_file_sha256": item["source_file_sha256"],
                           "q2_artifact_sha256": Q2_SHA256, "declared_text_fields": item["declared_text_fields"],
                           "declared_relations": item["declared_relations"], "cross_family_merge": False}
            expected_runs.add((run, SOURCE_SNAPSHOT_ID, parser, canonical_json(scope), canonical_json(config_json), "complete", MIGRATION_APPLIED_AT[19]))
            expected_parent: set[tuple[Any, ...]] = set()
            q2_rows = conn.execute(f"SELECT occurrence_id,source_file_path,source_ordinal,source_pointer,raw_record_json,raw_record_sha256 FROM q2.{_q(family + '_row_occurrence')}").fetchall()
            for row in q2_rows:
                owners = conn.execute(f"SELECT field_name,field_pair_ordinal,raw_hash_text FROM q2.{_q(family + '_text_owner_occurrence')} WHERE row_occurrence_id=? ORDER BY field_pair_ordinal,field_name,owner_id", (row[0],)).fetchall()
                observations = [{"field": str(owner[0]), "pair_ordinal": int(owner[1]), "raw_value": str(owner[2])} for owner in owners]
                expected_parent.add((row[0], family, contract, run, SOURCE_SNAPSHOT_ID, parser, row[1],
                    _id(SOURCE_SNAPSHOT_ID, row[1], row[3]), row[3], row[2], row[4], row[5],
                    canonical_json(observations), "raw_text_fields_observed",
                    canonical_json({"coverage": Q2_COVERAGE, "declared_text_fields_processed": True,
                                    "declared_relations_processed": True, "logical_identity_created": False})))
            _assert_extra(conn, family + "_row_occurrence", expected_parent)
            counts: dict[str, int] = {"raw_rows": len(expected_parent)}
            comparisons = {
                "text_owner_occurrence": "owner_id,row_occurrence_id,field_name,field_pair_ordinal,raw_hash_text,resolution_status,candidate_count",
                "text_resolution_occurrence": "resolution_id,owner_id,locale,resolution_status,candidate_count",
                "text_candidate_occurrence": "candidate_id,owner_id,locale,shard_tier,relative_path,pair_ordinal,pointer,raw_key_json,raw_text_json,raw_text,raw_key_sha256,raw_text_sha256,raw_record_sha256",
            }
            for suffix, selected in comparisons.items():
                table = family + "_" + suffix; key = {"text_owner_occurrence":"text_owners","text_resolution_occurrence":"text_resolutions","text_candidate_occurrence":"text_candidates"}[suffix]
                counts[key] = conn.execute(f"SELECT COUNT(*) FROM main.{_q(table)}").fetchone()[0]
                if counts[key] != item["counts"][key]: raise R1VerificationError(f"total table count mismatch: {table}")
                missing = conn.execute(f"SELECT {selected} FROM q2.{_q(table)} EXCEPT SELECT {selected} FROM main.{_q(table)} WHERE ingest_run_id=?", (run,)).fetchone()
                extra = conn.execute(f"SELECT {selected} FROM main.{_q(table)} WHERE ingest_run_id=? EXCEPT SELECT {selected} FROM q2.{_q(table)}", (run,)).fetchone()
                if missing or extra: raise R1VerificationError(f"exact Q2 projection mismatch: {table}")
            if counts["text_resolutions"] != counts["text_owners"] * 3: raise R1VerificationError(f"locale cardinality mismatch: {family}")
            tables = [family + suffix for suffix in ("_row_occurrence", "_text_owner_occurrence", "_text_resolution_occurrence", "_text_candidate_occurrence")]
            family_digests[family] = [(table, len(rows := _ordered_rows(conn, table, run)), _rowset_sha(rows)) for table in tables]
            family_results.append({"family": family, "ingest_run_id": run, "counts": counts})

        _assert_extra(conn, "schema_migration", {(18, MIGRATION_APPLIED_AT[18]), (19, MIGRATION_APPLIED_AT[19])})
        _assert_extra(conn, "parser_build", expected_parsers); _assert_extra(conn, "ingest_run", expected_runs)
        wave_id = "batch-wave:r1-schema19:" + _id(R1_CONTRACT, Q2_MANIFEST_SHA256, *Q2_DEFINITIONS)[:40]
        wave_metadata = canonical_json({"coverage_level": R1_COVERAGE, "corpus_complete": False, "product_accepted": False,
                                        "family_order": list(Q2_DEFINITIONS), "logical_identity_created": False, "cross_family_merge": False})
        _assert_extra(conn, "batch_wave", {(wave_id, SOURCE_SNAPSHOT_ID, R1_CONTRACT, Q2_MANIFEST_SHA256, 19, "complete", wave_metadata)})
        expected_shards: set[tuple[Any, ...]] = set(); expected_imports: set[tuple[Any, ...]] = set()
        expected_table_digests: set[tuple[Any, ...]] = set(); expected_chunks: set[tuple[Any, ...]] = set()
        for family in Q2_DEFINITIONS:
            item, digests = _item(manifest, family), family_digests[family]; run = item["ingest_run_id"]
            tables = [value[0] for value in digests]; combined = hashlib.sha256(canonical_json(digests).encode()).hexdigest()
            shard_id = "batch-shard:r1:" + _id(wave_id, family, run, combined)[:40]
            expected_shards.add((shard_id, wave_id, family, item["family_contract_version"], run, Q2_MANIFEST_SHA256, 1,
                canonical_json(["q2_sealed_artifact"]), canonical_json(tables), combined, "imported",
                canonical_json({"q2_artifact_sha256": Q2_SHA256, "logical_identity_created": False})))
            expected_imports.add(("batch-shard-import:r1:" + _id(shard_id, Q2_SHA256)[:40], wave_id, shard_id, 19,
                Q2_SHA256, sum(value[1] for value in digests), "complete",
                canonical_json({"source": "sealed_q2_projection", "family": family})))
            for table, count, digest in digests:
                digest_id = "batch-table-digest:r1:" + _id(shard_id, table, digest)[:40]
                sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
                ordering = _table_order(table); rows = _ordered_rows(conn, table, run)
                chunks = [rows[start:start + CHUNK_SIZE] for start in range(0, len(rows), CHUNK_SIZE)]
                expected_table_digests.add((digest_id, wave_id, shard_id, table, hashlib.sha256(str(sql).encode()).hexdigest(),
                    canonical_json(list(ordering)), count, digest, CHUNK_SIZE, len(chunks),
                    canonical_json({"run_scope": run, "digest_contract": "canonical-json-row-v1"})))
                columns = _columns(conn, "main", table); indexes = tuple(columns.index(value) for value in ordering)
                for ordinal, chunk in enumerate(chunks):
                    expected_chunks.add(("batch-chunk-digest:r1:" + _id(digest_id, ordinal)[:40], digest_id, ordinal,
                        canonical_json([chunk[0][index] for index in indexes]), canonical_json([chunk[-1][index] for index in indexes]),
                        len(chunk), _rowset_sha(tuple(chunk))))
        _assert_extra(conn, "batch_shard", expected_shards); _assert_extra(conn, "batch_shard_import", expected_imports)
        _assert_extra(conn, "batch_table_digest", expected_table_digests); _assert_extra(conn, "batch_table_chunk_digest", expected_chunks)
        size = target.stat().st_size; increment = size - SCHEMA17_SIZE
        if increment > 4 * 1024**3: raise R1VerificationError("candidate increment exceeds 4 GiB")
        return {"status":"verified","schema_version":19,"coverage_level":R1_COVERAGE,"product_accepted":False,
                "schema17_accepted_unchanged":True,"candidate":{"path":str(target),"size_bytes":size,"sha256":_sha(target),
                "increment_from_schema17_bytes":increment},"families":family_results,"top_level_tool_count":25,
                "top_level_tool_registry_changed":False,"exact_additive_closure":True,"digest_ledger_recomputed":True}
    except sqlite3.Error as exc:
        raise R1VerificationError(f"SQLite verification failed: {exc}") from exc
    finally:
        conn.close()


__all__ = ["R1VerificationError", "verify_candidate"]
