"""Independent reconstruction of GCG rows, locale slots and physical candidate sets."""
import hashlib,json,sqlite3
from collections import Counter
from pathlib import Path
from .gcg_card_source import sha,array_records,pairs,check_files
ROOT=Path(__file__).resolve().parents[2]
def require(value,label):
 if not value:raise ValueError(label)
def verify(root,scope,artifact,report):
 root=Path(root);scope=Path(scope);artifact=Path(artifact);m=json.loads(scope.read_text());check_files(root,m)
 def stable_identity():
  require(not any(Path(str(artifact)+suffix).exists() for suffix in ('-wal','-shm','-journal')),'artifact sidecar')
  st=artifact.stat();return (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)
 before_identity=stable_identity();require(before_identity[2]==report['artifact']['size_bytes'],'artifact size');before=sha(artifact);c=sqlite3.connect(Path(artifact).absolute().as_uri()+'?mode=ro&immutable=1',uri=True)
 require(c.execute('SELECT count(*) FROM gcg_card_release').fetchone()[0]==1,'release singleton');stored=json.loads(c.execute('SELECT manifest_json FROM gcg_card_release').fetchone()[0]);require(stored=={k:v for k,v in report.items() if k!='artifact'},'release binding')
 require(stored['snapshot_id']==m['snapshot_id'] and stored['scope_sha256']==sha(scope) and stored['contract']=='gcg-card-evidence-v1','release source provenance')
 def ident(*parts):return 'gcg:'+hashlib.sha256(json.dumps(parts,sort_keys=True,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()[:40]
 implementation={p:sha(ROOT/p) for p in ('src/genshin_lore/gcg_card_build.py','src/genshin_lore/gcg_card_source.py','src/genshin_lore/loading_tips_source.py','schemas/gcg-card-v1.sql')};parser=ident('parser',implementation);run=ident('run',parser,sha(scope));require(stored['implementation']==implementation and stored['run_id']==run and stored['parser_build_id']==parser,'parser/run')
 paths=['ExcelBinOutput/GCGDeckCardExcelConfigData.json','ExcelBinOutput/GCGCardFaceExcelConfigData.json'];fields=[['storyTitleTextMapHash','storyDescTextMapHash','accessDescTextMapHash'],['nameTextMapHash','descTextMapHash','lockTipTextMapHash']];source=[list(array_records(root/path)) for path in paths];require([len(x) for x in source]==m['primary_rows'],'source denominator')
 wanted={str(row[field]) for k,rows in enumerate(source) for _,row,_ in rows for field in fields[k]};maps={}
 for locale in ('CHS','EN','JP'):
  for tier in ('','_Medium'):
   path=f'TextMap/TextMap{tier}{locale}.json';index={}
   for ordinal,(key,value) in enumerate(pairs(root/path)):
    if key in wanted:index.setdefault(key,[]).append((path,ordinal,'/'+key,value))
   maps[locale,tier]=index
 rows=[];slots=[];texts=[];refs=[];candidates=[];states=Counter();relations=Counter()
 for k,records in enumerate(source):
  for ordinal,obj,raw in records:
   rid=ident(run,'row',paths[k],ordinal);rows.append((rid,run,['deck_card','card_face'][k],ordinal,str(obj['id']),paths[k],'/'+str(ordinal),raw))
   for field in fields[k]:
    for locale in ('CHS','EN','JP'):
     value=str(obj[field]);matches=maps[locale,''].get(value,[])
     if not matches:matches=maps[locale,'_Medium'].get(value,[])
     status={0:'missing',1:'resolved'}.get(len(matches),'ambiguous');states[status]+=1;sid=ident(run,'slot',paths[k],ordinal,field,locale);slots.append((sid,rid,field,locale,value,status))
     for path,i,pointer,text in matches:texts.append((ident(sid,path,i),sid,path,i,pointer,text))
   observed=[('/faceIdList/'+str(i),v) for i,v in enumerate(obj['faceIdList'])] if k==0 else [('/cardId',obj['cardId'])]
   for ptr,value in observed:
    # Independently scan the entire declared opposite source, no builder index
    # or reciprocal-value filter that could hide physical candidates.
    matches=[(i,target) for i,target,_ in source[1-k] if target['id']==value];status={0:'missing',1:'resolved'}.get(len(matches),'ambiguous');relations[status]+=1;ref=ident(run,'relation',paths[k],ordinal,ptr);refs.append((ref,rid,'/'+str(ordinal)+ptr,json.dumps(value),paths[1-k],status,len(matches),1))
    for i,target in matches:
     target_id=ident(run,'row',paths[1-k],i);candidates.append((ident(ref,target_id),ref,target_id))
 seals={}
 for table,expected in [('gcg_card_row',rows),('gcg_card_slot',slots),('gcg_card_text',texts),('gcg_card_relation',refs),('gcg_card_candidate',candidates)]:
  actual=c.execute('SELECT * FROM '+table+' ORDER BY id').fetchall();require(actual==sorted(expected),table+' source equality');seals[table]={'rows':len(actual),'sha256':hashlib.sha256(json.dumps(actual,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()}
 require(sum(states.values())==len(rows)*9==stored['slots'] and len(rows)==stored['rows'] and dict(states)==stored['text_states'] and dict(relations)==stored['relationship_states'],'slot/relation state partition');require(not c.execute('PRAGMA foreign_key_check').fetchall(),'FK');require(c.execute('PRAGMA integrity_check').fetchone()[0]=='ok','integrity')
 expected=sqlite3.connect(':memory:');expected.executescript((ROOT/'schemas/gcg-card-v1.sql').read_text());sql="SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name";require(c.execute(sql).fetchall()==expected.execute(sql).fetchall(),'schema/guards');expected.close();c.close();require(sha(artifact)==before==report['artifact']['sha256'],'artifact stable');check_files(root,m);require(stable_identity()==before_identity,'artifact identity stability')
 return {'status':'pass','run_id':run,'artifact_sha256':before,'logical_seals':seals,'text_states':dict(states),'relationship_states':dict(relations),'source_coordinates_exact':True}
