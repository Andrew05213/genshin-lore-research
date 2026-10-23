"""Deterministic explicit-adapter research projection over accepted evidence."""
from __future__ import annotations
import hashlib,json,sqlite3,time
from itertools import islice
from pathlib import Path
from typing import Any
from .research_adapters import CONTAINER_ONLY,DIRECT_TEXT,RELATIONS,ROW_FAMILIES,CORE_PROJECTED_PREFIXES,CORE_SPECIAL,QUEST_TALK,UNSUPPORTED_RELATION_TABLES,declared_relation_tables,relation_like_tables

SCHEMA='''
PRAGMA journal_mode=OFF; PRAGMA synchronous=FULL;
CREATE TABLE corpus_release(id INTEGER PRIMARY KEY CHECK(id=1),manifest_sha256 TEXT NOT NULL,corpus_ref TEXT NOT NULL,adapter_registry TEXT NOT NULL,built_at TEXT NOT NULL,source_count INTEGER NOT NULL,container_count INTEGER NOT NULL,text_count INTEGER NOT NULL,relation_count INTEGER NOT NULL,candidate_count INTEGER NOT NULL);
CREATE TABLE source_artifact(source_key TEXT PRIMARY KEY,artifact_family TEXT NOT NULL,artifact_contract TEXT NOT NULL,artifact_parser TEXT NOT NULL,artifact_run TEXT NOT NULL,path TEXT NOT NULL,size_bytes INTEGER NOT NULL,sha256 TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE adapter_coverage(source_key TEXT NOT NULL REFERENCES source_artifact(source_key),adapter_id TEXT NOT NULL,projection_kind TEXT NOT NULL,source_table TEXT NOT NULL,denominator INTEGER NOT NULL,indexed_count INTEGER NOT NULL,status TEXT NOT NULL CHECK(status IN ('complete','unsupported','not_declared','duplicate_projection')),detail TEXT NOT NULL,PRIMARY KEY(source_key,adapter_id,projection_kind,source_table)) WITHOUT ROWID;
CREATE TABLE research_container(id INTEGER PRIMARY KEY,source_key TEXT NOT NULL REFERENCES source_artifact(source_key),adapter_id TEXT NOT NULL,evidence_family TEXT NOT NULL,evidence_contract TEXT NOT NULL,evidence_parser TEXT NOT NULL,evidence_run TEXT NOT NULL,occurrence_id TEXT NOT NULL,container_kind TEXT NOT NULL,source_table TEXT NOT NULL,pk_column TEXT NOT NULL,pk_value TEXT NOT NULL,source_path TEXT NOT NULL,source_pointer TEXT NOT NULL,source_file_sha256 TEXT NOT NULL,source_git_blob TEXT,ordinal INTEGER,ordering_basis TEXT,UNIQUE(source_key,source_table,pk_value),UNIQUE(source_key,evidence_family,evidence_contract,evidence_parser,evidence_run,occurrence_id));
CREATE INDEX research_container_identity ON research_container(evidence_family,evidence_contract,evidence_run,occurrence_id); CREATE INDEX research_container_path ON research_container(source_key,source_path,source_pointer);
CREATE TABLE research_text(id INTEGER PRIMARY KEY,container_id INTEGER NOT NULL REFERENCES research_container(id),source_key TEXT NOT NULL,adapter_id TEXT NOT NULL,evidence_family TEXT NOT NULL,evidence_contract TEXT NOT NULL,evidence_parser TEXT NOT NULL,evidence_run TEXT NOT NULL,occurrence_id TEXT NOT NULL,child_kind TEXT NOT NULL,child_id TEXT NOT NULL,source_table TEXT NOT NULL,pk_column TEXT NOT NULL,pk_value TEXT NOT NULL,text_column TEXT NOT NULL,locale TEXT,owner_field TEXT,ordinal INTEGER,pointer TEXT NOT NULL,text_source_path TEXT NOT NULL,text_source_pointer TEXT NOT NULL,text_source_file_sha256 TEXT NOT NULL,text_source_git_blob TEXT,physical_pair_ordinal INTEGER,raw_text TEXT NOT NULL,text_sha256 TEXT NOT NULL,UNIQUE(source_key,source_table,pk_value,text_column));
CREATE INDEX research_text_scope ON research_text(evidence_family,evidence_run,id); CREATE INDEX research_text_container ON research_text(container_id,ordinal,id); CREATE INDEX research_text_locale ON research_text(locale,id);
CREATE VIRTUAL TABLE research_text_fts USING fts5(raw_text,content='research_text',content_rowid='id',tokenize='trigram case_sensitive 1');
CREATE TABLE research_relation(id INTEGER PRIMARY KEY,container_id INTEGER NOT NULL REFERENCES research_container(id),source_key TEXT NOT NULL,adapter_id TEXT NOT NULL,source_table TEXT NOT NULL,pk_column TEXT NOT NULL,pk_value TEXT NOT NULL,relation_kind TEXT NOT NULL,source_pointer TEXT NOT NULL,raw_locator TEXT,status TEXT NOT NULL,search_complete INTEGER NOT NULL,candidate_count INTEGER NOT NULL,candidate_namespace TEXT,UNIQUE(source_key,source_table,pk_value));
CREATE INDEX research_relation_source ON research_relation(container_id,relation_kind,id);
CREATE TABLE research_candidate(id INTEGER PRIMARY KEY,relation_id INTEGER NOT NULL REFERENCES research_relation(id),source_table TEXT NOT NULL,pk_column TEXT NOT NULL,pk_value TEXT NOT NULL,target_family TEXT,target_contract TEXT,target_parser TEXT,target_run TEXT,target_occurrence_id TEXT,target_source_path TEXT,target_pointer TEXT,raw_target TEXT,UNIQUE(relation_id,source_table,pk_value));
CREATE INDEX research_candidate_target ON research_candidate(target_family,target_contract,target_run,target_occurrence_id,id);
'''


_CONTAINER_INSERT='insert into research_container values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)'
_TEXT_INSERT='insert into research_text values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)'

def _with_containers(out,rows,container_id,*,row_limit=512):
 """Attach exact output parents in bounded reads, preserving source order."""
 if not 1<=row_limit<=512:raise ValueError('Research parent batch limit must be 1..512')
 rows=iter(rows)
 while batch:=list(islice(rows,row_limit)):
  ids=[container_id(row) for row in batch]
  unique=tuple(dict.fromkeys(ids))
  parents={row['id']:row for row in out.execute(
   'SELECT * FROM research_container WHERE id IN ('+','.join('?' for _ in unique)+')',unique)}
  missing=set(unique)-parents.keys()
  if missing:raise ValueError('Research text parent container missing: '+','.join(map(str,sorted(missing))))
  for row,cid in zip(batch,ids):yield row,parents[cid]

def sha256_file(path:Path)->str:
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(8<<20),b''):h.update(b)
 return h.hexdigest()
def _tables(db):return {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
def _cols(db,t):return {r[1] for r in db.execute(f'pragma table_info("{t}")')}
def _prov(db,r0,path,sha=None,blob=None):
 path=str(path or '')
 if sha:return str(sha),None if blob is None else str(blob)
 if path in r0:return r0[path]
 raise ValueError('source provenance unavailable: '+path)
def _identity(c,row=None):
 if row is not None and all(k in row.keys() for k in ('family','family_contract_version','parser_build_id','ingest_run_id')):return tuple(str(row[k]) for k in ('family','family_contract_version','parser_build_id','ingest_run_id'))
 return c['family'],c['contract'],c['parser'],c['run']
def _container_values(n,key,adapter,ident,occ,kind,table,pk,path,ptr,sha,blob,ordinal=None,basis=None,pkcol='id'):
 return (n,key,adapter,*ident,str(occ),str(kind),table,pkcol,str(pk),str(path or ''),str(ptr or '/'),sha,blob,ordinal,basis)

def _container(out,n,*args,**kwargs):
 out.execute(_CONTAINER_INSERT,_container_values(n,*args,**kwargs));return n

def _text_values(n,cid,key,adapter,ident,occ,ckind,child,table,pk,locale,owner,ordinal,ptr,path,tptr,sha,blob,pair,raw,pkcol='id'):
 raw=str(raw)
 return (n,cid,key,adapter,*ident,str(occ),ckind,str(child),table,pkcol,str(pk),'raw_text',locale,owner,ordinal,str(ptr or tptr or ''),str(path or ''),str(tptr or ''),sha,blob,pair,raw,hashlib.sha256(raw.encode()).hexdigest())

def _text(out,n,*args,**kwargs):
 out.execute(_TEXT_INSERT,_text_values(n,*args,**kwargs));return n

def _component(src,out,key,c,r0,cseq,tseq):
 fam=c['family'];mapping={};tables=_tables(src)
 if fam in ROW_FAMILIES:s=ROW_FAMILIES[fam];parent=s['parent']
 elif fam in CONTAINER_ONLY:s=CONTAINER_ONLY[fam];parent=s['parent']
 elif fam in DIRECT_TEXT:s=DIRECT_TEXT[fam];parent=s['parent']
 elif fam=='codexquest_archive':s={'kind':'codexquest'};parent='codexquest_container'
 elif fam=='quest_talk_pilot':s=QUEST_TALK;parent=s['parent']
 else:return cseq,tseq,mapping
 cols=_cols(src,parent)
 for row in src.execute(f'select * from {parent} order by id'):
  path=row['source_path'] if 'source_path' in cols else None;ptr=row['pointer'] if 'pointer' in cols else row['source_pointer'] if 'source_pointer' in cols else '/';sha=row['source_sha256'] if 'source_sha256' in cols else row['source_file_sha256'] if 'source_file_sha256' in cols else None;blob=row['git_blob'] if 'git_blob' in cols else row['source_blob_oid'] if 'source_blob_oid' in cols else None
  if fam=='quest_talk_pilot':ptr,path,sha,blob=src.execute('select r.pointer,f.relative_path,f.sha256,f.upstream_blob_oid from source_record r join source_file f on f.id=r.source_file_id where r.id=?',(row['source_record_id'],)).fetchone()
  sha,blob=_prov(src,r0,path,sha,blob);cseq+=1;kind=row['kind'] if 'kind' in cols else row['container_kind'] if 'container_kind' in cols else s['kind'];ordinal=row['ordinal'] if 'ordinal' in cols else row['source_ordinal'] if 'source_ordinal' in cols else None
  mapping[str(row['id'])]=_container(out,cseq,key,f'{fam}:{parent}',_identity(c),row['id'],kind,parent,row['id'],path,ptr,sha,blob,ordinal,'physical_node_ordinal' if fam=='quest_talk_pilot' else ('source_ordinal' if ordinal is not None else None))
 out.execute('insert into adapter_coverage values(?,?,?,?,?,?,?,?)',(key,f'{fam}:{parent}','container',parent,len(mapping),len(mapping),'complete','explicit container adapter'))
 before=tseq
 if fam in ROW_FAMILIES:
  s=ROW_FAMILIES[fam];pc=_cols(src,s['parent']);tc=_cols(src,s['text']);extra=',p.source_file_sha256 parent_sha,p.source_blob_oid parent_blob' if 'source_file_sha256' in pc else ',NULL parent_sha,NULL parent_blob'
  sql=f'''select t.*,s.{s['parent_fk']} parent_id,s.{s['owner']} owner,s.locale slot_locale,p.source_path parent_path{extra} from {s['text']} t join {s['slot']} s on s.id=t.slot_id join {s['parent']} p on p.id=s.{s['parent_fk']} order by t.id'''
  for row in src.execute(sql):
   if row['raw_text'] is None:continue
   path=row['source_path'] if 'source_path' in tc else row['parent_path'];sha=row['source_file_sha256'] if 'source_file_sha256' in tc else None;blob=row['source_blob_oid'] if 'source_blob_oid' in tc else None;sha,blob=_prov(src,r0,path,sha,blob);tseq+=1
   ptr=row['pointer'] if 'pointer' in tc else row['source_pointer'];pair=row['pair_ordinal'] if 'pair_ordinal' in tc else None
   _text(out,tseq,mapping[str(row['parent_id'])],key,f"{fam}:{s['text']}",_identity(c),row['parent_id'],'text',row['id'],s['text'],row['id'],row['slot_locale'],row['owner'],pair,ptr,path,ptr,sha,blob,pair,row['raw_text'])
  text_table=s['text']
 elif fam in DIRECT_TEXT:
  s=DIRECT_TEXT[fam];text_table=s['text']
  for row in src.execute(f'''select t.*,f.source_path,f.source_sha256,f.git_blob from {text_table} t join {s['parent']} f on f.id=t.{s['fk']} order by t.id'''):
   raw=row['raw_text'] if 'raw_text' in row.keys() else row['raw_cue_text']
   if raw is None:continue
   bits=row['source_path'].split('/');known=('CHS','CHT','DE','EN','ES','FR','ID','IT','JP','KR','PT','RU','TH','TR','VI');locale=bits[1] if len(bits)>2 and bits[1] in known else None;tseq+=1
   _text(out,tseq,mapping[str(row[s['fk']])],key,f'{fam}:{text_table}',_identity(c),row[s['fk']],s['child_kind'],row['id'],text_table,row['id'],locale,'body' if fam=='readable_archive' else 'cue_text',row['ordinal'],row['pointer'],row['source_path'],row['pointer'],row['source_sha256'],row['git_blob'],None,raw)
 elif fam=='codexquest_archive':
  text_table='codexquest_text_candidate';sql='select c.id,s.container_id,s.owner_field,o.* from codexquest_text_candidate c join codexquest_text_slot s on s.id=c.slot_id join codexquest_text_occurrence o on o.id=c.text_occurrence_id order by c.id'
  for row in src.execute(sql):
   sha,blob=_prov(src,r0,row['source_path'],None,row['source_blob_oid']);tseq+=1;_text(out,tseq,mapping[str(row['container_id'])],key,'codexquest:text_candidate',_identity(c),row['container_id'],'text_candidate',row['id'],text_table,row['id'],row['locale'],row['owner_field'],row['source_ordinal'],row['source_pointer'],row['source_path'],row['source_pointer'],sha,blob,row['source_ordinal'],row['raw_text'])
 elif fam=='quest_talk_pilot':
  text_table=s['text'];sql=f"select t.*,n.ordinal node_ordinal,n.pointer node_pointer from {s['text']} t join {s['node']} n on n.id=t.node_id where t.raw_text is not null order by t.id"
  for row in src.execute(sql):
   loc=json.loads(row['locator_json']);item=loc[0] if isinstance(loc,list) and loc else {};path=item.get('path','');ptr=item.get('pointer',row['id']);sha,blob=_prov(src,r0,path);tseq+=1
   _text(out,tseq,mapping[str(row['container_id'])],key,'quest_talk:physical_text',_identity(c),row['container_id'],'talk_node_text',row['id'],text_table,row['id'],row['locale'],'talk_text',row['node_ordinal'],row['node_pointer'],path,ptr,sha,blob,item.get('pair_ordinal'),row['raw_text'])
 else:return cseq,tseq,mapping
 denom=src.execute(f"select count(*) from {text_table}"+(" where raw_text is not null" if 'raw_text' in _cols(src,text_table) else '')).fetchone()[0]
 out.execute('insert into adapter_coverage values(?,?,?,?,?,?,?,?)',(key,f'{fam}:{text_table}','text',text_table,denom,tseq-before,'complete','all declared non-null text rows adapted'))
 return cseq,tseq,mapping

def _run_identity(src,run,fallback):
 ir=src.execute('select parser_build_id,scope_json from ingest_run where id=?',(run,)).fetchone();scope=json.loads(ir[1]);contract=src.execute('select parser_version from parser_build where id=?',(ir[0],)).fetchone()[0];return str(scope.get('family',fallback)),str(contract),str(ir[0]),str(run)
def _core(src,out,key,c,r0,cseq,tseq):
 tables=_tables(src);containers={}
 def add(table,fixed=None):
  nonlocal cseq
  cols=_cols(src,table);count=0
  def parents():
   nonlocal cseq,count
   for row in src.execute(f"select * from {table} order by {'occurrence_id' if 'occurrence_id' in cols else 'id'}"):
    pk=row['occurrence_id'] if 'occurrence_id' in cols else row['id'];ident=fixed or _identity(c,row);path=row['source_file_path'] if 'source_file_path' in cols else None;ptr=row['source_pointer'] if 'source_pointer' in cols else row['source_record_pointer'] if 'source_record_pointer' in cols else '/';sha=blob=None
    if path is None and 'source_record_id' in cols:
     q=src.execute('select r.pointer,f.relative_path,f.sha256,f.upstream_blob_oid from source_record r join source_file f on f.id=r.source_file_id where r.id=?',(row['source_record_id'],)).fetchone()
     if q:ptr,path,sha,blob=q
    sha,blob=_prov(src,r0,path,sha,blob);cseq+=1;count+=1;containers[(table,str(pk))]=cseq
    yield _container_values(cseq,key,f'schema20:{table}',ident,pk,table,table,pk,path,ptr,sha,blob,row['source_ordinal'] if 'source_ordinal' in cols else row['row_index'] if 'row_index' in cols else None,'source_ordinal' if 'source_ordinal' in cols else None,'occurrence_id' if 'occurrence_id' in cols else 'id')
  out.executemany(_CONTAINER_INSERT,parents())
  out.execute('insert into adapter_coverage values(?,?,?,?,?,?,?,?)',(key,f'schema20:{table}','container',table,count,count,'complete','identity from authoritative row/run metadata'))
 for prefix in CORE_PROJECTED_PREFIXES:
  if prefix+'_row_occurrence' in tables:add(prefix+'_row_occurrence')
 for fam,s in CORE_SPECIAL.items():
  rt=s['row']
  if rt not in tables:continue
  runs=[x[0] for x in src.execute(f'select distinct ingest_run_id from {rt}')]
  if len(runs)==1:add(rt,_run_identity(src,runs[0],fam))
  else:
   # multi-run character rows are inserted independently to preserve exact run identity
   cols=_cols(src,rt);count=0
   def parents():
    nonlocal cseq,count
    for row in src.execute(f'select * from {rt} order by id'):
     ident=_run_identity(src,row['ingest_run_id'],fam);ptr,path,sha,blob=src.execute('select r.pointer,f.relative_path,f.sha256,f.upstream_blob_oid from source_record r join source_file f on f.id=r.source_file_id where r.id=?',(row['source_record_id'],)).fetchone();cseq+=1;count+=1;containers[(rt,str(row['id']))]=cseq
     yield _container_values(cseq,key,f'schema20:{rt}',ident,row['id'],rt,rt,row['id'],path,ptr,sha,blob,row['row_index'] if 'row_index' in cols else None,'source_row_index')
   out.executemany(_CONTAINER_INSERT,parents())
   out.execute('insert into adapter_coverage values(?,?,?,?,?,?,?,?)',(key,f'schema20:{rt}','container',rt,count,count,'complete','multi-run identity from ingest_run/parser_build'))
 # These legacy accepted tables predate row-level family columns. Their exact
 # family/contract/parser/run is recovered from each sealed ingest_run.
 if 'passage_occurrence' in tables:
  rt='passage_occurrence';count=0
  def parents():
   nonlocal cseq,count
   for row in src.execute('select * from passage_occurrence order by passage_occurrence_id'):
    ident=_run_identity(src,row['ingest_run_id'],'quest_corpus_evidence');_record_ptr,path,sha,blob=src.execute('select r.pointer,f.relative_path,f.sha256,f.upstream_blob_oid from source_record r join source_file f on f.id=r.source_file_id where r.id=?',(row['source_record_id'],)).fetchone();ptr=row['source_pointer'];cseq+=1;count+=1;containers[(rt,str(row['passage_occurrence_id']))]=cseq
    yield _container_values(cseq,key,'schema20:passage_occurrence',ident,row['passage_occurrence_id'],'passage',rt,row['passage_occurrence_id'],path,ptr,sha,blob,None,'passage occurrence','passage_occurrence_id')
  out.executemany(_CONTAINER_INSERT,parents())
  out.execute('insert into adapter_coverage values(?,?,?,?,?,?,?,?)',(key,'schema20:passage_occurrence','container',rt,count,count,'complete','exact quest passage run identity'))
 if 'static_evidence_container' in tables:
  rt='static_evidence_container';count=0
  def parents():
   nonlocal cseq,count
   for row in src.execute('select * from static_evidence_container order by id'):
    ident=_run_identity(src,row['ingest_run_id'],row['source_family']);ptr,path,sha,blob=src.execute('select r.pointer,f.relative_path,f.sha256,f.upstream_blob_oid from source_record r join source_file f on f.id=r.source_file_id where r.id=?',(row['source_record_id'],)).fetchone();cseq+=1;count+=1;containers[(rt,str(row['id']))]=cseq
    yield _container_values(cseq,key,'schema20:static_evidence_container',ident,row['id'],row['container_kind'],rt,row['id'],path,ptr,sha,blob,None,'file_local_ordinal for readable segments')
  out.executemany(_CONTAINER_INSERT,parents())
  out.execute('insert into adapter_coverage values(?,?,?,?,?,?,?,?)',(key,'schema20:static_evidence_container','container',rt,count,count,'complete','exact static run identity'))
 # modern exact-family candidates
 for prefix in CORE_PROJECTED_PREFIXES:
  ct=prefix+'_text_candidate_occurrence';ot=prefix+'_text_owner_occurrence';rt=prefix+'_row_occurrence'
  if not {ct,ot,rt}<=tables:continue
  cc=_cols(src,ct);oc=_cols(src,ot);pk='candidate_id' if 'candidate_id' in cc else 'occurrence_id';before=tseq
  if 'owner_id' in cc:sql=f"select c.*,o.row_occurrence_id,o.{'field_name' if 'field_name' in oc else 'field_path'} owner_field from {ct} c join {ot} o on o.{'owner_id' if 'owner_id' in oc else 'occurrence_id'}=c.owner_id and o.ingest_run_id=c.ingest_run_id order by c.{pk}"
  else:
   res=prefix+'_text_resolution_occurrence';sql=f"select c.*,o.row_occurrence_id,o.{'field_name' if 'field_name' in oc else 'field_path'} owner_field from {ct} c join {res} z on z.occurrence_id=c.resolution_occurrence_id join {ot} o on o.occurrence_id=z.owner_occurrence_id order by c.{pk}"
  def texts():
   nonlocal tseq
   for row,cr in _with_containers(out,src.execute(sql),lambda r:containers[(rt,str(r['row_occurrence_id']))]):
    cid=cr['id'];path=row['relative_path'];sha,blob=_prov(src,r0,path);tseq+=1
    yield _text_values(tseq,cid,key,f'schema20:{ct}',tuple(cr[3:7]),row['row_occurrence_id'],'text_candidate',row[pk],ct,row[pk],row['locale'],row['owner_field'],row['pair_ordinal'],row['pointer'],path,row['pointer'],sha,blob,row['pair_ordinal'],row['raw_text'],pk)
  out.executemany(_TEXT_INSERT,texts())
  n=src.execute(f'select count(*) from {ct}').fetchone()[0];out.execute('insert into adapter_coverage values(?,?,?,?,?,?,?,?)',(key,f'schema20:{ct}','text',ct,n,tseq-before,'complete','explicit owner-to-row join'))
 # legacy special candidates
 for fam,s in CORE_SPECIAL.items():
  ct=s.get('text');rt=s['row']
  if not ct or ct not in tables:continue
  if ct.startswith(('quest_codex','world_archive','tutorial')):
   p=ct.rsplit('_text_candidate_occurrence',1)[0];sql=f'select c.*,o.source_row_occurrence_id row_occurrence_id,o.field_name owner_field from {ct} c join {p}_text_resolution_occurrence z on z.id=c.resolution_occurrence_id join {p}_text_owner_occurrence o on o.id=z.owner_occurrence_id order by c.id'
  else:sql='select c.*,z.source_row_occurrence_id row_occurrence_id,z.source_pointer owner_field from character_text_candidate_occurrence c join character_text_resolution_occurrence z on z.id=c.resolution_occurrence_id order by c.id'
  before=tseq
  def texts():
   nonlocal tseq
   for row,cr in _with_containers(out,src.execute(sql),lambda r:containers[(rt,str(r['row_occurrence_id']))]):
    cid=cr['id'];path,sha,blob=src.execute('select relative_path,sha256,upstream_blob_oid from source_file where id=?',(row['source_file_id'],)).fetchone();tseq+=1
    yield _text_values(tseq,cid,key,f'schema20:{ct}',tuple(cr[3:7]),row['row_occurrence_id'],'text_candidate',row['id'],ct,row['id'],row['locale'],row['owner_field'],None,row['source_pointer'],path,row['source_pointer'],sha,blob,None,row['raw_text'])
  out.executemany(_TEXT_INSERT,texts())
  n=src.execute(f'select count(*) from {ct}').fetchone()[0];out.execute('insert into adapter_coverage values(?,?,?,?,?,?,?,?)',(key,f'schema20:{ct}','text',ct,n,tseq-before,'complete','explicit legacy owner-to-row join'))
 # Quest passage text is held directly by the passage occurrence.
 if 'passage_occurrence_text' in tables:
  ct='passage_occurrence_text';before=tseq
  def texts():
   nonlocal tseq
   for row,cr in _with_containers(out,src.execute('select * from passage_occurrence_text order by id'),lambda r:containers[('passage_occurrence',str(r['passage_occurrence_id']))]):
    cid=cr['id'];path,sha,blob=src.execute('select f.relative_path,f.sha256,f.upstream_blob_oid from source_record r join source_file f on f.id=r.source_file_id where r.id=?',(row['source_record_id'],)).fetchone();tseq+=1
    yield _text_values(tseq,cid,key,'schema20:passage_occurrence_text',tuple(cr[3:7]),row['passage_occurrence_id'],'passage_text',row['id'],ct,row['id'],row['language'],'passage_text',None,cr['source_pointer'],path,cr['source_pointer'],sha,blob,None,row['raw_text'])
  out.executemany(_TEXT_INSERT,texts())
  n=src.execute('select count(*) from passage_occurrence_text').fetchone()[0];out.execute('insert into adapter_coverage values(?,?,?,?,?,?,?,?)',(key,'schema20:passage_occurrence_text','text',ct,n,tseq-before,'complete','exact passage occurrence text'))
 # Static readable segments and TextMap candidates are separate physical text
 # occurrences attached to the same accepted static container namespace.
 if 'static_segment_occurrence' in tables:
  ct='static_segment_occurrence';before=tseq
  def texts():
   nonlocal tseq
   for row,cr in _with_containers(out,src.execute('select * from static_segment_occurrence order by id'),lambda r:containers[('static_evidence_container',str(r['evidence_container_id']))]):
    cid=cr['id'];tseq+=1
    yield _text_values(tseq,cid,key,'schema20:static_segment_occurrence',tuple(cr[3:7]),row['evidence_container_id'],'readable_segment',row['id'],ct,row['id'],row['locale'],'readable_segment',row['file_local_ordinal'],row['source_pointer'],cr['source_path'],row['source_pointer'],cr['source_file_sha256'],cr['source_git_blob'],None,row['raw_text'])
  out.executemany(_TEXT_INSERT,texts())
  n=src.execute('select count(*) from static_segment_occurrence').fetchone()[0];out.execute('insert into adapter_coverage values(?,?,?,?,?,?,?,?)',(key,'schema20:static_segment_occurrence','text',ct,n,tseq-before,'complete','exact readable segment occurrence'))
 if 'static_text_candidate_occurrence' in tables:
  ct='static_text_candidate_occurrence';before=tseq
  sql='select c.*,z.source_container_id from static_text_candidate_occurrence c join static_text_resolution_occurrence z on z.id=c.resolution_occurrence_id order by c.id'
  def texts():
   nonlocal tseq
   for row,cr in _with_containers(out,src.execute(sql),lambda r:containers[('static_evidence_container',str(r['source_container_id']))]):
    cid=cr['id'];path,sha,blob=src.execute('select relative_path,sha256,upstream_blob_oid from source_file where id=?',(row['source_file_id'],)).fetchone();tseq+=1
    yield _text_values(tseq,cid,key,'schema20:static_text_candidate_occurrence',tuple(cr[3:7]),row['source_container_id'],'text_candidate',row['id'],ct,row['id'],row['locale'],'static_text_candidate',None,row['source_pointer'],path,row['source_pointer'],sha,blob,None,row['raw_text'])
  out.executemany(_TEXT_INSERT,texts())
  n=src.execute('select count(*) from static_text_candidate_occurrence').fetchone()[0];out.execute('insert into adapter_coverage values(?,?,?,?,?,?,?,?)',(key,'schema20:static_text_candidate_occurrence','text',ct,n,tseq-before,'complete','exact TextMap candidate occurrence'))
 adapted={r[0] for r in out.execute("select source_table from adapter_coverage where source_key=? and projection_kind='text'",(key,))}
 for table in sorted(tables-adapted-{'textmap_physical_occurrence'}):
  if 'raw_text' in _cols(src,table):
   n=src.execute(f'select count(*) from {table} where raw_text is not null').fetchone()[0]
   if n:
    duplicate=table.endswith('_text_resolution_occurrence') or table in ('codexquest_text_occurrence','codexquest_body_chunk','character_lore_reuse_candidate')
    out.execute('insert into adapter_coverage values(?,?,?,?,?,?,?,?)',(key,f'schema20:{"duplicate" if duplicate else "unsupported"}:{table}','text',table,n,0,'duplicate_projection' if duplicate else 'unsupported','represented by exact candidate/body projection' if duplicate else 'no explicit evidence-identity/container adapter'))
 return cseq,tseq

def _relations(src,out,key,c,containers,rseq,kseq):
 fam=c['family']
 for s in RELATIONS.get(fam,()):
  if s['table'] not in _tables(src):continue
  cols=_cols(src,s['table']);rb=rseq;kb=kseq;relation_denominator=src.execute(f"select count(*) from {s['table']}").fetchone()[0]
  for row in src.execute(f"select * from {s['table']} order by id"):
   cid=containers.get(str(row[s['parent']]))
   if cid is None:continue
   kc=s.get('kind_col','relation_kind');kind=row[kc] if kc in cols else s.get('kind','relation');ptr=row[s.get('pointer','pointer')];raw=row[s.get('raw','pointer')];complete=row['search_complete'] if 'search_complete' in cols else s.get('complete_literal',0);ns=row[s['namespace']] if s.get('namespace') in cols else s.get('namespace_literal');rseq+=1
   out.execute('insert into research_relation values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(rseq,cid,key,f"{fam}:{s['table']}",s['table'],'id',str(row['id']),str(kind),str(ptr),None if raw is None else str(raw),str(row['status']),int(complete),int(row['candidate_count']),ns))
   if s['candidate'] not in _tables(src):continue
   for x in src.execute(f"select * from {s['candidate']} where relation_id=? order by id",(row['id'],)):
    xc=set(x.keys());mode=s.get('target_mode');tf=tc=tp=tr=to=tpath=tptr=rawtarget=None
    if mode=='exact':tf=x['target_family'] if 'target_family' in xc else None;tc=x['target_contract'] if 'target_contract' in xc else None;tp=x['target_parser'] if 'target_parser' in xc else x['target_parser_build_id'] if 'target_parser_build_id' in xc else None;tr=x['target_run'] if 'target_run' in xc else x['target_run_id'] if 'target_run_id' in xc else None;to=x['target_occurrence_id'] if 'target_occurrence_id' in xc else None;tpath=x['target_source_path'] if 'target_source_path' in xc else None;tptr=x['target_source_pointer'] if 'target_source_pointer' in xc else x['target_pointer'] if 'target_pointer' in xc else None
    elif mode=='local_row':tf,tc,tp,tr=c['family'],c['contract'],c['parser'],c['run'];to=x['target_row_id'];tpath=x['target_source_path'] if 'target_source_path' in xc else None;tptr=x['target_pointer'] if 'target_pointer' in xc else None
    elif mode=='readable_path':tf='readable_archive';tpath=x['target_source_path'];tptr=x['target_pointer']
    elif mode=='raw_npc_id':tf='npc_archive';rawtarget=str(x['npc_id'])
    else:tf,tc,tp,tr=c['family'],c['contract'],c['parser'],c['run'];to=x['target_row_id'] if 'target_row_id' in xc else None
    kseq+=1;out.execute('insert into research_candidate values(?,?,?,?,?,?,?,?,?,?,?,?,?)',(kseq,rseq,s['candidate'],'id',str(x['id']),tf,tc,tp,tr,None if to is None else str(to),tpath,tptr,rawtarget))
  relation_indexed=rseq-rb;out.execute('insert into adapter_coverage values(?,?,?,?,?,?,?,?)',(key,f"{fam}:{s['table']}",'relation',s['table'],relation_denominator,relation_indexed,'complete' if relation_denominator==relation_indexed else 'unsupported','explicit relation adapter; source parents outside indexed container namespace remain unsupported'))
  n=src.execute(f"select count(*) from {s['candidate']}").fetchone()[0] if s['candidate'] in _tables(src) else 0;status='complete' if kseq-kb==n else 'unsupported';out.execute('insert into adapter_coverage values(?,?,?,?,?,?,?,?)',(key,f"{fam}:{s['candidate']}",'candidate',s['candidate'],n,kseq-kb,status,'exact target fields preserved; raw-only target stays unresolved'))
 return rseq,kseq

def _record_unsupported_relations(src,out,key,family):
 actual=relation_like_tables(src,family);declared=declared_relation_tables(family)
 unknown=actual-declared
 if unknown:raise ValueError('relation-like tables absent from explicit adapter registry: '+','.join(sorted(unknown)))
 for table in UNSUPPORTED_RELATION_TABLES.get(family,()):
  if table not in _tables(src):continue
  n=src.execute(f'select count(*) from "{table}"').fetchone()[0]
  kind='candidate' if 'candidate' in table or table.endswith('_candidate') else 'relation'
  out.execute('insert into adapter_coverage values(?,?,?,?,?,?,?,?)',(key,f'{family}:unsupported:{table}',kind,table,n,0,'unsupported','explicitly inventoried; no safe exact-coordinate research adapter'))

def build_index(corpus_manifest:Path,bindings_path:Path,output:Path)->dict[str,Any]:
 started=time.perf_counter();m=json.loads(corpus_manifest.read_text());b=json.loads(bindings_path.read_text())
 if output.exists():raise FileExistsError(output)
 pp=Path(b['provenance_artifact']);pid=m['provenance_dependency']
 if pp.stat().st_size!=pid['size_bytes'] or sha256_file(pp)!=pid['sha256']:raise ValueError('R0 provenance dependency drift')
 p=sqlite3.connect(f'file:{pp}?mode=ro&immutable=1',uri=True);r0={str(a):(str(s),None) for a,s in p.execute('select relative_path,source_sha256 from source_file')};p.close();out=sqlite3.connect(output);out.row_factory=sqlite3.Row;out.executescript(SCHEMA);cs=ts=rs=ks=0
 try:
  for c in m['components']:
   key=c['source_key'];path=Path(b['artifacts'][key]);st=path.stat()
   if st.st_size!=c['artifact']['size_bytes'] or sha256_file(path)!=c['artifact']['sha256']:raise ValueError('artifact drift: '+key)
   out.execute('insert into source_artifact values(?,?,?,?,?,?,?,?)',(key,c['family'],c['contract'],c['parser'],c['run'],str(path),st.st_size,c['artifact']['sha256']));src=sqlite3.connect(f'file:{path}?mode=ro&immutable=1',uri=True);src.row_factory=sqlite3.Row
   if c['family']=='schema20_core':cs,ts=_core(src,out,key,c,r0,cs,ts);mapping={}
   else:cs,ts,mapping=_component(src,out,key,c,r0,cs,ts);rs,ks=_relations(src,out,key,c,mapping,rs,ks)
   _record_unsupported_relations(src,out,key,c['family'])
   adapted={x[0] for x in out.execute('select source_table from adapter_coverage where source_key=?',(key,))}
   for table in sorted(_tables(src)-adapted):
    if 'raw_text' in _cols(src,table) and table!='textmap_physical_occurrence':
     n=src.execute(f'select count(*) from {table} where raw_text is not null').fetchone()[0]
     if n:
      duplicate=table in ('codexquest_text_occurrence','codexquest_body_chunk','character_lore_reuse_candidate') or table.endswith('_text_resolution_occurrence')
      out.execute('insert into adapter_coverage values(?,?,?,?,?,?,?,?)',(key,f'{"duplicate" if duplicate else "unsupported"}:{table}','text',table,n,0,'duplicate_projection' if duplicate else 'unsupported','represented by exact candidate/body projection' if duplicate else 'explicit adapter absent'))
   src.close()
  out.execute('insert into research_text_fts(rowid,raw_text) select id,raw_text from research_text');out.execute('insert into corpus_release values(1,?,?,?,?,?,?,?,?,?)',(hashlib.sha256(corpus_manifest.read_bytes()).hexdigest(),m['corpus_ref'],'research-explicit-adapters-v2','deterministic-no-wall-clock',len(m['components']),cs,ts,rs,ks));out.commit()
 except Exception:
  out.close();output.unlink(missing_ok=True);raise
 out.close();return {'status':'pass','corpus_ref':m['corpus_ref'],'components':len(m['components']),'container_rows':cs,'text_rows':ts,'relation_rows':rs,'candidate_rows':ks,'size_bytes':output.stat().st_size,'sha256':sha256_file(output),'seconds':time.perf_counter()-started}
