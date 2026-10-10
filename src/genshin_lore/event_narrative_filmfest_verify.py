"""Independent verifier for the Filmfest Shooting Event v3 archive."""
import hashlib,json,sqlite3
from collections import Counter,defaultdict
from pathlib import Path
from .event_narrative_source import array_records,check_files,pairs,sha
ROOT=Path(__file__).resolve().parents[2]
TABLES=('event_narrative_row','event_narrative_slot','event_narrative_text','event_narrative_relation','event_narrative_relation_candidate')
def iid(*parts):return 'event-narrative:'+hashlib.sha256(json.dumps(parts,sort_keys=True,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()[:40]
def rows_of(conn,table):
 cols=[x[1] for x in conn.execute('PRAGMA table_info('+table+')')];return [tuple(x) for x in conn.execute('SELECT '+','.join(cols)+' FROM '+table+' ORDER BY '+','.join(cols))]
def verify(root,scope_path,artifact,build_report,talk_paths):
 root=Path(root);scope_path=Path(scope_path);artifact=Path(artifact);scope=json.loads(scope_path.read_text());check_files(root,scope)
 if {'quest_linked','mapped_complement'}!=set(talk_paths):raise ValueError('Filmfest exact Talk artifacts required')
 impl={p:sha(ROOT/p) for p in ('src/genshin_lore/event_narrative_filmfest.py','src/genshin_lore/event_narrative_source.py','schemas/event-narrative-v3.sql')};parser=iid('parser',impl);scope_sha=sha(scope_path);run=iid('run',parser,scope_sha)
 for key,want in (('implementation',impl),('parser_build_id',parser),('scope_sha256',scope_sha),('run_id',run),('snapshot_id',scope['snapshot_id'])):
  if build_report.get(key)!=want:raise ValueError('Filmfest independent identity mismatch: '+key)
 if artifact.stat().st_size!=build_report['artifact']['size_bytes'] or sha(artifact)!=build_report['artifact']['sha256']:raise ValueError('Filmfest artifact fingerprint')
 candidate_rows=[];wanted_ids=set();physical={s['kind']:list(array_records(root/s['path'])) for s in scope['sources']}
 if {s['kind']:len(physical[s['kind']]) for s in scope['sources']}!=scope['row_denominator']:raise ValueError('Filmfest denominator')
 primary=scope['sources'][0]
 for _,row,_ in physical[primary['kind']]:wanted_ids.add(str(row['talkID']))
 artifacts={x['scope_kind']:x for x in scope['talk_candidate_namespace']['artifacts']}
 for kind,path in talk_paths.items():
  path=Path(path);seal=artifacts[kind]
  if path.stat().st_size!=seal['size_bytes'] or sha(path)!=seal['sha256']:raise ValueError('Filmfest Talk artifact seal')
  conn=sqlite3.connect(path);conn.row_factory=sqlite3.Row
  sql="SELECT id,ingest_run_id,native_id,metadata_json FROM quest_talk_physical_container WHERE ingest_run_id=? AND kind='talk_root' AND native_id IN ("+','.join('?' for _ in wanted_ids)+')'
  for item in conn.execute(sql,[seal['run_id'],*sorted(wanted_ids)]):
   if item['ingest_run_id']!=seal['run_id']:raise ValueError('Filmfest Talk exact run')
   meta=json.loads(item['metadata_json']);candidate_rows.append({'raw_locator':item['native_id'],'target_source_path':meta['source_path'],'target_source_pointer':meta['source_pointer'],'target_parser_build_id':seal['parser_build_id'],'target_run_id':item['ingest_run_id'],'target_occurrence_id':item['id'],'scope_kind':kind})
  conn.close()
 if sorted(candidate_rows,key=lambda x:tuple(sorted(x.items())))!=sorted(scope['talk_candidates'],key=lambda x:tuple(sorted(x.items()))):raise ValueError('Filmfest independent Talk candidate enumeration')
 file_seals={x['path']:x for x in scope['files']};lookup=defaultdict(list)
 for x in candidate_rows:lookup[x['raw_locator']].append(x)
 locators=[]
 for source in scope['sources']:
  for ordinal,row,_ in physical[source['kind']]:
   for spec in source['text_owners']:
    value=row.get(spec['field'])
    if spec['shape']=='scalar':
     if type(value) is not int:raise ValueError('Filmfest scalar owner')
     locators.append((source,ordinal,spec['field'],value,spec['role_status']))
    elif spec['shape']=='array':
     if not isinstance(value,list) or any(type(v) is not int for v in value):raise ValueError('Filmfest array owner')
     locators.extend((source,ordinal,f"{spec['field']}/{i}",v,spec['role_status']) for i,v in enumerate(value))
    else:raise ValueError('Filmfest owner shape')
 wanted={str(x[3]) for x in locators};maps={}
 for locale in scope['languages']:
  levels=[]
  for suffix in ('','_Medium'):
   path=f'TextMap/TextMap{suffix}{locale}.json';found=defaultdict(list)
   for pair,(key,text) in enumerate(pairs(root/path)):
    if key in wanted:found[key].append((path,pair,'/'+key,text))
   levels.append(found)
  maps[locale]=levels
 expected={x:[] for x in TABLES};states=Counter();roles=Counter();relation_states=Counter();row_lookup=defaultdict(list);row_ids={}
 for source in scope['sources']:
  for ordinal,row,raw in physical[source['kind']]:
   path=source['path'];rid=iid(run,'row',path,ordinal);row_ids[(source['kind'],ordinal)]=rid;row_lookup[(source['kind'],str(row[source['native_id_field']]))].append((path,'/'+str(ordinal),rid));seal=file_seals[path];expected['event_narrative_row'].append((rid,run,source['boundary'],path,seal['sha256'],seal['blob'],ordinal,'/'+str(ordinal),source['native_id_field'],str(row[source['native_id_field']]),raw,hashlib.sha256(raw.encode()).hexdigest()))
 for source in scope['sources']:
  for ordinal,row,_ in physical[source['kind']]:
   path=source['path'];rid=row_ids[(source['kind'],ordinal)]
   for _,_,field,value,role in (x for x in locators if x[0]['kind']==source['kind'] and x[1]==ordinal):
    roles[role]+=len(scope['languages'])
    for locale in scope['languages']:
     main=maps[locale][0].get(str(value),[]);medium=maps[locale][1].get(str(value),[]);hits=main or medium;status='missing' if not hits else 'resolved_main' if main and len(hits)==1 else 'resolved_medium' if medium and len(hits)==1 else 'ambiguous';states[status]+=1;sid=iid(run,'slot',path,ordinal,field,locale);expected['event_narrative_slot'].append((sid,rid,field,role,locale,str(value),status,len(hits)))
     for tp,pair,pointer,text in hits:
      ts=file_seals[tp];expected['event_narrative_text'].append((iid(run,'text',path,ordinal,field,locale,tp,pair),sid,tp,ts['sha256'],ts['blob'],pair,pointer,text,hashlib.sha256(text.encode()).hexdigest()))
   for spec in source.get('internal_relations',[]):
    values=row.get(spec['field'])
    if not isinstance(values,list):raise ValueError('Filmfest verifier relation array')
    for position,item in enumerate(values):
     if spec['shape']=='array_scalar':value=item
     elif spec['shape'] in ('array_object_id','array_object_id_optional'):
      if not isinstance(item,dict):raise ValueError('Filmfest verifier relation object')
      if 'id' not in item:
       if spec['shape']=='array_object_id_optional':continue
       raise ValueError('Filmfest verifier relation id')
      value=item['id']
     else:raise ValueError('Filmfest verifier relation shape')
     if type(value) is not int:raise ValueError('Filmfest verifier relation locator')
     hits=sorted(row_lookup[(spec['target_kind'],str(value))]);status='missing' if not hits else 'resolved_unique' if len(hits)==1 else 'ambiguous';relation_states[status]+=1;pointer=f'/{ordinal}/{spec["field"]}/{position}';rel=iid(run,'relation',path,pointer,spec['relation_kind']);expected['event_narrative_relation'].append((rel,rid,spec['relation_kind'],spec['field'],pointer,str(value),'filmfest_physical_rows:'+spec['target_kind'],status,len(hits),1))
     for tp,tptr,tid in hits:expected['event_narrative_relation_candidate'].append((iid(run,'relation-candidate',rel,tp,run,tid),rel,tp,tptr,str(value),'event_narrative_archive',scope['family_contract_version'],parser,run,tid))
   if source.get('talk_relation_field'):
    field=source['talk_relation_field'];value=str(row[field]);hits=sorted(lookup[value],key=lambda x:(x['target_source_path'],x['target_run_id'],x['target_occurrence_id']));status='missing' if not hits else 'resolved_unique' if len(hits)==1 else 'ambiguous';relation_states[status]+=1;rel=iid(run,'relation',path,ordinal,field);expected['event_narrative_relation'].append((rel,rid,'source_authored_talk_reference',field,'/'+str(ordinal)+'/'+field,value,'accepted_mapped_talk_program_roots_v7',status,len(hits),1))
    for x in hits:expected['event_narrative_relation_candidate'].append((iid(run,'relation-candidate',rel,x['target_source_path'],x['target_run_id'],x['target_occurrence_id']),rel,x['target_source_path'],x['target_source_pointer'],value,'quest_talk_pilot','quest-talk-evidence-v7',x['target_parser_build_id'],x['target_run_id'],x['target_occurrence_id']))
 conn=sqlite3.connect('file:'+str(artifact.resolve())+'?mode=ro&immutable=1',uri=True);reference=sqlite3.connect(':memory:');reference.executescript((ROOT/'schemas/event-narrative-v3.sql').read_text());structure=reference.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall();reference.close()
 if conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()!=structure:raise ValueError('Filmfest schema structure')
 for table,values in expected.items():
  if sorted(values)!=rows_of(conn,table):raise ValueError('Filmfest full business-column mismatch/no-extra-row: '+table)
 if conn.execute('PRAGMA integrity_check').fetchall()!=[('ok',)] or conn.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Filmfest database integrity')
 release=json.loads(conn.execute('SELECT manifest_json FROM event_narrative_release WHERE id=1').fetchone()[0]);conn.close();counts={k:len(v) for k,v in expected.items()};want_release={'contract':scope['family_contract_version'],'run_id':run,'parser_build_id':parser,'scope_sha256':scope_sha,'implementation':impl,'snapshot_id':scope['snapshot_id'],'counts':counts,'text_state_counts':dict(sorted(states.items())),'owner_role_counts':dict(sorted(roles.items())),'relation_state_counts':dict(sorted(relation_states.items())),'relation_occurrences':counts['event_narrative_relation']}
 if release!=want_release or {k:v for k,v in build_report.items() if k!='artifact'}!=want_release:raise ValueError('Filmfest release mismatch')
 logical={k:hashlib.sha256(json.dumps(sorted(v),ensure_ascii=False,separators=(',',':')).encode()).hexdigest() for k,v in expected.items()}
 return {'status':'pass','actual_bytes_git_blob_match':True,'identity_independently_reconstructed':True,'verified_artifact':{'size_bytes':artifact.stat().st_size,'sha256':sha(artifact)},'snapshot_id':scope['snapshot_id'],'scope_sha256':scope_sha,'implementation':impl,'parser_build_id':parser,'run_id':run,'contract':scope['family_contract_version'],'source_files':len(scope['files']),'counts':counts,'text_states':dict(sorted(states.items())),'owner_roles':dict(sorted(roles.items())),'relation_states':dict(sorted(relation_states.items())),'full_business_column_equality':True,'no_extra_rows':True,'logical_seals':logical}
