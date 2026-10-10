"""Exact-run read-only provider for the Mail narrative archive."""
import base64,hashlib,json,os,sqlite3
from pathlib import Path
from .family_evidence import FamilyEvidenceResult
ROOT=Path(__file__).resolve().parents[2];BUDGET=262144
def _sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(4*1024**2),b''):h.update(b)
 return h.hexdigest()
def _fp(p):
 p=Path(p);s=p.stat();return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,_sha(p))
def _identity(p):
 s=Path(p).stat();return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)
def _token(scope,last):return base64.urlsafe_b64encode(json.dumps({'scope':scope,'last':last},sort_keys=True,separators=(',',':')).encode()).decode()
def _decode(value,scope):
 if value is None:return None
 try:x=json.loads(base64.urlsafe_b64decode(value.encode()))
 except Exception as e:raise ValueError('invalid cursor') from e
 if x.get('scope')!=scope or not isinstance(x.get('last'),str):raise ValueError('cursor scope mismatch')
 return x['last']
def _filters(value,allowed):
 if value is None:return {}
 if not isinstance(value,dict) or any(k not in allowed or not isinstance(v,str) or not v for k,v in value.items()):raise ValueError('filters')
 return dict(sorted(value.items()))
def _bounded(rows,scope,limit):
 out=[]
 for row in rows[:limit]:
  candidate=out+[row]
  if len(json.dumps(candidate,ensure_ascii=False,separators=(',',':')).encode())>BUDGET:
   if not out:raise ValueError('single record exceeds inline limit')
   break
  out=candidate
 more=len(rows)>len(out);return {'items':out,'delivered_count':len(out),'next_cursor':_token(scope,out[-1]['id']) if more and out else None,'scope_complete':not more,'partial':more}
class MailArchiveProvider:
 family='mail_narrative_archive';family_contract_version='mail-narrative-evidence-v1'
 def __init__(self,path,manifest_path):
  self.path=Path(path).resolve();self.manifest_path=Path(manifest_path).resolve();self.invalidated=False
  if self.manifest_path!=ROOT/'manifests/mail-archive/runtime.json':raise ValueError('explicit tracked Mail manifest required')
  mf=_fp(self.manifest_path);self.manifest=json.loads(self.manifest_path.read_text());m=self.manifest;af=_fp(self.path)
  if m['contract']!='mail-narrative-runtime-v1' or af[4]!=m['artifact']['sha256'] or af[2]!=m['artifact']['size_bytes']:raise ValueError('Mail artifact qualification')
  self.scope=ROOT/'manifests/mail-archive/scope.json';sf=_fp(self.scope)
  if sf[4]!=m['release']['scope_sha256']:raise ValueError('Mail scope binding')
  self.bindings={self.path:af[:4],self.manifest_path:mf[:4],self.scope:sf[:4]};self.run=m['release']['run_id'];self.conn=sqlite3.connect(self.path.as_uri()+'?mode=ro&immutable=1',uri=True,check_same_thread=False);self.conn.row_factory=sqlite3.Row
  structure=[list(r) for r in self.conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
  if structure!=m['schema_structure'] or json.loads(self.conn.execute('SELECT manifest_json FROM mail_release WHERE id=1').fetchone()[0])!=m['release'] or self.conn.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Mail release qualification')
  for table,n in m['counts'].items():
   if self.conn.execute('SELECT count(*) FROM '+table).fetchone()[0]!=n:raise ValueError('Mail count seal')
  if any(Path(str(self.path)+s).exists() for s in ('-wal','-shm','-journal')):raise ValueError('Mail sidecar')
 def _stable(self):
  try:
   if self.invalidated or any(_identity(p)!=v for p,v in self.bindings.items()) or any(Path(str(self.path)+s).exists() for s in ('-wal','-shm','-journal')):raise ValueError
  except (OSError,ValueError):self.invalidated=True;raise ValueError('Mail permanently invalidated')
 def capability(self):
  self._stable();return {'family':self.family,'family_contract_version':self.family_contract_version,'evidence_only':True,'evidence_available':True,'supported_operations':['list_family_evidence','get_family_evidence','search_family_text'],'supported_filters':{'list':['source_kind','native_id'],'search':['locale','owner_field']},'pagination_contract':'exact run/contract/operation/filter/query keyset; 262144-byte list/search budget','container_scope':'one physical Mail, BirthdayMail, or AlchemySimLetter row','coverage_semantics':{'coverage':'complete_frozen_mail_template_birthday_schedule_and_alchemy_letter_projection','all_mailbox_gameplay':False,'full_7_0':False},'eligible_runs':[{'ingest_run_id':self.run,'parser_build_id':self.manifest['release']['parser_build_id'],'snapshot_id':self.manifest['release']['snapshot_id'],'counts':self.manifest['counts'],'text_state_counts':self.manifest['release']['text_state_counts'],'relation_state_counts':self.manifest['release']['relation_state_counts'],'artifact':self.manifest['artifact'],'corpus_complete':True,'product_accepted':self.manifest['product_accepted']}]}
 def _result(self,data,status='ok',run=None):self._stable();return FamilyEvidenceResult(status,self.family,run or self.run,self.family_contract_version,'complete_frozen_mail_template_birthday_schedule_and_alchemy_letter_projection',True,data)
 def _check(self,run,contract):self._stable();return run==self.run and contract==self.family_contract_version
 def list_evidence(self,*,ingest_run_id,family_contract_version,filters=None,cursor=None,limit=50):
  if not self._check(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  if type(limit) is not int or not 1<=limit<=100:raise ValueError('limit')
  f=_filters(filters,{'source_kind','native_id'});scope={'run':self.run,'contract':self.family_contract_version,'op':'list','filters':f};last=_decode(cursor,scope);sql='SELECT id,source_kind,ordinal,native_id,source_path,pointer FROM mail_row WHERE run_id=?';args=[self.run]
  for k in ('source_kind','native_id'):
   if k in f:sql+=' AND '+k+'=?';args.append(f[k])
  if last:sql+=' AND id>?';args.append(last)
  rows=[dict(x) for x in self.conn.execute(sql+' ORDER BY id LIMIT ?',(*args,limit+1))];return self._result(_bounded(rows,scope,limit))
 def get_evidence(self,*,ingest_run_id,family_contract_version,occurrence_id):
  if not self._check(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  row=self.conn.execute('SELECT * FROM mail_row WHERE id=? AND run_id=?',(occurrence_id,self.run)).fetchone()
  if row is None:return self._result({'status':'NotFound'},'not_found')
  slots=[dict(x) for x in self.conn.execute('SELECT * FROM mail_slot WHERE row_id=? ORDER BY owner_field,locale',(occurrence_id,))];texts=[dict(x) for x in self.conn.execute('SELECT t.* FROM mail_text t JOIN mail_slot s ON s.id=t.slot_id WHERE s.row_id=? ORDER BY t.id',(occurrence_id,))];rels=[dict(x) for x in self.conn.execute('SELECT * FROM mail_relation WHERE row_id=? ORDER BY id',(occurrence_id,))];cands=[]
  for rel in rels:
   for x in self.conn.execute('SELECT * FROM mail_candidate WHERE relation_id=? ORDER BY id',(rel['id'],)):
    item=dict(x);item['runtime_locator']={'family':self.family,'family_contract_version':self.family_contract_version,'ingest_run_id':self.run,'occurrence_id':item['target_row_id']};cands.append(item)
  data={'container':dict(row),'text_slots':slots,'texts':texts,'relations':rels,'candidates':cands,'complete':True,'target_bodies_embedded':False};
  if len(json.dumps(data,ensure_ascii=False,separators=(',',':')).encode())>1048576:raise ValueError('single Mail container exceeds inline limit')
  return self._result(data)
 def search_text(self,*,ingest_run_id,family_contract_version,query,filters=None,cursor=None,limit=50):
  if not self._check(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  if not isinstance(query,str) or not query or type(limit) is not int or not 1<=limit<=100:raise ValueError('query/limit')
  f=_filters(filters,{'locale','owner_field'});scope={'run':self.run,'contract':self.family_contract_version,'op':'search','filters':f,'query':query};last=_decode(cursor,scope);sql='SELECT t.id,t.slot_id,t.source_path,t.pair_ordinal,t.pointer,t.raw_text,t.raw_text_sha256,s.row_id AS container_id,s.locale,s.owner_field FROM mail_text t JOIN mail_slot s ON s.id=t.slot_id JOIN mail_row r ON r.id=s.row_id WHERE r.run_id=? AND instr(t.raw_text,?)>0';args=[self.run,query]
  for k in ('locale','owner_field'):
   if k in f:sql+=' AND s.'+k+'=?';args.append(f[k])
  if last:sql+=' AND t.id>?';args.append(last)
  rows=[dict(x) for x in self.conn.execute(sql+' ORDER BY t.id LIMIT ?',(*args,limit+1))];return self._result(_bounded(rows,scope,limit))
