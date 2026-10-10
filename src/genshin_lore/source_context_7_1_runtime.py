"""Prepublication, exact-run Readable/Document title-context proof surface.

This is deliberately not registered in the accepted Research profile.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .loading_tips_source import array_records, pairs
from .source_context_7_1_build import SNAPSHOT


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _open(path):
    return sqlite3.connect("file:" + str(Path(path).resolve()) + "?mode=ro&immutable=1", uri=True)


class SourceContextPreview:
    def __init__(self, source, source_scope, document_artifact, document_report,
                 readable_artifact, readable_report, context_artifact, context_report):
        self.source = Path(source).resolve()
        self.scope = source_scope
        if source_scope["snapshot_id"] != SNAPSHOT:
            raise ValueError("prepublication source scope mismatch")
        self.document_source_seals = {item["path"]: item["sha256"]
                                      for item in source_scope["document_sources"]}
        self.document_report = document_report
        self.readable_report = readable_report
        self.context_report = context_report
        self.paths = {"document_archive": Path(document_artifact).resolve(),
                      "readable_archive": Path(readable_artifact).resolve()}
        self.context_path = Path(context_artifact).resolve()
        for path, report in ((self.paths["document_archive"], document_report),
                             (self.paths["readable_archive"], readable_report),
                             (self.context_path, context_report)):
            if report["artifact"] != {"size_bytes": path.stat().st_size, "sha256": _sha(path)}:
                raise ValueError("prepublication artifact identity mismatch")
            if report["snapshot_id"] != SNAPSHOT:
                raise ValueError("prepublication snapshot mismatch")
        self.db = {family: _open(path) for family, path in self.paths.items()}
        self.context = _open(self.context_path)
        for connection in (*self.db.values(), self.context):
            connection.row_factory = sqlite3.Row

    def close(self):
        for connection in (*self.db.values(), self.context):
            connection.close()

    def _context(self, family, anchor_id):
        row = self.context.execute(
            "SELECT * FROM context_entry WHERE family=? AND anchor_id=? AND source_run_id=?",
            (family, anchor_id, self._run(family)),
        ).fetchone()
        if row is None or row["snapshot_id"] != SNAPSHOT:
            raise ValueError("source context exact-run binding missing")
        return row

    def _run(self, family):
        return (self.document_report if family == "document_archive" else self.readable_report)["run_id"]

    def _source(self, relative_path):
        path = (self.source / relative_path).resolve()
        if not path.is_relative_to(self.source):
            raise ValueError("source path outside pinned checkout")
        return path

    @staticmethod
    def _heading(lines, ordinal):
        for preceding in reversed(lines[:ordinal + 1]):
            text = preceding["raw_text"].strip()
            if text.startswith("【") and text.endswith("】"):
                return text[1:-1]
        return None

    def _readable(self, text_id):
        row = self.db["readable_archive"].execute(
            "SELECT l.*,f.run_id,f.source_path,f.source_sha256 FROM readable_line l "
            "JOIN readable_file f ON f.id=l.file_id WHERE l.id=?", (text_id,),
        ).fetchone()
        if row is None or row["run_id"] != self._run("readable_archive"):
            raise ValueError("Readable exact-run evidence not found")
        source = self._source(row["source_path"])
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["source_sha256"]:
            raise ValueError("Readable source drift")
        if raw[row["byte_start"]:row["byte_end"]].decode("utf-8") != row["raw_text"] + row["terminator"]:
            raise ValueError("Readable physical line drift")
        lines = self.db["readable_archive"].execute(
            "SELECT ordinal,raw_text FROM readable_line WHERE file_id=? AND ordinal<=? ORDER BY ordinal",
            (row["file_id"], row["ordinal"]),
        ).fetchall()
        return row, self._heading(lines, len(lines) - 1)

    def _document(self, text_id):
        row = self.db["document_archive"].execute(
            "SELECT t.*,s.row_id,s.locale AS text_locale,d.run_id,d.source_path AS document_source_path,d.ordinal,d.raw_json "
            "FROM document_text t JOIN document_slot s ON s.id=t.slot_id "
            "JOIN document_row d ON d.id=s.row_id WHERE t.id=?", (text_id,),
        ).fetchone()
        if row is None or row["run_id"] != self._run("document_archive"):
            raise ValueError("Document exact-run evidence not found")
        for relative in (row["document_source_path"], row["source_path"]):
            if relative not in self.document_source_seals or _sha(self._source(relative)) != self.document_source_seals[relative]:
                raise ValueError("Document source/locator file drift")
        source_rows = array_records(self._source(row["document_source_path"]))
        exact = next((raw for ordinal, _, raw in source_rows if ordinal == row["ordinal"]), None)
        if exact != row["raw_json"]:
            raise ValueError("Document physical row drift")
        source_pairs = list(pairs(self._source(row["source_path"])))
        if row["pair_ordinal"] >= len(source_pairs) or source_pairs[row["pair_ordinal"]] != (row["pointer"].lstrip("/"), row["raw_text"]):
            raise ValueError("Document TextMap physical pair drift")
        return row, None

    def read(self, evidence_ref):
        if evidence_ref.get("snapshot_id") != SNAPSHOT:
            raise ValueError("cross-snapshot evidence rejected")
        family = evidence_ref.get("family")
        if family not in self.db or evidence_ref.get("run_id") != self._run(family):
            raise ValueError("cross-run evidence rejected")
        text_id = evidence_ref.get("text_id")
        row, heading = (self._readable(text_id) if family == "readable_archive" else self._document(text_id))
        anchor = row["file_id"] if family == "readable_archive" else row["row_id"]
        context = self._context(family, anchor)
        source_path = row["source_path"] if family == "readable_archive" else row["document_source_path"]
        locale = (source_path.split("/", 2)[1] if family == "readable_archive" else row["text_locale"])
        if evidence_ref.get("source_path") != source_path or evidence_ref.get("pointer") != row["pointer"]:
            raise ValueError("evidence physical coordinate mismatch")
        label = context["citation_label_base"]
        if heading:
            label += "—「" + heading + "」"
        return {
            "evidence_ref": evidence_ref, "raw_text": row["raw_text"], "locale": locale,
            "text_provenance": {"source_path": row["source_path"], "pointer": row["pointer"],
                                "pair_ordinal": row["pair_ordinal"] if family == "document_archive" else None},
            "source_context": {"display_title": context["display_title"],
                               "display_subtitle": context["display_subtitle"],
                               "chapter_title": heading or context["chapter_title"],
                               "source_kind": context["source_kind"],
                               "title_resolution_status": context["title_resolution_status"],
                               "title_basis": context["title_basis"],
                               "candidate_titles": json.loads(context["candidate_titles_json"])},
            "citation_label": label,
            "provenance": {"snapshot_id": SNAPSHOT, "run_id": self._run(family),
                           "source_path": source_path, "pointer": row["pointer"]},
        }

    def inspect(self, evidence_ref):
        return self.read(evidence_ref)

    def search(self, query, family="readable_archive", limit=10):
        if not query or family not in self.db or not 1 <= limit <= 100:
            raise ValueError("bounded prepublication search arguments required")
        if family == "readable_archive":
            rows = self.db[family].execute(
                "SELECT l.id,l.pointer,f.source_path FROM readable_line l JOIN readable_file f ON f.id=l.file_id "
                "WHERE instr(l.raw_text,?)>0 ORDER BY f.source_path,l.ordinal,l.id LIMIT ?", (query, limit),
            ).fetchall()
        else:
            rows = self.db[family].execute(
                "SELECT t.id,t.pointer,d.source_path FROM document_text t "
                "JOIN document_slot s ON s.id=t.slot_id JOIN document_row d ON d.id=s.row_id "
                "WHERE instr(t.raw_text,?)>0 ORDER BY d.source_path,d.ordinal,t.id LIMIT ?", (query, limit),
            ).fetchall()
        return [self.read({"snapshot_id": SNAPSHOT, "family": family, "run_id": self._run(family),
                           "text_id": row["id"], "source_path": row["source_path"], "pointer": row["pointer"]})
                for row in rows]
