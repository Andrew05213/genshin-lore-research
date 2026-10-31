"""Tiny real index/oracle/runtime pipeline, not a full upstream reconstruction.

The source stages create synthetic SQLite fixtures. The actual index builder,
independent complete oracle, receipt checks and public MCP process are used.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest

from genshin_lore import research_index
from genshin_lore.research71_candidate_relations import project, record_unsupported
from scripts.build import Rebuild, seal, write
from scripts.builders.research_dataset import build, VERSION
from scripts.check import payload
from scripts.validators.research_dataset import verify_connection, verify_required_source_projections

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ['describe_corpus', 'search_evidence', 'read_evidence', 'follow_evidence', 'inspect_evidence']


@pytest.mark.parametrize('rows', [0, 1])
def test_empty_and_nonempty_proof_scopes_remain_declared_partial(rows):
    src = sqlite3.connect(':memory:')
    out = sqlite3.connect(':memory:')
    out.row_factory = sqlite3.Row
    out.executescript(research_index.SCHEMA)
    src.execute('CREATE TABLE comparison_scope_proof(id INTEGER PRIMARY KEY)')
    if rows:
        src.execute('INSERT INTO comparison_scope_proof VALUES(1)')
    component = {'source_key':'core', 'family':'schema20_core', 'adapter':'core33'}
    record_unsupported(src, out, 'core', component)
    row = out.execute('SELECT * FROM adapter_coverage').fetchone()
    assert (row['source_table'], row['denominator'], row['indexed_count'], row['status']) == (
        'comparison_scope_proof', rows, 0, 'unsupported')
    verify_required_source_projections(out, {'components':[component]}, {'core':src})
    out.execute('DELETE FROM adapter_coverage')
    with pytest.raises(ValueError, match='relation:comparison_scope_proof'):
        verify_required_source_projections(out, {'components':[component]}, {'core':src})
    src.close()
    out.close()


@pytest.mark.parametrize('mode,family', [('core33','schema20_core'),
    ('quest_narrative_archive','quest_narrative_archive')])
def test_unknown_relation_table_is_not_an_automatically_unsupported_adapter(mode, family):
    src = sqlite3.connect(':memory:')
    src.execute('CREATE TABLE unregistered_scope_proof(id INTEGER PRIMARY KEY)')
    out = sqlite3.connect(':memory:')
    out.executescript(research_index.SCHEMA)
    with pytest.raises(ValueError, match='absent from explicit adapter'):
        record_unsupported(src, out, 'fixture', {'adapter':mode, 'family':family})
    assert out.execute('SELECT COUNT(*) FROM adapter_coverage').fetchone()[0] == 0
    src.close()
    out.close()


@pytest.mark.parametrize('mode,required,partial', [
    ('quest_narrative_archive',('excel_row','text_candidate'),
        (('candidate','child_candidate'),('candidate','excel_candidate'),('candidate','main_root_candidate'))),
    ('quest_review_archive',('evidence_row','text_candidate'),(('candidate','locator_candidate'),)),
    ('quest_talk_physical',('physical_container','text_slot'),(('relation','physical_reference'),)),
    ('dependency_only',(),(('candidate','lore_avatar_candidate'),('candidate','voice_avatar_candidate'),
        ('candidate','voice_lut_candidate'))),
])
def test_candidate_partial_scopes_cannot_be_silently_omitted(mode, required, partial):
    src = sqlite3.connect(':memory:')
    out = sqlite3.connect(':memory:')
    out.row_factory = sqlite3.Row
    out.executescript(research_index.SCHEMA)
    for table in required:
        src.execute(f'CREATE TABLE {table}(id INTEGER PRIMARY KEY, raw_text)')
    for _, table in partial:
        src.execute(f'CREATE TABLE {table}(id INTEGER PRIMARY KEY)')
    src.execute(f'INSERT INTO {partial[0][1]} VALUES(1)')
    component = {'source_key':'fixture','adapter':mode,'family':'synthetic'}
    record_unsupported(src,out,'fixture',component)
    # The required container/text fixture tables are genuinely empty.
    for kind, table in zip(('container','text'),required):
        out.execute('INSERT INTO adapter_coverage VALUES(?,?,?,?,?,?,?,?)',
            ('fixture','synthetic',kind,table,0,0,'complete','empty synthetic projection'))
    verify_required_source_projections(out,{'components':[component]},{'fixture':src})
    row = out.execute('SELECT * FROM adapter_coverage WHERE source_table=?',(partial[0][1],)).fetchone()
    assert (row['denominator'],row['indexed_count'],row['status']) == (1,0,'unsupported')
    out.execute('DELETE FROM adapter_coverage WHERE source_table=?',(partial[0][1],))
    with pytest.raises(ValueError, match=partial[0][1]):
        verify_required_source_projections(out,{'components':[component]},{'fixture':src})
    src.close()
    out.close()


def test_talk_physical_explicit_projection_is_not_also_declared_unsupported():
    src = sqlite3.connect(':memory:')
    src.row_factory = sqlite3.Row
    src.executescript('''
        CREATE TABLE talk_physical_file(id TEXT PRIMARY KEY,source_path,pointer);
        CREATE TABLE talk_physical_reference(id TEXT PRIMARY KEY,file_id,pointer,status,candidate_count);
        CREATE TABLE talk_physical_candidate(id TEXT PRIMARY KEY,reference_id);
        INSERT INTO talk_physical_file VALUES('file','synthetic.json','/0');
        INSERT INTO talk_physical_reference VALUES('ref','file','/0/ref','unresolved',0);
    ''')
    out = sqlite3.connect(':memory:')
    out.row_factory = sqlite3.Row
    out.executescript(research_index.SCHEMA)
    component = {'adapter':'talk_physical','family':'talk_physical_archive',
                 'source_key':'fixture','contract':'synthetic','parser':'synthetic','run':'synthetic'}
    out.execute("INSERT INTO source_artifact VALUES('fixture','talk_physical_archive','synthetic','synthetic','synthetic','fixture',0,'fixture')")
    record_unsupported(src,out,'fixture',component)
    _, _, mapping = research_index._component(src,out,'fixture',component,
        {'synthetic.json':('synthetic-source-seal',None)},0,0)
    project(src,out,'fixture',component,mapping,0,0)
    verify_required_source_projections(out,{'components':[component]},{'fixture':src})
    values = list(out.execute("SELECT status,indexed_count FROM adapter_coverage WHERE projection_kind IN ('relation','candidate') ORDER BY projection_kind"))
    assert [tuple(r) for r in values] == [('complete',0),('complete',1)]
    assert out.execute('SELECT status FROM research_relation').fetchone()[0] == 'unresolved'
    src.close()
    out.close()


def pipeline(output):
    """Two tiny source artifacts + real derived-index publication and receipts."""
    output.mkdir()
    job = object.__new__(Rebuild)
    job.output, job.source, job.history = output, output/'synthetic-source', None
    job.identity_root = ROOT
    arm = output.name
    body = '合成原文 '+arm
    source_sha = hashlib.sha256(body.encode()).hexdigest()

    def source_builder(path):
        with sqlite3.connect(path) as db:
            db.executescript('''
                CREATE TABLE loading_tip_row(id TEXT PRIMARY KEY,source_path,pointer);
                CREATE TABLE loading_tip_slot(id TEXT PRIMARY KEY,row_id,field,locale);
                CREATE TABLE loading_tip_text(id TEXT PRIMARY KEY,slot_id,raw_text,source_path,pointer);
            ''')
            for n in (1, 2):
                db.execute('INSERT INTO loading_tip_row VALUES(?,?,?)', (str(n),'synthetic.json','/'+str(n)))
                db.execute('INSERT INTO loading_tip_slot VALUES(?,?,?,?)', (str(n),str(n),'body','CHS'))
                db.execute('INSERT INTO loading_tip_text VALUES(?,?,?,?,?)',
                    (str(n),str(n),body+' '+str(n),'synthetic.json','/'+str(n)+'/body'))
        return {'artifact':seal(path)}

    def source_oracle(path, _):
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as db:
            if db.execute('SELECT raw_text FROM loading_tip_text ORDER BY id').fetchall() != [(body+' 1',),(body+' 2',)]:
                raise ValueError('synthetic source stage mismatch')
        return {'status':'PASS'}

    job.task('tips', source_builder, source_oracle)
    def core_builder(path):
        with sqlite3.connect(path) as db:
            # Keep another scope when the negative probe removes comparison.
            db.executescript('CREATE TABLE comparison_scope_proof(id INTEGER PRIMARY KEY);'
                             'INSERT INTO comparison_scope_proof VALUES(1);'
                             'CREATE TABLE candidate_scope_proof(id INTEGER PRIMARY KEY);')
        return {'artifact':seal(path)}

    def core_oracle(path, _):
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as db:
            if (db.execute('SELECT * FROM comparison_scope_proof').fetchall() != [(1,)] or
                db.execute('SELECT * FROM candidate_scope_proof').fetchall() != []):
                raise ValueError('synthetic core stage mismatch')
        return {'status':'PASS'}

    job.task('core',core_builder,core_oracle)
    core = job.path('core')
    provenance = output/'provenance.sqlite'
    with sqlite3.connect(provenance) as db:
        db.execute('CREATE TABLE source_file(relative_path,source_sha256)')
        db.execute('INSERT INTO source_file VALUES(?,?)', ('synthetic.json',source_sha))
    corpus = job.metadata_path('manifests/research/corpus.json')
    bundle = job.metadata_path('manifests/product/bundle.json')
    for name, count in (('m3-7.1-provider',20),('special-core-7.1',13)):
        # Exercise the real mapping reader; no claim to test 33 real parsers.
        job.bind('manifests/'+name+'/accepted-exact-mappings-v1.json', {'mappings':[
            {'provider_contract':'synthetic','parser_build_id':'synthetic','ingest_run_id':'synthetic'}
            for _ in range(count)]})
    components = []
    for key,family,mode,path in [('core','schema20_core','core33',core),
                               ('tips','loading_tips_archive','formal_compatible',job.path('tips'))]:
        receipt = output/key/'verified.json'
        components.append({'source_key':key,'family':family,'adapter':mode,
            'contract':'synthetic-contract','parser':'synthetic-parser-'+arm,'run':'synthetic-run-'+arm,
            'artifact':seal(path),'runtime_manifest':str(receipt),'runtime_manifest_sha256':seal(receipt)['sha256']})
    write(bundle, {'components':components,'product_accepted':False})
    manifest = {'corpus_ref':'synthetic-'+arm,'snapshot':{'id':'synthetic-snapshot','version':'7.1'},
        'components':components,'adapter_registry':'research-explicit-adapters-v2',
        'candidate_adapter_revision':VERSION,'adapter_implementation_sha256':{
            p.name:seal(p)['sha256'] for p in (ROOT/'src/genshin_lore').glob('research71_candidate_*.py')
            if p.stem != 'research71_candidate_query'},
        'product_bundle':{'path':'manifests/product/bundle.json','sha256':seal(bundle)['sha256']},
        'provenance_dependency':seal(provenance)}
    write(corpus, manifest)
    bindings = {'artifacts':{'core':str(core),'tips':str(job.path('tips'))},'provenance_artifact':str(provenance)}
    write(output/'bindings.json', bindings)
    reports = output/'index-oracles'
    inputs = job.dependency_inputs('core','tips')+[provenance,corpus,bundle,output/'bindings.json']
    job.task('research_index',lambda p:build(manifest,bindings,p,100_000_000,
        corpus_path=corpus,reports_dir=reports),
        lambda p,_:json.loads((reports/f'index-memory-oracle-{p.stem}.json').read_text()),inputs=inputs)
    with sqlite3.connect(job.path('research_index')) as db:
        counts = {t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in
            ('source_artifact','adapter_coverage','research_container','research_text','research_relation','research_candidate')}
    profile = {'status':'accepted','status_meaning':'local fixture qualification, NOT formal acceptance',
        'corpus_ref':manifest['corpus_ref'],'corpus_manifest':'manifests/research/corpus.json',
        'evidence_product_bundle':'manifests/product/bundle.json','evidence_product_bundle_sha256':seal(bundle)['sha256'],
        'qualification_scope':'local_7.1_candidate_only','product_accepted':False,
        'candidate_runtime_sha256':seal(ROOT/'src/genshin_lore/research71_candidate_query.py')['sha256'],
        'derived_index':{**seal(job.path('research_index')),'counts':counts}}
    job.bind('manifests/research/profile.json',profile)
    config = output/'runtime-config.json'
    write(config, {'corpus':str(corpus.relative_to(output)),
        'profile':'metadata/manifests/research/profile.json',
        'bindings':'bindings.json','index':'research_index/artifact.sqlite'})
    return job, manifest, bindings, config


@pytest.mark.parametrize('utf8_mode', ['1', '0'])
def test_actual_index_oracle_receipt_and_mcp_read_from_two_outputs(tmp_path, utf8_mode):
    work = tmp_path / '中文 space'
    work.mkdir()
    outputs = [pipeline(work/arm) for arm in ('a','b')]

    async def check(config, corpus, arm):
        params = StdioServerParameters(command=sys.executable,
            args=[str(ROOT/'scripts/serve_dialogue.py'),'--config',str(config)],
            cwd=str(tmp_path),env={**os.environ,'PYTHONPATH':str(ROOT/'src'),
                                   'PYTHONUTF8':utf8_mode})
        with anyio.fail_after(25):
            async with stdio_client(params) as (read,write_stream):
                async with ClientSession(read,write_stream) as client:
                    await client.initialize()
                    assert [t.name for t in (await client.list_tools()).tools] == TOOLS
                    async def call(name, **args):
                        result = payload(await client.call_tool(name, {'corpus_ref':corpus,**args}))
                        assert result['status'] == 'ok', result
                        return result['data']
                    description = await call('describe_corpus')
                    core = next(c for c in description['components'] if c['artifact_source_key']=='core')
                    assert core['unsupported_relation_tables'] == ['candidate_scope_proof','comparison_scope_proof']
                    assert core['relation_coverage_complete'] is False
                    first = await call('search_evidence',query='合成原文',locale='CHS',limit=1)
                    second = await call('search_evidence',query='合成原文',locale='CHS',limit=1,cursor=first['next_cursor'])
                    a, b = first['items'][0]['evidence_ref'], second['items'][0]['evidence_ref']
                    assert a != b
                    for ref in (a,b):
                        body = await call('read_evidence',evidence_ref=ref)
                        inspected = await call('inspect_evidence',evidence_ref=ref)
                        assert '合成原文 '+arm in body['raw_text']
                        assert inspected['evidence_ref'] == ref
                        assert body['provenance'] == inspected['text_provenance']
                        assert body['text_proof']['logical_identity_proven'] is False
                    await call('follow_evidence',evidence_ref=a)

    for job, manifest, bindings, config in outputs:
        anyio.run(check, config, manifest['corpus_ref'], job.output.name)
        # The oracle must still reject loss of the empty/nonprojected contract.
        with sqlite3.connect(':memory:') as changed:
            with sqlite3.connect(job.path('research_index')) as src:
                src.backup(changed)
            changed.execute("DELETE FROM adapter_coverage WHERE source_table='comparison_scope_proof'")
            with pytest.raises(ValueError, match='relation:comparison_scope_proof'):
                verify_connection(changed, manifest, bindings)
        with sqlite3.connect(':memory:') as changed:
            with sqlite3.connect(job.path('research_index')) as src:
                src.backup(changed)
            changed.execute("UPDATE adapter_coverage SET status='complete' WHERE source_table='comparison_scope_proof'")
            with pytest.raises(ValueError, match='extras/omissions in complete adapter'):
                verify_connection(changed, manifest, bindings)
        job.dependency_inputs('research_index')
