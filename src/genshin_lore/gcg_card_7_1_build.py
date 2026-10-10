"""GCG lore deck-card/card-face physical projection, no logical card identity."""
import hashlib,json,sqlite3
from collections import Counter,defaultdict
from pathlib import Path
from .gcg_card_7_1_source import PRIMARY,sha,array_records,pairs,check_files
ROOT=Path(__file__).resolve().parents[2]
FIELDS=(('storyTitleTextMapHash','storyDescTextMapHash','accessDescTextMapHash'),('nameTextMapHash','descTextMapHash','lockTipTextMapHash'))
IMPLEMENTATION=('src/genshin_lore/gcg_card_7_1_build.py','src/genshin_lore/gcg_card_7_1_source.py','src/genshin_lore/loading_tips_source.py','schemas/gcg-card-v1.sql')
def identity(*parts):return 'gcg:'+hashlib.sha256(json.dumps(parts,sort_keys=True,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()[:40]
def build(root,scope,output):
 root=Path(root);scope=Path(scope);output=Path(output);m=json.loads(scope.read_text());check_files(root,m);implementation={p:sha(ROOT/p) for p in IMPLEMENTATION};parser=identity('parser',implementation);run=identity('run',parser,sha(scope))
 sources=[list(array_records(root/p)) for p in PRIMARY];wanted=set();entries=[];indexes=[defaultdict(list),defaultdict(list)]
 for index,rows in enumerate(sources):
  if len(rows)!=m['primary_rows'][index]:raise ValueError('physical denominator')
  for ordinal,row,raw in rows:
   if type(row.get('id')) is not int or any(type(row.get(f)) is not int for f in FIELDS[index]):raise ValueError('GCG text shape')
   if index==0:
    if not isinstance(row.get('faceIdList'),list) or any(type(v) is not int for v in row['faceIdList']):raise ValueError('face list shape')
   elif type(row.get('cardId')) is not int:raise ValueError('card reference shape')
   rid=identity(run,'row',PRIMARY[index],ordinal);entries.append((index,ordinal,row,raw,rid));indexes[index][row['id']].append(rid);wanted.update(str(row[f]) for f in FIELDS[index])
 maps={}
 for locale in ('CHS','EN','JP'):
  for tier in ('','_Medium'):
   path=f'TextMap/TextMap{tier}{locale}.json';lookup=defaultdict(list)
   for i,(key,text) in enumerate(pairs(root/path)):
    if key in wanted:lookup[key].append((path,i,'/'+key,text))
   maps[locale,tier]=lookup
 with output.open('xb'):pass
 c=sqlite3.connect(output);c.execute('PRAGMA foreign_keys=ON');c.executescript((ROOT/'schemas/gcg-card-v1.sql').read_text());states=Counter();relations=Counter()
 for index,ordinal,row,raw,rid in entries:c.execute('INSERT INTO gcg_card_row VALUES(?,?,?,?,?,?,?,?)',(rid,run,('deck_card','card_face')[index],ordinal,str(row['id']),PRIMARY[index],'/'+str(ordinal),raw))
 for index,ordinal,row,raw,rid in entries:
  for field in FIELDS[index]:
   for locale in ('CHS','EN','JP'):
    locator=str(row[field]);hits=maps[locale,''].get(locator,[]) or maps[locale,'_Medium'].get(locator,[]);status='missing' if not hits else 'resolved' if len(hits)==1 else 'ambiguous';states[status]+=1;sid=identity(run,'slot',PRIMARY[index],ordinal,field,locale);c.execute('INSERT INTO gcg_card_slot VALUES(?,?,?,?,?,?)',(sid,rid,field,locale,locator,status))
    for path,pair,pointer,text in hits:c.execute('INSERT INTO gcg_card_text VALUES(?,?,?,?,?,?)',(identity(sid,path,pair),sid,path,pair,pointer,text))
  refs=[('/faceIdList/'+str(i),v) for i,v in enumerate(row['faceIdList'])] if index==0 else [('/cardId',row['cardId'])]
  for pointer,value in refs:
   targets=indexes[1-index].get(value,[]);status='missing' if not targets else 'resolved' if len(targets)==1 else 'ambiguous';relations[status]+=1;ref=identity(run,'relation',PRIMARY[index],ordinal,pointer)
   c.execute('INSERT INTO gcg_card_relation VALUES(?,?,?,?,?,?,?,?)',(ref,rid,'/'+str(ordinal)+pointer,json.dumps(value),PRIMARY[1-index],status,len(targets),1))
   for target in targets:c.execute('INSERT INTO gcg_card_candidate VALUES(?,?,?)',(identity(ref,target),ref,target))
 release={'contract':'gcg-card-evidence-v1','run_id':run,'parser_build_id':parser,'implementation':implementation,'scope_sha256':sha(scope),'snapshot_id':m['snapshot_id'],'rows':len(entries),'slots':len(entries)*9,'text_states':dict(states),'relationship_states':dict(relations)}
 c.execute('INSERT INTO gcg_card_release VALUES(?)',(json.dumps(release,sort_keys=True),));c.commit()
 if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('FK')
 c.execute('VACUUM');c.close();check_files(root,m);return {**release,'artifact':{'size_bytes':output.stat().st_size,'sha256':sha(output)}}
