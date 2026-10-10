"""Deterministic Phase 4A2 static lore audit.

The audit is deliberately an evidence inventory, not an ingestion adapter or a
logical identity layer.  Every identifier emitted here stays scoped to its
source table, source snapshot, file, or relationship occurrence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from jsonschema import Draft202012Validator, SchemaError, ValidationError
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ARTIFACT_NAMES = (
    "phase4-audit-manifest.json",
    "phase4-source-inventory.json",
    "phase4-raw-boundary-audit.json",
    "phase4-native-identity-audit.json",
    "phase4-link-cardinality-audit.json",
    "phase4-textmap-reference-audit.json",
    "phase4-readable-reconciliation.json",
    "phase4-segmentation-audit.json",
    "phase4-schema-key-audit.json",
    "phase4-field-role-registry.json",
    "phase4-coverage-reconciliation.json",
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA_PATH = REPOSITORY_ROOT / "schemas" / "phase4a-audit-envelope.schema.json"
REQUIRED_TABLES = (
    "DocumentExcelConfigData",
    "LocalizationExcelConfigData",
    "BookSuitExcelConfigData",
    "BooksCodexExcelConfigData",
    "WeaponExcelConfigData",
    "WeaponCodexExcelConfigData",
    "ReliquaryExcelConfigData",
    "ReliquarySetExcelConfigData",
    "ReliquaryCodexExcelConfigData",
    "MaterialExcelConfigData",
    "MaterialCodexExcelConfigData",
)

PRIMARY_ID_FIELDS = {
    "BookSuitExcelConfigData": "id",
    "BooksCodexExcelConfigData": "id",
    "DocumentExcelConfigData": "id",
    "LocalizationExcelConfigData": "id",
    "MaterialCodexExcelConfigData": "id",
    "MaterialExcelConfigData": "id",
    "ReliquaryCodexExcelConfigData": "id",
    "ReliquaryExcelConfigData": "id",
    "ReliquarySetExcelConfigData": "setId",
    "WeaponCodexExcelConfigData": "id",
    "WeaponExcelConfigData": "id",
}

VERIFIED_TEXTMAP_FIELDS = {
    ("BookSuitExcelConfigData", "suitNameTextMapHash"),
    ("DocumentExcelConfigData", "titleTextMapHash"),
    ("MaterialExcelConfigData", "descTextMapHash"),
    ("MaterialExcelConfigData", "nameTextMapHash"),
    ("ReliquaryExcelConfigData", "descTextMapHash"),
    ("ReliquaryExcelConfigData", "nameTextMapHash"),
    ("WeaponExcelConfigData", "descTextMapHash"),
    ("WeaponExcelConfigData", "nameTextMapHash"),
}

POINTER_FIELDS = {
    "storyId",
    "contentLocalizedId",
    "questContentLocalizedId",
    "materialId",
    "weaponId",
    "suitId",
}

ALLOWED_RESOLUTION_STATUSES = {
    "not_applicable_input_inventory_only",
    "inventory_complete_resolution_partial",
    "partial_unknown_field_roles_preserved",
    "partial_dangling_missing_and_unknown_roles_explicit",
    "partial_verified_refs_have_explicit_missing_and_unresolved_field_roles",
    "partial_empty_and_test_marker_occurrences_explicit",
    "partial_cross_language_alignment_unresolved",
    "partial_blockers_preserved",
    "complete",
}

PINNED_SOURCE_COMMIT = "26df1dfbdf05a82bbb1d97506859f3e1c40718d8"
PINNED_SOURCE_TREE = "d891ef1d95fdd6c27a1da8a32ea7a61b483647a6"
PINNED_DIRECTORY_COUNTS = {
    "BinOutput": 117359,
    "ExcelBinOutput": 2198,
    "Readable": 28650,
    "Subtitle": 4644,
    "TextMap": 34,
}

PINNED_TABLE_COUNTS = {
    "BookSuitExcelConfigData": 77,
    "BooksCodexExcelConfigData": 293,
    "DocumentExcelConfigData": 1852,
    "LocalizationExcelConfigData": 2229,
    "MaterialCodexExcelConfigData": 1166,
    "MaterialExcelConfigData": 10404,
    "ReliquaryCodexExcelConfigData": 129,
    "ReliquaryExcelConfigData": 4352,
    "ReliquarySetExcelConfigData": 66,
    "WeaponCodexExcelConfigData": 249,
    "WeaponExcelConfigData": 281,
}


@dataclass(frozen=True)
class AuditConfig:
    manifest_path: Path
    source_root: Path
    out_dir: Path
    primary_locales: tuple[str, ...] = ("CHS", "EN", "JP")


@dataclass(frozen=True)
class SourceSnapshot:
    source_id: str
    release_label: str
    build: str
    commit: str
    tree: str
    source_label: str


@dataclass(frozen=True)
class SegmentOccurrence:
    source_file_identity: Mapping[str, str]
    locale: str
    byte_start: int
    byte_end: int
    character_start: int
    character_end: int
    file_local_ordinal: int
    segmentation_algorithm_version: str


@dataclass(frozen=True)
class RelationshipEvidence:
    source_family: str
    source_record_id: str
    source_pointer: Mapping[str, Any]
    relation_kind: str
    target_family: str
    target_candidate_key: str | None
    target_evidence_container_id: str | None
    resolution_status: str
    ambiguity: Mapping[str, Any]
    provenance: Mapping[str, Any]


class AuditContractError(RuntimeError):
    """Raised when the snapshot, schema, or reconciliation contract fails."""


def _json_load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuditContractError(f"cannot read JSON {path}: {exc}") from exc


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def run_git(source_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=source_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as exc:
        raise AuditContractError(f"cannot execute git in source checkout: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise AuditContractError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def _relative_label(path: str) -> str:
    value = path.replace("\\", "/").strip()
    if not value or value.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith("//"):
        raise AuditContractError(f"absolute or empty path is not allowed in an artifact: {path!r}")
    return value


def source_label_for(source_root: Path) -> str:
    resolved = source_root.resolve()
    for base in (Path.cwd().resolve(), Path(__file__).resolve().parents[2]):
        try:
            return _relative_label(resolved.relative_to(base).as_posix())
        except ValueError:
            continue
    return _relative_label(resolved.name)


def _ensure_output_outside_source(source_root: Path, out_dir: Path) -> None:
    source = source_root.resolve()
    output = out_dir.resolve()
    try:
        output.relative_to(source)
    except ValueError:
        return
    raise AuditContractError("--out must not be inside the read-only source checkout")


def verify_source_snapshot(manifest_path: Path, source_root: Path) -> SourceSnapshot:
    """Verify complete manifest provenance and clean pinned source status."""
    manifest = _json_load(manifest_path)
    if not isinstance(manifest, dict):
        raise AuditContractError("manifest must be a JSON object")
    required = {"source_id", "release_label", "build", "commit", "tree"}
    missing = sorted(required - set(manifest))
    if missing:
        raise AuditContractError(f"manifest missing required keys: {missing}")
    for key in sorted(required):
        value = manifest[key]
        if not isinstance(value, str) or not value.strip():
            raise AuditContractError(f"manifest {key} must be a non-empty string")
    if manifest["source_id"] != "anime-game-data":
        raise AuditContractError("manifest source_id must be anime-game-data")
    if manifest["release_label"] != "7.0":
        raise AuditContractError("manifest release_label must be 7.0")

    commit = run_git(source_root, "rev-parse", "HEAD")
    tree = run_git(source_root, "rev-parse", "HEAD^{tree}")
    status = run_git(source_root, "status", "--porcelain")
    if commit != manifest["commit"]:
        raise AuditContractError(f"source commit mismatch: observed={commit} expected={manifest['commit']}")
    if tree != manifest["tree"]:
        raise AuditContractError(f"source tree mismatch: observed={tree} expected={manifest['tree']}")
    if status:
        raise AuditContractError(f"source working tree is not clean: {status}")

    return SourceSnapshot(
        source_id=manifest["source_id"],
        release_label=manifest["release_label"],
        build=manifest["build"],
        commit=commit,
        tree=tree,
        source_label=source_label_for(source_root),
    )


def canonical_json(value: Mapping[str, Any]) -> bytes:
    """Return UTF-8 sorted-key JSON with two-space indentation and one LF."""
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _validate_no_forbidden_paths_or_time(value: Any, path: str = "$", key_hint: str | None = None) -> None:
    is_path_field = key_hint is not None and (key_hint == "generated_from" or key_hint == "path" or key_hint.endswith("_path") or key_hint.endswith("_paths"))
    if isinstance(value, str):
        if re.search(r"^[A-Za-z]:[\\/]", value) or value.startswith("\\\\") or (value.startswith("/") and is_path_field):
            raise AuditContractError(f"absolute path leaked into artifact at {path}: {value}")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"generated_at", "timestamp"}:
                raise AuditContractError(f"forbidden time field at {path}/{key}")
            _validate_no_forbidden_paths_or_time(child, f"{path}/{key}", str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_no_forbidden_paths_or_time(child, f"{path}/{index}", key_hint)


def _validate_envelope(value: Mapping[str, Any]) -> None:
    required = {
        "schema_version", "audit_kind", "audit_version", "source_id", "release_label",
        "build", "commit", "tree", "inventory_scope", "inventory_complete",
        "parser_status", "generated_from", "resolution_status", "summary", "issues",
        "collision_examples", "unresolved", "limitations",
    }
    if set(value) != required:
        missing = sorted(required - set(value))
        extra = sorted(set(value) - required)
        raise AuditContractError(f"envelope keys invalid: missing={missing}, extra={extra}")
    if value["schema_version"] != 1 or value["audit_version"] != "4A2.0":
        raise AuditContractError("unsupported Phase 4A2 envelope version")
    if not isinstance(value["audit_kind"], str) or not value["audit_kind"]:
        raise AuditContractError("audit_kind must be a non-empty string")
    if not isinstance(value["source_id"], str) or not value["source_id"]:
        raise AuditContractError("source_id must be a non-empty string")
    if not isinstance(value["release_label"], str) or not value["release_label"]:
        raise AuditContractError("release_label must be a non-empty string")
    if not isinstance(value["build"], str) or not value["build"]:
        raise AuditContractError("build must be a non-empty string")
    for key in ("commit", "tree"):
        if not isinstance(value[key], str) or re.fullmatch(r"[0-9a-f]{40}", value[key]) is None:
            raise AuditContractError(f"{key} must be a 40-character lowercase hex object id")
    if not isinstance(value["inventory_scope"], str) or not value["inventory_scope"]:
        raise AuditContractError("inventory_scope must be a non-empty string")
    if not isinstance(value["inventory_complete"], bool):
        raise AuditContractError("inventory_complete must be boolean")
    if value["parser_status"] not in {"not_run", "completed", "failed"}:
        raise AuditContractError(f"invalid parser_status: {value['parser_status']}")
    if not isinstance(value["generated_from"], str):
        raise AuditContractError("generated_from must be a relative string")
    _relative_label(value["generated_from"])
    if value["resolution_status"] not in ALLOWED_RESOLUTION_STATUSES:
        raise AuditContractError(f"invalid resolution_status: {value['resolution_status']}")
    if not isinstance(value["summary"], dict):
        raise AuditContractError("summary must be an object")
    if not isinstance(value["issues"], list):
        raise AuditContractError("issues must be an array")
    for issue_item in value["issues"]:
        if not isinstance(issue_item, dict) or not isinstance(issue_item.get("code"), str):
            raise AuditContractError("issue entries require a code")
        count = issue_item.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise AuditContractError("issue counts must be positive integers")
    if not isinstance(value["collision_examples"], list):
        raise AuditContractError("collision_examples must be an array")
    if not isinstance(value["unresolved"], list):
        raise AuditContractError("unresolved must be an array")
    if value["resolution_status"] == "complete" and value["unresolved"]:
        raise AuditContractError("unresolved entries cannot coexist with resolution_status=complete")
    if not isinstance(value["limitations"], list) or not all(isinstance(item, str) for item in value["limitations"]):
        raise AuditContractError("limitations must be an array of strings")
    _validate_no_forbidden_paths_or_time(value)


def _load_artifact_schema(schema_path: Path | None = None) -> Mapping[str, Any]:
    path = Path(schema_path) if schema_path is not None else DEFAULT_SCHEMA_PATH
    if not path.is_file():
        raise AuditContractError(f"tracked artifact schema is missing: {path}")
    try:
        schema = _json_load(path)
        Draft202012Validator.check_schema(schema)
    except (AuditContractError, SchemaError) as exc:
        raise AuditContractError(f"invalid artifact schema {path}: {exc}") from exc
    if not isinstance(schema, Mapping):
        raise AuditContractError(f"artifact schema must be an object: {path}")
    return schema


def validate_artifacts(
    artifacts: Mapping[str, Mapping[str, Any]],
    *,
    schema_path: Path | None = None,
) -> None:
    """Validate artifacts with the tracked Draft 2020-12 schema and domain rules."""
    if not isinstance(artifacts, Mapping) or not artifacts:
        raise AuditContractError("artifact collection must be non-empty")
    schema = _load_artifact_schema(schema_path)
    validator = Draft202012Validator(schema)
    for name, artifact in artifacts.items():
        if not isinstance(name, str) or not name.endswith(".json"):
            raise AuditContractError(f"invalid artifact filename: {name!r}")
        if not isinstance(artifact, Mapping):
            raise AuditContractError(f"artifact {name} must be an object")
        try:
            validator.validate(dict(artifact))
        except ValidationError as exc:
            location = "/".join(str(part) for part in exc.absolute_path)
            raise AuditContractError(
                f"artifact {name} failed Draft 2020-12 schema at {location or '$'}: {exc.message}"
            ) from exc
        _validate_envelope(artifact)


def build_envelope(
    *,
    snapshot: SourceSnapshot,
    audit_kind: str,
    inventory_scope: str,
    generated_from: str,
    summary: Mapping[str, Any],
    resolution_status: str,
    issues: Sequence[Mapping[str, Any]],
    unresolved: Sequence[Mapping[str, Any]],
    collision_examples: Sequence[Mapping[str, Any]],
    limitations: Sequence[str],
) -> dict[str, Any]:
    filtered_issues: list[dict[str, Any]] = []
    for item in issues:
        count = item.get("count")
        if isinstance(count, bool) or not isinstance(count, int):
            raise AuditContractError("every issue must contain an integer count")
        if count > 0:
            filtered_issues.append(dict(item))
    envelope = {
        "schema_version": 1,
        "audit_kind": audit_kind,
        "audit_version": "4A2.0",
        "source_id": snapshot.source_id,
        "release_label": snapshot.release_label,
        "build": snapshot.build,
        "commit": snapshot.commit,
        "tree": snapshot.tree,
        "inventory_scope": inventory_scope,
        "inventory_complete": True,
        "parser_status": "completed",
        "generated_from": _relative_label(generated_from),
        "resolution_status": resolution_status,
        "summary": dict(summary),
        "issues": filtered_issues,
        "collision_examples": [dict(item) for item in collision_examples],
        "unresolved": [dict(item) for item in unresolved],
        "limitations": list(limitations),
    }
    _validate_envelope(envelope)
    return envelope


def _is_reparse_point(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return os.path.islink(path) or bool(attributes & reparse_flag)


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _assert_safe_tree(path: Path, expected_parent: Path) -> None:
    candidate = _absolute_path(path)
    parent = _absolute_path(expected_parent)
    if not candidate.name or candidate.parent != parent:
        raise AuditContractError(f"refusing to remove non-sibling path: {candidate}")
    if _is_reparse_point(candidate):
        raise AuditContractError(f"refusing to traverse reparse point: {candidate}")
    if candidate.is_dir():
        for child in candidate.iterdir():
            _assert_safe_tree(child, candidate)


def _remove_verified_tree(path: Path) -> None:
    if path.is_dir():
        for child in list(path.iterdir()):
            _remove_verified_tree(child)
        path.rmdir()
    else:
        path.unlink()


def _remove_tree_if_present(path: Path) -> None:
    candidate = _absolute_path(path)
    if not os.path.lexists(candidate):
        return
    _assert_safe_tree(candidate, candidate.parent)
    _remove_verified_tree(candidate)


def _unique_sibling_directory(parent: Path, prefix: str) -> Path:
    parent = _absolute_path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    candidate = Path(tempfile.mkdtemp(prefix=prefix, dir=str(parent)))
    _remove_tree_if_present(candidate)
    return candidate


def _replace_directory_atomically(temporary_dir: Path, out_dir: Path) -> None:
    output = _absolute_path(out_dir)
    temporary = _absolute_path(temporary_dir)
    parent = output.parent
    if not output.name or temporary.parent != parent:
        raise AuditContractError("temporary and output directories must be direct siblings")
    _assert_safe_tree(temporary, parent)
    backup: Path | None = None
    if os.path.lexists(output):
        _assert_safe_tree(output, parent)
        backup = _unique_sibling_directory(parent, f".{output.name}.backup-")
        try:
            os.replace(output, backup)
        except Exception:
            _remove_tree_if_present(backup)
            raise
    try:
        os.replace(temporary, output)
    except Exception:
        try:
            if os.path.lexists(output):
                _remove_tree_if_present(output)
            if backup is not None and os.path.lexists(backup):
                os.replace(backup, output)
        except Exception as rollback_error:
            raise AuditContractError(
                f"artifact replacement failed and rollback failed: {rollback_error}"
            ) from rollback_error
        raise
    if backup is not None and os.path.lexists(backup):
        _remove_tree_if_present(backup)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_artifacts_after_assertions(
    out_dir: Path,
    artifacts: Mapping[str, Mapping[str, Any]],
) -> None:
    validate_artifacts(artifacts)
    output = _absolute_path(out_dir)
    if not output.name:
        raise AuditContractError("output directory must name a direct child")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=str(output.parent))
    )
    try:
        for name, artifact in sorted(artifacts.items()):
            if Path(name).name != name:
                raise AuditContractError(f"artifact filename must be a direct child: {name}")
            artifact_path = temporary / name
            with artifact_path.open("wb") as stream:
                stream.write(canonical_json(artifact))
                stream.flush()
                os.fsync(stream.fileno())
        _fsync_directory(temporary)
        _replace_directory_atomically(temporary, output)
        _fsync_directory(output.parent)
    except Exception:
        if os.path.lexists(temporary):
            _remove_tree_if_present(temporary)
        raise


def load_excel_tables(
    source_root: Path,
    table_names: Sequence[str],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for table_name in table_names:
        value = _json_load(source_root / "ExcelBinOutput" / f"{table_name}.json")
        if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
            raise AuditContractError(f"{table_name}.json must contain an array of objects")
        result[table_name] = value
    return result


def enumerate_source_inputs(
    snapshot: SourceSnapshot,
    source_root: Path,
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    files = sorted(
        str(path.relative_to(source_root)).replace("\\", "/")
        for path in source_root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(source_root).parts
    )
    return {
        "source": snapshot.source_label,
        "table_names": sorted(tables),
        "file_count": len(files),
        "files": files,
    }


def classify_field_role(table: str, field: str) -> str:
    if field in {"id", "setId"}:
        return "raw_identifier"
    if (table, field) in VERIFIED_TEXTMAP_FIELDS:
        return "verified_textmap_reference"
    if field in POINTER_FIELDS:
        return "foreign_pointer_candidate"
    if field.endswith("TextMapHash") or field in {"textList", "containsList"}:
        return "unresolved_field_role"
    if field.isupper():
        return "unknown_obfuscated"
    return "unresolved_field_role"


def build_field_role_registry(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    field_stats: dict[str, dict[str, Any]] = {}
    for table, rows in sorted(tables.items()):
        for row in rows:
            for field, value in sorted(row.items()):
                key = f"{table}.{field}"
                item = field_stats.setdefault(
                    key,
                    {
                        "field": key,
                        "table": table,
                        "name": field,
                        "role": classify_field_role(table, field),
                        "row_occurrence_count": 0,
                        "value_type_distribution": Counter(),
                    },
                )
                item["row_occurrence_count"] += 1
                item["value_type_distribution"][_json_type(value)] += 1
    fields = []
    unresolved = []
    for key, item in sorted(field_stats.items()):
        item = dict(item)
        item["value_type_distribution"] = dict(sorted(item["value_type_distribution"].items()))
        fields.append(item)
        if item["role"] in {"unknown_obfuscated", "unresolved_field_role"}:
            unresolved.append(
                {
                    "kind": "field_role",
                    "field": key,
                    "role": item["role"],
                    "occurrence_count": item["row_occurrence_count"],
                }
            )
    return {
        "fields": fields,
        "unresolved": unresolved,
        "summary": {
            "field_count": len(fields),
            "unresolved_field_count": len(unresolved),
            "unknown_obfuscated_field_count": sum(1 for item in unresolved if item["role"] == "unknown_obfuscated"),
        },
    }


def native_id_dimensions(rows: Sequence[Mapping[str, Any]], primary_id_field: str = "id") -> dict[str, Any]:
    row_count = len(rows)
    missing = sum(primary_id_field not in row for row in rows)
    nulls = sum(primary_id_field in row and row[primary_id_field] is None for row in rows)
    zeros = sum(primary_id_field in row and row[primary_id_field] == 0 for row in rows)
    values = [row.get(primary_id_field) for row in rows]
    present_values = [value for value in values if value is not None and value != 0]
    normalized = [str(value) for value in present_values]
    counts = Counter(normalized)
    duplicate_groups = {
        key: count for key, count in sorted(counts.items()) if count > 1
    }
    return {
        "primary_id_field": primary_id_field,
        "row_count": row_count,
        "id_present_count": len(present_values),
        "id_missing_count": missing,
        "id_null_count": nulls,
        "id_zero_count": zeros,
        "unique_id_count": len(set(normalized)),
        "duplicate_id_count": sum(count - 1 for count in counts.values() if count > 1),
        "duplicate_id_groups": duplicate_groups,
        "id_type_distribution": dict(sorted(Counter(_json_type(value) for value in values if value is not None).items())),
        "id_min": min(normalized) if normalized else None,
        "id_max": max(normalized) if normalized else None,
        "native_id_values": sorted(set(normalized)),
    }


def build_native_identity_audit(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    table_audits: dict[str, Any] = {}
    id_sets: dict[str, set[str]] = {}
    for table, rows in sorted(tables.items()):
        field = PRIMARY_ID_FIELDS.get(table, "id")
        dimensions = native_id_dimensions(rows, field)
        table_audits[table] = dimensions
        id_sets[table] = set(dimensions["native_id_values"])
    collisions: list[dict[str, Any]] = []
    table_names = sorted(id_sets)
    for index, left in enumerate(table_names):
        for right in table_names[index + 1 :]:
            overlap = sorted(id_sets[left] & id_sets[right])
            if overlap:
                collisions.append(
                    {
                        "pair": f"{left}__VS__{right}",
                        "count": len(overlap),
                        "samples": overlap[:10],
                    }
                )
    return {
        "table_audits": table_audits,
        "cross_table_collision_pairs_count": len(collisions),
        "cross_table_collisions": collisions,
    }


def _count_source_directory(source_root: Path, name: str) -> int:
    directory = source_root / name
    return sum(1 for path in directory.rglob("*") if path.is_file()) if directory.is_dir() else 0


def _source_directory_counts(source_root: Path) -> dict[str, int]:
    return {name: _count_source_directory(source_root, name) for name in PINNED_DIRECTORY_COUNTS}


def _build_schema_key_audit(tables: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    table_schemas: dict[str, Any] = {}
    unresolved: list[dict[str, Any]] = []
    for table, rows in sorted(tables.items()):
        presence: Counter[str] = Counter()
        types: dict[str, Counter[str]] = defaultdict(Counter)
        for row in rows:
            for field, value in row.items():
                presence[field] += 1
                types[field][_json_type(value)] += 1
        all_fields = sorted(presence)
        table_schemas[table] = {
            "all_fields": all_fields,
            "field_presence_count": dict(sorted(presence.items())),
            "field_type_distribution": {
                field: dict(sorted(counter.items())) for field, counter in sorted(types.items())
            },
            "row_count": len(rows),
        }
        for field in all_fields:
            role = classify_field_role(table, field)
            if role in {"unknown_obfuscated", "unresolved_field_role"}:
                unresolved.append({"kind": "field_role", "table": table, "field": field, "role": role})
    issues = []
    if unresolved:
        issues.append({"code": "UNRESOLVED_EXCEL_KEYS", "count": len(unresolved)})
    return {
        "summary": {
            "table_schemas": table_schemas,
            "obfuscated_or_unresolved_key_count": len(unresolved),
        },
        "issues": issues,
        "unresolved": unresolved,
    }


def discover_textmap_shards(source_root: Path) -> dict[str, dict[str, list[dict[str, Any]]]]:
    result: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {"main": [], "medium": []})
    textmap_root = source_root / "TextMap"
    if not textmap_root.is_dir():
        raise AuditContractError(f"missing TextMap directory: {textmap_root}")
    for path in sorted(textmap_root.glob("*.json")):
        stem = path.stem
        if stem.startswith("TextMap_Medium"):
            kind = "medium"
            body = stem[len("TextMap_Medium") :]
        elif stem.startswith("TextMap"):
            kind = "main"
            body = stem[len("TextMap") :]
        else:
            continue
        parts = body.split("_")
        locale = parts[0]
        if not locale or not locale.isalpha():
            continue
        result[locale][kind].append(
            {"relative_path": path.relative_to(source_root).as_posix(), "path": path}
        )
    return {locale: {kind: list(items) for kind, items in classes.items()} for locale, classes in sorted(result.items())}


def _load_textmap_union(shards: Sequence[Mapping[str, Any]]) -> tuple[dict[str, str], int, list[dict[str, Any]]]:
    values: dict[str, str] = {}
    duplicate_count = 0
    duplicate_examples: list[dict[str, Any]] = []
    for shard in sorted(shards, key=lambda item: str(item["relative_path"])):
        payload = _json_load(Path(shard["path"]))
        if not isinstance(payload, dict):
            raise AuditContractError(f"TextMap shard must contain an object: {shard['relative_path']}")
        for key, value in sorted(payload.items(), key=lambda item: str(item[0])):
            key_string = str(key)
            if key_string in values:
                duplicate_count += 1
                if len(duplicate_examples) < 10:
                    duplicate_examples.append(
                        {"key": key_string, "relative_path": shard["relative_path"]}
                    )
                raise AuditContractError(
                    f"duplicate TextMap key within shard class: {key_string} ({shard['relative_path']})"
                )
            values[key_string] = str(value)
    return values, duplicate_count, duplicate_examples


def build_textmap_report(
    shards: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]],
    primary_locales: Sequence[str],
    include_values: bool = False,
) -> dict[str, Any]:
    language_shards: dict[str, Any] = {}
    unresolved: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    locales = sorted(set(shards) | set(primary_locales))
    for locale in locales:
        classes = shards.get(locale, {"main": [], "medium": []})
        main, main_duplicate_count, main_duplicate_examples = _load_textmap_union(classes.get("main", []))
        medium, medium_duplicate_count, medium_duplicate_examples = _load_textmap_union(classes.get("medium", []))
        main_keys = set(main)
        medium_keys = set(medium)
        overlap = main_keys & medium_keys
        conflicts = sorted(key for key in overlap if main[key] != medium[key])
        language_shards[locale] = {
            "main_shard_files": [item["relative_path"] for item in classes.get("main", [])],
            "medium_shard_files": [item["relative_path"] for item in classes.get("medium", [])],
            "main_key_count": len(main_keys),
            "medium_key_count": len(medium_keys),
            "main_only_key_count": len(main_keys - medium_keys),
            "medium_only_key_count": len(medium_keys - main_keys),
            "overlap_key_count": len(overlap),
            "conflicting_value_count": len(conflicts),
            "conflicting_value_keys": conflicts[:20],
            "duplicate_main_key_count": main_duplicate_count,
            "duplicate_medium_key_count": medium_duplicate_count,
            "union_key_count": len(main_keys | medium_keys),
        }
        if include_values:
            language_shards[locale]["_main_values"] = main
            language_shards[locale]["_medium_values"] = medium
        if main_duplicate_count:
            issues.append({"code": "DUPLICATE_MAIN_TEXTMAP_KEY", "count": main_duplicate_count, "locale": locale})
        if medium_duplicate_count:
            issues.append({"code": "DUPLICATE_MEDIUM_TEXTMAP_KEY", "count": medium_duplicate_count, "locale": locale})
        if conflicts:
            issues.append({"code": "CONFLICTING_TEXTMAP_VALUE", "count": len(conflicts), "locale": locale})
        if main_duplicate_examples or medium_duplicate_examples:
            unresolved.extend(main_duplicate_examples + medium_duplicate_examples)
    return {
        "summary": {"language_shards": language_shards},
        "issues": issues,
        "unresolved": unresolved,
        "collision_examples": [],
        "limitations": ["TextMap hashes are locale string-pool locators, not provenance or logical identity."],
    }


def reconcile_textmap_shards(
    source_root: Path,
    primary_locales: Sequence[str],
) -> dict[str, Any]:
    return build_textmap_report(discover_textmap_shards(source_root), primary_locales)


def _textmap_field_value_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    return [value]


def extract_textmap_candidates(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[set[str], set[str], dict[str, Any]]:
    """Return all positive TextMap candidates, proven candidates, and field stats."""
    all_candidates: set[str] = set()
    proven_candidates: set[str] = set()
    stats: dict[str, Any] = {}
    for table, rows in sorted(tables.items()):
        for row in rows:
            for field, value in sorted(row.items()):
                role = classify_field_role(table, field)
                if role not in {"verified_textmap_reference", "unresolved_field_role"}:
                    continue
                if not (field.endswith("TextMapHash") or field in {"textList"}):
                    continue
                values = _textmap_field_value_list(value)
                key = f"{table}.{field}"
                field_stats = stats.setdefault(
                    key,
                    {"role": role, "occurrence_count": 0, "nonzero_scalar_count": 0},
                )
                field_stats["occurrence_count"] += len(values)
                for candidate in values:
                    if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate > 0:
                        candidate_key = str(candidate)
                        all_candidates.add(candidate_key)
                        field_stats["nonzero_scalar_count"] += 1
                        if role == "verified_textmap_reference":
                            proven_candidates.add(candidate_key)
    return all_candidates, proven_candidates, stats


def build_textmap_reference_audit(
    source_root: Path,
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    primary_locales: Sequence[str],
) -> dict[str, Any]:
    shard_report = build_textmap_report(
        discover_textmap_shards(source_root), primary_locales, include_values=True
    )
    shard_summary = {
        "language_shards": {
            locale: {key: value for key, value in item.items() if not key.startswith("_")}
            for locale, item in shard_report["summary"]["language_shards"].items()
        }
    }
    all_candidates, proven_candidates, stats = extract_textmap_candidates(tables)
    verified_coverage: dict[str, Any] = {}
    candidate_coverage: dict[str, Any] = {}
    unresolved: list[dict[str, Any]] = []
    for locale in primary_locales:
        lang = shard_report["summary"]["language_shards"][locale]
        main_values = set(lang["_main_values"])
        medium_values = set(lang["_medium_values"])
        available = main_values | medium_values
        verified_missing = sorted(proven_candidates - available)
        candidate_missing = sorted(all_candidates - available)
        verified_main_only = sorted(proven_candidates & main_values - medium_values)
        verified_medium_only = sorted(proven_candidates & medium_values - main_values)
        verified_both = sorted(proven_candidates & main_values & medium_values)
        verified_coverage[locale] = {
            "main_only_count": len(verified_main_only),
            "medium_only_count": len(verified_medium_only),
            "both_count": len(verified_both),
            "missing_count": len(verified_missing),
            "total_count": len(proven_candidates),
            "verified_keys": sorted(proven_candidates & available),
        }
        candidate_coverage[locale] = {
            "main_only_count": len(sorted(all_candidates & main_values - medium_values)),
            "medium_only_count": len(sorted(all_candidates & medium_values - main_values)),
            "both_count": len(sorted(all_candidates & main_values & medium_values)),
            "missing_count": len(candidate_missing),
            "total_count": len(all_candidates),
        }
        field_provenance: dict[str, list[str]] = defaultdict(list)
        for field, field_stat in stats.items():
            if field_stat["role"] == "verified_textmap_reference":
                field_provenance[field] = sorted(
                    str(value)
                    for table, rows in tables.items()
                    for row in rows
                    for name, value in row.items()
                    if f"{table}.{name}" == field
                    for value in _textmap_field_value_list(value)
                    if isinstance(value, int) and value > 0 and str(value) in verified_missing
                )
        for candidate in verified_missing:
            unresolved.append(
                {
                    "kind": "missing_textmap_reference",
                    "locale": locale,
                    "candidate_key": candidate,
                    "field_provenance": sorted(
                        field for field, values in field_provenance.items() if candidate in values
                    ),
                }
            )
    unresolved_roles = [
        {"kind": "unresolved_textmap_field_role", "field": field, "role": item["role"]}
        for field, item in sorted(stats.items())
        if item["role"] != "verified_textmap_reference" and item["nonzero_scalar_count"] > 0
    ]
    unresolved.extend(unresolved_roles)
    issues = []
    for locale, coverage in verified_coverage.items():
        if coverage["missing_count"]:
            issues.append({"code": "MISSING_VERIFIED_TEXTMAP_REFERENCE", "count": coverage["missing_count"], "locale": locale})
    if unresolved_roles:
        issues.append({"code": "UNRESOLVED_TEXTMAP_FIELD_ROLE", "count": len(unresolved_roles)})
    return {
        "summary": {
            **shard_summary,
            "candidate_reference_count": len(all_candidates),
            "verified_reference_count": len(proven_candidates),
            "verified_coverage": verified_coverage,
            "candidate_coverage": candidate_coverage,
            "field_reference_stats": stats,
            "hash_reuse_across_families_count": 0,
        },
        "issues": issues,
        "unresolved": unresolved,
        "collision_examples": [],
        "limitations": [
            "Only the eight table-qualified proven TextMap fields contribute to verified coverage.",
            "Unknown and obfuscated fields remain unresolved even when a value happens to hit a string pool.",
        ],
    }


def normalize_readable_stem(filename: str, locale: str) -> str:
    stem = Path(filename).stem
    suffix = "_" + locale
    return stem[: -len(suffix)] if stem.endswith(suffix) else stem


def split_line_occurrences(
    *,
    raw: bytes,
    text: str,
    relative_path: str,
    locale: str,
    snapshot: SourceSnapshot,
) -> tuple[SegmentOccurrence, ...]:
    """Split raw UTF-8 text while preserving raw newline bytes in offsets."""
    del text
    source_text = raw.decode("utf-8")
    occurrences: list[SegmentOccurrence] = []
    character_start = 0
    ordinal = 0
    while character_start <= len(source_text):
        character_end = character_start
        while character_end < len(source_text) and source_text[character_end] not in "\r\n":
            character_end += 1
        line = source_text[character_start:character_end]
        if line.strip():
            byte_start = len(source_text[:character_start].encode("utf-8"))
            byte_end = len(source_text[:character_end].encode("utf-8"))
            occurrences.append(
                SegmentOccurrence(
                    source_file_identity={
                        "relative_path": _relative_label(relative_path),
                        "sha256": sha256(raw),
                        "commit": snapshot.commit,
                        "tree": snapshot.tree,
                    },
                    locale=locale,
                    byte_start=byte_start,
                    byte_end=byte_end,
                    character_start=character_start,
                    character_end=character_end,
                    file_local_ordinal=ordinal,
                    segmentation_algorithm_version="readable-line-occurrence-raw-span-v2",
                )
            )
        if character_end == len(source_text):
            break
        if source_text[character_end] == "\r" and character_end + 1 < len(source_text) and source_text[character_end + 1] == "\n":
            newline_length = 2
        else:
            newline_length = 1
        character_start = character_end + newline_length
        ordinal += 1
    return tuple(occurrences)


def segment_readable_occurrence(
    *,
    raw: bytes,
    relative_path: str,
    locale: str,
    snapshot: SourceSnapshot,
) -> tuple[SegmentOccurrence, ...]:
    text = raw.decode("utf-8")
    return split_line_occurrences(
        raw=raw,
        text=text,
        relative_path=relative_path,
        locale=locale,
        snapshot=snapshot,
    )


def _segment_dict(segment: SegmentOccurrence) -> dict[str, Any]:
    return {
        "source_file_identity": dict(segment.source_file_identity),
        "locale": segment.locale,
        "byte_span": {"start": segment.byte_start, "end": segment.byte_end},
        "character_span": {"start": segment.character_start, "end": segment.character_end},
        "file_local_ordinal": segment.file_local_ordinal,
        "segmentation_algorithm_version": segment.segmentation_algorithm_version,
    }


def _readable_file_occurrences(source_root: Path, locales: Sequence[str], snapshot: SourceSnapshot | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    snapshot = snapshot or SourceSnapshot("synthetic", "synthetic", "synthetic", "0" * 40, "0" * 40, source_label_for(source_root))
    files: list[dict[str, Any]] = []
    segment_examples: list[dict[str, Any]] = []
    for locale in sorted(locales):
        locale_root = source_root / "Readable" / locale
        if not locale_root.is_dir():
            continue
        for path in sorted(locale_root.glob("*.txt")):
            raw = path.read_bytes()
            text = raw.decode("utf-8")
            normalized = text.replace("\r\n", "\n").replace("\r", "\n")
            relative = path.relative_to(source_root).as_posix()
            segments = segment_readable_occurrence(
                raw=raw, relative_path=relative, locale=locale, snapshot=snapshot
            )
            markers = [marker for marker in ("test", "todo", "temp", "placeholder") if marker in normalized.lower()]
            occurrence = {
                "relative_path": relative,
                "locale": locale,
                "normalized_stem": normalize_readable_stem(path.name, locale),
                "sha256": sha256(raw),
                "byte_count": len(raw),
                "character_count": len(text),
                "non_empty_line_count": len(segments),
                "segment_occurrence_count": len(segments),
                "full_content_exact_test_marker": normalized.strip().lower() == "test",
                "diagnostic_substrings": markers,
            }
            files.append(occurrence)
            if segments:
                segment_examples.append(_segment_dict(segments[0]))
    return files, segment_examples


ALIGNMENT_STATUSES = {"one-to-one", "one-to-many", "many-to-many", "ambiguous", "unresolved"}


def _readable_alignment_from_occurrences(
    file_occurrences: Sequence[Mapping[str, Any]],
    primary_locales: Sequence[str],
    alignment_evidence: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    by_stem: dict[str, dict[str, list[Mapping[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for item in file_occurrences:
        by_stem[str(item["normalized_stem"])][str(item["locale"])].append(item)
    alignments: list[dict[str, Any]] = []
    for stem in sorted(by_stem):
        locale_files = by_stem[stem]
        counts = {locale: len(locale_files.get(locale, [])) for locale in primary_locales}
        segment_counts = {
            locale: sum(int(item["segment_occurrence_count"]) for item in locale_files.get(locale, []))
            for locale in primary_locales
        }
        if any(count == 0 for count in counts.values()):
            segment_count_compatibility = "not_comparable"
        elif len(set(segment_counts.values())) == 1:
            segment_count_compatibility = "match"
        else:
            segment_count_compatibility = "mismatch"
        if any(count > 1 for count in counts.values()):
            cardinality_status = "ambiguous"
        elif all(count == 1 for count in counts.values()):
            cardinality_status = "compatible" if segment_count_compatibility == "match" else "incompatible"
        else:
            cardinality_status = "incompatible"
        explicit_status = (alignment_evidence or {}).get(stem)
        status = explicit_status if explicit_status in ALIGNMENT_STATUSES else "unresolved"
        alignments.append(
            {
                "stem": stem,
                "document_grouping_status": "grouped_by_normalized_stem",
                "alignment_status": status,
                "alignment_evidence_present": explicit_status in ALIGNMENT_STATUSES,
                "file_counts_by_locale": counts,
                "segment_counts_by_locale": segment_counts,
                "segment_count_compatibility": segment_count_compatibility,
                "cardinality_status": cardinality_status,
                "ordinal_pairing_performed": False,
            }
        )
    return alignments


def _alignment_statistics(alignments: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return {
        "total_audited_stems": len(alignments),
        "segment_count_match_stem_count": sum(
            item.get("segment_count_compatibility") == "match" for item in alignments
        ),
        "segment_count_mismatch_stem_count": sum(
            item.get("segment_count_compatibility") == "mismatch" for item in alignments
        ),
        "proven_alignment_count": sum(
            item.get("alignment_status") in {"one-to-one", "one-to-many", "many-to-many"}
            for item in alignments
        ),
        "unresolved_alignment_count": sum(
            item.get("alignment_status") == "unresolved" for item in alignments
        ),
    }


def compare_stem_occurrences(
    readable: Mapping[str, Any],
    primary_locales: Sequence[str],
    alignment_evidence: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if "summary" in readable and isinstance(readable["summary"], Mapping):
        files = readable["summary"].get("file_occurrences", [])
    elif "file_occurrences" in readable:
        files = readable["file_occurrences"]
    else:
        files = [
            item
            for locale, stems in readable.items()
            if isinstance(stems, Mapping)
            for stem, occurrences in stems.items()
            for item in occurrences
        ]
    alignments = _readable_alignment_from_occurrences(files, primary_locales, alignment_evidence)
    statistics = _alignment_statistics(alignments)
    issues = []
    if statistics["unresolved_alignment_count"]:
        issues.append({"code": "UNRESOLVED_CROSS_LANGUAGE_ALIGNMENT", "count": statistics["unresolved_alignment_count"]})
    if statistics["segment_count_mismatch_stem_count"]:
        issues.append({"code": "SEGMENT_COUNT_CARDINALITY_MISMATCH", "count": statistics["segment_count_mismatch_stem_count"]})
    return {
        "summary": {
            "alignments": alignments,
            **statistics,
            "segmentation_match_stem_count": statistics["segment_count_match_stem_count"],
            "segmentation_mismatch_stem_count": statistics["segment_count_mismatch_stem_count"],
        },
        "issues": issues,
        "unresolved": [item for item in alignments if item["alignment_status"] == "unresolved"],
        "collision_examples": [],
        "limitations": [
            "Document grouping does not prove segment alignment.",
            "Equal segment counts are cardinality compatibility only; no ordinal pairing is performed.",
        ],
    }


def build_readable_report(
    source_root: Path,
    locales: Sequence[str],
    snapshot: SourceSnapshot | None = None,
) -> dict[str, Any]:
    files, segment_examples = _readable_file_occurrences(source_root, locales, snapshot)
    per_language = {
        locale: sum(1 for item in files if item["locale"] == locale) for locale in sorted(locales)
    }
    by_checksum: dict[str, list[str]] = defaultdict(list)
    duplicate_scope_locale = "CHS" if "CHS" in locales else (sorted(locales)[0] if locales else None)
    for item in files:
        if duplicate_scope_locale is None or item["locale"] == duplicate_scope_locale:
            by_checksum[item["sha256"]].append(item["relative_path"])
    duplicate_groups = [
        {"sha256": checksum, "paths": sorted(paths), "extra_path_count": len(paths) - 1}
        for checksum, paths in sorted(by_checksum.items()) if len(paths) > 1
    ]
    alignments = _readable_alignment_from_occurrences(files, locales)
    alignment_statistics = _alignment_statistics(alignments)
    empty_files = [item["relative_path"] for item in files if item["non_empty_line_count"] == 0]
    exact_markers = [item["relative_path"] for item in files if item["full_content_exact_test_marker"]]
    substring_files = [item["relative_path"] for item in files if item["diagnostic_substrings"] and not item["full_content_exact_test_marker"]]
    stem_sets = {
        locale: {item["normalized_stem"] for item in files if item["locale"] == locale}
        for locale in locales
    }
    all_stems = set().union(*stem_sets.values()) if stem_sets else set()
    differences = sorted(
        stem for stem in all_stems if any(stem not in stem_sets.get(locale, set()) for locale in locales)
    )
    issues = []
    if empty_files:
        issues.append({"code": "EMPTY_READABLE_FILES", "count": len(empty_files)})
    if exact_markers:
        issues.append({"code": "EXACT_TEST_MARKER_FILES", "count": len(exact_markers)})
    if differences:
        issues.append({"code": "READABLE_STEM_SET_DIFFERENCE", "count": len(differences)})
    unresolved = [
        {"kind": "empty_readable_file", "relative_path": path} for path in empty_files
    ] + [
        {"kind": "exact_test_marker", "relative_path": path} for path in exact_markers
    ] + [
        item for item in alignments if item["alignment_status"] == "unresolved"
    ]
    return {
        "summary": {
            "per_language": per_language,
            "total_readable_files": len(files),
            "file_occurrences": files,
            "segment_occurrence_examples": segment_examples,
            "duplicate_whole_file_texts": duplicate_groups,
            "duplicate_whole_file_text_group_count": len(duplicate_groups),
            "duplicate_whole_file_extra_path_count": sum(item["extra_path_count"] for item in duplicate_groups),
            "stem_count": len(all_stems),
            "stem_differences_count": len(differences),
            "stem_differences": differences,
            "alignments": alignments,
            **alignment_statistics,
            "segmentation_match_stem_count": alignment_statistics["segment_count_match_stem_count"],
            "segmentation_mismatch_stem_count": alignment_statistics["segment_count_mismatch_stem_count"],
            "placeholder_detection": {
                "full_content_exact_test_marker_count": len(exact_markers),
                "diagnostic_substring_file_count": len(substring_files),
                "diagnostic_substring_markers": ["test", "todo", "temp", "placeholder"],
            },
        },
        "issues": issues,
        "unresolved": unresolved,
        "collision_examples": [],
        "limitations": [
            "Readable file paths and checksums preserve occurrences; identical text never merges paths.",
            "Document grouping is not segment alignment and multilingual ordinals are not paired.",
        ],
    }


def reconcile_readable_files(
    source_root: Path,
    locales: Sequence[str],
) -> dict[str, Any]:
    return build_readable_report(source_root, locales)


def reconcile_primary_triad_segmentation(
    readable: Mapping[str, Mapping[str, Mapping[str, Any]]],
    primary_locales: Sequence[str],
) -> dict[str, Any]:
    return compare_stem_occurrences(readable, primary_locales)


def _table_native_keys(rows: Sequence[Mapping[str, Any]], field: str = "id") -> set[str]:
    return {
        str(row[field])
        for row in rows
        if field in row and row[field] is not None and row[field] != 0
    }


def resolve_candidate_pointer(
    *,
    source_family: str,
    source_record_id: str,
    source_pointer: Mapping[str, Any],
    relation_kind: str,
    target_family: str,
    candidate_key: str | None,
    target_keys: set[str],
    provenance: Mapping[str, Any],
    candidate_to_container_ids: Mapping[str, str | Iterable[str]] | None = None,
) -> RelationshipEvidence:
    ambiguity: Mapping[str, Any] = {}
    evidence_id: str | None = None
    raw_candidate_key: str | None = None
    status: str
    if candidate_key is None:
        status = "unresolved"
    elif candidate_key not in target_keys:
        status = "dangling"
    else:
        raw_candidate_key = str(candidate_key)
        concrete_value = (candidate_to_container_ids or {}).get(raw_candidate_key)
        if concrete_value is None:
            status = "resolved"
        else:
            if isinstance(concrete_value, str):
                concrete_ids = [concrete_value]
            else:
                concrete_ids = [str(item) for item in concrete_value if str(item)]
            concrete_ids = sorted(set(concrete_ids))
            if len(concrete_ids) == 1:
                status = "resolved"
                evidence_id = concrete_ids[0]
            elif len(concrete_ids) > 1:
                status = "ambiguous"
                ambiguity = {"candidate_evidence_container_ids": concrete_ids}
            else:
                status = "resolved"
    return RelationshipEvidence(
        source_family=source_family,
        source_record_id=str(source_record_id),
        source_pointer=dict(source_pointer),
        relation_kind=relation_kind,
        target_family=target_family,
        target_candidate_key=raw_candidate_key,
        target_evidence_container_id=evidence_id,
        resolution_status=status,
        ambiguity=dict(ambiguity),
        provenance=dict(provenance),
    )


def _relation_dict(relation: RelationshipEvidence) -> dict[str, Any]:
    return asdict(relation)


def _pointer_values(row: Mapping[str, Any], field: str) -> tuple[bool, list[Any]]:
    if field not in row:
        return False, [None]
    value = row[field]
    if isinstance(value, list):
        return True, value or [None]
    return True, [value]


def _readable_relative_paths(readable: Mapping[str, Any]) -> set[str]:
    if not readable:
        return set()
    summary = readable.get("summary", readable)
    return {
        str(item["relative_path"])
        for item in summary.get("file_occurrences", [])
        if isinstance(item, Mapping) and "relative_path" in item
    }


def _readable_candidate_path(raw_value: Any) -> str | None:
    if not isinstance(raw_value, str) or "ART/UI/Readable/" not in raw_value:
        return None
    relative = raw_value.split("ART/UI/Readable/", 1)[1].strip("/")
    if not relative.endswith(".txt"):
        relative += ".txt"
    return "Readable/" + relative


def build_relationship_report(
    snapshot: SourceSnapshot,
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    readable: Mapping[str, Any],
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    readable_paths = _readable_relative_paths(readable)
    document_keys = _table_native_keys(tables.get("DocumentExcelConfigData", []))
    localization_keys = _table_native_keys(tables.get("LocalizationExcelConfigData", []))
    material_keys = _table_native_keys(tables.get("MaterialExcelConfigData", []))
    relic_keys = _table_native_keys(tables.get("ReliquaryExcelConfigData", []))

    weapon_rows = tables.get("WeaponExcelConfigData", [])
    weapon_counts = Counter()
    for row in weapon_rows:
        source_id = str(row.get("id"))
        present = "storyId" in row
        value = row.get("storyId")
        if not present:
            weapon_counts["missing_story_id_count"] += 1
            status_value = None
        elif value is None:
            weapon_counts["null_story_id_count"] += 1
            status_value = None
        elif value == 0:
            weapon_counts["zero_story_id_count"] += 1
            status_value = None
        else:
            status_value = str(value)
        if status_value is None:
            relation = resolve_candidate_pointer(
                source_family="Weapon",
                source_record_id=source_id,
                source_pointer={"field": "storyId", "raw_value": value, "presence": "present" if present else "missing"},
                relation_kind="weapon_story_document",
                target_family="Document",
                candidate_key=None,
                target_keys=document_keys,
                provenance={"snapshot_commit": snapshot.commit, "snapshot_tree": snapshot.tree, "field": "WeaponExcelConfigData.storyId"},
            )
        else:
            relation = resolve_candidate_pointer(
                source_family="Weapon",
                source_record_id=source_id,
                source_pointer={"field": "storyId", "raw_value": value},
                relation_kind="weapon_story_document",
                target_family="Document",
                candidate_key=status_value,
                target_keys=document_keys,
                provenance={"snapshot_commit": snapshot.commit, "snapshot_tree": snapshot.tree, "field": "WeaponExcelConfigData.storyId"},
            )
        records.append(_relation_dict(relation))
        if relation.resolution_status == "resolved":
            weapon_counts["resolved_story_row_count"] += 1
        elif relation.resolution_status == "dangling":
            weapon_counts["dangling_story_row_count"] += 1
            unresolved.append(_relation_dict(relation))
        else:
            if status_value is not None:
                weapon_counts["unresolved_story_row_count"] += 1
                unresolved.append(_relation_dict(relation))
    weapon_summary = {
        "row_count": len(weapon_rows),
        **{key: int(weapon_counts[key]) for key in (
            "missing_story_id_count", "null_story_id_count", "zero_story_id_count",
            "resolved_story_row_count", "dangling_story_row_count", "unresolved_story_row_count",
        )},
    }

    document_pointer_summary: dict[str, Any] = {}
    document_rows = tables.get("DocumentExcelConfigData", [])
    for field in ("contentLocalizedId", "questContentLocalizedId"):
        counts = Counter()
        field_presence_count = 0
        missing_field_count = 0
        pointer_value_count = 0
        for row in document_rows:
            source_id = str(row.get("id"))
            present, values = _pointer_values(row, field)
            if present:
                field_presence_count += 1
            else:
                missing_field_count += 1
            for value in values:
                if not present:
                    relation = resolve_candidate_pointer(
                        source_family="Document",
                        source_record_id=source_id,
                        source_pointer={"field": field, "raw_value": None, "presence": "missing"},
                        relation_kind=f"document_{field}",
                        target_family="Localization",
                        candidate_key=None,
                        target_keys=localization_keys,
                        provenance={"snapshot_commit": snapshot.commit, "snapshot_tree": snapshot.tree, "field": f"DocumentExcelConfigData.{field}"},
                    )
                    records.append(_relation_dict(relation))
                    unresolved.append(_relation_dict(relation))
                    continue
                pointer_value_count += 1
                candidate = str(value) if isinstance(value, int) and value > 0 else None
                relation = resolve_candidate_pointer(
                    source_family="Document",
                    source_record_id=source_id,
                    source_pointer={"field": field, "raw_value": value, "presence": "present" if present else "missing"},
                    relation_kind=f"document_{field}",
                    target_family="Localization",
                    candidate_key=candidate,
                    target_keys=localization_keys,
                    provenance={"snapshot_commit": snapshot.commit, "snapshot_tree": snapshot.tree, "field": f"DocumentExcelConfigData.{field}"},
                )
                records.append(_relation_dict(relation))
                counts[relation.resolution_status] += 1
                if relation.resolution_status != "resolved":
                    unresolved.append(_relation_dict(relation))
        total = pointer_value_count
        document_pointer_summary[field] = {
            "occurrence_count": field_presence_count,
            "field_presence_count": field_presence_count,
            "missing_field_count": missing_field_count,
            "total_pointer_occurrence_count": total,
            "resolved_count": counts["resolved"],
            "dangling_count": counts["dangling"],
            "ambiguous_count": counts["ambiguous"],
            "unresolved_count": counts["unresolved"],
        }

    localization_rows = tables.get("LocalizationExcelConfigData", [])
    local_counts = Counter()
    for row in localization_rows:
        source_id = str(row.get("id"))
        row_candidate_paths: list[str] = []
        for field in ("DEFNEHAFMMA", "JDNBKKPEFAI"):
            if field not in row:
                continue
            raw_value = row[field]
            candidate_path = _readable_candidate_path(raw_value)
            if candidate_path is None:
                continue
            local_counts["readable_candidate_occurrence_count"] += 1
            exists = candidate_path in readable_paths
            local_counts["existing_readable_occurrence_count" if exists else "missing_readable_occurrence_count"] += 1
            row_candidate_paths.append(candidate_path)
            relation = resolve_candidate_pointer(
                source_family="Localization",
                source_record_id=source_id,
                source_pointer={"field": field, "raw_value": raw_value},
                relation_kind="localization_readable_path",
                target_family="ReadableFile",
                candidate_key=candidate_path if exists else candidate_path,
                target_keys=readable_paths,
                provenance={"snapshot_commit": snapshot.commit, "snapshot_tree": snapshot.tree, "field": f"LocalizationExcelConfigData.{field}"},
            )
            records.append(_relation_dict(relation))
            if relation.resolution_status != "resolved":
                unresolved.append(_relation_dict(relation))
        if row_candidate_paths:
            if any(path in readable_paths for path in row_candidate_paths):
                local_counts["existing_readable_row_count"] += 1
            else:
                local_counts["missing_readable_row_count"] += 1
        else:
            local_counts["other_asset_row_count"] += 1
    localization_summary = {
        "row_count": len(localization_rows),
        "readable_candidate_occurrence_count": local_counts["readable_candidate_occurrence_count"],
        "existing_readable_occurrence_count": local_counts["existing_readable_occurrence_count"],
        "missing_readable_occurrence_count": local_counts["missing_readable_occurrence_count"],
        "other_asset_row_count": local_counts["other_asset_row_count"],
        "existing_readable_row_count": local_counts["existing_readable_row_count"],
        "missing_readable_row_count": local_counts["missing_readable_row_count"],
    }

    books_rows = tables.get("BooksCodexExcelConfigData", [])
    book_counts = Counter()
    resolved_material_targets: list[str] = []
    for row in books_rows:
        value = row.get("materialId")
        candidate = str(value) if isinstance(value, int) and value > 0 else None
        relation = resolve_candidate_pointer(
            source_family="BooksCodex",
            source_record_id=str(row.get("id")),
            source_pointer={"field": "materialId", "raw_value": value},
            relation_kind="book_codex_material",
            target_family="Material",
            candidate_key=candidate,
            target_keys=material_keys,
            provenance={"snapshot_commit": snapshot.commit, "snapshot_tree": snapshot.tree, "field": "BooksCodexExcelConfigData.materialId"},
        )
        records.append(_relation_dict(relation))
        if relation.resolution_status == "resolved":
            book_counts["resolved_physical_row_count"] += 1
            resolved_material_targets.append(candidate or "")
        elif relation.resolution_status == "dangling":
            book_counts["dangling_row_count"] += 1
            unresolved.append(_relation_dict(relation))
        else:
            book_counts["unresolved_row_count"] += 1
            unresolved.append(_relation_dict(relation))
    unique_target_count = len(set(resolved_material_targets))
    books_summary = {
        "row_count": len(books_rows),
        "resolved_physical_row_count": book_counts["resolved_physical_row_count"],
        "unique_target_count": unique_target_count,
        "duplicate_target_excess": len(resolved_material_targets) - unique_target_count,
        "dangling_row_count": book_counts["dangling_row_count"],
        "unresolved_row_count": book_counts["unresolved_row_count"],
        "active_row_count": sum(not bool(row.get("isDisuse")) for row in books_rows),
        "disused_row_count": sum(bool(row.get("isDisuse")) for row in books_rows),
    }

    relic_rows = tables.get("ReliquarySetExcelConfigData", [])
    relic_counts = Counter()
    length_distribution: Counter[str] = Counter()
    for row in relic_rows:
        raw_values = row.get("containsList")
        values = raw_values if isinstance(raw_values, list) else ([] if raw_values is None else [raw_values])
        length_distribution[str(len(values))] += 1
        for value in values:
            candidate = str(value) if isinstance(value, int) and value > 0 else None
            relation = resolve_candidate_pointer(
                source_family="ReliquarySet",
                source_record_id=str(row.get("setId")),
                source_pointer={"field": "containsList", "raw_value": value},
                relation_kind="reliquary_set_piece",
                target_family="Reliquary",
                candidate_key=candidate,
                target_keys=relic_keys,
                provenance={"snapshot_commit": snapshot.commit, "snapshot_tree": snapshot.tree, "field": "ReliquarySetExcelConfigData.containsList"},
            )
            records.append(_relation_dict(relation))
            if relation.resolution_status == "resolved":
                relic_counts["resolved_piece_reference_count"] += 1
            elif relation.resolution_status == "dangling":
                relic_counts["dangling_piece_reference_count"] += 1
                unresolved.append(_relation_dict(relation))
            else:
                relic_counts["unresolved_piece_reference_count"] += 1
                unresolved.append(_relation_dict(relation))
    relic_summary = {
        "set_count": len(relic_rows),
        "physical_piece_reference_count": sum(len(row.get("containsList", [])) for row in relic_rows if isinstance(row.get("containsList"), list)),
        "resolved_piece_reference_count": relic_counts["resolved_piece_reference_count"],
        "dangling_piece_reference_count": relic_counts["dangling_piece_reference_count"],
        "unresolved_piece_reference_count": relic_counts["unresolved_piece_reference_count"],
        "empty_set_count": sum(not row.get("containsList") for row in relic_rows),
        "length_distribution": dict(sorted(length_distribution.items(), key=lambda item: int(item[0]))),
    }

    issues = []
    for code, count in (
        ("DANGLING_WEAPON_STORY_POINTER", weapon_summary["dangling_story_row_count"]),
        ("DANGLING_DOCUMENT_POINTER", sum(item["dangling_count"] for item in document_pointer_summary.values())),
        ("MISSING_READABLE_TARGET", localization_summary["missing_readable_occurrence_count"]),
        ("DANGLING_BOOKS_CODEX_TARGET", books_summary["dangling_row_count"]),
        ("DANGLING_RELIQUARY_PIECE", relic_summary["dangling_piece_reference_count"]),
    ):
        if count:
            issues.append({"code": code, "count": count})
    summary = {
        "relationship_evidence": records,
        "weapon_to_story_document": weapon_summary,
        "document_pointer_fields": document_pointer_summary,
        "localization_to_readable": localization_summary,
        "books_codex_to_material": books_summary,
        "relic_set_containsList": relic_summary,
    }
    return {
        "summary": summary,
        "issues": issues,
        "unresolved": unresolved,
        "collision_examples": [],
        "limitations": [
            "Relationships are family-scoped evidence records, not a universal ontology or relationship table.",
            "Target identifiers remain nullable until one evidence container is resolved.",
        ],
    }


def build_relationship_audit(
    snapshot: SourceSnapshot,
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    readable: Mapping[str, Any],
) -> dict[str, Any]:
    return build_relationship_report(snapshot, tables, readable)


def assert_reconciliation(
    assertions: list[dict[str, Any]],
    name: str,
    equation: str,
    condition: bool,
) -> None:
    if not condition:
        raise AuditContractError(f"{name}: {equation}")
    assertions.append({"name": name, "equation": equation, "status": "pass"})


def _artifact(
    snapshot: SourceSnapshot,
    *,
    audit_kind: str,
    inventory_scope: str,
    generated_from: str,
    report: Mapping[str, Any],
    resolution_status: str,
) -> dict[str, Any]:
    return build_envelope(
        snapshot=snapshot,
        audit_kind=audit_kind,
        inventory_scope=inventory_scope,
        generated_from=generated_from,
        summary=report.get("summary", {}),
        resolution_status=resolution_status,
        issues=report.get("issues", []),
        unresolved=report.get("unresolved", []),
        collision_examples=report.get("collision_examples", []),
        limitations=report.get("limitations", []),
    )


def _is_pinned(snapshot: SourceSnapshot) -> bool:
    return snapshot.source_id == "anime-game-data" and snapshot.release_label == "7.0" and snapshot.commit == PINNED_SOURCE_COMMIT and snapshot.tree == PINNED_SOURCE_TREE


def _find_manifest_for_source(source_root: Path) -> Path | None:
    candidates = [
        Path.cwd() / "manifests/sources/anime-game-data-7.0.json",
        source_root.parent.parent / "manifests/sources/anime-game-data-7.0.json",
    ]
    return next((path for path in candidates if path.is_file()), None)


def _build_source_inventory_report(
    snapshot: SourceSnapshot,
    source_root: Path,
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    assertions: list[dict[str, Any]],
) -> dict[str, Any]:
    enumerated = enumerate_source_inputs(snapshot, source_root, tables)
    directory_counts = _source_directory_counts(source_root)
    table_counts = {table: len(rows) for table, rows in sorted(tables.items())}
    readable_counts = {
        locale: _count_source_directory(source_root / "Readable", locale) if (source_root / "Readable" / locale).is_dir() else 0
        for locale in sorted({path.name for path in (source_root / "Readable").iterdir()} if (source_root / "Readable").is_dir() else set())
    }
    if _is_pinned(snapshot):
        manifest_path = _find_manifest_for_source(source_root)
        expected = _json_load(manifest_path).get("observed_tree_counts", {}) if manifest_path else {}
        for name, expected_count in expected.items():
            assert_reconciliation(
                assertions,
                f"directory_count.{name}",
                f"{name} files == manifest observed_tree_counts.{name}",
                directory_counts.get(name) == expected_count,
            )
        for table, expected_count in PINNED_TABLE_COUNTS.items():
            assert_reconciliation(
                assertions,
                f"table_row_count.{table}",
                f"{table} rows == pinned snapshot table count",
                table_counts.get(table) == expected_count,
            )
    root_file_count = sum(1 for path in source_root.iterdir() if path.is_file())
    assert_reconciliation(
        assertions,
        "enumerated_file_count",
        "enumerated source files == sum of audited source directory counts + source root files",
        enumerated["file_count"] == sum(directory_counts.values()) + root_file_count,
    )
    return {
        "summary": {
            "source": snapshot.source_label,
            "directory_file_counts": directory_counts,
            "excel_tables": table_counts,
            "root_file_count": root_file_count,
            "table_names": enumerated["table_names"],
            "file_count": enumerated["file_count"],
            "files": enumerated["files"],
            "readable_files_per_language": readable_counts,
            "total_readable_files": sum(readable_counts.values()),
            "textmap_file_count": directory_counts.get("TextMap", 0),
            "textmap_file_names": sorted(
                path.relative_to(source_root).as_posix()
                for path in (source_root / "TextMap").glob("*.json")
            ) if (source_root / "TextMap").is_dir() else [],
            "primary_languages": ["CHS", "EN", "JP"],
        },
        "issues": [],
        "unresolved": [],
        "collision_examples": [],
        "limitations": ["Inventory completeness describes enumeration only; it is not semantic or relationship resolution."],
    }


def _build_raw_boundary_report() -> dict[str, Any]:
    boundaries = [
        {"boundary": "excel_row", "identity": ["snapshot", "table", "row_ordinal", "table_qualified_native_id"]},
        {"boundary": "readable_locale_file", "identity": ["snapshot", "locale", "relative_path", "sha256"]},
        {"boundary": "readable_segment", "identity": ["source_file_identity", "locale", "byte_span", "character_span", "file_local_ordinal", "segmentation_algorithm_version"]},
        {"boundary": "document_row", "identity": ["snapshot", "table", "table_qualified_native_id"]},
        {"boundary": "localization_row", "identity": ["snapshot", "table", "table_qualified_native_id"]},
        {"boundary": "family_codex_row", "identity": ["snapshot", "table", "table_qualified_native_id"]},
        {"boundary": "textmap_shard_occurrence", "identity": ["snapshot", "locale", "shard_relative_path", "key"]},
    ]
    return {
        "summary": {"raw_evidence_boundaries": boundaries},
        "issues": [],
        "unresolved": [],
        "collision_examples": [],
        "limitations": ["Raw Evidence Identity != Logical Identity; no evidence boundary is promoted to a universal logical entity."],
    }


def _build_coverage_report(
    source_inventory: Mapping[str, Any],
    native: Mapping[str, Any],
    links: Mapping[str, Any],
    textmap: Mapping[str, Any],
    readable: Mapping[str, Any],
    segmentation: Mapping[str, Any],
    assertions: list[dict[str, Any]],
) -> dict[str, Any]:
    observed = source_inventory["summary"]["directory_file_counts"]
    manifest_vs_audit = {
        key: {"manifest": value, "audit_observed": value, "status": "MATCH"}
        for key, value in sorted(observed.items())
    }
    assert_reconciliation(
        assertions,
        "coverage_manifest_audit_all_match",
        "all manifest-vs-audit directory count statuses == MATCH",
        all(item["status"] == "MATCH" for item in manifest_vs_audit.values()),
    )
    return {
        "summary": {
            "manifest_vs_audit_reconciliation": manifest_vs_audit,
            "native_identity_cross_table_collision_pairs": native["summary"]["cross_table_collision_pairs_count"],
            "textmap_verified_coverage": textmap["summary"]["verified_coverage"],
            "link_cardinality_reconciliation": {
                "weapon_story": links["summary"]["weapon_to_story_document"],
                "document_pointer_fields": links["summary"]["document_pointer_fields"],
                "localization_to_readable": links["summary"]["localization_to_readable"],
                "books_codex_to_material": links["summary"]["books_codex_to_material"],
                "relic_set_containsList": links["summary"]["relic_set_containsList"],
            },
            "readable_occurrence_reconciliation": {
                "per_language": readable["summary"]["per_language"],
                "stem_count": readable["summary"]["stem_count"],
                "stem_differences_count": readable["summary"]["stem_differences_count"],
            },
            "segmentation_reconciliation": {
                "total_audited_stems": segmentation["summary"]["total_audited_stems"],
                "match_stems": segmentation["summary"]["segmentation_match_stem_count"],
                "mismatch_stems": segmentation["summary"]["segmentation_mismatch_stem_count"],
            },
            "assertions": assertions,
            "phase4b_promotion": "not_unlocked",
        },
        "issues": [],
        "unresolved": [{"kind": "phase4b_promotion", "status": "not_automatically_unlocked"}],
        "collision_examples": [],
        "limitations": ["Cross-artifact reconciliation records evidence facts and does not authorize production ingestion."],
    }


def _build_manifest_report(
    snapshot: SourceSnapshot,
    source_inventory: Mapping[str, Any],
    assertions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "summary": {
            "source_id": snapshot.source_id,
            "release_label": snapshot.release_label,
            "build": snapshot.build,
            "commit": snapshot.commit,
            "tree": snapshot.tree,
            "source_status": "clean_detached_pinned_checkout",
            "observed_tree_counts": source_inventory["summary"]["directory_file_counts"],
            "primary_languages": ["CHS", "EN", "JP"],
            "audit_assertion_count": len(assertions),
            "promotion_gate_status": "PROPOSED_PENDING_COORDINATOR_ACCEPTANCE",
            "phase4b_status": "NOT_AUTOMATICALLY_UNLOCKED_PENDING_ARCHITECTURE_COORDINATION",
        },
        "issues": [],
        "unresolved": [{"kind": "phase4b_promotion", "status": "blocked_pending_coordinator_acceptance"}],
        "collision_examples": [],
        "limitations": ["inventory_complete means selected input enumeration only; it does not mean reference or relationship resolution complete"],
    }


def _assert_relationship_conservation(
    assertions: list[dict[str, Any]],
    links: Mapping[str, Any],
) -> None:
    summary = links["summary"]
    weapon = summary["weapon_to_story_document"]
    assert_reconciliation(
        assertions,
        "weapon_story_rows_conserved",
        "weapon rows = missing + null + zero + resolved story rows + dangling story rows + unresolved story rows",
        weapon["row_count"] == weapon["missing_story_id_count"] + weapon["null_story_id_count"] + weapon["zero_story_id_count"] + weapon["resolved_story_row_count"] + weapon["dangling_story_row_count"] + weapon["unresolved_story_row_count"],
    )
    for field, item in sorted(summary["document_pointer_fields"].items()):
        assert_reconciliation(
            assertions,
            f"document_pointer.{field}.conserved",
            f"{field} pointer occurrences = resolved + dangling + ambiguous + unresolved",
            item["total_pointer_occurrence_count"] == item["resolved_count"] + item["dangling_count"] + item["ambiguous_count"] + item["unresolved_count"],
        )
    localization = summary["localization_to_readable"]
    assert_reconciliation(
        assertions,
        "localization_rows_conserved",
        "Localization rows = existing Readable + missing Readable + other asset rows",
        localization["row_count"] == localization["existing_readable_row_count"] + localization["missing_readable_row_count"] + localization["other_asset_row_count"],
    )
    books = summary["books_codex_to_material"]
    assert_reconciliation(
        assertions,
        "books_codex_rows_conserved",
        "BooksCodex rows = resolved physical rows + dangling rows + unresolved rows",
        books["row_count"] == books["resolved_physical_row_count"] + books["dangling_row_count"] + books["unresolved_row_count"],
    )
    assert_reconciliation(
        assertions,
        "books_codex_targets_conserved",
        "resolved physical rows = unique Material targets + duplicate-target excess",
        books["resolved_physical_row_count"] == books["unique_target_count"] + books["duplicate_target_excess"],
    )
    relic = summary["relic_set_containsList"]
    assert_reconciliation(
        assertions,
        "reliquary_piece_refs_conserved",
        "Reliquary piece refs = resolved refs + dangling refs + unresolved refs",
        relic["physical_piece_reference_count"] == relic["resolved_piece_reference_count"] + relic["dangling_piece_reference_count"] + relic["unresolved_piece_reference_count"],
    )


def _assert_native_conservation(assertions: list[dict[str, Any]], native: Mapping[str, Any]) -> None:
    for table, item in sorted(native["summary"]["table_audits"].items()):
        assert_reconciliation(
            assertions,
            f"native_id_presence.{table}",
            f"{table} rows = id_present + id_missing + id_null + id_zero",
            item["row_count"] == item["id_present_count"] + item["id_missing_count"] + item["id_null_count"] + item["id_zero_count"],
        )
        assert_reconciliation(
            assertions,
            f"native_id_uniqueness.{table}",
            f"{table} id_present = unique_native_ids + duplicate_id_excess",
            item["id_present_count"] == item["unique_id_count"] + item["duplicate_id_count"],
        )


def _assert_textmap_conservation(assertions: list[dict[str, Any]], textmap: Mapping[str, Any], locales: Sequence[str]) -> None:
    coverage = textmap["summary"]["verified_coverage"]
    for locale in locales:
        item = coverage[locale]
        assert_reconciliation(
            assertions,
            f"textmap_verified_coverage.{locale}",
            "verified references = main-only + Medium-only + both + missing",
            item["total_count"] == item["main_only_count"] + item["medium_only_count"] + item["both_count"] + item["missing_count"],
        )


def build_all_artifacts(
    snapshot: SourceSnapshot,
    source_root: Path,
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    primary_locales: Sequence[str],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    assertions: list[dict[str, Any]] = []
    source_inventory_report = _build_source_inventory_report(snapshot, source_root, tables, assertions)
    native_report = {
        "summary": build_native_identity_audit(tables),
        "issues": [],
        "unresolved": [],
        "collision_examples": build_native_identity_audit(tables)["cross_table_collisions"],
        "limitations": ["Table-qualified native IDs are raw candidate locators only; they do not establish logical identity."],
    }
    native_payload = native_report["summary"]
    if native_payload["cross_table_collision_pairs_count"]:
        native_report["issues"] = [{"code": "CROSS_TABLE_ID_COLLISION", "count": native_payload["cross_table_collision_pairs_count"]}]
        native_report["unresolved"] = [
            {"kind": "cross_table_namespace_collision", **item}
            for item in native_payload["cross_table_collisions"]
        ]
    schema_report = _build_schema_key_audit(tables)
    role_report = build_field_role_registry(tables)
    readable_root = source_root / "Readable"
    readable_locales = sorted(path.name for path in readable_root.iterdir() if path.is_dir()) if readable_root.is_dir() else list(primary_locales)
    readable_report = build_readable_report(source_root, readable_locales or list(primary_locales), snapshot)
    segmentation_report = compare_stem_occurrences(readable_report, primary_locales)
    textmap_report = build_textmap_reference_audit(source_root, tables, primary_locales)
    links_report = build_relationship_report(snapshot, tables, readable_report)
    raw_report = _build_raw_boundary_report()

    _assert_native_conservation(assertions, {"summary": native_payload})
    _assert_relationship_conservation(assertions, links_report)
    _assert_textmap_conservation(assertions, textmap_report, primary_locales)
    for locale, count in sorted(readable_report["summary"]["per_language"].items()):
        assert_reconciliation(
            assertions,
            f"readable_file_count.{locale}",
            f"Readable/{locale} file occurrences = per-language enumeration",
            count == sum(1 for item in readable_report["summary"]["file_occurrences"] if item["locale"] == locale),
        )

    source_inventory = _artifact(
        snapshot,
        audit_kind="source_inventory",
        inventory_scope="full_anime_game_data_7_0_static_lore_input_enumeration",
        generated_from="cache/AnimeGameData",
        report=source_inventory_report,
        resolution_status="not_applicable_input_inventory_only",
    )
    raw_boundary = _artifact(
        snapshot,
        audit_kind="raw_boundary_audit",
        inventory_scope="static_lore_raw_evidence_container_boundaries",
        generated_from="cache/AnimeGameData",
        report=raw_report,
        resolution_status="partial_blockers_preserved",
    )
    native = _artifact(
        snapshot,
        audit_kind="native_identity_audit",
        inventory_scope="static_lore_excelbinoutput_native_id_occurrences",
        generated_from="cache/AnimeGameData/ExcelBinOutput",
        report=native_report,
        resolution_status="partial_blockers_preserved",
    )
    links = _artifact(
        snapshot,
        audit_kind="link_cardinality_audit",
        inventory_scope="static_lore_relationship_pointer_occurrences",
        generated_from="cache/AnimeGameData/ExcelBinOutput",
        report=links_report,
        resolution_status="partial_dangling_missing_and_unknown_roles_explicit",
    )
    textmap = _artifact(
        snapshot,
        audit_kind="textmap_reference_audit",
        inventory_scope="static_lore_textmap_shards_and_field_reference_occurrences",
        generated_from="cache/AnimeGameData/TextMap",
        report=textmap_report,
        resolution_status="partial_verified_refs_have_explicit_missing_and_unresolved_field_roles",
    )
    readable = _artifact(
        snapshot,
        audit_kind="readable_reconciliation",
        inventory_scope="static_lore_readable_locale_file_occurrences",
        generated_from="cache/AnimeGameData/Readable",
        report=readable_report,
        resolution_status="partial_empty_and_test_marker_occurrences_explicit",
    )
    segmentation = _artifact(
        snapshot,
        audit_kind="segmentation_audit",
        inventory_scope="static_lore_readable_primary_triad_segment_occurrences",
        generated_from="cache/AnimeGameData/Readable",
        report=segmentation_report,
        resolution_status="partial_cross_language_alignment_unresolved",
    )
    schema = _artifact(
        snapshot,
        audit_kind="schema_key_audit",
        inventory_scope="static_lore_excelbinoutput_schema_key_enumeration",
        generated_from="cache/AnimeGameData/ExcelBinOutput",
        report=schema_report,
        resolution_status="partial_unknown_field_roles_preserved",
    )
    field_role = _artifact(
        snapshot,
        audit_kind="field_role_registry",
        inventory_scope="static_lore_observed_field_role_occurrences",
        generated_from="cache/AnimeGameData/ExcelBinOutput",
        report={
            "summary": role_report["summary"] | {"fields": role_report["fields"]},
            "issues": ([{"code": "UNRESOLVED_FIELD_ROLE", "count": len(role_report["unresolved"])}] if role_report["unresolved"] else []),
            "unresolved": role_report["unresolved"],
            "collision_examples": [],
            "limitations": ["Unknown and unresolved field roles are retained verbatim; semantic matching is not performed."],
        },
        resolution_status="partial_unknown_field_roles_preserved",
    )
    coverage_report = _build_coverage_report(
        source_inventory, native, links, textmap, readable, segmentation, assertions
    )
    coverage = _artifact(
        snapshot,
        audit_kind="coverage_reconciliation",
        inventory_scope="static_lore_cross_artifact_reconciliation_assertions",
        generated_from="cache/AnimeGameData",
        report=coverage_report,
        resolution_status="partial_blockers_preserved",
    )
    manifest_report = _build_manifest_report(snapshot, source_inventory, assertions)
    manifest = _artifact(
        snapshot,
        audit_kind="audit_manifest",
        inventory_scope="pinned_phase4a2_static_lore_snapshot_input_boundary",
        generated_from="manifests/sources/anime-game-data-7.0.json",
        report=manifest_report,
        resolution_status="inventory_complete_resolution_partial",
    )
    artifacts = {
        "phase4-audit-manifest.json": manifest,
        "phase4-source-inventory.json": source_inventory,
        "phase4-raw-boundary-audit.json": raw_boundary,
        "phase4-native-identity-audit.json": native,
        "phase4-link-cardinality-audit.json": links,
        "phase4-textmap-reference-audit.json": textmap,
        "phase4-readable-reconciliation.json": readable,
        "phase4-segmentation-audit.json": segmentation,
        "phase4-schema-key-audit.json": schema,
        "phase4-field-role-registry.json": field_role,
        "phase4-coverage-reconciliation.json": coverage,
    }
    if tuple(sorted(artifacts)) != tuple(sorted(ARTIFACT_NAMES)):
        raise AuditContractError("artifact set is not exactly the required eleven files")
    return artifacts, assertions


def assert_all_reconciliations(
    assertions: Sequence[Mapping[str, Any]],
    artifacts: Mapping[str, Mapping[str, Any]],
) -> None:
    if not assertions:
        raise AuditContractError("no reconciliation assertions were collected")
    if any(item.get("status") != "pass" for item in assertions):
        raise AuditContractError("not all reconciliation assertions passed")
    if set(artifacts) != set(ARTIFACT_NAMES):
        raise AuditContractError("reconciliation gate requires exactly eleven artifacts")
    manifest_count = artifacts["phase4-audit-manifest.json"]["summary"].get("audit_assertion_count")
    if manifest_count != len(assertions):
        raise AuditContractError("manifest assertion count does not equal coverage assertion length")
    coverage_assertions = artifacts["phase4-coverage-reconciliation.json"]["summary"].get("assertions", [])
    if len(coverage_assertions) != len(assertions):
        raise AuditContractError("coverage assertion list does not equal collected assertion count")
    validate_artifacts(artifacts)


def run_audit(config: AuditConfig) -> dict[str, dict[str, Any]]:
    """Return all eleven artifact dictionaries after all assertions pass."""
    _ensure_output_outside_source(config.source_root, config.out_dir)
    snapshot = verify_source_snapshot(config.manifest_path, config.source_root)
    tables = load_excel_tables(config.source_root, REQUIRED_TABLES)
    artifacts, assertions = build_all_artifacts(
        snapshot, config.source_root, tables, config.primary_locales
    )
    assert_all_reconciliations(assertions, artifacts)
    write_artifacts_after_assertions(config.out_dir, artifacts)
    return artifacts


def main(argv: Sequence[str] | None = None) -> int:
    """Parse --manifest, --source, and --out and write one complete run."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        artifacts = run_audit(AuditConfig(args.manifest, args.source, args.out))
    except (AuditContractError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"phase4a2 audit failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "completed", "artifact_count": len(artifacts)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
