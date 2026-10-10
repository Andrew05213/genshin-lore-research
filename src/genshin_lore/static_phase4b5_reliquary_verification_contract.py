from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .static_phase4b5_reliquary_manifest import StaticPhase4B5Manifest
from .source_record_contract import SourceRecordContract
from .character_phase4c2_schema import (
    CharacterPhase4C2SchemaError,
    verify_character_phase4c2_schema_signature,
)
from .util import canonical_json, digest_id, normalize_text, sha256_bytes, sha256_text


RUN_FAMILY = "static_phase4b5_reliquary_corpus"
REQUIRED_TRIGGERS = {
    "trg_source_snapshot_identity_immutable",
    "trg_source_file_identity_immutable",
    "trg_source_record_identity_immutable",
    "trg_static_evidence_container_immutable",
    "trg_static_evidence_container_delete_immutable",
    "trg_static_segment_occurrence_immutable",
    "trg_static_segment_occurrence_delete_immutable",
    "trg_static_relationship_occurrence_immutable",
    "trg_static_relationship_occurrence_delete_immutable",
    "trg_static_text_resolution_occurrence_immutable",
    "trg_static_text_resolution_occurrence_delete_immutable",
    "trg_static_text_candidate_occurrence_immutable",
    "trg_static_text_candidate_occurrence_delete_immutable",
}


class Phase4B5ContractError(RuntimeError):
    pass


def _fail(message: str) -> None:
    raise Phase4B5ContractError(message)


def _json(raw: str | None, label: str) -> Any:
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        _fail(f"{label} is not JSON: {exc}")
    if raw != canonical_json(value):
        _fail(f"{label} is not canonical JSON")
    return value


def _exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        _fail(f"{label} keys mismatch")
    return value


def _set_digest(items: list[dict[str, Any]]) -> str:
    ordered = sorted(canonical_json(item) for item in items)
    return sha256_text(canonical_json(ordered))


def expected_phase4b5_reliquary_identity(manifest: StaticPhase4B5Manifest) -> dict[str, Any]:
    source = manifest.source
    snapshot_id = digest_id(
        "snap",
        canonical_json(
            {
                "commit": source["commit"],
                "tree": source["tree"],
                "build": source["build"],
                "release": source["release_label"],
            }
        ),
    )
    parser_build_id = digest_id(
        "pb-static-phase4b5",
        canonical_json(
            {
                "versions": manifest.versions,
                "manifest_id": manifest.manifest_id,
            }
        ),
    )
    config_checksum = sha256_text(
        canonical_json(
            {
                "manifest_id": manifest.manifest_id,
                "manifest_raw_sha256": manifest.raw_sha256,
                "occurrence_set_digests": manifest.expected["occurrence_set_digests"],
            }
        )
    )
    ingest_run_id = digest_id(
        "ir-static-phase4b5",
        canonical_json(
            {
                "snapshot_id": snapshot_id,
                "parser_build_id": parser_build_id,
                "config_checksum": config_checksum,
            }
        ),
    )
    scope = {
        "family": RUN_FAMILY,
        "manifest_id": manifest.manifest_id,
        "selected_paths": [item.relative_path for item in manifest.files],
    }
    run_config = {
        "manifest_raw_sha256": manifest.raw_sha256,
        "versions": manifest.versions,
        "full_static_scan": False,
        "logical_identity_created": False,
        "cross_language_alignment_created": False,
        "textmap_evidence_container_created": False,
    }
    return {
        "snapshot_id": snapshot_id,
        "config_checksum": config_checksum,
        "parser_build_id": parser_build_id,
        "ingest_run_id": ingest_run_id,
        "scope_json": canonical_json(scope),
        "config_json": canonical_json(run_config),
    }


def verify_phase4b5_reliquary_source_contract(
    source_root: str | Path,
    manifest: StaticPhase4B5Manifest,
) -> dict[str, Any]:
    root = Path(source_root).resolve()
    if not root.is_dir():
        _fail(f"source root is not a directory: {root}")

    def git(*args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(root), *args],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            _fail(f"source checkout verification failed: {exc}")
        return result.stdout.strip()

    commit = git("rev-parse", "HEAD")
    tree = git("rev-parse", "HEAD^{tree}")
    dirty = git("status", "--porcelain=v1", "--untracked-files=all")
    if commit != manifest.source["commit"]:
        _fail(f"source checkout commit mismatch: {commit}")
    if tree != manifest.source["tree"]:
        _fail(f"source checkout tree mismatch: {tree}")
    if dirty:
        _fail(f"source checkout is dirty: {dirty.splitlines()[0]}")
    file_contracts: list[dict[str, Any]] = []
    for spec in manifest.files:
        path = root.joinpath(*spec.relative_path.split("/"))
        try:
            raw = path.read_bytes()
        except OSError as exc:
            _fail(f"cannot read sealed source {spec.relative_path}: {exc}")
        actual = {
            "relative_path": spec.relative_path,
            "format": spec.format,
            "size_bytes": len(raw),
            "sha256": sha256_bytes(raw),
        }
        expected = {
            "relative_path": spec.relative_path,
            "format": spec.format,
            "size_bytes": spec.size_bytes,
            "sha256": spec.sha256,
        }
        if actual != expected:
            _fail(f"sealed source file mismatch: {spec.relative_path}")
        file_contracts.append(actual)
    digest = _set_digest(file_contracts)
    if digest != manifest.expected["occurrence_set_digests"]["files"]:
        _fail("sealed source file occurrence digest mismatch")
    return {
        "root": str(root), "commit": commit, "tree": tree, "clean": True,
        "selected_files": len(file_contracts), "files_digest": digest,
    }


def _verify_schema(conn: sqlite3.Connection) -> int:
    versions = [int(row[0]) for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")]
    if versions not in (list(range(1, 10)), list(range(1, 11))):
        _fail(f"schema migration set is not exactly known 1..9 or 1..10: {versions}")
    if versions[-1] == 10:
        try:
            verify_character_phase4c2_schema_signature(conn)
        except CharacterPhase4C2SchemaError as exc:
            _fail(str(exc))
    triggers = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
    }
    missing = REQUIRED_TRIGGERS - triggers
    if missing:
        _fail(f"required immutable triggers are missing: {sorted(missing)}")
    return versions[-1]


def _verify_binding(
    conn: sqlite3.Connection,
    manifest: StaticPhase4B5Manifest,
    identity: dict[str, Any],
) -> None:
    source = manifest.source
    row = conn.execute(
        "SELECT id,kind,url,license_note FROM source_catalog WHERE id=?",
        (source["source_id"],),
    ).fetchone()
    if row is None or tuple(row) != (source["source_id"], "primary", source["upstream"], None):
        _fail("source_catalog binding mismatch")
    row = conn.execute(
        'SELECT source_id,release_label,build,"commit",tree,observed_at,status '
        "FROM source_snapshot WHERE id=?",
        (identity["snapshot_id"],),
    ).fetchone()
    expected_snapshot = (
        source["source_id"], source["release_label"], source["build"], source["commit"],
        source["tree"], source["observed_at"], "active",
    )
    if row is None or tuple(row) != expected_snapshot:
        _fail("source_snapshot binding mismatch")
    row = conn.execute(
        "SELECT parser_version,mapping_version,fingerprint,config_checksum,created_at "
        "FROM parser_build WHERE id=?",
        (identity["parser_build_id"],),
    ).fetchone()
    expected_parser = (
        manifest.versions["parser"], manifest.versions["projection"],
        "static-phase4b5-sealed-reliquary-corpus", identity["config_checksum"], source["observed_at"],
    )
    if row is None or tuple(row) != expected_parser:
        _fail("parser_build binding mismatch")
    row = conn.execute(
        "SELECT snapshot_id,parser_build_id,scope_json,config_json,status,created_at "
        "FROM ingest_run WHERE id=?",
        (identity["ingest_run_id"],),
    ).fetchone()
    expected_run = (
        identity["snapshot_id"], identity["parser_build_id"], identity["scope_json"],
        identity["config_json"], "complete", source["observed_at"],
    )
    if row is None or tuple(row) != expected_run:
        _fail("ingest_run binding mismatch")
    phase_runs = [
        str(item[0])
        for item in conn.execute(
            "SELECT id FROM ingest_run WHERE json_extract(scope_json,'$.family')=? ORDER BY id",
            (RUN_FAMILY,),
        )
    ]
    if phase_runs != [identity["ingest_run_id"]]:
        _fail(f"unexpected Phase 4B5 ingest run set: {phase_runs}")


def _verify_files(
    conn: sqlite3.Connection,
    manifest: StaticPhase4B5Manifest,
    identity: dict[str, Any],
    source_root: str | Path,
) -> dict[str, sqlite3.Row]:
    root = Path(source_root).resolve()
    result: dict[str, sqlite3.Row] = {}
    for spec in manifest.files:
        file_id = digest_id("sf", identity["snapshot_id"], spec.relative_path)
        row = conn.execute("SELECT * FROM source_file WHERE id=?", (file_id,)).fetchone()
        if row is None:
            _fail(f"source_file is missing: {spec.relative_path}")
        actual_root = os.path.normcase(str(Path(str(row["root_path"])).resolve(strict=False)))
        expected_root = os.path.normcase(str(root))
        expected = (
            identity["snapshot_id"], spec.relative_path, None, spec.sha256,
            spec.size_bytes, spec.format, expected_root,
        )
        actual = (
            row["snapshot_id"], row["relative_path"], row["upstream_blob_oid"], row["sha256"],
            row["size_bytes"], row["format"], actual_root,
        )
        if actual != expected:
            _fail(f"source_file attribute mismatch: {spec.relative_path}")
        result[spec.relative_path] = row
    return result


def _container_rows(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT c.*,sf.relative_path,sr.pointer,sr.record_kind,sr.native_id AS record_native_id,
                  sr.raw_sha256,sr.raw_json
             FROM static_evidence_container c
             JOIN source_file sf ON sf.id=c.source_file_id
             JOIN source_record sr ON sr.id=c.source_record_id
            WHERE c.ingest_run_id=? ORDER BY c.id""",
        (run_id,),
    ).fetchall()


def _contract_from_containers(
    rows: list[sqlite3.Row],
    manifest: StaticPhase4B5Manifest,
    identity: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, sqlite3.Row], dict[str, str], dict[str, str]]:
    contracts: list[dict[str, Any]] = []
    by_id: dict[str, sqlite3.Row] = {}
    family_by_id: dict[str, str] = {}
    classification_by_id: dict[str, str] = {}
    for row in rows:
        metadata = _exact_object(
            _json(row["metadata_json"], f"container {row['id']} metadata"),
            {"family", "classification", "cross_language_alignment", "manifest_id", "manifest_raw_sha256", "raw_evidence_identity"}
            if row["container_kind"] == "readable_file"
            else {"family", "manifest_id", "manifest_raw_sha256", "raw_evidence_identity"},
            f"container {row['id']} metadata",
        )
        family = metadata["family"]
        if family != "reliquaries":
            _fail(f"container {row['id']} family mismatch: {family}")
        by_id[row["id"]] = row
        family_by_id[row["id"]] = family
        if row["container_kind"] == "readable_file":
            classification_by_id[row["id"]] = metadata["classification"]
        contracts.append(
            {
                "family": family,
                "source_path": row["relative_path"],
                "record_pointer": row["pointer"],
                "source_family": row["source_family"],
                "locale": row["locale"],
                "native_id": row["record_native_id"],
            }
        )
    return contracts, by_id, family_by_id, classification_by_id


def _verify_segments(
    conn: sqlite3.Connection,
    run_id: str,
    family_by_id: dict[str, str],
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT s.*,sf.relative_path,sr.pointer AS record_pointer
             FROM static_segment_occurrence s
             JOIN static_evidence_container c ON c.id=s.evidence_container_id
             JOIN source_file sf ON sf.id=c.source_file_id
             JOIN source_record sr ON sr.id=s.source_record_id
            WHERE s.ingest_run_id=? ORDER BY s.id""",
        (run_id,),
    ).fetchall()
    contracts: list[dict[str, Any]] = []
    for row in rows:
        family = family_by_id[row["evidence_container_id"]]
        contracts.append(
            {
                "family": family,
                "source_path": row["relative_path"],
                "locale": row["locale"],
                "file_local_ordinal": row["file_local_ordinal"],
                "byte_start": row["byte_start"],
                "byte_end": row["byte_end"],
                "character_start": row["character_start"],
                "character_end": row["character_end"],
                "raw_text": row["raw_text"],
                "normalized_text": row["normalized_text"],
                "raw_checksum": row["raw_checksum"],
                "normalized_checksum": row["normalized_checksum"],
            }
        )
    return contracts


def _verify_relationships(
    conn: sqlite3.Connection,
    run_id: str,
    by_container_id: dict[str, sqlite3.Row],
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT r.*,sf.relative_path AS source_relative_path,sr.pointer AS source_record_pointer
             FROM static_relationship_occurrence r
             JOIN static_evidence_container c ON c.id=r.source_container_id
             JOIN source_file sf ON sf.id=c.source_file_id
             JOIN source_record sr ON sr.id=r.source_record_id
            WHERE r.ingest_run_id=? ORDER BY r.id""",
        (run_id,),
    ).fetchall()
    contracts: list[dict[str, Any]] = []
    for row in rows:
        target_record = None
        if row["target_evidence_container_id"] is not None:
            target_container = by_container_id.get(row["target_evidence_container_id"])
            if target_container is None:
                _fail(f"relationship {row['id']} points outside the run")
            if target_container["container_kind"] == "readable_file":
                target_record = {
                    "family": "Readable",
                    "relative_path": target_container["relative_path"],
                    "pointer": target_container["pointer"],
                }
            else:
                target_record = {
                    "table": target_container["source_family"],
                    "pointer": target_container["pointer"],
                }
        metadata = _json(row["metadata_json"], f"relationship {row['id']} metadata")
        cand_key = _json(row["target_candidate_key_json"], f"relationship {row['id']} key")
        ambiguity = _json(row["ambiguity_json"], f"relationship {row['id']} ambiguity")
        contracts.append(
            {
                "family": metadata["family"],
                "source_table": row["source_family"],
                "source_record_pointer": row["source_record_pointer"],
                "source_pointer": row["source_pointer"],
                "relation_kind": row["relation_kind"],
                "field_role": row["field_role"],
                "raw_value": metadata["raw_value"],
                "target_family": row["target_family"],
                "target_candidate_key": cand_key,
                "status": row["candidate_resolution_status"],
                "target_record": target_record,
                "ambiguity": ambiguity or {},
            }
        )
    return contracts


def _verify_text_resolutions(
    conn: sqlite3.Connection,
    run_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT tr.*,sf.relative_path AS source_relative_path,sr.pointer AS source_record_pointer
             FROM static_text_resolution_occurrence tr
             JOIN static_evidence_container c ON c.id=tr.source_container_id
             JOIN source_file sf ON sf.id=c.source_file_id
             JOIN source_record sr ON sr.id=tr.source_record_id
            WHERE tr.ingest_run_id=? ORDER BY tr.id""",
        (run_id,),
    ).fetchall()
    contracts: list[dict[str, Any]] = []
    for row in rows:
        metadata = _json(row["metadata_json"], f"resolution {row['id']} metadata")
        contracts.append(
            {
                "family": metadata["family"],
                "source_table": row["source_relative_path"].split("/")[-1].replace(".json", ""),
                "source_record_pointer": row["source_record_pointer"],
                "source_pointer": row["source_pointer"],
                "field": row["source_pointer"].split("/")[-1],
                "textmap_hash": row["textmap_hash"],
                "locale": row["locale"],
                "status": row["resolution_status"],
                "raw_text": row["raw_text"],
                "normalized_text": row["normalized_text"],
                "raw_checksum": row["raw_checksum"],
                "normalized_checksum": row["normalized_checksum"],
            }
        )
    return contracts


def _verify_text_candidates(
    conn: sqlite3.Connection,
    run_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT tc.*,
                  sf.relative_path AS source_relative_path,
                  owner_sf.relative_path AS owner_relative_path,
                  owner_sr.pointer AS owner_record_pointer,
                  tr.source_pointer AS owner_source_pointer,
                  tr.textmap_hash AS tr_textmap_hash
             FROM static_text_candidate_occurrence tc
             JOIN source_file sf ON sf.id=tc.source_file_id
             JOIN static_text_resolution_occurrence tr ON tr.id=tc.resolution_occurrence_id
             JOIN static_evidence_container owner_c ON owner_c.id=tr.source_container_id
             JOIN source_file owner_sf ON owner_sf.id=owner_c.source_file_id
             JOIN source_record owner_sr ON owner_sr.id=owner_c.source_record_id
            WHERE tc.ingest_run_id=? ORDER BY tc.id""",
        (run_id,),
    ).fetchall()
    contracts: list[dict[str, Any]] = []
    for row in rows:
        metadata = _json(row["metadata_json"], f"candidate {row['id']} metadata")
        contracts.append(
            {
                "family": metadata["family"],
                "source_table": row["owner_relative_path"].split("/")[-1].replace(".json", ""),
                "source_record_pointer": row["owner_record_pointer"],
                "source_pointer": row["owner_source_pointer"],
                "field": row["owner_source_pointer"].split("/")[-1],
                "textmap_hash": row["tr_textmap_hash"],
                "locale": row["locale"],
                "status": row["resolution_status"],
                "shard_kind": row["shard_kind"],
                "source_path": row["source_relative_path"],
                "source_pointer": row["source_pointer"],
                "raw_text": row["raw_text"],
                "normalized_text": row["normalized_text"],
                "raw_checksum": row["raw_checksum"],
                "normalized_checksum": row["normalized_checksum"],
            }
        )
    return contracts


def _verify_source_records(
    conn: sqlite3.Connection,
    run_id: str,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, SourceRecordContract],
]:
    rows = conn.execute(
        """SELECT DISTINCT sr.id,sr.source_file_id,sf.relative_path,sr.pointer,sr.record_kind,sr.native_id,
                  sr.raw_sha256,sr.raw_json
             FROM source_record sr
             JOIN source_file sf ON sf.id=sr.source_file_id
            WHERE sr.id IN (
                SELECT source_record_id FROM static_evidence_container WHERE ingest_run_id=?
                UNION
                SELECT source_record_id FROM static_text_candidate_occurrence WHERE ingest_run_id=?
            )
            ORDER BY sf.relative_path, sr.pointer""",
        (run_id, run_id),
    ).fetchall()
    record_contracts: list[dict[str, Any]] = []
    asset_contracts: list[dict[str, Any]] = []
    validated_records: dict[str, SourceRecordContract] = {}
    for row in rows:
        expected_id = digest_id("sr", row["source_file_id"], row["pointer"])
        if row["id"] != expected_id:
            _fail(f"source_record occurrence identity mismatch: {row['id']}")
        raw_json = row["raw_json"]
        if row["record_kind"] == "readable_file":
            raw_text = json.loads(raw_json)["text"]
            db_raw_json = canonical_json(raw_text)
        else:
            db_raw_json = raw_json
        record_contracts.append(
            {
                "source_path": row["relative_path"],
                "pointer": row["pointer"],
                "record_kind": "static_" + row["record_kind"] if not row["record_kind"].startswith("static_") else row["record_kind"],
                "native_id": row["native_id"],
                "raw_sha256": row["raw_sha256"],
                "raw_json": db_raw_json,
            }
        )
        validated_records[str(row["id"])] = SourceRecordContract.from_row(row)
        if "TextMap" in row["relative_path"]:
            asset_contracts.append(
                {
                    "source_path": row["relative_path"],
                    "source_pointer": row["pointer"],
                    "locale": "CHS" if "CHS" in row["relative_path"] else "EN" if "EN" in row["relative_path"] else "JP",
                    "shard_kind": "medium" if "Medium" in row["relative_path"] else "main",
                    "textmap_hash": row["native_id"],
                    "raw_checksum": row["raw_sha256"],
                }
            )
    return record_contracts, asset_contracts, validated_records


def verify_phase4b5_reliquary_database_contract(
    conn: sqlite3.Connection,
    manifest: StaticPhase4B5Manifest,
    source_root: str | Path,
    *,
    validated_source_records: dict[str, SourceRecordContract] | None = None,
) -> dict[str, Any]:
    schema_version = _verify_schema(conn)
    identity = expected_phase4b5_reliquary_identity(manifest)
    _verify_binding(conn, manifest, identity)
    _verify_files(conn, manifest, identity, source_root)

    run_id = identity["ingest_run_id"]
    containers, by_id, family_by_id, classification_by_id = _contract_from_containers(
        _container_rows(conn, run_id), manifest, identity,
    )
    segments = _verify_segments(conn, run_id, family_by_id)
    relationships = _verify_relationships(conn, run_id, by_id)
    resolutions = _verify_text_resolutions(conn, run_id)
    candidates = _verify_text_candidates(conn, run_id)
    source_records, text_assets, record_rows = _verify_source_records(conn, run_id)

    digests = {
        "files": manifest.expected["occurrence_set_digests"]["files"],
        "source_records": _set_digest(source_records),
        "containers": _set_digest(containers),
        "segments": _set_digest(segments),
        "relationships": _set_digest(relationships),
        "text_resolutions": _set_digest(resolutions),
        "text_candidates": _set_digest(candidates),
        "text_assets": _set_digest(text_assets),
    }

    if digests != manifest.expected["occurrence_set_digests"]:
        differing = [k for k in digests if digests[k] != manifest.expected["occurrence_set_digests"][k]]
        _fail(f"occurrence set digest mismatch in database: {differing}")

    counts = {
        "files": len(manifest.files),
        "source_records": len(source_records),
        "containers": len(containers),
        "segments": len(segments),
        "relationships": len(relationships),
        "text_resolutions": len(resolutions),
        "text_candidates": len(candidates),
    }

    rel_counts = Counter(r["status"] for r in relationships)
    res_counts = Counter(r["status"] for r in resolutions)
    status_counts = {
        "relationships": {
            s: rel_counts[s]
            for s in ("resolved", "ambiguous", "unresolved", "dangling")
        },
        "text_resolutions": {
            s: res_counts[s]
            for s in ("unique_main", "unique_medium", "both_equal", "both_conflict", "missing")
        },
    }

    family_counts = {
        "reliquaries": {
            "containers": len(containers),
            "segments": len(segments),
            "relationships": len(relationships),
            "text_resolutions": len(resolutions),
            "text_candidates": len(candidates),
        }
    }

    family_selected_paths = {
        "reliquaries": sorted({spec.relative_path for spec in manifest.files})
    }

    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        _fail(f"foreign key violations found: {fk_errors}")
    if validated_source_records is not None:
        for record_id, contract in record_rows.items():
            prior = validated_source_records.setdefault(record_id, contract)
            if prior != contract:
                _fail(f"source_record contract conflict: {record_id}")

    return {
        "protocol": "wave1-stage-final-state-contract-v1",
        "contract_id": (
            "wave1-stage-final-state-v1:phase4b5:" + manifest.manifest_id
        ),
        "status": "pass",
        "run_id": identity["ingest_run_id"],
        "parser_build_id": identity["parser_build_id"],
        "manifest_sha256": manifest.raw_sha256,
        "exact_counts": counts,
        "required_checks": [
            "schema", "source_binding", "run_binding", "exact_counts",
            "full_rows", "occurrence_digests", "logical_boundary",
            "foreign_keys",
        ],
        "schema_version": schema_version,
        "identity": identity,
        "counts": counts,
        "family_counts": family_counts,
        "family_selected_paths": family_selected_paths,
        "status_counts": status_counts,
        "locale_breakdown": manifest.expected["locale_breakdown"],
        "occurrence_set_digests": digests,
        "foreign_key_check": [],
    }
