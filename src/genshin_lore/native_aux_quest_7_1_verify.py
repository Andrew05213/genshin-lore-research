"""Independent 7.1 source-to-rowset oracle for Aux/Quest exact historical scope."""
from __future__ import annotations
import hashlib,json,sqlite3,time
from collections import Counter,defaultdict
from pathlib import Path
from .event_7_1_source import SNAPSHOT,array_records,check_files,pairs,sha
ROOT=Path(__file__).resolve().parents[2];TABLES=('native_archive_container','native_archive_slot','native_archive_text','native_archive_relation','native_archive_candidate');SELECTION='manifests/native-archive-7.1/aux-quest-selection-v1.json'
def iid(*p):return 'naq71:'+hashlib.sha256(json.dumps(p,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:40]
def normalized_schema(c):return [[a,b,d,' '.join((s or '').split()).lower()] for a,b,d,s in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
def check_artifact(path,identity):
 if Path(path).stat().st_size!=identity['size_bytes'] or sha(path)!=identity['sha256']:raise ValueError('7.1 accepted dependency artifact mismatch')
def relation_values(row,spec):
 value=row.get(spec['field']);shape=spec.get('shape','scalar')
 if shape in ('scalar','scalar_optional'):return [] if value is None else [(spec['field'],value)]
 if shape=='array' and isinstance(value,list):return [(f"{spec['field']}/{i}",x) for i,x in enumerate(value) if x is not None]
 if value is not None:raise ValueError('declared relation shape mismatch')
 return []
def dependency_manifests(metadata_root=None):
 base=Path(metadata_root) if metadata_root is not None else ROOT
 return base/'manifests/quest-derived-7.1/standalone-candidates-v1.json',base/'manifests/native-archive-7.1/bulk-runtime.json'
def expected_external(source_root,quest_artifact,bulk_artifact,*,metadata_root=None):
 qn_manifest,bulk_manifest=dependency_manifests(metadata_root)
 qn=json.loads(qn_manifest.read_text())['components']['quest_narrative_archive'];bulk=json.loads(bulk_manifest.read_text())
 if qn['source_snapshot']!=SNAPSHOT or bulk['source_snapshot']!=SNAPSHOT:raise ValueError('cross-snapshot target dependency')
 check_artifact(quest_artifact,qn['artifact']);check_artifact(bulk_artifact,bulk['artifact']);out=[];cache={}
 def exact(path,pointer,native,raw_sha,field):
  if path not in cache:cache[path]=list(array_records(Path(source_root)/path))
  index=int(pointer[1:]);physical=cache[path][index]
  if pointer!='/'+str(index) or physical[0]!=index or str(physical[1].get(field))!=native or hashlib.sha256(physical[2].encode()).hexdigest()!=raw_sha:raise ValueError('accepted target second-hop physical mismatch '+path+pointer)
 q=sqlite3.connect(f'file:{Path(quest_artifact).resolve()}?mode=ro&immutable=1',uri=True);q.row_factory=sqlite3.Row
 for namespace,kind,field in (('quest_main','main_quest','id'),('quest_step','quest_step','subId')):
  for row in q.execute('SELECT id,run_id,source_path,pointer,native_id,raw_sha256 FROM excel_row WHERE kind=? ORDER BY source_path,pointer,id',(kind,)):
   if row['run_id']!=qn['ingest_run_id']:raise ValueError('Quest target run')
   exact(row['source_path'],row['pointer'],row['native_id'],row['raw_sha256'],field)
   out.append({'namespace':namespace,'raw_locator':row['native_id'],'target_source_path':row['source_path'],'target_source_pointer':row['pointer'],'target_native_id':row['native_id'],'target_raw_sha256':row['raw_sha256'],'target_family':'quest_narrative_archive','target_contract':qn['contract'],'target_parser':qn['parser_build_id'],'target_run':row['run_id'],'target_occurrence_id':row['id']})
 q.close();b=sqlite3.connect(f'file:{Path(bulk_artifact).resolve()}?mode=ro&immutable=1',uri=True);b.row_factory=sqlite3.Row
 for row in b.execute("SELECT id,run_id,source_path,pointer,native_id,raw_sha256 FROM native_archive_container WHERE source_path='ExcelBinOutput/GCGRuleTextDetailExcelConfigData.json' ORDER BY pointer,id"):
  if row['run_id']!=bulk['ingest_run_id']:raise ValueError('Native Bulk target run')
  exact(row['source_path'],row['pointer'],row['native_id'],row['raw_sha256'],'id')
  out.append({'namespace':'gcg_rule_detail','raw_locator':row['native_id'],'target_source_path':row['source_path'],'target_source_pointer':row['pointer'],'target_native_id':row['native_id'],'target_raw_sha256':row['raw_sha256'],'target_family':'native_archive','target_contract':bulk['family_contract_version'],'target_parser':bulk['parser_build_id'],'target_run':row['run_id'],'target_occurrence_id':row['id']})
 b.close()
 if len(out)!=4417+17814+85:raise ValueError('external target denominator')
 out.sort(key=lambda x:(x['namespace'],str(x['raw_locator']),x['target_source_path'],x['target_source_pointer'],x['target_occurrence_id']))
 return out,{'quest_narrative':{'manifest':'manifests/quest-derived-7.1/standalone-candidates-v1.json','manifest_sha256':sha(qn_manifest),'artifact':{'path':str(Path(quest_artifact).resolve()),'size_bytes':qn['artifact']['size_bytes'],'sha256':qn['artifact']['sha256']},'contract':qn['contract'],'parser':qn['parser_build_id'],'run':qn['ingest_run_id'],'required_capabilities':['mainquest_exact_rows','quest_excel_exact_rows']},'native_bulk':{'manifest':'manifests/native-archive-7.1/bulk-runtime.json','manifest_sha256':sha(bulk_manifest),'artifact':{'path':str(Path(bulk_artifact).resolve()),'size_bytes':bulk['artifact']['size_bytes'],'sha256':bulk['artifact']['sha256']},'contract':bulk['family_contract_version'],'parser':bulk['parser_build_id'],'run':bulk['ingest_run_id']}}
def verify(source_root,scope_path,artifact,quest_artifact,bulk_artifact,runtime_manifest_path=None,*,metadata_root=None):
 start=time.perf_counter();scope=json.loads(Path(scope_path).read_text());selection_path=ROOT/scope.get('selection_manifest','');selection=json.loads(selection_path.read_text())
 if scope.get('selection_manifest')!=SELECTION or sha(selection_path)!=scope.get('selection_sha256') or scope['sources']!=selection['sources'] or scope['languages']!=selection['languages'] or scope['textmaps']!=selection['textmaps']:raise ValueError('selection mismatch')
 selected={x['path'] for x in selection['sources']}|{x for locale in selection['textmaps'].values() for x in locale.values()}
 if {x['path'] for x in scope['files']}!=selected:raise ValueError('scope file denominator mismatch')
 check_files(source_root,scope)
 external_rows,dependencies=expected_external(source_root,quest_artifact,bulk_artifact,metadata_root=metadata_root)
 main_quest_proof={'status':'same_snapshot_exact_physical_capability_reuse','source_path':'ExcelBinOutput/MainQuestExcelConfigData.json','physical_rows':sum(x['namespace']=='quest_main' for x in external_rows),'accepted_parser':dependencies['quest_narrative']['parser'],'accepted_run':dependencies['quest_narrative']['run']}
 if scope.get('main_quest_reconciliation')!=main_quest_proof:raise ValueError('MainQuest capability declaration mismatch')
 if scope['external_candidates']!=external_rows:raise ValueError('external candidate namespace mismatch')
 if scope['dependencies']!=dependencies:raise ValueError('accepted dependency manifests/artifacts mismatch')
 impl={p:sha(ROOT/p) for p in ('src/genshin_lore/native_aux_quest_7_1_build.py','src/genshin_lore/event_7_1_source.py','schemas/native-aux-quest-7.1-v1.sql',SELECTION)};parser='naq71-parser:'+hashlib.sha256(json.dumps(impl,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:40];scope_sha=sha(scope_path);run='naq71-run:'+hashlib.sha256((parser+':'+scope_sha).encode()).hexdigest()[:40];seals={x['path']:x for x in scope['files']};records={};wanted=set()
 for spec in scope['sources']:
  rows=list(array_records(Path(source_root)/spec['path']));records[spec['path']]=rows
  for ordinal,row,raw in rows:
   if not isinstance(row,dict):raise ValueError('row shape')
   for owner in spec['text_owners']:
    value=row.get(owner['field'])
    if value is not None and type(value) is not int:raise ValueError('declared owner shape mismatch')
    if type(value) is int:wanted.add(str(value))
 bulk_scope=json.loads((ROOT/'manifests/native-archive-7.1/bulk-scope.json').read_text());overlap={s['path'] for s in scope['sources']} & {s['path'] for s in bulk_scope['sources']}
 if overlap:raise ValueError('same-snapshot Native Bulk physical overlap '+json.dumps(sorted(overlap)))
 maps={}
 for locale in scope['languages']:
  tiers=[]
  for tier in ('main','medium'):
   path=scope['textmaps'][locale][tier];idx=defaultdict(list)
   for ordinal,(key,text) in enumerate(pairs(Path(source_root)/path)):
    if key in wanted:idx[key].append((path,ordinal,'/'+key,text,seals[path]))
   tiers.append(idx)
  maps[locale]=tiers
 expected={t:[] for t in TABLES};states=Counter();roles=Counter();relstates=Counter();containers={};local=defaultdict(list)
 for spec in scope['sources']:
  path=spec['path'];seal=seals[path]
  for ordinal,row,raw in records[path]:
   pointer='/'+str(ordinal);cid=iid(run,'container',path,pointer);nf=spec.get('native_id_field');nv=None if nf is None or row.get(nf) is None else str(row.get(nf));containers[(path,pointer)]=cid;expected['native_archive_container'].append((cid,run,spec['boundary'],'physical_row',path,seal['sha256'],seal['blob'],ordinal,pointer,nf,nv,raw,hashlib.sha256(raw.encode()).hexdigest(),None,None,None,None,None))
   if nf and nv is not None:local[(path,nf,nv)].append({'target_source_path':path,'target_source_pointer':pointer,'target_native_id':nv,'target_family':scope['family'],'target_contract':scope['family_contract_version'],'target_parser':parser,'target_run':run,'target_occurrence_id':cid})
   for owner in spec['text_owners']:
    field=owner['field'];value=row.get(field)
    if type(value) is not int:continue
    locator=str(value);roles[owner['owner_role_status']]+=3
    for locale in scope['languages']:
     main=maps[locale][0].get(locator,[]);medium=maps[locale][1].get(locator,[]);hits=main or medium;status='missing' if not hits else 'resolved_main' if main and len(hits)==1 else 'resolved_medium' if medium and len(hits)==1 else 'ambiguous';states[status]+=1;sid=iid(run,'slot',path,pointer,field,locale);expected['native_archive_slot'].append((sid,cid,field,owner['owner_role_status'],locale,locator,status,len(hits)))
     for tp,pair,pp,text,ts in hits:expected['native_archive_text'].append((iid(run,'text',sid,tp,pair),sid,tp,ts['sha256'],ts['blob'],pair,pp,text,hashlib.sha256(text.encode()).hexdigest()))
 external=defaultdict(list)
 for x in external_rows:external[(x['namespace'],str(x['raw_locator']))].append(x)
 for spec in scope['sources']:
  path=spec['path']
  for ordinal,row,raw in records[path]:
   pointer='/'+str(ordinal);cid=containers[(path,pointer)]
   for rel in spec.get('relations',[]):
    for relative,value in relation_values(row,rel):
     locator=str(value);target=rel['target'];hits=local[(target['path'],target['native_id_field'],locator)] if target['mode']=='local' else external[(target['namespace'],locator)];hits=sorted(hits,key=lambda x:(x['target_source_path'],x['target_source_pointer'],x['target_run'],x['target_occurrence_id']));status='missing' if not hits else 'resolved_unique' if len(hits)==1 else 'ambiguous';relstates[status]+=1;sp=pointer+'/'+relative;rid=iid(run,'relation',path,sp,rel['relation_kind']);expected['native_archive_relation'].append((rid,cid,rel['relation_kind'],rel['field'],sp,locator,rel['candidate_namespace'],status,len(hits),1))
     for x in hits:expected['native_archive_candidate'].append((iid(run,'candidate',rid,x['target_occurrence_id']),rid,x['target_source_path'],x['target_source_pointer'],x.get('target_native_id'),x['target_family'],x['target_contract'],x['target_parser'],x['target_run'],x['target_occurrence_id']))
 for table in TABLES:expected[table].sort()
 c=sqlite3.connect(artifact);s=sqlite3.connect(':memory:');s.execute('PRAGMA foreign_keys=ON');s.executescript((ROOT/'schemas/native-aux-quest-7.1-v1.sql').read_text());schema_proof=normalized_schema(s)
 if normalized_schema(c)!=schema_proof:raise ValueError('schema structure mismatch')
 if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('SQLite integrity/FK')
 if any(Path(str(artifact)+suffix).exists() for suffix in ('-wal','-shm','-journal')):raise ValueError('SQLite sidecars')
 for table in TABLES:
  n=len(c.execute('SELECT * FROM '+table+' LIMIT 0').description);got=c.execute('SELECT * FROM '+table+' ORDER BY '+','.join(str(i+1) for i in range(n))).fetchall()
  if got!=expected[table]:raise ValueError('business row mismatch '+table)
 counts={t:len(expected[t]) for t in TABLES};release=json.loads(c.execute('SELECT manifest_json FROM native_archive_release WHERE id=1').fetchone()[0]);c.close();s.close();want={'contract':scope['family_contract_version'],'run_id':run,'parser_build_id':parser,'scope_sha256':scope_sha,'implementation':impl,'snapshot_id':scope['snapshot_id'],'counts':counts,'text_state_counts':dict(sorted(states.items())),'owner_role_counts':dict(sorted(roles.items())),'relation_state_counts':dict(sorted(relstates.items())),'physical_rows':counts['native_archive_container'],'projection_reuse_rows':0,'new_wave_physical_rows':counts['native_archive_container'],'new_projection_reuse_rows':0,'searchable_text_candidates':counts['native_archive_text'],'template_text_preserved_verbatim':True,'historical_body_copied':False,'corpus_complete':True,'full_7_1':False}
 if release!=want:raise ValueError('release mismatch')
 logical={t:hashlib.sha256('\n'.join(json.dumps(list(x),ensure_ascii=False,separators=(',',':')) for x in expected[t]).encode()).hexdigest() for t in TABLES};runtime=json.loads(Path(runtime_manifest_path).read_text()) if runtime_manifest_path else None
 if runtime and (runtime['release']!=release or runtime['artifact']!={'size_bytes':Path(artifact).stat().st_size,'sha256':sha(artifact)} or runtime.get('logical_seals')!=logical or runtime.get('schema_structure_normalized')!=schema_proof or runtime.get('schema_structure_match') is not True or runtime.get('write_once_structure_match') is not True or runtime.get('unexpected_authoritative_objects')!=[]):raise ValueError('runtime mismatch')
 elapsed=time.perf_counter()-start;return {'status':'pass','actual_bytes_git_blob_match':True,'identity_independently_reconstructed':True,'selection_independently_bound':True,'declared_owner_shape_proof':'all selected owner values are scalar integer or null','declared_owner_non_scalar_count':0,'accepted_scope_physical_overlap_count':0,'accepted_scope_physical_overlap':{},'main_quest_reconciliation_independently_reconstructed':main_quest_proof,'external_candidate_namespace_independently_reconstructed':True,'schema_structure_match':True,'write_once_structure_match':True,'unexpected_authoritative_objects':[],'schema_structure_normalized':schema_proof,'verified_artifact':{'size_bytes':Path(artifact).stat().st_size,'sha256':sha(artifact)},'snapshot_id':scope['snapshot_id'],'scope_sha256':scope_sha,'implementation':impl,'parser_build_id':parser,'run_id':run,'contract':scope['family_contract_version'],'counts':counts,'text_states':dict(sorted(states.items())),'owner_roles':dict(sorted(roles.items())),'relation_states':dict(sorted(relstates.items())),'full_business_column_equality':True,'no_extra_rows':True,'logical_seals':logical,'performance':{'full_verifier_seconds':elapsed,'rows_per_second':counts['native_archive_container']/elapsed}}
