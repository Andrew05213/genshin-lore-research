"""Family-specific 7.1 World and QuestCodex core provider projections.

World and QuestCodex have distinct source/relationship rules. The shared
SQLite writer is only an occurrence container, not a universal entity model.
"""
from __future__ import annotations

import hashlib
import json
import os
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


def _qualify(root: Path, frozen: dict, rule: dict) -> dict[str, bytes]:
    if (_git(root, "rev-parse", "HEAD"), _git(root, "rev-parse", "HEAD^{tree}")) != (
            frozen["source_commit"], frozen["source_tree"]):
        raise ValueError("7.1 source pin mismatch")
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("7.1 source checkout dirty")
    paths = sorted(set(rule["primary_paths"] + rule["lookup_paths"] + frozen["textmap_paths"]))
    files = {}
    for relative in paths:
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError("source path unavailable: " + relative)
        raw = path.read_bytes()
        seal = frozen["source_files"][relative]
        if (len(raw), _sha(raw), _git(root, "rev-parse", "HEAD:" + relative)) != (
                seal["size_bytes"], seal["sha256"], seal["git_blob_oid"]):
            raise ValueError("source file seal mismatch: " + relative)
        files[relative] = raw
    return files


def _key_index(rows: list[dict], field: str, positive_only: bool = False) -> dict[Any, list[dict]]:
    index: dict[Any, list[dict]] = defaultdict(list)
    for row in rows:
        value = row["value"].get(field)
        if isinstance(value, bool) or type(value) in (list, dict) or value is None:
            continue
        if positive_only:
            if type(value) is int and value > 0:
                pass
            elif type(value) is str and value.isdigit() and int(value) > 0:
                value = int(value)
            else:
                continue
        index[value].append(row)
    return index


def _text_pairs(files: dict[str, bytes], paths: list[str], wanted: set[str]) -> dict[tuple[str, str], list[tuple]]:
    found: dict[tuple[str, str], list[tuple]] = defaultdict(list)
    for relative in paths:
        locale = next((value for value in LOCALES if relative.endswith(value + ".json")), None)
        if locale is None:
            raise ValueError("TextMap locale unavailable")
        tier = "medium" if "_Medium" in relative else "main"
        for pair in iter_textmap_pairs(files[relative], wanted_keys=wanted):
            if pair.key_text in wanted:
                found[(locale, pair.key_text)].append((
                    relative, tier, pair.pair_ordinal, pair.pointer, pair.raw_text,
                    pair.raw_record_sha256))
    for values in found.values():
        values.sort(key=lambda item: (item[0], item[2]))
    return found


def _world_target(row: dict) -> tuple[str | None, str | None]:
    if row.get("type") == "CODEX_MONSTER":
        return "ExcelBinOutput/MonsterDescribeExcelConfigData.json", "id"
    if "type" not in row:
        return "ExcelBinOutput/MonsterExcelConfigData.json", "id"
    return None, None


def _text_state(candidates: list[tuple]) -> str:
    main = [item for item in candidates if item[1] == "main"]
    medium = [item for item in candidates if item[1] == "medium"]
    if not candidates:
        return "missing"
    if len(candidates) == 1:
        return "unique_main" if main else "unique_medium"
    if len(main) == 1 and len(medium) == 1:
        return "both_equal" if main[0][4] == medium[0][4] else "both_conflict"
    return "resolved_candidate_set"


def _guard(conn: sqlite3.Connection) -> None:
    for table in TABLES:
        for action in ("INSERT", "UPDATE", "DELETE"):
            conn.execute(f"""CREATE TRIGGER {table}_immutable_{action.lower()} BEFORE {action} ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable historical special evidence'); END""")


def build(source_root: str | Path, family: str, output_path: str | Path) -> dict:
    if family not in ("world_archive", "quest_codex_archive"):
        raise ValueError("historical family not supported")
    root = Path(source_root).resolve()
    output = Path(output_path).resolve()
    frozen_raw = INPUT.read_bytes()
    frozen = json.loads(frozen_raw)
    rules_raw = RULES.read_bytes()
    rules = json.loads(rules_raw)
    if frozen["contract_count"] != 13 or rules["source_snapshot"] != frozen["source_snapshot"]:
        raise ValueError("historical special input identity mismatch")
    boundary = next(item for item in frozen["contracts"] if item["family"] == family)
    source_rules = rules[family]
    if boundary["provider_contract"] != source_rules["provider_contract"]:
        raise ValueError("historical provider contract mismatch")
    files = _qualify(root, frozen, boundary)
    physical = {path: _iter_array(raw, path) for path, raw in files.items() if path not in frozen["textmap_paths"]}
    for path, rows in physical.items():
        if len(rows) != frozen["source_files"][path]["physical_rows"]:
            raise ValueError("physical source denominator mismatch: " + path)
    if family == "world_archive":
        selected = [(path, row, "primary") for path in boundary["primary_paths"] for row in physical[path]]
        relation_specs = source_rules["relations"]
    else:
        quest_path = source_rules["primary"]
        chapter_path = source_rules["support"]
        chapter_index = _key_index(physical[chapter_path], "id")
        chapter_refs = {row["value"]["chapterId"] for row in physical[quest_path]}
        support = [row for row in physical[chapter_path] if row["value"].get("id") in chapter_refs]
        if {row["value"]["id"] for row in support} != chapter_refs:
            raise ValueError("QuestCodex Chapter support closure has dangling chapterId")
        selected = [(quest_path, row, "primary") for row in physical[quest_path]]
        selected.extend((chapter_path, row, "support") for row in support)
        relation_specs = source_rules["relations"]
    wanted = set()
    for path, row, kind in selected:
        fields = (source_rules["primary"][path]["text_owners"] if family == "world_archive" else
                  source_rules["support_text_owners"] if kind == "support" else [])
        for field in fields:
            if field not in row["value"] or type(row["value"][field]) is not int:
                raise ValueError(f"required TextMap owner absent/non-int: {path}{row['pointer']}/{field}")
            wanted.add(str(row["value"][field]))
    textmap = _text_pairs(files, frozen["textmap_paths"], wanted)
    indexes: dict[tuple[str, str], dict[Any, list[dict]]] = {}
    if family == "world_archive":
        for spec in relation_specs:
            target = spec.get("target")
            if target:
                indexes[(target, spec["target_field"])] = _key_index(physical[target], spec["target_field"], True)
        for path in ("ExcelBinOutput/MonsterDescribeExcelConfigData.json",
                     "ExcelBinOutput/MonsterExcelConfigData.json"):
            indexes[(path, "id")] = _key_index(physical[path], "id", True)
    else:
        for spec in relation_specs:
            indexes[(spec["target"], spec["target_field"])] = _key_index(
                physical[spec["target"]], spec["target_field"])
    builder_sha = _file_sha(Path(__file__))
    schema_sha = _file_sha(SCHEMA)
    parser = "historical-special-71-parser:" + _id(family, boundary["provider_contract"], builder_sha,
                                                      schema_sha, _sha(frozen_raw), _sha(rules_raw))
    seals = [(path, frozen["source_files"][path]["sha256"]) for path in sorted(files)]
    run = "historical-special-71-run:" + _id(frozen["source_snapshot"], parser, seals)
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
            role = ("primary" if path in boundary["primary_paths"] else
                    "support" if family == "quest_codex_archive" and path == source_rules["support"] else
                    "textmap" if path in frozen["textmap_paths"] else "lookup")
            item = frozen["source_files"][path]
            conn.execute("INSERT INTO source_file VALUES(?,?,?,?,?)",
                         (path, role, item["size_bytes"], item["sha256"], item["git_blob_oid"]))
        def observation(row_id: str, kind: str, pointer: str, field: str, status: str, payload: Any) -> str:
            observation_id = "historical71-obs:" + _id(run, row_id, kind, pointer, field)
            conn.execute("INSERT INTO observation VALUES(?,?,?,?,?,?,?)", (
                observation_id, row_id, kind, pointer, field, status, _json(payload)))
            counts[kind] += 1
            counts[kind + ":" + status] += 1
            return observation_id
        def candidate(parent_id: str, kind: str, ordinal: int, path: str, row_ordinal: int,
                      pointer: str, raw_sha: str, value: Any) -> None:
            conn.execute("INSERT INTO physical_candidate VALUES(?,?,?,?,?,?,?,?,?)", (
                "historical71-candidate:" + _id(run, parent_id, ordinal), parent_id,
                kind, ordinal, path, row_ordinal, pointer, raw_sha, _json(value)))
            counts[kind + "_candidates"] += 1
        for path, source_row, kind in selected:
            ordinal, pointer, data = source_row["ordinal"], source_row["pointer"], source_row["value"]
            if type(data.get("id")) is not int:
                raise ValueError(f"native id shape changed: {path}{pointer}/id")
            row_id = "historical71-row:" + _id(run, path, pointer, source_row["token_sha256"])
            conn.execute("INSERT INTO physical_row VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                row_id, frozen["source_snapshot"], parser, run, kind,
                path, ordinal, pointer, source_row["token"], source_row["token_sha256"],
                _json({"id": data["id"]})))
            counts[kind + "_rows"] += 1
            for field, value in data.items():
                field_pointer = f"{pointer}/{field}"
                observation(row_id, "raw_field", field_pointer, field, "present", {"raw_value": value})
                if OBFUSCATED.fullmatch(field) or (
                    family == "world_archive" and path.endswith("ViewCodexExcelConfigData.json")
                    and field in ("configId", "groupId")) or (
                    family == "quest_codex_archive" and field in
                    ("activityId", "beginQuestId", "cityId", "endQuestId", "groupId")):
                    if isinstance(value, list):
                        for child_ordinal, child in enumerate(value):
                            observation(row_id, "opaque", f"{field_pointer}/{child_ordinal}", field,
                                        "opaque_unresolved", {"raw_value": child, "array_ordinal": child_ordinal})
                    else:
                        observation(row_id, "opaque", field_pointer, field, "opaque_unresolved",
                                    {"raw_value": value})
            if family == "quest_codex_archive" and kind == "primary":
                for field in source_rules["optional_primary_fields"]:
                    if field not in data:
                        observation(row_id, "raw_field", f"{pointer}/{field}", field, "missing", {"raw_value": None})
            assets = (source_rules["primary"][path]["assets"] if family == "world_archive" else
                      source_rules["support_assets"] if kind == "support" else [])
            for field in assets:
                value = data.get(field)
                observed = (isinstance(value, str) and bool(value)) or (
                    type(value) is int and value > 0 and field == "chapterImageHash")
                status = "observed" if observed else (
                    "missing" if value is None or value == "" or value == 0 else "invalid")
                observation(row_id, "asset", f"{pointer}/{field}", field, status,
                            {"raw_reference": str(value) if status == "observed" else None,
                             "raw_value": value})
            if family == "world_archive":
                active_relations = [spec for spec in relation_specs if spec["source"] == path]
            else:
                active_relations = relation_specs if kind == "primary" else []
            for spec in active_relations:
                field = spec["field"]
                raw_value = data.get(field)
                if family == "world_archive" and spec.get("dynamic"):
                    target_path, target_field = _world_target(data)
                else:
                    target_path, target_field = spec["target"], spec["target_field"]
                valid = raw_value if type(raw_value) is int and raw_value > 0 else (
                    int(raw_value) if family == "world_archive" and
                    type(raw_value) is str and raw_value.isdigit() and int(raw_value) > 0 else None)
                if field not in data or raw_value is None:
                    status, matches = "missing", []
                elif valid is None:
                    status, matches = ("invalid" if family == "world_archive" else "missing"), []
                elif target_path is None:
                    status, matches = "mapping_unresolved", []
                else:
                    matches = indexes[(target_path, target_field)].get(valid, [])
                    status = ("dangling" if family == "world_archive" else "missing") if not matches else (
                        "resolved_unique" if len(matches) == 1 else
                        "ambiguous" if family == "world_archive" else "resolved_candidate_set")
                rel_id = observation(row_id, "relation", f"{pointer}/{field}", field, status, {
                    "raw_value": raw_value, "target_path": target_path,
                    "target_field": target_field, "candidate_count": len(matches),
                    "candidate_navigation_only": True})
                for candidate_ordinal, target in enumerate(matches):
                    candidate(rel_id, "relation", candidate_ordinal, target_path,
                              target["ordinal"], target["pointer"], target["token_sha256"],
                              target["value"][target_field])
            fields = (source_rules["primary"][path]["text_owners"] if family == "world_archive" else
                      source_rules["support_text_owners"] if kind == "support" else [])
            for field in fields:
                raw_hash = data[field]
                owner_pointer = f"{pointer}/{field}"
                observation(row_id, "text_owner", owner_pointer, field, "raw_observed",
                            {"raw_hash": raw_hash, "hash_identity_effect": "none"})
                for locale in LOCALES:
                    matches = textmap.get((locale, str(raw_hash)), [])
                    state = _text_state(matches)
                    resolution_id = observation(row_id, "text_resolution", owner_pointer,
                                                field + "@" + locale, state,
                                                {"locale": locale, "candidate_count": len(matches)})
                    for candidate_ordinal, item in enumerate(matches):
                        target_path, tier, pair_ordinal, pair_pointer, text, record_sha = item
                        candidate(resolution_id, "textmap", candidate_ordinal, target_path,
                                  pair_ordinal, pair_pointer, record_sha,
                                  {"locale": locale, "tier": tier, "hash": str(raw_hash), "raw_text": text})
        release = {
            "contract": "historical-special-7.1-exact-provider-candidate-v1",
            "family": family, "provider_contract": boundary["provider_contract"],
            "source_snapshot": frozen["source_snapshot"], "source_commit": frozen["source_commit"],
            "source_tree": frozen["source_tree"], "input_sha256": _sha(frozen_raw),
            "rules_sha256": _sha(rules_raw), "schema_sha256": schema_sha,
            "builder_sha256": builder_sha, "parser_build_id": parser, "ingest_run_id": run,
            "source_seals": seals, "counts": dict(sorted(counts.items())),
            "product_accepted": False, "research_accepted": False,
            "logical_identity_created": False,
        }
        conn.execute("INSERT INTO release VALUES(1,?)", (_json(release),))
        _guard(conn)
        conn.commit()
        if conn.execute("PRAGMA integrity_check").fetchone() != ("ok",) or conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("historical special SQLite integrity/FK failed")
        conn.close()
        conn = None
        return {"family": family, "path": str(output), "size_bytes": output.stat().st_size,
                "sha256": _file_sha(output), "parser_build_id": parser,
                "ingest_run_id": run, "counts": release["counts"]}
    except BaseException:
        if conn is not None:
            conn.close()
        raise


__all__ = ["build"]
