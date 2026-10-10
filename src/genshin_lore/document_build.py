"""Document physical rows and locator observations; external bodies never copied."""
import hashlib,json,sqlite3
from collections import Counter,defaultdict
from pathlib import Path
from .document_source import PRIMARY,LOCALIZATION,LANGUAGES,sha,array_records,pairs,check_files
ROOT=Path(__file__).resolve().parents[2]
IMPLEMENTATION=('src/genshin_lore/document_build.py','src/genshin_lore/document_source.py','src/genshin_lore/loading_tips_source.py','schemas/document-v1.sql')
def identity(*parts):return 'document:'+hashlib.sha256(json.dumps(parts,sort_keys=True,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()[:40]
def build(root,scope,output):
 root=Path(root);scope=Path(scope);output=Path(output);m=json.loads(scope.read_text());inv=check_files(root,m);readable={x['path']:x for x in inv};seals={x['path']:x for x in m['files']};implementation={p:sha(ROOT/p) for p in IMPLEMENTATION};parser=identity('parser',implementation);run=identity('run',parser,sha(scope));docs=list(array_records(root/PRIMARY));loc=list(array_records(root/LOCALIZATION));index=defaultdict(list)
 for i,obj,raw in loc:index[obj['id']].append((i,obj))
 if len(docs)!=m['primary_rows']:raise ValueError('primary denominator')
 wanted=set()
 for i,obj,raw in docs:
  if type(obj.get('id')) is not int or type(obj.get('titleTextMapHash')) is not int:raise ValueError('Document primary shape')
  for field in ('questIDList','contentLocalizedId','questContentLocalizedId'):
   if field in obj and (not isinstance(obj[field],list) or any(type(x) is not int for x in obj[field])):raise ValueError('Document locator shape')
  wanted.add(str(obj['titleTextMapHash']))
 maps={}
 for lang in LANGUAGES:
  for tier in ('','_Medium'):
   path=f'TextMap/TextMap{tier}{lang}.json';lookup=defaultdict(list)
   for i,(key,text) in enumerate(pairs(root/path)):
    if key in wanted:lookup[key].append((path,i,'/'+key,text))
   maps[lang,tier]=lookup
 with output.open('xb'):pass
 c=sqlite3.connect(output);c.execute('PRAGMA foreign_keys=ON');c.executescript((ROOT/'schemas/document-v1.sql').read_text());text_states=Counter();relation_states=defaultdict(Counter)
 def relation(rid,origin,kind,path,ptr,raw,boundary,complete,targets):
  status=('missing' if not targets else 'resolved' if len(targets)==1 else 'ambiguous') if complete else 'mapping_unresolved';ref=identity(run,'relation',rid,origin,kind,path,ptr)
  c.execute('INSERT INTO document_relation VALUES(?,?,?,?,?,?,?,?,?,?,?)',(ref,rid,origin,kind,path,ptr,json.dumps(raw,ensure_ascii=True),boundary,status,len(targets),int(complete)));relation_states[kind][status]+=1
  for target_path,target_ptr,seal in targets:c.execute('INSERT INTO document_candidate VALUES(?,?,?,?,?)',(identity(ref,target_path,target_ptr),ref,target_path,target_ptr,json.dumps(seal,sort_keys=True)))
 for i,obj,raw in docs:
  rid=identity(run,'row',PRIMARY,i);c.execute('INSERT INTO document_row VALUES(?,?,?,?,?,?,?,?)',(rid,run,'document',i,str(obj['id']),PRIMARY,f'/{i}',raw))
  for lang in LANGUAGES:
   value=str(obj['titleTextMapHash']);hits=maps[lang,''].get(value,[]) or maps[lang,'_Medium'].get(value,[]);status='missing' if not hits else 'resolved' if len(hits)==1 else 'ambiguous';text_states[status]+=1;sid=identity(run,'slot',PRIMARY,i,'titleTextMapHash',lang);c.execute('INSERT INTO document_slot VALUES(?,?,?,?,?,?)',(sid,rid,'titleTextMapHash',lang,value,status))
   for path,pair,ptr,text in hits:c.execute('INSERT INTO document_text VALUES(?,?,?,?,?,?)',(identity(sid,path,pair),sid,path,pair,ptr,text))
  for field in ('questIDList','questContentLocalizedId','contentLocalizedId'):
   for j,value in enumerate(obj.get(field,[])):
    origin=f'/{i}/{field}/{j}';matches=index.get(value,[]);complete=field!='contentLocalizedId';targets=[(LOCALIZATION,f'/{k}',{**seals[LOCALIZATION],'native_id':x['id']}) for k,x in matches]
    relation(rid,origin,'document_localization',PRIMARY,origin,value,'all physical Localization rows; '+('frozen explicit/project locator contract' if complete else 'equality observations only; namespace unresolved'),complete,targets)
    # Only mapped references permit second-hop path interpretation.
    if complete:
     for k,x in matches:
      for key,lang in [('DEFNEHAFMMA','CHS'),('JDNBKKPEFAI','CHS'),('enPath','EN'),('jpPath','JP')]:
       value=x.get(key);prefix=f'ART/UI/Readable/{lang}/';mapped=isinstance(value,str) and value.startswith(prefix) and len(value)>len(prefix) and all(p not in ('','.','..') for p in value[len(prefix):].split('/'));path='Readable/'+value[len('ART/UI/Readable/'):] + '.txt' if mapped else None
       found=[] if path not in readable else [(path,'',readable[path])]
       relation(rid,origin,'localization_readable',LOCALIZATION,f'/{k}/{key}',value,'sealed all Readable CHS/EN/JP physical paths; exact exported path contract' if mapped else 'non-Readable/unknown path namespace; no inferred file',mapped,found)
  if 'contentPath' in obj:relation(rid,f'/{i}/contentPath','document_content_path',PRIMARY,f'/{i}/contentPath',obj['contentPath'],'raw media locator; UI/DynamicBook is not a Readable namespace',False,[])
 release={'contract':'document-evidence-v1','run_id':run,'parser_build_id':parser,'implementation':implementation,'scope_sha256':sha(scope),'snapshot_id':m['snapshot_id'],'rows':len(docs),'slots':len(docs)*3,'text_states':dict(text_states),'relationship_states':{k:dict(v) for k,v in relation_states.items()}}
 c.execute('INSERT INTO document_release VALUES(1,?)',(json.dumps(release,sort_keys=True),));c.commit()
 if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('FK')
 c.execute('VACUUM');c.close();check_files(root,m);return {**release,'artifact':{'size_bytes':output.stat().st_size,'sha256':sha(output)}}
