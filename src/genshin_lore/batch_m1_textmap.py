from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
from .platform_io import peak_rss_bytes
import shutil
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .build_metrics import finish_measurement, start_measurement
from .batch_release import (
    BatchReleaseError,
    assert_output_paths,
    create_private_staging,
    fsync_file,
    publish_absent,
)
from .db import Database
from .util import canonical_json, digest_id, sha256_bytes, sha256_text


SOURCE_COMMIT = "26df1dfbdf05a82bbb1d97506859f3e1c40718d8"
SOURCE_TREE = "d891ef1d95fdd6c27a1da8a32ea7a61b483647a6"
SNAPSHOT_ID = f"anime-game-data:7.0:{SOURCE_COMMIT}"
CONTRACT_VERSION = "snapshot-textmap-physical-v2"
PARSING_CONTRACT_VERSION = "object-container-pair-ordinal-v2"
PARSER_VERSION = "batch-m1-textmap-physical-parser-v2"
MAPPING_VERSION = "physical-occurrence-only-v2"
WAVE_ID = "batch-wave:m1-textmap-foundation-v2"
FAMILY = "__snapshot_textmap_physical__"
EXPECTED_SCHEMA13_SIZE = 1_723_596_800
EXPECTED_SCHEMA13_SHA256 = "7ff8163b6007a41b0827af05fbcf996353617c52f354a131431da912712e58be"
CANDIDATE_INCREMENT_LIMIT_BYTES = 4 * 1024**3
# Conservative on-disk reservation multipliers used before any cache/shard
# write.  They are derived from the sealed source bytes rather than the
# builder's eventual output size, and intentionally leave room for SQLite
# temporary pages and the private staging inode.
TEXTMAP_CACHE_RESERVE_MULTIPLIER = 32
TEXTMAP_SHARD_RESERVE_MULTIPLIER = 16
TEXTMAP_SHARD_HEADROOM_BYTES = 256 * 1024**2
TEXTMAP_RELEASE_HEADROOM_BYTES = 512 * 1024**2
MIGRATION_0014_APPLIED_AT = "2026-09-01T00:00:00+00:00"
MIGRATION_0015_APPLIED_AT = "2026-09-02T00:00:00+00:00"
CHUNK_SIZE = 50_000
RSS_LIMIT_BYTES = 8 * 1024**3
WORKER_MARGIN = 0.10
WORKER_COUNTS = (1, 2, 4)
WORKER_TRIAL_ORDER = (1, 4, 2, 2, 1, 4, 4, 2, 1)


class BatchM1Error(RuntimeError):
    pass


@dataclass(frozen=True)
class PairOccurrence:
    pair_ordinal: int
    pointer: str
    raw_key_json: str
    key_text: str
    raw_text_json: str
    raw_text: str
    raw_key_sha256: str
    raw_text_sha256: str
    raw_record_sha256: str


def canonical_bytes(value: Any) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _skip_ws(text: str, position: int) -> int:
    while position < len(text) and text[position] in " \t\r\n":
        position += 1
    return position


def _pointer(ordinal: int, key: str) -> str:
    escaped = key.replace("~", "~0").replace("/", "~1")
    return f"/@{ordinal}:{escaped}"


def iter_textmap_pairs(raw: bytes, *, wanted_keys: set[str] | frozenset[str] | None = None) -> Iterator[PairOccurrence]:
    """Validate every pair; optionally materialize only selected lexical rows.

    Selection never short-circuits parsing, type/duplicate checks or EOF.
    With no selection the complete physical rowset is unchanged.
    """

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BatchM1Error("TextMap is not valid UTF-8") from exc
    decoder = json.JSONDecoder()
    position = _skip_ws(text, 0)
    if position >= len(text) or text[position] != "{":
        raise BatchM1Error("TextMap must be a top-level JSON object")
    position += 1
    ordinal = 0
    seen: set[str] = set()
    while True:
        position = _skip_ws(text, position)
        if position < len(text) and text[position] == "}":
            position = _skip_ws(text, position + 1)
            if position != len(text):
                raise BatchM1Error("TextMap has trailing non-whitespace bytes")
            return
        key_start = position
        try:
            key, key_end = decoder.raw_decode(text, position)
        except json.JSONDecodeError as exc:
            raise BatchM1Error(f"invalid TextMap key at character {position}") from exc
        if not isinstance(key, str):
            raise BatchM1Error(f"TextMap key {ordinal} is not a string")
        raw_key_json = text[key_start:key_end]
        position = _skip_ws(text, key_end)
        if position >= len(text) or text[position] != ":":
            raise BatchM1Error(f"TextMap pair {ordinal} has no colon")
        position = _skip_ws(text, position + 1)
        value_start = position
        try:
            value, value_end = decoder.raw_decode(text, position)
        except json.JSONDecodeError as exc:
            raise BatchM1Error(f"invalid TextMap value at pair {ordinal}") from exc
        if not isinstance(value, str):
            raise BatchM1Error(f"TextMap value {ordinal} is not a string")
        raw_text_json = text[value_start:value_end]
        if wanted_keys is None or key in wanted_keys:
            raw_record = f'{{"key":{raw_key_json},"text":{raw_text_json}}}'
            yield PairOccurrence(
                pair_ordinal=ordinal,
                pointer=_pointer(ordinal, key),
                raw_key_json=raw_key_json,
                key_text=key,
                raw_text_json=raw_text_json,
                raw_text=value,
                raw_key_sha256=sha256_text(raw_key_json),
                raw_text_sha256=sha256_text(raw_text_json),
                raw_record_sha256=sha256_text(raw_record),
            )
        if key in seen:
            raise BatchM1Error(f"duplicate TextMap key observed at pair {ordinal}: {key!r}")
        seen.add(key)
        ordinal += 1
        position = _skip_ws(text, value_end)
        if position >= len(text):
            raise BatchM1Error("unterminated TextMap object")
        if text[position] == ",":
            position += 1
            continue
        if text[position] == "}":
            position = _skip_ws(text, position + 1)
            if position != len(text):
                raise BatchM1Error("TextMap has trailing non-whitespace bytes")
            return
        raise BatchM1Error(f"invalid delimiter after TextMap pair {ordinal - 1}")


def logical_pair_bytes(relative_path: str, item: PairOccurrence) -> bytes:
    return canonical_bytes(
        [
            relative_path,
            item.pair_ordinal,
            item.pointer,
            item.raw_key_json,
            item.key_text,
            item.raw_text_json,
            item.raw_text,
            item.raw_key_sha256,
            item.raw_text_sha256,
            item.raw_record_sha256,
        ]
    )


def scan_textmap(path: Path, relative_path: str) -> dict[str, Any]:
    raw = path.read_bytes()
    digest = hashlib.sha256()
    count = 0
    first: dict[str, Any] | None = None
    last: dict[str, Any] | None = None
    for item in iter_textmap_pairs(raw):
        digest.update(logical_pair_bytes(relative_path, item))
        anchor = {
            "key_text": item.key_text,
            "pair_ordinal": item.pair_ordinal,
            "pointer": item.pointer,
            "raw_key_json": item.raw_key_json,
            "raw_text_json": item.raw_text_json,
        }
        if first is None:
            first = anchor
        last = anchor
        count += 1
    return {
        "first_pair": first,
        "last_pair": last,
        "logical_pair_sha256": digest.hexdigest(),
        "pair_count": count,
        "raw_sha256": sha256_bytes(raw),
        "raw_size_bytes": len(raw),
    }


def verify_source_pin(source_root: Path) -> None:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=source_root, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=source_root, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain=v1"], cwd=source_root, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ).stdout
    if (commit, tree, status) != (SOURCE_COMMIT, SOURCE_TREE, ""):
        raise BatchM1Error("pinned source commit/tree/clean preflight failed")


def manifest_without_derived_ids(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result.pop("derived_identity", None)
    return result


def derive_identity(value: Mapping[str, Any]) -> dict[str, str]:
    config_checksum = sha256_bytes(canonical_bytes(manifest_without_derived_ids(value)))
    parser_build_id = digest_id(
        "pb-batch-m1-textmap", PARSER_VERSION, MAPPING_VERSION,
        PARSING_CONTRACT_VERSION, config_checksum,
    )
    ingest_run_id = digest_id("ir-batch-m1-textmap", SNAPSHOT_ID, parser_build_id, config_checksum)
    shard_id = digest_id("batch-shard", WAVE_ID, FAMILY, CONTRACT_VERSION, ingest_run_id)
    return {
        "config_checksum": config_checksum,
        "ingest_run_id": ingest_run_id,
        "parser_build_id": parser_build_id,
        "shard_id": shard_id,
        "wave_id": WAVE_ID,
    }


def load_manifest(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    if raw != canonical_bytes(json.loads(raw)):
        raise BatchM1Error("M1 manifest is not canonical JSON")
    value = json.loads(raw)
    if value.get("schema_version") != 2 or value.get("manifest_id") != "anime-game-data-7.0-batch-m1-textmap-corpus":
        raise BatchM1Error("unsupported M1 manifest")
    source = value.get("source")
    if not isinstance(source, dict) or source.get("commit") != SOURCE_COMMIT or source.get("tree") != SOURCE_TREE or source.get("snapshot_id") != SNAPSHOT_ID:
        raise BatchM1Error("M1 manifest source identity mismatch")
    contract = value.get("contract")
    if contract != {
        "contract_version": CONTRACT_VERSION,
        "family": FAMILY,
        "logical_identity_created": False,
        "parsing_contract_version": PARSING_CONTRACT_VERSION,
        "text_or_hash_merges_occurrences": False,
    }:
        raise BatchM1Error("M1 physical occurrence contract mismatch")
    files = value.get("files")
    if not isinstance(files, list) or len(files) != 6:
        raise BatchM1Error("M1 manifest must seal exactly six TextMap files")
    if value.get("derived_identity") != derive_identity(value):
        raise BatchM1Error("M1 derived identity mismatch")
    return value, sha256_bytes(raw)


def cache_key(manifest: Mapping[str, Any]) -> str:
    identity = manifest["derived_identity"]
    payload = {
        "files": [
            {key: item[key] for key in ("relative_path", "git_blob_oid", "raw_size_bytes", "raw_sha256")}
            for item in manifest["files"]
        ],
        "parser_build_id": identity["parser_build_id"],
        "parsing_contract_version": manifest["contract"]["parsing_contract_version"],
        "source": manifest["source"],
    }
    return sha256_bytes(canonical_bytes(payload))


def storage_reservation(
    manifest: Mapping[str, Any], baseline_size_bytes: int, *, candidate_count: int = 1,
) -> dict[str, int]:
    """Return a conservative upper bound for the next batch write set.

    The source files are the only inputs that can be written into the M1
    cache/shard.  A generous multiplier covers tokenized SQLite rows and
    duplicated lexical text.  Candidate reservation explicitly includes the
    complete 4 GiB increment allowance, a second private copy, and SQLite
    journal/sidecar headroom; it is deliberately independent of the previous
    prototype's observed candidate size.
    """

    raw_source_bytes = sum(int(item["raw_size_bytes"]) for item in manifest["files"])
    cache_bytes = raw_source_bytes * TEXTMAP_CACHE_RESERVE_MULTIPLIER
    shard_bytes = raw_source_bytes * TEXTMAP_SHARD_RESERVE_MULTIPLIER
    shard_reserve = cache_bytes + shard_bytes + TEXTMAP_SHARD_HEADROOM_BYTES
    candidate_upper = int(baseline_size_bytes) + CANDIDATE_INCREMENT_LIMIT_BYTES
    candidate_reserve_each = candidate_upper * 2 + TEXTMAP_RELEASE_HEADROOM_BYTES
    return {
        "raw_source_bytes": raw_source_bytes,
        "cache_upper_bound_bytes": cache_bytes,
        "shard_upper_bound_bytes": shard_bytes,
        "shard_headroom_bytes": TEXTMAP_SHARD_HEADROOM_BYTES,
        "before_shard_bytes": shard_reserve,
        "candidate_upper_bound_bytes": candidate_upper,
        "candidate_release_headroom_bytes": TEXTMAP_RELEASE_HEADROOM_BYTES,
        "candidate_reserve_each_bytes": candidate_reserve_each,
        "candidate_count": int(candidate_count),
        "before_candidates_bytes": candidate_reserve_each * int(candidate_count),
    }


def _peak_rss_bytes() -> int:
    value = peak_rss_bytes()
    if value is None:
        raise BatchM1Error("process peak memory metric unavailable on this platform")
    return value


def _benchmark_file(arguments: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    root, spec = arguments
    started = time.perf_counter()
    raw = (Path(root) / spec["relative_path"]).read_bytes()
    count = sum(1 for _item in iter_textmap_pairs(raw))
    if sha256_bytes(raw) != spec["raw_sha256"] or count != spec["pair_count"]:
        raise BatchM1Error(f"benchmark input mismatch: {spec['relative_path']}")
    return {
        "pair_count": count,
        "peak_rss_bytes": _peak_rss_bytes(),
        "pid": os.getpid(),
        "wall_seconds": time.perf_counter() - started,
    }


def benchmark_workers(source_root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    for spec in manifest["files"]:
        with (source_root / spec["relative_path"]).open("rb") as handle:
            while handle.read(1024 * 1024):
                pass
    trials: dict[str, list[dict[str, Any]]] = {str(value): [] for value in WORKER_COUNTS}
    tasks = [(str(source_root), item) for item in manifest["files"]]
    for workers in WORKER_TRIAL_ORDER:
        start = time.perf_counter()
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as executor:
            rows = list(executor.map(_benchmark_file, tasks))
        per_process: dict[int, int] = {}
        for row in rows:
            per_process[row["pid"]] = max(per_process.get(row["pid"], 0), row["peak_rss_bytes"])
        trials[str(workers)].append({
            "cache_state": "uncontrolled_os_cache_after_explicit_file_warmup",
            "pair_count": sum(item["pair_count"] for item in rows),
            "peak_rss_upper_bound_bytes": sum(per_process.values()) + _peak_rss_bytes(),
            "process_count_observed": len(per_process),
            "wall_seconds": round(time.perf_counter() - start, 6),
        })
    summaries: dict[str, Any] = {}
    for workers in WORKER_COUNTS:
        rows = trials[str(workers)]
        walls = sorted(float(item["wall_seconds"]) for item in rows)
        median = walls[1]
        spread = (walls[-1] - walls[0]) / median if median else float("inf")
        summaries[str(workers)] = {
            "conservative_peak_rss_bytes": max(item["peak_rss_upper_bound_bytes"] for item in rows),
            "spread_ratio": round(spread, 6),
            "trials": rows,
            "wall_median_seconds": round(median, 6),
        }
    best = min(item["wall_median_seconds"] for item in summaries.values())
    eligible = [
        workers for workers in WORKER_COUNTS
        if summaries[str(workers)]["wall_median_seconds"] <= best * (1 + WORKER_MARGIN)
        and summaries[str(workers)]["conservative_peak_rss_bytes"] <= RSS_LIMIT_BYTES
    ]
    if not eligible:
        raise BatchM1Error("no TextMap worker count satisfies wall/RSS selection contract")
    return {
        "best_wall_seconds": best,
        "cache_state": "uncontrolled_os_cache",
        "execution_order": list(WORKER_TRIAL_ORDER),
        "measurement_count_per_worker": 3,
        "rss_limit_bytes": RSS_LIMIT_BYTES,
        "selected_worker_count": min(eligible),
        "selection_contract": "smallest worker count within best+10% and conservative peak RSS <=8 GiB",
        "summaries": summaries,
    }


SPOOL_SCHEMA = """
PRAGMA page_size=4096;
PRAGMA journal_mode=OFF;
PRAGMA synchronous=OFF;
CREATE TABLE spool_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE pair_occurrence(
 pair_ordinal INTEGER PRIMARY KEY,pointer TEXT NOT NULL,raw_key_json TEXT NOT NULL,
 key_text TEXT NOT NULL,raw_text_json TEXT NOT NULL,raw_text TEXT NOT NULL,
 raw_key_sha256 TEXT NOT NULL,raw_text_sha256 TEXT NOT NULL,raw_record_sha256 TEXT NOT NULL
);
"""


def _compile_spool(arguments: tuple[str, dict[str, Any], str, str]) -> dict[str, Any]:
    source_root, spec, cache_root, overall_cache_key = arguments
    cache_dir = Path(cache_root) / overall_cache_key
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / (spec["raw_sha256"] + ".sqlite")
    if destination.exists():
        with sqlite3.connect(f"file:{destination.as_posix()}?mode=ro&immutable=1", uri=True) as connection:
            metadata = dict(connection.execute("SELECT key,value FROM spool_metadata"))
            count = int(connection.execute("SELECT COUNT(*) FROM pair_occurrence").fetchone()[0])
        if metadata == {
            "cache_key": overall_cache_key,
            "logical_pair_sha256": spec["logical_pair_sha256"],
            "raw_sha256": spec["raw_sha256"],
            "relative_path": spec["relative_path"],
        } and count == spec["pair_count"]:
            return {"cache_hit": True, "pair_count": count, "path": str(destination), "peak_rss_bytes": _peak_rss_bytes()}
        raise BatchM1Error(f"invalid TextMap cache entry: {destination}")
    raw = (Path(source_root) / spec["relative_path"]).read_bytes()
    if len(raw) != spec["raw_size_bytes"] or sha256_bytes(raw) != spec["raw_sha256"]:
        raise BatchM1Error(f"TextMap file seal mismatch: {spec['relative_path']}")
    fd, temporary_name = tempfile.mkstemp(prefix=".m1-spool-", suffix=".sqlite", dir=cache_dir)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        connection = sqlite3.connect(temporary)
        connection.executescript(SPOOL_SCHEMA)
        connection.executemany(
            "INSERT INTO spool_metadata VALUES (?,?)",
            sorted({
                "cache_key": overall_cache_key,
                "logical_pair_sha256": spec["logical_pair_sha256"],
                "raw_sha256": spec["raw_sha256"],
                "relative_path": spec["relative_path"],
            }.items()),
        )
        batch: list[tuple[Any, ...]] = []
        count = 0
        digest = hashlib.sha256()
        for item in iter_textmap_pairs(raw):
            digest.update(logical_pair_bytes(spec["relative_path"], item))
            batch.append(tuple(item.__dict__.values()))
            count += 1
            if len(batch) >= 10_000:
                connection.executemany("INSERT INTO pair_occurrence VALUES (?,?,?,?,?,?,?,?,?)", batch)
                batch.clear()
        if batch:
            connection.executemany("INSERT INTO pair_occurrence VALUES (?,?,?,?,?,?,?,?,?)", batch)
        connection.commit()
        connection.execute("VACUUM")
        connection.close()
        if count != spec["pair_count"] or digest.hexdigest() != spec["logical_pair_sha256"]:
            raise BatchM1Error(f"TextMap logical seal mismatch: {spec['relative_path']}")
        os.chmod(temporary, 0o600)
        try:
            publish_absent(
                destination, temporary,
                inputs=(("cache_root", cache_dir),),
            )
        except BatchReleaseError:
            # Another worker may have published the same content while this
            # worker was compiling it.  Never replace that inode; accept it
            # only after independently checking its metadata and row count.
            if not destination.exists():
                raise
            with sqlite3.connect(
                f"file:{destination.as_posix()}?mode=ro&immutable=1", uri=True
            ) as existing:
                metadata = dict(existing.execute("SELECT key,value FROM spool_metadata"))
                existing_count = int(
                    existing.execute("SELECT COUNT(*) FROM pair_occurrence").fetchone()[0]
                )
            if metadata != {
                "cache_key": overall_cache_key,
                "logical_pair_sha256": spec["logical_pair_sha256"],
                "raw_sha256": spec["raw_sha256"],
                "relative_path": spec["relative_path"],
            } or existing_count != spec["pair_count"]:
                raise BatchM1Error(f"racing TextMap cache entry is invalid: {destination}")
            return {
                "cache_hit": True,
                "pair_count": existing_count,
                "path": str(destination),
                "peak_rss_bytes": _peak_rss_bytes(),
            }
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"cache_hit": False, "pair_count": count, "path": str(destination), "peak_rss_bytes": _peak_rss_bytes()}


SHARD_SCHEMA = """
PRAGMA page_size=4096;
PRAGMA journal_mode=OFF;
PRAGMA synchronous=OFF;
CREATE TABLE shard_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE textmap_source_container(
 relative_path TEXT PRIMARY KEY,locale TEXT NOT NULL,shard_tier TEXT NOT NULL,
 source_pointer TEXT NOT NULL CHECK(source_pointer='/'),raw_json TEXT NOT NULL,
 raw_sha256 TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE textmap_pair_occurrence(
 relative_path TEXT NOT NULL,locale TEXT NOT NULL,shard_tier TEXT NOT NULL,
 source_record_pointer TEXT NOT NULL CHECK(source_record_pointer='/'),
 pair_ordinal INTEGER NOT NULL,pointer TEXT NOT NULL,raw_key_json TEXT NOT NULL,
 key_text TEXT NOT NULL,raw_text_json TEXT NOT NULL,raw_text TEXT NOT NULL,
 raw_key_sha256 TEXT NOT NULL,raw_text_sha256 TEXT NOT NULL,raw_record_sha256 TEXT NOT NULL,
 PRIMARY KEY(relative_path,pair_ordinal)
) WITHOUT ROWID;
CREATE TABLE table_digest(
 table_name TEXT PRIMARY KEY,schema_sql_sha256 TEXT NOT NULL,ordering_json TEXT NOT NULL,
 row_count INTEGER NOT NULL,logical_row_sha256 TEXT NOT NULL,chunk_size INTEGER NOT NULL,
 chunk_count INTEGER NOT NULL
) WITHOUT ROWID;
CREATE TABLE chunk_digest(
 table_name TEXT NOT NULL,chunk_ordinal INTEGER NOT NULL,first_key_json TEXT NOT NULL,
 last_key_json TEXT NOT NULL,row_count INTEGER NOT NULL,logical_row_sha256 TEXT NOT NULL,
 PRIMARY KEY(table_name,chunk_ordinal)
) WITHOUT ROWID;
"""


PAIR_COLUMNS = (
    "relative_path", "locale", "shard_tier", "pair_ordinal", "pointer",
    "raw_key_json", "key_text", "raw_text_json", "raw_text", "raw_key_sha256",
    "raw_text_sha256", "raw_record_sha256",
)


def _pair_row_bytes(row: Sequence[Any]) -> bytes:
    return canonical_bytes(list(row))


def _digest_rows(
    rows: Iterable[Sequence[Any]], chunk_size: int = CHUNK_SIZE,
    *, key_indexes: tuple[int, ...] = (0, 3),
) -> tuple[int, str, list[dict[str, Any]]]:
    full = hashlib.sha256()
    chunk = hashlib.sha256()
    chunks: list[dict[str, Any]] = []
    count = 0
    first_key: list[Any] | None = None
    last_key: list[Any] | None = None
    chunk_count = 0
    for row in rows:
        raw = _pair_row_bytes(row)
        full.update(raw)
        chunk.update(raw)
        key = [row[index] for index in key_indexes]
        if first_key is None:
            first_key = key
        last_key = key
        count += 1
        chunk_count += 1
        if chunk_count == chunk_size:
            chunks.append({"first_key": first_key, "last_key": last_key, "logical_row_sha256": chunk.hexdigest(), "row_count": chunk_count})
            chunk = hashlib.sha256(); first_key = None; last_key = None; chunk_count = 0
    if chunk_count:
        chunks.append({"first_key": first_key, "last_key": last_key, "logical_row_sha256": chunk.hexdigest(), "row_count": chunk_count})
    return count, full.hexdigest(), chunks


def build_shard(
    source_root: Path, manifest_path: Path, destination: Path, cache_dir: Path,
    *, workers: int, overwrite: bool = False,
) -> dict[str, Any]:
    manifest, manifest_sha = load_manifest(manifest_path)
    verify_source_pin(source_root)
    if workers not in WORKER_COUNTS:
        raise BatchM1Error("workers must be one of 1, 2, or 4")
    if overwrite:
        raise BatchM1Error("M1 shard overwrite is forbidden; choose an absent target")
    try:
        assert_output_paths(
            destination,
            inputs=(("manifest", manifest_path), ("cache", cache_dir)),
            target_must_be_absent=True,
        )
    except BatchReleaseError as exc:
        raise BatchM1Error(str(exc)) from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    started = start_measurement()
    overall_cache_key = cache_key(manifest)
    tasks = [(str(source_root), item, str(cache_dir), overall_cache_key) for item in manifest["files"]]
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as executor:
        spools = list(executor.map(_compile_spool, tasks))
    fd, temporary = create_private_staging(destination, prefix=".m1-shard-")
    os.close(fd)
    try:
        connection = sqlite3.connect(temporary)
        connection.executescript(SHARD_SCHEMA)
        for index, (spec, spool) in enumerate(zip(manifest["files"], spools, strict=True)):
            raw = (source_root / spec["relative_path"]).read_bytes()
            try:
                raw_json = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise BatchM1Error(f"TextMap source container is not UTF-8: {spec['relative_path']}") from exc
            if sha256_bytes(raw) != spec["raw_sha256"]:
                raise BatchM1Error(f"TextMap source container seal mismatch: {spec['relative_path']}")
            connection.execute(
                "INSERT INTO textmap_source_container VALUES (?,?,?,?,?,?)",
                (spec["relative_path"], spec["locale"], spec["tier"], "/", raw_json, spec["raw_sha256"]),
            )
            connection.execute("ATTACH DATABASE ? AS spool", (spool["path"],))
            connection.execute(
                "INSERT INTO textmap_pair_occurrence "
                "SELECT ?,?,?,?,pair_ordinal,pointer,raw_key_json,key_text,raw_text_json,raw_text,"
                "raw_key_sha256,raw_text_sha256,raw_record_sha256 FROM spool.pair_occurrence ORDER BY pair_ordinal",
                (spec["relative_path"], spec["locale"], spec["tier"], "/"),
            )
            connection.commit()
            connection.execute("DETACH DATABASE spool")
        rows = connection.execute(
            "SELECT " + ",".join(PAIR_COLUMNS) + " FROM textmap_pair_occurrence ORDER BY relative_path,pair_ordinal"
        )
        row_count, rowset_sha, chunks = _digest_rows(rows)
        schema_sql = str(connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='textmap_pair_occurrence'"
        ).fetchone()[0])
        schema_sha = sha256_text(" ".join(schema_sql.split()))
        connection.execute(
            "INSERT INTO table_digest VALUES (?,?,?,?,?,?,?)",
            ("textmap_pair_occurrence", schema_sha, canonical_json(["relative_path", "pair_ordinal"]), row_count, rowset_sha, CHUNK_SIZE, len(chunks)),
        )
        connection.executemany(
            "INSERT INTO chunk_digest VALUES (?,?,?,?,?,?)",
            [("textmap_pair_occurrence", index, canonical_json(item["first_key"]), canonical_json(item["last_key"]), item["row_count"], item["logical_row_sha256"]) for index, item in enumerate(chunks)],
        )
        identity = manifest["derived_identity"]
        metadata = {
            "cache_key": overall_cache_key,
            "contract_version": CONTRACT_VERSION,
            "family": FAMILY,
            "source_record_granularity": "one_object_container_per_file",
            "container_count": str(len(manifest["files"])),
            "ingest_run_id": identity["ingest_run_id"],
            "manifest_sha256": manifest_sha,
            "parser_build_id": identity["parser_build_id"],
            "row_count": str(row_count),
            "rowset_sha256": rowset_sha,
            "schema_version": "2",
            "snapshot_id": SNAPSHOT_ID,
            "write_set_json": canonical_json(["textmap_physical_occurrence"]),
        }
        connection.executemany("INSERT INTO shard_metadata VALUES (?,?)", sorted(metadata.items()))
        connection.commit()
        connection.execute("VACUUM")
        connection.close()
        fsync_file(temporary)
        publish_absent(
            destination,
            temporary,
            inputs=(
                ("source_root", source_root),
                ("manifest", manifest_path),
                ("cache", cache_dir),
            ),
        )
    finally:
        if temporary.exists():
            temporary.unlink()
    metrics = finish_measurement(started)
    return {
        "cache_hits": sum(1 for item in spools if item["cache_hit"]),
        "cache_key": overall_cache_key,
        "manifest_sha256": manifest_sha,
        "metrics": metrics,
        "pair_count": row_count,
        "rowset_sha256": rowset_sha,
        "shard_path": str(destination),
        "shard_sha256": file_sha256(destination),
        "shard_size_bytes": destination.stat().st_size,
        "workers": workers,
    }


def _apply_schema15(candidate: Path) -> None:
    with Database(candidate) as database:
        database.initialize_through(15)
        # Migration application is mechanically validated first, then its
        # candidate-only ledger tuples are pinned so two fresh builds have the
        # same bytes.  These timestamps are not evidence identity.
        database.conn.executemany(
            "UPDATE schema_migration SET applied_at=? WHERE version=?",
            ((MIGRATION_0014_APPLIED_AT, 14), (MIGRATION_0015_APPLIED_AT, 15)),
        )
        database.conn.commit()


def _raw_record_json(row: sqlite3.Row) -> str:
    return f'{{"key":{row["raw_key_json"]},"text":{row["raw_text_json"]}}}'


def import_shard_into_candidate(
    baseline: Path, shard: Path, manifest_path: Path, destination: Path,
) -> dict[str, Any]:
    manifest, manifest_sha = load_manifest(manifest_path)
    try:
        assert_output_paths(
            destination,
            inputs=(
                ("baseline", baseline),
                ("shard", shard),
                ("manifest", manifest_path),
            ),
            target_must_be_absent=True,
        )
    except BatchReleaseError as exc:
        raise BatchM1Error(str(exc)) from exc
    if baseline.stat().st_size != EXPECTED_SCHEMA13_SIZE or file_sha256(baseline) != EXPECTED_SCHEMA13_SHA256:
        raise BatchM1Error("accepted Schema 13 baseline seal mismatch")
    started = start_measurement()
    fd, temporary = create_private_staging(destination, prefix=".m1-candidate-")
    try:
        with baseline.open("rb") as source, os.fdopen(fd, "wb") as target:
            fd = -1
            shutil.copyfileobj(source, target, 1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        _apply_schema15(temporary)
        shard_sha = file_sha256(shard)
        with sqlite3.connect(shard) as shard_connection:
            shard_connection.row_factory = sqlite3.Row
            metadata = dict(shard_connection.execute("SELECT key,value FROM shard_metadata"))
        identity = manifest["derived_identity"]
        if metadata.get("manifest_sha256") != manifest_sha or metadata.get("rowset_sha256") != manifest["expected"]["logical_pair_sha256"]:
            raise BatchM1Error("standalone shard manifest/rowset binding mismatch")
        connection = sqlite3.connect(temporary)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("ATTACH DATABASE ? AS shard", (str(shard),))
        file_ids: dict[str, str] = {}
        for spec in manifest["files"]:
            row = connection.execute(
                "SELECT id,sha256,size_bytes FROM source_file WHERE snapshot_id=? AND relative_path=?",
                (SNAPSHOT_ID, spec["relative_path"]),
            ).fetchone()
            if row is None or (row["sha256"], row["size_bytes"]) != (spec["raw_sha256"], spec["raw_size_bytes"]):
                raise BatchM1Error(f"accepted source_file mismatch: {spec['relative_path']}")
            file_ids[spec["relative_path"]] = str(row["id"])
        containers = {
            str(row["relative_path"]): row
            for row in connection.execute(
                "SELECT relative_path,source_pointer,raw_json,raw_sha256 FROM shard.textmap_source_container ORDER BY relative_path"
            )
        }
        if tuple(containers) != tuple(sorted(spec["relative_path"] for spec in manifest["files"])):
            raise BatchM1Error("standalone shard source-container inventory mismatch")
        connection.execute(
            "INSERT INTO parser_build VALUES (?,?,?,?,?,?)",
            (identity["parser_build_id"], PARSER_VERSION, MAPPING_VERSION, sha256_text(PARSER_VERSION + "\n" + PARSING_CONTRACT_VERSION), identity["config_checksum"], MIGRATION_0015_APPLIED_AT),
        )
        connection.execute(
            "INSERT INTO ingest_run VALUES (?,?,?,?,?,?,?)",
            (identity["ingest_run_id"], SNAPSHOT_ID, identity["parser_build_id"], canonical_json({"files": [item["relative_path"] for item in manifest["files"]], "scope": "six pinned TextMap physical pair containers"}), canonical_json({"manifest_sha256": manifest_sha, "parsing_contract_version": PARSING_CONTRACT_VERSION}), "complete", MIGRATION_0015_APPLIED_AT),
        )
        connection.execute(
            "INSERT INTO batch_wave VALUES (?,?,?,?,?,?,?)",
            (WAVE_ID, SNAPSHOT_ID, "batch-wave-v2", manifest_sha, 15, "complete", canonical_json({"coverage_level": "raw_physical", "product_accepted": False, "source_record_granularity": "one_object_container_per_file"})),
        )
        connection.execute(
            "INSERT INTO batch_shard VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (identity["shard_id"], WAVE_ID, FAMILY, CONTRACT_VERSION, identity["ingest_run_id"], manifest_sha, 1, canonical_json(["source_snapshot", "source_file"]), canonical_json(["source_record", "textmap_physical_occurrence"]), metadata["rowset_sha256"], "imported", canonical_json({"logical_identity_created": False, "cache_key": metadata["cache_key"]})),
        )
        insert_records: list[tuple[Any, ...]] = []
        insert_occurrences: list[tuple[Any, ...]] = []
        for spec in sorted(manifest["files"], key=lambda item: item["relative_path"]):
            container = containers[spec["relative_path"]]
            source_file_id = file_ids[spec["relative_path"]]
            source_record_id = digest_id("sr", source_file_id, "/")
            if container["source_pointer"] != "/" or container["raw_sha256"] != spec["raw_sha256"]:
                raise BatchM1Error(f"source container seal mismatch: {spec['relative_path']}")
            insert_records.append((source_record_id, source_file_id, "/", "textmap_object_container", None, container["raw_sha256"], container["raw_json"]))
        connection.executemany("INSERT INTO source_record VALUES (?,?,?,?,?,?,?)", insert_records)
        insert_records_count = len(insert_records)
        insert_records.clear()
        cursor = connection.execute("SELECT * FROM shard.textmap_pair_occurrence ORDER BY relative_path,pair_ordinal")
        imported = 0
        for row in cursor:
            source_file_id = file_ids[row["relative_path"]]
            source_record_id = digest_id("sr", source_file_id, "/")
            if row["source_record_pointer"] != "/":
                raise BatchM1Error("pair occurrence is not bound to the object container")
            occurrence_id = digest_id("textmap-physical", identity["ingest_run_id"], source_file_id, row["pair_ordinal"])
            insert_occurrences.append((
                occurrence_id, identity["ingest_run_id"], SNAPSHOT_ID, identity["parser_build_id"], source_file_id,
                source_record_id, "/", row["locale"], row["shard_tier"], row["pair_ordinal"], row["pointer"],
                row["raw_key_json"], row["key_text"], row["raw_text_json"], row["raw_text"],
                row["raw_key_sha256"], row["raw_text_sha256"], row["raw_record_sha256"],
                canonical_json({"hash_identity_effect": "none", "text_identity_effect": "none"}),
            ))
            imported += 1
            if len(insert_occurrences) >= 10_000:
                connection.executemany("INSERT INTO textmap_physical_occurrence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", insert_occurrences)
                insert_occurrences.clear()
        if insert_occurrences:
            connection.executemany("INSERT INTO textmap_physical_occurrence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", insert_occurrences)
        table_columns = [str(row[1]) for row in connection.execute("PRAGMA table_info(textmap_physical_occurrence)")]
        order = ["ingest_run_id", "source_file_id", "pair_ordinal"]
        rows = connection.execute(
            "SELECT " + ",".join(table_columns) + " FROM textmap_physical_occurrence WHERE ingest_run_id=? ORDER BY ingest_run_id,source_file_id,pair_ordinal",
            (identity["ingest_run_id"],),
        )
        row_count, rowset_sha, chunks = _digest_rows(rows, key_indexes=(1, 4, 9))
        schema_sql = str(connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='textmap_physical_occurrence'").fetchone()[0])
        digest_id_value = digest_id("batch-table-digest", WAVE_ID, identity["shard_id"], "textmap_physical_occurrence")
        connection.execute(
            "INSERT INTO batch_table_digest VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (digest_id_value, WAVE_ID, identity["shard_id"], "textmap_physical_occurrence", sha256_text(" ".join(schema_sql.split())), canonical_json(order), row_count, rowset_sha, CHUNK_SIZE, len(chunks), canonical_json({"digest_contract": "canonical-json-row-v1"})),
        )
        connection.executemany(
            "INSERT INTO batch_table_chunk_digest VALUES (?,?,?,?,?,?,?)",
            [(digest_id("batch-chunk-digest", digest_id_value, index), digest_id_value, index, canonical_json(item["first_key"]), canonical_json(item["last_key"]), item["row_count"], item["logical_row_sha256"]) for index, item in enumerate(chunks)],
        )
        connection.execute(
            "INSERT INTO batch_shard_import VALUES (?,?,?,?,?,?,?,?)",
            (digest_id("batch-shard-import", WAVE_ID, identity["shard_id"]), WAVE_ID, identity["shard_id"], 15, shard_sha, imported, "complete", canonical_json({"source_record_rows": insert_records_count, "pair_occurrence_rows": imported})),
        )
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise BatchM1Error(f"candidate foreign-key violations: {violations[:5]}")
        connection.commit()
        connection.close()
        candidate_size = temporary.stat().st_size
        candidate_increment = candidate_size - baseline.stat().st_size
        if candidate_increment > CANDIDATE_INCREMENT_LIMIT_BYTES:
            raise BatchM1Error(
                "Schema 15 candidate increment exceeds the 4 GiB hard limit: "
                f"{candidate_increment} > {CANDIDATE_INCREMENT_LIMIT_BYTES} bytes"
            )
        fsync_file(temporary)
        publish_absent(
            destination,
            temporary,
            inputs=(
                ("baseline", baseline),
                ("shard", shard),
                ("manifest", manifest_path),
            ),
        )
    finally:
        if fd >= 0:
            os.close(fd)
        if temporary.exists():
            temporary.unlink()
    metrics = finish_measurement(started)
    return {
        "candidate_path": str(destination),
        "candidate_sha256": file_sha256(destination),
        "candidate_size_bytes": destination.stat().st_size,
        "candidate_increment_bytes": destination.stat().st_size - baseline.stat().st_size,
        "imported_pair_count": imported,
        "manifest_sha256": manifest_sha,
        "metrics": metrics,
        "rowset_sha256": rowset_sha,
        "schema_version": 15,
        "shard_sha256": shard_sha,
    }
