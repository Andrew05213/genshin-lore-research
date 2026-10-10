"""Independent verifier for Auxiliary Text Sources + Quest Delta Bulk Wave."""
from __future__ import annotations
import hashlib,json,sqlite3,subprocess,time
from collections import Counter,defaultdict
from pathlib import Path
from .event_narrative_source import array_records,check_files,pairs,sha
ROOT=Path(__file__).resolve().parents[2];TABLES=('native_archive_container','native_archive_slot','native_archive_text','native_archive_relation','native_archive_candidate');SELECTION='manifests/native-archive/aux-quest-selection.json'
def iid(*p):return 'native-archive:'+hashlib.sha256(json.dumps(p,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:40]
def normalized_schema(c):return [[a,b,d,' '.join((s or '').split()).lower()] for a,b,d,s in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
def exact_component(runtime_path):
 current=json.loads((ROOT/'manifests/product/current.json').read_text());bundle=json.loads((ROOT/current['explicit_product_bundle']).read_text());hits=[x for x in bundle['additional_family_components'] if x.get('runtime_manifest')==runtime_path]
 if len(hits)!=1:raise ValueError('accepted dependency component missing')
 runtime=json.loads((ROOT/runtime_path).read_text());component=hits[0]
 if component['artifact']!=runtime['artifact'] or component['ingest_run_id']!=runtime['release']['run_id'] or component['parser_build_id']!=runtime['release']['parser_build_id']:raise ValueError('accepted dependency identity mismatch')
 return component,runtime
def check_artifact(path,identity):
 if Path(path).stat().st_size!=identity['size_bytes'] or sha(path)!=identity['sha256']:raise ValueError('historical dependency artifact mismatch')
def relation_values(row,spec):
 value=row.get(spec['field']);shape=spec.get('shape','scalar')
 if shape in ('scalar','scalar_optional'):return [] if value is None else [(spec['field'],value)]
 if shape=='array' and isinstance(value,list):return [(f"{spec['field']}/{i}",x) for i,x in enumerate(value) if x is not None]
 if value is not None:raise ValueError('declared relation shape mismatch')
 return []
def main_quest_reconciliation(source_root,quest_artifact,component,runtime):
 path='ExcelBinOutput/MainQuestExcelConfigData.json';source=Path(source_root)/path
 pinned_blob=subprocess.check_output(['git','-C',str(source_root),'rev-parse',scope_pin(runtime)+':'+path],text=True).strip()
 from .event_narrative_source import git_blob
 if git_blob(source)!=pinned_blob:raise ValueError('MainQuest actual bytes/pinned blob mismatch')
 rows=list(array_records(source));owners=('titleTextMapHash','descTextMapHash','unlockDescTextMapHash');locales=('CHS','EN','JP')
 q=sqlite3.connect(f'file:{Path(quest_artifact).resolve()}?mode=ro&immutable=1',uri=True);q.row_factory=sqlite3.Row
 got=list(q.execute("SELECT * FROM quest_narrative_container WHERE run_id=? AND kind='main_quest_row' ORDER BY source_ordinal",(runtime['release']['run_id'],)))
 if len(rows)!=4372 or len(got)!=len(rows):raise ValueError('MainQuest physical denominator mismatch')
 slot_count=0
 for (ordinal,row,raw),actual in zip(rows,got):
  projected_raw=json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':'))
  expected=(path,ordinal,'/'+str(ordinal),str(row['id']),pinned_blob,hashlib.sha256(projected_raw.encode()).hexdigest(),projected_raw)
  observed=(actual['source_path'],actual['source_ordinal'],actual['source_pointer'],actual['native_id'],actual['source_blob_oid'],actual['raw_sha256'],actual['raw_json'])
  if observed!=expected:raise ValueError('MainQuest physical coordinate mismatch')
  slots=[tuple(x) for x in q.execute('SELECT owner_field,locale,raw_locator FROM quest_narrative_text_slot WHERE container_id=? ORDER BY owner_field,locale',(actual['id'],))]
  expected_slots=sorted((owner,locale,str(row[owner])) for owner in owners for locale in locales)
  if slots!=expected_slots:raise ValueError('MainQuest owner projection mismatch')
  slot_count+=len(slots)
 q.close();proof={'status':'no_delta_complete_reuse','source_path':path,'pinned_blob':pinned_blob,'physical_rows':len(rows),'owner_occurrences':len(rows)*len(owners),'locale_slots':slot_count,'owner_fields':list(owners),'physical_coordinate_equality':True,'owner_projection_equality':True,'accepted_family':runtime['family'],'accepted_contract':runtime['family_contract_version'],'accepted_parser':runtime['release']['parser_build_id'],'accepted_run':runtime['release']['run_id'],'artifact':component['artifact']}
 return proof
def scope_pin(runtime):
 snapshot=runtime['release']['snapshot_id'];prefix='anime-game-data:7.0:'
 if not snapshot.startswith(prefix):raise ValueError('Quest Narrative snapshot identity')
 return snapshot[len(prefix):]
def expected_external(quest_artifact,bulk_artifact):
 qc,qr=exact_component('manifests/quest-narrative/runtime.json');bc,br=exact_component('manifests/native-archive/bulk-runtime.json');check_artifact(quest_artifact,qc['artifact']);check_artifact(bulk_artifact,bc['artifact']);out=[]
 q=sqlite3.connect(f'file:{Path(quest_artifact).resolve()}?mode=ro&immutable=1',uri=True);q.row_factory=sqlite3.Row
 for namespace,kind in (('quest_main','main_quest_row'),('quest_step','quest_step_row')):
  for row in q.execute('SELECT id,source_path,source_pointer,native_id FROM quest_narrative_container WHERE run_id=? AND kind=? ORDER BY source_path,source_pointer,id',(qr['release']['run_id'],kind)):out.append({'namespace':namespace,'raw_locator':row['native_id'],'target_source_path':row['source_path'],'target_source_pointer':row['source_pointer'],'target_native_id':row['native_id'],'target_family':qr['family'],'target_contract':qr['family_contract_version'],'target_parser':qr['release']['parser_build_id'],'target_run':qr['release']['run_id'],'target_occurrence_id':row['id']})
 q.close();b=sqlite3.connect(f'file:{Path(bulk_artifact).resolve()}?mode=ro&immutable=1',uri=True);b.row_factory=sqlite3.Row
 for row in b.execute("SELECT id,source_path,pointer,native_id FROM native_archive_container WHERE run_id=? AND source_path='ExcelBinOutput/GCGRuleTextDetailExcelConfigData.json' ORDER BY pointer,id",(br['release']['run_id'],)):out.append({'namespace':'gcg_rule_detail','raw_locator':row['native_id'],'target_source_path':row['source_path'],'target_source_pointer':row['pointer'],'target_native_id':row['native_id'],'target_family':br['family'],'target_contract':br['family_contract_version'],'target_parser':br['release']['parser_build_id'],'target_run':br['release']['run_id'],'target_occurrence_id':row['id']})
 b.close();return sorted(out,key=lambda x:(x['namespace'],str(x['raw_locator']),x['target_source_path'],x['target_source_pointer'],x['target_occurrence_id']))
def verify(source_root,scope_path,artifact,quest_artifact,bulk_artifact,runtime_manifest_path=None):
 start=time.perf_counter();scope=json.loads(Path(scope_path).read_text());selection_path=ROOT/scope.get('selection_manifest','');selection=json.loads(selection_path.read_text())
 if scope.get('selection_manifest')!=SELECTION or sha(selection_path)!=scope.get('selection_sha256') or scope['sources']!=selection['sources'] or scope['languages']!=selection['languages'] or scope['textmaps']!=selection['textmaps']:raise ValueError('selection mismatch')
 selected={x['path'] for x in selection['sources']}|{x for locale in selection['textmaps'].values() for x in locale.values()}
 if {x['path'] for x in scope['files']}!=selected:raise ValueError('scope file denominator mismatch')
 check_files(source_root,scope);qc,qr=exact_component('manifests/quest-narrative/runtime.json');main_quest_proof=main_quest_reconciliation(source_root,quest_artifact,qc,qr)
 declared_main=scope.get('main_quest_reconciliation',{})
 for declared_key,proof_key in (('source_path','source_path'),('physical_rows','physical_rows'),('text_owner_occurrences','owner_occurrences'),('accepted_family','accepted_family'),('accepted_contract','accepted_contract'),('accepted_parser','accepted_parser'),('accepted_run','accepted_run'),('status','status')):
  if declared_main.get(declared_key)!=main_quest_proof[proof_key]:raise ValueError('MainQuest reconciliation declaration mismatch')
 external_rows=expected_external(quest_artifact,bulk_artifact)
 if scope['external_candidates']!=external_rows:raise ValueError('external candidate namespace mismatch')
 for name,dep in scope['dependencies'].items():
  if sha(ROOT/dep['runtime_manifest'])!=dep['runtime_manifest_sha256']:raise ValueError('dependency runtime manifest drift '+name)
 impl={p:sha(ROOT/p) for p in ('src/genshin_lore/native_aux_quest_archive_build.py','src/genshin_lore/native_text_bulk_archive_build.py','src/genshin_lore/event_narrative_source.py','schemas/native-archive-v1.sql',SELECTION)};parser='native-archive-parser:'+hashlib.sha256(json.dumps(impl,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:40];scope_sha=sha(scope_path);run='native-archive-run:'+hashlib.sha256((parser+':'+scope_sha).encode()).hexdigest()[:40];seals={x['path']:x for x in scope['files']};records={};wanted=set()
 for spec in scope['sources']:
  rows=list(array_records(Path(source_root)/spec['path']));records[spec['path']]=rows
  for ordinal,row,raw in rows:
   if not isinstance(row,dict):raise ValueError('row shape')
   for owner in spec['text_owners']:
    value=row.get(owner['field'])
    if value is not None and type(value) is not int:raise ValueError('declared owner shape mismatch')
    if type(value) is int:wanted.add(str(value))
 from scripts.generate_native_text_delta_ledger import accepted_projection
 state,_=accepted_projection(ROOT,Path(source_root),exclude_runtime_paths=('manifests/native-archive/aux-quest-runtime.json',));overlap={s['path']:sorted(state[s['path']]['physical']) for s in scope['sources'] if state[s['path']]['physical']}
 if overlap:raise ValueError('historical physical overlap '+json.dumps(overlap,sort_keys=True))
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
 c=sqlite3.connect(artifact);s=sqlite3.connect(':memory:');s.execute('PRAGMA foreign_keys=ON');s.executescript((ROOT/'schemas/native-archive-v1.sql').read_text());schema_proof=normalized_schema(s)
 if normalized_schema(c)!=schema_proof:raise ValueError('schema structure mismatch')
 if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('foreign keys')
 for table in TABLES:
  n=len(c.execute('SELECT * FROM '+table+' LIMIT 0').description);got=c.execute('SELECT * FROM '+table+' ORDER BY '+','.join(str(i+1) for i in range(n))).fetchall()
  if got!=expected[table]:raise ValueError('business row mismatch '+table)
 counts={t:len(expected[t]) for t in TABLES};release=json.loads(c.execute('SELECT manifest_json FROM native_archive_release WHERE id=1').fetchone()[0]);c.close();s.close();want={'contract':scope['family_contract_version'],'run_id':run,'parser_build_id':parser,'scope_sha256':scope_sha,'implementation':impl,'snapshot_id':scope['snapshot_id'],'counts':counts,'text_state_counts':dict(sorted(states.items())),'owner_role_counts':dict(sorted(roles.items())),'relation_state_counts':dict(sorted(relstates.items())),'physical_rows':counts['native_archive_container'],'projection_reuse_rows':0,'new_wave_physical_rows':counts['native_archive_container'],'new_projection_reuse_rows':0,'searchable_text_candidates':counts['native_archive_text'],'template_text_preserved_verbatim':True,'historical_body_copied':False,'corpus_complete':True,'full_7_0':False}
 if release!=want:raise ValueError('release mismatch')
 logical={t:hashlib.sha256('\n'.join(json.dumps(list(x),ensure_ascii=False,separators=(',',':')) for x in expected[t]).encode()).hexdigest() for t in TABLES};runtime=json.loads(Path(runtime_manifest_path).read_text()) if runtime_manifest_path else None
 if runtime and (runtime['release']!=release or runtime['artifact']!={'size_bytes':Path(artifact).stat().st_size,'sha256':sha(artifact)} or runtime.get('logical_seals')!=logical or runtime.get('schema_structure_normalized')!=schema_proof or runtime.get('schema_structure_match') is not True or runtime.get('write_once_structure_match') is not True or runtime.get('unexpected_authoritative_objects')!=[]):raise ValueError('runtime mismatch')
 elapsed=time.perf_counter()-start;return {'status':'pass','actual_bytes_git_blob_match':True,'identity_independently_reconstructed':True,'selection_independently_bound':True,'declared_owner_shape_proof':'all selected owner values are scalar integer or null','declared_owner_non_scalar_count':0,'accepted_scope_physical_overlap_count':0,'accepted_scope_physical_overlap':{},'main_quest_reconciliation_independently_reconstructed':main_quest_proof,'external_candidate_namespace_independently_reconstructed':True,'schema_structure_match':True,'write_once_structure_match':True,'unexpected_authoritative_objects':[],'schema_structure_normalized':schema_proof,'verified_artifact':{'size_bytes':Path(artifact).stat().st_size,'sha256':sha(artifact)},'snapshot_id':scope['snapshot_id'],'scope_sha256':scope_sha,'implementation':impl,'parser_build_id':parser,'run_id':run,'contract':scope['family_contract_version'],'counts':counts,'text_states':dict(sorted(states.items())),'owner_roles':dict(sorted(roles.items())),'relation_states':dict(sorted(relstates.items())),'full_business_column_equality':True,'no_extra_rows':True,'logical_seals':logical,'performance':{'full_verifier_seconds':elapsed,'rows_per_second':counts['native_archive_container']/elapsed}}
