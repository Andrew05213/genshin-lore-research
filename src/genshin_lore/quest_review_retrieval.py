"""Bounded exact-run retrieval for native Quest review evidence."""
import json,sqlite3
from pathlib import Path
from .family_evidence import FamilyEvidenceResult
from .r5_quest_talk_retrieval import _fingerprint,_identity,_cursor,_decode,_filters
ROOT=Path(__file__).resolve().parents[2];BUDGET=262144
def wire_size(result):return 3*len(json.dumps(json.dumps(result.envelope(),ensure_ascii=True),ensure_ascii=True).encode())+8192
class QuestReviewProvider:
 family='quest_review_archive';family_contract_version='quest-review-archive-evidence-v1'
 def __init__(self,artifact,manifest):
  self.path=Path(artifact).absolute();self.manifest_path=Path(manifest).resolve();self.invalidated=False
  if self.manifest_path!=ROOT/'manifests/quest-review/runtime.json':raise ValueError('explicit tracked Quest review manifest required')
  mf=_fingerprint(self.manifest_path);self.manifest=json.loads(self.manifest_path.read_text());m=self.manifest;fp=_fingerprint(self.path)
  if m['contract']!='quest-review-runtime-v1' or fp[2]!=m['artifact']['size_bytes'] or fp[4]!=m['artifact']['sha256']:raise ValueError('Quest review qualification')
  scope=ROOT/'manifests/quest-review/scope.json';sf=_fingerprint(scope)
  if sf[4]!=m['release']['scope_sha256']:raise ValueError('scope binding')
  self.bindings={self.path:fp[:4],self.manifest_path:mf[:4],scope:sf[:4]};self.run=m['release']['run_id'];self.conn=sqlite3.connect(self.path.as_uri()+'?mode=ro&immutable=1',uri=True,check_same_thread=False);self.conn.row_factory=sqlite3.Row
  if any(self.conn.execute('select count(*) from '+t).fetchone()[0]!=n for t,n in m['release']['counts'].items()):raise ValueError('count seal')
  structure=[list(x) for x in self.conn.execute("select type,name,tbl_name,sql from sqlite_master where name not like 'sqlite_%' order by type,name")]
  if structure!=m['schema_structure'] or json.loads(self.conn.execute('select manifest_json from quest_review_release').fetchone()[0])!=m['release'] or self.conn.execute('pragma foreign_key_check').fetchall():raise ValueError('structure/release/FK')
  self._stable()
 def _stable(self):
  try:
   if self.invalidated or any(_identity(p)!=v for p,v in self.bindings.items()) or any(Path(str(self.path)+s).exists() for s in ('-wal','-shm','-journal')):raise ValueError('drift')
  except Exception:self.invalidated=True;raise ValueError('Quest review run permanently invalidated')
 def capability(self):
  self._stable();r=self.manifest['release'];return {'family':self.family,'family_contract_version':self.family_contract_version,'supported_operations':['list_family_evidence','get_family_evidence','search_family_text'],'supported_filters':{'list':['record_kind','row_kind','native_id','container_id','relation_kind','target_occurrence_id','locale'],'search':['locale','row_kind','container_id','owner_field']},'pagination_contract':{'scope':'exact run + contract + record kind + filters','ordering':'physical occurrence id','max_limit':100,'payload_budget_bytes':BUDGET,'partial_responses_explicit':True},'eligible_runs':[{'ingest_run_id':self.run,'parser_build_id':r['parser_build_id'],'snapshot_id':r['snapshot_id'],'coverage':'complete QuestSummarizationText + QuestReview + QuestReviewTabGroup physical-row projection','corpus_complete':True,'product_accepted':self.manifest['product_accepted'],'counts':r['counts'],'relation_states':r['relation_states'],'text_slots':r['text_slots'],'semantic_relation_resolution':'partial_with_explicit_unresolved','candidate_search_complete':False}],'default_run':False,'latest_run':False,'quest_or_talk_body_copied':False,'search_text_scope':'family-owned review/summary text only'}
 def _result(self,data,status='ok',run=None):self._stable();return FamilyEvidenceResult(status,self.family,run or self.run,self.family_contract_version,'complete_declared_quest_review_archive_projection',True,data)
 def _check(self,run,contract):self._stable();return run==self.run and contract==self.family_contract_version
 def _missing(self,run):return self._result({'status':'NotFound'},'not_found',run)
 def _bounded(self,data,scope,rows,more,key='id'):
  if not rows:return self._result({**data,'items':[],'delivered_count':0,'next_cursor':None,'scope_complete':True,'partial':False})
  while rows:
   result=self._result({**data,'items':rows,'delivered_count':len(rows),'next_cursor':_cursor(scope,rows[-1][key]) if more else None,'scope_complete':not more,'partial':more})
   if wire_size(result)<=BUDGET:return result
   rows.pop();more=True
  raise ValueError('single physical record exceeds inline limit; use exact get; no truncation')
 def list_evidence(self,*,ingest_run_id,family_contract_version,filters=None,cursor=None,limit=50):
  if not self._check(ingest_run_id,family_contract_version):return self._missing(ingest_run_id)
  if type(limit) is not int or not 1<=limit<=100:raise ValueError('limit')
  f=_filters(filters,{'record_kind','row_kind','native_id','container_id','relation_kind','target_occurrence_id','locale'});kind=f.get('record_kind','rows');scope={'run':self.run,'contract':self.family_contract_version,'kind':kind,'filters':f};last=_decode(cursor,scope);where=[];args=[]
  config={'rows':('quest_review_row',{'record_kind','row_kind','native_id'}),'relations':('quest_review_relation',{'record_kind','container_id','relation_kind'}),'candidates':('quest_review_candidate',{'record_kind','container_id','target_occurrence_id'}),'text_slots':('quest_review_text_slot',{'record_kind','container_id','locale'})}
  if kind not in config:raise ValueError('record_kind')
  table,allowed=config[kind]
  if set(f)-allowed:raise ValueError('filter scope')
  if last is not None:where.append('id>?');args.append(last)
  if kind=='rows' and 'row_kind' in f:where.append('kind=?');args.append(f['row_kind'])
  if kind=='rows' and 'native_id' in f:where.append('native_id=?');args.append(f['native_id'])
  if kind in ('relations','text_slots') and 'container_id' in f:where.append('row_id=?');args.append(f['container_id'])
  if kind=='relations' and 'relation_kind' in f:where.append('kind=?');args.append(f['relation_kind'])
  if kind=='text_slots' and 'locale' in f:where.append('locale=?');args.append(f['locale'])
  if kind=='candidates':
   if 'container_id' in f:where.append('relation_id in(select id from quest_review_relation where row_id=?)');args.append(f['container_id'])
   if 'target_occurrence_id' in f:where.append('target_occurrence_id=?');args.append(f['target_occurrence_id'])
  rows=[dict(x) for x in self.conn.execute('select * from '+table+(' where '+' and '.join(where) if where else '')+' order by id limit ?',(*args,limit+1))];more=len(rows)>limit;return self._bounded({'record_kind':kind},scope,rows[:limit],more)
 def get_evidence(self,*,ingest_run_id,family_contract_version,occurrence_id):
  if not self._check(ingest_run_id,family_contract_version):return self._missing(ingest_run_id)
  row=self.conn.execute('select * from quest_review_row where id=?',(occurrence_id,)).fetchone()
  if row:
   data=dict(row);data['counts']={'relations':self.conn.execute('select count(*) from quest_review_relation where row_id=?',(occurrence_id,)).fetchone()[0],'text_slots':self.conn.execute('select count(*) from quest_review_text_slot where row_id=?',(occurrence_id,)).fetchone()[0]};data['bounded_record_kinds']=['relations','candidates','text_slots'];data['partial']=True;return self._result(data)
  for table in ('quest_review_relation','quest_review_text_slot'):
   row=self.conn.execute('select * from '+table+' where id=?',(occurrence_id,)).fetchone()
   if row:
    data={table:dict(row)}
    if table=='quest_review_relation':data['candidates']=[dict(x) for x in self.conn.execute('select * from quest_review_candidate where relation_id=? order by id',(occurrence_id,))]
    else:data['text_candidates']=[dict(x) for x in self.conn.execute('select * from quest_review_text_candidate where slot_id=? order by id',(occurrence_id,))]
    result=self._result(data)
    if wire_size(result)>BUDGET:raise ValueError('single physical record exceeds inline limit; no truncation')
    return result
  return self._missing(ingest_run_id)
 def search_text(self,*,ingest_run_id,family_contract_version,query,filters=None,cursor=None,limit=50):
  if not self._check(ingest_run_id,family_contract_version):return self._missing(ingest_run_id)
  if not isinstance(query,str) or not query or type(limit) is not int or not 1<=limit<=100:raise ValueError('query/limit')
  f=_filters(filters,{'locale','row_kind','container_id','owner_field'});scope={'run':self.run,'contract':self.family_contract_version,'query':query,'filters':f};last=_decode(cursor,scope);where=['instr(t.raw_text,?)>0'];args=[query]
  for key,col in [('locale','s.locale'),('row_kind','r.kind'),('container_id','r.id'),('owner_field','s.owner_field')]:
   if key in f:where.append(col+'=?');args.append(f[key])
  if last is not None:where.append('t.id>?');args.append(last)
  sql='''select t.*,s.locale,s.owner_field,r.id as container_id,r.kind as row_kind from quest_review_text_candidate t join quest_review_text_slot s on s.id=t.slot_id join quest_review_row r on r.id=s.row_id where '''+' and '.join(where)+' order by t.id limit ?';rows=[dict(x) for x in self.conn.execute(sql,(*args,limit+1))];more=len(rows)>limit
  return self._bounded({'literal_substring':True},scope,rows[:limit],more)
