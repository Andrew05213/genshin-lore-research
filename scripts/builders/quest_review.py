#!/usr/bin/env python3
"""Snapshot-local Quest Review projection; all relations remain raw candidates."""
from __future__ import annotations
import argparse,hashlib,json,sqlite3,subprocess,sys
from collections import defaultdict,Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from genshin_lore.loading_tips_source import array_records,pairs,sha

COMMIT='9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99'
TREE='aa578c08c402af39b41f1ffc95124d10f5410272'
SNAPSHOT='anime-game-data:7.1:'+COMMIT
SUMMARY='ExcelBinOutput/QuestSummarizationTextExcelConfigData.json'
GROUP='ExcelBinOutput/QuestReviewExcelConfigData.json'
TAB='ExcelBinOutput/QuestReviewTabGroupExcelConfigData.json'
STEP='ExcelBinOutput/QuestExcelConfigData.json'
TABLES=('evidence_row','locator','locator_candidate','text_slot','text_candidate')
SOURCES=(SUMMARY,GROUP,TAB,STEP)
QT_MANIFEST='manifests/quest-talk-7.1/standalone-candidate.json'
QT_ARTIFACT='/tmp/genshin-lore-qt-7.1-final-b.sqlite'
FIELD_PROOF=ROOT/'manifests/quest-derived-7.1/review-field-continuity-v1.json'

def canon(x):return json.dumps(x,ensure_ascii=True,sort_keys=True,separators=(',',':'))
def digest(value):return hashlib.sha256(value).hexdigest()
def key(*parts):return 'qr71:'+digest(canon(parts).encode())[:40]
def pin(source):
 actual=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()
 if actual!=[COMMIT,TREE]:raise ValueError('wrong 7.1 source pin')
def seals(source):
 files=(*SOURCES,*(f'TextMap/TextMap{tier}{lang}.json' for lang in ('CHS','EN','JP') for tier in ('','_Medium')))
 return {p:{'size':(source/p).stat().st_size,'sha256':sha(source/p),'blob':subprocess.check_output(['git','-C',str(source),'rev-parse',COMMIT+':'+p],text=True).strip()} for p in files}
def guard(c):
 for table in (*TABLES,'release'):
  for op in ('INSERT','UPDATE','DELETE'):
   c.execute(f"CREATE TRIGGER immutable_{table}_{op.lower()} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'immutable quest review'); END")
def build(source,output,qt_manifest=QT_MANIFEST,qt_artifact=QT_ARTIFACT):
 source=Path(source);output=Path(output);pin(source)
 source_seals=seals(source)
 proof=json.loads(FIELD_PROOF.read_text())
 if proof['verdict']!='PROVEN_PHYSICAL_FIELD_CONTINUITY_NOT_LOGICAL_IDENTITY' or proof['new_source_commit']!=COMMIT:raise ValueError('Quest Review field proof')
 for path,entry in proof['source_seals'].items():
  if source_seals[path]['sha256']!=entry['new_sha256'] or source_seals[path]['blob']!=entry['new_git_blob']:raise ValueError('Quest Review field proof source '+path)
 accepted=json.loads(Path(qt_manifest).read_text())
 if accepted['source_snapshot']!=SNAPSHOT or accepted['status']!='accepted_standalone_candidate':raise ValueError('Quest/Talk dependency acceptance')
 if Path(qt_artifact).stat().st_size!=accepted['artifact']['size_bytes'] or sha(qt_artifact)!=accepted['artifact']['sha256']:raise ValueError('Quest/Talk dependency artifact')
 qt=sqlite3.connect(Path(qt_artifact).absolute().as_uri()+'?mode=ro&immutable=1',uri=True)
 qt_release=qt.execute('SELECT source_commit,source_tree,parser_build_id,ingest_run_id FROM release').fetchone()
 if qt_release!=(COMMIT,TREE,accepted['parser_build_id'],accepted['ingest_run_id']):raise ValueError('Quest/Talk dependency run')
 qt.close()
 binding={'manifest_sha256':sha(qt_manifest),'artifact':accepted['artifact'],'parser_build_id':accepted['parser_build_id'],'ingest_run_id':accepted['ingest_run_id'],'contract':accepted['contract']}
 impl={p:sha(ROOT/p) for p in ('scripts/builders/quest_review.py','scripts/maintenance/quest_review_fields.py','schemas/quest-review-7.1-v1.sql','src/genshin_lore/loading_tips_source.py','manifests/quest-derived-7.1/review-field-continuity-v1.json')}
 parser=key('parser',impl);run=key('run',SNAPSHOT,parser,source_seals,binding)
 parsed={};rows=[];row_ids={};id_lookup=defaultdict(list)
 for path,kind,field in ((SUMMARY,'summarization','Id'),(GROUP,'review_group','id'),(TAB,'review_tab','id')):
  parsed[path]=[]
  for ordinal,value,lexical in array_records(source/path):
   if not isinstance(value,dict) or not isinstance(value.get(field),int):raise ValueError(f'unknown row shape {path}/{ordinal}')
   ptr=f'/{ordinal}';rid=key('row',run,path,ptr);row_ids[(path,ptr)]=rid
   rows.append((rid,run,kind,path,ordinal,ptr,str(value[field]),lexical,digest(lexical.encode())))
   parsed[path].append((ordinal,value))
   if path in (GROUP,TAB):id_lookup[value[field]].append((path,ptr,str(value[field])))
 step_lookup=defaultdict(list)
 for ordinal,value,_ in array_records(source/STEP):
  if not isinstance(value,dict) or not isinstance(value.get('subId'),int):raise ValueError(f'QuestExcel shape /{ordinal}')
  step_lookup[value['subId']].append((STEP,f'/{ordinal}',str(value['subId'])))
 loc=[];candidates=[]
 def add_locator(path,ordinal,role,pointer,raw,hits):
  rid=row_ids[(path,f'/{ordinal}')];lid=key('locator',rid,role,pointer)
  loc.append((lid,rid,role,pointer,str(raw),'mapping_unresolved',len(hits)))
  for target_path,target_pointer,native in hits:
   candidates.append((key('candidate',lid,target_path,target_pointer),lid,target_path,target_pointer,native))
 for ordinal,value in parsed[SUMMARY]:
  add_locator(SUMMARY,ordinal,'summarization_id_observation',f'/{ordinal}/Id',value['Id'],step_lookup[value['Id']])
 for ordinal,value in parsed[GROUP]:
  members=value.get('GJHKONEAHPC',[])
  if not isinstance(members,list) or any(not isinstance(v,int) for v in members):raise ValueError(f'group member shape /{ordinal}')
  for i,target in enumerate(members):add_locator(GROUP,ordinal,'review_member_locator_observation',f'/{ordinal}/GJHKONEAHPC/{i}',target,id_lookup[target])
 owners=[]
 for ordinal,value in parsed[SUMMARY]:
  owners.append((SUMMARY,ordinal,'DescTextMapHash',value.get('DescTextMapHash')))
 for ordinal,value in parsed[TAB]:
  owners.append((TAB,ordinal,'titleTextMapHash',value.get('titleTextMapHash')))
  texts=value.get('CCIHLEICLMF')
  if not isinstance(texts,list) or any(not isinstance(v,int) for v in texts):raise ValueError(f'tab text owner shape /{ordinal}')
  owners.extend((TAB,ordinal,f'CCIHLEICLMF/{i}',v) for i,v in enumerate(texts))
 if any(not isinstance(raw,int) for _,_,_,raw in owners):raise ValueError('text owner shape')
 maps={}
 for lang in ('CHS','EN','JP'):
  for tier in ('','_Medium'):
   path=f'TextMap/TextMap{tier}{lang}.json';mapping=defaultdict(list)
   for index,(h,text) in enumerate(pairs(source/path)):mapping[h].append((path,index,'/'+h,text))
   maps[lang,tier]=mapping
 slots=[];text_candidates=[]
 for path,ordinal,field,raw in owners:
  rid=row_ids[(path,f'/{ordinal}')]
  for lang in ('CHS','EN','JP'):
   hits=maps[lang,''].get(str(raw),[]) or maps[lang,'_Medium'].get(str(raw),[])
   sid=key('slot',rid,field,lang);status='missing' if not hits else 'resolved_unique' if len(hits)==1 else 'resolved_candidate_set'
   slots.append((sid,rid,field,lang,str(raw),status,len(hits)))
   for target_path,index,pointer,text in hits:
    text_candidates.append((key('text',sid,target_path,index,pointer),sid,target_path,index,pointer,text,digest(text.encode())))
 data=(rows,loc,candidates,slots,text_candidates)
 release={'contract':'quest-review-archive-7.1-evidence-v1','status':'candidate','snapshot':SNAPSHOT,'source_commit':COMMIT,'source_tree':TREE,'source_seals':source_seals,'implementation':impl,'parser_build_id':parser,'ingest_run_id':run,'quest_talk_binding':binding,'field_proof_sha256':sha(FIELD_PROOF),'counts':{t:len(d) for t,d in zip(TABLES,data)},'kinds':dict(Counter(r[2] for r in rows)),'locator_semantics':'mapping_unresolved equality navigation; no logical relation','text_resolution':'main else Medium; all physical candidates preserved','product_accepted':False}
 output.parent.mkdir(parents=True,exist_ok=True)
 if output.exists():raise FileExistsError(output)
 c=sqlite3.connect(output);c.execute('PRAGMA page_size=4096');c.execute('PRAGMA journal_mode=OFF');c.execute('PRAGMA foreign_keys=ON')
 try:
  c.executescript((ROOT/'schemas/quest-review-7.1-v1.sql').read_text())
  for table,records in zip(TABLES,data):
   if records:c.executemany(f"INSERT INTO {table} VALUES ({','.join('?'*len(records[0]))})",records)
  c.execute('INSERT INTO release VALUES(1,?)',(canon(release),));guard(c);c.commit();c.execute('VACUUM');c.close()
 except BaseException:
  c.close();raise
 return {'release':release,'artifact':{'size_bytes':output.stat().st_size,'sha256':sha(output)}}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--output',required=True);p.add_argument('--quest-talk-manifest',default=QT_MANIFEST);p.add_argument('--quest-talk-artifact',default=QT_ARTIFACT);p.add_argument('--report');a=p.parse_args();result=build(a.source,a.output,a.quest_talk_manifest,a.quest_talk_artifact)
 if a.report:
  path=Path(a.report)
  if path.exists():raise FileExistsError(path)
  path.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
 print(json.dumps(result['release']['counts'],ensure_ascii=False))
