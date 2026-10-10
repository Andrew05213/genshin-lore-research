from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import ManifestError
from .util import sha256_bytes


EXPECTED_COMMIT = "26df1dfbdf05a82bbb1d97506859f3e1c40718d8"
EXPECTED_TREE = "d891ef1d95fdd6c27a1da8a32ea7a61b483647a6"
HEX_40 = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
LOCALES = ("CHS", "EN", "JP")
RESOLUTION_STATUSES = (
    "unique_main",
    "unique_medium",
    "both_equal",
    "both_conflict",
    "missing",
)

EXPECTED_AGGREGATES = {
    "ExcelBinOutput/WeaponExcelConfigData.json",
    "ExcelBinOutput/WeaponCodexExcelConfigData.json",
    "ExcelBinOutput/DocumentExcelConfigData.json",
    "ExcelBinOutput/LocalizationExcelConfigData.json",
    "ExcelBinOutput/ReliquarySetExcelConfigData.json",
    "ExcelBinOutput/ReliquaryCodexExcelConfigData.json",
    "ExcelBinOutput/ReliquaryExcelConfigData.json",
    "ExcelBinOutput/MaterialExcelConfigData.json",
    "ExcelBinOutput/MaterialCodexExcelConfigData.json",
}
EXPECTED_TEXTMAPS = {
    "TextMap/TextMapCHS.json",
    "TextMap/TextMap_MediumCHS.json",
    "TextMap/TextMapEN.json",
    "TextMap/TextMap_MediumEN.json",
    "TextMap/TextMapJP.json",
    "TextMap/TextMap_MediumJP.json",
}
EXPECTED_READABLES = {
    "Readable/CHS/Book1257.txt",
    "Readable/EN/Book1257_EN.txt",
    "Readable/JP/Book1257_JP.txt",
    "Readable/CHS/Weapon15516.txt",
    "Readable/EN/Weapon15516_EN.txt",
    "Readable/JP/Weapon15516_JP.txt",
    *{
        path
        for piece in range(1, 6)
        for path in (
            f"Readable/CHS/Relic15048_{piece}.txt",
            f"Readable/EN/Relic15048_{piece}_EN.txt",
            f"Readable/JP/Relic15048_{piece}_JP.txt",
        )
    },
}
EXPECTED_FILES = EXPECTED_AGGREGATES | EXPECTED_TEXTMAPS | EXPECTED_READABLES
EXPECTED_COUNTS = {
    "selected_files": 36,
    "containers": 56,
    "segments": 420,
    "relationships": 128,
    "text_resolutions": 144,
    "text_candidates": 129,
}
EXPECTED_FAMILY_COUNTS = {
    "book1257": {
        "containers": 3,
        "segments": 0,
        "relationships": 0,
        "text_resolutions": 0,
        "text_candidates": 0,
    },
    "weapon15516": {
        "containers": 7,
        "segments": 93,
        "relationships": 10,
        "text_resolutions": 9,
        "text_candidates": 9,
    },
    "reliquary15048": {
        "containers": 43,
        "segments": 327,
        "relationships": 107,
        "text_resolutions": 105,
        "text_candidates": 105,
    },
    "materials": {
        "containers": 3,
        "segments": 0,
        "relationships": 11,
        "text_resolutions": 30,
        "text_candidates": 15,
    },
}
EXPECTED_STATUS_COUNTS = {
    "unique_main": 3,
    "unique_medium": 126,
    "both_equal": 0,
    "both_conflict": 0,
    "missing": 15,
}


@dataclass(frozen=True)
class StaticPhase4B2FileSpec:
    relative_path: str
    format: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class StaticPhase4B2Manifest:
    path: Path
    raw_sha256: str
    schema_version: int
    manifest_id: str
    source: dict[str, str]
    versions: dict[str, str]
    files: tuple[StaticPhase4B2FileSpec, ...]
    families: dict[str, Any]
    textmap: dict[str, Any]
    expected: dict[str, Any]


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ManifestError(f"{label} must be an array")
    return value


def _exact(value: dict[str, Any], keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise ManifestError(
            f"{label} keys invalid: missing={sorted(keys - set(value))}, "
            f"extra={sorted(set(value) - keys)}"
        )


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{label} must be a non-empty string")
    return value


def _relative(value: Any, label: str) -> str:
    text = _nonempty(value, label)
    if "\\" in text or re.match(r"^[A-Za-z]:", text):
        raise ManifestError(f"{label} must be a normalized relative path")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ManifestError(f"{label} must be a normalized relative path")
    return text


def _record(value: Any, label: str) -> dict[str, Any]:
    record = _object(value, label)
    _exact(
        record,
        {"relative_path", "pointer", "table", "native_field", "native_id"},
        label,
    )
    record["relative_path"] = _relative(record["relative_path"], f"{label}.relative_path")
    pointer = _nonempty(record["pointer"], f"{label}.pointer")
    if not pointer.startswith("/"):
        raise ManifestError(f"{label}.pointer must be an absolute JSON pointer")
    for key in ("table", "native_field", "native_id"):
        _nonempty(record[key], f"{label}.{key}")
    return record


def _target_excel(value: Any, label: str) -> dict[str, Any]:
    target = _object(value, label)
    _exact(target, {"table", "native_id", "record_pointer"}, label)
    for key in ("table", "native_id"):
        _nonempty(target[key], f"{label}.{key}")
    pointer = _nonempty(target["record_pointer"], f"{label}.record_pointer")
    if not pointer.startswith("/"):
        raise ManifestError(f"{label}.record_pointer must be an absolute JSON pointer")
    return target


def _target_readable(value: Any, label: str) -> dict[str, Any]:
    target = _object(value, label)
    _exact(target, {"family", "locale", "relative_path", "record_pointer"}, label)
    if target["family"] != "Readable":
        raise ManifestError(f"{label}.family must be 'Readable'")
    if target["locale"] not in LOCALES:
        raise ManifestError(f"{label}.locale must be one of {LOCALES}")
    _relative(target["relative_path"], f"{label}.relative_path")
    pointer = _nonempty(target["record_pointer"], f"{label}.record_pointer")
    if not pointer.startswith("/"):
        raise ManifestError(f"{label}.record_pointer must be an absolute JSON pointer")
    return target


def _numeric_relationship(value: Any, label: str, *, kind: str = "field") -> dict[str, Any]:
    rel = _object(value, label)
    if kind == "field":
        _exact(rel, {"source_field", "raw_value", "target"}, label)
        _nonempty(rel["source_field"], f"{label}.source_field")
    elif kind == "suffix":
        _exact(rel, {"source_pointer_suffix", "raw_value", "target"}, label)
        suffix = _nonempty(rel["source_pointer_suffix"], f"{label}.source_pointer_suffix")
        if not suffix.startswith("/"):
            raise ManifestError(f"{label}.source_pointer_suffix must start with '/'")
    elif kind == "index":
        _exact(rel, {"index", "raw_value", "target"}, label)
        if type(rel["index"]) is not int or rel["index"] < 0:
            raise ManifestError(f"{label}.index must be a non-negative integer")
    elif kind == "slot":
        _exact(rel, {"raw_value", "target"}, label)
    else:
        raise ManifestError(f"unknown numeric relationship kind {kind}")
    if type(rel["raw_value"]) is not int:
        raise ManifestError(f"{label}.raw_value must be an integer")
    _target_excel(rel["target"], f"{label}.target")
    return rel


def _readable_fields(
    value: Any,
    label: str,
    allowed_keys: set[str] | None = None,
) -> dict[str, Any]:
    fields = _object(value, label)
    if allowed_keys is not None:
        _exact(fields, allowed_keys, label)
    for key, item in fields.items():
        _nonempty(key, f"{label} field key")
        field_obj = _object(item, f"{label}.{key}")
        _exact(field_obj, {"raw_value", "target"}, f"{label}.{key}")
        _nonempty(field_obj["raw_value"], f"{label}.{key}.raw_value")
        _target_readable(field_obj["target"], f"{label}.{key}.target")
    return fields


def _text_fields(value: Any, label: str, allowed_keys: set[str] | None = None) -> dict[str, Any]:
    fields = _object(value, label)
    if allowed_keys is not None:
        _exact(fields, allowed_keys, label)
    for key, val in fields.items():
        _nonempty(key, f"{label} field key")
        if type(val) is not int or val < 0:
            raise ManifestError(f"{label}.{key} must be a non-negative integer")
    return fields


def _readable_spec(value: Any, label: str, *, allow_ordinals: bool = False) -> dict[str, Any]:
    spec = _object(value, label)
    expected_keys = {"relative_path", "record_pointer", "locale", "classification"}
    if allow_ordinals:
        expected_keys.add("segment_ordinals")
    _exact(spec, expected_keys, label)
    _relative(spec["relative_path"], f"{label}.relative_path")
    pointer = _nonempty(spec["record_pointer"], f"{label}.record_pointer")
    if not pointer.startswith("/"):
        raise ManifestError(f"{label}.record_pointer must be an absolute JSON pointer")
    if spec["locale"] not in LOCALES:
        raise ManifestError(f"{label}.locale must be one of {LOCALES}")
    if spec["classification"] not in {"placeholder_exact_test", "empty", "narrative"}:
        raise ManifestError(f"{label}.classification is invalid")
    if allow_ordinals:
        ordinals = _list(spec["segment_ordinals"], f"{label}.segment_ordinals")
        if any(type(x) is not int or x < 0 for x in ordinals):
            raise ManifestError(f"{label}.segment_ordinals must contain non-negative integers")
    return spec


def _validate_book(families: dict[str, Any], files: dict[str, StaticPhase4B2FileSpec]) -> None:
    book = _object(families["book1257"], "families.book1257")
    _exact(book, {"readables"}, "families.book1257")
    readables = _list(book["readables"], "families.book1257.readables")
    if len(readables) != 3:
        raise ManifestError("Book1257 must seal exactly three locale files")
    expected = {
        "CHS": ("Readable/CHS/Book1257.txt", "placeholder_exact_test", 5),
        "EN": ("Readable/EN/Book1257_EN.txt", "empty", 0),
        "JP": ("Readable/JP/Book1257_JP.txt", "empty", 0),
    }
    seen: set[str] = set()
    for index, raw in enumerate(readables):
        item = _readable_spec(raw, f"Book1257 readables[{index}]", allow_ordinals=True)
        locale = item["locale"]
        if locale in seen or locale not in expected:
            raise ManifestError("Book1257 locales must be exactly CHS, EN, and JP")
        seen.add(locale)
        path, classification, size = expected[locale]
        if (
            item["relative_path"] != path
            or item["record_pointer"] != "/text"
            or item["classification"] != classification
            or item["segment_ordinals"] != []
            or files[path].size_bytes != size
        ):
            raise ManifestError(f"Book1257 sealed semantics mismatch for {locale}")


def _validate_family_shapes(families: dict[str, Any]) -> None:
    _exact(
        families,
        {"book1257", "weapon15516", "reliquary15048", "materials"},
        "families",
    )
    weapon = _object(families["weapon15516"], "families.weapon15516")
    _exact(
        weapon,
        {"weapon", "codex", "document", "localization", "readables", "segment_ordinals"},
        "families.weapon15516",
    )
    w_weapon = _object(weapon["weapon"], "families.weapon15516.weapon")
    _exact(w_weapon, {"record", "story_relationship", "text_fields"}, "families.weapon15516.weapon")
    _record(w_weapon["record"], "families.weapon15516.weapon.record")
    _numeric_relationship(w_weapon["story_relationship"], "families.weapon15516.weapon.story_relationship", kind="field")
    _text_fields(w_weapon["text_fields"], "families.weapon15516.weapon.text_fields", {"nameTextMapHash", "descTextMapHash"})

    w_codex = _object(weapon["codex"], "families.weapon15516.codex")
    _exact(w_codex, {"record", "weapon_relationship"}, "families.weapon15516.codex")
    _record(w_codex["record"], "families.weapon15516.codex.record")
    _numeric_relationship(w_codex["weapon_relationship"], "families.weapon15516.codex.weapon_relationship", kind="field")

    w_doc = _object(weapon["document"], "families.weapon15516.document")
    _exact(w_doc, {"record", "localization_relationship", "text_fields"}, "families.weapon15516.document")
    _record(w_doc["record"], "families.weapon15516.document.record")
    _numeric_relationship(w_doc["localization_relationship"], "families.weapon15516.document.localization_relationship", kind="suffix")
    _text_fields(w_doc["text_fields"], "families.weapon15516.document.text_fields", {"titleTextMapHash"})

    EXACT_READABLE_FIELDS = {"DEFNEHAFMMA", "JDNBKKPEFAI", "enPath", "jpPath"}
    w_loc = _object(weapon["localization"], "families.weapon15516.localization")
    _exact(w_loc, {"record", "readable_fields"}, "families.weapon15516.localization")
    _record(w_loc["record"], "families.weapon15516.localization.record")
    _readable_fields(
        w_loc["readable_fields"],
        "families.weapon15516.localization.readable_fields",
        EXACT_READABLE_FIELDS,
    )

    w_readables = _list(weapon["readables"], "families.weapon15516.readables")
    if len(w_readables) != 3:
        raise ManifestError("Weapon15516 must seal exactly three Readable records")
    for idx, r in enumerate(w_readables):
        _readable_spec(r, f"families.weapon15516.readables[{idx}]", allow_ordinals=False)

    w_ordinals = _list(weapon["segment_ordinals"], "families.weapon15516.segment_ordinals")
    if len(w_ordinals) != 31 or any(type(x) is not int or x < 0 for x in w_ordinals):
        raise ManifestError("Weapon15516 must seal 31 ordinals per locale")

    reliquary = _object(families["reliquary15048"], "families.reliquary15048")
    _exact(reliquary, {"set", "codex", "pieces", "stories"}, "families.reliquary15048")
    r_set = _object(reliquary["set"], "families.reliquary15048.set")
    _exact(r_set, {"record", "contains_relationships"}, "families.reliquary15048.set")
    _record(r_set["record"], "families.reliquary15048.set.record")
    r_contains = _list(r_set["contains_relationships"], "families.reliquary15048.set.contains_relationships")
    if len(r_contains) != 5:
        raise ManifestError("Reliquary15048 set must contain exactly 5 relationships")
    for idx, c in enumerate(r_contains):
        _numeric_relationship(c, f"families.reliquary15048.set.contains_relationships[{idx}]", kind="index")

    codex = _list(reliquary["codex"], "families.reliquary15048.codex")
    pieces = _list(reliquary["pieces"], "families.reliquary15048.pieces")
    stories = _list(reliquary["stories"], "families.reliquary15048.stories")
    if len(codex) != 2 or len(pieces) != 15 or len(stories) != 5:
        raise ManifestError("Reliquary15048 must seal 2 Codex, 15 piece, and 5 story records")

    for idx, c_item in enumerate(codex):
        c_obj = _object(c_item, f"families.reliquary15048.codex[{idx}]")
        _exact(c_obj, {"record", "level", "suit_relationship", "slots"}, f"families.reliquary15048.codex[{idx}]")
        _record(c_obj["record"], f"families.reliquary15048.codex[{idx}].record")
        if c_obj["level"] not in (4, 5):
            raise ManifestError(f"families.reliquary15048.codex[{idx}].level must be 4 or 5")
        _numeric_relationship(c_obj["suit_relationship"], f"families.reliquary15048.codex[{idx}].suit_relationship", kind="field")
        slots = _object(c_obj["slots"], f"families.reliquary15048.codex[{idx}].slots")
        _exact(slots, {"capId", "cupId", "flowerId", "leatherId", "sandId"}, f"families.reliquary15048.codex[{idx}].slots")
        for slot_name, slot_val in slots.items():
            _numeric_relationship(slot_val, f"families.reliquary15048.codex[{idx}].slots.{slot_name}", kind="slot")

    piece_ids: set[str] = set()
    piece_pointers: set[str] = set()
    for index, raw in enumerate(pieces):
        item = _object(raw, f"families.reliquary15048.pieces[{index}]")
        _exact(
            item,
            {"record", "set_relationship", "story_relationship", "text_fields"},
            f"families.reliquary15048.pieces[{index}]",
        )
        record = _record(item["record"], f"families.reliquary15048.pieces[{index}].record")
        if record["table"] != "ReliquaryExcelConfigData":
            raise ManifestError("Reliquary piece table namespace mismatch")
        _numeric_relationship(item["set_relationship"], f"families.reliquary15048.pieces[{index}].set_relationship", kind="field")
        _numeric_relationship(item["story_relationship"], f"families.reliquary15048.pieces[{index}].story_relationship", kind="field")
        _text_fields(item["text_fields"], f"families.reliquary15048.pieces[{index}].text_fields", {"nameTextMapHash", "descTextMapHash"})
        piece_ids.add(record["native_id"])
        piece_pointers.add(record["pointer"])
    if len(piece_ids) != 15 or len(piece_pointers) != 15:
        raise ManifestError("Reliquary piece raw records must remain distinct")

    for index, raw in enumerate(stories):
        story = _object(raw, f"families.reliquary15048.stories[{index}]")
        _exact(story, {"document", "localization", "readables", "segment_ordinals"}, f"families.reliquary15048.stories[{index}]")
        s_doc = _object(story["document"], f"families.reliquary15048.stories[{index}].document")
        _exact(s_doc, {"record", "localization_relationship", "text_fields"}, f"families.reliquary15048.stories[{index}].document")
        _record(s_doc["record"], f"families.reliquary15048.stories[{index}].document.record")
        _numeric_relationship(s_doc["localization_relationship"], f"families.reliquary15048.stories[{index}].document.localization_relationship", kind="suffix")
        _text_fields(s_doc["text_fields"], f"families.reliquary15048.stories[{index}].document.text_fields", {"titleTextMapHash"})

        s_loc = _object(story["localization"], f"families.reliquary15048.stories[{index}].localization")
        _exact(s_loc, {"record", "readable_fields"}, f"families.reliquary15048.stories[{index}].localization")
        _record(s_loc["record"], f"families.reliquary15048.stories[{index}].localization.record")
        _readable_fields(
            s_loc["readable_fields"],
            f"families.reliquary15048.stories[{index}].localization.readable_fields",
            EXACT_READABLE_FIELDS,
        )

        s_readables = _list(story["readables"], f"families.reliquary15048.stories[{index}].readables")
        if len(s_readables) != 3:
            raise ManifestError(f"families.reliquary15048.stories[{index}] must seal 3 readables")
        for r_idx, r in enumerate(s_readables):
            _readable_spec(r, f"families.reliquary15048.stories[{index}].readables[{r_idx}]", allow_ordinals=False)

        s_ordinals = _list(story["segment_ordinals"], f"families.reliquary15048.stories[{index}].segment_ordinals")
        if not s_ordinals or any(type(x) is not int or x < 0 for x in s_ordinals):
            raise ManifestError(f"families.reliquary15048.stories[{index}].segment_ordinals invalid")

    materials = _object(families["materials"], "families.materials")
    _exact(materials, {"base", "codex"}, "families.materials")
    bases = _list(materials["base"], "families.materials.base")
    if len(bases) != 2:
        raise ManifestError("materials.base must contain exactly two rows")
    for index, item in enumerate(bases):
        b_obj = _object(item, f"families.materials.base[{index}]")
        _exact(b_obj, {"record", "text_fields"}, f"families.materials.base[{index}]")
        _record(b_obj["record"], f"families.materials.base[{index}].record")
        _text_fields(b_obj["text_fields"], f"families.materials.base[{index}].text_fields", {"nameTextMapHash", "descTextMapHash"})

    codex_item = _object(materials["codex"], "families.materials.codex")
    _exact(codex_item, {"record", "material_relationship", "text_fields"}, "families.materials.codex")
    _record(codex_item["record"], "families.materials.codex.record")
    _numeric_relationship(codex_item["material_relationship"], "families.materials.codex.material_relationship", kind="field")
    _text_fields(
        codex_item["text_fields"],
        "families.materials.codex.text_fields",
        {
            "nameTextMapHash",
            "descTextMapHash",
            "questReplaceName1TextMapHash",
            "questReplaceDesc1TextMapHash",
            "questReplaceName2TextMapHash",
            "questReplaceDesc2TextMapHash",
        },
    )


def _validate_textmap(textmap: dict[str, Any], file_paths: set[str]) -> None:
    _exact(textmap, {"locales", "hash_statuses"}, "textmap")
    locales = _object(textmap["locales"], "textmap.locales")
    _exact(locales, set(LOCALES), "textmap.locales")
    locator_paths: set[str] = set()
    for locale in LOCALES:
        pair = _object(locales[locale], f"textmap.locales.{locale}")
        _exact(pair, {"main", "medium"}, f"textmap.locales.{locale}")
        locator_paths.update(_relative(path, "TextMap locator") for path in pair.values())
    if locator_paths != EXPECTED_TEXTMAPS or not locator_paths <= file_paths:
        raise ManifestError("TextMap locator set mismatch")

    statuses = _object(textmap["hash_statuses"], "textmap.hash_statuses")
    _exact(statuses, set(RESOLUTION_STATUSES), "textmap.hash_statuses")
    expected_lengths = {
        "unique_main": 1,
        "unique_medium": 42,
        "both_equal": 0,
        "both_conflict": 0,
        "missing": 5,
    }
    all_hashes: list[str] = []
    for status, expected_length in expected_lengths.items():
        values = _list(statuses[status], f"textmap.hash_statuses.{status}")
        if len(values) != expected_length or any(
            not isinstance(item, str) or not item.isdigit() for item in values
        ):
            raise ManifestError(f"TextMap status partition mismatch for {status}")
        all_hashes.extend(values)
    if len(all_hashes) != 48 or len(set(all_hashes)) != 48:
        raise ManifestError("TextMap selected hashes must be 48 distinct locators")


def load_static_phase4b2_manifest(path: str | Path) -> StaticPhase4B2Manifest:
    manifest_path = Path(path)
    try:
        raw = manifest_path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read Phase 4B2 manifest {manifest_path}: {exc}") from exc
    value = _object(value, "Phase 4B2 manifest")
    _exact(
        value,
        {"schema_version", "manifest_id", "source", "versions", "files", "families", "textmap", "expected"},
        "Phase 4B2 manifest",
    )
    if value["schema_version"] != 1:
        raise ManifestError("Phase 4B2 manifest schema_version must be 1")
    manifest_id = _nonempty(value["manifest_id"], "manifest_id")
    if manifest_id != "anime-game-data-7.0-phase4b2-multifamily-canary":
        raise ManifestError("Phase 4B2 manifest_id mismatch")

    source = _object(value["source"], "source")
    _exact(source, {"source_id", "release_label", "commit", "tree"}, "source")
    if source.get("source_id") != "anime-game-data" or source.get("release_label") != "7.0":
        raise ManifestError("Phase 4B2 source must be anime-game-data release 7.0")
    if not isinstance(source.get("commit"), str) or not HEX_40.fullmatch(source["commit"]):
        raise ManifestError("source.commit must be lowercase 40-hex")
    if not isinstance(source.get("tree"), str) or not HEX_40.fullmatch(source["tree"]):
        raise ManifestError("source.tree must be lowercase 40-hex")
    if source["commit"] != EXPECTED_COMMIT or source["tree"] != EXPECTED_TREE:
        raise ManifestError("Phase 4B2 manifest source pin mismatch")

    versions = _object(value["versions"], "versions")
    _exact(versions, {"parser", "projection", "segmentation"}, "versions")
    for key, version in versions.items():
        _nonempty(version, f"versions.{key}")

    raw_files = _list(value["files"], "files")
    if len(raw_files) != 36:
        raise ManifestError("Phase 4B2 files must contain exactly 36 entries")
    files: list[StaticPhase4B2FileSpec] = []
    seen: set[str] = set()
    for index, raw_file in enumerate(raw_files):
        item = _object(raw_file, f"files[{index}]")
        _exact(item, {"relative_path", "format", "size_bytes", "sha256"}, f"files[{index}]")
        relative_path = _relative(item["relative_path"], f"files[{index}].relative_path")
        if relative_path in seen:
            raise ManifestError(f"duplicate selected file: {relative_path}")
        seen.add(relative_path)
        if item["format"] not in {"json", "txt"}:
            raise ManifestError(f"unsupported selected file format: {item['format']!r}")
        if type(item["size_bytes"]) is not int or item["size_bytes"] < 0:
            raise ManifestError(f"files[{index}].size_bytes must be a non-negative integer")
        if not isinstance(item["sha256"], str) or not HEX_64.fullmatch(item["sha256"]):
            raise ManifestError(f"files[{index}].sha256 must be lowercase 64-hex")
        files.append(
            StaticPhase4B2FileSpec(
                relative_path=relative_path,
                format=item["format"],
                size_bytes=item["size_bytes"],
                sha256=item["sha256"],
            )
        )
    if seen != EXPECTED_FILES:
        raise ManifestError("Phase 4B2 selected file set mismatch")

    families = _object(value["families"], "families")
    _validate_family_shapes(families)
    files_by_path = {item.relative_path: item for item in files}
    _validate_book(families, files_by_path)
    textmap = _object(value["textmap"], "textmap")
    _validate_textmap(textmap, seen)

    expected = _object(value["expected"], "expected")
    _exact(expected, {"counts", "families", "resolution_statuses"}, "expected")
    counts = _object(expected["counts"], "expected.counts")
    _exact(counts, {"selected_files", "containers", "segments", "relationships", "text_resolutions", "text_candidates"}, "expected.counts")
    if counts != EXPECTED_COUNTS:
        raise ManifestError("Phase 4B2 exact counts mismatch")
    exp_families = _object(expected["families"], "expected.families")
    _exact(exp_families, {"book1257", "weapon15516", "reliquary15048", "materials"}, "expected.families")
    for fam_key in ("book1257", "weapon15516", "reliquary15048", "materials"):
        fam_counts = _object(exp_families[fam_key], f"expected.families.{fam_key}")
        _exact(fam_counts, {"containers", "segments", "relationships", "text_resolutions", "text_candidates"}, f"expected.families.{fam_key}")
    if exp_families != EXPECTED_FAMILY_COUNTS:
        raise ManifestError("Phase 4B2 family count matrix mismatch")
    res_statuses = _object(expected["resolution_statuses"], "expected.resolution_statuses")
    _exact(res_statuses, set(RESOLUTION_STATUSES), "expected.resolution_statuses")
    if res_statuses != EXPECTED_STATUS_COUNTS:
        raise ManifestError("Phase 4B2 resolution status counts mismatch")

    return StaticPhase4B2Manifest(
        path=manifest_path,
        raw_sha256=sha256_bytes(raw),
        schema_version=1,
        manifest_id=manifest_id,
        source={key: str(source[key]) for key in source},
        versions={key: str(versions[key]) for key in versions},
        files=tuple(files),
        families=copy_json(families),
        textmap=copy_json(textmap),
        expected=copy_json(expected),
    )


def copy_json(value: dict[str, Any]) -> dict[str, Any]:
    """Return a detached JSON-only copy without preserving caller mutation hooks."""
    return json.loads(json.dumps(value, ensure_ascii=False))


__all__ = [
    "LOCALES",
    "RESOLUTION_STATUSES",
    "StaticPhase4B2FileSpec",
    "StaticPhase4B2Manifest",
    "load_static_phase4b2_manifest",
]
