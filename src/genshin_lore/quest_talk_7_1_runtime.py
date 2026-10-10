"""Explicit-run read-only access to a verified 7.1 standalone candidate."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from .quest_talk_7_1_verify import CONTRACT, SNAPSHOT


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class QuestTalk71Candidate:
    def __init__(self, artifact: Path, report: dict, verification_report: dict, expected_run: str):
        artifact = Path(artifact).resolve()
        if not isinstance(expected_run, str) or not expected_run:
            raise ValueError("explicit exact ingest run required")
        if any(Path(str(artifact) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise ValueError("candidate artifact sidecar")
        if report.get("contract") != CONTRACT or report.get("snapshot_id") != SNAPSHOT or report.get("ingest_run_id") != expected_run:
            raise ValueError("candidate report or run mismatch")
        if report.get("artifact") != {"size_bytes": artifact.stat().st_size, "sha256": _sha(artifact)}:
            raise ValueError("candidate artifact identity mismatch")
        if verification_report.get("status") != "PASS" or verification_report.get("artifact_sha256") != report["artifact"]["sha256"] or verification_report.get("ingest_run_id") != expected_run:
            raise ValueError("independent verification is required for candidate access")
        self.conn = sqlite3.connect("file:" + str(artifact) + "?mode=ro&immutable=1", uri=True)
        self.conn.row_factory = sqlite3.Row
        release = self.conn.execute("SELECT contract,snapshot_id,ingest_run_id FROM release").fetchall()
        if len(release) != 1 or tuple(release[0]) != (CONTRACT, SNAPSHOT, expected_run):
            self.conn.close()
            raise ValueError("artifact is not the requested 7.1 exact run")
        self.run = expected_run

    def close(self) -> None:
        self.conn.close()

    def read_node(self, *, ingest_run_id: str, node_id: str, before: int = 0, after: int = 0) -> dict:
        if ingest_run_id != self.run:
            raise ValueError("exact run mismatch; no sibling fallback")
        if type(before) is not int or type(after) is not int or not 0 <= before <= 20 or not 0 <= after <= 20:
            raise ValueError("physical context bound")
        node = self.conn.execute("SELECT * FROM talk_node WHERE id=? AND ingest_run_id=?", (node_id, self.run)).fetchone()
        if node is None:
            return {"status": "NotFound", "ingest_run_id": self.run}
        container = self.conn.execute("SELECT source_path,pointer,kind FROM physical_container WHERE id=? AND ingest_run_id=?", (node["container_id"], self.run)).fetchone()
        if container is None:
            raise ValueError("node physical container missing")
        previous = self.conn.execute(
            "SELECT id,ordinal,pointer FROM talk_node WHERE container_id=? AND ingest_run_id=? AND ordinal<? ORDER BY ordinal DESC LIMIT ?",
            (node["container_id"], self.run, node["ordinal"], before),
        ).fetchall()
        following = self.conn.execute(
            "SELECT id,ordinal,pointer FROM talk_node WHERE container_id=? AND ingest_run_id=? AND ordinal>? ORDER BY ordinal ASC LIMIT ?",
            (node["container_id"], self.run, node["ordinal"], after),
        ).fetchall()
        context = [dict(row) for row in reversed(previous)] + [
            {"id": node["id"], "ordinal": node["ordinal"], "pointer": node["pointer"]},
        ] + [dict(row) for row in following]
        return {
            "status": "ok", "ingest_run_id": self.run, "snapshot_id": SNAPSHOT,
            "source_path": container["source_path"], "container_pointer": container["pointer"],
            "container_kind": container["kind"], "node": dict(node), "context": context,
            "context_scope": "single_physical_container",
        }
