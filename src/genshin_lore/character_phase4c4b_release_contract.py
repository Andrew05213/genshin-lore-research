from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .character_phase4c4b_manifest import (
    CharacterPhase4C4BManifest,
    EXPECTED_AUDIT_BINDING,
    EXPECTED_AVATAR_ID,
    EXPECTED_AVATAR_IDS,
    EXPECTED_COMMIT,
    EXPECTED_COUNTS,
    EXPECTED_POSITIVE_AVATAR_IDS,
    EXPECTED_STATUS_COUNTS,
    EXPECTED_TABLES,
    EXPECTED_TABLE_ROW_COUNTS,
    EXPECTED_TREE,
    Phase4C4BManifestError,
    load_character_phase4c4b_manifest,
)
from .util import canonical_json, digest_id, normalize_text, sha256_bytes, sha256_text


RUN_FAMILY = "character_phase4c4b_bounded_six_avatar"
LOCALES = ("CHS", "EN", "JP")
TEXTMAP_PATHS = {
    "CHS": {"main": "TextMap/TextMapCHS.json", "medium": "TextMap/TextMap_MediumCHS.json"},
    "EN": {"main": "TextMap/TextMapEN.json", "medium": "TextMap/TextMap_MediumEN.json"},
    "JP": {"main": "TextMap/TextMapJP.json", "medium": "TextMap/TextMap_MediumJP.json"},
}
ITEM_PATHS = (
    "BinOutput/Voice/Items/1248787199.json",
    "BinOutput/Voice/Items/1560160621.json",
    "BinOutput/Voice/Items/2939652103.json",
    "BinOutput/Voice/Items/750982387.json",
)
LUT_PATH = "BinOutput/Voice/Lut/Lut.json"
TEXT_FIELDS = {
    "AvatarExcelConfigData": (
        "descTextMapHash", "nameTextMapHash",
    ),
    "FetterInfoExcelConfigData": (
        "avatarConstellationAfterTextMapHash", "avatarConstellationBeforTextMapHash",
        "avatarDetailTextMapHash", "avatarNativeTextMapHash", "avatarTitleTextMapHash",
        "avatarVisionAfterTextMapHash", "avatarVisionBeforTextMapHash",
        "cvChineseTextMapHash", "cvEnglishTextMapHash", "cvJapaneseTextMapHash",
        "cvKoreanTextMapHash",
    ),
    "FetterStoryExcelConfigData": (
        "storyContext2TextMapHash", "storyContextTextMapHash", "storyTitle2TextMapHash",
        "storyTitleLockedTextMapHash", "storyTitleTextMapHash",
    ),
    "FettersExcelConfigData": (
        "voiceFileTextTextMapHash", "voiceTitleLockedTextMapHash", "voiceTitleTextMapHash",
    ),
}
SELECTED_PATHS = tuple(sorted({
    *(f"ExcelBinOutput/{table}.json" for table in EXPECTED_TABLES),
    *(path for locale in TEXTMAP_PATHS.values() for path in locale.values()),
    LUT_PATH,
    *ITEM_PATHS,
}))


class Phase4C4BReleaseContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class Phase4C4BReleaseExpected:
    manifest: CharacterPhase4C4BManifest
    source_root: Path
    snapshot_id: str
    parser_build_id: str
    ingest_run_id: str
    config_checksum: str
    derived: dict[str, Any]


def _fail(message: str) -> None:
    raise Phase4C4BReleaseContractError(message)


def _set_digest(items: list[dict[str, Any]]) -> str:
    encoded = sorted(canonical_json(item) for item in items)
    return sha256_text(canonical_json(encoded))


def _raw_checksum(value: Any) -> str:
    return sha256_text(value if isinstance(value, str) else canonical_json(value))


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        _fail(f"cannot verify pinned source checkout: {exc}")
    return result.stdout.strip()


def _verify_phase4c4b_release_source_pin(root: Path) -> None:
    if not root.is_dir():
        _fail(f"source root is not a directory: {root}")
    if _git(root, "status", "--porcelain=v1"):
        _fail("Phase 4C4B source checkout is not clean")
    if _git(root, "rev-parse", "HEAD") != EXPECTED_COMMIT:
        _fail("Phase 4C4B source commit mismatch")
    if _git(root, "rev-parse", "HEAD^{tree}") != EXPECTED_TREE:
        _fail("Phase 4C4B source tree mismatch")


def _file_specs(root: Path) -> list[dict[str, Any]]:
    roles = {
        **{f"ExcelBinOutput/{table}.json": "primary_table" for table in EXPECTED_TABLES},
        **{path: "explicit_textmap_candidate_shard" for locale in TEXTMAP_PATHS.values() for path in locale.values()},
        LUT_PATH: "voice_lut",
        **{path: "fetter_voice_items_container_file" for path in ITEM_PATHS},
    }
    result: list[dict[str, Any]] = []
    for relative in SELECTED_PATHS:
        path = root / relative
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise Phase4C4BReleaseContractError(f"cannot read sealed source file {relative}: {exc}") from exc
        result.append({
            "relative_path": relative,
            "format": "json",
            "size_bytes": len(raw),
            "sha256": sha256_bytes(raw),
            "blob_oid": _git(root, "rev-parse", f"HEAD:{relative}"),
            "role": roles[relative],
        })
    return result


def _json(root: Path, relative: str) -> Any:
    try:
        return json.loads((root / relative).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase4C4BReleaseContractError(f"cannot parse sealed JSON {relative}: {exc}") from exc


def _selected_rows(root: Path) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    occurrences: list[dict[str, Any]] = []
    tables: dict[str, list[dict[str, Any]]] = {}
    table_contract: dict[str, dict[str, Any]] = {}
    selected_ids = set(EXPECTED_AVATAR_IDS)
    for table, (native_field, _) in EXPECTED_TABLES.items():
        relative = f"ExcelBinOutput/{table}.json"
        rows = _json(root, relative)
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            _fail(f"{table} must be an array of objects")
        tables[table] = rows
        selected_specs: list[dict[str, Any]] = []
        for row_index, row in enumerate(rows):
            avatar_id = row.get("id") if table == "AvatarExcelConfigData" else row.get("avatarId")
            if str(avatar_id) not in selected_ids:
                continue
            native_value = row.get(native_field)
            if isinstance(native_value, bool) or not isinstance(native_value, int):
                _fail(f"{table}/{row_index}/{native_field} is not an integer locator")
            spec = {
                "row_index": row_index,
                "pointer": f"/{row_index}",
                "native_locator_value": str(native_value),
                "avatar_id": str(avatar_id),
                "raw_sha256": _raw_checksum(row),
            }
            selected_specs.append(spec)
            occurrences.append({
                "relative_path": relative,
                "source_pointer": f"/{row_index}",
                "table_name": table,
                "row_index": row_index,
                "native_locator_field": native_field,
                "native_locator_value": str(native_value),
                "avatar_id": str(avatar_id),
                "raw_value": row,
                "raw_checksum": spec["raw_sha256"],
            })
        table_contract[table] = {
            "relative_path": relative,
            "native_locator_field": native_field,
            "selected_rows": selected_specs,
            "selected_set_digest": _set_digest(selected_specs),
        }
    occurrences.sort(key=lambda item: (item["relative_path"], item["row_index"]))
    return occurrences, tables, table_contract


def _row_key(table: str, row_index: int) -> str:
    return f"{table}:{row_index}"


def _derive_raw_slots(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    for occurrence in rows:
        table = occurrence["table_name"]
        row_index = occurrence["row_index"]
        row = occurrence["raw_value"]
        condition_fields = {
            "FetterInfoExcelConfigData": ("finishConds",),
            "FetterStoryExcelConfigData": ("finishConds", "openConds"),
            "FettersExcelConfigData": ("openConds",),
        }.get(table, ())
        if condition_fields:
            for field in condition_fields:
                value = row.get(field)
                if not isinstance(value, list):
                    _fail(f"{table}/{row_index}/{field} must be an array")
                for array_index, element in enumerate(value):
                    if not isinstance(element, dict):
                        _fail(f"{table}/{row_index}/{field}/{array_index} must be an object")
                    slots.append({
                        "row_key": _row_key(table, row_index),
                        "source_pointer": f"/{row_index}/{field}/{array_index}",
                        "field_name": field,
                        "array_index": array_index,
                        "slot_kind": "condition_element",
                        "observation_status": "observed",
                        "raw_value": element,
                        "metadata": {
                            "semantic_interpretation": "none_raw_condition_element",
                            "condition_shape": "empty" if not element else "nonempty",
                        },
                    })
        for field, value in row.items():
            if field == "tips":
                if not isinstance(value, list):
                    _fail(f"{table}/{row_index}/tips must be an array")
                for array_index, element in enumerate(value):
                    if isinstance(element, bool) or not isinstance(element, int):
                        _fail(f"{table}/{row_index}/tips/{array_index} is not an opaque integer")
                    slots.append({
                        "row_key": _row_key(table, row_index),
                        "source_pointer": f"/{row_index}/tips/{array_index}",
                        "field_name": "tips",
                        "array_index": array_index,
                        "slot_kind": "opaque_integer",
                        "observation_status": "opaque_unresolved",
                        "raw_value": element,
                        "metadata": {"reason": "tips_array_integer", "textmap_lookup_performed": False},
                    })
            elif field.isupper() and any(character.isalpha() for character in field) and isinstance(value, int) and not isinstance(value, bool):
                alias = table == "AvatarExcelConfigData" and field == "PFODNJGLJNP"
                if alias and value != row.get("descTextMapHash"):
                    _fail("Avatar PFODNJGLJNP alias observation is no longer equal")
                slots.append({
                    "row_key": _row_key(table, row_index),
                    "source_pointer": f"/{row_index}/{field}",
                    "field_name": field,
                    "array_index": None,
                    "slot_kind": "opaque_alias" if alias else "opaque_integer",
                    "observation_status": "observed_equal" if alias else "opaque_unresolved",
                    "raw_value": value,
                    "metadata": (
                        {"alias_of_source_pointer": f"/{row_index}/descTextMapHash", "identity_effect": "none", "text_owner_effect": "none"}
                        if alias else {"reason": "obfuscated_integer_field", "textmap_lookup_performed": False}
                    ),
                })
    slots.sort(key=lambda item: (item["row_key"], item["source_pointer"]))
    return slots


def _derive_text(
    root: Path,
    rows: list[dict[str, Any]],
    relations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    shards: dict[tuple[str, str], dict[str, Any]] = {}
    for locale in LOCALES:
        for shard_kind in ("main", "medium"):
            relative = TEXTMAP_PATHS[locale][shard_kind]
            value = _json(root, relative)
            if not isinstance(value, dict):
                _fail(f"TextMap shard is not an object: {relative}")
            shards[(locale, shard_kind)] = value
    resolutions: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    field_counts: Counter[str] = Counter()
    for occurrence in rows:
        table = occurrence["table_name"]
        row_index = occurrence["row_index"]
        row = occurrence["raw_value"]
        for field in TEXT_FIELDS[table]:
            value = row.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                _fail(f"{table}/{row_index}/{field} is not a positive TextMap hash")
            textmap_hash = str(value)
            source_pointer = f"/{row_index}/{field}"
            owner_key = f"{table}:{row_index}:{field}"
            field_counts[f"{table}.{field}"] += 1
            relations.append({
                "row_key": _row_key(table, row_index),
                "source_pointer": source_pointer,
                "relation_kind": "textmap_owner_candidate",
                "field_role": field,
                "target_family": "TextMap",
                "target_candidate_key": {"textmap_hash": textmap_hash},
                "resolution_status": "resolved",
                "target_row_key": None,
                "target_voice_key": None,
                "candidate_scopes": [],
                "raw_value": value,
                "metadata": {"owner_key": owner_key, "equal_hash_merge_effect": "none"},
            })
            for locale in LOCALES:
                main_text = shards[(locale, "main")].get(textmap_hash)
                medium_text = shards[(locale, "medium")].get(textmap_hash)
                if main_text is not None and not isinstance(main_text, str):
                    _fail(f"TextMap main candidate is not text: {locale}/{textmap_hash}")
                if medium_text is not None and not isinstance(medium_text, str):
                    _fail(f"TextMap medium candidate is not text: {locale}/{textmap_hash}")
                if main_text is None and medium_text is None:
                    status, selected_text = "missing", None
                elif main_text is not None and medium_text is None:
                    status, selected_text = "unique_main", main_text
                elif main_text is None and medium_text is not None:
                    status, selected_text = "unique_medium", medium_text
                elif main_text == medium_text:
                    status, selected_text = "both_equal", main_text
                else:
                    status, selected_text = "both_conflict", None
                scopes: list[dict[str, Any]] = []
                for shard_kind, text in (("main", main_text), ("medium", medium_text)):
                    if text is None:
                        continue
                    relative = TEXTMAP_PATHS[locale][shard_kind]
                    scope = {"relative_path": relative, "source_pointer": f"/{textmap_hash}", "shard_kind": shard_kind}
                    scopes.append(scope)
                    normalized_candidate = normalize_text(text)
                    candidates.append({
                        "owner_key": owner_key,
                        "row_key": _row_key(table, row_index),
                        "relationship_source_pointer": source_pointer,
                        "locale": locale,
                        "textmap_hash": textmap_hash,
                        "resolution_status": status,
                        "source_relative_path": relative,
                        "source_pointer": f"/{textmap_hash}",
                        "shard_kind": shard_kind,
                        "raw_text": text,
                        "normalized_text": normalized_candidate,
                        "raw_checksum": sha256_text(text),
                        "normalized_checksum": sha256_text(normalized_candidate),
                    })
                normalized = None if selected_text is None else normalize_text(selected_text)
                resolutions.append({
                    "owner_key": owner_key,
                    "row_key": _row_key(table, row_index),
                    "relationship_source_pointer": source_pointer,
                    "locale": locale,
                    "textmap_hash": textmap_hash,
                    "resolution_status": status,
                    "raw_text": selected_text,
                    "normalized_text": normalized,
                    "raw_checksum": None if selected_text is None else sha256_text(selected_text),
                    "normalized_checksum": None if normalized is None else sha256_text(normalized),
                    "candidate_scopes": scopes,
                })
    resolutions.sort(key=lambda item: (item["owner_key"], item["locale"]))
    candidates.sort(key=lambda item: (item["owner_key"], item["locale"], item["shard_kind"]))
    return resolutions, candidates, dict(sorted(field_counts.items()))


def _derive_voice(
    root: Path,
    rows: list[dict[str, Any]],
    relations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve Fetters references while preserving row attachments.

    A physical LUT/Items container is emitted once per run because Schema 10
    keys it by source record.  Every Fetters row still gets its own attachment
    relation and manifest target, including repeated references (the native
    pair 10000005/10000007 intentionally exercises this case).
    """
    lut = _json(root, LUT_PATH)
    if not isinstance(lut, dict):
        _fail("Voice LUT must be an object")
    item_files = {relative: _json(root, relative) for relative in ITEM_PATHS}
    if not all(isinstance(value, dict) for value in item_files.values()):
        _fail("Voice Items files must be objects")
    lut_by_slot: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for key, record in lut.items():
        if isinstance(record, dict) and record.get("PKPBCKFKHEE") == "Fetter":
            lut_by_slot.setdefault(str(record.get("MHDDLMLPIBB")), []).append((str(key), record))
    containers: list[dict[str, Any]] = []
    audio: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    seen_voice_keys: set[str] = set()
    for occurrence in rows:
        if occurrence["table_name"] != "FettersExcelConfigData":
            continue
        row_index = occurrence["row_index"]
        row = occurrence["raw_value"]
        raw_slot = row.get("voiceFile")
        if not isinstance(raw_slot, (str, int)) or isinstance(raw_slot, bool):
            _fail(f"Fetters/{row_index}/voiceFile is not a physical slot locator")
        slot = str(raw_slot)
        lut_candidates = lut_by_slot.get(slot, [])
        if len(lut_candidates) != 1:
            _fail(f"Fetter voice slot {slot} has {len(lut_candidates)} LUT candidates")
        lut_key, lut_record = lut_candidates[0]
        item_file_id = lut_record.get("DFEELKOFAKE")
        item_path = f"BinOutput/Voice/Items/{item_file_id}.json"
        item_root = item_files.get(item_path)
        if not isinstance(item_root, dict):
            _fail(f"Fetter voice slot {slot} selects an out-of-bound Items file")
        item_candidates = [
            (str(key), value) for key, value in item_root.items()
            if isinstance(value, dict)
            and value.get("PKPBCKFKHEE") == "Fetter"
            and str(value.get("MHDDLMLPIBB")) == slot
        ]
        if len(item_candidates) != 1:
            _fail(f"Fetter voice slot {slot} has {len(item_candidates)} Items candidates")
        item_key, item_record = item_candidates[0]
        if item_record.get("AIFHPAAHJOC") != item_key:
            _fail(f"Fetter Items UUID/object-key mismatch for slot {slot}")
        variants = item_record.get("BOICINMJKCP")
        if not isinstance(variants, list):
            _fail(f"Fetter Items BOICINMJKCP is not an array for slot {slot}")
        voice_key = f"{item_path}:{item_key}"
        candidate_scopes = {
            "lut_candidates": [{"relative_path": LUT_PATH, "source_pointer": f"/{lut_key}"}],
            "item_candidates": [{"relative_path": item_path, "source_pointer": f"/{item_key}"}],
            "target_scope": {"relative_path": item_path, "source_pointer": f"/{item_key}"},
        }
        if voice_key not in seen_voice_keys:
            seen_voice_keys.add(voice_key)
            containers.append({
                "voice_key": voice_key,
                "voice_slot": slot,
                "lut_relative_path": LUT_PATH,
                "lut_source_pointer": f"/{lut_key}",
                "lut_raw_value": lut_record,
                "lut_raw_checksum": _raw_checksum(lut_record),
                "item_relative_path": item_path,
                "item_source_pointer": f"/{item_key}",
                "item_raw_value": item_record,
                "item_raw_checksum": _raw_checksum(item_record),
                "resolution_status": "resolved_unique_container",
                "candidate_scopes": candidate_scopes,
                "audio_variant_count": len(variants),
            })
            for array_index, variant in enumerate(variants):
                if not isinstance(variant, dict):
                    _fail(f"Fetter audio variant is not an object for slot {slot}/{array_index}")
                selector = variant.get("NMAENMONCGJ")
                path = variant.get("IKNGALOCDCK")
                if not isinstance(selector, str) or not isinstance(path, str):
                    _fail(f"Fetter audio variant selector/path is invalid for slot {slot}/{array_index}")
                audio.append({
                    "voice_key": voice_key,
                    "item_source_pointer": f"/{item_key}",
                    "source_pointer": f"/{item_key}/BOICINMJKCP/{array_index}",
                    "array_index": array_index,
                    "audio_path": path,
                    "audio_path_status": "observed",
                    "character_child_selector": selector,
                    "selector_status": "observed_unresolved",
                    "metadata": {"identity_effect": "none", "universal_foreign_key_created": False},
                })
        targets.append({
            "source_row_pointer": f"/{row_index}/voiceFile",
            "voice_slot": slot,
            "lut_path": LUT_PATH,
            "lut_pointer": f"/{lut_key}",
            "lut_raw_sha256": _raw_checksum(lut_record),
            "item_path": item_path,
            "item_pointer": f"/{item_key}",
            "item_raw_sha256": _raw_checksum(item_record),
            "audio_variant_count": len(variants),
        })
        relations.append({
            "row_key": _row_key("FettersExcelConfigData", row_index),
            "source_pointer": f"/{row_index}/voiceFile",
            "relation_kind": "voice_container_attachment",
            "field_role": "voiceFile_physical_container_locator",
            "target_family": "FetterVoiceItemsContainer",
            "target_candidate_key": {"voice_slot": slot},
            "resolution_status": "resolved_unique_container",
            "target_row_key": None,
            "target_voice_key": voice_key,
            "candidate_scopes": candidate_scopes,
            "raw_value": raw_slot,
            "metadata": {"character_selector_used_for_selection": False, "naming_convention_fk_created": False},
        })
    containers.sort(key=lambda item: item["voice_key"])
    targets.sort(key=lambda item: int(item["source_row_pointer"].split("/")[1]))
    audio.sort(key=lambda item: (item["voice_key"], item["array_index"]))
    return containers, audio, targets
def derive_character_phase4c4b_release_contract(source_root: str | Path) -> dict[str, Any]:
    root = Path(source_root).resolve()
    _verify_phase4c4b_release_source_pin(root)
    files = _file_specs(root)
    rows, _, tables = _selected_rows(root)
    raw_slots = _derive_raw_slots(rows)
    relations: list[dict[str, Any]] = []
    avatar_rows = {
        occurrence["avatar_id"]: occurrence
        for occurrence in rows
        if occurrence["table_name"] == "AvatarExcelConfigData"
    }
    for occurrence in rows:
        if occurrence["table_name"] == "AvatarExcelConfigData":
            continue
        row = occurrence["raw_value"]
        row_index = occurrence["row_index"]
        table = occurrence["table_name"]
        avatar_id = occurrence["avatar_id"]
        target = avatar_rows.get(avatar_id)
        target_row_key = None if target is None else _row_key("AvatarExcelConfigData", target["row_index"])
        target_pointer = None if target is None else target["source_pointer"]
        relations.append({
            "row_key": _row_key(table, row_index),
            "source_pointer": f"/{row_index}/avatarId",
            "relation_kind": "avatar_row_candidate",
            "field_role": "avatarId_table_scoped_candidate",
            "target_family": "AvatarExcelConfigData",
            "target_candidate_key": {"table": "AvatarExcelConfigData", "native_id": avatar_id},
            "resolution_status": "resolved" if target is not None else "dangling",
            "target_row_key": target_row_key,
            "target_voice_key": None,
            "candidate_scopes": [] if target is None else [{"relative_path": "ExcelBinOutput/AvatarExcelConfigData.json", "source_pointer": target_pointer}],
            "raw_value": row.get("avatarId"),
            "metadata": {"logical_character_created": False, "native_id_scope": "family_table_scoped"},
        })
    text_resolutions, text_candidates, field_counts = _derive_text(root, rows, relations)
    voice_containers, audio_variants, voice_targets = _derive_voice(root, rows, relations)
    relations.sort(key=lambda item: (item["row_key"], item["source_pointer"], item["relation_kind"]))

    source_record_map: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        source_record_map[(row["relative_path"], row["source_pointer"])] = {
            "relative_path": row["relative_path"], "source_pointer": row["source_pointer"],
            "record_kind": "excel_row", "native_id": row["native_locator_value"],
            "raw_value": row["raw_value"], "raw_checksum": row["raw_checksum"],
        }
    for candidate in text_candidates:
        key = (candidate["source_relative_path"], candidate["source_pointer"])
        source_record_map[key] = {
            "relative_path": key[0], "source_pointer": key[1],
            "record_kind": "textmap_entry", "native_id": candidate["textmap_hash"],
            "raw_value": candidate["raw_text"], "raw_checksum": candidate["raw_checksum"],
        }
    for container in voice_containers:
        for kind in ("lut", "item"):
            key = (container[f"{kind}_relative_path"], container[f"{kind}_source_pointer"])
            source_record_map[key] = {
                "relative_path": key[0], "source_pointer": key[1],
                "record_kind": f"voice_{kind}_record", "native_id": container["voice_slot"],
                "raw_value": container[f"{kind}_raw_value"], "raw_checksum": container[f"{kind}_raw_checksum"],
            }
    source_records = sorted(source_record_map.values(), key=lambda item: (item["relative_path"], item["source_pointer"]))

    digests = {
        "files": _set_digest(files),
        "source_records": _set_digest([{key: item[key] for key in ("relative_path", "source_pointer", "record_kind", "native_id", "raw_checksum")} for item in source_records]),
        "rows": _set_digest([{key: item[key] for key in ("relative_path", "source_pointer", "table_name", "row_index", "native_locator_field", "native_locator_value", "avatar_id", "raw_checksum")} for item in rows]),
        "raw_slots": _set_digest(raw_slots),
        "relations": _set_digest(relations),
        "text_resolutions": _set_digest(text_resolutions),
        "text_candidates": _set_digest(text_candidates),
        "voice_containers": _set_digest([{key: item[key] for key in item if key not in {"lut_raw_value", "item_raw_value"}} for item in voice_containers]),
        "audio_variants": _set_digest(audio_variants),
    }
    status_counts = {
        "relations": dict(sorted(Counter(item["resolution_status"] for item in relations).items())),
        "text_resolutions": {key: Counter(item["resolution_status"] for item in text_resolutions).get(key, 0) for key in ("unique_main", "unique_medium", "both_equal", "both_conflict", "missing")},
        "raw_slots": dict(sorted(Counter(item["observation_status"] for item in raw_slots).items())),
        "condition_shapes": dict(sorted(Counter(
            item["metadata"]["condition_shape"]
            for item in raw_slots if item["slot_kind"] == "condition_element"
        ).items())),
        "voice_containers": dict(sorted(Counter(item["resolution_status"] for item in voice_containers).items())),
        "audio_paths": dict(sorted(Counter(item["audio_path_status"] for item in audio_variants).items())),
        "character_child_selectors": dict(sorted(Counter(item["selector_status"] for item in audio_variants).items())),
    }
    counts = {
        "selected_files": len(files), "source_records": len(source_records), "rows": len(rows),
        "raw_slots": len(raw_slots),
        "condition_slots": sum(item["slot_kind"] == "condition_element" for item in raw_slots),
        "condition_nonempty": sum(
            item["slot_kind"] == "condition_element" and item["metadata"]["condition_shape"] == "nonempty"
            for item in raw_slots
        ),
        "condition_empty": sum(
            item["slot_kind"] == "condition_element" and item["metadata"]["condition_shape"] == "empty"
            for item in raw_slots
        ),
        "opaque_slots": sum(item["slot_kind"] != "condition_element" for item in raw_slots),
        "relations": len(relations),
        "avatar_relations": sum(item["relation_kind"] == "avatar_row_candidate" for item in relations),
        "text_owner_occurrences": sum(item["relation_kind"] == "textmap_owner_candidate" for item in relations),
        "voice_attachments": sum(item["relation_kind"] == "voice_container_attachment" for item in relations),
        "text_resolutions": len(text_resolutions), "text_candidates": len(text_candidates),
        "voice_containers": len(voice_containers), "audio_variants": len(audio_variants),
        "distinct_audio_paths": len({item["audio_path"] for item in audio_variants}),
        "distinct_character_child_selectors": len({item["character_child_selector"] for item in audio_variants}),
        "unattached_lut_entries": 0,
    }
    return {
        "files": files, "tables": tables, "voice_targets": voice_targets,
        "rows": rows, "raw_slots": raw_slots, "relations": relations,
        "text_resolutions": text_resolutions, "text_candidates": text_candidates,
        "voice_containers": voice_containers, "audio_variants": audio_variants,
        "source_records": source_records, "counts": counts, "status_counts": status_counts,
        "table_row_counts": {table: len(indexes["selected_rows"]) for table, indexes in tables.items()},
        "text_owner_field_counts": field_counts, "occurrence_set_digests": digests,
    }



def expected_phase4c4b_release_identity(
    manifest: CharacterPhase4C4BManifest,
) -> dict[str, str]:
    source = manifest.source
    snapshot_id = digest_id("snap", canonical_json({
        "commit": source["commit"],
        "tree": source["tree"],
        "build": source["build"],
        "release": source["release_label"],
    }))
    parser_build_id = digest_id(
        "pb-character-phase4c4b",
        canonical_json({
            "versions": manifest.versions,
            "manifest_id": manifest.manifest_id,
        }),
    )
    config_checksum = sha256_text(canonical_json({
        "manifest_id": manifest.manifest_id,
        "manifest_raw_sha256": manifest.raw_sha256,
        "occurrence_set_digests": manifest.expected[
            "occurrence_set_digests"
        ],
    }))
    ingest_run_id = digest_id(
        "ir-character-phase4c4b",
        canonical_json({
            "snapshot_id": snapshot_id,
            "parser_build_id": parser_build_id,
            "config_checksum": config_checksum,
        }),
    )
    return {
        "snapshot_id": snapshot_id,
        "parser_build_id": parser_build_id,
        "config_checksum": config_checksum,
        "ingest_run_id": ingest_run_id,
    }


def _load_bound_release_report(
    repo_root: Path,
    spec: dict[str, str],
) -> dict[str, Any]:
    path = repo_root / spec["relative_path"]
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(
            "cannot independently read Phase 4C1 audit artifact "
            f"{spec['relative_path']}: {exc}"
        )
    if hashlib.sha256(raw).hexdigest() != spec["sha256"]:
        _fail(
            "Phase 4C1 audit artifact hash mismatch: "
            f"{spec['relative_path']}"
        )
    if (
        not isinstance(value, dict)
        or value.get("audit_version") != "4C1.1"
    ):
        _fail(
            "Phase 4C1 audit artifact version mismatch: "
            f"{spec['relative_path']}"
        )
    return value


def _selected_audit_scope(scope: dict[str, Any]) -> bool:
    path = scope.get("path")
    row = scope.get("row")
    return (
        (
            path == "ExcelBinOutput/AvatarExcelConfigData.json"
            and row == 2
        )
        or (
            path == "ExcelBinOutput/FetterInfoExcelConfigData.json"
            and row == 1
        )
        or (
            path == "ExcelBinOutput/FetterStoryExcelConfigData.json"
            and isinstance(row, int)
            and 0 <= row <= 7
        )
        or (
            path == "ExcelBinOutput/FettersExcelConfigData.json"
            and isinstance(row, int)
            and 579 <= row <= 650
        )
    )


def _verify_phase4c1_release_contract(
    manifest: CharacterPhase4C4BManifest,
    derived: dict[str, Any],
) -> None:
    """Independently bind the release check to the sealed C1 artifacts."""
    repo_root = Path(__file__).resolve().parents[2]
    audit_manifest = _load_bound_release_report(
        repo_root, EXPECTED_AUDIT_BINDING["manifest"]
    )
    listed = {
        (item.get("relative_path"), item.get("sha256"))
        for item in audit_manifest.get("artifacts", [])
        if isinstance(item, dict)
    }
    expected_listed = {
        (item["relative_path"], item["sha256"])
        for item in EXPECTED_AUDIT_BINDING["artifacts"]
    }
    if listed != expected_listed:
        _fail("Phase 4C1 audit manifest artifact binding mismatch")
    reports = {
        Path(spec["relative_path"]).name:
            _load_bound_release_report(repo_root, spec)
        for spec in EXPECTED_AUDIT_BINDING["artifacts"]
    }
    source = reports["phase4c1-source-inventory.json"]
    source_files = {
        item["relative_path"]: item
        for item in source.get("files", [])
        if isinstance(item, dict)
    }
    for item in derived["files"]:
        audited = source_files.get(item["relative_path"])
        if audited is None:
            _fail(f"Phase 4C1 source inventory omits {item['relative_path']}")
        for key in ("sha256", "size_bytes", "blob_oid", "role"):
            if audited.get(key) != item[key]:
                _fail(f"Phase 4C1 source inventory mismatch: {item['relative_path']} {key}")
    row_audit = reports["phase4c1-row-identity-audit.json"]
    voice_audit = reports["phase4c1-voice-chain-audit.json"]
    if (
        row_audit.get("commit") != manifest.source["commit"]
        or row_audit.get("tree") != manifest.source["tree"]
        or voice_audit.get("commit") != manifest.source["commit"]
    ):
        _fail("Phase 4C1 audit source binding mismatch")
    unattached_slots = {
        str(item.get("voice_slot"))
        for item in voice_audit.get("unattached_lut_entries", [])
        if isinstance(item, dict)
    }
    selected_slots = {str(item["voice_slot"]) for item in derived["voice_targets"]}
    if selected_slots & unattached_slots:
        _fail("selected Voice slots intersect C1 unattached LUT entries")
def build_phase4c4b_release_expected(
    source_root: str | Path,
    manifest_path: str | Path,
) -> Phase4C4BReleaseExpected:
    if not isinstance(manifest_path, (str, Path)):
        _fail(
            "public Phase 4C4B release API requires a filesystem "
            "manifest path"
        )
    try:
        manifest = load_character_phase4c4b_manifest(manifest_path)
    except Phase4C4BManifestError as exc:
        raise Phase4C4BReleaseContractError(str(exc)) from exc
    derived = derive_character_phase4c4b_release_contract(
        source_root
    )
    manifest_files = [
        {
            "relative_path": item.relative_path,
            "format": item.format,
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
            "blob_oid": item.blob_oid,
            "role": item.role,
        }
        for item in manifest.files
    ]
    if derived["files"] != manifest_files:
        _fail("release source file inventory mismatch")
    if derived["tables"] != manifest.tables:
        _fail("release primary row pointers/checksums mismatch")
    if derived["voice_targets"] != list(manifest.voice_targets):
        _fail("release Voice physical chain mismatch")
    for key in (
        "counts",
        "status_counts",
        "table_row_counts",
        "text_owner_field_counts",
        "occurrence_set_digests",
    ):
        if derived[key] != manifest.expected[key]:
            _fail(f"release {key} mismatch")
    _verify_phase4c1_release_contract(manifest, derived)
    identity = expected_phase4c4b_release_identity(manifest)
    return Phase4C4BReleaseExpected(
        manifest,
        Path(source_root).resolve(),
        derived=derived,
        **identity,
    )


def _ensure_row(
    conn: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    values: tuple[Any, ...],
    *,
    insert_missing: bool,
) -> None:
    row = conn.execute(f"SELECT {','.join(columns)} FROM {table} WHERE id=?", (values[0],)).fetchone()
    if row is None:
        if not insert_missing:
            raise sqlite3.IntegrityError(f"missing {table} occurrence {values[0]}")
        placeholders = ",".join("?" for _ in columns)
        conn.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES ({placeholders})", values)
        return
    actual = tuple(row[column] for column in columns)
    if actual != values:
        raise sqlite3.IntegrityError(f"write-once {table} occurrence mismatch: {values[0]}")


def _occurrence_ids(preflight: Phase4C4BReleaseExpected) -> dict[str, dict[Any, str]]:
    manifest_sha = preflight.manifest.raw_sha256
    run = preflight.ingest_run_id
    snapshot = preflight.snapshot_id
    rows = {
        _row_key(item["table_name"], item["row_index"]): digest_id("character-row", snapshot, manifest_sha, run, item["relative_path"], item["source_pointer"])
        for item in preflight.derived["rows"]
    }
    voices = {
        item["voice_key"]: digest_id("character-voice-container", snapshot, manifest_sha, run, item["item_relative_path"], item["item_source_pointer"])
        for item in preflight.derived["voice_containers"]
    }
    relations = {
        (item["row_key"], item["source_pointer"], item["relation_kind"]): digest_id("character-relation", snapshot, manifest_sha, run, item["row_key"], item["source_pointer"], item["relation_kind"])
        for item in preflight.derived["relations"]
    }
    resolutions = {
        (item["owner_key"], item["locale"]): digest_id("character-text-resolution", snapshot, manifest_sha, run, item["owner_key"], item["locale"])
        for item in preflight.derived["text_resolutions"]
    }
    return {"rows": rows, "voices": voices, "relations": relations, "resolutions": resolutions}


def verify_phase4c4b_release_occurrence_rows(
    conn: sqlite3.Connection,
    preflight: Phase4C4BReleaseExpected,
    *,
    insert_missing: bool,
) -> None:
    ids = _occurrence_ids(preflight)
    file_ids = {item["relative_path"]: digest_id("sf", preflight.snapshot_id, item["relative_path"]) for item in preflight.derived["files"]}
    record_ids = {(item["relative_path"], item["source_pointer"]): digest_id("sr", file_ids[item["relative_path"]], item["source_pointer"]) for item in preflight.derived["source_records"]}
    row_records: dict[str, str] = {}
    for item in preflight.derived["rows"]:
        key = _row_key(item["table_name"], item["row_index"])
        record_id = record_ids[(item["relative_path"], item["source_pointer"])]
        row_records[key] = record_id
        _ensure_row(conn, "character_row_occurrence", (
            "id", "ingest_run_id", "snapshot_id", "source_file_id", "source_record_id", "source_pointer",
            "table_name", "row_index", "native_locator_field", "native_locator_value", "avatar_id", "raw_checksum", "metadata_json",
        ), (
            ids["rows"][key], preflight.ingest_run_id, preflight.snapshot_id, file_ids[item["relative_path"]], record_id,
            item["source_pointer"], item["table_name"], item["row_index"], item["native_locator_field"], item["native_locator_value"],
            item["avatar_id"], item["raw_checksum"], canonical_json({"logical_identity_created": False, "raw_evidence_identity": True}),
        ), insert_missing=insert_missing)
    for item in preflight.derived["voice_containers"]:
        voice_id = ids["voices"][item["voice_key"]]
        _ensure_row(conn, "character_voice_container_occurrence", (
            "id", "ingest_run_id", "snapshot_id", "voice_slot", "lut_source_file_id", "lut_source_record_id", "lut_source_pointer",
            "item_source_file_id", "item_source_record_id", "item_source_pointer", "resolution_status", "candidate_scopes_json", "metadata_json",
        ), (
            voice_id, preflight.ingest_run_id, preflight.snapshot_id, item["voice_slot"], file_ids[item["lut_relative_path"]],
            record_ids[(item["lut_relative_path"], item["lut_source_pointer"])], item["lut_source_pointer"],
            file_ids[item["item_relative_path"]], record_ids[(item["item_relative_path"], item["item_source_pointer"])], item["item_source_pointer"],
            item["resolution_status"], canonical_json(item["candidate_scopes"]),
            canonical_json({"audio_variant_count": item["audio_variant_count"], "voice_slot_identity_effect": "none"}),
        ), insert_missing=insert_missing)
    for item in preflight.derived["raw_slots"]:
        slot_id = digest_id("character-raw-slot", preflight.snapshot_id, preflight.manifest.raw_sha256, preflight.ingest_run_id, item["row_key"], item["source_pointer"])
        _ensure_row(conn, "character_raw_slot_occurrence", (
            "id", "ingest_run_id", "snapshot_id", "row_occurrence_id", "source_record_id", "source_pointer", "field_name", "array_index",
            "slot_kind", "observation_status", "raw_json", "metadata_json",
        ), (
            slot_id, preflight.ingest_run_id, preflight.snapshot_id, ids["rows"][item["row_key"]], row_records[item["row_key"]],
            item["source_pointer"], item["field_name"], item["array_index"], item["slot_kind"], item["observation_status"],
            canonical_json(item["raw_value"]), canonical_json(item["metadata"]),
        ), insert_missing=insert_missing)
    for item in preflight.derived["relations"]:
        relation_key = (item["row_key"], item["source_pointer"], item["relation_kind"])
        _ensure_row(conn, "character_relation_occurrence", (
            "id", "ingest_run_id", "snapshot_id", "source_row_occurrence_id", "source_record_id", "source_pointer", "relation_kind",
            "field_role", "target_family", "target_candidate_key_json", "resolution_status", "target_row_occurrence_id", "target_voice_container_id",
            "candidate_scopes_json", "metadata_json",
        ), (
            ids["relations"][relation_key], preflight.ingest_run_id, preflight.snapshot_id, ids["rows"][item["row_key"]], row_records[item["row_key"]],
            item["source_pointer"], item["relation_kind"], item["field_role"], item["target_family"], canonical_json(item["target_candidate_key"]),
            item["resolution_status"], None if item["target_row_key"] is None else ids["rows"][item["target_row_key"]],
            None if item["target_voice_key"] is None else ids["voices"][item["target_voice_key"]], canonical_json(item["candidate_scopes"]),
            canonical_json({**item["metadata"], "raw_value": item["raw_value"]}),
        ), insert_missing=insert_missing)
    for item in preflight.derived["text_resolutions"]:
        relation_key = (item["row_key"], item["relationship_source_pointer"], "textmap_owner_candidate")
        resolution_id = ids["resolutions"][(item["owner_key"], item["locale"])]
        _ensure_row(conn, "character_text_resolution_occurrence", (
            "id", "ingest_run_id", "snapshot_id", "relationship_occurrence_id", "source_row_occurrence_id", "source_record_id", "source_pointer",
            "locale", "textmap_hash", "resolution_status", "raw_text", "normalized_text", "raw_checksum", "normalized_checksum", "candidate_scopes_json", "metadata_json",
        ), (
            resolution_id, preflight.ingest_run_id, preflight.snapshot_id, ids["relations"][relation_key], ids["rows"][item["row_key"]],
            row_records[item["row_key"]], item["relationship_source_pointer"], item["locale"], item["textmap_hash"], item["resolution_status"],
            item["raw_text"], item["normalized_text"], item["raw_checksum"], item["normalized_checksum"], canonical_json(item["candidate_scopes"]),
            canonical_json({"missing_is_legal": True, "hash_identity_effect": "none"}),
        ), insert_missing=insert_missing)
    for item in preflight.derived["text_candidates"]:
        resolution_id = ids["resolutions"][(item["owner_key"], item["locale"])]
        candidate_id = digest_id("character-text-candidate", preflight.snapshot_id, preflight.manifest.raw_sha256, preflight.ingest_run_id,
                                 item["owner_key"], item["locale"], item["shard_kind"], item["source_pointer"])
        _ensure_row(conn, "character_text_candidate_occurrence", (
            "id", "resolution_occurrence_id", "ingest_run_id", "snapshot_id", "source_file_id", "source_record_id", "source_pointer", "shard_kind",
            "locale", "resolution_status", "raw_text", "normalized_text", "raw_checksum", "normalized_checksum", "metadata_json",
        ), (
            candidate_id, resolution_id, preflight.ingest_run_id, preflight.snapshot_id, file_ids[item["source_relative_path"]],
            record_ids[(item["source_relative_path"], item["source_pointer"])], item["source_pointer"], item["shard_kind"], item["locale"],
            item["resolution_status"], item["raw_text"], item["normalized_text"], item["raw_checksum"], item["normalized_checksum"],
            canonical_json({"owner_key": item["owner_key"], "candidate_identity_effect": "none"}),
        ), insert_missing=insert_missing)
    voice_item_records = {
        item["voice_key"]: record_ids[(item["item_relative_path"], item["item_source_pointer"])]
        for item in preflight.derived["voice_containers"]
    }
    for item in preflight.derived["audio_variants"]:
        audio_id = digest_id("character-audio-variant", preflight.snapshot_id, preflight.manifest.raw_sha256, preflight.ingest_run_id,
                             item["voice_key"], item["source_pointer"])
        _ensure_row(conn, "character_audio_variant_occurrence", (
            "id", "ingest_run_id", "snapshot_id", "voice_container_id", "item_source_record_id", "source_pointer", "array_index", "audio_path",
            "audio_path_status", "character_child_selector", "selector_status", "metadata_json",
        ), (
            audio_id, preflight.ingest_run_id, preflight.snapshot_id, ids["voices"][item["voice_key"]], voice_item_records[item["voice_key"]],
            item["source_pointer"], item["array_index"], item["audio_path"], item["audio_path_status"], item["character_child_selector"],
            item["selector_status"], canonical_json(item["metadata"]),
        ), insert_missing=insert_missing)



__all__ = [
    "Phase4C4BReleaseContractError",
    "Phase4C4BReleaseExpected",
    "build_phase4c4b_release_expected",
    "expected_phase4c4b_release_identity",
    "verify_phase4c4b_release_occurrence_rows",
]
