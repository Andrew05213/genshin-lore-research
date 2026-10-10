from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import Database
from .errors import ManifestError
from .phase4a_audit import segment_readable_occurrence, verify_source_snapshot
from .source import LocatedFile, SourceBundle, load_manifest, snapshot_id
from .static_phase4b2_manifest import (
    LOCALES,
    StaticPhase4B2FileSpec,
    StaticPhase4B2Manifest,
    load_static_phase4b2_manifest,
)
from .static_phase4b2_verification_contract import expected_phase4b2_identity
from .talk_textmap import parse_textmap_json_with_duplicates
from .util import canonical_json, digest_id, normalize_text, sha256_bytes, sha256_text


TABLE_FILES = {
    "WeaponExcelConfigData": "ExcelBinOutput/WeaponExcelConfigData.json",
    "WeaponCodexExcelConfigData": "ExcelBinOutput/WeaponCodexExcelConfigData.json",
    "DocumentExcelConfigData": "ExcelBinOutput/DocumentExcelConfigData.json",
    "LocalizationExcelConfigData": "ExcelBinOutput/LocalizationExcelConfigData.json",
    "ReliquarySetExcelConfigData": "ExcelBinOutput/ReliquarySetExcelConfigData.json",
    "ReliquaryCodexExcelConfigData": "ExcelBinOutput/ReliquaryCodexExcelConfigData.json",
    "ReliquaryExcelConfigData": "ExcelBinOutput/ReliquaryExcelConfigData.json",
    "MaterialExcelConfigData": "ExcelBinOutput/MaterialExcelConfigData.json",
    "MaterialCodexExcelConfigData": "ExcelBinOutput/MaterialCodexExcelConfigData.json",
}


@dataclass(frozen=True)
class PreflightStaticPhase4B2File:
    spec: StaticPhase4B2FileSpec
    located: LocatedFile
    raw: bytes
    parsed: Any
    duplicate_keys: frozenset[str]

    @property
    def relative_path(self) -> str:
        return self.spec.relative_path


@dataclass(frozen=True)
class PreflightStaticPhase4B2Container:
    family_key: str
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
    classification: str

    @property
    def coordinate(self) -> tuple[str, str]:
        return self.relative_path, self.record_pointer


@dataclass(frozen=True)
class PreflightStaticPhase4B2Segment:
    family_key: str
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


@dataclass(frozen=True)
class PreflightStaticPhase4B2Relationship:
    family_key: str
    source_relative_path: str
    source_record_pointer: str
    source_family: str
    source_pointer: str
    relation_kind: str
    field_role: str
    target_family: str
    target_candidate_key: dict[str, Any]
    candidate_resolution_status: str
    target_evidence_container_coordinate: tuple[str, str] | None
    raw_value: Any


@dataclass(frozen=True)
class PreflightStaticPhase4B2TextResolution:
    family_key: str
    owning_source_relative_path: str
    owning_source_record_pointer: str
    relationship_source_pointer: str
    locale: str
    textmap_hash: str
    resolution_status: str
    raw_text: str | None
    normalized_text: str | None
    raw_checksum: str | None
    normalized_checksum: str | None


@dataclass(frozen=True)
class PreflightStaticPhase4B2TextCandidate:
    family_key: str
    owning_source_relative_path: str
    owning_source_record_pointer: str
    relationship_source_pointer: str
    locale: str
    textmap_hash: str
    resolution_status: str
    shard_kind: str
    source_relative_path: str
    source_record_pointer: str
    raw_text: str
    normalized_text: str
    raw_checksum: str
    normalized_checksum: str


@dataclass(frozen=True)
class PreflightStaticPhase4B2Result:
    source_manifest: dict[str, Any]
    manifest: StaticPhase4B2Manifest
    snapshot_id: str
    parser_build_id: str
    ingest_run_id: str
    config_checksum: str
    files: tuple[PreflightStaticPhase4B2File, ...]
    containers: tuple[PreflightStaticPhase4B2Container, ...]
    segments: tuple[PreflightStaticPhase4B2Segment, ...]
    relationships: tuple[PreflightStaticPhase4B2Relationship, ...]
    text_resolutions: tuple[PreflightStaticPhase4B2TextResolution, ...]
    text_candidates: tuple[PreflightStaticPhase4B2TextCandidate, ...]


def _json_index(value: Any, pointer: str) -> Any:
    current = value
    if pointer == "":
        return current
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ManifestError(f"invalid JSON pointer: {pointer!r}")
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            if isinstance(current, list):
                current = current[int(part)]
            elif isinstance(current, dict):
                current = current[part]
            else:
                raise KeyError(part)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ManifestError(f"source pointer does not exist: {pointer}") from exc
    return current


def _verify_source_roots(
    source_manifest_path: str | Path,
    source_roots: list[str | Path],
) -> Any:
    if not source_roots:
        raise ManifestError("at least one Phase 4B2 source root is required")
    first_snapshot = None
    seen: set[Path] = set()
    for raw_root in source_roots:
        root = Path(raw_root).resolve()
        if root in seen:
            continue
        seen.add(root)
        try:
            verified = verify_source_snapshot(Path(source_manifest_path), root)
        except Exception as exc:
            raise ManifestError(
                f"source checkout pin/clean verification failed for {root}: {exc}"
            ) from exc
        if first_snapshot is None:
            first_snapshot = verified
    return first_snapshot


def _load_selected_files(
    manifest: StaticPhase4B2Manifest,
    source_roots: list[str | Path],
) -> list[PreflightStaticPhase4B2File]:
    bundle = SourceBundle(source_roots)
    result: list[PreflightStaticPhase4B2File] = []
    for spec in manifest.files:
        try:
            located, raw = bundle.read_bytes(spec.relative_path)
        except Exception as exc:
            raise ManifestError(
                f"cannot read sealed Phase 4B2 source {spec.relative_path}: {exc}"
            ) from exc
        if len(raw) != spec.size_bytes:
            raise ManifestError(
                f"source size mismatch for {spec.relative_path}: "
                f"actual={len(raw)} expected={spec.size_bytes}"
            )
        digest = sha256_bytes(raw)
        if digest != spec.sha256:
            raise ManifestError(
                f"source SHA-256 mismatch for {spec.relative_path}: "
                f"actual={digest} expected={spec.sha256}"
            )
        duplicates: frozenset[str] = frozenset()
        if spec.relative_path.startswith("TextMap/"):
            # File identity is sealed above. Defer the expensive JSON parse until
            # every owning aggregate relationship has independently validated.
            parsed = None
        elif spec.format == "json":
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ManifestError(f"invalid aggregate JSON {spec.relative_path}: {exc}") from exc
        else:
            try:
                parsed = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ManifestError(f"invalid UTF-8 Readable {spec.relative_path}: {exc}") from exc
        result.append(
            PreflightStaticPhase4B2File(
                spec=spec,
                located=located,
                raw=raw,
                parsed=parsed,
                duplicate_keys=duplicates,
            )
        )
    return result


class _ProjectionInventory:
    def __init__(
        self,
        manifest: StaticPhase4B2Manifest,
        files: list[PreflightStaticPhase4B2File],
        source_snapshot: Any,
    ) -> None:
        self.manifest = manifest
        self.files = {item.relative_path: item for item in files}
        self.source_snapshot = source_snapshot
        self.containers: list[PreflightStaticPhase4B2Container] = []
        self.segments: list[PreflightStaticPhase4B2Segment] = []
        self.relationships: list[PreflightStaticPhase4B2Relationship] = []
        self.text_resolutions: list[PreflightStaticPhase4B2TextResolution] = []
        self.text_candidates: list[PreflightStaticPhase4B2TextCandidate] = []
        self.by_coordinate: dict[tuple[str, str], PreflightStaticPhase4B2Container] = {}
        self.by_table_native: dict[tuple[str, str], PreflightStaticPhase4B2Container] = {}

    def add_excel(self, family_key: str, record: dict[str, Any]) -> PreflightStaticPhase4B2Container:
        table = str(record["table"])
        relative_path = str(record["relative_path"])
        expected_path = TABLE_FILES.get(table)
        if expected_path is None or relative_path != expected_path:
            raise ManifestError(
                f"table/file namespace mismatch: table={table!r} file={relative_path!r}"
            )
        source_file = self.files.get(relative_path)
        if source_file is None:
            raise ManifestError(f"selected aggregate is absent: {relative_path}")
        row = _json_index(source_file.parsed, str(record["pointer"]))
        if not isinstance(row, dict):
            raise ManifestError(f"selected aggregate row is not an object: {relative_path}#{record['pointer']}")
        native_field = str(record["native_field"])
        native_id = str(record["native_id"])
        if str(row.get(native_field)) != native_id:
            raise ManifestError(
                f"native/table scope mismatch at {relative_path}#{record['pointer']}: "
                f"{native_field}={row.get(native_field)!r} expected={native_id!r}"
            )
        item = PreflightStaticPhase4B2Container(
            family_key=family_key,
            relative_path=relative_path,
            record_pointer=str(record["pointer"]),
            record_kind="excel_row",
            native_id=native_id,
            container_kind="excel_row",
            source_family=table,
            locale=None,
            candidate_namespace=table,
            candidate_key={"table": table, "native_id": native_id},
            raw_value=row,
            raw_json=canonical_json(row),
            classification="raw_excel_row",
        )
        self._register_container(item)
        return item

    def add_readable(self, family_key: str, spec: dict[str, Any]) -> PreflightStaticPhase4B2Container:
        relative_path = str(spec["relative_path"])
        source_file = self.files.get(relative_path)
        if source_file is None or not isinstance(source_file.parsed, str):
            raise ManifestError(f"selected Readable is absent or invalid: {relative_path}")
        locale = str(spec["locale"])
        if locale not in LOCALES or relative_path.split("/")[1] != locale:
            raise ManifestError(f"Readable locale/path mismatch: {relative_path}")
        classification = str(spec["classification"])
        if classification == "placeholder_exact_test":
            if source_file.raw != b"test ":
                raise ManifestError("Book1257 CHS placeholder raw value mismatch")
        elif classification == "empty":
            if source_file.raw != b"":
                raise ManifestError(f"sealed empty Readable is not zero-byte: {relative_path}")
        elif classification == "narrative":
            if not source_file.raw:
                raise ManifestError(f"sealed narrative Readable is empty: {relative_path}")
        else:
            raise ManifestError(f"unsupported Readable classification: {classification}")
        item = PreflightStaticPhase4B2Container(
            family_key=family_key,
            relative_path=relative_path,
            record_pointer=str(spec["record_pointer"]),
            record_kind="readable_file",
            native_id=PureName(relative_path),
            container_kind="readable_file",
            source_family="Readable",
            locale=locale,
            candidate_namespace="Readable",
            candidate_key={"locale": locale, "relative_path": relative_path},
            raw_value=source_file.parsed,
            raw_json=canonical_json({"text": source_file.parsed}),
            classification=classification,
        )
        self._register_container(item)
        return item

    def _register_container(self, item: PreflightStaticPhase4B2Container) -> None:
        if item.coordinate in self.by_coordinate:
            raise ManifestError(f"duplicate selected source record coordinate: {item.coordinate}")
        self.by_coordinate[item.coordinate] = item
        if item.container_kind == "excel_row":
            key = (item.source_family, item.native_id)
            if key in self.by_table_native:
                raise ManifestError(f"duplicate selected table/native candidate: {key}")
            self.by_table_native[key] = item
        self.containers.append(item)

    def add_segments(
        self,
        container: PreflightStaticPhase4B2Container,
        expected_ordinals: list[int],
    ) -> None:
        if container.classification != "narrative":
            if expected_ordinals:
                raise ManifestError("placeholder/empty Readable cannot declare segments")
            return
        source_file = self.files[container.relative_path]
        occurrences = segment_readable_occurrence(
            raw=source_file.raw,
            relative_path=container.relative_path,
            locale=str(container.locale),
            snapshot=self.source_snapshot,
        )
        ordinals = [item.file_local_ordinal for item in occurrences]
        if ordinals != expected_ordinals:
            raise ManifestError(
                f"segmentation ordinal mismatch for {container.relative_path}: "
                f"actual={ordinals} expected={expected_ordinals}"
            )
        text = str(source_file.parsed)
        for occurrence in occurrences:
            raw_text = text[occurrence.character_start : occurrence.character_end]
            normalized_text = normalize_text(raw_text)
            if raw_text == "":
                raise ManifestError(f"empty narrative segment forbidden: {container.relative_path}")
            self.segments.append(
                PreflightStaticPhase4B2Segment(
                    family_key=container.family_key,
                    relative_path=container.relative_path,
                    record_pointer=container.record_pointer,
                    locale=str(container.locale),
                    source_pointer=f"/text/line/{occurrence.file_local_ordinal}",
                    file_local_ordinal=occurrence.file_local_ordinal,
                    byte_start=occurrence.byte_start,
                    byte_end=occurrence.byte_end,
                    character_start=occurrence.character_start,
                    character_end=occurrence.character_end,
                    raw_text=raw_text,
                    normalized_text=normalized_text,
                    raw_checksum=sha256_text(raw_text),
                    normalized_checksum=sha256_text(normalized_text),
                    segmentation_version=self.manifest.versions["segmentation"],
                )
            )

    def add_numeric_relationship(
        self,
        family_key: str,
        source: PreflightStaticPhase4B2Container,
        spec: dict[str, Any],
        *,
        relation_kind: str,
        source_field: str | None = None,
        source_pointer_suffix: str | None = None,
    ) -> None:
        if source_field is not None:
            source_pointer = f"{source.record_pointer}/{source_field}"
            raw_value = _json_index(source.raw_value, f"/{source_field}")
        elif source_pointer_suffix is not None:
            source_pointer = f"{source.record_pointer}{source_pointer_suffix}"
            raw_value = _json_index(source.raw_value, source_pointer_suffix)
        else:
            raise ManifestError("numeric relationship has no owning source pointer")
        if raw_value != spec.get("raw_value"):
            raise ManifestError(f"raw relationship value mismatch at {source_pointer}")
        target = spec.get("target")
        if not isinstance(target, dict):
            raise ManifestError(f"numeric relationship target missing at {source_pointer}")
        candidate = {
            "table": str(target.get("table")),
            "native_id": str(raw_value),
        }
        sealed_candidate = {
            "table": str(target.get("table")),
            "native_id": str(target.get("native_id")),
        }
        if candidate != sealed_candidate:
            raise ManifestError(
                f"raw relationship and table-qualified candidate disagree at {source_pointer}"
            )
        target_container = self.by_table_native.get(
            (candidate["table"], candidate["native_id"])
        )
        if (
            target_container is None
            or target_container.record_pointer != target.get("record_pointer")
        ):
            raise ManifestError(
                f"selected target record does not prove candidate at {source_pointer}"
            )
        self.relationships.append(
            PreflightStaticPhase4B2Relationship(
                family_key=family_key,
                source_relative_path=source.relative_path,
                source_record_pointer=source.record_pointer,
                source_family=source.source_family,
                source_pointer=source_pointer,
                relation_kind=relation_kind,
                field_role="foreign_pointer_candidate",
                target_family=target_container.source_family,
                target_candidate_key=candidate,
                candidate_resolution_status="resolved",
                target_evidence_container_coordinate=target_container.coordinate,
                raw_value=raw_value,
            )
        )

    def add_readable_relationship(
        self,
        family_key: str,
        source: PreflightStaticPhase4B2Container,
        field: str,
        spec: dict[str, Any],
    ) -> None:
        source_pointer = f"{source.record_pointer}/{field}"
        raw_value = _json_index(source.raw_value, f"/{field}")
        if raw_value != spec.get("raw_value") or not isinstance(raw_value, str):
            raise ManifestError(f"raw Readable relationship value mismatch at {source_pointer}")
        target = spec.get("target")
        if not isinstance(target, dict):
            raise ManifestError(f"Readable relationship target missing at {source_pointer}")
        prefix = "ART/UI/"
        if not raw_value.startswith(prefix):
            raise ManifestError(f"Readable raw asset path is invalid at {source_pointer}")
        derived_path = f"{raw_value[len(prefix):]}.txt"
        candidate = {
            "locale": str(target.get("locale")),
            "relative_path": derived_path,
        }
        sealed_candidate = {
            "locale": str(target.get("locale")),
            "relative_path": str(target.get("relative_path")),
        }
        if candidate != sealed_candidate:
            raise ManifestError(f"raw Readable path and sealed candidate disagree at {source_pointer}")
        target_container = self.by_coordinate.get((derived_path, str(target.get("record_pointer"))))
        if (
            target_container is None
            or target_container.source_family != "Readable"
            or target_container.locale != candidate["locale"]
            or target.get("family") != "Readable"
        ):
            raise ManifestError(f"selected Readable target does not prove candidate at {source_pointer}")
        self.relationships.append(
            PreflightStaticPhase4B2Relationship(
                family_key=family_key,
                source_relative_path=source.relative_path,
                source_record_pointer=source.record_pointer,
                source_family=source.source_family,
                source_pointer=source_pointer,
                relation_kind="localization_readable_candidate",
                field_role=("unknown_obfuscated" if field in {"DEFNEHAFMMA", "JDNBKKPEFAI"} else "unresolved_field_role"),
                target_family="Readable",
                target_candidate_key=candidate,
                candidate_resolution_status="resolved",
                target_evidence_container_coordinate=target_container.coordinate,
                raw_value=raw_value,
            )
        )

    def add_text_relationships(
        self,
        family_key: str,
        source: PreflightStaticPhase4B2Container,
        text_fields: dict[str, Any],
        *,
        relation_kind: str,
        verified: bool,
    ) -> None:
        for field, sealed_hash in text_fields.items():
            source_pointer = f"{source.record_pointer}/{field}"
            raw_value = _json_index(source.raw_value, f"/{field}")
            if raw_value != sealed_hash or type(raw_value) is not int:
                raise ManifestError(f"TextMap raw value mismatch at {source_pointer}")
            self.relationships.append(
                PreflightStaticPhase4B2Relationship(
                    family_key=family_key,
                    source_relative_path=source.relative_path,
                    source_record_pointer=source.record_pointer,
                    source_family=source.source_family,
                    source_pointer=source_pointer,
                    relation_kind=relation_kind,
                    field_role=("verified_textmap_reference" if verified else "unresolved_field_role"),
                    target_family="TextMap",
                    target_candidate_key={"textmap_hash": str(raw_value)},
                    candidate_resolution_status="resolved",
                    target_evidence_container_coordinate=None,
                    raw_value=raw_value,
                )
            )

    def hydrate_textmap(self) -> None:
        textmap_values: dict[str, dict[str, Any]] = {}
        textmap_duplicates: dict[str, frozenset[str]] = {}
        for locale in LOCALES:
            for shard_kind in ("main", "medium"):
                relative_path = str(
                    self.manifest.textmap["locales"][locale][shard_kind]
                )
                if relative_path in textmap_values:
                    continue
                source_file = self.files[relative_path]
                try:
                    parsed, duplicate_values = parse_textmap_json_with_duplicates(
                        source_file.raw
                    )
                except Exception as exc:
                    raise ManifestError(
                        f"invalid TextMap JSON {relative_path}: {exc}"
                    ) from exc
                textmap_values[relative_path] = parsed
                textmap_duplicates[relative_path] = frozenset(duplicate_values)
        status_by_hash: dict[str, str] = {}
        for status, hashes in self.manifest.textmap["hash_statuses"].items():
            for textmap_hash in hashes:
                status_by_hash[str(textmap_hash)] = str(status)
        text_relationships = [
            item for item in self.relationships if item.target_family == "TextMap"
        ]
        relationship_hashes = {
            str(item.target_candidate_key["textmap_hash"]) for item in text_relationships
        }
        if relationship_hashes != set(status_by_hash) or len(text_relationships) != 48:
            raise ManifestError(
                "owning TextMap relationship set does not match the sealed 48 hashes"
            )
        for relationship in text_relationships:
            textmap_hash = str(relationship.target_candidate_key["textmap_hash"])
            for locale in LOCALES:
                candidates: list[PreflightStaticPhase4B2TextCandidate] = []
                for shard_kind in ("main", "medium"):
                    relative_path = str(
                        self.manifest.textmap["locales"][locale][shard_kind]
                    )
                    source_file = self.files[relative_path]
                    if textmap_hash in textmap_duplicates[relative_path]:
                        raise ManifestError(
                            f"selected TextMap key is duplicated: {relative_path}#/{textmap_hash}"
                        )
                    parsed = textmap_values[relative_path]
                    if textmap_hash not in parsed:
                        continue
                    raw_text = parsed[textmap_hash]
                    if not isinstance(raw_text, str):
                        raise ManifestError(
                            f"selected TextMap value is not text: {relative_path}#/{textmap_hash}"
                        )
                    normalized_text = normalize_text(raw_text)
                    candidates.append(
                        PreflightStaticPhase4B2TextCandidate(
                            family_key=relationship.family_key,
                            owning_source_relative_path=relationship.source_relative_path,
                            owning_source_record_pointer=relationship.source_record_pointer,
                            relationship_source_pointer=relationship.source_pointer,
                            locale=locale,
                            textmap_hash=textmap_hash,
                            resolution_status="",
                            shard_kind=shard_kind,
                            source_relative_path=relative_path,
                            source_record_pointer=f"/{textmap_hash}",
                            raw_text=raw_text,
                            normalized_text=normalized_text,
                            raw_checksum=sha256_text(raw_text),
                            normalized_checksum=sha256_text(normalized_text),
                        )
                    )
                if not candidates:
                    status = "missing"
                    resolved_raw = None
                    resolved_normalized = None
                    raw_checksum = None
                    normalized_checksum = None
                elif len(candidates) == 1:
                    status = f"unique_{candidates[0].shard_kind}"
                    resolved_raw = candidates[0].raw_text
                    resolved_normalized = candidates[0].normalized_text
                    raw_checksum = candidates[0].raw_checksum
                    normalized_checksum = candidates[0].normalized_checksum
                elif candidates[0].raw_text == candidates[1].raw_text:
                    status = "both_equal"
                    resolved_raw = candidates[0].raw_text
                    resolved_normalized = candidates[0].normalized_text
                    raw_checksum = candidates[0].raw_checksum
                    normalized_checksum = candidates[0].normalized_checksum
                else:
                    status = "both_conflict"
                    resolved_raw = None
                    resolved_normalized = None
                    raw_checksum = None
                    normalized_checksum = None
                if status != status_by_hash.get(textmap_hash):
                    raise ManifestError(
                        f"TextMap resolution status mismatch for {locale}/{textmap_hash}: "
                        f"actual={status} expected={status_by_hash.get(textmap_hash)}"
                    )
                self.text_resolutions.append(
                    PreflightStaticPhase4B2TextResolution(
                        family_key=relationship.family_key,
                        owning_source_relative_path=relationship.source_relative_path,
                        owning_source_record_pointer=relationship.source_record_pointer,
                        relationship_source_pointer=relationship.source_pointer,
                        locale=locale,
                        textmap_hash=textmap_hash,
                        resolution_status=status,
                        raw_text=resolved_raw,
                        normalized_text=resolved_normalized,
                        raw_checksum=raw_checksum,
                        normalized_checksum=normalized_checksum,
                    )
                )
                self.text_candidates.extend(
                    PreflightStaticPhase4B2TextCandidate(
                        **{
                            **candidate.__dict__,
                            "resolution_status": status,
                        }
                    )
                    for candidate in candidates
                )


def PureName(relative_path: str) -> str:
    return PurePathName(relative_path.rsplit("/", 1)[-1])


def PurePathName(filename: str) -> str:
    return filename[:-4] if filename.endswith(".txt") else filename


def _add_readables(
    inventory: _ProjectionInventory,
    family_key: str,
    specs: list[dict[str, Any]],
    ordinals: list[int],
) -> list[PreflightStaticPhase4B2Container]:
    result = []
    for spec in specs:
        container = inventory.add_readable(family_key, spec)
        inventory.add_segments(container, list(spec.get("segment_ordinals", ordinals)))
        result.append(container)
    return result


def _project_book1257(inventory: _ProjectionInventory) -> None:
    family = inventory.manifest.families["book1257"]
    _add_readables(inventory, "book1257", family["readables"], [])


def _project_weapon(inventory: _ProjectionInventory) -> None:
    family = inventory.manifest.families["weapon15516"]
    weapon = inventory.add_excel("weapon15516", family["weapon"]["record"])
    codex = inventory.add_excel("weapon15516", family["codex"]["record"])
    document = inventory.add_excel("weapon15516", family["document"]["record"])
    localization = inventory.add_excel("weapon15516", family["localization"]["record"])
    _add_readables(
        inventory,
        "weapon15516",
        family["readables"],
        family["segment_ordinals"],
    )
    inventory.add_numeric_relationship(
        "weapon15516",
        codex,
        family["codex"]["weapon_relationship"],
        relation_kind="weapon_codex_weapon_candidate",
        source_field="weaponId",
    )
    inventory.add_numeric_relationship(
        "weapon15516",
        weapon,
        family["weapon"]["story_relationship"],
        relation_kind="weapon_story_document_candidate",
        source_field="storyId",
    )
    inventory.add_numeric_relationship(
        "weapon15516",
        document,
        family["document"]["localization_relationship"],
        relation_kind="document_localization_candidate",
        source_pointer_suffix="/questIDList/0",
    )
    for field, spec in family["localization"]["readable_fields"].items():
        inventory.add_readable_relationship("weapon15516", localization, field, spec)
    inventory.add_text_relationships(
        "weapon15516",
        weapon,
        family["weapon"]["text_fields"],
        relation_kind="weapon_textmap_candidate",
        verified=True,
    )
    inventory.add_text_relationships(
        "weapon15516",
        document,
        family["document"]["text_fields"],
        relation_kind="document_title_textmap_candidate",
        verified=True,
    )


def _project_reliquary(inventory: _ProjectionInventory) -> None:
    family = inventory.manifest.families["reliquary15048"]
    set_container = inventory.add_excel("reliquary15048", family["set"]["record"])
    codex_containers = [
        inventory.add_excel("reliquary15048", item["record"])
        for item in family["codex"]
    ]
    piece_containers = [
        inventory.add_excel("reliquary15048", item["record"])
        for item in family["pieces"]
    ]
    story_containers: list[tuple[PreflightStaticPhase4B2Container, PreflightStaticPhase4B2Container]] = []
    for story in family["stories"]:
        document = inventory.add_excel("reliquary15048", story["document"]["record"])
        localization = inventory.add_excel("reliquary15048", story["localization"]["record"])
        _add_readables(
            inventory,
            "reliquary15048",
            story["readables"],
            story["segment_ordinals"],
        )
        story_containers.append((document, localization))

    for relationship in family["set"]["contains_relationships"]:
        inventory.add_numeric_relationship(
            "reliquary15048",
            set_container,
            relationship,
            relation_kind="reliquary_set_contains_candidate",
            source_pointer_suffix=f"/containsList/{relationship['index']}",
        )
    for spec, source in zip(family["codex"], codex_containers):
        inventory.add_numeric_relationship(
            "reliquary15048",
            source,
            spec["suit_relationship"],
            relation_kind="reliquary_codex_set_candidate",
            source_field="suitId",
        )
        for field, relationship in spec["slots"].items():
            inventory.add_numeric_relationship(
                "reliquary15048",
                source,
                relationship,
                relation_kind="reliquary_codex_piece_candidate",
                source_field=field,
            )
    for spec, source in zip(family["pieces"], piece_containers):
        inventory.add_numeric_relationship(
            "reliquary15048",
            source,
            spec["set_relationship"],
            relation_kind="reliquary_piece_set_candidate",
            source_field="setId",
        )
        inventory.add_numeric_relationship(
            "reliquary15048",
            source,
            spec["story_relationship"],
            relation_kind="reliquary_piece_story_document_candidate",
            source_field="storyId",
        )
        inventory.add_text_relationships(
            "reliquary15048",
            source,
            spec["text_fields"],
            relation_kind="reliquary_piece_textmap_candidate",
            verified=True,
        )
    for story, (document, localization) in zip(family["stories"], story_containers):
        inventory.add_numeric_relationship(
            "reliquary15048",
            document,
            story["document"]["localization_relationship"],
            relation_kind="document_localization_candidate",
            source_pointer_suffix="/questIDList/0",
        )
        for field, relationship in story["localization"]["readable_fields"].items():
            inventory.add_readable_relationship(
                "reliquary15048", localization, field, relationship
            )
        inventory.add_text_relationships(
            "reliquary15048",
            document,
            story["document"]["text_fields"],
            relation_kind="document_title_textmap_candidate",
            verified=True,
        )


def _project_materials(inventory: _ProjectionInventory) -> None:
    family = inventory.manifest.families["materials"]
    bases = [
        inventory.add_excel("materials", item["record"])
        for item in family["base"]
    ]
    codex = inventory.add_excel("materials", family["codex"]["record"])
    for spec, source in zip(family["base"], bases):
        inventory.add_text_relationships(
            "materials",
            source,
            spec["text_fields"],
            relation_kind="material_base_textmap_candidate",
            verified=True,
        )
    inventory.add_numeric_relationship(
        "materials",
        codex,
        family["codex"]["material_relationship"],
        relation_kind="material_codex_material_candidate",
        source_field="materialId",
    )
    inventory.add_text_relationships(
        "materials",
        codex,
        family["codex"]["text_fields"],
        relation_kind="material_codex_presentation_textmap_candidate",
        verified=False,
    )


def _validate_counts(inventory: _ProjectionInventory) -> None:
    actual = {
        "selected_files": len(inventory.files),
        "containers": len(inventory.containers),
        "segments": len(inventory.segments),
        "relationships": len(inventory.relationships),
        "text_resolutions": len(inventory.text_resolutions),
        "text_candidates": len(inventory.text_candidates),
    }
    if actual != inventory.manifest.expected["counts"]:
        raise ManifestError(
            f"Phase 4B2 preflight count mismatch: actual={actual} "
            f"expected={inventory.manifest.expected['counts']}"
        )
    status_counts = Counter(item.resolution_status for item in inventory.text_resolutions)
    if dict(status_counts) != {
        key: value
        for key, value in inventory.manifest.expected["resolution_statuses"].items()
        if value
    }:
        raise ManifestError(
            f"Phase 4B2 resolution status count mismatch: {dict(status_counts)}"
        )
    for family_key, expected in inventory.manifest.expected["families"].items():
        family_actual = {
            "containers": sum(item.family_key == family_key for item in inventory.containers),
            "segments": sum(item.family_key == family_key for item in inventory.segments),
            "relationships": sum(item.family_key == family_key for item in inventory.relationships),
            "text_resolutions": sum(item.family_key == family_key for item in inventory.text_resolutions),
            "text_candidates": sum(item.family_key == family_key for item in inventory.text_candidates),
        }
        if family_actual != expected:
            raise ManifestError(
                f"Phase 4B2 family count mismatch for {family_key}: "
                f"actual={family_actual} expected={expected}"
            )


def preflight_static_phase4b2_evidence_bundle(
    source_roots: list[str | Path],
    source_manifest_path: str | Path,
    phase4b2_manifest_path: str | Path,
) -> PreflightStaticPhase4B2Result:
    """Validate the complete sealed canary before any target path is touched."""
    source_manifest = load_manifest(source_manifest_path)
    manifest = load_static_phase4b2_manifest(phase4b2_manifest_path)
    for key in ("source_id", "release_label", "commit", "tree"):
        if str(source_manifest.get(key)) != manifest.source[key]:
            raise ManifestError(f"source manifest and Phase 4B2 manifest disagree on {key}")
    source_snapshot = _verify_source_roots(source_manifest_path, source_roots)
    files = _load_selected_files(manifest, source_roots)
    inventory = _ProjectionInventory(manifest, files, source_snapshot)
    _project_book1257(inventory)
    _project_weapon(inventory)
    _project_reliquary(inventory)
    _project_materials(inventory)
    inventory.hydrate_textmap()
    _validate_counts(inventory)

    identity = expected_phase4b2_identity(manifest)
    snapshot_id_value = identity["snapshot_id"]
    config_checksum = identity["config_checksum"]
    parser_build_id = identity["parser_build_id"]
    ingest_run_id = identity["ingest_run_id"]
    return PreflightStaticPhase4B2Result(
        source_manifest=dict(source_manifest),
        manifest=manifest,
        snapshot_id=snapshot_id_value,
        parser_build_id=parser_build_id,
        ingest_run_id=ingest_run_id,
        config_checksum=config_checksum,
        files=tuple(files),
        containers=tuple(inventory.containers),
        segments=tuple(inventory.segments),
        relationships=tuple(inventory.relationships),
        text_resolutions=tuple(inventory.text_resolutions),
        text_candidates=tuple(inventory.text_candidates),
    )


_B2_COUNT_KEYS = (
    "containers",
    "segments",
    "relationships",
    "text_resolutions",
    "text_candidates",
)


def _container_id(preflight: PreflightStaticPhase4B2Result, item: PreflightStaticPhase4B2Container) -> str:
    return digest_id(
        "static-container",
        preflight.snapshot_id,
        preflight.manifest.raw_sha256,
        preflight.ingest_run_id,
        item.relative_path,
        item.record_pointer,
    )


def _relationship_id(
    preflight: PreflightStaticPhase4B2Result,
    item: PreflightStaticPhase4B2Relationship,
) -> str:
    return digest_id(
        "static-relationship",
        preflight.snapshot_id,
        preflight.manifest.raw_sha256,
        preflight.ingest_run_id,
        item.source_relative_path,
        item.source_record_pointer,
        item.source_pointer,
    )


def _relationship_coordinate(item: PreflightStaticPhase4B2Relationship) -> tuple[str, str, str]:
    return item.source_relative_path, item.source_record_pointer, item.source_pointer


def _text_owner_coordinate(
    item: PreflightStaticPhase4B2TextResolution | PreflightStaticPhase4B2TextCandidate,
) -> tuple[str, str, str]:
    return (
        item.owning_source_relative_path,
        item.owning_source_record_pointer,
        item.relationship_source_pointer,
    )


def _resolution_id(
    preflight: PreflightStaticPhase4B2Result,
    relationship_id: str,
    item: PreflightStaticPhase4B2TextResolution,
) -> str:
    return digest_id(
        "static-text-resolution",
        preflight.snapshot_id,
        preflight.manifest.raw_sha256,
        preflight.ingest_run_id,
        relationship_id,
        item.locale,
    )


def _candidate_id(
    preflight: PreflightStaticPhase4B2Result,
    resolution_id: str,
    item: PreflightStaticPhase4B2TextCandidate,
) -> str:
    return digest_id(
        "static-text-candidate",
        preflight.snapshot_id,
        preflight.manifest.raw_sha256,
        preflight.ingest_run_id,
        resolution_id,
        item.source_relative_path,
        item.source_record_pointer,
        item.shard_kind,
    )


def _ensure_immutable_row(
    db: Database,
    table: str,
    columns: tuple[str, ...],
    values: tuple[Any, ...],
) -> None:
    row_id = values[0]
    existing = db.conn.execute(
        f"SELECT {','.join(columns)} FROM {table} WHERE id=?", (row_id,)
    ).fetchone()
    if existing is not None:
        actual = tuple(existing[column] for column in columns)
        if actual != values:
            raise sqlite3.IntegrityError(f"{table} immutable identity mismatch for {row_id}")
        return
    placeholders = ",".join("?" for _ in columns)
    db.conn.execute(
        f"INSERT INTO {table}({','.join(columns)}) VALUES ({placeholders})",
        values,
    )


def _insert_static_relationship_occurrence(db: Database, values: tuple[Any, ...]) -> None:
    _ensure_immutable_row(
        db,
        "static_relationship_occurrence",
        (
            "id", "ingest_run_id", "snapshot_id", "source_container_id",
            "source_record_id", "source_family", "source_pointer", "relation_kind",
            "field_role", "target_family", "target_candidate_key_json",
            "candidate_resolution_status", "target_evidence_container_id",
            "ambiguity_json", "metadata_json",
        ),
        values,
    )


def _insert_static_text_resolution_occurrence(db: Database, values: tuple[Any, ...]) -> None:
    _ensure_immutable_row(
        db,
        "static_text_resolution_occurrence",
        (
            "id", "ingest_run_id", "snapshot_id",
            "static_relationship_occurrence_id", "source_container_id",
            "source_record_id", "source_pointer", "locale", "textmap_hash",
            "resolution_status", "raw_text", "normalized_text", "raw_checksum",
            "normalized_checksum", "metadata_json",
        ),
        values,
    )


def _insert_static_text_candidate_occurrence(db: Database, values: tuple[Any, ...]) -> None:
    _ensure_immutable_row(
        db,
        "static_text_candidate_occurrence",
        (
            "id", "resolution_occurrence_id", "ingest_run_id", "snapshot_id",
            "source_file_id", "source_record_id", "source_pointer", "shard_kind",
            "locale", "resolution_status", "raw_text", "normalized_text",
            "raw_checksum", "normalized_checksum", "metadata_json",
        ),
        values,
    )


def _run_counts(db: Database, ingest_run_id: str) -> dict[str, int]:
    tables = {
        "containers": "static_evidence_container",
        "segments": "static_segment_occurrence",
        "relationships": "static_relationship_occurrence",
        "text_resolutions": "static_text_resolution_occurrence",
        "text_candidates": "static_text_candidate_occurrence",
    }
    return {
        key: int(
            db.conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE ingest_run_id=?",
                (ingest_run_id,),
            ).fetchone()[0]
        )
        for key, table in tables.items()
    }


def _run_scope(preflight: PreflightStaticPhase4B2Result) -> dict[str, Any]:
    return {
        "family": "static_phase4b2_multifamily",
        "manifest_id": preflight.manifest.manifest_id,
        "selected_paths": [item.relative_path for item in preflight.files],
    }


def _run_config(preflight: PreflightStaticPhase4B2Result) -> dict[str, Any]:
    return {
        "manifest_raw_sha256": preflight.manifest.raw_sha256,
        "versions": preflight.manifest.versions,
        "full_static_scan": False,
        "logical_identity_created": False,
        "textmap_evidence_container_created": False,
    }


def _expected_ids(preflight: PreflightStaticPhase4B2Result) -> dict[str, list[str]]:
    relationship_ids = {
        _relationship_coordinate(item): _relationship_id(preflight, item)
        for item in preflight.relationships
    }
    resolution_ids: dict[tuple[tuple[str, str, str], str], str] = {}
    for item in preflight.text_resolutions:
        owner = _text_owner_coordinate(item)
        resolution_ids[(owner, item.locale)] = _resolution_id(
            preflight, relationship_ids[owner], item
        )
    return {
        "static_evidence_container": sorted(
            _container_id(preflight, item) for item in preflight.containers
        ),
        "static_segment_occurrence": sorted(
            digest_id(
                "static-segment", preflight.snapshot_id,
                preflight.manifest.raw_sha256, preflight.ingest_run_id,
                item.relative_path, item.record_pointer, item.file_local_ordinal,
                item.byte_start, item.byte_end, item.character_start, item.character_end,
            )
            for item in preflight.segments
        ),
        "static_relationship_occurrence": sorted(relationship_ids.values()),
        "static_text_resolution_occurrence": sorted(resolution_ids.values()),
        "static_text_candidate_occurrence": sorted(
            _candidate_id(
                preflight,
                resolution_ids[(_text_owner_coordinate(item), item.locale)],
                item,
            )
            for item in preflight.text_candidates
        ),
    }


def _family_counts(preflight: PreflightStaticPhase4B2Result) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for family_key in ("book1257", "weapon15516", "reliquary15048", "materials"):
        result[family_key] = {
            "containers": sum(item.family_key == family_key for item in preflight.containers),
            "segments": sum(item.family_key == family_key for item in preflight.segments),
            "relationships": sum(item.family_key == family_key for item in preflight.relationships),
            "text_resolutions": sum(item.family_key == family_key for item in preflight.text_resolutions),
            "text_candidates": sum(item.family_key == family_key for item in preflight.text_candidates),
        }
    return result


def _database_fingerprint(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    stat = path.stat()
    return {
        "sha256": sha256_bytes(raw),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _build_report(
    target: Path,
    preflight: PreflightStaticPhase4B2Result,
) -> dict[str, Any]:
    counts = {"files": len(preflight.files), **{
        key: int(preflight.manifest.expected["counts"][key]) for key in _B2_COUNT_KEYS
    }}
    return {
        "status": "complete",
        "db_path": str(target.resolve()),
        "snapshot_id": preflight.snapshot_id,
        "parser_build_id": preflight.parser_build_id,
        "ingest_run_id": preflight.ingest_run_id,
        "config_checksum": preflight.config_checksum,
        "database_fingerprint": _database_fingerprint(target),
        "manifest": {
            "manifest_id": preflight.manifest.manifest_id,
            "raw_sha256": preflight.manifest.raw_sha256,
        },
        "counts": counts,
        "family_counts": _family_counts(preflight),
        "resolution_statuses": dict(preflight.manifest.expected["resolution_statuses"]),
        "scope": {
            "selected_paths": [item.relative_path for item in preflight.files],
            "full_static_scan": False,
            "fts_rebuilt": False,
            "logical_identity_created": False,
            "textmap_evidence_container_created": False,
            "cross_language_alignment_created": False,
        },
    }


def _validate_existing_complete_run(
    db: Database,
    preflight: PreflightStaticPhase4B2Result,
    row: sqlite3.Row,
) -> None:
    expected_identity = (
        preflight.snapshot_id,
        preflight.parser_build_id,
        canonical_json(_run_scope(preflight)),
        canonical_json(_run_config(preflight)),
        "complete",
    )
    actual_identity = tuple(
        row[column]
        for column in ("snapshot_id", "parser_build_id", "scope_json", "config_json", "status")
    )
    if actual_identity != expected_identity:
        raise sqlite3.IntegrityError("existing Phase 4B2 run identity/status mismatch")
    expected_counts = {
        key: int(preflight.manifest.expected["counts"][key]) for key in _B2_COUNT_KEYS
    }
    if _run_counts(db, preflight.ingest_run_id) != expected_counts:
        raise sqlite3.IntegrityError("existing Phase 4B2 run cardinality mismatch")
    for table, expected in _expected_ids(preflight).items():
        actual = [
            item[0]
            for item in db.conn.execute(
                f"SELECT id FROM {table} WHERE ingest_run_id=? ORDER BY id",
                (preflight.ingest_run_id,),
            )
        ]
        if actual != expected:
            raise sqlite3.IntegrityError(f"existing Phase 4B2 {table} identity set mismatch")
    if db.conn.execute("PRAGMA foreign_key_check").fetchall():
        raise sqlite3.IntegrityError("existing Phase 4B2 database has foreign key violations")


def ingest_static_phase4b2_evidence_bundle(
    db_path: str | Path,
    source_roots: list[str | Path],
    source_manifest_path: str | Path,
    phase4b2_manifest_path: str | Path,
) -> dict[str, Any]:
    """Append the sealed B2 run after a side-effect-free complete preflight."""
    preflight = preflight_static_phase4b2_evidence_bundle(
        source_roots, source_manifest_path, phase4b2_manifest_path
    )
    target = Path(db_path)
    with Database(target) as db:
        db.initialize_through(9)
        existing_run = db.conn.execute(
            """SELECT snapshot_id, parser_build_id, scope_json, config_json, status
                 FROM ingest_run WHERE id=?""",
            (preflight.ingest_run_id,),
        ).fetchone()
        if existing_run is not None:
            _validate_existing_complete_run(db, preflight, existing_run)
            return _build_report(target, preflight)

        expected_counts = {
            key: int(preflight.manifest.expected["counts"][key]) for key in _B2_COUNT_KEYS
        }
        deterministic_time = preflight.source_manifest.get(
            "observed_at", "2026-08-19T00:00:00+08:00"
        )
        with db.transaction():
            source = preflight.source_manifest
            source_values = (
                source["source_id"],
                "primary",
                source.get("upstream", "https://github.com/DimbreathBot/AnimeGameData"),
                None,
            )
            existing_source = db.conn.execute(
                "SELECT id, kind, url, license_note FROM source_catalog WHERE id=?",
                (source["source_id"],),
            ).fetchone()
            if existing_source is None:
                db.conn.execute(
                    "INSERT INTO source_catalog(id,kind,url,license_note) VALUES (?,?,?,?)",
                    source_values,
                )
            elif tuple(existing_source) != source_values:
                raise sqlite3.IntegrityError("existing source catalog does not match sealed B2 provenance")

            existing_snapshot = db.conn.execute(
                'SELECT source_id,release_label,build,"commit",tree,observed_at FROM source_snapshot WHERE id=?',
                (preflight.snapshot_id,),
            ).fetchone()
            snapshot_identity = (
                source["source_id"], source["release_label"], source["build"],
                source["commit"], source["tree"], deterministic_time,
            )
            if existing_snapshot is None:
                db.ensure_snapshot(
                    {
                        "id": preflight.snapshot_id,
                        "source_id": source["source_id"],
                        "release_label": source["release_label"],
                        "build": source["build"],
                        "commit": source["commit"],
                        "tree": source["tree"],
                        "observed_at": deterministic_time,
                        "parser_version": preflight.manifest.versions["parser"],
                        "mapping_version": preflight.manifest.versions["projection"],
                        "status": "active",
                        "metadata": {"sealed_source_manifest": True},
                    }
                )
            elif tuple(existing_snapshot) != snapshot_identity:
                raise sqlite3.IntegrityError("existing snapshot does not match sealed B2 provenance")

            db.ensure_parser_build(
                preflight.parser_build_id,
                parser_version=preflight.manifest.versions["parser"],
                mapping_version=preflight.manifest.versions["projection"],
                fingerprint="static-phase4b2-sealed-multifamily-evidence",
                config_checksum=preflight.config_checksum,
                created_at=deterministic_time,
            )
            scope_json = canonical_json(_run_scope(preflight))
            config_json = canonical_json(_run_config(preflight))
            db.conn.execute(
                """INSERT INTO ingest_run(
                       id,snapshot_id,parser_build_id,scope_json,config_json,status,created_at)
                   VALUES (?,?,?,?,?,'running',?)""",
                (
                    preflight.ingest_run_id, preflight.snapshot_id,
                    preflight.parser_build_id, scope_json, config_json,
                    deterministic_time,
                ),
            )

            file_ids: dict[str, str] = {}
            for item in preflight.files:
                file_ids[item.relative_path] = db.ensure_source_file(
                    preflight.snapshot_id,
                    {
                        "relative_path": item.relative_path,
                        "sha256": item.spec.sha256,
                        "size_bytes": item.spec.size_bytes,
                        "format": item.spec.format,
                        "root_path": str(item.located.root),
                    },
                )

            record_ids: dict[tuple[str, str], str] = {}
            container_ids: dict[tuple[str, str], str] = {}
            for item in preflight.containers:
                file_id = file_ids[item.relative_path]
                record_id = db.ensure_source_record(
                    file_id,
                    item.record_pointer,
                    item.record_kind,
                    item.native_id,
                    item.raw_value,
                    raw_json=item.raw_json,
                )
                coordinate = item.coordinate
                record_ids[coordinate] = record_id
                container_id = _container_id(preflight, item)
                container_ids[coordinate] = container_id
                _ensure_immutable_row(
                    db,
                    "static_evidence_container",
                    (
                        "id", "ingest_run_id", "snapshot_id", "source_file_id",
                        "source_record_id", "container_kind", "source_family", "locale",
                        "candidate_namespace", "candidate_key_json", "metadata_json",
                    ),
                    (
                        container_id, preflight.ingest_run_id, preflight.snapshot_id,
                        file_id, record_id, item.container_kind, item.source_family,
                        item.locale, item.candidate_namespace,
                        canonical_json(item.candidate_key),
                        canonical_json(
                            {
                                "family_key": item.family_key,
                                "classification": item.classification,
                                "manifest_id": preflight.manifest.manifest_id,
                                "manifest_raw_sha256": preflight.manifest.raw_sha256,
                                "raw_evidence_identity": "snapshot_source_coordinate_occurrence",
                            }
                        ),
                    ),
                )

            for item in preflight.segments:
                coordinate = (item.relative_path, item.record_pointer)
                segment_id = digest_id(
                    "static-segment", preflight.snapshot_id,
                    preflight.manifest.raw_sha256, preflight.ingest_run_id,
                    item.relative_path, item.record_pointer, item.file_local_ordinal,
                    item.byte_start, item.byte_end, item.character_start, item.character_end,
                )
                _ensure_immutable_row(
                    db,
                    "static_segment_occurrence",
                    (
                        "id", "ingest_run_id", "snapshot_id", "evidence_container_id",
                        "source_record_id", "container_kind", "locale", "source_pointer",
                        "file_local_ordinal", "byte_start", "byte_end", "character_start",
                        "character_end", "raw_text", "normalized_text", "raw_checksum",
                        "normalized_checksum", "segmentation_version", "metadata_json",
                    ),
                    (
                        segment_id, preflight.ingest_run_id, preflight.snapshot_id,
                        container_ids[coordinate], record_ids[coordinate], "readable_file",
                        item.locale, item.source_pointer, item.file_local_ordinal,
                        item.byte_start, item.byte_end, item.character_start,
                        item.character_end, item.raw_text, item.normalized_text,
                        item.raw_checksum, item.normalized_checksum,
                        item.segmentation_version,
                        canonical_json(
                            {
                                "family_key": item.family_key,
                                "identity_scope": "snapshot_source_coordinate_occurrence",
                                "cross_language_alignment": "unresolved",
                            }
                        ),
                    ),
                )

            relationship_ids: dict[tuple[str, str, str], str] = {}
            for item in preflight.relationships:
                source_coordinate = (item.source_relative_path, item.source_record_pointer)
                relationship_id = _relationship_id(preflight, item)
                relationship_ids[_relationship_coordinate(item)] = relationship_id
                target_container_id = (
                    None
                    if item.target_evidence_container_coordinate is None
                    else container_ids[item.target_evidence_container_coordinate]
                )
                _insert_static_relationship_occurrence(
                    db,
                    (
                        relationship_id, preflight.ingest_run_id, preflight.snapshot_id,
                        container_ids[source_coordinate], record_ids[source_coordinate],
                        item.source_family, item.source_pointer, item.relation_kind,
                        item.field_role, item.target_family,
                        canonical_json(item.target_candidate_key),
                        item.candidate_resolution_status, target_container_id, "{}",
                        canonical_json(
                            {
                                "family_key": item.family_key,
                                "raw_value": item.raw_value,
                                "manifest_id": preflight.manifest.manifest_id,
                                "candidate_resolution_detail": "raw_value_derived_then_manifest_and_target_checked",
                            }
                        ),
                    ),
                )

            resolution_ids: dict[tuple[tuple[str, str, str], str], str] = {}
            for item in preflight.text_resolutions:
                owner = _text_owner_coordinate(item)
                relationship_id = relationship_ids[owner]
                source_coordinate = (owner[0], owner[1])
                resolution_id = _resolution_id(preflight, relationship_id, item)
                resolution_ids[(owner, item.locale)] = resolution_id
                _insert_static_text_resolution_occurrence(
                    db,
                    (
                        resolution_id, preflight.ingest_run_id, preflight.snapshot_id,
                        relationship_id, container_ids[source_coordinate],
                        record_ids[source_coordinate], item.relationship_source_pointer,
                        item.locale, item.textmap_hash, item.resolution_status,
                        item.raw_text, item.normalized_text, item.raw_checksum,
                        item.normalized_checksum,
                        canonical_json(
                            {
                                "family_key": item.family_key,
                                "locator_only": True,
                                "owning_static_source": {
                                    "relative_path": owner[0],
                                    "record_pointer": owner[1],
                                    "source_pointer": owner[2],
                                },
                                "both_conflict_auto_selected": False,
                            }
                        ),
                    ),
                )

            text_record_ids: dict[tuple[str, str], str] = {}
            for item in preflight.text_candidates:
                owner = _text_owner_coordinate(item)
                resolution_id = resolution_ids[(owner, item.locale)]
                source_coordinate = (item.source_relative_path, item.source_record_pointer)
                source_record_id = text_record_ids.get(source_coordinate)
                if source_record_id is None:
                    source_record_id = db.ensure_source_record(
                        file_ids[item.source_relative_path],
                        item.source_record_pointer,
                        "textmap_entry",
                        item.textmap_hash,
                        item.raw_text,
                        raw_json=canonical_json(item.raw_text),
                    )
                    text_record_ids[source_coordinate] = source_record_id
                _insert_static_text_candidate_occurrence(
                    db,
                    (
                        _candidate_id(preflight, resolution_id, item), resolution_id,
                        preflight.ingest_run_id, preflight.snapshot_id,
                        file_ids[item.source_relative_path], source_record_id,
                        item.source_record_pointer, item.shard_kind, item.locale,
                        item.resolution_status, item.raw_text, item.normalized_text,
                        item.raw_checksum, item.normalized_checksum,
                        canonical_json(
                            {
                                "family_key": item.family_key,
                                "textmap_hash_is_locator_not_identity": True,
                                "owning_relationship_id": relationship_ids[owner],
                                "exact_textmap_source": {
                                    "relative_path": item.source_relative_path,
                                    "record_pointer": item.source_record_pointer,
                                },
                            }
                        ),
                    ),
                )

            actual_counts = _run_counts(db, preflight.ingest_run_id)
            if actual_counts != expected_counts:
                raise sqlite3.IntegrityError(
                    f"Phase 4B2 write cardinality mismatch: {actual_counts}"
                )
            foreign_keys = db.conn.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_keys:
                raise sqlite3.IntegrityError(
                    f"Phase 4B2 foreign key check failed: {foreign_keys}"
                )
            db.conn.execute(
                "UPDATE ingest_run SET status='complete' WHERE id=? AND status='running'",
                (preflight.ingest_run_id,),
            )
            if db.conn.execute("SELECT changes()").fetchone()[0] != 1:
                raise sqlite3.IntegrityError("Phase 4B2 run completion transition failed")

    return _build_report(target, preflight)


__all__ = [
    "PreflightStaticPhase4B2Container",
    "PreflightStaticPhase4B2File",
    "PreflightStaticPhase4B2Relationship",
    "PreflightStaticPhase4B2Result",
    "PreflightStaticPhase4B2Segment",
    "PreflightStaticPhase4B2TextCandidate",
    "PreflightStaticPhase4B2TextResolution",
    "ingest_static_phase4b2_evidence_bundle",
    "preflight_static_phase4b2_evidence_bundle",
]
