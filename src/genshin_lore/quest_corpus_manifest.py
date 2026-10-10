"""Sealed Quest Corpus Manifest Contract and Loader for Phase 3.2A.

Provides deterministic, fail-closed dataclasses, parsers, and cross-file
verifiers for:
1. TextMap asset bundles (schema_version 1)
2. Quest candidate bundles (schema_version 2)
3. Quest evidence corpus manifests (schema_version 1)

Architecture Principle:
    Raw Evidence Identity != Logical Identity
    This module performs strictly read-only manifest loading and integrity
    verification; it performs zero database writes, zero schema migrations,
    and zero network I/O.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ManifestError


# ---------------------------------------------------------------------------
# Strict Field Validators (Fail-Closed)
# ---------------------------------------------------------------------------

def _validate_dict(val: Any, context: str) -> dict[str, Any]:
    if not isinstance(val, dict):
        raise ManifestError(f"{context} must be a JSON object, got {type(val).__name__}")
    return val


def _check_exact_keys(data: dict[str, Any], allowed_keys: set[str], context: str) -> None:
    actual_keys = set(data.keys())
    if actual_keys != allowed_keys:
        missing = allowed_keys - actual_keys
        extra = actual_keys - allowed_keys
        errors = []
        if missing:
            errors.append(f"missing required keys: {sorted(missing)}")
        if extra:
            errors.append(f"unknown/disallowed keys: {sorted(extra)}")
        raise ManifestError(f"{context} field mismatch: {'; '.join(errors)}")


def _validate_non_negative_int(val: Any, field_name: str) -> int:
    # Explicitly reject bool since bool is a subclass of int in Python
    if type(val) is not int or val < 0:
        raise ManifestError(
            f"{field_name} must be a non-negative integer, got {type(val).__name__}: {val!r}"
        )
    return val


def _validate_non_empty_str(val: Any, field_name: str) -> str:
    if not isinstance(val, str) or not val.strip():
        raise ManifestError(f"{field_name} must be a non-empty string, got {val!r}")
    return val


def _validate_sha256(val: Any, field_name: str) -> str:
    if not isinstance(val, str) or len(val) != 64 or any(c not in "0123456789abcdef" for c in val):
        raise ManifestError(
            f"{field_name} must be a lowercase 64-hex SHA-256 string, got {val!r}"
        )
    return val


def _validate_git_hex(val: Any, field_name: str) -> str:
    if not isinstance(val, str) or len(val) != 40 or any(c not in "0123456789abcdef" for c in val):
        raise ManifestError(
            f"{field_name} must be a lowercase 40-hex object id, got {val!r}"
        )
    return val


def _validate_relative_path(val: Any, field_name: str = "relative_path") -> str:
    if not isinstance(val, str) or not val:
        raise ManifestError(f"{field_name} must be a non-empty relative path string")
    if "\\" in val:
        raise ManifestError(f"{field_name} must be a relative path using POSIX '/' separators, not backslashes: {val!r}")
    if ":" in val:
        raise ManifestError(f"{field_name} must be a relative path without drive letters or colons: {val!r}")
    if val.startswith("/"):
        raise ManifestError(f"{field_name} must be a relative path, not absolute: {val!r}")
    parts = val.split("/")
    for part in parts:
        if not part or part == "." or part == "..":
            raise ManifestError(
                f"{field_name} contains invalid or traversal path segment '{part}' in relative path: {val!r}"
            )
    return val


def _resolve_manifest_file(root: Path, relative_path: str) -> Path:
    clean_rel = _validate_relative_path(relative_path)
    resolved_root = root.resolve()
    target = (resolved_root / clean_rel).resolve()
    if not (target == resolved_root or resolved_root in target.parents):
        raise ManifestError(f"relative path '{relative_path}' escapes manifest root '{root}'")
    return target


def _read_and_verify_json_file(
    path: Path, expected_sha256: str | None = None
) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ManifestError(f"cannot read manifest file at {path}: {exc}") from exc

    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        raise ManifestError(
            f"checksum mismatch for {path}: expected {expected_sha256}, got {actual_sha256}"
        )

    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"invalid JSON in manifest at {path}: {exc}") from exc

    return _validate_dict(data, str(path)), actual_sha256


# ---------------------------------------------------------------------------
# Data Models (Frozen / Immutable)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceSnapshot:
    """Pinned upstream repository snapshot identity."""

    upstream: str
    release_label: str
    commit: str
    tree: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "upstream": self.upstream,
            "release_label": self.release_label,
            "commit": self.commit,
            "tree": self.tree,
        }


@dataclass(frozen=True)
class TextMapEntry:
    """One multilingual TextMap shard entry."""

    language: str
    tier: str
    relative_path: str
    raw_sha256: str
    raw_size_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "tier": self.tier,
            "relative_path": self.relative_path,
            "raw_sha256": self.raw_sha256,
            "raw_size_bytes": self.raw_size_bytes,
        }


@dataclass(frozen=True)
class TextMapAssetManifest:
    """Shared sealed TextMap asset bundle manifest (schema_version 1)."""

    schema_version: int
    kind: str
    manifest_id: str
    source_snapshot: SourceSnapshot
    textmaps: tuple[TextMapEntry, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "manifest_id": self.manifest_id,
            "source_snapshot": self.source_snapshot.as_dict(),
            "textmaps": [t.as_dict() for t in self.textmaps],
        }


@dataclass(frozen=True)
class TextMapManifestReference:
    """Sealed reference to the shared TextMap asset manifest."""

    manifest_id: str
    relative_path: str
    raw_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "relative_path": self.relative_path,
            "raw_sha256": self.raw_sha256,
        }


@dataclass(frozen=True)
class CandidateTalkEntry:
    """Metadata for one candidate Talk file discovered for a Quest talk reference."""

    relative_path: str
    normalized_sha256: str
    node_count: int
    edge_count: int
    branch_node_count: int
    merge_node_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "normalized_sha256": self.normalized_sha256,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "branch_node_count": self.branch_node_count,
            "merge_node_count": self.merge_node_count,
        }


@dataclass(frozen=True)
class QuestTalkReference:
    """One raw Quest talk reference pointer with resolved candidate proofs."""

    pointer: str
    index: int
    namespace: str
    talk_id: int
    candidate_count: int
    candidates: tuple[CandidateTalkEntry, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "pointer": self.pointer,
            "index": self.index,
            "namespace": self.namespace,
            "talk_id": self.talk_id,
            "candidate_count": self.candidate_count,
            "candidates": [c.as_dict() for c in self.candidates],
        }


@dataclass(frozen=True)
class QuestCandidateBundleAudit:
    """Audit discovery metadata for a Quest candidate bundle."""

    audit_schema_version: str
    normalized_index_sha256: str
    ambiguity_map_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "audit_schema_version": self.audit_schema_version,
            "normalized_index_sha256": self.normalized_index_sha256,
            "ambiguity_map_sha256": self.ambiguity_map_sha256,
        }


@dataclass(frozen=True)
class QuestFileEntry:
    """Quest raw file reference and checksum."""

    quest_id: int
    relative_path: str
    raw_sha256: str
    raw_size_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "quest_id": self.quest_id,
            "relative_path": self.relative_path,
            "raw_sha256": self.raw_sha256,
            "raw_size_bytes": self.raw_size_bytes,
        }


@dataclass(frozen=True)
class QuestCandidateBundleManifest:
    """Per-Quest sealed candidate bundle manifest (schema_version 2)."""

    schema_version: int
    kind: str
    manifest_id: str
    source_snapshot: SourceSnapshot
    audit: QuestCandidateBundleAudit
    textmap_manifest: TextMapManifestReference
    quest: QuestFileEntry
    reference_count: int
    references: tuple[QuestTalkReference, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "manifest_id": self.manifest_id,
            "source_snapshot": self.source_snapshot.as_dict(),
            "audit": self.audit.as_dict(),
            "textmap_manifest": self.textmap_manifest.as_dict(),
            "quest": self.quest.as_dict(),
            "reference_count": self.reference_count,
            "references": [r.as_dict() for r in self.references],
        }


@dataclass(frozen=True)
class CorpusInvariants:
    """Aggregated structural and quantitative invariant proofs for the corpus."""

    quest_count: int
    total_talk_references: int
    unique_talk_candidates: int
    expected_occurrences: int
    expected_edges: int
    expected_texts_per_language: int
    expected_branch_nodes: int
    expected_merge_nodes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "quest_count": self.quest_count,
            "total_talk_references": self.total_talk_references,
            "unique_talk_candidates": self.unique_talk_candidates,
            "expected_occurrences": self.expected_occurrences,
            "expected_edges": self.expected_edges,
            "expected_texts_per_language": self.expected_texts_per_language,
            "expected_branch_nodes": self.expected_branch_nodes,
            "expected_merge_nodes": self.expected_merge_nodes,
        }


@dataclass(frozen=True)
class QuestBundleReference:
    """Corpus reference to one per-Quest candidate bundle manifest."""

    quest_id: int
    manifest_id: str
    relative_path: str
    raw_sha256: str
    reference_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "quest_id": self.quest_id,
            "manifest_id": self.manifest_id,
            "relative_path": self.relative_path,
            "raw_sha256": self.raw_sha256,
            "reference_count": self.reference_count,
        }


@dataclass(frozen=True)
class QuestCorpusManifest:
    """Master multi-Quest evidence corpus manifest (schema_version 1)."""

    schema_version: int
    kind: str
    manifest_id: str
    source_snapshot: SourceSnapshot
    textmap_manifest: TextMapManifestReference
    corpus_invariants: CorpusInvariants
    quest_bundles: tuple[QuestBundleReference, ...]
    scalable_scope: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "manifest_id": self.manifest_id,
            "source_snapshot": self.source_snapshot.as_dict(),
            "textmap_manifest": self.textmap_manifest.as_dict(),
            "corpus_invariants": self.corpus_invariants.as_dict(),
            "quest_bundles": [b.as_dict() for b in self.quest_bundles],
            **({"scalable_scope": self.scalable_scope} if self.scalable_scope is not None else {}),
        }


@dataclass(frozen=True)
class VerifiedQuestCorpusManifestSet:
    """Fully validated, cross-file verified multi-Quest corpus manifest bundle set."""

    corpus_manifest: QuestCorpusManifest
    textmap_manifest: TextMapAssetManifest
    quest_candidate_bundles: tuple[QuestCandidateBundleManifest, ...]
    manifest_root: Path


# ---------------------------------------------------------------------------
# Strict Object Parsers
# ---------------------------------------------------------------------------

def parse_source_snapshot(val: Any, context: str = "source_snapshot") -> SourceSnapshot:
    data = _validate_dict(val, context)
    _check_exact_keys(data, {"upstream", "release_label", "commit", "tree"}, context)
    return SourceSnapshot(
        upstream=_validate_non_empty_str(data["upstream"], f"{context}.upstream"),
        release_label=_validate_non_empty_str(data["release_label"], f"{context}.release_label"),
        commit=_validate_git_hex(data["commit"], f"{context}.commit"),
        tree=_validate_git_hex(data["tree"], f"{context}.tree"),
    )


def parse_textmap_manifest_reference(
    val: Any, context: str = "textmap_manifest"
) -> TextMapManifestReference:
    data = _validate_dict(val, context)
    _check_exact_keys(data, {"manifest_id", "relative_path", "raw_sha256"}, context)
    return TextMapManifestReference(
        manifest_id=_validate_non_empty_str(data["manifest_id"], f"{context}.manifest_id"),
        relative_path=_validate_relative_path(data["relative_path"], f"{context}.relative_path"),
        raw_sha256=_validate_sha256(data["raw_sha256"], f"{context}.raw_sha256"),
    )


def parse_textmap_asset_manifest(data: dict[str, Any]) -> TextMapAssetManifest:
    _check_exact_keys(
        data,
        {"schema_version", "kind", "manifest_id", "source_snapshot", "textmaps"},
        "TextMapAssetManifest",
    )

    schema_version = _validate_non_negative_int(data["schema_version"], "schema_version")
    if schema_version != 1:
        raise ManifestError(f"unsupported textmap asset manifest schema_version: {schema_version}, expected 1")

    kind = _validate_non_empty_str(data["kind"], "kind")
    if kind != "textmap_asset_bundle":
        raise ManifestError(f"invalid textmap asset manifest kind: {kind!r}, expected 'textmap_asset_bundle'")

    manifest_id = _validate_non_empty_str(data["manifest_id"], "manifest_id")
    source_snapshot = parse_source_snapshot(data["source_snapshot"])

    raw_textmaps = data["textmaps"]
    if not isinstance(raw_textmaps, list):
        raise ManifestError("textmaps must be a list of TextMap entries")

    entries: list[TextMapEntry] = []
    seen_pairs: set[tuple[str, str]] = set()

    for idx, item in enumerate(raw_textmaps):
        ctx = f"textmaps[{idx}]"
        entry_dict = _validate_dict(item, ctx)
        _check_exact_keys(
            entry_dict,
            {"language", "tier", "relative_path", "raw_sha256", "raw_size_bytes"},
            ctx,
        )
        lang = _validate_non_empty_str(entry_dict["language"], f"{ctx}.language")
        if lang not in {"CHS", "EN", "JP"}:
            raise ManifestError(f"invalid textmap language: {lang!r}, expected CHS, EN, or JP")

        tier = _validate_non_empty_str(entry_dict["tier"], f"{ctx}.tier")
        if tier not in {"main", "medium"}:
            raise ManifestError(f"invalid textmap tier: {tier!r}, expected 'main' or 'medium'")

        pair = (lang, tier)
        if pair in seen_pairs:
            raise ManifestError(f"duplicate textmap language/tier pair: {pair}")
        seen_pairs.add(pair)

        entries.append(
            TextMapEntry(
                language=lang,
                tier=tier,
                relative_path=_validate_relative_path(entry_dict["relative_path"], f"{ctx}.relative_path"),
                raw_sha256=_validate_sha256(entry_dict["raw_sha256"], f"{ctx}.raw_sha256"),
                raw_size_bytes=_validate_non_negative_int(entry_dict["raw_size_bytes"], f"{ctx}.raw_size_bytes"),
            )
        )

    expected_pairs = {
        ("CHS", "main"),
        ("CHS", "medium"),
        ("EN", "main"),
        ("EN", "medium"),
        ("JP", "main"),
        ("JP", "medium"),
    }
    if seen_pairs != expected_pairs:
        missing = expected_pairs - seen_pairs
        raise ManifestError(f"textmaps manifest must cover all 6 CHS/EN/JP main/medium shards; missing: {missing}")

    return TextMapAssetManifest(
        schema_version=schema_version,
        kind=kind,
        manifest_id=manifest_id,
        source_snapshot=source_snapshot,
        textmaps=tuple(entries),
    )


def parse_quest_candidate_bundle_manifest(data: dict[str, Any]) -> QuestCandidateBundleManifest:
    _check_exact_keys(
        data,
        {
            "schema_version",
            "kind",
            "manifest_id",
            "source_snapshot",
            "audit",
            "textmap_manifest",
            "quest",
            "reference_count",
            "references",
        },
        "QuestCandidateBundleManifest",
    )

    schema_version = _validate_non_negative_int(data["schema_version"], "schema_version")
    if schema_version != 2:
        raise ManifestError(
            f"unsupported quest candidate bundle schema_version: {schema_version}, expected 2"
        )

    kind = _validate_non_empty_str(data["kind"], "kind")
    if kind != "quest_talk_candidate_bundle":
        raise ManifestError(
            f"invalid quest candidate bundle kind: {kind!r}, expected 'quest_talk_candidate_bundle'"
        )

    manifest_id = _validate_non_empty_str(data["manifest_id"], "manifest_id")
    source_snapshot = parse_source_snapshot(data["source_snapshot"])

    # Audit
    audit_dict = _validate_dict(data["audit"], "audit")
    _check_exact_keys(
        audit_dict,
        {"audit_schema_version", "normalized_index_sha256", "ambiguity_map_sha256"},
        "audit",
    )
    audit = QuestCandidateBundleAudit(
        audit_schema_version=_validate_non_empty_str(audit_dict["audit_schema_version"], "audit.audit_schema_version"),
        normalized_index_sha256=_validate_sha256(audit_dict["normalized_index_sha256"], "audit.normalized_index_sha256"),
        ambiguity_map_sha256=_validate_sha256(audit_dict["ambiguity_map_sha256"], "audit.ambiguity_map_sha256"),
    )

    # TextMap manifest reference
    textmap_ref = parse_textmap_manifest_reference(data["textmap_manifest"])

    # Quest file entry
    quest_dict = _validate_dict(data["quest"], "quest")
    _check_exact_keys(
        quest_dict,
        {"quest_id", "relative_path", "raw_sha256", "raw_size_bytes"},
        "quest",
    )
    quest = QuestFileEntry(
        quest_id=_validate_non_negative_int(quest_dict["quest_id"], "quest.quest_id"),
        relative_path=_validate_relative_path(quest_dict["relative_path"], "quest.relative_path"),
        raw_sha256=_validate_sha256(quest_dict["raw_sha256"], "quest.raw_sha256"),
        raw_size_bytes=_validate_non_negative_int(quest_dict["raw_size_bytes"], "quest.raw_size_bytes"),
    )

    reference_count = _validate_non_negative_int(data["reference_count"], "reference_count")
    raw_references = data["references"]
    if not isinstance(raw_references, list):
        raise ManifestError("references must be a list of QuestTalkReference entries")
    if len(raw_references) != reference_count:
        raise ManifestError(
            f"reference_count mismatch: declared {reference_count}, actual references count {len(raw_references)}"
        )

    references: list[QuestTalkReference] = []
    for idx, ref_item in enumerate(raw_references):
        ref_ctx = f"references[{idx}]"
        r_dict = _validate_dict(ref_item, ref_ctx)
        _check_exact_keys(
            r_dict,
            {"pointer", "index", "namespace", "talk_id", "candidate_count", "candidates"},
            ref_ctx,
        )

        r_index = _validate_non_negative_int(r_dict["index"], f"{ref_ctx}.index")
        if r_index != idx:
            raise ManifestError(f"{ref_ctx}.index must match list index {idx}, got {r_index}")

        r_ns = _validate_non_empty_str(r_dict["namespace"], f"{ref_ctx}.namespace")
        if r_ns != "QUEST":
            raise ManifestError(f"{ref_ctx}.namespace must be 'QUEST', got {r_ns!r}")

        r_talk_id = _validate_non_negative_int(r_dict["talk_id"], f"{ref_ctx}.talk_id")
        r_cand_count = _validate_non_negative_int(r_dict["candidate_count"], f"{ref_ctx}.candidate_count")
        raw_cands = r_dict["candidates"]
        if not isinstance(raw_cands, list):
            raise ManifestError(f"{ref_ctx}.candidates must be a list")
        if len(raw_cands) != r_cand_count:
            raise ManifestError(
                f"{ref_ctx}.candidate_count mismatch: declared {r_cand_count}, actual {len(raw_cands)}"
            )

        candidates: list[CandidateTalkEntry] = []
        for c_idx, c_item in enumerate(raw_cands):
            c_ctx = f"{ref_ctx}.candidates[{c_idx}]"
            c_dict = _validate_dict(c_item, c_ctx)
            _check_exact_keys(
                c_dict,
                {
                    "relative_path",
                    "normalized_sha256",
                    "node_count",
                    "edge_count",
                    "branch_node_count",
                    "merge_node_count",
                },
                c_ctx,
            )
            candidates.append(
                CandidateTalkEntry(
                    relative_path=_validate_relative_path(c_dict["relative_path"], f"{c_ctx}.relative_path"),
                    normalized_sha256=_validate_sha256(c_dict["normalized_sha256"], f"{c_ctx}.normalized_sha256"),
                    node_count=_validate_non_negative_int(c_dict["node_count"], f"{c_ctx}.node_count"),
                    edge_count=_validate_non_negative_int(c_dict["edge_count"], f"{c_ctx}.edge_count"),
                    branch_node_count=_validate_non_negative_int(c_dict["branch_node_count"], f"{c_ctx}.branch_node_count"),
                    merge_node_count=_validate_non_negative_int(c_dict["merge_node_count"], f"{c_ctx}.merge_node_count"),
                )
            )

        references.append(
            QuestTalkReference(
                pointer=_validate_non_empty_str(r_dict["pointer"], f"{ref_ctx}.pointer"),
                index=r_index,
                namespace=r_ns,
                talk_id=r_talk_id,
                candidate_count=r_cand_count,
                candidates=tuple(candidates),
            )
        )

    return QuestCandidateBundleManifest(
        schema_version=schema_version,
        kind=kind,
        manifest_id=manifest_id,
        source_snapshot=source_snapshot,
        audit=audit,
        textmap_manifest=textmap_ref,
        quest=quest,
        reference_count=reference_count,
        references=tuple(references),
    )


def parse_quest_corpus_manifest(data: dict[str, Any]) -> QuestCorpusManifest:
    scope = data.get("scalable_scope")
    if scope is not None:
        from .quest_talk_scope import validate_scope
        validate_scope(scope)
    _check_exact_keys(
        {k: v for k, v in data.items() if k != "scalable_scope"},
        {
            "schema_version",
            "kind",
            "manifest_id",
            "source_snapshot",
            "textmap_manifest",
            "corpus_invariants",
            "quest_bundles",
        },
        "QuestCorpusManifest",
    )

    schema_version = _validate_non_negative_int(data["schema_version"], "schema_version")
    if schema_version != 1:
        raise ManifestError(f"unsupported quest corpus manifest schema_version: {schema_version}, expected 1")

    kind = _validate_non_empty_str(data["kind"], "kind")
    if kind != "quest_evidence_corpus":
        raise ManifestError(f"invalid quest corpus manifest kind: {kind!r}, expected 'quest_evidence_corpus'")

    manifest_id = _validate_non_empty_str(data["manifest_id"], "manifest_id")
    source_snapshot = parse_source_snapshot(data["source_snapshot"])
    textmap_ref = parse_textmap_manifest_reference(data["textmap_manifest"])

    # Corpus Invariants
    inv_dict = _validate_dict(data["corpus_invariants"], "corpus_invariants")
    _check_exact_keys(
        inv_dict,
        {
            "quest_count",
            "total_talk_references",
            "unique_talk_candidates",
            "expected_occurrences",
            "expected_edges",
            "expected_texts_per_language",
            "expected_branch_nodes",
            "expected_merge_nodes",
        },
        "corpus_invariants",
    )
    corpus_invariants = CorpusInvariants(
        quest_count=_validate_non_negative_int(inv_dict["quest_count"], "corpus_invariants.quest_count"),
        total_talk_references=_validate_non_negative_int(inv_dict["total_talk_references"], "corpus_invariants.total_talk_references"),
        unique_talk_candidates=_validate_non_negative_int(inv_dict["unique_talk_candidates"], "corpus_invariants.unique_talk_candidates"),
        expected_occurrences=_validate_non_negative_int(inv_dict["expected_occurrences"], "corpus_invariants.expected_occurrences"),
        expected_edges=_validate_non_negative_int(inv_dict["expected_edges"], "corpus_invariants.expected_edges"),
        expected_texts_per_language=_validate_non_negative_int(inv_dict["expected_texts_per_language"], "corpus_invariants.expected_texts_per_language"),
        expected_branch_nodes=_validate_non_negative_int(inv_dict["expected_branch_nodes"], "corpus_invariants.expected_branch_nodes"),
        expected_merge_nodes=_validate_non_negative_int(inv_dict["expected_merge_nodes"], "corpus_invariants.expected_merge_nodes"),
    )

    # Quest Bundles
    raw_bundles = data["quest_bundles"]
    if not isinstance(raw_bundles, list):
        raise ManifestError("quest_bundles must be a list of QuestBundleReference entries")

    quest_bundles: list[QuestBundleReference] = []
    seen_quest_ids: set[int] = set()
    seen_manifest_ids: set[str] = set()
    seen_paths: set[str] = set()
    prev_quest_id: int | None = None

    for idx, b_item in enumerate(raw_bundles):
        b_ctx = f"quest_bundles[{idx}]"
        b_dict = _validate_dict(b_item, b_ctx)
        _check_exact_keys(
            b_dict,
            {"quest_id", "manifest_id", "relative_path", "raw_sha256", "reference_count"},
            b_ctx,
        )

        qid = _validate_non_negative_int(b_dict["quest_id"], f"{b_ctx}.quest_id")
        mid = _validate_non_empty_str(b_dict["manifest_id"], f"{b_ctx}.manifest_id")
        rel_path = _validate_relative_path(b_dict["relative_path"], f"{b_ctx}.relative_path")
        raw_sha = _validate_sha256(b_dict["raw_sha256"], f"{b_ctx}.raw_sha256")
        ref_count = _validate_non_negative_int(b_dict["reference_count"], f"{b_ctx}.reference_count")

        if prev_quest_id is not None and qid <= prev_quest_id:
            if qid == prev_quest_id:
                raise ManifestError(f"duplicate quest_id in quest_bundles: {qid}")
            raise ManifestError(
                f"quest_bundles must be sorted strictly ascending by quest_id; encountered {qid} after {prev_quest_id}"
            )
        prev_quest_id = qid

        if mid in seen_manifest_ids:
            raise ManifestError(f"duplicate manifest_id in quest_bundles: {mid!r}")
        seen_manifest_ids.add(mid)

        if rel_path in seen_paths:
            raise ManifestError(f"duplicate relative_path in quest_bundles: {rel_path!r}")
        seen_paths.add(rel_path)

        seen_quest_ids.add(qid)
        quest_bundles.append(
            QuestBundleReference(
                quest_id=qid,
                manifest_id=mid,
                relative_path=rel_path,
                raw_sha256=raw_sha,
                reference_count=ref_count,
            )
        )

    if scope is not None:
        if manifest_id in {"anime-game-data-7.0-aq-7000-7014", "anime-game-data-7.0-r5-quest-71008-pilot", "anime-game-data-7.0-r6-medium-quest-talk"}:
            raise ManifestError("historical frozen manifest cannot use scalable interpretation")
        if scope["snapshot_id"] != "anime-game-data:7.0:" + source_snapshot.commit[:12]:
            raise ManifestError("scalable snapshot identity mismatch")
        if scope["manifest_id"] != manifest_id or scope["source_commit"] != source_snapshot.commit or scope["source_tree"] != source_snapshot.tree:
            raise ManifestError("scalable scope identity mismatch")
        if scope["quest_selection"] != [b.quest_id for b in quest_bundles]:
            raise ManifestError("scalable Quest selection mismatch")
    return QuestCorpusManifest(
        schema_version=schema_version,
        kind=kind,
        manifest_id=manifest_id,
        source_snapshot=source_snapshot,
        textmap_manifest=textmap_ref,
        corpus_invariants=corpus_invariants,
        quest_bundles=tuple(quest_bundles),
        scalable_scope=scope,
    )


# ---------------------------------------------------------------------------
# Public Loaders & Cross-File Verifier
# ---------------------------------------------------------------------------

def load_textmap_asset_manifest(path: str | Path) -> TextMapAssetManifest:
    """Load and validate a TextMap asset manifest file."""
    manifest_path = Path(path).resolve()
    data, _ = _read_and_verify_json_file(manifest_path)
    return parse_textmap_asset_manifest(data)


def load_quest_candidate_bundle_manifest(path: str | Path) -> QuestCandidateBundleManifest:
    """Load and validate a per-Quest candidate bundle v2 manifest file."""
    manifest_path = Path(path).resolve()
    data, _ = _read_and_verify_json_file(manifest_path)
    return parse_quest_candidate_bundle_manifest(data)


def load_quest_corpus_manifest(path: str | Path) -> QuestCorpusManifest:
    """Load and validate a master multi-Quest corpus manifest file."""
    manifest_path = Path(path).resolve()
    data, _ = _read_and_verify_json_file(manifest_path)
    return parse_quest_corpus_manifest(data)


def load_and_verify_quest_corpus_manifest(
    path: str | Path,
    manifest_root: str | Path | None = None,
) -> VerifiedQuestCorpusManifestSet:
    """Load master corpus manifest, read and verify all referenced manifests.

    Verifications performed:
    1. Resolve all relative paths from manifest_root (or master's parent directory).
    2. Read shared TextMap manifest and verify raw byte SHA-256 matches master reference.
    3. Read all per-Quest bundle manifests and verify raw byte SHA-256 matches master entry.
    4. Verify source_snapshot is strictly identical across master, textmaps, and bundles.
    5. Verify manifest_id matches referenced values.
    6. Verify textmap_manifest reference is strictly identical across master and all bundles.
    7. Verify quest_id, reference_count in each bundle match master entry.
    8. Verify corpus_invariants by recomputing quest_count, total_talk_references,
       unique_talk_candidates, expected_occurrences, expected_edges, and expected_texts.

    Returns an immutable VerifiedQuestCorpusManifestSet.
    """
    master_path = Path(path).resolve()
    if manifest_root is not None:
        root = Path(manifest_root).resolve()
    else:
        root = master_path.parent.resolve()

    # 1. Load Master Manifest
    master = load_quest_corpus_manifest(master_path)

    # 2. Resolve and verify shared TextMap manifest
    textmap_file = _resolve_manifest_file(root, master.textmap_manifest.relative_path)
    tm_data, _ = _read_and_verify_json_file(textmap_file, master.textmap_manifest.raw_sha256)
    textmap_manifest = parse_textmap_asset_manifest(tm_data)

    if textmap_manifest.manifest_id != master.textmap_manifest.manifest_id:
        raise ManifestError(
            f"textmap manifest_id mismatch: master expects {master.textmap_manifest.manifest_id!r}, "
            f"but file declares {textmap_manifest.manifest_id!r}"
        )
    if textmap_manifest.source_snapshot != master.source_snapshot:
        raise ManifestError(
            f"source_snapshot mismatch between master corpus manifest and textmap manifest: "
            f"{master.source_snapshot} != {textmap_manifest.source_snapshot}"
        )

    # 3. Resolve, verify, and load all per-Quest candidate bundles
    loaded_bundles: list[QuestCandidateBundleManifest] = []
    all_candidate_paths: list[str] = []
    physical_candidates: dict[str, CandidateTalkEntry] = {}
    total_node_count = 0
    total_edge_count = 0
    total_branch_count = 0
    total_merge_count = 0

    for bundle_ref in master.quest_bundles:
        bundle_file = _resolve_manifest_file(root, bundle_ref.relative_path)
        b_data, _ = _read_and_verify_json_file(bundle_file, bundle_ref.raw_sha256)
        bundle = parse_quest_candidate_bundle_manifest(b_data)

        # Cross-file identity & consistency checks
        if bundle.manifest_id != bundle_ref.manifest_id:
            raise ManifestError(
                f"manifest_id mismatch for quest {bundle_ref.quest_id}: master expects {bundle_ref.manifest_id!r}, "
                f"bundle declares {bundle.manifest_id!r}"
            )
        if bundle.quest.quest_id != bundle_ref.quest_id:
            raise ManifestError(
                f"quest_id mismatch: master entry expects {bundle_ref.quest_id}, bundle declares {bundle.quest.quest_id}"
            )
        if bundle.reference_count != bundle_ref.reference_count:
            raise ManifestError(
                f"reference_count mismatch for quest {bundle_ref.quest_id}: master entry expects {bundle_ref.reference_count}, "
                f"bundle declares {bundle.reference_count}"
            )
        if bundle.source_snapshot != master.source_snapshot:
            raise ManifestError(
                f"source_snapshot mismatch between master corpus manifest and quest {bundle.quest.quest_id} bundle: "
                f"{master.source_snapshot} != {bundle.source_snapshot}"
            )
        if bundle.textmap_manifest != master.textmap_manifest:
            raise ManifestError(
                f"textmap manifest reference mismatch in quest {bundle.quest.quest_id} bundle: "
                f"bundle references {bundle.textmap_manifest}, but master expects {master.textmap_manifest}"
            )

        # Accumulate candidate statistics for corpus invariant validation
        for ref in bundle.references:
            for cand in ref.candidates:
                all_candidate_paths.append(cand.relative_path)
                if master.scalable_scope is not None:
                    prior = physical_candidates.get(cand.relative_path)
                    if prior is not None:
                        if prior != cand:
                            raise ManifestError("repeated physical candidate metadata drift")
                        continue
                    physical_candidates[cand.relative_path] = cand
                total_node_count += cand.node_count
                total_edge_count += cand.edge_count
                total_branch_count += cand.branch_node_count
                total_merge_count += cand.merge_node_count

        loaded_bundles.append(bundle)

    # 4. Verify Corpus Invariants
    inv = master.corpus_invariants

    if inv.quest_count != len(loaded_bundles):
        raise ManifestError(
            f"corpus_invariants.quest_count mismatch: expected {inv.quest_count}, actual {len(loaded_bundles)}"
        )

    computed_total_refs = sum(b.reference_count for b in loaded_bundles)
    if inv.total_talk_references != computed_total_refs:
        raise ManifestError(
            f"corpus_invariants.total_talk_references mismatch: expected {inv.total_talk_references}, "
            f"actual computed {computed_total_refs}"
        )

    computed_unique_cands = len(set(all_candidate_paths))
    if inv.unique_talk_candidates != computed_unique_cands:
        raise ManifestError(
            f"corpus_invariants.unique_talk_candidates mismatch: expected {inv.unique_talk_candidates}, "
            f"actual computed {computed_unique_cands}"
        )

    if inv.expected_occurrences != total_node_count:
        raise ManifestError(
            f"corpus_invariants.expected_occurrences mismatch: expected {inv.expected_occurrences}, "
            f"actual computed {total_node_count}"
        )

    if inv.expected_edges != total_edge_count:
        raise ManifestError(
            f"corpus_invariants.expected_edges mismatch: expected {inv.expected_edges}, "
            f"actual computed {total_edge_count}"
        )

    # Each dialogue node occurrence has 1 text per language
    if inv.expected_texts_per_language != total_node_count:
        raise ManifestError(
            f"corpus_invariants.expected_texts_per_language mismatch: expected {inv.expected_texts_per_language}, "
            f"actual computed {total_node_count}"
        )

    if inv.expected_branch_nodes != total_branch_count:
        raise ManifestError(
            f"corpus_invariants.expected_branch_nodes mismatch: expected {inv.expected_branch_nodes}, "
            f"actual computed {total_branch_count}"
        )

    if inv.expected_merge_nodes != total_merge_count:
        raise ManifestError(
            f"corpus_invariants.expected_merge_nodes mismatch: expected {inv.expected_merge_nodes}, "
            f"actual computed {total_merge_count}"
        )

    return VerifiedQuestCorpusManifestSet(
        corpus_manifest=master,
        textmap_manifest=textmap_manifest,
        quest_candidate_bundles=tuple(loaded_bundles),
        manifest_root=root,
    )
