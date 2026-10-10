from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .static_phase4b3_manifest import (
    EXPECTED_COMMIT,
    EXPECTED_TREE,
    StaticPhase4B3FileSpec,
    StaticPhase4B3Manifest,
    load_static_phase4b3_manifest,
)
from .util import canonical_json, digest_id, normalize_text, sha256_bytes, sha256_text


LOCALES = ("CHS", "EN", "JP")
TABLE_FILES = {
    table: f"ExcelBinOutput/{table}.json"
    for table in (
        "BookSuitExcelConfigData",
        "BooksCodexExcelConfigData",
        "WeaponExcelConfigData",
        "WeaponCodexExcelConfigData",
        "MaterialExcelConfigData",
        "DocumentExcelConfigData",
        "LocalizationExcelConfigData",
    )
}
TEXT_FIELDS = {
    "BookSuitExcelConfigData": ("suitNameTextMapHash",),
    "MaterialExcelConfigData": ("nameTextMapHash", "descTextMapHash"),
    "WeaponExcelConfigData": ("nameTextMapHash", "descTextMapHash"),
    "DocumentExcelConfigData": ("titleTextMapHash",),
}
TEXT_RELATION_KINDS = {
    ("BookSuitExcelConfigData", "suitNameTextMapHash"): "book_suit_name_textmap_candidate",
    ("MaterialExcelConfigData", "nameTextMapHash"): "book_material_name_textmap_candidate",
    ("MaterialExcelConfigData", "descTextMapHash"): "book_material_description_textmap_candidate",
    ("WeaponExcelConfigData", "nameTextMapHash"): "weapon_name_textmap_candidate",
    ("WeaponExcelConfigData", "descTextMapHash"): "weapon_description_textmap_candidate",
    ("DocumentExcelConfigData", "titleTextMapHash"): "document_title_textmap_candidate",
}
LOCALIZATION_FIELDS = {
    "DEFNEHAFMMA": ("CHS", "unknown_obfuscated"),
    "JDNBKKPEFAI": ("CHS", "unknown_obfuscated"),
    "enPath": ("EN", "unresolved_field_role"),
    "jpPath": ("JP", "unresolved_field_role"),
}
B1_STATIC_RECORD_COORDINATES = {
    ("ExcelBinOutput/DocumentExcelConfigData.json", "/6"),
    ("ExcelBinOutput/LocalizationExcelConfigData.json", "/8"),
    ("Readable/CHS/Book1.txt", "/text"),
    ("Readable/EN/Book1_EN.txt", "/text"),
    ("Readable/JP/Book1_JP.txt", "/text"),
}


class Phase4B3EvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Phase4B3File:
    spec: StaticPhase4B3FileSpec
    path: Path
    root: Path
    raw: bytes
    parsed: Any

    @property
    def relative_path(self) -> str:
        return self.spec.relative_path


@dataclass(frozen=True)
class Phase4B3SourceRecord:
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
class Phase4B3Container:
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
class Phase4B3Segment:
    family: str
    relative_path: str
    record_pointer: str
    locale: str
    source_pointer: str
    file_local_ordinal: int
    byte_start: int
    byte_end: int
    character_start: int
    character_end: int
    raw_text: str
    normalized_text: str
    raw_checksum: str
    normalized_checksum: str
    segmentation_version: str

    @property
    def container_coordinate(self) -> tuple[str, str]:
        return self.relative_path, self.record_pointer


@dataclass(frozen=True)
class Phase4B3Relationship:
    family: str
    source_relative_path: str
    source_record_pointer: str
    source_family: str
    source_pointer: str
    relation_kind: str
    field_role: str
    raw_value: Any
    target_family: str | None
    target_candidate_key: dict[str, Any] | None
    status: str
    target_container_coordinate: tuple[str, str] | None
    ambiguity: dict[str, Any]

    @property
    def coordinate(self) -> tuple[str, str, str]:
        return self.source_relative_path, self.source_record_pointer, self.source_pointer


@dataclass(frozen=True)
class Phase4B3TextCandidate:
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
class Phase4B3TextResolution:
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

    def with_owner_pointer(self, pointer: str) -> "Phase4B3TextResolution":
        return replace(self, relationship_source_pointer=pointer)


@dataclass(frozen=True)
class Phase4B3TextMapValue:
    shard_kind: str
    raw_text: str
    normalized_text: str
    raw_checksum: str
    normalized_checksum: str


@dataclass(frozen=True)
class Phase4B3TextMapOutcome:
    status: str
    candidates: tuple[Phase4B3TextMapValue, ...]
    raw_text: str | None
    normalized_text: str | None
    raw_checksum: str | None
    normalized_checksum: str | None


@dataclass(frozen=True)
class Phase4B3Preflight:
    manifest: StaticPhase4B3Manifest
    source_manifest: dict[str, Any]
    snapshot_id: str
    parser_build_id: str
    ingest_run_id: str
    config_checksum: str
    files: tuple[Phase4B3File, ...]
    source_records: tuple[Phase4B3SourceRecord, ...]
    containers: tuple[Phase4B3Container, ...]
    segments: tuple[Phase4B3Segment, ...]
    relationships: tuple[Phase4B3Relationship, ...]
    text_resolutions: tuple[Phase4B3TextResolution, ...]
    text_candidates: tuple[Phase4B3TextCandidate, ...]
    status_counts: dict[str, dict[str, int]]
    occurrence_set_digests: dict[str, str]


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def _sha_value(value: Any) -> str:
    return sha256_text(value if isinstance(value, str) else canonical_json(value))


def _set_digest(items: list[dict[str, Any]]) -> str:
    ordered = sorted(canonical_json(item) for item in items)
    return sha256_text(canonical_json(ordered))


def classify_phase4b3_readable(raw: bytes) -> str:
    try:
        normalized = normalize_text(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise Phase4B3EvidenceError(f"Readable is not UTF-8: {exc}") from exc
    if not raw:
        return "empty"
    if not normalized.strip():
        return "blank_whitespace_only"
    if normalized.strip().lower() == "test":
        return "placeholder_exact_test"
    return "narrative"


def resolve_phase4b3_textmap_occurrence(
    main_text: str | None,
    medium_text: str | None,
) -> Phase4B3TextMapOutcome:
    if main_text is not None and not isinstance(main_text, str):
        raise Phase4B3EvidenceError("main TextMap candidate is not text")
    if medium_text is not None and not isinstance(medium_text, str):
        raise Phase4B3EvidenceError("Medium TextMap candidate is not text")
    pairs = (("main", main_text), ("medium", medium_text))
    candidates = tuple(
        Phase4B3TextMapValue(
            shard_kind=kind,
            raw_text=text,
            normalized_text=normalize_text(text),
            raw_checksum=sha256_text(text),
            normalized_checksum=sha256_text(normalize_text(text)),
        )
        for kind, text in pairs
        if text is not None
    )
    if main_text is None and medium_text is None:
        status, selected = "missing", None
    elif main_text is not None and medium_text is None:
        status, selected = "unique_main", main_text
    elif main_text is None:
        status, selected = "unique_medium", medium_text
    elif main_text == medium_text:
        status, selected = "both_equal", main_text
    else:
        status, selected = "both_conflict", None
    normalized = None if selected is None else normalize_text(selected)
    return Phase4B3TextMapOutcome(
        status=status,
        candidates=candidates,
        raw_text=selected,
        normalized_text=normalized,
        raw_checksum=None if selected is None else sha256_text(selected),
        normalized_checksum=None if normalized is None else sha256_text(normalized),
    )


def _line_segments(raw: bytes) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Phase4B3EvidenceError(f"Readable is not UTF-8: {exc}") from exc
    result: list[dict[str, Any]] = []
    character_start = 0
    ordinal = 0
    while character_start <= len(text):
        character_end = character_start
        while character_end < len(text) and text[character_end] not in "\r\n":
            character_end += 1
        line = text[character_start:character_end]
        if line.strip():
            normalized = normalize_text(line)
            result.append(
                {
                    "file_local_ordinal": ordinal,
                    "byte_start": len(text[:character_start].encode("utf-8")),
                    "byte_end": len(text[:character_end].encode("utf-8")),
                    "character_start": character_start,
                    "character_end": character_end,
                    "raw_text": line,
                    "normalized_text": normalized,
                    "raw_checksum": sha256_text(line),
                    "normalized_checksum": sha256_text(normalized),
                }
            )
        if character_end == len(text):
            break
        character_start = character_end + (2 if text[character_end : character_end + 2] == "\r\n" else 1)
        ordinal += 1
    return result


def _verify_checkout(root: Path, manifest: StaticPhase4B3Manifest) -> None:
    if not root.is_dir():
        raise Phase4B3EvidenceError(f"source root is not a directory: {root}")

    def git(*args: str) -> str:
        try:
            completed = subprocess.run(
                ["git", "-C", str(root), *args],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise Phase4B3EvidenceError(f"cannot verify source checkout {root}: {exc}") from exc
        return completed.stdout.strip()

    head = git("rev-parse", "HEAD")
    tree = git("rev-parse", "HEAD^{tree}")
    dirty = git("status", "--porcelain=v1", "--untracked-files=all")
    if head != EXPECTED_COMMIT or head != manifest.source["commit"]:
        raise Phase4B3EvidenceError(f"source commit mismatch: {head}")
    if tree != EXPECTED_TREE or tree != manifest.source["tree"]:
        raise Phase4B3EvidenceError(f"source tree mismatch: {tree}")
    if dirty:
        raise Phase4B3EvidenceError(f"source checkout is not clean: {dirty.splitlines()[0]}")


def _load_files(root: Path, manifest: StaticPhase4B3Manifest) -> list[Phase4B3File]:
    result: list[Phase4B3File] = []
    for spec in manifest.files:
        path = root.joinpath(*spec.relative_path.split("/"))
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise Phase4B3EvidenceError(f"cannot read sealed source {spec.relative_path}: {exc}") from exc
        if len(raw) != spec.size_bytes or sha256_bytes(raw) != spec.sha256:
            raise Phase4B3EvidenceError(f"sealed source identity mismatch: {spec.relative_path}")
        parsed: Any = None
        if spec.format == "json":
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise Phase4B3EvidenceError(f"invalid sealed JSON {spec.relative_path}: {exc}") from exc
        elif spec.format == "txt":
            try:
                parsed = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise Phase4B3EvidenceError(f"invalid sealed Readable {spec.relative_path}: {exc}") from exc
        result.append(Phase4B3File(spec=spec, path=path, root=root, raw=raw, parsed=parsed))
    return result


def _unique_index(rows: list[dict[str, Any]], table: str) -> dict[str, tuple[int, dict[str, Any]]]:
    result: dict[str, tuple[int, dict[str, Any]]] = {}
    for index, row in enumerate(rows):
        key = str(row.get("id"))
        if key in result:
            raise Phase4B3EvidenceError(f"duplicate native candidate {table}.id={key}")
        result[key] = index, row
    return result


def _reconstruct(
    root: Path,
    manifest: StaticPhase4B3Manifest,
    files: list[Phase4B3File],
) -> tuple[
    list[Phase4B3SourceRecord],
    list[Phase4B3Container],
    list[Phase4B3Segment],
    list[Phase4B3Relationship],
    list[Phase4B3TextResolution],
    list[Phase4B3TextCandidate],
    dict[str, dict[str, int]],
    dict[str, str],
]:
    by_path = {item.relative_path: item for item in files}
    tables: dict[str, list[dict[str, Any]]] = {}
    indexes: dict[str, dict[str, tuple[int, dict[str, Any]]]] = {}
    for table, relative in TABLE_FILES.items():
        parsed = by_path[relative].parsed
        if not isinstance(parsed, list) or not all(isinstance(row, dict) for row in parsed):
            raise Phase4B3EvidenceError(f"{table} is not an array of objects")
        expected_inventory = manifest.tables[table]["source_inventory_count"]
        if len(parsed) != expected_inventory:
            raise Phase4B3EvidenceError(f"{table} inventory mismatch: {len(parsed)} != {expected_inventory}")
        tables[table] = parsed
        indexes[table] = _unique_index(parsed, table)

    selected: dict[str, dict[int, str]] = {table: {} for table in TABLE_FILES}
    relationship_raw: list[dict[str, Any]] = []
    text_owners: list[dict[str, Any]] = []

    for table, family in (
        ("BookSuitExcelConfigData", "books"),
        ("BooksCodexExcelConfigData", "books"),
        ("WeaponExcelConfigData", "weapons"),
        ("WeaponCodexExcelConfigData", "weapons"),
    ):
        selected[table] = {index: family for index in range(len(tables[table]))}

    def add_relation(
        *,
        family: str,
        source_table: str,
        source_record_pointer: str,
        source_pointer: str,
        relation_kind: str,
        field_role: str,
        raw_value: Any,
        target_family: str | None,
        target_candidate_key: dict[str, Any] | None,
        status: str,
        target_record: dict[str, Any] | None = None,
        ambiguity: dict[str, Any] | None = None,
    ) -> None:
        relationship_raw.append(
            {
                "family": family,
                "source_table": source_table,
                "source_record_pointer": source_record_pointer,
                "source_pointer": source_pointer,
                "relation_kind": relation_kind,
                "field_role": field_role,
                "raw_value": raw_value,
                "target_family": target_family,
                "target_candidate_key": target_candidate_key,
                "status": status,
                "target_record": target_record,
                "ambiguity": ambiguity or {},
            }
        )

    for index, row in enumerate(tables["BooksCodexExcelConfigData"]):
        material_id = _positive_int(row.get("materialId"))
        target = indexes["MaterialExcelConfigData"].get(str(material_id))
        if target is None:
            status, target_record = "dangling", None
        else:
            target_index, _ = target
            selected["MaterialExcelConfigData"][target_index] = "books"
            status, target_record = "resolved", {"table": "MaterialExcelConfigData", "pointer": f"/{target_index}"}
        add_relation(
            family="books", source_table="BooksCodexExcelConfigData",
            source_record_pointer=f"/{index}", source_pointer=f"/{index}/materialId",
            relation_kind="books_codex_material_bridge", field_role="foreign_pointer_candidate",
            raw_value=row.get("materialId"), target_family="MaterialExcelConfigData",
            target_candidate_key=None if material_id is None else {"table": "MaterialExcelConfigData", "native_id": str(material_id)},
            status=status, target_record=target_record,
        )

    for index, family in sorted(selected["MaterialExcelConfigData"].items()):
        row = tables["MaterialExcelConfigData"][index]
        native_id = str(row["id"])
        target = indexes["DocumentExcelConfigData"].get(native_id)
        if target is None:
            status, target_record = "dangling", None
        else:
            target_index, _ = target
            selected["DocumentExcelConfigData"][target_index] = family
            status, target_record = "resolved", {"table": "DocumentExcelConfigData", "pointer": f"/{target_index}"}
        add_relation(
            family=family, source_table="MaterialExcelConfigData", source_record_pointer=f"/{index}",
            source_pointer=f"/{index}/id", relation_kind="book_material_document_implicit_candidate",
            field_role="raw_identifier", raw_value=row["id"], target_family="DocumentExcelConfigData",
            target_candidate_key={"table": "DocumentExcelConfigData", "native_id": native_id},
            status=status, target_record=target_record,
        )

    for index, row in enumerate(tables["WeaponExcelConfigData"]):
        if "storyId" not in row:
            add_relation(
                family="weapons", source_table="WeaponExcelConfigData", source_record_pointer=f"/{index}",
                source_pointer=f"/{index}/storyId", relation_kind="weapon_story_document",
                field_role="foreign_pointer_candidate", raw_value=None, target_family="DocumentExcelConfigData",
                target_candidate_key=None, status="unresolved",
            )
            continue
        story_id = _positive_int(row.get("storyId"))
        target = indexes["DocumentExcelConfigData"].get(str(story_id))
        if target is None:
            status, target_record = "dangling", None
        else:
            target_index, _ = target
            prior = selected["DocumentExcelConfigData"].get(target_index)
            if prior is not None and prior != "weapons":
                raise Phase4B3EvidenceError("Books/Weapons Document selection overlap")
            selected["DocumentExcelConfigData"][target_index] = "weapons"
            status, target_record = "resolved", {"table": "DocumentExcelConfigData", "pointer": f"/{target_index}"}
        add_relation(
            family="weapons", source_table="WeaponExcelConfigData", source_record_pointer=f"/{index}",
            source_pointer=f"/{index}/storyId", relation_kind="weapon_story_document",
            field_role="foreign_pointer_candidate", raw_value=row.get("storyId"), target_family="DocumentExcelConfigData",
            target_candidate_key=None if story_id is None else {"table": "DocumentExcelConfigData", "native_id": str(story_id)},
            status=status, target_record=target_record,
        )

    for index, row in enumerate(tables["WeaponCodexExcelConfigData"]):
        weapon_id = _positive_int(row.get("weaponId"))
        target = indexes["WeaponExcelConfigData"].get(str(weapon_id))
        status = "resolved" if target else "dangling"
        target_record = None if target is None else {"table": "WeaponExcelConfigData", "pointer": f"/{target[0]}"}
        add_relation(
            family="weapons", source_table="WeaponCodexExcelConfigData", source_record_pointer=f"/{index}",
            source_pointer=f"/{index}/weaponId", relation_kind="weapon_codex_weapon",
            field_role="foreign_pointer_candidate", raw_value=row.get("weaponId"), target_family="WeaponExcelConfigData",
            target_candidate_key=None if weapon_id is None else {"table": "WeaponExcelConfigData", "native_id": str(weapon_id)},
            status=status, target_record=target_record,
        )

    for doc_index, family in sorted(selected["DocumentExcelConfigData"].items()):
        row = tables["DocumentExcelConfigData"][doc_index]
        raw_candidates = row.get("questIDList")
        values = raw_candidates if isinstance(raw_candidates, list) else []
        candidate_ids = [value for value in (_positive_int(item) for item in values) if value is not None]
        concrete: list[dict[str, Any]] = []
        for value in candidate_ids:
            target = indexes["LocalizationExcelConfigData"].get(str(value))
            if target is not None:
                target_index, _ = target
                prior = selected["LocalizationExcelConfigData"].get(target_index)
                if prior is not None and prior != family:
                    raise Phase4B3EvidenceError("Books/Weapons Localization selection overlap")
                selected["LocalizationExcelConfigData"][target_index] = family
                concrete.append({"table": "LocalizationExcelConfigData", "pointer": f"/{target_index}", "native_id": str(value)})
        if not candidate_ids:
            status, target_key, target_record, ambiguity = "unresolved", None, None, {}
        elif len(candidate_ids) == 1 and len(concrete) == 1:
            status = "resolved"
            target_key = {"table": "LocalizationExcelConfigData", "native_id": str(candidate_ids[0])}
            target_record, ambiguity = {"table": "LocalizationExcelConfigData", "pointer": concrete[0]["pointer"]}, {}
        elif len(candidate_ids) == 1:
            status = "dangling"
            target_key = {"table": "LocalizationExcelConfigData", "native_id": str(candidate_ids[0])}
            target_record, ambiguity = None, {}
        else:
            status, target_key, target_record = "ambiguous", None, None
            ambiguity = {
                "rule": "questIDList-localization-candidate-set-v1",
                "candidates": [
                    {
                        "native_id": str(value),
                        "record_pointer": None if indexes["LocalizationExcelConfigData"].get(str(value)) is None else f"/{indexes['LocalizationExcelConfigData'][str(value)][0]}",
                    }
                    for value in candidate_ids
                ],
            }
        add_relation(
            family=family, source_table="DocumentExcelConfigData", source_record_pointer=f"/{doc_index}",
            source_pointer=f"/{doc_index}/questIDList", relation_kind="document_localization_implicit_candidate_set",
            field_role="unresolved_field_role", raw_value=raw_candidates, target_family="LocalizationExcelConfigData",
            target_candidate_key=target_key, status=status, target_record=target_record, ambiguity=ambiguity,
        )
        for field in ("contentLocalizedId", "questContentLocalizedId"):
            if field not in row:
                continue
            raw_field = row[field]
            occurrences = raw_field if isinstance(raw_field, list) else [raw_field]
            for sub_index, raw_value in enumerate(occurrences):
                value = _positive_int(raw_value)
                target = indexes["LocalizationExcelConfigData"].get(str(value))
                if value is None:
                    status, target_key, target_record = "unresolved", None, None
                elif target is None:
                    status = "dangling"
                    target_key = {"table": "LocalizationExcelConfigData", "native_id": str(value)}
                    target_record = None
                else:
                    status = "resolved"
                    target_index, _ = target
                    prior = selected["LocalizationExcelConfigData"].get(target_index)
                    if prior is not None and prior != family:
                        raise Phase4B3EvidenceError("Books/Weapons Localization selection overlap")
                    selected["LocalizationExcelConfigData"][target_index] = family
                    target_key = {"table": "LocalizationExcelConfigData", "native_id": str(value)}
                    target_record = {"table": "LocalizationExcelConfigData", "pointer": f"/{target_index}"}
                suffix = f"/{sub_index}" if isinstance(raw_field, list) else ""
                add_relation(
                    family=family, source_table="DocumentExcelConfigData", source_record_pointer=f"/{doc_index}",
                    source_pointer=f"/{doc_index}/{field}{suffix}",
                    relation_kind=f"document_{field}_localization_candidate", field_role="foreign_pointer_candidate",
                    raw_value=raw_value, target_family="LocalizationExcelConfigData",
                    target_candidate_key=target_key, status=status, target_record=target_record,
                )

    sealed_paths = set(by_path)
    readable_roles: dict[str, str] = {}
    for loc_index, family in sorted(selected["LocalizationExcelConfigData"].items()):
        row = tables["LocalizationExcelConfigData"][loc_index]
        for field, (locale, field_role) in LOCALIZATION_FIELDS.items():
            raw_value = row.get(field)
            candidate_path: str | None = None
            if isinstance(raw_value, str):
                prefix = f"ART/UI/Readable/{locale}/"
                if raw_value.startswith(prefix) and len(raw_value) > len(prefix):
                    candidate_path = "Readable/" + raw_value[len("ART/UI/Readable/") :] + ".txt"
            if candidate_path is None:
                status, target_key, target_record = "unresolved", None, None
            elif candidate_path in sealed_paths:
                prior = readable_roles.get(candidate_path)
                if prior is not None and prior != family:
                    raise Phase4B3EvidenceError(f"Books/Weapons Readable overlap: {candidate_path}")
                readable_roles[candidate_path] = family
                status = "resolved"
                target_key = {"locale": locale, "relative_path": candidate_path}
                target_record = {"family": "Readable", "relative_path": candidate_path, "pointer": "/text"}
            else:
                status = "dangling"
                target_key = {"locale": locale, "relative_path": candidate_path}
                target_record = None
            add_relation(
                family=family, source_table="LocalizationExcelConfigData", source_record_pointer=f"/{loc_index}",
                source_pointer=f"/{loc_index}/{field}", relation_kind="localization_readable_candidate",
                field_role=field_role, raw_value=raw_value, target_family="Readable",
                target_candidate_key=target_key, status=status, target_record=target_record,
            )

    for table, fields in TEXT_FIELDS.items():
        for index, family in sorted(selected[table].items()):
            row = tables[table][index]
            for field in fields:
                value = _positive_int(row.get(field))
                if value is not None:
                    text_owners.append(
                        {
                            "family": family, "source_table": table,
                            "source_record_pointer": f"/{index}", "source_pointer": f"/{index}/{field}",
                            "field": field, "textmap_hash": str(value),
                        }
                    )

    selected_hashes = {owner["textmap_hash"] for owner in text_owners}
    text_assets: dict[tuple[str, str, str], str] = {}
    for locale in LOCALES:
        for shard_kind in ("main", "medium"):
            relative = manifest.boundaries["textmap_shards"][locale][shard_kind]
            shard = by_path[relative].parsed
            if not isinstance(shard, dict):
                raise Phase4B3EvidenceError(f"TextMap shard is not an object: {relative}")
            for textmap_hash in selected_hashes:
                if textmap_hash in shard:
                    value = shard[textmap_hash]
                    if not isinstance(value, str):
                        raise Phase4B3EvidenceError(f"TextMap value is not text: {relative}#{textmap_hash}")
                    text_assets[(locale, shard_kind, textmap_hash)] = value

    resolution_raw: list[dict[str, Any]] = []
    candidate_raw: list[dict[str, Any]] = []
    for owner in text_owners:
        for locale in LOCALES:
            textmap_hash = owner["textmap_hash"]
            outcome = resolve_phase4b3_textmap_occurrence(
                text_assets.get((locale, "main", textmap_hash)),
                text_assets.get((locale, "medium", textmap_hash)),
            )
            resolution_raw.append(
                {
                    **owner, "locale": locale, "status": outcome.status,
                    "raw_text": outcome.raw_text, "normalized_text": outcome.normalized_text,
                    "raw_checksum": outcome.raw_checksum, "normalized_checksum": outcome.normalized_checksum,
                }
            )
            for candidate in outcome.candidates:
                candidate_raw.append(
                    {
                        **owner, "locale": locale, "status": outcome.status,
                        "shard_kind": candidate.shard_kind,
                        "source_path": manifest.boundaries["textmap_shards"][locale][candidate.shard_kind],
                        "source_pointer": f"/{textmap_hash}",
                        "raw_text": candidate.raw_text, "normalized_text": candidate.normalized_text,
                        "raw_checksum": candidate.raw_checksum, "normalized_checksum": candidate.normalized_checksum,
                    }
                )
        add_relation(
            family=owner["family"], source_table=owner["source_table"],
            source_record_pointer=owner["source_record_pointer"], source_pointer=owner["source_pointer"],
            relation_kind=TEXT_RELATION_KINDS[(owner["source_table"], owner["field"])],
            field_role="verified_textmap_reference", raw_value=int(owner["textmap_hash"]),
            target_family="TextMap", target_candidate_key={"textmap_hash": owner["textmap_hash"]}, status="resolved",
        )

    selected_rows_raw: dict[str, list[dict[str, Any]]] = {}
    container_digest_raw: list[dict[str, Any]] = []
    containers: list[Phase4B3Container] = []
    for table in TABLE_FILES:
        selected_rows_raw[table] = []
        for index, family in sorted(selected[table].items()):
            row = tables[table][index]
            contract = {
                "pointer": f"/{index}", "native_id": str(row["id"]),
                "raw_sha256": _sha_value(row), "family": family,
            }
            selected_rows_raw[table].append(contract)
            container_digest_raw.append(
                {
                    "family": family, "source_path": TABLE_FILES[table], "record_pointer": f"/{index}",
                    "source_family": table, "locale": None, "native_id": str(row["id"]),
                }
            )
            containers.append(
                Phase4B3Container(
                    family=family, relative_path=TABLE_FILES[table], record_pointer=f"/{index}",
                    record_kind="static_excel_row", native_id=str(row["id"]), container_kind="excel_row",
                    source_family=table, locale=None, candidate_namespace=f"agd.static.{table}",
                    candidate_key={"table": table, "native_id": str(row["id"])}, raw_value=row,
                    raw_json=canonical_json(row),
                    metadata={"family": family, "classification": "raw_excel_row", "cross_language_alignment": "not_applicable"},
                )
            )
        manifest_rows = manifest.tables[table]["selected_rows"]
        if selected_rows_raw[table] != manifest_rows:
            raise Phase4B3EvidenceError(f"derived selected row set disagrees with manifest: {table}")
        if _set_digest(selected_rows_raw[table]) != manifest.tables[table]["selected_set_digest"]:
            raise Phase4B3EvidenceError(f"derived selected row digest disagrees with manifest: {table}")

    readable_contracts: list[dict[str, Any]] = []
    segment_digest_raw: list[dict[str, Any]] = []
    segments: list[Phase4B3Segment] = []
    for relative, family in sorted(readable_roles.items()):
        file = by_path[relative]
        locale = relative.split("/")[1]
        classification = classify_phase4b3_readable(file.raw)
        local_segments = [] if classification != "narrative" else _line_segments(file.raw)
        readable_contract = {
            "relative_path": relative, "locale": locale, "family": family,
            "classification": classification, "segment_count": len(local_segments),
            "segment_set_digest": _set_digest(local_segments),
        }
        readable_contracts.append(readable_contract)
        container_digest_raw.append(
            {
                "family": family, "source_path": relative, "record_pointer": "/text",
                "source_family": "Readable", "locale": locale, "native_id": Path(relative).stem,
            }
        )
        containers.append(
            Phase4B3Container(
                family=family, relative_path=relative, record_pointer="/text", record_kind="static_readable_file",
                native_id=Path(relative).stem, container_kind="readable_file", source_family="Readable",
                locale=locale, candidate_namespace=f"agd.static.Readable.{locale}",
                candidate_key={"locale": locale, "relative_path": relative}, raw_value=file.parsed,
                raw_json=canonical_json(file.parsed),
                metadata={"family": family, "classification": classification, "cross_language_alignment": "unresolved"},
            )
        )
        for item in local_segments:
            segment_digest_raw.append({"family": family, "source_path": relative, "locale": locale, **item})
            segments.append(
                Phase4B3Segment(
                    family=family, relative_path=relative, record_pointer="/text", locale=locale,
                    source_pointer=f"/lines/{item['file_local_ordinal']}",
                    segmentation_version=manifest.versions["segmentation"], **item,
                )
            )
    if readable_contracts != list(manifest.readables):
        raise Phase4B3EvidenceError("derived Readable selection/classification disagrees with manifest")

    coordinate_lookup = {item.coordinate: item for item in containers}
    relationships: list[Phase4B3Relationship] = []
    for item in relationship_raw:
        target_record = item["target_record"]
        target_coordinate: tuple[str, str] | None = None
        if target_record is not None:
            if "table" in target_record:
                target_coordinate = TABLE_FILES[target_record["table"]], target_record["pointer"]
            else:
                target_coordinate = target_record["relative_path"], target_record["pointer"]
            if target_coordinate not in coordinate_lookup:
                raise Phase4B3EvidenceError(f"resolved target not selected: {target_coordinate}")
        if item["status"] != "resolved" and target_coordinate is not None:
            raise Phase4B3EvidenceError("non-resolved relationship selected a concrete target")
        relationships.append(
            Phase4B3Relationship(
                family=item["family"], source_relative_path=TABLE_FILES[item["source_table"]],
                source_record_pointer=item["source_record_pointer"], source_family=item["source_table"],
                source_pointer=item["source_pointer"], relation_kind=item["relation_kind"], field_role=item["field_role"],
                raw_value=item["raw_value"], target_family=item["target_family"],
                target_candidate_key=item["target_candidate_key"], status=item["status"],
                target_container_coordinate=target_coordinate, ambiguity=item["ambiguity"],
            )
        )

    resolutions = [
        Phase4B3TextResolution(
            family=item["family"], owning_source_relative_path=TABLE_FILES[item["source_table"]],
            owning_source_record_pointer=item["source_record_pointer"], relationship_source_pointer=item["source_pointer"],
            locale=item["locale"], textmap_hash=item["textmap_hash"], status=item["status"],
            raw_text=item["raw_text"], normalized_text=item["normalized_text"],
            raw_checksum=item["raw_checksum"], normalized_checksum=item["normalized_checksum"],
        )
        for item in resolution_raw
    ]
    candidates = [
        Phase4B3TextCandidate(
            family=item["family"], owning_source_relative_path=TABLE_FILES[item["source_table"]],
            owning_source_record_pointer=item["source_record_pointer"],
            relationship_source_pointer=f"{item['source_record_pointer']}/{item['field']}",
            locale=item["locale"], textmap_hash=item["textmap_hash"], status=item["status"],
            shard_kind=item["shard_kind"], source_relative_path=item["source_path"],
            source_record_pointer=item["source_pointer"], raw_text=item["raw_text"],
            normalized_text=item["normalized_text"], raw_checksum=item["raw_checksum"],
            normalized_checksum=item["normalized_checksum"],
        )
        for item in candidate_raw
    ]

    text_asset_digest_raw = [
        {
            "source_path": manifest.boundaries["textmap_shards"][locale][shard],
            "source_pointer": f"/{textmap_hash}", "locale": locale, "shard_kind": shard,
            "textmap_hash": textmap_hash, "raw_checksum": sha256_text(text),
        }
        for (locale, shard, textmap_hash), text in sorted(text_assets.items())
    ]
    source_records: list[Phase4B3SourceRecord] = []
    source_record_digest_raw: list[dict[str, Any]] = []
    for container in containers:
        source_records.append(
            Phase4B3SourceRecord(
                relative_path=container.relative_path, pointer=container.record_pointer,
                record_kind=container.record_kind, native_id=container.native_id,
                raw_sha256=_sha_value(container.raw_value), raw_json=container.raw_json,
                raw_value=container.raw_value,
            )
        )
        source_record_digest_raw.append(
            {
                "source_path": container.relative_path, "pointer": container.record_pointer,
                "record_kind": container.record_kind, "native_id": container.native_id,
                "raw_sha256": _sha_value(container.raw_value), "raw_json": container.raw_json,
            }
        )
    for item in text_asset_digest_raw:
        text = text_assets[(item["locale"], item["shard_kind"], item["textmap_hash"])]
        source_records.append(
            Phase4B3SourceRecord(
                relative_path=item["source_path"], pointer=item["source_pointer"],
                record_kind="static_textmap_entry", native_id=item["textmap_hash"],
                raw_sha256=item["raw_checksum"], raw_json=canonical_json(text), raw_value=text,
            )
        )
        source_record_digest_raw.append(
            {
                "source_path": item["source_path"], "pointer": item["source_pointer"],
                "record_kind": "static_textmap_entry", "native_id": item["textmap_hash"],
                "raw_sha256": item["raw_checksum"], "raw_json": canonical_json(text),
            }
        )

    file_digest_raw = [
        {"relative_path": item.relative_path, "format": item.spec.format, "size_bytes": item.spec.size_bytes, "sha256": item.spec.sha256}
        for item in files
    ]
    digests = {
        "files": _set_digest(file_digest_raw),
        "source_records": _set_digest(source_record_digest_raw),
        "containers": _set_digest(container_digest_raw),
        "segments": _set_digest(segment_digest_raw),
        "relationships": _set_digest(relationship_raw),
        "text_resolutions": _set_digest(resolution_raw),
        "text_candidates": _set_digest(candidate_raw),
        "text_assets": _set_digest(text_asset_digest_raw),
    }
    if digests != manifest.expected["occurrence_set_digests"]:
        differing = [key for key in digests if digests[key] != manifest.expected["occurrence_set_digests"][key]]
        raise Phase4B3EvidenceError(f"derived occurrence set digest mismatch: {differing}")

    counts = {
        "selected_files": len(files), "source_records": len(source_records), "containers": len(containers),
        "segments": len(segments), "relationships": len(relationships),
        "text_resolutions": len(resolutions), "text_candidates": len(candidates),
    }
    if counts != manifest.expected["counts"]:
        raise Phase4B3EvidenceError(f"derived occurrence counts mismatch: {counts}")
    status_counts = {
        "relationships": {
            status: Counter(item.status for item in relationships)[status]
            for status in ("resolved", "dangling", "ambiguous", "unresolved")
        },
        "text_resolutions": {
            status: Counter(item.status for item in resolutions)[status]
            for status in ("unique_main", "unique_medium", "both_equal", "both_conflict", "missing")
        },
        "readables": {
            status: Counter(item["classification"] for item in readable_contracts)[status]
            for status in ("narrative", "empty", "blank_whitespace_only", "placeholder_exact_test")
        },
    }
    if status_counts["relationships"] != {
        key: manifest.expected["relationship_statuses"][key]
        for key in ("resolved", "dangling", "ambiguous", "unresolved")
    }:
        raise Phase4B3EvidenceError("relationship status reconciliation mismatch")
    if status_counts["text_resolutions"] != manifest.expected["text_resolution_statuses"]:
        raise Phase4B3EvidenceError("TextMap status reconciliation mismatch")
    if status_counts["readables"] != manifest.expected["readable_classifications"]:
        raise Phase4B3EvidenceError("Readable status reconciliation mismatch")
    return source_records, containers, segments, relationships, resolutions, candidates, status_counts, digests


def preflight_static_phase4b3_evidence_bundle(
    source_root: str | Path,
    manifest_path: str | Path,
) -> Phase4B3Preflight:
    """Rebuild every sealed occurrence without touching a DB or report path."""
    manifest = load_static_phase4b3_manifest(manifest_path)
    root = Path(source_root).resolve()
    _verify_checkout(root, manifest)
    files = _load_files(root, manifest)
    (
        source_records, containers, segments, relationships,
        resolutions, candidates, status_counts, digests,
    ) = _reconstruct(root, manifest, files)
    source_manifest = dict(manifest.source)
    snapshot_id = f"{source_manifest['source_id']}:{source_manifest['release_label']}:{source_manifest['commit'][:12]}"
    config = {
        "family": "static_phase4b3_book_weapon_corpus",
        "manifest_raw_sha256": manifest.raw_sha256,
        "selected_paths": [item.relative_path for item in files],
        "snapshot": {"commit": source_manifest["commit"], "tree": source_manifest["tree"]},
        "versions": manifest.versions,
    }
    config_checksum = sha256_text(canonical_json(config))
    parser_build_id = digest_id(
        "pb-static-phase4b3", snapshot_id, manifest.raw_sha256,
        manifest.versions["parser"], manifest.versions["projection"],
        manifest.versions["segmentation"], config_checksum,
    )
    ingest_run_id = digest_id(
        "ir-static-phase4b3", snapshot_id, manifest.raw_sha256, parser_build_id, config_checksum,
    )
    return Phase4B3Preflight(
        manifest=manifest, source_manifest=source_manifest, snapshot_id=snapshot_id,
        parser_build_id=parser_build_id, ingest_run_id=ingest_run_id,
        config_checksum=config_checksum, files=tuple(files), source_records=tuple(source_records),
        containers=tuple(containers), segments=tuple(segments), relationships=tuple(relationships),
        text_resolutions=tuple(resolutions), text_candidates=tuple(candidates),
        status_counts=status_counts, occurrence_set_digests=digests,
    )


def _run_scope(preflight: Phase4B3Preflight) -> dict[str, Any]:
    return {
        "family": "static_phase4b3_book_weapon_corpus",
        "manifest_id": preflight.manifest.manifest_id,
        "selected_paths": [item.relative_path for item in preflight.files],
    }


def _run_config(preflight: Phase4B3Preflight) -> dict[str, Any]:
    return {
        "manifest_raw_sha256": preflight.manifest.raw_sha256,
        "versions": preflight.manifest.versions,
        "full_static_scan": False,
        "logical_identity_created": False,
        "cross_language_alignment_created": False,
        "textmap_evidence_container_created": False,
    }


def _container_id(preflight: Phase4B3Preflight, item: Phase4B3Container) -> str:
    return digest_id(
        "static-container", preflight.snapshot_id, preflight.manifest.raw_sha256,
        preflight.ingest_run_id, item.relative_path, item.record_pointer,
    )


def _relationship_id(preflight: Phase4B3Preflight, item: Phase4B3Relationship) -> str:
    return digest_id(
        "static-relationship", preflight.snapshot_id, preflight.manifest.raw_sha256,
        preflight.ingest_run_id, item.source_relative_path, item.source_record_pointer, item.source_pointer,
    )


def _resolution_id(preflight: Phase4B3Preflight, relationship_id: str, locale: str) -> str:
    return digest_id(
        "static-text-resolution", preflight.snapshot_id, preflight.manifest.raw_sha256,
        preflight.ingest_run_id, relationship_id, locale,
    )


def _candidate_id(
    preflight: Phase4B3Preflight,
    resolution_id: str,
    item: Phase4B3TextCandidate,
) -> str:
    return digest_id(
        "static-text-candidate", preflight.snapshot_id, preflight.manifest.raw_sha256,
        preflight.ingest_run_id, resolution_id, item.source_relative_path,
        item.source_record_pointer, item.shard_kind,
    )


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


def _db_record_kind(item: Phase4B3SourceRecord) -> str:
    if item.coordinate in B1_STATIC_RECORD_COORDINATES:
        return item.record_kind
    return {
        "static_excel_row": "excel_row",
        "static_readable_file": "readable_file",
        "static_textmap_entry": "textmap_entry",
    }[item.record_kind]


def _insert_text_candidate(
    conn: sqlite3.Connection,
    values: tuple[Any, ...],
    *,
    insert_missing: bool = True,
) -> None:
    _ensure_row(
        conn, "static_text_candidate_occurrence",
        (
            "id", "resolution_occurrence_id", "ingest_run_id", "snapshot_id", "source_file_id",
            "source_record_id", "source_pointer", "shard_kind", "locale", "resolution_status",
            "raw_text", "normalized_text", "raw_checksum", "normalized_checksum", "metadata_json",
        ),
        values,
        insert_missing=insert_missing,
    )


def _schema9_readonly_check(target: Path) -> None:
    if not target.is_file():
        raise Phase4B3EvidenceError(f"existing schema-9 database is required: {target}")
    try:
        uri = target.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            conn.execute("PRAGMA query_only=ON")
            versions = [row[0] for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")]
            if versions != list(range(1, 10)):
                raise Phase4B3EvidenceError(f"database schema contract is not exactly 1..9: {versions}")
        finally:
            conn.close()
    except Phase4B3EvidenceError:
        raise
    except sqlite3.Error as exc:
        raise Phase4B3EvidenceError(f"cannot validate target schema: {exc}") from exc


def _run_counts(conn: sqlite3.Connection, run_id: str) -> dict[str, int]:
    tables = {
        "containers": "static_evidence_container",
        "segments": "static_segment_occurrence",
        "relationships": "static_relationship_occurrence",
        "text_resolutions": "static_text_resolution_occurrence",
        "text_candidates": "static_text_candidate_occurrence",
    }
    return {
        key: int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE ingest_run_id=?", (run_id,)).fetchone()[0])
        for key, table in tables.items()
    }


def _fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"sha256": sha256_bytes(path.read_bytes()), "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _family_counts(preflight: Phase4B3Preflight) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    textmap_paths = {
        path
        for locale_paths in preflight.manifest.boundaries["textmap_shards"].values()
        for path in locale_paths.values()
    }
    for family in ("books", "weapons"):
        container_coordinates = {
            item.coordinate for item in preflight.containers if item.family == family
        }
        candidate_coordinates = {
            (item.source_relative_path, item.source_record_pointer)
            for item in preflight.text_candidates
            if item.family == family
        }
        selected_paths = {
            item.relative_path for item in preflight.containers if item.family == family
        } | {
            item.source_relative_path
            for item in preflight.text_candidates
            if item.family == family
        } | textmap_paths
        result[family] = {
            "selected_files": len(selected_paths),
            "source_records": len(container_coordinates | candidate_coordinates),
            "containers": sum(item.family == family for item in preflight.containers),
            "segments": sum(item.family == family for item in preflight.segments),
            "relationships": sum(item.family == family for item in preflight.relationships),
            "text_resolutions": sum(item.family == family for item in preflight.text_resolutions),
            "text_candidates": sum(item.family == family for item in preflight.text_candidates),
        }
    return result


def _build_report(target: Path, preflight: Phase4B3Preflight) -> dict[str, Any]:
    return {
        "status": "complete",
        "db_path": str(target.resolve()),
        "snapshot_id": preflight.snapshot_id,
        "parser_build_id": preflight.parser_build_id,
        "ingest_run_id": preflight.ingest_run_id,
        "config_checksum": preflight.config_checksum,
        "database_fingerprint": _fingerprint(target),
        "manifest": {"manifest_id": preflight.manifest.manifest_id, "raw_sha256": preflight.manifest.raw_sha256},
        "counts": {"files": len(preflight.files), **{
            key: len(getattr(preflight, key))
            for key in ("source_records", "containers", "segments", "relationships", "text_resolutions", "text_candidates")
        }},
        "family_counts": _family_counts(preflight),
        "family_selected_paths": {
            family: sorted(
                {item.relative_path for item in preflight.containers if item.family == family}
                | {
                    item.source_relative_path
                    for item in preflight.text_candidates
                    if item.family == family
                }
                | {
                    path
                    for locale_paths in preflight.manifest.boundaries["textmap_shards"].values()
                    for path in locale_paths.values()
                }
            )
            for family in ("books", "weapons")
        },
        "status_counts": preflight.status_counts,
        "locale_breakdown": preflight.manifest.expected["locale_breakdown"],
        "occurrence_set_digests": preflight.occurrence_set_digests,
        "scope": {
            "selected_paths": [item.relative_path for item in preflight.files],
            "full_static_scan": False, "fts_rebuilt": False, "logical_identity_created": False,
            "cross_language_alignment_created": False, "textmap_evidence_container_created": False,
        },
    }


def _write_occurrences(
    conn: sqlite3.Connection,
    preflight: Phase4B3Preflight,
    *,
    insert_missing: bool = True,
) -> None:
    def ensure(table: str, columns: tuple[str, ...], values: tuple[Any, ...]) -> None:
        _ensure_row(conn, table, columns, values, insert_missing=insert_missing)

    source = preflight.source_manifest
    source_values = (source["source_id"], "primary", source["upstream"], None)
    row = conn.execute("SELECT id,kind,url,license_note FROM source_catalog WHERE id=?", (source["source_id"],)).fetchone()
    if row is None:
        if not insert_missing:
            raise sqlite3.IntegrityError("source catalog row is missing")
        conn.execute("INSERT INTO source_catalog(id,kind,url,license_note) VALUES (?,?,?,?)", source_values)
    elif tuple(row) != source_values:
        raise sqlite3.IntegrityError("source catalog mismatch")

    snapshot_columns = ("source_id", "release_label", "build", "commit", "tree", "observed_at")
    snapshot_values = (
        source["source_id"], source["release_label"], source["build"], source["commit"], source["tree"], source["observed_at"],
    )
    row = conn.execute(
        'SELECT source_id,release_label,build,"commit",tree,observed_at FROM source_snapshot WHERE id=?',
        (preflight.snapshot_id,),
    ).fetchone()
    if row is None:
        if not insert_missing:
            raise sqlite3.IntegrityError("source snapshot row is missing")
        conn.execute(
            'INSERT INTO source_snapshot(id,source_id,release_label,build,"commit",tree,observed_at,parser_version,mapping_version,status,metadata_json) '
            "VALUES (?,?,?,?,?,?,?,?,?,'active',?)",
            (
                preflight.snapshot_id, *snapshot_values, preflight.manifest.versions["parser"],
                preflight.manifest.versions["projection"], canonical_json({"sealed_source_manifest": True}),
            ),
        )
    elif tuple(row[column] for column in snapshot_columns) != snapshot_values:
        raise sqlite3.IntegrityError("source snapshot mismatch")

    parser_values = (
        preflight.parser_build_id, preflight.manifest.versions["parser"], preflight.manifest.versions["projection"],
        "static-phase4b3-sealed-book-weapon-corpus", preflight.config_checksum, source["observed_at"],
    )
    ensure(
        "parser_build", ("id", "parser_version", "mapping_version", "fingerprint", "config_checksum", "created_at"), parser_values,
    )
    run_values = (
        preflight.ingest_run_id, preflight.snapshot_id, preflight.parser_build_id,
        canonical_json(_run_scope(preflight)), canonical_json(_run_config(preflight)),
        "running" if insert_missing else "complete", source["observed_at"],
    )
    ensure(
        "ingest_run", ("id", "snapshot_id", "parser_build_id", "scope_json", "config_json", "status", "created_at"), run_values,
    )

    file_ids: dict[str, str] = {}
    for item in preflight.files:
        file_id = digest_id("sf", preflight.snapshot_id, item.relative_path)
        file_ids[item.relative_path] = file_id
        ensure(
            "source_file",
            ("id", "snapshot_id", "relative_path", "upstream_blob_oid", "sha256", "size_bytes", "format", "root_path"),
            (
                file_id, preflight.snapshot_id, item.relative_path, None, item.spec.sha256,
                item.spec.size_bytes, item.spec.format, str(item.root),
            ),
        )

    record_ids: dict[tuple[str, str], str] = {}
    for item in preflight.source_records:
        file_id = file_ids[item.relative_path]
        record_id = digest_id("sr", file_id, item.pointer)
        record_ids[item.coordinate] = record_id
        # Phase 4B1/B2 already sealed Readable source_record.raw_json as an
        # explicit text object.  The B3 manifest digest keeps the raw string
        # occurrence form, while the shared immutable DB row must retain that
        # established representation.
        db_raw_json = (
            canonical_json({"text": item.raw_value})
            if item.record_kind == "static_readable_file"
            else item.raw_json
        )
        # The B2 WeaponCodex canary established weaponId as this raw table's
        # immutable source_record native locator.  B3 still seals the row's
        # table-scoped `id` as its container candidate; these are deliberately
        # separate locator roles, not logical identity.
        db_native_id = (
            str(item.raw_value["weaponId"])
            if item.record_kind == "static_excel_row"
            and item.relative_path == TABLE_FILES["WeaponCodexExcelConfigData"]
            else item.native_id
        )
        ensure(
            "source_record",
            ("id", "source_file_id", "pointer", "record_kind", "native_id", "raw_sha256", "raw_json"),
            (
                record_id, file_id, item.pointer, _db_record_kind(item), db_native_id,
                item.raw_sha256, db_raw_json,
            ),
        )

    container_ids: dict[tuple[str, str], str] = {}
    for item in preflight.containers:
        container_id = _container_id(preflight, item)
        container_ids[item.coordinate] = container_id
        metadata = {
            **item.metadata, "manifest_id": preflight.manifest.manifest_id,
            "manifest_raw_sha256": preflight.manifest.raw_sha256,
            "raw_evidence_identity": "snapshot_source_coordinate_ingest_run_occurrence",
        }
        ensure(
            "static_evidence_container",
            (
                "id", "ingest_run_id", "snapshot_id", "source_file_id", "source_record_id", "container_kind",
                "source_family", "locale", "candidate_namespace", "candidate_key_json", "metadata_json",
            ),
            (
                container_id, preflight.ingest_run_id, preflight.snapshot_id, file_ids[item.relative_path],
                record_ids[item.coordinate], item.container_kind, item.source_family, item.locale,
                item.candidate_namespace, canonical_json(item.candidate_key), canonical_json(metadata),
            ),
        )

    for item in preflight.segments:
        coordinate = item.container_coordinate
        segment_id = digest_id(
            "static-segment", preflight.snapshot_id, preflight.manifest.raw_sha256, preflight.ingest_run_id,
            item.relative_path, item.record_pointer, item.file_local_ordinal, item.byte_start, item.byte_end,
            item.character_start, item.character_end,
        )
        ensure(
            "static_segment_occurrence",
            (
                "id", "ingest_run_id", "snapshot_id", "evidence_container_id", "source_record_id", "container_kind",
                "locale", "source_pointer", "file_local_ordinal", "byte_start", "byte_end", "character_start",
                "character_end", "raw_text", "normalized_text", "raw_checksum", "normalized_checksum",
                "segmentation_version", "metadata_json",
            ),
            (
                segment_id, preflight.ingest_run_id, preflight.snapshot_id, container_ids[coordinate],
                record_ids[coordinate], "readable_file", item.locale, item.source_pointer,
                item.file_local_ordinal, item.byte_start, item.byte_end, item.character_start, item.character_end,
                item.raw_text, item.normalized_text, item.raw_checksum, item.normalized_checksum,
                item.segmentation_version,
                canonical_json({"family": item.family, "identity_scope": "snapshot_run_container_occurrence", "cross_language_alignment": "unresolved"}),
            ),
        )

    relationship_ids: dict[tuple[str, str, str], str] = {}
    for item in preflight.relationships:
        relationship_id = _relationship_id(preflight, item)
        relationship_ids[item.coordinate] = relationship_id
        source_coordinate = item.source_relative_path, item.source_record_pointer
        target_id = None if item.target_container_coordinate is None else container_ids[item.target_container_coordinate]
        ensure(
            "static_relationship_occurrence",
            (
                "id", "ingest_run_id", "snapshot_id", "source_container_id", "source_record_id", "source_family",
                "source_pointer", "relation_kind", "field_role", "target_family", "target_candidate_key_json",
                "candidate_resolution_status", "target_evidence_container_id", "ambiguity_json", "metadata_json",
            ),
            (
                relationship_id, preflight.ingest_run_id, preflight.snapshot_id, container_ids[source_coordinate],
                record_ids[source_coordinate], item.source_family, item.source_pointer, item.relation_kind,
                item.field_role, item.target_family,
                None if item.target_candidate_key is None else canonical_json(item.target_candidate_key),
                item.status, target_id, canonical_json(item.ambiguity),
                canonical_json({
                    "family": item.family, "raw_value": item.raw_value,
                    "manifest_id": preflight.manifest.manifest_id,
                    "candidate_resolution_detail": "family_specific_raw_value_and_sealed_candidate_set",
                }),
            ),
        )

    resolution_ids: dict[tuple[tuple[str, str, str], str], str] = {}
    for item in preflight.text_resolutions:
        owner = (
            item.owning_source_relative_path, item.owning_source_record_pointer, item.relationship_source_pointer,
        )
        relationship_id = relationship_ids[owner]
        resolution_id = _resolution_id(preflight, relationship_id, item.locale)
        resolution_ids[(owner, item.locale)] = resolution_id
        source_coordinate = owner[0], owner[1]
        ensure(
            "static_text_resolution_occurrence",
            (
                "id", "ingest_run_id", "snapshot_id", "static_relationship_occurrence_id", "source_container_id",
                "source_record_id", "source_pointer", "locale", "textmap_hash", "resolution_status", "raw_text",
                "normalized_text", "raw_checksum", "normalized_checksum", "metadata_json",
            ),
            (
                resolution_id, preflight.ingest_run_id, preflight.snapshot_id, relationship_id,
                container_ids[source_coordinate], record_ids[source_coordinate], item.relationship_source_pointer,
                item.locale, item.textmap_hash, item.status, item.raw_text, item.normalized_text,
                item.raw_checksum, item.normalized_checksum,
                canonical_json({
                    "family": item.family, "locator_only": True,
                    "owning_static_source": {"relative_path": owner[0], "record_pointer": owner[1], "source_pointer": owner[2]},
                    "both_conflict_auto_selected": False,
                }),
            ),
        )

    for item in preflight.text_candidates:
        owner = (
            item.owning_source_relative_path, item.owning_source_record_pointer, item.relationship_source_pointer,
        )
        resolution_id = resolution_ids[(owner, item.locale)]
        source_coordinate = item.source_relative_path, item.source_record_pointer
        _insert_text_candidate(
            conn,
            (
                _candidate_id(preflight, resolution_id, item), resolution_id, preflight.ingest_run_id,
                preflight.snapshot_id, file_ids[item.source_relative_path], record_ids[source_coordinate],
                item.source_record_pointer, item.shard_kind, item.locale, item.status, item.raw_text,
                item.normalized_text, item.raw_checksum, item.normalized_checksum,
                canonical_json({
                    "family": item.family, "textmap_hash_is_locator_not_identity": True,
                    "owning_relationship_id": relationship_ids[owner],
                    "exact_textmap_source": {"relative_path": item.source_relative_path, "record_pointer": item.source_record_pointer},
                }),
            ),
            insert_missing=insert_missing,
        )


def _validate_complete_run(conn: sqlite3.Connection, preflight: Phase4B3Preflight) -> None:
    row = conn.execute(
        "SELECT snapshot_id,parser_build_id,scope_json,config_json,status,created_at FROM ingest_run WHERE id=?",
        (preflight.ingest_run_id,),
    ).fetchone()
    expected = (
        preflight.snapshot_id, preflight.parser_build_id, canonical_json(_run_scope(preflight)),
        canonical_json(_run_config(preflight)), "complete", preflight.source_manifest["observed_at"],
    )
    if row is None or tuple(row) != expected:
        raise sqlite3.IntegrityError("existing Phase 4B3 run identity mismatch")
    _write_occurrences(conn, preflight, insert_missing=False)
    expected_counts = {
        key: len(getattr(preflight, key))
        for key in ("containers", "segments", "relationships", "text_resolutions", "text_candidates")
    }
    if _run_counts(conn, preflight.ingest_run_id) != expected_counts:
        raise sqlite3.IntegrityError("existing Phase 4B3 occurrence cardinality mismatch")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise sqlite3.IntegrityError("existing Phase 4B3 database has foreign key violations")


def ingest_static_phase4b3_evidence_bundle(
    db_path: str | Path,
    source_root: str | Path,
    manifest_path: str | Path,
) -> dict[str, Any]:
    """Append exactly one deterministic B3 run after a side-effect-free preflight."""
    preflight = preflight_static_phase4b3_evidence_bundle(source_root, manifest_path)
    target = Path(db_path)
    _schema9_readonly_check(target)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        if conn.execute("SELECT 1 FROM ingest_run WHERE id=?", (preflight.ingest_run_id,)).fetchone() is not None:
            _validate_complete_run(conn, preflight)
            return _build_report(target, preflight)
        try:
            conn.execute("BEGIN")
            _write_occurrences(conn, preflight)
            expected_counts = {
                key: len(getattr(preflight, key))
                for key in ("containers", "segments", "relationships", "text_resolutions", "text_candidates")
            }
            if _run_counts(conn, preflight.ingest_run_id) != expected_counts:
                raise sqlite3.IntegrityError("Phase 4B3 write cardinality mismatch")
            foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_keys:
                raise sqlite3.IntegrityError(f"Phase 4B3 foreign key check failed: {foreign_keys[:3]}")
            conn.execute(
                "UPDATE ingest_run SET status='complete' WHERE id=? AND status='running'",
                (preflight.ingest_run_id,),
            )
            if conn.execute("SELECT changes()").fetchone()[0] != 1:
                raise sqlite3.IntegrityError("Phase 4B3 run completion transition failed")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    except Phase4B3EvidenceError:
        raise
    except sqlite3.Error as exc:
        raise Phase4B3EvidenceError(str(exc)) from exc
    finally:
        conn.close()
    return _build_report(target, preflight)
