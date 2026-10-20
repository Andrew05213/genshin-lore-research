"""Snapshot-local special/core M5C provider candidate projection.

The historical M5B rules define *which* fields are observed. All physical
rows, candidate targets and TextMap pairs are independently taken from 7.1.
No logical identity or cross-snapshot relation is created.
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

from .batch_m1_textmap import iter_textmap_pairs
from .batch_m5c_projection import _field_occurrences, _iter_array, _pointer, _walk_raw_fields


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "manifests/special-core-7.1/input-contract-v1.json"
SCHEMA = ROOT / "schemas/special-core-7.1-provider-v1.sql"
LOCALES = ("CHS", "EN", "JP")
TABLES = ("source_file", "evidence_row", "raw_field", "opaque_observation",
          "asset_observation", "relation_observation", "relation_candidate",
          "text_owner", "text_resolution", "text_candidate", "release")


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        sha = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def identity(*parts: Any) -> str:
    return digest(canonical(parts).encode())[:40]


def _git(source: Path, *args: str) -> str:
    return subprocess.check_output(["git", "--no-optional-locks", "-C", str(source), *args], text=True).strip()


def _qualify(source: Path, frozen: dict, rule: dict) -> dict[str, bytes]:
    if (_git(source, "rev-parse", "HEAD"), _git(source, "rev-parse", "HEAD^{tree}")) != (
            frozen["source_commit"], frozen["source_tree"]):
        raise ValueError("exact 7.1 source pin required")
    if _git(source, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("7.1 source checkout is dirty")
    paths = set(rule["primary_paths"] + rule["lookup_paths"] + (
        frozen["textmap_paths"] if rule["text_owner_fields"] else []))
    result = {}
    for relative in sorted(paths):
        path = source / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError("source path absent or symlink: " + relative)
        raw = path.read_bytes()
        entry = frozen["source_files"][relative]
        if (len(raw), digest(raw), _git(source, "rev-parse", "HEAD:" + relative)) != (
                entry["size_bytes"], entry["sha256"], entry["git_blob_oid"]):
            raise ValueError("source file identity drift: " + relative)
        result[relative] = raw
    return result


def _textmap(files: dict[str, bytes], paths: list[str], wanted: set[str]) -> dict[tuple[str, str], list[tuple]]:
    found: dict[tuple[str, str], list[tuple]] = defaultdict(list)
    for path in paths:
        tier = "medium" if "_Medium" in path else "main"
        locale = next((item for item in LOCALES if path.endswith(item + ".json")), None)
        if locale is None:
            raise ValueError("unrecognized TextMap shard " + path)
        for pair in iter_textmap_pairs(files[path]):
            if pair.key_text in wanted:
                found[(locale, pair.key_text)].append((
                    path, tier, pair.pair_ordinal, pair.pointer, pair.raw_key_json,
                    pair.raw_text_json, pair.raw_text, pair.raw_key_sha256,
                    pair.raw_text_sha256, pair.raw_record_sha256))
    for candidates in found.values():
        candidates.sort(key=lambda item: (item[0], item[2]))
    return found


def _rows(raw: bytes, label: str) -> list[dict]:
    return _iter_array(raw, label)


def _targets(rows: list[dict], field: str) -> dict[Any, list[dict]]:
    found: dict[Any, list[dict]] = defaultdict(list)
    for row in rows:
        value = row["value"].get(field)
        if value is None or type(value) in (list, dict):
            continue
        found[value].append(row)
    return found


def _write_guards(conn: sqlite3.Connection) -> None:
    for table in TABLES:
        for action in ("INSERT", "UPDATE", "DELETE"):
            conn.execute(f"""CREATE TRIGGER {table}_immutable_{action.lower()}
                BEFORE {action} ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable special-core evidence'); END""")


def build(source_root: str | Path, family: str, output_path: str | Path) -> dict:
    source = Path(source_root).resolve()
    output = Path(output_path).resolve()
    contract_raw = CONTRACT.read_bytes()
    frozen = json.loads(contract_raw)
    if frozen["contract"] != "special-core-7.1-provider-input-v1" or frozen["contract_count"] != 13:
        raise ValueError("frozen special/core input contract mismatch")
    rule = next((item for item in frozen["contracts"] if item["family"] == family), None)
    if rule is None or rule["kind"] != "M5C_WAVE02":
        raise ValueError("M5C family is not in frozen build boundary")
    files = _qualify(source, frozen, rule)
    primary = rule["primary_paths"][0]
    all_rows = _rows(files[primary], primary)
    if len(all_rows) != rule["primary_physical_rows"]:
        raise ValueError("primary physical denominator drift")
    selected = rule["selected"]
    if selected:
        key = selected["native_field"]
        anchors = selected["native_values"]
        rows = [row for row in all_rows if row["value"].get(key) in anchors]
        if len(rows) != len(anchors) or Counter(row["value"].get(key) for row in rows) != Counter(anchors):
            raise ValueError("ProfilePicture five-anchor canary cannot be projected uniquely")
    else:
        rows = all_rows
    shape = rule["consumed_shape"]
    if len(rows) != shape["selected_rows"]:
        raise ValueError("selected primary denominator drift")
    for field, count in shape["identity_field_present"].items():
        if sum(field in row["value"] for row in rows) != count:
            raise ValueError("identity field denominator drift: " + field)
    for field, count in shape["text_owner_present"].items():
        observed = [row["value"][field] for row in rows if field in row["value"]]
        if len(observed) != count or sorted({type(value).__name__ for value in observed}) != shape["text_owner_types"][field]:
            raise ValueError("TextMap owner field denominator/type drift: " + field)
    for relation_field, count in shape["relation_slots"].items():
        if sum(len(_field_occurrences(row["value"], relation_field)) for row in rows) != count:
            raise ValueError("relation locator slot denominator drift: " + relation_field)
    lookup_rows = {}
    for path in rule["lookup_paths"]:
        lookup_rows[path] = _rows(files[path], path)
        if len(lookup_rows[path]) != frozen["source_files"][path]["physical_rows"]:
            raise ValueError("lookup denominator drift: " + path)
    target_indexes = {(relation["target_source_file"], relation["target_locator_field"]):
                      _targets(lookup_rows[relation["target_source_file"]], relation["target_locator_field"])
                      for relation in rule["relations"]}
    wanted = {str(row["value"][field]) for row in rows for field in rule["text_owner_fields"]
              if field in row["value"]}
    textmap = _textmap(files, frozen["textmap_paths"], wanted) if wanted else {}
    builder_sha = file_digest(Path(__file__))
    schema_sha = file_digest(SCHEMA)
    contract_sha = digest(contract_raw)
    parser = "special-core-71-parser:" + identity("m5c-v1", family, rule["provider_contract"],
                                                    builder_sha, schema_sha, contract_sha)
    source_seals = [(path, frozen["source_files"][path]["sha256"]) for path in sorted(files)]
    run = "special-core-71-run:" + identity(frozen["source_snapshot"], parser, source_seals)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    stat = os.fstat(fd)
    owner = (stat.st_dev, stat.st_ino)
    os.close(fd)
    conn = None
    counts: Counter[str] = Counter()
    try:
        conn = sqlite3.connect(output)
        conn.execute("PRAGMA page_size=4096")
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA.read_text())
        for path in sorted(files):
            role = "primary" if path == primary else "lookup" if path in rule["lookup_paths"] else "textmap"
            item = frozen["source_files"][path]
            conn.execute("INSERT INTO source_file VALUES(?,?,?,?,?)",
                         (path, role, item["size_bytes"], item["sha256"], item["git_blob_oid"]))
        for row in rows:
            ordinal = row["ordinal"]
            pointer = row["pointer"]
            value = row["value"]
            row_id = "special71-row:" + identity(family, run, primary, pointer, row["token_sha256"])
            native = {key: value.get(key) for key in rule["identity_fields"]}
            conn.execute("INSERT INTO evidence_row VALUES(?,?,?,?,?,?,?,?,?,?)", (
                row_id, frozen["source_snapshot"], parser, run, primary, ordinal,
                pointer, row["token"], row["token_sha256"], canonical(native)))
            counts["rows"] += 1
            opaque_pointers = {
                _pointer(ordinal, coords) for path in rule["opaque_unresolved_paths"]
                for coords, _item, present, _reason in _field_occurrences(value, path) if present}
            asset_pointers = {
                _pointer(ordinal, coords) for path in rule["asset_raw_only_paths"]
                for coords, _item, present, _reason in _field_occurrences(value, path) if present}
            for local_pointer, field_path, field_value in _walk_raw_fields(value):
                full_pointer = f"/{ordinal}{local_pointer}"
                role = ("opaque_unresolved" if full_pointer in opaque_pointers else
                        "asset_raw_only" if full_pointer in asset_pointers else "raw_observed")
                conn.execute("INSERT INTO raw_field VALUES(?,?,?,?,?,?)", (
                    "special71-raw:" + identity(run, primary, full_pointer), row_id,
                    full_pointer, field_path, canonical(field_value), role))
                counts["raw_fields"] += 1
            for path in rule["opaque_unresolved_paths"]:
                for coords, field_value, present, _reason in _field_occurrences(value, path):
                    full_pointer = _pointer(ordinal, coords)
                    conn.execute("INSERT INTO opaque_observation VALUES(?,?,?,?,?,?)", (
                        "special71-opaque:" + identity(run, primary, full_pointer, path),
                        row_id, full_pointer, path, canonical(field_value if present else None),
                        "opaque_unresolved"))
                    counts["opaque"] += 1
            for path in rule["asset_raw_only_paths"]:
                for coords, field_value, present, reason in _field_occurrences(value, path):
                    full_pointer = _pointer(ordinal, coords)
                    conn.execute("INSERT INTO asset_observation VALUES(?,?,?,?,?,?)", (
                        "special71-asset:" + identity(run, primary, full_pointer, path),
                        row_id, full_pointer, path, canonical(field_value) if present else None,
                        "raw_observed" if present else reason))
                    counts["assets"] += 1
            for relation_index, relation in enumerate(rule["relations"]):
                target_path = relation["target_source_file"]
                target_field = relation["target_locator_field"]
                index = target_indexes[(target_path, target_field)]
                condition = relation.get("condition")
                for coords, raw_value, present, reason in _field_occurrences(value, relation["field"]):
                    full_pointer = _pointer(ordinal, coords)
                    if not present:
                        state, matches = reason, []
                    elif condition and value.get(condition["path"]) != condition["equals"]:
                        state, matches = "unresolved", []
                    else:
                        matches = index.get(raw_value, [])
                        state = "missing_target" if not matches else (
                            "resolved_unique" if len(matches) == 1 else "resolved_candidate_set")
                    relation_id = "special71-rel:" + identity(run, primary, full_pointer, relation_index)
                    conn.execute("INSERT INTO relation_observation VALUES(?,?,?,?,?,?,?,?,?,?)", (
                        relation_id, row_id, full_pointer, relation["field"],
                        canonical(raw_value) if present else None, target_path, target_field,
                        canonical(condition or {}), state, len(matches)))
                    counts["relations"] += 1
                    counts["relation:" + state] += 1
                    for candidate_ordinal, target in enumerate(matches):
                        candidate_id = "special71-rel-candidate:" + identity(relation_id, candidate_ordinal)
                        conn.execute("INSERT INTO relation_candidate VALUES(?,?,?,?,?,?,?,?)", (
                            candidate_id, relation_id, candidate_ordinal, target_path,
                            target["ordinal"], target["pointer"], target["token_sha256"],
                            canonical(target["value"][target_field])))
                        counts["relation_candidates"] += 1
            for field in rule["text_owner_fields"]:
                if field not in value:
                    continue
                raw_hash = value[field]
                owner_pointer = f"/{ordinal}/{field}"
                owner_id = "special71-owner:" + identity(run, primary, owner_pointer)
                conn.execute("INSERT INTO text_owner VALUES(?,?,?,?,?)", (
                    owner_id, row_id, owner_pointer, field, canonical(raw_hash)))
                counts["owners"] += 1
                for locale in LOCALES:
                    matches = textmap.get((locale, str(raw_hash)), [])
                    state = "missing" if not matches else (
                        "resolved_unique" if len(matches) == 1 else "resolved_candidate_set")
                    resolution_id = "special71-resolution:" + identity(owner_id, locale)
                    conn.execute("INSERT INTO text_resolution VALUES(?,?,?,?,?)",
                                 (resolution_id, owner_id, locale, state, len(matches)))
                    counts["resolutions"] += 1
                    counts["text:" + state] += 1
                    for item in matches:
                        path, tier, pair_ordinal, pair_pointer, raw_key, raw_text_json, text, key_sha, text_sha, record_sha = item
                        candidate_id = "special71-text:" + identity(resolution_id, path, pair_ordinal)
                        conn.execute("INSERT INTO text_candidate VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                            candidate_id, resolution_id, locale, tier, path, pair_ordinal,
                            pair_pointer, raw_key, raw_text_json, text, key_sha, text_sha, record_sha))
                        counts["text_candidates"] += 1
        release = {
            "contract": "special-core-7.1-m5c-provider-candidate-v1",
            "family": family, "provider_contract": rule["provider_contract"],
            "source_snapshot": frozen["source_snapshot"], "source_commit": frozen["source_commit"],
            "source_tree": frozen["source_tree"], "parser_build_id": parser, "ingest_run_id": run,
            "input_contract_sha256": contract_sha, "schema_sha256": schema_sha,
            "builder_sha256": builder_sha, "source_file_sha256": source_seals,
            "selected_primary_rows": len(rows), "full_primary_rows": len(all_rows),
            "counts": dict(sorted(counts.items())), "product_accepted": False,
            "research_accepted": False, "logical_identity_created": False,
        }
        conn.execute("INSERT INTO release VALUES(1,?)", (canonical(release),))
        _write_guards(conn)
        conn.commit()
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("special/core candidate SQLite integrity failed")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("special/core candidate FK failure")
        conn.close()
        conn = None
        return {"family": family, "path": str(output), "size_bytes": output.stat().st_size,
                "sha256": file_digest(output), "parser_build_id": parser,
                "ingest_run_id": run, "counts": release["counts"]}
    except BaseException:
        if conn is not None:
            conn.close()
        raise


__all__ = ["build", "canonical", "digest", "file_digest", "identity", "CONTRACT", "SCHEMA", "TABLES"]
