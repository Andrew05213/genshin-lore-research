"""Pinned, family-specific contract for the Phase 4E2 World Archive canary.

The contract reads the immutable 4E1 audit artifacts and the pinned upstream
files.  It creates physical occurrence tuples only; it has no database side
effects and defines no universal Codex/Archive entity model.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .util import canonical_json, digest_id, normalize_text, sha256_bytes, sha256_text


FAMILY = "world_archive"
FAMILY_CONTRACT_VERSION = "world-archive-evidence-v1"
RUN_FAMILY = "world_archive_phase4e2_canary"
EXPECTED_COMMIT = "26df1dfbdf05a82bbb1d97506859f3e1c40718d8"
EXPECTED_TREE = "d891ef1d95fdd6c27a1da8a32ea7a61b483647a6"
EXPECTED_SNAPSHOT_ID = f"anime-game-data:7.0:{EXPECTED_COMMIT}"
EXPECTED_BUILD = "CNRELWin7.0.0_R47482070_S47579390_D47579390"
EXPECTED_OBSERVED_AT = "2026-08-19T00:00:00+08:00"
EXPECTED_MANIFEST_SHA256 = "ec2d180ee7eb6ff5df1fab0cf5aa659df84391466e0fdab0014a189a4fe5ea05"
MANIFEST_ID = "anime-game-data-7.0-phase4e2-world-archive-canary"
VERSIONS = {
    "family_contract": FAMILY_CONTRACT_VERSION,
    "parser": "world-archive-phase4e2-canary-parser-v1",
    "projection": "world-archive-phase4e2-family-projection-v1",
    "relationship_rule": "phase4e1-field-specific-candidate-v1",
    "text_rule": "explicit-owner-locale-main-medium-v1",
    "asset_rule": "raw-reference-observation-v1",
    "identity_rule": "snapshot-run-source-coordinate-occurrence-v1",
}
PRIMARY_PATHS = {
    "AnimalCodexExcelConfigData": "ExcelBinOutput/AnimalCodexExcelConfigData.json",
    "ViewCodexExcelConfigData": "ExcelBinOutput/ViewCodexExcelConfigData.json",
}
SELECTED_ROWS = {
    "AnimalCodexExcelConfigData": (0, 12, 170),
    "ViewCodexExcelConfigData": (0,),
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


class WorldArchivePhase4E2ContractError(RuntimeError):
    pass


def _fail(message: str) -> None:
    raise WorldArchivePhase4E2ContractError(message)


def _strict_bytes(raw: bytes, label: str) -> Any:
    def reject(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                _fail(f"duplicate JSON key in {label}: {key}")
            out[key] = value
        return out
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=reject)
    except (UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot parse {label}: {exc}")


def strict_json_file(path: Path, label: str) -> Any:
    try:
        return _strict_bytes(path.read_bytes(), label)
    except OSError as exc:
        _fail(f"cannot read {label}: {exc}")


def verify_source_pin(root: Path) -> None:
    root = root.resolve()
    try:
        top = subprocess.run(["git", "--no-optional-locks", "-C", str(root), "rev-parse", "--show-toplevel"], check=True, capture_output=True, text=True, timeout=60).stdout.strip()
        status = subprocess.run(["git", "--no-optional-locks", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"], check=True, capture_output=True, text=True, timeout=60).stdout.strip()
        commit = subprocess.run(["git", "--no-optional-locks", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True, timeout=60).stdout.strip()
        tree = subprocess.run(["git", "--no-optional-locks", "-C", str(root), "rev-parse", "HEAD^{tree}"], check=True, capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        _fail(f"cannot inspect pinned World Archive source: {exc}")
    if Path(top).resolve() != root or status or commit != EXPECTED_COMMIT or tree != EXPECTED_TREE:
        _fail("World Archive source checkout pin/cleanliness mismatch")


def _audit_reports(repo_root: Path) -> dict[str, Any]:
    base = repo_root.resolve() / "reports/phase4e1-world-archive"
    reports: dict[str, Any] = {}
    for name, expected in AUDIT_ARTIFACTS.items():
        path = base / name
        raw = path.read_bytes()
        if sha256_bytes(raw) != expected:
            _fail(f"Phase 4E1 artifact digest mismatch: {name}")
        value = _strict_bytes(raw, name)
        if not isinstance(value, dict) or value.get("audit_version") != "4E1.1":
            _fail(f"Phase 4E1 artifact contract mismatch: {name}")
        reports[name] = value
    return reports


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    raw = manifest_path.read_bytes()
    if EXPECTED_MANIFEST_SHA256 and sha256_bytes(raw) != EXPECTED_MANIFEST_SHA256:
        _fail("World Archive Phase 4E2 manifest SHA-256 mismatch")
    value = _strict_bytes(raw, "World Archive Phase 4E2 manifest")
    if not isinstance(value, dict) or set(value) != {"schema_version", "manifest_id", "source", "versions", "audit_binding", "boundary", "identity", "files", "selected_rows", "expected"}:
        _fail("World Archive Phase 4E2 manifest root keys mismatch")
    if value["schema_version"] != 1 or value["manifest_id"] != MANIFEST_ID or value["versions"] != VERSIONS:
        _fail("World Archive Phase 4E2 manifest identity/version mismatch")
    source = value["source"]
    expected_source = {"source_id": "anime-game-data", "upstream": "https://github.com/DimbreathBot/AnimeGameData", "release_label": "7.0", "build": EXPECTED_BUILD, "commit": EXPECTED_COMMIT, "tree": EXPECTED_TREE, "observed_at": EXPECTED_OBSERVED_AT, "snapshot_id": EXPECTED_SNAPSHOT_ID}
    if source != expected_source:
        _fail("World Archive Phase 4E2 source identity mismatch")
    boundary = value["boundary"]
    if boundary != {"family": FAMILY, "family_contract_version": FAMILY_CONTRACT_VERSION, "selected_rows": {key: list(rows) for key, rows in SELECTED_ROWS.items()}, "selected_primary_files": 2, "selected_textmap_files": 6, "full_world_archive_scan": False, "corpus_complete": False}:
        _fail("World Archive Phase 4E2 bounded boundary mismatch")
    identity = value["identity"]
    if identity != {"raw_evidence_identity": "snapshot_parser_build_ingest_run_source_file_source_record_row_field_occurrence", "logical_identity_created": False, "native_id_identity_effect": "none_table_scoped_locator_only", "text_hash_identity_effect": "none_owner_locale_candidate_locator_only", "asset_identity_effect": "none_raw_reference_only", "checksum_identity_effect": "none_change_detection_only", "context_scope": "single_physical_world_archive_row_container_only"}:
        _fail("World Archive Phase 4E2 identity contract mismatch")
    if value["audit_binding"] != AUDIT_ARTIFACTS:
        _fail("World Archive Phase 4E2 audit binding mismatch")
    files = value["files"]
    if not isinstance(files, list) or len(files) != 8 or len({item.get("relative_path") for item in files if isinstance(item, dict)}) != 8:
        _fail("World Archive Phase 4E2 file boundary mismatch")
    selected = value["selected_rows"]
    if not isinstance(selected, list) or len(selected) != 4:
        _fail("World Archive Phase 4E2 selected row count mismatch")
    return value


def _scope_selected(item: Mapping[str, Any]) -> bool:
    scope = item.get("scope") or item.get("source_scope") or item.get("owner_scope")
    return isinstance(scope, dict) and scope.get("row") in SELECTED_ROWS.get(str(item.get("table") or str(scope.get("path", "")).split("/")[-1].removesuffix(".json")), ())


def _set_digest(items: list[dict[str, Any]], keys: tuple[str, ...]) -> str:
    return sha256_text(canonical_json(sorted(canonical_json({key: item.get(key) for key in keys}) for item in items)))


def derive_source_contract(source_root: str | Path, repo_root: str | Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    root = Path(source_root).resolve()
    verify_source_pin(root)
    reports = _audit_reports(Path(repo_root))
    inventory = reports["phase4e1-source-inventory.json"]
    files = inventory.get("files")
    if not isinstance(files, list) or len(files) != 8:
        _fail("Phase 4E1 selected file inventory mismatch")
    manifest_files = manifest.get("files")
    by_manifest = {item["relative_path"]: item for item in manifest_files if isinstance(item, dict)}
    file_values: dict[str, Any] = {}
    files_out: list[dict[str, Any]] = []
    for item in files:
        relative = item["relative_path"]
        raw = (root / relative).read_bytes()
        if len(raw) != item["size_bytes"] or sha256_bytes(raw) != item["sha256"]:
            _fail(f"World Archive source file digest mismatch: {relative}")
        file_values[relative] = _strict_bytes(raw, relative)
        wanted = {key: item[key] for key in ("relative_path", "format", "role", "source_records", "size_bytes", "sha256", "blob_oid")}
        if by_manifest.get(relative) != wanted:
            _fail(f"World Archive manifest/source inventory mismatch: {relative}")
        files_out.append(wanted)

    row_report = reports["phase4e1-row-identity-audit.json"]
    selected_keys = {(table, index) for table, indexes in SELECTED_ROWS.items() for index in indexes}
    row_audit = {(item["table"], item["scope"]["row"]): item for item in row_report["row_occurrences"] if (item["table"], item["scope"]["row"]) in selected_keys}
    rows: list[dict[str, Any]] = []
    raw_fields: list[dict[str, Any]] = []
    source_records: list[dict[str, Any]] = []
    for table, index in sorted(selected_keys):
        audit = row_audit[(table, index)]
        raw_row = file_values[PRIMARY_PATHS[table]][index]
        locator = audit["native_locator"]
        if raw_row.get("id") != locator["raw_value"]:
            _fail(f"World Archive selected row locator mismatch: {table}/{index}")
        pointer = f"/{index}"
        checksum = sha256_text(canonical_json(raw_row))
        row = {"table": table, "relative_path": PRIMARY_PATHS[table], "row_index": index, "pointer": pointer, "native_locator_value": str(raw_row["id"]), "raw_checksum": checksum, "raw_value": raw_row}
        rows.append(row)
        source_records.append({"relative_path": row["relative_path"], "source_pointer": pointer, "record_kind": "world_archive_primary_row", "native_id": str(raw_row["id"]), "raw_checksum": checksum, "raw_value": raw_row})
        for field in audit["raw_fields"]:
            raw_fields.append({"table": table, "row_index": index, "source_pointer": field["scope"]["pointer"], "field_name": field["scope"]["field"], "field_kind": "array" if isinstance(field["raw_value"], list) else "scalar", "raw_value": field["raw_value"]})

    rel_report = reports["phase4e1-relationship-audit.json"]
    relationships: list[dict[str, Any]] = []
    for values in rel_report["relationships"].values():
        for item in values:
            scope = item["source_scope"]
            table = scope["path"].split("/")[-1].removesuffix(".json")
            if (table, scope["row"]) not in selected_keys:
                continue
            relationships.append({"table": table, "row_index": scope["row"], "source_pointer": scope["pointer"], "field_name": scope["field"], "relation_kind": item["relation_kind"], "raw_value": item["raw_value"], "candidate_key": item["candidate_key"], "resolution_status": item["status"], "target_table": item["target_table"], "target_field": item["target_field"], "candidate_scopes": item["target_candidate_scopes"], "mapping_basis": item["mapping_basis"]})
    opaque: list[dict[str, Any]] = []
    for item in rel_report["unresolved_id_like_field_occurrences"]:
        scope = item["scope"]
        if (item["table"], scope["row"]) in selected_keys:
            opaque.append({"table": item["table"], "row_index": scope["row"], "source_pointer": scope["pointer"], "field_name": scope["field"], "array_index": None, "raw_value": item["raw_value"], "resolution_status": "opaque_unresolved", "reason": item["reason"]})
    for item in rel_report["array_element_occurrences"]:
        scope = item["scope"]
        if (item["table"], scope["row"]) in selected_keys:
            opaque.append({"table": item["table"], "row_index": scope["row"], "source_pointer": scope["pointer"], "field_name": scope["field"], "array_index": scope["array_index"], "raw_value": item["raw_value"], "resolution_status": "opaque_unresolved", "reason": "obfuscated_array_position_has_no_authoritative_mapping"})

    text_report = reports["phase4e1-text-owner-audit.json"]
    owners: list[dict[str, Any]] = []
    resolutions: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for item in text_report["owner_occurrences"]:
        scope = item["owner_scope"]
        if (item["table"], scope["row"]) not in selected_keys:
            continue
        owner_key = f"{item['table']}:{scope['row']}:{item['field']}"
        owner = {"owner_key": owner_key, "table": item["table"], "row_index": scope["row"], "source_pointer": scope["pointer"], "field_name": item["field"], "field_role": item["field_role"], "textmap_hash": item["normalized_hash"], "raw_value": item["raw_value"]}
        owners.append(owner)
        for locale, resolution in sorted(item["locale_resolutions"].items()):
            status = resolution["status"]
            valid = [candidate for candidate in resolution["candidates"] if candidate["status"] == "valid_text"]
            chosen = valid[0] if status in {"unique_main", "unique_medium", "both_equal"} else None
            res = {"owner_key": owner_key, "table": item["table"], "row_index": scope["row"], "source_pointer": scope["pointer"], "locale": locale, "textmap_hash": item["normalized_hash"], "resolution_status": status, "raw_text": None if chosen is None else chosen["raw_value"], "normalized_text": None if chosen is None else normalize_text(chosen["raw_value"]), "raw_checksum": None if chosen is None else chosen["text_checksum"], "normalized_checksum": None if chosen is None else sha256_text(normalize_text(chosen["raw_value"])), "candidate_scopes": resolution["candidate_scopes"]}
            resolutions.append(res)
            for candidate in valid:
                candidate_scope = candidate["scope"]
                raw_text = candidate["raw_value"]
                cand = {"owner_key": owner_key, "locale": locale, "shard_kind": candidate["candidate_kind"], "source_relative_path": candidate_scope["path"], "source_pointer": candidate_scope["pointer"], "raw_text": raw_text, "normalized_text": normalize_text(raw_text), "raw_checksum": candidate["text_checksum"], "normalized_checksum": sha256_text(normalize_text(raw_text))}
                candidates.append(cand)
                record = {"relative_path": cand["source_relative_path"], "source_pointer": cand["source_pointer"], "record_kind": "world_archive_text_candidate", "native_id": str(item["normalized_hash"]), "raw_checksum": cand["raw_checksum"], "raw_value": raw_text}
                if record not in source_records:
                    source_records.append(record)

    asset_report = reports["phase4e1-asset-reference-audit.json"]
    assets: list[dict[str, Any]] = []
    for item in asset_report["field_observations"]:
        scope = item["scope"]
        if (item["table"], scope["row"]) in selected_keys:
            status = "observed" if item["status"] == "present" else item["status"]
            assets.append({"table": item["table"], "row_index": scope["row"], "source_pointer": scope["pointer"], "field_name": scope["field"], "asset_kind": item["asset_role"], "raw_reference": item["raw_value"] if status == "observed" else None, "reference_status": status})

    collections = {"rows": rows, "raw_fields": raw_fields, "relationships": relationships, "opaque": opaque, "text_owners": owners, "text_resolutions": resolutions, "text_candidates": candidates, "assets": assets, "source_records": source_records}
    counts = {key: len(value) for key, value in collections.items()} | {"files": len(files_out)}
    status_counts = {
        "relationships": dict(sorted(Counter(item["resolution_status"] for item in relationships).items())),
        "opaque": dict(sorted(Counter(item["resolution_status"] for item in opaque).items())),
        "text_resolutions": dict(sorted(Counter(item["resolution_status"] for item in resolutions).items())),
        "assets": dict(sorted(Counter(item["reference_status"] for item in assets).items())),
    }
    digests = {key: _set_digest(value, tuple(sorted({field for item in value for field in item}))) for key, value in collections.items() if key != "source_records"}
    result = {"files": files_out, **collections, "counts": counts, "status_counts": status_counts, "occurrence_set_digests": digests, "selected_table_counts": {key: len(value) for key, value in SELECTED_ROWS.items()}}
    expected = manifest.get("expected")
    if isinstance(expected, dict) and expected and expected != {key: result[key] for key in ("counts", "status_counts", "occurrence_set_digests", "selected_table_counts")}:
        _fail("World Archive derived contract differs from sealed manifest")
    return result


def expected_identity(manifest: Mapping[str, Any]) -> dict[str, str]:
    parser_build_id = digest_id("pb-world-archive-phase4e2", canonical_json({"versions": manifest["versions"], "manifest_id": manifest["manifest_id"]}))
    config_checksum = sha256_text(canonical_json({"manifest_id": manifest["manifest_id"], "versions": manifest["versions"], "selected_rows": manifest["selected_rows"], "audit_binding": manifest["audit_binding"]}))
    ingest_run_id = digest_id("ir-world-archive-phase4e2", EXPECTED_SNAPSHOT_ID, parser_build_id, config_checksum)
    return {"snapshot_id": EXPECTED_SNAPSHOT_ID, "parser_build_id": parser_build_id, "config_checksum": config_checksum, "ingest_run_id": ingest_run_id}


def occurrence_id(kind: str, identity: Mapping[str, str], *parts: Any) -> str:
    return digest_id(f"world-archive-{kind}", identity["snapshot_id"], identity["parser_build_id"], identity["ingest_run_id"], *parts)
