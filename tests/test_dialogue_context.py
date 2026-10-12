"""Synthetic dialogue and role records; no copyrighted game transcript."""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from genshin_lore.research_dialogue_context import DialogueContext
from genshin_lore.research_dialogue_context import DialogueReadAdapter
from genshin_lore.research_index import SCHEMA
from genshin_lore.research71_candidate_query import CandidateResearch71

ROOT = Path(__file__).resolve().parents[1]


def insert(db, table, values):
    columns = [r[1] for r in db.execute(f'PRAGMA table_info({table})')]
    defaults = {k: '' for k in columns}
    defaults.update(values)
    db.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
               [defaults[k] for k in columns])


def fixture():
    q = object.__new__(CandidateResearch71)
    q.db = sqlite3.connect(':memory:'); q.db.row_factory = sqlite3.Row
    q.db.executescript(SCHEMA)
    q.corpus_ref = 'synthetic-corpus'; q.source_context = None
    q.manifest = {'snapshot': {'id': 'synthetic-snapshot'}}
    q.components = {}
    q.r0_provenance = {'Dialog.json': 'source', 'Npc.json': 'source', 'Text.json': 'source'}
    sources = {}
    for key, family in [('dialog', 'dialog_archive'), ('npc', 'npc_archive')]:
        src = sqlite3.connect(':memory:'); src.row_factory = sqlite3.Row
        src.executescript((ROOT/'schemas/narrative-7.1-npc-dialog-reminder.sql').read_text())
        sources[key] = src
        q.components[key] = {'adapter': 'narrative_'+key, 'family': family,
                             'run': 'run-'+key, 'contract': 'contract', 'parser': 'parser'}
        insert(q.db, 'source_artifact', {'source_key': key})
    q._source = lambda key: sources[key]
    q.sources = sources
    q.coverage = []

    def physical(key, ordinal, native, original):
        src = sources[key]; raw = json.dumps(original, ensure_ascii=False, sort_keys=True)
        path = 'Dialog.json' if key == 'dialog' else 'Npc.json'
        src.execute('INSERT INTO physical_row VALUES(?,?,?,?,?,?,?)',
                    (ordinal, f'synthetic:{key}:{ordinal}', path, '/'+str(ordinal), str(native), raw,
                     hashlib.sha256(raw.encode()).hexdigest()))
        cid = 100+ordinal if key == 'dialog' else 200+ordinal
        insert(q.db, 'research_container', {'id': cid, 'source_key': key,
            'adapter_id': 'narrative_'+key, 'evidence_family': q.components[key]['family'],
            'evidence_contract': 'contract', 'evidence_parser': 'parser', 'evidence_run': 'run-'+key,
            'occurrence_id': f'synthetic:{key}:{ordinal}', 'container_kind': 'physical_row',
            'source_table': 'physical_row', 'pk_column': 'ordinal', 'pk_value': str(ordinal),
            'source_path': path, 'source_pointer': '/'+str(ordinal), 'source_file_sha256': 'source',
            'ordinal': ordinal})
        return q.db.execute('SELECT * FROM research_container WHERE id=?', (cid,)).fetchone()

    def text(key, parent, ordinal, field, body, locale='CHS', tier='main', pair=0):
        src = sources[key]; hash_value = str(ordinal*10+pair)
        src.execute('INSERT OR IGNORE INTO text_owner VALUES(?,?,?,?)', (ordinal, field, hash_value, 'source_authored_content'))
        src.execute('INSERT OR IGNORE INTO text_resolution VALUES(?,?,?,?,?)', (ordinal, field, locale, 'resolved_unique', 1))
        src.execute('INSERT INTO text_candidate VALUES(?,?,?,?,?,?,?,?)',
                    (ordinal, field, locale, tier, 'Text.json', pair, hash_value, body))
        cols = ['row_ordinal', 'field', 'locale', 'tier', 'pair_ordinal']
        pk = json.dumps([ordinal, field, locale, tier, pair])
        tid = q.db.execute('SELECT coalesce(max(id),0)+1 FROM research_text').fetchone()[0]
        insert(q.db, 'research_text', {'id': tid, 'container_id': parent['id'], 'source_key': key,
            'adapter_id': 'narrative_'+key+':text_candidate', 'evidence_family': parent['evidence_family'],
            'evidence_contract': 'contract', 'evidence_parser': 'parser', 'evidence_run': 'run-'+key,
            'occurrence_id': parent['occurrence_id'], 'child_id': 'text-'+str(tid), 'source_table': 'text_candidate',
            'pk_column': json.dumps(cols), 'pk_value': pk, 'text_column': 'raw_text', 'locale': locale,
            'owner_field': field, 'ordinal': ordinal, 'text_source_path': 'Text.json',
            'text_source_pointer': '/'+hash_value, 'text_source_file_sha256': 'source',
            'physical_pair_ordinal': pair, 'raw_text': body,
            'text_sha256': hashlib.sha256(body.encode()).hexdigest()})
        return q.db.execute('SELECT * FROM research_text WHERE id=?', (tid,)).fetchone()

    npc = physical('npc', 0, '7', {'id': 7})
    text('npc', npc, 0, 'nameTextMapHash', '合成角色甲')
    parents = []
    for ordinal, successors in [(0, [11, 12]), (1, [10]), (2, [])]:
        role = {'id': '7', 'type': 'TALK_ROLE_NPC'}
        p = physical('dialog', ordinal, 10+ordinal, {'id': 10+ordinal, 'nextDialogs': successors, 'talkRole': role})
        parents.append(p)
        text('dialog', p, ordinal, 'talkContentTextMapHash', ['合成问题？', '合成回答。', '合成回答。'][ordinal])
        src = sources['dialog']
        src.execute('INSERT INTO locator VALUES(?,?,?,?,?,?,?)', (ordinal, 'talkRole', 0, json.dumps(role), 'npc', 'resolved_unique', 1))
        src.execute('INSERT INTO physical_candidate VALUES(?,?,?,?,?,?)', (ordinal, 'talkRole', 0, 0, 'Npc.json', '/0'))
        for i, native in enumerate(successors):
            src.execute('INSERT INTO locator VALUES(?,?,?,?,?,?,?)', (ordinal, 'nextDialogs', i, str(native), 'dialog', 'resolved_unique', 1))
            src.execute('INSERT INTO physical_candidate VALUES(?,?,?,?,?,?)', (ordinal, 'nextDialogs', i, native-10, 'Dialog.json', '/'+str(native-10)))
        if not successors:
            src.execute('INSERT INTO locator VALUES(?,?,?,?,?,?,?)', (ordinal, 'nextDialogs', -1, '[]', 'dialog', 'terminal_empty', 0))
    return q, parents, text


def test_read_view_preserves_branch_cycle_occurrences_and_formal_refs():
    q, parents, _ = fixture(); ref = q._container_ref(parents[0])
    value = q.read(q.corpus_ref, ref, view='dialogue', limit=10)
    assert len(value['nodes']) == 3
    assert len(value['nodes'][0]['edges']) == 2
    assert value['reachable_local_graph_exhausted'] and value['next_cursor'] is None
    assert not value['corpus_complete'] and 'not playback' in value['ordering_basis']
    refs = [n['texts'][0]['evidence_ref'] for n in value['nodes']]
    assert len(set(refs)) == 3  # equal bodies are different occurrences
    for node in value['nodes']:
        assert node['speaker']['display_name'] == '合成角色甲'
        assert node['speaker']['logical_identity_proven'] is False
        for t in node['texts']:
            assert q.read(q.corpus_ref, t['evidence_ref'])['raw_text'] == t['raw_text']
            assert q.inspect(q.corpus_ref, t['evidence_ref'])['text_provenance'] == t['provenance']


def test_pagination_and_cursor_anchor_limit_locale_isolation():
    q, parents, _ = fixture(); ref = q._container_ref(parents[0]); cursor = None; nodes = []
    for _ in range(3):
        page = q.read(q.corpus_ref, ref, view='dialogue', limit=1, cursor=cursor)
        nodes += [n['node'] for n in page['nodes']]; cursor = page['next_cursor']
    assert len(set(nodes)) == 3 and cursor is None
    first = q.read(q.corpus_ref, ref, view='dialogue', limit=1)['next_cursor']
    for changes in ({'limit': 2}, {'locale': 'EN'}, {'evidence_ref': q._container_ref(parents[1])}):
        args = dict(corpus_ref=q.corpus_ref, evidence_ref=ref, view='dialogue', limit=1, cursor=first)
        args.update(changes)
        with pytest.raises(ValueError, match='cursor scope'): q.read(**args)


def test_default_read_remains_single_container_and_adds_role_card():
    q, parents, _ = fixture(); ref = q._container_ref(parents[0])
    value = q.read(q.corpus_ref, ref, limit=10)
    assert len(value['context']) == 1 and value['context_scope'] == 'same physical container'
    assert value['dialogue_source']['speaker']['display_name'] == '合成角色甲'
    assert value['dialogue_source']['locations'] == []
    assert 'no declared' in value['dialogue_source']['location_unresolved_reason']


def test_equal_name_candidates_are_not_logical_deduplication():
    q, parents, add_text = fixture()
    npc = q.db.execute('SELECT * FROM research_container WHERE id=200').fetchone()
    add_text('npc', npc, 0, 'nameTextMapHash', '合成角色甲', tier='medium', pair=1)
    speaker = DialogueContext(q).card(parents[0])['speaker']
    assert speaker['display_name'] is None and speaker['status'] == 'ambiguous'
    assert len(speaker['names']) == 2


def test_missing_locale_name_and_body_stay_unresolved():
    q, parents, _ = fixture()
    page = q.read(q.corpus_ref, q._container_ref(parents[0]), view='dialogue', limit=10, locale='EN')
    assert all(n['body_status'] == 'no_resolved_body_in_locale' for n in page['nodes'])
    assert all(n['speaker']['display_name'] is None for n in page['nodes'])
    assert page['partial']


def test_predecessor_follows_source_edges_not_neighbour_rows():
    q, parents, _ = fixture()
    page = q.follow(q.corpus_ref, q._container_ref(parents[2]), relation_kind='dialogue_predecessor', limit=1)
    assert len(page['items']) == 1
    assert page['items'][0]['candidates'][0]['target_evidence_ref'] == q._container_ref(parents[0])
    assert page['items'][0]['forward_proof'][0]['raw_locator'] == 12
    assert page['scope_complete']


def test_predecessor_can_hide_targets_without_changing_proof():
    q, parents, _ = fixture()
    page = q.follow(q.corpus_ref, q._container_ref(parents[2]), relation_kind='dialogue_predecessor', limit=1, resolve_targets=False)
    assert page['items'][0]['candidates'][0]['target_evidence_ref'] is None
    assert page['items'][0]['forward_proof']


def talk_fixture():
    q, _, _ = fixture()
    src = sqlite3.connect(':memory:'); src.row_factory = sqlite3.Row
    src.executescript((ROOT/'schemas/quest-talk-7.1-standalone-v1.sql').read_text())
    qs = sqlite3.connect(':memory:'); qs.row_factory = sqlite3.Row
    qs.executescript((ROOT/'schemas/quest-narrative-7.1-v1.sql').read_text())
    q.sources.update(talk=src, quest=qs)
    for key, adapter, family in [('talk','quest_talk_physical','quest_talk_pilot'), ('quest','quest_narrative_archive','quest_narrative_archive')]:
        q.components[key] = {'adapter': adapter, 'family': family, 'run':'run-'+key,
                             'parser':'parser','contract':'contract'}
        insert(q.db, 'source_artifact', {'source_key': key})
    q.r0_provenance.update({'Talk.json':'source','Quest.json':'source','Main.json':'source'})
    for identity, kind, path in [('talk-root','talk_root','Talk.json'), ('quest-root','quest_root','Quest.json')]:
        src.execute('INSERT INTO source_file VALUES(?,?,?,?,?,?,?)', (path,'snap','talk','blob','source',1,b'{}'))
        src.execute('INSERT INTO physical_container VALUES(?,?,?,?,?,?,?,?)', (identity,'run-talk',path,'/',kind,'qualified','1','source'))
        insert(q.db,'research_container',{'id':301 if kind=='talk_root' else 302, 'source_key':'talk',
            'adapter_id':'quest_talk_physical','evidence_family':'quest_talk_pilot',
            'evidence_contract':'contract','evidence_parser':'parser','evidence_run':'run-talk',
            'occurrence_id':identity,'container_kind':kind,'source_table':'physical_container',
            'pk_column':'id','pk_value':identity,'source_path':path,'source_pointer':'/',
            'source_file_sha256':'source'})
    source_candidate={'container_id':'talk-root','path':'Talk.json','pointer':'/','kind':'talk_root'}
    src.execute('INSERT INTO physical_reference VALUES(?,?,?,?,?,?,?,?,?)',
                ('link','quest-root','run-talk','/talk','quest_to_talk','1','{"id":1}',
                 'resolved_unique',json.dumps([source_candidate])))
    for i, target in [(0,'node-1'), (1,None)]:
        edges = [{'ordinal':0,'raw_target_id':2,'status':'local','local_candidates':[target],'physical_candidates':[]}] if target else []
        src.execute('INSERT INTO talk_node VALUES(?,?,?,?,?,?,?,?,?,?)',
                    ('node-'+str(i),'talk-root','run-talk',i,'/nodes/'+str(i),str(i+1),'9','literal_key','{}',json.dumps(edges)))
        text='合成任务对白'+str(i); key='9'+str(i)
        loc={'path':'Text.json','pointer':'/'+key,'pair_ordinal':i,'raw_text':text}
        src.execute('INSERT INTO text_slot VALUES(?,?,?,?,?,?,?)', ('slot-'+str(i),'node-'+str(i),'run-talk','CHS','resolved_main',text,json.dumps([loc])))
        insert(q.db,'research_text',{'id':10+i,'container_id':301,'source_key':'talk','adapter_id':'quest_talk_physical:text_slot',
            'evidence_family':'quest_talk_pilot','evidence_contract':'contract','evidence_parser':'parser',
            'evidence_run':'run-talk','occurrence_id':'talk-root','child_id':'child-'+str(i),
            'source_table':'text_slot','pk_column':'id','pk_value':'slot-'+str(i),'text_column':'raw_text',
            'locale':'CHS','ordinal':i,'text_source_path':'Text.json','text_source_pointer':'/'+key,
            'text_source_file_sha256':'source','physical_pair_ordinal':i,'raw_text':text,
            'text_sha256':hashlib.sha256(text.encode()).hexdigest()})
    raw='{}'
    qs.execute('INSERT INTO excel_row VALUES(?,?,?,?,?,?,?,?,?)', ('main','run-quest','main','Main.json',0,'/0','3',raw,hashlib.sha256(raw.encode()).hexdigest()))
    qs.execute('INSERT INTO main_root_candidate VALUES(?,?,?,?,?,?)', ('root-link','main','quest-root','Quest.json','1','physical_candidate'))
    insert(q.db,'research_container',{'id':400,'source_key':'quest','adapter_id':'quest_narrative_archive',
        'evidence_family':'quest_narrative_archive','evidence_contract':'contract','evidence_parser':'parser',
        'evidence_run':'run-quest','occurrence_id':'main','source_table':'excel_row','pk_column':'id',
        'pk_value':'main','source_path':'Main.json','source_pointer':'/0','source_file_sha256':'source'})
    return q


def test_talk_view_uses_local_edges_and_reverse_quest_chain():
    q=talk_fixture(); parent=q.db.execute('SELECT * FROM research_container WHERE id=301').fetchone()
    page=q.read(q.corpus_ref,q._container_ref(parent),view='dialogue',limit=3)
    assert len(page['nodes'])==2 and page['reachable_local_graph_exhausted']
    loc=page['source_context']['locations'][0]
    assert loc['status']=='physical_candidate' and len(loc['chain'])==2
    assert loc['titles']==[]  # missing title is never invented
    ref=page['nodes'][1]['texts'][0]['evidence_ref']
    previous=q.follow(q.corpus_ref,ref,relation_kind='dialogue_predecessor',limit=2)
    assert previous['items'][0]['node']=='node-0'


def test_talk_external_candidate_is_not_traversed_as_local():
    q=talk_fixture(); src=q.sources['talk']
    edges=[{'ordinal':0,'raw_target_id':99,'status':'external','local_candidates':[],
            'physical_candidates':['another-container-node']}]
    src.execute('UPDATE talk_node SET successors_json=? WHERE id=?',(json.dumps(edges),'node-0'))
    parent=q.db.execute('SELECT * FROM research_container WHERE id=301').fetchone()
    page=q.read(q.corpus_ref,q._container_ref(parent),view='dialogue',limit=3)
    assert len(page['nodes'])==1 and page['partial']
    assert page['nodes'][0]['edges'][0]['external_candidates']==['another-container-node']


@pytest.mark.parametrize('change', ['reference_kind', 'reference_run', 'main_run'])
def test_reverse_quest_chain_uses_only_declared_kind_and_exact_runs(change):
    q=talk_fixture()
    if change=='reference_kind':
        q.sources['talk'].execute("UPDATE physical_reference SET kind='wrapper_to_talk'")
    elif change=='reference_run':
        q.sources['talk'].execute("UPDATE physical_reference SET ingest_run_id='foreign-run'")
    else:
        q.sources['quest'].execute("UPDATE excel_row SET run_id='foreign-run'")
    parent=q.db.execute('SELECT * FROM research_container WHERE id=301').fetchone()
    page=q.read(q.corpus_ref,q._container_ref(parent),view='dialogue',limit=3)
    assert page['source_context']['locations']==[]
    assert page['source_context']['location_unresolved_reason']


def test_opt_in_adapter_keeps_underlying_identity_and_delegation():
    q, parents, _ = fixture(); adapter = DialogueReadAdapter(q)
    ref = q._container_ref(parents[0])
    assert adapter.read(q.corpus_ref,ref)['evidence_ref']==ref
    assert adapter.inspect(q.corpus_ref,ref)==q.inspect(q.corpus_ref,ref)
    assert adapter.read(q.corpus_ref,ref,view='dialogue',limit=1)['next_cursor']
    assert 'read' not in q.__dict__  # no method monkey-patch on qualified runtime
    assert adapter.follow(q.corpus_ref,ref,relation_kind='dialogue_predecessor',limit=1)['items']


def test_opt_in_adapter_discloses_extension_without_changing_base_authority():
    from types import SimpleNamespace
    original={'release_id':'synthetic-release','formal_release':True}
    q=SimpleNamespace(describe=lambda *a,**kw:original)
    value=DialogueReadAdapter(q).describe('synthetic')
    assert value['release_id']==original['release_id']
    assert value['dialogue_reading_extension']['status']=='opt_in_not_release_promoted'
    assert 'dialogue_reading_extension' not in original


@pytest.mark.parametrize('change', ['body', 'role', 'target', 'hash'])
def test_source_and_mapping_drift_rejected(change):
    q, parents, _ = fixture()
    if change == 'body': q.sources['dialog'].execute("UPDATE text_candidate SET raw_text='changed' WHERE row_ordinal=0")
    if change == 'role': q.sources['dialog'].execute("UPDATE locator SET raw_value='{}' WHERE field='talkRole'")
    if change == 'target': q.sources['dialog'].execute("UPDATE physical_candidate SET target_pointer='/99' WHERE field='talkRole'")
    if change == 'hash': q.sources['dialog'].execute("UPDATE physical_row SET raw_sha256='bad' WHERE ordinal=0")
    with pytest.raises((ValueError, RuntimeError)):
        q.read(q.corpus_ref, q._container_ref(parents[0]), view='dialogue', limit=3)


@pytest.mark.parametrize('args', [{'view':'wrong'}, {'view':'dialogue','limit':0},
                                  {'view':'dialogue','limit':21}, {'view':'dialogue','before':1},
                                  {'view':'dialogue','locale':'unknown'}])
def test_invalid_view_arguments_rejected(args):
    q, parents, _ = fixture()
    with pytest.raises(ValueError):q.read(q.corpus_ref, q._container_ref(parents[0]), **args)
