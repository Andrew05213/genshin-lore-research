"""Independent full-source Document 7.1 verifier; no builder/decoder imports."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from .loading_tips_source import array_records, pairs

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
TREE = "aa578c08c402af39b41f1ffc95124d10f5410272"
SNAPSHOT = "anime-game-data:7.1:" + COMMIT
PRIMARY = "ExcelBinOutput/DocumentExcelConfigData.json"
LOCALIZATION = "ExcelBinOutput/LocalizationExcelConfigData.json"
SCHEMA = ROOT / "schemas/document-v1.sql"
CODE = ("src/genshin_lore/document_7_1_build.py", "src/genshin_lore/document_7_1_physical.py", "schemas/document-v1.sql")


def require(condition, name):
    if not condition:
        raise ValueError("Document 7.1 independent verifier: " + name)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def file_digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def ident(*parts):
    return "document71:" + digest(encoded(parts).encode())[:40]


def tree(root, prefix):
    data = subprocess.check_output(["git", "-C", str(root), "ls-tree", "-r", "-z", COMMIT, "--", prefix])
    out = {}
    for item in data.split(b"\0"):
        if item:
            meta, path = item.split(b"\t", 1)
            mode, kind, blob = meta.decode().split()
            require((mode, kind) == ("100644", "blob"), "Git tree mode")
            out[path.decode()] = blob
    return out


def verify(source_root, scope_path, artifact_path, report):
    root, scope_path, artifact = Path(source_root).resolve(), Path(scope_path).resolve(), Path(artifact_path).resolve()
    scope_raw = scope_path.read_bytes()
    scope = json.loads(scope_raw)
    require((scope["source_commit"], scope["source_tree"], scope["snapshot_id"]) == (COMMIT, TREE, SNAPSHOT), "source pin")
    require(subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD", "HEAD^{tree}"], text=True).splitlines() == [COMMIT, TREE], "checkout pin")
    require(not subprocess.check_output(["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"], text=True).strip(), "checkout clean")
    require(not any(Path(str(artifact) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")), "sidecar")
    actual_artifact = {"size_bytes": artifact.stat().st_size, "sha256": file_digest(artifact)}
    require(report["artifact"] == actual_artifact, "artifact seal")
    implementation = {path: file_digest(ROOT / path) for path in CODE}
    parser_id = ident("parser", implementation)
    run_id = ident("run", SNAPSHOT, parser_id, digest(scope_raw))
    require((report["implementation"], report["parser_build_id"], report["run_id"], report["scope_sha256"]) ==
            (implementation, parser_id, run_id, digest(scope_raw)), "implementation/parser/run")
    readable = tree(root, "Readable")
    readable_seal = digest((json.dumps(sorted(readable.items()), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode())
    require(len(readable) == scope["readable"]["file_count"] and readable_seal == scope["readable"]["path_blob_set_sha256"], "Readable denominator")
    physical = {path.relative_to(root).as_posix() for path in (root / "Readable").rglob("*.txt")}
    require(physical == set(readable), "Readable source file set")
    document_sources = {item["path"]: item for item in scope["document_sources"]}
    for path, seal in document_sources.items():
        raw = (root / path).read_bytes()
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        require((len(raw), digest(raw), blob) == (seal["size_bytes"], seal["sha256"], seal["blob"]), "source seal " + path)
        require(subprocess.check_output(["git", "-C", str(root), "rev-parse", COMMIT + ":" + path], text=True).strip() == blob, "Git source blob")
    docs = list(array_records(root / PRIMARY))
    locs = list(array_records(root / LOCALIZATION))
    require(len(docs) == scope["document_primary_rows"] and len(locs) == scope["document_source_row_counts"][LOCALIZATION], "Document/Localization row denominator")
    index = defaultdict(list)
    for ordinal, row, _ in locs:
        require(isinstance(row, dict) and type(row.get("id")) is int, "Localization row ID")
        for field in ("MBOPLLJEHCJ", "FCNGPFLCLFA"):
            require(field in row and isinstance(row[field], str), "CHS authored locator " + field)
        for field in ("enPath", "jpPath"):
            if field in row:
                require(isinstance(row[field], str), "optional authored locator " + field)
        index[row["id"]].append((ordinal, row))
    wanted = set()
    for _, row, _ in docs:
        require(isinstance(row, dict) and type(row.get("id")) is int and type(row.get("titleTextMapHash")) is int, "Document required fields")
        for field in ("questIDList", "contentLocalizedId", "questContentLocalizedId"):
            if field in row:
                require(isinstance(row[field], list) and all(type(item) is int for item in row[field]), "Document locator list " + field)
        wanted.add(str(row["titleTextMapHash"]))
    maps = {}
    for locale in ("CHS", "EN", "JP"):
        for tier in ("", "_Medium"):
            path = f"TextMap/TextMap{tier}{locale}.json"
            selected = defaultdict(list)
            for ordinal, (key, value) in enumerate(pairs(root / path)):
                if key in wanted:
                    selected[key].append((path, ordinal, "/" + key, value))
            maps[locale, tier] = selected
    db = sqlite3.connect("file:" + str(artifact) + "?mode=ro&immutable=1", uri=True)
    try:
        require(db.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite integrity")
        require(not db.execute("PRAGMA foreign_key_check").fetchall(), "SQLite FK")
        expected_structure = sqlite3.connect(":memory:")
        expected_structure.executescript(SCHEMA.read_text())
        structure_sql = "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        require(db.execute(structure_sql).fetchall() == expected_structure.execute(structure_sql).fetchall(), "SQLite schema")
        expected_structure.close()
        releases = db.execute("SELECT id,manifest_json FROM document_release").fetchall()
        require(len(releases) == 1 and releases[0][0] == 1, "single release")
        release = json.loads(releases[0][1])
        require(release == {key: value for key, value in report.items() if key not in ("artifact", "build_seconds")}, "release/report binding")
        require(release["contract"] == "document-7.1-evidence-v1" and release["snapshot_id"] == SNAPSHOT, "release identity")
        rows, slots, texts, relations, candidates = [], [], [], [], []
        title_states = Counter()
        relation_states = defaultdict(Counter)
        readable_seals = {}

        def source_target(path):
            if path not in readable:
                return None
            if path not in readable_seals:
                raw = (root / path).read_bytes()
                blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
                require(blob == readable[path], "Readable candidate blob")
                readable_seals[path] = {"path": path, "size_bytes": len(raw), "sha256": digest(raw), "blob": blob}
            return readable_seals[path]

        def add_relation(row_id, origin, kind, source_path, pointer, raw_value, boundary, complete, targets):
            status = ("missing" if not targets else "resolved" if len(targets) == 1 else "ambiguous") if complete else "mapping_unresolved"
            rid = ident(run_id, "relation", row_id, origin, kind, source_path, pointer)
            relations.append((rid, row_id, origin, kind, source_path, pointer, json.dumps(raw_value, ensure_ascii=True), boundary, status, len(targets), int(complete)))
            relation_states[kind][status] += 1
            for path, target_pointer, seal in targets:
                candidates.append((ident(rid, path, target_pointer), rid, path, target_pointer, json.dumps(seal, sort_keys=True)))

        for ordinal, row, raw_json in docs:
            row_id = ident(run_id, "row", PRIMARY, ordinal)
            rows.append((row_id, run_id, "document", ordinal, str(row["id"]), PRIMARY, f"/{ordinal}", raw_json))
            for locale in ("CHS", "EN", "JP"):
                value = str(row["titleTextMapHash"])
                hits = maps[locale, ""].get(value, []) or maps[locale, "_Medium"].get(value, [])
                status = "missing" if not hits else "resolved" if len(hits) == 1 else "ambiguous"
                title_states[status] += 1
                slot_id = ident(run_id, "slot", PRIMARY, ordinal, "titleTextMapHash", locale)
                slots.append((slot_id, row_id, "titleTextMapHash", locale, value, status))
                for path, pair_ordinal, pointer, text in hits:
                    texts.append((ident(slot_id, path, pair_ordinal), slot_id, path, pair_ordinal, pointer, text))
            for field in ("questIDList", "questContentLocalizedId", "contentLocalizedId"):
                for j, value in enumerate(row.get(field, [])):
                    origin = f"/{ordinal}/{field}/{j}"
                    matches = index.get(value, [])
                    complete = field != "contentLocalizedId"
                    targets = [(LOCALIZATION, f"/{k}", {**document_sources[LOCALIZATION], "native_id": loc["id"]}) for k, loc in matches]
                    add_relation(row_id, origin, "document_localization", PRIMARY, origin, value,
                                 "all physical Localization rows; " + ("explicit/project locator contract" if complete else "equality observations only; namespace unresolved"), complete, targets)
                    if not complete:
                        continue
                    for k, loc in matches:
                        for key, locale in (("MBOPLLJEHCJ", "CHS"), ("FCNGPFLCLFA", "CHS"), ("enPath", "EN"), ("jpPath", "JP")):
                            if key not in loc:
                                continue
                            value = loc[key]
                            prefix = f"ART/UI/Readable/{locale}/"
                            suffix = value[len(prefix):] if value.startswith(prefix) else None
                            mapped = suffix is not None and bool(suffix) and all(part not in ("", ".", "..") for part in suffix.split("/"))
                            target = "Readable/" + value[len("ART/UI/Readable/"):] + ".txt" if mapped else None
                            seal = source_target(target) if target is not None else None
                            targets2 = [(target, "", seal)] if seal is not None else []
                            add_relation(row_id, origin, "localization_readable", LOCALIZATION, f"/{k}/{key}", value,
                                         "all pinned Readable physical paths; 7.1 source-authored exported path" if mapped else "non-Readable/unknown path namespace; no inferred file",
                                         mapped, targets2)
            if "contentPath" in row:
                origin = f"/{ordinal}/contentPath"
                add_relation(row_id, origin, "document_content_path", PRIMARY, origin, row["contentPath"],
                             "raw media locator; not a Readable namespace", False, [])
        seals = {}
        for table, expected in (("document_row", rows), ("document_slot", slots), ("document_text", texts),
                                ("document_relation", relations), ("document_candidate", candidates)):
            actual = db.execute("SELECT * FROM " + table + " ORDER BY id").fetchall()
            require(actual == sorted(expected), table + " no extras/omissions/source equality")
            seals[table] = {"rows": len(actual), "sha256": digest(json.dumps(actual, ensure_ascii=True, separators=(",", ":")).encode())}
        require(release["rows"] == len(rows) and release["slots"] == len(slots), "row/slot counts")
        require(release["text_states"] == dict(title_states), "text states")
        require(release["relationship_states"] == {key: dict(value) for key, value in relation_states.items()}, "relation states")
    finally:
        db.close()
    require(file_digest(artifact) == actual_artifact["sha256"], "artifact changed during verification")
    require(not any(Path(str(artifact) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")), "post-verify sidecar")
    return {
        "status": "PASS", "artifact": actual_artifact, "snapshot_id": SNAPSHOT, "run_id": run_id,
        "logical_seals": seals, "text_states": dict(title_states),
        "relationship_states": {key: dict(value) for key, value in relation_states.items()},
        "source_scope_complete": True, "silent_skip_count": 0, "wrong_cross_snapshot_identity_merge_count": 0,
    }
