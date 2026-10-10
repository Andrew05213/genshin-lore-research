"""Bounded read-only retrieval for the reviewed Character canary runs.

This module exposes raw, run-scoped occurrences only.  It deliberately does
not call a corpus verifier or promote native locators, text, voice slots,
selectors, paths, or checksums to logical identity.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from ._readonly_database import ReadonlyDatabase
from .character_contracts import (
    C2_CONTRACT,
    CHARACTER_RUN_CONTRACTS,
    CHARACTER_RUN_CONTRACT_BY_ID,
    LOCALES,
    exact_schema_10,
    exact_schema_10_or_11,
    family_run_ids,
    row_matches_contract,
)
from .character_phase4c2_manifest import (
    EXPECTED_AVATAR_ID,
    EXPECTED_VERSIONS,
)
from .util import text_occurrences


# These names remain C2 compatibility aliases for callers that imported the
# original single-run module. Qualification itself uses the explicit two-entry
# registry and never treats these aliases as defaults.
RUN_FAMILY = C2_CONTRACT.run_family
MANIFEST_ID = C2_CONTRACT.manifest_id
AVATAR_ID = C2_CONTRACT.boundary["avatar_id"]

_SNAPSHOT_ID = C2_CONTRACT.snapshot_id
_PARSER_BUILD_ID = C2_CONTRACT.parser_build_id
_INGEST_RUN_ID = C2_CONTRACT.ingest_run_id
_CONFIG_CHECKSUM = C2_CONTRACT.config_checksum
_MANIFEST_SHA256 = C2_CONTRACT.manifest_raw_sha256
_PARSER_VERSION = C2_CONTRACT.parser_version
_MAPPING_VERSION = C2_CONTRACT.mapping_version
_PARSER_FINGERPRINT = C2_CONTRACT.parser_fingerprint
_SOURCE_BUILD = C2_CONTRACT.source_build
_SOURCE_COMMIT = C2_CONTRACT.source_commit
_SOURCE_TREE = C2_CONTRACT.source_tree
_SOURCE_UPSTREAM = C2_CONTRACT.source["upstream"]
_OBSERVED_AT = C2_CONTRACT.source["observed_at"]
_SNAPSHOT_METADATA = {"sealed_source_manifest": True}
_VERSIONS = dict(EXPECTED_VERSIONS)
_SELECTED_PATHS = list(C2_CONTRACT.scope["selected_paths"])
_EXPECTED_SCOPE = dict(C2_CONTRACT.scope)
_EXPECTED_CONFIG = dict(C2_CONTRACT.config)



@dataclass(frozen=True)
class CharacterNotFound:
    """A legal bounded Character lookup result with no eligible occurrence."""

    reason: str
    lookup: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "NotFound",
            "reason": self.reason,
            "lookup": self.lookup,
            "evidence_only": True,
            "coverage": "canary_only",
            "corpus_complete": False,
        }


@dataclass(frozen=True)
class CharacterCapabilitySnapshot:
    """Immutable startup qualification for the reviewed Character runs."""

    schema_supported: bool
    evidence_available: bool
    coverage: str
    # Serialized list of eligible-run binding objects.  The name is retained
    # privately so old positional construction remains source-compatible.
    _binding_json: str | None = None

    @property
    def corpus_complete(self) -> bool:
        return any(
            item.get("corpus_complete") is True
            for item in self.eligible_runs
        )

    @property
    def eligible(self) -> bool:
        return self.evidence_available and self.coverage in {
            "canary_only",
            "declared_character_fetter_source_closure_complete",
        }

    @property
    def eligible_runs(self) -> list[dict[str, Any]]:
        if self._binding_json is None:
            return []
        decoded = json.loads(self._binding_json)
        if isinstance(decoded, list):
            return decoded
        # Compatibility for tests/callers that constructed a pre-4C4C
        # snapshot with one nested binding object.
        return [decoded] if isinstance(decoded, dict) else []

    @property
    def binding(self) -> None:
        """There is intentionally no singleton/default Character binding."""

        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_supported": self.schema_supported,
            "evidence_available": self.evidence_available,
            "coverage": self.coverage,
            "corpus_complete": self.corpus_complete,
            # Kept as a null compatibility sentinel; no singleton/default
            # binding is exposed. Callers must use eligible_runs.
            "binding": None,
            "eligible_runs": self.eligible_runs,
            "explicit_ingest_run_required": True,
            "default_run": False,
            "latest_run": False,
            "boundary": {
                "locales": list(LOCALES),
                "full_character_scan": False,
                "full_voice_scan": False,
                "per_run": [
                    {
                        "run_family": item.get("run_family"),
                        "ingest_run_id": item.get("ingest_run_id"),
                        "manifest_id": item.get("manifest_id"),
                        "coverage": item.get("coverage", "canary_only"),
                        "corpus_complete": item.get("corpus_complete", False),
                        "boundary": item.get("boundary", {}),
                    }
                    for item in self.eligible_runs
                ],
            },
            "selection": {
                "ingest_run_id": "required",
                "explicit_ingest_run_required": True,
                "default_run": False,
                "latest_run": False,
                "sibling_fallback": False,
            },
        }



def _json(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(value)


def _require_text(value: str, label: str) -> str:
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _page(limit: int, offset: int, *, maximum: int) -> tuple[int, int]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be non-negative")
    return limit, offset


def _window(before: int, after: int) -> tuple[int, int]:
    for value, label in ((before, "before"), (after, "after")):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 50:
            raise ValueError(f"{label} must be between 0 and 50")
    return before, after


_RUN_SELECT = """
    SELECT ir.id, ir.snapshot_id, ir.parser_build_id, ir.scope_json,
           ir.config_json, ir.status AS run_status,
           ir.created_at AS run_created_at,
           pb.parser_version, pb.mapping_version, pb.fingerprint,
           pb.config_checksum, pb.created_at AS parser_created_at,
           ss.source_id, ss.release_label, ss.build,
           ss."commit" AS snapshot_commit, ss.tree AS snapshot_tree,
           ss.observed_at, ss.status AS snapshot_status,
           ss.metadata_json AS snapshot_metadata_json,
           sc.kind AS source_kind, sc.url AS source_url,
           sc.license_note AS source_license_note
      FROM ingest_run ir
      JOIN parser_build pb ON pb.id=ir.parser_build_id
      JOIN source_snapshot ss ON ss.id=ir.snapshot_id
      JOIN source_catalog sc ON sc.id=ss.source_id
     WHERE ir.id=?
"""


def _exact_schema_10(conn: Any) -> bool:
    # Let a corrupt/non-SQLite file remain a SQLite error at the CLI boundary.
    return exact_schema_10(conn)


def _exact_schema_supported(conn: Any) -> bool:
    """Accept historical Schema 10 or the exact combined M5 Schema 11."""
    return exact_schema_10_or_11(conn)


def _same_family_run_ids(
    conn: Any,
    run_family: str = RUN_FAMILY,
) -> list[str] | None:
    return family_run_ids(conn, run_family)


def _qualified_scope(
    db: ReadonlyDatabase,
    ingest_run_id: str,
    operation: str,
) -> dict[str, Any] | CharacterNotFound:
    requested = _require_text(ingest_run_id, "ingest_run_id")
    lookup = {"operation": operation, "ingest_run_id": requested}
    if not _exact_schema_supported(db.conn):
        return CharacterNotFound(
            "character_schema_ineligible",
            {**lookup, "required_schema_version": 10},
        )
    return _qualified_scope_with_known_schema(db, requested, lookup)


def _qualified_scope_with_known_schema(
    db: ReadonlyDatabase,
    requested: str,
    lookup: dict[str, Any],
) -> dict[str, Any] | CharacterNotFound:
    row = db.conn.execute(_RUN_SELECT, (requested,)).fetchone()
    if row is None:
        return CharacterNotFound("ingest_run_not_found", lookup)
    contract = CHARACTER_RUN_CONTRACT_BY_ID.get(requested)
    if contract is None:
        return CharacterNotFound("ingest_run_ineligible", lookup)
    try:
        scope = _json(row["scope_json"])
        config = _json(row["config_json"])
        snapshot_metadata = _json(row["snapshot_metadata_json"], {})
    except (TypeError, json.JSONDecodeError):
        return CharacterNotFound("ingest_run_ineligible", lookup)
    if not isinstance(scope, dict) or not isinstance(config, dict):
        return CharacterNotFound("ingest_run_ineligible", lookup)

    eligible = row_matches_contract(
        row,
        scope,
        config,
        snapshot_metadata,
        contract,
        _same_family_run_ids(db.conn, contract.run_family),
    )
    if not eligible:
        return CharacterNotFound("ingest_run_ineligible", lookup)

    return {
        "ingest_run": {
            "id": row["id"],
            "snapshot_id": row["snapshot_id"],
            "parser_build_id": row["parser_build_id"],
            "status": row["run_status"],
            "created_at": row["run_created_at"],
            "scope": scope,
            "config": config,
        },
        "parser_build": {
            "id": row["parser_build_id"],
            "parser_version": row["parser_version"],
            "mapping_version": row["mapping_version"],
            "fingerprint": row["fingerprint"],
            "config_checksum": row["config_checksum"],
            "created_at": row["parser_created_at"],
        },
        "source_snapshot": {
            "id": row["snapshot_id"],
            "source_id": row["source_id"],
            "source_kind": row["source_kind"],
            "source_url": row["source_url"],
            "release_label": row["release_label"],
            "build": row["build"],
            "commit": row["snapshot_commit"],
            "tree": row["snapshot_tree"],
            "observed_at": row["observed_at"],
            "status": row["snapshot_status"],
            "metadata": snapshot_metadata,
        },
        "character_contract": {
            "run_family": contract.run_family,
            "manifest_id": contract.manifest_id,
            "manifest_raw_sha256": contract.manifest_raw_sha256,
            "ingest_run_id": contract.ingest_run_id,
            "snapshot_id": contract.snapshot_id,
            "parser_build_id": contract.parser_build_id,
            "config_checksum": contract.config_checksum,
            "coverage": contract.coverage,
            "corpus_complete": contract.corpus_complete,
            "boundary": contract.boundary_public(),
        },
    }


def inspect_character_capability(
    db: ReadonlyDatabase,
) -> CharacterCapabilitySnapshot:
    """Qualify the explicit C2/C4B/C5B bindings without auditing occurrences."""

    if not _exact_schema_supported(db.conn):
        return CharacterCapabilitySnapshot(False, False, "unavailable", "[]")

    bindings: list[dict[str, Any]] = []
    for contract in CHARACTER_RUN_CONTRACTS:
        lookup = {
            "operation": "inspect_character_capability",
            "ingest_run_id": contract.ingest_run_id,
        }
        scope = _qualified_scope_with_known_schema(
            db, contract.ingest_run_id, lookup
        )
        if isinstance(scope, CharacterNotFound):
            continue
        bindings.append(contract.binding_from_scope(scope))
    serialized = json.dumps(
        bindings,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    complete_bindings = [
        item for item in bindings if item.get("corpus_complete") is True
    ]
    coverage = (
        complete_bindings[0]["coverage"]
        if complete_bindings
        else ("canary_only" if bindings else "unavailable")
    )
    return CharacterCapabilitySnapshot(
        True,
        bool(bindings),
        coverage,
        serialized,
    )


def _success(scope: dict[str, Any], **payload: Any) -> dict[str, Any]:
    contract = scope["character_contract"]
    # Contract metadata is an internal qualification detail. Keep the
    # occurrence handler scope shape byte-for-byte compatible with C2 while
    # selecting the family-specific boundary above.
    public_scope = {
        key: value for key, value in scope.items() if key != "character_contract"
    }
    return {
        "status": "ok",
        "evidence_only": True,
        "coverage": contract["coverage"],
        "corpus_complete": contract["corpus_complete"],
        "boundary": {
            "family": contract["run_family"],
            "manifest_id": contract["manifest_id"],
            **contract["boundary"],
        },
        "scope": public_scope,
        **payload,
    }


def _source_record(
    db: ReadonlyDatabase,
    source_record_id: str,
    *,
    source_file_id: str | None = None,
    source_pointer: str | None = None,
    snapshot_id: str | None = None,
    include_raw_payload: bool = False,
) -> dict[str, Any] | None:
    where = ["sr.id=?"]
    params: list[Any] = [source_record_id]
    if source_file_id is not None:
        where.append("sr.source_file_id=?")
        params.append(source_file_id)
    if source_pointer is not None:
        where.append("sr.pointer=?")
        params.append(source_pointer)
    if snapshot_id is not None:
        where.append("sf.snapshot_id=?")
        params.append(snapshot_id)
    row = db.conn.execute(
        """
        SELECT sr.id, sr.source_file_id, sr.pointer, sr.record_kind,
               sr.native_id, sr.raw_sha256, sr.raw_json,
               sf.snapshot_id, sf.relative_path, sf.upstream_blob_oid,
               sf.sha256 AS file_sha256, sf.size_bytes, sf.format
          FROM source_record sr
          JOIN source_file sf ON sf.id=sr.source_file_id
         WHERE """
        + " AND ".join(where),
        params,
    ).fetchone()
    if row is None:
        return None
    result = {
        "id": row["id"],
        "source_file_id": row["source_file_id"],
        "snapshot_id": row["snapshot_id"],
        "relative_path": row["relative_path"],
        "pointer": row["pointer"],
        "record_kind": row["record_kind"],
        "native_id": row["native_id"],
        "raw_sha256": row["raw_sha256"],
        "file_sha256": row["file_sha256"],
        "upstream_blob_oid": row["upstream_blob_oid"],
        "file_size_bytes": row["size_bytes"],
        "file_format": row["format"],
    }
    if include_raw_payload:
        result["raw_payload"] = _json(row["raw_json"])
    return result


def _row_summary(db: ReadonlyDatabase, row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "ingest_run_id": row["ingest_run_id"],
        "snapshot_id": row["snapshot_id"],
        "source_file_id": row["source_file_id"],
        "source_record_id": row["source_record_id"],
        "source_pointer": row["source_pointer"],
        "table_name": row["table_name"],
        "row_index": row["row_index"],
        "native_locator_field": row["native_locator_field"],
        "native_locator_value": row["native_locator_value"],
        "avatar_id": row["avatar_id"],
        "raw_checksum": row["raw_checksum"],
        "metadata": _json(row["metadata_json"], {}),
        "provenance": _source_record(
            db,
            row["source_record_id"],
            source_file_id=row["source_file_id"],
            source_pointer=row["source_pointer"],
            snapshot_id=row["snapshot_id"],
        ),
    }


def _voice_summary(db: ReadonlyDatabase, row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "ingest_run_id": row["ingest_run_id"],
        "snapshot_id": row["snapshot_id"],
        "voice_slot": row["voice_slot"],
        "resolution_status": row["resolution_status"],
        "candidate_scopes": _json(row["candidate_scopes_json"], {}),
        "metadata": _json(row["metadata_json"], {}),
        "lut_provenance": _source_record(
            db,
            row["lut_source_record_id"],
            source_file_id=row["lut_source_file_id"],
            source_pointer=row["lut_source_pointer"],
            snapshot_id=row["snapshot_id"],
        ),
        "item_provenance": _source_record(
            db,
            row["item_source_record_id"],
            source_file_id=row["item_source_file_id"],
            source_pointer=row["item_source_pointer"],
            snapshot_id=row["snapshot_id"],
        ),
    }


def _audio_item(row: Any, *, anchor_id: str | None = None) -> dict[str, Any]:
    return {
        "id": row["id"],
        "ingest_run_id": row["ingest_run_id"],
        "snapshot_id": row["snapshot_id"],
        "voice_container_id": row["voice_container_id"],
        "item_source_record_id": row["item_source_record_id"],
        "source_pointer": row["source_pointer"],
        "array_index": row["array_index"],
        "audio_path": row["audio_path"],
        "audio_path_status": row["audio_path_status"],
        "character_child_selector": row["character_child_selector"],
        "selector_status": row["selector_status"],
        "metadata": _json(row["metadata_json"], {}),
        **({"is_anchor": row["id"] == anchor_id} if anchor_id is not None else {}),
    }


def _candidate_items(db: ReadonlyDatabase, resolution: Any) -> list[dict[str, Any]]:
    rows = db.conn.execute(
        """
        SELECT c.*
          FROM character_text_candidate_occurrence c
          JOIN source_file sf
            ON sf.id=c.source_file_id
           AND sf.snapshot_id=c.snapshot_id
          JOIN source_record sr
            ON sr.id=c.source_record_id
           AND sr.source_file_id=c.source_file_id
           AND sr.pointer=c.source_pointer
          JOIN character_text_resolution_occurrence tr
            ON tr.id=c.resolution_occurrence_id
           AND tr.ingest_run_id=c.ingest_run_id
           AND tr.snapshot_id=c.snapshot_id
           AND tr.locale=c.locale
           AND tr.resolution_status=c.resolution_status
         WHERE c.resolution_occurrence_id=?
           AND c.ingest_run_id=?
           AND c.snapshot_id=?
           AND c.locale=?
           AND c.resolution_status=?
           AND tr.relationship_occurrence_id=?
           AND tr.source_row_occurrence_id=?
           AND tr.source_record_id=?
           AND tr.source_pointer=?
         ORDER BY c.shard_kind, sf.relative_path, c.source_pointer, c.id
        """,
        (
            resolution["id"],
            resolution["ingest_run_id"],
            resolution["snapshot_id"],
            resolution["locale"],
            resolution["resolution_status"],
            resolution["relationship_occurrence_id"],
            resolution["source_row_occurrence_id"],
            resolution["source_record_id"],
            resolution["source_pointer"],
        ),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "resolution_occurrence_id": row["resolution_occurrence_id"],
            "ingest_run_id": row["ingest_run_id"],
            "snapshot_id": row["snapshot_id"],
            "source_file_id": row["source_file_id"],
            "source_record_id": row["source_record_id"],
            "source_pointer": row["source_pointer"],
            "shard_kind": row["shard_kind"],
            "locale": row["locale"],
            "resolution_status": row["resolution_status"],
            "raw_text": row["raw_text"],
            "normalized_text": row["normalized_text"],
            "raw_checksum": row["raw_checksum"],
            "normalized_checksum": row["normalized_checksum"],
            "metadata": _json(row["metadata_json"], {}),
            "provenance": _source_record(
                db,
                row["source_record_id"],
                source_file_id=row["source_file_id"],
                source_pointer=row["source_pointer"],
                snapshot_id=row["snapshot_id"],
            ),
        }
        for row in rows
    ]


def _text_resolution_item(db: ReadonlyDatabase, row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "ingest_run_id": row["ingest_run_id"],
        "snapshot_id": row["snapshot_id"],
        "relationship_occurrence_id": row["relationship_occurrence_id"],
        "source_row_occurrence_id": row["source_row_occurrence_id"],
        "source_record_id": row["source_record_id"],
        "source_pointer": row["source_pointer"],
        "locale": row["locale"],
        "textmap_hash": row["textmap_hash"],
        "resolution_status": row["resolution_status"],
        "raw_text": row["raw_text"],
        "normalized_text": row["normalized_text"],
        "raw_checksum": row["raw_checksum"],
        "normalized_checksum": row["normalized_checksum"],
        "candidate_scopes": _json(row["candidate_scopes_json"], []),
        "metadata": _json(row["metadata_json"], {}),
        "candidates": _candidate_items(db, row),
    }


def list_character_rows(
    db: ReadonlyDatabase,
    *,
    ingest_run_id: str,
    table_name: str | None = None,
    native_locator_field: str | None = None,
    native_locator_value: str | None = None,
    avatar_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any] | CharacterNotFound:
    """List raw Character row occurrences using exact raw-column filters."""
    limit, offset = _page(limit, offset, maximum=100)
    scope = _qualified_scope(db, ingest_run_id, "list_character_rows")
    if isinstance(scope, CharacterNotFound):
        return scope
    snapshot_id = scope["source_snapshot"]["id"]
    where = ["cro.ingest_run_id=?", "cro.snapshot_id=?"]
    params: list[Any] = [ingest_run_id, snapshot_id]
    for column, value in (
        ("table_name", table_name),
        ("native_locator_field", native_locator_field),
        ("native_locator_value", native_locator_value),
        ("avatar_id", avatar_id),
    ):
        if value is not None:
            where.append(f"cro.{column}=?")
            params.append(str(value))
    where_sql = " AND ".join(where)
    total = int(
        db.conn.execute(
            "SELECT COUNT(*) FROM character_row_occurrence cro "
            "JOIN source_record sr "
            "ON sr.id=cro.source_record_id "
            "AND sr.source_file_id=cro.source_file_id "
            "AND sr.pointer=cro.source_pointer "
            "JOIN source_file sf "
            "ON sf.id=cro.source_file_id "
            "AND sf.snapshot_id=cro.snapshot_id "
            f"WHERE {where_sql}",
            params,
        ).fetchone()[0]
    )
    rows = db.conn.execute(
        "SELECT cro.* FROM character_row_occurrence cro "
        "JOIN source_record sr "
        "ON sr.id=cro.source_record_id "
        "AND sr.source_file_id=cro.source_file_id "
        "AND sr.pointer=cro.source_pointer "
        "JOIN source_file sf "
        "ON sf.id=cro.source_file_id "
        "AND sf.snapshot_id=cro.snapshot_id "
        f"WHERE {where_sql} "
        "ORDER BY cro.table_name, cro.row_index, cro.id LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return _success(
        scope,
        filters={
            "table_name": table_name,
            "native_locator_field": native_locator_field,
            "native_locator_value": native_locator_value,
            "avatar_id": avatar_id,
        },
        items=[_row_summary(db, row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def get_character_row_evidence(
    db: ReadonlyDatabase,
    *,
    ingest_run_id: str,
    row_occurrence_id: str,
    raw_slot_limit: int = 500,
    raw_slot_offset: int = 0,
    relationship_limit: int = 500,
    relationship_offset: int = 0,
) -> dict[str, Any] | CharacterNotFound:
    """Return one raw row and its directly attached occurrence evidence."""
    raw_slot_limit, raw_slot_offset = _page(
        raw_slot_limit, raw_slot_offset, maximum=500
    )
    relationship_limit, relationship_offset = _page(
        relationship_limit, relationship_offset, maximum=500
    )
    requested_id = _require_text(row_occurrence_id, "row_occurrence_id")
    scope = _qualified_scope(db, ingest_run_id, "get_character_row_evidence")
    if isinstance(scope, CharacterNotFound):
        return scope
    snapshot_id = scope["source_snapshot"]["id"]
    row = db.conn.execute(
        "SELECT * FROM character_row_occurrence "
        "WHERE ingest_run_id=? AND snapshot_id=? AND id=?",
        (ingest_run_id, snapshot_id, requested_id),
    ).fetchone()
    if row is None:
        return CharacterNotFound(
            "row_occurrence_not_found",
            {
                "operation": "get_character_row_evidence",
                "ingest_run_id": ingest_run_id,
                "row_occurrence_id": requested_id,
            },
        )
    row_item = _row_summary(db, row)
    if row_item["provenance"] is None:
        return CharacterNotFound(
            "evidence_scope_ineligible",
            {
                "operation": "get_character_row_evidence",
                "ingest_run_id": ingest_run_id,
                "row_occurrence_id": requested_id,
                "source_record_id": row["source_record_id"],
            },
        )
    row_item["provenance"] = _source_record(
        db,
        row["source_record_id"],
        source_file_id=row["source_file_id"],
        source_pointer=row["source_pointer"],
        snapshot_id=row["snapshot_id"],
        include_raw_payload=True,
    )

    slot_total = int(
        db.conn.execute(
            "SELECT COUNT(*) FROM character_raw_slot_occurrence "
            "WHERE ingest_run_id=? AND snapshot_id=? "
            "AND row_occurrence_id=? AND source_record_id=?",
            (
                ingest_run_id,
                snapshot_id,
                requested_id,
                row["source_record_id"],
            ),
        ).fetchone()[0]
    )
    slot_rows = db.conn.execute(
        "SELECT * FROM character_raw_slot_occurrence "
        "WHERE ingest_run_id=? AND snapshot_id=? "
        "AND row_occurrence_id=? AND source_record_id=? "
        "ORDER BY source_pointer, id LIMIT ? OFFSET ?",
        (
            ingest_run_id,
            snapshot_id,
            requested_id,
            row["source_record_id"],
            raw_slot_limit,
            raw_slot_offset,
        ),
    ).fetchall()
    slots = [
        {
            "id": item["id"],
            "ingest_run_id": item["ingest_run_id"],
            "snapshot_id": item["snapshot_id"],
            "row_occurrence_id": item["row_occurrence_id"],
            "source_record_id": item["source_record_id"],
            "source_pointer": item["source_pointer"],
            "field_name": item["field_name"],
            "array_index": item["array_index"],
            "slot_kind": item["slot_kind"],
            "observation_status": item["observation_status"],
            "raw_value": _json(item["raw_json"]),
            "metadata": _json(item["metadata_json"], {}),
            "provenance": row_item["provenance"],
        }
        for item in slot_rows
    ]

    relation_total = int(
        db.conn.execute(
            "SELECT COUNT(*) FROM character_relation_occurrence "
            "WHERE ingest_run_id=? AND snapshot_id=? "
            "AND source_row_occurrence_id=? AND source_record_id=?",
            (
                ingest_run_id,
                snapshot_id,
                requested_id,
                row["source_record_id"],
            ),
        ).fetchone()[0]
    )
    relation_rows = db.conn.execute(
        "SELECT * FROM character_relation_occurrence "
        "WHERE ingest_run_id=? AND snapshot_id=? "
        "AND source_row_occurrence_id=? AND source_record_id=? "
        "ORDER BY source_pointer, relation_kind, id LIMIT ? OFFSET ?",
        (
            ingest_run_id,
            snapshot_id,
            requested_id,
            row["source_record_id"],
            relationship_limit,
            relationship_offset,
        ),
    ).fetchall()
    relationships: list[dict[str, Any]] = []
    for relation in relation_rows:
        target: dict[str, Any] | None = None
        if relation["target_row_occurrence_id"] is not None:
            target_row = db.conn.execute(
                "SELECT * FROM character_row_occurrence "
                "WHERE ingest_run_id=? AND snapshot_id=? AND id=?",
                (
                    ingest_run_id,
                    snapshot_id,
                    relation["target_row_occurrence_id"],
                ),
            ).fetchone()
            if target_row is None:
                return CharacterNotFound(
                    "evidence_scope_ineligible",
                    {
                        "operation": "get_character_row_evidence",
                        "ingest_run_id": ingest_run_id,
                        "row_occurrence_id": requested_id,
                        "relationship_occurrence_id": relation["id"],
                        "target_row_occurrence_id": relation[
                            "target_row_occurrence_id"
                        ],
                    },
                )
            target_row_item = _row_summary(db, target_row)
            if target_row_item["provenance"] is None:
                return CharacterNotFound(
                    "evidence_scope_ineligible",
                    {
                        "operation": "get_character_row_evidence",
                        "ingest_run_id": ingest_run_id,
                        "row_occurrence_id": requested_id,
                        "relationship_occurrence_id": relation["id"],
                        "target_row_occurrence_id": relation[
                            "target_row_occurrence_id"
                        ],
                    },
                )
            target = {"kind": "row_occurrence", **target_row_item}
        elif relation["target_voice_container_id"] is not None:
            target_voice = db.conn.execute(
                "SELECT * FROM character_voice_container_occurrence "
                "WHERE ingest_run_id=? AND snapshot_id=? AND id=?",
                (
                    ingest_run_id,
                    snapshot_id,
                    relation["target_voice_container_id"],
                ),
            ).fetchone()
            if target_voice is None:
                return CharacterNotFound(
                    "evidence_scope_ineligible",
                    {
                        "operation": "get_character_row_evidence",
                        "ingest_run_id": ingest_run_id,
                        "row_occurrence_id": requested_id,
                        "relationship_occurrence_id": relation["id"],
                        "target_voice_container_id": relation[
                            "target_voice_container_id"
                        ],
                    },
                )
            target_voice_item = _voice_summary(db, target_voice)
            if (
                target_voice_item["lut_provenance"] is None
                or target_voice_item["item_provenance"] is None
            ):
                return CharacterNotFound(
                    "evidence_scope_ineligible",
                    {
                        "operation": "get_character_row_evidence",
                        "ingest_run_id": ingest_run_id,
                        "row_occurrence_id": requested_id,
                        "relationship_occurrence_id": relation["id"],
                        "target_voice_container_id": relation[
                            "target_voice_container_id"
                        ],
                    },
                )
            target = {
                "kind": "voice_container_occurrence",
                **target_voice_item,
            }
        text_rows = db.conn.execute(
            "SELECT * FROM character_text_resolution_occurrence "
            "WHERE ingest_run_id=? AND snapshot_id=? "
            "AND relationship_occurrence_id=? "
            "AND source_row_occurrence_id=? "
            "AND source_record_id=? AND source_pointer=? "
            "ORDER BY locale, source_pointer, id",
            (
                ingest_run_id,
                snapshot_id,
                relation["id"],
                requested_id,
                row["source_record_id"],
                relation["source_pointer"],
            ),
        ).fetchall()
        relationships.append(
            {
                "id": relation["id"],
                "ingest_run_id": relation["ingest_run_id"],
                "snapshot_id": relation["snapshot_id"],
                "source_row_occurrence_id": relation["source_row_occurrence_id"],
                "source_record_id": relation["source_record_id"],
                "source_pointer": relation["source_pointer"],
                "relation_kind": relation["relation_kind"],
                "field_role": relation["field_role"],
                "target_family": relation["target_family"],
                "target_candidate_key": _json(relation["target_candidate_key_json"], {}),
                "resolution_status": relation["resolution_status"],
                "candidate_scopes": _json(relation["candidate_scopes_json"]),
                "metadata": _json(relation["metadata_json"], {}),
                "provenance": row_item["provenance"],
                "direct_target_occurrence": target,
                "text_resolutions": [
                    _text_resolution_item(db, text_row) for text_row in text_rows
                ],
            }
        )
    return _success(
        scope,
        row=row_item,
        raw_slots={
            "items": slots,
            "total": slot_total,
            "limit": raw_slot_limit,
            "offset": raw_slot_offset,
        },
        relationships={
            "items": relationships,
            "total": relation_total,
            "limit": relationship_limit,
            "offset": relationship_offset,
        },
    )


def search_character_text_evidence(
    db: ReadonlyDatabase,
    *,
    ingest_run_id: str,
    locale: str,
    query: str,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any] | CharacterNotFound:
    """Search resolution raw_text with literal, case-sensitive SQLite instr."""
    limit, offset = _page(limit, offset, maximum=100)
    literal = _require_text(query, "query")
    if locale not in LOCALES:
        raise ValueError(f"locale must be one of: {', '.join(LOCALES)}")
    scope = _qualified_scope(db, ingest_run_id, "search_character_text_evidence")
    if isinstance(scope, CharacterNotFound):
        return scope
    snapshot_id = scope["source_snapshot"]["id"]
    base = """
          FROM character_text_resolution_occurrence tr
          JOIN character_row_occurrence cro
            ON cro.id=tr.source_row_occurrence_id
           AND cro.ingest_run_id=tr.ingest_run_id
           AND cro.snapshot_id=tr.snapshot_id
           AND cro.source_record_id=tr.source_record_id
          JOIN character_relation_occurrence rel
            ON rel.id=tr.relationship_occurrence_id
           AND rel.ingest_run_id=tr.ingest_run_id
           AND rel.snapshot_id=tr.snapshot_id
           AND rel.source_row_occurrence_id=tr.source_row_occurrence_id
           AND rel.source_record_id=tr.source_record_id
           AND rel.source_pointer=tr.source_pointer
          JOIN source_record sr
            ON sr.id=cro.source_record_id
           AND sr.source_file_id=cro.source_file_id
           AND sr.pointer=cro.source_pointer
          JOIN source_file sf
            ON sf.id=cro.source_file_id
           AND sf.snapshot_id=cro.snapshot_id
         WHERE tr.ingest_run_id=? AND tr.snapshot_id=? AND tr.locale=?
           AND instr(tr.raw_text, ?) > 0
    """
    params = (ingest_run_id, snapshot_id, locale, literal)
    total = int(db.conn.execute("SELECT COUNT(*) " + base, params).fetchone()[0])
    rows = db.conn.execute(
        "SELECT tr.*, cro.table_name, cro.row_index, cro.source_file_id, "
        "cro.source_pointer AS row_source_pointer, "
        "cro.native_locator_field, cro.native_locator_value, cro.avatar_id, "
        "cro.raw_checksum AS row_raw_checksum, cro.metadata_json AS row_metadata_json "
        + base
        + " ORDER BY tr.locale, cro.table_name, cro.row_index, "
        "tr.source_pointer, tr.id LIMIT ? OFFSET ?",
        params + (limit, offset),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        resolution = _text_resolution_item(db, row)
        resolution["matches"] = [
            {"start": start, "end": end}
            for start, end in text_occurrences(row["raw_text"], literal)
        ]
        resolution["row"] = {
            "id": row["source_row_occurrence_id"],
            "ingest_run_id": row["ingest_run_id"],
            "snapshot_id": row["snapshot_id"],
            "source_file_id": row["source_file_id"],
            "source_record_id": row["source_record_id"],
            "source_pointer": row["row_source_pointer"],
            "table_name": row["table_name"],
            "row_index": row["row_index"],
            "native_locator_field": row["native_locator_field"],
            "native_locator_value": row["native_locator_value"],
            "avatar_id": row["avatar_id"],
            "raw_checksum": row["row_raw_checksum"],
            "metadata": _json(row["row_metadata_json"], {}),
            "provenance": _source_record(
                db,
                row["source_record_id"],
                source_file_id=row["source_file_id"],
                snapshot_id=row["snapshot_id"],
            ),
        }
        items.append(resolution)
    return _success(
        scope,
        query={
            "locale": locale,
            "literal": literal,
            "semantics": "case_sensitive_literal_instr_raw_text",
        },
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


def get_character_voice_container(
    db: ReadonlyDatabase,
    *,
    ingest_run_id: str,
    voice_container_id: str,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any] | CharacterNotFound:
    """Return one physical Voice container and its occurrence children."""
    limit, offset = _page(limit, offset, maximum=500)
    requested_id = _require_text(voice_container_id, "voice_container_id")
    scope = _qualified_scope(db, ingest_run_id, "get_character_voice_container")
    if isinstance(scope, CharacterNotFound):
        return scope
    snapshot_id = scope["source_snapshot"]["id"]
    row = db.conn.execute(
        "SELECT * FROM character_voice_container_occurrence "
        "WHERE ingest_run_id=? AND snapshot_id=? AND id=?",
        (ingest_run_id, snapshot_id, requested_id),
    ).fetchone()
    if row is None:
        return CharacterNotFound(
            "voice_container_not_found",
            {
                "operation": "get_character_voice_container",
                "ingest_run_id": ingest_run_id,
                "voice_container_id": requested_id,
            },
        )
    voice_item = _voice_summary(db, row)
    if (
        voice_item["lut_provenance"] is None
        or voice_item["item_provenance"] is None
    ):
        return CharacterNotFound(
            "evidence_scope_ineligible",
            {
                "operation": "get_character_voice_container",
                "ingest_run_id": ingest_run_id,
                "voice_container_id": requested_id,
            },
        )
    total = int(
        db.conn.execute(
            "SELECT COUNT(*) FROM character_audio_variant_occurrence "
            "WHERE ingest_run_id=? AND snapshot_id=? "
            "AND voice_container_id=? AND item_source_record_id=?",
            (
                ingest_run_id,
                snapshot_id,
                requested_id,
                row["item_source_record_id"],
            ),
        ).fetchone()[0]
    )
    audio = db.conn.execute(
        "SELECT * FROM character_audio_variant_occurrence "
        "WHERE ingest_run_id=? AND snapshot_id=? "
        "AND voice_container_id=? AND item_source_record_id=? "
        "ORDER BY array_index, id LIMIT ? OFFSET ?",
        (
            ingest_run_id,
            snapshot_id,
            requested_id,
            row["item_source_record_id"],
            limit,
            offset,
        ),
    ).fetchall()
    return _success(
        scope,
        voice_container=voice_item,
        audio_variants={
            "items": [_audio_item(item) for item in audio],
            "total": total,
            "limit": limit,
            "offset": offset,
        },
    )


def get_character_voice_context(
    db: ReadonlyDatabase,
    *,
    ingest_run_id: str,
    audio_variant_occurrence_id: str,
    before: int = 2,
    after: int = 2,
) -> dict[str, Any] | CharacterNotFound:
    """Window one audio occurrence inside exactly one physical container."""
    before, after = _window(before, after)
    anchor_id = _require_text(
        audio_variant_occurrence_id,
        "audio_variant_occurrence_id",
    )
    scope = _qualified_scope(db, ingest_run_id, "get_character_voice_context")
    if isinstance(scope, CharacterNotFound):
        return scope
    snapshot_id = scope["source_snapshot"]["id"]
    anchor = db.conn.execute(
        "SELECT * FROM character_audio_variant_occurrence "
        "WHERE ingest_run_id=? AND snapshot_id=? AND id=?",
        (ingest_run_id, snapshot_id, anchor_id),
    ).fetchone()
    if anchor is None:
        return CharacterNotFound(
            "audio_variant_occurrence_not_found",
            {
                "operation": "get_character_voice_context",
                "ingest_run_id": ingest_run_id,
                "audio_variant_occurrence_id": anchor_id,
            },
        )
    container = db.conn.execute(
        "SELECT * FROM character_voice_container_occurrence "
        "WHERE ingest_run_id=? AND snapshot_id=? AND id=? "
        "AND item_source_record_id=?",
        (
            ingest_run_id,
            snapshot_id,
            anchor["voice_container_id"],
            anchor["item_source_record_id"],
        ),
    ).fetchone()
    if container is None:
        return CharacterNotFound(
            "evidence_scope_ineligible",
            {
                "operation": "get_character_voice_context",
                "ingest_run_id": ingest_run_id,
                "audio_variant_occurrence_id": anchor_id,
                "voice_container_id": anchor["voice_container_id"],
            },
        )
    container_item = _voice_summary(db, container)
    if (
        container_item["lut_provenance"] is None
        or container_item["item_provenance"] is None
    ):
        return CharacterNotFound(
            "evidence_scope_ineligible",
            {
                "operation": "get_character_voice_context",
                "ingest_run_id": ingest_run_id,
                "audio_variant_occurrence_id": anchor_id,
                "voice_container_id": anchor["voice_container_id"],
            },
        )
    previous = db.conn.execute(
        "SELECT * FROM character_audio_variant_occurrence "
        "WHERE ingest_run_id=? AND snapshot_id=? "
        "AND voice_container_id=? AND item_source_record_id=? "
        "AND (array_index < ? OR (array_index=? AND id < ?)) "
        "ORDER BY array_index DESC, id DESC LIMIT ?",
        (
            ingest_run_id,
            snapshot_id,
            anchor["voice_container_id"],
            anchor["item_source_record_id"],
            anchor["array_index"],
            anchor["array_index"],
            anchor_id,
            before,
        ),
    ).fetchall()
    following = db.conn.execute(
        "SELECT * FROM character_audio_variant_occurrence "
        "WHERE ingest_run_id=? AND snapshot_id=? "
        "AND voice_container_id=? AND item_source_record_id=? "
        "AND (array_index > ? OR (array_index=? AND id > ?)) "
        "ORDER BY array_index, id LIMIT ?",
        (
            ingest_run_id,
            snapshot_id,
            anchor["voice_container_id"],
            anchor["item_source_record_id"],
            anchor["array_index"],
            anchor["array_index"],
            anchor_id,
            after,
        ),
    ).fetchall()
    window_rows = [*reversed(previous), anchor, *following]
    return _success(
        scope,
        context_scope="single_character_voice_container_occurrence",
        voice_container=container_item,
        anchor=_audio_item(anchor, anchor_id=anchor_id),
        window={
            "before": before,
            "after": after,
            "items": [_audio_item(item, anchor_id=anchor_id) for item in window_rows],
        },
    )


__all__ = [
    "CharacterCapabilitySnapshot",
    "CharacterNotFound",
    "get_character_row_evidence",
    "get_character_voice_container",
    "get_character_voice_context",
    "inspect_character_capability",
    "list_character_rows",
    "search_character_text_evidence",
]
