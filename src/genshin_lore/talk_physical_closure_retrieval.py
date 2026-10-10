"""Bounded exact-run retrieval for Talk physical-tree classifications."""
import base64,json,sqlite3
from pathlib import Path
from .family_evidence import FamilyEvidenceResult
from .r5_quest_talk_retrieval import _fingerprint,_identity,_cursor,_decode,_filters
ROOT=Path(__file__).resolve().parents[2];BUDGET=262144;CHUNK=49152
def wire_size(result):return 3*len(json.dumps(json.dumps(result.envelope(),ensure_ascii=True),ensure_ascii=True).encode())+8192
class TalkPhysicalClosureProvider:
 family='talk_physical_archive';family_contract_version='talk-physical-closure-v1'
 def __init__(self,artifact,manifest):
  self.path=Path(artifact).absolute();self.manifest_path=Path(manifest).resolve();self.invalidated=False
  if self.manifest_path!=ROOT/'manifests/talk-physical-closure/runtime.json':raise ValueError('explicit tracked Talk closure manifest required')
  mf=_fingerprint(self.manifest_path);self.manifest=json.loads(self.manifest_path.read_text());m=self.manifest;fp=_fingerprint(self.path)
  if m['contract']!='talk-physical-closure-runtime-v1' or fp[2]!=m['artifact']['size_bytes'] or fp[4]!=m['artifact']['sha256']:raise ValueError('Talk closure artifact qualification')
  scope=ROOT/'manifests/talk-physical-closure/scope.json';sf=_fingerprint(scope)
  if sf[4]!=m['release']['scope_sha256']:raise ValueError('scope binding')
  self.bindings={self.path:fp[:4],self.manifest_path:mf[:4],scope:sf[:4]};self.run=m['release']['run_id'];self.conn=sqlite3.connect(self.path.as_uri()+'?mode=ro&immutable=1',uri=True,check_same_thread=False);self.conn.row_factory=sqlite3.Row
  if any(self.conn.execute('select count(*) from '+t).fetchone()[0]!=n for t,n in m['release']['counts'].items()):raise ValueError('count seal')
  structure=[list(x) for x in self.conn.execute("select type,name,tbl_name,sql from sqlite_master where name not like 'sqlite_%' order by type,name")]
  if structure!=m['schema_structure'] or json.loads(self.conn.execute('select manifest_json from talk_physical_release').fetchone()[0])!=m['release'] or self.conn.execute('pragma foreign_key_check').fetchall():raise ValueError('structure/release/FK')
  self._stable()
 def _stable(self):
  try:
   if self.invalidated or any(_identity(p)!=v for p,v in self.bindings.items()) or any(Path(str(self.path)+s).exists() for s in ('-wal','-shm','-journal')):raise ValueError('drift')
  except Exception:self.invalidated=True;raise ValueError('Talk closure run permanently invalidated')
 def capability(self):
  self._stable();r=self.manifest['release'];return {'family':self.family,'family_contract_version':self.family_contract_version,'supported_operations':['list_family_evidence','get_family_evidence'],'supported_filters':{'list':['record_kind','classification','shape','container_id','reference_id']},'pagination_contract':{'scope':'exact run + contract + record kind + filters','ordering':'physical occurrence id','max_limit':100,'raw_body_chunk_bytes':CHUNK,'partial_responses_explicit':True},'search_contract':'unsupported: no family-owned text; use exact quest_talk_pilot v7 target locator','eligible_runs':[{'ingest_run_id':self.run,'parser_build_id':r['parser_build_id'],'snapshot_id':r['snapshot_id'],'coverage':'all55925 Talk physical files classified; complement bodies/relations delivered; mapped bodies exact-locator reuse','corpus_complete':True,'product_accepted':self.manifest['product_accepted'],'counts':r['counts'],'physical_talk_tree_classification_complete':True,'semantic_mapping_resolution':'partial_with_terminal_unresolved'}],'default_run':False,'latest_run':False,'no_body_copy':True}
 def _result(self,data,status='ok',run=None):self._stable();return FamilyEvidenceResult(status,self.family,run or self.run,self.family_contract_version,'complete_talk_physical_tree_classification',True,data)
 def _check(self,run,contract):self._stable();return run==self.run and contract==self.family_contract_version
 def _missing(self,run):return self._result({'status':'NotFound'},'not_found',run)
 def list_evidence(self,*,ingest_run_id,family_contract_version,filters=None,cursor=None,limit=50):
  if not self._check(ingest_run_id,family_contract_version):return self._missing(ingest_run_id)
  if type(limit) is not int or not 1<=limit<=100:raise ValueError('limit')
  f=_filters(filters,{'record_kind','classification','shape','container_id','reference_id'});kind=f.get('record_kind','files');scope={'run':self.run,'contract':self.family_contract_version,'kind':kind,'filters':f};last=_decode(cursor,scope);args=[]
  if kind=='files':table='talk_physical_file';where=[]
  elif kind=='references':table='talk_physical_reference';where=[]
  elif kind=='candidates':table='talk_physical_candidate';where=[]
  elif kind=='raw_body_chunks':
   if set(f)-{'record_kind','container_id'} or 'container_id' not in f:raise ValueError('raw chunks require exact container_id')
   row=self.conn.execute("select id,source_path,source_sha256,size_bytes,raw_bytes from talk_physical_file where id=? and run_id=?",(f['container_id'],self.run)).fetchone()
   if not row:return self._missing(ingest_run_id)
   if row['raw_bytes'] is None:raise ValueError('mapped program body is reused; follow exact target locator')
   if last is None:offset=0
   else:
    try:offset=int(last)
    except (TypeError,ValueError):raise ValueError('invalid raw chunk cursor offset')
    if str(offset)!=last or offset<=0 or offset>=len(row['raw_bytes']) or offset%CHUNK:raise ValueError('invalid raw chunk cursor offset')
   raw=row['raw_bytes'];end=min(len(raw),offset+CHUNK);item={'container_id':row['id'],'source_path':row['source_path'],'source_sha256':row['source_sha256'],'encoding':'base64_raw_bytes','offset':offset,'end':end,'total_bytes':len(raw),'raw_base64':base64.b64encode(raw[offset:end]).decode(),'partial':end<len(raw)};return self._result({'record_kind':kind,'items':[item],'delivered_count':1,'total':(len(raw)+CHUNK-1)//CHUNK,'next_cursor':None if end==len(raw) else _cursor(scope,str(end)),'scope_complete':end==len(raw),'partial':True})
  else:raise ValueError('record_kind')
  where=[]
  if last is not None:where.append('id>?');args.append(last)
  if kind=='files' and 'classification' in f:where.append('classification=?');args.append(f['classification'])
  if kind=='files' and 'shape' in f:where.append('shape=?');args.append(f['shape'])
  if kind=='references' and 'container_id' in f:where.append('file_id=?');args.append(f['container_id'])
  if kind=='candidates' and 'reference_id' in f:where.append('reference_id=?');args.append(f['reference_id'])
  if set(f)-({'record_kind','classification','shape'} if kind=='files' else {'record_kind','container_id'} if kind=='references' else {'record_kind','reference_id'}):raise ValueError('filter scope')
  columns='*' if kind!='files' else "id,run_id,source_path,source_blob_oid,source_sha256,size_bytes,root_keys_json,classification,shape,target_family,target_contract,target_run,target_occurrence_id"
  rows=[dict(x) for x in self.conn.execute('select '+columns+' from '+table+(' where '+' and '.join(where) if where else '')+' order by id limit ?',(*args,limit+1))];more=len(rows)>limit;rows=rows[:limit]
  if not rows:return self._result({'record_kind':kind,'items':[],'delivered_count':0,'next_cursor':None,'scope_complete':True,'partial':False})
  while rows:
   data={'record_kind':kind,'items':rows,'delivered_count':len(rows),'next_cursor':_cursor(scope,rows[-1]['id']) if more else None,'scope_complete':not more,'partial':more}
   result=self._result(data)
   if wire_size(result)<=BUDGET:return result
   rows.pop();more=True
  raise ValueError('single record exceeds inline limit; use exact get; no truncation')
 def get_evidence(self,*,ingest_run_id,family_contract_version,occurrence_id):
  if not self._check(ingest_run_id,family_contract_version):return self._missing(ingest_run_id)
  row=self.conn.execute("select id,run_id,source_path,source_blob_oid,source_sha256,size_bytes,root_keys_json,classification,shape,target_family,target_contract,target_run,target_occurrence_id,raw_bytes is not null as raw_available from talk_physical_file where id=?",(occurrence_id,)).fetchone()
  if row:
   d=dict(row);d['raw_body_delivery']={'record_kind':'raw_body_chunks','container_id':occurrence_id,'chunk_bytes':CHUNK,'available':bool(d.pop('raw_available'))};d['reference_count']=self.conn.execute('select count(*) from talk_physical_reference where file_id=?',(occurrence_id,)).fetchone()[0];d['partial']=True;return self._result(d)
  ref=self.conn.execute('select * from talk_physical_reference where id=?',(occurrence_id,)).fetchone()
  if ref:return self._result({'reference':dict(ref),'candidates':[dict(x) for x in self.conn.execute('select * from talk_physical_candidate where reference_id=? order by id',(occurrence_id,))],'single_physical_container':True})
  return self._missing(ingest_run_id)
 def search_text(self,**kwargs):return self._missing(kwargs['ingest_run_id'])
