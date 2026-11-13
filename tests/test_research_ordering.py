"""Synthetic SQL ordering oracle; source qualification is tested separately."""
import pytest

from test_dialogue_context import fixture, insert
from genshin_lore.dialogue import DialogueReadAdapter


def ordered_fixture(keyword):
    q, _, _ = fixture()
    template = dict(q.db.execute('SELECT * FROM research_text LIMIT 1').fetchone())
    q.db.execute('DELETE FROM research_text')
    roles = ['nameTextMapHash']*35 + ['body', 'talkContentTextMapHash', 'unknown_field', 'titleTextMapHash']
    for n, role in enumerate(roles, 1):
        row = {**template, 'id': n, 'owner_field': role, 'child_id': str(n), 'pk_value':str(n),
               'raw_text': keyword if role in ('nameTextMapHash', 'titleTextMapHash') else keyword+' 合成正文',
               'locale': 'CHS' if n != 37 else 'EN'}
        insert(q.db, 'research_text', row)
    q.db.execute("INSERT INTO research_text_fts(research_text_fts) VALUES('rebuild')")
    q.unavailable = []
    q._recheck_text = lambda row: row['raw_text']
    q._decorate_context = lambda item, row: item
    return q


@pytest.mark.parametrize('keyword', ['合成人名', '月', '？', 'Test Name'])
def test_body_before_names_is_global_and_every_page_has_exact_once_coverage(keyword):
    q = ordered_fixture(keyword)
    cursor, items = None, []
    while True:
        page = q.search(q.corpus_ref, keyword, limit=3, cursor=cursor)
        items.extend(page['items'])
        cursor = page['next_cursor']
        if not cursor:
            break
    assert [i['owner_field'] for i in items[:4]] == [
        'titleTextMapHash', 'body', 'talkContentTextMapHash', 'unknown_field']
    assert len(items) == len({i['evidence_ref'] for i in items}) == 39
    assert [i['evidence_ref'] for i in items] == [i['evidence_ref'] for i in q.search(q.corpus_ref, keyword, limit=100)['items']]
    assert all(keyword in i['snippet'] for i in items)
    assert page['ranking']['mode'] == 'body-first-v2'


def test_filters_and_old_cursor_are_not_reinterpreted():
    q = ordered_fixture('合成')
    args = dict(locale='CHS', families=['npc_archive'], source_prefix='Text.', limit=2)
    first = q.search(q.corpus_ref, '合成', **args)
    assert first['next_cursor'] and all(i['locale'] == 'CHS' for i in first['items'])
    for change in ({'locale':'EN'}, {'limit':3}, {'source_prefix':'Text_'}):
        with pytest.raises(ValueError, match='cursor'):
            q.search(q.corpus_ref, '合成', cursor=first['next_cursor'], **{**args, **change})
    assert not q.search(q.corpus_ref, '合成', source_prefix='Text_')['items']
    old_scope = {'op':'search', 'q':'合成', 'locale':'CHS', 'families':['npc_archive'],
                 'source_prefix':'Text.', 'limit':2}
    old = q._cursor(old_scope, {'rank':0, 'id':1})
    with pytest.raises(ValueError, match='cursor'):
        q.search(q.corpus_ref, '合成', cursor=old, **args)
    assert not q.search(q.corpus_ref, '不存在')['items']


def test_maintenance_adapter_uses_new_order_without_changing_qualified_runtime():
    q = ordered_fixture('合成')
    # A sealed runtime may still implement its original order. Literal routing
    # uses the explicitly installed maintenance implementation after startup.
    q.search = lambda *a, **k: {'unchanged_nonliteral_delegate': True}
    adapter = DialogueReadAdapter(q)
    page = adapter.search(q.corpus_ref, '合成', limit=2)
    assert page['items'][1]['owner_field'] == 'body'
    assert adapter.search(q.corpus_ref, '合成', mode='semantic') == {'unchanged_nonliteral_delegate': True}


def test_duplicate_body_is_deferred_but_every_occurrence_remains_readable():
    q = ordered_fixture('合成')
    q.db.execute("UPDATE research_text SET owner_field='body',raw_text='合成 同文' WHERE id IN (1,2,3)")
    q.db.execute("INSERT INTO research_text_fts(research_text_fts) VALUES('rebuild')")
    page = q.search(q.corpus_ref, '合成', limit=100)['items']
    assert [i['snippet'] for i in page[:5]] == ['合成', '合成 同文', '合成 合成正文', '合成 合成正文', '合成 同文']
    same = [i for i in page if i['snippet'] == '合成 同文']
    assert len(same) == len({i['evidence_ref'] for i in same}) == 3


@pytest.mark.parametrize('keyword', ['合成', '合成角色', '？', 'Test Name'])
def test_diversified_cursor_can_resume_at_a_lower_physical_id(keyword):
    q = ordered_fixture(keyword)
    q.db.execute('DELETE FROM research_text WHERE id>5')
    q.db.execute(
        "UPDATE research_text SET owner_field='body',locale='CHS',"
        "raw_text=? || CASE WHEN id<=3 THEN ' A' WHEN id=4 THEN ' B' ELSE ' C' END",
        (keyword,),
    )
    q.db.execute("INSERT INTO research_text_fts(research_text_fts) VALUES('rebuild')")
    refs = {r['id']: q._text_ref(r) for r in q.db.execute('SELECT * FROM research_text')}
    first = q.search(q.corpus_ref, keyword, limit=3)
    assert [hit['evidence_ref'] for hit in first['items']] == [refs[i] for i in (1, 4, 5)]
    scope = {'op': 'search', 'q': keyword, 'locale': None, 'families': [],
             'source_prefix': None, 'limit': 3, 'ordering': 'body-first-v2'}
    # A cursor issued before the SQL optimization keeps the same position.
    old_cursor = q._cursor(scope, {'rank': 1, 'repeat': 1, 'id': 5})
    assert first['next_cursor'] == old_cursor
    last = q.search(q.corpus_ref, keyword, limit=3, cursor=old_cursor)
    assert [hit['evidence_ref'] for hit in last['items']] == [refs[i] for i in (2, 3)]
    assert last['next_cursor'] is None and last['scope_complete']


@pytest.mark.parametrize('keyword', ['合成', '合成角色'])
def test_repeat_numbers_are_computed_after_every_scope_filter(keyword):
    q = ordered_fixture(keyword)
    q.db.execute('DELETE FROM research_text WHERE id>6')
    q.db.execute(
        "UPDATE research_text SET owner_field='body',locale='CHS',"
        "text_source_path='Text_/selected.json',"
        "raw_text=? || CASE WHEN id=5 THEN ' B' ELSE ' A' END",
        (keyword,),
    )
    q.db.execute("UPDATE research_text SET locale='EN' WHERE id=1")
    q.db.execute("UPDATE research_text SET evidence_family='dialog_archive' WHERE id=3")
    q.db.execute("UPDATE research_text SET text_source_path='TextX/excluded.json' WHERE id=6")
    q.db.execute("INSERT INTO research_text_fts(research_text_fts) VALUES('rebuild')")
    refs = {r['id']: q._text_ref(r) for r in q.db.execute('SELECT * FROM research_text')}
    args = dict(locale='CHS', families=['npc_archive'], source_prefix='Text_')
    expected = [refs[i] for i in (2, 5, 4)]
    for limit in (1, 3):
        actual, cursor = [], None
        for _ in range(4):
            page = q.search(q.corpus_ref, keyword, limit=limit, cursor=cursor, **args)
            actual.extend(hit['evidence_ref'] for hit in page['items'])
            cursor = page['next_cursor']
            if cursor is None:
                assert page['scope_complete']
                break
        assert cursor is None and actual == expected
