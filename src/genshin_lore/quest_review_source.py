"""Pinned physical source contract for native Quest review and summarization evidence."""
from __future__ import annotations
import hashlib,json,sqlite3,subprocess
from collections import defaultdict
from pathlib import Path
from .loading_tips_source import array_records,pairs,sha
from .quest_narrative_source import ident as narrative_ident

COMMIT='26df1dfbdf05a82bbb1d97506859f3e1c40718d8';TREE='d891ef1d95fdd6c27a1da8a32ea7a61b483647a6';SNAPSHOT='anime-game-data:7.0:'+COMMIT
FAMILY='quest_review_archive';CONTRACT='quest-review-archive-evidence-v1'
SUMMARY='ExcelBinOutput/QuestSummarizationTextExcelConfigData.json';REVIEW='ExcelBinOutput/QuestReviewExcelConfigData.json';TAB='ExcelBinOutput/QuestReviewTabGroupExcelConfigData.json';STEP='ExcelBinOutput/QuestExcelConfigData.json'
LANGS=('CHS','EN','JP');PRIMARY=(SUMMARY,REVIEW,TAB);TEXTMAPS=tuple(f'TextMap/TextMap{tier}{lang}.json' for lang in LANGS for tier in ('','_Medium'))

def canonical(x):return json.dumps(x,sort_keys=True,ensure_ascii=True,separators=(',',':'))
def sha_bytes(x):return hashlib.sha256(x).hexdigest()
def ident(*parts):return 'quest-review:'+sha_bytes(canonical(parts).encode())[:40]
def load(path):return json.loads(Path(path).read_text())
def file_seal(root,relative):
 p=Path(root)/relative;blob=subprocess.check_output(['git','-C',str(root),'rev-parse',COMMIT+':'+relative],text=True).strip()
 return {'path':relative,'size_bytes':p.stat().st_size,'sha256':sha(p),'blob':blob}
def check_pin(root):
 got=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()
 if got!=[COMMIT,TREE]:raise ValueError('source pin')

def _rows(root,path,kind,id_field):
 seal=file_seal(root,path);out=[]
 for ordinal,value,lexical in array_records(Path(root)/path):
  if not isinstance(value,dict) or id_field not in value:raise ValueError('row shape '+path)
  pointer=f'/{ordinal}';out.append({'kind':kind,'source_path':path,'source_ordinal':ordinal,'source_pointer':pointer,'native_id':str(value[id_field]),'source_blob_oid':seal['blob'],'source_file_sha256':seal['sha256'],'raw_record_sha256':sha_bytes(lexical.encode()),'raw_record_json':lexical,'classification':'physical_row_processed','value':value})
 return out

def analyze(root):
 check_pin(root);summary=_rows(root,SUMMARY,'summarization','Id');review=_rows(root,REVIEW,'review_group','id');tabs=_rows(root,TAB,'review_tab','id');steps=load(Path(root)/STEP);step_ids=defaultdict(list)
 for i,x in enumerate(steps):step_ids[x['subId']].append(i)
 review_ids=defaultdict(list)
 for x in review+tabs:review_ids[int(x['native_id'])].append((x['source_path'],x['source_pointer']))
 summary_candidates=sum(len(step_ids[int(x['native_id'])]) for x in summary);refs=[v for x in review for v in x['value'].get('KHAPIAPJAHD',[])];review_candidates=sum(len(review_ids[v]) for v in refs)
 locators=[(x,'DescTextMapHash',x['value']['DescTextMapHash']) for x in summary]+[(x,'titleTextMapHash',x['value']['titleTextMapHash']) for x in tabs]+[(x,f'GIAIIFFDFNE/{i}',v) for x in tabs for i,v in enumerate(x['value']['GIAIIFFDFNE'])]
 maps={};status=defaultdict(int);texts=0
 for lang in LANGS:
  for tier in ('','_Medium'):
   path=f'TextMap/TextMap{tier}{lang}.json';d=defaultdict(list)
   for ordinal,(key,text) in enumerate(pairs(Path(root)/path)):d[key].append((path,ordinal,'/'+key,text))
   maps[lang,tier]=d
 for _,_,locator in locators:
  for lang in LANGS:
   hits=maps[lang,''].get(str(locator),[]) or maps[lang,'_Medium'].get(str(locator),[]);state='missing' if not hits else 'resolved' if len(hits)==1 else 'ambiguous';status[state]+=1;texts+=len(hits)
 return {'rows':{'summarization':len(summary),'review_group':len(review),'review_tab':len(tabs)},'relations':{'summarization_id_observation':len(summary),'review_member_locator_observation':len(refs)},'relation_states':{'mapping_unresolved':len(summary)+len(refs)},'candidates':summary_candidates+review_candidates,'candidate_cardinality':{'zero':sum(not step_ids[int(x['native_id'])] for x in summary)+sum(not review_ids[v] for v in refs),'one':sum(len(step_ids[int(x['native_id'])])==1 for x in summary)+sum(len(review_ids[v])==1 for v in refs),'multiple':sum(len(step_ids[int(x['native_id'])])>1 for x in summary)+sum(len(review_ids[v])>1 for v in refs)},'text_slots':dict(status),'texts':texts,'text_owners':len(locators)}

def project(root,run,narrative_artifact,narrative_manifest):
 check_pin(root);nm=load(narrative_manifest);nrun=nm['release']['run_id'];db=sqlite3.connect(Path(narrative_artifact).absolute().as_uri()+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
 if Path(narrative_artifact).stat().st_size!=nm['artifact']['size_bytes'] or sha(narrative_artifact)!=nm['artifact']['sha256']:raise ValueError('narrative dependency')
 steps=load(Path(root)/STEP);step_ids=defaultdict(list)
 for ordinal,value in enumerate(steps):step_ids[value['subId']].append({'source_path':STEP,'source_pointer':f'/{ordinal}','native_id':str(value['subId']),'occurrence_id':narrative_ident('container',STEP,f'/{ordinal}')})
 for hits in step_ids.values():
  for hit in hits:
   actual=db.execute("select source_path,source_pointer,native_id from quest_narrative_container where id=? and kind='quest_step_row'",(hit['occurrence_id'],)).fetchone()
   if actual is None or tuple(actual)!=(hit['source_path'],hit['source_pointer'],hit['native_id']):raise ValueError('narrative target coordinate')
 db.close()
 rows0=_rows(root,SUMMARY,'summarization','Id')+_rows(root,REVIEW,'review_group','id')+_rows(root,TAB,'review_tab','id');rows=[];row_by={}
 for x in rows0:
  rid=ident('row',run,x['source_path'],x['source_pointer']);row_by[(x['source_path'],x['source_pointer'])]=rid;rows.append({'id':rid,'run_id':run,**{k:v for k,v in x.items() if k!='value'}})
 relations=[];candidates=[]
 def relation(row,kind,pointer,value,namespace,hits,status,semantics):
  rid=ident('relation',run,row['source_path'],pointer,kind);relations.append({'id':rid,'row_id':row_by[(row['source_path'],row['source_pointer'])],'kind':kind,'source_pointer':pointer,'raw_value_json':canonical(value),'candidate_namespace':namespace,'status':status,'candidate_count':len(hits),'search_complete':0,'relation_semantics':semantics})
  for hit in hits:
   cid=ident('candidate',rid,hit['target_source_path'],hit['target_source_pointer'],hit['target_run'],hit['target_occurrence_id']);candidates.append({'id':cid,'relation_id':rid,**hit})
 summary=[x for x in rows0 if x['source_path']==SUMMARY]
 for x in summary:
  value=int(x['native_id']);hits=[{'target_source_path':n['source_path'],'target_source_pointer':n['source_pointer'],'target_native_id':n['native_id'],'target_family':'quest_narrative_archive','target_contract':'quest-narrative-evidence-v1','target_run':nrun,'target_occurrence_id':n['occurrence_id'],'locator_kind':'explicit_unresolved_equality_navigation_candidate'} for n in step_ids[value]]
  relation(x,'summarization_id_observation',x['source_pointer']+'/Id',value,'observed equal raw values in complete QuestExcel subId physical namespace; source does not prove identity or close semantic candidate search',hits,'mapping_unresolved','cross-source equality observation; candidates are navigation aids, not source-authored relations')
 review_lookup=defaultdict(list)
 for target in (x for x in rows0 if x['source_path'] in (REVIEW,TAB)):review_lookup[int(target['native_id'])].append(target)
 for x in (y for y in rows0 if y['source_path']==REVIEW):
  for j,value in enumerate(x['value'].get('KHAPIAPJAHD',[])):
   hits=[{'target_source_path':target['source_path'],'target_source_pointer':target['source_pointer'],'target_native_id':target['native_id'],'target_family':FAMILY,'target_contract':CONTRACT,'target_run':run,'target_occurrence_id':row_by[(target['source_path'],target['source_pointer'])],'locator_kind':'explicit_unresolved_same_value_navigation_candidate'} for target in review_lookup[value]]
   relation(x,'review_member_locator_observation',x['source_pointer']+f'/KHAPIAPJAHD/{j}',value,'observed equal raw values across complete QuestReview and QuestReviewTabGroup physical rows; obfuscated field semantics remain unresolved',hits,'mapping_unresolved','raw review-member observation with 0/1/N navigation candidates; not asserted as source-authored child relation')
 tabs=[x for x in rows0 if x['source_path']==TAB]
 locators=[(x,'DescTextMapHash',x['value']['DescTextMapHash']) for x in summary]+[(x,'titleTextMapHash',x['value']['titleTextMapHash']) for x in tabs]+[(x,f'GIAIIFFDFNE/{i}',v) for x in tabs for i,v in enumerate(x['value']['GIAIIFFDFNE'])]
 maps={};seals={p:file_seal(root,p) for p in TEXTMAPS};slots=[];texts=[]
 for lang in LANGS:
  for tier in ('','_Medium'):
   path=f'TextMap/TextMap{tier}{lang}.json';d=defaultdict(list)
   for ordinal,(key,text) in enumerate(pairs(Path(root)/path)):d[key].append((path,ordinal,'/'+key,text,seals[path]['blob']))
   maps[lang,tier]=d
 for row,field,locator in locators:
  rowid=row_by[(row['source_path'],row['source_pointer'])]
  for lang in LANGS:
   hits=maps[lang,''].get(str(locator),[]) or maps[lang,'_Medium'].get(str(locator),[]);status='missing' if not hits else 'resolved' if len(hits)==1 else 'ambiguous';sid=ident('slot',rowid,field,lang);slots.append({'id':sid,'row_id':rowid,'owner_field':field,'locale':lang,'raw_locator':str(locator),'status':status,'candidate_count':len(hits)})
   for path,ordinal,pointer,text,blob in hits:texts.append({'id':ident('text',sid,path,ordinal,pointer),'slot_id':sid,'source_path':path,'source_blob_oid':blob,'source_ordinal':ordinal,'source_pointer':pointer,'raw_text':text,'raw_text_sha256':sha_bytes(text.encode())})
 return rows,relations,candidates,slots,texts
