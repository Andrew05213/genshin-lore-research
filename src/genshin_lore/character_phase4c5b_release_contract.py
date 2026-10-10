"""Independent Phase 4C5B release source oracle.

This module is deliberately a separate family implementation.  It consumes
the fixed 15-file source closure, keeps every physical occurrence scoped to a
snapshot and ingest run, and writes only the existing Schema 10 tables from
migration 0010.  It does not create logical Character identity.
"""

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

from .character_phase4c5b_manifest import (
    EXPECTED_BUILD,
    EXPECTED_COMMIT,
    EXPECTED_COUNTS,
    EXPECTED_COVERAGE,
    EXPECTED_MANIFEST_ID,
    EXPECTED_OWNER_FIELD_COUNTS,
    EXPECTED_PATHS,
    EXPECTED_SNAPSHOT_ID,
    EXPECTED_STATUS_COUNTS,
    EXPECTED_TABLES,
    EXPECTED_TABLE_ROW_COUNTS,
    EXPECTED_TEXT_FIELDS,
    EXPECTED_TREE,
    EXPECTED_VERSIONS,
    ITEM_PATHS,
    LOCALES,
    LUT_PATH,
    TEXTMAP_PATHS,
    CharacterPhase4C5BManifest,
    Phase4C5BManifestError,
    load_character_phase4c5b_manifest,
    strict_json_load,
)
from .util import canonical_json, digest_id, normalize_text, sha256_bytes, sha256_text


RUN_FAMILY = "character_phase4c5b_full_source_closure"
PARSER_FINGERPRINT = "character-phase4c5b-full-boundary-projection"


class Phase4C5BReleaseContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class Phase4C5BPreflightResult:
    manifest: CharacterPhase4C5BManifest
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
    raise Phase4C5BReleaseContractError(message)


def _raw_checksum(value: Any) -> str:
    return sha256_text(value if isinstance(value, str) else canonical_json(value))


def _set_digest(items: list[dict[str, Any]]) -> str:
    encoded = sorted(canonical_json(item) for item in items)
    return sha256_text(canonical_json(encoded))


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        _fail(f"cannot verify pinned source checkout: {exc}")
    return result.stdout.strip()


def verify_phase4c5b_source_pin(root: str | Path) -> None:
    source = Path(root).resolve()
    if not source.is_dir():
        _fail(f"source root is not a directory: {source}")
    if _git(source, "status", "--porcelain=v1"):
        _fail("Phase 4C5B source checkout is not clean")
    if _git(source, "rev-parse", "HEAD") != EXPECTED_COMMIT:
        _fail("Phase 4C5B source commit mismatch")
    if _git(source, "rev-parse", "HEAD^{tree}") != EXPECTED_TREE:
        _fail("Phase 4C5B source tree mismatch")


def _file_specs(root: Path) -> list[dict[str, Any]]:
    roles = {
        **{f"ExcelBinOutput/{table}.json": "primary_table" for table in EXPECTED_TABLES},
        **{path: "explicit_textmap_candidate_shard" for locale in TEXTMAP_PATHS.values() for path in locale.values()},
        LUT_PATH: "voice_lut",
        **{path: "fetter_voice_items_container_file" for path in ITEM_PATHS},
    }
    result: list[dict[str, Any]] = []
    for relative in EXPECTED_PATHS:
        path = root / relative
        try:
            raw = path.read_bytes()
        except OSError as exc:
            _fail(f"cannot read sealed source file {relative}: {exc}")
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
        return strict_json_load(root / relative, label=relative)
    except Phase4C5BManifestError as exc:
        _fail(str(exc))


def _row_key(table: str, row_index: int) -> str:
    return f"{table}:{row_index}"


def _selected_rows(root: Path) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    occurrences: list[dict[str, Any]] = []
    all_tables: dict[str, list[dict[str, Any]]] = {}
    table_contract: dict[str, dict[str, Any]] = {}
    for table, native_field in EXPECTED_TABLES.items():
        relative = f"ExcelBinOutput/{table}.json"
        rows = _json(root, relative)
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            _fail(f"{table} must be an array of objects")
        if len(rows) != EXPECTED_TABLE_ROW_COUNTS[table]:
            _fail(f"{table} row count changed: {len(rows)}")
        all_tables[table] = rows
        specs: list[dict[str, Any]] = []
        for row_index, row in enumerate(rows):
            avatar_value = row.get("id") if table == "AvatarExcelConfigData" else row.get("avatarId")
            native_value = row.get(native_field)
            if isinstance(native_value, bool) or not isinstance(native_value, int):
                _fail(f"{table}/{row_index}/{native_field} is not an integer locator")
            item = {
                "row_index": row_index,
                "pointer": f"/{row_index}",
                "native_locator_value": str(native_value),
                "avatar_id": str(avatar_value) if avatar_value is not None else None,
                "raw_sha256": _raw_checksum(row),
            }
            specs.append(item)
            occurrences.append({
                "relative_path": relative,
                "source_pointer": f"/{row_index}",
                "table_name": table,
                "row_index": row_index,
                "native_locator_field": native_field,
                "native_locator_value": str(native_value),
                "avatar_id": str(avatar_value) if avatar_value is not None else None,
                "raw_value": row,
                "raw_checksum": item["raw_sha256"],
            })
        table_contract[table] = {
            "relative_path": relative,
            "native_locator_field": native_field,
            "row_count": len(specs),
            "rows": specs,
            "coordinate_digest": _set_digest([{"relative_path": relative, "source_pointer": item["pointer"], "raw_sha256": item["raw_sha256"], "native_locator_value": item["native_locator_value"]} for item in specs]),
        }
    occurrences.sort(key=lambda item: (item["relative_path"], item["row_index"]))
    return occurrences, all_tables, table_contract


def _derive_raw_slots(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    condition_fields = {
        "FetterInfoExcelConfigData": ("finishConds",),
        "FetterStoryExcelConfigData": ("finishConds", "openConds"),
        "FettersExcelConfigData": ("openConds",),
    }
    for occurrence in rows:
        table, row_index, row = occurrence["table_name"], occurrence["row_index"], occurrence["raw_value"]
        for field in condition_fields.get(table, ()):
            value = row.get(field)
            if not isinstance(value, list):
                _fail(f"{table}/{row_index}/{field} must be an array")
            for array_index, element in enumerate(value):
                if not isinstance(element, dict):
                    _fail(f"{table}/{row_index}/{field}/{array_index} must be an object")
                slots.append({
                    "row_key": _row_key(table, row_index),
                    "source_pointer": f"/{row_index}/{field}/{array_index}",
                    "field_name": field, "array_index": array_index,
                    "slot_kind": "condition_element", "observation_status": "observed",
                    "raw_value": element,
                    "metadata": {"semantic_interpretation": "none_raw_condition_element", "condition_shape": "empty" if not element else "nonempty"},
                })
        for field, value in row.items():
            if field == "tips":
                if not isinstance(value, list):
                    _fail(f"{table}/{row_index}/tips must be an array")
                for array_index, element in enumerate(value):
                    if isinstance(element, bool) or not isinstance(element, int):
                        _fail(f"{table}/{row_index}/tips/{array_index} is not an opaque integer")
                    slots.append({
                        "row_key": _row_key(table, row_index), "source_pointer": f"/{row_index}/tips/{array_index}",
                        "field_name": "tips", "array_index": array_index, "slot_kind": "opaque_integer",
                        "observation_status": "opaque_unresolved", "raw_value": element,
                        "metadata": {"reason": "tips_array_integer", "textmap_lookup_performed": False},
                    })
            elif field.isupper() and any(character.isalpha() for character in field) and isinstance(value, int) and not isinstance(value, bool):
                alias = table == "AvatarExcelConfigData" and field == "PFODNJGLJNP"
                if alias and value != row.get("descTextMapHash"):
                    _fail("Avatar PFODNJGLJNP alias observation is no longer equal")
                slots.append({
                    "row_key": _row_key(table, row_index), "source_pointer": f"/{row_index}/{field}",
                    "field_name": field, "array_index": None,
                    "slot_kind": "opaque_alias" if alias else "opaque_integer",
                    "observation_status": "observed_equal" if alias else "opaque_unresolved",
                    "raw_value": value,
                    "metadata": ({"alias_of_source_pointer": f"/{row_index}/descTextMapHash", "identity_effect": "none", "text_owner_effect": "none"} if alias else {"reason": "obfuscated_integer_field", "textmap_lookup_performed": False}),
                })
    slots.sort(key=lambda item: (item["row_key"], item["source_pointer"]))
    return slots


def _derive_text(root: Path, rows: list[dict[str, Any]], relations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    shards: dict[tuple[str, str], dict[str, Any]] = {}
    for locale in LOCALES:
        for kind in ("main", "medium"):
            value = _json(root, TEXTMAP_PATHS[locale][kind])
            if not isinstance(value, dict):
                _fail(f"TextMap shard is not an object: {locale}/{kind}")
            shards[(locale, kind)] = value
    resolutions: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    field_counts: Counter[str] = Counter()
    for occurrence in rows:
        table, row_index, row = occurrence["table_name"], occurrence["row_index"], occurrence["raw_value"]
        for field in EXPECTED_TEXT_FIELDS[table]:
            value = row.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                _fail(f"{table}/{row_index}/{field} is not a positive TextMap hash")
            text_hash, source_pointer = str(value), f"/{row_index}/{field}"
            owner_key = f"{table}:{row_index}:{field}"
            field_counts[f"{table}.{field}"] += 1
            relations.append({
                "row_key": _row_key(table, row_index), "source_pointer": source_pointer,
                "relation_kind": "textmap_owner_candidate", "field_role": field,
                "target_family": "TextMap", "target_candidate_key": {"textmap_hash": text_hash},
                "resolution_status": "resolved", "target_row_key": None, "target_voice_key": None,
                "candidate_scopes": [], "raw_value": value,
                "metadata": {"owner_key": owner_key, "equal_hash_merge_effect": "none"},
            })
            for locale in LOCALES:
                main, medium = shards[(locale, "main")].get(text_hash), shards[(locale, "medium")].get(text_hash)
                if main is not None and not isinstance(main, str):
                    _fail(f"TextMap main candidate is not text: {locale}/{text_hash}")
                if medium is not None and not isinstance(medium, str):
                    _fail(f"TextMap medium candidate is not text: {locale}/{text_hash}")
                if main is None and medium is None:
                    status, selected = "missing", None
                elif main is not None and medium is None:
                    status, selected = "unique_main", main
                elif main is None and medium is not None:
                    status, selected = "unique_medium", medium
                elif main == medium:
                    status, selected = "both_equal", main
                else:
                    status, selected = "both_conflict", None
                scopes: list[dict[str, Any]] = []
                for kind, text in (("main", main), ("medium", medium)):
                    if text is None:
                        continue
                    relative = TEXTMAP_PATHS[locale][kind]
                    scopes.append({"relative_path": relative, "source_pointer": f"/{text_hash}", "shard_kind": kind})
                    normalized = normalize_text(text)
                    candidates.append({
                        "owner_key": owner_key, "row_key": _row_key(table, row_index),
                        "relationship_source_pointer": source_pointer, "locale": locale,
                        "textmap_hash": text_hash, "resolution_status": status,
                        "source_relative_path": relative, "source_pointer": f"/{text_hash}",
                        "shard_kind": kind, "raw_text": text, "normalized_text": normalized,
                        "raw_checksum": sha256_text(text), "normalized_checksum": sha256_text(normalized),
                    })
                normalized = None if selected is None else normalize_text(selected)
                resolutions.append({
                    "owner_key": owner_key, "row_key": _row_key(table, row_index),
                    "relationship_source_pointer": source_pointer, "locale": locale,
                    "textmap_hash": text_hash, "resolution_status": status,
                    "raw_text": selected, "normalized_text": normalized,
                    "raw_checksum": None if selected is None else sha256_text(selected),
                    "normalized_checksum": None if normalized is None else sha256_text(normalized),
                    "candidate_scopes": scopes,
                })
    resolutions.sort(key=lambda item: (item["owner_key"], item["locale"]))
    candidates.sort(key=lambda item: (item["owner_key"], item["locale"], item["shard_kind"]))
    return resolutions, candidates, dict(sorted(field_counts.items()))


def _derive_voice(root: Path, rows: list[dict[str, Any]], relations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    lut = _json(root, LUT_PATH)
    if not isinstance(lut, dict):
        _fail("Voice LUT must be an object")
    item_roots = {path: _json(root, path) for path in ITEM_PATHS}
    if not all(isinstance(value, dict) for value in item_roots.values()):
        _fail("Voice Items files must be objects")
    lut_records: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for key, value in lut.items():
        if isinstance(value, dict) and value.get("PKPBCKFKHEE") == "Fetter":
            slot = str(value.get("MHDDLMLPIBB"))
            lut_records.setdefault(slot, []).append((str(key), value))
    containers: list[dict[str, Any]] = []
    audio: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    by_slot: dict[str, dict[str, Any]] = {}
    for slot, candidates in lut_records.items():
        if len(candidates) != 1:
            _fail(f"Fetter voice slot {slot} has {len(candidates)} LUT candidates")
        lut_key, lut_record = candidates[0]
        item_path = f"BinOutput/Voice/Items/{lut_record.get('DFEELKOFAKE')}.json"
        item_root = item_roots.get(item_path)
        if item_root is None:
            _fail(f"Fetter voice slot {slot} selects an out-of-bound Items file")
        item_candidates = [(str(key), value) for key, value in item_root.items() if isinstance(value, dict) and value.get("PKPBCKFKHEE") == "Fetter" and str(value.get("MHDDLMLPIBB")) == slot]
        if len(item_candidates) != 1:
            _fail(f"Fetter voice slot {slot} has {len(item_candidates)} Items candidates")
        item_key, item_record = item_candidates[0]
        if item_record.get("AIFHPAAHJOC") != item_key:
            _fail(f"Fetter Items UUID/object-key mismatch for slot {slot}")
        variants = item_record.get("BOICINMJKCP")
        if not isinstance(variants, list):
            _fail(f"Fetter Items BOICINMJKCP is not an array for slot {slot}")
        voice_key = f"{item_path}:{item_key}"
        container = {
            "voice_key": voice_key, "voice_slot": slot,
            "lut_relative_path": LUT_PATH, "lut_source_pointer": f"/{lut_key}",
            "lut_raw_value": lut_record, "lut_raw_checksum": _raw_checksum(lut_record),
            "item_relative_path": item_path, "item_source_pointer": f"/{item_key}",
            "item_raw_value": item_record, "item_raw_checksum": _raw_checksum(item_record),
            "resolution_status": "resolved_unique_container",
            "candidate_scopes": {"lut_candidates": [{"relative_path": LUT_PATH, "source_pointer": f"/{lut_key}"}], "item_candidates": [{"relative_path": item_path, "source_pointer": f"/{item_key}"}], "target_scope": {"relative_path": item_path, "source_pointer": f"/{item_key}"}},
            "audio_variant_count": len(variants),
        }
        containers.append(container)
        by_slot[slot] = container
        for array_index, variant in enumerate(variants):
            if not isinstance(variant, dict) or not isinstance(variant.get("NMAENMONCGJ"), str) or not isinstance(variant.get("IKNGALOCDCK"), str):
                _fail(f"Fetter audio variant is invalid for slot {slot}/{array_index}")
            audio.append({
                "voice_key": voice_key, "item_source_pointer": f"/{item_key}",
                "source_pointer": f"/{item_key}/BOICINMJKCP/{array_index}", "array_index": array_index,
                "audio_path": variant["IKNGALOCDCK"], "audio_path_status": "observed",
                "character_child_selector": variant["NMAENMONCGJ"], "selector_status": "observed_unresolved",
                "metadata": {"identity_effect": "none", "universal_foreign_key_created": False},
            })
    for occurrence in rows:
        if occurrence["table_name"] != "FettersExcelConfigData":
            continue
        row, row_index = occurrence["raw_value"], occurrence["row_index"]
        raw_slot = row.get("voiceFile")
        if isinstance(raw_slot, bool) or not isinstance(raw_slot, (str, int)):
            _fail(f"Fetters/{row_index}/voiceFile is not a physical slot locator")
        slot = str(raw_slot)
        container = by_slot.get(slot)
        if container is None:
            relations.append({
                "row_key": _row_key("FettersExcelConfigData", row_index), "source_pointer": f"/{row_index}/voiceFile",
                "relation_kind": "voice_container_attachment", "field_role": "voiceFile_physical_container_locator",
                "target_family": "FetterVoiceItemsContainer", "target_candidate_key": {"voice_slot": slot},
                "resolution_status": "dangling", "target_row_key": None, "target_voice_key": None,
                "candidate_scopes": [], "raw_value": raw_slot, "metadata": {"physical_container_missing": True},
            })
            continue
        targets.append({
            "source_row_pointer": f"/{row_index}/voiceFile", "voice_slot": slot,
            "lut_path": LUT_PATH, "lut_pointer": container["lut_source_pointer"],
            "lut_raw_sha256": container["lut_raw_checksum"], "item_path": container["item_relative_path"],
            "item_pointer": container["item_source_pointer"], "item_raw_sha256": container["item_raw_checksum"],
            "audio_variant_count": container["audio_variant_count"],
        })
        relations.append({
            "row_key": _row_key("FettersExcelConfigData", row_index), "source_pointer": f"/{row_index}/voiceFile",
            "relation_kind": "voice_container_attachment", "field_role": "voiceFile_physical_container_locator",
            "target_family": "FetterVoiceItemsContainer", "target_candidate_key": {"voice_slot": slot},
            "resolution_status": "resolved_unique_container", "target_row_key": None, "target_voice_key": container["voice_key"],
            "candidate_scopes": container["candidate_scopes"], "raw_value": raw_slot,
            "metadata": {"character_selector_used_for_selection": False, "naming_convention_fk_created": False},
        })
    containers.sort(key=lambda item: item["voice_key"])
    audio.sort(key=lambda item: (item["voice_key"], item["array_index"]))
    targets.sort(key=lambda item: int(item["source_row_pointer"].split("/")[1]))
    return containers, audio, targets


def derive_character_phase4c5b_source_contract(source_root: str | Path) -> dict[str, Any]:
    root = Path(source_root).resolve()
    verify_phase4c5b_source_pin(root)
    files = _file_specs(root)
    rows, all_tables, tables = _selected_rows(root)
    raw_slots = _derive_raw_slots(rows)
    relations: list[dict[str, Any]] = []
    avatar_rows = {item["avatar_id"]: item for item in rows if item["table_name"] == "AvatarExcelConfigData"}
    for occurrence in rows:
        if occurrence["table_name"] == "AvatarExcelConfigData":
            continue
        target = avatar_rows.get(occurrence["avatar_id"])
        relations.append({
            "row_key": _row_key(occurrence["table_name"], occurrence["row_index"]),
            "source_pointer": f"/{occurrence['row_index']}/avatarId", "relation_kind": "avatar_row_candidate",
            "field_role": "avatarId_table_scoped_candidate", "target_family": "AvatarExcelConfigData",
            "target_candidate_key": {"table": "AvatarExcelConfigData", "native_id": occurrence["avatar_id"]},
            "resolution_status": "resolved" if target is not None else "dangling",
            "target_row_key": None if target is None else _row_key("AvatarExcelConfigData", target["row_index"]),
            "target_voice_key": None,
            "candidate_scopes": [] if target is None else [{"relative_path": target["relative_path"], "source_pointer": target["source_pointer"]}],
            "raw_value": occurrence["raw_value"].get("avatarId"),
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
            "relative_path": key[0], "source_pointer": key[1], "record_kind": "textmap_entry",
            "native_id": candidate["textmap_hash"], "raw_value": candidate["raw_text"], "raw_checksum": candidate["raw_checksum"],
        }
    for container in voice_containers:
        for kind in ("lut", "item"):
            key = (container[f"{kind}_relative_path"], container[f"{kind}_source_pointer"])
            source_record_map[key] = {
                "relative_path": key[0], "source_pointer": key[1], "record_kind": f"voice_{kind}_record",
                "native_id": container["voice_slot"], "raw_value": container[f"{kind}_raw_value"], "raw_checksum": container[f"{kind}_raw_checksum"],
            }
    source_records = sorted(source_record_map.values(), key=lambda item: (item["relative_path"], item["source_pointer"]))
    digests = {
        "files": _set_digest(files),
        "source_records": _set_digest([{key: item[key] for key in ("relative_path", "source_pointer", "record_kind", "native_id", "raw_checksum")} for item in source_records]),
        "rows": _set_digest([{key: item[key] for key in ("relative_path", "source_pointer", "table_name", "row_index", "native_locator_field", "native_locator_value", "avatar_id", "raw_checksum")} for item in rows]),
        "raw_slots": _set_digest(raw_slots), "relations": _set_digest(relations),
        "text_resolutions": _set_digest(text_resolutions), "text_candidates": _set_digest(text_candidates),
        "voice_containers": _set_digest([{key: item[key] for key in item if key not in {"lut_raw_value", "item_raw_value"}} for item in voice_containers]),
        "audio_variants": _set_digest(audio_variants),
    }
    status_counts = {
        "relations": dict(sorted(Counter(item["resolution_status"] for item in relations).items())),
        "text_resolutions": {key: Counter(item["resolution_status"] for item in text_resolutions).get(key, 0) for key in ("unique_main", "unique_medium", "both_equal", "both_conflict", "missing")},
        "raw_slots": dict(sorted(Counter(item["observation_status"] for item in raw_slots).items())),
        "condition_shapes": dict(sorted(Counter(item["metadata"]["condition_shape"] for item in raw_slots if item["slot_kind"] == "condition_element").items())),
        "voice_containers": dict(sorted(Counter(item["resolution_status"] for item in voice_containers).items())),
        "audio_paths": dict(sorted(Counter(item["audio_path_status"] for item in audio_variants).items())),
        "character_child_selectors": dict(sorted(Counter(item["selector_status"] for item in audio_variants).items())),
    }
    counts = {
        "selected_files": len(files), "source_records": len(source_records), "rows": len(rows), "raw_slots": len(raw_slots),
        "condition_slots": sum(item["slot_kind"] == "condition_element" for item in raw_slots),
        "condition_nonempty": sum(item["slot_kind"] == "condition_element" and item["metadata"]["condition_shape"] == "nonempty" for item in raw_slots),
        "condition_empty": sum(item["slot_kind"] == "condition_element" and item["metadata"]["condition_shape"] == "empty" for item in raw_slots),
        "opaque_slots": sum(item["slot_kind"] != "condition_element" for item in raw_slots), "relations": len(relations),
        "avatar_relations": sum(item["relation_kind"] == "avatar_row_candidate" for item in relations),
        "text_owner_occurrences": sum(item["relation_kind"] == "textmap_owner_candidate" for item in relations),
        "voice_attachments": sum(item["relation_kind"] == "voice_container_attachment" for item in relations),
        "text_resolutions": len(text_resolutions), "text_candidates": len(text_candidates), "voice_containers": len(voice_containers),
        "audio_variants": len(audio_variants), "distinct_audio_paths": len({item["audio_path"] for item in audio_variants}),
        "distinct_character_child_selectors": len({item["character_child_selector"] for item in audio_variants}),
        "unattached_lut_entries": len(voice_containers) - len({target["item_path"] + ":" + target["item_pointer"].lstrip("/") for target in voice_targets}),
        "unattached_children": sum(item["audio_variant_count"] for item in voice_containers if item["voice_key"] not in {target["item_path"] + ":" + target["item_pointer"].lstrip("/") for target in voice_targets}),
    }
    if counts != EXPECTED_COUNTS:
        _fail(f"Phase 4C5B source counts changed: {counts}")
    if status_counts != EXPECTED_STATUS_COUNTS:
        _fail(f"Phase 4C5B source status counts changed: {status_counts}")
    return {
        "files": files, "tables": tables, "all_tables": all_tables, "voice_targets": voice_targets,
        "rows": rows, "raw_slots": raw_slots, "relations": relations, "text_resolutions": text_resolutions,
        "text_candidates": text_candidates, "voice_containers": voice_containers, "audio_variants": audio_variants,
        "source_records": source_records, "counts": counts, "status_counts": status_counts,
        "table_row_counts": {table: len(value["rows"]) for table, value in tables.items()},
        "text_owner_field_counts": field_counts, "occurrence_set_digests": digests,
    }
def expected_phase4c5b_identity(manifest: CharacterPhase4C5BManifest) -> dict[str, str]:
    if manifest.snapshot_id != EXPECTED_SNAPSHOT_ID:
        _fail("Phase 4C5B snapshot is not the fixed parser-independent C2 snapshot")
    parser_build_id = digest_id("pb-character-phase4c5b", canonical_json({"versions": manifest.versions, "manifest_id": manifest.manifest_id}))
    config_checksum = sha256_text(canonical_json({"manifest_id": manifest.manifest_id, "manifest_raw_sha256": manifest.raw_sha256, "occurrence_set_digests": manifest.expected["occurrence_set_digests"], "coverage": EXPECTED_COVERAGE}))
    ingest_run_id = digest_id("ir-character-phase4c5b", canonical_json({"snapshot_id": manifest.snapshot_id, "parser_build_id": parser_build_id, "config_checksum": config_checksum}))
    return {"snapshot_id": manifest.snapshot_id, "parser_build_id": parser_build_id, "config_checksum": config_checksum, "ingest_run_id": ingest_run_id}


def _coord_digest(items: list[dict[str, Any]]) -> str:
    return _set_digest(items)


def _manifest_closure(derived: dict[str, Any]) -> dict[str, Any]:
    rows = [[item["relative_path"], item["source_pointer"], item["raw_checksum"]] for item in derived["rows"]]
    row_by_key = {_row_key(item["table_name"], item["row_index"]): item for item in derived["rows"]}
    owner_relations = [item for item in derived["relations"] if item["relation_kind"] == "textmap_owner_candidate"]
    owners = [[row_by_key[item["row_key"]]["relative_path"], item["source_pointer"]] for item in owner_relations]
    owner_index = {(item["row_key"], item["source_pointer"]): index for index, item in enumerate(owner_relations)}
    candidates = [[owner_index[(item["row_key"], item["relationship_source_pointer"])], item["source_relative_path"], item["source_pointer"], item["locale"], item["shard_kind"]] for item in derived["text_candidates"]]
    attachments = [["ExcelBinOutput/FettersExcelConfigData.json", item["source_row_pointer"], item["voice_slot"]] for item in derived["voice_targets"]]
    container_by_key = {item["voice_key"]: item for item in derived["voice_containers"]}
    children = [[container_by_key[item["voice_key"]]["item_relative_path"], item["source_pointer"], item["voice_key"], item["array_index"]] for item in derived["audio_variants"]]
    containers = [{"voice_key": item["voice_key"], "voice_slot": item["voice_slot"], "item_relative_path": item["item_relative_path"], "item_source_pointer": item["item_source_pointer"], "lut_source_pointer": item["lut_source_pointer"], "child_count": item["audio_variant_count"], "child_coordinate_digest": _coord_digest([child for child in children if child[2] == item["voice_key"]])} for item in derived["voice_containers"]]
    return {
        "rows": {"count": len(rows), "coordinate_digest": _coord_digest(rows), "coordinates": rows},
        "owners": {"count": len(owners), "coordinate_digest": _coord_digest(owners), "coordinates": owners},
        "candidates": {"count": len(candidates), "coordinate_digest": _coord_digest(candidates), "coordinates": candidates},
        "voice_attachments": {"count": len(attachments), "coordinate_digest": _coord_digest(attachments), "coordinates": attachments},
        "voice_containers": {"count": len(containers), "coordinate_digest": _coord_digest(containers), "items": containers},
        "voice_children": {"count": len(children), "coordinate_digest": _coord_digest(children), "coordinates": children},
    }

@dataclass(frozen=True)
class Phase4C5BReleaseExpected:
    """All source-derived values required by the independent SQL verifier."""
    manifest: CharacterPhase4C5BManifest
    source_root: Path
    snapshot_id: str
    parser_build_id: str
    ingest_run_id: str
    config_checksum: str
    derived: dict[str, Any]
    sql_rows: dict[str, tuple[tuple[Any, ...], ...]]


def build_phase4c5b_release_expected(
    source_root: str | Path,
    manifest_path: str | Path,
) -> Phase4C5BReleaseExpected:
    try:
        manifest = load_character_phase4c5b_manifest(manifest_path)
        derived = derive_character_phase4c5b_source_contract(source_root)
        manifest_files = [{"relative_path": item.relative_path, "format": item.format, "size_bytes": item.size_bytes, "sha256": item.sha256, "blob_oid": item.blob_oid, "role": item.role} for item in manifest.files]
        if derived["files"] != manifest_files:
            _fail("sealed Phase 4C5B file inventory does not match source bytes")
        if derived["table_row_counts"] != manifest.expected["table_row_counts"]:
            _fail("Phase 4C5B primary row counts do not match manifest")
        for table, value in derived["tables"].items():
            expected_rows = [[value["relative_path"], item["pointer"], item["native_locator_value"], item["avatar_id"], item["raw_sha256"]] for item in value["rows"]]
            if manifest.tables[table]["rows"] != expected_rows or manifest.tables[table]["coordinate_digest"] != value["coordinate_digest"]:
                _fail(f"Phase 4C5B {table} row coordinates do not match manifest")
        if derived["text_owner_field_counts"] != manifest.expected["owner_field_counts"]:
            _fail("Phase 4C5B owner field counts do not match manifest")
        if derived["counts"] != manifest.expected["counts"] or derived["status_counts"] != manifest.expected["status_counts"] or derived["occurrence_set_digests"] != manifest.expected["occurrence_set_digests"]:
            _fail("Phase 4C5B source derivation does not match manifest seal")
        if _manifest_closure(derived) != manifest.closure:
            _fail("Phase 4C5B source closure coordinates do not match manifest")
        identity = expected_phase4c5b_identity(manifest)
        preflight = _ReleasePreflightResult(manifest, Path(source_root).resolve(), identity["snapshot_id"], identity["parser_build_id"], identity["ingest_run_id"], identity["config_checksum"], derived)
        return Phase4C5BReleaseExpected(
            manifest=manifest, source_root=preflight.source_root,
            snapshot_id=preflight.snapshot_id, parser_build_id=preflight.parser_build_id,
            ingest_run_id=preflight.ingest_run_id, config_checksum=preflight.config_checksum,
            derived=derived, sql_rows=_expected_sql_rows(preflight),
        )
    except Phase4C5BReleaseContractError:
        raise
    except Exception as exc:
        raise Phase4C5BReleaseContractError(str(exc)) from exc


@dataclass(frozen=True)
class _ReleasePreflightResult:
    manifest: CharacterPhase4C5BManifest
    source_root: Path
    snapshot_id: str
    parser_build_id: str
    ingest_run_id: str
    config_checksum: str
    derived: dict[str, Any]


def _occurrence_ids(preflight: _ReleasePreflightResult) -> dict[str, dict[Any, str]]:
    digest = preflight.manifest.raw_sha256
    run, snapshot = preflight.ingest_run_id, preflight.snapshot_id
    rows = {_row_key(item["table_name"], item["row_index"]): digest_id("character-row", snapshot, digest, run, item["relative_path"], item["source_pointer"]) for item in preflight.derived["rows"]}
    voices = {item["voice_key"]: digest_id("character-voice-container", snapshot, digest, run, item["item_relative_path"], item["item_source_pointer"]) for item in preflight.derived["voice_containers"]}
    relations = {(item["row_key"], item["source_pointer"], item["relation_kind"]): digest_id("character-relation", snapshot, digest, run, item["row_key"], item["source_pointer"], item["relation_kind"]) for item in preflight.derived["relations"]}
    resolutions = {(item["owner_key"], item["locale"]): digest_id("character-text-resolution", snapshot, digest, run, item["owner_key"], item["locale"]) for item in preflight.derived["text_resolutions"]}
    return {"rows": rows, "voices": voices, "relations": relations, "resolutions": resolutions}


def _expected_sql_rows(preflight: _ReleasePreflightResult) -> dict[str, tuple[tuple[Any, ...], ...]]:
    """Build canonical tuples through source coordinates, never through DB state."""
    d, run, snapshot = preflight.derived, preflight.ingest_run_id, preflight.snapshot_id
    ids = _occurrence_ids(preflight)
    file_ids = {item["relative_path"]: digest_id("sf", snapshot, item["relative_path"]) for item in d["files"]}
    record_ids = {(item["relative_path"], item["source_pointer"]): digest_id("sr", file_ids[item["relative_path"]], item["source_pointer"]) for item in d["source_records"]}
    row_records = {_row_key(item["table_name"], item["row_index"]): record_ids[(item["relative_path"], item["source_pointer"])] for item in d["rows"]}
    rows: list[tuple[Any, ...]] = []
    for item in d["rows"]:
        key = _row_key(item["table_name"], item["row_index"])
        rows.append((ids["rows"][key], run, snapshot, file_ids[item["relative_path"]], row_records[key], item["source_pointer"], item["table_name"], item["row_index"], item["native_locator_field"], item["native_locator_value"], item["avatar_id"], item["raw_checksum"], canonical_json({"logical_identity_created": False, "raw_evidence_identity": True})))
    voices: list[tuple[Any, ...]] = []
    for item in d["voice_containers"]:
        voices.append((ids["voices"][item["voice_key"]], run, snapshot, item["voice_slot"], file_ids[item["lut_relative_path"]], record_ids[(item["lut_relative_path"], item["lut_source_pointer"])], item["lut_source_pointer"], file_ids[item["item_relative_path"]], record_ids[(item["item_relative_path"], item["item_source_pointer"])], item["item_source_pointer"], item["resolution_status"], canonical_json(item["candidate_scopes"]), canonical_json({"audio_variant_count": item["audio_variant_count"], "voice_slot_identity_effect": "none"})))
    slots: list[tuple[Any, ...]] = []
    for item in d["raw_slots"]:
        slot_id = digest_id("character-raw-slot", snapshot, preflight.manifest.raw_sha256, run, item["row_key"], item["source_pointer"])
        slots.append((slot_id, run, snapshot, ids["rows"][item["row_key"]], row_records[item["row_key"]], item["source_pointer"], item["field_name"], item["array_index"], item["slot_kind"], item["observation_status"], canonical_json(item["raw_value"]), canonical_json(item["metadata"])))
    relations: list[tuple[Any, ...]] = []
    for item in d["relations"]:
        key = (item["row_key"], item["source_pointer"], item["relation_kind"])
        relations.append((ids["relations"][key], run, snapshot, ids["rows"][item["row_key"]], row_records[item["row_key"]], item["source_pointer"], item["relation_kind"], item["field_role"], item["target_family"], canonical_json(item["target_candidate_key"]), item["resolution_status"], None if item["target_row_key"] is None else ids["rows"][item["target_row_key"]], None if item["target_voice_key"] is None else ids["voices"][item["target_voice_key"]], canonical_json(item["candidate_scopes"]), canonical_json({**item["metadata"], "raw_value": item["raw_value"]})))
    resolutions: list[tuple[Any, ...]] = []
    for item in d["text_resolutions"]:
        relation_key = (item["row_key"], item["relationship_source_pointer"], "textmap_owner_candidate")
        resolutions.append((ids["resolutions"][(item["owner_key"], item["locale"])], run, snapshot, ids["relations"][relation_key], ids["rows"][item["row_key"]], row_records[item["row_key"]], item["relationship_source_pointer"], item["locale"], item["textmap_hash"], item["resolution_status"], item["raw_text"], item["normalized_text"], item["raw_checksum"], item["normalized_checksum"], canonical_json(item["candidate_scopes"]), canonical_json({"missing_is_legal": True, "hash_identity_effect": "none"})))
    candidates: list[tuple[Any, ...]] = []
    for item in d["text_candidates"]:
        resolution_id = ids["resolutions"][(item["owner_key"], item["locale"])]
        candidate_id = digest_id("character-text-candidate", snapshot, preflight.manifest.raw_sha256, run, item["owner_key"], item["locale"], item["shard_kind"], item["source_pointer"])
        candidates.append((candidate_id, resolution_id, run, snapshot, file_ids[item["source_relative_path"]], record_ids[(item["source_relative_path"], item["source_pointer"])], item["source_pointer"], item["shard_kind"], item["locale"], item["resolution_status"], item["raw_text"], item["normalized_text"], item["raw_checksum"], item["normalized_checksum"], canonical_json({"owner_key": item["owner_key"], "candidate_identity_effect": "none"})))
    voice_item_records = {item["voice_key"]: record_ids[(item["item_relative_path"], item["item_source_pointer"])] for item in d["voice_containers"]}
    audio: list[tuple[Any, ...]] = []
    for item in d["audio_variants"]:
        audio_id = digest_id("character-audio-variant", snapshot, preflight.manifest.raw_sha256, run, item["voice_key"], item["source_pointer"])
        audio.append((audio_id, run, snapshot, ids["voices"][item["voice_key"]], voice_item_records[item["voice_key"]], item["source_pointer"], item["array_index"], item["audio_path"], item["audio_path_status"], item["character_child_selector"], item["selector_status"], canonical_json(item["metadata"])))
    source_records = [(digest_id("sr", file_ids[item["relative_path"]], item["source_pointer"]), file_ids[item["relative_path"]], item["source_pointer"], item["record_kind"], item["native_id"], item["raw_checksum"], canonical_json(item["raw_value"])) for item in d["source_records"]]
    return {"source_records": tuple(sorted(source_records)), "rows": tuple(sorted(rows)), "voices": tuple(sorted(voices)), "raw_slots": tuple(sorted(slots)), "relations": tuple(sorted(relations)), "text_resolutions": tuple(sorted(resolutions)), "text_candidates": tuple(sorted(candidates)), "audio_variants": tuple(sorted(audio))}


__all__ = [
    "PARSER_FINGERPRINT", "RUN_FAMILY", "Phase4C5BReleaseContractError",
    "Phase4C5BReleaseExpected", "build_phase4c5b_release_expected",
    "expected_phase4c5b_identity", "verify_phase4c5b_source_pin",
]
