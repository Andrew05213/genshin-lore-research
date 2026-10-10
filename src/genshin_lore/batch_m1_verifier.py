from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from .util import canonical_json, digest_id, sha256_bytes, sha256_text


SOURCE_COMMIT = "26df1dfbdf05a82bbb1d97506859f3e1c40718d8"
SOURCE_TREE = "d891ef1d95fdd6c27a1da8a32ea7a61b483647a6"
SNAPSHOT_ID = f"anime-game-data:7.0:{SOURCE_COMMIT}"
SOURCE_UPSTREAM = "https://github.com/DimbreathBot/AnimeGameData"
FAMILY = "__snapshot_textmap_physical__"
PARSER_VERSION = "batch-m1-textmap-physical-parser-v2"
MAPPING_VERSION = "physical-occurrence-only-v2"
PARSING_CONTRACT_VERSION = "object-container-pair-ordinal-v2"
BUILD_TIMESTAMP = "2026-09-02T00:00:00+00:00"
M1_TABLES = {
    "batch_wave",
    "batch_shard",
    "batch_shard_import",
    "textmap_physical_occurrence",
    "batch_table_digest",
    "batch_table_chunk_digest",
}
PAIR_COLUMNS = (
    "relative_path",
    "locale",
    "shard_tier",
    "pair_ordinal",
    "pointer",
    "raw_key_json",
    "key_text",
    "raw_text_json",
    "raw_text",
    "raw_key_sha256",
    "raw_text_sha256",
    "raw_record_sha256",
)
MIGRATION_0014_APPLIED_AT = "2026-09-01T00:00:00+00:00"
MIGRATION_0015_APPLIED_AT = "2026-09-02T00:00:00+00:00"
CANDIDATE_INCREMENT_LIMIT_BYTES = 4 * 1024**3
EXPECTED_AGGREGATE_LOGICAL_SHA256 = "934b666c252c5a4def295672136902ff187f5bd37af5742bbe2bc88da06d09d0"
MIGRATIONS_ROOT = Path(__file__).resolve().parents[2] / "migrations"
MIGRATION_0014_SHA256 = "abc905d8fcb1d1e1507fa7a1cd4447748bcf42d5dc06454a1aaa1f62566f1e48"
MIGRATION_0015_SHA256 = "be90d86a5363e7af99fed5ebc5780775b9481c3cf6bf58a4acf25fe237b6a18e"
EXPECTED_TEXTMAP_FILES: dict[str, dict[str, Any]] = {
    "TextMap/TextMapCHS.json": {
        "locale": "CHS", "tier": "main", "git_blob_oid": "8c2a31b9e4466bfafb3cf795ae5ec6bb3d456881",
        "pair_count": 605937, "raw_size_bytes": 48771857,
        "raw_sha256": "4fb047433ff43936457dfbfd5fb68ff1dd8f90cd3384de74cdcf13416ec3a607",
        "logical_pair_sha256": "47afe0e09d8e6bbc1b14e594a7ab8ee12d266fa0ede86ac226081c497816c97b",
    },
    "TextMap/TextMap_MediumCHS.json": {
        "locale": "CHS", "tier": "medium", "git_blob_oid": "334f9b24c5ffff6a47a77eaab6adac4f2dcc3bc4",
        "pair_count": 238998, "raw_size_bytes": 18550916,
        "raw_sha256": "5231cb762579e6b2d3a4bd7a427d642afafc3d2983aa9f75269b5ba7b0082d38",
        "logical_pair_sha256": "942f2ee69ec0d4e9fffa45dbb5233c46b46982d795d800d04725a6a106db3733",
    },
    "TextMap/TextMapEN.json": {
        "locale": "EN", "tier": "main", "git_blob_oid": "e4f6094219e7609b8145cd7835034b1717880092",
        "pair_count": 585531, "raw_size_bytes": 54368516,
        "raw_sha256": "640704947a9da215c82352b3dfaf0489169998fc212ad62a2e77f077ecb8af7f",
        "logical_pair_sha256": "6ed90228b637574f1c1d485bea299c8046b2809ac4d144d02e7a3878d36fb04c",
    },
    "TextMap/TextMap_MediumEN.json": {
        "locale": "EN", "tier": "medium", "git_blob_oid": "5ed1e79e923f016dfa7db009390d07dca8f0917a",
        "pair_count": 229324, "raw_size_bytes": 20424271,
        "raw_sha256": "bd93680447c1ca07e2263ff3f7771afb29f0c584473007ade4e4705f6e289bbd",
        "logical_pair_sha256": "b2edd4678bee8da0102255d6f3af602f951d63fc64d1965356be402248cc1283",
    },
    "TextMap/TextMapJP.json": {
        "locale": "JP", "tier": "main", "git_blob_oid": "7ef028f518c0ca9fae530775ed3c85641e6696ea",
        "pair_count": 585353, "raw_size_bytes": 61908087,
        "raw_sha256": "fa6771ebdd8b9866b6d1777aa62c0cad43454e65e936c74a87491f2d9de45167",
        "logical_pair_sha256": "fda7bdc7a7efd27fb264a667b45697028ebc03e9aa614e9818c70d826006f883",
    },
    "TextMap/TextMap_MediumJP.json": {
        "locale": "JP", "tier": "medium", "git_blob_oid": "12485b4ca9f5fc9fa1af41d8786011d4be834990",
        "pair_count": 229382, "raw_size_bytes": 22744816,
        "raw_sha256": "367a332abedf4e10be7212cc7a387fc64e563d276f75e60f3ccd89d33eeadebd",
        "logical_pair_sha256": "8f73db4bf5c3fa316e436a4565a7a9e6f319f69ed14a76732c693d7969f4662b",
    },
}
EXPECTED_TEXTMAP_PATH_ORDER = (
    "TextMap/TextMapCHS.json",
    "TextMap/TextMap_MediumCHS.json",
    "TextMap/TextMapEN.json",
    "TextMap/TextMap_MediumEN.json",
    "TextMap/TextMapJP.json",
    "TextMap/TextMap_MediumJP.json",
)
EXPECTED_BOUNDARY = {
    "corpus_complete": True,
    "coverage": "six_pinned_textmap_physical_pair_closure",
    "evidence_only": True,
    "file_count": 6,
    "pair_count": 2_474_525,
    "product_accepted": False,
    "record_coverage": "complete_for_selected_six_textmap_object_pair_containers",
}
EXPECTED_CONTRACT = {
    "contract_version": "snapshot-textmap-physical-v2",
    "family": "__snapshot_textmap_physical__",
    "logical_identity_created": False,
    "parsing_contract_version": "object-container-pair-ordinal-v2",
    "text_or_hash_merges_occurrences": False,
}


class BatchM1VerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourcePair:
    pair_ordinal: int
    pointer: str
    raw_key_json: str
    key_text: str
    raw_text_json: str
    raw_text: str


@dataclass(frozen=True)
class SourceEvidence:
    raw_json: str
    pair_count: int
    logical_pair_sha256: str
    first_pair: SourcePair
    last_pair: SourcePair
    samples: dict[int, SourcePair]


def _fail(message: str) -> None:
    raise BatchM1VerificationError(message)


def _canonical_bytes(value: Any) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


def _skip_ws(text: str, position: int) -> int:
    while position < len(text) and text[position] in " \t\r\n":
        position += 1
    return position


def _escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def _iter_source_pairs(raw: bytes) -> Iterable[SourcePair]:
    """Independent lexical object-pair reader used by the verifier.

    It intentionally does not import the builder parser. Raw token spelling is
    retained for the bounded samples while the complete source is scanned for
    duplicate keys, ordering and trailing bytes.
    """

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BatchM1VerificationError("independent TextMap JSON parse failed") from exc
    decoder = json.JSONDecoder()
    position = _skip_ws(text, 0)
    if position >= len(text) or text[position] != "{":
        _fail("independent TextMap root is not an object")
    position += 1
    ordinal = 0
    seen: set[str] = set()
    while True:
        position = _skip_ws(text, position)
        if position < len(text) and text[position] == "}":
            position = _skip_ws(text, position + 1)
            if position != len(text):
                _fail("independent TextMap has trailing non-whitespace bytes")
            return
        key_start = position
        try:
            key, key_end = decoder.raw_decode(text, position)
        except json.JSONDecodeError as exc:
            raise BatchM1VerificationError(
                f"independent TextMap key parse failed at pair {ordinal}"
            ) from exc
        if not isinstance(key, str):
            _fail(f"independent TextMap key {ordinal} is not a string")
        raw_key_json = text[key_start:key_end]
        position = _skip_ws(text, key_end)
        if position >= len(text) or text[position] != ":":
            _fail(f"independent TextMap pair {ordinal} has no colon")
        position = _skip_ws(text, position + 1)
        value_start = position
        try:
            value, value_end = decoder.raw_decode(text, position)
        except json.JSONDecodeError as exc:
            raise BatchM1VerificationError(
                f"independent TextMap value parse failed at pair {ordinal}"
            ) from exc
        if not isinstance(value, str):
            _fail(f"independent TextMap value {ordinal} is not a string")
        if key in seen:
            _fail(f"independent TextMap duplicate key at pair {ordinal}: {key!r}")
        seen.add(key)
        raw_text_json = text[value_start:value_end]
        yield SourcePair(
            pair_ordinal=ordinal,
            pointer=f"/@{ordinal}:{_escape(key)}",
            raw_key_json=raw_key_json,
            key_text=key,
            raw_text_json=raw_text_json,
            raw_text=value,
        )
        ordinal += 1
        position = _skip_ws(text, value_end)
        if position >= len(text):
            _fail("independent TextMap object is unterminated")
        if text[position] == ",":
            position += 1
            continue
        if text[position] == "}":
            position = _skip_ws(text, position + 1)
            if position != len(text):
                _fail("independent TextMap has trailing non-whitespace bytes")
            return
        _fail(f"independent TextMap invalid delimiter after pair {ordinal - 1}")


def _source_pair_bytes(
    spec: dict[str, Any], item: SourcePair, *, include_family_fields: bool = False
) -> bytes:
    """Independent canonical row encoding (kept separate from the builder)."""

    values = [spec["relative_path"]]
    if include_family_fields:
        values.extend((spec["locale"], spec["tier"]))
    values.extend([
        item.pair_ordinal,
        item.pointer,
        item.raw_key_json,
        item.key_text,
        item.raw_text_json,
        item.raw_text,
        sha256_text(item.raw_key_json),
        sha256_text(item.raw_text_json),
        sha256_text(f'{{"key":{item.raw_key_json},"text":{item.raw_text_json}}}'),
    ])
    return _canonical_bytes(values)


def _pairs(raw: bytes) -> list[tuple[str, str]]:
    """Compatibility helper retained for focused fixtures."""

    return [(item.key_text, item.raw_text) for item in _iter_source_pairs(raw)]


def _source_evidence(
    source_root: Path,
    spec: dict[str, Any],
    *,
    aggregate_digest: "hashlib._Hash | None" = None,
) -> SourceEvidence:
    raw = (source_root / spec["relative_path"]).read_bytes()
    if len(raw) != spec["raw_size_bytes"] or sha256_bytes(raw) != spec["raw_sha256"]:
        _fail(f"source seal mismatch: {spec['relative_path']}")
    try:
        raw_json = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BatchM1VerificationError(
            f"source container is not UTF-8: {spec['relative_path']}"
        ) from exc
    first: SourcePair | None = None
    last: SourcePair | None = None
    samples: dict[int, SourcePair] = {}
    logical_digest = hashlib.sha256()
    count = 0
    for item in _iter_source_pairs(raw):
        logical_digest.update(_source_pair_bytes(spec, item))
        if aggregate_digest is not None:
            aggregate_digest.update(
                _source_pair_bytes(spec, item, include_family_fields=True)
            )
        if first is None:
            first = item
        if item.pair_ordinal < 3 or item.pair_ordinal % 100_000 == 0:
            samples[item.pair_ordinal] = item
        last = item
        count += 1
    if first is None or last is None:
        _fail(f"source TextMap is empty: {spec['relative_path']}")
    samples[last.pair_ordinal] = last
    if count != spec["pair_count"]:
        _fail(f"source pair count mismatch: {spec['relative_path']}")
    logical_pair_sha256 = logical_digest.hexdigest()
    if logical_pair_sha256 != spec["logical_pair_sha256"]:
        _fail(f"source logical pair digest mismatch: {spec['relative_path']}")
    if _anchor(first) != spec["first_pair"] or _anchor(last) != spec["last_pair"]:
        _fail(f"source first/last anchor mismatch: {spec['relative_path']}")
    return SourceEvidence(raw_json, count, logical_pair_sha256, first, last, samples)


def _anchor(item: SourcePair) -> dict[str, Any]:
    return {
        "key_text": item.key_text,
        "pair_ordinal": item.pair_ordinal,
        "pointer": item.pointer,
        "raw_key_json": item.raw_key_json,
        "raw_text_json": item.raw_text_json,
    }


def _derived_identity(value: dict[str, Any]) -> dict[str, str]:
    without_identity = dict(value)
    without_identity.pop("derived_identity", None)
    config_checksum = sha256_bytes(_canonical_bytes(without_identity))
    parser_build_id = digest_id(
        "pb-batch-m1-textmap",
        "batch-m1-textmap-physical-parser-v2",
        "physical-occurrence-only-v2",
        "object-container-pair-ordinal-v2",
        config_checksum,
    )
    ingest_run_id = digest_id(
        "ir-batch-m1-textmap", SNAPSHOT_ID, parser_build_id, config_checksum
    )
    return {
        "config_checksum": config_checksum,
        "ingest_run_id": ingest_run_id,
        "parser_build_id": parser_build_id,
        "shard_id": digest_id(
            "batch-shard", "batch-wave:m1-textmap-foundation-v2", FAMILY,
            "snapshot-textmap-physical-v2", ingest_run_id,
        ),
        "wave_id": "batch-wave:m1-textmap-foundation-v2",
    }


def _source_git_value(source_root: Path, revision: str) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", revision],
            cwd=source_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise BatchM1VerificationError("independent source git preflight failed") from exc


def _verify_source_git_preflight(source_root: Path, manifest: dict[str, Any]) -> None:
    source = manifest["source"]
    if source.get("upstream") != SOURCE_UPSTREAM:
        _fail("source upstream identity mismatch")
    if _source_git_value(source_root, "HEAD") != SOURCE_COMMIT:
        _fail("source commit does not match fixed manifest pin")
    if _source_git_value(source_root, "HEAD^{tree}") != SOURCE_TREE:
        _fail("source tree does not match fixed manifest pin")
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain=v1"],
            cwd=source_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise BatchM1VerificationError("independent source cleanliness check failed") from exc
    if status:
        _fail("pinned source checkout is dirty")
    for spec in manifest["files"]:
        if _source_git_value(source_root, f"HEAD:{spec['relative_path']}") != spec["git_blob_oid"]:
            _fail(f"source blob mismatch: {spec['relative_path']}")


def _manifest(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    value = json.loads(raw)
    if raw != _canonical_bytes(value):
        _fail("manifest is not canonical JSON")
    if value.get("schema_version") != 2 or value.get(
        "manifest_id"
    ) != "anime-game-data-7.0-batch-m1-textmap-corpus":
        _fail("manifest identity mismatch")
    source = value.get("source", {})
    if (
        source.get("commit") != SOURCE_COMMIT
        or source.get("tree") != SOURCE_TREE
        or source.get("snapshot_id") != SNAPSHOT_ID
    ):
        _fail("manifest source pin mismatch")
    if source.get("upstream") != SOURCE_UPSTREAM:
        _fail("production verifier rejects test-only fixture manifest")
    boundary = value.get("boundary")
    if not isinstance(boundary, dict) or set(boundary) != set(EXPECTED_BOUNDARY):
        _fail("manifest boundary shape mismatch")
    if boundary != EXPECTED_BOUNDARY:
        _fail("manifest fixed TextMap boundary mismatch")
    contract = value.get("contract", {})
    if contract != EXPECTED_CONTRACT:
        _fail("manifest contract or identity policy mismatch")
    files = value.get("files", [])
    if not isinstance(files, list) or len(files) != 6:
        _fail("manifest does not contain exactly six files")
    actual_paths = tuple(item.get("relative_path") for item in files if isinstance(item, dict))
    if actual_paths != EXPECTED_TEXTMAP_PATH_ORDER:
        _fail("manifest TextMap path boundary mismatch")
    if len(set(actual_paths)) != len(actual_paths):
        _fail("manifest TextMap paths are not unique")
    required_file_keys = {
        "first_pair", "git_blob_oid", "last_pair", "locale",
        "logical_pair_sha256", "pair_count", "raw_sha256", "raw_size_bytes",
        "relative_path", "tier",
    }
    for item in files:
        if not isinstance(item, dict) or set(item) != required_file_keys:
            _fail("manifest TextMap file entry shape mismatch")
        relative = item["relative_path"]
        if not isinstance(relative, str) or relative.startswith("/") or ".." in Path(relative).parts:
            _fail("manifest TextMap path is not a relative fixed path")
        fixed = EXPECTED_TEXTMAP_FILES.get(relative)
        if fixed is None or any(item[key] != fixed[key] for key in fixed):
            _fail(f"manifest fixed TextMap seal mismatch: {relative}")
    migration = value.get("migration", {})
    if not isinstance(migration, dict) or set(migration) != {
        "raw_sha256", "relative_path", "target_schema_version",
    } or migration.get("target_schema_version") != 15:
        _fail("manifest migration seal shape mismatch")
    if migration != {
        "raw_sha256": MIGRATION_0015_SHA256,
        "relative_path": "migrations/0015_batch_projection_foundation.sql",
        "target_schema_version": 15,
    }:
        _fail("manifest migration seal mismatch")
    expected = value.get("expected", {})
    if not isinstance(expected, dict) or set(expected) != {"logical_pair_sha256", "pair_count"}:
        _fail("manifest expected seal shape mismatch")
    if expected.get("pair_count") != sum(int(item["pair_count"]) for item in files):
        _fail("manifest pair denominator mismatch")
    if (
        expected["pair_count"] != sum(item["pair_count"] for item in EXPECTED_TEXTMAP_FILES.values())
        or expected["logical_pair_sha256"] != EXPECTED_AGGREGATE_LOGICAL_SHA256
    ):
        _fail("manifest aggregate TextMap seal mismatch")
    if value.get("derived_identity") != _derived_identity(value):
        _fail("manifest derived identity mismatch")
    return value, sha256_bytes(raw)


def _load_source_evidence(
    source_root: Path, manifest: dict[str, Any]
) -> tuple[dict[str, SourceEvidence], str]:
    """Scan every selected file once and independently produce all seals."""

    _verify_source_git_preflight(source_root, manifest)
    aggregate = hashlib.sha256()
    evidence: dict[str, SourceEvidence] = {}
    # The aggregate contract is canonicalized by relative path, independently
    # of the manifest's locale-grouped declaration order.
    for spec in sorted(manifest["files"], key=lambda item: item["relative_path"]):
        evidence[spec["relative_path"]] = _source_evidence(
            source_root, spec, aggregate_digest=aggregate
        )
    aggregate_sha256 = aggregate.hexdigest()
    if aggregate_sha256 != manifest["expected"]["logical_pair_sha256"]:
        _fail("independent aggregate TextMap logical digest mismatch")
    if manifest["source"].get("upstream") == SOURCE_UPSTREAM and aggregate_sha256 != EXPECTED_AGGREGATE_LOGICAL_SHA256:
        _fail("fixed aggregate TextMap logical digest mismatch")
    return evidence, aggregate_sha256


def _logical_digest(rows: Iterable[Sequence[Any]]) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        digest.update(_canonical_bytes(list(row)))
        count += 1
    return count, digest.hexdigest()


def _digest_rows(
    rows: Iterable[Sequence[Any]],
    *,
    chunk_size: int = 50_000,
    key_indexes: tuple[int, ...] = (0, 3),
) -> tuple[int, str, list[dict[str, Any]]]:
    full = hashlib.sha256()
    chunk = hashlib.sha256()
    chunks: list[dict[str, Any]] = []
    count = 0
    first_key: list[Any] | None = None
    last_key: list[Any] | None = None
    in_chunk = 0
    for row in rows:
        values = list(row)
        raw = _canonical_bytes(values)
        full.update(raw)
        chunk.update(raw)
        key = [values[index] for index in key_indexes]
        if first_key is None:
            first_key = key
        last_key = key
        count += 1
        in_chunk += 1
        if in_chunk == chunk_size:
            chunks.append(
                {
                    "first_key": first_key,
                    "last_key": last_key,
                    "logical_row_sha256": chunk.hexdigest(),
                    "row_count": in_chunk,
                }
            )
            chunk = hashlib.sha256()
            first_key = None
            last_key = None
            in_chunk = 0
    if in_chunk:
        chunks.append(
            {
                "first_key": first_key,
                "last_key": last_key,
                "logical_row_sha256": chunk.hexdigest(),
                "row_count": in_chunk,
            }
        )
    return count, full.hexdigest(), chunks


def _digest_rows_by_path(
    rows: Iterable[Sequence[Any]],
) -> dict[str, tuple[int, str]]:
    digests: dict[str, tuple[int, hashlib._Hash]] = {}
    for row in rows:
        path = str(row[0])
        digest = digests.setdefault(path, (0, hashlib.sha256()))
        digest[1].update(_canonical_bytes(list(row)))
        digests[path] = (digest[0] + 1, digest[1])
    return {path: (count, digest.hexdigest()) for path, (count, digest) in digests.items()}


def _sample_check(
    row: sqlite3.Row,
    item: SourcePair,
    *,
    candidate: bool,
    expected_id: str | None = None,
) -> None:
    pointer = item.pointer
    if (
        int(row["pair_ordinal"]) != item.pair_ordinal
        or row["source_pointer"] != pointer
        or row["source_record_pointer"] != "/"
        or row["key_text"] != item.key_text
        or row["raw_key_json"] != item.raw_key_json
        or row["raw_text_json"] != item.raw_text_json
        or row["raw_text"] != item.raw_text
    ):
        _fail("physical pair sample mismatch")
    if candidate and expected_id is not None and row["id"] != expected_id:
        _fail("candidate occurrence identity mismatch")
    if json.loads(row["raw_key_json"]) != item.key_text or json.loads(
        row["raw_text_json"]
    ) != item.raw_text:
        _fail("lexical sample does not decode to source pair")
    raw_record = f'{{"key":{row["raw_key_json"]},"text":{row["raw_text_json"]}}}'
    if (
        sha256_text(row["raw_key_json"]) != row["raw_key_sha256"]
        or sha256_text(row["raw_text_json"]) != row["raw_text_sha256"]
        or sha256_text(raw_record) != row["raw_record_sha256"]
    ):
        _fail("lexical sample checksum mismatch")


def _index_columns(connection: sqlite3.Connection, table: str) -> list[tuple[str, bool, tuple[str, ...]]]:
    result: list[tuple[str, bool, tuple[str, ...]]] = []
    escaped = table.replace('"', '""')
    for row in connection.execute(f'PRAGMA index_list("{escaped}")'):
        index_name = str(row[1])
        columns = tuple(
            str(item[2])
            for item in connection.execute(
                f'PRAGMA index_info("{index_name.replace(chr(34), chr(34) * 2)}")'
            )
        )
        result.append((index_name, bool(row[2]), columns))
    return result


def _check_v2_indexes(connection: sqlite3.Connection) -> None:
    indexes = _index_columns(connection, "textmap_physical_occurrence")
    forbidden = {
        ("ingest_run_id", "source_record_id"),
        ("id", "ingest_run_id", "snapshot_id", "parser_build_id"),
    }
    if any(name == "idx_textmap_physical_run_file_pair" for name, _unique, _columns in indexes):
        _fail("v2 occurrence table retains redundant run/file/pair index")
    if any(unique and columns in forbidden for _name, unique, columns in indexes):
        _fail("v2 occurrence table retains per-pair source_record uniqueness")


def _split_sql(raw: bytes) -> tuple[str, ...]:
    try:
        script = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BatchM1VerificationError("migration SQL is not UTF-8") from exc
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            buffer = ""
            if statement:
                statements.append(statement)
    if buffer.strip():
        _fail("migration SQL has an incomplete statement")
    return tuple(statements)


def _normalized_sql(sql: str) -> str:
    without_comments = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return " ".join(without_comments.split()).rstrip(";").casefold()


def _migration_objects(raw: bytes) -> dict[tuple[str, str], str]:
    objects: dict[tuple[str, str], str] = {}
    pattern = re.compile(
        r"\bCREATE\s+(TABLE|INDEX|TRIGGER)\s+"
        r"(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
        flags=re.IGNORECASE,
    )
    for statement in _split_sql(raw):
        match = pattern.search(statement)
        if match is None:
            continue
        key = (match.group(1).casefold(), match.group(2))
        if key in objects:
            _fail(f"duplicate migration object declaration: {key[1]}")
        objects[key] = _normalized_sql(statement)
    return objects


def _migration_fk_inventory(raws: Iterable[bytes]) -> dict[str, tuple[tuple[Any, ...], ...]]:
    """Execute only the pinned DDL in memory to obtain its FK oracle."""

    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE source_snapshot(id TEXT PRIMARY KEY);
        CREATE TABLE source_file(
            id TEXT PRIMARY KEY, snapshot_id TEXT,
            UNIQUE(id, snapshot_id)
        );
        CREATE TABLE parser_build(id TEXT PRIMARY KEY);
        CREATE TABLE ingest_run(
            id TEXT PRIMARY KEY, snapshot_id TEXT, parser_build_id TEXT,
            UNIQUE(id, snapshot_id, parser_build_id)
        );
        CREATE TABLE source_record(
            id TEXT PRIMARY KEY, source_file_id TEXT, pointer TEXT,
            UNIQUE(id, source_file_id, pointer)
        );
        """
    )
    for raw in raws:
        connection.executescript(raw.decode("utf-8-sig"))
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND (name LIKE 'avatar_codex_%' OR name LIKE 'batch_%' "
            "OR name='textmap_physical_occurrence')"
        )
    }
    result: dict[str, tuple[tuple[Any, ...], ...]] = {}
    for table in tables:
        quoted = table.replace('"', '""')
        result[table] = tuple(
            sorted(
                tuple(row)
                for row in connection.execute(f'PRAGMA foreign_key_list("{quoted}")')
            )
        )
    connection.close()
    return result


def _migration_oracle(connection: sqlite3.Connection, manifest: dict[str, Any]) -> dict[str, Any]:
    """Check pinned migration bytes and independently derive DDL/FK inventory."""

    paths = (
        MIGRATIONS_ROOT / "0014_phase4g2_avatar_codex_canary_evidence.sql",
        MIGRATIONS_ROOT / "0015_batch_projection_foundation.sql",
    )
    expected_hashes = (MIGRATION_0014_SHA256, MIGRATION_0015_SHA256)
    raws: list[bytes] = []
    expected_objects: dict[tuple[str, str], str] = {}
    for path, expected_hash in zip(paths, expected_hashes, strict=True):
        raw = path.read_bytes()
        if sha256_bytes(raw) != expected_hash:
            _fail(f"migration raw SQL seal mismatch: {path.name}")
        raws.append(raw)
        expected_objects.update(_migration_objects(raw))
    migration = manifest["migration"]
    if migration["relative_path"] != "migrations/0015_batch_projection_foundation.sql" or migration["raw_sha256"] != MIGRATION_0015_SHA256:
        _fail("manifest 0015 migration binding mismatch")

    actual_objects: dict[tuple[str, str], str] = {}
    for row in connection.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
    ):
        object_type, name, sql = str(row[0]), str(row[1]), row[2]
        key = (object_type, name)
        if key in expected_objects:
            actual_objects[key] = _normalized_sql(str(sql))
    if set(actual_objects) != set(expected_objects):
        _fail("candidate migration DDL object inventory mismatch")
    for key, expected_sql in expected_objects.items():
        if actual_objects[key] != expected_sql:
            _fail(f"candidate migration DDL mismatch: {key[1]}")

    new_tables = {
        name for kind, name in expected_objects if kind == "table"
    }
    expected_object_names = {name for _kind, name in expected_objects}
    actual_prefixed = {
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            "SELECT type,name,tbl_name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        )
        if str(row[1]) in expected_object_names or str(row[2]) in new_tables
    }
    if actual_prefixed != set(expected_objects):
        _fail("candidate migration prefixed object inventory has unexpected objects")

    expected_fks = _migration_fk_inventory(raws)
    for table, wanted in expected_fks.items():
        quoted = table.replace('"', '""')
        actual = tuple(
            sorted(
                tuple(row)
                for row in connection.execute(f'PRAGMA foreign_key_list("{quoted}")')
            )
        )
        if actual != wanted:
            _fail(f"candidate migration foreign-key inventory mismatch: {table}")

    expected_indexes: dict[str, set[str]] = {}
    expected_columns: dict[str, tuple[str, ...]] = {}
    for (kind, name), sql in expected_objects.items():
        if kind != "index":
            continue
        match = re.search(r"\bon\s+([a-z_][a-z0-9_]*)\s*\(([^)]*)\)", sql)
        if match is None:
            _fail(f"migration index declaration cannot be parsed: {name}")
        table = match.group(1)
        columns = tuple(item.strip().strip('"`[]') for item in match.group(2).split(","))
        expected_indexes.setdefault(table, set()).add(name)
        expected_columns[name] = columns
    for table in expected_indexes:
        quoted = table.replace('"', '""')
        actual_named = {
            str(row[1])
            for row in connection.execute(f'PRAGMA index_list("{quoted}")')
            if not str(row[1]).startswith("sqlite_autoindex_")
        }
        if actual_named != expected_indexes[table]:
            _fail(f"candidate migration index inventory mismatch: {table}")
        for name in actual_named:
            actual_columns = tuple(
                str(row[2])
                for row in connection.execute(
                    f'PRAGMA index_info("{name.replace(chr(34), chr(34) * 2)}")'
                )
            )
            if actual_columns != expected_columns[name]:
                _fail(f"candidate migration index columns mismatch: {name}")
    return {
        "migration_sha256": {path.name: expected for path, expected in zip(paths, expected_hashes, strict=True)},
        "object_count": len(expected_objects),
        "fk_table_count": len(expected_fks),
        "index_table_count": len(expected_indexes),
    }


AVATAR_CODEX_TABLES = {
    "avatar_codex_row_occurrence",
    "avatar_codex_raw_field_occurrence",
    "avatar_codex_relationship_occurrence",
    "avatar_codex_relationship_candidate_occurrence",
    "avatar_codex_opaque_occurrence",
}
PROVENANCE_TABLES = {"schema_migration", "parser_build", "ingest_run", "source_file", "source_record"}


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _user_tables(connection: sqlite3.Connection, schema: str = "main") -> tuple[str, ...]:
    quoted_schema = _quoted_identifier(schema)
    return tuple(
        str(row[0])
        for row in connection.execute(
            f"SELECT name FROM {quoted_schema}.sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    )


def _table_columns(
    connection: sqlite3.Connection, schema: str, table: str
) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        tuple(row)
        for row in connection.execute(
            f"PRAGMA {_quoted_identifier(schema)}.table_info({_quoted_identifier(table)})"
        )
    )


def _rowset_difference_exists(
    connection: sqlite3.Connection,
    left_schema: str,
    right_schema: str,
    table: str,
    columns: tuple[tuple[Any, ...], ...],
) -> bool:
    names = ",".join(_quoted_identifier(str(row[1])) for row in columns)
    query = (
        "SELECT 1 FROM (SELECT " + names + " FROM "
        + f"{_quoted_identifier(left_schema)}.{_quoted_identifier(table)} EXCEPT SELECT {names} FROM "
        + f"{_quoted_identifier(right_schema)}.{_quoted_identifier(table)}) LIMIT 1"
    )
    return connection.execute(query).fetchone() is not None


def _baseline_preservation_oracle(
    connection: sqlite3.Connection,
    baseline: Path,
    identity: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Independently prove Schema 13 preservation and exact provenance delta."""

    baseline_uri = baseline.resolve().as_uri() + "?mode=ro&immutable=1"
    try:
        connection.execute("ATTACH DATABASE ? AS baseline", (baseline_uri,))
    except sqlite3.DatabaseError as exc:
        _fail(f"cannot attach accepted Schema 13 baseline: {exc}")
    try:
        baseline_tables = _user_tables(connection, "baseline")
        candidate_tables = _user_tables(connection, "main")
        expected_new_tables = AVATAR_CODEX_TABLES | M1_TABLES
        if set(candidate_tables) != set(baseline_tables) | expected_new_tables:
            _fail("candidate Schema 13 preservation table inventory mismatch")
        protected: dict[str, dict[str, int]] = {}
        for table in baseline_tables:
            if table in PROVENANCE_TABLES:
                continue
            columns = _table_columns(connection, "baseline", table)
            if columns != _table_columns(connection, "main", table):
                _fail(f"Schema 13 protected table shape changed: {table}")
            before = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM baseline.{_quoted_identifier(table)}"
                ).fetchone()[0]
            )
            after = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM main.{_quoted_identifier(table)}"
                ).fetchone()[0]
            )
            if before != after:
                _fail(f"Schema 13 protected table count changed: {table}")
            if _rowset_difference_exists(connection, "main", "baseline", table, columns):
                _fail(f"Schema 13 protected rows changed: {table}")
            if _rowset_difference_exists(connection, "baseline", "main", table, columns):
                _fail(f"Schema 13 protected rows were removed: {table}")
            protected[table] = {"baseline_rows": before, "candidate_rows": after}

        file_ids = {
            spec["relative_path"]: digest_id("sf", SNAPSHOT_ID, spec["relative_path"])
            for spec in manifest["files"]
        }
        expected_added_ids = {
            "parser_build": {identity["parser_build_id"]},
            "ingest_run": {identity["ingest_run_id"]},
            "source_file": set(file_ids.values()),
            "source_record": {
                digest_id("sr", file_id, "/") for file_id in file_ids.values()
            },
        }
        provenance: dict[str, dict[str, int]] = {}
        for table in sorted(PROVENANCE_TABLES - {"schema_migration"}):
            columns = _table_columns(connection, "baseline", table)
            if columns != _table_columns(connection, "main", table):
                _fail(f"Schema 13 provenance table shape changed: {table}")
            if _rowset_difference_exists(connection, "baseline", "main", table, columns):
                _fail(f"Schema 13 provenance rows were removed: {table}")
            base_ids = {
                str(row[0])
                for row in connection.execute(
                    f"SELECT id FROM baseline.{_quoted_identifier(table)}"
                )
            }
            candidate_added = {
                str(row[0])
                for row in connection.execute(
                    f"SELECT id FROM main.{_quoted_identifier(table)} EXCEPT "
                    f"SELECT id FROM baseline.{_quoted_identifier(table)}"
                )
            }
            wanted = expected_added_ids[table] - base_ids
            if candidate_added != wanted:
                _fail(f"unexpected Schema 13 provenance additions: {table}")
            provenance[table] = {
                "baseline_rows": int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM baseline.{_quoted_identifier(table)}"
                    ).fetchone()[0]
                ),
                "candidate_rows": int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM main.{_quoted_identifier(table)}"
                    ).fetchone()[0]
                ),
                "added_rows": len(candidate_added),
            }

        migration_columns = _table_columns(connection, "baseline", "schema_migration")
        if migration_columns != _table_columns(connection, "main", "schema_migration"):
            _fail("Schema 13 schema_migration shape changed")
        if _rowset_difference_exists(connection, "baseline", "main", "schema_migration", migration_columns):
            _fail("Schema 13 migration ledger rows were removed")
        migration_added = [
            tuple(row)
            for row in connection.execute(
                "SELECT version,applied_at FROM main.schema_migration EXCEPT "
                "SELECT version,applied_at FROM baseline.schema_migration "
                "ORDER BY version,applied_at"
            )
        ]
        if migration_added != [
            (14, MIGRATION_0014_APPLIED_AT),
            (15, MIGRATION_0015_APPLIED_AT),
        ]:
            _fail("Schema 13 migration ledger delta is not exact 0014/0015")
        provenance["schema_migration"] = {
            "baseline_rows": int(
                connection.execute("SELECT COUNT(*) FROM baseline.schema_migration").fetchone()[0]
            ),
            "candidate_rows": int(
                connection.execute("SELECT COUNT(*) FROM main.schema_migration").fetchone()[0]
            ),
            "added_rows": len(migration_added),
        }
        return {
            "baseline_user_table_count": len(baseline_tables),
            "candidate_user_table_count": len(candidate_tables),
            "protected_exact_table_count": len(protected),
            "protected_exact_tables": protected,
            "provenance_tables": provenance,
            "expected_new_tables": sorted(expected_new_tables),
        }
    finally:
        connection.execute("DETACH DATABASE baseline")


def verify_shard(shard: Path, manifest_path: Path, source_root: Path) -> dict[str, Any]:
    manifest, manifest_sha = _manifest(manifest_path)
    evidence, source_aggregate_sha256 = _load_source_evidence(source_root, manifest)
    connection = sqlite3.connect(f"file:{shard.as_posix()}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if tables != {
            "shard_metadata",
            "textmap_source_container",
            "textmap_pair_occurrence",
            "table_digest",
            "chunk_digest",
        }:
            _fail("standalone shard table inventory mismatch")
        metadata = dict(connection.execute("SELECT key,value FROM shard_metadata"))
        if (
            metadata.get("manifest_sha256") != manifest_sha
            or int(metadata.get("row_count", -1)) != manifest["expected"]["pair_count"]
            or int(metadata.get("container_count", -1)) != len(manifest["files"])
            or metadata.get("schema_version") != "2"
        ):
            _fail("standalone shard metadata mismatch")
        if source_aggregate_sha256 != manifest["expected"]["logical_pair_sha256"]:
            _fail("standalone source aggregate seal mismatch")
        container_rows = connection.execute(
            "SELECT relative_path,locale,shard_tier,source_pointer,raw_json,raw_sha256 "
            "FROM textmap_source_container ORDER BY relative_path"
        ).fetchall()
        if len(container_rows) != len(manifest["files"]):
            _fail("standalone source-container row count mismatch")
        containers = {str(row["relative_path"]): row for row in container_rows}
        expected_paths = tuple(sorted(item["relative_path"] for item in manifest["files"]))
        if tuple(containers) != expected_paths:
            _fail("standalone shard source-container inventory mismatch")
        for spec in manifest["files"]:
            container = containers[spec["relative_path"]]
            source = evidence[spec["relative_path"]]
            if (
                container["source_pointer"] != "/"
                or container["locale"] != spec["locale"]
                or container["shard_tier"] != spec["tier"]
                or container["raw_json"] != source.raw_json
                or container["raw_sha256"] != spec["raw_sha256"]
            ):
                _fail("standalone source-container lexical seal mismatch")
        pair_count = int(
            connection.execute("SELECT COUNT(*) FROM textmap_pair_occurrence").fetchone()[0]
        )
        if pair_count != manifest["expected"]["pair_count"]:
            _fail("standalone pair denominator mismatch")
        duplicate_keys = int(
            connection.execute(
                "SELECT COUNT(*) FROM ("
                "SELECT relative_path,key_text FROM textmap_pair_occurrence "
                "GROUP BY relative_path,key_text HAVING COUNT(*)>1)"
            ).fetchone()[0]
        )
        if duplicate_keys:
            _fail("standalone pair keys are not unique within a physical container")
        equal_text_groups = int(
            connection.execute(
                "SELECT COUNT(*) FROM ("
                "SELECT raw_text FROM textmap_pair_occurrence "
                "GROUP BY raw_text HAVING COUNT(*)>1)"
            ).fetchone()[0]
        )
        bad_coordinates = int(
            connection.execute(
                "SELECT COUNT(*) FROM textmap_pair_occurrence "
                "WHERE source_record_pointer<> '/' OR pair_ordinal<0 "
                "OR substr(pointer,1,2)<> '/@'"
            ).fetchone()[0]
        )
        if bad_coordinates:
            _fail("standalone pair coordinate constraints failed")
        rows = connection.execute(
            "SELECT " + ",".join(PAIR_COLUMNS)
            + " FROM textmap_pair_occurrence ORDER BY relative_path,pair_ordinal"
        )
        count, digest, chunks = _digest_rows(rows, key_indexes=(0, 3))
        if count != pair_count or digest != manifest["expected"]["logical_pair_sha256"]:
            _fail("standalone logical rowset digest mismatch")
        table_digest = connection.execute(
            "SELECT row_count,logical_row_sha256,chunk_size,chunk_count "
            "FROM table_digest WHERE table_name='textmap_pair_occurrence'"
        ).fetchone()
        if table_digest is None or tuple(table_digest) != (
            count,
            digest,
            50_000,
            len(chunks),
        ):
            _fail("standalone table digest ledger mismatch")
        stored_chunks = [
            tuple(row)
            for row in connection.execute(
                "SELECT chunk_ordinal,first_key_json,last_key_json,row_count,logical_row_sha256 "
                "FROM chunk_digest WHERE table_name='textmap_pair_occurrence' ORDER BY chunk_ordinal"
            )
        ]
        expected_chunks = [
            (
                index,
                canonical_json(item["first_key"]),
                canonical_json(item["last_key"]),
                item["row_count"],
                item["logical_row_sha256"],
            )
            for index, item in enumerate(chunks)
        ]
        if stored_chunks != expected_chunks:
            _fail("standalone chunk digest ledger mismatch")
        for spec in manifest["files"]:
            source = evidence[spec["relative_path"]]
            for ordinal, item in source.samples.items():
                row = connection.execute(
                    "SELECT *,pointer AS source_pointer FROM textmap_pair_occurrence "
                    "WHERE relative_path=? AND pair_ordinal=?",
                    (spec["relative_path"], ordinal),
                ).fetchone()
                if row is None:
                    _fail("standalone physical pair sample is missing")
                _sample_check(row, item, candidate=False)
        return {
            "equal_text_groups": equal_text_groups,
            "pair_count": count,
            "rowset_sha256": digest,
            "status": "verified",
        }
    finally:
        connection.close()


def _candidate_projection_rows(connection: sqlite3.Connection, run_id: str) -> Iterable[sqlite3.Row]:
    return connection.execute(
        "SELECT f.relative_path,o.locale,o.shard_tier,o.pair_ordinal,o.source_pointer,"
        "o.raw_key_json,o.key_text,o.raw_text_json,o.raw_text,o.raw_key_sha256,"
        "o.raw_text_sha256,o.raw_record_sha256 "
        "FROM textmap_physical_occurrence o "
        "JOIN source_file f ON f.id=o.source_file_id "
        "WHERE o.ingest_run_id=? AND f.snapshot_id=? "
        "ORDER BY f.relative_path,o.pair_ordinal",
        (run_id, SNAPSHOT_ID),
    )


def _verify_all_occurrence_ids(
    connection: sqlite3.Connection,
    run_id: str,
    expected_count: int,
) -> int:
    """Recompute every physical occurrence ID without trusting builder output."""

    count = 0
    rows = connection.execute(
        "SELECT id,source_file_id,pair_ordinal "
        "FROM textmap_physical_occurrence "
        "WHERE ingest_run_id=? ORDER BY source_file_id,pair_ordinal",
        (run_id,),
    )
    for row in rows:
        expected = digest_id(
            "textmap-physical",
            run_id,
            str(row["source_file_id"]),
            int(row["pair_ordinal"]),
        )
        if str(row["id"]) != expected:
            _fail(
                "candidate physical occurrence ID mismatch at "
                f"{row['source_file_id']}:{row['pair_ordinal']}"
            )
        count += 1
    if count != expected_count:
        _fail("candidate physical occurrence ID oracle count mismatch")
    return count


def _verify_occurrence_run_closure(
    connection: sqlite3.Connection,
    run_id: str,
    expected_count: int,
) -> int:
    """Require the entire occurrence table to belong to one exact run."""

    total = int(
        connection.execute(
            "SELECT COUNT(*) FROM textmap_physical_occurrence"
        ).fetchone()[0]
    )
    if total != expected_count:
        _fail("candidate occurrence table total count mismatch")
    run_ids = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT DISTINCT ingest_run_id "
            "FROM textmap_physical_occurrence ORDER BY ingest_run_id"
        )
    )
    if run_ids != (run_id,):
        _fail("candidate occurrence table contains foreign-run rows")
    foreign_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM textmap_physical_occurrence "
            "WHERE ingest_run_id<>?",
            (run_id,),
        ).fetchone()[0]
    )
    if foreign_count:
        _fail("candidate occurrence table contains foreign-run rows")
    return total


def verify_candidate(
    candidate: Path,
    manifest_path: Path,
    source_root: Path,
    shard: Path,
    baseline: Path,
) -> dict[str, Any]:
    manifest, manifest_sha = _manifest(manifest_path)
    identity = manifest["derived_identity"]
    evidence, source_aggregate_sha256 = _load_source_evidence(source_root, manifest)
    if (
        baseline.stat().st_size != 1_723_596_800
        or _file_sha256(baseline)
        != "7ff8163b6007a41b0827af05fbcf996353617c52f354a131431da912712e58be"
    ):
        _fail("accepted Schema 13 baseline seal mismatch")
    increment = candidate.stat().st_size - baseline.stat().st_size
    if increment > CANDIDATE_INCREMENT_LIMIT_BYTES:
        _fail("candidate increment exceeds the 4 GiB hard limit")
    connection = sqlite3.connect(f"file:{candidate.as_posix()}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        migration_oracle = _migration_oracle(connection, manifest)
        if source_aggregate_sha256 != manifest["expected"]["logical_pair_sha256"]:
            _fail("candidate source aggregate seal mismatch")
        versions = [
            int(row[0])
            for row in connection.execute(
                "SELECT version FROM schema_migration ORDER BY version"
            )
        ]
        if versions != list(range(1, 16)):
            _fail("candidate migration ledger is not exact 1..15")
        ledger = {
            int(row[0]): str(row[1])
            for row in connection.execute(
                "SELECT version,applied_at FROM schema_migration WHERE version IN (14,15)"
            )
        }
        if ledger != {14: MIGRATION_0014_APPLIED_AT, 15: MIGRATION_0015_APPLIED_AT}:
            _fail("candidate migration ledger timestamps are not pinned")
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not M1_TABLES.issubset(tables):
            _fail("candidate is missing M1 tables")
        for table in (
            "avatar_codex_row_occurrence",
            "avatar_codex_raw_field_occurrence",
            "avatar_codex_relationship_occurrence",
            "avatar_codex_relationship_candidate_occurrence",
            "avatar_codex_opaque_occurrence",
        ):
            if table in tables and int(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            ):
                _fail("candidate unexpectedly imports Schema 14 canary rows")
        _check_v2_indexes(connection)
        for table in ("batch_wave", "batch_shard", "batch_shard_import", "batch_table_digest"):
            if int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) != 1:
                _fail(f"candidate M1 metadata inventory mismatch: {table}")
        if int(connection.execute("SELECT COUNT(*) FROM batch_table_chunk_digest").fetchone()[0]) <= 0:
            _fail("candidate M1 chunk digest inventory is empty")
        parser = connection.execute(
            "SELECT parser_version,mapping_version,fingerprint,config_checksum,created_at "
            "FROM parser_build WHERE id=?",
            (identity["parser_build_id"],),
        ).fetchone()
        if parser is None or tuple(parser) != (
            PARSER_VERSION,
            MAPPING_VERSION,
            sha256_text(PARSER_VERSION + "\n" + PARSING_CONTRACT_VERSION),
            identity["config_checksum"],
            BUILD_TIMESTAMP,
        ):
            _fail("candidate parser_build identity mismatch")
        snapshot = connection.execute(
            'SELECT source_id,release_label,build,"commit",tree,status FROM source_snapshot WHERE id=?',
            (SNAPSHOT_ID,),
        ).fetchone()
        if snapshot is None or tuple(snapshot) != (
            "anime-game-data",
            "7.0",
            "CNRELWin7.0.0_R47482070_S47579390_D47579390",
            SOURCE_COMMIT,
            SOURCE_TREE,
            "active",
        ):
            _fail("candidate parser-independent source_snapshot identity mismatch")
        run = connection.execute(
            "SELECT snapshot_id,parser_build_id,scope_json,config_json,status,created_at "
            "FROM ingest_run WHERE id=?",
            (identity["ingest_run_id"],),
        ).fetchone()
        expected_scope = canonical_json({
            "files": [item["relative_path"] for item in manifest["files"]],
            "scope": "six pinned TextMap physical pair containers",
        })
        expected_config = canonical_json({
            "manifest_sha256": manifest_sha,
            "parsing_contract_version": PARSING_CONTRACT_VERSION,
        })
        if run is None or tuple(run) != (
            SNAPSHOT_ID,
            identity["parser_build_id"],
            expected_scope,
            expected_config,
            "complete",
            BUILD_TIMESTAMP,
        ):
            _fail("candidate exact TextMap run mismatch")
        wave = connection.execute(
            "SELECT snapshot_id,contract_version,manifest_sha256,target_schema_version,"
            "status,metadata_json FROM batch_wave WHERE id=?",
            (identity["wave_id"],),
        ).fetchone()
        expected_wave_metadata = {
            "coverage_level": "raw_physical",
            "product_accepted": False,
            "source_record_granularity": "one_object_container_per_file",
        }
        if (
            wave is None
            or tuple(wave[:5])
            != (SNAPSHOT_ID, "batch-wave-v2", manifest_sha, 15, "complete")
            or json.loads(wave["metadata_json"]) != expected_wave_metadata
        ):
            _fail("candidate batch wave binding mismatch")
        shard_row = connection.execute(
            "SELECT wave_id,family,family_contract_version,ingest_run_id,manifest_sha256,"
            "shard_schema_version,read_set_json,write_set_json,rowset_sha256,status,metadata_json "
            "FROM batch_shard WHERE id=?",
            (identity["shard_id"],),
        ).fetchone()
        if shard_row is None:
            _fail("candidate batch shard row is missing")
        if (
            tuple(shard_row[:6])
            != (
                identity["wave_id"],
                FAMILY,
                "snapshot-textmap-physical-v2",
                identity["ingest_run_id"],
                manifest_sha,
                1,
            )
            or json.loads(shard_row["read_set_json"]) != ["source_snapshot", "source_file"]
            or json.loads(shard_row["write_set_json"])
            != ["source_record", "textmap_physical_occurrence"]
            or shard_row["rowset_sha256"] != manifest["expected"]["logical_pair_sha256"]
            or shard_row["status"] != "imported"
        ):
            _fail("candidate batch shard binding mismatch")
        shard_metadata = json.loads(shard_row["metadata_json"])
        if (
            shard_metadata.get("logical_identity_created") is not False
            or not isinstance(shard_metadata.get("cache_key"), str)
        ):
            _fail("candidate batch shard metadata policy mismatch")
        file_ids: dict[str, str] = {}
        containers: dict[str, sqlite3.Row] = {}
        for spec in manifest["files"]:
            file_row = connection.execute(
                "SELECT id,sha256,size_bytes FROM source_file "
                "WHERE snapshot_id=? AND relative_path=?",
                (SNAPSHOT_ID, spec["relative_path"]),
            ).fetchone()
            if file_row is None or (
                file_row["sha256"],
                file_row["size_bytes"],
            ) != (spec["raw_sha256"], spec["raw_size_bytes"]):
                _fail(f"candidate source_file mismatch: {spec['relative_path']}")
            file_ids[spec["relative_path"]] = str(file_row["id"])
            container = connection.execute(
                "SELECT id,pointer,record_kind,native_id,raw_json,raw_sha256 "
                "FROM source_record WHERE source_file_id=? AND pointer='/'",
                (str(file_row["id"]),),
            ).fetchone()
            source = evidence[spec["relative_path"]]
            if (
                container is None
                or container["pointer"] != "/"
                or container["record_kind"] != "textmap_object_container"
                or container["native_id"] is not None
                or container["raw_json"] != source.raw_json
                or container["raw_sha256"] != spec["raw_sha256"]
            ):
                _fail("candidate source_record container lexical binding mismatch")
            containers[spec["relative_path"]] = container
            count_row = connection.execute(
                "SELECT COUNT(*),COUNT(DISTINCT pair_ordinal),"
                "MIN(pair_ordinal),MAX(pair_ordinal),"
                "COUNT(DISTINCT source_record_id),"
                "SUM(source_record_pointer<> '/'),"
                "SUM(substr(source_pointer,1,2)<> '/@') "
                "FROM textmap_physical_occurrence "
                "WHERE ingest_run_id=? AND source_file_id=?",
                (identity["ingest_run_id"], file_row["id"]),
            ).fetchone()
            count, distinct_ordinals, minimum, maximum, records, bad_record, bad_pointer = count_row
            if (
                count != source.pair_count
                or distinct_ordinals != source.pair_count
                or minimum != 0
                or maximum != source.pair_count - 1
                or records != 1
                or bad_record not in (0, None)
                or bad_pointer not in (0, None)
            ):
                _fail("candidate physical occurrence denominator/coordinate mismatch")
            mismatch = connection.execute(
                "SELECT COUNT(*) FROM textmap_physical_occurrence "
                "WHERE ingest_run_id=? AND source_file_id=? AND source_record_id<>? ",
                (identity["ingest_run_id"], file_row["id"], container["id"]),
            ).fetchone()[0]
            if mismatch:
                _fail("candidate pair is not bound to its object-container source_record")
            duplicate_keys = connection.execute(
                "SELECT COUNT(*) FROM (SELECT key_text FROM textmap_physical_occurrence "
                "WHERE ingest_run_id=? AND source_file_id=? "
                "GROUP BY key_text HAVING COUNT(*)>1)",
                (identity["ingest_run_id"], file_row["id"]),
            ).fetchone()[0]
            if duplicate_keys:
                _fail("candidate pair keys are not unique within a physical container")
        allowed_file_ids = tuple(file_ids.values())
        placeholders = ",".join("?" for _ in allowed_file_ids)
        extras = connection.execute(
            "SELECT COUNT(*) FROM textmap_physical_occurrence "
            "WHERE ingest_run_id=? AND source_file_id NOT IN (" + placeholders + ")",
            (identity["ingest_run_id"], *allowed_file_ids),
        ).fetchone()[0]
        if extras:
            _fail("candidate contains out-of-manifest occurrence rows")
        total = _verify_occurrence_run_closure(
            connection,
            identity["ingest_run_id"],
            manifest["expected"]["pair_count"],
        )
        occurrence_id_oracle_count = _verify_all_occurrence_ids(
            connection,
            identity["ingest_run_id"],
            total,
        )
        equal_text_groups = int(
            connection.execute(
                "SELECT COUNT(*) FROM (SELECT raw_text FROM textmap_physical_occurrence "
                "WHERE ingest_run_id=? GROUP BY raw_text HAVING COUNT(*)>1)",
                (identity["ingest_run_id"],),
            ).fetchone()[0]
        )
        if equal_text_groups <= 0:
            _fail("candidate unexpectedly merges all equal-text occurrences")
        projection = _candidate_projection_rows(connection, identity["ingest_run_id"])
        projected_count, projected_digest = _logical_digest(projection)
        if projected_count != total or projected_digest != manifest["expected"]["logical_pair_sha256"]:
            _fail("candidate logical pair projection digest mismatch")
        for spec in manifest["files"]:
            source = evidence[spec["relative_path"]]
            for ordinal, item in source.samples.items():
                row = connection.execute(
                    "SELECT o.*,r.pointer AS record_pointer,r.raw_json AS record_raw_json,"
                    "r.raw_sha256 AS record_raw_sha256 "
                    "FROM textmap_physical_occurrence o "
                    "JOIN source_record r ON r.id=o.source_record_id "
                    "WHERE o.ingest_run_id=? AND o.source_file_id=? AND o.pair_ordinal=?",
                    (identity["ingest_run_id"], file_ids[spec["relative_path"]], ordinal),
                ).fetchone()
                if row is None:
                    _fail("candidate physical pair sample is missing")
                if (
                    row["record_pointer"] != "/"
                    or row["record_raw_json"] != source.raw_json
                    or row["record_raw_sha256"] != spec["raw_sha256"]
                ):
                    _fail("candidate sample source_record binding mismatch")
                _sample_check(
                    row,
                    item,
                    candidate=True,
                    expected_id=digest_id(
                        "textmap-physical",
                        identity["ingest_run_id"],
                        file_ids[spec["relative_path"]],
                        ordinal,
                    ),
                )
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            _fail("candidate foreign-key check failed")
        preservation = _baseline_preservation_oracle(
            connection, baseline, identity, manifest
        )
        import_row = connection.execute(
            "SELECT imported_row_count,shard_file_sha256,status "
            "FROM batch_shard_import"
        ).fetchone()
        if (
            import_row is None
            or import_row["imported_row_count"] != total
            or import_row["shard_file_sha256"] != _file_sha256(shard)
            or import_row["status"] != "complete"
        ):
            _fail("candidate shard import metadata mismatch")
        digest_rows = connection.execute(
            "SELECT id,ingest_run_id,snapshot_id,parser_build_id,source_file_id,"
            "source_record_id,source_record_pointer,locale,shard_tier,pair_ordinal,"
            "source_pointer,raw_key_json,key_text,raw_text_json,raw_text,"
            "raw_key_sha256,raw_text_sha256,raw_record_sha256,metadata_json "
            "FROM textmap_physical_occurrence "
            "WHERE ingest_run_id=? ORDER BY ingest_run_id,source_file_id,pair_ordinal",
            (identity["ingest_run_id"],),
        )
        digest_count, digest_value, chunks = _digest_rows(
            digest_rows,
            key_indexes=(1, 4, 9),
        )
        digest = connection.execute(
            "SELECT row_count,logical_row_sha256,chunk_size,chunk_count,"
            "schema_sql_sha256,ordering_json,metadata_json "
            "FROM batch_table_digest WHERE table_name='textmap_physical_occurrence'"
        ).fetchone()
        schema_sql = str(
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='textmap_physical_occurrence'"
            ).fetchone()[0]
        )
        if (
            digest is None
            or tuple(digest[:4]) != (
                digest_count,
                digest_value,
                50_000,
                len(chunks),
            )
            or digest["schema_sql_sha256"] != sha256_text(" ".join(schema_sql.split()))
            or json.loads(digest["ordering_json"])
            != ["ingest_run_id", "source_file_id", "pair_ordinal"]
            or json.loads(digest["metadata_json"])
            != {"digest_contract": "canonical-json-row-v1"}
        ):
            _fail("candidate table digest ledger mismatch")
        stored_chunks = [
            tuple(row)
            for row in connection.execute(
                "SELECT chunk_ordinal,first_key_json,last_key_json,row_count,logical_row_sha256 "
                "FROM batch_table_chunk_digest ORDER BY chunk_ordinal"
            )
        ]
        expected_chunks = [
            (
                index,
                canonical_json(item["first_key"]),
                canonical_json(item["last_key"]),
                item["row_count"],
                item["logical_row_sha256"],
            )
            for index, item in enumerate(chunks)
        ]
        if stored_chunks != expected_chunks:
            _fail("candidate chunk digest ledger mismatch")
        return {
            "pair_count": total,
            "schema_version": 15,
            "status": "verified",
            "table_rowset_sha256": digest_value,
            "source_aggregate_sha256": source_aggregate_sha256,
            "migration_oracle": migration_oracle,
            "baseline_preservation": preservation,
            "occurrence_id_oracle_count": occurrence_id_oracle_count,
            "candidate_increment_bytes": (
                candidate.stat().st_size - baseline.stat().st_size
            ),
        }
    finally:
        connection.close()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
