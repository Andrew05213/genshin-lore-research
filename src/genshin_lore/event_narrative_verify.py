"""Independent source reconstruction for the event-native narrative archive."""
import hashlib,json,sqlite3
from collections import Counter,defaultdict
from pathlib import Path
from .event_narrative_source import array_records,check_files,pairs,sha
ROOT=Path(__file__).resolve().parents[2]
def _id(*parts):return 'event-narrative:'+hashlib.sha256(json.dumps(parts,sort_keys=True,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()[:40]
def _table(conn,name):
 cols=[x[1] for x in conn.execute('PRAGMA table_info('+name+')')];return [tuple(x) for x in conn.execute('SELECT '+','.join(cols)+' FROM '+name+' ORDER BY '+','.join(cols))]
def verify(root,scope_path,artifact,build_report):
 root=Path(root);scope_path=Path(scope_path);artifact=Path(artifact);manifest=json.loads(scope_path.read_text());check_files(root,manifest)
 if artifact.stat().st_size!=build_report['artifact']['size_bytes'] or sha(artifact)!=build_report['artifact']['sha256']:raise ValueError('event artifact fingerprint')
 impl={p:sha(ROOT/p) for p in ('src/genshin_lore/event_narrative_build.py','src/genshin_lore/event_narrative_source.py','schemas/event-narrative-v1.sql')};parser=_id('parser',impl);scope_sha=sha(scope_path);run=_id('run',parser,scope_sha)
 for key,want in (('implementation',impl),('parser_build_id',parser),('scope_sha256',scope_sha),('run_id',run),('snapshot_id',manifest['snapshot_id'])):
  if build_report.get(key)!=want:raise ValueError('independent event implementation/scope/parser/run identity mismatch: '+key)
 file_seals={item['path']:item for item in manifest['files']};physical={item['kind']:list(array_records(root/item['path'])) for item in manifest['sources']}
 if {item['kind']:len(physical[item['kind']]) for item in manifest['sources']}!=manifest['row_denominator']:raise ValueError('event source denominator')
 wanted={str(row[field]) for item in manifest['sources'] for _,row,_ in physical[item['kind']] for field in item['text_fields']};maps={}
 for locale in manifest['languages']:
  tiers=[]
  for suffix in ('','_Medium'):
   path=f'TextMap/TextMap{suffix}{locale}.json';lookup=defaultdict(list)
   for ordinal,(key,text) in enumerate(pairs(root/path)):
    if key in wanted:lookup[key].append((path,ordinal,'/'+key,text))
   tiers.append(lookup)
  maps[locale]=tiers
 expected={x:[] for x in ('event_narrative_row','event_narrative_slot','event_narrative_text')};states=Counter();roles=Counter()
 for item in manifest['sources']:
  kind=item['kind'];path=item['path'];native_field=item.get('native_id_field')
  for ordinal,row,raw in physical[kind]:
   native=None if native_field is None or native_field not in row else str(row[native_field]);rid=_id(run,'row',path,ordinal);seal=file_seals[path];expected['event_narrative_row'].append((rid,run,kind,path,seal['sha256'],seal['blob'],ordinal,'/'+str(ordinal),native_field,native,raw,hashlib.sha256(raw.encode()).hexdigest()))
   for field in item['text_fields']:
    role=item.get('owner_roles',{}).get(field,'resolved');locator=str(row[field]);roles[role]+=len(manifest['languages'])
    for locale in manifest['languages']:
     main=maps[locale][0].get(locator,[]);medium=maps[locale][1].get(locator,[]);hits=main or medium;status='missing' if not hits else 'resolved_main' if main and len(hits)==1 else 'resolved_medium' if medium and len(hits)==1 else 'ambiguous';states[status]+=1;sid=_id(run,'slot',path,ordinal,field,locale);expected['event_narrative_slot'].append((sid,rid,field,role,locale,locator,status,len(hits)))
     for text_path,pair,pointer,text in hits:
      seal=file_seals[text_path];expected['event_narrative_text'].append((_id(run,'text',path,ordinal,field,locale,text_path,pair),sid,text_path,seal['sha256'],seal['blob'],pair,pointer,text,hashlib.sha256(text.encode()).hexdigest()))
 conn=sqlite3.connect('file:'+str(artifact.resolve())+'?mode=ro&immutable=1',uri=True)
 reference=sqlite3.connect(':memory:');reference.executescript((ROOT/'schemas/event-narrative-v1.sql').read_text());expected_structure=reference.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall();reference.close()
 if conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()!=expected_structure:raise ValueError('event schema structure mismatch')
 if conn.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise ValueError('event integrity check')
 for table,values in expected.items():
  if sorted(values)!=_table(conn,table):raise ValueError('event full business-column mismatch/no-extra-row: '+table)
 if conn.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('event FK')
 release=json.loads(conn.execute('SELECT manifest_json FROM event_narrative_release WHERE id=1').fetchone()[0]);conn.close();counts={k:len(v) for k,v in expected.items()};expected_release={'contract':manifest.get('family_contract_version','event-narrative-evidence-v1'),'run_id':run,'parser_build_id':parser,'scope_sha256':scope_sha,'implementation':impl,'snapshot_id':manifest['snapshot_id'],'counts':counts,'text_state_counts':dict(sorted(states.items())),'owner_role_counts':dict(sorted(roles.items())),'relation_occurrences':0}
 if release!=expected_release or {k:v for k,v in build_report.items() if k!='artifact'}!=expected_release:raise ValueError('independently reconstructed event release mismatch')
 return {'status':'pass','actual_bytes_git_blob_match':True,'identity_independently_reconstructed':True,'verified_artifact':{'size_bytes':artifact.stat().st_size,'sha256':sha(artifact)},'snapshot_id':manifest['snapshot_id'],'scope_sha256':scope_sha,'implementation':impl,'parser_build_id':parser,'run_id':run,'contract':expected_release['contract'],'source_files':len(manifest['files']),'counts':counts,'text_states':dict(sorted(states.items())),'owner_roles':dict(sorted(roles.items())),'relation_occurrences':0,'full_business_column_equality':True,'no_extra_rows':True,'logical_seals':{k:hashlib.sha256(json.dumps(sorted(v),ensure_ascii=False,separators=(',',':')).encode()).hexdigest() for k,v in expected.items()}}
