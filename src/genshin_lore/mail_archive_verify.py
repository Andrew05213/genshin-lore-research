"""Independent pinned-source verifier for Mail narrative evidence."""
import hashlib,json,sqlite3
from collections import Counter,defaultdict
from pathlib import Path
from .mail_archive_source import array_records,check_files,pairs,sha
ROOT=Path(__file__).resolve().parents[2]
def _id(*parts):return 'mail:'+hashlib.sha256(json.dumps(parts,sort_keys=True,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()[:40]
def _rows(conn,table):
 cols=[x[1] for x in conn.execute('PRAGMA table_info('+table+')')]
 return [tuple(r) for r in conn.execute('SELECT '+','.join(cols)+' FROM '+table+' ORDER BY '+','.join(cols))]
def verify(root,scope_path,artifact,build_report):
 root=Path(root);scope_path=Path(scope_path);artifact=Path(artifact);m=json.loads(scope_path.read_text());check_files(root,m)
 if artifact.stat().st_size!=build_report['artifact']['size_bytes'] or sha(artifact)!=build_report['artifact']['sha256']:raise ValueError('artifact fingerprint')
 impl={p:sha(ROOT/p) for p in ('src/genshin_lore/mail_archive_build.py','src/genshin_lore/mail_archive_source.py','schemas/mail-archive-v1.sql')};parser=_id('parser',impl);scope_sha=sha(scope_path);run=_id('run',parser,scope_sha)
 if build_report.get('implementation')!=impl or build_report.get('parser_build_id')!=parser or build_report.get('scope_sha256')!=scope_sha or build_report.get('run_id')!=run or build_report.get('snapshot_id')!=m['snapshot_id']:raise ValueError('independent Mail implementation/scope/parser/run identity mismatch')
 specs=(('mail_template',m['primary_sources'][0]),('birthday_schedule',m['primary_sources'][1]),('alchemy_sim_letter',m['primary_sources'][2]));physical={k:list(array_records(root/p)) for k,p in specs}
 expected={k:[] for k in ('mail_row','mail_slot','mail_text','mail_relation','mail_candidate')};rowids={};wanted=set()
 for kind,path in specs:
  for ordinal,row,raw in physical[kind]:
   rid=_id(run,'row',path,ordinal);rowids[(kind,ordinal)]=rid;expected['mail_row'].append((rid,run,kind,ordinal,str(row['id']),path,'/'+str(ordinal),raw,hashlib.sha256(raw.encode()).hexdigest()))
   if kind in m['text_fields_by_source']:wanted.update(str(row[f]) for f in m['text_fields_by_source'][kind])
 maps={}
 for locale in m['languages']:
  tiers=[]
  for suffix in ('','_Medium'):
   path=f'TextMap/TextMap{suffix}{locale}.json';look=defaultdict(list)
   for ordinal,(key,text) in enumerate(pairs(root/path)):
    if key in wanted:look[key].append((path,ordinal,'/'+key,text))
   tiers.append(look)
  maps[locale]=tiers
 states=Counter()
 for kind in m['text_fields_by_source']:
  for ordinal,row,_ in physical[kind]:
   rid=rowids[(kind,ordinal)];path=dict(specs)[kind]
   for field in m['text_fields_by_source'][kind]:
    loc=str(row[field])
    for locale in m['languages']:
     main=maps[locale][0].get(loc,[]);medium=maps[locale][1].get(loc,[]);hits=main or medium;status='missing' if not hits else 'resolved_main' if main and len(hits)==1 else 'resolved_medium' if medium and len(hits)==1 else 'ambiguous';states[status]+=1;sid=_id(run,'slot',path,ordinal,field,locale)
     expected['mail_slot'].append((sid,rid,field,locale,loc,status,len(hits)))
     for tp,pair,pointer,text in hits:expected['mail_text'].append((_id(run,'text',path,ordinal,field,locale,tp,pair),sid,tp,pair,pointer,text,hashlib.sha256(text.encode()).hexdigest()))
 byid=defaultdict(list)
 for ordinal,row,raw in physical['mail_template']:byid[str(row['id'])].append((ordinal,raw))
 bp=m['primary_sources'][1];tp=m['primary_sources'][0];relstates=Counter()
 for ordinal,row,_ in physical['birthday_schedule']:
  hits=byid.get(str(row['mailId']),[]);status='missing' if not hits else 'resolved_unique' if len(hits)==1 else 'ambiguous';relstates[status]+=1;rel=_id(run,'relation',bp,ordinal,'mailId')
  expected['mail_relation'].append((rel,rowids[('birthday_schedule',ordinal)],'birthday_schedule_to_mail_template','/'+str(ordinal)+'/mailId',str(row['mailId']),'MailExcelConfigData.id physical rows',status,len(hits),1))
  for to,raw in hits:expected['mail_candidate'].append((_id(run,'candidate',bp,ordinal,tp,to),rel,rowids[('mail_template',to)],tp,to,'/'+str(to),hashlib.sha256(raw.encode()).hexdigest()))
 c=sqlite3.connect('file:'+str(artifact.resolve())+'?mode=ro&immutable=1',uri=True)
 for table,values in expected.items():
  if sorted(values)!=_rows(c,table):raise ValueError('full business-column mismatch/no-extra-row: '+table)
 if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('FK')
 release=json.loads(c.execute('SELECT manifest_json FROM mail_release WHERE id=1').fetchone()[0]);c.close()
 counts={k:len(v) for k,v in expected.items()};expected_release={'contract':'mail-narrative-evidence-v1','run_id':run,'parser_build_id':parser,'scope_sha256':scope_sha,'implementation':impl,'snapshot_id':m['snapshot_id'],'counts':counts,'text_state_counts':dict(sorted(states.items())),'relation_state_counts':dict(sorted(relstates.items()))}
 if release!=expected_release or {k:v for k,v in build_report.items() if k!='artifact'}!=expected_release:raise ValueError('independently reconstructed release seal mismatch')
 return {'status':'pass','source_files':len(m['files']),'actual_bytes_git_blob_match':True,'identity_independently_reconstructed':True,'counts':counts,'text_states':dict(sorted(states.items())),'relation_states':dict(sorted(relstates.items())),'full_business_column_equality':True,'no_extra_rows':True,'logical_seals':{k:hashlib.sha256(json.dumps(sorted(v),ensure_ascii=False,separators=(',',':')).encode()).hexdigest() for k,v in expected.items()}}
