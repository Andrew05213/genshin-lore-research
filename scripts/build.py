"""Rebuild the bounded 7.1 data product from source, never from deployed DBs.

Each stage records its original builder and independent source verifier result.
Resume accepts only a verified artifact with the same seal; failed stages are
preserved and cannot be silently reused. Generated bindings are local metadata,
not inherited release acceptance.
"""
from __future__ import annotations
import argparse
import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import os
import sys
import time
import shutil

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from scripts.build_component import qualify, sha
from scripts.build_support import (
    RECEIPT_CONTRACT, load_object, pipeline_identity, require_success,
)
SNAPSHOT='anime-game-data:7.1:9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99'


def load(path):return json.loads(Path(path).read_text())


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
    if path.exists():
        if path.read_text()!=raw:raise FileExistsError('binding drift: '+str(path))
    else:
        with path.open('x') as stream:stream.write(raw)


def module(name):return importlib.import_module('genshin_lore.'+name)


def seal(path):return {'size_bytes':Path(path).stat().st_size,'sha256':sha(path)}


def allocated(paths):
    """Allocated bytes of owned paths, with inode and nested-root deduplication."""
    from genshin_lore.batch_space import _walk_bytes
    seen=set()
    return sum(_walk_bytes(Path(root),seen)[1] for root in paths)


def release(path):
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as db:
        for table,column in [('release','manifest_json'),('release','payload'),('release','json'),('foundation_release','manifest_json'),('event_narrative_release','manifest_json'),('document_release','manifest_json'),('native_archive_release','manifest_json'),('subtitle_release','manifest_json'),('readable_release','manifest_json'),('talk_physical_release','manifest_json'),('gcg_card_release','manifest_json'),('loading_tip_release','manifest_json'),('mail_release','manifest_json'),('native71_release','manifest_json'),('event71_release','manifest_json'),('codex_release','manifest_json')]:
            try:return json.loads(db.execute(f'SELECT {column} FROM {table}').fetchone()[0])
            except sqlite3.OperationalError:continue
        if {r[1] for r in db.execute('PRAGMA table_info(release)')} >= {'snapshot_id','source_commit','source_tree','contract','parser_build_id','ingest_run_id'}:
            db.row_factory=sqlite3.Row
            return dict(db.execute('SELECT * FROM release').fetchone())
    raise ValueError('no declared release in '+str(path))


def script_verify(name,arguments,result):
    env={**os.environ,'PYTHONPATH':str(ROOT/'src')}
    subprocess.run([sys.executable,str(ROOT/'scripts'/name),*map(str,arguments)],cwd=ROOT,env=env,check=True)
    return load(result)


class Rebuild:
    def __init__(self,source,output,history):
        self.source=source.resolve();self.output=output.resolve();self.history=history.resolve() if history else None
        qualify(self.source);self.output.mkdir(parents=True,exist_ok=True)

    def path(self,name):return self.output/name/'artifact.sqlite'
    def report(self,name):return load(self.output/name/'build.json')

    @property
    def metadata(self):
        return self.output/'metadata'

    def metadata_path(self, relative):
        path=Path(relative)
        if path.is_absolute():
            path=path.relative_to(ROOT)
        if '..' in path.parts:
            raise ValueError('metadata path outside build output')
        target=self.metadata/path
        if not target.resolve().is_relative_to(self.output.resolve()):
            raise ValueError('metadata path outside build output')
        return target

    def dependency_inputs(self,*names):
        paths=[]
        for name in names:
            directory=self.output/name
            receipt=load_object(directory/'verified.json')
            if receipt.get('contract')!=RECEIPT_CONTRACT or receipt.get('independent_verifier_completed') is not True:
                raise ValueError('dependency requires current complete verification: '+name)
            for record in ('build.json','verify.json','invocation.json'):
                if receipt.get('records',{}).get(record)!=seal(directory/record):
                    raise ValueError('dependency report drift: '+name)
            require_success(load_object(directory/'verify.json'))
            identity=receipt.get('identity',{})
            invocation=load_object(directory/'invocation.json')
            if (identity.get('parameters',{}).get('stage')!=name or
                    invocation.get('stage')!=name or invocation.get('identity')!=identity or
                    receipt.get('source_snapshot')!=SNAPSHOT or invocation.get('source_snapshot')!=SNAPSHOT):
                raise ValueError('dependency stage identity mismatch: '+name)
            actual=pipeline_identity(getattr(self,'identity_root',ROOT),self.source,self.history,
                inputs=identity.get('stage_inputs',{}),parameters=identity.get('parameters'))
            if actual!=identity:
                raise ValueError('dependency implementation/input identity drift: '+name)
            if seal(self.path(name))!=receipt.get('artifact'):
                raise ValueError('dependency artifact drift: '+name)
            if any(Path(str(self.path(name))+suffix).exists() for suffix in ('-wal','-shm','-journal')):
                raise ValueError('dependency SQLite sidecar: '+name)
            paths.extend([self.path(name),directory/'verified.json'])
        return paths

    def index_headroom(self):
        remaining=self.available-max(0,allocated((ROOT,self.output))-self.allocated_start)
        if remaining<7_000_000_000:
            raise ValueError('new stage allocations consumed required index peak headroom')
        return remaining

    def task(self,name,builder,verifier,*,inputs=(),parameters=None):
        directory=self.output/name;artifact=self.path(name)
        identity=pipeline_identity(getattr(self,'identity_root',ROOT),self.source,self.history,
                                   inputs=inputs,parameters={'stage':name,**(parameters or {})})
        if directory.exists():
            try:
                receipt=load_object(directory/'verified.json')
                if receipt.get('contract')!=RECEIPT_CONTRACT or receipt.get('identity')!=identity:
                    raise ValueError('resume identity mismatch or incompatible old receipt: '+name)
                if (receipt.get('source_snapshot')!=SNAPSHOT or
                        receipt.get('independent_verifier_completed') is not True or
                        receipt['artifact']!=seal(artifact) or
                        any(Path(str(artifact)+s).exists() for s in ('-wal','-shm','-journal'))):
                    raise ValueError('resume seal/sidecar drift: '+name)
                records={f:load_object(directory/f) for f in ('build.json','verify.json','invocation.json')}
                if receipt.get('records')!={f:seal(directory/f) for f in records}:
                    raise ValueError('resume record integrity drift: '+name)
                if records['invocation.json'].get('identity')!=identity:
                    raise ValueError('resume invocation identity mismatch: '+name)
                require_success(records['verify.json'])
                expected=records['build.json'].get('artifact',records['build.json'])
                if {k:expected.get(k) for k in ('size_bytes','sha256')}!=receipt['artifact']:
                    raise ValueError('resume build report artifact mismatch: '+name)
            except (ValueError,KeyError,OSError,TypeError) as exc:
                write(directory/('failure-'+str(time.time_ns())+'.json'),
                      {'phase':'resume','error_type':type(exc).__name__,'error':str(exc)})
                raise ValueError('resume rejected; existing evidence preserved: '+name+': '+str(exc)) from exc
            print('REUSE_VERIFIED',name,flush=True);return records['build.json']
        directory.mkdir();print('BUILD',name,flush=True)
        start=time.monotonic();phase='build'
        write(directory/'invocation.json',{'stage':name,'source_snapshot':SNAPSHOT,
              'identity':identity,'formal_release_accepted':False})
        try:
            report=builder(artifact)
            if not isinstance(report,dict) or not report:
                raise ValueError('builder returned no report')
            write(directory/'build.json',report)
            before=seal(artifact)
            expected=report.get('artifact',report)
            if {k:expected.get(k) for k in ('size_bytes','sha256')}!=before:
                raise ValueError('builder report artifact seal mismatch')
            phase='independent verification'
            check=verifier(artifact,report)
            write(directory/'verify.json',check)
            require_success(check)
            if seal(artifact)!=before or any(Path(str(artifact)+s).exists() for s in ('-wal','-shm','-journal')):
                raise ValueError('verification changed artifact or left sidecars')
            if pipeline_identity(getattr(self,'identity_root',ROOT),self.source,self.history,
                                 inputs=inputs,parameters={'stage':name,**(parameters or {})})!=identity:
                raise ValueError('stage implementation or inputs changed during execution')
            write(directory/'verified.json',{'contract':RECEIPT_CONTRACT,'identity':identity,
                  'artifact':before,'source_snapshot':SNAPSHOT,
                  'records':{f:seal(directory/f) for f in ('build.json','verify.json','invocation.json')},
                  'independent_verifier_completed':True,'seconds':time.monotonic()-start,
                  'formal_release_accepted':False})
        except BaseException as exc:
            write(directory/('failure-'+str(time.time_ns())+'.json'),
                  {'phase':phase,'error_type':type(exc).__name__,'error':str(exc)})
            raise
        print('VERIFIED',name,seal(artifact)['size_bytes'],flush=True);return report

    def bind(self,relative,value):write(self.metadata_path(relative),value)

    def bound_scope(self,name,value):
        path=self.output/'bound-scopes'/(name+'.json');write(path,value);return path

    def runtime(self,name,family,path_in_artifact=False):
        p=self.path(name);r=release(p)
        return {'source_snapshot':SNAPSHOT,'family':family,'family_contract_version':r.get('family_contract_version') or r.get('contract'),
                'parser_build_id':r.get('parser_build_id',r.get('parser')),'ingest_run_id':r.get('ingest_run_id',r.get('run_id',r.get('run'))),
                'artifact':{**seal(p),**({'path_machine_local':str(p)} if path_in_artifact else {})},'release':r,
                'product_accepted':False,'qualification':'local independently source-verified rebuild; no deployed artifact reused'}

    def core(self):
        m3=module('m3_7_1_provider_build');mv=module('m3_7_1_provider_verify')
        recipe=load(ROOT/'recipes/m3-7.1-provider-mappings.json')
        for family in sorted({m['family'] for m in recipe['mappings']}):
            self.task(family,lambda p,f=family:m3.build(self.source,f,p),
                      lambda p,r,f=family:mv.verify(self.source,f,p,self.output/f/'build.json'))
        for m in recipe['mappings']:
            p=self.path(m['family']);r=release(p)
            m['candidate_artifact']={**seal(p),'path_machine_local':str(p)}
            m['parser_build_id']=r['parser_build_ids'][m['version']];m['ingest_run_id']=r['ingest_run_ids'][m['version']]
            proof=self.output/m['family']/'verify.json'
            m['independent_verifier_reports']=[{'path':str(proof),'sha256':sha(proof)}]
        self.bind('manifests/m3-7.1-provider/accepted-exact-mappings-v1.json',recipe)
        special=load(ROOT/'recipes/special-core-7.1-mappings.json')
        for m in special['mappings']:
            family=m['family'];kind='special_historical' if m['kind']=='HISTORICAL_SPECIAL' else 'special_core'
            builder=module(kind+'_7_1_build');verifier=module(kind+'_7_1_verify')
            self.task(family,lambda p,f=family,b=builder:b.build(self.source,f,p),
                      lambda p,r,f=family,v=verifier:v.verify(self.source,f,p))
            p=self.path(family);r=release(p)
            m['candidate_artifact']={**seal(p),'path_machine_local':str(p)}
            m['parser_build_id']=r['parser_build_id'];m['ingest_run_id']=r['ingest_run_id']
        self.bind('manifests/special-core-7.1/accepted-exact-mappings-v1.json',special)
        dependencies=sorted({m['family'] for m in recipe['mappings']+special['mappings']})
        self.task('schema20_core',lambda p:module('schema20_7_1_candidate').build(self.source,p,include_special=True,metadata_root=self.metadata),
                  lambda p,r:module('schema20_7_1_oracle').verify(self.source,p,r,metadata_root=self.metadata),
                  inputs=self.dependency_inputs(*dependencies)+[
                      self.metadata_path('manifests/m3-7.1-provider/accepted-exact-mappings-v1.json'),
                      self.metadata_path('manifests/special-core-7.1/accepted-exact-mappings-v1.json')])

    def character(self):
        scope=ROOT/'manifests/character-7.1-physical-foundation/source-scope.json'
        self.task('character_foundation',lambda p:module('character_7_1_foundation_build').build(self.source,scope,p),
                  lambda p,r:module('character_7_1_foundation_verify').verify(self.source,scope,p,expected_artifact=r['artifact']))
        manifest=load(ROOT/'recipes/character-foundation.json')
        manifest['artifact']={**seal(self.path('character_foundation')),'machine_local_path':str(self.path('character_foundation'))}
        self.bind('manifests/character-7.1-physical-foundation/immutable-runtime.json',manifest)
        builder=module('character_7_1_successor_build');verifier=module('character_7_1_successor_verify')
        for role in ('text','lore','voice'):
            def build(p,role=role):
                manifest=self.metadata_path('manifests/character-7.1-physical-foundation/immutable-runtime.json')
                if role=='text':return builder.build_text(self.source,self.path('character_foundation'),p,foundation_manifest=manifest)
                return getattr(builder,'build_'+role)(self.path('character_foundation'),self.path('character_text'),p,foundation_manifest=manifest)
            self.task('character_'+role,build,lambda p,r,k=role:verifier.verify(k,self.path('character_foundation'),p,source_root=self.source if k=='text' else None,text_path=None if k=='text' else self.path('character_text'),expected_artifact=r['artifact'],foundation_manifest=self.metadata_path('manifests/character-7.1-physical-foundation/immutable-runtime.json')),
                      inputs=self.dependency_inputs('character_foundation',*(['character_text'] if role!='text' else []))+
                      [self.metadata_path('manifests/character-7.1-physical-foundation/immutable-runtime.json')])

    def narrative(self):
        for family in ('npc','dialog','reminder'):
            self.task('narrative_'+family,lambda p,f=family:module('narrative_7_1_build').build(f,self.source,p,ROOT),
                      lambda p,r:module('narrative_7_1_verify').verify(self.source,p,r))

    def simple(self):
        for name,mod,scope in (
            ('document','document_7_1','document-readable-7.1/source-scope.json'),
            ('gcg_card','gcg_card_7_1','gcg-card-7.1/scope.json'),
            ('loading_tips','loading_tips_7_1','loading-tips-7.1/scope.json'),
            ('mail','mail_7_1','mail-archive-7.1/source-scope-v1.json'),
            ('native_message','native_message_7_1','native-archive-7.1/message-scope-v1.json')):
            sp=ROOT/'manifests'/scope;builder=module(mod+'_build');verifier=module(mod+'_verify')
            self.task(name,lambda p,b=builder,s=sp:b.build(self.source,s,p),
                      lambda p,r,v=verifier,s=sp,n=name:v.verify(self.source,s,p) if n=='loading_tips' else v.verify(self.source,s,p,r))

    def quest(self):
        scope=ROOT/'manifests/quest-talk-7.1/source-scope.json'
        qt=self.task('quest_talk',lambda p:module('quest_talk_7_1_build').build(self.source,scope,p),
                     lambda p,r:module('quest_talk_7_1_verify').verify(self.source,scope,p,r))
        qm={'contract':qt['contract'],'status':'accepted_standalone_candidate','source_snapshot':SNAPSHOT,
            'source_commit':'9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99','source_tree':'aa578c08c402af39b41f1ffc95124d10f5410272',
            'parser_build_id':qt['parser_build_id'],'ingest_run_id':qt['ingest_run_id'],'artifact':seal(self.path('quest_talk')),
            'local_artifact_path':str(self.path('quest_talk')),
            'product_accepted':False,'qualification':'local independently source-verified rebuild, not formal release'}
        qmp=self.metadata_path('manifests/quest-talk-7.1/standalone-candidate.json');self.bind('manifests/quest-talk-7.1/standalone-candidate.json',qm)
        bn=importlib.import_module('scripts.builders.quest_narrative');vn=importlib.import_module('scripts.validators.quest_narrative')
        self.task('quest_narrative',lambda p:bn.build(self.source,p,self.path('quest_talk'),str(qmp)),
                  lambda p,r:vn.verify(self.source,p,self.output/'quest_narrative/build.json',self.path('quest_talk'),qmp),
                  inputs=self.dependency_inputs('quest_talk')+[qmp])
        if self.history is None:raise ValueError('--historical-source required for the original Review field-continuity oracle')
        br=importlib.import_module('scripts.builders.quest_review');vr=importlib.import_module('scripts.validators.quest_review')
        self.task('quest_review',lambda p:br.build(self.source,p,qmp,self.path('quest_talk')),
                  lambda p,r:vr.verify(self.source,p,self.output/'quest_review/build.json',qmp,self.path('quest_talk'),self.history),
                  inputs=self.dependency_inputs('quest_talk')+[qmp])
        components={}
        for name in ('quest_narrative','quest_review'):
            p=self.path(name);r=release(p)
            components[name+'_archive']={'acceptance_status':'PHYSICAL_FOUNDATION_ACCEPTED','source_snapshot':SNAPSHOT,
                'product_accepted':False,'artifact':{**seal(p),'path_machine_local':str(p)},
                **{k:r[k] for k in ('contract','parser_build_id','ingest_run_id')}}
        self.bind('manifests/quest-derived-7.1/standalone-candidates-v1.json',{'components':components,'qualification':'local source-oracle passed; not current authority'})
        b=importlib.import_module('scripts.builders.quest_review_binding_v2');v=importlib.import_module('scripts.validators.quest_review_binding_v2')
        manifest=self.metadata_path('manifests/quest-derived-7.1/standalone-candidates-v1.json')
        self.task('quest_review_binding',lambda p:b.build(self.source,p,manifest_path=manifest),lambda p,r:v.verify(self.source,p,self.output/'quest_review_binding/build.json',manifest_path=manifest),
                  inputs=self.dependency_inputs('quest_narrative','quest_review')+[manifest])
        b=module('talk_physical_7_1')
        self.task('talk_physical',lambda p:b.build(self.source,self.path('quest_talk'),seal(self.path('quest_talk')),p),
                  lambda p,r:importlib.import_module('scripts.validators.talk_physical').verify(self.source,p,self.path('quest_talk'),seal(p)),
                  inputs=self.dependency_inputs('quest_talk'))

    def codex(self):
        b=importlib.import_module('scripts.builders.codexquest');v=importlib.import_module('scripts.validators.codexquest')
        mapping=self.metadata_path('manifests/special-core-7.1/accepted-exact-mappings-v1.json')
        self.task('codexquest',lambda p:b.build(self.source,p,mapping,self.path('quest_codex_archive')),
                  lambda p,r:v.verify(self.source,p,self.output/'codexquest/build.json',mapping,self.path('quest_codex_archive')),
                  inputs=self.dependency_inputs('quest_codex_archive')+[mapping])

    def r0(self):
        b=module('batch_m3_physical')
        parser='pb-batch-m3-r0-physical-7.1:23e78b08f997ab610cd55a11f91f2f4c252260af'
        run='ir-batch-m3-r0-physical-7.1:9fc2c32cc6bb81dd6091e504d43e40bdcf930ea2'
        def build(path):
            report=b.build_r0_artifact(self.source,path,repo_root=ROOT,snapshot_id=SNAPSHOT,
                  parser_build_id=parser,ingest_run_id=run,artifact_limit_bytes=6*1024**3)
            # Adapt R0's native report; task still compares this declared seal
            # to actual bytes and requires the independent source verifier.
            return {**report,'artifact':{'size_bytes':report['artifact_size_bytes'],
                                        'sha256':report['artifact_sha256']}}
        self.task('r0',build,
                  lambda p,r:script_verify('validators/source_archive.py',('--source',self.source,'--artifact',p,'--output',p.parent/'source-oracle.json'),p.parent/'source-oracle.json'))

    def readable(self):
        scope=ROOT/'manifests/document-readable-7.1/source-scope.json'
        self.task('readable',lambda p:module('readable_7_1_build').build(self.source,scope,p,self.path('r0'),self.path('document'),self.report('document')),
                  lambda p,r:module('readable_7_1_verify').verify(self.source,scope,p,self.path('r0'),self.path('document'),self.report('document'),r),
                  inputs=self.dependency_inputs('r0','document'))
        scope=ROOT/'manifests/direct-text-7.1/subtitle-scope.json'
        def verify_subtitle(p,r):
            rel=release(p)
            runtime=self.bound_scope('subtitle-runtime',{'artifact':seal(p),'counts':rel['counts'],'release':rel,
                'qualification':'proposed local binding; independent verification still required'})
            return module('direct_text_7_1_verify').verify_subtitle(self.source,scope,p,self.path('r0'),runtime_manifest_path=runtime)
        self.task('subtitle',lambda p:module('direct_text_7_1_build').build_subtitle(self.source,scope,p,self.path('r0')),
                  verify_subtitle,inputs=self.dependency_inputs('r0'))

    def events(self):
        if self.history is None:raise ValueError('--historical-source required by the original Event field-continuity oracles')
        for number,mod in ((10,'event_legacy_7_1'),(11,'event_successor_7_1'),(14,'event_tps_7_1'),(17,'event_native_text_7_1')):
            name='event_'+str(number);filename='native-text-wave-scope-v1.json' if number==17 else f'component-{number}-scope-v1.json'
            sp=ROOT/'manifests/event-narrative-7.1'/filename;b=module(mod+'_build');v=module(mod+'_verify')
            self.task(name,lambda p,b=b,s=sp:b.build(self.source,s,p),
                      lambda p,r,v=v,s=sp,n=number:v.verify(self.source,s,p,r) if n==17 else v.verify(self.source,self.history,s,p,r))
        qt_path=self.metadata_path('manifests/quest-talk-7.1/standalone-candidate.json');qt=load(qt_path)
        scope=load(ROOT/'manifests/event-narrative-7.1/component-12-scope-v1.json')
        scope['talk_candidate_namespace'].update(accepted_manifest_sha256=sha(qt_path),
            artifact=qt['artifact'],parser=qt['parser_build_id'],run=qt['ingest_run_id'])
        b=module('event_filmfest_7_1_build');scope['talk_candidates']=b._target_candidates(self.source,self.path('quest_talk'),scope)
        sp=self.bound_scope('event12',scope)
        self.task('event_12',lambda p:b.build(self.source,sp,self.path('quest_talk'),p),
                  lambda p,r:module('event_filmfest_7_1_verify').verify(self.source,self.history,sp,self.path('quest_talk'),p),
                  inputs=self.dependency_inputs('quest_talk')+[qt_path,sp])
        sp=ROOT/'manifests/event-narrative-7.1/component-15-scope-v1.json'
        self.task('event_15',lambda p:module('event_photo_echo_7_1_build').build(self.source,sp,p),
                  lambda p,r:module('event_photo_echo_7_1_verify').verify(self.source,sp,p,r,{}))
        sp=ROOT/'manifests/event-narrative-7.1/component-13-physical-scope-v1.json'
        self.task('event_13',lambda p:module('event13_7_1_physical').build(self.source,sp,p),
                  lambda p,r:module('event13_7_1_physical_verify').verify(self.source,sp,p))
        for n in (13,15):self.bind(f'manifests/event-narrative-7.1/component-{n}-physical-runtime-v1.json' if n==13 else 'manifests/event-narrative-7.1/component-15-runtime-v1.json',self.runtime('event_'+str(n),'event_narrative_archive',True))
        families={f:{'artifact':{**seal(self.path('narrative_'+f)),'path_machine_local':str(self.path('narrative_'+f))},'release':release(self.path('narrative_'+f))} for f in ('npc','dialog','reminder')}
        self.bind('manifests/narrative-7.1/runtime.json',{'families':families})
        scope=load(ROOT/'manifests/event-narrative-7.1/component-16-18-physical-scope-v1.json')
        for key,name,manifest in (
            ('event15_photo','event_15','manifests/event-narrative-7.1/component-15-runtime-v1.json'),
            ('dialog','narrative_dialog','manifests/narrative-7.1/runtime.json'),
            ('reminder','narrative_reminder','manifests/narrative-7.1/runtime.json')):
            scope['accepted_inputs'][key].update(path_machine_local=str(self.path(name)),**seal(self.path(name)),
                runtime_manifest=str(self.metadata_path(manifest)),runtime_manifest_sha256=sha(self.metadata_path(manifest)),release=release(self.path(name)))
        sp=self.bound_scope('event16-18',scope)
        for n in (16,18):
            self.task('event_'+str(n),lambda p,n=n:module('event16_18_7_1_selection').build(self.source,sp,str(n),p),
                      lambda p,r:module('event16_18_7_1_selection_verify').verify(self.source,sp,p),
                      inputs=self.dependency_inputs('event_15','narrative_dialog','narrative_reminder')+[sp])
        self.bind('manifests/event-narrative-7.1/component-16-physical-runtime-v1.json',self.runtime('event_16','event_narrative_archive',True))

    def native(self):
        scope=ROOT/'manifests/native-archive-7.1/bulk-scope.json'
        self.task('native_bulk',lambda p:module('native_bulk_7_1_build').build(self.source,scope,p),
                  lambda p,r:module('native_bulk_7_1_verify').verify(self.source,scope,p))
        self.bind('manifests/native-archive-7.1/bulk-runtime.json',self.runtime('native_bulk','native_archive'))
        self.bind('manifests/native-archive-7.1/message-runtime-v1.json',self.runtime('native_message','native_archive'))
        sp=self.output/'bound-scopes/native-book-music.json'
        if not sp.exists():
            env={**os.environ,'PYTHONPATH':str(ROOT/'src')}
            subprocess.run([sys.executable,str(ROOT/'scripts/maintenance/native_book_music_scope.py'),'--source',str(self.source),
                '--message-artifact',str(self.path('native_message')),'--message-runtime',str(self.metadata_path('manifests/native-archive-7.1/message-runtime-v1.json')),'--output',str(sp)],env=env,cwd=ROOT,check=True)
        self.task('native_book_music',lambda p:module('native_book_music_7_1_build').build(self.source,sp,p),
                  lambda p,r:module('native_book_music_7_1_verify').verify(self.source,sp,p,self.path('native_message')),
                  inputs=self.dependency_inputs('native_message')+[sp])
        scope=load(ROOT/'manifests/native-archive-7.1/aux-quest-scope-v1.json')
        ext,deps=module('native_aux_quest_7_1_verify').expected_external(self.source,self.path('quest_narrative'),self.path('native_bulk'))
        scope.update(external_candidates=ext,dependencies=deps)
        scope['main_quest_reconciliation'].update(accepted_parser=deps['quest_narrative']['parser'],accepted_run=deps['quest_narrative']['run'])
        sp=self.bound_scope('native-aux-quest',scope)
        self.task('native_aux_quest',lambda p:module('native_aux_quest_7_1_build').build(self.source,sp,p),
                  lambda p,r:module('native_aux_quest_7_1_verify').verify(self.source,sp,p,self.path('quest_narrative'),self.path('native_bulk')),
                  inputs=self.dependency_inputs('quest_narrative','native_bulk')+[sp])
        binding=load(ROOT/'recipes/event13-snez-binding.json');r=release(self.path('event_13'))
        with sqlite3.connect(self.path('event_13')) as db:
            binding['occurrences']=[{'ordinal':o,'pointer':p,'raw_sha256':s,'target_occurrence_id':i} for i,o,p,s in
                db.execute('SELECT id,ordinal,pointer,raw_sha256 FROM physical_row WHERE source_path=? ORDER BY ordinal',(binding['source_path'],))]
        binding.update(target_artifact={**seal(self.path('event_13')),'path_machine_local':str(self.path('event_13'))},target_parser=r['parser_build_id'],target_run=r['ingest_run_id'])
        self.bind('manifests/event-narrative-7.1/component-13-snez-physical-binding-v1.json',binding)
        scope=load(ROOT/'manifests/native-archive-7.1/successor-scope-v1.json')
        scope['event13_snez_binding']=str(self.metadata_path(scope['event13_snez_binding']))
        scope['event13_snez_binding_sha256']=sha(Path(scope['event13_snez_binding']));sp=self.bound_scope('native21',scope)
        self.task('native_successor',lambda p:module('native21_7_1_build').build(self.source,sp,p,metadata_root=self.metadata),
                  lambda p,r:module('native21_7_1_verify').verify(self.source,sp,p,metadata_root=self.metadata),
                  inputs=self.dependency_inputs('event_13','quest_narrative','narrative_npc','quest_talk')+[sp,*module('native21_7_1_build').dependency_manifests(self.metadata)])
        scope=load(ROOT/'manifests/native-archive-7.1/high-value-scope-v1.json')
        event_path=self.metadata_path('manifests/event-narrative-7.1/component-16-physical-runtime-v1.json');event=load(event_path)
        with sqlite3.connect(self.path('event_16')) as db:
            scope['event16_reuse_targets']=[{'source_path':path,'source_pointer':pointer,'ordinal':o,'raw_sha256':s,'target_occurrence_id':rid} for rid,path,pointer,o,s in db.execute("SELECT id,source_path,pointer,ordinal,raw_sha256 FROM selected_occurrence WHERE source_kind='talk_excel_support' ORDER BY ordinal,id")]
        scope.update(event16_artifact=event['artifact'],event16_parser=event['parser_build_id'],event16_run=event['ingest_run_id'],
            event16_manifest_sha256=sha(event_path))
        sp=self.bound_scope('native26',scope)
        self.task('native_high_value',lambda p:module('native26_7_1_physical').build(self.source,sp,p,metadata_root=self.metadata),
                  lambda p,r:module('native26_7_1_verify').verify(self.source,sp,p,metadata_root=self.metadata),
                  inputs=self.dependency_inputs('event_16','quest_talk')+[sp,*module('native26_7_1_physical').dependency_manifests(self.metadata)])

    def index(self):
        """Rebind the existing full projector/oracle to THIS rebuild only."""
        builder=importlib.import_module('scripts.builders.research_dataset')
        caps=load(ROOT/'recipes/capability-names.json');components=[]
        bindings={'provenance_artifact':str(self.path('r0')),'artifacts':{}}
        names={'codexquest_archive':'codexquest','document_archive':'document',
            'readable_archive':'readable','quest_narrative_archive':'quest_narrative',
            'quest_review_archive':'quest_review','quest_talk_physical':'quest_talk',
            'loading_tips':'loading_tips','mail_narrative':'mail',
            'event_native_text_17':'event_17','native_successor_physical':'native_successor',
            'native_high_value_physical':'native_high_value'}
        def component(name,key,family,contract,adapter,**extra):
            receipt=self.output/name/'verified.json'
            self.dependency_inputs(name)
            r=release(self.path(name)) if name!='schema20_core' else {}
            c={'source_key':key,'family':family,'contract':contract,
                'parser':r.get('parser_build_id',r.get('parser','assembly-only-not-evidence-parser')),
                'run':r.get('ingest_run_id',r.get('run_id',r.get('run','assembly-only-not-evidence-run'))),
                'adapter':adapter,'artifact':seal(self.path(name)),
                'runtime_manifest':str(receipt),'runtime_manifest_sha256':sha(receipt),
                'limitations':['declared source scope only; physical candidates are not logical ownership'],**extra}
            components.append(c);bindings['artifacts'][key]=str(self.path(name))
        component('schema20_core','schema20-core-7.1','schema20_core','schema20-7.1-core33-materialization-v1','core33')
        for cap in caps['capabilities']:
            cid=cap['capability_id']
            if cid.startswith('character_'):continue
            name=names.get(cid,cid)
            if cid.startswith(('event_narrative_','event_physical_')):name='event_'+cid.rsplit('_',1)[1]
            r=release(self.path(name));contract=cap['contract']
            if cid.startswith('event_physical_'):contract=r['contract']
            component(name,'7.1-'+cid,'quest_talk_pilot' if cid=='quest_talk_physical' else cap['family'],
                contract,cid if cid in builder.SPECS else 'formal_compatible',
                registered_capability_id=cid,limitations=cap['limitations'])
        for role in ('foundation','text','lore','voice'):
            r=release(self.path('character_'+role))
            component('character_'+role,'7.1-character-'+role,'character_archive',r['contract'],
                'character_'+role if role in ('foundation','text') else 'dependency_only',derived_role=role)
        for name,key,family,adapter in (
            ('quest_review_binding','7.1-quest-review-binding','quest_review_archive','quest_review_binding'),
            ('gcg_card','7.1-gcg-card','gcg_card_archive','formal_compatible'),
            ('subtitle','7.1-subtitle','subtitle_archive','formal_compatible'),
            ('talk_physical','7.1-talk-physical','talk_physical_archive','talk_physical')):
            r=release(self.path(name))
            component(name,key,family,r.get('contract',r.get('family_contract_version')),adapter)
        if len(components)!=35:raise ValueError('35 exact input denominator changed')
        bundle={'contract':'candidate-7.1-complete-product-bundle-v1','status':'local_source_verified_rebuild',
            'product_accepted':False,'corpus_complete':False,'snapshot':SNAPSHOT,
            'core_provider_contracts_materialized':33,'components':components,
            'historical_contract_gaps':8,'historical_component_gap_records':12}
        bp=self.metadata_path('manifests/product/candidate-7.1-complete-bundle-v1.json');write(bp,bundle)
        manifest={'corpus_ref':'candidate-7.1-high-value-v1','status':'candidate',
            'adapter_registry':'research-explicit-adapters-v2','candidate_adapter_revision':builder.VERSION,
            'adapter_implementation_sha256':{p.name:sha(p) for p in sorted((ROOT/'src/genshin_lore').glob('research71_candidate_*.py')) if p.stem!='research71_candidate_query'},
            'snapshot':{'id':SNAPSHOT,'version':'7.1'},
            'product_bundle':{'path':str(bp.relative_to(self.metadata)),'sha256':sha(bp),'product_accepted':False},
            'provenance_dependency':seal(self.path('r0')),'components':components,'outer_corpus_complete':False,
            'terminal_unresolved':['historical semantic gaps retained','snapshot existence is not first release evidence']}
        cp=self.metadata_path('manifests/research/candidate-7.1-complete-v1.json');write(cp,manifest)
        write(self.output/'complete-bindings.json',bindings)
        reports=self.output/'index-oracles';reports.mkdir(exist_ok=True)
        # The independently verified complete source rowsets are checked by
        # the original builder before publishing its deterministic index.
        # The initial whole-project headroom already includes any resume inputs.
        # Deduct only this invocation's new owned bytes, never count them twice.
        remaining=self.index_headroom()
        self.task('research_index',lambda p:builder.build(manifest,bindings,p,remaining,corpus_path=cp,reports_dir=reports),
            lambda p,r:load(reports/('index-memory-oracle-'+p.stem+'.json')),
            inputs=self.dependency_inputs(*[p.parent.name for p in map(Path,bindings['artifacts'].values())])+[cp,self.output/'complete-bindings.json'])
        index=self.path('research_index')
        with sqlite3.connect(index) as db:
            counts={t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in
                ('source_artifact','adapter_coverage','research_container','research_text','research_relation','research_candidate')}
        profile={'status':'accepted','status_meaning':'legacy prepublication gate, NOT deployed release acceptance',
            'corpus_ref':manifest['corpus_ref'],'corpus_manifest':str(cp.relative_to(self.metadata)),
            'evidence_product_bundle':str(bp.relative_to(self.metadata)),'evidence_product_bundle_sha256':sha(bp),
            'product_accepted':False,'full_research_7_1_accepted':False,'qualification_scope':'local_7.1_candidate_only',
            'candidate_runtime_sha256':sha(ROOT/'src/genshin_lore/research71_candidate_query.py'),
            'derived_index':{**seal(index),'counts':counts}}
        pp=self.metadata_path('manifests/research/candidate-7.1-complete-profile-v1.json');write(pp,profile)
        self.bind('manifests/research-7.1/7.1-capability-registry-v1.json',caps)
        paths=('src/genshin_lore/narrative_assistant_7_1.py','src/genshin_lore/research71_preview_server.py','src/genshin_lore/research71_unified.py')
        self.bind('manifests/research/candidate-7.1-agent-runtime-v2.json',{
            'contract':'research71-unified-agent-runtime-v1','status':'local_candidate_qualification','snapshot':SNAPSHOT,
            'product_accepted':False,'full_research_7_1_accepted':False,'legacy_fallback':False,
            'implementation_sha256':{p:sha(ROOT/p) for p in paths},
            'corpus_manifest_sha256':sha(cp),'candidate_profile_sha256':sha(pp)})
        write(self.output/'runtime-config.json',{'corpus':str(cp.relative_to(self.output)),'profile':str(pp.relative_to(self.output)),
            'bindings':'complete-bindings.json','index':str(index.relative_to(self.output))})
        from scripts.serve import smoke
        write(self.output/'runtime-smoke.json',smoke(self.output/'runtime-config.json'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--historical-source',type=Path,help='Pinned historical source for independent field-continuity oracles; not a database')
    parser.add_argument('--output-root',type=Path,required=True)
    stages=('core','character','narrative','simple','quest','codex','r0','readable','events','native','index')
    parser.add_argument('--stage',choices=(*stages,'all'),required=True)
    parser.add_argument('--available-allocated-bytes',type=int,default=0,help='Fresh project hard-limit headroom, NOT host free space; required for all/index')
    args=parser.parse_args()
    if args.stage in ('all','quest','events'):
        if args.historical_source is None:parser.error('--historical-source required for independent field-continuity verification')
        proof=load(ROOT/'manifests/quest-derived-7.1/review-field-continuity-v1.json')
        actual=subprocess.check_output(['git','-C',str(args.historical_source),'rev-parse','HEAD'],text=True).strip()
        dirty=subprocess.check_output(['git','-C',str(args.historical_source),'status','--porcelain','--untracked-files=all'],text=True).strip()
        if actual!=proof['old_source_commit'] or dirty:raise ValueError('clean pinned historical source required; no database substitution')
    if args.stage in ('all','index'):
        peak=25_000_000_000 if args.stage=='all' else 7_000_000_000
        parent=args.output_root.resolve()
        while not parent.exists():parent=parent.parent
        if args.available_allocated_bytes<peak or shutil.disk_usage(parent).free<peak+1_000_000_000:
            raise ValueError('insufficient freshly measured project/host peak headroom')
    allocated_start=allocated((ROOT,args.output_root))
    job=Rebuild(args.source,args.output_root,args.historical_source)
    job.available=args.available_allocated_bytes
    job.allocated_start=allocated_start
    for stage in stages if args.stage=='all' else (args.stage,):getattr(job,stage)()


if __name__=='__main__':main()
