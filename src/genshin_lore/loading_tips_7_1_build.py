"""Snapshot-local 7.1 LoadingTips standalone evidence builder."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .loading_tips_7_1_source import FIELDS, LANGUAGES, PRIMARY, array_records, check_scope, pairs, sha

ROOT = Path(__file__).resolve().parents[2]
TABLES = ("loading_tip_row", "loading_tip_slot", "loading_tip_text")
IMPLEMENTATION = ("src/genshin_lore/loading_tips_7_1_build.py",
                  "src/genshin_lore/loading_tips_7_1_source.py",
                  "src/genshin_lore/loading_tips_source.py", "schemas/loading-tips-v1.sql")


def _id(*parts: object) -> str:
    data = json.dumps(parts, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    return "lt71:" + hashlib.sha256(data).hexdigest()[:40]


def build(source_root: Path, scope_path: Path, output: Path) -> dict:
    source_root, scope_path, output = Path(source_root), Path(scope_path), Path(output)
    if output.exists() or any(Path(str(output) + suffix).exists()
                              for suffix in ("-wal", "-shm", "-journal")):
        raise FileExistsError("LoadingTips output or sidecar exists")
    scope = json.loads(scope_path.read_text())
    check_scope(source_root, scope)
    implementation = {name: sha(ROOT / name) for name in IMPLEMENTATION}
    parser = _id("parser", implementation)
    scope_sha = sha(scope_path)
    run = _id("run", parser, scope_sha)
    source = list(array_records(source_root / PRIMARY))
    if len(source) != scope["primary_rows"]:
        raise ValueError("LoadingTips physical denominator")
    wanted: set[str] = set()
    for ordinal, row, _ in source:
        if type(row) is not dict or type(row.get("id")) is not int:
            raise ValueError("LoadingTips physical row/id shape")
        for field in FIELDS:
            if type(row.get(field)) is not int:
                raise ValueError(f"declared text owner absent/noninteger: /{ordinal}/{field}")
            wanted.add(str(row[field]))
    maps: dict[tuple[str, str], dict[str, list[tuple]]] = {}
    for locale in LANGUAGES:
        for tier in ("main", "medium"):
            path = f"TextMap/TextMap{'_Medium' if tier == 'medium' else ''}{locale}.json"
            found: dict[str, list[tuple]] = defaultdict(list)
            for ordinal, (key, text) in enumerate(pairs(source_root / path)):
                if key in wanted:
                    found[key].append((path, ordinal, "/" + key, text))
            maps[locale, tier] = found
    expected: dict[str, list[tuple]] = {name: [] for name in TABLES}
    states: Counter = Counter()
    for ordinal, row, raw in source:
        row_id = _id(run, "row", ordinal)
        expected["loading_tip_row"].append((
            row_id, run, ordinal, str(row["id"]), PRIMARY, "/" + str(ordinal), raw,
        ))
        for field in FIELDS:
            locator = str(row[field])
            for locale in LANGUAGES:
                main = maps[locale, "main"].get(locator, [])
                matches = main or maps[locale, "medium"].get(locator, [])
                status = "missing" if not matches else "resolved" if len(matches) == 1 else "ambiguous"
                states[status] += 1
                slot_id = _id(run, "slot", ordinal, field, locale)
                expected["loading_tip_slot"].append((
                    slot_id, row_id, field, locale, locator, status,
                ))
                for path, pair_ordinal, pointer, text in matches:
                    expected["loading_tip_text"].append((
                        _id(run, "text", ordinal, field, locale, path, pair_ordinal),
                        slot_id, path, pair_ordinal, pointer, text,
                    ))
    for table in TABLES:
        expected[table].sort()
    release = {"contract": "loading-tips-7.1-standalone-v1",
               "family_contract_version": "loading-tips-evidence-v1",
               "snapshot_id": scope["snapshot_id"], "parser_build_id": parser,
               "run_id": run, "scope_sha256": scope_sha,
               "implementation": implementation,
               "counts": {table: len(expected[table]) for table in TABLES},
               "text_state_counts": dict(sorted(states.items())),
               "silent_skips": 0, "cross_snapshot_lookup_count": 0,
               "scope_complete_for_selected_paths": True, "full_7_1": False}
    owned = None
    db = None
    try:
        fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        stat = output.stat()
        owned = (stat.st_dev, stat.st_ino)
        db = sqlite3.connect(output)
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript((ROOT / "schemas/loading-tips-v1.sql").read_text())
        for table in TABLES:
            db.executemany("INSERT INTO " + table + " VALUES(" + ",".join("?" * len(expected[table][0])) + ")",
                           expected[table])
        db.execute("INSERT INTO loading_tip_release VALUES(?)",
                   (json.dumps(release, sort_keys=True, separators=(",", ":")),))
        db.commit()
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("LoadingTips integrity/FK")
        db.execute("VACUUM")
        db.close()
        db = None
        check_scope(source_root, scope)
        if any(Path(str(output) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise ValueError("SQLite sidecar after LoadingTips build")
        return {**release,
                "artifact": {"size_bytes": output.stat().st_size, "sha256": sha(output)},
                "logical_seals": {table: hashlib.sha256("\n".join(
                    json.dumps(list(row), ensure_ascii=True, separators=(",", ":"))
                    for row in expected[table]).encode()).hexdigest() for table in TABLES}}
    except Exception:
        if db is not None:
            db.close()
        if owned is not None and output.exists():
            stat = output.stat()
            if (stat.st_dev, stat.st_ino) == owned:
                output.unlink()
        raise
