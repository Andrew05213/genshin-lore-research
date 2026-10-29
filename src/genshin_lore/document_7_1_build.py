"""Snapshot-local Document 7.1 builder; never mutates the 7.0 implementation."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

from .document_7_1_physical import document_row, localization_readable_locators
from .loading_tips_source import array_records, pairs

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
TREE = "aa578c08c402af39b41f1ffc95124d10f5410272"
SNAPSHOT = "anime-game-data:7.1:" + COMMIT
PRIMARY = "ExcelBinOutput/DocumentExcelConfigData.json"
LOCALIZATION = "ExcelBinOutput/LocalizationExcelConfigData.json"
CONTRACT = "document-7.1-evidence-v1"
SCHEMA = ROOT / "schemas/document-v1.sql"
IMPL = ("src/genshin_lore/document_7_1_build.py", "src/genshin_lore/document_7_1_physical.py", "schemas/document-v1.sql")


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def identity(*parts):
    return "document71:" + sha(canonical(parts).encode())[:40]


def git_blobs(root, prefix):
    data = subprocess.check_output(["git", "-C", str(root), "ls-tree", "-r", "-z", COMMIT, "--", prefix])
    result = {}
    for item in data.split(b"\0"):
        if not item:
            continue
        meta, path = item.split(b"\t", 1)
        mode, kind, blob = meta.decode().split()
        if mode != "100644" or kind != "blob":
            raise ValueError("unregistered Git tree entry")
        result[path.decode()] = blob
    return result


def exact_bytes(root, path, blob):
    raw = (root / path).read_bytes()
    if hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != blob:
        raise ValueError("pinned source drift: " + path)
    return raw


def qualify(root, scope_path):
    root, scope_path = Path(root).resolve(), Path(scope_path).resolve()
    raw = scope_path.read_bytes()
    scope = json.loads(raw)
    if (scope.get("contract"), scope.get("source_commit"), scope.get("source_tree"), scope.get("snapshot_id")) != (
        "document-readable-7.1-source-scope-v1", COMMIT, TREE, SNAPSHOT,
    ):
        raise ValueError("7.1 Document source scope")
    if subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD", "HEAD^{tree}"], text=True).splitlines() != [COMMIT, TREE]:
        raise ValueError("7.1 source checkout pin")
    if subprocess.check_output(["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"], text=True).strip():
        raise ValueError("7.1 source checkout dirty")
    readable = git_blobs(root, "Readable")
    seal = sha((json.dumps(sorted(readable.items()), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode())
    if len(readable) != scope["readable"]["file_count"] or seal != scope["readable"]["path_blob_set_sha256"]:
        raise ValueError("7.1 Readable path/blob denominator")
    declared = {item["path"]: item for item in scope["document_sources"]}
    for path, item in declared.items():
        blob = subprocess.check_output(["git", "-C", str(root), "rev-parse", COMMIT + ":" + path], text=True).strip()
        raw_file = exact_bytes(root, path, blob)
        if (len(raw_file), sha(raw_file), blob) != (item["size_bytes"], item["sha256"], item["blob"]):
            raise ValueError("7.1 Document/TextMap source seal: " + path)
    return root, scope, raw, readable, declared


def build(source_root, scope_path, output):
    started = time.monotonic()
    root, scope, scope_raw, readable, declared = qualify(source_root, scope_path)
    output = Path(output).resolve()
    if output.exists() or output.is_symlink() or any(Path(str(output) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise FileExistsError("Document 7.1 target or sidecar exists")
    implementation = {path: file_sha(ROOT / path) for path in IMPL}
    parser_id = identity("parser", implementation)
    run_id = identity("run", SNAPSHOT, parser_id, sha(scope_raw))
    docs = list(array_records(root / PRIMARY))
    locs = list(array_records(root / LOCALIZATION))
    if len(docs) != scope["document_primary_rows"] or len(locs) != scope["document_source_row_counts"][LOCALIZATION]:
        raise ValueError("7.1 Document/Localization row denominator")
    lookup = defaultdict(list)
    for ordinal, row, _ in locs:
        for locator in localization_readable_locators(row, ordinal):
            pass
        lookup[row["id"]].append((ordinal, row))
    wanted = set()
    for ordinal, row, _ in docs:
        wanted.add(str(document_row(row, ordinal)["title_text_hash"]))
    textmaps = {}
    for locale in ("CHS", "EN", "JP"):
        for tier in ("", "_Medium"):
            path = f"TextMap/TextMap{tier}{locale}.json"
            selected = defaultdict(list)
            for pair_ordinal, (key, value) in enumerate(pairs(root / path)):
                if key in wanted:
                    selected[key].append((path, pair_ordinal, "/" + key, value))
            textmaps[locale, tier] = selected
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, scratch_name = tempfile.mkstemp(prefix=".document71-stage-", suffix=".sqlite", dir=output.parent)
    os.close(fd)
    scratch = Path(scratch_name)
    compact = Path(scratch_name + ".compact")
    db = None
    states = Counter()
    relations = defaultdict(Counter)
    readable_seals = {}

    def target_seal(path):
        if path not in readable:
            return None
        if path not in readable_seals:
            raw_file = exact_bytes(root, path, readable[path])
            readable_seals[path] = {"path": path, "size_bytes": len(raw_file), "sha256": sha(raw_file), "blob": readable[path]}
        return readable_seals[path]

    try:
        db = sqlite3.connect(scratch)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=OFF")
        db.executescript(SCHEMA.read_text())

        def relation(row_id, origin, kind, source_path, pointer, raw_value, boundary, complete, targets):
            status = ("missing" if not targets else "resolved" if len(targets) == 1 else "ambiguous") if complete else "mapping_unresolved"
            ref = identity(run_id, "relation", row_id, origin, kind, source_path, pointer)
            db.execute("INSERT INTO document_relation VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                ref, row_id, origin, kind, source_path, pointer,
                json.dumps(raw_value, ensure_ascii=True), boundary, status, len(targets), int(complete),
            ))
            relations[kind][status] += 1
            for path, target_pointer, seal in targets:
                db.execute("INSERT INTO document_candidate VALUES(?,?,?,?,?)", (
                    identity(ref, path, target_pointer), ref, path, target_pointer, json.dumps(seal, sort_keys=True),
                ))

        for ordinal, row, raw_json in docs:
            decoded = document_row(row, ordinal)
            row_id = identity(run_id, "row", PRIMARY, ordinal)
            db.execute("INSERT INTO document_row VALUES(?,?,?,?,?,?,?,?)", (
                row_id, run_id, "document", ordinal, str(decoded["native_id"]), PRIMARY, f"/{ordinal}", raw_json,
            ))
            for locale in ("CHS", "EN", "JP"):
                value = str(decoded["title_text_hash"])
                hits = textmaps[locale, ""].get(value, []) or textmaps[locale, "_Medium"].get(value, [])
                status = "missing" if not hits else "resolved" if len(hits) == 1 else "ambiguous"
                states[status] += 1
                slot = identity(run_id, "slot", PRIMARY, ordinal, "titleTextMapHash", locale)
                db.execute("INSERT INTO document_slot VALUES(?,?,?,?,?,?)", (
                    slot, row_id, "titleTextMapHash", locale, value, status,
                ))
                for path, pair_ordinal, pointer, text in hits:
                    db.execute("INSERT INTO document_text VALUES(?,?,?,?,?,?)", (
                        identity(slot, path, pair_ordinal), slot, path, pair_ordinal, pointer, text,
                    ))
            for field in ("questIDList", "questContentLocalizedId", "contentLocalizedId"):
                for element, value in enumerate(row.get(field, [])):
                    origin = f"/{ordinal}/{field}/{element}"
                    matches = lookup.get(value, [])
                    complete = field != "contentLocalizedId"
                    targets = [(LOCALIZATION, f"/{loc_ordinal}", {**declared[LOCALIZATION], "native_id": loc_row["id"]}) for loc_ordinal, loc_row in matches]
                    relation(row_id, origin, "document_localization", PRIMARY, origin, value,
                             "all physical Localization rows; " + ("explicit/project locator contract" if complete else "equality observations only; namespace unresolved"), complete, targets)
                    if not complete:
                        continue
                    for loc_ordinal, loc_row in matches:
                        for locator in localization_readable_locators(loc_row, loc_ordinal):
                            path = locator["target_path"]
                            seal = target_seal(path) if path is not None else None
                            hits = [(path, "", seal)] if seal is not None else []
                            relation(row_id, origin, "localization_readable", LOCALIZATION, locator["pointer"], locator["raw_locator"],
                                     "all pinned Readable physical paths; 7.1 source-authored exported path" if path else "non-Readable/unknown path namespace; no inferred file",
                                     path is not None, hits)
            if "contentPath" in row:
                origin = f"/{ordinal}/contentPath"
                relation(row_id, origin, "document_content_path", PRIMARY, origin, row["contentPath"],
                         "raw media locator; not a Readable namespace", False, [])
        release = {
            "contract": CONTRACT, "snapshot_id": SNAPSHOT,
            "run_id": run_id, "parser_build_id": parser_id,
            "implementation": implementation, "scope_sha256": sha(scope_raw),
            "rows": len(docs), "slots": len(docs) * 3,
            "text_states": dict(states), "relationship_states": {key: dict(value) for key, value in relations.items()},
            "silent_skip_count": 0, "product_accepted": False,
        }
        db.execute("INSERT INTO document_release VALUES(1,?)", (json.dumps(release, sort_keys=True),))
        db.commit()
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Document 7.1 SQLite integrity")
        db.execute("VACUUM INTO ?", (str(compact),))
        db.close()
        db = None
        qualify(root, scope_path)
        if output.exists() or output.is_symlink():
            raise FileExistsError("Document 7.1 output appeared during build")
        os.link(compact, output)
        compact.unlink()
        scratch.unlink()
        return {**release, "artifact": {"size_bytes": output.stat().st_size, "sha256": file_sha(output)},
                "build_seconds": time.monotonic() - started}
    finally:
        if db is not None:
            db.close()
