"""Read-only, release-scoped Quest/Talk discovery over the 7.1 standalone run.

No accepted 7.1 Research corpus, relation closure, or cross-snapshot identity is
implied.  Ambiguous Quest→Talk references are exposed, never auto-selected.
"""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from pathlib import Path

from .quest_talk_7_1_build import at, canonical
from .quest_talk_7_1_runtime import QuestTalk71Candidate


def _encode(data):
    body = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    seal = hashlib.sha256(b"qt71-bounded-ref-v1\0" + body).digest()[:16]
    return "qt71-bounded:" + base64.urlsafe_b64encode(body + seal).decode().rstrip("=")


def _decode(value):
    if not isinstance(value, str) or not value.startswith("qt71-bounded:"):
        raise ValueError("not a 7.1 bounded evidence reference")
    raw = value.split(":", 1)[1]
    data = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    body, seal = data[:-16], data[-16:]
    if hashlib.sha256(b"qt71-bounded-ref-v1\0" + body).digest()[:16] != seal:
        raise ValueError("bounded evidence reference seal mismatch")
    return json.loads(body)


class QuestTalk71Story:
    def __init__(self, artifact: Path, build_report: dict, verification_report: dict,
                 candidate_manifest: dict, release_scope: dict, source_root: Path):
        if candidate_manifest.get("status") != "accepted_standalone_candidate" or candidate_manifest.get("product_accepted") is not False:
            raise ValueError("7.1 standalone candidate authority required")
        if candidate_manifest.get("artifact") != build_report.get("artifact"):
            raise ValueError("7.1 standalone artifact binding mismatch")
        self.base = QuestTalk71Candidate(artifact, build_report, verification_report,
                                         candidate_manifest["ingest_run_id"])
        self.db = self.base.conn
        self.run = self.base.run
        self.snapshot = candidate_manifest["source_snapshot"]
        self.source_root = Path(source_root)
        if release_scope.get("contract") != "research-release-scope-v1" or \
                release_scope.get("status") != "prepublication_candidate" or \
                release_scope.get("source_artifacts", {}).get("7.1") != candidate_manifest["artifact"]:
            self.close()
            raise ValueError("release scope or 7.1 artifact binding mismatch")
        self.scopes = [x for x in release_scope["scopes"] if x["release_version"] == "7.1"]
        if len(self.scopes) != 2 or any(x["source_snapshot"] != self.snapshot for x in self.scopes):
            self.close()
            raise ValueError("7.1 release-scoped chapter selectors missing")
        source_identity = release_scope["source_identity"]["7.1"]
        observed = subprocess.check_output(
            ["git", "-C", str(self.source_root), "rev-parse", "HEAD", "HEAD^{tree}"], text=True
        ).splitlines()
        if observed != [source_identity["commit"], source_identity["tree"]] or \
                subprocess.check_output(["git", "-C", str(self.source_root), "status", "--porcelain=v1"], text=True).strip():
            self.close()
            raise ValueError("7.1 source checkout pin drift")
        for name, expected in source_identity["file_sha256"].items():
            with (self.source_root / name).open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    self.close()
                    raise ValueError("7.1 release selector source file drift")
        shard_rows = list(self.db.execute("SELECT path,sha256 FROM textmap_shard WHERE locale='CHS'"))
        self.maps = {}
        for shard in shard_rows:
            path = self.source_root / shard["path"]
            with path.open("rb") as stream:
                raw = stream.read()
            if hashlib.sha256(raw).hexdigest() != shard["sha256"]:
                self.close()
                raise ValueError("7.1 snapshot-local TextMap drift")
            self.maps[shard["path"]] = json.loads(raw)
        if len(self.maps) != 2:
            self.close()
            raise ValueError("7.1 CHS TextMap scope incomplete")

    def close(self):
        self.base.close()

    def _containers(self, chapter_titles=None):
        requested = set(chapter_titles or (x["official_title"] for x in self.scopes))
        if requested - {x["official_title"] for x in self.scopes}:
            raise ValueError("unknown official 7.1 chapter title")
        roots = {r["container_id"] for scope in self.scopes if scope["official_title"] in requested
                 for selector in scope["main_quest_selectors"]
                 for r in selector["quest_root_candidates"]}
        selected = set(roots)
        unresolved = []
        for root in sorted(roots):
            for ref in self.db.execute("SELECT * FROM physical_reference WHERE container_id=? ORDER BY pointer,id", (root,)):
                candidates = json.loads(ref["candidates_json"])
                if ref["status"] == "resolved_unique" and len(candidates) == 1:
                    selected.add(candidates[0]["container_id"])
                else:
                    unresolved.append({"reference_id": ref["id"], "status": ref["status"],
                                       "source_pointer": ref["pointer"], "candidate_count": len(candidates)})
        # Embedded dialogue is physically inside the selected Quest source file.
        for root in sorted(roots):
            row = self.db.execute("SELECT source_path FROM physical_container WHERE id=? AND kind='quest_root'", (root,)).fetchone()
            if row is None:
                raise ValueError("release selector lost physical Quest root")
            selected.update(x[0] for x in self.db.execute(
                "SELECT id FROM physical_container WHERE source_path=? AND kind='embedded_dialogue'", (row[0],)))
        return sorted(selected), unresolved

    def _recheck(self, slot):
        node = self.db.execute("SELECT * FROM talk_node WHERE id=? AND ingest_run_id=?", (slot["node_id"], self.run)).fetchone()
        if node is None:
            raise ValueError("7.1 text slot orphan")
        container = self.db.execute("SELECT * FROM physical_container WHERE id=? AND ingest_run_id=?", (node["container_id"], self.run)).fetchone()
        if container is None:
            raise ValueError("7.1 physical container orphan")
        source = self.db.execute("SELECT * FROM source_file WHERE path=? AND snapshot_id=?", (container["source_path"], self.snapshot)).fetchone()
        if source is None or hashlib.sha256(source["raw_bytes"]).hexdigest() != source["sha256"]:
            raise ValueError("7.1 exact physical source file drift")
        with (self.source_root / container["source_path"]).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != source["sha256"]:
                raise ValueError("7.1 fixed source checkout and artifact bytes disagree")
        exact = at(json.loads(source["raw_bytes"]), node["pointer"])
        if canonical(exact) != node["raw_json"]:
            raise ValueError("7.1 physical Talk node drift")
        locators = json.loads(slot["locators_json"])
        if slot["status"] not in ("resolved_main", "resolved_medium") or not locators:
            raise ValueError("7.1 slot not exact resolved text")
        if not any(str(node["text_hash"]) == x["pointer"].lstrip("/") and
                   self.maps[x["path"]].get(x["pointer"].lstrip("/")) == slot["raw_text"] == x["raw_text"]
                   for x in locators if x["path"] in self.maps):
            raise ValueError("7.1 snapshot-local text resolution drift")
        return node, container, source

    def search(self, query, *, chapter_titles=None, limit=20, offset=0):
        if not isinstance(query, str) or not query or type(limit) is not int or not 1 <= limit <= 100 or \
                type(offset) is not int or offset < 0:
            raise ValueError("bounded story search arguments")
        containers, unresolved = self._containers(chapter_titles)
        if not containers:
            raise ValueError("release chapter has no bounded physical containers")
        rows = self.db.execute(
            "SELECT s.* FROM text_slot s JOIN talk_node n ON n.id=s.node_id "
            "WHERE s.ingest_run_id=? AND s.locale='CHS' AND s.status IN ('resolved_main','resolved_medium') "
            f"AND n.container_id IN ({','.join('?' for _ in containers)}) AND instr(s.raw_text,?)>0 "
            "ORDER BY n.container_id,n.ordinal,s.id LIMIT ? OFFSET ?",
            (self.run, *containers, query, limit + 1, offset),
        ).fetchall()
        items = []
        for slot in rows[:limit]:
            node, container, source = self._recheck(slot)
            ref = _encode({"v": 1, "snapshot": self.snapshot, "run": self.run,
                           "container": container["id"], "node": node["id"], "slot": slot["id"]})
            items.append({"evidence_ref": ref, "raw_text": slot["raw_text"],
                          "source_path": container["source_path"], "source_pointer": node["pointer"],
                          "source_file_sha256": source["sha256"], "physical_container": container["id"],
                          "node_ordinal": node["ordinal"], "text_status": slot["status"],
                          "retrieval_mode": "exact_bounded_literal", "discovery_only": True})
        return {"items": items, "next_offset": offset + len(items) if len(rows) > limit else None,
                "coverage": "7.1 Quest/Talk only; selected official chapters and resolved physical references",
                "partial": True, "product_accepted": False, "source_snapshot": self.snapshot,
                "release_attribution_basis": "external official notice; separate from game evidence",
                "unresolved_reference_count": len(unresolved),
                "unresolved_reference_statuses": {status: sum(x["status"] == status for x in unresolved)
                                                  for status in sorted({x["status"] for x in unresolved})},
                "line_level_first_appearance": "unknown"}

    def read(self, evidence_ref, *, before=0, after=0):
        parts = _decode(evidence_ref)
        if parts.get("v") != 1 or parts.get("snapshot") != self.snapshot or parts.get("run") != self.run:
            raise ValueError("bounded evidence snapshot or exact run mismatch")
        slot = self.db.execute("SELECT * FROM text_slot WHERE id=? AND node_id=? AND ingest_run_id=?",
                               (parts.get("slot"), parts.get("node"), self.run)).fetchone()
        if slot is None:
            raise ValueError("bounded evidence slot absent")
        node, container, source = self._recheck(slot)
        if container["id"] != parts.get("container"):
            raise ValueError("bounded physical container mismatch")
        allowed, _ = self._containers()
        if container["id"] not in allowed:
            raise ValueError("bounded reference is outside official 7.1 chapter scope")
        physical = self.base.read_node(ingest_run_id=self.run, node_id=node["id"], before=before, after=after)
        return {"evidence_ref": evidence_ref, "raw_text": slot["raw_text"],
                "provenance": {"snapshot": self.snapshot, "parser": "quest-talk-7.1-standalone-physical-v1",
                               "run": self.run, "source_path": container["source_path"],
                               "source_file_sha256": source["sha256"], "pointer": node["pointer"],
                               "container_id": container["id"], "node_ordinal": node["ordinal"]},
                "physical_context": physical["context"], "context_scope": "single_physical_container",
                "coverage": "7.1 Quest/Talk only", "partial": True, "product_accepted": False,
                "line_level_first_appearance": "unknown"}
