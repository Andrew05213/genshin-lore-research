from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


EXPECTED_COMMIT = "26df1dfbdf05a82bbb1d97506859f3e1c40718d8"
EXPECTED_TREE = "d891ef1d95fdd6c27a1da8a32ea7a61b483647a6"
EXPECTED_BUILD = "CNRELWin7.0.0_R47482070_S47579390_D47579390"
EXPECTED_MANIFEST_ID = "anime-game-data-7.0-phase4b3-book-weapon-corpus"
EXPECTED_TABLE_INVENTORIES = {
    "BookSuitExcelConfigData": 77,
    "BooksCodexExcelConfigData": 293,
    "WeaponExcelConfigData": 281,
    "WeaponCodexExcelConfigData": 249,
    "MaterialExcelConfigData": 10404,
    "DocumentExcelConfigData": 1852,
    "LocalizationExcelConfigData": 2229,
}
EXPECTED_SELECTED_ROWS = {
    "BookSuitExcelConfigData": 77,
    "BooksCodexExcelConfigData": 293,
    "WeaponExcelConfigData": 281,
    "WeaponCodexExcelConfigData": 249,
    "MaterialExcelConfigData": 288,
    "DocumentExcelConfigData": 537,
    "LocalizationExcelConfigData": 542,
}
EXPECTED_COUNTS = {
    "selected_files": 1639,
    "source_records": 9016,
    "containers": 3893,
    "segments": 33135,
    "relationships": 5584,
    "text_resolutions": 5256,
    "text_candidates": 5123,
}
EXPECTED_FAMILIES = {
    "books": {
        "containers": 2110,
        "segments": 18420,
        "relationships": 2980,
        "text_resolutions": 2823,
        "text_candidates": 2805,
    },
    "weapons": {
        "containers": 1783,
        "segments": 14715,
        "relationships": 2604,
        "text_resolutions": 2433,
        "text_candidates": 2318,
    },
}
EXPECTED_RELATIONSHIP_STATUSES = {
    "resolved": 5542,
    "ambiguous": 2,
    "unresolved": 29,
    "dangling": 11,
}
EXPECTED_TEXT_STATUSES = {
    "unique_main": 0,
    "unique_medium": 5123,
    "both_equal": 0,
    "both_conflict": 0,
    "missing": 133,
}
EXPECTED_READABLE_CLASSIFICATIONS = {
    "narrative": 1625,
    "empty": 0,
    "blank_whitespace_only": 1,
    "placeholder_exact_test": 0,
}
EXPECTED_LOCALE_BREAKDOWN = {
    "CHS": {
        "readable_containers": 542,
        "segments": 11048,
        "text_resolutions": 1752,
        "text_candidates": 1703,
    },
    "EN": {
        "readable_containers": 542,
        "segments": 11044,
        "text_resolutions": 1752,
        "text_candidates": 1710,
    },
    "JP": {
        "readable_containers": 542,
        "segments": 11043,
        "text_resolutions": 1752,
        "text_candidates": 1710,
    },
}
EXPECTED_OCCURRENCE_DIGESTS = {
    "files": "b44939949056d60a73d8b97a7e07c3aebf797bdc81efa94cdcda78e170e90226",
    "source_records": "14de5865e56e5b54df0455f961c0a3dcae0ff1db299fa3ed002636e221f5f84c",
    "containers": "bd2f90e02db267e21480a13317c2425cae7269c0f22592702a9dd885e803256f",
    "segments": "2da8192f6875bc1ac7ce70e5dbe6bc5f704ba41a66cb486cff22b5497f48d475",
    "relationships": "38771a6a7d18ee1578bbb098acfa64b9f284891d7f026a3ed09f2712d4920797",
    "text_resolutions": "c029a7494dc61c7f4929e52badd2c6b7cf7b4279ae80b8b14ab701ff999a8a91",
    "text_candidates": "e0994aab9ba582f4fc2a97b5350d51d25de987a4b11e658a96966d651ba6161a",
    "text_assets": "60ec442c60139635f0ed5da09643b8a373fe50a64d6e646549243e69c906e635",
}
EXPECTED_VERSIONS = {
    "manifest_contract": "static-phase4b3-corpus-manifest-v1",
    "parser": "static-phase4b3-corpus-parser-v1",
    "projection": "static-phase4b3-books-weapons-projection-v1",
    "segmentation": "readable-line-occurrence-raw-span-v2",
    "books_material_document_rule": "books-material-id-document-native-candidate-v1",
    "document_localization_rule": "questIDList-localization-candidate-set-v1",
    "localization_readable_rule": "primary-triad-localization-readable-path-v1",
    "textmap_resolution_rule": "owner-locale-main-medium-occurrence-v1",
}
EXPECTED_TEXTMAP_SHARDS = {
    "CHS": {"main": "TextMap/TextMapCHS.json", "medium": "TextMap/TextMap_MediumCHS.json"},
    "EN": {"main": "TextMap/TextMapEN.json", "medium": "TextMap/TextMap_MediumEN.json"},
    "JP": {"main": "TextMap/TextMapJP.json", "medium": "TextMap/TextMap_MediumJP.json"},
}
HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class Phase4B3ManifestError(RuntimeError):
    pass


@dataclass(frozen=True)
class StaticPhase4B3FileSpec:
    relative_path: str
    format: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class StaticPhase4B3Manifest:
    path: Path
    raw_sha256: str
    manifest_id: str
    source: dict[str, Any]
    versions: dict[str, str]
    boundaries: dict[str, Any]
    files: tuple[StaticPhase4B3FileSpec, ...]
    tables: dict[str, dict[str, Any]]
    readables: tuple[dict[str, Any], ...]
    expected: dict[str, Any]
    raw: dict[str, Any]


def _error(message: str) -> Phase4B3ManifestError:
    return Phase4B3ManifestError(message)


def _exact_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        raise _error(f"{label} keys mismatch: missing={sorted(expected-actual)} extra={sorted(actual-expected)}")
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _set_digest(items: list[dict[str, Any]]) -> str:
    ordered = sorted(_canonical(item) for item in items)
    return hashlib.sha256(_canonical(ordered).encode("utf-8")).hexdigest()


def _hex(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEX_64.fullmatch(value) is None:
        raise _error(f"{label} must be a lower-case SHA-256")
    return value


def _relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise _error(f"{label} must be a non-empty POSIX relative path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts) or pure.as_posix() != value:
        raise _error(f"{label} is not a canonical relative path: {value!r}")
    return value


def _load_files(value: Any) -> tuple[StaticPhase4B3FileSpec, ...]:
    if not isinstance(value, list):
        raise _error("files must be an array")
    specs: list[StaticPhase4B3FileSpec] = []
    raw_specs: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        item = _exact_keys(raw, {"relative_path", "format", "size_bytes", "sha256"}, f"files[{index}]")
        path = _relative_path(item["relative_path"], f"files[{index}].relative_path")
        if item["format"] not in {"json", "txt"}:
            raise _error(f"files[{index}].format is unsupported")
        if not isinstance(item["size_bytes"], int) or isinstance(item["size_bytes"], bool) or item["size_bytes"] < 0:
            raise _error(f"files[{index}].size_bytes must be non-negative")
        digest = _hex(item["sha256"], f"files[{index}].sha256")
        specs.append(StaticPhase4B3FileSpec(path, item["format"], item["size_bytes"], digest))
        raw_specs.append(dict(item))
    paths = [item.relative_path for item in specs]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise _error("selected file paths must be sorted and unique")
    if len(specs) != EXPECTED_COUNTS["selected_files"]:
        raise _error("selected file inventory count mismatch")
    if _set_digest(raw_specs) != EXPECTED_OCCURRENCE_DIGESTS["files"]:
        raise _error("selected file occurrence digest mismatch")
    return tuple(specs)


def _load_tables(value: Any) -> dict[str, dict[str, Any]]:
    tables = _exact_keys(value, set(EXPECTED_TABLE_INVENTORIES), "tables")
    result: dict[str, dict[str, Any]] = {}
    for table in sorted(tables):
        raw = _exact_keys(
            tables[table],
            {"relative_path", "native_field", "source_inventory_count", "selected_count", "selected_rows", "selected_set_digest"},
            f"tables.{table}",
        )
        expected_path = f"ExcelBinOutput/{table}.json"
        if _relative_path(raw["relative_path"], f"tables.{table}.relative_path") != expected_path:
            raise _error(f"{table} file binding mismatch")
        if raw["native_field"] != "id":
            raise _error(f"{table} native field must be id")
        if raw["source_inventory_count"] != EXPECTED_TABLE_INVENTORIES[table]:
            raise _error(f"{table} source inventory mismatch")
        if raw["selected_count"] != EXPECTED_SELECTED_ROWS[table]:
            raise _error(f"{table} selected count mismatch")
        rows = raw["selected_rows"]
        if not isinstance(rows, list) or len(rows) != raw["selected_count"]:
            raise _error(f"{table} selected row list/count mismatch")
        checked: list[dict[str, Any]] = []
        pointers: list[str] = []
        native_ids: list[str] = []
        for index, selected in enumerate(rows):
            item = _exact_keys(selected, {"pointer", "native_id", "raw_sha256", "family"}, f"tables.{table}.selected_rows[{index}]")
            if not isinstance(item["pointer"], str) or re.fullmatch(r"/(0|[1-9][0-9]*)", item["pointer"]) is None:
                raise _error(f"{table} selected row pointer is invalid")
            if not isinstance(item["native_id"], str) or not item["native_id"]:
                raise _error(f"{table} selected native ID is invalid")
            _hex(item["raw_sha256"], f"{table} row raw_sha256")
            if item["family"] not in {"books", "weapons"}:
                raise _error(f"{table} selected family is invalid")
            pointers.append(item["pointer"])
            native_ids.append(item["native_id"])
            checked.append(dict(item))
        numeric = [int(pointer[1:]) for pointer in pointers]
        if numeric != sorted(numeric) or len(pointers) != len(set(pointers)) or len(native_ids) != len(set(native_ids)):
            raise _error(f"{table} selected coordinates/native candidates are not unique and ordered")
        if table in EXPECTED_TABLE_INVENTORIES and table in {
            "BookSuitExcelConfigData", "BooksCodexExcelConfigData", "WeaponExcelConfigData", "WeaponCodexExcelConfigData"
        }:
            if pointers != [f"/{index}" for index in range(EXPECTED_TABLE_INVENTORIES[table])]:
                raise _error(f"{table} primary inventory is not complete")
        if _set_digest(checked) != raw["selected_set_digest"]:
            raise _error(f"{table} selected row digest mismatch")
        _hex(raw["selected_set_digest"], f"{table} selected_set_digest")
        result[table] = dict(raw)
    return result


def _load_readables(value: Any, file_paths: set[str]) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or len(value) != 1626:
        raise _error("readables must contain exactly 1626 selected files")
    result: list[dict[str, Any]] = []
    paths: list[str] = []
    for index, raw in enumerate(value):
        item = _exact_keys(
            raw,
            {"relative_path", "locale", "family", "classification", "segment_count", "segment_set_digest"},
            f"readables[{index}]",
        )
        path = _relative_path(item["relative_path"], f"readables[{index}].relative_path")
        if path not in file_paths:
            raise _error(f"readable path is not sealed by files: {path}")
        if item["locale"] not in {"CHS", "EN", "JP"} or not path.startswith(f"Readable/{item['locale']}/"):
            raise _error(f"readable locale/path mismatch: {path}")
        if item["family"] not in {"books", "weapons"}:
            raise _error("readable family is invalid")
        if item["classification"] not in EXPECTED_READABLE_CLASSIFICATIONS:
            raise _error("readable classification is invalid")
        if not isinstance(item["segment_count"], int) or isinstance(item["segment_count"], bool) or item["segment_count"] < 0:
            raise _error("readable segment count is invalid")
        _hex(item["segment_set_digest"], "readable segment_set_digest")
        paths.append(path)
        result.append(dict(item))
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise _error("readable paths must be sorted and unique")
    classifications = {key: sum(item["classification"] == key for item in result) for key in EXPECTED_READABLE_CLASSIFICATIONS}
    if classifications != EXPECTED_READABLE_CLASSIFICATIONS:
        raise _error("readable classification inventory mismatch")
    return tuple(result)


def load_static_phase4b3_manifest(path: str | Path) -> StaticPhase4B3Manifest:
    manifest_path = Path(path)
    try:
        raw_bytes = manifest_path.read_bytes()
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _error(f"cannot load Phase 4B3 manifest: {exc}") from exc
    root = _exact_keys(
        raw,
        {"schema_version", "manifest_id", "source", "versions", "boundaries", "files", "tables", "readables", "expected"},
        "manifest",
    )
    if root["schema_version"] != 1 or root["manifest_id"] != EXPECTED_MANIFEST_ID:
        raise _error("unsupported Phase 4B3 manifest identity")
    source = _exact_keys(root["source"], {"source_id", "upstream", "release_label", "build", "commit", "tree", "observed_at"}, "source")
    expected_source = {
        "source_id": "anime-game-data",
        "upstream": "https://github.com/DimbreathBot/AnimeGameData",
        "release_label": "7.0",
        "build": EXPECTED_BUILD,
        "commit": EXPECTED_COMMIT,
        "tree": EXPECTED_TREE,
        "observed_at": "2026-08-19T00:00:00+08:00",
    }
    if source != expected_source:
        raise _error("Phase 4B3 source pin does not match the accepted 7.0 snapshot")
    versions = _exact_keys(root["versions"], set(EXPECTED_VERSIONS), "versions")
    if versions != EXPECTED_VERSIONS:
        raise _error("Phase 4B3 version contract mismatch")
    boundaries = _exact_keys(
        root["boundaries"],
        {"primary_table_inventory", "locales", "textmap_shards", "material_scope", "document_localization_scope", "readable_scope"},
        "boundaries",
    )
    if boundaries["primary_table_inventory"] != {key: EXPECTED_TABLE_INVENTORIES[key] for key in (
        "BookSuitExcelConfigData", "BooksCodexExcelConfigData", "WeaponExcelConfigData", "WeaponCodexExcelConfigData"
    )}:
        raise _error("primary table boundary mismatch")
    if boundaries["locales"] != ["CHS", "EN", "JP"] or boundaries["textmap_shards"] != EXPECTED_TEXTMAP_SHARDS:
        raise _error("primary-triad TextMap boundary mismatch")
    if boundaries["material_scope"] != "books_codex_referenced_rows_only":
        raise _error("Material bridge boundary mismatch")
    files = _load_files(root["files"])
    tables = _load_tables(root["tables"])
    readables = _load_readables(root["readables"], {item.relative_path for item in files})
    expected = _exact_keys(
        root["expected"],
        {"counts", "families", "relationship_statuses", "text_resolution_statuses", "readable_classifications", "locale_breakdown", "occurrence_set_digests"},
        "expected",
    )
    if expected["counts"] != EXPECTED_COUNTS:
        raise _error("Phase 4B3 total count contract mismatch")
    if expected["families"] != EXPECTED_FAMILIES:
        raise _error("Phase 4B3 family count contract mismatch")
    if expected["relationship_statuses"] != EXPECTED_RELATIONSHIP_STATUSES:
        raise _error("Phase 4B3 relationship status contract mismatch")
    if expected["text_resolution_statuses"] != EXPECTED_TEXT_STATUSES:
        raise _error("Phase 4B3 TextMap status contract mismatch")
    if expected["readable_classifications"] != EXPECTED_READABLE_CLASSIFICATIONS:
        raise _error("Phase 4B3 Readable classification contract mismatch")
    if expected["locale_breakdown"] != EXPECTED_LOCALE_BREAKDOWN:
        raise _error("Phase 4B3 locale reconciliation mismatch")
    if expected["occurrence_set_digests"] != EXPECTED_OCCURRENCE_DIGESTS:
        raise _error("Phase 4B3 occurrence digest contract mismatch")
    return StaticPhase4B3Manifest(
        path=manifest_path,
        raw_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        manifest_id=root["manifest_id"],
        source=dict(source),
        versions=dict(versions),
        boundaries=dict(boundaries),
        files=files,
        tables=tables,
        readables=readables,
        expected=dict(expected),
        raw=dict(root),
    )
