"""Read-only facts and release checks for the sealed AQ corpus database.

The caller owns the connection lifecycle.  Every function here operates on
an already-open SQLite connection supplied by the existing MCP read-only seam;
this module never initializes, migrates, or writes a database.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from .db import validate_exact_schema_structure
from ._quest_corpus_release_contract import (
    AQ_MASTER_MANIFEST_ID,
    AQ_RELEASE_COUNTS,
    LANGUAGES,
    CorpusExpectation,
    build_identity_scope_expectation,
    parser_fingerprint,
    is_int,
    require_mapping,
    require_report_value,
    verification_error,
)
from .quest_corpus_evidence import (
    MAPPING_VERSION_CORPUS,
    PARSER_VERSION_CORPUS,
    PROJECTION_VERSION_CORPUS,
)
from .discovery import DISCOVERY_VERSION
from .mapping import MAPPING_VERSION
from .parsers import deob_talk_generic
from .util import canonical_json, digest_id, sha256_text


def _json_column(value: Any, label: str) -> Any:
    if not isinstance(value, str):
        raise verification_error(f"{label} is not JSON text")
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise verification_error(f"{label} contains malformed JSON: {exc}") from exc


def _row_map(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return None if row is None else {key: row[key] for key in row.keys()}


def _extract_dialog_nodes(raw_json: Any, path: str) -> tuple[dict[str, Any], ...]:
    value = _json_column(raw_json, f"source_record.raw_json for {path}")
    try:
        normalized = deob_talk_generic(value)
    except Exception as exc:
        raise verification_error(f"cannot normalize Talk source record {path}: {exc}") from exc
    nodes = normalized.get("dialogList")
    if not isinstance(nodes, list):
        raise verification_error(f"Talk source record {path} has no dialogList array")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict) or not is_int(node.get("id")):
            raise verification_error(f"Talk source record {path} node {index} has no integer id")
        node_id = str(node["id"])
        if node_id in seen:
            raise verification_error(f"Talk source record {path} repeats node {node_id}")
        seen.add(node_id)
        if not isinstance(node.get("nextDialogs", []), list):
            raise verification_error(f"Talk source record {path} node {node_id} has non-array nextDialogs")
        result.append(node)
    return tuple(result)


def _check_required_tables(
    conn: sqlite3.Connection,
    schema_version: int,
) -> int:
    try:
        names = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        versions = [
            int(row[0])
            for row in conn.execute(
                "SELECT version FROM schema_migration ORDER BY version"
            ).fetchall()
        ]
    except sqlite3.Error as exc:
        raise verification_error(f"database schema cannot be inspected: {exc}") from exc
    required = {
        "schema_migration",
        "source_catalog",
        "source_snapshot",
        "source_file",
        "source_record",
        "document",
        "document_version",
        "parser_build",
        "ingest_run",
        "evidence_container",
        "identity_group",
        "identity_group_container",
        "passage_occurrence",
        "passage_occurrence_text",
        "occurrence_edge",
        "candidate_scope_proof",
        "document_evidence",
        "logical_talk",
        "passage",
        "passage_edge",
    }
    missing = sorted(required - names)
    if missing:
        raise verification_error(f"database is missing required schema tables: {missing}")
    if schema_version not in (7, 10):
        raise verification_error(
            f"AQ database audit does not support Schema {schema_version}"
        )
    if versions != list(range(1, schema_version + 1)):
        raise verification_error(
            "database schema migration set must be exactly "
            f"1..{schema_version}, got {versions}"
        )
    forbidden = {
        "static_evidence_container",
        "static_segment_occurrence",
        "static_relationship_occurrence",
        "static_text_resolution_occurrence",
        "static_text_candidate_occurrence",
        "character_row_occurrence",
        "character_raw_slot_occurrence",
        "character_relation_occurrence",
        "character_text_resolution_occurrence",
        "character_text_candidate_occurrence",
        "character_voice_container_occurrence",
        "character_audio_variant_occurrence",
    }
    leaked = sorted(names & forbidden)
    if schema_version == 7 and leaked:
        raise verification_error(f"AQ Schema 7 database contains higher-stage tables: {leaked}")
    try:
        validate_exact_schema_structure(conn, schema_version)
    except (RuntimeError, ValueError, sqlite3.Error) as exc:
        raise verification_error(
            f"AQ Schema {schema_version} structural contract mismatch: {exc}"
        ) from exc
    return schema_version


@dataclass(frozen=True)
class _BindingFacts:
    schema_version: int
    scope: dict[str, Any]
    config: dict[str, Any]


def _verify_binding_root(
    conn: sqlite3.Connection,
    expected: CorpusExpectation,
    schema_version: int,
) -> _BindingFacts:
    schema_version = _check_required_tables(conn, schema_version)
    fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_rows:
        raise verification_error(
            f"PRAGMA foreign_key_check reported violations: {[tuple(row) for row in fk_rows[:5]]}"
        )

    snapshot_row = conn.execute(
        "SELECT * FROM source_snapshot WHERE id=?", (expected.snapshot_id,)
    ).fetchone()
    snapshot = _row_map(snapshot_row)
    if snapshot is None:
        raise verification_error(f"database is missing expected source_snapshot {expected.snapshot_id}")
    source = expected.source_manifest
    for column, value in (
        ("source_id", source["source_id"]),
        ("release_label", source["release_label"]),
        ("build", source.get("build")),
        ("commit", source["commit"]),
        ("tree", source["tree"]),
    ):
        if value is not None and snapshot.get(column) != value:
            raise verification_error(
                f"source_snapshot.{column} mismatch: expected {value!r}, got {snapshot.get(column)!r}"
            )
    if snapshot.get("status") not in {"active", "superseded"}:
        raise verification_error(f"source_snapshot status is not releasable: {snapshot.get('status')!r}")
    if conn.execute("SELECT id FROM source_catalog WHERE id=?", (source["source_id"],)).fetchone() is None:
        raise verification_error(f"database is missing source_catalog {source['source_id']}")

    parser = _row_map(
        conn.execute("SELECT * FROM parser_build WHERE id=?", (expected.parser_build_id,)).fetchone()
    )
    if parser is None:
        raise verification_error(f"database is missing expected parser_build {expected.parser_build_id}")
    for column, value in {
        "parser_version": PARSER_VERSION_CORPUS,
        "mapping_version": MAPPING_VERSION_CORPUS,
        "fingerprint": "quest-talk-manifest-driven-v1" if expected.manifest_set.corpus_manifest.scalable_scope is not None else parser_fingerprint(
            expected.manifest_set.corpus_manifest.manifest_id
        ),
        "config_checksum": expected.config_checksum,
    }.items():
        if parser.get(column) != value:
            raise verification_error(
                f"parser_build.{column} mismatch: expected {value!r}, got {parser.get(column)!r}"
            )

    run = _row_map(
        conn.execute("SELECT * FROM ingest_run WHERE id=?", (expected.ingest_run_id,)).fetchone()
    )
    if run is None:
        raise verification_error(f"database is missing expected ingest_run {expected.ingest_run_id}")
    if run.get("snapshot_id") != expected.snapshot_id:
        raise verification_error("ingest_run snapshot_id does not match sealed snapshot")
    if run.get("parser_build_id") != expected.parser_build_id:
        raise verification_error("ingest_run parser_build_id does not match sealed parser build")
    if run.get("status") != "complete":
        raise verification_error(f"ingest_run status must be complete, got {run.get('status')!r}")
    scope = require_mapping(_json_column(run.get("scope_json"), "ingest_run.scope_json"), "ingest_run.scope_json")
    config = require_mapping(_json_column(run.get("config_json"), "ingest_run.config_json"), "ingest_run.config_json")
    if scope.get("family") != "quest_corpus_evidence":
        raise verification_error(f"ingest_run scope family mismatch: {scope.get('family')!r}")
    if scope.get("quest_ids") != list(expected.quest_ids):
        raise verification_error("ingest_run scope quest_ids do not match sealed manifest")
    if scope.get("selected_paths") != list(expected.selected_paths):
        raise verification_error("ingest_run scope selected_paths do not match sealed inventory")
    if scope.get("talk_ids") != sorted({int(reference.talk_id) for reference in expected.references}):
        raise verification_error("ingest_run scope talk_ids do not match sealed references")
    if config != {
        "full_scan": False,
        "languages": list(LANGUAGES),
        "projection": PROJECTION_VERSION_CORPUS,
        "textmap_hydration": "v1",
    }:
        raise verification_error(f"ingest_run config mismatch: {config!r}")
    return _BindingFacts(schema_version=schema_version, scope=scope, config=config)


@dataclass(frozen=True)
class _SourceFacts:
    source_files_by_path: dict[str, sqlite3.Row]
    records_by_file: dict[str, list[sqlite3.Row]]
    source_file_count: int
    source_record_count: int


def _load_source_inventory(
    conn: sqlite3.Connection,
    expected: CorpusExpectation,
) -> _SourceFacts:
    source_file_rows = conn.execute(
        "SELECT * FROM source_file WHERE snapshot_id=? ORDER BY relative_path, id",
        (expected.snapshot_id,),
    ).fetchall()
    source_files_by_path: dict[str, sqlite3.Row] = {}
    for row in source_file_rows:
        path = str(row["relative_path"])
        if path in source_files_by_path:
            raise verification_error(f"duplicate source_file path in snapshot: {path}")
        source_files_by_path[path] = row
    missing_paths = [path for path in expected.selected_paths if path not in source_files_by_path]
    if missing_paths:
        raise verification_error(f"selected source files are absent from database: {missing_paths[:5]}")

    source_record_rows = conn.execute(
        """SELECT sr.*, sf.relative_path AS source_path, sf.snapshot_id AS source_snapshot_id
           FROM source_record sr JOIN source_file sf ON sf.id=sr.source_file_id
           WHERE sf.snapshot_id=? ORDER BY sf.relative_path, sr.pointer, sr.id""",
        (expected.snapshot_id,),
    ).fetchall()
    records_by_file: dict[str, list[sqlite3.Row]] = {}
    for row in source_record_rows:
        records_by_file.setdefault(str(row["source_path"]), []).append(row)
    for path in expected.quest_paths:
        count = sum(row["record_kind"] == "quest_root" for row in records_by_file.get(path, ()))
        if count != 1:
            raise verification_error(f"expected one quest_root source record for {path}, got {count}")
    for path in expected.talk_paths:
        count = sum(row["record_kind"] == "talk_root" for row in records_by_file.get(path, ()))
        if count != 1:
            raise verification_error(f"expected one talk_root source record for {path}, got {count}")
    return _SourceFacts(
        source_files_by_path=source_files_by_path,
        records_by_file=records_by_file,
        source_file_count=len(source_file_rows),
        source_record_count=len(source_record_rows),
    )


@dataclass(frozen=True)
class _EvidenceFacts:
    container_rows: list[sqlite3.Row]
    containers_by_path: dict[str, sqlite3.Row]
    nodes_by_path: dict[str, tuple[dict[str, Any], ...]]
    occurrence_rows: list[sqlite3.Row]
    occurrences_by_path: dict[str, dict[str, sqlite3.Row]]


def _load_evidence_containers_and_occurrences(
    conn: sqlite3.Connection,
    expected: CorpusExpectation,
) -> _EvidenceFacts:
    container_rows = conn.execute(
        """SELECT ec.*, sr.raw_json, sr.raw_sha256 AS source_record_raw_sha256,
                  sr.record_kind, sf.relative_path AS source_path,
                  sf.snapshot_id AS source_snapshot_id
           FROM evidence_container ec
           JOIN source_record sr ON sr.id=ec.source_record_id
           JOIN source_file sf ON sf.id=sr.source_file_id
           WHERE ec.ingest_run_id=? ORDER BY sf.relative_path, ec.id""",
        (expected.ingest_run_id,),
    ).fetchall()
    if len(container_rows) != len(expected.talk_paths):
        raise verification_error(
            f"evidence_container count mismatch: expected {len(expected.talk_paths)}, got {len(container_rows)}"
        )
    containers_by_path: dict[str, sqlite3.Row] = {}
    nodes_by_path: dict[str, tuple[dict[str, Any], ...]] = {}
    for row in container_rows:
        path = str(row["source_path"])
        if path in containers_by_path:
            raise verification_error(f"duplicate evidence container for sealed Talk path {path}")
        if path not in expected.candidate_by_path:
            raise verification_error(f"evidence container points outside sealed Talk inventory: {path}")
        if row["kind"] != "talk_root" or row["record_kind"] != "talk_root":
            raise verification_error(f"unexpected evidence container source kind for {path}")
        if row["source_snapshot_id"] != expected.snapshot_id:
            raise verification_error(f"evidence container source snapshot mismatch for {path}")
        if row["raw_checksum"] != row["source_record_raw_sha256"]:
            raise verification_error(f"evidence container raw checksum mismatch for {path}")
        nodes = _extract_dialog_nodes(row["raw_json"], path)
        candidate = expected.candidate_by_path[path]
        if len(nodes) != candidate.node_count:
            raise verification_error(f"Talk node count mismatch for {path}: expected {candidate.node_count}, got {len(nodes)}")
        if sum(len(node.get("nextDialogs", [])) for node in nodes) != candidate.edge_count:
            raise verification_error(f"Talk raw edge count mismatch for {path}")
        normalized = deob_talk_generic(_json_column(row["raw_json"], f"Talk {path}"))
        if row["normalized_checksum"] != sha256_text(canonical_json(normalized)):
            raise verification_error(f"evidence container normalized checksum mismatch for {path}")
        if row["normalized_checksum"] != candidate.normalized_sha256:
            raise verification_error(f"Talk normalized checksum mismatch for {path}")
        if str(row["talk_id"]) != expected.candidate_talk_id_by_path[path]:
            raise verification_error(f"evidence container talk_id mismatch for {path}")
        if str(row["namespace"]) != "QUEST":
            raise verification_error(f"evidence container namespace mismatch for {path}")
        containers_by_path[path] = row
        nodes_by_path[path] = nodes
    if set(containers_by_path) != set(expected.talk_paths):
        raise verification_error("evidence_container paths do not exactly match sealed candidate paths")

    occurrence_rows = conn.execute(
        """SELECT po.*, ec.source_record_id AS container_source_record_id,
                  sf.relative_path AS source_path, sf.snapshot_id AS source_snapshot_id
           FROM passage_occurrence po
           JOIN evidence_container ec
             ON ec.id=po.evidence_container_id AND ec.ingest_run_id=po.ingest_run_id
           JOIN source_file sf ON sf.id=(
               SELECT sr.source_file_id FROM source_record sr
                WHERE sr.id=ec.source_record_id)
           WHERE po.ingest_run_id=? ORDER BY sf.relative_path, po.sequence_key, po.passage_occurrence_id""",
        (expected.ingest_run_id,),
    ).fetchall()
    expected_count = expected.manifest_set.corpus_manifest.corpus_invariants.expected_occurrences
    if len(occurrence_rows) != expected_count:
        raise verification_error(f"passage_occurrence count mismatch: expected {expected_count}, got {len(occurrence_rows)}")
    occurrences_by_path: dict[str, dict[str, sqlite3.Row]] = {}
    for row in occurrence_rows:
        path = str(row["source_path"])
        container = containers_by_path.get(path)
        if container is None:
            raise verification_error(f"passage_occurrence points outside sealed containers: {path}")
        if row["evidence_container_id"] != container["id"] or row["source_record_id"] != container["source_record_id"]:
            raise verification_error(f"passage_occurrence container/source mismatch for {path}")
        if row["source_snapshot_id"] != expected.snapshot_id:
            raise verification_error(f"passage_occurrence source snapshot mismatch for {path}")
        node_id = str(row["node_id"])
        occurrence_map = occurrences_by_path.setdefault(path, {})
        if node_id in occurrence_map:
            raise verification_error(f"duplicate passage_occurrence node {node_id} for {path}")
        index = len(occurrence_map)
        if row["source_pointer"] != f"/dialogList/{index}" or row["sequence_key"] != f"{index:04d}":
            raise verification_error(f"passage_occurrence ordering/pointer mismatch for {path} node {node_id}")
        node = nodes_by_path[path][index]
        if node_id != str(node["id"]):
            raise verification_error(f"passage_occurrence node mismatch for {path} index {index}")
        if row["namespace"] != container["namespace"] or str(row["talk_id"]) != str(container["talk_id"]):
            raise verification_error(f"passage_occurrence native scope mismatch for {path} node {node_id}")
        occurrence_map[node_id] = row
    for path in expected.talk_paths:
        occurrence_map = occurrences_by_path.get(path, {})
        candidate = expected.candidate_by_path[path]
        if len(occurrence_map) != candidate.node_count or set(occurrence_map) != {str(node["id"]) for node in nodes_by_path[path]}:
            raise verification_error(f"passage_occurrence node inventory mismatch for {path}")
    return _EvidenceFacts(
        container_rows=container_rows,
        containers_by_path=containers_by_path,
        nodes_by_path=nodes_by_path,
        occurrence_rows=occurrence_rows,
        occurrences_by_path=occurrences_by_path,
    )


def _verify_graph(
    conn: sqlite3.Connection,
    expected: CorpusExpectation,
    evidence: _EvidenceFacts,
) -> dict[str, Any]:
    node_paths: dict[str, list[str]] = {}
    for path in sorted(evidence.nodes_by_path):
        for node in evidence.nodes_by_path[path]:
            node_paths.setdefault(str(node["id"]), []).append(path)

    external: list[dict[str, Any]] = []
    dangling: list[dict[str, Any]] = []
    local_pairs: set[tuple[str, str, str]] = set()
    raw_pointer_count = 0
    for path in sorted(evidence.nodes_by_path):
        local_ids = {str(node["id"]) for node in evidence.nodes_by_path[path]}
        for node in evidence.nodes_by_path[path]:
            source_id = str(node["id"])
            targets = node.get("nextDialogs", [])
            raw_pointer_count += len(targets)
            for target in targets:
                if not is_int(target):
                    continue
                target_id = str(target)
                if target_id in local_ids:
                    local_pairs.add((path, source_id, target_id))
                    continue
                target_paths = sorted(node_paths.get(target_id, []))
                item = {
                    "source_talk_path": path,
                    "source_node_id": source_id,
                    "target_node_id": target_id,
                    "target_candidate_paths": target_paths,
                    "classification": (
                        "external_target"
                        if len(target_paths) == 1
                        else "ambiguous_target" if target_paths else "dangling_target"
                    ),
                }
                (external if target_paths else dangling).append(item)

    edge_rows = conn.execute(
        """SELECT oe.*, sf.relative_path AS source_path,
                  from_po.node_id AS from_node_id, to_po.node_id AS to_node_id,
                  from_po.evidence_container_id AS from_container_id,
                  to_po.evidence_container_id AS to_container_id
           FROM occurrence_edge oe
           JOIN evidence_container ec
             ON ec.id=oe.evidence_container_id AND ec.ingest_run_id=oe.ingest_run_id
           JOIN source_record sr ON sr.id=ec.source_record_id
           JOIN source_file sf ON sf.id=sr.source_file_id
           JOIN passage_occurrence from_po
             ON from_po.passage_occurrence_id=oe.from_passage_occurrence_id
            AND from_po.ingest_run_id=oe.ingest_run_id
            AND from_po.evidence_container_id=oe.evidence_container_id
           JOIN passage_occurrence to_po
             ON to_po.passage_occurrence_id=oe.to_passage_occurrence_id
            AND to_po.ingest_run_id=oe.ingest_run_id
            AND to_po.evidence_container_id=oe.evidence_container_id
           WHERE oe.ingest_run_id=? ORDER BY sf.relative_path, from_po.sequence_key, to_po.sequence_key, oe.id""",
        (expected.ingest_run_id,),
    ).fetchall()
    actual_pairs: set[tuple[str, str, str]] = set()
    for row in edge_rows:
        path = str(row["source_path"])
        container = evidence.containers_by_path.get(path)
        if container is None:
            raise verification_error(f"occurrence_edge points outside sealed containers: {path}")
        if row["from_container_id"] != row["to_container_id"] or row["from_container_id"] != container["id"]:
            raise verification_error(f"occurrence_edge crosses container scope for {path}")
        if row["edge_type"] != "next":
            raise verification_error(f"unexpected occurrence_edge type for {path}: {row['edge_type']!r}")
        actual_pairs.add((path, str(row["from_node_id"]), str(row["to_node_id"])))
    if actual_pairs != local_pairs:
        raise verification_error(
            f"occurrence_edge projection mismatch: expected {len(local_pairs)} local edges, got {len(actual_pairs)}"
        )
    return {
        "raw_next_pointer_count": raw_pointer_count,
        "intra_container_edge_count": len(actual_pairs),
        "external_target_pointer_count": len(external),
        "ambiguous_target_pointer_count": sum(item["classification"] == "ambiguous_target" for item in external),
        "dangling_target_pointer_count": len(dangling),
        "external_targets": external,
        "dangling_targets": dangling,
    }


def _verify_text(
    conn: sqlite3.Connection,
    expected: CorpusExpectation,
    evidence: _EvidenceFacts,
    source: _SourceFacts,
) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT pot.*, po.node_id, ec.id AS container_id,
                  sf.relative_path AS text_source_path,
                  sf.snapshot_id AS text_source_snapshot_id,
                  occ_sf.relative_path AS occurrence_source_path
           FROM passage_occurrence_text pot
           JOIN passage_occurrence po
             ON po.passage_occurrence_id=pot.passage_occurrence_id
            AND po.ingest_run_id=pot.ingest_run_id
            AND po.evidence_container_id=pot.evidence_container_id
           JOIN evidence_container ec
             ON ec.id=pot.evidence_container_id AND ec.ingest_run_id=pot.ingest_run_id
           JOIN source_record text_sr ON text_sr.id=pot.source_record_id
           JOIN source_file sf ON sf.id=text_sr.source_file_id
           JOIN source_record occ_sr ON occ_sr.id=ec.source_record_id
           JOIN source_file occ_sf ON occ_sf.id=occ_sr.source_file_id
           WHERE pot.ingest_run_id=? ORDER BY occ_sf.relative_path, po.sequence_key, pot.language, pot.id""",
        (expected.ingest_run_id,),
    ).fetchall()
    text_counts = {language: 0 for language in LANGUAGES}
    nonempty_count = 0
    seen: set[tuple[str, str]] = set()
    for row in rows:
        path = str(row["occurrence_source_path"])
        container = evidence.containers_by_path.get(path)
        if container is None:
            raise verification_error(f"passage_occurrence_text points outside sealed containers: {path}")
        if row["container_id"] != container["id"]:
            raise verification_error(f"passage_occurrence_text container mismatch for {path}")
        language = str(row["language"])
        if language not in LANGUAGES:
            raise verification_error(f"unexpected passage_occurrence_text language: {language!r}")
        if str(row["text_source_path"]) not in expected.textmap_paths:
            raise verification_error(f"passage_occurrence_text source is outside selected TextMaps: {row['text_source_path']!r}")
        if row["text_source_snapshot_id"] != expected.snapshot_id:
            raise verification_error("passage_occurrence_text source snapshot mismatch")
        key = (str(row["passage_occurrence_id"]), language)
        if key in seen:
            raise verification_error(f"duplicate occurrence text language row: {key}")
        seen.add(key)
        text_counts[language] += 1
        if row["raw_text"]:
            nonempty_count += 1

    expected_slots = expected.manifest_set.corpus_manifest.corpus_invariants.expected_texts_per_language * len(LANGUAGES)
    missing = expected_slots - len(rows)
    if missing < 0:
        raise verification_error("passage_occurrence_text count exceeds sealed language slots")
    textmap_records = [
        row
        for path in expected.textmap_paths
        for row in source.records_by_file.get(path, ())
        if row["record_kind"] == "textmap_entry"
    ]
    selected_hashes: set[str] = {str(node.get("talkContentTextMapHash", 0)) for nodes in evidence.nodes_by_path.values() for node in nodes if str(node.get("talkContentTextMapHash", 0)) != "0"}
    for row in textmap_records:
        data = _json_column(row["raw_json"], f"TextMap source record {row['id']}")
        if isinstance(data, dict) and isinstance(data.get("hash"), str):
            selected_hashes.add(data["hash"])
    return {
        "status": "partial" if missing else "complete",
        "languages": list(LANGUAGES),
        "expected_language_slots": expected_slots,
        "total_occurrence_texts": len(rows),
        "counts_by_language": text_counts,
        "resolved_occurrence_text_count": len(rows),
        "nonempty_count": nonempty_count,
        "missing": missing,
        "both_equal": 0,
        "both_conflict": 0,
        "duplicate": 0,
        "selected_content_hash_count": len(selected_hashes),
        "selected_textmap_source_file_count": sum(
            path in source.source_files_by_path for path in expected.textmap_paths
        ),
        "selected_textmap_source_record_count": len(textmap_records),
        "role_name_hydrated": False,
        "old_run_preserved": None,
    }


def _verify_documents_and_evidence(
    conn: sqlite3.Connection,
    expected: CorpusExpectation,
    source: _SourceFacts,
    evidence: _EvidenceFacts,
) -> int:
    rows = conn.execute(
        """SELECT de.*, sr.pointer AS source_pointer, sr.record_kind,
                  sf.relative_path AS quest_source_path,
                  sf.snapshot_id AS reference_snapshot_id
           FROM document_evidence de
           JOIN source_record sr ON sr.id=de.source_record_id
           JOIN source_file sf ON sf.id=sr.source_file_id
           WHERE de.ingest_run_id=? ORDER BY sf.relative_path, sr.pointer, de.id""",
        (expected.ingest_run_id,),
    ).fetchall()
    if len(rows) != len(expected.references):
        raise verification_error(
            f"document_evidence count mismatch: expected {len(expected.references)}, got {len(rows)}"
        )
    refs_by_pointer = {
        (reference.quest_path, f"/OJACLOOEAMG/{reference.index}"): reference
        for reference in expected.references
    }
    seen: set[tuple[str, str]] = set()
    container_id_by_path = {
        path: str(row["id"]) for path, row in evidence.containers_by_path.items()
    }
    bundles_by_id = {
        bundle.manifest_id: bundle
        for bundle in expected.manifest_set.quest_candidate_bundles
    }
    expected_proofs: dict[str, tuple[Any, ...]] = {}
    referenced_proof_ids: set[str] = set()
    for row in rows:
        key = (str(row["quest_source_path"]), str(row["source_pointer"]))
        reference = refs_by_pointer.get(key)
        if reference is None:
            raise verification_error(f"document_evidence points outside sealed Quest references: {key}")
        if key in seen:
            raise verification_error(f"duplicate document_evidence for reference {key}")
        seen.add(key)
        if row["record_kind"] != "quest_talk_reference" or row["reference_snapshot_id"] != expected.snapshot_id:
            raise verification_error(f"document_evidence source record scope mismatch for {key}")
        candidate_count = len(reference.candidates)
        expected_result = "unresolved" if not candidate_count else "evidence_only" if candidate_count == 1 else "ambiguous"
        expected_group = digest_id("ig", expected.ingest_run_id, reference.namespace, reference.talk_id) if candidate_count > 1 else None
        if row["result"] != expected_result or row["identity_group_id"] != expected_group:
            raise verification_error(f"document_evidence candidate-state mismatch for {key}")
        if row["namespace"] != reference.namespace or str(row["talk_id"]) != reference.talk_id:
            raise verification_error(f"document_evidence native reference mismatch for {key}")
        if row["document_id"] != f"agd:quest:{reference.quest_id}":
            raise verification_error(f"document_evidence document mismatch for {key}")
        if row["evidence_container_id"] != (container_id_by_path[reference.candidate_path] if reference.candidate_path else None):
            raise verification_error(f"document_evidence selected a different candidate for {key}")
        if (
            row["candidate_manifest_id"] != reference.bundle_manifest_id
            or row["candidate_manifest_checksum"] != reference.bundle_manifest_checksum
        ):
            raise verification_error(f"document_evidence candidate manifest binding mismatch for {key}")
        bundle = bundles_by_id.get(reference.bundle_manifest_id)
        if bundle is None or reference.index >= len(bundle.references):
            raise verification_error(f"sealed candidate bundle is absent for {key}")
        raw_reference = bundle.references[reference.index]
        if (
            raw_reference.namespace != reference.namespace
            or str(raw_reference.talk_id) != reference.talk_id
            or raw_reference.candidates != reference.candidates
        ):
            raise verification_error(f"sealed candidate reference drift for {key}")

        expected_proof_id = digest_id(
            "candidate-scope-proof",
            expected.ingest_run_id,
            reference.namespace,
            reference.talk_id,
            reference.bundle_manifest_id,
            reference.bundle_manifest_checksum,
        )
        metadata_json = canonical_json({
            "audit_cache_state": "verified",
            "manifest_inventory_complete": True,
            "audit_mapping_version": MAPPING_VERSION,
            "raw_byte_basis": "utf-8-sig:json:canonical_sha256",
            "candidates": [{
                "relative_path": candidate.relative_path,
                "raw_sha256": str(source.source_files_by_path[candidate.relative_path]["sha256"]),
                "normalized_checksum": candidate.normalized_sha256,
                "raw_size_bytes": int(source.source_files_by_path[candidate.relative_path]["size_bytes"]),
            } for candidate in reference.candidates],
            "structural_errors": [],
        })
        expected_proof = (
            expected_proof_id,
            expected.ingest_run_id,
            reference.bundle_manifest_id,
            reference.bundle_manifest_checksum,
            expected.snapshot_id,
            expected.source_manifest["commit"],
            expected.source_manifest["tree"],
            DISCOVERY_VERSION,
            expected.audit_cache_sha256,
            reference.namespace,
            reference.talk_id,
            1,
            candidate_count,
            metadata_json,
        )
        prior = expected_proofs.setdefault(expected_proof_id, expected_proof)
        if prior != expected_proof:
            raise verification_error(
                f"candidate_scope_proof source contract conflict for {key}"
            )
        if row["candidate_scope_proof_id"] != expected_proof_id:
            raise verification_error(f"document_evidence proof ID mismatch for {key}")
        referenced_proof_ids.add(expected_proof_id)
        proof = conn.execute(
            "SELECT * FROM candidate_scope_proof WHERE id=? AND ingest_run_id=?",
            (row["candidate_scope_proof_id"], expected.ingest_run_id),
        ).fetchone()
        if proof is None:
            raise verification_error(f"document_evidence candidate scope proof missing for {key}")
        proof_dict = _row_map(proof) or {}
        actual_proof = tuple(proof_dict.get(column) for column in (
            "id", "ingest_run_id", "manifest_id", "manifest_checksum",
            "snapshot_id", "snapshot_commit", "snapshot_tree",
            "audit_algorithm_version", "audit_cache_digest", "namespace",
            "talk_id", "inventory_complete", "candidate_count", "metadata_json",
        ))
        if actual_proof != expected_proof:
            raise verification_error(f"candidate_scope_proof full-row mismatch for {key}")
    if seen != set(refs_by_pointer):
        raise verification_error("document_evidence references do not exactly match sealed Quest pointers")
    proof_columns = (
        "id,ingest_run_id,manifest_id,manifest_checksum,snapshot_id,"
        "snapshot_commit,snapshot_tree,audit_algorithm_version,"
        "audit_cache_digest,namespace,talk_id,inventory_complete,"
        "candidate_count,metadata_json"
    )
    all_proofs = [
        tuple(row)
        for row in conn.execute(
            f"SELECT {proof_columns} FROM candidate_scope_proof ORDER BY id"
        )
    ]
    if all_proofs != sorted(expected_proofs.values()):
        raise verification_error(
            "candidate_scope_proof exact full-table set mismatch: "
            f"actual={len(all_proofs)} expected={len(expected_proofs)}"
        )
    all_referenced_proof_ids = {
        str(row[0])
        for row in conn.execute(
            "SELECT DISTINCT candidate_scope_proof_id FROM document_evidence"
        )
    }
    if (
        referenced_proof_ids != set(expected_proofs)
        or all_referenced_proof_ids != set(expected_proofs)
    ):
        raise verification_error(
            "candidate_scope_proof referenced/source-derived ID set mismatch"
        )

    for quest_id in expected.quest_ids:
        document = conn.execute(
            "SELECT * FROM document WHERE stable_id=?", (f"agd:quest:{quest_id}",)
        ).fetchone()
        if document is None or document["content_type"] != "quest" or str(document["native_id"]) != str(quest_id):
            raise verification_error(f"Quest document missing or malformed: {quest_id}")
        if conn.execute(
            "SELECT * FROM document_version WHERE document_id=? AND snapshot_id=?",
            (f"agd:quest:{quest_id}", expected.snapshot_id),
        ).fetchone() is None:
            raise verification_error(f"Quest document_version missing for {quest_id}")
        count = int(conn.execute(
            "SELECT COUNT(*) FROM document_evidence WHERE document_id=? AND ingest_run_id=?",
            (f"agd:quest:{quest_id}", expected.ingest_run_id),
        ).fetchone()[0])
        expected_count = sum(reference.quest_id == quest_id for reference in expected.references)
        if count != expected_count:
            raise verification_error(
                f"Quest {quest_id} evidence row count mismatch: expected {expected_count}, got {count}"
            )
    return len(rows)


def _verify_identity_scope(conn: sqlite3.Connection, expected: CorpusExpectation) -> bool:
    # Talk projection records an observed native-key group and membership for
    # every canonical container.  That projection identity is independent of
    # document_evidence ambiguity: the sealed AQ references are all resolved
    # evidence_only, but their 149 projected containers still have 149 groups
    # and 149 memberships.  Rebuild both full-row sets from sealed manifest
    # coordinates and deterministic identities; never infer expectations from
    # the database rows being verified.
    identity = build_identity_scope_expectation(expected)

    actual_groups = [
        tuple(row)
        for row in conn.execute(
            "SELECT id,ingest_run_id,namespace,talk_id,metadata_json "
            "FROM identity_group WHERE ingest_run_id=? ORDER BY id",
            (expected.ingest_run_id,),
        )
    ]
    wanted_groups = list(identity.groups)
    if actual_groups != wanted_groups:
        raise verification_error(
            "sealed AQ identity_group exact current-run set mismatch: "
            f"actual={len(actual_groups)} expected={len(wanted_groups)}"
        )

    actual_memberships = [
        tuple(row)
        for row in conn.execute(
            "SELECT identity_group_id,ingest_run_id,evidence_container_id "
            "FROM identity_group_container WHERE ingest_run_id=? "
            "ORDER BY identity_group_id,ingest_run_id,evidence_container_id",
            (expected.ingest_run_id,),
        )
    ]
    wanted_memberships = list(identity.memberships)
    if actual_memberships != wanted_memberships:
        raise verification_error(
            "sealed AQ identity_group_container exact current-run set mismatch: "
            f"actual={len(actual_memberships)} expected={len(wanted_memberships)}"
        )
    return int(conn.execute(
        "SELECT COUNT(*) FROM ingest_run WHERE id<>?", (expected.ingest_run_id,)
    ).fetchone()[0]) > 0


def _database_counts(
    expected: CorpusExpectation,
    source: _SourceFacts,
    evidence: _EvidenceFacts,
    graph: dict[str, Any],
    text: dict[str, Any],
    document_evidence_count: int,
) -> dict[str, Any]:
    counts = {
        "quest_count": len(expected.quest_ids),
        "evidence_container_count": len(evidence.container_rows),
        "occurrence_count": len(evidence.occurrence_rows),
        "occurrence_edge_count": graph["intra_container_edge_count"],
        "source_file_count": source.source_file_count,
        "source_record_count": source.source_record_count,
        "document_evidence_count": document_evidence_count,
        "document_evidence_evidence_only_count": sum(len(r.candidates) == 1 for r in expected.references),
    }
    master = expected.manifest_set.corpus_manifest
    if master.manifest_id == AQ_MASTER_MANIFEST_ID:
        for key, value in (
            ("local_edges", graph["intra_container_edge_count"]),
            ("external_pointers", graph["external_target_pointer_count"]),
            ("dangling_pointers", graph["dangling_target_pointer_count"]),
            ("resolved_texts", text["resolved_occurrence_text_count"]),
            ("text_counts", text["counts_by_language"]),
            ("missing_texts", text["missing"]),
        ):
            if value != AQ_RELEASE_COUNTS[key]:
                raise verification_error(f"AQ release {key} mismatch: expected {AQ_RELEASE_COUNTS[key]!r}, got {value!r}")
    return counts


def _reconcile_report(
    build_report: dict[str, Any],
    expected: CorpusExpectation,
    source: _SourceFacts,
    evidence: _EvidenceFacts,
    graph: dict[str, Any],
    text: dict[str, Any],
    document_evidence_count: int,
) -> dict[str, Any]:
    counts = _database_counts(
        expected,
        source,
        evidence,
        graph,
        text,
        document_evidence_count,
    )
    for key in (
        "evidence_container_count",
        "occurrence_count",
        "occurrence_edge_count",
    ):
        require_report_value(build_report, key, counts[key])
    for key in ("source_file_count", "source_record_count"):
        if key in build_report:
            require_report_value(build_report, key, counts[key])
    require_report_value(build_report, "reference_counts.total_references", len(expected.references))
    require_report_value(build_report, "reference_counts.resolved_references", sum(len(r.candidates) == 1 for r in expected.references))
    require_report_value(build_report, "reference_counts.ambiguous_references", sum(len(r.candidates) > 1 for r in expected.references))
    require_report_value(build_report, "reference_counts.unresolved_references", sum(len(r.candidates) == 0 for r in expected.references))
    for key, value in graph.items():
        require_report_value(build_report, f"graph_projection.{key}", value)
    for key, value in text.items():
        if value is not None:
            require_report_value(build_report, f"text_hydration.{key}", value)
    return counts


def _audit_database_state(
    conn: sqlite3.Connection,
    expected: CorpusExpectation,
    *,
    schema_version: int,
    build_report: dict[str, Any] | None,
) -> dict[str, Any]:
    binding = _verify_binding_root(conn, expected, schema_version)
    source = _load_source_inventory(conn, expected)
    evidence = _load_evidence_containers_and_occurrences(conn, expected)
    graph = _verify_graph(conn, expected, evidence)
    text = _verify_text(conn, expected, evidence, source)
    text["old_run_preserved"] = _verify_identity_scope(conn, expected)
    document_evidence_count = _verify_documents_and_evidence(
        conn, expected, source, evidence
    )
    if build_report is None:
        counts = _database_counts(
            expected,
            source,
            evidence,
            graph,
            text,
            document_evidence_count,
        )
    else:
        counts = _reconcile_report(
            build_report,
            expected,
            source,
            evidence,
            graph,
            text,
            document_evidence_count,
        )
    return {
        "schema_version": binding.schema_version,
        "counts": counts,
        "graph_projection": graph,
        "text_hydration": text,
        "foreign_key_check": {"ok": True, "violations": []},
        "run": {
            "ingest_run_id": expected.ingest_run_id,
            "parser_build_id": expected.parser_build_id,
            "snapshot_id": expected.snapshot_id,
            "status": "complete",
            "scope": binding.scope,
            "config": binding.config,
        },
        "manifest": {
            "manifest_id": expected.manifest_set.corpus_manifest.manifest_id,
            "raw_sha256": expected.master_manifest_sha256,
            "audit_cache_sha256": expected.audit_cache_sha256,
            "selected_paths": list(expected.selected_paths),
        },
    }


def audit_database_state(
    conn: sqlite3.Connection,
    expected: CorpusExpectation,
    *,
    schema_version: int = 10,
) -> dict[str, Any]:
    """Verify AQ from manifests and persisted rows, without a stage report."""

    result = _audit_database_state(
        conn,
        expected,
        schema_version=schema_version,
        build_report=None,
    )
    exact_counts = {
        "quest_count": len(expected.quest_ids),
        "evidence_container_count": len(expected.talk_paths),
        "occurrence_count": AQ_RELEASE_COUNTS["expected_occurrences"],
        "occurrence_edge_count": AQ_RELEASE_COUNTS["local_edges"],
        "document_evidence_count": len(expected.references),
        "resolved_occurrence_text_count": AQ_RELEASE_COUNTS["resolved_texts"],
    }
    return {
        "protocol": "wave1-stage-final-state-contract-v1",
        "contract_id": (
            "wave1-stage-final-state-v1:aq:"
            + expected.manifest_set.corpus_manifest.manifest_id
        ),
        "status": "pass",
        "run_id": expected.ingest_run_id,
        "parser_build_id": expected.parser_build_id,
        "manifest_sha256": expected.master_manifest_sha256,
        "exact_counts": exact_counts,
        "required_checks": [
            "schema", "source_binding", "run_binding", "exact_counts",
            "graph_projection", "text_hydration", "quest_documents",
            "logical_boundary", "foreign_keys",
        ],
        **result,
    }


def audit_database(
    conn: sqlite3.Connection,
    expected: CorpusExpectation,
    build_report: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild release facts from the run-scoped rows and reconcile the report."""

    return _audit_database_state(
        conn,
        expected,
        schema_version=7,
        build_report=build_report,
    )


__all__ = ["audit_database", "audit_database_state"]
