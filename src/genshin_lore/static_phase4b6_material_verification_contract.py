"""Independent verification contract for the sealed Phase 4B6 material run.

This module deliberately does not import or call the material builder.  It
reconstructs the persisted occurrence sets from SQLite and compares their
canonical digests with the sealed manifest.  A material row, checksum, or
TextMap hash is therefore never treated as a logical identity.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from .character_phase4c2_schema import (
    CharacterPhase4C2SchemaError,
    verify_character_phase4c2_schema_signature,
)
from .source_record_contract import SourceRecordContract
from .util import canonical_json, digest_id, normalize_text, sha256_bytes, sha256_text


RUN_FAMILY = "static_phase4b6_material_corpus"

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

_STATUS_KEYS = ("resolved", "ambiguous", "unresolved", "dangling")
_TEXT_STATUS_KEYS = ("unique_main", "unique_medium", "both_equal", "both_conflict", "missing")


class Phase4B6ContractError(RuntimeError):
    """Raised when a database does not satisfy the sealed B6 contract."""


def _fail(message: str) -> None:
    raise Phase4B6ContractError(message)


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


def _set_digest(items: list[dict[str, Any]]) -> str:
    ordered = sorted(canonical_json(item) for item in items)
    return sha256_text(canonical_json(ordered))


def _selection_rule(manifest: Any) -> str | None:
    """Return an explicit selection marker when a synthetic manifest has one.

    The sealed Lane-A manifest binds the selection rule through its exact
    ``versions`` object (including ``identity_rule``), parser-build ID, and
    run config.  It intentionally does not add a second free-form field to
    ``scope_json``.  Synthetic contract fixtures may still expose an
    explicit marker, so retain that compatibility without inventing one for
    production manifests.
    """
    for value in (
        getattr(manifest, "selection_rule", None),
        getattr(manifest, "boundaries", {}).get("selection_rule"),
        getattr(manifest, "versions", {}).get("selection_rule"),
        getattr(manifest, "raw", {}).get("selection_rule"),
    ):
        if isinstance(value, str) and value:
            return value
    return None


def _material_text_field_contract(manifest: Any) -> dict[tuple[str, str], tuple[str, str]]:
    """Index the manifest's rich Material TextMap field roles by coordinate."""

    declared = getattr(manifest, "text_fields", ())
    if not isinstance(declared, (list, tuple)):
        _fail("manifest TextMap field contract is not a sequence")
    result: dict[tuple[str, str], tuple[str, str]] = {}
    for item in declared:
        if not isinstance(item, dict):
            _fail("manifest TextMap field contract contains a non-object")
        table = item.get("table")
        field = item.get("field")
        role = item.get("field_role")
        relation_kind = item.get("relation_kind")
        if not all(isinstance(value, str) and value for value in (table, field, role, relation_kind)):
            _fail("manifest TextMap field contract contains an invalid coordinate")
        key = (table, field)
        if key in result:
            _fail(f"manifest TextMap field contract is duplicated: {table}.{field}")
        result[key] = role, relation_kind
    return result


def expected_phase4b6_material_identity(manifest: Any) -> dict[str, Any]:
    """Reconstruct the immutable IDs used by the B6 builder.

    The IDs bind source snapshot, parser/projection versions, manifest bytes,
    and the complete occurrence digest set.  They are locators for one run,
    not semantic identities for materials or passages.
    """

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
        "pb-static-phase4b6",
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
        "ir-static-phase4b6",
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
    selection_rule = _selection_rule(manifest)
    if selection_rule is not None:
        scope["selection_rule"] = selection_rule

    run_config: dict[str, Any] = {
        "manifest_raw_sha256": manifest.raw_sha256,
        "versions": manifest.versions,
        "full_static_scan": False,
        "logical_identity_created": False,
        "cross_language_alignment_created": False,
        "textmap_evidence_container_created": False,
    }
    if selection_rule is not None:
        run_config["selection_rule"] = selection_rule

    return {
        "snapshot_id": snapshot_id,
        "config_checksum": config_checksum,
        "parser_build_id": parser_build_id,
        "ingest_run_id": ingest_run_id,
        "scope_json": canonical_json(scope),
        "config_json": canonical_json(run_config),
    }


def expected_phase4b6_source_snapshot_binding(manifest: Any) -> dict[str, Any]:
    """Return the source-only binding for a shared snapshot row.

    ``source_snapshot`` predates the explicit parser-build/run split and still
    has legacy ``parser_version``/``mapping_version`` columns.  Those columns
    are not part of raw source identity and therefore are intentionally absent
    here: a Material run must be able to reuse a snapshot first recorded by
    Reliquary, Talk, Quest, or another family without rewriting that row.
    Lifecycle status and the sealed-source marker remain gate metadata and are
    checked separately from the parser columns.
    """

    source = manifest.source
    return {
        "core": (
            source["source_id"],
            source["release_label"],
            source["build"],
            source["commit"],
            source["tree"],
            source["observed_at"],
        ),
        "status": "active",
        "metadata_json": canonical_json({"sealed_source_manifest": True}),
    }


def verify_phase4b6_material_source_contract(
    source_root: str | Path,
    manifest: Any,
) -> dict[str, Any]:
    """Verify the pinned source checkout and every sealed file digest."""

    root = Path(source_root).resolve()
    if not root.is_dir():
        _fail(f"source root is not a directory: {root}")

    # Recompute the manifest identity from its own bytes.  The release gate
    # must not merely trust a loader-populated ``raw_sha256`` attribute or a
    # caller-provided manifest object.  Synthetic tests may patch this source
    # contract, but every real release manifest is required to be on disk.
    manifest_path = getattr(manifest, "path", None)
    if manifest_path is None:
        _fail("manifest path is missing")
    try:
        manifest_bytes = Path(manifest_path).read_bytes()
        manifest_document = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot self-verify manifest bytes: {exc}")
    if sha256_bytes(manifest_bytes) != manifest.raw_sha256:
        _fail("manifest raw SHA-256 mismatch")
    if not isinstance(manifest_document, dict) or manifest_document.get("manifest_id") != manifest.manifest_id:
        _fail("manifest ID self-verification mismatch")

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

    # Independently recalculate the two primary table occurrence sets from
    # source JSON.  This protects against a manifest whose sealed row list or
    # row checksums were copied from a builder report without matching the
    # pinned checkout.
    table_digests: dict[str, str] = {}
    table_contracts = getattr(manifest, "tables", {})
    if not isinstance(table_contracts, dict):
        _fail("manifest table inventory is not an object")
    for table, table_spec in table_contracts.items():
        if not isinstance(table_spec, dict):
            _fail(f"manifest table inventory is malformed: {table}")
        relative_path = table_spec.get("relative_path")
        if not isinstance(relative_path, str):
            _fail(f"manifest table path is missing: {table}")
        table_path = root.joinpath(*relative_path.split("/"))
        try:
            parsed = json.loads(table_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            _fail(f"cannot parse sealed table {table}: {exc}")
        if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
            _fail(f"sealed table is not an array of objects: {table}")
        native_field = table_spec.get("native_field", "id")
        rows: list[dict[str, Any]] = []
        native_ids: set[str] = set()
        for index, raw_row in enumerate(parsed):
            if native_field not in raw_row:
                _fail(f"sealed table row has no native field: {table}/{index}")
            native_id = str(raw_row[native_field])
            if native_id in native_ids:
                _fail(f"sealed table has duplicate native ID: {table}/{native_id}")
            native_ids.add(native_id)
            rows.append(
                {
                    "pointer": f"/{index}",
                    "native_id": native_id,
                    "raw_sha256": sha256_text(canonical_json(raw_row)),
                    "family": "materials",
                }
            )
        if table_spec.get("source_inventory_count") != len(rows) or table_spec.get("selected_count") != len(rows):
            _fail(f"sealed table inventory count mismatch: {table}")
        if table_spec.get("selected_rows") != rows:
            _fail(f"sealed table selected rows mismatch: {table}")
        table_digest = _set_digest(rows)
        if table_spec.get("selected_set_digest") != table_digest:
            _fail(f"sealed table occurrence digest mismatch: {table}")
        table_digests[str(table)] = table_digest

    digest = _set_digest(file_contracts)
    if digest != manifest.expected["occurrence_set_digests"]["files"]:
        _fail("sealed source file occurrence digest mismatch")
    return {
        "root": str(root),
        "commit": commit,
        "tree": tree,
        "clean": True,
        "selected_files": len(file_contracts),
        "files_digest": digest,
        "table_occurrence_digests": table_digests,
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


def _verify_sqlite_runtime_features(conn: sqlite3.Connection) -> None:
    """Fail closed when the DB engine cannot honor the verifier's SQL plan."""

    row = conn.execute("SELECT sqlite_version()").fetchone()
    try:
        version = tuple(int(part) for part in str(row[0]).split(".")[:3])
    except (AttributeError, IndexError, TypeError, ValueError) as exc:
        _fail(f"SQLite version is not parseable: {exc}")
    if version < (3, 35, 0):
        _fail(f"SQLite >= 3.35 is required for the B6 verifier: {version}")

    # The candidate audit deliberately pins this join to the schema-9
    # evidence-container primary-key index.  Verify that exact planner
    # contract before executing the query; an alternate schema must fail
    # closed instead of silently taking a quadratic plan.
    index_names = {
        str(index_row[1])
        for index_row in conn.execute(
            "PRAGMA index_list('static_evidence_container')"
        ).fetchall()
        if len(index_row) > 1
    }
    if "sqlite_autoindex_static_evidence_container_1" not in index_names:
        _fail("schema-9 evidence-container primary-key index is unavailable")


def _verify_binding(conn: sqlite3.Connection, manifest: Any, identity: dict[str, Any]) -> None:
    source = manifest.source
    snapshot_binding = expected_phase4b6_source_snapshot_binding(manifest)
    expected_license = source.get("license_note")
    row = conn.execute(
        "SELECT id,kind,url,license_note FROM source_catalog WHERE id=?",
        (source["source_id"],),
    ).fetchone()
    if row is None or tuple(row) != (source["source_id"], "primary", source["upstream"], expected_license):
        _fail("source_catalog binding mismatch")

    row = conn.execute(
        'SELECT source_id,release_label,build,"commit",tree,observed_at, '
        'status,metadata_json '
        "FROM source_snapshot WHERE id=?",
        (identity["snapshot_id"],),
    ).fetchone()
    if (
        row is None
        or tuple(row[column] for column in ("source_id", "release_label", "build", "commit", "tree", "observed_at"))
        != snapshot_binding["core"]
        or row["status"] != snapshot_binding["status"]
        or row["metadata_json"] != snapshot_binding["metadata_json"]
    ):
        _fail("source_snapshot binding mismatch")

    row = conn.execute(
        "SELECT parser_version,mapping_version,fingerprint,config_checksum,created_at "
        "FROM parser_build WHERE id=?",
        (identity["parser_build_id"],),
    ).fetchone()
    expected_parser = (
        manifest.versions["parser"],
        manifest.versions["projection"],
        "static-phase4b6-sealed-material-corpus",
        identity["config_checksum"],
        source["observed_at"],
    )
    if row is None or tuple(row) != expected_parser:
        _fail("parser_build binding mismatch")

    row = conn.execute(
        "SELECT snapshot_id,parser_build_id,scope_json,config_json,status,created_at "
        "FROM ingest_run WHERE id=?",
        (identity["ingest_run_id"],),
    ).fetchone()
    expected_run = (
        identity["snapshot_id"],
        identity["parser_build_id"],
        identity["scope_json"],
        identity["config_json"],
        "complete",
        source["observed_at"],
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
        _fail(f"unexpected Phase 4B6 ingest run set: {phase_runs}")


def _verify_files(
    conn: sqlite3.Connection,
    manifest: Any,
    identity: dict[str, Any],
    source_root: str | Path | None,
) -> None:
    expected_root = None
    if source_root is not None:
        root = Path(source_root).resolve()
        expected_root = os.path.normcase(str(root))
    for spec in manifest.files:
        file_id = digest_id("sf", identity["snapshot_id"], spec.relative_path)
        row = conn.execute("SELECT * FROM source_file WHERE id=?", (file_id,)).fetchone()
        if row is None:
            _fail(f"source_file is missing: {spec.relative_path}")
        actual_root = os.path.normcase(str(Path(str(row["root_path"])).resolve(strict=False)))
        expected = (
            identity["snapshot_id"], spec.relative_path, None, spec.sha256,
            spec.size_bytes, spec.format,
        )
        actual = (
            row["snapshot_id"], row["relative_path"], row["upstream_blob_oid"], row["sha256"],
            row["size_bytes"], row["format"],
        )
        if actual != expected or (
            expected_root is not None and actual_root != expected_root
        ):
            _fail(f"source_file attribute mismatch: {spec.relative_path}")


def _container_rows(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT c.*, sf.relative_path, sr.pointer,
                         sr.record_kind, sr.native_id AS record_native_id,
                         sr.raw_sha256, sr.raw_json
                    FROM static_evidence_container c
                    JOIN source_file sf ON sf.id=c.source_file_id
                    JOIN source_record sr ON sr.id=c.source_record_id
                   WHERE c.ingest_run_id=?
                   ORDER BY c.id""",
        (run_id,),
    ).fetchall()


def _verify_containers(
    rows: list[sqlite3.Row],
    manifest: Any,
    identity: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, sqlite3.Row], dict[str, str]]:
    contracts: list[dict[str, Any]] = []
    by_id: dict[str, sqlite3.Row] = {}
    family_by_id: dict[str, str] = {}
    manifest_id = manifest.manifest_id
    manifest_sha = manifest.raw_sha256
    table_specs = getattr(manifest, "tables", {})
    table_paths = {
        str(table): str(spec["relative_path"])
        for table, spec in table_specs.items()
        if isinstance(spec, dict) and isinstance(spec.get("relative_path"), str)
    }

    for row in rows:
        if row["ingest_run_id"] != identity["ingest_run_id"] or row["snapshot_id"] != identity["snapshot_id"]:
            _fail(f"container {row['id']} run/snapshot binding mismatch")
        expected_file_id = digest_id("sf", identity["snapshot_id"], row["relative_path"])
        expected_record_id = digest_id("sr", expected_file_id, row["pointer"])
        expected_container_id = digest_id(
            "static-container",
            identity["snapshot_id"],
            manifest.raw_sha256,
            identity["ingest_run_id"],
            row["relative_path"],
            row["pointer"],
        )
        if (
            row["id"] != expected_container_id
            or row["source_file_id"] != expected_file_id
            or row["source_record_id"] != expected_record_id
        ):
            _fail(f"container {row['id']} occurrence identity mismatch")
        if row["container_kind"] != "excel_row" or row["locale"] is not None:
            _fail(f"container {row['id']} is outside the B6 Excel-row boundary")
        expected_path = table_paths.get(str(row["source_family"]))
        if expected_path is None or row["relative_path"] != expected_path:
            _fail(f"container {row['id']} source table/file binding mismatch")
        if row["candidate_namespace"] != f"agd.static.{row['source_family']}":
            _fail(f"container {row['id']} candidate namespace mismatch")
        candidate_key = _json(row["candidate_key_json"], f"container {row['id']} candidate key")
        expected_key = {
            "table": row["source_family"],
            "native_id": row["record_native_id"],
        }
        if candidate_key != expected_key:
            _fail(f"container {row['id']} candidate key mismatch")
        metadata = _json(row["metadata_json"], f"container {row['id']} metadata")
        if not isinstance(metadata, dict):
            _fail(f"container {row['id']} metadata must be an object")
        required = {"family", "manifest_id", "manifest_raw_sha256", "raw_evidence_identity"}
        if row["container_kind"] == "readable_file":
            required |= {"classification", "cross_language_alignment"}
        if set(metadata) != required:
            _fail(f"container {row['id']} metadata keys mismatch")
        if metadata["family"] != "materials":
            _fail(f"container {row['id']} family mismatch: {metadata['family']}")
        if metadata["manifest_id"] != manifest_id or metadata["manifest_raw_sha256"] != manifest_sha:
            _fail(f"container {row['id']} manifest binding mismatch")
        if metadata["raw_evidence_identity"] != "snapshot_source_coordinate_ingest_run_occurrence":
            _fail(f"container {row['id']} raw identity scope mismatch")
        by_id[row["id"]] = row
        family_by_id[row["id"]] = "materials"
        contracts.append(
            {
                "family": "materials",
                "source_path": row["relative_path"],
                "record_pointer": row["pointer"],
                "source_family": row["source_family"],
                "locale": row["locale"],
                "native_id": row["record_native_id"],
            }
        )
    return contracts, by_id, family_by_id


def _verify_table_inventory(
    rows: list[sqlite3.Row],
    manifest: Any,
) -> None:
    """Match every sealed table coordinate and raw row checksum independently."""

    table_specs = getattr(manifest, "tables", {})
    if not isinstance(table_specs, dict):
        _fail("manifest table inventory is not an object")
    for table, spec in table_specs.items():
        if not isinstance(spec, dict):
            _fail(f"manifest table contract is not an object: {table}")
        selected = spec.get("selected_rows")
        if not isinstance(selected, list):
            _fail(f"manifest selected rows are not an array: {table}")
        actual = [
            {
                "pointer": str(row["pointer"]),
                "native_id": str(row["record_native_id"]),
                "raw_sha256": str(row["raw_sha256"]),
                "family": "materials",
            }
            for row in rows
            if str(row["source_family"]) == str(table)
        ]
        for item in actual:
            if not item["pointer"].startswith("/") or not item["pointer"][1:].isdigit():
                _fail(f"selected table pointer is malformed: {table}")
        actual.sort(key=lambda item: int(item["pointer"][1:]))
        if any(not isinstance(item, dict) for item in selected):
            _fail(f"manifest selected row contract is malformed: {table}")
        expected = [dict(item) for item in selected]
        if actual != expected:
            _fail(f"selected table inventory mismatch: {table}")
        selected_digest = spec.get("selected_set_digest")
        if isinstance(selected_digest, str) and selected_digest != _set_digest(actual):
            _fail(f"selected table inventory digest mismatch: {table}")


def _verify_segments(
    conn: sqlite3.Connection,
    run_id: str,
    snapshot_id: str,
    family_by_id: dict[str, str],
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT s.*, sf.relative_path, sr.pointer AS record_pointer
             FROM static_segment_occurrence s
             JOIN static_evidence_container c ON c.id=s.evidence_container_id
             JOIN source_file sf ON sf.id=c.source_file_id
             JOIN source_record sr ON sr.id=s.source_record_id
            WHERE s.ingest_run_id=?
            ORDER BY s.id""",
        (run_id,),
    ).fetchall()
    contracts: list[dict[str, Any]] = []
    for row in rows:
        if row["snapshot_id"] != snapshot_id or row["evidence_container_id"] not in family_by_id:
            _fail(f"segment {row['id']} is outside the B6 owned container set")
        contracts.append(
            {
                "family": family_by_id[row["evidence_container_id"]],
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
    snapshot_id: str,
    by_container_id: dict[str, sqlite3.Row],
    manifest: Any,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT r.*, sf.relative_path AS source_relative_path,
                         sr.pointer AS source_record_pointer
                    FROM static_relationship_occurrence r
                    JOIN static_evidence_container c ON c.id=r.source_container_id
                    JOIN source_file sf ON sf.id=c.source_file_id
                    JOIN source_record sr ON sr.id=r.source_record_id
                   WHERE r.ingest_run_id=?
                   ORDER BY r.id""",
        (run_id,),
    ).fetchall()
    contracts: list[dict[str, Any]] = []
    table_names = set(getattr(manifest, "tables", {}))
    text_field_contract = _material_text_field_contract(manifest)
    for row in rows:
        if row["snapshot_id"] != snapshot_id or row["source_container_id"] not in by_container_id:
            _fail(f"relationship {row['id']} is outside the B6 owned container set")
        expected_id = digest_id(
            "static-relationship",
            snapshot_id,
            manifest.raw_sha256,
            run_id,
            row["source_relative_path"],
            row["source_record_pointer"],
            row["source_pointer"],
        )
        if row["id"] != expected_id:
            _fail(f"relationship {row['id']} occurrence identity mismatch")
        source_container = by_container_id[row["source_container_id"]]
        if row["source_family"] not in table_names or source_container["source_family"] != row["source_family"]:
            _fail(f"relationship {row['id']} source table binding mismatch")
        if source_container["source_record_id"] != row["source_record_id"]:
            _fail(f"relationship {row['id']} source record binding mismatch")
        target_record = None
        if row["target_evidence_container_id"] is not None:
            target = by_container_id.get(row["target_evidence_container_id"])
            if target is None:
                _fail(f"relationship {row['id']} points outside the B6 run")
            if target["snapshot_id"] != snapshot_id or target["ingest_run_id"] != run_id:
                _fail(f"relationship {row['id']} target is outside the B6 run")
            if row["target_family"] != target["source_family"]:
                _fail(f"relationship {row['id']} target family mismatch")
            target_record = {
                "family": "Readable",
                "relative_path": target["relative_path"],
                "pointer": target["pointer"],
            } if target["container_kind"] == "readable_file" else {
                "table": target["source_family"],
                "pointer": target["pointer"],
            }
        metadata = _json(row["metadata_json"], f"relationship {row['id']} metadata")
        if not isinstance(metadata, dict) or set(metadata) != {
            "family",
            "raw_value",
            "manifest_id",
            "candidate_resolution_detail",
            "manifest_field_role",
            "manifest_relation_kind",
            "storage_field_role",
        }:
            _fail(f"relationship {row['id']} family metadata mismatch")
        if metadata["family"] != "materials" or metadata["manifest_id"] != manifest.manifest_id:
            _fail(f"relationship {row['id']} manifest metadata mismatch")
        if metadata["candidate_resolution_detail"] != "family_specific_raw_value_and_sealed_candidate_set":
            _fail(f"relationship {row['id']} candidate-resolution metadata mismatch")
        source_field = str(row["source_pointer"]).rsplit("/", 1)[-1]
        if row["target_family"] == "TextMap":
            expected_role = text_field_contract.get((str(row["source_family"]), source_field))
            if expected_role is None:
                _fail(f"relationship {row['id']} TextMap field is outside the manifest contract")
            expected_storage_role = (
                "verified_textmap_reference"
                if row["candidate_resolution_status"] == "resolved"
                else "unresolved_field_role"
            )
        elif row["relation_kind"] == "material_codex_material_candidate":
            expected_role = ("foreign_pointer_candidate", row["relation_kind"])
            expected_storage_role = "foreign_pointer_candidate"
        else:
            _fail(f"relationship {row['id']} is outside the B6 relationship contract")
        if (
            metadata["manifest_field_role"] != expected_role[0]
            or metadata["manifest_relation_kind"] != expected_role[1]
            or metadata["storage_field_role"] != expected_storage_role
            or row["field_role"] != expected_storage_role
        ):
            _fail(f"relationship {row['id']} field-role metadata mismatch")
        candidate_key = _json(row["target_candidate_key_json"], f"relationship {row['id']} candidate key")
        ambiguity = _json(row["ambiguity_json"], f"relationship {row['id']} ambiguity")
        if row["target_family"] == "TextMap":
            if row["candidate_resolution_status"] == "resolved":
                if not isinstance(candidate_key, dict) or set(candidate_key) != {"textmap_hash"}:
                    _fail(f"relationship {row['id']} TextMap candidate key mismatch")
            elif row["candidate_resolution_status"] == "unresolved":
                if candidate_key is not None:
                    _fail(f"relationship {row['id']} unresolved TextMap candidate must be null")
            else:
                _fail(f"relationship {row['id']} TextMap owner status mismatch")
            if row["target_evidence_container_id"] is not None:
                _fail(f"relationship {row['id']} TextMap relationship has an evidence target")
        elif row["relation_kind"] == "material_codex_material_candidate":
            if row["target_family"] != "MaterialExcelConfigData":
                _fail(f"relationship {row['id']} MaterialCodex target mismatch")
            if row["candidate_resolution_status"] == "resolved" and target_record is None:
                _fail(f"relationship {row['id']} resolved without an evidence target")
        else:
            _fail(f"relationship {row['id']} is outside the B6 relationship contract")
        contracts.append(
            {
                "family": "materials",
                "source_table": row["source_family"],
                "source_record_pointer": row["source_record_pointer"],
                "source_pointer": row["source_pointer"],
                "relation_kind": row["relation_kind"],
                "field_role": metadata["manifest_field_role"],
                "raw_value": metadata.get("raw_value"),
                "target_family": row["target_family"],
                "target_candidate_key": candidate_key,
                "status": row["candidate_resolution_status"],
                "target_record": target_record,
                "ambiguity": ambiguity or {},
            }
        )
    return contracts


def _text_owner_contracts(relationships: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruct the manifest's owning-field occurrences from relationships."""

    owners: list[dict[str, Any]] = []
    for item in relationships:
        if item["target_family"] != "TextMap" or item["status"] != "resolved":
            continue
        candidate = item["target_candidate_key"]
        if not isinstance(candidate, dict) or set(candidate) != {"textmap_hash"}:
            _fail("TextMap owner candidate key is malformed")
        owners.append(
            {
                "family": "materials",
                "source_table": item["source_table"],
                "source_record_pointer": item["source_record_pointer"],
                "source_pointer": item["source_pointer"],
                "field": item["source_pointer"].rsplit("/", 1)[-1],
                "field_role": item["field_role"],
                "relation_kind": item["relation_kind"],
                "textmap_hash": str(candidate["textmap_hash"]),
            }
        )
    return owners


def _verify_text_resolutions(
    conn: sqlite3.Connection,
    run_id: str,
    snapshot_id: str,
    manifest: Any,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT tr.*, sf.relative_path AS source_relative_path,
                         sr.pointer AS source_record_pointer,
                         r.source_family AS owner_source_family,
                         r.source_pointer AS owner_source_pointer,
                         r.target_family AS owner_target_family,
                         r.field_role AS owner_field_role,
                         r.candidate_resolution_status AS owner_status,
                         r.target_candidate_key_json AS owner_candidate_key
                    FROM static_text_resolution_occurrence tr
                    JOIN static_evidence_container c ON c.id=tr.source_container_id
                    JOIN static_relationship_occurrence r
                      ON r.id=tr.static_relationship_occurrence_id
                     AND r.ingest_run_id=tr.ingest_run_id
                     AND r.snapshot_id=tr.snapshot_id
                     AND r.source_container_id=tr.source_container_id
                     AND r.source_record_id=tr.source_record_id
                     AND r.source_pointer=tr.source_pointer
                    JOIN source_file sf ON sf.id=c.source_file_id
                    JOIN source_record sr ON sr.id=tr.source_record_id
                   WHERE tr.ingest_run_id=?
                   ORDER BY tr.id""",
        (run_id,),
    ).fetchall()
    contracts: list[dict[str, Any]] = []
    text_field_contract = _material_text_field_contract(manifest)
    for row in rows:
        if row["snapshot_id"] != snapshot_id:
            _fail(f"text resolution {row['id']} snapshot mismatch")
        expected_id = digest_id(
            "static-text-resolution",
            snapshot_id,
            manifest.raw_sha256,
            run_id,
            row["static_relationship_occurrence_id"],
            row["locale"],
        )
        if row["id"] != expected_id:
            _fail(f"text resolution {row['id']} occurrence identity mismatch")
        if row["owner_target_family"] != "TextMap" or row["owner_field_role"] != "verified_textmap_reference":
            _fail(f"resolution {row['id']} is not owned by a TextMap relationship")
        if row["owner_status"] != "resolved":
            _fail(f"resolution {row['id']} owner relationship is not resolved")
        owner_key = _json(row["owner_candidate_key"], f"resolution {row['id']} owner candidate key")
        if not isinstance(owner_key, dict) or set(owner_key) != {"textmap_hash"}:
            _fail(f"resolution {row['id']} owner candidate key mismatch")
        if str(owner_key["textmap_hash"]) != str(row["textmap_hash"]):
            _fail(f"resolution {row['id']} TextMap hash mismatch")
        metadata = _json(row["metadata_json"], f"resolution {row['id']} metadata")
        expected_metadata = {
            "family": "materials",
            "locator_only": True,
            "owning_static_source": {
                "relative_path": row["source_relative_path"],
                "record_pointer": row["source_record_pointer"],
                "source_pointer": row["source_pointer"],
            },
            "both_conflict_auto_selected": False,
        }
        if metadata != expected_metadata:
            _fail(f"resolution {row['id']} family metadata mismatch")
        if row["source_pointer"] != row["owner_source_pointer"] or row["owner_source_family"] not in set(getattr(manifest, "tables", {})):
            _fail(f"resolution {row['id']} owner coordinate mismatch")
        source_table = row["source_relative_path"].split("/")[-1].replace(".json", "")
        source_field = row["source_pointer"].split("/")[-1]
        text_field_role = text_field_contract.get((source_table, source_field))
        if text_field_role is None:
            _fail(f"resolution {row['id']} TextMap field contract mismatch")
        contracts.append(
            {
                "family": "materials",
                "source_table": source_table,
                "source_record_pointer": row["source_record_pointer"],
                "source_pointer": row["source_pointer"],
                "field": source_field,
                "field_role": text_field_role[0],
                "relation_kind": text_field_role[1],
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
    snapshot_id: str,
    manifest: Any,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """WITH scoped_candidates AS MATERIALIZED (
                         SELECT *
                           FROM static_text_candidate_occurrence
                          WHERE ingest_run_id=?
                    )
               SELECT tc.*, sf.relative_path AS source_relative_path,
                         owner_sf.relative_path AS owner_relative_path,
                         owner_sr.pointer AS owner_record_pointer,
                         tr.source_pointer AS owner_source_pointer,
                         tr.textmap_hash AS tr_textmap_hash,
                         tr.resolution_status AS owner_resolution_status,
                         tr.source_container_id AS owner_container_id
                    FROM scoped_candidates tc
                    JOIN source_file sf ON sf.id=tc.source_file_id
                    JOIN static_text_resolution_occurrence tr
                      ON tr.id=tc.resolution_occurrence_id
                     AND tr.ingest_run_id=tc.ingest_run_id
                     AND tr.snapshot_id=tc.snapshot_id
                    JOIN static_evidence_container owner_c
                      INDEXED BY sqlite_autoindex_static_evidence_container_1
                      ON owner_c.id=tr.source_container_id
                     AND owner_c.ingest_run_id=tr.ingest_run_id
                     AND owner_c.snapshot_id=tr.snapshot_id
                    JOIN source_file owner_sf ON owner_sf.id=owner_c.source_file_id
                   JOIN source_record owner_sr ON owner_sr.id=owner_c.source_record_id
                   """,
        (run_id,),
    ).fetchall()
    contracts: list[dict[str, Any]] = []
    text_field_contract = _material_text_field_contract(manifest)
    for row in rows:
        if row["snapshot_id"] != snapshot_id:
            _fail(f"text candidate {row['id']} snapshot mismatch")
        expected_id = digest_id(
            "static-text-candidate",
            snapshot_id,
            manifest.raw_sha256,
            run_id,
            row["resolution_occurrence_id"],
            row["source_relative_path"],
            row["source_pointer"],
            row["shard_kind"],
        )
        if row["id"] != expected_id:
            _fail(f"text candidate {row['id']} occurrence identity mismatch")
        if row["owner_resolution_status"] != row["resolution_status"]:
            _fail(f"text candidate {row['id']} resolution status mismatch")
        if str(row["source_pointer"]) != f"/{row['tr_textmap_hash']}":
            _fail(f"text candidate {row['id']} source pointer mismatch")
        boundary_paths = getattr(manifest, "boundaries", {}).get("textmap_shards", {})
        if isinstance(boundary_paths, dict):
            locale_paths = boundary_paths.get(row["locale"])
            if isinstance(locale_paths, dict) and row["source_relative_path"] != locale_paths.get(row["shard_kind"]):
                _fail(f"text candidate {row['id']} TextMap shard binding mismatch")
        metadata = _json(row["metadata_json"], f"candidate {row['id']} metadata")
        expected_metadata = {
            "family": "materials",
            "owning_static_source": {
                "relative_path": row["owner_relative_path"],
                "record_pointer": row["owner_record_pointer"],
                "source_pointer": row["owner_source_pointer"],
            },
        }
        if metadata != expected_metadata:
            _fail(f"candidate {row['id']} family metadata mismatch")
        if row["owner_source_pointer"] == row["source_pointer"]:
            _fail(f"candidate {row['id']} owner/source pointer collision")
        if row["raw_checksum"] != sha256_text(row["raw_text"]):
            _fail(f"candidate {row['id']} raw checksum mismatch")
        normalized = normalize_text(str(row["raw_text"]))
        if row["normalized_text"] != normalized or row["normalized_checksum"] != sha256_text(normalized):
            _fail(f"candidate {row['id']} normalized text mismatch")
        source_table = row["owner_relative_path"].split("/")[-1].replace(".json", "")
        source_field = row["owner_source_pointer"].split("/")[-1]
        text_field_role = text_field_contract.get((source_table, source_field))
        if text_field_role is None:
            _fail(f"candidate {row['id']} TextMap field contract mismatch")
        contracts.append(
            {
                "family": "materials",
                "source_table": source_table,
                "source_record_pointer": row["owner_record_pointer"],
                "source_pointer": row["owner_source_pointer"],
                "field": source_field,
                "field_role": text_field_role[0],
                "relation_kind": text_field_role[1],
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
    snapshot_id: str,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, SourceRecordContract],
]:
    rows = conn.execute(
        """SELECT DISTINCT sr.id, sr.source_file_id, sf.relative_path, sr.pointer,
                                sr.record_kind, sr.native_id,
                                sr.raw_sha256, sr.raw_json
                           FROM source_record sr
                           JOIN source_file sf ON sf.id=sr.source_file_id
                          WHERE sr.id IN (
                                SELECT source_record_id
                                  FROM static_evidence_container
                                 WHERE ingest_run_id=?
                                UNION
                                SELECT source_record_id
                                  FROM static_text_candidate_occurrence
                                 WHERE ingest_run_id=?
                          )
                          ORDER BY sf.relative_path, sr.pointer""",
        (run_id, run_id),
    ).fetchall()
    records: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []
    validated_records: dict[str, SourceRecordContract] = {}
    for row in rows:
        raw_json = row["raw_json"]
        parsed_raw = _json(raw_json, f"source record {row['id']} raw_json")
        expected_record_id = digest_id(
            "sr",
            digest_id("sf", snapshot_id, row["relative_path"]),
            row["pointer"],
        )
        if row["id"] != expected_record_id:
            _fail(f"source record {row['id']} occurrence identity mismatch")
        if row["record_kind"] == "readable_file":
            try:
                raw_text = parsed_raw["text"]
            except (TypeError, KeyError, json.JSONDecodeError) as exc:
                _fail(f"readable source record {row['id']} is malformed: {exc}")
            db_raw_json = canonical_json(raw_text)
        else:
            db_raw_json = raw_json
        if row["record_kind"] == "excel_row":
            if not isinstance(parsed_raw, dict) or row["raw_sha256"] != sha256_text(raw_json):
                _fail(f"Excel source record {row['id']} raw checksum mismatch")
        elif row["record_kind"] == "textmap_entry":
            if not isinstance(parsed_raw, str) or row["raw_sha256"] != sha256_text(parsed_raw):
                _fail(f"TextMap source record {row['id']} raw checksum mismatch")
        elif row["record_kind"] == "readable_file":
            if not isinstance(parsed_raw, dict) or not isinstance(parsed_raw.get("text"), str):
                _fail(f"readable source record {row['id']} is malformed")
        records.append(
            {
                "source_path": row["relative_path"],
                "pointer": row["pointer"],
                "record_kind": (
                    "static_" + row["record_kind"]
                    if not str(row["record_kind"]).startswith("static_")
                    else row["record_kind"]
                ),
                "native_id": row["native_id"],
                "raw_sha256": row["raw_sha256"],
                "raw_json": db_raw_json,
            }
        )
        validated_records[str(row["id"])] = SourceRecordContract.from_row(row)
        if "TextMap" in row["relative_path"]:
            assets.append(
                {
                    "source_path": row["relative_path"],
                    "source_pointer": row["pointer"],
                    "locale": (
                        "CHS" if "CHS" in row["relative_path"]
                        else "EN" if "EN" in row["relative_path"]
                        else "JP"
                    ),
                    "shard_kind": "medium" if "Medium" in row["relative_path"] else "main",
                    "textmap_hash": row["native_id"],
                    "raw_checksum": row["raw_sha256"],
                }
            )
    return records, assets, validated_records


def _verify_reconciliations(
    manifest: Any,
    containers: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    resolutions: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    source_records: list[dict[str, Any]],
    text_assets: list[dict[str, Any]],
    text_owners: list[dict[str, Any]],
) -> dict[str, Any]:
    """Check manifest cardinalities/statuses without consulting the builder."""

    expected = getattr(manifest, "expected", {})
    if not isinstance(expected, dict):
        _fail("manifest expected section is not an object")

    actual_counts = {
        "files": len(manifest.files),
        "source_records": len(source_records),
        "containers": len(containers),
        "segments": len(segments),
        "relationships": len(relationships),
        "text_resolutions": len(resolutions),
        "text_candidates": len(candidates),
        "text_assets": len(text_assets),
        "text_owner_occurrences": len(text_owners),
    }
    actual_inventory_counts = dict(actual_counts)
    expected_counts = expected.get("counts")
    if isinstance(expected_counts, dict):
        expected_normalized = {
            "files": expected_counts.get("selected_files", expected_counts.get("files")),
            "source_records": expected_counts.get("source_records"),
            "containers": expected_counts.get("containers"),
            "segments": expected_counts.get("segments", 0),
            "relationships": expected_counts.get("relationships"),
            "text_resolutions": expected_counts.get("text_resolutions"),
            "text_candidates": expected_counts.get("text_candidates"),
            "text_assets": expected_counts.get("text_assets"),
            "text_owner_occurrences": expected_counts.get("text_owner_occurrences"),
        }
        if {key: actual_counts[key] for key in expected_normalized} != expected_normalized:
            _fail(f"occurrence counts mismatch: {actual_counts}")

    rel_status = Counter(item["status"] for item in relationships)
    resolution_status = Counter(item["status"] for item in resolutions)
    status_counts = {
        "relationships": {key: rel_status[key] for key in _STATUS_KEYS},
        "text_resolutions": {key: resolution_status[key] for key in _TEXT_STATUS_KEYS},
    }
    expected_rel_status = expected.get("relationship_statuses")
    if expected_rel_status is None:
        expected_rel_status = (expected.get("status_counts") or {}).get("relationships")
    if expected_rel_status is not None and status_counts["relationships"] != expected_rel_status:
        _fail("relationship status reconciliation mismatch")
    expected_text_status = expected.get("text_resolution_statuses")
    if expected_text_status is None:
        expected_text_status = (expected.get("status_counts") or {}).get("text_resolutions")
    if expected_text_status is not None and status_counts["text_resolutions"] != expected_text_status:
        _fail("TextMap resolution status reconciliation mismatch")

    relation_kind_counts = Counter(item["relation_kind"] for item in relationships)
    expected_relation_kinds = expected.get("relationship_kind_counts")
    if expected_relation_kinds is not None and dict(sorted(relation_kind_counts.items())) != expected_relation_kinds:
        _fail("relationship kind reconciliation mismatch")
    field_counts = Counter(
        f"{item['source_table']}.{item['field']}"
        for item in text_owners
    )
    expected_field_counts = expected.get("field_occurrence_counts")
    if expected_field_counts is not None and dict(sorted(field_counts.items())) != expected_field_counts:
        _fail("TextMap owning-field reconciliation mismatch")

    locale_breakdown: dict[str, dict[str, int]] = {}
    for item in resolutions:
        locale_breakdown.setdefault(item["locale"], {"text_resolutions": 0, "text_candidates": 0, "text_assets": 0})["text_resolutions"] += 1
    for item in candidates:
        locale_breakdown.setdefault(item["locale"], {"text_resolutions": 0, "text_candidates": 0, "text_assets": 0})["text_candidates"] += 1
    for item in text_assets:
        locale_breakdown.setdefault(item["locale"], {"text_resolutions": 0, "text_candidates": 0, "text_assets": 0})["text_assets"] += 1
    locale_breakdown = {key: locale_breakdown[key] for key in sorted(locale_breakdown)}
    expected_locales = expected.get("locale_breakdown")
    if expected_locales is not None and locale_breakdown != expected_locales:
        _fail("locale reconciliation mismatch")

    ids_by_table: dict[str, set[str]] = {}
    for item in containers:
        ids_by_table.setdefault(item["source_family"], set()).add(str(item["native_id"]))
    id_owners: Counter[str] = Counter(
        native_id
        for ids in ids_by_table.values()
        for native_id in ids
    )
    collisions = sum(1 for count in id_owners.values() if count > 1)
    expected_collisions = expected.get("cross_table_numeric_id_collisions")
    if expected_collisions is not None and collisions != expected_collisions:
        _fail("cross-table numeric ID collision reconciliation mismatch")

    family_counts = {
        "materials": {
            "containers": len(containers),
            "segments": len(segments),
            "relationships": len(relationships),
            "text_resolutions": len(resolutions),
            "text_candidates": len(candidates),
            "text_assets": len(text_assets),
            "text_owner_occurrences": len(text_owners),
        }
    }
    family_inventory_counts = {
        "materials": {
            **family_counts["materials"],
            "text_assets": len(text_assets),
            "text_owner_occurrences": len(text_owners),
        }
    }
    expected_families = expected.get("families")
    if expected_families is None:
        expected_families = expected.get("family_counts")
    if expected_families is not None:
        for family, expected_family in expected_families.items():
            actual_family = family_inventory_counts.get(family)
            if actual_family is None or any(actual_family.get(key) != value for key, value in expected_family.items()):
                _fail("family occurrence counts mismatch")

    # The manifest's file digest is independently checked against bytes in
    # verify_phase4b6_material_source_contract; retain it here as the exact
    # source-set identity rather than trusting a build report.
    file_contracts = [
        {
            "relative_path": item.relative_path,
            "format": item.format,
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
        }
        for item in manifest.files
    ]
    digests = {
        "files": _set_digest(file_contracts),
        "source_records": _set_digest(source_records),
        "containers": _set_digest(containers),
        "segments": _set_digest(segments),
        "relationships": _set_digest(relationships),
        "text_resolutions": _set_digest(resolutions),
        "text_candidates": _set_digest(candidates),
        "text_assets": _set_digest(text_assets),
        "text_owners": _set_digest(text_owners),
    }
    expected_digests = expected.get("occurrence_set_digests")
    if not isinstance(expected_digests, dict) or digests != expected_digests:
        differing = [key for key in digests if not isinstance(expected_digests, dict) or digests[key] != expected_digests.get(key)]
        _fail(f"occurrence set digest mismatch in database: {differing}")

    return {
        "counts": actual_counts,
        "inventory_counts": actual_inventory_counts,
        "family_counts": family_counts,
        "family_inventory_counts": family_inventory_counts,
        "status_counts": status_counts,
        "locale_breakdown": locale_breakdown,
        "occurrence_set_digests": digests,
    }


def verify_phase4b6_material_database_contract(
    conn: sqlite3.Connection,
    manifest: Any,
    source_root: str | Path | None = None,
    *,
    validated_source_records: dict[str, SourceRecordContract] | None = None,
) -> dict[str, Any]:
    """Recompute all B6 occurrence sets from a read-only SQLite connection."""

    _verify_sqlite_runtime_features(conn)
    schema_version = _verify_schema(conn)
    identity = expected_phase4b6_material_identity(manifest)
    _verify_binding(conn, manifest, identity)
    _verify_files(conn, manifest, identity, source_root)

    run_id = identity["ingest_run_id"]
    container_rows = _container_rows(conn, run_id)
    containers, by_id, family_by_id = _verify_containers(container_rows, manifest, identity)
    _verify_table_inventory(container_rows, manifest)
    segments = _verify_segments(conn, run_id, identity["snapshot_id"], family_by_id)
    relationships = _verify_relationships(conn, run_id, identity["snapshot_id"], by_id, manifest)
    resolutions = _verify_text_resolutions(conn, run_id, identity["snapshot_id"], manifest)
    candidates = _verify_text_candidates(conn, run_id, identity["snapshot_id"], manifest)
    source_records, text_assets, record_rows = _verify_source_records(
        conn, run_id, identity["snapshot_id"]
    )
    text_owners = _text_owner_contracts(relationships)
    reconciled = _verify_reconciliations(
        manifest,
        containers,
        segments,
        relationships,
        resolutions,
        candidates,
        source_records,
        text_assets,
        text_owners,
    )
    counts = reconciled["counts"]
    family_counts = reconciled["family_counts"]
    status_counts = reconciled["status_counts"]
    family_selected_paths = {"materials": sorted({spec.relative_path for spec in manifest.files})}
    expected_selected = manifest.expected.get("family_selected_paths")
    if expected_selected is not None and family_selected_paths != expected_selected:
        _fail("family selected paths mismatch")

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
            "wave1-stage-final-state-v1:phase4b6:" + manifest.manifest_id
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
        "family_inventory_counts": reconciled["family_inventory_counts"],
        "family_selected_paths": family_selected_paths,
        "status_counts": status_counts,
        "inventory_counts": reconciled["inventory_counts"],
        "locale_breakdown": reconciled["locale_breakdown"],
        "occurrence_set_digests": reconciled["occurrence_set_digests"],
        "foreign_key_check": [],
    }


__all__ = [
    "RUN_FAMILY",
    "Phase4B6ContractError",
    "expected_phase4b6_material_identity",
    "expected_phase4b6_source_snapshot_binding",
    "verify_phase4b6_material_source_contract",
    "verify_phase4b6_material_database_contract",
]
