from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .db import Database
from .util import text_occurrences


@dataclass(frozen=True)
class NotFound:
    """A cardinality-aware lookup with no matching Talk occurrence."""

    lookup: tuple[str, str, str, str]


@dataclass(frozen=True)
class AmbiguousLookup:
    """A cardinality-aware lookup with more than one occurrence candidate."""

    lookup: tuple[str, str, str, str]
    candidates: tuple[dict[str, Any], ...]

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        return tuple(str(candidate["passage_occurrence_id"]) for candidate in self.candidates)


def active_snapshot_id(db: Database) -> str:
    row = db.conn.execute("SELECT id FROM source_snapshot WHERE status='active' ORDER BY observed_at DESC LIMIT 1").fetchone()
    if row is None:
        raise ValueError("database has no active snapshot")
    return str(row[0])


def _json(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(value)


def _source(db: Database, source_record_id: str | None) -> dict[str, Any] | None:
    if not source_record_id:
        return None
    rows = db.conn.execute(
        """SELECT sr.id, sr.pointer, sr.record_kind, sr.native_id, sr.raw_sha256,
                  sf.relative_path, sf.sha256 AS file_sha256, sf.root_path,
                  ss.id AS snapshot_id, ss.release_label, ss.build, ss."commit", ss.tree,
                  sc.kind AS source_kind
           FROM source_record sr JOIN source_file sf ON sf.id=sr.source_file_id
           JOIN source_snapshot ss ON ss.id=sf.snapshot_id
           JOIN source_catalog sc ON sc.id=ss.source_id WHERE sr.id=?""",
        (source_record_id,),
    ).fetchall()
    if not rows:
        return None
    return dict(rows[0])


def _talk_evidence(db: Database, evidence_id: str | None) -> dict[str, Any] | None:
    if not evidence_id:
        return None
    row = db.conn.execute(
        """SELECT te.id, te.snapshot_id, te.source_file_id, te.source_record_id,
                  te.source_pointer, te.native_namespace, te.native_id,
                  te.raw_sha256, te.normalized_checksum, te.status,
                  te.metadata_json, sf.relative_path, sf.sha256 AS file_sha256
           FROM talk_evidence te JOIN source_file sf ON sf.id=te.source_file_id
           WHERE te.id=?""",
        (evidence_id,),
    ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["metadata"] = _json(item.pop("metadata_json"), {})
    logicals = db.conn.execute(
        """SELECT lte.logical_talk_id, lte.relation_status, lte.method,
                  lt.native_namespace, lt.native_id, lt.status
           FROM logical_talk_evidence lte JOIN logical_talk lt
             ON lt.id=lte.logical_talk_id
           WHERE lte.evidence_id=? ORDER BY lte.logical_talk_id""",
        (evidence_id,),
    ).fetchall()
    item["logical_talks"] = [dict(logical) for logical in logicals]
    return item


def _speaker_names(db: Database, passage_id: str) -> dict[str, str]:
    names: dict[str, str] = {}
    for row in db.conn.execute(
        "SELECT language, value_json FROM metadata_fact WHERE subject_type='passage' AND subject_id=? AND key='speaker_name'",
        (passage_id,),
    ).fetchall():
        value = _json(row["value_json"], {})
        if row["language"] and isinstance(value, dict) and isinstance(value.get("text"), str):
            names[str(row["language"])] = value["text"]
    return names


def _parents(db: Database, document_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    current = document_id
    seen = {current}
    for _ in range(12):
        row = db.conn.execute(
            """SELECT dr.to_document_id, dr.relation_type, dr.source_class, dr.method,
                      d.content_type, d.native_id, d.status
               FROM document_relation dr JOIN document d ON d.stable_id=dr.to_document_id
               WHERE dr.from_document_id=? ORDER BY dr.relation_type, dr.to_document_id LIMIT 1""",
            (current,),
        ).fetchone()
        if row is None or row["to_document_id"] in seen:
            break
        result.append(dict(row))
        current = row["to_document_id"]
        seen.add(current)
    return result


def exact_search(
    db: Database,
    query: str,
    snapshot_id: str | None = None,
    include_placeholders: bool = False,
    language: str | None = None,
) -> list[dict[str, Any]]:
    """Return literal substring matches without collapsing evidence rows.

    ``language`` is intentionally an explicit filter.  With no filter the
    result spans every imported language; this is why a multilingual query can
    legitimately return more rows than the CHS-only gold count.
    """
    snapshot_id = snapshot_id or active_snapshot_id(db)
    passage_language_sql = ""
    orphan_language_sql = ""
    passage_args: list[Any] = [snapshot_id, query]
    orphan_args: list[Any] = [snapshot_id, query]
    if language:
        passage_language_sql = " AND pt.language=?"
        orphan_language_sql = " AND lt.language=?"
        passage_args.append(language)
        orphan_args.append(language)
    passage_rows = db.conn.execute(
        f"""SELECT pt.id AS evidence_id, pt.passage_id, pt.language, pt.raw_text,
                  pt.normalized_text, pt.raw_checksum, pt.normalized_checksum,
                  pt.localized_text_id, p.document_id, p.passage_type,
                  p.talk_evidence_id,
                  d.content_type AS document_type, d.status AS document_status,
                  pv.sequence_key, pv.speaker_json,
                  sr.id AS source_record_id, sr.pointer, sr.raw_sha256,
                  sf.relative_path, sf.sha256 AS file_sha256
           FROM passage_text pt
           JOIN passage p ON p.stable_id=pt.passage_id
           JOIN document d ON d.stable_id=p.document_id
           LEFT JOIN passage_version pv ON pv.passage_id=p.stable_id AND pv.snapshot_id=pt.snapshot_id
           JOIN source_record sr ON sr.id=pt.source_record_id
           JOIN source_file sf ON sf.id=sr.source_file_id
           WHERE pt.snapshot_id=? AND instr(pt.raw_text, ?) > 0{passage_language_sql}
           ORDER BY pt.language, p.stable_id""",
        passage_args,
    ).fetchall()
    orphan_rows = db.conn.execute(
        f"""SELECT lt.id AS evidence_id, lt.language, lt.raw_text, lt.normalized_text,
                  lt.raw_checksum, lt.normalized_checksum, lt.textmap_hash,
                  lt.source_record_id, sr.pointer, sr.raw_sha256,
                  sf.relative_path, sf.sha256 AS file_sha256
           FROM localized_text lt
           JOIN source_record sr ON sr.id=lt.source_record_id
           JOIN source_file sf ON sf.id=sr.source_file_id
           WHERE lt.snapshot_id=? AND instr(lt.raw_text, ?) > 0{orphan_language_sql}
             AND NOT EXISTS (SELECT 1 FROM passage_text pt WHERE pt.localized_text_id=lt.id)
           ORDER BY lt.language, lt.textmap_hash, lt.shard""",
        orphan_args,
    ).fetchall()
    results: list[dict[str, Any]] = []
    for row in passage_rows:
        if not include_placeholders and row["document_status"] == "placeholder":
            continue
        item = dict(row)
        item["evidence_type"] = "passage"
        item["matches"] = [{"start": start, "end": end} for start, end in text_occurrences(row["raw_text"], query)]
        item["attribution_status"] = "linked"
        item["source_class"] = "original"
        item["speaker"] = _json(row["speaker_json"])
        item["speaker_names"] = _speaker_names(db, row["passage_id"])
        item["provenance"] = _source(db, row["source_record_id"])
        item["talk_evidence"] = _talk_evidence(db, row["talk_evidence_id"])
        item["parents"] = _parents(db, row["document_id"])
        results.append(item)
    for row in orphan_rows:
        item = dict(row)
        item["evidence_type"] = "localized_text"
        item["passage_id"] = None
        item["document_id"] = None
        item["matches"] = [{"start": start, "end": end} for start, end in text_occurrences(row["raw_text"], query)]
        item["attribution_status"] = "orphan"
        item["source_class"] = "original"
        item["provenance"] = _source(db, row["source_record_id"])
        results.append(item)
    return results


def search_text(db: Database, query: str, snapshot_id: str | None = None, language: str | None = None) -> list[dict[str, Any]]:
    # Phase 1 keeps the lexical contract deterministic and substring-verified.
    return exact_search(db, query, snapshot_id, language=language)


def get_passage(db: Database, passage_id: str, snapshot_id: str | None = None) -> dict[str, Any]:
    snapshot_id = snapshot_id or active_snapshot_id(db)
    row = db.conn.execute(
        """SELECT p.stable_id, p.document_id, p.passage_type, p.native_namespace,
                  p.native_id, p.identity_algorithm, p.talk_evidence_id,
                  d.content_type AS document_type,
                  d.native_id AS document_native_id, d.status AS document_status,
                  pv.sequence_key, pv.speaker_json, pv.start_ms, pv.end_ms,
                  pv.raw_checksum AS passage_raw_checksum,
                  pv.normalized_checksum AS passage_normalized_checksum,
                  pv.source_record_id AS passage_source_record_id,
                  pv.talk_evidence_id AS passage_version_talk_evidence_id
           FROM passage p JOIN document d ON d.stable_id=p.document_id
           JOIN passage_version pv ON pv.passage_id=p.stable_id AND pv.snapshot_id=?
           WHERE p.stable_id=?""",
        (snapshot_id, passage_id),
    ).fetchone()
    if row is None:
        raise KeyError(f"passage not found: {passage_id}")
    result = dict(row)
    result["speaker"] = _json(result.pop("speaker_json"))
    result["passage_provenance"] = _source(db, result.pop("passage_source_record_id"))
    result["talk_evidence"] = _talk_evidence(db, result.get("passage_version_talk_evidence_id") or result.get("talk_evidence_id"))
    texts = db.conn.execute(
        """SELECT pt.language, pt.raw_text, pt.normalized_text, pt.raw_checksum,
                  pt.normalized_checksum, pt.localized_text_id, pt.source_record_id,
                  lt.textmap_hash, lt.shard
           FROM passage_text pt LEFT JOIN localized_text lt ON lt.id=pt.localized_text_id
           WHERE pt.passage_id=? AND pt.snapshot_id=? ORDER BY pt.language""",
        (passage_id, snapshot_id),
    ).fetchall()
    result["texts"] = []
    for text in texts:
        item = dict(text)
        item["source_class"] = "original"
        item["provenance"] = _source(db, text["source_record_id"])
        result["texts"].append(item)
    result["facts"] = [
        {**dict(fact), "value": _json(fact["value_json"]), "provenance": _source(db, fact["source_record_id"])}
        for fact in db.conn.execute("SELECT * FROM metadata_fact WHERE subject_type='passage' AND subject_id=? ORDER BY key, language", (passage_id,)).fetchall()
    ]
    result["speaker_names"] = {
        fact["language"]: fact["value"]["text"]
        for fact in result["facts"]
        if fact["key"] == "speaker_name" and fact["language"] and isinstance(fact["value"], dict) and isinstance(fact["value"].get("text"), str)
    }
    result["edges_out"] = []
    for edge in db.conn.execute("SELECT * FROM passage_edge WHERE from_passage_id=? ORDER BY edge_type, to_passage_id", (passage_id,)).fetchall():
        item = dict(edge)
        item["provenance"] = _source(db, edge["source_record_id"])
        result["edges_out"].append(item)
    result["edges_in"] = []
    for edge in db.conn.execute("SELECT * FROM passage_edge WHERE to_passage_id=? ORDER BY edge_type, from_passage_id", (passage_id,)).fetchall():
        item = dict(edge)
        item["provenance"] = _source(db, edge["source_record_id"])
        result["edges_in"].append(item)
    result["parents"] = _parents(db, result["document_id"])
    return result


_OCCURRENCE_SELECT = """
    SELECT po.passage_occurrence_id, po.ingest_run_id,
           po.evidence_container_id, po.source_record_id,
           po.source_pointer, po.namespace, po.talk_id, po.node_id,
           po.sequence_key, po.raw_checksum, po.normalized_checksum,
           po.metadata_json AS occurrence_metadata_json,
           ir.parser_build_id, ir.scope_json AS run_scope_json,
           ir.config_json AS run_config_json, ir.status AS run_status,
           ir.created_at AS run_created_at,
           ss.id AS snapshot_id, ss.source_id AS snapshot_source_id,
           ss.release_label AS snapshot_release_label,
           ss.build AS snapshot_build, ss."commit" AS snapshot_commit,
           ss.tree AS snapshot_tree, ss.observed_at AS snapshot_observed_at,
           ss.parser_version AS snapshot_parser_version,
           ss.mapping_version AS snapshot_mapping_version,
           ss.status AS snapshot_status,
           ss.metadata_json AS snapshot_metadata_json,
           ec.kind AS container_kind, ec.namespace AS container_namespace,
           ec.talk_id AS container_talk_id,
           ec.raw_checksum AS container_raw_checksum,
           ec.normalized_checksum AS container_normalized_checksum,
           ec.metadata_json AS container_metadata_json
      FROM passage_occurrence po
      JOIN ingest_run ir
        ON ir.id=po.ingest_run_id
      JOIN source_snapshot ss
        ON ss.id=ir.snapshot_id
      JOIN evidence_container ec
        ON ec.id=po.evidence_container_id
       AND ec.ingest_run_id=po.ingest_run_id
"""


def _occurrence_rows(
    db: Database,
    where_sql: str,
    args: tuple[Any, ...],
) -> list[Any]:
    return db.conn.execute(
        _OCCURRENCE_SELECT
        + where_sql
        + " ORDER BY po.passage_occurrence_id",
        args,
    ).fetchall()


def _occurrence_scope(db: Database, row: Any) -> dict[str, Any]:
    return {
        "run": {
            "id": row["ingest_run_id"],
            "snapshot_id": row["snapshot_id"],
            "parser_build_id": row["parser_build_id"],
            "scope": _json(row["run_scope_json"], {}),
            "config": _json(row["run_config_json"], {}),
            "status": row["run_status"],
            "created_at": row["run_created_at"],
        },
        "snapshot": {
            "id": row["snapshot_id"],
            "source_id": row["snapshot_source_id"],
            "release_label": row["snapshot_release_label"],
            "build": row["snapshot_build"],
            "commit": row["snapshot_commit"],
            "tree": row["snapshot_tree"],
            "observed_at": row["snapshot_observed_at"],
            "parser_version": row["snapshot_parser_version"],
            "mapping_version": row["snapshot_mapping_version"],
            "status": row["snapshot_status"],
            "metadata": _json(row["snapshot_metadata_json"], {}),
        },
        "container": {
            "id": row["evidence_container_id"],
            "ingest_run_id": row["ingest_run_id"],
            "source_record_id": row["source_record_id"],
            "kind": row["container_kind"],
            "namespace": row["container_namespace"],
            "talk_id": row["container_talk_id"],
            "raw_checksum": row["container_raw_checksum"],
            "normalized_checksum": row["container_normalized_checksum"],
            "metadata": _json(row["container_metadata_json"], {}),
        },
        "source_record": _source(db, row["source_record_id"]),
    }


def _occurrence_texts(db: Database, row: Any) -> list[dict[str, Any]]:
    text_rows = db.conn.execute(
        """SELECT id, passage_occurrence_id, ingest_run_id,
                  evidence_container_id, language, raw_text, normalized_text,
                  raw_checksum, normalized_checksum, source_record_id,
                  source_start, source_end
           FROM passage_occurrence_text
           WHERE passage_occurrence_id=?
             AND ingest_run_id=?
             AND evidence_container_id=?
           ORDER BY language, id""",
        (
            row["passage_occurrence_id"],
            row["ingest_run_id"],
            row["evidence_container_id"],
        ),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for text_row in text_rows:
        item = dict(text_row)
        item["provenance"] = _source(db, text_row["source_record_id"])
        result.append(item)
    return result


def _occurrence_item(
    db: Database,
    row: Any,
    *,
    include_scope: bool,
) -> dict[str, Any]:
    item = {
        "passage_occurrence_id": row["passage_occurrence_id"],
        "ingest_run_id": row["ingest_run_id"],
        "snapshot_id": row["snapshot_id"],
        "evidence_container_id": row["evidence_container_id"],
        "source_record_id": row["source_record_id"],
        "source_pointer": row["source_pointer"],
        "namespace": row["namespace"],
        "talk_id": row["talk_id"],
        "node_id": row["node_id"],
        "sequence_key": row["sequence_key"],
        "raw_checksum": row["raw_checksum"],
        "normalized_checksum": row["normalized_checksum"],
        "metadata": _json(row["occurrence_metadata_json"], {}),
        "texts": _occurrence_texts(db, row),
    }
    if include_scope:
        item["scope"] = _occurrence_scope(db, row)
    return item


def _occurrence_candidate(row: Any) -> dict[str, Any]:
    return {
        "passage_occurrence_id": row["passage_occurrence_id"],
        "ingest_run_id": row["ingest_run_id"],
        "snapshot_id": row["snapshot_id"],
        "evidence_container_id": row["evidence_container_id"],
        "source_record_id": row["source_record_id"],
        "source_pointer": row["source_pointer"],
        "namespace": row["namespace"],
        "talk_id": row["talk_id"],
        "node_id": row["node_id"],
        "sequence_key": row["sequence_key"],
        "raw_checksum": row["raw_checksum"],
        "normalized_checksum": row["normalized_checksum"],
    }


def get_passage_occurrence(
    db: Database,
    passage_occurrence_id: str,
) -> dict[str, Any]:
    """Return one concrete occurrence and its preserved evidence lineage."""
    rows = _occurrence_rows(
        db,
        " WHERE po.passage_occurrence_id=?",
        (str(passage_occurrence_id),),
    )
    if not rows:
        raise KeyError(f"passage occurrence not found: {passage_occurrence_id}")
    if len(rows) != 1:
        raise RuntimeError(f"passage occurrence identity is not unique: {passage_occurrence_id}")
    return _occurrence_item(db, rows[0], include_scope=True)


def find_dialog_occurrence(
    db: Database,
    ingest_run_id: str,
    namespace: str,
    talk_id: int | str,
    node_id: int | str,
) -> dict[str, Any] | NotFound | AmbiguousLookup:
    """Resolve a Talk native triple without selecting an evidence occurrence."""
    lookup = (
        str(ingest_run_id),
        str(namespace),
        str(talk_id),
        str(node_id),
    )
    rows = _occurrence_rows(
        db,
        """ WHERE po.ingest_run_id=?
              AND po.namespace=?
              AND po.talk_id=?
              AND po.node_id=?""",
        lookup,
    )
    if not rows:
        return NotFound(lookup)
    if len(rows) == 1:
        return _occurrence_item(db, rows[0], include_scope=True)
    return AmbiguousLookup(
        lookup,
        tuple(_occurrence_candidate(row) for row in rows),
    )


def _occurrence_context(
    db: Database,
    occurrence_row: Any,
    before: int,
    after: int,
) -> dict[str, Any]:
    occurrence = _occurrence_item(db, occurrence_row, include_scope=True)
    sibling_rows = _occurrence_rows(
        db,
        """ WHERE po.ingest_run_id=?
              AND po.evidence_container_id=?""",
        (
            occurrence_row["ingest_run_id"],
            occurrence_row["evidence_container_id"],
        ),
    )
    position = next(
        (
            index
            for index, row in enumerate(sibling_rows)
            if row["passage_occurrence_id"] == occurrence_row["passage_occurrence_id"]
        ),
        None,
    )
    if position is None:
        raise KeyError(
            f"passage occurrence left its scope: {occurrence_row['passage_occurrence_id']}"
        )
    start = max(0, position - before)
    end = min(len(sibling_rows), position + after + 1)
    window = [
        _occurrence_item(db, row, include_scope=False)
        for row in sibling_rows[start:end]
    ]

    edge_rows = db.conn.execute(
        """SELECT id, ingest_run_id, evidence_container_id,
                  from_passage_occurrence_id, to_passage_occurrence_id,
                  edge_type, metadata_json
           FROM occurrence_edge
           WHERE ingest_run_id=? AND evidence_container_id=?
           ORDER BY from_passage_occurrence_id, to_passage_occurrence_id, id""",
        (
            occurrence_row["ingest_run_id"],
            occurrence_row["evidence_container_id"],
        ),
    ).fetchall()
    edges: list[dict[str, Any]] = []
    for edge_row in edge_rows:
        edge = dict(edge_row)
        edge["metadata"] = _json(edge.pop("metadata_json"), {})
        edges.append(edge)

    outdegree: dict[str, int] = {}
    indegree: dict[str, int] = {}
    for edge in edges:
        if edge["edge_type"] != "next":
            continue
        source = str(edge["from_passage_occurrence_id"])
        target = str(edge["to_passage_occurrence_id"])
        outdegree[source] = outdegree.get(source, 0) + 1
        indegree[target] = indegree.get(target, 0) + 1

    sibling_by_id = {
        row["passage_occurrence_id"]: _occurrence_item(
            db,
            row,
            include_scope=False,
        )
        for row in sibling_rows
    }
    occurrence_id = occurrence_row["passage_occurrence_id"]
    graph_neighbor_ids = {
        edge["to_passage_occurrence_id"]
        if edge["from_passage_occurrence_id"] == occurrence_id
        else edge["from_passage_occurrence_id"]
        for edge in edges
        if edge["edge_type"] == "next"
        and (
            edge["from_passage_occurrence_id"] == occurrence_id
            or edge["to_passage_occurrence_id"] == occurrence_id
        )
    }
    branch_targets = {
        node: sorted(
            edge["to_passage_occurrence_id"]
            for edge in edges
            if edge["edge_type"] == "next"
            and edge["from_passage_occurrence_id"] == node
        )
        for node, count in outdegree.items()
        if count > 1
    }
    merge_sources = {
        node: sorted(
            edge["from_passage_occurrence_id"]
            for edge in edges
            if edge["edge_type"] == "next"
            and edge["to_passage_occurrence_id"] == node
        )
        for node, count in indegree.items()
        if count > 1
    }
    return {
        "scope": occurrence["scope"],
        "occurrence": occurrence,
        "window": window,
        "graph_neighbors": [
            sibling_by_id[node]
            for node in sorted(graph_neighbor_ids)
            if node in sibling_by_id
        ],
        "graph_edges": edges,
        "branch_nodes": sorted(
            node for node, count in outdegree.items() if count > 1
        ),
        "merge_nodes": sorted(
            node for node, count in indegree.items() if count > 1
        ),
        "branch_targets": branch_targets,
        "merge_sources": merge_sources,
    }


def find_dialog_passage(
    db: Database,
    namespace: str,
    talk_id: int | str,
    node_id: int | str,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    """Resolve the snapshot-local (namespace, talk_id, node_id) alternate key."""
    snapshot_id = snapshot_id or active_snapshot_id(db)
    row = db.conn.execute(
        """SELECT p.stable_id
           FROM passage p JOIN talk_evidence te ON te.id=p.talk_evidence_id
           JOIN passage_version pv ON pv.passage_id=p.stable_id AND pv.snapshot_id=?
           WHERE te.snapshot_id=? AND te.native_namespace=? AND te.native_id=?
             AND p.native_id=? AND p.passage_type='dialog'""",
        (snapshot_id, snapshot_id, str(namespace), str(talk_id), str(node_id)),
    ).fetchone()
    if row is None:
        raise KeyError(f"dialog not found: {(namespace, talk_id, node_id)}")
    return get_passage(db, str(row[0]), snapshot_id)


def _get_legacy_context(db: Database, passage_id: str, before: int = 2, after: int = 2, snapshot_id: str | None = None) -> dict[str, Any]:
    snapshot_id = snapshot_id or active_snapshot_id(db)
    passage = get_passage(db, passage_id, snapshot_id)
    siblings = db.conn.execute(
        """SELECT p.stable_id, pv.sequence_key, pt.language, pt.raw_text,
                  pt.source_record_id, pt.raw_checksum, pt.normalized_checksum,
                  p.passage_type, p.document_id
           FROM passage p JOIN passage_version pv ON pv.passage_id=p.stable_id AND pv.snapshot_id=?
           LEFT JOIN passage_text pt ON pt.passage_id=p.stable_id AND pt.snapshot_id=? AND pt.language='CHS'
           WHERE p.document_id=? ORDER BY pv.sequence_key, p.stable_id""",
        (snapshot_id, snapshot_id, passage["document_id"]),
    ).fetchall()
    position = next((i for i, row in enumerate(siblings) if row["stable_id"] == passage_id), 0)
    start = max(0, position - before)
    end = min(len(siblings), position + after + 1)
    context_rows = []
    for row in siblings[start:end]:
        item = dict(row)
        item["provenance"] = _source(db, row["source_record_id"])
        item["source_class"] = "original"
        context_rows.append(item)
    edges = [
        {**dict(row), "provenance": _source(db, row["source_record_id"])}
        for row in db.conn.execute(
            """SELECT pe.* FROM passage_edge pe
               JOIN passage p1 ON p1.stable_id=pe.from_passage_id
               JOIN passage p2 ON p2.stable_id=pe.to_passage_id
               WHERE p1.document_id=? AND p2.document_id=?
               ORDER BY pe.from_passage_id, pe.to_passage_id""",
            (passage["document_id"], passage["document_id"]),
        ).fetchall()
    ]
    outdegree: dict[str, int] = {}
    indegree: dict[str, int] = {}
    for edge in edges:
        if edge["edge_type"] != "next":
            continue
        outdegree[edge["from_passage_id"]] = outdegree.get(edge["from_passage_id"], 0) + 1
        indegree[edge["to_passage_id"]] = indegree.get(edge["to_passage_id"], 0) + 1
    sibling_by_id = {}
    for row in siblings:
        item = dict(row)
        item["provenance"] = _source(db, row["source_record_id"])
        item["source_class"] = "original"
        sibling_by_id[row["stable_id"]] = item
    graph_neighbor_ids = {
        edge["to_passage_id"] if edge["from_passage_id"] == passage_id else edge["from_passage_id"]
        for edge in edges
        if edge["edge_type"] == "next" and (edge["from_passage_id"] == passage_id or edge["to_passage_id"] == passage_id)
    }
    branch_targets = {
        node: sorted(edge["to_passage_id"] for edge in edges if edge["edge_type"] == "next" and edge["from_passage_id"] == node)
        for node, count in outdegree.items()
        if count > 1
    }
    merge_sources = {
        node: sorted(edge["from_passage_id"] for edge in edges if edge["edge_type"] == "next" and edge["to_passage_id"] == node)
        for node, count in indegree.items()
        if count > 1
    }
    return {
        "passage": passage,
        "document_id": passage["document_id"],
        "parents": passage["parents"],
        "window": context_rows,
        "graph_neighbors": [sibling_by_id[node] for node in sorted(graph_neighbor_ids) if node in sibling_by_id],
        "graph_edges": edges,
        "branch_nodes": sorted(node for node, count in outdegree.items() if count > 1),
        "merge_nodes": sorted(node for node, count in indegree.items() if count > 1),
        "branch_targets": branch_targets,
        "merge_sources": merge_sources,
    }


def get_context(
    db: Database,
    passage_occurrence_id: str,
    before: int = 2,
    after: int = 2,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    """Return occurrence-scoped context, retaining legacy non-Talk compatibility."""
    rows = _occurrence_rows(
        db,
        " WHERE po.passage_occurrence_id=?",
        (str(passage_occurrence_id),),
    )
    if rows:
        if len(rows) != 1:
            raise RuntimeError(
                f"passage occurrence identity is not unique: {passage_occurrence_id}"
            )
        occurrence_row = rows[0]
        if snapshot_id is not None and str(snapshot_id) != str(occurrence_row["snapshot_id"]):
            raise ValueError(
                f"snapshot does not match occurrence: {snapshot_id} != {occurrence_row['snapshot_id']}"
            )
        return _occurrence_context(db, occurrence_row, before, after)
    return _get_legacy_context(
        db,
        passage_occurrence_id,
        before=before,
        after=after,
        snapshot_id=snapshot_id,
    )


def get_document(db: Database, document_id: str, include_passages: bool = True, snapshot_id: str | None = None) -> dict[str, Any]:
    snapshot_id = snapshot_id or active_snapshot_id(db)
    row = db.conn.execute("SELECT * FROM document WHERE stable_id=?", (document_id,)).fetchone()
    if row is None:
        raise KeyError(f"document not found: {document_id}")
    result: dict[str, Any] = dict(row)
    result["version"] = None
    version = db.conn.execute("SELECT * FROM document_version WHERE document_id=? AND snapshot_id=?", (document_id, snapshot_id)).fetchone()
    if version:
        result["version"] = {**dict(version), "metadata": _json(version["metadata_json"]), "provenance": _source(db, version["source_record_id"])}
    result["facts"] = [
        {**dict(fact), "value": _json(fact["value_json"]), "provenance": _source(db, fact["source_record_id"])}
        for fact in db.conn.execute("SELECT * FROM metadata_fact WHERE subject_type='document' AND subject_id=? ORDER BY key, language", (document_id,)).fetchall()
    ]
    result["children"] = [dict(child) for child in db.conn.execute("SELECT dr.*, d.content_type, d.native_id, d.status FROM document_relation dr JOIN document d ON d.stable_id=dr.from_document_id WHERE dr.to_document_id=? ORDER BY d.content_type, d.native_id", (document_id,)).fetchall()]
    result["parents"] = _parents(db, document_id)
    if row["content_type"] == "talk":
        logical = db.conn.execute(
            """SELECT lt.id, lt.native_namespace, lt.native_id, lt.status, lt.metadata_json
               FROM logical_talk lt WHERE lt.id=?""",
            (document_id,),
        ).fetchone()
        if logical is not None:
            result["logical_talk"] = {
                "id": logical["id"],
                "native_namespace": logical["native_namespace"],
                "native_id": logical["native_id"],
                "status": logical["status"],
                "metadata": _json(logical["metadata_json"], {}),
            }
            result["evidence"] = [
                {
                    **(_talk_evidence(db, evidence["evidence_id"]) or {}),
                    "relation_status": evidence["relation_status"],
                    "method": evidence["method"],
                    "confidence": evidence["confidence"],
                }
                for evidence in db.conn.execute(
                    """SELECT evidence_id, relation_status, method, confidence
                       FROM logical_talk_evidence
                       WHERE logical_talk_id=? ORDER BY evidence_id""",
                    (document_id,),
                ).fetchall()
            ]
    if include_passages:
        result["passages"] = [
            {
                "stable_id": row["stable_id"],
                "passage_type": row["passage_type"],
                "native_id": row["native_id"],
                "talk_evidence_id": row["talk_evidence_id"],
                "sequence_key": row["sequence_key"],
                "texts": [
                    {
                        **dict(text),
                        "source_class": "original",
                        "provenance": _source(db, text["source_record_id"]),
                    }
                    for text in db.conn.execute(
                        "SELECT language, raw_text, raw_checksum, normalized_checksum, localized_text_id, source_record_id FROM passage_text WHERE passage_id=? AND snapshot_id=? ORDER BY language",
                        (row["stable_id"], snapshot_id),
                    ).fetchall()
                ],
            }
            for row in db.conn.execute("SELECT p.*, pv.sequence_key FROM passage p JOIN passage_version pv ON pv.passage_id=p.stable_id AND pv.snapshot_id=? WHERE p.document_id=? ORDER BY pv.sequence_key, p.stable_id", (snapshot_id, document_id)).fetchall()
        ]
    return result


def get_quest_traversal(db: Database, quest_id: str | int, snapshot_id: str | None = None) -> dict[str, Any]:
    """Return an evidence traversal for a Quest without simulating gameplay.

    The native Quest record currently gives us two independently evidenced
    child relations (``stage_of`` and ``talk_of``).  We expose both sets and
    then traverse each resolved Talk's native dialog edges.  No inferred
    subquest-to-talk edge is invented when the raw Quest does not contain one.
    Every dialog walk uses a visited set, so malformed/cyclic raw graphs cannot
    cause an infinite retrieval loop.
    """

    snapshot_id = snapshot_id or active_snapshot_id(db)
    native_quest_id = str(quest_id)
    evidence_document_id = native_quest_id if native_quest_id.startswith("agd:quest:") else f"agd:quest:{native_quest_id}"
    evidence_link = db.conn.execute(
        """SELECT 1
           FROM document_evidence de
           JOIN ingest_run ir ON ir.id=de.ingest_run_id
           WHERE de.document_id=? AND ir.snapshot_id=?
           LIMIT 1""",
        (evidence_document_id, snapshot_id),
    ).fetchone()
    if evidence_link is not None:
        from .quest_retrieval import get_quest_evidence_traversal

        return get_quest_evidence_traversal(db, evidence_document_id, snapshot_id)
    native_quest_id = str(quest_id)
    document_id = native_quest_id if native_quest_id.startswith("agd:quest:") else f"agd:quest:{native_quest_id}"
    quest = get_document(db, document_id, include_passages=False, snapshot_id=snapshot_id)
    children = db.conn.execute(
        """SELECT dr.*, d.content_type, d.native_id, d.status
           FROM document_relation dr JOIN document d ON d.stable_id=dr.from_document_id
           WHERE dr.to_document_id=? ORDER BY dr.relation_type, CAST(d.native_id AS INTEGER), d.stable_id""",
        (document_id,),
    ).fetchall()
    stages: list[dict[str, Any]] = []
    talks: list[dict[str, Any]] = []
    for row in children:
        item = dict(row)
        item["provenance"] = _source(db, row["source_record_id"])
        if row["relation_type"] == "stage_of":
            condition_rows = db.conn.execute(
                """SELECT dr.*, d.content_type, d.native_id, d.status
                   FROM document_relation dr JOIN document d ON d.stable_id=dr.to_document_id
                   WHERE dr.from_document_id=? AND dr.relation_type='subquest_talk_condition'
                   ORDER BY CAST(d.native_id AS INTEGER), d.stable_id""",
                (row["from_document_id"],),
            ).fetchall()
            item["talk_conditions"] = []
            for condition in condition_rows:
                condition_item = dict(condition)
                condition_item["provenance"] = _source(db, condition["source_record_id"])
                item["talk_conditions"].append(condition_item)
            stages.append(item)
        elif row["relation_type"] == "talk_of":
            talk_doc = get_document(db, row["from_document_id"], include_passages=False, snapshot_id=snapshot_id)
            talk_doc["relation"] = item
            passages = db.conn.execute(
                """SELECT p.stable_id, p.native_id, pv.sequence_key, pv.status
                   FROM passage p JOIN passage_version pv ON pv.passage_id=p.stable_id AND pv.snapshot_id=?
                   WHERE p.document_id=? ORDER BY pv.sequence_key, p.stable_id""",
                (snapshot_id, row["from_document_id"]),
            ).fetchall()
            node_rows = [get_passage(db, passage["stable_id"], snapshot_id=snapshot_id) for passage in passages]
            node_ids = {row["stable_id"] for row in passages}
            edges = [
                {**dict(edge), "provenance": _source(db, edge["source_record_id"])}
                for edge in db.conn.execute(
                    """SELECT * FROM passage_edge
                       WHERE from_passage_id IN (SELECT stable_id FROM passage WHERE document_id=?)
                         AND to_passage_id IN (SELECT stable_id FROM passage WHERE document_id=?)
                       ORDER BY from_passage_id, to_passage_id""",
                    (row["from_document_id"], row["from_document_id"]),
                ).fetchall()
            ]
            adjacency: dict[str, list[str]] = {}
            for edge in edges:
                if edge["edge_type"] == "next":
                    adjacency.setdefault(edge["from_passage_id"], []).append(edge["to_passage_id"])
            visited: set[str] = set()
            cycle_edges: list[tuple[str, str]] = []
            active: set[str] = set()

            def walk(node: str) -> None:
                if node in visited:
                    return
                active.add(node)
                visited.add(node)
                for target in adjacency.get(node, []):
                    if target in active:
                        cycle_edges.append((node, target))
                    elif target not in visited:
                        walk(target)
                active.remove(node)

            for passage in passages:
                walk(passage["stable_id"])
            talk_doc["dialog_graph"] = {
                "nodes": node_rows,
                "edges": edges,
                "visited_node_count": len(visited),
                "cycle_edges": [{"from": source, "to": target} for source, target in cycle_edges],
                "branch_nodes": sorted(node for node, targets in adjacency.items() if len(targets) > 1),
                "merge_nodes": sorted(target for target in {target for targets in adjacency.values() for target in targets} if sum(target in targets for targets in adjacency.values()) > 1),
            }
            talks.append(talk_doc)
    return {
        "quest": quest,
        "stages": stages,
        "talks": talks,
        "relation_semantics": {
            "stage_of": "native Quest subQuest record",
            "talk_of": "native Quest talk reference",
            "subquest_talk_condition": "native subQuest QUEST_CONTENT_COMPLETE_TALK finish condition; not a guessed playback order",
            "stage_to_talk": "only emitted when the native finish condition explicitly names a Quest talk reference",
        },
        "stats": {
            "stage_count": len(stages),
            "talk_count": len(talks),
            "subquest_talk_condition_count": sum(len(stage.get("talk_conditions", [])) for stage in stages),
            "resolved_talk_count": sum(1 for talk in talks if talk["status"] == "active"),
            "unresolved_talk_count": sum(1 for talk in talks if talk["status"] != "active"),
            "dialog_node_count": sum(len(talk.get("dialog_graph", {}).get("nodes", [])) for talk in talks),
            "edge_count": sum(len(talk.get("dialog_graph", {}).get("edges", [])) for talk in talks),
            "cycle_count": sum(len(talk.get("dialog_graph", {}).get("cycle_edges", [])) for talk in talks),
        },
    }


def compare_languages(db: Database, passage_id: str, snapshot_id: str | None = None) -> dict[str, Any]:
    passage = get_passage(db, passage_id, snapshot_id)
    return {
        "passage_id": passage_id,
        "native_id": passage["native_id"],
        "languages": {text["language"]: {"text": text["raw_text"], "textmap_hash": text.get("textmap_hash"), "shard": text.get("shard"), "provenance": text["provenance"]} for text in passage["texts"]},
    }
