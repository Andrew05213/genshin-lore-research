"""Frozen manifest contract for the Phase 4C5B full Character boundary.

The manifest is intentionally family-specific.  It describes the fixed 15
source files and the coordinates which make up the Character/Fetter raw
evidence closure; it is not a logical Character model and it does not make
text, native ids, or voice slots identity keys.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .util import canonical_json


EXPECTED_MANIFEST_ID = "anime-game-data-7.0-phase4c5b-character-fetter-full"
EXPECTED_COMMIT = "26df1dfbdf05a82bbb1d97506859f3e1c40718d8"
EXPECTED_TREE = "d891ef1d95fdd6c27a1da8a32ea7a61b483647a6"
EXPECTED_BUILD = "CNRELWin7.0.0_R47482070_S47579390_D47579390"
EXPECTED_SNAPSHOT_ID = "snap:48f434afe1049d73a502e3f2b24b67e575436300"
EXPECTED_COVERAGE = "declared_character_fetter_source_closure_complete"
EXPECTED_FILE_COUNT = 15
EXPECTED_TABLE_ROW_COUNTS = {
    "AvatarExcelConfigData": 165,
    "FetterInfoExcelConfigData": 122,
    "FetterStoryExcelConfigData": 958,
    "FettersExcelConfigData": 9032,
}
EXPECTED_TABLES = {
    "AvatarExcelConfigData": "id",
    "FetterInfoExcelConfigData": "fetterId",
    "FetterStoryExcelConfigData": "fetterId",
    "FettersExcelConfigData": "fetterId",
}
EXPECTED_TEXT_FIELDS = {
    "AvatarExcelConfigData": ("descTextMapHash", "nameTextMapHash"),
    "FetterInfoExcelConfigData": (
        "avatarConstellationAfterTextMapHash",
        "avatarConstellationBeforTextMapHash",
        "avatarDetailTextMapHash",
        "avatarNativeTextMapHash",
        "avatarTitleTextMapHash",
        "avatarVisionAfterTextMapHash",
        "avatarVisionBeforTextMapHash",
        "cvChineseTextMapHash",
        "cvEnglishTextMapHash",
        "cvJapaneseTextMapHash",
        "cvKoreanTextMapHash",
    ),
    "FetterStoryExcelConfigData": (
        "storyContext2TextMapHash",
        "storyContextTextMapHash",
        "storyTitle2TextMapHash",
        "storyTitleLockedTextMapHash",
        "storyTitleTextMapHash",
    ),
    "FettersExcelConfigData": (
        "voiceFileTextTextMapHash",
        "voiceTitleLockedTextMapHash",
        "voiceTitleTextMapHash",
    ),
}
EXPECTED_PATHS = tuple(sorted(
    (
        "BinOutput/Voice/Items/1248787199.json",
        "BinOutput/Voice/Items/1560160621.json",
        "BinOutput/Voice/Items/2939652103.json",
        "BinOutput/Voice/Items/750982387.json",
        "BinOutput/Voice/Lut/Lut.json",
        "ExcelBinOutput/AvatarExcelConfigData.json",
        "ExcelBinOutput/FetterInfoExcelConfigData.json",
        "ExcelBinOutput/FetterStoryExcelConfigData.json",
        "ExcelBinOutput/FettersExcelConfigData.json",
        "TextMap/TextMapCHS.json",
        "TextMap/TextMapEN.json",
        "TextMap/TextMapJP.json",
        "TextMap/TextMap_MediumCHS.json",
        "TextMap/TextMap_MediumEN.json",
        "TextMap/TextMap_MediumJP.json",
    )
))
TEXTMAP_PATHS = {
    "CHS": {"main": "TextMap/TextMapCHS.json", "medium": "TextMap/TextMap_MediumCHS.json"},
    "EN": {"main": "TextMap/TextMapEN.json", "medium": "TextMap/TextMap_MediumEN.json"},
    "JP": {"main": "TextMap/TextMapJP.json", "medium": "TextMap/TextMap_MediumJP.json"},
}
ITEM_PATHS = tuple(path for path in EXPECTED_PATHS if "/Items/" in path)
LUT_PATH = "BinOutput/Voice/Lut/Lut.json"
LOCALES = ("CHS", "EN", "JP")

EXPECTED_COUNTS = {
    "selected_files": 15,
    "source_records": 76414,
    "rows": 10277,
    "raw_slots": 78179,
    "condition_slots": 39124,
    "condition_nonempty": 5695,
    "condition_empty": 33429,
    "opaque_slots": 39055,
    "relations": 52702,
    "avatar_relations": 10112,
    "text_owner_occurrences": 33558,
    "voice_attachments": 9032,
    "text_resolutions": 100674,
    "text_candidates": 65387,
    "voice_containers": 375,
    "audio_variants": 9611,
    "distinct_audio_paths": 9600,
    "distinct_character_child_selectors": 128,
    "unattached_lut_entries": 8,
    "unattached_children": 105,
}
EXPECTED_STATUS_COUNTS = {
    "relations": {"resolved": 43670, "resolved_unique_container": 9032},
    "text_resolutions": {
        "unique_main": 0, "unique_medium": 65387,
        "both_equal": 0, "both_conflict": 0, "missing": 35287,
    },
    "raw_slots": {"observed": 39124, "opaque_unresolved": 38890, "observed_equal": 165},
    "condition_shapes": {"empty": 33429, "nonempty": 5695},
    "voice_containers": {"resolved_unique_container": 375},
    "audio_paths": {"observed": 9611},
    "character_child_selectors": {"observed_unresolved": 9611},
}
EXPECTED_OWNER_FIELD_COUNTS = {
    "AvatarExcelConfigData.descTextMapHash": 165,
    "AvatarExcelConfigData.nameTextMapHash": 165,
    "FetterInfoExcelConfigData.avatarConstellationAfterTextMapHash": 122,
    "FetterInfoExcelConfigData.avatarConstellationBeforTextMapHash": 122,
    "FetterInfoExcelConfigData.avatarDetailTextMapHash": 122,
    "FetterInfoExcelConfigData.avatarNativeTextMapHash": 122,
    "FetterInfoExcelConfigData.avatarTitleTextMapHash": 122,
    "FetterInfoExcelConfigData.avatarVisionAfterTextMapHash": 122,
    "FetterInfoExcelConfigData.avatarVisionBeforTextMapHash": 122,
    "FetterInfoExcelConfigData.cvChineseTextMapHash": 122,
    "FetterInfoExcelConfigData.cvEnglishTextMapHash": 122,
    "FetterInfoExcelConfigData.cvJapaneseTextMapHash": 122,
    "FetterInfoExcelConfigData.cvKoreanTextMapHash": 122,
    "FetterStoryExcelConfigData.storyContext2TextMapHash": 958,
    "FetterStoryExcelConfigData.storyContextTextMapHash": 958,
    "FetterStoryExcelConfigData.storyTitle2TextMapHash": 958,
    "FetterStoryExcelConfigData.storyTitleLockedTextMapHash": 958,
    "FetterStoryExcelConfigData.storyTitleTextMapHash": 958,
    "FettersExcelConfigData.voiceFileTextTextMapHash": 9032,
    "FettersExcelConfigData.voiceTitleLockedTextMapHash": 9032,
    "FettersExcelConfigData.voiceTitleTextMapHash": 9032,
}
EXPECTED_VERSIONS = {
    "manifest_contract": "character-phase4c5b-full-boundary-manifest-v1",
    "parser": "character-phase4c5b-full-boundary-parser-v1",
    "projection": "character-phase4c5b-full-boundary-projection-v1",
    "textmap_resolution_rule": "owner-locale-main-medium-occurrence-v1",
    "voice_chain_rule": "fetter-row-lut-items-container-child-v1",
    "identity_rule": "character-family-raw-occurrence-v1",
}
EXPECTED_AUDIT_BINDING = {
    "audit_version": "4C1.1",
    "manifest": {
        "relative_path": "reports/phase4c1-character/phase4c1-audit-manifest.json",
        "sha256": "3a357f45b7f97804d465dea2fc0ca7ecb3ed15811c19bf9772be2f0d7077d3f7",
    },
}


class Phase4C5BManifestError(RuntimeError):
    pass


@dataclass(frozen=True)
class Phase4C5BFileSpec:
    relative_path: str
    format: str
    size_bytes: int
    sha256: str
    blob_oid: str
    role: str


@dataclass(frozen=True)
class CharacterPhase4C5BManifest:
    path: Path
    raw_sha256: str
    manifest_id: str
    source: dict[str, Any]
    snapshot_id: str
    versions: dict[str, str]
    audit_binding: dict[str, Any]
    boundary: dict[str, Any]
    identity: dict[str, Any]
    files: tuple[Phase4C5BFileSpec, ...]
    tables: dict[str, dict[str, Any]]
    closure: dict[str, Any]
    expected: dict[str, Any]
    raw: dict[str, Any]


HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _fail(message: str) -> None:
    raise Phase4C5BManifestError(message)


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def strict_json_loads(raw: str | bytes, *, label: str = "JSON") -> Any:
    try:
        return json.loads(raw, object_pairs_hook=_strict_pairs)
    except Phase4C5BManifestError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Phase4C5BManifestError(f"invalid {label}: {exc}") from exc


def strict_json_load(path: str | Path, *, label: str | None = None) -> Any:
    item = Path(path)
    try:
        raw = item.read_bytes()
    except OSError as exc:
        raise Phase4C5BManifestError(f"cannot read {label or item}: {exc}") from exc
    return strict_json_loads(raw, label=label or str(item))


def _exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        _fail(f"{label} keys mismatch")
    return value


def _hex(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        _fail(f"{label} must be a lower-case SHA-256")
    return value


def _path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        _fail(f"{label} must be a POSIX relative path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or pure.as_posix() != value or any(part in {"", ".", ".."} for part in pure.parts):
        _fail(f"{label} is not canonical")
    return value


def _coordinate_digest(value: list[Any]) -> str:
    encoded = sorted(canonical_json(item) for item in value)
    return hashlib.sha256(canonical_json(encoded).encode("utf-8")).hexdigest()


def _validate_coordinate_list(value: Any, label: str, *, shape: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(f"{label} must be an array")
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, list):
            _fail(f"{label}[{index}] must be an exact tuple array")
        lengths = {"table": 5, "row": 3, "owner": 2, "candidate": 5, "attachment": 3, "child": 4}
        if shape not in lengths or len(item) != lengths[shape]:
            _fail(f"{label}[{index}] tuple length mismatch")
        if shape == "table":
            relative, pointer, native, avatar, checksum = item
            if not all(isinstance(value, str) for value in (relative, pointer, native, avatar, checksum)) or HEX64.fullmatch(checksum) is None:
                _fail(f"{label}[{index}] table tuple types mismatch")
        elif shape in {"row", "owner", "attachment"}:
            relative, pointer = item[0], item[1]
            if not isinstance(relative, str) or not isinstance(pointer, str):
                _fail(f"{label}[{index}] coordinate types mismatch")
            if shape == "row" and (not isinstance(item[2], str) or HEX64.fullmatch(item[2]) is None):
                _fail(f"{label}[{index}] row checksum mismatch")
            if shape == "attachment" and not isinstance(item[2], str):
                _fail(f"{label}[{index}] attachment slot mismatch")
        elif shape == "candidate":
            owner, relative, pointer, locale, kind = item
            if isinstance(owner, bool) or not isinstance(owner, int) or not isinstance(relative, str) or not isinstance(pointer, str) or locale not in LOCALES or kind not in {"main", "medium"}:
                _fail(f"{label}[{index}] candidate tuple types mismatch")
        else:
            relative, pointer, voice_key, array_index = item
            if not isinstance(relative, str) or not isinstance(pointer, str) or not isinstance(voice_key, str) or isinstance(array_index, bool) or not isinstance(array_index, int) or array_index < 0:
                _fail(f"{label}[{index}] child tuple types mismatch")
        _path(relative, f"{label}[{index}].relative_path")
        if not pointer.startswith("/") or len(pointer) < 2:
            _fail(f"{label}[{index}].source_pointer is invalid")
        marker = canonical_json(item)
        if marker in seen:
            _fail(f"{label}[{index}] duplicate coordinate")
        seen.add(marker)
    return value


def load_character_phase4c5b_manifest(path: str | Path) -> CharacterPhase4C5BManifest:
    manifest_path = Path(path).resolve()
    try:
        raw_bytes = manifest_path.read_bytes()
    except OSError as exc:
        raise Phase4C5BManifestError(f"cannot load Phase 4C5B manifest: {exc}") from exc
    raw = strict_json_loads(raw_bytes, label="Phase 4C5B manifest")
    root = _exact(raw, {
        "schema_version", "manifest_id", "source", "snapshot_id", "versions",
        "phase4c1_audit_binding", "boundary", "identity", "files", "tables",
        "closure", "expected",
    }, "manifest")
    if root["schema_version"] != 1 or root["manifest_id"] != EXPECTED_MANIFEST_ID:
        _fail("unsupported Phase 4C5B manifest identity")
    source = _exact(root["source"], {"source_id", "upstream", "release_label", "build", "commit", "tree", "observed_at"}, "source")
    expected_source = {
        "source_id": "anime-game-data",
        "upstream": "https://github.com/DimbreathBot/AnimeGameData",
        "release_label": "7.0", "build": EXPECTED_BUILD,
        "commit": EXPECTED_COMMIT, "tree": EXPECTED_TREE,
        "observed_at": "2026-08-19T00:00:00+08:00",
    }
    if source != expected_source:
        _fail("Phase 4C5B source pin mismatch")
    if root["snapshot_id"] != EXPECTED_SNAPSHOT_ID:
        _fail("Phase 4C5B must reuse the parser-independent C2 snapshot")
    versions = _exact(root["versions"], set(EXPECTED_VERSIONS), "versions")
    if versions != EXPECTED_VERSIONS:
        _fail("Phase 4C5B parser/projection version mismatch")
    audit_binding = _exact(root["phase4c1_audit_binding"], {"audit_version", "manifest"}, "phase4c1_audit_binding")
    if audit_binding != EXPECTED_AUDIT_BINDING:
        _fail("Phase 4C1 audit binding mismatch")
    boundary = _exact(root["boundary"], {"locales", "coverage", "corpus_complete", "support_scope", "primary_scope", "textmap_scope", "voice_scope", "full_character_scan", "full_voice_scan"}, "boundary")
    if boundary != {
        "locales": list(LOCALES), "coverage": EXPECTED_COVERAGE,
        "corpus_complete": True,
        "support_scope": "all_primary_rows_explicit_owner_candidates_all_fetter_voice_physical_closure",
        "primary_scope": "all_rows_in_four_primary_files_in_fixed_snapshot",
        "textmap_scope": "explicit_owner_candidates_only_from_six_selected_shards",
        "voice_scope": "all_fetter_lut_records_and_items_containers_in_four_selected_files",
        "full_character_scan": False, "full_voice_scan": False,
    }:
        _fail("Phase 4C5B boundary mismatch")
    identity = _exact(root["identity"], {"raw_evidence_identity", "logical_identity_created", "native_id_scope", "textmap_hash_scope", "voice_slot_scope", "selector_scope", "checksum_semantics", "context_scope"}, "identity")
    if identity != {
        "raw_evidence_identity": "snapshot_ingest_run_source_file_source_record_occurrence",
        "logical_identity_created": False,
        "native_id_scope": "family_table_scoped_candidate_locator",
        "textmap_hash_scope": "owner_locale_shard_candidate_locator",
        "voice_slot_scope": "fetter_lut_items_physical_container_locator",
        "selector_scope": "container_child_observed_unresolved_no_foreign_key",
        "checksum_semantics": "change_detection_only",
        "context_scope": "voice_container_only_if_future_retrieval_is_authorized",
    }:
        _fail("Phase 4C5B identity boundary mismatch")
    files_raw = root["files"]
    if not isinstance(files_raw, list) or len(files_raw) != EXPECTED_FILE_COUNT:
        _fail("Phase 4C5B selected file count mismatch")
    files: list[Phase4C5BFileSpec] = []
    for index, value in enumerate(files_raw):
        item = _exact(value, {"relative_path", "format", "size_bytes", "sha256", "blob_oid", "role"}, f"files[{index}]")
        relative = _path(item["relative_path"], f"files[{index}].relative_path")
        if item["format"] != "json" or not isinstance(item["size_bytes"], int) or item["size_bytes"] < 0:
            _fail(f"files[{index}] format/size mismatch")
        _hex(item["sha256"], f"files[{index}].sha256")
        if not isinstance(item["blob_oid"], str) or re.fullmatch(r"[0-9a-f]{40}", item["blob_oid"]) is None:
            _fail(f"files[{index}].blob_oid is invalid")
        if item["role"] not in {"primary_table", "explicit_textmap_candidate_shard", "voice_lut", "fetter_voice_items_container_file"}:
            _fail(f"files[{index}].role is invalid")
        files.append(Phase4C5BFileSpec(relative, item["format"], item["size_bytes"], item["sha256"], item["blob_oid"], item["role"]))
    if [item.relative_path for item in files] != list(EXPECTED_PATHS) or len({item.relative_path for item in files}) != EXPECTED_FILE_COUNT:
        _fail("Phase 4C5B selected paths must be the exact sorted 15-file boundary")
    tables = root["tables"]
    if not isinstance(tables, dict) or set(tables) != set(EXPECTED_TABLES):
        _fail("Phase 4C5B table manifest mismatch")
    for table, native_field in EXPECTED_TABLES.items():
        item = _exact(tables[table], {"relative_path", "native_locator_field", "row_count", "coordinate_digest", "rows"}, f"tables.{table}")
        if item["relative_path"] != f"ExcelBinOutput/{table}.json" or item["native_locator_field"] != native_field or item["row_count"] != EXPECTED_TABLE_ROW_COUNTS[table]:
            _fail(f"{table} table contract mismatch")
        _hex(item["coordinate_digest"], f"tables.{table}.coordinate_digest")
        _validate_coordinate_list(item["rows"], f"tables.{table}.rows", shape="table")
        if len(item["rows"]) != item["row_count"]:
            _fail(f"{table} row coordinate count mismatch")
        expected_digest = _coordinate_digest([
            {"relative_path": row[0], "source_pointer": row[1], "raw_sha256": row[4], "native_locator_value": row[2]}
            for row in item["rows"]
        ])
        if expected_digest != item["coordinate_digest"]:
            _fail(f"{table} coordinate digest does not match its tuples")
    closure = _exact(root["closure"], {"rows", "owners", "candidates", "voice_attachments", "voice_containers", "voice_children"}, "closure")
    for key in ("rows", "owners", "candidates", "voice_attachments", "voice_children"):
        item = _exact(closure[key], {"count", "coordinate_digest", "coordinates"}, f"closure.{key}")
        if not isinstance(item["count"], int) or item["count"] < 0:
            _fail(f"closure.{key}.count is invalid")
        _hex(item["coordinate_digest"], f"closure.{key}.coordinate_digest")
        shape = {"rows": "row", "owners": "owner", "candidates": "candidate", "voice_attachments": "attachment", "voice_children": "child"}[key]
        _validate_coordinate_list(item["coordinates"], f"closure.{key}.coordinates", shape=shape)
        if len(item["coordinates"]) != item["count"]:
            _fail(f"closure.{key} coordinate count mismatch")
        if _coordinate_digest(item["coordinates"]) != item["coordinate_digest"]:
            _fail(f"closure.{key} coordinate digest does not match its tuples")
    containers = closure["voice_containers"]
    if not isinstance(containers, dict) or set(containers) != {"count", "coordinate_digest", "items"}:
        _fail("closure.voice_containers shape mismatch")
    if containers["count"] != EXPECTED_COUNTS["voice_containers"]:
        _fail("Phase 4C5B physical container count mismatch")
    _hex(containers["coordinate_digest"], "closure.voice_containers.coordinate_digest")
    if not isinstance(containers["items"], list) or len(containers["items"]) != containers["count"]:
        _fail("Phase 4C5B physical container item count mismatch")
    for index, item in enumerate(containers["items"]):
        value = _exact(item, {"voice_key", "voice_slot", "item_relative_path", "item_source_pointer", "lut_source_pointer", "child_count", "child_coordinate_digest"}, f"closure.voice_containers.items[{index}]")
        if not isinstance(value["voice_key"], str) or not isinstance(value["voice_slot"], str) or not isinstance(value["child_count"], int) or value["child_count"] < 0:
            _fail(f"closure.voice_containers.items[{index}] value mismatch")
        _path(value["item_relative_path"], f"closure.voice_containers.items[{index}].item_relative_path")
        if not value["item_source_pointer"].startswith("/") or not value["lut_source_pointer"].startswith("/"):
            _fail(f"closure.voice_containers.items[{index}] pointer mismatch")
        _hex(value["child_coordinate_digest"], f"closure.voice_containers.items[{index}].child_coordinate_digest")
    if _coordinate_digest(containers["items"]) != containers["coordinate_digest"]:
        _fail("closure.voice_containers coordinate digest does not match its tuples")
    row_coordinates = {
        (row[0], row[1])
        for table in tables.values()
        for row in table["rows"]
    }
    closure_rows = closure["rows"]["coordinates"]
    if {tuple(row[:2]) for row in closure_rows} != row_coordinates:
        _fail("closure.rows does not exactly cover all primary table rows")
    owners = closure["owners"]["coordinates"]
    owner_keys = set()
    for row in owners:
        if row[0] not in {f"ExcelBinOutput/{table}.json" for table in EXPECTED_TABLES} or not re.match(r"^/[0-9]+/", row[1]):
            _fail("closure owner points outside a primary row")
        row_pointer = "/" + row[1].split("/", 2)[1]
        if (row[0], row_pointer) not in row_coordinates:
            _fail("closure owner points outside a primary row")
        owner_keys.add(tuple(row))
    candidates = closure["candidates"]["coordinates"]
    for owner_index, relative, pointer, locale, kind in candidates:
        if owner_index < 0 or owner_index >= len(owners) or tuple(owners[owner_index]) not in owner_keys:
            _fail("closure candidate owner index is dangling")
        if relative != TEXTMAP_PATHS[locale][kind] or pointer != f"/{pointer[1:]}" or not pointer[1:]:
            _fail("closure candidate shard/pointer mismatch")
    attachments = closure["voice_attachments"]["coordinates"]
    for relative, pointer, slot in attachments:
        if relative != "ExcelBinOutput/FettersExcelConfigData.json" or not re.fullmatch(r"/[0-9]+/voiceFile", pointer) or not isinstance(slot, str):
            _fail("closure voice attachment coordinate mismatch")
    containers_by_key: dict[str, dict[str, Any]] = {}
    for item in containers["items"]:
        if item["voice_key"] in containers_by_key:
            _fail("closure voice container duplicate identity")
        if item["item_relative_path"] not in ITEM_PATHS or not item["voice_key"].startswith(item["item_relative_path"] + ":") or item["lut_source_pointer"] == "":
            _fail("closure voice container cross-reference mismatch")
        containers_by_key[item["voice_key"]] = item
    children_by_container: dict[str, list[Any]] = {key: [] for key in containers_by_key}
    for relative, pointer, voice_key, array_index in closure["voice_children"]["coordinates"]:
        if voice_key not in containers_by_key or relative != containers_by_key[voice_key]["item_relative_path"] or not re.fullmatch(r"/[0-9A-Za-z-]+/BOICINMJKCP/[0-9]+", pointer):
            _fail("closure voice child points outside its physical container")
        children_by_container[voice_key].append([relative, pointer, voice_key, array_index])
    for key, item in containers_by_key.items():
        children = children_by_container[key]
        if len(children) != item["child_count"] or _coordinate_digest(children) != item["child_coordinate_digest"]:
            _fail(f"closure voice child count/digest mismatch: {key}")
    expected = _exact(root["expected"], {"counts", "status_counts", "table_row_counts", "owner_field_counts", "occurrence_set_digests"}, "expected")
    if expected["counts"] != EXPECTED_COUNTS or expected["status_counts"] != EXPECTED_STATUS_COUNTS or expected["table_row_counts"] != EXPECTED_TABLE_ROW_COUNTS or expected["owner_field_counts"] != EXPECTED_OWNER_FIELD_COUNTS:
        _fail("Phase 4C5B expected count/status contract mismatch")
    if not isinstance(expected["occurrence_set_digests"], dict) or set(expected["occurrence_set_digests"]) != {"files", "source_records", "rows", "raw_slots", "relations", "text_resolutions", "text_candidates", "voice_containers", "audio_variants"}:
        _fail("Phase 4C5B occurrence digest contract mismatch")
    for key, value in expected["occurrence_set_digests"].items():
        _hex(value, f"expected.occurrence_set_digests.{key}")
    return CharacterPhase4C5BManifest(
        path=manifest_path, raw_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        manifest_id=root["manifest_id"], source=source, snapshot_id=root["snapshot_id"],
        versions=versions, audit_binding=audit_binding, boundary=boundary,
        identity=identity, files=tuple(files), tables=tables, closure=closure,
        expected=expected, raw=raw,
    )


__all__ = [
    "EXPECTED_AUDIT_BINDING", "EXPECTED_COMMIT", "EXPECTED_COUNTS", "EXPECTED_COVERAGE",
    "EXPECTED_BUILD", "EXPECTED_FILE_COUNT", "EXPECTED_MANIFEST_ID", "EXPECTED_OWNER_FIELD_COUNTS",
    "EXPECTED_PATHS", "EXPECTED_SNAPSHOT_ID", "EXPECTED_STATUS_COUNTS", "EXPECTED_TABLES",
    "EXPECTED_TABLE_ROW_COUNTS", "EXPECTED_TEXT_FIELDS", "EXPECTED_TREE", "EXPECTED_VERSIONS",
    "ITEM_PATHS", "LOCALES", "LUT_PATH", "TEXTMAP_PATHS", "CharacterPhase4C5BManifest",
    "Phase4C5BFileSpec", "Phase4C5BManifestError", "load_character_phase4c5b_manifest",
    "strict_json_load", "strict_json_loads",
]
