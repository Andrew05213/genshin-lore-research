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
EXPECTED_MANIFEST_ID = "anime-game-data-7.0-phase4b5-reliquary-corpus"
EXPECTED_TABLE_INVENTORIES = {
    "ReliquaryExcelConfigData": 4352,
    "ReliquarySetExcelConfigData": 66,
    "ReliquaryCodexExcelConfigData": 129,
    "DocumentExcelConfigData": 1852,
    "LocalizationExcelConfigData": 2229,
}
EXPECTED_SELECTED_ROWS = {
    "ReliquaryExcelConfigData": 4352,
    "ReliquarySetExcelConfigData": 66,
    "ReliquaryCodexExcelConfigData": 129,
    "DocumentExcelConfigData": 305,
    "LocalizationExcelConfigData": 305,
}
EXPECTED_COUNTS = {
    "selected_files": 926,
    "source_records": 32258,
    "containers": 6072,
    "segments": 11475,
    "relationships": 20322,
    "text_resolutions": 27027,
    "text_candidates": 26186,
}
EXPECTED_FAMILIES = {
    "reliquaries": {
        "containers": 6072,
        "segments": 11475,
        "relationships": 20322,
        "text_resolutions": 27027,
        "text_candidates": 26186,
    },
}
EXPECTED_RELATIONSHIP_STATUSES = {
    "resolved": 20151,
    "ambiguous": 0,
    "unresolved": 171,
    "dangling": 0,
}
EXPECTED_TEXT_STATUSES = {
    "unique_main": 0,
    "unique_medium": 26186,
    "both_equal": 0,
    "both_conflict": 0,
    "missing": 841,
}
EXPECTED_READABLE_CLASSIFICATIONS = {
    "narrative": 897,
    "empty": 12,
    "blank_whitespace_only": 6,
    "placeholder_exact_test": 0,
}
EXPECTED_LOCALE_BREAKDOWN = {
    "CHS": {
        "readable_containers": 305,
        "segments": 3825,
        "text_resolutions": 9009,
        "text_candidates": 8934,
    },
    "EN": {
        "readable_containers": 305,
        "segments": 3825,
        "text_resolutions": 9009,
        "text_candidates": 8624,
    },
    "JP": {
        "readable_containers": 305,
        "segments": 3825,
        "text_resolutions": 9009,
        "text_candidates": 8628,
    },
}
EXPECTED_OCCURRENCE_DIGESTS = {
    "containers": "bc3aeccb9d2f5ee02a02afa522949dac2dfaeaf09387654216ca1953faf8bb2e",
    "files": "eaf520eba1f6405ee6d4cc3c52020cf7e120785d6f14c34c3ed46e21f61f834c",
    "relationships": "27400d5996dd7bbd66e15e0e186120e681f8e365c8619e3aa821ca84a059d770",
    "segments": "b2640a2fa98a72a102a8fbdb096f5505e3cd1b4fb5d03fd001b2ee237999d299",
    "source_records": "834463107bab565e87088560aa56b5e238e271bc58406ed7324f874add6de6c7",
    "text_assets": "4e7f90e5ffc6e5cf2a76025f1c5c3d7084d67312a82fa46a667acf37ae3d8cdd",
    "text_candidates": "2274c5091ed5bfb94327d06b3bc02b249eeec625e7ffc4fd38ac55a2429d8a44",
    "text_resolutions": "6f7a5438473be8e45eca4b0e8ae35dc13cadde1dd6c00cd135d98c54c336619a",
}
EXPECTED_VERSIONS = {
    "manifest_contract": "static-phase4b5-corpus-manifest-v1",
    "parser": "static-phase4b5-corpus-parser-v1",
    "projection": "static-phase4b5-reliquary-projection-v1",
    "segmentation": "readable-line-occurrence-raw-span-v2",
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


class Phase4B5ManifestError(RuntimeError):
    pass


@dataclass(frozen=True)
class StaticPhase4B5FileSpec:
    relative_path: str
    format: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class StaticPhase4B5Manifest:
    path: Path
    raw_sha256: str
    manifest_id: str
    source: dict[str, Any]
    versions: dict[str, str]
    boundaries: dict[str, Any]
    files: tuple[StaticPhase4B5FileSpec, ...]
    tables: dict[str, dict[str, Any]]
    readables: tuple[dict[str, Any], ...]
    expected: dict[str, Any]
    raw: dict[str, Any]


def _error(message: str) -> Phase4B5ManifestError:
    return Phase4B5ManifestError(message)


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


def _load_files(value: Any) -> tuple[StaticPhase4B5FileSpec, ...]:
    if not isinstance(value, list):
        raise _error("files must be an array")
    specs: list[StaticPhase4B5FileSpec] = []
    raw_specs: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        item = _exact_keys(raw, {"relative_path", "format", "size_bytes", "sha256"}, f"files[{index}]")
        path = _relative_path(item["relative_path"], f"files[{index}].relative_path")
        if item["format"] not in {"json", "txt"}:
            raise _error(f"files[{index}].format is unsupported")
        if not isinstance(item["size_bytes"], int) or isinstance(item["size_bytes"], bool) or item["size_bytes"] < 0:
            raise _error(f"files[{index}].size_bytes must be non-negative")
        sha = _hex(item["sha256"], f"files[{index}].sha256")
        specs.append(StaticPhase4B5FileSpec(path, item["format"], item["size_bytes"], sha))
        raw_specs.append({"relative_path": path, "format": item["format"], "size_bytes": item["size_bytes"], "sha256": sha})
    if len(specs) != EXPECTED_COUNTS["selected_files"]:
        raise _error(f"expected {EXPECTED_COUNTS['selected_files']} selected files, got {len(specs)}")
    ordered = [spec.relative_path for spec in specs]
    if ordered != sorted(ordered) or len(ordered) != len(set(ordered)):
        raise _error("files array must be sorted and strictly unique")
    if _set_digest(raw_specs) != EXPECTED_OCCURRENCE_DIGESTS["files"]:
        raise _error("files set digest mismatch")
    return tuple(specs)


def _load_tables(value: Any) -> dict[str, dict[str, Any]]:
    tables = _exact_keys(value, set(EXPECTED_TABLE_INVENTORIES), "tables")
    for table_name, expected_inv in EXPECTED_TABLE_INVENTORIES.items():
        entry = _exact_keys(
            tables[table_name],
            {"relative_path", "native_field", "source_inventory_count", "selected_count", "selected_rows", "selected_set_digest"},
            f"tables.{table_name}",
        )
        if entry["relative_path"] != f"ExcelBinOutput/{table_name}.json":
            raise _error(f"tables.{table_name}.relative_path mismatch")
        if entry["native_field"] not in {"id", "setId"}:
            raise _error(f"tables.{table_name}.native_field unsupported")
        if entry["source_inventory_count"] != expected_inv:
            raise _error(f"tables.{table_name}.source_inventory_count must be {expected_inv}")
        selected_count = EXPECTED_SELECTED_ROWS[table_name]
        if entry["selected_count"] != selected_count:
            raise _error(f"tables.{table_name}.selected_count must be {selected_count}")
        rows = entry["selected_rows"]
        if not isinstance(rows, list) or len(rows) != selected_count:
            raise _error(f"tables.{table_name}.selected_rows length mismatch")
        for idx, row in enumerate(rows):
            _exact_keys(row, {"pointer", "native_id", "raw_sha256", "family"}, f"tables.{table_name}.selected_rows[{idx}]")
            if not isinstance(row["pointer"], str) or not row["pointer"].startswith("/"):
                raise _error(f"tables.{table_name}.selected_rows[{idx}].pointer invalid")
            if not isinstance(row["native_id"], str) or not row["native_id"]:
                raise _error(f"tables.{table_name}.selected_rows[{idx}].native_id invalid")
            _hex(row["raw_sha256"], f"tables.{table_name}.selected_rows[{idx}].raw_sha256")
            if row["family"] != "reliquaries":
                raise _error(f"tables.{table_name}.selected_rows[{idx}].family must be reliquaries")
        if table_name in EXPECTED_SELECTED_ROWS and table_name in ("ReliquaryExcelConfigData", "ReliquarySetExcelConfigData", "ReliquaryCodexExcelConfigData"):
            expected_ptrs = [f"/{idx}" for idx in range(selected_count)]
            actual_ptrs = [row["pointer"] for row in rows]
            if actual_ptrs != expected_ptrs:
                raise _error(f"tables.{table_name}.selected_rows pointers must cover all rows in index order")
        if _set_digest(rows) != entry["selected_set_digest"]:
            raise _error(f"tables.{table_name}.selected_set_digest mismatch")
    return tables


def _load_readables(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or len(value) != 915:
        raise _error("readables must be an array of exactly 915 items")
    out: list[dict[str, Any]] = []
    for idx, raw in enumerate(value):
        item = _exact_keys(
            raw,
            {"relative_path", "locale", "family", "classification", "segment_count", "segment_set_digest"},
            f"readables[{idx}]",
        )
        path = _relative_path(item["relative_path"], f"readables[{idx}].relative_path")
        if item["locale"] not in {"CHS", "EN", "JP"}:
            raise _error(f"readables[{idx}].locale unsupported")
        if item["family"] != "reliquaries":
            raise _error(f"readables[{idx}].family must be reliquaries")
        if item["classification"] not in EXPECTED_READABLE_CLASSIFICATIONS:
            raise _error(f"readables[{idx}].classification unsupported")
        if not isinstance(item["segment_count"], int) or item["segment_count"] < 0:
            raise _error(f"readables[{idx}].segment_count invalid")
        _hex(item["segment_set_digest"], f"readables[{idx}].segment_set_digest")
        out.append(item)
    return tuple(out)


def load_static_phase4b5_reliquary_manifest(manifest_path: str | Path) -> StaticPhase4B5Manifest:
    path = Path(manifest_path).resolve()
    if not path.is_file():
        raise _error(f"Phase 4B5 manifest not found: {path}")
    raw_bytes = path.read_bytes()
    raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    try:
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error(f"Phase 4B5 manifest is not valid UTF-8 JSON: {exc}") from exc

    _exact_keys(raw, {"schema_version", "manifest_id", "source", "versions", "boundaries", "files", "tables", "readables", "expected"}, "manifest")
    if raw["schema_version"] != 1:
        raise _error("manifest schema_version must be 1")
    if raw["manifest_id"] != EXPECTED_MANIFEST_ID:
        raise _error(f"manifest_id must be {EXPECTED_MANIFEST_ID}")

    source = _exact_keys(raw["source"], {"source_id", "upstream", "release_label", "build", "commit", "tree", "observed_at"}, "source")
    if source["commit"] != EXPECTED_COMMIT or source["tree"] != EXPECTED_TREE or source["build"] != EXPECTED_BUILD or source["release_label"] != "7.0":
        raise _error("source pins mismatch AnimeGameData 7.0 release")

    versions = _exact_keys(raw["versions"], set(EXPECTED_VERSIONS), "versions")
    for key, val in EXPECTED_VERSIONS.items():
        if versions[key] != val:
            raise _error(f"versions.{key} must be {val}")

    boundaries = _exact_keys(raw["boundaries"], {"primary_table_inventory", "locales", "textmap_shards", "document_localization_scope", "readable_scope"}, "boundaries")
    if boundaries["primary_table_inventory"] != {"ReliquaryExcelConfigData": 4352, "ReliquarySetExcelConfigData": 66, "ReliquaryCodexExcelConfigData": 129}:
        raise _error("boundaries.primary_table_inventory mismatch")
    if boundaries["locales"] != ["CHS", "EN", "JP"]:
        raise _error("boundaries.locales mismatch")
    if boundaries["textmap_shards"] != EXPECTED_TEXTMAP_SHARDS:
        raise _error("boundaries.textmap_shards mismatch")

    files = _load_files(raw["files"])
    tables = _load_tables(raw["tables"])
    readables = _load_readables(raw["readables"])

    expected = _exact_keys(raw["expected"], {"counts", "families", "relationship_statuses", "text_resolution_statuses", "readable_classifications", "locale_breakdown", "occurrence_set_digests"}, "expected")
    if expected["counts"] != EXPECTED_COUNTS:
        raise _error("expected.counts mismatch")
    if expected["families"] != EXPECTED_FAMILIES:
        raise _error("expected.families mismatch")
    if expected["relationship_statuses"] != EXPECTED_RELATIONSHIP_STATUSES:
        raise _error("expected.relationship_statuses mismatch")
    if expected["text_resolution_statuses"] != EXPECTED_TEXT_STATUSES:
        raise _error("expected.text_resolution_statuses mismatch")
    if expected["readable_classifications"] != EXPECTED_READABLE_CLASSIFICATIONS:
        raise _error("expected.readable_classifications mismatch")
    if expected["locale_breakdown"] != EXPECTED_LOCALE_BREAKDOWN:
        raise _error("expected.locale_breakdown mismatch")
    if expected["occurrence_set_digests"] != EXPECTED_OCCURRENCE_DIGESTS:
        raise _error("expected.occurrence_set_digests mismatch")

    return StaticPhase4B5Manifest(
        path=path,
        raw_sha256=raw_sha256,
        manifest_id=EXPECTED_MANIFEST_ID,
        source=source,
        versions=versions,
        boundaries=boundaries,
        files=files,
        tables=tables,
        readables=readables,
        expected=expected,
        raw=raw,
    )
