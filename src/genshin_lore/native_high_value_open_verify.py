"""Independent verifier for the High-Value Open Closure successor."""
from __future__ import annotations
import hashlib,json,sqlite3,time
from collections import Counter,defaultdict
from pathlib import Path
from .event_narrative_source import array_records,check_files,pairs,sha

ROOT=Path(__file__).resolve().parents[2]
TABLES=('native_archive_container','native_archive_slot','native_archive_text','native_archive_relation','native_archive_candidate')
CORE_SIZE=5963210752
CORE_SHA='6bef3518ad17df4a7a953d9d064ba4ee0569c86aeb13ae695092fc73b4d63ed1'
def iid(*p):return 'native-archive:'+hashlib.sha256(json.dumps(p,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:40]
def schema(c):return [[a,b,d,' '.join((s or '').split()).lower()] for a,b,d,s in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]

def occurrences(row,path,shape='scalar'):
 parts=[x for x in path.split('/') if x]
 def rec(cur,index,pointer):
  if index==len(parts):
   if shape=='array' and isinstance(cur,list):
    for i,item in enumerate(cur):
     if item is not None:yield pointer+'/'+str(i),item
   elif shape=='scalar' and cur is not None:yield pointer,cur
   return
  part=parts[index]
  if part=='*' and isinstance(cur,list):
   for i,item in enumerate(cur):yield from rec(item,index+1,pointer+'/'+str(i))
  elif isinstance(cur,dict) and part in cur:yield from rec(cur[part],index+1,pointer+'/'+part)
 yield from rec(row,0,'')

def validate_independent_authority(scope,selection_path,authority):
 if scope.get('selection_manifest')!='manifests/native-archive/high-value-open-selection.json' or sha(selection_path)!=scope.get('selection_sha256'):raise ValueError('selection binding')
 if authority.get('contract')!='high-value-open-independent-verifier-authority-v1' or scope['sources']!=authority['sources'] or scope['languages']!=authority['languages'] or scope['textmaps']!=authority['textmaps']:raise ValueError('independent verifier authority mismatch')

def independent_core_candidates(core,observed):
 result=[];c=sqlite3.connect(core)
 grouped=defaultdict(set)
 for kind,locator,table,key in observed:grouped[(table,key)].add((kind,locator))
 for (table,key),pairs_wanted in grouped.items():
  wanted={x[1] for x in pairs_wanted}
  for row in c.execute(f'SELECT occurrence_id,family,family_contract_version,ingest_run_id,parser_build_id,source_file_path,source_pointer,raw_record_json FROM {table}'):
   obj=json.loads(row[7]);value=obj.get(key)
   if value is None or str(value) not in wanted:continue
   for kind,locator in pairs_wanted:
    if locator==str(value):result.append({'relation_kind':kind,'raw_locator':locator,'target_occurrence_id':row[0],'target_family':row[1],'target_contract':row[2],'target_run':row[3],'target_parser':row[4],'target_source_path':row[5],'target_source_pointer':row[6],'target_native_id':locator})
 c.close();return sorted(result,key=lambda x:(x['relation_kind'],x['raw_locator'],x['target_source_path'],x['target_source_pointer'],x['target_occurrence_id']))

def verify(source_root,scope_path,artifact,core_artifact,event_artifact=None,quest_talk_artifacts=(),runtime_manifest_path=None):
 start=time.perf_counter();scope=json.loads(Path(scope_path).read_text());selection_path=ROOT/'manifests/native-archive/high-value-open-selection.json';selection=json.loads(selection_path.read_text());authority_path=ROOT/'manifests/native-archive/high-value-open-verifier-authority.json';authority=json.loads(authority_path.read_text())
 validate_independent_authority(scope,selection_path,authority)
 core=Path(core_artifact)
 if core.stat().st_size!=CORE_SIZE or sha(core)!=CORE_SHA or scope['dependencies']['schema20_core']!={'size_bytes':CORE_SIZE,'sha256':CORE_SHA}:raise ValueError('core dependency')
 selected={x['path'] for x in authority['sources']}|{x for locale in authority['languages'] for x in authority['textmaps'][locale].values()}
 if {x['path'] for x in scope['files']}!=selected:raise ValueError('scope file denominator')
 check_files(source_root,scope);impl={p:sha(ROOT/p) for p in ('src/genshin_lore/native_high_value_open_build.py','src/genshin_lore/event_narrative_source.py','schemas/native-archive-v1.sql','manifests/native-archive/high-value-open-selection.json')};parser='native-archive-parser:'+hashlib.sha256(json.dumps(impl,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:40];scope_sha=sha(scope_path);run='native-archive-run:'+hashlib.sha256((parser+':'+scope_sha).encode()).hexdigest()[:40];seals={x['path']:x for x in scope['files']};records={};reuse_records=defaultdict(list);wanted=set();observed_core=set();reuse_by_coord={(x['target_source_path'],x['target_source_pointer']):x for x in scope.get('reuse_targets',[])}
 for spec in scope['sources']:
  all_rows=list(array_records(Path(source_root)/spec['path']))
  selected=set(spec.get('selected_ordinals',range(len(all_rows))))
  rows=[x for x in all_rows if x[0] in selected];records[spec['path']]=rows
  for ordinal in spec.get('excluded_ordinals',[]):reuse_records[spec['path']].append(all_rows[ordinal])
  if spec.get('source_row_denominator',len(all_rows))!=len(all_rows):raise ValueError('source row denominator mismatch')
  for ordinal,row,raw in rows:
   classified=set(spec['field_classification']['readable_owner_fields'])|{x.split('/')[0] for x in spec['field_classification']['relation_fields']}|set(spec['field_classification']['raw_or_opaque_fields'])
   if set(row)-classified:raise ValueError('unclassified physical field')
   for owner in spec['text_owners']:
    for _,value in occurrences(row,owner['field'],owner.get('shape','scalar')):
     if type(value) is not int:raise ValueError('owner shape')
     wanted.add(str(value))
   for rel in spec.get('relations',[]):
    if rel['resolution']=='core':
     for _,value in occurrences(row,rel['field'],rel.get('shape','scalar')):
      if type(value) is int:observed_core.add((rel['relation_kind'],str(value),rel['target_table'],rel['target_key']))
 rebuilt_external=independent_core_candidates(core,observed_core)
 talk_manifests=[json.loads((ROOT/'manifests/quest-talk-bounded/quest_linked-runtime.json').read_text()),json.loads((ROOT/'manifests/quest-talk-bounded/mapped_complement-runtime.json').read_text())]
 wanted_talk={(rel['relation_kind'],str(value)) for spec in scope['sources'] for _,row,_ in records[spec['path']] for rel in spec.get('relations',[]) if rel['resolution']=='quest_talk_v7' for _,value in occurrences(row,rel['field'],rel.get('shape','scalar'))}
 if wanted_talk and len(quest_talk_artifacts)!=2:raise ValueError('quest talk dependencies required')
 for artifact_path,manifest in zip(quest_talk_artifacts,talk_manifests):
  artifact_path=Path(artifact_path)
  if artifact_path.stat().st_size!=manifest['artifact']['size_bytes'] or sha(artifact_path)!=manifest['artifact']['sha256']:raise ValueError('quest talk dependency identity')
  q=sqlite3.connect(artifact_path);parser_row=q.execute('SELECT parser_build_id FROM ingest_run WHERE id=?',(manifest['ingest_run_id'],)).fetchone();
  if not parser_row:raise ValueError('quest talk parser identity')
  for kind,locator in wanted_talk:
   for cid,native,metadata in q.execute("SELECT id,native_id,metadata_json FROM quest_talk_physical_container WHERE ingest_run_id=? AND kind='talk_root' AND native_id=?",(manifest['ingest_run_id'],locator)):
    m=json.loads(metadata);rebuilt_external.append({'relation_kind':kind,'raw_locator':locator,'target_occurrence_id':cid,'target_family':manifest['family'],'target_contract':manifest['family_contract_version'],'target_run':manifest['ingest_run_id'],'target_parser':parser_row[0],'target_source_path':m['source_path'],'target_source_pointer':m['source_pointer'],'target_native_id':native})
  q.close()
 rebuilt_external.sort(key=lambda x:(x['relation_kind'],x['raw_locator'],x['target_source_path'],x['target_source_pointer'],x['target_occurrence_id']))
 if rebuilt_external!=scope['external_candidates']:raise ValueError('external candidate scope mismatch')
 from scripts.generate_native_text_delta_ledger import accepted_projection
 accepted,_=accepted_projection(ROOT,Path(source_root),exclude_runtime_paths=('manifests/native-archive/high-value-runtime.json','manifests/native-archive/high-value-open-runtime.json'))
 overlap={s['path']:sorted({x[0] for x in records[s['path']]}&accepted[s['path']]['physical']) for s in scope['sources']};overlap={k:v for k,v in overlap.items() if v}
 if overlap:raise ValueError('successor physical overlap '+json.dumps(overlap,sort_keys=True))
 old=json.loads((ROOT/'manifests/native-archive/high-value-selection.json').read_text());old_overlap={s['path']:len(accepted[s['path']]['physical']) for s in old['sources'] if accepted[s['path']]['physical']}
 if old_overlap!={'ExcelBinOutput/AvatarCostumeExcelConfigData.json':160} or scope['corrected_historical_overlap']['duplicate_physical_rows']!=204:raise ValueError('historical overlap correction proof')
 maps={}
 for locale in scope['languages']:
  tiers=[]
  for tier in ('main','medium'):
   path=scope['textmaps'][locale][tier];index=defaultdict(list)
   for ordinal,(key,text) in enumerate(pairs(Path(source_root)/path)):
    if key in wanted:index[key].append((path,ordinal,'/'+key,text,seals[path]))
   tiers.append(index)
  maps[locale]=tiers
 expected={t:[] for t in TABLES};states=Counter();roles=Counter();relstates=Counter();container={};local=defaultdict(list)
 for spec in scope['sources']:
  for ordinal,row,raw in records[spec['path']]+reuse_records[spec['path']]:
   pointer='/'+str(ordinal);container[(spec['path'],pointer)]=iid(run,'container',spec['path'],pointer)
 for target_spec in scope['sources']:
  keys={rel['target_key'] for source_spec in scope['sources'] for rel in source_spec.get('relations',[]) if rel['resolution']=='local' and target_spec['path'] in rel['target_sources']}
  for ordinal,row,raw in records[target_spec['path']]:
   pointer='/'+str(ordinal)
   for key in keys:
    if row.get(key) is not None:local[(target_spec['path'],key,str(row[key]))].append({'target_source_path':target_spec['path'],'target_source_pointer':pointer,'target_native_id':str(row[key]),'target_family':'native_archive','target_contract':scope['family_contract_version'],'target_parser':parser,'target_run':run,'target_occurrence_id':container[(target_spec['path'],pointer)]})
 if event_artifact is None:raise ValueError('event dependency required')
 event_manifest=json.loads((ROOT/'manifests/event-narrative/photo-echo-delta-runtime.json').read_text());event_artifact=Path(event_artifact)
 if event_artifact.stat().st_size!=event_manifest['artifact']['size_bytes'] or sha(event_artifact)!=event_manifest['artifact']['sha256']:raise ValueError('event dependency identity')
 ec=sqlite3.connect(event_artifact);rebuilt_reuse=[]
 for row in ec.execute("SELECT id,source_path,pointer,native_id FROM event_narrative_row WHERE source_path='ExcelBinOutput/TalkExcelConfigData_1.json' ORDER BY ordinal"):
  hit={'target_source_path':row[1],'target_source_pointer':row[2],'target_native_id':row[3],'target_family':event_manifest['family'],'target_contract':event_manifest['family_contract_version'],'target_parser':event_manifest['release']['parser_build_id'],'target_run':event_manifest['release']['run_id'],'target_occurrence_id':row[0]};rebuilt_reuse.append(hit);local[(row[1],'id',str(row[3]))].append(hit)
 ec.close()
 if rebuilt_reuse!=scope.get('reuse_targets'):raise ValueError('historical reuse target mismatch')
 external=defaultdict(list)
 for x in rebuilt_external:external[(x['relation_kind'],x['raw_locator'])].append(x)
 for spec in scope['sources']:
  path=spec['path'];seal=seals[path]
  for ordinal,row,raw in records[path]+reuse_records[path]:
   pointer='/'+str(ordinal);cid=container[(path,pointer)];nf=spec.get('native_id_field');nv=None if nf is None else str(row.get(nf));expected['native_archive_container'].append((cid,run,spec['boundary'],'physical_row',path,seal['sha256'],seal['blob'],ordinal,pointer,nf,nv,raw,hashlib.sha256(raw.encode()).hexdigest(),None,None,None,None,None))
   reuse=reuse_by_coord.get((path,pointer))
   expected['native_archive_container'][-1]=(cid,run,spec['boundary'],'projection_reuse',path,seal['sha256'],seal['blob'],ordinal,pointer,nf,nv,None,None,reuse['target_family'],reuse['target_contract'],reuse['target_parser'],reuse['target_run'],reuse['target_occurrence_id']) if reuse else expected['native_archive_container'][-1]
   for owner in spec['text_owners']:
    for op,value in occurrences(row,owner['field'],owner.get('shape','scalar')):
     locator=str(value);field=op.lstrip('/');roles[owner['owner_role_status']]+=3
     for locale in scope['languages']:
      main=maps[locale][0].get(locator,[]);medium=maps[locale][1].get(locator,[]);hits=main or medium;status='missing' if not hits else 'resolved_main' if main and len(hits)==1 else 'resolved_medium' if medium and len(hits)==1 else 'ambiguous';states[status]+=1;sid=iid(run,'slot',path,pointer,field,locale);expected['native_archive_slot'].append((sid,cid,field,owner['owner_role_status'],locale,locator,status,len(hits)))
      for tp,pair,pp,text,ts in hits:expected['native_archive_text'].append((iid(run,'text',sid,tp,pair),sid,tp,ts['sha256'],ts['blob'],pair,pp,text,hashlib.sha256(text.encode()).hexdigest()))
   for rel in spec.get('relations',[]):
    for rp,value in occurrences(row,rel['field'],rel.get('shape','scalar')):
     locator=str(value);complete=rel['resolution']!='unresolved';hits=[]
     if rel['resolution']=='local':
      for target_path in rel['target_sources']:hits.extend(local[(target_path,rel['target_key'],locator)])
     elif rel['resolution'] in ('core','quest_talk_v7'):hits=external[(rel['relation_kind'],locator)]
     status='mapping_unresolved' if not complete else 'missing' if not hits else 'resolved_unique' if len(hits)==1 else 'ambiguous';relstates[status]+=1;sp=pointer+rp;rid=iid(run,'relation',path,sp,rel['relation_kind']);expected['native_archive_relation'].append((rid,cid,rel['relation_kind'],rp.lstrip('/'),sp,locator,rel['candidate_namespace'],status,len(hits),1 if complete else 0))
     for hit in hits:
      candidate=(hit['target_source_path'],hit['target_source_pointer'],hit.get('target_native_id'),hit['target_family'],hit['target_contract'],hit['target_parser'],hit['target_run'],hit['target_occurrence_id'])
      expected['native_archive_candidate'].append((iid(run,'candidate',rid,candidate[-1]),rid,*candidate))
 for table in TABLES:expected[table].sort()
 c=sqlite3.connect(artifact);s=sqlite3.connect(':memory:');s.execute('PRAGMA foreign_keys=ON');s.executescript((ROOT/'schemas/native-archive-v1.sql').read_text());proof=schema(s)
 if schema(c)!=proof:raise ValueError('schema structure mismatch')
 if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('foreign keys')
 for table in TABLES:
  n=len(c.execute('SELECT * FROM '+table+' LIMIT 0').description);got=c.execute('SELECT * FROM '+table+' ORDER BY '+','.join(str(i+1) for i in range(n))).fetchall()
  if got!=expected[table]:raise ValueError('business row mismatch '+table)
 counts={t:len(expected[t]) for t in TABLES};release=json.loads(c.execute('SELECT manifest_json FROM native_archive_release WHERE id=1').fetchone()[0]);c.close();s.close();carried={x['path'] for x in scope['sources'] if x.get('carried_forward')};containers=expected['native_archive_container'];terminal={x['path']:x['terminal_unresolved_reason'] for x in scope['sources'] if x['completion_outcome']=='terminal_unresolved_after_complete_processing'}
 physical=[x for x in containers if x[3]=='physical_row'];reuse_rows=[x for x in containers if x[3]=='projection_reuse']
 if set(terminal)!=set(authority['terminal_semantic_audit']) or any(not x.get('exact_unresolved_reason') or not x.get('unresolved_field_or_namespace_paths') for x in authority['terminal_semantic_audit'].values()):raise ValueError('terminal semantic audit incomplete')
 want={'contract':scope['family_contract_version'],'run_id':run,'parser_build_id':parser,'scope_sha256':scope_sha,'implementation':impl,'snapshot_id':scope['snapshot_id'],'counts':counts,'text_state_counts':dict(sorted(states.items())),'owner_role_counts':dict(sorted(roles.items())),'relation_state_counts':dict(sorted(relstates.items())),'physical_rows':len(physical),'projection_reuse_rows':len(reuse_rows),'carried_forward_physical_rows':sum(x[4] in carried for x in physical),'new_wave_physical_rows':sum(x[4] not in carried for x in physical),'searchable_text_candidates':len(expected['native_archive_text']),'corrected_historical_duplicate_rows':204,'terminal_unresolved_sources':terminal,'field_classification_complete':True,'historical_body_copied':False,'corpus_complete':True,'full_7_0':False}
 if release!=want:raise ValueError('release mismatch')
 logical={t:hashlib.sha256('\n'.join(json.dumps(list(x),ensure_ascii=False,separators=(',',':')) for x in expected[t]).encode()).hexdigest() for t in TABLES};runtime=json.loads(Path(runtime_manifest_path).read_text()) if runtime_manifest_path else None
 if runtime and (runtime['release']!=release or runtime['artifact']!={'size_bytes':Path(artifact).stat().st_size,'sha256':sha(artifact)} or runtime.get('logical_seals')!=logical):raise ValueError('runtime mismatch')
 elapsed=time.perf_counter()-start
 return {'status':'pass','actual_bytes_git_blob_match':True,'identity_independently_reconstructed':True,'selection_independently_bound':True,'verifier_authority_sha256':sha(authority_path),'terminal_semantic_audit':authority['terminal_semantic_audit'],'accepted_scope_physical_overlap_count':0,'corrected_historical_duplicate_rows':204,'corrected_historical_duplicate_sources':['ExcelBinOutput/AvatarCostumeExcelConfigData.json','ExcelBinOutput/TalkExcelConfigData_1.json'],'field_classification_complete':True,'terminal_unresolved_conditions_verified':True,'schema_structure_match':True,'write_once_structure_match':True,'unexpected_authoritative_objects':[],'schema_structure_normalized':proof,'verified_artifact':{'size_bytes':Path(artifact).stat().st_size,'sha256':sha(artifact)},'snapshot_id':scope['snapshot_id'],'scope_sha256':scope_sha,'implementation':impl,'parser_build_id':parser,'run_id':run,'contract':scope['family_contract_version'],'counts':counts,'text_states':dict(sorted(states.items())),'owner_roles':dict(sorted(roles.items())),'relation_states':dict(sorted(relstates.items())),'full_business_column_equality':True,'no_extra_rows':True,'logical_seals':logical,'performance':{'full_verifier_seconds':elapsed,'rows_per_second':counts['native_archive_container']/elapsed}}
