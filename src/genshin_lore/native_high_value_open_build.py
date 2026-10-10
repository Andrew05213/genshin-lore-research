"""Builder for the High-Value Open Closure successor."""
from __future__ import annotations
import hashlib,json,os,sqlite3
from collections import Counter,defaultdict
from pathlib import Path
from .event_narrative_source import array_records,check_files,pairs,sha

ROOT=Path(__file__).resolve().parents[2]
TABLES=('native_archive_container','native_archive_slot','native_archive_text','native_archive_relation','native_archive_candidate')

def ident(*parts):return 'native-archive:'+hashlib.sha256(json.dumps(parts,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:40]
def implementation():return {p:sha(ROOT/p) for p in ('src/genshin_lore/native_high_value_open_build.py','src/genshin_lore/event_narrative_source.py','schemas/native-archive-v1.sql','manifests/native-archive/high-value-open-selection.json')}
def identities(scope_path):
 impl=implementation();parser='native-archive-parser:'+hashlib.sha256(json.dumps(impl,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:40];scope_sha=sha(scope_path);run='native-archive-run:'+hashlib.sha256((parser+':'+scope_sha).encode()).hexdigest()[:40];return impl,parser,scope_sha,run

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

def _maps(root,scope,wanted):
 out={};filemap={x['path']:x for x in scope['files']}
 for locale in scope['languages']:
  sets=[]
  for tier in ('main','medium'):
   path=scope['textmaps'][locale][tier];d=defaultdict(list)
   for ordinal,(k,v) in enumerate(pairs(Path(root)/path)):
    if k in wanted:d[k].append((path,ordinal,'/'+k,v,filemap[path]))
   sets.append(d)
  out[locale]=sets
 return out

def reconstruct(source_root,scope_path):
 scope=json.loads(Path(scope_path).read_text())
 if scope.get('selection_manifest')!='manifests/native-archive/high-value-open-selection.json':raise ValueError('unapproved selection authority')
 selection_path=ROOT/scope['selection_manifest'];selection=json.loads(selection_path.read_text())
 if sha(selection_path)!=scope.get('selection_sha256') or scope['sources']!=selection['sources'] or scope['languages']!=selection['languages'] or scope['textmaps']!=selection['textmaps']:raise ValueError('frozen source selection mismatch')
 check_files(source_root,scope);impl,parser,scope_sha,run=identities(scope_path);seals={x['path']:x for x in scope['files']};records={};reuse_records=defaultdict(list);wanted=set();reuse_by_coord={(x['target_source_path'],x['target_source_pointer']):x for x in scope.get('reuse_targets',[])}
 for source in scope['sources']:
  rows=[]
  selected=None if 'selected_ordinals' not in source else set(source['selected_ordinals'])
  for ordinal,row,raw in array_records(Path(source_root)/source['path']):
   if selected is not None and ordinal not in selected:continue
   if not isinstance(row,dict):raise ValueError('native archive row object required')
   classified=set(source['field_classification']['readable_owner_fields'])|{x.split('/')[0] for x in source['field_classification']['relation_fields']}|set(source['field_classification']['raw_or_opaque_fields'])
   if set(row)-classified:raise ValueError('field classification mismatch '+source['path'])
   rows.append((ordinal,row,raw))
   for owner in source['text_owners']:
    for _,value in occurrences(row,owner['field'],owner.get('shape','scalar')):
     if type(value) is not int:raise ValueError('declared owner shape mismatch')
     wanted.add(str(value))
  records[source['path']]=rows
  if source.get('excluded_ordinals'):
   all_rows=list(array_records(Path(source_root)/source['path']))
   for ordinal in source['excluded_ordinals']:
    item=all_rows[ordinal];reuse_records[source['path']].append(item)
 maps=_maps(source_root,scope,wanted);expected={x:[] for x in TABLES};states=Counter();roles=Counter();relations=Counter();container_by_coord={};local=defaultdict(list)
 for source in scope['sources']:
  for ordinal,row,raw in records[source['path']]+reuse_records[source['path']]:
   pointer='/'+str(ordinal);cid=ident(run,'container',source['path'],pointer);container_by_coord[(source['path'],pointer)]=cid
 # Complete local indexes may target a different selected source.
 for source in scope['sources']:
  keys={r['target_key'] for spec in scope['sources'] for r in spec.get('relations',[]) if r['resolution']=='local' and source['path'] in r['target_sources']}
  for ordinal,row,raw in records[source['path']]:
   pointer='/'+str(ordinal);cid=container_by_coord[(source['path'],pointer)]
   for key in keys:
    if row.get(key) is not None:local[(source['path'],key,str(row[key]))].append({'target_source_path':source['path'],'target_source_pointer':pointer,'target_native_id':str(row[key]),'target_family':'native_archive','target_contract':scope['family_contract_version'],'target_parser':parser,'target_run':run,'target_occurrence_id':cid})
 for hit in scope.get('reuse_targets',[]):
  local[(hit['target_source_path'],'id',str(hit['target_native_id']))].append(hit)
 external=defaultdict(list)
 for x in scope['external_candidates']:external[(x['relation_kind'],x['raw_locator'])].append(x)
 for source in scope['sources']:
  path=source['path'];seal=seals[path]
  for ordinal,row,raw in records[path]+reuse_records[path]:
   pointer='/'+str(ordinal);cid=container_by_coord[(path,pointer)];nf=source.get('native_id_field');nv=None if nf is None else str(row.get(nf))
   reuse=reuse_by_coord.get((path,pointer))
   if reuse:expected['native_archive_container'].append((cid,run,source['boundary'],'projection_reuse',path,seal['sha256'],seal['blob'],ordinal,pointer,nf,nv,None,None,reuse['target_family'],reuse['target_contract'],reuse['target_parser'],reuse['target_run'],reuse['target_occurrence_id']))
   else:expected['native_archive_container'].append((cid,run,source['boundary'],'physical_row',path,seal['sha256'],seal['blob'],ordinal,pointer,nf,nv,raw,hashlib.sha256(raw.encode()).hexdigest(),None,None,None,None,None))
   for owner in source['text_owners']:
    for owner_pointer,value in occurrences(row,owner['field'],owner.get('shape','scalar')):
     locator=str(value);role=owner['owner_role_status'];roles[role]+=3;owner_field=owner_pointer.lstrip('/')
     for locale in scope['languages']:
      main=maps[locale][0].get(locator,[]);medium=maps[locale][1].get(locator,[]);hits=main or medium;status='missing' if not hits else 'resolved_main' if main and len(hits)==1 else 'resolved_medium' if medium and len(hits)==1 else 'ambiguous';states[status]+=1;sid=ident(run,'slot',path,pointer,owner_field,locale);expected['native_archive_slot'].append((sid,cid,owner_field,role,locale,locator,status,len(hits)))
      for tp,pair,pp,text,ts in hits:expected['native_archive_text'].append((ident(run,'text',sid,tp,pair),sid,tp,ts['sha256'],ts['blob'],pair,pp,text,hashlib.sha256(text.encode()).hexdigest()))
   for rel in source.get('relations',[]):
    for rel_pointer,value in occurrences(row,rel['field'],rel.get('shape','scalar')):
     locator=str(value);hits=[];complete=rel['resolution']!='unresolved'
     if rel['resolution']=='local':
       for target_path in rel['target_sources']:hits.extend(local[(target_path,rel['target_key'],locator)])
     elif rel['resolution'] in ('core','quest_talk_v7'):hits=external[(rel['relation_kind'],locator)]
     status='mapping_unresolved' if not complete else 'missing' if not hits else 'resolved_unique' if len(hits)==1 else 'ambiguous';relations[status]+=1;sp=pointer+rel_pointer;rid=ident(run,'relation',path,sp,rel['relation_kind']);expected['native_archive_relation'].append((rid,cid,rel['relation_kind'],rel_pointer.lstrip('/'),sp,locator,rel['candidate_namespace'],status,len(hits),1 if complete else 0))
     for hit in hits:
      candidate=(hit['target_source_path'],hit['target_source_pointer'],hit.get('target_native_id'),hit['target_family'],hit['target_contract'],hit['target_parser'],hit['target_run'],hit['target_occurrence_id'])
      expected['native_archive_candidate'].append((ident(run,'candidate',rid,candidate[-1]),rid,*candidate))
 for table in TABLES:expected[table].sort()
 carried_paths={x['path'] for x in scope['sources'] if x.get('carried_forward')};terminal={x['path']:x['terminal_unresolved_reason'] for x in scope['sources'] if x['completion_outcome']=='terminal_unresolved_after_complete_processing'};counts={k:len(v) for k,v in expected.items()};containers=expected['native_archive_container'];physical=[x for x in containers if x[3]=='physical_row'];reuse=[x for x in containers if x[3]=='projection_reuse']
 release={'contract':scope['family_contract_version'],'run_id':run,'parser_build_id':parser,'scope_sha256':scope_sha,'implementation':impl,'snapshot_id':scope['snapshot_id'],'counts':counts,'text_state_counts':dict(sorted(states.items())),'owner_role_counts':dict(sorted(roles.items())),'relation_state_counts':dict(sorted(relations.items())),'physical_rows':len(physical),'projection_reuse_rows':len(reuse),'carried_forward_physical_rows':sum(x[4] in carried_paths for x in physical),'new_wave_physical_rows':sum(x[4] not in carried_paths for x in physical),'searchable_text_candidates':len(expected['native_archive_text']),'corrected_historical_duplicate_rows':scope['corrected_historical_overlap']['duplicate_physical_rows'],'terminal_unresolved_sources':terminal,'field_classification_complete':True,'historical_body_copied':False,'corpus_complete':True,'full_7_0':False}
 return scope,expected,release

def _owned(path,owned):
 try:s=Path(path).stat();return owned==(s.st_dev,s.st_ino)
 except FileNotFoundError:return False
def build(source_root,scope_path,output):
 output=Path(output);side=[Path(str(output)+x) for x in ('-wal','-shm','-journal')]
 if output.exists() or any(x.exists() for x in side):raise FileExistsError('output or sidecar exists')
 scope,expected,release=reconstruct(source_root,scope_path);owned=None;owned_sidecars={};conn=None
 try:
  def capture():
   for item in side:
    if item.exists() and item not in owned_sidecars:
     stat=item.stat();owned_sidecars[item]=(stat.st_dev,stat.st_ino)
  fd=os.open(output,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd);s=output.stat();owned=(s.st_dev,s.st_ino);conn=sqlite3.connect(output);capture();conn.execute('PRAGMA foreign_keys=ON');conn.executescript((ROOT/'schemas/native-archive-v1.sql').read_text());capture()
  for table in TABLES:
   if expected[table]:conn.executemany('INSERT INTO '+table+' VALUES('+','.join('?'*len(expected[table][0]))+')',expected[table])
  capture();conn.execute('INSERT INTO native_archive_release VALUES(1,?)',(json.dumps(release,sort_keys=True,separators=(',',':')),));conn.commit();capture();conn.execute('VACUUM');capture();conn.close();conn=None
  release['artifact']={'size_bytes':output.stat().st_size,'sha256':sha(output)};release['logical_seals']={t:hashlib.sha256('\n'.join(json.dumps(list(x),ensure_ascii=False,separators=(',',':')) for x in expected[t]).encode()).hexdigest() for t in TABLES};return release
 except Exception:
  if conn is not None:capture();conn.close();capture()
  if _owned(output,owned):output.unlink()
  for item,identity in owned_sidecars.items():
   if _owned(item,identity):item.unlink()
  raise
