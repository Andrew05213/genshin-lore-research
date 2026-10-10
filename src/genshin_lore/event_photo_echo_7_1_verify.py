"""Independent verifier for the remaining Activity narrative archive wave."""
import copy,hashlib,json,sqlite3,time
from collections import Counter,defaultdict
from pathlib import Path
from .event_7_1_source import array_records,check_files,pairs,sha
ROOT=Path(__file__).resolve().parents[2]
HISTORICAL=ROOT/'manifests/event-narrative/photo-echo-scope.json'
TABLES=('event_narrative_row','event_narrative_slot','event_narrative_text','event_narrative_relation','event_narrative_relation_candidate')
def selected_records(root,source):
 records=list(array_records(root/source['path']))
 selected=source.get('selected_native_ids')
 if selected is None:return records
 field=source.get('selection_field')
 if not field or len(selected)!=len(set(selected)):raise ValueError('Photo/Echo 7.1 verifier selection contract')
 wanted={str(value) for value in selected};result=[]
 for record in records:
  row=record[1]
  if field not in row:raise ValueError('Photo/Echo 7.1 verifier selection field')
  if str(row[field]) in wanted:result.append(record)
 if {str(record[1][field]) for record in result}!=wanted or len(result)!=len(wanted):raise ValueError('Photo/Echo 7.1 verifier selected denominator')
 return result
def iid(*parts):return 'event-narrative:'+hashlib.sha256(json.dumps(parts,sort_keys=True,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()[:40]
def rows_of(conn,table):
 cols=[x[1] for x in conn.execute('PRAGMA table_info('+table+')')];return [tuple(x) for x in conn.execute('SELECT '+','.join(cols)+' FROM '+table+' ORDER BY '+','.join(cols))]
def group_owner_occurrences(physical,sources):
 locators=[];grouped=defaultdict(list)
 for source in sources:
  for ordinal,row,_ in physical[source['kind']]:
   for spec in source['text_owners']:
    condition=spec.get('when')
    if condition is not None:
     if condition.get('present') is True and condition['field'] not in row:continue
     if 'equals' in condition and row.get(condition['field'])!=condition['equals']:continue
    value=row.get(spec['field'])
    if spec['shape']=='scalar':
     if type(value) is not int:raise ValueError('Photo/Echo 7.1 scalar owner')
     item=(source,ordinal,spec['field'],value,spec['role_status']);locators.append(item);grouped[(source['kind'],ordinal)].append(item[2:])
    elif spec['shape']=='array':
     if not isinstance(value,list) or any(type(v) is not int for v in value):raise ValueError('Photo/Echo 7.1 array owner')
     additions=[(source,ordinal,f"{spec['field']}/{i}",v,spec['role_status']) for i,v in enumerate(value)];locators.extend(additions);grouped[(source['kind'],ordinal)].extend(x[2:] for x in additions)
    else:raise ValueError('Photo/Echo 7.1 owner shape')
 return locators,grouped
def terminal_sentinel_counts(root,scope):
 counts={}
 for declaration,values in scope.get('terminal_sentinels',{}).items():
  kind,field=declaration.rsplit('.',1);matches=[s for s in scope['sources'] if s['kind']==kind]
  if len(matches)!=1:raise ValueError('Photo/Echo 7.1 terminal sentinel source kind')
  source=matches[0];declared=sorted(values)
  relation_specs=[spec for spec in source.get('internal_relations',[]) if spec.get('field')==field]
  if len(relation_specs)!=1 or sorted(relation_specs[0].get('skip_values',[]))!=declared:raise ValueError('Photo/Echo 7.1 terminal sentinel/skip contract mismatch')
  full=[row for _,row,_ in array_records(Path(root)/source['path'])]
  counts[declaration]=sum(1 for row in full if row.get(field) in values)
 return counts
def verify_photo_echo_delta_selection(root,scope):
 if scope.get('contract')!='event-narrative-photo-echo-delta-source-v1':return
 required={
  'ExcelBinOutput/ActivityPhotographExcelConfigData.json',
  'ExcelBinOutput/ActivityPhotographPageExcelConfigData.json',
  'ExcelBinOutput/ActivityPhotographPosExtendExcelConfigData.json',
  'ExcelBinOutput/EchoShellExcelConfigData.json'}
 if set(scope.get('selector_inputs',()))!=required:raise ValueError('Photo/Echo 7.1 selector input contract')
 sealed={item['path'] for item in scope['files']}
 if not required<=sealed:raise ValueError('Photo/Echo 7.1 selector inputs not sealed')
 def load(name):return json.loads((root/name).read_text())
 photo_root=load('ExcelBinOutput/ActivityPhotographExcelConfigData.json')
 pages=load('ExcelBinOutput/ActivityPhotographPageExcelConfigData.json')
 extensions=load('ExcelBinOutput/ActivityPhotographPosExtendExcelConfigData.json')
 positions=load('ExcelBinOutput/ActivityPhotographPosExcelConfigData.json')
 echo=load('ExcelBinOutput/EchoShellExcelConfigData.json')
 talks=load('ExcelBinOutput/TalkExcelConfigData_1.json')
 accepted=set(photo_root[0]['KDLEHDLHHOD'])
 if accepted!={value for row in pages for value in row['FLKPLMMNLFL']} or accepted!={row['id'] for row in extensions}:raise ValueError('Photo/Echo 7.1 Photograph selection chain')
 derived={
  'activity_photograph_historical_position':sorted({row['id'] for row in positions}-accepted),
  'echo_shell_reminder':sorted({value for row in echo for value in row.get('reminderList',[])}),
  'echo_shell_dialog':sorted({value for row in echo for value in row.get('voiceList',[])})}
 derived['echo_shell_talk_support']=sorted(row['id'] for row in talks if row.get('initDialog') in set(derived['echo_shell_dialog']))
 declared={source['kind']:source.get('selected_native_ids') for source in scope['sources']}
 if derived!=declared:raise ValueError('Photo/Echo 7.1 independently reconstructed selection mismatch')
def verify_photo_echo_7_1_selection(root,scope):
 sources={item['kind']:item for item in scope['sources']}
 expected_kinds={'activity_photograph_root','activity_photograph_page','activity_photograph_position','activity_photograph_position_extension','echo_shell','echo_shell_story'}
 if set(sources)!=expected_kinds:raise ValueError('Photo/Echo 7.1 source kind denominator')
 root_rows=list(array_records(Path(root)/sources['activity_photograph_root']['path']))
 pages=list(array_records(Path(root)/sources['activity_photograph_page']['path']))
 extensions=list(array_records(Path(root)/sources['activity_photograph_position_extension']['path']))
 positions=list(array_records(Path(root)/sources['activity_photograph_position']['path']))
 if len(root_rows)!=1:raise ValueError('Photo/Echo 7.1 root denominator')
 selected=root_rows[0][1].get('JPAAHMBJGOJ')
 if not isinstance(selected,list) or any(type(x) is not int for x in selected) or len(selected)!=len(set(selected)):
  raise ValueError('Photo/Echo 7.1 root selector shape')
 page_ids=[]
 for _,row,_ in pages:
  values=row.get('JOLGNPDDIJK')
  if not isinstance(values,list) or any(type(x) is not int for x in values):raise ValueError('Photo/Echo 7.1 page selector shape')
  page_ids.extend(values)
 extension_ids=[row.get('id') for _,row,_ in extensions]
 position_ids=[row.get('id') for _,row,_ in positions]
 if (len(page_ids)!=len(set(page_ids)) or len(extension_ids)!=len(set(extension_ids))
     or len(position_ids)!=len(set(position_ids)) or set(selected)!=set(page_ids)
     or set(selected)!=set(extension_ids) or not set(selected)<=set(position_ids)):
  raise ValueError('Photo/Echo 7.1 source-owned selector disagreement')
 declaration=sources['activity_photograph_position']
 if (declaration.get('selection_field')!='id' or declaration.get('selected_native_ids')!=sorted(selected)
     or len([id_ for id_ in position_ids if id_ in set(selected)])!=len(selected)):
  raise ValueError('Photo/Echo 7.1 manifest selector differs from source')
def verify(root,scope_path,artifact,build_report,dependency_paths):
 started=time.perf_counter()
 root=Path(root);scope_path=Path(scope_path);artifact=Path(artifact);scope=json.loads(scope_path.read_text());check_files(root,scope)
 verify_photo_echo_7_1_selection(root,scope)
 historical=json.loads(HISTORICAL.read_text())
 if scope.get('contract')!='event-photo-echo-7.1-versioned-source-v1' or scope.get('historical_scope_sha256')!=sha(HISTORICAL) or scope.get('family_contract_version')!=historical['family_contract_version'] or {x['path'] for x in scope['sources']}!={x['path'] for x in historical['sources']} or len(scope['sources'])!=6 or len(scope['files'])!=12:
  raise ValueError('Photo/Echo 7.1 historical selector/contract drift')
 aliases={'KDLEHDLHHOD':'JPAAHMBJGOJ','FLKPLMMNLFL':'JOLGNPDDIJK','DLGNPIDJDAD':'EHLBLMHEAAB','EINGJLOCLJO':'PKPHBGMFODP','HPAMAMOHMMM':'ICMKPBCEJJB','NPDJPIBIDDG':'ELJEELOLFPI'}
 if scope.get('field_renames')!=aliases:raise ValueError('Photo/Echo 7.1 field alias contract')
 expected_sources=copy.deepcopy(historical['sources'])
 for declaration in expected_sources:
  for owner in declaration.get('text_owners',[]):owner['field']=aliases.get(owner['field'],owner['field'])
  for relation in declaration.get('internal_relations',[]):relation['field']=aliases.get(relation['field'],relation['field'])
  if declaration['kind']=='activity_photograph_position':
   declaration['selected_native_ids']=next(x['selected_native_ids'] for x in scope['sources'] if x['kind']=='activity_photograph_position')
 if scope['sources']!=expected_sources:raise ValueError('Photo/Echo 7.1 historical source projection differs beyond declared aliases')
 if any(Path(str(artifact)+suffix).exists() for suffix in ('-wal','-shm','-journal')):raise ValueError('Photo/Echo 7.1 sidecar')
 verify_photo_echo_delta_selection(root,scope)
 talk_paths={k:v for k,v in dependency_paths.items() if k in ('quest_linked','mapped_complement')};external_path=dependency_paths.get('historical_event')
 needs_talk=any(s.get('talk_relations') for s in scope['sources'])
 if needs_talk and {'quest_linked','mapped_complement'}!=set(talk_paths):raise ValueError('Photo/Echo 7.1 exact Talk artifacts required')
 if not needs_talk and talk_paths:raise ValueError('Photo/Echo 7.1 unexpected Talk artifact dependency')
 impl={p:sha(ROOT/p) for p in ('src/genshin_lore/event_photo_echo_7_1_build.py','src/genshin_lore/event_7_1_source.py','schemas/event-photo-echo-7.1-v1.sql')};parser=iid('parser',impl);scope_sha=sha(scope_path);run=iid('run',parser,scope_sha)
 for key,want in (('implementation',impl),('parser_build_id',parser),('scope_sha256',scope_sha),('run_id',run),('snapshot_id',scope['snapshot_id'])):
   if build_report.get(key)!=want:raise ValueError('Photo/Echo 7.1 independent identity mismatch: '+key)
 if artifact.stat().st_size!=build_report['artifact']['size_bytes'] or sha(artifact)!=build_report['artifact']['sha256']:raise ValueError('Photo/Echo 7.1 artifact fingerprint')
 candidate_rows=[];wanted_ids=set();physical={s['kind']:selected_records(root,s) for s in scope['sources']};source_scanned=time.perf_counter()
 if {s['kind']:len(physical[s['kind']]) for s in scope['sources']}!=scope['row_denominator']:raise ValueError('Photo/Echo 7.1 denominator')
 terminal_counts=terminal_sentinel_counts(root,scope)
 for source in scope['sources']:
  for _,row,_ in physical[source['kind']]:
   for spec in source.get('talk_relations',[]):
    value=row[spec['field']];wanted_ids.update(str(x) for x in (value if spec['shape']=='array' else [value]))
 artifacts={x['scope_kind']:x for x in scope.get('talk_candidate_namespace',{}).get('artifacts',[])}
 for kind,path in talk_paths.items():
  path=Path(path);seal=artifacts[kind]
  if path.stat().st_size!=seal['size_bytes'] or sha(path)!=seal['sha256']:raise ValueError('Photo/Echo 7.1 Talk artifact seal')
  conn=sqlite3.connect(path);conn.row_factory=sqlite3.Row
  sql="SELECT id,ingest_run_id,native_id,metadata_json FROM quest_talk_physical_container WHERE ingest_run_id=? AND kind='talk_root' AND native_id IN ("+','.join('?' for _ in wanted_ids)+')'
  for item in conn.execute(sql,[seal['run_id'],*sorted(wanted_ids)]):
   if item['ingest_run_id']!=seal['run_id']:raise ValueError('Photo/Echo 7.1 Talk exact run')
   meta=json.loads(item['metadata_json']);candidate_rows.append({'raw_locator':item['native_id'],'target_source_path':meta['source_path'],'target_source_pointer':meta['source_pointer'],'target_parser_build_id':seal['parser_build_id'],'target_run_id':item['ingest_run_id'],'target_occurrence_id':item['id'],'scope_kind':kind})
  conn.close()
 if sorted(candidate_rows,key=lambda x:tuple(sorted(x.items())))!=sorted(scope['talk_candidates'],key=lambda x:tuple(sorted(x.items()))):raise ValueError('Photo/Echo 7.1 independent Talk candidate enumeration')
 external_rows=[];external_seal=scope.get('external_candidate_namespace',{}).get('artifact')
 if external_seal:
  if external_path is None:raise ValueError('Photo/Echo 7.1 historical exact artifact required')
  external_path=Path(external_path)
  if external_path.stat().st_size!=external_seal['size_bytes'] or sha(external_path)!=external_seal['sha256']:raise ValueError('Photo/Echo 7.1 historical artifact seal')
  wanted={(x['target_kind'],str(x['raw_locator'])) for x in scope.get('external_relation_candidates',[])}
  old=sqlite3.connect(external_path);old.row_factory=sqlite3.Row
  for item in old.execute('SELECT id,source_path,pointer,native_id FROM event_narrative_row WHERE run_id=?',(external_seal['run_id'],)):
   kind=external_seal['source_kind_by_path'].get(item['source_path'])
   if (kind,str(item['native_id'])) in wanted:external_rows.append({'target_kind':kind,'raw_locator':str(item['native_id']),'target_source_path':item['source_path'],'target_source_pointer':item['pointer'],'target_native_id':str(item['native_id']),'target_family':'event_narrative_archive','target_contract':external_seal['contract'],'target_parser_build_id':external_seal['parser_build_id'],'target_run_id':external_seal['run_id'],'target_occurrence_id':item['id']})
  old.close()
  if sorted(external_rows,key=lambda x:tuple(sorted(x.items())))!=sorted(scope.get('external_relation_candidates',[]),key=lambda x:tuple(sorted(x.items()))):raise ValueError('Photo/Echo 7.1 independent historical candidate enumeration')
 elif external_path is not None:raise ValueError('Photo/Echo 7.1 unexpected historical artifact dependency')
 file_seals={x['path']:x for x in scope['files']};lookup=defaultdict(list)
 for x in candidate_rows:lookup[x['raw_locator']].append(x)
 locators,owners_by_occurrence=group_owner_occurrences(physical,scope['sources'])
 owners_reconstructed=time.perf_counter();wanted={str(x[3]) for x in locators};maps={}
 for locale in scope['languages']:
  levels=[]
  for suffix in ('','_Medium'):
   path=f'TextMap/TextMap{suffix}{locale}.json';found=defaultdict(list)
   for pair,(key,text) in enumerate(pairs(root/path)):
    if key in wanted:found[key].append((path,pair,'/'+key,text))
   levels.append(found)
  maps[locale]=levels
 expected={x:[] for x in TABLES};states=Counter();roles=Counter();relation_states=Counter();row_lookup=defaultdict(list);compound=defaultdict(list);row_ids={}
 for item in external_rows:row_lookup[(item['target_kind'],str(item['raw_locator']))].append(item)
 for source in scope['sources']:
  for ordinal,row,raw in physical[source['kind']]:
   path=source['path'];rid=iid(run,'row',path,ordinal);row_ids[(source['kind'],ordinal)]=rid;lookup_field=source.get('relation_lookup_field')
   if lookup_field is not None:
    if lookup_field not in row:
     if not source.get('relation_lookup_field_optional'):raise ValueError('Photo/Echo 7.1 verifier relation lookup shape')
    else:row_lookup[(source['kind'],str(row[lookup_field]))].append({'target_source_path':path,'target_source_pointer':'/'+str(ordinal),'target_native_id':str(row[lookup_field]),'target_family':'event_narrative_archive','target_contract':scope['family_contract_version'],'target_parser_build_id':parser,'target_run_id':run,'target_occurrence_id':rid})
   for fields in source.get('relation_lookup_field_sets',[]):
    if any(field not in row for field in fields):raise ValueError('Photo/Echo 7.1 verifier composite lookup shape')
    compound[(source['kind'],tuple(fields),tuple(str(row[field]) for field in fields))].append((path,'/'+str(ordinal),rid))
   native_field=source.get('native_id_field');native=None if native_field is None else str(row[native_field]);seal=file_seals[path];expected['event_narrative_row'].append((rid,run,source['boundary'],path,seal['sha256'],seal['blob'],ordinal,'/'+str(ordinal),native_field,native,raw,hashlib.sha256(raw.encode()).hexdigest()))
 for source in scope['sources']:
  for ordinal,row,_ in physical[source['kind']]:
   path=source['path'];rid=row_ids[(source['kind'],ordinal)]
   for field,value,role in owners_by_occurrence[(source['kind'],ordinal)]:
    roles[role]+=len(scope['languages'])
    for locale in scope['languages']:
     main=maps[locale][0].get(str(value),[]);medium=maps[locale][1].get(str(value),[]);hits=main or medium;status='missing' if not hits else 'resolved_main' if main and len(hits)==1 else 'resolved_medium' if medium and len(hits)==1 else 'ambiguous';states[status]+=1;sid=iid(run,'slot',path,ordinal,field,locale);expected['event_narrative_slot'].append((sid,rid,field,role,locale,str(value),status,len(hits)))
     for tp,pair,pointer,text in hits:
      ts=file_seals[tp];expected['event_narrative_text'].append((iid(run,'text',path,ordinal,field,locale,tp,pair),sid,tp,ts['sha256'],ts['blob'],pair,pointer,text,hashlib.sha256(text.encode()).hexdigest()))
   for spec in source.get('internal_relations',[]):
    values=row.get(spec['field'])
    if spec['shape']=='scalar':values=[values]
    elif spec['shape']=='scalar_optional':
     if values is None:continue
     values=[values]
    elif not isinstance(values,list):raise ValueError('Photo/Echo 7.1 verifier relation array')
    for position,item in enumerate(values):
     if spec['shape'] in ('array_scalar','scalar','scalar_optional'):value=item
     elif spec['shape'] in ('array_object_id','array_object_id_optional'):
      if not isinstance(item,dict):raise ValueError('Photo/Echo 7.1 verifier relation object')
      if 'id' not in item:
       if spec['shape']=='array_object_id_optional':continue
       raise ValueError('Photo/Echo 7.1 verifier relation id')
      value=item['id']
     else:raise ValueError('Photo/Echo 7.1 verifier relation shape')
     if type(value) is not int:raise ValueError('Photo/Echo 7.1 verifier relation locator')
     if value in spec.get('skip_values',[]):continue
     target_fields=spec.get('target_lookup_fields')
     if target_fields:
      source_fields=spec['source_lookup_fields'];key=tuple(str(row[field]) for field in source_fields);hits=sorted(compound[(spec['target_kind'],tuple(target_fields),key)])
     else:hits=sorted(row_lookup[(spec['target_kind'],str(value))],key=lambda x:(x['target_source_path'],x['target_source_pointer'],x['target_run_id'],x['target_occurrence_id']))
     inferred='missing' if not hits else 'resolved_unique' if len(hits)==1 else 'ambiguous';status=spec.get('status',inferred);search_complete=int(spec.get('search_complete',True));relation_states[status]+=1;pointer=f'/{ordinal}/{spec["field"]}'+('' if spec['shape'] in ('scalar','scalar_optional') else f'/{position}');rel=iid(run,'relation',path,pointer,spec['relation_kind']);namespace=spec.get('candidate_namespace','event_wave_physical_rows:'+spec['target_kind']);expected['event_narrative_relation'].append((rel,rid,spec['relation_kind'],spec['field'],pointer,str(value),namespace,status,len(hits),search_complete))
     for hit in hits:expected['event_narrative_relation_candidate'].append((iid(run,'relation-candidate',rel,hit['target_source_path'],hit['target_run_id'],hit['target_occurrence_id']),rel,hit['target_source_path'],hit['target_source_pointer'],str(value),hit['target_family'],hit['target_contract'],hit['target_parser_build_id'],hit['target_run_id'],hit['target_occurrence_id']))
   for spec in source.get('talk_relations',[]):
    field=spec['field'];raw=row[field];values=raw if spec['shape']=='array' else [raw]
    if not isinstance(values,list) or any(type(x) is not int for x in values):raise ValueError('Photo/Echo 7.1 verifier Talk locator shape')
    for position,raw_value in enumerate(values):
     value=str(raw_value);hits=sorted(lookup[value],key=lambda x:(x['target_source_path'],x['target_run_id'],x['target_occurrence_id']));status='missing' if not hits else 'resolved_unique' if len(hits)==1 else 'ambiguous';relation_states[status]+=1;pointer=f'/{ordinal}/{field}'+('' if spec['shape']=='scalar' else f'/{position}');rel=iid(run,'relation',path,pointer,spec['relation_kind']);expected['event_narrative_relation'].append((rel,rid,spec['relation_kind'],field,pointer,value,'accepted_mapped_talk_program_roots_v7',status,len(hits),1))
     for x in hits:expected['event_narrative_relation_candidate'].append((iid(run,'relation-candidate',rel,x['target_source_path'],x['target_run_id'],x['target_occurrence_id']),rel,x['target_source_path'],x['target_source_pointer'],value,'quest_talk_pilot','quest-talk-evidence-v7',x['target_parser_build_id'],x['target_run_id'],x['target_occurrence_id']))
 evidence_reconstructed=time.perf_counter();conn=sqlite3.connect('file:'+str(artifact.resolve())+'?mode=ro&immutable=1',uri=True);reference=sqlite3.connect(':memory:');reference.executescript((ROOT/'schemas/event-photo-echo-7.1-v1.sql').read_text());structure=reference.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall();reference.close()
 if conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()!=structure:raise ValueError('Photo/Echo 7.1 schema structure')
 for table,values in expected.items():
  if sorted(values)!=rows_of(conn,table):raise ValueError('Photo/Echo 7.1 full business-column mismatch/no-extra-row: '+table)
 if conn.execute('PRAGMA integrity_check').fetchall()!=[('ok',)] or conn.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Photo/Echo 7.1 database integrity')
 release=json.loads(conn.execute('SELECT manifest_json FROM event_narrative_release WHERE id=1').fetchone()[0]);conn.close();counts={k:len(v) for k,v in expected.items()};want_release={'contract':scope['family_contract_version'],'run_id':run,'parser_build_id':parser,'scope_sha256':scope_sha,'implementation':impl,'snapshot_id':scope['snapshot_id'],'counts':counts,'text_state_counts':dict(sorted(states.items())),'owner_role_counts':dict(sorted(roles.items())),'relation_state_counts':dict(sorted(relation_states.items())),'relation_occurrences':counts['event_narrative_relation']}
 if release!=want_release or {k:v for k,v in build_report.items() if k!='artifact'}!=want_release:raise ValueError('Photo/Echo 7.1 release mismatch')
 logical={k:hashlib.sha256(json.dumps(sorted(v),ensure_ascii=False,separators=(',',':')).encode()).hexdigest() for k,v in expected.items()}
 finished=time.perf_counter();return {'status':'pass','actual_bytes_git_blob_match':True,'identity_independently_reconstructed':True,'verified_artifact':{'size_bytes':artifact.stat().st_size,'sha256':sha(artifact)},'snapshot_id':scope['snapshot_id'],'scope_sha256':scope_sha,'implementation':impl,'parser_build_id':parser,'run_id':run,'contract':scope['family_contract_version'],'source_files':len(scope['files']),'counts':counts,'text_states':dict(sorted(states.items())),'owner_roles':dict(sorted(roles.items())),'relation_states':dict(sorted(relation_states.items())),'terminal_sentinel_counts':dict(sorted(terminal_counts.items())),'full_business_column_equality':True,'no_extra_rows':True,'logical_seals':logical,'performance':{'source_scan_seconds':source_scanned-started,'owner_occurrence_grouping_seconds':owners_reconstructed-source_scanned,'text_lookup_and_relation_reconstruction_seconds':evidence_reconstructed-owners_reconstructed,'database_comparison_and_hash_seconds':finished-evidence_reconstructed,'full_verifier_seconds':finished-started,'physical_rows_per_second':counts['event_narrative_row']/(finished-started)}}
