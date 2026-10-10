"""Versioned 7.1 builder for exact historical Auxiliary/Quest source scope."""
from __future__ import annotations
import hashlib,json,os,sqlite3
from collections import Counter,defaultdict
from pathlib import Path
from .event_7_1_source import array_records,check_files,pairs,sha
ROOT=Path(__file__).resolve().parents[2];SELECTION='manifests/native-archive-7.1/aux-quest-selection-v1.json'
TABLES=('native_archive_container','native_archive_slot','native_archive_text','native_archive_relation','native_archive_candidate')
def ident(*parts):return 'naq71:'+hashlib.sha256(json.dumps(parts,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:40]
def _maps(root,scope,wanted):
 out={};filemap={x['path']:x for x in scope['files']}
 for locale in scope['languages']:
  sets=[]
  for tier in ('main','medium'):
   path=scope['textmaps'][locale][tier];d=defaultdict(list)
   for ordinal,(key,value) in enumerate(pairs(Path(root)/path)):
    if key in wanted:d[key].append((path,ordinal,'/'+key,value,filemap[path]))
   sets.append(d)
  out[locale]=sets
 return out
def _owned(path,owned):
 try:s=Path(path).stat();return owned==(s.st_dev,s.st_ino)
 except FileNotFoundError:return False
def implementation():return {p:sha(ROOT/p) for p in ('src/genshin_lore/native_aux_quest_7_1_build.py','src/genshin_lore/event_7_1_source.py','schemas/native-aux-quest-7.1-v1.sql',SELECTION)}
def identities(scope_path):
 impl=implementation();parser='naq71-parser:'+hashlib.sha256(json.dumps(impl,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:40];scope_sha=sha(scope_path);run='naq71-run:'+hashlib.sha256((parser+':'+scope_sha).encode()).hexdigest()[:40];return impl,parser,scope_sha,run
def relation_values(row,spec):
 value=row.get(spec['field']);shape=spec.get('shape','scalar')
 if shape in ('scalar','scalar_optional'):return [] if value is None else [(spec['field'],value)]
 if shape=='array' and isinstance(value,list):return [(f"{spec['field']}/{i}",x) for i,x in enumerate(value) if x is not None]
 if value is not None:raise ValueError('declared relation shape mismatch '+spec['field'])
 return []
def reconstruct(source_root,scope_path):
 scope=json.loads(Path(scope_path).read_text());selection_path=ROOT/scope.get('selection_manifest','');selection=json.loads(selection_path.read_text())
 if scope.get('selection_manifest')!=SELECTION or sha(selection_path)!=scope.get('selection_sha256') or scope['sources']!=selection['sources'] or scope['languages']!=selection['languages'] or scope['textmaps']!=selection['textmaps']:raise ValueError('frozen source selection mismatch')
 check_files(source_root,scope);impl,parser,scope_sha,run=identities(scope_path);seals={x['path']:x for x in scope['files']};records={};wanted=set()
 for source in scope['sources']:
  rows=[]
  for ordinal,row,raw in array_records(Path(source_root)/source['path']):
   if not isinstance(row,dict):raise ValueError('native archive row object required')
   rows.append((ordinal,row,raw))
   for owner in source['text_owners']:
    value=row.get(owner['field'])
    if value is not None and type(value) is not int:raise ValueError('declared owner shape mismatch')
    if type(value) is int:wanted.add(str(value))
  records[source['path']]=rows
 maps=_maps(source_root,scope,wanted);expected={x:[] for x in TABLES};states=Counter();roles=Counter();relations=Counter();containers={};local=defaultdict(list)
 for source in scope['sources']:
  path=source['path'];seal=seals[path]
  for ordinal,row,raw in records[path]:
   pointer='/'+str(ordinal);cid=ident(run,'container',path,pointer);nf=source.get('native_id_field');nv=None if nf is None or row.get(nf) is None else str(row.get(nf));containers[(path,pointer)]=(cid,nv)
   expected['native_archive_container'].append((cid,run,source['boundary'],'physical_row',path,seal['sha256'],seal['blob'],ordinal,pointer,nf,nv,raw,hashlib.sha256(raw.encode()).hexdigest(),None,None,None,None,None))
   if nf and nv is not None:local[(path,nf,nv)].append({'target_source_path':path,'target_source_pointer':pointer,'target_native_id':nv,'target_family':scope['family'],'target_contract':scope['family_contract_version'],'target_parser':parser,'target_run':run,'target_occurrence_id':cid})
   for owner in source['text_owners']:
    field=owner['field'];value=row.get(field)
    if type(value) is not int:continue
    locator=str(value);roles[owner['owner_role_status']]+=3
    for locale in scope['languages']:
     main=maps[locale][0].get(locator,[]);medium=maps[locale][1].get(locator,[]);hits=main or medium;status='missing' if not hits else 'resolved_main' if main and len(hits)==1 else 'resolved_medium' if medium and len(hits)==1 else 'ambiguous';states[status]+=1;sid=ident(run,'slot',path,pointer,field,locale);expected['native_archive_slot'].append((sid,cid,field,owner['owner_role_status'],locale,locator,status,len(hits)))
     for tp,pair,pp,text,ts in hits:expected['native_archive_text'].append((ident(run,'text',sid,tp,pair),sid,tp,ts['sha256'],ts['blob'],pair,pp,text,hashlib.sha256(text.encode()).hexdigest()))
 external=defaultdict(list)
 for x in scope['external_candidates']:external[(x['namespace'],str(x['raw_locator']))].append(x)
 for source in scope['sources']:
  path=source['path']
  for ordinal,row,raw in records[path]:
   pointer='/'+str(ordinal);cid=containers[(path,pointer)][0]
   for spec in source.get('relations',[]):
    for relative,value in relation_values(row,spec):
     locator=str(value);target=spec['target'];hits=local[(target['path'],target['native_id_field'],locator)] if target['mode']=='local' else external[(target['namespace'],locator)];hits=sorted(hits,key=lambda x:(x['target_source_path'],x['target_source_pointer'],x['target_run'],x['target_occurrence_id']));status='missing' if not hits else 'resolved_unique' if len(hits)==1 else 'ambiguous';relations[status]+=1;sp=pointer+'/'+relative;rid=ident(run,'relation',path,sp,spec['relation_kind']);expected['native_archive_relation'].append((rid,cid,spec['relation_kind'],spec['field'],sp,locator,spec['candidate_namespace'],status,len(hits),1))
     for hit in hits:expected['native_archive_candidate'].append((ident(run,'candidate',rid,hit['target_occurrence_id']),rid,hit['target_source_path'],hit['target_source_pointer'],hit.get('target_native_id'),hit['target_family'],hit['target_contract'],hit['target_parser'],hit['target_run'],hit['target_occurrence_id']))
 for table in TABLES:expected[table].sort()
 counts={k:len(v) for k,v in expected.items()};release={'contract':scope['family_contract_version'],'run_id':run,'parser_build_id':parser,'scope_sha256':scope_sha,'implementation':impl,'snapshot_id':scope['snapshot_id'],'counts':counts,'text_state_counts':dict(sorted(states.items())),'owner_role_counts':dict(sorted(roles.items())),'relation_state_counts':dict(sorted(relations.items())),'physical_rows':counts['native_archive_container'],'projection_reuse_rows':0,'new_wave_physical_rows':counts['native_archive_container'],'new_projection_reuse_rows':0,'searchable_text_candidates':counts['native_archive_text'],'template_text_preserved_verbatim':True,'historical_body_copied':False,'corpus_complete':True,'full_7_1':False}
 return scope,expected,release
def build(source_root,scope_path,output):
 output=Path(output);side=[Path(str(output)+x) for x in ('-wal','-shm','-journal')]
 if output.exists() or any(x.exists() for x in side):raise FileExistsError('output or sidecar exists')
 scope,expected,release=reconstruct(source_root,scope_path);owned=None;owned_sidecars={};conn=None
 try:
  def capture():
   for item in side:
    if item.exists() and item not in owned_sidecars:
     stat=item.stat();owned_sidecars[item]=(stat.st_dev,stat.st_ino)
  fd=os.open(output,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd);s=output.stat();owned=(s.st_dev,s.st_ino);conn=sqlite3.connect(output);capture();conn.execute('PRAGMA foreign_keys=ON');conn.executescript((ROOT/'schemas/native-aux-quest-7.1-v1.sql').read_text());capture()
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
