"""Build one exact, family-local 7.1 M3 v1+v2 candidate artifact.

The family and both contracts are explicit. TextMap lookup is snapshot-local;
neither native values nor equal text create a cross-snapshot identity.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from . import batch_m3_r1_projection as physical_array_reader
from . import batch_m1_textmap as lexical_textmap_reader
from .batch_m3_r1_projection import _iter_array_records
from .batch_m1_textmap import iter_textmap_pairs


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "schemas/m3-7.1-provider-family-v1.sql"
CONTRACT = ROOT / "manifests/m3-7.1-provider/input-contract-v1.json"
SNAPSHOT = "anime-game-data:7.1:9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
SOURCE_COMMIT = SNAPSHOT.rsplit(":", 1)[1]
SOURCE_TREE = "aa578c08c402af39b41f1ffc95124d10f5410272"
LOCALES = ("CHS", "EN", "JP")
TABLES = ("source_file", "v1_row", "v2_row", "v2_owner", "v2_resolution", "v2_candidate", "release")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(*parts: Any) -> str:
    return hashlib.sha256(canonical(parts).encode()).hexdigest()


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "--no-optional-locks", "-C", str(root), *args], text=True).strip()


def qualify_source(source: Path, family: dict, contract: dict) -> dict[str, bytes]:
    if (_git(source, "rev-parse", "HEAD"), _git(source, "rev-parse", "HEAD^{tree}")) != (SOURCE_COMMIT, SOURCE_TREE):
        raise ValueError("exact 7.1 source checkout required")
    if _git(source, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("7.1 source checkout is dirty")
    if contract["source_snapshot"] != SNAPSHOT:
        raise ValueError("frozen input snapshot differs")
    identities = {family["source_path"]: family["source_identity"]}
    identities.update({path: contract["textmap_sources"][path] for path in family["v2_textmap_paths"]})
    result = {}
    for relative, frozen in sorted(identities.items()):
        path = source / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError("source path absent or symlink: " + relative)
        raw = path.read_bytes()
        if (len(raw), sha(raw), _git(source, "rev-parse", "HEAD:" + relative)) != (
            frozen["size_bytes"], frozen["sha256"], frozen["git_blob_oid"]
        ):
            raise ValueError("source file identity drift: " + relative)
        result[relative] = raw
    return result


def _candidate_map(raw_files: dict[str, bytes], family: dict, hashes: set[str]) -> dict[tuple[str, str], list[tuple]]:
    matched: dict[tuple[str, str], list[tuple]] = defaultdict(list)
    for relative in family["v2_textmap_paths"]:
        suffix = relative.rsplit("/", 1)[1]
        tier = "medium" if "_Medium" in suffix else "main"
        locale = next((value for value in LOCALES if suffix.endswith(value + ".json")), None)
        if locale is None:
            raise ValueError("unknown TextMap locale: " + relative)
        for item in iter_textmap_pairs(raw_files[relative]):
            if item.key_text in hashes:
                matched[(locale, item.key_text)].append((
                    tier, relative, item.pair_ordinal, "/", item.pointer,
                    item.raw_key_json, item.raw_text_json, item.raw_text,
                    item.raw_key_sha256, item.raw_text_sha256, item.raw_record_sha256,
                    sha(item.raw_text.encode()),
                ))
    for values in matched.values():
        values.sort(key=lambda row: (row[1], row[2]))
    return matched


def _owned_output(path: Path) -> tuple[sqlite3.Connection, tuple[int, int]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    stat = os.fstat(fd)
    os.close(fd)
    owner = (stat.st_dev, stat.st_ino)
    conn = None
    try:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA page_size=4096")
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA.read_text())
        return conn, owner
    except Exception:
        if conn is not None:
            conn.close()
        _remove_if_owned(path, owner)
        raise


def _remove_if_owned(path: Path, owner: tuple[int, int] | None) -> None:
    if owner is not None and path.exists():
        stat = path.stat()
        if (stat.st_dev, stat.st_ino) == owner:
            path.unlink()


def build(source_root: str | Path, family_name: str, output_path: str | Path,
          contract_path: str | Path = CONTRACT) -> dict:
    source = Path(source_root).resolve()
    output = Path(output_path).resolve()
    frozen_raw = Path(contract_path).read_bytes()
    frozen = json.loads(frozen_raw)
    if frozen.get("contract") != "m3-7.1-ten-family-provider-input-v1":
        raise ValueError("M3 frozen input contract mismatch")
    family = next((item for item in frozen["families"] if item["family"] == family_name), None)
    if family is None:
        raise ValueError("undeclared M3 family")
    files = qualify_source(source, family, frozen)
    source_path = family["source_path"]
    source_rows = list(_iter_array_records(files[source_path]))
    if len(source_rows) != family["source_physical_rows"]:
        raise ValueError("family physical row denominator changed")
    key_fields = set(family["key_observation_fields"])
    text_fields = set(family["v2_text_fields"])
    observed = Counter()
    hashes: set[str] = set()
    for _ordinal, _pointer, _token, row_pairs, _raw_sha in source_rows:
        if row_pairs is None:
            raise ValueError("M3 physical row is not an object")
        for field, value in row_pairs:
            if field in key_fields | text_fields:
                if type(value) is not int:
                    raise ValueError("declared source field is not integer: " + field)
                observed[field] += 1
                if field in text_fields:
                    hashes.add(str(value))
    for field, expected in family["consumed_field_observations"].items():
        if observed[field] != expected["occurrences"] or expected["types"] != {"int": expected["occurrences"]}:
            raise ValueError("declared field physical denominator/type changed: " + field)
    candidates = _candidate_map(files, family, hashes) if text_fields else {}
    schema_raw = SCHEMA.read_bytes()
    builder_sha = file_sha(Path(__file__))
    contract_sha = sha(frozen_raw)
    helper_shas = {
        "physical_array_reader": file_sha(Path(physical_array_reader.__file__)),
        "textmap_lexical_reader": file_sha(Path(lexical_textmap_reader.__file__)),
    }
    textmap_observation = None
    if family["v2_textmap_paths"]:
        textmap_parser = "m3-71-textmap-parser:" + identity(
            "six-file-lexical-textmap-v1", helper_shas["textmap_lexical_reader"], sha(schema_raw)
        )[:40]
        textmap_run = "m3-71-textmap-run:" + identity(
            SNAPSHOT, textmap_parser,
            [(path, frozen["textmap_sources"][path]["sha256"])
             for path in family["v2_textmap_paths"]],
        )[:40]
        textmap_observation = {"snapshot_id": SNAPSHOT, "parser_build_id": textmap_parser,
                               "ingest_run_id": textmap_run,
                               "scope": "family-local exact six-file TextMap observation; no separate shard"}
    config = canonical({"family": family_name, "source": source_path,
                        "keys": family["key_observation_fields"], "text": family["v2_text_fields"],
                        "v1": family["v1_contract"], "v2": family["v2_contract"]})
    parsers = {version: "m3-7.1-parser:" + identity("parser-v1", version, builder_sha,
                                                      helper_shas, sha(schema_raw),
                                                      sha(config.encode()))[:40]
               for version in ("v1", "v2")}
    runs = {version: "m3-7.1-run:" + identity("run-v1", SNAPSHOT, family_name, version,
                                               parsers[version], contract_sha,
                                               family["source_identity"]["sha256"],
                                               [(path, frozen["textmap_sources"][path]["sha256"])
                                                for path in family["v2_textmap_paths"]] if version == "v2" else [])[:40]
            for version in ("v1", "v2")}
    conn = None
    owner = None
    try:
        conn, owner = _owned_output(output)
        for relative in sorted(files):
            entry = family["source_identity"] if relative == source_path else frozen["textmap_sources"][relative]
            conn.execute("INSERT INTO source_file VALUES(?,?,?,?,?)",
                         (relative, "primary" if relative == source_path else "textmap",
                          entry["size_bytes"], entry["sha256"], entry["git_blob_oid"]))
        counts = Counter({key: 0 for key in ("v1_rows", "v2_rows", "text_owners",
                                             "text_resolutions", "text_candidates")})
        for ordinal, pointer, token, row_pairs, token_sha in source_rows:
            v1_id = "m3-71-v1:" + identity(family_name, runs["v1"], source_path, pointer, token_sha)[:40]
            v2_id = "m3-71-v2:" + identity(family_name, runs["v2"], source_path, pointer, token_sha)[:40]
            key_observations = [{"field": key, "pair_ordinal": index, "raw_value": value}
                                for index, (key, value) in enumerate(row_pairs) if key in key_fields]
            conn.execute("INSERT INTO v1_row VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (v1_id, SNAPSHOT, parsers["v1"], runs["v1"], source_path, ordinal,
                          pointer, token, token_sha, canonical(key_observations)))
            conn.execute("INSERT INTO v2_row VALUES(?,?,?,?,?,?,?,?,?)",
                         (v2_id, SNAPSHOT, parsers["v2"], runs["v2"], source_path, ordinal,
                          pointer, token, token_sha))
            counts["v1_rows"] += 1
            counts["v2_rows"] += 1
            for pair_ordinal, (field, value) in enumerate(row_pairs):
                if field not in text_fields:
                    continue
                raw_hash = str(value)
                owner_id = "m3-71-owner:" + identity(family_name, runs["v2"], v2_id,
                                                       field, pair_ordinal)[:40]
                per_locale = {locale: candidates.get((locale, raw_hash), ()) for locale in LOCALES}
                states = {locale: ("missing" if not per_locale[locale] else
                                   "resolved_unique" if len(per_locale[locale]) == 1 else
                                   "resolved_candidate_set") for locale in LOCALES}
                overall = ("resolved_candidate_set" if "resolved_candidate_set" in states.values() else
                           "partial_missing" if "missing" in states.values() else "resolved_all_locales")
                count = sum(len(value) for value in per_locale.values())
                conn.execute("INSERT INTO v2_owner VALUES(?,?,?,?,?,?,?,?)",
                             (owner_id, v2_id, field, pair_ordinal, canonical(value), raw_hash, overall, count))
                counts["text_owners"] += 1
                for locale in LOCALES:
                    resolution_id = "m3-71-resolution:" + identity(owner_id, locale)[:40]
                    found = per_locale[locale]
                    conn.execute("INSERT INTO v2_resolution VALUES(?,?,?,?,?)",
                                 (resolution_id, owner_id, locale, states[locale], len(found)))
                    counts["text_resolutions"] += 1
                    counts[locale + ":" + states[locale]] += 1
                    for (tier, relative, pair_index, source_record_pointer, text_pointer,
                         raw_key_json, raw_text_json, text, key_sha, text_sha,
                         record_sha, rendered_sha) in found:
                        candidate_id = "m3-71-candidate:" + identity(owner_id, relative, pair_index)[:40]
                        conn.execute("INSERT INTO v2_candidate VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                     (candidate_id, owner_id, textmap_observation["snapshot_id"],
                                      textmap_observation["parser_build_id"],
                                      textmap_observation["ingest_run_id"],
                                      locale, tier, relative, pair_index,
                                      source_record_pointer, text_pointer, raw_key_json,
                                      raw_text_json, text, key_sha, text_sha, record_sha,
                                      rendered_sha))
                        counts["text_candidates"] += 1
        release = {
            "contract": "m3-7.1-family-provider-candidate-v1",
            "family": family_name, "source_snapshot": SNAPSHOT,
            "source_commit": SOURCE_COMMIT, "source_tree": SOURCE_TREE,
            "frozen_input_contract_sha256": contract_sha,
            "schema_sha256": sha(schema_raw), "builder_implementation_sha256": builder_sha,
            "parser_helper_sha256": helper_shas,
            "textmap_observation": textmap_observation,
            "source_file": source_path, "source_file_sha256": family["source_identity"]["sha256"],
            "textmap_file_sha256": {path: frozen["textmap_sources"][path]["sha256"]
                                    for path in family["v2_textmap_paths"]},
            "family_contracts": {"v1": family["v1_contract"], "v2": family["v2_contract"]},
            "declared_key_fields": family["key_observation_fields"],
            "declared_text_fields": family["v2_text_fields"],
            "parser_build_ids": parsers, "ingest_run_ids": runs,
            "counts": dict(sorted(counts.items())),
            "product_accepted": False, "research_accepted": False,
            "logical_identity_created": False,
        }
        conn.execute("INSERT INTO release VALUES(1,?)", (canonical(release),))
        conn.commit()
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("M3 candidate SQLite integrity failed")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("M3 candidate foreign key failure")
        conn.close()
        conn = None
        if any(Path(str(output) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise ValueError("M3 candidate sidecar present")
        qualify_source(source, family, frozen)
        with output.open("rb") as stream:
            os.fsync(stream.fileno())
        return {**release, "artifact": {"size_bytes": output.stat().st_size,
                                         "sha256": file_sha(output)}}
    except Exception:
        if conn is not None:
            conn.close()
        _remove_if_owned(output, owner)
        raise
