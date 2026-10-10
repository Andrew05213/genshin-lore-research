"""Read-only 7.1 capability router; not a Research corpus or product runtime."""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

from .character_7_1_bounded import Character71Bounded
from .narrative_7_1_bounded import Narrative71
from .quest_derived_7_1_bounded import QuestDerived71
from .source_context_7_1_runtime import SourceContextPreview
from .event_native_text_7_1_runtime import EventNativeText71Runtime
from .native_message_7_1_runtime import NativeMessage71Runtime
from .event_legacy_7_1_runtime import EventLegacy71Provider
from .event_successor_7_1_runtime import EventSuccessor71Provider
from .event_filmfest_7_1_runtime import EventFilmfest71Provider
from .event_tps_7_1_runtime import EventTPS71Provider
from .event_photo_echo_7_1_runtime import EventPhotoEcho71Provider
from .native_book_music_7_1_compat import NativeBookMusic71Provider
from .native_aux_quest_7_1_compat import NativeAuxQuest71Provider
from .native_archive_retrieval import NativeArchiveProvider
from .mail_7_1_runtime import Mail71Runtime

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = "anime-game-data:7.1:9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
REGISTRY = ROOT / "manifests/research-7.1/7.1-capability-registry-v1.json"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def token(value):
    return base64.urlsafe_b64encode(canonical(value).encode()).decode().rstrip("=")


def untoken(value):
    try:
        obj = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid bounded token") from exc
    if not isinstance(obj, dict) or token(obj) != value:
        raise ValueError("noncanonical bounded token")
    return obj


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def physical_pointer(root, source_path, pointer):
    path = (root / source_path).resolve()
    if not path.is_relative_to(root) or not pointer.startswith("/"):
        raise ValueError("source coordinate outside pinned snapshot")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    for part in pointer[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


class Research71Bounded:
    def __init__(self, source, registry=REGISTRY):
        self.source = Path(source).resolve()
        identity = subprocess.check_output(["git", "-C", str(self.source), "rev-parse", "HEAD", "HEAD^{tree}"], text=True).splitlines()
        if identity != ["9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99",
                        "aa578c08c402af39b41f1ffc95124d10f5410272"]:
            raise ValueError("exact 7.1 source commit/tree required")
        if subprocess.check_output(["git", "-C", str(self.source), "status", "--porcelain=v1"], text=True).strip():
            raise ValueError("7.1 source checkout not clean")
        self.registry = json.loads(Path(registry).read_text(encoding="utf-8"))
        if (self.registry.get("contract") != "research-7.1-bounded-capabilities-v1"
                or self.registry.get("snapshot") != SNAPSHOT
                or self.registry.get("product_accepted") is not False
                or self.registry.get("research_corpus_accepted") is not False):
            raise ValueError("bounded registry authority")
        self.entries = {x["capability_id"]: x for x in self.registry["capabilities"]}
        if len(self.entries) != len(self.registry["capabilities"]):
            raise ValueError("duplicate capability")
        if any(x.get("snapshot") != SNAPSHOT or not x.get("run") or not x.get("parser")
               or not set(x.get("supported_operations", [])).issubset({"search", "read", "inspect", "follow"})
               for x in self.entries.values()):
            raise ValueError("invalid capability snapshot/run/operations")
        self.adapters = {}
        self.checked = {}

    def close(self):
        for adapter in self.adapters.values():
            if hasattr(adapter, "close"):
                adapter.close()
            elif hasattr(adapter, "conn"):
                adapter.conn.close()
        self.adapters.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def describe(self):
        return {**self.registry, "partial": True}

    def entry(self, capability_id, operation):
        entry = self.entries.get(capability_id)
        if entry is None or operation not in entry["supported_operations"]:
            raise ValueError("unsupported capability/operation")
        return entry

    def adapter(self, entry):
        route = entry["route"]
        cache_key = entry["capability_id"] if route == "physical_sqlite" else route
        if cache_key in self.adapters:
            return self.adapters[cache_key]
        for artifact in entry["artifacts"]:
            path = Path(artifact["path"])
            if (not path.is_file() or path.stat().st_size != artifact["size_bytes"]
                    or any(Path(str(path) + ext).exists() for ext in ("-wal", "-shm", "-journal"))):
                raise ValueError("artifact missing/drift/sidecar: " + str(path))
            prior_seal = self.checked.get(str(path))
            if prior_seal is not None and prior_seal != artifact["sha256"]:
                raise ValueError("conflicting artifact seals: " + str(path))
            if prior_seal is None:
                if sha(path) != artifact["sha256"]:
                    raise ValueError("artifact SHA drift: " + str(path))
                self.checked[str(path)] = artifact["sha256"]
        if route == "character":
            adapter = Character71Bounded(ROOT / "manifests/character-7.1-successor/runtime.json", self.source)
        elif route == "narrative":
            base = ROOT / "reports/narrative-7.1-trio"
            bindings = {name: {"artifact": f"/tmp/genshin-lore-narrative71-{name}-v4-a.sqlite",
                               "report": str(base / f"{name}-build-a.json")}
                        for name in ("npc", "dialog", "reminder")}
            adapter = Narrative71(self.source, bindings)
        elif route == "quest":
            adapter = QuestDerived71(source=self.source,
                manifest=ROOT / "manifests/quest-derived-7.1/standalone-candidates-v1.json",
                narrative=Path("/tmp/quest-narrative-7.1-final-a.sqlite"),
                review=Path("/tmp/quest-review-7.1-final-a.sqlite"),
                review_binding=Path("/tmp/quest-review-7.1-binding-v2-a.sqlite"),
                codex=Path("/tmp/codexquest-7.1-v3-a.sqlite"),
                quest_talk=Path("/tmp/genshin-lore-qt-7.1-final-b.sqlite"))
        elif route == "source_context":
            scope = json.loads((ROOT / "manifests/document-readable-7.1/source-scope.json").read_text())
            def report(name):
                return json.loads(Path(f"/tmp/genshin-lore-{name}-7.1-a-build.json").read_text())
            adapter = SourceContextPreview(self.source, scope,
                Path("/tmp/genshin-lore-document-7.1-a.sqlite"), report("document"),
                Path("/tmp/genshin-lore-readable-7.1-a.sqlite"), report("readable"),
                Path("/tmp/genshin-lore-source-context-7.1-a.sqlite"), report("source-context"))
        elif route == "event17":
            adapter = EventNativeText71Runtime(self.source, Path(entry["artifacts"][0]["path"]),
                ROOT / "manifests/event-narrative-7.1/native-text-wave-runtime-v1.json")
        elif route == "native_message":
            adapter = NativeMessage71Runtime(self.source, Path(entry["artifacts"][0]["path"]),
                ROOT / "manifests/native-archive-7.1/message-runtime-v1.json")
        elif route == "physical_sqlite":
            path = Path(entry["artifacts"][0]["path"])
            adapter = sqlite3.connect(path.absolute().as_uri() + "?mode=ro&immutable=1", uri=True)
            adapter.row_factory = sqlite3.Row
            release = json.loads(adapter.execute("SELECT * FROM release WHERE id=1").fetchone()[1])
            if (release.get("snapshot") != SNAPSHOT or release.get("parser_build_id") != entry["parser"]
                    or release.get("ingest_run_id") != entry["run"]):
                adapter.close()
                raise ValueError("physical release identity drift")
        elif route == "loading_tips_71":
            path = Path(entry["artifacts"][0]["path"])
            adapter = sqlite3.connect(path.absolute().as_uri() + "?mode=ro&immutable=1", uri=True)
            adapter.row_factory = sqlite3.Row
            release = json.loads(adapter.execute("SELECT manifest_json FROM loading_tip_release LIMIT 1").fetchone()[0])
            if (release.get("snapshot_id") != SNAPSHOT or release.get("parser_build_id") != entry["parser"]
                    or release.get("run_id") != entry["run"]):
                adapter.close()
                raise ValueError("LoadingTips release identity drift")
        elif route == "native_bulk_71":
            adapter = NativeArchiveProvider(entry["artifacts"][0]["path"],
                ROOT / "manifests/native-archive-7.1/bulk-runtime.json")
        elif route == "mail_71":
            adapter = Mail71Runtime(Path(entry["artifacts"][0]["path"]),
                ROOT / "manifests/mail-archive-7.1/runtime-v1.json", self.source)
        elif route.startswith("event_"):
            component = int(route.split("_", 1)[1])
            providers = {10: EventLegacy71Provider, 11: EventSuccessor71Provider,
                         12: EventFilmfest71Provider, 14: EventTPS71Provider,
                         15: EventPhotoEcho71Provider}
            adapter = providers[component](entry["artifacts"][0]["path"],
                ROOT / f"manifests/event-narrative-7.1/component-{component}-runtime-v1.json")
        elif route.startswith("native_archive_"):
            family = route.removeprefix("native_archive_")
            providers = {"book_music": NativeBookMusic71Provider,
                         "aux_quest": NativeAuxQuest71Provider}
            adapter = providers[family](entry["artifacts"][0]["path"],
                ROOT / f"manifests/native-archive-7.1/{family.replace('_', '-')}-runtime-v1.json")
        else:
            raise ValueError("unimplemented route")
        self.adapters[cache_key] = adapter
        return adapter

    def ref(self, entry, local):
        return "research71:" + token({"snapshot": SNAPSHOT, "capability_id": entry["capability_id"],
                                     "run": entry["run"], "local": local})

    def unref(self, evidence_ref, operation):
        if not isinstance(evidence_ref, str) or not evidence_ref.startswith("research71:"):
            raise ValueError("foreign evidence_ref")
        value = untoken(evidence_ref.split(":", 1)[1])
        entry = self.entry(value.get("capability_id"), operation)
        if value.get("snapshot") != SNAPSHOT or value.get("run") != entry["run"]:
            raise ValueError("cross-snapshot/run evidence_ref")
        return entry, value["local"]

    def envelope(self, entry, local, payload):
        context = payload.get("source_context") or self._derived_context(entry, payload) or {
            "display_title": None, "display_subtitle": None, "chapter_title": None,
            "source_kind": entry["family"], "title_resolution_status": "unresolved",
            "title_basis": None, "candidate_titles": []}
        if "title_resolution_status" not in context and "resolution_status" in context:
            context = {**context, "title_resolution_status": context["resolution_status"]}
        physical = payload.get("container") or payload.get("evidence") or payload.get("provenance")
        if physical is None:
            physical = payload if entry["route"] in ("quest", "source_context", "physical_sqlite") else {}
        path = physical.get("source_path") or physical.get("quest_source_path")
        pointer = physical.get("pointer") or physical.get("source_pointer")
        if pointer is None and path and entry["selector"] in ("codexquest_archive", "quest_talk_physical"):
            pointer = "/"  # The exact root is the whole pinned physical file.
        label = payload.get("citation_label") or context.get("citation_label")
        if label is None and path and pointer:
            label = f"{path} {pointer}（物理来源；标题未解析）"
        return {"evidence_ref": self.ref(entry, local), "snapshot": SNAPSHOT,
                "capability_id": entry["capability_id"], "family": entry["family"],
                "contract": entry["contract"], "parser": entry["parser"], "run": entry["run"],
                "artifacts": entry["artifacts"], "capability_status": entry["status"],
                "limitations": entry["limitations"], "source_context": context,
                "citation_label": label, "source_path": path, "pointer": pointer,
                "product_accepted": False, "research_corpus_accepted": False, "partial": True,
                "data": payload}

    @staticmethod
    def _derived_context(entry, payload):
        if entry["route"] != "quest":
            return None
        candidates = [x for x in payload.get("text_candidates", [])
                      if x.get("owner_field") == "titleTextMapHash" and x.get("locale") == "CHS"
                      and x.get("raw_text")]
        if len(candidates) != 1 or candidates[0].get("status") != "resolved_unique":
            return None
        title = candidates[0]["raw_text"]
        return {"display_title": title, "display_subtitle": None, "chapter_title": None,
                "source_kind": entry["family"], "title_resolution_status": "resolved",
                "title_basis": "source-authored titleTextMapHash; exact 7.1 TextMap candidate",
                "candidate_titles": [title], "citation_label": f"「{title}」—任务"}

    def search(self, query, capability_id, *, limit=10, cursor=None):
        entry = self.entry(capability_id, "search")
        if not isinstance(query, str) or not query or type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("literal query and limit 1..20 required")
        after = None
        if cursor is not None:
            parsed = untoken(cursor)
            if any(parsed.get(k) != v for k, v in {
                "capability_id": capability_id, "run": entry["run"], "query": query, "limit": limit}.items()):
                raise ValueError("cursor scope mismatch")
            after = parsed["after"]
        adapter = self.adapter(entry)
        pairs = []
        next_after = None
        if entry["route"] == "character":
            page = adapter.search(query, kind=entry["selector"], limit=limit, offset=after or 0)
            pairs = [({"kind": x["kind"], "row_id": x["provenance"]["evidence_ref"]}, x)
                     for x in page["results"]]
            next_after = page["next_offset"]
        elif entry["route"] == "narrative":
            page = adapter.search(entry["selector"], query, limit=limit, cursor=after)
            pairs = [(x["evidence_ref"], x) for x in page["results"]]
            next_after = page["next_cursor"]
        elif entry["route"] == "quest":
            page = adapter.search(family=entry["selector"], run=entry["run"],
                                  literal=query, limit=limit, cursor=after)
            pairs = []
            for hit in page["results"]:
                exact = adapter.read(hit["evidence_ref"], child_limit=0)
                pairs.append((hit["evidence_ref"], {**hit, "text_candidates": exact.get("text_candidates", []),
                                                     "container": exact["container"]}))
            next_after = page["next_cursor"]
        elif entry["route"] == "source_context":
            family = entry["selector"]
            offset = after or 0
            if family == "readable_archive":
                # Two independently sourced match streams: line bytes and exact
                # source-authored titles. Merge physical line identities before
                # pagination; a line hit must not hide a matching book title.
                need = offset + limit + 1
                line_rows = adapter.db[family].execute(
                    "SELECT l.id,l.pointer,f.source_path,l.ordinal FROM readable_line l "
                    "JOIN readable_file f ON f.id=l.file_id WHERE instr(l.raw_text,?)>0 "
                    "ORDER BY f.source_path,l.ordinal,l.id LIMIT ?", (query, need)).fetchall()
                anchors = adapter.context.execute(
                    "SELECT anchor_id,source_run_id FROM context_entry c "
                    "WHERE c.family='readable_archive' AND c.title_resolution_status='resolved' "
                    "AND instr(c.display_title,?)>0 ORDER BY c.source_path,c.anchor_id LIMIT ?",
                    (query, need)).fetchall()
                title_rows = []
                for anchor in anchors:
                    line = adapter.db[family].execute(
                        "SELECT l.id,l.pointer,f.source_path,l.ordinal FROM readable_file f "
                        "JOIN readable_line l ON l.file_id=f.id AND l.ordinal=0 "
                        "WHERE f.id=? AND f.run_id=?", tuple(anchor)).fetchone()
                    if line is None:
                        raise ValueError("resolved book title lacks a physical first line")
                    title_rows.append(line)
                unique = {row["id"]: row for row in (*line_rows, *title_rows)}
                ordered = sorted(unique.values(), key=lambda row: (row["source_path"], row["ordinal"], row["id"]))
                rows = ordered[offset:offset + limit + 1]
            else:
                rows = adapter.db[family].execute(
                    "SELECT t.id,t.pointer,d.source_path FROM document_text t JOIN document_slot s ON s.id=t.slot_id "
                    "JOIN document_row d ON d.id=s.row_id WHERE instr(t.raw_text,?)>0 "
                    "ORDER BY d.source_path,d.ordinal,t.id LIMIT ? OFFSET ?",
                    (query, limit + 1, offset)).fetchall()
            for row in rows[:limit]:
                local = {"snapshot_id": SNAPSHOT, "family": family, "run_id": entry["run"],
                         "text_id": row["id"], "source_path": row["source_path"], "pointer": row["pointer"]}
                pairs.append((local, adapter.read(local)))
            next_after = offset + limit if len(rows) > limit else None
        elif entry["route"] == "event17":
            page = adapter.search(run=entry["run"], contract=entry["contract"],
                                  query=query, locale="CHS", limit=limit, cursor=after)
            pairs = [(x["container_id"], x) for x in page["items"]]
            next_after = page["next_cursor"]
        elif entry["route"] == "native_message":
            page = adapter.search(run=entry["run"], contract=entry["contract"],
                                  query=query, locale="CHS", limit=limit, cursor=after)
            pairs = [(x["row_id"], x) for x in page["items"]]
            next_after = page["next_cursor"]
        elif entry["route"].startswith("event_"):
            page = adapter.search_text(ingest_run_id=entry["run"], family_contract_version=entry["contract"],
                                       query=query, filters={"locale": "CHS"}, limit=limit, cursor=after).envelope()["data"]
            pairs = [(x["container_id"], x) for x in page["items"]]
            next_after = page["next_cursor"]
        elif entry["route"].startswith("native_archive_"):
            page = adapter.search_text(ingest_run_id=entry["run"], family_contract_version=entry["contract"],
                                       query=query, filters={"locale": "CHS"}, limit=limit, cursor=after).envelope()["data"]
            pairs = [(x["container_id"], x) for x in page["items"]]
            next_after = page["next_cursor"]
        elif entry["route"] == "native_bulk_71":
            page = adapter.search_text(ingest_run_id=entry["run"], family_contract_version=entry["contract"],
                                       query=query, filters={"locale": "CHS"}, limit=limit, cursor=after).envelope()["data"]
            pairs = [(x["container_id"], x) for x in page["items"]]
            next_after = page["next_cursor"]
        elif entry["route"] == "mail_71":
            page = adapter.search_text(ingest_run_id=entry["run"], family_contract_version=entry["contract"],
                                       query=query, filters={"locale": "CHS"}, limit=limit, cursor=after).envelope()["data"]
            pairs = [(x["container_id"], x) for x in page["items"]]
            next_after = page["next_cursor"]
        elif entry["route"] == "loading_tips_71":
            rows = adapter.execute(
                "SELECT t.id,t.raw_text,s.row_id FROM loading_tip_text t JOIN loading_tip_slot s ON s.id=t.slot_id "
                "WHERE s.locale='CHS' AND t.id>? AND instr(t.raw_text,?)>0 ORDER BY t.id LIMIT ?",
                (after or "", query, limit + 1)).fetchall()
            pairs = [(x["row_id"], {"raw_text": x["raw_text"], "text_candidate_id": x["id"]})
                     for x in rows[:limit]]
            next_after = rows[limit - 1]["id"] if len(rows) > limit else None
        elif entry["route"] == "physical_sqlite":
            selector = entry["selector"]
            if selector == "event13":
                rows = adapter.execute(
                    "SELECT c.id,c.raw_text,o.row_id,o.field_pointer FROM textmap_candidate c "
                    "JOIN numeric_observation o ON o.id=c.observation_id WHERE c.locale='CHS' "
                    "AND c.id>? AND instr(c.raw_text,?)>0 ORDER BY c.id LIMIT ?",
                    (after or "", query, limit + 1)).fetchall()
                pairs = [(x["row_id"], {"text_candidate_id": x["id"], "raw_text": x["raw_text"],
                          "field_pointer": x["field_pointer"],
                          "semantic_mapping": "uninterpreted_numeric_TextMap_candidate_only"}) for x in rows[:limit]]
            elif selector in ("native21", "native26"):
                rows = adapter.execute(
                    "SELECT c.id,c.raw_text,s.row_id,s.owner_field,s.owner_role_status FROM text_candidate c "
                    "JOIN text_slot s ON s.id=c.slot_id WHERE s.locale='CHS' AND c.id>? "
                    "AND instr(c.raw_text,?)>0 ORDER BY c.id LIMIT ?",
                    (after or "", query, limit + 1)).fetchall()
                pairs = [(x["row_id"], {"text_candidate_id": x["id"], "raw_text": x["raw_text"],
                          "owner_field": x["owner_field"], "owner_role_status": x["owner_role_status"]})
                         for x in rows[:limit]]
            else:
                rows = adapter.execute(
                    "SELECT id,source_path,pointer FROM selected_occurrence WHERE run_id=? AND id>? "
                    "AND (instr(source_path,?)>0 OR instr(pointer,?)>0 OR instr(coalesce(raw_json,''),?)>0) "
                    "ORDER BY id LIMIT ?", (entry["run"], after or "", query, query, query, limit + 1)).fetchall()
                pairs = [(x["id"], {"source_path": x["source_path"], "pointer": x["pointer"],
                          "search_semantics": "physical_path_pointer_or_raw_literal_only"}) for x in rows[:limit]]
            next_after = rows[limit - 1]["id"] if len(rows) > limit else None
        results = []
        for local, payload in pairs:
            if entry["route"] not in ("character", "quest", "source_context"):
                exact = self.read(self.ref(entry, local))
                payload = {**payload, "container": {"source_path": exact["source_path"],
                          "pointer": exact["pointer"]},
                           "source_context": exact["source_context"],
                           "citation_label": exact["citation_label"]}
            results.append(self.envelope(entry, local, payload))
        return {"snapshot": SNAPSHOT, "capability_id": capability_id,
                "results": results,
                "next_cursor": token({"capability_id": capability_id, "run": entry["run"],
                                      "query": query, "limit": limit, "after": next_after}) if next_after is not None else None,
                "partial": True, "candidate_retrieval_complete": next_after is None,
                "product_accepted": False}

    def read(self, evidence_ref):
        entry, local = self.unref(evidence_ref, "read")
        adapter = self.adapter(entry)
        if entry["route"] == "character":
            payload = adapter.read(local["kind"], local["row_id"])
        elif entry["route"] == "narrative":
            payload = adapter.read(local)
        elif entry["route"] == "quest":
            payload = adapter.read(local)
        elif entry["route"] == "source_context":
            payload = adapter.read(local)
        elif entry["route"].startswith("event_"):
            payload = adapter.get_evidence(ingest_run_id=entry["run"],
                family_contract_version=entry["contract"], occurrence_id=local).envelope()["data"]
            if payload.get("status") == "NotFound":
                raise KeyError(evidence_ref)
        elif entry["route"].startswith("native_archive_"):
            payload = adapter.get_evidence(ingest_run_id=entry["run"],
                family_contract_version=entry["contract"], occurrence_id=local).envelope()["data"]
            if payload.get("status") == "NotFound":
                raise KeyError(evidence_ref)
        elif entry["route"] == "native_bulk_71":
            payload = adapter.get_evidence(ingest_run_id=entry["run"],
                family_contract_version=entry["contract"], occurrence_id=local).envelope()["data"]
            if payload.get("status") == "NotFound":
                raise KeyError(evidence_ref)
        elif entry["route"] == "mail_71":
            payload = adapter.get_evidence(ingest_run_id=entry["run"],
                family_contract_version=entry["contract"], occurrence_id=local).envelope()["data"]
            if payload.get("status") == "NotFound":
                raise KeyError(evidence_ref)
        elif entry["route"] == "loading_tips_71":
            row = adapter.execute("SELECT * FROM loading_tip_row WHERE id=? AND run_id=?",
                                  (local, entry["run"])).fetchone()
            if row is None:
                raise KeyError(evidence_ref)
            container = dict(row)
            source_row = json.loads((self.source / container["source_path"]).read_text(encoding="utf-8"))[container["ordinal"]]
            if source_row != json.loads(container["raw_json"]):
                raise ValueError("LoadingTips exact source row drift")
            text = [dict(x) for x in adapter.execute(
                "SELECT t.*,s.field,s.locale FROM loading_tip_text t JOIN loading_tip_slot s ON s.id=t.slot_id "
                "WHERE s.row_id=? ORDER BY t.id", (local,))]
            payload = {"container": container, "text_candidates": text, "single_physical_container": True}
        elif entry["route"] == "physical_sqlite":
            payload = self._physical_read(adapter, entry, local)
        else:
            payload = adapter.get(run=entry["run"], contract=entry["contract"], occurrence_id=local)
            if payload.get("status") == "NotFound":
                raise KeyError(evidence_ref)
        container = payload.get("container")
        if container and container.get("raw_json") is not None and container.get("source_path") and container.get("pointer"):
            if json.loads(container["raw_json"]) != physical_pointer(self.source, container["source_path"], container["pointer"]):
                raise ValueError("bounded source-to-container drift")
        return self.envelope(entry, local, payload)

    def _physical_read(self, adapter, entry, local):
        selector = entry["selector"]
        table = {"event13": "physical_row", "event16": "selected_occurrence",
                 "event18": "selected_occurrence", "native21": "row_occurrence",
                 "native26": "row_occurrence"}[selector]
        row = adapter.execute(f"SELECT * FROM {table} WHERE id=? AND run_id=?", (local, entry["run"])).fetchone()
        if row is None:
            raise KeyError(local)
        result = dict(row)
        if result.get("raw_json") is not None and hashlib.sha256(result["raw_json"].encode()).hexdigest() != result["raw_sha256"]:
            raise ValueError("raw physical record SHA drift")
        if result.get("raw_json") is not None and json.loads(result["raw_json"]) != physical_pointer(
                self.source, result["source_path"], result["pointer"]):
            raise ValueError("raw physical source coordinate drift")
        if selector in ("event16", "event18") and result["projection_kind"] == "accepted_readthrough":
            target_entry = self.entries.get("narrative_" + result["target_family"])
            if target_entry is None or (result["target_contract"], result["target_parser"], result["target_run"]) != (
                    target_entry["contract"], target_entry["parser"], target_entry["run"]):
                raise ValueError("Event read-through target contract drift")
            target_ref = self.ref(target_entry, result["target_occurrence_id"])
            target = self.read(target_ref)["data"]["evidence"]
            if (target["source_path"], target["pointer"], target["raw_sha256"]) != (
                    result["source_path"], result["pointer"], result["raw_sha256"]):
                raise ValueError("Event read-through target coordinate drift")
            result["target_ref"] = target_ref
        if selector in ("native21", "native26") and result["raw_json"] is None:
            target_capability = "event_physical_13" if selector == "native21" else "event_physical_16"
            target_entry = self.entries[target_capability]
            target_db = self.adapter(target_entry)
            target_release = json.loads(target_db.execute("SELECT * FROM release WHERE id=1").fetchone()[1])
            if (result["reuse_family"] != "event_narrative_archive" or result["reuse_contract"] != target_release["contract"]
                    or result["reuse_parser"] != target_entry["parser"] or result["reuse_run"] != target_entry["run"]):
                raise ValueError("Native physical reuse contract drift")
            target = self._physical_read(target_db, target_entry, result["reuse_occurrence_id"])
            if (target["source_path"], target["pointer"], target["raw_sha256"]) != (
                    result["source_path"], result["pointer"], result["raw_sha256"]):
                raise ValueError("Native physical reuse coordinate drift")
            result["reuse_target_ref"] = self.ref(target_entry, result["reuse_occurrence_id"])
        if selector == "event13":
            result["textmap_candidate_observations"] = [dict(x) for x in adapter.execute(
                "SELECT o.field_pointer,o.raw_value,c.locale,c.tier,c.source_path,c.pair_ordinal,c.raw_text "
                "FROM numeric_observation o LEFT JOIN textmap_candidate c ON c.observation_id=o.id "
                "WHERE o.row_id=? ORDER BY o.field_pointer,c.locale,c.tier,c.pair_ordinal", (local,))]
        elif selector in ("native21", "native26"):
            result["text_slots"] = [dict(x) for x in adapter.execute(
                "SELECT owner_field,owner_role_status,locale,raw_hash,state,candidate_count FROM text_slot "
                "WHERE row_id=? ORDER BY owner_field,locale", (local,))]
        result["single_physical_container"] = True
        result["semantic_owner_mapping_unresolved"] = selector in ("event13", "native21", "native26") or result.get("source_kind") == "reminder"
        result["logical_relation_closure"] = False
        return result

    def inspect(self, evidence_ref):
        entry, local = self.unref(evidence_ref, "inspect")
        adapter = self.adapter(entry)
        if entry["route"] == "character":
            payload = adapter.inspect(local["kind"], local["row_id"])
        elif entry["route"] == "narrative":
            payload = adapter.inspect(local)
        elif entry["route"] == "quest":
            payload = adapter.inspect(local)
        elif entry["route"] == "source_context":
            payload = adapter.inspect(local)
        elif entry["route"] == "physical_sqlite":
            payload = self._physical_read(adapter, entry, local)
        else:
            payload = self.read(evidence_ref)["data"]
        return self.envelope(entry, local, payload)

    def follow(self, evidence_ref, *, limit=20, cursor=None):
        entry, local = self.unref(evidence_ref, "follow")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("bounded follow limit 1..50")
        after = None
        if cursor is not None:
            parsed = untoken(cursor)
            if parsed.get("evidence_ref") != evidence_ref or parsed.get("limit") != limit:
                raise ValueError("follow cursor scope mismatch")
            after = parsed.get("after")
        def next_cursor(next_after):
            return token({"evidence_ref": evidence_ref, "limit": limit, "after": next_after}) if next_after is not None else None
        adapter = self.adapter(entry)
        if entry["route"] == "character":
            if after is not None and (type(after) is not int or after < 0):
                raise ValueError("Character follow cursor")
            if local["kind"] != "avatar":
                return {"evidence_ref": evidence_ref, "status": "unsupported", "edges": [], "partial": True}
            page = adapter.follow(local["row_id"], limit=limit, offset=after or 0)
            edges = [{"target_ref": self.ref(self.entries["character_" + x["kind"]],
                                           {"kind": x["kind"], "row_id": x["evidence_ref"]}),
                      "status": "physical_candidate", "logical_relation_closure": False}
                     for x in page["items"]]
            return {"evidence_ref": evidence_ref, "edges": edges,
                    "next_cursor": next_cursor(page["next_offset"]), "partial": True}
        if entry["route"] == "narrative":
            if after is not None and not isinstance(after, str):
                raise ValueError("Narrative follow cursor")
            page = adapter.follow(local, limit=limit, cursor=after)
            edges = []
            for edge in page["results"]:
                target_entry = self.entries.get("narrative_" + edge.get("target_family", ""))
                edges.append({**edge,
                    "target_ref": self.ref(target_entry, edge["evidence_ref"])
                    if target_entry and "evidence_ref" in edge else None,
                    "logical_relation_closure": False})
            return {"evidence_ref": evidence_ref, "edges": edges,
                    "locator_states": page.get("locator_states", []),
                    "search_complete": page["search_complete"],
                    "next_cursor": next_cursor(page["next_cursor"]), "partial": True}
        if entry["route"] == "quest":
            if after is not None and (type(after) is not int or after < 0):
                raise ValueError("Quest follow cursor")
            page = adapter.follow(local)
            all_edges = page["edges"]
            edges = []
            for edge in all_edges[(after or 0):(after or 0) + limit]:
                target = edge.get("target_ref")
                target_entry = next((x for x in self.entries.values()
                    if x["route"] == "quest" and isinstance(target, dict)
                    and x["selector"] == target.get("family")), None)
                edges.append({**edge, "target_ref": self.ref(target_entry, target) if target_entry else None,
                              "logical_relation_closure": False})
            next_offset = (after or 0) + limit if (after or 0) + limit < len(all_edges) else None
            return {"evidence_ref": evidence_ref, "edges": edges,
                    "next_cursor": next_cursor(next_offset), "partial": True}
        if entry["route"] == "physical_sqlite" and entry["selector"] in ("event16", "event18"):
            row = self._physical_read(adapter, entry, local)
            target_entry = self.entries.get("narrative_" + str(row.get("target_family")))
            if row["projection_kind"] != "accepted_readthrough" or target_entry is None:
                return {"evidence_ref": evidence_ref, "status": "unsupported", "edges": [], "partial": True}
            target_ref = self.ref(target_entry, row["target_occurrence_id"])
            target = self.read(target_ref)["data"]["evidence"]
            if (target["source_path"], target["pointer"], target["raw_sha256"]) != (
                    row["source_path"], row["pointer"], row["raw_sha256"]):
                raise ValueError("Event accepted target second-hop drift")
            return {"evidence_ref": evidence_ref, "edges": [{"target_ref": target_ref,
                "status": "resolved_unique_physical_candidate", "candidate_navigation": True,
                "logical_relation_closure": False}], "partial": True}
        if entry["route"] == "physical_sqlite" and entry["selector"] in ("native21", "native26"):
            row = self._physical_read(adapter, entry, local)
            locators = [dict(x) for x in adapter.execute(
                "SELECT source_field,relation_kind,raw_locator,status,candidate_count FROM relation_locator "
                "WHERE row_id=? ORDER BY id", (local,))]
            edges = ([{"target_ref": row["reuse_target_ref"], "status": "resolved_unique_physical_candidate",
                       "candidate_navigation": True, "logical_relation_closure": False}]
                     if row.get("reuse_target_ref") else [])
            return {"evidence_ref": evidence_ref, "edges": edges, "locator_states": locators,
                    "unexposed_relation_candidates": sum(x["candidate_count"] for x in locators),
                    "search_complete": False if locators else True, "partial": True}
        return {"evidence_ref": evidence_ref, "status": "unsupported",
                "edges": [], "partial": True}
