"""Sealed AQ 7000-7014 Quest Evidence Corpus Builder (Phase 3.2B).

Builds a complete, multi-Quest, multilingual (CHS/EN/JP) evidence corpus on the
existing database schema using sealed manifests from Phase 3.2A.

Architecture Principles:
    - Raw Evidence Identity != Logical Identity
    - Quest is family-specific document identity (agd:quest:{id}).
    - Talk remains raw evidence container (ec:...).
    - Passage remains run/snapshot-scoped occurrence (po:... / occ:...).
    - Checksum only seals bytes/projection, not semantic identity.
    - Zero legacy writes (no logical_talk, talk document, passage, passage_edge).
    - Single atomic database transaction.
    - Fail-closed preflight without database side-effects.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .db import Database
from .discovery import TalkCandidate, TalkDiscoveryIndex, load_talk_discovery_index_cache
from .errors import ManifestError
from .parsers import deob_quest_generic, deob_quest_talk_reference, deob_talk_generic
from .quest_corpus_manifest import (
    CandidateTalkEntry,
    QuestCandidateBundleManifest,
    QuestFileEntry,
    QuestTalkReference,
    TextMapAssetManifest,
    TextMapEntry,
    VerifiedQuestCorpusManifestSet,
    load_and_verify_quest_corpus_manifest,
)
from .quest_resolution import (
    CandidateManifestEntry,
    CandidateScopeManifest,
    QuestEvidenceResolutionLink,
    link_quest_reference_to_evidence,
)
from .source import LocatedFile, SourceBundle, load_manifest, snapshot_id
from .talk_evidence import project_talk_root
from .talk_textmap import (
    LANGUAGES,
    ResolvedNodeText,
    parse_textmap_json_with_duplicates,
    resolve_talk_textmap,
)
from .util import canonical_json, digest_id, sha256_bytes, sha256_text


PARSER_VERSION_CORPUS = "quest-corpus-evidence-v1"
MAPPING_VERSION_CORPUS = "quest-corpus-evidence-v1"
PROJECTION_VERSION_CORPUS = "quest-corpus-occurrence-v1"
AQ_CORPUS_MANIFEST_ID = "anime-game-data-7.0-aq-7000-7014"
R5_CORPUS_MANIFEST_ID = "anime-game-data-7.0-r5-quest-71008-pilot"
R6_MEDIUM_CORPUS_MANIFEST_ID = "anime-game-data-7.0-r6-medium-quest-talk"
R5_REPRODUCIBLE_TIMESTAMP = "2026-09-09T00:00:00+00:00"
R6_REPRODUCIBLE_TIMESTAMP = "2026-09-10T00:00:00+00:00"


R5_QUEST_CORPUS_MANIFEST_IDS = frozenset({
    R5_CORPUS_MANIFEST_ID,
    R6_MEDIUM_CORPUS_MANIFEST_ID,
})


def r5_parser_implementation_sha256() -> str:
    """Bind new R5 parser identity to every implementation module it executes."""
    digest = hashlib.sha256()
    for name in (
        "mapping.py", "parsers.py", "quest_corpus_manifest.py",
        "quest_resolution.py", "talk_evidence.py", "talk_textmap.py",
        "quest_corpus_evidence.py", "quest_talk_scope.py", "discovery.py",
    ):
        digest.update(name.encode("utf-8")); digest.update(b"\0")
        digest.update(Path(__file__).with_name(name).read_bytes()); digest.update(b"\0")
    return digest.hexdigest()


@dataclass(frozen=True)
class PreflightCorpusCandidate:
    """Preflight-verified candidate Talk file."""

    candidate_spec: CandidateTalkEntry
    audit_candidate: TalkCandidate
    located: LocatedFile
    raw_bytes: bytes
    raw_talk: dict[str, Any]
    norm_talk: dict[str, Any]
    dialog_list: list[dict[str, Any]]
    node_count: int
    edge_count: int
    branch_count: int
    merge_count: int
    node_ids: tuple[int, ...]
    native_id: int


@dataclass(frozen=True)
class PreflightCorpusReference:
    """Preflight-verified raw Quest talk reference."""

    ref_spec: QuestTalkReference
    index: int
    pointer: str
    namespace: str
    talk_id: int
    raw_reference: dict[str, Any]
    deob_reference: dict[str, Any]
    ref_scope: CandidateScopeManifest
    candidates: tuple[PreflightCorpusCandidate, ...]


@dataclass(frozen=True)
class PreflightCorpusQuest:
    """Preflight-verified Quest bundle."""

    bundle_manifest: QuestCandidateBundleManifest
    quest_id: int
    located: LocatedFile
    raw_bytes: bytes
    raw_quest: dict[str, Any]
    norm_quest: dict[str, Any]
    references: tuple[PreflightCorpusReference, ...]


@dataclass(frozen=True)
class PreflightTextMapShard:
    """Preflight-verified TextMap shard."""

    entry: TextMapEntry
    located: LocatedFile
    raw_bytes: bytes
    parsed_dict: dict[str, str]
    duplicates: set[str]


@dataclass(frozen=True)
class GraphTargetAudit:
    """Audit record for a raw Talk pointer that leaves its container."""

    source_talk_path: str
    source_node_id: str
    target_node_id: str
    target_candidate_paths: tuple[str, ...]
    classification: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_talk_path": self.source_talk_path,
            "source_node_id": self.source_node_id,
            "target_node_id": self.target_node_id,
            "target_candidate_paths": list(self.target_candidate_paths),
            "classification": self.classification,
        }


def audit_cross_container_targets(
    candidates: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[GraphTargetAudit, ...]:
    """Classify non-local Talk pointers against the complete sealed candidate set.

    This is deliberately an audit-only operation.  It never chooses a target,
    creates an occurrence, or emits an occurrence edge.  Candidate paths are
    retained in full so an ambiguous native node ID remains ambiguous.
    """
    node_paths: dict[str, list[str]] = {}
    for talk_path in sorted(candidates):
        for node in candidates[talk_path]:
            if not isinstance(node, Mapping):
                continue
            node_id = node.get("id")
            if not isinstance(node_id, int) or isinstance(node_id, bool):
                continue
            node_paths.setdefault(str(node_id), []).append(talk_path)

    audits: list[GraphTargetAudit] = []
    for talk_path in sorted(candidates):
        dialog_list = candidates[talk_path]
        local_node_ids = {
            str(node.get("id"))
            for node in dialog_list
            if isinstance(node, Mapping)
            and isinstance(node.get("id"), int)
            and not isinstance(node.get("id"), bool)
        }
        for node in dialog_list:
            if not isinstance(node, Mapping):
                continue
            source_node_id = node.get("id")
            if not isinstance(source_node_id, int) or isinstance(source_node_id, bool):
                continue
            next_dialogs = node.get("nextDialogs", ())
            if not isinstance(next_dialogs, Sequence) or isinstance(next_dialogs, (str, bytes)):
                continue
            for target in next_dialogs:
                if not isinstance(target, int) or isinstance(target, bool):
                    continue
                target_node_id = str(target)
                if target_node_id in local_node_ids:
                    continue
                target_paths = tuple(sorted(node_paths.get(target_node_id, ())))
                if not target_paths:
                    classification = "dangling_target"
                elif len(target_paths) == 1:
                    classification = "external_target"
                else:
                    classification = "ambiguous_target"
                audits.append(
                    GraphTargetAudit(
                        source_talk_path=talk_path,
                        source_node_id=str(source_node_id),
                        target_node_id=target_node_id,
                        target_candidate_paths=target_paths,
                        classification=classification,
                    )
                )
    return tuple(audits)


@dataclass(frozen=True)
class PreflightQuestCorpusResult:
    """Immutable fail-closed preflight result for the AQ 7000-7014 corpus."""

    source_manifest: dict[str, Any]
    manifest_set: VerifiedQuestCorpusManifestSet
    audit_index: TalkDiscoveryIndex
    audit_cache_sha256: str
    snapshot_id_value: str
    corpus_config_checksum: str
    parser_build_id: str
    ingest_run_id: str
    quests: tuple[PreflightCorpusQuest, ...]
    main_shards: dict[str, tuple[dict[str, str], set[str]]]
    med_shards: dict[str, tuple[dict[str, str], set[str]]]
    shard_located_and_bytes: dict[str, tuple[Any, bytes]]
    selected_paths: list[str]
    all_unique_candidates: dict[str, PreflightCorpusCandidate]
    graph_target_audits: tuple[GraphTargetAudit, ...]
    allowed_external_target_ids_by_path: dict[str, tuple[str, ...]]


def _register_source_file(
    db: Database,
    *,
    snapshot_id_value: str,
    located: Any,
    raw: bytes,
) -> str:
    """Register a source file in source_file table."""
    return db.ensure_source_file(
        snapshot_id_value,
        {
            "relative_path": located.relative_path,
            "sha256": sha256_bytes(raw),
            "size_bytes": len(raw),
            "format": "json",
            "root_path": str(located.root),
        },
    )


def _find_manifest_root(
    corpus_manifest_path: str | Path,
    manifest_root: str | Path | None = None,
) -> Path:
    if manifest_root is not None:
        return Path(manifest_root).resolve()
    p = Path(corpus_manifest_path).resolve()
    curr = p.parent
    for _ in range(4):
        if (curr / "manifests").is_dir():
            return curr
        curr = curr.parent
    return p.parent


def preflight_quest_corpus_evidence_bundle(
    source_roots: list[str | Path],
    source_manifest_path: str | Path,
    corpus_manifest_path: str | Path,
    audit_cache_path: str | Path,
    manifest_root: str | Path | None = None,
) -> PreflightQuestCorpusResult:
    """Validate all source provenance, manifests, files, and invariants without DB side-effects."""
    # 1. Load source snapshot manifest
    source_manifest = load_manifest(source_manifest_path)
    snapshot_id_value = snapshot_id(source_manifest)

    # 2. Load and verify three-tier quest corpus manifest set
    resolved_manifest_root = _find_manifest_root(corpus_manifest_path, manifest_root)
    manifest_set = load_and_verify_quest_corpus_manifest(
        corpus_manifest_path, manifest_root=resolved_manifest_root
    )
    corpus_manifest = manifest_set.corpus_manifest
    scalable = corpus_manifest.scalable_scope is not None
    if not scalable and corpus_manifest.manifest_id not in {
        AQ_CORPUS_MANIFEST_ID,
        *R5_QUEST_CORPUS_MANIFEST_IDS,
    }:
        raise ManifestError(
            f"unsupported quest corpus manifest_id: {corpus_manifest.manifest_id!r}"
        )
    textmap_manifest = manifest_set.textmap_manifest

    # Cross-verify source snapshot metadata
    snap = corpus_manifest.source_snapshot
    if snap.commit != source_manifest["commit"]:
        raise ManifestError(
            f"commit mismatch: source manifest {source_manifest['commit']!r} vs corpus manifest {snap.commit!r}"
        )
    if snap.tree != source_manifest["tree"]:
        raise ManifestError(
            f"tree mismatch: source manifest {source_manifest['tree']!r} vs corpus manifest {snap.tree!r}"
        )
    if snap.release_label != source_manifest["release_label"]:
        raise ManifestError(
            f"release_label mismatch: source manifest {source_manifest['release_label']!r} vs corpus manifest {snap.release_label!r}"
        )
    if snap.upstream != source_manifest.get("upstream"):
        raise ManifestError(
            f"upstream mismatch: source manifest {source_manifest.get('upstream')!r} vs corpus manifest {snap.upstream!r}"
        )

    if scalable and corpus_manifest.scalable_scope["snapshot_id"] != snapshot_id_value:
        raise ManifestError("scalable snapshot identity mismatch")

    # 3. Load and strictly verify discovery index audit cache
    raw_audit_cache_bytes = Path(audit_cache_path).read_bytes()
    audit_cache_sha256 = sha256_bytes(raw_audit_cache_bytes)
    for b_manifest in manifest_set.quest_candidate_bundles:
        if b_manifest.audit.normalized_index_sha256 != audit_cache_sha256:
            raise ManifestError(
                f"audit cache SHA-256 mismatch for quest {b_manifest.quest.quest_id}: "
                f"actual {audit_cache_sha256} vs manifest {b_manifest.audit.normalized_index_sha256}"
            )

    audit_index = load_talk_discovery_index_cache(
        audit_cache_path,
        expected_sha256=audit_cache_sha256,
        expected_snapshot=corpus_manifest.source_snapshot,
    )
    audit_cands_by_path: dict[str, TalkCandidate] = {
        cand.relative_path: cand for cand in audit_index.candidates
    }

    # 4. Source bundle
    bundle = SourceBundle(source_roots)

    # 5. Verify TextMap shards
    main_shards: dict[str, tuple[dict[str, str], set[str]]] = {}
    med_shards: dict[str, tuple[dict[str, str], set[str]]] = {}
    shard_located_and_bytes: dict[str, tuple[Any, bytes]] = {}

    for tm_entry in textmap_manifest.textmaps:
        try:
            shard_loc, shard_bytes = bundle.read_bytes(tm_entry.relative_path)
        except Exception as exc:
            raise ManifestError(
                f"Cannot read TextMap shard {tm_entry.relative_path}: {exc}"
            ) from exc

        actual_tm_sha = sha256_bytes(shard_bytes)
        if actual_tm_sha != tm_entry.raw_sha256:
            raise ManifestError(
                f"TextMap shard {tm_entry.relative_path} raw SHA256 mismatch: actual {actual_tm_sha} vs manifest {tm_entry.raw_sha256}"
            )
        if len(shard_bytes) != tm_entry.raw_size_bytes:
            raise ManifestError(
                f"TextMap shard {tm_entry.relative_path} raw size mismatch: actual {len(shard_bytes)} vs manifest {tm_entry.raw_size_bytes}"
            )

        try:
            parsed_dict, dups = parse_textmap_json_with_duplicates(shard_bytes)
        except Exception as exc:
            raise ManifestError(
                f"Cannot parse TextMap shard {tm_entry.relative_path}: {exc}"
            ) from exc

        shard_located_and_bytes[tm_entry.relative_path] = (shard_loc, shard_bytes)
        if tm_entry.tier == "main":
            main_shards[tm_entry.language] = (parsed_dict, dups)
        else:
            med_shards[tm_entry.language] = (parsed_dict, dups)

    # 6. Verify Quests, References, and Talk Candidates
    verified_quests: list[PreflightCorpusQuest] = []
    all_unique_candidates: dict[str, PreflightCorpusCandidate] = {}
    seen_talk_paths: list[str] = []

    total_occurrences_computed = 0
    total_edges_computed = 0
    total_branch_nodes_computed = 0
    total_merge_nodes_computed = 0

    for bundle_ref_entry, bundle_manifest in zip(
        corpus_manifest.quest_bundles, manifest_set.quest_candidate_bundles
    ):
        quest_entry = bundle_manifest.quest
        try:
            quest_loc, quest_raw_bytes = bundle.read_bytes(quest_entry.relative_path)
        except Exception as exc:
            raise ManifestError(
                f"Cannot read Quest file {quest_entry.relative_path}: {exc}"
            ) from exc

        actual_quest_sha = sha256_bytes(quest_raw_bytes)
        if actual_quest_sha != quest_entry.raw_sha256:
            raise ManifestError(
                f"Quest raw SHA256 mismatch for {quest_entry.relative_path}: actual {actual_quest_sha} vs manifest {quest_entry.raw_sha256}"
            )
        if len(quest_raw_bytes) != quest_entry.raw_size_bytes:
            raise ManifestError(
                f"Quest raw size mismatch for {quest_entry.relative_path}: actual {len(quest_raw_bytes)} vs manifest {quest_entry.raw_size_bytes}"
            )

        try:
            raw_quest = json.loads(quest_raw_bytes.decode("utf-8-sig"))
        except Exception as exc:
            raise ManifestError(f"Quest JSON decode failed for {quest_entry.relative_path}: {exc}") from exc

        norm_quest = deob_quest_generic(raw_quest)
        actual_quest_id = int(
            norm_quest.get("id")
            or norm_quest.get("Id")
            or raw_quest.get("Id")
            or raw_quest.get("id")
            or 0
        )
        if actual_quest_id != quest_entry.quest_id:
            raise ManifestError(
                f"Quest ID mismatch: actual {actual_quest_id} vs manifest {quest_entry.quest_id}"
            )

        raw_ojac = raw_quest.get("OJACLOOEAMG") or []
        if not isinstance(raw_ojac, list):
            raise ManifestError(f"Quest {quest_entry.quest_id} raw JSON OJACLOOEAMG is not a list")
        if len(raw_ojac) != bundle_manifest.reference_count or len(raw_ojac) != len(
            bundle_manifest.references
        ):
            raise ManifestError(
                f"Quest {quest_entry.quest_id} reference count mismatch: actual {len(raw_ojac)} vs manifest {bundle_manifest.reference_count}"
            )

        verified_refs: list[PreflightCorpusReference] = []

        for idx, ref_spec in enumerate(bundle_manifest.references):
            if ref_spec.index != idx:
                raise ManifestError(
                    f"Reference index {ref_spec.index} != position {idx} in quest {quest_entry.quest_id}"
                )
            expected_pointer = f"/OJACLOOEAMG/{idx}"
            if ref_spec.pointer != expected_pointer:
                raise ManifestError(
                    f"Reference pointer {ref_spec.pointer!r} != {expected_pointer!r} in quest {quest_entry.quest_id}"
                )

            raw_ref = raw_ojac[idx]
            deob_ref = deob_quest_talk_reference(raw_ref)
            actual_ref_talk_id = int(deob_ref.get("talkId") or 0)
            if ref_spec.talk_id != actual_ref_talk_id:
                raise ManifestError(
                    f"Reference talkId mismatch at {expected_pointer}: actual {actual_ref_talk_id} vs manifest {ref_spec.talk_id}"
                )

            if scalable:
                expected_paths = sorted(c.relative_path for c in audit_index.candidates if c.talk_id == actual_ref_talk_id)
                if sorted(c.relative_path for c in ref_spec.candidates) != expected_paths:
                    raise ManifestError("scalable candidate set differs from complete native-ID scope")
            verified_cands_for_ref: list[PreflightCorpusCandidate] = []
            candidate_manifest_entries: list[CandidateManifestEntry] = []

            for cand_spec in ref_spec.candidates:
                cand_path = cand_spec.relative_path
                audit_cand = audit_cands_by_path.get(cand_path)
                if audit_cand is None:
                    raise ManifestError(
                        f"Talk candidate {cand_path} not found in discovery index audit cache"
                    )
                if audit_cand.talk_id != ref_spec.talk_id:
                    raise ManifestError(
                        f"Discovery index candidate talk_id {audit_cand.talk_id} != reference talk_id {ref_spec.talk_id}"
                    )

                if cand_path not in all_unique_candidates:
                    try:
                        cand_loc, cand_bytes = bundle.read_bytes(cand_path)
                    except Exception as exc:
                        raise ManifestError(
                            f"Cannot read Talk candidate {cand_path}: {exc}"
                        ) from exc

                    actual_cand_sha = sha256_bytes(cand_bytes)
                    if actual_cand_sha != audit_cand.raw_sha256:
                        raise ManifestError(
                            f"Candidate raw SHA256 mismatch for {cand_path}: actual {actual_cand_sha} vs audit cache {audit_cand.raw_sha256}"
                        )
                    if len(cand_bytes) != audit_cand.size_bytes:
                        raise ManifestError(
                            f"Candidate raw size mismatch for {cand_path}: actual {len(cand_bytes)} vs audit cache {audit_cand.size_bytes}"
                        )

                    try:
                        raw_talk = json.loads(cand_bytes.decode("utf-8-sig"))
                    except Exception as exc:
                        raise ManifestError(
                            f"Candidate Talk JSON decode failed for {cand_path}: {exc}"
                        ) from exc

                    norm_talk = deob_talk_generic(raw_talk)
                    actual_cand_talk_id = int(
                        norm_talk.get("talkId")
                        or raw_talk.get("talkId")
                        or raw_talk.get("id")
                        or raw_talk.get("Id")
                        or 0
                    )
                    if actual_cand_talk_id != ref_spec.talk_id:
                        raise ManifestError(
                            f"Candidate native talk ID mismatch in {cand_path}: actual {actual_cand_talk_id} vs reference {ref_spec.talk_id}"
                        )

                    actual_norm_ck = sha256_text(canonical_json(norm_talk))
                    if actual_norm_ck != cand_spec.normalized_sha256:
                        raise ManifestError(
                            f"Candidate normalized checksum mismatch for {cand_path}: actual {actual_norm_ck} vs manifest {cand_spec.normalized_sha256}"
                        )
                    if actual_norm_ck != audit_cand.normalized_checksum:
                        raise ManifestError(
                            f"Candidate normalized checksum mismatch for {cand_path}: actual {actual_norm_ck} vs audit cache {audit_cand.normalized_checksum}"
                        )

                    dialog_list = norm_talk.get("dialogList", [])
                    actual_node_cnt = len(dialog_list)
                    actual_edge_cnt = sum(len(n.get("nextDialogs", [])) for n in dialog_list)

                    # Compute branch and merge nodes
                    out_deg: dict[int, int] = {}
                    in_deg: dict[int, int] = {}
                    node_ids_set: set[int] = set()

                    for d in dialog_list:
                        d_id = int(d["id"])
                        node_ids_set.add(d_id)
                        next_ds = [
                            int(nd)
                            for nd in (d.get("nextDialogs") or [])
                            if isinstance(nd, int) and not isinstance(nd, bool)
                        ]
                        out_deg[d_id] = len(next_ds)
                        for nd in next_ds:
                            in_deg[nd] = in_deg.get(nd, 0) + 1

                    actual_b_cnt = sum(1 for _, deg in out_deg.items() if deg > 1)
                    actual_m_cnt = sum(1 for _, deg in in_deg.items() if deg > 1)
                    actual_node_ids = tuple(sorted(node_ids_set))

                    if actual_node_cnt != cand_spec.node_count:
                        raise ManifestError(
                            f"Candidate node_count mismatch for {cand_path}: actual {actual_node_cnt} vs manifest {cand_spec.node_count}"
                        )
                    if actual_node_cnt != audit_cand.node_count:
                        raise ManifestError(
                            f"Candidate node_count mismatch for {cand_path}: actual {actual_node_cnt} vs audit cache {audit_cand.node_count}"
                        )
                    if actual_edge_cnt != cand_spec.edge_count:
                        raise ManifestError(
                            f"Candidate edge_count mismatch for {cand_path}: actual {actual_edge_cnt} vs manifest {cand_spec.edge_count}"
                        )
                    if actual_edge_cnt != audit_cand.edge_count:
                        raise ManifestError(
                            f"Candidate edge_count mismatch for {cand_path}: actual {actual_edge_cnt} vs audit cache {audit_cand.edge_count}"
                        )
                    if actual_b_cnt != cand_spec.branch_node_count:
                        raise ManifestError(
                            f"Candidate branch_node_count mismatch for {cand_path}: actual {actual_b_cnt} vs manifest {cand_spec.branch_node_count}"
                        )
                    if actual_m_cnt != cand_spec.merge_node_count:
                        raise ManifestError(
                            f"Candidate merge_node_count mismatch for {cand_path}: actual {actual_m_cnt} vs manifest {cand_spec.merge_node_count}"
                        )
                    if actual_node_ids != tuple(audit_cand.node_ids):
                        raise ManifestError(
                            f"Candidate node_ids mismatch for {cand_path}: actual {actual_node_ids} vs audit cache {audit_cand.node_ids}"
                        )

                    verified_cand = PreflightCorpusCandidate(
                        candidate_spec=cand_spec,
                        audit_candidate=audit_cand,
                        located=cand_loc,
                        raw_bytes=cand_bytes,
                        raw_talk=raw_talk,
                        norm_talk=norm_talk,
                        dialog_list=dialog_list,
                        node_count=actual_node_cnt,
                        edge_count=actual_edge_cnt,
                        branch_count=actual_b_cnt,
                        merge_count=actual_m_cnt,
                        node_ids=actual_node_ids,
                        native_id=actual_cand_talk_id,
                    )
                    all_unique_candidates[cand_path] = verified_cand
                    seen_talk_paths.append(cand_path)

                    total_occurrences_computed += actual_node_cnt
                    total_edges_computed += actual_edge_cnt
                    total_branch_nodes_computed += actual_b_cnt
                    total_merge_nodes_computed += actual_m_cnt
                else:
                    verified_cand = all_unique_candidates[cand_path]

                verified_cands_for_ref.append(verified_cand)
                candidate_manifest_entries.append(
                    CandidateManifestEntry(
                        relative_path=cand_spec.relative_path,
                        raw_sha256=audit_cand.raw_sha256,
                        normalized_checksum=cand_spec.normalized_sha256,
                        raw_size_bytes=audit_cand.size_bytes,
                        node_count=cand_spec.node_count,
                        edge_count=cand_spec.edge_count,
                    )
                )

            # Build reference candidate scope manifest for verification & linking
            ref_scope = CandidateScopeManifest(
                manifest_id=bundle_manifest.manifest_id,
                manifest_checksum=bundle_ref_entry.raw_sha256,
                snapshot_id=snapshot_id_value,
                snapshot_commit=bundle_manifest.source_snapshot.commit,
                snapshot_tree=bundle_manifest.source_snapshot.tree,
                audit_algorithm_version=audit_index.discovery_version,
                audit_mapping_version=audit_index.mapping_version,
                raw_byte_basis="utf-8-sig:json:canonical_sha256",
                audit_cache_digest=audit_cache_sha256,
                audit_cache_state="verified",
                inventory_complete=True,
                namespace=ref_spec.namespace,
                talk_id=str(ref_spec.talk_id),
                candidate_count=len(candidate_manifest_entries),
                candidates=tuple(candidate_manifest_entries),
                structural_errors=(),
            )

            verified_refs.append(
                PreflightCorpusReference(
                    ref_spec=ref_spec,
                    index=idx,
                    pointer=expected_pointer,
                    namespace=ref_spec.namespace,
                    talk_id=ref_spec.talk_id,
                    raw_reference=raw_ref,
                    deob_reference=deob_ref,
                    ref_scope=ref_scope,
                    candidates=tuple(verified_cands_for_ref),
                )
            )

        verified_quests.append(
            PreflightCorpusQuest(
                bundle_manifest=bundle_manifest,
                quest_id=quest_entry.quest_id,
                located=quest_loc,
                raw_bytes=quest_raw_bytes,
                raw_quest=raw_quest,
                norm_quest=norm_quest,
                references=tuple(verified_refs),
            )
        )

    # 7. Aggregate invariants check
    inv = corpus_manifest.corpus_invariants
    if inv.quest_count != len(verified_quests):
        raise ManifestError(
            f"Corpus quest count mismatch: expected {inv.quest_count}, actual {len(verified_quests)}"
        )
    total_refs_count = sum(len(q.references) for q in verified_quests)
    if inv.total_talk_references != total_refs_count:
        raise ManifestError(
            f"Corpus total references mismatch: expected {inv.total_talk_references}, actual {total_refs_count}"
        )
    if inv.unique_talk_candidates != len(all_unique_candidates):
        raise ManifestError(
            f"Corpus unique candidates mismatch: expected {inv.unique_talk_candidates}, actual {len(all_unique_candidates)}"
        )
    if inv.expected_occurrences != total_occurrences_computed:
        raise ManifestError(
            f"Corpus occurrences mismatch: expected {inv.expected_occurrences}, actual {total_occurrences_computed}"
        )
    if inv.expected_edges != total_edges_computed:
        raise ManifestError(
            f"Corpus edges mismatch: expected {inv.expected_edges}, actual {total_edges_computed}"
        )
    if inv.expected_texts_per_language != total_occurrences_computed:
        raise ManifestError(
            f"Corpus texts per language mismatch: expected {inv.expected_texts_per_language}, actual {total_occurrences_computed}"
        )
    if inv.expected_branch_nodes != total_branch_nodes_computed:
        raise ManifestError(
            f"Corpus branch nodes mismatch: expected {inv.expected_branch_nodes}, actual {total_branch_nodes_computed}"
        )
    if inv.expected_merge_nodes != total_merge_nodes_computed:
        raise ManifestError(
            f"Corpus merge nodes mismatch: expected {inv.expected_merge_nodes}, actual {total_merge_nodes_computed}"
        )

    # Audit every non-local pointer only after the complete sealed candidate
    # inventory is known.  The historical AQ/R5 boundaries remain fail-closed
    # for unverified dangling references.  R6 explicitly includes several
    # real dangling pointers as legal raw-evidence outcomes; they are recorded
    # in graph_projection and never projected as occurrence edges.
    graph_target_audits = audit_cross_container_targets(
        {
            path: candidate.dialog_list
            for path, candidate in all_unique_candidates.items()
        }
    )
    dangling_targets = tuple(
        audit for audit in graph_target_audits if audit.classification == "dangling_target"
    )
    if dangling_targets and not scalable and corpus_manifest.manifest_id != R6_MEDIUM_CORPUS_MANIFEST_ID:
        first = dangling_targets[0]
        raise ManifestError(
            "sealed Talk corpus contains unverified dangling target: "
            f"{first.source_talk_path} node {first.source_node_id} -> {first.target_node_id}"
        )
    allowed_external_target_ids_by_path: dict[str, tuple[str, ...]] = {}
    for audit in graph_target_audits:
        allowed_classifications = ("external_target", "ambiguous_target")
        if scalable or corpus_manifest.manifest_id == R6_MEDIUM_CORPUS_MANIFEST_ID:
            allowed_classifications += ("dangling_target",)
        if audit.classification in allowed_classifications:
            current = set(allowed_external_target_ids_by_path.get(audit.source_talk_path, ()))
            current.add(audit.target_node_id)
            allowed_external_target_ids_by_path[audit.source_talk_path] = tuple(sorted(current))

    # 8. Assemble selected paths (15 quests + 149 talks + 6 textmaps = 170)
    selected_paths: list[str] = []
    for q in verified_quests:
        selected_paths.append(q.bundle_manifest.quest.relative_path)
    for tp in seen_talk_paths:
        if tp not in selected_paths:
            selected_paths.append(tp)
    for tm_entry in textmap_manifest.textmaps:
        if tm_entry.relative_path not in selected_paths:
            selected_paths.append(tm_entry.relative_path)

    if scalable:
        scope = corpus_manifest.scalable_scope
        if scope["quest_source_files"] != [q.bundle_manifest.quest.relative_path for q in verified_quests]:
            raise ManifestError("scalable Quest file denominator mismatch")
        if scope["expected_physical_source_denominator"] != len(selected_paths):
            raise ManifestError("scalable source denominator mismatch")
        if scope["candidate_lookup_denominator"] != audit_index.candidate_file_count:
            raise ManifestError("scalable candidate lookup denominator mismatch")
        if scope["textmap_shards"] != [t.relative_path for t in textmap_manifest.textmaps]:
            raise ManifestError("scalable TextMap shard set mismatch")
    # 9. Compute deterministic IDs
    master_raw_bytes = Path(corpus_manifest_path).read_bytes()
    master_manifest_sha256 = sha256_bytes(master_raw_bytes)

    config_data = {
        "audit_cache_sha256": audit_cache_sha256,
        "child_manifest_hashes": [b.raw_sha256 for b in corpus_manifest.quest_bundles],
        "languages": ["CHS", "EN", "JP"],
        "mapping_version": MAPPING_VERSION_CORPUS,
        "master_manifest_sha256": master_manifest_sha256,
        "parser_version": PARSER_VERSION_CORPUS,
        "projection_version": PROJECTION_VERSION_CORPUS,
        "source_snapshot": {
            "commit": corpus_manifest.source_snapshot.commit,
            "release_label": corpus_manifest.source_snapshot.release_label,
            "snapshot_id": snapshot_id_value,
            "source_id": source_manifest["source_id"],
            "tree": corpus_manifest.source_snapshot.tree,
            "upstream": corpus_manifest.source_snapshot.upstream,
        },
        "textmap_hydration": "v1",
        "textmap_manifest_sha256": corpus_manifest.textmap_manifest.raw_sha256,
    }
    if scalable or corpus_manifest.manifest_id in R5_QUEST_CORPUS_MANIFEST_IDS:
        config_data["parser_implementation_sha256"] = r5_parser_implementation_sha256()
    corpus_config_checksum = sha256_text(canonical_json(config_data))

    parser_build_id = digest_id(
        "parser-build",
        snapshot_id_value,
        PARSER_VERSION_CORPUS,
        corpus_config_checksum,
    )
    ingest_run_id = digest_id(
        "ingest-run",
        snapshot_id_value,
        parser_build_id,
        master_manifest_sha256,
        corpus_config_checksum,
    )

    return PreflightQuestCorpusResult(
        source_manifest=source_manifest,
        manifest_set=manifest_set,
        audit_index=audit_index,
        audit_cache_sha256=audit_cache_sha256,
        snapshot_id_value=snapshot_id_value,
        corpus_config_checksum=corpus_config_checksum,
        parser_build_id=parser_build_id,
        ingest_run_id=ingest_run_id,
        quests=tuple(verified_quests),
        main_shards=main_shards,
        med_shards=med_shards,
        shard_located_and_bytes=shard_located_and_bytes,
        selected_paths=selected_paths,
        all_unique_candidates=all_unique_candidates,
        graph_target_audits=graph_target_audits,
        allowed_external_target_ids_by_path=allowed_external_target_ids_by_path,
    )


def ingest_quest_corpus_evidence_bundle(
    db_path: str | Path,
    source_roots: list[str | Path],
    source_manifest_path: str | Path,
    corpus_manifest_path: str | Path,
    audit_cache_path: str | Path,
    manifest_root: str | Path | None = None,
) -> dict[str, Any]:
    """Ingest sealed AQ 7000-7014 quest evidence corpus inside a single atomic transaction."""
    # 1. Run fail-closed preflight BEFORE touching DB or file system
    preflight = preflight_quest_corpus_evidence_bundle(
        source_roots=source_roots,
        source_manifest_path=source_manifest_path,
        corpus_manifest_path=corpus_manifest_path,
        audit_cache_path=audit_cache_path,
        manifest_root=manifest_root,
    )

    # 2. Create parent directory and open database
    target_db_path = Path(db_path)
    target_db_path.parent.mkdir(parents=True, exist_ok=True)

    with Database(target_db_path) as db:
        db.initialize_through(7)
        corpus_manifest_id = preflight.manifest_set.corpus_manifest.manifest_id
        scalable = preflight.manifest_set.corpus_manifest.scalable_scope is not None
        reproducible_timestamp = (
            (
                R5_REPRODUCIBLE_TIMESTAMP
                if corpus_manifest_id == R5_CORPUS_MANIFEST_ID
                else R6_REPRODUCIBLE_TIMESTAMP
            )
            if scalable or corpus_manifest_id in R5_QUEST_CORPUS_MANIFEST_IDS
            else None
        )
        if reproducible_timestamp is not None:
            # Historical migrations record wall time.  The bounded R5 artifact
            # is a fresh standalone candidate, so normalize only its ledger to
            # fixed contract metadata and leave the accepted AQ path unchanged.
            with db.transaction():
                db.conn.execute(
                    "UPDATE schema_migration SET applied_at=?",
                    (reproducible_timestamp,),
                )

        legacy_before = {
            "logical_talk": int(
                db.conn.execute("SELECT COUNT(*) FROM logical_talk").fetchone()[0]
            ),
            "talk_document": int(
                db.conn.execute(
                    "SELECT COUNT(*) FROM document WHERE content_type='talk'"
                ).fetchone()[0]
            ),
            "passage": int(
                db.conn.execute("SELECT COUNT(*) FROM passage").fetchone()[0]
            ),
            "passage_edge": int(
                db.conn.execute("SELECT COUNT(*) FROM passage_edge").fetchone()[0]
            ),
        }
        old_runs_count = int(
            db.conn.execute(
                "SELECT COUNT(*) FROM ingest_run WHERE id != ?",
                (preflight.ingest_run_id,),
            ).fetchone()[0]
        )
        old_run_preserved = old_runs_count > 0

        # All mutations happen within a single atomic database transaction
        with db.transaction():
            db.ensure_source(
                preflight.source_manifest["source_id"],
                "primary",
                preflight.source_manifest["upstream"],
                "Upstream repository snapshot",
            )
            parser_fingerprint = (
                "quest-corpus-evidence-7000-7014"
                if corpus_manifest_id == AQ_CORPUS_MANIFEST_ID
                else (
                    "quest-corpus-evidence-r5-quest-71008-pilot"
                    if corpus_manifest_id == R5_CORPUS_MANIFEST_ID
                    else "quest-corpus-evidence-r6-medium-quest-talk"
                )
            )
            if scalable:
                parser_fingerprint = "quest-talk-manifest-driven-v1"
            db.ensure_snapshot(
                {
                    "id": preflight.snapshot_id_value,
                    "source_id": preflight.source_manifest["source_id"],
                    "release_label": preflight.source_manifest["release_label"],
                    "build": preflight.source_manifest["build"],
                    "commit": preflight.source_manifest["commit"],
                    "tree": preflight.source_manifest["tree"],
                    "observed_at": preflight.source_manifest.get(
                        "observed_at", "2026-08-21T00:00:00Z"
                    ),
                    "parser_version": PARSER_VERSION_CORPUS,
                    "mapping_version": MAPPING_VERSION_CORPUS,
                    "status": "active",
                    "metadata": {
                        # This accepted snapshot tuple predates R5 and must stay
                        # byte-compatible; corpus identity belongs to parser/run.
                        "scope": "AQ 7000-7014 sealed quest evidence corpus",
                        "corpus_manifest_id": "anime-game-data-7.0-aq-7000-7014",
                    },
                }
            )
            db.ensure_parser_build(
                preflight.parser_build_id,
                parser_version=PARSER_VERSION_CORPUS,
                mapping_version=MAPPING_VERSION_CORPUS,
                fingerprint=parser_fingerprint,
                config_checksum=preflight.corpus_config_checksum,
                created_at=reproducible_timestamp,
            )

            all_talk_ids = sorted(
                {
                    ref.talk_id
                    for quest in preflight.quests
                    for ref in quest.references
                }
            )

            db.ensure_ingest_run(
                preflight.ingest_run_id,
                snapshot_id=preflight.snapshot_id_value,
                parser_build_id=preflight.parser_build_id,
                scope={
                    "family": "quest_corpus_evidence",
                    "quest_ids": [q.quest_id for q in preflight.quests],
                    "talk_ids": all_talk_ids,
                    "selected_paths": preflight.selected_paths,
                },
                config={
                    "projection": PROJECTION_VERSION_CORPUS,
                    "languages": list(LANGUAGES),
                    "full_scan": False,
                    "textmap_hydration": "v1",
                },
                status="running",
                created_at=reproducible_timestamp,
            )

            # Register TextMap source files
            shard_file_ids: dict[str, str] = {}
            for rel_path, (shard_loc, shard_bytes) in preflight.shard_located_and_bytes.items():
                shard_file_id = _register_source_file(
                    db,
                    snapshot_id_value=preflight.snapshot_id_value,
                    located=shard_loc,
                    raw=shard_bytes,
                )
                shard_file_ids[rel_path] = shard_file_id

            # Project talk roots and link quest references
            projected_candidates: dict[str, tuple[Any, Any]] = {}
            registered_textmap_records: dict[tuple[str, str], str] = {}

            total_resolved_text_count = 0
            total_nonempty_text_count = 0
            total_missing = 0
            total_both_equal = 0
            total_both_conflict = 0
            total_duplicate = 0
            counts_by_language: dict[str, int] = {lang: 0 for lang in LANGUAGES}
            distinct_content_hashes: set[str] = set()

            resolved_references_count = 0
            ambiguous_references_count = 0
            unresolved_references_count = 0
            zero_ref_quests: list[int] = []

            for quest in preflight.quests:
                doc_id = f"agd:quest:{quest.quest_id}"

                # Register Quest source file & root record
                quest_file_id = _register_source_file(
                    db,
                    snapshot_id_value=preflight.snapshot_id_value,
                    located=quest.located,
                    raw=quest.raw_bytes,
                )
                quest_record_id = db.ensure_source_record(
                    quest_file_id,
                    "/",
                    "quest_root",
                    quest.quest_id,
                    quest.raw_quest,
                    raw_json=canonical_json(quest.raw_quest),
                )
                db.ensure_document(
                    doc_id,
                    "quest",
                    "quest",
                    quest.quest_id,
                    "active",
                )
                db.ensure_document_version(
                    doc_id,
                    preflight.snapshot_id_value,
                    quest_record_id,
                    quest.raw_quest,
                    quest.norm_quest,
                    {
                        "scope": f"selected Quest {quest.quest_id} root",
                        "mapping_version": MAPPING_VERSION_CORPUS,
                    },
                )

                if not quest.references:
                    zero_ref_quests.append(quest.quest_id)
                    continue

                for ref_item in quest.references:
                    # Register reference source record
                    ref_rec_id = db.ensure_source_record(
                        quest_file_id,
                        ref_item.pointer,
                        "quest_talk_reference",
                        int(ref_item.talk_id),
                        ref_item.raw_reference,
                        raw_json=canonical_json(ref_item.raw_reference),
                    )

                    # Project candidates
                    for cand in ref_item.candidates:
                        cand_path = cand.candidate_spec.relative_path
                        if cand_path not in projected_candidates:
                            talk_file_id = _register_source_file(
                                db,
                                snapshot_id_value=preflight.snapshot_id_value,
                                located=cand.located,
                                raw=cand.raw_bytes,
                            )
                            talk_rec_id = db.ensure_source_record(
                                talk_file_id,
                                "/",
                                "talk_root",
                                int(cand.native_id),
                                cand.raw_bytes,
                                raw_json=cand.raw_bytes.decode("utf-8"),
                            )

                            resolution = resolve_talk_textmap(
                                dialog_list=cand.dialog_list,
                                textmap_shards=preflight.main_shards,
                                medium_shards=preflight.med_shards,
                                languages=LANGUAGES,
                            )
                            total_resolved_text_count += resolution.resolved_count
                            total_nonempty_text_count += resolution.nonempty_count
                            total_missing += resolution.missing_count
                            total_both_equal += resolution.both_equal_count
                            total_both_conflict += resolution.both_conflict_count
                            total_duplicate += resolution.duplicate_count
                            for lang in LANGUAGES:
                                counts_by_language[lang] += resolution.counts_by_language.get(
                                    lang, 0
                                )
                            for h in resolution.selected_content_hashes:
                                distinct_content_hashes.add(h)

                            # Register TextMap source records
                            for item in resolution.node_texts:
                                for cand_item in item.candidates:
                                    key = (cand_item.relative_path, item.textmap_hash)
                                    if key not in registered_textmap_records:
                                        file_id = shard_file_ids[cand_item.relative_path]
                                        textmap_entry = {
                                            "hash": item.textmap_hash,
                                            "value": cand_item.raw_text,
                                        }
                                        rec_id = db.ensure_source_record(
                                            file_id,
                                            cand_item.pointer,
                                            "textmap_entry",
                                            item.textmap_hash,
                                            textmap_entry,
                                            raw_json=canonical_json(textmap_entry),
                                        )
                                        registered_textmap_records[key] = rec_id

                            resolved_with_records: list[ResolvedNodeText] = []
                            for item in resolution.node_texts:
                                rec_id = None
                                if item.relative_path and item.status in (
                                    "unique_main",
                                    "unique_medium",
                                    "both_equal",
                                ):
                                    rec_id = registered_textmap_records.get(
                                        (item.relative_path, item.textmap_hash)
                                    )
                                resolved_with_records.append(
                                    ResolvedNodeText(
                                        dialog_index=item.dialog_index,
                                        node_id=item.node_id,
                                        language=item.language,
                                        textmap_hash=item.textmap_hash,
                                        status=item.status,
                                        raw_text=item.raw_text,
                                        normalized_text=item.normalized_text,
                                        shard=item.shard,
                                        relative_path=item.relative_path,
                                        pointer=item.pointer,
                                        source_record_id=rec_id,
                                        candidates=item.candidates,
                                    )
                                )

                            projection = project_talk_root(
                                db,
                                ingest_run_id=preflight.ingest_run_id,
                                source_record_id=talk_rec_id,
                                raw_talk=cand.raw_talk,
                                preserve_physical_raw_nodes=scalable,
                                namespace=ref_item.namespace,
                                resolved_texts=resolved_with_records,
                                allowed_external_target_ids=preflight.allowed_external_target_ids_by_path.get(
                                    cand_path, ()
                                ),
                            )
                            projected_candidates[cand_path] = (projection, resolution)

                    # Link reference
                    link = link_quest_reference_to_evidence(
                        db,
                        document_id=doc_id,
                        ingest_run_id=preflight.ingest_run_id,
                        namespace=ref_item.namespace,
                        talk_id=int(ref_item.talk_id),
                        source_record_id=ref_rec_id,
                        candidate_scope=ref_item.ref_scope,
                        reference_metadata={
                            "normalized_pointer": f"/talks/{ref_item.index}"
                        },
                    )

                    if link.result == "evidence_only":
                        resolved_references_count += 1
                    elif link.result == "ambiguous":
                        ambiguous_references_count += 1
                    else:
                        unresolved_references_count += 1

            # Update ingest_run status to complete
            db.ensure_ingest_run(
                preflight.ingest_run_id,
                snapshot_id=preflight.snapshot_id_value,
                parser_build_id=preflight.parser_build_id,
                scope={
                    "family": "quest_corpus_evidence",
                    "quest_ids": [q.quest_id for q in preflight.quests],
                    "talk_ids": all_talk_ids,
                    "selected_paths": preflight.selected_paths,
                },
                config={
                    "projection": PROJECTION_VERSION_CORPUS,
                    "languages": list(LANGUAGES),
                    "full_scan": False,
                    "textmap_hydration": "v1",
                },
                status="complete",
            )

            legacy_after = {
                "logical_talk": int(
                    db.conn.execute("SELECT COUNT(*) FROM logical_talk").fetchone()[0]
                ),
                "talk_document": int(
                    db.conn.execute(
                        "SELECT COUNT(*) FROM document WHERE content_type='talk'"
                    ).fetchone()[0]
                ),
                "passage": int(
                    db.conn.execute("SELECT COUNT(*) FROM passage").fetchone()[0]
                ),
                "passage_edge": int(
                    db.conn.execute("SELECT COUNT(*) FROM passage_edge").fetchone()[0]
                ),
            }
            legacy_writes = {
                key: legacy_after[key] - legacy_before[key] for key in legacy_before
            }

            # Gather total occurrences and edges created in this run
            total_occurrences_in_run = int(
                db.conn.execute(
                    "SELECT COUNT(*) FROM passage_occurrence WHERE ingest_run_id=?",
                    (preflight.ingest_run_id,),
                ).fetchone()[0]
            )
            total_edges_in_run = int(
                db.conn.execute(
                    "SELECT COUNT(*) FROM occurrence_edge WHERE ingest_run_id=?",
                    (preflight.ingest_run_id,),
                ).fetchone()[0]
            )
            total_containers_in_run = int(
                db.conn.execute(
                    "SELECT COUNT(*) FROM evidence_container WHERE ingest_run_id=?",
                    (preflight.ingest_run_id,),
                ).fetchone()[0]
            )
            total_source_files = int(
                db.conn.execute(
                    "SELECT COUNT(*) FROM source_file WHERE snapshot_id=?",
                    (preflight.snapshot_id_value,),
                ).fetchone()[0]
            )
            total_source_records = int(
                db.conn.execute(
                    "SELECT COUNT(*) FROM source_record sr JOIN source_file sf ON sf.id=sr.source_file_id WHERE sf.snapshot_id=?",
                    (preflight.snapshot_id_value,),
                ).fetchone()[0]
            )

            graph_target_audits = preflight.graph_target_audits
            external_target_audits = [
                audit for audit in graph_target_audits
                if audit.classification in ("external_target", "ambiguous_target")
            ]
            dangling_target_audits = [
                audit for audit in graph_target_audits
                if audit.classification == "dangling_target"
            ]
            raw_next_pointer_count = sum(
                len(node.get("nextDialogs", ()))
                for candidate in preflight.all_unique_candidates.values()
                for node in candidate.dialog_list
                if isinstance(node, dict)
                and isinstance(node.get("nextDialogs", ()), list)
            )

            return {
                "status": "complete",
                "source_snapshot": {
                    "snapshot_id": preflight.snapshot_id_value,
                    "source_id": preflight.source_manifest["source_id"],
                    "release_label": preflight.source_manifest["release_label"],
                    "commit": preflight.source_manifest["commit"],
                    "tree": preflight.source_manifest["tree"],
                },
                "master_manifest": {
                    "manifest_id": preflight.manifest_set.corpus_manifest.manifest_id,
                    "raw_sha256": sha256_bytes(Path(corpus_manifest_path).read_bytes()),
                },
                "audit_cache_sha256": preflight.audit_cache_sha256,
                "parser_build_id": preflight.parser_build_id,
                "ingest_run_id": preflight.ingest_run_id,
                "corpus_config_checksum": preflight.corpus_config_checksum,
                "quest_count": len(preflight.quests),
                "zero_reference_quest_ids": zero_ref_quests,
                "reference_counts": {
                    "total_references": len(preflight.manifest_set.corpus_manifest.quest_bundles)
                    and sum(len(q.references) for q in preflight.quests),
                    "resolved_references": resolved_references_count,
                    "ambiguous_references": ambiguous_references_count,
                    "unresolved_references": unresolved_references_count,
                },
                "selected_paths": preflight.selected_paths,
                "selected_path_count": len(preflight.selected_paths),
                "source_file_count": total_source_files,
                "source_record_count": total_source_records,
                "evidence_container_count": total_containers_in_run,
                "occurrence_count": total_occurrences_in_run,
                "occurrence_edge_count": total_edges_in_run,
                "branch_node_count": preflight.manifest_set.corpus_manifest.corpus_invariants.expected_branch_nodes,
                "merge_node_count": preflight.manifest_set.corpus_manifest.corpus_invariants.expected_merge_nodes,
                "graph_projection": {
                    "raw_next_pointer_count": raw_next_pointer_count,
                    "intra_container_edge_count": total_edges_in_run,
                    "external_target_pointer_count": len(external_target_audits),
                    "ambiguous_target_pointer_count": sum(
                        1
                        for audit in external_target_audits
                        if audit.classification == "ambiguous_target"
                    ),
                    "dangling_target_pointer_count": len(dangling_target_audits),
                    "external_targets": [audit.as_dict() for audit in external_target_audits],
                    "dangling_targets": [audit.as_dict() for audit in dangling_target_audits],
                },
                "text_hydration": {
                    "status": "partial" if total_missing else "complete",
                    "languages": list(LANGUAGES),
                    "expected_language_slots": total_occurrences_in_run * len(LANGUAGES),
                    "total_occurrence_texts": total_resolved_text_count,
                    "counts_by_language": dict(counts_by_language),
                    "resolved_occurrence_text_count": total_resolved_text_count,
                    "nonempty_count": total_nonempty_text_count,
                    "missing": total_missing,
                    "both_equal": total_both_equal,
                    "both_conflict": total_both_conflict,
                    "duplicate": total_duplicate,
                    "selected_content_hash_count": len(distinct_content_hashes),
                    "selected_textmap_source_file_count": len(shard_file_ids),
                    "selected_textmap_source_record_count": len(registered_textmap_records),
                    "role_name_hydrated": False,
                    "old_run_preserved": old_run_preserved,
                },
                "legacy_writes": legacy_writes,
                "scope": {
                    "selected_paths": preflight.selected_paths,
                    "full_scan": False,
                    "fts_rebuilt": False,
                    "embedding": False,
                    "semantic_search": False,
                },
            }
