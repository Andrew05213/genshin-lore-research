"""Builder for the Native Book / MusicGame archive wave."""
from __future__ import annotations
import hashlib,json,os,sqlite3
from collections import Counter,defaultdict
from pathlib import Path
from .event_narrative_source import array_records,check_files,pairs,sha
ROOT=Path(__file__).resolve().parents[2]
TABLES=('native_archive_container','native_archive_slot','native_archive_text','native_archive_relation','native_archive_candidate')
def ident(*parts):return 'native-archive:'+hashlib.sha256(json.dumps(parts,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:40]
def implementation():return {p:sha(ROOT/p) for p in ('src/genshin_lore/native_book_music_archive_build.py','src/genshin_lore/event_narrative_source.py','schemas/native-archive-v1.sql','manifests/native-archive/book-music-selection.json')}
def identities(scope_path):
 impl=implementation();parser='native-archive-parser:'+hashlib.sha256(json.dumps(impl,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:40];scope_sha=sha(scope_path);run='native-archive-run:'+hashlib.sha256((parser+':'+scope_sha).encode()).hexdigest()[:40];return impl,parser,scope_sha,run
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
 if scope.get('selection_manifest')!='manifests/native-archive/book-music-selection.json':raise ValueError('unapproved selection authority')
 selection_path=ROOT/scope['selection_manifest'];selection=json.loads(selection_path.read_text())
 if sha(selection_path)!=scope.get('selection_sha256') or scope['sources']!=selection['sources'] or scope['languages']!=selection['languages'] or scope['textmaps']!=selection['textmaps']:raise ValueError('frozen source selection mismatch')
 check_files(source_root,scope);impl,parser,scope_sha,run=identities(scope_path);seals={x['path']:x for x in scope['files']};records={};wanted=set()
 for source in scope['sources']:
  rows=[]
  for ordinal,row,raw in array_records(Path(source_root)/source['path']):
   if not isinstance(row,dict):raise ValueError('native archive row object required')
   rows.append((ordinal,row,raw))
   for owner in source['text_owners']:
    value=row.get(owner['field']);
    if type(value) is int:wanted.add(str(value))
  records[source['path']]=rows
 maps=_maps(source_root,scope,wanted);expected={x:[] for x in TABLES};states=Counter();roles=Counter();relations=Counter();container_by_coord={}
 for source in scope['sources']:
  path=source['path'];seal=seals[path]
  for ordinal,row,raw in records[path]:
   pointer='/'+str(ordinal);cid=ident(run,'container',path,pointer);container_by_coord[(path,pointer)]=cid;nf=source.get('native_id_field');nv=None if nf is None else str(row.get(nf));reuse=source.get('reuse')
   if reuse:
    target=next((x for x in scope['reuse_targets'] if x['source_path']==path and x['source_pointer']==pointer),None)
    if target is None:raise ValueError('missing exact reuse target')
    expected['native_archive_container'].append((cid,run,source['boundary'],'projection_reuse',path,seal['sha256'],seal['blob'],ordinal,pointer,nf,nv,None,None,target['target_family'],target['target_contract'],target['target_parser'],target['target_run'],target['target_occurrence_id']))
   else:expected['native_archive_container'].append((cid,run,source['boundary'],'physical_row',path,seal['sha256'],seal['blob'],ordinal,pointer,nf,nv,raw,hashlib.sha256(raw.encode()).hexdigest(),None,None,None,None,None))
   for owner in source['text_owners']:
    field=owner['field'];value=row.get(field)
    if type(value) is not int:continue
    locator=str(value);role=owner['owner_role_status'];roles[role]+=3
    for locale in scope['languages']:
     main=maps[locale][0].get(locator,[]);medium=maps[locale][1].get(locator,[]);hits=main or medium;status='missing' if not hits else 'resolved_main' if main and len(hits)==1 else 'resolved_medium' if medium and len(hits)==1 else 'ambiguous';states[status]+=1;sid=ident(run,'slot',path,pointer,field,locale);expected['native_archive_slot'].append((sid,cid,field,role,locale,locator,status,len(hits)))
     for tp,pair,pp,text,ts in hits:expected['native_archive_text'].append((ident(run,'text',sid,tp,pair),sid,tp,ts['sha256'],ts['blob'],pair,pp,text,hashlib.sha256(text.encode()).hexdigest()))
   for relspec in source.get('relations',[]):
    value=row.get(relspec['field'])
    if value is None:continue
    locator=str(value);hits=[x for x in scope['external_candidates'] if x['relation_kind']==relspec['relation_kind'] and x['raw_locator']==locator];status='missing' if not hits else 'resolved_unique' if len(hits)==1 else 'ambiguous';relations[status]+=1;sp=pointer+'/'+relspec['field'];rid=ident(run,'relation',path,sp,relspec['relation_kind']);expected['native_archive_relation'].append((rid,cid,relspec['relation_kind'],relspec['field'],sp,locator,relspec['candidate_namespace'],status,len(hits),1))
    for hit in hits:expected['native_archive_candidate'].append((ident(run,'candidate',rid,hit['target_occurrence_id']),rid,hit['target_source_path'],hit['target_source_pointer'],hit.get('target_native_id'),hit['target_family'],hit['target_contract'],hit['target_parser'],hit['target_run'],hit['target_occurrence_id']))
 for table in TABLES:expected[table].sort()
 carried_paths={x['path'] for x in scope['sources'] if x.get('carried_forward')};counts={k:len(v) for k,v in expected.items()};release={'contract':scope['family_contract_version'],'run_id':run,'parser_build_id':parser,'scope_sha256':scope_sha,'implementation':impl,'snapshot_id':scope['snapshot_id'],'counts':counts,'text_state_counts':dict(sorted(states.items())),'owner_role_counts':dict(sorted(roles.items())),'relation_state_counts':dict(sorted(relations.items())),'physical_rows':sum(1 for x in expected['native_archive_container'] if x[3]=='physical_row'),'projection_reuse_rows':sum(1 for x in expected['native_archive_container'] if x[3]=='projection_reuse'),'carried_forward_physical_rows':sum(1 for x in expected['native_archive_container'] if x[3]=='physical_row' and x[4] in carried_paths),'carried_forward_projection_reuse_rows':sum(1 for x in expected['native_archive_container'] if x[3]=='projection_reuse' and x[4] in carried_paths),'new_wave_physical_rows':sum(1 for x in expected['native_archive_container'] if x[3]=='physical_row' and x[4] not in carried_paths),'new_projection_reuse_rows':sum(1 for x in expected['native_archive_container'] if x[3]=='projection_reuse' and x[4] not in carried_paths),'searchable_text_candidates':len(expected['native_archive_text']),'template_text_preserved_verbatim':True,'historical_body_copied':False,'corpus_complete':True,'full_7_0':False}
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
  for table in TABLES:conn.executemany('INSERT INTO '+table+' VALUES('+','.join('?'*len(expected[table][0]))+')',expected[table]) if expected[table] else None
  capture();conn.execute('INSERT INTO native_archive_release VALUES(1,?)',(json.dumps(release,sort_keys=True,separators=(',',':')),));conn.commit();capture();conn.execute('VACUUM');capture();conn.close();conn=None
  release['artifact']={'size_bytes':output.stat().st_size,'sha256':sha(output)};release['logical_seals']={t:hashlib.sha256('\n'.join(json.dumps(list(x),ensure_ascii=False,separators=(',',':')) for x in expected[t]).encode()).hexdigest() for t in TABLES};return release
 except Exception:
  if conn is not None:
   capture();conn.close();capture()
  if _owned(output,owned):output.unlink()
  for item,identity in owned_sidecars.items():
   if _owned(item,identity):item.unlink()
  raise
