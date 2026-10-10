from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .character_phase4c2_manifest import (
    CharacterPhase4C2Manifest,
    EXPECTED_AUDIT_BINDING,
    EXPECTED_AVATAR_ID,
    EXPECTED_COMMIT,
    EXPECTED_COUNTS,
    EXPECTED_STATUS_COUNTS,
    EXPECTED_TABLES,
    EXPECTED_TREE,
    Phase4C2ManifestError,
    load_character_phase4c2_manifest,
)
from .character_phase4c2_schema import (
    CharacterPhase4C2SchemaError,
    verify_character_phase4c2_schema_signature,
)
from .db import Database, MIGRATIONS_DIR
from .util import canonical_json, digest_id, normalize_text, sha256_bytes, sha256_text


RUN_FAMILY = "character_phase4c2_avatar10000003_canary"
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


class Phase4C2EvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Phase4C2PreflightResult:
    manifest: CharacterPhase4C2Manifest
    source_root: Path
    snapshot_id: str
    parser_build_id: str
    ingest_run_id: str
    config_checksum: str
    derived: dict[str, Any]


@dataclass(frozen=True)
class _DatabaseIdentity:
    device: int
    inode: int
    mode: int
    sha256: str
    size_bytes: int
    mtime_ns: int


def _fail(message: str) -> None:
    raise Phase4C2EvidenceError(message)


def _posix_builder_runtime_supported() -> bool:
    return (
        os.name == "posix"
        and callable(getattr(os, "replace", None))
        and callable(
            getattr(sqlite3.Connection, "backup", None)
        )
        and hasattr(os.stat_result, "st_ino")
    )


def _require_posix_builder_runtime() -> None:
    if not _posix_builder_runtime_supported():
        raise Phase4C2EvidenceError(
            "Phase 4C2 Character canary builder is POSIX-only; "
            "this runtime lacks the required same-directory atomic "
            "replace, inode CAS, SQLite backup, or exclusive-lock "
            "semantics"
        )


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


def verify_phase4c2_source_pin(root: Path) -> None:
    if not root.is_dir():
        _fail(f"source root is not a directory: {root}")
    if _git(root, "status", "--porcelain=v1"):
        _fail("Phase 4C2 source checkout is not clean")
    if _git(root, "rev-parse", "HEAD") != EXPECTED_COMMIT:
        _fail("Phase 4C2 source commit mismatch")
    if _git(root, "rev-parse", "HEAD^{tree}") != EXPECTED_TREE:
        _fail("Phase 4C2 source tree mismatch")


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
            raise Phase4C2EvidenceError(f"cannot read sealed source file {relative}: {exc}") from exc
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
        raise Phase4C2EvidenceError(f"cannot parse sealed JSON {relative}: {exc}") from exc


def _selected_rows(root: Path) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    occurrences: list[dict[str, Any]] = []
    tables: dict[str, list[dict[str, Any]]] = {}
    table_contract: dict[str, dict[str, Any]] = {}
    for table, (native_field, selected_indexes) in EXPECTED_TABLES.items():
        relative = f"ExcelBinOutput/{table}.json"
        rows = _json(root, relative)
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            _fail(f"{table} must be an array of objects")
        tables[table] = rows
        selected_specs: list[dict[str, Any]] = []
        for row_index in selected_indexes:
            if row_index >= len(rows):
                _fail(f"{table} selected row is out of range: {row_index}")
            row = rows[row_index]
            avatar_id = row.get("id") if table == "AvatarExcelConfigData" else row.get("avatarId")
            if str(avatar_id) != EXPECTED_AVATAR_ID:
                _fail(f"{table}/{row_index} is not avatarId={EXPECTED_AVATAR_ID}")
            native_value = row.get(native_field)
            if isinstance(native_value, bool) or not isinstance(native_value, int):
                _fail(f"{table}/{row_index}/{native_field} is not an integer locator")
            spec = {
                "row_index": row_index,
                "pointer": f"/{row_index}",
                "native_locator_value": str(native_value),
                "avatar_id": EXPECTED_AVATAR_ID,
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
                "avatar_id": EXPECTED_AVATAR_ID,
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
    containers.sort(key=lambda item: item["voice_key"])
    targets.sort(key=lambda item: int(item["source_row_pointer"].split("/")[1]))
    audio.sort(key=lambda item: (item["voice_key"], item["array_index"]))
    if len({item["voice_key"] for item in containers}) != len(containers):
        _fail("selected Fetter rows did not resolve to distinct physical Voice containers")
    return containers, audio, targets


def derive_character_phase4c2_source_contract(source_root: str | Path) -> dict[str, Any]:
    root = Path(source_root).resolve()
    verify_phase4c2_source_pin(root)
    files = _file_specs(root)
    rows, _, tables = _selected_rows(root)
    raw_slots = _derive_raw_slots(rows)
    relations: list[dict[str, Any]] = []
    avatar_key = _row_key("AvatarExcelConfigData", 2)
    for occurrence in rows:
        if occurrence["table_name"] == "AvatarExcelConfigData":
            continue
        row = occurrence["raw_value"]
        row_index = occurrence["row_index"]
        table = occurrence["table_name"]
        relations.append({
            "row_key": _row_key(table, row_index),
            "source_pointer": f"/{row_index}/avatarId",
            "relation_kind": "avatar_row_candidate",
            "field_role": "avatarId_table_scoped_candidate",
            "target_family": "AvatarExcelConfigData",
            "target_candidate_key": {"table": "AvatarExcelConfigData", "native_id": EXPECTED_AVATAR_ID},
            "resolution_status": "resolved",
            "target_row_key": avatar_key,
            "target_voice_key": None,
            "candidate_scopes": [{"relative_path": "ExcelBinOutput/AvatarExcelConfigData.json", "source_pointer": "/2"}],
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
        "table_row_counts": {table: len(indexes) for table, (_, indexes) in EXPECTED_TABLES.items()},
        "text_owner_field_counts": field_counts, "occurrence_set_digests": digests,
    }


def expected_phase4c2_identity(manifest: CharacterPhase4C2Manifest) -> dict[str, str]:
    source = manifest.source
    snapshot_id = digest_id("snap", canonical_json({
        "commit": source["commit"], "tree": source["tree"], "build": source["build"],
        "release": source["release_label"],
    }))
    parser_build_id = digest_id("pb-character-phase4c2", canonical_json({
        "versions": manifest.versions, "manifest_id": manifest.manifest_id,
    }))
    config_checksum = sha256_text(canonical_json({
        "manifest_id": manifest.manifest_id, "manifest_raw_sha256": manifest.raw_sha256,
        "occurrence_set_digests": manifest.expected["occurrence_set_digests"],
    }))
    ingest_run_id = digest_id("ir-character-phase4c2", canonical_json({
        "snapshot_id": snapshot_id, "parser_build_id": parser_build_id,
        "config_checksum": config_checksum,
    }))
    return {
        "snapshot_id": snapshot_id, "parser_build_id": parser_build_id,
        "config_checksum": config_checksum, "ingest_run_id": ingest_run_id,
    }


def _verify_bound_phase4c1_unattached_disjoint(derived: dict[str, Any]) -> None:
    project_root = Path(__file__).resolve().parents[2]
    specs = [
        EXPECTED_AUDIT_BINDING["manifest"],
        *EXPECTED_AUDIT_BINDING["artifacts"],
    ]
    reports: dict[str, dict[str, Any]] = {}
    for spec in specs:
        path = project_root / spec["relative_path"]
        try:
            raw = path.read_bytes()
            value = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            _fail(f"cannot read bound Phase 4C1 audit artifact {spec['relative_path']}: {exc}")
        if hashlib.sha256(raw).hexdigest() != spec["sha256"]:
            _fail(f"bound Phase 4C1 audit artifact hash mismatch: {spec['relative_path']}")
        if not isinstance(value, dict) or value.get("audit_version") != "4C1.1":
            _fail(f"bound Phase 4C1 audit artifact version mismatch: {spec['relative_path']}")
        reports[Path(spec["relative_path"]).name] = value
    audit_manifest = reports["phase4c1-audit-manifest.json"]
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
    voice = reports["phase4c1-voice-chain-audit.json"]
    canary_slots = {str(item["voice_slot"]) for item in derived["voice_targets"]}
    unattached_slots = {
        str(item.get("voice_slot"))
        for item in voice.get("unattached_lut_entries", [])
        if isinstance(item, dict)
    }
    overlap = sorted(canary_slots & unattached_slots)
    if overlap:
        _fail(f"Phase 4C2 canary slots intersect Phase 4C1 unattached LUT entries: {overlap}")
    if derived["counts"]["unattached_lut_entries"] != 0:
        _fail("Phase 4C2 canary unattached count is not zero")


def preflight_character_phase4c2_evidence_bundle(
    source_root: str | Path,
    manifest_path: str | Path,
) -> Phase4C2PreflightResult:
    if not isinstance(manifest_path, (str, Path)):
        _fail("public Phase 4C2 API requires a filesystem manifest path")
    try:
        manifest = load_character_phase4c2_manifest(manifest_path)
    except Phase4C2ManifestError as exc:
        raise Phase4C2EvidenceError(str(exc)) from exc
    derived = derive_character_phase4c2_source_contract(source_root)
    _verify_bound_phase4c1_unattached_disjoint(derived)
    manifest_files = [
        {"relative_path": item.relative_path, "format": item.format, "size_bytes": item.size_bytes,
         "sha256": item.sha256, "blob_oid": item.blob_oid, "role": item.role}
        for item in manifest.files
    ]
    if derived["files"] != manifest_files:
        _fail("sealed Phase 4C2 file inventory does not match source bytes")
    if derived["tables"] != manifest.tables:
        _fail("sealed Phase 4C2 primary row pointers/checksums do not match source")
    if derived["voice_targets"] != list(manifest.voice_targets):
        _fail("sealed Phase 4C2 Voice physical chain does not match source")
    for key in ("counts", "status_counts", "table_row_counts", "text_owner_field_counts", "occurrence_set_digests"):
        if derived[key] != manifest.expected[key]:
            _fail(f"sealed Phase 4C2 {key} mismatch")
    identity = expected_phase4c2_identity(manifest)
    return Phase4C2PreflightResult(manifest, Path(source_root).resolve(), derived=derived, **identity)


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


def _occurrence_ids(preflight: Phase4C2PreflightResult) -> dict[str, dict[Any, str]]:
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


def verify_phase4c2_occurrence_rows(
    conn: sqlite3.Connection,
    preflight: Phase4C2PreflightResult,
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


def _fingerprint(target: Path) -> dict[str, Any]:
    stat = target.stat()
    return {
        "sha256": sha256_bytes(target.read_bytes()),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _schema_versions(conn: sqlite3.Connection) -> list[int]:
    return [
        int(row[0])
        for row in conn.execute(
            "SELECT version FROM schema_migration ORDER BY version"
        )
    ]


def _same_family_run_ids(conn: sqlite3.Connection) -> list[str]:
    result: list[str] = []
    for row in conn.execute("SELECT id,scope_json FROM ingest_run ORDER BY id"):
        try:
            scope = json.loads(str(row["scope_json"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise sqlite3.IntegrityError(
                f"ingest_run scope_json is invalid: {row['id']}"
            ) from exc
        if isinstance(scope, dict) and scope.get("family") == RUN_FAMILY:
            result.append(str(row["id"]))
    return result


def _expected_run_scope(preflight: Phase4C2PreflightResult) -> str:
    return canonical_json({
        "family": RUN_FAMILY,
        "manifest_id": preflight.manifest.manifest_id,
        "selected_paths": [
            item["relative_path"] for item in preflight.derived["files"]
        ],
    })


def _expected_run_config(preflight: Phase4C2PreflightResult) -> str:
    return canonical_json({
        "manifest_raw_sha256": preflight.manifest.raw_sha256,
        "versions": preflight.manifest.versions,
        "logical_identity_created": False,
        "mcp_capability_added": False,
    })


_CHARACTER_COUNT_TABLES = {
    "rows": "character_row_occurrence",
    "raw_slots": "character_raw_slot_occurrence",
    "relations": "character_relation_occurrence",
    "text_resolutions": "character_text_resolution_occurrence",
    "text_candidates": "character_text_candidate_occurrence",
    "voice_containers": "character_voice_container_occurrence",
    "audio_variants": "character_audio_variant_occurrence",
}


def _assert_character_table_scope(
    conn: sqlite3.Connection,
    preflight: Phase4C2PreflightResult,
    *,
    expect_complete: bool,
) -> None:
    for count_key, table in _CHARACTER_COUNT_TABLES.items():
        total = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        scoped = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE ingest_run_id=?",
                (preflight.ingest_run_id,),
            ).fetchone()[0]
        )
        expected = (
            int(preflight.derived["counts"][count_key])
            if expect_complete
            else 0
        )
        if scoped != expected or total != expected:
            raise sqlite3.IntegrityError(
                f"Phase 4C2 {table} expected-set pollution: "
                f"scoped={scoped} total={total} expected={expected}"
            )


def _assert_source_and_run_contract(
    conn: sqlite3.Connection,
    preflight: Phase4C2PreflightResult,
) -> None:
    source = preflight.manifest.source
    catalog = conn.execute(
        "SELECT kind,url,license_note FROM source_catalog WHERE id=?",
        (source["source_id"],),
    ).fetchone()
    if catalog is None or tuple(catalog) != (
        "primary", source["upstream"], None,
    ):
        raise sqlite3.IntegrityError("Phase 4C2 source_catalog mismatch")
    snapshot = conn.execute(
        'SELECT source_id,release_label,build,"commit",tree,observed_at,status,metadata_json '
        "FROM source_snapshot WHERE id=?",
        (preflight.snapshot_id,),
    ).fetchone()
    if snapshot is None or tuple(snapshot) != (
        source["source_id"],
        source["release_label"],
        source["build"],
        source["commit"],
        source["tree"],
        source["observed_at"],
        "active",
        canonical_json({"sealed_source_manifest": True}),
    ):
        raise sqlite3.IntegrityError(
            "Phase 4C2 parser-independent source_snapshot mismatch"
        )
    parser = conn.execute(
        "SELECT parser_version,mapping_version,fingerprint,config_checksum,created_at "
        "FROM parser_build WHERE id=?",
        (preflight.parser_build_id,),
    ).fetchone()
    if parser is None or tuple(parser) != (
        preflight.manifest.versions["parser"],
        preflight.manifest.versions["projection"],
        "character-phase4c2-sealed-avatar10000003-canary",
        preflight.config_checksum,
        source["observed_at"],
    ):
        raise sqlite3.IntegrityError("Phase 4C2 parser_build mismatch")
    run = conn.execute(
        "SELECT snapshot_id,parser_build_id,scope_json,config_json,status,created_at "
        "FROM ingest_run WHERE id=?",
        (preflight.ingest_run_id,),
    ).fetchone()
    if run is None or tuple(run) != (
        preflight.snapshot_id,
        preflight.parser_build_id,
        _expected_run_scope(preflight),
        _expected_run_config(preflight),
        "complete",
        source["observed_at"],
    ):
        raise sqlite3.IntegrityError("Phase 4C2 ingest_run mismatch")
    family_ids = _same_family_run_ids(conn)
    if family_ids != [preflight.ingest_run_id]:
        raise sqlite3.IntegrityError(
            f"Phase 4C2 same-family ingest run set mismatch: {family_ids}"
        )

    file_ids: dict[str, str] = {}
    for item in preflight.derived["files"]:
        file_id = digest_id(
            "sf", preflight.snapshot_id, item["relative_path"]
        )
        file_ids[item["relative_path"]] = file_id
        row = conn.execute(
            "SELECT snapshot_id,relative_path,upstream_blob_oid,sha256,"
            "size_bytes,format FROM source_file WHERE id=?",
            (file_id,),
        ).fetchone()
        if row is None:
            raise sqlite3.IntegrityError(
                f"Phase 4C2 source_file mismatch: {item['relative_path']}"
            )
        actual_file = tuple(row)
        if (
            actual_file[0:2]
            != (
                preflight.snapshot_id,
                item["relative_path"],
            )
            or actual_file[2] not in (None, item["blob_oid"])
            or actual_file[3:]
            != (
                item["sha256"],
                item["size_bytes"],
                item["format"],
            )
        ):
            raise sqlite3.IntegrityError(
                f"Phase 4C2 source_file mismatch: {item['relative_path']}"
            )
    for item in preflight.derived["source_records"]:
        file_id = file_ids[item["relative_path"]]
        record_id = digest_id(
            "sr", file_id, item["source_pointer"]
        )
        row = conn.execute(
            "SELECT source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json "
            "FROM source_record WHERE id=?",
            (record_id,),
        ).fetchone()
        if row is None or tuple(row) != (
            file_id,
            item["source_pointer"],
            item["record_kind"],
            item["native_id"],
            item["raw_checksum"],
            canonical_json(item["raw_value"]),
        ):
            raise sqlite3.IntegrityError(
                "Phase 4C2 source_record mismatch: "
                f"{item['relative_path']}#{item['source_pointer']}"
            )


def _verify_existing_complete_bundle(
    conn: sqlite3.Connection,
    preflight: Phase4C2PreflightResult,
) -> None:
    versions = _schema_versions(conn)
    if versions != list(range(1, 11)):
        raise sqlite3.IntegrityError(
            f"existing Phase 4C2 schema is not exactly 1..10: {versions}"
        )
    verify_character_phase4c2_schema_signature(conn)
    _assert_source_and_run_contract(conn, preflight)
    verify_phase4c2_occurrence_rows(
        conn, preflight, insert_missing=False
    )
    _assert_character_table_scope(
        conn, preflight, expect_complete=True
    )
    static_leak = sum(
        int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE ingest_run_id=?",
                (preflight.ingest_run_id,),
            ).fetchone()[0]
        )
        for table in (
            "static_evidence_container",
            "static_segment_occurrence",
            "static_relationship_occurrence",
            "static_text_resolution_occurrence",
            "static_text_candidate_occurrence",
        )
    )
    if static_leak:
        raise sqlite3.IntegrityError(
            "Phase 4C2 run leaked into static business tables"
        )
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise sqlite3.IntegrityError(
            f"Phase 4C2 foreign key violations: {violations[:3]}"
        )


def _inspect_target_readonly(
    target: Path,
    preflight: Phase4C2PreflightResult,
) -> bool:
    if not target.is_file():
        _fail(
            f"existing combined evidence database is required: {target}"
        )
    uri = target.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
            versions = _schema_versions(conn)
            if versions not in (
                list(range(1, 10)),
                list(range(1, 11)),
            ):
                raise sqlite3.IntegrityError(
                    "Phase 4C2 input schema must be exactly 1..9 "
                    f"or complete known 1..10: {versions}"
                )
            if versions[-1] == 9:
                character_objects = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM sqlite_master "
                        "WHERE name GLOB 'character_*' "
                        "OR name GLOB 'trg_character_*' "
                        "OR name GLOB 'idx_character_*' "
                        "OR name GLOB 'uq_character_*'"
                    ).fetchone()[0]
                )
                if character_objects:
                    raise sqlite3.IntegrityError(
                        "schema9 input contains premature Character objects"
                    )
            else:
                verify_character_phase4c2_schema_signature(conn)
            family_ids = _same_family_run_ids(conn)
            expected_row = conn.execute(
                "SELECT 1 FROM ingest_run WHERE id=?",
                (preflight.ingest_run_id,),
            ).fetchone()
            if family_ids not in (
                [],
                [preflight.ingest_run_id],
            ):
                raise sqlite3.IntegrityError(
                    f"Phase 4C2 same-family ingest run set mismatch: {family_ids}"
                )
            if expected_row is not None:
                if family_ids != [preflight.ingest_run_id]:
                    raise sqlite3.IntegrityError(
                        "expected Phase 4C2 run has a mismatched family scope"
                    )
                _verify_existing_complete_bundle(conn, preflight)
                return True
            if family_ids:
                raise sqlite3.IntegrityError(
                    f"unexpected Phase 4C2 sibling run: {family_ids}"
                )
            if versions[-1] == 10:
                _assert_character_table_scope(
                    conn, preflight, expect_complete=False
                )
            return False
        finally:
            conn.close()
    except (
        CharacterPhase4C2SchemaError,
        sqlite3.Error,
        OSError,
        ValueError,
        TypeError,
        KeyError,
    ) as exc:
        raise Phase4C2EvidenceError(str(exc)) from exc


def _execute_migration_0010_atomic(
    conn: sqlite3.Connection,
) -> None:
    paths = sorted(
        MIGRATIONS_DIR.glob(
            "0010_phase4c2_character_canary_evidence.sql"
        )
    )
    if len(paths) != 1:
        raise sqlite3.IntegrityError(
            f"expected exactly one Phase 4C2 migration, found {len(paths)}"
        )
    buffer = ""
    for line in paths[0].read_text(encoding="utf-8").splitlines(
        keepends=True
    ):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            buffer = ""
            if statement:
                conn.execute(statement)
    if buffer.strip():
        raise sqlite3.IntegrityError(
            "Phase 4C2 migration has an incomplete SQL statement"
        )


def _ensure_exact_id_row(
    conn: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    values: tuple[Any, ...],
) -> None:
    row = conn.execute(
        f"SELECT {','.join(columns)} FROM {table} WHERE id=?",
        (values[0],),
    ).fetchone()
    if row is None:
        conn.execute(
            f"INSERT INTO {table}({','.join(columns)}) "
            f"VALUES ({','.join('?' for _ in columns)})",
            values,
        )
        return
    if tuple(row[column] for column in columns) != values:
        raise sqlite3.IntegrityError(
            f"Phase 4C2 immutable {table} mismatch: {values[0]}"
        )


def _write_source_parser_run_and_occurrences(
    db: Database,
    preflight: Phase4C2PreflightResult,
) -> None:
    conn = db.conn
    source = preflight.manifest.source
    _ensure_exact_id_row(
        conn,
        "source_catalog",
        ("id", "kind", "url", "license_note"),
        (
            source["source_id"],
            "primary",
            source["upstream"],
            None,
        ),
    )
    snapshot_id = preflight.snapshot_id
    snapshot = conn.execute(
        'SELECT source_id,release_label,build,"commit",tree,observed_at,status,metadata_json '
        "FROM source_snapshot WHERE id=?",
        (snapshot_id,),
    ).fetchone()
    expected_snapshot = (
        source["source_id"],
        source["release_label"],
        source["build"],
        source["commit"],
        source["tree"],
        source["observed_at"],
        "active",
        canonical_json({"sealed_source_manifest": True}),
    )
    if snapshot is None:
        conn.execute(
            'INSERT INTO source_snapshot('
            'id,source_id,release_label,build,"commit",tree,observed_at,'
            "parser_version,mapping_version,status,metadata_json"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                snapshot_id,
                *expected_snapshot[:6],
                "legacy-unscoped",
                "legacy-unscoped",
                *expected_snapshot[6:],
            ),
        )
    elif tuple(snapshot) != expected_snapshot:
        raise sqlite3.IntegrityError(
            "Phase 4C2 parser-independent source_snapshot mismatch"
        )

    file_ids: dict[str, str] = {}
    for item in preflight.derived["files"]:
        file_id = digest_id(
            "sf", snapshot_id, item["relative_path"]
        )
        file_ids[item["relative_path"]] = file_id
        row = conn.execute(
            "SELECT snapshot_id,relative_path,upstream_blob_oid,sha256,"
            "size_bytes,format FROM source_file WHERE id=?",
            (file_id,),
        ).fetchone()
        expected_file = (
            snapshot_id,
            item["relative_path"],
            item["blob_oid"],
            item["sha256"],
            item["size_bytes"],
            item["format"],
        )
        if row is None:
            conn.execute(
                "INSERT INTO source_file("
                "id,snapshot_id,relative_path,upstream_blob_oid,sha256,"
                "size_bytes,format,root_path"
                ") VALUES (?,?,?,?,?,?,?,?)",
                (
                    file_id,
                    *expected_file,
                    str(preflight.source_root),
                ),
            )
        else:
            actual = tuple(row)
            if (
                actual[0:2] != expected_file[0:2]
                or actual[3:] != expected_file[3:]
            ):
                raise sqlite3.IntegrityError(
                    f"Phase 4C2 source_file mismatch: {item['relative_path']}"
                )
            if actual[2] not in (None, item["blob_oid"]):
                raise sqlite3.IntegrityError(
                    "Phase 4C2 source_file blob OID mismatch: "
                    f"{item['relative_path']}"
                )

    for item in preflight.derived["source_records"]:
        file_id = file_ids[item["relative_path"]]
        record_id = digest_id(
            "sr", file_id, item["source_pointer"]
        )
        _ensure_exact_id_row(
            conn,
            "source_record",
            (
                "id",
                "source_file_id",
                "pointer",
                "record_kind",
                "native_id",
                "raw_sha256",
                "raw_json",
            ),
            (
                record_id,
                file_id,
                item["source_pointer"],
                item["record_kind"],
                item["native_id"],
                item["raw_checksum"],
                canonical_json(item["raw_value"]),
            ),
        )
    _ensure_exact_id_row(
        conn,
        "parser_build",
        (
            "id",
            "parser_version",
            "mapping_version",
            "fingerprint",
            "config_checksum",
            "created_at",
        ),
        (
            preflight.parser_build_id,
            preflight.manifest.versions["parser"],
            preflight.manifest.versions["projection"],
            "character-phase4c2-sealed-avatar10000003-canary",
            preflight.config_checksum,
            source["observed_at"],
        ),
    )
    if conn.execute(
        "SELECT 1 FROM ingest_run WHERE id=?",
        (preflight.ingest_run_id,),
    ).fetchone() is not None:
        raise sqlite3.IntegrityError(
            "Phase 4C2 run appeared after readonly preflight"
        )
    conn.execute(
        "INSERT INTO ingest_run("
        "id,snapshot_id,parser_build_id,scope_json,config_json,status,created_at"
        ") VALUES (?,?,?,?,?,?,?)",
        (
            preflight.ingest_run_id,
            preflight.snapshot_id,
            preflight.parser_build_id,
            _expected_run_scope(preflight),
            _expected_run_config(preflight),
            "running",
            source["observed_at"],
        ),
    )
    verify_phase4c2_occurrence_rows(
        conn, preflight, insert_missing=True
    )
    if conn.execute(
        "UPDATE ingest_run SET status='complete' "
        "WHERE id=? AND status='running'",
        (preflight.ingest_run_id,),
    ).rowcount != 1:
        raise sqlite3.IntegrityError(
            "Phase 4C2 completion transition failed"
        )
    _verify_existing_complete_bundle(conn, preflight)


_SQLITE_SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")


def _temporary_database_path(target: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{target.name}.phase4c2-",
        suffix=".sqlite",
        dir=target.parent,
    )
    os.close(descriptor)
    return Path(name)


def _cleanup_temporary_database(path: Path) -> None:
    for candidate in (
        path,
        *(Path(str(path) + suffix) for suffix in _SQLITE_SIDECAR_SUFFIXES),
    ):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass


def _assert_no_sqlite_sidecars(
    target: Path,
    *,
    stage: str,
) -> None:
    present = [
        str(Path(str(target) + suffix))
        for suffix in _SQLITE_SIDECAR_SUFFIXES
        if Path(str(target) + suffix).exists()
    ]
    if present:
        raise Phase4C2EvidenceError(
            f"Phase 4C2 requires an offline sidecar-free database "
            f"{stage}: {present}"
        )


def _capture_database_identity(target: Path) -> _DatabaseIdentity:
    before = target.stat()
    raw = target.read_bytes()
    after = target.stat()
    before_tuple = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
    )
    after_tuple = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
    )
    if before_tuple != after_tuple or len(raw) != after.st_size:
        raise Phase4C2EvidenceError(
            "Phase 4C2 database changed while capturing CAS identity"
        )
    return _DatabaseIdentity(
        device=after.st_dev,
        inode=after.st_ino,
        mode=after.st_mode,
        sha256=sha256_bytes(raw),
        size_bytes=after.st_size,
        mtime_ns=after.st_mtime_ns,
    )


def _assert_database_identity(
    target: Path,
    expected: _DatabaseIdentity,
    *,
    stage: str,
) -> None:
    actual = _capture_database_identity(target)
    if actual != expected:
        raise Phase4C2EvidenceError(
            "Phase 4C2 input database CAS mismatch "
            f"{stage}: expected={expected} actual={actual}"
        )


def _assert_delete_journal_mode(
    conn: sqlite3.Connection,
) -> None:
    mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
    if mode.casefold() != "delete":
        raise Phase4C2EvidenceError(
            "Phase 4C2 requires DELETE journal mode on the offline "
            f"input database, observed {mode!r}"
        )


def _capture_offline_database_identity(
    target: Path,
) -> _DatabaseIdentity:
    _assert_no_sqlite_sidecars(target, stage="before snapshot")
    identity = _capture_database_identity(target)
    uri = target.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(
        uri, uri=True, timeout=0
    ) as readonly:
        readonly.execute("PRAGMA query_only=ON")
        readonly.execute("PRAGMA busy_timeout=0")
        _assert_delete_journal_mode(readonly)
    _assert_no_sqlite_sidecars(target, stage="before snapshot")
    _assert_database_identity(
        target, identity, stage="before snapshot"
    )
    return identity


def _backup_consistent_snapshot(
    target: Path,
    expected_identity: _DatabaseIdentity,
) -> Path:
    _assert_no_sqlite_sidecars(target, stage="before SQLite backup")
    _assert_database_identity(
        target, expected_identity, stage="before SQLite backup"
    )
    temporary = _temporary_database_path(target)
    try:
        source_uri = target.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(
            source_uri, uri=True, timeout=0
        ) as source, sqlite3.connect(temporary) as destination:
            source.execute("PRAGMA query_only=ON")
            source.execute("PRAGMA busy_timeout=0")
            destination.execute("PRAGMA busy_timeout=0")
            _assert_delete_journal_mode(source)
            source.backup(destination)
            destination.commit()
        _assert_no_sqlite_sidecars(
            target, stage="after SQLite backup"
        )
        _assert_database_identity(
            target, expected_identity, stage="after SQLite backup"
        )
        return temporary
    except Exception:
        _cleanup_temporary_database(temporary)
        raise


def _finalize_replace_with_cas(
    target: Path,
    temporary: Path,
    expected_identity: _DatabaseIdentity,
) -> None:
    _assert_no_sqlite_sidecars(
        target, stage="before exclusive finalization"
    )
    _assert_database_identity(
        target, expected_identity, stage="before exclusive finalization"
    )
    uri = target.resolve().as_uri() + "?mode=rw"
    lock = sqlite3.connect(
        uri,
        uri=True,
        timeout=0,
        isolation_level=None,
    )
    locked = False
    try:
        lock.execute("PRAGMA busy_timeout=0")
        _assert_delete_journal_mode(lock)
        lock.execute("BEGIN EXCLUSIVE")
        locked = True
        _assert_no_sqlite_sidecars(
            target, stage="under exclusive finalization lock"
        )
        _assert_database_identity(
            target,
            expected_identity,
            stage="under exclusive finalization lock",
        )
        os.replace(temporary, target)
    finally:
        if locked:
            try:
                lock.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        lock.close()


def _append_on_copy_atomically(
    target: Path,
    preflight: Phase4C2PreflightResult,
    original_versions: list[int],
    original_identity: _DatabaseIdentity,
) -> None:
    temporary = _backup_consistent_snapshot(
        target, original_identity
    )
    replaced = False
    try:
        with Database(temporary) as db:
            conn = db.conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                if original_versions == list(range(1, 10)):
                    _execute_migration_0010_atomic(conn)
                    conn.execute(
                        "INSERT INTO schema_migration(version,applied_at) "
                        "VALUES (?,?)",
                        (
                            10,
                            preflight.manifest.source["observed_at"],
                        ),
                    )
                if _schema_versions(conn) != list(range(1, 11)):
                    raise sqlite3.IntegrityError(
                        "Phase 4C2 transactional migration did not produce "
                        "exact schema 1..10"
                    )
                verify_character_phase4c2_schema_signature(conn)
                _write_source_parser_run_and_occurrences(
                    db, preflight
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        uri = temporary.resolve().as_uri() + "?mode=ro"
        readonly = sqlite3.connect(uri, uri=True)
        readonly.row_factory = sqlite3.Row
        try:
            readonly.execute("PRAGMA query_only=ON")
            _verify_existing_complete_bundle(readonly, preflight)
        finally:
            readonly.close()
        os.chmod(
            temporary, stat.S_IMODE(original_identity.mode)
        )
        _finalize_replace_with_cas(
            target, temporary, original_identity
        )
        replaced = True
    finally:
        if not replaced:
            _cleanup_temporary_database(temporary)


def _build_report(
    target: Path,
    preflight: Phase4C2PreflightResult,
) -> dict[str, Any]:
    return {
        "status": "complete",
        "db_path": str(target.resolve()),
        "snapshot_id": preflight.snapshot_id,
        "parser_build_id": preflight.parser_build_id,
        "ingest_run_id": preflight.ingest_run_id,
        "config_checksum": preflight.config_checksum,
        "manifest": {
            "manifest_id": preflight.manifest.manifest_id,
            "raw_sha256": preflight.manifest.raw_sha256,
        },
        "database_fingerprint": _fingerprint(target),
        "counts": preflight.derived["counts"],
        "status_counts": preflight.derived["status_counts"],
        "table_row_counts": preflight.derived["table_row_counts"],
        "text_owner_field_counts": preflight.derived[
            "text_owner_field_counts"
        ],
        "occurrence_set_digests": preflight.derived[
            "occurrence_set_digests"
        ],
        "scope": {
            "avatar_id": EXPECTED_AVATAR_ID,
            "selected_paths": [
                item["relative_path"]
                for item in preflight.derived["files"]
            ],
            "full_character_scan": False,
            "full_voice_scan": False,
            "logical_identity_created": False,
            "logical_passage_created": False,
            "mcp_capability_added": False,
            "future_context_scope": "voice_container_only",
        },
    }


def ingest_character_phase4c2_evidence_bundle(
    db_path: str | Path,
    source_root: str | Path,
    manifest_path: str | Path,
) -> dict[str, Any]:
    # The write-capable canary builder requires POSIX rename/inode/locking
    # semantics. The generator, source preflight, and read-only verifier
    # remain cross-platform entry points.
    _require_posix_builder_runtime()
    # Source, manifest, and Phase 4C1 audit preflight completes before any
    # write connection or temporary output database is created.
    preflight = preflight_character_phase4c2_evidence_bundle(
        source_root, manifest_path
    )
    target = Path(db_path).resolve()
    try:
        original_identity = _capture_offline_database_identity(
            target
        )
        existing = _inspect_target_readonly(target, preflight)
        if existing:
            _assert_database_identity(
                target,
                original_identity,
                stage="after idempotent read-only verification",
            )
            return _build_report(target, preflight)
        uri = target.as_uri() + "?mode=ro"
        with sqlite3.connect(
            uri, uri=True, timeout=0
        ) as readonly:
            readonly.execute("PRAGMA query_only=ON")
            readonly.execute("PRAGMA busy_timeout=0")
            _assert_delete_journal_mode(readonly)
            original_versions = [
                int(row[0])
                for row in readonly.execute(
                    "SELECT version FROM schema_migration ORDER BY version"
                )
            ]
        _assert_no_sqlite_sidecars(
            target, stage="after read-only inspection"
        )
        _assert_database_identity(
            target,
            original_identity,
            stage="after read-only inspection",
        )
        _append_on_copy_atomically(
            target,
            preflight,
            original_versions,
            original_identity,
        )
    except Phase4C2EvidenceError:
        raise
    except (
        CharacterPhase4C2SchemaError,
        sqlite3.Error,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
    ) as exc:
        raise Phase4C2EvidenceError(str(exc)) from exc
    return _build_report(target, preflight)


__all__ = [
    "Phase4C2EvidenceError",
    "Phase4C2PreflightResult",
    "RUN_FAMILY",
    "derive_character_phase4c2_source_contract",
    "expected_phase4c2_identity",
    "ingest_character_phase4c2_evidence_bundle",
    "preflight_character_phase4c2_evidence_bundle",
    "verify_phase4c2_occurrence_rows",
    "verify_phase4c2_source_pin",
]
