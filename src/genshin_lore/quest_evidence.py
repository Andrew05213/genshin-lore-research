from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .db import Database
from .parsers import (
    deob_quest,
    deob_quest_generic,
    deob_quest_talk_reference,
    deob_talk_generic,
)
from .source import SourceBundle, load_manifest, snapshot_id
from .talk_evidence import project_talk_root
from .talk_textmap import (
    ALL_TEXTMAP_PATHS,
    LANGUAGES,
    ResolvedNodeText,
    TEXTMAP_SHARD_PATHS,
    parse_textmap_json_with_duplicates,
    resolve_talk_textmap,
)
from .util import canonical_json, digest_id, sha256_bytes, sha256_text


QUEST_7008_PATH = "BinOutput/Quest/63ccb113.json"
TALK_700814_PATH = "BinOutput/Talk/Quest/79661eb8.json"
QUEST_7008_ID = 7008
TALK_700814_ID = 700814
QUEST_NAMESPACE = "QUEST"
PARSER_VERSION = "quest-evidence-textmap-v1"
MAPPING_VERSION = "quest-evidence-textmap-v1"
SELECTED_PATHS = [QUEST_7008_PATH, TALK_700814_PATH, *ALL_TEXTMAP_PATHS]


@dataclass(frozen=True)
class QuestEvidenceLink:
    """One explicit Quest-reference resolution outcome."""

    document_evidence_id: str
    document_id: str
    ingest_run_id: str
    namespace: str
    talk_id: str
    result: str
    evidence_container_id: str | None
    identity_group_id: str | None
    candidate_container_ids: tuple[str, ...]
    source_record_id: str | None

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
        }


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
        """SELECT ingest_run_id, namespace, talk_id
           FROM identity_group WHERE id=?""",
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
                   identity_group_id, evidence_container_id)
               VALUES (?, ?)
               ON CONFLICT(identity_group_id, evidence_container_id) DO NOTHING""",
            (group_id, container_id),
        )
    return group_id


def _existing_link(
    db: Database,
    *,
    document_id: str,
    ingest_run_id: str,
    namespace: str,
    talk_id: str,
    result: str,
) -> sqlite3.Row | None:
    return db.conn.execute(
        """SELECT * FROM document_evidence
           WHERE document_id=? AND ingest_run_id=? AND namespace=?
             AND talk_id=? AND result=?""",
        (document_id, ingest_run_id, namespace, talk_id, result),
    ).fetchone()


def _link_from_row(db: Database, row: sqlite3.Row) -> QuestEvidenceLink:
    candidate_ids: tuple[str, ...] = ()
    if row["identity_group_id"] is not None:
        candidate_ids = tuple(
            str(item[0])
            for item in db.conn.execute(
                """SELECT evidence_container_id
                   FROM identity_group_container
                   WHERE identity_group_id=? ORDER BY evidence_container_id""",
                (row["identity_group_id"],),
            ).fetchall()
        )
    elif row["evidence_container_id"] is not None:
        candidate_ids = (str(row["evidence_container_id"]),)
    return QuestEvidenceLink(
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
            str(row["identity_group_id"])
            if row["identity_group_id"] is not None
            else None
        ),
        candidate_container_ids=candidate_ids,
        source_record_id=(
            str(row["source_record_id"])
            if row["source_record_id"] is not None
            else None
        ),
    )


def _insert_link(
    db: Database,
    *,
    document_id: str,
    ingest_run_id: str,
    namespace: str,
    talk_id: str,
    result: str,
    evidence_container_id: str | None,
    identity_group_id: str | None,
    source_record_id: str | None,
    candidate_count: int,
) -> QuestEvidenceLink:
    existing = _existing_link(
        db,
        document_id=document_id,
        ingest_run_id=ingest_run_id,
        namespace=namespace,
        talk_id=talk_id,
        result=result,
    )
    if existing is not None:
        expected = (evidence_container_id, identity_group_id)
        actual = (existing["evidence_container_id"], existing["identity_group_id"])
        if actual != expected:
            raise sqlite3.IntegrityError(
                f"document_evidence identity mismatch for {existing['id']}"
            )
        return _link_from_row(db, existing)

    target_id = evidence_container_id or identity_group_id or "unresolved"
    row_id = digest_id(
        "de",
        document_id,
        ingest_run_id,
        namespace,
        talk_id,
        result,
        target_id,
    )
    db.conn.execute(
        """INSERT INTO document_evidence(
               id, document_id, ingest_run_id, namespace, talk_id, result,
               evidence_container_id, identity_group_id, source_record_id,
               metadata_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            row_id,
            document_id,
            ingest_run_id,
            namespace,
            talk_id,
            result,
            evidence_container_id,
            identity_group_id,
            source_record_id,
            canonical_json(
                {
                    "method": "exact_native_key",
                    "candidate_count": candidate_count,
                }
            ),
        ),
    )
    row = db.conn.execute("SELECT * FROM document_evidence WHERE id=?", (row_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"document_evidence insert disappeared: {row_id}")
    return _link_from_row(db, row)


def _phase31b_unhardened_link(
    db: Database,
    *,
    document_id: str,
    ingest_run_id: str,
    namespace: str,
    talk_id: int | str,
    source_record_id: str | None = None,
) -> QuestEvidenceLink:
    """Link one Quest reference to evidence without creating a logical Talk.

    The candidate query is exact on the ingest run, native namespace, native
    Talk ID, and ``talk_root`` kind.  It intentionally has no checksum or text
    predicate and never selects one candidate from an ambiguous result.
    """

    native_namespace = str(namespace)
    native_talk_id = str(talk_id)
    candidates = db.conn.execute(
        """SELECT id
           FROM evidence_container
           WHERE ingest_run_id=? AND kind='talk_root'
             AND namespace=? AND talk_id=?
           ORDER BY id""",
        (ingest_run_id, native_namespace, native_talk_id),
    ).fetchall()
    candidate_ids = tuple(str(row[0]) for row in candidates)
    if len(candidate_ids) == 1:
        return _insert_link(
            db,
            document_id=document_id,
            ingest_run_id=ingest_run_id,
            namespace=native_namespace,
            talk_id=native_talk_id,
            result="evidence_only",
            evidence_container_id=candidate_ids[0],
            identity_group_id=None,
            source_record_id=source_record_id,
            candidate_count=1,
        )
    if len(candidate_ids) > 1:
        group_id = _identity_group(
            db,
            ingest_run_id=ingest_run_id,
            namespace=native_namespace,
            talk_id=native_talk_id,
            container_ids=candidate_ids,
        )
        return _insert_link(
            db,
            document_id=document_id,
            ingest_run_id=ingest_run_id,
            namespace=native_namespace,
            talk_id=native_talk_id,
            result="ambiguous",
            evidence_container_id=None,
            identity_group_id=group_id,
            source_record_id=source_record_id,
            candidate_count=len(candidate_ids),
        )
    return _insert_link(
        db,
        document_id=document_id,
        ingest_run_id=ingest_run_id,
        namespace=native_namespace,
        talk_id=native_talk_id,
        result="unresolved",
        evidence_container_id=None,
        identity_group_id=None,
        source_record_id=source_record_id,
        candidate_count=0,
    )


def _register_source_file(
    db: Database,
    *,
    snapshot_id_value: str,
    located: Any,
    raw: bytes,
) -> str:
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


def ingest_quest_7008_evidence_slice(
    db_path: str | Path,
    source_roots: list[str | Path],
    manifest_path: str | Path,
    candidate_scope_manifest_path: str | Path | None = None,
    audit_cache_path: str | Path | None = None,
) -> dict[str, Any]:
    """Project only Quest 7008 and Talk 700814 with selected TextMap hydration into the evidence layer.

    This is deliberately a selected-path fixture operation.  It reads only the 8 selected paths:
    Quest 7008, Talk 700814, and the 6 CHS/EN/JP main+Medium TextMap shards.  It does not
    build a Talk index, inventory a full source bundle, ingest other Quest roots,
    rebuild FTS, or write legacy logical Talk/passage structures.
    """

    manifest = load_manifest(manifest_path)
    snapshot_id_value = snapshot_id(manifest)
    repository_root = Path(__file__).resolve().parents[2]
    candidate_scope = load_candidate_scope_manifest(
        candidate_scope_manifest_path
        or repository_root / "manifests/candidate-scopes/anime-game-data-7.0-quest-7008-talk-700814.json",
        audit_cache_path=audit_cache_path
        or repository_root / "cache/discovery/talk-index-anime-game-data_7_0_26df1dfbdf05-c7f76e60ca30ebdb683e.json",
    )
    bundle = SourceBundle(source_roots)
    parser_build_id = digest_id("parser-build", snapshot_id_value, PARSER_VERSION)
    ingest_run_id = digest_id("ingest-run", snapshot_id_value, "quest-7008-talk-700814-textmap-v1")
    quest_document_id = "agd:quest:7008"

    with Database(db_path) as db:
        db.initialize()
        legacy_before = {
            "logical_talk": int(db.conn.execute("SELECT COUNT(*) FROM logical_talk").fetchone()[0]),
            "talk_document": int(db.conn.execute("SELECT COUNT(*) FROM document WHERE content_type='talk'").fetchone()[0]),
            "passage": int(db.conn.execute("SELECT COUNT(*) FROM passage").fetchone()[0]),
            "passage_edge": int(db.conn.execute("SELECT COUNT(*) FROM passage_edge").fetchone()[0]),
        }
        old_runs_count = int(
            db.conn.execute("SELECT COUNT(*) FROM ingest_run WHERE id != ?", (ingest_run_id,)).fetchone()[0]
        )
        old_run_preserved = (old_runs_count > 0)

        with db.transaction():
            db.ensure_source(
                manifest["source_id"],
                "primary",
                manifest["upstream"],
                "Upstream repository snapshot",
            )
            db.ensure_snapshot(
                {
                    "id": snapshot_id_value,
                    "source_id": manifest["source_id"],
                    "release_label": manifest["release_label"],
                    "build": manifest["build"],
                    "commit": manifest["commit"],
                    "tree": manifest["tree"],
                    "observed_at": manifest.get("observed_at", "2026-08-21T00:00:00Z"),
                    "parser_version": "quest-evidence-v1",
                    "mapping_version": "quest-evidence-v1",
                    "status": "active",
                    "metadata": {"scope": "Quest 7008 / Talk 700814 evidence-only"},
                }
            )
            db.ensure_parser_build(
                parser_build_id,
                parser_version=PARSER_VERSION,
                mapping_version=MAPPING_VERSION,
                fingerprint="quest-7008-talk-700814",
                config_checksum="selected-paths-only",
            )
            db.ensure_ingest_run(
                ingest_run_id,
                snapshot_id=snapshot_id_value,
                parser_build_id=parser_build_id,
                scope={
                    "family": "quest_evidence",
                    "quest_ids": [QUEST_7008_ID],
                    "talk_ids": [TALK_700814_ID],
                    "selected_paths": SELECTED_PATHS,
                },
                config={
                    "projection": "talk_occurrence",
                    "languages": list(LANGUAGES),
                    "full_scan": False,
                    "textmap_hydration": "v1",
                },
                status="running",
            )

            quest_located, raw_quest = bundle.read_json(QUEST_7008_PATH)
            normalized_quest = deob_quest(raw_quest)
            quest_raw = quest_located.path.read_bytes()
            quest_file_id = _register_source_file(
                db,
                snapshot_id_value=snapshot_id_value,
                located=quest_located,
                raw=quest_raw,
            )
            quest_record_id = db.ensure_source_record(
                quest_file_id,
                "/",
                "quest_root",
                QUEST_7008_ID,
                raw_quest,
                raw_json=canonical_json(raw_quest),
            )
            db.ensure_document(quest_document_id, "quest", "quest", QUEST_7008_ID, "active")
            db.ensure_document_version(
                quest_document_id,
                snapshot_id_value,
                quest_record_id,
                raw_quest,
                normalized_quest,
                {"scope": "selected Quest 7008 root", "mapping_version": "quest-evidence-v1"},
            )

            selected_index: int | None = None
            selected_raw_reference: dict[str, Any] | None = None
            for index, raw_reference in enumerate(raw_quest.get("OJACLOOEAMG", [])):
                reference = deob_quest_talk_reference(raw_reference)
                if reference.get("talkId") == TALK_700814_ID:
                    selected_index = index
                    selected_raw_reference = raw_reference
                    break
            if selected_index is None or selected_raw_reference is None:
                raise ValueError("Quest 7008 fixture does not contain Talk 700814")
            reference_record_id = db.ensure_source_record(
                quest_file_id,
                f"/OJACLOOEAMG/{selected_index}",
                "quest_talk_reference",
                TALK_700814_ID,
                selected_raw_reference,
                raw_json=canonical_json(selected_raw_reference),
            )

            talk_located, raw_talk = bundle.read_json(TALK_700814_PATH)
            talk_file_raw = talk_located.path.read_bytes()
            talk_file_id = _register_source_file(
                db,
                snapshot_id_value=snapshot_id_value,
                located=talk_located,
                raw=talk_file_raw,
            )
            talk_record_id = db.ensure_source_record(
                talk_file_id,
                "/",
                "talk_root",
                TALK_700814_ID,
                talk_file_raw,
                raw_json=talk_file_raw.decode("utf-8"),
            )
            normalized_talk = deob_talk_generic(raw_talk)
            dialog_list = normalized_talk["dialogList"]

            main_shards: dict[str, tuple[dict[str, str], set[str]]] = {}
            med_shards: dict[str, tuple[dict[str, str], set[str]]] = {}
            shard_file_ids: dict[str, str] = {}

            for lang in LANGUAGES:
                for shard_type in ("main", "medium"):
                    rel_path = TEXTMAP_SHARD_PATHS[lang][shard_type]
                    shard_located, shard_raw_bytes = bundle.read_bytes(rel_path)
                    shard_file_id = _register_source_file(
                        db,
                        snapshot_id_value=snapshot_id_value,
                        located=shard_located,
                        raw=shard_raw_bytes,
                    )
                    shard_file_ids[rel_path] = shard_file_id
                    parsed_dict, dups = parse_textmap_json_with_duplicates(shard_raw_bytes)
                    if shard_type == "main":
                        main_shards[lang] = (parsed_dict, dups)
                    else:
                        med_shards[lang] = (parsed_dict, dups)

            resolution = resolve_talk_textmap(
                dialog_list=dialog_list,
                textmap_shards=main_shards,
                medium_shards=med_shards,
                languages=LANGUAGES,
            )

            registered_records: dict[tuple[str, str], str] = {}
            for item in resolution.node_texts:
                for cand in item.candidates:
                    key = (cand.relative_path, item.textmap_hash)
                    if key not in registered_records:
                        file_id = shard_file_ids[cand.relative_path]
                        textmap_entry = {
                            "hash": item.textmap_hash,
                            "value": cand.raw_text,
                        }
                        rec_id = db.ensure_source_record(
                            file_id,
                            cand.pointer,
                            "textmap_entry",
                            item.textmap_hash,
                            textmap_entry,
                            raw_json=canonical_json(textmap_entry),
                        )
                        registered_records[key] = rec_id

            resolved_with_records: list[ResolvedNodeText] = []
            for item in resolution.node_texts:
                rec_id = None
                if item.relative_path and item.status in ("unique_main", "unique_medium", "both_equal"):
                    rec_id = registered_records.get((item.relative_path, item.textmap_hash))
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
                ingest_run_id=ingest_run_id,
                source_record_id=talk_record_id,
                raw_talk=raw_talk,
                namespace=QUEST_NAMESPACE,
                resolved_texts=resolved_with_records,
            )
            link = link_quest_reference_to_evidence(
                db,
                document_id=quest_document_id,
                ingest_run_id=ingest_run_id,
                namespace=QUEST_NAMESPACE,
                talk_id=TALK_700814_ID,
                source_record_id=reference_record_id,
                candidate_scope=candidate_scope,
                reference_metadata={"normalized_pointer": f"/talks/{selected_index}"},
            )
            db.ensure_ingest_run(
                ingest_run_id,
                snapshot_id=snapshot_id_value,
                parser_build_id=parser_build_id,
                scope={
                    "family": "quest_evidence",
                    "quest_ids": [QUEST_7008_ID],
                    "talk_ids": [TALK_700814_ID],
                    "selected_paths": SELECTED_PATHS,
                },
                config={
                    "projection": "talk_occurrence",
                    "languages": list(LANGUAGES),
                    "full_scan": False,
                    "textmap_hydration": "v1",
                },
                status="complete",
            )

            legacy_after = {
                "logical_talk": int(db.conn.execute("SELECT COUNT(*) FROM logical_talk").fetchone()[0]),
                "talk_document": int(db.conn.execute("SELECT COUNT(*) FROM document WHERE content_type='talk'").fetchone()[0]),
                "passage": int(db.conn.execute("SELECT COUNT(*) FROM passage").fetchone()[0]),
                "passage_edge": int(db.conn.execute("SELECT COUNT(*) FROM passage_edge").fetchone()[0]),
            }
            legacy_writes = {key: legacy_after[key] - legacy_before[key] for key in legacy_before}
            return {
                "status": "complete",
                "snapshot_id": snapshot_id_value,
                "ingest_run_id": ingest_run_id,
                "parser_build_id": parser_build_id,
                "quest_id": QUEST_7008_ID,
                "talk_id": TALK_700814_ID,
                "link": link.as_dict(),
                "projection": {
                    "evidence_container_id": projection.evidence_container_id,
                    "identity_group_id": projection.identity_group_id,
                    "occurrence_count": len(projection.occurrence_ids),
                    "edge_count": len(projection.occurrence_edge_ids),
                },
                "text_hydration": {
                    "status": resolution.status,
                    "languages": list(resolution.languages),
                    "resolved_occurrence_text_count": resolution.resolved_count,
                    "counts_by_language": dict(resolution.counts_by_language),
                    "nonempty_count": resolution.nonempty_count,
                    "missing": resolution.missing_count,
                    "both_equal": resolution.both_equal_count,
                    "both_conflict": resolution.both_conflict_count,
                    "duplicate": resolution.duplicate_count,
                    "selected_content_hash_count": len(resolution.selected_content_hashes),
                    "selected_textmap_source_file_count": len(shard_file_ids),
                    "selected_textmap_source_record_count": len(registered_records),
                    "role_name_hydrated": False,
                    "old_run_preserved": old_run_preserved,
                },
                "scope": {
                    "selected_paths": SELECTED_PATHS,
                    "full_scan": False,
                    "fts_rebuilt": False,
                    "embedding": False,
                },
                "candidate_scope": {
                    "manifest_id": candidate_scope.manifest_id,
                    "audit_cache_state": candidate_scope.audit_cache_state,
                },
                "legacy_writes": legacy_writes,
            }


from .quest_resolution import (
    CandidateScopeManifest,
    QuestCandidateBundleManifest,
    link_quest_reference_to_evidence as _link_quest_reference_to_evidence,
    load_candidate_scope_manifest,
    load_quest_candidate_bundle_manifest,
)


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
) -> Any:
    """Resolve a raw Quest reference through the hardened scope contract."""

    return _link_quest_reference_to_evidence(
        db,
        document_id=document_id,
        ingest_run_id=ingest_run_id,
        namespace=namespace,
        talk_id=talk_id,
        source_record_id=source_record_id,
        candidate_scope=candidate_scope,
        reference_metadata=reference_metadata,
    )


PARSER_VERSION_COMPLETE = "quest-evidence-complete-v1"
MAPPING_VERSION_COMPLETE = "quest-evidence-complete-v1"


@dataclass(frozen=True)
class PreflightVerifiedCandidate:
    candidate_spec: CandidateManifestEntry
    located: Any
    raw_bytes: bytes
    raw_talk: dict[str, Any]
    norm_talk: dict[str, Any]
    dialog_list: list[dict[str, Any]]
    node_count: int
    edge_count: int


@dataclass(frozen=True)
class PreflightVerifiedReference:
    ref_spec: QuestCandidateBundleReference
    index: int
    pointer: str
    namespace: str
    talk_id: str
    raw_reference: dict[str, Any]
    deob_reference: dict[str, Any]
    ref_scope: CandidateScopeManifest
    candidates: tuple[PreflightVerifiedCandidate, ...]


@dataclass(frozen=True)
class PreflightCompleteBundleResult:
    manifest: dict[str, Any]
    bundle_manifest: QuestCandidateBundleManifest
    snapshot_id_value: str
    parser_build_id: str
    ingest_run_id: str
    quest_document_id: str
    quest_located: Any
    quest_raw_bytes: bytes
    raw_quest: dict[str, Any]
    norm_quest: dict[str, Any]
    main_shards: dict[str, tuple[dict[str, str], set[str]]]
    med_shards: dict[str, tuple[dict[str, str], set[str]]]
    shard_located_and_bytes: dict[str, tuple[Any, bytes]]
    references: tuple[PreflightVerifiedReference, ...]
    selected_paths: list[str]


def preflight_quest_7008_complete_evidence_bundle(
    source_roots: list[str | Path],
    manifest_path: str | Path,
    bundle_manifest_path: str | Path | None = None,
    audit_cache_path: str | Path | None = None,
) -> PreflightCompleteBundleResult:
    """Validate all source provenance, files, hashes, and candidates without DB side-effects."""
    manifest = load_manifest(manifest_path)
    snapshot_id_value = snapshot_id(manifest)
    repository_root = Path(__file__).resolve().parents[2]

    bundle_manifest = load_quest_candidate_bundle_manifest(
        bundle_manifest_path
        or repository_root
        / "manifests/candidate-scopes/anime-game-data-7.0-quest-7008-all-talks.json",
        audit_cache_path=audit_cache_path
        or repository_root
        / "cache/discovery/talk-index-anime-game-data_7_0_26df1dfbdf05-c7f76e60ca30ebdb683e.json",
    )

    if bundle_manifest.structural_errors:
        raise ValueError(
            f"Bundle manifest structural errors: {bundle_manifest.structural_errors}"
        )
    if bundle_manifest.audit_cache_state != "verified":
        raise ValueError(
            f"Bundle manifest audit cache state is {bundle_manifest.audit_cache_state!r}, expected 'verified'"
        )
    if not bundle_manifest.inventory_complete:
        raise ValueError("Bundle manifest inventory proof is incomplete")

    # Verify source snapshot vs manifest
    if bundle_manifest.snapshot_id != snapshot_id_value:
        raise ValueError(
            f"snapshot_id mismatch: source manifest {snapshot_id_value} vs bundle {bundle_manifest.snapshot_id}"
        )
    if bundle_manifest.snapshot_commit != manifest["commit"]:
        raise ValueError(
            f"commit mismatch: source manifest {manifest['commit']} vs bundle {bundle_manifest.snapshot_commit}"
        )
    if bundle_manifest.snapshot_tree != manifest["tree"]:
        raise ValueError(
            f"tree mismatch: source manifest {manifest['tree']} vs bundle {bundle_manifest.snapshot_tree}"
        )
    if bundle_manifest.source_id != manifest["source_id"]:
        raise ValueError(
            f"source_id mismatch: source manifest {manifest['source_id']} vs bundle {bundle_manifest.source_id}"
        )
    if bundle_manifest.release_label != manifest["release_label"]:
        raise ValueError(
            f"release_label mismatch: source manifest {manifest['release_label']} vs bundle {bundle_manifest.release_label}"
        )

    bundle = SourceBundle(source_roots)

    # Verify Quest file
    try:
        quest_located, quest_raw_bytes = bundle.read_bytes(
            bundle_manifest.quest_relative_path
        )
    except Exception as exc:
        raise ValueError(
            f"Cannot read Quest file {bundle_manifest.quest_relative_path}: {exc}"
        ) from exc

    actual_quest_sha = sha256_bytes(quest_raw_bytes)
    if actual_quest_sha != bundle_manifest.quest_raw_sha256:
        raise ValueError(
            f"Quest raw SHA256 mismatch: actual {actual_quest_sha} vs manifest {bundle_manifest.quest_raw_sha256}"
        )
    if len(quest_raw_bytes) != bundle_manifest.quest_raw_size_bytes:
        raise ValueError(
            f"Quest raw size mismatch: actual {len(quest_raw_bytes)} vs manifest {bundle_manifest.quest_raw_size_bytes}"
        )

    try:
        raw_quest = json.loads(quest_raw_bytes.decode("utf-8-sig"))
    except Exception as exc:
        raise ValueError(f"Quest JSON decode failed: {exc}") from exc

    norm_quest = deob_quest_generic(raw_quest)
    actual_quest_id = (
        norm_quest.get("id")
        or norm_quest.get("Id")
        or raw_quest.get("Id")
        or raw_quest.get("id")
    )
    if actual_quest_id != bundle_manifest.quest_id:
        raise ValueError(
            f"Quest ID mismatch: actual {actual_quest_id} vs manifest {bundle_manifest.quest_id}"
        )

    raw_ojac = raw_quest.get("OJACLOOEAMG")
    if not isinstance(raw_ojac, list):
        raise ValueError("Quest raw JSON missing OJACLOOEAMG array")
    if len(raw_ojac) != bundle_manifest.reference_count or len(raw_ojac) != len(
        bundle_manifest.references
    ):
        raise ValueError(
            f"Quest references count mismatch: actual {len(raw_ojac)} vs manifest count {bundle_manifest.reference_count}"
        )

    # Verify each reference and its candidates
    verified_refs: list[PreflightVerifiedReference] = []
    seen_talk_paths: list[str] = []

    for idx, ref_spec in enumerate(bundle_manifest.references):
        if ref_spec.index != idx:
            raise ValueError(
                f"Reference index {ref_spec.index} does not match array position {idx}"
            )
        expected_pointer = f"/OJACLOOEAMG/{idx}"
        if ref_spec.pointer != expected_pointer:
            raise ValueError(
                f"Reference pointer {ref_spec.pointer!r} does not match {expected_pointer!r}"
            )
        raw_ref = raw_ojac[idx]
        deob_ref = deob_quest_talk_reference(raw_ref)
        actual_ref_talk_id = str(deob_ref.get("talkId"))
        if ref_spec.talk_id != actual_ref_talk_id:
            raise ValueError(
                f"Reference talkId mismatch at {expected_pointer}: actual {actual_ref_talk_id} vs manifest {ref_spec.talk_id}"
            )

        ref_scope = bundle_manifest.reference_scope(
            ref_spec.talk_id,
            ref_spec.namespace,
            pointer=ref_spec.pointer,
            index=ref_spec.index,
        )
        if ref_scope.structural_errors:
            raise ValueError(
                f"Reference scope errors for {ref_spec.pointer}: {ref_scope.structural_errors}"
            )

        verified_cands: list[PreflightVerifiedCandidate] = []
        for cand in ref_spec.candidates:
            if cand.relative_path not in seen_talk_paths:
                seen_talk_paths.append(cand.relative_path)
            try:
                cand_loc, cand_bytes = bundle.read_bytes(cand.relative_path)
            except Exception as exc:
                raise ValueError(
                    f"Cannot read Talk candidate {cand.relative_path}: {exc}"
                ) from exc

            actual_cand_sha = sha256_bytes(cand_bytes)
            if actual_cand_sha != cand.raw_sha256:
                raise ValueError(
                    f"Candidate raw SHA256 mismatch for {cand.relative_path}: actual {actual_cand_sha} vs manifest {cand.raw_sha256}"
                )
            if len(cand_bytes) != cand.raw_size_bytes:
                raise ValueError(
                    f"Candidate raw size mismatch for {cand.relative_path}: actual {len(cand_bytes)} vs manifest {cand.raw_size_bytes}"
                )

            try:
                raw_talk = json.loads(cand_bytes.decode("utf-8-sig"))
            except Exception as exc:
                raise ValueError(
                    f"Candidate Talk JSON decode failed for {cand.relative_path}: {exc}"
                ) from exc

            norm_talk = deob_talk_generic(raw_talk)
            actual_cand_talk_id = str(
                norm_talk.get("talkId")
                or raw_talk.get("talkId")
                or raw_talk.get("id")
                or raw_talk.get("Id")
            )
            if actual_cand_talk_id != ref_spec.talk_id:
                raise ValueError(
                    f"Candidate native talk ID mismatch in {cand.relative_path}: actual {actual_cand_talk_id} vs reference {ref_spec.talk_id}"
                )

            actual_norm_ck = sha256_text(canonical_json(norm_talk))
            if actual_norm_ck != cand.normalized_checksum:
                raise ValueError(
                    f"Candidate normalized checksum mismatch for {cand.relative_path}: actual {actual_norm_ck} vs manifest {cand.normalized_checksum}"
                )

            dialog_list = norm_talk.get("dialogList", [])
            actual_node_cnt = len(dialog_list)
            actual_edge_cnt = sum(len(n.get("nextDialogs", [])) for n in dialog_list)
            if cand.node_count is not None and actual_node_cnt != cand.node_count:
                raise ValueError(
                    f"Candidate node_count mismatch for {cand.relative_path}: actual {actual_node_cnt} vs manifest {cand.node_count}"
                )
            if cand.edge_count is not None and actual_edge_cnt != cand.edge_count:
                raise ValueError(
                    f"Candidate edge_count mismatch for {cand.relative_path}: actual {actual_edge_cnt} vs manifest {cand.edge_count}"
                )

            verified_cands.append(
                PreflightVerifiedCandidate(
                    candidate_spec=cand,
                    located=cand_loc,
                    raw_bytes=cand_bytes,
                    raw_talk=raw_talk,
                    norm_talk=norm_talk,
                    dialog_list=dialog_list,
                    node_count=actual_node_cnt,
                    edge_count=actual_edge_cnt,
                )
            )

        verified_refs.append(
            PreflightVerifiedReference(
                ref_spec=ref_spec,
                index=idx,
                pointer=expected_pointer,
                namespace=ref_spec.namespace,
                talk_id=ref_spec.talk_id,
                raw_reference=raw_ref,
                deob_reference=deob_ref,
                ref_scope=ref_scope,
                candidates=tuple(verified_cands),
            )
        )

    # Verify TextMaps
    main_shards: dict[str, tuple[dict[str, str], set[str]]] = {}
    med_shards: dict[str, tuple[dict[str, str], set[str]]] = {}
    shard_located_and_bytes: dict[str, tuple[Any, bytes]] = {}

    if not bundle_manifest.textmaps:
        raise ValueError("Bundle manifest does not contain sealed textmaps specification")
    if len(bundle_manifest.textmaps) != 6:
        raise ValueError(
            f"Bundle manifest textmaps count must be 6, got {len(bundle_manifest.textmaps)}"
        )

    for tm in bundle_manifest.textmaps:
        try:
            shard_loc, shard_bytes = bundle.read_bytes(tm.relative_path)
        except Exception as exc:
            raise ValueError(
                f"Cannot read TextMap shard {tm.relative_path}: {exc}"
            ) from exc

        actual_tm_sha = sha256_bytes(shard_bytes)
        if actual_tm_sha != tm.raw_sha256:
            raise ValueError(
                f"TextMap shard {tm.relative_path} raw SHA256 mismatch: actual {actual_tm_sha} vs manifest {tm.raw_sha256}"
            )
        if len(shard_bytes) != tm.raw_size_bytes:
            raise ValueError(
                f"TextMap shard {tm.relative_path} raw size mismatch: actual {len(shard_bytes)} vs manifest {tm.raw_size_bytes}"
            )

        try:
            parsed_dict, dups = parse_textmap_json_with_duplicates(shard_bytes)
        except Exception as exc:
            raise ValueError(
                f"Cannot parse TextMap shard {tm.relative_path}: {exc}"
            ) from exc

        shard_located_and_bytes[tm.relative_path] = (shard_loc, shard_bytes)
        if tm.shard == "main":
            main_shards[tm.language] = (parsed_dict, dups)
        else:
            med_shards[tm.language] = (parsed_dict, dups)

    # Assemble dynamic selected_paths
    selected_paths: list[str] = [bundle_manifest.quest_relative_path]
    for sp in seen_talk_paths:
        if sp not in selected_paths:
            selected_paths.append(sp)
    for tp in ALL_TEXTMAP_PATHS:
        if tp not in selected_paths:
            selected_paths.append(tp)

    parser_build_id = digest_id(
        "parser-build",
        snapshot_id_value,
        PARSER_VERSION_COMPLETE,
        bundle_manifest.manifest_checksum,
    )
    ingest_run_id = digest_id(
        "ingest-run",
        snapshot_id_value,
        "quest-7008-complete-evidence-v1",
        bundle_manifest.manifest_checksum,
    )
    quest_document_id = "agd:quest:7008"

    return PreflightCompleteBundleResult(
        manifest=manifest,
        bundle_manifest=bundle_manifest,
        snapshot_id_value=snapshot_id_value,
        parser_build_id=parser_build_id,
        ingest_run_id=ingest_run_id,
        quest_document_id=quest_document_id,
        quest_located=quest_located,
        quest_raw_bytes=quest_raw_bytes,
        raw_quest=raw_quest,
        norm_quest=norm_quest,
        main_shards=main_shards,
        med_shards=med_shards,
        shard_located_and_bytes=shard_located_and_bytes,
        references=tuple(verified_refs),
        selected_paths=selected_paths,
    )


def ingest_quest_7008_complete_evidence_bundle(
    db_path: str | Path,
    source_roots: list[str | Path],
    manifest_path: str | Path,
    bundle_manifest_path: str | Path | None = None,
    audit_cache_path: str | Path | None = None,
) -> dict[str, Any]:
    """Project Quest 7008 and all 15 raw Talk references with TextMap hydration into evidence."""
    # 1. Run complete fail-closed preflight BEFORE creating or opening database
    preflight = preflight_quest_7008_complete_evidence_bundle(
        source_roots=source_roots,
        manifest_path=manifest_path,
        bundle_manifest_path=bundle_manifest_path,
        audit_cache_path=audit_cache_path,
    )

    # 2. Preflight succeeded; now create directory and open database
    target_db_path = Path(db_path)
    target_db_path.parent.mkdir(parents=True, exist_ok=True)

    with Database(target_db_path) as db:
        db.initialize()
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

        with db.transaction():
            db.ensure_source(
                preflight.manifest["source_id"],
                "primary",
                preflight.manifest["upstream"],
                "Upstream repository snapshot",
            )
            db.ensure_snapshot(
                {
                    "id": preflight.snapshot_id_value,
                    "source_id": preflight.manifest["source_id"],
                    "release_label": preflight.manifest["release_label"],
                    "build": preflight.manifest["build"],
                    "commit": preflight.manifest["commit"],
                    "tree": preflight.manifest["tree"],
                    "observed_at": preflight.manifest.get(
                        "observed_at", "2026-08-21T00:00:00Z"
                    ),
                    "parser_version": "quest-evidence-v1",
                    "mapping_version": "quest-evidence-v1",
                    "status": "active",
                    "metadata": {
                        "scope": "Quest 7008 complete 15-talk evidence bundle"
                    },
                }
            )
            db.ensure_parser_build(
                preflight.parser_build_id,
                parser_version=PARSER_VERSION_COMPLETE,
                mapping_version=MAPPING_VERSION_COMPLETE,
                fingerprint="quest-7008-complete-talk-bundle",
                config_checksum=f"selected-{len(preflight.selected_paths)}-paths",
            )
            db.ensure_ingest_run(
                preflight.ingest_run_id,
                snapshot_id=preflight.snapshot_id_value,
                parser_build_id=preflight.parser_build_id,
                scope={
                    "family": "quest_evidence",
                    "quest_ids": [QUEST_7008_ID],
                    "talk_ids": [int(r.talk_id) for r in preflight.references],
                    "selected_paths": preflight.selected_paths,
                },
                config={
                    "projection": "talk_occurrence",
                    "languages": list(LANGUAGES),
                    "full_scan": False,
                    "textmap_hydration": "v1",
                },
                status="running",
            )

            # Register Quest 7008 file & root record
            quest_file_id = _register_source_file(
                db,
                snapshot_id_value=preflight.snapshot_id_value,
                located=preflight.quest_located,
                raw=preflight.quest_raw_bytes,
            )
            quest_record_id = db.ensure_source_record(
                quest_file_id,
                "/",
                "quest_root",
                QUEST_7008_ID,
                preflight.raw_quest,
                raw_json=canonical_json(preflight.raw_quest),
            )
            db.ensure_document(
                preflight.quest_document_id,
                "quest",
                "quest",
                QUEST_7008_ID,
                "active",
            )
            db.ensure_document_version(
                preflight.quest_document_id,
                preflight.snapshot_id_value,
                quest_record_id,
                preflight.raw_quest,
                preflight.norm_quest,
                {
                    "scope": "selected Quest 7008 root",
                    "mapping_version": "quest-evidence-v1",
                },
            )

            # Register reference source records by (index, pointer)
            reference_records_by_index: dict[int, str] = {}
            for ref_item in preflight.references:
                ref_rec_id = db.ensure_source_record(
                    quest_file_id,
                    ref_item.pointer,
                    "quest_talk_reference",
                    int(ref_item.talk_id),
                    ref_item.raw_reference,
                    raw_json=canonical_json(ref_item.raw_reference),
                )
                reference_records_by_index[ref_item.index] = ref_rec_id

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

            # Project each candidate and link each reference
            projected_candidates: dict[str, tuple[Any, Any]] = {}
            registered_textmap_records: dict[tuple[str, str], str] = {}
            references_report: list[dict[str, Any]] = []
            talks_report: list[dict[str, Any]] = []
            total_resolved_text_count = 0
            total_nonempty_text_count = 0
            total_missing = 0
            total_both_equal = 0
            total_both_conflict = 0
            total_duplicate = 0
            counts_by_language: dict[str, int] = {lang: 0 for lang in LANGUAGES}
            distinct_content_hashes: set[str] = set()
            canary_700814: dict[str, Any] | None = None

            for ref_item in preflight.references:
                ref_rec_id = reference_records_by_index[ref_item.index]

                # Project each candidate in this reference
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
                            int(ref_item.talk_id),
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
                            namespace=ref_item.namespace,
                            resolved_texts=resolved_with_records,
                        )
                        projected_candidates[cand_path] = (projection, resolution)

                        if ref_item.talk_id == "700814":
                            canary_700814 = {
                                "talk_id": 700814,
                                "evidence_container_id": projection.evidence_container_id,
                                "occurrence_count": len(projection.occurrence_ids),
                                "edge_count": len(projection.occurrence_edge_ids),
                                "branch_node_id": "70081408",
                                "merge_node_id": "70081411",
                            }

                link = link_quest_reference_to_evidence(
                    db,
                    document_id=preflight.quest_document_id,
                    ingest_run_id=preflight.ingest_run_id,
                    namespace=ref_item.namespace,
                    talk_id=int(ref_item.talk_id),
                    source_record_id=ref_rec_id,
                    candidate_scope=ref_item.ref_scope,
                    reference_metadata={
                        "normalized_pointer": f"/talks/{ref_item.index}"
                    },
                )
                references_report.append(link.as_dict())

                # Per-talk report entry
                if ref_item.candidates:
                    for cand in ref_item.candidates:
                        projection, resolution = projected_candidates[
                            cand.candidate_spec.relative_path
                        ]
                        talk_entry = {
                            "pointer": ref_item.pointer,
                            "index": ref_item.index,
                            "namespace": ref_item.namespace,
                            "talk_id": ref_item.talk_id,
                            "result": link.result,
                            "candidate_count": len(ref_item.candidates),
                            "relative_path": cand.candidate_spec.relative_path,
                            "evidence_container_id": (
                                projection.evidence_container_id
                                if link.result == "evidence_only"
                                else None
                            ),
                            "candidate_container_ids": list(
                                link.candidate_container_ids
                            ),
                            "occurrence_count": len(projection.occurrence_ids),
                            "edge_count": len(projection.occurrence_edge_ids),
                            "resolved_text_count": resolution.resolved_count,
                            "counts_by_language": dict(resolution.counts_by_language),
                            "missing": resolution.missing_count,
                            "both_equal": resolution.both_equal_count,
                            "both_conflict": resolution.both_conflict_count,
                            "duplicate": resolution.duplicate_count,
                        }
                        talks_report.append(talk_entry)
                else:
                    talk_entry = {
                        "pointer": ref_item.pointer,
                        "index": ref_item.index,
                        "namespace": ref_item.namespace,
                        "talk_id": ref_item.talk_id,
                        "result": link.result,
                        "candidate_count": 0,
                        "relative_path": None,
                        "evidence_container_id": None,
                        "candidate_container_ids": [],
                        "occurrence_count": 0,
                        "edge_count": 0,
                        "resolved_text_count": 0,
                        "counts_by_language": {lang: 0 for lang in LANGUAGES},
                        "missing": 0,
                        "both_equal": 0,
                        "both_conflict": 0,
                        "duplicate": 0,
                    }
                    talks_report.append(talk_entry)

            db.ensure_ingest_run(
                preflight.ingest_run_id,
                snapshot_id=preflight.snapshot_id_value,
                parser_build_id=preflight.parser_build_id,
                scope={
                    "family": "quest_evidence",
                    "quest_ids": [QUEST_7008_ID],
                    "talk_ids": [int(r.talk_id) for r in preflight.references],
                    "selected_paths": preflight.selected_paths,
                },
                config={
                    "projection": "talk_occurrence",
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

            ambiguities_count = sum(
                1 for r in references_report if r["result"] == "ambiguous"
            )
            unresolved_count = sum(
                1 for r in references_report if r["result"] == "unresolved"
            )

            return {
                "status": "complete",
                "snapshot_id": preflight.snapshot_id_value,
                "ingest_run_id": preflight.ingest_run_id,
                "parser_build_id": preflight.parser_build_id,
                "quest_id": QUEST_7008_ID,
                "quest_document_id": preflight.quest_document_id,
                "candidate_bundle": {
                    "manifest_id": preflight.bundle_manifest.manifest_id,
                    "manifest_checksum": preflight.bundle_manifest.manifest_checksum,
                    "reference_count": preflight.bundle_manifest.reference_count,
                    "audit_cache_state": preflight.bundle_manifest.audit_cache_state,
                },
                "scope": {
                    "selected_path_count": len(preflight.selected_paths),
                    "selected_paths": preflight.selected_paths,
                    "full_scan": False,
                    "fts_rebuilt": False,
                    "embedding": False,
                },
                "references": references_report,
                "talks": talks_report,
                "text_hydration": {
                    "status": (
                        "complete"
                        if total_missing == 0
                        and total_both_conflict == 0
                        and total_duplicate == 0
                        else "partial"
                    ),
                    "languages": list(LANGUAGES),
                    "resolved_occurrence_text_count": total_resolved_text_count,
                    "counts_by_language": dict(counts_by_language),
                    "nonempty_count": total_nonempty_text_count,
                    "missing": total_missing,
                    "both_equal": total_both_equal,
                    "both_conflict": total_both_conflict,
                    "duplicate": total_duplicate,
                    "selected_content_hash_count": len(distinct_content_hashes),
                    "selected_textmap_source_file_count": len(shard_file_ids),
                    "selected_textmap_source_record_count": len(
                        registered_textmap_records
                    ),
                    "role_name_hydrated": False,
                    "old_run_preserved": old_run_preserved,
                },
                "ambiguities_count": ambiguities_count,
                "unresolved_count": unresolved_count,
                "legacy_writes": legacy_writes,
                "canary_700814": canary_700814,
            }
