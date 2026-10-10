"""Readonly release gate for the sealed Phase 4B2 static evidence run."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from .character_phase4c2_schema import (
    CharacterPhase4C2SchemaError,
    verify_character_phase4c2_schema_signature,
)
from ._readonly_database import open_readonly_database
from .source_record_contract import SourceRecordContract
from .static_phase4b2_manifest import (
    LOCALES,
    StaticPhase4B2Manifest,
    load_static_phase4b2_manifest,
)
from .static_phase4b2_verification_contract import (
    ExpectedRelationship as _ExpectedRelationship,
    Phase4B2ExpectedOccurrenceBundle,
    expected_phase4b2_identity,
    verify_phase4b2_relationship_text_occurrence_sets,
    verify_phase4b2_schema_coverage,
    verify_phase4b2_source_reference_coverage,
    verify_phase4b2_sources_containers_segments,
)
from .util import canonical_json, normalize_text, sha256_text


class StaticPhase4B2VerificationError(RuntimeError):
    """The database, build report, or sealed manifest failed the B2 gate."""


def _error(message: str) -> StaticPhase4B2VerificationError:
    return StaticPhase4B2VerificationError(message)


def _fingerprint(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        stat = path.stat()
    except OSError as exc:
        raise _error(f"cannot fingerprint database {path}: {exc}") from exc
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _assert_unchanged(path: Path, before: dict[str, Any]) -> None:
    after = _fingerprint(path)
    if after != before:
        raise _error(
            "database fingerprint changed during Phase 4B2 verification: "
            f"before={before}, after={after}"
        )


def _load_build_report(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _error(f"cannot read Phase 4B2 build report {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise _error("Phase 4B2 build report must be a JSON object")
    required = {
        "status", "snapshot_id", "parser_build_id", "ingest_run_id",
        "config_checksum", "database_fingerprint", "manifest", "counts",
        "family_counts", "resolution_statuses", "scope",
    }
    missing = sorted(required - set(value))
    if missing:
        raise _error(f"Phase 4B2 build report missing fields: {missing}")
    if value["status"] != "complete":
        raise _error("Phase 4B2 build report status is not complete")
    for key in ("snapshot_id", "parser_build_id", "ingest_run_id", "config_checksum"):
        if not isinstance(value[key], str) or not value[key]:
            raise _error(f"Phase 4B2 build report {key} must be a non-empty string")
    return value


def _expected_counts(manifest: StaticPhase4B2Manifest) -> dict[str, int]:
    counts = manifest.expected["counts"]
    return {
        "files": int(counts["selected_files"]),
        "containers": int(counts["containers"]),
        "segments": int(counts["segments"]),
        "relationships": int(counts["relationships"]),
        "text_resolutions": int(counts["text_resolutions"]),
        "text_candidates": int(counts["text_candidates"]),
    }


def _verify_build_binding(
    build: dict[str, Any],
    manifest: StaticPhase4B2Manifest,
    database_fingerprint: dict[str, Any],
) -> None:
    expected_manifest = {
        "manifest_id": manifest.manifest_id,
        "raw_sha256": manifest.raw_sha256,
    }
    if build["manifest"] != expected_manifest:
        raise _error("Phase 4B2 build report manifest binding mismatch")
    if build["counts"] != _expected_counts(manifest):
        raise _error("Phase 4B2 build report exact count binding mismatch")
    if build["family_counts"] != manifest.expected["families"]:
        raise _error("Phase 4B2 build report family count binding mismatch")
    if build["resolution_statuses"] != manifest.expected["resolution_statuses"]:
        raise _error("Phase 4B2 build report resolution status binding mismatch")
    expected_paths = [item.relative_path for item in manifest.files]
    if build["scope"].get("selected_paths") != expected_paths:
        raise _error("Phase 4B2 build report selected path binding mismatch")
    if build["database_fingerprint"] != database_fingerprint:
        raise _error("Phase 4B2 build report database fingerprint mismatch")


def _one(conn: sqlite3.Connection, sql: str, parameters: tuple[Any, ...] = ()) -> sqlite3.Row:
    row = conn.execute(sql, parameters).fetchone()
    if row is None:
        raise _error("Phase 4B2 database is missing a required bound row")
    return row


def _run_counts(conn: sqlite3.Connection, run_id: str) -> dict[str, int]:
    tables = {
        "containers": "static_evidence_container",
        "segments": "static_segment_occurrence",
        "relationships": "static_relationship_occurrence",
        "text_resolutions": "static_text_resolution_occurrence",
        "text_candidates": "static_text_candidate_occurrence",
    }
    return {
        key: int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE ingest_run_id=?", (run_id,)
            ).fetchone()[0]
        )
        for key, table in tables.items()
    }


def _family_counts(conn: sqlite3.Connection, run_id: str) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    table_map = {
        "containers": ("static_evidence_container", "metadata_json"),
        "segments": ("static_segment_occurrence", "metadata_json"),
        "relationships": ("static_relationship_occurrence", "metadata_json"),
        "text_resolutions": ("static_text_resolution_occurrence", "metadata_json"),
        "text_candidates": ("static_text_candidate_occurrence", "metadata_json"),
    }
    for family in ("book1257", "weapon15516", "reliquary15048", "materials"):
        result[family] = {
            key: int(
                conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE ingest_run_id=? "
                    f"AND json_extract({metadata},'$.family_key')=?",
                    (run_id, family),
                ).fetchone()[0]
            )
            for key, (table, metadata) in table_map.items()
        }
    return result


def _verify_selected_files(
    conn: sqlite3.Connection,
    snapshot_id: str,
    manifest: StaticPhase4B2Manifest,
) -> None:
    for item in manifest.files:
        row = _one(
            conn,
            """SELECT sha256,size_bytes,format FROM source_file
                WHERE snapshot_id=? AND relative_path=?""",
            (snapshot_id, item.relative_path),
        )
        if (row["sha256"], int(row["size_bytes"]), row["format"]) != (
            item.sha256,
            item.size_bytes,
            item.format,
        ):
            raise _error(f"sealed source file identity mismatch: {item.relative_path}")


def _verify_run_binding(
    conn: sqlite3.Connection,
    manifest: StaticPhase4B2Manifest,
    build: dict[str, Any],
) -> dict[str, Any]:
    run_id = build["ingest_run_id"]
    run = _one(
        conn,
        """SELECT snapshot_id,parser_build_id,scope_json,config_json,status
             FROM ingest_run WHERE id=?""",
        (run_id,),
    )
    if run["status"] != "complete":
        raise _error("Phase 4B2 ingest run is not complete")
    if run["snapshot_id"] != build["snapshot_id"]:
        raise _error("Phase 4B2 snapshot/run binding mismatch")
    if run["parser_build_id"] != build["parser_build_id"]:
        raise _error("Phase 4B2 parser/run binding mismatch")
    expected_scope = {
        "family": "static_phase4b2_multifamily",
        "manifest_id": manifest.manifest_id,
        "selected_paths": [item.relative_path for item in manifest.files],
    }
    expected_config = {
        "manifest_raw_sha256": manifest.raw_sha256,
        "versions": manifest.versions,
        "full_static_scan": False,
        "logical_identity_created": False,
        "textmap_evidence_container_created": False,
    }
    if run["scope_json"] != canonical_json(expected_scope):
        raise _error("Phase 4B2 ingest run scope mismatch")
    if run["config_json"] != canonical_json(expected_config):
        raise _error("Phase 4B2 ingest run config mismatch")

    snapshot = _one(
        conn,
        'SELECT source_id,release_label,"commit",tree FROM source_snapshot WHERE id=?',
        (build["snapshot_id"],),
    )
    expected_snapshot = (
        manifest.source["source_id"], manifest.source["release_label"],
        manifest.source["commit"], manifest.source["tree"],
    )
    if tuple(snapshot) != expected_snapshot:
        raise _error("Phase 4B2 source snapshot pin mismatch")
    parser = _one(
        conn,
        """SELECT parser_version,mapping_version,fingerprint,config_checksum
             FROM parser_build WHERE id=?""",
        (build["parser_build_id"],),
    )
    expected_parser = (
        manifest.versions["parser"], manifest.versions["projection"],
        "static-phase4b2-sealed-multifamily-evidence", build["config_checksum"],
    )
    if tuple(parser) != expected_parser:
        raise _error("Phase 4B2 parser build binding mismatch")
    _verify_selected_files(conn, build["snapshot_id"], manifest)
    return {
        "ingest_run_id": run_id,
        "snapshot_id": build["snapshot_id"],
        "parser_build_id": build["parser_build_id"],
        "status": run["status"],
        "scope": expected_scope,
        "config": expected_config,
    }


def _extract_json_pointer(value: Any, pointer: str) -> Any:
    current = value
    if pointer in ("", "/"):
        return current
    if not pointer.startswith("/"):
        raise _error(f"invalid JSON pointer: {pointer}")
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            if isinstance(current, list):
                current = current[int(part)]
            elif isinstance(current, dict):
                current = current[part]
            else:
                raise KeyError(part)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise _error(f"cannot resolve pointer {pointer} in raw JSON: {exc}") from exc
    return current


def _expected_weapon_relationships(manifest: StaticPhase4B2Manifest) -> list[_ExpectedRelationship]:
    family = manifest.families["weapon15516"]
    weapon_rec = family["weapon"]["record"]
    codex_rec = family["codex"]["record"]
    doc_rec = family["document"]["record"]
    loc_rec = family["localization"]["record"]
    rels: list[_ExpectedRelationship] = []

    c_rel = family["codex"]["weapon_relationship"]
    rels.append(
        _ExpectedRelationship(
            family_key="weapon15516",
            source_file_path=codex_rec["relative_path"],
            source_record_pointer=codex_rec["pointer"],
            source_record_native_id=str(codex_rec["native_id"]),
            source_family=codex_rec["table"],
            source_pointer=f"{codex_rec['pointer']}/{c_rel['source_field']}",
            relation_kind="weapon_codex_weapon_candidate",
            field_role="foreign_pointer_candidate",
            target_family=c_rel["target"]["table"],
            target_candidate_key={"native_id": str(c_rel["target"]["native_id"]), "table": c_rel["target"]["table"]},
            target_coordinate=(weapon_rec["relative_path"], c_rel["target"]["record_pointer"]),
            target_record_native_id=str(weapon_rec["native_id"]),
            expected_raw_value=c_rel["raw_value"],
        )
    )

    w_rel = family["weapon"]["story_relationship"]
    rels.append(
        _ExpectedRelationship(
            family_key="weapon15516",
            source_file_path=weapon_rec["relative_path"],
            source_record_pointer=weapon_rec["pointer"],
            source_record_native_id=str(weapon_rec["native_id"]),
            source_family=weapon_rec["table"],
            source_pointer=f"{weapon_rec['pointer']}/{w_rel['source_field']}",
            relation_kind="weapon_story_document_candidate",
            field_role="foreign_pointer_candidate",
            target_family=w_rel["target"]["table"],
            target_candidate_key={"native_id": str(w_rel["target"]["native_id"]), "table": w_rel["target"]["table"]},
            target_coordinate=(doc_rec["relative_path"], w_rel["target"]["record_pointer"]),
            target_record_native_id=str(doc_rec["native_id"]),
            expected_raw_value=w_rel["raw_value"],
        )
    )

    d_rel = family["document"]["localization_relationship"]
    rels.append(
        _ExpectedRelationship(
            family_key="weapon15516",
            source_file_path=doc_rec["relative_path"],
            source_record_pointer=doc_rec["pointer"],
            source_record_native_id=str(doc_rec["native_id"]),
            source_family=doc_rec["table"],
            source_pointer=f"{doc_rec['pointer']}{d_rel['source_pointer_suffix']}",
            relation_kind="document_localization_candidate",
            field_role="foreign_pointer_candidate",
            target_family=d_rel["target"]["table"],
            target_candidate_key={"native_id": str(d_rel["target"]["native_id"]), "table": d_rel["target"]["table"]},
            target_coordinate=(loc_rec["relative_path"], d_rel["target"]["record_pointer"]),
            target_record_native_id=str(loc_rec["native_id"]),
            expected_raw_value=d_rel["raw_value"],
        )
    )

    for field, spec in family["localization"]["readable_fields"].items():
        role = "unknown_obfuscated" if field in {"DEFNEHAFMMA", "JDNBKKPEFAI"} else "unresolved_field_role"
        target = spec["target"]
        rels.append(
            _ExpectedRelationship(
                family_key="weapon15516",
                source_file_path=loc_rec["relative_path"],
                source_record_pointer=loc_rec["pointer"],
                source_record_native_id=str(loc_rec["native_id"]),
                source_family=loc_rec["table"],
                source_pointer=f"{loc_rec['pointer']}/{field}",
                relation_kind="localization_readable_candidate",
                field_role=role,
                target_family="Readable",
                target_candidate_key={"locale": target["locale"], "relative_path": target["relative_path"]},
                target_coordinate=(target["relative_path"], target["record_pointer"]),
                target_record_native_id=Path(target["relative_path"]).stem,
                expected_raw_value=spec["raw_value"],
            )
        )

    for field, hash_val in family["weapon"]["text_fields"].items():
        rels.append(
            _ExpectedRelationship(
                family_key="weapon15516",
                source_file_path=weapon_rec["relative_path"],
                source_record_pointer=weapon_rec["pointer"],
                source_record_native_id=str(weapon_rec["native_id"]),
                source_family=weapon_rec["table"],
                source_pointer=f"{weapon_rec['pointer']}/{field}",
                relation_kind="weapon_textmap_candidate",
                field_role="verified_textmap_reference",
                target_family="TextMap",
                target_candidate_key={"textmap_hash": str(hash_val)},
                target_coordinate=None,
                target_record_native_id=None,
                expected_raw_value=hash_val,
            )
        )

    for field, hash_val in family["document"]["text_fields"].items():
        rels.append(
            _ExpectedRelationship(
                family_key="weapon15516",
                source_file_path=doc_rec["relative_path"],
                source_record_pointer=doc_rec["pointer"],
                source_record_native_id=str(doc_rec["native_id"]),
                source_family=doc_rec["table"],
                source_pointer=f"{doc_rec['pointer']}/{field}",
                relation_kind="document_title_textmap_candidate",
                field_role="verified_textmap_reference",
                target_family="TextMap",
                target_candidate_key={"textmap_hash": str(hash_val)},
                target_coordinate=None,
                target_record_native_id=None,
                expected_raw_value=hash_val,
            )
        )

    return rels


def _expected_reliquary_relationships(manifest: StaticPhase4B2Manifest) -> list[_ExpectedRelationship]:
    family = manifest.families["reliquary15048"]
    set_rec = family["set"]["record"]
    rels: list[_ExpectedRelationship] = []

    for item in family["set"]["contains_relationships"]:
        target = item["target"]
        rels.append(
            _ExpectedRelationship(
                family_key="reliquary15048",
                source_file_path=set_rec["relative_path"],
                source_record_pointer=set_rec["pointer"],
                source_record_native_id=str(set_rec["native_id"]),
                source_family=set_rec["table"],
                source_pointer=f"{set_rec['pointer']}/containsList/{item['index']}",
                relation_kind="reliquary_set_contains_candidate",
                field_role="foreign_pointer_candidate",
                target_family=target["table"],
                target_candidate_key={"native_id": str(target["native_id"]), "table": target["table"]},
                target_coordinate=("ExcelBinOutput/ReliquaryExcelConfigData.json", target["record_pointer"]),
                target_record_native_id=str(target["native_id"]),
                expected_raw_value=item["raw_value"],
            )
        )

    for codex in family["codex"]:
        c_rec = codex["record"]
        suit = codex["suit_relationship"]
        s_target = suit["target"]
        rels.append(
            _ExpectedRelationship(
                family_key="reliquary15048",
                source_file_path=c_rec["relative_path"],
                source_record_pointer=c_rec["pointer"],
                source_record_native_id=str(c_rec["native_id"]),
                source_family=c_rec["table"],
                source_pointer=f"{c_rec['pointer']}/{suit['source_field']}",
                relation_kind="reliquary_codex_set_candidate",
                field_role="foreign_pointer_candidate",
                target_family=s_target["table"],
                target_candidate_key={"native_id": str(s_target["native_id"]), "table": s_target["table"]},
                target_coordinate=("ExcelBinOutput/ReliquarySetExcelConfigData.json", s_target["record_pointer"]),
                target_record_native_id=str(s_target["native_id"]),
                expected_raw_value=suit["raw_value"],
            )
        )
        for slot_field in ("capId", "cupId", "flowerId", "leatherId", "sandId"):
            slot = codex["slots"][slot_field]
            slot_target = slot["target"]
            rels.append(
                _ExpectedRelationship(
                    family_key="reliquary15048",
                    source_file_path=c_rec["relative_path"],
                    source_record_pointer=c_rec["pointer"],
                    source_record_native_id=str(c_rec["native_id"]),
                    source_family=c_rec["table"],
                    source_pointer=f"{c_rec['pointer']}/{slot_field}",
                    relation_kind="reliquary_codex_piece_candidate",
                    field_role="foreign_pointer_candidate",
                    target_family=slot_target["table"],
                    target_candidate_key={"native_id": str(slot_target["native_id"]), "table": slot_target["table"]},
                    target_coordinate=("ExcelBinOutput/ReliquaryExcelConfigData.json", slot_target["record_pointer"]),
                    target_record_native_id=str(slot_target["native_id"]),
                    expected_raw_value=slot["raw_value"],
                )
            )

    for piece in family["pieces"]:
        p_rec = piece["record"]
        set_rel = piece["set_relationship"]
        st = set_rel["target"]
        rels.append(
            _ExpectedRelationship(
                family_key="reliquary15048",
                source_file_path=p_rec["relative_path"],
                source_record_pointer=p_rec["pointer"],
                source_record_native_id=str(p_rec["native_id"]),
                source_family=p_rec["table"],
                source_pointer=f"{p_rec['pointer']}/{set_rel['source_field']}",
                relation_kind="reliquary_piece_set_candidate",
                field_role="foreign_pointer_candidate",
                target_family=st["table"],
                target_candidate_key={"native_id": str(st["native_id"]), "table": st["table"]},
                target_coordinate=("ExcelBinOutput/ReliquarySetExcelConfigData.json", st["record_pointer"]),
                target_record_native_id=str(st["native_id"]),
                expected_raw_value=set_rel["raw_value"],
            )
        )
        story_rel = piece["story_relationship"]
        s_target = story_rel["target"]
        rels.append(
            _ExpectedRelationship(
                family_key="reliquary15048",
                source_file_path=p_rec["relative_path"],
                source_record_pointer=p_rec["pointer"],
                source_record_native_id=str(p_rec["native_id"]),
                source_family=p_rec["table"],
                source_pointer=f"{p_rec['pointer']}/{story_rel['source_field']}",
                relation_kind="reliquary_piece_story_document_candidate",
                field_role="foreign_pointer_candidate",
                target_family=s_target["table"],
                target_candidate_key={"native_id": str(s_target["native_id"]), "table": s_target["table"]},
                target_coordinate=("ExcelBinOutput/DocumentExcelConfigData.json", s_target["record_pointer"]),
                target_record_native_id=str(s_target["native_id"]),
                expected_raw_value=story_rel["raw_value"],
            )
        )
        for field, hash_val in piece["text_fields"].items():
            rels.append(
                _ExpectedRelationship(
                    family_key="reliquary15048",
                    source_file_path=p_rec["relative_path"],
                    source_record_pointer=p_rec["pointer"],
                    source_record_native_id=str(p_rec["native_id"]),
                    source_family=p_rec["table"],
                    source_pointer=f"{p_rec['pointer']}/{field}",
                    relation_kind="reliquary_piece_textmap_candidate",
                    field_role="verified_textmap_reference",
                    target_family="TextMap",
                    target_candidate_key={"textmap_hash": str(hash_val)},
                    target_coordinate=None,
                    target_record_native_id=None,
                    expected_raw_value=hash_val,
                )
            )

    for story in family["stories"]:
        d_rec = story["document"]["record"]
        loc_rel = story["document"]["localization_relationship"]
        l_target = loc_rel["target"]
        rels.append(
            _ExpectedRelationship(
                family_key="reliquary15048",
                source_file_path=d_rec["relative_path"],
                source_record_pointer=d_rec["pointer"],
                source_record_native_id=str(d_rec["native_id"]),
                source_family=d_rec["table"],
                source_pointer=f"{d_rec['pointer']}{loc_rel['source_pointer_suffix']}",
                relation_kind="document_localization_candidate",
                field_role="foreign_pointer_candidate",
                target_family=l_target["table"],
                target_candidate_key={"native_id": str(l_target["native_id"]), "table": l_target["table"]},
                target_coordinate=("ExcelBinOutput/LocalizationExcelConfigData.json", l_target["record_pointer"]),
                target_record_native_id=str(l_target["native_id"]),
                expected_raw_value=loc_rel["raw_value"],
            )
        )
        for field, hash_val in story["document"]["text_fields"].items():
            rels.append(
                _ExpectedRelationship(
                    family_key="reliquary15048",
                    source_file_path=d_rec["relative_path"],
                    source_record_pointer=d_rec["pointer"],
                    source_record_native_id=str(d_rec["native_id"]),
                    source_family=d_rec["table"],
                    source_pointer=f"{d_rec['pointer']}/{field}",
                    relation_kind="document_title_textmap_candidate",
                    field_role="verified_textmap_reference",
                    target_family="TextMap",
                    target_candidate_key={"textmap_hash": str(hash_val)},
                    target_coordinate=None,
                    target_record_native_id=None,
                    expected_raw_value=hash_val,
                )
            )
        l_rec = story["localization"]["record"]
        for field, spec in story["localization"]["readable_fields"].items():
            role = "unknown_obfuscated" if field in {"DEFNEHAFMMA", "JDNBKKPEFAI"} else "unresolved_field_role"
            target = spec["target"]
            rels.append(
                _ExpectedRelationship(
                    family_key="reliquary15048",
                    source_file_path=l_rec["relative_path"],
                    source_record_pointer=l_rec["pointer"],
                    source_record_native_id=str(l_rec["native_id"]),
                    source_family=l_rec["table"],
                    source_pointer=f"{l_rec['pointer']}/{field}",
                    relation_kind="localization_readable_candidate",
                    field_role=role,
                    target_family="Readable",
                    target_candidate_key={"locale": target["locale"], "relative_path": target["relative_path"]},
                    target_coordinate=(target["relative_path"], target["record_pointer"]),
                    target_record_native_id=Path(target["relative_path"]).stem,
                    expected_raw_value=spec["raw_value"],
                )
            )

    return rels


def _expected_materials_relationships(manifest: StaticPhase4B2Manifest) -> list[_ExpectedRelationship]:
    family = manifest.families["materials"]
    rels: list[_ExpectedRelationship] = []

    for base in family["base"]:
        b_rec = base["record"]
        for field, hash_val in base["text_fields"].items():
            rels.append(
                _ExpectedRelationship(
                    family_key="materials",
                    source_file_path=b_rec["relative_path"],
                    source_record_pointer=b_rec["pointer"],
                    source_record_native_id=str(b_rec["native_id"]),
                    source_family=b_rec["table"],
                    source_pointer=f"{b_rec['pointer']}/{field}",
                    relation_kind="material_base_textmap_candidate",
                    field_role="verified_textmap_reference",
                    target_family="TextMap",
                    target_candidate_key={"textmap_hash": str(hash_val)},
                    target_coordinate=None,
                    target_record_native_id=None,
                    expected_raw_value=hash_val,
                )
            )

    c_rec = family["codex"]["record"]
    m_rel = family["codex"]["material_relationship"]
    m_target = m_rel["target"]
    rels.append(
        _ExpectedRelationship(
            family_key="materials",
            source_file_path=c_rec["relative_path"],
            source_record_pointer=c_rec["pointer"],
            source_record_native_id=str(c_rec["native_id"]),
            source_family=c_rec["table"],
            source_pointer=f"{c_rec['pointer']}/{m_rel['source_field']}",
            relation_kind="material_codex_material_candidate",
            field_role="foreign_pointer_candidate",
            target_family=m_target["table"],
            target_candidate_key={"native_id": str(m_target["native_id"]), "table": m_target["table"]},
            target_coordinate=("ExcelBinOutput/MaterialExcelConfigData.json", m_target["record_pointer"]),
            target_record_native_id=str(m_target["native_id"]),
            expected_raw_value=m_rel["raw_value"],
        )
    )
    for field, hash_val in family["codex"]["text_fields"].items():
        rels.append(
            _ExpectedRelationship(
                family_key="materials",
                source_file_path=c_rec["relative_path"],
                source_record_pointer=c_rec["pointer"],
                source_record_native_id=str(c_rec["native_id"]),
                source_family=c_rec["table"],
                source_pointer=f"{c_rec['pointer']}/{field}",
                relation_kind="material_codex_presentation_textmap_candidate",
                field_role="unresolved_field_role",
                target_family="TextMap",
                target_candidate_key={"textmap_hash": str(hash_val)},
                target_coordinate=None,
                target_record_native_id=None,
                expected_raw_value=hash_val,
            )
        )

    return rels


def _verify_exact_relationships_and_textmap(
    conn: sqlite3.Connection,
    manifest: StaticPhase4B2Manifest,
    run_id: str,
    snapshot_id: str,
) -> None:
    expected_rels: list[_ExpectedRelationship] = [
        *_expected_weapon_relationships(manifest),
        *_expected_reliquary_relationships(manifest),
        *_expected_materials_relationships(manifest),
    ]
    if len(expected_rels) != 128:
        raise _error(f"expected relationships cardinality mismatch: {len(expected_rels)} != 128")

    status_by_hash = {
        h: status
        for status, hashes in manifest.textmap["hash_statuses"].items()
        for h in hashes
    }

    verified_rel_ids: set[str] = set()

    for exp in expected_rels:
        row = conn.execute(
            """SELECT r.id, r.ingest_run_id, r.snapshot_id, r.source_family, r.source_pointer,
                      r.relation_kind, r.field_role, r.target_family, r.target_candidate_key_json,
                      r.candidate_resolution_status, r.target_evidence_container_id,
                      r.ambiguity_json, r.metadata_json,
                      sc.source_family AS source_container_source_family,
                      sc.candidate_namespace AS source_container_candidate_namespace,
                      sf.relative_path AS source_file_path,
                      sr.pointer AS source_record_pointer,
                      sr.native_id AS source_record_native_id,
                      sr.raw_json AS source_raw_json,
                      tc.source_family AS target_container_source_family,
                      tc.candidate_namespace AS target_container_candidate_namespace,
                      tf.relative_path AS target_file_path,
                      tr.pointer AS target_record_pointer,
                      tr.native_id AS target_record_native_id
                 FROM static_relationship_occurrence r
                 JOIN static_evidence_container sc ON sc.id = r.source_container_id
                 JOIN source_file sf ON sf.id = sc.source_file_id
                 JOIN source_record sr ON sr.id = sc.source_record_id
                 LEFT JOIN static_evidence_container tc ON tc.id = r.target_evidence_container_id
                 LEFT JOIN source_file tf ON tf.id = tc.source_file_id
                 LEFT JOIN source_record tr ON tr.id = tc.source_record_id
                WHERE r.ingest_run_id = ?
                  AND sf.relative_path = ?
                  AND sr.pointer = ?
                  AND r.source_pointer = ?""",
            (run_id, exp.source_file_path, exp.source_record_pointer, exp.source_pointer),
        ).fetchone()

        if row is None:
            raise _error(
                f"missing expected relationship: {exp.source_file_path}#{exp.source_pointer}"
            )

        verified_rel_ids.add(row["id"])

        if (
            row["ingest_run_id"] != run_id
            or row["snapshot_id"] != snapshot_id
            or row["source_family"] != exp.source_family
            or row["source_container_source_family"] != exp.source_family
            or row["source_container_candidate_namespace"] != exp.source_family
            or row["source_record_native_id"] != exp.source_record_native_id
            or row["relation_kind"] != exp.relation_kind
            or row["field_role"] != exp.field_role
            or row["target_family"] != exp.target_family
            or row["candidate_resolution_status"] != "resolved"
            or row["ambiguity_json"] != "{}"
        ):
            raise _error(f"relationship attribute mismatch at {exp.source_pointer}")

        actual_key = json.loads(row["target_candidate_key_json"])
        if actual_key != exp.target_candidate_key:
            raise _error(
                f"relationship candidate key mismatch at {exp.source_pointer}: "
                f"actual={actual_key} expected={exp.target_candidate_key}"
            )

        meta = json.loads(row["metadata_json"])
        expected_meta = {
            "candidate_resolution_detail": "raw_value_derived_then_manifest_and_target_checked",
            "family_key": exp.family_key,
            "manifest_id": manifest.manifest_id,
            "raw_value": exp.expected_raw_value,
        }
        if meta != expected_meta:
            raise _error(
                f"relationship metadata_json mismatch at {exp.source_pointer}: "
                f"actual={meta} expected={expected_meta}"
            )

        if exp.target_coordinate is None:
            if row["target_evidence_container_id"] is not None:
                raise _error(f"relationship unexpectedly has target container at {exp.source_pointer}")
        else:
            exp_file, exp_ptr = exp.target_coordinate
            if row["target_evidence_container_id"] is None:
                raise _error(f"relationship missing required target container at {exp.source_pointer}")
            if row["target_file_path"] != exp_file or row["target_record_pointer"] != exp_ptr:
                raise _error(
                    f"relationship target coordinate mismatch at {exp.source_pointer}: "
                    f"actual=({row['target_file_path']}, {row['target_record_pointer']}) "
                    f"expected=({exp_file}, {exp_ptr})"
                )
            if row["target_container_source_family"] != exp.target_family:
                raise _error(f"target container source_family mismatch at {exp.source_pointer}")
            if row["target_container_candidate_namespace"] != exp.target_family:
                raise _error(f"target container candidate_namespace mismatch at {exp.source_pointer}")
            if row["target_record_native_id"] != exp.target_record_native_id:
                raise _error(
                    f"target record native_id mismatch at {exp.source_pointer}: "
                    f"actual={row['target_record_native_id']} expected={exp.target_record_native_id}"
                )

        raw_source_obj = json.loads(row["source_raw_json"])
        suffix = exp.source_pointer[len(exp.source_record_pointer):]
        raw_val = _extract_json_pointer(raw_source_obj, suffix)
        if raw_val != exp.expected_raw_value:
            raise _error(
                f"raw value mismatch at {exp.source_pointer}: "
                f"actual={raw_val!r} expected={exp.expected_raw_value!r}"
            )

        if exp.target_family == "TextMap":
            textmap_hash = str(exp.target_candidate_key["textmap_hash"])
            expected_status = status_by_hash.get(textmap_hash)
            if expected_status is None:
                raise _error(f"unsealed TextMap hash {textmap_hash}")

            for locale in LOCALES:
                res = conn.execute(
                    """SELECT id, ingest_run_id, snapshot_id, textmap_hash, resolution_status,
                              raw_text, normalized_text, raw_checksum, normalized_checksum,
                              source_pointer, metadata_json
                         FROM static_text_resolution_occurrence
                        WHERE ingest_run_id = ?
                          AND static_relationship_occurrence_id = ?
                          AND locale = ?""",
                    (run_id, row["id"], locale),
                ).fetchone()

                if res is None:
                    raise _error(
                        f"missing text resolution for {exp.source_pointer} {locale} {textmap_hash}"
                    )

                if (
                    res["ingest_run_id"] != run_id
                    or res["snapshot_id"] != snapshot_id
                    or res["textmap_hash"] != textmap_hash
                    or res["resolution_status"] != expected_status
                    or res["source_pointer"] != exp.source_pointer
                ):
                    raise _error(
                        f"text resolution mismatch for {exp.source_pointer} {locale} {textmap_hash}: "
                        f"actual_status={res['resolution_status']} expected_status={expected_status}"
                    )

                res_meta = json.loads(res["metadata_json"])
                expected_res_meta = {
                    "both_conflict_auto_selected": False,
                    "family_key": exp.family_key,
                    "locator_only": True,
                    "owning_static_source": {
                        "record_pointer": exp.source_record_pointer,
                        "relative_path": exp.source_file_path,
                        "source_pointer": exp.source_pointer,
                    },
                }
                if res_meta != expected_res_meta:
                    raise _error(f"text resolution metadata mismatch for {exp.source_pointer} {locale} {textmap_hash}")

                cands = conn.execute(
                    """SELECT tc.id, tc.ingest_run_id, tc.snapshot_id, tc.shard_kind, tc.locale,
                              tc.resolution_status, tc.source_pointer, tc.raw_text, tc.normalized_text,
                              tc.raw_checksum, tc.normalized_checksum, tc.metadata_json,
                              sf.relative_path AS source_file_path,
                              sr.pointer AS source_record_pointer,
                              sr.native_id AS source_record_native_id,
                              sr.raw_json AS source_record_raw_json
                         FROM static_text_candidate_occurrence tc
                         JOIN source_file sf ON sf.id = tc.source_file_id
                         JOIN source_record sr ON sr.id = tc.source_record_id
                        WHERE tc.resolution_occurrence_id = ?
                        ORDER BY tc.shard_kind""",
                    (res["id"],),
                ).fetchall()

                for cand in cands:
                    if (
                        cand["ingest_run_id"] != run_id
                        or cand["snapshot_id"] != snapshot_id
                        or cand["locale"] != locale
                        or cand["resolution_status"] != expected_status
                        or cand["source_pointer"] != f"/{textmap_hash}"
                        or cand["source_record_pointer"] != f"/{textmap_hash}"
                        or cand["source_record_native_id"] != textmap_hash
                    ):
                        raise _error(f"candidate occurrence attribute mismatch for {locale}/{textmap_hash}")

                    cand_meta = json.loads(cand["metadata_json"])
                    expected_cand_meta = {
                        "exact_textmap_source": {
                            "record_pointer": f"/{textmap_hash}",
                            "relative_path": cand["source_file_path"],
                        },
                        "family_key": exp.family_key,
                        "owning_relationship_id": row["id"],
                        "textmap_hash_is_locator_not_identity": True,
                    }
                    if cand_meta != expected_cand_meta:
                        raise _error(f"candidate occurrence metadata mismatch for {locale}/{textmap_hash}")

                    # Verify exact text & checksum against source_record.raw_json
                    source_raw_text = json.loads(cand["source_record_raw_json"])
                    if not isinstance(source_raw_text, str):
                        raise _error(f"candidate source record is not raw string for {locale}/{textmap_hash}")
                    expected_norm_text = normalize_text(source_raw_text)
                    expected_raw_checksum = sha256_text(source_raw_text)
                    expected_norm_checksum = sha256_text(expected_norm_text)

                    if cand["raw_text"] != source_raw_text:
                        raise _error(f"candidate raw_text does not match source_record for {locale}/{textmap_hash}")
                    if cand["normalized_text"] != expected_norm_text:
                        raise _error(f"candidate normalized_text does not match source_record for {locale}/{textmap_hash}")
                    if cand["raw_checksum"] != expected_raw_checksum:
                        raise _error(f"candidate raw_checksum invalid for {locale}/{textmap_hash}")
                    if cand["normalized_checksum"] != expected_norm_checksum:
                        raise _error(f"candidate normalized_checksum invalid for {locale}/{textmap_hash}")

                if expected_status == "missing":
                    if len(cands) != 0:
                        raise _error(f"missing TextMap resolution has candidates for {locale}/{textmap_hash}")
                    if any(
                        res[k] is not None
                        for k in ("raw_text", "normalized_text", "raw_checksum", "normalized_checksum")
                    ):
                        raise _error(f"missing TextMap resolution has unexpected text/checksum for {locale}/{textmap_hash}")
                elif expected_status == "unique_main":
                    if len(cands) != 1:
                        raise _error(f"unique_main must have exactly 1 candidate for {locale}/{textmap_hash}")
                    cand = cands[0]
                    if (
                        cand["shard_kind"] != "main"
                        or cand["source_file_path"] != manifest.textmap["locales"][locale]["main"]
                    ):
                        raise _error(f"unique_main candidate source mismatch for {locale}/{textmap_hash}")
                    if (
                        res["raw_text"],
                        res["normalized_text"],
                        res["raw_checksum"],
                        res["normalized_checksum"],
                    ) != (
                        cand["raw_text"],
                        cand["normalized_text"],
                        cand["raw_checksum"],
                        cand["normalized_checksum"],
                    ):
                        raise _error(f"unique_main candidate/resolution text mismatch for {locale}/{textmap_hash}")
                elif expected_status == "unique_medium":
                    if len(cands) != 1:
                        raise _error(f"unique_medium must have exactly 1 candidate for {locale}/{textmap_hash}")
                    cand = cands[0]
                    if (
                        cand["shard_kind"] != "medium"
                        or cand["source_file_path"] != manifest.textmap["locales"][locale]["medium"]
                    ):
                        raise _error(f"unique_medium candidate source mismatch for {locale}/{textmap_hash}")
                    if (
                        res["raw_text"],
                        res["normalized_text"],
                        res["raw_checksum"],
                        res["normalized_checksum"],
                    ) != (
                        cand["raw_text"],
                        cand["normalized_text"],
                        cand["raw_checksum"],
                        cand["normalized_checksum"],
                    ):
                        raise _error(f"unique_medium candidate/resolution text mismatch for {locale}/{textmap_hash}")
                elif expected_status == "both_equal":
                    if len(cands) != 2:
                        raise _error(f"both_equal must have exactly 2 candidates for {locale}/{textmap_hash}")
                    shards = {c["shard_kind"] for c in cands}
                    if shards != {"main", "medium"}:
                        raise _error(f"both_equal candidate shards mismatch for {locale}/{textmap_hash}")
                    if cands[0]["raw_text"] != cands[1]["raw_text"]:
                        raise _error(f"both_equal candidates text mismatch for {locale}/{textmap_hash}")
                    if (
                        res["raw_text"],
                        res["normalized_text"],
                        res["raw_checksum"],
                        res["normalized_checksum"],
                    ) != (
                        cands[0]["raw_text"],
                        cands[0]["normalized_text"],
                        cands[0]["raw_checksum"],
                        cands[0]["normalized_checksum"],
                    ):
                        raise _error(f"both_equal candidate/resolution text mismatch for {locale}/{textmap_hash}")
                elif expected_status == "both_conflict":
                    if len(cands) != 2:
                        raise _error(f"both_conflict must have exactly 2 candidates for {locale}/{textmap_hash}")
                    shards = {c["shard_kind"] for c in cands}
                    if shards != {"main", "medium"}:
                        raise _error(f"both_conflict candidate shards mismatch for {locale}/{textmap_hash}")
                    if cands[0]["raw_text"] == cands[1]["raw_text"]:
                        raise _error(f"both_conflict candidates unexpectedly equal for {locale}/{textmap_hash}")
                    if any(
                        res[k] is not None
                        for k in ("raw_text", "normalized_text", "raw_checksum", "normalized_checksum")
                    ):
                        raise _error(f"both_conflict TextMap resolution has unexpected text/checksum for {locale}/{textmap_hash}")

    if len(verified_rel_ids) != 128:
        raise _error("failed to verify all 128 canary relationships")


def _verify_canaries(
    conn: sqlite3.Connection,
    manifest: StaticPhase4B2Manifest,
    run_id: str,
    snapshot_id: str,
) -> dict[str, Any]:
    book_rows = conn.execute(
        """SELECT sf.relative_path,sf.size_bytes,c.locale,
                  json_extract(c.metadata_json,'$.classification') classification
             FROM static_evidence_container c
             JOIN source_file sf ON sf.id=c.source_file_id
            WHERE c.ingest_run_id=? AND json_extract(c.metadata_json,'$.family_key')='book1257'
            ORDER BY c.locale""",
        (run_id,),
    ).fetchall()
    expected_book = [
        ("Readable/CHS/Book1257.txt", 5, "CHS", "placeholder_exact_test"),
        ("Readable/EN/Book1257_EN.txt", 0, "EN", "empty"),
        ("Readable/JP/Book1257_JP.txt", 0, "JP", "empty"),
    ]
    if [tuple(row) for row in book_rows] != expected_book:
        raise _error("Book1257 placeholder/empty file semantics mismatch")
    book_segments = int(
        conn.execute(
            """SELECT COUNT(*) FROM static_segment_occurrence s
                 JOIN static_evidence_container c ON c.id=s.evidence_container_id
                WHERE s.ingest_run_id=? AND json_extract(c.metadata_json,'$.family_key')='book1257'""",
            (run_id,),
        ).fetchone()[0]
    )
    if book_segments != 0:
        raise _error("Book1257 placeholder/empty files generated segment occurrences")

    weapon_sources = dict(
        conn.execute(
            """SELECT source_family,COUNT(*) FROM static_evidence_container
                WHERE ingest_run_id=? AND json_extract(metadata_json,'$.family_key')='weapon15516'
                GROUP BY source_family""",
            (run_id,),
        )
    )
    expected_weapon_sources = {
        "WeaponExcelConfigData": 1, "WeaponCodexExcelConfigData": 1,
        "DocumentExcelConfigData": 1, "LocalizationExcelConfigData": 1,
        "Readable": 3,
    }
    if weapon_sources != expected_weapon_sources:
        raise _error("Weapon15516 raw evidence container chain mismatch")

    piece_rows = conn.execute(
        """SELECT sr.native_id,sr.pointer FROM static_evidence_container c
             JOIN source_record sr ON sr.id=c.source_record_id
            WHERE c.ingest_run_id=? AND c.source_family='ReliquaryExcelConfigData'""",
        (run_id,),
    ).fetchall()
    if len(piece_rows) != 15 or len({row["native_id"] for row in piece_rows}) != 15:
        raise _error("Reliquary15048 fifteen raw piece variants were merged or lost")
    contains_count = int(
        conn.execute(
            """SELECT COUNT(*) FROM static_relationship_occurrence
                WHERE ingest_run_id=? AND source_pointer LIKE '%/containsList/%'""",
            (run_id,),
        ).fetchone()[0]
    )
    codex_slot_count = int(
        conn.execute(
            """SELECT COUNT(*) FROM static_relationship_occurrence
                WHERE ingest_run_id=? AND source_family='ReliquaryCodexExcelConfigData'
                  AND relation_kind='reliquary_codex_piece_candidate'""",
            (run_id,),
        ).fetchone()[0]
    )
    if (contains_count, codex_slot_count) != (5, 10):
        raise _error("Reliquary15048 source field occurrences are incomplete")

    material_containers = int(
        conn.execute(
            """SELECT COUNT(*) FROM static_evidence_container
                WHERE ingest_run_id=? AND json_extract(metadata_json,'$.family_key')='materials'""",
            (run_id,),
        ).fetchone()[0]
    )
    replacement_fields = conn.execute(
        """SELECT source_pointer FROM static_relationship_occurrence
            WHERE ingest_run_id=? AND source_family='MaterialCodexExcelConfigData'
              AND target_family='TextMap' ORDER BY source_pointer""",
        (run_id,),
    ).fetchall()
    if material_containers != 3 or len(replacement_fields) != 6 or len({row[0] for row in replacement_fields}) != 6:
        raise _error("Material base/codex replacement occurrence boundary mismatch")

    return {
        "book1257": {
            "containers": len(book_rows),
            "segments": book_segments,
            "locale_statuses": {row["locale"]: row["classification"] for row in book_rows},
        },
        "weapon15516": {"containers": sum(weapon_sources.values()), "full_chain_verified": True},
        "reliquary15048": {
            "raw_piece_rows": len(piece_rows),
            "contains_relationships": contains_count,
            "codex_slot_relationships": codex_slot_count,
        },
        "materials": {
            "containers": material_containers,
            "replacement_text_fields": len(replacement_fields),
        },
    }


def _verify_text_provenance(conn: sqlite3.Connection, run_id: str) -> tuple[dict[str, int], dict[str, int]]:
    status_counts = Counter(
        {
            row["resolution_status"]: int(row["count"])
            for row in conn.execute(
                """SELECT resolution_status,COUNT(*) count
                     FROM static_text_resolution_occurrence
                    WHERE ingest_run_id=? GROUP BY resolution_status""",
                (run_id,),
            )
        }
    )
    resolution_owner_join_count = int(
        conn.execute(
            """SELECT COUNT(*) FROM static_text_resolution_occurrence tr
                 JOIN static_relationship_occurrence r
                   ON r.id=tr.static_relationship_occurrence_id
                  AND r.ingest_run_id=tr.ingest_run_id
                  AND r.snapshot_id=tr.snapshot_id
                  AND r.source_container_id=tr.source_container_id
                  AND r.source_record_id=tr.source_record_id
                  AND r.source_pointer=tr.source_pointer
                WHERE tr.ingest_run_id=?""",
            (run_id,),
        ).fetchone()[0]
    )
    candidate_source_join_count = int(
        conn.execute(
            """SELECT COUNT(*) FROM static_text_candidate_occurrence tc
                 JOIN source_file sf ON sf.id=tc.source_file_id AND sf.snapshot_id=tc.snapshot_id
                 JOIN source_record sr ON sr.id=tc.source_record_id
                                      AND sr.source_file_id=tc.source_file_id
                                      AND sr.pointer=tc.source_pointer
                WHERE tc.ingest_run_id=?""",
            (run_id,),
        ).fetchone()[0]
    )
    textmap_target_container_count = int(
        conn.execute(
            """SELECT COUNT(*) FROM static_relationship_occurrence
                WHERE ingest_run_id=? AND target_family='TextMap'
                  AND target_evidence_container_id IS NOT NULL""",
            (run_id,),
        ).fetchone()[0]
    )
    missing_candidate_count = int(
        conn.execute(
            """SELECT COUNT(*) FROM static_text_candidate_occurrence tc
                 JOIN static_text_resolution_occurrence tr ON tr.id=tc.resolution_occurrence_id
                WHERE tc.ingest_run_id=? AND tr.resolution_status='missing'""",
            (run_id,),
        ).fetchone()[0]
    )
    return dict(status_counts), {
        "resolution_owner_join_count": resolution_owner_join_count,
        "candidate_source_join_count": candidate_source_join_count,
        "textmap_target_container_count": textmap_target_container_count,
        "missing_candidate_count": missing_candidate_count,
    }


def _verify_schema(conn: sqlite3.Connection) -> int:
    versions = [
        int(row[0])
        for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")
    ]
    if versions not in (list(range(1, 10)), list(range(1, 11))):
        raise _error(
            f"Phase 4B2 schema migration set is not exactly known 1..9 or 1..10: {versions}"
        )
    if versions[-1] == 10:
        try:
            verify_character_phase4c2_schema_signature(conn)
        except CharacterPhase4C2SchemaError as exc:
            raise _error(str(exc)) from exc
    return versions[-1]


def _audit_database(
    conn: sqlite3.Connection,
    manifest: StaticPhase4B2Manifest,
    build: dict[str, Any],
    validated_source_records: dict[str, SourceRecordContract] | None = None,
) -> dict[str, Any]:
    schema_version = _verify_schema(conn)
    schema_coverage = verify_phase4b2_schema_coverage(conn)
    run = _verify_run_binding(conn, manifest, build)
    run_id = run["ingest_run_id"]
    snapshot_id = build["snapshot_id"]
    expected = _expected_counts(manifest)
    counts = {"files": expected["files"], **_run_counts(conn, run_id)}
    if counts != expected:
        raise _error(f"Phase 4B2 run-scoped count mismatch: actual={counts} expected={expected}")
    family_counts = _family_counts(conn, run_id)
    if family_counts != manifest.expected["families"]:
        raise _error("Phase 4B2 run-scoped family count mismatch")
    status_counts, text_provenance = _verify_text_provenance(conn, run_id)
    expected_statuses = {
        key: int(value) for key, value in manifest.expected["resolution_statuses"].items()
    }
    actual_statuses = {key: int(status_counts.get(key, 0)) for key in expected_statuses}
    if actual_statuses != expected_statuses:
        raise _error("Phase 4B2 TextMap resolution status mismatch")
    if text_provenance != {
        "resolution_owner_join_count": 144,
        "candidate_source_join_count": 129,
        "textmap_target_container_count": 0,
        "missing_candidate_count": 0,
    }:
        raise _error("Phase 4B2 TextMap dual provenance mismatch")
    foreign_keys = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()]
    if foreign_keys:
        raise _error(f"Phase 4B2 foreign key check failed: {foreign_keys}")
    source_records, containers, segments = verify_phase4b2_sources_containers_segments(
        conn, manifest, run_id, snapshot_id
    )
    expected_relationships: tuple[_ExpectedRelationship, ...] = tuple(
        [
            *_expected_weapon_relationships(manifest),
            *_expected_reliquary_relationships(manifest),
            *_expected_materials_relationships(manifest),
        ]
    )
    _verify_exact_relationships_and_textmap(conn, manifest, run_id, snapshot_id)
    (
        relationships,
        text_resolutions,
        text_candidates,
        text_source_records,
    ) = verify_phase4b2_relationship_text_occurrence_sets(
        conn,
        manifest,
        run_id,
        snapshot_id,
        expected_relationships,
        containers,
    )
    all_source_records_by_id = {
        item.source_record_id: item for item in (*source_records, *text_source_records)
    }
    if len(all_source_records_by_id) != len(source_records) + len(text_source_records):
        for item in (*source_records, *text_source_records):
            if all_source_records_by_id[item.source_record_id] != item:
                raise _error(
                    f"Phase 4B2 shared source_record contract mismatch: {item.source_record_id}"
                )
    all_source_records = tuple(all_source_records_by_id.values())
    verify_phase4b2_source_reference_coverage(conn, run_id, all_source_records)
    if validated_source_records is not None:
        for item in all_source_records:
            contract = SourceRecordContract(
                id=item.source_record_id,
                source_file_id=item.source_file_id,
                pointer=item.pointer,
                record_kind=item.record_kind,
                native_id=item.native_id,
                raw_sha256=item.raw_sha256,
                raw_json=item.raw_json,
            )
            prior = validated_source_records.setdefault(contract.id, contract)
            if prior != contract:
                raise _error(
                    f"Phase 4B2 source_record contract conflict: {contract.id}"
                )
    occurrence_bundle = Phase4B2ExpectedOccurrenceBundle(
        source_records=all_source_records,
        containers=containers,
        segments=segments,
        relationships=relationships,
        text_resolutions=text_resolutions,
        text_candidates=text_candidates,
    )
    canaries = _verify_canaries(conn, manifest, run_id, snapshot_id)
    return {
        "schema_version": schema_version,
        "run": run,
        "counts": counts,
        "family_counts": family_counts,
        "resolution_statuses": actual_statuses,
        "text_provenance": text_provenance,
        "canaries": canaries,
        "occurrence_coverage": {
            "source_records": len(occurrence_bundle.source_records),
            "containers": len(occurrence_bundle.containers),
            "segments": len(occurrence_bundle.segments),
            "relationships": len(occurrence_bundle.relationships),
            "text_resolutions": len(occurrence_bundle.text_resolutions),
            "text_candidates": len(occurrence_bundle.text_candidates),
        },
        "schema_coverage": schema_coverage,
        "foreign_key_check": [],
    }


def verify_phase4b2_database_contract(
    conn: sqlite3.Connection,
    manifest: StaticPhase4B2Manifest,
    *,
    validated_source_records: dict[str, SourceRecordContract] | None = None,
) -> dict[str, Any]:
    """Verify B2 final-state rows without a build report or new connection."""

    result = _audit_database(
        conn,
        manifest,
        expected_phase4b2_identity(manifest),
        validated_source_records,
    )
    identity = expected_phase4b2_identity(manifest)
    return {
        "protocol": "wave1-stage-final-state-contract-v1",
        "contract_id": (
            "wave1-stage-final-state-v1:phase4b2:" + manifest.manifest_id
        ),
        "status": "pass",
        "run_id": identity["ingest_run_id"],
        "parser_build_id": identity["parser_build_id"],
        "manifest_sha256": manifest.raw_sha256,
        "exact_counts": result["counts"],
        "required_checks": [
            "schema", "source_binding", "run_binding", "exact_counts",
            "full_rows", "text_provenance", "logical_boundary", "foreign_keys",
        ],
        **result,
    }


def verify_static_phase4b2_evidence_db(
    db_path: str | Path,
    build_report_path: str | Path,
    phase4b2_manifest_path: str | Path,
) -> dict[str, Any]:
    """Verify the exact B2 release contract using SQLite mode=ro only."""
    database_path = Path(db_path).resolve()
    if not database_path.is_file():
        raise _error(f"Phase 4B2 database file not found: {database_path}")
    before = _fingerprint(database_path)
    try:
        manifest = load_static_phase4b2_manifest(Path(phase4b2_manifest_path).resolve())
        build = _load_build_report(Path(build_report_path).resolve())
        _verify_build_binding(build, manifest, before)
        with open_readonly_database(database_path) as readonly:
            if int(readonly.conn.execute("PRAGMA query_only").fetchone()[0]) != 1:
                raise _error("readonly Phase 4B2 database did not enable PRAGMA query_only")
            if int(readonly.conn.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
                raise _error("readonly Phase 4B2 database did not enable PRAGMA foreign_keys")
            details = _audit_database(readonly.conn, manifest, build)
    except StaticPhase4B2VerificationError:
        _assert_unchanged(database_path, before)
        raise
    except Exception as exc:
        _assert_unchanged(database_path, before)
        raise _error(f"cannot verify readonly Phase 4B2 database: {exc}") from exc
    after = _fingerprint(database_path)
    if before != after:
        raise _error(
            "database fingerprint changed during Phase 4B2 verification: "
            f"before={before}, after={after}"
        )
    return {
        "status": "pass",
        "protocol": "static-phase4b2-evidence-db-v1",
        "schema_version": details["schema_version"],
        "database": {
            "path": str(database_path),
            "fingerprint_before": before,
            "fingerprint_after": after,
            "unchanged": True,
        },
        "database_fingerprint_before": before,
        "database_fingerprint_after": after,
        "database_fingerprint_unchanged": True,
        "manifest_binding": {
            "manifest_id": manifest.manifest_id,
            "raw_sha256": manifest.raw_sha256,
            "selected_paths": [item.relative_path for item in manifest.files],
            **manifest.source,
        },
        "run_binding": details["run"],
        "counts": details["counts"],
        "family_counts": details["family_counts"],
        "resolution_statuses": details["resolution_statuses"],
        "text_provenance": details["text_provenance"],
        "canaries": details["canaries"],
        "occurrence_coverage": details["occurrence_coverage"],
        "schema_coverage": details["schema_coverage"],
        "foreign_key_check": details["foreign_key_check"],
        "mcp_readonly_compatible": True,
        "blockers": [],
    }


__all__ = [
    "StaticPhase4B2VerificationError",
    "verify_phase4b2_database_contract",
    "verify_static_phase4b2_evidence_db",
]
