"""Independent source reconstruction for direct-text artifacts."""
from __future__ import annotations
import hashlib,json,re,sqlite3,subprocess,time
from collections import Counter
from pathlib import Path
from .direct_text_7_1_source import COMMIT,SNAPSHOT,blob_oid,canonical,ident,implementation_seals,sha_bytes,sha_file,split_lines

ROOT=Path(__file__).resolve().parents[2];R0_CONTRACT='raw-physical-evidence-v1';R0_RUN='ir-batch-m3-r0-physical-7.1:9fc2c32cc6bb81dd6091e504d43e40bdcf930ea2'
TIME_RE=re.compile(r'^(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})[ \t]*$')
R0_PARSER='pb-batch-m3-r0-physical-7.1:23e78b08f997ab610cd55a11f91f2f4c252260af'
def _ms(g):h,m,s,z=map(int,g);return ((h*60+m)*60+s)*1000+z
def _subtitle_records(raw):
 text=raw.decode('utf-8-sig');blocks=re.split(r'\n\s*\n',text.strip('\n')) if text else []
 for n,block in enumerate(blocks):
  x=block.split('\n');idx=x[0] if x else '';timing=x[1] if len(x)>1 else None;m=TIME_RE.fullmatch(timing or '')
  if idx.isdigit() and m:kind='cue';start=_ms(m.groups()[:4]);end=_ms(m.groups()[4:]);body='\n'.join(x[2:])
  elif len(x)==1 and idx.isdigit():kind='malformed_orphan_index';start=end=None;body=''
  else:kind='malformed_unsupported';start=end=None;body='\n'.join(x[1:])
  yield n,kind,idx,timing,start,end,body,sha_bytes(block.encode())
def _r0(path,root):
 c=sqlite3.connect(f'file:{Path(path).resolve().as_posix()}?mode=ro&immutable=1',uri=True);out={x[0]:f'file:{x[1]}' for x in c.execute('SELECT relative_path,source_file_id FROM source_file WHERE root=?',(root,))};c.close();return out
def _equal(c,table,expected):
 cols=[x[1] for x in c.execute('PRAGMA table_info('+table+')')];actual=list(c.execute('SELECT * FROM '+table+' ORDER BY '+','.join(cols)));expected=sorted(expected)
 if actual!=expected:raise ValueError(f'{table} full business-column mismatch: actual={len(actual)} expected={len(expected)}')
 h=hashlib.sha256()
 for row in actual:h.update((canonical(list(row))+'\n').encode())
 return {'rows':len(actual),'sha256':h.hexdigest()}
def _identity(kind,scope_path):
 paths=('src/genshin_lore/direct_text_7_1_source.py','src/genshin_lore/direct_text_7_1_build.py',f'schemas/{kind}-evidence-v1.sql');impl=implementation_seals(ROOT,paths);parser=ident(kind+'-parser',impl);run=ident(kind+'-run',parser,sha_file(Path(scope_path)));return impl,parser,run
def _git_tree(source_root,prefix):
 out=subprocess.check_output(['git','-C',str(source_root),'ls-tree','-r',COMMIT,'--',prefix],text=True)
 return {line.split('\t',1)[1]:line.split()[2] for line in out.splitlines() if line}
def _pinned_inventory(source_root,scope,tree):
 pinned=_git_tree(source_root,tree);actual={p.relative_to(source_root).as_posix() for p in (source_root/tree).rglob('*') if p.is_file()};declared={x['path'] for x in scope['files']}
 if set(pinned)!=actual or actual!=declared:raise ValueError(tree+' pinned/actual/scope path denominator mismatch')
 rows=[]
 for rel in sorted(pinned):
  raw=(source_root/rel).read_bytes();row={'path':rel,'size_bytes':len(raw),'sha256':sha_bytes(raw),'blob':blob_oid(raw)}
  if row['blob']!=pinned[rel]:raise ValueError(tree+' actual bytes do not match pinned Git blob: '+rel)
  rows.append(row)
 if rows!=scope['files'] or len(rows)!=scope['file_denominator']:raise ValueError(tree+' scope inventory mismatch')
 return rows
def _verify_lookup_sources(source_root,scope,kind):
 for item in scope['lookup_sources']:
  pinned=_git_tree(source_root,item['path'])
  if set(pinned)!={item['path']}:raise ValueError(kind+' lookup missing from pinned tree')
  raw=(source_root/item['path']).read_bytes();actual={'path':item['path'],'size_bytes':len(raw),'sha256':sha_bytes(raw),'blob':blob_oid(raw)}
  if actual!=item or actual['blob']!=pinned[item['path']]:raise ValueError(kind+' lookup pinned byte/blob mismatch')
def _verify_r0(path,scope,root):
 if {'size_bytes':Path(path).stat().st_size,'sha256':sha_file(Path(path))}!={'size_bytes':scope['artifact_size_bytes'],'sha256':scope['artifact_sha256']}:raise ValueError(root+' R0 artifact mismatch')
 c=sqlite3.connect(f'file:{Path(path).resolve().as_posix()}?mode=ro&immutable=1',uri=True);meta={k:json.loads(v) for k,v in c.execute('SELECT key,value FROM metadata')};count=c.execute('SELECT count(*) FROM source_file WHERE root=?',(root,)).fetchone()[0];c.close()
 expected_family='raw_'+root.lower()+'_json_occurrence'
 if scope.get('contract')!=R0_CONTRACT or scope.get('run')!=R0_RUN or scope.get('family')!=expected_family or meta.get('ingest_run_id')!=scope['run'] or meta.get('parser_build_id')!=R0_PARSER or meta.get('snapshot_id')!=SNAPSHOT or count!=meta['root_counts'][root]['files']:raise ValueError(root+' R0 release identity mismatch')
def _verify_document(path,scope,runtime_path=None):
 runtime=json.loads(Path(runtime_path or ROOT/'manifests/document-archive/runtime.json').read_text())
 if {'size_bytes':Path(path).stat().st_size,'sha256':sha_file(Path(path))}!={'size_bytes':scope['artifact_size_bytes'],'sha256':scope['artifact_sha256']} or runtime['artifact']!={'size_bytes':scope['artifact_size_bytes'],'sha256':scope['artifact_sha256']}:raise ValueError('Readable Document artifact mismatch')
 c=sqlite3.connect(f'file:{Path(path).resolve().as_posix()}?mode=ro&immutable=1',uri=True);release=json.loads(c.execute('SELECT manifest_json FROM document_release WHERE id=1').fetchone()[0]);c.close()
 if runtime['family']!=scope['family'] or runtime['family_contract_version']!=scope['contract'] or release['contract']!=scope['contract'] or release['run_id']!=scope['run'] or runtime['release']['run_id']!=scope['run'] or runtime['release']['parser_build_id']!=release['parser_build_id'] or runtime['release']!=release:raise ValueError('Readable Document exact authority mismatch')
def _norm_sql(value):return ' '.join((value or '').split()).lower()
def _schema_snapshot(c):
 tables={}
 for name,sql in c.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
  tables[name]={'sql':_norm_sql(sql),'columns':[list(x) for x in c.execute('PRAGMA table_xinfo('+json.dumps(name)+')')],'foreign_keys':[list(x) for x in c.execute('PRAGMA foreign_key_list('+json.dumps(name)+')')],'indexes':[]}
  for row in c.execute('PRAGMA index_list('+json.dumps(name)+')'):
   index_name=row[1];index_sql=c.execute('SELECT sql FROM sqlite_master WHERE type="index" AND name=?',(index_name,)).fetchone();tables[name]['indexes'].append({'list':list(row),'sql':_norm_sql(index_sql[0] if index_sql else None),'columns':[list(x) for x in c.execute('PRAGMA index_xinfo('+json.dumps(index_name)+')')]})
 objects=[{'type':t,'name':n,'table':tbl,'sql':_norm_sql(sql)} for t,n,tbl,sql in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
 return {'tables':tables,'objects':objects}
def _expected_triggers(c,kind):
 unique={
  'readable':{'readable_file':['id=NEW.id','source_path=NEW.source_path'],'readable_line':['id=NEW.id','file_id=NEW.file_id AND ordinal=NEW.ordinal'],'readable_relation':['id=NEW.id','origin_family=NEW.origin_family AND origin_run IS NEW.origin_run AND origin_occurrence_id=NEW.origin_occurrence_id AND origin_source_path=NEW.origin_source_path AND origin_pointer=NEW.origin_pointer AND relation_kind=NEW.relation_kind AND raw_locator=NEW.raw_locator'],'readable_candidate':['id=NEW.id','relation_id=NEW.relation_id AND target_file_id=NEW.target_file_id'],'readable_release':['id=NEW.id']},
  'subtitle':{'subtitle_file':['id=NEW.id','source_path=NEW.source_path'],'subtitle_record':['id=NEW.id','file_id=NEW.file_id AND ordinal=NEW.ordinal'],'subtitle_relation':['id=NEW.id','origin_source_path=NEW.origin_source_path AND origin_pointer=NEW.origin_pointer AND relation_kind=NEW.relation_kind AND raw_locator=NEW.raw_locator'],'subtitle_candidate':['id=NEW.id','relation_id=NEW.relation_id AND target_file_id=NEW.target_file_id'],'subtitle_release':['id=NEW.id']},
 }[kind]
 for table,expressions in unique.items():
  c.execute(f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'immutable evidence'); END")
  c.execute(f"CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'immutable evidence'); END")
  clauses=' OR '.join(f'EXISTS(SELECT 1 FROM {table} WHERE {expr})' for expr in expressions)
  c.execute(f"CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table} WHEN {clauses} BEGIN SELECT RAISE(ABORT,'immutable evidence collision'); END")
def _verify_schema(c,kind):
 expected=sqlite3.connect(':memory:');expected.executescript((ROOT/f'schemas/{kind}-evidence-v1.sql').read_text());_expected_triggers(expected,kind);want=_schema_snapshot(expected);expected.close();got=_schema_snapshot(c)
 if got!=want:raise ValueError(kind+' schema/write-once structure mismatch')
 digest=sha_bytes(canonical(want).encode());return {'schema_structure_match':True,'write_once_structure_match':True,'unexpected_authoritative_objects':[],'normalized_schema_sha256':digest,'authoritative_object_count':len(want['objects'])}
def _runtime(kind,path=None):
 if path is None:raise ValueError('7.1 candidate requires an explicit snapshot-local runtime manifest')
 return json.loads(Path(path).read_text())
def _verify_release(kind,release,runtime,expected,seals,counts,summaries):
 if any(release.get(k)!=v for k,v in expected.items()) or release.get('logical_seals')!=seals or release.get('counts')!=counts:raise ValueError(kind+' release identity/logical/count mismatch')
 if runtime.get('counts')!=counts or runtime.get('release')!=release:raise ValueError(kind+' runtime manifest count/release mismatch')
 for key,value in summaries.items():
  if release.get(key)!=value:raise ValueError(kind+' independently reconstructed summary mismatch: '+key)
def verify_readable(source_root,scope_path,artifact,r0_artifact,document_artifact,runtime_manifest_path=None,document_runtime_manifest_path=None):
 started=time.perf_counter();source_root=Path(source_root);scope=json.loads(Path(scope_path).read_text());actual_inv=_pinned_inventory(source_root,scope,'Readable');_verify_lookup_sources(source_root,scope,'Readable');_verify_r0(r0_artifact,scope['r0_dependency'],'Readable');_verify_document(document_artifact,scope['accepted_dependency'],document_runtime_manifest_path)
 impl,parser,run=_identity('readable',scope_path);r0=_r0(r0_artifact,'Readable');files={};file_rows=[];line_rows=[]
 for item in actual_inv:
  rel=item['path'];raw=(source_root/rel).read_bytes();lines=list(split_lines(raw));fid=ident('readable-file',run,rel);files[rel]=fid
  file_rows.append((fid,run,rel,item['sha256'],item['blob'],len(raw),'utf-8',len(lines),int(not raw),item['sha256'],'raw_readable_json_occurrence',R0_CONTRACT,R0_RUN,r0[rel]))
  for n,a,b,text,term in lines:line_rows.append((ident('readable-line',fid,n),fid,n,f'/line/{n}',a,b,term,text,sha_bytes(text.encode())))
 rel_rows=[];cand_rows=[];states=Counter();loc=json.loads((source_root/'ExcelBinOutput/LocalizationExcelConfigData.json').read_text())
 for n,row in enumerate(loc):
  for key,value in row.items():
   if not isinstance(value,str) or not value.startswith('ART/UI/Readable/'):continue
   target='Readable/'+value[len('ART/UI/Readable/'):]+'.txt';hits=[target] if target in files else [];status='resolved_unique' if hits else 'missing';rid=ident('readable-relation','localization',n,key,value)
   rel_rows.append((rid,'source_localization',None,None,None,f'localization:{n}','ExcelBinOutput/LocalizationExcelConfigData.json',f'/{n}/{key}','localization_readable_path',value,'all frozen Readable physical paths; exact exported path + .txt',status,1,len(hits)));states[status]+=1
   for target in hits:cand_rows.append((ident('readable-candidate',rid,target),rid,files[target],target,r0[target]))
 dc=sqlite3.connect(f'file:{Path(document_artifact).resolve().as_posix()}?mode=ro&immutable=1',uri=True);dc.row_factory=sqlite3.Row;drel=json.loads(dc.execute('SELECT manifest_json FROM document_release WHERE id=1').fetchone()[0])
 for row in dc.execute("SELECT r.id,r.row_id,r.origin_pointer,r.source_path,r.pointer,r.raw_value,c.target_source_path FROM document_relation r JOIN document_candidate c ON c.relation_id=r.id WHERE r.kind='localization_readable' ORDER BY r.id,c.id"):
  target=row['target_source_path'];hits=[target] if target in files else [];rid=ident('readable-relation','document',row['id'],target);status='resolved_unique' if hits else 'missing'
  rel_rows.append((rid,'document_archive','document-evidence-v1',drel['parser_build_id'],drel['run_id'],row['id'],row['source_path'],row['pointer'],'document_localization_readable',row['raw_value'],'accepted Document explicit localization path candidate',status,1,len(hits)));states[status]+=1
  for target in hits:cand_rows.append((ident('readable-candidate',rid,target),rid,files[target],target,r0[target]))
 dc.close();c=sqlite3.connect(f'file:{Path(artifact).resolve().as_posix()}?mode=ro&immutable=1',uri=True);schema_proof=_verify_schema(c,'readable')
 seals={};seals['readable_file']=_equal(c,'readable_file',file_rows);seals['readable_line']=_equal(c,'readable_line',line_rows);seals['readable_relation']=_equal(c,'readable_relation',rel_rows);seals['readable_candidate']=_equal(c,'readable_candidate',cand_rows)
 release=json.loads(c.execute('SELECT manifest_json FROM readable_release WHERE id=1').fetchone()[0]);c.close()
 expected={'contract':'readable-evidence-v1','snapshot_id':SNAPSHOT,'parser_build_id':parser,'run_id':run,'scope_sha256':sha_file(Path(scope_path)),'implementation':impl};counts={k:len(v) for k,v in [('readable_file',file_rows),('readable_line',line_rows),('readable_relation',rel_rows),('readable_candidate',cand_rows)]};summaries={'relation_states':dict(sorted(states.items())),'r0_reused_files':len(file_rows),'raw_body_blob_bytes_copied':0,'physical_tree_complete':True,'structured_body_projection_complete':True,'semantic_owner_complete':False,'structured_text_bytes':sum(x['size_bytes'] for x in actual_inv)}
 _verify_release('Readable',release,_runtime('readable',runtime_manifest_path),expected,seals,counts,summaries)
 elapsed=time.perf_counter()-started
 return {'status':'pass','actual_bytes_git_blob_match':True,'pinned_tree_path_set_match':True,'lookup_pinned_blob_match':True,'dependency_identity_match':True,'full_business_column_equality':True,'no_extra_rows':True,**schema_proof,'artifact':{'size_bytes':Path(artifact).stat().st_size,'sha256':sha_file(Path(artifact))},'snapshot_id':SNAPSHOT,'scope_sha256':expected['scope_sha256'],'implementation':impl,'parser_build_id':parser,'run_id':run,'contract':'readable-evidence-v1','counts':counts,'independent_summaries':summaries,'logical_seals':seals,'duration_seconds':elapsed,'rows_per_second':sum(x['rows'] for x in seals.values())/max(elapsed,.001)}
def verify_subtitle(source_root,scope_path,artifact,r0_artifact,runtime_manifest_path=None):
 started=time.perf_counter();source_root=Path(source_root);artifact=Path(artifact);scope=json.loads(Path(scope_path).read_text())
 if any(Path(str(artifact)+suffix).exists() for suffix in ('-wal','-shm','-journal')):raise ValueError('7.1 Subtitle SQLite sidecar present')
 before=artifact.stat();before_identity=(before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)
 runtime=_runtime('subtitle',runtime_manifest_path)
 if runtime.get('artifact')!={'size_bytes':before.st_size,'sha256':sha_file(artifact)}:raise ValueError('7.1 Subtitle runtime artifact identity mismatch')
 actual_inv=_pinned_inventory(source_root,scope,'Subtitle');_verify_lookup_sources(source_root,scope,'Subtitle');_verify_r0(r0_artifact,scope['r0_dependency'],'Subtitle')
 impl,parser,run=_identity('subtitle',scope_path);r0=_r0(r0_artifact,'Subtitle');files={};file_rows=[];record_rows=[];record_states=Counter()
 for item in actual_inv:
  rel=item['path'];raw=(source_root/rel).read_bytes();records=list(_subtitle_records(raw));fid=ident('subtitle-file',run,rel);files[rel]=fid;valid=sum(x[1]=='cue' for x in records);mal=len(records)-valid
  file_rows.append((fid,run,rel,item['sha256'],item['blob'],len(raw),'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8',len(records),valid,mal,int(not raw),item['sha256'],'raw_subtitle_json_occurrence',R0_CONTRACT,R0_RUN,r0[rel]))
  for n,kind,index,timing,a,b,text,digest in records:record_rows.append((ident('subtitle-record',fid,n),fid,n,f'/record/{n}',kind,index,timing,a,b,text,digest));record_states[kind]+=1
 rel_rows=[];cand_rows=[];states=Counter();loc=json.loads((source_root/'ExcelBinOutput/LocalizationExcelConfigData.json').read_text())
 for n,row in enumerate(loc):
  for key,value in row.items():
   if not isinstance(value,str) or not value.endswith('.mihoyobin'):continue
   target='Subtitle/'+value[:-len('.mihoyobin')]+'.srt';hits=[target] if target in files else [];status='resolved_unique' if hits else 'missing';rid=ident('subtitle-relation',n,key,value)
   rel_rows.append((rid,'ExcelBinOutput/LocalizationExcelConfigData.json',f'/{n}/{key}','localization_subtitle_resource',value,'all frozen Subtitle physical paths; authored locale resource path with .mihoyobin -> .srt export contract',status,1,len(hits)));states[status]+=1
   for target in hits:cand_rows.append((ident('subtitle-candidate',rid,target),rid,files[target],target,r0[target]))
 c=sqlite3.connect(f'file:{Path(artifact).resolve().as_posix()}?mode=ro&immutable=1',uri=True)
 if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('7.1 Subtitle SQLite integrity/FK mismatch')
 schema_proof=_verify_schema(c,'subtitle');seals={}
 for table,rows in [('subtitle_file',file_rows),('subtitle_record',record_rows),('subtitle_relation',rel_rows),('subtitle_candidate',cand_rows)]:seals[table]=_equal(c,table,rows)
 release=json.loads(c.execute('SELECT manifest_json FROM subtitle_release WHERE id=1').fetchone()[0]);c.close();expected={'contract':'subtitle-evidence-v1','snapshot_id':SNAPSHOT,'parser_build_id':parser,'run_id':run,'scope_sha256':sha_file(Path(scope_path)),'implementation':impl};counts={k:len(v) for k,v in [('subtitle_file',file_rows),('subtitle_record',record_rows),('subtitle_relation',rel_rows),('subtitle_candidate',cand_rows)]};summaries={'record_states':dict(sorted(record_states.items())),'relation_states':dict(sorted(states.items())),'r0_reused_files':len(file_rows),'raw_body_blob_bytes_copied':0,'physical_tree_complete':True,'cue_projection_complete':True,'semantic_owner_complete':False}
 _verify_release('Subtitle',release,runtime,expected,seals,counts,summaries)
 after=artifact.stat()
 if (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)!=before_identity or sha_file(artifact)!=runtime['artifact']['sha256'] or any(Path(str(artifact)+suffix).exists() for suffix in ('-wal','-shm','-journal')):raise ValueError('7.1 Subtitle artifact changed during verification')
 elapsed=time.perf_counter()-started
 return {'status':'pass','actual_bytes_git_blob_match':True,'pinned_tree_path_set_match':True,'lookup_pinned_blob_match':True,'dependency_identity_match':True,'full_business_column_equality':True,'no_extra_rows':True,'sqlite_integrity_check':True,'foreign_key_check':True,'sidecars_absent':True,'artifact_identity_stable':True,'runtime_artifact_identity_match':True,**schema_proof,'artifact':{'size_bytes':Path(artifact).stat().st_size,'sha256':sha_file(Path(artifact))},'snapshot_id':SNAPSHOT,'scope_sha256':expected['scope_sha256'],'implementation':impl,'parser_build_id':parser,'run_id':run,'contract':'subtitle-evidence-v1','counts':counts,'independent_summaries':summaries,'logical_seals':seals,'duration_seconds':elapsed,'rows_per_second':sum(x['rows'] for x in seals.values())/max(elapsed,.001)}
