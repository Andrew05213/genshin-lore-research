"""Read-only retrieval for the sealed Phase 4D2B Core Tutorial boundary.

The four functions in this module expose physical Tutorial evidence only.  A
request must name an ``ingest_run_id`` explicitly; there is no latest/default
run selection and no sibling fallback.  Qualification is deliberately
metadata-only: it checks the exact Schema 11 seal and the already-sealed
Phase 4D2B run tuple, but never runs the (source-scanning) M2 release
verifier or builder.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from ._readonly_database import ReadonlyDatabase
from .batch_m5d_runtime import BatchM5DError, _verify_schema17 as verify_schema17
from .db import validate_exact_schema_structure
from .tutorial_phase4d2a_contract import occurrence_id
from .tutorial_phase4d2a_schema import (
    TutorialPhase4D2ASchemaError,
    verify_tutorial_phase4d2a_schema_signature,
)
from .tutorial_phase4d2b_contract import (
    PARSER_FINGERPRINT,
    RUN_FAMILY,
    expected_identity,
)
from .tutorial_phase4d2b_manifest import (
    EXPECTED_BUILD,
    EXPECTED_COMMIT,
    EXPECTED_COVERAGE,
    EXPECTED_MANIFEST_ID,
    EXPECTED_OBSERVED_AT,
    EXPECTED_PUSH_TIPS_CORPUS_COMPLETE,
    EXPECTED_RELEASE,
    EXPECTED_SOURCE_ID,
    EXPECTED_TREE,
    EXPECTED_VERSIONS,
    LOCALES,
    TABLE_PATHS,
    TutorialPhase4D2BManifest,
    TutorialPhase4D2BManifestError,
    load_tutorial_phase4d2b_manifest,
)
from .util import digest_id, text_occurrences


ROOT = Path(__file__).resolve().parents[2]
TUTORIAL_PHASE4D2B_MANIFEST_PATH = (
    ROOT / "manifests/tutorial/anime-game-data-7.0-phase4d2b-core-tutorial-full-boundary.json"
)

TUTORIAL_TABLES = (
    "TutorialExcelConfigData",
    "TutorialDetailExcelConfigData",
    "TutorialCatalogExcelConfigData",
    "PushTipsCodexExcelConfigData",
)
TUTORIAL_PRIMARY_TABLES = (
    "TutorialExcelConfigData",
    "TutorialDetailExcelConfigData",
    "TutorialCatalogExcelConfigData",
)


class _EvidenceScopeError(RuntimeError):
    """A stored child/provenance tuple cannot be proven to be in scope."""


@dataclass(frozen=True)
class TutorialNotFound:
    """A legal domain result for an absent or ineligible Tutorial lookup."""

    reason: str
    lookup: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "NotFound",
            "reason": self.reason,
            "lookup": self.lookup,
            "evidence_only": True,
            "coverage": "unavailable",
            "corpus_complete": False,
        }


@dataclass(frozen=True)
class TutorialCapabilitySnapshot:
    """Immutable server-start qualification for the sealed Tutorial run."""

    schema_supported: bool
    evidence_available: bool
    coverage: str
    # Keep the binding serialized so callers cannot mutate the server-start
    # snapshot through a shared nested dictionary.
    _binding_json: str | None = None

    @property
    def eligible_runs(self) -> list[dict[str, Any]]:
        if self._binding_json is None:
            return []
        try:
            decoded = json.loads(self._binding_json)
        except (TypeError, json.JSONDecodeError):
            return []
        if isinstance(decoded, list):
            return decoded
        return [decoded] if isinstance(decoded, dict) else []

    @property
    def corpus_complete(self) -> bool:
        return any(item.get("corpus_complete") is True for item in self.eligible_runs)

    @property
    def eligible(self) -> bool:
        return (
            self.evidence_available
            and self.coverage == EXPECTED_COVERAGE
            and self.corpus_complete
        )

    @property
    def binding(self) -> None:
        """There is intentionally no singleton/default Tutorial binding."""

        return None

    def to_dict(self) -> dict[str, Any]:
        bindings = self.eligible_runs
        boundary = {
            "family": RUN_FAMILY,
            "manifest_id": EXPECTED_MANIFEST_ID,
            "coverage": self.coverage,
            "corpus_complete": self.corpus_complete,
            "selected_files": (
                list(bindings[0].get("boundary", {}).get("selected_files", []))
                if bindings
                else []
            ),
            "locales": list(LOCALES),
            "push_tips_corpus_complete": EXPECTED_PUSH_TIPS_CORPUS_COMPLETE,
            "context_scope": "single_physical_tutorial_row_container_only",
        }
        return {
            "schema_supported": self.schema_supported,
            "evidence_available": self.evidence_available,
            "coverage": self.coverage,
            "corpus_complete": self.corpus_complete,
            "binding": None,
            "eligible_runs": bindings,
            "explicit_ingest_run_required": True,
            "default_run": False,
            "latest_run": False,
            "boundary": boundary,
            "selection": {
                "ingest_run_id": "required",
                "explicit_ingest_run_required": True,
                "default_run": False,
                "latest_run": False,
                "sibling_fallback": False,
            },
        }


@dataclass(frozen=True)
class _Qualification:
    """One immutable metadata binding returned by the qualification pass."""

    manifest: TutorialPhase4D2BManifest
    identity: dict[str, str]
    run: dict[str, Any]
    parser_build: dict[str, Any]
    snapshot: dict[str, Any]
    source_catalog: dict[str, Any]


def _strict_json(value: Any, label: str, *, default: Any = None) -> Any:
    if value is None:
        return default

    def reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key in {label}: {key}")
            result[key] = item
        return result

    try:
        return json.loads(str(value), object_pairs_hook=reject_pairs)
    except (TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not strict JSON") from exc


def _strict_object(value: Any, label: str) -> dict[str, Any]:
    parsed = _strict_json(value, label)
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be a JSON object")
    return parsed


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _page(limit: Any, offset: Any, *, maximum: int) -> tuple[int, int]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be non-negative")
    return limit, offset


def _index(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _window(before: Any, after: Any) -> tuple[int, int]:
    for value, label in ((before, "before"), (after, "after")):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 50:
            raise ValueError(f"{label} must be between 0 and 50")
    return before, after


@lru_cache(maxsize=1)
def _sealed_manifest() -> TutorialPhase4D2BManifest:
    """Load the committed sealed manifest, without loading any source file."""

    return load_tutorial_phase4d2b_manifest(TUTORIAL_PHASE4D2B_MANIFEST_PATH)


def _expected_scope(manifest: TutorialPhase4D2BManifest) -> dict[str, Any]:
    return {
        "family": RUN_FAMILY,
        "manifest_id": manifest.manifest_id,
        "snapshot_id": manifest.source["snapshot_id"],
        "coverage": EXPECTED_COVERAGE,
        "corpus_complete": True,
        "completeness_claim": EXPECTED_COVERAGE,
        "push_tips_corpus_complete": EXPECTED_PUSH_TIPS_CORPUS_COMPLETE,
        "selected_paths": [item.relative_path for item in manifest.files],
        "closure_counts": manifest.expected["counts"],
        "occurrence_set_digests": manifest.expected["occurrence_set_digests"],
        "full_core_tutorial_scan": True,
        "logical_identity_created": False,
        "mcp_capability_added": False,
    }


def _expected_config(manifest: TutorialPhase4D2BManifest) -> dict[str, Any]:
    return {
        "manifest_raw_sha256": manifest.raw_sha256,
        "versions": manifest.versions,
        "source_commit": EXPECTED_COMMIT,
        "source_tree": manifest.source["tree"],
        "coverage": EXPECTED_COVERAGE,
        "corpus_complete": True,
        "completeness_claim": EXPECTED_COVERAGE,
        "push_tips_corpus_complete": EXPECTED_PUSH_TIPS_CORPUS_COMPLETE,
        "schema_contract": "schema11-migration0011-exact",
        "occurrence_set_digests": manifest.expected["occurrence_set_digests"],
        "logical_identity_created": False,
        "mcp_capability_added": False,
    }


def _not_found(reason: str, operation: str, ingest_run_id: Any, **extra: Any) -> TutorialNotFound:
    lookup = {"operation": operation, "ingest_run_id": ingest_run_id}
    lookup.update(extra)
    return TutorialNotFound(reason, lookup)


def _schema11_supported(conn: sqlite3.Connection) -> bool:
    try:
        versions = [
            int(row[0])
            for row in conn.execute(
                "SELECT version FROM schema_migration ORDER BY version"
            )
        ]
    except (sqlite3.Error, TypeError, ValueError):
        return False
    if versions not in (
        list(range(1, 12)), list(range(1, 13)), list(range(1, 14)),
        list(range(1, 16)), list(range(1, 17)), list(range(1, 18)), list(range(1, 20)), list(range(1, 21)),
    ):
        return False
    try:
        if versions[-1] == 11:
            verify_tutorial_phase4d2a_schema_signature(conn)
        elif versions[-1] == 17:
            # Schema 17 is additive Wave02.  Require the exact M5D gate first
            # so Tutorial cannot accept a generic Schema 16 prefix or sibling.
            verify_schema17(conn)
            verify_tutorial_phase4d2a_schema_signature(conn, full_schema=False)
        else:
            validate_exact_schema_structure(conn, versions[-1])
    except (
        BatchM5DError,
        TutorialPhase4D2ASchemaError,
        sqlite3.Error,
        RuntimeError,
        ValueError,
        TypeError,
    ):
        return False
    return True


def _same_family_is_exact(conn: sqlite3.Connection, run_id: str) -> bool:
    family_ids: list[str] = []
    try:
        rows = conn.execute("SELECT id,scope_json FROM ingest_run ORDER BY id").fetchall()
        for row in rows:
            scope = _strict_object(row["scope_json"], f"ingest_run {row['id']} scope_json")
            if scope.get("family") == RUN_FAMILY:
                family_ids.append(str(row["id"]))
    except (sqlite3.Error, ValueError, TypeError, KeyError):
        return False
    return family_ids == [run_id]


def _qualified_scope(
    db: ReadonlyDatabase,
    ingest_run_id: Any,
    operation: str,
) -> _Qualification | TutorialNotFound:
    requested = _require_text(ingest_run_id, "ingest_run_id")
    lookup = {"operation": operation, "ingest_run_id": requested}

    # Qualification is intentionally fast and metadata-only.  In particular,
    # it must never call tutorial_phase4d2b_maintenance or any builder.
    if not _schema11_supported(db.conn):
        return TutorialNotFound(
            "tutorial_schema_ineligible",
            {**lookup, "required_schema_version": 11},
        )
    try:
        manifest = _sealed_manifest()
        identity = expected_identity(manifest)
    except (TutorialPhase4D2BManifestError, RuntimeError, OSError, ValueError, TypeError, KeyError):
        return TutorialNotFound(
            "tutorial_manifest_ineligible",
            {**lookup, "manifest_id": EXPECTED_MANIFEST_ID},
        )

    try:
        exists = db.conn.execute(
            "SELECT 1 FROM ingest_run WHERE id=?", (requested,)
        ).fetchone()
    except sqlite3.Error:
        return TutorialNotFound("tutorial_schema_ineligible", lookup)
    if exists is None:
        return TutorialNotFound("ingest_run_not_found", lookup)
    if requested != identity["ingest_run_id"]:
        return TutorialNotFound("ingest_run_ineligible", lookup)

    try:
        row = db.conn.execute(
            """
            SELECT ir.id, ir.snapshot_id, ir.parser_build_id,
                   ir.scope_json, ir.config_json, ir.status AS run_status,
                   ir.created_at AS run_created_at,
                   pb.parser_version, pb.mapping_version, pb.fingerprint,
                   pb.config_checksum, pb.created_at AS parser_created_at,
                   ss.id AS snapshot_id_value, ss.source_id,
                   ss.release_label, ss.build,
                   ss."commit" AS snapshot_commit, ss.tree AS snapshot_tree,
                   ss.observed_at, ss.parser_version AS snapshot_parser_version,
                   ss.mapping_version AS snapshot_mapping_version,
                   ss.status AS snapshot_status,
                   ss.metadata_json AS snapshot_metadata_json,
                   sc.kind AS source_kind, sc.url AS source_url,
                   sc.license_note AS source_license_note
              FROM ingest_run ir
              JOIN parser_build pb ON pb.id=ir.parser_build_id
              JOIN source_snapshot ss ON ss.id=ir.snapshot_id
              JOIN source_catalog sc ON sc.id=ss.source_id
             WHERE ir.id=?
            """,
            (requested,),
        ).fetchone()
        if row is None:
            return TutorialNotFound("ingest_run_ineligible", lookup)

        scope = _strict_object(row["scope_json"], "Tutorial ingest_run scope_json")
        config = _strict_object(row["config_json"], "Tutorial ingest_run config_json")
        snapshot_metadata = _strict_object(
            row["snapshot_metadata_json"], "Tutorial source_snapshot metadata_json"
        )

        expected_scope = _expected_scope(manifest)
        expected_config = _expected_config(manifest)
        if (
            row["id"] != identity["ingest_run_id"]
            or row["snapshot_id"] != identity["snapshot_id"]
            or row["parser_build_id"] != identity["parser_build_id"]
            or row["run_status"] != "complete"
            or row["run_created_at"] != EXPECTED_OBSERVED_AT
            or scope != expected_scope
            or config != expected_config
        ):
            return TutorialNotFound("ingest_run_ineligible", lookup)

        if (
            row["parser_version"] != EXPECTED_VERSIONS["parser"]
            or row["mapping_version"] != EXPECTED_VERSIONS["projection"]
            or row["fingerprint"] != PARSER_FINGERPRINT
            or row["config_checksum"] != identity["config_checksum"]
            or row["parser_created_at"] != EXPECTED_OBSERVED_AT
        ):
            return TutorialNotFound("ingest_run_ineligible", lookup)

        expected_source = {
            "source_id": EXPECTED_SOURCE_ID,
            "release_label": EXPECTED_RELEASE,
            "build": EXPECTED_BUILD,
            "commit": EXPECTED_COMMIT,
            "tree": EXPECTED_TREE,
            "observed_at": EXPECTED_OBSERVED_AT,
        }
        if (
            row["snapshot_id_value"] != identity["snapshot_id"]
            or row["source_id"] != expected_source["source_id"]
            or row["release_label"] != expected_source["release_label"]
            or row["build"] != expected_source["build"]
            or row["snapshot_commit"] != expected_source["commit"]
            or row["snapshot_tree"] != expected_source["tree"]
            or row["observed_at"] != expected_source["observed_at"]
            or row["snapshot_parser_version"] != "legacy-unscoped"
            or row["snapshot_mapping_version"] != "legacy-unscoped"
            or row["snapshot_status"] != "active"
            or row["source_kind"] != "primary"
            or row["source_url"] != "https://github.com/DimbreathBot/AnimeGameData"
            or row["source_license_note"] is not None
            or snapshot_metadata
            != {"sealed_source_manifest": True, "phase4d2b_full_boundary": True}
        ):
            return TutorialNotFound("ingest_run_ineligible", lookup)

        if not _same_family_is_exact(db.conn, identity["ingest_run_id"]):
            return TutorialNotFound("ingest_run_ineligible", lookup)

        return _Qualification(
            manifest=manifest,
            identity=identity,
            run={
                "id": row["id"],
                "snapshot_id": row["snapshot_id"],
                "parser_build_id": row["parser_build_id"],
                "status": row["run_status"],
                "created_at": row["run_created_at"],
                "scope": scope,
                "config": config,
            },
            parser_build={
                "id": row["parser_build_id"],
                "parser_version": row["parser_version"],
                "mapping_version": row["mapping_version"],
                "fingerprint": row["fingerprint"],
                "config_checksum": row["config_checksum"],
                "created_at": row["parser_created_at"],
            },
            snapshot={
                "id": row["snapshot_id_value"],
                "source_id": row["source_id"],
                "release_label": row["release_label"],
                "build": row["build"],
                "commit": row["snapshot_commit"],
                "tree": row["snapshot_tree"],
                "observed_at": row["observed_at"],
                "parser_version": row["snapshot_parser_version"],
                "mapping_version": row["snapshot_mapping_version"],
                "status": row["snapshot_status"],
                "metadata": snapshot_metadata,
            },
            source_catalog={
                "id": row["source_id"],
                "kind": row["source_kind"],
                "url": row["source_url"],
                "license_note": row["source_license_note"],
            },
        )
    except (sqlite3.Error, ValueError, TypeError, KeyError):
        return TutorialNotFound("ingest_run_ineligible", lookup)


def _tutorial_binding(scope: _Qualification) -> dict[str, Any]:
    """Serialize the exact metadata qualification as one stable run binding."""

    snapshot = scope.snapshot
    return {
        "run_family": RUN_FAMILY,
        "manifest_id": scope.manifest.manifest_id,
        "manifest_raw_sha256": scope.manifest.raw_sha256,
        "ingest_run_id": scope.identity["ingest_run_id"],
        "snapshot_id": scope.identity["snapshot_id"],
        "parser_build_id": scope.identity["parser_build_id"],
        "parser_version": scope.parser_build["parser_version"],
        "mapping_version": scope.parser_build["mapping_version"],
        "parser_fingerprint": scope.parser_build["fingerprint"],
        "config_checksum": scope.parser_build["config_checksum"],
        "source": {
            "source_id": snapshot["source_id"],
            "upstream": "https://github.com/DimbreathBot/AnimeGameData",
            "release_label": snapshot["release_label"],
            "build": snapshot["build"],
            "commit": snapshot["commit"],
            "tree": snapshot["tree"],
            "observed_at": snapshot["observed_at"],
        },
        "scope": scope.run["scope"],
        "config": scope.run["config"],
        "coverage": EXPECTED_COVERAGE,
        "corpus_complete": True,
        "boundary": _boundary(scope.manifest),
    }


def inspect_tutorial_capability(
    db: ReadonlyDatabase,
) -> TutorialCapabilitySnapshot:
    """Qualify the exact Phase 4D2B run using metadata only.

    This startup inspector intentionally does not import a maintenance module,
    read upstream files, or query any Tutorial occurrence table.  The returned
    frozen snapshot is retained by the MCP server for its entire lifetime.
    """

    try:
        if not _schema11_supported(db.conn):
            return TutorialCapabilitySnapshot(False, False, "unavailable", "[]")
        scope = _qualified_scope(
            db,
            expected_identity(_sealed_manifest())["ingest_run_id"],
            "inspect_tutorial_capability",
        )
    except (
        sqlite3.Error,
        TutorialPhase4D2BManifestError,
        RuntimeError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
    ):
        return TutorialCapabilitySnapshot(True, False, "unavailable", "[]")
    if isinstance(scope, TutorialNotFound):
        return TutorialCapabilitySnapshot(True, False, "unavailable", "[]")
    binding_json = json.dumps(
        [_tutorial_binding(scope)],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return TutorialCapabilitySnapshot(
        True,
        True,
        EXPECTED_COVERAGE,
        binding_json,
    )


def _boundary(manifest: TutorialPhase4D2BManifest) -> dict[str, Any]:
    return {
        "family": RUN_FAMILY,
        "manifest_id": manifest.manifest_id,
        "coverage": EXPECTED_COVERAGE,
        "corpus_complete": True,
        "selected_files": [item.relative_path for item in manifest.files],
        "locales": list(LOCALES),
        "push_tips_corpus_complete": EXPECTED_PUSH_TIPS_CORPUS_COMPLETE,
        "logical_identity_created": False,
        "context_scope": "single_physical_tutorial_row_container_only",
    }


def _success(scope: _Qualification, **payload: Any) -> dict[str, Any]:
    return {
        "status": "ok",
        "evidence_only": True,
        "coverage": EXPECTED_COVERAGE,
        "corpus_complete": True,
        "boundary": _boundary(scope.manifest),
        "scope": {
            "ingest_run": scope.run,
            "parser_build": scope.parser_build,
            "source_snapshot": scope.snapshot,
            "source_catalog": scope.source_catalog,
        },
        **payload,
    }


def _source_record(
    db: ReadonlyDatabase,
    source_record_id: str,
    *,
    source_file_id: str,
    source_pointer: str,
    snapshot_id: str,
    include_raw_payload: bool = False,
) -> dict[str, Any] | None:
    """Read provenance only when every stored source coordinate agrees."""

    row = db.conn.execute(
        """
        SELECT sr.id, sr.source_file_id, sr.pointer, sr.record_kind,
               sr.native_id, sr.raw_sha256, sr.raw_json,
               sf.snapshot_id, sf.relative_path, sf.upstream_blob_oid,
               sf.sha256 AS file_sha256, sf.size_bytes, sf.format, sf.root_path,
               ss.source_id, ss.release_label, ss.build,
               ss."commit" AS snapshot_commit, ss.tree AS snapshot_tree
          FROM source_record sr
          JOIN source_file sf
            ON sf.id=sr.source_file_id
           AND sf.id=?
           AND sf.snapshot_id=?
          JOIN source_snapshot ss ON ss.id=sf.snapshot_id
         WHERE sr.id=? AND sr.source_file_id=? AND sr.pointer=?
           AND ss.id=?
        """,
        (
            source_file_id,
            snapshot_id,
            source_record_id,
            source_file_id,
            source_pointer,
            snapshot_id,
        ),
    ).fetchone()
    if row is None:
        return None
    try:
        selected_paths = {item.relative_path for item in _sealed_manifest().files}
    except (TutorialPhase4D2BManifestError, OSError, ValueError, TypeError):
        return None
    if row["relative_path"] not in selected_paths:
        return None
    item: dict[str, Any] = {
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
        "root_path": row["root_path"],
        "source_id": row["source_id"],
        "release_label": row["release_label"],
        "build": row["build"],
        "commit": row["snapshot_commit"],
        "tree": row["snapshot_tree"],
    }
    if include_raw_payload:
        item["raw_payload"] = _strict_json(row["raw_json"], "Tutorial source_record raw_json")
    return item


def _expected_row_bindings(
    qualification: _Qualification,
) -> dict[str, tuple[str, str, str, int, str, str, str, str, str]]:
    """Derive the sealed row coordinate tuple without scanning upstream files."""

    bindings: dict[str, tuple[str, str, str, int, str, str, str, str, str]] = {}
    for spec in qualification.manifest.selected_rows:
        relative_path = TABLE_PATHS[spec.table]
        source_file_id = digest_id("sf", qualification.identity["snapshot_id"], relative_path)
        source_record_id = digest_id("sr", source_file_id, spec.pointer)
        row_id = occurrence_id(
            "row",
            qualification.identity,
            spec.table,
            relative_path,
            spec.pointer,
        )
        family_role = "lookup" if spec.table == "PushTipsCodexExcelConfigData" else "primary"
        bindings[row_id] = (
            source_file_id,
            source_record_id,
            spec.pointer,
            spec.row_index,
            spec.table,
            family_role,
            spec.native_locator_field,
            spec.native_locator_value,
            spec.raw_sha256,
        )
    return bindings


_SEALED_ROW_COLUMNS = (
    "id",
    "ingest_run_id",
    "snapshot_id",
    "parser_build_id",
    "source_file_id",
    "source_record_id",
    "source_pointer",
    "table_name",
    "family_role",
    "row_index",
    "native_locator_field",
    "native_locator_value",
    "raw_checksum",
)


def _sealed_row_cte(
    qualification: _Qualification,
) -> tuple[str, tuple[Any, ...]]:
    """Return a parameterized exact manifest-row relation for SQL consumers."""

    bindings = _expected_row_bindings(qualification)
    identity = qualification.identity
    values: list[Any] = []
    placeholders: list[str] = []
    for row_id, binding in bindings.items():
        (
            source_file_id,
            source_record_id,
            source_pointer,
            row_index,
            table_name,
            family_role,
            native_locator_field,
            native_locator_value,
            raw_checksum,
        ) = binding
        values.extend(
            (
                row_id,
                identity["ingest_run_id"],
                identity["snapshot_id"],
                identity["parser_build_id"],
                source_file_id,
                source_record_id,
                source_pointer,
                table_name,
                family_role,
                row_index,
                native_locator_field,
                native_locator_value,
                raw_checksum,
            )
        )
        placeholders.append("(" + ",".join("?" for _ in _SEALED_ROW_COLUMNS) + ")")
    if not placeholders:
        raise _EvidenceScopeError("sealed Tutorial manifest has no selected rows")
    cte = (
        "sealed_tutorial_rows("
        + ",".join(_SEALED_ROW_COLUMNS)
        + ") AS (VALUES "
        + ",".join(placeholders)
        + ")"
    )
    return cte, tuple(values)


def _row_select(
    db: ReadonlyDatabase,
    qualification: _Qualification,
    *,
    row_occurrence_id: str | None = None,
    table_name: str | None = None,
    native_locator_field: str | None = None,
    native_locator_value: str | None = None,
) -> list[sqlite3.Row]:
    where = [
        "cro.ingest_run_id=?",
        "cro.snapshot_id=?",
        "cro.parser_build_id=?",
    ]
    params: list[Any] = [
        qualification.identity["ingest_run_id"],
        qualification.identity["snapshot_id"],
        qualification.identity["parser_build_id"],
    ]
    selected_paths = tuple(item.relative_path for item in qualification.manifest.files)
    where.append("sf.relative_path IN (" + ",".join("?" for _ in selected_paths) + ")")
    params.extend(selected_paths)
    if row_occurrence_id is not None:
        where.append("cro.id=?")
        params.append(row_occurrence_id)
    if table_name is not None:
        where.append("cro.table_name=?")
        params.append(table_name)
    if native_locator_field is not None:
        where.append("cro.native_locator_field=?")
        params.append(native_locator_field)
    if native_locator_value is not None:
        where.append("cro.native_locator_value=?")
        params.append(native_locator_value)
    sealed_cte, sealed_params = _sealed_row_cte(qualification)
    rows = db.conn.execute(
        "WITH "
        + sealed_cte
        + " "
        + """
        SELECT cro.*
          FROM tutorial_row_occurrence cro
          JOIN sealed_tutorial_rows sealed
            ON sealed.id=cro.id
           AND sealed.ingest_run_id=cro.ingest_run_id
           AND sealed.snapshot_id=cro.snapshot_id
           AND sealed.parser_build_id=cro.parser_build_id
           AND sealed.source_file_id=cro.source_file_id
           AND sealed.source_record_id=cro.source_record_id
           AND sealed.source_pointer=cro.source_pointer
           AND sealed.table_name=cro.table_name
           AND sealed.family_role=cro.family_role
           AND sealed.row_index=cro.row_index
           AND sealed.native_locator_field=cro.native_locator_field
           AND sealed.native_locator_value=cro.native_locator_value
           AND sealed.raw_checksum=cro.raw_checksum
          JOIN source_record sr
            ON sr.id=cro.source_record_id
           AND sr.source_file_id=cro.source_file_id
           AND sr.pointer=cro.source_pointer
          JOIN source_file sf
            ON sf.id=cro.source_file_id
           AND sf.snapshot_id=cro.snapshot_id
         WHERE """
        + " AND ".join(where),
        sealed_params + tuple(params),
    ).fetchall()
    return rows


def _row_summary(
    db: ReadonlyDatabase,
    row: Mapping[str, Any],
    *,
    include_raw_payload: bool,
) -> dict[str, Any]:
    provenance = _source_record(
        db,
        str(row["source_record_id"]),
        source_file_id=str(row["source_file_id"]),
        source_pointer=str(row["source_pointer"]),
        snapshot_id=str(row["snapshot_id"]),
        include_raw_payload=include_raw_payload,
    )
    if provenance is None:
        raise _EvidenceScopeError(
            f"Tutorial row source provenance is outside exact scope: {row['id']}"
        )
    return {
        "id": row["id"],
        "ingest_run_id": row["ingest_run_id"],
        "snapshot_id": row["snapshot_id"],
        "parser_build_id": row["parser_build_id"],
        "source_file_id": row["source_file_id"],
        "source_record_id": row["source_record_id"],
        "source_pointer": row["source_pointer"],
        "table_name": row["table_name"],
        "family_role": row["family_role"],
        "row_index": row["row_index"],
        "native_locator_field": row["native_locator_field"],
        "native_locator_value": row["native_locator_value"],
        "raw_checksum": row["raw_checksum"],
        "metadata": _strict_object(row["metadata_json"], "Tutorial row metadata_json"),
        "provenance": provenance,
    }


def _child_scope(
    row: Mapping[str, Any],
    qualification: _Qualification,
    *,
    child_row: Mapping[str, Any],
    parent_record_id_column: str = "source_record_id",
    parent_row_id_column: str = "source_row_occurrence_id",
) -> bool:
    return (
        child_row["ingest_run_id"] == qualification.identity["ingest_run_id"]
        and child_row["snapshot_id"] == qualification.identity["snapshot_id"]
        and child_row["parser_build_id"] == qualification.identity["parser_build_id"]
        and child_row[parent_row_id_column] == row["id"]
        and child_row[parent_record_id_column] == row["source_record_id"]
    )


def _child_pointer(row: Mapping[str, Any], field_name: str, array_index: Any = None) -> str:
    escaped = str(field_name).replace("~", "~0").replace("/", "~1")
    pointer = str(row["source_pointer"]).rstrip("/") + "/" + escaped
    if array_index is not None:
        pointer += "/" + str(array_index)
    return pointer


def _raw_field_items(
    db: ReadonlyDatabase,
    qualification: _Qualification,
    row: Mapping[str, Any],
    *,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    base = """
        FROM tutorial_raw_field_occurrence rf
        JOIN tutorial_row_occurrence parent
          ON parent.id=rf.row_occurrence_id
         AND parent.ingest_run_id=rf.ingest_run_id
         AND parent.snapshot_id=rf.snapshot_id
         AND parent.parser_build_id=rf.parser_build_id
         AND parent.source_record_id=rf.source_record_id
        JOIN source_record sr
          ON sr.id=rf.source_record_id
         AND sr.source_file_id=parent.source_file_id
         AND sr.pointer=parent.source_pointer
        JOIN source_file sf
          ON sf.id=parent.source_file_id
         AND sf.snapshot_id=rf.snapshot_id
        WHERE rf.ingest_run_id=? AND rf.snapshot_id=? AND rf.parser_build_id=?
          AND rf.row_occurrence_id=? AND rf.source_record_id=?
    """
    params = (
        qualification.identity["ingest_run_id"],
        qualification.identity["snapshot_id"],
        qualification.identity["parser_build_id"],
        row["id"],
        row["source_record_id"],
    )
    total = int(db.conn.execute("SELECT COUNT(*) " + base, params).fetchone()[0])
    rows = db.conn.execute(
        "SELECT rf.* "
        + base
        + " ORDER BY rf.source_pointer, rf.id LIMIT ? OFFSET ?",
        params + (limit, offset),
    ).fetchall()
    provenance = _source_record(
        db,
        str(row["source_record_id"]),
        source_file_id=str(row["source_file_id"]),
        source_pointer=str(row["source_pointer"]),
        snapshot_id=str(row["snapshot_id"]),
        include_raw_payload=False,
    )
    if provenance is None:
        raise _EvidenceScopeError("Tutorial raw-field parent provenance is outside scope")
    result = []
    for item in rows:
        if not _child_scope(
            row,
            qualification,
            child_row=item,
            parent_row_id_column="row_occurrence_id",
        ) or item["source_pointer"] != _child_pointer(
            row,
            str(item["field_name"]),
            item["array_index"] if item["field_kind"] == "array_item" else None,
        ):
            raise _EvidenceScopeError("Tutorial raw-field child scope mismatch")
        result.append(
            {
                "id": item["id"],
                "ingest_run_id": item["ingest_run_id"],
                "snapshot_id": item["snapshot_id"],
                "parser_build_id": item["parser_build_id"],
                "row_occurrence_id": item["row_occurrence_id"],
                "source_record_id": item["source_record_id"],
                "source_pointer": item["source_pointer"],
                "field_name": item["field_name"],
                "array_index": item["array_index"],
                "field_kind": item["field_kind"],
                "raw_json": item["raw_json"],
                "raw_value": _strict_json(item["raw_json"], "Tutorial raw field raw_json"),
                "metadata": _strict_object(item["metadata_json"], "Tutorial raw field metadata_json"),
                "provenance": provenance,
            }
        )
    return result, total


def _target_row(
    db: ReadonlyDatabase,
    qualification: _Qualification,
    target_id: str,
    *,
    include_raw_payload: bool = False,
) -> dict[str, Any] | None:
    rows = _row_select(
        db,
        qualification,
        row_occurrence_id=target_id,
    )
    if len(rows) != 1:
        return None
    row = rows[0]
    return _row_summary(db, row, include_raw_payload=include_raw_payload)


def _relation_rows(
    db: ReadonlyDatabase,
    qualification: _Qualification,
    row: Mapping[str, Any],
    *,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    detail = """
        SELECT r.id, r.ingest_run_id, r.snapshot_id, r.parser_build_id,
               r.source_row_occurrence_id, r.source_record_id,
               r.source_pointer, r.field_name, r.array_index,
               r.raw_value_json, r.candidate_key_json,
               r.resolution_status, r.target_row_occurrence_id,
               r.candidate_scopes_json, r.metadata_json,
               'detail' AS relation_source
          FROM tutorial_detail_relation_occurrence r
         WHERE r.ingest_run_id=? AND r.snapshot_id=? AND r.parser_build_id=?
           AND r.source_row_occurrence_id=? AND r.source_record_id=?
    """
    push = """
        SELECT r.id, r.ingest_run_id, r.snapshot_id, r.parser_build_id,
               r.source_row_occurrence_id, r.source_record_id,
               r.source_pointer, r.field_name, NULL AS array_index,
               r.raw_value_json, r.candidate_key_json,
               r.resolution_status, r.target_row_occurrence_id,
               r.candidate_scopes_json, r.metadata_json,
               'push_tips' AS relation_source
          FROM tutorial_push_tips_candidate_occurrence r
         WHERE r.ingest_run_id=? AND r.snapshot_id=? AND r.parser_build_id=?
           AND r.source_row_occurrence_id=? AND r.source_record_id=?
    """
    params = (
        qualification.identity["ingest_run_id"],
        qualification.identity["snapshot_id"],
        qualification.identity["parser_build_id"],
        row["id"],
        row["source_record_id"],
    )
    union = f"{detail} UNION ALL {push}"
    total = int(db.conn.execute("SELECT COUNT(*) FROM (" + union + ")", params + params).fetchone()[0])
    relation_rows = db.conn.execute(
        "SELECT * FROM (" + union + ") ORDER BY source_pointer, id LIMIT ? OFFSET ?",
        params + params + (limit, offset),
    ).fetchall()
    parent_provenance = _source_record(
        db,
        str(row["source_record_id"]),
        source_file_id=str(row["source_file_id"]),
        source_pointer=str(row["source_pointer"]),
        snapshot_id=str(row["snapshot_id"]),
        include_raw_payload=False,
    )
    if parent_provenance is None:
        raise _EvidenceScopeError("Tutorial relation parent provenance is outside scope")
    result: list[dict[str, Any]] = []
    for relation in relation_rows:
        expected_pointer = _child_pointer(
            row,
            str(relation["field_name"]),
            relation["array_index"] if relation["relation_source"] == "detail" else None,
        )
        if (
            not _child_scope(row, qualification, child_row=relation)
            or relation["source_pointer"] != expected_pointer
        ):
            raise _EvidenceScopeError("Tutorial relation child scope mismatch")
        target_id = relation["target_row_occurrence_id"]
        direct_target = None
        if relation["resolution_status"] == "resolved_unique":
            if target_id is None:
                raise _EvidenceScopeError("resolved Tutorial relation has no target")
            direct_target = _target_row(db, qualification, str(target_id))
            if direct_target is None:
                raise _EvidenceScopeError("resolved Tutorial relation target is outside scope")
        elif target_id is not None:
            raise _EvidenceScopeError("unresolved Tutorial relation has a target")
        result.append(
            {
                "id": relation["id"],
                "ingest_run_id": relation["ingest_run_id"],
                "snapshot_id": relation["snapshot_id"],
                "parser_build_id": relation["parser_build_id"],
                "source_row_occurrence_id": relation["source_row_occurrence_id"],
                "source_record_id": relation["source_record_id"],
                "source_pointer": relation["source_pointer"],
                "field_name": relation["field_name"],
                "array_index": relation["array_index"] if "array_index" in relation.keys() else None,
                "raw_value_json": relation["raw_value_json"],
                "raw_value": _strict_json(relation["raw_value_json"], "Tutorial relation raw_value_json", default=None),
                "candidate_key": _strict_object(relation["candidate_key_json"], "Tutorial relation candidate_key_json"),
                "target_candidate_key": _strict_object(relation["candidate_key_json"], "Tutorial relation candidate_key_json"),
                "resolution_status": relation["resolution_status"],
                "target_row_occurrence_id": target_id,
                "candidate_scopes": _strict_json(relation["candidate_scopes_json"], "Tutorial relation candidate_scopes_json"),
                "metadata": _strict_object(relation["metadata_json"], "Tutorial relation metadata_json"),
                "relation_kind": relation["relation_source"],
                "provenance": parent_provenance,
                "direct_target_occurrence": direct_target,
            }
        )
    return result, total


def _candidate_items(
    db: ReadonlyDatabase,
    qualification: _Qualification,
    resolution: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows = db.conn.execute(
        """
        SELECT c.*
          FROM tutorial_text_candidate_occurrence c
          JOIN tutorial_text_resolution_occurrence tr
            ON tr.id=c.resolution_occurrence_id
           AND tr.ingest_run_id=c.ingest_run_id
           AND tr.snapshot_id=c.snapshot_id
           AND tr.parser_build_id=c.parser_build_id
           AND tr.owner_occurrence_id=?
           AND tr.source_row_occurrence_id=?
           AND tr.source_record_id=?
           AND tr.source_pointer=?
           AND tr.locale=c.locale
           AND tr.resolution_status=c.resolution_status
          JOIN source_record sr
            ON sr.id=c.source_record_id
           AND sr.source_file_id=c.source_file_id
           AND sr.pointer=c.source_pointer
          JOIN source_file sf
            ON sf.id=c.source_file_id
           AND sf.snapshot_id=c.snapshot_id
         WHERE c.resolution_occurrence_id=?
           AND c.ingest_run_id=? AND c.snapshot_id=? AND c.parser_build_id=?
         ORDER BY c.shard_kind, c.source_pointer, c.id
        """,
        (
            resolution["owner_occurrence_id"],
            resolution["source_row_occurrence_id"],
            resolution["source_record_id"],
            resolution["source_pointer"],
            resolution["id"],
            qualification.identity["ingest_run_id"],
            qualification.identity["snapshot_id"],
            qualification.identity["parser_build_id"],
        ),
    ).fetchall()
    candidate_scopes = _strict_json(
        resolution["candidate_scopes_json"],
        "Tutorial text resolution candidate_scopes_json",
    )
    if not isinstance(candidate_scopes, list):
        raise _EvidenceScopeError("Tutorial text candidate scopes are not an array")
    result = []
    for item in rows:
        provenance = _source_record(
            db,
            str(item["source_record_id"]),
            source_file_id=str(item["source_file_id"]),
            source_pointer=str(item["source_pointer"]),
            snapshot_id=str(item["snapshot_id"]),
            include_raw_payload=False,
        )
        if provenance is None:
            raise _EvidenceScopeError("Tutorial text candidate provenance is outside scope")
        if not any(
            isinstance(scope, dict)
            and scope.get("relative_path") == provenance["relative_path"]
            and scope.get("shard_kind") == item["shard_kind"]
            and scope.get("source_pointer") == item["source_pointer"]
            for scope in candidate_scopes
        ):
            raise _EvidenceScopeError("Tutorial text candidate is outside declared candidate scopes")
        result.append(
            {
                "id": item["id"],
                "ingest_run_id": item["ingest_run_id"],
                "snapshot_id": item["snapshot_id"],
                "parser_build_id": item["parser_build_id"],
                "resolution_occurrence_id": item["resolution_occurrence_id"],
                "source_file_id": item["source_file_id"],
                "source_record_id": item["source_record_id"],
                "source_pointer": item["source_pointer"],
                "shard_kind": item["shard_kind"],
                "locale": item["locale"],
                "resolution_status": item["resolution_status"],
                "raw_text": item["raw_text"],
                "normalized_text": item["normalized_text"],
                "raw_checksum": item["raw_checksum"],
                "normalized_checksum": item["normalized_checksum"],
                "metadata": _strict_object(item["metadata_json"], "Tutorial text candidate metadata_json"),
                "provenance": provenance,
            }
        )
    return result


def _resolution_item(
    db: ReadonlyDatabase,
    qualification: _Qualification,
    row: Mapping[str, Any],
) -> dict[str, Any]:
    candidates = _candidate_items(db, qualification, row)
    return {
        "id": row["id"],
        "ingest_run_id": row["ingest_run_id"],
        "snapshot_id": row["snapshot_id"],
        "parser_build_id": row["parser_build_id"],
        "owner_occurrence_id": row["owner_occurrence_id"],
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
        "candidate_scopes": _strict_json(row["candidate_scopes_json"], "Tutorial text resolution candidate_scopes_json"),
        "metadata": _strict_object(row["metadata_json"], "Tutorial text resolution metadata_json"),
        "candidates": candidates,
    }


def _text_owner_items(
    db: ReadonlyDatabase,
    qualification: _Qualification,
    row: Mapping[str, Any],
) -> list[dict[str, Any]]:
    owner_rows = db.conn.execute(
        """
        SELECT o.*
          FROM tutorial_text_owner_occurrence o
          JOIN tutorial_row_occurrence parent
            ON parent.id=o.source_row_occurrence_id
           AND parent.ingest_run_id=o.ingest_run_id
           AND parent.snapshot_id=o.snapshot_id
           AND parent.parser_build_id=o.parser_build_id
           AND parent.source_record_id=o.source_record_id
          JOIN source_record sr
            ON sr.id=o.source_record_id
           AND sr.source_file_id=parent.source_file_id
           AND sr.pointer=parent.source_pointer
          JOIN source_file sf
            ON sf.id=parent.source_file_id
           AND sf.snapshot_id=o.snapshot_id
         WHERE o.ingest_run_id=? AND o.snapshot_id=? AND o.parser_build_id=?
           AND o.source_row_occurrence_id=? AND o.source_record_id=?
         ORDER BY o.source_pointer, o.id
        """,
        (
            qualification.identity["ingest_run_id"],
            qualification.identity["snapshot_id"],
            qualification.identity["parser_build_id"],
            row["id"],
            row["source_record_id"],
        ),
    ).fetchall()
    parent_provenance = _source_record(
        db,
        str(row["source_record_id"]),
        source_file_id=str(row["source_file_id"]),
        source_pointer=str(row["source_pointer"]),
        snapshot_id=str(row["snapshot_id"]),
        include_raw_payload=False,
    )
    if parent_provenance is None:
        raise _EvidenceScopeError("Tutorial text owner parent provenance is outside scope")
    result: list[dict[str, Any]] = []
    for owner in owner_rows:
        if (
            not _child_scope(row, qualification, child_row=owner)
            or owner["source_pointer"] != _child_pointer(row, str(owner["field_name"]))
        ):
            raise _EvidenceScopeError("Tutorial text owner child scope mismatch")
        resolution_rows = db.conn.execute(
            """
            SELECT tr.*
              FROM tutorial_text_resolution_occurrence tr
             WHERE tr.ingest_run_id=? AND tr.snapshot_id=? AND tr.parser_build_id=?
               AND tr.owner_occurrence_id=?
               AND tr.source_row_occurrence_id=? AND tr.source_record_id=?
               AND tr.source_pointer=?
             ORDER BY tr.locale, tr.id
            """,
            (
                qualification.identity["ingest_run_id"],
                qualification.identity["snapshot_id"],
                qualification.identity["parser_build_id"],
                owner["id"],
                row["id"],
                row["source_record_id"],
                owner["source_pointer"],
            ),
        ).fetchall()
        resolutions = []
        for resolution in resolution_rows:
            if (
                resolution["owner_occurrence_id"] != owner["id"]
                or resolution["source_row_occurrence_id"] != row["id"]
                or resolution["source_record_id"] != row["source_record_id"]
                or resolution["source_pointer"] != owner["source_pointer"]
            ):
                raise _EvidenceScopeError("Tutorial text resolution scope mismatch")
            resolutions.append(_resolution_item(db, qualification, resolution))
        result.append(
            {
                "id": owner["id"],
                "ingest_run_id": owner["ingest_run_id"],
                "snapshot_id": owner["snapshot_id"],
                "parser_build_id": owner["parser_build_id"],
                "source_row_occurrence_id": owner["source_row_occurrence_id"],
                "source_record_id": owner["source_record_id"],
                "source_pointer": owner["source_pointer"],
                "field_name": owner["field_name"],
                "field_role": owner["field_role"],
                "textmap_hash": owner["textmap_hash"],
                "raw_value_json": owner["raw_value_json"],
                "raw_value": _strict_json(owner["raw_value_json"], "Tutorial text owner raw_value_json"),
                "metadata": _strict_object(owner["metadata_json"], "Tutorial text owner metadata_json"),
                "provenance": parent_provenance,
                "resolutions": resolutions,
            }
        )
    return result


def _image_items(
    db: ReadonlyDatabase,
    qualification: _Qualification,
    row: Mapping[str, Any],
) -> list[dict[str, Any]]:
    image_rows = db.conn.execute(
        """
        SELECT i.*
          FROM tutorial_image_reference_occurrence i
          JOIN tutorial_row_occurrence parent
            ON parent.id=i.source_row_occurrence_id
           AND parent.ingest_run_id=i.ingest_run_id
           AND parent.snapshot_id=i.snapshot_id
           AND parent.parser_build_id=i.parser_build_id
           AND parent.source_record_id=i.source_record_id
          JOIN source_record sr
            ON sr.id=i.source_record_id
           AND sr.source_file_id=parent.source_file_id
           AND sr.pointer=parent.source_pointer
          JOIN source_file sf
            ON sf.id=parent.source_file_id
           AND sf.snapshot_id=i.snapshot_id
         WHERE i.ingest_run_id=? AND i.snapshot_id=? AND i.parser_build_id=?
           AND i.source_row_occurrence_id=? AND i.source_record_id=?
         ORDER BY i.source_pointer, i.id
        """,
        (
            qualification.identity["ingest_run_id"],
            qualification.identity["snapshot_id"],
            qualification.identity["parser_build_id"],
            row["id"],
            row["source_record_id"],
        ),
    ).fetchall()
    provenance = _source_record(
        db,
        str(row["source_record_id"]),
        source_file_id=str(row["source_file_id"]),
        source_pointer=str(row["source_pointer"]),
        snapshot_id=str(row["snapshot_id"]),
        include_raw_payload=False,
    )
    if provenance is None:
        raise _EvidenceScopeError("Tutorial image parent provenance is outside scope")
    result = []
    for image in image_rows:
        if (
            not _child_scope(row, qualification, child_row=image)
            or image["source_pointer"] != _child_pointer(
                row, str(image["field_name"]), image["array_index"]
            )
        ):
            raise _EvidenceScopeError("Tutorial image child scope mismatch")
        result.append(
            {
                "id": image["id"],
                "ingest_run_id": image["ingest_run_id"],
                "snapshot_id": image["snapshot_id"],
                "parser_build_id": image["parser_build_id"],
                "source_row_occurrence_id": image["source_row_occurrence_id"],
                "source_record_id": image["source_record_id"],
                "source_pointer": image["source_pointer"],
                "field_name": image["field_name"],
                "array_index": image["array_index"],
                "raw_image_name": image["raw_image_name"],
                "reference_status": image["reference_status"],
                "metadata": _strict_object(image["metadata_json"], "Tutorial image metadata_json"),
                "provenance": provenance,
            }
        )
    return result


def list_tutorial_rows(
    db: ReadonlyDatabase,
    *,
    ingest_run_id: str,
    table_name: str | None = None,
    native_locator_field: str | None = None,
    native_locator_value: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any] | TutorialNotFound:
    """List physical Tutorial row occurrences with exact raw-column filters."""

    limit, offset = _page(limit, offset, maximum=100)
    if table_name is not None:
        _require_text(table_name, "table_name")
    if native_locator_field is not None:
        _require_text(native_locator_field, "native_locator_field")
    if native_locator_value is not None:
        _require_text(native_locator_value, "native_locator_value")
    scope = _qualified_scope(db, ingest_run_id, "list_tutorial_rows")
    if isinstance(scope, TutorialNotFound):
        return scope
    try:
        rows = _row_select(
            db,
            scope,
            table_name=table_name,
            native_locator_field=native_locator_field,
            native_locator_value=native_locator_value,
        )
        # Count and page use the same exact source-coordinate qualification.
        total = len(rows)
        rows = sorted(
            rows,
            key=lambda item: (str(item["table_name"]), int(item["row_index"]), str(item["id"])),
        )
        page = rows[offset : offset + limit]
        items = [_row_summary(db, row, include_raw_payload=False) for row in page]
    except (sqlite3.Error, ValueError, TypeError, KeyError, _EvidenceScopeError):
        return _not_found("evidence_scope_ineligible", "list_tutorial_rows", ingest_run_id)
    return _success(
        scope,
        filters={
            "table_name": table_name,
            "native_locator_field": native_locator_field,
            "native_locator_value": native_locator_value,
        },
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


def get_tutorial_row_evidence(
    db: ReadonlyDatabase,
    *,
    ingest_run_id: str,
    row_occurrence_id: str,
    raw_field_limit: int = 500,
    raw_field_offset: int = 0,
    raw_slot_limit: int | None = None,
    raw_slot_offset: int | None = None,
    relationship_limit: int = 500,
    relationship_offset: int = 0,
) -> dict[str, Any] | TutorialNotFound:
    """Return one physical row and all directly attached raw evidence."""

    # ``raw_slot_*`` is a small compatibility alias for callers following the
    # Character row API; Tutorial's canonical name is ``raw_field_*``.
    if raw_slot_limit is not None:
        raw_field_limit = raw_slot_limit
    if raw_slot_offset is not None:
        raw_field_offset = raw_slot_offset
    raw_field_limit, raw_field_offset = _page(raw_field_limit, raw_field_offset, maximum=500)
    relationship_limit, relationship_offset = _page(
        relationship_limit, relationship_offset, maximum=500
    )
    requested_id = _require_text(row_occurrence_id, "row_occurrence_id")
    scope = _qualified_scope(db, ingest_run_id, "get_tutorial_row_evidence")
    if isinstance(scope, TutorialNotFound):
        return scope
    try:
        rows = _row_select(db, scope, row_occurrence_id=requested_id)
        if not rows:
            return _not_found(
                "row_occurrence_not_found",
                "get_tutorial_row_evidence",
                ingest_run_id,
                row_occurrence_id=requested_id,
            )
        if len(rows) != 1:
            return _not_found(
                "evidence_scope_ineligible",
                "get_tutorial_row_evidence",
                ingest_run_id,
                row_occurrence_id=requested_id,
            )
        row = rows[0]
        row_item = _row_summary(db, row, include_raw_payload=True)
        raw_fields, raw_field_total = _raw_field_items(
            db,
            scope,
            row,
            limit=raw_field_limit,
            offset=raw_field_offset,
        )
        relationships, relationship_total = _relation_rows(
            db,
            scope,
            row,
            limit=relationship_limit,
            offset=relationship_offset,
        )
        text_owners = _text_owner_items(db, scope, row)
        images = _image_items(db, scope, row)
    except (sqlite3.Error, ValueError, TypeError, KeyError, _EvidenceScopeError):
        return _not_found(
            "evidence_scope_ineligible",
            "get_tutorial_row_evidence",
            ingest_run_id,
            row_occurrence_id=requested_id,
        )
    return _success(
        scope,
        row=row_item,
        raw_fields={
            "items": raw_fields,
            "total": raw_field_total,
            "limit": raw_field_limit,
            "offset": raw_field_offset,
        },
        # The singular ``raw_slots`` alias keeps the physical-field payload
        # easy to consume alongside the established Character repository.
        raw_slots={
            "items": raw_fields,
            "total": raw_field_total,
            "limit": raw_field_limit,
            "offset": raw_field_offset,
        },
        relationships={
            "items": relationships,
            "total": relationship_total,
            "limit": relationship_limit,
            "offset": relationship_offset,
        },
        text_owners=text_owners,
        image_references=images,
    )


def search_tutorial_text_evidence(
    db: ReadonlyDatabase,
    *,
    ingest_run_id: str,
    locale: str,
    query: str,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any] | TutorialNotFound:
    """Search stored resolution ``raw_text`` using case-sensitive literal ``instr``."""

    limit, offset = _page(limit, offset, maximum=100)
    literal = _require_text(query, "query")
    if locale not in LOCALES:
        raise ValueError(f"locale must be one of: {', '.join(LOCALES)}")
    scope = _qualified_scope(db, ingest_run_id, "search_tutorial_text_evidence")
    if isinstance(scope, TutorialNotFound):
        return scope
    sealed_cte, sealed_params = _sealed_row_cte(scope)
    base = """
          FROM tutorial_text_resolution_occurrence tr
          JOIN tutorial_text_owner_occurrence own
            ON own.id=tr.owner_occurrence_id
           AND own.ingest_run_id=tr.ingest_run_id
           AND own.snapshot_id=tr.snapshot_id
           AND own.parser_build_id=tr.parser_build_id
           AND own.source_row_occurrence_id=tr.source_row_occurrence_id
           AND own.source_record_id=tr.source_record_id
           AND own.source_pointer=tr.source_pointer
          JOIN tutorial_row_occurrence cro
            ON cro.id=tr.source_row_occurrence_id
           AND cro.ingest_run_id=tr.ingest_run_id
           AND cro.snapshot_id=tr.snapshot_id
           AND cro.parser_build_id=tr.parser_build_id
           AND cro.source_record_id=tr.source_record_id
          JOIN sealed_tutorial_rows sealed
            ON sealed.id=cro.id
           AND sealed.ingest_run_id=cro.ingest_run_id
           AND sealed.snapshot_id=cro.snapshot_id
           AND sealed.parser_build_id=cro.parser_build_id
           AND sealed.source_file_id=cro.source_file_id
           AND sealed.source_record_id=cro.source_record_id
           AND sealed.source_pointer=cro.source_pointer
           AND sealed.table_name=cro.table_name
           AND sealed.family_role=cro.family_role
           AND sealed.row_index=cro.row_index
           AND sealed.native_locator_field=cro.native_locator_field
           AND sealed.native_locator_value=cro.native_locator_value
           AND sealed.raw_checksum=cro.raw_checksum
          JOIN source_record sr
            ON sr.id=cro.source_record_id
           AND sr.source_file_id=cro.source_file_id
           AND sr.pointer=cro.source_pointer
          JOIN source_file sf
            ON sf.id=cro.source_file_id
           AND sf.snapshot_id=cro.snapshot_id
         WHERE tr.ingest_run_id=? AND tr.snapshot_id=? AND tr.parser_build_id=?
           AND tr.locale=? AND instr(tr.raw_text, ?) > 0
    """
    params = (
        scope.identity["ingest_run_id"],
        scope.identity["snapshot_id"],
        scope.identity["parser_build_id"],
        locale,
        literal,
    )
    query_params = sealed_params + params
    try:
        total = int(
            db.conn.execute(
                "WITH " + sealed_cte + " SELECT COUNT(*) " + base,
                query_params,
            ).fetchone()[0]
        )
        rows = db.conn.execute(
            "WITH "
            + sealed_cte
            + " "
            + """
            SELECT tr.*, own.field_name AS owner_field_name,
                   own.field_role AS owner_field_role,
                   own.textmap_hash AS owner_textmap_hash,
                   own.raw_value_json AS owner_raw_value_json,
                   own.metadata_json AS owner_metadata_json,
                   cro.id AS row_id,
                   cro.ingest_run_id AS row_ingest_run_id,
                   cro.snapshot_id AS row_snapshot_id,
                   cro.parser_build_id AS row_parser_build_id,
                   cro.source_file_id AS row_source_file_id,
                   cro.source_record_id AS row_source_record_id,
                   cro.source_pointer AS row_source_pointer,
                   cro.table_name AS row_table_name,
                   cro.family_role AS row_family_role,
                   cro.row_index AS row_row_index,
                   cro.native_locator_field AS row_native_locator_field,
                   cro.native_locator_value AS row_native_locator_value,
                   cro.raw_checksum AS row_raw_checksum,
                   cro.metadata_json AS row_metadata_json
            """
            + base
            + " ORDER BY tr.locale, cro.table_name, cro.row_index, "
              "tr.source_pointer, tr.id LIMIT ? OFFSET ?",
            query_params + (limit, offset),
        ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            resolution = _resolution_item(db, scope, row)
            row_projection = {
                "id": row["row_id"],
                "ingest_run_id": row["row_ingest_run_id"],
                "snapshot_id": row["row_snapshot_id"],
                "parser_build_id": row["row_parser_build_id"],
                "source_file_id": row["row_source_file_id"],
                "source_record_id": row["row_source_record_id"],
                "source_pointer": row["row_source_pointer"],
                "table_name": row["row_table_name"],
                "family_role": row["row_family_role"],
                "row_index": row["row_row_index"],
                "native_locator_field": row["row_native_locator_field"],
                "native_locator_value": row["row_native_locator_value"],
                "raw_checksum": row["row_raw_checksum"],
                "metadata_json": row["row_metadata_json"],
            }
            row_item = _row_summary(db, row_projection, include_raw_payload=False)
            if row["source_pointer"] != _child_pointer(
                row_projection,
                str(row["owner_field_name"]),
            ):
                raise _EvidenceScopeError("Tutorial search owner pointer is outside row scope")
            owner = {
                "id": row["owner_occurrence_id"],
                "ingest_run_id": row["ingest_run_id"],
                "snapshot_id": row["snapshot_id"],
                "parser_build_id": row["parser_build_id"],
                "source_row_occurrence_id": row["source_row_occurrence_id"],
                "source_record_id": row["source_record_id"],
                "source_pointer": row["source_pointer"],
                "field_name": row["owner_field_name"],
                "field_role": row["owner_field_role"],
                "textmap_hash": row["owner_textmap_hash"],
                "raw_value": _strict_json(row["owner_raw_value_json"], "Tutorial text owner raw_value_json"),
                "metadata": _strict_object(row["owner_metadata_json"], "Tutorial text owner metadata_json"),
                "provenance": row_item["provenance"],
            }
            resolution["matches"] = [
                {"start": start, "end": end}
                for start, end in text_occurrences(row["raw_text"], literal)
            ]
            # Match Character search's flat resolution item while also making
            # owner evidence explicit for Tutorial's owner/resolution schema.
            items.append(
                {
                    **resolution,
                    "owner": owner,
                    "owner_occurrence": owner,
                    "row": row_item,
                    "source_evidence": row_item["provenance"],
                }
            )
    except (sqlite3.Error, ValueError, TypeError, KeyError, _EvidenceScopeError):
        return _not_found(
            "evidence_scope_ineligible",
            "search_tutorial_text_evidence",
            ingest_run_id,
            locale=locale,
            query=literal,
        )
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


def _context_index_alias(
    center_detail_array_index: int | None,
    detail_array_index: int | None,
    center_index: int | None,
) -> int:
    values = [
        value
        for value in (center_detail_array_index, detail_array_index, center_index)
        if value is not None
    ]
    if not values:
        raise ValueError("center_detail_array_index is required")
    if any(value != values[0] for value in values):
        raise ValueError("detail array index aliases must agree")
    return _index(values[0], "center_detail_array_index")


def get_tutorial_context(
    db: ReadonlyDatabase,
    *,
    ingest_run_id: str,
    row_occurrence_id: str,
    center_detail_array_index: int | None = None,
    detail_array_index: int | None = None,
    center_index: int | None = None,
    before: int = 2,
    after: int = 2,
) -> dict[str, Any] | TutorialNotFound:
    """Window stored ``detailIdList`` occurrences in one Tutorial row only."""

    center = _context_index_alias(
        center_detail_array_index,
        detail_array_index,
        center_index,
    )
    before, after = _window(before, after)
    requested_id = _require_text(row_occurrence_id, "row_occurrence_id")
    scope = _qualified_scope(db, ingest_run_id, "get_tutorial_context")
    if isinstance(scope, TutorialNotFound):
        return scope
    try:
        rows = _row_select(db, scope, row_occurrence_id=requested_id)
        if not rows:
            return _not_found(
                "context_row_occurrence_not_found",
                "get_tutorial_context",
                ingest_run_id,
                row_occurrence_id=requested_id,
                center_detail_array_index=center,
            )
        if len(rows) != 1:
            return _not_found(
                "context_container_ineligible",
                "get_tutorial_context",
                ingest_run_id,
                row_occurrence_id=requested_id,
                center_detail_array_index=center,
            )
        row = rows[0]
        if row["table_name"] != "TutorialExcelConfigData" or row["family_role"] != "primary":
            return _not_found(
                "context_container_ineligible",
                "get_tutorial_context",
                ingest_run_id,
                row_occurrence_id=requested_id,
                center_detail_array_index=center,
            )
        row_item = _row_summary(db, row, include_raw_payload=True)
        relation_base = """
              FROM tutorial_detail_relation_occurrence r
             WHERE r.ingest_run_id=? AND r.snapshot_id=? AND r.parser_build_id=?
               AND r.source_row_occurrence_id=? AND r.source_record_id=?
               AND r.field_name='detailIdList'
               AND r.array_index BETWEEN ? AND ?
        """
        params = (
            scope.identity["ingest_run_id"],
            scope.identity["snapshot_id"],
            scope.identity["parser_build_id"],
            row["id"],
            row["source_record_id"],
            max(0, center - before),
            center + after,
        )
        relation_rows = db.conn.execute(
            "SELECT r.* " + relation_base + " ORDER BY r.array_index, r.id",
            params,
        ).fetchall()
        center_rows = [
            relation
            for relation in relation_rows
            if relation["array_index"] == center
        ]
        if len(center_rows) != 1:
            return _not_found(
                "detail_array_index_not_found",
                "get_tutorial_context",
                ingest_run_id,
                row_occurrence_id=requested_id,
                center_detail_array_index=center,
            )
        parent_provenance = _source_record(
            db,
            str(row["source_record_id"]),
            source_file_id=str(row["source_file_id"]),
            source_pointer=str(row["source_pointer"]),
            snapshot_id=str(row["snapshot_id"]),
            include_raw_payload=False,
        )
        if parent_provenance is None:
            raise _EvidenceScopeError("Tutorial context parent provenance is outside scope")
        context_items: list[dict[str, Any]] = []
        for relation in relation_rows:
            expected_pointer = _child_pointer(
                row,
                str(relation["field_name"]),
                relation["array_index"],
            )
            if (
                not _child_scope(row, scope, child_row=relation)
                or relation["source_pointer"] != expected_pointer
            ):
                raise _EvidenceScopeError("Tutorial context relation scope mismatch")
            target_id = relation["target_row_occurrence_id"]
            direct_target = None
            target_row_evidence = None
            if relation["resolution_status"] == "resolved_unique":
                if target_id is None:
                    raise _EvidenceScopeError("resolved Tutorial context relation has no target")
                direct_target = _target_row(db, scope, str(target_id), include_raw_payload=False)
                if direct_target is None or direct_target["table_name"] != "TutorialDetailExcelConfigData":
                    raise _EvidenceScopeError("Tutorial context target is outside detail scope")
                # Follow only this stored direct FK.  The nested evidence is
                # itself row-local and never asks for another Tutorial parent.
                target_rows = _row_select(db, scope, row_occurrence_id=str(target_id))
                target = target_rows[0]
                target_raw, target_raw_total = _raw_field_items(
                    db, scope, target, limit=500, offset=0
                )
                target_owners = _text_owner_items(db, scope, target)
                target_images = _image_items(db, scope, target)
                target_row_evidence = {
                    "row": _row_summary(db, target, include_raw_payload=True),
                    "raw_fields": {
                        "items": target_raw,
                        "total": target_raw_total,
                        "limit": 500,
                        "offset": 0,
                    },
                    "text_owners": target_owners,
                    "image_references": target_images,
                    "relationships": {"items": [], "total": 0, "limit": 0, "offset": 0},
                }
            elif target_id is not None:
                raise _EvidenceScopeError("unresolved Tutorial context relation has a target")
            item = {
                "id": relation["id"],
                "ingest_run_id": relation["ingest_run_id"],
                "snapshot_id": relation["snapshot_id"],
                "parser_build_id": relation["parser_build_id"],
                "source_row_occurrence_id": relation["source_row_occurrence_id"],
                "source_record_id": relation["source_record_id"],
                "source_pointer": relation["source_pointer"],
                "field_name": relation["field_name"],
                "array_index": relation["array_index"],
                "raw_value_json": relation["raw_value_json"],
                "raw_value": _strict_json(relation["raw_value_json"], "Tutorial context raw_value_json", default=None),
                "candidate_key": _strict_object(relation["candidate_key_json"], "Tutorial context candidate_key_json"),
                "resolution_status": relation["resolution_status"],
                "target_row_occurrence_id": target_id,
                "candidate_scopes": _strict_json(relation["candidate_scopes_json"], "Tutorial context candidate_scopes_json"),
                "metadata": _strict_object(relation["metadata_json"], "Tutorial context metadata_json"),
                "provenance": parent_provenance,
                "direct_target_occurrence": direct_target,
                "target_row_evidence": target_row_evidence,
                "is_center": relation["array_index"] == center,
            }
            context_items.append(item)
    except (sqlite3.Error, ValueError, TypeError, KeyError, _EvidenceScopeError):
        return _not_found(
            "evidence_scope_ineligible",
            "get_tutorial_context",
            ingest_run_id,
            row_occurrence_id=requested_id,
            center_detail_array_index=center,
        )
    return _success(
        scope,
        context_scope="single_physical_tutorial_row_container_only",
        container=row_item,
        row=row_item,
        center_detail_array_index=center,
        anchor=next(item for item in context_items if item["is_center"]),
        window={
            "before": before,
            "after": after,
            "items": context_items,
            "ordering": "stored_detailIdList_array_index",
        },
    )


__all__ = [
    "TutorialCapabilitySnapshot",
    "TutorialNotFound",
    "inspect_tutorial_capability",
    "get_tutorial_context",
    "get_tutorial_row_evidence",
    "list_tutorial_rows",
    "search_tutorial_text_evidence",
]
