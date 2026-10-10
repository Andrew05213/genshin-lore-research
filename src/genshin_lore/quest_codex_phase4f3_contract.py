"""Frozen full-boundary source contract for Phase 4F3 QuestCodex Archive."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .quest_codex_phase4f2_contract import (
    AUDIT_ARTIFACTS,
    EXPECTED_BUILD,
    EXPECTED_COMMIT,
    EXPECTED_OBSERVED_AT,
    EXPECTED_SNAPSHOT_ID,
    EXPECTED_TREE,
    FAMILY,
    FAMILY_CONTRACT_VERSION,
    PRIMARY_PATH,
    QUEST_LOOKUP_PATH,
    SUPPORT_PATH,
    _digest,
    _reports,
    _strict,
    _table,
    verify_source_pin,
)
from .util import canonical_json, digest_id, normalize_text, sha256_bytes, sha256_text

RUN_FAMILY = "quest_codex_archive_phase4f3_full_source_closure"
EXPECTED_COVERAGE = "declared_quest_codex_archive_source_closure_complete"
MANIFEST_ID = "anime-game-data-7.0-phase4f3-quest-codex-full"
EXPECTED_MANIFEST_SHA256 = "bc01b928cfc28c1dcb10dac4a303f22117d09b8d84458a739fc632ac91bdc7b8"
VERSIONS = {
    "manifest_contract": "quest-codex-phase4f3-full-boundary-manifest-v1",
    "family_contract": FAMILY_CONTRACT_VERSION,
    "parser": "quest-codex-phase4f3-full-boundary-parser-v1",
    "projection": "quest-codex-phase4f3-full-boundary-projection-v1",
    "relationship_rule": "phase4f1-exact-source-coordinate-candidates-v1",
    "text_rule": "explicit-chapter-owner-locale-main-medium-v1",
    "asset_rule": "raw-reference-observation-v1",
    "identity_rule": "snapshot-run-source-coordinate-occurrence-v1",
}


class QuestCodexPhase4F3ContractError(RuntimeError):
    pass


def _fail(message: str) -> None:
    raise QuestCodexPhase4F3ContractError(message)


def load_manifest(path: str | Path) -> dict[str, Any]:
    raw = Path(path).resolve().read_bytes()
    if EXPECTED_MANIFEST_SHA256 and sha256_bytes(raw) != EXPECTED_MANIFEST_SHA256:
        _fail("Phase 4F3 manifest SHA-256 mismatch")
    value = _strict(raw, "Phase 4F3 manifest")
    required = {
        "schema_version", "manifest_id", "source", "versions", "audit_binding",
        "boundary", "identity", "files", "selected_rows", "expected",
    }
    if not isinstance(value, dict) or set(value) != required or value["schema_version"] != 1:
        _fail("Phase 4F3 manifest root mismatch")
    if value["manifest_id"] != MANIFEST_ID or value["versions"] != VERSIONS:
        _fail("Phase 4F3 manifest identity/version mismatch")
    if value["audit_binding"] != AUDIT_ARTIFACTS:
        _fail("Phase 4F3 audit binding mismatch")
    expected_source = {
        "source_id": "anime-game-data",
        "upstream": "https://github.com/DimbreathBot/AnimeGameData",
        "release_label": "7.0",
        "build": EXPECTED_BUILD,
        "commit": EXPECTED_COMMIT,
        "tree": EXPECTED_TREE,
        "observed_at": EXPECTED_OBSERVED_AT,
        "snapshot_id": EXPECTED_SNAPSHOT_ID,
    }
    if value["source"] != expected_source:
        _fail("Phase 4F3 source identity mismatch")
    boundary = {
        "family": FAMILY,
        "family_contract_version": FAMILY_CONTRACT_VERSION,
        "primary_rows": 493,
        "referenced_chapter_support_rows": 140,
        "chapter_inventory_rows": 419,
        "quest_lookup_inventory_rows": 17814,
        "selected_source_files": 9,
        "full_quest_codex_scan": True,
        "quest_lookup_projection": "physical_relationship_candidates_only",
        "corpus_complete": True,
        "coverage": EXPECTED_COVERAGE,
    }
    if value["boundary"] != boundary:
        _fail("Phase 4F3 full boundary mismatch")
    identity = {
        "raw_evidence_identity": "snapshot_parser_build_ingest_run_source_file_source_record_coordinate_occurrence",
        "logical_identity_created": False,
        "candidate_identity_effect": "none_physical_lookup_occurrences_only",
        "shared_chapter_projection": "one_physical_support_row_with_independent_relationship_occurrences",
        "text_hash_identity_effect": "none_owner_locale_candidate_locator_only",
        "asset_identity_effect": "none_raw_reference_only",
        "get_scope": "single_physical_row_container_only",
    }
    if value["identity"] != identity:
        _fail("Phase 4F3 identity contract mismatch")
    return value


def derive_source_contract(
    source_root: str | Path,
    repo_root: str | Path,
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(source_root).resolve()
    verify_source_pin(root)
    reports = _reports(Path(repo_root))
    inventory = reports["phase4f1-source-inventory.json"]
    wanted_paths = {PRIMARY_PATH, SUPPORT_PATH, QUEST_LOOKUP_PATH} | {
        item["relative_path"] for item in inventory["files"]
        if item["relative_path"].startswith("TextMap/")
    }
    files: list[dict[str, Any]] = []
    values: dict[str, Any] = {}
    for item in inventory["files"]:
        path = item["relative_path"]
        if path not in wanted_paths:
            continue
        raw = (root / path).read_bytes()
        if len(raw) != item["size_bytes"] or sha256_bytes(raw) != item["sha256"]:
            _fail(f"source file seal mismatch: {path}")
        values[path] = _strict(raw, path)
        files.append({key: item[key] for key in (
            "relative_path", "format", "role", "source_records", "size_bytes", "sha256", "blob_oid"
        )})
    if len(files) != 9:
        _fail("Phase 4F3 selected source file boundary mismatch")

    row_report = reports["phase4f1-row-identity-audit.json"]
    audit_rows = row_report["row_occurrences"]
    rows: list[dict[str, Any]] = []
    source_records: dict[tuple[str, str], dict[str, Any]] = {}
    selected: set[tuple[str, int]] = set()
    for audit in audit_rows:
        scope = audit["scope"]
        path, index = scope["path"], scope["row"]
        raw_row = values[path][index]
        if raw_row.get("id") != audit["native_id"]:
            _fail("row locator mismatch")
        checksum = sha256_text(canonical_json(raw_row))
        pointer = f"/{index}"
        rows.append({
            "evidence_kind": audit["evidence_kind"], "table": _table(path),
            "relative_path": path, "row_index": index, "pointer": pointer,
            "native_locator_value": str(audit["native_id"]), "raw_checksum": checksum,
            "raw_value": raw_row,
        })
        selected.add((path, index))
        source_records[(path, pointer)] = {
            "relative_path": path, "source_pointer": pointer,
            "record_kind": audit["evidence_kind"], "native_id": str(audit["native_id"]),
            "raw_checksum": checksum, "raw_value": raw_row,
        }

    raw_fields: list[dict[str, Any]] = []
    for item in row_report["raw_field_occurrences"]:
        scope = item["scope"]
        raw_fields.append({
            "table": _table(scope["path"]), "row_index": scope["row"],
            "source_pointer": scope["pointer"], "field_name": scope["field"],
            "field_presence": "present", "raw_value": item["raw_value"],
        })
    for row in rows:
        if row["evidence_kind"] == "quest_codex_entry":
            for field in ("tabType", "isDisuse"):
                if field not in row["raw_value"]:
                    raw_fields.append({
                        "table": row["table"], "row_index": row["row_index"],
                        "source_pointer": f"/{row['row_index']}/{field}", "field_name": field,
                        "field_presence": "missing", "raw_value": None,
                    })

    rel_report = reports["phase4f1-relationship-candidate-audit.json"]
    relationships: list[dict[str, Any]] = []
    relationship_candidates: list[dict[str, Any]] = []
    for relation_group in (rel_report["chapter_relationships"], rel_report["parent_quest_candidate_relationships"]):
        for item in relation_group:
            scope = item["source_scope"]
            relation_key = f"{scope['path']}:{scope['pointer']}"
            relationships.append({
                "relationship_key": relation_key, "table": _table(scope["path"]),
                "row_index": scope["row"], "source_pointer": scope["pointer"],
                "field_name": scope["field"], "raw_value": item["raw_value"],
                "target_table": item["target_table"], "target_field": item["target_field"],
                "resolution_status": item["status"],
            })
            for ordinal, target in enumerate(item["target_scopes"]):
                raw_row = values[target["path"]][target["row"]]
                checksum = sha256_text(canonical_json(raw_row))
                relationship_candidates.append({
                    "relationship_key": relation_key, "candidate_ordinal": ordinal,
                    "source_relative_path": target["path"], "source_pointer": target["pointer"],
                    "target_table": item["target_table"], "target_field": item["target_field"],
                    "target_raw_value": raw_row[item["target_field"]], "raw_checksum": checksum,
                    "raw_value": raw_row,
                })
                source_records.setdefault((target["path"], target["pointer"]), {
                    "relative_path": target["path"], "source_pointer": target["pointer"],
                    "record_kind": "relationship_lookup_candidate",
                    "native_id": str(raw_row.get("id", raw_row.get(item["target_field"]))),
                    "raw_checksum": checksum, "raw_value": raw_row,
                })

    text_report = reports["phase4f1-text-evidence-audit.json"]
    owners: list[dict[str, Any]] = []
    for item in text_report["owner_occurrences"]:
        scope = item["scope"]
        owners.append({
            "owner_key": f"{scope['path']}:{scope['pointer']}", "table": _table(scope["path"]),
            "row_index": scope["row"], "source_pointer": scope["pointer"],
            "field_name": scope["field"], "field_role": item["field_role"],
            "textmap_hash": str(item["raw_hash"]), "raw_value": item["raw_hash"],
        })
    owner_keys = {item["owner_key"] for item in owners}
    resolutions: list[dict[str, Any]] = []
    text_candidates: list[dict[str, Any]] = []
    for item in text_report["resolutions"]:
        scope = item["owner_scope"]
        key = f"{scope['path']}:{scope['pointer']}"
        if key not in owner_keys:
            _fail("text resolution owner escaped selected Chapter support rows")
        chosen = item["candidates"][0] if item["status"] == "unique_medium" else None
        resolutions.append({
            "owner_key": key, "table": _table(scope["path"]), "row_index": scope["row"],
            "source_pointer": scope["pointer"], "locale": item["locale"],
            "resolution_status": item["status"], "raw_text": None if chosen is None else chosen["raw_text"],
            "normalized_text": None if chosen is None else normalize_text(chosen["raw_text"]),
            "raw_checksum": None if chosen is None else sha256_text(chosen["raw_text"]),
            "normalized_checksum": None if chosen is None else sha256_text(normalize_text(chosen["raw_text"])),
        })
        for candidate in item["candidates"]:
            raw_text = candidate["raw_text"]
            text_candidates.append({
                "owner_key": key, "locale": item["locale"], "shard_kind": candidate["shard"],
                "source_relative_path": candidate["source_path"], "source_pointer": candidate["source_pointer"],
                "raw_text": raw_text, "normalized_text": normalize_text(raw_text),
                "raw_checksum": sha256_text(raw_text),
                "normalized_checksum": sha256_text(normalize_text(raw_text)),
            })
            source_records.setdefault((candidate["source_path"], candidate["source_pointer"]), {
                "relative_path": candidate["source_path"], "source_pointer": candidate["source_pointer"],
                "record_kind": "quest_codex_text_candidate", "native_id": item["hash"],
                "raw_checksum": sha256_text(raw_text), "raw_value": raw_text,
            })

    asset_report = reports["phase4f1-asset-opaque-audit.json"]
    assets = [{
        "table": _table(item["scope"]["path"]), "row_index": item["scope"]["row"],
        "source_pointer": item["scope"]["pointer"], "field_name": item["scope"]["field"],
        "asset_kind": item["asset_role"],
        "raw_reference": str(item["raw_value"]) if item["status"] == "observed" else None,
        "reference_status": item["status"],
    } for item in asset_report["asset_occurrences"]]
    opaque: list[dict[str, Any]] = []
    for item in asset_report["opaque_field_occurrences"]:
        scope = item["scope"]
        opaque.append({
            "table": _table(scope["path"]), "row_index": scope["row"],
            "source_pointer": scope["pointer"], "field_name": scope["field"],
            "array_index": None, "raw_value": item["raw_value"],
            "resolution_status": "opaque_unresolved",
        })
    for item in asset_report["opaque_array_positions"]:
        scope = item["scope"]
        opaque.append({
            "table": _table(scope["path"]), "row_index": scope["row"],
            "source_pointer": scope["pointer"], "field_name": scope["field"],
            "array_index": scope["array_index"], "raw_value": item["raw_value"],
            "resolution_status": "opaque_unresolved",
        })

    collections = {
        "rows": rows, "raw_fields": raw_fields, "relationships": relationships,
        "relationship_candidates": relationship_candidates, "opaque": opaque,
        "text_owners": owners, "text_resolutions": resolutions,
        "text_candidates": text_candidates, "assets": assets,
        "source_records": sorted(source_records.values(), key=lambda x: (x["relative_path"], x["source_pointer"])),
    }
    counts = {key: len(value) for key, value in collections.items()} | {"files": len(files)}
    status_counts = {
        "row_kinds": dict(sorted(Counter(x["evidence_kind"] for x in rows).items())),
        "field_presence": dict(sorted(Counter(x["field_presence"] for x in raw_fields).items())),
        "relationships": dict(sorted(Counter(x["resolution_status"] for x in relationships).items())),
        "opaque": dict(sorted(Counter(x["resolution_status"] for x in opaque).items())),
        "text_resolutions": dict(sorted(Counter(x["resolution_status"] for x in resolutions).items())),
        "assets": dict(sorted(Counter(x["reference_status"] for x in assets).items())),
    }
    digests = {key: _digest(value) for key, value in collections.items() if key != "source_records"}
    result = {"files": files, **collections, "counts": counts, "status_counts": status_counts, "occurrence_set_digests": digests}
    if manifest is not None:
        expected = {key: result[key] for key in ("counts", "status_counts", "occurrence_set_digests")}
        selected_rows = {
            "quest_codex_entry": [x["row_index"] for x in rows if x["evidence_kind"] == "quest_codex_entry"],
            "chapter_support": [x["row_index"] for x in rows if x["evidence_kind"] == "chapter_support"],
        }
        if manifest["files"] != files or manifest["selected_rows"] != selected_rows or manifest["expected"] != expected:
            _fail("Phase 4F3 derived contract differs from sealed manifest")
    return result


def expected_identity(manifest: Mapping[str, Any]) -> dict[str, str]:
    parser_build_id = digest_id("pb-quest-codex-phase4f3", canonical_json({
        "versions": manifest["versions"], "manifest_id": manifest["manifest_id"],
    }))
    config_checksum = sha256_text(canonical_json({
        "manifest_id": manifest["manifest_id"], "versions": manifest["versions"],
        "selected_rows": manifest["selected_rows"], "audit_binding": manifest["audit_binding"],
    }))
    ingest_run_id = digest_id(
        "ir-quest-codex-phase4f3", EXPECTED_SNAPSHOT_ID, parser_build_id, config_checksum
    )
    return {
        "snapshot_id": EXPECTED_SNAPSHOT_ID, "parser_build_id": parser_build_id,
        "config_checksum": config_checksum, "ingest_run_id": ingest_run_id,
    }


def occurrence_id(kind: str, identity: Mapping[str, str], *parts: Any) -> str:
    return digest_id(
        f"quest-codex-{kind}", identity["snapshot_id"], identity["parser_build_id"],
        identity["ingest_run_id"], *parts,
    )
