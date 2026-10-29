"""Current Native Message dependencies never inherit historical machine paths."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.build import Rebuild, ROOT
from scripts.build_support import file_seal
from genshin_lore import native_message_7_1_binding as binding
from genshin_lore import native_message_7_1_build as builder
from genshin_lore import native_message_7_1_verify as verifier


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    import hashlib
    import shutil
    import sqlite3
    root=tmp_path/'implementation'
    for name in ('src/genshin_lore/native_message_7_1_build.py',
                 'src/genshin_lore/event_7_1_source.py','schemas/native-message-7.1-v1.sql'):
        target=root/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/name,target)
    monkeypatch.setattr(binding,'ROOT',root)
    monkeypatch.setattr(builder,'ROOT',root)
    monkeypatch.setattr(verifier,'ROOT',root)
    job=object.__new__(Rebuild)
    job.source=tmp_path/'source';job.source.mkdir()
    job.output=tmp_path/'output';job.output.mkdir()
    job.history=None;job.identity_root=root
    records=[{'id':1,'text':10,'quest':7},{'id':2,'text':11,'quest':8},{'id':3,'text':12,'quest':9}]
    write(job.source/'message.json',records)
    write(job.source/'main.json',{'10':'synthetic main'})
    write(job.source/'medium.json',{'11':'synthetic medium'})
    spec={'path':'message.json','boundary':'synthetic_message','native_id_field':'id',
          'text_owners':[{'field':'text','owner_role_status':'mapping_unresolved'}],
          'relations':[{'field':'quest','relation_kind':'main_quest','candidate_namespace':'main_quest'}]}
    old={'sources':[spec],'languages':['CHS'],'textmaps':{'CHS':{'main':'main.json','medium':'medium.json'}},
         'family_contract_version':'synthetic-historical-v1'}
    write(root/'historical.json',old)
    dependency={'artifact':{'path_machine_local':'/tmp/historical.sqlite','size_bytes':1,'sha256':'0'*64},
                'target_artifact_machine_local':'/tmp/historical.sqlite','capability':'mainquest_exact_rows',
                'contract':'quest-narrative-7.1-derived-v1','family':'quest_narrative_archive',
                'parser':'old-parser','run':'old-run','physical_target_denominator':3}
    scope={'contract':'native-message-7.1-exact-source-scope-v1','snapshot_id':binding.SNAPSHOT,
           'historical_selection':'historical.json','historical_selection_sha256':file_seal(root/'historical.json')['sha256'],
           'sources':[spec],'languages':old['languages'],'textmaps':old['textmaps'],
           'files':[{'path':n,**file_seal(job.source/n),'blob':'synthetic'} for n in ('message.json','main.json','medium.json')],
           'row_denominator':{'message.json':3},'raw_field_alignment':{'semantic_role':'mapping_unresolved'},
           'dependency':dependency,'external_candidates':[]}
    write(root/binding.SCOPE,scope)
    def check_files(source, scope):
        for item in scope['files']:
            assert file_seal(source/item['path'])=={k:item[k] for k in ('size_bytes','sha256')}
    monkeypatch.setattr(builder,'check_files',check_files)
    monkeypatch.setattr(verifier,'check_files',check_files)
    def qt_build(p):
        p.write_bytes(b'synthetic quest talk');return {'artifact':file_seal(p)}
    job.task('quest_talk',qt_build,lambda p,r:{'status':'PASS'})
    qmp=job.metadata_path(binding.QT_MANIFEST);write(qmp,{'synthetic':'quest talk binding'})
    target_rows=[('current-a','current-run','MainQuest.json','/0','7','a'*64),
                 ('current-b','current-run','MainQuest.json','/1','7','b'*64),
                 ('current-c','current-run','MainQuest.json','/2','8','c'*64)]
    calls=[]
    def oracle(source,artifact,report,qt,manifest):
        calls.append(artifact)
        with sqlite3.connect(artifact) as db:
            actual=db.execute('SELECT id,run_id,source_path,pointer,native_id,raw_sha256 FROM excel_row ORDER BY id').fetchall()
        if actual!=target_rows:raise ValueError('synthetic independent MainQuest source mismatch')
        return {'verdict':'PASS','counts':{'excel_row':3},'logical_seals':{'synthetic':'exact'}}
    # Only the costly upstream oracle is substituted in proof-boundary tests.
    # The Native Message builder, independent source oracle and v2 receipts run.
    monkeypatch.setattr(binding.oracle,'verify',oracle)
    def qn_build(p):
        with sqlite3.connect(p) as db:
            db.execute('CREATE TABLE excel_row(id,run_id,source_path,pointer,native_id,raw_sha256,kind)')
            db.executemany('INSERT INTO excel_row VALUES(?,?,?,?,?,?,?)',[(*r,'main_quest') for r in target_rows])
        return {'artifact':file_seal(p),'release':{'contract':dependency['contract'],'snapshot':binding.SNAPSHOT,
                'parser_build_id':'current-parser','ingest_run_id':'current-run'}}
    job.task('quest_narrative',qn_build,lambda p,r:oracle(job.source,p,None,None,None),
             inputs=job.dependency_inputs('quest_talk')+[qmp])
    current=binding.prepare_scope(job.source,job.output)
    path=job.metadata_path(binding.SCOPE);write(path,current)
    return SimpleNamespace(job=job,root=root,scope=current,path=path,calls=calls,qmp=qmp)


def test_current_scope_replays_oracle_and_preserves_historical_seal(fresh):
    before=(fresh.root/binding.SCOPE).read_bytes();count=len(fresh.calls)
    binding.qualify_scope(fresh.job.source,fresh.path,fresh.scope)
    assert len(fresh.calls)==count+1
    assert (fresh.root/binding.SCOPE).read_bytes()==before
    assert fresh.scope['dependency']['parser']=='current-parser'
    assert {c['target_occurrence_id'] for c in fresh.scope['external_candidates']}=={'current-a','current-b','current-c'}
    assert fresh.scope['dependency']['target_artifact_machine_local']==str(fresh.job.path('quest_narrative'))


def test_actual_message_builder_and_independent_oracle_keep_ambiguous_missing(fresh):
    path=fresh.job.output/'message.sqlite'
    report=builder.build(fresh.job.source,fresh.path,path)
    result=verifier.verify(fresh.job.source,fresh.path,path,report)
    assert result['second_hop_checked']==3
    assert result['relation_states']=={'resolved_candidate_set':1,'resolved_unique':1,'missing':1}
    assert result['text_states']=={'resolved_main':1,'resolved_medium':1,'missing':1}


@pytest.mark.parametrize('field',['receipt','path','parser','run','denominator','scope','local_contract'])
def test_local_scope_cannot_rebind_dependency_or_source(fresh,field):
    if field=='receipt':fresh.scope['local_rebuild']['receipt']['sha256']='0'*64
    elif field=='path':fresh.scope['dependency']['target_artifact_machine_local']='/tmp/historical.sqlite'
    elif field=='scope':fresh.scope['sources']=[]
    elif field=='local_contract':fresh.scope['local_rebuild']['contract']='unknown'
    else:fresh.scope['dependency'][{'denominator':'physical_target_denominator'}.get(field,field)]='wrong'
    write(fresh.path,fresh.scope)
    with pytest.raises(ValueError):binding.qualify_scope(fresh.job.source,fresh.path,fresh.scope)


@pytest.mark.parametrize('record',['verified.json','verify.json','build.json','invocation.json'])
def test_missing_dependency_proof_is_rejected_without_fallback(fresh,record):
    (fresh.job.output/'quest_narrative'/record).unlink()
    with pytest.raises((ValueError,FileNotFoundError)):
        binding.qualify_scope(fresh.job.source,fresh.path,fresh.scope)


@pytest.mark.parametrize('mutation',['legacy','incomplete','implementation','environment','source','inputs','oracle'])
def test_resealed_false_dependency_is_rejected(fresh,mutation):
    directory=fresh.job.output/'quest_narrative';rp=directory/'verified.json'
    receipt=json.loads(rp.read_text());identity=receipt['identity']
    if mutation=='legacy':receipt['contract']='local-build-stage-receipt-v1'
    elif mutation=='incomplete':receipt['independent_verifier_completed']=False
    elif mutation=='oracle':write(directory/'verify.json',{'verdict':'PASS','counts':{'excel_row':0}})
    else:
        if mutation=='implementation':next(iter(identity['implementation_and_contracts'].values()))['sha256']='0'*64
        elif mutation=='environment':identity['environment']['sqlite']='wrong'
        elif mutation=='source':identity['source_root']=str(fresh.job.output)
        else:identity['stage_inputs']={}
        invocation=json.loads((directory/'invocation.json').read_text());invocation['identity']=identity
        write(directory/'invocation.json',invocation)
    receipt['records']={n:file_seal(directory/n) for n in ('build.json','verify.json','invocation.json')}
    write(rp,receipt);fresh.scope['local_rebuild']['receipt']=file_seal(rp);write(fresh.path,fresh.scope)
    with pytest.raises(ValueError):binding.qualify_scope(fresh.job.source,fresh.path,fresh.scope)


@pytest.mark.parametrize('suffix',['-wal','-shm','-journal'])
def test_target_sidecars_are_preserved_and_rejected(fresh,suffix):
    p=Path(str(fresh.job.path('quest_narrative'))+suffix);p.write_bytes(b'diagnostic')
    with pytest.raises(ValueError,match='sidecar'):
        binding.qualify_scope(fresh.job.source,fresh.path,fresh.scope)
    assert p.read_bytes()==b'diagnostic'


@pytest.mark.parametrize('mutation',['omit','extra','retarget','run','pointer','hash'])
def test_message_builder_rejects_incomplete_or_retargeted_current_candidates(fresh,mutation):
    rows=fresh.scope['external_candidates']
    if mutation=='omit':rows.pop()
    elif mutation=='extra':rows.append(dict(rows[0]))
    else:rows[0][{'retarget':'target_occurrence_id','run':'target_run','pointer':'target_source_pointer','hash':'target_raw_sha256'}[mutation]]='wrong'
    write(fresh.path,fresh.scope)
    with pytest.raises(ValueError,match='complete target set'):
        builder.project(fresh.job.source,fresh.path)


@pytest.mark.parametrize('table',['native71_row','native71_relation','native71_candidate'])
def test_independent_oracle_rejects_removed_rows_even_with_resealed_artifact(fresh,table):
    import sqlite3
    path=fresh.job.output/'message.sqlite';report=builder.build(fresh.job.source,fresh.path,path)
    with sqlite3.connect(path) as db:
        triggers=db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
        for (name,) in triggers:db.execute('DROP TRIGGER '+name)
        if table=='native71_row':
            for child in ('native71_text','native71_slot','native71_candidate','native71_relation'):
                db.execute('DELETE FROM '+child)
        elif table=='native71_relation':db.execute('DELETE FROM native71_candidate')
        db.execute('DELETE FROM '+table)
        schema=(ROOT/'schemas/native-message-7.1-v1.sql').read_text()
        # Restore the exact immutable trigger definitions, so row comparison is tested.
        import re
        for sql in re.findall(r'CREATE TRIGGER.*?END;',schema,re.S):db.executescript(sql)
    report['artifact']=file_seal(path)
    with pytest.raises(ValueError,match='source rowset mismatch'):
        verifier.verify(fresh.job.source,fresh.path,path,report)


def test_output_local_scope_cannot_be_moved_to_sibling(fresh):
    other=fresh.job.output.parent/'other/metadata'/binding.SCOPE
    write(other,fresh.scope)
    with pytest.raises((ValueError,FileNotFoundError)):
        binding.qualify_scope(fresh.job.source,other,fresh.scope)


@pytest.mark.parametrize('mutation',['omit','extra','retarget','run','pointer','hash'])
def test_independent_oracle_reconstructs_scope_candidate_set(fresh,mutation):
    path=fresh.job.output/'message.sqlite';report=builder.build(fresh.job.source,fresh.path,path)
    rows=fresh.scope['external_candidates']
    if mutation=='omit':rows.pop()
    elif mutation=='extra':rows.append(dict(rows[0]))
    else:rows[0][{'retarget':'target_occurrence_id','run':'target_run','pointer':'target_source_pointer','hash':'target_raw_sha256'}[mutation]]='wrong'
    write(fresh.path,fresh.scope)
    # Even after recomputing the scope/run seal, the independent source-derived
    # candidate comparison must reject it before reading the output rowsets.
    report['run_id']=verifier.identity('run',report['parser_build_id'],file_seal(fresh.path)['sha256'])
    with pytest.raises(ValueError,match='complete target set'):
        verifier.verify(fresh.job.source,fresh.path,path,report)


@pytest.mark.parametrize('consumer',[builder.project,verifier.verify])
def test_removed_local_flag_cannot_select_a_forged_historical_dependency(fresh,consumer):
    path=fresh.job.output/'message.sqlite';report=builder.build(fresh.job.source,fresh.path,path)
    del fresh.scope['local_rebuild'];write(fresh.path,fresh.scope)
    args=(fresh.job.source,fresh.path) if consumer is builder.project else (fresh.job.source,fresh.path,path,report)
    with pytest.raises(ValueError,match='historical scope'):consumer(*args)


def test_resealed_target_parent_identity_cannot_shrink_source_scope(fresh):
    import sqlite3
    artifact=fresh.job.path('quest_narrative');directory=artifact.parent
    with sqlite3.connect(artifact) as db:db.execute("UPDATE excel_row SET native_id='unreferenced' WHERE id='current-a'")
    report=json.loads((directory/'build.json').read_text());report['artifact']=file_seal(artifact)
    write(directory/'build.json',report)
    receipt=json.loads((directory/'verified.json').read_text());receipt['artifact']=file_seal(artifact)
    receipt['records']['build.json']=file_seal(directory/'build.json');write(directory/'verified.json',receipt)
    fresh.scope['local_rebuild']['receipt']=file_seal(directory/'verified.json');write(fresh.path,fresh.scope)
    with pytest.raises(ValueError,match='independent MainQuest source mismatch'):
        binding.qualify_scope(fresh.job.source,fresh.path,fresh.scope)


def test_declared_empty_source_is_not_rejected_as_incomplete(fresh):
    write(fresh.job.source/'message.json',[])
    for scope in (fresh.scope,json.loads((fresh.root/binding.SCOPE).read_text())):
        scope['row_denominator']['message.json']=0
        scope['files'][0].update(file_seal(fresh.job.source/'message.json'))
        if 'local_rebuild' not in scope:write(fresh.root/binding.SCOPE,scope)
    # The static contract changed only in the synthetic fixture. Recreate the
    # upstream proofs with this fixture contract in their implementation seal.
    from scripts.build_support import pipeline_identity
    for name in ('quest_talk','quest_narrative'):
        directory=fresh.job.output/name;receipt=json.loads((directory/'verified.json').read_text())
        old=receipt['identity'];identity=pipeline_identity(fresh.root,fresh.job.source,None,
            inputs=old['stage_inputs'],parameters=old['parameters'])
        receipt['identity']=identity
        invocation=json.loads((directory/'invocation.json').read_text());invocation['identity']=identity
        write(directory/'invocation.json',invocation)
        receipt['records']['invocation.json']=file_seal(directory/'invocation.json')
        write(directory/'verified.json',receipt)
    scope=binding.prepare_scope(fresh.job.source,fresh.job.output);write(fresh.path,scope)
    path=fresh.job.output/'empty.sqlite';report=builder.build(fresh.job.source,fresh.path,path)
    result=verifier.verify(fresh.job.source,fresh.path,path,report)
    assert result['counts']=={name:0 for name in builder.TABLES}


def test_simple_components_do_not_consume_future_quest_narrative(tmp_path, monkeypatch):
    job=object.__new__(Rebuild)
    job.source=tmp_path
    names=[]
    job.task=lambda name,*args,**kwargs:names.append(name)
    job.simple()
    assert names == ['document','gcg_card','loading_tips','mail']


def test_native_message_is_scheduled_after_verified_quest_narrative(tmp_path, monkeypatch):
    job=object.__new__(Rebuild)
    job.source=tmp_path/'source'; job.output=tmp_path/'output'; job.history=None
    calls=[]
    class Done(Exception): pass
    def task(name,*args,**kwargs):
        calls.append(name)
        return {'contract':'synthetic','parser_build_id':'parser','ingest_run_id':'run'}
    job.task=task
    job.bind=lambda *args:None
    job.dependency_inputs=lambda *args:[]
    monkeypatch.setattr('scripts.build.seal',lambda p:{'size_bytes':1,'sha256':'synthetic'})
    def native():
        calls.append('native_message');raise Done
    job.native_message=native
    with pytest.raises(Done):job.quest()
    assert calls==['quest_talk','quest_narrative','native_message']
