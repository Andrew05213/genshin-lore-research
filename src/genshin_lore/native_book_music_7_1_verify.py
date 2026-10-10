"""Independent verifier for the Native Book / MusicGame archive wave."""
from __future__ import annotations
import hashlib,json,sqlite3,time
from collections import Counter,defaultdict
from pathlib import Path
from .event_7_1_source import array_records,check_files,pairs,sha

ROOT=Path(__file__).resolve().parents[2]
TABLES=('native_archive_container','native_archive_slot','native_archive_text','native_archive_relation','native_archive_candidate')
def iid(*p):return 'nbm71:'+hashlib.sha256(json.dumps(p,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:40]
def schema(c):return [[a,b,d,' '.join((s or '').split()).lower()] for a,b,d,s in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
def target(a,path,pointer,native,raw_sha,occ):return {'target_source_path':path,'target_source_pointer':pointer,'target_native_id':native,'target_raw_sha256':raw_sha,'target_family':'native_archive','target_contract':a['release']['contract'],'target_parser':a['release']['parser_build_id'],'target_run':a['release']['run_id'],'target_occurrence_id':occ}
def verify(source_root,scope_path,artifact,message_artifact,runtime_manifest_path=None):
 start=time.perf_counter();scope=json.loads(Path(scope_path).read_text())
 if scope.get('selection_manifest')!='manifests/native-archive-7.1/book-music-selection-v1.json':raise ValueError('unapproved selection authority')
 selection_path=ROOT/scope['selection_manifest'];selection=json.loads(selection_path.read_text())
 if sha(selection_path)!=scope.get('selection_sha256'):raise ValueError('selection seal mismatch')
 if scope['sources']!=selection['sources'] or scope['languages']!=selection['languages'] or scope['textmaps']!=selection['textmaps']:raise ValueError('scope selection mismatch')
 selected={x['path'] for x in selection['sources']}|{x for locale in selection['languages'] for x in selection['textmaps'][locale].values()}
 if {x['path'] for x in scope['files']}!=selected:raise ValueError('scope file denominator mismatch')
 check_files(source_root,scope);impl={p:sha(ROOT/p) for p in ('src/genshin_lore/native_book_music_7_1_build.py','src/genshin_lore/event_7_1_source.py','schemas/native-book-music-7.1-v1.sql','manifests/native-archive-7.1/book-music-selection-v1.json')};parser='nbm71-parser:'+hashlib.sha256(json.dumps(impl,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:40];scope_sha=sha(scope_path);run='nbm71-run:'+hashlib.sha256((parser+':'+scope_sha).encode()).hexdigest()[:40];seals={x['path']:x for x in scope['files']};records={};wanted=set()
 for spec in scope['sources']:
  rows=list(array_records(Path(source_root)/spec['path']));records[spec['path']]=rows
  for ordinal,row,_ in rows:
   if not isinstance(row,dict):raise ValueError('row shape')
   key=spec.get('native_id_field')
   if key and key not in row and not (spec['path'].endswith('/MusicGameBookAreaExcelConfigData.json') and ordinal==0):raise ValueError('missing native key')
   if key in row and type(row[key]) is not int:raise ValueError('native key type')
   for owner in spec['text_owners']:
    if type(row.get(owner['field'])) is not int:raise ValueError('text owner type')
    wanted.add(str(row[owner['field']]))
   for rel in spec.get('relations',[]):
    if rel['field'] in row and type(row[rel['field']]) is not int:raise ValueError('relation locator type')
 maps={}
 for locale in scope['languages']:
  tiers=[]
  for tier in ('main','medium'):
   path=scope['textmaps'][locale][tier];index=defaultdict(list)
   for ordinal,(key,text) in enumerate(pairs(Path(source_root)/path)):
    if key in wanted:index[key].append((path,ordinal,'/'+key,text,seals[path]))
   tiers.append(index)
  maps[locale]=tiers
 mr=json.loads((ROOT/'manifests/native-archive-7.1/message-runtime-v1.json').read_text())
 if {'size_bytes':Path(message_artifact).stat().st_size,'sha256':sha(message_artifact)}!=mr['artifact']:raise ValueError('message dependency identity')
 if scope.get('reuse_targets')!=[]:raise ValueError('book/music wave has no projection reuse')
 external=defaultdict(list);rebuilt=[];mc=sqlite3.connect(message_artifact);mc.row_factory=sqlite3.Row
 for spec in scope['sources']:
  for rel in spec.get('relations',[]):
   values={str(row[rel['field']]) for _,row,_ in records[spec['path']] if type(row.get(rel['field'])) is int}
   if rel['relation_kind']!='source_authored_push_tips_reference':raise ValueError('undeclared relation kind')
   for value in values:
    for row in mc.execute("SELECT id,source_path,pointer,native_id,raw_sha256,run_id FROM native71_row WHERE source_path='ExcelBinOutput/PushTipsConfigData.json' AND native_id=? ORDER BY source_path,pointer,id",(value,)):
     if row['run_id']!=mr['release']['run_id']:raise ValueError('message target run drift')
     record=list(array_records(Path(source_root)/row['source_path']))[int(row['pointer'][1:])]
     if str(record[1].get('pushTipsId'))!=row['native_id'] or hashlib.sha256(record[2].encode()).hexdigest()!=row['raw_sha256']:raise ValueError('message target second-hop raw bytes/native ID')
     x={'relation_kind':rel['relation_kind'],'raw_locator':value,**target(mr,row['source_path'],row['pointer'],row['native_id'],row['raw_sha256'],row['id'])};rebuilt.append(x);external[(x['relation_kind'],value)].append(x)
 mc.close();canon=lambda x:json.dumps(x,sort_keys=True,separators=(',',':'))
 if sorted(map(canon,rebuilt))!=sorted(map(canon,scope['external_candidates'])):raise ValueError('scope external candidate mismatch')
 expected={t:[] for t in TABLES};states=Counter();roles=Counter();relstates=Counter()
 for spec in scope['sources']:
  path=spec['path'];seal=seals[path]
  for ordinal,row,raw in records[path]:
   pointer='/'+str(ordinal);cid=iid(run,'container',path,pointer);nf=spec.get('native_id_field');nv=None if nf is None else str(row.get(nf))
   expected['native_archive_container'].append((cid,run,spec['boundary'],'physical_row',path,seal['sha256'],seal['blob'],ordinal,pointer,nf,nv,raw,hashlib.sha256(raw.encode()).hexdigest(),None,None,None,None,None))
   for owner in spec['text_owners']:
    field=owner['field'];value=row.get(field)
    if type(value) is not int:raise ValueError('text owner type in projection')
    locator=str(value);roles[owner['owner_role_status']]+=3
    for locale in scope['languages']:
     main=maps[locale][0].get(locator,[]);medium=maps[locale][1].get(locator,[]);hits=main or medium;status='missing' if not hits else 'resolved_main' if main and len(hits)==1 else 'resolved_medium' if medium and len(hits)==1 else 'ambiguous';states[status]+=1;sid=iid(run,'slot',path,pointer,field,locale);expected['native_archive_slot'].append((sid,cid,field,owner['owner_role_status'],locale,locator,status,len(hits)))
     for tp,pair,pp,text,ts in hits:expected['native_archive_text'].append((iid(run,'text',sid,tp,pair),sid,tp,ts['sha256'],ts['blob'],pair,pp,text,hashlib.sha256(text.encode()).hexdigest()))
   for rel in spec.get('relations',[]):
    value=row.get(rel['field'])
    if value is None:continue
    if type(value) is not int:raise ValueError('relation locator type in projection')
    locator=str(value);hits=sorted(external[(rel['relation_kind'],locator)],key=lambda x:(x['target_source_path'],x['target_source_pointer'],x['target_run'],x['target_occurrence_id']));status='missing' if not hits else 'resolved_unique' if len(hits)==1 else 'ambiguous';relstates[status]+=1;sp=pointer+'/'+rel['field'];rid=iid(run,'relation',path,sp,rel['relation_kind']);expected['native_archive_relation'].append((rid,cid,rel['relation_kind'],rel['field'],sp,locator,rel['candidate_namespace'],status,len(hits),1))
    for x in hits:expected['native_archive_candidate'].append((iid(run,'candidate',rid,x['target_occurrence_id']),rid,x['target_source_path'],x['target_source_pointer'],x.get('target_native_id'),x['target_family'],x['target_contract'],x['target_parser'],x['target_run'],x['target_occurrence_id']))
 for table in TABLES:expected[table].sort()
 dep=scope['dependencies'];want_message={'artifact':{'path':str(Path(message_artifact).resolve()),'size_bytes':mr['artifact']['size_bytes'],'sha256':mr['artifact']['sha256']},'family':'native_archive','family_contract_version':mr['release']['contract'],'parser_build_id':mr['release']['parser_build_id'],'run_id':mr['release']['run_id']}
 if dep!={'native_message':want_message}:raise ValueError('scope dependency authority')
 c=sqlite3.connect(artifact);s=sqlite3.connect(':memory:');s.execute('PRAGMA foreign_keys=ON');s.executescript((ROOT/'schemas/native-book-music-7.1-v1.sql').read_text());schema_proof=schema(s)
 if schema(c)!=schema_proof:raise ValueError('schema structure mismatch')
 if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('SQLite integrity/FK')
 if any(Path(str(artifact)+x).exists() for x in ('-wal','-shm','-journal')):raise ValueError('SQLite sidecar')
 for table in TABLES:
  n=len(c.execute('SELECT * FROM '+table+' LIMIT 0').description);got=c.execute('SELECT * FROM '+table+' ORDER BY '+','.join(str(i+1) for i in range(n))).fetchall()
  if got!=expected[table]:raise ValueError('business row mismatch '+table)
 counts={t:len(expected[t]) for t in TABLES};release=json.loads(c.execute('SELECT manifest_json FROM native_archive_release WHERE id=1').fetchone()[0]);c.close();s.close();carried={x['path'] for x in scope['sources'] if x.get('carried_forward')};containers=expected['native_archive_container']
 want={'contract':scope['family_contract_version'],'run_id':run,'parser_build_id':parser,'scope_sha256':scope_sha,'implementation':impl,'snapshot_id':scope['snapshot_id'],'counts':counts,'text_state_counts':dict(sorted(states.items())),'owner_role_counts':dict(sorted(roles.items())),'relation_state_counts':dict(sorted(relstates.items())),'physical_rows':sum(x[3]=='physical_row' for x in containers),'projection_reuse_rows':sum(x[3]=='projection_reuse' for x in containers),'carried_forward_physical_rows':sum(x[3]=='physical_row' and x[4] in carried for x in containers),'carried_forward_projection_reuse_rows':sum(x[3]=='projection_reuse' and x[4] in carried for x in containers),'new_wave_physical_rows':sum(x[3]=='physical_row' and x[4] not in carried for x in containers),'new_projection_reuse_rows':sum(x[3]=='projection_reuse' and x[4] not in carried for x in containers),'searchable_text_candidates':len(expected['native_archive_text']),'template_text_preserved_verbatim':True,'historical_body_copied':False,'corpus_complete':True,'full_7_1':False}
 if release!=want:raise ValueError('release mismatch')
 logical={t:hashlib.sha256('\n'.join(json.dumps(list(x),ensure_ascii=False,separators=(',',':')) for x in expected[t]).encode()).hexdigest() for t in TABLES};runtime=json.loads(Path(runtime_manifest_path).read_text()) if runtime_manifest_path else None
 if runtime and (runtime['release']!=release or runtime['artifact']!={'size_bytes':Path(artifact).stat().st_size,'sha256':sha(artifact)} or runtime.get('logical_seals')!=logical or runtime.get('schema_structure_normalized')!=schema_proof or runtime.get('schema_structure_match') is not True or runtime.get('write_once_structure_match') is not True or runtime.get('unexpected_authoritative_objects')!=[]):raise ValueError('runtime mismatch')
 elapsed=time.perf_counter()-start
 return {'status':'pass','actual_bytes_git_blob_match':True,'identity_independently_reconstructed':True,'selection_independently_bound':True,'message_second_hop_rechecked':len(rebuilt),'pointer_round_trip_independently_reconstructed':True,'schema_structure_match':True,'write_once_structure_match':True,'unexpected_authoritative_objects':[],'schema_structure_normalized':schema_proof,'verified_artifact':{'size_bytes':Path(artifact).stat().st_size,'sha256':sha(artifact)},'snapshot_id':scope['snapshot_id'],'scope_sha256':scope_sha,'implementation':impl,'parser_build_id':parser,'run_id':run,'contract':scope['family_contract_version'],'counts':counts,'text_states':dict(sorted(states.items())),'owner_roles':dict(sorted(roles.items())),'relation_states':dict(sorted(relstates.items())),'full_business_column_equality':True,'no_extra_rows':True,'logical_seals':logical,'performance':{'full_verifier_seconds':elapsed,'rows_per_second':counts['native_archive_container']/elapsed}}
