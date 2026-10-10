"""Builders for Readable text files and Subtitle timed-cue evidence."""
from __future__ import annotations
import hashlib,json,os,re,sqlite3
from collections import Counter
from pathlib import Path
from .direct_text_7_1_source import SNAPSHOT,blob_oid,canonical,ident,implementation_seals,sha_bytes,sha_file,split_lines

ROOT=Path(__file__).resolve().parents[2]
R0_FAMILY={'readable':'raw_readable_json_occurrence','subtitle':'raw_subtitle_json_occurrence'}
R0_CONTRACT='raw-physical-evidence-v1';R0_RUN='ir-batch-m3-r0-physical-7.1:9fc2c32cc6bb81dd6091e504d43e40bdcf930ea2'
IMPLEMENTATION_COMMON=('src/genshin_lore/direct_text_7_1_source.py','src/genshin_lore/direct_text_7_1_build.py')
TIME_RE=re.compile(r'^(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})[ \t]*$')

def _ms(groups):
 h,m,s,z=map(int,groups);return ((h*60+m)*60+s)*1000+z
def parse_srt(raw:bytes):
 text=raw.decode('utf-8-sig');records=[]
 blocks=re.split(r'\n\s*\n',text.strip('\n')) if text else []
 for ordinal,block in enumerate(blocks):
  lines=block.split('\n');idx=lines[0] if lines else '';timing=lines[1] if len(lines)>1 else None;m=TIME_RE.fullmatch(timing or '')
  if idx.isdigit() and m:
   kind='cue';start=_ms(m.groups()[:4]);end=_ms(m.groups()[4:]);body='\n'.join(lines[2:])
  elif len(lines)==1 and idx.isdigit():kind='malformed_orphan_index';start=end=None;body=''
  else:kind='malformed_unsupported';start=end=None;body='\n'.join(lines[1:])
  records.append((ordinal,kind,idx,timing,start,end,body,sha_bytes(block.encode('utf-8'))))
 return records
def _r0_map(path:Path,root_name:str):
 c=sqlite3.connect(f'file:{path.resolve().as_posix()}?mode=ro&immutable=1',uri=True)
 rows={r[0]:f'file:{r[1]}' for r in c.execute('SELECT relative_path,source_file_id FROM source_file WHERE root=?',(root_name,))};c.close();return rows
def _triggers(c,tables,unique):
 for table in tables:
  c.execute(f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'immutable evidence'); END")
  c.execute(f"CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'immutable evidence'); END")
  clauses=[f"EXISTS(SELECT 1 FROM {table} WHERE {expr})" for expr in unique[table]]
  c.execute(f"CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table} WHEN {' OR '.join(clauses)} BEGIN SELECT RAISE(ABORT,'immutable evidence collision'); END")
def _open_owned(output:Path):
 if any(Path(str(output)+s).exists() for s in ('','-wal','-shm','-journal')):raise FileExistsError('output or sidecar exists')
 with output.open('xb'):pass
 st=output.stat();return (st.st_dev,st.st_ino)
def _cleanup(output,owned):
 try:st=output.stat()
 except FileNotFoundError:return
 if (st.st_dev,st.st_ino)!=owned:return
 for p in (Path(str(output)+'-wal'),Path(str(output)+'-shm'),Path(str(output)+'-journal'),output):
  try:p.unlink()
  except FileNotFoundError:pass
def _schema_structure(c):return [list(x) for x in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
def _artifact(path):return {'size_bytes':path.stat().st_size,'sha256':sha_file(path)}
def _logical(c,tables):
 out={}
 for table in tables:
  cols=[x[1] for x in c.execute('PRAGMA table_info('+table+')')];h=hashlib.sha256();count=0
  for row in c.execute('SELECT * FROM '+table+' ORDER BY '+','.join(cols)):
   h.update((canonical(list(row))+'\n').encode());count+=1
  out[table]={'rows':count,'sha256':h.hexdigest()}
 return out

def build_readable(source_root,scope_path,output,r0_artifact,document_artifact):
 source_root=Path(source_root);scope_path=Path(scope_path);output=Path(output);scope=json.loads(scope_path.read_text());repo=ROOT
 impl_paths=IMPLEMENTATION_COMMON+('schemas/readable-evidence-v1.sql',);impl=implementation_seals(repo,impl_paths);parser=ident('readable-parser',impl);run=ident('readable-run',parser,sha_file(scope_path))
 if _artifact(Path(r0_artifact))!={'size_bytes':scope['r0_dependency']['artifact_size_bytes'],'sha256':scope['r0_dependency']['artifact_sha256']} or _artifact(Path(document_artifact))!={'size_bytes':scope['accepted_dependency']['artifact_size_bytes'],'sha256':scope['accepted_dependency']['artifact_sha256']}:raise ValueError('historical dependency artifact mismatch')
 lookup=scope['lookup_sources'][0];lookup_raw=(source_root/lookup['path']).read_bytes()
 if sha_bytes(lookup_raw)!=lookup['sha256'] or blob_oid(lookup_raw)!=lookup['blob']:raise ValueError('lookup source byte/blob mismatch')
 r0=_r0_map(Path(r0_artifact),'Readable')
 if len(scope['files'])!=len(r0):raise ValueError('R0 Readable denominator mismatch')
 owned=_open_owned(output);c=None
 try:
  c=sqlite3.connect(output);c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA journal_mode=DELETE');c.executescript((repo/'schemas/readable-evidence-v1.sql').read_text())
  files={};line_count=0;copied=0
  for item in scope['files']:
   rel=item['path'];raw=(source_root/rel).read_bytes()
   if sha_bytes(raw)!=item['sha256'] or blob_oid(raw)!=item['blob'] or rel not in r0:raise ValueError('source/blob/R0 binding mismatch '+rel)
   lines=list(split_lines(raw));fid=ident('readable-file',run,rel);files[rel]=fid
   c.execute('INSERT INTO readable_file VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(fid,run,rel,item['sha256'],item['blob'],len(raw),'utf-8',len(lines),int(not raw),item['sha256'],R0_FAMILY['readable'],R0_CONTRACT,R0_RUN,r0[rel]))
   for ordinal,start,end,text,term in lines:
    c.execute('INSERT INTO readable_line VALUES(?,?,?,?,?,?,?,?,?)',(ident('readable-line',fid,ordinal),fid,ordinal,f'/line/{ordinal}',start,end,term,text,sha_bytes(text.encode())));line_count+=1;copied+=len(text.encode())+len(term.encode())
  rel_states=Counter();candidate_count=0
  loc=json.loads((source_root/'ExcelBinOutput/LocalizationExcelConfigData.json').read_text())
  for ordinal,row in enumerate(loc):
   for key,value in row.items():
    if not isinstance(value,str) or not value.startswith('ART/UI/Readable/'):continue
    target='Readable/'+value[len('ART/UI/Readable/'):]+'.txt';hits=[target] if target in files else [];status='resolved_unique' if len(hits)==1 else 'missing';rid=ident('readable-relation','localization',ordinal,key,value)
    c.execute('INSERT INTO readable_relation VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(rid,'source_localization',None,None,None,f'localization:{ordinal}','ExcelBinOutput/LocalizationExcelConfigData.json',f'/{ordinal}/{key}','localization_readable_path',value,'all frozen Readable physical paths; exact exported path + .txt',status,1,len(hits)));rel_states[status]+=1
    for target in hits:c.execute('INSERT INTO readable_candidate VALUES(?,?,?,?,?)',(ident('readable-candidate',rid,target),rid,files[target],target,r0[target]));candidate_count+=len(hits)
  # Preserve accepted Document provenance as an exact navigation layer; no body is copied.
  dc=sqlite3.connect(f'file:{Path(document_artifact).resolve().as_posix()}?mode=ro&immutable=1',uri=True);dc.row_factory=sqlite3.Row
  drel=json.loads(dc.execute('SELECT manifest_json FROM document_release WHERE id=1').fetchone()[0])
  for row in dc.execute("SELECT r.id,r.row_id,r.origin_pointer,r.source_path,r.pointer,r.raw_value,c.target_source_path FROM document_relation r JOIN document_candidate c ON c.relation_id=r.id WHERE r.kind='localization_readable' ORDER BY r.id,c.id"):
   target=row['target_source_path'];rid=ident('readable-relation','document',row['id'],target);hits=[target] if target in files else []
   c.execute('INSERT INTO readable_relation VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(rid,'document_archive','document-evidence-v1',drel['parser_build_id'],drel['run_id'],row['id'],row['source_path'],row['pointer'],'document_localization_readable',row['raw_value'],'accepted Document explicit localization path candidate', 'resolved_unique' if hits else 'missing',1,len(hits)));rel_states['resolved_unique' if hits else 'missing']+=1
   for target in hits:c.execute('INSERT INTO readable_candidate VALUES(?,?,?,?,?)',(ident('readable-candidate',rid,target),rid,files[target],target,r0[target]));candidate_count+=len(hits)
  dc.close()
  tables=['readable_file','readable_line','readable_relation','readable_candidate'];logical=_logical(c,tables)
  release={'contract':'readable-evidence-v1','snapshot_id':SNAPSHOT,'parser_build_id':parser,'run_id':run,'scope_sha256':sha_file(scope_path),'implementation':impl,'counts':{'readable_file':len(files),'readable_line':line_count,'readable_relation':sum(rel_states.values()),'readable_candidate':candidate_count},'logical_seals':logical,'relation_states':dict(rel_states),'structured_text_bytes':copied,'raw_body_blob_bytes_copied':0,'r0_reused_files':len(files),'physical_tree_complete':True,'structured_body_projection_complete':True,'semantic_owner_complete':False}
  c.execute('INSERT INTO readable_release VALUES(1,?)',(canonical(release),));_triggers(c,['readable_file','readable_line','readable_relation','readable_candidate','readable_release'],{'readable_file':['id=NEW.id','source_path=NEW.source_path'],'readable_line':['id=NEW.id','file_id=NEW.file_id AND ordinal=NEW.ordinal'],'readable_relation':['id=NEW.id','origin_family=NEW.origin_family AND origin_run IS NEW.origin_run AND origin_occurrence_id=NEW.origin_occurrence_id AND origin_source_path=NEW.origin_source_path AND origin_pointer=NEW.origin_pointer AND relation_kind=NEW.relation_kind AND raw_locator=NEW.raw_locator'],'readable_candidate':['id=NEW.id','relation_id=NEW.relation_id AND target_file_id=NEW.target_file_id'],'readable_release':['id=NEW.id']});c.commit()
  if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('foreign key check')
  c.execute('VACUUM');c.close();c=None
  return {**release,'artifact':_artifact(output),'schema_structure':_schema_structure(sqlite3.connect(f'file:{output.resolve().as_posix()}?mode=ro&immutable=1',uri=True))}
 except Exception:
  if c is not None:c.close()
  _cleanup(output,owned);raise

def build_subtitle(source_root,scope_path,output,r0_artifact):
 source_root=Path(source_root);scope_path=Path(scope_path);output=Path(output);scope=json.loads(scope_path.read_text());repo=ROOT
 impl_paths=IMPLEMENTATION_COMMON+('schemas/subtitle-evidence-v1.sql',);impl=implementation_seals(repo,impl_paths);parser=ident('subtitle-parser',impl);run=ident('subtitle-run',parser,sha_file(scope_path))
 if _artifact(Path(r0_artifact))!={'size_bytes':scope['r0_dependency']['artifact_size_bytes'],'sha256':scope['r0_dependency']['artifact_sha256']}:raise ValueError('historical dependency artifact mismatch')
 lookup=scope['lookup_sources'][0];lookup_raw=(source_root/lookup['path']).read_bytes()
 if sha_bytes(lookup_raw)!=lookup['sha256'] or blob_oid(lookup_raw)!=lookup['blob']:raise ValueError('lookup source byte/blob mismatch')
 r0=_r0_map(Path(r0_artifact),'Subtitle')
 if len(scope['files'])!=len(r0):raise ValueError('R0 Subtitle denominator mismatch')
 owned=_open_owned(output);c=None
 try:
  c=sqlite3.connect(output);c.execute('PRAGMA foreign_keys=ON');c.executescript((repo/'schemas/subtitle-evidence-v1.sql').read_text());files={};records=Counter()
  for item in scope['files']:
   rel=item['path'];raw=(source_root/rel).read_bytes()
   if sha_bytes(raw)!=item['sha256'] or blob_oid(raw)!=item['blob'] or rel not in r0:raise ValueError('source/blob/R0 binding mismatch '+rel)
   parsed=parse_srt(raw);fid=ident('subtitle-file',run,rel);files[rel]=fid;valid=sum(x[1]=='cue' for x in parsed);mal=len(parsed)-valid
   c.execute('INSERT INTO subtitle_file VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(fid,run,rel,item['sha256'],item['blob'],len(raw),'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8',len(parsed),valid,mal,int(not raw),item['sha256'],R0_FAMILY['subtitle'],R0_CONTRACT,R0_RUN,r0[rel]))
   for ordinal,kind,index,timing,start,end,text,digest in parsed:c.execute('INSERT INTO subtitle_record VALUES(?,?,?,?,?,?,?,?,?,?,?)',(ident('subtitle-record',fid,ordinal),fid,ordinal,f'/record/{ordinal}',kind,index,timing,start,end,text,digest));records[kind]+=1
  loc=json.loads((source_root/'ExcelBinOutput/LocalizationExcelConfigData.json').read_text());states=Counter();candidates=0
  for ordinal,row in enumerate(loc):
   for key,value in row.items():
    if not isinstance(value,str) or not value.endswith('.mihoyobin'):continue
    target='Subtitle/'+value[:-len('.mihoyobin')]+'.srt';hits=[target] if target in files else [];status='resolved_unique' if hits else 'missing';rid=ident('subtitle-relation',ordinal,key,value)
    c.execute('INSERT INTO subtitle_relation VALUES(?,?,?,?,?,?,?,?,?)',(rid,'ExcelBinOutput/LocalizationExcelConfigData.json',f'/{ordinal}/{key}','localization_subtitle_resource',value,'all frozen Subtitle physical paths; authored locale resource path with .mihoyobin -> .srt export contract',status,1,len(hits)));states[status]+=1
    for target in hits:c.execute('INSERT INTO subtitle_candidate VALUES(?,?,?,?,?)',(ident('subtitle-candidate',rid,target),rid,files[target],target,r0[target]));candidates+=len(hits)
  tables=['subtitle_file','subtitle_record','subtitle_relation','subtitle_candidate'];logical=_logical(c,tables)
  release={'contract':'subtitle-evidence-v1','snapshot_id':SNAPSHOT,'parser_build_id':parser,'run_id':run,'scope_sha256':sha_file(scope_path),'implementation':impl,'counts':{'subtitle_file':len(files),'subtitle_record':sum(records.values()),'subtitle_relation':sum(states.values()),'subtitle_candidate':candidates},'logical_seals':logical,'record_states':dict(records),'relation_states':dict(states),'r0_reused_files':len(files),'raw_body_blob_bytes_copied':0,'physical_tree_complete':True,'cue_projection_complete':True,'semantic_owner_complete':False}
  c.execute('INSERT INTO subtitle_release VALUES(1,?)',(canonical(release),));_triggers(c,['subtitle_file','subtitle_record','subtitle_relation','subtitle_candidate','subtitle_release'],{'subtitle_file':['id=NEW.id','source_path=NEW.source_path'],'subtitle_record':['id=NEW.id','file_id=NEW.file_id AND ordinal=NEW.ordinal'],'subtitle_relation':['id=NEW.id','origin_source_path=NEW.origin_source_path AND origin_pointer=NEW.origin_pointer AND relation_kind=NEW.relation_kind AND raw_locator=NEW.raw_locator'],'subtitle_candidate':['id=NEW.id','relation_id=NEW.relation_id AND target_file_id=NEW.target_file_id'],'subtitle_release':['id=NEW.id']});c.commit()
  if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('foreign key check')
  c.execute('VACUUM');c.close();c=None
  return {**release,'artifact':_artifact(output),'schema_structure':_schema_structure(sqlite3.connect(f'file:{output.resolve().as_posix()}?mode=ro&immutable=1',uri=True))}
 except Exception:
  if c is not None:c.close()
  _cleanup(output,owned);raise
