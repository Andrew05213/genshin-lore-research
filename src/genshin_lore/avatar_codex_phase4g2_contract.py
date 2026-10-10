"""Frozen, family-specific source contract for the Phase 4G2 canary.

This module is deliberately independent from the manifest generator.  It
re-reads the pinned source and accepted Phase 4G1 reports and reconstructs the
five-row physical boundary used by both the builder and the release oracle.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from .util import canonical_json, digest_id, sha256_bytes, sha256_text


FAMILY = "avatar_codex_archive"
FAMILY_CONTRACT_VERSION = "avatar-codex-archive-evidence-v1"
RUN_FAMILY = "avatar_codex_archive_phase4g2_canary"
EXPECTED_COMMIT = "26df1dfbdf05a82bbb1d97506859f3e1c40718d8"
EXPECTED_TREE = "d891ef1d95fdd6c27a1da8a32ea7a61b483647a6"
EXPECTED_SNAPSHOT_ID = f"anime-game-data:7.0:{EXPECTED_COMMIT}"
EXPECTED_BUILD = "CNRELWin7.0.0_R47482070_S47579390_D47579390"
EXPECTED_OBSERVED_AT = "2026-08-19T00:00:00+08:00"
EXPECTED_MIGRATION_APPLIED_AT = "2026-09-01T00:00:00+00:00"
MANIFEST_ID = "anime-game-data-7.0-phase4g2-avatar-codex-canary"
EXPECTED_MANIFEST_SHA256 = "7bf55ad27f3fca024e46bdd8be8291ff0f7227d6295ed226d08f898f6c770aaa"
MIGRATION_RELATIVE = "migrations/0014_phase4g2_avatar_codex_canary_evidence.sql"
MIGRATION_SHA256 = "abc905d8fcb1d1e1507fa7a1cd4447748bcf42d5dc06454a1aaa1f62566f1e48"
PRIMARY_TABLE = "AvatarCodexExcelConfigData"
LOOKUP_TABLE = "AvatarExcelConfigData"
PRIMARY_PATH = f"ExcelBinOutput/{PRIMARY_TABLE}.json"
LOOKUP_PATH = f"ExcelBinOutput/{LOOKUP_TABLE}.json"
PRIMARY_ROWS = 120
LOOKUP_ROWS = 165
SELECTED_ROWS = (0, 36, 104, 105, 119)
SELECTED_IDS = (10000033, 10000062, 10000117, 10000118, 10000150)
PRIMARY_FIELDS = ("avatarId", "beginTime", "sortFactor", "sortId", "hideWhenDontHave", "FMDEJPDNFKB")

SOURCE_MANIFEST_RELATIVE = "manifests/sources/anime-game-data-7.0.json"
SOURCE_MANIFEST_SHA256 = "fd3434cd1d9a75ea5e49c74844cafc0ecf320b92e0cc9110d097b33ece89461d"
TREE_COUNTS = {
    "BinOutput": 117359,
    "ExcelBinOutput": 2198,
    "Readable": 28650,
    "Subtitle": 4644,
    "TextMap": 34,
}
EXPECTED_FILES = {
    PRIMARY_PATH: {
        "blob_oid": "077fc7145e90914cef221c6b14125845a4250993",
        "sha256": "4b2b89e9c5a5225cbbdc29a1c9fb2c61b0962d772238a99758af7ffbadb1d40b",
        "size_bytes": 13786,
        "source_records": PRIMARY_ROWS,
        "role": "primary_physical_rows",
    },
    LOOKUP_PATH: {
        "blob_oid": "6fdcf920522b8f69462438c98461b3a46247f90d",
        "sha256": "11d914363aa771d5297dbc1ecb03d9814c730866efaccfdeae9cdf6be4f44ab3",
        "size_bytes": 346512,
        "source_records": LOOKUP_ROWS,
        "role": "relationship_lookup_inventory_only",
    },
}
M1_ARTIFACTS = {
    "phase4g1-source-inventory.json": "9748960fd50e33a94994cfccabd9b5905e5e7ec2b80d2ad4081f7b57ceea3fe9",
    "phase4g1-row-identity-audit.json": "052bc38758b7f113f90f968d5d943c0b74fcd01eed84a8c655cc382b05d7d28c",
    "phase4g1-relationship-candidate-audit.json": "e8858c16efe127c4d9464e3e5048dffdd7e1e53f31584f997981e209976094bd",
    "phase4g1-anchor-matrix.json": "a1c734a0a6c3b8461abf5c43a08ae548c8cf6dfe39de79bad76808b7cc231571",
    "phase4g1-exclusions.json": "bd7c7611714310dda71834f5f6022126bd86a3dc82526423d1df5c348d18c66e",
    "phase4g1-reconciliation.json": "0ead94f0cb790da98f1c6fb752325c47408353e35c903065fc9b1cad45f69211",
    "phase4g1-audit-manifest.json": "22635b39fd2b96e4dffd0846fe6cb97ddcf55149ff0fdd19a08c699fe417b03e",
}


class AvatarCodexPhase4G2ContractError(RuntimeError):
    """A source, manifest, or frozen canary contract failed closed."""


def _fail(message: str) -> None:
    raise AvatarCodexPhase4G2ContractError(message)


def _strict(raw: bytes, label: str) -> Any:
    def reject(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail(f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=reject, parse_constant=lambda value: _fail(f"non-standard JSON constant in {label}: {value}"))
    except AvatarCodexPhase4G2ContractError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"invalid JSON in {label}: {exc}")


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), *args],
            check=False, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _fail(f"cannot inspect source checkout: {exc}")
    if result.returncode:
        _fail(f"source git failed for {' '.join(args)}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def verify_source_pin(root: str | Path) -> None:
    path = Path(root).resolve()
    if _git(path, "status", "--porcelain=v1", "--untracked-files=all"):
        _fail("pinned AnimeGameData checkout is not clean")
    if _git(path, "rev-parse", "HEAD") != EXPECTED_COMMIT:
        _fail("AnimeGameData checkout commit does not match pinned 7.0 source")
    if _git(path, "rev-parse", "HEAD^{tree}") != EXPECTED_TREE:
        _fail("AnimeGameData checkout tree does not match pinned 7.0 source")
    names = _git(path, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    counts = Counter(name.split("/", 1)[0] for name in names)
    if {key: counts[key] for key in TREE_COUNTS} != TREE_COUNTS:
        _fail("AnimeGameData tree-directory counts changed")


def _load_reports(repo_root: Path) -> dict[str, Any]:
    base = repo_root / "reports" / "phase4g1-avatar-codex"
    reports: dict[str, Any] = {}
    for name, expected in M1_ARTIFACTS.items():
        try:
            raw = (base / name).read_bytes()
        except OSError as exc:
            _fail(f"Phase 4G1 artifact is unreadable: {name}: {exc}")
        if sha256_bytes(raw) != expected:
            _fail(f"Phase 4G1 artifact SHA-256 mismatch: {name}")
        value = _strict(raw, name)
        if not isinstance(value, dict) or value.get("audit_version") != "4G1.1":
            _fail(f"Phase 4G1 artifact contract mismatch: {name}")
        reports[name] = value
    summary = reports["phase4g1-reconciliation.json"].get("summary", {})
    if summary.get("status") != "pass" or summary.get("check_count") != 15 or summary.get("passed_checks") != 15 or not all(summary.get("checks", {}).values()):
        _fail("Phase 4G1 reconciliation is not the accepted 15/15 report")
    return reports


def _load_file(root: Path, relative: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    expected = EXPECTED_FILES[relative]
    path = root / relative
    if path.is_symlink() or not path.is_file():
        _fail(f"required source file is not a regular file: {relative}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        _fail(f"required source file is unreadable: {relative}: {exc}")
    if _git(root, "hash-object", "--", relative) != expected["blob_oid"] or len(raw) != expected["size_bytes"] or sha256_bytes(raw) != expected["sha256"]:
        _fail(f"source file byte/blob seal mismatch: {relative}")
    value = _strict(raw, relative)
    if not isinstance(value, list) or len(value) != expected["source_records"] or any(not isinstance(item, dict) for item in value):
        _fail(f"source file row shape changed: {relative}")
    return value, {
        "relative_path": relative,
        "format": "json",
        "role": expected["role"],
        "source_records": len(value),
        "selected_records": 5 if relative == PRIMARY_PATH else 0,
        "selected": relative == PRIMARY_PATH,
        "blob_oid": expected["blob_oid"],
        "sha256": expected["sha256"],
        "size_bytes": expected["size_bytes"],
        "strict_duplicate_key_policy": "reject",
    }


def load_manifest(path: str | Path) -> dict[str, Any]:
    try:
        raw = Path(path).resolve().read_bytes()
    except OSError as exc:
        _fail(f"manifest is unreadable: {exc}")
    if sha256_bytes(raw) != EXPECTED_MANIFEST_SHA256:
        _fail("Phase 4G2 manifest SHA-256 mismatch")
    value = _strict(raw, "Phase 4G2 manifest")
    if not isinstance(value, dict) or value.get("schema_version") != 1 or value.get("manifest_id") != MANIFEST_ID:
        _fail("Phase 4G2 manifest root/identity mismatch")
    required = {"schema_version", "manifest_id", "family", "family_contract_version", "candidate_stage", "source", "versions", "audit_binding", "migration_binding", "boundary", "anchors", "identity", "exclusions", "files", "selected_occurrences", "expected", "generator"}
    if set(value) != required:
        _fail("Phase 4G2 manifest key set mismatch")
    if value["family"] != FAMILY or value["family_contract_version"] != FAMILY_CONTRACT_VERSION or value["candidate_stage"] != "bounded_canary":
        _fail("Phase 4G2 family contract mismatch")
    if value["audit_binding"] != M1_ARTIFACTS:
        _fail("Phase 4G2 M1 audit binding mismatch")
    migration = value["migration_binding"]
    expected_migration = {
        "relative_path": MIGRATION_RELATIVE,
        "sha256": MIGRATION_SHA256,
        "schema_from": 13,
        "schema_to": 14,
        "tables": ["avatar_codex_row_occurrence", "avatar_codex_raw_field_occurrence", "avatar_codex_relationship_occurrence", "avatar_codex_relationship_candidate_occurrence", "avatar_codex_opaque_occurrence"],
        "indexes": ["idx_avatar_codex_row_run_row", "idx_avatar_codex_raw_run_row", "idx_avatar_codex_relation_run_status", "idx_avatar_codex_relation_candidate", "idx_avatar_codex_opaque_run_field"],
        "immutable_triggers": ["trg_avatar_codex_row_insert_existing_immutable", "trg_avatar_codex_row_immutable", "trg_avatar_codex_row_delete_immutable", "trg_avatar_codex_raw_insert_existing_immutable", "trg_avatar_codex_raw_immutable", "trg_avatar_codex_raw_delete_immutable", "trg_avatar_codex_relationship_insert_existing_immutable", "trg_avatar_codex_relationship_immutable", "trg_avatar_codex_relationship_delete_immutable", "trg_avatar_codex_candidate_insert_existing_immutable", "trg_avatar_codex_candidate_immutable", "trg_avatar_codex_candidate_delete_immutable", "trg_avatar_codex_opaque_insert_existing_immutable", "trg_avatar_codex_opaque_immutable", "trg_avatar_codex_opaque_delete_immutable"],
    }
    if migration != expected_migration:
        _fail("Phase 4G2 migration binding mismatch")
    source = value["source"]
    expected_source = {
        "source_id": "anime-game-data", "release_label": "7.0", "build": EXPECTED_BUILD,
        "commit": EXPECTED_COMMIT, "tree": EXPECTED_TREE, "snapshot_id": EXPECTED_SNAPSHOT_ID,
        "observed_at": EXPECTED_OBSERVED_AT, "upstream": "https://github.com/DimbreathBot/AnimeGameData",
        "source_manifest": {"relative_path": SOURCE_MANIFEST_RELATIVE, "sha256": SOURCE_MANIFEST_SHA256, "size_bytes": 860},
        "tree_directory_counts": TREE_COUNTS,
    }
    if source != expected_source:
        _fail("Phase 4G2 source binding mismatch")
    boundary = value["boundary"]
    expected_boundary = {
        "family": FAMILY, "family_contract_version": FAMILY_CONTRACT_VERSION,
        "primary_table": PRIMARY_TABLE, "primary_path": PRIMARY_PATH, "primary_source_rows": 120,
        "selected_rows": list(SELECTED_ROWS), "selected_ids": list(SELECTED_IDS),
        "selected_primary_rows": 5, "supplement_count": 0,
        "lookup_table": LOOKUP_TABLE, "lookup_path": LOOKUP_PATH, "lookup_inventory_rows": 165,
        "lookup_rows_are_primary_evidence": False, "coverage": "bounded_canary",
        "corpus_complete": False, "evidence_only": True,
    }
    if boundary != expected_boundary:
        _fail("Phase 4G2 bounded boundary mismatch")
    anchors = value["anchors"]
    expected_matrix = {
        "ordinary_primary_row_shape": [True, False, False, False, True],
        "hideWhenDontHave_true": [False, True, False, False, False],
        "FMDEJPDNFKB_opaque_unresolved": [False, False, True, True, False],
        "last_source_row": [False, False, False, False, True],
    }
    if anchors != {"fixed_rows": list(SELECTED_ROWS), "fixed_ids": list(SELECTED_IDS), "supplements": [], "coverage_matrix": expected_matrix}:
        _fail("Phase 4G2 anchor matrix mismatch")
    identity = value["identity"]
    if identity != {
        "raw_evidence_identity": "snapshot_parser_build_ingest_run_source_file_source_record_coordinate_occurrence",
        "logical_identity_created": False, "native_id_effect": "table_scoped_locator_only",
        "lookup_candidate_effect": "physical_lookup_occurrence_only", "cross_family_character_alignment": False,
        "text_hash_identity_effect": "excluded", "asset_identity_effect": "excluded",
        "get_scope": "single_physical_row_container_only",
    }:
        _fail("Phase 4G2 identity contract mismatch")
    exclusions = value["exclusions"]
    if exclusions != {
        "textmap_paths": ["TextMap/TextMapCHS.json", "TextMap/TextMap_MediumCHS.json", "TextMap/TextMapEN.json", "TextMap/TextMap_MediumEN.json", "TextMap/TextMapJP.json", "TextMap/TextMap_MediumJP.json"],
        "lookup_fields": ["nameTextMapHash", "descTextMapHash", "iconName", "imageName"],
        "lookup_payload_selected": False, "assets_selected": False, "textmap_selected": False,
        "character_family_merge": False, "full_avatar_codex": False,
        "reason": "Only five AvatarCodex physical rows and five explicit AvatarExcel lookup candidates are selected.",
    }:
        _fail("Phase 4G2 exclusion contract mismatch")
    return value


def _scope(path: str, row: int, field: str | None = None) -> dict[str, Any]:
    pointer = f"/{row}" + (f"/{field}" if field is not None else "")
    return {"snapshot_id": EXPECTED_SNAPSHOT_ID, "path": path, "source_file": path, "row": row, "source_record": row, "pointer": pointer, "field": field, "array_index": None}


def _record_checksum(value: Any) -> str:
    return sha256_text(canonical_json(value))


def _occurrence_digest(values: Sequence[Mapping[str, Any]]) -> str:
    return sha256_text(canonical_json(sorted(canonical_json(item) for item in values)))


def _sequence_digest(values: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(canonical_json(value).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def derive_source_contract(source_root: str | Path, repo_root: str | Path, manifest: Mapping[str, Any] | None = None) -> dict[str, Any]:
    root = Path(source_root).resolve()
    repository = Path(repo_root).resolve()
    verify_source_pin(root)
    reports = _load_reports(repository)
    primary, primary_file = _load_file(root, PRIMARY_PATH)
    lookup, lookup_file = _load_file(root, LOOKUP_PATH)
    if manifest is not None:
        if manifest.get("files") != [primary_file, lookup_file]:
            _fail("Phase 4G2 manifest file boundary mismatch")
    source_summary = reports["phase4g1-source-inventory.json"]["summary"]
    if source_summary.get("primary_physical_rows") != 120 or source_summary.get("lookup_inventory_rows") != 165 or source_summary.get("selected_files") != 1:
        _fail("Phase 4G1 source summary mismatch")
    row_report = reports["phase4g1-row-identity-audit.json"]
    relation_report = reports["phase4g1-relationship-candidate-audit.json"]
    audit_rows = {(item["scope"]["path"], item["scope"]["row"]): item for item in row_report["row_occurrences"]}
    audit_relations = {(item["source_scope"]["path"], item["source_scope"]["row"]): item for item in relation_report["relationships"]}
    lookup_index: dict[int, list[int]] = {}
    for index, row in enumerate(lookup):
        native = row.get("id")
        if isinstance(native, bool) or not isinstance(native, int):
            _fail("AvatarExcel lookup id is not an integer")
        lookup_index.setdefault(native, []).append(index)
    rows: list[dict[str, Any]] = []
    raw_fields: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    relationship_candidates: list[dict[str, Any]] = []
    opaque: list[dict[str, Any]] = []
    source_records: list[dict[str, Any]] = []
    selected_occurrences: list[dict[str, Any]] = []
    for anchor_order, (row_index, expected_id) in enumerate(zip(SELECTED_ROWS, SELECTED_IDS)):
        raw = dict(primary[row_index])
        if raw.get("avatarId") != expected_id:
            _fail(f"AvatarCodex anchor id changed at source row {row_index}")
        checksum = _record_checksum(raw)
        audit = audit_rows.get((PRIMARY_PATH, row_index))
        if audit is None or audit.get("raw_record_checksum") != checksum:
            _fail(f"Phase 4G1 row checksum mismatch at source row {row_index}")
        if set(raw) - set(PRIMARY_FIELDS):
            _fail(f"unexpected AvatarCodex raw field at source row {row_index}")
        if not isinstance(raw.get("beginTime"), str):
            _fail("beginTime must remain a raw string")
        if "hideWhenDontHave" in raw and not isinstance(raw["hideWhenDontHave"], bool):
            _fail("hideWhenDontHave must remain a raw boolean")
        row_value = {
            "anchor_order": anchor_order, "evidence_kind": "avatar_codex_entry", "table": PRIMARY_TABLE,
            "relative_path": PRIMARY_PATH, "row_index": row_index, "pointer": f"/{row_index}",
            "native_locator_field": "avatarId", "native_locator_value": str(expected_id),
            "raw_checksum": checksum, "raw_value": raw, "scope": _scope(PRIMARY_PATH, row_index),
        }
        rows.append(row_value)
        source_records.append({"relative_path": PRIMARY_PATH, "source_pointer": f"/{row_index}", "record_kind": "avatar_codex_entry", "native_id": str(expected_id), "raw_checksum": checksum, "raw_value": raw})
        for field, value in raw.items():
            raw_fields.append({"table": PRIMARY_TABLE, "row_index": row_index, "source_pointer": f"/{row_index}/{field}", "field_name": field, "field_presence": "present", "raw_value": value})
            if field == "FMDEJPDNFKB":
                opaque.append({"table": PRIMARY_TABLE, "row_index": row_index, "source_pointer": f"/{row_index}/{field}", "field_name": field, "array_index": None, "raw_value": value, "resolution_status": "opaque_unresolved"})
        relation = audit_relations.get((PRIMARY_PATH, row_index))
        matches = lookup_index.get(expected_id, [])
        if relation is None or relation.get("status") != "resolved_unique" or len(matches) != 1 or relation.get("candidate_count") != 1:
            _fail(f"AvatarCodex avatarId lookup relationship changed at source row {row_index}")
        lookup_row = matches[0]
        lookup_raw = dict(lookup[lookup_row])
        lookup_checksum = _record_checksum(lookup_raw)
        candidate_audit = relation["candidate_records"][0]
        if candidate_audit.get("lookup_row") != lookup_row or candidate_audit.get("raw_record_checksum") != lookup_checksum:
            _fail(f"AvatarExcel candidate coordinate/checksum changed at source row {row_index}")
        relationship_key = f"{PRIMARY_PATH}:/{row_index}/avatarId"
        relationships.append({"relationship_key": relationship_key, "table": PRIMARY_TABLE, "row_index": row_index, "source_pointer": f"/{row_index}/avatarId", "field_name": "avatarId", "raw_value": expected_id, "target_table": LOOKUP_TABLE, "target_field": "id", "resolution_status": "resolved_unique"})
        relationship_candidates.append({"relationship_key": relationship_key, "candidate_ordinal": 0, "source_relative_path": LOOKUP_PATH, "source_pointer": f"/{lookup_row}", "target_table": LOOKUP_TABLE, "target_field": "id", "target_raw_value": expected_id, "raw_checksum": lookup_checksum, "raw_value": lookup_raw})
        source_records.append({"relative_path": LOOKUP_PATH, "source_pointer": f"/{lookup_row}", "record_kind": "relationship_lookup_candidate", "native_id": str(expected_id), "raw_checksum": lookup_checksum, "raw_value": lookup_raw})
        selected_occurrences.append({"anchor_order": anchor_order, "source_row": row_index, "avatar_id": expected_id, "source_pointer": f"/{row_index}", "raw_checksum": checksum, "lookup_row": lookup_row, "lookup_pointer": f"/{lookup_row}", "lookup_raw_checksum": lookup_checksum})
    collections = {"rows": rows, "raw_fields": raw_fields, "relationships": relationships, "relationship_candidates": relationship_candidates, "opaque": opaque}
    counts = {key: len(value) for key, value in collections.items()} | {"source_records": len(source_records), "files": 2}
    status_counts = {
        "row_kinds": dict(sorted(Counter(item["evidence_kind"] for item in rows).items())),
        "field_presence": dict(sorted(Counter(item["field_presence"] for item in raw_fields).items())),
        "relationships": dict(sorted(Counter(item["resolution_status"] for item in relationships).items())),
        "opaque": dict(sorted(Counter(item["resolution_status"] for item in opaque).items())),
    }
    result = {**collections, "source_records": source_records, "counts": counts, "status_counts": status_counts, "occurrence_set_digests": {key: _occurrence_digest(value) for key, value in collections.items()}, "selected_occurrences": selected_occurrences, "files": [primary_file, lookup_file], "source_record_digest": _sequence_digest(source_records)}
    if manifest is not None:
        if manifest.get("selected_occurrences") != selected_occurrences:
            _fail("Phase 4G2 selected occurrence boundary mismatch")
        expected = manifest.get("expected")
        if not isinstance(expected, dict) or expected != {"counts": counts, "status_counts": status_counts, "occurrence_set_digests": result["occurrence_set_digests"], "source_record_digest": result["source_record_digest"]}:
            _fail("Phase 4G2 manifest expected counts/digests mismatch")
    return result


def expected_identity(manifest: Mapping[str, Any]) -> dict[str, str]:
    parser_build_id = digest_id("pb-avatar-codex-phase4g2", canonical_json({"versions": manifest["versions"], "manifest_id": manifest["manifest_id"]}))
    config_checksum = sha256_text(canonical_json({"manifest_id": manifest["manifest_id"], "versions": manifest["versions"], "selected_occurrences": manifest["selected_occurrences"], "audit_binding": manifest["audit_binding"], "migration_binding": manifest["migration_binding"]}))
    ingest_run_id = digest_id("ir-avatar-codex-phase4g2", EXPECTED_SNAPSHOT_ID, parser_build_id, config_checksum)
    return {"snapshot_id": EXPECTED_SNAPSHOT_ID, "parser_build_id": parser_build_id, "config_checksum": config_checksum, "ingest_run_id": ingest_run_id}


def occurrence_id(kind: str, identity: Mapping[str, str], *parts: Any) -> str:
    return digest_id(f"avatar-codex-{kind}", identity["snapshot_id"], identity["parser_build_id"], identity["ingest_run_id"], *parts)


__all__ = [
    "AvatarCodexPhase4G2ContractError", "EXPECTED_BUILD", "EXPECTED_COMMIT", "EXPECTED_MANIFEST_SHA256",
    "EXPECTED_MIGRATION_APPLIED_AT", "EXPECTED_OBSERVED_AT", "EXPECTED_SNAPSHOT_ID", "EXPECTED_TREE",
    "FAMILY", "FAMILY_CONTRACT_VERSION", "LOOKUP_PATH", "LOOKUP_TABLE", "M1_ARTIFACTS", "MANIFEST_ID",
    "MIGRATION_RELATIVE", "MIGRATION_SHA256", "PRIMARY_PATH", "PRIMARY_TABLE", "RUN_FAMILY", "SELECTED_IDS",
    "SELECTED_ROWS", "derive_source_contract", "expected_identity", "load_manifest", "occurrence_id", "verify_source_pin",
]
