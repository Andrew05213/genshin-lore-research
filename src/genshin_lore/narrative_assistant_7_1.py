"""Capability-first assistant context over exact standalone 7.1 evidence.

This module prepares auditable context; it does not infer story relations or
declare a 7.1 Research corpus/product. An answering agent must cite the exact
refs in ``citations`` and keep candidate/unknown material out of factual claims.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import defaultdict

from .research_7_1_bounded import Research71Bounded, SNAPSHOT


ROUTES = {
    "character": ("character_avatar",),
    "npc": ("narrative_npc",),
    "spoken": ("narrative_npc", "character_avatar"),
    "quest": ("quest_narrative_archive", "quest_review_archive"),
    "event": ("event_native_text_17", "event_narrative_10", "event_narrative_11",
              "event_narrative_12", "event_narrative_14", "event_narrative_15"),
    "book": ("readable_archive", "document_archive"),
    "codex": ("codexquest_archive",),
    "native": ("native_message", "native_book_music", "native_aux_quest", "native_bulk"),
    "version_story": ("quest_narrative_archive", "event_native_text_17", "readable_archive",
                      "character_avatar", "narrative_npc"),
}

CAPABILITY_GROUPS = {
    "character": ("character_avatar", "character_story", "character_voice"),
    "npc": ("narrative_npc", "narrative_dialog"),
    "spoken": ("narrative_npc", "narrative_dialog", "character_avatar", "character_voice"),
    "quest": ("quest_narrative_archive", "quest_review_archive", "quest_talk_physical"),
    "event": ROUTES["event"] + ("narrative_dialog", "quest_talk_physical"),
    "book": ROUTES["book"],
    "codex": ROUTES["codex"],
    "native": ROUTES["native"],
    "version_story": ROUTES["version_story"] + ("character_story", "narrative_dialog", "quest_talk_physical"),
}

TITLE_FIELDS = {"titleTextMapHash", "nameTextMapHash", "storyTitleTextMapHash", "voiceTitleTextMapHash"}
PHYSICAL_STATUSES = {"PHYSICAL_CAPABILITY_ACCEPTED", "RAW_ONLY_ACCEPTED"}
DIRECT_TEXT_STATES = {"source_exact", "resolved_unique", "resolved_main", "resolved_medium",
                      "unique_main", "unique_medium"}
FORBIDDEN_CLAIM_RE = re.compile(r"(首次实装|7\.1新增|7\.0新增|语义顺序|剧情发生顺序|唯一拥有|同一个逻辑实体)")
VERSION_RE = re.compile(r"(?<!\d)(\d+\.\d+)(?!\d)")
AUTHORED_FIELDS = TITLE_FIELDS | {"descTextMapHash", "unlockDescTextMapHash", "contentTextMapHash",
                                 "talkContentTextMapHash", "talkTitleTextMapHash", "talkRoleNameTextMapHash",
                                 "storyContextTextMapHash", "voiceFileTextTextMapHash", "senderTextMapHash"}
SNAPSHOT_LOCAL_AUTHORED_CONTRACTS = {
    "character-7.1-lore-successor-v1", "character-7.1-voice-successor-v1",
}


def _explicit(*values):
    return next((value for value in values if value is not None), None)


def _normalize_text_proof(payload, parent, candidate, role, *, contract, candidate_count):
    """One precedence rule for text_fields and text_slots physical observations."""
    authored = contract in SNAPSHOT_LOCAL_AUTHORED_CONTRACTS and role in AUTHORED_FIELDS
    owner = _explicit(candidate.get("owner_role_status"), parent.get("owner_role_status"),
                      payload.get("owner_role_status"))
    if owner is None:
        owner = "source_authored_field" if authored else "unknown"
    semantic = _explicit(candidate.get("semantic_owner_mapping"), parent.get("semantic_owner_mapping"),
                         payload.get("semantic_owner_mapping"))
    if semantic is None:
        semantic = ("unresolved" if payload.get("semantic_owner_mapping_unresolved") is True else
                    "source_authored_field" if authored else "unknown")
    continuity = _explicit(candidate.get("field_continuity_status"), parent.get("field_continuity_status"),
                           payload.get("field_continuity_status"))
    if continuity is None:
        continuity = "not_applicable" if authored else "unknown"
        continuity_basis = "snapshot_local_accepted_source_authored_contract" if authored else "unknown"
    elif continuity == "not_applicable":
        continuity_basis = "snapshot_local_accepted_source_authored_contract" if authored else "unproven_not_applicable"
    else:
        continuity_basis = "explicit_runtime_status"
    status = _explicit(candidate.get("status"), candidate.get("resolution_status"),
                       parent.get("resolution_status"), parent.get("status"), parent.get("state")) or "unknown"
    if candidate_count is not None and candidate_count > 1 and status in DIRECT_TEXT_STATES:
        status = "resolved_candidate_set"
    return {"resolution_state": status, "owner_role_status": owner,
            "semantic_owner_mapping": semantic, "field_continuity_status": continuity,
            "field_continuity_basis": continuity_basis}


def _version_scope(question):
    versions = set(VERSION_RE.findall(question))
    if len(versions) > 1 or (versions and re.search(
            r"(?:相比|对比|比较)(?:以前|之前|旧版|其他版本)|(?:以前|之前|旧版|其他版本)(?:相比|对比|比较)", question)):
        return "unsupported_comparison", "Cross-version comparison requires a separate release/history authority."
    if versions and versions != {"7.1"}:
        return "unsupported_version", "This entry is pinned to 7.1, not the requested version."
    if re.search(r"新增|首次|初次实装|是不是.*新", question):
        return "unsupported_release_attribution", "Snapshot presence cannot prove release novelty; independent release/history evidence is required."
    if (re.search(r"比较|对比|有何不同|有什么不同|有何差异|有什么差异|区别|差异", question)
            and (re.search(r"(?:和|与|跟)", question) or re.search(r"两个|两名|二人", question))):
        return "unsupported_multi_subject", "This bounded planner cannot compare multiple entities; no entity is selected."
    return "supported", ("Explicit 7.1 snapshot requested." if versions else
                         "No version specified; defaulting explicitly to the pinned 7.1 snapshot.")


def _intent(question):
    q = question.lower()
    if "npc" in q or "非玩家角色" in q:
        return "npc"
    if "说过什么" in q or "说了什么" in q:
        return "spoken"
    if any(w in q for w in ("角色", "人物", "角色故事", "语音")):
        return "character"
    if any(w in q for w in ("图鉴", "codex")):
        return "codex"
    if any(w in q for w in ("活动", "event")):
        return "event"
    if any(w in q for w in ("任务", "主线", "quest")):
        return "quest"
    if any(w in q for w in ("原生文本", "native")):
        return "native"
    if any(w in q for w in ("书籍", "这本", "书名", "book")) or re.search(r"《[^》]+》", question):
        return "book"
    if "剧情" in q and ("7.1" in q or "版本" in q):
        return "version_story"
    return "unknown"


def _subject(question, explicit):
    if explicit is not None:
        if not isinstance(explicit, str) or not explicit.strip():
            raise ValueError("explicit subject must be nonempty")
        return explicit.strip(), "explicit_subject"
    for pattern in (r"《([^》]{1,80})》", r"[「“\"]([^」”\"]{1,80})[」”\"]"):
        match = re.search(pattern, question)
        if match:
            return match.group(1), "quoted_subject"
    cleaned = re.sub(r"^(?:介绍|总结|分析|请介绍|请总结)", "", question.strip())
    cleaned = re.sub(r"^\d+\.\d+(?:版本|时期|的)?", "", cleaned)
    for pattern in (r"^(.{1,30}?)有哪些(?:角色)?语音", r"^(.{1,30}?)的角色(?:故事|背景)",
                    r"^(.{1,30}?)角色(?:故事|背景)",
                    r"^(.{1,30}?)的(?:背景|故事)", r"^(.{1,30}?)说(?:过|了)什么",
                    r"^(.{1,30}?)发生了什么", r"^(.{1,30}?)这个活动讲了什么",
                    r"^(.{1,30}?)活动讲了什么", r"^(.{1,30}?)活动剧情"):
        match = re.search(pattern, cleaned)
        if match:
            term = re.sub(r"(?:NPC|npc|非玩家角色|任务|活动|角色)$", "", match.group(1).strip()).strip()
            if term:
                return term, "literal_question_span"
    return None, "subject_not_identified"


class CapabilityPlanner71:
    def __init__(self, registry):
        self.registry = registry
        self.entries = {entry["capability_id"]: entry for entry in registry["capabilities"]}

    def plan(self, question, *, subject=None):
        if not isinstance(question, str) or not question.strip():
            raise ValueError("nonempty question required")
        version_status, version_note = _version_scope(question)
        intent = _intent(question)
        term, basis = _subject(question.strip(), subject)
        if version_status == "unsupported_multi_subject":
            term, basis = None, "multi_subject_not_selected"
        search = list(ROUTES.get(intent, ())) if version_status == "supported" else []
        declared = list(CAPABILITY_GROUPS.get(intent, ()))
        for capability in declared:
            if capability not in self.entries:
                raise ValueError("declared 7.1 capability missing: " + capability)
        for capability in search:
            if "search" not in self.entries[capability]["supported_operations"]:
                raise ValueError("route lacks search: " + capability)
        unknown = [version_note]
        if term is None:
            unknown.append("No exact search subject was identified; supply an explicit subject or quote the title/name.")
        if intent == "quest" or intent == "version_story":
            unknown.append("Quest Narrative historical subId/semantic order is unresolved; physical order is not story order.")
        if intent == "event":
            unknown.append("Event selected-component coverage is partial; raw Reminder fields do not prove body/title/speaker roles.")
        return {"contract": "narrative-assistant-7.1-capability-plan-v1", "question": question,
                "intent": intent, "subject": term, "subject_basis": basis,
                "version_status": version_status, "version_note": version_note,
                "planned_capabilities": declared, "search_capabilities": search,
                "unknown_before_search": unknown, "product_accepted": False,
                "research_corpus_accepted": False, "snapshot": SNAPSHOT,
                "partial": True, "no_global_search": True}


def _text_observations(payload, hit, limit=12, locale="CHS", contract=None, text_limit=400):
    """Retain each physical text candidate's own proof state, never a container vote."""
    found = []

    def put(text, role, status, *, owner_status="unknown", semantic_mapping="unknown",
            source_path=None, pointer=None, candidate_id=None, slot_id=None,
            candidate_count=None, selected_candidate_count=None, selected=None,
            text_locale="unknown", tier=None, pair_ordinal=None,
            field_continuity_status="unknown", field_continuity_basis="unknown"):
        if isinstance(text, str) and text:
            found.append({"text": text if text_limit is None else text[:text_limit],
                          "truncated": text_limit is not None and len(text) > text_limit,
                          "text_length": len(text),
                          "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                          "role": role, "owner_field": role, "resolution_state": status or "unknown",
                          "owner_role_status": owner_status or "unknown",
                          "semantic_owner_mapping": semantic_mapping or "unknown",
                          "field_continuity_status": field_continuity_status or "unknown",
                          "field_continuity_basis": field_continuity_basis,
                          "locale": text_locale or "unknown", "slot_id": slot_id,
                          "candidate_count": candidate_count,
                          "selected_candidate_count": selected_candidate_count,
                          "selected": selected, "tier": tier, "pair_ordinal": pair_ordinal,
                          "source_path": source_path, "pointer": pointer,
                          "candidate_id": candidate_id})

    if isinstance(payload.get("raw_text"), str):
        raw_locale = payload.get("locale") or "unknown"
        if raw_locale in (locale, "unknown"):
            text_source = payload.get("text_provenance") or payload.get("provenance") or {}
            put(payload["raw_text"], "direct_text", "source_exact", owner_status="source_exact",
                semantic_mapping="source_exact", text_locale=raw_locale,
                source_path=text_source.get("source_path"), pointer=text_source.get("pointer"),
                pair_ordinal=text_source.get("pair_ordinal"), field_continuity_status="not_applicable",
                field_continuity_basis="direct_source_bytes")
    if isinstance(hit.get("raw_text"), str) and not any(
            payload.get(k) for k in ("raw_text", "text_fields", "texts", "text_candidates")):
        put(hit["raw_text"], "search_hit_text", hit.get("status") or hit.get("resolution_status") or "observed",
            candidate_id=hit.get("text_candidate_id") or hit.get("hit_id") or hit.get("id"),
            text_locale=hit.get("locale") or "unknown")
    for item in payload.get("text_fields", []):
        if item.get("resolution_status") == "missing":
            continue
        for candidate in item.get("candidates", []):
            if candidate.get("locale") and item.get("locale") and candidate["locale"] != item["locale"]:
                continue
            candidate_locale = candidate.get("locale") or item.get("locale") or "unknown"
            if candidate_locale not in (locale, "unknown"):
                continue
            role = item.get("field_name", "text_field")
            proof = _normalize_text_proof(payload, item, candidate, role, contract=contract,
                                          candidate_count=len(item.get("candidates", [])))
            put(candidate.get("text"), role, proof["resolution_state"],
                owner_status=proof["owner_role_status"], semantic_mapping=proof["semantic_owner_mapping"],
                source_path=candidate.get("source_path"), pointer=candidate.get("pointer") or
                ("/" + str(candidate["source_key"]) if candidate.get("source_key") is not None else None),
                candidate_id=candidate.get("id") or {"tier": candidate.get("tier"),
                                                       "physical_ordinal": candidate.get("physical_ordinal")},
                slot_id=item.get("owner_id"), candidate_count=len(item.get("candidates", [])),
                selected_candidate_count=_explicit(candidate.get("selected_candidate_count"), item.get("selected_candidate_count")),
                selected=_explicit(candidate.get("selected"), item.get("selected")),
                text_locale=candidate_locale, tier=candidate.get("tier"),
                pair_ordinal=candidate.get("pair_ordinal", candidate.get("physical_ordinal")),
                field_continuity_status=proof["field_continuity_status"],
                field_continuity_basis=proof["field_continuity_basis"])
    slots = {slot.get("id"): slot for slot in (payload.get("text_slots") or payload.get("slots") or [])}
    for key in ("texts", "text_candidates"):
        for item in payload.get(key, []):
            slot = slots.get(item.get("slot_id"), {})
            slot_locale = slot.get("locale")
            item_locale = item.get("locale")
            if slot_locale and item_locale and slot_locale != item_locale:
                continue  # Conflicting physical locale claims are not safe to show.
            actual_locale = item_locale or slot_locale or "unknown"
            if actual_locale not in (locale, "unknown"):
                continue
            role = item.get("owner_field") or item.get("field") or slot.get("owner_field") or key
            count = slot.get("candidate_count", item.get("candidate_count"))
            proof = _normalize_text_proof(payload, slot, item, role, contract=contract,
                                          candidate_count=count)
            put(item.get("raw_text") or item.get("text"), role, proof["resolution_state"],
                owner_status=proof["owner_role_status"], semantic_mapping=proof["semantic_owner_mapping"],
                source_path=item.get("source_path"), pointer=item.get("pointer"),
                candidate_id=item.get("id") or {"source_path": item.get("source_path"),
                                                 "pair_ordinal": item.get("pair_ordinal")},
                slot_id=item.get("slot_id"), candidate_count=count,
                selected_candidate_count=slot.get("selected_candidate_count", item.get("selected_candidate_count")),
                selected=item.get("selected", slot.get("selected")), text_locale=actual_locale,
                tier=item.get("tier"), pair_ordinal=item.get("pair_ordinal"),
                field_continuity_status=proof["field_continuity_status"],
                field_continuity_basis=proof["field_continuity_basis"])
    for item in payload.get("textmap_candidate_observations", []):
        if item.get("locale") == locale:
            put(item.get("raw_text"), "uninterpreted_numeric_TextMap_candidate", "physical_observation",
                owner_status="unresolved", semantic_mapping="unresolved",
                source_path=item.get("source_path"), pointer=None,
                candidate_id={"tier": item.get("tier"), "pair_ordinal": item.get("pair_ordinal"),
                              "owner_field_pointer": item.get("field_pointer")},
                text_locale=locale, tier=item.get("tier"), pair_ordinal=item.get("pair_ordinal"))
    return found[:limit], len(found) > limit


def _enrich_source_context(context, payload, status):
    """Only a unique CHS title/name owner can supply a new display label."""
    if context.get("title_resolution_status") not in (None, "unresolved") or status in PHYSICAL_STATUSES:
        return context
    slots = payload.get("text_slots") or payload.get("slots") or []
    candidates = payload.get("texts") or payload.get("text_candidates") or []
    titles = []
    for slot in slots:
        if (slot.get("owner_field") not in TITLE_FIELDS or slot.get("locale") != "CHS"
                or slot.get("owner_role_status") != "resolved"
                or slot.get("status") not in DIRECT_TEXT_STATES
                or slot.get("candidate_count") != 1
                or slot.get("selected_candidate_count") not in (None, 1)
                or slot.get("semantic_owner_mapping") not in ("resolved", "source_authored_field", "source_exact")
                or slot.get("field_continuity_status") not in (
                    None, "unknown", "not_applicable", "proven", "proven_continuity", "unchanged", "source_named")
                or payload.get("semantic_owner_mapping_unresolved") is True):
            continue
        matches = [c for c in candidates if c.get("slot_id") == slot.get("id") and
                   c.get("raw_text") and c.get("selected") in (1, True) and
                   c.get("locale", "CHS") == "CHS" and
                   c.get("semantic_owner_mapping") in ("resolved", "source_authored_field", "source_exact")]
        if len(matches) == 1:
            titles.append((matches[0]["raw_text"], slot["owner_field"], matches[0]))
    if len(titles) != 1:
        return context
    title, field, candidate = titles[0]
    return {**context, "display_title": title, "title_resolution_status": "resolved",
            "title_basis": f"source-authored {field}; exact snapshot-local TextMap candidate",
            "candidate_titles": [{"title": title, "source_path": candidate.get("source_path"),
                                  "pointer": candidate.get("pointer"), "candidate_id": candidate.get("id")}]}


def _observation_support(item, record):
    """Proof for this excerpt only; a sibling title cannot promote a body."""
    if item.get("locale") != "CHS" or item.get("resolution_state") not in DIRECT_TEXT_STATES:
        return "physical_candidate"
    if item.get("owner_role_status") not in ("resolved", "source_authored_field", "source_exact"):
        return "physical_candidate"
    if item.get("semantic_owner_mapping") not in ("source_exact", "source_authored_field", "resolved"):
        return "physical_candidate"
    continuity = item.get("field_continuity_status")
    basis = item.get("field_continuity_basis")
    if continuity not in ("proven", "proven_continuity", "unchanged", "source_named") and not (
            continuity == "not_applicable" and basis in (
                "direct_source_bytes", "snapshot_local_accepted_source_authored_contract")):
        return "physical_candidate"
    if item.get("candidate_count") is not None and item["candidate_count"] != 1:
        return "physical_candidate"
    if item.get("selected_candidate_count") is not None and item["selected_candidate_count"] != 1:
        return "physical_candidate"
    if item.get("selected") is not None and item["selected"] not in (1, True):
        return "physical_candidate"
    if record.get("capability_status") in PHYSICAL_STATUSES:
        return "physical_candidate"
    if record.get("relation_scope_status") != "direct_occurrence":
        return "physical_candidate"
    return "confirmed_excerpt"


def classify_claim(claim, records):
    """Check only literal quotation presence; never claim semantic entailment."""
    if not isinstance(claim, str) or not claim.strip():
        raise ValueError("nonempty claim required")
    if FORBIDDEN_CLAIM_RE.search(claim):
        return {"claim": claim, "support_status": "unsupported", "evidence_refs": [],
                "verbatim_quotation_match": False, "semantic_claim_audited": False,
                "reason": "release, order, or logical-identity semantics require separate proof"}
    supported = []
    candidate = []
    for record in records:
        matching = [item for item in record["text_observations"] if claim in item["text"]]
        if any(_observation_support(item, record) == "confirmed_excerpt" for item in matching):
            supported.append(record["evidence_ref"])
        elif matching:
            candidate.append(record["evidence_ref"])
    if supported:
        return {"claim": claim, "support_status": "verbatim_match_only", "evidence_refs": supported,
                "verbatim_quotation_match": True, "semantic_claim_audited": False,
                "reason": "literal substring present in a confirmed excerpt; factual entailment was not audited"}
    if candidate:
        return {"claim": claim, "support_status": "candidate_supported", "evidence_refs": candidate,
                "verbatim_quotation_match": True, "semantic_claim_audited": False,
                "reason": "literal physical observation, but owner, relation, or text resolution is not proven"}
    return {"claim": claim, "support_status": "unsupported", "evidence_refs": [],
            "verbatim_quotation_match": False, "semantic_claim_audited": False,
            "reason": "no exact cited source text in the bounded collected context"}


class NarrativeAssistant71:
    def __init__(self, source, *, runtime=None):
        self.runtime = runtime if runtime is not None else Research71Bounded(source)
        self.planner = CapabilityPlanner71(self.runtime.registry)

    def close(self):
        self.runtime.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def plan(self, question, *, subject=None):
        return self.planner.plan(question, subject=subject)

    def _call(self, operation, *args, **kwargs):
        metrics = getattr(self, "_task_metrics", None)
        deadline = getattr(self, "_task_deadline_at", None)
        if deadline is not None and time.perf_counter() >= deadline:
            raise TimeoutError("bounded task retrieval exceeded task deadline before " + operation)
        if metrics is not None and operation in ("read", "follow"):
            limits = self._task_limits
            key = operation + "_calls"
            bound = limits["max_evidence_reads" if operation == "read" else "max_follow_calls"]
            if metrics[key] >= bound:
                raise ValueError("bounded task " + operation + " call budget exhausted")
        started = time.perf_counter()
        if metrics is not None:
            metrics[operation + "_calls"] += 1
            if operation == "follow":
                metrics["follow_pages"] += 1
        try:
            value = getattr(self.runtime, operation)(*args, **kwargs)
        finally:
            if metrics is not None:
                metrics[operation + "_ms"] += (time.perf_counter() - started) * 1000
        if deadline is not None and time.perf_counter() >= deadline:
            raise TimeoutError("bounded task retrieval exceeded task deadline after " + operation)
        return value

    def _record(self, evidence_ref, hit=None, *, origin="direct", text_limit=400):
        read = self._call("read", evidence_ref)
        inspect = self._call("inspect", evidence_ref)
        if (read["evidence_ref"] != inspect["evidence_ref"] or
                (read["source_path"], read["pointer"]) != (inspect["source_path"], inspect["pointer"])):
            raise ValueError("read/inspect provenance divergence")
        entry, occurrence = self.runtime.unref(evidence_ref, "read")
        if not read["source_path"] or not read["pointer"]:
            raise ValueError("physical source coordinate missing")
        data = read["data"]
        hit_data = (hit or {}).get("data") or {}
        texts, texts_truncated = _text_observations(data, hit_data, contract=read["contract"], text_limit=text_limit)
        texts_truncated = texts_truncated or data.get("text_slots_truncated") is True
        # The bounded runtime owns display labels. Do not create an assistant-only
        # label that a second-hop read cannot independently reproduce.
        context = read["source_context"]
        relation_scope = {"direct": "direct_occurrence", "seed": "seeded_unverified_context",
                          "follow": "physical_candidate_navigation"}[origin]
        proof_record = {"capability_status": entry["status"], "relation_scope_status": relation_scope}
        for index, item in enumerate(texts):
            item["observation_index"] = index
            item["origin"] = origin
            item["relation_scope_status"] = relation_scope
            item["capability_status"] = entry["status"]
            item["support_status"] = _observation_support(item, proof_record)
            item["proof_status"] = item["support_status"]
            limits = []
            if item["resolution_state"] not in DIRECT_TEXT_STATES:
                limits.append("text_resolution_not_unique_or_unknown")
            if item["owner_role_status"] not in ("resolved", "source_authored_field", "source_exact"):
                limits.append("owner_role_not_proven")
            if item["semantic_owner_mapping"] not in ("resolved", "source_authored_field", "source_exact"):
                limits.append("semantic_owner_mapping_not_proven")
            if item["field_continuity_status"] not in (
                    "proven", "proven_continuity", "unchanged", "source_named") and not (
                    item["field_continuity_status"] == "not_applicable" and
                    item["field_continuity_basis"] in (
                        "direct_source_bytes", "snapshot_local_accepted_source_authored_contract")):
                limits.append("field_continuity_not_proven")
            if item["locale"] != "CHS":
                limits.append("locale_not_CHS_or_unknown")
            if item["candidate_count"] is not None and item["candidate_count"] != 1:
                limits.append("multiple_or_missing_physical_text_candidates")
            if item["selected_candidate_count"] is not None and item["selected_candidate_count"] != 1:
                limits.append("selected_candidate_set_not_unique")
            if item["selected"] is not None and item["selected"] not in (1, True):
                limits.append("text_candidate_not_selected")
            if entry["status"] in PHYSICAL_STATUSES:
                limits.append("physical_capability_only")
            if origin != "direct":
                limits.append("seed_or_follow_does_not_prove_logical_relation")
            item["limitations"] = limits
            # Unified delivery retains TEXT identity separately from the owner
            # container's foundation run. This metadata does not change proof.
            bindings = [x for x in data.get('text_candidates', []) if x.get('id') == item['candidate_id']]
            binding = data.get('direct_text_identity') if item['role']=='direct_text' else (bindings[0] if len(bindings)==1 else None)
            if binding and binding.get('formal_evidence_ref'):
                item['formal_evidence_ref']=binding['formal_evidence_ref']
                item['evidence_identity']=binding['evidence_identity']
        evidence_status = "evidence_backed" if any(
            x["support_status"] == "confirmed_excerpt" for x in texts) else "candidate_supported"
        citation_label = read["citation_label"]
        return {"evidence_ref": evidence_ref, "snapshot": read["snapshot"], "capability_id": entry["capability_id"],
                "family": read["family"], "contract": read["contract"], "artifact": read["artifacts"],
                "parser": read["parser"], "run": read["run"], "source_path": read["source_path"],
                "pointer": read["pointer"], "occurrence": occurrence,
                "text_observations": texts, "text_observations_truncated": texts_truncated,
                "resolution_state": context.get("title_resolution_status", "unresolved"),
                "source_context": context, "citation_label": citation_label,
                "source_structure": data.get('structure'), "backend": read.get('backend', 'legacy_bounded'),
                "corpus_ref": read.get('corpus_ref'),
                "evidence_status": evidence_status, "limitations": read["limitations"],
                "capability_status": read["capability_status"], "single_physical_container": True,
                "relation_scope_status": relation_scope,
                "product_accepted": False, "research_corpus_accepted": False}

    def ask(self, question, *, subject=None, seed_refs=(), claims=(), per_capability=1,
            max_follow_reads=4):
        if type(per_capability) is not int or not 1 <= per_capability <= 3:
            raise ValueError("per_capability must be 1..3")
        if type(max_follow_reads) is not int or not 0 <= max_follow_reads <= 8:
            raise ValueError("max_follow_reads must be 0..8")
        seed_refs = tuple(seed_refs)
        start = time.perf_counter()
        planning_started = time.perf_counter()
        plan = self.plan(question, subject=subject)
        if getattr(self, "_task_metrics", None) is not None:
            self._task_metrics["planning_ms"] += (time.perf_counter() - planning_started) * 1000
        invocation = {"subject_override": subject, "seed_refs": list(seed_refs),
                      "per_capability": per_capability, "max_follow_reads": max_follow_reads,
                      "claims": list(claims)}
        if plan["version_status"] != "supported":
            return {"contract": "narrative-assistant-7.1-context-v1", "plan": plan,
                    "invocation": invocation,
                    "status": plan["version_status"], "reason": plan["version_note"],
                    "routes": [], "evidence": [], "context": {"confirmed_evidence": [], "physical_candidates": [],
                    "confirmed_evidence_refs": [], "physical_candidate_refs": [],
                    "candidate_edges": [], "unknown": [plan["version_note"]]},
                    "citations": [], "requested_claims": list(claims), "claim_audit": [], "safe_answer_draft": None,
                    "partial": True, "product_accepted": False, "research_corpus_accepted": False,
                    "latency_ms": round((time.perf_counter() - start) * 1000, 3)}
        records = []
        seen = set()
        routes = []
        anchors = []
        if plan["subject"] is not None:
            for cid in plan["search_capabilities"]:
                begun = time.perf_counter()
                page = self._call("search", plan["subject"], cid, limit=per_capability)
                route = {"capability_id": cid, "query": plan["subject"], "hit_count": len(page["results"]),
                         "next_cursor_present": page["next_cursor"] is not None,
                         "latency_ms": round((time.perf_counter() - begun) * 1000, 3)}
                routes.append(route)
                for hit in page["results"]:
                    ref = hit["evidence_ref"]
                    if ref not in seen:
                        records.append(self._record(ref, hit))
                        seen.add(ref)
                    anchors.append(ref)
        for ref in seed_refs:
            if ref not in seen:
                seeded = self._record(ref, origin="seed")
                seeded["limitations"] = seeded["limitations"] + [
                    "Seed ref has no carried navigation proof; do not infer its relation to this question"]
                records.append(seeded)
                seen.add(ref)
            anchors.append(ref)
        edges = []
        follow_unknown = []
        target_refs = []
        for ref in dict.fromkeys(anchors):
            entry, _ = self.runtime.unref(ref, "read")
            if "follow" not in entry["supported_operations"]:
                continue
            page = self._call("follow", ref, limit=50)
            if page.get("status") == "unsupported":
                follow_unknown.append({"evidence_ref": ref, "reason": "follow unsupported for this capability"})
            if page.get("next_cursor") is not None or page.get("search_complete") is False:
                follow_unknown.append({"evidence_ref": ref, "reason": "candidate navigation/page incomplete"})
            for edge in page.get("edges", []):
                if edge.get("logical_relation_closure") is True:
                    raise ValueError("candidate edge falsely promoted to logical relation")
                compact = {"source_ref": ref, "target_ref": edge.get("target_ref"),
                           "status": edge.get("status") or edge.get("state") or "physical_candidate",
                           "logical_relation_closure": False}
                edges.append(compact)
                if compact["target_ref"]:
                    target_refs.append(compact["target_ref"])
            for state in page.get("locator_states", []):
                if state.get("state") in ("missing", "unresolved", "terminal_unresolved"):
                    follow_unknown.append({"evidence_ref": ref, "reason": state["state"], "locator": state})
        # Representative read-through is a bounded sample, never a completeness claim.
        by_capability = defaultdict(list)
        selected_targets = set()
        for ref in target_refs:
            target_entry, _ = self.runtime.unref(ref, "read")
            if ref not in seen and ref not in selected_targets:
                by_capability[target_entry["capability_id"]].append(ref)
                selected_targets.add(ref)
        selected = []
        for cid in sorted(by_capability):
            selected.extend(by_capability[cid][:2])
        for ref in selected[:max_follow_reads]:
            target_record = self._record(ref, origin="follow")
            target_record["limitations"] = target_record["limitations"] + [
                "Reached through physical candidate navigation; logical ownership is not proven"]
            records.append(target_record)
            seen.add(ref)
        confirmed = [(r, t) for r in records for t in r["text_observations"]
                     if t["support_status"] == "confirmed_excerpt"]
        candidate_only = [(r, t) for r in records for t in r["text_observations"]
                          if t["support_status"] != "confirmed_excerpt"]
        unknown = list(plan["unknown_before_search"])
        if not records:
            unknown.append("No exact evidence was retrieved in the selected bounded capabilities; this does not prove absence from 7.1 source.")
        if any(x["next_cursor_present"] for x in routes):
            unknown.append("At least one search route has more results than this bounded page.")
        if follow_unknown or len(selected) > max_follow_reads:
            unknown.append("Follow read-through is sampled or candidate navigation is incomplete; no complete relationship claim is permitted.")
        if any(r["text_observations_truncated"] for r in records):
            unknown.append("At least one text candidate list was truncated in the assistant context.")
        if any(r["capability_status"] in PHYSICAL_STATUSES for r in records):
            unknown.append("Physical-only evidence may be cited as a raw observation, not as an interpreted story field.")
        if any(r["relation_scope_status"] == "seeded_unverified_context" for r in records):
            unknown.append("Seeded evidence carries no verified relationship to this question; it remains a candidate.")
        audit = [classify_claim(claim, records) for claim in claims]
        def context_item(record, observation):
            return {"evidence_ref": record["evidence_ref"], "display_label": record["citation_label"],
                    "source_path": record["source_path"], "pointer": record["pointer"],
                    "excerpt": observation["text"], "observation_index": observation["observation_index"],
                    "resolution_state": observation["resolution_state"],
                    "owner_role_status": observation["owner_role_status"],
                    "semantic_owner_mapping": observation["semantic_owner_mapping"],
                    "locale": observation["locale"], "candidate_id": observation["candidate_id"],
                    "text_source_path": observation["source_path"], "text_pointer": observation["pointer"],
                    "relation_scope_status": observation["relation_scope_status"],
                    "limitations": record["limitations"] + observation["limitations"]}
        citations = [{"key": f"[{i}]", "display_label": r["citation_label"],
                      "evidence_ref": r["evidence_ref"], "snapshot": r["snapshot"],
                      "source_path": r["source_path"], "pointer": r["pointer"]}
                     for i, r in enumerate(records, start=1)]
        # Deliberately no plot-summary generator: only exact excerpts are safe by construction.
        snippets = []
        draft_excerpts = []
        for i, record in enumerate(records, start=1):
            direct = next((t for t in record["text_observations"]
                           if t["support_status"] == "confirmed_excerpt"), None)
            if direct:
                snippets.append(f"{record['citation_label']}：「{direct['text'][:120]}」[{i}]")
                draft_excerpts.append({"evidence_ref": record["evidence_ref"],
                                       "observation_index": direct["observation_index"],
                                       "citation_key": f"[{i}]", "excerpt": direct["text"][:120]})
            if len(snippets) == 3:
                break
        answer = ("基于已核对的 7.1 直接文本摘录：" + "；".join(snippets) + "。"
                  if snippets else "当前选定能力中没有足以直接陈述的文本证据。")
        if unknown or candidate_only:
            answer += " 这只是有界证据摘录；候选关系与未证明语义不能据此写成完整剧情结论。"
        result = {"contract": "narrative-assistant-7.1-context-v1", "plan": plan,
                  "invocation": invocation,
                  "routes": routes, "evidence": records, "context": {
                      "confirmed_evidence": [context_item(r, t) for r, t in confirmed],
                      "physical_candidates": [context_item(r, t) for r, t in candidate_only],
                      "confirmed_evidence_refs": list(dict.fromkeys(r["evidence_ref"] for r, _ in confirmed)),
                      "physical_candidate_refs": list(dict.fromkeys(r["evidence_ref"] for r, _ in candidate_only)),
                      "candidate_edges": edges, "unknown": unknown, "follow_unknown": follow_unknown},
                  "citations": citations, "requested_claims": list(claims),
                  "claim_audit": audit, "safe_answer_draft": answer,
                  "draft_excerpts": draft_excerpts,
                  "safety": {"independent_output_audit": "not_run",
                             "unsupported_claims_rejected": sum(x["support_status"] == "unsupported" for x in audit)},
                  "latency_ms": round((time.perf_counter() - start) * 1000, 3),
                  "partial": True, "product_accepted": False, "research_corpus_accepted": False}
        return result

    def ask_task(self, question, *, budget=None):
        """Bounded goal-directed read-through; supplemental refs stay tainted seeds."""
        limits = {"max_search_pages": 2, "max_evidence_reads": 48,
                  "max_follow_calls": 64, "max_payload_bytes": 750_000,
                  "max_elapsed_ms": 12_000, "max_auto_refs": 8,
                  "max_sibling_scan": 32, "max_follow_pages_per_anchor": 2,
                  "max_follow_targets_inspected_per_anchor": 100}
        if budget:
            if not isinstance(budget, dict) or any(k not in limits or type(v) is not int or v < 0
                                                   for k, v in budget.items()):
                raise ValueError("invalid bounded task budget")
            limits.update(budget)
        caps = {"max_search_pages": 4, "max_evidence_reads": 64, "max_follow_calls": 80,
                "max_payload_bytes": 1_000_000, "max_elapsed_ms": 30_000, "max_auto_refs": 8,
                "max_sibling_scan": 128, "max_follow_pages_per_anchor": 4,
                "max_follow_targets_inspected_per_anchor": 200}
        if any(not 1 <= limits[k] <= bound for k, bound in caps.items()):
            raise ValueError("task budget outside bounded limits")
        started = time.perf_counter()
        preliminary = self.plan(question)
        preliminary_planning_ms = (time.perf_counter() - started) * 1000
        route_count = len(preliminary["search_capabilities"])
        if route_count and (limits["max_evidence_reads"] < route_count
                            or limits["max_follow_calls"] < route_count):
            raise ValueError("task call budget too small for one bounded search page")
        per_capability = min(3, max(1, limits["max_evidence_reads"] // max(route_count, 1)),
                             max(1, limits["max_follow_calls"] // max(route_count, 1)))
        metrics = defaultdict(float)
        # Zero means no lower call was started, not a missing measurement.
        for operation in ("search", "read", "inspect", "follow"):
            metrics[operation + "_calls"] = 0
        metrics["planning_ms"] = preliminary_planning_ms
        self._task_metrics = metrics
        self._task_limits = limits
        self._task_deadline_at = started + limits["max_elapsed_ms"] / 1000
        try:
            initial = self.ask(question, per_capability=per_capability, max_follow_reads=0)
            if (time.perf_counter() - started) * 1000 > limits["max_elapsed_ms"]:
                raise TimeoutError("initial bounded retrieval exceeded task deadline")
            if len(json.dumps(initial, ensure_ascii=False).encode()) > limits["max_payload_bytes"]:
                raise ValueError("first bounded search page exceeds task payload budget")
            plan = initial["plan"]
            goal = ("voice" if plan["intent"] == "character" and "语音" in question else
                    "story" if plan["intent"] == "character" else
                    "dialog" if plan["intent"] == "npc" else plan["intent"])
            role_filter = {
                "voice": {"voiceFileTextTextMapHash"},
                "story": {"storyContextTextMapHash"},
                "dialog": {"talkContentTextMapHash"},
                "spoken": {"talkContentTextMapHash", "voiceFileTextTextMapHash"},
                "quest": {"descTextMapHash", "talk_node_text", "talkContentTextMapHash"},
                "book": {"direct_text"},
                # Named event descriptions can contain the narrative itself.
                # Inclusion is a material goal, not owner/relation proof.
                "event": {"descTextMapHash", "contentTextMapHash", "talkContentTextMapHash", "direct_text"},
                "codex": {"DialogNormal", "Narratage", "Aside", "MainQuestDesp"},
                "native": {"textMapContentTextMapHash", "descTextMapHash", "descriptTextMapHash",
                           "requirementDescTextMapHash", "inviteDescTextMapHash",
                           "inviteHostDescTextMapHash", "inviteGuestDescTextMapHash"},
            }.get(goal)
            selected, proofs, inspected, paged_candidate_edges = [], [], set(), []
            deferred_spoken = []

            def room(operation):
                key = "read_calls" if operation == "read" else "follow_calls"
                bound = limits["max_evidence_reads"] if operation == "read" else limits["max_follow_calls"]
                reserve = 0
                if operation == "follow" and selected:
                    reserve = sum("follow" in self.runtime.unref(ref, "read")[0]["supported_operations"]
                                  for ref in [r["evidence_ref"] for r in initial["evidence"]] + selected)
                return metrics[key] + reserve < bound and (time.perf_counter() - started) * 1000 < limits["max_elapsed_ms"]

            def add(ref, proof, *, require_body=True):
                if not ref or ref in inspected:
                    return "SKIPPED_DUPLICATE"
                if len(selected) >= limits["max_auto_refs"]:
                    return "STOP_AUTO_REF_LIMIT"
                if not room("read"):
                    return "STOP_BUDGET_OR_DEADLINE"
                # A probe is not deliverable evidence: reserve the direct rereads
                # and at least one seed reread (or the existing selected set).
                # Inspect and all replay calls remain deadline checked by _call.
                if metrics["read_calls"] + 1 + len(initial["evidence"]) + max(1, len(selected)) > limits["max_evidence_reads"]:
                    return "STOP_REPLAY_RESERVE"
                inspected.add(ref)
                entry, _ = self.runtime.unref(ref, "read")
                content = self._call("read", ref)
                texts, _ = _text_observations(content["data"], {}, contract=content["contract"])
                body = any(t["text"].strip() and t["locale"] == "CHS"
                           and (role_filter is None or t["role"] in role_filter) for t in texts)
                if not require_body or body:
                    selected.append(ref)
                    proofs.append({"evidence_ref": ref, "basis": proof,
                                   "capability_id": entry["capability_id"]})
                    return "ADDED"
                return "SKIPPED_NO_BODY"

            if plan["version_status"] == "supported" and plan["subject"]:
                if goal == "book":
                    for record in initial["evidence"]:
                        if record["capability_id"] != "readable_archive":
                            continue
                        entry, local = self.runtime.unref(record["evidence_ref"], "read")
                        db = self.runtime.adapter(entry).db["readable_archive"]
                        line = db.execute("SELECT file_id,ordinal FROM readable_line WHERE id=?",
                                          (local["text_id"],)).fetchone()
                        if line is None:
                            continue
                        siblings = db.execute(
                            "SELECT id,pointer,ordinal FROM readable_line WHERE file_id=? AND ordinal>? "
                            "ORDER BY ordinal,id LIMIT ?", (line["file_id"], line["ordinal"],
                                                              limits["max_sibling_scan"])).fetchall()
                        for sibling in siblings:
                            next_local = {**local, "text_id": sibling["id"], "pointer": sibling["pointer"]}
                            outcome = add(self.runtime.ref(entry, next_local),
                                          {"kind": "same_physical_file_next_line",
                                           "anchor_ref": record["evidence_ref"],
                                           "physical_ordinal": sibling["ordinal"]})
                            if outcome.startswith("STOP_"):
                                break
                        break
                else:
                    wanted = {"voice": {"character_voice"}, "story": {"character_story"},
                              "dialog": {"narrative_dialog"},
                              "spoken": {"character_voice", "narrative_dialog"}}.get(goal)
                    anchors = [record for record in initial["evidence"]
                               if record["relation_scope_status"] == "direct_occurrence"]
                    if goal == "spoken":
                        anchors.sort(key=lambda record: record["capability_id"] != "character_avatar")
                    spoken_channels = ({"character_voice" if record["capability_id"] == "character_avatar"
                                        else "narrative_dialog" for record in anchors
                                        if record["capability_id"] in ("character_avatar", "narrative_npc")}
                                       if goal == "spoken" else set())
                    # A page marker is expanded only when reached. Its next page is
                    # scheduled *after* sampled targets, so a satisfied goal avoids
                    # launching another follow call.
                    frontier = [(None, record["evidence_ref"], 1, 1, None, []) for record in anchors
                                if "follow" in self.runtime.unref(record["evidence_ref"], "read")[0]["supported_operations"]]
                    per_page = max(1, min(16, limits["max_follow_targets_inspected_per_anchor"]
                                            // limits["max_follow_pages_per_anchor"]))
                    scanned_by_anchor = defaultdict(int)
                    visited = set()
                    while frontier and len(visited) < 200 and len(selected) < limits["max_auto_refs"]:
                        ref, anchor, depth, page_number, cursor, path = frontier.pop(0)
                        if ref is None:
                            if not room("follow") or scanned_by_anchor[anchor] >= limits["max_follow_targets_inspected_per_anchor"]:
                                continue
                            page = self._call("follow", anchor, limit=50,
                                              **({"cursor": cursor} if cursor else {}))
                            paged_candidate_edges.extend({
                                "source_ref": anchor, "target_ref": edge.get("target_ref"),
                                "status": edge.get("status") or edge.get("state") or "physical_candidate",
                                "logical_relation_closure": False,
                                "page_number": page_number, "edge_ordinal": ordinal,
                            } for ordinal, edge in enumerate(page.get("edges", [])))
                            targets = [edge["target_ref"] for edge in page.get("edges", []) if edge.get("target_ref")]
                            take = min(len(targets), limits["max_follow_targets_inspected_per_anchor"]
                                       - scanned_by_anchor[anchor])
                            sampled, remainder = targets[:min(per_page, take)], targets[min(per_page, take):take]
                            scanned_by_anchor[anchor] += take
                            next_cursor = page.get("next_cursor")
                            continuation = ([(None, anchor, depth, page_number + 1, next_cursor, path)]
                                            if next_cursor and page_number < limits["max_follow_pages_per_anchor"] else [])
                            def next_path(target):
                                return path + [{"source_ref": anchor, "target_ref": target,
                                                "page_number": page_number}]
                            frontier[:0] = ([(target, anchor, depth, page_number, None, next_path(target))
                                             for target in sampled]
                                            + continuation
                                            + [(target, anchor, depth, page_number, None, next_path(target))
                                               for target in remainder])
                            continue
                        if ref in visited:
                            continue
                        visited.add(ref)
                        entry, _ = self.runtime.unref(ref, "read")
                        cid = entry["capability_id"]
                        if wanted is None or cid in wanted:
                            channel_count = sum(self.runtime.unref(chosen, "read")[0]["capability_id"] == cid
                                                for chosen in selected)
                            proof = {"kind": "physical_candidate_path", "anchor_ref": anchor,
                                     "depth": depth, "page_number": page_number, "path": path}
                            if goal == "spoken" and len(spoken_channels) == 2 and channel_count >= 2:
                                deferred_spoken.append((ref, proof))
                            else:
                                add(ref, proof)
                        if goal in ("quest", "codex") and depth < 3 and "follow" in entry["supported_operations"] and room("follow"):
                            next_marker = (None, ref, depth + 1, 1, None, path)
                            if cid == "quest_talk_physical" or goal == "codex":
                                frontier.insert(0, next_marker)
                            else:
                                frontier.append(next_marker)
                        if wanted is not None and len(selected) >= 4:
                            break
                    # Balance channels first. If the bounded scan found less than
                    # four usable bodies, release the unused quota to the other
                    # channel without claiming the empty channel lacks content.
                    if goal == "spoken" and len(selected) < 4:
                        for ref, proof in deferred_spoken:
                            outcome = add(ref, proof)
                            if outcome.startswith("STOP_") or len(selected) >= 4:
                                break
                if not selected and limits["max_search_pages"] > 1:
                    for cid in plan["search_capabilities"]:
                        page = self._call("search", plan["subject"], cid, limit=per_capability)
                        for page_number in range(2, limits["max_search_pages"] + 1):
                            if not page.get("next_cursor") or not room("read"):
                                break
                            page = self._call("search", plan["subject"], cid,
                                              limit=per_capability, cursor=page["next_cursor"])
                            for hit in page["results"]:
                                add(hit["evidence_ref"], {"kind": "bounded_search_page",
                                                          "capability_id": cid, "page_number": page_number})
                            if selected:
                                break
                        if selected:
                            break
            # Reuse the v3-compatible context builder. Auto-selected refs are
            # explicitly seeded; neither candidate nor same-file order is a
            # logical relationship or a confirmed narrative claim.
            direct_count = len(initial["evidence"])
            replay_follows = sum(
                "follow" in self.runtime.entries[r["capability_id"]]["supported_operations"]
                for r in initial["evidence"])
            # The final ask must re-read and inspect every delivered seed and
            # re-read the direct hits. Trim only the tail that cannot be fully
            # verified; never discard an already feasible nonempty prefix.
            while selected and (metrics["read_calls"] + direct_count + len(selected)
                                > limits["max_evidence_reads"] or
                                metrics["follow_calls"] + replay_follows + sum(
                                    "follow" in self.runtime.unref(ref, "read")[0]["supported_operations"]
                                    for ref in selected) > limits["max_follow_calls"]):
                selected.pop()
                proofs.pop()
            if not selected:
                result = initial
            else:
                result = self.ask(question, seed_refs=selected, per_capability=per_capability, max_follow_reads=0)
            if len(json.dumps(result, ensure_ascii=False).encode()) > limits["max_payload_bytes"]:
                raise ValueError("bounded task result exceeds payload budget after verified replay")
            context_started = time.perf_counter()
            material = []
            citation_keys = {x["evidence_ref"]: x["key"] for x in result["citations"]}
            for record in result["evidence"]:
                for item in record["text_observations"]:
                    if (not item["text"].strip() or item["locale"] != "CHS"
                            or role_filter is not None and item["role"] not in role_filter
                            or goal == "book" and (record["capability_id"] != "readable_archive"
                                                      or record["pointer"] == "/line/0")):
                        continue
                    material.append({"evidence_ref": record["evidence_ref"], "capability_id": record["capability_id"],
                                     "role": item["role"], "excerpt": item["text"],
                                     "proof_status": item["proof_status"], "citation_key": citation_keys[record["evidence_ref"]],
                                     "source_path": record["source_path"], "pointer": record["pointer"],
                                     "observation_index": item["observation_index"]})
            distinct = len({x["evidence_ref"] for x in material})
            secondary = (goal != "quest" or
                         (any(x["capability_id"] == "quest_talk_physical" for x in material) and
                          any(x["capability_id"] == "quest_narrative_archive" for x in material)))
            if goal == "codex":
                secondary = any(x["pointer"].count("/") >= 4 for x in material)
            minimum = {"voice": 3, "story": 3, "dialog": 3, "book": 4,
                       "spoken": 3, "quest": 2, "event": 2, "codex": 2, "native": 2}.get(goal, 1)
            task_status = ("UNSUPPORTED" if plan["version_status"] != "supported" else
                           "NO_EVIDENCE" if not result["evidence"] else
                           "SAFE_BUT_INCOMPLETE" if not material else
                           "COMPLETE" if distinct >= minimum and secondary else "PARTIAL_USEFUL")
            incompletion_reason = ("No goal-matching CHS body was found in the bounded sampled evidence."
                                   if task_status == "SAFE_BUT_INCOMPLETE" else None)
            candidate_excerpts = [x for x in material if x["proof_status"] != "confirmed_excerpt"]
            if goal == "codex":
                section = next((x for x in candidate_excerpts if x["pointer"].count("/") == 2), None)
                nodes = [x for x in candidate_excerpts if x["pointer"].count("/") >= 4]
                candidate_excerpts = ([section] if section else []) + nodes
            if goal == "quest":
                # Reserve display capacity for both physical Quest and Talk material.
                talk = next((x for x in candidate_excerpts if x["capability_id"] == "quest_talk_physical"), None)
                if talk:
                    candidate_excerpts = [talk] + [x for x in candidate_excerpts if x is not talk]
            result["task"] = {"contract": "narrative-assistant-7.1-task-retrieval-v1",
                              "goal": goal, "task_status": task_status,
                              "incompletion_reason": incompletion_reason,
                              "task_status_scope": "bounded_material_goal_only",
                              "corpus_or_story_complete": False,
                              "evidence_goal": {"minimum_distinct_body_refs": minimum,
                                                "quest_text_and_talk_text_required_for_complete": goal == "quest",
                                                "codex_node_text_required_for_complete": goal == "codex"},
                              "retrieval_summary": {"body_observations": len(material),
                                                    "distinct_body_refs": distinct,
                                                    "auto_selected_refs": len(selected)},
                              "answer_context": {"confirmed_excerpts": [x for x in material
                                                                           if x["proof_status"] == "confirmed_excerpt"][:3],
                                                 "candidate_excerpts": candidate_excerpts[:5],
                                                 "unknown": result["context"]["unknown"]},
                              "auto_seed_proofs": proofs, "budget": limits,
                              "paged_candidate_edges": paged_candidate_edges,
                              "metrics": {**{key: round(value, 3) if key.endswith("_ms") else int(value)
                                              for key, value in metrics.items()},
                                          "context_build_ms": round((time.perf_counter() - context_started) * 1000, 3),
                                          "total_ms": round((time.perf_counter() - started) * 1000, 3)}}
            while True:
                actual_bytes = len(json.dumps(result, ensure_ascii=False).encode())
                if result["task"]["metrics"].get("payload_bytes") == actual_bytes:
                    break
                result["task"]["metrics"]["payload_bytes"] = actual_bytes
            if result["task"]["metrics"]["payload_bytes"] > limits["max_payload_bytes"]:
                raise ValueError("bounded task result exceeds payload budget")
            if (time.perf_counter() - started) * 1000 > limits["max_elapsed_ms"]:
                raise TimeoutError("bounded task retrieval exceeded task deadline")
            return result
        finally:
            self._task_metrics = None
            self._task_limits = None
            self._task_deadline_at = None
