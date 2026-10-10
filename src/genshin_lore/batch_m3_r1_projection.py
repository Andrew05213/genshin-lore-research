"""Batch M3 family-specific R1 JSON projections.

The shared shard protocol only serialises rows supplied by an explicit family
contract.  This module therefore declares ten ordinary JSON families one by
one, keeps a lexical source record and physical locator for every row, and
never turns a native field into a logical identity or cross-family join.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .batch_m2_verifier import M2VerificationError, verify_shard
from .batch_m0_baseline import SOURCE_COMMIT, SOURCE_ROOTS, SOURCE_SNAPSHOT_ID, SOURCE_TREE
from .batch_space import ProjectSpaceGate, SpaceGateError, ensure_owned_tmp_root, new_invocation_id
from .family_projection_protocol import FamilyShardSpec, TableSpec, canonical_row, spec_digest
from .shard_builder import ShardBuildError, build_shard


SNAPSHOT_ID = SOURCE_SNAPSHOT_ID
R1_COVERAGE = "R1_structured_family"
OWNER = "batch-m3-r1-family-projection"
ARTIFACT_LIMIT_BYTES = 4 * 1024**3
PARSER_BUILD_PREFIX = "pb-batch-m3-r1-family"
INGEST_RUN_PREFIX = "ir-batch-m3-r1-family"
NEXT_IDENTITY_CONTRACT = "batch-m3-r1-family-identity-v2"


class R1ProjectionError(RuntimeError):
    """An explicit family projection cannot be reconstructed or verified."""


@dataclass(frozen=True)
class FamilyDefinition:
    family: str
    family_contract_version: str
    source_file: str
    key_observation_fields: tuple[str, ...]
    boundary: str

    @property
    def table_name(self) -> str:
        return f"{self.family}_row_occurrence"

    @property
    def parser_build_id(self) -> str:
        return f"{PARSER_BUILD_PREFIX}:{self.family}:{SOURCE_COMMIT}"

    @property
    def ingest_run_id(self) -> str:
        return f"{INGEST_RUN_PREFIX}:{self.family}:{SOURCE_COMMIT}"

    def next_parser_build_id(self, implementation_sha256: str, contract_sha256: str) -> str:
        """Derive a future parser identity without rewriting the accepted v1 run."""
        return derive_parser_build_id_v2(self.family, implementation_sha256, contract_sha256)

    def next_ingest_run_id(
        self, parser_build_id: str, config_sha256: str, source_seal_sha256: str,
    ) -> str:
        return derive_ingest_run_id_v2(
            self.family, parser_build_id, config_sha256, source_seal_sha256
        )


# These are deliberately separate declarations, even though the mechanical
# row shape is shared.  The fields are raw observations only; they are not
# logical IDs and are never used to merge evidence between families.
FAMILY_DEFINITIONS: tuple[FamilyDefinition, ...] = (
    FamilyDefinition("activity_archive", "activity-archive-evidence-v1", "ExcelBinOutput/ActivityExcelConfigData.json", ("ActivityId",), "all ActivityExcelConfigData top-level row occurrences"),
    FamilyDefinition("achievement_archive", "achievement-archive-evidence-v1", "ExcelBinOutput/AchievementExcelConfigData.json", ("id",), "all AchievementExcelConfigData top-level row occurrences"),
    FamilyDefinition("avatar_config_archive", "avatar-config-archive-evidence-v1", "ExcelBinOutput/AvatarExcelConfigData.json", ("id",), "all AvatarExcelConfigData top-level row occurrences; no Character merge"),
    FamilyDefinition("dungeon_archive", "dungeon-archive-evidence-v1", "ExcelBinOutput/DungeonExcelConfigData.json", ("id",), "all DungeonExcelConfigData top-level row occurrences"),
    FamilyDefinition("monster_archive", "monster-archive-evidence-v1", "ExcelBinOutput/MonsterExcelConfigData.json", ("id",), "all MonsterExcelConfigData top-level row occurrences"),
    FamilyDefinition("npc_archive", "npc-archive-evidence-v1", "ExcelBinOutput/NpcExcelConfigData.json", ("id",), "all NpcExcelConfigData top-level row occurrences"),
    FamilyDefinition("scene_archive", "scene-archive-evidence-v1", "ExcelBinOutput/SceneExcelConfigData.json", ("id",), "all SceneExcelConfigData top-level row occurrences"),
    FamilyDefinition("weapon_config_archive", "weapon-config-archive-evidence-v1", "ExcelBinOutput/WeaponExcelConfigData.json", ("id",), "all WeaponExcelConfigData top-level row occurrences; separate from Static weapons contract"),
    FamilyDefinition("reliquary_config_archive", "reliquary-config-archive-evidence-v1", "ExcelBinOutput/ReliquaryExcelConfigData.json", ("id",), "all ReliquaryExcelConfigData top-level row occurrences; separate from Static reliquaries contract"),
    FamilyDefinition("material_config_archive", "material-config-archive-evidence-v1", "ExcelBinOutput/MaterialExcelConfigData.json", ("id",), "all MaterialExcelConfigData top-level row occurrences; separate from Static materials contract"),
)
FAMILY_BY_NAME = {item.family: item for item in FAMILY_DEFINITIONS}


_ROW_DDL_TEMPLATE = """CREATE TABLE {table_name} (
    occurrence_id TEXT PRIMARY KEY NOT NULL,
    family TEXT NOT NULL,
    family_contract_version TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    source_file_path TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL,
    source_ordinal INTEGER NOT NULL CHECK (source_ordinal >= 0),
    raw_record_json TEXT NOT NULL,
    raw_record_sha256 TEXT NOT NULL,
    raw_key_observations_json TEXT NOT NULL,
    record_status TEXT NOT NULL,
    metadata_json TEXT NOT NULL
)"""


def _skip_ws(text: str, position: int) -> int:
    while position < len(text) and text[position] in " \t\r\n":
        position += 1
    return position


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_id(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8", "surrogateescape"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _identity_sha(value: str, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{label} must be a 64-character SHA-256")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{label} must be lowercase hexadecimal SHA-256") from exc
    if value != value.lower():
        raise ValueError(f"{label} must be lowercase hexadecimal SHA-256")
    return value


def derive_parser_build_id_v2(
    family: str, implementation_sha256: str, contract_sha256: str,
) -> str:
    """Bind a new parser identity to implementation and family contract bytes."""
    if family not in FAMILY_BY_NAME:
        raise ValueError(f"unknown Batch M3 family: {family}")
    digest = _digest_id(
        NEXT_IDENTITY_CONTRACT,
        family,
        _identity_sha(implementation_sha256, "implementation_sha256"),
        _identity_sha(contract_sha256, "contract_sha256"),
    )
    return f"{PARSER_BUILD_PREFIX}:v2:{family}:{digest}"


def derive_ingest_run_id_v2(
    family: str,
    parser_build_id: str,
    config_sha256: str,
    source_seal_sha256: str,
) -> str:
    """Bind a new run to parser, configuration, and the exact source seal."""
    if family not in FAMILY_BY_NAME:
        raise ValueError(f"unknown Batch M3 family: {family}")
    if not isinstance(parser_build_id, str) or not parser_build_id:
        raise ValueError("parser_build_id must be a non-empty string")
    digest = _digest_id(
        NEXT_IDENTITY_CONTRACT,
        family,
        parser_build_id,
        _identity_sha(config_sha256, "config_sha256"),
        _identity_sha(source_seal_sha256, "source_seal_sha256"),
    )
    return f"{INGEST_RUN_PREFIX}:v2:{family}:{digest}"


def _pairs_object(pairs: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    # Keep duplicate keys and their order.  Nested objects are represented by
    # the same hook, so raw key observations never collapse a repeated member.
    return pairs


def _iter_array_records(raw: bytes) -> Iterator[tuple[int, str, str, list[tuple[str, Any]] | None, str]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise R1ProjectionError("family source is not UTF-8") from exc
    decoder = json.JSONDecoder(object_pairs_hook=_pairs_object)
    position = _skip_ws(text, 0)
    if position >= len(text) or text[position] != "[":
        raise R1ProjectionError("family source must be a top-level JSON array")
    position += 1
    ordinal = 0
    while True:
        position = _skip_ws(text, position)
        if position < len(text) and text[position] == "]":
            if _skip_ws(text, position + 1) != len(text):
                raise R1ProjectionError("family source has trailing bytes")
            return
        start = position
        try:
            parsed, end = decoder.raw_decode(text, position)
        except json.JSONDecodeError as exc:
            raise R1ProjectionError("family record token boundary is invalid") from exc
        token = text[start:end]
        pointer = f"/{ordinal}"
        if isinstance(parsed, list) and all(isinstance(item, tuple) and len(item) == 2 for item in parsed):
            pairs = parsed
        else:
            pairs = None
        yield ordinal, pointer, token, pairs, _sha256(token.encode("utf-8"))
        ordinal += 1
        position = _skip_ws(text, end)
        if position >= len(text):
            raise R1ProjectionError("family source array is unterminated")
        if text[position] == ",":
            position += 1
            continue
        if text[position] == "]":
            if _skip_ws(text, position + 1) != len(text):
                raise R1ProjectionError("family source has trailing bytes")
            return
        raise R1ProjectionError("family source array delimiter is invalid")


def _git(source_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise R1ProjectionError(result.stderr.strip() or result.stdout.strip() or "source git inspection failed")
    return result.stdout.strip()


def verify_source_pin(source_root: str | Path) -> dict[str, Any]:
    root = Path(source_root).resolve()
    if not root.is_dir() or root.is_symlink():
        raise R1ProjectionError(f"pinned source checkout is unavailable: {root}")
    try:
        clean = not bool(_git(root, "status", "--porcelain=v1", "--untracked-files=all"))
        commit = _git(root, "rev-parse", "HEAD")
        tree = _git(root, "rev-parse", "HEAD^{tree}")
    except (OSError, R1ProjectionError) as exc:
        raise R1ProjectionError(str(exc)) from exc
    if not clean or commit != SOURCE_COMMIT or tree != SOURCE_TREE:
        raise R1ProjectionError(
            f"source pin mismatch: clean={clean} commit={commit} tree={tree}"
        )
    return {"status": "matched", "commit": commit, "tree": tree, "snapshot_id": SNAPSHOT_ID}


def _key_observations(pairs: list[tuple[str, Any]] | None, fields: Sequence[str]) -> list[dict[str, Any]]:
    if pairs is None:
        return []
    result: list[dict[str, Any]] = []
    wanted = set(fields)
    for pair_ordinal, (key, value) in enumerate(pairs):
        if key in wanted:
            result.append({"field": key, "pair_ordinal": pair_ordinal, "raw_value": value})
    return result


def _read_definition(definition: FamilyDefinition, source_root: Path) -> FamilyShardSpec:
    source_path = source_root / definition.source_file
    if not source_path.is_file() or source_path.is_symlink():
        raise R1ProjectionError(f"family source file is unavailable: {source_path}")
    try:
        raw = source_path.read_bytes()
    except OSError as exc:
        raise R1ProjectionError(f"cannot read family source file: {source_path}") from exc
    source_sha256 = _sha256(raw)
    rows: list[tuple[Any, ...]] = []
    for ordinal, pointer, token, pairs, raw_sha256 in _iter_array_records(raw):
        source_record_id = _digest_id(SNAPSHOT_ID, definition.source_file, pointer)
        occurrence_id = _digest_id(
            definition.family, definition.ingest_run_id, definition.source_file,
            pointer, raw_sha256,
        )
        observations = _key_observations(pairs, definition.key_observation_fields)
        rows.append((
            occurrence_id,
            definition.family,
            definition.family_contract_version,
            definition.ingest_run_id,
            SNAPSHOT_ID,
            definition.parser_build_id,
            definition.source_file,
            source_record_id,
            pointer,
            ordinal,
            token,
            raw_sha256,
            json.dumps(observations, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            "raw_observed",
            json.dumps({
                "identity_effect": "physical_occurrence_only",
                "logical_identity_created": False,
                "relations": "not_in_scope",
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        ))
    table = TableSpec(
        name=definition.table_name,
        create_sql=_ROW_DDL_TEMPLATE.format(table_name=definition.table_name),
        columns=(
            "occurrence_id", "family", "family_contract_version", "ingest_run_id",
            "snapshot_id", "parser_build_id", "source_file_path", "source_record_id",
            "source_pointer", "source_ordinal", "raw_record_json", "raw_record_sha256",
            "raw_key_observations_json", "record_status", "metadata_json",
        ),
        rows=tuple(rows),
        order_columns=("source_file_path", "source_ordinal", "source_pointer", "occurrence_id"),
        sort_key=lambda row: (str(row[6]), int(row[9]), str(row[8]), str(row[0])),
        index_sql=(
            f"CREATE INDEX idx_{definition.family}_source_ordinal ON {definition.table_name} (source_file_path,source_ordinal)",
        ),
    )
    return FamilyShardSpec(
        family=definition.family,
        family_contract_version=definition.family_contract_version,
        ingest_run_id=definition.ingest_run_id,
        snapshot_id=SNAPSHOT_ID,
        parser_build_id=definition.parser_build_id,
        coverage_level=R1_COVERAGE,
        corpus_complete=True,
        product_accepted=False,
        read_set=(definition.source_file,),
        write_set=(definition.table_name,),
        tables=(table,),
        metadata={
            "projection_kind": "family_specific_lexical_row_projection_v1",
            "boundary": definition.boundary,
            "source_file": definition.source_file,
            "source_file_sha256": source_sha256,
            "source_row_count": len(rows),
            "key_observation_fields": list(definition.key_observation_fields),
            "raw_record_contract": "lexical_top_level_array_token_v1",
            "relations": "not_in_scope",
            "logical_identity_created": False,
            "cross_family_merge": False,
            "textmap_or_asset_projection": False,
        },
    )


def load_specs(
    source_root: str | Path,
    selected_families: Sequence[str] | None = None,
    *,
    verify_pin: bool = True,
) -> tuple[FamilyShardSpec, ...]:
    root = Path(source_root).resolve()
    if verify_pin:
        verify_source_pin(root)
    requested = tuple(FAMILY_BY_NAME if selected_families is None else selected_families)
    if not requested:
        raise R1ProjectionError("at least one family is required")
    if len(set(requested)) != len(requested):
        raise R1ProjectionError("duplicate family selection")
    unknown = sorted(set(requested) - set(FAMILY_BY_NAME))
    if unknown:
        raise R1ProjectionError(f"family is outside the explicit M3 R1 boundary: {unknown}")
    return tuple(_read_definition(FAMILY_BY_NAME[name], root) for name in sorted(requested))


def _space_gate(repo_root: Path, owned_root: Path, invocation_id: str) -> ProjectSpaceGate:
    try:
        return ProjectSpaceGate(
            repo_root,
            owned_tmp_roots=(owned_root,),
            accounting_tmp_roots=(owned_root,),
            cleanup_tmp_roots=(owned_root,),
            owner=OWNER,
            invocation_id=invocation_id,
            discovery_root="/tmp",
        )
    except SpaceGateError as exc:
        raise R1ProjectionError(str(exc)) from exc


def build_wave(
    source_root: str | Path,
    output_root: str | Path,
    *,
    repo_root: str | Path | None = None,
    selected_families: Sequence[str] | None = None,
    invocation_id: str | None = None,
    verify_pin: bool = True,
) -> dict[str, Any]:
    source = Path(source_root).resolve()
    output = Path(output_root).absolute()
    invocation = invocation_id or new_invocation_id()
    try:
        owned = ensure_owned_tmp_root(output, owner=OWNER, invocation_id=invocation)
    except SpaceGateError as exc:
        raise R1ProjectionError(str(exc)) from exc
    repo = Path(repo_root).resolve() if repo_root is not None else Path(__file__).resolve().parents[2]
    gate = _space_gate(repo, owned, invocation)
    specs = load_specs(source, selected_families, verify_pin=verify_pin)
    families: list[dict[str, Any]] = []
    for spec in specs:
        target = owned / f"{spec.family}.sqlite"
        try:
            gate.check(f"m3-r1-shard:{spec.family}", projected_bytes=128 * 1024**2)
            result = build_shard(spec, target)
            if result["size_bytes"] > ARTIFACT_LIMIT_BYTES:
                raise R1ProjectionError(f"family shard exceeds 4 GiB: {spec.family}")
            verification = verify_shard(target, spec)
        except (ShardBuildError, M2VerificationError, SpaceGateError) as exc:
            raise R1ProjectionError(f"family shard failed: {spec.family}: {exc}") from exc
        families.append({
            "family": spec.family,
            "family_contract_version": spec.family_contract_version,
            "ingest_run_id": spec.ingest_run_id,
            "parser_build_id": spec.parser_build_id,
            "coverage_level": spec.coverage_level,
            "corpus_complete": spec.corpus_complete,
            "product_accepted": spec.product_accepted,
            "source_file": spec.metadata["source_file"],
            "source_file_sha256": spec.metadata["source_file_sha256"],
            "row_count": spec.metadata["source_row_count"],
            "key_observation_fields": spec.metadata["key_observation_fields"],
            "spec_digest": spec_digest(spec),
            "artifact": result,
            "verification": verification,
            "logical_identity_created": False,
        })
    return {
        "report_version": "batch-m3-r1-family-projections-v1",
        "milestone": "M3",
        "status": "pending_sol_review",
        "coverage_level": R1_COVERAGE,
        "corpus_complete": False,
        "product_accepted": False,
        "source": verify_source_pin(source) if verify_pin else {
            "status": "unverified_test_source", "snapshot_id": SNAPSHOT_ID,
        },
        "source_snapshot": SNAPSHOT_ID,
        "projection_family_count": len(families),
        "families": families,
        "identity_policy": "raw_evidence_identity_is_not_logical_identity; no cross-family merge",
        "mcp_tool_count": 25,
        "provider_or_registry_changed": False,
        "schema13_accepted_unchanged": True,
        "output_root": str(owned),
        "invocation_id": invocation,
        "space": gate.report(),
    }



def verify_wave(
    report: Mapping[str, Any],
    source_root: str | Path,
    *,
    verify_pin: bool = True,
) -> dict[str, Any]:
    source = Path(source_root).resolve()
    actual_families = report.get("families")
    if not isinstance(actual_families, list):
        raise R1ProjectionError("R1 report family list is invalid")
    family_names: list[str] = []
    for item in actual_families:
        if not isinstance(item, Mapping) or not isinstance(item.get("family"), str):
            raise R1ProjectionError("R1 report contains an unknown family")
        family = str(item["family"])
        if family not in FAMILY_BY_NAME:
            raise R1ProjectionError("R1 report contains an unknown family")
        family_names.append(family)
    if len(set(family_names)) != len(family_names):
        raise R1ProjectionError("R1 report contains a duplicate family")
    expected_specs = tuple(load_specs(source, [family], verify_pin=verify_pin)[0] for family in family_names)
    by_family = {spec.family: spec for spec in expected_specs}
    verified: list[dict[str, Any]] = []
    for item in actual_families:
        family = str(item["family"])
        spec = by_family[family]
        expected_summary = {
            "family_contract_version": spec.family_contract_version,
            "ingest_run_id": spec.ingest_run_id,
            "parser_build_id": spec.parser_build_id,
            "coverage_level": spec.coverage_level,
            "corpus_complete": spec.corpus_complete,
            "product_accepted": spec.product_accepted,
            "source_file": spec.metadata["source_file"],
            "source_file_sha256": spec.metadata["source_file_sha256"],
            "row_count": spec.metadata["source_row_count"],
            "key_observation_fields": spec.metadata["key_observation_fields"],
            "spec_digest": spec_digest(spec),
        }
        for key, expected in expected_summary.items():
            if item.get(key) != expected:
                raise R1ProjectionError(f"R1 handoff metadata changed: {family}.{key}")
        artifact = item.get("artifact")
        if not isinstance(artifact, Mapping) or not isinstance(artifact.get("path"), str):
            raise R1ProjectionError(f"R1 artifact binding is missing: {family}")
        path = Path(str(artifact["path"]))
        try:
            result = verify_shard(path, spec)
        except (M2VerificationError, ShardBuildError) as exc:
            raise R1ProjectionError(f"R1 shard verification failed: {family}: {exc}") from exc
        if result.get("path") != artifact.get("path"):
            raise R1ProjectionError(f"R1 artifact path changed: {family}")
        if result.get("sha256") != artifact.get("sha256") or result.get("size_bytes") != artifact.get("size_bytes"):
            raise R1ProjectionError(f"R1 artifact identity changed: {family}")
        for key in ("family", "ingest_run_id", "coverage_level", "corpus_complete", "product_accepted"):
            expected = spec.family if key == "family" else getattr(spec, key)
            if result.get(key) != expected:
                raise R1ProjectionError(f"R1 shard metadata changed: {family}.{key}")
        metadata = artifact.get("metadata")
        if not isinstance(metadata, Mapping):
            raise R1ProjectionError(f"R1 artifact metadata is missing: {family}")
        for key in ("family", "family_contract_version", "ingest_run_id", "parser_build_id", "coverage_level", "corpus_complete", "product_accepted", "spec_digest"):
            expected = spec.family if key == "family" else (spec_digest(spec) if key == "spec_digest" else getattr(spec, key))
            if metadata.get(key) != expected:
                raise R1ProjectionError(f"R1 artifact metadata changed: {family}.{key}")
        family_metadata = metadata.get("family_metadata")
        if not isinstance(family_metadata, Mapping):
            raise R1ProjectionError(f"R1 family metadata is missing: {family}")
        for key in ("source_file", "source_file_sha256", "source_row_count", "key_observation_fields"):
            if family_metadata.get(key) != spec.metadata[key]:
                raise R1ProjectionError(f"R1 family metadata changed: {family}.{key}")
        verified.append({
            "family": family,
            "status": "verified",
            "path": result["path"],
            "sha256": result["sha256"],
            "size_bytes": result["size_bytes"],
            "row_count": spec.metadata["source_row_count"],
            "tables": result.get("tables", []),
        })
    if int(report.get("projection_family_count", -1)) != len(expected_specs):
        raise R1ProjectionError("R1 projection family count is not deterministic")
    return {"status": "verified", "projection_family_count": len(verified), "families": verified}


def portable_manifest(report: Mapping[str, Any]) -> dict[str, Any]:
    families = []
    for item in report.get("families", []):
        if not isinstance(item, Mapping):
            raise R1ProjectionError("invalid R1 family report entry")
        families.append({
            key: item[key]
            for key in (
                "family", "family_contract_version", "ingest_run_id", "parser_build_id",
                "coverage_level", "corpus_complete", "product_accepted", "source_file",
                "source_file_sha256", "row_count", "key_observation_fields", "spec_digest",
                "logical_identity_created",
            )
        })
    return {
        "manifest_version": "batch-m3-r1-family-projections-v1",
        "milestone": "M3",
        "status": report.get("status"),
        "source_snapshot": SNAPSHOT_ID,
        "source_commit": SOURCE_COMMIT,
        "source_tree": SOURCE_TREE,
        "coverage_level": R1_COVERAGE,
        "corpus_complete": False,
        "product_accepted": False,
        "projection_family_count": len(families),
        "families": families,
        "identity_policy": "raw_evidence_identity_is_not_logical_identity; no cross-family merge",
        "mcp_tool_count": 25,
        "provider_or_registry_changed": False,
        "schema13_accepted_unchanged": True,
    }


__all__ = [
    "ARTIFACT_LIMIT_BYTES", "FAMILY_DEFINITIONS", "FamilyDefinition", "R1ProjectionError",
    "SNAPSHOT_ID", "build_wave", "load_specs", "portable_manifest", "spec_digest",
    "verify_source_pin", "verify_wave",
]
