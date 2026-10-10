"""Independent read-only verifier for the Phase 3.1C3B pilot.

The verifier does not use a builder report as an oracle.  It rechecks the
pinned source files, the C3A manifest seals, and every comparison row from
the physical occurrence coordinates.  A malformed, incomplete, ambiguous, or
foreign result fails closed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from pathlib import Path
from typing import Any, Mapping

from .db import Database
from .phase4e5_combined_gate import assert_path_matrix
from .talk_comparison_phase31c3b_contract import (
    CONTRACT_VERSION,
    FAMILY,
    MANIFEST_PATH,
    MATCHER_METHOD,
    MIGRATION_PATH,
    MIGRATION_SHA256,
    NAMESPACE,
    OBSERVED_AT,
    PROOF_ALGORITHM,
    SCOPE_KEY,
    SIDES,
    SNAPSHOT_ID,
    TALK_ID,
    TalkComparisonPhase31C3BError,
    canonical,
    derive_source_evidence,
    ingest_entry,
    load_manifest,
    occurrence_id,
    proof_artifact_entry,
    proof_entry,
    sha256_bytes,
    side_entry,
    verify_repository_inputs,
)
from .world_archive_phase4e2_promotion import assert_sidecars_absent
from .util import digest_id


class TalkComparisonPhase31C3BVerificationError(TalkComparisonPhase31C3BError):
    """The C3B pilot does not satisfy its independent release contract."""


SCHEMA7_CORE_TABLES = (
    "candidate_scope_proof",
    "compare_run",
    "comparison_absence_assertion",
    "comparison_change_label",
    "comparison_change_observation",
    "comparison_match_anchor",
    "comparison_match_candidate",
    "comparison_match_decision",
    "comparison_match_set",
    "comparison_scope_proof",
    "comparison_side",
    "document",
    "document_evidence",
    "document_relation",
    "document_version",
    "evidence_container",
    "identity_group",
    "identity_group_container",
    "ingest_issue",
    "ingest_run",
    "localized_text",
    "logical_talk",
    "logical_talk_evidence",
    "matcher_build",
    "metadata_fact",
    "occurrence_edge",
    "parser_build",
    "passage",
    "passage_edge",
    "passage_occurrence",
    "passage_occurrence_text",
    "passage_text",
    "passage_version",
    "schema_migration",
    "source_catalog",
    "source_file",
    "source_record",
    "source_snapshot",
    "sqlite_sequence",
    "talk_evidence",
    "textmap_conflict",
)
SCHEMA7_FTS5_TABLES = (
    "fts_evidence",
    "fts_evidence_config",
    "fts_evidence_content",
    "fts_evidence_data",
    "fts_evidence_docsize",
    "fts_evidence_idx",
)
SCHEMA7_FALLBACK_TABLES = ("fts_evidence",)

SCHEMA7_CORE_ROW_COUNTS = {
    "candidate_scope_proof": 0,
    "compare_run": 1,
    "comparison_absence_assertion": 0,
    "comparison_change_label": 28,
    "comparison_change_observation": 14,
    "comparison_match_anchor": 28,
    "comparison_match_candidate": 14,
    "comparison_match_decision": 14,
    "comparison_match_set": 14,
    "comparison_scope_proof": 2,
    "comparison_side": 2,
    "document": 0,
    "document_evidence": 0,
    "document_relation": 0,
    "document_version": 0,
    "evidence_container": 2,
    "identity_group": 0,
    "identity_group_container": 0,
    "ingest_issue": 0,
    "ingest_run": 2,
    "localized_text": 0,
    "logical_talk": 0,
    "logical_talk_evidence": 0,
    "matcher_build": 1,
    "metadata_fact": 0,
    "occurrence_edge": 28,
    "parser_build": 2,
    "passage": 0,
    "passage_edge": 0,
    "passage_occurrence": 28,
    "passage_occurrence_text": 84,
    "passage_text": 0,
    "passage_version": 0,
    "schema_migration": 7,
    "source_catalog": 1,
    "source_file": 7,
    "source_record": 43,
    "source_snapshot": 1,
    "sqlite_sequence": 0,
    "talk_evidence": 0,
    "textmap_conflict": 0,
}
SCHEMA7_FTS5_ROW_COUNTS = {
    "fts_evidence": 0,
    "fts_evidence_config": 1,
    "fts_evidence_content": 0,
    "fts_evidence_data": 2,
    "fts_evidence_docsize": 0,
    "fts_evidence_idx": 0,
}
SCHEMA7_FALLBACK_ROW_COUNTS = {"fts_evidence": 0}


def _fail(message: str) -> None:
    raise TalkComparisonPhase31C3BVerificationError(message)


def _strict_json(raw: bytes, label: str) -> Any:
    def reject(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail(f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=reject, parse_constant=lambda value: _fail(f"non-standard JSON constant in {label}: {value}"))
    except TalkComparisonPhase31C3BVerificationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"invalid JSON in {label}: {exc}")


def _fingerprint(path_value: str | Path) -> dict[str, Any]:
    path = Path(os.path.abspath(os.fspath(path_value)))
    assert_sidecars_absent(path, "during C3B verification fingerprint")
    try:
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            _fail("C3B database must be a regular non-symlink file")
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != identity:
                _fail("C3B database descriptor identity changed")
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            after_open = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after = os.lstat(path)
    except OSError as exc:
        _fail(f"cannot fingerprint C3B database: {exc}")
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    descriptor_identity = (after_open.st_dev, after_open.st_ino, after_open.st_size, after_open.st_mtime_ns)
    if identity != after_identity or identity != descriptor_identity:
        _fail("C3B database changed while fingerprinting")
    assert_sidecars_absent(path, "after C3B verification fingerprint")
    return {"sha256": digest.hexdigest(), "size_bytes": after.st_size, "st_dev": after.st_dev, "st_ino": after.st_ino}


def _read_stable_report(path_value: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = Path(os.path.abspath(os.fspath(path_value)))
    before = _fingerprint(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            wanted = (before["st_dev"], before["st_ino"], before["size_bytes"])
            if (opened.st_dev, opened.st_ino, opened.st_size) != wanted:
                _fail("C3B report identity changed before stable read")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            after_open = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after_path = os.lstat(path)
    except OSError as exc:
        _fail(f"cannot read C3B report stably: {exc}")
    opened_identity = (after_open.st_dev, after_open.st_ino, after_open.st_size)
    if opened_identity != wanted or (after_path.st_dev, after_path.st_ino, after_path.st_size) != wanted:
        _fail("C3B report changed during stable read")
    after = _fingerprint(path)
    if before != after:
        _fail("C3B report changed across stable read")
    value = _strict_json(b"".join(chunks), str(path))
    if not isinstance(value, dict):
        _fail("C3B report must be a JSON object")
    return value, after


def _schema7_row_contract(fts_mode: str) -> dict[str, int]:
    if set(SCHEMA7_CORE_TABLES) != set(SCHEMA7_CORE_ROW_COUNTS):
        _fail("internal Schema 7 core table inventory contract is inconsistent")
    if fts_mode == "fts5":
        if set(SCHEMA7_FTS5_TABLES) != set(SCHEMA7_FTS5_ROW_COUNTS):
            _fail("internal Schema 7 FTS5 table inventory contract is inconsistent")
        result = dict(SCHEMA7_CORE_ROW_COUNTS)
        result.update(SCHEMA7_FTS5_ROW_COUNTS)
        return result
    if fts_mode == "fallback":
        if set(SCHEMA7_FALLBACK_TABLES) != set(SCHEMA7_FALLBACK_ROW_COUNTS):
            _fail("internal Schema 7 fallback table inventory contract is inconsistent")
        result = dict(SCHEMA7_CORE_ROW_COUNTS)
        result.update(SCHEMA7_FALLBACK_ROW_COUNTS)
        return result
    _fail(f"unknown Schema 7 FTS mode: {fts_mode}")


def _verify_table_inventory(conn: sqlite3.Connection, fts_mode: str) -> None:
    expected_counts = _schema7_row_contract(fts_mode)
    expected_names = tuple(sorted(expected_counts))
    actual_names = tuple(
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    )
    if actual_names != expected_names:
        _fail(
            "Schema 7 table inventory mismatch: "
            f"expected {expected_names}, observed {actual_names}"
        )
    for table, expected_count in expected_counts.items():
        actual_count = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        if actual_count != expected_count:
            _fail(
                f"Schema 7 row contract mismatch for {table}: "
                f"{actual_count} != {expected_count}"
            )


def _verify_schema_7(conn: sqlite3.Connection) -> str:
    versions = [int(row[0]) for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")]
    if versions != list(range(1, 8)):
        _fail(f"C3B database must have exact migration ledger 1..7: {versions}")
    migration = Path(__file__).resolve().parents[2] / MIGRATION_PATH
    try:
        migration_sha = sha256_bytes(migration.read_bytes())
    except OSError as exc:
        _fail(f"cannot read C2 migration: {exc}")
    if migration_sha != MIGRATION_SHA256:
        _fail("C2 migration 0007 source seal mismatch")
    snapshot = Database._stage_structural_snapshot(conn)
    mode = str(snapshot["fts"]["mode"])
    expected = {"fts5": "a2839758d6133db02ff470002d9b5117203adab1715cd86dccccc9de00ca0469", "fallback": "891b6d972edcae964371bcb6780ed227ba12cd8f8b54d850d681dad5d2a070f6"}.get(mode)
    if expected is None or Database._stage_structural_signature(conn) != expected:
        _fail("C3B Schema 7 structural seal mismatch")
    migration_times = {str(row[0]) for row in conn.execute("SELECT applied_at FROM schema_migration")}
    if migration_times != {OBSERVED_AT}:
        _fail("C3B migration ledger timestamps are not deterministic")
    _verify_table_inventory(conn, mode)
    return mode


def _scope_json(manifest: Mapping[str, Any], side: str) -> str:
    return canonical(
        {
            "family": FAMILY,
            "family_contract_version": CONTRACT_VERSION,
            "scope_key": SCOPE_KEY,
            "manifest_id": manifest["manifest_id"],
            "compare_run_id": manifest["compare_run"]["compare_run_id"],
            "side": side,
            "source_snapshot_id": SNAPSHOT_ID,
            "selected_file_seals_sha256": manifest["identity_derivation"]["identity_input"]["scope_contract"]["selected_file_seals_sha256"],
            "exact_ingest_run_required": True,
            "evidence_only": True,
            "corpus_complete": False,
        }
    )


def _config_json(manifest: Mapping[str, Any], side: str) -> str:
    parser = side_entry(manifest, side)
    return canonical(
        {
            "manifest_sha256": sha256_bytes(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"),
            "manifest_id": manifest["manifest_id"],
            "side": side,
            "parser_version": parser["parser_version"],
            "mapping_version": parser["mapping_version"],
            "projection_version": parser["projection_version"],
            "matcher_method": MATCHER_METHOD,
            "logical_identity_created": False,
            "full_corpus_scan": False,
            "mcp_provider_added": False,
        }
    )


def _expected_metadata(record: Mapping[str, Any]) -> str:
    return canonical({"physical_occurrence": True, "logical_identity_created": False, "content_textmap_hash": record["content_textmap_hash"], "next_node_ids": record["next_node_ids"]})


def _verify_source_rows(conn: sqlite3.Connection, p: Any) -> tuple[str, dict[str, str], dict[tuple[str, str], str]]:
    source = p.manifest["source_snapshot"]
    wanted_catalog = (
        str(source["source_id"]),
        "primary",
        "https://github.com/DimbreathBot/AnimeGameData",
        None,
    )
    catalog_rows = [
        tuple(row)
        for row in conn.execute(
            "SELECT id,kind,url,license_note FROM source_catalog ORDER BY id"
        )
    ]
    if catalog_rows != [wanted_catalog]:
        _fail("source_catalog full row contract or exact total changed")
    wanted_snapshot = (
        SNAPSHOT_ID,
        source["source_id"],
        source["release_label"],
        source["build"],
        source["commit"],
        source["tree"],
        OBSERVED_AT,
        "legacy-unscoped",
        "legacy-unscoped",
        "active",
        canonical({"parser_independent": True, "pinned_source": True}),
    )
    snapshot_rows = [
        tuple(row)
        for row in conn.execute(
            'SELECT id,source_id,release_label,build,"commit",tree,observed_at,parser_version,mapping_version,status,metadata_json '
            "FROM source_snapshot ORDER BY id"
        )
    ]
    if snapshot_rows != [wanted_snapshot]:
        _fail("source_snapshot full row contract or exact total changed")
    file_ids: dict[str, str] = {}
    for item in p.source["files"]:
        relative = str(item["relative_path"])
        file_id = digest_id("sf", SNAPSHOT_ID, relative)
        file_ids[relative] = file_id
        row = conn.execute("SELECT id,snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path FROM source_file WHERE id=?", (file_id,)).fetchone()
        wanted = (file_id, SNAPSHOT_ID, relative, item["git_blob"], item["sha256"], int(item["size_bytes"]), Path(relative).suffix.lstrip(".") or "json", str(p.source_root))
        if row is None or tuple(row) != wanted:
            _fail(f"source_file provenance mismatch: {relative}")
    if int(conn.execute("SELECT COUNT(*) FROM source_file").fetchone()[0]) != 7:
        _fail("C3B database contains foreign source files")
    record_ids: dict[tuple[str, str], str] = {}
    talk_relative = str(p.source["files"][0]["relative_path"])
    talk_record_id = digest_id("sr", file_ids[talk_relative], "/")
    talk_raw_json = canonical(p.source["talk_value"])
    wanted_talk = (file_ids[talk_relative], "/", "talk_root", TALK_ID, sha256_bytes(talk_raw_json.encode("utf-8")), talk_raw_json)
    row = conn.execute("SELECT id,source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json FROM source_record WHERE id=?", (talk_record_id,)).fetchone()
    if row is None or tuple(row) != (talk_record_id, *wanted_talk):
        _fail("Talk source record provenance mismatch")
    record_ids[(talk_relative, "/")] = talk_record_id
    for text in p.source["texts"]:
        relative, pointer = str(text["relative_path"]), str(text["source_pointer"])
        record_id = digest_id("sr", file_ids[relative], pointer)
        record_ids[(relative, pointer)] = record_id
        raw_json = canonical(text["raw_text"])
        wanted = (file_ids[relative], pointer, "textmap_entry", text["textmap_hash"], sha256_bytes(raw_json.encode("utf-8")), raw_json)
        row = conn.execute("SELECT id,source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json FROM source_record WHERE id=?", (record_id,)).fetchone()
        if row is None or tuple(row) != (record_id, *wanted):
            _fail(f"TextMap source record provenance mismatch: {relative} {pointer}")
    if int(conn.execute("SELECT COUNT(*) FROM source_record").fetchone()[0]) != len(record_ids):
        _fail("C3B database contains foreign source records")
    return talk_record_id, file_ids, record_ids


def _verify_side(conn: sqlite3.Connection, p: Any, side: str, talk_record_id: str, file_ids: Mapping[str, str], record_ids: Mapping[tuple[str, str], str]) -> dict[str, Any]:
    parser = side_entry(p.manifest, side)
    ingest = ingest_entry(p.manifest, side)
    run_id = str(ingest["ingest_run_id"])
    parser_id = str(parser["parser_build_id"])
    parser_row = conn.execute("SELECT id,parser_version,mapping_version,fingerprint,config_checksum,created_at FROM parser_build WHERE id=?", (parser_id,)).fetchone()
    if parser_row is None or tuple(parser_row) != (parser_id, parser["parser_version"], parser["mapping_version"], f"talk-comparison-pilot-controlled-replay-{side}", parser["config_checksum"], OBSERVED_AT):
        _fail(f"{side} parser build provenance mismatch")
    run = conn.execute("SELECT id,snapshot_id,parser_build_id,scope_json,config_json,status,created_at FROM ingest_run WHERE id=?", (run_id,)).fetchone()
    if run is None or tuple(run) != (run_id, SNAPSHOT_ID, parser_id, _scope_json(p.manifest, side), _config_json(p.manifest, side), "complete", OBSERVED_AT):
        _fail(f"{side} ingest run provenance/status mismatch")
    container_id = occurrence_id("talk-container", run_id, SNAPSHOT_ID, "BinOutput/Talk/Quest/79661eb8.json")
    container = conn.execute("SELECT id,ingest_run_id,source_record_id,kind,namespace,talk_id,raw_checksum,normalized_checksum,metadata_json FROM evidence_container WHERE id=?", (container_id,)).fetchone()
    wanted_container = (container_id, run_id, talk_record_id, "talk_root", NAMESPACE, TALK_ID, p.source["talk_raw_sha256"], p.manifest["source_scope"]["candidate"]["normalized_checksum"], canonical({"family": FAMILY, "family_contract_version": CONTRACT_VERSION, "scope_key": SCOPE_KEY, "physical_occurrence": True, "logical_identity_created": False}))
    if container is None or tuple(container) != wanted_container:
        _fail(f"{side} evidence container mismatch")
    occurrence_ids: dict[tuple[str, str, str, str], str] = {}
    passages = sorted(
        conn.execute("SELECT passage_occurrence_id,evidence_container_id,source_record_id,source_pointer,namespace,talk_id,node_id,sequence_key,raw_checksum,normalized_checksum,metadata_json FROM passage_occurrence WHERE ingest_run_id=?", (run_id,)).fetchall(),
        key=lambda row: int(str(row[3]).rsplit("/", 1)[1]),
    )
    if len(passages) != 14:
        _fail(f"{side} passage occurrence count is not 14")
    for row, record in zip(passages, p.source["records"]):
        coordinate = (NAMESPACE, TALK_ID, str(record["node_id"]), str(record["source_pointer"]))
        expected_id = occurrence_id("talk-passage", run_id, *coordinate)
        expected = (expected_id, container_id, talk_record_id, record["source_pointer"], NAMESPACE, TALK_ID, record["node_id"], f"{int(record['index']):04d}", record["raw_record_sha256"], record["raw_record_sha256"], _expected_metadata(record))
        if tuple(row) != expected:
            _fail(f"{side} passage occurrence mismatch at {record['source_pointer']}")
        occurrence_ids[coordinate] = expected_id
    text_by_key = {(str(item["node_id"]), str(item["language"])): item for item in p.source["texts"]}
    texts = conn.execute("SELECT id,passage_occurrence_id,evidence_container_id,language,raw_text,normalized_text,raw_checksum,normalized_checksum,source_record_id,source_start,source_end FROM passage_occurrence_text WHERE ingest_run_id=? ORDER BY passage_occurrence_id,language", (run_id,)).fetchall()
    if len(texts) != 42:
        _fail(f"{side} text occurrence count is not 42")
    text_tuples: list[tuple[Any, ...]] = []
    for row in texts:
        passage_row = conn.execute("SELECT source_pointer,node_id FROM passage_occurrence WHERE passage_occurrence_id=? AND ingest_run_id=?", (row[1], run_id)).fetchone()
        if passage_row is None:
            _fail(f"{side} text points to an unknown passage")
        text = text_by_key[(str(passage_row[1]), str(row[3]))]
        expected_passage = occurrence_ids[(NAMESPACE, TALK_ID, str(passage_row[1]), str(passage_row[0]))]
        expected_id = occurrence_id("talk-passage-text", run_id, expected_passage, row[3])
        raw_checksum = sha256_bytes(str(text["raw_text"]).encode("utf-8"))
        normalized_checksum = sha256_bytes(str(text["normalized_text"]).encode("utf-8"))
        expected = (expected_id, expected_passage, container_id, text["language"], text["raw_text"], text["normalized_text"], raw_checksum, normalized_checksum, record_ids[(str(text["relative_path"]), str(text["source_pointer"]))], None, None)
        if tuple(row) != expected:
            _fail(f"{side} text occurrence mismatch")
        text_tuples.append((str(passage_row[0]), row[3], row[4], row[5], row[6], row[7]))
    edges = conn.execute("SELECT id,evidence_container_id,from_passage_occurrence_id,to_passage_occurrence_id,edge_type,metadata_json FROM occurrence_edge WHERE ingest_run_id=? ORDER BY id", (run_id,)).fetchall()
    if len(edges) != 14:
        _fail(f"{side} edge occurrence count is not 14")
    edge_tuples: list[tuple[Any, ...]] = []
    for edge, expected_edge in zip(edges, sorted(p.source["edges"], key=lambda item: occurrence_id("talk-edge", run_id, container_id, occurrence_ids[(NAMESPACE, TALK_ID, str(item["from_node_id"]), str(item["from_source_pointer"]))], occurrence_ids[(NAMESPACE, TALK_ID, str(item["to_node_id"]), str(item["to_source_pointer"]))], item["edge_type"]))):
        from_id = occurrence_ids[(NAMESPACE, TALK_ID, str(expected_edge["from_node_id"]), str(expected_edge["from_source_pointer"]))]
        to_id = occurrence_ids[(NAMESPACE, TALK_ID, str(expected_edge["to_node_id"]), str(expected_edge["to_source_pointer"]))]
        expected_id = occurrence_id("talk-edge", run_id, container_id, from_id, to_id, expected_edge["edge_type"])
        expected = (expected_id, container_id, from_id, to_id, expected_edge["edge_type"], canonical({"physical_occurrence": True, "source_edge": expected_edge}))
        if tuple(edge) != expected:
            _fail(f"{side} edge occurrence mismatch")
        edge_tuples.append((expected_edge["from_source_pointer"], expected_edge["to_source_pointer"], edge[4], edge[5]))
    text_tuples.sort(key=lambda item: (str(item[0]), str(item[1])))
    edge_tuples.sort(key=lambda item: (str(item[0]), str(item[1]), str(item[2])))
    return {"side": side, "run_id": run_id, "parser_id": parser_id, "container_id": container_id, "occurrence_ids": occurrence_ids, "passage_tuples": [(row[3], row[4], row[6], row[7], row[8], row[9], row[10]) for row in passages], "text_tuples": text_tuples, "edge_tuples": edge_tuples}


def _verify_comparison(conn: sqlite3.Connection, p: Any, sides: Mapping[str, Mapping[str, Any]]) -> None:
    manifest = p.manifest
    compare_id = str(manifest["compare_run"]["compare_run_id"])
    matcher = manifest["matcher"]
    matcher_row = conn.execute("SELECT matcher_kind,algorithm_version,config_checksum,metadata_json,created_at FROM matcher_build WHERE id=?", (matcher["matcher_build_id"],)).fetchone()
    wanted_matcher = (matcher["matcher_kind"], matcher["algorithm_version"], matcher["config_checksum"], canonical({"family": FAMILY, "contract_version": CONTRACT_VERSION, "manifest_id": manifest["manifest_id"], "hypothesis_only": True}), OBSERVED_AT)
    if matcher_row is None or tuple(matcher_row) != wanted_matcher:
        _fail("matcher build row mismatch")
    compare_row = conn.execute("SELECT matcher_build_id,family,status,started_at,completed_at,metadata_json FROM compare_run WHERE id=?", (compare_id,)).fetchone()
    wanted_compare = (matcher["matcher_build_id"], FAMILY, "complete", OBSERVED_AT, OBSERVED_AT, canonical({"family_contract_version": CONTRACT_VERSION, "scope_key": SCOPE_KEY, "manifest_id": manifest["manifest_id"], "source_snapshot_id": SNAPSHOT_ID, "matcher_method": MATCHER_METHOD, "evidence_only": True, "corpus_complete": False, "logical_identity_created": False}))
    if compare_row is None or tuple(compare_row) != wanted_compare:
        _fail("complete compare_run identity/status mismatch")
    side_rows = [tuple(row) for row in conn.execute("SELECT side,ingest_run_id FROM comparison_side WHERE compare_run_id=? ORDER BY side", (compare_id,))]
    if side_rows != [("baseline", sides["baseline"]["run_id"]), ("target", sides["target"]["run_id"])]:
        _fail("comparison sides are not the exact baseline/target runs")
    for side in SIDES:
        proof = proof_entry(manifest, side)
        proof_artifact = proof_artifact_entry(manifest, side)
        row = conn.execute("SELECT side,ingest_run_id,family,scope_key,scope_json,inventory_manifest_id,inventory_manifest_checksum,coverage_proof_id,coverage_proof_checksum,proof_algorithm_version,inventory_complete,expected_count,observed_count,parser_status,extraction_status,coverage_status,proof_status,metadata_json FROM comparison_scope_proof WHERE compare_run_id=? AND side=?", (compare_id, side)).fetchone()
        metadata = canonical({"proof_artifact_id": proof["proof_artifact_id"], "proof_artifact_checksum": proof["proof_artifact_checksum"], "selected_file_seals": proof_artifact["selected_file_seals"], "source_output": manifest["outputs"], "controlled_replay": True, "provenance_side": side})
        scope = canonical({"family": FAMILY, "family_contract_version": CONTRACT_VERSION, "scope_key": SCOPE_KEY, "side": side, "source_snapshot_id": SNAPSHOT_ID, "selected_file_seals_sha256": proof["selected_file_seals_sha256"], "raw_output": manifest["outputs"]["raw"], "text_output": manifest["outputs"]["text"], "graph_output": manifest["outputs"]["graph"]})
        expected = (side, sides[side]["run_id"], FAMILY, SCOPE_KEY, scope, manifest["source_scope"]["manifest_id"], manifest["source_scope"]["sha256"], proof["proof_artifact_id"], proof["proof_artifact_checksum"], PROOF_ALGORITHM, 1, 14, 14, "passed", "complete", "complete", "verified", metadata)
        if row is None or tuple(row) != expected:
            _fail(f"{side} scope proof mismatch")
    records = p.source["records"]
    for record in records:
        coordinate = (NAMESPACE, TALK_ID, str(record["node_id"]), str(record["source_pointer"]))
        match_set_id = occurrence_id("talk-match-set", compare_id, canonical({"namespace": coordinate[0], "talk_id": coordinate[1], "node_id": coordinate[2], "source_pointer": coordinate[3]}))
        base_id = sides["baseline"]["occurrence_ids"][coordinate]
        target_id = sides["target"]["occurrence_ids"][coordinate]
        base_container, target_container = sides["baseline"]["container_id"], sides["target"]["container_id"]
        provenance = canonical({"compare_run_id": compare_id, "source_snapshot_id": SNAPSHOT_ID, "scope_key": SCOPE_KEY, "coordinate_match": True, "logical_identity_created": False})
        row = conn.execute("SELECT resolution_status,method,algorithm_version,justification,provenance FROM comparison_match_set WHERE id=? AND compare_run_id=?", (match_set_id, compare_id)).fetchone()
        expected = ("resolved", MATCHER_METHOD, MATCHER_METHOD, "exact physical occurrence coordinate hypothesis with bilateral proof", provenance)
        if row is None or tuple(row) != expected:
            _fail("match set mismatch")
        anchors = [tuple(item) for item in conn.execute("SELECT side,ingest_run_id,passage_occurrence_id,evidence_container_id FROM comparison_match_anchor WHERE match_set_id=? AND compare_run_id=? ORDER BY side", (match_set_id, compare_id))]
        if anchors != [("baseline", sides["baseline"]["run_id"], base_id, base_container), ("target", sides["target"]["run_id"], target_id, target_container)]:
            _fail("match anchor mismatch")
        tuple_json = {"namespace": coordinate[0], "talk_id": coordinate[1], "node_id": coordinate[2], "source_pointer": coordinate[3]}
        candidate_id = occurrence_id("talk-match-candidate", compare_id, match_set_id, canonical(tuple_json))
        candidate = conn.execute("SELECT candidate_kind,baseline_passage_occurrence_id,baseline_ingest_run_id,baseline_evidence_container_id,target_passage_occurrence_id,target_ingest_run_id,target_evidence_container_id,rank,confidence,features_json,justification,provenance FROM comparison_match_candidate WHERE id=? AND match_set_id=? AND compare_run_id=?", (candidate_id, match_set_id, compare_id)).fetchone()
        expected_candidate = ("pair", base_id, sides["baseline"]["run_id"], base_container, target_id, sides["target"]["run_id"], target_container, 1, 1.0, canonical({"method": MATCHER_METHOD, "candidate_tuple": tuple_json, "hypothesis_only": True}), "unique candidate within the exact compare run and coordinate scope", provenance)
        if candidate is None or tuple(candidate) != expected_candidate:
            _fail("pair candidate mismatch")
        decision_id = occurrence_id("talk-match-decision", compare_id, match_set_id, candidate_id)
        decision = conn.execute("SELECT candidate_id,decision,justification,provenance,created_at FROM comparison_match_decision WHERE id=? AND match_set_id=? AND compare_run_id=?", (decision_id, match_set_id, compare_id)).fetchone()
        expected_decision = (candidate_id, "accepted", "accepted only after two verified same-scope proofs and one pair candidate", provenance, OBSERVED_AT)
        if decision is None or tuple(decision) != expected_decision:
            _fail("accepted decision mismatch")
        observation_id = occurrence_id("talk-change-observation", compare_id, match_set_id)
        observation = conn.execute("SELECT presence_outcome,observation_status,absence_assertion_id,absence_missing_side,evidence_json,provenance FROM comparison_change_observation WHERE id=? AND match_set_id=? AND compare_run_id=?", (observation_id, match_set_id, compare_id)).fetchone()
        expected_observation = ("present_both", "observed", None, None, canonical({"raw_output_tuple_equal": True, "text_output_tuple_equal": True, "graph_output_tuple_equal": True, "source_snapshot_equal": True, "parser_build_provenance_distinct": True, "ingest_run_provenance_distinct": True, "match_hypothesis_only": True}), provenance)
        if observation is None or tuple(observation) != expected_observation:
            _fail("present_both observation mismatch")
        labels = [str(item[0]) for item in conn.execute("SELECT label FROM comparison_change_label WHERE observation_id=? AND match_set_id=? AND compare_run_id=? ORDER BY label", (observation_id, match_set_id, compare_id))]
        if labels != ["parser_delta", "provenance_only"]:
            _fail("comparison labels are not the exact C3B pair")
        for label in labels:
            provenance_row = conn.execute("SELECT provenance FROM comparison_change_label WHERE observation_id=? AND match_set_id=? AND compare_run_id=? AND label=?", (observation_id, match_set_id, compare_id, label)).fetchone()
            if provenance_row is None or tuple(provenance_row) != (canonical({"label_is_observation": True, "method": MATCHER_METHOD, "provenance": "controlled_replay"}),):
                _fail("comparison label provenance mismatch")
    counts = {
        "match_sets": int(conn.execute("SELECT COUNT(*) FROM comparison_match_set").fetchone()[0]),
        "pair_candidates": int(conn.execute("SELECT COUNT(*) FROM comparison_match_candidate WHERE candidate_kind='pair'").fetchone()[0]),
        "accepted_decisions": int(conn.execute("SELECT COUNT(*) FROM comparison_match_decision WHERE decision='accepted'").fetchone()[0]),
        "present_both_observations": int(conn.execute("SELECT COUNT(*) FROM comparison_change_observation WHERE presence_outcome='present_both'").fetchone()[0]),
        "parser_delta_labels": int(conn.execute("SELECT COUNT(*) FROM comparison_change_label WHERE label='parser_delta'").fetchone()[0]),
        "provenance_only_labels": int(conn.execute("SELECT COUNT(*) FROM comparison_change_label WHERE label='provenance_only'").fetchone()[0]),
    }
    if counts != {"match_sets": 14, "pair_candidates": 14, "accepted_decisions": 14, "present_both_observations": 14, "parser_delta_labels": 14, "provenance_only_labels": 14}:
        _fail(f"C3B comparison counts changed: {counts}")
    if int(conn.execute("SELECT COUNT(*) FROM comparison_absence_assertion").fetchone()[0]) != 0:
        _fail("C3B must not create absence assertions for present_both results")
    forbidden = ("raw_changed", "text_changed", "structure_changed", "graph_changed", "timing_changed")
    if conn.execute("SELECT 1 FROM comparison_change_label WHERE label IN (?,?,?,?,?) LIMIT 1", forbidden).fetchone() is not None:
        _fail("forbidden comparison label is present")


def _counts(conn: sqlite3.Connection, fts_mode: str | None = None) -> dict[str, int]:
    mode = fts_mode or str(Database._stage_structural_snapshot(conn)["fts"]["mode"])
    return {
        table: int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        for table in _schema7_row_contract(mode)
    }


def verify_open_connection(conn: sqlite3.Connection, p: Any) -> dict[str, Any]:
    fts_mode = _verify_schema_7(conn)
    talk_record_id, file_ids, record_ids = _verify_source_rows(conn, p)
    sides = {side: _verify_side(conn, p, side, talk_record_id, file_ids, record_ids) for side in SIDES}
    if sides["baseline"]["passage_tuples"] != sides["target"]["passage_tuples"] or sides["baseline"]["text_tuples"] != sides["target"]["text_tuples"] or sides["baseline"]["edge_tuples"] != sides["target"]["edge_tuples"]:
        _fail("baseline and target raw/text/graph evidence are not occurrence-equivalent")
    if sides["baseline"]["run_id"] == sides["target"]["run_id"] or sides["baseline"]["parser_id"] == sides["target"]["parser_id"]:
        _fail("baseline and target provenance must be distinct")
    _verify_comparison(conn, p, sides)
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        _fail("C3B foreign-key check failed")
    return {"status": "complete", "schema_version": 7, "family": FAMILY, "family_contract_version": CONTRACT_VERSION, "coverage": "bounded_comparison_pilot", "corpus_complete": False, "evidence_only": True, "counts": _counts(conn, fts_mode), "compare_run_id": p.manifest["compare_run"]["compare_run_id"], "source_snapshot_id": SNAPSHOT_ID, "scope_key": SCOPE_KEY}


def _expected_build_report(
    p: Any,
    result: Mapping[str, Any],
    fingerprint: Mapping[str, Any],
    database_path: Path,
) -> dict[str, Any]:
    return {
        "status": "complete",
        "phase": "3.1C3B",
        "schema_version": 7,
        "family": FAMILY,
        "family_contract_version": CONTRACT_VERSION,
        "coverage": "bounded_comparison_pilot",
        "corpus_complete": False,
        "evidence_only": True,
        "source_snapshot_id": SNAPSHOT_ID,
        "scope_key": SCOPE_KEY,
        "compare_run_id": p.manifest["compare_run"]["compare_run_id"],
        "matcher_build_id": p.manifest["matcher"]["matcher_build_id"],
        "parser_build_ids": {side: side_entry(p.manifest, side)["parser_build_id"] for side in SIDES},
        "ingest_run_ids": {side: ingest_entry(p.manifest, side)["ingest_run_id"] for side in SIDES},
        "counts": result["counts"],
        "expected_result": p.manifest["expected_result"],
        "output_equivalence": p.manifest["output_equivalence"],
        "database_sha256": fingerprint["sha256"],
        "database_size_bytes": fingerprint["size_bytes"],
        "database_path": str(database_path.resolve()),
        "database_artifact_tracked": False,
        "mcp_change": False,
        "logical_identity_created": False,
        "exact_replay": "fresh target required; rerun to a new target must be byte-exact; existing target is fail-closed",
    }


def verify(
    database_path: str | Path,
    report_path: str | Path | None,
    source_root: str | Path,
    repo_root: str | Path,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    paths = assert_path_matrix(
        {"database": database_path, "source_root": source_root, "repo_root": repo_root, "manifest": manifest_path or Path(repo_root) / MANIFEST_PATH, **({"report": report_path} if report_path is not None else {})},
        protected_database_names=("database",), input_files=("database", "manifest") + (("report",) if report_path is not None else ()), input_directories=("source_root", "repo_root"),
    )
    p_manifest = load_manifest(paths["repo_root"], paths["manifest"])
    verify_repository_inputs(paths["repo_root"], p_manifest)
    # The source derivation is performed here, not read from the build report.
    from types import SimpleNamespace
    p = SimpleNamespace(source_root=paths["source_root"], repo_root=paths["repo_root"], manifest_path=paths["manifest"], manifest=p_manifest, source=derive_source_evidence(paths["source_root"], p_manifest), repository_inputs={})
    before = _fingerprint(paths["database"])
    try:
        with sqlite3.connect(paths["database"].as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            result = verify_open_connection(conn, p)
    except sqlite3.Error as exc:
        _fail(f"cannot open C3B database read-only: {exc}")
    after = _fingerprint(paths["database"])
    if before != after:
        _fail("C3B database changed during verification")
    expected_report = _expected_build_report(p, result, after, paths["database"])
    result.update({"database_sha256": after["sha256"], "database_size_bytes": after["size_bytes"], "database_path": str(paths["database"].resolve()), "database_artifact_tracked": False})
    if report_path is not None:
        report, report_fingerprint = _read_stable_report(paths["report"])
        if report != expected_report:
            _fail("C3B build report does not exactly match independently reconstructed report")
        if _fingerprint(paths["report"]) != report_fingerprint:
            _fail("C3B build report changed during exact verification")
    return result


__all__ = ["TalkComparisonPhase31C3BVerificationError", "verify", "verify_open_connection"]
