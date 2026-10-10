"""Independent Readable 7.1 verifier: rebuild all physical rows from source."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
TREE = "aa578c08c402af39b41f1ffc95124d10f5410272"
SNAPSHOT = "anime-game-data:7.1:" + COMMIT
SCHEMA = ROOT / "schemas/readable-evidence-v1.sql"
CODE = ("src/genshin_lore/readable_7_1_build.py", "src/genshin_lore/direct_text_source.py", "schemas/readable-evidence-v1.sql")


def require(condition, name):
    if not condition:
        raise ValueError("Readable 7.1 independent verifier: " + name)


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def file_digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def identity(prefix, *parts):
    return prefix + ":" + digest(canonical(parts).encode())[:40]


def source_lines(raw):
    text = raw.decode("utf-8")
    offset = 0
    for ordinal, unit in enumerate(text.splitlines(keepends=True)):
        size = len(unit.encode("utf-8"))
        body = unit.rstrip("\r\n")
        terminator = unit[len(body):]
        yield ordinal, offset, offset + size, body, terminator
        offset += size


def source_tree(root):
    data = subprocess.check_output(["git", "-C", str(root), "ls-tree", "-r", "-z", COMMIT, "--", "Readable"])
    result = {}
    for item in data.split(b"\0"):
        if item:
            metadata, path = item.split(b"\t", 1)
            mode, kind, blob = metadata.decode().split()
            require((mode, kind) == ("100644", "blob"), "Git Readable file type")
            result[path.decode()] = blob
    return result


def exact_table(db, table, expected):
    actual = db.execute("SELECT * FROM " + table + " ORDER BY id").fetchall()
    require(actual == sorted(expected), table + " full business row equality")
    h = hashlib.sha256()
    for row in actual:
        h.update((canonical(list(row)) + "\n").encode())
    return {"rows": len(actual), "sha256": h.hexdigest()}


def verify(source_root, scope_path, artifact_path, r0_artifact, document_artifact, document_report, report):
    root, scope_path, artifact = Path(source_root).resolve(), Path(scope_path).resolve(), Path(artifact_path).resolve()
    r0_artifact, document_artifact = Path(r0_artifact).resolve(), Path(document_artifact).resolve()
    scope_raw = scope_path.read_bytes()
    scope = json.loads(scope_raw)
    require((scope["source_commit"], scope["source_tree"], scope["snapshot_id"]) == (COMMIT, TREE, SNAPSHOT), "source scope pin")
    require(subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD", "HEAD^{tree}"], text=True).splitlines() == [COMMIT, TREE], "checkout pin")
    require(not subprocess.check_output(["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"], text=True).strip(), "checkout clean")
    require(not any(Path(str(artifact) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")), "SQLite sidecar")
    artifact_seal = {"size_bytes": artifact.stat().st_size, "sha256": file_digest(artifact)}
    require(report["artifact"] == artifact_seal, "artifact seal")
    require((r0_artifact.stat().st_size, file_digest(r0_artifact)) ==
            (scope["r0_dependency"]["size_bytes"], scope["r0_dependency"]["sha256"]), "7.1 R0 exact dependency")
    require(document_report["artifact"] == {"size_bytes": document_artifact.stat().st_size, "sha256": file_digest(document_artifact)}, "7.1 Document exact dependency")
    require(document_report["snapshot_id"] == SNAPSHOT and document_report["contract"] == "document-7.1-evidence-v1", "Document snapshot/contract")
    implementation = {path: file_digest(ROOT / path) for path in CODE}
    parser_id = identity("readable71-parser", implementation)
    run_id = identity("readable71-run", SNAPSHOT, parser_id, digest(scope_raw), document_report["artifact"]["sha256"], scope["r0_dependency"]["sha256"])
    require((report["implementation"], report["parser_build_id"], report["run_id"], report["scope_sha256"]) ==
            (implementation, parser_id, run_id, digest(scope_raw)), "implementation/parser/run")
    paths = source_tree(root)
    path_blob_seal = digest((json.dumps(sorted(paths.items()), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode())
    require(len(paths) == scope["readable"]["file_count"] and path_blob_seal == scope["readable"]["path_blob_set_sha256"], "Readable path/blob denominator")
    actual_paths = {path.relative_to(root).as_posix() for path in (root / "Readable").rglob("*.txt")}
    require(actual_paths == set(paths), "Readable checkout file set")
    r0 = sqlite3.connect("file:" + str(r0_artifact) + "?mode=ro&immutable=1", uri=True)
    r0_rows = r0.execute("SELECT relative_path,source_file_id,source_sha256,snapshot_id,ingest_run_id FROM source_file WHERE root='Readable'").fetchall()
    r0.close()
    require(len(r0_rows) == len(paths) and {row[0] for row in r0_rows} == set(paths), "R0 Readable physical set")
    for path, _, _, snapshot, run in r0_rows:
        require(snapshot == SNAPSHOT and run == scope["r0_dependency"]["ingest_run_id"], "R0 snapshot/run " + path)
    r0_map = {path: ("file:" + str(row_id), source_sha) for path, row_id, source_sha, _, _ in r0_rows}
    file_rows, line_rows, files = [], [], {}
    for path, blob in sorted(paths.items()):
        raw = (root / path).read_bytes()
        computed_blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        require(computed_blob == blob and r0_map[path][1] == digest(raw), "source/R0 bytes " + path)
        file_id = identity("readable71-file", run_id, path)
        files[path] = file_id
        lines = list(source_lines(raw))
        file_rows.append((file_id, run_id, path, digest(raw), blob, len(raw), "utf-8", len(lines), int(not raw), digest(raw),
                          "raw_readable_json_occurrence", "raw-physical-evidence-v1", scope["r0_dependency"]["ingest_run_id"], r0_map[path][0]))
        for ordinal, start, end, body, term in lines:
            line_rows.append((identity("readable71-line", file_id, ordinal), file_id, ordinal, f"/line/{ordinal}",
                              start, end, term, body, digest(body.encode())))
    localization_path = root / "ExcelBinOutput/LocalizationExcelConfigData.json"
    localization = json.loads(localization_path.read_text())
    relation_rows, candidate_rows = [], []
    states = Counter()

    def add(origin_family, contract, parser, exact_run, occurrence, source_path, pointer, kind, locator, boundary, target):
        rid = identity("readable71-relation", origin_family, exact_run, occurrence, source_path, pointer, kind, locator)
        present = target in files
        status = "resolved_unique" if present else "missing"
        relation_rows.append((rid, origin_family, contract, parser, exact_run, occurrence, source_path, pointer,
                              kind, locator, boundary, status, 1, int(present)))
        states[status] += 1
        if present:
            candidate_rows.append((identity("readable71-candidate", rid, target), rid, files[target], target, r0_map[target][0]))

    for ordinal, row in enumerate(localization):
        require(isinstance(row, dict) and type(row.get("id")) is int, "Localization row shape")
        for key, value in row.items():
            if not isinstance(value, str) or not value.startswith("ART/UI/Readable/"):
                continue
            target = "Readable/" + value[len("ART/UI/Readable/"):] + ".txt"
            add("source_localization", None, None, None, f"localization:{ordinal}",
                "ExcelBinOutput/LocalizationExcelConfigData.json", f"/{ordinal}/{key}",
                "localization_readable_path", value, "all pinned 7.1 Readable paths; exact exported path + .txt", target)
    document = sqlite3.connect("file:" + str(document_artifact) + "?mode=ro&immutable=1", uri=True)
    document.row_factory = sqlite3.Row
    release_row = document.execute("SELECT manifest_json FROM document_release WHERE id=1").fetchone()
    require(release_row is not None, "Document release missing")
    document_release = json.loads(release_row[0])
    require(document_release["run_id"] == document_report["run_id"] and document_release["snapshot_id"] == SNAPSHOT, "Document release binding")
    for row in document.execute(
        "SELECT r.id,r.source_path,r.pointer,r.raw_value,c.target_source_path "
        "FROM document_relation r JOIN document_candidate c ON c.relation_id=r.id "
        "WHERE r.kind='localization_readable' ORDER BY r.id,c.id"
    ):
        target = row["target_source_path"]
        add("document_archive", document_release["contract"], document_release["parser_build_id"], document_release["run_id"],
            row["id"], row["source_path"], row["pointer"], "document_localization_readable", row["raw_value"],
            "exact same-snapshot Document candidate path", target)
    document.close()
    db = sqlite3.connect("file:" + str(artifact) + "?mode=ro&immutable=1", uri=True)
    try:
        require(db.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite integrity")
        require(not db.execute("PRAGMA foreign_key_check").fetchall(), "SQLite FK")
        seals = {
            "readable_file": exact_table(db, "readable_file", file_rows),
            "readable_line": exact_table(db, "readable_line", line_rows),
            "readable_relation": exact_table(db, "readable_relation", relation_rows),
            "readable_candidate": exact_table(db, "readable_candidate", candidate_rows),
        }
        releases = db.execute("SELECT id,manifest_json FROM readable_release").fetchall()
        require(len(releases) == 1 and releases[0][0] == 1, "single release")
        release = json.loads(releases[0][1])
        require(release == {key: value for key, value in report.items() if key not in ("artifact", "build_seconds")}, "release/report binding")
        require(release["logical_seals"] == seals, "logical seals")
        require(release["counts"] == {"readable_file": len(file_rows), "readable_line": len(line_rows),
                                      "readable_relation": len(relation_rows), "readable_candidate": len(candidate_rows)}, "row counts")
        require(release["relation_states"] == dict(states), "relation states")
        schema = sqlite3.connect(":memory:")
        schema.executescript(SCHEMA.read_text())
        tables = ("readable_file", "readable_line", "readable_relation", "readable_candidate", "readable_release")
        for table in tables:
            schema.execute(f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'immutable evidence'); END")
            schema.execute(f"CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'immutable evidence'); END")
            schema.execute(f"CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE id=NEW.id) BEGIN SELECT RAISE(ABORT,'immutable evidence collision'); END")
        structure = "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        require(db.execute(structure).fetchall() == schema.execute(structure).fetchall(), "schema/write-once structure")
        schema.close()
    finally:
        db.close()
    require(file_digest(artifact) == artifact_seal["sha256"], "artifact drift")
    require(not any(Path(str(artifact) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")), "post-verify sidecar")
    return {"status": "PASS", "artifact": artifact_seal, "snapshot_id": SNAPSHOT, "run_id": run_id,
            "counts": release["counts"], "logical_seals": seals, "relation_states": dict(states),
            "source_scope_complete": True, "silent_skip_count": 0, "cross_snapshot_identity_merge_count": 0}
