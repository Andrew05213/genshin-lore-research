from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .db import Database
from .talk_textmap import LANGUAGES, TEXTMAP_SHARD_PATHS
from .util import canonical_json, digest_id, sha256_bytes


@dataclass(frozen=True)
class TextMapManifestEntry:
    language: str
    shard: str
    relative_path: str
    raw_sha256: str
    raw_size_bytes: int


@dataclass(frozen=True)
class CandidateManifestEntry:
    relative_path: str
    raw_sha256: str
    normalized_checksum: str
    raw_size_bytes: int
    node_count: int | None = None
    edge_count: int | None = None

    def identity(self) -> tuple[str, str, str, int]:
        return (
            self.relative_path,
            self.raw_sha256,
            self.normalized_checksum,
            self.raw_size_bytes,
        )


@dataclass(frozen=True)
class CandidateScopeManifest:
    manifest_id: str
    manifest_checksum: str
    snapshot_id: str
    snapshot_commit: str
    snapshot_tree: str
    audit_mapping_version: str
    raw_byte_basis: str
    audit_algorithm_version: str
    audit_cache_digest: str
    audit_cache_state: str
    inventory_complete: bool
    namespace: str
    talk_id: str
    candidate_count: int
    candidates: tuple[CandidateManifestEntry, ...]
    structural_errors: tuple[str, ...] = ()

    @property
    def proof_inventory_complete(self) -> bool:
        return self.inventory_complete and self.audit_cache_state == "verified"


@dataclass(frozen=True)
class QuestCandidateBundleReference:
    pointer: str
    index: int
    namespace: str
    talk_id: str
    candidate_count: int
    candidates: tuple[CandidateManifestEntry, ...]
    structural_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class QuestCandidateBundleManifest:
    manifest_id: str
    manifest_checksum: str
    snapshot_id: str
    snapshot_commit: str
    snapshot_tree: str
    source_id: str
    release_label: str
    audit_mapping_version: str
    raw_byte_basis: str
    audit_algorithm_version: str
    audit_cache_digest: str
    audit_cache_state: str
    inventory_complete: bool
    quest_relative_path: str
    quest_raw_sha256: str
    quest_raw_size_bytes: int
    quest_id: int
    reference_count: int
    references: tuple[QuestCandidateBundleReference, ...]
    textmaps: tuple[TextMapManifestEntry, ...] = ()
    structural_errors: tuple[str, ...] = ()

    @property
    def proof_inventory_complete(self) -> bool:
        return self.inventory_complete and self.audit_cache_state == "verified"

    def reference_scope(
        self,
        talk_id: int | str,
        namespace: str = "QUEST",
        *,
        pointer: str | None = None,
        index: int | None = None,
    ) -> CandidateScopeManifest:
        str_talk_id = str(talk_id)
        for ref in self.references:
            if index is not None and ref.index != index:
                continue
            if pointer is not None and ref.pointer != pointer:
                continue
            if ref.talk_id == str_talk_id and ref.namespace == namespace:
                return CandidateScopeManifest(
                    manifest_id=self.manifest_id,
                    manifest_checksum=self.manifest_checksum,
                    snapshot_id=self.snapshot_id,
                    snapshot_commit=self.snapshot_commit,
                    snapshot_tree=self.snapshot_tree,
                    audit_mapping_version=self.audit_mapping_version,
                    raw_byte_basis=self.raw_byte_basis,
                    audit_algorithm_version=self.audit_algorithm_version,
                    audit_cache_digest=self.audit_cache_digest,
                    audit_cache_state=self.audit_cache_state,
                    inventory_complete=self.inventory_complete,
                    namespace=ref.namespace,
                    talk_id=ref.talk_id,
                    candidate_count=ref.candidate_count,
                    candidates=ref.candidates,
                    structural_errors=tuple(
                        self.structural_errors + ref.structural_errors
                    ),
                )
        return CandidateScopeManifest(
            manifest_id=self.manifest_id,
            manifest_checksum=self.manifest_checksum,
            snapshot_id=self.snapshot_id,
            snapshot_commit=self.snapshot_commit,
            snapshot_tree=self.snapshot_tree,
            audit_mapping_version=self.audit_mapping_version,
            raw_byte_basis=self.raw_byte_basis,
            audit_algorithm_version=self.audit_algorithm_version,
            audit_cache_digest=self.audit_cache_digest,
            audit_cache_state=self.audit_cache_state,
            inventory_complete=self.inventory_complete,
            namespace=namespace,
            talk_id=str_talk_id,
            candidate_count=0,
            candidates=(),
            structural_errors=tuple(
                self.structural_errors
                + (f"Talk {namespace}:{str_talk_id} not in bundle manifest",)
            ),
        )


@dataclass(frozen=True)
class QuestEvidenceResolutionLink:
    document_evidence_id: str
    document_id: str
    ingest_run_id: str
    namespace: str
    talk_id: str
    result: str
    evidence_container_id: str | None
    identity_group_id: str | None
    candidate_container_ids: tuple[str, ...]
    source_record_id: str
    candidate_scope_proof_id: str
    candidate_manifest_id: str
    candidate_manifest_checksum: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.document_evidence_id,
            "document_evidence_id": self.document_evidence_id,
            "document_id": self.document_id,
            "ingest_run_id": self.ingest_run_id,
            "namespace": self.namespace,
            "talk_id": self.talk_id,
            "result": self.result,
            "evidence_container_id": self.evidence_container_id,
            "identity_group_id": self.identity_group_id,
            "candidate_container_ids": list(self.candidate_container_ids),
            "source_record_id": self.source_record_id,
            "candidate_scope_proof_id": self.candidate_scope_proof_id,
            "candidate_manifest_id": self.candidate_manifest_id,
            "candidate_manifest_checksum": self.candidate_manifest_checksum,
        }


def _required_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"candidate scope {field} must be an object")
    return value


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"candidate scope {field} must be a non-empty string")
    return value


def _safe_relative_path(value: Any) -> str:
    relative_path = _required_text(value, "candidate.relative_path")
    path = PurePosixPath(relative_path)
    if path.is_absolute() or ".." in path.parts or "\\" in relative_path:
        raise ValueError("candidate scope candidate.relative_path must be repository-relative")
    return relative_path


_CURRENT_DISCOVERY_VERSION = "phase3-evidence-identity-0.4"
_GIT_BLOB_RAW_BYTE_BASIS = "pinned_git_blob"


def _valid_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _required_nonnegative_int(value: Any, field: str) -> int:
    if not _valid_nonnegative_int(value):
        raise ValueError(f"candidate scope {field} must be a non-negative integer")
    return value


def _audit_cache_semantic_errors(
    cache: Any,
    *,
    snapshot_id: str,
    snapshot_commit: str,
    snapshot_tree: str,
    audit_algorithm_version: str,
    audit_mapping_version: str,
    raw_byte_basis: str,
    inventory_complete: bool,
    candidate_file_count: int,
    valid_file_count: int,
    invalid_file_count: int,
    namespace: str,
    talk_id: str,
    lookup_method: str,
    manifest_candidates: tuple[CandidateManifestEntry, ...],
) -> list[str]:
    """Verify one sealed lookup against an already-generated discovery cache."""

    if not isinstance(cache, dict):
        return ["audit cache root must be an object"]

    errors: list[str] = []
    if audit_algorithm_version != _CURRENT_DISCOVERY_VERSION:
        errors.append("manifest discovery algorithm is not phase3-evidence-identity-0.4")
    if raw_byte_basis != _GIT_BLOB_RAW_BYTE_BASIS:
        errors.append("manifest raw byte basis is not pinned_git_blob")
    for cache_field, expected, label in (
        ("snapshot_id", snapshot_id, "snapshot_id"),
        ("commit", snapshot_commit, "commit"),
        ("tree", snapshot_tree, "tree"),
        ("discovery_version", audit_algorithm_version, "discovery version"),
        ("mapping_version", audit_mapping_version, "mapping version"),
    ):
        if cache.get(cache_field) != expected:
            errors.append(f"audit cache {label} does not match manifest")
    if cache.get("inventory_complete") is not inventory_complete:
        errors.append("audit cache inventory_complete does not match manifest")
    cache_candidate_file_count = cache.get("candidate_file_count")
    cache_valid_file_count = cache.get("valid_file_count")
    cache_invalid_file_count = cache.get("invalid_file_count")
    if (
        cache_candidate_file_count != candidate_file_count
        or not _valid_nonnegative_int(cache_candidate_file_count)
    ):
        errors.append("audit cache candidate_file_count does not match manifest")
    if (
        cache_valid_file_count != valid_file_count
        or not _valid_nonnegative_int(cache_valid_file_count)
    ):
        errors.append("audit cache valid_file_count does not match manifest")
    if cache_invalid_file_count != invalid_file_count or not _valid_nonnegative_int(
        cache_invalid_file_count
    ):
        errors.append("audit cache invalid_file_count does not match manifest")
    if (
        _valid_nonnegative_int(cache_candidate_file_count)
        and _valid_nonnegative_int(cache_valid_file_count)
        and _valid_nonnegative_int(cache_invalid_file_count)
        and cache_candidate_file_count != cache_valid_file_count + cache_invalid_file_count
    ):
        errors.append(
            "audit cache candidate_file_count must equal valid_file_count plus invalid_file_count"
        )

    cache_candidates = cache.get("candidates")
    if not isinstance(cache_candidates, list):
        return [*errors, "audit cache candidates must be an array"]

    if _valid_nonnegative_int(cache_valid_file_count) and len(cache_candidates) != cache_valid_file_count:
        errors.append("audit cache candidates length does not match valid_file_count")

    matching_talk_id = [
        candidate
        for candidate in cache_candidates
        if isinstance(candidate, dict) and str(candidate.get("talk_id")) == talk_id
    ]
    for candidate in matching_talk_id:
        if not isinstance(candidate.get("native_namespace"), str):
            errors.append("audit cache candidate lacks native_namespace")

    selected = [
        candidate
        for candidate in matching_talk_id
        if candidate.get("native_namespace") == namespace
    ]
    cache_identities: list[tuple[str, str, str, int]] = []
    for candidate in selected:
        relative_path = candidate.get("relative_path")
        raw_sha256 = candidate.get("raw_sha256")
        normalized_checksum = candidate.get("normalized_checksum")
        size_bytes = candidate.get("size_bytes")
        if not all(isinstance(value, str) and value for value in (relative_path, raw_sha256, normalized_checksum)):
            errors.append("audit cache candidate is missing an identity checksum or path")
            continue
        if not _valid_nonnegative_int(size_bytes):
            errors.append("audit cache candidate is missing a raw byte size")
            continue
        if candidate.get("parse_status") != "valid":
            errors.append("audit cache candidate is not valid")
        if candidate.get("native_id_verified") is not True:
            errors.append("audit cache candidate native ID is not verified")
        if candidate.get("discovery_method") != lookup_method:
            errors.append("audit cache candidate lookup method does not match manifest")
        cache_identities.append((relative_path, raw_sha256, normalized_checksum, size_bytes))

    if sorted(cache_identities) != sorted(candidate.identity() for candidate in manifest_candidates):
        errors.append("audit cache-derived candidate subset does not match manifest")
    return errors


def load_candidate_scope_manifest(
    manifest_path: str | Path,
    *,
    audit_cache_path: str | Path | None = None,
) -> CandidateScopeManifest:
    """Load a sealed candidate scope without discovering any Talk files.

    The optional audit cache is read only: its bytes, metadata, inventory state,
    and exact native-key candidate subset must agree with the sealed manifest.
    It is never walked or rebuilt; without it, the scope is explicitly
    incomplete and therefore cannot promote a link to ``evidence_only``.
    """

    path = Path(manifest_path)
    try:
        raw_manifest = path.read_bytes()
        manifest = json.loads(raw_manifest.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read candidate scope manifest {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("candidate scope manifest root must be an object")

    source_snapshot = _required_object(manifest.get("source_snapshot"), "source_snapshot")
    audit = _required_object(manifest.get("audit"), "audit")
    lookup = _required_object(manifest.get("lookup"), "lookup")
    candidates_value = manifest.get("candidates")
    if not isinstance(candidates_value, list):
        raise ValueError("candidate scope candidates must be an array")
    if manifest.get("kind") != "quest_talk_candidate_scope":
        raise ValueError("candidate scope kind must be quest_talk_candidate_scope")
    if manifest.get("schema_version") != 1:
        raise ValueError("candidate scope schema_version must be 1")
    inventory_complete = audit.get("inventory_complete")
    if not isinstance(inventory_complete, bool):
        raise ValueError("candidate scope audit.inventory_complete must be boolean")
    candidate_count = _required_nonnegative_int(manifest.get("candidate_count"), "candidate_count")

    structural_errors: list[str] = []
    candidates: list[CandidateManifestEntry] = []
    for index, value in enumerate(candidates_value):
        candidate = _required_object(value, f"candidates[{index}]")
        raw_size_bytes = candidate.get("raw_size_bytes")
        if not _valid_nonnegative_int(raw_size_bytes):
            structural_errors.append(f"candidates[{index}].raw_size_bytes must be a non-negative integer")
            raw_size_bytes = -1
        candidates.append(
            CandidateManifestEntry(
                relative_path=_safe_relative_path(candidate.get("relative_path")),
                raw_sha256=_required_text(candidate.get("raw_sha256"), "candidate.raw_sha256"),
                normalized_checksum=_required_text(
                    candidate.get("normalized_checksum"), "candidate.normalized_checksum"
                ),
                raw_size_bytes=raw_size_bytes,
            )
        )

    declared_audit_digest = _required_text(audit.get("audit_cache_sha256"), "audit.audit_cache_sha256")
    audit_mapping_version = audit.get("mapping_version")
    if not isinstance(audit_mapping_version, str) or not audit_mapping_version:
        structural_errors.append("audit.mapping_version must be a non-empty string")
        audit_mapping_version = ""
    raw_byte_basis = audit.get("raw_byte_basis")
    if not isinstance(raw_byte_basis, str) or not raw_byte_basis:
        structural_errors.append("audit.raw_byte_basis must be a non-empty string")
        raw_byte_basis = ""
    audit_algorithm_version = _required_text(
        audit.get("discovery_algorithm_version"), "audit.discovery_algorithm_version"
    )
    lookup_method = _required_text(audit.get("lookup_method"), "audit.lookup_method")
    audit_candidate_file_count = audit.get("candidate_file_count")
    audit_valid_file_count = audit.get("valid_file_count")
    audit_invalid_file_count = audit.get("invalid_file_count")
    if not _valid_nonnegative_int(audit_candidate_file_count):
        structural_errors.append("audit.candidate_file_count must be a non-negative integer")
        audit_candidate_file_count = -1
    if not _valid_nonnegative_int(audit_valid_file_count):
        structural_errors.append("audit.valid_file_count must be a non-negative integer")
        audit_valid_file_count = -1
    if not _valid_nonnegative_int(audit_invalid_file_count):
        structural_errors.append("audit.invalid_file_count must be a non-negative integer")
        audit_invalid_file_count = -1

    audit_cache_state = "unavailable"
    if candidate_count != len(candidates):
        structural_errors.append("candidate_count does not equal candidates length")
    if audit_cache_path is not None:
        try:
            raw_audit_cache = Path(audit_cache_path).read_bytes()
        except OSError:
            audit_cache_state = "unavailable"
        else:
            if sha256_bytes(raw_audit_cache) != declared_audit_digest:
                audit_cache_state = "mismatch"
            else:
                try:
                    audit_cache = json.loads(raw_audit_cache.decode("utf-8-sig"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    structural_errors.append("audit cache is not valid JSON")
                    audit_cache_state = "mismatch"
                else:
                    cache_errors = _audit_cache_semantic_errors(
                        audit_cache,
                        snapshot_id=_required_text(
                            source_snapshot.get("snapshot_id"), "source_snapshot.snapshot_id"
                        ),
                        snapshot_commit=_required_text(source_snapshot.get("commit"), "source_snapshot.commit"),
                        snapshot_tree=_required_text(source_snapshot.get("tree"), "source_snapshot.tree"),
                        audit_algorithm_version=audit_algorithm_version,
                        audit_mapping_version=audit_mapping_version,
                        raw_byte_basis=raw_byte_basis,
                        inventory_complete=inventory_complete,
                        candidate_file_count=audit_candidate_file_count,
                        valid_file_count=audit_valid_file_count,
                        invalid_file_count=audit_invalid_file_count,
                        namespace=_required_text(lookup.get("namespace"), "lookup.namespace"),
                        talk_id=_required_text(lookup.get("talk_id"), "lookup.talk_id"),
                        lookup_method=lookup_method,
                        manifest_candidates=tuple(candidates),
                    )
                    if cache_errors:
                        structural_errors.extend(cache_errors)
                        audit_cache_state = "mismatch"
                    else:
                        audit_cache_state = "verified"

    return CandidateScopeManifest(
        manifest_id=_required_text(manifest.get("manifest_id"), "manifest_id"),
        manifest_checksum=sha256_bytes(raw_manifest),
        snapshot_id=_required_text(source_snapshot.get("snapshot_id"), "source_snapshot.snapshot_id"),
        snapshot_commit=_required_text(source_snapshot.get("commit"), "source_snapshot.commit"),
        snapshot_tree=_required_text(source_snapshot.get("tree"), "source_snapshot.tree"),
        audit_algorithm_version=audit_algorithm_version,
        audit_mapping_version=audit_mapping_version,
        raw_byte_basis=raw_byte_basis,
        audit_cache_digest=declared_audit_digest,
        audit_cache_state=audit_cache_state,
        inventory_complete=inventory_complete,
        namespace=_required_text(lookup.get("namespace"), "lookup.namespace"),
        talk_id=_required_text(lookup.get("talk_id"), "lookup.talk_id"),
        candidate_count=candidate_count,
        candidates=tuple(candidates),
        structural_errors=tuple(structural_errors),
    )


def load_quest_candidate_bundle_manifest(
    manifest_path: str | Path,
    *,
    audit_cache_path: str | Path | None = None,
) -> QuestCandidateBundleManifest:
    """Load a sealed candidate bundle manifest without discovering Talk files."""
    path = Path(manifest_path)
    try:
        raw_manifest = path.read_bytes()
        manifest = json.loads(raw_manifest.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read candidate bundle manifest {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("candidate bundle manifest root must be an object")

    if manifest.get("schema_version") != 1:
        raise ValueError("candidate bundle manifest schema_version must be 1")
    if manifest.get("kind") != "quest_talk_candidate_bundle":
        raise ValueError("candidate bundle manifest kind must be quest_talk_candidate_bundle")

    manifest_id_val = _required_text(manifest.get("manifest_id"), "manifest_id")
    source_snapshot = _required_object(manifest.get("source_snapshot"), "source_snapshot")
    source_id_val = _required_text(source_snapshot.get("source_id"), "source_snapshot.source_id")
    release_label_val = _required_text(
        source_snapshot.get("release_label"), "source_snapshot.release_label"
    )
    audit = _required_object(manifest.get("audit"), "audit")
    quest = _required_object(manifest.get("quest"), "quest")
    references_val = manifest.get("references")
    if not isinstance(references_val, list):
        raise ValueError("candidate bundle references must be an array")
    reference_count = _required_nonnegative_int(manifest.get("reference_count"), "reference_count")
    if reference_count != len(references_val):
        raise ValueError("reference_count does not equal references length")

    inventory_complete = audit.get("inventory_complete")
    if not isinstance(inventory_complete, bool):
        raise ValueError("audit.inventory_complete must be boolean")
    declared_audit_digest = _required_text(audit.get("audit_cache_sha256"), "audit.audit_cache_sha256")
    audit_algorithm_version = _required_text(
        audit.get("discovery_algorithm_version"), "audit.discovery_algorithm_version"
    )
    lookup_method = _required_text(audit.get("lookup_method"), "audit.lookup_method")
    audit_mapping_version = _required_text(audit.get("mapping_version"), "audit.mapping_version")
    raw_byte_basis = _required_text(audit.get("raw_byte_basis"), "audit.raw_byte_basis")
    audit_candidate_file_count = _required_nonnegative_int(
        audit.get("candidate_file_count"), "audit.candidate_file_count"
    )
    audit_valid_file_count = _required_nonnegative_int(
        audit.get("valid_file_count"), "audit.valid_file_count"
    )
    audit_invalid_file_count = _required_nonnegative_int(
        audit.get("invalid_file_count"), "audit.invalid_file_count"
    )

    quest_relative_path = _safe_relative_path(quest.get("relative_path"))
    quest_raw_sha256 = _required_text(quest.get("raw_sha256"), "quest.raw_sha256")
    quest_raw_size = _required_nonnegative_int(quest.get("raw_size_bytes"), "quest.raw_size_bytes")
    quest_id_val = _required_nonnegative_int(quest.get("quest_id"), "quest.quest_id")

    structural_errors: list[str] = []
    parsed_refs: list[QuestCandidateBundleReference] = []

    textmaps_val = manifest.get("textmaps")
    parsed_textmaps: list[TextMapManifestEntry] = []
    if not isinstance(textmaps_val, list):
        structural_errors.append("candidate bundle textmaps must be an array")
    elif len(textmaps_val) != 6:
        structural_errors.append(
            f"candidate bundle textmaps must contain exactly 6 shards, got {len(textmaps_val)}"
        )
    else:
        expected_shard_pairs = [
            ("CHS", "main"),
            ("CHS", "medium"),
            ("EN", "main"),
            ("EN", "medium"),
            ("JP", "main"),
            ("JP", "medium"),
        ]
        for tm_idx, tm_item in enumerate(textmaps_val):
            if not isinstance(tm_item, dict):
                structural_errors.append(f"textmaps[{tm_idx}] must be an object")
                continue
            tm_lang = tm_item.get("language")
            tm_shard = tm_item.get("shard")
            exp_lang, exp_shard = expected_shard_pairs[tm_idx]
            if tm_lang != exp_lang or tm_shard != exp_shard:
                structural_errors.append(
                    f"textmaps[{tm_idx}] expected language {exp_lang!r} and shard {exp_shard!r}, got language {tm_lang!r} and shard {tm_shard!r}"
                )
            if tm_lang not in LANGUAGES or tm_shard not in ("main", "medium"):
                structural_errors.append(
                    f"textmaps[{tm_idx}] has invalid language or shard: {tm_lang!r}/{tm_shard!r}"
                )
                continue
            exp_path = TEXTMAP_SHARD_PATHS[tm_lang][tm_shard]
            tm_path = tm_item.get("relative_path")
            if tm_path != exp_path:
                structural_errors.append(
                    f"textmaps[{tm_idx}] relative_path {tm_path!r} does not match expected {exp_path!r}"
                )
            tm_sha = tm_item.get("raw_sha256")
            if not isinstance(tm_sha, str) or len(tm_sha) != 64:
                structural_errors.append(f"textmaps[{tm_idx}].raw_sha256 must be a 64-character hex string")
                tm_sha = ""
            tm_size = tm_item.get("raw_size_bytes")
            if not _valid_nonnegative_int(tm_size):
                structural_errors.append(f"textmaps[{tm_idx}].raw_size_bytes must be a non-negative integer")
                tm_size = -1
            parsed_textmaps.append(
                TextMapManifestEntry(
                    language=str(tm_lang),
                    shard=str(tm_shard),
                    relative_path=str(tm_path),
                    raw_sha256=str(tm_sha),
                    raw_size_bytes=int(tm_size),
                )
            )

    for index, ref_item in enumerate(references_val):
        ref_obj = _required_object(ref_item, f"references[{index}]")
        pointer = _required_text(ref_obj.get("pointer"), f"references[{index}].pointer")
        expected_pointer = f"/OJACLOOEAMG/{index}"
        if pointer != expected_pointer:
            structural_errors.append(
                f"references[{index}].pointer {pointer!r} does not match expected {expected_pointer!r}"
            )
        ref_index = _required_nonnegative_int(ref_obj.get("index"), f"references[{index}].index")
        if ref_index != index:
            structural_errors.append(f"references[{index}].index {ref_index} does not match array position {index}")
        ref_namespace = _required_text(ref_obj.get("namespace"), f"references[{index}].namespace")
        ref_talk_id = _required_text(str(ref_obj.get("talk_id")), f"references[{index}].talk_id")
        ref_cand_count = _required_nonnegative_int(
            ref_obj.get("candidate_count"), f"references[{index}].candidate_count"
        )
        cands_val = ref_obj.get("candidates")
        if not isinstance(cands_val, list):
            structural_errors.append(f"references[{index}].candidates must be an array")
            cands_val = []
        if ref_cand_count != len(cands_val):
            structural_errors.append(
                f"references[{index}].candidate_count {ref_cand_count} does not match candidates length {len(cands_val)}"
            )

        ref_candidates: list[CandidateManifestEntry] = []
        for c_idx, c_item in enumerate(cands_val):
            c_obj = _required_object(c_item, f"references[{index}].candidates[{c_idx}]")
            raw_size_bytes = c_obj.get("raw_size_bytes")
            if not _valid_nonnegative_int(raw_size_bytes):
                structural_errors.append(
                    f"references[{index}].candidates[{c_idx}].raw_size_bytes must be a non-negative integer"
                )
                raw_size_bytes = -1
            node_cnt = c_obj.get("node_count")
            if node_cnt is not None and not _valid_nonnegative_int(node_cnt):
                structural_errors.append(
                    f"references[{index}].candidates[{c_idx}].node_count must be a non-negative integer"
                )
                node_cnt = None
            edge_cnt = c_obj.get("edge_count")
            if edge_cnt is not None and not _valid_nonnegative_int(edge_cnt):
                structural_errors.append(
                    f"references[{index}].candidates[{c_idx}].edge_count must be a non-negative integer"
                )
                edge_cnt = None

            ref_candidates.append(
                CandidateManifestEntry(
                    relative_path=_safe_relative_path(c_obj.get("relative_path")),
                    raw_sha256=_required_text(
                        c_obj.get("raw_sha256"), f"references[{index}].candidates[{c_idx}].raw_sha256"
                    ),
                    normalized_checksum=_required_text(
                        c_obj.get("normalized_checksum"),
                        f"references[{index}].candidates[{c_idx}].normalized_checksum",
                    ),
                    raw_size_bytes=raw_size_bytes,
                    node_count=node_cnt,
                    edge_count=edge_cnt,
                )
            )

        parsed_refs.append(
            QuestCandidateBundleReference(
                pointer=pointer,
                index=ref_index,
                namespace=ref_namespace,
                talk_id=ref_talk_id,
                candidate_count=ref_cand_count,
                candidates=tuple(ref_candidates),
            )
        )

    audit_cache_state = "unavailable"
    if audit_cache_path is not None:
        try:
            raw_audit_cache = Path(audit_cache_path).read_bytes()
        except OSError:
            audit_cache_state = "unavailable"
        else:
            if sha256_bytes(raw_audit_cache) != declared_audit_digest:
                audit_cache_state = "mismatch"
            else:
                try:
                    audit_cache = json.loads(raw_audit_cache.decode("utf-8-sig"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    structural_errors.append("audit cache is not valid JSON")
                    audit_cache_state = "mismatch"
                else:
                    for ref in parsed_refs:
                        cache_errors = _audit_cache_semantic_errors(
                            audit_cache,
                            snapshot_id=_required_text(
                                source_snapshot.get("snapshot_id"), "source_snapshot.snapshot_id"
                            ),
                            snapshot_commit=_required_text(
                                source_snapshot.get("commit"), "source_snapshot.commit"
                            ),
                            snapshot_tree=_required_text(
                                source_snapshot.get("tree"), "source_snapshot.tree"
                            ),
                            audit_algorithm_version=audit_algorithm_version,
                            audit_mapping_version=audit_mapping_version,
                            raw_byte_basis=raw_byte_basis,
                            inventory_complete=inventory_complete,
                            candidate_file_count=audit_candidate_file_count,
                            valid_file_count=audit_valid_file_count,
                            invalid_file_count=audit_invalid_file_count,
                            namespace=ref.namespace,
                            talk_id=ref.talk_id,
                            lookup_method=lookup_method,
                            manifest_candidates=ref.candidates,
                        )
                        if cache_errors:
                            structural_errors.extend(cache_errors)
                            audit_cache_state = "mismatch"
                            break
                    else:
                        audit_cache_state = "verified"

    return QuestCandidateBundleManifest(
        manifest_id=manifest_id_val,
        manifest_checksum=sha256_bytes(raw_manifest),
        snapshot_id=_required_text(source_snapshot.get("snapshot_id"), "source_snapshot.snapshot_id"),
        snapshot_commit=_required_text(source_snapshot.get("commit"), "source_snapshot.commit"),
        snapshot_tree=_required_text(source_snapshot.get("tree"), "source_snapshot.tree"),
        source_id=source_id_val,
        release_label=release_label_val,
        audit_algorithm_version=audit_algorithm_version,
        audit_mapping_version=audit_mapping_version,
        raw_byte_basis=raw_byte_basis,
        audit_cache_digest=declared_audit_digest,
        audit_cache_state=audit_cache_state,
        inventory_complete=inventory_complete,
        quest_relative_path=quest_relative_path,
        quest_raw_sha256=quest_raw_sha256,
        quest_raw_size_bytes=quest_raw_size,
        quest_id=quest_id_val,
        reference_count=reference_count,
        references=tuple(parsed_refs),
        textmaps=tuple(parsed_textmaps),
        structural_errors=tuple(structural_errors),
    )


def _reference_row(
    db: Database,
    *,
    source_record_id: str,
    ingest_run_id: str,
    talk_id: str,
) -> sqlite3.Row:
    row = db.conn.execute(
        """SELECT sr.id, sr.record_kind, sr.native_id, sr.pointer,
                  sf.snapshot_id, ir.snapshot_id AS run_snapshot_id
           FROM source_record sr
           JOIN source_file sf ON sf.id=sr.source_file_id
           JOIN ingest_run ir ON ir.id=?
           WHERE sr.id=?""",
        (ingest_run_id, source_record_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"Quest reference source_record does not exist: {source_record_id}")
    if row["record_kind"] != "quest_talk_reference":
        raise ValueError("source_record_id must identify a quest_talk_reference")
    if row["snapshot_id"] != row["run_snapshot_id"]:
        raise ValueError("Quest reference source_record must belong to the resolution snapshot")
    if str(row["native_id"]) != talk_id:
        raise ValueError("Quest reference source_record native talk ID does not match link input")
    return row


def _candidate_rows(
    db: Database,
    *,
    ingest_run_id: str,
    namespace: str,
    talk_id: str,
) -> list[sqlite3.Row]:
    return db.conn.execute(
        """SELECT ec.id, ec.raw_checksum, ec.normalized_checksum,
                  sf.relative_path, sf.size_bytes
           FROM evidence_container ec
           JOIN source_record sr ON sr.id=ec.source_record_id
           JOIN source_file sf ON sf.id=sr.source_file_id
           WHERE ec.ingest_run_id=? AND ec.kind='talk_root'
             AND ec.namespace=? AND ec.talk_id=?
           ORDER BY ec.id""",
        (ingest_run_id, namespace, talk_id),
    ).fetchall()


def _scope_proof_id(
    *,
    ingest_run_id: str,
    namespace: str,
    talk_id: str,
    candidate_scope: CandidateScopeManifest,
) -> str:
    return digest_id(
        "candidate-scope-proof",
        ingest_run_id,
        namespace,
        talk_id,
        candidate_scope.manifest_id,
        candidate_scope.manifest_checksum,
    )


def _ensure_scope_proof(
    db: Database,
    *,
    ingest_run_id: str,
    namespace: str,
    talk_id: str,
    candidate_scope: CandidateScopeManifest,
) -> str:
    proof_id = _scope_proof_id(
        ingest_run_id=ingest_run_id,
        namespace=namespace,
        talk_id=talk_id,
        candidate_scope=candidate_scope,
    )
    proof_complete = int(candidate_scope.proof_inventory_complete)
    metadata = canonical_json(
        {
            "audit_cache_state": candidate_scope.audit_cache_state,
            "manifest_inventory_complete": candidate_scope.inventory_complete,
            "audit_mapping_version": candidate_scope.audit_mapping_version,
            "raw_byte_basis": candidate_scope.raw_byte_basis,
            "candidates": [
                {
                    "relative_path": candidate.relative_path,
                    "raw_sha256": candidate.raw_sha256,
                    "normalized_checksum": candidate.normalized_checksum,
                    "raw_size_bytes": candidate.raw_size_bytes,
                }
                for candidate in candidate_scope.candidates
            ],
            "structural_errors": list(candidate_scope.structural_errors),
        }
    )
    expected = (
        ingest_run_id,
        candidate_scope.manifest_id,
        candidate_scope.manifest_checksum,
        candidate_scope.snapshot_id,
        candidate_scope.snapshot_commit,
        candidate_scope.snapshot_tree,
        candidate_scope.audit_algorithm_version,
        candidate_scope.audit_cache_digest,
        namespace,
        talk_id,
        proof_complete,
        candidate_scope.candidate_count,
        metadata,
    )
    existing = db.conn.execute(
        """SELECT ingest_run_id, manifest_id, manifest_checksum, snapshot_id,
                  snapshot_commit, snapshot_tree, audit_algorithm_version,
                  audit_cache_digest, namespace, talk_id, inventory_complete,
                  candidate_count, metadata_json
           FROM candidate_scope_proof WHERE id=?""",
        (proof_id,),
    ).fetchone()
    if existing is None:
        db.conn.execute(
            """INSERT INTO candidate_scope_proof(
                   id, ingest_run_id, manifest_id, manifest_checksum,
                   snapshot_id, snapshot_commit, snapshot_tree,
                   audit_algorithm_version, audit_cache_digest,
                   namespace, talk_id, inventory_complete, candidate_count,
                   metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (proof_id, *expected),
        )
    elif tuple(existing) != expected:
        raise sqlite3.IntegrityError(f"candidate_scope_proof identity mismatch for {proof_id}")
    return proof_id


def _identity_group(
    db: Database,
    *,
    ingest_run_id: str,
    namespace: str,
    talk_id: str,
    container_ids: Iterable[str],
) -> str:
    group_id = digest_id("ig", ingest_run_id, namespace, talk_id)
    row = db.conn.execute(
        "SELECT ingest_run_id, namespace, talk_id FROM identity_group WHERE id=?",
        (group_id,),
    ).fetchone()
    if row is None:
        db.conn.execute(
            """INSERT INTO identity_group(
                   id, ingest_run_id, namespace, talk_id, metadata_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                group_id,
                ingest_run_id,
                namespace,
                talk_id,
                canonical_json({"meaning": "observed native-key alias"}),
            ),
        )
    elif tuple(row) != (ingest_run_id, namespace, talk_id):
        raise sqlite3.IntegrityError(f"identity_group identity mismatch for {group_id}")
    for container_id in container_ids:
        db.conn.execute(
            """INSERT INTO identity_group_container(
                   identity_group_id, ingest_run_id, evidence_container_id)
               VALUES (?, ?, ?)
               ON CONFLICT(identity_group_id, ingest_run_id, evidence_container_id)
               DO NOTHING""",
            (group_id, ingest_run_id, container_id),
        )
    return group_id


def _link_from_row(db: Database, row: sqlite3.Row) -> QuestEvidenceResolutionLink:
    if row["identity_group_id"] is not None:
        candidate_ids = tuple(
            str(item[0])
            for item in db.conn.execute(
                """SELECT evidence_container_id
                   FROM identity_group_container
                   WHERE identity_group_id=? AND ingest_run_id=?
                   ORDER BY evidence_container_id""",
                (row["identity_group_id"], row["ingest_run_id"]),
            ).fetchall()
        )
    elif row["evidence_container_id"] is not None:
        candidate_ids = (str(row["evidence_container_id"]),)
    else:
        candidate_ids = ()
    return QuestEvidenceResolutionLink(
        document_evidence_id=str(row["id"]),
        document_id=str(row["document_id"]),
        ingest_run_id=str(row["ingest_run_id"]),
        namespace=str(row["namespace"]),
        talk_id=str(row["talk_id"]),
        result=str(row["result"]),
        evidence_container_id=(
            str(row["evidence_container_id"])
            if row["evidence_container_id"] is not None
            else None
        ),
        identity_group_id=(
            str(row["identity_group_id"]) if row["identity_group_id"] is not None else None
        ),
        candidate_container_ids=candidate_ids,
        source_record_id=str(row["source_record_id"]),
        candidate_scope_proof_id=str(row["candidate_scope_proof_id"]),
        candidate_manifest_id=str(row["candidate_manifest_id"]),
        candidate_manifest_checksum=str(row["candidate_manifest_checksum"]),
    )


def _outcome(
    db: Database,
    *,
    ingest_run_id: str,
    namespace: str,
    talk_id: str,
    candidate_scope: CandidateScopeManifest,
) -> tuple[str, tuple[str, ...], list[str]]:
    run = db.conn.execute(
        """SELECT ir.snapshot_id, ss."commit", ss.tree
           FROM ingest_run ir JOIN source_snapshot ss ON ss.id=ir.snapshot_id
           WHERE ir.id=?""",
        (ingest_run_id,),
    ).fetchone()
    if run is None:
        raise ValueError(f"ingest_run does not exist: {ingest_run_id}")
    errors = list(candidate_scope.structural_errors)
    if candidate_scope.audit_cache_state == "mismatch":
        errors.append("audit cache proof mismatch")
    if candidate_scope.snapshot_id != run["snapshot_id"]:
        errors.append("manifest snapshot_id does not match resolution run")
    if candidate_scope.snapshot_commit != run["commit"]:
        errors.append("manifest commit does not match resolution run snapshot")
    if candidate_scope.snapshot_tree != run["tree"]:
        errors.append("manifest tree does not match resolution run snapshot")
    if candidate_scope.namespace != namespace or candidate_scope.talk_id != talk_id:
        errors.append("manifest lookup does not match Quest reference native key")
    candidates = _candidate_rows(
        db,
        ingest_run_id=ingest_run_id,
        namespace=namespace,
        talk_id=talk_id,
    )
    actual_identities = sorted(
        (
            str(row["relative_path"]),
            str(row["raw_checksum"]),
            str(row["normalized_checksum"]),
            int(row["size_bytes"]),
        )
        for row in candidates
    )
    expected_identities = sorted(candidate.identity() for candidate in candidate_scope.candidates)
    if actual_identities != expected_identities:
        errors.append("candidate path, raw bytes, or normalized checksum mismatch")
    if errors:
        return "quarantined", (), errors
    if not candidate_scope.inventory_complete or candidate_scope.audit_cache_state != "verified":
        reason = (
            "audit cache unavailable"
            if candidate_scope.audit_cache_state == "unavailable"
            else "candidate inventory is partial"
        )
        return "incomplete_scope", (), [reason]
    candidate_ids = tuple(str(row["id"]) for row in candidates)
    if not candidate_ids:
        return "unresolved", (), []
    if len(candidate_ids) == 1:
        return "evidence_only", candidate_ids, []
    return "ambiguous", candidate_ids, []


def link_quest_reference_to_evidence(
    db: Database,
    *,
    document_id: str,
    ingest_run_id: str,
    namespace: str,
    talk_id: int | str,
    source_record_id: str,
    candidate_scope: CandidateScopeManifest,
    reference_metadata: dict[str, Any] | None = None,
) -> QuestEvidenceResolutionLink:
    """Resolve one raw Quest reference against a sealed candidate scope.

    Checksums only verify that concrete candidates match the audit occurrence;
    they never form relationship identity or select among same-native candidates.
    """

    if not source_record_id:
        raise ValueError("source_record_id is required for a Quest evidence link")
    native_namespace = str(namespace)
    native_talk_id = str(talk_id)
    _reference_row(
        db,
        source_record_id=source_record_id,
        ingest_run_id=ingest_run_id,
        talk_id=native_talk_id,
    )
    proof_id = _ensure_scope_proof(
        db,
        ingest_run_id=ingest_run_id,
        namespace=native_namespace,
        talk_id=native_talk_id,
        candidate_scope=candidate_scope,
    )
    result, candidate_ids, errors = _outcome(
        db,
        ingest_run_id=ingest_run_id,
        namespace=native_namespace,
        talk_id=native_talk_id,
        candidate_scope=candidate_scope,
    )
    evidence_container_id: str | None = None
    identity_group_id: str | None = None
    if result == "evidence_only":
        evidence_container_id = candidate_ids[0]
    elif result == "ambiguous":
        identity_group_id = _identity_group(
            db,
            ingest_run_id=ingest_run_id,
            namespace=native_namespace,
            talk_id=native_talk_id,
            container_ids=candidate_ids,
        )

    row_id = digest_id("de", source_record_id, ingest_run_id)
    metadata = {
        "method": "exact_native_key",
        "candidate_count": len(candidate_ids),
        "manifest_candidate_count": candidate_scope.candidate_count,
        "validation_errors": errors,
    }
    if reference_metadata:
        metadata["reference"] = reference_metadata
    metadata_json = canonical_json(metadata)
    expected = (
        document_id,
        ingest_run_id,
        source_record_id,
        native_namespace,
        native_talk_id,
        result,
        proof_id,
        candidate_scope.manifest_id,
        candidate_scope.manifest_checksum,
        evidence_container_id,
        identity_group_id,
        metadata_json,
    )
    existing = db.conn.execute("SELECT * FROM document_evidence WHERE id=?", (row_id,)).fetchone()
    if existing is not None:
        actual = (
            existing["document_id"],
            existing["ingest_run_id"],
            existing["source_record_id"],
            existing["namespace"],
            existing["talk_id"],
            existing["result"],
            existing["candidate_scope_proof_id"],
            existing["candidate_manifest_id"],
            existing["candidate_manifest_checksum"],
            existing["evidence_container_id"],
            existing["identity_group_id"],
            existing["metadata_json"],
        )
        if actual != expected:
            raise sqlite3.IntegrityError(
                f"raw Quest reference already has a different resolution outcome: {source_record_id}"
            )
        return _link_from_row(db, existing)

    db.conn.execute(
        """INSERT INTO document_evidence(
               id, document_id, ingest_run_id, source_record_id,
               namespace, talk_id, result, candidate_scope_proof_id,
               candidate_manifest_id, candidate_manifest_checksum,
               evidence_container_id, identity_group_id, metadata_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (row_id, *expected),
    )
    row = db.conn.execute("SELECT * FROM document_evidence WHERE id=?", (row_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"document_evidence insert disappeared: {row_id}")
    return _link_from_row(db, row)
