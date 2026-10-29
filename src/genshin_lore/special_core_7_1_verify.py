"""Independent raw-source oracle for 7.1 M5C special/core candidates.

This module does not import the candidate builder or its projection helpers.
The shared lexical JSON/TextMap readers are physical token readers only.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .batch_m1_textmap import iter_textmap_pairs
from .batch_m5c_projection import _iter_array


ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "manifests/special-core-7.1/input-contract-v1.json"
SCHEMA = ROOT / "schemas/special-core-7.1-provider-v1.sql"
BUILDER = ROOT / "src/genshin_lore/special_core_7_1_build.py"
LOCALES = ("CHS", "EN", "JP")
TABLES = ("source_file", "evidence_row", "raw_field", "opaque_observation",
          "asset_observation", "relation_observation", "relation_candidate",
          "text_owner", "text_resolution", "text_candidate", "release")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def _id(*parts: Any) -> str:
    return _sha(_json(parts).encode())[:40]


def _git(source: Path, *args: str) -> str:
    return subprocess.check_output(["git", "--no-optional-locks", "-C", str(source), *args], text=True).strip()


def _pointer_atom(value: Any) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _walk(value: Any, pointer: str = "", path: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            place = pointer + "/" + _pointer_atom(key)
            name = f"{path}.{key}" if path else key
            yield place, name, child
            yield from _walk(child, place, name)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            place = pointer + "/" + str(index)
            yield place, path + "[]", child
            yield from _walk(child, place, path + "[]")


def _occurrences(value: Any, dotted: str):
    parts = dotted.replace("[]", ".[]").split(".")
    def visit(node: Any, index: int, pointer: str):
        if index == len(parts):
            yield pointer, node, True, None
            return
        part = parts[index]
        if part == "[]":
            if not isinstance(node, list):
                yield pointer, None, False, "missing_field"
            elif not node:
                yield pointer, None, False, "empty_slot"
            else:
                for ordinal, child in enumerate(node):
                    yield from visit(child, index + 1, pointer + "/" + str(ordinal))
        elif not isinstance(node, dict) or part not in node:
            yield pointer + "/" + _pointer_atom(part), None, False, (
                "empty_slot" if isinstance(node, dict) and not node else "missing_field")
        else:
            yield from visit(node[part], index + 1, pointer + "/" + _pointer_atom(part))
    return list(visit(value, 0, ""))


def _schema_objects() -> list[tuple]:
    memory = sqlite3.connect(":memory:")
    try:
        memory.executescript(SCHEMA.read_text())
        for table in TABLES:
            for action in ("INSERT", "UPDATE", "DELETE"):
                memory.execute(f"""CREATE TRIGGER {table}_immutable_{action.lower()}
                    BEFORE {action} ON {table}
                    BEGIN SELECT RAISE(ABORT,'immutable special-core evidence'); END""")
        return memory.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
    finally:
        memory.close()


def _actual(conn: sqlite3.Connection, table: str) -> list[tuple]:
    return conn.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()


def _compare(conn: sqlite3.Connection, table: str, expected: list[tuple]) -> int:
    actual = _actual(conn, table)
    wanted = sorted(expected, key=lambda row: str(row[0]))
    if actual != wanted:
        for index, (left, right) in enumerate(zip(actual, wanted)):
            if left != right:
                raise ValueError(f"{table} source oracle mismatch at row {index}: stored={left[:4]!r} expected={right[:4]!r}")
        raise ValueError(f"{table} source oracle count mismatch: stored={len(actual)} expected={len(wanted)}")
    return len(actual)


def verify(source_root: str | Path, family: str, artifact_path: str | Path) -> dict:
    source = Path(source_root).resolve()
    artifact = Path(artifact_path).resolve()
    if not artifact.is_file() or artifact.is_symlink():
        raise ValueError("candidate artifact unavailable")
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(artifact) + suffix).exists():
            raise ValueError("SQLite sidecar exists: " + suffix)
    frozen_raw = INPUT.read_bytes()
    frozen = json.loads(frozen_raw)
    if frozen["contract"] != "special-core-7.1-provider-input-v1" or frozen["contract_count"] != 13:
        raise ValueError("frozen input contract drift")
    rule = next((item for item in frozen["contracts"] if item["family"] == family), None)
    if rule is None or rule["kind"] != "M5C_WAVE02":
        raise ValueError("family not in M5C oracle boundary")
    if (_git(source, "rev-parse", "HEAD"), _git(source, "rev-parse", "HEAD^{tree}")) != (
            frozen["source_commit"], frozen["source_tree"]):
        raise ValueError("source commit/tree mismatch")
    if _git(source, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("source checkout dirty")
    paths = sorted(set(rule["primary_paths"] + rule["lookup_paths"] + (
        frozen["textmap_paths"] if rule["text_owner_fields"] else [])))
    files = {}
    expected: dict[str, list[tuple]] = {table: [] for table in TABLES}
    for relative in paths:
        file = source / relative
        if not file.is_file() or file.is_symlink():
            raise ValueError("source file missing: " + relative)
        raw = file.read_bytes()
        seal = frozen["source_files"][relative]
        if (len(raw), _sha(raw), _git(source, "rev-parse", "HEAD:" + relative)) != (
                seal["size_bytes"], seal["sha256"], seal["git_blob_oid"]):
            raise ValueError("source file seal differs: " + relative)
        files[relative] = raw
        role = "primary" if relative in rule["primary_paths"] else (
            "lookup" if relative in rule["lookup_paths"] else "textmap")
        expected["source_file"].append((relative, role, seal["size_bytes"], seal["sha256"], seal["git_blob_oid"]))
    primary_path = rule["primary_paths"][0]
    all_primary = _iter_array(files[primary_path], primary_path)
    if len(all_primary) != rule["primary_physical_rows"]:
        raise ValueError("primary denominator mismatch")
    if rule["selected"]:
        selection = rule["selected"]
        anchors = selection["native_values"]
        primary = [row for row in all_primary if row["value"].get(selection["native_field"]) in anchors]
        if len(primary) != len(anchors) or Counter(row["value"].get(selection["native_field"]) for row in primary) != Counter(anchors):
            raise ValueError("bounded canary anchor closure failed")
    else:
        primary = all_primary
    shape = rule["consumed_shape"]
    if len(primary) != shape["selected_rows"]:
        raise ValueError("selected primary denominator differs")
    for field, count in shape["identity_field_present"].items():
        if sum(field in row["value"] for row in primary) != count:
            raise ValueError("identity field denominator differs: " + field)
    for field, count in shape["text_owner_present"].items():
        values = [row["value"][field] for row in primary if field in row["value"]]
        if len(values) != count or sorted({type(value).__name__ for value in values}) != shape["text_owner_types"][field]:
            raise ValueError("TextMap owner field denominator/type differs: " + field)
    for field, count in shape["relation_slots"].items():
        if sum(len(_occurrences(row["value"], field)) for row in primary) != count:
            raise ValueError("relation slot denominator differs: " + field)
    lookups = {}
    for relative in rule["lookup_paths"]:
        lookups[relative] = _iter_array(files[relative], relative)
        if len(lookups[relative]) != frozen["source_files"][relative]["physical_rows"]:
            raise ValueError("lookup denominator mismatch: " + relative)
    indexed = {}
    for relation in rule["relations"]:
        target_path, target_field = relation["target_source_file"], relation["target_locator_field"]
        index = defaultdict(list)
        for row in lookups[target_path]:
            value = row["value"].get(target_field)
            if value is not None and type(value) not in (list, dict):
                index[value].append(row)
        indexed[(target_path, target_field)] = index
    hashes = {str(row["value"][field]) for row in primary for field in rule["text_owner_fields"]
              if field in row["value"]}
    text_pairs = defaultdict(list)
    for relative in frozen["textmap_paths"] if hashes else []:
        locale = next((name for name in LOCALES if relative.endswith(name + ".json")), None)
        if locale is None:
            raise ValueError("TextMap locale missing")
        tier = "medium" if "_Medium" in relative else "main"
        for pair in iter_textmap_pairs(files[relative], wanted_keys=hashes):
            if pair.key_text in hashes:
                text_pairs[(locale, pair.key_text)].append((
                    relative, tier, pair.pair_ordinal, pair.pointer,
                    pair.raw_key_json, pair.raw_text_json, pair.raw_text,
                    pair.raw_key_sha256, pair.raw_text_sha256, pair.raw_record_sha256))
    for value in text_pairs.values():
        value.sort(key=lambda item: (item[0], item[2]))
    builder_sha = _file_sha(BUILDER)
    schema_sha = _file_sha(SCHEMA)
    contract_sha = _sha(frozen_raw)
    parser = "special-core-71-parser:" + _id("m5c-v1", family, rule["provider_contract"],
                                               builder_sha, schema_sha, contract_sha)
    seals = [(path, frozen["source_files"][path]["sha256"]) for path in paths]
    run = "special-core-71-run:" + _id(frozen["source_snapshot"], parser, seals)
    counts: Counter[str] = Counter()
    for row in primary:
        ordinal, pointer, data = row["ordinal"], row["pointer"], row["value"]
        row_id = "special71-row:" + _id(family, run, primary_path, pointer, row["token_sha256"])
        identity_fields = {name: data.get(name) for name in rule["identity_fields"]}
        expected["evidence_row"].append((
            row_id, frozen["source_snapshot"], parser, run, primary_path, ordinal,
            pointer, row["token"], row["token_sha256"], _json(identity_fields)))
        counts["rows"] += 1
        opaque_set = {
            f"/{ordinal}{place}" for field in rule["opaque_unresolved_paths"]
            for place, _value, present, _reason in _occurrences(data, field) if present}
        asset_set = {
            f"/{ordinal}{place}" for field in rule["asset_raw_only_paths"]
            for place, _value, present, _reason in _occurrences(data, field) if present}
        for place, field_name, value in _walk(data):
            full = f"/{ordinal}{place}"
            role = ("opaque_unresolved" if full in opaque_set else
                    "asset_raw_only" if full in asset_set else "raw_observed")
            expected["raw_field"].append((
                "special71-raw:" + _id(run, primary_path, full), row_id,
                full, field_name, _json(value), role))
            counts["raw_fields"] += 1
        for field in rule["opaque_unresolved_paths"]:
            for place, value, present, _reason in _occurrences(data, field):
                full = f"/{ordinal}{place}"
                expected["opaque_observation"].append((
                    "special71-opaque:" + _id(run, primary_path, full, field),
                    row_id, full, field, _json(value if present else None), "opaque_unresolved"))
                counts["opaque"] += 1
        for field in rule["asset_raw_only_paths"]:
            for place, value, present, reason in _occurrences(data, field):
                full = f"/{ordinal}{place}"
                expected["asset_observation"].append((
                    "special71-asset:" + _id(run, primary_path, full, field), row_id,
                    full, field, _json(value) if present else None,
                    "raw_observed" if present else reason))
                counts["assets"] += 1
        for relation_index, relation in enumerate(rule["relations"]):
            target_path, target_field = relation["target_source_file"], relation["target_locator_field"]
            condition = relation.get("condition")
            for place, raw_value, present, reason in _occurrences(data, relation["field"]):
                full = f"/{ordinal}{place}"
                if not present:
                    state, candidates = reason, []
                elif condition and data.get(condition["path"]) != condition["equals"]:
                    state, candidates = "unresolved", []
                else:
                    candidates = indexed[(target_path, target_field)].get(raw_value, [])
                    state = ("missing_target" if not candidates else
                             "resolved_unique" if len(candidates) == 1 else "resolved_candidate_set")
                rel_id = "special71-rel:" + _id(run, primary_path, full, relation_index)
                expected["relation_observation"].append((
                    rel_id, row_id, full, relation["field"],
                    _json(raw_value) if present else None, target_path, target_field,
                    _json(condition or {}), state, len(candidates)))
                counts["relations"] += 1
                counts["relation:" + state] += 1
                for candidate_ordinal, target in enumerate(candidates):
                    expected["relation_candidate"].append((
                        "special71-rel-candidate:" + _id(rel_id, candidate_ordinal),
                        rel_id, candidate_ordinal, target_path, target["ordinal"],
                        target["pointer"], target["token_sha256"],
                        _json(target["value"][target_field])))
                    counts["relation_candidates"] += 1
        for field in rule["text_owner_fields"]:
            if field not in data:
                continue
            raw_hash = data[field]
            full = f"/{ordinal}/{field}"
            owner_id = "special71-owner:" + _id(run, primary_path, full)
            expected["text_owner"].append((owner_id, row_id, full, field, _json(raw_hash)))
            counts["owners"] += 1
            for locale in LOCALES:
                candidates = text_pairs.get((locale, str(raw_hash)), [])
                state = ("missing" if not candidates else
                         "resolved_unique" if len(candidates) == 1 else "resolved_candidate_set")
                res_id = "special71-resolution:" + _id(owner_id, locale)
                expected["text_resolution"].append((res_id, owner_id, locale, state, len(candidates)))
                counts["resolutions"] += 1
                counts["text:" + state] += 1
                for item in candidates:
                    path, tier, pair_ordinal, pair_pointer, raw_key, raw_text_json, text, key_sha, text_sha, record_sha = item
                    expected["text_candidate"].append((
                        "special71-text:" + _id(res_id, path, pair_ordinal), res_id,
                        locale, tier, path, pair_ordinal, pair_pointer,
                        raw_key, raw_text_json, text, key_sha, text_sha, record_sha))
                    counts["text_candidates"] += 1
    release = {
        "contract": "special-core-7.1-m5c-provider-candidate-v1",
        "family": family, "provider_contract": rule["provider_contract"],
        "source_snapshot": frozen["source_snapshot"], "source_commit": frozen["source_commit"],
        "source_tree": frozen["source_tree"], "parser_build_id": parser, "ingest_run_id": run,
        "input_contract_sha256": contract_sha, "schema_sha256": schema_sha,
        "builder_sha256": builder_sha, "source_file_sha256": seals,
        "selected_primary_rows": len(primary), "full_primary_rows": len(all_primary),
        "counts": dict(sorted(counts.items())), "product_accepted": False,
        "research_accepted": False, "logical_identity_created": False,
    }
    expected["release"].append((1, _json(release)))
    conn = sqlite3.connect(f"file:{artifact}?mode=ro", uri=True)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("SQLite integrity failure")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("SQLite FK failure")
        observed_schema = conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
        normalized = lambda rows: [(kind, name, table, " ".join(sql.split()))
                                   for kind, name, table, sql in rows]
        if normalized(observed_schema) != normalized(_schema_objects()):
            raise ValueError("candidate schema/guard definitions differ from frozen independent expectation")
        checked = {table: _compare(conn, table, expected[table]) for table in TABLES}
    finally:
        conn.close()
    return {"status": "PASS", "family": family, "artifact": str(artifact),
            "size_bytes": artifact.stat().st_size, "sha256": _file_sha(artifact),
            "tables_checked": checked, "counts": dict(sorted(counts.items())),
            "independent_source_reconstruction": True, "source_snapshot": frozen["source_snapshot"]}


__all__ = ["verify"]
