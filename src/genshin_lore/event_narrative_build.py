"""Build three complete, physically distinct event-native narrative boundaries."""
import hashlib,json,sqlite3
from collections import Counter,defaultdict
from pathlib import Path
from .event_narrative_source import array_records,check_files,pairs,sha
ROOT=Path(__file__).resolve().parents[2]
def ident(*parts):return 'event-narrative:'+hashlib.sha256(json.dumps(parts,sort_keys=True,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()[:40]
def implementation():return {p:sha(ROOT/p) for p in ('src/genshin_lore/event_narrative_build.py','src/genshin_lore/event_narrative_source.py','schemas/event-narrative-v1.sql')}
def identities(scope_path):
 impl=implementation();parser=ident('parser',impl);scope_sha=sha(scope_path);return impl,parser,scope_sha,ident('run',parser,scope_sha)
def _build(root,scope_path,output,ownership):
 root=Path(root);scope_path=Path(scope_path);output=Path(output);manifest=json.loads(scope_path.read_text());check_files(root,manifest);impl,parser,scope_sha,run=identities(scope_path)
 file_seals={item['path']:item for item in manifest['files']};physical={item['kind']:list(array_records(root/item['path'])) for item in manifest['sources']}
 if {item['kind']:len(physical[item['kind']]) for item in manifest['sources']}!=manifest['row_denominator']:raise ValueError('event row denominator')
 wanted={str(row[field]) for item in manifest['sources'] for _,row,_ in physical[item['kind']] for field in item['text_fields']};maps={}
 for locale in manifest['languages']:
  tiers=[]
  for suffix in ('','_Medium'):
   path=f'TextMap/TextMap{suffix}{locale}.json';lookup=defaultdict(list)
   for ordinal,(key,text) in enumerate(pairs(root/path)):
    if key in wanted:lookup[key].append((path,ordinal,'/'+key,text))
   tiers.append(lookup)
  maps[locale]=tiers
 sidecars=[Path(str(output)+suffix) for suffix in ('-wal','-shm','-journal')]
 ownership['preexisting_sidecars']={path:path.stat().st_ino for path in sidecars if path.exists()}
 if ownership['preexisting_sidecars']:raise FileExistsError('pre-existing Event database sidecar')
 with output.open('xb'):pass
 stat=output.stat();ownership['output_identity']=(stat.st_dev,stat.st_ino);conn=None
 try:
  conn=sqlite3.connect(output);conn.execute('PRAGMA foreign_keys=ON');conn.executescript((ROOT/'schemas/event-narrative-v1.sql').read_text());states=Counter();roles=Counter()
  for item in manifest['sources']:
   kind=item['kind'];path=item['path'];native_field=item.get('native_id_field')
   for ordinal,row,raw in physical[kind]:
    if not isinstance(row,dict):raise ValueError('event physical row object required')
    native=None if native_field is None or native_field not in row else str(row[native_field]);rid=ident(run,'row',path,ordinal)
    seal=file_seals[path];conn.execute('INSERT INTO event_narrative_row VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(rid,run,kind,path,seal['sha256'],seal['blob'],ordinal,'/'+str(ordinal),native_field,native,raw,hashlib.sha256(raw.encode()).hexdigest()))
    for field in item['text_fields']:
     if type(row.get(field)) is not int:raise ValueError('event text locator shape: '+kind+'/'+field)
     role=item.get('owner_roles',{}).get(field,'resolved');roles[role]+=len(manifest['languages']);locator=str(row[field])
     for locale in manifest['languages']:
      main=maps[locale][0].get(locator,[]);medium=maps[locale][1].get(locator,[]);hits=main or medium
      status='missing' if not hits else 'resolved_main' if main and len(hits)==1 else 'resolved_medium' if medium and len(hits)==1 else 'ambiguous';states[status]+=1;sid=ident(run,'slot',path,ordinal,field,locale)
      conn.execute('INSERT INTO event_narrative_slot VALUES(?,?,?,?,?,?,?,?)',(sid,rid,field,role,locale,locator,status,len(hits)))
      for text_path,pair,pointer,text in hits:
       text_seal=file_seals[text_path];conn.execute('INSERT INTO event_narrative_text VALUES(?,?,?,?,?,?,?,?,?)',(ident(run,'text',path,ordinal,field,locale,text_path,pair),sid,text_path,text_seal['sha256'],text_seal['blob'],pair,pointer,text,hashlib.sha256(text.encode()).hexdigest()))
  counts={table:conn.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('event_narrative_row','event_narrative_slot','event_narrative_text')};release={'contract':manifest.get('family_contract_version','event-narrative-evidence-v1'),'run_id':run,'parser_build_id':parser,'scope_sha256':scope_sha,'implementation':impl,'snapshot_id':manifest['snapshot_id'],'counts':counts,'text_state_counts':dict(sorted(states.items())),'owner_role_counts':dict(sorted(roles.items())),'relation_occurrences':0}
  conn.execute('INSERT INTO event_narrative_release VALUES(1,?)',(json.dumps(release,sort_keys=True),));conn.commit()
  if conn.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('event FK')
  conn.execute('VACUUM');check_files(root,manifest);return {**release,'artifact':{'size_bytes':output.stat().st_size,'sha256':sha(output)}}
 except Exception:
  ownership['created_sidecars']={path:(path.stat().st_dev,path.stat().st_ino) for path in sidecars if path not in ownership['preexisting_sidecars'] and path.exists()}
  raise
 finally:
  if conn is not None:conn.close()
def build(root,scope_path,output):
 output=Path(output);ownership={}
 try:return _build(root,scope_path,output,ownership)
 except Exception:
  identity=ownership.get('output_identity')
  if identity:
   try:
    stat=output.stat()
    if (stat.st_dev,stat.st_ino)==identity:output.unlink()
   except FileNotFoundError:pass
   for path,owned_identity in ownership.get('created_sidecars',{}).items():
    try:
     stat=path.stat()
     if (stat.st_dev,stat.st_ino)==owned_identity:path.unlink()
    except FileNotFoundError:pass
  raise
