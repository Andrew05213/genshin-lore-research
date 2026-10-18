"""Independent release verification for Phase 4C5B.

The verifier consumes the release-owned source oracle.  It never calls the
projection builder or its preflight and it compares every persisted business
column to tuples recomputed from source coordinates.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from .character_contracts import C2_CONTRACT, C4B_CONTRACT
from .character_phase4c2_schema import verify_character_phase4c2_schema_signature
from .character_phase4c5b_manifest import (
    EXPECTED_BUILD,
    EXPECTED_COMMIT,
    EXPECTED_COUNTS,
    EXPECTED_COVERAGE,
    EXPECTED_PATHS,
    EXPECTED_SNAPSHOT_ID,
    EXPECTED_STATUS_COUNTS,
    strict_json_load,
    strict_json_loads,
)
from .character_phase4c5b_release_contract import (
    PARSER_FINGERPRINT,
    RUN_FAMILY,
    Phase4C5BReleaseExpected,
    Phase4C5BReleaseContractError,
    build_phase4c5b_release_expected,
)
from .util import canonical_json, digest_id


class Phase4C5BVerificationError(RuntimeError):
    pass


def _sidecars(path: Path) -> tuple[Path, Path, Path]:
    return tuple(Path(f"{path}{suffix}") for suffix in ("-journal", "-wal", "-shm"))


def _assert_sidecars_absent(path: Path, stage: str) -> None:
    found = [str(item) for item in _sidecars(path) if item.exists()]
    if found:
        raise Phase4C5BVerificationError(f"Phase 4C5B sidecars absent check failed {stage}: {found}")


def _fingerprint(path: Path) -> dict[str, Any]:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    stable_before = (before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns)
    stable_after = (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns)
    if stable_before != stable_after:
        raise Phase4C5BVerificationError("database changed while fingerprinting")
    return {
        "sha256": digest.hexdigest(), "size_bytes": after.st_size,
        "mtime_ns": after.st_mtime_ns, "inode": after.st_ino,
        "device": after.st_dev, "mode": after.st_mode,
    }


def _fail(message: str) -> None:
    raise Phase4C5BVerificationError(message)


def _schema_versions(conn: sqlite3.Connection) -> list[int]:
    try:
        return [int(row[0]) for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")]
    except sqlite3.Error as exc:
        _fail(f"schema migration ledger is unreadable: {exc}")


def _strict_json(value: Any, label: str) -> Any:
    try:
        return strict_json_loads(str(value), label=label)
    except Exception as exc:
        _fail(f"{label} is not strict JSON: {exc}")


def _verify_all_json_columns(conn: sqlite3.Connection) -> None:
    for table, column in conn.execute("SELECT name,name FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"):
        columns = [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')]
        json_columns = [column for column in columns if column.endswith("_json") or column == "raw_json"]
        for column in json_columns:
            for row in conn.execute(f'SELECT rowid,"{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'):
                _strict_json(row[1], f"{table}.{column}[{row[0]}]")


def _expected_scope(expected: Phase4C5BReleaseExpected) -> str:
    return canonical_json({
        "family": RUN_FAMILY, "manifest_id": expected.manifest.manifest_id,
        "snapshot_id": expected.snapshot_id, "coverage": EXPECTED_COVERAGE,
        "corpus_complete": True, "selected_paths": list(EXPECTED_PATHS),
        "closure_counts": expected.derived["counts"],
        "occurrence_set_digests": expected.derived["occurrence_set_digests"],
        "full_character_scan": False, "full_voice_scan": False,
        "logical_identity_created": False, "mcp_capability_added": False,
    })


def _expected_config(expected: Phase4C5BReleaseExpected) -> str:
    return canonical_json({
        "manifest_raw_sha256": expected.manifest.raw_sha256, "versions": expected.manifest.versions,
        "source_commit": EXPECTED_COMMIT, "source_tree": expected.manifest.source["tree"],
        "coverage": EXPECTED_COVERAGE, "corpus_complete": True,
        "schema_contract": "schema10-migration0010-exact",
        "occurrence_set_digests": expected.derived["occurrence_set_digests"],
        "logical_identity_created": False, "mcp_capability_added": False,
    })


def _verify_provenance(conn: sqlite3.Connection, expected: Phase4C5BReleaseExpected) -> None:
    source = expected.manifest.source
    row = conn.execute("SELECT kind,url,license_note FROM source_catalog WHERE id=?", (source["source_id"],)).fetchone()
    if row is None or tuple(row) != ("primary", source["upstream"], None):
        _fail("Phase 4C5B source_catalog mismatch")
    # source_snapshot is parser-independent.  A combined artifact may already
    # carry parser_version/mapping_version labels written by another accepted
    # family; C5B qualification must not rewrite or conflate those fields.
    row = conn.execute('SELECT source_id,release_label,build,"commit",tree,observed_at,status,metadata_json FROM source_snapshot WHERE id=?', (EXPECTED_SNAPSHOT_ID,)).fetchone()
    expected_snapshot = (source["source_id"], source["release_label"], EXPECTED_BUILD, EXPECTED_COMMIT, source["tree"], source["observed_at"], "active", canonical_json({"sealed_source_manifest": True}))
    if row is None or tuple(row) != expected_snapshot:
        _fail("Phase 4C5B parser-independent source_snapshot mismatch")
    row = conn.execute("SELECT parser_version,mapping_version,fingerprint,config_checksum,created_at FROM parser_build WHERE id=?", (expected.parser_build_id,)).fetchone()
    if row is None or tuple(row) != (expected.manifest.versions["parser"], expected.manifest.versions["projection"], PARSER_FINGERPRINT, expected.config_checksum, source["observed_at"]):
        _fail("Phase 4C5B parser_build mismatch")
    row = conn.execute("SELECT snapshot_id,parser_build_id,scope_json,config_json,status,created_at FROM ingest_run WHERE id=?", (expected.ingest_run_id,)).fetchone()
    if row is None or tuple(row) != (expected.snapshot_id, expected.parser_build_id, _expected_scope(expected), _expected_config(expected), "complete", source["observed_at"]):
        _fail("Phase 4C5B ingest_run binding/status mismatch")
    scope_runs: list[str] = []
    for run_id, scope_json in conn.execute("SELECT id,scope_json FROM ingest_run ORDER BY id"):
        parsed = _strict_json(scope_json, f"ingest_run {run_id} scope_json")
        if isinstance(parsed, dict) and parsed.get("family") == RUN_FAMILY:
            scope_runs.append(str(run_id))
    if scope_runs != [expected.ingest_run_id]:
        _fail(f"Phase 4C5B same-family run set mismatch: {scope_runs}")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        _fail("Phase 4C5B foreign key check failed")


def _verify_source_files(
    conn: sqlite3.Connection,
    expected: Phase4C5BReleaseExpected,
    *,
    allow_extra_source_files: bool = False,
    allow_extra_source_records: bool = False,
) -> None:
    rows = conn.execute(
        "SELECT relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path "
        "FROM source_file WHERE snapshot_id=? ORDER BY relative_path",
        (expected.snapshot_id,),
    ).fetchall()
    source_root = str(expected.source_root)
    want = {
        item["relative_path"]: (
            item["relative_path"], item["blob_oid"], item["sha256"],
            item["size_bytes"], item["format"], source_root,
        )
        for item in expected.derived["files"]
    }
    actual = {str(row[0]): tuple(row) for row in rows}
    if not allow_extra_source_files and set(actual) != set(want):
        _fail("Phase 4C5B source_file closure/root_path mismatch")
    if set(want) - set(actual):
        _fail("Phase 4C5B source_file closure is missing a sealed path")
    for relative_path, wanted in want.items():
        got = actual[relative_path]
        # A C2/C4B artifact may have shared TextMap rows with no Git OID.
        # The content, snapshot/path, format, and source-root identity remain
        # mandatory in that combined-only case.
        oid_ok = got[1] == wanted[1] or (
            allow_extra_source_files
            and relative_path.startswith("TextMap/")
            and got[1] is None
        )
        if (
            not oid_ok
            or got[0] != wanted[0]
            or got[2:] != wanted[2:]
        ):
            _fail("Phase 4C5B source_file closure/root_path mismatch")

    source_file_ids = {
        item["relative_path"]: digest_id(
            "sf", expected.snapshot_id, item["relative_path"]
        )
        for item in expected.derived["files"]
    }
    placeholders = ",".join("?" for _ in source_file_ids)
    record_rows = conn.execute(
        f"SELECT id,source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json "
        f"FROM source_record WHERE source_file_id IN ({placeholders}) ORDER BY id",
        tuple(source_file_ids.values()),
    ).fetchall()
    want_records = {row[0]: tuple(row) for row in expected.sql_rows["source_records"]}
    actual_records = {str(row[0]): tuple(row) for row in record_rows}
    if not allow_extra_source_records and set(actual_records) != set(want_records):
        _fail("Phase 4C5B source_record exact tuple mismatch")
    if set(want_records) - set(actual_records):
        _fail("Phase 4C5B source_record closure is missing a sealed record")
    for record_id, wanted in want_records.items():
        if actual_records[record_id] != wanted:
            _fail("Phase 4C5B source_record exact tuple mismatch")
    if allow_extra_source_records:
        # Existing static corpora may own additional TextMap records in the
        # shared source files.  They are not Character provenance.  Any extra
        # Excel/voice record, or any Character occurrence that references an
        # unsealed record/file, is rejected.
        if any(
            row[3] != "textmap_entry"
            for record_id, row in actual_records.items()
            if record_id not in want_records
        ):
            _fail("Phase 4C5B extra non-TextMap source provenance")
        character_record_columns = {
            "character_row_occurrence": ("source_record_id",),
            "character_raw_slot_occurrence": ("source_record_id",),
            "character_relation_occurrence": ("source_record_id",),
            "character_text_resolution_occurrence": ("source_record_id",),
            "character_text_candidate_occurrence": ("source_record_id",),
            "character_voice_container_occurrence": (
                "lut_source_record_id", "item_source_record_id",
            ),
            "character_audio_variant_occurrence": ("item_source_record_id",),
        }
        referenced_records: set[str] = set()
        referenced_files: set[str] = set()
        for table, columns in character_record_columns.items():
            for column in columns:
                referenced_records.update(
                    str(row[0])
                    for row in conn.execute(
                        f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL"
                    )
                )
        character_file_columns = {
            "character_row_occurrence": ("source_file_id",),
            "character_text_candidate_occurrence": ("source_file_id",),
            "character_voice_container_occurrence": (
                "lut_source_file_id", "item_source_file_id",
            ),
        }
        for table, columns in character_file_columns.items():
            for column in columns:
                referenced_files.update(
                    str(row[0])
                    for row in conn.execute(
                        f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL"
                    )
                )
        if referenced_records - set(want_records):
            _fail("Phase 4C5B Character source_record provenance expansion")
        if referenced_files - set(source_file_ids.values()):
            _fail("Phase 4C5B Character source_file provenance expansion")


def _canonical_rows(rows: list[tuple[Any, ...]]) -> str:
    return canonical_json(sorted((list(row) for row in rows), key=canonical_json))


def _verify_exact_sql(
    conn: sqlite3.Connection,
    expected: Phase4C5BReleaseExpected,
    *,
    allowed_other_run_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    specs = {
        "character_row_occurrence": ("id,ingest_run_id,snapshot_id,source_file_id,source_record_id,source_pointer,table_name,row_index,native_locator_field,native_locator_value,avatar_id,raw_checksum,metadata_json", "rows"),
        "character_voice_container_occurrence": ("id,ingest_run_id,snapshot_id,voice_slot,lut_source_file_id,lut_source_record_id,lut_source_pointer,item_source_file_id,item_source_record_id,item_source_pointer,resolution_status,candidate_scopes_json,metadata_json", "voices"),
        "character_raw_slot_occurrence": ("id,ingest_run_id,snapshot_id,row_occurrence_id,source_record_id,source_pointer,field_name,array_index,slot_kind,observation_status,raw_json,metadata_json", "raw_slots"),
        "character_relation_occurrence": ("id,ingest_run_id,snapshot_id,source_row_occurrence_id,source_record_id,source_pointer,relation_kind,field_role,target_family,target_candidate_key_json,resolution_status,target_row_occurrence_id,target_voice_container_id,candidate_scopes_json,metadata_json", "relations"),
        "character_text_resolution_occurrence": ("id,ingest_run_id,snapshot_id,relationship_occurrence_id,source_row_occurrence_id,source_record_id,source_pointer,locale,textmap_hash,resolution_status,raw_text,normalized_text,raw_checksum,normalized_checksum,candidate_scopes_json,metadata_json", "text_resolutions"),
        "character_text_candidate_occurrence": ("id,resolution_occurrence_id,ingest_run_id,snapshot_id,source_file_id,source_record_id,source_pointer,shard_kind,locale,resolution_status,raw_text,normalized_text,raw_checksum,normalized_checksum,metadata_json", "text_candidates"),
        "character_audio_variant_occurrence": ("id,ingest_run_id,snapshot_id,voice_container_id,item_source_record_id,source_pointer,array_index,audio_path,audio_path_status,character_child_selector,selector_status,metadata_json", "audio_variants"),
    }
    allowed_runs = {expected.ingest_run_id, *allowed_other_run_ids}
    for table, (columns, key) in specs.items():
        present_runs = {
            str(row[0])
            for row in conn.execute(
                f"SELECT DISTINCT ingest_run_id FROM {table}"
            )
        }
        unexpected_runs = present_runs - allowed_runs
        if unexpected_runs:
            _fail(
                f"Phase 4C5B {table} run-scope pollution: "
                f"unexpected_runs={sorted(unexpected_runs)}"
            )
        actual = [
            tuple(row)
            for row in conn.execute(
                f"SELECT {columns} FROM {table} WHERE ingest_run_id=? ORDER BY id",
                (expected.ingest_run_id,),
            )
        ]
        want = list(expected.sql_rows[key])
        if len(actual) != len(want) or _canonical_rows(actual) != _canonical_rows(want):
            _fail(f"Phase 4C5B exact SQL tuple/digest mismatch: {table}")
    # The direct status and closure checks are intentionally separate from the
    # tuple comparison so an equal-count status substitution cannot pass.
    d = expected.derived
    if conn.execute("SELECT COUNT(*) FROM character_voice_container_occurrence WHERE ingest_run_id=? AND voice_slot='2008' AND id IN (SELECT voice_container_id FROM character_audio_variant_occurrence)", (expected.ingest_run_id,)).fetchone()[0] != 0:
        _fail("Phase 4C5B empty slot 2008 unexpectedly has children")
    attached = int(conn.execute("SELECT COUNT(DISTINCT target_voice_container_id) FROM character_relation_occurrence WHERE ingest_run_id=? AND relation_kind='voice_container_attachment' AND resolution_status='resolved_unique_container'", (expected.ingest_run_id,)).fetchone()[0])
    unattached_children = int(conn.execute("SELECT COUNT(*) FROM character_audio_variant_occurrence a JOIN character_voice_container_occurrence c ON c.id=a.voice_container_id WHERE a.ingest_run_id=? AND c.ingest_run_id=? AND NOT EXISTS (SELECT 1 FROM character_relation_occurrence r WHERE r.ingest_run_id=? AND r.target_voice_container_id=c.id)", (expected.ingest_run_id, expected.ingest_run_id, expected.ingest_run_id)).fetchone()[0])
    if attached != 367 or int(d["counts"]["voice_containers"]) - attached != 8 or unattached_children != 105:
        _fail("Phase 4C5B physical voice closure mismatch")
    return {"counts": d["counts"], "status_counts": d["status_counts"], "occurrence_set_digests": d["occurrence_set_digests"], "oracle": "independent_source_coordinate_sql_exact_v2"}


def _reject_logical_pollution(
    conn: sqlite3.Connection,
    *,
    allow_existing_product_documents: bool = False,
) -> None:
    names = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    forbidden = {name for name in names if name.casefold() in {"logical_character", "character_document", "character_passage", "character_logical_identity"}}
    if forbidden:
        _fail(f"Phase 4C5B forbidden logical tables present: {sorted(forbidden)}")
    # Existing product document/passage tables are allowed only when empty; the
    # full boundary is raw evidence and must not create derived documents.
    if not allow_existing_product_documents:
        for name in sorted(names):
            if name.casefold() in {"document", "documents", "passage", "passages"}:
                if conn.execute(f'SELECT 1 FROM "{name}" LIMIT 1').fetchone() is not None:
                    _fail(f"Phase 4C5B logical document/passage pollution: {name}")



def _reject_logical_rows_for_run(
    conn: sqlite3.Connection,
    run_id: str,
) -> None:
    """Ensure the raw C5B run never writes logical/static business rows."""

    business_tables = (
        "evidence_container",
        "identity_group",
        "passage_occurrence",
        "occurrence_edge",
        "document_evidence",
        "static_evidence_container",
        "static_segment_occurrence",
        "static_relationship_occurrence",
        "static_text_resolution_occurrence",
        "static_text_candidate_occurrence",
        "passage_occurrence_text",
    )
    for table in business_tables:
        columns = {
            str(row[1])
            for row in conn.execute(f'PRAGMA table_info("{table}")')
        }
        if "ingest_run_id" in columns and conn.execute(
            f'SELECT 1 FROM "{table}" WHERE ingest_run_id=? LIMIT 1',
            (run_id,),
        ).fetchone() is not None:
            _fail(f"Phase 4C5B run leaked into logical/static table: {table}")

def verify_character_phase4c5b_database_contract(
    conn: sqlite3.Connection,
    expected: Phase4C5BReleaseExpected,
    *,
    allowed_other_run_ids: tuple[str, ...] = (),
    allow_extra_source_files: bool = False,
    allow_extra_source_records: bool = False,
) -> dict[str, Any]:
    if _schema_versions(conn) != list(range(1, 11)):
        _fail("Phase 4C5B requires exact Schema 10 migration ledger 1..10")
    try:
        verify_character_phase4c2_schema_signature(conn)
    except Exception as exc:
        _fail(f"Phase 4C5B Schema 10 signature mismatch: {exc}")
    allowed = tuple(allowed_other_run_ids)
    if allowed not in (
        (),
        (C2_CONTRACT.ingest_run_id, C4B_CONTRACT.ingest_run_id),
    ):
        _fail(
            "C5B combined verification permits only the fixed Phase 4C2 and Phase 4C4B runs"
        )
    if allowed == () and (allow_extra_source_files or allow_extra_source_records):
        _fail("C5B source-provenance allowances require the fixed C2+C4B siblings")
    if allowed and not (allow_extra_source_files and allow_extra_source_records):
        _fail("C5B combined verification requires both explicit shared-source allowances")
    _verify_all_json_columns(conn)
    _reject_logical_pollution(
        conn,
        allow_existing_product_documents=bool(allowed),
    )
    _reject_logical_rows_for_run(conn, expected.ingest_run_id)
    _verify_provenance(conn, expected)
    _verify_source_files(
        conn, expected,
        allow_extra_source_files=allow_extra_source_files,
        allow_extra_source_records=allow_extra_source_records,
    )
    return _verify_exact_sql(
        conn, expected, allowed_other_run_ids=allowed_other_run_ids
    )


def _assert_report(report: dict[str, Any], expected: Phase4C5BReleaseExpected, fingerprint: dict[str, Any]) -> None:
    if report.get("status") != "complete":
        _fail("Phase 4C5B build report is not complete")
    if report.get("ingest_run_id") != expected.ingest_run_id or report.get("snapshot_id") != expected.snapshot_id or report.get("parser_build_id") != expected.parser_build_id or report.get("config_checksum") != expected.config_checksum:
        _fail("Phase 4C5B build report run binding mismatch")
    if report.get("coverage") != EXPECTED_COVERAGE or report.get("corpus_complete") is not True:
        _fail("Phase 4C5B build report coverage/corpus binding mismatch")
    manifest = report.get("manifest")
    if not isinstance(manifest, dict) or manifest.get("manifest_id") != expected.manifest.manifest_id or manifest.get("raw_sha256") != expected.manifest.raw_sha256:
        _fail("Phase 4C5B build report manifest binding mismatch")
    if report.get("counts") != expected.derived["counts"] or report.get("status_counts") != expected.derived["status_counts"] or report.get("table_row_counts") != expected.derived["table_row_counts"] or report.get("owner_field_counts") != expected.derived["text_owner_field_counts"] or report.get("occurrence_set_digests") != expected.derived["occurrence_set_digests"]:
        _fail("Phase 4C5B build report source contract mismatch")
    if report.get("database_fingerprint") != fingerprint:
        _fail("Phase 4C5B build report fingerprint does not match database")
    if report.get("sidecars_absent") is not True or str(report.get("journal_mode", "")).casefold() != "delete":
        _fail("Phase 4C5B build report sidecar/journal binding mismatch")


    if report.get("scope") != {
        "selected_paths": list(EXPECTED_PATHS), "full_character_scan": False,
        "full_voice_scan": False, "logical_identity_created": False,
        "mcp_capability_added": False,
    }:
        _fail("Phase 4C5B build report scope binding mismatch")

def verify_character_phase4c5b_evidence_db(db_path: str | Path, build_report_path: str | Path, manifest_path: str | Path, source_root: str | Path) -> dict[str, Any]:
    database = Path(db_path).resolve()
    _assert_sidecars_absent(database, "before fingerprint")
    before = _fingerprint(database)
    try:
        expected = build_phase4c5b_release_expected(source_root, manifest_path)
    except Phase4C5BReleaseContractError as exc:
        _fail(str(exc))
    _assert_sidecars_absent(database, "before open")
    try:
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA busy_timeout=0")
            mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).casefold()
            if mode != "delete":
                _fail(f"Phase 4C5B requires DELETE journal mode, got {mode}")
            result = verify_character_phase4c5b_database_contract(conn, expected)
    except sqlite3.Error as exc:
        _fail(str(exc))
    _assert_sidecars_absent(database, "after close")
    after = _fingerprint(database)
    if before != after:
        _fail("database changed during read-only verification")
    try:
        report = strict_json_load(build_report_path, label="Phase 4C5B build report")
    except Exception as exc:
        _fail(str(exc))
    if not isinstance(report, dict):
        _fail("Phase 4C5B build report must be an object")
    _assert_report(report, expected, before)
    return {**result, "database_fingerprint": after, "sidecars_absent": True, "journal_mode": "delete", "report": str(Path(build_report_path).resolve()), "release_contract": "fixed_15_file_source_oracle_v2", "oracle": "independent_source_coordinate_sql_exact_v2"}


__all__ = ["Phase4C5BVerificationError", "verify_character_phase4c5b_database_contract", "verify_character_phase4c5b_evidence_db", "_fingerprint", "_sidecars"]
