"""Presentation may group/shorten, never change source identity or proof states."""
import copy
import json

import pytest

from test_codex_dialogue import populated_fixture
from genshin_lore.dialogue.presentation import reading_card, page_groups, search_reading, source_link, character_reading
from genshin_lore.citation_handoff import CitationHandoff, bind_capture, render_answer


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


def test_character_preview_uses_declared_family_and_bounded_source_navigation():
    from types import SimpleNamespace
    calls = []
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


def test_character_near_name_is_not_promoted_to_an_exact_subject():
    from types import SimpleNamespace
    service = SimpleNamespace(corpus_ref='fixture', components={
        'qualified': {'adapter': 'character_text', 'family': 'declared'}},
        search=lambda *a, **k: {'items': [{'snippet': '合成角色的同名近似项', 'owner_field': 'nameTextMapHash'}]})
    assert character_reading(service, '合成角色', 'CHS') == ([], [])


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
