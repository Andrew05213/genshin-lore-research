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
EXPECTED_MANIFEST_ID = "anime-game-data-7.0-phase4b6-material-corpus"
EXPECTED_TABLE_INVENTORIES = {
    "MaterialExcelConfigData": 10404,
    "MaterialCodexExcelConfigData": 1166,
}
EXPECTED_SELECTED_ROW_DIGESTS = {
    "MaterialExcelConfigData": "0fd0fa51fbbc6640a791d09bf56d7d3ef3bb30b6e781975d1d3f114b5f143973",
    "MaterialCodexExcelConfigData": "e7f46ed7c411af9d542fac03601780714eae60531e9d5ff10719d70d2d965059",
}
EXPECTED_COUNTS = {
    "selected_files": 8,
    "source_records": 100138,
    "containers": 11570,
    "segments": 0,
    "relationships": 70586,
    "text_resolutions": 208260,
    "text_candidates": 88568,
    "text_assets": 88568,
    "text_owner_occurrences": 69420,
}
EXPECTED_FAMILIES = {
    "materials": {
        "containers": 11570,
        "segments": 0,
        "relationships": 70586,
        "text_resolutions": 208260,
        "text_candidates": 88568,
        "text_assets": 88568,
        "text_owner_occurrences": 69420,
    }
}
EXPECTED_RELATIONSHIP_STATUSES = {
    "resolved": 70586,
    "ambiguous": 0,
    "unresolved": 0,
    "dangling": 0,
}
EXPECTED_TEXT_STATUSES = {
    "unique_main": 1932,
    "unique_medium": 86636,
    "both_equal": 0,
    "both_conflict": 0,
    "missing": 119692,
}
EXPECTED_LOCALE_BREAKDOWN = {
    "CHS": {"text_resolutions": 69420, "text_candidates": 29922, "text_assets": 29922},
    "EN": {"text_resolutions": 69420, "text_candidates": 29316, "text_assets": 29316},
    "JP": {"text_resolutions": 69420, "text_candidates": 29330, "text_assets": 29330},
}
EXPECTED_TEXTMAP_SHARDS = {
    "CHS": {"main": "TextMap/TextMapCHS.json", "medium": "TextMap/TextMap_MediumCHS.json"},
    "EN": {"main": "TextMap/TextMapEN.json", "medium": "TextMap/TextMap_MediumEN.json"},
    "JP": {"main": "TextMap/TextMapJP.json", "medium": "TextMap/TextMap_MediumJP.json"},
}
EXPECTED_VERSIONS = {
    "manifest_contract": "static-phase4b6-material-manifest-v1",
    "parser": "static-phase4b6-material-parser-v1",
    "projection": "static-phase4b6-material-projection-v1",
    "textmap_resolution_rule": "owner-locale-main-medium-occurrence-v1",
    "identity_rule": "material-table-scoped-raw-occurrence-v1",
}
EXPECTED_TEXT_FIELDS = [
    {"table": "MaterialExcelConfigData", "field": "nameTextMapHash", "field_role": "display_name", "relation_kind": "material_name_textmap_candidate", "owner_occurrences": 10404},
    {"table": "MaterialExcelConfigData", "field": "descTextMapHash", "field_role": "description", "relation_kind": "material_description_textmap_candidate", "owner_occurrences": 10404},
    {"table": "MaterialExcelConfigData", "field": "effectDescTextMapHash", "field_role": "effect_description", "relation_kind": "material_effect_description_textmap_candidate", "owner_occurrences": 10404},
    {"table": "MaterialExcelConfigData", "field": "interactionTitleTextMapHash", "field_role": "interaction_title", "relation_kind": "material_interaction_title_textmap_candidate", "owner_occurrences": 10404},
    {"table": "MaterialExcelConfigData", "field": "specialDescTextMapHash", "field_role": "special_description", "relation_kind": "material_special_description_textmap_candidate", "owner_occurrences": 10404},
    {"table": "MaterialExcelConfigData", "field": "typeDescTextMapHash", "field_role": "type_description", "relation_kind": "material_type_description_textmap_candidate", "owner_occurrences": 10404},
    {"table": "MaterialCodexExcelConfigData", "field": "nameTextMapHash", "field_role": "display_name", "relation_kind": "material_codex_name_textmap_candidate", "owner_occurrences": 1166},
    {"table": "MaterialCodexExcelConfigData", "field": "descTextMapHash", "field_role": "description", "relation_kind": "material_codex_description_textmap_candidate", "owner_occurrences": 1166},
    {"table": "MaterialCodexExcelConfigData", "field": "questReplaceName1TextMapHash", "field_role": "state_variant_name_1", "relation_kind": "material_codex_quest_replace_name1_textmap_candidate", "owner_occurrences": 1166},
    {"table": "MaterialCodexExcelConfigData", "field": "questReplaceName2TextMapHash", "field_role": "state_variant_name_2", "relation_kind": "material_codex_quest_replace_name2_textmap_candidate", "owner_occurrences": 1166},
    {"table": "MaterialCodexExcelConfigData", "field": "questReplaceDesc1TextMapHash", "field_role": "state_variant_description_1", "relation_kind": "material_codex_quest_replace_desc1_textmap_candidate", "owner_occurrences": 1166},
    {"table": "MaterialCodexExcelConfigData", "field": "questReplaceDesc2TextMapHash", "field_role": "state_variant_description_2", "relation_kind": "material_codex_quest_replace_desc2_textmap_candidate", "owner_occurrences": 1166},
]
EXPECTED_RELATIONSHIP_CONTRACTS = [
    {
        "relation_kind": "material_codex_material_candidate",
        "source_table": "MaterialCodexExcelConfigData",
        "source_pointer": "/{index}/materialId",
        "field_role": "foreign_pointer_candidate",
        "target_table": "MaterialExcelConfigData",
        "target_locator": "table_scoped_native_id",
        "status_policy": "resolved_or_dangling_or_unresolved",
    },
    *[
        {
            "relation_kind": item["relation_kind"],
            "source_table": item["table"],
            "source_pointer": f"/{{index}}/{item['field']}",
            "field_role": item["field_role"],
            "target_table": "TextMap",
            "target_locator": "owner_locale_shard_textmap_hash_candidate",
            "status_policy": "resolved_reference_with_independent_text_resolution",
        }
        for item in EXPECTED_TEXT_FIELDS
    ],
]
EXPECTED_RELATIONSHIP_KIND_COUNTS = {
    item["relation_kind"]: (1166 if item["relation_kind"] == "material_codex_material_candidate" else item["owner_occurrences"])
    for item in [{**contract, **next((field for field in EXPECTED_TEXT_FIELDS if field["relation_kind"] == contract["relation_kind"]), {})} for contract in EXPECTED_RELATIONSHIP_CONTRACTS]
}
EXPECTED_FIELD_OCCURRENCE_COUNTS = {
    f"{item['table']}.{item['field']}": item["owner_occurrences"] for item in EXPECTED_TEXT_FIELDS
}
EXPECTED_OCCURRENCE_DIGESTS = {
    "containers": "227589696c53e33cde43e9095f819f71cab4417d8f90d8098c97c2089059a71b",
    "files": "4e76ebbe3ef0cb854f7beaad0d8a2f6cddf2d24303db0b9ce73c7fceab40ec4d",
    "relationships": "9db1008a75140e11f62d2f9c3e4b14f5396c8bfceb85d8b7cb81cd4a02e53b8f",
    "segments": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    "source_records": "019976f47794c1ec1e4d0ffca7cb70e4dc5645f57af7df0b70c6bc5633a12256",
    "text_assets": "c5606038537970491de807d39b03b6d591369746a81e599e622b192a241d78ff",
    "text_candidates": "9a11a95339f96d2e99f1345a16ea757162f630b9e094708e73c0546db70975f4",
    "text_owners": "ad54c59a53a98c85612f838ba705961b2a8ca226be6caafbcb8de2d66717b453",
    "text_resolutions": "f0ec1db600b8ffcd38a0529777869b999222af0e331bfbd61b864a01bd981f88",
}
EXPECTED_CANARIES = {
    "material_101001": {
        "table": "MaterialExcelConfigData",
        "pointer": "/636",
        "native_id": "101001",
        "row_raw_sha256": "5e366cfbaa97e1b6a9d7f326fc1fba7e967a5fa2df741405766bdb0510a430ea",
    },
    "material_codex_40100001": {
        "table": "MaterialCodexExcelConfigData",
        "pointer": "/0",
        "native_id": "40100001",
        "material_id": "101001",
        "replacement_fields": [
            "questReplaceName1TextMapHash",
            "questReplaceName2TextMapHash",
            "questReplaceDesc1TextMapHash",
            "questReplaceDesc2TextMapHash",
        ],
        "replacement_values": {
            "questReplaceDesc1TextMapHash": 1742759892,
            "questReplaceDesc2TextMapHash": 2479426549,
            "questReplaceName1TextMapHash": 1221984078,
            "questReplaceName2TextMapHash": 972096252,
        },
        "table": "MaterialCodexExcelConfigData",
    },
}
HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class Phase4B6ManifestError(RuntimeError):
    pass


@dataclass(frozen=True)
class StaticPhase4B6FileSpec:
    relative_path: str
    format: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class StaticPhase4B6MaterialManifest:
    path: Path
    raw_sha256: str
    manifest_id: str
    source: dict[str, Any]
    versions: dict[str, str]
    boundaries: dict[str, Any]
    identity: dict[str, Any]
    files: tuple[StaticPhase4B6FileSpec, ...]
    tables: dict[str, dict[str, Any]]
    text_fields: tuple[dict[str, Any], ...]
    relationships: tuple[dict[str, Any], ...]
    canaries: dict[str, Any]
    expected: dict[str, Any]
    raw: dict[str, Any]


def _error(message: str) -> Phase4B6ManifestError:
    return Phase4B6ManifestError(message)


def _exact_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        raise _error(f"{label} keys mismatch: missing={sorted(expected - actual)} extra={sorted(actual - expected)}")
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _set_digest(items: list[dict[str, Any]]) -> str:
    return hashlib.sha256(_canonical(sorted(_canonical(item) for item in items)).encode("utf-8")).hexdigest()


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


def _load_files(value: Any) -> tuple[StaticPhase4B6FileSpec, ...]:
    if not isinstance(value, list):
        raise _error("files must be an array")
    specs: list[StaticPhase4B6FileSpec] = []
    raw_specs: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        item = _exact_keys(raw, {"relative_path", "format", "size_bytes", "sha256"}, f"files[{index}]")
        path = _relative_path(item["relative_path"], f"files[{index}].relative_path")
        if item["format"] != "json":
            raise _error(f"files[{index}].format must be json")
        if not isinstance(item["size_bytes"], int) or isinstance(item["size_bytes"], bool) or item["size_bytes"] < 0:
            raise _error(f"files[{index}].size_bytes must be non-negative")
        digest = _hex(item["sha256"], f"files[{index}].sha256")
        specs.append(StaticPhase4B6FileSpec(path, item["format"], item["size_bytes"], digest))
        raw_specs.append({"relative_path": path, "format": item["format"], "size_bytes": item["size_bytes"], "sha256": digest})
    paths = [item.relative_path for item in specs]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise _error("selected file paths must be sorted and unique")
    if len(specs) != EXPECTED_COUNTS["selected_files"]:
        raise _error("selected file inventory count mismatch")
    expected_paths = {
        "ExcelBinOutput/MaterialCodexExcelConfigData.json",
        "ExcelBinOutput/MaterialExcelConfigData.json",
        *(path for locale in EXPECTED_TEXTMAP_SHARDS.values() for path in locale.values()),
    }
    if set(paths) != expected_paths:
        raise _error("selected files exceed the explicit Material/TextMap boundary")
    if _set_digest(raw_specs) != EXPECTED_OCCURRENCE_DIGESTS["files"]:
        raise _error("selected file occurrence digest mismatch")
    return tuple(specs)


def _load_tables(value: Any) -> dict[str, dict[str, Any]]:
    tables = _exact_keys(value, set(EXPECTED_TABLE_INVENTORIES), "tables")
    result: dict[str, dict[str, Any]] = {}
    for table, inventory in EXPECTED_TABLE_INVENTORIES.items():
        raw = _exact_keys(
            tables[table],
            {"relative_path", "native_field", "source_inventory_count", "selected_count", "selected_rows", "selected_set_digest"},
            f"tables.{table}",
        )
        expected_path = f"ExcelBinOutput/{table}.json"
        if _relative_path(raw["relative_path"], f"tables.{table}.relative_path") != expected_path:
            raise _error(f"{table} relative path mismatch")
        if raw["native_field"] != "id":
            raise _error(f"{table} native field must be id")
        if raw["source_inventory_count"] != inventory or raw["selected_count"] != inventory:
            raise _error(f"{table} inventory/selection mismatch")
        rows = raw["selected_rows"]
        if not isinstance(rows, list) or len(rows) != inventory:
            raise _error(f"{table} selected row list mismatch")
        checked: list[dict[str, Any]] = []
        pointers: list[str] = []
        native_ids: list[str] = []
        for index, selected in enumerate(rows):
            item = _exact_keys(selected, {"pointer", "native_id", "raw_sha256", "family"}, f"tables.{table}.selected_rows[{index}]")
            if item["pointer"] != f"/{index}":
                raise _error(f"{table} selected row coordinate mismatch at {index}")
            if not isinstance(item["native_id"], str) or not item["native_id"]:
                raise _error(f"{table} selected native ID is invalid")
            _hex(item["raw_sha256"], f"tables.{table}.selected_rows[{index}].raw_sha256")
            if item["family"] != "materials":
                raise _error(f"{table} selected family must be materials")
            pointers.append(item["pointer"])
            native_ids.append(item["native_id"])
            checked.append(dict(item))
        if len(native_ids) != len(set(native_ids)):
            raise _error(f"{table} selected native IDs are not unique")
        selected_set_digest = _hex(raw["selected_set_digest"], f"tables.{table}.selected_set_digest")
        if selected_set_digest != EXPECTED_SELECTED_ROW_DIGESTS[table]:
            raise _error(f"{table} selected row seal mismatch")
        if _set_digest(checked) != EXPECTED_SELECTED_ROW_DIGESTS[table]:
            raise _error(f"{table} selected row digest mismatch")
        result[table] = dict(raw)
    return result


def _load_text_fields(value: Any) -> tuple[dict[str, Any], ...]:
    if value != EXPECTED_TEXT_FIELDS:
        raise _error("Material TextMap owning-field contract mismatch")
    for index, item in enumerate(value):
        _exact_keys(item, {"table", "field", "field_role", "relation_kind", "owner_occurrences"}, f"text_fields[{index}]")
    return tuple(dict(item) for item in value)


def _load_relationships(value: Any) -> tuple[dict[str, Any], ...]:
    if value != EXPECTED_RELATIONSHIP_CONTRACTS:
        raise _error("Material relationship contract mismatch")
    for index, item in enumerate(value):
        _exact_keys(item, {"relation_kind", "source_table", "source_pointer", "field_role", "target_table", "target_locator", "status_policy"}, f"relationships[{index}]")
    return tuple(dict(item) for item in value)


def load_static_phase4b6_material_manifest(manifest_path: str | Path) -> StaticPhase4B6MaterialManifest:
    path = Path(manifest_path).resolve()
    try:
        raw_bytes = path.read_bytes()
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _error(f"cannot load Phase 4B6 material manifest: {exc}") from exc
    root = _exact_keys(
        raw,
        {"schema_version", "manifest_id", "source", "versions", "boundaries", "identity", "files", "tables", "text_fields", "relationships", "canaries", "expected"},
        "manifest",
    )
    if root["schema_version"] != 1 or root["manifest_id"] != EXPECTED_MANIFEST_ID:
        raise _error("unsupported Phase 4B6 manifest identity")
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
        raise _error("Phase 4B6 source pin does not match the accepted 7.0 snapshot")
    versions = _exact_keys(root["versions"], set(EXPECTED_VERSIONS), "versions")
    if versions != EXPECTED_VERSIONS:
        raise _error("Phase 4B6 version contract mismatch")
    boundaries = _exact_keys(root["boundaries"], {"primary_table_inventory", "locales", "textmap_shards", "support_scope"}, "boundaries")
    if boundaries["primary_table_inventory"] != EXPECTED_TABLE_INVENTORIES:
        raise _error("Phase 4B6 primary table boundary mismatch")
    if boundaries["locales"] != ["CHS", "EN", "JP"] or boundaries["textmap_shards"] != EXPECTED_TEXTMAP_SHARDS:
        raise _error("Phase 4B6 TextMap boundary mismatch")
    if boundaries["support_scope"] != "primary_material_tables_and_explicit_textmap_shards_only":
        raise _error("Phase 4B6 support scope mismatch")
    identity = _exact_keys(root["identity"], {"raw_evidence_identity", "native_id_scope", "textmap_hash_scope", "checksum_semantics", "logical_identity_created", "cross_table_numeric_id_collisions"}, "identity")
    expected_identity = {
        "raw_evidence_identity": "snapshot_ingest_run_source_file_source_record_occurrence",
        "native_id_scope": "table_scoped_candidate_locator",
        "textmap_hash_scope": "owner_locale_shard_candidate_locator",
        "checksum_semantics": "change_detection_only",
        "logical_identity_created": False,
        "cross_table_numeric_id_collisions": 0,
    }
    if identity != expected_identity:
        raise _error("Phase 4B6 raw identity contract mismatch")
    files = _load_files(root["files"])
    tables = _load_tables(root["tables"])
    text_fields = _load_text_fields(root["text_fields"])
    relationships = _load_relationships(root["relationships"])
    canaries = _exact_keys(root["canaries"], set(EXPECTED_CANARIES), "canaries")
    if canaries != EXPECTED_CANARIES:
        raise _error("Phase 4B6 canary contract mismatch")
    expected = _exact_keys(
        root["expected"],
        {"counts", "families", "relationship_statuses", "relationship_kind_counts", "text_resolution_statuses", "locale_breakdown", "field_occurrence_counts", "cross_table_numeric_id_collisions", "occurrence_set_digests"},
        "expected",
    )
    if expected["counts"] != EXPECTED_COUNTS:
        raise _error("Phase 4B6 total count contract mismatch")
    if expected["families"] != EXPECTED_FAMILIES:
        raise _error("Phase 4B6 family count contract mismatch")
    if expected["relationship_statuses"] != EXPECTED_RELATIONSHIP_STATUSES:
        raise _error("Phase 4B6 relationship status contract mismatch")
    if expected["relationship_kind_counts"] != EXPECTED_RELATIONSHIP_KIND_COUNTS:
        raise _error("Phase 4B6 relationship kind count contract mismatch")
    if expected["text_resolution_statuses"] != EXPECTED_TEXT_STATUSES:
        raise _error("Phase 4B6 TextMap status contract mismatch")
    if expected["locale_breakdown"] != EXPECTED_LOCALE_BREAKDOWN:
        raise _error("Phase 4B6 locale reconciliation mismatch")
    if expected["field_occurrence_counts"] != EXPECTED_FIELD_OCCURRENCE_COUNTS:
        raise _error("Phase 4B6 field occurrence contract mismatch")
    if expected["cross_table_numeric_id_collisions"] != 0:
        raise _error("Phase 4B6 cross-table identity collision contract mismatch")
    if expected["occurrence_set_digests"] != EXPECTED_OCCURRENCE_DIGESTS:
        raise _error("Phase 4B6 occurrence digest contract mismatch")
    return StaticPhase4B6MaterialManifest(
        path=path,
        raw_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        manifest_id=root["manifest_id"],
        source=dict(source),
        versions=dict(versions),
        boundaries=dict(boundaries),
        identity=dict(identity),
        files=files,
        tables=tables,
        text_fields=text_fields,
        relationships=relationships,
        canaries=dict(canaries),
        expected=dict(expected),
        raw=dict(root),
    )
