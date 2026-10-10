"""Build a sealed, run-scoped Material evidence bundle.

Phase 4B6 deliberately owns only the Material and MaterialCodex source
families.  It stores raw rows and field-level TextMap pointer evidence; it
does not introduce a logical Material, a universal static adapter, or a
cross-version passage identity.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .static_phase4b6_material_verification_contract import (
    expected_phase4b6_source_snapshot_binding,
)
from .util import canonical_json, digest_id, normalize_text, sha256_bytes, sha256_text


LOCALES = ("CHS", "EN", "JP")
FAMILY = "materials"
TABLE_FILES = {
    "MaterialExcelConfigData": "ExcelBinOutput/MaterialExcelConfigData.json",
    "MaterialCodexExcelConfigData": "ExcelBinOutput/MaterialCodexExcelConfigData.json",
}
TEXT_FIELDS = {
    "MaterialExcelConfigData": (
        "nameTextMapHash",
        "descTextMapHash",
        "effectDescTextMapHash",
        "interactionTitleTextMapHash",
        "specialDescTextMapHash",
        "typeDescTextMapHash",
    ),
    "MaterialCodexExcelConfigData": (
        "nameTextMapHash",
        "descTextMapHash",
        "questReplaceName1TextMapHash",
        "questReplaceName2TextMapHash",
        "questReplaceDesc1TextMapHash",
        "questReplaceDesc2TextMapHash",
    ),
}
TEXT_RELATION_KINDS = {
    ("MaterialExcelConfigData", "nameTextMapHash"): "material_name_textmap_candidate",
    ("MaterialExcelConfigData", "descTextMapHash"): "material_description_textmap_candidate",
    ("MaterialExcelConfigData", "effectDescTextMapHash"): "material_effect_description_textmap_candidate",
    ("MaterialExcelConfigData", "interactionTitleTextMapHash"): "material_interaction_title_textmap_candidate",
    ("MaterialExcelConfigData", "specialDescTextMapHash"): "material_special_description_textmap_candidate",
    ("MaterialExcelConfigData", "typeDescTextMapHash"): "material_type_description_textmap_candidate",
    ("MaterialCodexExcelConfigData", "nameTextMapHash"): "material_codex_name_textmap_candidate",
    ("MaterialCodexExcelConfigData", "descTextMapHash"): "material_codex_description_textmap_candidate",
    ("MaterialCodexExcelConfigData", "questReplaceName1TextMapHash"): "material_codex_quest_replace_name1_textmap_candidate",
    ("MaterialCodexExcelConfigData", "questReplaceName2TextMapHash"): "material_codex_quest_replace_name2_textmap_candidate",
    ("MaterialCodexExcelConfigData", "questReplaceDesc1TextMapHash"): "material_codex_quest_replace_desc1_textmap_candidate",
    ("MaterialCodexExcelConfigData", "questReplaceDesc2TextMapHash"): "material_codex_quest_replace_desc2_textmap_candidate",
}
TEXT_FIELD_ROLES = {
    ("MaterialExcelConfigData", "nameTextMapHash"): "display_name",
    ("MaterialExcelConfigData", "descTextMapHash"): "description",
    ("MaterialExcelConfigData", "effectDescTextMapHash"): "effect_description",
    ("MaterialExcelConfigData", "interactionTitleTextMapHash"): "interaction_title",
    ("MaterialExcelConfigData", "specialDescTextMapHash"): "special_description",
    ("MaterialExcelConfigData", "typeDescTextMapHash"): "type_description",
    ("MaterialCodexExcelConfigData", "nameTextMapHash"): "display_name",
    ("MaterialCodexExcelConfigData", "descTextMapHash"): "description",
    ("MaterialCodexExcelConfigData", "questReplaceName1TextMapHash"): "state_variant_name_1",
    ("MaterialCodexExcelConfigData", "questReplaceName2TextMapHash"): "state_variant_name_2",
    ("MaterialCodexExcelConfigData", "questReplaceDesc1TextMapHash"): "state_variant_description_1",
    ("MaterialCodexExcelConfigData", "questReplaceDesc2TextMapHash"): "state_variant_description_2",
}
TEXTMAP_RELATIVE_DEFAULTS = {
    locale: {
        "main": f"TextMap/TextMap{locale}.json",
        "medium": f"TextMap/TextMap_Medium{locale}.json",
    }
    for locale in LOCALES
}


class Phase4B6EvidenceError(RuntimeError):
    """The sealed source, manifest, or schema-9 target failed closed."""


@dataclass(frozen=True)
class Phase4B6File:
    spec: Any
    path: Path
    root: Path
    raw: bytes
    parsed: Any

    @property
    def relative_path(self) -> str:
        return str(self.spec.relative_path)


@dataclass(frozen=True)
class Phase4B6SourceRecord:
    relative_path: str
    pointer: str
    record_kind: str
    native_id: str
    raw_sha256: str
    raw_json: str
    raw_value: Any

    @property
    def coordinate(self) -> tuple[str, str]:
        return self.relative_path, self.pointer


@dataclass(frozen=True)
class Phase4B6Container:
    family: str
    relative_path: str
    record_pointer: str
    record_kind: str
    native_id: str
    container_kind: str
    source_family: str
    locale: str | None
    candidate_namespace: str
    candidate_key: dict[str, Any]
    raw_value: Any
    raw_json: str
    metadata: dict[str, Any]

    @property
    def coordinate(self) -> tuple[str, str]:
        return self.relative_path, self.record_pointer


@dataclass(frozen=True)
class Phase4B6Relationship:
    family: str
    source_relative_path: str
    source_record_pointer: str
    source_family: str
    source_pointer: str
    relation_kind: str
    field_role: str
    target_family: str | None
    target_candidate_key: dict[str, Any] | None
    status: str
    target_container_coordinate: tuple[str, str] | None
    raw_value: Any
    ambiguity: dict[str, Any]

    @property
    def coordinate(self) -> tuple[str, str, str]:
        return self.source_relative_path, self.source_record_pointer, self.source_pointer


@dataclass(frozen=True)
class Phase4B6TextCandidate:
    family: str
    owning_source_relative_path: str
    owning_source_record_pointer: str
    relationship_source_pointer: str
    locale: str
    textmap_hash: str
    status: str
    shard_kind: str
    source_relative_path: str
    source_record_pointer: str
    raw_text: str
    normalized_text: str
    raw_checksum: str
    normalized_checksum: str


@dataclass(frozen=True)
class Phase4B6TextResolution:
    family: str
    owning_source_relative_path: str
    owning_source_record_pointer: str
    relationship_source_pointer: str
    locale: str
    textmap_hash: str
    status: str
    raw_text: str | None
    normalized_text: str | None
    raw_checksum: str | None
    normalized_checksum: str | None

    @property
    def owner_source_pointer(self) -> str:
        return self.relationship_source_pointer

    @property
    def occurrence_coordinate(self) -> tuple[str, str, str, str]:
        return (
            self.owning_source_relative_path,
            self.owning_source_record_pointer,
            self.relationship_source_pointer,
            self.locale,
        )


@dataclass(frozen=True)
class Phase4B6ResolvedTextMapOccurrence:
    status: str
    raw_text: str | None
    normalized_text: str | None
    raw_checksum: str | None
    normalized_checksum: str | None
    candidates: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class Phase4B6PreflightResult:
    manifest: Any
    source_root: Path
    snapshot_id: str
    parser_build_id: str
    ingest_run_id: str
    config_checksum: str
    files: tuple[Phase4B6File, ...]
    source_records: tuple[Phase4B6SourceRecord, ...]
    containers: tuple[Phase4B6Container, ...]
    segments: tuple[Any, ...]
    relationships: tuple[Phase4B6Relationship, ...]
    text_resolutions: tuple[Phase4B6TextResolution, ...]
    text_candidates: tuple[Phase4B6TextCandidate, ...]
    status_counts: dict[str, dict[str, int]]
    family_counts: dict[str, dict[str, int]]
    occurrence_set_digests: dict[str, str]
    family_selected_paths: dict[str, list[str]]
    locale_breakdown: dict[str, dict[str, int]]
    text_owner_occurrences: int
    text_asset_occurrences: int


def _set_digest(items: list[dict[str, Any]]) -> str:
    ordered = sorted(canonical_json(item) for item in items)
    return sha256_text(canonical_json(ordered))


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def resolve_phase4b6_textmap_occurrence(
    main_text: str | None,
    medium_text: str | None,
) -> Phase4B6ResolvedTextMapOccurrence:
    if main_text is None and medium_text is None:
        return Phase4B6ResolvedTextMapOccurrence("missing", None, None, None, None, ())
    if main_text is not None and medium_text is None:
        normalized = normalize_text(main_text)
        return Phase4B6ResolvedTextMapOccurrence(
            "unique_main",
            main_text,
            normalized,
            sha256_text(main_text),
            sha256_text(normalized),
            ({"shard_kind": "main", "raw_text": main_text},),
        )
    if main_text is None and medium_text is not None:
        normalized = normalize_text(medium_text)
        return Phase4B6ResolvedTextMapOccurrence(
            "unique_medium",
            medium_text,
            normalized,
            sha256_text(medium_text),
            sha256_text(normalized),
            ({"shard_kind": "medium", "raw_text": medium_text},),
        )
    if main_text == medium_text:
        normalized = normalize_text(main_text)
        return Phase4B6ResolvedTextMapOccurrence(
            "both_equal",
            main_text,
            normalized,
            sha256_text(main_text),
            sha256_text(normalized),
            (
                {"shard_kind": "main", "raw_text": main_text},
                {"shard_kind": "medium", "raw_text": medium_text},
            ),
        )
    return Phase4B6ResolvedTextMapOccurrence(
        "both_conflict",
        None,
        None,
        None,
        None,
        (
            {"shard_kind": "main", "raw_text": main_text},
            {"shard_kind": "medium", "raw_text": medium_text},
        ),
    )


def _candidate_dicts(outcome: Phase4B6ResolvedTextMapOccurrence) -> tuple[dict[str, str], ...]:
    return tuple(outcome.candidates)


def _manifest_from_internal_input(manifest_input: Any) -> Any:
    if not isinstance(manifest_input, (str, Path)):
        return manifest_input
    try:
        from .static_phase4b6_material_manifest import load_static_phase4b6_material_manifest
    except ModuleNotFoundError as exc:
        raise Phase4B6EvidenceError(
            "Phase 4B6 manifest loader is unavailable; integrate the Lane A manifest module"
        ) from exc
    try:
        return load_static_phase4b6_material_manifest(manifest_input)
    except Exception as exc:
        if isinstance(exc, Phase4B6EvidenceError):
            raise
        raise Phase4B6EvidenceError(f"cannot load Phase 4B6 manifest: {exc}") from exc


def _manifest_attr(manifest: Any, name: str, default: Any = None) -> Any:
    return getattr(manifest, name, default)


def _verify_source_checkout(root: Path, manifest: Any) -> None:
    source = _manifest_attr(manifest, "source", {}) or {}
    expected_commit = source.get("commit")
    expected_tree = source.get("tree")
    if not expected_commit or not expected_tree:
        raise Phase4B6EvidenceError("manifest source must pin commit and tree")
    try:
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError) as exc:
        raise Phase4B6EvidenceError(f"source checkout verification failed: {exc}") from exc
    if commit != expected_commit:
        raise Phase4B6EvidenceError(
            f"source checkout commit mismatch: expected {expected_commit}, got {commit}"
        )
    if tree != expected_tree:
        raise Phase4B6EvidenceError(
            f"source checkout tree mismatch: expected {expected_tree}, got {tree}"
        )
    if dirty:
        raise Phase4B6EvidenceError(f"source checkout is not clean:\n{dirty}")


def _relative_path(value: Any) -> str:
    path = str(value)
    pure = PurePosixPath(path)
    if (
        not path
        or "\\" in path
        or ":" in path
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or pure.as_posix() != path
    ):
        raise Phase4B6EvidenceError(f"manifest path is not canonical relative POSIX path: {path!r}")
    return path


def _load_files(root: Path, manifest: Any) -> tuple[Phase4B6File, ...]:
    specs = tuple(_manifest_attr(manifest, "files", ()) or ())
    if not specs:
        raise Phase4B6EvidenceError("Phase 4B6 manifest has no selected files")
    result: list[Phase4B6File] = []
    seen: set[str] = set()
    for spec in specs:
        relative = _relative_path(getattr(spec, "relative_path", None))
        if relative in seen:
            raise Phase4B6EvidenceError(f"duplicate selected file: {relative}")
        seen.add(relative)
        target = (root / Path(*PurePosixPath(relative).parts)).resolve()
        if root not in target.parents:
            raise Phase4B6EvidenceError(f"selected file escapes source root: {relative}")
        if not target.is_file():
            raise Phase4B6EvidenceError(f"selected manifest file not found: {relative}")
        raw = target.read_bytes()
        expected_size = getattr(spec, "size_bytes", None)
        expected_sha = getattr(spec, "sha256", None)
        if expected_size is not None and len(raw) != expected_size:
            raise Phase4B6EvidenceError(
                f"file size mismatch for {relative}: expected {expected_size}, got {len(raw)}"
            )
        if expected_sha is not None and sha256_bytes(raw) != expected_sha:
            raise Phase4B6EvidenceError(f"file sha256 mismatch for {relative}")
        fmt = getattr(spec, "format", "json")
        if fmt == "json":
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise Phase4B6EvidenceError(f"cannot parse JSON file {relative}: {exc}") from exc
        elif fmt == "txt":
            try:
                parsed = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise Phase4B6EvidenceError(f"invalid UTF-8 file {relative}: {exc}") from exc
        else:
            raise Phase4B6EvidenceError(f"unsupported selected file format: {fmt}")
        result.append(Phase4B6File(spec, target, root, raw, parsed))
    return tuple(result)


def _table_field(entry: Any, name: str, default: Any = None) -> Any:
    if isinstance(entry, dict):
        return entry.get(name, default)
    return getattr(entry, name, default)


def _table_specs(manifest: Any) -> dict[str, Any]:
    tables = _manifest_attr(manifest, "tables", {}) or {}
    missing = [table for table in TABLE_FILES if table not in tables]
    if missing:
        raise Phase4B6EvidenceError(f"manifest missing Material table contracts: {missing}")
    return {table: tables[table] for table in TABLE_FILES}


def _selected_table_rows(
    manifest: Any,
    files: dict[str, Phase4B6File],
) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, dict[str, tuple[int, dict[str, Any]]]],
    dict[str, set[int]],
]:
    table_specs = _table_specs(manifest)
    tables: dict[str, list[dict[str, Any]]] = {}
    indexes: dict[str, dict[str, tuple[int, dict[str, Any]]]] = {}
    selected_indexes: dict[str, set[int]] = {}
    for table, entry in table_specs.items():
        relative = _relative_path(_table_field(entry, "relative_path", TABLE_FILES[table]))
        if relative != TABLE_FILES[table] or relative not in files:
            raise Phase4B6EvidenceError(f"manifest table file binding mismatch: {table}")
        parsed = files[relative].parsed
        if not isinstance(parsed, list) or not all(isinstance(row, dict) for row in parsed):
            raise Phase4B6EvidenceError(f"{table} is not an array of objects")
        inventory = _table_field(entry, "source_inventory_count")
        if inventory is not None and len(parsed) != inventory:
            raise Phase4B6EvidenceError(f"{table} inventory mismatch: {len(parsed)} != {inventory}")
        native_field = _table_field(entry, "native_field", "id")
        if native_field != "id":
            raise Phase4B6EvidenceError(f"{table} native field must be id")
        index: dict[str, tuple[int, dict[str, Any]]] = {}
        for row_index, row in enumerate(parsed):
            if native_field not in row:
                raise Phase4B6EvidenceError(f"{table} row {row_index} has no native id")
            native_id = str(row[native_field])
            if native_id in index:
                raise Phase4B6EvidenceError(f"duplicate {table}.id={native_id}")
            index[native_id] = row_index, row
        selected_rows = _table_field(entry, "selected_rows")
        if selected_rows is None:
            selected_rows = [
                {
                    "pointer": f"/{row_index}",
                    "native_id": str(row["id"]),
                    "raw_sha256": sha256_text(canonical_json(row)),
                    "family": FAMILY,
                }
                for row_index, row in enumerate(parsed)
            ]
        if not isinstance(selected_rows, list):
            raise Phase4B6EvidenceError(f"{table}.selected_rows must be an array")
        selected: set[int] = set()
        for selected_row in selected_rows:
            if not isinstance(selected_row, dict):
                raise Phase4B6EvidenceError(f"{table}.selected_rows contains a non-object")
            pointer = selected_row.get("pointer")
            if not isinstance(pointer, str) or not pointer.startswith("/") or not pointer[1:].isdigit():
                raise Phase4B6EvidenceError(f"{table} selected pointer is invalid: {pointer!r}")
            row_index = int(pointer[1:])
            if row_index < 0 or row_index >= len(parsed) or row_index in selected:
                raise Phase4B6EvidenceError(f"{table} selected pointer is out of range or duplicate")
            row = parsed[row_index]
            native_id = str(row["id"])
            if selected_row.get("native_id") != native_id:
                raise Phase4B6EvidenceError(f"{table} selected native ID mismatch at {pointer}")
            expected_raw_sha = sha256_text(canonical_json(row))
            if selected_row.get("raw_sha256") not in (None, expected_raw_sha):
                raise Phase4B6EvidenceError(f"{table} selected row raw checksum mismatch at {pointer}")
            if selected_row.get("family", FAMILY) != FAMILY:
                raise Phase4B6EvidenceError(f"{table} selected family is not {FAMILY}")
            selected.add(row_index)
        selected_count = _table_field(entry, "selected_count")
        if selected_count is not None and selected_count != len(selected):
            raise Phase4B6EvidenceError(f"{table} selected row count mismatch")
        selected_digest = _table_field(entry, "selected_set_digest")
        if selected_digest and selected_digest != "synthetic":
            rows_for_digest = [
                {
                    "pointer": f"/{row_index}",
                    "native_id": str(parsed[row_index]["id"]),
                    "raw_sha256": sha256_text(canonical_json(parsed[row_index])),
                    "family": FAMILY,
                }
                for row_index in sorted(selected)
            ]
            if _set_digest(rows_for_digest) != selected_digest:
                raise Phase4B6EvidenceError(f"{table} selected row digest mismatch")
        tables[table] = parsed
        indexes[table] = index
        selected_indexes[table] = selected
    return tables, indexes, selected_indexes


def _textmap_paths(manifest: Any) -> dict[str, dict[str, str]]:
    boundaries = _manifest_attr(manifest, "boundaries", {}) or {}
    value = boundaries.get("textmap_shards", TEXTMAP_RELATIVE_DEFAULTS)
    result: dict[str, dict[str, str]] = {}
    for locale in LOCALES:
        item = value.get(locale, TEXTMAP_RELATIVE_DEFAULTS[locale])
        result[locale] = {
            "main": _relative_path(item["main"]),
            "medium": _relative_path(item["medium"]),
        }
    return result


def _text_field_contract(manifest: Any) -> dict[tuple[str, str], tuple[str, str]]:
    declared = _manifest_attr(manifest, "text_fields", None)
    supported = {
        (table_name, field_name)
        for table_name, names in TEXT_FIELDS.items()
        for field_name in names
    }
    if declared:
        result: dict[tuple[str, str], tuple[str, str]] = {}
        for item in declared:
            if not isinstance(item, dict):
                raise Phase4B6EvidenceError("manifest text_fields contains a non-object")
            table = item.get("table")
            field = item.get("field")
            role = item.get("field_role")
            relation_kind = item.get("relation_kind")
            if (table, field) not in supported:
                raise Phase4B6EvidenceError(f"unsupported Material TextMap field contract: {table}.{field}")
            if not isinstance(role, str) or not role or not isinstance(relation_kind, str) or not relation_kind:
                raise Phase4B6EvidenceError(f"invalid Material TextMap field contract: {table}.{field}")
            if (table, field) in result:
                raise Phase4B6EvidenceError(f"duplicate Material TextMap field contract: {table}.{field}")
            result[(table, field)] = role, relation_kind
        if set(result) != supported:
            raise Phase4B6EvidenceError("manifest Material TextMap field contract is incomplete")
        return result
    return {
        key: (TEXT_FIELD_ROLES[key], TEXT_RELATION_KINDS[key])
        for key in TEXT_FIELD_ROLES
    }


def _raw_hash(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def _validate_canaries(
    manifest: Any,
    tables: dict[str, list[dict[str, Any]]],
    selected_indexes: dict[str, set[int]],
) -> None:
    """Rebuild the sealed source canaries from raw rows, never from metadata.

    Lane A's manifest loader validates the shape of the canary declaration;
    this builder still checks the declaration against the pinned bytes so a
    stale/incorrect row seal cannot silently authorize a write.
    """
    canaries = _manifest_attr(manifest, "canaries", None)
    if not canaries:
        return
    if not isinstance(canaries, dict):
        raise Phase4B6EvidenceError("manifest canaries must be an object")

    material = canaries.get("material_101001")
    if material is not None:
        table = "MaterialExcelConfigData"
        pointer = material.get("pointer") if isinstance(material, dict) else None
        if pointer != "/636":
            raise Phase4B6EvidenceError("Material canary pointer must be /636")
        row_index = 636
        row = tables[table][row_index] if row_index < len(tables[table]) else None
        if row_index not in selected_indexes[table] or row is None:
            raise Phase4B6EvidenceError("Material canary row /636 is not selected")
        if str(row.get("id")) != str(material.get("native_id")):
            raise Phase4B6EvidenceError("Material canary native ID mismatch")
        if _raw_hash(row) != material.get("row_raw_sha256"):
            raise Phase4B6EvidenceError("Material canary row raw checksum mismatch")

    codex = canaries.get("material_codex_40100001")
    if codex is not None:
        table = "MaterialCodexExcelConfigData"
        pointer = codex.get("pointer") if isinstance(codex, dict) else None
        if pointer != "/0":
            raise Phase4B6EvidenceError("Material Codex canary pointer must be /0")
        row = tables[table][0] if tables[table] else None
        if 0 not in selected_indexes[table] or row is None:
            raise Phase4B6EvidenceError("Material Codex canary row /0 is not selected")
        if str(row.get("id")) != str(codex.get("native_id")):
            raise Phase4B6EvidenceError("Material Codex canary native ID mismatch")
        if str(row.get("materialId")) != str(codex.get("material_id")):
            raise Phase4B6EvidenceError("Material Codex canary material pointer mismatch")
        replacement_fields = codex.get("replacement_fields")
        replacement_values = codex.get("replacement_values")
        if not isinstance(replacement_fields, list) or not isinstance(replacement_values, dict):
            raise Phase4B6EvidenceError("Material Codex canary replacement contract is invalid")
        if any(row.get(field) != replacement_values.get(field) for field in replacement_fields):
            raise Phase4B6EvidenceError("Material Codex canary replacement value mismatch")


def _preflight_static_phase4b6_material_evidence_bundle_internal(
    source_root: str | Path,
    manifest_input: Any,
) -> Phase4B6PreflightResult:
    """Private synthetic-fixture seam; public callers must provide a path."""
    root = Path(source_root).resolve()
    if not root.is_dir():
        raise Phase4B6EvidenceError(f"source root directory not found: {root}")
    manifest = _manifest_from_internal_input(manifest_input)
    _verify_source_checkout(root, manifest)
    loaded = _load_files(root, manifest)
    files = {item.relative_path: item for item in loaded}
    tables, indexes, selected_indexes = _selected_table_rows(manifest, files)
    _validate_canaries(manifest, tables, selected_indexes)
    textmap_paths = _textmap_paths(manifest)
    text_field_contract = _text_field_contract(manifest)
    fields_by_table = {
        table: tuple(field for (owner_table, field) in text_field_contract if owner_table == table)
        for table in TABLE_FILES
    }
    for locale in LOCALES:
        for shard_kind in ("main", "medium"):
            relative = textmap_paths[locale][shard_kind]
            if relative not in files or not isinstance(files[relative].parsed, dict):
                raise Phase4B6EvidenceError(f"missing or invalid TextMap shard: {relative}")

    manifest_id = str(_manifest_attr(manifest, "manifest_id", "phase4b6-material"))
    manifest_sha = str(_manifest_attr(manifest, "raw_sha256", ""))
    if not manifest_sha:
        manifest_sha = sha256_text(canonical_json(_manifest_attr(manifest, "raw", {})))

    containers: list[Phase4B6Container] = []
    for table in TABLE_FILES:
        relative = TABLE_FILES[table]
        for row_index in sorted(selected_indexes[table]):
            row = tables[table][row_index]
            native_id = str(row["id"])
            containers.append(
                Phase4B6Container(
                    family=FAMILY,
                    relative_path=relative,
                    record_pointer=f"/{row_index}",
                    record_kind="static_excel_row",
                    native_id=native_id,
                    container_kind="excel_row",
                    source_family=table,
                    locale=None,
                    candidate_namespace=f"agd.static.{table}",
                    candidate_key={"table": table, "native_id": native_id},
                    raw_value=row,
                    raw_json=canonical_json(row),
                    metadata={
                        "family": FAMILY,
                        "manifest_id": manifest_id,
                        "manifest_raw_sha256": manifest_sha,
                        "raw_evidence_identity": "snapshot_source_coordinate_ingest_run_occurrence",
                    },
                )
            )
    container_lookup = {item.coordinate: item for item in containers}

    relationships: list[Phase4B6Relationship] = []

    def add_relation(
        *,
        table: str,
        row_index: int,
        source_pointer: str,
        relation_kind: str,
        field_role: str,
        target_family: str | None,
        target_candidate_key: dict[str, Any] | None,
        status: str,
        target_coordinate: tuple[str, str] | None,
        raw_value: Any,
        ambiguity: dict[str, Any] | None = None,
    ) -> None:
        relationships.append(
            Phase4B6Relationship(
                family=FAMILY,
                source_relative_path=TABLE_FILES[table],
                source_record_pointer=f"/{row_index}",
                source_family=table,
                source_pointer=source_pointer,
                relation_kind=relation_kind,
                field_role=field_role,
                target_family=target_family,
                target_candidate_key=target_candidate_key,
                status=status,
                target_container_coordinate=target_coordinate,
                raw_value=raw_value,
                ambiguity=ambiguity or {},
            )
        )

    # MaterialCodex.materialId is a candidate pointer, not an identity join.
    for row_index in sorted(selected_indexes["MaterialCodexExcelConfigData"]):
        row = tables["MaterialCodexExcelConfigData"][row_index]
        raw_value = row.get("materialId")
        material_id = _positive_int(raw_value)
        target = indexes["MaterialExcelConfigData"].get(str(material_id)) if material_id is not None else None
        if material_id is None:
            status, target_key, target_coordinate = "unresolved", None, None
        elif target is None or target[0] not in selected_indexes["MaterialExcelConfigData"]:
            status = "dangling"
            target_key = {"table": "MaterialExcelConfigData", "native_id": str(material_id)}
            target_coordinate = None
        else:
            status = "resolved"
            target_key = {"table": "MaterialExcelConfigData", "native_id": str(material_id)}
            target_coordinate = (TABLE_FILES["MaterialExcelConfigData"], f"/{target[0]}")
            canaries = _manifest_attr(manifest, "canaries", {}) or {}
            codex_canary = canaries.get("material_codex_40100001") if isinstance(canaries, dict) else None
            if row_index == 0 and codex_canary is not None and target[0] != 636:
                raise Phase4B6EvidenceError("Material Codex canary /0 must target Material /636")
        add_relation(
            table="MaterialCodexExcelConfigData",
            row_index=row_index,
            source_pointer=f"/{row_index}/materialId",
            relation_kind="material_codex_material_candidate",
            field_role="foreign_pointer_candidate",
            target_family="MaterialExcelConfigData",
            target_candidate_key=target_key,
            status=status,
            target_coordinate=target_coordinate,
            raw_value=raw_value,
        )

    text_owners: list[dict[str, Any]] = []
    for table in TABLE_FILES:
        fields = fields_by_table[table]
        for row_index in sorted(selected_indexes[table]):
            row = tables[table][row_index]
            for field in fields:
                field_role, relation_kind = text_field_contract[(table, field)]
                raw_value = row.get(field)
                textmap_hash = _positive_int(raw_value)
                if textmap_hash is None:
                    add_relation(
                        table=table,
                        row_index=row_index,
                        source_pointer=f"/{row_index}/{field}",
                        relation_kind=relation_kind,
                        field_role=field_role,
                        target_family="TextMap",
                        target_candidate_key=None,
                        status="unresolved",
                        target_coordinate=None,
                        raw_value=raw_value,
                    )
                    continue
                text_owners.append(
                    {
                        "family": FAMILY,
                        "source_table": table,
                        "source_record_pointer": f"/{row_index}",
                        "source_pointer": f"/{row_index}/{field}",
                        "field": field,
                        "field_role": field_role,
                        "relation_kind": relation_kind,
                        "textmap_hash": str(textmap_hash),
                    }
                )

    text_resolutions: list[Phase4B6TextResolution] = []
    text_candidates: list[Phase4B6TextCandidate] = []
    for owner in text_owners:
        table = owner["source_table"]
        row_index = int(owner["source_record_pointer"][1:])
        field = owner["field"]
        textmap_hash = int(owner["textmap_hash"])
        source_pointer = owner["source_pointer"]
        relation_kind = owner["relation_kind"]
        add_relation(
            table=table,
            row_index=row_index,
            source_pointer=source_pointer,
            relation_kind=relation_kind,
            field_role=owner["field_role"],
            target_family="TextMap",
            target_candidate_key={"textmap_hash": owner["textmap_hash"]},
            status="resolved",
            target_coordinate=None,
            raw_value=textmap_hash,
        )
        for locale in LOCALES:
            main_path = textmap_paths[locale]["main"]
            medium_path = textmap_paths[locale]["medium"]
            main_map = files[main_path].parsed
            medium_map = files[medium_path].parsed
            key = str(textmap_hash)
            main_text = main_map.get(key)
            medium_text = medium_map.get(key)
            if main_text is not None and not isinstance(main_text, str):
                raise Phase4B6EvidenceError(f"TextMap value is not text: {main_path}#{key}")
            if medium_text is not None and not isinstance(medium_text, str):
                raise Phase4B6EvidenceError(f"TextMap value is not text: {medium_path}#{key}")
            outcome = resolve_phase4b6_textmap_occurrence(main_text, medium_text)
            text_resolutions.append(
                Phase4B6TextResolution(
                    family=FAMILY,
                    owning_source_relative_path=TABLE_FILES[table],
                    owning_source_record_pointer=f"/{row_index}",
                    relationship_source_pointer=source_pointer,
                    locale=locale,
                    textmap_hash=key,
                    status=outcome.status,
                    raw_text=outcome.raw_text,
                    normalized_text=outcome.normalized_text,
                    raw_checksum=outcome.raw_checksum,
                    normalized_checksum=outcome.normalized_checksum,
                )
            )
            for candidate in _candidate_dicts(outcome):
                shard_kind = candidate["shard_kind"]
                raw_text = candidate["raw_text"]
                normalized = normalize_text(raw_text)
                source_path = textmap_paths[locale][shard_kind]
                text_candidates.append(
                    Phase4B6TextCandidate(
                        family=FAMILY,
                        owning_source_relative_path=TABLE_FILES[table],
                        owning_source_record_pointer=f"/{row_index}",
                        relationship_source_pointer=source_pointer,
                        locale=locale,
                        textmap_hash=key,
                        status=outcome.status,
                        shard_kind=shard_kind,
                        source_relative_path=source_path,
                        source_record_pointer=f"/{key}",
                        raw_text=raw_text,
                        normalized_text=normalized,
                        raw_checksum=sha256_text(raw_text),
                        normalized_checksum=sha256_text(normalized),
                    )
                )

    source_records: list[Phase4B6SourceRecord] = []
    for container in containers:
        source_records.append(
            Phase4B6SourceRecord(
                relative_path=container.relative_path,
                pointer=container.record_pointer,
                record_kind=container.record_kind,
                native_id=container.native_id,
                raw_sha256=_raw_hash(container.raw_value),
                raw_json=container.raw_json,
                raw_value=container.raw_value,
            )
        )
    distinct_text_sources: dict[tuple[str, str], tuple[str, str]] = {}
    for candidate in text_candidates:
        distinct_text_sources[(candidate.source_relative_path, candidate.source_record_pointer)] = (
            candidate.textmap_hash,
            candidate.raw_text,
        )
    for (relative_path, pointer), (textmap_hash, raw_text) in sorted(distinct_text_sources.items()):
        source_records.append(
            Phase4B6SourceRecord(
                relative_path=relative_path,
                pointer=pointer,
                record_kind="static_textmap_entry",
                native_id=textmap_hash,
                raw_sha256=sha256_text(raw_text),
                raw_json=canonical_json(raw_text),
                raw_value=raw_text,
            )
        )

    raw_files = [
        {
            "relative_path": item.relative_path,
            "format": getattr(item.spec, "format", "json"),
            "size_bytes": getattr(item.spec, "size_bytes", len(item.raw)),
            "sha256": getattr(item.spec, "sha256", sha256_bytes(item.raw)),
        }
        for item in loaded
    ]
    raw_source_records = [
        {
            "source_path": item.relative_path,
            "pointer": item.pointer,
            "record_kind": item.record_kind,
            "native_id": item.native_id,
            "raw_sha256": item.raw_sha256,
            "raw_json": item.raw_json,
        }
        for item in source_records
    ]
    raw_containers = [
        {
            "family": item.family,
            "source_path": item.relative_path,
            "record_pointer": item.record_pointer,
            "source_family": item.source_family,
            "locale": item.locale,
            "native_id": item.native_id,
        }
        for item in containers
    ]
    raw_relationships = [
        {
            "family": item.family,
            "source_table": item.source_family,
            "source_record_pointer": item.source_record_pointer,
            "source_pointer": item.source_pointer,
            "relation_kind": item.relation_kind,
            "field_role": item.field_role,
            "raw_value": item.raw_value,
            "target_family": item.target_family,
            "target_candidate_key": item.target_candidate_key,
            "status": item.status,
            "target_record": None
            if item.target_container_coordinate is None
            else {
                "table": item.target_family,
                "pointer": item.target_container_coordinate[1],
            },
            "ambiguity": item.ambiguity,
        }
        for item in relationships
    ]
    raw_resolutions = [
        {
            "family": item.family,
            "source_table": item.owning_source_relative_path.split("/")[-1].replace(".json", ""),
            "source_record_pointer": item.owning_source_record_pointer,
            "source_pointer": item.relationship_source_pointer,
            "field": item.relationship_source_pointer.split("/")[-1],
            "field_role": text_field_contract[
                (
                    item.owning_source_relative_path.split("/")[-1].replace(".json", ""),
                    item.relationship_source_pointer.split("/")[-1],
                )
            ][0],
            "relation_kind": text_field_contract[
                (
                    item.owning_source_relative_path.split("/")[-1].replace(".json", ""),
                    item.relationship_source_pointer.split("/")[-1],
                )
            ][1],
            "textmap_hash": item.textmap_hash,
            "locale": item.locale,
            "status": item.status,
            "raw_text": item.raw_text,
            "normalized_text": item.normalized_text,
            "raw_checksum": item.raw_checksum,
            "normalized_checksum": item.normalized_checksum,
        }
        for item in text_resolutions
    ]
    raw_candidates = [
        {
            "family": item.family,
            "source_table": item.owning_source_relative_path.split("/")[-1].replace(".json", ""),
            "source_record_pointer": item.owning_source_record_pointer,
            "source_pointer": item.relationship_source_pointer,
            "field": item.relationship_source_pointer.split("/")[-1],
            "field_role": text_field_contract[
                (
                    item.owning_source_relative_path.split("/")[-1].replace(".json", ""),
                    item.relationship_source_pointer.split("/")[-1],
                )
            ][0],
            "relation_kind": text_field_contract[
                (
                    item.owning_source_relative_path.split("/")[-1].replace(".json", ""),
                    item.relationship_source_pointer.split("/")[-1],
                )
            ][1],
            "textmap_hash": item.textmap_hash,
            "locale": item.locale,
            "status": item.status,
            "shard_kind": item.shard_kind,
            "source_path": item.source_relative_path,
            "source_pointer": item.source_record_pointer,
            "raw_text": item.raw_text,
            "normalized_text": item.normalized_text,
            "raw_checksum": item.raw_checksum,
            "normalized_checksum": item.normalized_checksum,
        }
        for item in text_candidates
    ]
    raw_text_assets = [
        {
            "source_path": relative_path,
            "source_pointer": pointer,
            "locale": "CHS" if "CHS" in relative_path else "EN" if "EN" in relative_path else "JP",
            "shard_kind": "medium" if "Medium" in relative_path else "main",
            "textmap_hash": textmap_hash,
            "raw_checksum": sha256_text(raw_text),
        }
        for (relative_path, pointer), (textmap_hash, raw_text) in sorted(distinct_text_sources.items())
    ]
    digests = {
        "files": _set_digest(raw_files),
        "source_records": _set_digest(raw_source_records),
        "containers": _set_digest(raw_containers),
        "segments": _set_digest([]),
        "relationships": _set_digest(raw_relationships),
        "text_resolutions": _set_digest(raw_resolutions),
        "text_candidates": _set_digest(raw_candidates),
        "text_assets": _set_digest(raw_text_assets),
        "text_owners": _set_digest(text_owners),
    }
    expected = _manifest_attr(manifest, "expected", {}) or {}
    expected_digests = expected.get("occurrence_set_digests") if isinstance(expected, dict) else None
    if expected_digests and expected_digests != digests:
        raise Phase4B6EvidenceError("derived occurrence set digest mismatch")
    counts = {
        "selected_files": len(loaded),
        "source_records": len(source_records),
        "containers": len(containers),
        "segments": 0,
        "relationships": len(relationships),
        "text_resolutions": len(text_resolutions),
        "text_candidates": len(text_candidates),
        "text_assets": len(distinct_text_sources),
        "text_owner_occurrences": len(text_owners),
    }
    expected_counts = expected.get("counts") if isinstance(expected, dict) else None
    if expected_counts:
        normalized_expected_counts = {
            "selected_files": expected_counts.get("selected_files", expected_counts.get("files")),
            "source_records": expected_counts.get("source_records"),
            "containers": expected_counts.get("containers"),
            "segments": expected_counts.get("segments", 0),
            "relationships": expected_counts.get("relationships"),
            "text_resolutions": expected_counts.get("text_resolutions"),
            "text_candidates": expected_counts.get("text_candidates"),
            "text_assets": expected_counts.get("text_assets"),
            "text_owner_occurrences": expected_counts.get("text_owner_occurrences"),
        }
        if normalized_expected_counts != counts:
            raise Phase4B6EvidenceError(f"derived occurrence counts mismatch: {counts}")
    status_counts = {
        "relationships": {
            status: Counter(item.status for item in relationships)[status]
            for status in ("resolved", "ambiguous", "unresolved", "dangling")
        },
        "text_resolutions": {
            status: Counter(item.status for item in text_resolutions)[status]
            for status in ("unique_main", "unique_medium", "both_equal", "both_conflict", "missing")
        },
    }
    expected_relationship_statuses = expected.get("relationship_statuses") if isinstance(expected, dict) else None
    if expected_relationship_statuses:
        if status_counts["relationships"] != expected_relationship_statuses:
            raise Phase4B6EvidenceError("relationship status reconciliation mismatch")
    expected_text_statuses = expected.get("text_resolution_statuses") if isinstance(expected, dict) else None
    if expected_text_statuses:
        if status_counts["text_resolutions"] != expected_text_statuses:
            raise Phase4B6EvidenceError("TextMap status reconciliation mismatch")

    expected_relationship_kinds = expected.get("relationship_kind_counts") if isinstance(expected, dict) else None
    if expected_relationship_kinds:
        actual_relationship_kinds = dict(sorted(Counter(item.relation_kind for item in relationships).items()))
        if actual_relationship_kinds != expected_relationship_kinds:
            raise Phase4B6EvidenceError("relationship kind reconciliation mismatch")
    expected_field_counts = expected.get("field_occurrence_counts") if isinstance(expected, dict) else None
    if expected_field_counts:
        # Include fields with zero positive pointers when a synthetic fixture
        # intentionally exercises unresolved field evidence.
        actual_field_counts = {
            f"{table}.{field}": sum(
                1 for owner in text_owners if owner["source_table"] == table and owner["field"] == field
            )
            for table, fields in fields_by_table.items()
            for field in fields
        }
        if actual_field_counts != expected_field_counts:
            raise Phase4B6EvidenceError("TextMap owner field reconciliation mismatch")
    expected_families = expected.get("families") if isinstance(expected, dict) else None
    if expected_families and expected_families != {
        FAMILY: {
            "containers": len(containers),
            "segments": 0,
            "relationships": len(relationships),
            "text_resolutions": len(text_resolutions),
            "text_candidates": len(text_candidates),
            "text_assets": len(distinct_text_sources),
            "text_owner_occurrences": len(text_owners),
        }
    }:
        raise Phase4B6EvidenceError("family occurrence reconciliation mismatch")
    expected_collisions = expected.get("cross_table_numeric_id_collisions") if isinstance(expected, dict) else None
    if expected_collisions is not None:
        actual_collisions = len(
            set(indexes["MaterialExcelConfigData"]) & set(indexes["MaterialCodexExcelConfigData"])
        )
        if actual_collisions != expected_collisions:
            raise Phase4B6EvidenceError("cross-table numeric identity reconciliation mismatch")

    # Text assets are deduplicated by locale/shard/hash, while candidates are
    # one row per owner/shard.  Count assets from their source path rather
    # than candidates so repeated owner pointers do not inflate the value.
    locale_breakdown = {
        locale: {
            "text_resolutions": sum(item.locale == locale for item in text_resolutions),
            "text_candidates": sum(item.locale == locale for item in text_candidates),
            "text_assets": sum(
                1
                for (relative_path, _), _value in distinct_text_sources.items()
                if f"TextMap{locale}.json" in relative_path
                or f"TextMap_Medium{locale}.json" in relative_path
            ),
        }
        for locale in LOCALES
    }
    expected_locale_breakdown = expected.get("locale_breakdown") if isinstance(expected, dict) else None
    if expected_locale_breakdown and locale_breakdown != expected_locale_breakdown:
        raise Phase4B6EvidenceError("locale occurrence reconciliation mismatch")

    family_counts = {
        FAMILY: {
            "containers": len(containers),
            "segments": 0,
            "relationships": len(relationships),
            "text_resolutions": len(text_resolutions),
            "text_candidates": len(text_candidates),
            "text_assets": len(distinct_text_sources),
            "text_owner_occurrences": len(text_owners),
        }
    }
    family_selected_paths = {FAMILY: sorted(item.relative_path for item in loaded)}
    source = _manifest_attr(manifest, "source", {}) or {}
    snapshot_id = digest_id(
        "snap",
        canonical_json(
            {
                "commit": source.get("commit"),
                "tree": source.get("tree"),
                "build": source.get("build"),
                "release": source.get("release_label"),
            }
        ),
    )
    versions = _manifest_attr(manifest, "versions", {}) or {}
    parser_build_id = digest_id(
        "pb-static-phase4b6",
        canonical_json({"versions": versions, "manifest_id": manifest_id}),
    )
    config_checksum = sha256_text(
        canonical_json(
            {
                "manifest_id": manifest_id,
                "manifest_raw_sha256": manifest_sha,
                "occurrence_set_digests": digests,
            }
        )
    )
    ingest_run_id = digest_id(
        "ir-static-phase4b6",
        canonical_json(
            {
                "snapshot_id": snapshot_id,
                "parser_build_id": parser_build_id,
                "config_checksum": config_checksum,
            }
        ),
    )
    return Phase4B6PreflightResult(
        manifest=manifest,
        source_root=root,
        snapshot_id=snapshot_id,
        parser_build_id=parser_build_id,
        ingest_run_id=ingest_run_id,
        config_checksum=config_checksum,
        files=loaded,
        source_records=tuple(source_records),
        containers=tuple(containers),
        segments=(),
        relationships=tuple(relationships),
        text_resolutions=tuple(text_resolutions),
        text_candidates=tuple(text_candidates),
        status_counts=status_counts,
        family_counts=family_counts,
        occurrence_set_digests=digests,
        family_selected_paths=family_selected_paths,
        locale_breakdown=locale_breakdown,
        text_owner_occurrences=len(text_owners),
        text_asset_occurrences=len(distinct_text_sources),
    )


def _require_manifest_path(manifest_input: Any) -> str | Path:
    if not isinstance(manifest_input, (str, Path)):
        raise Phase4B6EvidenceError(
            "public Phase 4B6 API requires a manifest path; object manifests are internal-test-only"
        )
    return manifest_input


def preflight_static_phase4b6_material_evidence_bundle(
    source_root: str | Path,
    manifest_path: str | Path,
) -> Phase4B6PreflightResult:
    """Preflight a sealed Phase 4B6 manifest loaded from a filesystem path."""
    _require_manifest_path(manifest_path)
    return _preflight_static_phase4b6_material_evidence_bundle_internal(source_root, manifest_path)


def _ensure_row(
    conn: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    values: tuple[Any, ...],
    *,
    insert_missing: bool = True,
) -> None:
    row_id = values[0]
    row = conn.execute(f"SELECT {','.join(columns)} FROM {table} WHERE id=?", (row_id,)).fetchone()
    if row is not None:
        if tuple(row[column] for column in columns) != values:
            raise sqlite3.IntegrityError(f"{table} immutable row mismatch: {row_id}")
        return
    if not insert_missing:
        raise sqlite3.IntegrityError(f"{table} expected row is missing: {row_id}")
    placeholders = ",".join("?" for _ in columns)
    conn.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES ({placeholders})", values)


def _db_record_kind(kind: str) -> str:
    return {"static_excel_row": "excel_row", "static_textmap_entry": "textmap_entry"}.get(kind, kind)


def _storage_field_role(item: Phase4B6Relationship) -> str:
    """Map a family-local role to the conservative schema-9 role enum.

    The sealed Material manifest records richer field roles (for example
    ``state_variant_name_1``), while schema 8 intentionally allows only the
    Phase 4A coarse vocabulary.  Preserve the richer role in immutable
    metadata and store the schema-valid coarse role in ``field_role``.
    """
    if item.target_family == "TextMap":
        return "verified_textmap_reference" if item.status == "resolved" else "unresolved_field_role"
    return item.field_role


def _run_counts(conn: sqlite3.Connection, run_id: str) -> dict[str, int]:
    return {
        "containers": int(conn.execute("SELECT COUNT(*) FROM static_evidence_container WHERE ingest_run_id=?", (run_id,)).fetchone()[0]),
        "segments": int(conn.execute("SELECT COUNT(*) FROM static_segment_occurrence WHERE ingest_run_id=?", (run_id,)).fetchone()[0]),
        "relationships": int(conn.execute("SELECT COUNT(*) FROM static_relationship_occurrence WHERE ingest_run_id=?", (run_id,)).fetchone()[0]),
        "text_resolutions": int(conn.execute("SELECT COUNT(*) FROM static_text_resolution_occurrence WHERE ingest_run_id=?", (run_id,)).fetchone()[0]),
        "text_candidates": int(conn.execute("SELECT COUNT(*) FROM static_text_candidate_occurrence WHERE ingest_run_id=?", (run_id,)).fetchone()[0]),
    }


def _write_occurrences(
    conn: sqlite3.Connection,
    preflight: Phase4B6PreflightResult,
    *,
    insert_missing: bool = True,
) -> None:
    def ensure(table: str, columns: tuple[str, ...], values: tuple[Any, ...]) -> None:
        _ensure_row(conn, table, columns, values, insert_missing=insert_missing)

    source = preflight.manifest.source
    source_values = (source["source_id"], "primary", source["upstream"], None)
    row = conn.execute("SELECT id,kind,url,license_note FROM source_catalog WHERE id=?", (source["source_id"],)).fetchone()
    if row is None:
        if not insert_missing:
            raise sqlite3.IntegrityError("source catalog row is missing")
        conn.execute("INSERT INTO source_catalog(id,kind,url,license_note) VALUES (?,?,?,?)", source_values)
    elif tuple(row) != source_values:
        raise sqlite3.IntegrityError("source catalog mismatch")

    snapshot_binding = expected_phase4b6_source_snapshot_binding(preflight.manifest)
    snapshot_values = snapshot_binding["core"]
    snapshot_parser_version = preflight.manifest.versions.get("parser", "static-phase4b6-parser-v1")
    snapshot_mapping_version = preflight.manifest.versions.get("projection", "static-phase4b6-material-projection-v1")
    snapshot_status = snapshot_binding["status"]
    snapshot_metadata_json = snapshot_binding["metadata_json"]
    snapshot_contract = (
        *snapshot_values,
        snapshot_parser_version,
        snapshot_mapping_version,
        snapshot_status,
        snapshot_metadata_json,
    )
    row = conn.execute(
        'SELECT source_id,release_label,build,"commit",tree,observed_at,status,metadata_json '
        "FROM source_snapshot WHERE id=?",
        (preflight.snapshot_id,),
    ).fetchone()
    if row is None:
        if not insert_missing:
            raise sqlite3.IntegrityError("source snapshot row is missing")
        conn.execute(
            'INSERT INTO source_snapshot(id,source_id,release_label,build,"commit",tree,observed_at,parser_version,mapping_version,status,metadata_json) '
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                preflight.snapshot_id,
                *snapshot_contract,
            ),
        )
    elif (
        tuple(row[column] for column in ("source_id", "release_label", "build", "commit", "tree", "observed_at"))
        != snapshot_values
        or row["status"] != snapshot_status
        or row["metadata_json"] != snapshot_metadata_json
    ):
        raise sqlite3.IntegrityError("source snapshot mismatch")

    parser_values = (
        preflight.parser_build_id,
        preflight.manifest.versions.get("parser", "static-phase4b6-parser-v1"),
        preflight.manifest.versions.get("projection", "static-phase4b6-material-projection-v1"),
        "static-phase4b6-sealed-material-corpus",
        preflight.config_checksum,
        source["observed_at"],
    )
    ensure("parser_build", ("id", "parser_version", "mapping_version", "fingerprint", "config_checksum", "created_at"), parser_values)
    run_values = (
        preflight.ingest_run_id,
        preflight.snapshot_id,
        preflight.parser_build_id,
        canonical_json({"family": "static_phase4b6_material_corpus", "manifest_id": preflight.manifest.manifest_id, "selected_paths": [item.relative_path for item in preflight.files]}),
        canonical_json({"manifest_raw_sha256": preflight.manifest.raw_sha256, "versions": preflight.manifest.versions, "full_static_scan": False, "logical_identity_created": False, "cross_language_alignment_created": False, "textmap_evidence_container_created": False}),
        "running" if insert_missing else "complete",
        source["observed_at"],
    )
    ensure("ingest_run", ("id", "snapshot_id", "parser_build_id", "scope_json", "config_json", "status", "created_at"), run_values)

    file_ids: dict[str, str] = {}
    for item in preflight.files:
        file_id = digest_id("sf", preflight.snapshot_id, item.relative_path)
        file_ids[item.relative_path] = file_id
        ensure(
            "source_file",
            ("id", "snapshot_id", "relative_path", "upstream_blob_oid", "sha256", "size_bytes", "format", "root_path"),
            (file_id, preflight.snapshot_id, item.relative_path, None, getattr(item.spec, "sha256", sha256_bytes(item.raw)), getattr(item.spec, "size_bytes", len(item.raw)), getattr(item.spec, "format", "json"), str(item.root)),
        )

    record_ids: dict[tuple[str, str], str] = {}
    for item in preflight.source_records:
        file_id = file_ids[item.relative_path]
        record_id = digest_id("sr", file_id, item.pointer)
        record_ids[item.coordinate] = record_id
        ensure(
            "source_record",
            ("id", "source_file_id", "pointer", "record_kind", "native_id", "raw_sha256", "raw_json"),
            (record_id, file_id, item.pointer, _db_record_kind(item.record_kind), item.native_id, item.raw_sha256, item.raw_json),
        )

    container_ids: dict[tuple[str, str], str] = {}
    for item in preflight.containers:
        container_id = digest_id("static-container", preflight.snapshot_id, preflight.manifest.raw_sha256, preflight.ingest_run_id, item.relative_path, item.record_pointer)
        container_ids[item.coordinate] = container_id
        ensure(
            "static_evidence_container",
            ("id", "ingest_run_id", "snapshot_id", "source_file_id", "source_record_id", "container_kind", "source_family", "locale", "candidate_namespace", "candidate_key_json", "metadata_json"),
            (
                container_id, preflight.ingest_run_id, preflight.snapshot_id, file_ids[item.relative_path],
                record_ids[item.coordinate], item.container_kind, item.source_family, item.locale,
                item.candidate_namespace, canonical_json(item.candidate_key), canonical_json(item.metadata),
            ),
        )

    relationship_ids: dict[tuple[str, str, str], str] = {}
    for item in preflight.relationships:
        relation_id = digest_id("static-relationship", preflight.snapshot_id, preflight.manifest.raw_sha256, preflight.ingest_run_id, item.source_relative_path, item.source_record_pointer, item.source_pointer)
        relationship_ids[item.coordinate] = relation_id
        source_coordinate = item.source_relative_path, item.source_record_pointer
        target_id = None if item.target_container_coordinate is None else container_ids[item.target_container_coordinate]
        ensure(
            "static_relationship_occurrence",
            ("id", "ingest_run_id", "snapshot_id", "source_container_id", "source_record_id", "source_family", "source_pointer", "relation_kind", "field_role", "target_family", "target_candidate_key_json", "candidate_resolution_status", "target_evidence_container_id", "ambiguity_json", "metadata_json"),
            (
                relation_id, preflight.ingest_run_id, preflight.snapshot_id, container_ids[source_coordinate],
                record_ids[source_coordinate], item.source_family, item.source_pointer, item.relation_kind,
                _storage_field_role(item), item.target_family,
                None if item.target_candidate_key is None else canonical_json(item.target_candidate_key), item.status,
                target_id, canonical_json(item.ambiguity),
                canonical_json({
                    "family": item.family,
                    "raw_value": item.raw_value,
                    "manifest_id": preflight.manifest.manifest_id,
                    "candidate_resolution_detail": "family_specific_raw_value_and_sealed_candidate_set",
                    "manifest_field_role": item.field_role,
                    "manifest_relation_kind": item.relation_kind,
                    "storage_field_role": _storage_field_role(item),
                }),
            ),
        )

    resolution_ids: dict[tuple[str, str, str, str], str] = {}
    for item in preflight.text_resolutions:
        owner = (item.owning_source_relative_path, item.owning_source_record_pointer, item.relationship_source_pointer)
        relation_id = relationship_ids[owner]
        resolution_id = digest_id("static-text-resolution", preflight.snapshot_id, preflight.manifest.raw_sha256, preflight.ingest_run_id, relation_id, item.locale)
        resolution_ids[item.occurrence_coordinate] = resolution_id
        source_coordinate = owner[0], owner[1]
        ensure(
            "static_text_resolution_occurrence",
            ("id", "ingest_run_id", "snapshot_id", "static_relationship_occurrence_id", "source_container_id", "source_record_id", "source_pointer", "locale", "textmap_hash", "resolution_status", "raw_text", "normalized_text", "raw_checksum", "normalized_checksum", "metadata_json"),
            (
                resolution_id, preflight.ingest_run_id, preflight.snapshot_id, relation_id, container_ids[source_coordinate], record_ids[source_coordinate], item.relationship_source_pointer, item.locale, item.textmap_hash, item.status, item.raw_text, item.normalized_text, item.raw_checksum, item.normalized_checksum,
                canonical_json({"family": item.family, "locator_only": True, "owning_static_source": {"relative_path": owner[0], "record_pointer": owner[1], "source_pointer": owner[2]}, "both_conflict_auto_selected": False}),
            ),
        )
    for item in preflight.text_candidates:
        owner = (item.owning_source_relative_path, item.owning_source_record_pointer, item.relationship_source_pointer)
        resolution_id = resolution_ids[(owner[0], owner[1], owner[2], item.locale)]
        candidate_id = digest_id("static-text-candidate", preflight.snapshot_id, preflight.manifest.raw_sha256, preflight.ingest_run_id, resolution_id, item.source_relative_path, item.source_record_pointer, item.shard_kind)
        ensure(
            "static_text_candidate_occurrence",
            ("id", "resolution_occurrence_id", "ingest_run_id", "snapshot_id", "source_file_id", "source_record_id", "source_pointer", "shard_kind", "locale", "resolution_status", "raw_text", "normalized_text", "raw_checksum", "normalized_checksum", "metadata_json"),
            (
                candidate_id, resolution_id, preflight.ingest_run_id, preflight.snapshot_id, file_ids[item.source_relative_path], record_ids[(item.source_relative_path, item.source_record_pointer)], item.source_record_pointer, item.shard_kind, item.locale, item.status, item.raw_text, item.normalized_text, item.raw_checksum, item.normalized_checksum,
                canonical_json({"family": item.family, "owning_static_source": {"relative_path": owner[0], "record_pointer": owner[1], "source_pointer": owner[2]}}),
            ),
        )


def _schema9_readonly_check(target: Path) -> None:
    if not target.is_file():
        raise Phase4B6EvidenceError(f"existing schema-9 database is required: {target}")
    try:
        uri = target.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            conn.execute("PRAGMA query_only=ON")
            versions = [row[0] for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")]
            if versions != list(range(1, 10)):
                raise Phase4B6EvidenceError(f"database schema contract is not exactly 1..9: {versions}")
        finally:
            conn.close()
    except Phase4B6EvidenceError:
        raise
    except sqlite3.Error as exc:
        raise Phase4B6EvidenceError(f"cannot validate target schema: {exc}") from exc


def _ingest_static_phase4b6_material_evidence_bundle_internal(
    db_path: str | Path,
    source_root: str | Path,
    manifest_input: Any,
    *,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    """Private synthetic-fixture seam; public callers must provide a path."""
    target_db = Path(db_path).resolve()
    _schema9_readonly_check(target_db)
    if report_path is not None:
        report = Path(report_path).resolve()
        if report == target_db:
            raise Phase4B6EvidenceError("database and build report must not resolve to the same file")
        if report.exists():
            raise Phase4B6EvidenceError(f"build report already exists: {report}")
    preflight = _preflight_static_phase4b6_material_evidence_bundle_internal(source_root, manifest_input)
    conn = sqlite3.connect(str(target_db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        existing = conn.execute("SELECT status FROM ingest_run WHERE id=?", (preflight.ingest_run_id,)).fetchone()
        if existing is not None:
            if existing["status"] != "complete":
                raise sqlite3.IntegrityError("existing Phase 4B6 run is incomplete")
            _write_occurrences(conn, preflight, insert_missing=False)
            expected = {
                "containers": len(preflight.containers),
                "segments": 0,
                "relationships": len(preflight.relationships),
                "text_resolutions": len(preflight.text_resolutions),
                "text_candidates": len(preflight.text_candidates),
            }
            if _run_counts(conn, preflight.ingest_run_id) != expected:
                raise sqlite3.IntegrityError("existing Phase 4B6 occurrence cardinality mismatch")
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise sqlite3.IntegrityError("existing Phase 4B6 database has foreign key violations")
        else:
            try:
                conn.execute("BEGIN")
                _write_occurrences(conn, preflight)
                expected = {
                    "containers": len(preflight.containers),
                    "segments": 0,
                    "relationships": len(preflight.relationships),
                    "text_resolutions": len(preflight.text_resolutions),
                    "text_candidates": len(preflight.text_candidates),
                }
                if _run_counts(conn, preflight.ingest_run_id) != expected:
                    raise sqlite3.IntegrityError("Phase 4B6 write cardinality mismatch")
                foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
                if foreign_keys:
                    raise sqlite3.IntegrityError(f"Phase 4B6 foreign key check failed: {foreign_keys[:3]}")
                if conn.execute("UPDATE ingest_run SET status='complete' WHERE id=? AND status='running'", (preflight.ingest_run_id,)).rowcount != 1:
                    raise sqlite3.IntegrityError("Phase 4B6 run completion transition failed")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    except Phase4B6EvidenceError:
        raise
    except sqlite3.Error as exc:
        raise Phase4B6EvidenceError(str(exc)) from exc
    finally:
        conn.close()
    result = _build_report(target_db, preflight)
    if report_path is not None:
        report = Path(report_path).resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(canonical_json(result) + "\n", encoding="utf-8")
    return result


def ingest_static_phase4b6_material_evidence_bundle(
    db_path: str | Path,
    source_root: str | Path,
    manifest_path: str | Path,
    *,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    """Append a sealed Phase 4B6 bundle using a filesystem manifest path."""
    _require_manifest_path(manifest_path)
    return _ingest_static_phase4b6_material_evidence_bundle_internal(
        db_path,
        source_root,
        manifest_path,
        report_path=report_path,
    )


def _build_report(db_path: Path, preflight: Phase4B6PreflightResult) -> dict[str, Any]:
    stat = db_path.stat()
    return {
        "status": "complete",
        "db_path": str(db_path.resolve()),
        "snapshot_id": preflight.snapshot_id,
        "parser_build_id": preflight.parser_build_id,
        "ingest_run_id": preflight.ingest_run_id,
        "config_checksum": preflight.config_checksum,
        "database_fingerprint": {"sha256": sha256_bytes(db_path.read_bytes()), "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns},
        "manifest": {"manifest_id": preflight.manifest.manifest_id, "raw_sha256": preflight.manifest.raw_sha256},
        "counts": {
            "files": len(preflight.files),
            "source_records": len(preflight.source_records),
            "containers": len(preflight.containers),
            "segments": 0,
            "relationships": len(preflight.relationships),
            "text_resolutions": len(preflight.text_resolutions),
            "text_candidates": len(preflight.text_candidates),
            "text_assets": preflight.text_asset_occurrences,
            "text_owner_occurrences": preflight.text_owner_occurrences,
        },
        "family_counts": preflight.family_counts,
        "family_selected_paths": preflight.family_selected_paths,
        "status_counts": preflight.status_counts,
        "locale_breakdown": preflight.locale_breakdown,
        "occurrence_set_digests": preflight.occurrence_set_digests,
        "scope": {
            "selected_paths": [item.relative_path for item in preflight.files],
            "full_static_scan": False,
            "fts_rebuilt": False,
            "logical_identity_created": False,
            "cross_language_alignment_created": False,
            "textmap_evidence_container_created": False,
        },
    }
