from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable

from .db import Database
from .parsers import deob_talk_generic
from .util import canonical_json, digest_id, normalize_text, sha256_text


MISSING_NAMESPACE = "__missing_or_empty__"
PROJECTION_METADATA = '{"projection":"talk_evidence_v1"}'


@dataclass(frozen=True)
class TalkProjection:
    evidence_container_id: str
    identity_group_id: str
    occurrence_ids: tuple[str, ...]
    occurrence_edge_ids: tuple[str, ...]


def _ensure_row(
    db: Database,
    *,
    table: str,
    row_id: str,
    columns: tuple[str, ...],
    values: tuple[Any, ...],
    id_column: str = "id",
) -> None:
    selected = db.conn.execute(
        f"SELECT {', '.join(columns)} FROM {table} WHERE {id_column}=?",
        (row_id,),
    ).fetchone()
    if selected is not None:
        actual = tuple(selected[column] for column in columns)
        if actual != values:
            raise sqlite3.IntegrityError(f"{table} identity mismatch for {row_id}")
        return

    placeholders = ", ".join("?" for _ in values)
    db.conn.execute(
        f"INSERT INTO {table}({id_column}, {', '.join(columns)}) VALUES (?, {placeholders})",
        (row_id, *values),
    )


def _namespace(raw_talk: dict[str, Any], normalized_talk: dict[str, Any], explicit: str | None) -> str:
    if explicit:
        return str(explicit)
    for value in (
        raw_talk.get("namespace"),
        raw_talk.get("type"),
        raw_talk.get("ALBFHGKNMLK"),
        normalized_talk.get("namespace"),
        normalized_talk.get("type"),
        normalized_talk.get("ALBFHGKNMLK"),
    ):
        if isinstance(value, str) and value:
            return value
    return MISSING_NAMESPACE


def _raw_nodes(raw_talk: dict[str, Any], normalized_talk: dict[str, Any]) -> list[Any]:
    for key in ("dialogList", "PFALHAKIILD"):
        value = raw_talk.get(key)
        if isinstance(value, list):
            return value
        value = normalized_talk.get(key)
        if isinstance(value, list):
            return value
    return []


def _node_text(node: dict[str, Any]) -> str:
    for key in ("text", "content", "dialogText", "rawText"):
        value = node.get(key)
        if isinstance(value, str):
            return value
    return ""


def _ensure_membership(
    db: Database, identity_group_id: str, ingest_run_id: str, container_id: str
) -> None:
    db.conn.execute(
        """INSERT INTO identity_group_container(
               identity_group_id, ingest_run_id, evidence_container_id)
           VALUES (?, ?, ?)
           ON CONFLICT(identity_group_id, ingest_run_id, evidence_container_id) DO NOTHING""",
        (identity_group_id, ingest_run_id, container_id),
    )


def _ensure_occurrence_edge(
    db: Database,
    *,
    edge_id: str,
    ingest_run_id: str,
    evidence_container_id: str,
    from_occurrence_id: str,
    to_occurrence_id: str,
    edge_type: str,
) -> None:
    _ensure_row(
        db,
        table="occurrence_edge",
        row_id=edge_id,
        columns=(
            "ingest_run_id",
            "evidence_container_id",
            "from_passage_occurrence_id",
            "to_passage_occurrence_id",
            "edge_type",
            "metadata_json",
        ),
        values=(
            ingest_run_id,
            evidence_container_id,
            from_occurrence_id,
            to_occurrence_id,
            edge_type,
            "{}",
        ),
    )


def project_talk_root(
    db: Database,
    *,
    ingest_run_id: str,
    source_record_id: str,
    raw_talk: dict[str, Any],
    language: str = "CHS",
    namespace: str | None = None,
    resolved_texts: Iterable[Any] | None = None,
    allowed_external_target_ids: Iterable[str | int] | None = None,
    preserve_physical_raw_nodes: bool = False,
) -> TalkProjection:
    """Project one supplied raw Talk root into isolated evidence occurrences.

    This function consumes one already-registered source record.  It does not
    discover files, resolve logical Talk identities, or write legacy passage
    tables.
    """
    source_record = db.conn.execute(
        "SELECT raw_sha256 FROM source_record WHERE id=?",
        (source_record_id,),
    ).fetchone()
    if source_record is None:
        raise sqlite3.IntegrityError(f"source_record does not exist: {source_record_id}")

    normalized_talk = deob_talk_generic(raw_talk)
    talk_id = str(normalized_talk["talkId"])
    native_namespace = _namespace(raw_talk, normalized_talk, namespace)
    dialog_list = normalized_talk["dialogList"]
    # Frozen historical callers retain their checksum contract. Scalable v3
    # explicitly binds raw checksums to the physical (possibly obfuscated) node.
    raw_dialog_list = _raw_nodes(raw_talk, normalized_talk)
    if preserve_physical_raw_nodes:
        raw_dialog_list = next((raw_talk[key] for key in ("dialogList", "PFALHAKIILD") if isinstance(raw_talk.get(key), list)), [])
    if len(raw_dialog_list) != len(dialog_list):
        raw_dialog_list = list(dialog_list)

    # Talk occurrence edges are container-local by schema contract.  A
    # missing target is therefore an integrity failure by default.  The
    # corpus builder may pass a preflight-audited allow-list for targets that
    # are known to live in another sealed Talk container; those targets are
    # deliberately skipped here and reported by the caller rather than being
    # represented by a cross-container or ghost occurrence edge.
    allowed_external_ids = {
        str(target_id) for target_id in (allowed_external_target_ids or ())
    }
    local_node_ids = {
        str(node.get("id"))
        for node in dialog_list
        if isinstance(node, dict) and isinstance(node.get("id"), int)
        and not isinstance(node.get("id"), bool)
    }
    for node in dialog_list:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        for target in node.get("nextDialogs", []):
            if not isinstance(target, int) or isinstance(target, bool):
                continue
            target_id = str(target)
            if target_id not in local_node_ids and target_id not in allowed_external_ids:
                raise ValueError(f"missing Talk node {target_id} referenced by {node_id}")

    container_id = digest_id("ec", ingest_run_id, source_record_id)
    normalized_checksum = sha256_text(canonical_json(normalized_talk))
    _ensure_row(
        db,
        table="evidence_container",
        row_id=container_id,
        columns=(
            "ingest_run_id",
            "source_record_id",
            "kind",
            "namespace",
            "talk_id",
            "raw_checksum",
            "normalized_checksum",
            "metadata_json",
        ),
        values=(
            ingest_run_id,
            source_record_id,
            "talk_root",
            native_namespace,
            talk_id,
            source_record["raw_sha256"],
            normalized_checksum,
            PROJECTION_METADATA,
        ),
    )

    identity_group_id = digest_id("ig", ingest_run_id, native_namespace, talk_id)
    _ensure_row(
        db,
        table="identity_group",
        row_id=identity_group_id,
        columns=("ingest_run_id", "namespace", "talk_id", "metadata_json"),
        values=(ingest_run_id, native_namespace, talk_id, PROJECTION_METADATA),
    )
    _ensure_membership(db, identity_group_id, ingest_run_id, container_id)

    occurrence_ids: list[str] = []
    node_to_occurrence: dict[str, str] = {}
    node_targets: list[tuple[str, Iterable[Any]]] = []
    for index, node in enumerate(dialog_list):
        if not isinstance(node, dict) or not isinstance(node.get("id"), int) or isinstance(node["id"], bool):
            raise ValueError("Talk dialogList contains a node without an integer native ID")
        node_id = str(node["id"])
        occurrence_id = digest_id("po", ingest_run_id, container_id, node_id)
        if node_id in node_to_occurrence:
            raise ValueError(f"duplicate Talk dialog node {node_id}")
        node_to_occurrence[node_id] = occurrence_id
        occurrence_ids.append(occurrence_id)
        raw_node = raw_dialog_list[index] if isinstance(raw_dialog_list[index], dict) else node
        raw_node_checksum = sha256_text(canonical_json(raw_node))
        normalized_node_checksum = sha256_text(canonical_json(node))
        _ensure_row(
            db,
            table="passage_occurrence",
            row_id=occurrence_id,
            id_column="passage_occurrence_id",
            columns=(
                "ingest_run_id",
                "evidence_container_id",
                "source_record_id",
                "source_pointer",
                "namespace",
                "talk_id",
                "node_id",
                "sequence_key",
                "raw_checksum",
                "normalized_checksum",
                "metadata_json",
            ),
            values=(
                ingest_run_id,
                container_id,
                source_record_id,
                f"/dialogList/{index}",
                native_namespace,
                talk_id,
                node_id,
                f"{index:04d}",
                raw_node_checksum,
                normalized_node_checksum,
                "{}",
            ),
        )

        if resolved_texts is None:
            raw_text = _node_text(node)
            normalized_text = normalize_text(raw_text)
            text_id = digest_id("pot", occurrence_id, ingest_run_id, container_id, language)
            _ensure_row(
                db,
                table="passage_occurrence_text",
                row_id=text_id,
                columns=(
                    "passage_occurrence_id",
                    "ingest_run_id",
                    "evidence_container_id",
                    "language",
                    "raw_text",
                    "normalized_text",
                    "raw_checksum",
                    "normalized_checksum",
                    "source_record_id",
                    "source_start",
                    "source_end",
                ),
                values=(
                    occurrence_id,
                    ingest_run_id,
                    container_id,
                    language,
                    raw_text,
                    normalized_text,
                    sha256_text(raw_text),
                    sha256_text(normalized_text),
                    source_record_id,
                    None,
                    None,
                ),
            )
        next_dialogs = node.get("nextDialogs", [])
        if not isinstance(next_dialogs, list):
            raise ValueError(f"Talk node {node_id} nextDialogs is not an array")
        node_targets.append((node_id, next_dialogs))

    if resolved_texts is not None:
        for rt in resolved_texts:
            status = getattr(rt, "status", None)
            raw_text = getattr(rt, "raw_text", "")
            if status in ("unique_main", "unique_medium", "both_equal") and raw_text:
                target_node_id = str(getattr(rt, "node_id"))
                occ_id = node_to_occurrence.get(target_node_id)
                if occ_id is None:
                    continue
                rt_source_record_id = getattr(rt, "source_record_id", None)
                if not rt_source_record_id:
                    raise sqlite3.IntegrityError(
                        f"missing source_record_id for resolved text {target_node_id}:{getattr(rt, 'language')}"
                    )
                rt_language = str(getattr(rt, "language"))
                rt_normalized = str(getattr(rt, "normalized_text", normalize_text(raw_text)))
                text_id = digest_id("pot", occ_id, ingest_run_id, container_id, rt_language)
                _ensure_row(
                    db,
                    table="passage_occurrence_text",
                    row_id=text_id,
                    columns=(
                        "passage_occurrence_id",
                        "ingest_run_id",
                        "evidence_container_id",
                        "language",
                        "raw_text",
                        "normalized_text",
                        "raw_checksum",
                        "normalized_checksum",
                        "source_record_id",
                        "source_start",
                        "source_end",
                    ),
                    values=(
                        occ_id,
                        ingest_run_id,
                        container_id,
                        rt_language,
                        raw_text,
                        rt_normalized,
                        sha256_text(raw_text),
                        sha256_text(rt_normalized),
                        rt_source_record_id,
                        None,
                        None,
                    ),
                )

    edge_ids: list[str] = []
    for node_id, next_dialogs in node_targets:
        for target in next_dialogs:
            if not isinstance(target, int) or isinstance(target, bool):
                continue
            target_id = str(target)
            if target_id not in node_to_occurrence:
                if target_id in allowed_external_ids:
                    continue
                raise ValueError(f"missing Talk node {target_id} referenced by {node_id}")
            edge_id = digest_id(
                "oe",
                ingest_run_id,
                container_id,
                node_to_occurrence[node_id],
                node_to_occurrence[target_id],
                "next",
            )
            _ensure_occurrence_edge(
                db,
                edge_id=edge_id,
                ingest_run_id=ingest_run_id,
                evidence_container_id=container_id,
                from_occurrence_id=node_to_occurrence[node_id],
                to_occurrence_id=node_to_occurrence[target_id],
                edge_type="next",
            )
            edge_ids.append(edge_id)

    return TalkProjection(
        evidence_container_id=container_id,
        identity_group_id=identity_group_id,
        occurrence_ids=tuple(occurrence_ids),
        occurrence_edge_ids=tuple(edge_ids),
    )
