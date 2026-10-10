"""Deterministic discovery of hash-named raw Talk files.

AnimeGameData stores Talk raw files under opaque/hash-named paths beneath
``BinOutput/Talk`` (including ``Quest``, ``Activity`` and other partitions).
eight-character filename.  The filename is not a logical identity.  This
module therefore builds an index by reading the native Talk ID from every
candidate file and keeps the evidence needed to audit the decision.

The index is deliberately independent from the SQLite evidence tables.  It is
an ingestion aid/cache, not a second source of truth: every resolved candidate
still has to be read and registered by the normal pipeline.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .errors import ManifestError, SchemaCanaryError
from .mapping import MAPPING_VERSION, TALK_MAP, deobfuscate
from .source import SourceBundle
from .util import canonical_json, sha256_bytes


DISCOVERY_VERSION = "phase3-evidence-identity-0.4"
TALK_RELATIVE_ROOT = "BinOutput/Talk"


@dataclass(frozen=True)
class TalkCandidate:
    """One parseable raw Talk candidate found in a snapshot."""

    talk_id: int
    relative_path: str
    root_path: str
    raw_sha256: str
    normalized_checksum: str
    size_bytes: int
    node_count: int
    edge_count: int
    node_ids: tuple[int, ...]
    native_id_key: str
    native_id_verified: bool
    discovery_method: str
    parse_status: str = "valid"
    error: str | None = None
    native_namespace: str = "__missing_or_empty__"
    native_namespace_source: str = "missing"


@dataclass(frozen=True)
class TalkDiscoveryResult:
    talk_id: int
    status: str
    candidates: tuple[TalkCandidate, ...]
    discovery_method: str
    native_id_verified: bool
    reason: str | None = None

    @property
    def candidate(self) -> TalkCandidate | None:
        return self.candidates[0] if self.status == "resolved" and len(self.candidates) == 1 else None


@dataclass(frozen=True)
class TalkDiscoveryIndex:
    snapshot_id: str
    release_label: str
    commit: str
    tree: str
    discovery_version: str
    mapping_version: str
    inventory_complete: bool
    inventory_root: str | None
    candidate_file_count: int
    valid_file_count: int
    invalid_file_count: int
    candidates: tuple[TalkCandidate, ...]
    source_roots: tuple[str, ...] = ()
    cache_path: str | None = None

    def by_talk_id(self) -> dict[int, tuple[TalkCandidate, ...]]:
        grouped: dict[int, list[TalkCandidate]] = {}
        for candidate in self.candidates:
            grouped.setdefault(candidate.talk_id, []).append(candidate)
        return {key: tuple(value) for key, value in grouped.items()}

    def resolve(self, talk_id: int, expected_init_dialog: int | None = None) -> TalkDiscoveryResult:
        return self._resolve_from_candidates(
            int(talk_id), self.by_talk_id().get(int(talk_id), ()), expected_init_dialog
        )

    def resolve_many(self, talk_ids: Iterable[int], expected_init_dialogs: dict[int, int] | None = None) -> dict[int, TalkDiscoveryResult]:
        """Resolve a batch without rebuilding the candidate index per ID.

        ``resolve`` remains intentionally stateless and convenient for small
        lookups, but a full Quest corpus can contain tens of thousands of
        references.  Recomputing ``by_talk_id`` for every reference would scan
        the complete raw Talk inventory once per reference.  Build the grouped
        map once here and apply the same deterministic rules to each requested
        ID.
        """
        expected = expected_init_dialogs or {}
        grouped = self.by_talk_id()
        return {
            int(talk_id): self._resolve_from_candidates(
                int(talk_id), grouped.get(int(talk_id), ()), expected.get(int(talk_id))
            )
            for talk_id in sorted({int(value) for value in talk_ids})
        }

    def _resolve_from_candidates(
        self,
        talk_id: int,
        candidates: tuple[TalkCandidate, ...],
        expected_init_dialog: int | None = None,
    ) -> TalkDiscoveryResult:
        """Apply the Phase 2 resolution rule to an already-grouped ID."""
        if expected_init_dialog is not None and candidates:
            matching = tuple(candidate for candidate in candidates if expected_init_dialog in candidate.node_ids)
            if len(matching) == 1:
                candidate = matching[0]
                return TalkDiscoveryResult(
                    int(talk_id),
                    "resolved",
                    matching,
                    "native_id_exact_scan+init_dialog_node_match",
                    candidate.native_id_verified,
                )
            if len(matching) > 1:
                return TalkDiscoveryResult(
                    int(talk_id),
                    "ambiguous",
                    matching,
                    "native_id_exact_scan+init_dialog_node_match",
                    all(candidate.native_id_verified for candidate in matching),
                    reason="multiple raw files contain the native Talk ID and expected initDialog node",
                )
            return TalkDiscoveryResult(
                int(talk_id),
                "ambiguous",
                candidates,
                "native_id_exact_scan",
                all(candidate.native_id_verified for candidate in candidates),
                reason="multiple raw files contain the native Talk ID but none contains the reference initDialog node",
            )
        if len(candidates) == 1:
            candidate = candidates[0]
            return TalkDiscoveryResult(
                int(talk_id),
                "resolved",
                candidates,
                candidate.discovery_method,
                candidate.native_id_verified,
            )
        if len(candidates) > 1:
            return TalkDiscoveryResult(
                int(talk_id),
                "ambiguous",
                candidates,
                "native_id_exact_scan",
                all(candidate.native_id_verified for candidate in candidates),
                reason="multiple parseable raw files contain the same native Talk ID",
            )
        if self.inventory_complete:
            return TalkDiscoveryResult(
                int(talk_id),
                "genuinely_absent",
                (),
                "native_id_exact_scan_complete_inventory",
                False,
                reason="complete BinOutput/Talk candidate inventory has no parseable matching native ID",
            )
        return TalkDiscoveryResult(
            int(talk_id),
            "unresolved",
            (),
            "native_id_exact_scan_partial_inventory",
            False,
            reason="candidate inventory is partial; absence cannot be proven",
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "release_label": self.release_label,
            "commit": self.commit,
            "tree": self.tree,
            "discovery_version": self.discovery_version,
            "mapping_version": self.mapping_version,
            "inventory_complete": self.inventory_complete,
            "inventory_root": self.inventory_root,
            "candidate_file_count": self.candidate_file_count,
            "valid_file_count": self.valid_file_count,
            "invalid_file_count": self.invalid_file_count,
            "source_roots": list(self.source_roots),
            "candidates": [asdict(candidate) for candidate in self.candidates],
        }


def _candidate_paths(bundle: SourceBundle) -> list[tuple[str, Path]]:
    """Return first-root-wins ``BinOutput/Talk`` JSON paths in stable order."""

    found: dict[str, Path] = {}
    for root in bundle.roots:
        directory = root.joinpath(*TALK_RELATIVE_ROOT.split("/"))
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.json"):
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                found.setdefault(relative, path)
    return sorted(found.items())


def _parse_candidate(relative_path: str, path: Path) -> TalkCandidate | None:
    """Parse one file; return ``None`` for a non-Talk/invalid root.

    Invalid files are counted by the caller through the returned error tuple
    from ``_scan_candidates``.  The native ID is accepted only when it is the
    mapped ``IOKNFDJFGDH`` field and is an integer; no filename or textual
    similarity is consulted.
    """

    raw_bytes = path.read_bytes()
    raw_sha = sha256_bytes(raw_bytes)
    try:
        raw = json.loads(raw_bytes.decode("utf-8-sig"))
        if not isinstance(raw, dict):
            return None
        normalized = deobfuscate(raw, TALK_MAP)
        if not isinstance(normalized, dict):
            return None
        talk_id = normalized.get("talkId")
        dialog_list = normalized.get("dialogList")
        if not isinstance(talk_id, int) or isinstance(talk_id, bool):
            return None
        if not isinstance(dialog_list, list):
            return None
        node_ids: set[int] = set()
        edge_count = 0
        for node in dialog_list:
            if not isinstance(node, dict) or not isinstance(node.get("id"), int):
                return None
            node_ids.add(int(node["id"]))
            next_dialogs = node.get("nextDialogs", [])
            if not isinstance(next_dialogs, list):
                return None
            edge_count += sum(1 for target in next_dialogs if isinstance(target, int))
        # Duplicate native node IDs are a malformed structure, not a second
        # candidate.  Keep it out of resolved data so the caller can audit it.
        if len(node_ids) != len(dialog_list):
            return None
        # ``BinOutput/Talk`` also contains a handful of files directly at its
        # root, so fixed ``parents[n]`` indexing would mis-record their
        # provenance root.  Locate the path anchor instead.
        path_parts = path.parts
        source_root = path.parent
        for index in range(len(path_parts) - 1):
            if path_parts[index:index + 2] == ("BinOutput", "Talk"):
                source_root = Path(*path_parts[:index]) if index else Path(path.anchor)
                break
        return TalkCandidate(
            talk_id=int(talk_id),
            relative_path=relative_path,
            root_path=str(source_root),
            raw_sha256=raw_sha,
            normalized_checksum=hashlib.sha256(canonical_json(normalized).encode("utf-8")).hexdigest(),
            size_bytes=len(raw_bytes),
            node_count=len(dialog_list),
            edge_count=edge_count,
            node_ids=tuple(sorted(node_ids)),
            native_id_key="IOKNFDJFGDH",
            native_id_verified=True,
            discovery_method="native_id_exact_scan",
            native_namespace=_talk_native_namespace(raw)[0],
            native_namespace_source=_talk_native_namespace(raw)[1],
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, SchemaCanaryError):
        return None


def _scan_candidates(bundle: SourceBundle) -> tuple[list[TalkCandidate], int, int, list[str]]:
    candidates: list[TalkCandidate] = []
    invalid = 0
    errors: list[str] = []
    paths = _candidate_paths(bundle)
    for relative, path in paths:
        candidate = _parse_candidate(relative, path)
        if candidate is None:
            invalid += 1
            errors.append(relative)
        else:
            candidates.append(candidate)
    return candidates, len(paths), invalid, errors


def _git_head_matches(root: Path, commit: str) -> bool:
    """Check that a source root is a Git checkout at the manifest commit."""

    if not (root / ".git").exists():
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.stdout.strip().lower() == commit.lower()


def _git_talk_inventory_complete(root: Path, commit: str) -> bool:
    """Verify that a sparse checkout contains every tracked Talk JSON path.

    Matching ``HEAD`` alone is not enough: a sparse checkout can point at the
    right commit while omitting arbitrary directories.  Absence is promoted to
    ``genuinely_absent`` only after the manifest's tracked Talk paths are all
    present locally.
    """

    if not _git_head_matches(root, commit):
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-tree", "-r", "--name-only", commit, "--", TALK_RELATIVE_ROOT],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    expected = {
        line.strip().replace("\\", "/")
        for line in result.stdout.splitlines()
        if line.strip().lower().endswith(".json")
    }
    if not expected:
        return False
    directory = root.joinpath(*TALK_RELATIVE_ROOT.split("/"))
    if not directory.is_dir():
        return False
    actual = {path.relative_to(root).as_posix() for path in directory.rglob("*.json") if path.is_file()}
    return expected.issubset(actual)


def _cache_key(snapshot_id: str, commit: str, tree: str, source_roots: Iterable[Path]) -> str:
    root_token = canonical_json([str(root) for root in source_roots])
    token = hashlib.sha256(f"{snapshot_id}|{commit}|{tree}|{DISCOVERY_VERSION}|{MAPPING_VERSION}|{root_token}".encode()).hexdigest()[:20]
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in snapshot_id)
    return f"talk-index-{safe}-{token}.json"


def _from_json(value: dict[str, Any], cache_path: Path | None = None) -> TalkDiscoveryIndex:
    candidates = tuple(
        TalkCandidate(**{**item, "node_ids": tuple(int(x) for x in item.get("node_ids", []))})
        for item in value.get("candidates", [])
    )
    return TalkDiscoveryIndex(
        snapshot_id=str(value["snapshot_id"]),
        release_label=str(value["release_label"]),
        commit=str(value["commit"]),
        tree=str(value["tree"]),
        discovery_version=str(value["discovery_version"]),
        mapping_version=str(value["mapping_version"]),
        inventory_complete=bool(value["inventory_complete"]),
        inventory_root=value.get("inventory_root"),
        candidate_file_count=int(value["candidate_file_count"]),
        valid_file_count=int(value["valid_file_count"]),
        invalid_file_count=int(value["invalid_file_count"]),
        candidates=candidates,
        source_roots=tuple(str(item) for item in value.get("source_roots", [])),
        cache_path=str(cache_path) if cache_path else None,
    )


def build_talk_index(
    bundle: SourceBundle,
    manifest: dict[str, Any],
    snapshot_id: str,
    *,
    cache_dir: str | Path | None = None,
    use_cache: bool = True,
) -> TalkDiscoveryIndex:
    """Scan the fixed snapshot and return an auditable Talk index.

    Cache identity includes the source snapshot commit/tree and parser/mapping
    versions.  A cache from another snapshot therefore cannot be reused.  A
    cache is only accepted when its candidate directory is still present; the
    raw SHA-256 values remain the provenance check when a candidate is read by
    ingestion.
    """

    cache_path: Path | None = None
    if cache_dir is not None:
        cache_path = Path(cache_dir) / _cache_key(snapshot_id, manifest["commit"], manifest["tree"], bundle.roots)
        if use_cache and cache_path.is_file():
            try:
                value = json.loads(cache_path.read_text(encoding="utf-8"))
                if (
                    value.get("snapshot_id") == snapshot_id
                    and value.get("commit") == manifest["commit"]
                    and value.get("tree") == manifest["tree"]
                    and value.get("mapping_version") == MAPPING_VERSION
                    and value.get("discovery_version") == DISCOVERY_VERSION
                    and value.get("source_roots") == [str(root) for root in bundle.roots]
                ):
                    return _from_json(value, cache_path)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
                pass

    candidates, file_count, invalid_count, _invalid_paths = _scan_candidates(bundle)
    candidates.sort(key=lambda item: (item.talk_id, item.relative_path))
    inventory_root: str | None = None
    inventory_complete = False
    for root in bundle.roots:
        directory = root.joinpath(*TALK_RELATIVE_ROOT.split("/"))
        if directory.is_dir() and _git_talk_inventory_complete(root, manifest["commit"]):
            inventory_root = str(root)
            inventory_complete = True
            break
    index = TalkDiscoveryIndex(
        snapshot_id=snapshot_id,
        release_label=str(manifest["release_label"]),
        commit=str(manifest["commit"]),
        tree=str(manifest["tree"]),
        discovery_version=DISCOVERY_VERSION,
        mapping_version=MAPPING_VERSION,
        inventory_complete=inventory_complete,
        inventory_root=inventory_root,
        candidate_file_count=file_count,
        valid_file_count=len(candidates),
        invalid_file_count=invalid_count,
        candidates=tuple(candidates),
        source_roots=tuple(str(root) for root in bundle.roots),
        cache_path=str(cache_path) if cache_path else None,
    )
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(index.to_json(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return index


def load_talk_discovery_index_cache(
    path: str | Path,
    *,
    expected_sha256: str | None = None,
    expected_snapshot: Any = None,
) -> TalkDiscoveryIndex:
    """Load and strictly validate an existing talk discovery index cache file.

    This function is strictly read-only: it does NOT scan Talk directories, does
    NOT write or update caches, and does NOT treat machine-specific absolute
    root_paths as identity.
    """
    cache_path = Path(path).resolve()
    if not cache_path.is_file():
        raise ManifestError(f"discovery cache file not found at {cache_path}")

    try:
        raw_bytes = cache_path.read_bytes()
    except OSError as exc:
        raise ManifestError(f"cannot read discovery cache file at {cache_path}: {exc}") from exc

    actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if expected_sha256 is not None:
        if actual_sha256.lower() != expected_sha256.lower():
            raise ManifestError(
                f"discovery cache checksum mismatch for {cache_path}: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )

    try:
        value = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"invalid JSON in discovery cache at {cache_path}: {exc}") from exc

    if not isinstance(value, dict):
        raise ManifestError(f"discovery cache root must be a JSON object, got {type(value).__name__}")

    required_keys = {
        "snapshot_id",
        "release_label",
        "commit",
        "tree",
        "discovery_version",
        "mapping_version",
        "inventory_complete",
        "candidate_file_count",
        "valid_file_count",
        "invalid_file_count",
        "candidates",
    }
    missing = required_keys - set(value.keys())
    if missing:
        raise ManifestError(f"discovery cache at {cache_path} missing required keys: {sorted(missing)}")

    if value.get("discovery_version") != DISCOVERY_VERSION:
        raise ManifestError(
            f"discovery_version mismatch in {cache_path}: expected {DISCOVERY_VERSION}, got {value.get('discovery_version')!r}"
        )
    if value.get("mapping_version") != MAPPING_VERSION:
        raise ManifestError(
            f"mapping_version mismatch in {cache_path}: expected {MAPPING_VERSION}, got {value.get('mapping_version')!r}"
        )
    if value.get("inventory_complete") is not True:
        raise ManifestError(
            f"inventory_complete must be true in sealed discovery cache at {cache_path}, got {value.get('inventory_complete')!r}"
        )

    if expected_snapshot is not None:
        exp_commit = getattr(expected_snapshot, "commit", None) or (
            expected_snapshot.get("commit") if isinstance(expected_snapshot, dict) else None
        )
        exp_tree = getattr(expected_snapshot, "tree", None) or (
            expected_snapshot.get("tree") if isinstance(expected_snapshot, dict) else None
        )
        exp_release = getattr(expected_snapshot, "release_label", None) or (
            expected_snapshot.get("release_label") if isinstance(expected_snapshot, dict) else None
        )

        if exp_commit and value.get("commit") != exp_commit:
            raise ManifestError(
                f"snapshot commit mismatch in {cache_path}: expected {exp_commit}, got {value.get('commit')}"
            )
        if exp_tree and value.get("tree") != exp_tree:
            raise ManifestError(
                f"snapshot tree mismatch in {cache_path}: expected {exp_tree}, got {value.get('tree')}"
            )
        if exp_release and value.get("release_label") != exp_release:
            raise ManifestError(
                f"snapshot release_label mismatch in {cache_path}: expected {exp_release}, got {value.get('release_label')}"
            )

    return _from_json(value, cache_path)


def discovery_report(index: TalkDiscoveryIndex, talk_ids: Iterable[int], expected_init_dialogs: dict[int, int] | None = None) -> dict[str, Any]:
    """Serialize requested Talk statuses without turning unresolved data active."""

    results = index.resolve_many(talk_ids, expected_init_dialogs)
    return {
        "snapshot_id": index.snapshot_id,
        "commit": index.commit,
        "tree": index.tree,
        "discovery_version": index.discovery_version,
        "mapping_version": index.mapping_version,
        "inventory_complete": index.inventory_complete,
        "inventory_root": index.inventory_root,
        "source_roots": list(index.source_roots),
        "candidate_file_count": index.candidate_file_count,
        "valid_file_count": index.valid_file_count,
        "invalid_file_count": index.invalid_file_count,
        "status_counts": {
            status: sum(1 for result in results.values() if result.status == status)
            for status in ("resolved", "genuinely_absent", "ambiguous", "unresolved")
        },
        "talks": [
            {
                "talk_id": talk_id,
                "status": result.status,
                "discovery_method": result.discovery_method,
                "native_id_verified": result.native_id_verified,
                "expected_init_dialog": (expected_init_dialogs or {}).get(talk_id),
                "reason": result.reason,
                "candidates": [asdict(candidate) for candidate in result.candidates],
            }
            for talk_id, result in sorted(results.items())
        ],
        "cache_path": index.cache_path,
    }


def dialog_identity_audit(index: TalkDiscoveryIndex, *, example_limit: int = 20) -> dict[str, Any]:
    """Audit whether ``(talk_id, node_id)`` is unique in the raw inventory.

    The current evidence schema can scope a dialog identity by Talk, but a
    scoped key is only safe when the same pair does not point at multiple raw
    records.  This audit deliberately considers every parseable Talk
    candidate, including non-Quest partitions and duplicate native Talk IDs.
    A conflict is not resolved by choosing a path: the paths and normalized
    checksums remain visible so promotion can stop for an explicit decision.
    """
    pair_paths: dict[tuple[int, int], list[str]] = {}
    pair_checksums: dict[tuple[int, int], set[str]] = {}
    for candidate in index.candidates:
        for node_id in candidate.node_ids:
            pair = (int(candidate.talk_id), int(node_id))
            pair_paths.setdefault(pair, []).append(candidate.relative_path)
            pair_checksums.setdefault(pair, set()).add(candidate.normalized_checksum)
    conflicts = [pair for pair, paths in pair_paths.items() if len(paths) > 1]
    different_content = [pair for pair in conflicts if len(pair_checksums[pair]) > 1]
    talk_file_counts: dict[int, int] = {}
    for candidate in index.candidates:
        talk_file_counts[candidate.talk_id] = talk_file_counts.get(candidate.talk_id, 0) + 1
    examples = []
    for talk_id, node_id in sorted(conflicts)[: max(0, int(example_limit))]:
        pair = (talk_id, node_id)
        examples.append(
            {
                "talk_id": talk_id,
                "node_id": node_id,
                "paths": sorted(pair_paths[pair]),
                "normalized_checksums": sorted(pair_checksums[pair]),
            }
        )
    return {
        "identity": "talk_scoped_dialog",
        "key": ["talk_id", "node_id"],
        "status": "unique" if not conflicts else "blocked",
        "snapshot_id": index.snapshot_id,
        "commit": index.commit,
        "tree": index.tree,
        "inventory_complete": index.inventory_complete,
        "candidate_file_count": index.candidate_file_count,
        "valid_file_count": index.valid_file_count,
        "invalid_file_count": index.invalid_file_count,
        "talk_id_count": len(talk_file_counts),
        "talk_ids_with_multiple_raw_files": sum(1 for count in talk_file_counts.values() if count > 1),
        "pair_count": len(pair_paths),
        "pair_conflict_count": len(conflicts),
        "pair_conflict_record_memberships": sum(len(pair_paths[pair]) for pair in conflicts),
        "pair_conflicts_with_different_normalized_content": len(different_content),
        "examples": examples,
    }


def _talk_path_namespace(relative_path: str) -> str:
    parts = Path(relative_path).parts
    try:
        talk_index = parts.index("Talk")
    except ValueError:
        return "unknown"
    return "/".join(parts[talk_index + 1 : -1]) or "root"


def _talk_native_namespace(raw: dict[str, Any]) -> tuple[str, str]:
    """Read the native Talk kind without treating a path as semantic ID.

    The fixed 7.0 snapshot contains both obfuscated roots with
    ``ALBFHGKNMLK`` and already-normalized records exposing the same value as
    ``type``.  The field source is returned alongside the value so the audit
    cannot silently turn a fallback into an original fact.
    """
    normalized = deobfuscate(raw, TALK_MAP)
    native_kind = normalized.get("ALBFHGKNMLK")
    if isinstance(native_kind, str) and native_kind:
        return native_kind, "ALBFHGKNMLK"
    normalized_kind = normalized.get("type")
    if isinstance(normalized_kind, str) and normalized_kind:
        return normalized_kind, "type"
    return "__missing_or_empty__", "missing"


def talk_scope_audit(index: TalkDiscoveryIndex) -> dict[str, Any]:
    """Audit native Talk parent and dialog scopes for a complete snapshot.

    ``native_kind`` is the semantic kind exposed by the raw Talk object; the
    directory partition is reported separately as provenance context.  The
    audit intentionally keeps every raw candidate occurrence.  It does not
    collapse same-content files and does not promote a path/checksum to a
    public stable identity.
    """
    namespace_records: dict[str, int] = {}
    namespace_talks: dict[str, set[int]] = {}
    namespace_sources: dict[str, dict[str, int]] = {}
    parent_records: dict[tuple[str, int], list[dict[str, Any]]] = {}
    triple_records: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    path_parent_records: dict[tuple[str, int], list[dict[str, Any]]] = {}
    path_triple_records: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    read_errors: list[dict[str, str]] = []
    for candidate in index.candidates:
        path = Path(candidate.root_path) / candidate.relative_path
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            namespace, namespace_source = _talk_native_namespace(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            read_errors.append({"relative_path": candidate.relative_path, "error": str(exc)})
            namespace, namespace_source = "__read_error__", "error"
        path_namespace = _talk_path_namespace(candidate.relative_path)
        namespace_records[namespace] = namespace_records.get(namespace, 0) + 1
        namespace_talks.setdefault(namespace, set()).add(candidate.talk_id)
        source_counts = namespace_sources.setdefault(namespace, {})
        source_counts[namespace_source] = source_counts.get(namespace_source, 0) + 1
        record = {
            "relative_path": candidate.relative_path,
            "path_namespace": path_namespace,
            "namespace": namespace,
            "namespace_source": namespace_source,
            "talk_id": candidate.talk_id,
            "raw_sha256": candidate.raw_sha256,
            "normalized_checksum": candidate.normalized_checksum,
            "node_count": candidate.node_count,
            "node_ids": list(candidate.node_ids),
        }
        parent_records.setdefault((namespace, candidate.talk_id), []).append(record)
        path_parent_records.setdefault((path_namespace, candidate.talk_id), []).append(record)
        for node_id in candidate.node_ids:
            triple_records.setdefault((namespace, candidate.talk_id, int(node_id)), []).append(record)
            path_triple_records.setdefault((path_namespace, candidate.talk_id, int(node_id)), []).append(record)

    parent_collisions = {
        key: records for key, records in parent_records.items() if len(records) > 1
    }
    triple_collisions = {
        key: records for key, records in triple_records.items() if len(records) > 1
    }
    path_parent_collisions = {
        key: records for key, records in path_parent_records.items() if len(records) > 1
    }
    path_triple_collisions = {
        key: records for key, records in path_triple_records.items() if len(records) > 1
    }
    parent_different_content = {
        key
        for key, records in parent_collisions.items()
        if len({record["normalized_checksum"] for record in records}) > 1
    }
    triple_different_content = {
        key
        for key, records in triple_collisions.items()
        if len({record["normalized_checksum"] for record in records}) > 1
    }
    path_parent_different_content = {
        key
        for key, records in path_parent_collisions.items()
        if len({record["normalized_checksum"] for record in records}) > 1
    }
    path_triple_different_content = {
        key
        for key, records in path_triple_collisions.items()
        if len({record["normalized_checksum"] for record in records}) > 1
    }

    def serialize_collisions(collisions: dict[tuple[Any, ...], list[dict[str, Any]]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for key, records in sorted(collisions.items()):
            row = {
                "namespace": key[0],
                "talk_id": key[1],
                "records": sorted(records, key=lambda record: record["relative_path"]),
            }
            if len(key) == 3:
                row["node_id"] = key[2]
            rows.append(row)
        return rows

    return {
        "snapshot_id": index.snapshot_id,
        "commit": index.commit,
        "tree": index.tree,
        "inventory_complete": index.inventory_complete,
        "candidate_file_count": index.candidate_file_count,
        "valid_file_count": index.valid_file_count,
        "invalid_file_count": index.invalid_file_count,
        "namespace_definition": {
            "semantic_field": "ALBFHGKNMLK",
            "normalized_field_fallback": "type",
            "missing_value": "__missing_or_empty__",
            "path_namespace_is_provenance_only": True,
        },
        "namespace_record_counts": dict(sorted(namespace_records.items())),
        "namespace_talk_counts": {
            namespace: len(talk_ids)
            for namespace, talk_ids in sorted(namespace_talks.items())
        },
        "namespace_source_counts": {
            namespace: dict(sorted(counts.items()))
            for namespace, counts in sorted(namespace_sources.items())
        },
        "path_namespace_to_kind_counts": {
            path_namespace: dict(sorted(counts.items()))
            for path_namespace, counts in sorted(
                (
                    path_namespace,
                    {
                        namespace: sum(
                            1
                            for records in parent_records.values()
                            for record in records
                            if record["path_namespace"] == path_namespace and record["namespace"] == namespace
                        )
                        for namespace in namespace_records
                        if any(
                            record["path_namespace"] == path_namespace and record["namespace"] == namespace
                            for records in parent_records.values()
                            for record in records
                        )
                    },
                )
                for path_namespace in sorted(
                    {
                        record["path_namespace"]
                        for records in parent_records.values()
                        for record in records
                    }
                )
            )
        },
        "parent_identity": {
            "key": ["namespace", "talk_id"],
            "pair_count": len(parent_records),
            "collision_pair_count": len(parent_collisions),
            "collision_memberships": sum(len(records) for records in parent_collisions.values()),
            "collision_different_normalized_content_count": len(parent_different_content),
            "status": "unique" if not parent_collisions else "non_unique",
            "collision_by_namespace": dict(
                sorted(
                    {
                        namespace: sum(1 for key in parent_collisions if key[0] == namespace)
                        for namespace in {key[0] for key in parent_collisions}
                    }.items()
                )
            ),
            "collisions": serialize_collisions(parent_collisions),
        },
        "dialog_identity": {
            "key": ["namespace", "talk_id", "node_id"],
            "pair_count": len(triple_records),
            "collision_pair_count": len(triple_collisions),
            "collision_memberships": sum(len(records) for records in triple_collisions.values()),
            "collision_different_normalized_content_count": len(triple_different_content),
            "status": "unique" if not triple_collisions else "non_unique",
            "collisions": serialize_collisions(triple_collisions),
        },
        "path_scope_audit": {
            "parent_identity": {
                "key": ["path_namespace", "talk_id"],
                "pair_count": len(path_parent_records),
                "collision_pair_count": len(path_parent_collisions),
                "collision_memberships": sum(len(records) for records in path_parent_collisions.values()),
                "collision_different_normalized_content_count": len(path_parent_different_content),
                "status": "unique" if not path_parent_collisions else "non_unique",
                "collision_by_namespace": dict(
                    sorted(
                        {
                            namespace: sum(1 for key in path_parent_collisions if key[0] == namespace)
                            for namespace in {key[0] for key in path_parent_collisions}
                        }.items()
                    )
                ),
            },
            "dialog_identity": {
                "key": ["path_namespace", "talk_id", "node_id"],
                "pair_count": len(path_triple_records),
                "collision_pair_count": len(path_triple_collisions),
                "collision_memberships": sum(len(records) for records in path_triple_collisions.values()),
                "collision_different_normalized_content_count": len(path_triple_different_content),
                "status": "unique" if not path_triple_collisions else "non_unique",
            },
        },
        "read_errors": read_errors,
    }
