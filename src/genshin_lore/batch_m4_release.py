"""Batch M4 wave-level deterministic merge and release verification.

The M4 builder consumes the already verified object-container TextMap
candidate and independently verified M3 family shards. It copies the TextMap
candidate once, applies the structure-wave migration once, and bulk loads
family tables in a fixed order. No shared entity or relation model is created;
each family table remains a physical occurrence surface.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Mapping, Sequence

from .batch_m1_textmap import (
    MIGRATION_0015_APPLIED_AT,
    SNAPSHOT_ID as M1_SNAPSHOT_ID,
    SOURCE_COMMIT as M1_SOURCE_COMMIT,
    SOURCE_TREE as M1_SOURCE_TREE,
)
from .batch_m1_verifier import BatchM1VerificationError, verify_candidate as verify_m1_candidate
from .batch_m2_verifier import M2VerificationError, verify_shard
from .batch_m3_r1_projection import R1ProjectionError, load_specs, verify_wave
from .batch_release import BatchReleaseError, assert_output_paths, create_private_staging, fsync_file, publish_absent
from .batch_space import ProjectSpaceGate, SpaceGateError, ensure_owned_tmp_root, new_invocation_id
from .db import Database
from .family_projection_protocol import FamilyShardSpec, TableSpec, canonical_row, rowset_digest, schema_digest, spec_digest
from .util import canonical_json, digest_id, sha256_bytes, sha256_text


class M4ReleaseError(RuntimeError):
    """M4 input, merge, or independent release verification failure."""


SOURCE_COMMIT = M1_SOURCE_COMMIT
SOURCE_TREE = M1_SOURCE_TREE
SNAPSHOT_ID = M1_SNAPSHOT_ID
SCHEMA_VERSION = 16
MIGRATION_0016_APPLIED_AT = "2026-09-03T00:00:00+00:00"
MIGRATION_0016_SHA256 = "60ce010a3c596af5e82625b79e5e11e6f75018ea275e94d0eae7cadbb047c79f"
M4_WAVE_CONTRACT = "batch-wave-v3"
M4_REPORT_VERSION = "batch-m4-wave-release-v1"
# A freshly merged wave is still a non-product candidate. Acceptance is an
# external release step (tracked oracle + receipts + runtime/stdio gate), so
# the database metadata must never describe the candidate as an accepted
# product before that step has completed.
M4_COVERAGE = "R2_wave_candidate"
M4_ACCEPTED_COVERAGE = "R2_wave_accepted_product"
M4_OWNER = "batch-m4-wave-release"
M4_FAMILY_ORDER = (
    "achievement_archive",
    "activity_archive",
    "avatar_config_archive",
    "dungeon_archive",
    "material_config_archive",
    "monster_archive",
    "npc_archive",
    "reliquary_config_archive",
    "scene_archive",
    "weapon_config_archive",
)
M1_CANDIDATE_SIZE = 4_975_841_280
M1_CANDIDATE_SHA256 = "fd6052e4e8d1714c6b4574b4ed2be90ce63acaa34eac01c7b830843a7019840e"
SCHEMA13_SIZE = 1_723_596_800
SCHEMA13_SHA256 = "7ff8163b6007a41b0827af05fbcf996353617c52f354a131431da912712e58be"
CANDIDATE_INCREMENT_LIMIT_BYTES = 4 * 1024**3
STAGING_HEADROOM_BYTES = 512 * 1024**2
CHUNK_SIZE = 50_000


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical(value: Any) -> str:
    return canonical_json(value)


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro&immutable=1"


def _table_rows(conn: sqlite3.Connection, table: TableSpec) -> tuple[tuple[Any, ...], ...]:
    columns = ",".join(_q(column) for column in table.columns)
    rows = [tuple(row) for row in conn.execute(f"SELECT {columns} FROM {_q(table.name)}")]
    key = table.sort_key or (lambda row: tuple(row))
    return tuple(sorted(rows, key=lambda row: (key(row), canonical_row(row))))


def _chunk_digests(
    rows: Sequence[Sequence[Any]], key_indexes: Sequence[int],
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for ordinal, start in enumerate(range(0, len(rows), CHUNK_SIZE)):
        chunk = rows[start : start + CHUNK_SIZE]
        chunks.append({
            "chunk_ordinal": ordinal,
            "first_key": [chunk[0][index] for index in key_indexes],
            "last_key": [chunk[-1][index] for index in key_indexes],
            "row_count": len(chunk),
            "logical_row_sha256": rowset_digest(chunk),
        })
    return chunks


def _table_digest(table: TableSpec, rows: Sequence[Sequence[Any]]) -> dict[str, Any]:
    ordering = tuple(table.order_columns or table.columns)
    key_indexes = tuple(table.columns.index(column) for column in ordering)
    return {
        "table_name": table.name,
        "schema_sql_sha256": schema_digest(table.create_sql),
        "ordering": list(ordering),
        "row_count": len(rows),
        "logical_row_sha256": rowset_digest(rows),
        "chunk_size": CHUNK_SIZE,
        "chunk_count": (len(rows) + CHUNK_SIZE - 1) // CHUNK_SIZE,
        "chunks": _chunk_digests(rows, key_indexes),
    }


def _load_json(path: str | Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise M4ReleaseError(f"{label} is unavailable or invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise M4ReleaseError(f"{label} must be a JSON object")
    return value, raw


def _m4_wave_id(handoff_sha256: str) -> str:
    return digest_id("batch-m4-wave", SNAPSHOT_ID, handoff_sha256, ",".join(M4_FAMILY_ORDER))


def _family_ids(spec: FamilyShardSpec, wave_id: str) -> dict[str, str]:
    shard_id = digest_id("batch-m4-shard", wave_id, spec.family, spec.family_contract_version, spec.ingest_run_id, spec_digest(spec))
    table_digest_id = digest_id("batch-m4-table", shard_id, spec.table_names[0])
    import_id = digest_id("batch-m4-import", shard_id)
    return {"shard_id": shard_id, "table_digest_id": table_digest_id, "import_id": import_id}


def _validate_m1_input(m1_candidate: Path, m1_manifest: Path, m1_shard: Path, schema13_baseline: Path, source_root: Path) -> dict[str, Any]:
    if not m1_candidate.is_file() or m1_candidate.is_symlink():
        raise M4ReleaseError(f"M1 candidate is unavailable: {m1_candidate}")
    if m1_candidate.stat().st_size != M1_CANDIDATE_SIZE or _sha256_file(m1_candidate) != M1_CANDIDATE_SHA256:
        raise M4ReleaseError("M1 candidate is not the retained object-container v2 artifact")
    try:
        return verify_m1_candidate(m1_candidate, m1_manifest, source_root, m1_shard, schema13_baseline)
    except (BatchM1VerificationError, OSError, sqlite3.Error, KeyError, ValueError) as exc:
        raise M4ReleaseError(f"M1 candidate independent verification failed: {exc}") from exc


def _validate_m3_input(handoff_path: Path, source_root: Path) -> tuple[dict[str, Any], bytes, tuple[FamilyShardSpec, ...], dict[str, Any]]:
    handoff, raw = _load_json(handoff_path, "M3 R1 handoff")
    if handoff.get("report_version") != "batch-m3-r1-family-projections-v1":
        raise M4ReleaseError("unsupported M3 R1 handoff version")
    raw_families = handoff.get("families")
    if not isinstance(raw_families, list):
        raise M4ReleaseError("M3 R1 handoff family list is invalid")
    names = tuple(item.get("family") for item in raw_families if isinstance(item, Mapping))
    if names != M4_FAMILY_ORDER:
        raise M4ReleaseError("M3 family order/boundary is not the fixed M4 wave")
    if handoff.get("product_accepted") is not False or handoff.get("coverage_level") != "R1_structured_family":
        raise M4ReleaseError("M3 handoff is not a non-product R1 wave")
    try:
        verification = verify_wave(handoff, source_root)
        specs = load_specs(source_root, names)
    except (R1ProjectionError, M2VerificationError, OSError, ValueError) as exc:
        raise M4ReleaseError(f"M3 handoff independent verification failed: {exc}") from exc
    if verification.get("projection_family_count") != len(M4_FAMILY_ORDER):
        raise M4ReleaseError("M3 handoff family count mismatch")
    return handoff, raw, specs, verification


def _insert_provenance(conn: sqlite3.Connection, spec: FamilyShardSpec) -> None:
    parser_version = "batch-m3-r1-family-projection-v1"
    mapping_version = "family-specific-lexical-row-projection-v1"
    fingerprint = sha256_text(f"{parser_version}\n{mapping_version}\n{spec.family_contract_version}")
    config_checksum = spec_digest(spec)
    conn.execute(
        "INSERT INTO parser_build(id,parser_version,mapping_version,fingerprint,config_checksum,created_at) VALUES (?,?,?,?,?,?)",
        (spec.parser_build_id, parser_version, mapping_version, fingerprint, config_checksum, MIGRATION_0016_APPLIED_AT),
    )
    scope = {
        "family": spec.family,
        "provider_family": spec.family,
        "family_contract_version": spec.family_contract_version,
        "snapshot_id": spec.snapshot_id,
        "source_file": spec.metadata["source_file"],
        "exact_ingest_run_required": True,
        "logical_identity_created": False,
    }
    config = {
        "spec_digest": config_checksum,
        "source_file_sha256": spec.metadata["source_file_sha256"],
        "projection_kind": spec.metadata["projection_kind"],
        "cross_family_merge": False,
        "textmap_or_asset_projection": False,
    }
    conn.execute(
        "INSERT INTO ingest_run(id,snapshot_id,parser_build_id,scope_json,config_json,status,created_at) VALUES (?,?,?,?,?,?,?)",
        (spec.ingest_run_id, spec.snapshot_id, spec.parser_build_id, _canonical(scope), _canonical(config), "complete", MIGRATION_0016_APPLIED_AT),
    )


def _insert_wave_metadata(conn: sqlite3.Connection, wave_id: str, handoff_sha256: str, specs: Sequence[FamilyShardSpec], shard_summaries: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    conn.execute(
        "INSERT INTO batch_wave(id,snapshot_id,contract_version,manifest_sha256,target_schema_version,status,metadata_json) VALUES (?,?,?,?,?,?,?)",
        (wave_id, SNAPSHOT_ID, M4_WAVE_CONTRACT, handoff_sha256, SCHEMA_VERSION, "complete", _canonical({
            "coverage_level": M4_COVERAGE, "corpus_complete": False, "product_accepted": False,
            "family_order": list(M4_FAMILY_ORDER), "r0_record_scope_complete": False,
            "logical_identity_created": False, "cross_family_merge": False,
        })),
    )
    result: dict[str, Any] = {"wave_id": wave_id, "families": []}
    for spec in specs:
        summary = shard_summaries[spec.family]
        ids = _family_ids(spec, wave_id)
        table = spec.tables[0]
        rows = table.ordered_rows()
        digest = _table_digest(table, rows)
        conn.execute(
            "INSERT INTO batch_shard(id,wave_id,family,family_contract_version,ingest_run_id,manifest_sha256,shard_schema_version,read_set_json,write_set_json,rowset_sha256,status,metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ids["shard_id"], wave_id, spec.family, spec.family_contract_version, spec.ingest_run_id,
             handoff_sha256, spec.shard_schema_version, _canonical(list(spec.read_set)), _canonical(list(spec.write_set)),
             digest["logical_row_sha256"], "imported", _canonical({
                 "spec_digest": spec_digest(spec), "source_file": spec.metadata["source_file"],
                 "source_file_sha256": spec.metadata["source_file_sha256"], "projection_kind": spec.metadata["projection_kind"],
                 "logical_identity_created": False, "cross_family_merge": False,
             }))
        )
        conn.execute(
            "INSERT INTO batch_shard_import(id,wave_id,shard_id,target_schema_version,shard_file_sha256,imported_row_count,status,metadata_json) VALUES (?,?,?,?,?,?,?,?)",
            (ids["import_id"], wave_id, ids["shard_id"], SCHEMA_VERSION, summary["sha256"], digest["row_count"], "complete", _canonical({"family": spec.family, "spec_digest": spec_digest(spec)})),
        )
        conn.execute(
            "INSERT INTO batch_table_digest(id,wave_id,shard_id,table_name,schema_sql_sha256,ordering_json,row_count,logical_row_sha256,chunk_size,chunk_count,metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ids["table_digest_id"], wave_id, ids["shard_id"], table.name, digest["schema_sql_sha256"], _canonical(digest["ordering"]), digest["row_count"], digest["logical_row_sha256"], CHUNK_SIZE, digest["chunk_count"], _canonical({"digest_contract": "family-rowset-canonical-v1", "spec_digest": spec_digest(spec)})),
        )
        conn.executemany(
            "INSERT INTO batch_table_chunk_digest(id,table_digest_id,chunk_ordinal,first_key_json,last_key_json,row_count,logical_row_sha256) VALUES (?,?,?,?,?,?,?)",
            [(digest_id("batch-m4-chunk", ids["table_digest_id"], str(chunk["chunk_ordinal"])), ids["table_digest_id"], chunk["chunk_ordinal"], _canonical(chunk["first_key"]), _canonical(chunk["last_key"]), chunk["row_count"], chunk["logical_row_sha256"]) for chunk in digest["chunks"]],
        )
        result["families"].append({
            "family": spec.family, "family_contract_version": spec.family_contract_version,
            "ingest_run_id": spec.ingest_run_id, "parser_build_id": spec.parser_build_id,
            "spec_digest": spec_digest(spec), "table": digest,
            "shard": {"id": ids["shard_id"], "path": summary["path"], "sha256": summary["sha256"], "size_bytes": summary["size_bytes"]},
        })
    return result


def build_candidate(m1_candidate: str | Path, m1_manifest: str | Path, m1_shard: str | Path, schema13_baseline: str | Path, m3_handoff: str | Path, source_root: str | Path, output_path: str | Path, *, repo_root: str | Path | None = None, invocation_id: str | None = None) -> dict[str, Any]:
    """Create one deterministic Schema 16 M4 candidate from exact inputs."""
    m1 = Path(m1_candidate).absolute(); m1_manifest_path = Path(m1_manifest).absolute(); m1_shard_path = Path(m1_shard).absolute()
    baseline13 = Path(schema13_baseline).absolute(); handoff_path = Path(m3_handoff).absolute(); source = Path(source_root).absolute(); target = Path(output_path).absolute()
    repo = Path(repo_root).absolute() if repo_root is not None else Path(__file__).resolve().parents[2]
    if target.exists() or target.is_symlink():
        raise M4ReleaseError(f"M4 target must be absent: {target}")
    m1_result = _validate_m1_input(m1, m1_manifest_path, m1_shard_path, baseline13, source)
    handoff, handoff_raw, specs, m3_result = _validate_m3_input(handoff_path, source)
    handoff_sha256 = sha256_bytes(handoff_raw); wave_id = _m4_wave_id(handoff_sha256); invocation = invocation_id or new_invocation_id()
    try:
        owned_root = ensure_owned_tmp_root(target.parent, owner=M4_OWNER, invocation_id=invocation)
    except SpaceGateError as exc:
        raise M4ReleaseError(str(exc)) from exc
    projected = m1.stat().st_size + sum(Path(item["artifact"]["path"]).stat().st_size for item in handoff["families"]) + STAGING_HEADROOM_BYTES
    try:
        gate = ProjectSpaceGate(repo, owned_tmp_roots=(owned_root,), accounting_tmp_roots=(owned_root,), cleanup_tmp_roots=(owned_root,), owner=M4_OWNER, invocation_id=invocation, discovery_root="/tmp")
        gate.check("m4-wave-candidate", projected_bytes=projected)
    except (SpaceGateError, OSError) as exc:
        raise M4ReleaseError(str(exc)) from exc
    semantic_inputs = [("m1_candidate", m1), ("m1_manifest", m1_manifest_path), ("m1_shard", m1_shard_path), ("schema13_baseline", baseline13), ("m3_handoff", handoff_path)]
    staging: Path | None = None; metadata: dict[str, Any]
    try:
        assert_output_paths(target, inputs=semantic_inputs, target_must_be_absent=True)
        fd, staging = create_private_staging(target, prefix=".m4-wave-"); os.close(fd); shutil.copyfile(m1, staging)
        with Database(staging) as database:
            database.initialize_through(SCHEMA_VERSION)
            database.conn.execute("UPDATE schema_migration SET applied_at=? WHERE version=?", (MIGRATION_0016_APPLIED_AT, SCHEMA_VERSION)); database.conn.commit()
        connection = sqlite3.connect(staging); connection.execute("PRAGMA journal_mode=DELETE"); connection.execute("PRAGMA synchronous=FULL"); connection.execute("PRAGMA foreign_keys=ON")
        try:
            connection.execute("BEGIN")
            shard_summaries: dict[str, Mapping[str, Any]] = {}
            for spec, family_item in zip(specs, handoff["families"], strict=True):
                shard_summaries[spec.family] = verify_shard(Path(family_item["artifact"]["path"]).absolute(), spec); _insert_provenance(connection, spec)
            attached_aliases: list[str] = []
            for ordinal, spec in enumerate(specs):
                alias = f"m4_shard_{ordinal}"; shard_path = Path(handoff["families"][ordinal]["artifact"]["path"]).absolute(); connection.execute(f"ATTACH DATABASE ? AS {_q(alias)}", (_uri(shard_path),)); attached_aliases.append(alias); table = spec.tables[0]; columns = ",".join(_q(column) for column in table.columns); order_columns = ",".join(_q(column) for column in (table.order_columns or table.columns)); cursor = connection.cursor(); cursor.execute(f"INSERT INTO main.{_q(table.name)} ({columns}) SELECT {columns} FROM {_q(alias)}.{_q(table.name)} ORDER BY {order_columns}"); cursor.close()
            metadata = _insert_wave_metadata(connection, wave_id, handoff_sha256, specs, shard_summaries); connection.commit()
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations: raise M4ReleaseError(f"M4 candidate foreign-key check failed: {violations[:3]}")
            for alias in reversed(attached_aliases): connection.execute(f"DETACH DATABASE {_q(alias)}")
        except BaseException:
            connection.rollback(); raise
        finally: connection.close()
        increment = staging.stat().st_size - baseline13.stat().st_size
        if increment > CANDIDATE_INCREMENT_LIMIT_BYTES: raise M4ReleaseError(f"M4 candidate increment exceeds 4 GiB: {increment} > {CANDIDATE_INCREMENT_LIMIT_BYTES}")
        fsync_file(staging); publish_absent(target, staging, inputs=semantic_inputs); staging = None
    except (OSError, sqlite3.Error, BatchReleaseError, M2VerificationError, M4ReleaseError) as exc:
        if isinstance(exc, M4ReleaseError): raise
        raise M4ReleaseError(f"M4 candidate build failed: {exc}") from exc
    finally:
        if staging is not None:
            for sidecar in (Path(f"{staging}-journal"), Path(f"{staging}-wal"), Path(f"{staging}-shm")):
                try: sidecar.unlink()
                except FileNotFoundError: pass
            try: staging.unlink()
            except FileNotFoundError: pass
    return {
        "report_version": M4_REPORT_VERSION, "milestone": "M4", "status": "pending_sol_review", "coverage_level": M4_COVERAGE,
        "corpus_complete": False, "product_accepted": False, "schema_version": SCHEMA_VERSION,
        "source": {"snapshot_id": SNAPSHOT_ID, "commit": SOURCE_COMMIT, "tree": SOURCE_TREE}, "wave_id": wave_id, "wave_manifest_sha256": handoff_sha256,
        "families": metadata["families"], "m1_input": {"path": str(m1), "sha256": _sha256_file(m1), "size_bytes": m1.stat().st_size, "verification": m1_result},
        "m3_input": {"path": str(handoff_path), "sha256": handoff_sha256, "verification": m3_result},
        "candidate": {"path": str(target), "sha256": _sha256_file(target), "size_bytes": target.stat().st_size, "increment_from_schema13_bytes": target.stat().st_size - baseline13.stat().st_size},
        "identity_policy": "raw_evidence_identity_is_not_logical_identity; no cross-family merge",
        "textmap_policy": "six object-container source_records and 2,474,525 independent pair occurrences are preserved in the M1 input",
        "mcp_tool_count": 25,
        "family_provider_registry_changed": True,
        "top_level_tool_registry_changed": False,
        "top_level_tool_count": 25,
        "schema13_accepted_unchanged": True,
        "runtime_provider_status": "not_started", "space": gate.report(),
    }


def _rowset_difference(conn: sqlite3.Connection, left: str, right: str, table: str, columns: Sequence[str]) -> bool:
    names = ",".join(_q(column) for column in columns)
    return conn.execute(f"SELECT 1 FROM (SELECT {names} FROM {_q(left)}.{_q(table)} EXCEPT SELECT {names} FROM {_q(right)}.{_q(table)}) LIMIT 1").fetchone() is not None


def _table_columns(conn: sqlite3.Connection, schema: str, table: str) -> tuple[str, ...]:
    return tuple(str(row[1]) for row in conn.execute(f"PRAGMA {_q(schema)}.table_info({_q(table)})"))


def _added_ids(conn: sqlite3.Connection, table: str) -> set[str]:
    key_column = "version" if table == "schema_migration" else "id"
    return {str(row[0]) for row in conn.execute(
        f"SELECT {_q(key_column)} FROM main.{_q(table)} EXCEPT SELECT {_q(key_column)} FROM m1.{_q(table)}"
    )}


def _expected_append_ids(
    specs: Sequence[FamilyShardSpec], wave_id: str,
) -> dict[str, set[str]]:
    expected: dict[str, set[str]] = {
        "schema_migration": {"16"},
        "parser_build": {spec.parser_build_id for spec in specs},
        "ingest_run": {spec.ingest_run_id for spec in specs},
        "batch_wave": {wave_id},
        "batch_shard": set(),
        "batch_shard_import": set(),
        "batch_table_digest": set(),
        "batch_table_chunk_digest": set(),
    }
    for spec in specs:
        ids = _family_ids(spec, wave_id)
        expected["batch_shard"].add(ids["shard_id"])
        expected["batch_shard_import"].add(ids["import_id"])
        expected["batch_table_digest"].add(ids["table_digest_id"])
        digest = _table_digest(spec.tables[0], spec.tables[0].ordered_rows())
        expected["batch_table_chunk_digest"].update(
            digest_id("batch-m4-chunk", ids["table_digest_id"], str(chunk["chunk_ordinal"]))
            for chunk in digest["chunks"]
        )
    return expected


def verify_candidate(candidate: str | Path, m1_candidate: str | Path, m1_manifest: str | Path, m1_shard: str | Path, schema13_baseline: str | Path, m3_handoff: str | Path, source_root: str | Path) -> dict[str, Any]:
    """Independently verify the M4 candidate and exact family rowsets."""
    target = Path(candidate).absolute(); m1 = Path(m1_candidate).absolute(); baseline13 = Path(schema13_baseline).absolute(); source = Path(source_root).absolute()
    if not target.is_file() or target.is_symlink(): raise M4ReleaseError(f"M4 candidate is unavailable: {target}")
    if baseline13.stat().st_size != SCHEMA13_SIZE or _sha256_file(baseline13) != SCHEMA13_SHA256: raise M4ReleaseError("accepted Schema 13 baseline seal mismatch")
    increment = target.stat().st_size - baseline13.stat().st_size
    if increment > CANDIDATE_INCREMENT_LIMIT_BYTES: raise M4ReleaseError("M4 candidate increment exceeds the 4 GiB hard limit")
    m1_result = _validate_m1_input(m1, Path(m1_manifest).absolute(), Path(m1_shard).absolute(), baseline13, source)
    handoff, handoff_raw, specs, m3_result = _validate_m3_input(Path(m3_handoff).absolute(), source); handoff_sha256 = sha256_bytes(handoff_raw); wave_id = _m4_wave_id(handoff_sha256)
    connection = sqlite3.connect(f"file:{target.as_posix()}?mode=ro&immutable=1", uri=True); connection.row_factory = sqlite3.Row; connection.execute("PRAGMA query_only=ON")
    try:
        versions = tuple(int(row[0]) for row in connection.execute("SELECT version FROM schema_migration ORDER BY version"))
        if versions != tuple(range(1, SCHEMA_VERSION + 1)): raise M4ReleaseError(f"M4 migration ledger is not exact 1..{SCHEMA_VERSION}")
        ledger = {int(row[0]): str(row[1]) for row in connection.execute("SELECT version,applied_at FROM schema_migration WHERE version>=14")}
        if ledger != {14: "2026-09-01T00:00:00+00:00", 15: MIGRATION_0015_APPLIED_AT, 16: MIGRATION_0016_APPLIED_AT}: raise M4ReleaseError("M4 migration ledger timestamps are not pinned")
        tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}; expected_new = {spec.tables[0].name for spec in specs}
        with sqlite3.connect(_uri(m1), uri=True) as m1_conn: m1_tables = {str(row[0]) for row in m1_conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables != m1_tables | expected_new: raise M4ReleaseError("M4 candidate table inventory is not M1 plus exact family wave")
        wave = connection.execute("SELECT snapshot_id,contract_version,manifest_sha256,target_schema_version,status,metadata_json FROM batch_wave WHERE id=?", (wave_id,)).fetchone()
        if wave is None or tuple(wave[:5]) != (SNAPSHOT_ID, M4_WAVE_CONTRACT, handoff_sha256, SCHEMA_VERSION, "complete"): raise M4ReleaseError("M4 batch_wave binding mismatch")
        wave_metadata = json.loads(str(wave[5]))
        if (wave_metadata.get("family_order") != list(M4_FAMILY_ORDER)
                or wave_metadata.get("coverage_level") != M4_COVERAGE
                or wave_metadata.get("product_accepted") is not False
                or wave_metadata.get("logical_identity_created") is not False):
            raise M4ReleaseError("M4 wave metadata policy mismatch")
        append_tables = {"schema_migration", "parser_build", "ingest_run", "batch_wave", "batch_shard", "batch_shard_import", "batch_table_digest", "batch_table_chunk_digest"}
        connection.execute(f"ATTACH DATABASE ? AS m1", (_uri(m1),))
        for table in sorted(m1_tables - append_tables):
            columns = _table_columns(connection, "m1", table)
            if columns != _table_columns(connection, "main", table): raise M4ReleaseError(f"M1 table shape changed: {table}")
            if _rowset_difference(connection, "m1", "main", table, columns) or _rowset_difference(connection, "main", "m1", table, columns): raise M4ReleaseError(f"M1 protected table changed: {table}")
        for table in append_tables & m1_tables:
            columns = _table_columns(connection, "m1", table)
            if columns != _table_columns(connection, "main", table): raise M4ReleaseError(f"M1 append table shape changed: {table}")
            if _rowset_difference(connection, "m1", "main", table, columns): raise M4ReleaseError(f"M1 append table removed prior rows: {table}")
        expected_added = _expected_append_ids(specs, wave_id)
        for table, expected_ids in expected_added.items():
            if table not in m1_tables:
                raise M4ReleaseError(f"M1 append table is missing: {table}")
            actual_ids = _added_ids(connection, table)
            if actual_ids != expected_ids:
                raise M4ReleaseError(f"M4 append allowlist mismatch for {table}: expected {sorted(expected_ids)}, got {sorted(actual_ids)}")
        connection.execute("DETACH DATABASE m1")
        family_results: list[dict[str, Any]] = []
        for spec in specs:
            family_item = next(item for item in handoff["families"] if item["family"] == spec.family); shard_path = Path(family_item["artifact"]["path"]).absolute(); shard_verification = verify_shard(shard_path, spec); table = spec.tables[0]; sql = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table.name,)).fetchone()
            if sql is None or schema_digest(str(sql[0])) != schema_digest(table.create_sql): raise M4ReleaseError(f"M4 family table schema mismatch: {spec.family}")
            rows = _table_rows(connection, table)
            if rows != table.ordered_rows(): raise M4ReleaseError(f"M4 family rowset mismatch: {spec.family}")
            family_results.append({"family": spec.family, "status": "verified", "row_count": len(rows), "rowset_sha256": rowset_digest(rows), "shard": shard_verification})
        fk = connection.execute("PRAGMA foreign_key_check").fetchall()
        if fk: raise M4ReleaseError(f"M4 candidate foreign-key check failed: {fk[:3]}")
        allowed_runs = {spec.ingest_run_id for spec in specs}
        for row in connection.execute("SELECT id FROM ingest_run"):
            if str(row[0]).startswith("ir-batch-m3-r1-family:") and str(row[0]) not in allowed_runs: raise M4ReleaseError("M4 candidate contains an unexpected M3 family run")
        return {"status": "verified", "schema_version": SCHEMA_VERSION, "candidate_path": str(target), "candidate_sha256": _sha256_file(target), "candidate_size_bytes": target.stat().st_size, "candidate_increment_bytes": increment, "wave_id": wave_id, "wave_manifest_sha256": handoff_sha256, "family_count": len(family_results), "families": family_results, "m1_verification": m1_result, "m3_verification": m3_result, "mcp_tool_count": 25,
            "family_provider_registry_changed": True,
            "top_level_tool_registry_changed": False,
            "top_level_tool_count": 25,
            "schema13_accepted_unchanged": True}
    except (sqlite3.Error, OSError, M2VerificationError, BatchM1VerificationError) as exc:
        if isinstance(exc, M4ReleaseError): raise
        raise M4ReleaseError(f"M4 candidate verification failed: {exc}") from exc
    finally: connection.close()


__all__ = ["CANDIDATE_INCREMENT_LIMIT_BYTES", "M4_ACCEPTED_COVERAGE", "M4_COVERAGE", "M4_REPORT_VERSION", "M4ReleaseError", "SCHEMA_VERSION", "build_candidate", "verify_candidate"]
