"""Contract and source oracle helpers for the Phase 3.1C3B pilot.

The C3B implementation deliberately treats the C3A manifest as a sealed
input.  This module contains only deterministic, family-specific source
facts; it does not create a logical Talk or a cross-version identity.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import unicodedata
from pathlib import Path
from typing import Any, Mapping, Sequence


COMMIT = "26df1dfbdf05a82bbb1d97506859f3e1c40718d8"
TREE = "d891ef1d95fdd6c27a1da8a32ea7a61b483647a6"
SNAPSHOT_ID = f"anime-game-data:7.0:{COMMIT}"
FAMILY = "talk_comparison_pilot"
CONTRACT_VERSION = "talk-comparison-pilot-v1"
SCOPE_KEY = "talk:QUEST:700814"
NAMESPACE = "QUEST"
TALK_ID = "700814"
TALK_PATH = "BinOutput/Talk/Quest/79661eb8.json"
MANIFEST_PATH = "manifests/comparison/anime-game-data-7.0-talk-700814-phase31c3-pilot.json"
MANIFEST_SHA256 = "d896641d624e99e06313c9dca7c26e787a4fa3a8eafd202691f54d64fc1a47c9"
SCOPE_MANIFEST_PATH = "manifests/candidate-scopes/anime-game-data-7.0-quest-7008-talk-700814.json"
SCOPE_MANIFEST_SHA256 = "6c9d6dee30bd4cc2fbcf0c6c243d6f5d2edbda352162406761f16c3c4ccfe8bb"
MIGRATION_PATH = "migrations/0007_phase31c_comparison_foundation.sql"
MIGRATION_SHA256 = "87837972bd66ef954c74f013766f35d9b6e5ca04b2c2f049144de99d6eb85f1a"
MATCHER_METHOD = "talk_occurrence_coordinate_v1"
PROOF_ALGORITHM = "talk_comparison_scope_proof_v1"
OBSERVED_AT = "2026-09-01T00:00:00Z"
SCHEMA_VERSION = 7
LOCALES = ("CHS", "EN", "JP")
SIDES = ("baseline", "target")
NODE_ORDER = (
    "70081401", "70081402", "70081403", "70081404", "70081405",
    "70081406", "70081407", "70081408", "70081409", "70081411",
    "70081412", "70081413", "70081414", "70081410",
)

TEXTMAP_PATHS = {
    "CHS": {"main": "TextMap/TextMapCHS.json", "medium": "TextMap/TextMap_MediumCHS.json"},
    "EN": {"main": "TextMap/TextMapEN.json", "medium": "TextMap/TextMap_MediumEN.json"},
    "JP": {"main": "TextMap/TextMapJP.json", "medium": "TextMap/TextMap_MediumJP.json"},
}
SOURCE_PATHS = (
    TALK_PATH,
    TEXTMAP_PATHS["CHS"]["main"], TEXTMAP_PATHS["CHS"]["medium"],
    TEXTMAP_PATHS["EN"]["main"], TEXTMAP_PATHS["EN"]["medium"],
    TEXTMAP_PATHS["JP"]["main"], TEXTMAP_PATHS["JP"]["medium"],
)


class TalkComparisonPhase31C3BError(RuntimeError):
    """The C3B source, manifest, or database contract is invalid."""


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sequence_digest(values: Sequence[Any]) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(canonical(value).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")


def occurrence_id(prefix: str, *parts: object) -> str:
    payload = "\x1f".join(str(item) for item in parts)
    return f"{prefix}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:40]}"


def _strict_json(raw: bytes, label: str) -> Any:
    def reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise TalkComparisonPhase31C3BError(f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise TalkComparisonPhase31C3BError(f"non-standard JSON constant in {label}: {value}")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=reject_pairs, parse_constant=reject_constant)
    except TalkComparisonPhase31C3BError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TalkComparisonPhase31C3BError(f"invalid JSON in {label}: {exc}") from exc


def _read_regular(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise TalkComparisonPhase31C3BError(f"required regular file is missing: {label}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise TalkComparisonPhase31C3BError(f"cannot read {label}: {exc}") from exc


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", timeout=120, check=False,
    )
    if result.returncode:
        raise TalkComparisonPhase31C3BError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def load_manifest(repo_root: str | Path, manifest_path: str | Path | None = None) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    path = Path(manifest_path or root / MANIFEST_PATH).resolve()
    raw = _read_regular(path, str(path))
    if sha256_bytes(raw) != MANIFEST_SHA256:
        raise TalkComparisonPhase31C3BError("C3A manifest byte seal mismatch")
    value = _strict_json(raw, str(path))
    if not isinstance(value, dict):
        raise TalkComparisonPhase31C3BError("C3A manifest is not an object")
    if value.get("manifest_id") != "anime-game-data-7.0-talk-700814-phase31c3-pilot":
        raise TalkComparisonPhase31C3BError("C3A manifest ID mismatch")
    if value.get("family") != FAMILY or value.get("family_contract_version") != CONTRACT_VERSION:
        raise TalkComparisonPhase31C3BError("C3A family contract mismatch")
    if value.get("coverage") != "bounded_comparison_pilot" or value.get("corpus_complete") is not False:
        raise TalkComparisonPhase31C3BError("C3A bounded coverage contract mismatch")
    if value.get("evidence_only") is not True or value.get("projection_authorized") is not False:
        raise TalkComparisonPhase31C3BError("C3A evidence-only contract mismatch")
    if value.get("m3_database_rows_generated") is not False:
        raise TalkComparisonPhase31C3BError("C3A row-generation marker changed")
    source = value.get("source_snapshot")
    if not isinstance(source, dict) or source.get("snapshot_id") != SNAPSHOT_ID or source.get("commit") != COMMIT or source.get("tree") != TREE:
        raise TalkComparisonPhase31C3BError("C3A source snapshot pin mismatch")
    matcher = value.get("matcher")
    if not isinstance(matcher, dict) or matcher.get("method") != MATCHER_METHOD or matcher.get("hypothesis_only") is not True:
        raise TalkComparisonPhase31C3BError("C3A matcher contract mismatch")
    expected = value.get("expected_result")
    if not isinstance(expected, dict) or expected.get("match_sets") != 14 or expected.get("pair_candidates") != 14 or expected.get("accepted_decisions") != 14 or expected.get("present_both_observations") != 14:
        raise TalkComparisonPhase31C3BError("C3A expected result count mismatch")
    if expected.get("labels_per_observation") != ["parser_delta", "provenance_only"]:
        raise TalkComparisonPhase31C3BError("C3A labels changed")
    return value


def _verify_repo_seal(repo_root: Path, relative: str, expected_sha256: str) -> dict[str, Any]:
    raw = _read_regular(repo_root / relative, relative)
    actual_blob = _git(repo_root, "hash-object", "--", relative)
    tracked_blob = _git(repo_root, "rev-parse", f"HEAD:{relative}")
    if actual_blob != tracked_blob or sha256_bytes(raw) != expected_sha256:
        raise TalkComparisonPhase31C3BError(f"tracked repository seal mismatch: {relative}")
    return {"relative_path": relative, "git_blob": actual_blob, "sha256": sha256_bytes(raw), "size_bytes": len(raw)}


def _verify_source_checkout(source_root: Path) -> None:
    if not source_root.is_dir():
        raise TalkComparisonPhase31C3BError(f"source checkout is missing: {source_root}")
    if _git(source_root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise TalkComparisonPhase31C3BError("pinned source checkout is not clean")
    if _git(source_root, "rev-parse", "HEAD") != COMMIT or _git(source_root, "rev-parse", "HEAD^{tree}") != TREE:
        raise TalkComparisonPhase31C3BError("source checkout is not the pinned 7.0 tree")


def _load_source_file(source_root: Path, manifest_file: Mapping[str, Any]) -> tuple[bytes, dict[str, Any]]:
    relative = str(manifest_file["relative_path"])
    raw = _read_regular(source_root / relative, relative)
    actual_blob = _git(source_root, "hash-object", "--", relative)
    expected_blob = str(manifest_file.get("git_blob", manifest_file.get("blob_oid", "")))
    if actual_blob != expected_blob or sha256_bytes(raw) != manifest_file.get("sha256") or len(raw) != int(manifest_file.get("size_bytes", -1)):
        raise TalkComparisonPhase31C3BError(f"pinned source file seal mismatch: {relative}")
    return raw, dict(manifest_file)


def _talk_records(raw: bytes) -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]]]:
    value = _strict_json(raw, TALK_PATH)
    if not isinstance(value, dict) or value.get("IOKNFDJFGDH") != 700814 or value.get("ALBFHGKNMLK") != NAMESPACE:
        raise TalkComparisonPhase31C3BError("Talk 700814 root identity mismatch")
    nodes = value.get("PFALHAKIILD")
    if not isinstance(nodes, list) or len(nodes) != 14:
        raise TalkComparisonPhase31C3BError("Talk 700814 must contain 14 nodes")
    records: list[dict[str, Any]] = []
    by_id: dict[str, int] = {}
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise TalkComparisonPhase31C3BError(f"Talk node {index} is not an object")
        node_id = node.get("OIFGMOHKPOI")
        next_ids = node.get("KMLAFCBMFEI")
        text_hash = node.get("OACNIBLFFDI")
        if not isinstance(node_id, int) or isinstance(node_id, bool) or not isinstance(next_ids, list) or not all(isinstance(item, int) and not isinstance(item, bool) for item in next_ids) or not isinstance(text_hash, int) or isinstance(text_hash, bool):
            raise TalkComparisonPhase31C3BError(f"Talk node {index} has invalid raw fields")
        node_text = str(node_id)
        if node_text in by_id:
            raise TalkComparisonPhase31C3BError(f"duplicate Talk node ID: {node_text}")
        by_id[node_text] = index
        records.append({
            "index": index,
            "node_id": node_text,
            "source_pointer": f"/dialogList/{index}",
            "content_textmap_hash": str(text_hash),
            "next_node_ids": [str(item) for item in next_ids],
            "raw_record_sha256": sha256_bytes(canonical(node).encode("utf-8")),
        })
    if tuple(sorted(by_id)) != tuple(sorted(str(70081401 + index) for index in range(14))):
        raise TalkComparisonPhase31C3BError("Talk node ID set changed")
    edges: list[dict[str, Any]] = []
    for record in records:
        for target in record["next_node_ids"]:
            if target not in by_id:
                raise TalkComparisonPhase31C3BError(f"Talk edge points to unknown node: {target}")
            edges.append({
                "edge_type": "next",
                "from_node_id": record["node_id"],
                "from_source_pointer": record["source_pointer"],
                "to_node_id": target,
                "to_source_pointer": f"/dialogList/{by_id[target]}",
            })
    if len(edges) != 14:
        raise TalkComparisonPhase31C3BError("Talk edge count changed")
    return value, records, edges


def derive_source_evidence(source_root: str | Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Read and independently seal the seven source files selected by C3A."""
    root = Path(source_root).resolve()
    _verify_source_checkout(root)
    selected = manifest.get("selected_files")
    if not isinstance(selected, list) or len(selected) != 8:
        raise TalkComparisonPhase31C3BError("C3A selected input list must contain eight entries")
    source_entries = [item for item in selected if isinstance(item, dict) and item.get("origin") == "pinned_anime_game_data"]
    if len(source_entries) != 7 or tuple(item.get("relative_path") for item in source_entries) != SOURCE_PATHS:
        raise TalkComparisonPhase31C3BError("C3A selected source file boundary changed")
    scope_entry = selected[7]
    if scope_entry.get("relative_path") != SCOPE_MANIFEST_PATH or scope_entry.get("sha256") != SCOPE_MANIFEST_SHA256:
        raise TalkComparisonPhase31C3BError("C3A scope manifest seal changed")
    # The caller supplies a repository-root manifest, so source-root parsing
    # stays independent from repository path traversal.  Repository seals are
    # verified by ``verify_repository_inputs`` below.
    files: list[dict[str, Any]] = []
    raw_by_path: dict[str, bytes] = {}
    for item in source_entries:
        raw, metadata = _load_source_file(root, item)
        raw_by_path[str(item["relative_path"])] = raw
        files.append(metadata)
    talk_value, records, edges = _talk_records(raw_by_path[TALK_PATH])
    expected_graph = manifest["graph_contract"]
    if sequence_digest(edges) != expected_graph.get("edge_tuple_sha256") or edges != expected_graph.get("directed_edges"):
        raise TalkComparisonPhase31C3BError("Talk graph does not match C3A graph contract")
    maps: dict[tuple[str, str], dict[str, Any]] = {}
    file_by_path = {str(item["relative_path"]): item for item in files}
    for language in LOCALES:
        for shard in ("main", "medium"):
            relative = TEXTMAP_PATHS[language][shard]
            value = _strict_json(raw_by_path[relative], relative)
            if not isinstance(value, dict):
                raise TalkComparisonPhase31C3BError(f"TextMap is not an object: {relative}")
            maps[(language, shard)] = value
    texts: list[dict[str, Any]] = []
    for record in records:
        text_hash = str(record["content_textmap_hash"])
        for language in LOCALES:
            main, medium = maps[(language, "main")], maps[(language, "medium")]
            if text_hash not in main or text_hash in medium:
                raise TalkComparisonPhase31C3BError("TextMap resolution is not the sealed unique_main result")
            raw_text = "" if main[text_hash] is None else str(main[text_hash])
            texts.append({
                "dialog_index": int(record["index"]),
                "node_id": str(record["node_id"]),
                "language": language,
                "textmap_hash": text_hash,
                "status": "unique_main",
                "raw_text": raw_text,
                "normalized_text": normalize_text(raw_text),
                "relative_path": TEXTMAP_PATHS[language]["main"],
                "source_pointer": f"/entries/{text_hash}",
            })
    if len(texts) != 42:
        raise TalkComparisonPhase31C3BError("TextMap occurrence count changed")
    outputs = manifest.get("outputs")
    raw_output = outputs.get("raw") if isinstance(outputs, dict) else None
    actual_talk_sha256 = sha256_bytes(raw_by_path[TALK_PATH])
    if (
        not isinstance(raw_output, dict)
        or raw_output.get("byte_sha256") != actual_talk_sha256
        or raw_output.get("byte_count") != len(raw_by_path[TALK_PATH])
    ):
        raise TalkComparisonPhase31C3BError(
            "C3A raw output byte seal does not match the pinned Talk file"
        )
    if not isinstance(outputs, dict) or outputs.get("raw", {}).get("tuple_sha256") != sequence_digest(records) or outputs.get("text", {}).get("tuple_sha256") != sequence_digest(texts) or outputs.get("graph", {}).get("tuple_sha256") != sequence_digest(edges):
        raise TalkComparisonPhase31C3BError("source output digest does not match C3A")
    return {
        "talk_value": talk_value,
        "talk_bytes": raw_by_path[TALK_PATH],
        "talk_raw_sha256": actual_talk_sha256,
        "talk_raw_size_bytes": len(raw_by_path[TALK_PATH]),
        "records": records,
        "edges": edges,
        "texts": texts,
        "files": files,
        "raw_by_path": raw_by_path,
        "file_by_path": file_by_path,
    }


def verify_repository_inputs(repo_root: str | Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    scope = _verify_repo_seal(root, SCOPE_MANIFEST_PATH, SCOPE_MANIFEST_SHA256)
    migration = _verify_repo_seal(root, MIGRATION_PATH, MIGRATION_SHA256)
    source_manifest = manifest.get("source_manifest")
    if not isinstance(source_manifest, dict):
        raise TalkComparisonPhase31C3BError("C3A source manifest metadata is missing")
    source_path = "manifests/sources/anime-game-data-7.0.json"
    source_raw = _read_regular(root / source_path, source_path)
    if sha256_bytes(source_raw) != source_manifest.get("sha256") or len(source_raw) != int(source_manifest.get("size_bytes", -1)):
        raise TalkComparisonPhase31C3BError("tracked source manifest seal mismatch")
    return {"scope_manifest": scope, "migration": migration, "source_manifest": {"relative_path": source_path, "sha256": sha256_bytes(source_raw), "size_bytes": len(source_raw)}}


def side_entry(manifest: Mapping[str, Any], side: str) -> dict[str, Any]:
    entries = [item for item in manifest.get("parser_builds", []) if item.get("side") == side]
    if len(entries) != 1:
        raise TalkComparisonPhase31C3BError(f"manifest parser build side is not unique: {side}")
    return dict(entries[0])


def ingest_entry(manifest: Mapping[str, Any], side: str) -> dict[str, Any]:
    entries = [item for item in manifest.get("ingest_runs", []) if item.get("side") == side]
    if len(entries) != 1:
        raise TalkComparisonPhase31C3BError(f"manifest ingest run side is not unique: {side}")
    return dict(entries[0])


def proof_entry(manifest: Mapping[str, Any], side: str) -> dict[str, Any]:
    entries = [item for item in manifest.get("scope_proofs", []) if item.get("side") == side]
    if len(entries) != 1:
        raise TalkComparisonPhase31C3BError(f"manifest scope proof side is not unique: {side}")
    return dict(entries[0])


def proof_artifact_entry(manifest: Mapping[str, Any], side: str) -> dict[str, Any]:
    entries = [item for item in manifest.get("proof_artifacts", []) if item.get("side") == side]
    if len(entries) != 1:
        raise TalkComparisonPhase31C3BError(f"manifest proof artifact side is not unique: {side}")
    return dict(entries[0])


__all__ = [
    "COMMIT", "CONTRACT_VERSION", "FAMILY", "LOCALES", "MANIFEST_PATH", "MANIFEST_SHA256",
    "MATCHER_METHOD", "MIGRATION_PATH", "MIGRATION_SHA256", "NAMESPACE", "NODE_ORDER",
    "OBSERVED_AT", "PROOF_ALGORITHM", "SCOPE_KEY", "SCOPE_MANIFEST_PATH", "SCOPE_MANIFEST_SHA256",
    "SIDES", "SNAPSHOT_ID", "SOURCE_PATHS", "TALK_ID", "TALK_PATH", "TEXTMAP_PATHS",
    "TalkComparisonPhase31C3BError", "canonical", "derive_source_evidence", "ingest_entry",
    "json_bytes", "load_manifest", "normalize_text", "occurrence_id", "proof_entry",
    "sequence_digest", "sha256_bytes", "side_entry", "verify_repository_inputs", "proof_artifact_entry",
]
