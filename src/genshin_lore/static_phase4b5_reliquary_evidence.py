from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .static_phase4b5_reliquary_manifest import (
    EXPECTED_COMMIT,
    EXPECTED_TREE,
    StaticPhase4B5FileSpec,
    StaticPhase4B5Manifest,
    load_static_phase4b5_reliquary_manifest,
)
from .util import canonical_json, digest_id, normalize_text, sha256_bytes, sha256_text


LOCALES = ("CHS", "EN", "JP")
TABLE_FILES = {
    "ReliquaryExcelConfigData": "ExcelBinOutput/ReliquaryExcelConfigData.json",
    "ReliquarySetExcelConfigData": "ExcelBinOutput/ReliquarySetExcelConfigData.json",
    "ReliquaryCodexExcelConfigData": "ExcelBinOutput/ReliquaryCodexExcelConfigData.json",
    "DocumentExcelConfigData": "ExcelBinOutput/DocumentExcelConfigData.json",
    "LocalizationExcelConfigData": "ExcelBinOutput/LocalizationExcelConfigData.json",
}
TEXT_FIELDS = {
    "ReliquaryExcelConfigData": ("nameTextMapHash", "descTextMapHash"),
    "DocumentExcelConfigData": ("titleTextMapHash",),
}
TEXT_RELATION_KINDS = {
    ("ReliquaryExcelConfigData", "nameTextMapHash"): "reliquary_name_textmap_candidate",
    ("ReliquaryExcelConfigData", "descTextMapHash"): "reliquary_description_textmap_candidate",
    ("DocumentExcelConfigData", "titleTextMapHash"): "document_title_textmap_candidate",
}
LOCALIZATION_FIELDS = {
    "DEFNEHAFMMA": ("CHS", "unknown_obfuscated"),
    "JDNBKKPEFAI": ("CHS", "unknown_obfuscated"),
    "enPath": ("EN", "unresolved_field_role"),
    "jpPath": ("JP", "unresolved_field_role"),
}
CODEX_SLOT_FIELDS = ("flowerId", "leatherId", "sandId", "cupId", "capId")


class Phase4B5EvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Phase4B5File:
    spec: StaticPhase4B5FileSpec
    path: Path
    root: Path
    raw: bytes
    parsed: Any

    @property
    def relative_path(self) -> str:
        return self.spec.relative_path


@dataclass(frozen=True)
class Phase4B5SourceRecord:
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
class Phase4B5Container:
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
class Phase4B5Segment:
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
class Phase4B5Relationship:
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
class Phase4B5TextCandidate:
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
class Phase4B5TextResolution:
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
class Phase4B5ResolvedTextMapOccurrence:
    status: str
    raw_text: str | None
    normalized_text: str | None
    raw_checksum: str | None
    normalized_checksum: str | None
    candidates: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class Phase4B5PreflightResult:
    manifest: StaticPhase4B5Manifest
    source_root: Path
    snapshot_id: str
    parser_build_id: str
    ingest_run_id: str
    config_checksum: str
    files: tuple[Phase4B5File, ...]
    source_records: tuple[Phase4B5SourceRecord, ...]
    containers: tuple[Phase4B5Container, ...]
    segments: tuple[Phase4B5Segment, ...]
    relationships: tuple[Phase4B5Relationship, ...]
    text_resolutions: tuple[Phase4B5TextResolution, ...]
    text_candidates: tuple[Phase4B5TextCandidate, ...]
    status_counts: dict[str, dict[str, int]]
    family_counts: dict[str, dict[str, int]]
    occurrence_set_digests: dict[str, str]
    family_selected_paths: dict[str, list[str]]


def _set_digest(items: list[dict[str, Any]]) -> str:
    ordered = sorted((canonical_json(item) for item in items))
    return hashlib.sha256(canonical_json(ordered).encode("utf-8")).hexdigest()


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def classify_phase4b5_readable(raw: bytes) -> str:
    if not raw:
        return "empty"
    normalized = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        return "blank_whitespace_only"
    if normalized.strip().lower() == "test":
        return "placeholder_exact_test"
    return "narrative"


def resolve_phase4b5_textmap_occurrence(
    main_text: str | None,
    medium_text: str | None,
) -> Phase4B5ResolvedTextMapOccurrence:
    if main_text is None and medium_text is None:
        return Phase4B5ResolvedTextMapOccurrence(
            status="missing",
            raw_text=None,
            normalized_text=None,
            raw_checksum=None,
            normalized_checksum=None,
            candidates=(),
        )
    if main_text is not None and medium_text is None:
        norm = normalize_text(main_text)
        return Phase4B5ResolvedTextMapOccurrence(
            status="unique_main",
            raw_text=main_text,
            normalized_text=norm,
            raw_checksum=sha256_text(main_text),
            normalized_checksum=sha256_text(norm),
            candidates=({"shard_kind": "main", "raw_text": main_text},),
        )
    if main_text is None and medium_text is not None:
        norm = normalize_text(medium_text)
        return Phase4B5ResolvedTextMapOccurrence(
            status="unique_medium",
            raw_text=medium_text,
            normalized_text=norm,
            raw_checksum=sha256_text(medium_text),
            normalized_checksum=sha256_text(norm),
            candidates=({"shard_kind": "medium", "raw_text": medium_text},),
        )
    if main_text == medium_text:
        norm = normalize_text(main_text)
        return Phase4B5ResolvedTextMapOccurrence(
            status="both_equal",
            raw_text=main_text,
            normalized_text=norm,
            raw_checksum=sha256_text(main_text),
            normalized_checksum=sha256_text(norm),
            candidates=(
                {"shard_kind": "main", "raw_text": main_text},
                {"shard_kind": "medium", "raw_text": medium_text},
            ),
        )
    return Phase4B5ResolvedTextMapOccurrence(
        status="both_conflict",
        raw_text=None,
        normalized_text=None,
        raw_checksum=None,
        normalized_checksum=None,
        candidates=(
            {"shard_kind": "main", "raw_text": main_text},
            {"shard_kind": "medium", "raw_text": medium_text},
        ),
    )


def _verify_source_checkout(root: Path) -> None:
    try:
        commit_res = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        tree_res = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"],
            capture_output=True,
            text=True,
            check=True,
        )
        status_res = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise Phase4B5EvidenceError(f"source checkout verification failed: {exc}") from exc

    commit = commit_res.stdout.strip()
    tree = tree_res.stdout.strip()
    status = status_res.stdout.strip()

    if commit != EXPECTED_COMMIT:
        raise Phase4B5EvidenceError(f"source checkout commit mismatch: expected {EXPECTED_COMMIT}, got {commit}")
    if tree != EXPECTED_TREE:
        raise Phase4B5EvidenceError(f"source checkout tree mismatch: expected {EXPECTED_TREE}, got {tree}")
    if status != "":
        raise Phase4B5EvidenceError(f"source checkout is not clean:\n{status}")


def _line_segments(raw: bytes) -> list[dict[str, Any]]:
    text = raw.decode("utf-8")
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
                    "raw_checksum": sha256_bytes(line.encode("utf-8")),
                    "normalized_checksum": sha256_bytes(normalized.encode("utf-8")),
                }
            )
        if character_end == len(text):
            break
        newline = 2 if text[character_end : character_end + 2] == "\r\n" else 1
        character_start = character_end + newline
        ordinal += 1
    return result


def preflight_static_phase4b5_reliquary_evidence_bundle(
    source_root: str | Path,
    manifest_input: StaticPhase4B5Manifest | str | Path,
) -> Phase4B5PreflightResult:
    root = Path(source_root).resolve()
    if not root.is_dir():
        raise Phase4B5EvidenceError(f"source root directory not found: {root}")
    manifest = manifest_input if isinstance(manifest_input, StaticPhase4B5Manifest) else load_static_phase4b5_reliquary_manifest(manifest_input)

    _verify_source_checkout(root)

    file_map: dict[str, Phase4B5File] = {}
    for spec in manifest.files:
        file_path = root / spec.relative_path
        if not file_path.is_file():
            raise Phase4B5EvidenceError(f"selected manifest file not found: {spec.relative_path}")
        raw_bytes = file_path.read_bytes()
        if len(raw_bytes) != spec.size_bytes:
            raise Phase4B5EvidenceError(f"file size mismatch for {spec.relative_path}: expected {spec.size_bytes}, got {len(raw_bytes)}")
        actual_sha = sha256_bytes(raw_bytes)
        if actual_sha != spec.sha256:
            raise Phase4B5EvidenceError(f"file sha256 mismatch for {spec.relative_path}: expected {spec.sha256}, got {actual_sha}")
        parsed: Any = None
        if spec.format == "json":
            try:
                parsed = json.loads(raw_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise Phase4B5EvidenceError(f"cannot parse JSON file {spec.relative_path}: {exc}") from exc
        file_map[spec.relative_path] = Phase4B5File(spec, file_path, root, raw_bytes, parsed)

    ordered_files = tuple(file_map[spec.relative_path] for spec in manifest.files)

    # Validate table data from loaded files
    tables_data: dict[str, list[dict[str, Any]]] = {}
    for table_name in manifest.tables:
        rel = f"ExcelBinOutput/{table_name}.json"
        parsed_rows = file_map[rel].parsed
        if not isinstance(parsed_rows, list) or len(parsed_rows) != manifest.tables[table_name]["source_inventory_count"]:
            raise Phase4B5EvidenceError(f"table row count mismatch for {table_name}")
        tables_data[table_name] = parsed_rows

    indexes = {
        table_name: {
            str(row[manifest.tables[table_name]["native_field"]]): (idx, row)
            for idx, row in enumerate(rows)
        }
        for table_name, rows in tables_data.items()
    }

    family = "reliquaries"
    manifest_id = manifest.manifest_id
    manifest_sha = manifest.raw_sha256

    containers: list[Phase4B5Container] = []
    relationships: list[Phase4B5Relationship] = []
    text_owners: list[dict[str, Any]] = []

    # 1. Primary Reliquary tables (ReliquaryExcelConfigData, ReliquarySetExcelConfigData, ReliquaryCodexExcelConfigData)
    for table_name in ("ReliquaryExcelConfigData", "ReliquarySetExcelConfigData", "ReliquaryCodexExcelConfigData"):
        native_field = manifest.tables[table_name]["native_field"]
        rel = f"ExcelBinOutput/{table_name}.json"
        for idx, row in enumerate(tables_data[table_name]):
            native_id = str(row[native_field])
            cand_key = {"table": table_name, "native_id": native_id}
            containers.append(
                Phase4B5Container(
                    family=family,
                    relative_path=rel,
                    record_pointer=f"/{idx}",
                    record_kind="static_excel_row",
                    native_id=native_id,
                    container_kind="excel_row",
                    source_family=table_name,
                    locale=None,
                    candidate_namespace=f"agd.static.{table_name}",
                    candidate_key=cand_key,
                    raw_value=row,
                    raw_json=canonical_json(row),
                    metadata={
                        "family": family,
                        "manifest_id": manifest_id,
                        "manifest_raw_sha256": manifest_sha,
                    },
                )
            )

    # Relationships from ReliquarySetExcelConfigData
    for idx, row in enumerate(tables_data["ReliquarySetExcelConfigData"]):
        contains_list = row.get("containsList")
        if isinstance(contains_list, list):
            for sub_idx, piece_raw in enumerate(contains_list):
                piece_id = _positive_int(piece_raw)
                target = indexes["ReliquaryExcelConfigData"].get(str(piece_id))
                target_coord = None if target is None else (TABLE_FILES["ReliquaryExcelConfigData"], f"/{target[0]}")
                status = "resolved" if target else "dangling"
                relationships.append(
                    Phase4B5Relationship(
                        family=family,
                        source_relative_path=TABLE_FILES["ReliquarySetExcelConfigData"],
                        source_record_pointer=f"/{idx}",
                        source_family="ReliquarySetExcelConfigData",
                        source_pointer=f"/{idx}/containsList/{sub_idx}",
                        relation_kind="reliquary_set_contains_candidate",
                        field_role="foreign_pointer_candidate",
                        raw_value=piece_raw,
                        target_family="ReliquaryExcelConfigData",
                        target_candidate_key=None if piece_id is None else {"table": "ReliquaryExcelConfigData", "native_id": str(piece_id)},
                        status=status,
                        target_container_coordinate=target_coord,
                        ambiguity={},
                    )
                )

    # Relationships from ReliquaryCodexExcelConfigData
    for idx, row in enumerate(tables_data["ReliquaryCodexExcelConfigData"]):
        suit_id = _positive_int(row.get("suitId"))
        target = indexes["ReliquarySetExcelConfigData"].get(str(suit_id))
        target_coord = None if target is None else (TABLE_FILES["ReliquarySetExcelConfigData"], f"/{target[0]}")
        status = "resolved" if target else "dangling" if suit_id is not None else "unresolved"
        relationships.append(
            Phase4B5Relationship(
                family=family,
                source_relative_path=TABLE_FILES["ReliquaryCodexExcelConfigData"],
                source_record_pointer=f"/{idx}",
                source_family="ReliquaryCodexExcelConfigData",
                source_pointer=f"/{idx}/suitId",
                relation_kind="reliquary_codex_set_candidate",
                field_role="foreign_pointer_candidate",
                raw_value=row.get("suitId"),
                target_family="ReliquarySetExcelConfigData",
                target_candidate_key=None if suit_id is None else {"table": "ReliquarySetExcelConfigData", "native_id": str(suit_id)},
                status=status,
                target_container_coordinate=target_coord,
                ambiguity={},
            )
        )

        for slot_field in CODEX_SLOT_FIELDS:
            if slot_field not in row:
                relationships.append(
                    Phase4B5Relationship(
                        family=family,
                        source_relative_path=TABLE_FILES["ReliquaryCodexExcelConfigData"],
                        source_record_pointer=f"/{idx}",
                        source_family="ReliquaryCodexExcelConfigData",
                        source_pointer=f"/{idx}/{slot_field}",
                        relation_kind="reliquary_codex_piece_candidate",
                        field_role="foreign_pointer_candidate",
                        raw_value=None,
                        target_family="ReliquaryExcelConfigData",
                        target_candidate_key=None,
                        status="unresolved",
                        target_container_coordinate=None,
                        ambiguity={},
                    )
                )
                continue
            piece_id = _positive_int(row.get(slot_field))
            target = indexes["ReliquaryExcelConfigData"].get(str(piece_id))
            target_coord = None if target is None else (TABLE_FILES["ReliquaryExcelConfigData"], f"/{target[0]}")
            status = "resolved" if target else "dangling" if piece_id is not None else "unresolved"
            relationships.append(
                Phase4B5Relationship(
                    family=family,
                    source_relative_path=TABLE_FILES["ReliquaryCodexExcelConfigData"],
                    source_record_pointer=f"/{idx}",
                    source_family="ReliquaryCodexExcelConfigData",
                    source_pointer=f"/{idx}/{slot_field}",
                    relation_kind="reliquary_codex_piece_candidate",
                    field_role="foreign_pointer_candidate",
                    raw_value=row.get(slot_field),
                    target_family="ReliquaryExcelConfigData",
                    target_candidate_key=None if piece_id is None else {"table": "ReliquaryExcelConfigData", "native_id": str(piece_id)},
                    status=status,
                    target_container_coordinate=target_coord,
                    ambiguity={},
                )
            )

    # Relationships and selected Documents from ReliquaryExcelConfigData
    selected_doc_indices: set[int] = set()
    for idx, row in enumerate(tables_data["ReliquaryExcelConfigData"]):
        # setId
        if "setId" not in row:
            relationships.append(
                Phase4B5Relationship(
                    family=family,
                    source_relative_path=TABLE_FILES["ReliquaryExcelConfigData"],
                    source_record_pointer=f"/{idx}",
                    source_family="ReliquaryExcelConfigData",
                    source_pointer=f"/{idx}/setId",
                    relation_kind="reliquary_piece_set_candidate",
                    field_role="foreign_pointer_candidate",
                    raw_value=None,
                    target_family="ReliquarySetExcelConfigData",
                    target_candidate_key=None,
                    status="unresolved",
                    target_container_coordinate=None,
                    ambiguity={},
                )
            )
        else:
            set_id = _positive_int(row.get("setId"))
            target = indexes["ReliquarySetExcelConfigData"].get(str(set_id))
            target_coord = None if target is None else (TABLE_FILES["ReliquarySetExcelConfigData"], f"/{target[0]}")
            status = "resolved" if target else "dangling" if set_id is not None else "unresolved"
            relationships.append(
                Phase4B5Relationship(
                    family=family,
                    source_relative_path=TABLE_FILES["ReliquaryExcelConfigData"],
                    source_record_pointer=f"/{idx}",
                    source_family="ReliquaryExcelConfigData",
                    source_pointer=f"/{idx}/setId",
                    relation_kind="reliquary_piece_set_candidate",
                    field_role="foreign_pointer_candidate",
                    raw_value=row.get("setId"),
                    target_family="ReliquarySetExcelConfigData",
                    target_candidate_key=None if set_id is None else {"table": "ReliquarySetExcelConfigData", "native_id": str(set_id)},
                    status=status,
                    target_container_coordinate=target_coord,
                    ambiguity={},
                )
            )

        # storyId
        if "storyId" not in row:
            relationships.append(
                Phase4B5Relationship(
                    family=family,
                    source_relative_path=TABLE_FILES["ReliquaryExcelConfigData"],
                    source_record_pointer=f"/{idx}",
                    source_family="ReliquaryExcelConfigData",
                    source_pointer=f"/{idx}/storyId",
                    relation_kind="reliquary_piece_story_document_candidate",
                    field_role="foreign_pointer_candidate",
                    raw_value=None,
                    target_family="DocumentExcelConfigData",
                    target_candidate_key=None,
                    status="unresolved",
                    target_container_coordinate=None,
                    ambiguity={},
                )
            )
        else:
            story_id = _positive_int(row.get("storyId"))
            target = indexes["DocumentExcelConfigData"].get(str(story_id))
            if target is None:
                target_coord = None
                status = "dangling" if story_id is not None else "unresolved"
            else:
                target_index, _ = target
                selected_doc_indices.add(target_index)
                target_coord = (TABLE_FILES["DocumentExcelConfigData"], f"/{target_index}")
                status = "resolved"
            relationships.append(
                Phase4B5Relationship(
                    family=family,
                    source_relative_path=TABLE_FILES["ReliquaryExcelConfigData"],
                    source_record_pointer=f"/{idx}",
                    source_family="ReliquaryExcelConfigData",
                    source_pointer=f"/{idx}/storyId",
                    relation_kind="reliquary_piece_story_document_candidate",
                    field_role="foreign_pointer_candidate",
                    raw_value=row.get("storyId"),
                    target_family="DocumentExcelConfigData",
                    target_candidate_key=None if story_id is None else {"table": "DocumentExcelConfigData", "native_id": str(story_id)},
                    status=status,
                    target_container_coordinate=target_coord,
                    ambiguity={},
                )
            )

    # 2. Selected Document containers and relationships
    selected_loc_indices: set[int] = set()
    for doc_idx in sorted(selected_doc_indices):
        doc_row = tables_data["DocumentExcelConfigData"][doc_idx]
        native_id = str(doc_row["id"])
        cand_key = {"table": "DocumentExcelConfigData", "native_id": native_id}
        containers.append(
            Phase4B5Container(
                family=family,
                relative_path=TABLE_FILES["DocumentExcelConfigData"],
                record_pointer=f"/{doc_idx}",
                record_kind="static_excel_row",
                native_id=native_id,
                container_kind="excel_row",
                source_family="DocumentExcelConfigData",
                locale=None,
                candidate_namespace="agd.static.DocumentExcelConfigData",
                candidate_key=cand_key,
                raw_value=doc_row,
                raw_json=canonical_json(doc_row),
                metadata={
                    "family": family,
                    "manifest_id": manifest_id,
                    "manifest_raw_sha256": manifest_sha,
                },
            )
        )

        raw_candidates = doc_row.get("questIDList")
        values = raw_candidates if isinstance(raw_candidates, list) else []
        candidate_ids = [_positive_int(val) for val in values]
        candidate_ids = [val for val in candidate_ids if val is not None]
        concrete: list[dict[str, Any]] = []
        for val in candidate_ids:
            target = indexes["LocalizationExcelConfigData"].get(str(val))
            if target is not None:
                loc_target_idx, _ = target
                selected_loc_indices.add(loc_target_idx)
                concrete.append(
                    {
                        "table": "LocalizationExcelConfigData",
                        "pointer": f"/{loc_target_idx}",
                        "native_id": str(val),
                    }
                )
        if not candidate_ids:
            status, target_key, target_coord, ambiguity = "unresolved", None, None, {}
        elif len(candidate_ids) == 1 and len(concrete) == 1:
            status = "resolved"
            target_key = {"table": "LocalizationExcelConfigData", "native_id": str(candidate_ids[0])}
            target_coord = (TABLE_FILES["LocalizationExcelConfigData"], concrete[0]["pointer"])
            ambiguity = {}
        elif len(candidate_ids) == 1:
            status = "dangling"
            target_key = {"table": "LocalizationExcelConfigData", "native_id": str(candidate_ids[0])}
            target_coord, ambiguity = None, {}
        else:
            status, target_key, target_coord = "ambiguous", None, None
            ambiguity = {
                "rule": "questIDList-localization-candidate-set-v1",
                "candidates": [
                    {
                        "native_id": str(v),
                        "record_pointer": None
                        if indexes["LocalizationExcelConfigData"].get(str(v)) is None
                        else f"/{indexes['LocalizationExcelConfigData'][str(v)][0]}",
                    }
                    for v in candidate_ids
                ],
            }
        relationships.append(
            Phase4B5Relationship(
                family=family,
                source_relative_path=TABLE_FILES["DocumentExcelConfigData"],
                source_record_pointer=f"/{doc_idx}",
                source_family="DocumentExcelConfigData",
                source_pointer=f"/{doc_idx}/questIDList",
                relation_kind="document_localization_implicit_candidate_set",
                field_role="unresolved_field_role",
                raw_value=raw_candidates,
                target_family="LocalizationExcelConfigData",
                target_candidate_key=target_key,
                status=status,
                target_container_coordinate=target_coord,
                ambiguity=ambiguity,
            )
        )

    # 3. Selected Localization containers and relationships
    readable_paths_set: set[str] = set()
    for loc_idx in sorted(selected_loc_indices):
        loc_row = tables_data["LocalizationExcelConfigData"][loc_idx]
        native_id = str(loc_row["id"])
        cand_key = {"table": "LocalizationExcelConfigData", "native_id": native_id}
        containers.append(
            Phase4B5Container(
                family=family,
                relative_path=TABLE_FILES["LocalizationExcelConfigData"],
                record_pointer=f"/{loc_idx}",
                record_kind="static_excel_row",
                native_id=native_id,
                container_kind="excel_row",
                source_family="LocalizationExcelConfigData",
                locale=None,
                candidate_namespace="agd.static.LocalizationExcelConfigData",
                candidate_key=cand_key,
                raw_value=loc_row,
                raw_json=canonical_json(loc_row),
                metadata={
                    "family": family,
                    "manifest_id": manifest_id,
                    "manifest_raw_sha256": manifest_sha,
                },
            )
        )

        for field, (loc_locale, field_role) in LOCALIZATION_FIELDS.items():
            raw_val = loc_row.get(field)
            candidate_path: str | None = None
            if isinstance(raw_val, str):
                prefix = f"ART/UI/Readable/{loc_locale}/"
                if raw_val.startswith(prefix) and len(raw_val) > len(prefix):
                    candidate_path = "Readable/" + raw_val[len("ART/UI/Readable/") :] + ".txt"
            if candidate_path is None:
                status, target_key, target_coord = "unresolved", None, None
            elif candidate_path in file_map:
                readable_paths_set.add(candidate_path)
                status = "resolved"
                target_key = {"locale": loc_locale, "relative_path": candidate_path}
                target_coord = (candidate_path, "/text")
            else:
                status = "dangling"
                target_key = {"locale": loc_locale, "relative_path": candidate_path}
                target_coord = None
            relationships.append(
                Phase4B5Relationship(
                    family=family,
                    source_relative_path=TABLE_FILES["LocalizationExcelConfigData"],
                    source_record_pointer=f"/{loc_idx}",
                    source_family="LocalizationExcelConfigData",
                    source_pointer=f"/{loc_idx}/{field}",
                    relation_kind="localization_readable_candidate",
                    field_role=field_role,
                    raw_value=raw_val,
                    target_family="Readable",
                    target_candidate_key=target_key,
                    status=status,
                    target_container_coordinate=target_coord,
                    ambiguity={},
                )
            )

    # 4. Readable containers and segments
    segments: list[Phase4B5Segment] = []
    for rel_path in sorted(readable_paths_set):
        r_file = file_map[rel_path]
        r_locale = rel_path.split("/")[1]
        stem = Path(rel_path).stem
        classification = classify_phase4b5_readable(r_file.raw)
        containers.append(
            Phase4B5Container(
                family=family,
                relative_path=rel_path,
                record_pointer="/text",
                record_kind="static_readable_file",
                native_id=stem,
                container_kind="readable_file",
                source_family="Readable",
                locale=r_locale,
                candidate_namespace=f"agd.static.Readable.{r_locale}",
                candidate_key={"locale": r_locale, "stem": stem},
                raw_value=r_file.raw.decode("utf-8"),
                raw_json=canonical_json(r_file.raw.decode("utf-8")),
                metadata={
                    "family": family,
                    "classification": classification,
                    "cross_language_alignment": "unresolved",
                    "manifest_id": manifest_id,
                    "manifest_raw_sha256": manifest_sha,
                },
            )
        )
        if classification == "narrative":
            local_segs = _line_segments(r_file.raw)
            for seg in local_segs:
                segments.append(
                    Phase4B5Segment(
                        family=family,
                        relative_path=rel_path,
                        record_pointer="/text",
                        locale=r_locale,
                        source_pointer=f"/lines/{seg['file_local_ordinal']}",
                        file_local_ordinal=seg["file_local_ordinal"],
                        byte_start=seg["byte_start"],
                        byte_end=seg["byte_end"],
                        character_start=seg["character_start"],
                        character_end=seg["character_end"],
                        raw_text=seg["raw_text"],
                        normalized_text=seg["normalized_text"],
                        raw_checksum=seg["raw_checksum"],
                        normalized_checksum=seg["normalized_checksum"],
                        segmentation_version=manifest.versions["segmentation"],
                    )
                )

    # 5. TextMap relationships, resolutions, and candidates
    for table_name, fields in (
        ("ReliquaryExcelConfigData", ("nameTextMapHash", "descTextMapHash")),
        ("DocumentExcelConfigData", ("titleTextMapHash",)),
    ):
        rows_to_check = (
            enumerate(tables_data[table_name])
            if table_name == "ReliquaryExcelConfigData"
            else ((doc_idx, tables_data[table_name][doc_idx]) for doc_idx in sorted(selected_doc_indices))
        )
        for idx, row in rows_to_check:
            for field in fields:
                val = _positive_int(row.get(field))
                if val is None:
                    continue
                text_owners.append(
                    {
                        "family": family,
                        "source_table": table_name,
                        "source_record_pointer": f"/{idx}",
                        "source_pointer": f"/{idx}/{field}",
                        "field": field,
                        "textmap_hash": str(val),
                    }
                )

    text_resolutions: list[Phase4B5TextResolution] = []
    text_candidates: list[Phase4B5TextCandidate] = []

    for owner in text_owners:
        h = owner["textmap_hash"]
        for locale in LOCALES:
            main_path = manifest.boundaries["textmap_shards"][locale]["main"]
            med_path = manifest.boundaries["textmap_shards"][locale]["medium"]
            main_map = file_map[main_path].parsed
            med_map = file_map[med_path].parsed
            main_text = main_map.get(h)
            med_text = med_map.get(h)
            resolved = resolve_phase4b5_textmap_occurrence(main_text, med_text)
            text_resolutions.append(
                Phase4B5TextResolution(
                    family=family,
                    owning_source_relative_path=TABLE_FILES[owner["source_table"]],
                    owning_source_record_pointer=owner["source_record_pointer"],
                    relationship_source_pointer=owner["source_pointer"],
                    locale=locale,
                    textmap_hash=h,
                    status=resolved.status,
                    raw_text=resolved.raw_text,
                    normalized_text=resolved.normalized_text,
                    raw_checksum=resolved.raw_checksum,
                    normalized_checksum=resolved.normalized_checksum,
                )
            )
            for cand in resolved.candidates:
                shard_kind = cand["shard_kind"]
                raw_t = cand["raw_text"]
                norm_t = normalize_text(raw_t)
                cand_path = main_path if shard_kind == "main" else med_path
                text_candidates.append(
                    Phase4B5TextCandidate(
                        family=family,
                        owning_source_relative_path=TABLE_FILES[owner["source_table"]],
                        owning_source_record_pointer=owner["source_record_pointer"],
                        relationship_source_pointer=owner["source_pointer"],
                        locale=locale,
                        textmap_hash=h,
                        status=resolved.status,
                        shard_kind=shard_kind,
                        source_relative_path=cand_path,
                        source_record_pointer=f"/{h}",
                        raw_text=raw_t,
                        normalized_text=norm_t,
                        raw_checksum=sha256_text(raw_t),
                        normalized_checksum=sha256_text(norm_t),
                    )
                )

        relationships.append(
            Phase4B5Relationship(
                family=family,
                source_relative_path=TABLE_FILES[owner["source_table"]],
                source_record_pointer=owner["source_record_pointer"],
                source_family=owner["source_table"],
                source_pointer=owner["source_pointer"],
                relation_kind=TEXT_RELATION_KINDS[(owner["source_table"], owner["field"])],
                field_role="verified_textmap_reference",
                raw_value=int(owner["textmap_hash"]),
                target_family="TextMap",
                target_candidate_key={"textmap_hash": owner["textmap_hash"]},
                status="resolved",
                target_container_coordinate=None,
                ambiguity={},
            )
        )

    # Build Source Records
    source_records: list[Phase4B5SourceRecord] = []
    for c in containers:
        source_records.append(
            Phase4B5SourceRecord(
                relative_path=c.relative_path,
                pointer=c.record_pointer,
                record_kind=c.record_kind,
                native_id=c.native_id,
                raw_sha256=sha256_bytes(c.raw_value.encode("utf-8") if isinstance(c.raw_value, str) else canonical_json(c.raw_value).encode("utf-8")),
                raw_json=c.raw_json,
                raw_value=c.raw_value,
            )
        )

    distinct_text_sources: set[tuple[str, str, str, str]] = set()
    for cand in text_candidates:
        distinct_text_sources.add(
            (
                cand.source_relative_path,
                cand.source_record_pointer,
                cand.textmap_hash,
                cand.raw_text,
            )
        )

    for src_path, src_ptr, native_id, raw_text in sorted(distinct_text_sources):
        source_records.append(
            Phase4B5SourceRecord(
                relative_path=src_path,
                pointer=src_ptr,
                record_kind="static_textmap_entry",
                native_id=native_id,
                raw_sha256=sha256_text(raw_text),
                raw_json=canonical_json(raw_text),
                raw_value=raw_text,
            )
        )

    # Calculate digests
    raw_files_dict = [
        {"relative_path": spec.relative_path, "format": spec.format, "size_bytes": spec.size_bytes, "sha256": spec.sha256}
        for spec in manifest.files
    ]
    raw_src_records_dict = [
        {"source_path": r.relative_path, "pointer": r.pointer, "record_kind": r.record_kind, "native_id": r.native_id, "raw_sha256": r.raw_sha256, "raw_json": r.raw_json}
        for r in source_records
    ]
    raw_containers_dict = [
        {"family": c.family, "source_path": c.relative_path, "record_pointer": c.record_pointer, "source_family": c.source_family, "locale": c.locale, "native_id": c.native_id}
        for c in containers
    ]
    raw_segments_dict = [
        {
            "family": s.family, "source_path": s.relative_path, "locale": s.locale,
            "file_local_ordinal": s.file_local_ordinal, "byte_start": s.byte_start, "byte_end": s.byte_end,
            "character_start": s.character_start, "character_end": s.character_end,
            "raw_text": s.raw_text, "normalized_text": s.normalized_text,
            "raw_checksum": s.raw_checksum, "normalized_checksum": s.normalized_checksum,
        }
        for s in segments
    ]
    raw_rel_dict = [
        {
            "family": rel.family, "source_table": rel.source_family, "source_record_pointer": rel.source_record_pointer,
            "source_pointer": rel.source_pointer, "relation_kind": rel.relation_kind, "field_role": rel.field_role,
            "raw_value": rel.raw_value, "target_family": rel.target_family, "target_candidate_key": rel.target_candidate_key,
            "status": rel.status,
            "target_record": None if rel.target_container_coordinate is None else (
                {"family": "Readable", "relative_path": rel.target_container_coordinate[0], "pointer": rel.target_container_coordinate[1]}
                if rel.target_family == "Readable"
                else {"table": rel.target_family, "pointer": rel.target_container_coordinate[1]}
            ),
            "ambiguity": rel.ambiguity,
        }
        for rel in relationships
    ]
    raw_res_dict = [
        {
            "family": res.family, "source_table": res.owning_source_relative_path.split("/")[-1].replace(".json", ""),
            "source_record_pointer": res.owning_source_record_pointer, "source_pointer": res.relationship_source_pointer,
            "field": res.relationship_source_pointer.split("/")[-1], "textmap_hash": res.textmap_hash,
            "locale": res.locale, "status": res.status, "raw_text": res.raw_text, "normalized_text": res.normalized_text,
            "raw_checksum": res.raw_checksum, "normalized_checksum": res.normalized_checksum,
        }
        for res in text_resolutions
    ]
    raw_cand_dict = [
        {
            "family": c.family, "source_table": c.owning_source_relative_path.split("/")[-1].replace(".json", ""),
            "source_record_pointer": c.owning_source_record_pointer, "source_pointer": c.relationship_source_pointer,
            "field": c.relationship_source_pointer.split("/")[-1], "textmap_hash": c.textmap_hash,
            "locale": c.locale, "status": c.status, "shard_kind": c.shard_kind,
            "source_path": c.source_relative_path, "source_pointer": c.source_record_pointer,
            "raw_text": c.raw_text, "normalized_text": c.normalized_text,
            "raw_checksum": c.raw_checksum, "normalized_checksum": c.normalized_checksum,
        }
        for c in text_candidates
    ]
    raw_assets_dict = [
        {
            "source_path": src_path, "source_pointer": src_ptr,
            "locale": "CHS" if "CHS" in src_path else "EN" if "EN" in src_path else "JP",
            "shard_kind": "medium" if "Medium" in src_path else "main",
            "textmap_hash": native_id, "raw_checksum": sha256_text(raw_text),
        }
        for src_path, src_ptr, native_id, raw_text in sorted(distinct_text_sources)
    ]

    occurrence_set_digests = {
        "files": _set_digest(raw_files_dict),
        "source_records": _set_digest(raw_src_records_dict),
        "containers": _set_digest(raw_containers_dict),
        "segments": _set_digest(raw_segments_dict),
        "relationships": _set_digest(raw_rel_dict),
        "text_resolutions": _set_digest(raw_res_dict),
        "text_candidates": _set_digest(raw_cand_dict),
        "text_assets": _set_digest(raw_assets_dict),
    }

    rel_counts = Counter(r.status for r in relationships)
    res_counts = Counter(r.status for r in text_resolutions)
    status_counts = {
        "relationships": {
            status: rel_counts[status]
            for status in ("resolved", "ambiguous", "unresolved", "dangling")
        },
        "text_resolutions": {
            status: res_counts[status]
            for status in ("unique_main", "unique_medium", "both_equal", "both_conflict", "missing")
        },
    }

    family_counts = {
        family: {
            "containers": len(containers),
            "segments": len(segments),
            "relationships": len(relationships),
            "text_resolutions": len(text_resolutions),
            "text_candidates": len(text_candidates),
        }
    }

    family_selected_paths = {
        family: sorted({spec.relative_path for spec in manifest.files})
    }

    snapshot_id_val = digest_id(
        "snap",
        canonical_json(
            {
                "commit": manifest.source["commit"],
                "tree": manifest.source["tree"],
                "build": manifest.source["build"],
                "release": manifest.source["release_label"],
            }
        ),
    )
    parser_build_id = digest_id(
        "pb-static-phase4b5",
        canonical_json(
            {
                "versions": manifest.versions,
                "manifest_id": manifest.manifest_id,
            }
        ),
    )
    config_checksum = sha256_text(
        canonical_json(
            {
                "manifest_id": manifest.manifest_id,
                "manifest_raw_sha256": manifest.raw_sha256,
                "occurrence_set_digests": occurrence_set_digests,
            }
        )
    )
    ingest_run_id = digest_id(
        "ir-static-phase4b5",
        canonical_json(
            {
                "snapshot_id": snapshot_id_val,
                "parser_build_id": parser_build_id,
                "config_checksum": config_checksum,
            }
        ),
    )

    return Phase4B5PreflightResult(
        manifest=manifest,
        source_root=root,
        snapshot_id=snapshot_id_val,
        parser_build_id=parser_build_id,
        ingest_run_id=ingest_run_id,
        config_checksum=config_checksum,
        files=ordered_files,
        source_records=tuple(source_records),
        containers=tuple(containers),
        segments=tuple(segments),
        relationships=tuple(relationships),
        text_resolutions=tuple(text_resolutions),
        text_candidates=tuple(text_candidates),
        status_counts=status_counts,
        family_counts=family_counts,
        occurrence_set_digests=occurrence_set_digests,
        family_selected_paths=family_selected_paths,
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


def _db_record_kind(kind: str) -> str:
    return {
        "static_excel_row": "excel_row",
        "static_readable_file": "readable_file",
        "static_textmap_entry": "textmap_entry",
    }.get(kind, kind)


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


def _write_occurrences(
    conn: sqlite3.Connection,
    preflight: Phase4B5PreflightResult,
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
        "static-phase4b5-sealed-reliquary-corpus", preflight.config_checksum, source["observed_at"],
    )
    ensure(
        "parser_build", ("id", "parser_version", "mapping_version", "fingerprint", "config_checksum", "created_at"), parser_values,
    )
    run_values = (
        preflight.ingest_run_id, preflight.snapshot_id, preflight.parser_build_id,
        canonical_json({"family": "static_phase4b5_reliquary_corpus", "manifest_id": preflight.manifest.manifest_id, "selected_paths": [item.relative_path for item in preflight.files]}),
        canonical_json({"manifest_raw_sha256": preflight.manifest.raw_sha256, "versions": preflight.manifest.versions, "full_static_scan": False, "logical_identity_created": False, "cross_language_alignment_created": False, "textmap_evidence_container_created": False}),
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
        db_raw_json = (
            canonical_json({"text": item.raw_value})
            if item.record_kind == "static_readable_file"
            else item.raw_json
        )
        ensure(
            "source_record",
            ("id", "source_file_id", "pointer", "record_kind", "native_id", "raw_sha256", "raw_json"),
            (
                record_id, file_id, item.pointer, _db_record_kind(item.record_kind), item.native_id,
                item.raw_sha256, db_raw_json,
            ),
        )

    container_ids: dict[tuple[str, str], str] = {}
    for item in preflight.containers:
        container_id = digest_id(
            "static-container", preflight.snapshot_id, preflight.manifest.raw_sha256,
            preflight.ingest_run_id, item.relative_path, item.record_pointer,
        )
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
        relationship_id = digest_id(
            "static-relationship", preflight.snapshot_id, preflight.manifest.raw_sha256,
            preflight.ingest_run_id, item.source_relative_path, item.source_record_pointer, item.source_pointer,
        )
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
        resolution_id = digest_id(
            "static-text-resolution", preflight.snapshot_id, preflight.manifest.raw_sha256,
            preflight.ingest_run_id, relationship_id, item.locale,
        )
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
        candidate_id = digest_id(
            "static-text-candidate", preflight.snapshot_id, preflight.manifest.raw_sha256,
            preflight.ingest_run_id, resolution_id, item.source_relative_path,
            item.source_record_pointer, item.shard_kind,
        )
        ensure(
            "static_text_candidate_occurrence",
            (
                "id", "resolution_occurrence_id", "ingest_run_id", "snapshot_id", "source_file_id",
                "source_record_id", "source_pointer", "shard_kind", "locale", "resolution_status",
                "raw_text", "normalized_text", "raw_checksum", "normalized_checksum", "metadata_json",
            ),
            (
                candidate_id, resolution_id, preflight.ingest_run_id, preflight.snapshot_id,
                file_ids[item.source_relative_path], record_ids[(item.source_relative_path, item.source_record_pointer)],
                item.source_record_pointer, item.shard_kind, item.locale, item.status,
                item.raw_text, item.normalized_text, item.raw_checksum, item.normalized_checksum,
                canonical_json({
                    "family": item.family,
                    "owning_static_source": {"relative_path": owner[0], "record_pointer": owner[1], "source_pointer": owner[2]},
                }),
            ),
        )


def _schema9_readonly_check(target: Path) -> None:
    if not target.is_file():
        raise Phase4B5EvidenceError(f"existing schema-9 database is required: {target}")
    try:
        uri = target.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            conn.execute("PRAGMA query_only=ON")
            versions = [row[0] for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")]
            if versions != list(range(1, 10)):
                raise Phase4B5EvidenceError(f"database schema contract is not exactly 1..9: {versions}")
        finally:
            conn.close()
    except Phase4B5EvidenceError:
        raise
    except sqlite3.Error as exc:
        raise Phase4B5EvidenceError(f"cannot validate target schema: {exc}") from exc


def ingest_static_phase4b5_reliquary_evidence_bundle(
    db_path: str | Path,
    source_root: str | Path,
    manifest_input: StaticPhase4B5Manifest | str | Path,
    *,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    target_db = Path(db_path).resolve()
    _schema9_readonly_check(target_db)

    if report_path is not None:
        rep_p = Path(report_path).resolve()
        if rep_p == target_db:
            raise Phase4B5EvidenceError("database and build report must not resolve to the same file")
        if rep_p.exists():
            raise Phase4B5EvidenceError(f"build report already exists: {rep_p}")

    preflight = preflight_static_phase4b5_reliquary_evidence_bundle(source_root, manifest_input)
    manifest = preflight.manifest

    conn = sqlite3.connect(str(target_db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")

    try:
        if conn.execute("SELECT 1 FROM ingest_run WHERE id = ?", (preflight.ingest_run_id,)).fetchone() is not None:
            # Validate existing complete run
            row = conn.execute("SELECT status FROM ingest_run WHERE id = ?", (preflight.ingest_run_id,)).fetchone()
            if row is None or row["status"] != "complete":
                raise sqlite3.IntegrityError("existing Phase 4B5 run is incomplete")
            _write_occurrences(conn, preflight, insert_missing=False)
            expected_counts = {
                "containers": len(preflight.containers),
                "segments": len(preflight.segments),
                "relationships": len(preflight.relationships),
                "text_resolutions": len(preflight.text_resolutions),
                "text_candidates": len(preflight.text_candidates),
            }
            if _run_counts(conn, preflight.ingest_run_id) != expected_counts:
                raise sqlite3.IntegrityError("existing Phase 4B5 occurrence cardinality mismatch")
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise sqlite3.IntegrityError("existing Phase 4B5 database has foreign key violations")
            report = _build_report(target_db, preflight)
            if report_path is not None:
                rep_p.parent.mkdir(parents=True, exist_ok=True)
                rep_p.write_text(canonical_json(report) + "\n", encoding="utf-8")
            return report

        try:
            conn.execute("BEGIN")
            _write_occurrences(conn, preflight)
            expected_counts = {
                "containers": len(preflight.containers),
                "segments": len(preflight.segments),
                "relationships": len(preflight.relationships),
                "text_resolutions": len(preflight.text_resolutions),
                "text_candidates": len(preflight.text_candidates),
            }
            if _run_counts(conn, preflight.ingest_run_id) != expected_counts:
                raise sqlite3.IntegrityError("Phase 4B5 write cardinality mismatch")
            foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_keys:
                raise sqlite3.IntegrityError(f"Phase 4B5 foreign key check failed: {foreign_keys[:3]}")
            conn.execute(
                "UPDATE ingest_run SET status='complete' WHERE id=? AND status='running'",
                (preflight.ingest_run_id,),
            )
            if conn.execute("SELECT changes()").fetchone()[0] != 1:
                raise sqlite3.IntegrityError("Phase 4B5 run completion transition failed")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    except Phase4B5EvidenceError:
        raise
    except sqlite3.Error as exc:
        raise Phase4B5EvidenceError(str(exc)) from exc
    finally:
        conn.close()

    report = _build_report(target_db, preflight)
    if report_path is not None:
        rep_p.parent.mkdir(parents=True, exist_ok=True)
        rep_p.write_text(canonical_json(report) + "\n", encoding="utf-8")
    return report


def _build_report(db_path: Path, preflight: Phase4B5PreflightResult) -> dict[str, Any]:
    stat = db_path.stat()
    return {
        "status": "complete",
        "db_path": str(db_path.resolve()),
        "snapshot_id": preflight.snapshot_id,
        "parser_build_id": preflight.parser_build_id,
        "ingest_run_id": preflight.ingest_run_id,
        "config_checksum": preflight.config_checksum,
        "database_fingerprint": {
            "sha256": sha256_bytes(db_path.read_bytes()),
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        },
        "manifest": {
            "manifest_id": preflight.manifest.manifest_id,
            "raw_sha256": preflight.manifest.raw_sha256,
        },
        "counts": {
            "files": len(preflight.files),
            "source_records": len(preflight.source_records),
            "containers": len(preflight.containers),
            "segments": len(preflight.segments),
            "relationships": len(preflight.relationships),
            "text_resolutions": len(preflight.text_resolutions),
            "text_candidates": len(preflight.text_candidates),
        },
        "family_counts": preflight.family_counts,
        "family_selected_paths": preflight.family_selected_paths,
        "status_counts": preflight.status_counts,
        "locale_breakdown": preflight.manifest.expected["locale_breakdown"],
        "occurrence_set_digests": preflight.occurrence_set_digests,
        "scope": {
            "selected_paths": [spec.relative_path for spec in preflight.manifest.files],
            "full_static_scan": False,
            "fts_rebuilt": False,
            "logical_identity_created": False,
            "cross_language_alignment_created": False,
            "textmap_evidence_container_created": False,
        },
    }
