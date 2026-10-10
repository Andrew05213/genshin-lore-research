"""Batch M3 R0 inventory and machine-readable coverage state.

M3 deliberately consumes the tracked M0 evidence instead of rescanning the
9GB source tree.  It reports file-level coverage separately from record-level
coverage and carries the M2 family shards as a first R1 wave.  No database,
schema, provider, or MCP artifact is created by this module.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import batch_space
from .batch_m0_baseline import (
    BatchM0Error,
    SOURCE_COMMIT as M0_SOURCE_COMMIT,
    SOURCE_ROOTS as M0_SOURCE_ROOTS,
    SOURCE_SNAPSHOT_ID as M0_SOURCE_SNAPSHOT_ID,
    SOURCE_TREE as M0_SOURCE_TREE,
    validate_tracked_acceptance_contract,
)
from .batch_m3_physical import (
    BatchM3PhysicalError,
    build_r0_artifact as build_narrow_r0_artifact,
    verify_r0_artifact as verify_narrow_r0_artifact,
)
from .batch_m3_r1_projection import (
    R1ProjectionError,
    load_specs as load_r1_specs,
    portable_manifest as portable_r1_manifest,
    verify_wave as verify_r1_wave,
)
from .family_projection_protocol import rowset_digest, schema_digest, spec_digest

REPORT_VERSION = 2
REPORT_NAME = "batch-m3-coverage.json"
MANIFEST_NAME = "batch-m3-manifest.json"
MARKDOWN_NAME = "batch-m3-coverage.md"
M0_REPORT_SHA256 = "0959cb92f8b5beb2ac4d708240f29b62deebf2b0ccb9e30b4df2c3d05d66b798"
M2_MANIFEST_SHA256 = "7b9d3f9a47554747df205f27462b9787b4737c9da15a144fc3c5d1a52aa60467"
M2_HANDOFF_SHA256 = "de4bbe1b9bdbae7db5a5153878f4fb17994b4eed33e5fa5c9ec6185bc6bc061e"
M2_AUTHORITATIVE_MANIFEST_SHA256 = "6d0fea0c131882dd94ff5a1022614641b96cae8cdc355093aa5d5fbf9e792353"
SOURCE_COMMIT = M0_SOURCE_COMMIT
SOURCE_TREE = M0_SOURCE_TREE
SNAPSHOT_ID = M0_SOURCE_SNAPSHOT_ID
EXPECTED_M0_FILE_COUNT = 152_885
EXPECTED_M0_BYTES = 9_154_196_685
PROJECT_SPACE_LIMIT_BYTES = batch_space.PROJECT_SPACE_LIMIT_BYTES
PROJECT_SPACE_WARNING_BYTES = batch_space.PROJECT_SPACE_WARNING_BYTES
M0_RELATIVE = "reports/batch-m0/batch-m0-summary.json"
M0_MARKDOWN_RELATIVE = "reports/batch-m0/batch-m0-summary.md"
M2_RELATIVE = "reports/batch-m2/batch-m2-manifest.json"
M2_HANDOFF_RELATIVE = "reports/batch-m2/batch-m2-handoff.json"
M2_AUTHORITATIVE_RELATIVE = "manifests/batch/anime-game-data-7.0-batch-m2-family-shards.json"
R1_HANDOFF_RELATIVE = "reports/batch-m3/batch-m3-r1-handoff.json"
R1_MANIFEST_RELATIVE = "reports/batch-m3/batch-m3-r1-manifest.json"
M2_MANIFEST_VERSION = "batch-m2-family-shards-v1"
M2_STATUS = "sol_xhigh_pass_m2_gate_closed"
M2_COVERAGE = "R1_structured_family"
M2_FAMILY_ORDER = (
    "avatar_codex_archive",
    "core_tutorial",
    "quest_codex_archive",
    "world_archive",
)
EXPECTED_M2_FAMILIES = {
    "avatar_codex_archive": {
        "family_contract_version": "avatar-codex-archive-evidence-v1",
        "ingest_run_id": "ir-avatar-codex-batch-m2:26df1dfbdf05a82bbb1d97506859f3e1c40718d8",
        "parser_build_id": "pb-avatar-codex-batch-m2:26df1dfbdf05a82bbb1d97506859f3e1c40718d8",
        "spec_digest": "4ffba00cb9bf09631e1f8069b6600aeafd8d084fb0c8f085fab399d6c3d1c1a6",
    },
    "core_tutorial": {
        "family_contract_version": "core-tutorial-evidence-v1",
        "ingest_run_id": "ir-core_tutorial-batch-m2-replay:26df1dfbdf05a82bbb1d97506859f3e1c40718d8",
        "parser_build_id": "pb-family-replay-batch-m2:26df1dfbdf05a82bbb1d97506859f3e1c40718d8",
        "spec_digest": "83f38bd08f12ba9aa3035ccbfea09163ffdf39de8b86dc964e6ea21764959bdd",
    },
    "quest_codex_archive": {
        "family_contract_version": "quest-codex-archive-evidence-v1",
        "ingest_run_id": "ir-quest_codex_archive-batch-m2-replay:26df1dfbdf05a82bbb1d97506859f3e1c40718d8",
        "parser_build_id": "pb-family-replay-batch-m2:26df1dfbdf05a82bbb1d97506859f3e1c40718d8",
        "spec_digest": "978cd20312028012d31a47ffd3e8ea3e0b41956b3eb491ee75505cf324019bda",
    },
    "world_archive": {
        "family_contract_version": "world-archive-evidence-v1",
        "ingest_run_id": "ir-world_archive-batch-m2-replay:26df1dfbdf05a82bbb1d97506859f3e1c40718d8",
        "parser_build_id": "pb-family-replay-batch-m2:26df1dfbdf05a82bbb1d97506859f3e1c40718d8",
        "spec_digest": "7af3b9f6f2a117f89f313b8313d6745bf4c212820876f011aed8f45da7a38369",
    },
}
M2_REPLAY_COVERAGE = {
    "avatar_codex_archive": "R1_structured_family",
    "core_tutorial": "R1_family_replay_comparison",
    "quest_codex_archive": "R1_family_replay_comparison",
    "world_archive": "R1_family_replay_comparison",
}
M0_UNSUPPORTED_ATOMS = (
    "physical_members_addressable_record_semantics_unsupported",
    "unparsed_container",
    "unsupported_record_layout",
)
BROAD_R0_RECORD_TARGET = "90-95%"
BROAD_R1_FAMILY_MIN = 10
BROAD_R1_FAMILY_MAX = 20
PHYSICAL_ARTIFACT_SCHEMA_VERSION = "batch-m3-r0-physical-occurrence-v1"
PHYSICAL_ARTIFACT_NAME = "batch-m3-r0-physical.sqlite"
PHYSICAL_PARSER_BUILD_ID = "pb-batch-m3-r0-physical:v1"
PHYSICAL_INGEST_RUN_ID = "ir-batch-m3-r0-physical:v1"
ARTIFACT_LIMIT_BYTES = 4 * 1024**3
# SQLite can briefly need rollback/temp pages while the compact artifact is
# being filled. Reserve this bounded increment before opening the writer.
TEMP_OVERHEAD_RESERVATION_BYTES = 512 * 1024**2
ARTIFACT_RESERVATION_BYTES = ARTIFACT_LIMIT_BYTES + TEMP_OVERHEAD_RESERVATION_BYTES
PHYSICAL_OCCURRENCE_TABLE = "physical_occurrence"
FILE_INVENTORY_TABLE = "file_inventory"
ARTIFACT_IDENTITY_TABLE = "artifact_identity"
PHYSICAL_VIEW = "physical_occurrences"
R0_RECORD_TARGET_MIN_PERCENT = 90.0
R0_RECORD_TARGET_MAX_PERCENT = 95.0
BLOCKER_DEFINITION = (
    "Blocker means functional/evidence semantics, data integrity, completeness "
    "contract, performance or project-space gate, release correctness, or "
    "compatibility break. Security hardening and assumed attack-surface issues "
    "are recorded as non-blocking notes during development."
)


class BatchM3Error(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def canonical_compact(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BatchM3Error(f"{label} unavailable or invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise BatchM3Error(f"{label} must be a JSON object")
    return value


def _load_json_with_raw(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except (OSError, UnicodeError) as exc:
        raise BatchM3Error(f"{label} unavailable: {exc}") from exc
    try:
        value = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BatchM3Error(f"{label} unavailable or invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise BatchM3Error(f"{label} must be a JSON object")
    return value, raw


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    if result.returncode:
        raise BatchM3Error(result.stderr.strip() or result.stdout.strip() or "source git inspection failed")
    return result.stdout.strip()


def source_pin_status(source_root: Path, expected_pin: Mapping[str, Any] | None = None) -> dict[str, Any]:
    expected = expected_pin or {
        "commit": SOURCE_COMMIT,
        "tree": SOURCE_TREE,
        "snapshot_id": SNAPSHOT_ID,
        "roots": list(M0_SOURCE_ROOTS),
    }
    if not source_root.is_dir():
        return {
            "status": "unavailable",
            "reason": "pinned source checkout is unavailable",
            "expected_commit": expected.get("commit"),
            "expected_tree": expected.get("tree"),
        }
    try:
        clean = not bool(_git(source_root, "status", "--porcelain=v1", "--untracked-files=all"))
        commit = _git(source_root, "rev-parse", "HEAD")
        tree = _git(source_root, "rev-parse", "HEAD^{tree}")
    except (OSError, BatchM3Error) as exc:
        return {
            "status": "unavailable",
            "reason": str(exc),
            "expected_commit": expected.get("commit"),
            "expected_tree": expected.get("tree"),
        }
    return {
        "status": "matched" if clean and commit == expected.get("commit") and tree == expected.get("tree") else "mismatch",
        "clean": clean,
        "commit": commit,
        "tree": tree,
        "expected_commit": expected.get("commit"),
        "expected_tree": expected.get("tree"),
    }


def _validate_m0(repo_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = repo_root / M0_RELATIVE
    m0, raw = _load_json_with_raw(path, "M0 report")
    if raw != canonical_bytes(m0):
        raise BatchM3Error("tracked M0 report is not byte-exact canonical JSON")
    if _sha256_bytes(raw) != M0_REPORT_SHA256:
        raise BatchM3Error("tracked M0 report whole-file SHA-256 mismatch")
    try:
        validate_tracked_acceptance_contract(m0, raw, expected_report_sha256=M0_REPORT_SHA256)
    except BatchM0Error as exc:
        raise BatchM3Error(f"tracked M0 acceptance contract failed: {exc}") from exc
    denominator = m0["source_denominator"]
    record_level = denominator["record_level"]
    status_counts = record_level.get("status_counts")
    expected_counts = {
        "physical_members_addressable_record_semantics_unsupported": 114789,
        "physical_top_level_occurrences_addressable": 4461,
        "unparsed_container": 341,
        "unsupported_record_layout": 33294,
    }
    if status_counts != expected_counts:
        raise BatchM3Error("tracked M0 unsupported/record status contract changed")
    fidelity = denominator["physical_fidelity"]
    if fidelity.get("coordinate_contract") != "pair_ordinal_path_v1" or fidelity.get("byte_spans_implemented") is not False:
        raise BatchM3Error("tracked M0 coordinate contract changed")
    binding = {
        "report": M0_RELATIVE,
        "report_sha256": M0_REPORT_SHA256,
        "status": m0["status"],
        "source_pin": m0["source_pin"],
        "file_level": denominator["file_level"],
        "record_level": {
            "coverage_complete": record_level["coverage_complete"],
            "physical_top_level_occurrences": record_level["physical_top_level_occurrences"],
            "status_counts": status_counts,
        },
        "unsupported": {key: status_counts[key] for key in M0_UNSUPPORTED_ATOMS},
        "coordinate_contract": fidelity["coordinate_contract"],
        "byte_spans_implemented": fidelity["byte_spans_implemented"],
    }
    return m0, binding


def _require_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise BatchM3Error(f"M2 {label} contract changed")


def _validate_m2_family(item: Mapping[str, Any], *, handoff: bool = False) -> None:
    family = item.get("family")
    if family not in EXPECTED_M2_FAMILIES:
        raise BatchM3Error("M2 family set/order contract changed")
    expected = EXPECTED_M2_FAMILIES[family]
    _require_equal(item.get("spec_digest"), expected["spec_digest"], f"{family} spec digest")
    if not handoff:
        for key in ("family_contract_version", "ingest_run_id", "parser_build_id"):
            _require_equal(item.get(key), expected[key], f"{family} {key}")
        return
    _require_equal(item.get("kind"), "shard", f"{family} kind")
    artifact, spec, verification = item.get("artifact"), item.get("spec"), item.get("verification")
    if not isinstance(artifact, dict) or not isinstance(spec, dict) or not isinstance(verification, dict):
        raise BatchM3Error(f"M2 {family} handoff family container missing")
    metadata, spec_metadata = artifact.get("metadata"), spec.get("metadata")
    if not isinstance(metadata, dict) or not isinstance(spec_metadata, dict):
        raise BatchM3Error(f"M2 {family} metadata missing")
    for container, label in ((metadata, "artifact metadata"), (spec, "spec")):
        _require_equal(container.get("family"), family, f"{family} {label} family")
        _require_equal(container.get("family_contract_version"), expected["family_contract_version"], f"{family} {label} contract")
        _require_equal(container.get("parser_build_id"), expected["parser_build_id"], f"{family} {label} parser")
        _require_equal(container.get("ingest_run_id"), expected["ingest_run_id"], f"{family} {label} run")
    _require_equal(metadata.get("spec_digest"), expected["spec_digest"], f"{family} metadata spec digest")
    for container, label in ((metadata, "artifact metadata"), (spec, "spec")):
        _require_equal(container.get("corpus_complete"), False, f"{family} {label} completeness")
        _require_equal(container.get("product_accepted"), False, f"{family} {label} product acceptance")
    _require_equal(spec.get("snapshot_id"), SNAPSHOT_ID, f"{family} snapshot")
    for container, label in ((metadata, "artifact metadata"), (spec, "spec"), (verification, "verification")):
        _require_equal(container.get("coverage_level"), M2_REPLAY_COVERAGE[family], f"{family} {label} coverage")
    _require_equal(verification.get("family"), family, f"{family} verification family")
    _require_equal(verification.get("ingest_run_id"), expected["ingest_run_id"], f"{family} verification run")
    _require_equal(verification.get("status"), "verified", f"{family} verification status")
    _require_equal(verification.get("product_accepted"), False, f"{family} verification product acceptance")
    _require_equal(verification.get("corpus_complete"), False, f"{family} verification completeness")
    if spec_metadata.get("provider_or_registry_changed", False) is not False or spec_metadata.get("g2_canary_imported", False) is not False:
        raise BatchM3Error(f"M2 {family} provider/G2 contract changed")


def _validate_m2(repo_root: Path) -> dict[str, Any]:
    authoritative, authoritative_raw = _load_json_with_raw(
        repo_root / M2_AUTHORITATIVE_RELATIVE, "authoritative M2 manifest"
    )
    manifest, manifest_raw = _load_json_with_raw(repo_root / M2_RELATIVE, "M2 manifest")
    handoff, handoff_raw = _load_json_with_raw(repo_root / M2_HANDOFF_RELATIVE, "M2 handoff")
    if _sha256_bytes(authoritative_raw) != M2_AUTHORITATIVE_MANIFEST_SHA256:
        raise BatchM3Error("authoritative M2 manifest whole-file SHA-256 mismatch")
    # This tracked manifest is the human-readable authoritative release input;
    # its exact bytes (plus strict duplicate-key parsing) are the canonical
    # identity used by the M3 report.
    authoritative_source = authoritative.get("source")
    if not isinstance(authoritative_source, dict):
        raise BatchM3Error("authoritative M2 manifest source binding is missing")
    for key, value in (
        ("source_id", "anime-game-data"),
        ("release_label", "7.0"),
        ("commit", SOURCE_COMMIT),
        ("tree", SOURCE_TREE),
        ("snapshot_id", SNAPSHOT_ID),
    ):
        _require_equal(authoritative_source.get(key), value, f"authoritative source {key}")
    for key, value in (
        ("manifest_version", M2_MANIFEST_VERSION),
        ("coverage_level", M2_COVERAGE),
        ("corpus_complete", False),
        ("product_accepted", False),
        ("family_projection_protocol", "family-projection-shard-v1"),
        ("m3_status", "not_started"),
        ("m4_status", "not_started"),
    ):
        _require_equal(authoritative.get(key), value, f"authoritative {key}")
    auth_families = authoritative.get("families")
    if not isinstance(auth_families, list) or [
        item.get("family") for item in auth_families if isinstance(item, dict)
    ] != list(M2_FAMILY_ORDER):
        raise BatchM3Error("authoritative M2 family order contract changed")
    auth_by_family: dict[str, Mapping[str, Any]] = {}
    for item in auth_families:
        if not isinstance(item, dict):
            raise BatchM3Error("authoritative M2 family entry is not an object")
        family = item.get("family")
        projection_kind = item.get("projection_kind")
        if family not in EXPECTED_M2_FAMILIES or not isinstance(projection_kind, str) or not projection_kind:
            raise BatchM3Error("authoritative M2 family projection contract changed")
        auth_by_family[family] = item
    mcp = authoritative.get("mcp")
    if not isinstance(mcp, dict) or mcp.get("top_level_tool_count") != 25 or mcp.get("provider_or_registry_changed") is not False:
        raise BatchM3Error("authoritative M2 MCP contract changed")
    if _sha256_bytes(manifest_raw) != M2_MANIFEST_SHA256:
        raise BatchM3Error("tracked M2 manifest whole-file SHA-256 mismatch")
    if _sha256_bytes(handoff_raw) != M2_HANDOFF_SHA256:
        raise BatchM3Error("tracked M2 handoff whole-file SHA-256 mismatch")
    manifest_expected = (("manifest_version", M2_MANIFEST_VERSION), ("status", M2_STATUS), ("source_snapshot", SNAPSHOT_ID), ("coverage_level", M2_COVERAGE), ("m3_status", "authorized_not_started"), ("m4_status", "not_started"), ("mcp_tool_count", 25))
    for key, value in manifest_expected:
        _require_equal(manifest.get(key), value, key)
    for key in ("corpus_complete", "product_accepted", "g2_canary_imported"):
        _require_equal(manifest.get(key), False, key)
    manifest_families = manifest.get("families")
    if not isinstance(manifest_families, list) or [item.get("family") for item in manifest_families if isinstance(item, dict)] != list(M2_FAMILY_ORDER):
        raise BatchM3Error("M2 manifest family order contract changed")
    for item in manifest_families:
        if not isinstance(item, dict):
            raise BatchM3Error("M2 manifest family entry is not an object")
        _validate_m2_family(item)
    handoff_expected = (("milestone", "M2"), ("report_version", M2_MANIFEST_VERSION), ("status", M2_STATUS), ("coverage_level", M2_COVERAGE), ("m3_status", "authorized_not_started"), ("m4_status", "not_started"))
    for key, value in handoff_expected:
        _require_equal(handoff.get(key), value, key)
    for key in ("corpus_complete", "product_accepted", "schema14_g2_canary_imported", "provider_or_registry_changed"):
        _require_equal(handoff.get(key), False, key)
    _require_equal(handoff.get("schema13_accepted_unchanged"), True, "Schema 13 preservation")
    review = handoff.get("review")
    if not isinstance(review, dict) or review.get("verdict") != "PASS" or review.get("m3_authorized") is not True or review.get("blockers") != []:
        raise BatchM3Error("M2 review gate contract changed")
    handoff_families = handoff.get("families")
    if not isinstance(handoff_families, list) or [item.get("family") for item in handoff_families if isinstance(item, dict)] != list(M2_FAMILY_ORDER):
        raise BatchM3Error("M2 handoff family order contract changed")
    for item in handoff_families:
        if not isinstance(item, dict):
            raise BatchM3Error("M2 handoff family entry is not an object")
        _validate_m2_family(item, handoff=True)
        family = item["family"]
        authoritative_projection = auth_by_family[family]["projection_kind"]
        artifact_metadata = item["artifact"]["metadata"]
        spec_metadata = item["spec"]["metadata"]
        handoff_projection = spec_metadata.get("projection_kind") or artifact_metadata.get("projection_kind")
        if family == "avatar_codex_archive":
            # The Avatar adapter predates the projection_kind field; its
            # family-local metadata is the independent shape oracle.
            family_metadata = artifact_metadata.get("family_metadata")
            if not isinstance(family_metadata, dict) or family_metadata.get("logical_identity_created") is not False:
                raise BatchM3Error("Avatar M2 handoff family projection contract changed")
            handoff_projection = "full_family_specific_pilot"
        if handoff_projection != authoritative_projection:
            raise BatchM3Error(f"{family} handoff/authoritative projection kind mismatch")
    families = []
    for item in manifest_families:
        family = item["family"]
        authoritative_family = auth_by_family[family]
        _require_equal(item.get("family_contract_version"), authoritative_family.get("family_contract_version"), f"{family} authoritative contract")
        _require_equal(item.get("spec_digest"), EXPECTED_M2_FAMILIES[family]["spec_digest"], f"{family} generated spec digest")
        families.append({"family": family, "family_contract_version": item["family_contract_version"], "ingest_run_id": item["ingest_run_id"], "parser_build_id": item["parser_build_id"], "coverage_level": M2_REPLAY_COVERAGE[family], "projection_kind": authoritative_family["projection_kind"], "corpus_complete": False, "product_accepted": False, "tables": item["tables"], "read_set": item["read_set"], "write_set": item["write_set"], "source": "tracked M2 family shard manifest"})
    projection_families = [item for item in families if item["projection_kind"] != "family_specific_replay_comparison_digest"]
    replay_families = [item for item in families if item["projection_kind"] == "family_specific_replay_comparison_digest"]
    return {"status": "available", "manifest_version": manifest["manifest_version"], "families": families, "projection_family_count": len(projection_families), "replay_comparison_family_count": len(replay_families), "product_accepted": False, "mcp_tool_count": 25, "manifest_sha256": M2_MANIFEST_SHA256, "handoff_sha256": M2_HANDOFF_SHA256, "authoritative_manifest": M2_AUTHORITATIVE_RELATIVE, "authoritative_manifest_sha256": M2_AUTHORITATIVE_MANIFEST_SHA256}


def _current_space(repo_root: Path) -> dict[str, Any]:
    # Explicit roots make the inventory auditable while keeping this a bounded
    # project-space walk rather than a source parser/denominator scan.
    tmp_roots = batch_space.discover_project_tmp_roots("/tmp")
    measured = batch_space.inventory(repo_root, tmp_roots, include_convention_roots=True)
    result = measured.to_dict()
    # M3's own three output files are derived views of this report.  Excluding
    # them keeps a fresh build and a later --check on the same inputs stable,
    # while all source/cache and other project files remain in the gate.
    excluded: list[str] = []
    logical_removed = allocated_removed = files_removed = 0
    for relative in (REPORT_NAME, MANIFEST_NAME, MARKDOWN_NAME):
        output = repo_root / "reports" / "batch-m3" / relative
        try:
            info = output.lstat()
        except FileNotFoundError:
            continue
        if not output.is_file() or output.is_symlink():
            continue
        logical_removed += int(info.st_size)
        blocks = int(getattr(info, "st_blocks", 0)) * 512
        allocated_removed += blocks if blocks else int(info.st_size)
        files_removed += 1
        excluded.append(str(output))
    if files_removed:
        result["logical_bytes"] -= logical_removed
        result["allocated_bytes"] -= allocated_removed
        for root in result["roots"]:
            if root["path"] == str(repo_root):
                root["logical_bytes"] -= logical_removed
                root["allocated_bytes"] -= allocated_removed
                root["file_count"] -= files_removed
                break
    result.update({"status": "available", "gate": "blocker" if result["allocated_bytes"] >= PROJECT_SPACE_LIMIT_BYTES else "pass", "measurement_source": "current_batch_space_inventory", "accounting_roots": [root["path"] for root in result["roots"]], "excluded_derived_outputs": excluded})
    result["warning"] = result["allocated_bytes"] >= PROJECT_SPACE_WARNING_BYTES
    result["over_limit"] = result["allocated_bytes"] >= PROJECT_SPACE_LIMIT_BYTES
    return result

def _validate_r1_handoff(
    repo_root: Path,
    source_root: Path,
    handoff_path: str | Path,
) -> dict[str, Any]:
    """Reconstruct and independently verify the real M3 R1 family wave."""

    handoff_file = Path(handoff_path).absolute()
    handoff, handoff_raw = _load_json_with_raw(handoff_file, "M3 R1 handoff")
    if handoff_raw != canonical_bytes(handoff):
        raise BatchM3Error("M3 R1 handoff is not byte-exact canonical JSON")
    for key, value in (
        ("report_version", "batch-m3-r1-family-projections-v1"),
        ("milestone", "M3"),
        ("coverage_level", "R1_structured_family"),
        ("corpus_complete", False),
        ("product_accepted", False),
        ("mcp_tool_count", 25),
        ("provider_or_registry_changed", False),
        ("schema13_accepted_unchanged", True),
    ):
        _require_equal(handoff.get(key), value, f"R1 handoff {key}")
    if handoff.get("status") != "pending_sol_review":
        raise BatchM3Error("M3 R1 handoff status is not pending_sol_review")
    source = handoff.get("source")
    if not isinstance(source, dict):
        raise BatchM3Error("M3 R1 handoff source binding is missing")
    for key, value in (("commit", SOURCE_COMMIT), ("tree", SOURCE_TREE), ("snapshot_id", SNAPSHOT_ID)):
        _require_equal(source.get(key), value, f"R1 handoff source {key}")
    families = handoff.get("families")
    if not isinstance(families, list) or len(families) < BROAD_R1_FAMILY_MIN:
        raise BatchM3Error("M3 R1 handoff has fewer than the 10-family minimum")
    names = [item.get("family") for item in families if isinstance(item, dict)]
    if len(names) != len(families) or len(set(names)) != len(names):
        raise BatchM3Error("M3 R1 handoff family set is not unique")
    try:
        verification = verify_r1_wave(handoff, source_root)
    except (R1ProjectionError, OSError) as exc:
        raise BatchM3Error(f"M3 R1 independent verification failed: {exc}") from exc
    if verification.get("status") != "verified" or verification.get("projection_family_count") != len(families):
        raise BatchM3Error("M3 R1 independent verification count mismatch")
    verified_by_family = {item["family"]: item for item in verification["families"]}
    try:
        expected_specs = {
            spec.family: spec
            for spec in load_r1_specs(source_root, names)
        }
    except (R1ProjectionError, OSError) as exc:
        raise BatchM3Error(f"M3 R1 family spec reconstruction failed: {exc}") from exc
    if set(expected_specs) != set(names):
        raise BatchM3Error("M3 R1 reconstructed family set does not match handoff")
    normalized: list[dict[str, Any]] = []
    for item in sorted(families, key=lambda value: str(value["family"])):
        family = str(item["family"])
        spec = expected_specs.get(family)
        if spec is None:
            raise BatchM3Error(f"M3 R1 family spec missing: {family}")
        artifact = item.get("artifact")
        if not isinstance(artifact, dict):
            raise BatchM3Error(f"M3 R1 artifact binding missing: {family}")
        metadata = artifact.get("metadata")
        if not isinstance(metadata, dict):
            raise BatchM3Error(f"M3 R1 artifact metadata missing: {family}")
        family_metadata = metadata.get("family_metadata")
        if family_metadata != dict(spec.metadata):
            raise BatchM3Error(f"M3 R1 reconstructed family metadata mismatch: {family}")
        if item.get("logical_identity_created") is not False or spec.metadata.get("logical_identity_created") is not False:
            raise BatchM3Error(f"M3 R1 logical identity marker changed: {family}")
        verified = verified_by_family.get(family)
        if verified is None or verified.get("status") != "verified":
            raise BatchM3Error(f"M3 R1 family verification missing: {family}")
        expected_tables: list[dict[str, Any]] = []
        for table in spec.tables:
            ordered = table.ordered_rows()
            expected_tables.append({
                "table_name": table.name,
                "schema_sql_sha256": schema_digest(table.create_sql),
                "row_count": len(ordered),
                "rowset_sha256": rowset_digest(ordered),
                "ordering": list(range(len(table.columns))),
                "write_set_member": table.name in spec.write_set,
            })
        if artifact.get("tables") != expected_tables:
            raise BatchM3Error(f"M3 R1 artifact table digest mismatch: {family}")
        expected_verified_tables = [
            {
                "table_name": table["table_name"],
                "row_count": table["row_count"],
                "rowset_sha256": table["rowset_sha256"],
            }
            for table in expected_tables
        ]
        if verified.get("tables") != expected_verified_tables:
            raise BatchM3Error(f"M3 R1 verified table closure mismatch: {family}")
        normalized.append({
            "family": spec.family,
            "family_contract_version": spec.family_contract_version,
            "ingest_run_id": spec.ingest_run_id,
            "parser_build_id": spec.parser_build_id,
            "coverage_level": spec.coverage_level,
            "projection_kind": spec.metadata["projection_kind"],
            "corpus_complete": spec.corpus_complete,
            "product_accepted": spec.product_accepted,
            "tables": [table.name for table in spec.tables],
            "read_set": list(spec.read_set),
            "write_set": list(spec.write_set),
            "source": "M3 R1 family projection handoff",
            "source_file": spec.metadata["source_file"],
            "source_file_sha256": spec.metadata["source_file_sha256"],
            "row_count": spec.metadata["source_row_count"],
            "key_observation_fields": list(spec.metadata["key_observation_fields"]),
            "spec_digest": spec_digest(spec),
            "artifact": {
                "path": verified["path"],
                "size_bytes": verified["size_bytes"],
                "sha256": verified["sha256"],
            },
            "verification": verified,
            "logical_identity_created": False,
        })
    manifest_file = handoff_file.with_name("batch-m3-r1-manifest.json")
    manifest, manifest_raw = _load_json_with_raw(manifest_file, "M3 R1 manifest")
    if manifest_raw != canonical_bytes(manifest) or manifest != portable_r1_manifest(handoff):
        raise BatchM3Error("M3 R1 portable manifest does not match handoff")
    return {
        "status": "verified",
        "handoff_path": str(handoff_file),
        "manifest_path": str(manifest_file),
        "manifest_sha256": _sha256_bytes(manifest_raw),
        "projection_family_count": len(normalized),
        "families": normalized,
        "verification": verification,
    }


def _build_legacy_report(
    repo_root: str | Path,
    source_root: str | Path,
    r1_handoff_path: str | Path | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    source = Path(source_root).resolve()
    m0, m0_binding = _validate_m0(repo)
    m2 = _validate_m2(repo)
    r1_wave = None
    if r1_handoff_path is not None:
        r1_wave = _validate_r1_handoff(repo, source, r1_handoff_path)
    r1 = dict(m2)
    if r1_wave is not None:
        r1["projection_family_count"] = m2["projection_family_count"] + r1_wave["projection_family_count"]
        r1["batch_projection_family_count"] = r1_wave["projection_family_count"]
        r1["family_projection_wave"] = r1_wave
    denominator = m0["source_denominator"]
    file_level = denominator["file_level"]
    record_level = denominator["record_level"]
    r0_file = {
        "status": "complete",
        "coverage_complete": file_level["coverage_complete"],
        "file_count": file_level["file_count"],
        "total_bytes": file_level["total_bytes"],
        "git_inventory_sha256": file_level["git_inventory_sha256"],
        "physical_file_manifest_sha256": file_level["physical_file_manifest_sha256"],
    }
    r0_record = {
        "status": "incomplete",
        "coverage_complete": record_level["coverage_complete"],
        "coverage_note": record_level["coverage_note"],
        "files_with_addressable_physical_occurrences": record_level["files_with_addressable_physical_occurrences"],
        "physical_top_level_occurrences": record_level["physical_top_level_occurrences"],
        "status_counts": record_level["status_counts"],
    }
    unsupported = {key: m0_binding["unsupported"][key] for key in M0_UNSUPPORTED_ATOMS}
    space = _current_space(repo)
    pin = source_pin_status(source)
    blocked = pin["status"] != "matched" or space["gate"] == "blocker"
    result = {
        "report_kind": "batch_m3_r0_inventory_r1_wave_status",
        "report_version": REPORT_VERSION,
        "status": "blocked" if blocked else "pending_sol_review",
        "blocker_definition": BLOCKER_DEFINITION,
        "source_pin": {"commit": SOURCE_COMMIT, "tree": SOURCE_TREE, "snapshot_id": SNAPSHOT_ID, "roots": list(M0_SOURCE_ROOTS)},
        "source_checkout": pin,
        "m0_binding": m0_binding,
        "coverage_levels": {
            "R0_raw_physical": {"file_level": r0_file, "record_level": r0_record, "unsupported": unsupported},
            "R1_structured_family": r1,
            "R2_accepted_product": {"status": "not_started", "product_accepted": False, "reason": "M4 wave release is not part of M3"},
        },
        "families": m2["families"] + ([] if r1_wave is None else r1_wave["families"]),
        "project_space": space,
        "identity_policy": "raw_evidence_identity_is_not_logical_identity; no cross-family merge",
        "mcp_tool_count": 25,
        "g2_canary_imported": False,
        "limitations": [
            "M3 consumes the tracked M0 denominator; it does not rescan or invent missing source records.",
            "R0 file completeness and record completeness are separate.",
            "M2 replay comparisons remain comparison evidence; only explicit source-bound family shards count toward the R1 projection target.",
            "R1 family projections are non-product evidence and do not create providers or MCP tools.",
        ],
        "stop_conditions": {
            "denominator_changed": False,
            "source_pin_mismatch": pin["status"] != "matched",
            "unsupported_not_listed": False,
            "project_space_over_40_gib": space["gate"] == "blocker",
            "r2_claimed_without_wave_release": False,
        },
    }
    if r1_wave is not None:
        result["r1_handoff_path"] = r1_wave["handoff_path"]
    return result

def verify_report(
    report_path: str | Path,
    markdown_path: str | Path,
    repo_root: str | Path,
    source_root: str | Path | None = None,
    manifest_path: str | Path | None = None,
    artifact_path: str | Path | None = None,
    r1_handoff_path: str | Path | None = None,
) -> dict[str, Any]:
    report_value, report_raw = _load_json_with_raw(Path(report_path), "M3 report")
    if report_raw != canonical_bytes(report_value):
        raise BatchM3Error("M3 JSON report is not byte-exact canonical JSON")
    source = Path(source_root) if source_root is not None else Path(repo_root) / "cache/upstream-agd-git"
    referenced_artifact_path = artifact_path if artifact_path is not None else report_value.get("artifact_path")
    referenced_r1_handoff = r1_handoff_path if r1_handoff_path is not None else report_value.get("r1_handoff_path")
    if referenced_artifact_path is not None:
        artifact_file = Path(str(referenced_artifact_path))
        if not artifact_file.is_file() or artifact_file.is_symlink():
            raise BatchM3Error("M3 R0 artifact referenced by report is unavailable")
        expected = build_report(repo_root, source, artifact_file, r1_handoff_path=referenced_r1_handoff)
    else:
        expected = build_report(repo_root, source, r1_handoff_path=referenced_r1_handoff)
    # Timings are observational measurements, not artifact identity. Preserve
    # the recorded values while reconstructing all deterministic report fields.
    if "timings" in report_value and "timings" in expected:
        expected["timings"] = report_value["timings"]
    # The elapsed verifier time is recorded only under the observational
    # timings object; physical_artifact is an identity binding and must stay
    # free of wall-clock values.
    if report_value != expected:
        raise BatchM3Error("M3 report does not match deterministic reconstruction")
    if Path(markdown_path).read_text(encoding="utf-8") != render_markdown(report_value):
        raise BatchM3Error("M3 markdown report mismatch")
    manifest_file = Path(manifest_path) if manifest_path is not None else Path(report_path).with_name(MANIFEST_NAME)
    manifest_value, manifest_raw = _load_json_with_raw(manifest_file, "M3 manifest")
    if manifest_raw != canonical_bytes(manifest_value) or manifest_value != build_manifest(expected):
        raise BatchM3Error("M3 manifest does not match deterministic reconstruction")
    return {"status": "verified", "report_status": report_value["status"], "report_sha256": _sha256_bytes(report_raw), "manifest_sha256": _sha256_bytes(manifest_raw)}


# ---------------------------------------------------------------------------
# M3 R0 physical-occurrence artifact
# ---------------------------------------------------------------------------
# The physical scanner owns the source walk, SQLite shape, deterministic
# occurrence order, and source-bound verifier.  Keep these small facades for
# callers that historically imported the inventory module; there must be only
# one implementation of the artifact contract.


def build_physical_artifact(
    source_root: str | Path,
    artifact_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    selected_roots: Sequence[str] | None = None,
    source_roots: Sequence[str] | None = None,
    temp_root: str | Path | None = None,
    invocation_id: str | None = None,
    parser_build_id: str = PHYSICAL_PARSER_BUILD_ID,
    ingest_run_id: str = PHYSICAL_INGEST_RUN_ID,
    snapshot_id: str = SNAPSHOT_ID,
) -> dict[str, Any]:
    """Compatibility facade for the canonical standalone R0 scanner."""
    if selected_roots is not None and source_roots is not None:
        if tuple(selected_roots) != tuple(source_roots):
            raise BatchM3Error("selected_roots and source_roots disagree")
    roots = selected_roots if selected_roots is not None else source_roots
    # The historical API allowed an omitted destination and returned an
    # invocation-owned temporary artifact.  Preserve that convenience while
    # delegating all actual work to batch_m3_physical.
    if artifact_path is None:
        owned_root = Path(temp_root).absolute() if temp_root is not None else Path(
            tempfile.mkdtemp(prefix="batch-m3-r0-inventory-", dir="/tmp")
        )
        artifact_path = owned_root / PHYSICAL_ARTIFACT_NAME
        temp_root = owned_root
    try:
        return build_narrow_r0_artifact(
            source_root,
            artifact_path,
            repo_root=repo_root,
            selected_roots=roots,
            temp_root=temp_root,
            invocation_id=invocation_id,
            snapshot_id=snapshot_id,
            parser_build_id=parser_build_id,
            ingest_run_id=ingest_run_id,
        )
    except BatchM3PhysicalError as exc:
        raise BatchM3Error(str(exc)) from exc


# Friendly alias retained for older batch orchestration callers.
build_r0_physical_artifact = build_physical_artifact


def verify_physical_artifact(
    artifact_path: str | Path,
    *,
    source_root: str | Path | None = None,
    selected_roots: Sequence[str] | None = None,
    expected_run_id: str = PHYSICAL_INGEST_RUN_ID,
    expected_snapshot_id: str = SNAPSHOT_ID,
    expected_parser_build_id: str = PHYSICAL_PARSER_BUILD_ID,
) -> dict[str, Any]:
    """Verify the canonical R0 artifact and optionally bind it to source bytes."""
    started = time.monotonic()
    try:
        result = verify_narrow_r0_artifact(
            artifact_path,
            source_root=source_root,
            selected_roots=selected_roots,
            snapshot_id=expected_snapshot_id,
            parser_build_id=expected_parser_build_id,
            ingest_run_id=expected_run_id,
        )
    except BatchM3PhysicalError as exc:
        raise BatchM3Error(f"M3 R0 artifact verification failed: {exc}") from exc
    metadata = result.get("metadata", {})
    metadata_roots = metadata.get("source_roots")
    if metadata_roots is None:
        metadata_roots = selected_roots or M0_SOURCE_ROOTS
    roots = tuple(metadata_roots)
    if tuple(roots) != tuple(M0_SOURCE_ROOTS):
        scope = "bounded_selected_roots"
    else:
        scope = "full_five_root_boundary"
    return {
        "schema_version": metadata.get("schema_version", PHYSICAL_ARTIFACT_SCHEMA_VERSION),
        "snapshot_id": result["snapshot_id"],
        "parser_build_id": result["parser_build_id"],
        "ingest_run_id": result["ingest_run_id"],
        "coordinate_contract": metadata.get("coordinate_contract", "pair_ordinal_path_v1"),
        "scope": scope,
        "owner": "batch-m3-r0-physical",
        "portable_path": None,
        "selected_roots": list(roots),
        "file_count": int(result["file_denominator"]),
        "file_bytes": int(result["file_bytes"]),
        "record_denominator": int(result["record_denominator"]),
        "record_numerator": int(result["record_numerator"]),
        "coverage_percent": float(result["coverage_percent"]),
        "record_status_counts": dict(result["unsupported_counts"]),
        "unsupported_counts": dict(result["unsupported_counts"]),
        "artifact_size_bytes": int(result["artifact_size_bytes"]),
        "artifact_sha256": result["artifact_sha256"],
        "source_bound": source_root is not None,
        "verification_seconds": round(time.monotonic() - started, 6),
    }

def _augment_report_with_artifact(
    report: dict[str, Any],
    artifact: Mapping[str, Any],
    *,
    selected_roots: Sequence[str] | None = None,
) -> dict[str, Any]:
    r0 = report["coverage_levels"]["R0_raw_physical"]
    verifier_seconds = artifact.get("verification_seconds")
    artifact_binding = dict(artifact)
    artifact_binding.pop("verification_seconds", None)
    r0["physical_artifact"] = artifact_binding
    r0["scope"] = artifact.get("scope") or ("full_five_root_boundary" if tuple(selected_roots or M0_SOURCE_ROOTS) == tuple(M0_SOURCE_ROOTS) else "bounded_selected_roots")
    r0["file_level"].update({
        "status": "complete",
        "coverage_complete": True,
        "file_count": artifact["file_count"],
        "total_bytes": artifact["file_bytes"],
    })
    denominator = int(artifact["record_denominator"])
    numerator = int(artifact["record_numerator"])
    coverage_percent = round((100.0 * numerator / denominator) if denominator else 100.0, 6)
    target_result = "pass" if coverage_percent >= R0_RECORD_TARGET_MIN_PERCENT else "blocker"
    r0["record_level"].update({
        "status": "incomplete",
        "physical_coverage_complete": numerator == denominator,
        "semantic_coverage_complete": False,
        "record_denominator": denominator,
        "record_numerator": numerator,
        "coverage_percent": coverage_percent,
        "target_90_95_percent": {
            "minimum_percent": R0_RECORD_TARGET_MIN_PERCENT,
            "maximum_percent": R0_RECORD_TARGET_MAX_PERCENT,
            "result": target_result,
            "note": "physical occurrence capture target; semantic family coverage remains separate",
        },
    })
    r0["unsupported"] = {
        key: int(artifact["record_status_counts"].get(key, 0))
        for key in M0_UNSUPPORTED_ATOMS
    }
    r0["unsupported_remainder"] = {
        "file_status_counts": dict(sorted(artifact["record_status_counts"].items())),
        "unparsed_container": r0["unsupported"]["unparsed_container"],
        "unsupported_record_layout": r0["unsupported"]["unsupported_record_layout"],
        "physical_members_addressable_record_semantics_unsupported": r0["unsupported"]["physical_members_addressable_record_semantics_unsupported"],
    }
    r1 = report["coverage_levels"]["R1_structured_family"]
    projection_count = int(r1["projection_family_count"])
    shortfall = max(0, BROAD_R1_FAMILY_MIN - projection_count)
    r1.update({
        "r1_projection_family_count": projection_count,
        "r1_projection_target": {
            "minimum_family_count": BROAD_R1_FAMILY_MIN,
            "maximum_family_count": BROAD_R1_FAMILY_MAX,
            "shortfall": shortfall,
            "status": "blocked" if shortfall else "met",
        },
        "status": "blocked" if shortfall else "pending_sol_review",
        "scope_blocker": (
            f"R1 projection family count {projection_count} is below the 10-family minimum."
            if shortfall else None
        ),
    })
    report["r1_projection_family_count"] = projection_count
    report["r1_projection_target_shortfall"] = shortfall
    report["artifact_path"] = artifact.get("path")
    report["artifact_size_bytes"] = artifact.get("artifact_size_bytes")
    report["artifact_sha256"] = artifact.get("artifact_sha256")
    report["artifact_binding"] = {
        "path": artifact.get("path"),
        "owner": "batch-m3-r0-physical",
        "portable_path": None,
        "portable": False,
        "snapshot_id": artifact.get("snapshot_id"),
        "parser_build_id": artifact.get("parser_build_id"),
        "ingest_run_id": artifact.get("ingest_run_id"),
    }
    report["timings"] = {
        "r0_scan_build_seconds": None,
        "r0_source_bound_verifier_seconds": verifier_seconds,
        "report_build_seconds": None,
    }
    report["status"] = "blocked" if report.get("status") == "blocked" or shortfall or target_result == "blocker" else "pending_sol_review"
    report["stop_conditions"]["r1_projection_target_shortfall"] = shortfall > 0
    report["stop_conditions"]["r0_record_target_below_90_percent"] = target_result == "blocker"
    report["limitations"] = [
        "R0 physical coverage is an occurrence inventory, not family structure or semantic interpretation.",
        "Unsupported and unparsed containers remain explicit file-level evidence and are not guessed into records.",
        "R1 replay comparisons are not projections; only source-bound family shards count toward the 10-family R1 target.",
        "R1 shards are non-product evidence and do not register providers or MCP tools.",
    ]
    return report


def _validate_source_bound_r0_binding(
    artifact: Mapping[str, Any],
    m0_binding: Mapping[str, Any],
    *,
    selected_roots: Sequence[str] | None = None,
) -> None:
    """Require a source-bound R0 artifact to cover exactly the M0 boundary."""

    expected_roots = tuple(M0_SOURCE_ROOTS)
    if selected_roots is not None and tuple(selected_roots) != expected_roots:
        raise BatchM3Error("source-bound R0 selected_roots must equal the exact M0 five-root boundary")
    actual_roots = artifact.get("selected_roots")
    if not isinstance(actual_roots, (list, tuple)) or tuple(actual_roots) != expected_roots:
        raise BatchM3Error("source-bound R0 artifact selected_roots do not match the exact M0 five-root boundary")
    if artifact.get("scope") != "full_five_root_boundary":
        raise BatchM3Error("source-bound R0 artifact scope is not the exact M0 five-root boundary")
    if artifact.get("source_bound") is not True:
        raise BatchM3Error("R0 artifact is not source-bound")

    file_level = m0_binding.get("file_level")
    record_level = m0_binding.get("record_level")
    if not isinstance(file_level, Mapping) or not isinstance(record_level, Mapping):
        raise BatchM3Error("tracked M0 denominator binding is incomplete")
    expected_file_count = file_level.get("file_count")
    expected_file_bytes = file_level.get("total_bytes")
    expected_records = record_level.get("physical_top_level_occurrences")
    expected_status_counts = record_level.get("status_counts")
    checks = (
        (artifact.get("file_count"), expected_file_count, "file_count"),
        (artifact.get("file_bytes"), expected_file_bytes, "file_bytes"),
        (artifact.get("record_denominator"), expected_records, "record denominator"),
        (artifact.get("record_numerator"), expected_records, "record numerator"),
    )
    for actual, expected, label in checks:
        if actual != expected:
            raise BatchM3Error(f"source-bound R0 artifact {label} does not match tracked M0 binding")
    for key in ("record_status_counts", "unsupported_counts"):
        if artifact.get(key) != expected_status_counts:
            raise BatchM3Error(f"source-bound R0 artifact {key} do not match tracked M0 binding")


def build_report(
    repo_root: str | Path,
    source_root: str | Path,
    artifact_path: str | Path | None = None,
    *,
    selected_roots: Sequence[str] | None = None,
    r1_handoff_path: str | Path | None = None,
) -> dict[str, Any]:
    report = _build_legacy_report(repo_root, source_root, r1_handoff_path=r1_handoff_path)
    if artifact_path is None:
        # Compatibility/read-only view used by older contract tests. The M3
        # CLI always supplies a freshly scanned artifact.
        report["r1_projection_family_count"] = report["coverage_levels"]["R1_structured_family"]["projection_family_count"]
        report["r1_projection_target_shortfall"] = max(0, BROAD_R1_FAMILY_MIN - report["r1_projection_family_count"])
        return report
    if selected_roots is not None and tuple(selected_roots) != tuple(M0_SOURCE_ROOTS):
        raise BatchM3Error("source-bound R0 selected_roots must equal the exact M0 five-root boundary")
    artifact = verify_physical_artifact(
        artifact_path,
        source_root=source_root,
        selected_roots=M0_SOURCE_ROOTS,
        expected_run_id=PHYSICAL_INGEST_RUN_ID,
        expected_snapshot_id=SNAPSHOT_ID,
        expected_parser_build_id=PHYSICAL_PARSER_BUILD_ID,
    )
    artifact["path"] = str(Path(artifact_path).absolute())
    _validate_source_bound_r0_binding(
        artifact,
        report["m0_binding"],
        selected_roots=selected_roots,
    )
    return _augment_report_with_artifact(report, artifact, selected_roots=selected_roots)


def render_markdown(report: Mapping[str, Any]) -> str:
    # Preserve the compact legacy rendering for metadata-only contract tests.
    if "physical_artifact" not in report["coverage_levels"]["R0_raw_physical"]:
        r0 = report["coverage_levels"]["R0_raw_physical"]
        rec = r0["record_level"]
        return "\n".join([
            "# Batch M3 R0 inventory / R1 wave status", "",
            f"Status: `{report['status']}` (non-product)", "",
            f"Snapshot: `{report['source_pin']['snapshot_id']}`",
            f"R0 file coverage: `{r0['file_level']['status']}` ({r0['file_level']['file_count']} files)",
            f"R0 record coverage: `{rec['status']}` ({rec['physical_top_level_occurrences']} addressable occurrences)", "",
            "Unsupported/unparsed counts:", "",
            *[f"- `{key}`: {r0['unsupported'][key]}" for key in sorted(r0['unsupported'])], "",
            f"R1 family shards: {len(report['families'])}; R2 product: `not_started`.",
            f"Project space: {report['project_space']['allocated_bytes']} allocated bytes / {report['project_space']['limit_bytes']} limit ({report['project_space']['gate']}).", "",
            f"Blocker definition: {report['blocker_definition']}", "",
        ])
    r0 = report["coverage_levels"]["R0_raw_physical"]
    rec = r0["record_level"]
    target = rec["target_90_95_percent"]
    r1 = report["coverage_levels"]["R1_structured_family"]
    artifact = r0["physical_artifact"]
    lines = [
        "# Batch M3 R0 physical inventory / R1 wave status", "",
        f"Status: `{report['status']}` (non-product; M4: `not_started`)", "",
        f"Snapshot: `{report['source_pin']['snapshot_id']}`",
        f"R0 boundary: `{r0['scope']}`; files `{r0['file_level']['file_count']}` / `{r0['file_level']['total_bytes']}` bytes",
        f"R0 record coverage: `{rec['record_numerator']}` / `{rec['record_denominator']}` = `{rec['coverage_percent']}%` (90–95% target: `{target['result']}`)",
        f"Physical artifact: `{artifact.get('path')}`; `{artifact.get('artifact_size_bytes')}` bytes; SHA-256 `{artifact.get('artifact_sha256')}`",
        f"R0 timings: scan/build `{report.get('timings', {}).get('r0_scan_build_seconds')}` s; source-bound verify `{report.get('timings', {}).get('r0_source_bound_verifier_seconds')}` s.",
        "",
        "Unsupported/unparsed remainder:", "",
        *[f"- `{key}`: {r0['unsupported'][key]}" for key in sorted(r0['unsupported'])],
        "",
        f"R1 projection families: `{r1['r1_projection_family_count']}`; target `10-20`; shortfall `{r1['r1_projection_target']['shortfall']}`.",
        f"R1 status: `{r1['status']}`; R2 product: `not_started`.",
        f"Project space: {report['project_space']['allocated_bytes']} allocated bytes / {report['project_space']['limit_bytes']} limit ({report['project_space']['gate']}).",
        "",
        f"Blocker definition: {report['blocker_definition']}", "",
    ]
    return "\n".join(lines)


def build_manifest(report: Mapping[str, Any]) -> dict[str, Any]:
    r1 = report["coverage_levels"]["R1_structured_family"]
    result = {
        "manifest_version": "batch-m3-r0-r1-wave-v1",
        "milestone": "M3",
        "status": report["status"],
        "coverage_level": "R0_raw_physical_plus_R1_structured_family",
        "corpus_complete": False,
        "product_accepted": False,
        "source_snapshot": report["source_pin"]["snapshot_id"],
        "m0_report": M0_RELATIVE,
        "m2_manifest": M2_RELATIVE,
        "m2_authoritative_manifest": M2_AUTHORITATIVE_RELATIVE,
        "m2_authoritative_manifest_sha256": M2_AUTHORITATIVE_MANIFEST_SHA256,
        "families": report["families"],
        "projection_family_count": r1["projection_family_count"],
        "replay_comparison_family_count": r1["replay_comparison_family_count"],
        "r1_projection_family_count": r1.get("r1_projection_family_count", r1["projection_family_count"]),
        "r1_projection_target_shortfall": r1.get("r1_projection_target", {}).get("shortfall", max(0, BROAD_R1_FAMILY_MIN-r1["projection_family_count"])),
        "project_space_gate": report["project_space"],
        "m4_status": "not_started",
        "g2_canary_imported": False,
        "mcp_tool_count": 25,
    }
    if "physical_artifact" in report["coverage_levels"]["R0_raw_physical"]:
        r0 = report["coverage_levels"]["R0_raw_physical"]
        artifact = r0["physical_artifact"]
        result.update({
            "physical_artifact": artifact,
            "r0_scope": r0["scope"],
            "r0_file_count": r0["file_level"]["file_count"],
            "r0_record_denominator": r0["record_level"]["record_denominator"],
            "r0_record_numerator": r0["record_level"]["record_numerator"],
            "r0_record_coverage_percent": r0["record_level"]["coverage_percent"],
            "r0_record_target": r0["record_level"]["target_90_95_percent"],
            "blocker_definition": report["blocker_definition"],
        })
    return result


__all__ = [
    "ARTIFACT_LIMIT_BYTES", "ARTIFACT_RESERVATION_BYTES", "BROAD_R1_FAMILY_MAX", "BROAD_R1_FAMILY_MIN",
    "BatchM3Error", "M2_AUTHORITATIVE_MANIFEST_SHA256", "MANIFEST_NAME", "MARKDOWN_NAME", "REPORT_NAME",
    "PHYSICAL_ARTIFACT_NAME", "PHYSICAL_ARTIFACT_SCHEMA_VERSION", "PHYSICAL_INGEST_RUN_ID", "PHYSICAL_PARSER_BUILD_ID",
    "build_manifest", "build_physical_artifact", "build_r0_physical_artifact", "build_report", "canonical_bytes",
    "render_markdown", "verify_physical_artifact", "verify_report",
]
