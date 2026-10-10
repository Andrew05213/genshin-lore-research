"""Independent runtime oracle for the Batch M4 wave.

The release report and receipts are release-gate inputs only.  Runtime trust is
the tracked manifest plus an independent streaming comparison of Schema 16
row occurrences, parser/run provenance, wave metadata, and batch digest
ledgers.  No family projection code is imported here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ._readonly_database import ReadonlyDatabase
from .family_projection_protocol import canonical_row, schema_digest
from .util import canonical_json, digest_id, sha256_bytes

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_MANIFEST_PATH = ROOT / "manifests/batch/anime-game-data-7.0-batch-m4-wave01-runtime.json"
RUNTIME_MANIFEST_ID = "anime-game-data-7.0-batch-m4-wave01-runtime"
RUNTIME_MANIFEST_VERSION = "batch-m4-wave01-runtime-v1"
RUNTIME_SCHEMA_VERSION = 16
RUNTIME_SCHEMA_CONTRACT = "schema16-migration0016-additive-wave01-exact"
RUNTIME_COVERAGE = "R2_wave_accepted_product"
RUNTIME_WAVE_CONTRACT = "batch-wave-v3"
MIGRATION_0014_APPLIED_AT = "2026-09-01T00:00:00+00:00"
MIGRATION_0015_APPLIED_AT = "2026-09-02T00:00:00+00:00"
MIGRATION_0016_APPLIED_AT = "2026-09-03T00:00:00+00:00"
MIGRATION_0016_SHA256 = "60ce010a3c596af5e82625b79e5e11e6f75018ea275e94d0eae7cadbb047c79f"
SCHEMA16_STRUCTURAL_SIGNATURES = {
    "fts5": "219f51d46f8fb07b743bc51ac3d60abd79e8a8cb2684973066f5e74b1a249986",
    "fallback": "dad2660b32a847e6416cf1acee1f6fdb162c9388e27aa07b0467caf0e92a2c92",
}
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
M4_CANDIDATE_METADATA = {
    "coverage_level": "R2_wave_candidate",
    "corpus_complete": False,
    "product_accepted": False,
    "family_order": list(M4_FAMILY_ORDER),
    "r0_record_scope_complete": False,
    "logical_identity_created": False,
    "cross_family_merge": False,
}
M4_PROVIDER_REGISTRY_FLAGS = {
    "family_provider_registry_changed": True,
    "top_level_tool_registry_changed": False,
    "top_level_tool_count": 25,
}
FAMILY_TABLES = {family: f"{family}_row_occurrence" for family in M4_FAMILY_ORDER}
ROW_COLUMNS = (
    "occurrence_id",
    "family",
    "family_contract_version",
    "ingest_run_id",
    "snapshot_id",
    "parser_build_id",
    "source_file_path",
    "source_record_id",
    "source_pointer",
    "source_ordinal",
    "raw_record_json",
    "raw_record_sha256",
    "raw_key_observations_json",
    "record_status",
    "metadata_json",
)
ORDER_COLUMNS = (
    "source_file_path",
    "source_ordinal",
    "source_pointer",
    "occurrence_id",
)
PROVENANCE_TABLES = (
    "batch_wave",
    "batch_shard",
    "batch_shard_import",
    "batch_table_digest",
    "batch_table_chunk_digest",
)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PHYSICAL_SOURCE_POINTER_PATH = re.compile(
    r"^\$\.families\[\d+\]\.table\.chunks\[\d+\]\.(?:first_key|last_key)\[2\]$"
)


class BatchM4RuntimeOracleError(RuntimeError):
    """The tracked runtime contract does not match the candidate database."""


def _q(identifier: str) -> str:
    if not isinstance(identifier, str) or not _IDENTIFIER.fullmatch(identifier):
        raise BatchM4RuntimeOracleError(f"unsafe SQLite identifier: {identifier!r}")
    return '"' + identifier.replace('"', '""') + '"'


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise BatchM4RuntimeOracleError(f"candidate database is unavailable: {path}") from exc
    return digest.hexdigest()


def _strict_json(raw: bytes, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise BatchM4RuntimeOracleError(f"duplicate {label} key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
        )
    except BatchM4RuntimeOracleError:
        raise
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BatchM4RuntimeOracleError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise BatchM4RuntimeOracleError(f"{label} must be a JSON object")
    return value


def _without_self_seal(value: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(value)
    body.pop("manifest_sha256", None)
    body.pop("manifest_canonical_sha256", None)
    return body


def canonical_manifest_sha256(value: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json(_without_self_seal(value)).encode("utf-8"))


def _portable_scan(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _portable_scan(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _portable_scan(item, f"{path}[{index}]")
    elif isinstance(value, str):
        # ORDER_COLUMNS fixes source_pointer at index 2 in each chunk key.  It
        # is a physical JSON Pointer, not a filesystem path; exempt only this
        # exact generated-manifest position and keep all other path checks.
        if _PHYSICAL_SOURCE_POINTER_PATH.fullmatch(path):
            return
        if value.startswith("/") or value.startswith("~") or re.match(r"^[A-Za-z]:[\\/]", value):
            raise BatchM4RuntimeOracleError(f"runtime manifest contains an absolute path at {path}")
        if "/tmp/" in value or value.startswith("tmp/"):
            raise BatchM4RuntimeOracleError(f"runtime manifest contains a machine-local path at {path}")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise BatchM4RuntimeOracleError(f"{label} must be a non-empty string")
    return value


def _sha(value: Any, label: str) -> str:
    result = _text(value, label)
    if not _SHA256.fullmatch(result):
        raise BatchM4RuntimeOracleError(f"{label} must be a lowercase SHA-256")
    return result


def _json_value(raw: Any, label: str) -> Any:
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BatchM4RuntimeOracleError(f"{label} contains invalid JSON") from exc


def _same_json(actual_raw: Any, expected: Any, label: str) -> None:
    if _json_value(actual_raw, label) != expected:
        raise BatchM4RuntimeOracleError(f"{label} mismatch")


def _validate_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BatchM4RuntimeOracleError(f"{label} must be an object")
    return value


def load_runtime_manifest(path: str | Path = RUNTIME_MANIFEST_PATH) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    try:
        value = _strict_json(manifest_path.read_bytes(), "M4 runtime manifest")
    except OSError as exc:
        raise BatchM4RuntimeOracleError(f"M4 runtime manifest is unavailable: {manifest_path}") from exc
    _portable_scan(value)
    seal = value.get("manifest_sha256")
    if not isinstance(seal, str) or not _SHA256.fullmatch(seal):
        raise BatchM4RuntimeOracleError("M4 runtime manifest canonical seal is missing")
    if canonical_manifest_sha256(value) != seal:
        raise BatchM4RuntimeOracleError("M4 runtime manifest canonical SHA-256 mismatch")
    if value.get("manifest_id") != RUNTIME_MANIFEST_ID:
        raise BatchM4RuntimeOracleError("M4 runtime manifest id mismatch")
    if value.get("manifest_contract") != "batch-m4-runtime-business-oracle-v1":
        raise BatchM4RuntimeOracleError("M4 runtime manifest contract mismatch")
    if value.get("manifest_version") != RUNTIME_MANIFEST_VERSION:
        raise BatchM4RuntimeOracleError("M4 runtime manifest version mismatch")
    if value.get("schema_version") != RUNTIME_SCHEMA_VERSION:
        raise BatchM4RuntimeOracleError("M4 runtime manifest schema version mismatch")
    if value.get("schema_contract") != RUNTIME_SCHEMA_CONTRACT:
        raise BatchM4RuntimeOracleError("M4 runtime manifest Schema 16 contract mismatch")
    if value.get("snapshot_id") != value.get("source", {}).get("snapshot_id"):
        raise BatchM4RuntimeOracleError("M4 runtime manifest snapshot/source mismatch")
    if value.get("coverage_level") != RUNTIME_COVERAGE or value.get("corpus_complete") is not False:
        raise BatchM4RuntimeOracleError("M4 runtime manifest coverage contract mismatch")
    if value.get("product_accepted") is not True:
        raise BatchM4RuntimeOracleError("M4 runtime manifest is not product accepted")
    order = value.get("family_order")
    if tuple(order or ()) != M4_FAMILY_ORDER:
        raise BatchM4RuntimeOracleError("M4 runtime manifest family order mismatch")
    source = _validate_mapping(value.get("source"), "runtime manifest source")
    for key in ("snapshot_id", "commit", "tree"):
        _text(source.get(key), f"source.{key}")
    families = value.get("families")
    if not isinstance(families, list) or len(families) != len(M4_FAMILY_ORDER):
        raise BatchM4RuntimeOracleError("M4 runtime manifest family list mismatch")
    names = tuple(
        item.get("family") for item in families if isinstance(item, Mapping)
    )
    if names != M4_FAMILY_ORDER:
        raise BatchM4RuntimeOracleError("M4 runtime manifest family boundary mismatch")
    wave = _validate_mapping(value.get("wave"), "runtime manifest wave")
    _text(wave.get("id"), "wave.id")
    _text(wave.get("snapshot_id"), "wave.snapshot_id")
    _sha(wave.get("manifest_sha256"), "wave.manifest_sha256")
    if (
        wave.get("snapshot_id") != source["snapshot_id"]
        or wave.get("contract_version") != RUNTIME_WAVE_CONTRACT
        or wave.get("target_schema_version") != RUNTIME_SCHEMA_VERSION
        or wave.get("status") != "complete"
    ):
        raise BatchM4RuntimeOracleError("M4 runtime manifest wave contract mismatch")
    metadata = _validate_mapping(wave.get("metadata"), "wave.metadata")
    if dict(metadata) != M4_CANDIDATE_METADATA:
        raise BatchM4RuntimeOracleError("M4 runtime manifest candidate wave metadata mismatch")
    structural = _validate_mapping(value.get("structural_seal"), "runtime manifest structural seal")
    if structural.get("migration_0014_applied_at") != MIGRATION_0014_APPLIED_AT:
        raise BatchM4RuntimeOracleError("M4 migration 0014 seal mismatch")
    if structural.get("migration_0015_applied_at") != MIGRATION_0015_APPLIED_AT:
        raise BatchM4RuntimeOracleError("M4 migration 0015 seal mismatch")
    if structural.get("migration_0016_applied_at") != MIGRATION_0016_APPLIED_AT:
        raise BatchM4RuntimeOracleError("M4 migration 0016 timestamp seal mismatch")
    if structural.get("migration_0016_sha256") != MIGRATION_0016_SHA256:
        raise BatchM4RuntimeOracleError("M4 migration 0016 raw SQL seal mismatch")
    registry = _validate_mapping(value.get("provider_registry"), "runtime manifest provider_registry")
    if dict(registry) != M4_PROVIDER_REGISTRY_FLAGS:
        raise BatchM4RuntimeOracleError("M4 provider registry flags mismatch")
    provenance = value.get("provenance_tables")
    if not isinstance(provenance, list) or tuple(item.get("table") for item in provenance) != PROVENANCE_TABLES:
        raise BatchM4RuntimeOracleError("M4 runtime manifest provenance table boundary mismatch")
    for item in provenance:
        if not isinstance(item, Mapping):
            raise BatchM4RuntimeOracleError("M4 provenance entry is invalid")
        _text(item.get("table"), "provenance.table")
        if item.get("scope") != "m4_wave":
            raise BatchM4RuntimeOracleError("M4 provenance scope mismatch")
        _sha(item.get("sha256"), "provenance.sha256")
        if isinstance(item.get("row_count"), bool) or not isinstance(item.get("row_count"), int) or item.get("row_count") < 1:
            raise BatchM4RuntimeOracleError("M4 provenance row count is invalid")
        columns = item.get("columns")
        if not isinstance(columns, list) or not columns:
            raise BatchM4RuntimeOracleError("M4 provenance columns are invalid")
    return value


def _schema16_structural_seal(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        versions = tuple(int(row[0]) for row in conn.execute(
            "SELECT version FROM schema_migration ORDER BY version"
        ))
    except (sqlite3.Error, TypeError, ValueError) as exc:
        raise BatchM4RuntimeOracleError("Schema 16 migration ledger is unavailable") from exc
    if versions != tuple(range(1, RUNTIME_SCHEMA_VERSION + 1)):
        raise BatchM4RuntimeOracleError("Schema 16 migration ledger is not exact 1..16")
    ledger = {
        int(row[0]): str(row[1])
        for row in conn.execute(
            "SELECT version,applied_at FROM schema_migration WHERE version>=14"
        )
    }
    expected_ledger = {
        14: MIGRATION_0014_APPLIED_AT,
        15: MIGRATION_0015_APPLIED_AT,
        16: MIGRATION_0016_APPLIED_AT,
    }
    if ledger != expected_ledger:
        raise BatchM4RuntimeOracleError("Schema 16 migration ledger timestamps are not pinned")
    migration = ROOT / "migrations/0016_batch_wave01_family_projection.sql"
    try:
        raw_sha = sha256_bytes(migration.read_bytes())
    except OSError as exc:
        raise BatchM4RuntimeOracleError("Schema 16 migration SQL is unavailable") from exc
    if raw_sha != MIGRATION_0016_SHA256:
        raise BatchM4RuntimeOracleError("migration 0016 raw SQL seal mismatch")
    try:
        from .db import Database
        snapshot = Database._stage_structural_snapshot(conn)
        mode = str(snapshot["fts"]["mode"])
        signature = Database._stage_structural_signature(conn)
    except (sqlite3.Error, KeyError, TypeError, ValueError) as exc:
        raise BatchM4RuntimeOracleError("Schema 16 structural seal could not be checked") from exc
    if signature != SCHEMA16_STRUCTURAL_SIGNATURES.get(mode):
        raise BatchM4RuntimeOracleError("Schema 16 structural signature mismatch")
    return {"mode": mode, "signature": signature, "migration_0016_sha256": raw_sha}


def verify_schema16(conn: sqlite3.Connection) -> dict[str, Any]:
    """Verify only the parser-independent Schema 16 structural contract."""

    return _schema16_structural_seal(conn)


def _db_identity(path: Path) -> tuple[int, int, int, int]:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise BatchM4RuntimeOracleError("candidate database path is unavailable") from exc
    if not os.path.isfile(path) or path.is_symlink():
        raise BatchM4RuntimeOracleError("candidate database must be a regular non-symlink file")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _candidate_seal(db: ReadonlyDatabase, manifest: Mapping[str, Any], *, hash_file: bool) -> None:
    candidate = manifest.get("candidate")
    if candidate is None:
        return
    candidate = _validate_mapping(candidate, "candidate")
    size = candidate.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise BatchM4RuntimeOracleError("runtime candidate size seal is invalid")
    digest = _sha(candidate.get("sha256"), "candidate.sha256")
    identity = _db_identity(db.path)
    if identity[2] != size:
        raise BatchM4RuntimeOracleError("runtime candidate size seal mismatch")
    if hash_file and _sha256_file(db.path) != digest:
        raise BatchM4RuntimeOracleError("runtime candidate SHA-256 seal mismatch")


def _table_schema_digest(conn: sqlite3.Connection, table_name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone()
    if row is None or not isinstance(row[0], str):
        raise BatchM4RuntimeOracleError(f"family table is unavailable: {table_name}")
    return schema_digest(str(row[0]))


def _table_shape(conn: sqlite3.Connection, table_name: str) -> None:
    columns = tuple(
        str(row[1]) for row in conn.execute(f"PRAGMA table_info({_q(table_name)})")
    )
    if columns != ROW_COLUMNS:
        raise BatchM4RuntimeOracleError(f"{table_name} column shape mismatch")


def _digest_rows(rows: Sequence[Sequence[Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(canonical_row(tuple(row)).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _table_rows_digest(
    conn: sqlite3.Connection,
    table_name: str,
    run_id: str,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    _table_shape(conn, table_name)
    columns = expected.get("columns")
    if tuple(columns or ()) != ROW_COLUMNS:
        raise BatchM4RuntimeOracleError(f"{table_name} column contract mismatch")
    ordering = tuple(expected.get("ordering") or ())
    if ordering != ORDER_COLUMNS:
        raise BatchM4RuntimeOracleError(f"{table_name} ordering contract mismatch")
    chunk_size = expected.get("chunk_size")
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
        raise BatchM4RuntimeOracleError(f"{table_name} chunk size is invalid")
    expected_chunks = expected.get("chunks")
    if not isinstance(expected_chunks, list):
        raise BatchM4RuntimeOracleError(f"{table_name} chunks are invalid")
    digest_id_value = None
    table_digest = expected.get("_table_digest_id")
    if isinstance(table_digest, str) and table_digest:
        digest_id_value = table_digest
    select = ",".join(_q(column) for column in ROW_COLUMNS)
    order_sql = ",".join(_q(column) for column in ORDER_COLUMNS)
    cursor = conn.execute(
        f"SELECT {select} FROM {_q(table_name)} WHERE ingest_run_id=? ORDER BY {order_sql}",
        (run_id,),
    )
    rowset = hashlib.sha256()
    chunks: list[dict[str, Any]] = []
    chunk_rows: list[tuple[Any, ...]] = []
    total = 0
    key_indexes = tuple(ROW_COLUMNS.index(column) for column in ORDER_COLUMNS)
    for raw in cursor:
        row = tuple(raw)
        encoded = canonical_row(row).encode("utf-8")
        rowset.update(encoded)
        rowset.update(b"\n")
        chunk_rows.append(row)
        total += 1
        if len(chunk_rows) == chunk_size:
            chunks.append(_chunk_value_with_id(chunks, chunk_rows, key_indexes, digest_id_value))
            chunk_rows = []
    if chunk_rows:
        chunks.append(_chunk_value_with_id(chunks, chunk_rows, key_indexes, digest_id_value))
    actual = {
        "table_name": table_name,
        "columns": list(ROW_COLUMNS),
        "schema_sql_sha256": _table_schema_digest(conn, table_name),
        "ordering": list(ORDER_COLUMNS),
        "row_count": total,
        "logical_row_sha256": rowset.hexdigest(),
        "chunk_size": chunk_size,
        "chunk_count": len(chunks),
        "chunks": chunks,
    }
    for key in (
        "table_name", "columns", "schema_sql_sha256", "ordering", "row_count",
        "logical_row_sha256", "chunk_size", "chunk_count",
    ):
        if actual[key] != expected.get(key):
            raise BatchM4RuntimeOracleError(f"{table_name} {key} digest mismatch")
    if actual["chunks"] != expected_chunks:
        raise BatchM4RuntimeOracleError(f"{table_name} chunk digest mismatch")
    return actual


def _chunk_value_with_id(
    prior: Sequence[Mapping[str, Any]],
    rows: Sequence[Sequence[Any]],
    key_indexes: Sequence[int],
    digest_id_value: str | None,
) -> dict[str, Any]:
    ordinal = len(prior)
    value: dict[str, Any] = {
        "chunk_ordinal": ordinal,
        "first_key": [rows[0][index] for index in key_indexes],
        "last_key": [rows[-1][index] for index in key_indexes],
        "row_count": len(rows),
        "logical_row_sha256": _digest_rows(rows),
    }
    if digest_id_value is not None:
        value["id"] = digest_id("batch-m4-chunk", digest_id_value, str(ordinal))
    return value


def _family_item(manifest: Mapping[str, Any], family: str) -> Mapping[str, Any]:
    for item in manifest["families"]:
        if isinstance(item, Mapping) and item.get("family") == family:
            return item
    raise BatchM4RuntimeOracleError(f"runtime manifest family is missing: {family}")


def _metadata(value: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    result = value.get("metadata")
    if not isinstance(result, Mapping):
        raise BatchM4RuntimeOracleError(f"{label} metadata seal is missing")
    return result


def _verify_provenance_tables(
    conn: sqlite3.Connection,
    manifest: Mapping[str, Any],
    wave_id: str,
) -> None:
    entries = manifest.get("provenance_tables")
    if not isinstance(entries, list) or tuple(item.get("table") for item in entries) != PROVENANCE_TABLES:
        raise BatchM4RuntimeOracleError("M4 provenance table list mismatch")
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise BatchM4RuntimeOracleError("M4 provenance entry is invalid")
        table = _text(entry.get("table"), "provenance.table")
        columns = entry.get("columns")
        if not isinstance(columns, list) or tuple(columns) != tuple(
            str(row[1]) for row in conn.execute(f"PRAGMA table_info({_q(table)})")
        ):
            raise BatchM4RuntimeOracleError(f"{table} provenance columns mismatch")
        if table == "batch_wave":
            rows = [
                tuple(row) for row in conn.execute(
                    f"SELECT {','.join(_q(c) for c in columns)} FROM {_q(table)} "
                    "WHERE id=? ORDER BY id", (wave_id,)
                )
            ]
        elif table == "batch_table_chunk_digest":
            selected = ",".join(f"c.{_q(column)}" for column in columns)
            rows = [
                tuple(row) for row in conn.execute(
                    f"SELECT {selected} FROM {_q(table)} c "
                    f"JOIN {_q('batch_table_digest')} d ON d.id=c.table_digest_id "
                    "WHERE d.wave_id=? ORDER BY c.id", (wave_id,)
                )
            ]
        else:
            rows = [
                tuple(row) for row in conn.execute(
                    f"SELECT {','.join(_q(c) for c in columns)} FROM {_q(table)} "
                    "WHERE wave_id=? ORDER BY id", (wave_id,)
                )
            ]
        if entry.get("scope") != "m4_wave" or entry.get("row_count") != len(rows):
            raise BatchM4RuntimeOracleError(f"{table} provenance scope/count mismatch")
        if entry.get("sha256") != _digest_rows(rows):
            raise BatchM4RuntimeOracleError(f"{table} provenance digest mismatch")


def _verify_family(
    conn: sqlite3.Connection,
    manifest: Mapping[str, Any],
    family: str,
) -> dict[str, Any]:
    source = _validate_mapping(manifest.get("source"), "source")
    wave = _validate_mapping(manifest.get("wave"), "wave")
    wave_id = _text(wave.get("id"), "wave.id")
    item = _family_item(manifest, family)
    contract = _text(item.get("family_contract_version"), f"{family}.family_contract_version")
    run_id = _text(item.get("ingest_run_id"), f"{family}.ingest_run_id")
    parser_id = _text(item.get("parser_build_id"), f"{family}.parser_build_id")
    table_name = FAMILY_TABLES[family]
    table = _validate_mapping(item.get("table"), f"{family}.table")
    if table.get("table_name") != table_name:
        raise BatchM4RuntimeOracleError(f"{family} table binding mismatch")
    if conn.execute(
        "SELECT 1 FROM batch_wave WHERE id=? AND snapshot_id=? AND contract_version=? "
        "AND manifest_sha256=? AND target_schema_version=? AND status=?",
        (wave_id, source["snapshot_id"], RUNTIME_WAVE_CONTRACT, wave["manifest_sha256"],
         RUNTIME_SCHEMA_VERSION, "complete"),
    ).fetchone() is None:
        raise BatchM4RuntimeOracleError("batch_wave exact manifest binding mismatch")
    wave_row = conn.execute(
        "SELECT metadata_json FROM batch_wave WHERE id=?", (wave_id,)
    ).fetchone()
    _same_json(wave_row[0], wave["metadata"], "batch_wave.metadata_json")
    _table_shape(conn, table_name)
    count = conn.execute(f"SELECT COUNT(*) FROM {_q(table_name)}").fetchone()
    if count is None or int(count[0]) != int(table.get("row_count", -1)):
        raise BatchM4RuntimeOracleError(f"{family} total row count mismatch")
    expected_values = {
        "family": family,
        "family_contract_version": contract,
        "snapshot_id": source["snapshot_id"],
        "parser_build_id": parser_id,
        "ingest_run_id": run_id,
    }
    for column, expected_value in expected_values.items():
        if conn.execute(
            f"SELECT 1 FROM {_q(table_name)} WHERE {_q(column)}<>? LIMIT 1",
            (expected_value,),
        ).fetchone() is not None:
            raise BatchM4RuntimeOracleError(f"{family} {column} provenance mismatch")
    actual_table = dict(table)
    digest_id_value = _text(
        _validate_mapping(item.get("table_digest"), f"{family}.table_digest").get("id"),
        f"{family}.table_digest.id",
    )
    actual_table["_table_digest_id"] = digest_id_value
    actual_table = _table_rows_digest(conn, table_name, run_id, actual_table)
    shard = _validate_mapping(item.get("shard"), f"{family}.shard")
    shard_id = _text(shard.get("id"), f"{family}.shard.id")
    shard_row = conn.execute(
        "SELECT id,wave_id,family,family_contract_version,ingest_run_id,manifest_sha256,"
        "shard_schema_version,read_set_json,write_set_json,rowset_sha256,status,metadata_json "
        "FROM batch_shard WHERE id=?", (shard_id,)
    ).fetchone()
    if shard_row is None or tuple(shard_row[:7]) != (
        shard_id, wave_id, family, contract, run_id, wave["manifest_sha256"],
        shard.get("shard_schema_version"),
    ) or shard_row[10] != "imported" or shard_row[9] != shard.get("rowset_sha256"):
        raise BatchM4RuntimeOracleError(f"{family} batch_shard provenance mismatch")
    if shard_row[9] != actual_table["logical_row_sha256"]:
        raise BatchM4RuntimeOracleError(f"{family} batch_shard rowset mismatch")
    _same_json(shard_row[7], shard.get("read_set"), f"{family}.shard.read_set_json")
    _same_json(shard_row[8], shard.get("write_set"), f"{family}.shard.write_set_json")
    _same_json(shard_row[11], _metadata(shard, f"{family}.shard"), f"{family}.shard.metadata_json")
    imported = _validate_mapping(item.get("import"), f"{family}.import")
    import_id = _text(imported.get("id"), f"{family}.import.id")
    import_row = conn.execute(
        "SELECT id,wave_id,shard_id,target_schema_version,shard_file_sha256,"
        "imported_row_count,status,metadata_json FROM batch_shard_import WHERE id=?",
        (import_id,),
    ).fetchone()
    if import_row is None or tuple(import_row[:4]) != (
        import_id, wave_id, shard_id, RUNTIME_SCHEMA_VERSION,
    ) or import_row[5] != actual_table["row_count"] or import_row[6] != "complete":
        raise BatchM4RuntimeOracleError(f"{family} batch_shard_import provenance mismatch")
    for index, key in ((4, "shard_file_sha256"),):
        if import_row[index] != imported.get(key):
            raise BatchM4RuntimeOracleError(f"{family} import {key} mismatch")
    _same_json(import_row[7], _metadata(imported, f"{family}.import"), f"{family}.import.metadata_json")
    table_digest = _validate_mapping(item.get("table_digest"), f"{family}.table_digest")
    digest_row = conn.execute(
        "SELECT id,wave_id,shard_id,table_name,schema_sql_sha256,ordering_json,row_count,"
        "logical_row_sha256,chunk_size,chunk_count,metadata_json FROM batch_table_digest WHERE id=?",
        (digest_id_value,),
    ).fetchone()
    if digest_row is None or tuple(digest_row[:5]) != (
        digest_id_value, wave_id, shard_id, table_name, table["schema_sql_sha256"],
    ) or digest_row[6] != table["row_count"] or digest_row[7] != table["logical_row_sha256"] or digest_row[8] != table["chunk_size"] or digest_row[9] != table["chunk_count"]:
        raise BatchM4RuntimeOracleError(f"{family} batch_table_digest provenance mismatch")
    _same_json(digest_row[5], table["ordering"], f"{family}.table_digest.ordering_json")
    _same_json(digest_row[10], _metadata(table_digest, f"{family}.table_digest"), f"{family}.table_digest.metadata_json")
    chunks = table.get("chunks")
    if not isinstance(chunks, list):
        raise BatchM4RuntimeOracleError(f"{family} table chunks are missing")
    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            raise BatchM4RuntimeOracleError(f"{family} chunk seal is invalid")
        chunk_id = _text(chunk.get("id"), f"{family}.chunk.id")
        row = conn.execute(
            "SELECT id,table_digest_id,chunk_ordinal,first_key_json,last_key_json,row_count,logical_row_sha256 "
            "FROM batch_table_chunk_digest WHERE id=?", (chunk_id,)
        ).fetchone()
        if row is None or tuple(row[:3]) != (chunk_id, digest_id_value, chunk.get("chunk_ordinal")) or row[5] != chunk.get("row_count") or row[6] != chunk.get("logical_row_sha256"):
            raise BatchM4RuntimeOracleError(f"{family} chunk provenance mismatch")
        _same_json(row[3], chunk.get("first_key"), f"{family}.chunk.first_key_json")
        _same_json(row[4], chunk.get("last_key"), f"{family}.chunk.last_key_json")
    actual_chunks = conn.execute(
        "SELECT COUNT(*) FROM batch_table_chunk_digest WHERE table_digest_id=?",
        (digest_id_value,),
    ).fetchone()
    if actual_chunks is None or int(actual_chunks[0]) != len(chunks):
        raise BatchM4RuntimeOracleError(f"{family} chunk provenance closure mismatch")
    parser = _validate_mapping(item.get("parser_build"), f"{family}.parser_build")
    parser_row = conn.execute(
        "SELECT id,parser_version,mapping_version,fingerprint,config_checksum,created_at "
        "FROM parser_build WHERE id=?", (parser_id,)
    ).fetchone()
    if parser_row is None or tuple(parser_row) != (
        parser_id, parser.get("parser_version"), parser.get("mapping_version"),
        parser.get("fingerprint"), parser.get("config_checksum"), parser.get("created_at"),
    ):
        raise BatchM4RuntimeOracleError(f"{family} parser_build provenance mismatch")
    ingest = _validate_mapping(item.get("ingest_run"), f"{family}.ingest_run")
    ingest_row = conn.execute(
        "SELECT id,snapshot_id,parser_build_id,scope_json,config_json,status,created_at "
        "FROM ingest_run WHERE id=?", (run_id,)
    ).fetchone()
    if ingest_row is None or tuple(ingest_row[:3]) != (
        run_id, source["snapshot_id"], parser_id,
    ) or ingest_row[5] != ingest.get("status") or ingest_row[6] != ingest.get("created_at"):
        raise BatchM4RuntimeOracleError(f"{family} ingest_run provenance mismatch")
    if ingest.get("status") != "complete":
        raise BatchM4RuntimeOracleError(f"{family} ingest run is not complete")
    _same_json(ingest_row[3], ingest.get("scope"), f"{family}.ingest_run.scope_json")
    _same_json(ingest_row[4], ingest.get("config"), f"{family}.ingest_run.config_json")
    return {
        "family": family,
        "ingest_run_id": run_id,
        "parser_build_id": parser_id,
        "table_name": table_name,
        "row_count": actual_table["row_count"],
        "logical_row_sha256": actual_table["logical_row_sha256"],
    }


def verify_batch_m4_runtime(
    db: ReadonlyDatabase | sqlite3.Connection,
    manifest_path: str | Path = RUNTIME_MANIFEST_PATH,
    *,
    family: str | None = None,
    hash_candidate: bool = True,
) -> dict[str, Any]:
    manifest = load_runtime_manifest(manifest_path)
    conn = db.conn if isinstance(db, ReadonlyDatabase) else db
    if not isinstance(conn, sqlite3.Connection):
        raise TypeError("db must be ReadonlyDatabase or sqlite3.Connection")
    _schema16_structural_seal(conn)
    if isinstance(db, ReadonlyDatabase):
        _candidate_seal(db, manifest, hash_file=hash_candidate)
    source = _validate_mapping(manifest.get("source"), "source")
    wave = _validate_mapping(manifest.get("wave"), "wave")
    wave_id = _text(wave.get("id"), "wave.id")
    if conn.execute(
        "SELECT 1 FROM batch_wave WHERE id=? AND snapshot_id=? AND contract_version=? "
        "AND manifest_sha256=? AND target_schema_version=? AND status=?",
        (wave_id, source["snapshot_id"], RUNTIME_WAVE_CONTRACT, wave["manifest_sha256"],
         RUNTIME_SCHEMA_VERSION, "complete"),
    ).fetchone() is None:
        raise BatchM4RuntimeOracleError("batch_wave exact manifest binding mismatch")
    source_row = conn.execute(
        'SELECT id,"commit",tree FROM source_snapshot WHERE id=?', (source["snapshot_id"],)
    ).fetchone()
    if source_row is None or tuple(source_row) != (
        source.get("snapshot_id"), source.get("commit"), source.get("tree"),
    ):
        raise BatchM4RuntimeOracleError("source snapshot metadata mismatch")
    _same_json(
        conn.execute("SELECT metadata_json FROM batch_wave WHERE id=?", (wave_id,)).fetchone()[0],
        wave["metadata"],
        "batch_wave.metadata_json",
    )
    _verify_provenance_tables(conn, manifest, wave_id)
    selected = (family,) if family is not None else M4_FAMILY_ORDER
    if any(item not in M4_FAMILY_ORDER for item in selected):
        raise BatchM4RuntimeOracleError(f"unknown M4 family: {family}")
    results = [_verify_family(conn, manifest, item) for item in selected]
    foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_keys:
        raise BatchM4RuntimeOracleError(f"Schema 16 foreign-key check failed: {foreign_keys[:3]}")
    return {
        "status": "verified",
        "manifest_path": str(Path(manifest_path).resolve()),
        "manifest_sha256": manifest["manifest_sha256"],
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "wave_id": wave_id,
        "wave_manifest_sha256": wave["manifest_sha256"],
        "family_count": len(results),
        "families": results,
        "foreign_key_check": "clean",
    }


@dataclass(frozen=True)
class BatchM4RuntimeOracle:
    db: ReadonlyDatabase
    manifest_path: Path = RUNTIME_MANIFEST_PATH

    def verify_startup(self) -> dict[str, Any]:
        return verify_batch_m4_runtime(self.db, self.manifest_path, hash_candidate=True)

    def verify_family(self, family: str) -> dict[str, Any]:
        return verify_batch_m4_runtime(
            self.db, self.manifest_path, family=family, hash_candidate=False
        )


__all__ = [
    "BatchM4RuntimeOracle",
    "BatchM4RuntimeOracleError",
    "FAMILY_TABLES",
    "M4_CANDIDATE_METADATA",
    "M4_FAMILY_ORDER",
    "M4_PROVIDER_REGISTRY_FLAGS",
    "MIGRATION_0014_APPLIED_AT",
    "MIGRATION_0015_APPLIED_AT",
    "MIGRATION_0016_APPLIED_AT",
    "MIGRATION_0016_SHA256",
    "ORDER_COLUMNS",
    "PROVENANCE_TABLES",
    "ROW_COLUMNS",
    "RUNTIME_COVERAGE",
    "RUNTIME_MANIFEST_ID",
    "RUNTIME_MANIFEST_PATH",
    "RUNTIME_MANIFEST_VERSION",
    "RUNTIME_SCHEMA_CONTRACT",
    "RUNTIME_SCHEMA_VERSION",
    "RUNTIME_WAVE_CONTRACT",
    "SCHEMA16_STRUCTURAL_SIGNATURES",
    "canonical_manifest_sha256",
    "load_runtime_manifest",
    "verify_batch_m4_runtime",
    "verify_schema16",
]
