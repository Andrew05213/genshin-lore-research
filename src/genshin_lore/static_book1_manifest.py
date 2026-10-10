from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import ManifestError
from .source import validate_manifest
from .util import canonical_json, sha256_bytes


EXPECTED_FILES = {
    "ExcelBinOutput/DocumentExcelConfigData.json",
    "ExcelBinOutput/LocalizationExcelConfigData.json",
    "Readable/CHS/Book1.txt",
    "Readable/EN/Book1_EN.txt",
    "Readable/JP/Book1_JP.txt",
}
EXPECTED_RELATIONSHIP_POINTERS = {
    "/6/questIDList/0",
    "/8/DEFNEHAFMMA",
    "/8/JDNBKKPEFAI",
    "/8/enPath",
    "/8/jpPath",
    "/6/titleTextMapHash",
}
EXPECTED_COMMIT = "26df1dfbdf05a82bbb1d97506859f3e1c40718d8"
EXPECTED_TREE = "d891ef1d95fdd6c27a1da8a32ea7a61b483647a6"
EXPECTED_BOOK1_MANIFEST_SHA256 = (
    "f9e639ed79f7c57df967c93df13ecc826b8d564af9c8caccf253103bf4a5168c"
)
EXPECTED_SOURCE_MANIFEST_SHA256 = (
    "fd3434cd1d9a75ea5e49c74844cafc0ecf320b92e0cc9110d097b33ece89461d"
)
EXPECTED_SOURCE_MANIFEST_CANONICAL_SHA256 = (
    "4c931f999155f92452fe8c5eea1e1ae1fc3fcbda7a2fff456edb9d9dc7e8b85c"
)
EXPECTED_SOURCE_IDENTITY = {
    "schema_version": 1,
    "source_id": "anime-game-data",
    "upstream": "https://github.com/DimbreathBot/AnimeGameData",
    "release_label": "7.0",
    "build": "CNRELWin7.0.0_R47482070_S47579390_D47579390",
    "commit": EXPECTED_COMMIT,
    "tree": EXPECTED_TREE,
}
HEX_40 = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")

_RELATIONSHIP_CONTRACTS = {
    "/6/questIDList/0": {
        "source_record_pointer": "/6",
        "relation_kind": "document_localization_candidate",
        "field_role": "unresolved_field_role",
        "target_family": "Localization",
        "candidate_keys": {"native_id"},
        "target_record_pointer": "/8",
    },
    "/8/DEFNEHAFMMA": {
        "source_record_pointer": "/8",
        "relation_kind": "localization_readable_candidate",
        "field_role": "unknown_obfuscated",
        "target_family": "Readable",
        "candidate_keys": {"locale", "relative_path"},
    },
    "/8/JDNBKKPEFAI": {
        "source_record_pointer": "/8",
        "relation_kind": "localization_readable_candidate",
        "field_role": "unknown_obfuscated",
        "target_family": "Readable",
        "candidate_keys": {"locale", "relative_path"},
    },
    "/8/enPath": {
        "source_record_pointer": "/8",
        "relation_kind": "localization_readable_candidate",
        "field_role": "unresolved_field_role",
        "target_family": "Readable",
        "candidate_keys": {"locale", "relative_path"},
    },
    "/8/jpPath": {
        "source_record_pointer": "/8",
        "relation_kind": "localization_readable_candidate",
        "field_role": "unresolved_field_role",
        "target_family": "Readable",
        "candidate_keys": {"locale", "relative_path"},
    },
    "/6/titleTextMapHash": {
        "source_record_pointer": "/6",
        "relation_kind": "document_title_textmap_candidate",
        "field_role": "verified_textmap_reference",
        "target_family": "TextMap",
        "candidate_keys": {"textmap_hash"},
        "target_record_pointer": None,
    },
}
_READABLE_CANDIDATE_PATHS = {
    "CHS": "Readable/CHS/Book1.txt",
    "EN": "Readable/EN/Book1_EN.txt",
    "JP": "Readable/JP/Book1_JP.txt",
}
_TEXTMAP_LOCATOR_PATHS = {
    "CHS": "TextMap/TextMap_MediumCHS.json",
    "EN": "TextMap/TextMap_MediumEN.json",
    "JP": "TextMap/TextMap_MediumJP.json",
}


@dataclass(frozen=True)
class StaticBook1FileSpec:
    relative_path: str
    format: str
    size_bytes: int
    sha256: str
    record: dict[str, Any]


@dataclass(frozen=True)
class StaticBook1RelationshipSpec:
    source_record_pointer: str
    source_pointer: str
    relation_kind: str
    field_role: str
    target_family: str
    target_candidate_key: dict[str, Any]
    candidate_resolution_status: str
    target_record_pointer: str | None
    candidate_locators: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class StaticBook1Manifest:
    path: Path
    raw_sha256: str
    schema_version: int
    manifest_id: str
    source: dict[str, str]
    versions: dict[str, str]
    files: tuple[StaticBook1FileSpec, ...]
    relationships: tuple[StaticBook1RelationshipSpec, ...]
    expected: dict[str, Any]


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ManifestError(
            f"{label} keys invalid: missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{label} must be an object")
    return value


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{label} must be a non-empty string")
    return value


def _relative_path(value: Any, label: str) -> str:
    text = _nonempty(value, label)
    if "\\" in text or re.match(r"^[A-Za-z]:", text):
        raise ManifestError(f"{label} must be a normalized relative path")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ManifestError(f"{label} must be a normalized relative path")
    return text


def load_static_book1_source_manifest(path: str | Path) -> dict[str, Any]:
    """Load the one source manifest admitted by the sealed Book1 release."""

    manifest_path = Path(path)
    try:
        raw = manifest_path.read_bytes()
        normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        value = json.loads(normalized.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(
            f"cannot read Book1 source manifest {manifest_path}: {exc}"
        ) from exc
    if sha256_bytes(normalized) != EXPECTED_SOURCE_MANIFEST_SHA256:
        raise ManifestError("Book1 source manifest canonical-LF SHA-256 mismatch")
    if not isinstance(value, dict):
        raise ManifestError("Book1 source manifest must be an object")
    validate_manifest(value)
    canonical_digest = sha256_bytes(canonical_json(value).encode("utf-8"))
    if canonical_digest != EXPECTED_SOURCE_MANIFEST_CANONICAL_SHA256:
        raise ManifestError("Book1 source manifest canonical SHA-256 mismatch")
    identity = {key: value.get(key) for key in EXPECTED_SOURCE_IDENTITY}
    if identity != EXPECTED_SOURCE_IDENTITY:
        raise ManifestError(f"Book1 source manifest identity mismatch: {identity!r}")
    return value


def load_static_book1_manifest(path: str | Path) -> StaticBook1Manifest:
    manifest_path = Path(path)
    try:
        raw = manifest_path.read_bytes()
        normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        value = json.loads(normalized.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read Book1 manifest {manifest_path}: {exc}") from exc
    if sha256_bytes(normalized) != EXPECTED_BOOK1_MANIFEST_SHA256:
        raise ManifestError("Book1 manifest canonical-LF SHA-256 mismatch")
    value = _object(value, "Book1 manifest")
    _exact_keys(
        value,
        {"schema_version", "manifest_id", "source", "versions", "files", "relationships", "expected"},
        "Book1 manifest",
    )
    if value["schema_version"] != 1:
        raise ManifestError("Book1 manifest schema_version must be 1")

    source = _object(value["source"], "source")
    _exact_keys(source, {"source_id", "release_label", "commit", "tree"}, "source")
    if source.get("source_id") != "anime-game-data" or source.get("release_label") != "7.0":
        raise ManifestError("Book1 source must be anime-game-data release 7.0")
    if not isinstance(source.get("commit"), str) or not HEX_40.fullmatch(source["commit"]):
        raise ManifestError("Book1 source commit must be lowercase 40-hex")
    if not isinstance(source.get("tree"), str) or not HEX_40.fullmatch(source["tree"]):
        raise ManifestError("Book1 source tree must be lowercase 40-hex")
    if source["commit"] != EXPECTED_COMMIT or source["tree"] != EXPECTED_TREE:
        raise ManifestError("Book1 manifest is not pinned to the frozen AnimeGameData 7.0 snapshot")

    versions = _object(value["versions"], "versions")
    _exact_keys(versions, {"parser", "projection", "segmentation"}, "versions")
    for key, version in versions.items():
        _nonempty(version, f"versions.{key}")

    raw_files = value["files"]
    if not isinstance(raw_files, list) or len(raw_files) != 5:
        raise ManifestError("Book1 manifest files must contain exactly five entries")
    files: list[StaticBook1FileSpec] = []
    seen_paths: set[str] = set()
    for index, raw_file in enumerate(raw_files):
        item = _object(raw_file, f"files[{index}]")
        _exact_keys(item, {"relative_path", "format", "size_bytes", "sha256", "record"}, f"files[{index}]")
        relative = _relative_path(item["relative_path"], f"files[{index}].relative_path")
        if relative in seen_paths:
            raise ManifestError(f"duplicate Book1 file path: {relative}")
        seen_paths.add(relative)
        if not isinstance(item["size_bytes"], int) or isinstance(item["size_bytes"], bool) or item["size_bytes"] <= 0:
            raise ManifestError(f"files[{index}].size_bytes must be a positive integer")
        if not isinstance(item["sha256"], str) or not HEX_64.fullmatch(item["sha256"]):
            raise ManifestError(f"files[{index}].sha256 must be lowercase 64-hex")
        record = _object(item["record"], f"files[{index}].record")
        _exact_keys(record, {"pointer", "record_kind", "native_id", "source_family", "container_kind", "locale"}, f"files[{index}].record")
        for key in ("pointer", "record_kind", "native_id", "source_family", "container_kind"):
            _nonempty(record[key], f"files[{index}].record.{key}")
        if record["container_kind"] == "readable_file":
            if record["locale"] not in ("CHS", "EN", "JP"):
                raise ManifestError("readable Book1 record locale must be CHS, EN, or JP")
        elif record["container_kind"] == "excel_row":
            if record["locale"] is not None:
                raise ManifestError("excel Book1 record locale must be null")
        else:
            raise ManifestError("unsupported Book1 container_kind")
        files.append(StaticBook1FileSpec(relative, _nonempty(item["format"], "format"), item["size_bytes"], item["sha256"], dict(record)))
    if seen_paths != EXPECTED_FILES:
        raise ManifestError(f"Book1 file set mismatch: {sorted(seen_paths)}")

    raw_relationships = value["relationships"]
    if not isinstance(raw_relationships, list) or len(raw_relationships) != 6:
        raise ManifestError("Book1 manifest relationships must contain exactly six entries")
    relationships: list[StaticBook1RelationshipSpec] = []
    seen_pointers: set[str] = set()
    base_keys = {
        "source_record_pointer", "source_pointer", "relation_kind", "field_role",
        "target_family", "target_candidate_key", "candidate_resolution_status",
        "target_record_pointer",
    }
    for index, raw_relationship in enumerate(raw_relationships):
        item = _object(raw_relationship, f"relationships[{index}]")
        allowed = base_keys | ({"candidate_locators"} if item.get("source_pointer") == "/6/titleTextMapHash" else set())
        _exact_keys(item, allowed, f"relationships[{index}]")
        pointer = _nonempty(item["source_pointer"], f"relationships[{index}].source_pointer")
        if pointer in seen_pointers:
            raise ManifestError(f"duplicate relationship source pointer: {pointer}")
        seen_pointers.add(pointer)
        contract = _RELATIONSHIP_CONTRACTS.get(pointer)
        if contract is None:
            raise ManifestError(f"unsupported Book1 relationship source pointer: {pointer}")
        for field in (
            "source_record_pointer",
            "relation_kind",
            "field_role",
            "target_family",
        ):
            if item[field] != contract[field]:
                raise ManifestError(
                    f"Book1 relationship {pointer} has invalid {field}: {item[field]!r}"
                )
        if item["candidate_resolution_status"] != "resolved":
            raise ManifestError("sealed Book1 relationship candidates must be resolved")
        candidate = _object(item["target_candidate_key"], "target_candidate_key")
        _exact_keys(
            candidate,
            contract["candidate_keys"],
            f"relationship {pointer} target_candidate_key",
        )
        target_pointer = item["target_record_pointer"]
        if target_pointer is not None:
            _nonempty(target_pointer, "target_record_pointer")
        if pointer == "/6/questIDList/0":
            _nonempty(candidate["native_id"], "Localization candidate native_id")
            if not candidate["native_id"].isdigit():
                raise ManifestError("Localization candidate native_id must contain only digits")
            if target_pointer != contract["target_record_pointer"]:
                raise ManifestError("Localization candidate target_record_pointer must be /8")
        elif contract["target_family"] == "Readable":
            locale = _nonempty(candidate["locale"], "Readable candidate locale")
            if locale not in _READABLE_CANDIDATE_PATHS:
                raise ManifestError("Readable candidate locale must be CHS, EN, or JP")
            relative = _relative_path(
                candidate["relative_path"],
                "Readable candidate relative_path",
            )
            if relative != _READABLE_CANDIDATE_PATHS[locale]:
                raise ManifestError("Readable candidate locale and relative_path disagree")
            if target_pointer != f"/text:{relative}":
                raise ManifestError(
                    "Readable candidate target_record_pointer must identify its declared file"
                )
        else:
            textmap_hash = _nonempty(
                candidate["textmap_hash"],
                "TextMap candidate textmap_hash",
            )
            if not textmap_hash.isdigit():
                raise ManifestError("TextMap candidate textmap_hash must contain only digits")
            if target_pointer is not None:
                raise ManifestError("titleTextMapHash must not declare a concrete target")
        locators_raw = item.get("candidate_locators", [])
        if not isinstance(locators_raw, list):
            raise ManifestError("candidate_locators must be an array")
        locators: list[dict[str, str]] = []
        for locator in locators_raw:
            locator = _object(locator, "candidate locator")
            _exact_keys(locator, {"locale", "relative_path", "pointer"}, "candidate locator")
            locators.append({
                "locale": _nonempty(locator["locale"], "candidate locator locale"),
                "relative_path": _relative_path(locator["relative_path"], "candidate locator relative_path"),
                "pointer": _nonempty(locator["pointer"], "candidate locator pointer"),
            })
        if pointer == "/6/titleTextMapHash":
            if len(locators) != 3:
                raise ManifestError("titleTextMapHash requires exactly three locator candidates")
            locators_by_locale = {locator["locale"]: locator for locator in locators}
            if set(locators_by_locale) != set(_TEXTMAP_LOCATOR_PATHS):
                raise ManifestError("titleTextMapHash locators must be exactly CHS, EN, and JP")
            expected_pointer = f"/{candidate['textmap_hash']}"
            for locale, expected_path in _TEXTMAP_LOCATOR_PATHS.items():
                locator = locators_by_locale[locale]
                if (
                    locator["relative_path"] != expected_path
                    or locator["pointer"] != expected_pointer
                ):
                    raise ManifestError(
                        f"titleTextMapHash locator disagrees with locale/hash: {locale}"
                    )
        elif locators:
            raise ManifestError("only titleTextMapHash may declare candidate locators")
        relationships.append(
            StaticBook1RelationshipSpec(
                source_record_pointer=_nonempty(item["source_record_pointer"], "source_record_pointer"),
                source_pointer=pointer,
                relation_kind=_nonempty(item["relation_kind"], "relation_kind"),
                field_role=_nonempty(item["field_role"], "field_role"),
                target_family=_nonempty(item["target_family"], "target_family"),
                target_candidate_key=dict(candidate),
                candidate_resolution_status="resolved",
                target_record_pointer=target_pointer,
                candidate_locators=tuple(locators),
            )
        )
    if seen_pointers != EXPECTED_RELATIONSHIP_POINTERS:
        raise ManifestError(f"Book1 relationship pointer set mismatch: {sorted(seen_pointers)}")

    expected = _object(value["expected"], "expected")
    _exact_keys(expected, {"container_count", "segment_count", "segment_count_by_locale", "relationship_count", "non_empty_line_ordinals"}, "expected")
    if expected["container_count"] != 5 or expected["segment_count"] != 57 or expected["relationship_count"] != 6:
        raise ManifestError("Book1 frozen cardinalities must be 5/57/6")
    if expected["segment_count_by_locale"] != {"CHS": 19, "EN": 19, "JP": 19}:
        raise ManifestError("Book1 locale segment counts must be CHS/EN/JP=19")
    if expected["non_empty_line_ordinals"] != list(range(11)) + list(range(12, 20)):
        raise ManifestError("Book1 non-empty line ordinals are not frozen")

    return StaticBook1Manifest(
        path=manifest_path,
        raw_sha256=sha256_bytes(normalized),
        schema_version=1,
        manifest_id=_nonempty(value["manifest_id"], "manifest_id"),
        source={key: str(source[key]) for key in source},
        versions={key: str(versions[key]) for key in versions},
        files=tuple(files),
        relationships=tuple(relationships),
        expected=dict(expected),
    )
