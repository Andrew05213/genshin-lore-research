"""Bounded 7.0 Quest/Talk read-through over exact accepted physical artifacts.

The physical native-ID candidate census can be complete even though the
accepted Quest→Talk *semantic* relation has search_complete=0. This adapter
never upgrades the latter, and never chooses one candidate from a set.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .research_query import ResearchUnavailable


class QuestTalk70Story:
    def __init__(self, research, release_scope_path: Path):
        self.research = research
        self.release_scope = json.loads(release_scope_path.read_text())
        if self.release_scope.get("contract") != "research-release-scope-v1":
            raise ResearchUnavailable("unknown release-scope contract")
        self.scopes = [
            scope for scope in self.release_scope["scopes"]
            if scope["release_version"] == "7.0"
            and scope["release_attribution_status"] == "officially_confirmed"
            and scope["source_snapshot"] == research.manifest["snapshot"]["id"]
        ]
        if len(self.scopes) != 2:
            raise ResearchUnavailable("7.0 official chapter scope is not closed")
        self.quest_key = next(
            key for key, value in research.components.items()
            if value["family"] == "quest_narrative_archive"
        )
        self.closure_key = next(
            key for key, value in research.components.items()
            if value["family"] == "talk_physical_archive"
        )
        self.v7_keys = sorted(
            key for key, value in research.components.items()
            if value["contract"] == "quest-talk-evidence-v7"
        )
        if len(self.v7_keys) != 2:
            raise ResearchUnavailable("accepted 7.0 Quest/Talk physical runs incomplete")
        self._census = None
        self._roots = None
        self._text_cache = {}
        self._research_containers = {}

    def _physical_census(self):
        if self._census is not None:
            return self._census
        roots = {}
        by_native = defaultdict(list)
        quest_roots = {}
        for key in self.v7_keys:
            db = self.research._source(key)
            rows = db.execute(
                "SELECT c.id,c.kind,c.native_id,c.ingest_run_id,"
                "f.relative_path,f.sha256,f.upstream_blob_oid "
                "FROM quest_talk_physical_container c "
                "JOIN source_record r ON r.id=c.source_record_id "
                "JOIN source_file f ON f.id=r.source_file_id "
                "WHERE c.kind IN ('talk_root','quest_root')"
            )
            for row in rows:
                info = {
                    "container_id": row["id"], "kind": row["kind"],
                    "native_id": row["native_id"], "run": row["ingest_run_id"],
                    "path": row["relative_path"], "sha256": row["sha256"],
                    "blob_oid": row["upstream_blob_oid"], "source_key": key,
                }
                if row["kind"] == "talk_root":
                    if not row["native_id"] or info["path"] in roots:
                        raise ResearchUnavailable("duplicate or unbound Talk physical root")
                    roots[info["path"]] = info
                    by_native[row["native_id"]].append(info)
                elif info["path"] in quest_roots:
                    raise ResearchUnavailable("duplicate Quest physical root path")
                else:
                    quest_roots[info["path"]] = info
        closure = self.research._source(self.closure_key)
        mapped = {}
        for row in closure.execute(
            "SELECT source_path,source_sha256,target_run,target_occurrence_id "
            "FROM talk_physical_file WHERE classification='mapped_program_reuse'"
        ):
            mapped[row["source_path"]] = (
                row["source_sha256"], row["target_run"], row["target_occurrence_id"]
            )
        if len(mapped) != len(roots) or set(mapped) != set(roots):
            raise ResearchUnavailable("Talk physical census has omissions or extras")
        for path, info in roots.items():
            if mapped[path] != (info["sha256"], info["run"], info["container_id"]):
                raise ResearchUnavailable("Talk physical closure binding drift")
        terminal = closure.execute(
            "SELECT source_path,raw_bytes FROM talk_physical_file "
            "WHERE classification='mapping_unresolved_terminal'"
        ).fetchall()
        if len(terminal) != 1 or terminal[0]["source_path"] != "BinOutput/Talk/NpcOther/12634.json":
            raise ResearchUnavailable("unexpected unmapped Talk namespace")
        # The sole unmapped file has no source-authored native Talk ID or text;
        # it remains terminal, not a candidate in a native-ID equality scan.
        if json.loads(terminal[0]["raw_bytes"]) != {"defaultVocalBoneName": 6317}:
            raise ResearchUnavailable("unmapped Talk source shape drift")
        self._roots = quest_roots
        self._census = by_native
        return by_native

    def _scope_references(self):
        by_native = self._physical_census()
        narrative = self.research._source(self.quest_key)
        for scope in self.scopes:
            for selector in scope["main_quest_selectors"]:
                candidates = selector["accepted_relation_candidates"]
                if len(candidates) != 1 or candidates[0]["status"] != "resolved_unique" or candidates[0]["search_complete"] != 1:
                    raise ResearchUnavailable("release MainQuest selector is not exact")
                target = candidates[0]
                relation = narrative.execute(
                    "SELECT r.kind,r.status,r.candidate_count,r.search_complete,"
                    "m.source_path,m.source_pointer,m.native_id,c.target_source_path,"
                    "c.target_source_pointer,c.target_run,c.target_occurrence_id "
                    "FROM quest_narrative_relation r "
                    "JOIN quest_narrative_container m ON m.id=r.container_id "
                    "JOIN quest_narrative_candidate c ON c.relation_id=r.id "
                    "WHERE r.id=?", (target["relation_id"],)
                ).fetchall()
                if len(relation) != 1 or (
                    relation[0]["kind"], relation[0]["status"], relation[0]["candidate_count"],
                    relation[0]["search_complete"], relation[0]["source_path"],
                    relation[0]["source_pointer"], relation[0]["native_id"],
                    relation[0]["target_source_path"], relation[0]["target_source_pointer"],
                    relation[0]["target_run"], relation[0]["target_occurrence_id"],
                ) != (
                    "mainquest_physical_root", "resolved_unique", 1, 1,
                    selector["main_quest_path"], selector["main_quest_pointer"],
                    selector["main_quest_id"], target["target_source_path"],
                    target["target_source_pointer"], target["target_run"],
                    target["target_occurrence_id"],
                ):
                    raise ResearchUnavailable("official MainQuest→Quest exact relation drift")
                found = narrative.execute(
                    "SELECT id,source_path,source_pointer,source_blob_oid "
                    "FROM quest_narrative_container WHERE id=? AND run_id=?",
                    (target["target_occurrence_id"], target["target_run"]),
                ).fetchone()
                if found is None or (found["source_path"], found["source_pointer"]) != (
                    target["target_source_path"], target["target_source_pointer"]
                ):
                    raise ResearchUnavailable("release Quest source coordinate drift")
                root = self._roots.get(found["source_path"])
                if root is None or root["blob_oid"] != found["source_blob_oid"]:
                    raise ResearchUnavailable("Quest physical root not independently bound")
                db = self.research._source(root["source_key"])
                for ref in db.execute(
                    "SELECT id,pointer,native_id,candidates_json,search_complete "
                    "FROM quest_talk_physical_reference WHERE container_id=? "
                    "ORDER BY pointer,id", (root["container_id"],)
                ):
                    native = ref["native_id"]
                    physical = sorted(by_native.get(native, ()), key=lambda x: (x["path"], x["container_id"]))
                    declared = json.loads(ref["candidates_json"])
                    expected = {(x["path"], x["container_id"], x["sha256"]) for x in physical}
                    observed = {(x["path"], x["container_id"], x["sha256"]) for x in declared}
                    if expected != observed or len(declared) != len(expected):
                        raise ResearchUnavailable("bounded Quest→Talk physical candidate drift")
                    if ref["search_complete"] != 0:
                        raise ResearchUnavailable("semantic relation unexpectedly changed")
                    yield {
                        "chapter_title": scope["official_title"],
                        "release_scope_ref": scope["web_source"],
                        "source_quest": root["path"],
                        "source_quest_id": root["native_id"],
                        "source_quest_run": root["run"],
                        "reference_id": ref["id"],
                        "reference_pointer": ref["pointer"],
                        "raw_talk_id": native,
                        "physical_candidates": physical,
                        "physical_candidate_scan_complete": True,
                        "semantic_relation_complete": False,
                    }

    def references(self):
        """Return all physical locator observations in the two official chapters."""
        return list(self._scope_references())

    def _direct_containers(self):
        """Quest-root/embedded text in the selected physical Quest files."""
        self._physical_census()
        for scope in self.scopes:
            for selector in scope["main_quest_selectors"]:
                target = selector["accepted_relation_candidates"][0]
                path = target["target_source_path"]
                root = self._roots[path]
                db = self.research._source(root["source_key"])
                rows = db.execute(
                    "SELECT c.id,c.kind,c.native_id,c.ingest_run_id,r.pointer,"
                    "f.relative_path,f.sha256,f.upstream_blob_oid "
                    "FROM quest_talk_physical_container c "
                    "JOIN source_record r ON r.id=c.source_record_id "
                    "JOIN source_file f ON f.id=r.source_file_id "
                    "WHERE f.relative_path=? AND c.kind IN ('quest_root','embedded_dialogue') "
                    "ORDER BY c.kind,r.pointer,c.id", (path,)
                ).fetchall()
                if not any(row["id"] == root["container_id"] and row["kind"] == "quest_root"
                           for row in rows):
                    raise ResearchUnavailable("bounded Quest direct container root drift")
                for row in rows:
                    if (row["relative_path"], row["sha256"], row["upstream_blob_oid"]) != (
                        root["path"], root["sha256"], root["blob_oid"]
                    ):
                        raise ResearchUnavailable("embedded Quest physical source drift")
                    candidate = {
                        "container_id": row["id"], "kind": row["kind"],
                        "native_id": row["native_id"], "run": row["ingest_run_id"],
                        "path": row["relative_path"], "sha256": row["sha256"],
                        "source_key": root["source_key"],
                    }
                    yield {
                        "chapter_title": scope["official_title"],
                        "release_scope_ref": scope["web_source"],
                        "source_quest": path,
                        "source_quest_id": root["native_id"],
                        "reference_id": None,
                        "reference_pointer": row["pointer"],
                        "raw_talk_id": None,
                        "physical_candidates": [candidate],
                        "physical_candidate_scan_complete": True,
                        "semantic_relation_complete": False,
                        "direct_embedded_or_quest_text": True,
                    }

    def _candidate_texts(self, candidate):
        cached = self._text_cache.get(candidate["container_id"])
        if cached is not None:
            return cached
        db = self.research._source(candidate["source_key"])
        source_rows = db.execute(
            "SELECT t.id,t.raw_text,n.pointer,n.ordinal "
            "FROM quest_talk_physical_text t "
            "INDEXED BY quest_talk_physical_text_container "
            "JOIN quest_talk_physical_node n ON n.id=t.node_id "
            "WHERE t.container_id=? AND t.locale='CHS' AND t.raw_text IS NOT NULL "
            "ORDER BY t.id", (candidate["container_id"],)
        ).fetchall()
        container = self.research.db.execute(
            "SELECT id,source_key,source_path,source_file_sha256 FROM research_container "
            "WHERE evidence_family='quest_talk_pilot' AND evidence_contract='quest-talk-evidence-v7' "
            "AND evidence_run=? AND occurrence_id=?",
            (candidate["run"], candidate["container_id"]),
        ).fetchone()
        if container is None or (container["source_key"], container["source_path"],
                                 container["source_file_sha256"]) != (
            candidate["source_key"], candidate["path"], candidate["sha256"]
        ):
            raise ResearchUnavailable("bounded Talk container index binding drift")
        self._research_containers[candidate["container_id"]] = self.research.db.execute(
            "SELECT * FROM research_container WHERE id=?", (container["id"],)
        ).fetchone()
        indexed = self.research.db.execute(
            "SELECT * FROM research_text INDEXED BY research_text_container "
            "WHERE container_id=? AND locale='CHS'",
            (container["id"],),
        ).fetchall()
        mapped = {row["pk_value"]: row for row in indexed}
        if len(mapped) != len(source_rows) or {row["id"] for row in source_rows} != set(mapped):
            raise ResearchUnavailable("bounded physical Talk text projection has omissions")
        result = []
        for source_row in source_rows:
            row = mapped[source_row["id"]]
            if (row["raw_text"], row["pointer"], row["ordinal"]) != (
                source_row["raw_text"], source_row["pointer"], source_row["ordinal"]
            ):
                raise ResearchUnavailable("bounded Talk text/physical coordinate drift")
            result.append(row)
        self._text_cache[candidate["container_id"]] = result
        return result

    def search(self, query: str, limit: int = 20, cursor: str | None = None):
        if not isinstance(query, str) or not query:
            raise ValueError("nonempty literal story query required")
        limit = max(1, min(int(limit), 100))
        scope = {"op": "bounded-story-7.0", "q": query, "limit": limit}
        after = self.research._after(cursor, scope)
        if not isinstance(after, int) or after < 0:
            raise ValueError("bounded story cursor mismatch")
        matches = []
        references = list(self._scope_references())
        for relation in references:
            for candidate in relation["physical_candidates"]:
                for row in self._candidate_texts(candidate):
                    if query not in row["raw_text"]:
                        continue
                    sort_key = (relation["source_quest"], relation["reference_pointer"],
                                candidate["path"], row["ordinal"] if row["ordinal"] is not None else -1,
                                row["pointer"], row["id"])
                    matches.append((sort_key, row, relation))
        direct_containers = list(self._direct_containers())
        for relation in direct_containers:
            candidate = relation["physical_candidates"][0]
            for row in self._candidate_texts(candidate):
                if query not in row["raw_text"]:
                    continue
                sort_key = (relation["source_quest"], relation["reference_pointer"],
                            candidate["path"], row["ordinal"] if row["ordinal"] is not None else -1,
                            row["pointer"], row["id"])
                matches.append((sort_key, row, relation))
        matches.sort(key=lambda x: x[0])
        page = []
        for _, row, relation in matches[after:after + limit]:
            self.research._recheck_text(row)
            container = self.research.db.execute(
                "SELECT * FROM research_container WHERE id=?", (row["container_id"],)
            ).fetchone()
            item = {
                        "evidence_ref": self.research._text_ref(row),
                        "container_ref": self.research._container_ref(container),
                        "family": row["evidence_family"],
                        "source_path": container["source_path"],
                        "physical_pointer": row["pointer"],
                        "text_source_path": row["text_source_path"],
                        "text_source_pointer": row["text_source_pointer"],
                        "snippet": row["raw_text"][:180],
                        "chapter_title": relation["chapter_title"],
                        "release_scope_ref": relation["release_scope_ref"],
                        "source_quest": relation["source_quest"],
                        "reference_id": relation["reference_id"],
                        "reference_pointer": relation["reference_pointer"],
                        "raw_talk_id": relation["raw_talk_id"],
                        "direct_embedded_or_quest_text": relation.get("direct_embedded_or_quest_text", False),
                        "candidate_count": len(relation["physical_candidates"]),
                        "physical_candidate_set": [
                            {
                                "container_ref": self.research._container_ref(
                                    self._research_containers[candidate["container_id"]]
                                ),
                                "source_path": candidate["path"],
                                "native_id": candidate["native_id"],
                            }
                            for candidate in relation["physical_candidates"]
                        ],
                        "claim_qualification": "not_computed; requires a specific claim and all-candidate exact recheck",
                        "physical_candidate_scan_complete": True,
                        "semantic_relation_complete": False,
                        "discovery_only": True,
                        "relation_closure": False,
            }
            item = self.research._decorate_context(item, row, container)
            if len(json.dumps({"items": page + [item]}, ensure_ascii=False, separators=(",", ":")).encode()) > 90_000:
                break
            page.append(item)
        if matches[after:after + limit] and not page:
            raise ResearchUnavailable("bounded story item exceeds Research payload budget")
        next_cursor = self.research._cursor(scope, after + len(page)) if after + len(page) < len(matches) else None
        return {
            "items": page, "delivered_count": len(page), "next_cursor": next_cursor,
            "bounded_scope": "officially attributed 7.0 chapter VII acts I–II Quest roots, embedded dialogue, and Quest→Talk physical candidates",
            "physical_candidate_scan_complete": True,
            "bounded_direct_container_count": len(direct_containers),
            "bounded_quest_to_talk_reference_count": len(references),
            "bounded_match_count": len(matches),
            "bounded_page_complete": next_cursor is None,
            "semantic_relation_complete": False,
            "outer_corpus_complete": False,
            "partial": True,
            "product_accepted": True,
            "story_adapter_authority": (
                "prepublication_candidate" if self.research.allow_prepublication_profile
                else "accepted_current_research_profile"
            ),
            "candidate_selection": "none",
            "ranking": "deterministic physical evidence order",
        }
