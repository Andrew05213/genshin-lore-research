"""Complete LoadingTips physical row + title/description locale-slot projection."""
import hashlib,json,sqlite3
from collections import defaultdict,Counter
from pathlib import Path
from .loading_tips_source import sha,array_records,pairs,check_files
ROOT=Path(__file__).resolve().parents[2]
def ident(*parts):return 'lt:'+hashlib.sha256(json.dumps(parts,sort_keys=True,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()[:40]
def build(root,scope_path,output):
 root=Path(root);scope_path=Path(scope_path);output=Path(output);m=json.loads(scope_path.read_text());check_files(root,m)
 implementation={p:sha(ROOT/p) for p in ('src/genshin_lore/loading_tips_build.py','src/genshin_lore/loading_tips_source.py','schemas/loading-tips-v1.sql')}
 parser=ident('parser',implementation);run=ident('run',parser,sha(scope_path));rows=list(array_records(root/m['files'][0]['path']))
 if len(rows)!=m['primary_rows']:raise ValueError('row denominator')
 fields=('tipsTitleTextMapHash','tipsDescTextMapHash');languages=('CHS','EN','JP');wanted=set()
 for _,row,_ in rows:
  if type(row.get('id')) is not int or any(type(row.get(k)) is not int for k in fields):raise ValueError('unsupported LoadingTips shape')
  wanted.update(str(row[k]) for k in fields)
 maps={}
 for locale in languages:
  maps[locale]=[]
  for suffix in ('','_Medium'):
   path=f'TextMap/TextMap{suffix}{locale}.json';lookup=defaultdict(list)
   for ordinal,(key,text) in enumerate(pairs(root/path)):
    if key in wanted:lookup[key].append((path,ordinal,'/'+key,text))
   maps[locale].append(lookup)
 # Exclusive creation: never clobber an existing artifact.
 with output.open('xb'):pass
 c=sqlite3.connect(output);c.execute('PRAGMA foreign_keys=ON');c.executescript((ROOT/'schemas/loading-tips-v1.sql').read_text());counts=Counter()
 for ordinal,row,raw in rows:
  rid=ident(run,'row',ordinal);c.execute('INSERT INTO loading_tip_row VALUES(?,?,?,?,?,?,?)',(rid,run,ordinal,str(row['id']),m['files'][0]['path'],'/'+str(ordinal),raw))
  for field in fields:
   locator=str(row[field])
   for locale in languages:
    hits=maps[locale][0].get(locator,[]) or maps[locale][1].get(locator,[]);status='missing' if not hits else 'resolved' if len(hits)==1 else 'ambiguous';counts[status]+=1;sid=ident(run,'slot',ordinal,field,locale)
    c.execute('INSERT INTO loading_tip_slot VALUES(?,?,?,?,?,?)',(sid,rid,field,locale,locator,status))
    for path,pair,pointer,text in hits:c.execute('INSERT INTO loading_tip_text VALUES(?,?,?,?,?,?)',(ident(run,'text',ordinal,field,locale,path,pair),sid,path,pair,pointer,text))
 release={'contract':'loading-tips-evidence-v1','run_id':run,'parser_build_id':parser,'scope_sha256':sha(scope_path),'implementation':implementation,'snapshot_id':m['snapshot_id'],'rows':len(rows),'slots':len(rows)*6,'status_counts':dict(counts)}
 c.execute('INSERT INTO loading_tip_release VALUES(?)',(json.dumps(release,sort_keys=True),));c.commit()
 if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('FK')
 c.execute('VACUUM');c.close();check_files(root,m)
 return {**release,'artifact':{'size_bytes':output.stat().st_size,'sha256':sha(output)}}
