from __future__ import annotations

import json
import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import Database
from .errors import ManifestError
from .phase4a_audit import segment_readable_occurrence, verify_source_snapshot
from .source import LocatedFile, SourceBundle, snapshot_id
from .static_book1_manifest import (
    StaticBook1FileSpec,
    StaticBook1Manifest,
    StaticBook1RelationshipSpec,
    load_static_book1_manifest,
    load_static_book1_source_manifest,
)
from .util import canonical_json, digest_id, normalize_text, sha256_bytes, sha256_text


_QUEST_RELATIONSHIP_POINTER = "/6/questIDList/0"
_TITLE_TEXTMAP_RELATIONSHIP_POINTER = "/6/titleTextMapHash"
_READABLE_RELATIONSHIP_TARGETS = {
    "/8/DEFNEHAFMMA": (
        "ART/UI/Readable/CHS/Book1",
        "CHS",
        "Readable/CHS/Book1.txt",
    ),
    "/8/JDNBKKPEFAI": (
        "ART/UI/Readable/CHS/Book1",
        "CHS",
        "Readable/CHS/Book1.txt",
    ),
    "/8/enPath": (
        "ART/UI/Readable/EN/Book1_EN",
        "EN",
        "Readable/EN/Book1_EN.txt",
    ),
    "/8/jpPath": (
        "ART/UI/Readable/JP/Book1_JP",
        "JP",
        "Readable/JP/Book1_JP.txt",
    ),
}
_TEXTMAP_LOCATOR_PATHS = {
    "CHS": "TextMap/TextMap_MediumCHS.json",
    "EN": "TextMap/TextMap_MediumEN.json",
    "JP": "TextMap/TextMap_MediumJP.json",
}


@dataclass(frozen=True)
class PreflightStaticBook1File:
    spec: StaticBook1FileSpec
    located: LocatedFile
    raw: bytes
    record_value: Any
    record_json: str

    @property
    def relative_path(self) -> str:
        return self.spec.relative_path


@dataclass(frozen=True)
class PreflightStaticBook1Segment:
    relative_path: str
    record_pointer: str
    locale: str
    source_pointer: str
    file_local_ordinal: int
    byte_start: int
    byte_end: int
    character_start: int
    character_end: int
    raw_text: str
    normalized_text: str
    raw_checksum: str
    normalized_checksum: str
    segmentation_version: str


@dataclass(frozen=True)
class PreflightStaticBook1Relationship:
    spec: StaticBook1RelationshipSpec
    source_relative_path: str
    raw_value: Any
    target_relative_path: str | None

    @property
    def source_pointer(self) -> str:
        return self.spec.source_pointer


@dataclass(frozen=True)
class PreflightStaticBook1Result:
    source_manifest: dict[str, Any]
    manifest: StaticBook1Manifest
    snapshot_id: str
    parser_build_id: str
    ingest_run_id: str
    config_checksum: str
    files: tuple[PreflightStaticBook1File, ...]
    segments: tuple[PreflightStaticBook1Segment, ...]
    relationships: tuple[PreflightStaticBook1Relationship, ...]


def _json_index(value: Any, pointer: str) -> Any:
    current = value
    if pointer == "":
        return current
    if not pointer.startswith("/"):
        raise ManifestError(f"invalid JSON pointer: {pointer}")
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            if isinstance(current, list):
                current = current[int(part)]
            elif isinstance(current, dict):
                current = current[part]
            else:
                raise KeyError(part)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise ManifestError(f"source pointer does not exist: {pointer}") from exc
    return current


def _verify_source_roots(
    source_manifest_path: str | Path,
    files: list[PreflightStaticBook1File],
) -> None:
    roots = sorted({item.located.root for item in files}, key=lambda item: str(item))
    for root in roots:
        try:
            verify_source_snapshot(Path(source_manifest_path), root)
        except Exception as exc:
            raise ManifestError(f"source checkout pin/clean verification failed for {root.name}: {exc}") from exc


def _parse_file(
    spec: StaticBook1FileSpec,
    located: LocatedFile,
    raw: bytes,
) -> PreflightStaticBook1File:
    if len(raw) != spec.size_bytes:
        raise ManifestError(
            f"Book1 source size mismatch for {spec.relative_path}: "
            f"actual={len(raw)} expected={spec.size_bytes}"
        )
    actual_sha = sha256_bytes(raw)
    if actual_sha != spec.sha256:
        raise ManifestError(
            f"Book1 source SHA-256 mismatch for {spec.relative_path}: "
            f"actual={actual_sha} expected={spec.sha256}"
        )
    if spec.format == "json":
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManifestError(f"invalid Book1 JSON file {spec.relative_path}: {exc}") from exc
        record = _json_index(document, spec.record["pointer"])
        if not isinstance(record, dict):
            raise ManifestError(f"Book1 source record is not an object: {spec.relative_path}#{spec.record['pointer']}")
        if str(record.get("id")) != spec.record["native_id"]:
            raise ManifestError(
                f"Book1 native ID mismatch at {spec.relative_path}#{spec.record['pointer']}"
            )
        record_json = canonical_json(record)
    elif spec.format == "txt":
        try:
            record = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ManifestError(f"invalid UTF-8 Book1 readable {spec.relative_path}: {exc}") from exc
        if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw or raw.endswith(b"\n"):
            raise ManifestError(f"Book1 readable raw newline/BOM shape mismatch: {spec.relative_path}")
        record_json = canonical_json({"text": record})
    else:
        raise ManifestError(f"unsupported Book1 source format: {spec.format}")
    return PreflightStaticBook1File(spec, located, raw, record, record_json)


def _segments_for(
    files: list[PreflightStaticBook1File],
    source_manifest_path: str | Path,
    segmentation_version: str,
) -> list[PreflightStaticBook1Segment]:
    source_root = next(item.located.root for item in files)
    try:
        snapshot = verify_source_snapshot(Path(source_manifest_path), source_root)
    except Exception as exc:
        raise ManifestError(f"cannot construct pinned Book1 segment provenance: {exc}") from exc
    result: list[PreflightStaticBook1Segment] = []
    for item in files:
        locale = item.spec.record["locale"]
        if locale is None:
            continue
        occurrences = segment_readable_occurrence(
            raw=item.raw,
            relative_path=item.relative_path,
            locale=locale,
            snapshot=snapshot,
        )
        if any(segment.segmentation_algorithm_version != segmentation_version for segment in occurrences):
            raise ManifestError("Book1 segmentation version does not match the sealed parser")
        text = item.raw.decode("utf-8")
        for occurrence in occurrences:
            raw_text = text[occurrence.character_start : occurrence.character_end]
            normalized_text = normalize_text(raw_text)
            result.append(
                PreflightStaticBook1Segment(
                    relative_path=item.relative_path,
                    record_pointer=item.spec.record["pointer"],
                    locale=locale,
                    source_pointer=f"/text/line/{occurrence.file_local_ordinal}",
                    file_local_ordinal=occurrence.file_local_ordinal,
                    byte_start=occurrence.byte_start,
                    byte_end=occurrence.byte_end,
                    character_start=occurrence.character_start,
                    character_end=occurrence.character_end,
                    raw_text=raw_text,
                    normalized_text=normalized_text,
                    raw_checksum=sha256_text(raw_text),
                    normalized_checksum=sha256_text(normalized_text),
                    segmentation_version=segmentation_version,
                )
            )
    return result


def _relationship_value(
    spec: StaticBook1RelationshipSpec,
    files_by_record_pointer: dict[str, PreflightStaticBook1File],
) -> tuple[PreflightStaticBook1File, Any]:
    source = files_by_record_pointer.get(spec.source_record_pointer)
    if source is None or not isinstance(source.record_value, dict):
        raise ManifestError(f"relationship source record missing: {spec.source_record_pointer}")
    suffix = spec.source_pointer[len(spec.source_record_pointer) :]
    return source, _json_index(source.record_value, suffix)


def _verify_relationships(
    manifest: StaticBook1Manifest,
    files: list[PreflightStaticBook1File],
) -> list[PreflightStaticBook1Relationship]:
    files_by_record_pointer = {
        item.spec.record["pointer"]: item
        for item in files
        if item.spec.record["container_kind"] == "excel_row"
    }
    files_by_relative_path = {item.relative_path: item for item in files}
    result: list[PreflightStaticBook1Relationship] = []
    for spec in manifest.relationships:
        source, raw_value = _relationship_value(spec, files_by_record_pointer)
        target_relative: str | None
        if spec.source_pointer == _QUEST_RELATIONSHIP_POINTER:
            if raw_value != 200007:
                raise ManifestError("Book1 quest relationship raw native ID must be 200007")
            if spec.target_family != "Localization":
                raise ManifestError("Book1 quest relationship target family must be Localization")
            expected_candidate = {"native_id": str(raw_value)}
            if spec.target_candidate_key != expected_candidate:
                raise ManifestError(
                    "Book1 quest relationship candidate native_id disagrees with raw evidence"
                )
            target = files_by_record_pointer.get("/8")
            if (
                target is None
                or target.spec.record["source_family"] != "Localization"
                or target.spec.record["native_id"] != str(raw_value)
                or spec.target_record_pointer != target.spec.record["pointer"]
            ):
                raise ManifestError(
                    "Book1 quest relationship does not identify the sealed Localization target"
                )
            target_relative = target.relative_path
        elif spec.source_pointer in _READABLE_RELATIONSHIP_TARGETS:
            expected_asset, expected_locale, derived_relative = (
                _READABLE_RELATIONSHIP_TARGETS[spec.source_pointer]
            )
            if raw_value != expected_asset:
                raise ManifestError(
                    f"Book1 readable relationship raw asset path mismatch: {spec.source_pointer}"
                )
            if spec.target_family != "Readable":
                raise ManifestError("Book1 readable relationship target family must be Readable")
            expected_candidate = {
                "locale": expected_locale,
                "relative_path": derived_relative,
            }
            if spec.target_candidate_key != expected_candidate:
                raise ManifestError(
                    f"Book1 readable candidate disagrees with raw asset path: {spec.source_pointer}"
                )
            if spec.target_record_pointer != f"/text:{derived_relative}":
                raise ManifestError(
                    f"Book1 readable target record pointer mismatch: {spec.source_pointer}"
                )
            target = files_by_relative_path.get(derived_relative)
            if (
                target is None
                or target.spec.record["source_family"] != "Readable"
                or target.spec.record["locale"] != expected_locale
                or target.spec.record["pointer"] != "/text"
            ):
                raise ManifestError(
                    f"Book1 readable relationship target is not the selected sealed file: {spec.source_pointer}"
                )
            target_relative = target.relative_path
        elif spec.source_pointer == _TITLE_TEXTMAP_RELATIONSHIP_POINTER:
            if raw_value != 74547319:
                raise ManifestError("Book1 titleTextMapHash raw value must be 74547319")
            expected_hash = str(raw_value)
            if spec.target_candidate_key != {"textmap_hash": expected_hash}:
                raise ManifestError(
                    "Book1 titleTextMapHash candidate disagrees with raw evidence"
                )
            expected_locators = tuple(
                {
                    "locale": locale,
                    "relative_path": relative_path,
                    "pointer": f"/{expected_hash}",
                }
                for locale, relative_path in _TEXTMAP_LOCATOR_PATHS.items()
            )
            if spec.candidate_locators != expected_locators:
                raise ManifestError(
                    "Book1 titleTextMapHash locators disagree with raw hash or locale"
                )
            if spec.target_family != "TextMap" or spec.target_record_pointer is not None:
                raise ManifestError("Book1 titleTextMapHash must not bind a concrete target")
            target_relative = None
        else:
            raise ManifestError(
                f"unsupported Book1 relationship source pointer: {spec.source_pointer}"
            )
        result.append(PreflightStaticBook1Relationship(spec, source.relative_path, raw_value, target_relative))
    return result


def preflight_static_book1_evidence_bundle(
    source_roots: list[str | Path],
    source_manifest_path: str | Path,
    book1_manifest_path: str | Path,
) -> PreflightStaticBook1Result:
    """Seal all source input and invariants before any target path is touched."""
    source_manifest = load_static_book1_source_manifest(source_manifest_path)
    manifest = load_static_book1_manifest(book1_manifest_path)
    for key in ("source_id", "release_label", "commit", "tree"):
        if str(source_manifest.get(key)) != manifest.source[key]:
            raise ManifestError(f"source manifest and Book1 manifest disagree on {key}")
    bundle = SourceBundle(source_roots)
    verified_files: list[PreflightStaticBook1File] = []
    for spec in manifest.files:
        try:
            located, raw = bundle.read_bytes(spec.relative_path)
        except Exception as exc:
            raise ManifestError(f"cannot read sealed Book1 source {spec.relative_path}: {exc}") from exc
        verified_files.append(_parse_file(spec, located, raw))
    _verify_source_roots(source_manifest_path, verified_files)

    segments = _segments_for(verified_files, source_manifest_path, manifest.versions["segmentation"])
    by_locale: dict[str, int] = {}
    ordinals: dict[str, list[int]] = {}
    for segment in segments:
        by_locale[segment.locale] = by_locale.get(segment.locale, 0) + 1
        ordinals.setdefault(segment.locale, []).append(segment.file_local_ordinal)
    if len(segments) != manifest.expected["segment_count"] or by_locale != manifest.expected["segment_count_by_locale"]:
        raise ManifestError(f"Book1 segment cardinality mismatch: total={len(segments)} locales={by_locale}")
    for locale in ("CHS", "EN", "JP"):
        if ordinals.get(locale) != manifest.expected["non_empty_line_ordinals"]:
            raise ManifestError(f"Book1 segment ordinal mismatch for {locale}")
    relationships = _verify_relationships(manifest, verified_files)
    if len(relationships) != manifest.expected["relationship_count"]:
        raise ManifestError("Book1 relationship cardinality mismatch")

    snapshot_id_value = snapshot_id(source_manifest)
    config = {
        "family": "static_book1",
        "snapshot": {
            "commit": source_manifest["commit"],
            "tree": source_manifest["tree"],
        },
        "manifest_raw_sha256": manifest.raw_sha256,
        "versions": manifest.versions,
        "selected_paths": [item.relative_path for item in verified_files],
        "relationship_pointers": [item.source_pointer for item in relationships],
    }
    config_checksum = sha256_text(canonical_json(config))
    parser_build_id = digest_id(
        "pb-static-book1",
        snapshot_id_value,
        manifest.raw_sha256,
        manifest.versions["parser"],
        manifest.versions["projection"],
        manifest.versions["segmentation"],
        config_checksum,
    )
    ingest_run_id = digest_id(
        "ir-static-book1",
        snapshot_id_value,
        manifest.raw_sha256,
        parser_build_id,
        config_checksum,
    )
    return PreflightStaticBook1Result(
        source_manifest=dict(source_manifest),
        manifest=manifest,
        snapshot_id=snapshot_id_value,
        parser_build_id=parser_build_id,
        ingest_run_id=ingest_run_id,
        config_checksum=config_checksum,
        files=tuple(verified_files),
        segments=tuple(segments),
        relationships=tuple(relationships),
    )


def _ensure_static_row(
    db: Database,
    table: str,
    columns: tuple[str, ...],
    values: tuple[Any, ...],
) -> None:
    row_id = values[0]
    existing = db.conn.execute(
        f"SELECT {','.join(columns)} FROM {table} WHERE id=?", (row_id,)
    ).fetchone()
    if existing is not None:
        actual = tuple(existing[column] for column in columns)
        if actual != values:
            raise sqlite3.IntegrityError(f"{table} immutable identity mismatch for {row_id}")
        return
    placeholders = ",".join("?" for _ in columns)
    db.conn.execute(
        f"INSERT INTO {table}({','.join(columns)}) VALUES ({placeholders})", values
    )


def _insert_relationship_occurrence(db: Database, values: tuple[Any, ...]) -> None:
    columns = (
        "id", "ingest_run_id", "snapshot_id", "source_container_id",
        "source_record_id", "source_family", "source_pointer", "relation_kind",
        "field_role", "target_family", "target_candidate_key_json",
        "candidate_resolution_status", "target_evidence_container_id",
        "ambiguity_json", "metadata_json",
    )
    _ensure_static_row(db, "static_relationship_occurrence", columns, values)


def _run_counts(db: Database, ingest_run_id: str) -> dict[str, int]:
    return {
        "containers": int(db.conn.execute(
            "SELECT COUNT(*) FROM static_evidence_container WHERE ingest_run_id=?",
            (ingest_run_id,),
        ).fetchone()[0]),
        "segments": int(db.conn.execute(
            "SELECT COUNT(*) FROM static_segment_occurrence WHERE ingest_run_id=?",
            (ingest_run_id,),
        ).fetchone()[0]),
        "relationships": int(db.conn.execute(
            "SELECT COUNT(*) FROM static_relationship_occurrence WHERE ingest_run_id=? AND id LIKE 'static-relationship:%'",
            (ingest_run_id,),
        ).fetchone()[0]),
    }


def _closed_delete_storage_contract(target: Path) -> dict[str, Any]:
    sidecars = [
        Path(f"{target}{suffix}")
        for suffix in ("-journal", "-wal", "-shm")
        if Path(f"{target}{suffix}").exists()
    ]
    if sidecars:
        raise sqlite3.IntegrityError(
            f"Book1 database has SQLite sidecars after close: {sidecars}"
        )
    uri = target.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    finally:
        conn.close()
    if mode != "delete":
        raise sqlite3.IntegrityError(
            f"Book1 database journal_mode must persist as DELETE, got {mode!r}"
        )
    if any(Path(f"{target}{suffix}").exists() for suffix in ("-journal", "-wal", "-shm")):
        raise sqlite3.IntegrityError("Book1 readonly journal check created a sidecar")
    return {"journal_mode": "delete", "sidecars_absent": True}


def _build_report(
    target: Path,
    preflight: PreflightStaticBook1Result,
) -> dict[str, Any]:
    sqlite_storage = _closed_delete_storage_contract(target)
    stat = target.stat()
    return {
        "status": "complete",
        "db_path": str(target.resolve()),
        "snapshot_id": preflight.snapshot_id,
        "parser_build_id": preflight.parser_build_id,
        "ingest_run_id": preflight.ingest_run_id,
        "config_checksum": preflight.config_checksum,
        "database_fingerprint": {
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        },
        "sqlite_storage": sqlite_storage,
        "manifest": {
            "manifest_id": preflight.manifest.manifest_id,
            "raw_sha256": preflight.manifest.raw_sha256,
        },
        "counts": {"containers": 5, "segments": 57, "relationships": 6},
        "scope": {
            "selected_paths": [item.relative_path for item in preflight.files],
            "full_static_scan": False,
            "fts_rebuilt": False,
            "embedding": False,
            "semantic_search": False,
            "logical_identity_created": False,
            "cross_language_alignment_created": False,
        },
    }


def ingest_static_book1_evidence_bundle(
    db_path: str | Path,
    source_roots: list[str | Path],
    source_manifest_path: str | Path,
    book1_manifest_path: str | Path,
) -> dict[str, Any]:
    """Ingest the sealed Book1 slice; preflight precedes every target side effect."""
    preflight = preflight_static_book1_evidence_bundle(
        source_roots, source_manifest_path, book1_manifest_path
    )
    target = Path(db_path)
    with Database(target) as db:
        journal_mode = str(
            db.conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        ).lower()
        if journal_mode != "delete":
            raise sqlite3.IntegrityError(
                f"Book1 builder requires DELETE journal mode, got {journal_mode!r}"
            )
        db.initialize_through(8)
        existing_run = db.conn.execute(
            "SELECT status FROM ingest_run WHERE id=?",
            (preflight.ingest_run_id,),
        ).fetchone()
        expected_counts = {"containers": 5, "segments": 57, "relationships": 6}
        if (
            existing_run is not None
            and existing_run["status"] == "complete"
            and _run_counts(db, preflight.ingest_run_id) == expected_counts
            and not db.conn.execute("PRAGMA foreign_key_check").fetchall()
        ):
            db.close()
            return _build_report(target, preflight)
        with db.transaction():
            source = preflight.source_manifest
            db.ensure_source(
                source["source_id"],
                "primary",
                source.get("upstream", "https://github.com/DimbreathBot/AnimeGameData"),
            )
            existing_snapshot = db.conn.execute(
                'SELECT source_id,release_label,build,"commit",tree FROM source_snapshot WHERE id=?',
                (preflight.snapshot_id,),
            ).fetchone()
            if existing_snapshot is None:
                db.ensure_snapshot(
                    {
                        "id": preflight.snapshot_id,
                        "source_id": source["source_id"],
                        "release_label": source["release_label"],
                        "build": source["build"],
                        "commit": source["commit"],
                        "tree": source["tree"],
                        "observed_at": source.get("observed_at", "2026-08-19T00:00:00+08:00"),
                        "parser_version": preflight.manifest.versions["parser"],
                        "mapping_version": preflight.manifest.versions["projection"],
                        "status": "active",
                        "metadata": {"sealed_manifest_id": preflight.manifest.manifest_id},
                    }
                )
            else:
                expected_snapshot = (
                    source["source_id"], source["release_label"], source["build"],
                    source["commit"], source["tree"],
                )
                if tuple(existing_snapshot) != expected_snapshot:
                    raise sqlite3.IntegrityError("existing snapshot does not match sealed Book1 provenance")
            deterministic_time = source.get("observed_at", "2026-08-19T00:00:00+08:00")
            db.ensure_parser_build(
                preflight.parser_build_id,
                parser_version=preflight.manifest.versions["parser"],
                mapping_version=preflight.manifest.versions["projection"],
                fingerprint="static-book1-sealed-evidence",
                config_checksum=preflight.config_checksum,
                created_at=deterministic_time,
            )
            scope = {
                "family": "static_book1",
                "manifest_id": preflight.manifest.manifest_id,
                "selected_paths": [item.relative_path for item in preflight.files],
            }
            config = {
                "manifest_raw_sha256": preflight.manifest.raw_sha256,
                "versions": preflight.manifest.versions,
                "full_static_scan": False,
            }
            db.ensure_ingest_run(
                preflight.ingest_run_id,
                snapshot_id=preflight.snapshot_id,
                parser_build_id=preflight.parser_build_id,
                scope=scope,
                config=config,
                status="running",
                created_at=deterministic_time,
            )

            file_ids: dict[str, str] = {}
            record_ids: dict[str, str] = {}
            container_ids: dict[str, str] = {}
            for item in preflight.files:
                file_id = db.ensure_source_file(
                    preflight.snapshot_id,
                    {
                        "relative_path": item.relative_path,
                        "sha256": item.spec.sha256,
                        "size_bytes": item.spec.size_bytes,
                        "format": item.spec.format,
                        "root_path": str(item.located.root),
                    },
                )
                file_ids[item.relative_path] = file_id
                record_id = db.ensure_source_record(
                    file_id,
                    item.spec.record["pointer"],
                    item.spec.record["record_kind"],
                    item.spec.record["native_id"],
                    item.record_value,
                    raw_json=item.record_json,
                )
                record_ids[item.relative_path] = record_id
                container_id = digest_id(
                    "static-container",
                    preflight.snapshot_id,
                    preflight.manifest.raw_sha256,
                    preflight.ingest_run_id,
                    item.relative_path,
                    item.spec.record["pointer"],
                )
                container_ids[item.relative_path] = container_id
                if item.spec.record["source_family"] == "Readable":
                    candidate_key = {
                        "locale": item.spec.record["locale"],
                        "relative_path": item.relative_path,
                    }
                else:
                    candidate_key = {"native_id": item.spec.record["native_id"]}
                container_columns = (
                    "id", "ingest_run_id", "snapshot_id", "source_file_id",
                    "source_record_id", "container_kind", "source_family", "locale",
                    "candidate_namespace", "candidate_key_json", "metadata_json",
                )
                container_values = (
                    container_id, preflight.ingest_run_id, preflight.snapshot_id,
                    file_id, record_id, item.spec.record["container_kind"],
                    item.spec.record["source_family"], item.spec.record["locale"],
                    item.spec.record["source_family"], canonical_json(candidate_key),
                    canonical_json({
                        "manifest_id": preflight.manifest.manifest_id,
                        "manifest_raw_sha256": preflight.manifest.raw_sha256,
                        "relative_path": item.relative_path,
                        "source_pointer": item.spec.record["pointer"],
                    }),
                )
                _ensure_static_row(db, "static_evidence_container", container_columns, container_values)

            for segment in preflight.segments:
                segment_id = digest_id(
                    "static-segment", preflight.snapshot_id,
                    preflight.manifest.raw_sha256, preflight.ingest_run_id,
                    segment.relative_path, segment.record_pointer,
                    segment.file_local_ordinal, segment.byte_start, segment.byte_end,
                    segment.character_start, segment.character_end,
                )
                segment_columns = (
                    "id", "ingest_run_id", "snapshot_id", "evidence_container_id",
                    "source_record_id", "container_kind", "locale", "source_pointer",
                    "file_local_ordinal", "byte_start", "byte_end", "character_start",
                    "character_end", "raw_text", "normalized_text", "raw_checksum",
                    "normalized_checksum", "segmentation_version", "metadata_json",
                )
                segment_values = (
                    segment_id, preflight.ingest_run_id, preflight.snapshot_id,
                    container_ids[segment.relative_path], record_ids[segment.relative_path],
                    "readable_file", segment.locale, segment.source_pointer,
                    segment.file_local_ordinal, segment.byte_start, segment.byte_end,
                    segment.character_start, segment.character_end, segment.raw_text,
                    segment.normalized_text, segment.raw_checksum,
                    segment.normalized_checksum, segment.segmentation_version,
                    canonical_json({
                        "blank_line_ordinals_preserved_as_gaps": [11],
                        "identity_scope": "snapshot_source_coordinate_occurrence",
                        "cross_language_alignment": "unresolved",
                    }),
                )
                _ensure_static_row(db, "static_segment_occurrence", segment_columns, segment_values)

            for relationship in preflight.relationships:
                spec = relationship.spec
                relationship_id = digest_id(
                    "static-relationship", preflight.snapshot_id,
                    preflight.manifest.raw_sha256, preflight.ingest_run_id,
                    relationship.source_relative_path, spec.source_pointer,
                )
                target_container_id = (
                    container_ids.get(relationship.target_relative_path)
                    if relationship.target_relative_path is not None
                    else None
                )
                values = (
                    relationship_id, preflight.ingest_run_id, preflight.snapshot_id,
                    container_ids[relationship.source_relative_path],
                    record_ids[relationship.source_relative_path],
                    next(
                        item.spec.record["source_family"]
                        for item in preflight.files
                        if item.relative_path == relationship.source_relative_path
                    ),
                    spec.source_pointer, spec.relation_kind, spec.field_role,
                    spec.target_family, canonical_json(spec.target_candidate_key),
                    spec.candidate_resolution_status, target_container_id, "{}",
                    canonical_json({
                        "raw_value": relationship.raw_value,
                        "candidate_locators": list(spec.candidate_locators),
                        "manifest_id": preflight.manifest.manifest_id,
                        "candidate_resolution_detail": "exact_sealed_source_coordinate",
                    }),
                )
                _insert_relationship_occurrence(db, values)

            counts = _run_counts(db, preflight.ingest_run_id)
            if counts != expected_counts:
                raise sqlite3.IntegrityError(f"Book1 write cardinality mismatch: {counts}")
            foreign_keys = db.conn.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_keys:
                raise sqlite3.IntegrityError(f"Book1 foreign key check failed: {foreign_keys}")
            db.ensure_ingest_run(
                preflight.ingest_run_id,
                snapshot_id=preflight.snapshot_id,
                parser_build_id=preflight.parser_build_id,
                scope=scope,
                config=config,
                status="complete",
                created_at=deterministic_time,
            )

    return _build_report(target, preflight)
