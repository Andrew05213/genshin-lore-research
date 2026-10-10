"""Build the frozen Mail template + birthday schedule physical archive."""
import hashlib,json,sqlite3
from collections import Counter,defaultdict
from pathlib import Path
from .mail_archive_source import array_records,check_files,pairs,sha
ROOT=Path(__file__).resolve().parents[2]
def ident(*parts):return 'mail:'+hashlib.sha256(json.dumps(parts,sort_keys=True,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()[:40]
def build(root,scope_path,output):
 root=Path(root);scope_path=Path(scope_path);output=Path(output);m=json.loads(scope_path.read_text());check_files(root,m)
 impl={p:sha(ROOT/p) for p in ('src/genshin_lore/mail_archive_build.py','src/genshin_lore/mail_archive_source.py','schemas/mail-archive-v1.sql')};parser=ident('parser',impl);run=ident('run',parser,sha(scope_path))
 specs=(('mail_template',m['primary_sources'][0]),('birthday_schedule',m['primary_sources'][1]),('alchemy_sim_letter',m['primary_sources'][2]));source_rows={kind:list(array_records(root/path)) for kind,path in specs}
 if {k:len(v) for k,v in source_rows.items()}!=m['row_denominator']:raise ValueError('Mail row denominator')
 wanted={str(row[f]) for kind in m['text_fields_by_source'] for _,row,_ in source_rows[kind] for f in m['text_fields_by_source'][kind]};maps={}
 for locale in m['languages']:
  tiers=[]
  for suffix in ('','_Medium'):
   path=f'TextMap/TextMap{suffix}{locale}.json';lookup=defaultdict(list)
   for ordinal,(key,text) in enumerate(pairs(root/path)):
    if key in wanted:lookup[key].append((path,ordinal,'/'+key,text))
   tiers.append(lookup)
  maps[locale]=tiers
 with output.open('xb'):pass
 c=sqlite3.connect(output);c.execute('PRAGMA foreign_keys=ON');c.executescript((ROOT/'schemas/mail-archive-v1.sql').read_text());states=Counter();row_ids={}
 for kind,path in specs:
  for ordinal,row,raw in source_rows[kind]:
   if type(row.get('id')) is not int:raise ValueError('Mail native id shape')
   rid=ident(run,'row',path,ordinal);row_ids[(kind,ordinal)]=rid;c.execute('INSERT INTO mail_row VALUES(?,?,?,?,?,?,?,?,?)',(rid,run,kind,ordinal,str(row['id']),path,'/'+str(ordinal),raw,hashlib.sha256(raw.encode()).hexdigest()))
   if kind in m['text_fields_by_source']:
    if any(type(row.get(f)) is not int for f in m['text_fields_by_source'][kind]):raise ValueError('Mail text locator shape')
    for field in m['text_fields_by_source'][kind]:
     locator=str(row[field])
     for locale in m['languages']:
      main=maps[locale][0].get(locator,[]);medium=maps[locale][1].get(locator,[]);hits=main or medium
      status=('missing' if not hits else ('resolved_main' if main and len(hits)==1 else 'resolved_medium' if medium and len(hits)==1 else 'ambiguous'));states[status]+=1;sid=ident(run,'slot',path,ordinal,field,locale)
      c.execute('INSERT INTO mail_slot VALUES(?,?,?,?,?,?,?)',(sid,rid,field,locale,locator,status,len(hits)))
      for tp,pair,pointer,text in hits:c.execute('INSERT INTO mail_text VALUES(?,?,?,?,?,?,?)',(ident(run,'text',path,ordinal,field,locale,tp,pair),sid,tp,pair,pointer,text,hashlib.sha256(text.encode()).hexdigest()))
 template_by_id=defaultdict(list)
 for ordinal,row,raw in source_rows['mail_template']:template_by_id[str(row['id'])].append((ordinal,row,raw))
 birthday_path=m['primary_sources'][1];target_path=m['primary_sources'][0]
 for ordinal,row,_ in source_rows['birthday_schedule']:
  if type(row.get('mailId')) is not int:raise ValueError('Birthday mailId shape')
  source=row_ids[('birthday_schedule',ordinal)];hits=template_by_id.get(str(row['mailId']),[]);status='missing' if not hits else 'resolved_unique' if len(hits)==1 else 'ambiguous';rel=ident(run,'relation',birthday_path,ordinal,'mailId')
  c.execute('INSERT INTO mail_relation VALUES(?,?,?,?,?,?,?,?,?)',(rel,source,'birthday_schedule_to_mail_template','/'+str(ordinal)+'/mailId',str(row['mailId']),'MailExcelConfigData.id physical rows',status,len(hits),1))
  for target_ordinal,_,raw in hits:c.execute('INSERT INTO mail_candidate VALUES(?,?,?,?,?,?,?)',(ident(run,'candidate',birthday_path,ordinal,target_path,target_ordinal),rel,row_ids[('mail_template',target_ordinal)],target_path,target_ordinal,'/'+str(target_ordinal),hashlib.sha256(raw.encode()).hexdigest()))
 counts={t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('mail_row','mail_slot','mail_text','mail_relation','mail_candidate')};release={'contract':'mail-narrative-evidence-v1','run_id':run,'parser_build_id':parser,'scope_sha256':sha(scope_path),'implementation':impl,'snapshot_id':m['snapshot_id'],'counts':counts,'text_state_counts':dict(sorted(states.items())),'relation_state_counts':dict(c.execute('SELECT status,count(*) FROM mail_relation GROUP BY status').fetchall())}
 c.execute('INSERT INTO mail_release VALUES(1,?)',(json.dumps(release,sort_keys=True),));c.commit()
 if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('FK')
 c.execute('VACUUM');c.close();check_files(root,m)
 return {**release,'artifact':{'size_bytes':output.stat().st_size,'sha256':sha(output)}}
