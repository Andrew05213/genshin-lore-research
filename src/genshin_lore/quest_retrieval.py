from __future__ import annotations

from typing import Any

from .db import Database
from .retrieval import (
    _json,
    _occurrence_item,
    _occurrence_rows,
    _occurrence_scope,
    _source,
)


def _quest_document_view(db: Database, document_id: str, snapshot_id: str) -> dict[str, Any]:
    """Read Quest metadata while filtering all legacy Talk children in SQL."""

    row = db.conn.execute(
        "SELECT * FROM document WHERE stable_id=? AND content_type='quest'",
        (document_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Quest document not found: {document_id}")
    result: dict[str, Any] = dict(row)
    version = db.conn.execute(
        """SELECT * FROM document_version
           WHERE document_id=? AND snapshot_id=?""",
        (document_id, snapshot_id),
    ).fetchone()
    result["version"] = (
        {
            **dict(version),
            "metadata": _json(version["metadata_json"], {}),
            "provenance": _source(db, version["source_record_id"]),
        }
        if version is not None
        else None
    )
    result["facts"] = [
        {
            **dict(fact),
            "value": _json(fact["value_json"]),
            "provenance": _source(db, fact["source_record_id"]),
        }
        for fact in db.conn.execute(
            """SELECT * FROM metadata_fact
               WHERE subject_type='document' AND subject_id=?
               ORDER BY key, language""",
            (document_id,),
        ).fetchall()
    ]
    result["children"] = [
        dict(child)
        for child in db.conn.execute(
            """SELECT dr.*, d.content_type, d.native_id, d.status
               FROM document_relation dr
               JOIN document d ON d.stable_id=dr.from_document_id
               WHERE dr.to_document_id=? AND d.content_type <> 'talk'
               ORDER BY d.content_type, d.native_id""",
            (document_id,),
        ).fetchall()
    ]
    result["parents"] = [
        dict(parent)
        for parent in db.conn.execute(
            """SELECT dr.to_document_id, dr.relation_type, dr.source_class,
                      dr.method, d.content_type, d.native_id, d.status
               FROM document_relation dr
               JOIN document d ON d.stable_id=dr.to_document_id
               WHERE dr.from_document_id=? AND d.content_type <> 'talk'
               ORDER BY dr.relation_type, dr.to_document_id LIMIT 1""",
            (document_id,),
        ).fetchall()
    ]
    return result


def _link_item(db: Database, row: Any) -> dict[str, Any]:
    item = {
        "id": row["id"],
        "document_id": row["document_id"],
        "ingest_run_id": row["ingest_run_id"],
        "namespace": row["namespace"],
        "talk_id": row["talk_id"],
        "result": row["result"],
        "evidence_container_id": row["evidence_container_id"],
        "identity_group_id": row["identity_group_id"],
        "source_record_id": row["source_record_id"],
        "metadata": _json(row["metadata_json"], {}),
        "provenance": _source(db, row["source_record_id"]),
    }
    if row["evidence_container_id"] is not None:
        item["candidate_container_ids"] = [str(row["evidence_container_id"])]
    elif row["identity_group_id"] is not None:
        item["candidate_container_ids"] = [
            str(candidate[0])
            for candidate in db.conn.execute(
                """SELECT evidence_container_id
                   FROM identity_group_container
                   WHERE identity_group_id=? AND ingest_run_id=?
                   ORDER BY evidence_container_id""",
                (row["identity_group_id"], row["ingest_run_id"]),
            ).fetchall()
        ]
    else:
        item["candidate_container_ids"] = []
    return item


def _container_candidates(
    db: Database,
    *,
    identity_group_id: str,
    ingest_run_id: str,
    snapshot_id: str,
) -> list[dict[str, Any]]:
    rows = db.conn.execute(
        """SELECT ec.id, ec.ingest_run_id, ec.source_record_id,
                  ec.kind, ec.namespace, ec.talk_id, ec.raw_checksum,
                  ec.normalized_checksum, ec.metadata_json,
                  ir.parser_build_id, ir.scope_json AS run_scope_json,
                  ir.config_json AS run_config_json, ir.status AS run_status,
                  ir.created_at AS run_created_at,
                  ss.id AS snapshot_id, ss.source_id AS snapshot_source_id,
                  ss.release_label AS snapshot_release_label,
                  ss.build AS snapshot_build, ss."commit" AS snapshot_commit,
                  ss.tree AS snapshot_tree, ss.observed_at AS snapshot_observed_at,
                  ss.status AS snapshot_status
           FROM identity_group_container igc
           JOIN evidence_container ec ON ec.id=igc.evidence_container_id
           JOIN ingest_run ir ON ir.id=ec.ingest_run_id
           JOIN source_snapshot ss ON ss.id=ir.snapshot_id
           WHERE igc.identity_group_id=? AND igc.ingest_run_id=?
             AND igc.ingest_run_id=ec.ingest_run_id AND ss.id=?
           ORDER BY ec.id""",
        (identity_group_id, ingest_run_id, snapshot_id),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "id": row["id"],
                "ingest_run_id": row["ingest_run_id"],
                "snapshot_id": row["snapshot_id"],
                "source_record_id": row["source_record_id"],
                "namespace": row["namespace"],
                "talk_id": row["talk_id"],
                "kind": row["kind"],
                "raw_checksum": row["raw_checksum"],
                "normalized_checksum": row["normalized_checksum"],
                "metadata": _json(row["metadata_json"], {}),
                "scope": {
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
                        "status": row["snapshot_status"],
                    },
                    "container": {
                        "id": row["id"],
                        "ingest_run_id": row["ingest_run_id"],
                        "source_record_id": row["source_record_id"],
                        "kind": row["kind"],
                        "namespace": row["namespace"],
                        "talk_id": row["talk_id"],
                        "raw_checksum": row["raw_checksum"],
                        "normalized_checksum": row["normalized_checksum"],
                        "metadata": _json(row["metadata_json"], {}),
                    },
                    "source_record": _source(db, row["source_record_id"]),
                },
            }
        )
    return result


def _graph(
    db: Database,
    *,
    ingest_run_id: str,
    container_id: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    rows = _occurrence_rows(
        db,
        """ WHERE po.ingest_run_id=? AND po.evidence_container_id=?""",
        (ingest_run_id, container_id),
    )
    nodes = [_occurrence_item(db, row, include_scope=False) for row in rows]
    node_by_id = {str(row["passage_occurrence_id"]): node for row, node in zip(rows, nodes)}
    native_by_id = {key: str(value["node_id"]) for key, value in node_by_id.items()}
    scope = _occurrence_scope(db, rows[0]) if rows else None
    edge_rows = db.conn.execute(
        """SELECT id, ingest_run_id, evidence_container_id,
                  from_passage_occurrence_id, to_passage_occurrence_id,
                  edge_type, metadata_json
           FROM occurrence_edge
           WHERE ingest_run_id=? AND evidence_container_id=?
           ORDER BY from_passage_occurrence_id, to_passage_occurrence_id, id""",
        (ingest_run_id, container_id),
    ).fetchall()
    edges: list[dict[str, Any]] = []
    for row in edge_rows:
        edge = dict(row)
        edge["metadata"] = _json(edge.pop("metadata_json"), {})
        edge["from_node_id"] = native_by_id.get(str(edge["from_passage_occurrence_id"]))
        edge["to_node_id"] = native_by_id.get(str(edge["to_passage_occurrence_id"]))
        edge["provenance"] = scope["source_record"] if scope else None
        edges.append(edge)

    adjacency: dict[str, list[str]] = {}
    indegree: dict[str, int] = {}
    for edge in edges:
        if edge["edge_type"] != "next":
            continue
        source = str(edge["from_passage_occurrence_id"])
        target = str(edge["to_passage_occurrence_id"])
        adjacency.setdefault(source, []).append(target)
        indegree[target] = indegree.get(target, 0) + 1

    visited: set[str] = set()
    active: set[str] = set()
    cycle_edges: list[tuple[str, str]] = []

    def walk(node: str) -> None:
        if node in visited:
            return
        visited.add(node)
        active.add(node)
        for target in adjacency.get(node, []):
            if target in active:
                cycle_edges.append((node, target))
            elif target not in visited:
                walk(target)
        active.remove(node)

    for node in sorted(node_by_id):
        walk(node)
    branch_nodes = sorted(node for node, targets in adjacency.items() if len(targets) > 1)
    merge_nodes = sorted(node for node, count in indegree.items() if count > 1)
    graph = {
        "nodes": nodes,
        "edges": edges,
        "visited_node_count": len(visited),
        "cycle_edges": [{"from": source, "to": target} for source, target in cycle_edges],
        "branch_nodes": branch_nodes,
        "branch_node_ids": sorted(native_by_id[node] for node in branch_nodes),
        "branch_targets": {node: sorted(adjacency[node]) for node in branch_nodes},
        "branch_targets_by_node_id": {
            native_by_id[node]: sorted(native_by_id[target] for target in adjacency[node])
            for node in branch_nodes
        },
        "merge_nodes": merge_nodes,
        "merge_node_ids": sorted(native_by_id[node] for node in merge_nodes),
        "merge_sources": {
            node: sorted(
                str(edge["from_passage_occurrence_id"])
                for edge in edges
                if edge["edge_type"] == "next" and edge["to_passage_occurrence_id"] == node
            )
            for node in merge_nodes
        },
        "merge_sources_by_node_id": {
            native_by_id[node]: sorted(
                native_by_id[str(edge["from_passage_occurrence_id"])]
                for edge in edges
                if edge["edge_type"] == "next" and edge["to_passage_occurrence_id"] == node
            )
            for node in merge_nodes
        },
    }
    return graph, scope


def get_quest_evidence_traversal(
    db: Database,
    document_id: str,
    snapshot_id: str,
) -> dict[str, Any]:
    """Return Quest -> document_evidence -> occurrence graph results."""

    quest = _quest_document_view(db, document_id, snapshot_id)
    rows = db.conn.execute(
        """SELECT de.*
           FROM document_evidence de JOIN ingest_run ir ON ir.id=de.ingest_run_id
           WHERE de.document_id=? AND ir.snapshot_id=?
           ORDER BY de.namespace, CAST(de.talk_id AS INTEGER), de.talk_id,
                    de.source_record_id""",
        (document_id, snapshot_id),
    ).fetchall()
    talks: list[dict[str, Any]] = []
    for row in rows:
        link = _link_item(db, row)
        talk: dict[str, Any] = {
            "native_namespace": row["namespace"],
            "native_id": row["talk_id"],
            "result": row["result"],
            "document_evidence": link,
            "provenance": link["provenance"],
            "scope": None,
            "candidates": [],
            "dialog_graph": None,
        }
        if row["result"] == "evidence_only" and row["evidence_container_id"] is not None:
            graph, scope = _graph(
                db,
                ingest_run_id=str(row["ingest_run_id"]),
                container_id=str(row["evidence_container_id"]),
            )
            talk["scope"] = scope
            talk["provenance"] = scope["source_record"] if scope else link["provenance"]
            talk["dialog_graph"] = graph
        elif row["identity_group_id"] is not None:
            talk["candidates"] = _container_candidates(
                db,
                identity_group_id=str(row["identity_group_id"]),
                ingest_run_id=str(row["ingest_run_id"]),
                snapshot_id=snapshot_id,
            )
        talks.append(talk)

    return {
        "quest": quest,
        "stages": [],
        "talks": talks,
        "relation_semantics": {
            "quest_to_talk": "document_evidence result; no logical Talk projection",
            "evidence_only": "one exact native-key evidence_container",
            "ambiguous": "identity_group only; no candidate is selected",
            "unresolved": "no evidence container was located",
            "incomplete_scope": "candidate inventory proof is not complete",
            "quarantined": "manifest, audit or concrete candidate validation failed",
            "dialog_graph": "occurrence_edge within the selected run/container",
        },
        "stats": {
            "stage_count": 0,
            "talk_count": len(talks),
            "resolved_talk_count": sum(talk["result"] == "evidence_only" for talk in talks),
            "ambiguous_talk_count": sum(talk["result"] == "ambiguous" for talk in talks),
            "unresolved_talk_count": sum(talk["result"] == "unresolved" for talk in talks),
            "incomplete_scope_talk_count": sum(talk["result"] == "incomplete_scope" for talk in talks),
            "quarantined_talk_count": sum(talk["result"] == "quarantined" for talk in talks),
            "dialog_node_count": sum(
                len(talk["dialog_graph"]["nodes"])
                for talk in talks
                if talk["dialog_graph"] is not None
            ),
            "edge_count": sum(
                len(talk["dialog_graph"]["edges"])
                for talk in talks
                if talk["dialog_graph"] is not None
            ),
            "cycle_count": sum(
                len(talk["dialog_graph"]["cycle_edges"])
                for talk in talks
                if talk["dialog_graph"] is not None
            ),
        },
    }
