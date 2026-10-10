"""Independent verifier for native-archive-evidence-v1."""
from __future__ import annotations
import hashlib,json,sqlite3,time
from collections import Counter,defaultdict
from pathlib import Path
from .event_narrative_source import array_records,check_files,pairs,sha
ROOT=Path(__file__).resolve().parents[2];TABLES=('native_archive_container','native_archive_slot','native_archive_text','native_archive_relation','native_archive_candidate')
def iid(*parts):return 'native-archive:'+hashlib.sha256(json.dumps(parts,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:40]
def _schema(c):
 rows=[]
 for typ,name,tbl,sql in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"):
  rows.append((typ,name,tbl,' '.join((sql or '').split()).lower()))
 return rows
def _equal(c,t,rows):
 got=c.execute('SELECT * FROM '+t+' ORDER BY '+','.join(str(i+1) for i in range(len(c.execute('SELECT * FROM '+t+' LIMIT 0').description)))).fetchall()
 if got!=rows:raise ValueError('business row mismatch '+t)
def verify(source_root,scope_path,artifact,event_artifact,quest_artifact,runtime_manifest_path=None):
 started=time.perf_counter();scope=json.loads(Path(scope_path).read_text());check_files(source_root,scope);impl={p:sha(ROOT/p) for p in ('src/genshin_lore/native_archive_build.py','src/genshin_lore/event_narrative_source.py','schemas/native-archive-v1.sql')};parser='native-archive-parser:'+hashlib.sha256(json.dumps(impl,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:40];scope_sha=sha(scope_path);run='native-archive-run:'+hashlib.sha256((parser+':'+scope_sha).encode()).hexdigest()[:40];seals={x['path']:x for x in scope['files']};wanted=set();records={}
 for spec in scope['sources']:
  rows=list(array_records(Path(source_root)/spec['path']));records[spec['path']]=rows
  for _,row,_ in rows:
   if not isinstance(row,dict):raise ValueError('row shape')
   for owner in spec['text_owners']:
    v=row.get(owner['field']);
    if type(v) is int:wanted.add(str(v))
 maps={}
 for locale in scope['languages']:
  tiers=[]
  for tier in ('main','medium'):
   path=scope['textmaps'][locale][tier];index=defaultdict(list)
   for ordinal,(key,text) in enumerate(pairs(Path(source_root)/path)):
    if key in wanted:index[key].append((path,ordinal,'/'+json.dumps(key,ensure_ascii=False),text,seals[path]))
   tiers.append(index)
  maps[locale]=tiers
 expected={t:[] for t in TABLES};states=Counter();roles=Counter();relstates=Counter()
 # Reconstruct exact cross-artifact maps from accepted artifacts.  The scope is
 # checked as a declaration, never used as the verifier's candidate oracle.
 er=json.loads((ROOT/'manifests/event-narrative/archive-wave-runtime.json').read_text());qr=json.loads((ROOT/'manifests/quest-narrative/runtime.json').read_text())
 ec=sqlite3.connect(event_artifact);ec.row_factory=sqlite3.Row;reuse={}
 for row in ec.execute("SELECT id,source_path,pointer FROM event_narrative_row WHERE run_id=? AND source_path='ExcelBinOutput/SnezBookOfferingGameplayExcelConfigData.json' ORDER BY pointer,id",(er['release']['run_id'],)):
  reuse[(row['source_path'],row['pointer'])]={'source_path':row['source_path'],'source_pointer':row['pointer'],'target_family':'event_narrative_archive','target_contract':er['family_contract_version'],'target_parser':er['release']['parser_build_id'],'target_run':er['release']['run_id'],'target_occurrence_id':row['id']}
 ec.close()
 if sorted(reuse.values(),key=lambda x:(x['source_path'],x['source_pointer']))!=sorted(scope['reuse_targets'],key=lambda x:(x['source_path'],x['source_pointer'])):raise ValueError('scope reuse target mismatch')
 wanted_main={str(row['mainQuestID']) for _,row,_ in records['ExcelBinOutput/HandbookQuestGuideExcelConfigData.json'] if 'mainQuestID' in row};external=defaultdict(list);qc=sqlite3.connect(quest_artifact);qc.row_factory=sqlite3.Row;rebuilt=[]
 for value in sorted(wanted_main):
  for row in qc.execute("SELECT id,source_path,source_pointer,native_id FROM quest_narrative_container WHERE kind='main_quest_row' AND native_id=? ORDER BY source_path,source_pointer,id",(value,)):
   x={'relation_kind':'source_authored_main_quest_reference','raw_locator':value,'target_source_path':row['source_path'],'target_source_pointer':row['source_pointer'],'target_native_id':row['native_id'],'target_family':'quest_narrative_archive','target_contract':qr['family_contract_version'],'target_parser':qr['release']['parser_build_id'],'target_run':qr['release']['run_id'],'target_occurrence_id':row['id']};rebuilt.append(x);external[(x['relation_kind'],value)].append(x)
 qc.close()
 key=lambda x:json.dumps(x,sort_keys=True,separators=(',',':'))
 if sorted(map(key,rebuilt))!=sorted(map(key,scope['external_candidates'])):raise ValueError('scope external candidate mismatch')
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
   for relation in spec.get('relations',[]):
    value=row.get(relation['field'])
    if value is None:continue
    locator=str(value);hits=sorted(external[(relation['relation_kind'],locator)],key=lambda x:(x['target_source_path'],x['target_source_pointer'],x['target_run'],x['target_occurrence_id']));status='missing' if not hits else 'resolved_unique' if len(hits)==1 else 'ambiguous';relstates[status]+=1;sp=pointer+'/'+relation['field'];rid=iid(run,'relation',path,sp,relation['relation_kind']);expected['native_archive_relation'].append((rid,cid,relation['relation_kind'],relation['field'],sp,locator,relation['candidate_namespace'],status,len(hits),1))
    for x in hits:expected['native_archive_candidate'].append((iid(run,'candidate',rid,x['target_occurrence_id']),rid,x['target_source_path'],x['target_source_pointer'],x.get('target_native_id'),x['target_family'],x['target_contract'],x['target_parser'],x['target_run'],x['target_occurrence_id']))
 for t in TABLES:expected[t].sort()
 # Exact dependencies come from accepted tracked authority, not candidate self-report.
 for label,path,authority in (('event',Path(event_artifact),er),('quest',Path(quest_artifact),qr)):
  if path.stat().st_size!=authority['artifact']['size_bytes'] or sha(path)!=authority['artifact']['sha256']:raise ValueError(label+' dependency identity')
 dep=scope['dependencies'];expected_event_dependency=er['release']|{'artifact':er['artifact'],'family':er['family'],'family_contract_version':er['family_contract_version']};expected_quest_dependency=qr['release']|{'artifact':qr['artifact'],'family':qr['family'],'family_contract_version':qr['family_contract_version']}
 # Bind the complete declared dependency objects, including artifact bytes,
 # family/contract, snapshot, scope, implementation, parser, run, counts and
 # completeness claims.  Checking only run/parser would let a candidate scope
 # misstate its historical dependency while still pointing at the right file.
 if dep.get('historical_event')!=expected_event_dependency or dep.get('quest_narrative')!=expected_quest_dependency:raise ValueError('scope dependency authority')
 c=sqlite3.connect(artifact);expected_schema=sqlite3.connect(':memory:');expected_schema.execute('PRAGMA foreign_keys=ON');expected_schema.executescript((ROOT/'schemas/native-archive-v1.sql').read_text())
 if _schema(c)!=_schema(expected_schema):raise ValueError('schema structure mismatch')
 if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('foreign keys')
 for t in TABLES:_equal(c,t,expected[t])
 counts={t:len(expected[t]) for t in TABLES};release=json.loads(c.execute('SELECT manifest_json FROM native_archive_release WHERE id=1').fetchone()[0]);c.close();expected_schema.close();want={'contract':scope['family_contract_version'],'run_id':run,'parser_build_id':parser,'scope_sha256':scope_sha,'implementation':impl,'snapshot_id':scope['snapshot_id'],'counts':counts,'text_state_counts':dict(sorted(states.items())),'owner_role_counts':dict(sorted(roles.items())),'relation_state_counts':dict(sorted(relstates.items())),'physical_rows':sum(x[3]=='physical_row' for x in expected['native_archive_container']),'projection_reuse_rows':sum(x[3]=='projection_reuse' for x in expected['native_archive_container']),'historical_body_copied':False,'corpus_complete':True,'full_7_0':False}
 if release!=want:raise ValueError('release mismatch')
 logical={t:hashlib.sha256('\n'.join(json.dumps(list(x),ensure_ascii=False,separators=(',',':')) for x in expected[t]).encode()).hexdigest() for t in TABLES};runtime=json.loads(Path(runtime_manifest_path).read_text()) if runtime_manifest_path is not None else None
 if runtime and (runtime['release']!=release or runtime['artifact']!={'size_bytes':Path(artifact).stat().st_size,'sha256':sha(artifact)}):raise ValueError('runtime mismatch')
 return {'status':'pass','actual_bytes_git_blob_match':True,'identity_independently_reconstructed':True,'schema_structure_match':True,'write_once_structure_match':True,'unexpected_authoritative_objects':[],'verified_artifact':{'size_bytes':Path(artifact).stat().st_size,'sha256':sha(artifact)},'snapshot_id':scope['snapshot_id'],'scope_sha256':scope_sha,'implementation':impl,'parser_build_id':parser,'run_id':run,'contract':scope['family_contract_version'],'counts':counts,'text_states':dict(sorted(states.items())),'owner_roles':dict(sorted(roles.items())),'relation_states':dict(sorted(relstates.items())),'full_business_column_equality':True,'no_extra_rows':True,'logical_seals':logical,'performance':{'full_verifier_seconds':time.perf_counter()-started,'rows_per_second':counts['native_archive_container']/(time.perf_counter()-started)}}
