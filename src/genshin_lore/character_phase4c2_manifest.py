from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


EXPECTED_MANIFEST_ID = "anime-game-data-7.0-phase4c2-avatar10000003-canary"
EXPECTED_COMMIT = "26df1dfbdf05a82bbb1d97506859f3e1c40718d8"
EXPECTED_TREE = "d891ef1d95fdd6c27a1da8a32ea7a61b483647a6"
EXPECTED_BUILD = "CNRELWin7.0.0_R47482070_S47579390_D47579390"
EXPECTED_AVATAR_ID = "10000003"
EXPECTED_VERSIONS = {
    "manifest_contract": "character-phase4c2-canary-manifest-v1",
    "parser": "character-phase4c2-canary-parser-v1",
    "projection": "character-phase4c2-family-projection-v1",
    "textmap_resolution_rule": "owner-locale-main-medium-occurrence-v1",
    "voice_chain_rule": "fetter-row-lut-items-container-child-v1",
    "identity_rule": "character-family-raw-occurrence-v1",
}
EXPECTED_AUDIT_BINDING = {
    "audit_version": "4C1.1",
    "manifest": {
        "relative_path": "reports/phase4c1-character/phase4c1-audit-manifest.json",
        "sha256": "3a357f45b7f97804d465dea2fc0ca7ecb3ed15811c19bf9772be2f0d7077d3f7",
    },
    "artifacts": [
        {
            "relative_path": "reports/phase4c1-character/phase4c1-row-identity-audit.json",
            "sha256": "6310e9e7a5c660a8eadd79016125c8e11404e6cd23f5eac0d9b13a7e4fee5d54",
        },
        {
            "relative_path": "reports/phase4c1-character/phase4c1-source-inventory.json",
            "sha256": "d32ff5cbdd219c581b65d274dce79a85937187f4f92cfd67e93031aebd33c0c6",
        },
        {
            "relative_path": "reports/phase4c1-character/phase4c1-text-owner-audit.json",
            "sha256": "32bfe31f226b167a39a99c6e8afb321722c3618b39cd0ae986cf32e0b3278e76",
        },
        {
            "relative_path": "reports/phase4c1-character/phase4c1-voice-chain-audit.json",
            "sha256": "7451370791f21b3fbb9cee37abf6af8f552ff6a995f760beb0a86c2e27c627f6",
        },
    ],
}
EXPECTED_TABLES = {
    "AvatarExcelConfigData": ("id", (2,)),
    "FetterInfoExcelConfigData": ("fetterId", (1,)),
    "FetterStoryExcelConfigData": ("fetterId", tuple(range(0, 8))),
    "FettersExcelConfigData": ("fetterId", tuple(range(579, 651))),
}
EXPECTED_COUNTS = {
    "selected_files": 15,
    "source_records": 739,
    "rows": 82,
    "raw_slots": 624,
    "condition_slots": 313,
    "condition_nonempty": 42,
    "condition_empty": 271,
    "opaque_slots": 311,
    "relations": 422,
    "avatar_relations": 81,
    "text_owner_occurrences": 269,
    "voice_attachments": 72,
    "text_resolutions": 807,
    "text_candidates": 513,
    "voice_containers": 72,
    "audio_variants": 7776,
    "distinct_audio_paths": 7770,
    "distinct_character_child_selectors": 122,
    "unattached_lut_entries": 0,
}
EXPECTED_STATUS_COUNTS = {
    "relations": {"resolved": 350, "resolved_unique_container": 72},
    "text_resolutions": {
        "unique_main": 0,
        "unique_medium": 513,
        "both_equal": 0,
        "both_conflict": 0,
        "missing": 294,
    },
    "raw_slots": {"observed": 313, "opaque_unresolved": 310, "observed_equal": 1},
    "condition_shapes": {"empty": 271, "nonempty": 42},
    "voice_containers": {"resolved_unique_container": 72},
    "audio_paths": {"observed": 7776},
    "character_child_selectors": {"observed_unresolved": 7776},
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class Phase4C2ManifestError(RuntimeError):
    pass


@dataclass(frozen=True)
class Phase4C2FileSpec:
    relative_path: str
    format: str
    size_bytes: int
    sha256: str
    blob_oid: str
    role: str


@dataclass(frozen=True)
class CharacterPhase4C2Manifest:
    path: Path
    raw_sha256: str
    manifest_id: str
    source: dict[str, Any]
    versions: dict[str, str]
    audit_binding: dict[str, Any]
    boundary: dict[str, Any]
    identity: dict[str, Any]
    files: tuple[Phase4C2FileSpec, ...]
    tables: dict[str, dict[str, Any]]
    voice_targets: tuple[dict[str, Any], ...]
    expected: dict[str, Any]
    raw: dict[str, Any]


def _fail(message: str) -> None:
    raise Phase4C2ManifestError(message)


def _exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        _fail(f"{label} keys mismatch")
    return value


def _hex(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        _fail(f"{label} must be a lower-case SHA-256")
    return value


def _path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        _fail(f"{label} must be a POSIX relative path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or pure.as_posix() != value or any(part in {"", ".", ".."} for part in pure.parts):
        _fail(f"{label} is not canonical")
    return value


def load_character_phase4c2_manifest(path: str | Path) -> CharacterPhase4C2Manifest:
    manifest_path = Path(path).resolve()
    try:
        raw_bytes = manifest_path.read_bytes()
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase4C2ManifestError(f"cannot load Phase 4C2 manifest: {exc}") from exc
    root = _exact(
        raw,
        {"schema_version", "manifest_id", "source", "versions", "phase4c1_audit_binding", "boundary", "identity", "files", "tables", "voice_targets", "expected"},
        "manifest",
    )
    if root["schema_version"] != 1 or root["manifest_id"] != EXPECTED_MANIFEST_ID:
        _fail("unsupported Phase 4C2 manifest identity")
    source = _exact(root["source"], {"source_id", "upstream", "release_label", "build", "commit", "tree", "observed_at"}, "source")
    if source != {
        "source_id": "anime-game-data",
        "upstream": "https://github.com/DimbreathBot/AnimeGameData",
        "release_label": "7.0",
        "build": EXPECTED_BUILD,
        "commit": EXPECTED_COMMIT,
        "tree": EXPECTED_TREE,
        "observed_at": "2026-08-19T00:00:00+08:00",
    }:
        _fail("Phase 4C2 source pin mismatch")
    versions = _exact(root["versions"], set(EXPECTED_VERSIONS), "versions")
    if versions != EXPECTED_VERSIONS:
        _fail("Phase 4C2 parser/projection version mismatch")
    audit_binding = _exact(root["phase4c1_audit_binding"], {"audit_version", "manifest", "artifacts"}, "phase4c1_audit_binding")
    if audit_binding != EXPECTED_AUDIT_BINDING:
        _fail("Phase 4C1 audit binding mismatch")
    boundary = _exact(root["boundary"], {"avatar_id", "locales", "selected_primary_rows", "support_scope", "full_character_scan", "full_voice_scan"}, "boundary")
    if boundary != {
        "avatar_id": EXPECTED_AVATAR_ID,
        "locales": ["CHS", "EN", "JP"],
        "selected_primary_rows": 82,
        "support_scope": "selected_textmap_candidates_and_fetter_lut_items_targets_only",
        "full_character_scan": False,
        "full_voice_scan": False,
    }:
        _fail("Phase 4C2 canary boundary mismatch")
    identity = _exact(root["identity"], {"raw_evidence_identity", "logical_identity_created", "native_id_scope", "textmap_hash_scope", "voice_slot_scope", "selector_scope", "checksum_semantics", "context_scope"}, "identity")
    if identity != {
        "raw_evidence_identity": "snapshot_ingest_run_source_file_source_record_occurrence",
        "logical_identity_created": False,
        "native_id_scope": "family_table_scoped_candidate_locator",
        "textmap_hash_scope": "owner_locale_shard_candidate_locator",
        "voice_slot_scope": "fetter_lut_items_physical_container_locator",
        "selector_scope": "container_child_observed_unresolved_no_foreign_key",
        "checksum_semantics": "change_detection_only",
        "context_scope": "voice_container_only_if_future_retrieval_is_authorized",
    }:
        _fail("Phase 4C2 identity boundary mismatch")

    files_raw = root["files"]
    if not isinstance(files_raw, list) or len(files_raw) != 15:
        _fail("Phase 4C2 selected file count mismatch")
    files: list[Phase4C2FileSpec] = []
    for index, value in enumerate(files_raw):
        item = _exact(value, {"relative_path", "format", "size_bytes", "sha256", "blob_oid", "role"}, f"files[{index}]")
        relative = _path(item["relative_path"], f"files[{index}].relative_path")
        if item["format"] != "json" or not isinstance(item["size_bytes"], int) or item["size_bytes"] < 0:
            _fail(f"files[{index}] format/size mismatch")
        _hex(item["sha256"], f"files[{index}].sha256")
        if not isinstance(item["blob_oid"], str) or re.fullmatch(r"[0-9a-f]{40}", item["blob_oid"]) is None:
            _fail(f"files[{index}].blob_oid is invalid")
        if item["role"] not in {"primary_table", "explicit_textmap_candidate_shard", "voice_lut", "fetter_voice_items_container_file"}:
            _fail(f"files[{index}].role is invalid")
        files.append(Phase4C2FileSpec(relative, item["format"], item["size_bytes"], item["sha256"], item["blob_oid"], item["role"]))
    if [item.relative_path for item in files] != sorted(item.relative_path for item in files) or len({item.relative_path for item in files}) != 15:
        _fail("Phase 4C2 selected paths must be sorted and unique")

    tables = _exact(root["tables"], set(EXPECTED_TABLES), "tables")
    for table, (native_field, expected_rows) in EXPECTED_TABLES.items():
        item = _exact(tables[table], {"relative_path", "native_locator_field", "selected_rows", "selected_set_digest"}, f"tables.{table}")
        if item["relative_path"] != f"ExcelBinOutput/{table}.json" or item["native_locator_field"] != native_field:
            _fail(f"{table} table contract mismatch")
        rows = item["selected_rows"]
        if not isinstance(rows, list) or [entry.get("row_index") for entry in rows if isinstance(entry, dict)] != list(expected_rows):
            _fail(f"{table} selected row coordinates mismatch")
        for entry in rows:
            _exact(entry, {"row_index", "pointer", "native_locator_value", "avatar_id", "raw_sha256"}, f"tables.{table}.selected_rows")
            if entry["pointer"] != f"/{entry['row_index']}" or entry["avatar_id"] != EXPECTED_AVATAR_ID:
                _fail(f"{table} selected row pointer/avatar mismatch")
            _hex(entry["raw_sha256"], f"tables.{table}.row.raw_sha256")
        _hex(item["selected_set_digest"], f"tables.{table}.selected_set_digest")

    voice_targets = root["voice_targets"]
    if not isinstance(voice_targets, list) or len(voice_targets) != 72:
        _fail("Phase 4C2 voice target count mismatch")
    target_keys = {"source_row_pointer", "voice_slot", "lut_path", "lut_pointer", "lut_raw_sha256", "item_path", "item_pointer", "item_raw_sha256", "audio_variant_count"}
    for offset, item in enumerate(voice_targets):
        _exact(item, target_keys, f"voice_targets[{offset}]")
        if item["source_row_pointer"] != f"/{579 + offset}/voiceFile":
            _fail("Phase 4C2 voice source row sequence mismatch")
        _path(item["lut_path"], "voice target LUT path")
        _path(item["item_path"], "voice target Items path")
        _hex(item["lut_raw_sha256"], "voice target LUT checksum")
        _hex(item["item_raw_sha256"], "voice target Items checksum")
        if not isinstance(item["audio_variant_count"], int) or item["audio_variant_count"] < 0:
            _fail("voice target audio count is invalid")
    if len({item["voice_slot"] for item in voice_targets}) != 72 or len({(item["item_path"], item["item_pointer"]) for item in voice_targets}) != 72:
        _fail("Phase 4C2 voice targets are not unique physical selections")
    if sum(item["audio_variant_count"] for item in voice_targets) != 7776:
        _fail("Phase 4C2 voice audio total mismatch")
    selected_item_paths = {item["item_path"] for item in voice_targets}
    if selected_item_paths != {
        "BinOutput/Voice/Items/2939652103.json",
        "BinOutput/Voice/Items/750982387.json",
    }:
        _fail("Phase 4C2 canary Voice targets exceed the audited two Items files")

    expected = _exact(root["expected"], {"counts", "status_counts", "table_row_counts", "text_owner_field_counts", "occurrence_set_digests"}, "expected")
    if expected["counts"] != EXPECTED_COUNTS or expected["status_counts"] != EXPECTED_STATUS_COUNTS:
        _fail("Phase 4C2 sealed count/status contract mismatch")
    if expected["table_row_counts"] != {table: len(rows) for table, (_, rows) in EXPECTED_TABLES.items()}:
        _fail("Phase 4C2 table count reconciliation mismatch")
    if not isinstance(expected["text_owner_field_counts"], dict) or sum(expected["text_owner_field_counts"].values()) != 269:
        _fail("Phase 4C2 text owner field reconciliation mismatch")
    digests = _exact(expected["occurrence_set_digests"], {"files", "source_records", "rows", "raw_slots", "relations", "text_resolutions", "text_candidates", "voice_containers", "audio_variants"}, "expected.occurrence_set_digests")
    for key, value in digests.items():
        _hex(value, f"occurrence digest {key}")
    return CharacterPhase4C2Manifest(
        path=manifest_path,
        raw_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        manifest_id=root["manifest_id"],
        source=dict(source),
        versions=dict(versions),
        audit_binding=dict(audit_binding),
        boundary=dict(boundary),
        identity=dict(identity),
        files=tuple(files),
        tables={key: dict(value) for key, value in tables.items()},
        voice_targets=tuple(dict(item) for item in voice_targets),
        expected=dict(expected),
        raw=root,
    )


__all__ = [
    "CharacterPhase4C2Manifest",
    "Phase4C2ManifestError",
    "EXPECTED_AUDIT_BINDING",
    "EXPECTED_AVATAR_ID",
    "EXPECTED_BUILD",
    "EXPECTED_COMMIT",
    "EXPECTED_COUNTS",
    "EXPECTED_MANIFEST_ID",
    "EXPECTED_STATUS_COUNTS",
    "EXPECTED_TABLES",
    "EXPECTED_TREE",
    "EXPECTED_VERSIONS",
    "load_character_phase4c2_manifest",
]
