"""Sealed full-boundary manifest for the Phase 4D2B Tutorial release."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .tutorial_phase4d2a_manifest import (
    EXPECTED_BUILD,
    EXPECTED_COMMIT,
    EXPECTED_RELEASE,
    EXPECTED_SNAPSHOT_ID,
    EXPECTED_SOURCE_ID,
    EXPECTED_TABLES,
    EXPECTED_TREE,
    LOCALES,
    TABLE_PATHS,
    TEXTMAP_PATHS,
)

EXPECTED_MANIFEST_ID = "anime-game-data-7.0-phase4d2b-core-tutorial-full-boundary"
EXPECTED_MANIFEST_SHA256 = "ba969cadb7d3613a4acb0b6833a119e9693bedb4e599fbe01fcc123eb6c7c297"
EXPECTED_OBSERVED_AT = "2026-08-19T00:00:00+08:00"
EXPECTED_AUDIT_RUN_ID = f"phase4d1-core-tutorial-audit:{EXPECTED_COMMIT}"
EXPECTED_COVERAGE = "declared_core_tutorial_source_closure_complete"
EXPECTED_CORPUS_COMPLETE = True
EXPECTED_PUSH_TIPS_CORPUS_COMPLETE = False
EXPECTED_VERSIONS = {
    "manifest_contract": "tutorial-phase4d2b-full-boundary-manifest-v1",
    "parser": "tutorial-phase4d2b-full-boundary-parser-v1",
    "projection": "tutorial-phase4d2b-full-boundary-projection-v1",
    "relationship_rule": "tutorial-detail-and-push-tips-full-boundary-v1",
    "textmap_resolution_rule": "owner-locale-main-medium-full-boundary-v1",
    "image_reference_rule": "image-name-indexed-full-boundary-v1",
    "identity_rule": "tutorial-family-raw-occurrence-v1",
}
EXPECTED_COUNTS = {
    "selected_files": 10,
    "source_records": 16799,
    "rows": 6648,
    "primary_rows": 5866,
    "lookup_rows": 782,
    "raw_fields": 31251,
    "detail_relations": 4385,
    "push_tips_relations": 12,
    "relations": 4397,
    "text_owners": 3614,
    "text_resolutions": 10842,
    "text_candidates": 10151,
    "image_references": 3594,
}
EXPECTED_MISSING_FIELDS = {
    "TutorialExcelConfigData.detailIdList": 6,
    "TutorialDetailExcelConfigData.imageNameList": 8,
}
EXPECTED_IMAGE_REFERENCE_STATS = {
    "total_occurrences": 3594,
    "distinct_names": 3382,
    "duplicate_groups": 174,
    "excess_occurrences": 212,
}
EXPECTED_STATUS_COUNTS = {
    "detail_relations": {"dangling": 40, "resolved_unique": 4345},
    "push_tips_relations": {"dangling": 12},
    "text_resolutions": {
        "unique_main": 21,
        "unique_medium": 10130,
        "both_equal": 0,
        "both_conflict": 0,
        "missing": 691,
        "invalid": 0,
    },
    "image_references": {"observed": 3594},
    "raw_fields": {"array": 6619, "array_item": 9945, "scalar": 14687},
}
EXPECTED_SELECTED_TABLE_COUNTS = {
    "TutorialExcelConfigData": 2252,
    "TutorialDetailExcelConfigData": 3602,
    "TutorialCatalogExcelConfigData": 12,
    "PushTipsCodexExcelConfigData": 782,
}
EXPECTED_TEXT_OWNER_FIELD_COUNTS = {
    "TutorialCatalogExcelConfigData.titleTextMapHash": 12,
    "TutorialDetailExcelConfigData.descriptTextMapHash": 3602,
}
EXPECTED_ARTIFACTS = {
    "phase4d1-image-reference-audit.json": "372922eb7f88a14aad963027b872efbd0954f561e1f552a791c30efe8187b4ad",
    "phase4d1-reconciliation.json": "6d547fb526d58ea8a965109bf012a2eb039a33c9fc54404b65f8ad3b3e4f8fb2",
    "phase4d1-relationship-audit.json": "396afac0bfa8ccdde67e23ed842b7787443eb0c51258d74a84d1ab202f5d7616",
    "phase4d1-row-identity-audit.json": "78309654fe3aa262a067aee5d6ac3b928ea03cb44c79ba6be5f74b6634c4d490",
    "phase4d1-source-inventory.json": "80e6446798154fef7bc86aef8a7e00945b4cb65b3aa3618aa2305e27259dff41",
    "phase4d1-text-owner-audit.json": "5755e9f19f6118f8c7d18cda0161dc4f7701e45bc1bb69de6cc9641eeae8b509",
}
EXPECTED_AUDIT_MANIFEST_SHA256 = "4dd922a5ea2285af00a50d86ca00d520937c6e9a9337aaf127641109bc12c869"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class TutorialPhase4D2BManifestError(RuntimeError):
    """The sealed full-boundary Tutorial manifest is missing or malformed."""


@dataclass(frozen=True)
class TutorialPhase4D2BFileSpec:
    relative_path: str
    format: str
    role: str
    source_records: int
    selected_records: int
    size_bytes: int
    sha256: str
    blob_oid: str


@dataclass(frozen=True)
class TutorialPhase4D2BRowSpec:
    table: str
    row_index: int
    pointer: str
    native_locator_field: str
    native_locator_value: str
    raw_sha256: str


@dataclass(frozen=True)
class TutorialPhase4D2BManifest:
    path: Path
    raw_sha256: str
    manifest_id: str
    source: dict[str, Any]
    versions: dict[str, str]
    audit_binding: dict[str, Any]
    boundary: dict[str, Any]
    identity: dict[str, Any]
    files: tuple[TutorialPhase4D2BFileSpec, ...]
    selected_rows: tuple[TutorialPhase4D2BRowSpec, ...]
    expected: dict[str, Any]
    raw: dict[str, Any]


def _fail(message: str) -> None:
    raise TutorialPhase4D2BManifestError(message)


def _exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        _fail(f"{label} keys mismatch")
    return value


def _path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        _fail(f"{label} must be a POSIX relative path")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or parsed.as_posix() != value or any(part in {"", ".", ".."} for part in parsed.parts):
        _fail(f"{label} is not canonical")
    return value


def _hex(value: Any, label: str, pattern: re.Pattern[str] = HEX64) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _fail(f"{label} must be lowercase hexadecimal")
    return value


def _strict_json(raw: bytes, label: str) -> dict[str, Any]:
    def reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise TutorialPhase4D2BManifestError(f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot parse {label}: {exc}")
    if not isinstance(value, dict):
        _fail(f"{label} must be an object")
    return value


def _source() -> dict[str, Any]:
    return {
        "source_id": EXPECTED_SOURCE_ID,
        "upstream": "https://github.com/DimbreathBot/AnimeGameData",
        "release_label": EXPECTED_RELEASE,
        "build": EXPECTED_BUILD,
        "commit": EXPECTED_COMMIT,
        "tree": EXPECTED_TREE,
        "observed_at": EXPECTED_OBSERVED_AT,
        "snapshot_id": EXPECTED_SNAPSHOT_ID,
    }


def _audit_binding(value: Any) -> dict[str, Any]:
    binding = _exact(value, {"audit_version", "manifest", "artifacts"}, "phase4d1_audit_binding")
    if binding["audit_version"] != "4D1.1":
        _fail("Tutorial Phase 4D1 audit version mismatch")
    manifest = _exact(binding["manifest"], {"relative_path", "sha256"}, "phase4d1_audit_binding.manifest")
    if manifest != {
        "relative_path": "reports/phase4d1-tutorial/phase4d1-audit-manifest.json",
        "sha256": EXPECTED_AUDIT_MANIFEST_SHA256,
    }:
        _fail("Tutorial Phase 4D1 audit manifest binding mismatch")
    artifacts = binding["artifacts"]
    expected = {
        f"reports/phase4d1-tutorial/{name}": checksum
        for name, checksum in EXPECTED_ARTIFACTS.items()
    }
    if not isinstance(artifacts, list) or len(artifacts) != len(expected):
        _fail("Tutorial Phase 4D1 artifact binding count mismatch")
    actual: dict[str, str] = {}
    for index, item in enumerate(artifacts):
        entry = _exact(item, {"relative_path", "sha256"}, f"phase4d1_audit_binding.artifacts[{index}]")
        relative = _path(entry["relative_path"], f"phase4d1 artifact[{index}] path")
        _hex(entry["sha256"], f"phase4d1 artifact[{index}] sha256")
        actual[relative] = entry["sha256"]
    if actual != expected:
        _fail("Tutorial Phase 4D1 artifact binding mismatch")
    return binding


def load_tutorial_phase4d2b_manifest(path: str | Path) -> TutorialPhase4D2BManifest:
    manifest_path = Path(path).resolve()
    try:
        raw_bytes = manifest_path.read_bytes()
    except OSError as exc:
        _fail(f"cannot read Tutorial Phase 4D2B manifest: {exc}")
    raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if EXPECTED_MANIFEST_SHA256 and raw_sha256 != EXPECTED_MANIFEST_SHA256:
        _fail("Tutorial Phase 4D2B manifest SHA-256 mismatch")
    raw = _strict_json(raw_bytes, "Tutorial Phase 4D2B manifest")
    root = _exact(
        raw,
        {"schema_version", "manifest_id", "source", "versions", "phase4d1_audit_binding", "boundary", "identity", "files", "selected_rows", "candidate_scans", "expected"},
        "manifest",
    )
    if root["schema_version"] != 1 or root["manifest_id"] != EXPECTED_MANIFEST_ID:
        _fail("unsupported Tutorial Phase 4D2B manifest identity")
    if root["source"] != _source():
        _fail("Tutorial Phase 4D2B source pin mismatch")
    versions = _exact(root["versions"], set(EXPECTED_VERSIONS), "versions")
    if versions != EXPECTED_VERSIONS:
        _fail("Tutorial Phase 4D2B parser/projection version mismatch")
    binding = _audit_binding(root["phase4d1_audit_binding"])
    boundary = _exact(
        root["boundary"],
        {"locales", "coverage", "corpus_complete", "completeness_claim", "push_tips_corpus_complete", "selected_primary_rows", "selected_lookup_rows", "candidate_scans", "full_core_tutorial_scan", "support_scope"},
        "boundary",
    )
    if (
        boundary["locales"] != list(LOCALES)
        or boundary["coverage"] != EXPECTED_COVERAGE
        or boundary["corpus_complete"] is not EXPECTED_CORPUS_COMPLETE
        or boundary["completeness_claim"] != EXPECTED_COVERAGE
        or boundary["push_tips_corpus_complete"] is not EXPECTED_PUSH_TIPS_CORPUS_COMPLETE
        or boundary["full_core_tutorial_scan"] is not True
        or not isinstance(boundary["support_scope"], str)
        or not boundary["support_scope"]
    ):
        _fail("Tutorial Phase 4D2B boundary/completeness mismatch")
    primary = boundary["selected_primary_rows"]
    if not isinstance(primary, dict) or set(primary) != set(EXPECTED_TABLES) - {"PushTipsCodexExcelConfigData"}:
        _fail("Tutorial Phase 4D2B primary selection keys mismatch")
    lookup = boundary["selected_lookup_rows"]
    if not isinstance(lookup, list) or len(lookup) != EXPECTED_SELECTED_TABLE_COUNTS["PushTipsCodexExcelConfigData"]:
        _fail("Tutorial Phase 4D2B lookup selection mismatch")
    for table in EXPECTED_TABLES:
        rows = lookup if table == "PushTipsCodexExcelConfigData" else primary[table]
        if not isinstance(rows, list) or len(rows) != EXPECTED_SELECTED_TABLE_COUNTS[table]:
            _fail(f"Tutorial Phase 4D2B selected row count mismatch: {table}")
    identity = _exact(
        root["identity"],
        {"raw_evidence_identity", "logical_identity_created", "native_id_scope", "textmap_hash_scope", "image_name_scope", "push_tips_scope", "checksum_semantics", "context_scope"},
        "identity",
    )
    expected_identity = {
        "raw_evidence_identity": "snapshot_parser_build_ingest_run_source_file_source_record_row_field_array_occurrence",
        "logical_identity_created": False,
        "native_id_scope": "family_table_scoped_candidate_locator",
        "textmap_hash_scope": "owner_locale_shard_candidate_locator",
        "image_name_scope": "indexed_raw_asset_reference_no_identity",
        "push_tips_scope": "lookup_candidate_only_no_tutorial_merge",
        "checksum_semantics": "change_detection_only",
        "context_scope": "full_core_tutorial_source_closure",
    }
    if identity != expected_identity:
        _fail("Tutorial Phase 4D2B identity boundary mismatch")

    raw_files = root["files"]
    if not isinstance(raw_files, list) or len(raw_files) != EXPECTED_COUNTS["selected_files"]:
        _fail("Tutorial Phase 4D2B selected file count mismatch")
    files: list[TutorialPhase4D2BFileSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_files):
        entry = _exact(item, {"relative_path", "format", "role", "source_records", "selected_records", "size_bytes", "sha256", "blob_oid"}, f"files[{index}]")
        relative = _path(entry["relative_path"], f"files[{index}].relative_path")
        if relative in seen:
            _fail(f"duplicate Tutorial source file: {relative}")
        seen.add(relative)
        if entry["format"] != "json" or not isinstance(entry["source_records"], int) or entry["source_records"] <= 0 or not isinstance(entry["selected_records"], int) or entry["selected_records"] <= 0 or not isinstance(entry["size_bytes"], int) or entry["size_bytes"] <= 0:
            _fail(f"Tutorial source file counts invalid: {relative}")
        if entry["role"] not in {"primary_table", "lookup_inventory", "explicit_textmap_candidate_shard"}:
            _fail(f"Tutorial source file role invalid: {relative}")
        _hex(entry["sha256"], f"files[{index}].sha256")
        _hex(entry["blob_oid"], f"files[{index}].blob_oid", HEX40)
        files.append(TutorialPhase4D2BFileSpec(relative, entry["format"], entry["role"], entry["source_records"], entry["selected_records"], entry["size_bytes"], entry["sha256"], entry["blob_oid"]))
    expected_paths = sorted((*TABLE_PATHS.values(), *(path for paths in TEXTMAP_PATHS.values() for path in paths.values())))
    if [item.relative_path for item in files] != expected_paths:
        _fail("Tutorial Phase 4D2B selected source paths mismatch")

    boundary_rows: list[dict[str, Any]] = []
    for table in EXPECTED_TABLES:
        rows = lookup if table == "PushTipsCodexExcelConfigData" else primary[table]
        boundary_rows.extend(dict(item) for item in rows)
    if not isinstance(root["selected_rows"], list) or root["selected_rows"] != boundary_rows:
        _fail("Tutorial Phase 4D2B top-level and boundary rows disagree")
    selected_rows: list[TutorialPhase4D2BRowSpec] = []
    for table in EXPECTED_TABLES:
        rows = lookup if table == "PushTipsCodexExcelConfigData" else primary[table]
        for index, item in enumerate(rows):
            entry = _exact(item, {"row_index", "pointer", "native_locator_field", "native_locator_value", "raw_sha256"}, f"{table}.selected_rows[{index}]")
            if not isinstance(entry["row_index"], int) or entry["row_index"] < 0 or entry["pointer"] != f"/{entry['row_index']}" or entry["row_index"] != index:
                _fail(f"{table} selected row coordinate mismatch")
            if entry["native_locator_field"] != EXPECTED_TABLES[table][0] or not isinstance(entry["native_locator_value"], str) or not entry["native_locator_value"].isdigit():
                _fail(f"{table} native locator mismatch")
            _hex(entry["raw_sha256"], f"{table} row checksum")
            selected_rows.append(TutorialPhase4D2BRowSpec(table, entry["row_index"], entry["pointer"], entry["native_locator_field"], entry["native_locator_value"], entry["raw_sha256"]))

    scans = _exact(root["candidate_scans"], set(EXPECTED_TABLES), "candidate_scans")
    if boundary["candidate_scans"] != root["candidate_scans"]:
        _fail("Tutorial Phase 4D2B top-level and boundary candidate scans disagree")
    for table, (field, _) in EXPECTED_TABLES.items():
        entry = _exact(scans[table], {"relative_path", "native_locator_field", "source_records", "native_locator_digest"}, f"candidate_scans.{table}")
        if entry["relative_path"] != TABLE_PATHS[table] or entry["native_locator_field"] != field or entry["source_records"] <= 0:
            _fail(f"Tutorial Phase 4D2B candidate scan mismatch: {table}")
        _hex(entry["native_locator_digest"], f"candidate_scans.{table}.native_locator_digest")

    expected = _exact(
        root["expected"],
        {"counts", "status_counts", "missing_fields", "occurrence_set_digests", "selected_table_counts", "text_owner_field_counts", "image_reference_stats", "coverage"},
        "expected",
    )
    if expected["coverage"] != EXPECTED_COVERAGE:
        _fail("Tutorial Phase 4D2B expected coverage mismatch")
    for key, wanted in (("counts", EXPECTED_COUNTS), ("status_counts", EXPECTED_STATUS_COUNTS), ("missing_fields", EXPECTED_MISSING_FIELDS), ("selected_table_counts", EXPECTED_SELECTED_TABLE_COUNTS), ("text_owner_field_counts", EXPECTED_TEXT_OWNER_FIELD_COUNTS), ("image_reference_stats", EXPECTED_IMAGE_REFERENCE_STATS)):
        if expected[key] != wanted:
            _fail(f"Tutorial Phase 4D2B expected {key} mismatch")
    if not isinstance(expected["occurrence_set_digests"], dict) or not expected["occurrence_set_digests"]:
        _fail("Tutorial Phase 4D2B occurrence digest contract missing")
    for name, digest in expected["occurrence_set_digests"].items():
        _hex(digest, f"occurrence_set_digests.{name}")
    return TutorialPhase4D2BManifest(
        manifest_path,
        raw_sha256,
        EXPECTED_MANIFEST_ID,
        root["source"],
        versions,
        binding,
        boundary,
        identity,
        tuple(files),
        tuple(selected_rows),
        expected,
        raw,
    )


__all__ = [
    "EXPECTED_AUDIT_MANIFEST_SHA256",
    "EXPECTED_AUDIT_RUN_ID",
    "EXPECTED_BUILD",
    "EXPECTED_COMMIT",
    "EXPECTED_CORPUS_COMPLETE",
    "EXPECTED_COUNTS",
    "EXPECTED_COVERAGE",
    "EXPECTED_MANIFEST_ID",
    "EXPECTED_MANIFEST_SHA256",
    "EXPECTED_MISSING_FIELDS",
    "EXPECTED_IMAGE_REFERENCE_STATS",
    "EXPECTED_OBSERVED_AT",
    "EXPECTED_PUSH_TIPS_CORPUS_COMPLETE",
    "EXPECTED_SELECTED_TABLE_COUNTS",
    "EXPECTED_SOURCE_ID",
    "EXPECTED_STATUS_COUNTS",
    "EXPECTED_TEXT_OWNER_FIELD_COUNTS",
    "EXPECTED_TREE",
    "EXPECTED_VERSIONS",
    "LOCALES",
    "TABLE_PATHS",
    "TEXTMAP_PATHS",
    "TutorialPhase4D2BFileSpec",
    "TutorialPhase4D2BManifest",
    "TutorialPhase4D2BManifestError",
    "TutorialPhase4D2BRowSpec",
    "load_tutorial_phase4d2b_manifest",
]
