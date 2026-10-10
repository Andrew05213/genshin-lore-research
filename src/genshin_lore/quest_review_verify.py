"""Independent pinned-source verifier for Quest review evidence."""
from __future__ import annotations
import hashlib,json,sqlite3,subprocess,time
from collections import Counter,defaultdict
from pathlib import Path
from .loading_tips_source import array_records,pairs,sha
from .quest_review_source import COMMIT,TREE,SNAPSHOT,FAMILY,CONTRACT,SUMMARY,REVIEW,TAB,STEP,LANGS,TEXTMAPS,canonical,file_seal,ident,load,sha_bytes
from .quest_review_build import IMPLEMENTATION,TABLES
from .quest_narrative_source import ident as narrative_ident
from .source_integrity import pinned_file_seal

ROOT=Path(__file__).resolve().parents[2]
def check(value,message):
 if not value:raise ValueError(message)
def verify(source,scope_path,artifact,manifest_path,narrative_artifact,narrative_manifest):
 started=time.perf_counter();source=Path(source);scope=load(scope_path);manifest=load(manifest_path);nm=load(narrative_manifest)
 check(subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()==[COMMIT,TREE],'source pin')
 check([pinned_file_seal(source,COMMIT,x['path']) for x in scope['files']]==scope['files'],'actual bytes Git blob/source seals')
 impl={p:sha(ROOT/p) for p in IMPLEMENTATION};parser=ident('parser',impl);run=ident('run',SNAPSHOT,parser,sha(scope_path),CONTRACT,sha(narrative_manifest));check(manifest['release']['implementation']==impl and manifest['release']['parser_build_id']==parser and manifest['release']['run_id']==run,'identity')
 check(Path(artifact).stat().st_size==manifest['artifact']['size_bytes'] and sha(artifact)==manifest['artifact']['sha256'],'artifact seal');check(Path(narrative_artifact).stat().st_size==nm['artifact']['size_bytes'] and sha(narrative_artifact)==nm['artifact']['sha256'],'narrative seal')
 db=sqlite3.connect(Path(artifact).absolute().as_uri()+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row;ndb=sqlite3.connect(Path(narrative_artifact).absolute().as_uri()+'?mode=ro&immutable=1',uri=True);ndb.row_factory=sqlite3.Row
 step_rows=load(source/STEP);step_by=defaultdict(list)
 for ordinal,value in enumerate(step_rows):step_by[value['subId']].append((STEP,f'/{ordinal}',str(value['subId']),'quest_narrative_archive','quest-narrative-evidence-v1',nm['release']['run_id'],narrative_ident('container',STEP,f'/{ordinal}'),'explicit_unresolved_equality_navigation_candidate'))
 specs=((SUMMARY,'summarization','Id'),(REVIEW,'review_group','id'),(TAB,'review_tab','id'));rows=[];parsed={};row_ids={}
 for path,kind,key in specs:
  seal=file_seal(source,path);items=[]
  for ordinal,value,lexical in array_records(source/path):
   check(isinstance(value,dict) and key in value,'row shape');pointer=f'/{ordinal}';rowid=ident('row',run,path,pointer);row_ids[(path,pointer)]=rowid;items.append((ordinal,value));rows.append((rowid,run,kind,path,ordinal,pointer,str(value[key]),seal['blob'],seal['sha256'],sha_bytes(lexical.encode()),lexical,'physical_row_processed'))
  parsed[path]=items
 actual=[tuple(x) for x in db.execute('select * from quest_review_row order by id')];check(actual==sorted(rows),'rows')
 review_by=defaultdict(list)
 for path in (REVIEW,TAB):
  for ordinal,value in parsed[path]:review_by[int(value['id'])].append((path,f'/{ordinal}',str(value['id']),FAMILY,CONTRACT,run,row_ids[(path,f'/{ordinal}')],'explicit_unresolved_same_value_navigation_candidate'))
 relations=[];candidates=[]
 def add_relation(path,ordinal,row_native,kind,pointer,value,namespace,hits,status,semantics):
  rid=ident('relation',run,path,pointer,kind);relations.append((rid,row_ids[(path,f'/{ordinal}')],kind,pointer,canonical(value),namespace,status,len(hits),0,semantics))
  for hit in hits:
   cid=ident('candidate',rid,hit[0],hit[1],hit[5],hit[6]);candidates.append((cid,rid,*hit))
 for ordinal,value in parsed[SUMMARY]:
  native=int(value['Id']);hits=step_by[native]
  add_relation(SUMMARY,ordinal,native,'summarization_id_observation',f'/{ordinal}/Id',native,'observed equal raw values in complete QuestExcel subId physical namespace; source does not prove identity or close semantic candidate search',hits,'mapping_unresolved','cross-source equality observation; candidates are navigation aids, not source-authored relations')
 for ordinal,value in parsed[REVIEW]:
  native=int(value['id'])
  for j,target_id in enumerate(value.get('KHAPIAPJAHD',[])):
   hits=review_by[target_id]
   add_relation(REVIEW,ordinal,native,'review_member_locator_observation',f'/{ordinal}/KHAPIAPJAHD/{j}',target_id,'observed equal raw values across complete QuestReview and QuestReviewTabGroup physical rows; obfuscated field semantics remain unresolved',hits,'mapping_unresolved','raw review-member observation with 0/1/N navigation candidates; not asserted as source-authored child relation')
 actual_relations=[tuple(x) for x in db.execute('select * from quest_review_relation order by id')];expected_relations=sorted(relations);check(actual_relations==expected_relations,'relations')
 actual_candidates=[tuple(x) for x in db.execute('select * from quest_review_candidate order by id')];expected_candidates=sorted(candidates)
 if actual_candidates!=expected_candidates:
  mismatch=next(((a,b) for a,b in zip(actual_candidates,expected_candidates) if a!=b),(len(actual_candidates),len(expected_candidates)))
  raise ValueError('candidates '+repr(mismatch))
 for candidate in candidates:
  target_path,target_pointer,target_native,target_family,target_contract,target_run,target_occurrence,_=candidate[2:]
  if target_family==FAMILY:target=db.execute('select source_path,source_pointer,native_id from quest_review_row where id=?',(target_occurrence,)).fetchone()
  else:target=ndb.execute('select source_path,source_pointer,native_id from quest_narrative_container where id=? and run_id=?',(target_occurrence,target_run)).fetchone()
  check(target and tuple(target)==(target_path,target_pointer,target_native),'exact target locator')
 locators=[]
 for ordinal,value in parsed[SUMMARY]:locators.append((SUMMARY,ordinal,int(value['Id']),'DescTextMapHash',value['DescTextMapHash']))
 for ordinal,value in parsed[TAB]:
  locators.append((TAB,ordinal,int(value['id']),'titleTextMapHash',value['titleTextMapHash']))
  locators.extend((TAB,ordinal,int(value['id']),f'GIAIIFFDFNE/{i}',v) for i,v in enumerate(value['GIAIIFFDFNE']))
 maps={};seals={p:file_seal(source,p) for p in TEXTMAPS}
 for lang in LANGS:
  for tier in ('','_Medium'):
   path=f'TextMap/TextMap{tier}{lang}.json';d=defaultdict(list)
   for ordinal,(key,text) in enumerate(pairs(source/path)):d[key].append((path,seals[path]['blob'],ordinal,'/'+key,text,sha_bytes(text.encode())))
   maps[lang,tier]=d
 slots=[];texts=[]
 for path,ordinal,native,field,locator in locators:
  rowid=row_ids[(path,f'/{ordinal}')]
  for lang in LANGS:
   hits=maps[lang,''].get(str(locator),[]) or maps[lang,'_Medium'].get(str(locator),[]);status='missing' if not hits else 'resolved' if len(hits)==1 else 'ambiguous';sid=ident('slot',rowid,field,lang);slots.append((sid,rowid,field,lang,str(locator),status,len(hits)))
   texts.extend((ident('text',sid,p,o,ptr),sid,p,blob,o,ptr,text,textsha) for p,blob,o,ptr,text,textsha in hits)
 check([tuple(x) for x in db.execute('select * from quest_review_text_slot order by id')]==sorted(slots),'text slots');check([tuple(x) for x in db.execute('select * from quest_review_text_candidate order by id')]==sorted(texts),'text candidates')
 for table,expected in zip(TABLES,(rows,relations,candidates,slots,texts)):check(db.execute('select count(*) from '+table).fetchone()[0]==len(expected),'extra/missing '+table)
 check(not db.execute('pragma foreign_key_check').fetchall() and db.execute('pragma integrity_check').fetchone()[0]=='ok','database integrity');check(json.loads(db.execute('select manifest_json from quest_review_release').fetchone()[0])==manifest['release'],'release')
 logical={t:{'rows':db.execute('select count(*) from '+t).fetchone()[0],'sha256':hashlib.sha256(b''.join((canonical(list(x))+'\n').encode() for x in db.execute('select * from '+t+' order by 1'))).hexdigest()} for t in TABLES};db.close();ndb.close()
 return {'status':'pass','rows':len(rows),'relations':len(relations),'candidates':len(candidates),'text_slots':len(slots),'texts':len(texts),'row_kinds':dict(Counter(x[2] for x in rows)),'relation_states':dict(Counter(x[6] for x in relations)),'text_states':dict(Counter(x[5] for x in slots)),'independent_candidate_enumeration':True,'exact_narrative_locators':True,'logical_seals':logical,'artifact_sha256':manifest['artifact']['sha256'],'wall_seconds':time.perf_counter()-started}
