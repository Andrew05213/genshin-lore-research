"""Independent verifier for the corrected Native Archive successor."""
from __future__ import annotations
import hashlib,json,sqlite3,time
from collections import Counter,defaultdict
from pathlib import Path
from .event_narrative_source import array_records,check_files,pairs,sha

ROOT=Path(__file__).resolve().parents[2]
TABLES=('native_archive_container','native_archive_slot','native_archive_text','native_archive_relation','native_archive_candidate')
CORE={'size_bytes':5963210752,'sha256':'6bef3518ad17df4a7a953d9d064ba4ee0569c86aeb13ae695092fc73b4d63ed1'}
def iid(*p):return 'native-archive:'+hashlib.sha256(json.dumps(p,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:40]
def schema(c):return [[a,b,d,' '.join((s or '').split()).lower()] for a,b,d,s in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
def target(a,path,pointer,native,occ):return {'target_source_path':path,'target_source_pointer':pointer,'target_native_id':native,'target_family':a['family'],'target_contract':a['family_contract_version'],'target_parser':a.get('parser_build_id') or a.get('release',{}).get('parser_build_id'),'target_run':a.get('ingest_run_id') or a.get('release',{}).get('run_id'),'target_occurrence_id':occ}
def verify(source_root,scope_path,artifact,event_artifact,quest_artifact,core_artifact,talk_quest_artifact,talk_complement_artifact,runtime_manifest_path=None):
 start=time.perf_counter();scope=json.loads(Path(scope_path).read_text())
 if scope.get('selection_manifest')!='manifests/native-archive/successor-selection.json':raise ValueError('unapproved selection authority')
 selection_path=ROOT/scope['selection_manifest'];selection=json.loads(selection_path.read_text())
 if sha(selection_path)!=scope.get('selection_sha256'):raise ValueError('selection seal mismatch')
 if scope['sources']!=selection['sources'] or scope['languages']!=selection['languages'] or scope['textmaps']!=selection['textmaps']:raise ValueError('scope selection mismatch')
 selected={x['path'] for x in selection['sources']}|{x for locale in selection['languages'] for x in selection['textmaps'][locale].values()}
 if {x['path'] for x in scope['files']}!=selected:raise ValueError('scope file denominator mismatch')
 check_files(source_root,scope);impl={p:sha(ROOT/p) for p in ('src/genshin_lore/native_archive_successor_build.py','src/genshin_lore/event_narrative_source.py','schemas/native-archive-v1.sql','manifests/native-archive/successor-selection.json')};parser='native-archive-parser:'+hashlib.sha256(json.dumps(impl,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:40];scope_sha=sha(scope_path);run='native-archive-run:'+hashlib.sha256((parser+':'+scope_sha).encode()).hexdigest()[:40];seals={x['path']:x for x in scope['files']};records={};wanted=set()
 for spec in scope['sources']:
  rows=list(array_records(Path(source_root)/spec['path']));records[spec['path']]=rows
  for _,row,_ in rows:
   if not isinstance(row,dict):raise ValueError('row shape')
   for owner in spec['text_owners']:
    if type(row.get(owner['field'])) is int:wanted.add(str(row[owner['field']]))
 maps={}
 for locale in scope['languages']:
  tiers=[]
  for tier in ('main','medium'):
   path=scope['textmaps'][locale][tier];index=defaultdict(list)
   for ordinal,(key,text) in enumerate(pairs(Path(source_root)/path)):
    if key in wanted:index[key].append((path,ordinal,'/'+key,text,seals[path]))
   tiers.append(index)
  maps[locale]=tiers
 er=json.loads((ROOT/'manifests/event-narrative/archive-wave-runtime.json').read_text());qr=json.loads((ROOT/'manifests/quest-narrative/runtime.json').read_text());trs=[json.loads((ROOT/p).read_text()) for p in ('manifests/quest-talk-bounded/quest_linked-runtime.json','manifests/quest-talk-bounded/mapped_complement-runtime.json')]
 for item,name in zip(trs,('quest_linked','mapped_complement')):item['parser_build_id']=json.loads((ROOT/('manifests/quest-talk-immutable/'+name+'-runtime.json')).read_text())['parser_build_id']
 frozen_npc=selection['npc_dependency'];nr={'family':frozen_npc['family'],'family_contract_version':frozen_npc['family_contract_version'],'ingest_run_id':frozen_npc['run_id'],'parser_build_id':frozen_npc['parser_build_id']}
 for label,path,a in (('event',Path(event_artifact),er),('quest',Path(quest_artifact),qr),('talk quest',Path(talk_quest_artifact),trs[0]),('talk complement',Path(talk_complement_artifact),trs[1])):
  if {'size_bytes':path.stat().st_size,'sha256':sha(path)}!=a['artifact']:raise ValueError(label+' dependency identity')
 if {'size_bytes':Path(core_artifact).stat().st_size,'sha256':sha(core_artifact)}!=CORE:raise ValueError('core dependency identity')
 ec=sqlite3.connect(event_artifact);ec.row_factory=sqlite3.Row;reuse={}
 for row in ec.execute("SELECT id,source_path,pointer FROM event_narrative_row WHERE run_id=? AND source_path='ExcelBinOutput/SnezBookOfferingGameplayExcelConfigData.json' ORDER BY pointer,id",(er['release']['run_id'],)):
  reuse[(row['source_path'],row['pointer'])]={'source_path':row['source_path'],'source_pointer':row['pointer'],**target(er,row['source_path'],row['pointer'],None,row['id'])}
 ec.close()
 if sorted(reuse.values(),key=lambda x:(x['source_path'],x['source_pointer']))!=sorted(scope['reuse_targets'],key=lambda x:(x['source_path'],x['source_pointer'])):raise ValueError('scope reuse target mismatch')
 external=defaultdict(list);rebuilt=[];qc=sqlite3.connect(quest_artifact);qc.row_factory=sqlite3.Row
 for spec in scope['sources']:
  for rel in spec.get('relations',[]):
   values={str(row[rel['field']]) for _,row,_ in records[spec['path']] if type(row.get(rel['field'])) is int}
   kind='main_quest_row' if rel['relation_kind']=='source_authored_main_quest_reference' else 'quest_step_row' if rel['relation_kind'] in ('source_authored_open_quest_reference','source_authored_pre_quest_reference') else None
   if kind:
    for value in values:
     for row in qc.execute('SELECT id,source_path,source_pointer,native_id FROM quest_narrative_container WHERE kind=? AND native_id=? ORDER BY source_path,source_pointer,id',(kind,value)):
      x={'relation_kind':rel['relation_kind'],'raw_locator':value,**target(qr,row['source_path'],row['source_pointer'],row['native_id'],row['id'])};rebuilt.append(x);external[(x['relation_kind'],value)].append(x)
 qc.close();wanted_talk={str(row['finishTalkId']) for spec in scope['sources'] for _,row,_ in records[spec['path']] if type(row.get('finishTalkId')) is int}
 for rt,ap in zip(trs,(talk_quest_artifact,talk_complement_artifact)):
  c=sqlite3.connect(ap);c.row_factory=sqlite3.Row
  for value in wanted_talk:
   for row in c.execute("SELECT id,native_id,metadata_json FROM quest_talk_physical_container WHERE ingest_run_id=? AND kind='talk_root' AND native_id=? ORDER BY id",(rt['ingest_run_id'],value)):
    meta=json.loads(row['metadata_json']);x={'relation_kind':'source_authored_finish_talk_reference','raw_locator':value,**target(rt,meta['source_path'],meta['source_pointer'],row['native_id'],row['id'])};rebuilt.append(x);external[(x['relation_kind'],value)].append(x)
  c.close()
 wanted_npc={str(row['npcId']) for spec in scope['sources'] for _,row,_ in records[spec['path']] if type(row.get('npcId')) is int};c=sqlite3.connect(core_artifact);c.row_factory=sqlite3.Row
 exact={tuple(x) for x in c.execute('SELECT DISTINCT family_contract_version,ingest_run_id,parser_build_id FROM npc_archive_row_occurrence WHERE ingest_run_id=?',(nr['ingest_run_id'],)).fetchall()}
 if exact!={(nr['family_contract_version'],nr['ingest_run_id'],nr['parser_build_id'])}:raise ValueError('frozen NPC dependency mismatch')
 for row in c.execute('SELECT occurrence_id,source_file_path,source_pointer,raw_record_json FROM npc_archive_row_occurrence WHERE ingest_run_id=? ORDER BY source_file_path,source_ordinal,occurrence_id',(nr['ingest_run_id'],)):
  raw=json.loads(row['raw_record_json']);value=str(raw.get('id'))
  if value in wanted_npc:
   x={'relation_kind':'source_authored_npc_reference','raw_locator':value,**target(nr,row['source_file_path'],row['source_pointer'],value,row['occurrence_id'])};rebuilt.append(x);external[(x['relation_kind'],value)].append(x)
 c.close();canon=lambda x:json.dumps(x,sort_keys=True,separators=(',',':'))
 if sorted(map(canon,rebuilt))!=sorted(map(canon,scope['external_candidates'])):raise ValueError('scope external candidate mismatch')
 expected={t:[] for t in TABLES};states=Counter();roles=Counter();relstates=Counter()
 for spec in scope['sources']:
  path=spec['path'];seal=seals[path]
  for ordinal,row,raw in records[path]:
   pointer='/'+str(ordinal);cid=iid(run,'container',path,pointer);nf=spec.get('native_id_field');nv=None if nf is None else str(row.get(nf))
   if spec.get('reuse'):
    x=reuse[(path,pointer)];expected['native_archive_container'].append((cid,run,spec['boundary'],'projection_reuse',path,seal['sha256'],seal['blob'],ordinal,pointer,nf,nv,None,None,x['target_family'],x['target_contract'],x['target_parser'],x['target_run'],x['target_occurrence_id']))
   else:expected['native_archive_container'].append((cid,run,spec['boundary'],'physical_row',path,seal['sha256'],seal['blob'],ordinal,pointer,nf,nv,raw,hashlib.sha256(raw.encode()).hexdigest(),None,None,None,None,None))
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
 dep=scope['dependencies'];want_event=er['release']|{'artifact':er['artifact'],'family':er['family'],'family_contract_version':er['family_contract_version']};want_quest=qr['release']|{'artifact':qr['artifact'],'family':qr['family'],'family_contract_version':qr['family_contract_version']};want_npc={'artifact':CORE,'family':'npc_archive','family_contract_version':nr['family_contract_version'],'parser_build_id':nr['parser_build_id'],'run_id':nr['ingest_run_id']}
 if dep!={'historical_event':want_event,'quest_narrative':want_quest,'talk_runs':trs,'npc_core':want_npc}:raise ValueError('scope dependency authority')
 c=sqlite3.connect(artifact);s=sqlite3.connect(':memory:');s.execute('PRAGMA foreign_keys=ON');s.executescript((ROOT/'schemas/native-archive-v1.sql').read_text());schema_proof=schema(s)
 if schema(c)!=schema_proof:raise ValueError('schema structure mismatch')
 if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('foreign keys')
 for table in TABLES:
  n=len(c.execute('SELECT * FROM '+table+' LIMIT 0').description);got=c.execute('SELECT * FROM '+table+' ORDER BY '+','.join(str(i+1) for i in range(n))).fetchall()
  if got!=expected[table]:raise ValueError('business row mismatch '+table)
 counts={t:len(expected[t]) for t in TABLES};release=json.loads(c.execute('SELECT manifest_json FROM native_archive_release WHERE id=1').fetchone()[0]);c.close();s.close();carried={x['path'] for x in scope['sources'] if x.get('carried_forward')};containers=expected['native_archive_container']
 want={'contract':scope['family_contract_version'],'run_id':run,'parser_build_id':parser,'scope_sha256':scope_sha,'implementation':impl,'snapshot_id':scope['snapshot_id'],'counts':counts,'text_state_counts':dict(sorted(states.items())),'owner_role_counts':dict(sorted(roles.items())),'relation_state_counts':dict(sorted(relstates.items())),'physical_rows':sum(x[3]=='physical_row' for x in containers),'projection_reuse_rows':sum(x[3]=='projection_reuse' for x in containers),'successor_carried_forward_physical_rows':sum(x[3]=='physical_row' and x[4] in carried for x in containers),'successor_carried_forward_projection_reuse_rows':sum(x[3]=='projection_reuse' and x[4] in carried for x in containers),'new_wave_physical_rows':sum(x[3]=='physical_row' and x[4] not in carried for x in containers),'new_projection_reuse_rows':sum(x[3]=='projection_reuse' and x[4] not in carried for x in containers),'corrected_textmap_pointer_count':len(expected['native_archive_text']),'historical_body_copied':False,'corpus_complete':True,'full_7_0':False}
 if release!=want:raise ValueError('release mismatch')
 logical={t:hashlib.sha256('\n'.join(json.dumps(list(x),ensure_ascii=False,separators=(',',':')) for x in expected[t]).encode()).hexdigest() for t in TABLES};runtime=json.loads(Path(runtime_manifest_path).read_text()) if runtime_manifest_path else None
 if runtime and (runtime['release']!=release or runtime['artifact']!={'size_bytes':Path(artifact).stat().st_size,'sha256':sha(artifact)} or runtime.get('logical_seals')!=logical or runtime.get('schema_structure_normalized')!=schema_proof or runtime.get('schema_structure_match') is not True or runtime.get('write_once_structure_match') is not True or runtime.get('unexpected_authoritative_objects')!=[]):raise ValueError('runtime mismatch')
 elapsed=time.perf_counter()-start
 return {'status':'pass','actual_bytes_git_blob_match':True,'identity_independently_reconstructed':True,'selection_independently_bound':True,'pointer_round_trip_independently_reconstructed':True,'schema_structure_match':True,'write_once_structure_match':True,'unexpected_authoritative_objects':[],'schema_structure_normalized':schema_proof,'verified_artifact':{'size_bytes':Path(artifact).stat().st_size,'sha256':sha(artifact)},'snapshot_id':scope['snapshot_id'],'scope_sha256':scope_sha,'implementation':impl,'parser_build_id':parser,'run_id':run,'contract':scope['family_contract_version'],'counts':counts,'text_states':dict(sorted(states.items())),'owner_roles':dict(sorted(roles.items())),'relation_states':dict(sorted(relstates.items())),'full_business_column_equality':True,'no_extra_rows':True,'logical_seals':logical,'performance':{'full_verifier_seconds':elapsed,'rows_per_second':counts['native_archive_container']/elapsed}}
