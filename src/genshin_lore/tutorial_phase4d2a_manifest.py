"""Sealed manifest contract for the Phase 4D2A Tutorial canary."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


EXPECTED_MANIFEST_ID = "anime-game-data-7.0-phase4d2a-core-tutorial-canary"
EXPECTED_COMMIT = "26df1dfbdf05a82bbb1d97506859f3e1c40718d8"
EXPECTED_TREE = "d891ef1d95fdd6c27a1da8a32ea7a61b483647a6"
EXPECTED_BUILD = "CNRELWin7.0.0_R47482070_S47579390_D47579390"
EXPECTED_OBSERVED_AT = "2026-08-19T00:00:00+08:00"
EXPECTED_SOURCE_ID = "anime-game-data"
EXPECTED_RELEASE = "7.0"
EXPECTED_SNAPSHOT_ID = f"{EXPECTED_SOURCE_ID}:{EXPECTED_RELEASE}:{EXPECTED_COMMIT}"
EXPECTED_AUDIT_RUN_ID = f"phase4d1-core-tutorial-audit:{EXPECTED_COMMIT}"

# Set by the release step once the generated manifest is frozen.  An empty
# value is useful to the manifest generator itself; runtime loading always
# runs with the committed digest.
EXPECTED_MANIFEST_SHA256 = "76a3e48f352bd277dcd18ac5f2a9da6e74c8cbc57c565aaf5fdc001badbdf544"

EXPECTED_VERSIONS = {
    "manifest_contract": "tutorial-phase4d2a-canary-manifest-v1",
    "parser": "tutorial-phase4d2a-canary-parser-v1",
    "projection": "tutorial-phase4d2a-family-projection-v1",
    "relationship_rule": "tutorial-detail-and-push-tips-candidate-v1",
    "textmap_resolution_rule": "owner-locale-main-medium-occurrence-v1",
    "image_reference_rule": "image-name-indexed-raw-occurrence-v1",
    "identity_rule": "tutorial-family-raw-occurrence-v1",
}

EXPECTED_TABLES = {
    "TutorialExcelConfigData": ("id", "primary"),
    "TutorialDetailExcelConfigData": ("id", "primary"),
    "TutorialCatalogExcelConfigData": ("id", "primary"),
    "PushTipsCodexExcelConfigData": ("id", "lookup"),
}
EXPECTED_PRIMARY_TABLES = tuple(EXPECTED_TABLES)[:3]
EXPECTED_LOOKUP_TABLE = "PushTipsCodexExcelConfigData"
LOCALES = ("CHS", "EN", "JP")
TEXTMAP_PATHS = {
    locale: {
        "main": f"TextMap/TextMap{locale}.json",
        "medium": f"TextMap/TextMap_Medium{locale}.json",
    }
    for locale in LOCALES
}
TABLE_PATHS = {
    table: f"ExcelBinOutput/{table}.json" for table in EXPECTED_TABLES
}
TEXT_OWNER_FIELDS = {
    "TutorialDetailExcelConfigData": (("descriptTextMapHash", "description"),),
    "TutorialCatalogExcelConfigData": (("titleTextMapHash", "title"),),
}
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class TutorialPhase4D2AManifestError(RuntimeError):
    """The sealed Tutorial canary manifest is missing or malformed."""


@dataclass(frozen=True)
class TutorialPhase4D2AFileSpec:
    relative_path: str
    format: str
    role: str
    source_records: int
    selected_records: int
    size_bytes: int
    sha256: str
    blob_oid: str


@dataclass(frozen=True)
class TutorialPhase4D2ARowSpec:
    table: str
    row_index: int
    pointer: str
    native_locator_field: str
    native_locator_value: str
    raw_sha256: str


@dataclass(frozen=True)
class TutorialPhase4D2AManifest:
    path: Path
    raw_sha256: str
    manifest_id: str
    source: dict[str, Any]
    versions: dict[str, str]
    audit_binding: dict[str, Any]
    boundary: dict[str, Any]
    identity: dict[str, Any]
    files: tuple[TutorialPhase4D2AFileSpec, ...]
    selected_rows: tuple[TutorialPhase4D2ARowSpec, ...]
    expected: dict[str, Any]
    raw: dict[str, Any]


def _fail(message: str) -> None:
    raise TutorialPhase4D2AManifestError(message)


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
                raise TutorialPhase4D2AManifestError(f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot parse {label}: {exc}")
    if not isinstance(value, dict):
        _fail(f"{label} must be an object")
    return value


def load_tutorial_phase4d2a_manifest(path: str | Path) -> TutorialPhase4D2AManifest:
    manifest_path = Path(path).resolve()
    try:
        raw_bytes = manifest_path.read_bytes()
    except OSError as exc:
        _fail(f"cannot read Tutorial Phase 4D2A manifest: {exc}")
    raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if EXPECTED_MANIFEST_SHA256 and raw_sha256 != EXPECTED_MANIFEST_SHA256:
        _fail("Tutorial Phase 4D2A manifest SHA-256 mismatch")
    raw = _strict_json(raw_bytes, "Tutorial Phase 4D2A manifest")
    root = _exact(
        raw,
        {
            "schema_version", "manifest_id", "source", "versions",
            "phase4d1_audit_binding", "boundary", "identity", "files",
            "selected_rows", "candidate_scans", "expected",
        },
        "manifest",
    )
    if root["schema_version"] != 1 or root["manifest_id"] != EXPECTED_MANIFEST_ID:
        _fail("unsupported Tutorial Phase 4D2A manifest identity")
    source = _exact(
        root["source"],
        {"source_id", "upstream", "release_label", "build", "commit", "tree", "observed_at", "snapshot_id"},
        "source",
    )
    expected_source = {
        "source_id": EXPECTED_SOURCE_ID,
        "upstream": "https://github.com/DimbreathBot/AnimeGameData",
        "release_label": EXPECTED_RELEASE,
        "build": EXPECTED_BUILD,
        "commit": EXPECTED_COMMIT,
        "tree": EXPECTED_TREE,
        "observed_at": EXPECTED_OBSERVED_AT,
        "snapshot_id": EXPECTED_SNAPSHOT_ID,
    }
    if source != expected_source:
        _fail("Tutorial Phase 4D2A source pin mismatch")
    versions = _exact(root["versions"], set(EXPECTED_VERSIONS), "versions")
    if versions != EXPECTED_VERSIONS:
        _fail("Tutorial Phase 4D2A parser/projection version mismatch")
    binding = _exact(root["phase4d1_audit_binding"], {"audit_version", "manifest", "artifacts"}, "phase4d1_audit_binding")
    if binding.get("audit_version") != "4D1.1":
        _fail("Tutorial Phase 4D1 audit version mismatch")
    manifest_binding = _exact(binding["manifest"], {"relative_path", "sha256"}, "phase4d1_audit_binding.manifest")
    if manifest_binding["relative_path"] != "reports/phase4d1-tutorial/phase4d1-audit-manifest.json":
        _fail("Tutorial Phase 4D1 audit manifest path mismatch")
    _hex(manifest_binding["sha256"], "phase4d1 audit manifest sha256")
    artifacts = binding["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != 6:
        _fail("Tutorial Phase 4D1 artifact binding count mismatch")
    for index, item in enumerate(artifacts):
        entry = _exact(item, {"relative_path", "sha256"}, f"phase4d1_audit_binding.artifacts[{index}]")
        _path(entry["relative_path"], f"phase4d1 artifact[{index}] path")
        _hex(entry["sha256"], f"phase4d1 artifact[{index}] sha256")
    boundary = _exact(
        root["boundary"],
        {"locales", "selected_primary_rows", "selected_lookup_rows", "candidate_scans", "full_core_tutorial_scan", "support_scope"},
        "boundary",
    )
    if boundary["locales"] != list(LOCALES) or boundary["full_core_tutorial_scan"] is not False:
        _fail("Tutorial Phase 4D2A boundary mismatch")
    if not isinstance(boundary["support_scope"], str) or not boundary["support_scope"]:
        _fail("Tutorial Phase 4D2A support scope is invalid")
    primary_selection = boundary["selected_primary_rows"]
    if not isinstance(primary_selection, dict) or set(primary_selection) != set(EXPECTED_PRIMARY_TABLES):
        _fail("Tutorial Phase 4D2A primary selection table keys mismatch")
    lookup_selection = boundary["selected_lookup_rows"]
    if not isinstance(lookup_selection, list):
        _fail("Tutorial Phase 4D2A lookup selection must be a list")
    for table in (*EXPECTED_PRIMARY_TABLES, EXPECTED_LOOKUP_TABLE):
        rows = primary_selection[table] if table in EXPECTED_PRIMARY_TABLES else lookup_selection
        if not isinstance(rows, list) or not rows:
            _fail(f"Tutorial Phase 4D2A selected rows missing for {table}")
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
        "context_scope": "single_physical_tutorial_row_container_only",
    }
    if identity != expected_identity:
        _fail("Tutorial Phase 4D2A identity boundary mismatch")

    raw_files = root["files"]
    if not isinstance(raw_files, list) or len(raw_files) != 10:
        _fail("Tutorial Phase 4D2A selected file count mismatch")
    files: list[TutorialPhase4D2AFileSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_files):
        entry = _exact(item, {"relative_path", "format", "role", "source_records", "selected_records", "size_bytes", "sha256", "blob_oid"}, f"files[{index}]")
        relative = _path(entry["relative_path"], f"files[{index}].relative_path")
        if relative in seen:
            _fail(f"duplicate Tutorial source file: {relative}")
        seen.add(relative)
        if entry["format"] != "json" or not isinstance(entry["source_records"], int) or entry["source_records"] <= 0 or not isinstance(entry["selected_records"], int) or entry["selected_records"] <= 0 or not isinstance(entry["size_bytes"], int) or entry["size_bytes"] <= 0:
            _fail(f"Tutorial source file counts invalid: {relative}")
        _hex(entry["sha256"], f"files[{index}].sha256")
        _hex(entry["blob_oid"], f"files[{index}].blob_oid", HEX40)
        if entry["role"] not in {"primary_table", "lookup_inventory", "explicit_textmap_candidate_shard"}:
            _fail(f"Tutorial source file role invalid: {relative}")
        files.append(TutorialPhase4D2AFileSpec(relative, entry["format"], entry["role"], entry["source_records"], entry["selected_records"], entry["size_bytes"], entry["sha256"], entry["blob_oid"]))
    expected_paths = sorted((*TABLE_PATHS.values(), *(path for item in TEXTMAP_PATHS.values() for path in item.values())))
    if [item.relative_path for item in files] != expected_paths:
        _fail("Tutorial selected source paths mismatch")

    # ``selected_rows`` is the top-level identity/config input consumed by
    # expected_identity(); it must be exactly the same ordered rows as the
    # boundary selections, not a second independently mutable boundary.
    boundary_selected_rows: list[dict[str, Any]] = []
    for table in EXPECTED_TABLES:
        selected = primary_selection[table] if table in EXPECTED_PRIMARY_TABLES else lookup_selection
        boundary_selected_rows.extend(dict(item) for item in selected)
    if not isinstance(root["selected_rows"], list) or root["selected_rows"] != boundary_selected_rows:
        _fail("Tutorial selected_rows and boundary selections disagree")

    rows: list[TutorialPhase4D2ARowSpec] = []
    for table in EXPECTED_TABLES:
        raw_rows = boundary["selected_primary_rows" if table in EXPECTED_PRIMARY_TABLES else "selected_lookup_rows"]
        if table in EXPECTED_PRIMARY_TABLES and isinstance(raw_rows, dict):
            raw_rows = raw_rows.get(table)
        for index, item in enumerate(raw_rows):
            entry = _exact(item, {"row_index", "pointer", "native_locator_field", "native_locator_value", "raw_sha256"}, f"{table}.selected_rows[{index}]")
            if not isinstance(entry["row_index"], int) or entry["row_index"] < 0 or entry["pointer"] != f"/{entry['row_index']}":
                _fail(f"{table} selected row coordinate mismatch")
            if entry["native_locator_field"] != EXPECTED_TABLES[table][0] or not isinstance(entry["native_locator_value"], str) or not entry["native_locator_value"].isdigit():
                _fail(f"{table} native locator mismatch")
            _hex(entry["raw_sha256"], f"{table} row checksum")
            rows.append(TutorialPhase4D2ARowSpec(table, entry["row_index"], entry["pointer"], entry["native_locator_field"], entry["native_locator_value"], entry["raw_sha256"]))
    scans = _exact(root["candidate_scans"], set(EXPECTED_TABLES), "candidate_scans")
    if boundary["candidate_scans"] != root["candidate_scans"]:
        _fail("Tutorial boundary and top-level candidate scans disagree")
    for table, (field, role) in EXPECTED_TABLES.items():
        entry = _exact(scans[table], {"relative_path", "native_locator_field", "source_records", "native_locator_digest"}, f"candidate_scans.{table}")
        if entry["relative_path"] != TABLE_PATHS[table] or entry["native_locator_field"] != field or entry["source_records"] <= 0:
            _fail(f"{table} candidate scan mismatch")
        _hex(entry["native_locator_digest"], f"{table} candidate scan digest")
    expected = root["expected"]
    if not isinstance(expected, dict) or set(expected) != {"counts", "status_counts", "occurrence_set_digests", "selected_table_counts", "text_owner_field_counts"}:
        _fail("Tutorial Phase 4D2A expected contract keys mismatch")
    if not isinstance(expected["counts"], dict) or not isinstance(expected["status_counts"], dict) or not isinstance(expected["occurrence_set_digests"], dict):
        _fail("Tutorial Phase 4D2A expected contract shape mismatch")
    for name, value in expected["occurrence_set_digests"].items():
        _hex(value, f"occurrence_set_digests.{name}")
    return TutorialPhase4D2AManifest(
        manifest_path,
        raw_sha256,
        EXPECTED_MANIFEST_ID,
        source,
        versions,
        binding,
        boundary,
        identity,
        tuple(files),
        tuple(rows),
        expected,
        raw,
    )


__all__ = [
    "EXPECTED_AUDIT_RUN_ID",
    "EXPECTED_BUILD",
    "EXPECTED_COMMIT",
    "EXPECTED_MANIFEST_ID",
    "EXPECTED_MANIFEST_SHA256",
    "EXPECTED_OBSERVED_AT",
    "EXPECTED_RELEASE",
    "EXPECTED_SNAPSHOT_ID",
    "EXPECTED_SOURCE_ID",
    "EXPECTED_TABLES",
    "EXPECTED_TREE",
    "EXPECTED_VERSIONS",
    "LOCALES",
    "TABLE_PATHS",
    "TEXTMAP_PATHS",
    "TEXT_OWNER_FIELDS",
    "TutorialPhase4D2AFileSpec",
    "TutorialPhase4D2AManifest",
    "TutorialPhase4D2AManifestError",
    "TutorialPhase4D2ARowSpec",
    "load_tutorial_phase4d2a_manifest",
]
