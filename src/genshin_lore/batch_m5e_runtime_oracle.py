"""Independent Schema17 M5E runtime oracle.

M5E combines the accepted Wave01 family boundary with the candidate Wave02
family boundary in one explicit 21-family runtime contract.  The oracle does
not import a projection builder and does not infer identity across families.
It reads a portable manifest, validates an explicit M5E receipt, then streams
the exact database rowsets and both wave provenance scopes once at startup.
The resulting qualification snapshot is intended to be reused by providers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ._readonly_database import ReadonlyDatabase, open_readonly_database
from .batch_m4_runtime_oracle import M4_FAMILY_ORDER
from .batch_m5d_runtime import M5D_FAMILY_ORDER
from .family_projection_protocol import canonical_row, schema_digest
from .util import canonical_json, sha256_bytes

ROOT = Path(__file__).resolve().parents[2]
M5E_RUNTIME_MANIFEST_PATH = ROOT / "manifests/batch/anime-game-data-7.0-batch-m5e-wave02-runtime.json"
M5E_RECEIPT_PATH = ROOT / "reports/batch-m5e/batch-m5e-wave.release-receipt.json"
M5E_SCHEMA_VERSION = 17
M5E_SCHEMA_CONTRACT = "schema17-migration0017-additive-wave02-exact"
M5E_MANIFEST_ID = "anime-game-data-7.0-batch-m5e-wave02-runtime"
M5E_MANIFEST_CONTRACT = "batch-m5e-runtime-business-oracle-v1"
M5E_MANIFEST_VERSION = "batch-m5e-wave02-runtime-v1"
M5E_COVERAGE = "R2_wave_accepted_product"
M5E_CANDIDATE_COVERAGE = "R2_wave_candidate"
M5E_WAVE_CONTRACT = "batch-wave-v5"
M5E_FAMILY_ORDER = (*M4_FAMILY_ORDER, *M5D_FAMILY_ORDER)
M5E_WAVE_NAMES = ("wave01", "wave02")
M5E_PROVENANCE_TABLES = (
    "batch_wave", "batch_shard", "batch_shard_import",
    "batch_table_digest", "batch_table_chunk_digest",
)
M5E_MIGRATION_0014_APPLIED_AT = "2026-09-01T00:00:00+00:00"
M5E_MIGRATION_0015_APPLIED_AT = "2026-09-02T00:00:00+00:00"
M5E_MIGRATION_0016_APPLIED_AT = "2026-09-03T00:00:00+00:00"
M5E_MIGRATION_0017_APPLIED_AT = "2026-09-05T00:00:00+00:00"
M5E_MIGRATION_0017_SHA256 = "ec8e41402b0c07b5b94aece97607a066c452de12d65c2ee0c49f486df9474c36"
M5E_PROVIDER_REGISTRY = {
    "family_provider_registry_changed": True,
    "top_level_tool_registry_changed": False,
    "top_level_tool_count": 25,
}
M5E_IDENTITY_POLICY = "raw_evidence_identity_is_not_logical_identity; no cross-family merge"
M5E_BUSINESS_SEAL_ALGORITHM = "batch-m5e-family-business-rowset-v1"
M5E_PROVENANCE_SEAL_ALGORITHM = "batch-m5e-wave-provenance-v1"

M4_FAMILY_TABLES = {family: (f"{family}_row_occurrence",) for family in M4_FAMILY_ORDER}
M5D_FAMILY_TABLES = {
    "avatar_codex_archive": (
        "avatar_codex_row_occurrence", "avatar_codex_raw_field_occurrence",
        "avatar_codex_relationship_occurrence", "avatar_codex_relationship_candidate_occurrence",
        "avatar_codex_opaque_occurrence",
    ),
    "cook_recipe_archive": (
        "cook_recipe_archive_row_occurrence", "cook_recipe_archive_raw_field_occurrence",
        "cook_recipe_archive_relationship_occurrence", "cook_recipe_archive_relationship_candidate_occurrence",
        "cook_recipe_archive_text_owner_occurrence", "cook_recipe_archive_text_resolution_occurrence",
        "cook_recipe_archive_text_candidate_occurrence", "cook_recipe_archive_opaque_occurrence",
        "cook_recipe_archive_asset_occurrence",
    ),
    "cook_bonus_archive": (
        "cook_bonus_archive_row_occurrence", "cook_bonus_archive_raw_field_occurrence",
        "cook_bonus_archive_relationship_occurrence", "cook_bonus_archive_relationship_candidate_occurrence",
        "cook_bonus_archive_opaque_occurrence",
    ),
    "homeworld_furniture_archive": (
        "homeworld_furniture_archive_row_occurrence", "homeworld_furniture_archive_raw_field_occurrence",
        "homeworld_furniture_archive_relationship_occurrence", "homeworld_furniture_archive_relationship_candidate_occurrence",
        "homeworld_furniture_archive_text_owner_occurrence", "homeworld_furniture_archive_text_resolution_occurrence",
        "homeworld_furniture_archive_text_candidate_occurrence", "homeworld_furniture_archive_opaque_occurrence",
        "homeworld_furniture_archive_asset_occurrence",
    ),
    "furniture_make_archive": (
        "furniture_make_archive_row_occurrence", "furniture_make_archive_raw_field_occurrence",
        "furniture_make_archive_relationship_occurrence", "furniture_make_archive_relationship_candidate_occurrence",
        "furniture_make_archive_opaque_occurrence",
    ),
    "furniture_suite_archive": (
        "furniture_suite_archive_row_occurrence", "furniture_suite_archive_raw_field_occurrence",
        "furniture_suite_archive_relationship_occurrence", "furniture_suite_archive_relationship_candidate_occurrence",
        "furniture_suite_archive_text_owner_occurrence", "furniture_suite_archive_text_resolution_occurrence",
        "furniture_suite_archive_text_candidate_occurrence", "furniture_suite_archive_opaque_occurrence",
        "furniture_suite_archive_asset_occurrence",
    ),
    "homeworld_furniture_type_archive": (
        "homeworld_furniture_type_archive_row_occurrence", "homeworld_furniture_type_archive_raw_field_occurrence",
        "homeworld_furniture_type_archive_text_owner_occurrence", "homeworld_furniture_type_archive_text_resolution_occurrence",
        "homeworld_furniture_type_archive_text_candidate_occurrence", "homeworld_furniture_type_archive_opaque_occurrence",
        "homeworld_furniture_type_archive_asset_occurrence",
    ),
    "homeworld_bgm_archive": (
        "homeworld_bgm_archive_row_occurrence", "homeworld_bgm_archive_raw_field_occurrence",
        "homeworld_bgm_archive_text_owner_occurrence", "homeworld_bgm_archive_text_resolution_occurrence",
        "homeworld_bgm_archive_text_candidate_occurrence", "homeworld_bgm_archive_opaque_occurrence",
        "homeworld_bgm_archive_asset_occurrence",
    ),
    "profile_picture_archive": (
        "profile_picture_archive_row_occurrence", "profile_picture_archive_raw_field_occurrence",
        "profile_picture_archive_relationship_occurrence", "profile_picture_archive_relationship_candidate_occurrence",
        "profile_picture_archive_text_owner_occurrence", "profile_picture_archive_text_resolution_occurrence",
        "profile_picture_archive_text_candidate_occurrence", "profile_picture_archive_opaque_occurrence",
        "profile_picture_archive_asset_occurrence",
    ),
    "profile_frame_archive": (
        "profile_frame_archive_row_occurrence", "profile_frame_archive_raw_field_occurrence",
        "profile_frame_archive_text_owner_occurrence", "profile_frame_archive_text_resolution_occurrence",
        "profile_frame_archive_text_candidate_occurrence", "profile_frame_archive_opaque_occurrence",
        "profile_frame_archive_asset_occurrence",
    ),
    "gadget_title_archive": (
        "gadget_title_archive_row_occurrence", "gadget_title_archive_raw_field_occurrence",
        "gadget_title_archive_relationship_occurrence", "gadget_title_archive_relationship_candidate_occurrence",
        "gadget_title_archive_text_owner_occurrence", "gadget_title_archive_text_resolution_occurrence",
        "gadget_title_archive_text_candidate_occurrence",
    ),
}
M5E_FAMILY_TABLES = {**M4_FAMILY_TABLES, **M5D_FAMILY_TABLES}
M5E_FAMILY_WAVE = {
    **{family: "wave01" for family in M4_FAMILY_ORDER},
    **{family: "wave02" for family in M5D_FAMILY_ORDER},
}
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_POINTER_PATH = re.compile(r"^\$\.families\[\d+\].*\.(?:first_key|last_key)\[\d+\]$")


class BatchM5ERuntimeOracleError(RuntimeError):
    """The M5E manifest, receipt, or exact database does not qualify."""


def _q(identifier: str) -> str:
    if not isinstance(identifier, str) or not _IDENTIFIER.fullmatch(identifier):
        raise BatchM5ERuntimeOracleError(f"unsafe SQLite identifier: {identifier!r}")
    return '"' + identifier.replace('"', '""') + '"'


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise BatchM5ERuntimeOracleError(f"cannot hash file: {path}") from exc
    return digest.hexdigest()


def _digest_rows(rows: Sequence[Sequence[Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(canonical_row(tuple(row)).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _strict_json(raw: bytes, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise BatchM5ERuntimeOracleError(f"duplicate {label} key: {key}")
            result[key] = item
        return result
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
    except BatchM5ERuntimeOracleError:
        raise
    except (UnicodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BatchM5ERuntimeOracleError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise BatchM5ERuntimeOracleError(f"{label} must be an object")
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
        if _POINTER_PATH.fullmatch(path):
            return
        if value.startswith("/") or value.startswith("~") or re.match(r"^[A-Za-z]:[\\/]", value):
            raise BatchM5ERuntimeOracleError(f"runtime manifest contains an absolute path at {path}")
        if "/tmp/" in value or value.startswith("tmp/"):
            raise BatchM5ERuntimeOracleError(f"runtime manifest contains a machine-local path at {path}")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise BatchM5ERuntimeOracleError(f"{label} must be a non-empty string")
    return value


def _sha(value: Any, label: str) -> str:
    result = _text(value, label)
    if not _SHA256.fullmatch(result):
        raise BatchM5ERuntimeOracleError(f"{label} must be a lowercase SHA-256")
    return result


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BatchM5ERuntimeOracleError(f"{label} must be an object")
    return value


def _int(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise BatchM5ERuntimeOracleError(f"{label} must be an integer >= {minimum}")
    return value


def _validate_provenance_entries(entries: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(entries, list) or tuple(item.get("table") for item in entries if isinstance(item, Mapping)) != M5E_PROVENANCE_TABLES:
        raise BatchM5ERuntimeOracleError(f"{label} table boundary mismatch")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(entries):
        entry = _mapping(item, f"{label}[{index}]")
        if entry.get("table") != M5E_PROVENANCE_TABLES[index]:
            raise BatchM5ERuntimeOracleError(f"{label}[{index}] table order mismatch")
        columns = entry.get("columns")
        if not isinstance(columns, list) or not columns or not all(isinstance(column, str) and _IDENTIFIER.fullmatch(column) for column in columns):
            raise BatchM5ERuntimeOracleError(f"{label}[{index}].columns is invalid")
        _int(entry.get("row_count"), f"{label}[{index}].row_count")
        _sha(entry.get("sha256"), f"{label}[{index}].sha256")
        if not isinstance(entry.get("scope"), str) or not entry["scope"]:
            raise BatchM5ERuntimeOracleError(f"{label}[{index}].scope is invalid")
        result.append(dict(entry))
    return result


def _wave_provenance_digest(name: str, wave_id: str, entries: Sequence[Mapping[str, Any]]) -> str:
    return sha256_bytes(canonical_json({
        "algorithm": M5E_PROVENANCE_SEAL_ALGORITHM,
        "wave": name,
        "wave_id": wave_id,
        "tables": [
            {"table": item["table"], "row_count": item["row_count"], "sha256": item["sha256"]}
            for item in entries
        ],
    }).encode("utf-8"))


def _validate_table_spec(table: Any, family: str, ordinal: int) -> dict[str, Any]:
    value = _mapping(table, f"{family}.tables[{ordinal}]")
    name = _text(value.get("table_name"), f"{family}.tables[{ordinal}].table_name")
    expected_names = M5E_FAMILY_TABLES[family]
    if name != expected_names[ordinal]:
        raise BatchM5ERuntimeOracleError(f"{family} table order/name mismatch: {name}")
    columns = value.get("columns")
    ordering = value.get("ordering")
    if not isinstance(columns, list) or not columns or not all(isinstance(column, str) and _IDENTIFIER.fullmatch(column) for column in columns):
        raise BatchM5ERuntimeOracleError(f"{family}.{name} columns are invalid")
    if not isinstance(ordering, list) or not ordering or not all(isinstance(column, str) and column in columns for column in ordering):
        raise BatchM5ERuntimeOracleError(f"{family}.{name} ordering is invalid")
    _sha(value.get("schema_sql_sha256"), f"{family}.{name}.schema_sql_sha256")
    _int(value.get("row_count"), f"{family}.{name}.row_count")
    _sha(value.get("logical_row_sha256"), f"{family}.{name}.logical_row_sha256")
    _int(value.get("chunk_size"), f"{family}.{name}.chunk_size", minimum=1)
    chunk_count = _int(value.get("chunk_count"), f"{family}.{name}.chunk_count")
    chunks = value.get("chunks")
    if not isinstance(chunks, list) or len(chunks) != chunk_count:
        raise BatchM5ERuntimeOracleError(f"{family}.{name}.chunks are invalid")
    for chunk_index, chunk in enumerate(chunks):
        chunk_value = _mapping(chunk, f"{family}.{name}.chunks[{chunk_index}]")
        if chunk_value.get("chunk_ordinal") != chunk_index:
            raise BatchM5ERuntimeOracleError(f"{family}.{name} chunk ordinal mismatch")
        _int(chunk_value.get("row_count"), f"{family}.{name}.chunks[{chunk_index}].row_count", minimum=1)
        _sha(chunk_value.get("logical_row_sha256"), f"{family}.{name}.chunks[{chunk_index}].logical_row_sha256")
        if not isinstance(chunk_value.get("first_key"), list) or not isinstance(chunk_value.get("last_key"), list):
            raise BatchM5ERuntimeOracleError(f"{family}.{name} chunk keys are invalid")
    target_columns = value.get("target_columns")
    if target_columns is not None and (not isinstance(target_columns, list) or not target_columns or not all(isinstance(column, str) for column in target_columns)):
        raise BatchM5ERuntimeOracleError(f"{family}.{name}.target_columns are invalid")
    target_only = value.get("target_only_columns", [])
    if not isinstance(target_only, list) or not all(isinstance(column, str) for column in target_only):
        raise BatchM5ERuntimeOracleError(f"{family}.{name}.target_only_columns are invalid")
    return dict(value)


def load_runtime_manifest(path: str | Path = M5E_RUNTIME_MANIFEST_PATH, *, require_product_accepted: bool = False) -> dict[str, Any]:
    """Load and validate the portable M5E runtime manifest contract."""
    manifest_path = Path(path)
    try:
        value = _strict_json(manifest_path.read_bytes(), "M5E runtime manifest")
    except OSError as exc:
        raise BatchM5ERuntimeOracleError(f"M5E runtime manifest is unavailable: {manifest_path}") from exc
    _portable_scan(value)
    seal = _sha(value.get("manifest_sha256"), "manifest_sha256")
    if canonical_manifest_sha256(value) != seal:
        raise BatchM5ERuntimeOracleError("M5E runtime manifest canonical SHA-256 mismatch")
    if value.get("manifest_id") != M5E_MANIFEST_ID or value.get("manifest_contract") != M5E_MANIFEST_CONTRACT or value.get("manifest_version") != M5E_MANIFEST_VERSION:
        raise BatchM5ERuntimeOracleError("M5E runtime manifest identity mismatch")
    if value.get("schema_version") != M5E_SCHEMA_VERSION or value.get("schema_contract") != M5E_SCHEMA_CONTRACT:
        raise BatchM5ERuntimeOracleError("M5E runtime manifest Schema17 mismatch")
    if value.get("corpus_complete") is not False or value.get("identity_policy") != M5E_IDENTITY_POLICY:
        raise BatchM5ERuntimeOracleError("M5E runtime manifest coverage/identity policy mismatch")
    product = value.get("product_accepted")
    if not isinstance(product, bool) or value.get("acceptance_candidate") is not (not product):
        raise BatchM5ERuntimeOracleError("M5E acceptance_candidate/product_accepted mismatch")
    if require_product_accepted and product is not True:
        raise BatchM5ERuntimeOracleError("M5E runtime manifest is not product accepted")
    registry = _mapping(value.get("provider_registry"), "provider_registry")
    for key, expected in M5E_PROVIDER_REGISTRY.items():
        if registry.get(key) != expected:
            raise BatchM5ERuntimeOracleError("M5E provider registry contract mismatch")
    source = _mapping(value.get("source"), "source")
    snapshot_id = _text(value.get("snapshot_id"), "snapshot_id")
    if source.get("snapshot_id") != snapshot_id:
        raise BatchM5ERuntimeOracleError("M5E snapshot/source binding mismatch")
    _text(source.get("commit"), "source.commit")
    _text(source.get("tree"), "source.tree")
    if tuple(value.get("family_order") or ()) != M5E_FAMILY_ORDER:
        raise BatchM5ERuntimeOracleError("M5E family order mismatch")
    wave = _mapping(value.get("wave"), "wave")
    wave_id = _text(wave.get("id"), "wave.id")
    _sha(wave.get("manifest_sha256"), "wave.manifest_sha256")
    if wave.get("contract_version") != M5E_WAVE_CONTRACT or wave.get("snapshot_id") != snapshot_id or wave.get("target_schema_version") != M5E_SCHEMA_VERSION or wave.get("status") != "complete":
        raise BatchM5ERuntimeOracleError("M5E combined wave contract mismatch")
    wave_metadata = _mapping(wave.get("metadata"), "wave.metadata")
    if tuple(wave_metadata.get("family_order") or ()) != M5E_FAMILY_ORDER or wave_metadata.get("corpus_complete") is not False or wave_metadata.get("logical_identity_created") is not False or wave_metadata.get("cross_family_merge") is not False or wave_metadata.get("product_accepted") is not product:
        raise BatchM5ERuntimeOracleError("M5E combined wave metadata mismatch")
    source_waves = value.get("source_waves")
    if not isinstance(source_waves, list) or tuple(item.get("name") for item in source_waves if isinstance(item, Mapping)) != M5E_WAVE_NAMES:
        raise BatchM5ERuntimeOracleError("M5E source wave boundary mismatch")
    for index, source_wave in enumerate(source_waves):
        item = _mapping(source_wave, f"source_waves[{index}]")
        if item.get("name") != M5E_WAVE_NAMES[index]:
            raise BatchM5ERuntimeOracleError("M5E source wave order mismatch")
        expected_schema = 16 if index == 0 else 17
        expected_contract = "batch-wave-v3" if index == 0 else "batch-wave-v4"
        if item.get("schema_version") != expected_schema or item.get("schema_contract") != ("schema16-migration0016-additive-wave01-exact" if index == 0 else M5E_SCHEMA_CONTRACT):
            raise BatchM5ERuntimeOracleError(f"M5E {item['name']} source schema mismatch")
        _text(item.get("manifest_id"), f"source_waves[{index}].manifest_id")
        _text(item.get("manifest_version"), f"source_waves[{index}].manifest_version")
        _sha(item.get("manifest_sha256"), f"source_waves[{index}].manifest_sha256")
        _text(item.get("wave_id"), f"source_waves[{index}].wave_id")
        _sha(item.get("wave_manifest_sha256"), f"source_waves[{index}].wave_manifest_sha256")
        if item.get("wave_contract") != expected_contract:
            raise BatchM5ERuntimeOracleError(f"M5E {item['name']} source wave contract mismatch")
        if item.get("snapshot_id") != snapshot_id:
            raise BatchM5ERuntimeOracleError(f"M5E {item['name']} source snapshot mismatch")
        source_order = M4_FAMILY_ORDER if index == 0 else M5D_FAMILY_ORDER
        if tuple(item.get("family_order") or ()) != source_order:
            raise BatchM5ERuntimeOracleError(f"M5E {item['name']} source family order mismatch")
        _validate_provenance_entries(item.get("provenance_tables"), f"source_waves[{index}].provenance_tables")
    provenance_waves = value.get("provenance_waves")
    if not isinstance(provenance_waves, list) or tuple(item.get("name") for item in provenance_waves if isinstance(item, Mapping)) != M5E_WAVE_NAMES:
        raise BatchM5ERuntimeOracleError("M5E provenance wave order mismatch")
    source_by_name = {str(item["name"]): item for item in source_waves}
    provenance_by_name: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(provenance_waves):
        entry = _mapping(item, f"provenance_waves[{index}]")
        name = M5E_WAVE_NAMES[index]
        if entry.get("name") != name or entry.get("wave_id") != source_by_name[name]["wave_id"]:
            raise BatchM5ERuntimeOracleError(f"M5E provenance wave binding mismatch: {name}")
        tables = _validate_provenance_entries(entry.get("tables"), f"provenance_waves[{index}].tables")
        expected_sha = _wave_provenance_digest(name, str(entry["wave_id"]), tables)
        if entry.get("sha256") != expected_sha:
            raise BatchM5ERuntimeOracleError(f"M5E provenance wave seal mismatch: {name}")
        provenance_by_name[name] = entry
    families = value.get("families")
    if not isinstance(families, list) or tuple(item.get("family") for item in families if isinstance(item, Mapping)) != M5E_FAMILY_ORDER:
        raise BatchM5ERuntimeOracleError("M5E family list mismatch")
    runs = value.get("family_runs")
    contracts = value.get("family_contracts")
    if not isinstance(runs, Mapping) or set(runs) != set(M5E_FAMILY_ORDER) or not isinstance(contracts, Mapping) or set(contracts) != set(M5E_FAMILY_ORDER):
        raise BatchM5ERuntimeOracleError("M5E family run/contract map mismatch")
    for family, family_item in zip(M5E_FAMILY_ORDER, families):
        item = _mapping(family_item, f"families.{family}")
        if item.get("family") != family or item.get("wave") != M5E_FAMILY_WAVE[family]:
            raise BatchM5ERuntimeOracleError(f"M5E family wave binding mismatch: {family}")
        _text(item.get("family_contract_version"), f"{family}.family_contract_version")
        _text(item.get("ingest_run_id"), f"{family}.ingest_run_id")
        _text(item.get("parser_build_id"), f"{family}.parser_build_id")
        if runs[family] != item["ingest_run_id"] or contracts[family] != item["family_contract_version"]:
            raise BatchM5ERuntimeOracleError(f"M5E family run/contract map disagrees: {family}")
        if item.get("corpus_complete") is not False or not isinstance(item.get("coverage_level"), str) or not isinstance(item.get("operations"), list) or not item["operations"]:
            raise BatchM5ERuntimeOracleError(f"M5E family coverage/operations mismatch: {family}")
        primary = _text(item.get("primary_table"), f"{family}.primary_table")
        if primary != M5E_FAMILY_TABLES[family][0]:
            raise BatchM5ERuntimeOracleError(f"M5E family primary table mismatch: {family}")
        tables = item.get("tables")
        if not isinstance(tables, list) or len(tables) != len(M5E_FAMILY_TABLES[family]):
            raise BatchM5ERuntimeOracleError(f"M5E family table count mismatch: {family}")
        for ordinal, table in enumerate(tables):
            _validate_table_spec(table, family, ordinal)
        seal = _mapping(item.get("business_seal"), f"{family}.business_seal")
        _sha(seal.get("business_rows"), f"{family}.business_seal.business_rows")
        _sha(seal.get("provenance"), f"{family}.business_seal.provenance")
        if seal.get("provenance") != provenance_by_name[M5E_FAMILY_WAVE[family]]["sha256"]:
            raise BatchM5ERuntimeOracleError(f"M5E family provenance binding mismatch: {family}")
    flat = value.get("provenance_tables")
    if not isinstance(flat, list) or len(flat) != 2 * len(M5E_PROVENANCE_TABLES):
        raise BatchM5ERuntimeOracleError("M5E flattened provenance boundary mismatch")
    return value


def _db_identity(path: Path) -> tuple[int, int, int, int]:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise BatchM5ERuntimeOracleError(f"database is unavailable: {path}") from exc
    if not os.path.isfile(path) or path.is_symlink():
        raise BatchM5ERuntimeOracleError("database must be a regular non-symlink file")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _check_sidecars(path: Path) -> None:
    for suffix in ("-journal", "-wal", "-shm"):
        if os.path.lexists(Path(f"{path}{suffix}")):
            raise BatchM5ERuntimeOracleError(f"SQLite sidecar present: {path}{suffix}")


def _verify_schema17(conn: sqlite3.Connection, structural: Mapping[str, Any] | None = None) -> dict[str, Any]:
    try:
        versions = tuple(int(row[0]) for row in conn.execute("SELECT version FROM schema_migration ORDER BY version"))
        ledger = {int(row[0]): str(row[1]) for row in conn.execute("SELECT version,applied_at FROM schema_migration WHERE version>=14")}
    except (sqlite3.Error, TypeError, ValueError) as exc:
        raise BatchM5ERuntimeOracleError("Schema17 migration ledger is unavailable") from exc
    if versions != tuple(range(1, M5E_SCHEMA_VERSION + 1)):
        raise BatchM5ERuntimeOracleError("Schema17 migration ledger is not exact 1..17")
    expected_ledger = {14: M5E_MIGRATION_0014_APPLIED_AT, 15: M5E_MIGRATION_0015_APPLIED_AT, 16: M5E_MIGRATION_0016_APPLIED_AT, 17: M5E_MIGRATION_0017_APPLIED_AT}
    if ledger != expected_ledger:
        raise BatchM5ERuntimeOracleError("Schema17 migration timestamps are not pinned")
    migration = ROOT / "migrations/0017_batch_wave02_family_projection.sql"
    if _sha256_file(migration) != M5E_MIGRATION_0017_SHA256:
        raise BatchM5ERuntimeOracleError("migration 0017 raw SQL seal mismatch")
    result = {"migration_0017_sha256": M5E_MIGRATION_0017_SHA256}
    if structural:
        try:
            from .db import Database
            mode = str(Database._stage_structural_snapshot(conn)["fts"]["mode"])
            signature = Database._stage_structural_signature(conn)
        except (sqlite3.Error, KeyError, TypeError, ValueError) as exc:
            raise BatchM5ERuntimeOracleError("Schema17 structural signature is unavailable") from exc
        if structural.get("fts_mode") not in (None, mode) or structural.get("structural_signature") not in (None, signature):
            raise BatchM5ERuntimeOracleError("Schema17 structural signature mismatch")
        result.update({"fts_mode": mode, "structural_signature": signature})
    return result


def _json_equal(raw: Any, expected: Any, label: str) -> None:
    try:
        actual = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BatchM5ERuntimeOracleError(f"{label} is invalid JSON") from exc
    if actual != expected:
        raise BatchM5ERuntimeOracleError(f"{label} mismatch")


def _provenance_rows(conn: sqlite3.Connection, table: str, wave_id: str, columns: Sequence[str]) -> list[tuple[Any, ...]]:
    selected = ",".join(_q(column) for column in columns)
    if table == "batch_wave":
        query = f"SELECT {selected} FROM {_q(table)} WHERE id=? ORDER BY id"
        params = (wave_id,)
    elif table == "batch_table_chunk_digest":
        selected = ",".join(f"c.{_q(column)}" for column in columns)
        query = f"SELECT {selected} FROM {_q(table)} c JOIN batch_table_digest d ON d.id=c.table_digest_id WHERE d.wave_id=? ORDER BY c.id"
        params = (wave_id,)
    else:
        query = f"SELECT {selected} FROM {_q(table)} WHERE wave_id=? ORDER BY id"
        params = (wave_id,)
    try:
        return [tuple(row) for row in conn.execute(query, params)]
    except sqlite3.Error as exc:
        raise BatchM5ERuntimeOracleError(f"provenance table is unavailable: {table}") from exc


def _verify_provenance_wave(conn: sqlite3.Connection, source_wave: Mapping[str, Any], provenance: Mapping[str, Any]) -> str:
    wave_id = str(source_wave["wave_id"])
    source_entries = _validate_provenance_entries(source_wave["provenance_tables"], f"{source_wave['name']}.source_provenance")
    expected_entries = _validate_provenance_entries(provenance["tables"], f"{source_wave['name']}.provenance")
    if source_entries != expected_entries:
        raise BatchM5ERuntimeOracleError(f"{source_wave['name']} source/combined provenance mismatch")
    actual_entries: list[dict[str, Any]] = []
    for expected in expected_entries:
        table = str(expected["table"])
        actual_columns = tuple(str(row[1]) for row in conn.execute(f"PRAGMA table_info({_q(table)})"))
        if tuple(expected["columns"]) != actual_columns:
            raise BatchM5ERuntimeOracleError(f"{source_wave['name']} provenance columns mismatch: {table}")
        rows = _provenance_rows(conn, table, wave_id, actual_columns)
        actual = {"table": table, "columns": list(actual_columns), "scope": expected["scope"], "row_count": len(rows), "sha256": _digest_rows(rows)}
        if actual != expected:
            raise BatchM5ERuntimeOracleError(f"{source_wave['name']} provenance digest mismatch: {table}")
        actual_entries.append(actual)
    digest = _wave_provenance_digest(str(source_wave["name"]), wave_id, actual_entries)
    if digest != provenance.get("sha256"):
        raise BatchM5ERuntimeOracleError(f"{source_wave['name']} provenance seal mismatch")
    return digest


def _verify_source_wave_row(conn: sqlite3.Connection, source_wave: Mapping[str, Any]) -> None:
    row = conn.execute("SELECT snapshot_id,contract_version,manifest_sha256,target_schema_version,status,metadata_json FROM batch_wave WHERE id=?", (source_wave["wave_id"],)).fetchone()
    metadata = source_wave.get("wave_metadata")
    if row is None or tuple(row[:5]) != (source_wave["snapshot_id"], source_wave["wave_contract"], source_wave["wave_manifest_sha256"], source_wave["schema_version"], "complete"):
        raise BatchM5ERuntimeOracleError(f"{source_wave['name']} batch_wave exact binding mismatch")
    _json_equal(row[5], metadata, f"{source_wave['name']}.batch_wave.metadata_json")


def _schema_sql_sha256(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if row is None or not isinstance(row[0], str):
        raise BatchM5ERuntimeOracleError(f"table schema is unavailable: {table}")
    return schema_digest(str(row[0]))


def _chunk_summaries(rows: Sequence[Sequence[Any]], columns: Sequence[str], ordering: Sequence[str], chunk_size: int) -> list[dict[str, Any]]:
    indexes = tuple(columns.index(column) for column in ordering)
    result: list[dict[str, Any]] = []
    for ordinal, start in enumerate(range(0, len(rows), chunk_size)):
        chunk = rows[start:start + chunk_size]
        result.append({
            "chunk_ordinal": ordinal,
            "first_key": [chunk[0][index] for index in indexes],
            "last_key": [chunk[-1][index] for index in indexes],
            "row_count": len(chunk),
            "logical_row_sha256": _digest_rows(chunk),
        })
    return result


def _verify_table(conn: sqlite3.Connection, family: str, item: Mapping[str, Any], table: Mapping[str, Any], run_id: str, snapshot_id: str, parser_build_id: str) -> dict[str, Any]:
    name = str(table["table_name"])
    columns = tuple(str(column) for column in table["columns"])
    ordering = tuple(str(column) for column in table["ordering"])
    actual_columns = tuple(str(row[1]) for row in conn.execute(f"PRAGMA table_info({_q(name)})"))
    target_columns = tuple(str(column) for column in table.get("target_columns", ()))
    target_only = tuple(str(column) for column in table.get("target_only_columns", ()))
    if target_columns:
        if actual_columns != target_columns:
            raise BatchM5ERuntimeOracleError(f"{family}.{name} target columns mismatch")
    elif name == "avatar_codex_opaque_occurrence" and set(actual_columns) == set(columns) | {"array_index"}:
        if "array_index" not in target_only:
            target_only = ("array_index",)
    elif name == "avatar_codex_raw_field_occurrence" and set(actual_columns) == set(columns):
        pass
    elif actual_columns != columns:
        raise BatchM5ERuntimeOracleError(f"{family}.{name} columns mismatch")
    if any(column not in actual_columns for column in columns + target_only):
        raise BatchM5ERuntimeOracleError(f"{family}.{name} selected column missing")
    if _schema_sql_sha256(conn, name) != table.get("schema_sql_sha256"):
        raise BatchM5ERuntimeOracleError(f"{family}.{name} schema digest mismatch")
    selected = ",".join(_q(column) for column in columns)
    order = ",".join(_q(column) for column in ordering)
    try:
        rows = [tuple(row) for row in conn.execute(f"SELECT {selected} FROM {_q(name)} WHERE ingest_run_id=? ORDER BY {order}", (run_id,))]
    except sqlite3.Error as exc:
        raise BatchM5ERuntimeOracleError(f"{family}.{name} rowset query failed") from exc
    if len(rows) != table["row_count"] or _digest_rows(rows) != table["logical_row_sha256"]:
        raise BatchM5ERuntimeOracleError(f"{family}.{name} rowset digest mismatch")
    chunks = _chunk_summaries(rows, columns, ordering, int(table["chunk_size"]))
    expected_chunks = table["chunks"]
    for expected, actual in zip(expected_chunks, chunks):
        for key in ("chunk_ordinal", "first_key", "last_key", "row_count", "logical_row_sha256"):
            if expected.get(key) != actual[key]:
                raise BatchM5ERuntimeOracleError(f"{family}.{name} chunk digest mismatch")
    for column, expected in (("family", family), ("family_contract_version", item["family_contract_version"]), ("snapshot_id", snapshot_id), ("parser_build_id", parser_build_id), ("ingest_run_id", run_id)):
        if column in actual_columns and conn.execute(f"SELECT 1 FROM {_q(name)} WHERE {_q(column)}<>? LIMIT 1", (expected,)).fetchone() is not None:
            raise BatchM5ERuntimeOracleError(f"{family}.{name} provenance column mismatch: {column}")
    if target_only:
        coordinate = _mapping(table.get("target_coordinate_seal"), f"{family}.{name}.target_coordinate_seal")
        if coordinate.get("columns") != list(target_only):
            raise BatchM5ERuntimeOracleError(f"{family}.{name} target coordinate columns mismatch")
        coordinate_columns = columns + target_only
        coordinate_select = ",".join(_q(column) for column in coordinate_columns)
        coordinate_order = ",".join(_q(column) for column in columns)
        coordinate_rows = [tuple(row) for row in conn.execute(f"SELECT {coordinate_select} FROM {_q(name)} WHERE ingest_run_id=? ORDER BY {coordinate_order}", (run_id,))]
        if _digest_rows(coordinate_rows) != coordinate.get("rowset_sha256"):
            raise BatchM5ERuntimeOracleError(f"{family}.{name} target coordinate digest mismatch")
        for column in target_only:
            null_count = int(conn.execute(f"SELECT COUNT(*) FROM {_q(name)} WHERE ingest_run_id=? AND {_q(column)} IS NULL", (run_id,)).fetchone()[0])
            total = len(rows)
            if coordinate.get("null_counts", {}).get(column) != null_count or coordinate.get("non_null_counts", {}).get(column) != total - null_count:
                raise BatchM5ERuntimeOracleError(f"{family}.{name} target coordinate counts mismatch")
    return {"table_name": name, "row_count": len(rows), "logical_row_sha256": _digest_rows(rows), "schema_sql_sha256": table["schema_sql_sha256"]}


def business_rows_seal(family: str, tables: Sequence[Mapping[str, Any]]) -> str:
    """Compute a family-local business seal from physical table rowset seals."""
    value = {
        "algorithm": M5E_BUSINESS_SEAL_ALGORITHM,
        "family": family,
        "tables": [
            {"table_name": item["table_name"], "row_count": item["row_count"], "logical_row_sha256": item["logical_row_sha256"]}
            for item in tables
        ],
    }
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def _family_seal(family: str, table_summaries: Sequence[Mapping[str, Any]], provenance_sha: str) -> dict[str, Any]:
    return {
        "business_rows": business_rows_seal(family, table_summaries),
        "provenance": provenance_sha,
        "tables": [
            {"table_name": item["table_name"], "row_count": item["row_count"], "logical_row_sha256": item["logical_row_sha256"]}
            for item in table_summaries
        ],
        "row_count": table_summaries[0]["row_count"],
    }


def _verify_family_provenance(conn: sqlite3.Connection, family: str, item: Mapping[str, Any], wave: Mapping[str, Any], tables: Sequence[Mapping[str, Any]]) -> None:
    provenance = _mapping(item.get("provenance"), f"{family}.provenance")
    wave_id = str(wave["wave_id"])
    source_schema_version = wave.get("schema_version")
    if isinstance(source_schema_version, bool) or not isinstance(source_schema_version, int):
        raise BatchM5ERuntimeOracleError(
            f"{family} source wave schema version is invalid"
        )
    shard_id = provenance.get("shard_id")
    if not isinstance(shard_id, str) or not shard_id:
        raise BatchM5ERuntimeOracleError(f"{family} shard provenance id is missing")
    shard = conn.execute("SELECT wave_id,family,family_contract_version,ingest_run_id,manifest_sha256,status,rowset_sha256 FROM batch_shard WHERE id=?", (shard_id,)).fetchone()
    if shard is None or tuple(shard[:6]) != (wave_id, family, item["family_contract_version"], item["ingest_run_id"], wave["wave_manifest_sha256"], "imported"):
        raise BatchM5ERuntimeOracleError(f"{family} batch_shard provenance mismatch")
    shard_rowset = item.get("shard_rowset_sha256")
    if isinstance(shard_rowset, str) and shard[6] != shard_rowset:
        raise BatchM5ERuntimeOracleError(f"{family} shard rowset provenance mismatch")
    import_id = provenance.get("import_id")
    if not isinstance(import_id, str) or not import_id:
        raise BatchM5ERuntimeOracleError(f"{family} import provenance id is missing")
    imported = conn.execute("SELECT wave_id,shard_id,target_schema_version,imported_row_count,status FROM batch_shard_import WHERE id=?", (import_id,)).fetchone()
    expected_count = sum(int(table["row_count"]) for table in tables)
    if imported is None or tuple(imported) != (wave_id, shard_id, source_schema_version, expected_count, "complete"):
        raise BatchM5ERuntimeOracleError(f"{family} batch_shard_import provenance mismatch")
    table_ids = provenance.get("table_digest_ids")
    if not isinstance(table_ids, list) or tuple(table_ids) != tuple(table.get("table_digest_id") for table in tables):
        raise BatchM5ERuntimeOracleError(f"{family} table digest identity mismatch")
    for table, digest_id in zip(tables, table_ids):
        row = conn.execute("SELECT wave_id,shard_id,table_name,schema_sql_sha256,row_count,logical_row_sha256,chunk_size,chunk_count FROM batch_table_digest WHERE id=?", (digest_id,)).fetchone()
        if row is None or tuple(row) != (wave_id, shard_id, table["table_name"], table["schema_sql_sha256"], table["row_count"], table["logical_row_sha256"], table["chunk_size"], table["chunk_count"]):
            raise BatchM5ERuntimeOracleError(f"{family} batch_table_digest provenance mismatch: {table['table_name']}")


@dataclass(frozen=True)
class M5EFileStability:
    """Identity captured for one explicit M5E release input."""

    path: str
    device: int
    inode: int
    size_bytes: int
    mtime_ns: int
    sha256: str | None
    hash_content: bool = True

    @classmethod
    def capture(
        cls, path: str | Path, *, hash_content: bool = True
    ) -> "M5EFileStability":
        value = Path(path).absolute()
        try:
            info = os.lstat(value)
        except OSError as exc:
            raise BatchM5ERuntimeOracleError(
                f"M5E stability input is unavailable: {value}"
            ) from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise BatchM5ERuntimeOracleError(
                "M5E stability input must be a regular non-symlink file"
            )
        _check_sidecars(value)
        return cls(
            path=str(value),
            device=int(info.st_dev),
            inode=int(info.st_ino),
            size_bytes=int(info.st_size),
            mtime_ns=int(info.st_mtime_ns),
            sha256=_sha256_file(value) if hash_content else None,
            hash_content=hash_content,
        )

    def check(self) -> None:
        current = self.capture(self.path, hash_content=self.hash_content)
        if current != self:
            raise BatchM5ERuntimeOracleError(
                f"M5E stability input changed during qualification: {self.path}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "device": self.device,
            "inode": self.inode,
            "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns,
            "sha256": self.sha256,
            "hash_content": self.hash_content,
            "sidecars_absent": True,
        }


@dataclass(frozen=True)
class M5EStabilitySnapshot:
    """Stable identities for one explicit M5E qualification invocation."""

    database: M5EFileStability
    receipt: M5EFileStability
    runtime_manifest: M5EFileStability
    release_report: M5EFileStability | None
    companion_receipts: Mapping[str, M5EFileStability]

    @classmethod
    def capture(
        cls,
        database: str | Path,
        receipt: str | Path,
        runtime_manifest: str | Path,
        *,
        release_report: str | Path | None = None,
        companion_receipts: Mapping[str, str | Path] | None = None,
    ) -> "M5EStabilitySnapshot":
        companions = companion_receipts or {}
        return cls(
            # The candidate SHA is already streamed once by qualify_batch_m5e.
            # Re-hashing a 5+ GiB database for every stability check would
            # defeat the shared startup hash fast path; database stability is
            # therefore inode/mtime/size plus sidecar state only.
            database=M5EFileStability.capture(database, hash_content=False),
            receipt=M5EFileStability.capture(receipt),
            runtime_manifest=M5EFileStability.capture(runtime_manifest),
            release_report=(
                M5EFileStability.capture(release_report)
                if release_report is not None else None
            ),
            companion_receipts={
                str(name): M5EFileStability.capture(path)
                for name, path in sorted(companions.items())
            },
        )

    def check(self) -> None:
        self.database.check()
        self.receipt.check()
        self.runtime_manifest.check()
        if self.release_report is not None:
            self.release_report.check()
        for item in self.companion_receipts.values():
            item.check()

    def to_dict(self) -> dict[str, Any]:
        return {
            "database": self.database.to_dict(),
            "receipt": self.receipt.to_dict(),
            "runtime_manifest": self.runtime_manifest.to_dict(),
            "release_report": (
                self.release_report.to_dict()
                if self.release_report is not None else None
            ),
            "companion_receipts": {
                name: item.to_dict()
                for name, item in self.companion_receipts.items()
            },
        }


@dataclass(frozen=True)
class M5EQualification:
    status: str
    database_path: str
    manifest_path: str
    receipt_path: str
    manifest_sha256: str
    receipt_id: str
    candidate_sha256: str | None
    candidate_size_bytes: int | None
    product_accepted: bool
    family_row_counts: Mapping[str, int]
    family_seals: Mapping[str, Mapping[str, Any]]
    structural_seal: Mapping[str, Any]
    stability: M5EStabilitySnapshot
    row_audit_verified: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "database_path": self.database_path,
            "manifest_path": self.manifest_path,
            "receipt_path": self.receipt_path,
            "manifest_sha256": self.manifest_sha256,
            "receipt_id": self.receipt_id,
            "candidate_sha256": self.candidate_sha256,
            "candidate_size_bytes": self.candidate_size_bytes,
            "product_accepted": self.product_accepted,
            "family_row_counts": dict(self.family_row_counts),
            "family_seals": {key: dict(value) for key, value in self.family_seals.items()},
            "structural_seal": dict(self.structural_seal),
            "stability": self.stability.to_dict(),
            "candidate_hash_verified_once": self.candidate_sha256 is not None,
            "row_audit_verified_once": self.row_audit_verified,
            "identity_policy": M5E_IDENTITY_POLICY,
        }


def qualify_batch_m5e(
    db: ReadonlyDatabase | str | Path,
    *,
    manifest_path: str | Path = M5E_RUNTIME_MANIFEST_PATH,
    receipt_path: str | Path = M5E_RECEIPT_PATH,
    report_path: str | Path | None = None,
    companion_paths: Mapping[str, str | Path] | None = None,
    hash_candidate: bool = True,
    structural_seal: Mapping[str, Any] | None = None,
    release_validation: bool = True,
) -> M5EQualification:
    """Qualify one explicit bundle. Full release proof remains opt-in for runtime."""
    manifest = load_runtime_manifest(manifest_path)
    from .batch_m5e_receipt import load_release_receipt
    database_path = db.path if isinstance(db, ReadonlyDatabase) else Path(db)
    candidate = manifest.get("candidate")
    candidate_identity: dict[str, Any] | None = None
    if isinstance(candidate, Mapping):
        candidate_size = _int(candidate.get("size_bytes"), "candidate.size_bytes", minimum=1)
        candidate_sha = _sha(candidate.get("sha256"), "candidate.sha256")
        candidate_identity = {"size_bytes": candidate_size, "sha256": candidate_sha}
        # This checks regular-file identity and sidecars without hashing the
        # large candidate.  The candidate SHA is streamed exactly once below.
        M5EFileStability.capture(database_path, hash_content=False)
    receipt = load_release_receipt(
        receipt_path,
        database_path,
        require_accepted=False,
        report_path=report_path,
        companion_paths=companion_paths,
        # Preserve the explicit ``hash_candidate=False`` escape hatch's
        # former receipt-loader verification. The normal qualification path
        # prevalidates the manifest identity and performs the one actual DB
        # stream below; callers that disable candidate hashing retain the
        # loader's default exact-file check instead.
        _prevalidated_database_identity=(
            candidate_identity if hash_candidate else None
        ),
    )
    if receipt["wave_manifest_sha256"] != manifest["manifest_sha256"] or receipt["runtime_manifest_sha256"] != manifest["manifest_sha256"]:
        raise BatchM5ERuntimeOracleError("M5E receipt is not bound to the tracked manifest")
    if receipt.get("wave_id") != manifest.get("wave", {}).get("id"):
        raise BatchM5ERuntimeOracleError(
            "M5E receipt wave id is not bound to the runtime manifest"
        )
    if receipt["product_accepted"] is not manifest["product_accepted"]:
        raise BatchM5ERuntimeOracleError("M5E receipt/manifest acceptance status mismatch")
    if receipt["family_runs"] != manifest["family_runs"] or receipt["family_contracts"] != manifest["family_contracts"]:
        raise BatchM5ERuntimeOracleError("M5E receipt family run/contract binding mismatch")
    selected_report = Path(report_path) if report_path is not None else None
    if selected_report is None:
        report_binding = receipt.get("report")
        if isinstance(report_binding, Mapping) and isinstance(report_binding.get("filename"), str):
            selected_report = Path(receipt_path).absolute().parent / str(report_binding["filename"])
    stability = M5EStabilitySnapshot.capture(
        database_path,
        receipt_path,
        manifest_path,
        release_report=selected_report,
        companion_receipts=companion_paths,
    )
    own_db = not isinstance(db, ReadonlyDatabase)
    database = open_readonly_database(db) if own_db else db
    try:
        _check_sidecars(database.path)
        candidate_sha: str | None = None
        candidate_size: int | None = None
        if candidate is not None:
            candidate_value = _mapping(candidate, "candidate")
            candidate_size = _int(candidate_value.get("size_bytes"), "candidate.size_bytes", minimum=1)
            candidate_sha = _sha(candidate_value.get("sha256"), "candidate.sha256")
            identity = _db_identity(database.path)
            if identity[2] != candidate_size:
                raise BatchM5ERuntimeOracleError("M5E candidate size seal mismatch")
            if hash_candidate and _sha256_file(database.path) != candidate_sha:
                raise BatchM5ERuntimeOracleError("M5E candidate SHA-256 seal mismatch")
            if receipt["database"]["size_bytes"] != candidate_size or receipt["database"]["sha256"] != candidate_sha:
                raise BatchM5ERuntimeOracleError("M5E receipt/database candidate seal mismatch")
        conn = database.conn
        structural_value = structural_seal if structural_seal is not None else manifest.get("structural_seal")
        if not release_validation:
            versions = [int(row[0]) for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")]
            if versions != list(range(1, M5E_SCHEMA_VERSION + 1)):
                raise BatchM5ERuntimeOracleError("M5E startup migration ledger is not exact 1..17")
            actual_tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required_tables = set(M5E_PROVENANCE_TABLES) | {table for tables in M5E_FAMILY_TABLES.values() for table in tables}
            missing_tables = sorted(required_tables - actual_tables)
            if missing_tables:
                raise BatchM5ERuntimeOracleError(f"M5E startup required tables are missing: {missing_tables}")
            family_counts = {str(item["family"]): int(item["tables"][0]["row_count"]) for item in manifest["families"]}
            family_seals = {str(item["family"]): item["business_seal"] for item in manifest["families"]}
            structural = dict(structural_value) if isinstance(structural_value, Mapping) else {}
            stability.check()
            return M5EQualification(
                status="verified", database_path=str(database.path),
                manifest_path=str(Path(manifest_path).resolve()), receipt_path=str(Path(receipt_path).resolve()),
                manifest_sha256=manifest["manifest_sha256"], receipt_id=receipt["receipt_id"],
                candidate_sha256=candidate_sha, candidate_size_bytes=candidate_size,
                product_accepted=bool(receipt["product_accepted"]), family_row_counts=family_counts,
                family_seals=family_seals, structural_seal=structural, stability=stability,
                row_audit_verified=False,
            )
        structural = _verify_schema17(conn, structural_value if isinstance(structural_value, Mapping) else None)
        source = _mapping(manifest["source"], "source")
        for source_wave in manifest["source_waves"]:
            _verify_source_wave_row(conn, source_wave)
        provenance_by_name = {item["name"]: item for item in manifest["provenance_waves"]}
        for source_wave in manifest["source_waves"]:
            _verify_provenance_wave(conn, source_wave, provenance_by_name[source_wave["name"]])
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise BatchM5ERuntimeOracleError("M5E foreign_key_check is not clean")
        family_counts: dict[str, int] = {}
        family_seals: dict[str, Mapping[str, Any]] = {}
        receipt_seals = receipt.get("family_seals", {})
        for family, item in zip(M5E_FAMILY_ORDER, manifest["families"]):
            wave_name = M5E_FAMILY_WAVE[family]
            source_wave = next(entry for entry in manifest["source_waves"] if entry["name"] == wave_name)
            tables = item["tables"]
            summaries = [_verify_table(conn, family, item, table, item["ingest_run_id"], source["snapshot_id"], item["parser_build_id"]) for table in tables]
            _verify_family_provenance(conn, family, item, source_wave, tables)
            actual_seal = _family_seal(family, summaries, provenance_by_name[wave_name]["sha256"])
            expected_seal = item["business_seal"]
            if actual_seal != expected_seal:
                raise BatchM5ERuntimeOracleError(f"M5E business seal mismatch: {family}")
            if receipt_seals and receipt_seals.get(family) != actual_seal:
                raise BatchM5ERuntimeOracleError(f"M5E receipt family seal mismatch: {family}")
            family_counts[family] = summaries[0]["row_count"]
            family_seals[family] = actual_seal
        stability.check()
        return M5EQualification(
            status="verified",
            database_path=str(database.path),
            manifest_path=str(Path(manifest_path).resolve()),
            receipt_path=str(Path(receipt_path).resolve()),
            manifest_sha256=manifest["manifest_sha256"],
            receipt_id=receipt["receipt_id"],
            candidate_sha256=candidate_sha,
            candidate_size_bytes=candidate_size,
            product_accepted=bool(receipt["product_accepted"]),
            family_row_counts=family_counts,
            family_seals=family_seals,
            structural_seal=structural,
            stability=stability,
        )
    finally:
        if own_db:
            database.close()


def verify_batch_m5e_runtime(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Return a serializable qualification summary for the explicit bundle."""
    return qualify_batch_m5e(*args, **kwargs).to_dict()


class BatchM5ERuntimeOracle:
    """Reusable startup qualification snapshot for future routed providers."""

    def __init__(self, db: ReadonlyDatabase | str | Path, **kwargs: Any) -> None:
        self.qualification = qualify_batch_m5e(db, **kwargs)

    def verify(self) -> dict[str, Any]:
        return self.qualification.to_dict()


__all__ = [
    "BatchM5ERuntimeOracleError", "M5EFileStability", "M5EStabilitySnapshot",
    "M5EQualification", "BatchM5ERuntimeOracle",
    "M5E_SCHEMA_VERSION", "M5E_SCHEMA_CONTRACT", "M5E_MANIFEST_ID",
    "M5E_MANIFEST_CONTRACT", "M5E_MANIFEST_VERSION", "M5E_RUNTIME_MANIFEST_PATH",
    "M5E_RECEIPT_PATH", "M5E_FAMILY_ORDER", "M5E_FAMILY_TABLES", "M5E_FAMILY_WAVE",
    "M5E_PROVENANCE_TABLES", "M5E_WAVE_NAMES", "business_rows_seal",
    "canonical_manifest_sha256", "load_runtime_manifest", "qualify_batch_m5e",
    "verify_batch_m5e_runtime",
]
