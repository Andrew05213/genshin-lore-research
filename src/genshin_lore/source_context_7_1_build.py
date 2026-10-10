"""Derived, source-authored 7.1 Document/Readable display context."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "schemas/source-context-7.1-v1.sql"
CODE = ("src/genshin_lore/source_context_7_1_build.py", "schemas/source-context-7.1-v1.sql")
CONTRACT = "research-source-context-v1"
SNAPSHOT = "anime-game-data:7.1:9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"


def canon(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def identity(*parts):
    return "source-context71:" + sha(canon(parts).encode())[:40]


def _candidate(row):
    return {
        "title": row["title"], "basis": "source-authored-document-title",
        "document_row_id": row["document_row_id"],
        "document_source_path": row["document_source_path"],
        "document_pointer": row["document_pointer"],
        "title_slot_id": row["title_slot_id"],
        "title_text_id": row["title_text_id"],
        "title_text_source_path": row["title_text_source_path"],
        "title_text_pointer": row["title_text_pointer"],
    }


def _title(candidates, kind, path):
    candidates = sorted(candidates, key=lambda item: (item["document_row_id"], item["title_text_id"]))
    status = "unresolved" if not candidates else "resolved" if len(candidates) == 1 else "ambiguous"
    display = candidates[0]["title"] if status == "resolved" else None
    basis = "source-authored-document-title" if status == "resolved" else None
    label = (f"《{display}》" if kind in ("book", "document") else f"「{display}」") if display else (
        f"正式标题未解析（{path}）" if status == "unresolved" else f"标题存在多个来源候选（{path}）"
    )
    return status, display, basis, canon(candidates), label


def build(document_artifact, document_report, readable_artifact, readable_report, output):
    document_artifact, readable_artifact, output = map(lambda item: Path(item).resolve(),
                                                       (document_artifact, readable_artifact, output))
    for artifact, report, family in ((document_artifact, document_report, "Document"),
                                     (readable_artifact, readable_report, "Readable")):
        if report["artifact"] != {"size_bytes": artifact.stat().st_size, "sha256": file_sha(artifact)}:
            raise ValueError(family + " artifact seal")
        if report["snapshot_id"] != SNAPSHOT:
            raise ValueError(family + " snapshot mismatch")
    if document_report["contract"] != "document-7.1-evidence-v1" or readable_report["contract"] != "readable-7.1-evidence-v1":
        raise ValueError("Document/Readable contract mismatch")
    if readable_report["document_dependency"] != document_report["artifact"]:
        raise ValueError("Readable/Document dependency mismatch")
    if output.exists() or output.is_symlink() or any(Path(str(output) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise FileExistsError("source context target/sidecar exists")
    implementation = {path: file_sha(ROOT / path) for path in CODE}
    run_id = identity("run", SNAPSHOT, implementation, document_report["run_id"], readable_report["run_id"])
    doc = sqlite3.connect("file:" + str(document_artifact) + "?mode=ro&immutable=1", uri=True)
    doc.row_factory = sqlite3.Row
    readable = sqlite3.connect("file:" + str(readable_artifact) + "?mode=ro&immutable=1", uri=True)
    readable.row_factory = sqlite3.Row
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, stage_name = tempfile.mkstemp(prefix=".context71-stage-", suffix=".sqlite", dir=output.parent)
    os.close(fd)
    stage = Path(stage_name)
    compact = Path(stage_name + ".compact")
    db = None
    try:
        db = sqlite3.connect(stage)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=OFF")
        db.executescript(SCHEMA.read_text())
        title_rows = list(doc.execute(
            "SELECT d.id AS document_row_id,d.source_path AS document_source_path,d.pointer AS document_pointer,"
            "s.id AS title_slot_id,t.id AS title_text_id,t.raw_text AS title,"
            "t.source_path AS title_text_source_path,t.pointer AS title_text_pointer "
            "FROM document_row d JOIN document_slot s ON s.row_id=d.id "
            "JOIN document_text t ON t.slot_id=s.id "
            "WHERE s.field='titleTextMapHash' AND s.locale='CHS' "
            "AND s.status IN ('resolved','ambiguous')"
        ))
        document_titles = defaultdict(list)
        for row in title_rows:
            if isinstance(row["title"], str) and row["title"].strip():
                document_titles[row["document_row_id"]].append(_candidate(row))
        by_readable = defaultdict(dict)
        for row in doc.execute(
            "SELECT d.id AS document_row_id,d.source_path AS document_source_path,d.pointer AS document_pointer,"
            "s.id AS title_slot_id,t.id AS title_text_id,t.raw_text AS title,"
            "t.source_path AS title_text_source_path,t.pointer AS title_text_pointer,"
            "c.target_source_path,c.target_seal_json,r.status AS relation_status,r.search_complete "
            "FROM document_candidate c JOIN document_relation r ON r.id=c.relation_id "
            "JOIN document_row d ON d.id=r.row_id JOIN document_slot s ON s.row_id=d.id "
            "JOIN document_text t ON t.slot_id=s.id "
            "WHERE r.kind='localization_readable' AND s.field='titleTextMapHash' "
            "AND s.locale='CHS' AND s.status IN ('resolved','ambiguous')"
        ):
            if row["relation_status"] != "resolved" or row["search_complete"] != 1:
                continue
            if not isinstance(row["title"], str) or not row["title"].strip():
                continue
            candidate = _candidate(row)
            by_readable[row["target_source_path"]][(candidate["document_row_id"], candidate["title_text_id"])] = (
                candidate, json.loads(row["target_seal_json"]),
            )
        states = {"readable_archive": Counter(), "document_archive": Counter()}
        for row in doc.execute("SELECT id,source_path FROM document_row ORDER BY id"):
            candidates = document_titles.get(row["id"], [])
            status, display, basis, candidate_json, label = _title(candidates, "document", row["source_path"])
            db.execute("INSERT INTO context_entry VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                identity(run_id, "document_archive", document_report["run_id"], row["id"]), SNAPSHOT,
                document_report["run_id"], "document_archive", row["id"], row["source_path"], "document",
                display, None, None, status, basis, candidate_json, label,
            ))
            states["document_archive"][status] += 1
        for row in readable.execute("SELECT id,source_path,source_sha256 FROM readable_file ORDER BY id"):
            candidates = []
            for candidate, seal in by_readable.get(row["source_path"], {}).values():
                if seal.get("path") == row["source_path"] and seal.get("sha256") == row["source_sha256"]:
                    candidates.append(candidate)
            kind = "book" if Path(row["source_path"]).name.startswith("Book") else "readable"
            status, display, basis, candidate_json, label = _title(candidates, kind, row["source_path"])
            db.execute("INSERT INTO context_entry VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                identity(run_id, "readable_archive", readable_report["run_id"], row["id"]), SNAPSHOT,
                readable_report["run_id"], "readable_archive", row["id"], row["source_path"], kind,
                display, None, None, status, basis, candidate_json, label,
            ))
            states["readable_archive"][status] += 1
        seals = {}
        for table in ("context_entry",):
            digest = hashlib.sha256()
            n = 0
            for row in db.execute("SELECT * FROM " + table + " ORDER BY id"):
                digest.update((canon(list(row)) + "\n").encode())
                n += 1
            seals[table] = {"rows": n, "sha256": digest.hexdigest()}
        release = {
            "contract": CONTRACT, "snapshot_id": SNAPSHOT, "run_id": run_id,
            "implementation": implementation,
            "document_dependency": document_report["artifact"], "document_run_id": document_report["run_id"],
            "readable_dependency": readable_report["artifact"], "readable_run_id": readable_report["run_id"],
            "states": {family: dict(counter) for family, counter in states.items()},
            "logical_seals": seals, "context_is_evidence_truth": False, "product_accepted": False,
        }
        db.execute("INSERT INTO context_release VALUES(1,?)", (canon(release),))
        db.commit()
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("source context SQLite integrity")
        db.execute("VACUUM INTO ?", (str(compact),))
        db.close()
        db = None
        if file_sha(document_artifact) != document_report["artifact"]["sha256"] or file_sha(readable_artifact) != readable_report["artifact"]["sha256"]:
            raise ValueError("source context dependency drift")
        if output.exists() or output.is_symlink():
            raise FileExistsError("source context output appeared")
        os.link(compact, output)
        compact.unlink()
        return {**release, "artifact": {"size_bytes": output.stat().st_size, "sha256": file_sha(output)}}
    finally:
        if db is not None:
            db.close()
        doc.close()
        readable.close()
        stage.unlink(missing_ok=True)
        compact.unlink(missing_ok=True)
