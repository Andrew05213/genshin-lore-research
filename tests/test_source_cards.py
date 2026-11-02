"""Selected citation delivery preserves source bytes, scope and independent identity."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from genshin_lore.citation_handoff import CitationHandoff
from test_codex_dialogue import populated_fixture


def material(handoff, *, observation=0, start=0, ref='synthetic:exact:a'):
    text = '合成文字\n```\n<不是HTML>。'
    body = {'evidence_ref': ref, 'raw_text': text,
            'observation_index': observation,
            'text_range': {'start': start, 'end': start + len(text), 'complete': False},
            'locale': 'CHS', 'limitations': ['candidate speaker'],
            'text_proof': {'relation_status': 'physical_candidate'}}
    page = {'partial': True, 'scope_complete': False, 'corpus_complete': False,
            'source_titles': [{'raw_text': '合成篇章', 'evidence_ref': 'synthetic:title'}],
            'nodes': [{'speaker': {'display_name': '合成人物', 'status': 'ambiguous',
                                  'logical_identity_proven': False},
                       'source_context': {'title_indexes': [0]}, 'texts': [body]}]}
    out = handoff.attach({'status': 'ok', 'data': page}, 'read', compact=True)
    handle = out['data']['nodes'][0]['texts'][0]['citation_handle']
    inspected = {'evidence_ref': ref,
                 'text_provenance': {'snapshot': 'synthetic-snapshot', 'source_file': '合成.json',
                                     'source_pointer': '/node/1', 'run': 'synthetic-run'},
                 'text_proof': {'relation_status': 'physical_candidate'}}
    return handle, body, inspected


def test_selected_source_card_preserves_text_titles_states_and_exact_ref(tmp_path):
    handoff = CitationHandoff(source_cards=tmp_path/'cards')
    handle, body, inspection = material(handoff)
    original = copy.deepcopy(handoff.resolve(handle))
    result = handoff.save_source(handle, inspection, 'synthetic-corpus')
    raw = Path(result['metadata_path']).read_bytes()
    card = json.loads(raw)
    rendered = Path(result['markdown_path']).read_text(encoding='utf-8')
    assert result['status'] == 'SAVED'
    assert hashlib.sha256(raw).hexdigest() == result['card_sha256']
    assert card['quoted_text'] == body['raw_text']
    assert card['quoted_text_sha256'] == hashlib.sha256(body['raw_text'].encode()).hexdigest()
    assert card['evidence_ref'] == body['evidence_ref']
    assert card['locale'] == 'CHS'
    assert card['range'] == body['text_range']
    assert card['inspection'] == inspection
    assert card['read_record']['reading_context']['speaker']['status'] == 'ambiguous'
    assert card['read_record']['reading_context']['partial'] is True
    assert '合成篇章' in rendered and '合成人物' in rendered
    for technical in ('synthetic:exact', 'synthetic-snapshot', 'source_pointer',
                      'sha256', 'physical_candidate', 'inspect', '.json'):
        assert technical not in rendered
    assert '````\n' in rendered  # body backticks cannot close its display fence
    assert card['read_completed'] and card['inspect_completed']
    assert handoff.resolve(handle) == original  # subsequent inspect does not rewrite the read receipt
    assert not original['qualified_by_inspect']
    assert handoff.save_source(handle, inspection, 'synthetic-corpus') == result


def test_same_ref_different_observations_or_ranges_remain_distinct(tmp_path):
    handoff = CitationHandoff(source_cards=tmp_path)
    results = []
    for observation, start in ((0, 0), (1, 0), (1, 400)):
        handle, _, inspection = material(handoff, observation=observation, start=start)
        saved = handoff.save_source(handle, inspection, 'synthetic-corpus')
        results.append(json.loads(Path(saved['metadata_path']).read_bytes()))
    assert len(list(tmp_path.glob('*.json'))) == 3
    assert len({r['evidence_ref'] for r in results}) == 1
    assert [(r['observation_index'], r['range']['start']) for r in results] == [(0, 0), (1, 0), (1, 400)]


def test_equal_text_different_physical_refs_is_not_deduplicated(tmp_path):
    handoff = CitationHandoff(source_cards=tmp_path)
    paths = []
    for ref in ('synthetic:physical:a', 'synthetic:physical:b'):
        handle, _, inspection = material(handoff, ref=ref)
        paths.append(handoff.save_source(handle, inspection, 'synthetic-corpus')['metadata_path'])
    cards = [json.loads(Path(p).read_bytes()) for p in paths]
    assert paths[0] != paths[1]
    assert cards[0]['quoted_text'] == cards[1]['quoted_text']
    assert cards[0]['evidence_ref'] != cards[1]['evidence_ref']


def test_wrong_ref_expired_handle_and_non_read_locators_cannot_save(tmp_path):
    handoff = CitationHandoff(source_cards=tmp_path)
    handle, _, inspection = material(handoff)
    with pytest.raises(ValueError, match='mismatch'):
        handoff.save_source(handle, {**inspection, 'evidence_ref': 'synthetic:wrong'}, 'synthetic-corpus')
    with pytest.raises(ValueError, match='different-connection'):
        CitationHandoff(source_cards=tmp_path).save_source(handle, inspection, 'synthetic-corpus')
    with pytest.raises(ValueError, match='scoped read handle'):
        handoff.save_source(inspection['evidence_ref'], inspection, 'synthetic-corpus')
    for operation, locator in (('search', False), ('inspect', False), ('read', True)):
        out = handoff.attach({'status': 'ok', 'data': {'evidence_ref': inspection['evidence_ref'],
            'raw_text': '合成定位', 'locator_only': locator}}, operation)
        with pytest.raises(ValueError, match='actually read text'):
            handoff.save_source(out['data']['citation_handle'], inspection, 'synthetic-corpus')
    assert not list(tmp_path.iterdir())


def test_tampered_existing_card_is_never_overwritten(tmp_path):
    handoff = CitationHandoff(source_cards=tmp_path)
    handle, _, inspection = material(handoff)
    result = handoff.save_source(handle, inspection, 'synthetic-corpus')
    path = Path(result['metadata_path']); path.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='never overwritten'):
        handoff.save_source(handle, inspection, 'synthetic-corpus')
    assert path.read_bytes() == b'tampered'


def test_saved_dialogue_can_be_read_back_after_connection_restart(tmp_path):
    q, _, ref = populated_fixture()
    handoff = CitationHandoff(source_cards=tmp_path)
    page = handoff.attach({'status': 'ok', 'data': q.read(q.corpus_ref, ref,
        view='dialogue', detail='reading', limit=1)}, 'read', compact=True)
    handle = page['data']['nodes'][0]['texts'][0]['citation_handle']
    exact = handoff.resolve(handle)['evidence_ref']
    inspected = q.inspect(q.corpus_ref, exact)
    saved = handoff.save_source(handle, inspected, q.corpus_ref)
    card = json.loads(Path(saved['metadata_path']).read_bytes())
    with pytest.raises(ValueError, match='different-connection'):
        CitationHandoff().resolve(handle)
    assert q.read(q.corpus_ref, card['evidence_ref'])['raw_text'] == card['quoted_text']
    assert q.inspect(q.corpus_ref, card['evidence_ref'])['text_provenance'] == card['inspection']['text_provenance']


def test_valid_handle_does_not_prove_a_claim(tmp_path):
    handoff = CitationHandoff(source_cards=tmp_path)
    handle, _, inspection = material(handoff)
    saved = handoff.save_source(handle, inspection, 'synthetic-corpus')
    card = json.loads(Path(saved['metadata_path']).read_bytes())
    assert card['claim_support'].startswith('NOT_AUTOMATICALLY_CHECKED')
    assert card['inspection']['text_proof']['relation_status'] == 'physical_candidate'


def test_disabled_card_delivery_is_explicit_and_creates_nothing():
    handoff = CitationHandoff()
    handle, _, inspected = material(handoff)
    with pytest.raises(ValueError, match='not configured'):
        handoff.save_source(handle, inspected, 'synthetic-corpus')


def test_physical_read_card_uses_only_its_exact_anchor_locale(tmp_path):
    handoff = CitationHandoff(source_cards=tmp_path)
    q, _, ref = populated_fixture()
    page = q.read(q.corpus_ref, ref, view='dialogue', limit=1)
    exact = page['nodes'][0]['texts'][0]['evidence_ref']
    raw = q.read(q.corpus_ref, exact)
    assert 'locale' not in raw  # locale is explicitly present on its anchor context
    out = handoff.attach({'status': 'ok', 'data': raw}, 'read')
    saved = handoff.save_source(out['data']['citation_handle'], q.inspect(q.corpus_ref, exact), q.corpus_ref)
    assert json.loads(Path(saved['metadata_path']).read_bytes())['locale'] == 'CHS'


def test_collection_keeps_selected_order_ranges_and_independent_identity(tmp_path):
    handoff = CitationHandoff(source_cards=tmp_path)
    selections = [material(handoff, ref=ref, observation=i, start=i*400)
                  for i, ref in enumerate(('synthetic:a', 'synthetic:b', 'synthetic:a'))]
    saved = handoff.save_sources([(h, inspection) for h, _, inspection in selections], 'synthetic-corpus')
    value = json.loads(Path(saved['metadata_path']).read_bytes())
    assert [e['evidence_ref'] for e in value['entries']] == ['synthetic:a', 'synthetic:b', 'synthetic:a']
    assert [e['range']['start'] for e in value['entries']] == [0, 400, 800]
    assert all(e['inspection']['text_proof']['relation_status']=='physical_candidate' for e in value['entries'])
    rendered = Path(saved['markdown_path']).read_text(encoding='utf-8')
    assert rendered.count(selections[0][1]['raw_text']) == 3  # equal text is not identity deduplication
    assert len(list(tmp_path.glob('*.md'))) == len(list(tmp_path.glob('*.json'))) == 1


def test_collection_validates_every_selection_before_writing(tmp_path):
    handoff = CitationHandoff(source_cards=tmp_path/'cards')
    handle, _, inspection = material(handoff)
    with pytest.raises(ValueError, match='mismatch'):
        handoff.save_sources([(handle, inspection), (handle, {**inspection,'evidence_ref':'wrong'})], 'synthetic-corpus')
    assert not (tmp_path/'cards').exists()


@pytest.mark.parametrize('placement', ['source_result', 'reading_context'])
def test_resolved_book_and_document_display_titles_survive_in_cards(tmp_path, placement):
    h=CitationHandoff(source_cards=tmp_path)
    handle, _, inspection=material(h)
    record=h.resolve(handle)
    context={'display_title':'合成书籍标题','display_subtitle':'合成章节',
             'source_file':'private/source.json'}
    if placement=='source_result':record['source_result']['source_context']=context
    else:record['reading_context'].update(context)
    from genshin_lore.source_cards import save_card
    result=save_card(tmp_path,record,inspection,'synthetic-corpus')
    rendered=Path(result['markdown_path']).read_text(encoding='utf-8')
    assert '合成书籍标题' in rendered and '合成章节' in rendered
    assert 'private/source.json' not in rendered


def test_read_mapping_inherits_resolved_titles_without_technical_label_fallback(tmp_path):
    h=CitationHandoff(source_cards=tmp_path)
    _, body, inspection=material(h)
    out=h.attach({'status':'ok','data':{'source_context':{
        'display_title':'合成卷名','display_subtitle':'合成章节'},'context':[body]}},'read',compact=True)
    handle=out['data']['context'][0]['citation_handle']
    saved=h.save_source(handle,inspection,'synthetic-corpus')
    rendered=Path(saved['markdown_path']).read_text(encoding='utf-8')
    assert '合成卷名 · 合成章节' in rendered
