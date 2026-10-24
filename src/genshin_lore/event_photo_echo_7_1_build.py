"""Snapshot-local Photograph/EchoShell successor, bounded to its 7.0 contract."""
import hashlib,json,sqlite3
from collections import Counter,defaultdict
from pathlib import Path
from .event_7_1_source import array_records,check_files,pairs,sha
from .batch_release import assert_output_paths, create_private_staging, publish_absent, sidecars
ROOT=Path(__file__).resolve().parents[2]
HISTORICAL=ROOT/'manifests/event-narrative/photo-echo-scope.json'
SCHEMA=ROOT/'schemas/event-photo-echo-7.1-v1.sql'
TABLES=('event_narrative_row','event_narrative_slot','event_narrative_text','event_narrative_relation','event_narrative_relation_candidate')
def ident(*parts):return 'event-narrative:'+hashlib.sha256(json.dumps(parts,sort_keys=True,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()[:40]
def implementation():return {p:sha(ROOT/p) for p in ('src/genshin_lore/event_photo_echo_7_1_build.py','src/genshin_lore/event_7_1_source.py','schemas/event-photo-echo-7.1-v1.sql')}
def identities(scope_path):
 impl=implementation();parser=ident('parser',impl);scope_sha=sha(scope_path);return impl,parser,scope_sha,ident('run',parser,scope_sha)
def owners(row,specs):
 for spec in specs:
  condition=spec.get('when')
  if condition is not None:
   if condition.get('present') is True and condition['field'] not in row:continue
   if 'equals' in condition and row.get(condition['field'])!=condition['equals']:continue
  field=spec['field'];value=row.get(field)
  if spec['shape']=='scalar':
   if type(value) is not int:raise ValueError('Event wave scalar locator shape: '+field)
   yield field,value,spec['role_status']
  elif spec['shape']=='array':
   if not isinstance(value,list) or any(type(x) is not int for x in value):raise ValueError('Event wave array locator shape: '+field)
   for ordinal,locator in enumerate(value):yield f'{field}/{ordinal}',locator,spec['role_status']
  else:raise ValueError('Event wave owner shape')
def selected_records(root,source):
 records=list(array_records(root/source['path']))
 selected=source.get('selected_native_ids')
 if selected is None:return records
 field=source.get('selection_field')
 if not field or len(selected)!=len(set(selected)):raise ValueError('Event wave selection contract')
 wanted={str(value) for value in selected};result=[]
 for record in records:
  row=record[1]
  if field not in row:raise ValueError('Event wave selection field')
  if str(row[field]) in wanted:result.append(record)
 found={str(record[1][field]) for record in result}
 if found!=wanted or len(result)!=len(wanted):raise ValueError('Event wave selected denominator')
 return result
def reconstruct(root,scope_path):
 root=Path(root);scope_path=Path(scope_path);manifest=json.loads(scope_path.read_text());check_files(root,manifest);impl,parser,scope_sha,run=identities(scope_path)
 historical=json.loads(HISTORICAL.read_text())
 if manifest.get('contract')!='event-photo-echo-7.1-versioned-source-v1' or manifest.get('historical_scope_sha256')!=sha(HISTORICAL) or manifest.get('family_contract_version')!=historical['family_contract_version'] or {x['path'] for x in manifest['sources']}!={x['path'] for x in historical['sources']} or len(manifest['sources'])!=6 or len(manifest['files'])!=12:
  raise ValueError('Photo/Echo historical selector/contract drift')
 file_seals={x['path']:x for x in manifest['files']};physical={s['kind']:selected_records(root,s) for s in manifest['sources']}
 if {s['kind']:len(physical[s['kind']]) for s in manifest['sources']}!=manifest['row_denominator']:raise ValueError('Event wave row denominator')
 owner_rows=[(s,ordinal,row,field,locator,role) for s in manifest['sources'] for ordinal,row,_ in physical[s['kind']] for field,locator,role in owners(row,s['text_owners'])]
 wanted={str(x[4]) for x in owner_rows};maps={}
 for locale in manifest['languages']:
  tiers=[]
  for suffix in ('','_Medium'):
   path=f'TextMap/TextMap{suffix}{locale}.json';lookup=defaultdict(list)
   for ordinal,(key,text) in enumerate(pairs(root/path)):
    if key in wanted:lookup[key].append((path,ordinal,'/'+key,text))
   tiers.append(lookup)
  maps[locale]=tiers
 expected={x:[] for x in TABLES};states=Counter();roles=Counter();relations=Counter()
 candidates_by_locator=defaultdict(list);row_lookup=defaultdict(list);composite_lookup=defaultdict(list);row_ids={}
 for item in manifest['talk_candidates']:candidates_by_locator[str(item['raw_locator'])].append(item)
 for item in manifest.get('external_relation_candidates',[]):row_lookup[(item['target_kind'],str(item['raw_locator']))].append(item)
 for source in manifest['sources']:
  for ordinal,row,raw in physical[source['kind']]:
   path=source['path'];native_field=source.get('native_id_field');native=None if native_field is None else str(row[native_field]);rid=ident(run,'row',path,ordinal);seal=file_seals[path];row_ids[(source['kind'],ordinal)]=rid
   lookup_field=source.get('relation_lookup_field')
   if lookup_field is not None:
    if lookup_field not in row:
     if not source.get('relation_lookup_field_optional'):raise ValueError('Event wave relation lookup shape')
    else:row_lookup[(source['kind'],str(row[lookup_field]))].append({'target_source_path':path,'target_source_pointer':'/'+str(ordinal),'target_native_id':str(row[lookup_field]),'target_family':'event_narrative_archive','target_contract':manifest['family_contract_version'],'target_parser_build_id':parser,'target_run_id':run,'target_occurrence_id':rid})
   for fields in source.get('relation_lookup_field_sets',[]):
    if any(field not in row for field in fields):raise ValueError('Event wave composite lookup shape')
    composite_lookup[(source['kind'],tuple(fields),tuple(str(row[field]) for field in fields))].append((path,'/'+str(ordinal),rid))
   expected['event_narrative_row'].append((rid,run,source['boundary'],path,seal['sha256'],seal['blob'],ordinal,'/'+str(ordinal),native_field,native,raw,hashlib.sha256(raw.encode()).hexdigest()))
 for source in manifest['sources']:
  for ordinal,row,_ in physical[source['kind']]:
   path=source['path'];rid=row_ids[(source['kind'],ordinal)]
   for field,locator,role in owners(row,source['text_owners']):
    roles[role]+=len(manifest['languages'])
    for locale in manifest['languages']:
     main=maps[locale][0].get(str(locator),[]);medium=maps[locale][1].get(str(locator),[]);hits=main or medium
     status='missing' if not hits else 'resolved_main' if main and len(hits)==1 else 'resolved_medium' if medium and len(hits)==1 else 'ambiguous';states[status]+=1;sid=ident(run,'slot',path,ordinal,field,locale)
     expected['event_narrative_slot'].append((sid,rid,field,role,locale,str(locator),status,len(hits)))
     for text_path,pair,pointer,text in hits:
      tseal=file_seals[text_path];expected['event_narrative_text'].append((ident(run,'text',path,ordinal,field,locale,text_path,pair),sid,text_path,tseal['sha256'],tseal['blob'],pair,pointer,text,hashlib.sha256(text.encode()).hexdigest()))
   for spec in source.get('internal_relations',[]):
    values=row.get(spec['field']);
    if spec['shape']=='scalar':values=[values]
    elif spec['shape']=='scalar_optional':
     if values is None:continue
     values=[values]
    elif not isinstance(values,list):raise ValueError('Event wave internal relation array')
    for position,value in enumerate(values):
     if spec['shape'] in ('array_scalar','scalar','scalar_optional'):locator=value
     elif spec['shape'] in ('array_object_id','array_object_id_optional'):
      if not isinstance(value,dict):raise ValueError('Event wave internal relation object')
      if 'id' not in value:
       if spec['shape']=='array_object_id_optional':continue
       raise ValueError('Event wave internal relation id')
      locator=value['id']
     else:raise ValueError('Event wave internal relation shape')
     if type(locator) is not int:raise ValueError('Event wave internal relation locator')
     if locator in spec.get('skip_values',[]):continue
     target_fields=spec.get('target_lookup_fields')
     if target_fields:
      source_fields=spec['source_lookup_fields'];key=tuple(str(row[field]) for field in source_fields);hits=sorted(composite_lookup[(spec['target_kind'],tuple(target_fields),key)])
     else:hits=sorted(row_lookup[(spec['target_kind'],str(locator))],key=lambda x:(x['target_source_path'],x['target_source_pointer'],x['target_run_id'],x['target_occurrence_id']))
     inferred='missing' if not hits else 'resolved_unique' if len(hits)==1 else 'ambiguous';status=spec.get('status',inferred);search_complete=int(spec.get('search_complete',True));relations[status]+=1;pointer=f'/{ordinal}/{spec["field"]}'+('' if spec['shape'] in ('scalar','scalar_optional') else f'/{position}');rel=ident(run,'relation',path,pointer,spec['relation_kind'])
     namespace=spec.get('candidate_namespace','event_wave_physical_rows:'+spec['target_kind'])
     expected['event_narrative_relation'].append((rel,rid,spec['relation_kind'],spec['field'],pointer,str(locator),namespace,status,len(hits),search_complete))
     for hit in hits:expected['event_narrative_relation_candidate'].append((ident(run,'relation-candidate',rel,hit['target_source_path'],hit['target_run_id'],hit['target_occurrence_id']),rel,hit['target_source_path'],hit['target_source_pointer'],str(locator),hit['target_family'],hit['target_contract'],hit['target_parser_build_id'],hit['target_run_id'],hit['target_occurrence_id']))
   for spec in source.get('talk_relations',[]):
    field=spec['field'];raw=row[field];values=raw if spec['shape']=='array' else [raw]
    if not isinstance(values,list) or any(type(x) is not int for x in values):raise ValueError('Event wave Talk locator shape')
    for position,value in enumerate(values):
     locator=str(value);hits=sorted(candidates_by_locator[locator],key=lambda x:(x['target_source_path'],x['target_run_id'],x['target_occurrence_id']));status='missing' if not hits else 'resolved_unique' if len(hits)==1 else 'ambiguous';relations[status]+=1;pointer=f'/{ordinal}/{field}'+('' if spec['shape']=='scalar' else f'/{position}');relation_id=ident(run,'relation',path,pointer,spec['relation_kind'])
     expected['event_narrative_relation'].append((relation_id,rid,spec['relation_kind'],field,pointer,locator,'accepted_mapped_talk_program_roots_v7',status,len(hits),1))
     for item in hits:expected['event_narrative_relation_candidate'].append((ident(run,'relation-candidate',relation_id,item['target_source_path'],item['target_run_id'],item['target_occurrence_id']),relation_id,item['target_source_path'],item['target_source_pointer'],locator,'quest_talk_pilot','quest-talk-evidence-v7',item['target_parser_build_id'],item['target_run_id'],item['target_occurrence_id']))
 counts={k:len(v) for k,v in expected.items()};release={'contract':manifest['family_contract_version'],'run_id':run,'parser_build_id':parser,'scope_sha256':scope_sha,'implementation':impl,'snapshot_id':manifest['snapshot_id'],'counts':counts,'text_state_counts':dict(sorted(states.items())),'owner_role_counts':dict(sorted(roles.items())),'relation_state_counts':dict(sorted(relations.items())),'relation_occurrences':counts['event_narrative_relation']}
 return manifest,expected,release
def build(root,scope_path,output):
 output=Path(output).absolute();root=Path(root).absolute();scope_path=Path(scope_path).absolute()
 scope=json.loads(scope_path.read_text())
 inputs=[('source_root',root),('scope',scope_path),('historical_scope',HISTORICAL),('schema',SCHEMA)]
 inputs.extend((f'source_{i}',root/item['path']) for i,item in enumerate(scope['files']))
 assert_output_paths(output,inputs=inputs)
 manifest,expected,release=reconstruct(root,scope_path)
 descriptor,stage=create_private_staging(output,prefix='.event-photo-echo-7.1-')
 stage_identity=(stage.stat().st_dev,stage.stat().st_ino);conn=None
 try:
  import os
  os.close(descriptor)
  conn=sqlite3.connect(stage);conn.execute('PRAGMA foreign_keys=ON');conn.executescript(SCHEMA.read_text())
  for table in TABLES:
   marks=','.join('?' for _ in expected[table][0]) if expected[table] else ''
   if marks:conn.executemany('INSERT INTO '+table+' VALUES('+marks+')',expected[table])
  conn.execute('INSERT INTO event_narrative_release VALUES(1,?)',(json.dumps(release,sort_keys=True),));conn.commit()
  conn.execute('VACUUM')
  if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or conn.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Photo/Echo staging integrity/FK')
  for table in TABLES:
   columns=[x[1] for x in conn.execute('PRAGMA table_info('+table+')')]
   actual=[tuple(x) for x in conn.execute('SELECT '+','.join(columns)+' FROM '+table+' ORDER BY '+','.join(columns))]
   if actual!=sorted(expected[table]):raise ValueError('Photo/Echo staging full rowset: '+table)
  if json.loads(conn.execute('SELECT manifest_json FROM event_narrative_release WHERE id=1').fetchone()[0])!=release:raise ValueError('Photo/Echo staging release')
  conn.close();conn=None
  if any(path.exists() for path in sidecars(stage)):raise ValueError('Photo/Echo staging sidecar')
  check_files(root,manifest)
  seal={'size_bytes':stage.stat().st_size,'sha256':sha(stage)}
  publish_absent(output,stage,inputs=inputs)
  if output.stat().st_size!=seal['size_bytes'] or sha(output)!=seal['sha256']:raise ValueError('Photo/Echo published seal drift')
  return {**release,'artifact':seal}
 finally:
  if conn is not None:conn.close()
