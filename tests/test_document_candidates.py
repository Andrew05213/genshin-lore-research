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


@pytest.mark.parametrize('case', ['typed_null', 'wrong_pointer', 'ambiguous', 'disabled'])
def test_document_follow_reads_exact_source_candidate_with_nullable_raw_target(monkeypatch, case):
    from copy import deepcopy
    from genshin_lore import research71_candidate_query as module
    from genshin_lore.research_query import ResearchQuery, ResearchUnavailable

    q = object.__new__(module.CandidateResearch71)
    q.source_version = '7.2'
    q.db = sqlite3.connect(':memory:')
    q.db.row_factory = sqlite3.Row
    q.db.executescript('CREATE TABLE research_container(id,source_key,source_path,source_file_sha256);'
                      "INSERT INTO research_container VALUES(1,'7.2-readable_archive','Synthetic.txt','body-sha');")
    source = sqlite3.connect(':memory:')
    source.row_factory = sqlite3.Row
    source.executescript('CREATE TABLE document_relation(id,row_id,origin_pointer,kind);'
                        'CREATE TABLE document_candidate(id,relation_id,target_source_path,target_pointer,target_seal_json);'
                        "INSERT INTO document_relation VALUES('r','document-row','/0/opaque/0','document_localization');")
    locator = {'target': {'path': 'Synthetic.txt', 'sha256': 'body-sha'}, 'locale': 'CHS'}
    metadata = json.dumps({'candidate_readable_targets': [locator]})
    source.execute("INSERT INTO document_candidate VALUES('c','r','SyntheticLocalization.json','/4',?)", (metadata,))
    result = {'items': [{'relation_kind': 'document_localization', 'status': 'mapping_unresolved',
                         'proof_source_pointer': '/0/opaque/0', 'candidates': [{
                             'target_evidence_ref': None, 'target_source_path': 'SyntheticLocalization.json',
                             'target_pointer': '/4', 'raw_target': None}]}],
              'partial': True, 'scope_complete': False}
    if case == 'wrong_pointer':
        result['items'][0]['candidates'][0]['target_pointer'] = '/5'
    elif case == 'ambiguous':
        source.execute("INSERT INTO document_relation VALUES('r2','document-row','/0/opaque/0','document_localization')")
        source.execute("INSERT INTO document_candidate VALUES('c2','r2','SyntheticLocalization.json','/4',?)", (metadata,))
    monkeypatch.setattr(ResearchQuery, 'follow', lambda *a, **kw: deepcopy(result))
    monkeypatch.setattr(module, 'decode_ref', lambda _: {'kind': 'container'})
    q._container_for = lambda _: {'source_key': 'document-source', 'evidence_family': 'document_archive',
                                  'occurrence_id': 'document-row'}
    q.components = {'document-source': {'adapter': 'document_archive'}}
    reads = []
    q._source = lambda key: (reads.append(key), source)[1]
    q._recheck_container = lambda row: None
    q._container_ref = lambda row: 'supplied-body-ref'
    try:
        if case in ('wrong_pointer', 'ambiguous'):
            with pytest.raises(ResearchUnavailable, match='source coordinates'):
                q.follow('corpus', 'source-ref')
        else:
            value = q.follow('corpus', 'source-ref', None, 20, None, case != 'disabled')
            candidate = value['items'][0]['candidates'][0]
            assert candidate['raw_target'] is None  # Original projection is preserved.
            if case == 'disabled':
                assert not reads and 'candidate_readable_targets' not in candidate
            else:
                assert reads == ['document-source']
                assert candidate['candidate_readable_targets'][0]['target_evidence_ref'] == 'supplied-body-ref'
                assert candidate['candidate_readable_targets'][0]['namespace_proven'] is False
                assert value['partial'] and not value['scope_complete']
    finally:
        source.close()
        q.db.close()
