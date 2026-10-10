"""Explicit LoadingTips standalone family on the existing three routed verbs."""
import json,sqlite3
from pathlib import Path
from .family_evidence import FamilyEvidenceResult
from .r5_quest_talk_retrieval import _fingerprint,_identity,_cursor,_decode,_filters,R5QuestTalkError
ROOT=Path(__file__).resolve().parents[2]
class LoadingTipsProvider:
 family='loading_tips_archive'
 family_contract_version='loading-tips-evidence-v1'
 def __init__(self,artifact,manifest):
  self.path=Path(artifact).absolute();self.manifest_path=Path(manifest).resolve();self.invalidated=False
  if self.manifest_path!=ROOT/'manifests/loading-tips/runtime.json':raise ValueError('explicit tracked LoadingTips manifest required')
  mf=_fingerprint(self.manifest_path);self.manifest=json.loads(self.manifest_path.read_text());m=self.manifest;fp=_fingerprint(self.path)
  if m['contract']!='loading-tips-runtime-v1' or fp[4]!=m['artifact']['sha256'] or fp[2]!=m['artifact']['size_bytes']:raise ValueError('LoadingTips artifact qualification')
  self.scope=ROOT/'manifests/loading-tips/scope.json';sf=_fingerprint(self.scope)
  if sf[4]!=m['release']['scope_sha256']:raise ValueError('LoadingTips scope binding')
  self.bindings={self.path:fp[:4],self.manifest_path:mf[:4],self.scope:sf[:4]};self.run=m['release']['run_id'];self.conn=sqlite3.connect(self.path.as_uri()+'?mode=ro&immutable=1',uri=True,check_same_thread=False);self.conn.row_factory=sqlite3.Row
  if set(m['counts'])!={'loading_tip_row','loading_tip_slot','loading_tip_text'} or self.conn.execute('SELECT count(*) FROM loading_tip_release').fetchone()[0]!=1:raise ValueError('LoadingTips exact release/count keys')
  structure=[list(r) for r in self.conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
  if structure!=m['schema_structure']:raise ValueError('LoadingTips schema/write-once seal')
  if json.loads(self.conn.execute('SELECT manifest_json FROM loading_tip_release').fetchone()[0])!=m['release']:raise ValueError('LoadingTips run seal')
  if self.conn.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('LoadingTips FK')
  for t,n in m['counts'].items():
   if self.conn.execute('SELECT count(*) FROM '+t).fetchone()[0]!=n:raise ValueError('LoadingTips count seal')
  self._stable()
 def _stable(self):
  try:
   if self.invalidated or any(_identity(p)!=fp for p,fp in self.bindings.items()) or any(Path(str(self.path)+s).exists() for s in ('-wal','-shm','-journal')):raise ValueError('LoadingTips permanently invalidated')
  except (OSError,ValueError):self.invalidated=True;raise ValueError('LoadingTips permanently invalidated')
 def capability(self):
  self._stable();return {'family':self.family,'family_contract_version':self.family_contract_version,'evidence_only':True,'evidence_available':True,'supported_operations':['list_family_evidence','get_family_evidence','search_family_text'],'supported_filters':{'list':['native_id'],'search':['locale']},'container_scope':'one physical LoadingTips source row','pagination_contract':'exact run/operation/filter/query bound occurrence keyset','coverage_semantics':{'coverage':'full_loading_tips_two_text_field_boundary','full_7_0':False},'eligible_runs':[{'ingest_run_id':self.run,'parser_build_id':self.manifest['release']['parser_build_id'],'snapshot_id':self.manifest['release']['snapshot_id'],'counts':self.manifest['counts'],'status_counts':self.manifest['release']['status_counts'],'artifact':self.manifest['artifact'],'corpus_complete':True,'product_accepted':self.manifest['product_accepted']}]}
 def _result(self,data,status='ok',run=None):
  self._stable();return FamilyEvidenceResult(status,self.family,run or self.run,self.family_contract_version,'full_loading_tips_two_text_field_boundary',True,data)
 def _check(self,run,contract):self._stable();return run==self.run and contract==self.family_contract_version
 def list_evidence(self,*,ingest_run_id,family_contract_version,filters=None,cursor=None,limit=50):
  if not self._check(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  f=_filters(filters,{'native_id'});scope={'run':self.run,'contract':self.family_contract_version,'operation':'list','filters':f};last=_decode(cursor,scope)
  if type(limit) is not int or not 1<=limit<=100:raise ValueError('limit')
  sql='SELECT id AS occurrence_id,ordinal,native_id,source_path,pointer FROM loading_tip_row WHERE run_id=?';args=[self.run]
  if 'native_id' in f:sql+=' AND native_id=?';args.append(f['native_id'])
  if last:sql+=' AND id>?';args.append(last)
  rows=[dict(r) for r in self.conn.execute(sql+' ORDER BY id LIMIT ?',(*args,limit+1))];return self._result({'items':rows[:limit],'next_cursor':_cursor(scope,rows[limit-1]['occurrence_id']) if len(rows)>limit else None})
 def get_evidence(self,*,ingest_run_id,family_contract_version,occurrence_id):
  if not self._check(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  row=self.conn.execute('SELECT * FROM loading_tip_row WHERE id=? AND run_id=?',(occurrence_id,self.run)).fetchone()
  if row is None:return self._result({'status':'NotFound'},'not_found')
  slots=[dict(r) for r in self.conn.execute('SELECT * FROM loading_tip_slot WHERE row_id=? ORDER BY field,locale',(occurrence_id,))];texts=[dict(r) for r in self.conn.execute('SELECT t.* FROM loading_tip_text t JOIN loading_tip_slot s ON s.id=t.slot_id WHERE s.row_id=? ORDER BY t.id',(occurrence_id,))]
  return self._result({'container':dict(row),'slots':slots,'texts':texts,'provenance':self.manifest['release'],'source_files':self.manifest['source_files'],'complete':True,'opaque_fields':'all non-title/description fields preserved in raw_json, not interpreted'})
 def search_text(self,*,ingest_run_id,family_contract_version,query,filters=None,cursor=None,limit=50):
  if not self._check(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  f=_filters(filters,{'locale'})
  if 'locale' in f and f['locale'] not in ('CHS','EN','JP'):raise ValueError('locale')
  if not isinstance(query,str) or not query or type(limit) is not int or not 1<=limit<=100:raise ValueError('query/limit')
  scope={'run':self.run,'contract':self.family_contract_version,'operation':'search','filters':f,'query':query};last=_decode(cursor,scope)
  sql='SELECT t.*,s.row_id AS container_id,s.locale,s.field FROM loading_tip_text t JOIN loading_tip_slot s ON s.id=t.slot_id JOIN loading_tip_row r ON r.id=s.row_id WHERE r.run_id=? AND instr(t.raw_text,?)>0';args=[self.run,query]
  if 'locale' in f:sql+=' AND s.locale=?';args.append(f['locale'])
  if last:sql+=' AND t.id>?';args.append(last)
  rows=[dict(r) for r in self.conn.execute(sql+' ORDER BY t.id LIMIT ?',(*args,limit+1))];return self._result({'items':rows[:limit],'next_cursor':_cursor(scope,rows[limit-1]['id']) if len(rows)>limit else None})
