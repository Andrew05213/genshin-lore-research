"""M5D Schema 17 candidate runtime qualification and family declarations.

M5D is deliberately a non-product candidate boundary. This module owns the
single startup qualification for the one Schema 17 candidate and the literal
eleven family declarations consumed by later routed repositories. It does not
issue receipts, run stdio acceptance, or promote the candidate.
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
from .db import Database
from .family_projection_protocol import canonical_row, schema_digest
from .runtime_stability import RuntimeStabilityError
from .util import canonical_json, sha256_bytes

ROOT = Path(__file__).resolve().parents[2]
M5D_SCHEMA_VERSION = 17
M5D_SCHEMA_CONTRACT = "schema17-migration0017-additive-wave02-exact"
M5D_REPORT_VERSION = "batch-m5d-wave02-runtime-v1"
M5D_COVERAGE = "R2_wave_candidate"
M5D_WAVE_CONTRACT = "batch-wave-v4"
M5D_MANIFEST_ID = "anime-game-data-7.0-batch-m5d-wave02-runtime"
M5D_MANIFEST_VERSION = "batch-m5d-wave02-runtime-v1"
M5D_INDEPENDENT_REPORT_VERSION = "batch-m5d-wave02-independent-verification-v1"
M5D_REPORT_PATH = ROOT / "reports/batch-m5d/batch-m5d-report.json"
M5D_RUNTIME_MANIFEST_PATH = ROOT / "manifests/batch/anime-game-data-7.0-batch-m5d-wave02-runtime.json"
M5D_INDEPENDENT_REPORT_PATH = ROOT / "reports/batch-m5d/batch-m5d-independent-verification.json"
M5D_CANDIDATE_PATH = Path("/tmp/phase-m5d-wave02-20260905/schema17-wave02-candidate-r3.sqlite")
M5D_SPEC_DIR = ROOT / "reports/batch-m5c/repository-specs"
M5D_FAMILY_ORDER = (
    "avatar_codex_archive", "cook_recipe_archive", "cook_bonus_archive",
    "homeworld_furniture_archive", "furniture_make_archive", "furniture_suite_archive",
    "homeworld_furniture_type_archive", "homeworld_bgm_archive",
    "profile_picture_archive", "profile_frame_archive", "gadget_title_archive",
)
M5D_PROVENANCE_TABLES = (
    "batch_wave", "batch_shard", "batch_shard_import",
    "batch_table_digest", "batch_table_chunk_digest",
)
M5D_MIGRATION_0014_APPLIED_AT = "2026-09-01T00:00:00+00:00"
M5D_MIGRATION_0015_APPLIED_AT = "2026-09-02T00:00:00+00:00"
M5D_MIGRATION_0016_APPLIED_AT = "2026-09-03T00:00:00+00:00"
M5D_MIGRATION_0017_APPLIED_AT = "2026-09-05T00:00:00+00:00"
M5D_MIGRATION_0017_SHA256 = "ec8e41402b0c07b5b94aece97607a066c452de12d65c2ee0c49f486df9474c36"
M5D_SCHEMA17_STRUCTURAL_SIGNATURES = {
    "fts5": "db37af15c04ce68027bdb6a1fa2c04acee414d00449789638081284d39a3859b",
    "fallback": "8c7055fdebb0c4d57bf099bc56c1a98f0b06cd35b2287fb4bac6bcb93e9e9844",
}
M5D_REGISTRY_FLAGS = {
    "family_provider_registry_changed": True,
    "top_level_tool_registry_changed": False,
    "top_level_tool_count": 25,
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class BatchM5DError(RuntimeError):
    """The M5D report, candidate, or family boundary is not qualified."""


@dataclass(frozen=True)
class M5DChildTableDeclaration:
    table: str
    link_column: str
    parent_table: str | None = None
    parent_id_column: str = "id"
    parent_link_column: str = "row_occurrence_id"


# Literal family table sets: no table-name convention is used as an identity
# or relation adapter.
M5D_FAMILY_TABLES: dict[str, tuple[str, ...]] = {
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
M5D_PRIMARY_TABLES = {family: tables[0] for family, tables in M5D_FAMILY_TABLES.items()}
M5D_PRIMARY_ID_COLUMNS = {
    "avatar_codex_archive": "id", "cook_recipe_archive": "occurrence_id", "cook_bonus_archive": "occurrence_id",
    "homeworld_furniture_archive": "occurrence_id", "furniture_make_archive": "occurrence_id",
    "furniture_suite_archive": "occurrence_id", "homeworld_furniture_type_archive": "occurrence_id",
    "homeworld_bgm_archive": "occurrence_id", "profile_picture_archive": "occurrence_id",
    "profile_frame_archive": "occurrence_id", "gadget_title_archive": "occurrence_id",
}
M5D_RAW_RECORD_MODES = {
    "avatar_codex_archive": "metadata_json.raw_record_json",
    **{family: "raw_record_json" for family in M5D_FAMILY_ORDER if family != "avatar_codex_archive"},
}
M5D_SEARCH_TABLES: dict[str, tuple[str, str, str] | None] = {
    "avatar_codex_archive": None,
    "cook_recipe_archive": ("cook_recipe_archive_text_owner_occurrence", "cook_recipe_archive_text_resolution_occurrence", "cook_recipe_archive_text_candidate_occurrence"),
    "cook_bonus_archive": None,
    "homeworld_furniture_archive": ("homeworld_furniture_archive_text_owner_occurrence", "homeworld_furniture_archive_text_resolution_occurrence", "homeworld_furniture_archive_text_candidate_occurrence"),
    "furniture_make_archive": None,
    "furniture_suite_archive": ("furniture_suite_archive_text_owner_occurrence", "furniture_suite_archive_text_resolution_occurrence", "furniture_suite_archive_text_candidate_occurrence"),
    "homeworld_furniture_type_archive": ("homeworld_furniture_type_archive_text_owner_occurrence", "homeworld_furniture_type_archive_text_resolution_occurrence", "homeworld_furniture_type_archive_text_candidate_occurrence"),
    "homeworld_bgm_archive": ("homeworld_bgm_archive_text_owner_occurrence", "homeworld_bgm_archive_text_resolution_occurrence", "homeworld_bgm_archive_text_candidate_occurrence"),
    "profile_picture_archive": ("profile_picture_archive_text_owner_occurrence", "profile_picture_archive_text_resolution_occurrence", "profile_picture_archive_text_candidate_occurrence"),
    "profile_frame_archive": ("profile_frame_archive_text_owner_occurrence", "profile_frame_archive_text_resolution_occurrence", "profile_frame_archive_text_candidate_occurrence"),
    "gadget_title_archive": ("gadget_title_archive_text_owner_occurrence", "gadget_title_archive_text_resolution_occurrence", "gadget_title_archive_text_candidate_occurrence"),
}
M5D_SEARCH_ORDER = (
    "source_row_ordinal", "source_field_pointer", "locale", "relative_path", "pair_ordinal",
    "pointer", "raw_key_sha256", "raw_text_sha256", "raw_record_sha256",
)
M5D_CHILD_TABLES: dict[str, tuple[M5DChildTableDeclaration, ...]] = {
    "avatar_codex_archive": (
        M5DChildTableDeclaration("avatar_codex_raw_field_occurrence", "row_occurrence_id"),
        M5DChildTableDeclaration("avatar_codex_relationship_occurrence", "source_row_occurrence_id"),
        M5DChildTableDeclaration("avatar_codex_relationship_candidate_occurrence", "relationship_occurrence_id", "avatar_codex_relationship_occurrence", "id", "source_row_occurrence_id"),
        M5DChildTableDeclaration("avatar_codex_opaque_occurrence", "source_row_occurrence_id"),
    ),
}


@dataclass(frozen=True)
class M5DFamilyDeclaration:
    family: str
    family_contract_version: str
    coverage_level: str
    corpus_complete: bool
    ingest_run_id: str
    operations: tuple[str, ...]
    table_name: str
    filters: tuple[dict[str, Any], ...]
    sort: tuple[str, ...]
    search: Mapping[str, Any]
    table_names: tuple[str, ...]
    primary_id_column: str
    raw_record_mode: str


def _strict_json(path: Path, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise BatchM5DError(f"duplicate {label} key: {key}")
            result[key] = value
        return result
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise BatchM5DError(f"{label} is unavailable or invalid: {path}") from exc
    if not isinstance(value, dict):
        raise BatchM5DError(f"{label} must be an object")
    return value


def load_family_declarations(spec_dir: str | Path = M5D_SPEC_DIR) -> tuple[M5DFamilyDeclaration, ...]:
    """Load all eleven family-specific M5C operation declarations."""
    root = Path(spec_dir)
    result: list[M5DFamilyDeclaration] = []
    for family in M5D_FAMILY_ORDER:
        value = _strict_json(root / f"{family}.json", f"M5C {family} repository spec")
        if value.get("family") != family or value.get("status") != "implemented_non_product":
            raise BatchM5DError(f"M5C family boundary/status mismatch: {family}")
        contract = value.get("m5b_operation_contract")
        if not isinstance(contract, Mapping):
            raise BatchM5DError(f"M5C operation contract is missing: {family}")
        operations = contract.get("supported_operations")
        filters = contract.get("filters")
        if not isinstance(operations, list) or not all(isinstance(item, str) for item in operations):
            raise BatchM5DError(f"M5C operations are invalid: {family}")
        if not isinstance(filters, list) or not all(isinstance(item, dict) for item in filters):
            raise BatchM5DError(f"M5C filters are invalid: {family}")
        pagination = contract.get("pagination")
        cursor = pagination.get("cursor", {}) if isinstance(pagination, Mapping) else {}
        stable = cursor.get("stable_full_ordering", {}) if isinstance(cursor, Mapping) else {}
        sort = stable.get("list_family_evidence") if isinstance(stable, Mapping) else None
        if not isinstance(sort, list):
            sort = value.get("contract", {}).get("sort", [])
        if not isinstance(sort, list) or not all(isinstance(item, str) for item in sort):
            raise BatchM5DError(f"M5C ordering is invalid: {family}")
        tables = M5D_FAMILY_TABLES[family]
        text_tables = M5D_SEARCH_TABLES[family]
        if text_tables is not None and not all(item in tables for item in text_tables):
            raise BatchM5DError(f"M5D text table boundary mismatch: {family}")
        result.append(M5DFamilyDeclaration(
            family=family,
            family_contract_version=str(value["family_contract_version"]),
            coverage_level=str(value["coverage_level"]),
            corpus_complete=bool(value.get("corpus_complete", False)),
            ingest_run_id=str(value["ingest_run_id"]),
            operations=tuple(operations),
            table_name=M5D_PRIMARY_TABLES[family],
            filters=tuple(dict(item) for item in filters),
            sort=tuple(sort),
            search=dict(contract.get("search", {})) if isinstance(contract.get("search"), Mapping) else {},
            table_names=tables,
            primary_id_column=M5D_PRIMARY_ID_COLUMNS[family],
            raw_record_mode=M5D_RAW_RECORD_MODES[family],
        ))
    return tuple(result)


@dataclass(frozen=True)
class M5DStabilitySnapshot:
    """Identity snapshot shared by all providers after qualification."""
    database_path: str
    database_identity: tuple[int, int, int, int]
    report_path: str
    report_identity: tuple[int, int, int, int]
    report_sha256: str
    manifest_path: str
    manifest_identity: tuple[int, int, int, int]
    manifest_sha256: str
    verification_path: str
    verification_identity: tuple[int, int, int, int]
    verification_sha256: str

    def check(self) -> None:
        for path, expected, label in (
            (Path(self.database_path), self.database_identity, "database"),
            (Path(self.report_path), self.report_identity, "report"),
            (Path(self.manifest_path), self.manifest_identity, "manifest"),
            (Path(self.verification_path), self.verification_identity, "verification"),
        ):
            if any(os.path.lexists(Path(f"{path}{suffix}")) for suffix in ("-journal", "-wal", "-shm")):
                raise RuntimeStabilityError(f"m5d_{label}_sidecar_present")
            try:
                info = os.lstat(path)
            except OSError as exc:
                raise RuntimeStabilityError(f"m5d_{label}_missing") from exc
            actual = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            if actual != expected or not os.path.isfile(path) or path.is_symlink():
                raise RuntimeStabilityError(f"m5d_{label}_identity_changed")
        for path, expected, label in (
            (Path(self.report_path), self.report_sha256, "report"),
            (Path(self.manifest_path), self.manifest_sha256, "manifest"),
            (Path(self.verification_path), self.verification_sha256, "verification"),
        ):
            if _sha256_file(path) != expected:
                raise RuntimeStabilityError(f"m5d_{label}_content_changed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "database_path": self.database_path, "database_identity": list(self.database_identity),
            "database_sidecars_absent": True, "report_path": self.report_path,
            "report_identity": list(self.report_identity), "report_sha256": self.report_sha256,
            "manifest_path": self.manifest_path, "manifest_identity": list(self.manifest_identity),
            "manifest_sha256": self.manifest_sha256, "verification_path": self.verification_path,
            "verification_identity": list(self.verification_identity), "verification_sha256": self.verification_sha256,
        }


@dataclass(frozen=True)
class M5DQualification:
    status: str
    report_path: str
    manifest_path: str
    verification_path: str
    candidate_path: str
    candidate_sha256: str
    candidate_size_bytes: int
    wave_id: str
    snapshot_id: str
    parser_build_id: str
    manifest_sha256: str
    report_sha256: str
    verification_sha256: str
    family_row_counts: Mapping[str, int]
    stability: M5DStabilitySnapshot
    report: Mapping[str, Any]
    manifest: Mapping[str, Any]
    verification: Mapping[str, Any]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
    except OSError as exc:
        raise BatchM5DError(f"cannot hash {path}") from exc
    return digest.hexdigest()


def _identity(path: Path, label: str) -> tuple[int, int, int, int]:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise BatchM5DError(f"{label} is unavailable: {path}") from exc
    if not os.path.isfile(path) or path.is_symlink():
        raise BatchM5DError(f"{label} must be a regular non-symlink file: {path}")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _q(identifier: str) -> str:
    if not isinstance(identifier, str) or not _IDENTIFIER.fullmatch(identifier):
        raise BatchM5DError(f"unsafe SQLite identifier: {identifier!r}")
    return '"' + identifier.replace('"', '""') + '"'


def _json_equal(raw: Any, expected: Any, label: str) -> None:
    try:
        actual = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BatchM5DError(f"{label} is invalid JSON") from exc
    if actual != expected:
        raise BatchM5DError(f"{label} mismatch")


def _digest_rows(rows: Sequence[Sequence[Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(canonical_row(tuple(row)).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _verify_provenance_table(conn: sqlite3.Connection, entry: Mapping[str, Any], wave_id: str) -> None:
    table = str(entry.get("table")); columns = entry.get("columns")
    if not isinstance(columns, list) or not columns:
        raise BatchM5DError(f"{table} provenance columns are invalid")
    actual_columns = tuple(str(row[1]) for row in conn.execute(f"PRAGMA table_info({_q(table)})"))
    if tuple(columns) != actual_columns:
        raise BatchM5DError(f"{table} provenance columns mismatch")
    selected = ",".join(_q(str(item)) for item in columns)
    if table == "batch_wave":
        rows = [tuple(row) for row in conn.execute(f"SELECT {selected} FROM {_q(table)} WHERE id=? ORDER BY id", (wave_id,))]
    elif table == "batch_table_chunk_digest":
        rows = [tuple(row) for row in conn.execute(
            f"SELECT {','.join('c.' + _q(str(item)) for item in columns)} FROM {_q(table)} c JOIN batch_table_digest d ON d.id=c.table_digest_id WHERE d.wave_id=? ORDER BY c.id", (wave_id,)
        )]
    else:
        rows = [tuple(row) for row in conn.execute(f"SELECT {selected} FROM {_q(table)} WHERE wave_id=? ORDER BY id", (wave_id,))]
    if entry.get("scope") != "m5d_wave" or entry.get("row_count") != len(rows) or entry.get("sha256") != _digest_rows(rows):
        raise BatchM5DError(f"{table} provenance digest mismatch")


def _verify_table_digest(conn: sqlite3.Connection, table: Mapping[str, Any], run_id: str) -> int:
    name = str(table.get("table_name")); columns = table.get("columns"); ordering = table.get("ordering")
    if not isinstance(columns, list) or not isinstance(ordering, list) or not columns or not ordering:
        raise BatchM5DError(f"{name} table digest shape is invalid")
    actual_columns = tuple(str(row[1]) for row in conn.execute(f"PRAGMA table_info({_q(name)})"))
    avatar_additive = name in {"avatar_codex_raw_field_occurrence", "avatar_codex_opaque_occurrence"}
    expected_set, actual_set = set(str(item) for item in columns), set(actual_columns)
    allowed_sets = {frozenset(expected_set)}
    if name == "avatar_codex_opaque_occurrence":
        allowed_sets.add(frozenset(expected_set | {"array_index"}))
    if avatar_additive:
        if frozenset(actual_set) not in allowed_sets or any(item not in actual_columns for item in ordering):
            raise BatchM5DError(f"{name} table columns/order mismatch")
    elif tuple(columns) != actual_columns or any(item not in actual_columns for item in ordering):
        raise BatchM5DError(f"{name} table columns/order mismatch")
    schema_row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    if schema_row is None:
        raise BatchM5DError(f"{name} schema is unavailable")
    if not avatar_additive and schema_digest(str(schema_row[0])) != table.get("schema_sql_sha256"):
        raise BatchM5DError(f"{name} schema digest mismatch")
    selected = ",".join(_q(str(item)) for item in columns); ordered = ",".join(_q(str(item)) for item in ordering)
    rows = [tuple(row) for row in conn.execute(f"SELECT {selected} FROM {_q(name)} WHERE ingest_run_id=? ORDER BY {ordered}", (run_id,))]
    if len(rows) != table.get("row_count") or _digest_rows(rows) != table.get("logical_row_sha256"):
        raise BatchM5DError(f"{name} rowset digest mismatch")
    if name == "avatar_codex_opaque_occurrence":
        target_columns = tuple(str(row[1]) for row in conn.execute(
            f"PRAGMA table_info({_q(name)})"
        ))
        target_only = sorted(set(target_columns) - set(str(item) for item in columns))
        if target_only != ["array_index"]:
            raise BatchM5DError("Avatar opaque target-only coordinate mismatch")
        total = int(conn.execute(
            f"SELECT COUNT(*) FROM {_q(name)} WHERE ingest_run_id=?", (run_id,)
        ).fetchone()[0])
        null_count = int(conn.execute(
            f"SELECT COUNT(*) FROM {_q(name)} WHERE ingest_run_id=? AND array_index IS NULL",
            (run_id,),
        ).fetchone()[0])
        if null_count != total:
            raise BatchM5DError("Avatar opaque array_index must be all NULL")
    return len(rows)


def _verify_source_rowset_oracle(
    verification: Mapping[str, Any],
    declarations: Sequence[M5DFamilyDeclaration],
    family_items: Sequence[Mapping[str, Any]],
) -> None:
    """Check the complete coordinate seal emitted by the independent oracle.

    Runtime qualification binds this evidence; it does not rehash the eleven
    source shards. Providers therefore reuse one sealed qualification snapshot
    while still refusing a verifier report that only contains builder digests.
    """
    oracle = verification.get("source_rowset_oracle")
    if not isinstance(oracle, Mapping) or oracle.get("status") != "verified" \
            or oracle.get("independent") is not True \
            or oracle.get("family_count") != len(M5D_FAMILY_ORDER):
        raise BatchM5DError("M5D independent source rowset oracle is missing")
    if oracle.get("avatar_target_only_array_index") != {
        "table": "avatar_codex_opaque_occurrence",
        "columns": ["array_index"],
        "all_null": True,
    }:
        raise BatchM5DError("M5D Avatar array_index coordinate seal is missing")
    families = oracle.get("families")
    if not isinstance(families, list) or tuple(item.get("family") for item in families) != M5D_FAMILY_ORDER:
        raise BatchM5DError("M5D independent source rowset family order mismatch")
    family_by_name = {str(item["family"]): item for item in families}
    for declaration, family_item in zip(declarations, family_items):
        observed = family_by_name.get(declaration.family)
        if not isinstance(observed, Mapping):
            raise BatchM5DError(f"M5D source rowset oracle family missing: {declaration.family}")
        oracle_tables = observed.get("tables")
        manifest_tables = family_item.get("tables")
        if not isinstance(oracle_tables, list) or not isinstance(manifest_tables, list):
            raise BatchM5DError(f"M5D source rowset oracle tables missing: {declaration.family}")
        oracle_by_name = {str(item.get("table")): item for item in oracle_tables}
        for table in manifest_tables:
            name = str(table.get("table_name"))
            item = oracle_by_name.get(name)
            if not isinstance(item, Mapping) or item.get("row_count") != table.get("row_count"):
                raise BatchM5DError(f"M5D source rowset oracle count mismatch: {name}")
            for key in ("source_rowset_sha256", "target_rowset_sha256"):
                value = item.get(key)
                if not isinstance(value, str) or not _SHA256.fullmatch(value):
                    raise BatchM5DError(f"M5D source rowset oracle seal missing: {name}/{key}")
            target_only = item.get("target_only_columns")
            expected_target_only = ["array_index"] if name == "avatar_codex_opaque_occurrence" else []
            if target_only != expected_target_only:
                raise BatchM5DError(f"M5D source rowset target-only shape mismatch: {name}")
            coordinate = item.get("target_coordinate_seal")
            if not isinstance(coordinate, Mapping) or coordinate.get("columns") != expected_target_only:
                raise BatchM5DError(f"M5D source rowset coordinate seal missing: {name}")
            if name == "avatar_codex_opaque_occurrence":
                row_count = int(item["row_count"])
                if coordinate.get("null_counts", {}).get("array_index") != row_count \
                        or coordinate.get("non_null_counts", {}).get("array_index") != 0:
                    raise BatchM5DError("M5D Avatar array_index coordinate counts mismatch")


def _verify_live_target_rowset_oracle(
    conn: sqlite3.Connection,
    verification: Mapping[str, Any],
    declarations: Sequence[M5DFamilyDeclaration],
    family_items: Sequence[Mapping[str, Any]],
) -> None:
    """Bind every independent oracle target seal to the live candidate rows.

    The independent verifier compares sealed M5C source rows to the candidate.
    Runtime qualification additionally recomputes the reported target rowset
    and target-only coordinate digest from the read-only candidate, so a
    replaced/tampered DB cannot reuse an otherwise valid verification report.
    """
    oracle = verification["source_rowset_oracle"]
    families = oracle["families"]
    family_by_name = {str(item["family"]): item for item in families}
    for declaration, family_item in zip(declarations, family_items):
        observed = family_by_name[declaration.family]
        oracle_by_name = {str(item["table"]): item for item in observed["tables"]}
        for table in family_item["tables"]:
            name = str(table["table_name"])
            item = oracle_by_name.get(name)
            if not isinstance(item, Mapping):
                raise BatchM5DError(f"M5D live source rowset oracle table missing: {name}")
            source_columns = tuple(str(value) for value in item.get("source_columns", ()))
            target_only = item.get("target_only_columns")
            expected_target_only = ["array_index"] if name == "avatar_codex_opaque_occurrence" else []
            if not source_columns or target_only != expected_target_only:
                raise BatchM5DError(f"M5D live target coordinate shape mismatch: {name}")
            actual_columns = tuple(str(row[1]) for row in conn.execute(
                f"PRAGMA table_info({_q(name)})"
            ))
            if any(column not in actual_columns for column in source_columns):
                raise BatchM5DError(f"M5D live source columns missing: {name}")
            if any(column not in actual_columns for column in target_only):
                raise BatchM5DError(f"M5D live target-only column missing: {name}")
            selected = ",".join(_q(column) for column in source_columns)
            ordering = ",".join(_q(column) for column in source_columns)
            run_id = declaration.ingest_run_id
            rows = [tuple(row) for row in conn.execute(
                f"SELECT {selected} FROM {_q(name)} WHERE ingest_run_id=? ORDER BY {ordering}",
                (run_id,),
            )]
            if len(rows) != int(item["row_count"]):
                raise BatchM5DError(f"M5D live target row count mismatch: {name}")
            target_digest = _digest_rows(rows)
            if target_digest != item.get("target_rowset_sha256"):
                raise BatchM5DError(f"M5D live target rowset seal mismatch: {name}")
            coordinate_columns = (*source_columns, *target_only)
            coordinate_select = ",".join(_q(column) for column in coordinate_columns)
            coordinate_rows = [tuple(row) for row in conn.execute(
                f"SELECT {coordinate_select} FROM {_q(name)} "
                f"WHERE ingest_run_id=? ORDER BY {ordering}",
                (run_id,),
            )]
            coordinate = item.get("target_coordinate_seal")
            if not isinstance(coordinate, Mapping):
                raise BatchM5DError(f"M5D live target coordinate seal missing: {name}")
            if coordinate.get("columns") != expected_target_only:
                raise BatchM5DError(f"M5D live target coordinate columns mismatch: {name}")
            if _digest_rows(coordinate_rows) != coordinate.get("rowset_sha256"):
                raise BatchM5DError(f"M5D live target coordinate digest mismatch: {name}")
            if not target_only and coordinate.get("rowset_sha256") != target_digest:
                raise BatchM5DError(f"M5D live target coordinate/base digest mismatch: {name}")
            for column in target_only:
                null_count = int(conn.execute(
                    f"SELECT COUNT(*) FROM {_q(name)} WHERE ingest_run_id=? AND {_q(column)} IS NULL",
                    (run_id,),
                ).fetchone()[0])
                total = len(rows)
                non_null_count = total - null_count
                if (
                    coordinate.get("null_counts", {}).get(column) != null_count
                    or coordinate.get("non_null_counts", {}).get(column) != non_null_count
                ):
                    raise BatchM5DError(f"M5D live target coordinate counts mismatch: {name}/{column}")
                if name == "avatar_codex_opaque_occurrence" and (null_count != total or non_null_count != 0):
                    raise BatchM5DError("M5D Avatar target-only array_index is not all NULL")


def _verify_schema17(conn: sqlite3.Connection) -> dict[str, str]:
    versions = tuple(int(row[0]) for row in conn.execute("SELECT version FROM schema_migration ORDER BY version"))
    if versions != tuple(range(1, M5D_SCHEMA_VERSION + 1)):
        raise BatchM5DError("Schema 17 migration ledger is not exact 1..17")
    ledger = {int(row[0]): str(row[1]) for row in conn.execute("SELECT version,applied_at FROM schema_migration WHERE version>=14")}
    expected = {14: M5D_MIGRATION_0014_APPLIED_AT, 15: M5D_MIGRATION_0015_APPLIED_AT, 16: M5D_MIGRATION_0016_APPLIED_AT, 17: M5D_MIGRATION_0017_APPLIED_AT}
    if ledger != expected:
        raise BatchM5DError("Schema 17 migration timestamps are not pinned")
    migration = ROOT / "migrations/0017_batch_wave02_family_projection.sql"
    if _sha256_file(migration) != M5D_MIGRATION_0017_SHA256:
        raise BatchM5DError("migration 0017 raw SQL seal mismatch")
    try:
        mode = str(Database._stage_structural_snapshot(conn)["fts"]["mode"])
        signature = Database._stage_structural_signature(conn)
    except (KeyError, TypeError, ValueError, sqlite3.Error) as exc:
        raise BatchM5DError("Schema 17 structural signature is unavailable") from exc
    if signature != M5D_SCHEMA17_STRUCTURAL_SIGNATURES.get(mode):
        raise BatchM5DError("Schema 17 structural signature mismatch")
    return {"fts_mode": mode, "structural_signature": signature}


def qualify_batch_m5d(
    db: ReadonlyDatabase,
    report_path: str | Path = M5D_REPORT_PATH,
    manifest_path: str | Path = M5D_RUNTIME_MANIFEST_PATH,
    verification_path: str | Path = M5D_INDEPENDENT_REPORT_PATH,
    candidate_path: str | Path | None = None,
    spec_dir: str | Path = M5D_SPEC_DIR,
) -> M5DQualification:
    """Qualify one exact M5D bundle once; providers reuse its snapshot."""
    report = _strict_json(Path(report_path), "M5D report")
    manifest = _strict_json(Path(manifest_path), "M5D runtime manifest")
    verification = _strict_json(Path(verification_path), "M5D independent verification")
    report_file, manifest_file, verification_file = Path(report_path).resolve(), Path(manifest_path).resolve(), Path(verification_path).resolve()
    report_identity, manifest_identity, verification_identity = _identity(report_file, "M5D report"), _identity(manifest_file, "M5D runtime manifest"), _identity(verification_file, "M5D independent verification")
    report_sha, manifest_sha, verification_sha = _sha256_file(report_file), _sha256_file(manifest_file), _sha256_file(verification_file)
    if report.get("report_version") != M5D_REPORT_VERSION or report.get("status") != "implemented_non_product" or report.get("schema_version") != M5D_SCHEMA_VERSION or report.get("schema_contract") != M5D_SCHEMA_CONTRACT:
        raise BatchM5DError("M5D report identity/status/schema mismatch")
    if report.get("product_accepted") is not False or report.get("receipts_issued") is not False or report.get("stdio_accepted") is not False or report.get("verification_status") != "independent_verifier_required":
        raise BatchM5DError("M5D report crosses the M5E/product boundary")
    if manifest.get("manifest_id") != M5D_MANIFEST_ID or manifest.get("manifest_version") != M5D_MANIFEST_VERSION or manifest.get("schema_version") != M5D_SCHEMA_VERSION or manifest.get("schema_contract") != M5D_SCHEMA_CONTRACT:
        raise BatchM5DError("M5D manifest identity/schema mismatch")
    provider_registry = manifest.get("provider_registry")
    if (manifest.get("product_accepted") is not False
            or manifest.get("verification", {}).get("status") != "independent_verifier_required"
            or not isinstance(provider_registry, Mapping)
            or any(provider_registry.get(key) != value for key, value in M5D_REGISTRY_FLAGS.items())
            or provider_registry.get("m5e_receipts_issued") is not False
            or provider_registry.get("product_promotion") is not False):
        raise BatchM5DError("M5D manifest crosses the M5E/product boundary")
    if verification.get("report_version") != M5D_INDEPENDENT_REPORT_VERSION or verification.get("status") != "verified" or verification.get("independent") is not True or verification.get("schema_version") != M5D_SCHEMA_VERSION or verification.get("schema_contract") != M5D_SCHEMA_CONTRACT or verification.get("product_accepted") is not False or verification.get("foreign_key_check") != "clean":
        raise BatchM5DError("M5D independent verification identity/status mismatch")
    if verification.get("protected_schema16", {}).get("status") != "verified":
        raise BatchM5DError("protected Schema16 verification is missing")
    if verification.get("builder_report", {}).get("sha256") != report_sha or verification.get("runtime_manifest", {}).get("sha256") != manifest_sha:
        raise BatchM5DError("M5D report/manifest seal is not bound by independent evidence")
    manifest_self = manifest.get("manifest_sha256")
    body = dict(manifest); body.pop("manifest_sha256", None); body.pop("manifest_canonical_sha256", None)
    if not isinstance(manifest_self, str) or not _SHA256.fullmatch(manifest_self) or sha256_bytes(canonical_json(body).encode("utf-8")) != manifest_self:
        raise BatchM5DError("M5D manifest canonical seal mismatch")
    declarations = load_family_declarations(spec_dir)
    if tuple(item.family for item in declarations) != M5D_FAMILY_ORDER:
        raise BatchM5DError("M5D declaration order mismatch")
    family_items, report_items = manifest.get("families"), report.get("families")
    if not isinstance(family_items, list) or not isinstance(report_items, list) or tuple(item.get("family") for item in family_items) != M5D_FAMILY_ORDER or tuple(item.get("family") for item in report_items) != M5D_FAMILY_ORDER or family_items != report_items:
        raise BatchM5DError("M5D family metadata/order mismatch")
    _verify_source_rowset_oracle(verification, declarations, family_items)
    wave, source = manifest.get("wave"), manifest.get("source")
    if not isinstance(wave, Mapping) or not isinstance(source, Mapping) or wave.get("contract_version") != M5D_WAVE_CONTRACT or wave.get("target_schema_version") != M5D_SCHEMA_VERSION or wave.get("status") != "complete" or wave.get("metadata", {}).get("family_order") != list(M5D_FAMILY_ORDER) or wave.get("metadata", {}).get("product_accepted") is not False or wave.get("metadata", {}).get("corpus_complete") is not False:
        raise BatchM5DError("M5D wave/source metadata mismatch")
    wave_id, snapshot_id = str(wave.get("id")), str(source.get("snapshot_id"))
    candidate_meta, report_candidate, manifest_candidate = verification.get("candidate"), report.get("candidate"), manifest.get("candidate")
    if not isinstance(candidate_meta, Mapping) or not isinstance(report_candidate, Mapping) or not isinstance(manifest_candidate, Mapping):
        raise BatchM5DError("M5D candidate seal is missing")
    selected = Path(candidate_path).resolve() if candidate_path is not None else Path(str(candidate_meta.get("path"))).resolve()
    if selected != Path(str(candidate_meta.get("path"))).resolve() or selected != Path(str(report_candidate.get("path"))).resolve() or db.path.resolve() != selected:
        raise BatchM5DError("M5D candidate path is not the exact sealed candidate")
    candidate_size, candidate_sha = int(candidate_meta.get("size_bytes", -1)), str(candidate_meta.get("sha256"))
    if candidate_size <= 0 or not _SHA256.fullmatch(candidate_sha) or report_candidate.get("size_bytes") != candidate_size or report_candidate.get("sha256") != candidate_sha or manifest_candidate.get("size_bytes") != candidate_size or manifest_candidate.get("sha256") != candidate_sha:
        raise BatchM5DError("M5D candidate seals disagree")
    candidate_identity = _identity(selected, "M5D candidate")
    if candidate_identity[2] != candidate_size or _sha256_file(selected) != candidate_sha:
        raise BatchM5DError("M5D candidate size/SHA-256 seal mismatch")
    with db.conn:
        _verify_schema17(db.conn)
        _verify_live_target_rowset_oracle(db.conn, verification, declarations, family_items)
        if db.conn.execute("SELECT 1 FROM source_snapshot WHERE id=? AND \"commit\"=? AND tree=?", (snapshot_id, source.get("commit"), source.get("tree"))).fetchone() is None:
            raise BatchM5DError("M5D source snapshot metadata mismatch")
        wave_row = db.conn.execute("SELECT snapshot_id,contract_version,manifest_sha256,target_schema_version,status,metadata_json FROM batch_wave WHERE id=?", (wave_id,)).fetchone()
        if wave_row is None or tuple(wave_row[:5]) != (snapshot_id, M5D_WAVE_CONTRACT, wave.get("manifest_sha256"), M5D_SCHEMA_VERSION, "complete"):
            raise BatchM5DError("M5D batch_wave metadata mismatch")
        _json_equal(wave_row[5], wave.get("metadata"), "batch_wave.metadata_json")
        provenance, report_provenance = manifest.get("provenance_tables"), report.get("provenance_tables")
        if not isinstance(provenance, list) or tuple(item.get("table") for item in provenance) != M5D_PROVENANCE_TABLES or provenance != report_provenance:
            raise BatchM5DError("M5D provenance table boundary mismatch")
        for entry in provenance:
            _verify_provenance_table(db.conn, entry, wave_id)
        row_counts: dict[str, int] = {}
        for declaration, item in zip(declarations, family_items):
            if item.get("family_contract_version") != declaration.family_contract_version or item.get("ingest_run_id") != declaration.ingest_run_id or item.get("primary_table") != declaration.table_name or item.get("coverage_level") != declaration.coverage_level or item.get("corpus_complete") is not False or tuple(item.get("operations", ())) != declaration.operations:
                raise BatchM5DError(f"M5D family declaration mismatch: {declaration.family}")
            tables = item.get("tables")
            if not isinstance(tables, list) or tuple(table.get("table_name") for table in tables) != declaration.table_names:
                raise BatchM5DError(f"M5D family table boundary mismatch: {declaration.family}")
            for table in tables:
                _verify_table_digest(db.conn, table, declaration.ingest_run_id)
            primary_count = int(tables[0].get("row_count")); row_counts[declaration.family] = primary_count
            imported_row_count = sum(int(table.get("row_count", 0)) for table in tables)
            shard = db.conn.execute("SELECT wave_id,family,family_contract_version,ingest_run_id,manifest_sha256,status FROM batch_shard WHERE id=?", (item.get("shard_id"),)).fetchone()
            if shard is None or tuple(shard) != (wave_id, declaration.family, declaration.family_contract_version, declaration.ingest_run_id, wave.get("manifest_sha256"), "imported"):
                raise BatchM5DError(f"M5D batch_shard metadata mismatch: {declaration.family}")
            imported = db.conn.execute("SELECT wave_id,shard_id,target_schema_version,imported_row_count,status FROM batch_shard_import WHERE id=?", (item.get("import_id"),)).fetchone()
            if imported is None or tuple(imported) != (wave_id, item.get("shard_id"), M5D_SCHEMA_VERSION, imported_row_count, "complete"):
                raise BatchM5DError(f"M5D batch_shard_import metadata mismatch: {declaration.family}")
        if db.conn.execute("PRAGMA foreign_key_check").fetchall():
            raise BatchM5DError("M5D candidate foreign_key_check is not clean")
    parser_build_id = str(report_items[0].get("parser_build_id"))
    if not parser_build_id or any(item.get("parser_build_id") != parser_build_id for item in report_items):
        raise BatchM5DError("M5D parser build binding mismatch")
    stability = M5DStabilitySnapshot(
        str(db.path.resolve()), tuple(candidate_identity), str(report_file), tuple(report_identity), report_sha,
        str(manifest_file), tuple(manifest_identity), manifest_sha,
        str(verification_file), tuple(verification_identity), verification_sha,
    )
    return M5DQualification(
        status="verified", report_path=str(report_file), manifest_path=str(manifest_file), verification_path=str(verification_file),
        candidate_path=str(selected), candidate_sha256=candidate_sha, candidate_size_bytes=candidate_size, wave_id=wave_id,
        snapshot_id=snapshot_id, parser_build_id=parser_build_id, manifest_sha256=manifest_sha, report_sha256=report_sha,
        verification_sha256=verification_sha, family_row_counts=row_counts, stability=stability,
        report=report, manifest=manifest, verification=verification,
    )


def family_capability(declaration: M5DFamilyDeclaration, qualification: M5DQualification) -> dict[str, Any]:
    """Build a complete capability from the shared qualification snapshot."""
    run = {
        "ingest_run_id": declaration.ingest_run_id, "snapshot_id": qualification.snapshot_id,
        "parser_build_id": qualification.parser_build_id, "family_contract_version": declaration.family_contract_version,
        "coverage": declaration.coverage_level, "corpus_complete": False, "wave_id": qualification.wave_id,
        "row_count": qualification.family_row_counts[declaration.family],
    }
    supported_filters = {operation: [item for item in declaration.filters if operation in item.get("supported_operations", ())] for operation in ("list_family_evidence", "get_family_evidence", "search_family_text")}
    return {
        "family": declaration.family, "family_contract_version": declaration.family_contract_version,
        "table": declaration.table_name, "table_names": list(declaration.table_names), "ordering": list(declaration.sort),
        "container_scope": f"single_physical_{declaration.family}_row_occurrence", "schema_supported": True,
        "evidence_available": True, "unavailable_reason": None, "coverage": declaration.coverage_level,
        "corpus_complete": False, "eligible_exact_runs": [run],
        "supported_operations": list(declaration.operations), "supported_filters": supported_filters,
        "pagination_contract": {
            "kind": "deterministic_keyset", "cursor": "opaque_batch_m5d_family_cursor_v1",
            "limit": {"default": 50, "minimum": 1, "maximum": 100},
            "scope_binding": ["family", "ingest_run_id", "family_contract_version", "operation", "filters", "query", "stable_full_ordering"],
            "stable_full_ordering": {"list_family_evidence": list(declaration.sort), "search_family_text": list(M5D_SEARCH_ORDER)},
        },
        "coverage_semantics": {
            "coverage": declaration.coverage_level, "wave_coverage": M5D_COVERAGE,
            "corpus_complete": False, "outer_corpus_complete": False, "full_7_0_claimed": False,
            "family_boundary": "M5C-selected physical source boundary only",
        },
        "search_contract": {
            "supported": "search_family_text" in declaration.operations,
            "scope": f"single_physical_{declaration.family}_row_occurrence",
            "fields": list(declaration.search.get("owner_fields", ())),
            "query": "required_nonempty_literal_substring_over_resolved_raw_text",
            "matching": "case_sensitive_literal_substring", "locale": "explicit CHS/EN/JP filter; omission retains all locales",
            "occurrence_preserving": True, "cross_family_join": False,
        },
        "selection": {"ingest_run_id": "required_exact", "explicit_ingest_run_required": True, "default_run": False, "latest_run": False, "sibling_fallback": False},
        "release_qualification": {
            "status": qualification.status, "report": qualification.report_path, "runtime_manifest": qualification.manifest_path,
            "independent_verification": qualification.verification_path, "candidate": qualification.candidate_path,
            "candidate_sha256_verified_once": True, "receipts_issued": False, "product_accepted": False, "stdio_accepted": False,
        },
        "provider_registry": dict(M5D_REGISTRY_FLAGS), "evidence_only": True, "data_contract": declaration.family_contract_version,
    }


def build_batch_m5d_providers(*args: Any, **kwargs: Any) -> tuple[Any, ...]:
    """Construct all eleven providers from one qualified M5D snapshot.

    The import is intentionally lazy: the qualification/declaration module
    remains usable by builders and independent verifiers without importing the
    query layer, while runtime startup gets one shared qualification object.
    """
    from .batch_m5d_retrieval import build_batch_m5d_providers as provider_factory
    return provider_factory(*args, **kwargs)


__all__ = [
    "BatchM5DError", "M5DChildTableDeclaration", "M5DFamilyDeclaration", "M5DQualification", "M5DStabilitySnapshot",
    "M5D_CANDIDATE_PATH", "M5D_COVERAGE", "M5D_FAMILY_ORDER", "M5D_FAMILY_TABLES", "M5D_INDEPENDENT_REPORT_PATH",
    "M5D_MIGRATION_0017_SHA256", "M5D_PRIMARY_TABLES", "M5D_REPORT_PATH", "M5D_REPORT_VERSION", "M5D_RUNTIME_MANIFEST_PATH",
    "M5D_SCHEMA_CONTRACT", "M5D_SCHEMA_VERSION", "M5D_SPEC_DIR", "M5D_WAVE_CONTRACT", "build_batch_m5d_providers",
    "family_capability", "load_family_declarations", "qualify_batch_m5d",
]
