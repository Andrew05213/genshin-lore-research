"""v4 physical containers on the existing Quest/Talk exact-run transport."""
from __future__ import annotations
import json
from pathlib import Path
import sqlite3
from copy import deepcopy
from .family_evidence import FamilyEvidenceResult
from .r5_quest_talk_retrieval import R5QuestTalkError, _fingerprint, _identity, _cursor, _decode, _filters
from .quest_talk_physical_shapes import CONTRACT


def _reviewed_manifest_digests():
    catalog=json.loads((Path(__file__).resolve().parents[2]/'manifests/corpora/quest-talk-runtime-catalog.json').read_text())
    if catalog.get('contract')!='quest-talk-runtime-catalog-v1':raise R5QuestTalkError('runtime catalog contract')
    return {e['runtime_manifest_sha256'] for e in catalog['entries'] if e['status'] in ('release_verified','accepted_non_product_candidate','accepted_product_component')}


class QuestTalkPhysicalProvider:
    family='quest_talk_pilot'
    family_contract_version=CONTRACT
    runtime_contract='quest-talk-physical-runtime-v1'
    mapping_version='physical-shapes-v1'
    product_accepted=False
    binding_keys=('scope_manifest','shape_manifest')

    def __init__(self,artifact_path,runtime_manifest_path):
        self.artifact_path=Path(artifact_path).absolute();self.runtime_manifest_path=Path(runtime_manifest_path).absolute()
        self._invalidated=False;self._bindings={}
        mf=_fingerprint(self.runtime_manifest_path);self.manifest=json.loads(self.runtime_manifest_path.read_text())
        m=self.manifest
        if m.get('family')!='quest_talk_pilot' or m['contract']!=self.runtime_contract or m['family_contract_version']!=self.family_contract_version or m['corpus_complete'] is not False or m['product_accepted'] is not self.product_accepted:raise R5QuestTalkError('physical runtime contract mismatch')
        if mf[4] not in _reviewed_manifest_digests():raise R5QuestTalkError('unreviewed physical runtime manifest')
        self._bindings[self.runtime_manifest_path]=mf[:4]
        for key in self.binding_keys:
            path=self.runtime_manifest_path.parent/m[key]['path'];fp=_fingerprint(path)
            if fp[4]!=m[key]['sha256']:raise R5QuestTalkError('physical manifest binding mismatch')
            self._bindings[path]=fp[:4]
        self._assert_stable()
        fp=_fingerprint(self.artifact_path)
        if fp[2]!=m['artifact']['size_bytes'] or fp[4]!=m['artifact']['sha256']:raise R5QuestTalkError('physical artifact seal mismatch')
        self._bindings[self.artifact_path]=fp[:4]
        self.ingest_run_id=m['ingest_run_id'];self.coverage=m['coverage']
        self.conn=sqlite3.connect(self.artifact_path.as_uri()+'?mode=ro&immutable=1',uri=True,check_same_thread=False);self.conn.row_factory=sqlite3.Row
        if [tuple(r) for r in self.conn.execute('SELECT version,applied_at FROM schema_migration ORDER BY version')]!=[(i,'2000-01-01T00:00:00Z') for i in range(1,8)]:raise R5QuestTalkError('physical base ledger mismatch')
        actual_scope=json.loads(self.conn.execute('SELECT scope_json FROM ingest_run').fetchone()[0])
        frozen_scope=json.loads((self.runtime_manifest_path.parent/m['scope_manifest']['path']).read_text())
        if actual_scope != frozen_scope:raise R5QuestTalkError('artifact/scope binding mismatch')
        pins=['26df1dfbdf05a82bbb1d97506859f3e1c40718d8','d891ef1d95fdd6c27a1da8a32ea7a61b483647a6']
        if [frozen_scope['source_commit'],frozen_scope['source_tree']]!=pins:raise R5QuestTalkError('runtime source pin')
        snapshot=self.conn.execute('SELECT id,\"commit\",tree FROM source_snapshot').fetchall()
        if [tuple(r) for r in snapshot]!=[(m['snapshot_id'],*pins)]:raise R5QuestTalkError('runtime snapshot binding')
        runs=self.conn.execute('SELECT id,parser_build_id,snapshot_id FROM ingest_run').fetchall()
        if [tuple(r) for r in runs]!=[(self.ingest_run_id,m['parser_build_id'],m['snapshot_id'])]:raise R5QuestTalkError('physical run identity mismatch')
        structure=[list(r) for r in self.conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name LIKE 'quest_talk_physical_%' OR tbl_name LIKE 'quest_talk_physical_%' ORDER BY type,name")]
        if structure!=m['schema_extension_structure']:raise R5QuestTalkError('extension structure mismatch')
        parser=self.conn.execute('SELECT id,parser_version,mapping_version,fingerprint,config_checksum FROM parser_build').fetchall()
        if [tuple(r) for r in parser]!=[(m['parser_build_id'],self.family_contract_version,self.mapping_version,m['parser_fingerprint'],m['scope_manifest']['sha256'])]:raise R5QuestTalkError('parser build mismatch')
        if self.conn.execute('SELECT status FROM ingest_run').fetchone()[0]!='complete':raise R5QuestTalkError('incomplete ingest')
        required={'quest_talk_physical_container','quest_talk_physical_node','quest_talk_physical_text','quest_talk_physical_reference','quest_files','selected_talk_files','lookup_files'}
        if set(m['counts'])!=required:raise R5QuestTalkError('count seal keys mismatch')
        for table,count in m['counts'].items():
            if table.startswith('quest_talk_physical_') and self.conn.execute('SELECT count(*) FROM '+table).fetchone()[0]!=count:raise R5QuestTalkError('physical count seal mismatch')
        for key,sql in [('quest_files',"SELECT count(*) FROM quest_talk_physical_container WHERE kind='quest_root'"),('selected_talk_files',"SELECT count(*) FROM quest_talk_physical_container WHERE kind='talk_root'"),('lookup_files',"SELECT count(*) FROM source_file WHERE relative_path LIKE 'BinOutput/Talk/%'")]:
            if self.conn.execute(sql).fetchone()[0]!=m['counts'][key]:raise R5QuestTalkError('derived count seal mismatch')
        if self.conn.execute('PRAGMA foreign_key_check').fetchall():raise R5QuestTalkError('physical FK mismatch')
        self._assert_stable()

    def _assert_stable(self):
        if self._invalidated:raise R5QuestTalkError('physical batch invalidated')
        try:
            for path,expected in self._bindings.items():
                if _identity(path)!=expected:raise R5QuestTalkError('physical input drift')
            if any(Path(str(self.artifact_path)+s).exists() for s in ('-wal','-shm','-journal')):raise R5QuestTalkError('physical artifact sidecar')
        except (OSError,R5QuestTalkError):
            self._invalidated=True;raise R5QuestTalkError('physical batch invalidated')

    def capability(self):
        self._assert_stable();m=self.manifest
        return {'family':self.family,'family_contract_version':self.family_contract_version,'evidence_only':True,
                'supported_operations':['list_family_evidence','get_family_evidence','search_family_text'],
                'supported_filters':{'list':['kind','native_id','source_path'],'search':['locale','native_id','source_path']},
                'container_scope':'one physical root or nested dialogue list; never crosses runs or containers',
                'pagination_contract':'scope-bound physical occurrence keyset',
                'coverage_semantics':{'coverage':self.coverage,'corpus_complete':False,'product_accepted':self.product_accepted,'candidate_search_complete':m['candidate_search_complete']},
                'eligible_runs':[{'ingest_run_id':self.ingest_run_id,'parser_build_id':m['parser_build_id'],'snapshot_id':m['snapshot_id'],'coverage':self.coverage,'corpus_complete':False,'product_accepted':self.product_accepted,'artifact':deepcopy(m['artifact']),'scope_manifest':deepcopy(m['scope_manifest']),'counts':deepcopy(m['counts'])}]}

    def _result(self,status,data):
        self._assert_stable()
        return FamilyEvidenceResult(status,self.family,self.ingest_run_id,self.family_contract_version,self.coverage,False,data)

    def _check(self,run,contract):
        self._assert_stable()
        return run==self.ingest_run_id and contract==self.family_contract_version

    def _missing(self):return self._result('not_found',{'status':'NotFound'})

    def list_evidence(self,*,ingest_run_id,family_contract_version,filters=None,cursor=None,limit=50):
        if not self._check(ingest_run_id,family_contract_version):return FamilyEvidenceResult('not_found',self.family,ingest_run_id,family_contract_version,'unavailable',False,{'status':'NotFound'})
        f=_filters(filters,{'kind','native_id','source_path'})
        if type(limit) is not int or not 1<=limit<=1000:raise ValueError('limit out of range')
        scope={'run':ingest_run_id,'operation':'list','filters':f};last=_decode(cursor,scope)
        clauses=['c.ingest_run_id=?'];params=[ingest_run_id]
        for key,column in [('kind','c.kind'),('native_id','c.native_id'),('source_path','sf.relative_path')]:
            if key in f:clauses.append(column+'=?');params.append(f[key])
        if last:clauses.append('c.id>?');params.append(last)
        rows=self.conn.execute('SELECT c.id AS occurrence_id,c.kind,c.native_id,c.shape,sr.pointer,sf.relative_path AS source_path FROM quest_talk_physical_container c JOIN source_record sr ON sr.id=c.source_record_id JOIN source_file sf ON sf.id=sr.source_file_id WHERE '+' AND '.join(clauses)+' ORDER BY c.id LIMIT ?',(*params,limit+1)).fetchall()
        items=[dict(r) for r in rows[:limit]]
        return self._result('ok',{'items':items,'next_cursor':_cursor(scope,items[-1]['occurrence_id']) if len(rows)>limit else None})

    def get_evidence(self,*,ingest_run_id,family_contract_version,occurrence_id):
        if not self._check(ingest_run_id,family_contract_version):return FamilyEvidenceResult('not_found',self.family,ingest_run_id,family_contract_version,'unavailable',False,{'status':'NotFound'})
        row=self.conn.execute('SELECT c.*,sr.pointer,sr.raw_json,sf.relative_path AS source_path,sf.sha256 AS source_sha256,sf.size_bytes AS source_size_bytes FROM quest_talk_physical_container c JOIN source_record sr ON sr.id=c.source_record_id JOIN source_file sf ON sf.id=sr.source_file_id WHERE c.id=? AND c.ingest_run_id=?',(occurrence_id,ingest_run_id)).fetchone()
        if row is None:return self._missing()
        passages=[dict(x) for x in self.conn.execute('SELECT * FROM quest_talk_physical_node WHERE container_id=? ORDER BY ordinal',(occurrence_id,))]
        texts=[dict(x) for x in self.conn.execute('SELECT * FROM quest_talk_physical_text WHERE container_id=? ORDER BY node_id,locale',(occurrence_id,))]
        refs=[dict(x) for x in self.conn.execute('SELECT * FROM quest_talk_physical_reference WHERE container_id=? ORDER BY pointer',(occurrence_id,))]
        return self._result('ok',{'container':dict(row),'passages':passages,'texts':texts,'references':refs,
                                 'candidate_search_complete':self.manifest['candidate_search_complete'],
                                 'unresolved_lookup_paths':self.manifest['unresolved_lookup_paths']})

    def search_text(self,*,ingest_run_id,family_contract_version,query,filters=None,cursor=None,limit=50):
        if not self._check(ingest_run_id,family_contract_version):return FamilyEvidenceResult('not_found',self.family,ingest_run_id,family_contract_version,'unavailable',False,{'status':'NotFound'})
        if not isinstance(query,str) or not query:raise ValueError('query required')
        if type(limit) is not int or not 1<=limit<=1000:raise ValueError('limit out of range')
        f=_filters(filters,{'locale','native_id','source_path'})
        if 'locale' in f and f['locale'] not in ('CHS','EN','JP'):raise ValueError('invalid locale')
        scope={'run':ingest_run_id,'operation':'search','filters':f,'query':query};last=_decode(cursor,scope)
        clauses=['t.ingest_run_id=?','instr(t.raw_text,?)>0'];params=[ingest_run_id,query]
        for key,col in [('locale','t.locale'),('native_id','c.native_id'),('source_path','sf.relative_path')]:
            if key in f:clauses.append(col+'=?');params.append(f[key])
        if last:clauses.append('t.id>?');params.append(last)
        rows=self.conn.execute('SELECT t.*,c.native_id,sf.relative_path AS source_path FROM quest_talk_physical_text t JOIN quest_talk_physical_container c ON c.id=t.container_id JOIN source_record sr ON sr.id=c.source_record_id JOIN source_file sf ON sf.id=sr.source_file_id WHERE '+' AND '.join(clauses)+' ORDER BY t.id LIMIT ?',(*params,limit+1)).fetchall()
        items=[dict(r) for r in rows[:limit]]
        return self._result('ok',{'items':items,'next_cursor':_cursor(scope,items[-1]['id']) if len(rows)>limit else None})
