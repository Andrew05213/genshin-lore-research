"""Read-only bounded NPC/Dialog/raw Reminder runtime, with exact physical refs."""
import base64
import json
import sqlite3
from pathlib import Path
from .narrative_7_1_contract import ROOT, SPECS, SCOPE, SNAPSHOT, canonical, digest, sha, check_source, no_sidecars, ref
from .narrative_7_1_verify import source_rows, expected_schema


class Narrative71:
    def __init__(self, source, bindings, *, prepublication=False):
        self.source = Path(source)
        self.scope = json.loads(SCOPE.read_text())
        check_source(self.source, self.scope)
        self.prepublication = prepublication
        authority = None
        if not prepublication:
            authority = json.loads((ROOT / "manifests/narrative-7.1/runtime.json").read_text())
            if authority["status"] != "accepted_standalone_candidates" or authority["snapshot"] != SNAPSHOT:
                raise ValueError("standalone acceptance authority")
            for path, expected in authority["runtime_implementation"].items():
                if sha(ROOT / path) != expected:
                    raise ValueError("runtime implementation drift")
            for path, expected in authority['acceptance'].items():
                if sha(ROOT / path) != expected:
                    raise ValueError('standalone acceptance proof drift')
        self.db, self.release, self.paths, self.fingerprints, self.source_cache = {}, {}, {}, {}, {}
        for family, binding in bindings.items():
            if family not in SPECS:
                raise ValueError("unrecognized family")
            artifact = Path(binding["artifact"]).absolute()
            report = json.loads(Path(binding["report"]).read_text())
            if authority is not None and (report["artifact"] != authority["families"][family]["artifact"] or report["release"] != authority["families"][family]["release"]):
                raise ValueError("tracked exact family authority")
            no_sidecars(artifact)
            if artifact.stat().st_size != report["artifact"]["size_bytes"] or sha(artifact) != report["artifact"]["sha256"]:
                raise ValueError("exact artifact binding")
            c = sqlite3.connect(artifact.as_uri() + "?mode=ro&immutable=1", uri=True)
            if c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall() != expected_schema():
                raise ValueError("schema binding")
            release = json.loads(c.execute("SELECT payload FROM release").fetchone()[0])
            if release != report["release"] or release["family"] != family or release["snapshot"] != SNAPSHOT or release["scope_sha256"] != sha(SCOPE):
                raise ValueError("exact run/snapshot binding")
            expected_impl = {p: sha(ROOT / p) for p in ("src/genshin_lore/narrative_7_1_build.py", "src/genshin_lore/narrative_7_1_contract.py", "src/genshin_lore/loading_tips_source.py", "schemas/narrative-7.1-npc-dialog-reminder.sql")}
            parser = "narrative71-parser:" + digest(expected_impl)
            if release["implementation"] != expected_impl or release["parser"] != parser or release["run"] != "narrative71-run:" + digest([family, parser, sha(SCOPE)]) or release["source_files"] != self.scope["files"]:
                raise ValueError("independent parser/run/source binding")
            c.row_factory = sqlite3.Row
            self.db[family], self.release[family], self.paths[family] = c, release, artifact
            self.fingerprints[artifact] = self._stat(artifact)
        for item in self.scope["files"]:
            path = self.source / item["path"]
            self.fingerprints[path] = self._stat(path)

    @staticmethod
    def _stat(path):
        s = path.stat()
        return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)

    def _stable(self):
        for path, expected in self.fingerprints.items():
            if self._stat(path) != expected:
                raise ValueError("qualified input changed")
        for path in self.paths.values():
            no_sidecars(path)

    def _locate(self, evidence_ref):
        if not evidence_ref.startswith("narrative71:"):
            raise ValueError("foreign evidence ref")
        snapshot, family, run, n = json.loads(evidence_ref[len("narrative71:"):])
        if snapshot != SNAPSHOT or family not in self.db or run != self.release[family]["run"] or type(n) is not int or n < 0:
            raise ValueError("exact snapshot/family/run required")
        if ref(family, run, n) != evidence_ref:
            raise ValueError("noncanonical evidence ref")
        return family, n

    def _recheck(self, family, n):
        row = self.db[family].execute("SELECT * FROM physical_row WHERE ordinal=?", (n,)).fetchone()
        if row is None:
            raise KeyError("physical evidence not found")
        if family not in self.source_cache:
            self.source_cache[family] = source_rows(self.source / SPECS[family][0])
        source_row, raw = self.source_cache[family][n]
        if row["raw_json"] != raw or row["source_path"] != SPECS[family][0] or row["pointer"] != f"/{n}" or row["evidence_ref"] != ref(family, self.release[family]["run"], n):
            raise ValueError("exact source recheck")
        return dict(row)

    def _context(self, family, n, locale):
        c = self.db[family]
        field = "nameTextMapHash" if family == "npc" else "talkTitleTextMapHash" if family == "dialog" else None
        title_rows = [] if field is None else c.execute("SELECT * FROM text_candidate WHERE row_ordinal=? AND field=? AND locale=? ORDER BY tier,pair_ordinal", (n, field, locale)).fetchall()
        for title_row in title_rows:
            self._text_recheck(title_row)
        names = [r["raw_text"] for r in title_rows]
        status = "unresolved" if not names else "resolved" if len(names) == 1 and names[0] else "ambiguous" if len(names) > 1 else "unresolved"
        title = names[0] if status == "resolved" else None
        title_refs = [ref(family, self.release[family]["run"], n)] if title else []
        title_pairs = [dict(r) for r in title_rows]
        # Dialog may borrow a name only through a single exact NPC physical candidate.
        if family == "dialog" and status == "unresolved":
            candidates = c.execute("SELECT target_ordinal FROM physical_candidate WHERE row_ordinal=? AND field='talkRole'", (n,)).fetchall()
            if len(candidates) > 1:
                status = "ambiguous"
            elif len(candidates) == 1 and "npc" in self.db:
                owner = candidates[0][0]
                self._recheck("npc", owner)
                context = self._context("npc", owner, locale)
                status, title, names = context["title_resolution_status"], context["display_title"], context["candidate_titles"]
                title_refs = context["title_evidence_refs"]
                title_pairs = context["title_textmap_candidates"]
        label = f"「{title}」—{'NPC资料' if family == 'npc' else '对话'}" if title else f"{SPECS[family][0]} /{n}（标题{status}）"
        return {"display_title": title, "title_resolution_status": status, "candidate_titles": names,
                "source_kind": family, "title_basis": "source-authored exact physical candidates" if title else None,
                "title_evidence_refs": title_refs,
                "title_textmap_candidates": title_pairs,
                "citation_label": label}

    def _text_recheck(self, candidate):
        path = candidate["source_path"]
        if path not in self.source_cache:
            self.source_cache[path] = json.loads((self.source / path).read_text(), object_pairs_hook=lambda p: p)
        key, text = self.source_cache[path][candidate["pair_ordinal"]]
        if (key, text) != (candidate["hash"], candidate["raw_text"]):
            raise ValueError("exact TextMap pair recheck")

    def _envelope(self, family, data):
        self._stable()
        result = {"snapshot": SNAPSHOT, "family": family, "product_accepted": False, "research_corpus_accepted": False,
                  "partial": True, "coverage": "declared standalone physical families only", "logical_relation_closure": False,
                  "semantic_mapping": self.release[family]["semantic_mapping"], **data}
        result["authority"] = "prepublication" if self.prepublication else "accepted_standalone_candidates"
        if family == "reminder":
            result["text_observation_semantics"] = "uninterpreted_numeric_candidate_observation; not body/title/speaker evidence"
        if len(canonical(result).encode()) >= 262144:
            raise ValueError("payload exceeds bounded response budget; reduce limit")
        return result

    def _page(self, scope, cursor, limit):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit 1..100")
        offset = 0
        if cursor:
            payload = json.loads(base64.urlsafe_b64decode(cursor))
            if payload["scope"] != digest(scope) or type(payload["offset"]) is not int or payload["offset"] < 0:
                raise ValueError("cursor scope")
            offset = payload["offset"]
        return offset

    @staticmethod
    def _cursor(scope, offset):
        return base64.urlsafe_b64encode(canonical({"scope": digest(scope), "offset": offset}).encode()).decode()

    def search(self, family, query, locale="CHS", limit=20, cursor=None, native_key=None):
        self._stable()
        if family not in self.db or locale not in ("CHS", "EN", "JP") or not isinstance(query, str) or not query:
            raise ValueError("search scope")
        scope = ["search", self.release[family]["run"], query, locale, limit, native_key]
        offset = self._page(scope, cursor, limit)
        rows = self.db[family].execute("SELECT t.* FROM text_candidate t JOIN physical_row r ON r.ordinal=t.row_ordinal WHERE t.locale=? AND instr(t.raw_text,?)>0 AND (? IS NULL OR r.native_key=?) ORDER BY t.row_ordinal,t.field,t.tier,t.pair_ordinal LIMIT ? OFFSET ?", (locale, query, native_key, native_key, limit + 1, offset)).fetchall()
        results = []
        for text in rows[:limit]:
            self._text_recheck(text)
            row = self._recheck(family, text["row_ordinal"])
            context = self._context(family, text["row_ordinal"], locale)
            results.append({"evidence_ref": row["evidence_ref"], "text_candidate": dict(text), "source_context": context,
                            "citation_label": context["citation_label"], "uninterpreted_candidate_observation": family == "reminder"})
        return self._envelope(family, {"results": results, "candidate_retrieval_complete": True,
                                      "search_semantics": "literal_numeric_TextMap_observations_only; semantic_owner_search_unsupported" if family == "reminder" else "literal_declared_source_text",
                                      "next_cursor": self._cursor(scope, offset + limit) if len(rows) > limit else None})

    def read(self, evidence_ref, locale="CHS", limit=20, cursor=None):
        self._stable()
        if locale not in ("CHS", "EN", "JP"):
            raise ValueError("locale scope")
        family, n = self._locate(evidence_ref)
        scope = ["read", evidence_ref, locale, limit]
        offset = self._page(scope, cursor, limit)
        row = self._recheck(family, n)
        texts = self.db[family].execute("SELECT * FROM text_candidate WHERE row_ordinal=? AND locale=? ORDER BY field,tier,pair_ordinal LIMIT ? OFFSET ?", (n, locale, limit + 1, offset)).fetchall()
        for text in texts[:limit]:
            self._text_recheck(text)
        context = self._context(family, n, locale)
        return self._envelope(family, {"evidence": row, "texts": [dict(t) for t in texts[:limit]], "source_context": context,
                                      "citation_label": context["citation_label"], "single_physical_container": True,
                                      "next_cursor": self._cursor(scope, offset + limit) if len(texts) > limit else None})

    def inspect(self, evidence_ref, locale="CHS"):
        result = self.read(evidence_ref, locale)
        family, n = self._locate(evidence_ref)
        result["locators"] = [dict(r) for r in self.db[family].execute("SELECT * FROM locator WHERE row_ordinal=? ORDER BY field,item_ordinal", (n,))]
        result["text_resolutions"] = [dict(r) for r in self.db[family].execute("SELECT * FROM text_resolution WHERE row_ordinal=? ORDER BY field,locale", (n,))]
        result["text_owners"] = [dict(r) for r in self.db[family].execute("SELECT * FROM text_owner WHERE row_ordinal=? ORDER BY field", (n,))]
        return self._envelope(family, result)

    def follow(self, evidence_ref, limit=20, cursor=None):
        self._stable()
        family, n = self._locate(evidence_ref)
        self._recheck(family, n)
        scope = ["follow", evidence_ref, limit, sorted((f, r["run"]) for f, r in self.release.items())]
        offset = self._page(scope, cursor, limit)
        if family == "npc":
            if "dialog" not in self.db:
                return self._envelope(family, {"results": [], "search_complete": False, "status": "target_family_unavailable", "next_cursor": None})
            matches = self.db["dialog"].execute("SELECT row_ordinal AS target_ordinal,'dialog' AS target_family,field,item_ordinal FROM physical_candidate WHERE target_source_path=? AND target_ordinal=? ORDER BY row_ordinal,field,item_ordinal LIMIT ? OFFSET ?", (SPECS["npc"][0], n, limit + 1, offset)).fetchall()
        else:
            matches = self.db[family].execute("SELECT p.target_ordinal,l.target_family,p.field,p.item_ordinal FROM physical_candidate p JOIN locator l USING(row_ordinal,field,item_ordinal) WHERE p.row_ordinal=? ORDER BY p.field,p.item_ordinal,p.target_ordinal LIMIT ? OFFSET ?", (n, limit + 1, offset)).fetchall()
        results = []
        if family == 'npc':
            complete = not self.db['dialog'].execute(
                "SELECT 1 FROM locator WHERE field='talkRole' AND state IN ('mapping_unresolved','absent_discriminator') LIMIT 1"
            ).fetchone()
        else:
            complete = not self.db[family].execute(
                "SELECT 1 FROM locator WHERE row_ordinal=? AND state IN ('mapping_unresolved','absent_discriminator') LIMIT 1", (n,)
            ).fetchone()
        for match in matches[:limit]:
            target = match["target_family"]
            if target not in self.db:
                complete = False
                results.append({**dict(match), "status": "target_family_unavailable"})
            else:
                row = self._recheck(target, match["target_ordinal"])
                results.append({**dict(match), "evidence_ref": row["evidence_ref"], "candidate_navigation": True,
                                "target_parser": self.release[target]["parser"], "target_run": self.release[target]["run"],
                                "target_contract": self.release[target]["contract"], "target_snapshot": SNAPSHOT})
        return self._envelope(family, {"results": results, "candidate_navigation": True, "search_complete": complete,
                                      "locator_states": [] if family == "npc" else [dict(r) for r in self.db[family].execute("SELECT * FROM locator WHERE row_ordinal=? ORDER BY field,item_ordinal", (n,))],
                                      "unsupported_navigation": ["npc_to_reminder"],
                                      "next_cursor": self._cursor(scope, offset + limit) if len(matches) > limit else None})

    def close(self):
        for db in self.db.values():
            db.close()
