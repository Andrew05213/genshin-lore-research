"""Bounded exact-run retrieval for the Quest narrative physical projection."""
import json,sqlite3
from pathlib import Path
from .family_evidence import FamilyEvidenceResult
from .r5_quest_talk_retrieval import _fingerprint,_identity,_cursor,_decode,_filters
ROOT=Path(__file__).resolve().parents[2];BUDGET=262144
def wire_size(x):return 3*len(json.dumps(json.dumps(x.envelope(),ensure_ascii=True),ensure_ascii=True).encode())+8192
class QuestNarrativeProvider:
 family='quest_narrative_archive';family_contract_version='quest-narrative-evidence-v1'
 def __init__(self,artifact,manifest):
  self.path=Path(artifact).absolute();self.manifest_path=Path(manifest).resolve();self.invalidated=False
  if self.manifest_path!=ROOT/'manifests/quest-narrative/runtime.json':raise ValueError('explicit tracked Quest narrative manifest required')
  mf=_fingerprint(self.manifest_path);m=json.loads(self.manifest_path.read_text());fp=_fingerprint(self.path);self.manifest=m
  if m['contract']!='quest-narrative-runtime-v1' or fp[2]!=m['artifact']['size_bytes'] or fp[4]!=m['artifact']['sha256']:raise ValueError('Quest narrative qualification')
  scope=ROOT/'manifests/quest-narrative/scope.json';sf=_fingerprint(scope)
  if sf[4]!=m['release']['scope_sha256']:raise ValueError('scope binding')
  self.bindings={self.path:fp[:4],self.manifest_path:mf[:4],scope:sf[:4]};self.run=m['release']['run_id'];self.conn=sqlite3.connect(self.path.as_uri()+'?mode=ro&immutable=1',uri=True,check_same_thread=False);self.conn.row_factory=sqlite3.Row
  if any(self.conn.execute('select count(*) from '+t).fetchone()[0]!=n for t,n in m['release']['counts'].items()):raise ValueError('count seal')
  structure=[list(x) for x in self.conn.execute("select type,name,tbl_name,sql from sqlite_master where name not like 'sqlite_%' order by type,name")]
  if structure!=m['schema_structure'] or json.loads(self.conn.execute('select manifest_json from quest_narrative_release').fetchone()[0])!=m['release'] or self.conn.execute('pragma foreign_key_check').fetchall():raise ValueError('structure/release/FK')
  self._stable()
 def _stable(self):
  try:
   if self.invalidated or any(_identity(p)!=v for p,v in self.bindings.items()) or any(Path(str(self.path)+s).exists() for s in ('-wal','-shm','-journal')):raise ValueError('drift')
  except Exception:self.invalidated=True;raise ValueError('Quest narrative run permanently invalidated')
 def capability(self):
  self._stable();r=self.manifest['release'];return {'family':self.family,'family_contract_version':self.family_contract_version,'supported_operations':['list_family_evidence','get_family_evidence','search_family_text'],'supported_filters':{'list':['record_kind','container_kind','container_id','relation_kind','locale','native_id'],'search':['locale','container_kind','container_id','owner_field']},'pagination_contract':{'scope':'exact run + contract + record kind + filters','ordering':'physical occurrence id','max_limit':100,'payload_budget_bytes':BUDGET,'partial_responses_explicit':True},'search_text_scope':'Quest narrative delta-owned MainQuest/QuestExcel declared text only','eligible_runs':[{'ingest_run_id':self.run,'parser_build_id':r['parser_build_id'],'snapshot_id':r['snapshot_id'],'coverage':'all 4,466 Quest roots reused plus complete declared MainQuest/QuestExcel narrative projection','corpus_complete':True,'product_accepted':self.manifest['product_accepted'],'counts':r['counts'],'narrative_semantic_resolution':r['narrative_semantic_resolution']}],'default_run':False,'latest_run':False,'quest_or_talk_body_copied':False}
 def _result(self,data,status='ok',run=None):self._stable();return FamilyEvidenceResult(status,self.family,run or self.run,self.family_contract_version,'complete_declared_quest_narrative_projection',True,data)
 def _check(self,run,contract):self._stable();return run==self.run and contract==self.family_contract_version
 def _missing(self,run):return self._result({'status':'NotFound'},'not_found',run)
 def list_evidence(self,*,ingest_run_id,family_contract_version,filters=None,cursor=None,limit=50):
  if not self._check(ingest_run_id,family_contract_version):return self._missing(ingest_run_id)
  if type(limit) is not int or not 1<=limit<=100:raise ValueError('limit')
  f=_filters(filters,{'record_kind','container_kind','container_id','relation_kind','locale','native_id'});kind=f.get('record_kind','containers');scope={'run':self.run,'contract':self.family_contract_version,'kind':kind,'filters':f};last=_decode(cursor,scope);args=[];where=[]
  config={
   'containers':('quest_narrative_container',{'record_kind','container_kind','native_id'}),
   'children':('quest_narrative_child',{'record_kind','container_id'}),
   'relations':('quest_narrative_relation',{'record_kind','container_id','relation_kind'}),
   'candidates':('quest_narrative_candidate',{'record_kind','container_id'}),
   'text_slots':('quest_narrative_text_slot',{'record_kind','container_id','locale'}),
  }
  if kind not in config:raise ValueError('record_kind')
  table,allowed=config[kind]
  if set(f)-allowed:raise ValueError('filter scope')
  if last is not None:where.append('id>?');args.append(last)
  if kind=='containers' and 'container_kind' in f:where.append('kind=?');args.append(f['container_kind'])
  if kind=='containers' and 'native_id' in f:where.append('native_id=?');args.append(f['native_id'])
  if kind in ('children','relations','text_slots') and 'container_id' in f:where.append('container_id=?');args.append(f['container_id'])
  if kind=='relations' and 'relation_kind' in f:where.append('kind=?');args.append(f['relation_kind'])
  if kind=='text_slots' and 'locale' in f:where.append('locale=?');args.append(f['locale'])
  if kind=='candidates':
   if 'container_id' not in f:raise ValueError('candidate pages require exact container_id')
   where.append('relation_id in(select id from quest_narrative_relation where container_id=?)');args.append(f['container_id'])
  rows=[dict(x) for x in self.conn.execute('select * from '+table+(' where '+' and '.join(where) if where else '')+' order by id limit ?',(*args,limit+1))];more=len(rows)>limit;rows=rows[:limit]
  if not rows:return self._result({'record_kind':kind,'items':[],'delivered_count':0,'next_cursor':None,'scope_complete':True,'partial':False})
  while rows:
   result=self._result({'record_kind':kind,'items':rows,'delivered_count':len(rows),'next_cursor':_cursor(scope,rows[-1]['id']) if more else None,'scope_complete':not more,'partial':more})
   if wire_size(result)<=BUDGET:return result
   rows.pop();more=True
  raise ValueError('single physical record exceeds inline limit; use exact get; no truncation')
 def get_evidence(self,*,ingest_run_id,family_contract_version,occurrence_id):
  if not self._check(ingest_run_id,family_contract_version):return self._missing(ingest_run_id)
  row=self.conn.execute('select * from quest_narrative_container where id=?',(occurrence_id,)).fetchone()
  if row:
   d=dict(row);d['counts']={'children':self.conn.execute('select count(*) from quest_narrative_child where container_id=?',(occurrence_id,)).fetchone()[0],'relations':self.conn.execute('select count(*) from quest_narrative_relation where container_id=?',(occurrence_id,)).fetchone()[0],'text_slots':self.conn.execute('select count(*) from quest_narrative_text_slot where container_id=?',(occurrence_id,)).fetchone()[0]};d['bounded_record_kinds']=['children','relations','candidates','text_slots'];d['partial']=True;return self._result(d)
  for table in ('quest_narrative_child','quest_narrative_relation','quest_narrative_text_slot'):
   row=self.conn.execute('select * from '+table+' where id=?',(occurrence_id,)).fetchone()
   if row:
    d={table:dict(row)}
    if table=='quest_narrative_relation':d['candidates']=[dict(x) for x in self.conn.execute('select * from quest_narrative_candidate where relation_id=? order by id',(occurrence_id,))]
    if table=='quest_narrative_text_slot':d['text_candidates']=[dict(x) for x in self.conn.execute('select * from quest_narrative_text_candidate where slot_id=? order by id',(occurrence_id,))]
    return self._result(d)
  return self._missing(ingest_run_id)
 def search_text(self,*,ingest_run_id,family_contract_version,query,filters=None,cursor=None,limit=50):
  if not self._check(ingest_run_id,family_contract_version):return self._missing(ingest_run_id)
  if not isinstance(query,str) or not query:raise ValueError('query')
  if type(limit) is not int or not 1<=limit<=100:raise ValueError('limit')
  f=_filters(filters,{'locale','container_kind','container_id','owner_field'});scope={'run':self.run,'contract':self.family_contract_version,'query':query,'filters':f};last=_decode(cursor,scope);where=['instr(t.raw_text,?)>0'];args=[query]
  for key,col in [('locale','s.locale'),('container_kind','c.kind'),('container_id','c.id'),('owner_field','s.owner_field')]:
   if key in f:where.append(col+'=?');args.append(f[key])
  if last is not None:where.append('t.id>?');args.append(last)
  q='''select t.id,t.slot_id,t.source_path,t.source_ordinal,t.source_pointer,t.raw_text,s.locale,s.owner_field,c.id as container_id,c.kind as container_kind from quest_narrative_text_candidate t join quest_narrative_text_slot s on s.id=t.slot_id join quest_narrative_container c on c.id=s.container_id where '''+' and '.join(where)+' order by t.id limit ?'
  rows=[dict(x) for x in self.conn.execute(q,(*args,limit+1))];more=len(rows)>limit;rows=rows[:limit]
  if not rows:return self._result({'items':[],'delivered_count':0,'next_cursor':None,'scope_complete':True,'partial':False,'literal_substring':True})
  while rows:
   result=self._result({'items':rows,'delivered_count':len(rows),'next_cursor':_cursor(scope,rows[-1]['id']) if more else None,'scope_complete':not more,'partial':more,'literal_substring':True})
   if wire_size(result)<=BUDGET:return result
   rows.pop();more=True
  raise ValueError('single search result exceeds inline limit; use exact get; no truncation')
