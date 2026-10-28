"""Narrow Batch M3 R0 physical-occurrence scanner.

This module is deliberately independent from the historical M3 coverage
report.  It scans the five M0 source roots and stores only physical evidence:
file bytes and one row for every top-level array element or object pair.  It
does not infer a family, join files, or de-duplicate equal values.
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import stat
import sqlite3
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from . import batch_space
from .batch_release import (
    BatchReleaseError,
    assert_output_paths,
    create_private_staging,
    publish_absent,
)
from .batch_m0_baseline import (
    PairsObject,
    SOURCE_ROOTS,
    SOURCE_SNAPSHOT_ID,
    _record_layout,
    coordinate_text,
    parse_json_physical,
    physical_value_sha256,
)


SCHEMA_VERSION = "batch-m3-r0-physical-occurrence-v1"
PARSER_BUILD_ID = "pb-batch-m3-r0-physical:v1"
INGEST_RUN_ID = "ir-batch-m3-r0-physical:v1"
SNAPSHOT_ID = SOURCE_SNAPSHOT_ID
COORDINATE_CONTRACT = "pair_ordinal_path_v1"
ARTIFACT_LIMIT_BYTES = 4 * 1024**3
PROJECT_SPACE_RESERVATION_BYTES = ARTIFACT_LIMIT_BYTES
OWNER = "batch-m3-r0-physical"
SOURCE_FILE_TABLE = "source_file"
PHYSICAL_OCCURRENCE_TABLE = "physical_occurrence"
METADATA_TABLE = "metadata"


class BatchM3PhysicalError(RuntimeError):
    """An R0 artifact cannot be safely built or verified."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _path_sort_key(path: str) -> bytes:
    return path.encode("utf-8", "surrogateescape")


def _selected_roots(selected_roots: Sequence[str] | None) -> tuple[str, ...]:
    requested = tuple(SOURCE_ROOTS if selected_roots is None else selected_roots)
    if not requested:
        raise BatchM3PhysicalError("at least one fixed source root is required")
    if any(not isinstance(root, str) for root in requested):
        raise BatchM3PhysicalError("source roots must be strings")
    unknown = sorted(set(requested) - set(SOURCE_ROOTS))
    if unknown:
        raise BatchM3PhysicalError(f"source roots outside the fixed M0 boundary: {unknown}")
    if len(set(requested)) != len(requested):
        raise BatchM3PhysicalError("source roots must be unique")
    # Root order is part of the artifact's deterministic identity.  Accept a
    # caller's permutation, but canonicalize it after validating the exact
    # requested set so unknown/duplicate values cannot be silently dropped.
    return tuple(root for root in SOURCE_ROOTS if root in requested)


def _iter_source_files(source_root: Path, roots: Sequence[str]) -> Iterator[tuple[str, Path]]:
    """Yield regular source files in stable bytewise relative-path order."""

    paths: list[tuple[str, Path]] = []
    for root in roots:
        root_path = source_root / root
        if not root_path.is_dir() or root_path.is_symlink():
            raise BatchM3PhysicalError(f"fixed source root is unavailable: {root}")
        for current, directories, files in os.walk(root_path, topdown=True, followlinks=False):
            current_path = Path(current)
            for name in directories:
                directory = current_path / name
                if directory.is_symlink():
                    raise BatchM3PhysicalError(f"source tree contains a symlink directory: {directory}")
            directories.sort(key=_path_sort_key)
            for name in files:
                path = current_path / name
                try:
                    info = path.lstat()
                except OSError as exc:
                    raise BatchM3PhysicalError(f"cannot inspect source file: {path}") from exc
                if path.is_symlink() or not path.is_file():
                    raise BatchM3PhysicalError(f"source tree contains a non-regular file: {path}")
                paths.append((path.relative_to(source_root).as_posix(), path))
    for relative, path in sorted(paths, key=lambda item: _path_sort_key(item[0])):
        yield relative, path

def _iter_top_level_occurrences(value: Any) -> Iterator[tuple[int, str, Any]]:
    if isinstance(value, list):
        for ordinal, item in enumerate(value):
            yield ordinal, coordinate_text((("array", ordinal, None),)), item
    elif isinstance(value, PairsObject):
        # PairsObject intentionally preserves duplicate keys and member order.
        for ordinal, (key, item) in enumerate(value.pairs):
            yield ordinal, coordinate_text((("pair", ordinal, key),)), item


def _skip_json_ws(text: str, position: int) -> int:
    while position < len(text) and text[position] in " \t\r\n":
        position += 1
    return position


def _iter_top_level_lexical_occurrences(
    raw: bytes,
) -> Iterator[tuple[int, str, str]]:
    """Yield top-level coordinates with exact lexical token hashes.

    ``parse_json_physical`` preserves duplicate members and numeric spellings,
    but JSON string escapes and source whitespace are otherwise normalised by
    the parsed representation.  This companion iterator locates each top-level
    token in the original UTF-8 text and hashes that exact slice.  Only the
    fixed-size hash is stored; the source file SHA/path/coordinate remains the
    authoritative locator from which the lexical bytes are re-fetched.
    """

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BatchM3PhysicalError("JSON source is not valid UTF-8") from exc
    decoder = json.JSONDecoder()
    position = _skip_json_ws(text, 0)
    if position >= len(text):
        raise BatchM3PhysicalError("JSON source is empty")
    opener = text[position]
    if opener == "[":
        position += 1
        ordinal = 0
        while True:
            position = _skip_json_ws(text, position)
            if position < len(text) and text[position] == "]":
                position = _skip_json_ws(text, position + 1)
                if position != len(text):
                    raise BatchM3PhysicalError("JSON source has trailing bytes")
                return
            start = position
            try:
                _value, end = decoder.raw_decode(text, position)
            except json.JSONDecodeError as exc:
                raise BatchM3PhysicalError("JSON array token boundary is invalid") from exc
            yield ordinal, coordinate_text((("array", ordinal, None),)), _sha256_bytes(text[start:end].encode("utf-8"))
            ordinal += 1
            position = _skip_json_ws(text, end)
            if position >= len(text):
                raise BatchM3PhysicalError("JSON array is unterminated")
            if text[position] == ",":
                position += 1
                continue
            if text[position] == "]":
                position = _skip_json_ws(text, position + 1)
                if position != len(text):
                    raise BatchM3PhysicalError("JSON source has trailing bytes")
                return
            raise BatchM3PhysicalError("JSON array delimiter is invalid")
    elif opener == "{":
        position += 1
        ordinal = 0
        while True:
            position = _skip_json_ws(text, position)
            if position < len(text) and text[position] == "}":
                position = _skip_json_ws(text, position + 1)
                if position != len(text):
                    raise BatchM3PhysicalError("JSON source has trailing bytes")
                return
            key_start = position
            try:
                key, key_end = decoder.raw_decode(text, position)
            except json.JSONDecodeError as exc:
                raise BatchM3PhysicalError("JSON object key boundary is invalid") from exc
            if not isinstance(key, str):
                raise BatchM3PhysicalError("JSON object key is not a string")
            position = _skip_json_ws(text, key_end)
            if position >= len(text) or text[position] != ":":
                raise BatchM3PhysicalError("JSON object pair has no colon")
            position = _skip_json_ws(text, position + 1)
            try:
                _value, value_end = decoder.raw_decode(text, position)
            except json.JSONDecodeError as exc:
                raise BatchM3PhysicalError("JSON object value boundary is invalid") from exc
            yield ordinal, coordinate_text((("pair", ordinal, key),)), _sha256_bytes(text[key_start:value_end].encode("utf-8"))
            ordinal += 1
            position = _skip_json_ws(text, value_end)
            if position >= len(text):
                raise BatchM3PhysicalError("JSON object is unterminated")
            if text[position] == ",":
                position += 1
                continue
            if text[position] == "}":
                position = _skip_json_ws(text, position + 1)
                if position != len(text):
                    raise BatchM3PhysicalError("JSON source has trailing bytes")
                return
            raise BatchM3PhysicalError("JSON object delimiter is invalid")
    else:
        return


def _scan_file(relative_path: str, path: Path) -> tuple[dict[str, Any], Iterator[dict[str, Any]]]:
    try:
        raw = path.read_bytes()
    except (OSError, UnicodeError) as exc:
        raise BatchM3PhysicalError(f"cannot read source file: {relative_path}") from exc
    source_sha256 = _sha256_bytes(raw)
    root = relative_path.split("/", 1)[0]
    parse_status = "not_parsed_non_json"
    layout = "non_json"
    record_status = "unsupported_record_layout"
    parsed: Any = None
    expected_count = 0
    if path.suffix.lower() == ".json":
        # This fixed JSON decoder constructs an acyclic value tree. Defer
        # repeated cyclic scans while allocating it; refcounting still runs.
        # Restore the caller's GC state before consuming any occurrences.
        gc_enabled = gc.isenabled()
        if gc_enabled:
            gc.disable()
        try:
            parsed = parse_json_physical(raw)
        except (UnicodeError, ValueError, RecursionError):
            parse_status = "unparsed_container"
            layout = "empty_json_container" if not raw else "json_parse_failed"
            record_status = "unparsed_container"
        else:
            parse_status = "parsed_json"
            layout, expected_count, record_status = _record_layout(root, parsed)
        finally:
            if gc_enabled:
                gc.enable()

    # Keep the parsed container alive only for this file and yield one
    # occurrence at a time; never build a second list for a large TextMap.
    def occurrences() -> Iterator[dict[str, Any]]:
        # Unparsed/unsupported containers remain file-level evidence only;
        # never run the lexical iterator against malformed JSON.
        if parsed is None:
            return
        parsed_iterator = iter(_iter_top_level_occurrences(parsed))
        lexical_iterator = iter(_iter_top_level_lexical_occurrences(raw))
        while True:
            try:
                ordinal, pointer, _item = next(parsed_iterator)
            except StopIteration:
                try:
                    next(lexical_iterator)
                except StopIteration:
                    return
                raise BatchM3PhysicalError(
                    f"lexical occurrence count exceeds parsed count: {relative_path}"
                )
            try:
                lexical_ordinal, lexical_pointer, lexical_sha256 = next(lexical_iterator)
            except StopIteration as exc:
                raise BatchM3PhysicalError(
                    f"lexical occurrence count is shorter than parsed count: {relative_path}"
                ) from exc
            if (ordinal, pointer) != (lexical_ordinal, lexical_pointer):
                raise BatchM3PhysicalError(
                    f"lexical occurrence coordinate mismatch: {relative_path}"
                )
            yield {
                "ordinal": ordinal,
                "pointer": pointer,
                # The occurrence hash is deliberately lexical-token based.
                # The source file/coordinate is the retrievable raw locator.
                "value_sha256": lexical_sha256,
            }

    file_row = {
        "relative_path": relative_path,
        "root": root,
        "source_sha256": source_sha256,
        "size_bytes": len(raw),
        "parse_status": parse_status,
        "layout": layout,
        "record_status": record_status,
        "occurrence_count": expected_count,
    }
    return file_row, occurrences()


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA page_size=65536;
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        PRAGMA locking_mode=EXCLUSIVE;
        PRAGMA temp_store=MEMORY;
        PRAGMA user_version=1;
        CREATE TABLE source_file (
            source_file_id INTEGER PRIMARY KEY,
            relative_path TEXT NOT NULL UNIQUE,
            root TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
            snapshot_id TEXT NOT NULL,
            parser_build_id TEXT NOT NULL,
            ingest_run_id TEXT NOT NULL,
            parse_status TEXT NOT NULL,
            layout TEXT NOT NULL,
            record_status TEXT NOT NULL,
            occurrence_count INTEGER NOT NULL CHECK (occurrence_count >= 0)
        );
        CREATE TABLE physical_occurrence (
            occurrence_id INTEGER PRIMARY KEY,
            source_file_id INTEGER NOT NULL,
            snapshot_id TEXT NOT NULL,
            parser_build_id TEXT NOT NULL,
            ingest_run_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
            pointer TEXT NOT NULL,
            value_sha256 TEXT NOT NULL,
            FOREIGN KEY (source_file_id) REFERENCES source_file(source_file_id)
        );
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY NOT NULL,
            value TEXT NOT NULL
        );
        """
    )


def _put_metadata(connection: sqlite3.Connection, values: Mapping[str, Any]) -> None:
    connection.executemany(
        "INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)",
        ((str(key), _canonical_json(value)) for key, value in sorted(values.items())),
    )


def _artifact_root(temp_root: str | Path | None, invocation_id: str) -> Path:
    if temp_root is None:
        candidate = Path(tempfile.mkdtemp(prefix="batch-m3-r0-", dir=batch_space.temporary_root()))
    else:
        candidate = Path(temp_root).absolute()
    try:
        return batch_space.ensure_owned_tmp_root(candidate, owner=OWNER, invocation_id=invocation_id)
    except batch_space.SpaceGateError as exc:
        raise BatchM3PhysicalError(str(exc)) from exc


def _space_gate(
    repo_root: Path, owned_root: Path, invocation_id: str, *, reservation_bytes: int
) -> batch_space.ProjectSpaceGate:
    gate = batch_space.ProjectSpaceGate(
        repo_root,
        owned_tmp_roots=(owned_root,),
        accounting_tmp_roots=(owned_root,),
        cleanup_tmp_roots=(owned_root,),
        owner=OWNER,
        invocation_id=invocation_id,
        discovery_root="/tmp",
    )
    try:
        gate.check("m3-r0-physical-reservation", projected_bytes=reservation_bytes, cleanup=False)
    except batch_space.SpaceGateError as exc:
        raise BatchM3PhysicalError(str(exc)) from exc
    return gate


def _write_artifact(
    staging: Path,
    source_root: Path,
    roots: Sequence[str],
    *,
    snapshot_id: str,
    parser_build_id: str,
    ingest_run_id: str,
    artifact_limit_bytes: int,
) -> dict[str, Any]:
    if staging.is_symlink() or (staging.exists() and not staging.is_file()):
        raise BatchM3PhysicalError(f"staging artifact must be a regular file: {staging}")
    if staging.exists() and staging.stat().st_size != 0:
        raise BatchM3PhysicalError(f"staging artifact must be empty: {staging}")
    staging.parent.mkdir(parents=True, exist_ok=True)
    connection: sqlite3.Connection | None = None
    files = 0
    file_bytes = 0
    record_denominator = 0
    occurrence_id = 0
    status_counts: Counter[str] = Counter()
    parse_counts: Counter[str] = Counter()
    layout_counts: Counter[str] = Counter()
    root_counts: dict[str, Counter[str]] = defaultdict(Counter)
    try:
        connection = sqlite3.connect(staging)
        connection.execute(f"PRAGMA max_page_count={artifact_limit_bytes // 65536}")
        _create_schema(connection)
        _put_metadata(connection, {
            "schema_version": SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "parser_build_id": parser_build_id,
            "ingest_run_id": ingest_run_id,
            "coordinate_contract": COORDINATE_CONTRACT,
            "occurrence_hash_contract": "sha256(lexical_top_level_token_v1)",
            "source_roots": list(roots),
            "evidence_only": True,
            "corpus_complete": False,
            "product_accepted": False,
        })
        connection.commit()
        staging_identity_path = staging.absolute()
        for relative_path, path in _iter_source_files(source_root, roots):
            # A caller may place the target inside one selected source root;
            # the private same-directory staging inode is not source evidence.
            if path.absolute() == staging_identity_path:
                continue
            file_row, occurrence_rows = _scan_file(relative_path, path)
            files += 1
            file_id = files
            file_bytes += int(file_row["size_bytes"])
            record_denominator += int(file_row["occurrence_count"])
            status_counts[file_row["record_status"]] += 1
            parse_counts[file_row["parse_status"]] += 1
            layout_counts[file_row["layout"]] += 1
            root_counts[file_row["root"]]["files"] += 1
            root_counts[file_row["root"]]["bytes"] += int(file_row["size_bytes"])
            root_counts[file_row["root"]]["records"] += int(file_row["occurrence_count"])
            connection.execute(
                "INSERT INTO source_file VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    file_id, file_row["relative_path"], file_row["root"], file_row["source_sha256"],
                    file_row["size_bytes"], snapshot_id, parser_build_id, ingest_run_id,
                    file_row["parse_status"], file_row["layout"], file_row["record_status"],
                    file_row["occurrence_count"],
                ),
            )
            occurrence_rows_inserted = 0
            occurrence_batch: list[tuple[Any, ...]] = []
            for item in occurrence_rows:
                occurrence_id += 1
                occurrence_rows_inserted += 1
                occurrence_batch.append(
                    (occurrence_id, file_id, snapshot_id, parser_build_id, ingest_run_id,
                     item["ordinal"], item["pointer"], item["value_sha256"])
                )
                if len(occurrence_batch) >= 4096:
                    connection.executemany(
                        "INSERT INTO physical_occurrence VALUES (?,?,?,?,?,?,?,?)", occurrence_batch
                    )
                    occurrence_batch.clear()
            if occurrence_batch:
                connection.executemany(
                    "INSERT INTO physical_occurrence VALUES (?,?,?,?,?,?,?,?)", occurrence_batch
                )
            if occurrence_rows_inserted != int(file_row["occurrence_count"]):
                raise BatchM3PhysicalError("R0 occurrence generator count mismatch")
            if files % 256 == 0:
                connection.commit()
                if staging.stat().st_size > artifact_limit_bytes:
                    raise BatchM3PhysicalError("R0 artifact exceeds the explicit candidate limit")
        connection.commit()
        connection.execute("CREATE INDEX idx_source_file_relative_path ON source_file(relative_path)")
        connection.execute("CREATE INDEX idx_physical_occurrence_file_ordinal ON physical_occurrence(source_file_id,ordinal)")
        denominator = record_denominator
        dynamic = {
            "file_denominator": files,
            "file_bytes": file_bytes,
            "record_denominator": denominator,
            "record_numerator": occurrence_id,
            "coverage_percent": round(100.0 * occurrence_id / denominator, 6) if denominator else 100.0,
            "unsupported_counts": dict(sorted(status_counts.items())),
            "record_status_counts": dict(sorted(status_counts.items())),
            "parse_status_counts": dict(sorted(parse_counts.items())),
            "layout_counts": dict(sorted(layout_counts.items())),
            "root_counts": {root: dict(sorted(counts.items())) for root, counts in sorted(root_counts.items())},
        }
        _put_metadata(connection, dynamic)
        connection.commit()
    except sqlite3.Error as exc:
        raise BatchM3PhysicalError(f"R0 SQLite build failed: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()
    if not staging.is_file() or staging.stat().st_size > artifact_limit_bytes:
        raise BatchM3PhysicalError("R0 artifact is missing or exceeds the explicit candidate limit")
    os.chmod(staging, 0o600)
    return dynamic


def _metadata(connection: sqlite3.Connection) -> dict[str, Any]:
    try:
        rows = connection.execute("SELECT key,value FROM metadata ORDER BY key").fetchall()
        return {str(key): json.loads(value) for key, value in rows}
    except (sqlite3.Error, TypeError, json.JSONDecodeError) as exc:
        raise BatchM3PhysicalError(f"invalid R0 metadata: {exc}") from exc


def summarize_r0_artifact(artifact_path: str | Path) -> dict[str, Any]:
    path = Path(artifact_path).absolute()
    if not path.is_file() or path.is_symlink():
        raise BatchM3PhysicalError(f"R0 artifact is unavailable: {path}")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        metadata = _metadata(connection)
        file_count, file_bytes = connection.execute(
            "SELECT COUNT(*),COALESCE(SUM(size_bytes),0) FROM source_file"
        ).fetchone()
        record_count = connection.execute("SELECT COUNT(*) FROM physical_occurrence").fetchone()[0]
        status_counts = dict(connection.execute(
            "SELECT record_status,COUNT(*) FROM source_file GROUP BY record_status ORDER BY record_status"
        ).fetchall())
        denominator = int(metadata.get("record_denominator", record_count))
        numerator = int(metadata.get("record_numerator", record_count))
        return {
            "status": "verified",
            "path": str(path),
            "artifact_size_bytes": path.stat().st_size,
            "artifact_sha256": _sha256_file(path),
            "snapshot_id": metadata.get("snapshot_id"),
            "parser_build_id": metadata.get("parser_build_id"),
            "ingest_run_id": metadata.get("ingest_run_id"),
            "file_denominator": int(file_count),
            "file_bytes": int(file_bytes),
            "record_denominator": denominator,
            "record_numerator": numerator,
            "coverage_percent": round(100.0 * numerator / denominator, 6) if denominator else 100.0,
            "unsupported_counts": status_counts,
            "metadata": metadata,
        }
    except sqlite3.Error as exc:
        raise BatchM3PhysicalError(f"R0 artifact query failed: {exc}") from exc
    finally:
        connection.close()


def verify_r0_artifact(
    artifact_path: str | Path,
    *,
    source_root: str | Path | None = None,
    selected_roots: Sequence[str] | None = None,
    snapshot_id: str = SNAPSHOT_ID,
    parser_build_id: str = PARSER_BUILD_ID,
    ingest_run_id: str = INGEST_RUN_ID,
    artifact_limit_bytes: int = ARTIFACT_LIMIT_BYTES,
) -> dict[str, Any]:
    """Verify metadata and occurrence closure, optionally against source bytes.

    Queries are streamed by source file and occurrence rather than fetched as a
    giant result set; this is important for a real TextMap object container.
    """

    path = Path(artifact_path).absolute()
    summary = summarize_r0_artifact(path)
    if path.stat().st_size > artifact_limit_bytes:
        raise BatchM3PhysicalError("R0 artifact exceeds the explicit candidate limit")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        table_names = {
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if table_names != {SOURCE_FILE_TABLE, PHYSICAL_OCCURRENCE_TABLE, METADATA_TABLE}:
            raise BatchM3PhysicalError("R0 table closure mismatch")
        metadata = _metadata(connection)
        expected_metadata = {
            "schema_version": SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "parser_build_id": parser_build_id,
            "ingest_run_id": ingest_run_id,
            "coordinate_contract": COORDINATE_CONTRACT,
            "occurrence_hash_contract": "sha256(lexical_top_level_token_v1)",
            "evidence_only": True,
            "corpus_complete": False,
            "product_accepted": False,
        }
        for key, expected in expected_metadata.items():
            if metadata.get(key) != expected:
                raise BatchM3PhysicalError(f"R0 metadata mismatch for {key}")
        roots = _selected_roots(selected_roots if selected_roots is not None else metadata.get("source_roots"))
        if tuple(metadata.get("source_roots", ())) != roots:
            raise BatchM3PhysicalError("R0 source-root identity mismatch")
        file_cursor = connection.execute(
            "SELECT source_file_id,relative_path,root,source_sha256,size_bytes,snapshot_id,parser_build_id,ingest_run_id,parse_status,layout,record_status,occurrence_count FROM source_file ORDER BY source_file_id"
        )
        occurrence_cursor = connection.execute(
            "SELECT occurrence_id,source_file_id,snapshot_id,parser_build_id,ingest_run_id,ordinal,pointer,value_sha256 FROM physical_occurrence ORDER BY occurrence_id"
        )
        next_occurrence = occurrence_cursor.fetchone()
        previous_path: str | None = None
        file_count = 0
        file_bytes = 0
        record_denominator = 0
        record_numerator = 0
        status_counts: Counter[str] = Counter()
        source_iter = None
        if source_root is not None:
            source_iter = iter(_iter_source_files(Path(source_root).resolve(), roots))
        for row in file_cursor:
            file_count += 1
            file_id, relative_path, root, source_sha, size_bytes, row_snapshot, row_parser, row_run, parse_status, layout, record_status, occurrence_count = row
            if int(file_id) != file_count:
                raise BatchM3PhysicalError("R0 source-file IDs are not deterministic")
            if previous_path is not None and _path_sort_key(str(relative_path)) <= _path_sort_key(previous_path):
                raise BatchM3PhysicalError("R0 source-file order is not deterministic")
            previous_path = str(relative_path)
            if (row_snapshot, row_parser, row_run) != (snapshot_id, parser_build_id, ingest_run_id):
                raise BatchM3PhysicalError("R0 source-file identity mismatch")
            status_counts[str(record_status)] += 1
            file_bytes += int(size_bytes)
            record_denominator += int(occurrence_count)
            expected_occurrences = None
            if source_iter is not None:
                try:
                    expected_relative, expected_path = next(source_iter)
                except StopIteration as exc:
                    raise BatchM3PhysicalError("R0 source-file reconstruction is shorter than artifact") from exc
                expected_file, expected_occurrences = _scan_file(expected_relative, expected_path)
                actual_file = {
                    "relative_path": relative_path, "root": root, "source_sha256": source_sha,
                    "size_bytes": int(size_bytes), "parse_status": parse_status, "layout": layout,
                    "record_status": record_status, "occurrence_count": int(occurrence_count),
                }
                if actual_file != expected_file:
                    raise BatchM3PhysicalError("R0 source-file reconstruction mismatch")
            seen_for_file = 0
            while next_occurrence is not None and int(next_occurrence[1]) == int(file_id):
                occurrence_id, _file_id, occurrence_snapshot, occurrence_parser, occurrence_run, ordinal, pointer, value_sha = next_occurrence
                if int(occurrence_id) != record_numerator + 1:
                    raise BatchM3PhysicalError("R0 occurrence IDs are not deterministic")
                if (occurrence_snapshot, occurrence_parser, occurrence_run) != (snapshot_id, parser_build_id, ingest_run_id):
                    raise BatchM3PhysicalError("R0 occurrence identity mismatch")
                if int(ordinal) != seen_for_file:
                    raise BatchM3PhysicalError(f"R0 occurrence ordinals are not contiguous: {relative_path}")
                if not isinstance(pointer, str) or not isinstance(value_sha, str) or len(value_sha) != 64:
                    raise BatchM3PhysicalError("R0 occurrence coordinate/hash shape mismatch")
                if expected_occurrences is not None:
                    try:
                        expected_item = next(expected_occurrences)
                    except StopIteration as exc:
                        raise BatchM3PhysicalError("R0 source occurrence reconstruction is shorter than artifact") from exc
                    if (int(expected_item["ordinal"]), str(expected_item["pointer"]), str(expected_item["value_sha256"])) != (int(ordinal), str(pointer), str(value_sha)):
                        raise BatchM3PhysicalError("R0 physical occurrence reconstruction mismatch")
                seen_for_file += 1
                record_numerator += 1
                next_occurrence = occurrence_cursor.fetchone()
            if expected_occurrences is not None:
                try:
                    next(expected_occurrences)
                except StopIteration:
                    pass
                else:
                    raise BatchM3PhysicalError("R0 source occurrence reconstruction has extra rows")
            if seen_for_file != int(occurrence_count):
                raise BatchM3PhysicalError(f"R0 source-file occurrence count mismatch: {relative_path}")
        if next_occurrence is not None:
            raise BatchM3PhysicalError("R0 physical occurrence references an unknown source file")
        if source_iter is not None:
            try:
                next(source_iter)
            except StopIteration:
                pass
            else:
                raise BatchM3PhysicalError("R0 source-file reconstruction has extra files")
        actual_statuses = dict(sorted(status_counts.items()))
        if int(metadata.get("file_denominator", -1)) != file_count or int(metadata.get("record_denominator", -1)) != record_denominator or int(metadata.get("record_numerator", -1)) != record_numerator:
            raise BatchM3PhysicalError("R0 metadata counts do not match rows")
        if metadata.get("unsupported_counts") != actual_statuses or metadata.get("record_status_counts") != actual_statuses:
            raise BatchM3PhysicalError("R0 unsupported counts do not match rows")
        if summary["file_denominator"] != file_count or summary["file_bytes"] != file_bytes:
            raise BatchM3PhysicalError("R0 summary counts do not match rows")
    except sqlite3.Error as exc:
        raise BatchM3PhysicalError(f"R0 verification query failed: {exc}") from exc
    finally:
        connection.close()
    return summary

def build_r0_artifact(
    source_root: str | Path,
    output_path: str | Path,
    *,
    repo_root: str | Path | None = None,
    selected_roots: Sequence[str] | None = None,
    source_roots: Sequence[str] | None = None,
    temp_root: str | Path | None = None,
    invocation_id: str | None = None,
    snapshot_id: str = SNAPSHOT_ID,
    parser_build_id: str = PARSER_BUILD_ID,
    ingest_run_id: str = INGEST_RUN_ID,
    artifact_limit_bytes: int = ARTIFACT_LIMIT_BYTES,
) -> dict[str, Any]:
    """Build and publish a deterministic standalone R0 artifact.

    The scan is performed outside the final lock, but all output identity and
    target-absence checks are repeated by ``publish_absent`` under its stable
    sibling lock.  Source-bound verification completes on the same-directory
    private staging inode before the one atomic rename. R0 verification failures
    retain their staging or published artifact for diagnosis; no receipt is implied.
    """

    source = Path(source_root).resolve()
    output = Path(output_path).absolute()
    if artifact_limit_bytes < ARTIFACT_LIMIT_BYTES or artifact_limit_bytes % 65536:
        raise BatchM3PhysicalError("artifact limit must be at least the historical 4 GiB limit and page aligned")
    if selected_roots is not None and source_roots is not None and tuple(selected_roots) != tuple(source_roots):
        raise BatchM3PhysicalError("selected_roots and source_roots disagree")
    roots = _selected_roots(selected_roots if selected_roots is not None else source_roots)
    if not source.is_dir() or source.is_symlink():
        raise BatchM3PhysicalError(f"source root is unavailable: {source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    invocation = invocation_id or batch_space.new_invocation_id()
    owned = _artifact_root(temp_root, invocation)
    gate = _space_gate(
        Path(repo_root).resolve() if repo_root is not None else Path(__file__).resolve().parents[2],
        owned,
        invocation,
        reservation_bytes=artifact_limit_bytes,
    )
    # Preflight before any source scan. Failure leaves prior evidence intact.
    try:
        assert_output_paths(output, inputs=(("source", source),), target_must_be_absent=True)
    except BatchReleaseError as exc:
        raise BatchM3PhysicalError(str(exc)) from exc
    descriptor, staging = create_private_staging(
        output, prefix=f".{output.name}.{invocation}-"
    )
    os.close(descriptor)
    assert_output_paths(
        output, inputs=(("source", source),), staging=staging,
        target_must_be_absent=True,
    )
    dynamic = _write_artifact(
        staging, source, roots,
        snapshot_id=snapshot_id, parser_build_id=parser_build_id, ingest_run_id=ingest_run_id,
        artifact_limit_bytes=artifact_limit_bytes,
    )
    gate.check("m3-r0-physical-prepublish", projected_bytes=0, cleanup=False)
    # Verify exact source bytes on the private inode before atomic publication.
    verify_r0_artifact(
        staging, source_root=source, selected_roots=roots,
        snapshot_id=snapshot_id, parser_build_id=parser_build_id, ingest_run_id=ingest_run_id,
        artifact_limit_bytes=artifact_limit_bytes,
    )
    try:
        publish_absent(output, staging, inputs=(("source", source),))
    except BatchReleaseError as exc:
        raise BatchM3PhysicalError(str(exc)) from exc
    gate.check("m3-r0-physical-final", projected_bytes=0, cleanup=False)
    summary = verify_r0_artifact(
        output, selected_roots=roots, snapshot_id=snapshot_id,
        parser_build_id=parser_build_id, ingest_run_id=ingest_run_id,
        artifact_limit_bytes=artifact_limit_bytes,
    )
    summary.update({
        "dynamic": dynamic,
        "selected_roots": list(roots),
        "space": gate.report(),
        "invocation_id": invocation,
        "owned_temp_root": str(owned),
    })
    return summary


# Friendly aliases for callers that use the shorter verifier/summary names.
build_physical_artifact = build_r0_artifact
verify_artifact = verify_r0_artifact
summarize_artifact = summarize_r0_artifact


__all__ = [
    "ARTIFACT_LIMIT_BYTES", "BatchM3PhysicalError", "COORDINATE_CONTRACT",
    "INGEST_RUN_ID", "PARSER_BUILD_ID", "PROJECT_SPACE_RESERVATION_BYTES",
    "SCHEMA_VERSION", "SNAPSHOT_ID", "SOURCE_ROOTS", "build_physical_artifact",
    "build_r0_artifact", "summarize_artifact", "summarize_r0_artifact",
    "verify_artifact", "verify_r0_artifact",
]
