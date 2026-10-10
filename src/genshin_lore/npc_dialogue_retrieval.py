"""Explicit NPCDialogue standalone family on the existing three routed verbs."""
import json,sqlite3
from pathlib import Path
from .family_evidence import FamilyEvidenceResult
from .r5_quest_talk_retrieval import _fingerprint,_identity,_cursor,_decode,_filters,R5QuestTalkError
ROOT=Path(__file__).resolve().parents[2]
class NPCDialogueProvider:
 family='npc_dialogue_archive'
 family_contract_version='npc-dialogue-evidence-v1'
 def __init__(self,artifact,manifest):
  self.path=Path(artifact).absolute();self.manifest_path=Path(manifest).resolve();self.invalidated=False
  if self.manifest_path!=ROOT/'manifests/npc-dialogue/runtime.json':raise ValueError('explicit tracked NPCDialogue manifest required')
  mf=_fingerprint(self.manifest_path);self.manifest=json.loads(self.manifest_path.read_text());m=self.manifest;fp=_fingerprint(self.path)
  if m['contract']!='npc-dialogue-runtime-v1' or fp[4]!=m['artifact']['sha256'] or fp[2]!=m['artifact']['size_bytes']:raise ValueError('NPCDialogue artifact qualification')
  self.scope=ROOT/'manifests/npc-dialogue/scope.json';sf=_fingerprint(self.scope)
  if sf[4]!=m['release']['scope_sha256']:raise ValueError('NPCDialogue scope binding')
  self.bindings={self.path:fp[:4],self.manifest_path:mf[:4],self.scope:sf[:4]};self.run=m['release']['run_id'];self.conn=sqlite3.connect(self.path.as_uri()+'?mode=ro&immutable=1',uri=True,check_same_thread=False);self.conn.row_factory=sqlite3.Row
  if set(m['counts'])!={'npc_dialogue_npc','npc_dialogue_source','npc_dialogue_container','npc_dialogue_relation','npc_dialogue_candidate'} or self.conn.execute('SELECT count(*) FROM npc_dialogue_release').fetchone()[0]!=1:raise ValueError('NPCDialogue exact release/count keys')
  structure=[list(r) for r in self.conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
  if structure!=m['schema_structure']:raise ValueError('NPCDialogue schema/write-once seal')
  if json.loads(self.conn.execute('SELECT manifest_json FROM npc_dialogue_release').fetchone()[0])!=m['release']:raise ValueError('NPCDialogue run seal')
  if self.conn.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('NPCDialogue FK')
  for t,n in m['counts'].items():
   if self.conn.execute('SELECT count(*) FROM '+t).fetchone()[0]!=n:raise ValueError('NPCDialogue count seal')
  self._stable()
 def _stable(self):
  try:
   if self.invalidated or any(_identity(p)!=fp for p,fp in self.bindings.items()) or any(Path(str(self.path)+s).exists() for s in ('-wal','-shm','-journal')):raise ValueError('NPCDialogue permanently invalidated')
  except (OSError,ValueError):self.invalidated=True;raise ValueError('NPCDialogue permanently invalidated')
 def capability(self):
  self._stable();return {'family':self.family,'family_contract_version':self.family_contract_version,'supported_operations':['list_family_evidence','get_family_evidence'],'supported_filters':{'list':['native_id','npc_container_id','record_kind','relation_kind']},'evidence_available':True,'evidence_only':True,'container_scope':'one reused physical NPC row; reverse observations paged explicitly','pagination_contract':'run/contract/NPC/kind/filter bound keyset; 256KiB conservative wire budget','eligible_runs':[{'ingest_run_id':self.run,'parser_build_id':self.manifest['release']['parser_build_id'],'snapshot_id':self.manifest['release']['snapshot_id'],'counts':self.manifest['counts'],'relation_states':self.manifest['release']['relation_states'],'reused_npc_core':self.manifest['release']['core'],'corpus_complete':True,'product_accepted':self.manifest['product_accepted']}],'coverage_semantics':{'scope':'complete frozen reverse NPC-locator projection, not all NPC dialogue or full7.0','forward_relation_claim':False,'no_new_text':True,'search':'use exact existing npc_archive v2 text contract'}}
 def _result(self,data,status='ok',run=None):
  self._stable();r=FamilyEvidenceResult(status,self.family,run or self.run,self.family_contract_version,'declared_reverse_npc_locator_projection',True,data)
  # Stock SDK carries both structured and escaped text representations.
  if 3*len(json.dumps(json.dumps(r.envelope(),ensure_ascii=True),ensure_ascii=True).encode())+8192>262144:raise ValueError('response_exceeds_inline_limit; lower limit; no truncation')
  return r
 def _check(self,run,contract):self._stable();return run==self.run and contract==self.family_contract_version
 def _item(self,r):
  x=dict(r)
  if x.get('runtime_run'):
   pointer=x['pointer'].rsplit('/',1)[0];from .npc_dialogue_source import digest
   node='qt-node:'+digest((x['runtime_run'],x['source_path'],pointer))[:40]
   x['talk_get']={'family':'quest_talk_pilot','family_contract_version':'quest-talk-evidence-v7','ingest_run_id':x['runtime_run'],'occurrence_id':json.dumps(['child','nodes',x['runtime_id'],node],separators=(',',':'))}
  return x
 def list_evidence(self,*,ingest_run_id,family_contract_version,filters=None,cursor=None,limit=50):
  if not self._check(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  f=_filters(filters,{'native_id','npc_container_id','record_kind','relation_kind'})
  if type(limit)is not int or not 1<=limit<=100:raise ValueError('limit')
  scope={'run':self.run,'contract':self.family_contract_version,'operation':'list','filters':f};last=_decode(cursor,scope);args=[]
  if f.get('record_kind','npc')=='npc':
   if set(f)-{'record_kind','native_id'}:raise ValueError('NPC filters')
   sql='SELECT id,ordinal,native_id FROM npc_dialogue_npc WHERE 1=1'
   if 'native_id'in f:sql+=' AND native_id=?';args.append(f['native_id'])
   if last:sql+=' AND id>?';args.append(last)
   rows=[dict(x) for x in self.conn.execute(sql+' ORDER BY id LIMIT ?',(*args,limit+1))]
  elif f['record_kind']=='relations':
   if 'native_id'in f:raise ValueError('use exact NPC container')
   sql='SELECT r.*,p.source_path,p.pointer AS container_pointer,p.runtime_run,p.runtime_id,s.sha256 AS source_sha256,s.blob AS source_blob FROM npc_dialogue_relation r JOIN npc_dialogue_container p ON p.id=r.container_id JOIN npc_dialogue_source s ON s.path=p.source_path WHERE 1=1'
   if 'npc_container_id'in f:sql+=' AND r.id IN (SELECT relation_id FROM npc_dialogue_candidate WHERE npc_id=?)';args.append(f['npc_container_id'])
   if 'relation_kind'in f:
    if f['relation_kind'] not in ('talk_node_npc_role','talk_config_npc_reference','talk_node_role_namespace_unresolved'):raise ValueError('relation kind')
    sql+=' AND r.kind=?';args.append(f['relation_kind'])
   if last:sql+=' AND r.id>?';args.append(last)
   rows=[self._item(x) for x in self.conn.execute(sql+' ORDER BY r.id LIMIT ?',(*args,limit+1))]
  else:raise ValueError('record kind')
  if f.get('record_kind')=='relations' and rows:
   ids=[x['id'] for x in rows[:limit]];by={id:[] for id in ids}
   for x in self.conn.execute('SELECT c.relation_id,n.* FROM npc_dialogue_candidate c JOIN npc_dialogue_npc n ON n.id=c.npc_id WHERE c.relation_id IN ('+','.join('?' for _ in ids)+') ORDER BY c.id',ids):
    value=dict(x);by[value.pop('relation_id')].append(value)
   for row in rows[:limit]:row['npc_candidates']=by[row['id']];row['candidate_search_complete']=row['status']!='mapping_unresolved'
  delivered=rows[:limit]
  while True:
   data={'items':delivered,'bounded':True,'record_kind':f.get('record_kind','npc'),'npc_container_id':f.get('npc_container_id'),'delivered_count':len(delivered),'next_cursor':_cursor(scope,delivered[-1]['id']) if len(rows)>len(delivered) and delivered else None}
   try:return self._result(data)
   except ValueError as e:
    if not str(e).startswith('response_exceeds_inline_limit') or len(delivered)<=1:raise
    delivered=delivered[:max(1,len(delivered)//2)]
 def get_evidence(self,*,ingest_run_id,family_contract_version,occurrence_id):
  if not self._check(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  npc=self.conn.execute('SELECT * FROM npc_dialogue_npc WHERE id=?',(occurrence_id,)).fetchone()
  if npc is None:return self._result({'status':'NotFound'},'not_found')
  counts=dict(self.conn.execute('SELECT r.kind,count(*) FROM npc_dialogue_candidate x JOIN npc_dialogue_relation r ON r.id=x.relation_id WHERE x.npc_id=? GROUP BY r.kind',(occurrence_id,)).fetchall());core=self.manifest['release']['core'];return self._result({'npc_coordinate':dict(npc),'core_get':{k:core[k] for k in ('family','family_contract_version','ingest_run_id')}|{'occurrence_id':occurrence_id},'relation_counts':counts,'relation_page_filters':{'record_kind':'relations','npc_container_id':occurrence_id},'direction':'reverse; source-authored Talk references to NPC, not NPC-authored forward relations','body_included':False,'complete_summary':True})
 def search_text(self,**kwargs):return self._result({'status':'NotFound','reason':'operation_not_supported; use existing npc_archive v2 title/name text'},'not_found',kwargs.get('ingest_run_id'))
