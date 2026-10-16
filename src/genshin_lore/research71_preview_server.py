"""Opt-in MCP sidecar over existing bounded retrieval, not a Research corpus."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from time import monotonic
from typing import Any
import uuid
import subprocess

import anyio
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from .mcp_transport import _avoid_broken_anyio_worker_threads, _run_pipe_stdio
from .narrative_assistant_7_1 import NarrativeAssistant71
from .research_7_1_bounded import SNAPSHOT

PREVIEW_TOOL = "query_research71_preview"
INSPECT_TOOL = "inspect_research71_preview"
PAYLOAD_LIMIT = 131_072
INSTRUCTIONS = (
    "Explicit 7.1 questions use query_research71_preview; 7.0 or unspecified version stays on "
    "the separate accepted 7.0 Research server. Comparisons require separate version sources. "
    "This is fixed-snapshot, partial, bounded material retrieval, not an accepted 7.1 product/corpus. "
    "Answer from task.answer_context: confirmed excerpts and physical candidates are separate; "
    "candidate ownership/relations/order are not proven. COMPLETE is a material quota, not all "
    "voices/dialogue or complete story. Preserve unknown and cite this call's exact evidence_ref "
    "and observation_index, using supplied display labels. Inspect citations with inspect_research71_preview."
)
READONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
FULLTEXT_CAPABILITIES = {"character_story", "readable_archive"}
TEXT_PAGE_LIMIT = 2000


def text_view(observation, start, chars):
    """Unicode-code-point view of ONE original observation; never a new occurrence."""
    if type(start) is not int or type(chars) is not int or start < 0 or not 1 <= chars <= TEXT_PAGE_LIMIT:
        raise ValueError("text_start must be nonnegative; text_chars must be 1..2000")
    full = observation["text"]
    if start > len(full):
        raise ValueError("text_start outside original observation")
    end = min(len(full), start + chars)
    text = full[start:end]
    position = {"start": start, "end": end, "total_chars": len(full), "offset_unit": "Unicode code points",
                "has_more": end < len(full), "end_reached": end == len(full),
                "complete_original_in_this_view": start == 0 and end == len(full)}
    return {**observation, "text": text, "truncated": not position["complete_original_in_this_view"],
            "text_range": position, "segment_sha256": hashlib.sha256(text.encode()).hexdigest()}


def version_route(question: str, default_version: str = '7.0') -> str:
    """Select source only; this is not a second capability planner."""
    if not isinstance(question, str) or not question.strip() or len(question) > 1000:
        raise ValueError("question must be nonempty and at most 1000 characters")
    versions = set(re.findall(r"(?<!\d)(\d+\.\d+)(?!\d)", question))
    if len(versions) > 1:
        return "separate_version_sources_required"
    if not versions:
        versions = {default_version}
    if versions == {"7.0"}:
        return "accepted_7_0"
    return "preview_7_1" if versions == {"7.1"} else "unsupported_version"


def retrieval_question(question: str) -> str:
    # Supply syntactic book delimiters only, never a different title or seed.
    # The transformation is returned for audit; all other questions are unchanged.
    match = re.fullmatch(r"(7\.1\s*)([^《》\n]*书籍[^《》\n]*?)讲什么[？?]?", question.strip())
    return f"{match[1]}这本《{match[2]}》讲什么？" if match else question


def provenance(record: dict) -> dict:
    result = {key: record[key] for key in (
        "evidence_ref", "snapshot", "capability_id", "family", "contract", "parser", "run",
        "source_path", "pointer", "occurrence", "artifact", "citation_label", "source_context",
        "capability_status", "limitations",
    )}
    result.update({key:record.get(key) for key in ('source_structure','backend','corpus_ref')})
    return result


def compact_task(result: dict, original_question: str, request_id: str) -> dict:
    """Lossless proof/identity handoff for displayed excerpts, not a raw JSON dump."""
    records = {record["evidence_ref"]: record for record in result["evidence"]}
    context = {"unknown": list(result["task"]["answer_context"]["unknown"])}
    used = set()
    for section in ("confirmed_excerpts", "candidate_excerpts"):
        context[section] = []
        for excerpt in result["task"]["answer_context"][section]:
            record = records[excerpt["evidence_ref"]]
            observation = record["text_observations"][excerpt["observation_index"]]
            if excerpt["capability_id"] != record["capability_id"] or excerpt["role"] != observation["role"]:
                raise ValueError("task excerpt binding divergence")
            text = excerpt["excerpt"]
            context[section].append({**excerpt, "excerpt": text[:4096],
                                    "excerpt_truncated": observation["truncated"] or len(text) > 4096,
                                    "full_text_sha256": observation["text_sha256"],
                                    "text_length": observation.get("text_length"),
                                    "excerpt_sha256": hashlib.sha256(text[:4096].encode()).hexdigest(),
                                    "formal_text_evidence_ref": observation.get('formal_evidence_ref'),
                                    "text_evidence_identity": observation.get('evidence_identity'),
                                    "text_proof": {k: observation[k] for k in (
                                        "locale", "resolution_state", "owner_role_status", "semantic_owner_mapping",
                                        "field_continuity_status", "candidate_count", "selected_candidate_count",
                                        "selected", "candidate_id", "source_path", "pointer", "pair_ordinal",
                                        "origin", "relation_scope_status", "limitations")}})
            used.add(excerpt["evidence_ref"])
    citations = [{**citation, **provenance(records[citation["evidence_ref"]]), "request_id": request_id}
                 for citation in result["citations"] if citation["evidence_ref"] in used]
    for citation in citations:
        record = records[citation["evidence_ref"]]
        if record["family"] == "event_narrative_archive":
            # Show observed titles beside the used body, without resolving the
            # source-context label or upgrading any per-text proof. Each title
            # can be inspected with this exact ref and its observation index.
            citation["same_container_title_observations"] = [dict(item)
                for item in record["text_observations"]
                if item["role"] in ("titleTextMapHash", "markTitleTextMapHash")
                and item["locale"] == "CHS"]
    return {"status": "ok", "contract": "research71-agent-preview-v1", "request_id": request_id,
            "question": original_question, "retrieval_question": result["plan"]["question"],
            "snapshot": SNAPSHOT, "product_accepted": False, "research_corpus_accepted": False,
            "partial": True, "scope": "bounded_material_retrieval_only", "plan": result["plan"],
            "task": {key: result["task"][key] for key in (
                "goal", "task_status", "task_status_scope", "corpus_or_story_complete",
                "evidence_goal", "retrieval_summary", "incompletion_reason", "metrics")},
            "citations": citations, "lower_evidence_returned": False,
            "audit_path": INSPECT_TOOL, "excerpt_limit_chars": 4096,
            "task_answer_context_source": "task.answer_context", "_context": context}


def create_server(assistant: NarrativeAssistant71, *, semantic_discovery_supported=False) -> MCPServer:
    handoff = getattr(getattr(assistant.runtime, 'query', None), 'citation_handoff', None)
    released = getattr(assistant.runtime, 'released', False)
    instructions = INSTRUCTIONS if not released else (
        'Default and explicit 7.1 use this published bounded 7.1 source. Explicit 7.0 uses the '
        'separate retained accepted 7.0 server; comparisons require both sources. '
        'Consume task.answer_context, preserve physical-candidate/unknown restrictions and source uncertainty. '
        'COMPLETE is a quota, never full story/corpus. Inspect final refs and ranges. '
        'Eight historical contract differences remain; no first-release proof. '
        + ('Semantic discovery is available through the separate five-tool surface, never evidence or identity authority. ' if semantic_discovery_supported else 'No 7.1 semantic search. ')
        +
        'Old tool names and frozen evidence namespaces are retained for compatibility, not a legacy fallback.'
    )
    if handoff:
        instructions = (('Published bounded source: ' if released else 'Opt-in unified candidate: ') +
                        'use supplied [H...:R...:E...] citation handles with THIS server, including inspect evidence_ref. '
                        'Final claims select handles, not copied long refs; the explicit project delivery entry expands them after the completed turn. '
                        'Preserve candidate, actual range and non-exhaustive limits. ') + instructions
    server = MCPServer(name="genshin-lore-research71-preview", version="0.3.0" if released else "0.2.0", instructions=instructions)
    # Process-local, server-issued navigation locators only, never cached text.
    # Restart requires a new query; no latest/default book or guessed ref.
    book_cursors = {}
    text_cursors = {}

    def add_text_continuations(compact, records, question):
        displayed = {(x["evidence_ref"], x["observation_index"]): len(x["excerpt"])
                     for section in ("confirmed_excerpts", "candidate_excerpts")
                     for x in compact["task"]["answer_context"][section]}
        compact["text_continuations"] = []
        for record in records:
            for obs in record["text_observations"]:
                if record["capability_id"] not in FULLTEXT_CAPABILITIES or not obs["truncated"]:
                    continue
                index = obs["observation_index"]
                start = displayed.get((record["evidence_ref"], index), 0)
                handle = uuid.uuid4().hex
                # Save identity/position/seal, not the body. Each page requalifies the source.
                text_cursors[handle] = (question, record["evidence_ref"], index,
                                        start, obs["text_sha256"], obs["text_length"])
                compact["text_continuations"].append({"evidence_ref": record["evidence_ref"],
                    "observation_index": index, "role": obs["role"],
                    "citation_label": record["citation_label"],
                    "initial_range": {"start": 0, "end": start},
                    "total_chars": obs["text_length"], "full_text_sha256": obs["text_sha256"],
                    "continuation": handle})

    def text_page(handle, chars):
        if type(chars) is not int or not 1 <= chars <= TEXT_PAGE_LIMIT:
            raise ValueError("text_chars must be 1..2000")
        question, ref, index, start, expected_sha, expected_length = text_cursors[handle]
        entry, _ = assistant.runtime.unref(ref, "read")
        if entry["capability_id"] not in FULLTEXT_CAPABILITIES:
            raise ValueError("long text is only available for Story and Readable observations")
        started = monotonic()
        record = assistant._record(ref, origin="seed", text_limit=None)
        if monotonic() - started >= 20:
            raise TimeoutError("text page exceeded deadline during verified read")
        obs = record["text_observations"][index]
        if (obs["text_sha256"], obs["text_length"]) != (expected_sha, expected_length):
            raise ValueError("original observation seal changed")
        view = text_view(obs, start, chars)
        position = view["text_range"]
        record["text_observations"] = [dict(item) for item in record["text_observations"]]
        record["text_observations"][index] = view
        excerpt = {"evidence_ref": ref, "capability_id": record["capability_id"], "role": obs["role"],
                   "observation_index": index, "excerpt": view["text"], "proof_status": obs["proof_status"],
                   "citation_key": "[1]", "source_path": record["source_path"], "pointer": record["pointer"]}
        context = {"confirmed_excerpts": [], "candidate_excerpts": [excerpt],
                   "unknown": ["Original observation identity is unchanged; this is a character-range view, not a new occurrence.",
                               "EOF covers this one text observation, not all stories, volumes or the corpus.",
                               "Continuation does not establish ownership or relation to the question."]}
        result = {"evidence": [record], "citations": [{"key": "[1]", "evidence_ref": ref}],
                  "plan": {"question": question}, "task": {"answer_context": context,
                  "goal": "text_continuation", "task_status": "PARTIAL_USEFUL", "task_status_scope": "one bounded original-text view",
                  "corpus_or_story_complete": False, "evidence_goal": "next characters of the same text observation",
                  "retrieval_summary": {"characters": len(view["text"])}, "incompletion_reason": None,
                  "metrics": {"read_calls": 1, "inspect_calls": 1}}}
        compact = compact_task(result, question, uuid.uuid4().hex)
        compact["task"]["answer_context"] = compact.pop("_context")
        compact["task"]["answer_context"]["candidate_excerpts"][0]["text_range"] = position
        next_handle = None
        if position["has_more"]:
            next_handle = uuid.uuid4().hex
            text_cursors[next_handle] = (question, ref, index, position["end"], expected_sha, expected_length)
        compact["text_page"] = {**position, "evidence_ref": ref, "observation_index": index,
                                "full_text_sha256": expected_sha, "segment_sha256": view["segment_sha256"],
                                "continuation": next_handle}
        return compact

    def issue_book_cursor(question, anchor_ref, after):
        handle = uuid.uuid4().hex
        book_cursors[handle] = (question, anchor_ref, after)
        return handle

    def book_position(anchor_ref):
        entry, local = assistant.runtime.unref(anchor_ref, "read")
        if entry["capability_id"] != "readable_archive" or entry["route"] != "source_context":
            raise ValueError("continuation requires exact Readable evidence")
        adapter = assistant.runtime.adapter(entry)
        adapter.read(local)  # Existing exact source/run/coordinate checks.
        row, _ = adapter._readable(local["text_id"])
        return entry, adapter, row

    def book_page(handle, page_size):
        if handle not in book_cursors:
            raise ValueError("unknown continuation; query again after server restart")
        if type(page_size) is not int or not 1 <= page_size <= 12:
            raise ValueError("page_size must be 1..12 physical lines")
        question, anchor_ref, after = book_cursors[handle]
        started = monotonic()
        entry, adapter, anchor = book_position(anchor_ref)
        db = adapter.db["readable_archive"]
        rows = db.execute(
            "SELECT id,ordinal,pointer FROM readable_line WHERE file_id=? "
            "AND (ordinal>? OR (ordinal=? AND id>?)) ORDER BY ordinal,id LIMIT ?",
            (anchor["file_id"], after[0] if after else -1,
             after[0] if after else -1, after[1] if after else "", page_size + 1),
        ).fetchall()
        total = db.execute("SELECT count(*) FROM readable_line WHERE file_id=?", (anchor["file_id"],)).fetchone()[0]
        records, material, citations, covered = [], [], [], []
        verified_reads = 0
        stopped = None
        for row in rows[:page_size]:
            if monotonic() - started >= 20:
                stopped = "page_deadline"  # Do not start another read after expiry.
                break
            local = {"snapshot_id": SNAPSHOT, "family": "readable_archive", "run_id": entry["run"],
                     "text_id": row["id"], "source_path": anchor["source_path"], "pointer": row["pointer"]}
            ref = assistant.runtime.ref(entry, local)
            record = assistant._record(ref, origin="seed")  # read + inspect; proof policy unchanged
            verified_reads += 1
            if monotonic() - started >= 20:
                raise TimeoutError("book page exceeded deadline during verified read")
            key = f"[{len(records) + 1}]"
            items = [{"evidence_ref": ref, "capability_id": record["capability_id"], "role": obs["role"],
                      "excerpt": obs["text"], "proof_status": obs["proof_status"], "citation_key": key,
                      "source_path": record["source_path"], "pointer": record["pointer"],
                      "observation_index": obs["observation_index"]} for obs in record["text_observations"]]
            # Reserve space conservatively before accepting the next physical line.
            estimate = len(json.dumps([records + [record], material + items], ensure_ascii=False).encode())
            if estimate > PAYLOAD_LIMIT // 2:
                stopped = "page_payload_budget"
                break
            records.append(record)
            material.extend(items)
            citations.append({"key": key, "evidence_ref": ref})
            covered.append({"ordinal": row["ordinal"], "pointer": row["pointer"], "evidence_ref": ref})
        if rows and not covered:
            raise ValueError("first physical line exceeds page budget; no position advanced")
        next_after = (rows[len(covered)-1]["ordinal"], rows[len(covered)-1]["id"]) if covered else after
        has_more = len(rows) > len(covered)
        context = {"confirmed_excerpts": [x for x in material if x["proof_status"] == "confirmed_excerpt"],
                   "candidate_excerpts": [x for x in material if x["proof_status"] != "confirmed_excerpt"],
                   "unknown": ["Same-file physical continuation; seed relation remains unproven.",
                               "EOF covers this physical file only, not other volumes or the corpus."]}
        result = {"evidence": records, "citations": citations, "plan": {"question": question},
                  "task": {"answer_context": context, "goal": "book_continuation", "task_status": "PARTIAL_USEFUL" if material else "SAFE_BUT_INCOMPLETE",
                           "task_status_scope": "one bounded physical-file page", "corpus_or_story_complete": False,
                           "evidence_goal": "read next physical lines", "retrieval_summary": {"physical_lines": len(covered)},
                           "incompletion_reason": stopped, "metrics": {"read_calls": verified_reads, "inspect_calls": verified_reads,
                               "count_scope": "page line read/inspect calls, including a budget-rejected line; anchor source qualification separate"}}}
        compact = compact_task(result, question, uuid.uuid4().hex)
        compact["task"]["answer_context"] = compact.pop("_context")
        add_text_continuations(compact, records, question)
        compact["book_page"] = {"source_path": anchor["source_path"], "total_physical_lines": total,
                                "covered": covered, "has_more": has_more, "file_end_reached": not has_more,
                                "page_budget_reason": stopped, "text_truncated": any(
                                    x["excerpt_truncated"] for section in ("confirmed_excerpts", "candidate_excerpts")
                                    for x in compact["task"]["answer_context"][section]),
                                "continuation": issue_book_cursor(question, anchor_ref, next_after) if has_more else None}
        return compact

    def bounded(payload):
        if released:
            payload.update(product_accepted=True, research_corpus_accepted=True,
                           full_research_7_1_accepted=False, release_id=assistant.runtime.query.release_id,
                           release_status='accepted_bounded', backend='unified_release',
                           historical_contract_gaps=8, historical_component_gaps=12,
                           semantic_discovery_7_1_supported=semantic_discovery_supported,
                           evidence_namespace_preserved=True)
        if handoff and payload.get('status') == 'ok':
            payload = handoff.attach(payload, 'inspect' if 'observation' in payload else 'query',
                                     inspected_ref=payload.get('provenance', {}).get('evidence_ref'))
        if len(json.dumps(payload, ensure_ascii=False).encode()) >= PAYLOAD_LIMIT:
            return {"status": "response_too_large", "partial": True, "product_accepted": False,
                    "research_corpus_accepted": False, "snapshot": SNAPSHOT,
                    "error": "Preview payload exceeds cap; no unverified or silently relabeled material returned."}
        return payload

    @server.tool(name=PREVIEW_TOOL, annotations=READONLY,
                 description=("Default and explicit 7.1: published bounded Research; eight historical contract gaps remain. " if released else
                              "Explicit 7.1 only: partial bounded narrative material from accepted standalone capabilities. ") +
                             "Consume task.answer_context and supplied citations; COMPLETE is NOT full story/all dialogue. "
                             "For Book, initial excerpts are samples: use returned book_continuations[].continuation "
                             "with this same tool to read bounded sequential pages from the file start; then book_page.continuation "
                             "until file_end_reached or your budget. Never guess paths/refs. page_size=1..12. "
                             "Truncated Story/long Book lines expose text_continuations: pass their continuation to this tool "
                             "with text_chars=1..2000 (default 800); text_page gives original character range and EOF. "
                             "Book file EOF does not mean truncated lines were fully read. "
                             "Candidate excerpts do not prove ownership/order. Explicit 7.0 uses separate formal tools.")
    def query_research71_preview(question: str = "", continuation: str | None = None, page_size: int = 12,
                                 text_chars: int = 800) -> dict[str, Any]:
        try:
            if continuation is not None:
                scope = text_cursors.get(continuation) or book_cursors.get(continuation)
                if scope is None:
                    raise ValueError("unknown continuation; query again after server restart")
                if question and question != scope[0]:
                    raise ValueError("continuation question scope differs from original query")
                if continuation in text_cursors:
                    return bounded(text_page(continuation, text_chars))
                return bounded(book_page(continuation, page_size))
            route = version_route(question, '7.1' if released else '7.0')
            if route != "preview_7_1":
                return bounded({"status": "route_required", "route": route, "question": question,
                        "partial": True, "product_accepted": False, "research_corpus_accepted": False,
                        "retrieval_executed": False})
            result = assistant.ask_task(retrieval_question(question))
            compact = compact_task(result, question, uuid.uuid4().hex)
            compact["task"]["answer_context"] = compact.pop("_context")
            add_text_continuations(compact, result["evidence"], question)
            compact["book_continuations"] = []
            files = set()
            for citation in compact["citations"]:
                if citation["capability_id"] != "readable_archive":
                    continue
                _, _, row = book_position(citation["evidence_ref"])
                if row["file_id"] in files:
                    continue
                files.add(row["file_id"])
                compact["book_continuations"].append({"source_path": row["source_path"],
                    "citation_label": citation["citation_label"], "starts_at": "physical_file_start",
                    "initial_excerpts_are_sample_not_sequential_coverage": True,
                    "continuation": issue_book_cursor(question, citation["evidence_ref"], None)})
            return bounded(compact)
        except (ValueError, TimeoutError) as exc:
            return bounded({"status": "unavailable", "snapshot": SNAPSHOT, "partial": True,
                    "product_accepted": False, "research_corpus_accepted": False, "error": str(exc)})

    @server.tool(name=INSPECT_TOOL, annotations=READONLY,
                 description="Reread ONE exact research71 physical occurrence and text observation. "
                             "Returns provenance/proof; optional text_start/text_chars reread a bounded original character range "
                             "(0-based Unicode code points, max 2000). No relation proof or new occurrence.")
    def inspect_research71_preview(evidence_ref: str, observation_index: int = 0,
                                   text_start: int = 0, text_chars: int | None = None) -> dict[str, Any]:
        try:
            if handoff and evidence_ref.startswith('[H'):
                binding = handoff.resolve(evidence_ref)
                evidence_ref = binding['evidence_ref']
                if binding['observation_index'] is not None:
                    if observation_index not in (0, binding['observation_index']):
                        raise ValueError('handle observation conflicts with request')
                    observation_index = binding['observation_index']
                if binding['range'] and text_chars is None and text_start == 0:
                    start, end = binding['range']['start'], binding['range']['end']
                    # Ordinary excerpts keep legacy inspection. Explicit character
                    # views retain their supplied range, including a page at zero.
                    if start > 0 or 'text_range' in binding['source_result']:
                        text_start, text_chars = start, end - start
                elif binding['range'] and (text_chars is not None or text_start != 0):
                    if (text_start, text_start + (800 if text_chars is None else text_chars)) != (binding['range']['start'], binding['range']['end']):
                        raise ValueError('handle range conflicts with request; use original ref for a different view')
            if type(observation_index) is not int:
                raise ValueError("observation_index must be an integer")
            range_requested = text_chars is not None or text_start != 0
            if range_requested:
                if type(text_start) is not int or text_start < 0 or (text_chars is not None and
                        (type(text_chars) is not int or not 1 <= text_chars <= TEXT_PAGE_LIMIT)):
                    raise ValueError("text_start must be nonnegative; text_chars must be 1..2000")
                entry, _ = assistant.runtime.unref(evidence_ref, "read")
                if entry["capability_id"] not in FULLTEXT_CAPABILITIES:
                    raise ValueError("long text is only available for Story and Readable observations")
            record = assistant._record(evidence_ref, origin="seed", text_limit=None if range_requested else 400)
            if not 0 <= observation_index < len(record["text_observations"]):
                raise ValueError("observation_index outside exact physical occurrence")
            observation = record["text_observations"][observation_index]
            if range_requested:
                observation = text_view(observation, text_start, 800 if text_chars is None else text_chars)
            return bounded({"status": "ok", "snapshot": SNAPSHOT, "partial": True,
                            "product_accepted": False, "research_corpus_accepted": False,
                            "single_physical_container": True, "provenance": provenance(record),
                            "observation_index": observation_index,
                            "observation": {**observation, "text": observation["text"][:4096]},
                            "text_truncated": observation["truncated"] or len(observation["text"]) > 4096,
                            "relation_to_question": "not_proven_by_inspection"})
        except (ValueError, KeyError) as exc:
            return bounded({"status": "invalid_request", "partial": True, "error": str(exc)})
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description="Opt-in bounded 7.1 MCP preview sidecar, not current Research")
    parser.add_argument("--source", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--preview", action="store_true",
                        help="explicit acknowledgement of non-product, partial preview")
    modes.add_argument('--release', action='store_true', help='published bounded 7.1; exact release qualification required')
    parser.add_argument('--unified', action='store_true', help='use qualified unified candidate; no legacy fallback')
    parser.add_argument('--corpus', type=Path)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--bindings', type=Path)
    parser.add_argument('--index', type=Path)
    args = parser.parse_args(argv)
    runtime = None
    if args.unified or args.release:
        if not all((args.corpus, args.profile, args.bindings, args.index)):
            parser.error('unified mode requires corpus/profile/bindings/index; no fallback')
        identity=subprocess.check_output(['git','-C',str(args.source),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()
        if identity!=['9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99','aa578c08c402af39b41f1ffc95124d10f5410272'] or subprocess.check_output(['git','-C',str(args.source),'status','--porcelain=v1'],text=True).strip():
            parser.error('unified preview requires clean pinned 7.1 source; no fallback')
        from .research71_unified import Research71Unified
        runtime = Research71Unified(corpus=args.corpus, profile=args.profile, bindings=args.bindings, index=args.index, released=args.release)
    with NarrativeAssistant71(args.source, runtime=runtime) as assistant:
        server = create_server(assistant)
        _avoid_broken_anyio_worker_threads(server)
        anyio.run(_run_pipe_stdio, server)


if __name__ == "__main__":
    main()
