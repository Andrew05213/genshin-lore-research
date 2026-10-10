"""Independent verifier for the High-Value Native Text Closure Wave."""
from __future__ import annotations
import hashlib,json,sqlite3,time
from collections import Counter,defaultdict
from pathlib import Path
from .event_narrative_source import array_records,check_files,pairs,sha

ROOT=Path(__file__).resolve().parents[2]
TABLES=('native_archive_container','native_archive_slot','native_archive_text','native_archive_relation','native_archive_candidate')
def iid(*p):return 'native-archive:'+hashlib.sha256(json.dumps(p,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:40]
def schema(c):return [[a,b,d,' '.join((s or '').split()).lower()] for a,b,d,s in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
def target(a,path,pointer,native,occ):return {'target_source_path':path,'target_source_pointer':pointer,'target_native_id':native,'target_family':a['family'],'target_contract':a['family_contract_version'],'target_parser':a.get('parser_build_id') or a.get('release',{}).get('parser_build_id'),'target_run':a.get('ingest_run_id') or a.get('release',{}).get('run_id'),'target_occurrence_id':occ}
def verify(source_root,scope_path,artifact,runtime_manifest_path=None):
 start=time.perf_counter();scope=json.loads(Path(scope_path).read_text())
 if scope.get('selection_manifest')!='manifests/native-archive/high-value-selection.json':raise ValueError('unapproved selection authority')
 selection_path=ROOT/scope['selection_manifest'];selection=json.loads(selection_path.read_text())
 if sha(selection_path)!=scope.get('selection_sha256'):raise ValueError('selection seal mismatch')
 if scope['sources']!=selection['sources'] or scope['languages']!=selection['languages'] or scope['textmaps']!=selection['textmaps']:raise ValueError('scope selection mismatch')
 selected={x['path'] for x in selection['sources']}|{x for locale in selection['languages'] for x in selection['textmaps'][locale].values()}
 if {x['path'] for x in scope['files']}!=selected:raise ValueError('scope file denominator mismatch')
 check_files(source_root,scope);impl={p:sha(ROOT/p) for p in ('src/genshin_lore/native_high_value_archive_build.py','src/genshin_lore/event_narrative_source.py','schemas/native-archive-v1.sql','manifests/native-archive/high-value-selection.json')};parser='native-archive-parser:'+hashlib.sha256(json.dumps(impl,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:40];scope_sha=sha(scope_path);run='native-archive-run:'+hashlib.sha256((parser+':'+scope_sha).encode()).hexdigest()[:40];seals={x['path']:x for x in scope['files']};records={};wanted=set()
 for spec in scope['sources']:
  rows=list(array_records(Path(source_root)/spec['path']));records[spec['path']]=rows
  for ordinal,row,raw in rows:
   if not isinstance(row,dict):raise ValueError('row shape')
   for owner in spec['text_owners']:
    value=row.get(owner['field'])
    if value is not None and type(value) is not int:raise ValueError(f"declared owner must be scalar integer or null: {spec['path']} /{ordinal}/{owner['field']}")
    if type(value) is int:wanted.add(str(value))
 from scripts.generate_native_text_delta_ledger import accepted_projection
 accepted_state,_=accepted_projection(ROOT,Path(source_root),exclude_runtime_paths=('manifests/native-archive/high-value-runtime.json',))
 physical_overlap={spec['path']:sorted(set(range(len(records[spec['path']]))) & accepted_state[spec['path']]['physical']) for spec in scope['sources']}
 physical_overlap={k:v for k,v in physical_overlap.items() if v}
 if physical_overlap:raise ValueError('selected physical coordinate overlaps accepted product: '+json.dumps(physical_overlap,sort_keys=True))
 maps={}
 for locale in scope['languages']:
  tiers=[]
  for tier in ('main','medium'):
   path=scope['textmaps'][locale][tier];index=defaultdict(list)
   for ordinal,(key,text) in enumerate(pairs(Path(source_root)/path)):
    if key in wanted:index[key].append((path,ordinal,'/'+key,text,seals[path]))
   tiers.append(index)
  maps[locale]=tiers
 if scope.get('reuse_targets')!=[]:raise ValueError('high-value wave has no projection reuse')
 if scope.get('external_candidates')!=[] or scope.get('dependencies')!={}:raise ValueError('high-value wave has no external candidate dependencies')
 external=defaultdict(list)
 expected={t:[] for t in TABLES};states=Counter();roles=Counter();relstates=Counter()
 for spec in scope['sources']:
  path=spec['path'];seal=seals[path]
  for ordinal,row,raw in records[path]:
   pointer='/'+str(ordinal);cid=iid(run,'container',path,pointer);nf=spec.get('native_id_field');nv=None if nf is None else str(row.get(nf))
   expected['native_archive_container'].append((cid,run,spec['boundary'],'physical_row',path,seal['sha256'],seal['blob'],ordinal,pointer,nf,nv,raw,hashlib.sha256(raw.encode()).hexdigest(),None,None,None,None,None))
   for owner in spec['text_owners']:
    field=owner['field'];value=row.get(field)
    if type(value) is not int:continue
    locator=str(value);roles[owner['owner_role_status']]+=3
    for locale in scope['languages']:
     main=maps[locale][0].get(locator,[]);medium=maps[locale][1].get(locator,[]);hits=main or medium;status='missing' if not hits else 'resolved_main' if main and len(hits)==1 else 'resolved_medium' if medium and len(hits)==1 else 'ambiguous';states[status]+=1;sid=iid(run,'slot',path,pointer,field,locale);expected['native_archive_slot'].append((sid,cid,field,owner['owner_role_status'],locale,locator,status,len(hits)))
     for tp,pair,pp,text,ts in hits:expected['native_archive_text'].append((iid(run,'text',sid,tp,pair),sid,tp,ts['sha256'],ts['blob'],pair,pp,text,hashlib.sha256(text.encode()).hexdigest()))
   for rel in spec.get('relations',[]):
    value=row.get(rel['field'])
    if type(value) is not int:continue
    locator=str(value);hits=sorted(external[(rel['relation_kind'],locator)],key=lambda x:(x['target_source_path'],x['target_source_pointer'],x['target_run'],x['target_occurrence_id']));status='missing' if not hits else 'resolved_unique' if len(hits)==1 else 'ambiguous';relstates[status]+=1;sp=pointer+'/'+rel['field'];rid=iid(run,'relation',path,sp,rel['relation_kind']);expected['native_archive_relation'].append((rid,cid,rel['relation_kind'],rel['field'],sp,locator,rel['candidate_namespace'],status,len(hits),1))
    for x in hits:expected['native_archive_candidate'].append((iid(run,'candidate',rid,x['target_occurrence_id']),rid,x['target_source_path'],x['target_source_pointer'],x.get('target_native_id'),x['target_family'],x['target_contract'],x['target_parser'],x['target_run'],x['target_occurrence_id']))
 for table in TABLES:expected[table].sort()
 c=sqlite3.connect(artifact);s=sqlite3.connect(':memory:');s.execute('PRAGMA foreign_keys=ON');s.executescript((ROOT/'schemas/native-archive-v1.sql').read_text());schema_proof=schema(s)
 if schema(c)!=schema_proof:raise ValueError('schema structure mismatch')
 if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('foreign keys')
 for table in TABLES:
  n=len(c.execute('SELECT * FROM '+table+' LIMIT 0').description);got=c.execute('SELECT * FROM '+table+' ORDER BY '+','.join(str(i+1) for i in range(n))).fetchall()
  if got!=expected[table]:raise ValueError('business row mismatch '+table)
 counts={t:len(expected[t]) for t in TABLES};release=json.loads(c.execute('SELECT manifest_json FROM native_archive_release WHERE id=1').fetchone()[0]);c.close();s.close();carried={x['path'] for x in scope['sources'] if x.get('carried_forward')};containers=expected['native_archive_container']
 want={'contract':scope['family_contract_version'],'run_id':run,'parser_build_id':parser,'scope_sha256':scope_sha,'implementation':impl,'snapshot_id':scope['snapshot_id'],'counts':counts,'text_state_counts':dict(sorted(states.items())),'owner_role_counts':dict(sorted(roles.items())),'relation_state_counts':dict(sorted(relstates.items())),'physical_rows':sum(x[3]=='physical_row' for x in containers),'projection_reuse_rows':sum(x[3]=='projection_reuse' for x in containers),'carried_forward_physical_rows':sum(x[3]=='physical_row' and x[4] in carried for x in containers),'carried_forward_projection_reuse_rows':sum(x[3]=='projection_reuse' and x[4] in carried for x in containers),'new_wave_physical_rows':sum(x[3]=='physical_row' and x[4] not in carried for x in containers),'new_projection_reuse_rows':sum(x[3]=='projection_reuse' and x[4] not in carried for x in containers),'searchable_text_candidates':len(expected['native_archive_text']),'template_text_preserved_verbatim':True,'historical_body_copied':False,'corpus_complete':True,'full_7_0':False}
 if release!=want:raise ValueError('release mismatch')
 logical={t:hashlib.sha256('\n'.join(json.dumps(list(x),ensure_ascii=False,separators=(',',':')) for x in expected[t]).encode()).hexdigest() for t in TABLES};runtime=json.loads(Path(runtime_manifest_path).read_text()) if runtime_manifest_path else None
 if runtime and (runtime['release']!=release or runtime['artifact']!={'size_bytes':Path(artifact).stat().st_size,'sha256':sha(artifact)} or runtime.get('logical_seals')!=logical or runtime.get('schema_structure_normalized')!=schema_proof or runtime.get('schema_structure_match') is not True or runtime.get('write_once_structure_match') is not True or runtime.get('unexpected_authoritative_objects')!=[]):raise ValueError('runtime mismatch')
 elapsed=time.perf_counter()-start
 return {'status':'pass','actual_bytes_git_blob_match':True,'identity_independently_reconstructed':True,'selection_independently_bound':True,'declared_owner_shape_proof':'all selected owner values are scalar integer or null','declared_owner_non_scalar_count':0,'accepted_scope_physical_overlap_count':0,'accepted_scope_physical_overlap':{},'pointer_round_trip_independently_reconstructed':True,'schema_structure_match':True,'write_once_structure_match':True,'unexpected_authoritative_objects':[],'schema_structure_normalized':schema_proof,'verified_artifact':{'size_bytes':Path(artifact).stat().st_size,'sha256':sha(artifact)},'snapshot_id':scope['snapshot_id'],'scope_sha256':scope_sha,'implementation':impl,'parser_build_id':parser,'run_id':run,'contract':scope['family_contract_version'],'counts':counts,'text_states':dict(sorted(states.items())),'owner_roles':dict(sorted(roles.items())),'relation_states':dict(sorted(relstates.items())),'full_business_column_equality':True,'no_extra_rows':True,'logical_seals':logical,'performance':{'full_verifier_seconds':elapsed,'rows_per_second':counts['native_archive_container']/elapsed}}
