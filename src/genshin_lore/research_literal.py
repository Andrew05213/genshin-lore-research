"""Literal search ordering shared by source runtimes and qualified maintenance adapters.

Only display relevance changes. Filtering, source rechecks and exact identities
remain source-bound; old-order cursors cannot be reinterpreted.
"""
import json

ORDERING_VERSION = 'body-first-v2'
TITLE_FIELDS = frozenset({
    'titleTextMapHash', 'storyTitleTextMapHash', 'storyTitle2TextMapHash',
    'voiceTitleTextMapHash', 'MainQuestTitle', 'SubQuestTitle', 'ChapterTitle',
    'title', 'Title', 'storyTitleLockedTextMapHash', 'voiceTitleLockedTextMapHash',
})

# Declared body slots, not inferred ownership. Unknown slots remain searchable.
BODY_FIELDS = frozenset({
    'descTextMapHash', 'avatarDetailTextMapHash', 'storyContextTextMapHash',
    'storyContext2TextMapHash', 'voiceFileTextTextMapHash', 'body',
    'talkContentTextMapHash', 'contentTextMapHash', 'stepDescTextMapHash',
    'MainQuestDesp', 'reviewTextMapHash', 'markDescTextMapHash',
    'voiceDescTextMapHash', 'markTipContentTextMapHash', 'cgDesTextMapHash',
    'newsTextTextMapHash', 'talkContent/0', 'talkContent/1',
    'DialogNormal', 'Narratage', 'Aside',
})


def literal_search(q, query, locale, families, source_prefix, limit, cursor, *, name_anchor=False):
    # Import the runtime's exception contract, including a retained release's
    # qualified implementation when explicitly used by the maintenance adapter.
    from .research_query import ResearchUnavailable, SEARCH_DATA_BUDGET
    limit = max(1, min(int(limit), 100))
    families = sorted(set(families or ()))
    scope = {
        "op": "search",
        "q": query,
        "locale": locale,
        "families": families,
        "source_prefix": source_prefix,
        "limit": limit,
        "ordering": ORDERING_VERSION,
    }
    if name_anchor:
        scope['name_anchor'] = True
    after = q._after(cursor, scope)
    where = []
    args = []
    if name_anchor:
        where.append("t.owner_field='nameTextMapHash' and t.raw_text=?")
        args.append(query)
    if locale:
        where.append("t.locale=?")
        args.append(locale)
    if families:
        where.append("t.evidence_family in (%s)" % ",".join("?" * len(families)))
        args.extend(families)
    if source_prefix is not None:
        where.append("substr(t.text_source_path,1,length(?))=?")
        args.extend((source_prefix, source_prefix))
    names = "(lower(coalesce(t.owner_field,'')) like '%name%' or t.owner_field='SpeakerKnown')"
    titles = "(t.owner_field in (%s))" % ','.join("'" + field + "'" for field in sorted(TITLE_FIELDS))
    fields = ",".join("'" + field + "'" for field in sorted(BODY_FIELDS))
    substantive = f"(t.owner_field in ({fields}) or (t.evidence_family='readable_archive' and trim(t.raw_text)<>''))"
    rank = f"case when t.raw_text=? and {titles} then 0 when {substantive} then 1 when {titles} then 2 when not {names} then 3 when t.raw_text=? then 4 else 5 end"
    if after == 0:
        after = {"rank": -1, "repeat": 0, "id": 0}
    if not isinstance(after, dict) or set(after) != {"rank", "repeat", "id"}:
        raise ValueError("cursor position mismatch; restart search")
    rank_args = [query, query]
    filters = " and ".join(where) if where else "1=1"
    if len(query) >= 3 and all(ch.isalnum() or ch.isspace() for ch in query):
        inner = f"select t.*,{rank} research_rank from research_text_fts f join research_text t on t.id=f.rowid where f.raw_text match ?"
        args = [*rank_args, '"' + query.replace('"', '""') + '"', *args]
    else:
        inner = f"select t.*,{rank} research_rank from research_text t where instr(t.raw_text,?)>0"
        args = [*rank_args, query, *args]
    # Diversify identical display text only AFTER applying scope filters. Every
    # physical occurrence stays in the result, including subsequent pages.
    # Keep provenance and other wide columns out of both sorts. Scope filters
    # still see the full row, and the window still sees every scoped match.
    filtered = f"select id,locale,raw_text,research_rank from ({inner}) t where {filters}"
    varied = (
        "select id,research_rank,row_number() over "
        "(partition by research_rank,locale,raw_text order by id) repeat_rank "
        f"from ({filtered}) t"
    )
    page = (
        f"select id,research_rank,repeat_rank from ({varied}) t where "
        "research_rank>? or (research_rank=? and "
        "(repeat_rank>? or (repeat_rank=? and id>?))) "
        "order by research_rank,repeat_rank,id limit ?"
    )
    # Hydrate only the bounded page, in the original cursor order. Source
    # rechecks and byte-budget continuation below require the complete rows.
    sql = (
        f"select t.*,p.research_rank,p.repeat_rank from ({page}) p "
        "join research_text t on t.id=p.id "
        "order by p.research_rank,p.repeat_rank,p.id"
    )
    args.extend((int(after['rank']), int(after['rank']), int(after['repeat']),
                 int(after['repeat']), int(after['id']), limit+1))
    rows = q.db.execute(sql, args).fetchall()
    items = []
    for row in rows[:limit]:
        raw = q._recheck_text(row)
        if query not in raw:
            raise ResearchUnavailable(
                "candidate recall failed exact literal recheck"
            )
        pos = raw.find(query)
        basis = ("exact_title", "substantive_body_match", "title_match",
                 "unclassified_text_match", "exact_name_anchor", "name_match")[row["research_rank"]]
        item = {
            "evidence_ref": q._text_ref(row),
            "container_ref": q._container_ref(
                q.db.execute(
                    "select * from research_container where id=?",
                    (row["container_id"],),
                ).fetchone()
            ),
            "family": row["evidence_family"],
            "contract": row["evidence_contract"],
            "exact_run": row["evidence_run"],
            "source_path": row["text_source_path"],
            "physical_pointer": row["text_source_pointer"],
            "locale": row["locale"],
            "owner_field": row["owner_field"],
            "snippet": raw[max(0, pos - 80) : pos + len(query) + 80],
            "match_span": [pos, pos + len(query)],
            "provenance_ref": q._text_ref(row),
            "ranking_basis": basis,
            "recommended_next_actions": [
                "read_evidence",
                "inspect_evidence",
                "follow_evidence",
            ],
        }
        item = q._decorate_context(item, row)
        if (
            len(
                json.dumps(
                    {"items": items + [item]},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            )
            > SEARCH_DATA_BUDGET
        ):
            break
        items.append(item)
    searched, unsupported, unavailable, filtered, unknown = q._scope_coverage(
        families or None, locale
    )
    last_row = items and rows[len(items) - 1]
    last = {"rank": int(last_row["research_rank"]), "repeat":int(last_row['repeat_rank']), "id": int(last_row["id"])} if last_row else None
    more = len(rows) > len(items)
    return {
        "items": items,
        "delivered_count": len(items),
        "next_cursor": q._cursor(scope, last)
        if more and last is not None
        else None,
        "scope_complete": not more,
        "searched_scopes": searched,
        "unsupported_scopes": unsupported,
        "unavailable_scopes": unavailable,
        "locale_filtered_scopes": filtered,
        "locale_unknown_scopes": unknown,
        "partial": bool(unsupported or unavailable or (locale and unknown)),
        "ranking": {
            "mode": ORDERING_VERSION,
            "preserves_physical_occurrences": True,
        },
    }
