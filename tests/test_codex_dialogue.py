"""Synthetic source-labelled dialogue, not game text or inferred NPC identity."""
import hashlib
import json
import sqlite3

import pytest

from test_dialogue_context import fixture, insert, ROOT
from genshin_lore.dialogue import DialogueReadAdapter


def digest(value):
    return hashlib.sha256(value).hexdigest()


def codex_fixture(body_mentions_name=False, equal_bodies=False, legacy_atoms=False, item_type=None):
    q, _, _ = fixture()
    src = sqlite3.connect(':memory:'); src.row_factory = sqlite3.Row
    src.executescript((ROOT/'schemas/codexquest-7.1-v1.sql').read_text())
    q.sources['codex'] = src
    q.components['codex'] = {'adapter': 'codexquest_archive', 'family': 'codexquest_archive',
                              'run': 'run-codex', 'parser': 'parser', 'contract': 'contract'}
    insert(q.db, 'source_artifact', {'source_key': 'codex'})
    raw = {'title': {'textId': 1, 'kind': 'MainQuestTitle'}, 'sections': [{
        'title': {'textId': 2, 'kind': 'SubQuestTitle'}, 'nodes': []}]}
    slots = []
    def atom(pointer, label, text):
        number = len(slots)+1
        slots.append((pointer, label, text, number))
        return {'BNJEGIAOKGM': number, 'JOBGILDNLEL': label} if legacy_atoms else {'textId': number, 'DFKLAEHPOBE': label}
    raw['title'] = atom('/title', 'MainQuestTitle', '合成任务标题')
    raw['sections'][0]['title'] = atom('/sections/0/title', 'SubQuestTitle', '合成片段标题')
    for i in range(13):
        pointer = '/sections/0/nodes/'+str(i)
        raw['sections'][0]['nodes'].append({
            'itemType': item_type,
            'speaker': atom(pointer+'/speaker', 'SpeakerKnown', '合成角色甲' if i%2 == 0 else '合成角色乙'),
            'body': atom(pointer+'/body', 'DialogNormal', '合成角色甲' if body_mentions_name and i == 1
                         else '相同合成发言' if equal_bodies else '合成发言'+str(i))})
    encoded = json.dumps(raw, ensure_ascii=False).encode()
    seal = digest(encoded);q.r0_provenance['Codex.json'] = seal
    src.execute('INSERT INTO codex_root VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
        ('root', 'run-codex', 'Codex.json', 'blob', seal, encoded, 'rc_493' if legacy_atoms else 'new_without_type', '1', None,
         'present', 1, 'new_shape_unresolved'))
    return q, src, raw, slots, seal


def populated_fixture(**kwargs):
    # Kept separate so every physical candidate is mirrored in the real index.
    q, src, raw, slots, seal = codex_fixture(**kwargs)
    insert(q.db, 'research_container', {'id': 500, 'source_key': 'codex',
        'adapter_id': 'codexquest_archive:codex_root', 'evidence_family': 'codexquest_archive',
        'evidence_contract': 'contract', 'evidence_parser': 'parser', 'evidence_run': 'run-codex',
        'occurrence_id': 'root', 'container_kind': 'codex_root', 'source_table': 'codex_root',
        'pk_column': 'id', 'pk_value': 'root', 'source_path': 'Codex.json', 'source_pointer': '/',
        'source_file_sha256': seal})
    for i, node in enumerate(raw['sections'][0]['nodes']):
        encoded = json.dumps(node, ensure_ascii=False)
        src.execute('INSERT INTO codex_child VALUES(?,?,?,?,?,?,?)',
            ('node-'+str(i), 'root', 'node_like', '/sections/0/nodes/'+str(i), i, encoded, digest(encoded.encode())))
    for pointer, label, text, number in slots:
        slot = 'slot-'+str(number);candidate = 'candidate-'+str(number)
        src.execute('INSERT INTO codex_text_slot VALUES(?,?,?,?,?,?,?,?,?)',
            (slot, 'root', pointer, label, str(number), 'CHS', 'resolved_unique', 1, 'new_wrapper_role_unproven'))
        src.execute('INSERT INTO codex_text_candidate VALUES(?,?,?,?,?,?,?)',
            (candidate, slot, 'Text.json', number, '/'+str(number), text, digest(text.encode())))
        insert(q.db, 'research_text', {'id': 1000+number, 'container_id': 500, 'source_key': 'codex',
            'adapter_id': 'codexquest_archive:codex_text_candidate', 'evidence_family': 'codexquest_archive',
            'evidence_contract': 'contract', 'evidence_parser': 'parser', 'evidence_run': 'run-codex',
            'occurrence_id': 'root', 'child_id': candidate, 'source_table': 'codex_text_candidate',
            'pk_column': 'id', 'pk_value': candidate, 'text_column': 'raw_text', 'locale': 'CHS',
            'owner_field': label, 'ordinal': number, 'pointer': pointer, 'text_source_path': 'Text.json',
            'text_source_pointer': '/'+str(number), 'text_source_file_sha256': 'source',
            'physical_pair_ordinal': number, 'raw_text': text, 'text_sha256': digest(text.encode())})
    parent = q.db.execute('SELECT * FROM research_container WHERE id=200').fetchone()
    return q, src, q._container_ref(parent)


def test_npc_to_source_labelled_speeches_with_titles_and_neighbours():
    q, _, ref = populated_fixture()
    result = q.read(q.corpus_ref, ref, view='dialogue', limit=2)
    assert len(result['nodes']) == 2 and result['next_cursor']
    node = result['nodes'][0]
    assert node['texts'][0]['raw_text'] == '合成发言0'
    assert node['speaker']['display_name'] == '合成角色甲'
    assert node['speaker']['logical_identity_proven'] is False
    assert node['texts'][0]['text_proof']['owner_role_status'] == 'new_wrapper_role_unproven'
    assert [t['raw_text'] for t in node['source_context']['titles']] == ['合成任务标题', '合成片段标题']
    assert node['texts'][0]['citation_label'] == '合成任务标题 / 合成片段标题'
    assert node['source_context']['display_title'] == '合成任务标题'
    assert node['source_context']['after'][0]['texts'][0]['raw_text'] == '合成发言1'
    assert result['partial'] and not result['corpus_complete']


def test_speaker_pages_cover_each_physical_occurrence_and_eof():
    q, _, ref = populated_fixture();cursor = None;nodes = []
    while True:
        page = q.read(q.corpus_ref, ref, view='dialogue', limit=2, cursor=cursor)
        nodes.extend(n['node'] for n in page['nodes']);cursor = page['next_cursor']
        if cursor is None:break
    assert nodes == ['node-'+str(i) for i in range(0, 13, 2)]
    assert page['scope_complete'] and page['partial']  # only this source-label scope


def test_body_anchor_reads_actual_before_after_in_numeric_source_order():
    q, _, ref = populated_fixture()
    page = q.read(q.corpus_ref, ref, view='dialogue', limit=1)
    body = page['nodes'][0]['source_context']['after'][0]['texts'][0]
    result = q.read(q.corpus_ref, body['evidence_ref'], view='dialogue', limit=1)
    node = result['nodes'][0]
    assert node['texts'][0]['raw_text'] == '合成发言1'
    assert node['source_context']['before'][0]['texts'][0]['raw_text'] == '合成发言0'
    assert node['source_context']['after'][0]['texts'][0]['raw_text'] == '合成发言2'
    for material in [*node['texts'], *node['speaker']['names'], *node['source_context']['titles']]:
        assert q.read(q.corpus_ref, material['evidence_ref'])['raw_text'] == material['raw_text']
        assert q.inspect(q.corpus_ref, material['evidence_ref'])['text_provenance'] == material['provenance']


def test_released_adapter_uses_the_same_codex_reader_without_changing_base():
    q, _, ref = populated_fixture()
    assert DialogueReadAdapter(q).read(q.corpus_ref, ref, view='dialogue', limit=1)['nodes']


def test_cursor_anchor_locale_limit_and_cross_view_scope():
    q, _, ref = populated_fixture();cursor=q.read(q.corpus_ref,ref,view='dialogue',limit=1)['next_cursor']
    other=q._text_ref(q.db.execute("SELECT * FROM research_text WHERE owner_field='DialogNormal' LIMIT 1").fetchone())
    for changes in ({'limit': 2}, {'locale': 'EN'}, {'evidence_ref': other}):
        args=dict(corpus_ref=q.corpus_ref,evidence_ref=ref,view='dialogue',limit=1,cursor=cursor)
        with pytest.raises(ValueError, match='cursor scope'):q.read(**{**args,**changes})


@pytest.mark.parametrize('mutation', ['child', 'root', 'slot', 'missing_index_text'])
def test_real_binding_corruption_is_rejected(mutation):
    q, src, ref=populated_fixture()
    if mutation=='child':src.execute("UPDATE codex_child SET raw_sha256='wrong' WHERE id='node-0'")
    elif mutation=='root':src.execute("UPDATE codex_root SET raw_sha256='wrong'")
    elif mutation=='slot':src.execute("UPDATE codex_text_slot SET raw_hash='wrong' WHERE source_label='SpeakerKnown'")
    else:q.db.execute("DELETE FROM research_text WHERE raw_text='合成发言0'")
    with pytest.raises(ValueError):q.read(q.corpus_ref,ref,view='dialogue',limit=1)


def test_missing_locale_is_empty_not_relabelled_chinese():
    q, _, ref=populated_fixture()
    page=q.read(q.corpus_ref,ref,view='dialogue',locale='EN',limit=2)
    assert page['nodes']==[] and page['partial']
    assert not page['scope_complete'] and page['unresolved_reason']=='no source name in the requested locale'


def test_mentioning_the_name_is_not_a_speaker_match():
    q, _, ref=populated_fixture(body_mentions_name=True)
    page=q.read(q.corpus_ref,ref,view='dialogue',limit=20)
    assert all(n['speaker']['display_name']=='合成角色甲' for n in page['nodes'])
    assert 'node-1' not in [n['node'] for n in page['nodes']]
    assert page['nodes'][0]['source_context']['after'][0]['speaker']['display_name']=='合成角色乙'


def test_equal_body_strings_keep_distinct_physical_refs():
    q, _, ref=populated_fixture(equal_bodies=True)
    nodes=[];cursor=None
    while True:
        page=q.read(q.corpus_ref,ref,view='dialogue',limit=20,cursor=cursor)
        nodes.extend(page['nodes']);cursor=page['next_cursor']
        if cursor is None:break
    assert len(nodes) == 7
    refs=[n['texts'][0]['evidence_ref'] for n in nodes]
    assert len(set(refs))==7
    assert {n['texts'][0]['raw_text'] for n in nodes}=={'相同合成发言'}


def test_legal_missing_node_boundary_is_not_invented():
    q, src, ref=populated_fixture()
    src.execute("DELETE FROM codex_child WHERE id='node-0'")
    page=q.read(q.corpus_ref,ref,view='dialogue',limit=1)
    assert page['nodes'][0]['texts']==[]
    assert page['nodes'][0]['body_status']=='no_projected_node_boundary'
    assert page['nodes'][0]['matched_speaker_ref']
    assert page['partial']


def test_existing_legacy_locator_mapping_is_reused_without_role_promotion():
    q, _, ref=populated_fixture(legacy_atoms=True)
    page=q.read(q.corpus_ref,ref,view='dialogue',limit=1)
    assert page['nodes'][0]['texts'][0]['raw_text']=='合成发言0'
    assert page['nodes'][0]['texts'][0]['text_proof']['owner_role_status']=='new_wrapper_role_unproven'


def test_source_item_type_is_visible_without_inventing_playback_order():
    q, _, ref=populated_fixture(item_type='MultiDialog')
    page=q.read(q.corpus_ref,ref,view='dialogue',limit=1)
    node=page['nodes'][0]
    assert node['source_item_type']=='MultiDialog'
    assert 'not proven sequential' in node['text_grouping']
    assert node['logical_identity_proven'] is False
