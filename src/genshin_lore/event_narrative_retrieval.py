"""Exact-run bounded provider for event-native narrative physical rows."""
import base64,hashlib,json,sqlite3
from copy import deepcopy
from pathlib import Path
from .family_evidence import FamilyEvidenceResult
ROOT=Path(__file__).resolve().parents[2];BUDGET=262144
def _sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for block in iter(lambda:f.read(4*1024**2),b''):h.update(block)
 return h.hexdigest()
def _fp(path):
 p=Path(path);s=p.stat();return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,_sha(p))
def _identity(path):
 s=Path(path).stat();return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)
def _token(scope,last):return base64.urlsafe_b64encode(json.dumps({'scope':scope,'last':last},sort_keys=True,separators=(',',':')).encode()).decode()
def _decode(value,scope):
 if value is None:return None
 try:data=json.loads(base64.urlsafe_b64decode(value.encode()))
 except Exception as exc:raise ValueError('invalid cursor') from exc
 if data.get('scope')!=scope or not isinstance(data.get('last'),str):raise ValueError('cursor scope mismatch')
 return data['last']
def _filters(value,allowed):
 if value is None:return {}
 if not isinstance(value,dict) or any(k not in allowed or not isinstance(v,str) or not v for k,v in value.items()):raise ValueError('filters')
 return dict(sorted(value.items()))
def _wire_size(result):
 payload=json.dumps(json.dumps(result.envelope(),ensure_ascii=True,separators=(',',':')),ensure_ascii=True,separators=(',',':')).encode()
 return 3*len(payload)+8192
class EventNarrativeProvider:
 family='event_narrative_archive';family_contract_version='event-narrative-evidence-v1'
 def __init__(self,path,manifest_path):
  self.path=Path(path).resolve();self.manifest_path=Path(manifest_path).resolve();self.invalidated=False
  catalog=json.loads((ROOT/'manifests/event-narrative/runtime-catalog.json').read_text());entries={str((ROOT/x['runtime_manifest']).resolve()):x for x in catalog['entries']};entry=entries.get(str(self.manifest_path))
  if entry is None:raise ValueError('explicit tracked event manifest required')
  mf=_fp(self.manifest_path);self.manifest=json.loads(self.manifest_path.read_text());af=_fp(self.path);m=self.manifest
  contracts={'event-narrative-evidence-v1':'event-narrative-runtime-v1','event-narrative-evidence-v2':'event-narrative-runtime-v2','event-narrative-evidence-v3':'event-narrative-runtime-v3'}
  self.family_contract_version=m.get('family_contract_version');expected_runtime=contracts.get(self.family_contract_version)
  if m.get('family')!=self.family or expected_runtime is None or m.get('contract')!=expected_runtime or af[4]!=m['artifact']['sha256'] or af[2]!=m['artifact']['size_bytes']:raise ValueError('event artifact qualification')
  if entry['family_contract_version']!=self.family_contract_version:raise ValueError('event catalog contract binding')
  self.coverage_label=entry['coverage'];self.scope=(ROOT/entry['scope_manifest']).resolve();sf=_fp(self.scope)
  if sf[4]!=m['release']['scope_sha256']:raise ValueError('event scope binding')
  self.bindings={self.path:af[:4],self.manifest_path:mf[:4],self.scope:sf[:4]};self.run=m['release']['run_id'];self.conn=sqlite3.connect(self.path.as_uri()+'?mode=ro&immutable=1',uri=True,check_same_thread=False);self.conn.row_factory=sqlite3.Row
  structure=[list(x) for x in self.conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
  if structure!=m['schema_structure'] or json.loads(self.conn.execute('SELECT manifest_json FROM event_narrative_release WHERE id=1').fetchone()[0])!=m['release'] or self.conn.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('event release qualification')
  for table,count in m['counts'].items():
   if self.conn.execute('SELECT count(*) FROM '+table).fetchone()[0]!=count:raise ValueError('event count seal')
  if any(Path(str(self.path)+suffix).exists() for suffix in ('-wal','-shm','-journal')):raise ValueError('event sidecar')
 def _stable(self):
  try:
   if self.invalidated or any(_identity(path)!=value for path,value in self.bindings.items()) or any(Path(str(self.path)+suffix).exists() for suffix in ('-wal','-shm','-journal')):raise ValueError
  except (OSError,ValueError):self.invalidated=True;raise ValueError('event provider permanently invalidated')
 def capability(self):
  self._stable();return {'family':self.family,'family_contract_version':self.family_contract_version,'evidence_only':True,'evidence_available':True,'supported_operations':['list_family_evidence','get_family_evidence','search_family_text'],'supported_filters':{'list':['boundary','native_id'],'search':['boundary','locale','owner_field']},'pagination_contract':{'kind':'scope-bound keyset','full_response_budget_bytes':BUDGET,'legal_empty_page':True,'default_run':None,'latest_run':None,'sibling_fallback':False},'container_scope':'one physical event narrative source row','coverage_semantics':{'coverage':self.coverage_label,'relations_declared':self.manifest['release'].get('relation_occurrences',0),'full_activity':False,'full_7_0':False},'eligible_runs':[{'ingest_run_id':self.run,'parser_build_id':self.manifest['release']['parser_build_id'],'snapshot_id':self.manifest['release']['snapshot_id'],'coverage':self.coverage_label,'counts':self.manifest['counts'],'text_state_counts':self.manifest['release']['text_state_counts'],'artifact':self.manifest['artifact'],'corpus_complete':True,'product_accepted':self.manifest['product_accepted']}],'default_run':False,'latest_run':False}
 def _result(self,data,status='ok',run=None):self._stable();return FamilyEvidenceResult(status,self.family,run or self.run,self.family_contract_version,self.coverage_label,True,data)
 def _bounded(self,rows,scope,limit):
  out=[]
  for row in rows[:limit]:
   candidate=out+[row];more=len(rows)>len(candidate);data={'items':candidate,'delivered_count':len(candidate),'next_cursor':_token(scope,candidate[-1]['id']) if more else None,'scope_complete':not more,'partial':more}
   if _wire_size(self._result(data))>BUDGET:
    if not out:raise ValueError('single record exceeds inline limit')
    break
   out=candidate
  more=len(rows)>len(out);data={'items':out,'delivered_count':len(out),'next_cursor':_token(scope,out[-1]['id']) if more and out else None,'scope_complete':not more,'partial':more}
  result=self._result(data)
  if _wire_size(result)>BUDGET:raise ValueError('bounded response exceeds inline limit')
  return result
 def _check(self,run,contract):self._stable();return run==self.run and contract==self.family_contract_version
 def list_evidence(self,*,ingest_run_id,family_contract_version,filters=None,cursor=None,limit=50):
  if not self._check(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  if type(limit) is not int or not 1<=limit<=100:raise ValueError('limit')
  filters=_filters(filters,{'boundary','native_id'});scope={'run':self.run,'contract':self.family_contract_version,'operation':'list','filters':filters};last=_decode(cursor,scope);sql='SELECT id,boundary,source_path,ordinal,pointer,native_id_field,native_id FROM event_narrative_row WHERE run_id=?';args=[self.run]
  for key in ('boundary','native_id'):
   if key in filters:sql+=' AND '+key+'=?';args.append(filters[key])
  if last:sql+=' AND id>?';args.append(last)
  rows=[dict(x) for x in self.conn.execute(sql+' ORDER BY id LIMIT ?',(*args,limit+1))];return self._bounded(rows,scope,limit)
 def get_evidence(self,*,ingest_run_id,family_contract_version,occurrence_id):
  if not self._check(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  row=self.conn.execute('SELECT * FROM event_narrative_row WHERE id=? AND run_id=?',(occurrence_id,self.run)).fetchone()
  if row is None:return self._result({'status':'NotFound'},'not_found')
  slots=[dict(x) for x in self.conn.execute('SELECT * FROM event_narrative_slot WHERE row_id=? ORDER BY owner_field,locale',(occurrence_id,))];texts=[dict(x) for x in self.conn.execute('SELECT t.* FROM event_narrative_text t JOIN event_narrative_slot s ON s.id=t.slot_id WHERE s.row_id=? ORDER BY t.id',(occurrence_id,))]
  relations=[]
  if 'event_narrative_relation' in self.manifest['counts']:
   for relation in self.conn.execute('SELECT * FROM event_narrative_relation WHERE row_id=? ORDER BY id',(occurrence_id,)):
    item=dict(relation);item['candidates']=[dict(x) for x in self.conn.execute('SELECT * FROM event_narrative_relation_candidate WHERE relation_id=? ORDER BY id',(relation['id'],))];relations.append(item)
  data={'container':dict(row),'text_slots':slots,'texts':texts,'relations':relations,'complete':True,'target_bodies_embedded':False}
  result=self._result(data)
  if _wire_size(result)>BUDGET:raise ValueError('single event container exceeds inline limit')
  return result
 def search_text(self,*,ingest_run_id,family_contract_version,query,filters=None,cursor=None,limit=50):
  if not self._check(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  if not isinstance(query,str) or not query or type(limit) is not int or not 1<=limit<=100:raise ValueError('query/limit')
  filters=_filters(filters,{'boundary','locale','owner_field'});scope={'run':self.run,'contract':self.family_contract_version,'operation':'search','filters':filters,'query':query};last=_decode(cursor,scope);sql='SELECT t.id,t.slot_id,t.source_path,t.pair_ordinal,t.pointer,t.raw_text,t.raw_text_sha256,s.row_id AS container_id,s.locale,s.owner_field,r.boundary FROM event_narrative_text t JOIN event_narrative_slot s ON s.id=t.slot_id JOIN event_narrative_row r ON r.id=s.row_id WHERE r.run_id=? AND instr(t.raw_text,?)>0';args=[self.run,query]
  for key in ('boundary','locale','owner_field'):
   if key in filters:sql+=' AND '+('r.' if key=='boundary' else 's.')+key+'=?';args.append(filters[key])
  if last:sql+=' AND t.id>?';args.append(last)
  rows=[dict(x) for x in self.conn.execute(sql+' ORDER BY t.id LIMIT ?',(*args,limit+1))];return self._bounded(rows,scope,limit)

class EventNarrativeMultiRunProvider:
 family='event_narrative_archive'
 def __init__(self,providers):
  if not providers:raise ValueError('event multi-run providers required')
  contracts={x.family_contract_version for x in providers}
  if len(contracts)!=1:raise ValueError('event multi-run contract mismatch')
  self.family_contract_version=next(iter(contracts));self.providers={x.run:x for x in providers}
  if len(self.providers)!=len(providers):raise ValueError('duplicate event exact run')
 def capability(self):
  values=[self.providers[x].capability() for x in sorted(self.providers)];base=deepcopy(values[0]);base['eligible_runs']=[deepcopy(v['eligible_runs'][0]) for v in values];base['coverage_semantics']={'coverage':'explicit_exact_run_scoped_event_narrative_v3_archives','run_specific_coverage':True,'full_activity':False,'full_7_0':False};base['default_run']=False;base['latest_run']=False;return base
 def _pick(self,run):return self.providers.get(run)
 def _missing(self,run):return FamilyEvidenceResult('not_found',self.family,run,self.family_contract_version,'unavailable',False,{'status':'NotFound','reason':'ingest_run_id_mismatch'})
 def list_evidence(self,**kwargs):
  provider=self._pick(kwargs['ingest_run_id']);return provider.list_evidence(**kwargs) if provider else self._missing(kwargs['ingest_run_id'])
 def get_evidence(self,**kwargs):
  provider=self._pick(kwargs['ingest_run_id']);return provider.get_evidence(**kwargs) if provider else self._missing(kwargs['ingest_run_id'])
 def search_text(self,**kwargs):
  provider=self._pick(kwargs['ingest_run_id']);return provider.search_text(**kwargs) if provider else self._missing(kwargs['ingest_run_id'])

def combine_event_narrative_providers(providers):
 groups={}
 for provider in providers:groups.setdefault(provider.family_contract_version,[]).append(provider)
 return [items[0] if len(items)==1 else EventNarrativeMultiRunProvider(items) for _,items in sorted(groups.items())]
