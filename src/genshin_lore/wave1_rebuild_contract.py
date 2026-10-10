"""Builder-independent source and final-state contract for the Wave1 rebuild.

The contract proves exact final-state equivalence and preservation.  A
read-only SQLite file cannot prove the temporal history by which rows arrived.
No stage build report, receipt, or builder preflight is accepted here.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._quest_corpus_db_audit import audit_database_state
from ._quest_corpus_release_contract import CorpusExpectation, build_expectation
from .character_phase4c2_maintenance import (
    verify_character_phase4c2_database_contract,
)
from .character_phase4c2_release_contract import (
    Phase4C2ReleaseExpected,
    build_phase4c2_release_expected,
)
from .character_phase4c2_schema import verify_character_phase4c2_schema_signature
from .db import validate_exact_schema_structure
from .discovery import TalkDiscoveryIndex, load_talk_discovery_index_cache
from .parsers import deob_talk_generic
from .static_book1_verification_contract import (
    Book1Expectation,
    build_book1_expectation,
    verify_book1_database_contract,
)
from .static_phase4b2_maintenance import verify_phase4b2_database_contract
from .static_phase4b2_manifest import (
    StaticPhase4B2Manifest,
    load_static_phase4b2_manifest,
)
from .static_phase4b2_verification_contract import expected_phase4b2_identity
from .static_phase4b3_manifest import (
    StaticPhase4B3Manifest,
    load_static_phase4b3_manifest,
)
from .static_phase4b3_verification_contract import (
    expected_phase4b3_identity,
    verify_phase4b3_database_contract,
    verify_phase4b3_source_contract,
)
from .static_phase4b5_reliquary_manifest import (
    StaticPhase4B5Manifest,
    load_static_phase4b5_reliquary_manifest,
)
from .static_phase4b5_reliquary_verification_contract import (
    expected_phase4b5_reliquary_identity,
    verify_phase4b5_reliquary_database_contract,
    verify_phase4b5_reliquary_source_contract,
)
from .static_phase4b6_material_manifest import (
    StaticPhase4B6MaterialManifest,
    load_static_phase4b6_material_manifest,
)
from .static_phase4b6_material_verification_contract import (
    expected_phase4b6_material_identity,
    verify_phase4b6_material_database_contract,
    verify_phase4b6_material_source_contract,
)
from .static_book1_manifest import load_static_book1_source_manifest
from .talk_textmap import (
    LANGUAGES,
    parse_textmap_json_with_duplicates,
    resolve_talk_textmap,
)
from .source_record_contract import SourceRecordContract
from .util import canonical_json, digest_id, sha256_bytes, sha256_text


ANCESTRY_SEMANTICS = (
    "exact_final_state_equivalence_and_preservation_not_temporal_copy_history_proof"
)
SEALED_AQ_CACHE_SHA256 = (
    "0faedff4cc2a1074b3b5713e698990d630b6badb7362ae0d046a9db71c0ff890"
)
SEALED_AQ_CACHE_SIZE = 44_863_019
SEALED_AQ_CACHE_COUNTS = (55_925, 49_871, 6_054)


class Wave1RebuildContractError(RuntimeError):
    pass


def _fail(message: str) -> None:
    raise Wave1RebuildContractError(message)


@dataclass(frozen=True)
class ExpectedSourceFile:
    snapshot_id: str
    relative_path: str
    sha256: str
    size_bytes: int
    format: str
    upstream_blob_oid: str | None


@dataclass(frozen=True)
class Wave1RebuildExpectation:
    source_manifest: dict[str, Any]
    source_root: Path
    manifest_root: Path
    audit_cache_path: Path
    audit_cache: TalkDiscoveryIndex
    aq: CorpusExpectation
    book1: Book1Expectation
    phase4b2: StaticPhase4B2Manifest
    phase4b3: StaticPhase4B3Manifest
    phase4b5: StaticPhase4B5Manifest
    phase4b6: StaticPhase4B6MaterialManifest
    phase4c2: Phase4C2ReleaseExpected
    identities: dict[str, dict[str, Any]]
    source_files: dict[tuple[str, str], ExpectedSourceFile]
    source_audits: dict[str, Any]


def _verify_audit_cache(
    path: str | Path,
    aq: CorpusExpectation,
) -> tuple[Path, TalkDiscoveryIndex, dict[str, Any]]:
    cache_path = Path(path)
    if not cache_path.is_file() or cache_path.is_symlink():
        _fail(f"AQ audit cache is not a regular non-symlink file: {cache_path}")
    raw = cache_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if (
        digest != SEALED_AQ_CACHE_SHA256
        or digest != aq.audit_cache_sha256
        or len(raw) != SEALED_AQ_CACHE_SIZE
    ):
        _fail(
            "AQ audit cache exact-byte seal mismatch: "
            f"sha256={digest} size={len(raw)}"
        )
    index = load_talk_discovery_index_cache(
        cache_path,
        expected_sha256=digest,
        expected_snapshot=aq.manifest_set.corpus_manifest.source_snapshot,
    )
    counts = (
        index.candidate_file_count,
        index.valid_file_count,
        index.invalid_file_count,
    )
    if (
        counts != SEALED_AQ_CACHE_COUNTS
        or len(index.candidates) != SEALED_AQ_CACHE_COUNTS[1]
        or index.inventory_complete is not True
        or index.snapshot_id != aq.snapshot_id
    ):
        _fail(f"AQ audit cache inventory/snapshot mismatch: {counts}")
    return cache_path.resolve(), index, {
        "sha256": digest,
        "size_bytes": len(raw),
        "candidate_file_count": counts[0],
        "valid_file_count": counts[1],
        "invalid_file_count": counts[2],
        "inventory_complete": True,
        "snapshot_id": index.snapshot_id,
    }


def _assert_source_binding(source: dict[str, Any], stage: str, value: dict[str, Any]) -> None:
    for key in ("source_id", "release_label", "commit", "tree"):
        if str(value.get(key)) != str(source.get(key)):
            _fail(f"{stage} manifest source {key} mismatch")
    for key in ("upstream", "build"):
        if key in value and str(value[key]) != str(source.get(key)):
            _fail(f"{stage} manifest source {key} mismatch")


def _source_file_union(
    source: dict[str, Any],
    root: Path,
    aq: CorpusExpectation,
    audit_cache: TalkDiscoveryIndex,
    book1: Book1Expectation,
    b2: StaticPhase4B2Manifest,
    b3: StaticPhase4B3Manifest,
    b5: StaticPhase4B5Manifest,
    b6: StaticPhase4B6MaterialManifest,
    c2: Phase4C2ReleaseExpected,
    identities: dict[str, dict[str, Any]],
) -> tuple[dict[tuple[str, str], ExpectedSourceFile], dict[str, Any]]:
    union: dict[tuple[str, str], ExpectedSourceFile] = {}

    def add(
        snapshot_id: str,
        relative_path: str,
        sha256: str,
        size_bytes: int,
        format_value: str,
        blob_oid: str | None = None,
    ) -> None:
        key = (snapshot_id, relative_path)
        incoming = ExpectedSourceFile(
            snapshot_id,
            relative_path,
            sha256,
            int(size_bytes),
            format_value,
            blob_oid,
        )
        prior = union.get(key)
        if prior is None:
            union[key] = incoming
            return
        if (
            prior.sha256,
            prior.size_bytes,
            prior.format,
            prior.upstream_blob_oid,
        ) != (
            incoming.sha256,
            incoming.size_bytes,
            incoming.format,
            incoming.upstream_blob_oid,
        ):
            _fail(f"shared source_file immutable attributes conflict: {key}")

    legacy_snapshot = identities["aq"]["snapshot_id"]
    shared_snapshot = identities["phase4b5"]["snapshot_id"]
    candidates = {item.relative_path: item for item in audit_cache.candidates}
    for bundle in aq.manifest_set.quest_candidate_bundles:
        add(
            legacy_snapshot,
            bundle.quest.relative_path,
            bundle.quest.raw_sha256,
            bundle.quest.raw_size_bytes,
            "json",
        )
    for path in aq.talk_paths:
        candidate = candidates.get(path)
        if candidate is None:
            _fail(f"AQ audit cache omits selected Talk source: {path}")
        add(
            legacy_snapshot,
            path,
            candidate.raw_sha256,
            candidate.size_bytes,
            "json",
        )
    for item in aq.manifest_set.textmap_manifest.textmaps:
        add(
            legacy_snapshot,
            item.relative_path,
            item.raw_sha256,
            item.raw_size_bytes,
            "json",
        )
    for item in book1.manifest.files:
        add(legacy_snapshot, item.relative_path, item.sha256, item.size_bytes, item.format)
    for manifest in (b2, b3):
        for item in manifest.files:
            add(legacy_snapshot, item.relative_path, item.sha256, item.size_bytes, item.format)
    for manifest in (b5, b6):
        for item in manifest.files:
            add(shared_snapshot, item.relative_path, item.sha256, item.size_bytes, item.format)
    pre_c2_shared_paths = {
        (shared_snapshot, item.relative_path)
        for manifest in (b5, b6)
        for item in manifest.files
    }
    for item in c2.derived["files"]:
        key = (shared_snapshot, item["relative_path"])
        blob_oid = item.get("blob_oid")
        if key not in pre_c2_shared_paths and (
            not isinstance(blob_oid, str) or not blob_oid
        ):
            _fail(f"Phase 4C2-only source file lacks exact Git blob identity: {key}")
        add(
            shared_snapshot,
            item["relative_path"],
            item["sha256"],
            item["size_bytes"],
            item["format"],
            None if key in pre_c2_shared_paths else blob_oid,
        )

    total_bytes = 0
    for spec in union.values():
        path = root.joinpath(*spec.relative_path.split("/"))
        try:
            raw = path.read_bytes()
        except OSError as exc:
            _fail(f"cannot read sealed Wave1 source {spec.relative_path}: {exc}")
        if len(raw) != spec.size_bytes or sha256_bytes(raw) != spec.sha256:
            _fail(f"sealed Wave1 source mismatch: {spec.relative_path}")
        total_bytes += len(raw)
    return union, {
        "source_id": source["source_id"],
        "root": str(root),
        "exact_snapshot_path_count": len(union),
        "distinct_relative_path_count": len({item.relative_path for item in union.values()}),
        "verified_bytes": total_bytes,
    }


def build_wave1_rebuild_expectation(
    *,
    source_root: str | Path,
    source_manifest_path: str | Path,
    audit_cache_path: str | Path,
    manifest_root: str | Path,
    corpus_manifest_path: str | Path,
    book1_manifest_path: str | Path,
    phase4b2_manifest_path: str | Path,
    phase4b3_manifest_path: str | Path,
    phase4b5_manifest_path: str | Path,
    phase4b6_manifest_path: str | Path,
    phase4c2_manifest_path: str | Path,
) -> Wave1RebuildExpectation:
    root = Path(source_root).resolve()
    if not root.is_dir():
        _fail(f"Wave1 source root is not a directory: {root}")
    source = load_static_book1_source_manifest(source_manifest_path)
    aq = build_expectation(Path(corpus_manifest_path), manifest_root)
    if aq.source_manifest != source:
        _fail("AQ manifest-root source manifest differs from the supplied sealed source manifest")
    cache_path, audit_cache, cache_audit = _verify_audit_cache(audit_cache_path, aq)
    book1 = build_book1_expectation(source_manifest_path, book1_manifest_path, root)
    b2 = load_static_phase4b2_manifest(phase4b2_manifest_path)
    b3 = load_static_phase4b3_manifest(phase4b3_manifest_path)
    b5 = load_static_phase4b5_reliquary_manifest(phase4b5_manifest_path)
    b6 = load_static_phase4b6_material_manifest(phase4b6_manifest_path)
    for name, manifest in (
        ("phase4b2", b2),
        ("phase4b3", b3),
        ("phase4b5", b5),
        ("phase4b6", b6),
    ):
        _assert_source_binding(source, name, manifest.source)

    source_audits = {
        "aq_cache": cache_audit,
        "book1": {"selected_files": len(book1.files), "root": str(root)},
        "phase4b3": verify_phase4b3_source_contract(root, b3),
        "phase4b5": verify_phase4b5_reliquary_source_contract(root, b5),
        "phase4b6": verify_phase4b6_material_source_contract(root, b6),
    }
    c2 = build_phase4c2_release_expected(root, phase4c2_manifest_path)
    _assert_source_binding(source, "phase4c2", c2.manifest.source)

    identities = {
        "aq": {
            "snapshot_id": aq.snapshot_id,
            "parser_build_id": aq.parser_build_id,
            "ingest_run_id": aq.ingest_run_id,
            "config_checksum": aq.config_checksum,
        },
        "book1": {
            "snapshot_id": book1.snapshot_id,
            "parser_build_id": book1.parser_build_id,
            "ingest_run_id": book1.ingest_run_id,
            "config_checksum": book1.config_checksum,
        },
        "phase4b2": expected_phase4b2_identity(b2),
        "phase4b3": expected_phase4b3_identity(b3),
        "phase4b5": expected_phase4b5_reliquary_identity(b5),
        "phase4b6": expected_phase4b6_material_identity(b6),
        "phase4c2": {
            "snapshot_id": c2.snapshot_id,
            "parser_build_id": c2.parser_build_id,
            "ingest_run_id": c2.ingest_run_id,
            "config_checksum": c2.config_checksum,
        },
    }
    legacy = {identities[name]["snapshot_id"] for name in ("aq", "book1", "phase4b2", "phase4b3")}
    shared = {identities[name]["snapshot_id"] for name in ("phase4b5", "phase4b6", "phase4c2")}
    if len(legacy) != 1 or len(shared) != 1 or legacy == shared:
        _fail(f"Wave1 legacy/shared snapshot topology mismatch: legacy={legacy} shared={shared}")
    if len({item["parser_build_id"] for item in identities.values()}) != 7:
        _fail("Wave1 parser build IDs are not exactly seven unique values")
    if len({item["ingest_run_id"] for item in identities.values()}) != 7:
        _fail("Wave1 ingest run IDs are not exactly seven unique values")

    source_files, union_audit = _source_file_union(
        source, root, aq, audit_cache, book1, b2, b3, b5, b6, c2, identities
    )
    source_audits["exact_union"] = union_audit
    return Wave1RebuildExpectation(
        source,
        root,
        Path(manifest_root).resolve(),
        cache_path,
        audit_cache,
        aq,
        book1,
        b2,
        b3,
        b5,
        b6,
        c2,
        identities,
        source_files,
        source_audits,
    )


def _verify_global_identity_sets(
    conn: sqlite3.Connection,
    expected: Wave1RebuildExpectation,
) -> dict[str, Any]:
    parser_ids = [str(row[0]) for row in conn.execute("SELECT id FROM parser_build ORDER BY id")]
    run_ids = [str(row[0]) for row in conn.execute("SELECT id FROM ingest_run ORDER BY id")]
    wanted_parsers = sorted(item["parser_build_id"] for item in expected.identities.values())
    wanted_runs = sorted(item["ingest_run_id"] for item in expected.identities.values())
    if parser_ids != wanted_parsers:
        _fail(f"Wave1 parser_build set mismatch: {parser_ids}")
    if run_ids != wanted_runs:
        _fail(f"Wave1 ingest_run set mismatch: {run_ids}")

    source = expected.source_manifest
    catalogs = [tuple(row) for row in conn.execute(
        "SELECT id,kind,url,license_note FROM source_catalog ORDER BY id"
    )]
    if catalogs != [(source["source_id"], "primary", source["upstream"], None)]:
        _fail(f"Wave1 source_catalog exact set mismatch: {catalogs}")

    legacy_id = expected.identities["aq"]["snapshot_id"]
    shared_id = expected.identities["phase4b5"]["snapshot_id"]
    snapshots = conn.execute(
        'SELECT id,source_id,release_label,build,"commit",tree,observed_at,'
        "parser_version,mapping_version,status,metadata_json "
        "FROM source_snapshot ORDER BY id"
    ).fetchall()
    if [str(row["id"]) for row in snapshots] != sorted((legacy_id, shared_id)):
        _fail("Wave1 source_snapshot exact two-row set mismatch")
    # These two columns predate parser_build/ingest_run.  Freeze their exact
    # first-writer values only as legacy compatibility initialization
    # metadata; they are not the logical snapshot identity and they do not
    # identify any later family parser.
    initialization_pairs = {
        legacy_id: ("quest-corpus-evidence-v1", "quest-corpus-evidence-v1"),
        shared_id: (
            expected.phase4b5.versions["parser"],
            expected.phase4b5.versions["projection"],
        ),
    }
    legacy_columns: dict[str, list[str]] = {}
    for row in snapshots:
        snapshot_id = str(row["id"])
        metadata = (
            canonical_json({
                "scope": "AQ 7000-7014 sealed quest evidence corpus",
                "corpus_manifest_id": expected.aq.manifest_set.corpus_manifest.manifest_id,
            })
            if snapshot_id == legacy_id
            else canonical_json({"sealed_source_manifest": True})
        )
        actual_core = tuple(row[key] for key in (
            "source_id", "release_label", "build", "commit", "tree",
            "observed_at", "status", "metadata_json",
        ))
        expected_core = (
            source["source_id"], source["release_label"], source["build"],
            source["commit"], source["tree"], source["observed_at"],
            "active", metadata,
        )
        if actual_core != expected_core:
            _fail(f"Wave1 source_snapshot raw-source binding mismatch: {snapshot_id}")
        parser_pair = (str(row["parser_version"]), str(row["mapping_version"]))
        if parser_pair != initialization_pairs[snapshot_id]:
            _fail(
                "Wave1 source_snapshot legacy compatibility initialization "
                f"metadata mismatch: {snapshot_id} {parser_pair}"
            )
        legacy_columns[snapshot_id] = list(parser_pair)
    return {
        "parser_build_ids": parser_ids,
        "ingest_run_ids": run_ids,
        "snapshot_ids": [legacy_id, shared_id],
        "snapshot_legacy_compatibility_initialization_metadata": legacy_columns,
        "snapshot_legacy_columns_are_identity": False,
        "snapshot_parser_identity_source": "parser_build_and_ingest_run",
    }


def _verify_source_file_union(
    conn: sqlite3.Connection,
    expected: Wave1RebuildExpectation,
) -> dict[str, int]:
    rows = conn.execute(
        "SELECT id,snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path "
        "FROM source_file ORDER BY snapshot_id,relative_path"
    ).fetchall()
    actual_keys = {(str(row[1]), str(row[2])) for row in rows}
    if actual_keys != set(expected.source_files):
        _fail(
            "Wave1 source_file exact union mismatch: "
            f"missing={len(set(expected.source_files) - actual_keys)} "
            f"extra={len(actual_keys - set(expected.source_files))}"
        )
    expected_root = os.path.normcase(str(expected.source_root))
    for row in rows:
        key = (str(row[1]), str(row[2]))
        spec = expected.source_files[key]
        wanted_id = digest_id("sf", spec.snapshot_id, spec.relative_path)
        actual_root = os.path.normcase(str(Path(str(row[7])).resolve(strict=False)))
        if (
            str(row[0]) != wanted_id
            or row[3] != spec.upstream_blob_oid
            or str(row[4]) != spec.sha256
            or int(row[5]) != spec.size_bytes
            or str(row[6]) != spec.format
            or actual_root != expected_root
        ):
            _fail(f"Wave1 source_file immutable attribute mismatch: {key}")
    return {"source_files": len(rows)}


def _verify_run_ownership(conn: sqlite3.Connection, expected: Wave1RebuildExpectation) -> dict[str, Any]:
    ids = expected.identities
    aq_run = ids["aq"]["ingest_run_id"]
    static_runs = {
        ids[name]["ingest_run_id"]
        for name in ("book1", "phase4b2", "phase4b3", "phase4b5", "phase4b6")
    }
    character_run = ids["phase4c2"]["ingest_run_id"]
    families = {
        "aq": (
            aq_run,
            (
                "evidence_container", "identity_group", "identity_group_container",
                "passage_occurrence", "passage_occurrence_text", "occurrence_edge",
                "candidate_scope_proof", "document_evidence",
            ),
        ),
        "character": (
            character_run,
            (
                "character_row_occurrence", "character_raw_slot_occurrence",
                "character_relation_occurrence", "character_text_resolution_occurrence",
                "character_text_candidate_occurrence", "character_voice_container_occurrence",
                "character_audio_variant_occurrence",
            ),
        ),
    }
    counts: dict[str, dict[str, int]] = {}
    for family, (run_id, tables) in families.items():
        counts[family] = {}
        for table in tables:
            wrong = int(conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE ingest_run_id<>?",
                (run_id,),
            ).fetchone()[0])
            if wrong:
                _fail(f"Wave1 {table} contains {wrong} cross-family run rows")
            counts[family][table] = int(conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0])
    static_tables = (
        "static_evidence_container", "static_segment_occurrence",
        "static_relationship_occurrence", "static_text_resolution_occurrence",
        "static_text_candidate_occurrence",
    )
    counts["static"] = {}
    placeholders = ",".join("?" for _ in static_runs)
    for table in static_tables:
        wrong = int(conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE ingest_run_id NOT IN ({placeholders})",
            tuple(sorted(static_runs)),
        ).fetchone()[0])
        if wrong:
            _fail(f"Wave1 {table} contains {wrong} non-static run rows")
        counts["static"][table] = int(conn.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0])
    cross_run_targets = int(conn.execute(
        "SELECT COUNT(*) FROM static_relationship_occurrence r "
        "JOIN static_evidence_container c ON c.id=r.target_evidence_container_id "
        "WHERE r.target_evidence_container_id IS NOT NULL "
        "AND r.ingest_run_id<>c.ingest_run_id"
    ).fetchone()[0])
    if cross_run_targets:
        _fail(f"Wave1 static relationships cross ingest runs: {cross_run_targets}")
    logical_leaks = {
        "talk_documents": int(conn.execute(
            "SELECT COUNT(*) FROM document WHERE content_type='talk'"
        ).fetchone()[0]),
        "logical_talk": int(conn.execute("SELECT COUNT(*) FROM logical_talk").fetchone()[0]),
        "talk_evidence": int(conn.execute("SELECT COUNT(*) FROM talk_evidence").fetchone()[0]),
        "logical_talk_evidence": int(conn.execute(
            "SELECT COUNT(*) FROM logical_talk_evidence"
        ).fetchone()[0]),
        "passage": int(conn.execute("SELECT COUNT(*) FROM passage").fetchone()[0]),
        "passage_version": int(conn.execute(
            "SELECT COUNT(*) FROM passage_version"
        ).fetchone()[0]),
        "localized_text": int(conn.execute(
            "SELECT COUNT(*) FROM localized_text"
        ).fetchone()[0]),
        "passage_text": int(conn.execute("SELECT COUNT(*) FROM passage_text").fetchone()[0]),
        "document_relation": int(conn.execute(
            "SELECT COUNT(*) FROM document_relation"
        ).fetchone()[0]),
        "passage_edge": int(conn.execute("SELECT COUNT(*) FROM passage_edge").fetchone()[0]),
    }
    if any(logical_leaks.values()):
        _fail(f"Wave1 logical Talk/passage promotion leak: {logical_leaks}")
    return {
        "table_counts": counts,
        "cross_run_static_targets": 0,
        "logical_talk_passage_leaks": logical_leaks,
    }


def _json_pointer(value: Any, pointer: str) -> Any:
    if pointer == "/":
        return value
    if not pointer.startswith("/"):
        _fail(f"non-canonical JSON pointer in source_record contract: {pointer}")
    current = value
    for encoded in pointer[1:].split("/"):
        part = encoded.replace("~1", "/").replace("~0", "~")
        try:
            current = current[int(part)] if isinstance(current, list) else current[part]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            _fail(f"source_record pointer is absent from sealed source: {pointer}: {exc}")
    return current


def _aq_source_record_contracts(
    conn: sqlite3.Connection,
    expected: Wave1RebuildExpectation,
) -> dict[str, SourceRecordContract]:
    """Rebuild AQ record rows from sealed source bytes and exact AQ coordinates."""

    result: dict[str, SourceRecordContract] = {}
    parsed: dict[str, Any] = {}
    raw_bytes: dict[str, bytes] = {}
    raw_source_text: dict[str, str] = {}

    def source_json(relative_path: str) -> Any:
        if relative_path not in parsed:
            path = expected.source_root.joinpath(*relative_path.split("/"))
            try:
                raw = path.read_bytes()
                raw_bytes[relative_path] = raw
                raw_source_text[relative_path] = raw.decode("utf-8")
                parsed[relative_path] = json.loads(raw.decode("utf-8-sig"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                _fail(f"cannot parse AQ sealed source {relative_path}: {exc}")
        return parsed[relative_path]

    def add(
        relative_path: str,
        pointer: str,
        record_kind: str,
        native_id: str | int | None,
        raw_value: Any,
        record_json: str,
        *,
        checksum_text: str | None = None,
    ) -> None:
        file_id = digest_id("sf", expected.aq.snapshot_id, relative_path)
        record_id = digest_id("sr", file_id, pointer)
        checksum = sha256_text(
            checksum_text
            if checksum_text is not None
            else raw_value if isinstance(raw_value, str) else canonical_json(raw_value)
        )
        incoming = SourceRecordContract(
            record_id,
            file_id,
            pointer,
            record_kind,
            None if native_id is None else str(native_id),
            checksum,
            record_json,
        )
        prior = result.setdefault(record_id, incoming)
        if prior != incoming:
            _fail(f"AQ source_record contract conflict: {record_id}")

    for bundle in expected.aq.manifest_set.quest_candidate_bundles:
        relative_path = bundle.quest.relative_path
        document = source_json(relative_path)
        add(
            relative_path,
            "/",
            "quest_root",
            bundle.quest.quest_id,
            document,
            canonical_json(document),
        )
    for reference in expected.aq.references:
        pointer = f"/OJACLOOEAMG/{reference.index}"
        raw_reference = _json_pointer(source_json(reference.quest_path), pointer)
        add(
            reference.quest_path,
            pointer,
            "quest_talk_reference",
            reference.talk_id,
            raw_reference,
            canonical_json(raw_reference),
        )
    for relative_path in expected.aq.talk_paths:
        document = source_json(relative_path)
        text = raw_source_text[relative_path]
        add(
            relative_path,
            "/",
            "talk_root",
            expected.aq.candidate_talk_id_by_path[relative_path],
            document,
            text,
            checksum_text=text,
        )

    main_shards: dict[str, tuple[dict[str, str], set[str]]] = {}
    medium_shards: dict[str, tuple[dict[str, str], set[str]]] = {}
    for item in expected.aq.manifest_set.textmap_manifest.textmaps:
        source_json(item.relative_path)
        try:
            mapping, duplicates = parse_textmap_json_with_duplicates(
                raw_bytes[item.relative_path]
            )
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            _fail(f"cannot independently parse AQ TextMap {item.relative_path}: {exc}")
        target = main_shards if item.tier == "main" else medium_shards
        target[item.language] = (mapping, duplicates)

    expected_hydration: list[tuple[Any, ...]] = []
    resolved_count = 0
    missing_count = 0
    conflict_count = 0
    duplicate_count = 0
    for talk_path in expected.aq.talk_paths:
        expected_talk_id = int(expected.aq.candidate_talk_id_by_path[talk_path])
        try:
            talk = deob_talk_generic(
                source_json(talk_path), expected_talk_id=expected_talk_id
            )
            resolution = resolve_talk_textmap(
                dialog_list=talk["dialogList"],
                textmap_shards=main_shards,
                medium_shards=medium_shards,
                languages=LANGUAGES,
            )
        except (KeyError, TypeError, ValueError) as exc:
            _fail(f"cannot independently resolve AQ Talk hydration {talk_path}: {exc}")
        resolved_count += resolution.resolved_count
        missing_count += resolution.missing_count
        conflict_count += resolution.both_conflict_count
        duplicate_count += resolution.duplicate_count

        talk_file_id = digest_id("sf", expected.aq.snapshot_id, talk_path)
        talk_record_id = digest_id("sr", talk_file_id, "/")
        container_id = digest_id(
            "ec", expected.aq.ingest_run_id, talk_record_id
        )
        for item in resolution.node_texts:
            for candidate in item.candidates:
                payload = {"hash": item.textmap_hash, "value": candidate.raw_text}
                add(
                    candidate.relative_path,
                    candidate.pointer,
                    "textmap_entry",
                    item.textmap_hash,
                    payload,
                    canonical_json(payload),
                )
            if (
                item.status not in ("unique_main", "unique_medium", "both_equal")
                or not item.raw_text
            ):
                continue
            if item.relative_path is None or item.pointer is None:
                _fail(f"AQ resolved hydration lacks source coordinate: {talk_path}")
            text_file_id = digest_id(
                "sf", expected.aq.snapshot_id, item.relative_path
            )
            text_record_id = digest_id("sr", text_file_id, item.pointer)
            occurrence_id = digest_id(
                "po", expected.aq.ingest_run_id, container_id, item.node_id
            )
            text_id = digest_id(
                "pot", occurrence_id, expected.aq.ingest_run_id,
                container_id, item.language,
            )
            expected_hydration.append((
                text_id,
                occurrence_id,
                expected.aq.ingest_run_id,
                container_id,
                item.language,
                item.raw_text,
                item.normalized_text,
                sha256_text(item.raw_text),
                sha256_text(item.normalized_text),
                text_record_id,
                None,
                None,
                talk_path,
                item.node_id,
                f"{item.dialog_index:04d}",
                item.relative_path,
                item.pointer,
                item.textmap_hash,
            ))

    if (
        resolved_count != 9931
        or len(expected_hydration) != 9931
        or missing_count != 50
        or conflict_count != 0
        or duplicate_count != 0
    ):
        _fail(
            "AQ independent hydration source contract mismatch: "
            f"resolved={resolved_count} rows={len(expected_hydration)} "
            f"missing={missing_count} conflicts={conflict_count} "
            f"duplicates={duplicate_count}"
        )
    actual_hydration = [
        tuple(row)
        for row in conn.execute(
            "SELECT pot.id,pot.passage_occurrence_id,pot.ingest_run_id,"
            "pot.evidence_container_id,pot.language,pot.raw_text,"
            "pot.normalized_text,pot.raw_checksum,pot.normalized_checksum,"
            "pot.source_record_id,pot.source_start,pot.source_end,"
            "talk_sf.relative_path,po.node_id,po.sequence_key,"
            "text_sf.relative_path,text_sr.pointer,text_sr.native_id "
            "FROM passage_occurrence_text pot "
            "JOIN passage_occurrence po "
            "ON po.passage_occurrence_id=pot.passage_occurrence_id "
            "JOIN source_record talk_sr ON talk_sr.id=po.source_record_id "
            "JOIN source_file talk_sf ON talk_sf.id=talk_sr.source_file_id "
            "JOIN source_record text_sr ON text_sr.id=pot.source_record_id "
            "JOIN source_file text_sf ON text_sf.id=text_sr.source_file_id "
            "WHERE pot.ingest_run_id=? ORDER BY pot.id",
            (expected.aq.ingest_run_id,),
        )
    ]
    if actual_hydration != sorted(expected_hydration):
        _fail(
            "AQ 9931-row hydration coordinate/text/checksum contract mismatch: "
            f"actual={len(actual_hydration)} expected={len(expected_hydration)}"
        )

    owned_ids: set[str] = set()
    quest_document_ids = tuple(f"agd:quest:{item}" for item in expected.aq.quest_ids)
    placeholders = ",".join("?" for _ in quest_document_ids)
    owned_ids.update(
        str(row[0])
        for row in conn.execute(
            f"SELECT source_record_id FROM document_version "
            f"WHERE document_id IN ({placeholders}) AND snapshot_id=?",
            (*quest_document_ids, expected.aq.snapshot_id),
        )
    )
    for table in (
        "evidence_container",
        "passage_occurrence",
        "passage_occurrence_text",
        "document_evidence",
    ):
        owned_ids.update(
            str(row[0])
            for row in conn.execute(
                f"SELECT DISTINCT source_record_id FROM {table} "
                "WHERE ingest_run_id=? AND source_record_id IS NOT NULL",
                (expected.aq.ingest_run_id,),
            )
        )
    if owned_ids != set(result):
        _fail(
            "AQ exact source_record coordinate set mismatch: "
            f"missing={len(set(result) - owned_ids)} extra={len(owned_ids - set(result))}"
        )
    return result


def _book1_source_record_contracts(
    expected: Wave1RebuildExpectation,
) -> dict[str, SourceRecordContract]:
    specs = {item.relative_path: item for item in expected.book1.manifest.files}
    result: dict[str, SourceRecordContract] = {}
    for item in expected.book1.files:
        spec = specs[item.relative_path]
        result[item.source_record_id] = SourceRecordContract(
            item.source_record_id,
            item.source_file_id,
            str(spec.record["pointer"]),
            str(spec.record["record_kind"]),
            None if spec.record["native_id"] is None else str(spec.record["native_id"]),
            sha256_text(
                item.record
                if isinstance(item.record, str)
                else canonical_json(item.record)
            ),
            item.record_json,
        )
    return result


def _phase4c2_source_record_contracts(
    expected: Wave1RebuildExpectation,
) -> dict[str, SourceRecordContract]:
    result: dict[str, SourceRecordContract] = {}
    for item in expected.phase4c2.derived["source_records"]:
        file_id = digest_id(
            "sf", expected.phase4c2.snapshot_id, item["relative_path"]
        )
        record_id = digest_id("sr", file_id, item["source_pointer"])
        incoming = SourceRecordContract(
            record_id,
            file_id,
            item["source_pointer"],
            item["record_kind"],
            None if item["native_id"] is None else str(item["native_id"]),
            item["raw_checksum"],
            canonical_json(item["raw_value"]),
        )
        prior = result.setdefault(record_id, incoming)
        if prior != incoming:
            _fail(f"Phase 4C2 source_record contract conflict: {record_id}")
    return result


def _merge_source_record_contracts(
    target: dict[str, SourceRecordContract],
    owners: dict[str, set[str]],
    stage: str,
    incoming: Mapping[str, SourceRecordContract],
) -> None:
    for record_id, contract in incoming.items():
        if record_id != contract.id:
            _fail(f"{stage} source_record map key mismatch: {record_id}")
        prior = target.setdefault(record_id, contract)
        if prior != contract:
            _fail(f"shared source_record immutable attributes conflict: {record_id}")
        owners.setdefault(record_id, set()).add(stage)


def _verify_source_record_union(
    conn: sqlite3.Connection,
    stage_records: Mapping[str, Mapping[str, SourceRecordContract]],
) -> dict[str, Any]:
    expected: dict[str, SourceRecordContract] = {}
    owners: dict[str, set[str]] = {}
    for stage, records in stage_records.items():
        _merge_source_record_contracts(expected, owners, stage, records)
    actual: dict[str, SourceRecordContract] = {}
    for row in conn.execute(
        "SELECT id,source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json "
        "FROM source_record ORDER BY id"
    ):
        contract = SourceRecordContract.from_row(row)
        actual[contract.id] = contract
    missing = set(expected) - set(actual)
    extra = set(actual) - set(expected)
    mismatched = {
        record_id
        for record_id in set(expected) & set(actual)
        if expected[record_id] != actual[record_id]
    }
    if missing or extra or mismatched:
        _fail(
            "Wave1 source_record exact full-row union mismatch: "
            f"missing={len(missing)} extra={len(extra)} mismatched={len(mismatched)}"
        )
    digest = sha256_text(canonical_json(sorted(
        (item.as_dict() for item in expected.values()),
        key=lambda item: item["id"],
    )))
    return {
        "source_records": len(expected),
        "full_row_sha256": digest,
        "shared_record_count": sum(len(value) > 1 for value in owners.values()),
        "stage_record_counts": {
            stage: len(records) for stage, records in stage_records.items()
        },
        "missing": 0,
        "extra": 0,
        "mismatched": 0,
        "universal_business_consumer_required": False,
    }


def _manifest_counts(manifest: Any) -> dict[str, int]:
    counts = manifest.expected["counts"]
    normalized = {"files": int(counts["selected_files"])}
    for key in (
        "source_records", "containers", "segments", "relationships",
        "text_resolutions", "text_candidates", "text_assets",
        "text_owner_occurrences",
    ):
        if key in counts:
            normalized[key] = int(counts[key])
    return normalized


def _stage_contract_spec(
    expected: Wave1RebuildExpectation,
    stage: str,
) -> dict[str, Any]:
    common_checks = [
        "schema", "source_binding", "run_binding", "exact_counts",
        "full_rows", "occurrence_digests", "logical_boundary",
        "foreign_keys",
    ]
    if stage == "aq":
        manifest = expected.aq.manifest_set.corpus_manifest
        counts = {
            "quest_count": len(expected.aq.quest_ids),
            "evidence_container_count": len(expected.aq.talk_paths),
            "occurrence_count": 3327,
            "occurrence_edge_count": 3312,
            "document_evidence_count": len(expected.aq.references),
            "resolved_occurrence_text_count": 9931,
        }
        manifest_sha256 = expected.aq.master_manifest_sha256
        checks = [
            "schema", "source_binding", "run_binding", "exact_counts",
            "graph_projection", "text_hydration", "quest_documents",
            "logical_boundary", "foreign_keys",
        ]
        required_outputs = (
            "schema_version", "counts", "graph_projection",
            "text_hydration", "run", "manifest", "foreign_key_check",
        )
    elif stage == "book1":
        manifest = expected.book1.manifest
        counts = {"containers": 5, "segments": 57, "relationships": 6}
        manifest_sha256 = manifest.raw_sha256
        checks = [
            "schema", "source_binding", "run_binding", "exact_counts",
            "full_rows", "logical_boundary", "foreign_keys",
        ]
        required_outputs = (
            "schema_version", "identity", "counts", "locale_counts",
            "logical_leak_counts", "foreign_key_check",
        )
    elif stage == "phase4c2":
        manifest = expected.phase4c2.manifest
        counts = {
            key: int(value)
            for key, value in expected.phase4c2.derived["counts"].items()
        }
        manifest_sha256 = manifest.raw_sha256
        checks = common_checks
        required_outputs = (
            "schema_version", "schema_signature", "counts",
            "status_counts", "table_row_counts", "occurrence_set_digests",
            "foreign_key_check", "write_once_triggers",
        )
    else:
        manifest = getattr(expected, stage)
        counts = _manifest_counts(manifest)
        manifest_sha256 = manifest.raw_sha256
        checks = (
            [
                "schema", "source_binding", "run_binding", "exact_counts",
                "full_rows", "text_provenance", "logical_boundary",
                "foreign_keys",
            ]
            if stage == "phase4b2"
            else common_checks
        )
        required_outputs = (
            (
                "schema_version", "run", "counts", "family_counts",
                "resolution_statuses", "text_provenance", "canaries",
                "occurrence_coverage", "schema_coverage", "foreign_key_check",
            )
            if stage == "phase4b2"
            else (
                "schema_version", "identity", "counts", "family_counts",
                "family_selected_paths", "status_counts",
                "occurrence_set_digests", "foreign_key_check",
            )
        )
    identity = expected.identities[stage]
    return {
        "protocol": "wave1-stage-final-state-contract-v1",
        "contract_id": (
            f"wave1-stage-final-state-v1:{stage}:{manifest.manifest_id}"
        ),
        "status": "pass",
        "run_id": identity["ingest_run_id"],
        "parser_build_id": identity["parser_build_id"],
        "manifest_sha256": manifest_sha256,
        "exact_counts": counts,
        "required_checks": checks,
        "required_outputs": required_outputs,
    }


def _run_stage_contract(
    stage: str,
    verifier: Callable[[], Mapping[str, Any] | None],
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    result = verifier()
    if not isinstance(result, Mapping) or not result:
        _fail(f"Wave1 {stage} verifier returned no non-empty Mapping contract")
    for key in (
        "protocol", "contract_id", "status", "run_id", "parser_build_id",
        "manifest_sha256", "exact_counts", "required_checks",
    ):
        if key not in result or result[key] != spec[key]:
            _fail(
                f"Wave1 {stage} stage-contract protocol mismatch for {key}: "
                f"{result.get(key)!r}"
            )
    missing_outputs = [
        key for key in spec["required_outputs"] if key not in result
    ]
    if missing_outputs:
        _fail(f"Wave1 {stage} stage-contract output is incomplete: {missing_outputs}")
    if result["schema_version"] != 10:
        _fail(f"Wave1 {stage} stage-contract schema_version is not exactly 10")
    if stage == "aq":
        for key, value in spec["exact_counts"].items():
            actual = (
                result["text_hydration"].get(key)
                if key == "resolved_occurrence_text_count"
                else result["counts"].get(key)
            )
            if actual != value:
                _fail(f"Wave1 AQ raw count mismatch for {key}: {actual!r}")
        if result["foreign_key_check"] != {"ok": True, "violations": []}:
            _fail("Wave1 AQ stage-contract foreign-key output mismatch")
    else:
        if result["counts"] != spec["exact_counts"]:
            _fail(f"Wave1 {stage} raw count output mismatch")
        if result["foreign_key_check"] != []:
            _fail(f"Wave1 {stage} stage-contract foreign-key output mismatch")
    return {
        key: result[key]
        for key in (
            "protocol", "contract_id", "status", "run_id", "parser_build_id",
            "manifest_sha256", "exact_counts", "required_checks",
        )
    } | {"contract": dict(result)}


def verify_wave1_database_contract(
    conn: sqlite3.Connection,
    expected: Wave1RebuildExpectation,
) -> dict[str, Any]:
    versions = [int(row[0]) for row in conn.execute(
        "SELECT version FROM schema_migration ORDER BY version"
    )]
    if versions != list(range(1, 11)):
        _fail(f"Wave1 final schema ledger must be exactly 1..10: {versions}")
    validate_exact_schema_structure(conn, 10)
    schema_signature = verify_character_phase4c2_schema_signature(conn)
    identities = _verify_global_identity_sets(conn, expected)

    stage_contracts: dict[str, Any] = {}
    stage_records: dict[str, dict[str, SourceRecordContract]] = {}
    stage_contracts["aq"] = _run_stage_contract(
        "aq",
        lambda: audit_database_state(conn, expected.aq, schema_version=10),
        _stage_contract_spec(expected, "aq"),
    )
    stage_records["aq"] = _aq_source_record_contracts(conn, expected)
    stage_contracts["book1"] = _run_stage_contract(
        "book1",
        lambda: verify_book1_database_contract(
            conn, expected.book1, stage_exclusive=False
        ),
        _stage_contract_spec(expected, "book1"),
    )
    stage_records["book1"] = _book1_source_record_contracts(expected)
    stage_records["phase4b2"] = {}
    stage_contracts["phase4b2"] = _run_stage_contract(
        "phase4b2",
        lambda: verify_phase4b2_database_contract(
            conn,
            expected.phase4b2,
            validated_source_records=stage_records["phase4b2"],
        ),
        _stage_contract_spec(expected, "phase4b2"),
    )
    stage_records["phase4b3"] = {}
    stage_contracts["phase4b3"] = _run_stage_contract(
        "phase4b3",
        lambda: verify_phase4b3_database_contract(
            conn,
            expected.phase4b3,
            expected.source_root,
            validated_source_records=stage_records["phase4b3"],
        ),
        _stage_contract_spec(expected, "phase4b3"),
    )
    stage_records["phase4b5"] = {}
    stage_contracts["phase4b5"] = _run_stage_contract(
        "phase4b5",
        lambda: verify_phase4b5_reliquary_database_contract(
            conn,
            expected.phase4b5,
            expected.source_root,
            validated_source_records=stage_records["phase4b5"],
        ),
        _stage_contract_spec(expected, "phase4b5"),
    )
    stage_records["phase4b6"] = {}
    stage_contracts["phase4b6"] = _run_stage_contract(
        "phase4b6",
        lambda: verify_phase4b6_material_database_contract(
            conn,
            expected.phase4b6,
            expected.source_root,
            validated_source_records=stage_records["phase4b6"],
        ),
        _stage_contract_spec(expected, "phase4b6"),
    )
    stage_contracts["phase4c2"] = _run_stage_contract(
        "phase4c2",
        lambda: verify_character_phase4c2_database_contract(
            conn, expected.phase4c2
        ),
        _stage_contract_spec(expected, "phase4c2"),
    )
    stage_records["phase4c2"] = _phase4c2_source_record_contracts(expected)

    source_files = _verify_source_file_union(conn, expected)
    ownership = _verify_run_ownership(conn, expected)
    records = _verify_source_record_union(conn, stage_records)
    foreign_keys = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
    if foreign_keys:
        _fail(f"Wave1 foreign key violations: {foreign_keys[:3]}")
    return {
        "schema_version": 10,
        "schema_signature": schema_signature,
        "ancestry_semantics": ANCESTRY_SEMANTICS,
        "temporal_copy_history_proven": False,
        "identity_sets": identities,
        "source_file_union": source_files,
        "source_record_union": records,
        "run_ownership": ownership,
        "stage_contracts": stage_contracts,
        "foreign_key_check": [],
    }


__all__ = [
    "ANCESTRY_SEMANTICS",
    "Wave1RebuildContractError",
    "Wave1RebuildExpectation",
    "build_wave1_rebuild_expectation",
    "verify_wave1_database_contract",
]
