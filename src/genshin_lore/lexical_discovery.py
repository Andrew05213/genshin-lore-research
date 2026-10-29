"""Deterministic, evidence-backed lexical candidate discovery.

Lexical surfaces are an index over physical accepted evidence occurrences.  They
are not entities, aliases, relations, or evidence truth.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path

from .research_refs import encode_ref


LEXICON_CONTRACT = "research-source-authored-lexicon-v1"
NORMALIZATION_VERSION = "unicode-exact-case-sensitive-v1"
EXTRACTION_VERSION = "accepted-exact-span-then-cjk-2-4gram-v1"
STOP_POLICY_VERSION = "zh-low-information-v1"
LOW_INFORMATION = frozenset({
    "相关", "任务", "故事", "关于", "内容", "请问", "寻找", "查找", "资料",
    "什么", "一个", "一种", "这个", "那个", "以及", "还有", "其中", "如何",
    "为何", "为什么", "的", "了", "与", "和", "及", "在", "是", "被", "让",
})
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_LEXICAL_FIELD_MARKERS = ("name", "title", "speaker")


def is_source_authored_lexical_field(owner_field: str | None) -> bool:
    field = (owner_field or "").casefold()
    return any(marker in field for marker in _LEXICAL_FIELD_MARKERS)


def text_ref(row: sqlite3.Row, corpus_ref: str) -> str:
    return encode_ref({
        "v": 2, "kind": "text", "corpus": corpus_ref,
        "source": row["source_key"], "family": row["evidence_family"],
        "contract": row["evidence_contract"], "parser": row["evidence_parser"],
        "run": row["evidence_run"], "occurrence": row["occurrence_id"],
        "child": row["child_id"], "index": row["id"],
    })


def build_lexicon(index: Path, output: Path, corpus_ref: str,
                  research_index_sha256: str) -> dict[str, object]:
    if output.exists():
        raise FileExistsError(f"no-clobber: {output}")
    stage = output.with_name(f".{output.name}.staging")
    if stage.exists():
        raise FileExistsError(f"staging exists: {stage}")
    source = sqlite3.connect(index.resolve().as_uri()+"?mode=ro&immutable=1", uri=True)
    source.row_factory = sqlite3.Row
    target = sqlite3.connect(stage)
    try:
        target.executescript("""
        pragma journal_mode=delete;
        pragma synchronous=full;
        create table lexical_release(
          id integer primary key check(id=1), contract text not null,
          corpus_ref text not null, research_index_sha256 text not null,
          normalization_version text not null, extraction_version text not null,
          stop_policy_version text not null, surface_count integer not null,
          physical_occurrence_count integer not null
        );
        create table lexical_surface(
          surface_form text primary key, locale text not null,
          normalization_version text not null, physical_occurrence_count integer not null
        ) without rowid;
        create table lexical_occurrence(
          id integer primary key, surface_form text not null,
          research_text_id integer not null, evidence_ref text not null unique,
          source_field text not null, family text not null, locale text not null,
          foreign key(surface_form) references lexical_surface(surface_form)
        );
        create index lexical_occurrence_surface on lexical_occurrence(surface_form,id);
        create index lexical_occurrence_text on lexical_occurrence(research_text_id);
        """)
        rows = []
        counts: dict[str, int] = {}
        query = ("select * from research_text where locale='CHS' and length(raw_text) between 2 and 64 "
                 "and (lower(coalesce(owner_field,'')) like '%name%' "
                 "or lower(coalesce(owner_field,'')) like '%title%' "
                 "or lower(coalesce(owner_field,'')) like '%speaker%') order by id")
        for row in source.execute(query):
            surface = row["raw_text"]
            if surface in LOW_INFORMATION or not is_source_authored_lexical_field(row["owner_field"]):
                continue
            counts[surface] = counts.get(surface, 0) + 1
            rows.append((surface, row["id"], text_ref(row, corpus_ref), row["owner_field"],
                         row["evidence_family"], row["locale"]))
        target.executemany("insert into lexical_surface values(?,?,?,?)", [
            (surface, "CHS", NORMALIZATION_VERSION, count)
            for surface, count in sorted(counts.items())])
        target.executemany(
            "insert into lexical_occurrence(surface_form,research_text_id,evidence_ref,source_field,family,locale) "
            "values(?,?,?,?,?,?)", rows)
        target.execute("insert into lexical_release values(1,?,?,?,?,?,?,?,?)", (
            LEXICON_CONTRACT, corpus_ref, research_index_sha256, NORMALIZATION_VERSION,
            EXTRACTION_VERSION, STOP_POLICY_VERSION, len(counts), len(rows)))
        target.commit()
        if target.execute("pragma integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("lexicon integrity check failed")
        target.close()
        stage.replace(output)
        return {"contract": LEXICON_CONTRACT, "surface_count": len(counts),
                "physical_occurrence_count": len(rows), "size_bytes": output.stat().st_size,
                "normalization_version": NORMALIZATION_VERSION,
                "extraction_version": EXTRACTION_VERSION,
                "stop_policy_version": STOP_POLICY_VERSION}
    finally:
        source.close()
        try:
            target.close()
        except Exception:
            pass
        if stage.exists():
            stage.unlink()


class CorpusLexicon:
    def __init__(self, path: Path):
        self.path = path
        self.db = sqlite3.connect(path.resolve().as_uri()+"?mode=ro&immutable=1", uri=True)
        self.db.row_factory = sqlite3.Row
        self.surfaces = tuple(row[0] for row in self.db.execute(
            "select surface_form from lexical_surface order by length(surface_form) desc,surface_form"))
        self.surface_roles: dict[str, dict[str, bool]] = {}
        for row in self.db.execute(
            "select surface_form,max(lower(source_field) like '%title%') as has_title,"
            "max(lower(source_field) like '%name%' or lower(source_field) like '%speaker%') as has_name "
            "from lexical_occurrence group by surface_form"):
            self.surface_roles[row["surface_form"]] = {
                "title_backed": bool(row["has_title"]),
                "name_or_speaker_backed": bool(row["has_name"]),
            }

    def close(self) -> None:
        self.db.close()

    def extract(self, query: str) -> dict[str, object]:
        exact = [surface for surface in self.surfaces
                 if surface not in LOW_INFORMATION and surface in query]
        # Keep maximal exact spans.  A shorter surface remains only when it starts at
        # a distinct query location rather than merely nesting in a longer title/name.
        maximal = []
        for surface in exact:
            locations = {match.start() for match in re.finditer(re.escape(surface), query)}
            if any(surface in kept and locations == {
                    match.start() + kept.find(surface)
                    for match in re.finditer(re.escape(kept), query)} for kept in maximal):
                continue
            maximal.append(surface)
        anchors = list(maximal)
        basis_by_anchor = {anchor: "accepted_source_authored_exact_span" for anchor in maximal}
        masked = list(query)
        for surface in maximal:
            for match in re.finditer(re.escape(surface), query):
                masked[match.start():match.end()] = " " * len(surface)
        for run in _CJK.findall("".join(masked)):
            cleaned = run
            for stop in sorted(LOW_INFORMATION, key=lambda value: (-len(value), value)):
                cleaned = cleaned.replace(stop, " ")
            for part in cleaned.split():
                if len(part) < 2:
                    continue
                for size in range(min(4, len(part)), 1, -1):
                    for start in range(len(part) - size + 1):
                        gram = part[start:start + size]
                        if gram not in LOW_INFORMATION and gram not in anchors:
                            anchors.append(gram)
                            basis_by_anchor[gram] = ("deterministic_cjk_2_4gram_supplement"
                                if maximal else "deterministic_cjk_2_4gram_fallback")
        recognized = [{"surface_form": anchor, "basis": basis_by_anchor[anchor],
                       "source_field_roles": self.surface_roles.get(anchor, {}),
                       "query_offsets": [m.start() for m in re.finditer(re.escape(anchor), query)]}
                      for anchor in anchors]
        covered = [False] * len(query)
        for item in recognized:
            for start in item["query_offsets"]:
                for index in range(start, start + len(item["surface_form"])):
                    covered[index] = True
        unrecognized = [query[index] for index, value in enumerate(covered)
                        if not value and _CJK.match(query[index]) and query[index] not in LOW_INFORMATION]
        return {"query": query, "recognized_anchors": recognized,
                "unrecognized_spans": "".join(unrecognized),
                "lexicon_coverage": (sum(covered) / len(query)) if query else 0.0,
                "candidate_generation_complete": True, "partial": False,
                "extraction_version": EXTRACTION_VERSION,
                "stop_policy_version": STOP_POLICY_VERSION}


def sparse_rank(query: str, extracted: dict[str, object], rows: Sequence[dict],
                weighted: bool, families: set[str] | None = None,
                scope_count: int | None = None,
                anchor_document_frequency: dict[str, int] | None = None) -> list[dict]:
    anchors = [item["surface_form"] for item in extracted["recognized_anchors"]]
    basis_by_anchor = {item["surface_form"]: item["basis"]
                       for item in extracted["recognized_anchors"]}
    has_supplement = any("supplement" in basis for basis in basis_by_anchor.values())
    filtered = [row for row in rows if not families or row["evidence_family"] in families]
    denominator = len(filtered) if scope_count is None else scope_count
    document_frequency = (anchor_document_frequency or {
        anchor: sum(anchor in row["raw_text"] for row in filtered)
        for anchor in anchors
    })
    results = []
    for row in filtered:
        matches = []
        for anchor in anchors:
            offsets = [match.start() for match in re.finditer(re.escape(anchor), row["raw_text"])]
            if offsets:
                matches.append({"anchor": anchor, "literal_offsets": offsets})
        if not matches:
            continue
        # When an exact name is accompanied by uncovered high-information query
        # spans, a generic name-only occurrence is not promoted by sparse search.
        # It remains available to the dense branch and no relationship is inferred.
        if has_supplement and not any("supplement" in basis_by_anchor[m["anchor"]]
                                      for m in matches):
            roles = [next(item.get("source_field_roles", {})
                          for item in extracted["recognized_anchors"]
                          if item["surface_form"] == match["anchor"])
                     for match in matches]
            if roles and all(role.get("name_or_speaker_backed") and
                             not role.get("title_backed") for role in roles):
                continue
        score = 0.0
        for match in matches:
            anchor = match["anchor"]
            idf = math.log((denominator + 1) / (document_frequency[anchor] + 1)) + 1
            owner = (row.get("owner_field") or "").casefold()
            if row["raw_text"] == anchor:
                field_weight = 1.0
                field_basis = "exact_lexicon_surface_row"
            elif "name" in owner or "title" in owner or "speaker" in owner:
                field_weight = 1.5
                field_basis = "source_name_or_title_field"
            else:
                # A contextual passage is more useful discovery evidence than a
                # short mention.  The bounded length factor is uniform and carries
                # no family/entity semantics.
                field_weight = 3.0 * (1 + min(len(row["raw_text"]), 200) / 200)
                field_basis = "contextual_body_field"
            match["match_field"] = row.get("owner_field")
            match["field_basis"] = field_basis
            source_span_weight = (2.0 if basis_by_anchor[anchor] ==
                                  "accepted_source_authored_exact_span" else 1.0)
            score += idf * len(anchor) * field_weight * source_span_weight
        if len(matches) > 1:
            score *= 1 + 0.15 * (len(matches) - 1)
        results.append({"research_text_id": row["id"], "evidence_ref": row.get("evidence_ref"),
                        "family": row["evidence_family"], "matched_lexical_anchors": matches,
                        "sparse_score": score if weighted else float(len(matches)),
                        "ranking_basis": "weighted_source_field_literal" if weighted else "anchor_literal_union"})
    results.sort(key=lambda item: (-item["sparse_score"], item["research_text_id"]))
    for rank, item in enumerate(results, 1):
        item["sparse_rank"] = rank
    return results


def hybrid_rank(sparse: Sequence[dict], dense_ids: Sequence[int], method: str) -> list[dict]:
    sparse_by_id = {item["research_text_id"]: item for item in sparse}
    dense_rank = {text_id: rank for rank, text_id in enumerate(dense_ids, 1)}
    universe = list(dict.fromkeys([item["research_text_id"] for item in sparse] + list(dense_ids)))
    maximum = max((item["sparse_score"] for item in sparse), default=1.0)
    if method == "sparse_first":
        ordered = universe
    elif method == "rrf":
        ordered = sorted(universe, key=lambda text_id: (-((1 / (60 + sparse_by_id[text_id]["sparse_rank"]) if text_id in sparse_by_id else 0)
            + (1 / (60 + dense_rank[text_id]) if text_id in dense_rank else 0)), text_id))
    elif method == "weighted":
        ordered = sorted(universe, key=lambda text_id: (-((sparse_by_id[text_id]["sparse_score"] / maximum if text_id in sparse_by_id else 0)
            + (1 / math.log2(1 + dense_rank[text_id]) if text_id in dense_rank else 0)), text_id))
    else:
        raise ValueError("unknown hybrid method")
    output = []
    for rank, text_id in enumerate(ordered, 1):
        sparse_item = sparse_by_id.get(text_id)
        sparse_component = (sparse_item["sparse_score"] / maximum
                            if sparse_item else 0.0)
        dense_component = (1 / math.log2(1 + dense_rank[text_id])
                           if text_id in dense_rank else 0.0)
        output.append({"research_text_id": text_id,
                       "matched_lexical_anchors": sparse_item["matched_lexical_anchors"] if sparse_item else [],
                       "sparse_rank": sparse_item["sparse_rank"] if sparse_item else None,
                       "semantic_rank": dense_rank.get(text_id), "final_rank": rank,
                       "final_score": sparse_component + dense_component,
                       "ranking_basis": method, "retrieval_mode": "hybrid_candidate",
                       "discovery_only": True, "relation": None})
    return output


def sparse_preserving_rrf(sparse: Sequence[dict], dense_ids: Sequence[int],
                          sparse_limit: int = 100, dense_limit: int = 200,
                          constant: int = 60) -> list[dict]:
    """RRF a bounded union while preserving every physical sparse candidate."""
    sparse_pool = list(sparse[:sparse_limit])
    sparse_by_id = {int(item["research_text_id"]): item for item in sparse_pool}
    sparse_positions = {int(item["research_text_id"]): rank
                        for rank, item in enumerate(sparse_pool, 1)}
    dense_positions = {}
    for text_id in dense_ids:
        text_id = int(text_id)
        if text_id not in dense_positions:
            dense_positions[text_id] = len(dense_positions) + 1
        if len(dense_positions) >= dense_limit:
            break
    universe = set(sparse_positions) | set(dense_positions)
    score = lambda text_id: (
        (1 / (constant + sparse_positions[text_id])
         if text_id in sparse_positions else 0.0)
        + (1 / (constant + dense_positions[text_id])
           if text_id in dense_positions else 0.0))
    ordered = sorted(universe, key=lambda text_id: (-score(text_id), text_id))
    return [{"research_text_id": text_id,
             "sparse_item": sparse_by_id.get(text_id),
             "sparse_rank": sparse_positions.get(text_id),
             "semantic_rank": dense_positions.get(text_id),
             "rrf_score": score(text_id), "final_rank": rank}
            for rank, text_id in enumerate(ordered, 1)]


def dense_rerank_preserving(sparse: Sequence[dict], scores: dict[int, float],
                            limit: int = 50) -> list[dict]:
    """Rerank the exact sparse pool; dense absence never removes a candidate."""
    pool = list(sparse[:limit])
    ordered = sorted(pool, key=lambda item: (
        -scores.get(int(item["research_text_id"]), float("-inf")),
        int(item["research_text_id"])))
    return [{"sparse_item": item,
             "research_text_id": int(item["research_text_id"]),
             "dense_rerank_score": scores.get(int(item["research_text_id"])),
             "final_rank": rank}
            for rank, item in enumerate(ordered, 1)]


def page(items: Sequence[dict], limit: int, offset: int = 0) -> dict[str, object]:
    if limit < 1 or offset < 0:
        raise ValueError("invalid pagination")
    selected = list(items[offset:offset + limit])
    end = offset + len(selected)
    return {"items": selected, "delivered_count": len(selected),
            "next_offset": end if end < len(items) else None,
            "scope_complete": end >= len(items)}
