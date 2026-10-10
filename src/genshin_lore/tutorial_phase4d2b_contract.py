"""Pure full-boundary source contract for the Phase 4D2B Tutorial release."""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .tutorial_phase4d2a_contract import (
    TutorialPhase4D2AContractError,
    derive_tutorial_phase4d2a_source_contract,
    occurrence_id,
    strict_json_file,
)
from .tutorial_phase4d2b_manifest import (
    EXPECTED_AUDIT_MANIFEST_SHA256,
    EXPECTED_COUNTS,
    EXPECTED_COVERAGE,
    EXPECTED_IMAGE_REFERENCE_STATS,
    EXPECTED_MANIFEST_ID,
    EXPECTED_MISSING_FIELDS,
    EXPECTED_STATUS_COUNTS,
    EXPECTED_SELECTED_TABLE_COUNTS,
    EXPECTED_TEXT_OWNER_FIELD_COUNTS,
    EXPECTED_VERSIONS,
    TutorialPhase4D2BManifest,
)
from .util import canonical_json, digest_id, sha256_text

RUN_FAMILY = "tutorial_phase4d2b_core_full_boundary"
PARSER_FINGERPRINT = "tutorial-phase4d2b-full-boundary-projection"

# These are source facts, rather than values accepted from the M2 manifest.
# The Phase4D1 source inventory is pinned, but a sealed manifest can still be
# malformed (or a test can deliberately replace its seal). Keep the exact
# path/role/container/count boundary here and re-measure each source file at
# contract time so file-level metadata cannot become tautological.
EXPECTED_FILE_SEMANTICS: dict[str, dict[str, Any]] = {
    "ExcelBinOutput/PushTipsCodexExcelConfigData.json": {
        "format": "json", "role": "lookup_inventory", "container": "array",
        "source_records": 782, "selected_records": 782,
    },
    "ExcelBinOutput/TutorialCatalogExcelConfigData.json": {
        "format": "json", "role": "primary_table", "container": "array",
        "source_records": 12, "selected_records": 12,
    },
    "ExcelBinOutput/TutorialDetailExcelConfigData.json": {
        "format": "json", "role": "primary_table", "container": "array",
        "source_records": 3602, "selected_records": 3602,
    },
    "ExcelBinOutput/TutorialExcelConfigData.json": {
        "format": "json", "role": "primary_table", "container": "array",
        "source_records": 2252, "selected_records": 2252,
    },
    "TextMap/TextMapCHS.json": {
        "format": "json", "role": "explicit_textmap_candidate_shard", "container": "object",
        "source_records": 605937, "selected_records": 7,
    },
    "TextMap/TextMapEN.json": {
        "format": "json", "role": "explicit_textmap_candidate_shard", "container": "object",
        "source_records": 585531, "selected_records": 7,
    },
    "TextMap/TextMapJP.json": {
        "format": "json", "role": "explicit_textmap_candidate_shard", "container": "object",
        "source_records": 585353, "selected_records": 7,
    },
    "TextMap/TextMap_MediumCHS.json": {
        "format": "json", "role": "explicit_textmap_candidate_shard", "container": "object",
        "source_records": 238998, "selected_records": 3416,
    },
    "TextMap/TextMap_MediumEN.json": {
        "format": "json", "role": "explicit_textmap_candidate_shard", "container": "object",
        "source_records": 229324, "selected_records": 3357,
    },
    "TextMap/TextMap_MediumJP.json": {
        "format": "json", "role": "explicit_textmap_candidate_shard", "container": "object",
        "source_records": 229382, "selected_records": 3357,
    },
}


class TutorialPhase4D2BContractError(RuntimeError):
    """The pinned source or M2 full-boundary contract is inconsistent."""


def _fail(message: str) -> None:
    raise TutorialPhase4D2BContractError(message)


def _manifest_value(manifest: Any) -> dict[str, Any]:
    value = getattr(manifest, "raw", manifest)
    if not isinstance(value, dict):
        _fail("Tutorial Phase 4D2B manifest value is not an object")
    return value


def verify_phase4d1_audit_binding(repo_root: str | Path, manifest: TutorialPhase4D2BManifest) -> None:
    """Recheck every accepted Phase4D1 artifact before a full-boundary write."""
    root = Path(repo_root).resolve()
    binding = manifest.audit_binding
    manifest_spec = binding["manifest"]
    manifest_path = root / manifest_spec["relative_path"]
    try:
        raw = manifest_path.read_bytes()
    except OSError as exc:
        _fail(f"cannot read bound Phase 4D1 audit manifest: {exc}")
    if hashlib.sha256(raw).hexdigest() != EXPECTED_AUDIT_MANIFEST_SHA256:
        _fail("bound Phase 4D1 audit manifest digest changed")
    for item in binding["artifacts"]:
        path = root / item["relative_path"]
        try:
            raw = path.read_bytes()
        except OSError as exc:
            _fail(f"cannot read bound Phase 4D1 artifact {item['relative_path']}: {exc}")
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            _fail(f"bound Phase 4D1 artifact digest changed: {item['relative_path']}")
        try:
            value = strict_json_file(path, f"Phase 4D1 artifact {item['relative_path']}")
        except TutorialPhase4D2AContractError as exc:
            _fail(str(exc))
        if not isinstance(value, dict) or value.get("audit_version") != "4D1.1":
            _fail(f"bound Phase 4D1 artifact version changed: {item['relative_path']}")


def _file_value(item: Any, field: str) -> Any:
    """Read a file spec from either the typed manifest or its raw mapping."""
    if isinstance(item, Mapping):
        return item.get(field)
    return getattr(item, field, None)


def verify_phase4d2b_file_semantics(
    source_root: str | Path,
    manifest: TutorialPhase4D2BManifest | Mapping[str, Any],
    derived: Mapping[str, Any],
) -> None:
    """Recompute source file roles and cardinalities against Phase4D1.

    M1's generic source loader intentionally carries file metadata from its
    manifest. M2 must close that trust gap: each JSON file is loaded again,
    its list/object length is measured, and its result is compared with the
    exact Phase4D1 inventory semantics and the M2 manifest.
    """
    root = Path(source_root).resolve()
    raw = _manifest_value(manifest)
    raw_files = raw.get("files")
    if not isinstance(raw_files, list):
        _fail("Tutorial Phase 4D2B source file list is missing")
    manifest_files: dict[str, Any] = {}
    for item in raw_files:
        relative = _file_value(item, "relative_path")
        if not isinstance(relative, str) or relative in manifest_files:
            _fail("Tutorial Phase 4D2B source file path set is not unique")
        manifest_files[relative] = item
    expected_paths = set(EXPECTED_FILE_SEMANTICS)
    if set(manifest_files) != expected_paths:
        _fail("Tutorial Phase 4D2B source file path set differs from accepted Phase4D1 inventory")

    inventory_path = Path(__file__).resolve().parents[2] / "reports/phase4d1-tutorial/phase4d1-source-inventory.json"
    try:
        inventory_value = strict_json_file(inventory_path, "Phase 4D1 source inventory")
    except TutorialPhase4D2AContractError as exc:
        _fail(str(exc))
    if not isinstance(inventory_value, dict) or inventory_value.get("audit_version") != "4D1.1":
        _fail("Phase 4D1 source inventory version changed")
    inventory_rows = inventory_value.get("files")
    if not isinstance(inventory_rows, list):
        _fail("Phase 4D1 source inventory file list is missing")
    inventory: dict[str, Any] = {}
    inventory_keys = {
        "blob_oid", "format", "relative_path", "role", "selected_records",
        "sha256", "size_bytes", "source_records", "strict_duplicate_key_policy",
    }
    for item in inventory_rows:
        if not isinstance(item, dict) or set(item) != inventory_keys:
            _fail("Phase 4D1 source inventory entry keys changed")
        relative = item.get("relative_path")
        if not isinstance(relative, str) or relative in inventory:
            _fail("Phase 4D1 source inventory paths are not unique")
        inventory[relative] = item
    if set(inventory) != expected_paths:
        _fail("Phase 4D1 source inventory path set changed")

    selected_by_file = derived.get("selected_records_by_file")
    if not isinstance(selected_by_file, Mapping):
        _fail("Tutorial Phase 4D2B selected file counts are unavailable")
    derived_files = {
        item.get("relative_path"): item
        for item in derived.get("files", [])
        if isinstance(item, Mapping)
    }
    for relative, wanted in EXPECTED_FILE_SEMANTICS.items():
        spec = manifest_files[relative]
        audit = inventory[relative]
        if audit.get("strict_duplicate_key_policy") != "reject":
            _fail(f"Phase 4D1 duplicate-key policy changed: {relative}")
        for field in ("format", "role", "source_records", "selected_records"):
            if _file_value(spec, field) != wanted[field] or audit.get(field) != wanted[field]:
                _fail(f"Tutorial Phase 4D2B file {field} mismatch: {relative}")
        if selected_by_file.get(relative) != wanted["selected_records"]:
            _fail(f"Tutorial Phase 4D2B selected source count mismatch: {relative}")
        derived_item = derived_files.get(relative)
        if not isinstance(derived_item, Mapping) or any(
            derived_item.get(field) != _file_value(spec, field)
            for field in ("format", "role", "source_records", "selected_records", "size_bytes", "sha256", "blob_oid")
        ):
            _fail(f"Tutorial Phase 4D2B derived file metadata mismatch: {relative}")

        try:
            value = strict_json_file(root / relative, relative)
        except TutorialPhase4D2AContractError as exc:
            _fail(str(exc))
        if wanted["container"] == "array":
            actual_records = len(value) if isinstance(value, list) else None
        else:
            actual_records = len(value) if isinstance(value, dict) else None
        if actual_records != wanted["source_records"]:
            _fail(f"Tutorial Phase 4D2B actual source record count mismatch: {relative}")
        if audit.get("source_records") != actual_records or _file_value(spec, "source_records") != actual_records:
            _fail(f"Tutorial Phase 4D2B manifest/source inventory record count mismatch: {relative}")


def derive_tutorial_phase4d2b_source_contract(
    source_root: str | Path,
    manifest: TutorialPhase4D2BManifest,
) -> dict[str, Any]:
    """Derive the full physical contract from source, independently of SQL."""
    raw = _manifest_value(manifest)
    boundary = raw.get("boundary")
    if not isinstance(boundary, dict) or boundary.get("full_core_tutorial_scan") is not True:
        _fail("Tutorial Phase 4D2B requires full core Tutorial boundary")
    try:
        derived = derive_tutorial_phase4d2a_source_contract(source_root, raw)
    except TutorialPhase4D2AContractError as exc:
        _fail(str(exc))
    verify_phase4d2b_file_semantics(source_root, manifest, derived)
    image_names = [
        item["raw_image_name"]
        for item in derived["image_references"]
        if isinstance(item.get("raw_image_name"), str)
    ]
    image_counts = Counter(image_names)
    derived = dict(derived)
    derived["image_reference_stats"] = {
        "total_occurrences": len(image_names),
        "distinct_names": len(image_counts),
        "duplicate_groups": sum(count > 1 for count in image_counts.values()),
        "excess_occurrences": sum(count - 1 for count in image_counts.values()),
    }
    manifest_files = {item.relative_path: item for item in manifest.files}
    for item in derived["files"]:
        spec = manifest_files.get(item["relative_path"])
        if spec is None or any(
            getattr(spec, field) != item[field]
            for field in ("format", "role", "source_records", "selected_records", "size_bytes", "sha256", "blob_oid")
        ):
            _fail(f"Tutorial Phase 4D2B file-level source/selection contract mismatch: {item['relative_path']}")
    expected = manifest.expected
    for key in (
        "counts", "status_counts", "missing_fields", "selected_table_counts",
        "text_owner_field_counts", "image_reference_stats", "occurrence_set_digests",
    ):
        if derived[key] != expected[key]:
            _fail(f"Tutorial Phase 4D2B {key} mismatch against sealed manifest")
    for key, wanted in (
        ("counts", EXPECTED_COUNTS),
        ("status_counts", EXPECTED_STATUS_COUNTS),
        ("missing_fields", EXPECTED_MISSING_FIELDS),
        ("selected_table_counts", EXPECTED_SELECTED_TABLE_COUNTS),
        ("text_owner_field_counts", EXPECTED_TEXT_OWNER_FIELD_COUNTS),
        ("image_reference_stats", EXPECTED_IMAGE_REFERENCE_STATS),
    ):
        if derived[key] != wanted:
            _fail(f"Tutorial Phase 4D2B {key} differs from accepted Phase4D1 boundary")
    if expected["coverage"] != EXPECTED_COVERAGE:
        _fail("Tutorial Phase 4D2B completeness claim mismatch")
    return derived


def expected_identity(manifest: TutorialPhase4D2BManifest) -> dict[str, str]:
    raw = _manifest_value(manifest)
    versions = raw.get("versions")
    manifest_id = raw.get("manifest_id")
    if versions != EXPECTED_VERSIONS or manifest_id != EXPECTED_MANIFEST_ID:
        _fail("Tutorial Phase 4D2B manifest versions/identity are missing")
    parser_build_id = digest_id(
        "pb-tutorial-phase4d2b",
        canonical_json({"versions": versions, "manifest_id": manifest_id}),
    )
    config_checksum = sha256_text(canonical_json({
        "manifest_id": manifest_id,
        "versions": versions,
        "coverage": EXPECTED_COVERAGE,
        "corpus_complete": True,
        "push_tips_corpus_complete": False,
        "selected_rows": raw.get("selected_rows", []),
        "candidate_scans": raw.get("candidate_scans", {}),
        "occurrence_set_digests": raw.get("expected", {}).get("occurrence_set_digests", {}),
    }))
    snapshot_id = str(raw["source"]["snapshot_id"])
    ingest_run_id = digest_id(
        "ir-tutorial-phase4d2b",
        snapshot_id,
        parser_build_id,
        config_checksum,
    )
    return {
        "snapshot_id": snapshot_id,
        "parser_build_id": parser_build_id,
        "config_checksum": config_checksum,
        "ingest_run_id": ingest_run_id,
    }


__all__ = [
    "PARSER_FINGERPRINT",
    "RUN_FAMILY",
    "TutorialPhase4D2BContractError",
    "EXPECTED_FILE_SEMANTICS",
    "derive_tutorial_phase4d2b_source_contract",
    "expected_identity",
    "occurrence_id",
    "verify_phase4d1_audit_binding",
    "verify_phase4d2b_file_semantics",
]
