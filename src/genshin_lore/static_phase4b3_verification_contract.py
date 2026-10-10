from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .static_phase4b3_manifest import StaticPhase4B3Manifest
from .source_record_contract import SourceRecordContract
from .character_phase4c2_schema import (
    CharacterPhase4C2SchemaError,
    verify_character_phase4c2_schema_signature,
)
from .util import canonical_json, digest_id, normalize_text, sha256_bytes, sha256_text


RUN_FAMILY = "static_phase4b3_book_weapon_corpus"
B1_STATIC_RECORD_COORDINATES = {
    ("ExcelBinOutput/DocumentExcelConfigData.json", "/6"),
    ("ExcelBinOutput/LocalizationExcelConfigData.json", "/8"),
    ("Readable/CHS/Book1.txt", "/text"),
    ("Readable/EN/Book1_EN.txt", "/text"),
    ("Readable/JP/Book1_JP.txt", "/text"),
}
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


class Phase4B3ContractError(RuntimeError):
    pass


def _fail(message: str) -> None:
    raise Phase4B3ContractError(message)


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


def expected_phase4b3_identity(manifest: StaticPhase4B3Manifest) -> dict[str, Any]:
    source = manifest.source
    snapshot_id = f"{source['source_id']}:{source['release_label']}:{source['commit'][:12]}"
    config = {
        "family": RUN_FAMILY,
        "manifest_raw_sha256": manifest.raw_sha256,
        "selected_paths": [item.relative_path for item in manifest.files],
        "snapshot": {"commit": source["commit"], "tree": source["tree"]},
        "versions": manifest.versions,
    }
    config_checksum = sha256_text(canonical_json(config))
    parser_build_id = digest_id(
        "pb-static-phase4b3", snapshot_id, manifest.raw_sha256,
        manifest.versions["parser"], manifest.versions["projection"],
        manifest.versions["segmentation"], config_checksum,
    )
    ingest_run_id = digest_id(
        "ir-static-phase4b3", snapshot_id, manifest.raw_sha256, parser_build_id, config_checksum,
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


def verify_phase4b3_source_contract(
    source_root: str | Path,
    manifest: StaticPhase4B3Manifest,
) -> dict[str, Any]:
    """Independently verify the pinned checkout and every explicitly sealed path."""
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
    manifest: StaticPhase4B3Manifest,
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
        "static-phase4b3-sealed-book-weapon-corpus", identity["config_checksum"], source["observed_at"],
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
        _fail(f"unexpected Phase 4B3 ingest run set: {phase_runs}")


def _verify_files(
    conn: sqlite3.Connection,
    manifest: StaticPhase4B3Manifest,
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
    manifest: StaticPhase4B3Manifest,
    identity: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, sqlite3.Row], dict[str, str], dict[str, str]]:
    contracts: list[dict[str, Any]] = []
    by_id: dict[str, sqlite3.Row] = {}
    family_by_id: dict[str, str] = {}
    classification_by_id: dict[str, str] = {}
    table_selected: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        metadata = _exact_object(
            _json(row["metadata_json"], f"container {row['id']} metadata"),
            {"family", "classification", "cross_language_alignment", "manifest_id", "manifest_raw_sha256", "raw_evidence_identity"},
            f"container {row['id']} metadata",
        )
        family = metadata["family"]
        if family not in {"books", "weapons"}:
            _fail(f"container family is invalid: {row['id']}")
        expected_alignment = "unresolved" if row["container_kind"] == "readable_file" else "not_applicable"
        if metadata != {
            "family": family,
            "classification": metadata["classification"],
            "cross_language_alignment": expected_alignment,
            "manifest_id": manifest.manifest_id,
            "manifest_raw_sha256": manifest.raw_sha256,
            "raw_evidence_identity": "snapshot_source_coordinate_ingest_run_occurrence",
        }:
            _fail(f"container metadata attribute mismatch: {row['id']}")
        expected_id = digest_id(
            "static-container", identity["snapshot_id"], manifest.raw_sha256,
            identity["ingest_run_id"], row["relative_path"], row["pointer"],
        )
        if row["id"] != expected_id or row["snapshot_id"] != identity["snapshot_id"]:
            _fail(f"container occurrence identity mismatch: {row['id']}")
        candidate_key = _json(row["candidate_key_json"], f"container {row['id']} candidate key")
        if row["source_family"] == "Readable":
            expected_key = {"locale": row["locale"], "relative_path": row["relative_path"]}
            expected_namespace = f"agd.static.Readable.{row['locale']}"
            expected_kind = "readable_file"
            native_id = Path(row["relative_path"]).stem
            if row["pointer"] != "/text" or row["container_kind"] != expected_kind:
                _fail(f"Readable container shape mismatch: {row['id']}")
        else:
            expected_key = {"table": row["source_family"], "native_id": candidate_key.get("native_id")}
            expected_namespace = f"agd.static.{row['source_family']}"
            expected_kind = "excel_row"
            native_id = str(candidate_key.get("native_id"))
            if candidate_key.get("table") != row["source_family"] or row["locale"] is not None:
                _fail(f"Excel container table/locale mismatch: {row['id']}")
        if candidate_key != expected_key or row["candidate_namespace"] != expected_namespace or row["container_kind"] != expected_kind:
            _fail(f"container candidate attributes mismatch: {row['id']}")
        contract = {
            "family": family, "source_path": row["relative_path"], "record_pointer": row["pointer"],
            "source_family": row["source_family"], "locale": row["locale"], "native_id": native_id,
        }
        contracts.append(contract)
        by_id[row["id"]] = row
        family_by_id[row["id"]] = family
        classification_by_id[row["id"]] = str(metadata["classification"])
        if row["source_family"] != "Readable":
            table_selected[row["source_family"]].append(
                {
                    "pointer": row["pointer"], "native_id": native_id,
                    "raw_sha256": row["raw_sha256"], "family": family,
                }
            )
    for table, expected in manifest.tables.items():
        actual = sorted(table_selected[table], key=lambda item: int(item["pointer"][1:]))
        if actual != expected["selected_rows"] or _set_digest(actual) != expected["selected_set_digest"]:
            _fail(f"selected aggregate row contract mismatch: {table}")
    return contracts, by_id, family_by_id, classification_by_id


def _contract_from_records(
    conn: sqlite3.Connection,
    container_rows: list[sqlite3.Row],
    run_id: str,
) -> tuple[list[dict[str, Any]], dict[str, sqlite3.Row]]:
    referenced: dict[str, tuple[sqlite3.Row, dict[str, Any]]] = {}
    for row in container_rows:
        coordinate = row["relative_path"], row["pointer"]
        contract_kind = "static_readable_file" if row["source_family"] == "Readable" else "static_excel_row"
        candidate_key = _json(row["candidate_key_json"], f"container {row['id']} key")
        contract_native = (
            Path(row["relative_path"]).stem
            if row["source_family"] == "Readable"
            else str(candidate_key["native_id"])
        )
        expected_db_kind = contract_kind if coordinate in B1_STATIC_RECORD_COORDINATES else {
            "static_readable_file": "readable_file", "static_excel_row": "excel_row"
        }[contract_kind]
        expected_db_native = (
            str(_json(row["raw_json"], f"record {row['source_record_id']} raw")["weaponId"])
            if row["relative_path"] == "ExcelBinOutput/WeaponCodexExcelConfigData.json"
            else contract_native
        )
        if row["record_kind"] != expected_db_kind or row["record_native_id"] != expected_db_native:
            _fail(f"source_record kind/native locator mismatch: {row['source_record_id']}")
        raw_json = row["raw_json"]
        if contract_kind == "static_readable_file":
            wrapped = _exact_object(_json(raw_json, f"record {row['source_record_id']} raw"), {"text"}, "Readable raw_json")
            raw_value = wrapped["text"]
            if not isinstance(raw_value, str):
                _fail("Readable raw_json text is not text")
            contract_raw_json = canonical_json(raw_value)
            raw_sha = sha256_text(raw_value)
        else:
            raw_value = _json(raw_json, f"record {row['source_record_id']} raw")
            contract_raw_json = canonical_json(raw_value)
            raw_sha = sha256_text(contract_raw_json)
        if raw_sha != row["raw_sha256"]:
            _fail(f"source_record raw checksum mismatch: {row['source_record_id']}")
        referenced[row["source_record_id"]] = (
            row,
            {
                "source_path": row["relative_path"], "pointer": row["pointer"],
                "record_kind": contract_kind, "native_id": contract_native,
                "raw_sha256": row["raw_sha256"], "raw_json": contract_raw_json,
            },
        )
    candidate_records = conn.execute(
        """SELECT DISTINCT sr.*,sf.relative_path
             FROM static_text_candidate_occurrence tc
             JOIN source_record sr ON sr.id=tc.source_record_id
             JOIN source_file sf ON sf.id=tc.source_file_id
            WHERE tc.ingest_run_id=? ORDER BY sr.id""",
        (run_id,),
    ).fetchall()
    for row in candidate_records:
        if row["record_kind"] != "textmap_entry":
            _fail(f"TextMap source_record kind mismatch: {row['id']}")
        raw_value = _json(row["raw_json"], f"TextMap record {row['id']} raw")
        if not isinstance(raw_value, str) or sha256_text(raw_value) != row["raw_sha256"]:
            _fail(f"TextMap source_record checksum mismatch: {row['id']}")
        referenced[row["id"]] = (
            row,
            {
                "source_path": row["relative_path"], "pointer": row["pointer"],
                "record_kind": "static_textmap_entry", "native_id": row["native_id"],
                "raw_sha256": row["raw_sha256"], "raw_json": canonical_json(raw_value),
            },
        )
    for record_id, (row, _) in referenced.items():
        expected_id = digest_id("sr", row["source_file_id"], row["pointer"])
        if record_id != expected_id:
            _fail(f"source_record occurrence identity mismatch: {record_id}")
    return [item[1] for item in referenced.values()], {key: item[0] for key, item in referenced.items()}


def _contract_from_segments(
    conn: sqlite3.Connection,
    manifest: StaticPhase4B3Manifest,
    identity: dict[str, Any],
    family_by_container: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    rows = conn.execute(
        """SELECT s.*,sf.relative_path,sr.pointer
             FROM static_segment_occurrence s
             JOIN static_evidence_container c ON c.id=s.evidence_container_id
             JOIN source_file sf ON sf.id=c.source_file_id
             JOIN source_record sr ON sr.id=s.source_record_id
            WHERE s.ingest_run_id=? ORDER BY s.id""",
        (identity["ingest_run_id"],),
    ).fetchall()
    contracts: list[dict[str, Any]] = []
    local: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        expected_id = digest_id(
            "static-segment", identity["snapshot_id"], manifest.raw_sha256, identity["ingest_run_id"],
            row["relative_path"], row["pointer"], row["file_local_ordinal"], row["byte_start"], row["byte_end"],
            row["character_start"], row["character_end"],
        )
        if row["id"] != expected_id or row["source_pointer"] != f"/lines/{row['file_local_ordinal']}":
            _fail(f"segment occurrence identity/pointer mismatch: {row['id']}")
        metadata = _exact_object(
            _json(row["metadata_json"], f"segment {row['id']} metadata"),
            {"family", "identity_scope", "cross_language_alignment"},
            f"segment {row['id']} metadata",
        )
        family = family_by_container.get(row["evidence_container_id"])
        if metadata != {
            "family": family, "identity_scope": "snapshot_run_container_occurrence",
            "cross_language_alignment": "unresolved",
        }:
            _fail(f"segment metadata mismatch: {row['id']}")
        if (
            row["container_kind"] != "readable_file"
            or row["pointer"] != "/text"
            or row["segmentation_version"] != manifest.versions["segmentation"]
            or normalize_text(row["raw_text"]) != row["normalized_text"]
            or sha256_text(row["raw_text"]) != row["raw_checksum"]
            or sha256_text(row["normalized_text"]) != row["normalized_checksum"]
        ):
            _fail(f"segment attribute/checksum mismatch: {row['id']}")
        base = {
            "file_local_ordinal": row["file_local_ordinal"], "byte_start": row["byte_start"],
            "byte_end": row["byte_end"], "character_start": row["character_start"],
            "character_end": row["character_end"], "raw_text": row["raw_text"],
            "normalized_text": row["normalized_text"], "raw_checksum": row["raw_checksum"],
            "normalized_checksum": row["normalized_checksum"],
        }
        local[row["relative_path"]].append(base)
        contracts.append({"family": family, "source_path": row["relative_path"], "locale": row["locale"], **base})
    return contracts, local


def _contract_from_relationships(
    conn: sqlite3.Connection,
    manifest: StaticPhase4B3Manifest,
    identity: dict[str, Any],
    container_by_id: dict[str, sqlite3.Row],
    family_by_container: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, sqlite3.Row]]:
    rows = conn.execute(
        """SELECT r.*,sf.relative_path,sr.pointer,
                  tc.ingest_run_id AS target_run_id,tc.source_family AS concrete_target_family,
                  tsf.relative_path AS target_relative_path,tsr.pointer AS target_pointer
             FROM static_relationship_occurrence r
             JOIN static_evidence_container c ON c.id=r.source_container_id
             JOIN source_file sf ON sf.id=c.source_file_id
             JOIN source_record sr ON sr.id=r.source_record_id
             LEFT JOIN static_evidence_container tc ON tc.id=r.target_evidence_container_id
             LEFT JOIN source_file tsf ON tsf.id=tc.source_file_id
             LEFT JOIN source_record tsr ON tsr.id=tc.source_record_id
            WHERE r.ingest_run_id=? ORDER BY r.id""",
        (identity["ingest_run_id"],),
    ).fetchall()
    contracts: list[dict[str, Any]] = []
    by_id: dict[str, sqlite3.Row] = {}
    for row in rows:
        expected_id = digest_id(
            "static-relationship", identity["snapshot_id"], manifest.raw_sha256, identity["ingest_run_id"],
            row["relative_path"], row["pointer"], row["source_pointer"],
        )
        if row["id"] != expected_id or row["snapshot_id"] != identity["snapshot_id"]:
            _fail(f"relationship occurrence identity mismatch: {row['id']}")
        family = family_by_container.get(row["source_container_id"])
        metadata = _exact_object(
            _json(row["metadata_json"], f"relationship {row['id']} metadata"),
            {"family", "raw_value", "manifest_id", "candidate_resolution_detail"},
            f"relationship {row['id']} metadata",
        )
        if metadata != {
            "family": family, "raw_value": metadata["raw_value"], "manifest_id": manifest.manifest_id,
            "candidate_resolution_detail": "family_specific_raw_value_and_sealed_candidate_set",
        }:
            _fail(f"relationship metadata mismatch: {row['id']}")
        target_key = _json(row["target_candidate_key_json"], f"relationship {row['id']} target key")
        ambiguity = _json(row["ambiguity_json"], f"relationship {row['id']} ambiguity")
        target_record = None
        target_id = row["target_evidence_container_id"]
        if target_id is not None:
            if row["candidate_resolution_status"] != "resolved":
                _fail(f"non-resolved relationship selected target: {row['id']}")
            if target_id not in container_by_id or row["target_run_id"] != identity["ingest_run_id"]:
                _fail(f"relationship target is outside the B3 run: {row['id']}")
            if row["concrete_target_family"] != row["target_family"]:
                _fail(f"relationship concrete target family mismatch: {row['id']}")
            if row["target_family"] == "Readable":
                target_record = {
                    "family": "Readable", "relative_path": row["target_relative_path"],
                    "pointer": row["target_pointer"],
                }
            else:
                target_record = {"table": row["target_family"], "pointer": row["target_pointer"]}
        elif row["candidate_resolution_status"] != "resolved" and row["target_evidence_container_id"] is not None:
            _fail(f"unresolved relationship has target: {row['id']}")
        contract = {
            "family": family, "source_table": row["source_family"],
            "source_record_pointer": row["pointer"], "source_pointer": row["source_pointer"],
            "relation_kind": row["relation_kind"], "field_role": row["field_role"],
            "raw_value": metadata["raw_value"], "target_family": row["target_family"],
            "target_candidate_key": target_key, "status": row["candidate_resolution_status"],
            "target_record": target_record, "ambiguity": ambiguity,
        }
        contracts.append(contract)
        by_id[row["id"]] = row
    return contracts, by_id


def _owner_from_relationship(row: sqlite3.Row, family: str) -> dict[str, Any]:
    field = str(row["source_pointer"]).rsplit("/", 1)[-1]
    return {
        "family": family, "source_table": row["source_family"],
        "source_record_pointer": row["pointer"], "source_pointer": row["source_pointer"],
        "field": field, "textmap_hash": str(_json(row["target_candidate_key_json"], "TextMap target key")["textmap_hash"]),
    }


def _contract_from_text(
    conn: sqlite3.Connection,
    manifest: StaticPhase4B3Manifest,
    identity: dict[str, Any],
    relationship_by_id: dict[str, sqlite3.Row],
    family_by_container: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    resolution_rows = conn.execute(
        """SELECT tr.*,sf.relative_path,sr.pointer
             FROM static_text_resolution_occurrence tr
             JOIN static_evidence_container c ON c.id=tr.source_container_id
             JOIN source_file sf ON sf.id=c.source_file_id
             JOIN source_record sr ON sr.id=tr.source_record_id
            WHERE tr.ingest_run_id=? ORDER BY tr.id""",
        (identity["ingest_run_id"],),
    ).fetchall()
    resolutions: list[dict[str, Any]] = []
    resolution_owner: dict[str, tuple[dict[str, Any], sqlite3.Row]] = {}
    for row in resolution_rows:
        relationship = relationship_by_id.get(row["static_relationship_occurrence_id"])
        if relationship is None:
            _fail(f"text resolution owner relationship is outside exact set: {row['id']}")
        family = family_by_container.get(row["source_container_id"])
        owner = _owner_from_relationship(relationship, family)
        expected_id = digest_id(
            "static-text-resolution", identity["snapshot_id"], manifest.raw_sha256,
            identity["ingest_run_id"], relationship["id"], row["locale"],
        )
        if (
            row["id"] != expected_id
            or row["source_pointer"] != relationship["source_pointer"]
            or row["textmap_hash"] != owner["textmap_hash"]
        ):
            _fail(f"text resolution occurrence identity/owner mismatch: {row['id']}")
        metadata = _exact_object(
            _json(row["metadata_json"], f"resolution {row['id']} metadata"),
            {"family", "locator_only", "owning_static_source", "both_conflict_auto_selected"},
            f"resolution {row['id']} metadata",
        )
        expected_owner = {
            "relative_path": relationship["relative_path"], "record_pointer": relationship["pointer"],
            "source_pointer": relationship["source_pointer"],
        }
        if metadata != {
            "family": family, "locator_only": True, "owning_static_source": expected_owner,
            "both_conflict_auto_selected": False,
        }:
            _fail(f"text resolution metadata mismatch: {row['id']}")
        selected = row["resolution_status"] in {"unique_main", "unique_medium", "both_equal"}
        values = (row["raw_text"], row["normalized_text"], row["raw_checksum"], row["normalized_checksum"])
        if selected:
            if any(value is None for value in values):
                _fail(f"selected text resolution has null content: {row['id']}")
            if normalize_text(row["raw_text"]) != row["normalized_text"] or sha256_text(row["raw_text"]) != row["raw_checksum"] or sha256_text(row["normalized_text"]) != row["normalized_checksum"]:
                _fail(f"selected text resolution checksum mismatch: {row['id']}")
        elif any(value is not None for value in values):
            _fail(f"missing/conflict resolution selected text: {row['id']}")
        contract = {
            **owner, "locale": row["locale"], "status": row["resolution_status"],
            "raw_text": row["raw_text"], "normalized_text": row["normalized_text"],
            "raw_checksum": row["raw_checksum"], "normalized_checksum": row["normalized_checksum"],
        }
        resolutions.append(contract)
        resolution_owner[row["id"]] = owner, relationship

    candidate_rows = conn.execute(
        """SELECT tc.*,sf.relative_path,sr.pointer,sr.native_id AS record_native_id,
                  sr.record_kind,sr.raw_sha256 AS record_raw_sha256,sr.raw_json
             FROM static_text_candidate_occurrence tc
             JOIN source_file sf ON sf.id=tc.source_file_id
             JOIN source_record sr ON sr.id=tc.source_record_id
            WHERE tc.ingest_run_id=? ORDER BY tc.id""",
        (identity["ingest_run_id"],),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    assets: dict[tuple[str, str], dict[str, Any]] = {}
    for row in candidate_rows:
        owner_pair = resolution_owner.get(row["resolution_occurrence_id"])
        if owner_pair is None:
            _fail(f"text candidate resolution is outside exact set: {row['id']}")
        owner, relationship = owner_pair
        expected_id = digest_id(
            "static-text-candidate", identity["snapshot_id"], manifest.raw_sha256,
            identity["ingest_run_id"], row["resolution_occurrence_id"], row["relative_path"],
            row["pointer"], row["shard_kind"],
        )
        if row["id"] != expected_id or row["pointer"] != f"/{owner['textmap_hash']}":
            _fail(f"text candidate occurrence identity/pointer mismatch: {row['id']}")
        if row["record_kind"] != "textmap_entry" or row["record_native_id"] != owner["textmap_hash"]:
            _fail(f"text candidate source_record binding mismatch: {row['id']}")
        if (
            normalize_text(row["raw_text"]) != row["normalized_text"]
            or sha256_text(row["raw_text"]) != row["raw_checksum"]
            or sha256_text(row["normalized_text"]) != row["normalized_checksum"]
            or row["record_raw_sha256"] != row["raw_checksum"]
            or _json(row["raw_json"], f"candidate {row['id']} raw") != row["raw_text"]
        ):
            _fail(f"text candidate content/checksum mismatch: {row['id']}")
        metadata = _exact_object(
            _json(row["metadata_json"], f"candidate {row['id']} metadata"),
            {"family", "textmap_hash_is_locator_not_identity", "owning_relationship_id", "exact_textmap_source"},
            f"candidate {row['id']} metadata",
        )
        if metadata != {
            "family": owner["family"], "textmap_hash_is_locator_not_identity": True,
            "owning_relationship_id": relationship["id"],
            "exact_textmap_source": {"relative_path": row["relative_path"], "record_pointer": row["pointer"]},
        }:
            _fail(f"text candidate metadata mismatch: {row['id']}")
        contract = {
            **owner, "locale": row["locale"], "status": row["resolution_status"],
            "shard_kind": row["shard_kind"], "source_path": row["relative_path"],
            "source_pointer": row["pointer"], "raw_text": row["raw_text"],
            "normalized_text": row["normalized_text"], "raw_checksum": row["raw_checksum"],
            "normalized_checksum": row["normalized_checksum"],
        }
        candidates.append(contract)
        asset = {
            "source_path": row["relative_path"], "source_pointer": row["pointer"],
            "locale": row["locale"], "shard_kind": row["shard_kind"],
            "textmap_hash": owner["textmap_hash"], "raw_checksum": row["raw_checksum"],
        }
        prior = assets.setdefault((row["relative_path"], row["pointer"]), asset)
        if prior != asset:
            _fail(f"TextMap asset provenance is inconsistent: {row['relative_path']}#{row['pointer']}")
    return resolutions, candidates, list(assets.values())


def verify_phase4b3_database_contract(
    conn: sqlite3.Connection,
    manifest: StaticPhase4B3Manifest,
    source_root: str | Path,
    *,
    validated_source_records: dict[str, SourceRecordContract] | None = None,
) -> dict[str, Any]:
    schema_version = _verify_schema(conn)
    identity = expected_phase4b3_identity(manifest)
    _verify_binding(conn, manifest, identity)
    _verify_files(conn, manifest, identity, source_root)
    container_rows = _container_rows(conn, identity["ingest_run_id"])
    containers, container_by_id, family_by_container, classifications = _contract_from_containers(
        container_rows, manifest, identity
    )
    source_records, record_rows = _contract_from_records(
        conn, container_rows, identity["ingest_run_id"]
    )
    segments, segments_by_path = _contract_from_segments(conn, manifest, identity, family_by_container)
    relationships, relationship_by_id = _contract_from_relationships(
        conn, manifest, identity, container_by_id, family_by_container
    )
    resolutions, candidates, text_assets = _contract_from_text(
        conn, manifest, identity, relationship_by_id, family_by_container
    )
    file_contracts = [
        {"relative_path": item.relative_path, "format": item.format, "size_bytes": item.size_bytes, "sha256": item.sha256}
        for item in manifest.files
    ]
    contracts = {
        "files": file_contracts, "source_records": source_records, "containers": containers,
        "segments": segments, "relationships": relationships, "text_resolutions": resolutions,
        "text_candidates": candidates, "text_assets": text_assets,
    }
    digests = {key: _set_digest(value) for key, value in contracts.items()}
    if digests != manifest.expected["occurrence_set_digests"]:
        differing = [key for key in digests if digests[key] != manifest.expected["occurrence_set_digests"][key]]
        _fail(f"database occurrence set/attribute digest mismatch: {differing}")
    counts = {
        "files": len(file_contracts), "source_records": len(source_records), "containers": len(containers),
        "segments": len(segments), "relationships": len(relationships),
        "text_resolutions": len(resolutions), "text_candidates": len(candidates),
    }
    expected_counts = {"files": manifest.expected["counts"]["selected_files"], **{
        key: manifest.expected["counts"][key]
        for key in ("source_records", "containers", "segments", "relationships", "text_resolutions", "text_candidates")
    }}
    if counts != expected_counts:
        _fail(f"database occurrence counts mismatch: {counts}")

    for readable in manifest.readables:
        path = readable["relative_path"]
        local = segments_by_path.get(path, [])
        if len(local) != readable["segment_count"] or _set_digest(local) != readable["segment_set_digest"]:
            _fail(f"per-Readable segment contract mismatch: {path}")
        container_id = digest_id(
            "static-container", identity["snapshot_id"], manifest.raw_sha256,
            identity["ingest_run_id"], path, "/text",
        )
        if classifications.get(container_id) != readable["classification"]:
            _fail(f"Readable classification mismatch: {path}")

    status_counts = {
        "relationships": {
            status: Counter(item["status"] for item in relationships)[status]
            for status in ("resolved", "dangling", "ambiguous", "unresolved")
        },
        "text_resolutions": {
            status: Counter(item["status"] for item in resolutions)[status]
            for status in ("unique_main", "unique_medium", "both_equal", "both_conflict", "missing")
        },
        "readables": {
            status: Counter(classifications.values())[status]
            for status in ("narrative", "empty", "blank_whitespace_only", "placeholder_exact_test")
        },
    }
    expected_relationship_status = {
        key: manifest.expected["relationship_statuses"][key]
        for key in ("resolved", "dangling", "ambiguous", "unresolved")
    }
    if (
        status_counts["relationships"] != expected_relationship_status
        or status_counts["text_resolutions"] != manifest.expected["text_resolution_statuses"]
        or status_counts["readables"] != manifest.expected["readable_classifications"]
    ):
        _fail(f"database preserved-status reconciliation mismatch: {status_counts}")

    family_counts: dict[str, dict[str, int]] = {}
    family_paths: dict[str, list[str]] = {}
    textmap_paths = {
        path
        for locale_paths in manifest.boundaries["textmap_shards"].values()
        for path in locale_paths.values()
    }
    for family in ("books", "weapons"):
        paths = {
            item["source_path"] for item in containers if item["family"] == family
        } | {
            item["source_path"] for item in candidates if item["family"] == family
        } | textmap_paths
        record_coordinates = {
            (item["source_path"], item["record_pointer"])
            for item in containers if item["family"] == family
        } | {
            (item["source_path"], item["source_pointer"])
            for item in candidates if item["family"] == family
        }
        family_paths[family] = sorted(paths)
        family_counts[family] = {
            "selected_files": len(paths), "source_records": len(record_coordinates),
            "containers": sum(item["family"] == family for item in containers),
            "segments": sum(item["family"] == family for item in segments),
            "relationships": sum(item["family"] == family for item in relationships),
            "text_resolutions": sum(item["family"] == family for item in resolutions),
            "text_candidates": sum(item["family"] == family for item in candidates),
        }
        for key in ("containers", "segments", "relationships", "text_resolutions", "text_candidates"):
            if family_counts[family][key] != manifest.expected["families"][family][key]:
                _fail(f"family occurrence reconciliation mismatch: {family}.{key}")

    locale_breakdown = {
        locale: {
            "readable_containers": sum(item["locale"] == locale and item["source_family"] == "Readable" for item in containers),
            "segments": sum(item["locale"] == locale for item in segments),
            "text_resolutions": sum(item["locale"] == locale for item in resolutions),
            "text_candidates": sum(item["locale"] == locale for item in candidates),
        }
        for locale in ("CHS", "EN", "JP")
    }
    if locale_breakdown != manifest.expected["locale_breakdown"]:
        _fail(f"locale occurrence reconciliation mismatch: {locale_breakdown}")

    foreign_keys = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
    if foreign_keys:
        _fail(f"foreign_key_check failed: {foreign_keys[:3]}")
    if validated_source_records is not None:
        for record_id, row in record_rows.items():
            native_key = (
                "record_native_id"
                if "record_native_id" in row.keys()
                else "native_id"
            )
            native_id = row[native_key]
            contract = SourceRecordContract(
                id=record_id,
                source_file_id=str(row["source_file_id"]),
                pointer=str(row["pointer"]),
                record_kind=str(row["record_kind"]),
                native_id=None if native_id is None else str(native_id),
                raw_sha256=str(row["raw_sha256"]),
                raw_json=None if row["raw_json"] is None else str(row["raw_json"]),
            )
            prior = validated_source_records.setdefault(contract.id, contract)
            if prior != contract:
                _fail(f"source_record contract conflict: {contract.id}")
    return {
        "protocol": "wave1-stage-final-state-contract-v1",
        "contract_id": (
            "wave1-stage-final-state-v1:phase4b3:" + manifest.manifest_id
        ),
        "status": "pass",
        "run_id": identity["ingest_run_id"],
        "parser_build_id": identity["parser_build_id"],
        "manifest_sha256": manifest.raw_sha256,
        "exact_counts": counts,
        "required_checks": [
            "schema", "source_binding", "run_binding", "exact_counts",
            "full_rows", "occurrence_digests", "logical_boundary", "foreign_keys",
        ],
        "schema_version": schema_version,
        "identity": identity,
        "counts": counts,
        "family_counts": family_counts,
        "family_selected_paths": family_paths,
        "status_counts": status_counts,
        "locale_breakdown": locale_breakdown,
        "occurrence_set_digests": digests,
        "foreign_key_check": [],
    }
