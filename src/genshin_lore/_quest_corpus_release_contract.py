"""Sealed AQ corpus manifest and build-report release contract.

This module has no SQLite dependency.  It turns the verified master/child/
TextMap manifests into the exact inventory and immutable identifiers expected
by the read-only database audit.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import LoreDatabaseError, ManifestError
from .quest_corpus_evidence import (
    MAPPING_VERSION_CORPUS,
    PARSER_VERSION_CORPUS,
    PROJECTION_VERSION_CORPUS,
    R6_MEDIUM_CORPUS_MANIFEST_ID,
    r5_parser_implementation_sha256,
)
from .quest_corpus_manifest import (
    CandidateTalkEntry,
    VerifiedQuestCorpusManifestSet,
    load_and_verify_quest_corpus_manifest,
)
from .source import load_manifest, snapshot_id
from .util import canonical_json, digest_id, sha256_text


LANGUAGES = ("CHS", "EN", "JP")
AQ_QUEST_IDS = tuple(range(7000, 7015))
AQ_MASTER_MANIFEST_ID = "anime-game-data-7.0-aq-7000-7014"
R5_MASTER_MANIFEST_ID = "anime-game-data-7.0-r5-quest-71008-pilot"
R6_MEDIUM_MASTER_MANIFEST_ID = R6_MEDIUM_CORPUS_MANIFEST_ID
R5_QUEST_IDS = (71008,)
R6_MEDIUM_QUEST_IDS = (393, 3027, 6027, 71008, 73000, 74025)
AQ_IDENTITY_GROUP_METADATA_JSON = '{"projection":"talk_evidence_v1"}'

# These are the immutable boundaries of the named AQ release.  They are
# checked in addition to the manifest's own invariants so replacing the
# manifest bytes cannot silently redefine the operational release.
AQ_RELEASE_COUNTS = {
    "quest_count": 15,
    "total_talk_references": 149,
    "unique_talk_candidates": 149,
    "expected_occurrences": 3327,
    "expected_edges": 3315,
    "expected_texts_per_language": 3327,
    "local_edges": 3312,
    "external_pointers": 3,
    "dangling_pointers": 0,
    "resolved_texts": 9931,
    "text_counts": {"CHS": 3313, "EN": 3309, "JP": 3309},
    "missing_texts": 50,
}

R5_MANIFEST_COUNTS = {
    "quest_count": 1,
    "total_talk_references": 60,
    "unique_talk_candidates": 60,
    "expected_occurrences": 130,
    "expected_edges": 70,
    "expected_texts_per_language": 130,
    "expected_branch_nodes": 0,
    "expected_merge_nodes": 0,
}

# R6 is a new, explicit medium candidate boundary.  These invariants are
# intentionally sealed here in addition to the generated manifest so that a
# replacement manifest cannot silently widen the reviewed scope.
R6_MEDIUM_MANIFEST_COUNTS = {
    "quest_count": 6,
    "total_talk_references": 231,
    "unique_talk_candidates": 231,
    "expected_occurrences": 2970,
    "expected_edges": 2861,
    "expected_texts_per_language": 2970,
    "expected_branch_nodes": 127,
    "expected_merge_nodes": 108,
}


def parser_fingerprint(manifest_id: str) -> str:
    if manifest_id == AQ_MASTER_MANIFEST_ID:
        return "quest-corpus-evidence-7000-7014"
    if manifest_id == R5_MASTER_MANIFEST_ID:
        return "quest-corpus-evidence-r5-quest-71008-pilot"
    if manifest_id == R6_MEDIUM_MASTER_MANIFEST_ID:
        return "quest-corpus-evidence-r6-medium-quest-talk"
    raise verification_error(f"unsupported quest corpus manifest_id: {manifest_id!r}")


class QuestCorpusVerificationError(LoreDatabaseError):
    """A sealed corpus release gate could not be verified."""


def verification_error(
    message: str,
    cause: BaseException | None = None,
) -> QuestCorpusVerificationError:
    error = QuestCorpusVerificationError(message)
    if cause is not None:
        error.__cause__ = cause
    return error


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise verification_error(f"cannot read {label}: {path}: {exc}") from exc
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise verification_error(f"invalid JSON in {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise verification_error(f"{label} must be a JSON object: {path}")
    return value


def is_int(value: Any) -> bool:
    return type(value) is int


def is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise verification_error(f"{label} must be a JSON object")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise verification_error(f"{label} must be a JSON array")
    return value


def require_report_value(report: dict[str, Any], path: str, expected: Any) -> None:
    value: Any = report
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise verification_error(f"build report missing required field {path}")
        value = value[part]
    if value != expected:
        raise verification_error(
            f"build report {path} mismatch: expected {expected!r}, got {value!r}"
        )


def require_build_report_shape(report: dict[str, Any]) -> None:
    required = {
        "status",
        "source_snapshot",
        "master_manifest",
        "audit_cache_sha256",
        "parser_build_id",
        "ingest_run_id",
        "corpus_config_checksum",
        "selected_paths",
        "selected_path_count",
        "quest_count",
        "zero_reference_quest_ids",
        "reference_counts",
        "evidence_container_count",
        "occurrence_count",
        "occurrence_edge_count",
        "graph_projection",
        "text_hydration",
        "legacy_writes",
        "scope",
    }
    missing = sorted(required - set(report))
    if missing:
        raise verification_error(f"build report missing required fields: {missing}")
    if report.get("status") != "complete":
        raise verification_error(
            f"build report status must be 'complete', got {report.get('status')!r}"
        )
    snapshot = require_mapping(report["source_snapshot"], "build report source_snapshot")
    for key in ("snapshot_id", "source_id", "release_label", "commit", "tree"):
        if not isinstance(snapshot.get(key), str) or not snapshot[key]:
            raise verification_error(
                f"build report source_snapshot.{key} must be a non-empty string"
            )
    master = require_mapping(report["master_manifest"], "build report master_manifest")
    for key in ("manifest_id", "raw_sha256"):
        if not isinstance(master.get(key), str) or not master[key]:
            raise verification_error(f"build report master_manifest.{key} is missing or invalid")
    if not is_sha256(report["audit_cache_sha256"]):
        raise verification_error("build report audit_cache_sha256 must be a lowercase SHA-256")
    for key in ("parser_build_id", "ingest_run_id", "corpus_config_checksum"):
        if not isinstance(report.get(key), str) or not report[key]:
            raise verification_error(f"build report {key} must be a non-empty string")
    paths = require_list(report["selected_paths"], "build report selected_paths")
    if not all(isinstance(path, str) and path for path in paths):
        raise verification_error("build report selected_paths must contain non-empty strings")
    if not is_int(report["selected_path_count"]):
        raise verification_error("build report selected_path_count must be an integer")
    if report["selected_path_count"] != len(paths):
        raise verification_error("build report selected_path_count does not match selected_paths")
    for key in (
        "quest_count",
        "evidence_container_count",
        "occurrence_count",
        "occurrence_edge_count",
        "branch_node_count",
        "merge_node_count",
        "source_file_count",
        "source_record_count",
    ):
        if key in report and not is_int(report[key]):
            raise verification_error(f"build report {key} must be an integer")
    if not all(is_int(value) for value in require_list(report["zero_reference_quest_ids"], "build report zero_reference_quest_ids")):
        raise verification_error("build report zero_reference_quest_ids must contain integers")


@dataclass(frozen=True)
class ReferenceExpectation:
    quest_id: int
    quest_path: str
    index: int
    namespace: str
    talk_id: str
    candidate_path: str | None
    bundle_manifest_id: str
    bundle_manifest_checksum: str
    candidate: CandidateTalkEntry | None
    candidates: tuple[CandidateTalkEntry, ...] = ()


@dataclass(frozen=True)
class CorpusExpectation:
    manifest_set: VerifiedQuestCorpusManifestSet
    source_manifest: dict[str, Any]
    snapshot_id: str
    master_manifest_sha256: str
    audit_cache_sha256: str
    selected_paths: tuple[str, ...]
    quest_paths: tuple[str, ...]
    talk_paths: tuple[str, ...]
    textmap_paths: tuple[str, ...]
    quest_ids: tuple[int, ...]
    zero_reference_quest_ids: tuple[int, ...]
    references: tuple[ReferenceExpectation, ...]
    candidate_by_path: dict[str, CandidateTalkEntry]
    candidate_talk_id_by_path: dict[str, str]
    config_checksum: str
    parser_build_id: str
    ingest_run_id: str


@dataclass(frozen=True)
class IdentityScopeExpectation:
    groups: tuple[tuple[str, str, str, str, str], ...]
    memberships: tuple[tuple[str, str, str], ...]


def build_identity_scope_expectation(
    expected: CorpusExpectation,
) -> IdentityScopeExpectation:
    """Derive projected Talk identity rows from sealed manifest coordinates."""

    namespace_by_path: dict[str, str] = {}
    for reference in expected.references:
        for candidate in reference.candidates:
            prior = namespace_by_path.setdefault(candidate.relative_path, reference.namespace)
            if prior != reference.namespace:
                raise verification_error("candidate path has conflicting namespaces")
    if set(namespace_by_path) != set(expected.talk_paths):
        raise verification_error(
            "sealed AQ identity projection paths do not exactly match Talk paths"
        )

    groups: dict[str, tuple[str, str, str, str, str]] = {}
    memberships: set[tuple[str, str, str]] = set()
    for relative_path in expected.talk_paths:
        namespace = namespace_by_path[relative_path]
        talk_id = expected.candidate_talk_id_by_path[relative_path]
        source_file_id = digest_id("sf", expected.snapshot_id, relative_path)
        source_record_id = digest_id("sr", source_file_id, "/")
        container_id = digest_id("ec", expected.ingest_run_id, source_record_id)
        group_id = digest_id(
            "ig", expected.ingest_run_id, namespace, talk_id
        )
        group_row = (
            group_id,
            expected.ingest_run_id,
            namespace,
            talk_id,
            AQ_IDENTITY_GROUP_METADATA_JSON,
        )
        prior = groups.setdefault(group_id, group_row)
        if prior != group_row:
            raise verification_error(
                f"sealed AQ identity_group contract conflict for {group_id}"
            )
        memberships.add(
            (group_id, expected.ingest_run_id, container_id)
        )
    return IdentityScopeExpectation(
        groups=tuple(sorted(groups.values())),
        memberships=tuple(sorted(memberships)),
    )


def manifest_root_for(corpus_manifest_path: Path, manifest_root: str | Path | None) -> Path:
    if manifest_root is not None:
        return Path(manifest_root).resolve()
    current = corpus_manifest_path.parent
    for _ in range(5):
        if (current / "manifests").is_dir():
            return current
        if current.parent == current:
            break
        current = current.parent
    return corpus_manifest_path.parent.resolve()


def _load_source_manifest_for(
    root: Path,
    manifest_set: VerifiedQuestCorpusManifestSet,
) -> dict[str, Any]:
    snapshot = manifest_set.corpus_manifest.source_snapshot
    source_dir = root / "manifests" / "sources"
    candidates: list[Path] = []
    preferred = source_dir / f"anime-game-data-{snapshot.release_label}.json"
    if preferred.is_file():
        candidates.append(preferred)
    if source_dir.is_dir():
        candidates.extend(path for path in sorted(source_dir.glob("*.json")) if path not in candidates)
    for path in (root / "source-manifest.json", root / "manifest.json"):
        if path.is_file() and path not in candidates:
            candidates.append(path)
    for path in candidates:
        try:
            source = load_manifest(path)
        except ManifestError:
            continue
        if (
            source.get("release_label") == snapshot.release_label
            and source.get("commit") == snapshot.commit
            and source.get("tree") == snapshot.tree
            and source.get("upstream") == snapshot.upstream
        ):
            return source
    raise verification_error(
        "no source manifest matches the sealed corpus snapshot "
        f"{snapshot.release_label}/{snapshot.commit}/{snapshot.tree} under {root}"
    )


def build_expectation(
    corpus_manifest_path: Path,
    manifest_root: str | Path | None,
) -> CorpusExpectation:
    root = manifest_root_for(corpus_manifest_path, manifest_root)
    try:
        manifest_set = load_and_verify_quest_corpus_manifest(
            corpus_manifest_path,
            manifest_root=root,
        )
    except (ManifestError, OSError, ValueError) as exc:
        raise verification_error(f"corpus manifest verification failed: {exc}") from exc

    master = manifest_set.corpus_manifest
    source_manifest = _load_source_manifest_for(root, manifest_set)
    source_snapshot = master.source_snapshot
    actual_snapshot_id = snapshot_id(source_manifest)
    supported_ids = {
        AQ_MASTER_MANIFEST_ID: (AQ_QUEST_IDS, AQ_RELEASE_COUNTS),
        R5_MASTER_MANIFEST_ID: (R5_QUEST_IDS, R5_MANIFEST_COUNTS),
        R6_MEDIUM_MASTER_MANIFEST_ID: (R6_MEDIUM_QUEST_IDS, R6_MEDIUM_MANIFEST_COUNTS),
    }
    if master.scalable_scope is not None:
        supported_ids[master.manifest_id] = (tuple(master.scalable_scope["quest_selection"]), master.corpus_invariants.as_dict())
    if master.manifest_id not in supported_ids:
        raise verification_error(f"unsupported quest corpus manifest_id: {master.manifest_id!r}")
    if source_manifest.get("source_id") != "anime-game-data":
        raise verification_error(
            "supported quest corpus must bind to source_id 'anime-game-data', "
            f"got {source_manifest.get('source_id')!r}"
        )
    for field in ("release_label", "commit", "tree", "upstream"):
        if getattr(source_snapshot, field) != source_manifest.get(field):
            raise verification_error(f"source/corpus {field} mismatch")
    quest_ids = tuple(bundle.quest.quest_id for bundle in manifest_set.quest_candidate_bundles)
    expected_quest_ids, expected_manifest_counts = supported_ids[master.manifest_id]
    if quest_ids != expected_quest_ids:
        raise verification_error(
            f"{master.manifest_id} quest IDs mismatch: expected {list(expected_quest_ids)}, "
            f"got {list(quest_ids)}"
        )
    for key, expected_value in expected_manifest_counts.items():
        if key in {"local_edges", "external_pointers", "dangling_pointers", "resolved_texts", "text_counts", "missing_texts"}:
            continue
        if getattr(master.corpus_invariants, key) != expected_value:
            raise verification_error(
                f"sealed {master.manifest_id} invariant {key} mismatch: "
                f"expected {expected_value}, got {getattr(master.corpus_invariants, key)}"
            )

    quest_paths: list[str] = []
    talk_paths: list[str] = []
    textmap_paths = [entry.relative_path for entry in manifest_set.textmap_manifest.textmaps]
    references: list[ReferenceExpectation] = []
    candidate_by_path: dict[str, CandidateTalkEntry] = {}
    candidate_talk_id_by_path: dict[str, str] = {}
    for bundle in manifest_set.quest_candidate_bundles:
        quest_paths.append(bundle.quest.relative_path)
        bundle_ref = next(item for item in master.quest_bundles if item.quest_id == bundle.quest.quest_id)
        for index, ref in enumerate(bundle.references):
            if master.scalable_scope is None and len(ref.candidates) != 1:
                raise verification_error(f"historical reference {index} is not single-candidate")
            for candidate in ref.candidates:
                if candidate.relative_path not in candidate_by_path:
                    talk_paths.append(candidate.relative_path)
                    candidate_by_path[candidate.relative_path] = candidate
                    candidate_talk_id_by_path[candidate.relative_path] = str(ref.talk_id)
                elif candidate_by_path[candidate.relative_path] != candidate:
                    raise verification_error(f"candidate manifest metadata drift for {candidate.relative_path}")
            candidate = ref.candidates[0] if len(ref.candidates) == 1 else None
            references.append(
                ReferenceExpectation(
                    quest_id=bundle.quest.quest_id,
                    quest_path=bundle.quest.relative_path,
                    index=index,
                    namespace=ref.namespace,
                    talk_id=str(ref.talk_id),
                    candidate_path=candidate.relative_path if candidate else None,
                    bundle_manifest_id=bundle.manifest_id,
                    bundle_manifest_checksum=bundle_ref.raw_sha256,
                    candidate=candidate,
                    candidates=ref.candidates,
                )
            )
    selected_paths = tuple(quest_paths + talk_paths + textmap_paths)
    zero_refs = tuple(bundle.quest.quest_id for bundle in manifest_set.quest_candidate_bundles if not bundle.references)
    audit_values = {bundle.audit.normalized_index_sha256 for bundle in manifest_set.quest_candidate_bundles}
    if len(audit_values) != 1:
        raise verification_error("quest child manifests do not share one audit cache SHA-256")
    audit_cache_sha256 = next(iter(audit_values))
    master_sha = hashlib.sha256(corpus_manifest_path.read_bytes()).hexdigest()
    config_data = {
        "audit_cache_sha256": audit_cache_sha256,
        "child_manifest_hashes": [item.raw_sha256 for item in master.quest_bundles],
        "languages": list(LANGUAGES),
        "mapping_version": MAPPING_VERSION_CORPUS,
        "master_manifest_sha256": master_sha,
        "parser_version": PARSER_VERSION_CORPUS,
        "projection_version": PROJECTION_VERSION_CORPUS,
        "source_snapshot": {
            "commit": source_snapshot.commit,
            "release_label": source_snapshot.release_label,
            "snapshot_id": actual_snapshot_id,
            "source_id": source_manifest["source_id"],
            "tree": source_snapshot.tree,
            "upstream": source_snapshot.upstream,
        },
        "textmap_hydration": "v1",
        "textmap_manifest_sha256": master.textmap_manifest.raw_sha256,
    }
    if master.scalable_scope is not None or master.manifest_id in {R5_MASTER_MANIFEST_ID, R6_MEDIUM_MASTER_MANIFEST_ID}:
        config_data["parser_implementation_sha256"] = r5_parser_implementation_sha256()
    config_checksum = sha256_text(canonical_json(config_data))
    parser_id = digest_id("parser-build", actual_snapshot_id, PARSER_VERSION_CORPUS, config_checksum)
    run_id = digest_id("ingest-run", actual_snapshot_id, parser_id, master_sha, config_checksum)
    return CorpusExpectation(
        manifest_set=manifest_set,
        source_manifest=source_manifest,
        snapshot_id=actual_snapshot_id,
        master_manifest_sha256=master_sha,
        audit_cache_sha256=audit_cache_sha256,
        selected_paths=selected_paths,
        quest_paths=tuple(quest_paths),
        talk_paths=tuple(talk_paths),
        textmap_paths=tuple(textmap_paths),
        quest_ids=quest_ids,
        zero_reference_quest_ids=zero_refs,
        references=tuple(references),
        candidate_by_path=candidate_by_path,
        candidate_talk_id_by_path=candidate_talk_id_by_path,
        config_checksum=config_checksum,
        parser_build_id=parser_id,
        ingest_run_id=run_id,
    )


def verify_report_binding(report: dict[str, Any], expected: CorpusExpectation) -> None:
    master = expected.manifest_set.corpus_manifest
    snapshot = report["source_snapshot"]
    source = expected.source_manifest
    for key, value in (
        ("snapshot_id", expected.snapshot_id),
        ("source_id", source["source_id"]),
        ("release_label", source["release_label"]),
        ("commit", source["commit"]),
        ("tree", source["tree"]),
    ):
        if snapshot.get(key) != value:
            raise verification_error(
                f"build report source_snapshot.{key} mismatch: expected {value!r}, got {snapshot.get(key)!r}"
            )
    if "build" in snapshot and snapshot["build"] != source.get("build"):
        raise verification_error("build report source_snapshot.build does not match source manifest")
    if report["master_manifest"].get("manifest_id") != master.manifest_id:
        raise verification_error("build report master manifest_id does not match corpus manifest")
    if report["master_manifest"].get("raw_sha256") != expected.master_manifest_sha256:
        raise verification_error("build report master manifest raw_sha256 does not match corpus bytes")
    if report["audit_cache_sha256"] != expected.audit_cache_sha256:
        raise verification_error("build report audit_cache_sha256 does not match sealed child manifests")
    for key, value in (
        ("parser_build_id", expected.parser_build_id),
        ("ingest_run_id", expected.ingest_run_id),
        ("corpus_config_checksum", expected.config_checksum),
    ):
        if report[key] != value:
            raise verification_error(f"build report {key} does not match sealed config")
    if report["selected_paths"] != list(expected.selected_paths):
        raise verification_error("build report selected_paths do not match sealed inventory")
    scope = require_mapping(report["scope"], "build report scope")
    if scope.get("selected_paths") != list(expected.selected_paths):
        raise verification_error("build report scope.selected_paths do not match sealed inventory")
    if scope.get("full_scan") is not False:
        raise verification_error("build report scope.full_scan must be false")
    for key in ("fts_rebuilt", "embedding", "semantic_search"):
        if scope.get(key) is not False:
            raise verification_error(f"build report scope.{key} must be false")
    require_report_value(report, "selected_path_count", len(expected.selected_paths))
    require_report_value(report, "quest_count", len(expected.quest_ids))
    require_report_value(report, "zero_reference_quest_ids", list(expected.zero_reference_quest_ids))
    if report.get("branch_node_count") != master.corpus_invariants.expected_branch_nodes:
        raise verification_error("build report branch_node_count does not match corpus manifest")
    if report.get("merge_node_count") != master.corpus_invariants.expected_merge_nodes:
        raise verification_error("build report merge_node_count does not match corpus manifest")
    legacy = report.get("legacy_writes")
    if not isinstance(legacy, dict) or any(
        legacy.get(key) != 0 for key in ("logical_talk", "talk_document", "passage", "passage_edge")
    ):
        raise verification_error("build report legacy_writes must prove zero legacy writes")


__all__ = [
    "AQ_MASTER_MANIFEST_ID",
    "AQ_IDENTITY_GROUP_METADATA_JSON",
    "AQ_QUEST_IDS",
    "AQ_RELEASE_COUNTS",
    "R5_MASTER_MANIFEST_ID",
    "R5_QUEST_IDS",
    "R5_MANIFEST_COUNTS",
    "R6_MEDIUM_MASTER_MANIFEST_ID",
    "R6_MEDIUM_QUEST_IDS",
    "R6_MEDIUM_MANIFEST_COUNTS",
    "LANGUAGES",
    "CorpusExpectation",
    "IdentityScopeExpectation",
    "QuestCorpusVerificationError",
    "ReferenceExpectation",
    "build_identity_scope_expectation",
    "build_expectation",
    "is_int",
    "load_json_object",
    "manifest_root_for",
    "parser_fingerprint",
    "require_build_report_shape",
    "require_mapping",
    "require_report_value",
    "verification_error",
    "verify_report_binding",
]
