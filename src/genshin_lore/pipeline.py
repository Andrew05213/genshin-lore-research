from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import Database
from .canary import evaluate_canaries
from .discovery import TalkDiscoveryIndex, _talk_native_namespace, build_talk_index
from .errors import SchemaCanaryError, SourceMissingError
from .mapping import MAPPING_VERSION
from .parsers import (
    deob_dialog_node,
    deob_quest,
    deob_quest_generic,
    deob_quest_talk_reference,
    deob_talk,
    deob_talk_generic,
    hash_refs,
    parse_srt,
    readable_content_type,
    readable_stem_from_path,
    scan_textmaps,
    selected_text_lookup,
    split_readable,
    subtitle_base,
    subtitle_talk_id,
)
from .source import SourceBundle, load_manifest, snapshot_id
from .util import (
    as_hash,
    canonical_json,
    dialog_evidence_id,
    digest_id,
    logical_talk_id,
    normalize_text,
    sha256_bytes,
    stable_id,
    talk_evidence_id,
)


PARSER_VERSION = "phase1-vertical-slice-0.1"
PHASE2_PARSER_VERSION = "phase2-talk-discovery-0.2"
LANGUAGES = ("CHS", "EN", "JP")
READABLE_STEMS = ("Book1", "Weapon15516", "Relic15048_1", "Book1257")
QUEST_PATH = "BinOutput/Quest/63ccb113.json"
TALK_PATH = "BinOutput/Talk/Quest/79661eb8.json"
TEXTMAP_GOLD_QUERY = "燃珀"
SUBTITLE_STEM = "Cs_ZD_AQ70081401_YD_Boy"


@dataclass
class Registrar:
    db: Database
    bundle: SourceBundle
    snapshot_id: str
    files: dict[str, str]

    def __init__(self, db: Database, bundle: SourceBundle, snapshot_id_value: str):
        self.db = db
        self.bundle = bundle
        self.snapshot_id = snapshot_id_value
        self.files = {}

    def source_file(self, relative: str, located: Any | None = None, raw: bytes | None = None) -> str:
        relative = relative.replace("\\", "/")
        if relative in self.files:
            return self.files[relative]
        if located is None or raw is None:
            located, raw = self.bundle.read_bytes(relative)
        info = {
            "relative_path": relative,
            "sha256": sha256_bytes(raw),
            "size_bytes": len(raw),
            "root_path": str(located.root),
            "format": Path(relative).suffix.lstrip(".") or "unknown",
        }
        file_id = self.db.ensure_source_file(self.snapshot_id, info)
        self.files[relative] = file_id
        return file_id

    def record(
        self,
        relative: str,
        source_file_id: str,
        pointer: str,
        record_kind: str,
        native_id: str | int | None,
        raw_value: Any,
        *,
        persist_raw_json: bool = True,
    ) -> str:
        return self.db.ensure_source_record(
            source_file_id,
            pointer,
            record_kind,
            native_id,
            raw_value,
            raw_json=(canonical_json(raw_value) if persist_raw_json and not isinstance(raw_value, str) else None),
        )

    def issue(
        self,
        severity: str,
        code: str,
        message: str,
        source_file_id: str | None = None,
        source_record_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> int:
        return self.db.add_issue(self.snapshot_id, severity, code, message, source_file_id, source_record_id, details)


def _load_json(registrar: Registrar, relative: str, kind: str) -> tuple[Any, str, str]:
    located, value = registrar.bundle.read_json(relative)
    raw = located.path.read_bytes()
    file_id = registrar.source_file(relative, located, raw)
    record_id = registrar.record(relative, file_id, "/", kind, None, value)
    return value, file_id, record_id


def _readable_paths(stem: str, language: str) -> list[str]:
    if language == "CHS":
        return [f"Readable/CHS/{stem}.txt"]
    return [f"Readable/{language}/{stem}_{language}.txt", f"Readable/{language}/{stem}.txt"]


def _find_row_refs(rows: list[dict[str, Any]], ids: set[int]) -> list[tuple[int, dict[str, Any]]]:
    found = []
    for index, row in enumerate(rows):
        values: list[int] = []
        for value in row.values():
            if isinstance(value, int):
                values.append(value)
            elif isinstance(value, list):
                values.extend(item for item in value if isinstance(item, int))
        if ids.intersection(values):
            found.append((index, row))
    return found


def _load_readable_metadata(registrar: Registrar) -> dict[str, dict[str, Any]]:
    """Resolve the four selected Readable stems through actual Excel chains."""
    localization, loc_file_id, _loc_file_record = _load_json(registrar, "ExcelBinOutput/LocalizationExcelConfigData.json", "localization_table")
    documents, doc_file_id, _doc_file_record = _load_json(registrar, "ExcelBinOutput/DocumentExcelConfigData.json", "document_table")
    if not isinstance(localization, list) or not isinstance(documents, list):
        raise SchemaCanaryError("Readable metadata tables are not arrays")
    metadata: dict[str, dict[str, Any]] = {}
    for stem in READABLE_STEMS:
        loc_matches: list[tuple[int, dict[str, Any]]] = []
        for index, row in enumerate(localization):
            paths = [value for value in row.values() if isinstance(value, str)]
            if any(Path(value).name == stem for value in paths):
                loc_matches.append((index, row))
        if not loc_matches:
            registrar.issue("warning", "readable_metadata_missing", f"No Localization row resolves {stem}", details={"stem": stem})
            continue
        loc_index, loc_row = loc_matches[0]
        loc_id = int(loc_row["id"])
        loc_record = registrar.record(
            "ExcelBinOutput/LocalizationExcelConfigData.json",
            loc_file_id,
            f"/rows/{loc_index}",
            "localization_row",
            loc_id,
            loc_row,
        )
        doc_matches = _find_row_refs(documents, {loc_id})
        if not doc_matches:
            registrar.issue("warning", "readable_document_missing", f"No Document row resolves Localization {loc_id} for {stem}", source_record_id=loc_record, details={"stem": stem, "localization_id": loc_id})
            metadata[stem] = {"loc_id": loc_id, "loc_record_id": loc_record, "doc": None}
            continue
        doc_index, doc_row = doc_matches[0]
        doc_id = int(doc_row["id"])
        doc_record = registrar.record(
            "ExcelBinOutput/DocumentExcelConfigData.json",
            doc_file_id,
            f"/rows/{doc_index}",
            "document_row",
            doc_id,
            doc_row,
        )
        metadata[stem] = {
            "loc_id": loc_id,
            "loc_record_id": loc_record,
            "doc_id": doc_id,
            "doc_record_id": doc_record,
            "title_hash": as_hash(doc_row.get("titleTextMapHash")),
            "document_row": doc_row,
        }
    return metadata


def _load_talk_excel(registrar: Registrar, quest_id: int = 7008) -> dict[int, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    source_rows: dict[int, tuple[str, str]] = {}
    for relative in ("ExcelBinOutput/TalkExcelConfigData_0.json", "ExcelBinOutput/TalkExcelConfigData_1.json"):
        if registrar.bundle.try_locate(relative) is None:
            registrar.issue("warning", "talk_excel_missing", f"Missing optional cross-check table {relative}")
            continue
        loaded, file_id, _ = _load_json(registrar, relative, "talk_excel_table")
        if not isinstance(loaded, list):
            raise SchemaCanaryError(f"{relative} is not an array")
        for index, row in enumerate(loaded):
            if row.get("questId") == quest_id:
                tid = row.get("id")
                if isinstance(tid, int):
                    source_rows[tid] = (file_id, registrar.record(relative, file_id, f"/rows/{index}", "talk_excel_row", tid, row))
                    rows.append(row)
    by_id = {int(row["id"]): row for row in rows if isinstance(row.get("id"), int)}
    expected = {700801, 700802, 700803, 700805, 700806, 700807, 700809, 700810, 700811, 700812, 700814, 700821, 700822, 700823, 700830}
    if quest_id == 7008 and by_id and set(by_id) != expected:
        raise SchemaCanaryError(f"TalkExcel 7008 IDs differ from canary: {sorted(by_id)}")
    for tid, row in by_id.items():
        row["_source_record_id"] = source_rows[tid][1]
    return by_id


def _add_text_fact(
    registrar: Registrar,
    lookup: dict[tuple[str, str], Any],
    subject_type: str,
    subject_id: str,
    key: str,
    text_hash: str | None,
    language: str,
    fallback_record_id: str,
) -> None:
    if not text_hash:
        return
    entry = lookup.get((language, text_hash))
    if entry is None:
        registrar.issue("warning", "missing_textmap_reference", f"No selected TextMap value for hash {text_hash} ({language})", source_record_id=fallback_record_id, details={"hash": text_hash, "language": language, "subject_id": subject_id})
        return
    registrar.db.add_fact(
        subject_type,
        subject_id,
        key,
        {"text_map_hash": text_hash, "text": entry.text, "resolution": "current_snapshot"},
        "original",
        entry.source_record_id,
        language=language,
    )


UNKNOWN_TALK_NAMESPACE = "__unknown__"
AMBIGUOUS_TALK_NAMESPACE = "__ambiguous__"


def _talk_identity_from_detail(detail: dict[str, Any] | None, talk_id: int) -> tuple[str, str]:
    """Return an explicit namespace/status pair for a Quest Talk reference."""
    if detail and detail.get("status") == "resolved":
        namespace = str(detail.get("native_namespace") or UNKNOWN_TALK_NAMESPACE)
        return namespace, "unique"
    if detail and detail.get("status") == "ambiguous":
        return AMBIGUOUS_TALK_NAMESPACE, "ambiguous"
    return UNKNOWN_TALK_NAMESPACE, "unresolved"


def _ensure_talk_evidence(
    registrar: Registrar,
    detail: dict[str, Any],
    *,
    status: str = "active",
    raw_value: Any | None = None,
    normalized_value: dict[str, Any] | None = None,
) -> str | None:
    """Register a concrete Talk root and return its evidence identity."""
    source_record_id = detail.get("file_record_id")
    source_file_id = detail.get("source_file_id")
    if not source_record_id or not source_file_id:
        return None
    native_id = int(detail.get("talk_id") or detail.get("native_id") or 0)
    namespace = str(detail.get("native_namespace") or UNKNOWN_TALK_NAMESPACE)
    evidence_id = talk_evidence_id(registrar.snapshot_id, str(source_record_id))
    registrar.db.ensure_talk_evidence(
        evidence_id,
        registrar.snapshot_id,
        str(source_file_id),
        str(source_record_id),
        str(detail.get("source_pointer") or "/"),
        namespace,
        native_id,
        str(detail.get("raw_sha256") or ""),
        str(detail.get("normalized_checksum") or ""),
        status,
        {
            "relative_path": detail.get("relative_path"),
            "native_namespace_source": detail.get("native_namespace_source"),
            "raw_object": "root",
        },
    )
    return evidence_id


def _logical_document_id(namespace: str, talk_id: int) -> str:
    return logical_talk_id(namespace, talk_id)


def _insert_quest(
    registrar: Registrar,
    raw_quest: dict[str, Any],
    quest: dict[str, Any],
    quest_file_record_id: str,
    lookup: dict[tuple[str, str], Any],
    talk_details: dict[int, dict[str, Any]],
    talk_excel: dict[int, dict[str, Any]],
    *,
    quest_relative: str = QUEST_PATH,
    quest_id_value: int = 7008,
    persist_raw_json: bool = True,
) -> dict[str, Any]:
    db = registrar.db
    snapshot = registrar.snapshot_id
    quest_id = stable_id("quest", quest_id_value)
    db.ensure_document(quest_id, "quest", "quest", quest_id_value, "active")
    db.ensure_document_version(
        quest_id,
        snapshot,
        quest_file_record_id,
        raw_quest,
        quest,
        {"chapter_id": quest.get("chapterId"), "mapping_version": MAPPING_VERSION},
    )
    title_hash = as_hash(quest.get("titleTextMapHash"))
    desc_hash = as_hash(quest.get("descTextMapHash"))
    for language in LANGUAGES:
        _add_text_fact(registrar, lookup, "document", quest_id, "title", title_hash, language, quest_file_record_id)
        _add_text_fact(registrar, lookup, "document", quest_id, "description", desc_hash, language, quest_file_record_id)
    db.add_fact("document", quest_id, "chapter_id", quest.get("chapterId"), "original", quest_file_record_id)

    subquest_ids: list[int] = []
    subquest_talk_conditions: list[tuple[str, int, str]] = []
    for index, raw_sub in enumerate(raw_quest.get("EBNBLBEIFFJ", [])):
        sub = quest["subQuests"][index]
        sub_id = int(sub["subId"])
        subquest_ids.append(sub_id)
        sub_document = stable_id("quest_stage", sub_id)
        sub_record = registrar.record(
            quest_relative,
            registrar.files[quest_relative],
            f"/subQuests/{index}",
            "quest_subrecord",
            sub_id,
            raw_sub,
            persist_raw_json=persist_raw_json,
        )
        db.ensure_document(sub_document, "quest_stage", "quest_stage", sub_id, "active")
        db.ensure_document_version(sub_document, snapshot, sub_record, raw_sub, sub, {"order": sub.get("order"), "quest_id": 7008})
        db.add_relation(sub_document, quest_id, "stage_of", "original", sub_record)
        db.add_fact("document", sub_document, "order", sub.get("order"), "original", sub_record)
        _add_text_fact(registrar, lookup, "document", sub_document, "description", as_hash(sub.get("descTextMapHash")), "CHS", sub_record)
        for language in ("EN", "JP"):
            _add_text_fact(registrar, lookup, "document", sub_document, "description", as_hash(sub.get("descTextMapHash")), language, sub_record)
        db.add_fact("document", sub_document, "finish_conditions", sub.get("finishCond", []), "original", sub_record)
        for condition in sub.get("finishCond", []):
            if not isinstance(condition, dict) or condition.get("ALBFHGKNMLK") != "QUEST_CONTENT_COMPLETE_TALK":
                continue
            params = condition.get("param")
            if isinstance(params, list) and params and isinstance(params[0], int) and not isinstance(params[0], bool):
                subquest_talk_conditions.append((sub_document, int(params[0]), sub_record))

    talk_ids: list[int] = []
    talk_records: dict[int, str] = {}
    talk_detail_summaries: dict[int, dict[str, Any]] = {}
    talk_document_ids: dict[int, str] = {}
    for index, raw_ref in enumerate(raw_quest.get("OJACLOOEAMG", [])):
        ref = deob_quest_talk_reference(raw_ref)
        talk_id = int(ref["talkId"])
        talk_ids.append(talk_id)
        ref_record = registrar.record(
            quest_relative,
            registrar.files[quest_relative],
            f"/talks/{index}",
            "quest_talk_reference",
            talk_id,
            raw_ref,
            persist_raw_json=persist_raw_json,
        )
        talk_records[talk_id] = ref_record
        detail = talk_details.get(talk_id)
        has_detail = bool(detail and detail.get("status") == "resolved" and detail.get("raw") is not None and detail.get("normalized") is not None)
        namespace, logical_status = _talk_identity_from_detail(detail, talk_id)
        talk_document = _logical_document_id(namespace, talk_id)
        talk_document_ids[talk_id] = talk_document
        existing_logical = db.conn.execute("SELECT status FROM logical_talk WHERE id=?", (talk_document,)).fetchone()
        if existing_logical is not None and existing_logical[0] == "multiple_evidence":
            logical_status = "multiple_evidence"
        evidence_id: str | None = None
        if has_detail and detail is not None:
            evidence_id = _ensure_talk_evidence(registrar, detail)
        db.ensure_logical_talk(
            talk_document,
            namespace,
            talk_id,
            logical_status,
            {"source": "quest_talk_reference", "quest_id": quest_id_value},
        )
        db.ensure_document(talk_document, "talk", namespace, talk_id, "active" if has_detail else "unresolved")
        discovery_status = detail.get("status") if detail else "not_attempted"
        metadata = {
            "quest_id": ref.get("questId"),
            "initial_dialog": ref.get("initDialog"),
            "perform_cfg": ref.get("performCfg"),
            "npc_ids": ref.get("npcId", []),
            "detail_source": "raw_talk_file" if has_detail else "quest_reference_only",
            "discovery_status": discovery_status,
            "discovery_method": detail.get("discovery_method") if detail else None,
            "native_id_verified": detail.get("native_id_verified") if detail else False,
            "raw_path": detail.get("relative_path") if detail else None,
            "native_namespace": namespace,
            "logical_status": logical_status,
            "talk_evidence_id": evidence_id,
        }
        if evidence_id and detail is not None:
            db.link_logical_talk_evidence(
                talk_document,
                evidence_id,
                "resolved",
                "original",
                detail.get("file_record_id"),
                method=str(detail.get("discovery_method") or "native_id_exact_scan"),
                confidence=1.0,
            )
            if db.conn.execute(
                "SELECT 1 FROM document_version WHERE document_id=? AND snapshot_id=?",
                (talk_document, snapshot),
            ).fetchone() is None:
                db.ensure_document_version(
                    talk_document,
                    snapshot,
                    str(detail["file_record_id"]),
                    detail["raw"],
                    detail["normalized"],
                    {**metadata, "detail_source": detail.get("relative_path"), "dialog_count": len(detail["normalized"].get("dialogList", []))},
                    "active",
                )
        elif detail and detail.get("status") == "ambiguous":
            for ambiguous_evidence_id in detail.get("evidence_ids", []):
                db.link_logical_talk_evidence(
                    talk_document,
                    str(ambiguous_evidence_id),
                    "ambiguous",
                    "original",
                    ref_record,
                    method="native_id_exact_scan_multiple_candidates",
                    confidence=None,
                )
        elif db.conn.execute(
            "SELECT 1 FROM document_version WHERE document_id=? AND snapshot_id=?",
            (talk_document, snapshot),
        ).fetchone() is None:
            db.ensure_document_version(talk_document, snapshot, ref_record, raw_ref, ref, metadata, "unresolved")
        db.add_relation(
            talk_document,
            quest_id,
            "talk_of",
            "original",
            ref_record,
            method=f"quest_reference:{ref_record}",
        )
        db.add_fact("document", talk_document, "initial_dialog", ref.get("initDialog"), "original", ref_record)
        db.add_fact("document", talk_document, "perform_cfg", ref.get("performCfg"), "original", ref_record)
        db.add_fact("document", talk_document, "npc_ids", ref.get("npcId", []), "original", ref_record)
        excel = talk_excel.get(talk_id)
        if excel:
            if excel.get("initDialog") != ref.get("initDialog") or excel.get("performCfg") != ref.get("performCfg") or excel.get("npcId") != ref.get("npcId"):
                raise SchemaCanaryError(f"TalkExcel disagrees with Quest raw reference for talk {talk_id}")
        if not has_detail:
            issue_code = {
                "genuinely_absent": "talk_raw_genuinely_absent",
                "ambiguous": "talk_raw_ambiguous",
                "unresolved": "talk_raw_unresolved",
            }.get(discovery_status, "missing_selected_raw_file")
            registrar.issue(
                "warning",
                issue_code,
                f"Talk {talk_id} has a Quest reference but no active raw Talk detail",
                source_record_id=ref_record,
                details={"talk_id": talk_id, "discovery_status": discovery_status, "reason": detail.get("reason") if detail else None},
            )
        if has_detail:
            talk_file_record_id = str(detail["file_record_id"])
            raw_detail = detail["raw"]
            normalized_detail = detail["normalized"]
            detail_path = str(detail["relative_path"])
            db.ensure_document_version(
                talk_document,
                snapshot,
                talk_file_record_id,
                raw_detail,
                normalized_detail,
                {**metadata, "detail_source": detail_path, "dialog_count": len(normalized_detail.get("dialogList", []))},
            )
            talk_detail_summaries[talk_id] = {
                "status": detail.get("status"),
                "discovery_method": detail.get("discovery_method"),
                "native_id_verified": detail.get("native_id_verified", False),
                **_insert_talk(
                    registrar,
                    normalized_detail,
                    raw_detail,
                    talk_file_record_id,
                    detail_path,
                    lookup,
                    talk_excel.get(talk_id, {}),
                    talk_document_id=talk_document,
                    talk_evidence_id=evidence_id,
                    talk_namespace=namespace,
                    persist_node_raw_json=persist_raw_json,
                ),
            }
        elif detail:
            talk_detail_summaries[talk_id] = {
                "status": detail.get("status"),
                "reason": detail.get("reason"),
                "candidate_count": len(detail.get("candidates", [])),
            }
    if set(talk_ids) != set(talk_excel) and talk_excel:
        raise SchemaCanaryError("Quest 7008 talk references and TalkExcel selected IDs disagree")

    # This is a native Quest finish-condition edge, not a guessed stage/talk
    # ordering.  It is added only after all referenced Talk documents exist so
    # SQLite foreign keys remain enabled.  A condition pointing outside the
    # Quest's explicit Talk reference list is retained as an audit issue rather
    # than creating an unverified document.
    for sub_document, condition_talk_id, sub_record in subquest_talk_conditions:
        if condition_talk_id not in talk_ids:
            registrar.issue(
                "warning",
                "subquest_talk_condition_outside_quest_refs",
                f"Subquest condition points to Talk {condition_talk_id}, which is not a Quest talk reference",
                source_record_id=sub_record,
                details={"subquest_id": sub_document, "talk_id": condition_talk_id},
            )
            continue
        db.add_relation(
            sub_document,
            talk_document_ids.get(condition_talk_id, _logical_document_id(UNKNOWN_TALK_NAMESPACE, condition_talk_id)),
            "subquest_talk_condition",
            "original",
            sub_record,
            method=f"finish_condition:{sub_record}",
        )

    return {
        "quest_id": quest_id_value,
        "subquest_ids": subquest_ids,
        "talk_ids": talk_ids,
        "talk_records": talk_records,
        "talk_details": talk_detail_summaries,
        "subquest_talk_condition_count": sum(1 for sub_document, talk_id, _ in subquest_talk_conditions if talk_id in talk_ids),
    }


def _insert_talk(
    registrar: Registrar,
    talk: dict[str, Any],
    raw_talk: dict[str, Any],
    talk_file_record_id: str,
    talk_path: str,
    lookup: dict[tuple[str, str], Any],
    excel_row: dict[str, Any],
    *,
    talk_document_id: str | None = None,
    talk_evidence_id: str | None = None,
    talk_namespace: str | None = None,
    persist_node_raw_json: bool = True,
) -> dict[str, Any]:
    db = registrar.db
    snapshot = registrar.snapshot_id
    talk_id = int(talk["talkId"])
    namespace = str(talk_namespace or UNKNOWN_TALK_NAMESPACE)
    talk_document = talk_document_id or _logical_document_id(namespace, talk_id)
    if talk_evidence_id is None:
        raise SchemaCanaryError(f"Talk {talk_id} has no concrete raw evidence identity")
    db.ensure_document_version(talk_document, snapshot, talk_file_record_id, raw_talk, talk, {"talk_id": talk["talkId"], "dialog_count": len(talk["dialogList"]), "mapping_version": MAPPING_VERSION})
    nodes = talk["dialogList"]
    node_ids = {int(node["id"]) for node in nodes}
    next_map: dict[int, list[int]] = {}
    node_records: dict[int, str] = {}
    unresolved_speakers = 0
    for index, raw_node in enumerate(raw_talk.get("PFALHAKIILD", [])):
        node = deob_dialog_node(raw_node)
        node_id = int(node["id"])
        node_record = registrar.record(
            talk_path,
            registrar.files[talk_path],
            f"/dialogList/{index}",
            "dialog_node",
            node_id,
            raw_node,
            persist_raw_json=persist_node_raw_json,
        )
        node_records[node_id] = node_record
        passage_id = dialog_evidence_id(talk_evidence_id, node_id)
        db.ensure_passage(
            passage_id,
            talk_document,
            "dialog",
            f"dialog:{talk_evidence_id}",
            node_id,
            "raw_talk_evidence+native_dialog_id:v1",
            talk_evidence_id=talk_evidence_id,
        )
        role = node.get("talkRole") if isinstance(node.get("talkRole"), dict) else {}
        speaker_hash = as_hash(node.get("talkRoleNameTextMapHash"))
        speaker = {
            "role_type": role.get("_type"),
            "role_id": role.get("_id"),
            "role_numeric_id": role.get("_roleId"),
            "name_text_map_hash": speaker_hash,
        }
        content_hash = as_hash(node.get("talkContentTextMapHash"))
        db.ensure_passage_version(
            passage_id,
            snapshot,
            node_record,
            f"{index:04d}",
            raw_node,
            node,
            speaker=speaker,
            talk_evidence_id=talk_evidence_id,
        )
        db.add_fact("passage", passage_id, "speaker_role", {k: v for k, v in speaker.items() if k != "name_text_map_hash"}, "original", node_record)
        db.add_fact("passage", passage_id, "content_textmap_hash", content_hash, "original", node_record)
        for language in LANGUAGES:
            entry = lookup.get((language, content_hash or ""))
            if entry is None:
                registrar.issue("warning", "missing_textmap_reference", f"Dialog {node_id} has no {language} content TextMap value", source_record_id=node_record, details={"dialog_id": node_id, "language": language, "hash": content_hash})
                continue
            localized_id = digest_id("lt", registrar.snapshot_id, language, entry.shard, entry.textmap_hash)
            db.ensure_passage_text(passage_id, snapshot, language, entry.source_record_id, entry.text, localized_id)
        if speaker_hash:
            for language in LANGUAGES:
                entry = lookup.get((language, speaker_hash))
                if entry:
                    db.add_fact("passage", passage_id, "speaker_name", {"text_map_hash": speaker_hash, "text": entry.text}, "original", entry.source_record_id, language=language)
                else:
                    registrar.issue("warning", "missing_speaker_textmap_reference", f"Dialog {node_id} has no {language} speaker name value", source_record_id=node_record, details={"dialog_id": node_id, "language": language, "hash": speaker_hash})
        elif role.get("_type") == "TALK_ROLE_NPC":
            unresolved_speakers += 1
            registrar.issue("warning", "unresolved_speaker_name_hash", f"Dialog {node_id} has NPC role {role.get('_id')} but speaker name hash is 0", source_record_id=node_record, details={"dialog_id": node_id, "role": role})
        next_ids = [int(value) for value in node.get("nextDialogs", []) if isinstance(value, int)]
        next_map[node_id] = next_ids
    # All target passages now exist.  Add graph edges in a second pass so a
    # forward reference cannot violate the evidence-layer foreign key.
    for node_id, next_ids in next_map.items():
        for next_id in next_ids:
            if next_id in node_ids:
                db.add_edge(
                    dialog_evidence_id(talk_evidence_id, node_id),
                    dialog_evidence_id(talk_evidence_id, next_id),
                    "next",
                    "original",
                    node_records[node_id],
                )
            else:
                registrar.issue("warning", "dangling_dialog_edge", f"Dialog {node_id} points to absent dialog {next_id}", source_record_id=node_records[node_id], details={"from": node_id, "to": next_id})
    branch_nodes = sorted(node_id for node_id, next_ids in next_map.items() if len(next_ids) > 1)
    incoming: dict[int, int] = {node_id: 0 for node_id in node_ids}
    for next_ids in next_map.values():
        for next_id in next_ids:
            incoming[next_id] = incoming.get(next_id, 0) + 1
    merge_nodes = sorted(node_id for node_id, count in incoming.items() if count > 1)
    branch_targets = {node_id: sorted(next_ids) for node_id, next_ids in next_map.items() if len(next_ids) > 1}
    merge_sources = {
        node_id: sorted(source_id for source_id, next_ids in next_map.items() if node_id in next_ids)
        for node_id, count in incoming.items()
        if count > 1
    }
    db.add_fact(
        "document",
        talk_document,
        "graph_summary",
        {
            "node_count": len(nodes),
            "edge_count": sum(len(v) for v in next_map.values()),
            "branch_nodes": branch_nodes,
            "branch_targets": branch_targets,
            "merge_nodes": merge_nodes,
            "merge_sources": merge_sources,
        },
        "inferred",
        talk_file_record_id,
        method="deterministic graph degree analysis",
        confidence=1.0,
    )
    return {
        "talk_document_id": talk_document,
        "talk_evidence_id": talk_evidence_id,
        "talk_namespace": namespace,
        "talk_id": talk_id,
        "node_count": len(nodes),
        "edge_count": sum(len(v) for v in next_map.values()),
        "branch_nodes": branch_nodes,
        "branch_targets": branch_targets,
        "merge_nodes": merge_nodes,
        "merge_sources": merge_sources,
        "unresolved_speaker_count": unresolved_speakers,
    }


def _insert_readables(
    registrar: Registrar,
    metadata: dict[str, dict[str, Any]],
    lookup: dict[tuple[str, str], Any],
) -> list[dict[str, Any]]:
    db = registrar.db
    results: list[dict[str, Any]] = []
    for stem in READABLE_STEMS:
        meta = metadata.get(stem)
        if not meta or not meta.get("doc_id"):
            continue
        document_id = stable_id("readable", stem)
        content_type = readable_content_type(stem)
        doc_id = int(meta["doc_id"])
        title_hash = meta.get("title_hash")
        language_data: dict[str, dict[str, Any]] = {}
        for language in LANGUAGES:
            located = None
            for relative in _readable_paths(stem, language):
                located = registrar.bundle.try_locate(relative)
                if located:
                    break
            if located is None:
                registrar.issue("warning", "readable_language_file_missing", f"Missing {language} Readable file for {stem}", source_record_id=meta.get("doc_record_id"), details={"stem": stem, "language": language})
                language_data[language] = {"raw": "", "relative": None, "record_id": None, "paragraphs": []}
                continue
            raw_bytes = located.path.read_bytes()
            raw_text = raw_bytes.decode("utf-8-sig")
            relative = located.relative_path
            file_id = registrar.source_file(relative, located, raw_bytes)
            record_id = registrar.record(relative, file_id, "/text", "readable_text", stem, raw_text)
            language_data[language] = {"raw": raw_text, "relative": relative, "record_id": record_id, "paragraphs": split_readable(raw_text)}
        chs = language_data["CHS"]
        placeholder = normalize_text(chs["raw"]).strip().lower() in {"test", "todo", "placeholder"}
        status = "placeholder" if placeholder else "active"
        db.ensure_document(document_id, content_type, "readable", stem, status)
        version_record = chs.get("record_id") or meta.get("doc_record_id") or meta["loc_record_id"]
        raw_content = {language: data["raw"] for language, data in language_data.items()}
        normalized_content = {language: normalize_text(data["raw"]) for language, data in language_data.items()}
        db.ensure_document_version(
            document_id,
            registrar.snapshot_id,
            version_record,
            raw_content,
            normalized_content,
            {"stem": stem, "document_id": doc_id, "localization_id": meta["loc_id"], "title_hash": title_hash, "content_type": content_type},
            status,
        )
        db.add_fact("document", document_id, "document_native_id", doc_id, "original", meta.get("doc_record_id"))
        db.add_fact("document", document_id, "localization_id", meta["loc_id"], "original", meta["loc_record_id"])
        db.add_fact("document", document_id, "content_type", content_type, "inferred", meta.get("doc_record_id"), method="readable filename prefix", confidence=1.0)
        for language in LANGUAGES:
            _add_text_fact(registrar, lookup, "document", document_id, "title", title_hash, language, meta.get("doc_record_id") or meta["loc_record_id"])
        if placeholder:
            registrar.issue("warning", "placeholder_text", f"Readable {stem} contains placeholder-like text", source_record_id=chs.get("record_id"), details={"stem": stem, "text": chs["raw"]})
        for language, data in language_data.items():
            if not data["raw"]:
                registrar.issue("warning", "empty_readable_text", f"Readable {stem} is empty in {language}", source_record_id=data.get("record_id") or meta.get("doc_record_id"), details={"stem": stem, "language": language})
        max_paragraphs = max((len(data["paragraphs"]) for data in language_data.values()), default=0)
        for paragraph_index in range(max_paragraphs):
            passage_id = stable_id("readable_passage", f"{stem}:{paragraph_index}")
            passage_status = "placeholder" if placeholder else "active"
            db.ensure_passage(passage_id, document_id, "readable_paragraph", "readable", f"{stem}:{paragraph_index}", "filename+paragraph_index:v1")
            base_data = next((language_data[lang] for lang in LANGUAGES if paragraph_index < len(language_data[lang]["paragraphs"])), None)
            if base_data is None:
                continue
            base_text = base_data["paragraphs"][paragraph_index][1]
            db.ensure_passage_version(passage_id, registrar.snapshot_id, base_data["record_id"], f"{paragraph_index:04d}", base_text, normalize_text(base_text), status=passage_status)
            for language, data in language_data.items():
                if paragraph_index >= len(data["paragraphs"]):
                    continue
                text = data["paragraphs"][paragraph_index][1]
                start = data["raw"].find(text)
                end = start + len(text) if start >= 0 else None
                db.ensure_passage_text(passage_id, registrar.snapshot_id, language, data["record_id"], text, source_start=start if start >= 0 else None, source_end=end)
        results.append({"stem": stem, "document_id": document_id, "document_native_id": doc_id, "status": status, "languages": {lang: {"bytes": len(data["raw"].encode("utf-8")), "passages": len(data["paragraphs"])} for lang, data in language_data.items()}})
    return results


def _insert_subtitle(registrar: Registrar, known_talk_ids: list[int]) -> dict[str, Any]:
    db = registrar.db
    language_cues: dict[str, list[Any]] = {}
    source_records: dict[str, str] = {}
    relative_paths: dict[str, str] = {}
    for language in LANGUAGES:
        relative = f"Subtitle/{language}/{SUBTITLE_STEM}_{language}.srt"
        located = registrar.bundle.try_locate(relative)
        if located is None:
            registrar.issue("warning", "subtitle_language_file_missing", f"Missing subtitle {language} for {SUBTITLE_STEM}", details={"language": language})
            language_cues[language] = []
            continue
        raw_bytes = located.path.read_bytes()
        raw_text = raw_bytes.decode("utf-8-sig")
        file_id = registrar.source_file(relative, located, raw_bytes)
        file_record = registrar.record(relative, file_id, "/text", "subtitle_file", subtitle_base(SUBTITLE_STEM), raw_text)
        cues = parse_srt(raw_text)
        language_cues[language] = cues
        source_records[language] = file_record
        relative_paths[language] = relative
    document_id = stable_id("subtitle", subtitle_base(SUBTITLE_STEM))
    talk_id = subtitle_talk_id(SUBTITLE_STEM, known_talk_ids)
    source_record = source_records.get("CHS") or next(iter(source_records.values()), None)
    if source_record is None:
        return {"document_id": document_id, "cue_count": 0, "talk_id": None}
    raw_for_version = {language: [cue.__dict__ for cue in cues] for language, cues in language_cues.items()}
    normalized_for_version = {
        language: [
            {**cue.__dict__, "text": normalize_text(cue.text)}
            for cue in cues
        ]
        for language, cues in language_cues.items()
    }
    db.ensure_document(document_id, "subtitle", "subtitle", subtitle_base(SUBTITLE_STEM), "active")
    db.ensure_document_version(document_id, registrar.snapshot_id, source_record, raw_for_version, normalized_for_version, {"stem": subtitle_base(SUBTITLE_STEM), "talk_id_from_filename": talk_id})
    db.add_fact("document", document_id, "filename_stem", subtitle_base(SUBTITLE_STEM), "original", source_record)
    if talk_id is not None:
        talk_row = db.conn.execute(
            """SELECT stable_id FROM document
               WHERE content_type='talk' AND native_id=?
               ORDER BY CASE native_namespace WHEN 'QUEST' THEN 0 ELSE 1 END, stable_id
               LIMIT 1""",
            (str(talk_id),),
        ).fetchone()
        if talk_row is not None:
            db.add_relation(document_id, str(talk_row[0]), "subtitle_of", "inferred", source_record, method="AQ token in subtitle filename", confidence=0.99)
        else:
            registrar.issue("warning", "subtitle_talk_logical_missing", f"Subtitle filename names Talk {talk_id}, but no logical Talk document exists", source_record_id=source_record, details={"talk_id": talk_id})
    counts = {language: len(cues) for language, cues in language_cues.items()}
    if len(set(counts.values())) > 1:
        registrar.issue("warning", "subtitle_language_count_mismatch", f"Subtitle language cue counts differ: {counts}", source_record_id=source_record, details=counts)
    all_indices = sorted({cue.index for cues in language_cues.values() for cue in cues})
    cue_maps = {language: {cue.index: cue for cue in cues} for language, cues in language_cues.items()}
    for index in all_indices:
        passage_id = stable_id("subtitle_cue", f"{subtitle_base(SUBTITLE_STEM)}:{index}")
        base = next((cue_maps[language][index] for language in LANGUAGES if index in cue_maps[language]), None)
        if base is None:
            continue
        db.ensure_passage(passage_id, document_id, "subtitle_cue", "subtitle", f"{subtitle_base(SUBTITLE_STEM)}:{index}", "subtitle_stem+cue_index:v1")
        base_record = source_records.get("CHS") or source_records.get(next(iter(source_records), ""))
        db.ensure_passage_version(passage_id, registrar.snapshot_id, base_record, f"{index:04d}", base.text, normalize_text(base.text), start_ms=base.start_ms, end_ms=base.end_ms)
        for language in LANGUAGES:
            cue = cue_maps[language].get(index)
            if cue is None:
                registrar.issue("warning", "subtitle_cue_missing_language", f"Subtitle cue {index} missing in {language}", source_record_id=source_records.get(language) or source_record, details={"index": index, "language": language})
                continue
            db.ensure_passage_text(passage_id, registrar.snapshot_id, language, source_records[language], cue.text)
    return {"document_id": document_id, "cue_count": len(all_indices), "language_counts": counts, "talk_id": talk_id}


def _load_discovered_talk_details(
    registrar: Registrar,
    index: TalkDiscoveryIndex,
    talk_ids: list[int],
    expected_init_dialogs: dict[int, int] | None = None,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Materialize only deterministically resolved Talk candidates.

    Discovery metadata is retained for every reference.  A candidate becomes
    ingestible only after the path is read again, its raw checksum is compared
    with the index, and the mapped native ID is checked against the requested
    Talk ID.  This prevents a stale cache or a wrong hash path from becoming an
    active evidence document.
    """

    details: dict[int, dict[str, Any]] = {}
    for talk_id in sorted(set(talk_ids)):
        result = index.resolve(talk_id, (expected_init_dialogs or {}).get(talk_id))
        detail: dict[str, Any] = {
            "status": result.status,
            "discovery_method": result.discovery_method,
            "native_id_verified": result.native_id_verified,
            "reason": result.reason,
            "candidates": [candidate.__dict__ for candidate in result.candidates],
        }
        candidate = result.candidate
        if candidate is None:
            details[talk_id] = detail
            continue
        try:
            located, raw = registrar.bundle.read_bytes(candidate.relative_path)
            if sha256_bytes(raw) != candidate.raw_sha256:
                raise SchemaCanaryError("discovery cache raw checksum differs from source file")
            raw_value = json.loads(raw.decode("utf-8-sig"))
            normalized = deob_talk_generic(raw_value, expected_talk_id=talk_id)
            normalized_checksum = sha256_bytes(canonical_json(normalized).encode("utf-8"))
            if normalized_checksum != candidate.normalized_checksum:
                raise SchemaCanaryError("discovery cache normalized checksum differs from source file")
            native_namespace, namespace_source = _talk_native_namespace(raw_value)
            file_id = registrar.source_file(candidate.relative_path, located, raw)
            record_id = registrar.record(candidate.relative_path, file_id, "/", "talk_file", talk_id, raw_value)
            detail.update(
                {
                    "talk_id": talk_id,
                    "raw": raw_value,
                    "normalized": normalized,
                    "relative_path": candidate.relative_path,
                    "file_record_id": record_id,
                    "source_file_id": file_id,
                    "source_pointer": "/",
                    "raw_sha256": candidate.raw_sha256,
                    "normalized_checksum": candidate.normalized_checksum,
                    "native_namespace": native_namespace,
                    "native_namespace_source": namespace_source,
                }
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, SchemaCanaryError, TypeError, ValueError) as exc:
            detail["status"] = "unresolved"
            detail["native_id_verified"] = False
            detail["reason"] = f"resolved candidate failed source revalidation: {exc}"
            registrar.issue(
                "error",
                "talk_discovery_revalidation_failed",
                f"Talk {talk_id} discovery candidate could not be revalidated",
                details={"talk_id": talk_id, "relative_path": candidate.relative_path, "error": str(exc)},
            )
        details[talk_id] = detail
    report = {
        "talks": [
            {
                "talk_id": talk_id,
                "status": detail["status"],
                "discovery_method": detail.get("discovery_method"),
                "native_id_verified": detail.get("native_id_verified", False),
                "reason": detail.get("reason"),
                "relative_path": detail.get("relative_path"),
                "candidate_count": len(detail.get("candidates", [])),
                "expected_init_dialog": (expected_init_dialogs or {}).get(talk_id),
                "node_count": len(detail["normalized"].get("dialogList", [])) if detail.get("normalized") else None,
                "edge_count": sum(len(node.get("nextDialogs", [])) for node in detail["normalized"].get("dialogList", []) if isinstance(node, dict)) if detail.get("normalized") else None,
            }
            for talk_id, detail in sorted(details.items())
        ],
        "status_counts": {
            status: sum(1 for detail in details.values() if detail["status"] == status)
            for status in ("resolved", "genuinely_absent", "ambiguous", "unresolved")
        },
    }
    return details, report


def _quest_paths(bundle: SourceBundle) -> list[tuple[str, Path]]:
    """Return first-root-wins raw Quest JSON paths in deterministic order."""
    found: dict[str, Path] = {}
    for root in bundle.roots:
        directory = root / "BinOutput" / "Quest"
        if not directory.is_dir():
            continue
        for path in directory.glob("*.json"):
            if path.is_file():
                found.setdefault(path.relative_to(root).as_posix(), path)
    return sorted(found.items())


def _raw_quest_talk_refs(raw_quest: dict[str, Any]) -> list[Any]:
    refs = raw_quest.get("OJACLOOEAMG")
    if isinstance(refs, list):
        return refs
    refs = raw_quest.get("talks")
    return refs if isinstance(refs, list) else []


def _scan_quest_inventory(registrar: Registrar) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Scan every raw Quest root and retain only compact insertion metadata."""
    records: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    seen_ids: dict[int, str] = {}
    duplicate_ids: set[int] = set()
    total = 0
    for relative, path in _quest_paths(registrar.bundle):
        total += 1
        raw_bytes = path.read_bytes()
        file_id = registrar.source_file(relative, raw=raw_bytes)
        try:
            raw_value = json.loads(raw_bytes.decode("utf-8-sig"))
            root_record = registrar.record(
                relative,
                file_id,
                "/",
                "quest_file",
                None,
                raw_value,
                persist_raw_json=False,
            )
            quest = deob_quest_generic(raw_value)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, SchemaCanaryError, TypeError, ValueError) as exc:
            invalid.append({"relative_path": relative, "error": str(exc)})
            registrar.issue(
                "warning",
                "invalid_quest_root",
                f"Quest raw root could not be normalized: {relative}",
                source_file_id=file_id,
                details={"relative_path": relative, "error": str(exc)},
            )
            continue
        quest_id = int(quest["id"])
        if quest_id in seen_ids:
            duplicate_ids.add(quest_id)
            registrar.issue(
                "error",
                "duplicate_quest_native_id",
                f"Quest native ID {quest_id} appears in multiple raw files",
                source_file_id=file_id,
                source_record_id=root_record,
                details={"quest_id": quest_id, "first_path": seen_ids[quest_id], "second_path": relative},
            )
        else:
            seen_ids[quest_id] = relative
        refs: list[dict[str, Any]] = []
        for index, raw_ref in enumerate(_raw_quest_talk_refs(raw_value)):
            try:
                ref = deob_quest_talk_reference(raw_ref)
                talk_id = ref.get("talkId")
                if not isinstance(talk_id, int) or isinstance(talk_id, bool):
                    raise SchemaCanaryError("talk reference has no integer talkId")
                refs.append(
                    {
                        "index": index,
                        "talk_id": int(talk_id),
                        "init_dialog": ref.get("initDialog") if isinstance(ref.get("initDialog"), int) else None,
                        "perform_cfg": ref.get("performCfg"),
                    }
                )
            except (SchemaCanaryError, TypeError, ValueError) as exc:
                registrar.issue(
                    "warning",
                    "invalid_quest_talk_reference",
                    f"Quest {quest_id} contains an invalid Talk reference",
                    source_file_id=file_id,
                    source_record_id=root_record,
                    details={"quest_id": quest_id, "index": index, "error": str(exc)},
                )
        records.append(
            {
                "quest_id": quest_id,
                "relative_path": relative,
                "source_file_id": file_id,
                "source_record_id": root_record,
                "talk_refs": refs,
                "subquest_count": len(quest.get("subQuests", [])),
                "talk_reference_count": len(refs),
                "hashes": sorted(hash_refs(quest)),
                "family": _quest_family(refs),
            }
        )
    return records, {
        "file_count": total,
        "valid_record_count": len(records),
        "invalid_record_count": len(invalid),
        "unique_quest_count": len(seen_ids),
        "duplicate_native_id_count": len(duplicate_ids),
        "subquest_count": sum(int(item["subquest_count"]) for item in records),
        "talk_reference_count": sum(int(item["talk_reference_count"]) for item in records),
        "unique_talk_reference_count": len({ref["talk_id"] for item in records for ref in item["talk_refs"]}),
        "invalid_records": invalid,
        "family_distribution": _family_distribution(records),
    }


def _quest_family(refs: list[dict[str, Any]]) -> str:
    values = [str(ref.get("perform_cfg")) for ref in refs if ref.get("perform_cfg")]
    if not values:
        return "unknown"
    first = values[0].replace("\\", "/")
    return first.split("/", 1)[0] or "unknown"


def _family_distribution(records: list[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for record in records:
        family = str(record.get("family") or "unknown")
        result[family] = result.get(family, 0) + 1
    return dict(sorted(result.items()))


def _resolve_talk_metadata(
    index: TalkDiscoveryIndex,
    talk_ids: Iterable[int],
    expected_init_dialogs: dict[int, int] | None = None,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Resolve references without retaining raw JSON in the metadata map."""
    results = index.resolve_many(talk_ids, expected_init_dialogs)
    details: dict[int, dict[str, Any]] = {}
    for talk_id, result in results.items():
        detail: dict[str, Any] = {
            "talk_id": int(talk_id),
            "status": result.status,
            "discovery_method": result.discovery_method,
            "native_id_verified": result.native_id_verified,
            "reason": result.reason,
            "candidates": [candidate.__dict__ for candidate in result.candidates],
        }
        candidate = result.candidate
        if candidate is not None:
            detail.update(
                {
                    "candidate": candidate,
                    "relative_path": candidate.relative_path,
                    "raw_sha256": candidate.raw_sha256,
                    "normalized_checksum": candidate.normalized_checksum,
                    "native_namespace": candidate.native_namespace,
                    "native_namespace_source": candidate.native_namespace_source,
                    "source_pointer": "/",
                }
            )
        details[int(talk_id)] = detail
    return details, {
        "talks": [
            {
                "talk_id": talk_id,
                "status": detail["status"],
                "discovery_method": detail["discovery_method"],
                "native_id_verified": detail["native_id_verified"],
                "reason": detail.get("reason"),
                "relative_path": detail.get("relative_path"),
                "candidate_count": len(detail.get("candidates", [])),
                "expected_init_dialog": (expected_init_dialogs or {}).get(talk_id),
                "node_count": detail.get("candidate").node_count if detail.get("candidate") else None,
                "edge_count": detail.get("candidate").edge_count if detail.get("candidate") else None,
                "native_namespace": detail.get("native_namespace"),
            }
            for talk_id, detail in sorted(details.items())
        ],
        "status_counts": {
            status: sum(1 for detail in details.values() if detail["status"] == status)
            for status in ("resolved", "genuinely_absent", "ambiguous", "unresolved")
        },
    }


def _materialize_talk_detail(
    registrar: Registrar,
    detail: dict[str, Any],
    *,
    persist_raw_json: bool = True,
) -> dict[str, Any]:
    """Read and revalidate one resolved candidate on demand."""
    candidate = detail.get("candidate")
    if candidate is None:
        return detail
    located, raw = registrar.bundle.read_bytes(candidate.relative_path)
    if sha256_bytes(raw) != candidate.raw_sha256:
        raise SchemaCanaryError(f"Talk {candidate.talk_id} raw checksum changed: {candidate.relative_path}")
    raw_value = json.loads(raw.decode("utf-8-sig"))
    normalized = deob_talk_generic(raw_value, expected_talk_id=int(candidate.talk_id))
    normalized_checksum = sha256_bytes(canonical_json(normalized).encode("utf-8"))
    if normalized_checksum != candidate.normalized_checksum:
        raise SchemaCanaryError(f"Talk {candidate.talk_id} normalized checksum changed: {candidate.relative_path}")
    file_id = registrar.source_file(candidate.relative_path, located, raw)
    record_id = registrar.record(
        candidate.relative_path,
        file_id,
        "/",
        "talk_file",
        candidate.talk_id,
        raw_value,
        persist_raw_json=persist_raw_json,
    )
    namespace, namespace_source = _talk_native_namespace(raw_value)
    detail.update(
        {
            "raw": raw_value,
            "normalized": normalized,
            "relative_path": candidate.relative_path,
            "file_record_id": record_id,
            "source_file_id": file_id,
            "source_pointer": "/",
            "raw_sha256": candidate.raw_sha256,
            "normalized_checksum": candidate.normalized_checksum,
            "native_namespace": namespace,
            "native_namespace_source": namespace_source,
        }
    )
    return detail


def _register_talk_evidence_inventory(
    registrar: Registrar,
    index: TalkDiscoveryIndex,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Register every valid raw Talk occurrence without ingesting its dialog yet."""
    by_path: dict[str, dict[str, Any]] = {}
    logical_counts: dict[tuple[str, int], int] = {}
    for candidate in index.candidates:
        located, raw = registrar.bundle.read_bytes(candidate.relative_path)
        file_id = registrar.source_file(candidate.relative_path, located, raw)
        raw_value = json.loads(raw.decode("utf-8-sig"))
        namespace, namespace_source = _talk_native_namespace(raw_value)
        record_id = registrar.record(
            candidate.relative_path,
            file_id,
            "/",
            "talk_file",
            candidate.talk_id,
            raw_value,
            persist_raw_json=False,
        )
        detail = {
            "talk_id": candidate.talk_id,
            "relative_path": candidate.relative_path,
            "file_record_id": record_id,
            "source_file_id": file_id,
            "source_pointer": "/",
            "raw_sha256": candidate.raw_sha256,
            "normalized_checksum": candidate.normalized_checksum,
            "native_namespace": namespace,
            "native_namespace_source": namespace_source,
        }
        evidence_id = _ensure_talk_evidence(registrar, detail)
        detail["evidence_id"] = evidence_id
        by_path[candidate.relative_path] = detail
        key = (namespace, int(candidate.talk_id))
        logical_counts[key] = logical_counts.get(key, 0) + 1
        logical_id = _logical_document_id(namespace, int(candidate.talk_id))
        registrar.db.ensure_logical_talk(logical_id, namespace, int(candidate.talk_id), "unique", {"source": "raw_talk_inventory"})
        registrar.db.link_logical_talk_evidence(
            logical_id,
            str(evidence_id),
            "resolved",
            "original",
            record_id,
            method="native_id_exact_scan",
            confidence=1.0,
        )
    for (namespace, talk_id), count in logical_counts.items():
        logical_id = _logical_document_id(namespace, talk_id)
        registrar.db.ensure_logical_talk(
            logical_id,
            namespace,
            talk_id,
            "multiple_evidence" if count > 1 else "unique",
            {"source": "raw_talk_inventory", "evidence_count": count},
        )
    return by_path, {
        "raw_talk_evidence_count": len(by_path),
        "logical_talk_count": len(logical_counts),
        "logical_talk_multiple_evidence_count": sum(1 for count in logical_counts.values() if count > 1),
        "logical_talk_max_evidence_membership": max(logical_counts.values(), default=0),
    }


def ingest_slice(
    db_path: str | Path,
    source_roots: list[str | Path],
    manifest_path: str | Path,
    *,
    exact_query: str = TEXTMAP_GOLD_QUERY,
    discover_talks: bool = False,
    discovery_cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    snap_id = snapshot_id(manifest)
    bundle = SourceBundle(source_roots)
    with Database(db_path) as db:
        db.initialize()
        with db.transaction():
            db.ensure_source(manifest["source_id"], "primary", manifest["upstream"], "Upstream repository snapshot")
            db.ensure_snapshot(
                {
                    "id": snap_id,
                    "source_id": manifest["source_id"],
                    "release_label": manifest["release_label"],
                    "build": manifest["build"],
                    "commit": manifest["commit"],
                    "tree": manifest["tree"],
                    "observed_at": manifest["observed_at"],
                    "parser_version": PHASE2_PARSER_VERSION if discover_talks else PARSER_VERSION,
                    "mapping_version": MAPPING_VERSION,
                    "status": "staged",
                    "metadata": {"manifest": manifest, "source_roots": [str(Path(root).resolve()) for root in source_roots], "textmap_scope": "selected references plus CHS exact gold matches", "talk_discovery": discover_talks},
                }
            )
            registrar = Registrar(db, bundle, snap_id)
            raw_quest, quest_file_id, quest_file_record_id = _load_json(registrar, QUEST_PATH, "quest_file")
            quest = deob_quest(raw_quest)
            metadata = _load_readable_metadata(registrar)
            talk_excel = _load_talk_excel(registrar)
            discovery_index: TalkDiscoveryIndex | None = None
            talk_discovery_summary: dict[str, Any] | None = None
            if discover_talks:
                referenced_talk_ids = [
                    int(deob_quest_talk_reference(raw_ref)["talkId"])
                    for raw_ref in raw_quest.get("OJACLOOEAMG", [])
                ]
                expected_init_dialogs = {
                    int(deob_quest_talk_reference(raw_ref)["talkId"]): int(deob_quest_talk_reference(raw_ref)["initDialog"])
                    for raw_ref in raw_quest.get("OJACLOOEAMG", [])
                    if isinstance(deob_quest_talk_reference(raw_ref).get("initDialog"), int)
                }
                discovery_index = build_talk_index(
                    bundle,
                    manifest,
                    snap_id,
                    cache_dir=discovery_cache_dir,
                )
                talk_details, talk_discovery_summary = _load_discovered_talk_details(registrar, discovery_index, referenced_talk_ids, expected_init_dialogs)
                talk_discovery_summary.update(
                    {
                        "discovery_version": discovery_index.discovery_version,
                        "mapping_version": discovery_index.mapping_version,
                        "inventory_complete": discovery_index.inventory_complete,
                        "inventory_root": discovery_index.inventory_root,
                        "source_roots": list(discovery_index.source_roots),
                        "candidate_file_count": discovery_index.candidate_file_count,
                        "valid_file_count": discovery_index.valid_file_count,
                        "invalid_file_count": discovery_index.invalid_file_count,
                        "cache_path": discovery_index.cache_path,
                    }
                )
                raw_talk = None
                talk = None
            else:
                raw_talk, _talk_file_id, talk_file_record_id = _load_json(registrar, TALK_PATH, "talk_file")
                talk = deob_talk(raw_talk)
                talk_details = {
                    700814: {
                        "status": "resolved",
                        "discovery_method": "phase1_selected_path",
                        "native_id_verified": True,
                        "reason": None,
                        "candidates": [],
                        "raw": raw_talk,
                        "normalized": talk,
                        "relative_path": TALK_PATH,
                        "file_record_id": talk_file_record_id,
                        "source_file_id": _talk_file_id,
                        "source_pointer": "/",
                        "talk_id": 700814,
                        "raw_sha256": sha256_bytes(registrar.bundle.read_bytes(TALK_PATH)[1]),
                        "normalized_checksum": sha256_bytes(canonical_json(talk).encode("utf-8")),
                        "native_namespace": _talk_native_namespace(raw_talk)[0],
                        "native_namespace_source": _talk_native_namespace(raw_talk)[1],
                    }
                }
                talk_discovery_summary = {
                    "talks": [{"talk_id": 700814, "status": "resolved", "discovery_method": "phase1_selected_path", "native_id_verified": True, "relative_path": TALK_PATH, "candidate_count": 1, "node_count": len(talk.get("dialogList", [])), "edge_count": sum(len(node.get("nextDialogs", [])) for node in talk.get("dialogList", []) if isinstance(node, dict))}],
                    "status_counts": {"resolved": 1, "genuinely_absent": 0, "ambiguous": 0, "unresolved": 0},
                }
            if talk_excel and talk_excel.get(700814, {}).get("initDialog") != 70081401:
                raise SchemaCanaryError("TalkExcel 700814 initDialog canary failed")
            selected_hashes = hash_refs(quest)
            for detail in talk_details.values():
                if detail.get("normalized"):
                    selected_hashes.update(hash_refs(detail["normalized"]))
            selected_hashes.update(meta["title_hash"] for meta in metadata.values() if meta.get("title_hash"))
            entries, conflicts, textmap_counts = scan_textmaps(bundle, registrar, LANGUAGES, selected_hashes, exact_query)
            for conflict in conflicts:
                db.add_textmap_conflict(snap_id, conflict.__dict__)
                registrar.issue("error", "textmap_hash_conflict", f"TextMap hash {conflict.textmap_hash} differs between {conflict.shard_a} and {conflict.shard_b} ({conflict.language})", details=conflict.__dict__)
            for language, rows in entries.items():
                for entry in rows:
                    db.ensure_localized_text(snap_id, language, entry.shard, entry.textmap_hash, entry.source_file_id, entry.source_record_id, entry.text)
            lookup = selected_text_lookup(entries)
            quest_summary = _insert_quest(registrar, raw_quest, quest, quest_file_record_id, lookup, talk_details, talk_excel)
            quest_summary["talk_reference_count"] = len(quest_summary["talk_ids"])
            quest_summary["talk_detail_count"] = sum(1 for detail in talk_details.values() if detail.get("status") == "resolved" and detail.get("normalized") is not None)
            quest_summary["talk_reference_only_count"] = quest_summary["talk_reference_count"] - quest_summary["talk_detail_count"]
            readable_summary = _insert_readables(registrar, metadata, lookup)
            subtitle_summary = _insert_subtitle(registrar, quest_summary["talk_ids"])
            db.rebuild_fts(snap_id)
            if conflicts:
                db.conn.execute("UPDATE source_snapshot SET status='quarantined' WHERE id=?", (snap_id,))
            else:
                db.conn.execute("UPDATE source_snapshot SET status='active' WHERE id=?", (snap_id,))
            if discover_talks and discovery_index is not None:
                canary_summary = evaluate_canaries(bundle, discovery_index, exclude={7008})
            else:
                canary_summary = None
        report = db.report(snap_id)
        talk_700814_row = db.conn.execute(
            """SELECT stable_id FROM document
               WHERE content_type='talk' AND native_id='700814'
               ORDER BY CASE native_namespace WHEN 'QUEST' THEN 0 ELSE 1 END, stable_id
               LIMIT 1"""
        ).fetchone()
        talk_700814_document = str(talk_700814_row[0]) if talk_700814_row else _logical_document_id(UNKNOWN_TALK_NAMESPACE, 700814)
        talk_700814_detail = (quest_summary["talk_details"].get(700814) or {})
        evidence_700814 = talk_700814_detail.get("talk_evidence_id")
        def dialog_report_id(node_id: int) -> str:
            return dialog_evidence_id(str(evidence_700814), node_id) if evidence_700814 else stable_id("dialog", node_id)
        report.update(
            {
                "status": db.conn.execute("SELECT status FROM source_snapshot WHERE id=?", (snap_id,)).fetchone()[0],
                "manifest": {"source_id": manifest["source_id"], "release_label": manifest["release_label"], "build": manifest["build"], "commit": manifest["commit"], "tree": manifest["tree"]},
                "textmap_counts": textmap_counts,
                "textmap_selected_hash_count": len(selected_hashes),
                "quest": quest_summary,
                "talk_700814": {
                    "document_id": talk_700814_document,
                    "talk_evidence_id": evidence_700814,
                    "dialog_node_count": db.conn.execute("SELECT COUNT(*) FROM passage WHERE document_id=?", (talk_700814_document,)).fetchone()[0],
                    "edge_count": db.conn.execute("SELECT COUNT(*) FROM passage_edge WHERE from_passage_id IN (SELECT stable_id FROM passage WHERE document_id=?)", (talk_700814_document,)).fetchone()[0],
                    "branch_nodes": [dialog_report_id(node_id) for node_id in talk_700814_detail.get("branch_nodes", [])],
                    "branch_targets": {dialog_report_id(node_id): [dialog_report_id(target) for target in targets] for node_id, targets in talk_700814_detail.get("branch_targets", {}).items()},
                    "merge_nodes": [dialog_report_id(node_id) for node_id in talk_700814_detail.get("merge_nodes", [])],
                    "merge_sources": {dialog_report_id(node_id): [dialog_report_id(source) for source in sources] for node_id, sources in talk_700814_detail.get("merge_sources", {}).items()},
                    "unresolved_speaker_count": talk_700814_detail.get("unresolved_speaker_count", 0),
                },
                "readables": readable_summary,
                "subtitle": subtitle_summary,
                "talk_discovery": talk_discovery_summary,
                "canary": canary_summary,
                "fallbacks": [],
                "unparsed_fields": [
                    {"area": "QuestDialogue/InterAction", "reason": "camera/action behavior fields intentionally outside Phase 1 text slice"},
                    *([] if discover_talks else [{"area": "Talk references", "reason": "14 referenced raw Talk files are not present in the local fixture; references are retained as unresolved documents"}]),
                ],
            }
        )
        return report


def ingest_phase2(
    db_path: str | Path,
    source_roots: list[str | Path],
    manifest_path: str | Path,
    *,
    exact_query: str = TEXTMAP_GOLD_QUERY,
    discovery_cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run the Phase 1 slice with deterministic Talk discovery enabled."""

    return ingest_slice(
        db_path,
        source_roots,
        manifest_path,
        exact_query=exact_query,
        discover_talks=True,
        discovery_cache_dir=discovery_cache_dir,
    )


def ingest_full_quest(
    db_path: str | Path,
    source_roots: list[str | Path],
    manifest_path: str | Path,
    *,
    exact_query: str = TEXTMAP_GOLD_QUERY,
    discovery_cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Ingest the complete Quest/Talk evidence corpus for one fixed snapshot."""
    started = time.perf_counter()
    manifest = load_manifest(manifest_path)
    snap_id = snapshot_id(manifest)
    bundle = SourceBundle(source_roots)
    with Database(db_path) as db:
        db.initialize()
        with db.transaction():
            db.ensure_source(manifest["source_id"], "primary", manifest["upstream"], "Upstream repository snapshot")
            db.ensure_snapshot(
                {
                    "id": snap_id,
                    "source_id": manifest["source_id"],
                    "release_label": manifest["release_label"],
                    "build": manifest["build"],
                    "commit": manifest["commit"],
                    "tree": manifest["tree"],
                    "observed_at": manifest["observed_at"],
                    "parser_version": "phase3-full-quest-0.1",
                    "mapping_version": MAPPING_VERSION,
                    "status": "staged",
                    "metadata": {
                        "manifest": manifest,
                        "source_roots": [str(Path(root).resolve()) for root in source_roots],
                        "scope": "full Quest -> Subquest -> Talk -> Dialog evidence",
                        "identity_model": "logical_talk plus raw_talk_evidence",
                    },
                }
            )
            registrar = Registrar(db, bundle, snap_id)

            inventory_started = time.perf_counter()
            quest_records, quest_inventory = _scan_quest_inventory(registrar)
            quest_inventory["build_seconds"] = round(time.perf_counter() - inventory_started, 3)

            discovery_started = time.perf_counter()
            discovery_index = build_talk_index(
                bundle,
                manifest,
                snap_id,
                cache_dir=discovery_cache_dir,
            )
            all_talk_ids = sorted({ref["talk_id"] for item in quest_records for ref in item["talk_refs"]})
            expected_init_dialogs: dict[int, int] = {}
            init_conflicts: dict[int, list[int]] = {}
            for item in quest_records:
                for ref in item["talk_refs"]:
                    init_dialog = ref.get("init_dialog")
                    if not isinstance(init_dialog, int):
                        continue
                    talk_id = int(ref["talk_id"])
                    existing = expected_init_dialogs.get(talk_id)
                    if existing is None:
                        expected_init_dialogs[talk_id] = init_dialog
                    elif existing != init_dialog:
                        init_conflicts.setdefault(talk_id, sorted({existing, init_dialog})).append(init_dialog)
            for talk_id, values in sorted(init_conflicts.items()):
                registrar.issue(
                    "warning",
                    "talk_reference_init_dialog_conflict",
                    f"Talk {talk_id} has conflicting Quest initDialog references",
                    details={"talk_id": talk_id, "init_dialogs": sorted(set(values))},
                )
                expected_init_dialogs.pop(talk_id, None)
            talk_resolution, talk_discovery = _resolve_talk_metadata(discovery_index, all_talk_ids, expected_init_dialogs)
            talk_discovery.update(
                {
                    "discovery_version": discovery_index.discovery_version,
                    "mapping_version": discovery_index.mapping_version,
                    "inventory_complete": discovery_index.inventory_complete,
                    "inventory_root": discovery_index.inventory_root,
                    "source_roots": list(discovery_index.source_roots),
                    "candidate_file_count": discovery_index.candidate_file_count,
                    "valid_file_count": discovery_index.valid_file_count,
                    "invalid_file_count": discovery_index.invalid_file_count,
                    "cache_path": discovery_index.cache_path,
                    "expected_init_dialog_conflict_count": len(init_conflicts),
                    "build_seconds": round(time.perf_counter() - discovery_started, 3),
                }
            )

            evidence_started = time.perf_counter()
            evidence_by_path, evidence_summary = _register_talk_evidence_inventory(registrar, discovery_index)
            for detail in talk_resolution.values():
                candidates = detail.get("candidates", [])
                detail["evidence_ids"] = [
                    evidence_by_path[candidate["relative_path"]]["evidence_id"]
                    for candidate in candidates
                    if candidate.get("relative_path") in evidence_by_path
                ]
            evidence_summary["build_seconds"] = round(time.perf_counter() - evidence_started, 3)

            # First pass over resolved Talk roots collects every referenced hash;
            # raw dictionaries are released immediately and re-read on demand
            # during Quest insertion.
            textmap_hashes = set()
            for item in quest_records:
                textmap_hashes.update(item["hashes"])
            talk_hash_started = time.perf_counter()
            resolved_talks_for_hashes = 0
            for detail in talk_resolution.values():
                if detail.get("status") != "resolved":
                    continue
                materialized = _materialize_talk_detail(registrar, detail, persist_raw_json=False)
                textmap_hashes.update(hash_refs(materialized["normalized"]))
                materialized.pop("raw", None)
                materialized.pop("normalized", None)
                resolved_talks_for_hashes += 1
            talk_discovery["resolved_talks_hash_scan"] = resolved_talks_for_hashes
            talk_discovery["hash_scan_seconds"] = round(time.perf_counter() - talk_hash_started, 3)

            metadata = _load_readable_metadata(registrar)
            textmap_hashes.update(meta["title_hash"] for meta in metadata.values() if meta.get("title_hash"))
            entries, conflicts, textmap_counts = scan_textmaps(
                bundle,
                registrar,
                LANGUAGES,
                textmap_hashes,
                exact_query,
            )
            for conflict in conflicts:
                db.add_textmap_conflict(snap_id, conflict.__dict__)
                registrar.issue(
                    "error",
                    "textmap_hash_conflict",
                    f"TextMap hash {conflict.textmap_hash} differs between {conflict.shard_a} and {conflict.shard_b} ({conflict.language})",
                    details=conflict.__dict__,
                )
            for language, rows in entries.items():
                for entry in rows:
                    db.ensure_localized_text(
                        snap_id,
                        language,
                        entry.shard,
                        entry.textmap_hash,
                        entry.source_file_id,
                        entry.source_record_id,
                        entry.text,
                    )
            lookup = selected_text_lookup(entries)

            insertion_started = time.perf_counter()
            inserted_quests = 0
            inserted_subquests = 0
            inserted_talk_refs = 0
            inserted_dialog_nodes = 0
            for item in quest_records:
                located, raw_quest = bundle.read_json(item["relative_path"])
                quest = deob_quest_generic(raw_quest)
                quest_file_record_id = registrar.record(
                    item["relative_path"],
                    item["source_file_id"],
                    "/",
                    "quest_file",
                    item["quest_id"],
                    raw_quest,
                    persist_raw_json=False,
                )
                if quest_file_record_id != item["source_record_id"]:
                    registrar.issue(
                        "error",
                        "quest_source_record_identity_changed",
                        f"Quest source record identity changed during second pass: {item['relative_path']}",
                        source_file_id=item["source_file_id"],
                        details={"first": item["source_record_id"], "second": quest_file_record_id},
                    )
                details_for_quest: dict[int, dict[str, Any]] = {}
                for ref in item["talk_refs"]:
                    talk_id = int(ref["talk_id"])
                    base = talk_resolution.get(talk_id)
                    if base is None:
                        continue
                    detail = dict(base)
                    if detail.get("status") == "resolved":
                        detail = _materialize_talk_detail(registrar, detail)
                        evidence = evidence_by_path.get(detail.get("relative_path", ""))
                        if evidence:
                            detail["evidence_id"] = evidence["evidence_id"]
                    details_for_quest[talk_id] = detail
                talk_excel = _load_talk_excel(registrar, item["quest_id"]) if item["quest_id"] == 7008 else {}
                quest_summary = _insert_quest(
                    registrar,
                    raw_quest,
                    quest,
                    quest_file_record_id,
                    lookup,
                    details_for_quest,
                    talk_excel,
                    quest_relative=item["relative_path"],
                    quest_id_value=item["quest_id"],
                    persist_raw_json=False,
                )
                inserted_quests += 1
                inserted_subquests += len(quest_summary["subquest_ids"])
                inserted_talk_refs += len(quest_summary["talk_ids"])
                inserted_dialog_nodes += sum(
                    int(detail.get("node_count", 0))
                    for detail in quest_summary["talk_details"].values()
                    if detail.get("status") == "resolved"
                )
            insertion_seconds = round(time.perf_counter() - insertion_started, 3)

            readable_summary = _insert_readables(registrar, metadata, lookup)
            subtitle_summary = _insert_subtitle(registrar, all_talk_ids)
            db.rebuild_fts(snap_id)
            db.conn.execute(
                "UPDATE source_snapshot SET status=? WHERE id=?",
                ("quarantined" if conflicts else "active", snap_id),
            )

            graph_stats = {
                "node_count": int(db.conn.execute("SELECT COUNT(*) FROM passage WHERE passage_type='dialog'").fetchone()[0]),
                "edge_count": int(db.conn.execute("SELECT COUNT(*) FROM passage_edge WHERE edge_type='next'").fetchone()[0]),
                "branch_count": int(db.conn.execute("SELECT COUNT(*) FROM (SELECT from_passage_id FROM passage_edge WHERE edge_type='next' GROUP BY from_passage_id HAVING COUNT(*)>1)").fetchone()[0]),
                "merge_count": int(db.conn.execute("SELECT COUNT(*) FROM (SELECT to_passage_id FROM passage_edge WHERE edge_type='next' GROUP BY to_passage_id HAVING COUNT(*)>1)").fetchone()[0]),
                "dangling_edge_issue_count": int(db.conn.execute("SELECT COUNT(*) FROM ingest_issue WHERE snapshot_id=? AND code='dangling_dialog_edge'", (snap_id,)).fetchone()[0]),
            }
            language_counts = {
                language: int(
                    db.conn.execute(
                        "SELECT COUNT(*) FROM passage_text WHERE snapshot_id=? AND language=?",
                        (snap_id, language),
                    ).fetchone()[0]
                )
                for language in LANGUAGES
            }
            speaker_counts = {
                "resolved": int(db.conn.execute("SELECT COUNT(*) FROM metadata_fact WHERE subject_type='passage' AND key='speaker_name' AND source_class='original'").fetchone()[0]),
                "unresolved": int(db.conn.execute("SELECT COUNT(*) FROM ingest_issue WHERE snapshot_id=? AND code IN ('unresolved_speaker_name_hash','missing_speaker_textmap_reference')", (snap_id,)).fetchone()[0]),
                "hash_zero": int(db.conn.execute("SELECT COUNT(*) FROM ingest_issue WHERE snapshot_id=? AND code='unresolved_speaker_name_hash'", (snap_id,)).fetchone()[0]),
                "textmap_missing": int(db.conn.execute("SELECT COUNT(*) FROM ingest_issue WHERE snapshot_id=? AND code='missing_speaker_textmap_reference'", (snap_id,)).fetchone()[0]),
            }
            report = db.report(snap_id)
            report.update(
                {
                    "status": db.conn.execute("SELECT status FROM source_snapshot WHERE id=?", (snap_id,)).fetchone()[0],
                    "manifest": {"source_id": manifest["source_id"], "release_label": manifest["release_label"], "build": manifest["build"], "commit": manifest["commit"], "tree": manifest["tree"]},
                    "identity": {
                        **evidence_summary,
                        "logical_talk_multiple_evidence_collision_count": int(db.conn.execute("SELECT COUNT(*) FROM logical_talk WHERE status='multiple_evidence'").fetchone()[0]),
                        "talk_evidence_parent_binding_count": int(db.conn.execute("SELECT COUNT(*) FROM passage WHERE talk_evidence_id IS NOT NULL").fetchone()[0]),
                        "dialog_stable_id_algorithm": "digest(raw_talk_evidence_id,node_id)",
                    },
                    "quest_inventory": quest_inventory,
                    "talk_discovery": talk_discovery,
                    "dialog": graph_stats,
                    "language": {"passage_text_counts": language_counts, "missing_textmap_reference_count": int(db.conn.execute("SELECT COUNT(*) FROM ingest_issue WHERE snapshot_id=? AND code='missing_textmap_reference'", (snap_id,)).fetchone()[0])},
                    "speaker": speaker_counts,
                    "source_quality": {
                        "invalid_quest_root": int(db.conn.execute("SELECT COUNT(*) FROM ingest_issue WHERE snapshot_id=? AND code='invalid_quest_root'", (snap_id,)).fetchone()[0]),
                        "invalid_talk_root": discovery_index.invalid_file_count,
                        "issues_total": int(db.conn.execute("SELECT COUNT(*) FROM ingest_issue WHERE snapshot_id=?", (snap_id,)).fetchone()[0]),
                    },
                    "textmap_counts": textmap_counts,
                    "textmap_selected_hash_count": len(textmap_hashes),
                    "readables": readable_summary,
                    "subtitle": subtitle_summary,
                    "timing": {
                        "source_scan_seconds": quest_inventory["build_seconds"],
                        "talk_inventory_seconds": evidence_summary["build_seconds"],
                        "quest_insertion_seconds": insertion_seconds,
                        "total_seconds_before_report": round(time.perf_counter() - started, 3),
                    },
                    "counts": {
                        "inserted_quests": inserted_quests,
                        "inserted_subquests": inserted_subquests,
                        "inserted_talk_references": inserted_talk_refs,
                        "inserted_dialog_nodes": inserted_dialog_nodes,
                    },
                    "fallbacks": [],
                    "unparsed_fields": [
                        {"area": "QuestDialogue/InterAction", "reason": "camera/action behavior fields intentionally outside Quest evidence ingestion"},
                    ],
                }
            )
        report["timing"]["total_seconds"] = round(time.perf_counter() - started, 3)
        report["database_size_bytes"] = db.path.stat().st_size if db.path.exists() else None
        return report
