"""Atomic builder for the Phase 3.1C3B Talk comparison pilot.

The pilot is intentionally a standalone Schema-7 database.  It replays the
same pinned 7.0 source scope twice, with distinct parser/run provenance, and
stores only physical evidence and comparison hypotheses.  It does not touch
the accepted Schema 13/14 artifacts and it does not expose an MCP surface.
"""

from __future__ import annotations

from .platform_io import lock_exclusive, unlock
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .db import Database
from .phase4e5_combined_gate import assert_path_matrix
from .talk_comparison_phase31c3b_contract import (
    CONTRACT_VERSION,
    FAMILY,
    MANIFEST_PATH,
    MATCHER_METHOD,
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
    json_bytes,
    load_manifest,
    occurrence_id,
    proof_entry,
    proof_artifact_entry,
    sha256_bytes,
    side_entry,
    verify_repository_inputs,
)
from .util import digest_id
from .world_archive_phase4e2_promotion import assert_sidecars_absent, sync_directory


class TalkComparisonPhase31C3BBuilderError(TalkComparisonPhase31C3BError):
    """The C3B input, stage, or atomic promotion is invalid."""


@dataclass(frozen=True)
class Preflight:
    source_root: Path
    repo_root: Path
    manifest_path: Path
    manifest: dict[str, Any]
    source: dict[str, Any]
    repository_inputs: dict[str, Any]


_PROMOTION_TEST_HOOK = None
_BEFORE_REPORT_REPLACE_TEST_HOOK = None
_POST_REPLACE_TEST_HOOK = None


def _fail(message: str) -> None:
    raise TalkComparisonPhase31C3BBuilderError(message)


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
            "manifest_sha256": sha256_bytes(json_bytes(manifest)),
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


def preflight(
    source_root: str | Path,
    repo_root: str | Path,
    manifest_path: str | Path | None = None,
) -> Preflight:
    source = Path(source_root).resolve()
    repo = Path(repo_root).resolve()
    manifest = Path(manifest_path or repo / MANIFEST_PATH).resolve()
    value = load_manifest(repo, manifest)
    repository_inputs = verify_repository_inputs(repo, value)
    derived = derive_source_evidence(source, value)
    migration = repository_inputs["migration"]
    if migration["sha256"] != MIGRATION_SHA256:
        _fail("C3A migration seal mismatch")
    return Preflight(source, repo, manifest, value, derived, repository_inputs)


def _fingerprint(path_value: str | Path) -> dict[str, Any]:
    path = Path(os.path.abspath(os.fspath(path_value)))
    assert_sidecars_absent(path, "during C3B fingerprint")
    try:
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            _fail("C3B artifact must be a regular non-symlink file")
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != identity:
                _fail("C3B artifact descriptor identity changed")
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
        _fail(f"cannot fingerprint C3B artifact: {exc}")
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    descriptor_identity = (after_open.st_dev, after_open.st_ino, after_open.st_size, after_open.st_mtime_ns)
    if identity != after_identity or identity != descriptor_identity:
        _fail("C3B artifact changed while fingerprinting")
    assert_sidecars_absent(path, "after C3B fingerprint")
    return {
        "sha256": digest.hexdigest(),
        "size_bytes": after.st_size,
        "st_dev": after.st_dev,
        "st_ino": after.st_ino,
        "st_mode": stat.S_IMODE(after.st_mode),
    }


def _source_ids(conn: sqlite3.Connection, p: Preflight) -> tuple[str, dict[str, str], dict[tuple[str, str], str]]:
    source = p.manifest["source_snapshot"]
    source_id = str(source["source_id"])
    row = conn.execute(
        "SELECT kind,url,license_note FROM source_catalog WHERE id=?", (source_id,)
    ).fetchone()
    wanted_catalog = ("primary", "https://github.com/DimbreathBot/AnimeGameData", None)
    # The URL is provenance metadata only.  Keep the existing source catalog
    # value when present, but reject a conflicting kind or URL.
    if row is None:
        conn.execute(
            "INSERT INTO source_catalog(id,kind,url,license_note) VALUES(?,?,?,?)",
            (source_id, "primary", "https://github.com/DimbreathBot/AnimeGameData", None),
        )
    elif tuple(row) != wanted_catalog:
        raise sqlite3.IntegrityError("anime-game-data source catalog mismatch")
    snapshot_row = conn.execute(
        'SELECT source_id,release_label,build,"commit",tree,observed_at,status,metadata_json FROM source_snapshot WHERE id=?',
        (SNAPSHOT_ID,),
    ).fetchone()
    wanted_snapshot = (
        source_id, source["release_label"], source["build"], source["commit"],
        source["tree"], OBSERVED_AT, "active", canonical({"parser_independent": True, "pinned_source": True}),
    )
    if snapshot_row is None:
        conn.execute(
            'INSERT INTO source_snapshot(id,source_id,release_label,build,"commit",tree,observed_at,parser_version,mapping_version,status,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (SNAPSHOT_ID, *wanted_snapshot[:6], "legacy-unscoped", "legacy-unscoped", wanted_snapshot[6], wanted_snapshot[7]),
        )
    elif tuple(snapshot_row) != wanted_snapshot:
        raise sqlite3.IntegrityError("parser-independent source snapshot mismatch")
    file_ids: dict[str, str] = {}
    for item in p.source["files"]:
        relative = str(item["relative_path"])
        file_id = digest_id("sf", SNAPSHOT_ID, relative)
        file_ids[relative] = file_id
        wanted = (
            SNAPSHOT_ID, relative, item["git_blob"], item["sha256"], int(item["size_bytes"]),
            Path(relative).suffix.lstrip(".") or "json", str(p.source_root),
        )
        row = conn.execute(
            "SELECT snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path FROM source_file WHERE id=?",
            (file_id,),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO source_file(id,snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path) VALUES(?,?,?,?,?,?,?,?)",
                (file_id, *wanted),
            )
        elif tuple(row) != wanted:
            raise sqlite3.IntegrityError(f"source_file mismatch: {relative}")
    records: dict[tuple[str, str], str] = {}
    talk_file = p.source["file_by_path"][p.source["files"][0]["relative_path"]]
    talk_file_id = file_ids[str(talk_file["relative_path"])]
    talk_record_id = digest_id("sr", talk_file_id, "/")
    records[(str(talk_file["relative_path"]), "/")] = talk_record_id
    talk_raw_json = canonical(p.source["talk_value"])
    row = conn.execute(
        "SELECT source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json FROM source_record WHERE id=?",
        (talk_record_id,),
    ).fetchone()
    wanted_talk_record = (talk_file_id, "/", "talk_root", TALK_ID, sha256_bytes(talk_raw_json.encode("utf-8")), talk_raw_json)
    if row is None:
        conn.execute(
            "INSERT INTO source_record(id,source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json) VALUES(?,?,?,?,?,?,?)",
            (talk_record_id, *wanted_talk_record),
        )
    elif tuple(row) != wanted_talk_record:
        raise sqlite3.IntegrityError("Talk source record mismatch")
    for text in p.source["texts"]:
        relative = str(text["relative_path"])
        pointer = str(text["source_pointer"])
        file_id = file_ids[relative]
        record_id = digest_id("sr", file_id, pointer)
        records[(relative, pointer)] = record_id
        raw_json = canonical(text["raw_text"])
        wanted_record = (
            file_id, pointer, "textmap_entry", text["textmap_hash"],
            sha256_bytes(raw_json.encode("utf-8")), raw_json,
        )
        row = conn.execute(
            "SELECT source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json FROM source_record WHERE id=?",
            (record_id,),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO source_record(id,source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json) VALUES(?,?,?,?,?,?,?)",
                (record_id, *wanted_record),
            )
        elif tuple(row) != wanted_record:
            raise sqlite3.IntegrityError(f"TextMap source record mismatch: {relative} {pointer}")
    return talk_record_id, file_ids, records


def _write_side(
    conn: sqlite3.Connection,
    p: Preflight,
    side: str,
    talk_record_id: str,
    file_ids: Mapping[str, str],
    record_ids: Mapping[tuple[str, str], str],
) -> dict[str, Any]:
    manifest = p.manifest
    parser = side_entry(manifest, side)
    ingest = ingest_entry(manifest, side)
    proof = proof_entry(manifest, side)
    proof_artifact = proof_artifact_entry(manifest, side)
    parser_id = str(parser["parser_build_id"])
    run_id = str(ingest["ingest_run_id"])
    conn.execute(
        "INSERT INTO parser_build(id,parser_version,mapping_version,fingerprint,config_checksum,created_at) VALUES(?,?,?,?,?,?)",
        (
            parser_id, parser["parser_version"], parser["mapping_version"],
            f"talk-comparison-pilot-controlled-replay-{side}", parser["config_checksum"], OBSERVED_AT,
        ),
    )
    conn.execute(
        "INSERT INTO ingest_run(id,snapshot_id,parser_build_id,scope_json,config_json,status,created_at) VALUES(?,?,?,?,?,'complete',?)",
        (run_id, SNAPSHOT_ID, parser_id, _scope_json(manifest, side), _config_json(manifest, side), OBSERVED_AT),
    )
    container_id = occurrence_id("talk-container", run_id, SNAPSHOT_ID, "BinOutput/Talk/Quest/79661eb8.json")
    talk_raw_sha = str(p.source["talk_raw_sha256"])
    talk_norm_sha = str(manifest["source_scope"]["candidate"]["normalized_checksum"])
    conn.execute(
        "INSERT INTO evidence_container(id,ingest_run_id,source_record_id,kind,namespace,talk_id,raw_checksum,normalized_checksum,metadata_json) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            container_id, run_id, talk_record_id, "talk_root", NAMESPACE, TALK_ID,
            talk_raw_sha, talk_norm_sha,
            canonical({"family": FAMILY, "family_contract_version": CONTRACT_VERSION, "scope_key": SCOPE_KEY, "physical_occurrence": True, "logical_identity_created": False}),
        ),
    )
    occurrence_by_coordinate: dict[tuple[str, str, str, str], tuple[str, str]] = {}
    for record in p.source["records"]:
        pointer = str(record["source_pointer"])
        node_id = str(record["node_id"])
        coordinate = (NAMESPACE, TALK_ID, node_id, pointer)
        passage_id = occurrence_id("talk-passage", run_id, *coordinate)
        conn.execute(
            "INSERT INTO passage_occurrence(passage_occurrence_id,ingest_run_id,evidence_container_id,source_record_id,source_pointer,namespace,talk_id,node_id,sequence_key,raw_checksum,normalized_checksum,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                passage_id, run_id, container_id, talk_record_id, pointer, NAMESPACE, TALK_ID,
                node_id, f"{int(record['index']):04d}", record["raw_record_sha256"], record["raw_record_sha256"],
                canonical({"physical_occurrence": True, "logical_identity_created": False, "content_textmap_hash": record["content_textmap_hash"], "next_node_ids": record["next_node_ids"]}),
            ),
        )
        occurrence_by_coordinate[coordinate] = (passage_id, container_id)
    text_by_node = {(str(item["node_id"]), str(item["language"])): item for item in p.source["texts"]}
    text_count = 0
    for record in p.source["records"]:
        coordinate = (NAMESPACE, TALK_ID, str(record["node_id"]), str(record["source_pointer"]))
        passage_id, _ = occurrence_by_coordinate[coordinate]
        for language in ("CHS", "EN", "JP"):
            text = text_by_node[(str(record["node_id"]), language)]
            text_id = occurrence_id("talk-passage-text", run_id, passage_id, language)
            source_record_id = record_ids[(str(text["relative_path"]), str(text["source_pointer"]))]
            raw_checksum = sha256_bytes(str(text["raw_text"]).encode("utf-8"))
            normalized_checksum = sha256_bytes(str(text["normalized_text"]).encode("utf-8"))
            conn.execute(
                "INSERT INTO passage_occurrence_text(id,passage_occurrence_id,ingest_run_id,evidence_container_id,language,raw_text,normalized_text,raw_checksum,normalized_checksum,source_record_id,source_start,source_end) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    text_id, passage_id, run_id, container_id, language, text["raw_text"], text["normalized_text"],
                    raw_checksum, normalized_checksum, source_record_id, None, None,
                ),
            )
            text_count += 1
    for edge in p.source["edges"]:
        from_coordinate = (NAMESPACE, TALK_ID, str(edge["from_node_id"]), str(edge["from_source_pointer"]))
        to_coordinate = (NAMESPACE, TALK_ID, str(edge["to_node_id"]), str(edge["to_source_pointer"]))
        from_id = occurrence_by_coordinate[from_coordinate][0]
        to_id = occurrence_by_coordinate[to_coordinate][0]
        edge_id = occurrence_id("talk-edge", run_id, container_id, from_id, to_id, edge["edge_type"])
        conn.execute(
            "INSERT INTO occurrence_edge(id,ingest_run_id,evidence_container_id,from_passage_occurrence_id,to_passage_occurrence_id,edge_type,metadata_json) VALUES(?,?,?,?,?,?,?)",
            (edge_id, run_id, container_id, from_id, to_id, edge["edge_type"], canonical({"physical_occurrence": True, "source_edge": edge})),
        )
    return {
        "side": side,
        "parser_build_id": parser_id,
        "ingest_run_id": run_id,
        "proof_artifact_id": proof["proof_artifact_id"],
        "container_id": container_id,
        "passage_ids": occurrence_by_coordinate,
        "passage_count": len(occurrence_by_coordinate),
        "text_count": text_count,
        "edge_count": len(p.source["edges"]),
    }


def _write_comparison(conn: sqlite3.Connection, p: Preflight, sides: Mapping[str, Mapping[str, Any]]) -> None:
    manifest = p.manifest
    compare_id = str(manifest["compare_run"]["compare_run_id"])
    matcher = manifest["matcher"]
    conn.execute(
        "INSERT INTO matcher_build(id,matcher_kind,algorithm_version,config_checksum,metadata_json,created_at) VALUES(?,?,?,?,?,?)",
        (
            matcher["matcher_build_id"], matcher["matcher_kind"], matcher["algorithm_version"], matcher["config_checksum"],
            canonical({"family": FAMILY, "contract_version": CONTRACT_VERSION, "manifest_id": manifest["manifest_id"], "hypothesis_only": True}), OBSERVED_AT,
        ),
    )
    conn.execute(
        "INSERT INTO compare_run(id,matcher_build_id,family,status,started_at,completed_at,metadata_json) VALUES(?,?,?,?,?,?,?)",
        (
            compare_id, matcher["matcher_build_id"], FAMILY, "staged", OBSERVED_AT, None,
            canonical({"family_contract_version": CONTRACT_VERSION, "scope_key": SCOPE_KEY, "manifest_id": manifest["manifest_id"], "source_snapshot_id": SNAPSHOT_ID, "matcher_method": MATCHER_METHOD, "evidence_only": True, "corpus_complete": False, "logical_identity_created": False}),
        ),
    )
    for side in SIDES:
        conn.execute("INSERT INTO comparison_side(compare_run_id,side,ingest_run_id) VALUES(?,?,?)", (compare_id, side, sides[side]["ingest_run_id"]))
    source_output = manifest["outputs"]
    for side in SIDES:
        proof = proof_entry(manifest, side)
        proof_artifact = proof_artifact_entry(manifest, side)
        scope_json = canonical({
            "family": FAMILY, "family_contract_version": CONTRACT_VERSION, "scope_key": SCOPE_KEY,
            "side": side, "source_snapshot_id": SNAPSHOT_ID,
            "selected_file_seals_sha256": proof["selected_file_seals_sha256"],
            "raw_output": source_output["raw"], "text_output": source_output["text"], "graph_output": source_output["graph"],
        })
        metadata_json = canonical({
            "proof_artifact_id": proof["proof_artifact_id"], "proof_artifact_checksum": proof["proof_artifact_checksum"],
            "selected_file_seals": proof_artifact["selected_file_seals"], "source_output": source_output,
            "controlled_replay": True, "provenance_side": side,
        })
        conn.execute(
            "INSERT INTO comparison_scope_proof(compare_run_id,side,ingest_run_id,family,scope_key,scope_json,inventory_manifest_id,inventory_manifest_checksum,coverage_proof_id,coverage_proof_checksum,proof_algorithm_version,inventory_complete,expected_count,observed_count,parser_status,extraction_status,coverage_status,proof_status,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                compare_id, side, sides[side]["ingest_run_id"], FAMILY, SCOPE_KEY, scope_json,
                manifest["source_scope"]["manifest_id"], manifest["source_scope"]["sha256"], proof["proof_artifact_id"], proof["proof_artifact_checksum"],
                PROOF_ALGORITHM, 1, 14, 14, "passed", "complete", "complete", "verified", metadata_json,
            ),
        )
    conn.execute("UPDATE compare_run SET status='running' WHERE id=?", (compare_id,))
    for record in p.source["records"]:
        coordinate = (NAMESPACE, TALK_ID, str(record["node_id"]), str(record["source_pointer"]))
        tuple_json = {"namespace": coordinate[0], "talk_id": coordinate[1], "node_id": coordinate[2], "source_pointer": coordinate[3]}
        tuple_key = canonical(tuple_json)
        match_set_id = occurrence_id("talk-match-set", compare_id, tuple_key)
        base_id, base_container = sides["baseline"]["passage_ids"][coordinate]
        target_id, target_container = sides["target"]["passage_ids"][coordinate]
        provenance = canonical({"compare_run_id": compare_id, "source_snapshot_id": SNAPSHOT_ID, "scope_key": SCOPE_KEY, "coordinate_match": True, "logical_identity_created": False})
        conn.execute(
            "INSERT INTO comparison_match_set(id,compare_run_id,resolution_status,method,algorithm_version,justification,provenance) VALUES(?,?,?,?,?,?,?)",
            (match_set_id, compare_id, "resolved", MATCHER_METHOD, MATCHER_METHOD, "exact physical occurrence coordinate hypothesis with bilateral proof", provenance),
        )
        for side, occurrence, container in (("baseline", base_id, base_container), ("target", target_id, target_container)):
            conn.execute(
                "INSERT INTO comparison_match_anchor(match_set_id,compare_run_id,side,ingest_run_id,passage_occurrence_id,evidence_container_id) VALUES(?,?,?,?,?,?)",
                (match_set_id, compare_id, side, sides[side]["ingest_run_id"], occurrence, container),
            )
        candidate_id = occurrence_id("talk-match-candidate", compare_id, match_set_id, tuple_key)
        conn.execute(
            "INSERT INTO comparison_match_candidate(id,match_set_id,compare_run_id,candidate_kind,baseline_passage_occurrence_id,baseline_ingest_run_id,baseline_evidence_container_id,target_passage_occurrence_id,target_ingest_run_id,target_evidence_container_id,rank,confidence,features_json,justification,provenance) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                candidate_id, match_set_id, compare_id, "pair", base_id, sides["baseline"]["ingest_run_id"], base_container,
                target_id, sides["target"]["ingest_run_id"], target_container, 1, 1.0,
                canonical({"method": MATCHER_METHOD, "candidate_tuple": tuple_json, "hypothesis_only": True}),
                "unique candidate within the exact compare run and coordinate scope", provenance,
            ),
        )
        decision_id = occurrence_id("talk-match-decision", compare_id, match_set_id, candidate_id)
        conn.execute(
            "INSERT INTO comparison_match_decision(id,match_set_id,compare_run_id,candidate_id,decision,justification,provenance,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (decision_id, match_set_id, compare_id, candidate_id, "accepted", "accepted only after two verified same-scope proofs and one pair candidate", provenance, OBSERVED_AT),
        )
        observation_id = occurrence_id("talk-change-observation", compare_id, match_set_id)
        conn.execute(
            "INSERT INTO comparison_change_observation(id,compare_run_id,match_set_id,presence_outcome,observation_status,absence_assertion_id,absence_missing_side,evidence_json,provenance) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                observation_id, compare_id, match_set_id, "present_both", "observed", None, None,
                canonical({"raw_output_tuple_equal": True, "text_output_tuple_equal": True, "graph_output_tuple_equal": True, "source_snapshot_equal": True, "parser_build_provenance_distinct": True, "ingest_run_provenance_distinct": True, "match_hypothesis_only": True}), provenance,
            ),
        )
        for label in ("parser_delta", "provenance_only"):
            conn.execute(
                "INSERT INTO comparison_change_label(observation_id,compare_run_id,match_set_id,label,provenance) VALUES(?,?,?,?,?)",
                (observation_id, compare_id, match_set_id, label, canonical({"label_is_observation": True, "method": MATCHER_METHOD, "provenance": "controlled_replay"})),
            )
    conn.execute("UPDATE compare_run SET status='complete', completed_at=? WHERE id=?", (OBSERVED_AT, compare_id))


def _build_database(stage: Path, p: Preflight) -> dict[str, Any]:
    with Database(stage) as database:
        database.initialize_through(7)
        database.conn.execute("UPDATE schema_migration SET applied_at=?", (OBSERVED_AT,))
        database.conn.commit()
        conn = database.conn
        conn.execute("BEGIN IMMEDIATE")
        talk_record_id, file_ids, record_ids = _source_ids(conn, p)
        sides = {
            side: _write_side(conn, p, side, talk_record_id, file_ids, record_ids)
            for side in SIDES
        }
        _write_comparison(conn, p, sides)
        conn.commit()
    # The release verifier opens only immutable/query-only, so verify the
    # staged result before any final path is made visible.
    from .talk_comparison_phase31c3b_maintenance import verify_open_connection

    with sqlite3.connect(stage.as_uri() + "?mode=ro&immutable=1", uri=True) as readonly:
        readonly.row_factory = sqlite3.Row
        readonly.execute("PRAGMA query_only=ON")
        verify_open_connection(readonly, p)
    return _counts(stage, p)


def _counts(path: Path, p: Preflight) -> dict[str, Any]:
    from .talk_comparison_phase31c3b_maintenance import _counts as verified_counts

    with sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
        return verified_counts(conn)


def _report(path: Path, p: Preflight, target: Path) -> dict[str, Any]:
    fingerprint = _fingerprint(path)
    counts = _counts(path, p)
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
        "counts": counts,
        "expected_result": p.manifest["expected_result"],
        "output_equivalence": p.manifest["output_equivalence"],
        "database_sha256": fingerprint["sha256"],
        "database_size_bytes": fingerprint["size_bytes"],
        "database_path": str(target.resolve()),
        "database_artifact_tracked": False,
        "mcp_change": False,
        "logical_identity_created": False,
        "exact_replay": "fresh target required; rerun to a new target must be byte-exact; existing target is fail-closed",
    }


def _private_database(parent: Path, prefix: str) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=".sqlite", dir=parent)
    os.close(descriptor)
    path = Path(name)
    os.chmod(path, 0o600)
    return path


def _private_json(parent: Path, prefix: str, value: Mapping[str, Any]) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=".json", dir=parent)
    path = Path(name)
    os.chmod(path, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
    return path


def _cleanup(path: Path | None) -> None:
    if path is None:
        return
    for item in (path, Path(f"{path}-journal"), Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            item.unlink()
        except FileNotFoundError:
            pass


def _pair_lock_path(database: Path, report: Path) -> Path:
    if database.parent != report.parent:
        _fail("C3B database and report must share one output directory")
    return database.parent / ".phase31c3b-output.lock"


def _promote_absent_pair(database: Path, report: Path, database_stage: Path, report_stage: Path) -> None:
    lock_path = _pair_lock_path(database, report)
    if lock_path in (database, report):
        _fail("C3B output pair conflicts with its stable lock path")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    locked = False
    published_db = False
    published_report = False
    db_identity: tuple[int, int] | None = None
    report_identity: tuple[int, int] | None = None
    try:
        lock_exclusive(descriptor)
        locked = True
        lock_info = os.fstat(descriptor)
        lock_path_info = os.lstat(lock_path)
        if stat.S_ISLNK(lock_path_info.st_mode) or not stat.S_ISREG(lock_path_info.st_mode) or stat.S_IMODE(lock_info.st_mode) != 0o600 or (lock_info.st_dev, lock_info.st_ino) != (lock_path_info.st_dev, lock_path_info.st_ino):
            _fail("C3B stable lock identity/mode mismatch")
        if _PROMOTION_TEST_HOOK is not None:
            _PROMOTION_TEST_HOOK(database, database_stage)
        if os.path.lexists(database) or os.path.lexists(report):
            _fail("C3B final target appeared before promotion")
        assert_sidecars_absent(database, "under C3B stable lock")
        assert_sidecars_absent(report, "under C3B stable lock")
        assert_sidecars_absent(database_stage, "under C3B stable lock")
        assert_sidecars_absent(report_stage, "under C3B stable lock")
        for stage in (database_stage, report_stage):
            info = os.lstat(stage)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                _fail("C3B staging output is not a private 0600 regular file")
            stage_descriptor = os.open(stage, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fsync(stage_descriptor)
            finally:
                os.close(stage_descriptor)
        db_info, report_info = os.lstat(database_stage), os.lstat(report_stage)
        db_identity = (db_info.st_dev, db_info.st_ino)
        report_identity = (report_info.st_dev, report_info.st_ino)
        os.replace(database_stage, database)
        published_db = True
        sync_directory(database.parent)
        if _BEFORE_REPORT_REPLACE_TEST_HOOK is not None:
            _BEFORE_REPORT_REPLACE_TEST_HOOK(database, report)
        os.replace(report_stage, report)
        published_report = True
        sync_directory(report.parent)
        if _POST_REPLACE_TEST_HOOK is not None:
            _POST_REPLACE_TEST_HOOK(database, lock_path)
    except BaseException:
        if published_report and report_identity is not None:
            try:
                current = os.lstat(report)
                if not stat.S_ISLNK(current.st_mode) and (current.st_dev, current.st_ino) == report_identity:
                    report.unlink()
                    sync_directory(report.parent)
            except FileNotFoundError:
                pass
        if published_db and db_identity is not None:
            try:
                current = os.lstat(database)
                if not stat.S_ISLNK(current.st_mode) and (current.st_dev, current.st_ino) == db_identity:
                    database.unlink()
                    sync_directory(database.parent)
            except FileNotFoundError:
                pass
        raise
    finally:
        if locked:
            unlock(descriptor)
        os.close(descriptor)


def build(
    database_path: str | Path,
    report_path: str | Path,
    source_root: str | Path,
    repo_root: str | Path,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build a fresh standalone C3B pilot and publish it atomically.

    The target and report must be absent.  A second fresh target is the
    supported replay operation; an existing target is never overwritten.
    """
    paths = assert_path_matrix(
        {
            "database": database_path,
            "report": report_path,
            "source_root": source_root,
            "repo_root": repo_root,
            "manifest": manifest_path or Path(repo_root) / MANIFEST_PATH,
        },
        protected_database_names=("database",),
        input_files=("manifest",), input_directories=("source_root", "repo_root"),
        absent_outputs=("database", "report"),
    )
    database = paths["database"]
    report = paths["report"]
    _pair_lock_path(database, report)
    p = preflight(paths["source_root"], paths["repo_root"], paths["manifest"])
    database_stage = _private_database(database.parent, f".{database.name}.phase31c3b-")
    report_stage: Path | None = None
    try:
        _build_database(database_stage, p)
        result = _report(database_stage, p, database)
        report_stage = _private_json(report.parent, f".{report.name}.phase31c3b-", result)
        _promote_absent_pair(database, report, database_stage, report_stage)
        report_stage = None
        assert_sidecars_absent(database, "after C3B build promotion")
        return result
    except TalkComparisonPhase31C3BError:
        raise
    except Exception as exc:
        _fail(f"C3B atomic build failed: {exc}")
    finally:
        _cleanup(database_stage)
        _cleanup(report_stage)


__all__ = [
    "Preflight", "TalkComparisonPhase31C3BBuilderError", "build", "preflight",
]
