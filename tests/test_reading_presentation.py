"""Presentation may group/shorten, never change source identity or proof states."""
import copy
import json

import pytest

from test_codex_dialogue import populated_fixture
from genshin_lore.dialogue.presentation import reading_card, page_groups, search_reading, source_link, character_reading, text_groups
from genshin_lore.citation_handoff import CitationHandoff, bind_capture, render_answer


def test_compact_corpus_description_keeps_authority_and_exposes_full_details():
    from genshin_lore.dialogue.presentation import corpus_overview
    full = {'corpus_ref': 'synthetic-corpus', 'snapshot': {'id': 'synthetic-snapshot'},
        'status': 'accepted_bounded', 'historical_contract_gaps': 8,
        'outer_corpus_complete': False, 'terminal_unresolved': ['synthetic gap'],
        'search_semantics': {'default_mode': 'literal', 'modes': ['literal', 'semantic'],
                             'semantic_coverage': {'large_scope': ['synthetic scope'] * 500}},
        'components': [{'artifact_source_key': str(i), 'overall_projection_complete': i % 2 == 0,
                        'available': i != 3, 'searchable_tables': ['synthetic table'] * 100} for i in range(35)]}
    before = copy.deepcopy(full)
    overview = corpus_overview(full)
    assert full == before
    for key in ('corpus_ref', 'snapshot', 'status', 'historical_contract_gaps', 'outer_corpus_complete', 'terminal_unresolved'):
        assert overview[key] == full[key]
    assert overview['component_summary']['count'] == 35
    assert overview['component_summary']['unavailable'] == ['3']
    assert overview['component_summary']['partial_projection_count'] == 17
    assert overview['search_semantics']['default_mode'] == 'literal'
    assert overview['details_action']['arguments'] == {'corpus_ref': 'synthetic-corpus', 'detail': 'full'}
    assert len(json.dumps(overview)) < len(json.dumps(full)) / 4


def test_person_display_groups_separate_mentions_from_source_labelled_speech():
    q, _, ref = populated_fixture()
    result = {'items': [
        {'evidence_ref': ref, 'family': 'npc_archive', 'owner_field': 'nameTextMapHash', 'snippet': '合成角色甲'},
        {'evidence_ref': 'synthetic:mention', 'owner_field': 'DialogNormal', 'snippet': '合成角色甲被提到'},
        {'evidence_ref': 'synthetic:title', 'owner_field': 'MainQuestTitle', 'snippet': '合成任务标题'}]}
    original = copy.deepcopy(result)
    view = search_reading(q, result, '合成角色甲', 'CHS')
    groups = view['groups']
    assert groups['categories']['keyword_body_matches'] == [{'item_index': 1}]
    assert groups['categories']['name_matches'] == [{'item_index': 0}]
    assert groups['categories']['source_labelled_speech_candidates']
    assert groups['by_source_title']
    assert not groups['logical_identity_proven']
    assert result == original


def test_reading_page_is_smaller_and_preserves_full_text_refs_and_proof():
    q, _, ref = populated_fixture()
    full = q.read(q.corpus_ref, ref, view='dialogue', limit=2)
    compact = q.read(q.corpus_ref, ref, view='dialogue', limit=2, detail='reading')
    assert len(json.dumps(compact).encode()) < len(json.dumps(full).encode())
    assert compact['page_info']['returned_nodes'] == compact['delivered_count'] == 2
    assert compact['page_info']['requested_nodes'] == 2
    assert compact['page_info']['stop_reason'] == 'requested_node_limit'
    for a, b in zip(full['nodes'], compact['nodes']):
        for original, view in zip(a['texts'], b['texts']):
            assert view['raw_text'] == original['raw_text']
            assert view['evidence_ref'] == original['evidence_ref']
            assert view['text_proof'] == original['text_proof']
            assert 'provenance' not in view
            assert q.inspect(q.corpus_ref, view['evidence_ref'])['text_provenance'] == original['provenance']


def test_reading_pagination_covers_each_physical_occurrence_and_binds_detail():
    q, _, ref = populated_fixture()
    cursor = None; nodes = []
    while True:
        page = q.read(q.corpus_ref, ref, view='dialogue', detail='reading', limit=2, cursor=cursor)
        nodes.extend(n['node'] for n in page['nodes'])
        cursor = page['next_cursor']
        if not cursor: break
        with pytest.raises(ValueError, match='cursor scope'):
            q.read(q.corpus_ref, ref, view='dialogue', detail='full', limit=2, cursor=cursor)
    assert nodes == ['node-' + str(i) for i in range(0, 13, 2)]
    assert len(nodes) == len(set(nodes))
    assert page['scope_complete'] and page['partial']
    assert page['page_info']['stop_reason'] == 'matching_scope_exhausted'


def test_exact_name_panel_reads_bodies_without_changing_literal_results():
    q, _, ref = populated_fixture()
    result = {'items': [{'evidence_ref': ref, 'family': 'npc_archive',
                        'owner_field': 'nameTextMapHash', 'snippet': '合成角色甲'}],
              'next_cursor': 'unchanged cursor'}
    before = copy.deepcopy(result)
    view = search_reading(q, result, '合成角色甲', 'CHS')
    assert result == before
    assert view['body_previews'][0]['raw_text'] == '合成发言0'
    assert view['name_matches'][0]['item_index'] == 0
    assert view['body_previews'][0]['speaker_label'] == '合成角色甲'
    assert view['body_previews'][0]['text_proof']['owner_role_status'] == 'new_wrapper_role_unproven'
    assert not view['logical_identity_proven']
    assert view['next_actions'][0]['arguments']['evidence_ref'] == ref


def test_display_groups_keep_equal_text_at_separate_physical_occurrences():
    q, _, ref = populated_fixture(equal_bodies=True)
    page = q.read(q.corpus_ref, ref, view='dialogue', limit=2, detail='reading')
    groups = page_groups(page['nodes'])
    assert len(groups) == 1 and len(groups[0]['occurrences']) == 2
    assert page['nodes'][0]['texts'][0]['evidence_ref'] != page['nodes'][1]['texts'][0]['evidence_ref']
    assert groups[0]['logical_identity_proven'] is False
    assert 'text' not in groups[0]  # display references must not duplicate large bodies
    assert groups[0]['representative'] == {'node_index': 0, 'text_index': 0}


def test_links_are_only_real_pinned_source_file_links_not_fake_exact_anchors():
    text = {'provenance': {'snapshot': {'id': 'anime-game-data:7.1:' + 'a'*40},
                           'source_file': 'folder/中文 file.json', 'source_pointer': '/1'}}
    link = source_link(text)
    assert link['url'].startswith('https://github.com/DimbreathBot/AnimeGameData/blob/'+'a'*40+'/')
    assert '%E4%B8%AD' in link['url'] and '%20' in link['url']
    assert 'source_file_only' in link['scope']
    assert reading_card(text)['source_link'] == link
    assert source_link({'provenance': {'snapshot': {'id': 'unknown'}, 'source_file': 'file.json'}}) is None


def test_memory_handles_are_scoped_resolvable_and_do_not_need_renderer_files():
    q, _, ref = populated_fixture()
    page = q.read(q.corpus_ref, ref, view='dialogue', limit=1, detail='reading')
    handoff = CitationHandoff()
    result = handoff.attach({'status': 'ok', 'data': page}, 'read')
    item = result['data']['nodes'][0]['texts'][0]
    assert item['citation_number'].startswith('R1:E')
    assert handoff.resolve(item['citation_handle'])['evidence_ref'] == item['evidence_ref']
    with pytest.raises(ValueError, match='different-connection'):
        CitationHandoff().resolve(item['citation_handle'])
    assert 'connection-memory' in result['citation_handoff']['delivery']
    assert 'provenance' not in item


def test_invalid_reading_detail_is_not_silently_accepted():
    q, _, ref = populated_fixture()
    with pytest.raises(ValueError, match='detail must'):
        q.read(q.corpus_ref, ref, view='dialogue', detail='unknown', limit=2)


def test_compact_wire_result_binds_original_identity_and_inspect_before_render(tmp_path):
    q, _, ref = populated_fixture()
    page = q.read(q.corpus_ref, ref, view='dialogue', limit=2, detail='reading')
    original = copy.deepcopy(page)
    handoff = CitationHandoff(tmp_path)
    result = handoff.attach({'status': 'ok', 'data': page}, 'read', compact=True)
    assert page == original  # private mappings and caller data remain immutable
    body = result['data']['nodes'][0]['texts'][0]
    assert 'evidence_ref' not in body
    handle = body['citation_handle']
    exact = original['nodes'][0]['texts'][0]['evidence_ref']
    assert handoff.resolve(handle)['evidence_ref'] == exact
    calls = [{'result': result, 'status': 'completed', 'item_id': 'read', 'server': 'fixture', 'tool': 'read_evidence'}]
    bound = bind_capture({'tool_calls': calls, 'stdout_sha256': 'fixture'}, tmp_path)
    assert render_answer('合成说明。'+handle, bound)['status'] == 'DELIVERY_FAILED'
    inspected = handoff.attach({'status': 'ok', 'data': q.inspect(q.corpus_ref, exact)}, 'inspect', inspected_ref=exact)
    calls.append({'result': inspected, 'status': 'completed', 'item_id': 'inspect', 'server': 'fixture', 'tool': 'inspect_evidence'})
    bound = bind_capture({'tool_calls': calls, 'stdout_sha256': 'fixture'}, tmp_path)
    rendered = render_answer('合成说明。'+handle, bound)
    assert rendered['status'] == 'DELIVERED'
    assert rendered['citation_resolution'][0]['evidence_ref'] == exact
    assert rendered['citation_resolution'][0]['quoted_text'] == body['raw_text']
    assert handoff.resolve(handle)['source_result']['text_proof'] == original['nodes'][0]['texts'][0]['text_proof']


def test_filtered_search_does_not_auto_expand_beyond_filter(monkeypatch):
    q, _, ref = populated_fixture()
    def unexpected(*args, **kwargs):
        raise AssertionError('filtered reading panel must not auto-read other families')
    monkeypatch.setattr(q, 'read', unexpected)
    result = {'items': [{'evidence_ref': ref, 'family': 'npc_archive',
                        'owner_field': 'nameTextMapHash', 'snippet': '合成角色甲'}]}
    view = search_reading(q, result, '合成角色甲', 'CHS', filters=True)
    assert view['body_previews'] == []
    assert view['next_actions'] and view['name_matches']


def test_payload_uses_only_one_sdk_representation():
    from scripts.check import payload
    class Result:
        is_error = False
        structured_content = {}  # even an empty structured result is present
        @property
        def content(self):
            raise AssertionError('duplicate content representation consumed')
    assert payload(Result()) == {}


@pytest.mark.parametrize('provenance', [None, 'unavailable', {'snapshot': None}, {'snapshot': {'id': None}}])
def test_unavailable_optional_link_metadata_has_no_invented_url(provenance):
    assert source_link({'provenance': provenance}) is None


def test_character_preview_uses_declared_family_and_bounded_source_navigation(monkeypatch):
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr('genshin_lore.research_literal.literal_search',
        lambda svc, query, locale, families, *a, **kw: svc.search(svc.corpus_ref, query, locale, families, limit=3))
    def search(corpus, query, locale, families, *, limit):
        calls.append(('search', query, families, limit))
        return {'items': [{'snippet': query, 'owner_field': 'nameTextMapHash', 'evidence_ref': 'exact-anchor'}]}
    def follow(corpus, ref, *, relation_kind, limit):
        calls.append(('follow', ref, relation_kind, limit))
        return {'items': [{'status': 'physical_candidate', 'candidates': [
            {'target_evidence_ref': target} for target in ('exact-body-a', 'exact-body-a', 'exact-body-b', 'must-not-read')]}]}
    def read(corpus, ref, *, limit):
        calls.append(('read', ref, limit))
        return {'context': [
            {'evidence_ref': ref+'-title', 'raw_text': '合成标题不是正文', 'locale': 'CHS', 'owner_field': 'storyTitleTextMapHash'},
            {'evidence_ref': ref, 'raw_text': '合成正文相同但物理来源不同', 'locale': 'CHS', 'owner_field': 'storyContextTextMapHash'}]}
    service = SimpleNamespace(corpus_ref='fixture', components={
        'qualified': {'adapter': 'character_text', 'family': 'declared-character-family'}},
        search=search, follow=follow, read=read)
    bodies, actions = character_reading(service, '合成人物', 'CHS')
    assert calls == [('search', '合成人物', ['declared-character-family'], 3),
                     ('follow', 'exact-anchor', 'character_story_voice_candidate', 2),
                     ('read', 'exact-body-a', 50), ('read', 'exact-body-b', 50)]
    assert len(bodies) == 2 and bodies[0]['evidence_ref'] != bodies[1]['evidence_ref']
    assert all(b['owner_field'] == 'storyContextTextMapHash' and b['source_title_candidates'] for b in bodies)
    assert all(b['relation_status'] == 'physical_candidate' and not b['logical_identity_proven'] for b in bodies)
    assert actions[0]['arguments']['evidence_ref'] == 'exact-anchor'


def test_character_near_name_is_not_promoted_to_an_exact_subject(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('genshin_lore.research_literal.literal_search',
        lambda svc, query, locale, families, *a, **kw: svc.search(svc.corpus_ref, query, locale, families, limit=3))
    service = SimpleNamespace(corpus_ref='fixture', components={
        'qualified': {'adapter': 'character_text', 'family': 'declared'}},
        search=lambda *a, **k: {'items': [{'snippet': '合成角色的同名近似项', 'owner_field': 'nameTextMapHash'}]})
    assert character_reading(service, '合成角色', 'CHS') == ([], [])


def test_profile_hit_without_a_name_on_this_page_still_offers_character_material(monkeypatch):
    from genshin_lore.dialogue import presentation
    q, _, ref = populated_fixture()
    calls=[]
    def character(service, query, locale):
        calls.append((query,locale))
        return ([{'evidence_ref':'synthetic:story','raw_text':'合成故事正文',
                  'material_type':'character_story_or_profile','discovery_basis':'Character navigation'}],[])
    monkeypatch.setattr(presentation,'character_reading',character)
    result={'items':[{'evidence_ref':ref,'owner_field':'descTextMapHash','snippet':'合成人物介绍'}]}
    view=search_reading(q,result,'合成人物','CHS')
    assert calls==[('合成人物','CHS')]
    assert view['body_previews'][0]['raw_text']=='合成故事正文'
    assert view['groups']['by_material_type'][0]['material_type']=='character_story_or_profile'
    calls.clear()
    assert not search_reading(q,result,'合成人物','CHS',filters=True)['body_previews']
    assert not calls


def test_compact_physical_read_keeps_body_once_and_receipt_exact(tmp_path):
    q, _, ref=populated_fixture()
    page=q.read(q.corpus_ref,ref,view='dialogue',limit=1)
    exact=page['nodes'][0]['texts'][0]['evidence_ref']
    raw=q.read(q.corpus_ref,exact)
    original=copy.deepcopy(raw)
    h=CitationHandoff(tmp_path)
    out=h.attach({'status':'ok','data':raw},'read',compact=True)
    assert raw==original
    assert out['data']['raw_text']==original['raw_text']
    assert 'provenance' not in out['data']
    matched=[c for c in out['data']['context'] if c.get('body_in_anchor')]
    assert matched and all('raw_text' not in c for c in matched)
    record=h.resolve(out['data']['citation_handle'])
    assert record['source_result']==original and record['evidence_ref']==exact
    assert record['quoted_text']==original['raw_text']


def test_compact_anchor_does_not_hide_another_observation_of_the_same_ref():
    data={'evidence_ref':'synthetic:shared','raw_text':'合成正文','observation_index':0,
          'context':[{'evidence_ref':'synthetic:shared','raw_text':'合成正文','observation_index':1}]}
    delivered=CitationHandoff().attach({'status':'ok','data':data},'read',compact=True)
    assert delivered['data']['context'][0]['raw_text']=='合成正文'


def test_compact_search_preserves_hits_and_binds_repeated_locators_without_read_credit(tmp_path):
    exact, container = 'synthetic:text:' + 't'*900, 'synthetic:container:' + 'c'*900
    item = {'evidence_ref': exact, 'provenance_ref': exact, 'container_ref': container,
            'snippet': '合成摘录', 'text_proof': {'owner': 'ambiguous'}, 'locale': 'CHS'}
    data = {'items': [item], 'next_cursor': 'opaque-cursor', 'partial': True,
            'reading_view': {'body_previews': [{**item, 'raw_text': '合成正文'}],
                'next_actions': [{'tool': 'read_evidence', 'arguments': {
                    'corpus_ref': 'synthetic', 'evidence_ref': container, 'limit': 2}}]}}
    original = copy.deepcopy(data)
    handoff = CitationHandoff(tmp_path)
    out = handoff.attach({'status': 'ok', 'data': data}, 'search', compact=True)
    view = out['data']
    assert data == original
    assert view['next_cursor'] == data['next_cursor'] and view['partial']
    assert view['reading_view']['hit_identity_order_and_cursor_unchanged']
    assert not view['reading_view']['original_items_and_cursor_unchanged']
    assert view['items'][0]['evidence_ref'] == exact
    assert view['items'][0]['snippet'] == item['snippet']
    assert view['items'][0]['text_proof'] == item['text_proof']
    for key, expected in [('container_handle', container), ('provenance_handle', exact)]:
        record = handoff.resolve(view['items'][0][key])
        assert record['evidence_ref'] == expected and not record['material_read']
    action = view['reading_view']['next_actions'][0]['arguments']
    assert set(action) == {'corpus_ref', 'evidence_ref', 'limit'}
    assert handoff.resolve(action['evidence_ref'])['evidence_ref'] == container
    preview = view['reading_view']['body_previews'][0]
    assert 'evidence_ref' not in preview
    assert handoff.resolve(preview['citation_handle'])['source_result']['raw_text'] == '合成正文'
    assert not handoff.resolve(preview['citation_handle'])['material_read']
    assert len(json.dumps(view)) < len(json.dumps(original))


def test_compact_navigation_preserves_order_status_and_original_proof():
    data = {'items': [{'source_evidence_ref': 'synthetic:source', 'status': 'physical_candidate',
        'target_refs': ['synthetic:b', 'synthetic:a', 'synthetic:b'],
        'candidates': [{'target_evidence_ref': 'synthetic:c', 'status': 'ambiguous'}],
        'logical_identity_proven': False}], 'next_cursor': 'opaque', 'partial': True}
    original = copy.deepcopy(data)
    h = CitationHandoff()
    out = h.attach({'status': 'ok', 'data': data}, 'follow', compact=True)['data']
    item = out['items'][0]
    assert data == original
    assert [h.resolve(t)['evidence_ref'] for t in item['target_handles']] == original['items'][0]['target_refs']
    assert item['target_handles'][0] == item['target_handles'][2]  # same locator, no body/observation merge
    assert item['status'] == 'physical_candidate' and not item['logical_identity_proven']
    candidate = item['candidates'][0]
    assert candidate['status'] == 'ambiguous'
    record = h.resolve(candidate['target_citation_handle'])
    assert record['evidence_ref'] == 'synthetic:c' and not record['material_read']
    assert out['next_cursor'] == 'opaque' and out['partial']


def test_compact_locators_do_not_reuse_a_body_observation_handle():
    h = CitationHandoff()
    out = h.attach({'status': 'ok', 'data': {'context': [
        {'evidence_ref': 'synthetic:shared', 'raw_text': '片段甲', 'observation_index': 1},
        {'evidence_ref': 'synthetic:shared', 'raw_text': '片段乙', 'observation_index': 2}],
        'container_ref': 'synthetic:shared'}}, 'read', compact=True)['data']
    handles = [x['citation_handle'] for x in out['context']] + [out['container_handle']]
    assert len(set(handles)) == 3
    assert [h.resolve(t)['observation_index'] for t in handles] == [1, 2, None]
    assert h.resolve(handles[-1])['material_read'] is False


def test_compact_byte_boundary_has_resumable_exact_coverage(monkeypatch):
    from genshin_lore.dialogue import presentation
    q, _, ref = populated_fixture()
    page = q.read(q.corpus_ref, ref, view='dialogue', limit=20, detail='reading')
    # Interior nodes have two neighbour locators; size the test cap for the
    # largest single node, not just the shorter boundary node.
    single = max(len(json.dumps({'nodes': [node], 'name_candidates': page['name_candidates'],
        'source_titles': page['source_titles']}, ensure_ascii=False).encode()) for node in page['nodes'])
    monkeypatch.setattr(presentation, 'READING_PAGE_BYTES', single+1)
    cursor = None; nodes = []
    while True:
        page = q.read(q.corpus_ref, ref, view='dialogue', limit=20, detail='reading', cursor=cursor)
        nodes.extend(n['node'] for n in page['nodes'])
        cursor = page['next_cursor']
        if not cursor: break
        assert page['page_info']['stop_reason'] == 'response_byte_limit'
        assert page['page_info']['returned_nodes'] < 20
    assert nodes == ['node-'+str(i) for i in range(0, 13, 2)]


@pytest.mark.parametrize('keyword', ['标题', '合成任务标题'])
def test_title_only_search_never_advertises_prose_or_dialogue(keyword):
    q, _, _ = populated_fixture()
    q.unavailable = []
    q.db.execute("INSERT INTO research_text_fts(research_text_fts) VALUES('rebuild')")
    result = q.search(q.corpus_ref, keyword, 'CHS', limit=20)
    original = copy.deepcopy(result)
    assert result['items'] and all('Title' in h['owner_field'] for h in result['items'])
    view = search_reading(q, result, keyword, 'CHS')
    assert view['body_item_indexes'] == [] and view['body_previews'] == []
    assert [t['item_index'] for t in view['title_matches']] == list(range(len(result['items'])))
    assert view['next_actions'] and view['next_actions'][0]['tool'] == 'read_evidence'
    assert result == original


def test_body_and_unclassified_metadata_keep_separate_display_indexes():
    q, _, _ = populated_fixture()
    result = {'items': [
        {'evidence_ref': 'body', 'owner_field': 'DialogNormal', 'snippet': '合成对白'},
        {'evidence_ref': 'title', 'owner_field': 'MainQuestTitle', 'snippet': '合成标题'},
        {'evidence_ref': 'opaque', 'owner_field': 'unmapped_slot', 'snippet': '未知角色'},
        {'evidence_ref': 'label', 'owner_field': 'SpeakerKnown', 'snippet': '合成角色'}]}
    view = search_reading(q, result, '合成', 'CHS')
    assert view['body_item_indexes'] == [0]
    assert [v['item_index'] for v in view['title_matches']] == [1]
    assert [v['item_index'] for v in view['other_matches']] == [2]
    assert [v['item_index'] for v in view['name_matches']] == [3]


def test_search_groups_reference_previews_without_copying_bodies_or_merging_refs():
    previews = [{'raw_text': '合成同文'*10_000, 'locale': 'CHS', 'evidence_ref': ref}
                for ref in ('physical-a', 'physical-b')]
    groups = text_groups(previews)
    assert len(groups) == 1 and groups[0]['representative'] == {'preview_index': 0}
    assert groups[0]['occurrences'] == [{'preview_index': 0}, {'preview_index': 1}]
    assert 'text' not in groups[0] and len(json.dumps(groups).encode()) < 1000
    assert not groups[0]['logical_identity_proven']
    assert previews[0]['evidence_ref'] != previews[1]['evidence_ref']


def test_over_budget_handoff_does_not_commit_handles_or_receipts(tmp_path):
    handoff = CitationHandoff(tmp_path)
    value = {'status': 'ok', 'data': {'evidence_ref': 'exact-synthetic', 'raw_text': 'x'*200}}
    original = copy.deepcopy(value)
    assert handoff.attach(value, 'search', max_bytes=100) is None
    assert handoff.counter == 0 and not handoff._records and not list(tmp_path.iterdir())
    assert value == original
    result = handoff.attach(value, 'search', max_bytes=10_000)
    assert result['citation_handoff']['request'] == 'R1'
    assert len(list(tmp_path.iterdir())) == 1
    assert handoff.resolve(result['data']['citation_handle'])['evidence_ref'] == 'exact-synthetic'


def test_compact_neighbour_locator_requires_actual_body_read_not_just_inspect(tmp_path):
    q, _, ref = populated_fixture()
    h = CitationHandoff(tmp_path)
    page = h.attach({'status': 'ok', 'data': q.read(q.corpus_ref, ref, view='dialogue', limit=1, detail='reading')}, 'read', compact=True)
    locator = page['data']['nodes'][0]['source_context']['after'][0]['texts'][0]
    handle = locator['citation_handle']; record = h.resolve(handle); exact = record['evidence_ref']
    assert locator['locator_only'] and record['quoted_text'] is None and not record['material_read']
    inspected = h.attach({'status': 'ok', 'data': q.inspect(q.corpus_ref, exact)}, 'inspect', inspected_ref=exact)
    calls = [{'result': out, 'status': 'completed', 'item_id': str(i), 'server': 'fixture', 'tool': tool}
             for i, (out, tool) in enumerate(((page, 'read_evidence'), (inspected, 'inspect_evidence')))]
    def bound():
        return bind_capture({'tool_calls': calls, 'stdout_sha256': 'fixture'}, tmp_path)
    assert render_answer('合成陈述。'+handle, bound())['status'] == 'DELIVERY_FAILED'
    body = h.attach({'status': 'ok', 'data': q.read(q.corpus_ref, exact)}, 'read')
    calls.append({'result': body, 'status': 'completed', 'item_id': 'body', 'server': 'fixture', 'tool': 'read_evidence'})
    body_handle = body['data']['citation_handle']
    assert h.resolve(body_handle)['quoted_text'] == '合成发言1'
    assert render_answer('合成发言1。'+body_handle, bound())['status'] == 'DELIVERED'
    # The existing ledger permits a locator citation after THIS exact target
    # has actually been read and inspected, without rewriting its record.
    assert render_answer('合成陈述。'+handle, bound())['status'] == 'DELIVERED'
    assert not h.resolve(handle)['material_read']


@pytest.mark.parametrize('operation', ['read', 'query'])
def test_navigation_targets_are_unread_locators_even_inside_read_or_query(operation):
    h = CitationHandoff()
    out = h.attach({'status': 'ok', 'data': {'edges': [{'target_evidence_ref': 'synthetic-target'}]}}, operation)
    record = h.resolve(out['data']['edges'][0]['target_citation_handle'])
    assert record['source_result']['locator_only'] and not record['material_read']
    assert not record['qualified_by_inspect']


def test_optional_preview_never_hides_source_integrity_failure(monkeypatch):
    from genshin_lore.dialogue.context import DialogueContext
    q, _, ref = populated_fixture()
    def drift(*args, **kwargs):
        raise ValueError('Codex source bytes SHA drift')
    monkeypatch.setattr(DialogueContext, 'read', drift)
    result = {'items': [{'evidence_ref': ref, 'family': 'npc_archive',
                        'owner_field': 'nameTextMapHash', 'snippet': '合成角色甲'}]}
    with pytest.raises(ValueError, match='source bytes SHA drift'):
        search_reading(q, result, '合成角色甲', 'CHS')
