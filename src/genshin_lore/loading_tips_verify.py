"""Independent source-to-database oracle; no builder/resolution imports."""
import hashlib,json,sqlite3
from pathlib import Path
from collections import Counter
from .loading_tips_source import array_records,pairs,sha,check_files
ROOT=Path(__file__).resolve().parents[2]
def verify(root,scope_path,artifact,report):
 root=Path(root);m=json.loads(Path(scope_path).read_text());check_files(root,m);before=sha(artifact)
 c=sqlite3.connect(Path(artifact).absolute().as_uri()+'?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
 assert c.execute('SELECT count(*) FROM loading_tip_release').fetchone()[0]==1
 stored=json.loads(c.execute('SELECT manifest_json FROM loading_tip_release').fetchone()[0]);assert stored=={k:v for k,v in report.items() if k!='artifact'}
 def identity(*parts):return 'lt:'+hashlib.sha256(json.dumps(parts,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()[:40]
 implementation={p:sha(ROOT/p) for p in stored['implementation']};assert implementation==stored['implementation'];parser=identity('parser',implementation);run=identity('run',parser,sha(scope_path));assert run==stored['run_id'] and parser==stored['parser_build_id']
 source=list(array_records(root/m['files'][0]['path']));assert len(source)==m['primary_rows']==stored['rows']
 wanted={str(row[field]) for _,row,_ in source for field in ('tipsTitleTextMapHash','tipsDescTextMapHash')};maps={}
 for locale in ('CHS','EN','JP'):
  for tier in ('','_Medium'):
   path=f'TextMap/TextMap{tier}{locale}.json';index={}
   for i,(key,value) in enumerate(pairs(root/path)):
    if key in wanted:index.setdefault(key,[]).append((path,i,'/'+key,value))
   maps[locale,tier]=index
 expected_rows=[];expected_slots=[];expected_texts=[];states=Counter()
 for ordinal,value,raw in source:
  rid=identity(run,'row',ordinal);expected_rows.append((rid,run,ordinal,str(value['id']),m['files'][0]['path'],'/'+str(ordinal),raw))
  for field in ('tipsTitleTextMapHash','tipsDescTextMapHash'):
   for locale in ('CHS','EN','JP'):
    key=str(value[field]);candidates=maps[locale,''].get(key,[])
    if len(candidates)==0:candidates=maps[locale,'_Medium'].get(key,[])
    state={0:'missing',1:'resolved'}.get(len(candidates),'ambiguous');states[state]+=1;sid=identity(run,'slot',ordinal,field,locale);expected_slots.append((sid,rid,field,locale,key,state))
    for path,i,pointer,text in candidates:expected_texts.append((identity(run,'text',ordinal,field,locale,path,i),sid,path,i,pointer,text))
 seals={}
 for table,expected in [('loading_tip_row',expected_rows),('loading_tip_slot',expected_slots),('loading_tip_text',expected_texts)]:
  actual=[tuple(r) for r in c.execute('SELECT * FROM '+table+' ORDER BY id')];assert actual==sorted(expected),table
  seals[table]={'rows':len(actual),'sha256':hashlib.sha256(json.dumps(actual,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()}
 assert sum(states.values())==len(source)*6==stored['slots'];assert dict(states)==stored['status_counts'];assert not c.execute('PRAGMA foreign_key_check').fetchall();assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
 schema=sqlite3.connect(':memory:');schema.executescript((ROOT/'schemas/loading-tips-v1.sql').read_text());query="SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name";assert [tuple(x) for x in c.execute(query)]==schema.execute(query).fetchall();schema.close();c.close();assert sha(artifact)==before==report['artifact']['sha256'];check_files(root,m)
 return {'status':'pass','artifact_sha256':before,'run_id':run,'logical_seals':seals,'slots':sum(states.values()),'states':dict(states),'source_rows':len(source),'source_coordinate_equality':True}
