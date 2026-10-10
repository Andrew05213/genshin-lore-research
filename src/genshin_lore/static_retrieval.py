from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .retrieval import _json, _source
from .static_contracts import (
    STATIC_RUN_CONTRACTS,
    StaticArtifactSeal,
    exact_static_schema,
    match_static_artifact,
    qualify_static_run,
    strict_json_loads,
    strict_json_object,
)
from .util import canonical_json, digest_id, sha256_text


@dataclass(frozen=True)
class StaticNotFound:
    """A cardinality-aware static evidence container lookup with no match."""

    lookup: dict[str, Any]


@dataclass(frozen=True)
class StaticAmbiguousLookup:
    """A cardinality-aware static evidence container lookup with more than one match."""

    lookup: dict[str, Any]
    candidates: tuple[dict[str, Any], ...]


_STATIC_CONTAINER_SELECT = """
    SELECT sec.id, sec.ingest_run_id, sec.snapshot_id, sec.source_file_id,
           sec.source_record_id, sec.container_kind, sec.source_family,
           sec.locale, sec.candidate_namespace, sec.candidate_key_json,
           sec.metadata_json,
           ir.parser_build_id, ir.scope_json AS run_scope_json,
           ir.config_json AS run_config_json, ir.status AS run_status,
           ir.created_at AS run_created_at,
           ss.source_id AS snapshot_source_id,
           ss.release_label AS snapshot_release_label,
           ss.build AS snapshot_build, ss."commit" AS snapshot_commit,
           ss.tree AS snapshot_tree, ss.observed_at AS snapshot_observed_at,
           ss.parser_version AS snapshot_parser_version,
           ss.mapping_version AS snapshot_mapping_version,
           ss.status AS snapshot_status,
           ss.metadata_json AS snapshot_metadata_json,
           sr.pointer AS source_record_pointer,
           sf.relative_path AS source_file_relative_path
      FROM static_evidence_container sec
      JOIN ingest_run ir ON ir.id=sec.ingest_run_id
      JOIN source_snapshot ss ON ss.id=sec.snapshot_id
      JOIN source_record sr ON sr.id=sec.source_record_id
      JOIN source_file sf ON sf.id=sec.source_file_id
"""


def _normalize_candidate_key(candidate_key: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(candidate_key, str):
        parsed = strict_json_loads(candidate_key)
        if not isinstance(parsed, dict):
            raise ValueError(f"Candidate key JSON must be an object: {candidate_key}")
        candidate_key = parsed

    normalized: dict[str, Any] = {}
    for key, value in candidate_key.items():
        if isinstance(value, (int, float)):
            normalized[key] = str(value)
        else:
            normalized[key] = value
    return normalized


def _container_scope(db: Any, row: Any) -> dict[str, Any]:
    return {
        "run": {
            "id": row["ingest_run_id"],
            "snapshot_id": row["snapshot_id"],
            "parser_build_id": row["parser_build_id"],
            "scope": strict_json_object(row["run_scope_json"]),
            "config": strict_json_object(row["run_config_json"]),
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
            "id": row["id"],
            "ingest_run_id": row["ingest_run_id"],
            "source_record_id": row["source_record_id"],
            "container_kind": row["container_kind"],
            "source_family": row["source_family"],
            "locale": row["locale"],
            "candidate_namespace": row["candidate_namespace"],
            "candidate_key": _json(row["candidate_key_json"]),
            "metadata": _json(row["metadata_json"], {}),
        },
        "source_record": _source(db, row["source_record_id"]),
    }


def _container_item(db: Any, row: Any, *, include_scope: bool = True) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": row["id"],
        "ingest_run_id": row["ingest_run_id"],
        "snapshot_id": row["snapshot_id"],
        "source_file_id": row["source_file_id"],
        "source_record_id": row["source_record_id"],
        "container_kind": row["container_kind"],
        "source_family": row["source_family"],
        "locale": row["locale"],
        "candidate_namespace": row["candidate_namespace"],
        "candidate_key": _json(row["candidate_key_json"]),
        "metadata": _json(row["metadata_json"], {}),
    }
    if include_scope:
        item["scope"] = _container_scope(db, row)
    return item


def _segment_item(db: Any, row: Any, *, include_provenance: bool = True) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": row["id"],
        "ingest_run_id": row["ingest_run_id"],
        "snapshot_id": row["snapshot_id"],
        "evidence_container_id": row["evidence_container_id"],
        "source_record_id": row["source_record_id"],
        "container_kind": row["container_kind"],
        "locale": row["locale"],
        "source_pointer": row["source_pointer"],
        "file_local_ordinal": row["file_local_ordinal"],
        "byte_start": row["byte_start"],
        "byte_end": row["byte_end"],
        "character_start": row["character_start"],
        "character_end": row["character_end"],
        "raw_text": row["raw_text"],
        "normalized_text": row["normalized_text"],
        "raw_checksum": row["raw_checksum"],
        "normalized_checksum": row["normalized_checksum"],
        "segmentation_version": row["segmentation_version"],
        "metadata": _json(row["metadata_json"], {}),
    }
    if include_provenance:
        item["provenance"] = _source(db, row["source_record_id"])
    return item


def _relationship_item(db: Any, row: Any, *, include_provenance: bool = True) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": row["id"],
        "ingest_run_id": row["ingest_run_id"],
        "snapshot_id": row["snapshot_id"],
        "source_container_id": row["source_container_id"],
        "source_record_id": row["source_record_id"],
        "source_family": row["source_family"],
        "source_pointer": row["source_pointer"],
        "relation_kind": row["relation_kind"],
        "field_role": row["field_role"],
        "target_family": row["target_family"],
        "target_candidate_key": _json(row["target_candidate_key_json"]),
        "candidate_resolution_status": row["candidate_resolution_status"],
        "target_evidence_container_id": row["target_evidence_container_id"],
        "ambiguity": _json(row["ambiguity_json"], {}),
        "metadata": _json(row["metadata_json"], {}),
    }
    if include_provenance:
        item["provenance"] = _source(db, row["source_record_id"])
    return item


def list_static_evidence_containers(
    db: Any,
    *,
    ingest_run_id: str,
    source_family: str | None = None,
    container_kind: str | None = None,
    locale: str | None = None,
    candidate_namespace: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Paginate static evidence containers within explicit ingest_run_id."""
    if not ingest_run_id or not isinstance(ingest_run_id, str):
        raise ValueError("ingest_run_id must be a non-empty string")
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    if offset < 0:
        raise ValueError("offset must be non-negative")

    where_clauses = ["sec.ingest_run_id = ?"]
    params: list[Any] = [str(ingest_run_id)]

    if source_family is not None:
        where_clauses.append("sec.source_family = ?")
        params.append(str(source_family))
    if container_kind is not None:
        where_clauses.append("sec.container_kind = ?")
        params.append(str(container_kind))
    if locale is not None:
        where_clauses.append("sec.locale = ?")
        params.append(str(locale))
    if candidate_namespace is not None:
        where_clauses.append("sec.candidate_namespace = ?")
        params.append(str(candidate_namespace))

    where_sql = " WHERE " + " AND ".join(where_clauses)
    count_row = db.conn.execute(
        f"SELECT COUNT(*) FROM static_evidence_container sec {where_sql}",
        params,
    ).fetchone()
    total = int(count_row[0]) if count_row else 0

    sql = (
        _STATIC_CONTAINER_SELECT
        + where_sql
        + " ORDER BY sec.id ASC LIMIT ? OFFSET ?"
    )
    rows = db.conn.execute(sql, params + [limit, offset]).fetchall()
    items = [_container_item(db, row, include_scope=True) for row in rows]

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def find_static_evidence_container(
    db: Any,
    *,
    ingest_run_id: str,
    source_family: str,
    candidate_key: dict[str, Any] | str,
) -> dict[str, Any] | StaticNotFound | StaticAmbiguousLookup:
    """Look up a static evidence container by exact candidate key."""
    if not ingest_run_id or not isinstance(ingest_run_id, str):
        raise ValueError("ingest_run_id must be a non-empty string")
    if not source_family or not isinstance(source_family, str):
        raise ValueError("source_family must be a non-empty string")

    normalized_key = _normalize_candidate_key(candidate_key)
    canonical_key_json = canonical_json(normalized_key)
    lookup_meta = {
        "ingest_run_id": str(ingest_run_id),
        "source_family": str(source_family),
        "candidate_key": normalized_key,
    }

    sql = (
        _STATIC_CONTAINER_SELECT
        + """ WHERE sec.ingest_run_id = ?
                AND sec.source_family = ?
                AND sec.candidate_key_json = ?
            ORDER BY sec.id ASC"""
    )
    rows = db.conn.execute(
        sql,
        (str(ingest_run_id), str(source_family), canonical_key_json),
    ).fetchall()

    if not rows:
        return StaticNotFound(lookup=lookup_meta)
    if len(rows) == 1:
        item = _container_item(db, rows[0], include_scope=True)
        item["result"] = "evidence_only"
        return item

    candidates = tuple(_container_item(db, row, include_scope=True) for row in rows)
    return StaticAmbiguousLookup(lookup=lookup_meta, candidates=candidates)


def get_static_evidence_container(
    db: Any,
    *,
    ingest_run_id: str,
    evidence_container_id: str,
    segment_limit: int = 100,
    segment_offset: int = 0,
    relationship_limit: int = 100,
    relationship_offset: int = 0,
) -> dict[str, Any] | None:
    """Read single static container, provenance, container-local segments, and relationships."""
    if not ingest_run_id or not isinstance(ingest_run_id, str):
        raise ValueError("ingest_run_id must be a non-empty string")
    if not evidence_container_id or not isinstance(evidence_container_id, str):
        raise ValueError("evidence_container_id must be a non-empty string")
    if segment_limit < 1 or segment_limit > 500:
        raise ValueError("segment_limit must be between 1 and 500")
    if segment_offset < 0:
        raise ValueError("segment_offset must be non-negative")
    if relationship_limit < 1 or relationship_limit > 500:
        raise ValueError("relationship_limit must be between 1 and 500")
    if relationship_offset < 0:
        raise ValueError("relationship_offset must be non-negative")

    sql = (
        _STATIC_CONTAINER_SELECT
        + " WHERE sec.ingest_run_id = ? AND sec.id = ?"
    )
    row = db.conn.execute(sql, (str(ingest_run_id), str(evidence_container_id))).fetchone()
    if row is None:
        return None

    container = _container_item(db, row, include_scope=False)
    scope = _container_scope(db, row)
    provenance = {
        "snapshot": scope["snapshot"],
        "ingest_run": scope["run"],
        "source_file_relative_path": row["source_file_relative_path"],
        "source_record": scope["source_record"],
        "source_pointer": row["source_record_pointer"],
        "static_evidence_container": scope["container"],
    }

    # Query container-local segments
    seg_count_row = db.conn.execute(
        """SELECT COUNT(*) FROM static_segment_occurrence
           WHERE ingest_run_id = ? AND evidence_container_id = ?""",
        (str(ingest_run_id), str(evidence_container_id)),
    ).fetchone()
    segment_total = int(seg_count_row[0]) if seg_count_row else 0

    seg_rows = db.conn.execute(
        """SELECT * FROM static_segment_occurrence
           WHERE ingest_run_id = ? AND evidence_container_id = ?
           ORDER BY file_local_ordinal ASC, id ASC
           LIMIT ? OFFSET ?""",
        (str(ingest_run_id), str(evidence_container_id), segment_limit, segment_offset),
    ).fetchall()
    segments = [_segment_item(db, s_row, include_provenance=True) for s_row in seg_rows]

    # Query outgoing relationship summaries
    rel_count_row = db.conn.execute(
        """SELECT COUNT(*) FROM static_relationship_occurrence
           WHERE ingest_run_id = ? AND source_container_id = ?""",
        (str(ingest_run_id), str(evidence_container_id)),
    ).fetchone()
    relationship_total = int(rel_count_row[0]) if rel_count_row else 0

    rel_rows = db.conn.execute(
        """SELECT * FROM static_relationship_occurrence
           WHERE ingest_run_id = ? AND source_container_id = ?
           ORDER BY id ASC
           LIMIT ? OFFSET ?""",
        (str(ingest_run_id), str(evidence_container_id), relationship_limit, relationship_offset),
    ).fetchall()
    relationships = [_relationship_item(db, r_row, include_provenance=False) for r_row in rel_rows]

    return {
        "container": container,
        "scope": scope,
        "provenance": provenance,
        "segments": segments,
        "segment_total": segment_total,
        "segment_limit": segment_limit,
        "segment_offset": segment_offset,
        "relationships": relationships,
        "relationship_total": relationship_total,
        "relationship_limit": relationship_limit,
        "relationship_offset": relationship_offset,
    }


def list_static_relationship_evidence(
    db: Any,
    *,
    ingest_run_id: str,
    source_container_id: str | None = None,
    source_family: str | None = None,
    target_family: str | None = None,
    relation_kind: str | None = None,
    field_role: str | None = None,
    candidate_resolution_status: str | None = None,
    target_evidence_container_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List static relationship occurrences within explicit ingest_run_id."""
    if not ingest_run_id or not isinstance(ingest_run_id, str):
        raise ValueError("ingest_run_id must be a non-empty string")
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    if offset < 0:
        raise ValueError("offset must be non-negative")

    where_clauses = ["sro.ingest_run_id = ?"]
    params: list[Any] = [str(ingest_run_id)]

    if source_container_id is not None:
        where_clauses.append("sro.source_container_id = ?")
        params.append(str(source_container_id))
    if source_family is not None:
        where_clauses.append("sro.source_family = ?")
        params.append(str(source_family))
    if target_family is not None:
        where_clauses.append("sro.target_family = ?")
        params.append(str(target_family))
    if relation_kind is not None:
        where_clauses.append("sro.relation_kind = ?")
        params.append(str(relation_kind))
    if field_role is not None:
        where_clauses.append("sro.field_role = ?")
        params.append(str(field_role))
    if candidate_resolution_status is not None:
        where_clauses.append("sro.candidate_resolution_status = ?")
        params.append(str(candidate_resolution_status))
    if target_evidence_container_id is not None:
        where_clauses.append("sro.target_evidence_container_id = ?")
        params.append(str(target_evidence_container_id))

    where_sql = " WHERE " + " AND ".join(where_clauses)
    count_row = db.conn.execute(
        f"SELECT COUNT(*) FROM static_relationship_occurrence sro {where_sql}",
        params,
    ).fetchone()
    total = int(count_row[0]) if count_row else 0

    sql = f"""
        SELECT sro.*
          FROM static_relationship_occurrence sro
          {where_sql}
         ORDER BY sro.id ASC
         LIMIT ? OFFSET ?
    """
    rows = db.conn.execute(sql, params + [limit, offset]).fetchall()
    items = [_relationship_item(db, row, include_provenance=True) for row in rows]

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


REQUIRED_STATIC_TABLES = (
    "static_evidence_container",
    "static_segment_occurrence",
    "static_relationship_occurrence",
    "static_text_resolution_occurrence",
    "static_text_candidate_occurrence",
)


_STATIC_RUN_FAMILIES = {
    "static_book1": "b1",
    "book1": "b1",
    "static_phase4b2_multifamily": "b2",
    "static_phase4b3_book_weapon_corpus": "b3",
    "static_phase4b5_reliquary_corpus": "b5",
    "static_phase4b6_material_corpus": "b6",
}
_STATIC_MANIFESTS = {
    "anime-game-data-7.0-book1-canary": "b1",
    "anime-game-data-7.0-book1-static-evidence-canary": "b1",
    "anime-game-data-7.0-phase4b2-multifamily-canary": "b2",
    "anime-game-data-7.0-phase4b3-book-weapon-corpus": "b3",
    "anime-game-data-7.0-phase4b5-reliquary-corpus": "b5",
    "anime-game-data-7.0-phase4b6-material-corpus": "b6",
}
_STATIC_FAMILY_KEYS = {
    "book1257": "books",
    "weapon15516": "weapons",
    "reliquary15048": "reliquaries",
    "materials": "materials",
}
_STATIC_SOURCE_FAMILIES = {
    "BookSuitExcelConfigData": "books",
    "BooksCodexExcelConfigData": "books",
    "WeaponCodexExcelConfigData": "weapons",
    "WeaponExcelConfigData": "weapons",
    "MaterialCodexExcelConfigData": "materials",
    "MaterialExcelConfigData": "materials",
    "ReliquaryCodexExcelConfigData": "reliquaries",
    "ReliquaryExcelConfigData": "reliquaries",
    "ReliquarySetExcelConfigData": "reliquaries",
}
_STATIC_COVERAGE_RANK = {"canary_only": 1, "corpus_complete": 2}


def _json_object(value: str | None) -> dict[str, Any] | None:
    try:
        return strict_json_object(value)
    except ValueError:
        return None


def _classify_static_run(scope: dict[str, Any]) -> str | None:
    """Return a static run kind from persisted provenance, never its ID."""
    scope_family = scope.get("family")
    manifest_id = scope.get("manifest_id")
    by_scope = _STATIC_RUN_FAMILIES.get(scope_family)
    by_manifest = _STATIC_MANIFESTS.get(manifest_id)

    if by_scope is not None and by_scope == by_manifest:
        return by_scope
    if scope_family in ("static_lore", "static_evidence") and manifest_id is None:
        # Generic static evidence predates the sealed B1/B2/B3 manifests.  It
        # can expose evidence, but it cannot claim a corpus-complete family.
        return "generic"
    return None


def _container_family(row: Any) -> str | None:
    """Map explicit container provenance to a public family when possible."""
    metadata = _json_object(row["metadata_json"]) or {}
    family = metadata.get("family")
    if family in {"books", "weapons", "reliquaries", "materials"}:
        return family

    family_key = metadata.get("family_key")
    if isinstance(family_key, str):
        mapped = _STATIC_FAMILY_KEYS.get(family_key)
        if mapped is not None:
            return mapped

    return _STATIC_SOURCE_FAMILIES.get(row["source_family"])


def _has_manifest_provenance(
    run_scope: dict[str, Any],
    run_config: dict[str, Any],
    *,
    rows: list[Any],
) -> bool:
    """Check run-level and container-level manifest markers where present."""
    manifest_id = run_scope.get("manifest_id")
    manifest_sha = run_config.get("manifest_raw_sha256")
    if not isinstance(manifest_id, str) or not manifest_id:
        return False
    if not isinstance(manifest_sha, str) or not manifest_sha:
        return False

    # Sealed builders persist these markers on every container.  Requiring
    # agreement prevents a row from another provenance set from upgrading a
    # run.  Generic/B1/B2 databases that predate per-container markers are
    # still allowed to expose only evidence/canary coverage below.
    for row in rows:
        metadata = _json_object(row["metadata_json"]) or {}
        row_manifest_id = metadata.get("manifest_id")
        row_manifest_sha = metadata.get("manifest_raw_sha256")
        if row_manifest_id is not None and row_manifest_id != manifest_id:
            return False
        if row_manifest_sha is not None and row_manifest_sha != manifest_sha:
            return False
    return True


def _b3_family_coverage(
    run_scope: dict[str, Any],
    run_config: dict[str, Any],
    rows: list[Any],
) -> dict[str, str] | None:
    """Return B3 corpus claims only for a fully provenance-marked run."""
    if not _has_manifest_provenance(run_scope, run_config, rows=rows):
        return None

    families: set[str] = set()
    for row in rows:
        metadata = _json_object(row["metadata_json"]) or {}
        # B3's family marker is the explicit projection provenance.  Do not
        # infer it from table names, native IDs, checksums, or text.
        family = metadata.get("family")
        if family not in ("books", "weapons"):
            return None
        if metadata.get("manifest_id") != run_scope["manifest_id"]:
            return None
        if metadata.get("manifest_raw_sha256") != run_config["manifest_raw_sha256"]:
            return None
        families.add(family)
    return {family: "corpus_complete" for family in sorted(families)}


def _b5_family_coverage(
    run_scope: dict[str, Any],
    run_config: dict[str, Any],
    rows: list[Any],
) -> dict[str, str] | None:
    """Return B5 corpus claims only for a fully provenance-marked run."""
    if not _has_manifest_provenance(run_scope, run_config, rows=rows):
        return None

    families: set[str] = set()
    for row in rows:
        metadata = _json_object(row["metadata_json"]) or {}
        family = metadata.get("family")
        if family != "reliquaries":
            return None
        if metadata.get("manifest_id") != run_scope["manifest_id"]:
            return None
        if metadata.get("manifest_raw_sha256") != run_config["manifest_raw_sha256"]:
            return None
        families.add(family)
    return {family: "corpus_complete" for family in sorted(families)}


PHASE4B6_MATERIAL_MANIFEST_PATH = (
    Path(__file__).resolve().parents[2]
    / "manifests/static/anime-game-data-7.0-phase4b6-material-corpus.json"
)


def _load_phase4b6_material_manifest() -> Any:
    """Load the sealed B6 manifest only when a B6 run is being inspected."""
    try:
        from .static_phase4b6_material_manifest import load_static_phase4b6_material_manifest
        return load_static_phase4b6_material_manifest(PHASE4B6_MATERIAL_MANIFEST_PATH)
    except (ImportError, OSError, ValueError, RuntimeError):
        return None


def _phase4b6_expected_counts(manifest: Any) -> dict[str, int] | None:
    try:
        expected = manifest.expected["counts"]
        return {
            "files": int(expected["selected_files"]),
            "source_records": int(expected["source_records"]),
            "containers": int(expected["containers"]),
            "segments": int(expected["segments"]),
            "relationships": int(expected["relationships"]),
            "text_resolutions": int(expected["text_resolutions"]),
            "text_candidates": int(expected["text_candidates"]),
            "text_assets": int(expected["text_assets"]),
            "text_owner_occurrences": int(expected["text_owner_occurrences"]),
        }
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _phase4b6_selection_rule(manifest: Any) -> str | None:
    for value in (
        getattr(manifest, "selection_rule", None),
        getattr(manifest, "boundaries", {}).get("selection_rule"),
        getattr(manifest, "versions", {}).get("selection_rule"),
        getattr(manifest, "raw", {}).get("selection_rule"),
    ):
        if isinstance(value, str) and value:
            return value
    return None


def _b6_owned_inventory_matches(
    db: Any,
    run_id: str,
    run_snapshot_id: str,
    rows: list[Any],
    manifest: Any,
) -> bool:
    """Require exact B6 counts and provenance ownership before promotion."""
    expected = _phase4b6_expected_counts(manifest)
    if expected is None or not rows:
        return False
    if len(rows) != expected["containers"]:
        return False

    manifest_expected = getattr(manifest, "expected", {})
    expected_family_counts = manifest_expected.get("families")
    if expected_family_counts is None:
        expected_family_counts = manifest_expected.get("family_counts")
    expected_family = expected_family_counts.get("materials") if isinstance(expected_family_counts, dict) else None
    if not isinstance(expected_family, dict):
        return False

    # The source-record inventory is the exact provenance closure used by the
    # sealed builder: every material container row plus each distinct TextMap
    # candidate source record.  Native IDs/checksums never enter this test as
    # identities; they are only attributes of these coordinates.
    source_record_count = int(db.conn.execute(
        """SELECT COUNT(*) FROM (
               SELECT source_record_id FROM static_evidence_container WHERE ingest_run_id=?
               UNION
               SELECT source_record_id FROM static_text_candidate_occurrence WHERE ingest_run_id=?
           )""",
        (run_id, run_id),
    ).fetchone()[0])
    file_count = int(db.conn.execute(
        """SELECT COUNT(*) FROM (
               SELECT source_file_id FROM static_evidence_container WHERE ingest_run_id=?
               UNION
               SELECT source_file_id FROM static_text_candidate_occurrence WHERE ingest_run_id=?
           )""",
        (run_id, run_id),
    ).fetchone()[0])
    text_asset_count = int(db.conn.execute(
        """SELECT COUNT(*) FROM (
               SELECT DISTINCT sr.id
                 FROM source_record sr
                 JOIN source_file sf ON sf.id=sr.source_file_id
                WHERE sr.id IN (
                    SELECT source_record_id FROM static_text_candidate_occurrence WHERE ingest_run_id=?
                )
                  AND sf.relative_path LIKE '%TextMap%'
           )""",
        (run_id,),
    ).fetchone()[0])
    text_owner_count = int(db.conn.execute(
        """SELECT COUNT(*) FROM static_relationship_occurrence
            WHERE ingest_run_id=? AND target_family='TextMap'
              AND field_role='verified_textmap_reference'""",
        (run_id,),
    ).fetchone()[0])
    actual_family_counts = {
        "materials": {
            "containers": len(rows),
            "segments": int(db.conn.execute(
                "SELECT COUNT(*) FROM static_segment_occurrence WHERE ingest_run_id=?",
                (run_id,),
            ).fetchone()[0]),
            "relationships": int(db.conn.execute(
                "SELECT COUNT(*) FROM static_relationship_occurrence WHERE ingest_run_id=?",
                (run_id,),
            ).fetchone()[0]),
            "text_resolutions": int(db.conn.execute(
                "SELECT COUNT(*) FROM static_text_resolution_occurrence WHERE ingest_run_id=?",
                (run_id,),
            ).fetchone()[0]),
            "text_candidates": int(db.conn.execute(
                "SELECT COUNT(*) FROM static_text_candidate_occurrence WHERE ingest_run_id=?",
                (run_id,),
            ).fetchone()[0]),
            "text_assets": text_asset_count,
            "text_owner_occurrences": text_owner_count,
        },
    }
    if actual_family_counts != {"materials": expected_family}:
        return False

    actual_counts = {
        "files": file_count,
        "source_records": source_record_count,
        "containers": len(rows),
        "segments": actual_family_counts["materials"]["segments"],
        "relationships": actual_family_counts["materials"]["relationships"],
        "text_resolutions": actual_family_counts["materials"]["text_resolutions"],
        "text_candidates": actual_family_counts["materials"]["text_candidates"],
        "text_assets": text_asset_count,
        "text_owner_occurrences": text_owner_count,
    }
    for key, value in actual_counts.items():
        if value != expected[key]:
            return False

    expected_selected_paths = manifest_expected.get("family_selected_paths")
    if expected_selected_paths is None:
        expected_selected_paths = {
            "materials": sorted(str(item.relative_path) for item in manifest.files)
        }
    if expected_selected_paths is not None:
        selected_path_rows = db.conn.execute(
            """SELECT relative_path FROM source_file
                  WHERE id IN (
                      SELECT source_file_id FROM static_evidence_container WHERE ingest_run_id=?
                      UNION
                      SELECT source_file_id FROM static_text_candidate_occurrence WHERE ingest_run_id=?
                  )
                ORDER BY relative_path""",
            (run_id, run_id),
        ).fetchall()
        actual_selected_paths = {
            "materials": [str(item["relative_path"]) for item in selected_path_rows]
        }
        if actual_selected_paths != expected_selected_paths:
            return False

    table_specs = getattr(manifest, "tables", {})
    expected_rows_by_table: dict[str, dict[str, dict[str, Any]]] = {}
    if not isinstance(table_specs, dict):
        return False
    for table, table_spec in table_specs.items():
        selected_rows = table_spec.get("selected_rows") if isinstance(table_spec, dict) else None
        if not isinstance(selected_rows, list):
            return False
        expected_rows_by_table[str(table)] = {
            str(item.get("pointer")): item
            for item in selected_rows
            if isinstance(item, dict) and isinstance(item.get("pointer"), str)
        }

    for row in rows:
        metadata = _json_object(row["metadata_json"])
        if not isinstance(metadata, dict):
            return False
        if (
            set(metadata) != {
                "family",
                "manifest_id",
                "manifest_raw_sha256",
                "raw_evidence_identity",
            }
            or
            metadata.get("family") != "materials"
            or metadata.get("manifest_id") != manifest.manifest_id
            or metadata.get("manifest_raw_sha256") != manifest.raw_sha256
        ):
            return False
        if row["ingest_run_id"] != run_id or row["snapshot_id"] != run_snapshot_id:
            return False
        if row["source_family"] not in expected_rows_by_table:
            return False
        expected_file_id = digest_id("sf", run_snapshot_id, row["relative_path"])
        expected_record_id = digest_id("sr", expected_file_id, row["record_pointer"])
        expected_container_id = digest_id(
            "static-container",
            run_snapshot_id,
            manifest.raw_sha256,
            run_id,
            row["relative_path"],
            row["record_pointer"],
        )
        if (
            row["id"] != expected_container_id
            or row["source_file_id"] != expected_file_id
            or row["source_record_id"] != expected_record_id
        ):
            return False
        file_spec = next(
            (
                item
                for item in manifest.files
                if str(item.relative_path) == str(row["relative_path"])
            ),
            None,
        )
        if file_spec is None or (
            row["source_file_snapshot_id"] != run_snapshot_id
            or row["source_file_blob_oid"] is not None
            or row["source_file_sha256"] != file_spec.sha256
            or row["source_file_size_bytes"] != file_spec.size_bytes
            or row["source_file_format"] != file_spec.format
        ):
            return False
        selected_row = expected_rows_by_table[row["source_family"]].get(row["record_pointer"])
        if not isinstance(selected_row, dict):
            return False
        if (
            str(selected_row.get("native_id")) != str(row["record_native_id"])
            or selected_row.get("raw_sha256") != row["record_raw_sha256"]
            or selected_row.get("family") != "materials"
        ):
            return False
        try:
            record_value = strict_json_loads(str(row["record_raw_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        if (
            row["record_kind"] != "excel_row"
            or str(row["record_raw_json"]) != canonical_json(record_value)
            or row["record_raw_sha256"] != sha256_text(str(row["record_raw_json"]))
        ):
            return False
        if row["candidate_namespace"] != f"agd.static.{row['source_family']}":
            return False
        candidate_key = _json_object(row["candidate_key_json"])
        if candidate_key != {
            "table": row["source_family"],
            "native_id": str(row["record_native_id"]),
        }:
            return False

    # Every secondary occurrence must be owned by one of the same run's
    # containers.  This rejects borrowed rows and cross-run relationships.
    bad = db.conn.execute(
        """SELECT 1 FROM static_segment_occurrence s
            LEFT JOIN static_evidence_container c
              ON c.id=s.evidence_container_id AND c.ingest_run_id=s.ingest_run_id
           WHERE s.ingest_run_id=? AND c.id IS NULL LIMIT 1""",
        (run_id,),
    ).fetchone()
    if bad is not None:
        return False
    bad = db.conn.execute(
        """SELECT 1
             FROM static_text_resolution_occurrence tr
             LEFT JOIN static_evidence_container c
               ON c.id=tr.source_container_id
              AND c.ingest_run_id=tr.ingest_run_id
              AND c.snapshot_id=tr.snapshot_id
            WHERE tr.ingest_run_id=? AND c.id IS NULL LIMIT 1""",
        (run_id,),
    ).fetchone()
    if bad is not None:
        return False
    bad = db.conn.execute(
        """SELECT 1
             FROM static_text_candidate_occurrence tc
             LEFT JOIN static_text_resolution_occurrence tr
               ON tr.id=tc.resolution_occurrence_id
              AND tr.ingest_run_id=tc.ingest_run_id
              AND tr.snapshot_id=tc.snapshot_id
            WHERE tc.ingest_run_id=? AND tr.id IS NULL LIMIT 1""",
        (run_id,),
    ).fetchone()
    if bad is not None:
        return False
    bad = db.conn.execute(
        """SELECT 1 FROM static_relationship_occurrence r
            LEFT JOIN static_evidence_container c
              ON c.id=r.source_container_id AND c.ingest_run_id=r.ingest_run_id
           WHERE r.ingest_run_id=? AND c.id IS NULL LIMIT 1""",
        (run_id,),
    ).fetchone()
    if bad is not None:
        return False
    bad = db.conn.execute(
        """SELECT 1 FROM static_relationship_occurrence r
            JOIN static_evidence_container target ON target.id=r.target_evidence_container_id
           WHERE r.ingest_run_id=? AND r.target_evidence_container_id IS NOT NULL
             AND (target.ingest_run_id<>? OR target.snapshot_id<>r.snapshot_id) LIMIT 1""",
        (run_id, run_id),
    ).fetchone()
    if bad is not None:
        return False

    # Rebuild the run-scoped IDs for all secondary occurrences as well.  A
    # row copied from a different run can otherwise retain plausible source
    # coordinates while escaping the simple foreign-key ownership checks.
    relationship_rows = db.conn.execute(
        """SELECT r.id, r.ingest_run_id, r.snapshot_id,
                          r.source_container_id, r.source_record_id,
                          r.source_pointer, c.source_file_id,
                          c.source_record_id AS container_record_id,
                          c.ingest_run_id AS container_run_id,
                          c.snapshot_id AS container_snapshot_id,
                          sf.relative_path, sr.pointer AS record_pointer
                     FROM static_relationship_occurrence r
                     JOIN static_evidence_container c ON c.id=r.source_container_id
                     JOIN source_file sf ON sf.id=c.source_file_id
                     JOIN source_record sr ON sr.id=c.source_record_id
                    WHERE r.ingest_run_id=?""",
        (run_id,),
    ).fetchall()
    for row in relationship_rows:
        if (
            row["ingest_run_id"] != run_id
            or row["snapshot_id"] != run_snapshot_id
            or row["container_run_id"] != run_id
            or row["container_snapshot_id"] != run_snapshot_id
            or row["source_record_id"] != row["container_record_id"]
        ):
            return False
        expected_id = digest_id(
            "static-relationship",
            run_snapshot_id,
            manifest.raw_sha256,
            run_id,
            row["relative_path"],
            row["record_pointer"],
            row["source_pointer"],
        )
        if row["id"] != expected_id:
            return False

    resolution_rows = db.conn.execute(
        """SELECT tr.id, tr.ingest_run_id, tr.snapshot_id,
                          tr.static_relationship_occurrence_id,
                          tr.source_container_id, tr.source_record_id,
                          tr.locale, tr.source_pointer,
                          tr.textmap_hash, tr.resolution_status,
                          r.id AS relationship_id,
                          r.ingest_run_id AS relationship_run_id,
                          r.snapshot_id AS relationship_snapshot_id,
                          c.ingest_run_id AS container_run_id,
                          c.snapshot_id AS container_snapshot_id,
                          c.source_record_id AS container_record_id
                     FROM static_text_resolution_occurrence tr
                     JOIN static_relationship_occurrence r
                       ON r.id=tr.static_relationship_occurrence_id
                      AND r.ingest_run_id=tr.ingest_run_id
                      AND r.snapshot_id=tr.snapshot_id
                     JOIN static_evidence_container c
                       ON c.id=tr.source_container_id
                      AND c.ingest_run_id=tr.ingest_run_id
                      AND c.snapshot_id=tr.snapshot_id
                    WHERE tr.ingest_run_id=?""",
        (run_id,),
    ).fetchall()
    for row in resolution_rows:
        if (
            row["ingest_run_id"] != run_id
            or row["snapshot_id"] != run_snapshot_id
            or row["relationship_id"] != row["static_relationship_occurrence_id"]
            or row["relationship_run_id"] != run_id
            or row["relationship_snapshot_id"] != run_snapshot_id
            or row["container_run_id"] != run_id
            or row["container_snapshot_id"] != run_snapshot_id
            or row["source_record_id"] != row["container_record_id"]
        ):
            return False
        expected_id = digest_id(
            "static-text-resolution",
            run_snapshot_id,
            manifest.raw_sha256,
            run_id,
            row["static_relationship_occurrence_id"],
            row["locale"],
        )
        if row["id"] != expected_id:
            return False

    candidate_rows = db.conn.execute(
        """SELECT tc.id, tc.ingest_run_id, tc.snapshot_id,
                          tc.resolution_occurrence_id, tc.source_file_id,
                          tc.source_record_id, tc.source_pointer,
                          tc.shard_kind, tc.locale,
                          tr.id AS resolution_id,
                          tr.ingest_run_id AS resolution_run_id,
                          tr.snapshot_id AS resolution_snapshot_id,
                          sf.snapshot_id AS file_snapshot_id,
                          sf.relative_path, sr.pointer AS record_pointer
                     FROM static_text_candidate_occurrence tc
                     JOIN static_text_resolution_occurrence tr
                       ON tr.id=tc.resolution_occurrence_id
                      AND tr.ingest_run_id=tc.ingest_run_id
                      AND tr.snapshot_id=tc.snapshot_id
                     JOIN source_file sf ON sf.id=tc.source_file_id
                     JOIN source_record sr ON sr.id=tc.source_record_id
                    WHERE tc.ingest_run_id=?""",
        (run_id,),
    ).fetchall()
    textmap_paths = getattr(manifest, "boundaries", {}).get("textmap_shards", {})
    for row in candidate_rows:
        if (
            row["ingest_run_id"] != run_id
            or row["snapshot_id"] != run_snapshot_id
            or row["resolution_id"] != row["resolution_occurrence_id"]
            or row["resolution_run_id"] != run_id
            or row["resolution_snapshot_id"] != run_snapshot_id
            or row["file_snapshot_id"] != run_snapshot_id
            or row["source_record_id"] != digest_id(
                "sr",
                row["source_file_id"],
                row["record_pointer"],
            )
            or row["source_file_id"] != digest_id(
                "sf", run_snapshot_id, row["relative_path"]
            )
        ):
            return False
        locale_paths = textmap_paths.get(row["locale"]) if isinstance(textmap_paths, dict) else None
        if isinstance(locale_paths, dict) and row["relative_path"] != locale_paths.get(row["shard_kind"]):
            return False
        expected_id = digest_id(
            "static-text-candidate",
            run_snapshot_id,
            manifest.raw_sha256,
            run_id,
            row["resolution_occurrence_id"],
            row["relative_path"],
            row["source_pointer"],
            row["shard_kind"],
        )
        if row["id"] != expected_id:
            return False
    return True


def _b6_audit_matches_manifest(
    audited: dict[str, Any],
    identity: dict[str, Any],
    manifest: Any,
) -> bool:
    """Validate the sealed verifier result without rescanning its occurrence sets."""
    expected = getattr(manifest, "expected", None)
    expected_counts = _phase4b6_expected_counts(manifest)
    if not isinstance(expected, dict) or expected_counts is None:
        return False

    expected_families = expected.get("families")
    if expected_families is None:
        expected_families = expected.get("family_counts")
    if not isinstance(expected_families, dict):
        return False

    expected_paths = expected.get("family_selected_paths")
    if expected_paths is None:
        expected_paths = {
            "materials": sorted(str(item.relative_path) for item in manifest.files)
        }

    expected_statuses: dict[str, Any] = {}
    relationship_statuses = expected.get("relationship_statuses")
    text_resolution_statuses = expected.get("text_resolution_statuses")
    nested_statuses = expected.get("status_counts")
    if relationship_statuses is None and isinstance(nested_statuses, dict):
        relationship_statuses = nested_statuses.get("relationships")
    if text_resolution_statuses is None and isinstance(nested_statuses, dict):
        text_resolution_statuses = nested_statuses.get("text_resolutions")
    if relationship_statuses is not None:
        expected_statuses["relationships"] = relationship_statuses
    if text_resolution_statuses is not None:
        expected_statuses["text_resolutions"] = text_resolution_statuses

    audited_statuses = audited.get("status_counts")
    if not isinstance(audited_statuses, dict):
        return False
    if any(audited_statuses.get(key) != value for key, value in expected_statuses.items()):
        return False

    return (
        audited.get("identity") == identity
        and audited.get("counts") == expected_counts
        and audited.get("inventory_counts") == expected_counts
        and audited.get("family_counts") == expected_families
        and audited.get("family_inventory_counts") == expected_families
        and audited.get("family_selected_paths") == expected_paths
        and audited.get("occurrence_set_digests") == expected.get("occurrence_set_digests")
        and audited.get("foreign_key_check") == []
    )


def _b6_family_coverage(
    db: Any,
    run_row: Any,
    run_scope: dict[str, Any],
    run_config: dict[str, Any],
) -> dict[str, str] | None:
    """Deprecated compatibility hook; runtime eligibility uses the registry."""

    # This private hook was the old runtime B6 audit entry point.  Keep its
    # name for downstream test/diagnostic imports, but make it inert so a
    # caller cannot accidentally reintroduce a full occurrence audit into MCP
    # capability inspection.  Release/build code calls the verifier directly.
    return None


def inspect_static_capabilities(db: Any, prequalified_artifact_seal: StaticArtifactSeal | None = None) -> dict[str, Any]:
    """Inspect only the fixed Static contracts and immutable metadata.

    The B1--B6 release verifiers remain build/release authorities.  Runtime
    eligibility deliberately does not call one of them and does not query a
    Static occurrence table; a successful result means only that a previously
    sealed run is pinned to this database's immutable ledger.
    """

    schema_supported = exact_static_schema(db.conn)
    if not schema_supported:
        return {
            "schema_supported": False,
            "evidence_available": False,
            "completed_ingest_runs": [],
            "family_coverage": {},
            "data_families": ["talk", "quest"],
            "static_families": {},
            "eligible_runs": [],
            "artifact_seal": None,
        }

    # A metadata tuple is not sufficient to establish that the occurrence
    # tables belong to the separately released artifact.  The one-time
    # connection snapshot therefore requires a stable, sidecar-free file
    # content seal before consulting any run metadata.
    artifact_seal = prequalified_artifact_seal or match_static_artifact(getattr(db, "path", None))
    if artifact_seal is None:
        # Schema 11 is shared with the additive Tutorial canary.  A
        # Tutorial-only Schema 11 database must not make the pre-existing
        # Static surface appear available merely because its structural prefix
        # is exact; Static qualification on this schema is additionally pinned
        # to the reviewed M5 file seal.  Keep the historical Schema 9/10
        # schema-only behavior unchanged.
        try:
            latest_schema_version = int(
                db.conn.execute(
                    "SELECT MAX(version) FROM schema_migration"
                ).fetchone()[0]
            )
        except (AttributeError, KeyError, TypeError, ValueError, sqlite3.Error):
            latest_schema_version = None
        schema_available = latest_schema_version != 11
        return {
            "schema_supported": schema_available,
            "evidence_available": False,
            "completed_ingest_runs": [],
            "family_coverage": {},
            "data_families": ["talk", "quest"],
            "static_families": {},
            "eligible_runs": [],
            "artifact_seal": None,
        }

    eligible_bindings: list[dict[str, Any]] = []
    static_families: dict[str, str] = {}
    for contract in STATIC_RUN_CONTRACTS:
        binding = qualify_static_run(db.conn, contract)
        if binding is None:
            continue
        binding["artifact_seal"] = artifact_seal.as_dict()
        eligible_bindings.append(binding)
        for family, status in contract.family_coverage.items():
            old_status = static_families.get(family)
            if old_status is None or _STATIC_COVERAGE_RANK[status] > _STATIC_COVERAGE_RANK[old_status]:
                static_families[family] = status

    completed_static_runs = [str(item["ingest_run_id"]) for item in eligible_bindings]
    data_families = ["talk", "quest"]
    if "books" in static_families:
        data_families.append("book")
    if "weapons" in static_families:
        data_families.append("weapon")
    if "reliquaries" in static_families:
        data_families.append("reliquary")
    if "materials" in static_families:
        data_families.append("material")
    if static_families:
        data_families.append("static_lore")

    return {
        "schema_supported": True,
        "evidence_available": bool(eligible_bindings),
        "completed_ingest_runs": completed_static_runs,
        "family_coverage": static_families,
        "data_families": data_families,
        "static_families": static_families,
        # Kept internal for diagnostics and focused tests.  MCP's established
        # static_capability payload remains byte-for-byte compatible.
        "eligible_runs": eligible_bindings,
        "artifact_seal": artifact_seal.as_dict(),
    }


def get_static_relationship_evidence(
    db: Any,
    *,
    ingest_run_id: str,
    relationship_occurrence_id: str,
) -> dict[str, Any] | None:
    """Read complete evidence for a single relationship occurrence within explicit ingest_run_id."""
    if not ingest_run_id or not isinstance(ingest_run_id, str):
        raise ValueError("ingest_run_id must be a non-empty string")
    if not relationship_occurrence_id or not isinstance(relationship_occurrence_id, str):
        raise ValueError("relationship_occurrence_id must be a non-empty string")

    sql = f"""
        SELECT sro.*,
               sec.container_kind AS source_container_kind,
               sec.candidate_namespace AS source_candidate_namespace,
               sec.candidate_key_json AS source_candidate_key_json,
               sec.metadata_json AS source_container_metadata_json,
               sf.relative_path AS source_file_relative_path,
               ir.parser_build_id, ir.scope_json AS run_scope_json,
               ir.config_json AS run_config_json, ir.status AS run_status,
               ir.created_at AS run_created_at,
               ss.source_id AS snapshot_source_id,
               ss.release_label AS snapshot_release_label,
               ss.build AS snapshot_build, ss."commit" AS snapshot_commit,
               ss.tree AS snapshot_tree, ss.observed_at AS snapshot_observed_at,
               ss.parser_version AS snapshot_parser_version,
               ss.mapping_version AS snapshot_mapping_version,
               ss.status AS snapshot_status,
               ss.metadata_json AS snapshot_metadata_json
          FROM static_relationship_occurrence sro
          JOIN static_evidence_container sec ON sec.id=sro.source_container_id AND sec.ingest_run_id=sro.ingest_run_id
          JOIN ingest_run ir ON ir.id=sro.ingest_run_id
          JOIN source_snapshot ss ON ss.id=sro.snapshot_id
          JOIN source_record sr ON sr.id=sro.source_record_id
          JOIN source_file sf ON sf.id=sec.source_file_id
         WHERE sro.id = ? AND sro.ingest_run_id = ?
    """
    row = db.conn.execute(sql, (str(relationship_occurrence_id), str(ingest_run_id))).fetchone()
    if row is None:
        return None

    relationship = _relationship_item(db, row, include_provenance=False)

    source_provenance = {
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
        "ingest_run": {
            "id": row["ingest_run_id"],
            "snapshot_id": row["snapshot_id"],
            "parser_build_id": row["parser_build_id"],
            "scope": strict_json_object(row["run_scope_json"]),
            "config": strict_json_object(row["run_config_json"]),
            "status": row["run_status"],
            "created_at": row["run_created_at"],
        },
        "source_container": {
            "id": row["source_container_id"],
            "ingest_run_id": row["ingest_run_id"],
            "source_record_id": row["source_record_id"],
            "container_kind": row["source_container_kind"],
            "source_family": row["source_family"],
            "candidate_namespace": row["source_candidate_namespace"],
            "candidate_key": _json(row["source_candidate_key_json"]),
            "metadata": _json(row["source_container_metadata_json"], {}),
        },
        "source_file_relative_path": row["source_file_relative_path"],
        "source_record": _source(db, row["source_record_id"]),
        "source_pointer": row["source_pointer"],
    }

    # Target container resolution
    target_container: dict[str, Any] | None = None
    if row["target_evidence_container_id"] is not None:
        t_row = db.conn.execute(
            """SELECT id, ingest_run_id, snapshot_id, source_file_id,
                      source_record_id, container_kind, source_family,
                      locale, candidate_namespace, candidate_key_json, metadata_json
                 FROM static_evidence_container
                WHERE id = ? AND snapshot_id = ?""",
            (row["target_evidence_container_id"], row["snapshot_id"]),
        ).fetchone()
        if t_row is not None:
            target_container = _container_item(db, t_row, include_scope=False)

    target_resolution = {
        "status": row["candidate_resolution_status"],
        "target_family": row["target_family"],
        "target_candidate_key": _json(row["target_candidate_key_json"]),
        "target_evidence_container_id": row["target_evidence_container_id"],
        "target_container": target_container,
        "ambiguity": _json(row["ambiguity_json"], {}),
    }

    # Query TextMap resolutions and candidates
    res_rows = db.conn.execute(
        """SELECT * FROM static_text_resolution_occurrence
            WHERE ingest_run_id = ?
              AND static_relationship_occurrence_id = ?
            ORDER BY locale ASC, id ASC""",
        (row["ingest_run_id"], row["id"]),
    ).fetchall()

    text_resolutions: list[dict[str, Any]] = []
    for r in res_rows:
        cand_rows = db.conn.execute(
            """SELECT * FROM static_text_candidate_occurrence
                WHERE resolution_occurrence_id = ?
                  AND ingest_run_id = ?
                ORDER BY shard_kind ASC, id ASC""",
            (r["id"], row["ingest_run_id"]),
        ).fetchall()
        candidates = [
            {
                "id": c["id"],
                "resolution_occurrence_id": c["resolution_occurrence_id"],
                "ingest_run_id": c["ingest_run_id"],
                "snapshot_id": c["snapshot_id"],
                "source_file_id": c["source_file_id"],
                "source_record_id": c["source_record_id"],
                "source_pointer": c["source_pointer"],
                "shard_kind": c["shard_kind"],
                "locale": c["locale"],
                "resolution_status": c["resolution_status"],
                "raw_text": c["raw_text"],
                "normalized_text": c["normalized_text"],
                "raw_checksum": c["raw_checksum"],
                "normalized_checksum": c["normalized_checksum"],
                "metadata": _json(c["metadata_json"], {}),
                "provenance": _source(db, c["source_record_id"]),
            }
            for c in cand_rows
        ]
        text_resolutions.append(
            {
                "id": r["id"],
                "ingest_run_id": r["ingest_run_id"],
                "snapshot_id": r["snapshot_id"],
                "static_relationship_occurrence_id": r["static_relationship_occurrence_id"],
                "source_container_id": r["source_container_id"],
                "source_record_id": r["source_record_id"],
                "source_pointer": r["source_pointer"],
                "locale": r["locale"],
                "textmap_hash": r["textmap_hash"],
                "resolution_status": r["resolution_status"],
                "raw_text": r["raw_text"],
                "normalized_text": r["normalized_text"],
                "raw_checksum": r["raw_checksum"],
                "normalized_checksum": r["normalized_checksum"],
                "metadata": _json(r["metadata_json"], {}),
                "candidates": candidates,
            }
        )

    return {
        "relationship": relationship,
        "provenance": source_provenance,
        "target_resolution": target_resolution,
        "text_resolutions": text_resolutions,
    }
