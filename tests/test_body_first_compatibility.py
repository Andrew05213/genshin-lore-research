"""Source-checked, synthetic integration regressions for body-first search."""
import copy

import pytest

from test_dialogue_context import fixture
from test_codex_dialogue import populated_fixture
from test_research_ordering import ordered_fixture
from genshin_lore.dialogue import DialogueReadAdapter
from genshin_lore.dialogue.presentation import search_reading, TITLE_FIELDS
from genshin_lore.research_query import ResearchUnavailable


def indexed_fixture():
    q, parents, add_text = fixture(['合成同文', '合成同文', '合成不同正文'])
    add_text('dialog', parents[0], 0, 'talkContentTextMapHash', '合成同文', locale='EN')
    q.db.execute("INSERT INTO research_text_fts(research_text_fts) VALUES('rebuild')")
    return q


def all_hits(q, **kwargs):
    items, cursor = [], None
    for _ in range(20):
        page = q.search(q.corpus_ref, '合成', cursor=cursor, **kwargs)
        items.extend(page['items'])
        cursor = page['next_cursor']
        if cursor is None:
            assert page['scope_complete']
            return items
    pytest.fail('search cursor failed to exhaust a tiny fixture')


def test_same_text_across_locales_and_pages_keeps_exact_source_identity():
    q = indexed_fixture()
    whole = all_hits(q, limit=100)
    paged = all_hits(q, limit=1)
    assert [i['evidence_ref'] for i in paged] == [i['evidence_ref'] for i in whole]
    assert len(paged) == len({i['evidence_ref'] for i in paged}) == 5
    same = [i for i in paged if i['snippet'] == '合成同文']
    assert len(same) == 3 and {i['locale'] for i in same} == {'CHS', 'EN'}
    for hit in paged:
        read = q.read(q.corpus_ref, hit['evidence_ref'])
        inspected = q.inspect(q.corpus_ref, hit['evidence_ref'])
        assert read['raw_text'] == hit['snippet']
        assert inspected['text_provenance'] == read['provenance']
        assert inspected['evidence_ref'] == hit['evidence_ref']


def test_literal_search_reuses_coverage_cache_but_checks_live_availability():
    q = indexed_fixture()
    traced = []
    q.db.set_trace_callback(traced.append)
    args = dict(locale='CHS', families=['dialog_archive'], source_prefix='Text.', limit=1)
    first = q.search(q.corpus_ref, '合成', **args)
    metadata_sql = lambda: [s for s in traced if s.lower().startswith('select distinct')]
    count = len(metadata_sql())
    assert count == 4 and first['next_cursor']
    q.unavailable = ['dialog']
    second = q.search(q.corpus_ref, '合成', cursor=first['next_cursor'], **args)
    assert len(metadata_sql()) == count
    assert second['unavailable_scopes'] == ['dialog']
    assert len(q._scope_source_cache) == 4


def test_warm_metadata_cache_never_hides_source_body_corruption():
    q = indexed_fixture()
    q.search(q.corpus_ref, '合成', locale='CHS', families=['dialog_archive'])
    q.sources['dialog'].execute("UPDATE text_candidate SET raw_text='损坏合成原文' WHERE row_ordinal=0")
    with pytest.raises(ResearchUnavailable, match='drift'):
        q.search(q.corpus_ref, '合成', locale='CHS', families=['dialog_archive'])


def test_search_byte_budget_cursor_resumes_at_last_delivered_occurrence(monkeypatch):
    from genshin_lore import research_query
    q = indexed_fixture()
    expected = all_hits(q, limit=100)
    import json
    one = max(len(json.dumps({'items': [item]}, ensure_ascii=False,
                             separators=(',', ':')).encode()) for item in expected)
    monkeypatch.setattr(research_query, 'SEARCH_DATA_BUDGET', one + 1)
    actual = all_hits(q, limit=100)
    assert [i['evidence_ref'] for i in actual] == [i['evidence_ref'] for i in expected]


@pytest.mark.parametrize('change', [
    {'query': '合成同文'}, {'families': ['npc_archive']}, {'locale': 'EN'},
    {'source_prefix': 'Other.'}, {'limit': 2}, {'corpus_ref': 'another-corpus'},
])
def test_cursor_scope_cannot_cross_query_filter_limit_or_corpus(change):
    q = indexed_fixture()
    args = dict(corpus_ref=q.corpus_ref, query='合成', locale='CHS',
                families=['dialog_archive'], source_prefix='Text.', limit=1)
    cursor = q.search(**args)['next_cursor']
    assert cursor
    with pytest.raises((ValueError, LookupError)):
        q.search(**{**args, **change}, cursor=cursor)


def test_body_only_first_page_keeps_a_real_npc_dialogue_action():
    q, _, _ = populated_fixture(body_mentions_name=True)
    q.db.execute("INSERT INTO research_text_fts(research_text_fts) VALUES('rebuild')")
    result = q.search(q.corpus_ref, '合成角色甲', locale='CHS', limit=1)
    before = copy.deepcopy(result)
    assert result['items'][0]['owner_field'] == 'DialogNormal'
    panel = search_reading(q, result, '合成角色甲', 'CHS')
    assert result == before and panel['name_matches'] == []
    physical = next(a for a in panel['next_actions'] if a['arguments'].get('view', 'physical') == 'physical')
    assert physical['arguments']['evidence_ref'] == result['items'][0]['evidence_ref']
    body = q.read(**physical['arguments'])
    assert body['raw_text'] == '合成角色甲'
    dialogue = next(a for a in panel['next_actions'] if a['arguments'].get('view') == 'dialogue')
    speech = q.read(**dialogue['arguments'])
    assert speech['nodes'] and speech['nodes'][0]['speaker']['logical_identity_proven'] is False


@pytest.mark.parametrize('mode,release', [('lexical', None), ('semantic', None), ('hybrid', None), ('literal', '7.0')])
def test_maintenance_adapter_preserves_nonliteral_and_bounded_story_arguments(mode, release):
    q = indexed_fixture()
    calls = []
    q.search = lambda *args: calls.append(args) or {'delegated': True}
    adapter = DialogueReadAdapter(q)
    assert adapter.search('corpus', 'word', 'EN', ['family'], 'prefix', 7,
                          'cursor', mode, 'domain', release) == {'delegated': True}
    assert calls == [('corpus', 'word', 'EN', ['family'], 'prefix', 7, 'cursor', mode, 'domain', release)]


@pytest.mark.parametrize('title_field', sorted(TITLE_FIELDS))
def test_every_declared_presentation_title_has_exact_title_priority(title_field):
    q = ordered_fixture('合成')
    q.db.execute('UPDATE research_text SET owner_field=? WHERE id=39', (title_field,))
    hit = q.search(q.corpus_ref, '合成', limit=1)['items'][0]
    assert hit['owner_field'] == title_field
    assert hit['ranking_basis'] == 'exact_title'


def test_v1_trial_cursor_is_rejected_after_title_priority_changes():
    q = indexed_fixture()
    scope = {'op': 'search', 'q': '合成', 'locale': None, 'families': [],
             'source_prefix': None, 'limit': 1, 'ordering': 'body-first-v1'}
    old = q._cursor(scope, {'rank': 1, 'repeat': 1, 'id': 2})
    with pytest.raises(ValueError, match='cursor'):
        q.search(q.corpus_ref, '合成', limit=1, cursor=old)
