"""Independent all-row verifier for derived 7.1 Document/Readable titles."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "schemas/source-context-7.1-v1.sql"
SNAPSHOT = "anime-game-data:7.1:9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
CODE = ("src/genshin_lore/source_context_7_1_build.py", "schemas/source-context-7.1-v1.sql")


def require(value, label):
    if not value:
        raise ValueError("7.1 source-context verifier: " + label)


def enc(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ident(*parts):
    return "source-context71:" + sha(enc(parts).encode())[:40]


def verify(document_artifact, document_report, document_verification,
           readable_artifact, readable_report, readable_verification,
           context_artifact, context_report):
    doc_path, read_path, context_path = map(lambda path: Path(path).resolve(),
                                            (document_artifact, readable_artifact, context_artifact))
    for name, path, build, proof in (("Document", doc_path, document_report, document_verification),
                                     ("Readable", read_path, readable_report, readable_verification)):
        seal = {"size_bytes": path.stat().st_size, "sha256": file_sha(path)}
        require(build["artifact"] == seal and proof["status"] == "PASS" and proof["artifact"] == seal, name + " independent dependency")
        require(build["snapshot_id"] == SNAPSHOT, name + " snapshot")
    require(readable_report["document_dependency"] == document_report["artifact"], "Readable/Document exact binding")
    context_seal = {"size_bytes": context_path.stat().st_size, "sha256": file_sha(context_path)}
    require(context_report["artifact"] == context_seal, "context artifact seal")
    implementation = {path: file_sha(ROOT / path) for path in CODE}
    run = ident("run", SNAPSHOT, implementation, document_report["run_id"], readable_report["run_id"])
    require(context_report["implementation"] == implementation and context_report["run_id"] == run, "implementation/run")
    doc = sqlite3.connect("file:" + str(doc_path) + "?mode=ro&immutable=1", uri=True)
    doc.row_factory = sqlite3.Row
    readable = sqlite3.connect("file:" + str(read_path) + "?mode=ro&immutable=1", uri=True)
    readable.row_factory = sqlite3.Row
    context = sqlite3.connect("file:" + str(context_path) + "?mode=ro&immutable=1", uri=True)
    try:
        require(context.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite integrity")
        require(not context.execute("PRAGMA foreign_key_check").fetchall(), "SQLite FK")
        expected_schema = sqlite3.connect(":memory:")
        expected_schema.executescript(SCHEMA.read_text())
        structure = "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        require(context.execute(structure).fetchall() == expected_schema.execute(structure).fetchall(), "context schema/write-once")
        expected_schema.close()
        columns = (
            "d.id AS document_row_id,d.source_path AS document_source_path,d.pointer AS document_pointer,"
            "s.id AS title_slot_id,t.id AS title_text_id,t.raw_text AS title,"
            "t.source_path AS title_text_source_path,t.pointer AS title_text_pointer"
        )
        title_by_document = defaultdict(dict)
        for row in doc.execute(
            "SELECT " + columns + " FROM document_row d JOIN document_slot s ON s.row_id=d.id "
            "JOIN document_text t ON t.slot_id=s.id WHERE s.field='titleTextMapHash' "
            "AND s.locale='CHS' AND s.status IN ('resolved','ambiguous')"
        ):
            if not isinstance(row["title"], str) or not row["title"].strip():
                continue
            candidate = {
                "title": row["title"], "basis": "source-authored-document-title",
                "document_row_id": row["document_row_id"],
                "document_source_path": row["document_source_path"],
                "document_pointer": row["document_pointer"],
                "title_slot_id": row["title_slot_id"],
                "title_text_id": row["title_text_id"],
                "title_text_source_path": row["title_text_source_path"],
                "title_text_pointer": row["title_text_pointer"],
            }
            title_by_document[row["document_row_id"]][row["title_text_id"]] = candidate
        mapped = defaultdict(dict)
        for row in doc.execute(
            "SELECT d.id AS document_row_id,c.target_source_path,c.target_seal_json,"
            "r.status AS relation_status,r.search_complete "
            "FROM document_candidate c JOIN document_relation r ON r.id=c.relation_id "
            "JOIN document_row d ON d.id=r.row_id WHERE r.kind='localization_readable'"
        ):
            if row["relation_status"] != "resolved" or row["search_complete"] != 1:
                continue
            seal = json.loads(row["target_seal_json"])
            for candidate in title_by_document[row["document_row_id"]].values():
                mapped[row["target_source_path"]][(candidate["document_row_id"], candidate["title_text_id"])] = (candidate, seal)

        def title(candidates, kind, path):
            candidates = sorted(candidates, key=lambda item: (item["document_row_id"], item["title_text_id"]))
            state = "unresolved" if not candidates else "resolved" if len(candidates) == 1 else "ambiguous"
            display = candidates[0]["title"] if state == "resolved" else None
            basis = "source-authored-document-title" if state == "resolved" else None
            label = (f"《{display}》" if kind in ("book", "document") else f"「{display}」") if display else (
                f"正式标题未解析（{path}）" if state == "unresolved" else f"标题存在多个来源候选（{path}）"
            )
            return state, display, basis, enc(candidates), label

        expected = []
        states = {"document_archive": Counter(), "readable_archive": Counter()}
        for row in doc.execute("SELECT id,source_path FROM document_row ORDER BY id"):
            status, display, basis, candidates, label = title(list(title_by_document[row["id"]].values()), "document", row["source_path"])
            expected.append((ident(run, "document_archive", document_report["run_id"], row["id"]), SNAPSHOT,
                             document_report["run_id"], "document_archive", row["id"], row["source_path"],
                             "document", display, None, None, status, basis, candidates, label))
            states["document_archive"][status] += 1
        for row in readable.execute("SELECT id,source_path,source_sha256 FROM readable_file ORDER BY id"):
            candidates = []
            for candidate, seal in mapped.get(row["source_path"], {}).values():
                if seal.get("path") == row["source_path"] and seal.get("sha256") == row["source_sha256"]:
                    candidates.append(candidate)
            kind = "book" if Path(row["source_path"]).name.startswith("Book") else "readable"
            status, display, basis, candidate_json, label = title(candidates, kind, row["source_path"])
            expected.append((ident(run, "readable_archive", readable_report["run_id"], row["id"]), SNAPSHOT,
                             readable_report["run_id"], "readable_archive", row["id"], row["source_path"], kind,
                             display, None, None, status, basis, candidate_json, label))
            states["readable_archive"][status] += 1
        actual = [tuple(row) for row in context.execute("SELECT * FROM context_entry ORDER BY id")]
        require(actual == sorted(expected), "all context entries exact; no extras/omissions")
        digest = hashlib.sha256()
        for row in actual:
            digest.update((enc(list(row)) + "\n").encode())
        logical = {"context_entry": {"rows": len(actual), "sha256": digest.hexdigest()}}
        releases = context.execute("SELECT singleton,manifest_json FROM context_release").fetchall()
        require(len(releases) == 1 and releases[0][0] == 1, "single release")
        release = json.loads(releases[0][1])
        require(release == {key: value for key, value in context_report.items() if key != "artifact"}, "release/report binding")
        require(release["logical_seals"] == logical and release["states"] == {family: dict(counts) for family, counts in states.items()}, "logical/state seals")
        require(release["context_is_evidence_truth"] is False and release["product_accepted"] is False, "derived-only status")
        book_results = {}
        for book, expected_title in (("Book1119", "终北祷歌·上"), ("Book1121", "终北祷歌·下")):
            rows = context.execute("SELECT display_title,title_resolution_status,title_basis,candidate_titles_json FROM context_entry WHERE family='readable_archive' AND source_path=?",
                                   (f"Readable/CHS/{book}.txt",)).fetchall()
            require(len(rows) == 1 and rows[0][0] == expected_title and rows[0][1] == "resolved" and rows[0][2] == "source-authored-document-title", book + " title")
            require(len(json.loads(rows[0][3])) == 1, book + " exact source candidate")
            book_results[book] = expected_title
    finally:
        doc.close()
        readable.close()
        context.close()
    require(file_sha(context_path) == context_seal["sha256"], "context artifact drift")
    require(not any(Path(str(context_path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")), "post-verify sidecar")
    return {"status": "PASS", "artifact": context_seal, "run_id": run,
            "logical_seals": logical, "states": {family: dict(counts) for family, counts in states.items()},
            "books": book_results, "fabricated_display_title_count": 0,
            "wrong_cross_snapshot_identity_merge_count": 0}
