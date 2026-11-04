"""A candidate exported path permits reading, never confirmed book ownership."""
import json
import sqlite3

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
