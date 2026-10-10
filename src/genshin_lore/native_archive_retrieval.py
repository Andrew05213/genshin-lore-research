"""Exact-run provider for the native archive complement."""
import base64,hashlib,json,sqlite3
from copy import deepcopy
from pathlib import Path
from .family_evidence import FamilyEvidenceResult
BUDGET=262144
TABLES=('native_archive_container','native_archive_slot','native_archive_text','native_archive_relation','native_archive_candidate')
def schema(c):return [[a,b,d,' '.join((s or '').split()).lower()] for a,b,d,s in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
def logical(c):
 out={}
 for table in TABLES:
  n=len(c.execute('SELECT * FROM '+table+' LIMIT 0').description);rows=c.execute('SELECT * FROM '+table+' ORDER BY '+','.join(str(i+1) for i in range(n)))
  out[table]=hashlib.sha256('\n'.join(json.dumps(list(x),ensure_ascii=False,separators=(',',':')) for x in rows).encode()).hexdigest()
 return out
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
 return h.hexdigest()
def token(scope,last):return base64.urlsafe_b64encode(json.dumps({'scope':scope,'last':last},sort_keys=True,separators=(',',':')).encode()).decode()
def decode(value,scope):
 if value is None:return None
 try:x=json.loads(base64.urlsafe_b64decode(value))
 except Exception as exc:raise ValueError('invalid cursor') from exc
 if x.get('scope')!=scope or not isinstance(x.get('last'),str):raise ValueError('cursor scope mismatch')
 return x['last']
def wire(result):return 3*len(json.dumps(json.dumps(result.envelope(),ensure_ascii=True,separators=(',',':')),ensure_ascii=True,separators=(',',':')).encode())+8192
class NativeArchiveProvider:
 family='native_archive';family_contract_version='native-archive-evidence-v1';coverage='complete_frozen_handbook_rogue_beyond_and_snez_projection_complement'
 def __init__(self,path,manifest_path):
  self.path=Path(path).resolve();self.manifest_path=Path(manifest_path).resolve();self.manifest=json.loads(self.manifest_path.read_text());a=self.manifest['artifact']
  self.coverage=self.manifest.get('coverage_label',self.coverage)
  if self.path.stat().st_size!=a['size_bytes'] or sha(self.path)!=a['sha256']:raise ValueError('native archive artifact identity')
  self.fp={p:(p.stat().st_dev,p.stat().st_ino,p.stat().st_size,p.stat().st_mtime_ns) for p in (self.path,self.manifest_path)};self.invalid=False;self.run=self.manifest['release']['run_id'];self.conn=sqlite3.connect(f'file:{self.path}?mode=ro&immutable=1',uri=True,check_same_thread=False);self.conn.row_factory=sqlite3.Row
  if json.loads(self.conn.execute('SELECT manifest_json FROM native_archive_release WHERE id=1').fetchone()[0])!=self.manifest['release']:raise ValueError('native archive release')
  if 'schema_structure_normalized' in self.manifest and (logical(self.conn)!=self.manifest.get('logical_seals') or schema(self.conn)!=self.manifest.get('schema_structure_normalized') or self.manifest.get('schema_structure_match') is not True or self.manifest.get('write_once_structure_match') is not True or self.manifest.get('unexpected_authoritative_objects')!=[]):raise ValueError('native archive runtime structural proof')
 def _stable(self):
  if self.invalid or any((p.stat().st_dev,p.stat().st_ino,p.stat().st_size,p.stat().st_mtime_ns)!=v for p,v in self.fp.items()):self.invalid=True;raise ValueError('native archive provider permanently invalidated')
 def _ok(self,run,contract):self._stable();return run==self.run and contract==self.family_contract_version
 def _result(self,data,status='ok',run=None):return FamilyEvidenceResult(status,self.family,run or self.run,self.family_contract_version,self.coverage,True,data)
 def _bounded(self,rows,scope,limit):
  if not rows:return self._result({'record_kind':scope['record_kind'],'items':[],'delivered_count':0,'next_cursor':None,'scope_complete':True,'partial':False})
  out=[]
  for row in rows[:limit]:
   d={'record_kind':scope['record_kind'],'items':out+[row],'delivered_count':len(out)+1,'next_cursor':token(scope,row['id']) if len(rows)>len(out)+1 else None,'scope_complete':len(rows)<=len(out)+1,'partial':len(rows)>len(out)+1}
   if wire(self._result(d))>BUDGET:
    if not out:raise ValueError('single record exceeds inline limit')
    break
   out.append(row)
  more=len(rows)>len(out);return self._result({'record_kind':scope['record_kind'],'items':out,'delivered_count':len(out),'next_cursor':token(scope,out[-1]['id']) if more else None,'scope_complete':not more,'partial':more})
 def capability(self):
  self._stable();r=self.manifest['release'];return {'family':self.family,'family_contract_version':self.family_contract_version,'evidence_available':True,'evidence_only':True,'supported_operations':['list_family_evidence','get_family_evidence','search_family_text'],'supported_filters':{'list':['boundary','container_kind'],'search':['boundary','locale','owner_field']},'pagination_contract':{'list':'bounded deterministic container pages','search':'bounded deterministic literal-hit pages','get':'one complete physical container or explicit oversized failure','cursor_scope':['family','contract','run','record_kind','filters','query'],'legal_empty':{'items':[],'delivered_count':0,'next_cursor':None,'scope_complete':True,'partial':False},'wire_budget_bytes':BUDGET},'eligible_runs':[{'ingest_run_id':self.run,'parser_build_id':r['parser_build_id'],'snapshot_id':r['snapshot_id'],'counts':r['counts'],'coverage':self.coverage,'corpus_complete':True,'product_accepted':True}],'default_run':False,'latest_run':False,'sibling_fallback':False,'historical_body_copied':False}
 def list_evidence(self,*,ingest_run_id,family_contract_version,filters=None,cursor=None,limit=50):
  if not self._ok(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  f=filters or {};allowed={'boundary','container_kind'}
  if not isinstance(f,dict) or set(f)-allowed:raise ValueError('filters')
  scope={'run':self.run,'contract':self.family_contract_version,'record_kind':'containers','filters':dict(sorted(f.items()))};last=decode(cursor,scope);sql='SELECT id,boundary,container_kind,source_path,ordinal,pointer,native_id_field,native_id,reuse_family,reuse_contract,reuse_parser,reuse_run,reuse_occurrence_id FROM native_archive_container WHERE run_id=?';args=[self.run]
  for key in ('boundary','container_kind'):
   if f.get(key):sql+=' AND '+key+'=?';args.append(f[key])
  if last:sql+=' AND id>?';args.append(last)
  rows=[dict(x) for x in self.conn.execute(sql+' ORDER BY id LIMIT ?',(*args,limit+1))];return self._bounded(rows,scope,limit)
 def get_evidence(self,*,ingest_run_id,family_contract_version,occurrence_id):
  if not self._ok(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  row=self.conn.execute('SELECT * FROM native_archive_container WHERE id=? AND run_id=?',(occurrence_id,self.run)).fetchone()
  if not row:return self._result({'status':'NotFound'},'not_found')
  slots=[dict(x) for x in self.conn.execute('SELECT * FROM native_archive_slot WHERE container_id=? ORDER BY owner_field,locale',(occurrence_id,))];texts=[dict(x) for x in self.conn.execute('SELECT t.* FROM native_archive_text t JOIN native_archive_slot s ON s.id=t.slot_id WHERE s.container_id=? ORDER BY t.id',(occurrence_id,))];rels=[]
  for r in self.conn.execute('SELECT * FROM native_archive_relation WHERE container_id=? ORDER BY id',(occurrence_id,)):
   x=dict(r);x['candidates']=[dict(z) for z in self.conn.execute('SELECT * FROM native_archive_candidate WHERE relation_id=? ORDER BY id',(r['id'],))];rels.append(x)
  result=self._result({'container':dict(row),'text_slots':slots,'text_candidates':texts,'relations':rels,'complete_physical_container':True,'historical_body_embedded':False})
  if wire(result)>BUDGET:raise ValueError('physical container exceeds inline limit')
  return result
 def search_text(self,*,ingest_run_id,family_contract_version,query,filters=None,cursor=None,limit=50):
  if not self._ok(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  if not isinstance(query,str) or not query:raise ValueError('query')
  f=filters or {};allowed={'boundary','locale','owner_field'}
  if set(f)-allowed:raise ValueError('filters')
  scope={'run':self.run,'contract':self.family_contract_version,'record_kind':'search','query':query,'filters':dict(sorted(f.items()))};last=decode(cursor,scope);sql='SELECT t.id,t.slot_id,t.source_path,t.pointer,t.raw_text,t.raw_text_sha256,s.container_id,s.owner_field,s.locale,c.boundary FROM native_archive_text t JOIN native_archive_slot s ON s.id=t.slot_id JOIN native_archive_container c ON c.id=s.container_id WHERE c.run_id=? AND instr(t.raw_text,?)>0';args=[self.run,query]
  for col in ('boundary','locale','owner_field'):
   if f.get(col):sql+=' AND '+('c.' if col=='boundary' else 's.')+col+'=?';args.append(f[col])
  if last:sql+=' AND t.id>?';args.append(last)
  rows=[dict(x) for x in self.conn.execute(sql+' ORDER BY t.id LIMIT ?',(*args,limit+1))];return self._bounded(rows,scope,limit)

class NativeArchiveMultiRunProvider:
 family='native_archive';family_contract_version='native-archive-evidence-v1'
 def __init__(self,providers):
  if not providers or any(x.family_contract_version!=self.family_contract_version for x in providers):raise ValueError('native archive multi-run contract mismatch')
  self.providers={x.run:x for x in providers}
  if len(self.providers)!=len(providers):raise ValueError('duplicate native archive exact run')
 def capability(self):
  caps=[self.providers[x].capability() for x in sorted(self.providers)];out=deepcopy(caps[0]);out['eligible_runs']=[deepcopy(x['eligible_runs'][0]) for x in caps];out['coverage']='explicit_exact_run_scoped_native_archives';out['default_run']=False;out['latest_run']=False;out['sibling_fallback']=False;return out
 def _missing(self,run):return FamilyEvidenceResult('not_found',self.family,run,self.family_contract_version,'unavailable',False,{'status':'NotFound','reason':'ingest_run_id_mismatch'})
 def _call(self,name,kwargs):
  p=self.providers.get(kwargs['ingest_run_id']);return getattr(p,name)(**kwargs) if p else self._missing(kwargs['ingest_run_id'])
 def list_evidence(self,**kwargs):return self._call('list_evidence',kwargs)
 def get_evidence(self,**kwargs):return self._call('get_evidence',kwargs)
 def search_text(self,**kwargs):return self._call('search_text',kwargs)

def combine_native_archive_providers(providers):
 return providers if len(providers)<2 else [NativeArchiveMultiRunProvider(providers)]
