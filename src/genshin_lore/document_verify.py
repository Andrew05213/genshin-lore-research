"""Independent source reconstruction, including complete candidate enumeration."""
import hashlib,json,sqlite3
from pathlib import Path
from collections import Counter,defaultdict
from .document_source import sha,array_records,pairs,check_files
ROOT=Path(__file__).resolve().parents[2]
def require(ok,label):
 if not ok:raise ValueError(label)
def verify(root,scope,artifact,report):
 root=Path(root);scope=Path(scope);artifact=Path(artifact);m=json.loads(scope.read_text());inv=check_files(root,m)
 def stable():
  require(not any(Path(str(artifact)+s).exists() for s in ('-wal','-shm','-journal')),'sidecar');s=artifact.stat();return(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)
 before=stable();require(before[2]==report['artifact']['size_bytes'],'size');digest=sha(artifact);c=sqlite3.connect(artifact.absolute().as_uri()+'?mode=ro&immutable=1',uri=True)
 stored=c.execute('SELECT id,manifest_json FROM document_release').fetchall();require(len(stored)==1 and stored[0][0]==1,'singleton');release=json.loads(stored[0][1]);require(release=={k:v for k,v in report.items() if k!='artifact'},'report binding');require(release['contract']=='document-evidence-v1' and release['snapshot_id']==m['snapshot_id'] and release['scope_sha256']==sha(scope),'source binding')
 def ident(*parts):return 'document:'+hashlib.sha256(json.dumps(parts,sort_keys=True,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()[:40]
 implementation={p:sha(ROOT/p) for p in ('src/genshin_lore/document_build.py','src/genshin_lore/document_source.py','src/genshin_lore/loading_tips_source.py','schemas/document-v1.sql')};parser=ident('parser',implementation);run=ident('run',parser,sha(scope));require(release['implementation']==implementation and release['run_id']==run and release['parser_build_id']==parser,'identity')
 primary='ExcelBinOutput/DocumentExcelConfigData.json';local='ExcelBinOutput/LocalizationExcelConfigData.json';docs=list(array_records(root/primary));lookup=list(array_records(root/local));require(len(docs)==m['primary_rows'],'denominator');source_seals={x['path']:x for x in m['files']};wanted={str(x['titleTextMapHash']) for _,x,_ in docs};maps={}
 for lang in ('CHS','EN','JP'):
  for tier in ('','_Medium'):
   path=f'TextMap/TextMap{tier}{lang}.json';values={}
   for i,(key,text) in enumerate(pairs(root/path)):
    if key in wanted:values.setdefault(key,[]).append((path,i,'/'+key,text))
   maps[lang,tier]=values
 rows=[];slots=[];texts=[];refs=[];candidates=[];ts=Counter();rs=defaultdict(Counter)
 def add(rid,origin,kind,path,pointer,raw,boundary,complete,targets):
  state={0:'missing',1:'resolved'}.get(len(targets),'ambiguous') if complete else 'mapping_unresolved';ref=ident(run,'relation',rid,origin,kind,path,pointer);refs.append((ref,rid,origin,kind,path,pointer,json.dumps(raw,ensure_ascii=True),boundary,state,len(targets),int(complete)));rs[kind][state]+=1
  for p,ptr,seal in targets:candidates.append((ident(ref,p,ptr),ref,p,ptr,json.dumps(seal,sort_keys=True)))
 for ordinal,obj,raw in docs:
  rid=ident(run,'row',primary,ordinal);rows.append((rid,run,'document',ordinal,str(obj['id']),primary,f'/{ordinal}',raw))
  for lang in ('CHS','EN','JP'):
   value=str(obj['titleTextMapHash']);hits=maps[lang,''].get(value,[])
   if not hits:hits=maps[lang,'_Medium'].get(value,[])
   state={0:'missing',1:'resolved'}.get(len(hits),'ambiguous');ts[state]+=1;sid=ident(run,'slot',primary,ordinal,'titleTextMapHash',lang);slots.append((sid,rid,'titleTextMapHash',lang,value,state))
   for p,i,ptr,text in hits:texts.append((ident(sid,p,i),sid,p,i,ptr,text))
  for field in ('questIDList','questContentLocalizedId','contentLocalizedId'):
   for j,value in enumerate(obj.get(field,[])):
    origin=f'/{ordinal}/{field}/{j}';matches=[(i,x) for i,x,_ in lookup if type(x['id']) is int and x['id']==value];complete=field in ('questIDList','questContentLocalizedId');targets=[(local,f'/{i}',{**source_seals[local],'native_id':x['id']}) for i,x in matches]
    add(rid,origin,'document_localization',primary,origin,value,'all physical Localization rows; '+('frozen explicit/project locator contract' if complete else 'equality observations only; namespace unresolved'),complete,targets)
    if not complete:continue
    for i,x in matches:
     for key,locale in [('DEFNEHAFMMA','CHS'),('JDNBKKPEFAI','CHS'),('enPath','EN'),('jpPath','JP')]:
      value=x.get(key);components=value.split('/') if isinstance(value,str) else [];valid=len(components)>4 and components[:4]==['ART','UI','Readable',locale] and not any(part in ('','.','..') for part in components[4:])
      # Independent full physical path enumeration. Never builder-selected targets.
      expected='/'.join(components[2:])+'.txt' if valid else None;matches2=[(s['path'],'',s) for s in inv if s['path']==expected]
      add(rid,origin,'localization_readable',local,f'/{i}/{key}',value,'sealed all Readable CHS/EN/JP physical paths; exact exported path contract' if valid else 'non-Readable/unknown path namespace; no inferred file',valid,matches2)
  if 'contentPath' in obj:add(rid,f'/{ordinal}/contentPath','document_content_path',primary,f'/{ordinal}/contentPath',obj['contentPath'],'raw media locator; UI/DynamicBook is not a Readable namespace',False,[])
 seals={}
 for table,expected in [('document_row',rows),('document_slot',slots),('document_text',texts),('document_relation',refs),('document_candidate',candidates)]:
  actual=c.execute('SELECT * FROM '+table+' ORDER BY id').fetchall();require(actual==sorted(expected),table+' source equivalence');seals[table]={'rows':len(actual),'sha256':hashlib.sha256(json.dumps(actual,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()}
 require(len(rows)==release['rows'] and sum(ts.values())==len(rows)*3==release['slots'] and dict(ts)==release['text_states'] and {k:dict(v) for k,v in rs.items()}==release['relationship_states'],'state partition')
 require(not c.execute('PRAGMA foreign_key_check').fetchall(),'FK');require(c.execute('PRAGMA integrity_check').fetchone()[0]=='ok','integrity');expected=sqlite3.connect(':memory:');expected.executescript((ROOT/'schemas/document-v1.sql').read_text());sql="SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name";require(c.execute(sql).fetchall()==expected.execute(sql).fetchall(),'structure');expected.close();c.close();require(sha(artifact)==digest==report['artifact']['sha256'] and stable()==before,'artifact stable');check_files(root,m)
 return {'status':'pass','run_id':run,'artifact_sha256':digest,'logical_seals':seals,'text_states':dict(ts),'relationship_states':{k:dict(v) for k,v in rs.items()},'all_candidate_sets_independently_reconstructed':True,'all_primary_coordinates_exact':True,'readable_bodies_stored':False}
