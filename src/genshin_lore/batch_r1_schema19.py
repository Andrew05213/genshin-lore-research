"""R1 Schema 19 deterministic non-product release candidate builder."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from .batch_m0_baseline import SOURCE_SNAPSHOT_ID
from .batch_q2_uplift import Q2_COVERAGE, Q2_DEFINITIONS, qualify_q2_artifact
from .batch_release import assert_output_paths, create_private_staging, fsync_file, publish_absent
from .batch_space import PROJECT_SPACE_LIMIT_BYTES, ProjectSpaceGate, SpaceGateError, ensure_owned_tmp_root
from .db import Database
from .util import canonical_json

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = 19
R1_CONTRACT = "batch-r1-schema19-wave01-text-release-candidate-v1"
R1_COVERAGE = "R1_structured_family_release_candidate"
R1_OWNER = "batch-r1-schema19"
R1_FAMILY_ORDER = tuple(Q2_DEFINITIONS)
SCHEMA17_SIZE = 5_244_338_176
SCHEMA17_SHA256 = "464a49e77b2ebf7fd7b0ae68d907fdd3307ef01e0d8537065f4b54ad4a53ddab"
Q2_SIZE = 90_963_968
Q2_SHA256 = "888f948ad379bfda3f4e7e071d4ad08b6a4760322cc1a40914b69f7ca06726a5"
Q2_MANIFEST_SHA256 = "09d6bb410851cc0ad1c804e6840e5fe6d0934e4648d575dcad38cd757b850801"
MIGRATION_APPLIED_AT = {18: "2026-09-07T00:00:00+00:00", 19: "2026-09-08T00:00:00+00:00"}
CANDIDATE_INCREMENT_LIMIT_BYTES = 4 * 1024**3
CHUNK_SIZE = 50_000


class R1ReleaseError(RuntimeError):
    """An R1 source, identity, merge, storage, or release contract failed."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _id(*parts: object) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
    return digest.hexdigest()


def _q(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro&immutable=1"


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    raw = manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != Q2_MANIFEST_SHA256:
        raise R1ReleaseError("Q2 canonical tracked manifest seal mismatch")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise R1ReleaseError("Q2 manifest must be an object")
    return value


def family_item(manifest: Mapping[str, Any], family: str) -> Mapping[str, Any]:
    item = next((value for value in manifest["families"] if value.get("family") == family), None)
    if not isinstance(item, Mapping):
        raise R1ReleaseError(f"Q2 family is absent: {family}")
    return item


def validate_inputs(baseline: Path, q2: Path, manifest_path: Path) -> dict[str, Any]:
    if not baseline.is_file() or baseline.is_symlink():
        raise R1ReleaseError("exact Schema 17 baseline is unavailable")
    if baseline.stat().st_size != SCHEMA17_SIZE or sha256_file(baseline) != SCHEMA17_SHA256:
        raise R1ReleaseError("exact Schema 17 baseline seal mismatch")
    if not q2.is_file() or q2.is_symlink():
        raise R1ReleaseError("Q2 artifact is unavailable")
    if q2.stat().st_size != Q2_SIZE or sha256_file(q2) != Q2_SHA256:
        raise R1ReleaseError("Q2 artifact seal mismatch")
    manifest = load_manifest(manifest_path)
    try:
        qualified = qualify_q2_artifact(q2, manifest_path)
    except Exception as exc:
        raise R1ReleaseError(f"Q2 qualification failed: {exc}") from exc
    for key in ("contract", "snapshot_id", "corpus_complete", "product_accepted", "families"):
        if qualified.get(key) != manifest.get(key):
            raise R1ReleaseError(f"Q2 qualification/manifest mismatch: {key}")
    return manifest


TABLE_ORDER_SUFFIX = {
    "_row_occurrence": ("ingest_run_id", "source_file_path", "source_ordinal", "occurrence_id"),
    "_text_owner_occurrence": ("ingest_run_id", "row_occurrence_id", "field_pair_ordinal", "owner_id"),
    "_text_resolution_occurrence": ("ingest_run_id", "owner_id", "locale", "resolution_id"),
    "_text_candidate_occurrence": ("ingest_run_id", "owner_id", "locale", "relative_path", "pair_ordinal", "candidate_id"),
}


def table_order(table: str) -> tuple[str, ...]:
    for suffix, ordering in TABLE_ORDER_SUFFIX.items():
        if table.endswith(suffix):
            return ordering
    raise R1ReleaseError(f"R1 table has no ordering contract: {table}")


def ordered_rows(conn: sqlite3.Connection, table: str, run: str) -> list[tuple[Any, ...]]:
    columns = tuple(str(row[1]) for row in conn.execute(f"PRAGMA table_info({_q(table)})"))
    selected = ",".join(_q(column) for column in columns)
    ordering = ",".join(_q(column) for column in table_order(table))
    return [tuple(row) for row in conn.execute(
        f"SELECT {selected} FROM {_q(table)} WHERE ingest_run_id=? ORDER BY {ordering}", (run,)
    )]


def rowset_sha(rows: list[tuple[Any, ...]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(canonical_json(list(row)).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _insert_family(conn: sqlite3.Connection, item: Mapping[str, Any]) -> dict[str, Any]:
    family, run = str(item["family"]), str(item["ingest_run_id"])
    parser, contract = str(item["parser_build_id"]), str(item["family_contract_version"])
    config, source_file = str(item["configuration_sha256"]), str(item["source_file"])
    created = MIGRATION_APPLIED_AT[19]
    conn.execute(
        "INSERT INTO parser_build(id,parser_version,mapping_version,fingerprint,config_checksum,created_at) VALUES(?,?,?,?,?,?)",
        (parser, "batch-q2-wave01-text-uplift-v1", contract, _id("parser", parser, config), config, created),
    )
    scope = {"family": family, "family_contract_version": contract, "snapshot_id": SOURCE_SNAPSHOT_ID,
             "source_file": source_file, "exact_ingest_run_required": True,
             "physical_container_scope": "single_source_row", "logical_identity_created": False}
    config_json = {"configuration_sha256": config, "source_file_sha256": item["source_file_sha256"],
                   "q2_artifact_sha256": Q2_SHA256, "declared_text_fields": item["declared_text_fields"],
                   "declared_relations": item["declared_relations"], "cross_family_merge": False}
    conn.execute(
        "INSERT INTO ingest_run(id,snapshot_id,parser_build_id,scope_json,config_json,status,created_at) VALUES(?,?,?,?,?,?,?)",
        (run, SOURCE_SNAPSHOT_ID, parser, canonical_json(scope), canonical_json(config_json), "complete", created),
    )
    q2_rows = conn.execute(
        f"SELECT occurrence_id,source_file_path,source_ordinal,source_pointer,raw_record_json,raw_record_sha256 "
        f"FROM q2.{_q(family + '_row_occurrence')} ORDER BY source_ordinal,occurrence_id"
    ).fetchall()
    values: list[tuple[Any, ...]] = []
    for row in q2_rows:
        owners = conn.execute(
            f"SELECT field_name,field_pair_ordinal,raw_hash_text FROM q2.{_q(family + '_text_owner_occurrence')} "
            "WHERE row_occurrence_id=? ORDER BY field_pair_ordinal,field_name,owner_id", (row[0],)
        ).fetchall()
        observations = [{"field": str(owner[0]), "pair_ordinal": int(owner[1]), "raw_value": str(owner[2])}
                        for owner in owners]
        values.append((row[0], family, contract, run, SOURCE_SNAPSHOT_ID, parser, row[1],
                       _id(SOURCE_SNAPSHOT_ID, row[1], row[3]),
                       row[3], row[2], row[4], row[5], canonical_json(observations),
                       "raw_text_fields_observed", canonical_json({"coverage": Q2_COVERAGE,
                       "declared_text_fields_processed": True, "declared_relations_processed": True,
                       "logical_identity_created": False})))
    conn.executemany(f"INSERT INTO {_q(family + '_row_occurrence')} VALUES({','.join('?' for _ in range(15))})", values)
    conn.execute(
        f"INSERT INTO {_q(family + '_text_owner_occurrence')} "
        "(owner_id,ingest_run_id,row_occurrence_id,field_name,field_pair_ordinal,raw_hash_text,resolution_status,candidate_count) "
        f"SELECT owner_id,?,row_occurrence_id,field_name,field_pair_ordinal,raw_hash_text,resolution_status,candidate_count "
        f"FROM q2.{_q(family + '_text_owner_occurrence')} ORDER BY row_occurrence_id,field_pair_ordinal,owner_id", (run,))
    conn.execute(
        f"INSERT INTO {_q(family + '_text_resolution_occurrence')} "
        "(resolution_id,ingest_run_id,owner_id,locale,resolution_status,candidate_count) "
        f"SELECT resolution_id,?,owner_id,locale,resolution_status,candidate_count "
        f"FROM q2.{_q(family + '_text_resolution_occurrence')} ORDER BY owner_id,locale,resolution_id", (run,))
    conn.execute(
        f"INSERT INTO {_q(family + '_text_candidate_occurrence')} "
        "(candidate_id,ingest_run_id,owner_id,locale,shard_tier,relative_path,pair_ordinal,pointer,raw_key_json,raw_text_json,raw_text,raw_key_sha256,raw_text_sha256,raw_record_sha256) "
        f"SELECT candidate_id,?,owner_id,locale,shard_tier,relative_path,pair_ordinal,pointer,raw_key_json,raw_text_json,raw_text,raw_key_sha256,raw_text_sha256,raw_record_sha256 "
        f"FROM q2.{_q(family + '_text_candidate_occurrence')} ORDER BY owner_id,locale,relative_path,pair_ordinal,candidate_id", (run,))
    counts = {"raw_rows": len(values)}
    for key, suffix in (("text_owners", "text_owner_occurrence"), ("text_resolutions", "text_resolution_occurrence"),
                        ("text_candidates", "text_candidate_occurrence")):
        counts[key] = conn.execute(f"SELECT COUNT(*) FROM {_q(family + '_' + suffix)} WHERE ingest_run_id=?", (run,)).fetchone()[0]
    for key, value in counts.items():
        if value != int(item["counts"][key]):
            raise R1ReleaseError(f"R1 imported count mismatch: {family}.{key}")
    return {"family": family, "family_contract_version": contract, "ingest_run_id": run,
            "parser_build_id": parser, "counts": counts}


def _insert_wave(conn: sqlite3.Connection, families: list[dict[str, Any]]) -> str:
    wave_id = "batch-wave:r1-schema19:" + _id(R1_CONTRACT, Q2_MANIFEST_SHA256, *R1_FAMILY_ORDER)[:40]
    conn.execute(
        "INSERT INTO batch_wave(id,snapshot_id,contract_version,manifest_sha256,target_schema_version,status,metadata_json) VALUES(?,?,?,?,?,?,?)",
        (wave_id, SOURCE_SNAPSHOT_ID, R1_CONTRACT, Q2_MANIFEST_SHA256, SCHEMA_VERSION, "complete",
         canonical_json({"coverage_level": R1_COVERAGE, "corpus_complete": False, "product_accepted": False,
                         "family_order": list(R1_FAMILY_ORDER), "logical_identity_created": False,
                         "cross_family_merge": False})))
    for item in families:
        family, run = item["family"], item["ingest_run_id"]
        tables = [family + suffix for suffix in ("_row_occurrence", "_text_owner_occurrence",
                                                  "_text_resolution_occurrence", "_text_candidate_occurrence")]
        digests = [(table, len(rows := ordered_rows(conn, table, run)), rowset_sha(rows)) for table in tables]
        combined = hashlib.sha256(canonical_json(digests).encode()).hexdigest()
        shard_id = "batch-shard:r1:" + _id(wave_id, family, run, combined)[:40]
        conn.execute(
            "INSERT INTO batch_shard(id,wave_id,family,family_contract_version,ingest_run_id,manifest_sha256,shard_schema_version,read_set_json,write_set_json,rowset_sha256,status,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (shard_id, wave_id, family, item["family_contract_version"], run, Q2_MANIFEST_SHA256, 1,
             canonical_json(["q2_sealed_artifact"]), canonical_json(tables), combined, "imported",
             canonical_json({"q2_artifact_sha256": Q2_SHA256, "logical_identity_created": False})))
        conn.execute(
            "INSERT INTO batch_shard_import(id,wave_id,shard_id,target_schema_version,shard_file_sha256,imported_row_count,status,metadata_json) VALUES(?,?,?,?,?,?,?,?)",
            ("batch-shard-import:r1:" + _id(shard_id, Q2_SHA256)[:40], wave_id, shard_id, SCHEMA_VERSION,
             Q2_SHA256, sum(value[1] for value in digests), "complete",
             canonical_json({"source": "sealed_q2_projection", "family": family})))
        for table, count, digest in digests:
            digest_id = "batch-table-digest:r1:" + _id(shard_id, table, digest)[:40]
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
            ordering = table_order(table)
            columns = tuple(str(row[1]) for row in conn.execute(f"PRAGMA table_info({_q(table)})"))
            key_indexes = tuple(columns.index(column) for column in ordering)
            rows = ordered_rows(conn, table, run)
            chunks = [rows[start:start + CHUNK_SIZE] for start in range(0, len(rows), CHUNK_SIZE)]
            conn.execute(
                "INSERT INTO batch_table_digest(id,wave_id,shard_id,table_name,schema_sql_sha256,ordering_json,row_count,logical_row_sha256,chunk_size,chunk_count,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (digest_id, wave_id, shard_id, table, hashlib.sha256(str(sql).encode()).hexdigest(),
                 canonical_json(list(ordering)), count, digest, CHUNK_SIZE, len(chunks),
                 canonical_json({"run_scope": run, "digest_contract": "canonical-json-row-v1"})))
            for ordinal, chunk in enumerate(chunks):
                chunk_digest = rowset_sha(chunk)
                first_key = [chunk[0][index] for index in key_indexes]
                last_key = [chunk[-1][index] for index in key_indexes]
                conn.execute(
                    "INSERT INTO batch_table_chunk_digest(id,table_digest_id,chunk_ordinal,first_key_json,last_key_json,row_count,logical_row_sha256) VALUES(?,?,?,?,?,?,?)",
                    ("batch-chunk-digest:r1:" + _id(digest_id, ordinal)[:40], digest_id, ordinal,
                     canonical_json(first_key), canonical_json(last_key), len(chunk), chunk_digest))
    return wave_id


def build_candidate(baseline: str | Path, q2_artifact: str | Path, q2_manifest: str | Path,
                    output: str | Path, *, repo_root: str | Path = ROOT, invocation_id: str) -> dict[str, Any]:
    baseline_path, q2_path, manifest_path = (Path(value).resolve() for value in (baseline, q2_artifact, q2_manifest))
    target = Path(output).resolve()
    if target.exists() or target.is_symlink():
        raise R1ReleaseError("R1 output must be absent")
    manifest = validate_inputs(baseline_path, q2_path, manifest_path)
    try:
        owned = ensure_owned_tmp_root(target.parent, owner=R1_OWNER, invocation_id=invocation_id)
        gate = ProjectSpaceGate(Path(repo_root).resolve(), owned_tmp_roots=(owned,), accounting_tmp_roots=(owned,),
                                cleanup_tmp_roots=(owned,), owner=R1_OWNER, invocation_id=invocation_id,
                                discovery_root="/tmp")
        before = gate.check("r1-schema19-candidate", projected_bytes=SCHEMA17_SIZE)
    except SpaceGateError as exc:
        raise R1ReleaseError(str(exc)) from exc
    staging: Path | None = None
    inputs = (("schema17", baseline_path), ("q2", q2_path), ("manifest", manifest_path))
    try:
        assert_output_paths(target, inputs=inputs, target_must_be_absent=True)
        descriptor, staging = create_private_staging(target, prefix=".r1-schema19-")
        os.close(descriptor)
        shutil.copyfile(baseline_path, staging)
        with Database(staging) as database:
            database.initialize_through(SCHEMA_VERSION)
            for version, applied_at in MIGRATION_APPLIED_AT.items():
                database.conn.execute("UPDATE schema_migration SET applied_at=? WHERE version=?", (applied_at, version))
            database.conn.commit()
        conn = sqlite3.connect(staging)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("BEGIN")
            conn.execute("ATTACH DATABASE ? AS q2", (_uri(q2_path),))
            family_reports = [_insert_family(conn, family_item(manifest, family)) for family in R1_FAMILY_ORDER]
            wave_id = _insert_wave(conn, family_reports)
            conn.commit()
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise R1ReleaseError("R1 foreign-key check failed")
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise R1ReleaseError("R1 quick_check failed")
            conn.execute("DETACH DATABASE q2")
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
        increment = staging.stat().st_size - SCHEMA17_SIZE
        if increment > CANDIDATE_INCREMENT_LIMIT_BYTES:
            raise R1ReleaseError("R1 candidate increment exceeds 4 GiB")
        fsync_file(staging)
        publish_absent(target, staging, inputs=inputs)
        staging = None
    except Exception as exc:
        if isinstance(exc, R1ReleaseError):
            raise
        raise R1ReleaseError(f"R1 build failed: {exc}") from exc
    finally:
        if staging is not None:
            for suffix in ("", "-journal", "-wal", "-shm"):
                try: Path(str(staging) + suffix).unlink()
                except FileNotFoundError: pass
    candidate = {"path": str(target), "size_bytes": target.stat().st_size, "sha256": sha256_file(target),
                 "increment_from_schema17_bytes": target.stat().st_size - SCHEMA17_SIZE}
    return {"report_version": R1_CONTRACT, "milestone": "R1", "status": "candidate_complete",
            "schema_version": SCHEMA_VERSION, "coverage_level": R1_COVERAGE, "corpus_complete": False,
            "product_accepted": False, "schema17_accepted_unchanged": True, "wave_id": wave_id,
            "families": family_reports, "candidate": candidate, "top_level_tool_count": 25,
            "top_level_tool_registry_changed": False, "space_before_build": before.to_dict(),
            "space_limit_bytes": PROJECT_SPACE_LIMIT_BYTES}


__all__ = ["R1ReleaseError", "SCHEMA_VERSION", "R1_CONTRACT", "R1_COVERAGE", "R1_FAMILY_ORDER",
           "SCHEMA17_SIZE", "SCHEMA17_SHA256", "Q2_SHA256", "Q2_MANIFEST_SHA256", "build_candidate",
           "family_item", "load_manifest", "ordered_rows", "rowset_sha", "sha256_file", "validate_inputs"]
