"""A candidate exported path permits reading, never confirmed book ownership."""
import json
import sqlite3
import pytest

from genshin_lore.research_source_context import ResearchSourceContext, title_state


def document_fixture():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE document_row(id,source_path,pointer);
        CREATE TABLE document_slot(id,row_id,field,locale,status);
        CREATE TABLE document_text(id,slot_id,raw_text,source_path,pointer);
        CREATE TABLE document_relation(id,row_id,kind,status,search_complete);
        CREATE TABLE document_candidate(relation_id,target_seal_json);
        INSERT INTO document_row VALUES('row','SyntheticDocument.json','/0');
        INSERT INTO document_slot VALUES('slot','row','titleTextMapHash','CHS','resolved');
        INSERT INTO document_text VALUES('text','slot','合成书名','SyntheticTextMap.json','/1');
        INSERT INTO document_relation VALUES('relation','row','document_localization','mapping_unresolved',0);
    ''')
    target = {'target': {'path': 'Readable/CHS/Synthetic.txt', 'sha256': 'sealed-body'},
              'locale': 'CHS', 'source_path': 'SyntheticLocalization.json',
              'source_pointer': '/4/opaquePath', 'raw_locator': 'ART/UI/Readable/CHS/Synthetic'}
    db.execute('INSERT INTO document_candidate VALUES(?,?)',
        ('relation', json.dumps({'candidate_readable_targets': [target, {**target, 'source_pointer': '/4/secondPath'}]})))
    return db


def test_unknown_document_array_can_discover_sealed_body_without_title_promotion():
    with document_fixture() as db:
        context = ResearchSourceContext(None, db)
        assert context.readable_paths_for_title('合成书名') == ['Readable/CHS/Synthetic.txt']
        assert context.readable_paths_for_title('不存在的标题') == []
        assert context._readable_document_titles('Readable/CHS/Synthetic.txt', 'wrong-seal') == []
        titles = context._readable_document_titles('Readable/CHS/Synthetic.txt', 'sealed-body')
        assert len(titles) == 1  # One physical title, both locator observations retained.
        assert len(titles[0]['navigation_candidates']) == 2
        state = title_state(titles, source_kind='book', path='Readable/CHS/Synthetic.txt')
        assert state['display_title'] == '合成书名'
        assert state['title_resolution_status'] == 'candidate'
        assert '未证实' in state['citation_label']
        assert db.execute('SELECT status,search_complete FROM document_relation').fetchone()[:] == ('mapping_unresolved', 0)


def test_unresolved_raw_id_alone_never_generates_a_readable_path():
    with document_fixture() as db:
        db.execute("UPDATE document_candidate SET target_seal_json='{}'")
        assert ResearchSourceContext(None, db).readable_paths_for_title('合成书名') == []


def test_existing_direct_title_path_keeps_its_separate_status():
    with document_fixture() as db:
        db.execute("UPDATE document_relation SET kind='localization_readable',status='resolved',search_complete=1")
        db.execute('UPDATE document_candidate SET target_seal_json=?',
            (json.dumps({'path': 'Readable/CHS/Synthetic.txt', 'sha256': 'sealed-body'}),))
        context = ResearchSourceContext(None, db)
        titles = context._readable_document_titles('Readable/CHS/Synthetic.txt', 'sealed-body')
        assert title_state(titles, source_kind='book', path='Readable/CHS/Synthetic.txt')['title_resolution_status'] == 'resolved'


def test_reading_card_keeps_candidate_title_qualification():
    from genshin_lore.source_cards import _reading_body
    body = '合成正文。'
    rendered = _reading_body({'quoted_text': body, 'read_record': {
        'source_result': {'source_context': {'display_title': '合成书名', 'title_resolution_status': 'candidate'}},
        'reading_context': {}}})
    assert body in rendered and '候选书名' in rendered and '未证实' in rendered


def test_five_tool_candidate_path_resolves_only_exact_source_and_file_seal():
    from genshin_lore.research71_candidate_query import CandidateResearch71
    from genshin_lore.research_query import ResearchUnavailable
    q = object.__new__(CandidateResearch71)
    q.source_version = '7.2'
    q.db = sqlite3.connect(':memory:')
    q.db.row_factory = sqlite3.Row
    q.db.executescript('CREATE TABLE research_container(id,source_key,source_path,source_file_sha256);'
                      "INSERT INTO research_container VALUES(1,'7.2-readable_archive','Synthetic.txt','body-sha');"
                      "INSERT INTO research_container VALUES(2,'7.1-readable_archive','Synthetic.txt','body-sha');")
    checked = []
    q._recheck_container = lambda row: checked.append(row['id'])
    q._container_ref = lambda row: 'supplied-ref-' + str(row['id'])
    locator = {'target': {'path': 'Synthetic.txt', 'sha256': 'body-sha'}, 'locale': 'CHS'}
    raw = {'target_seal_json': json.dumps({'candidate_readable_targets': [locator, locator]})}
    result = q._document_readable_targets(raw)
    assert [r['target_evidence_ref'] for r in result] == ['supplied-ref-1', 'supplied-ref-1']
    assert checked == [1, 1]  # Two physical locator observations, not an identity deduplication.
    assert all(r['status'] == 'physical_candidate' and not r['namespace_proven'] for r in result)
    locator['target']['sha256'] = 'wrong'
    raw = {'target_seal_json': json.dumps({'candidate_readable_targets': [locator]})}
    with pytest.raises(ResearchUnavailable, match='exact source scope'):
        q._document_readable_targets(raw)
    assert q._document_readable_targets({}) == []
    q.db.close()


@pytest.fixture
def document_follow(monkeypatch):
    """Real stored follow/rechecks over a small synthetic index and source."""
    from genshin_lore import research71_candidate_query as module
    from genshin_lore import research_query, research_index
    q = object.__new__(module.CandidateResearch71)
    q.source_version = '7.2'
    q.corpus_ref = 'corpus'
    q.coverage = []
    q.db = sqlite3.connect(':memory:')
    q.db.row_factory = sqlite3.Row
    q.db.executescript(research_index.SCHEMA)
    q.db.execute("INSERT INTO research_container VALUES(1,'7.2-readable_archive','readable_archive',"
                 "'readable_archive','synthetic','parser','run','body','file','files','id','body',"
                 "'Synthetic.txt','/','body-sha',NULL,0,'physical')")
    q.db.execute("INSERT INTO research_container VALUES(2,'document-source','document_archive',"
                 "'document_archive','synthetic','parser','run','document-row','row','document_row','id',"
                 "'document-row','SyntheticDocument.json','/0','document-sha',NULL,0,'physical')")
    source = sqlite3.connect(':memory:')
    source.row_factory = sqlite3.Row
    source.executescript('CREATE TABLE document_relation(id PRIMARY KEY,row_id,origin_pointer,kind,source_path,pointer,status,candidate_count);'
                        'CREATE TABLE document_candidate(id PRIMARY KEY,relation_id,target_source_path,target_pointer,target_seal_json);')
    for i in (1, 2):
        locator = {'target': {'path': 'Synthetic.txt', 'sha256': 'body-sha'}, 'locale': 'CHS',
                   'source_pointer': '/4/field' + str(i)}
        source.execute('INSERT INTO document_relation VALUES(?,?,?,?,?,?,?,?)',
                       ('r'+str(i),'document-row','/0/opaque/0','document_localization',
                        'SyntheticLocalization.json',locator['source_pointer'],'mapping_unresolved',1))
        source.execute('INSERT INTO document_candidate VALUES(?,?,?,?,?)',
                       ('c'+str(i),'r'+str(i),'SyntheticLocalization.json','/4',
                        json.dumps({'candidate_readable_targets': [locator]})))
        q.db.execute('INSERT INTO research_relation VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (i,2,'document-source','document_archive','document_relation','id','r'+str(i),
                      'document_localization','/0/opaque/0','synthetic locator','mapping_unresolved',0,1,'unresolved'))
        q.db.execute('INSERT INTO research_candidate VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (i,i,'document_candidate','id','c'+str(i),None,None,None,None,None,
                      'SyntheticLocalization.json','/4',None))
    monkeypatch.setattr(research_query, 'relations_for', lambda *a: [])
    monkeypatch.setattr(module, 'decode_ref', lambda _: {'kind': 'container'})
    monkeypatch.setattr(research_query, 'decode_ref', lambda _: {'kind': 'container'})
    q._require = lambda corpus: None
    q._container_for = lambda _: q.db.execute('SELECT * FROM research_container WHERE id=2').fetchone()
    q.components = {'document-source': {'adapter': 'document_archive', 'family': 'document_archive'}}
    q._source = lambda key: source
    q._recheck_container = lambda row: None
    q._container_ref = lambda row: 'supplied-body-ref'
    q._target_ref = lambda row: None
    try:
        yield q, source
    finally:
        source.close()
        q.db.close()


@pytest.mark.parametrize('version', ['7.1', '7.2'])
@pytest.mark.parametrize('exported', [False, True])
def test_follow_keeps_distinct_localization_relations_with_equal_target_coordinates(document_follow, version, exported):
    q, source = document_follow
    q.source_version = version
    q.db.execute('UPDATE research_container SET source_key=? WHERE id=1', (version+'-readable_archive',))
    if not exported:
        source.execute("UPDATE document_candidate SET target_seal_json='{}'")
        source.execute("UPDATE document_relation SET kind='localization_readable'")
        q.db.execute("UPDATE research_relation SET relation_kind='localization_readable'")
    first = q.follow('corpus', 'source-ref', limit=1)
    second = q.follow('corpus', 'source-ref', limit=1, cursor=first['next_cursor'])
    assert first['next_cursor'] and second['next_cursor'] is None
    candidates = [page['items'][0]['candidates'][0] for page in (first, second)]
    assert all(c['raw_target'] is None for c in candidates)
    if exported:
        paths = [c['candidate_readable_targets'][0] for c in candidates]
        assert [p['source_locator']['source_pointer'] for p in paths] == ['/4/field1', '/4/field2']
        assert all(p['target_evidence_ref'] == 'supplied-body-ref' and not p['namespace_proven'] for p in paths)
        assert first['partial'] and second['partial']
    else:
        assert all('candidate_readable_targets' not in c for c in candidates)


@pytest.mark.parametrize('mutation', ['wrong_pointer', 'wrong_relation', 'missing_candidate'])
def test_document_follow_rejects_source_candidate_identity_drift(document_follow, mutation):
    from genshin_lore.research_query import ResearchUnavailable
    q, source = document_follow
    if mutation == 'wrong_pointer':
        source.execute("UPDATE document_candidate SET target_pointer='/5' WHERE id='c1'")
    elif mutation == 'wrong_relation':
        source.execute("UPDATE document_candidate SET relation_id='r2' WHERE id='c1'")
    else:
        source.execute("DELETE FROM document_candidate WHERE id='c1'")
    with pytest.raises(ResearchUnavailable, match='candidate.*(drift|identity|disappeared)'):
        q.follow('corpus', 'source-ref')


def test_document_follow_disabled_does_not_expand_exported_paths(document_follow, monkeypatch):
    q, _ = document_follow
    def forbidden(*args):
        raise AssertionError('resolve_targets=false must not expand exported paths')
    monkeypatch.setattr(q, '_document_readable_targets', forbidden)
    result = q.follow('corpus', 'source-ref', resolve_targets=False)
    assert len(result['items']) == 2
    assert all('candidate_readable_targets' not in item['candidates'][0] for item in result['items'])
