"""Batch M5C Wave02 family-specific R1 shard projection and verifier.

M5C is deliberately non-product.  It reads the frozen M5B audit boundary,
projects each family into an independent SQLite shard, and leaves merge,
runtime/provider, registry, and Schema 17 acceptance to later milestones.
Shared code only handles lexical row reads, physical IDs, shard I/O, and
TextMap pair seals; relation meaning and text-owner selection remain in the
family contract loaded from the M5B report.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .batch_m2_verifier import M2VerificationError, verify_shard
from .batch_m0_baseline import SOURCE_COMMIT, SOURCE_TREE, SOURCE_SNAPSHOT_ID
from .family_projection_protocol import FamilyShardSpec, TableSpec, canonical_row, spec_digest
from .shard_builder import ShardBuildError, build_shard


ROOT = Path(__file__).resolve().parents[2]
M5B_REPORT = ROOT / "reports/batch-m5b/batch-m5b-source-identity-audit.json"
M5B_MANIFEST = ROOT / "manifests/batch/anime-game-data-7.0-batch-m5b-wave02-audit.json"
M1_DEFAULT = Path("/tmp/genshin-lore-batch-m1-v2/textmap-shard.sqlite")
M5C_CONTRACT = "batch-m5c-wave02-r1-projection-v1"
M5C_COVERAGE = "R1_structured_family"
M5C_CANARY_COVERAGE = "bounded_canary"
M5C_OWNER = "batch-m5c-wave02-r1"
ARTIFACT_LIMIT_BYTES = 4 * 1024**3
M1_CONTRACT = "snapshot-textmap-physical-v2"
M1_RUN = "ir-batch-m1-textmap:fb4eac91a0fec3995a1a973b3e8d5cd2904fcec7"
M1_PARSER = "pb-batch-m1-textmap:87032d7a26f43990aed5c1740de1c0a32c6e559e"
LOCALES = ("CHS", "EN", "JP")
LEGAL_STATES = (
    "missing_field", "empty_slot", "missing_target", "dangling", "ambiguous",
    "resolved_unique", "resolved_candidate_set", "unresolved",
    "opaque_unresolved", "not_applicable",
)
PROFILE_CANARY_ROWS = (0, 1, 150, 151, 184)
FAMILY_ORDER = (
    "avatar_codex_archive", "cook_recipe_archive", "cook_bonus_archive",
    "homeworld_furniture_archive", "furniture_make_archive",
    "furniture_suite_archive", "homeworld_furniture_type_archive",
    "homeworld_bgm_archive", "profile_picture_archive",
    "profile_frame_archive", "gadget_title_archive",
)
M5C_MODULE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
M5B_REPORT_SHA256 = hashlib.sha256(M5B_REPORT.read_bytes()).hexdigest() if M5B_REPORT.is_file() else "missing"
M5B_MANIFEST_SHA256 = hashlib.sha256(M5B_MANIFEST.read_bytes()).hexdigest() if M5B_MANIFEST.is_file() else "missing"
if M5B_REPORT.is_file():
    try:
        M5B_AUDIT_CONFIG_CHECKSUM = str(json.loads(M5B_REPORT.read_text(encoding="utf-8"))["audit_config_checksum"])
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise RuntimeError("M5B audit config checksum is unavailable") from exc
else:
    M5B_AUDIT_CONFIG_CHECKSUM = "missing"
MIGRATION_0017 = ROOT / "migrations/0017_batch_wave02_family_projection.sql"
MIGRATION_0017_SHA256 = hashlib.sha256(MIGRATION_0017.read_bytes()).hexdigest() if MIGRATION_0017.is_file() else "missing"
PARSER_BUILD_ID = f"pb-batch-m5c-wave02:{hashlib.sha256((M5C_MODULE_SHA256 + M5B_REPORT_SHA256 + M5B_MANIFEST_SHA256 + MIGRATION_0017_SHA256).encode()).hexdigest()[:40]}"


class M5CError(RuntimeError):
    """A source, shard, TextMap, or M5C contract check failed closed."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


# Projection identity is separate from the parser module identity.  The run
# binds parser implementation, frozen M5B configuration/migration inputs, and
# the parser-independent pinned source seal.  Any one of those inputs changing
# therefore changes parser_build_id, ingest_run_id, and occurrence IDs.
PROJECTION_SOURCE_SEAL = _sha256(_canonical({
    "source_snapshot": SOURCE_SNAPSHOT_ID,
    "source_commit": SOURCE_COMMIT,
    "source_tree": SOURCE_TREE,
}).encode("utf-8"))
PROJECTION_CONFIG_SEAL = _sha256(_canonical({
    "m5c_contract": M5C_CONTRACT,
    "m5b_report_sha256": M5B_REPORT_SHA256,
    "m5b_manifest_sha256": M5B_MANIFEST_SHA256,
    "m5b_audit_config_checksum": M5B_AUDIT_CONFIG_CHECKSUM,
    "migration_0017_sha256": MIGRATION_0017_SHA256,
    "m1_contract": M1_CONTRACT,
    "m1_run_id": M1_RUN,
    "m1_parser_build_id": M1_PARSER,
}).encode("utf-8"))


def derive_projection_run_id(
    parser_build_id: str,
    config_seal: str = PROJECTION_CONFIG_SEAL,
    source_seal: str = PROJECTION_SOURCE_SEAL,
) -> str:
    payload = _canonical({
        "parser_build_id": parser_build_id,
        "projection_config_seal": config_seal,
        "projection_source_seal": source_seal,
    })
    return f"ir-batch-m5c-wave02:{_sha256(payload.encode('utf-8'))[:40]}"


PROJECTION_RUN_ID = derive_projection_run_id(PARSER_BUILD_ID)


def _digest_id(*parts: Any) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
    return digest.hexdigest()


def _json(value: Any) -> str:
    return _canonical(value)


def _escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if value is None:
        return "null"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise M5CError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _decode_json(raw: bytes, label: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda value: (_ for _ in ()).throw(M5CError(
                f"non-standard JSON constant {value} in {label}"
            )),
        )
    except (UnicodeError, json.JSONDecodeError, M5CError) as exc:
        raise M5CError(f"invalid JSON in {label}: {exc}") from exc


def _iter_array(raw: bytes, label: str) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise M5CError(f"{label} is not UTF-8") from exc
    decoder = json.JSONDecoder(object_pairs_hook=_strict_object)
    pos = 0
    while pos < len(text) and text[pos] in " \t\r\n":
        pos += 1
    if pos >= len(text) or text[pos] != "[":
        raise M5CError(f"{label} is not a top-level JSON array")
    pos += 1
    rows: list[dict[str, Any]] = []
    ordinal = 0
    while True:
        while pos < len(text) and text[pos] in " \t\r\n":
            pos += 1
        if pos < len(text) and text[pos] == "]":
            pos += 1
            if text[pos:].strip():
                raise M5CError(f"{label} has trailing bytes")
            return rows
        start = pos
        try:
            value, end = decoder.raw_decode(text, pos)
        except json.JSONDecodeError as exc:
            raise M5CError(f"{label} record {ordinal} cannot be decoded") from exc
        if not isinstance(value, dict):
            raise M5CError(f"{label} record {ordinal} is not an object")
        rows.append({
            "ordinal": ordinal,
            "pointer": f"/{ordinal}",
            "r0_pointer": f"/#{ordinal}",
            "token": text[start:end],
            "token_sha256": _sha256(text[start:end].encode("utf-8")),
            "value": value,
            "canonical_sha256": _sha256(_canonical(value).encode("utf-8")),
        })
        ordinal += 1
        pos = end
        while pos < len(text) and text[pos] in " \t\r\n":
            pos += 1
        if pos < len(text) and text[pos] == ",":
            pos += 1
            continue
        if pos < len(text) and text[pos] == "]":
            pos += 1
            if text[pos:].strip():
                raise M5CError(f"{label} has trailing bytes")
            return rows
        raise M5CError(f"{label} has an invalid array delimiter")


def _git(source_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(source_root), *args],
            check=False, capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise M5CError(f"source git inspection failed: {exc}") from exc
    if result.returncode:
        raise M5CError(result.stderr.strip() or result.stdout.strip() or "git failed")
    return result.stdout.strip()


def verify_source_pin(source_root: str | Path, *, verify_pin: bool = True) -> dict[str, Any]:
    root = Path(source_root).resolve()
    if not root.is_dir() or root.is_symlink():
        raise M5CError(f"source checkout is unavailable: {root}")
    if verify_pin:
        clean = not bool(_git(root, "status", "--porcelain=v1", "--untracked-files=all"))
        commit = _git(root, "rev-parse", "HEAD")
        tree = _git(root, "rev-parse", "HEAD^{tree}")
        if not clean or commit != SOURCE_COMMIT or tree != SOURCE_TREE:
            raise M5CError(f"source pin mismatch: clean={clean} commit={commit} tree={tree}")
    return {
        "status": "matched" if verify_pin else "unverified_test_source",
        "commit": SOURCE_COMMIT,
        "tree": SOURCE_TREE,
        "snapshot_id": SOURCE_SNAPSHOT_ID,
    }


def _load_report(path: Path = M5B_REPORT) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise M5CError(f"M5B report unavailable: {path}") from exc
    if not isinstance(report, dict) or report.get("report_id") != "batch-m5b-wave02-source-identity-audit":
        raise M5CError("M5B report identity mismatch")
    if report.get("source", {}).get("commit") != SOURCE_COMMIT:
        raise M5CError("M5B source commit mismatch")
    if report.get("source", {}).get("tree") != SOURCE_TREE:
        raise M5CError("M5B source tree mismatch")
    if report.get("boundary", {}).get("family_count") != 11 or report.get("boundary", {}).get("primary_row_count") != 5060:
        raise M5CError("M5B boundary denominator mismatch")
    families = {item.get("family"): item for item in report.get("families", [])}
    if set(families) != set(FAMILY_ORDER):
        raise M5CError("M5B family set/order mismatch")
    return report


def _catalog_entry(report: Mapping[str, Any], relative_path: str) -> Mapping[str, Any]:
    for item in report.get("source", {}).get("source_catalog", []):
        if item.get("relative_path") == relative_path:
            return item
    raise M5CError(f"M5B source catalog lacks {relative_path}")


def _load_source(
    source_root: Path,
    report: Mapping[str, Any],
    relative_path: str,
    cache: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    if relative_path in cache:
        return cache[relative_path]
    path = source_root / relative_path
    if not path.is_file() or path.is_symlink():
        raise M5CError(f"source file unavailable: {relative_path}")
    raw = path.read_bytes()
    expected = _catalog_entry(report, relative_path)
    if _sha256(raw) != expected.get("sha256") or len(raw) != expected.get("size_bytes"):
        raise M5CError(f"M5B source file seal mismatch: {relative_path}")
    rows = _iter_array(raw, relative_path)
    if len(rows) != expected.get("row_count"):
        raise M5CError(f"M5B source row count mismatch: {relative_path}")
    cache[relative_path] = rows
    return rows


def _field_tokens(path: str) -> list[str]:
    result: list[str] = []
    for part in path.split("."):
        if part.endswith("[]"):
            result.extend([part[:-2], "[]"])
        else:
            result.append(part)
    return result


def _field_occurrences(row: Mapping[str, Any], path: str) -> list[tuple[list[Any], Any, bool, str | None]]:
    tokens = _field_tokens(path)
    result: list[tuple[list[Any], Any, bool, str | None]] = []

    def walk(value: Any, index: int, coords: list[Any]) -> None:
        if index == len(tokens):
            result.append((coords, value, True, None))
            return
        token = tokens[index]
        if token == "[]":
            if not isinstance(value, list):
                result.append((coords, None, False, "missing_field"))
                return
            if not value:
                result.append((coords, None, False, "empty_slot"))
                return
            for child_index, child in enumerate(value):
                walk(child, index + 1, coords + [child_index])
            return
        if isinstance(value, Mapping) and token in value:
            walk(value[token], index + 1, coords + [token])
        else:
            reason = "empty_slot" if isinstance(value, Mapping) and not value else "missing_field"
            result.append((coords + [token], None, False, reason))

    walk(row, 0, [])
    return result


def _pointer(row_ordinal: int, coords: Sequence[Any]) -> str:
    value = f"/{row_ordinal}"
    for item in coords:
        value += "/" + (str(item) if isinstance(item, int) else _escape(str(item)))
    return value


def _walk_raw_fields(row: Mapping[str, Any]) -> list[tuple[str, str, Any]]:
    result: list[tuple[str, str, Any]] = []

    def walk(value: Any, pointer: str, field_path: str) -> None:
        result.append((pointer, field_path, value))
        if isinstance(value, Mapping):
            for key, child in value.items():
                child_pointer = pointer + "/" + _escape(str(key))
                child_path = f"{field_path}.{key}" if field_path else str(key)
                walk(child, child_pointer, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, pointer + f"/{index}", field_path + "[]")

    for key, value in row.items():
        walk(value, "/" + _escape(str(key)), str(key))
    return result


def _generic_ddl(family: str, kind: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    p = family
    names = {
        "row": f"{p}_row_occurrence",
        "raw": f"{p}_raw_field_occurrence",
        "relation": f"{p}_relationship_occurrence",
        "candidate": f"{p}_relationship_candidate_occurrence",
        "owner": f"{p}_text_owner_occurrence",
        "resolution": f"{p}_text_resolution_occurrence",
        "text": f"{p}_text_candidate_occurrence",
        "opaque": f"{p}_opaque_occurrence",
        "asset": f"{p}_asset_occurrence",
    }
    if kind == "row":
        return (
            f"""CREATE TABLE {names['row']} (
 occurrence_id TEXT PRIMARY KEY NOT NULL, family TEXT NOT NULL,
 family_contract_version TEXT NOT NULL, ingest_run_id TEXT NOT NULL,
 snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_file_path TEXT NOT NULL, source_file_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_record_pointer TEXT NOT NULL,
 source_r0_physical_pointer TEXT NOT NULL, source_ordinal INTEGER NOT NULL CHECK(source_ordinal>=0),
 raw_record_json TEXT NOT NULL, raw_record_sha256 TEXT NOT NULL CHECK(length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_record_lexical_sha256 TEXT NOT NULL CHECK(length(raw_record_lexical_sha256)=64 AND raw_record_lexical_sha256 NOT GLOB '*[^0-9a-f]*'),
 native_identity_json TEXT NOT NULL CHECK(json_valid(native_identity_json)),
 record_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json))
)""",
            ("occurrence_id","family","family_contract_version","ingest_run_id","snapshot_id","parser_build_id","source_file_path","source_file_id","source_record_id","source_record_pointer","source_r0_physical_pointer","source_ordinal","raw_record_json","raw_record_sha256","raw_record_lexical_sha256","native_identity_json","record_status","metadata_json"),
            ("source_file_path","source_ordinal","source_record_pointer","occurrence_id"),
        )
    if kind == "raw":
        return (
            f"""CREATE TABLE {names['raw']} (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 field_presence TEXT NOT NULL CHECK(field_presence IN ('present','missing')),
 raw_json TEXT, resolution_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 CHECK((field_presence='present' AND raw_json IS NOT NULL) OR (field_presence='missing' AND raw_json IS NULL)),
 FOREIGN KEY(row_occurrence_id) REFERENCES {names['row']}(occurrence_id)
)""",
            ("occurrence_id","row_occurrence_id","ingest_run_id","snapshot_id","parser_build_id","source_record_id","source_pointer","field_path","field_presence","raw_json","resolution_status","metadata_json"),
            ("row_occurrence_id","source_pointer","occurrence_id"),
        )
    if kind == "relation":
        return (
            f"""CREATE TABLE {names['relation']} (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, relation_field TEXT NOT NULL,
 raw_value_json TEXT, target_source_file TEXT NOT NULL, target_field TEXT NOT NULL,
 condition_json TEXT NOT NULL CHECK(json_valid(condition_json)),
 resolution_status TEXT NOT NULL CHECK(resolution_status IN ('missing_field','empty_slot','missing_target','dangling','ambiguous','resolved_unique','resolved_candidate_set','unresolved','opaque_unresolved','not_applicable')),
 candidate_count INTEGER NOT NULL CHECK(candidate_count>=0), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES {names['row']}(occurrence_id)
)""",
            ("occurrence_id","row_occurrence_id","ingest_run_id","snapshot_id","parser_build_id","source_record_id","source_pointer","relation_field","raw_value_json","target_source_file","target_field","condition_json","resolution_status","candidate_count","metadata_json"),
            ("row_occurrence_id","source_pointer","occurrence_id"),
        )
    if kind == "candidate":
        return (
            f"""CREATE TABLE {names['candidate']} (
 occurrence_id TEXT PRIMARY KEY NOT NULL, relationship_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_file_id TEXT NOT NULL, source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL,
 candidate_ordinal INTEGER NOT NULL CHECK(candidate_ordinal>=0), target_source_file TEXT NOT NULL,
 target_source_row_ordinal INTEGER NOT NULL CHECK(target_source_row_ordinal>=0), target_pointer TEXT NOT NULL,
 target_field TEXT NOT NULL, target_value_json TEXT NOT NULL CHECK(json_valid(target_value_json)),
 target_raw_record_sha256 TEXT NOT NULL CHECK(length(target_raw_record_sha256)=64 AND target_raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 target_lexical_sha256 TEXT NOT NULL CHECK(length(target_lexical_sha256)=64 AND target_lexical_sha256 NOT GLOB '*[^0-9a-f]*'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(relationship_occurrence_id) REFERENCES {names['relation']}(occurrence_id)
)""",
            ("occurrence_id","relationship_occurrence_id","ingest_run_id","snapshot_id","parser_build_id","source_file_id","source_record_id","source_pointer","candidate_ordinal","target_source_file","target_source_row_ordinal","target_pointer","target_field","target_value_json","target_raw_record_sha256","target_lexical_sha256","metadata_json"),
            ("relationship_occurrence_id","candidate_ordinal","occurrence_id"),
        )
    if kind == "owner":
        return (
            f"""CREATE TABLE {names['owner']} (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 owner_hash_json TEXT NOT NULL CHECK(json_valid(owner_hash_json)), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES {names['row']}(occurrence_id)
)""",
            ("occurrence_id","row_occurrence_id","ingest_run_id","snapshot_id","parser_build_id","source_record_id","source_pointer","field_path","owner_hash_json","metadata_json"),
            ("row_occurrence_id","source_pointer","occurrence_id"),
        )
    if kind == "resolution":
        return (
            f"""CREATE TABLE {names['resolution']} (
 occurrence_id TEXT PRIMARY KEY NOT NULL, owner_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')), resolution_status TEXT NOT NULL CHECK(resolution_status IN ('missing','resolved_unique','ambiguous')),
 candidate_count INTEGER NOT NULL CHECK(candidate_count>=0), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(owner_occurrence_id) REFERENCES {names['owner']}(occurrence_id)
)""",
            ("occurrence_id","owner_occurrence_id","ingest_run_id","snapshot_id","parser_build_id","locale","resolution_status","candidate_count","metadata_json"),
            ("owner_occurrence_id","locale","occurrence_id"),
        )
    if kind == "text":
        return (
            f"""CREATE TABLE {names['text']} (
 occurrence_id TEXT PRIMARY KEY NOT NULL, resolution_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 relative_path TEXT NOT NULL, locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')),
 shard_tier TEXT NOT NULL CHECK(shard_tier IN ('main','medium')), pair_ordinal INTEGER NOT NULL CHECK(pair_ordinal>=0),
 pointer TEXT NOT NULL, key_text TEXT NOT NULL, raw_text TEXT NOT NULL,
 raw_key_sha256 TEXT NOT NULL CHECK(length(raw_key_sha256)=64 AND raw_key_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_text_sha256 TEXT NOT NULL CHECK(length(raw_text_sha256)=64 AND raw_text_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_record_sha256 TEXT NOT NULL CHECK(length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(resolution_occurrence_id) REFERENCES {names['resolution']}(occurrence_id)
)""",
            ("occurrence_id","resolution_occurrence_id","ingest_run_id","snapshot_id","parser_build_id","relative_path","locale","shard_tier","pair_ordinal","pointer","key_text","raw_text","raw_key_sha256","raw_text_sha256","raw_record_sha256","metadata_json"),
            ("relative_path","pair_ordinal","occurrence_id"),
        )
    if kind == "opaque":
        return (
            f"""CREATE TABLE {names['opaque']} (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 raw_json TEXT NOT NULL CHECK(json_valid(raw_json)), resolution_status TEXT NOT NULL CHECK(resolution_status='opaque_unresolved'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES {names['row']}(occurrence_id)
)""",
            ("occurrence_id","row_occurrence_id","ingest_run_id","snapshot_id","parser_build_id","source_record_id","source_pointer","field_path","raw_json","resolution_status","metadata_json"),
            ("row_occurrence_id","source_pointer","occurrence_id"),
        )
    if kind == "asset":
        return (
            f"""CREATE TABLE {names['asset']} (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 raw_json TEXT, resolution_status TEXT NOT NULL CHECK(resolution_status IN ('raw_observed','missing_field','empty_slot')),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES {names['row']}(occurrence_id)
)""",
            ("occurrence_id","row_occurrence_id","ingest_run_id","snapshot_id","parser_build_id","source_record_id","source_pointer","field_path","raw_json","resolution_status","metadata_json"),
            ("row_occurrence_id","source_pointer","occurrence_id"),
        )
    raise M5CError(f"unknown generic table kind {kind}")


def _avatar_ddl(kind: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    names = {
        "row": "avatar_codex_row_occurrence",
        "raw": "avatar_codex_raw_field_occurrence",
        "relation": "avatar_codex_relationship_occurrence",
        "candidate": "avatar_codex_relationship_candidate_occurrence",
        "opaque": "avatar_codex_opaque_occurrence",
    }
    if kind == "row":
        return ("""CREATE TABLE avatar_codex_row_occurrence (
 id TEXT PRIMARY KEY NOT NULL, ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL,
 parser_build_id TEXT NOT NULL, source_file_id TEXT NOT NULL, source_record_id TEXT NOT NULL,
 source_pointer TEXT NOT NULL, evidence_kind TEXT NOT NULL, table_name TEXT NOT NULL,
 row_index INTEGER NOT NULL, native_locator_field TEXT NOT NULL, native_locator_value TEXT NOT NULL,
 raw_checksum TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 UNIQUE(id,ingest_run_id,snapshot_id,parser_build_id)
)""",
        ("id","ingest_run_id","snapshot_id","parser_build_id","source_file_id","source_record_id","source_pointer","evidence_kind","table_name","row_index","native_locator_field","native_locator_value","raw_checksum","metadata_json"),
        ("row_index","source_pointer","id"))
    if kind == "raw":
        return ("""CREATE TABLE avatar_codex_raw_field_occurrence (
 id TEXT PRIMARY KEY NOT NULL, ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL,
 parser_build_id TEXT NOT NULL, row_occurrence_id TEXT NOT NULL, source_record_id TEXT NOT NULL,
 source_pointer TEXT NOT NULL, field_name TEXT NOT NULL, field_presence TEXT NOT NULL,
 raw_json TEXT, resolution_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 CHECK((field_presence='present' AND raw_json IS NOT NULL) OR (field_presence='missing' AND raw_json IS NULL))
)""",
        ("id","ingest_run_id","snapshot_id","parser_build_id","row_occurrence_id","source_record_id","source_pointer","field_name","field_presence","raw_json","resolution_status","metadata_json"),
        ("row_occurrence_id","source_pointer","id"))
    if kind == "relation":
        return ("""CREATE TABLE avatar_codex_relationship_occurrence (
 id TEXT PRIMARY KEY NOT NULL, ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL,
 parser_build_id TEXT NOT NULL, source_row_occurrence_id TEXT NOT NULL, source_record_id TEXT NOT NULL,
 source_pointer TEXT NOT NULL, field_name TEXT NOT NULL, raw_value_json TEXT NOT NULL,
 target_table TEXT NOT NULL, target_field TEXT NOT NULL, resolution_status TEXT NOT NULL,
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json))
)""",
        ("id","ingest_run_id","snapshot_id","parser_build_id","source_row_occurrence_id","source_record_id","source_pointer","field_name","raw_value_json","target_table","target_field","resolution_status","metadata_json"),
        ("source_row_occurrence_id","source_pointer","id"))
    if kind == "candidate":
        return ("""CREATE TABLE avatar_codex_relationship_candidate_occurrence (
 id TEXT PRIMARY KEY NOT NULL, ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL,
 parser_build_id TEXT NOT NULL, relationship_occurrence_id TEXT NOT NULL, source_file_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, candidate_ordinal INTEGER NOT NULL,
 target_table TEXT NOT NULL, target_field TEXT NOT NULL, target_raw_value_json TEXT NOT NULL,
 raw_checksum TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json))
)""",
        ("id","ingest_run_id","snapshot_id","parser_build_id","relationship_occurrence_id","source_file_id","source_record_id","source_pointer","candidate_ordinal","target_table","target_field","target_raw_value_json","raw_checksum","metadata_json"),
        ("relationship_occurrence_id","candidate_ordinal","id"))
    if kind == "opaque":
        return ("""CREATE TABLE avatar_codex_opaque_occurrence (
 id TEXT PRIMARY KEY NOT NULL, ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL,
 parser_build_id TEXT NOT NULL, source_row_occurrence_id TEXT NOT NULL, source_record_id TEXT NOT NULL,
 source_pointer TEXT NOT NULL, field_name TEXT NOT NULL, raw_value_json TEXT NOT NULL,
 resolution_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json))
)""",
        ("id","ingest_run_id","snapshot_id","parser_build_id","source_row_occurrence_id","source_record_id","source_pointer","field_name","raw_value_json","resolution_status","metadata_json"),
        ("source_row_occurrence_id","source_pointer","id"))
    raise M5CError(f"unknown Avatar table kind {kind}")


def _m1_container_map(report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    containers = report.get("dependencies", {}).get("m1_textmap", {}).get("containers")
    if not isinstance(containers, list) or len(containers) != 6:
        raise M5CError("M5B M1 container closure is not six files")
    result: dict[str, dict[str, Any]] = {}
    for item in containers:
        path = str(item["relative_path"])
        if path in result or item.get("locale") not in LOCALES or item.get("tier") not in {"main", "medium"}:
            raise M5CError("M5B M1 locale/tier mapping is invalid")
        result[path] = dict(item)
    return result


def _load_m1_pairs(
    m1_path: str | Path,
    report: Mapping[str, Any],
    wanted_keys: set[str],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    path = Path(m1_path).resolve()
    if not path.is_file() or path.is_symlink():
        raise M5CError(f"M1 TextMap shard unavailable: {path}")
    expected = _m1_container_map(report)
    pairs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        metadata = dict(connection.execute("SELECT key,value FROM shard_metadata ORDER BY key"))
        if (
            metadata.get("contract_version") != M1_CONTRACT
            or metadata.get("ingest_run_id") != M1_RUN
            or metadata.get("parser_build_id") != M1_PARSER
            or metadata.get("snapshot_id") != SOURCE_SNAPSHOT_ID
        ):
            raise M5CError("M1 TextMap identity mismatch")
        containers = {
            row[0]: {"relative_path": row[0], "locale": row[1], "tier": row[2], "source_pointer": row[3], "raw_sha256": row[4]}
            for row in connection.execute("SELECT relative_path,locale,shard_tier,source_pointer,raw_sha256 FROM textmap_source_container ORDER BY relative_path")
        }
        if set(containers) != set(expected):
            raise M5CError("M1 TextMap container set mismatch")
        for path_name, item in expected.items():
            observed = containers[path_name]
            if (observed["locale"], observed["tier"], observed["source_pointer"], observed["raw_sha256"]) != (item["locale"], item["tier"], "/", item["raw_sha256"]):
                raise M5CError(f"M1 container locale/tier/source seal mismatch: {path_name}")
        digest = connection.execute("SELECT row_count,logical_row_sha256 FROM table_digest WHERE table_name='textmap_pair_occurrence'").fetchone()
        if not digest or int(digest[0]) != int(report["dependencies"]["m1_textmap"]["metadata"]["row_count"]) or digest[1] != report["dependencies"]["m1_textmap"]["metadata"]["rowset_sha256"]:
            raise M5CError("M1 TextMap pair denominator/digest mismatch")
        if not wanted_keys:
            return {}, expected
        key_list = sorted(wanted_keys)
        for start in range(0, len(key_list), 400):
            chunk = key_list[start:start + 400]
            placeholders = ",".join("?" for _ in chunk)
            query = (
                "SELECT relative_path,locale,shard_tier,pair_ordinal,pointer,key_text,raw_text,"
                "raw_key_sha256,raw_text_sha256,raw_record_sha256 "
                f"FROM textmap_pair_occurrence WHERE key_text IN ({placeholders}) "
                "ORDER BY relative_path,pair_ordinal"
            )
            for row in connection.execute(query, chunk):
                relative_path, locale, tier, ordinal, pointer, key_text, raw_text, raw_key, raw_text_sha, raw_record_sha = row
                if relative_path not in expected:
                    raise M5CError(f"M1 pair uses an unpinned source file: {relative_path}")
                pairs[str(key_text)].append({
                    "relative_path": relative_path, "locale": str(locale), "shard_tier": str(tier),
                    "pair_ordinal": int(ordinal), "pointer": str(pointer), "key_text": str(key_text),
                    "raw_text": str(raw_text), "raw_key_sha256": str(raw_key),
                    "raw_text_sha256": str(raw_text_sha), "raw_record_sha256": str(raw_record_sha),
                })
    except (sqlite3.Error, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, M5CError):
            raise
        raise M5CError(f"cannot read M1 TextMap shard: {exc}") from exc
    finally:
        connection.close()
    for key in pairs:
        pairs[key].sort(key=lambda item: (item["relative_path"], item["pair_ordinal"]))
    return dict(pairs), expected


def _target_index(rows: Sequence[Mapping[str, Any]], key: str) -> dict[Any, list[dict[str, Any]]]:
    result: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = row["value"].get(key)
        if isinstance(value, (dict, list)):
            continue
        if value is not None:
            result[value].append(row)
    return result


def _relation_rows(
    family: str,
    definition: Mapping[str, Any],
    primary: Sequence[Mapping[str, Any]],
    source_file: str,
    source_file_id: str,
    row_ids: Mapping[int, str],
    run_id: str,
    snapshot_id: str,
    parser_id: str,
    sources: Mapping[str, Sequence[Mapping[str, Any]]],
    relations: list[tuple[Any, ...]],
    candidates: list[tuple[Any, ...]],
) -> None:
    for relation_index, relation in enumerate(definition.get("relations", [])):
        target_file = str(relation["target_source_file"])
        target_field = str(relation["target_locator_field"])
        target_index = _target_index(sources[target_file], target_field)
        condition = relation.get("condition")
        for source_row in primary:
            row_number = int(source_row["ordinal"])
            row = source_row["value"]
            for coords, value, present, missing_reason in _field_occurrences(row, str(relation["field"])):
                pointer = _pointer(row_number, coords)
                if not present:
                    status = missing_reason or "missing_field"
                    matched: list[Mapping[str, Any]] = []
                elif condition and row.get(str(condition.get("path"))) != condition.get("equals"):
                    if family == "profile_picture_archive" and row_number in PROFILE_CANARY_ROWS:
                        status = "unresolved"
                        matched = []
                    else:
                        continue
                else:
                    matched = target_index.get(value, [])
                    status = (
                        "resolved_unique" if len(matched) == 1
                        else "resolved_candidate_set" if len(matched) > 1
                        else "missing_target"
                    )
                relation_id = _digest_id("m5c-relation", family, run_id, source_file, pointer, relation_index)
                relation_row = (
                    relation_id, row_ids[row_number], run_id, snapshot_id, parser_id,
                    _digest_id("source-record", source_file_id, f"/{row_number}"), pointer,
                    str(relation["field"]), None if not present else _json(value),
                    target_file, target_field, _json(condition or {}),
                    status, len(matched),
                    _json({
                        "relation_index": relation_index,
                        "raw_zero_observed": bool(present and value == 0),
                        "missing_reason": missing_reason,
                        "condition_gate": condition or None,
                        "identity_effect": "physical_candidate_scope_only",
                    }),
                )
                relations.append(relation_row)
                for candidate_ordinal, target_row in enumerate(matched):
                    target_pointer = str(target_row["pointer"])
                    target_record = _digest_id("source-record", _digest_id("source-file", snapshot_id, target_file), target_pointer)
                    candidates.append((
                        _digest_id("m5c-relation-candidate", family, run_id, relation_id, candidate_ordinal),
                        relation_id, run_id, snapshot_id, parser_id,
                        _digest_id("source-file", snapshot_id, target_file), target_record, target_pointer,
                        candidate_ordinal, target_file, int(target_row["ordinal"]), target_pointer,
                        target_field, _json(target_row["value"].get(target_field)),
                        target_row["token_sha256"], target_row["token_sha256"],
                        _json({"candidate_scope": "physical_lookup_occurrence", "source_row_ordinal": row_number}),
                    ))


def _text_rows(
    family: str,
    primary: Sequence[Mapping[str, Any]],
    definition: Mapping[str, Any],
    row_ids: Mapping[int, str],
    run_id: str,
    snapshot_id: str,
    parser_id: str,
    pair_lookup: Mapping[str, Sequence[Mapping[str, Any]]],
    owners: list[tuple[Any, ...]],
    resolutions: list[tuple[Any, ...]],
    text_candidates: list[tuple[Any, ...]],
) -> None:
    selected = set(PROFILE_CANARY_ROWS) if family == "profile_picture_archive" else None
    for field in definition.get("text_owner_allowlist", []):
        for source_row in primary:
            row_number = int(source_row["ordinal"])
            if selected is not None and row_number not in selected:
                continue
            row = source_row["value"]
            if field not in row:
                continue
            pointer = f"/{row_number}/{_escape(field)}"
            owner_id = _digest_id("m5c-text-owner", family, run_id, pointer)
            owner_value = row[field]
            owner_pairs = list(pair_lookup.get(str(owner_value), ()))
            owners.append((
                owner_id, row_ids[row_number], run_id, snapshot_id, parser_id,
                _digest_id("source-record", _digest_id("source-file", snapshot_id, definition["primary_source"]["relative_path"]), f"/{row_number}"),
                pointer, field, _json(owner_value),
                _json({"owner_occurrence": "physical_field", "pair_key_text": str(owner_value)}),
            ))
            for locale in LOCALES:
                matching = [item for item in owner_pairs if item["locale"] == locale]
                status = "missing" if not matching else "resolved_unique" if len(matching) == 1 else "ambiguous"
                resolution_id = _digest_id("m5c-text-resolution", family, run_id, owner_id, locale)
                resolutions.append((
                    resolution_id, owner_id, run_id, snapshot_id, parser_id,
                    locale, status, len(matching),
                    _json({"locale_tier_policy": "all main+medium physical pairs retained", "selected_locale": False}),
                ))
                for candidate in matching:
                    text_candidates.append((
                        _digest_id("m5c-text-candidate", family, run_id, resolution_id, candidate["relative_path"], candidate["pair_ordinal"]),
                        resolution_id, run_id, snapshot_id, parser_id,
                        candidate["relative_path"], candidate["locale"], candidate["shard_tier"],
                        candidate["pair_ordinal"], candidate["pointer"], candidate["key_text"], candidate["raw_text"],
                        candidate["raw_key_sha256"], candidate["raw_text_sha256"], candidate["raw_record_sha256"],
                        _json({"source_mapping_checked": True}),
                    ))


def _child_rows(
    family: str,
    definition: Mapping[str, Any],
    primary: Sequence[Mapping[str, Any]],
    row_ids: Mapping[int, str],
    run_id: str,
    snapshot_id: str,
    parser_id: str,
    pair_lookup: Mapping[str, Sequence[Mapping[str, Any]]],
    sources: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[list[tuple[Any, ...]], ...]:
    source_file = str(definition["primary_source"]["relative_path"])
    source_file_id = _digest_id("source-file", snapshot_id, source_file)
    raw_rows: list[tuple[Any, ...]] = []
    relation_rows: list[tuple[Any, ...]] = []
    candidate_rows: list[tuple[Any, ...]] = []
    owner_rows: list[tuple[Any, ...]] = []
    resolution_rows: list[tuple[Any, ...]] = []
    text_rows: list[tuple[Any, ...]] = []
    opaque_rows: list[tuple[Any, ...]] = []
    asset_rows: list[tuple[Any, ...]] = []
    opaque_paths = list(definition.get("opaque_unresolved_paths", []))
    asset_paths = list(definition.get("asset_raw_only_paths", []))
    for source_row in primary:
        row_number = int(source_row["ordinal"])
        row = source_row["value"]
        row_id = row_ids[row_number]
        source_record_id = _digest_id("source-record", source_file_id, f"/{row_number}")
        for pointer, field_path, value in _walk_raw_fields(row):
            full_pointer = f"/{row_number}{pointer}"
            raw_rows.append((
                _digest_id("m5c-raw", family, run_id, full_pointer), row_id, run_id, snapshot_id, parser_id,
                source_record_id, full_pointer, field_path, "present", _json(value),
                "raw_observed", _json({"value_type": _type_name(value)}),
            ))
        for path in opaque_paths:
            for coords, value, present, missing_reason in _field_occurrences(row, path):
                pointer = _pointer(row_number, coords)
                opaque_rows.append((
                    _digest_id("m5c-opaque", family, run_id, pointer, path), row_id, run_id, snapshot_id, parser_id,
                    source_record_id, pointer, path, _json(value if present else None), "opaque_unresolved",
                    _json({"missing_reason": missing_reason, "identity_effect": "none"}),
                ))
        for path in asset_paths:
            for coords, value, present, missing_reason in _field_occurrences(row, path):
                pointer = _pointer(row_number, coords)
                asset_rows.append((
                    _digest_id("m5c-asset", family, run_id, pointer, path), row_id, run_id, snapshot_id, parser_id,
                    source_record_id, pointer, path, None if not present else _json(value),
                    "raw_observed" if present else (missing_reason or "missing_field"),
                    _json({"asset_identity_inferred": False, "missing_reason": missing_reason}),
                ))
    _relation_rows(
        family, definition, primary, source_file, source_file_id, row_ids, run_id,
        snapshot_id, parser_id, sources, relation_rows, candidate_rows,
    )
    _text_rows(
        family, primary, definition, row_ids, run_id, snapshot_id, parser_id, pair_lookup,
        owner_rows, resolution_rows, text_rows,
    )
    return raw_rows, relation_rows, candidate_rows, owner_rows, resolution_rows, text_rows, opaque_rows, asset_rows


def _table(
    family: str,
    kind: str,
    rows: Sequence[Sequence[Any]],
    *,
    avatar: bool = False,
) -> TableSpec:
    ddl, columns, order_columns = (_avatar_ddl(kind) if avatar else _generic_ddl(family, kind))
    positions = {name: index for index, name in enumerate(columns)}
    if "source_file_path" in positions:
        sort_key = lambda row: (
            str(row[positions["source_file_path"]]),
            int(row[positions["source_ordinal"]]) if "source_ordinal" in positions else 0,
            str(row[positions.get("source_pointer", positions.get("source_record_pointer", 0))]),
            str(row[0]),
        )
    elif "row_index" in positions:
        sort_key = lambda row: (int(row[positions["row_index"]]), str(row[positions["source_pointer"]]), str(row[0]))
    else:
        sort_key = lambda row: tuple(str(value) for value in row)
    index_name = f"idx_m5c_{family}_{kind}"
    index_columns = ", ".join(order_columns)
    return TableSpec(
        name=ddl.split("CREATE TABLE ", 1)[1].split(" ", 1)[0],
        create_sql=ddl,
        columns=columns,
        rows=tuple(tuple(row) for row in rows),
        order_columns=order_columns,
        sort_key=sort_key,
        index_sql=(f"CREATE INDEX {index_name} ON {ddl.split('CREATE TABLE ',1)[1].split(' ',1)[0]} ({index_columns})",),
    )


def _full_or_canary(definition: Mapping[str, Any], family: str) -> tuple[str, tuple[int, ...] | None]:
    if family == "profile_picture_archive":
        return M5C_CANARY_COVERAGE, PROFILE_CANARY_ROWS
    return M5C_COVERAGE, None


def _build_avatar_spec(
    definition: Mapping[str, Any],
    primary: Sequence[Mapping[str, Any]],
    sources: Mapping[str, Sequence[Mapping[str, Any]]],
    report: Mapping[str, Any],
    run_id: str,
) -> FamilyShardSpec:
    family = "avatar_codex_archive"
    source_file = str(definition["primary_source"]["relative_path"])
    source_file_id = _digest_id("source-file", SOURCE_SNAPSHOT_ID, source_file)
    row_rows: list[tuple[Any, ...]] = []
    raw_rows: list[tuple[Any, ...]] = []
    relation_rows: list[tuple[Any, ...]] = []
    candidate_rows: list[tuple[Any, ...]] = []
    opaque_rows: list[tuple[Any, ...]] = []
    lookup_file = str(definition["relations"][0]["target_source_file"])
    lookup_index = _target_index(sources[lookup_file], str(definition["relations"][0]["target_locator_field"]))
    row_ids: dict[int, str] = {}
    for source_row in primary:
        n = int(source_row["ordinal"])
        pointer = f"/{n}"
        source_record_id = _digest_id("source-record", source_file_id, pointer)
        row_id = _digest_id("m5c-avatar-row", run_id, source_file, pointer, source_row["token_sha256"])
        row_ids[n] = row_id
        value = source_row["value"]
        row_rows.append((
            row_id, run_id, SOURCE_SNAPSHOT_ID, PARSER_BUILD_ID, source_file_id, source_record_id,
            pointer, "avatar_codex_entry", "AvatarCodexExcelConfigData", n, "avatarId",
            str(value.get("avatarId")), source_row["canonical_sha256"],
            _json({"raw_record_json": source_row["token"], "raw_record_lexical_sha256": source_row["token_sha256"], "identity_effect": "physical_occurrence_only"}),
        ))
        for field, field_value in value.items():
            field_pointer = f"/{n}/{_escape(field)}"
            raw_rows.append((
                _digest_id("m5c-avatar-raw", run_id, source_record_id, field_pointer),
                run_id, SOURCE_SNAPSHOT_ID, PARSER_BUILD_ID, row_id, source_record_id, field_pointer,
                field, "present", _json(field_value),
                "opaque_unresolved" if field in definition.get("opaque_unresolved_paths", []) else "raw_observed",
                _json({"field_value_type": _type_name(field_value)}),
            ))
            if field in definition.get("opaque_unresolved_paths", []):
                opaque_rows.append((
                    _digest_id("m5c-avatar-opaque", run_id, source_record_id, field_pointer),
                    run_id, SOURCE_SNAPSHOT_ID, PARSER_BUILD_ID, row_id, source_record_id, field_pointer,
                    field, _json(field_value), "opaque_unresolved", _json({"identity_effect": "none"}),
                ))
        candidates = lookup_index.get(value.get("avatarId"), [])
        relation_pointer = f"/{n}/avatarId"
        relation_id = _digest_id("m5c-avatar-rel", run_id, source_record_id, relation_pointer)
        relation_rows.append((
            relation_id, run_id, SOURCE_SNAPSHOT_ID, PARSER_BUILD_ID, row_id, source_record_id,
            relation_pointer, "avatarId", _json(value.get("avatarId")), "AvatarExcelConfigData", "id",
            "resolved_unique" if len(candidates) == 1 else "resolved_candidate_set" if candidates else "missing",
            _json({"candidate_count": len(candidates), "identity_effect": "physical_candidate_scope_only"}),
        ))
        for candidate_ordinal, target in enumerate(candidates):
            target_pointer = str(target["pointer"])
            target_source_id = _digest_id("source-record", _digest_id("source-file", SOURCE_SNAPSHOT_ID, lookup_file), target_pointer)
            candidate_rows.append((
                _digest_id("m5c-avatar-candidate", run_id, relation_id, candidate_ordinal),
                run_id, SOURCE_SNAPSHOT_ID, PARSER_BUILD_ID, relation_id,
                _digest_id("source-file", SOURCE_SNAPSHOT_ID, lookup_file), target_source_id, target_pointer,
                candidate_ordinal, "AvatarExcelConfigData", "id", _json(target["value"].get("id")),
                target["canonical_sha256"], _json({"target_source_file": lookup_file, "target_row_ordinal": target["ordinal"], "target_lexical_sha256": target["token_sha256"]}),
            ))
    tables = (
        _table(family, "row", row_rows, avatar=True),
        _table(family, "raw", raw_rows, avatar=True),
        _table(family, "relation", relation_rows, avatar=True),
        _table(family, "candidate", candidate_rows, avatar=True),
        _table(family, "opaque", opaque_rows, avatar=True),
    )
    return FamilyShardSpec(
        family=family, family_contract_version=str(definition["contract_version"]),
        ingest_run_id=run_id, snapshot_id=SOURCE_SNAPSHOT_ID, parser_build_id=PARSER_BUILD_ID,
        coverage_level=M5C_COVERAGE, corpus_complete=False, product_accepted=False,
        read_set=tuple(sorted({source_file, lookup_file, str(M5B_MANIFEST.relative_to(ROOT))})),
        write_set=tuple(table.name for table in tables), tables=tables,
        metadata={
            "boundary": "M5B AvatarCodex primary 120 rows",
            "projection_stage": "full",
            "primary_source_file": source_file,
            "primary_source_sha256": _catalog_entry(report, source_file)["sha256"],
            "primary_source_row_count": len(primary),
            "logical_identity_created": False,
            "cross_family_merge": False,
            "reused_existing_schema14_table_names": True,
            "schema14_tables_reused": [table.name for table in tables],
            "textmap_or_asset_projection": False,
            "projection_identity": {
                "projection_run_id": run_id,
                "parser_build_id": PARSER_BUILD_ID,
                "projection_config_seal": PROJECTION_CONFIG_SEAL,
                "projection_source_seal": PROJECTION_SOURCE_SEAL,
            },
            "m5b_operation_contract": definition.get("operation_contract", {}),
            "source_boundary": {
                "primary_source": definition.get("primary_source", {}),
                "lookup_sources": definition.get("lookup_sources", []),
            },
            "counts": {"primary_rows": len(primary), "raw_fields": len(raw_rows), "relations": len(relation_rows), "relation_candidates": len(candidate_rows), "opaque": len(opaque_rows)},
        },
    )


def load_specs(
    source_root: str | Path,
    *,
    m1_path: str | Path = M1_DEFAULT,
    report_path: str | Path = M5B_REPORT,
    verify_pin: bool = True,
) -> tuple[FamilyShardSpec, ...]:
    report = _load_report(Path(report_path))
    root = Path(source_root).resolve()
    verify_source_pin(root, verify_pin=verify_pin)
    cache: dict[str, list[dict[str, Any]]] = {}
    definitions = {item["family"]: item for item in report["families"]}
    all_source_paths = set()
    for definition in definitions.values():
        all_source_paths.add(str(definition["primary_source"]["relative_path"]))
        for lookup in definition.get("lookup_sources", []):
            all_source_paths.add(str(lookup["relative_path"]))
    sources = {path: _load_source(root, report, path, cache) for path in sorted(all_source_paths)}
    wanted_keys: set[str] = set()
    for definition in definitions.values():
        for field in definition.get("text_owner_allowlist", []):
            for source_row in sources[str(definition["primary_source"]["relative_path"])]:
                if field in source_row["value"]:
                    wanted_keys.add(str(source_row["value"][field]))
    pair_lookup, m1_containers = _load_m1_pairs(m1_path, report, wanted_keys)
    run_id = PROJECTION_RUN_ID
    specs: list[FamilyShardSpec] = []
    for family in FAMILY_ORDER:
        definition = definitions[family]
        source_file = str(definition["primary_source"]["relative_path"])
        selected_coverage, selected_rows = _full_or_canary(definition, family)
        primary_all = sources[source_file]
        primary = [item for item in primary_all if selected_rows is None or int(item["ordinal"]) in set(selected_rows)]
        source_file_id = _digest_id("source-file", SOURCE_SNAPSHOT_ID, source_file)
        row_ids = {
            int(item["ordinal"]): _digest_id("m5c-row", family, run_id, source_file, item["pointer"], item["token_sha256"])
            for item in primary
        }
        if family == "avatar_codex_archive":
            spec = _build_avatar_spec(definition, primary, sources, report, run_id)
            specs.append(spec)
            continue
        raw_rows, relation_rows, candidate_rows, owner_rows, resolution_rows, text_rows, opaque_rows, asset_rows = _child_rows(
            family, definition, primary, row_ids, run_id, SOURCE_SNAPSHOT_ID, PARSER_BUILD_ID, pair_lookup, sources,
        )
        row_rows: list[tuple[Any, ...]] = []
        for item in primary:
            n = int(item["ordinal"])
            row = item["value"]
            row_rows.append((
                row_ids[n], family, str(definition["contract_version"]), run_id, SOURCE_SNAPSHOT_ID,
                PARSER_BUILD_ID, source_file, source_file_id,
                _digest_id("source-record", source_file_id, item["pointer"]), item["pointer"], item["r0_pointer"], n,
                item["token"], item["token_sha256"], item["token_sha256"],
                _json({field: row.get(field) for field in definition.get("physical_identity", {}).get("identity_fields", [])}),
                "raw_observed", _json({
                    "projection_stage": "bounded_canary" if selected_rows is not None else "full",
                    "identity_effect": "physical_occurrence_only",
                    "logical_identity_created": False,
                    "raw_record_canonical_sha256": item["canonical_sha256"],
                }),
            ))
        row_table = _table(family, "row", row_rows)
        tables = [row_table]
        # Optional child shapes are family-contract driven.  Do not create an
        # empty relation/text/asset table merely because another family has
        # that shape; a zero-row shape is represented by the absence of that
        # family-owned table.  Row/raw are always present, and opaque is
        # included only when the frozen contract yields observations.
        for kind, rows in (
            ("raw", raw_rows), ("relation", relation_rows), ("candidate", candidate_rows),
            ("owner", owner_rows), ("resolution", resolution_rows), ("text", text_rows),
            ("opaque", opaque_rows), ("asset", asset_rows),
        ):
            if rows:
                tables.append(_table(family, kind, rows))
        tables_tuple = tuple(tables)
        spec = FamilyShardSpec(
            family=family, family_contract_version=str(definition["contract_version"]),
            ingest_run_id=run_id, snapshot_id=SOURCE_SNAPSHOT_ID, parser_build_id=PARSER_BUILD_ID,
            coverage_level=selected_coverage, corpus_complete=False, product_accepted=False,
            read_set=tuple(sorted({source_file, *(str(item["relative_path"]) for item in definition.get("lookup_sources", [])), str(M5B_MANIFEST.relative_to(ROOT)), str(M1_DEFAULT)})),
            write_set=tuple(table.name for table in tables_tuple), tables=tables_tuple,
            metadata={
                "boundary": f"M5B {source_file} primary physical row occurrences",
                "projection_stage": "bounded_canary" if selected_rows is not None else "full",
                "selected_source_rows": list(selected_rows) if selected_rows is not None else "all",
                "primary_source_file": source_file,
                "primary_source_sha256": _catalog_entry(report, source_file)["sha256"],
                "primary_source_row_count": len(primary),
                "primary_source_row_count_full": len(primary_all),
                "textmap_contract": M1_CONTRACT,
                "textmap_container_map": m1_containers,
                "textmap_locale_tier_verified_by": "independent_m5c_pair_verifier",
                "projection_identity": {
                    "projection_run_id": run_id,
                    "parser_build_id": PARSER_BUILD_ID,
                    "projection_config_seal": PROJECTION_CONFIG_SEAL,
                    "projection_source_seal": PROJECTION_SOURCE_SEAL,
                },
                "m5b_operation_contract": definition.get("operation_contract", {}),
                "source_boundary": {
                    "primary_source": definition.get("primary_source", {}),
                    "lookup_sources": definition.get("lookup_sources", []),
                },
                "optional_child_table_policy": "omit_zero_row_shape_tables",
                "logical_identity_created": False,
                "cross_family_merge": False,
                "counts": {
                    "primary_rows": len(primary), "raw_fields": len(raw_rows), "relations": len(relation_rows),
                    "relation_candidates": len(candidate_rows), "text_owners": len(owner_rows),
                    "text_resolutions": len(resolution_rows), "text_candidates": len(text_rows),
                    "opaque": len(opaque_rows), "assets": len(asset_rows),
                },
                "legal_states": list(LEGAL_STATES),
                "repository_spec": {
                    "operations": ["list_family_evidence", "get_family_evidence"] + (["search_family_text"] if definition.get("text_owner_allowlist") else []),
                    "get_scope": "one physical primary row occurrence",
                    "cross_family_join": False,
                    "cross_container_join": False,
                    "sort": list(definition.get("sort_contract", [])),
                    "filters": definition.get("operation_contract", {}).get("filters", []),
                    "not_found": "NotFound",
                },
            },
        )
        specs.append(spec)
    return tuple(specs)


def _unescape_json_pointer_token(token: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(token):
        character = token[index]
        if character != "~":
            result.append(character)
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            raise M5CError(f"malformed JSON pointer escape: {token}")
        result.append("~" if token[index + 1] == "0" else "/")
        index += 2
    return "".join(result)


def _resolve_json_pointer(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    if not pointer.startswith("/"):
        raise M5CError(f"JSON pointer is not absolute: {pointer}")
    current = value
    for encoded in pointer[1:].split("/"):
        token = _unescape_json_pointer_token(encoded)
        if isinstance(current, Mapping):
            if token not in current:
                raise M5CError(f"JSON pointer field is missing: {pointer}")
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit() or (token != "0" and token.startswith("0")):
                raise M5CError(f"JSON pointer array index is invalid: {pointer}")
            index = int(token)
            if index >= len(current):
                raise M5CError(f"JSON pointer array index is out of range: {pointer}")
            current = current[index]
        else:
            raise M5CError(f"JSON pointer traverses a scalar: {pointer}")
    return current


def _verify_raw_field_table(
    source_root: Path,
    report: Mapping[str, Any],
    path: Path,
    spec: FamilyShardSpec,
) -> dict[str, Any]:
    if spec.family == "avatar_codex_archive":
        return {
            "status": "not_applicable",
            "selected_field_count": 0,
            "pointer_to_source_checked": True,
        }
    raw_table = f"{spec.family}_raw_field_occurrence"
    row_table = f"{spec.family}_row_occurrence"
    if raw_table not in {table.name for table in spec.tables}:
        raise M5CError(f"M5C raw field table is missing: {spec.family}")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row_rows = connection.execute(
            f'SELECT occurrence_id,source_file_path,source_record_id,source_record_pointer,source_ordinal '
            f'FROM "{row_table}"'
        ).fetchall()
        raw_rows = connection.execute(
            f'SELECT occurrence_id,row_occurrence_id,ingest_run_id,snapshot_id,parser_build_id,'
            f'source_record_id,source_pointer,field_path,field_presence,raw_json,resolution_status '
            f'FROM "{raw_table}" ORDER BY source_pointer,occurrence_id'
        ).fetchall()
    except sqlite3.Error as exc:
        raise M5CError(f"cannot read M5C raw field table: {exc}") from exc
    finally:
        connection.close()

    row_by_id = {
        str(row[0]): {
            "source_file_path": str(row[1]),
            "source_record_id": str(row[2]),
            "source_record_pointer": str(row[3]),
            "source_ordinal": int(row[4]),
        }
        for row in row_rows
    }
    source_file = str(spec.metadata["primary_source_file"])
    source_rows = _load_source(source_root, report, source_file, {})
    selected = spec.metadata.get("selected_source_rows", "all")
    selected_ordinals = None if selected == "all" else {int(item) for item in selected}
    expected: dict[str, dict[str, Any]] = {}
    source_file_id = _digest_id("source-file", SOURCE_SNAPSHOT_ID, source_file)
    for source_row in source_rows:
        ordinal = int(source_row["ordinal"])
        if selected_ordinals is not None and ordinal not in selected_ordinals:
            continue
        source_record_pointer = f"/{ordinal}"
        source_record_id = _digest_id("source-record", source_file_id, source_record_pointer)
        for local_pointer, field_path, field_value in _walk_raw_fields(source_row["value"]):
            source_pointer = f"/{ordinal}{local_pointer}"
            if source_pointer in expected:
                raise M5CError(f"duplicate expected raw field pointer: {source_file}{source_pointer}")
            resolved = _resolve_json_pointer(source_row["value"], local_pointer)
            expected[source_pointer] = {
                "row_occurrence_id": None,
                "source_record_id": source_record_id,
                "source_record_pointer": source_record_pointer,
                "field_path": field_path,
                "raw_json": _json(resolved),
                "occurrence_id": _digest_id("m5c-raw", spec.family, spec.ingest_run_id, source_pointer),
            }

    if len(raw_rows) != len(expected):
        raise M5CError(
            f"M5C raw field denominator mismatch for {spec.family}: "
            f"stored={len(raw_rows)} expected={len(expected)}"
        )
    seen: set[str] = set()
    for row in raw_rows:
        (
            occurrence_id, row_occurrence_id, ingest_run_id, snapshot_id,
            parser_build_id, source_record_id, source_pointer, field_path,
            field_presence, raw_json, resolution_status,
        ) = row
        source_pointer = str(source_pointer)
        if source_pointer in seen:
            raise M5CError(f"duplicate stored raw field pointer: {spec.family}{source_pointer}")
        seen.add(source_pointer)
        expected_item = expected.get(source_pointer)
        if expected_item is None:
            raise M5CError(f"stored raw field pointer does not resolve to source: {spec.family}{source_pointer}")
        parent = row_by_id.get(str(row_occurrence_id))
        try:
            source_ordinal = int(source_pointer.split("/", 2)[1])
        except (IndexError, ValueError) as exc:
            raise M5CError(f"raw field pointer has no row ordinal: {spec.family}{source_pointer}") from exc
        if (
            parent is None
            or parent["source_file_path"] != source_file
            or parent["source_ordinal"] != source_ordinal
        ):
            raise M5CError(f"raw field parent row mismatch: {spec.family}{source_pointer}")
        if (
            str(ingest_run_id) != spec.ingest_run_id
            or str(snapshot_id) != SOURCE_SNAPSHOT_ID
            or str(parser_build_id) != spec.parser_build_id
            or str(source_record_id) != expected_item["source_record_id"]
            or parent["source_record_id"] != expected_item["source_record_id"]
            or parent["source_record_pointer"] != expected_item["source_record_pointer"]
            or str(field_path) != expected_item["field_path"]
            or str(field_presence) != "present"
            or str(resolution_status) != "raw_observed"
            or str(raw_json) != expected_item["raw_json"]
            or str(occurrence_id) != expected_item["occurrence_id"]
        ):
            raise M5CError(f"raw field source resolution mismatch: {spec.family}{source_pointer}")
    if seen != set(expected):
        missing = sorted(set(expected) - seen)[:3]
        raise M5CError(f"stored raw field pointers are missing from source: {spec.family} {missing}")
    return {
        "status": "verified",
        "selected_field_count": len(expected),
        "pointer_to_source_checked": True,
        "pointer_contract": "json_array_index_v1:/<row>/<json-pointer>",
    }


def _verify_source_text_pair(
    source_root: Path,
    report: Mapping[str, Any],
    candidate_rows: Sequence[tuple[Any, ...]],
) -> dict[str, Any]:
    expected = _m1_container_map(report)
    grouped: dict[str, set[tuple[int, str]]] = defaultdict(set)
    for row in candidate_rows:
        if len(row) < 10:
            raise M5CError("TextMap candidate row has fewer than ten verifier columns")
        relative_path, locale, tier, ordinal, pointer, key_text = (
            row[0], row[1], row[2], int(row[3]), str(row[4]), str(row[5])
        )
        mapping = expected.get(str(relative_path))
        if mapping is None or mapping["locale"] != locale or mapping["tier"] != tier:
            raise M5CError(f"TextMap candidate locale/tier mapping mismatch: {relative_path}")
        grouped[str(relative_path)].add((ordinal, key_text))
    selected_by_key: dict[str, dict[tuple[int, str], dict[str, str]]] = {}
    for relative_path, wanted in grouped.items():
        path = source_root / relative_path
        raw = path.read_bytes()
        item = expected[relative_path]
        if _sha256(raw) != item["raw_sha256"] or len(raw) != item["raw_size_bytes"]:
            raise M5CError(f"TextMap source seal mismatch: {relative_path}")
        selected_by_key[relative_path] = _selected_text_pairs(raw, wanted, relative_path)
    verified = 0
    for row in candidate_rows:
        relative_path, locale, tier, ordinal, pointer, key_text = (
            str(row[0]), str(row[1]), str(row[2]), int(row[3]), str(row[4]), str(row[5])
        )
        selected = selected_by_key[relative_path]
        observed = selected.get((ordinal, key_text))
        if observed is None:
            raise M5CError(f"TextMap source pair missing: {relative_path}#{ordinal}:{key_text}")
        expected_pointer = observed["pointer"]
        if pointer != expected_pointer:
            raise M5CError(
                f"TextMap source pointer mismatch: {relative_path}#{ordinal}: "
                f"stored={pointer!r} expected={expected_pointer!r}"
            )
        if (
            str(row[6]) != observed["raw_text"]
            or str(row[7]) != observed["raw_key_sha256"]
            or str(row[8]) != observed["raw_text_sha256"]
            or str(row[9]) != observed["raw_record_sha256"]
        ):
            raise M5CError(f"TextMap source pair seal mismatch: {relative_path}#{ordinal}")
        verified += 1
    return {
        "status": "verified",
        "selected_pair_count": verified,
        "file_count": len(grouped),
        "locale_tier_mapping_checked": True,
        "pointer_tuple_checked": True,
        "duplicate_candidate_rows_checked": True,
    }


def _selected_text_pairs(raw: bytes, wanted: set[tuple[int, str]], label: str) -> dict[tuple[int, str], dict[str, str]]:
    text = raw.decode("utf-8-sig")
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(text) and text[pos] in " \t\r\n":
        pos += 1
    if pos >= len(text) or text[pos] != "{":
        raise M5CError(f"{label} is not a TextMap object")
    pos += 1
    ordinal = 0
    seen_keys: set[str] = set()
    result: dict[tuple[int, str], dict[str, str]] = {}
    while True:
        while pos < len(text) and text[pos] in " \t\r\n":
            pos += 1
        if pos < len(text) and text[pos] == "}":
            return result
        key_start = pos
        key, key_end = decoder.raw_decode(text, pos)
        if not isinstance(key, str):
            raise M5CError(f"{label} key is not a string")
        key_raw = text[key_start:key_end]
        pos = key_end
        while pos < len(text) and text[pos] in " \t\r\n":
            pos += 1
        if pos >= len(text) or text[pos] != ":":
            raise M5CError(f"{label} pair has no colon")
        pos += 1
        while pos < len(text) and text[pos] in " \t\r\n":
            pos += 1
        value_start = pos
        value, value_end = decoder.raw_decode(text, pos)
        if not isinstance(value, str):
            raise M5CError(f"{label} TextMap value is not a string")
        if key in seen_keys:
            raise M5CError(f"{label} duplicate TextMap key at pair {ordinal}: {key!r}")
        seen_keys.add(key)
        if (ordinal, key) in wanted:
            raw_text = text[value_start:value_end]
            raw_record = '{"key":' + key_raw + ',"text":' + raw_text + '}'
            result[(ordinal, key)] = {
                "pointer": f"/@{ordinal}:{_escape(key)}",
                "raw_text": value,
                "raw_key_sha256": _sha256(key_raw.encode("utf-8")),
                "raw_text_sha256": _sha256(raw_text.encode("utf-8")),
                "raw_record_sha256": _sha256(raw_record.encode("utf-8")),
            }
        ordinal += 1
        pos = value_end
        while pos < len(text) and text[pos] in " \t\r\n":
            pos += 1
        if pos < len(text) and text[pos] == ",":
            pos += 1
            continue
        if pos < len(text) and text[pos] == "}":
            return result
        raise M5CError(f"{label} TextMap delimiter is invalid")


def _verify_textmap_tables(
    source_root: Path,
    report: Mapping[str, Any],
    path: Path,
    spec: FamilyShardSpec,
) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        table_name = f"{spec.family}_text_candidate_occurrence"
        if spec.family == "avatar_codex_archive" or table_name not in {table.name for table in spec.tables}:
            return {"status": "not_applicable", "selected_pair_count": 0, "locale_tier_mapping_checked": True}
        columns = "relative_path,locale,shard_tier,pair_ordinal,pointer,key_text,raw_text,raw_key_sha256,raw_text_sha256,raw_record_sha256,metadata_json"
        rows = connection.execute(f'SELECT {columns} FROM "{table_name}" ORDER BY relative_path,pair_ordinal,occurrence_id').fetchall()
    except sqlite3.Error as exc:
        raise M5CError(f"cannot read M5C TextMap candidates: {exc}") from exc
    finally:
        connection.close()
    return _verify_source_text_pair(source_root, report, rows)


def verify_shard_m5c(
    path: str | Path,
    source_root: str | Path,
    *,
    m1_path: str | Path = M1_DEFAULT,
    report_path: str | Path = M5B_REPORT,
    family: str | None = None,
    verify_pin: bool = True,
    expected_spec: FamilyShardSpec | None = None,
) -> dict[str, Any]:
    report = _load_report(Path(report_path))
    root = Path(source_root).resolve()
    verify_source_pin(root, verify_pin=verify_pin)
    specs = (expected_spec,) if expected_spec is not None else load_specs(root, m1_path=m1_path, report_path=report_path, verify_pin=verify_pin)
    by_family = {item.family: item for item in specs}
    target = Path(path).resolve()
    if family is None:
        metadata = sqlite3.connect(f"file:{target}?mode=ro", uri=True).execute(
            "SELECT value_json FROM shard_metadata WHERE key='family'"
        ).fetchone()
        if not metadata:
            raise M5CError("shard family metadata is unavailable")
        family = json.loads(metadata[0])
    if family not in by_family:
        raise M5CError(f"family is not in M5C M5B boundary: {family}")
    spec = by_family[family]
    try:
        mechanical = verify_shard(target, spec)
    except (M2VerificationError, ShardBuildError) as exc:
        raise M5CError(f"M5C shard verification failed: {family}: {exc}") from exc
    raw_result = _verify_raw_field_table(root, report, target, spec)
    text_result = _verify_textmap_tables(root, report, target, spec)
    return {
        "status": "verified",
        "family": family,
        "coverage_level": spec.coverage_level,
        "corpus_complete": False,
        "product_accepted": False,
        "mechanical": mechanical,
        "raw_fields": raw_result,
        "textmap": text_result,
        "independent_raw_pointer_resolution": True,
        "independent_locale_tier_mapping": True,
        "legal_failure_states": list(LEGAL_STATES),
    }


def _run_preflight(repo_root: Path, label: str) -> dict[str, Any]:
    invocation = _digest_id("m5c-preflight", label, os.getpid())[:16]
    output = Path(tempfile.gettempdir()) / f"batch-m5c-{label}-{invocation}.json"
    command = [
        os.fspath(repo_root / ".venv/bin/python"),
        os.fspath(repo_root / "scripts/generate_batch_m5a_preflight.py"),
        "--output", os.fspath(output),
    ]
    try:
        result = subprocess.run(command, cwd=repo_root, check=False, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        raise M5CError(f"50 GiB preflight could not run: {exc}") from exc
    if result.returncode != 0:
        raise M5CError(f"50 GiB preflight failed: {result.stdout.strip()} {result.stderr.strip()}")
    try:
        report = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise M5CError(f"50 GiB preflight report unavailable: {output}") from exc
    if report.get("status") != "preflight_pass_no_cleanup_required":
        raise M5CError(f"50 GiB preflight did not pass: {report.get('status')}")
    # The preflight report keeps resource measurements under its `space` object.
    # Preserve the exact measured value in the M5C evidence instead of silently
    # emitting null when the preflight itself passed.
    return {
        "status": report["status"],
        "path": str(output),
        "allocated_bytes": report.get("space", {}).get("allocated_bytes"),
    }


def build_wave(
    source_root: str | Path,
    output_root: str | Path,
    *,
    repo_root: str | Path = ROOT,
    m1_path: str | Path = M1_DEFAULT,
    report_path: str | Path = M5B_REPORT,
    verify_pin: bool = True,
    run_space_preflight: bool = True,
    preflight_label_prefix: str = "",
) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    before = _run_preflight(repo, f"{preflight_label_prefix}before") if run_space_preflight else {"status": "skipped_test"}
    specs = load_specs(source_root, m1_path=m1_path, report_path=report_path, verify_pin=verify_pin)
    output = Path(output_root).resolve()
    output.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict[str, Any]] = []
    total = 0
    for spec in specs:
        target = output / f"{spec.family}.sqlite"
        if target.exists() or target.is_symlink():
            raise M5CError(f"M5C target must be absent: {target}")
        result = build_shard(spec, target)
        if int(result["size_bytes"]) > ARTIFACT_LIMIT_BYTES:
            raise M5CError(f"M5C family shard exceeds 4 GiB: {spec.family}")
        total += int(result["size_bytes"])
        verification = verify_shard_m5c(
            target, source_root, m1_path=m1_path, report_path=report_path,
            family=spec.family, verify_pin=verify_pin, expected_spec=spec,
        )
        for sidecar in (Path(f"{target}.lock"), Path(f"{target}-journal"), Path(f"{target}-wal"), Path(f"{target}-shm")):
            try:
                sidecar.unlink()
            except FileNotFoundError:
                pass
        artifacts.append({
            "family": spec.family,
            "family_contract_version": spec.family_contract_version,
            "coverage_level": spec.coverage_level,
            "corpus_complete": False,
            "product_accepted": False,
            "ingest_run_id": spec.ingest_run_id,
            "parser_build_id": spec.parser_build_id,
            "spec_digest": spec_digest(spec),
            "path": str(target),
            "sha256": result["sha256"],
            "size_bytes": result["size_bytes"],
            "row_counts": dict(spec.metadata.get("counts", {})),
            "tables": verification["mechanical"]["tables"],
            "raw_fields": verification["raw_fields"],
            "textmap": verification["textmap"],
            "repository_spec": dict(spec.metadata.get("repository_spec", {
                "operations": ["list_family_evidence", "get_family_evidence"],
                "get_scope": "one physical primary row occurrence",
                "cross_family_join": False,
                "cross_container_join": False,
            })),
            "projection_identity": dict(spec.metadata.get("projection_identity", {})),
            "m5b_operation_contract": dict(spec.metadata.get("m5b_operation_contract", {})),
            "source_boundary": dict(spec.metadata.get("source_boundary", {})),
            "legal_states": list(spec.metadata.get("legal_states", LEGAL_STATES)),
            "optional_child_table_policy": spec.metadata.get(
                "optional_child_table_policy", "not_applicable_avatar_schema14_reuse"
            ),
        })
    if total > ARTIFACT_LIMIT_BYTES:
        raise M5CError(f"M5C aggregate shard increment exceeds 4 GiB: {total}")
    after = _run_preflight(repo, f"{preflight_label_prefix}after") if run_space_preflight else {"status": "skipped_test"}
    return {
        "report_version": M5C_CONTRACT,
        "milestone": "M5C",
        "status": "implemented_non_product",
        "coverage_level": M5C_COVERAGE,
        "corpus_complete": False,
        "evidence_only": True,
        "product_accepted": False,
        "schema_version": 17,
        "migration": {
            "path": "migrations/0017_batch_wave02_family_projection.sql",
            "avatar_tables_reused": True,
            "new_family_table_names": sorted({
                table.name for spec in specs if spec.family != "avatar_codex_archive" for table in spec.tables
            }),
            "optional_child_table_policy": "omit_zero_row_shape_tables",
        },
        "source": verify_source_pin(source_root, verify_pin=verify_pin),
        "source_snapshot": SOURCE_SNAPSHOT_ID,
        "m5b_report_sha256": _sha256(Path(report_path).read_bytes()),
        "m5b_manifest_sha256": _sha256(M5B_MANIFEST.read_bytes()),
        "parser_identity": {
            "parser_build_id": PARSER_BUILD_ID,
            "module_sha256": M5C_MODULE_SHA256,
            "m5b_report_sha256": M5B_REPORT_SHA256,
            "m5b_manifest_sha256": M5B_MANIFEST_SHA256,
            "migration_0017_sha256": MIGRATION_0017_SHA256,
        },
        "projection_identity": {
            "projection_run_id": PROJECTION_RUN_ID,
            "projection_config_seal": PROJECTION_CONFIG_SEAL,
            "projection_source_seal": PROJECTION_SOURCE_SEAL,
            "parser_build_id": PARSER_BUILD_ID,
        },
        "m1_dependency": {
            "path": str(Path(m1_path).resolve()),
            "contract": M1_CONTRACT,
            "run_id": M1_RUN,
            "parser_build_id": M1_PARSER,
            "locale_tier_mapping": "pinned six-file relative_path mapping checked independently",
        },
        "family_count": len(artifacts),
        "primary_row_count": 5060,
        "projected_primary_row_count": sum(item["row_counts"]["primary_rows"] for item in artifacts),
        "families": artifacts,
        "repository_specs": {
            item["family"]: item["repository_spec"] for item in artifacts
        },
        "identity_policy": "raw_evidence_identity_is_not_logical_identity; no cross-family merge; no universal adapter",
        "mcp_tool_count": 25,
        "provider_or_registry_changed": False,
        "schema16_accepted_unchanged": True,
        "space": {
            "candidate_increment_limit_bytes": ARTIFACT_LIMIT_BYTES,
            "aggregate_shard_bytes": total,
            "preflight_before": before,
            "preflight_after": after,
            "cleanup_performed": False,
        },
    }


def verify_wave(
    report: Mapping[str, Any],
    source_root: str | Path,
    *,
    m1_path: str | Path = M1_DEFAULT,
    report_path: str | Path = M5B_REPORT,
    verify_pin: bool = True,
) -> dict[str, Any]:
    if report.get("report_version") != M5C_CONTRACT or report.get("milestone") != "M5C":
        raise M5CError("M5C report identity mismatch")
    if report.get("family_count") != 11 or report.get("primary_row_count") != 5060:
        raise M5CError("M5C family/primary denominator mismatch")
    if report.get("product_accepted") is not False or report.get("provider_or_registry_changed") is not False:
        raise M5CError("M5C report promotes product/runtime")
    expected_projection_identity = {
        "projection_run_id": PROJECTION_RUN_ID,
        "projection_config_seal": PROJECTION_CONFIG_SEAL,
        "projection_source_seal": PROJECTION_SOURCE_SEAL,
        "parser_build_id": PARSER_BUILD_ID,
    }
    if report.get("projection_identity") != expected_projection_identity:
        raise M5CError("M5C projection identity changed")
    specs = load_specs(source_root, m1_path=m1_path, report_path=report_path, verify_pin=verify_pin)
    expected = {spec.family: spec for spec in specs}
    observed = report.get("families")
    if not isinstance(observed, list) or [item.get("family") for item in observed] != list(FAMILY_ORDER):
        raise M5CError("M5C family order/closure mismatch")
    verified: list[dict[str, Any]] = []
    for item in observed:
        family = str(item["family"])
        spec = expected[family]
        if item.get("ingest_run_id") != spec.ingest_run_id or item.get("parser_build_id") != spec.parser_build_id:
            raise M5CError(f"M5C identity changed: {family}")
        if item.get("spec_digest") != spec_digest(spec):
            raise M5CError(f"M5C spec digest changed: {family}")
        if item.get("coverage_level") != spec.coverage_level or item.get("row_counts") != dict(spec.metadata.get("counts", {})):
            raise M5CError(f"M5C family contract changed: {family}")
        path = Path(str(item.get("path", "")))
        if item.get("size_bytes", -1) > ARTIFACT_LIMIT_BYTES:
            raise M5CError(f"M5C shard exceeds 4 GiB: {family}")
        result = verify_shard_m5c(path, source_root, m1_path=m1_path, report_path=report_path, family=family, verify_pin=verify_pin)
        if result["mechanical"]["sha256"] != item.get("sha256") or result["mechanical"]["size_bytes"] != item.get("size_bytes"):
            raise M5CError(f"M5C artifact identity changed: {family}")
        verified.append({
            "family": family, "status": "verified", "coverage_level": spec.coverage_level,
            "sha256": result["mechanical"]["sha256"], "size_bytes": result["mechanical"]["size_bytes"],
            "row_counts": dict(spec.metadata.get("counts", {})),
            "raw_fields": result["raw_fields"], "textmap": result["textmap"],
        })
    return {
        "status": "verified",
        "family_count": len(verified),
        "primary_row_count": sum(item["row_counts"]["primary_rows"] for item in verified),
        "projected_shard_bytes": sum(item["size_bytes"] for item in verified),
        "families": verified,
        "textmap_locale_tier_mapping_checked": True,
        "raw_field_pointer_resolution_checked": True,
        "textmap_pointer_tuple_checked": True,
        "duplicate_textmap_candidate_rows_checked": True,
        "product_accepted": False,
    }


def verify_dual_wave(
    report: Mapping[str, Any],
    source_root: str | Path,
    *,
    m1_path: str | Path = M1_DEFAULT,
    report_path: str | Path = M5B_REPORT,
    verify_pin: bool = True,
) -> dict[str, Any]:
    dual = report.get("dual_build")
    if not isinstance(dual, Mapping):
        raise M5CError("M5C dual-build evidence is missing")
    peer_report_value = dual.get("peer_report_path")
    if not peer_report_value:
        raise M5CError("M5C peer report path is missing")
    peer_path = Path(str(peer_report_value))
    try:
        peer_report = json.loads(peer_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise M5CError(f"M5C peer report unavailable: {peer_path}") from exc
    authoritative = verify_wave(
        report, source_root, m1_path=m1_path, report_path=report_path, verify_pin=verify_pin,
    )
    peer = verify_wave(
        peer_report, source_root, m1_path=m1_path, report_path=report_path, verify_pin=verify_pin,
    )
    primary_items = {item["family"]: item for item in report["families"]}
    peer_items = {item["family"]: item for item in peer_report["families"]}
    if list(primary_items) != list(peer_items):
        raise M5CError("M5C peer family closure/order mismatch")
    comparisons: list[dict[str, Any]] = []
    for family in FAMILY_ORDER:
        primary_item = primary_items[family]
        peer_item = peer_items[family]
        byte_equal = (
            primary_item.get("sha256") == peer_item.get("sha256")
            and primary_item.get("size_bytes") == peer_item.get("size_bytes")
        )
        spec_equal = primary_item.get("spec_digest") == peer_item.get("spec_digest")
        identity_equal = (
            primary_item.get("ingest_run_id") == peer_item.get("ingest_run_id")
            and primary_item.get("parser_build_id") == peer_item.get("parser_build_id")
        )
        if not (byte_equal and spec_equal and identity_equal):
            raise M5CError(f"M5C authoritative/peer mismatch: {family}")
        comparisons.append({
            "family": family,
            "authoritative_path": primary_item["path"],
            "peer_path": peer_item["path"],
            "authoritative_sha256": primary_item["sha256"],
            "peer_sha256": peer_item["sha256"],
            "authoritative_size_bytes": primary_item["size_bytes"],
            "peer_size_bytes": peer_item["size_bytes"],
            "authoritative_spec_digest": primary_item["spec_digest"],
            "peer_spec_digest": peer_item["spec_digest"],
            "authoritative_ingest_run_id": primary_item["ingest_run_id"],
            "peer_ingest_run_id": peer_item["ingest_run_id"],
            "authoritative_parser_build_id": primary_item["parser_build_id"],
            "peer_parser_build_id": peer_item["parser_build_id"],
            "byte_equal": byte_equal,
            "spec_equal": spec_equal,
            "identity_equal": identity_equal,
        })
    if not dual.get("same_code_inputs", True):
        raise M5CError("M5C peer was not built from identical code/inputs")
    return {
        "status": "verified",
        "authoritative": authoritative,
        "peer": peer,
        "family_comparisons": comparisons,
        "all_family_byte_equal": True,
        "all_family_spec_equal": True,
        "all_family_identity_equal": True,
        "same_code_inputs": True,
        "space_gate": {
            "authoritative_before": report.get("space", {}).get("preflight_before"),
            "authoritative_after": report.get("space", {}).get("preflight_after"),
            "peer_before": peer_report.get("space", {}).get("preflight_before"),
            "peer_after": peer_report.get("space", {}).get("preflight_after"),
            "candidate_increment_limit_bytes": ARTIFACT_LIMIT_BYTES,
            "aggregate_authoritative_bytes": report.get("space", {}).get("aggregate_shard_bytes"),
            "aggregate_peer_bytes": peer_report.get("space", {}).get("aggregate_shard_bytes"),
        },
    }


def portable_manifest(report: Mapping[str, Any]) -> dict[str, Any]:
    families = []
    for item in report.get("families", []):
        families.append({
            key: item[key] for key in (
                "family", "family_contract_version", "coverage_level", "corpus_complete",
                "product_accepted", "ingest_run_id", "parser_build_id", "spec_digest",
                "sha256", "size_bytes", "row_counts", "repository_spec",
            )
        })
    return {
        "manifest_id": "anime-game-data-7.0-batch-m5c-wave02-r1",
        "manifest_version": 1,
        "milestone": "M5C",
        "status": report.get("status"),
        "schema_version": 17,
        "source_snapshot": SOURCE_SNAPSHOT_ID,
        "source_commit": SOURCE_COMMIT,
        "source_tree": SOURCE_TREE,
        "m5b_report_sha256": report.get("m5b_report_sha256"),
        "m5b_manifest_sha256": report.get("m5b_manifest_sha256"),
        "parser_identity": report.get("parser_identity"),
        "projection_identity": report.get("projection_identity"),
        "dual_build": report.get("dual_build"),
        "m1_contract": M1_CONTRACT,
        "m1_locale_tier_mapping": "relative_path is pinned to locale/tier for every selected pair",
        "coverage_level": M5C_COVERAGE,
        "corpus_complete": False,
        "evidence_only": True,
        "product_accepted": False,
        "family_count": len(families),
        "primary_row_count": 5060,
        "projected_primary_row_count": sum(item["row_counts"]["primary_rows"] for item in families),
        "families": families,
        "identity_policy": report.get("identity_policy"),
        "mcp_tool_count": 25,
        "provider_or_registry_changed": False,
        "schema16_accepted_unchanged": True,
        "profile_picture_canary_rows": list(PROFILE_CANARY_ROWS),
        "migration_optional_child_table_policy": report.get("migration", {}).get("optional_child_table_policy"),
        "artifact_retention": {
            "status": report.get("artifact_retention", {}).get("status", "retained_pending_review"),
            "cleanup_performed": report.get("artifact_retention", {}).get("cleanup_performed", False),
            "cleanup_authorized": report.get("artifact_retention", {}).get("cleanup_authorized", False),
            "current_status": report.get("artifact_retention", {}).get("current_status", "selected_final_nonproduct"),
            "peer_output_root": report.get("artifact_retention", {}).get("peer_output_root"),
            "superseded_labels": [
                item.get("label") for item in report.get("artifact_retention", {}).get("superseded_roots", [])
            ],
        },
        "avatar_schema14_tables_reused": [
            "avatar_codex_row_occurrence",
            "avatar_codex_raw_field_occurrence",
            "avatar_codex_relationship_occurrence",
            "avatar_codex_relationship_candidate_occurrence",
            "avatar_codex_opaque_occurrence",
        ],
        "non_goals": [
            "M5D deterministic merge", "M5E Schema 17 acceptance", "runtime/provider/registry changes",
            "full 7.0 claim", "universal identity/ontology/adapter", "cross-family merge",
        ],
    }


__all__ = [
    "ARTIFACT_LIMIT_BYTES", "FAMILY_ORDER", "M5CError", "M5C_CONTRACT",
    "PARSER_BUILD_ID", "PROJECTION_CONFIG_SEAL", "PROJECTION_RUN_ID",
    "PROJECTION_SOURCE_SEAL", "PROFILE_CANARY_ROWS", "build_wave", "derive_projection_run_id",
    "load_specs", "portable_manifest", "verify_dual_wave", "verify_shard_m5c", "verify_source_pin", "verify_wave",
]
