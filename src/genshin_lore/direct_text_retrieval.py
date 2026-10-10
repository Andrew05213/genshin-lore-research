"""Exact-run routed providers for Readable and Subtitle structured evidence."""
from __future__ import annotations
import base64,hashlib,json,sqlite3
from pathlib import Path
from .family_evidence import FamilyEvidenceResult
BUDGET=262144
def _sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
 return h.hexdigest()
def _id(path):s=Path(path).stat();return s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns
def _token(scope,last):return base64.urlsafe_b64encode(json.dumps({'scope':scope,'last':last},sort_keys=True,separators=(',',':')).encode()).decode()
def _decode(value,scope):
 if value is None:return None
 try:x=json.loads(base64.urlsafe_b64decode(value.encode()))
 except Exception as exc:raise ValueError('invalid cursor') from exc
 if x.get('scope')!=scope or not isinstance(x.get('last'),str):raise ValueError('cursor scope mismatch')
 return x['last']
def _wire(result):return 3*len(json.dumps(json.dumps(result.envelope(),ensure_ascii=True,separators=(',',':')),ensure_ascii=True,separators=(',',':')).encode())+8192
class _Base:
 def __init__(self,path,manifest_path):
  self.path=Path(path).resolve();self.manifest_path=Path(manifest_path).resolve();self.manifest=json.loads(self.manifest_path.read_text());a=self.manifest['artifact']
  if self.path.stat().st_size!=a['size_bytes'] or _sha(self.path)!=a['sha256']:raise ValueError('direct-text artifact identity')
  self.bindings={self.path:_id(self.path),self.manifest_path:_id(self.manifest_path)};self.invalid=False;self.run=self.manifest['release']['run_id'];self.conn=sqlite3.connect(f'file:{self.path.as_posix()}?mode=ro&immutable=1',uri=True,check_same_thread=False);self.conn.row_factory=sqlite3.Row
  if any(Path(str(self.path)+x).exists() for x in ('-wal','-shm','-journal')):raise ValueError('artifact sidecar')
 def _stable(self):
  if self.invalid or any(_id(p)!=v for p,v in self.bindings.items()):self.invalid=True;raise ValueError('direct-text provider permanently invalidated')
 def _check(self,run,contract):self._stable();return run==self.run and contract==self.family_contract_version
 def _result(self,data,status='ok',run=None):return FamilyEvidenceResult(status,self.family,run or self.run,self.family_contract_version,self.coverage,True,data)
 def _bounded(self,rows,scope,limit):
  if not rows:return self._result({'record_kind':scope['record_kind'],'items':[],'delivered_count':0,'next_cursor':None,'scope_complete':True,'partial':False})
  out=[]
  for row in rows[:limit]:
   candidate=out+[row];more=len(rows)>len(candidate);d={'record_kind':scope['record_kind'],'items':candidate,'delivered_count':len(candidate),'next_cursor':_token(scope,candidate[-1]['id']) if more else None,'scope_complete':not more,'partial':more}
   if _wire(self._result(d))>BUDGET:
    if not out:raise ValueError('single record exceeds inline limit')
    break
   out=candidate
  more=len(rows)>len(out);d={'record_kind':scope['record_kind'],'items':out,'delivered_count':len(out),'next_cursor':_token(scope,out[-1]['id']) if more else None,'scope_complete':not more,'partial':more};return self._result(d)
 def capability(self):
  self._stable();r=self.manifest['release'];return {'family':self.family,'family_contract_version':self.family_contract_version,'evidence_available':True,'evidence_only':True,'supported_operations':['list_family_evidence','get_family_evidence','search_family_text'],'supported_filters':self.filters,'pagination_contract':{'scope':'exact run + contract + record kind + filters/query','full_response_budget_bytes':BUDGET,'legal_empty_page':True},'eligible_runs':[{'ingest_run_id':self.run,'parser_build_id':r['parser_build_id'],'snapshot_id':r['snapshot_id'],'counts':r['counts'],'artifact':self.manifest['artifact'],'coverage':self.coverage,'corpus_complete':True,'product_accepted':True}],'default_run':False,'latest_run':False,'sibling_fallback':False,'raw_body_dependency':r0_locator(self.kind),'semantic_owner_complete':False}

def r0_locator(kind):return {'family':'raw_'+kind+'_json_occurrence','family_contract_version':'raw-physical-evidence-v1','ingest_run_id':'ir-batch-m3-r0-physical:v1','explicit_mount_required':True}

class ReadableArchiveProvider(_Base):
 family='readable_archive';family_contract_version='readable-evidence-v1';coverage='complete_frozen_readable_physical_and_line_projection';kind='readable';filters={'list':['record_kind','source_path_prefix','file_id'],'search':['source_path_prefix']}
 def __init__(self,path,manifest_path):
  super().__init__(path,manifest_path)
  if self.manifest.get('family')!=self.family or self.manifest.get('family_contract_version')!=self.family_contract_version:raise ValueError('Readable manifest')
  rel=json.loads(self.conn.execute('SELECT manifest_json FROM readable_release WHERE id=1').fetchone()[0])
  if rel!=self.manifest['release']:raise ValueError('Readable release')
 def list_evidence(self,*,ingest_run_id,family_contract_version,filters=None,cursor=None,limit=50):
  if not self._check(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  f=filters or {};allowed={'record_kind','source_path_prefix','file_id'}
  if not isinstance(f,dict) or set(f)-allowed:raise ValueError('filters')
  kind=f.get('record_kind','files');scope={'run':self.run,'contract':self.family_contract_version,'record_kind':kind,'filters':dict(sorted(f.items()))};last=_decode(cursor,scope);args=[]
  if kind=='files':sql='SELECT id,source_path,byte_length,encoding,line_count,empty,r0_occurrence_id FROM readable_file WHERE 1=1';pathcol='source_path'
  elif kind=='lines':sql='SELECT l.id,l.file_id,l.ordinal,l.pointer,l.byte_start,l.byte_end,l.terminator,l.raw_text,l.text_sha256,f.source_path FROM readable_line l JOIN readable_file f ON f.id=l.file_id WHERE 1=1';pathcol='f.source_path'
  elif kind=='relations':sql='SELECT r.id,r.origin_family,r.origin_contract,r.origin_parser,r.origin_run,r.origin_source_path,r.origin_pointer,r.relation_kind,r.raw_locator,r.status,r.search_complete,r.candidate_count,c.target_file_id,c.target_path,c.target_r0_occurrence_id FROM readable_relation r LEFT JOIN readable_candidate c ON c.relation_id=r.id WHERE 1=1';pathcol='c.target_path'
  else:raise ValueError('record_kind')
  if f.get('source_path_prefix'):sql+=f' AND {pathcol} LIKE ?';args.append(f['source_path_prefix']+'%')
  if f.get('file_id'):
   if kind=='lines':sql+=' AND l.file_id=?'
   elif kind=='files':sql+=' AND id=?'
   else:sql+=' AND c.target_file_id=?'
   args.append(f['file_id'])
  if last:sql+=(' AND l.id>?' if kind=='lines' else ' AND r.id>?' if kind=='relations' else ' AND id>?');args.append(last)
  rows=[dict(x) for x in self.conn.execute(sql+' ORDER BY '+('l.id' if kind=='lines' else 'r.id' if kind=='relations' else 'id')+' LIMIT ?',(*args,limit+1))];return self._bounded(rows,scope,limit)
 def get_evidence(self,*,ingest_run_id,family_contract_version,occurrence_id):
  if not self._check(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  row=self.conn.execute('SELECT * FROM readable_file WHERE id=?',(occurrence_id,)).fetchone()
  if row:return self._result({'container':dict(row),'body_delivery':{'record_kind':'lines','filter':{'file_id':occurrence_id},'bounded':True},'navigation_delivery':{'record_kind':'relations','filter':{'file_id':occurrence_id}},'raw_body':r0_locator('readable')|{'occurrence_id':row['r0_occurrence_id']},'complete_container_summary':True,'body_embedded':False})
  line=self.conn.execute('SELECT * FROM readable_line WHERE id=?',(occurrence_id,)).fetchone()
  if line:return self._result({'line':dict(line),'complete':True})
  return self._result({'status':'NotFound'},'not_found')
 def search_text(self,*,ingest_run_id,family_contract_version,query,filters=None,cursor=None,limit=50):
  if not self._check(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  if not isinstance(query,str) or not query:raise ValueError('query')
  f=filters or {};scope={'run':self.run,'contract':self.family_contract_version,'record_kind':'search','filters':dict(sorted(f.items())),'query':query};last=_decode(cursor,scope);sql='SELECT l.id,l.file_id,l.ordinal,l.pointer,l.raw_text,l.text_sha256,f.source_path FROM readable_line l JOIN readable_file f ON f.id=l.file_id WHERE instr(l.raw_text,?)>0';args=[query]
  if f.get('source_path_prefix'):sql+=' AND f.source_path LIKE ?';args.append(f['source_path_prefix']+'%')
  if set(f)-{'source_path_prefix'}:raise ValueError('filters')
  if last:sql+=' AND l.id>?';args.append(last)
  rows=[dict(x) for x in self.conn.execute(sql+' ORDER BY l.id LIMIT ?',(*args,limit+1))];return self._bounded(rows,scope,limit)

class SubtitleArchiveProvider(_Base):
 family='subtitle_archive';family_contract_version='subtitle-evidence-v1';coverage='complete_frozen_subtitle_physical_and_cue_projection';kind='subtitle';filters={'list':['record_kind','source_path_prefix','file_id','record_kind_filter'],'search':['source_path_prefix']}
 def __init__(self,path,manifest_path):
  super().__init__(path,manifest_path)
  if self.manifest.get('family')!=self.family or self.manifest.get('family_contract_version')!=self.family_contract_version:raise ValueError('Subtitle manifest')
  if json.loads(self.conn.execute('SELECT manifest_json FROM subtitle_release WHERE id=1').fetchone()[0])!=self.manifest['release']:raise ValueError('Subtitle release')
 def list_evidence(self,*,ingest_run_id,family_contract_version,filters=None,cursor=None,limit=50):
  if not self._check(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  f=filters or {};allowed={'record_kind','source_path_prefix','file_id','record_kind_filter'}
  if not isinstance(f,dict) or set(f)-allowed:raise ValueError('filters')
  kind=f.get('record_kind','files');scope={'run':self.run,'contract':self.family_contract_version,'record_kind':kind,'filters':dict(sorted(f.items()))};last=_decode(cursor,scope);args=[]
  if kind=='files':sql='SELECT id,source_path,byte_length,encoding,record_count,valid_cue_count,malformed_count,empty,r0_occurrence_id FROM subtitle_file WHERE 1=1';pathcol='source_path';idcol='id'
  elif kind=='cues':sql='SELECT r.*,f.source_path FROM subtitle_record r JOIN subtitle_file f ON f.id=r.file_id WHERE 1=1';pathcol='f.source_path';idcol='r.id'
  elif kind=='relations':sql='SELECT r.*,c.target_file_id,c.target_path,c.target_r0_occurrence_id FROM subtitle_relation r LEFT JOIN subtitle_candidate c ON c.relation_id=r.id WHERE 1=1';pathcol='c.target_path';idcol='r.id'
  else:raise ValueError('record_kind')
  if f.get('source_path_prefix'):sql+=f' AND {pathcol} LIKE ?';args.append(f['source_path_prefix']+'%')
  if f.get('file_id'):
   sql+=(' AND r.file_id=?' if kind=='cues' else ' AND c.target_file_id=?' if kind=='relations' else ' AND id=?');args.append(f['file_id'])
  if f.get('record_kind_filter'):
   if kind!='cues':raise ValueError('record_kind_filter')
   sql+=' AND r.record_kind=?';args.append(f['record_kind_filter'])
  if last:sql+=f' AND {idcol}>?';args.append(last)
  rows=[dict(x) for x in self.conn.execute(sql+f' ORDER BY {idcol} LIMIT ?',(*args,limit+1))];return self._bounded(rows,scope,limit)
 def get_evidence(self,*,ingest_run_id,family_contract_version,occurrence_id):
  if not self._check(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  row=self.conn.execute('SELECT * FROM subtitle_file WHERE id=?',(occurrence_id,)).fetchone()
  if row:return self._result({'container':dict(row),'cue_delivery':{'record_kind':'cues','filter':{'file_id':occurrence_id},'bounded':True},'navigation_delivery':{'record_kind':'relations','filter':{'file_id':occurrence_id}},'raw_body':r0_locator('subtitle')|{'occurrence_id':row['r0_occurrence_id']},'complete_container_summary':True,'body_embedded':False})
  rec=self.conn.execute('SELECT * FROM subtitle_record WHERE id=?',(occurrence_id,)).fetchone()
  if rec:return self._result({'record':dict(rec),'complete':True})
  return self._result({'status':'NotFound'},'not_found')
 def search_text(self,*,ingest_run_id,family_contract_version,query,filters=None,cursor=None,limit=50):
  if not self._check(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  if not isinstance(query,str) or not query:raise ValueError('query')
  f=filters or {};scope={'run':self.run,'contract':self.family_contract_version,'record_kind':'search','filters':dict(sorted(f.items())),'query':query};last=_decode(cursor,scope);sql='SELECT r.id,r.file_id,r.ordinal,r.pointer,r.raw_index,r.raw_timing,r.parsed_start_ms,r.parsed_end_ms,r.raw_text,f.source_path FROM subtitle_record r JOIN subtitle_file f ON f.id=r.file_id WHERE instr(r.raw_text,?)>0';args=[query]
  if f.get('source_path_prefix'):sql+=' AND f.source_path LIKE ?';args.append(f['source_path_prefix']+'%')
  if set(f)-{'source_path_prefix'}:raise ValueError('filters')
  if last:sql+=' AND r.id>?';args.append(last)
  rows=[dict(x) for x in self.conn.execute(sql+' ORDER BY r.id LIMIT ?',(*args,limit+1))];return self._bounded(rows,scope,limit)
