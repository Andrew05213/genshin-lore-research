"""Independent source-to-rowset oracle for World and QuestCodex 7.1 candidates.

No builder projection function is imported. Shared readers only expose exact
physical JSON/TextMap tokens; this oracle separately reconstructs all rows.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .batch_m1_textmap import iter_textmap_pairs
from .batch_m5c_projection import _iter_array


ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "manifests/special-core-7.1/input-contract-v1.json"
RULES = ROOT / "manifests/special-core-7.1/historical-rules-v1.json"
SCHEMA = ROOT / "schemas/special-historical-7.1-provider-v1.sql"
BUILDER = ROOT / "src/genshin_lore/special_historical_7_1_build.py"
TABLES = ("source_file", "physical_row", "observation", "physical_candidate", "release")
LOCALES = ("CHS", "EN", "JP")
OBFUSCATED = re.compile(r"^[A-Z]{11}$")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _id(*parts: Any) -> str:
    return _sha(_json(parts).encode())[:40]


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "--no-optional-locks", "-C", str(root), *args], text=True).strip()


def _expected_schema() -> list[tuple]:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(SCHEMA.read_text())
        for table in TABLES:
            for action in ("INSERT", "UPDATE", "DELETE"):
                conn.execute(f"""CREATE TRIGGER {table}_immutable_{action.lower()} BEFORE {action} ON {table}
                    BEGIN SELECT RAISE(ABORT,'immutable historical special evidence'); END""")
        return conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
    finally:
        conn.close()


def _compare(conn: sqlite3.Connection, table: str, expected: list[tuple]) -> int:
    actual = conn.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()
    wanted = sorted(expected, key=lambda item: str(item[0]))
    if actual != wanted:
        for index, (got, need) in enumerate(zip(actual, wanted)):
            if got != need:
                raise ValueError(f"{table} independent source rowset mismatch at {index}: "
                                 f"stored={got[:4]!r} expected={need[:4]!r}")
        raise ValueError(f"{table} row count mismatch: stored={len(actual)} expected={len(wanted)}")
    return len(actual)


def _index(rows: list[dict], key: str, world: bool = False) -> dict[Any, list[dict]]:
    result = defaultdict(list)
    for row in rows:
        value = row["value"].get(key)
        if value is None or isinstance(value, bool) or type(value) in (dict, list):
            continue
        if world:
            if type(value) is str and value.isdigit():
                value = int(value)
            if type(value) is not int or value <= 0:
                continue
        result[value].append(row)
    return result


def _text_status(matches: list[tuple]) -> str:
    if not matches:
        return "missing"
    main = [candidate for candidate in matches if candidate[1] == "main"]
    medium = [candidate for candidate in matches if candidate[1] == "medium"]
    if len(matches) == 1:
        return "unique_main" if main else "unique_medium"
    if len(matches) == 2 and len(main) == len(medium) == 1:
        return "both_equal" if main[0][4] == medium[0][4] else "both_conflict"
    return "resolved_candidate_set"


def verify(source_root: str | Path, family: str, artifact_path: str | Path) -> dict:
    if family not in ("world_archive", "quest_codex_archive"):
        raise ValueError("family is not historical special")
    root = Path(source_root).resolve()
    artifact = Path(artifact_path).resolve()
    if not artifact.is_file() or artifact.is_symlink():
        raise ValueError("artifact unavailable")
    if any(Path(str(artifact) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("SQLite sidecar present")
    frozen_raw, rules_raw = INPUT.read_bytes(), RULES.read_bytes()
    frozen, rules = json.loads(frozen_raw), json.loads(rules_raw)
    boundary = next(item for item in frozen["contracts"] if item["family"] == family)
    source_rules = rules[family]
    if boundary["provider_contract"] != source_rules["provider_contract"]:
        raise ValueError("provider rule identity differs")
    if (_git(root, "rev-parse", "HEAD"), _git(root, "rev-parse", "HEAD^{tree}")) != (
            frozen["source_commit"], frozen["source_tree"]):
        raise ValueError("source git pin differs")
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("source checkout dirty")
    paths = sorted(set(boundary["primary_paths"] + boundary["lookup_paths"] + frozen["textmap_paths"]))
    raw_files = {}
    expected = {table: [] for table in TABLES}
    for path in paths:
        file = root / path
        if not file.is_file() or file.is_symlink():
            raise ValueError("source file absent " + path)
        raw = file.read_bytes()
        seal = frozen["source_files"][path]
        if (len(raw), _sha(raw), _git(root, "rev-parse", "HEAD:" + path)) != (
                seal["size_bytes"], seal["sha256"], seal["git_blob_oid"]):
            raise ValueError("source file seal differs " + path)
        raw_files[path] = raw
        role = ("primary" if path in boundary["primary_paths"] else
                "support" if family == "quest_codex_archive" and path == source_rules["support"] else
                "textmap" if path in frozen["textmap_paths"] else "lookup")
        expected["source_file"].append((path, role, seal["size_bytes"], seal["sha256"], seal["git_blob_oid"]))
    rows_by_path = {}
    for path in paths:
        if path in frozen["textmap_paths"]:
            continue
        rows_by_path[path] = _iter_array(raw_files[path], path)
        if len(rows_by_path[path]) != frozen["source_files"][path]["physical_rows"]:
            raise ValueError("source physical denominator differs " + path)
    if family == "world_archive":
        selected = [(path, row, "primary") for path in boundary["primary_paths"] for row in rows_by_path[path]]
        relation_rules = source_rules["relations"]
    else:
        primary_path = source_rules["primary"]
        support_path = source_rules["support"]
        chapter_ids = {row["value"]["chapterId"] for row in rows_by_path[primary_path]}
        support = [row for row in rows_by_path[support_path] if row["value"].get("id") in chapter_ids]
        if {row["value"]["id"] for row in support} != chapter_ids:
            raise ValueError("Chapter support rowset is incomplete")
        selected = [(primary_path, row, "primary") for row in rows_by_path[primary_path]] + [
            (support_path, row, "support") for row in support]
        relation_rules = source_rules["relations"]
    wanted = set()
    for path, row, kind in selected:
        text_fields = (source_rules["primary"][path]["text_owners"] if family == "world_archive" else
                       source_rules["support_text_owners"] if kind == "support" else [])
        for field in text_fields:
            raw_hash = row["value"].get(field)
            if type(raw_hash) is not int:
                raise ValueError(f"TextMap owner locator invalid at {path}{row['pointer']}/{field}")
            wanted.add(str(raw_hash))
    pairs = defaultdict(list)
    for path in frozen["textmap_paths"]:
        locale = next((item for item in LOCALES if path.endswith(item + ".json")), None)
        if locale is None:
            raise ValueError("TextMap locale invalid")
        tier = "medium" if "_Medium" in path else "main"
        for pair in iter_textmap_pairs(raw_files[path]):
            if pair.key_text in wanted:
                pairs[(locale, pair.key_text)].append((
                    path, tier, pair.pair_ordinal, pair.pointer, pair.raw_text, pair.raw_record_sha256))
    for pair_list in pairs.values():
        pair_list.sort(key=lambda item: (item[0], item[2]))
    indexes = {}
    if family == "world_archive":
        for rule in relation_rules:
            if "target" in rule:
                indexes[(rule["target"], rule["target_field"])] = _index(
                    rows_by_path[rule["target"]], rule["target_field"], True)
        for path in ("ExcelBinOutput/MonsterDescribeExcelConfigData.json",
                     "ExcelBinOutput/MonsterExcelConfigData.json"):
            indexes[(path, "id")] = _index(rows_by_path[path], "id", True)
    else:
        for rule in relation_rules:
            indexes[(rule["target"], rule["target_field"])] = _index(
                rows_by_path[rule["target"]], rule["target_field"])
    parser = "historical-special-71-parser:" + _id(
        family, boundary["provider_contract"], _file_sha(BUILDER), _file_sha(SCHEMA),
        _sha(frozen_raw), _sha(rules_raw))
    seals = [(path, frozen["source_files"][path]["sha256"]) for path in paths]
    run = "historical-special-71-run:" + _id(frozen["source_snapshot"], parser, seals)
    counts: Counter[str] = Counter()
    def observe(row_id: str, kind: str, pointer: str, field: str, state: str, payload: Any) -> str:
        key = "historical71-obs:" + _id(run, row_id, kind, pointer, field)
        expected["observation"].append((key, row_id, kind, pointer, field, state, _json(payload)))
        counts[kind] += 1
        counts[kind + ":" + state] += 1
        return key
    def candidate(parent: str, kind: str, ordinal: int, path: str, row_ordinal: int,
                  pointer: str, token_sha: str, value: Any) -> None:
        expected["physical_candidate"].append((
            "historical71-candidate:" + _id(run, parent, ordinal), parent,
            kind, ordinal, path, row_ordinal, pointer, token_sha, _json(value)))
        counts[kind + "_candidates"] += 1
    for path, row, kind in selected:
        ordinal, pointer, data = row["ordinal"], row["pointer"], row["value"]
        if type(data.get("id")) is not int:
            raise ValueError(f"source native id invalid {path}{pointer}")
        row_id = "historical71-row:" + _id(run, path, pointer, row["token_sha256"])
        expected["physical_row"].append((
            row_id, frozen["source_snapshot"], parser, run, kind, path, ordinal,
            pointer, row["token"], row["token_sha256"], _json({"id": data["id"]})))
        counts[kind + "_rows"] += 1
        for field in data:
            value = data[field]
            coordinate = f"{pointer}/{field}"
            observe(row_id, "raw_field", coordinate, field, "present", {"raw_value": value})
            opaque = (bool(OBFUSCATED.fullmatch(field)) or
                      family == "world_archive" and path.endswith("ViewCodexExcelConfigData.json")
                      and field in ("configId", "groupId") or
                      family == "quest_codex_archive" and field in
                      ("activityId", "beginQuestId", "cityId", "endQuestId", "groupId"))
            if opaque:
                if isinstance(value, list):
                    for child_index, child in enumerate(value):
                        observe(row_id, "opaque", f"{coordinate}/{child_index}", field,
                                "opaque_unresolved", {"raw_value": child, "array_ordinal": child_index})
                else:
                    observe(row_id, "opaque", coordinate, field, "opaque_unresolved",
                            {"raw_value": value})
        if family == "quest_codex_archive" and kind == "primary":
            for field in source_rules["optional_primary_fields"]:
                if field not in data:
                    observe(row_id, "raw_field", f"{pointer}/{field}", field, "missing",
                            {"raw_value": None})
        asset_fields = (source_rules["primary"][path]["assets"] if family == "world_archive" else
                        source_rules["support_assets"] if kind == "support" else [])
        for field in asset_fields:
            value = data.get(field)
            valid = isinstance(value, str) and bool(value) or (
                field == "chapterImageHash" and type(value) is int and value > 0)
            status = "observed" if valid else (
                "missing" if value is None or value == "" or value == 0 else "invalid")
            observe(row_id, "asset", f"{pointer}/{field}", field, status,
                    {"raw_reference": str(value) if valid else None, "raw_value": value})
        active = ([rule for rule in relation_rules if rule["source"] == path]
                  if family == "world_archive" else relation_rules if kind == "primary" else [])
        for rule in active:
            field = rule["field"]
            value = data.get(field)
            if family == "world_archive" and "dynamic" in rule:
                if data.get("type") == "CODEX_MONSTER":
                    target, target_field = "ExcelBinOutput/MonsterDescribeExcelConfigData.json", "id"
                elif "type" not in data:
                    target, target_field = "ExcelBinOutput/MonsterExcelConfigData.json", "id"
                else:
                    target, target_field = None, None
            else:
                target, target_field = rule["target"], rule["target_field"]
            normalized = None
            if type(value) is int and value > 0:
                normalized = value
            elif family == "world_archive" and type(value) is str and value.isdigit() and int(value) > 0:
                normalized = int(value)
            if field not in data or value is None:
                status, matches = "missing", []
            elif normalized is None:
                status, matches = ("invalid" if family == "world_archive" else "missing"), []
            elif target is None:
                status, matches = "mapping_unresolved", []
            else:
                matches = indexes[(target, target_field)].get(normalized, [])
                if not matches:
                    status = "dangling" if family == "world_archive" else "missing"
                elif len(matches) == 1:
                    status = "resolved_unique"
                else:
                    status = "ambiguous" if family == "world_archive" else "resolved_candidate_set"
            relation_id = observe(row_id, "relation", f"{pointer}/{field}", field, status, {
                "raw_value": value, "target_path": target,
                "target_field": target_field, "candidate_count": len(matches),
                "candidate_navigation_only": True})
            for position, matched in enumerate(matches):
                candidate(relation_id, "relation", position, target, matched["ordinal"],
                          matched["pointer"], matched["token_sha256"], matched["value"][target_field])
        text_fields = (source_rules["primary"][path]["text_owners"] if family == "world_archive" else
                       source_rules["support_text_owners"] if kind == "support" else [])
        for field in text_fields:
            raw_hash = data[field]
            coordinate = f"{pointer}/{field}"
            observe(row_id, "text_owner", coordinate, field, "raw_observed",
                    {"raw_hash": raw_hash, "hash_identity_effect": "none"})
            for locale in LOCALES:
                matches = pairs.get((locale, str(raw_hash)), [])
                status = _text_status(matches)
                resolution_id = observe(row_id, "text_resolution", coordinate,
                                        field + "@" + locale, status,
                                        {"locale": locale, "candidate_count": len(matches)})
                for position, pair in enumerate(matches):
                    text_path, tier, pair_ordinal, pair_pointer, text, pair_sha = pair
                    candidate(resolution_id, "textmap", position, text_path, pair_ordinal,
                              pair_pointer, pair_sha,
                              {"locale": locale, "tier": tier, "hash": str(raw_hash), "raw_text": text})
    release = {
        "contract": "historical-special-7.1-exact-provider-candidate-v1",
        "family": family, "provider_contract": boundary["provider_contract"],
        "source_snapshot": frozen["source_snapshot"], "source_commit": frozen["source_commit"],
        "source_tree": frozen["source_tree"], "input_sha256": _sha(frozen_raw),
        "rules_sha256": _sha(rules_raw), "schema_sha256": _file_sha(SCHEMA),
        "builder_sha256": _file_sha(BUILDER), "parser_build_id": parser,
        "ingest_run_id": run, "source_seals": seals,
        "counts": dict(sorted(counts.items())), "product_accepted": False,
        "research_accepted": False, "logical_identity_created": False,
    }
    expected["release"].append((1, _json(release)))
    conn = sqlite3.connect(f"file:{artifact}?mode=ro", uri=True)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("candidate SQLite integrity failure")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("candidate FK failure")
        observed_schema = conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                                       "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
        normalize = lambda rows: [(kind, name, parent, " ".join(sql.split()))
                                  for kind, name, parent, sql in rows]
        if normalize(observed_schema) != normalize(_expected_schema()):
            raise ValueError("schema/index/trigger definition differs from independent expectation")
        checked = {table: _compare(conn, table, expected[table]) for table in TABLES}
    finally:
        conn.close()
    return {"status": "PASS", "family": family, "artifact": str(artifact),
            "size_bytes": artifact.stat().st_size, "sha256": _file_sha(artifact),
            "counts": dict(sorted(counts.items())), "tables_checked": checked,
            "independent_source_reconstruction": True}


__all__ = ["verify"]
