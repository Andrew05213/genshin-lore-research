"""Bounded exact-run retrieval for the physical CodexQuest archive."""
from __future__ import annotations
import hashlib,json,sqlite3
from pathlib import Path
from .family_evidence import FamilyEvidenceResult
from .r5_quest_talk_retrieval import _fingerprint,_identity,_cursor,_decode,_filters

ROOT=Path(__file__).resolve().parents[2];BUDGET=262144
def wire_size(result):return 3*len(json.dumps(json.dumps(result.envelope(),ensure_ascii=True),ensure_ascii=True).encode())+8192

class CodexQuestProvider:
 family='codexquest_archive';family_contract_version='codexquest-archive-evidence-v1'
 def __init__(self,artifact,manifest):
  self.path=Path(artifact).absolute();self.manifest_path=Path(manifest).resolve();self.invalidated=False
  if self.manifest_path!=ROOT/'manifests/codexquest/runtime.json':raise ValueError('explicit tracked CodexQuest manifest required')
  mf=_fingerprint(self.manifest_path);self.manifest=json.loads(self.manifest_path.read_text());m=self.manifest;fp=_fingerprint(self.path)
  if m['contract']!='codexquest-runtime-v1' or fp[2]!=m['artifact']['size_bytes'] or fp[4]!=m['artifact']['sha256']:raise ValueError('CodexQuest qualification')
  scope=ROOT/'manifests/codexquest/scope.json';sf=_fingerprint(scope)
  if sf[4]!=m['release']['scope_sha256']:raise ValueError('scope binding')
  self.bindings={self.path:fp[:4],self.manifest_path:mf[:4],scope:sf[:4]};self.run=m['release']['run_id'];self.conn=sqlite3.connect(self.path.as_uri()+'?mode=ro&immutable=1',uri=True,check_same_thread=False);self.conn.row_factory=sqlite3.Row
  if any(self.conn.execute('select count(*) from '+t).fetchone()[0]!=n for t,n in m['release']['counts'].items()):raise ValueError('count seal')
  structure=[list(x) for x in self.conn.execute("select type,name,tbl_name,sql from sqlite_master where name not like 'sqlite_%' order by type,name")]
  if structure!=m['schema_structure'] or json.loads(self.conn.execute('select manifest_json from codexquest_release').fetchone()[0])!=m['release'] or self.conn.execute('pragma foreign_key_check').fetchall():raise ValueError('structure/release/FK')
  self._stable()
 def _stable(self):
  try:
   if self.invalidated or any(_identity(p)!=v for p,v in self.bindings.items()) or any(Path(str(self.path)+s).exists() for s in ('-wal','-shm','-journal')):raise ValueError('drift')
  except Exception:self.invalidated=True;raise ValueError('CodexQuest run permanently invalidated')
 def capability(self):
  self._stable();r=self.manifest['release'];return {'family':self.family,'family_contract_version':self.family_contract_version,'supported_operations':['list_family_evidence','get_family_evidence','search_family_text'],'supported_filters':{'list':['record_kind','shape','native_id','quest_type','container_id','child_id','child_kind','relation_kind','target_occurrence_id','locale','owner_field'],'search':['locale','shape','native_id','container_id','owner_field']},'pagination_contract':{'scope':'exact run + contract + record kind + filters','ordering':'physical occurrence id; body chunks by byte order','max_limit':100,'payload_budget_bytes':BUDGET,'partial_responses_explicit':True,'empty_page_is_complete':True,'oversized_child':'child summary plus child_body_chunks scoped to exact child'},'relation_semantics_overlay':{'mainquest_equality_navigation':{'semantic_status':'mapping_unresolved','search_complete':False,'candidate_role':'equality navigation aid only'},'questcodex_reverse_navigation':{'client_relation_kind':'questcodex_equality_reverse_navigation','semantic_status':'mapping_unresolved','search_complete':False,'physical_candidate_scan_complete':True,'candidate_role':'reverse equality navigation aid; QuestCodex parentQuestId namespace into CodexQuest roots is not independently proven'}},'eligible_runs':[{'ingest_run_id':self.run,'parser_build_id':r['parser_build_id'],'snapshot_id':r['snapshot_id'],'coverage':'complete pinned BinOutput/CodexQuest physical archive projection with unresolved cross-source navigation semantics','corpus_complete':True,'product_accepted':self.manifest['product_accepted'],'counts':r['counts'],'shape_counts':r['shape_counts'],'artifact_recorded_relation_states':r['relation_states'],'client_relation_states':{'mapping_unresolved':r['counts']['codexquest_relation']},'text_slots':r['text_slots'],'full_questcodex_semantic_identity':False,'full_quest_gameplay':False,'full_talk':False,'full_7_0':False}],'default_run':False,'latest_run':False,'existing_family_body_copied':False,'search_text_scope':'CodexQuest-owned declared locator text only'}
 def _semantic_relation(self,row):
  value=dict(row)
  if value['kind']=='questcodex_reverse_navigation':
   value.update({'client_relation_kind':'questcodex_equality_reverse_navigation','artifact_recorded_status':value['status'],'artifact_recorded_search_complete':bool(value['search_complete']),'status':'mapping_unresolved','search_complete':False,'physical_candidate_scan_complete':True,'candidate_cardinality':'zero' if value['candidate_count']==0 else 'one' if value['candidate_count']==1 else 'multiple','relation_semantics':'reverse equality navigation aid over a completely scanned physical candidate set; semantic target namespace identity is not independently proven'})
  elif value['kind']=='mainquest_equality_navigation':
   value.update({'client_relation_kind':value['kind'],'status':'mapping_unresolved','search_complete':False,'physical_candidate_scan_complete':True,'candidate_cardinality':'zero' if value['candidate_count']==0 else 'one' if value['candidate_count']==1 else 'multiple'})
  return value
 def _result(self,data,status='ok',run=None):self._stable();return FamilyEvidenceResult(status,self.family,run or self.run,self.family_contract_version,'complete_declared_codexquest_physical_archive_projection',True,data)
 def _check(self,run,contract):self._stable();return run==self.run and contract==self.family_contract_version
 def _missing(self,run):return self._result({'status':'NotFound'},'not_found',run)
 def _bounded(self,data,scope,rows,more,key='id'):
  if not rows:return self._result({**data,'items':[],'delivered_count':0,'next_cursor':None,'scope_complete':True,'partial':False})
  while rows:
   result=self._result({**data,'items':rows,'delivered_count':len(rows),'next_cursor':_cursor(scope,rows[-1][key]) if more else None,'scope_complete':not more,'partial':more})
   if wire_size(result)<=BUDGET:return result
   rows.pop();more=True
  raise ValueError('single physical record exceeds inline limit; use body_chunks or a finer child kind; no truncation')
 def list_evidence(self,*,ingest_run_id,family_contract_version,filters=None,cursor=None,limit=50):
  if not self._check(ingest_run_id,family_contract_version):return self._missing(ingest_run_id)
  if type(limit) is not int or not 1<=limit<=100:raise ValueError('limit')
  allowed={'record_kind','shape','native_id','quest_type','container_id','child_id','child_kind','relation_kind','target_occurrence_id','locale','owner_field'};f=_filters(filters,allowed);kind=f.get('record_kind','containers');scope={'run':self.run,'contract':self.family_contract_version,'kind':kind,'filters':f};last=_decode(cursor,scope);where=[];args=[]
  if kind=='child_body_chunks':
   if set(f)!={'record_kind','child_id'}:raise ValueError('child_body_chunks requires exact child_id')
   row=self.conn.execute('select id,container_id,raw_json,raw_sha256 from codexquest_child where id=?',(f['child_id'],)).fetchone()
   if row is None:return self._result({'record_kind':kind,'items':[],'delivered_count':0,'next_cursor':None,'scope_complete':True,'partial':False})
   raw=row['raw_json'].encode();chunks=[]
   for ordinal,start in enumerate(range(0,len(raw),49152)):
    part=raw[start:start+49152];chunks.append({'id':row['id']+f':body:{ordinal:06d}','child_id':row['id'],'container_id':row['container_id'],'chunk_ordinal':ordinal,'byte_start':start,'byte_end':start+len(part),'raw_text':part.decode(),'raw_sha256':hashlib.sha256(part).hexdigest(),'child_raw_sha256':row['raw_sha256']})
   if last is not None:chunks=[x for x in chunks if x['id']>last]
   more=len(chunks)>limit;return self._bounded({'record_kind':kind},scope,chunks[:limit],more)
  config={'containers':('codexquest_container',{'record_kind','shape','native_id','quest_type'}),'body_chunks':('codexquest_body_chunk',{'record_kind','container_id'}),'children':('codexquest_child',{'record_kind','container_id','child_kind'}),'relations':('codexquest_relation',{'record_kind','container_id','relation_kind'}),'candidates':('codexquest_candidate',{'record_kind','container_id','target_occurrence_id'}),'text_slots':('codexquest_text_slot',{'record_kind','container_id','locale','owner_field'})}
  if kind not in config:raise ValueError('record_kind')
  table,applicable=config[kind]
  if set(f)-applicable:raise ValueError('filter scope')
  if last is not None:where.append('id>?');args.append(last)
  for key,col in [('shape','shape'),('native_id','native_id'),('quest_type','quest_type')]:
   if key in f:where.append(col+'=?');args.append(f[key])
  if kind in ('body_chunks','children','relations','text_slots') and 'container_id' in f:where.append('container_id=?');args.append(f['container_id'])
  if kind=='children' and 'child_kind' in f:where.append('kind=?');args.append(f['child_kind'])
  if kind=='relations' and 'relation_kind' in f:where.append('kind=?');args.append(f['relation_kind'])
  if kind=='text_slots':
   for key in ('locale','owner_field'):
    if key in f:where.append(key+'=?');args.append(f[key])
  if kind=='candidates':
   if 'container_id' in f:where.append('relation_id in(select id from codexquest_relation where container_id=?)');args.append(f['container_id'])
   if 'target_occurrence_id' in f:where.append('target_occurrence_id=?');args.append(f['target_occurrence_id'])
  columns='id,container_id,kind,source_pointer,ordinal,raw_sha256,length(cast(raw_json as blob)) as raw_size_bytes' if kind=='children' else '*'
  rows=[dict(x) for x in self.conn.execute('select '+columns+' from '+table+(' where '+' and '.join(where) if where else '')+' order by id limit ?',(*args,limit+1))]
  if kind=='relations':rows=[self._semantic_relation(x) for x in rows]
  more=len(rows)>limit;return self._bounded({'record_kind':kind},scope,rows[:limit],more)
 def get_evidence(self,*,ingest_run_id,family_contract_version,occurrence_id):
  if not self._check(ingest_run_id,family_contract_version):return self._missing(ingest_run_id)
  row=self.conn.execute('select * from codexquest_container where id=?',(occurrence_id,)).fetchone()
  if row:
   counts={name:self.conn.execute('select count(*) from '+table+' where '+parent+'=?',(occurrence_id,)).fetchone()[0] for name,table,parent in [('body_chunks','codexquest_body_chunk','container_id'),('children','codexquest_child','container_id'),('relations','codexquest_relation','container_id'),('text_slots','codexquest_text_slot','container_id')]};return self._result({'container':dict(row),'counts':counts,'bounded_record_kinds':['body_chunks','children','relations','candidates','text_slots'],'partial':True,'body_complete':False})
  for table in ('codexquest_body_chunk','codexquest_child','codexquest_relation','codexquest_text_slot'):
   row=self.conn.execute('select * from '+table+' where id=?',(occurrence_id,)).fetchone()
   if row:
    data={table:self._semantic_relation(row) if table=='codexquest_relation' else dict(row)}
    if table=='codexquest_relation':data['candidates']=[dict(x) for x in self.conn.execute('select * from codexquest_candidate where relation_id=? order by id',(occurrence_id,))]
    if table=='codexquest_text_slot':data['text_candidates']=[dict(x) for x in self.conn.execute('select c.*,t.* from codexquest_text_candidate c join codexquest_text_occurrence t on t.id=c.text_occurrence_id where c.slot_id=? order by c.id',(occurrence_id,))]
    result=self._result(data)
    if wire_size(result)>BUDGET:
     if table!='codexquest_child':raise ValueError('single physical record exceeds inline limit; use body_chunks or a finer child kind; no truncation')
     child=dict(row);raw=child.pop('raw_json');data={table:child,'raw_size_bytes':len(raw.encode()),'partial':True,'body_complete':False,'bounded_record_kind':'child_body_chunks'};result=self._result(data)
    return result
  return self._missing(ingest_run_id)
 def search_text(self,*,ingest_run_id,family_contract_version,query,filters=None,cursor=None,limit=50):
  if not self._check(ingest_run_id,family_contract_version):return self._missing(ingest_run_id)
  if not isinstance(query,str) or not query or type(limit) is not int or not 1<=limit<=100:raise ValueError('query/limit')
  f=_filters(filters,{'locale','shape','native_id','container_id','owner_field'});scope={'run':self.run,'contract':self.family_contract_version,'query':query,'filters':f};last=_decode(cursor,scope);where=['instr(t.raw_text,?)>0'];args=[query]
  for key,col in [('locale','t.locale'),('shape','r.shape'),('native_id','r.native_id'),('container_id','r.id'),('owner_field','s.owner_field')]:
   if key in f:where.append(col+'=?');args.append(f[key])
  if last is not None:where.append('c.id>?');args.append(last)
  sql='''with matching_ids as materialized (select id from codexquest_text_occurrence where instr(raw_text,?)>0) select c.id as id,c.slot_id,c.text_occurrence_id,t.source_path,t.source_blob_oid,t.source_ordinal,t.source_pointer,t.locale,t.shard_kind,t.raw_text,t.raw_text_sha256,s.container_id,s.owner_field,s.raw_locator,r.shape,r.native_id from matching_ids m cross join codexquest_text_candidate c indexed by cq_text_candidate_occurrence on c.text_occurrence_id=m.id cross join codexquest_text_occurrence t on t.id=m.id cross join codexquest_text_slot s on s.id=c.slot_id cross join codexquest_container r on r.id=s.container_id where '''+' and '.join(where[1:] or ['1'])+' order by c.id limit ?';rows=[dict(x) for x in self.conn.execute(sql,(*args,limit+1))];more=len(rows)>limit;return self._bounded({'literal_substring':True},scope,rows[:limit],more)

__all__=['CodexQuestProvider','BUDGET','wire_size']
