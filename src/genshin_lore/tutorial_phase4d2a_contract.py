"""Pure source contract for the Phase 4D2A Tutorial canary.

This module has no database or builder side effects.  The canary builder and
the read-only release verifier may both use it to independently check the
same pinned source/manifest boundary; it never reads or writes SQLite rows.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from .tutorial_phase4d2a_manifest import (
    EXPECTED_BUILD,
    EXPECTED_COMMIT,
    EXPECTED_OBSERVED_AT,
    EXPECTED_RELEASE,
    EXPECTED_SNAPSHOT_ID,
    EXPECTED_SOURCE_ID,
    EXPECTED_TABLES,
    EXPECTED_TREE,
    LOCALES,
    TABLE_PATHS,
    TEXTMAP_PATHS,
    TEXT_OWNER_FIELDS,
)
from .util import canonical_json, digest_id, normalize_text, sha256_bytes, sha256_text


RUN_FAMILY = "tutorial_phase4d2a_core_canary"
LOOKUP_TABLE = "PushTipsCodexExcelConfigData"
LOOKUP_PATH = TABLE_PATHS[LOOKUP_TABLE]
PRIMARY_TABLES = tuple(table for table, (_, role) in EXPECTED_TABLES.items() if role == "primary")
TEXTMAP_PATH_TO_LOCALE_KIND = {
    path: (locale, kind)
    for locale, paths in TEXTMAP_PATHS.items()
    for kind, path in paths.items()
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class TutorialPhase4D2AContractError(RuntimeError):
    """The pinned source or canary contract is inconsistent."""


def _fail(message: str) -> None:
    raise TutorialPhase4D2AContractError(message)


def _strict_load(raw: bytes, label: str) -> Any:
    def reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                _fail(f"duplicate JSON key in {label}: {key}")
            value[key] = item
        return value

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=reject_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot parse {label}: {exc}")


def strict_json_file(path: Path, label: str) -> Any:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        _fail(f"cannot read {label}: {exc}")
    return _strict_load(raw, label)


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        _fail(f"cannot inspect pinned Tutorial source checkout: {exc}")
    return result.stdout.strip()


def verify_source_pin(root: Path) -> None:
    root = root.resolve()
    if not root.is_dir():
        _fail(f"Tutorial source root is not a directory: {root}")
    if Path(_git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        _fail("Tutorial source root is not the Git toplevel")
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        _fail("Tutorial source checkout is not clean")
    if _git(root, "rev-parse", "HEAD") != EXPECTED_COMMIT:
        _fail("Tutorial source commit mismatch")
    if _git(root, "rev-parse", "HEAD^{tree}") != EXPECTED_TREE:
        _fail("Tutorial source tree mismatch")


def _manifest_value(manifest: Any) -> dict[str, Any]:
    if hasattr(manifest, "raw"):
        value = manifest.raw
    elif isinstance(manifest, Mapping):
        value = manifest
    else:
        _fail("Tutorial canary manifest value is not an object")
    if not isinstance(value, dict):
        _fail("Tutorial canary manifest value is not an object")
    return value


def _raw_checksum(value: Any) -> str:
    return sha256_text(value if isinstance(value, str) else canonical_json(value))


def _set_digest(items: list[dict[str, Any]]) -> str:
    return sha256_text(canonical_json(sorted(canonical_json(item) for item in items)))


def _positive_int(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return str(value)
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return str(int(value))
    return None


def _rows(value: Any, table: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        _fail(f"{table} source must be an array of objects")
    return value


def _source_files(root: Path, manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list):
        _fail("Tutorial manifest files are missing")
    values: dict[str, Any] = {}
    specs: list[dict[str, Any]] = []
    for item in raw_files:
        if not isinstance(item, dict):
            _fail("Tutorial manifest file entry is not an object")
        relative = item.get("relative_path")
        if not isinstance(relative, str):
            _fail("Tutorial manifest file path is invalid")
        path = root / relative
        try:
            raw = path.read_bytes()
        except OSError as exc:
            _fail(f"cannot read Tutorial source file {relative}: {exc}")
        if len(raw) != item.get("size_bytes") or sha256_bytes(raw) != item.get("sha256"):
            _fail(f"Tutorial source file digest/size mismatch: {relative}")
        try:
            blob = _git(root, "rev-parse", f"HEAD:{relative}")
        except TutorialPhase4D2AContractError:
            raise
        if blob != item.get("blob_oid"):
            _fail(f"Tutorial source file blob mismatch: {relative}")
        values[relative] = _strict_load(raw, relative)
        specs.append(dict(item))
    expected_paths = sorted((*TABLE_PATHS.values(), *(path for paths in TEXTMAP_PATHS.values() for path in paths.values())))
    if sorted(values) != expected_paths or len(specs) != len(expected_paths):
        _fail("Tutorial selected source path set mismatch")
    return specs, values


def _selected_specs(manifest: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    boundary = manifest.get("boundary")
    if not isinstance(boundary, dict):
        _fail("Tutorial manifest boundary is missing")
    result: dict[str, list[dict[str, Any]]] = {}
    for table, (_, role) in EXPECTED_TABLES.items():
        key = "selected_primary_rows" if role == "primary" else "selected_lookup_rows"
        rows = boundary.get(key)
        if role == "primary" and isinstance(rows, dict):
            rows = rows.get(table)
        if not isinstance(rows, list) or not rows:
            _fail(f"Tutorial canary has no selected rows for {table}")
        result[table] = []
        seen: set[int] = set()
        for item in rows:
            if not isinstance(item, dict):
                _fail(f"Tutorial selected row is not an object: {table}")
            index = item.get("row_index")
            pointer = item.get("pointer")
            if not isinstance(index, int) or index < 0 or pointer != f"/{index}" or index in seen:
                _fail(f"Tutorial selected row coordinate mismatch: {table}/{index}")
            seen.add(index)
            result[table].append(dict(item))
        result[table].sort(key=lambda item: item["row_index"])
    return result


def _verify_selected_rows(
    values: dict[str, Any],
    selected: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[tuple[str, int], dict[str, Any]]]:
    output: list[dict[str, Any]] = []
    by_key: dict[tuple[str, int], dict[str, Any]] = {}
    for table, specs in selected.items():
        native_field = EXPECTED_TABLES[table][0]
        source_rows = _rows(values[TABLE_PATHS[table]], table)
        for spec in specs:
            index = spec["row_index"]
            if index >= len(source_rows):
                _fail(f"Tutorial selected row is out of range: {table}/{index}")
            row = source_rows[index]
            value = _positive_int(row.get(native_field))
            if value is None or value != str(spec.get("native_locator_value")):
                _fail(f"Tutorial selected native locator mismatch: {table}/{index}")
            raw_checksum = _raw_checksum(row)
            if raw_checksum != spec.get("raw_sha256"):
                _fail(f"Tutorial selected row checksum mismatch: {table}/{index}")
            item = {
                "table": table,
                "relative_path": TABLE_PATHS[table],
                "row_index": index,
                "pointer": f"/{index}",
                "native_locator_field": native_field,
                "native_locator_value": value,
                "raw_value": row,
                "raw_checksum": raw_checksum,
                "family_role": EXPECTED_TABLES[table][1],
            }
            output.append(item)
            by_key[(table, index)] = item
    output.sort(key=lambda item: (item["table"], item["row_index"]))
    return output, by_key


def _candidate_scan(
    values: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    scans = manifest.get("candidate_scans")
    if not isinstance(scans, dict):
        _fail("Tutorial candidate scans are missing")
    result: dict[str, dict[str, Any]] = {}
    for table, (field, _) in EXPECTED_TABLES.items():
        rows = _rows(values[TABLE_PATHS[table]], table)
        entry = scans.get(table)
        if not isinstance(entry, dict):
            _fail(f"Tutorial candidate scan is missing: {table}")
        if entry.get("source_records") != len(rows) or entry.get("native_locator_field") != field:
            _fail(f"Tutorial candidate scan count/field mismatch: {table}")
        observations = []
        for index, row in enumerate(rows):
            value = _positive_int(row.get(field))
            if value is None:
                _fail(f"Tutorial candidate scan has invalid native locator: {table}/{index}")
            observations.append({
                "table": table,
                "row_index": index,
                "pointer": f"/{index}",
                "native_locator_value": value,
                "raw_sha256": _raw_checksum(row),
            })
        digest = _set_digest(observations)
        if digest != entry.get("native_locator_digest"):
            _fail(f"Tutorial candidate scan digest mismatch: {table}")
        result[table] = {
            "relative_path": TABLE_PATHS[table],
            "native_locator_field": field,
            "source_records": len(rows),
            "native_locator_digest": digest,
            "observations": observations,
        }
    return result


def _row_key(table: str, row_index: int) -> str:
    return f"{table}:{row_index}"


def _detail_relations(
    selected: dict[tuple[str, int], dict[str, Any]],
    values: dict[str, Any],
    scans: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    detail_rows = _rows(values[TABLE_PATHS["TutorialDetailExcelConfigData"]], "TutorialDetailExcelConfigData")
    detail_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in scans["TutorialDetailExcelConfigData"]["observations"]:
        detail_candidates[item["native_locator_value"]].append(item)
    result: list[dict[str, Any]] = []
    for (table, index), source in sorted(selected.items()):
        if table != "TutorialExcelConfigData":
            continue
        row = source["raw_value"]
        field = "detailIdList"
        base = {
            "source_table": table,
            "source_row_index": index,
            "source_row_key": _row_key(table, index),
            "source_record_pointer": source["pointer"],
            "field_name": field,
            "target_table": "TutorialDetailExcelConfigData",
        }
        if field not in row or row[field] is None:
            result.append({
                **base,
                "source_pointer": f"/{index}/{field}",
                "array_index": None,
                "raw_value": None if field not in row else row[field],
                "candidate_key": {"table": "TutorialDetailExcelConfigData", "field": "id"},
                "resolution_status": "missing",
                "target_row_key": None,
                "candidate_scopes": [],
            })
            continue
        if not isinstance(row[field], list):
            result.append({
                **base,
                "source_pointer": f"/{index}/{field}",
                "array_index": None,
                "raw_value": row[field],
                "candidate_key": {"table": "TutorialDetailExcelConfigData", "field": "id"},
                "resolution_status": "invalid",
                "target_row_key": None,
                "candidate_scopes": [],
            })
            continue
        for array_index, raw_value in enumerate(row[field]):
            value = _positive_int(raw_value)
            candidates = [] if value is None else detail_candidates.get(value, [])
            if value is None:
                status = "invalid"
            elif not candidates:
                status = "dangling"
            elif len(candidates) > 1:
                status = "ambiguous"
            else:
                status = "resolved_unique"
            target_key = None
            if status == "resolved_unique":
                target = candidates[0]
                target_key = _row_key("TutorialDetailExcelConfigData", target["row_index"])
                if ("TutorialDetailExcelConfigData", target["row_index"]) not in selected:
                    _fail(f"resolved Tutorial detail target is outside canary selection: {target_key}")
            result.append({
                **base,
                "source_pointer": f"/{index}/{field}/{array_index}",
                "array_index": array_index,
                "raw_value": raw_value,
                "candidate_key": {"table": "TutorialDetailExcelConfigData", "field": "id", "value": value} if value is not None else {"table": "TutorialDetailExcelConfigData", "field": "id"},
                "resolution_status": status,
                "target_row_key": target_key,
                "candidate_scopes": [
                    {"relative_path": TABLE_PATHS["TutorialDetailExcelConfigData"], "source_pointer": item["pointer"], "row_index": item["row_index"], "native_locator_value": item["native_locator_value"]}
                    for item in candidates
                ],
            })
    result.sort(key=lambda item: (item["source_table"], item["source_row_index"], item["source_pointer"]))
    return result


def _push_tips_relations(
    selected: dict[tuple[str, int], dict[str, Any]],
    values: dict[str, Any],
    scans: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in scans[LOOKUP_TABLE]["observations"]:
        row = _rows(values[LOOKUP_PATH], LOOKUP_TABLE)[item["row_index"]]
        value = _positive_int(row.get("pushTipId"))
        if value is not None:
            candidates[value].append(item)
    result: list[dict[str, Any]] = []
    for (table, index), source in sorted(selected.items()):
        if table != "TutorialCatalogExcelConfigData":
            continue
        row = source["raw_value"]
        field = "pushTipsId"
        pointer = f"/{index}/{field}"
        if field not in row or row[field] is None:
            status = "missing"
            raw_value = None if field not in row else row[field]
            value = None
            scopes: list[dict[str, Any]] = []
        else:
            raw_value = row[field]
            value = _positive_int(raw_value)
            scopes = [] if value is None else [
                {"relative_path": LOOKUP_PATH, "source_pointer": item["pointer"], "row_index": item["row_index"], "native_locator_value": item["native_locator_value"]}
                for item in candidates.get(value, [])
            ]
            if value is None:
                status = "invalid"
            elif not scopes:
                status = "dangling"
            elif len(scopes) > 1:
                status = "ambiguous"
            else:
                status = "resolved_unique"
        target_key = None
        if status == "resolved_unique":
            target = candidates[value][0]
            target_key = _row_key(LOOKUP_TABLE, target["row_index"])
            if (LOOKUP_TABLE, target["row_index"]) not in selected:
                _fail(f"resolved PushTips target is outside canary selection: {target_key}")
        result.append({
            "source_table": table,
            "source_row_index": index,
            "source_row_key": _row_key(table, index),
            "source_record_pointer": source["pointer"],
            "source_pointer": pointer,
            "field_name": field,
            "array_index": None,
            "raw_value": raw_value,
            "candidate_key": {"table": LOOKUP_TABLE, "field": "pushTipId", "value": value} if value is not None else {"table": LOOKUP_TABLE, "field": "pushTipId"},
            "resolution_status": status,
            "target_row_key": target_key,
            "candidate_scopes": scopes,
        })
    result.sort(key=lambda item: item["source_pointer"])
    return result


def _text_occurrences(
    selected: dict[tuple[str, int], dict[str, Any]],
    values: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    shards: dict[tuple[str, str], dict[str, Any]] = {}
    for locale in LOCALES:
        for kind, path in TEXTMAP_PATHS[locale].items():
            loaded = values.get(path)
            if not isinstance(loaded, dict):
                _fail(f"Tutorial TextMap shard is not an object: {path}")
            shards[(locale, kind)] = loaded
    owners: list[dict[str, Any]] = []
    resolutions: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    field_counts: Counter[str] = Counter()
    for (table, index), source in sorted(selected.items()):
        fields = TEXT_OWNER_FIELDS.get(table, ())
        if not fields:
            continue
        row = source["raw_value"]
        for field, field_role in fields:
            if field not in row:
                _fail(f"Tutorial text owner field is missing: {table}/{index}/{field}")
            value = _positive_int(row[field])
            if value is None:
                _fail(f"Tutorial text owner hash is invalid: {table}/{index}/{field}")
            pointer = f"/{index}/{field}"
            owner_key = f"{table}:{index}:{field}"
            owner = {
                "table": table,
                "row_index": index,
                "row_key": _row_key(table, index),
                "source_record_pointer": source["pointer"],
                "source_pointer": pointer,
                "field_name": field,
                "field_role": field_role,
                "textmap_hash": value,
                "raw_value": row[field],
                "owner_key": owner_key,
            }
            owners.append(owner)
            field_counts[f"{table}.{field}"] += 1
            for locale in LOCALES:
                main = shards[(locale, "main")].get(value)
                medium = shards[(locale, "medium")].get(value)
                if main is not None and not isinstance(main, str):
                    _fail(f"Tutorial TextMap main candidate is not text: {locale}/{value}")
                if medium is not None and not isinstance(medium, str):
                    _fail(f"Tutorial TextMap medium candidate is not text: {locale}/{value}")
                if main is None and medium is None:
                    status, chosen = "missing", None
                elif main is not None and medium is None:
                    status, chosen = "unique_main", main
                elif main is None and medium is not None:
                    status, chosen = "unique_medium", medium
                elif main == medium:
                    status, chosen = "both_equal", main
                else:
                    status, chosen = "both_conflict", None
                scopes: list[dict[str, Any]] = []
                for kind, text in (("main", main), ("medium", medium)):
                    if text is None:
                        continue
                    path = TEXTMAP_PATHS[locale][kind]
                    scopes.append({"relative_path": path, "source_pointer": f"/{value}", "shard_kind": kind})
                    normalized = normalize_text(text)
                    candidates.append({
                        "owner_key": owner_key,
                        "owner_table": table,
                        "owner_row_index": index,
                        "relationship_source_pointer": pointer,
                        "locale": locale,
                        "textmap_hash": value,
                        "resolution_status": status,
                        "source_relative_path": path,
                        "source_pointer": f"/{value}",
                        "shard_kind": kind,
                        "raw_text": text,
                        "normalized_text": normalized,
                        "raw_checksum": sha256_text(text),
                        "normalized_checksum": sha256_text(normalized),
                    })
                normalized = None if chosen is None else normalize_text(chosen)
                resolutions.append({
                    "owner_key": owner_key,
                    "owner_table": table,
                    "owner_row_index": index,
                    "relationship_source_pointer": pointer,
                    "locale": locale,
                    "textmap_hash": value,
                    "resolution_status": status,
                    "raw_text": chosen,
                    "normalized_text": normalized,
                    "raw_checksum": None if chosen is None else sha256_text(chosen),
                    "normalized_checksum": None if normalized is None else sha256_text(normalized),
                    "candidate_scopes": scopes,
                })
    owners.sort(key=lambda item: item["owner_key"])
    resolutions.sort(key=lambda item: (item["owner_key"], item["locale"]))
    candidates.sort(key=lambda item: (item["owner_key"], item["locale"], item["shard_kind"]))
    return owners, resolutions, dict(sorted(field_counts.items()))


def _images(
    selected: dict[tuple[str, int], dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for (table, index), source in sorted(selected.items()):
        if table != "TutorialDetailExcelConfigData":
            continue
        row = source["raw_value"]
        field = "imageNameList"
        if field not in row or row[field] is None:
            result.append({
                "source_table": table,
                "source_row_index": index,
                "source_row_key": _row_key(table, index),
                "source_record_pointer": source["pointer"],
                "source_pointer": f"/{index}/{field}",
                "field_name": field,
                "array_index": None,
                "raw_image_name": None if field not in row else row[field],
                "reference_status": "missing",
            })
            continue
        if not isinstance(row[field], list):
            result.append({
                "source_table": table,
                "source_row_index": index,
                "source_row_key": _row_key(table, index),
                "source_record_pointer": source["pointer"],
                "source_pointer": f"/{index}/{field}",
                "field_name": field,
                "array_index": None,
                "raw_image_name": row[field],
                "reference_status": "invalid",
            })
            continue
        for array_index, raw_name in enumerate(row[field]):
            result.append({
                "source_table": table,
                "source_row_index": index,
                "source_row_key": _row_key(table, index),
                "source_record_pointer": source["pointer"],
                "source_pointer": f"/{index}/{field}/{array_index}",
                "field_name": field,
                "array_index": array_index,
                "raw_image_name": raw_name,
                "reference_status": "observed" if isinstance(raw_name, str) else "invalid",
            })
    result.sort(key=lambda item: item["source_pointer"])
    return result


def _raw_fields(selected: dict[tuple[str, int], dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for (table, index), source in sorted(selected.items()):
        row = source["raw_value"]
        for field, value in row.items():
            root_pointer = f"/{index}/{field}"
            if isinstance(value, list):
                result.append({
                    "source_table": table,
                    "source_row_index": index,
                    "source_row_key": _row_key(table, index),
                    "source_record_pointer": source["pointer"],
                    "source_pointer": root_pointer,
                    "field_name": field,
                    "array_index": None,
                    "field_kind": "array",
                    "raw_value": value,
                })
                for array_index, element in enumerate(value):
                    result.append({
                        "source_table": table,
                        "source_row_index": index,
                        "source_row_key": _row_key(table, index),
                        "source_record_pointer": source["pointer"],
                        "source_pointer": f"{root_pointer}/{array_index}",
                        "field_name": field,
                        "array_index": array_index,
                        "field_kind": "array_item",
                        "raw_value": element,
                    })
            else:
                result.append({
                    "source_table": table,
                    "source_row_index": index,
                    "source_row_key": _row_key(table, index),
                    "source_record_pointer": source["pointer"],
                    "source_pointer": root_pointer,
                    "field_name": field,
                    "array_index": None,
                    "field_kind": "scalar",
                    "raw_value": value,
                })
    result.sort(key=lambda item: (item["source_table"], item["source_row_index"], item["source_pointer"]))
    return result


def _full_boundary_missing_fields(
    selected: dict[tuple[str, int], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Represent accepted absent relationship/asset fields as raw coordinates.

    Full-boundary release counts intentionally include only present detail
    array positions and image references.  The six absent ``detailIdList``
    fields and eight absent ``imageNameList`` fields remain observable through
    raw-field rows with a null value and an exact source pointer.
    """
    result: list[dict[str, Any]] = []
    fields = (
        ("TutorialExcelConfigData", "detailIdList"),
        ("TutorialDetailExcelConfigData", "imageNameList"),
    )
    for (table, index), source in sorted(selected.items()):
        row = source["raw_value"]
        for owner_table, field in fields:
            if table != owner_table or field in row:
                continue
            result.append({
                "source_table": table,
                "source_row_index": index,
                "source_row_key": _row_key(table, index),
                "source_record_pointer": source["pointer"],
                "source_pointer": f"/{index}/{field}",
                "field_name": field,
                "array_index": None,
                "field_kind": "scalar",
                "raw_value": None,
                "missing_field": True,
            })
    result.sort(key=lambda item: (item["source_table"], item["source_row_index"], item["source_pointer"]))
    return result


def _source_records(
    selected_rows: list[dict[str, Any]],
    text_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for item in selected_rows:
        key = (item["relative_path"], item["pointer"])
        output[key] = {
            "relative_path": item["relative_path"],
            "source_pointer": item["pointer"],
            "record_kind": "excel_row",
            "native_id": item["native_locator_value"],
            "raw_value": item["raw_value"],
            "raw_checksum": item["raw_checksum"],
        }
    for item in text_candidates:
        key = (item["source_relative_path"], item["source_pointer"])
        output[key] = {
            "relative_path": key[0],
            "source_pointer": key[1],
            "record_kind": "textmap_entry",
            "native_id": item["textmap_hash"],
            "raw_value": item["raw_text"],
            "raw_checksum": item["raw_checksum"],
        }
    result = sorted(output.values(), key=lambda item: (item["relative_path"], item["source_pointer"]))
    return result


def derive_tutorial_phase4d2a_source_contract(
    source_root: str | Path,
    manifest: Any,
) -> dict[str, Any]:
    """Derive all bounded canary observations from the pinned source."""
    root = Path(source_root).resolve()
    verify_source_pin(root)
    raw_manifest = _manifest_value(manifest)
    files, values = _source_files(root, raw_manifest)
    selected_specs = _selected_specs(raw_manifest)
    selected_rows, selected_by_key = _verify_selected_rows(values, selected_specs)
    scans = _candidate_scan(values, raw_manifest)
    raw_fields = _raw_fields(selected_by_key)
    full_boundary = bool(raw_manifest.get("boundary", {}).get("full_core_tutorial_scan") is True)
    missing_fields = _full_boundary_missing_fields(selected_by_key) if full_boundary else []
    raw_fields.extend(missing_fields)
    detail_relations = _detail_relations(selected_by_key, values, scans)
    push_relations = _push_tips_relations(selected_by_key, values, scans)
    if full_boundary:
        detail_relations = [item for item in detail_relations if item["resolution_status"] != "missing"]
    text_owners, text_resolutions, field_counts = _text_occurrences(selected_by_key, values)
    text_candidates: list[dict[str, Any]] = []
    # Reconstruct candidates from each resolution so callers receive the same
    # physical candidate list without exposing a second mutable index.
    for resolution in text_resolutions:
        for scope in resolution["candidate_scopes"]:
            path = scope["relative_path"]
            locale = resolution["locale"]
            kind = scope["shard_kind"]
            text = values[path].get(str(resolution["textmap_hash"]))
            normalized = normalize_text(text)
            text_candidates.append({
                "owner_key": resolution["owner_key"],
                "owner_table": resolution["owner_table"],
                "owner_row_index": resolution["owner_row_index"],
                "relationship_source_pointer": resolution["relationship_source_pointer"],
                "locale": locale,
                "textmap_hash": resolution["textmap_hash"],
                "resolution_status": resolution["resolution_status"],
                "source_relative_path": path,
                "source_pointer": scope["source_pointer"],
                "shard_kind": kind,
                "raw_text": text,
                "normalized_text": normalized,
                "raw_checksum": sha256_text(text),
                "normalized_checksum": sha256_text(normalized),
            })
    text_candidates.sort(key=lambda item: (item["owner_key"], item["locale"], item["shard_kind"]))
    images = _images(selected_by_key)
    if full_boundary:
        images = [item for item in images if item["reference_status"] != "missing"]
    source_records = _source_records(selected_rows, text_candidates)
    all_relations = detail_relations + push_relations
    relation_digest_items = [
        {key: item[key] for key in (
            "source_table", "source_row_index", "source_pointer", "field_name",
            "array_index", "raw_value", "candidate_key", "resolution_status",
            "target_row_key", "candidate_scopes",
        )}
        for item in all_relations
    ]
    occurrence_digests = {
        "rows": _set_digest([
            {key: item[key] for key in ("table", "relative_path", "row_index", "pointer", "native_locator_field", "native_locator_value", "raw_checksum")}
            for item in selected_rows
        ]),
        "raw_fields": _set_digest([
            {key: item[key] for key in ("source_table", "source_row_index", "source_pointer", "field_name", "array_index", "field_kind", "raw_value")}
            for item in raw_fields
        ]),
        "detail_relations": _set_digest([item for item in relation_digest_items if item["field_name"] == "detailIdList"]),
        "push_tips": _set_digest([item for item in relation_digest_items if item["field_name"] == "pushTipsId"]),
        "text_owners": _set_digest([
            {key: item[key] for key in ("table", "row_index", "source_pointer", "field_name", "field_role", "textmap_hash", "raw_value")}
            for item in text_owners
        ]),
        "text_resolutions": _set_digest([
            {key: item[key] for key in ("owner_key", "locale", "textmap_hash", "resolution_status", "raw_text", "normalized_text", "raw_checksum", "normalized_checksum", "candidate_scopes")}
            for item in text_resolutions
        ]),
        "text_candidates": _set_digest([
            {key: item[key] for key in ("owner_key", "locale", "textmap_hash", "resolution_status", "source_relative_path", "source_pointer", "shard_kind", "raw_text", "normalized_text", "raw_checksum", "normalized_checksum")}
            for item in text_candidates
        ]),
        "image_references": _set_digest([
            {key: item[key] for key in ("source_table", "source_row_index", "source_pointer", "field_name", "array_index", "raw_image_name", "reference_status")}
            for item in images
        ]),
        "source_records": _set_digest([
            {key: item[key] for key in ("relative_path", "source_pointer", "record_kind", "native_id", "raw_checksum")}
            for item in source_records
        ]),
    }
    counts = {
        "selected_files": len(files),
        "source_records": len(source_records),
        "rows": len(selected_rows),
        "primary_rows": sum(item["family_role"] == "primary" for item in selected_rows),
        "lookup_rows": sum(item["family_role"] == "lookup" for item in selected_rows),
        "raw_fields": len(raw_fields),
        "detail_relations": len(detail_relations),
        "push_tips_relations": len(push_relations),
        "relations": len(all_relations),
        "text_owners": len(text_owners),
        "text_resolutions": len(text_resolutions),
        "text_candidates": len(text_candidates),
        "image_references": len(images),
    }
    status_counts = {
        "detail_relations": dict(sorted(Counter(item["resolution_status"] for item in detail_relations).items())),
        "push_tips_relations": dict(sorted(Counter(item["resolution_status"] for item in push_relations).items())),
        "text_resolutions": {
            status: sum(item["resolution_status"] == status for item in text_resolutions)
            for status in ("unique_main", "unique_medium", "both_equal", "both_conflict", "missing", "invalid")
        },
        "image_references": dict(sorted(Counter(item["reference_status"] for item in images).items())),
        "raw_fields": dict(sorted(Counter(item["field_kind"] for item in raw_fields).items())),
    }
    selected_table_counts = {
        table: len(selected_specs[table]) for table in EXPECTED_TABLES
    }
    selected_records_by_file = Counter(item["relative_path"] for item in selected_rows)
    selected_records_by_file.update(item["source_relative_path"] for item in text_candidates)
    files_out: list[dict[str, Any]] = []
    for item in sorted(files, key=lambda value: value["relative_path"]):
        files_out.append({
            key: item[key]
            for key in ("relative_path", "format", "role", "source_records", "selected_records", "size_bytes", "sha256", "blob_oid")
        } | {"selected_records": selected_records_by_file[item["relative_path"]]})
    return {
        "files": files_out,
        "selected_rows": selected_rows,
        "candidate_scans": {
            table: {key: value for key, value in scan.items() if key != "observations"}
            for table, scan in scans.items()
        },
        "rows": selected_rows,
        "raw_fields": raw_fields,
        "detail_relations": detail_relations,
        "push_relations": push_relations,
        "relations": all_relations,
        "text_owners": text_owners,
        "text_resolutions": text_resolutions,
        "text_candidates": text_candidates,
        "image_references": images,
        "source_records": source_records,
        "counts": counts,
        "status_counts": status_counts,
        "selected_table_counts": selected_table_counts,
        "text_owner_field_counts": field_counts,
        "missing_fields": {
            "TutorialExcelConfigData.detailIdList": sum(
                item["field_name"] == "detailIdList" for item in missing_fields
            ),
            "TutorialDetailExcelConfigData.imageNameList": sum(
                item["field_name"] == "imageNameList" for item in missing_fields
            ),
        },
        "occurrence_set_digests": occurrence_digests,
        "selected_records_by_file": dict(sorted(selected_records_by_file.items())),
    }


def expected_identity(manifest: Any) -> dict[str, str]:
    raw = _manifest_value(manifest)
    versions = raw.get("versions")
    manifest_id = raw.get("manifest_id")
    if not isinstance(versions, dict) or not isinstance(manifest_id, str):
        _fail("Tutorial manifest versions/identity are missing")
    parser_build_id = digest_id(
        "pb-tutorial-phase4d2a",
        canonical_json({"versions": versions, "manifest_id": manifest_id}),
    )
    config_checksum = sha256_text(canonical_json({
        "manifest_id": manifest_id,
        "versions": versions,
        "selected_rows": raw.get("selected_rows", []),
        "candidate_scans": raw.get("candidate_scans", {}),
    }))
    ingest_run_id = digest_id(
        "ir-tutorial-phase4d2a",
        EXPECTED_SNAPSHOT_ID,
        parser_build_id,
        config_checksum,
    )
    return {
        "snapshot_id": EXPECTED_SNAPSHOT_ID,
        "parser_build_id": parser_build_id,
        "config_checksum": config_checksum,
        "ingest_run_id": ingest_run_id,
    }


def occurrence_id(kind: str, identity: Mapping[str, str], *parts: Any) -> str:
    return digest_id(f"tutorial-{kind}", identity["snapshot_id"], identity["parser_build_id"], identity["ingest_run_id"], *parts)


__all__ = [
    "RUN_FAMILY",
    "TutorialPhase4D2AContractError",
    "derive_tutorial_phase4d2a_source_contract",
    "expected_identity",
    "occurrence_id",
    "strict_json_file",
    "verify_source_pin",
]
