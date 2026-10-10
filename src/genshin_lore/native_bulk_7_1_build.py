"""Snapshot-local, declared-scope 7.1 Native Bulk evidence builder.

This is deliberately separate from the accepted 7.0 builder.  It consumes
only the tracked 49-path selection and the six *7.1* TextMap files; no
historical artifact or cross-snapshot lookup participates in projection.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from .event_narrative_source import array_records, git_blob, pairs, sha

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
TREE = "aa578c08c402af39b41f1ffc95124d10f5410272"
SELECTION = "manifests/native-archive/bulk-selection.json"
TABLES = ("native_archive_container", "native_archive_slot", "native_archive_text",
          "native_archive_relation", "native_archive_candidate")
IMPLEMENTATION = ("src/genshin_lore/native_bulk_7_1_build.py",
                  "src/genshin_lore/event_narrative_source.py",
                  "schemas/native-archive-v1.sql", SELECTION)


def identity(*parts: object) -> str:
    return "native-archive:" + hashlib.sha256(
        json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()[:40]


def _qualify(root: Path, scope_path: Path) -> tuple[dict, dict, str, str, str]:
    scope = json.loads(scope_path.read_text())
    head, tree = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD", "HEAD^{tree}"], text=True
    ).splitlines()
    if (head, tree) != (COMMIT, TREE) or subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain"]
    ):
        raise ValueError("exact clean 7.1 source checkout required")
    if (scope.get("source_commit"), scope.get("source_tree"), scope.get("snapshot_id")) != (
        COMMIT, TREE, "anime-game-data:7.1:" + COMMIT
    ):
        raise ValueError("7.1 scope pin")
    selection = json.loads((ROOT / SELECTION).read_text())
    if (scope.get("selection_manifest") != SELECTION
            or scope.get("selection_sha256") != sha(ROOT / SELECTION)
            or scope.get("sources") != selection["sources"]
            or scope.get("languages") != selection["languages"]
            or scope.get("textmaps") != selection["textmaps"]):
        raise ValueError("accepted Bulk selection drift")
    if scope.get("dependencies") != {} or scope.get("reuse_targets") != [] or scope.get("external_candidates") != []:
        raise ValueError("Bulk cannot acquire undeclared cross-family dependencies")
    paths = {spec["path"] for spec in scope["sources"]}
    paths.update(scope["textmaps"][locale][tier] for locale in scope["languages"]
                 for tier in ("main", "medium"))
    if (len(scope["sources"]) != 49 or len(paths) != 55 or len(scope["files"]) != 55
            or {item["path"] for item in scope["files"]} != paths):
        raise ValueError("declared 49+6 source denominator")
    for item in scope["files"]:
        path = root / item["path"]
        blob = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", f"{COMMIT}:{item['path']}"], text=True
        ).strip()
        if (not path.is_file() or path.is_symlink() or path.stat().st_size != item["size_bytes"]
                or sha(path) != item["sha256"] or git_blob(path) != blob or blob != item["blob"]):
            raise ValueError("source byte/blob drift: " + item["path"])
    implementation = {name: sha(ROOT / name) for name in IMPLEMENTATION}
    parser = "native-archive-7.1-parser:" + hashlib.sha256(
        json.dumps(implementation, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:40]
    scope_sha = sha(scope_path)
    run = "native-archive-7.1-run:" + hashlib.sha256(
        (parser + ":" + scope_sha).encode()
    ).hexdigest()[:40]
    return scope, implementation, parser, scope_sha, run


def _projection(root: Path, scope: dict, run: str) -> tuple[dict[str, list[tuple]], Counter, Counter]:
    file_seals = {item["path"]: item for item in scope["files"]}
    records: dict[str, list[tuple]] = {}
    wanted: set[str] = set()
    for spec in scope["sources"]:
        rows = []
        if spec.get("reuse") or spec.get("relations"):
            raise ValueError("Bulk source unexpectedly acquired relation/reuse")
        for ordinal, row, raw in array_records(root / spec["path"]):
            if not isinstance(row, dict):
                raise ValueError("Bulk physical row must be object")
            field = spec.get("native_id_field")
            if field is not None and field not in row:
                raise ValueError(f"declared native ID field absent: {spec['path']} /{ordinal}/{field}")
            for owner in spec["text_owners"]:
                name = owner["field"]
                if type(row.get(name)) is not int:
                    raise ValueError(f"declared TextMap locator absent/noninteger: {spec['path']} /{ordinal}/{name}")
                wanted.add(str(row[name]))
            rows.append((ordinal, row, raw))
        records[spec["path"]] = rows
    maps: dict[str, dict[str, dict[str, list[tuple]]]] = {}
    for locale in scope["languages"]:
        maps[locale] = {}
        for tier in ("main", "medium"):
            path = scope["textmaps"][locale][tier]
            found: dict[str, list[tuple]] = defaultdict(list)
            for ordinal, (key, value) in enumerate(pairs(root / path)):
                if key in wanted:
                    found[key].append((path, ordinal, "/" + key, value, file_seals[path]))
            maps[locale][tier] = found
    rows: dict[str, list[tuple]] = {table: [] for table in TABLES}
    states: Counter = Counter()
    roles: Counter = Counter()
    for spec in scope["sources"]:
        path = spec["path"]
        seal = file_seals[path]
        for ordinal, row, raw in records[path]:
            pointer = "/" + str(ordinal)
            cid = identity(run, "container", path, pointer)
            native_field = spec.get("native_id_field")
            native_id = None if native_field is None else str(row[native_field])
            rows["native_archive_container"].append((
                cid, run, spec["boundary"], "physical_row", path, seal["sha256"],
                seal["blob"], ordinal, pointer, native_field, native_id, raw,
                hashlib.sha256(raw.encode()).hexdigest(), None, None, None, None, None,
            ))
            for owner in spec["text_owners"]:
                field = owner["field"]
                locator = str(row[field])
                role = owner["owner_role_status"]
                for locale in scope["languages"]:
                    main = maps[locale]["main"].get(locator, [])
                    medium = maps[locale]["medium"].get(locator, [])
                    hits = main or medium
                    state = ("missing" if not hits else "resolved_main" if main and len(hits) == 1
                             else "resolved_medium" if medium and len(hits) == 1 else "ambiguous")
                    states[state] += 1
                    roles[role] += 1
                    slot = identity(run, "slot", path, pointer, field, locale)
                    rows["native_archive_slot"].append((
                        slot, cid, field, role, locale, locator, state, len(hits)
                    ))
                    for source_path, pair_ordinal, pair_pointer, text, text_seal in hits:
                        rows["native_archive_text"].append((
                            identity(run, "text", slot, source_path, pair_ordinal), slot,
                            source_path, text_seal["sha256"], text_seal["blob"],
                            pair_ordinal, pair_pointer, text, hashlib.sha256(text.encode()).hexdigest(),
                        ))
    for table in TABLES:
        rows[table].sort()
    return rows, states, roles


def build(source_root: Path, scope_path: Path, output: Path) -> dict:
    source_root, scope_path, output = Path(source_root), Path(scope_path), Path(output)
    if output.exists() or any(Path(str(output) + suffix).exists()
                              for suffix in ("-wal", "-shm", "-journal")):
        raise FileExistsError("output or SQLite sidecar already exists")
    scope, implementation, parser, scope_sha, run = _qualify(source_root, scope_path)
    rows, states, roles = _projection(source_root, scope, run)
    counts = {table: len(rows[table]) for table in TABLES}
    release = {
        "contract": "native-bulk-7.1-standalone-v1", "family_contract_version": "native-archive-evidence-v1",
        "snapshot_id": scope["snapshot_id"], "parser_build_id": parser, "run_id": run,
        "scope_sha256": scope_sha, "implementation": implementation,
        "counts": counts, "text_state_counts": dict(sorted(states.items())),
        "owner_role_counts": dict(sorted(roles.items())),
        "scope_complete_for_selected_paths": True, "full_7_1_native_text": False,
        "silent_skips": 0, "cross_snapshot_lookup_count": 0,
    }
    owned = None
    db = None
    try:
        fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        stat = output.stat()
        owned = (stat.st_dev, stat.st_ino)
        db = sqlite3.connect(output)
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript((ROOT / "schemas/native-archive-v1.sql").read_text())
        for table in TABLES:
            if rows[table]:
                db.executemany("INSERT INTO " + table + " VALUES(" + ",".join("?" * len(rows[table][0])) + ")", rows[table])
        db.execute("INSERT INTO native_archive_release VALUES(1,?)",
                   (json.dumps(release, sort_keys=True, separators=(",", ":")),))
        db.commit()
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("SQLite integrity/FK")
        db.execute("VACUUM")
        db.close()
        db = None
        _qualify(source_root, scope_path)
        if any(Path(str(output) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise ValueError("SQLite sidecar after build")
        return {**release, "artifact": {"size_bytes": output.stat().st_size, "sha256": sha(output)},
                "logical_seals": {table: hashlib.sha256("\n".join(
                    json.dumps(list(row), ensure_ascii=False, separators=(",", ":"))
                    for row in rows[table]).encode()).hexdigest() for table in TABLES}}
    except Exception:
        if db is not None:
            db.close()
        if owned is not None and output.exists():
            stat = output.stat()
            if (stat.st_dev, stat.st_ino) == owned:
                output.unlink()
        raise
