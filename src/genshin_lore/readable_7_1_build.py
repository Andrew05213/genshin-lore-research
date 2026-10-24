"""Snapshot-local Readable 7.1 evidence candidate with exact R0/Document bindings."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path

from .direct_text_source import split_lines

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
TREE = "aa578c08c402af39b41f1ffc95124d10f5410272"
SNAPSHOT = "anime-game-data:7.1:" + COMMIT
SCHEMA = ROOT / "schemas/readable-evidence-v1.sql"
IMPL = ("src/genshin_lore/readable_7_1_build.py", "src/genshin_lore/direct_text_source.py", "schemas/readable-evidence-v1.sql")


def canon(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ident(prefix, *parts):
    return prefix + ":" + sha(canon(parts).encode())[:40]


def blobs(root):
    raw = subprocess.check_output(["git", "-C", str(root), "ls-tree", "-r", "-z", COMMIT, "--", "Readable"])
    result = {}
    for entry in raw.split(b"\0"):
        if entry:
            metadata, path = entry.split(b"\t", 1)
            mode, kind, oid = metadata.decode().split()
            if (mode, kind) != ("100644", "blob"):
                raise ValueError("Readable Git file type")
            result[path.decode()] = oid
    return result


def source_bytes(root, path, blob):
    raw = (root / path).read_bytes()
    if hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != blob:
        raise ValueError("Readable source differs from Git tree: " + path)
    return raw


def logical(db, tables):
    seals = {}
    for table in tables:
        digest = hashlib.sha256()
        rows = 0
        for item in db.execute("SELECT * FROM " + table + " ORDER BY id"):
            digest.update((canon(list(item)) + "\n").encode())
            rows += 1
        seals[table] = {"rows": rows, "sha256": digest.hexdigest()}
    return seals


def immutable_triggers(db):
    for table in ("readable_file", "readable_line", "readable_relation", "readable_candidate", "readable_release"):
        db.execute(f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'immutable evidence'); END")
        db.execute(f"CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'immutable evidence'); END")
        db.execute(f"CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE id=NEW.id) BEGIN SELECT RAISE(ABORT,'immutable evidence collision'); END")


def build(source_root, scope_path, output, r0_artifact, document_artifact, document_report):
    started = time.monotonic()
    root, scope_path, output = Path(source_root).resolve(), Path(scope_path).resolve(), Path(output).resolve()
    r0_artifact, document_artifact = Path(r0_artifact).resolve(), Path(document_artifact).resolve()
    scope_raw = scope_path.read_bytes()
    scope = json.loads(scope_raw)
    if (scope["source_commit"], scope["source_tree"], scope["snapshot_id"]) != (COMMIT, TREE, SNAPSHOT):
        raise ValueError("Readable 7.1 source scope")
    if subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD", "HEAD^{tree}"], text=True).splitlines() != [COMMIT, TREE]:
        raise ValueError("Readable 7.1 source checkout pin")
    if subprocess.check_output(["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"], text=True).strip():
        raise ValueError("Readable 7.1 source checkout dirty")
    tree = blobs(root)
    seal = sha((json.dumps(sorted(tree.items()), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode())
    if len(tree) != scope["readable"]["file_count"] or seal != scope["readable"]["path_blob_set_sha256"]:
        raise ValueError("Readable 7.1 denominator")
    physical = {path.relative_to(root).as_posix() for path in (root / "Readable").rglob("*.txt")}
    if physical != set(tree):
        raise ValueError("Readable physical checkout/denominator")
    if (r0_artifact.stat().st_size, file_sha(r0_artifact)) != (scope["r0_dependency"]["size_bytes"], scope["r0_dependency"]["sha256"]):
        raise ValueError("7.1 R0 dependency seal")
    if document_report["artifact"] != {"size_bytes": document_artifact.stat().st_size, "sha256": file_sha(document_artifact)}:
        raise ValueError("7.1 Document dependency seal")
    if document_report["snapshot_id"] != SNAPSHOT or document_report["contract"] != "document-7.1-evidence-v1":
        raise ValueError("7.1 Document dependency snapshot")
    r0 = sqlite3.connect("file:" + str(r0_artifact) + "?mode=ro&immutable=1", uri=True)
    r0_rows = r0.execute("SELECT relative_path,source_file_id,source_sha256,snapshot_id,ingest_run_id FROM source_file WHERE root='Readable'").fetchall()
    r0.close()
    if len(r0_rows) != len(tree) or {row[0] for row in r0_rows} != set(tree):
        raise ValueError("R0 Readable source set mismatch")
    for path, _, _, snapshot, run in r0_rows:
        if snapshot != SNAPSHOT or run != scope["r0_dependency"]["ingest_run_id"]:
            raise ValueError("R0 Readable cross-snapshot/run binding: " + path)
    r0_map = {path: (f"file:{fid}", raw_sha) for path, fid, raw_sha, _, _ in r0_rows}
    implementation = {path: file_sha(ROOT / path) for path in IMPL}
    parser_id = ident("readable71-parser", implementation)
    run_id = ident("readable71-run", SNAPSHOT, parser_id, sha(scope_raw), document_report["artifact"]["sha256"], scope["r0_dependency"]["sha256"])
    if output.exists() or output.is_symlink() or any(Path(str(output) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise FileExistsError("Readable candidate target or sidecar exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, stage_name = tempfile.mkstemp(prefix=".readable71-stage-", suffix=".sqlite", dir=output.parent)
    os.close(fd)
    stage = Path(stage_name)
    compact = Path(stage_name + ".compact")
    db = None
    try:
        db = sqlite3.connect(stage)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=OFF")
        db.executescript(SCHEMA.read_text())
        files = {}
        total_lines = 0
        states = Counter()
        last = time.monotonic()
        for path, blob in sorted(tree.items()):
            raw = source_bytes(root, path, blob)
            digest = sha(raw)
            if r0_map[path][1] != digest:
                raise ValueError("R0/Readable source sha mismatch: " + path)
            file_id = ident("readable71-file", run_id, path)
            files[path] = file_id
            lines = list(split_lines(raw))
            db.execute("INSERT INTO readable_file VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                file_id, run_id, path, digest, blob, len(raw), "utf-8", len(lines), int(not raw), digest,
                "raw_readable_json_occurrence", "raw-physical-evidence-v1", scope["r0_dependency"]["ingest_run_id"], r0_map[path][0],
            ))
            for ordinal, start, end, text, terminator in lines:
                db.execute("INSERT INTO readable_line VALUES(?,?,?,?,?,?,?,?,?)", (
                    ident("readable71-line", file_id, ordinal), file_id, ordinal, f"/line/{ordinal}",
                    start, end, terminator, text, sha(text.encode()),
                ))
            total_lines += len(lines)
            if time.monotonic() - last >= 30:
                print(canon({"phase": "readable_files", "files": len(files), "lines": total_lines}), flush=True)
                last = time.monotonic()
        localization = json.loads((root / "ExcelBinOutput/LocalizationExcelConfigData.json").read_text())
        candidates = 0

        def add_relation(origin_family, origin_contract, origin_parser, origin_run, occurrence, source_path, pointer, kind, locator, boundary, target):
            nonlocal candidates
            rid = ident("readable71-relation", origin_family, origin_run, occurrence, source_path, pointer, kind, locator)
            hit = target in files
            status = "resolved_unique" if hit else "missing"
            db.execute("INSERT INTO readable_relation VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                rid, origin_family, origin_contract, origin_parser, origin_run, occurrence, source_path, pointer,
                kind, locator, boundary, status, 1, int(hit),
            ))
            states[status] += 1
            if hit:
                db.execute("INSERT INTO readable_candidate VALUES(?,?,?,?,?)", (
                    ident("readable71-candidate", rid, target), rid, files[target], target, r0_map[target][0],
                ))
                candidates += 1

        for ordinal, row in enumerate(localization):
            if not isinstance(row, dict) or type(row.get("id")) is not int:
                raise ValueError("7.1 Localization root shape")
            for key, value in row.items():
                if not isinstance(value, str) or not value.startswith("ART/UI/Readable/"):
                    continue
                target = "Readable/" + value[len("ART/UI/Readable/"):] + ".txt"
                add_relation("source_localization", None, None, None, f"localization:{ordinal}",
                             "ExcelBinOutput/LocalizationExcelConfigData.json", f"/{ordinal}/{key}",
                             "localization_readable_path", value, "all pinned 7.1 Readable paths; exact exported path + .txt", target)
        document = sqlite3.connect("file:" + str(document_artifact) + "?mode=ro&immutable=1", uri=True)
        document.row_factory = sqlite3.Row
        release = json.loads(document.execute("SELECT manifest_json FROM document_release WHERE id=1").fetchone()[0])
        if release["run_id"] != document_report["run_id"] or release["snapshot_id"] != SNAPSHOT:
            raise ValueError("Document/Readable exact run binding")
        for row in document.execute(
            "SELECT r.id,r.source_path,r.pointer,r.raw_value,c.target_source_path "
            "FROM document_relation r JOIN document_candidate c ON c.relation_id=r.id "
            "WHERE r.kind='localization_readable' ORDER BY r.id,c.id"
        ):
            target = row["target_source_path"]
            add_relation("document_archive", release["contract"], release["parser_build_id"], release["run_id"],
                         row["id"], row["source_path"], row["pointer"], "document_localization_readable",
                         row["raw_value"], "exact same-snapshot Document candidate path", target)
        document.close()
        tables = ("readable_file", "readable_line", "readable_relation", "readable_candidate")
        logical_seals = logical(db, tables)
        release = {
            "contract": "readable-7.1-evidence-v1", "snapshot_id": SNAPSHOT,
            "parser_build_id": parser_id, "run_id": run_id, "scope_sha256": sha(scope_raw),
            "implementation": implementation,
            "r0_dependency": scope["r0_dependency"], "document_dependency": document_report["artifact"],
            "counts": {"readable_file": len(files), "readable_line": total_lines,
                       "readable_relation": sum(states.values()), "readable_candidate": candidates},
            "logical_seals": logical_seals, "relation_states": dict(states),
            "raw_body_blob_bytes_copied": 0, "physical_tree_complete": True,
            "structured_body_projection_complete": True, "product_accepted": False,
        }
        db.execute("INSERT INTO readable_release VALUES(1,?)", (canon(release),))
        immutable_triggers(db)
        db.commit()
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Readable SQLite integrity")
        db.execute("VACUUM INTO ?", (str(compact),))
        db.close()
        db = None
        if file_sha(document_artifact) != document_report["artifact"]["sha256"] or file_sha(r0_artifact) != scope["r0_dependency"]["sha256"]:
            raise ValueError("Readable dependency changed during build")
        if sha(scope_path.read_bytes()) != sha(scope_raw):
            raise ValueError("Readable scope changed during build")
        if {path: file_sha(ROOT / path) for path in IMPL} != implementation:
            raise ValueError("Readable implementation changed during build")
        if output.exists() or output.is_symlink():
            raise FileExistsError("Readable output appeared during build")
        os.link(compact, output)
        compact.unlink()
        stage.unlink()
        return {**release, "artifact": {"size_bytes": output.stat().st_size, "sha256": file_sha(output)},
                "build_seconds": time.monotonic() - started}
    finally:
        if db is not None:
            db.close()
