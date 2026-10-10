"""Builder-independent source and SQLite contract for the sealed Book1 run."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import validate_exact_schema_structure
from .source import snapshot_id
from .static_book1_manifest import (
    StaticBook1Manifest,
    load_static_book1_manifest,
    load_static_book1_source_manifest,
)
from .util import canonical_json, digest_id, normalize_text, sha256_bytes, sha256_text


RUN_FAMILY = "static_book1"
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
}


class StaticBook1ContractError(RuntimeError):
    pass


def _fail(message: str) -> None:
    raise StaticBook1ContractError(message)


def _json_index(value: Any, pointer: str) -> Any:
    current = value
    for raw_part in pointer.removeprefix("/").split("/") if pointer else ():
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            current = current[int(part)] if isinstance(current, list) else current[part]
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            _fail(f"source pointer does not exist: {pointer}: {exc}")
    return current


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        _fail(f"cannot verify Book1 source checkout: {exc}")
    return result.stdout.strip()


@dataclass(frozen=True)
class ExpectedBook1File:
    relative_path: str
    raw: bytes
    record: Any
    record_json: str
    source_file_id: str
    source_record_id: str


@dataclass(frozen=True)
class ExpectedBook1Segment:
    relative_path: str
    locale: str
    ordinal: int
    byte_start: int
    byte_end: int
    character_start: int
    character_end: int
    raw_text: str


@dataclass(frozen=True)
class Book1Expectation:
    source_manifest: dict[str, Any]
    manifest: StaticBook1Manifest
    source_root: Path
    snapshot_id: str
    config_checksum: str
    parser_build_id: str
    ingest_run_id: str
    files: tuple[ExpectedBook1File, ...]
    segments: tuple[ExpectedBook1Segment, ...]


def _segments(raw: bytes, relative_path: str, locale: str) -> list[ExpectedBook1Segment]:
    text = raw.decode("utf-8")
    result: list[ExpectedBook1Segment] = []
    start = 0
    ordinal = 0
    while start <= len(text):
        end = start
        while end < len(text) and text[end] not in "\r\n":
            end += 1
        line = text[start:end]
        if line.strip():
            result.append(
                ExpectedBook1Segment(
                    relative_path,
                    locale,
                    ordinal,
                    len(text[:start].encode("utf-8")),
                    len(text[:end].encode("utf-8")),
                    start,
                    end,
                    line,
                )
            )
        if end == len(text):
            break
        start = end + (2 if text[end:end + 2] == "\r\n" else 1)
        ordinal += 1
    return result


def build_book1_expectation(
    source_manifest_path: str | Path,
    book1_manifest_path: str | Path,
    source_root: str | Path,
) -> Book1Expectation:
    """Independently rederive all B1 source coordinates and deterministic IDs."""

    source = load_static_book1_source_manifest(source_manifest_path)
    manifest = load_static_book1_manifest(book1_manifest_path)
    for key in ("source_id", "release_label", "commit", "tree"):
        if str(source.get(key)) != manifest.source[key]:
            _fail(f"source and Book1 manifests disagree on {key}")
    root = Path(source_root).resolve()
    if Path(_git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        _fail("Book1 source root is not the Git toplevel")
    if _git(root, "rev-parse", "HEAD") != manifest.source["commit"]:
        _fail("Book1 source commit mismatch")
    if _git(root, "rev-parse", "HEAD^{tree}") != manifest.source["tree"]:
        _fail("Book1 source tree mismatch")
    dirty = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if dirty:
        _fail(f"Book1 source checkout is dirty: {dirty.splitlines()[0]}")

    snapshot = snapshot_id(source)
    files: list[ExpectedBook1File] = []
    segments: list[ExpectedBook1Segment] = []
    for spec in manifest.files:
        path = root.joinpath(*spec.relative_path.split("/"))
        try:
            raw = path.read_bytes()
        except OSError as exc:
            _fail(f"cannot read Book1 sealed source {spec.relative_path}: {exc}")
        if len(raw) != spec.size_bytes or sha256_bytes(raw) != spec.sha256:
            _fail(f"Book1 sealed source mismatch: {spec.relative_path}")
        if spec.format == "json":
            try:
                document = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                _fail(f"invalid Book1 JSON {spec.relative_path}: {exc}")
            record = _json_index(document, spec.record["pointer"])
            if not isinstance(record, dict) or str(record.get("id")) != spec.record["native_id"]:
                _fail(f"Book1 JSON record mismatch: {spec.relative_path}")
            record_json = canonical_json(record)
        elif spec.format == "txt":
            try:
                record = raw.decode("utf-8")
            except UnicodeError as exc:
                _fail(f"invalid Book1 UTF-8 {spec.relative_path}: {exc}")
            if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw or raw.endswith(b"\n"):
                _fail(f"Book1 readable newline/BOM mismatch: {spec.relative_path}")
            record_json = canonical_json({"text": record})
            segments.extend(_segments(raw, spec.relative_path, str(spec.record["locale"])))
        else:
            _fail(f"unsupported Book1 source format: {spec.format}")
        file_id = digest_id("sf", snapshot, spec.relative_path)
        files.append(
            ExpectedBook1File(
                spec.relative_path,
                raw,
                record,
                record_json,
                file_id,
                digest_id("sr", file_id, spec.record["pointer"]),
            )
        )
    by_locale: dict[str, list[int]] = {}
    for segment in segments:
        by_locale.setdefault(segment.locale, []).append(segment.ordinal)
    if len(segments) != 57 or by_locale != {
        locale: list(manifest.expected["non_empty_line_ordinals"])
        for locale in ("CHS", "EN", "JP")
    }:
        _fail("independent Book1 segment inventory mismatch")

    by_pointer = {
        spec.record["pointer"]: item.record
        for spec, item in zip(manifest.files, files, strict=True)
    }
    for relationship in manifest.relationships:
        record = by_pointer[relationship.source_record_pointer]
        suffix = relationship.source_pointer[len(relationship.source_record_pointer):]
        raw_value = _json_index(record, suffix)
        candidate = relationship.target_candidate_key
        if relationship.source_pointer == "/6/questIDList/0":
            expected_value: Any = candidate["native_id"]
            if str(raw_value) != expected_value:
                _fail("Book1 questIDList raw value/candidate mismatch")
        elif relationship.source_pointer == "/6/titleTextMapHash":
            expected_value = candidate["textmap_hash"]
            if str(raw_value) != expected_value:
                _fail("Book1 titleTextMapHash raw value/candidate mismatch")
            expected_locator_pointer = f"/{expected_value}"
            if any(
                locator["pointer"] != expected_locator_pointer
                for locator in relationship.candidate_locators
            ):
                _fail("Book1 titleTextMapHash locator pointer mismatch")
        else:
            relative_path = candidate["relative_path"]
            expected_value = "ART/UI/" + relative_path.removesuffix(".txt")
            if raw_value != expected_value:
                _fail(
                    "Book1 localization raw asset path/candidate mismatch: "
                    f"{relationship.source_pointer}"
                )
    config = {
        "family": RUN_FAMILY,
        "snapshot": {"commit": source["commit"], "tree": source["tree"]},
        "manifest_raw_sha256": manifest.raw_sha256,
        "versions": manifest.versions,
        "selected_paths": [item.relative_path for item in files],
        "relationship_pointers": [item.source_pointer for item in manifest.relationships],
    }
    checksum = sha256_text(canonical_json(config))
    parser_build_id = digest_id(
        "pb-static-book1", snapshot, manifest.raw_sha256,
        manifest.versions["parser"], manifest.versions["projection"],
        manifest.versions["segmentation"], checksum,
    )
    ingest_run_id = digest_id(
        "ir-static-book1", snapshot, manifest.raw_sha256, parser_build_id, checksum
    )
    return Book1Expectation(
        source, manifest, root, snapshot, checksum, parser_build_id, ingest_run_id,
        tuple(files), tuple(segments),
    )


def _verify_schema(conn: sqlite3.Connection, schema_version: int) -> None:
    if schema_version not in (8, 10):
        _fail(f"Book1 verifier does not support Schema {schema_version}")
    versions = [int(row[0]) for row in conn.execute(
        "SELECT version FROM schema_migration ORDER BY version"
    )]
    if versions != list(range(1, schema_version + 1)):
        _fail(
            f"Book1 schema ledger must be exactly 1..{schema_version}: {versions}"
        )
    ledger = tuple(tuple(row) for row in conn.execute("PRAGMA table_info(schema_migration)"))
    if ledger != (
        (0, "version", "INTEGER", 0, None, 1),
        (1, "applied_at", "TEXT", 1, None, 0),
    ):
        _fail("Book1 schema ledger shape mismatch")
    try:
        validate_exact_schema_structure(conn, schema_version)
    except (RuntimeError, ValueError, sqlite3.Error) as exc:
        _fail(f"Book1 Schema {schema_version} structural contract mismatch: {exc}")
    triggers = {str(row[0]) for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger'"
    )}
    if not REQUIRED_TRIGGERS.issubset(triggers):
        _fail(f"Book1 immutable trigger set is incomplete: {sorted(REQUIRED_TRIGGERS - triggers)}")


def _record_raw_checksum(value: Any) -> str:
    return sha256_text(value if isinstance(value, str) else canonical_json(value))


def _verify_logical_boundary(
    conn: sqlite3.Connection,
    expected: Book1Expectation,
) -> dict[str, int]:
    # A B1-only fixture has no documents.  The production AQ -> B1 artifact
    # preserves the fifteen sealed Quest documents and versions from AQ.  B1
    # must accept that baseline without admitting Talk/logical-passage writes.
    document_rows = {
        tuple(row)
        for row in conn.execute(
            "SELECT stable_id,content_type,native_namespace,native_id,status "
            "FROM document"
        )
    }
    expected_documents = {
        (f"agd:quest:{quest_id}", "quest", "quest", str(quest_id), "active")
        for quest_id in range(7000, 7015)
    }
    if document_rows not in (set(), expected_documents):
        _fail("Book1 database document baseline is neither empty nor exact AQ Quest 7000..7014")

    version_rows = list(conn.execute(
        "SELECT dv.document_id,dv.snapshot_id,dv.raw_checksum,"
        "dv.normalized_checksum,dv.metadata_json,dv.status,"
        "sr.pointer,sr.record_kind,sr.native_id,sr.raw_sha256 "
        "FROM document_version dv "
        "JOIN source_record sr ON sr.id=dv.source_record_id "
        "ORDER BY dv.document_id"
    ))
    if not document_rows:
        if version_rows:
            _fail("Book1-only database contains orphan document versions")
    else:
        if len(version_rows) != 15:
            _fail("AQ -> Book1 database must preserve exactly 15 Quest document versions")
        seen_documents: set[str] = set()
        for row in version_rows:
            document_id = str(row[0])
            native_id = document_id.removeprefix("agd:quest:")
            if (
                document_id not in {item[0] for item in expected_documents}
                or document_id in seen_documents
                or row[1] != expected.snapshot_id
                or row[2] != row[9]
                or not isinstance(row[3], str)
                or len(row[3]) != 64
                or row[4] != canonical_json({
                    "scope": f"selected Quest {native_id} root",
                    "mapping_version": "quest-corpus-evidence-v1",
                })
                or row[5] != "active"
                or row[6] != "/"
                or row[7] != "quest_root"
                or str(row[8]) != native_id
            ):
                _fail(f"AQ Quest document_version baseline mismatch: {document_id}")
            seen_documents.add(document_id)

    zero_tables = (
        "logical_talk",
        "talk_evidence",
        "logical_talk_evidence",
        "passage",
        "passage_version",
        "passage_text",
        "localized_text",
        "document_relation",
        "passage_edge",
        "fts_evidence",
    )
    logical_counts = {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in zero_tables
    }
    logical_counts["talk_document"] = int(conn.execute(
        "SELECT COUNT(*) FROM document WHERE content_type='talk'"
    ).fetchone()[0])
    if any(logical_counts.values()):
        _fail(f"Book1 database contains logical promotion leakage: {logical_counts}")

    cross_family_tables = (
        "evidence_container",
        "identity_group",
        "identity_group_container",
        "passage_occurrence",
        "passage_occurrence_text",
        "occurrence_edge",
        "candidate_scope_proof",
        "document_evidence",
    )
    b1_cross_family_rows = sum(
        int(conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE ingest_run_id=?",
            (expected.ingest_run_id,),
        ).fetchone()[0])
        for table in cross_family_tables
    )
    if b1_cross_family_rows:
        _fail("Book1 run leaked into AQ/logical evidence tables")
    return {
        **logical_counts,
        "quest_documents": len(document_rows),
        "quest_document_versions": len(version_rows),
        "book1_cross_family_rows": b1_cross_family_rows,
    }


def verify_book1_database_contract(
    conn: sqlite3.Connection,
    expected: Book1Expectation,
    *,
    stage_exclusive: bool = True,
) -> dict[str, Any]:
    schema_version = 8 if stage_exclusive else 10
    _verify_schema(conn, schema_version)
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        _fail("Book1 database foreign_key_check is not empty")
    source = expected.source_manifest
    snapshot = conn.execute(
        'SELECT source_id,release_label,build,"commit",tree,observed_at,status '
        "FROM source_snapshot WHERE id=?",
        (expected.snapshot_id,),
    ).fetchone()
    if snapshot is None or tuple(snapshot) != (
        source["source_id"], source["release_label"], source["build"], source["commit"],
        source["tree"], source["observed_at"], "active",
    ):
        _fail("Book1 source_snapshot binding mismatch")
    catalog = conn.execute(
        "SELECT kind,url,license_note FROM source_catalog WHERE id=?",
        (source["source_id"],),
    ).fetchone()
    if catalog is None or tuple(catalog) != ("primary", source["upstream"], None):
        _fail("Book1 source_catalog binding mismatch")
    parser = conn.execute(
        "SELECT parser_version,mapping_version,fingerprint,config_checksum,created_at "
        "FROM parser_build WHERE id=?", (expected.parser_build_id,),
    ).fetchone()
    if parser is None or tuple(parser) != (
        expected.manifest.versions["parser"], expected.manifest.versions["projection"],
        "static-book1-sealed-evidence", expected.config_checksum, source["observed_at"],
    ):
        _fail("Book1 parser_build binding mismatch")
    scope = canonical_json({
        "family": RUN_FAMILY,
        "manifest_id": expected.manifest.manifest_id,
        "selected_paths": [item.relative_path for item in expected.files],
    })
    config = canonical_json({
        "manifest_raw_sha256": expected.manifest.raw_sha256,
        "versions": expected.manifest.versions,
        "full_static_scan": False,
    })
    run = conn.execute(
        "SELECT snapshot_id,parser_build_id,scope_json,config_json,status,created_at "
        "FROM ingest_run WHERE id=?", (expected.ingest_run_id,),
    ).fetchone()
    if run is None or tuple(run) != (
        expected.snapshot_id, expected.parser_build_id, scope, config, "complete", source["observed_at"]
    ):
        _fail("Book1 ingest_run binding mismatch")
    phase_runs = [str(row[0]) for row in conn.execute(
        "SELECT id FROM ingest_run WHERE json_extract(scope_json,'$.family')=? ORDER BY id",
        (RUN_FAMILY,),
    )]
    if phase_runs != [expected.ingest_run_id]:
        _fail(f"unexpected Book1 run set: {phase_runs}")
    parser_builds = [str(row[0]) for row in conn.execute(
        "SELECT id FROM parser_build WHERE fingerprint='static-book1-sealed-evidence' ORDER BY id"
    )]
    if parser_builds != [expected.parser_build_id]:
        _fail(f"unexpected Book1 parser build set: {parser_builds}")
    snapshots = [str(row[0]) for row in conn.execute("SELECT id FROM source_snapshot ORDER BY id")]
    if stage_exclusive and snapshots != [expected.snapshot_id]:
        _fail(f"unexpected source snapshot set for Book1 database: {snapshots}")
    if not stage_exclusive and expected.snapshot_id not in snapshots:
        _fail(f"Book1 source snapshot is absent from final database: {snapshots}")

    specs = {item.relative_path: item for item in expected.manifest.files}
    files = {item.relative_path: item for item in expected.files}
    container_ids: dict[str, str] = {}
    for relative_path, item in files.items():
        spec = specs[relative_path]
        row = conn.execute(
            "SELECT snapshot_id,relative_path,upstream_blob_oid,sha256,size_bytes,format,root_path "
            "FROM source_file WHERE id=?", (item.source_file_id,),
        ).fetchone()
        if row is None or (
            row[0], row[1], row[2], row[3], row[4], row[5],
            os.path.normcase(str(Path(str(row[6])).resolve(strict=False))),
        ) != (
            expected.snapshot_id, relative_path, None, spec.sha256, spec.size_bytes,
            spec.format, os.path.normcase(str(expected.source_root)),
        ):
            _fail(f"Book1 source_file mismatch: {relative_path}")
        record = conn.execute(
            "SELECT source_file_id,pointer,record_kind,native_id,raw_sha256,raw_json "
            "FROM source_record WHERE id=?", (item.source_record_id,),
        ).fetchone()
        if record is None or tuple(record) != (
            item.source_file_id, spec.record["pointer"], spec.record["record_kind"],
            spec.record["native_id"], _record_raw_checksum(item.record), item.record_json,
        ):
            _fail(f"Book1 source_record mismatch: {relative_path}")
        container_id = digest_id(
            "static-container", expected.snapshot_id, expected.manifest.raw_sha256,
            expected.ingest_run_id, relative_path, spec.record["pointer"],
        )
        container_ids[relative_path] = container_id
        candidate = (
            {"locale": spec.record["locale"], "relative_path": relative_path}
            if spec.record["container_kind"] == "readable_file"
            else {"native_id": spec.record["native_id"]}
        )
        container = conn.execute(
            "SELECT ingest_run_id,snapshot_id,source_file_id,source_record_id,container_kind,"
            "source_family,locale,candidate_namespace,candidate_key_json,metadata_json "
            "FROM static_evidence_container WHERE id=?", (container_id,),
        ).fetchone()
        if container is None or tuple(container) != (
            expected.ingest_run_id, expected.snapshot_id, item.source_file_id,
            item.source_record_id, spec.record["container_kind"], spec.record["source_family"],
            spec.record["locale"], spec.record["source_family"], canonical_json(candidate),
            canonical_json({
                "manifest_id": expected.manifest.manifest_id,
                "manifest_raw_sha256": expected.manifest.raw_sha256,
                "relative_path": relative_path,
                "source_pointer": spec.record["pointer"],
            }),
        ):
            _fail(f"Book1 container mismatch: {relative_path}")

    expected_record_ids = {item.source_record_id for item in expected.files}
    placeholders = ",".join("?" for _ in expected.files)
    actual_record_ids = {
        str(row[0])
        for row in conn.execute(
            f"SELECT id FROM source_record WHERE source_file_id IN ({placeholders})",
            tuple(item.source_file_id for item in expected.files),
        )
    }
    if stage_exclusive and actual_record_ids != expected_record_ids:
        _fail("Book1 selected source files contain extra or missing source records")
    if not stage_exclusive and not expected_record_ids.issubset(actual_record_ids):
        _fail("Book1 selected source files are missing expected source records")

    expected_segment_ids: set[str] = set()
    for segment in expected.segments:
        spec = specs[segment.relative_path]
        item = files[segment.relative_path]
        segment_id = digest_id(
            "static-segment", expected.snapshot_id, expected.manifest.raw_sha256,
            expected.ingest_run_id, segment.relative_path, spec.record["pointer"],
            segment.ordinal, segment.byte_start, segment.byte_end,
            segment.character_start, segment.character_end,
        )
        expected_segment_ids.add(segment_id)
        row = conn.execute("SELECT * FROM static_segment_occurrence WHERE id=?", (segment_id,)).fetchone()
        expected_values = (
            segment_id, expected.ingest_run_id, expected.snapshot_id,
            container_ids[segment.relative_path], item.source_record_id, "readable_file",
            segment.locale, f"/text/line/{segment.ordinal}", segment.ordinal,
            segment.byte_start, segment.byte_end, segment.character_start, segment.character_end,
            segment.raw_text, normalize_text(segment.raw_text), sha256_text(segment.raw_text),
            sha256_text(normalize_text(segment.raw_text)), expected.manifest.versions["segmentation"],
            canonical_json({
                "blank_line_ordinals_preserved_as_gaps": [11],
                "identity_scope": "snapshot_source_coordinate_occurrence",
                "cross_language_alignment": "unresolved",
            }),
        )
        if row is None or tuple(row) != expected_values:
            _fail(f"Book1 segment mismatch: {segment.relative_path}#{segment.ordinal}")

    by_record_pointer = {
        spec.record["pointer"]: (spec, files[spec.relative_path])
        for spec in expected.manifest.files
    }
    expected_relationship_ids: set[str] = set()
    for relationship in expected.manifest.relationships:
        source_spec, source_item = by_record_pointer[relationship.source_record_pointer]
        suffix = relationship.source_pointer[len(relationship.source_record_pointer):]
        raw_value = _json_index(source_item.record, suffix)
        relationship_id = digest_id(
            "static-relationship", expected.snapshot_id, expected.manifest.raw_sha256,
            expected.ingest_run_id, source_spec.relative_path, relationship.source_pointer,
        )
        expected_relationship_ids.add(relationship_id)
        if relationship.target_record_pointer == "/8":
            target_id = container_ids[by_record_pointer["/8"][0].relative_path]
        elif relationship.target_record_pointer and relationship.target_record_pointer.startswith("/text:"):
            target_id = container_ids[relationship.target_record_pointer.split(":", 1)[1]]
        else:
            target_id = None
        row = conn.execute("SELECT * FROM static_relationship_occurrence WHERE id=?", (relationship_id,)).fetchone()
        expected_values = (
            relationship_id, expected.ingest_run_id, expected.snapshot_id,
            container_ids[source_spec.relative_path], source_item.source_record_id,
            source_spec.record["source_family"], relationship.source_pointer,
            relationship.relation_kind, relationship.field_role, relationship.target_family,
            canonical_json(relationship.target_candidate_key), "resolved", target_id, "{}",
            canonical_json({
                "raw_value": raw_value,
                "candidate_locators": list(relationship.candidate_locators),
                "manifest_id": expected.manifest.manifest_id,
                "candidate_resolution_detail": "exact_sealed_source_coordinate",
            }),
        )
        if row is None or tuple(row) != expected_values:
            _fail(f"Book1 relationship mismatch: {relationship.source_pointer}")

    # Schema 8 is the AQ -> Book1 release ceiling: no later static stage is
    # present yet.  Freeze the complete table ownership sets, not merely the
    # rows carrying the expected run id, so a second run cannot smuggle
    # otherwise well-formed static evidence into a recast release artifact.
    ownership_filter = "" if stage_exclusive else " WHERE ingest_run_id=?"
    ownership_parameters: tuple[Any, ...] = (
        () if stage_exclusive else (expected.ingest_run_id,)
    )
    actual_containers = {
        (str(row[0]), str(row[1]))
        for row in conn.execute(
            "SELECT id,ingest_run_id FROM static_evidence_container" + ownership_filter,
            ownership_parameters,
        )
    }
    actual_segments = {
        (str(row[0]), str(row[1]))
        for row in conn.execute(
            "SELECT id,ingest_run_id FROM static_segment_occurrence" + ownership_filter,
            ownership_parameters,
        )
    }
    actual_relationships = {
        (str(row[0]), str(row[1]))
        for row in conn.execute(
            "SELECT id,ingest_run_id FROM static_relationship_occurrence" + ownership_filter,
            ownership_parameters,
        )
    }
    expected_containers = {
        (container_id, expected.ingest_run_id)
        for container_id in container_ids.values()
    }
    expected_segments = {
        (segment_id, expected.ingest_run_id)
        for segment_id in expected_segment_ids
    }
    expected_relationships = {
        (relationship_id, expected.ingest_run_id)
        for relationship_id in expected_relationship_ids
    }
    if (
        actual_containers != expected_containers
        or actual_segments != expected_segments
        or actual_relationships != expected_relationships
    ):
        _fail("Book1 database contains extra or missing static occurrence rows")
    logical_counts = _verify_logical_boundary(conn, expected)
    exact_counts = {"containers": 5, "segments": 57, "relationships": 6}
    return {
        "protocol": "wave1-stage-final-state-contract-v1",
        "contract_id": (
            "wave1-stage-final-state-v1:book1:" + expected.manifest.manifest_id
        ),
        "status": "pass",
        "run_id": expected.ingest_run_id,
        "parser_build_id": expected.parser_build_id,
        "manifest_sha256": expected.manifest.raw_sha256,
        "exact_counts": exact_counts,
        "required_checks": [
            "schema", "source_binding", "run_binding", "exact_counts",
            "full_rows", "logical_boundary", "foreign_keys",
        ],
        "schema_version": schema_version,
        "identity": {
            "snapshot_id": expected.snapshot_id,
            "parser_build_id": expected.parser_build_id,
            "ingest_run_id": expected.ingest_run_id,
            "config_checksum": expected.config_checksum,
        },
        "counts": exact_counts,
        "locale_counts": {"CHS": 19, "EN": 19, "JP": 19},
        "logical_leak_counts": logical_counts,
        "foreign_key_check": [],
    }


__all__ = [
    "Book1Expectation",
    "StaticBook1ContractError",
    "build_book1_expectation",
    "verify_book1_database_contract",
]
