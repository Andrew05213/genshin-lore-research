"""Pinned, family-specific contract for the Phase 4E3 World Archive release.

The contract deliberately derives the full declared boundary from the pinned
source checkout.  It stores physical occurrences only; no native id, text,
hash, checksum, image, or model path creates logical identity.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from .util import canonical_json, digest_id, normalize_text, sha256_bytes, sha256_text


FAMILY = "world_archive"
FAMILY_CONTRACT_VERSION = "world-archive-evidence-v1"
RUN_FAMILY = "world_archive_phase4e3_full_source_closure"
EXPECTED_COVERAGE = "declared_world_archive_source_closure_complete"
EXPECTED_CORPUS_COMPLETE = True
EXPECTED_COMMIT = "26df1dfbdf05a82bbb1d97506859f3e1c40718d8"
EXPECTED_TREE = "d891ef1d95fdd6c27a1da8a32ea7a61b483647a6"
EXPECTED_SNAPSHOT_ID = f"anime-game-data:7.0:{EXPECTED_COMMIT}"
EXPECTED_BUILD = "CNRELWin7.0.0_R47482070_S47579390_D47579390"
EXPECTED_OBSERVED_AT = "2026-08-19T00:00:00+08:00"
MANIFEST_ID = "anime-game-data-7.0-phase4e3-world-archive-full"
# Filled after the deterministic manifest is generated.  Empty is accepted
# only by the generator's provisional manifest; tracked/final loading is
# always byte-sealed by this value.
EXPECTED_MANIFEST_SHA256 = "7d991825bd78024aab363832605d3dbd85a244a486b6acc9a1b7706b106caa0f"
VERSIONS = {
    "manifest_contract": "world-archive-phase4e3-full-boundary-manifest-v2",
    "family_contract": FAMILY_CONTRACT_VERSION,
    "parser": "world-archive-phase4e3-full-boundary-parser-v1",
    "projection": "world-archive-phase4e3-full-boundary-projection-v1",
    "relationship_rule": "phase4e1-field-specific-candidate-v1",
    "text_rule": "explicit-owner-locale-main-medium-v1",
    "asset_rule": "raw-reference-observation-v1",
    "identity_rule": "snapshot-parser-build-ingest-run-source-coordinate-occurrence-v1",
}
PRIMARY_PATHS = {
    "AnimalCodexExcelConfigData": "ExcelBinOutput/AnimalCodexExcelConfigData.json",
    "ViewCodexExcelConfigData": "ExcelBinOutput/ViewCodexExcelConfigData.json",
}
SELECTED_ROWS = {
    "AnimalCodexExcelConfigData": tuple(range(584)),
    "ViewCodexExcelConfigData": tuple(range(268)),
}
LOCALES = ("CHS", "EN", "JP")
TEXTMAP_PATHS = {
    locale: {
        "main": f"TextMap/TextMap{locale}.json",
        "medium": f"TextMap/TextMap_Medium{locale}.json",
    }
    for locale in LOCALES
}
AUDIT_ARTIFACTS = {
    "phase4e1-audit-manifest.json": "ed7f5020802d1ae536224d23223b9f41255e945ffe7f46999b57a1875c43e879",
    "phase4e1-source-inventory.json": "e97272df279a8ec222c087a2c8db504ae883ae5ac8caed6599332862283abc84",
    "phase4e1-row-identity-audit.json": "c708ba2698825cfee1bd5a6031222bcc01cf5b3ddef5e3dad94f740ab0dcba96",
    "phase4e1-relationship-audit.json": "bd3bb504c4323cd614ebd5b0b190e6d954b3091771b5c1eb3e5c40b2dc3882da",
    "phase4e1-text-owner-audit.json": "9ccd4c369ba69e13dbe07850a4f77b1ac4a9dec765b5e8dac3e18f026cb8374b",
    "phase4e1-asset-reference-audit.json": "9d36514730e1dfb8de33f077ea8bcf9357321eba0dccc118bf1b671bcb3c4e44",
    "phase4e1-adjacent-inventory.json": "fe1641ea59d0fee60310d4174c44aef50565bd26530dcdab26630beb37f9315e",
    "phase4e1-reconciliation.json": "be08fe3afdda495cc1b171e086f245dc026cebdc8e4e5463066ec6d567b4c322",
}
RELATION_PROBE_PATHS = {
    "MonsterDescribeExcelConfigData": "ExcelBinOutput/MonsterDescribeExcelConfigData.json",
    "MonsterExcelConfigData": "ExcelBinOutput/MonsterExcelConfigData.json",
    "PushTipsCodexExcelConfigData": "ExcelBinOutput/PushTipsCodexExcelConfigData.json",
    "GadgetExcelConfigData": "ExcelBinOutput/GadgetExcelConfigData.json",
    "SceneExcelConfigData": "ExcelBinOutput/SceneExcelConfigData.json",
    "CityConfigData": "ExcelBinOutput/CityConfigData.json",
    "WorldAreaConfigData": "ExcelBinOutput/WorldAreaConfigData.json",
}
RELATION_SPECS = (
    {
        "table": "AnimalCodexExcelConfigData", "field": "describeId",
        "relation_kind": "animal_describe_id_candidate", "dynamic": True,
        "mapping_basis": "type_partition_and_exact_target_field_match",
    },
    {
        "table": "AnimalCodexExcelConfigData", "field": "pushTipsCodexId",
        "relation_kind": "animal_push_tips_codex_id_candidate", "target_table": "PushTipsCodexExcelConfigData",
        "target_field": "id", "mapping_basis": "explicit_Codex_ID_field_and_exact_lookup_table_field",
    },
    {
        "table": "ViewCodexExcelConfigData", "field": "cityId",
        "relation_kind": "view_city_id_candidate", "target_table": "CityConfigData",
        "target_field": "cityId", "mapping_basis": "explicit_field_and_exact_CityConfig_native_field",
    },
    {
        "table": "ViewCodexExcelConfigData", "field": "gadgetId",
        "relation_kind": "view_gadget_id_candidate", "target_table": "GadgetExcelConfigData",
        "target_field": "id", "mapping_basis": "explicit_field_and_exact_Gadget_native_field",
    },
    {
        "table": "ViewCodexExcelConfigData", "field": "sceneId",
        "relation_kind": "view_scene_id_candidate", "target_table": "SceneExcelConfigData",
        "target_field": "id", "mapping_basis": "explicit_field_and_exact_Scene_native_field",
    },
    {
        "table": "ViewCodexExcelConfigData", "field": "worldAreaId",
        "relation_kind": "view_world_area_id_candidate", "target_table": "WorldAreaConfigData",
        "target_field": "id", "mapping_basis": "explicit_field_and_exact_WorldArea_native_field",
    },
)
TEXT_OWNER_FIELDS = {
    "AnimalCodexExcelConfigData": (("descTextMapHash", "description"),),
    "ViewCodexExcelConfigData": (("descTextMapHash", "description"), ("nameTextMapHash", "name")),
}
ARRAY_FIELDS = {
    "AnimalCodexExcelConfigData": ("BLDKMKFAODH", "LLKLHHFKHBH", "NJLOJMHCLDN"),
    "ViewCodexExcelConfigData": (),
}
UNRESOLVED_ID_LIKE_FIELDS = {"ViewCodexExcelConfigData": ("configId", "groupId")}


class WorldArchivePhase4E3ContractError(RuntimeError):
    """Pinned source/audit/manifest data is inconsistent."""


def _fail(message: str) -> None:
    raise WorldArchivePhase4E3ContractError(message)


def _strict_bytes(raw: bytes, label: str) -> Any:
    def reject(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail(f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        _fail(f"non-standard JSON constant in {label}: {value}")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=reject, parse_constant=reject_constant)
    except WorldArchivePhase4E3ContractError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot parse {label}: {exc}")


def strict_json_file(path: Path, label: str) -> Any:
    try:
        return _strict_bytes(path.read_bytes(), label)
    except OSError as exc:
        _fail(f"cannot read {label}: {exc}")


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", "--no-optional-locks", "-C", str(root), *args], check=True, capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        _fail(f"cannot inspect pinned World Archive source: {exc}")


def verify_source_pin(root: Path) -> None:
    root = root.resolve()
    if not root.is_dir() or Path(_git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        _fail("World Archive source checkout root mismatch")
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        _fail("World Archive source checkout is not clean")
    if _git(root, "rev-parse", "HEAD") != EXPECTED_COMMIT or _git(root, "rev-parse", "HEAD^{tree}") != EXPECTED_TREE:
        _fail("World Archive source checkout pin mismatch")


def _audit_reports(repo_root: Path) -> dict[str, Any]:
    base = repo_root.resolve() / "reports/phase4e1-world-archive"
    reports: dict[str, Any] = {}
    for name, expected in AUDIT_ARTIFACTS.items():
        try:
            raw = (base / name).read_bytes()
        except OSError as exc:
            _fail(f"cannot read Phase 4E1 artifact {name}: {exc}")
        if sha256_bytes(raw) != expected:
            _fail(f"Phase 4E1 artifact digest mismatch: {name}")
        value = _strict_bytes(raw, name)
        if not isinstance(value, dict) or value.get("audit_version") != "4E1.1":
            _fail(f"Phase 4E1 artifact contract mismatch: {name}")
        reports[name] = value
    return reports


def _pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _scope(path: str, pointer: str, row: int | None = None, field: str | None = None, array_index: int | None = None) -> dict[str, Any]:
    return {"array_index": array_index, "audit_run": f"phase4e1-world-archive-audit:{EXPECTED_COMMIT}", "field": field, "path": path, "pointer": pointer, "row": row, "snapshot": EXPECTED_SNAPSHOT_ID}


def _field_scope(table: str, row: int, field: str) -> dict[str, Any]:
    return _scope(PRIMARY_PATHS[table], f"/{row}/{_pointer_token(field)}", row, field)


def _row_scope(table: str, row: int) -> dict[str, Any]:
    return _scope(PRIMARY_PATHS[table], f"/{row}", row)


def _array_scope(table: str, row: int, field: str, index: int) -> dict[str, Any]:
    return _scope(PRIMARY_PATHS[table], f"/{row}/{_pointer_token(field)}/{index}", row, field, index)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def _normalized_value(value: Any) -> str | None:
    value = _positive_int(value)
    return None if value is None else str(value)


def _raw_checksum(value: Any) -> str:
    return sha256_text(canonical_json(value))


def _set_digest(items: list[dict[str, Any]]) -> str:
    return sha256_text(canonical_json(sorted(canonical_json(item) for item in items)))


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    try:
        raw = manifest_path.read_bytes()
    except OSError as exc:
        _fail(f"cannot read World Archive Phase 4E3 manifest: {exc}")
    if EXPECTED_MANIFEST_SHA256 and sha256_bytes(raw) != EXPECTED_MANIFEST_SHA256:
        _fail("World Archive Phase 4E3 manifest SHA-256 mismatch")
    value = _strict_bytes(raw, "World Archive Phase 4E3 manifest")
    if not isinstance(value, dict):
        _fail("World Archive Phase 4E3 manifest must be an object")
    required = {"schema_version", "manifest_id", "source", "versions", "audit_binding", "boundary", "identity", "files", "selected_rows", "expected"}
    if set(value) != required or value["schema_version"] != 2 or value["manifest_id"] != MANIFEST_ID or value["versions"] != VERSIONS:
        _fail("World Archive Phase 4E3 manifest identity/version mismatch")
    source = value["source"]
    expected_source = {"source_id": "anime-game-data", "upstream": "https://github.com/DimbreathBot/AnimeGameData", "release_label": "7.0", "build": EXPECTED_BUILD, "commit": EXPECTED_COMMIT, "tree": EXPECTED_TREE, "observed_at": EXPECTED_OBSERVED_AT, "snapshot_id": EXPECTED_SNAPSHOT_ID}
    if source != expected_source:
        _fail("World Archive Phase 4E3 source identity mismatch")
    boundary = value["boundary"]
    wanted_boundary = {"family": FAMILY, "family_contract_version": FAMILY_CONTRACT_VERSION, "selected_rows": {key: list(rows) for key, rows in SELECTED_ROWS.items()}, "selected_primary_files": 2, "selected_textmap_files": 6, "full_world_archive_scan": True, "corpus_complete": True, "coverage": EXPECTED_COVERAGE, "adjacent_inventory_only": True}
    if boundary != wanted_boundary:
        _fail("World Archive Phase 4E3 full boundary mismatch")
    identity = value["identity"]
    wanted_identity = {"raw_evidence_identity": "snapshot_parser_build_ingest_run_source_file_source_record_row_field_occurrence", "logical_identity_created": False, "native_id_identity_effect": "none_table_scoped_locator_only", "text_hash_identity_effect": "none_owner_locale_candidate_locator_only", "asset_identity_effect": "none_raw_reference_only", "checksum_identity_effect": "none_change_detection_only", "context_scope": "single_physical_world_archive_row_container_only"}
    if identity != wanted_identity or value["audit_binding"] != AUDIT_ARTIFACTS:
        _fail("World Archive Phase 4E3 identity/audit binding mismatch")
    files = value["files"]
    if not isinstance(files, list) or {item.get("relative_path") for item in files if isinstance(item, dict)} != set((*PRIMARY_PATHS.values(), *(path for paths in TEXTMAP_PATHS.values() for path in paths.values()))):
        _fail("World Archive Phase 4E3 file boundary mismatch")
    selected = value["selected_rows"]
    if not isinstance(selected, list) or len(selected) != 852:
        _fail("World Archive Phase 4E3 selected row count mismatch")
    expected_keys = {"table", "row_index", "pointer", "native_locator_field", "native_locator_value", "raw_sha256"}
    expected_tables = (*(["AnimalCodexExcelConfigData"] * 584), *(["ViewCodexExcelConfigData"] * 268))
    for index, item in enumerate(selected):
        if not isinstance(item, dict) or set(item) != expected_keys:
            _fail(f"World Archive Phase 4E3 selected row spec shape mismatch: {index}")
        expected_table = expected_tables[index]
        if (
            item["table"] != expected_table
            or item["row_index"] != (index if index < 584 else index - 584)
            or item["pointer"] != f"/{item['row_index']}"
            or item["native_locator_field"] != "id"
            or not isinstance(item["native_locator_value"], str)
            or not item["native_locator_value"].isdigit()
            or not isinstance(item["raw_sha256"], str)
            or len(item["raw_sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in item["raw_sha256"])
        ):
            _fail(f"World Archive Phase 4E3 selected row coordinate/spec mismatch: {index}")
    expected = value.get("expected")
    expected_keys = {
        "counts", "status_counts", "occurrence_set_digests",
        "selected_table_counts", "runtime_business_seals",
    }
    if not isinstance(expected, dict) or set(expected) != expected_keys:
        _fail("World Archive Phase 4E3 expected seal shape mismatch")
    runtime = expected["runtime_business_seals"]
    if not isinstance(runtime, dict) or runtime.get("algorithm") != "canonical-sql-rowset-sha256-v1":
        _fail("World Archive Phase 4E3 runtime business seal mismatch")
    return value


def _verify_file(file_value: Mapping[str, Any], root: Path, relative: str) -> dict[str, Any]:
    if not isinstance(file_value, Mapping):
        _fail(f"invalid source inventory entry: {relative}")
    path = root / relative
    try:
        raw = path.read_bytes()
    except OSError as exc:
        _fail(f"cannot read selected source file {relative}: {exc}")
    expected_count = int(file_value["source_records"])
    value = _strict_bytes(raw, relative)
    actual_count = len(value) if isinstance(value, (list, dict)) else -1
    if actual_count != expected_count or sha256_bytes(raw) != file_value["sha256"] or len(raw) != file_value["size_bytes"] or _git(root, "rev-parse", f"HEAD:{relative}") != file_value["blob_oid"]:
        _fail(f"World Archive source file metadata/count mismatch: {relative}")
    return {"relative_path": relative, "format": file_value["format"], "role": file_value["role"], "source_records": expected_count, "size_bytes": file_value["size_bytes"], "sha256": file_value["sha256"], "blob_oid": file_value["blob_oid"], "value": value}


def _dynamic_describe_target(row: Any) -> tuple[str | None, str | None]:
    if not isinstance(row, dict):
        return None, None
    if row.get("type") == "CODEX_MONSTER":
        return "MonsterDescribeExcelConfigData", "id"
    if "type" not in row:
        return "MonsterExcelConfigData", "id"
    return None, None


def derive_source_contract(source_root: str | Path, repo_root: str | Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    root = Path(source_root).resolve()
    verify_source_pin(root)
    reports = _audit_reports(Path(repo_root))
    inventory = reports["phase4e1-source-inventory.json"]
    inventory_files = {item["relative_path"]: item for item in inventory.get("files", []) if isinstance(item, dict)}
    selected_paths = [*PRIMARY_PATHS.values(), *(path for paths in TEXTMAP_PATHS.values() for path in paths.values())]
    if set(inventory_files) != set(selected_paths):
        _fail("Phase 4E1 selected file set changed")
    manifest_files = {item.get("relative_path"): item for item in manifest.get("files", []) if isinstance(item, Mapping)}
    if set(manifest_files) != set(selected_paths):
        _fail("Phase 4E3 manifest selected file set mismatch")
    loaded = {path: _verify_file(inventory_files[path], root, path) for path in selected_paths}
    for path, item in loaded.items():
        spec = manifest_files[path]
        for key in ("relative_path", "format", "role", "source_records", "size_bytes", "sha256", "blob_oid"):
            if spec.get(key) != item[key]:
                _fail(f"World Archive manifest/source file mismatch: {path}")

    primary: dict[str, list[Any]] = {}
    for table, path in PRIMARY_PATHS.items():
        value = loaded[path]["value"]
        if not isinstance(value, list) or len(value) != len(SELECTED_ROWS[table]) or any(not isinstance(row, dict) for row in value):
            _fail(f"World Archive primary table shape/count mismatch: {table}")
        primary[table] = value
    textmaps = {(locale, kind): loaded[path]["value"] for locale, paths in TEXTMAP_PATHS.items() for kind, path in paths.items()}
    if any(not isinstance(value, dict) for value in textmaps.values()):
        _fail("World Archive TextMap shard shape mismatch")

    rows: list[dict[str, Any]] = []
    raw_fields: list[dict[str, Any]] = []
    source_records: list[dict[str, Any]] = []
    selected_specs: list[dict[str, Any]] = []
    for table in PRIMARY_PATHS:
        for row_index, raw_row in enumerate(primary[table]):
            native = _normalized_value(raw_row.get("id"))
            if native is None:
                _fail(f"invalid World Archive native locator: {table}/{row_index}")
            pointer = f"/{row_index}"
            checksum = _raw_checksum(raw_row)
            rows.append({"table": table, "relative_path": PRIMARY_PATHS[table], "row_index": row_index, "pointer": pointer, "native_locator_value": native, "raw_checksum": checksum, "raw_value": raw_row})
            selected_specs.append({"table": table, "row_index": row_index, "pointer": pointer, "native_locator_field": "id", "native_locator_value": native, "raw_sha256": checksum})
            source_records.append({"relative_path": PRIMARY_PATHS[table], "source_pointer": pointer, "record_kind": "world_archive_primary_row", "native_id": native, "raw_checksum": checksum, "raw_value": raw_row})
            for field, raw_value in raw_row.items():
                raw_fields.append({"table": table, "row_index": row_index, "source_pointer": f"/{row_index}/{_pointer_token(field)}", "field_name": field, "field_kind": "array" if isinstance(raw_value, list) else "scalar", "raw_value": raw_value})

    probe_values = {table: strict_json_file(root / path, path) for table, path in RELATION_PROBE_PATHS.items()}
    probe_indexes: dict[str, dict[str, list[int]]] = {}
    for table, value in probe_values.items():
        if not isinstance(value, list):
            _fail(f"relationship probe is not an array: {table}")
        field = "cityId" if table == "CityConfigData" else "id"
        index: dict[str, list[int]] = defaultdict(list)
        for position, row in enumerate(value):
            if isinstance(row, dict):
                normalized = _normalized_value(row.get(field))
                if normalized is not None:
                    index[normalized].append(position)
        probe_indexes[table] = index

    relationships: list[dict[str, Any]] = []
    for spec in RELATION_SPECS:
        table, field = spec["table"], spec["field"]
        for row_index, row in enumerate(primary[table]):
            raw_value = row.get(field) if field in row else None
            value = _normalized_value(raw_value)
            if field not in row or raw_value is None:
                status = "missing"
                target_table, target_field = (spec.get("target_table"), spec.get("target_field")) if not spec.get("dynamic") else _dynamic_describe_target(row)
            elif value is None:
                status = "invalid"
                target_table, target_field = (spec.get("target_table"), spec.get("target_field")) if not spec.get("dynamic") else _dynamic_describe_target(row)
            else:
                target_table, target_field = (spec.get("target_table"), spec.get("target_field")) if not spec.get("dynamic") else _dynamic_describe_target(row)
                if target_table is None or target_field is None:
                    status = "unresolved"
                else:
                    positions = probe_indexes[target_table].get(value, [])
                    status = "missing" if not positions and (field not in row or raw_value is None) else ("dangling" if not positions else "ambiguous" if len(positions) > 1 else "resolved_unique")
            positions = [] if target_table is None or target_field is None or value is None else probe_indexes[target_table].get(value, [])
            candidate_key = None if target_table is None or target_field is None or value is None else {"field": target_field, "table": target_table, "value": value}
            relationships.append({"table": table, "row_index": row_index, "source_pointer": f"/{row_index}/{_pointer_token(field)}", "field_name": field, "relation_kind": spec["relation_kind"], "raw_value": raw_value, "candidate_key": candidate_key, "resolution_status": status, "target_table": target_table, "target_field": target_field, "candidate_scopes": [_scope(RELATION_PROBE_PATHS[target_table], f"/{position}", position) for position in positions], "mapping_basis": spec["mapping_basis"]})

    opaque: list[dict[str, Any]] = []
    for table, fields in ARRAY_FIELDS.items():
        for row_index, row in enumerate(primary[table]):
            for field in fields:
                raw_value = row.get(field)
                if not isinstance(raw_value, list):
                    _fail(f"World Archive required opaque array field shape changed: {table}.{field}")
                for array_index, item in enumerate(raw_value):
                    opaque.append({"table": table, "row_index": row_index, "source_pointer": f"/{row_index}/{_pointer_token(field)}/{array_index}", "field_name": field, "array_index": array_index, "raw_value": item, "reason": "obfuscated_array_position_has_no_authoritative_mapping"})
    for table, fields in UNRESOLVED_ID_LIKE_FIELDS.items():
        for row_index, row in enumerate(primary[table]):
            for field in fields:
                if field not in row:
                    _fail(f"World Archive opaque field unexpectedly absent: {table}.{field}")
                opaque.append({"table": table, "row_index": row_index, "source_pointer": f"/{row_index}/{_pointer_token(field)}", "field_name": field, "array_index": None, "raw_value": row[field], "reason": "id_like_field_has_no_authoritative_target_mapping_in_boundary"})

    owners: list[dict[str, Any]] = []
    resolutions: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for table, fields in TEXT_OWNER_FIELDS.items():
        observed = {field for row in primary[table] for field in row if field.endswith("TextMapHash")}
        expected = {field for field, _role in fields}
        if observed != expected:
            _fail(f"World Archive explicit TextMap owner field set changed: {table}")
        for row_index, row in enumerate(primary[table]):
            for field, role in fields:
                raw_hash = row.get(field)
                text_hash = _normalized_value(raw_hash)
                owner_key = f"{table}:{row_index}:{field}"
                owner_pointer = _field_scope(table, row_index, field)["pointer"]
                owners.append({"owner_key": owner_key, "table": table, "row_index": row_index, "source_pointer": owner_pointer, "field_name": field, "field_role": role, "textmap_hash": text_hash or str(raw_hash), "raw_value": raw_hash})
                for locale in LOCALES:
                    local: list[dict[str, Any]] = []
                    if text_hash is not None:
                        for kind in ("main", "medium"):
                            mapping = textmaps[(locale, kind)]
                            if text_hash in mapping:
                                value = mapping[text_hash]
                                candidate = {"owner_key": owner_key, "locale": locale, "shard_kind": kind, "source_relative_path": TEXTMAP_PATHS[locale][kind], "source_pointer": f"/{_pointer_token(text_hash)}", "raw_text": value, "normalized_text": normalize_text(value) if isinstance(value, str) else None, "raw_checksum": sha256_text(value) if isinstance(value, str) else None, "normalized_checksum": sha256_text(normalize_text(value)) if isinstance(value, str) else None}
                                local.append(candidate)
                                if isinstance(value, str):
                                    candidates.append(candidate)
                                    record = {"relative_path": candidate["source_relative_path"], "source_pointer": candidate["source_pointer"], "record_kind": "world_archive_text_candidate", "native_id": text_hash, "raw_checksum": candidate["raw_checksum"], "raw_value": value}
                                    if not any(item["relative_path"] == record["relative_path"] and item["source_pointer"] == record["source_pointer"] for item in source_records):
                                        source_records.append(record)
                    valid = [item for item in local if item["raw_checksum"] is not None]
                    main = next((item for item in valid if item["shard_kind"] == "main"), None)
                    medium = next((item for item in valid if item["shard_kind"] == "medium"), None)
                    if text_hash is None:
                        status = "invalid"
                    elif any(item["raw_checksum"] is None for item in local):
                        status = "invalid"
                    elif main and medium:
                        status = "both_equal" if main["raw_text"] == medium["raw_text"] else "both_conflict"
                    elif main:
                        status = "unique_main"
                    elif medium:
                        status = "unique_medium"
                    else:
                        status = "missing"
                    resolutions.append({"owner_key": owner_key, "table": table, "row_index": row_index, "source_pointer": owner_pointer, "locale": locale, "textmap_hash": text_hash or str(raw_hash), "resolution_status": status, "raw_text": None if status not in {"unique_main", "unique_medium", "both_equal"} else (main or medium)["raw_text"], "normalized_text": None if status not in {"unique_main", "unique_medium", "both_equal"} else (main or medium)["normalized_text"], "raw_checksum": None if status not in {"unique_main", "unique_medium", "both_equal"} else (main or medium)["raw_checksum"], "normalized_checksum": None if status not in {"unique_main", "unique_medium", "both_equal"} else (main or medium)["normalized_checksum"], "candidate_scopes": [{**_scope(item["source_relative_path"], item["source_pointer"]), "row": None} for item in local]})

    assets: list[dict[str, Any]] = []
    for table, fields in (("AnimalCodexExcelConfigData", (("modelPath", "model_path"),)), ("ViewCodexExcelConfigData", (("image", "image"),))):
        for row_index, row in enumerate(primary[table]):
            for field, role in fields:
                value = row.get(field) if field in row else None
                if not isinstance(value, str) or value == "":
                    status = "missing" if value is None or value == "" else "invalid"
                    value = None
                else:
                    status = "observed"
                assets.append({"table": table, "row_index": row_index, "source_pointer": f"/{row_index}/{_pointer_token(field)}", "field_name": field, "asset_kind": role, "raw_reference": value, "reference_status": status})

    collections = {"rows": rows, "raw_fields": raw_fields, "relationships": relationships, "opaque": opaque, "text_owners": owners, "text_resolutions": resolutions, "text_candidates": candidates, "assets": assets}
    counts = {key: len(value) for key, value in collections.items()} | {"files": len(selected_paths), "source_records": len(source_records)}
    status_counts = {
        "relationships": _counts(relationships, "resolution_status"),
        "opaque": _counts(opaque, "resolution_status", default="opaque_unresolved"),
        "text_resolutions": _counts(resolutions, "resolution_status"),
        "assets": _counts(assets, "reference_status"),
    }
    digests = {key: _set_digest(value) for key, value in collections.items()}
    result = {"files": [{key: item[key] for key in ("relative_path", "format", "role", "source_records", "size_bytes", "sha256", "blob_oid")} for item in loaded.values()], **collections, "source_records": source_records, "counts": counts, "status_counts": status_counts, "occurrence_set_digests": digests, "selected_table_counts": {table: len(rows) for table, rows in SELECTED_ROWS.items()}}
    if list(manifest.get("selected_rows", ())) != selected_specs:
        _fail("World Archive Phase 4E3 selected_rows differ from independently derived selected_specs")
    expected = manifest.get("expected")
    source_expected = {
        key: result[key]
        for key in ("counts", "status_counts", "occurrence_set_digests", "selected_table_counts")
    }
    if isinstance(expected, Mapping) and expected and any(
        expected.get(key) != value for key, value in source_expected.items()
    ):
        _fail("World Archive Phase 4E3 derived contract differs from sealed manifest")
    return result


def _counts(items: list[dict[str, Any]], field: str, default: str | None = None) -> dict[str, int]:
    result: dict[str, int] = defaultdict(int)
    for item in items:
        value = item.get(field, default)
        if value is not None:
            result[str(value)] += 1
    return dict(sorted(result.items()))


def expected_identity(manifest: Mapping[str, Any]) -> dict[str, str]:
    parser_build_id = digest_id("pb-world-archive-phase4e3", canonical_json({"versions": manifest["versions"], "manifest_id": manifest["manifest_id"]}))
    config_checksum = sha256_text(canonical_json({"manifest_id": manifest["manifest_id"], "versions": manifest["versions"], "coverage": EXPECTED_COVERAGE, "corpus_complete": True, "selected_rows": manifest["boundary"]["selected_rows"], "audit_binding": manifest["audit_binding"]}))
    ingest_run_id = digest_id("ir-world-archive-phase4e3", EXPECTED_SNAPSHOT_ID, parser_build_id, config_checksum)
    return {"snapshot_id": EXPECTED_SNAPSHOT_ID, "parser_build_id": parser_build_id, "config_checksum": config_checksum, "ingest_run_id": ingest_run_id}


def occurrence_id(kind: str, identity: Mapping[str, str], *parts: Any) -> str:
    return digest_id(f"world-archive-{kind}", identity["snapshot_id"], identity["parser_build_id"], identity["ingest_run_id"], *parts)


__all__ = ["AUDIT_ARTIFACTS", "EXPECTED_BUILD", "EXPECTED_COMMIT", "EXPECTED_COVERAGE", "EXPECTED_CORPUS_COMPLETE", "EXPECTED_MANIFEST_SHA256", "EXPECTED_OBSERVED_AT", "EXPECTED_SNAPSHOT_ID", "EXPECTED_TREE", "FAMILY", "FAMILY_CONTRACT_VERSION", "MANIFEST_ID", "PRIMARY_PATHS", "RUN_FAMILY", "SELECTED_ROWS", "TEXTMAP_PATHS", "VERSIONS", "derive_source_contract", "expected_identity", "load_manifest", "occurrence_id"]
