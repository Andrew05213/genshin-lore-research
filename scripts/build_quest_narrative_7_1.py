#!/usr/bin/env python3
"""Quest Narrative 7.1 derived observations over the accepted Quest/Talk run."""
from __future__ import annotations
import argparse,hashlib,json,sqlite3,subprocess,sys
from collections import defaultdict,Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from genshin_lore.loading_tips_source import array_records,pairs,sha

COMMIT='9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99';TREE='aa578c08c402af39b41f1ffc95124d10f5410272'
SNAPSHOT='anime-game-data:7.1:'+COMMIT
MAIN='ExcelBinOutput/MainQuestExcelConfigData.json';STEP='ExcelBinOutput/QuestExcelConfigData.json';FMT='ExcelBinOutput/MainQuestFmtQuestRelateExcelConfigData.json'
QT_MANIFEST='manifests/quest-talk-7.1/standalone-candidate.json';QT_ARTIFACT='/tmp/genshin-lore-qt-7.1-final-b.sqlite'
TABLES=('excel_row','main_root_candidate','excel_candidate','child_observation','child_candidate','text_slot','text_candidate')
FIELDS={MAIN:('titleTextMapHash','descTextMapHash','unlockDescTextMapHash'),STEP:('stepDescTextMapHash','guideTipsTextMapHash')}
def canonical(v):return json.dumps(v,sort_keys=True,ensure_ascii=True,separators=(',',':'))
def digest(blob):return hashlib.sha256(blob).hexdigest()
def key(*parts):return 'qn71:'+digest(canonical(parts).encode())[:40]
def source_seals(source):
 paths=(MAIN,STEP,FMT,*(f'TextMap/TextMap{tier}{lang}.json' for lang in ('CHS','EN','JP') for tier in ('','_Medium')))
 return {p:{'size':(source/p).stat().st_size,'sha256':sha(source/p),'blob':subprocess.check_output(['git','-C',str(source),'rev-parse',COMMIT+':'+p],text=True).strip()} for p in paths}
def immutable(db):
 for table in (*TABLES,'release'):
  for operation in ('INSERT','UPDATE','DELETE'):
   db.execute(f"CREATE TRIGGER immutable_{table}_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'immutable quest narrative'); END")
def qt_binding(artifact,manifest_path):
 manifest=json.loads(Path(manifest_path).read_text())
 if manifest['status']!='accepted_standalone_candidate' or manifest['source_snapshot']!=SNAPSHOT:raise ValueError('Quest/Talk accepted 7.1 binding')
 if Path(artifact).stat().st_size!=manifest['artifact']['size_bytes'] or sha(artifact)!=manifest['artifact']['sha256']:raise ValueError('Quest/Talk artifact drift')
 db=sqlite3.connect(Path(artifact).absolute().as_uri()+'?mode=ro&immutable=1',uri=True)
 release=db.execute('SELECT source_commit,source_tree,parser_build_id,ingest_run_id FROM release').fetchone()
 if release!=(COMMIT,TREE,manifest['parser_build_id'],manifest['ingest_run_id']):raise ValueError('Quest/Talk run mismatch')
 return manifest,db
def build(source,output,qt_artifact,qt_manifest):
 source=Path(source);output=Path(output)
 if subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()!=[COMMIT,TREE]:raise ValueError('7.1 source pin')
 seals=source_seals(source);qt,qt_db=qt_binding(qt_artifact,qt_manifest)
 impl={p:sha(ROOT/p) for p in ('scripts/build_quest_narrative_7_1.py','schemas/quest-narrative-7.1-v1.sql','src/genshin_lore/loading_tips_source.py')}
 parser=key('parser',impl);run=key('run',SNAPSHOT,parser,seals,qt['ingest_run_id'],qt['artifact'])
 all_rows=[];owner_ids={};raw_rows={};indexed={MAIN:defaultdict(list),STEP:defaultdict(list)}
 for path,kind,native_field in ((MAIN,'main_quest','id'),(STEP,'quest_step','subId'),(FMT,'format_config','mainQuestID')):
  raw_rows[path]=[]
  for position,value,lexical in array_records(source/path):
   if not isinstance(value,dict) or type(value.get(native_field)) is not int:raise ValueError(f'Excel source shape {path}/{position}')
   pointer=f'/{position}';rid=key('excel',run,path,pointer);owner_ids[(path,pointer)]=rid
   all_rows.append((rid,run,kind,path,position,pointer,str(value[native_field]),lexical,digest(lexical.encode())))
   indexed.get(path,defaultdict(list))[value[native_field]].append((rid,path,pointer))
   raw_rows[path].append((position,value))
 roots=defaultdict(list)
 for cid,path,native in qt_db.execute("SELECT id,source_path,native_id FROM physical_container WHERE kind='quest_root' ORDER BY source_path,id"):
  if native is not None:roots[native].append((cid,path))
 root_candidates=[]
 for position,value in raw_rows[MAIN]:
  rid=owner_ids[(MAIN,f'/{position}')]
  for cid,path in roots.get(str(value['id']),[]):root_candidates.append((key('main_root',rid,cid),rid,cid,path,str(value['id']),'physical_candidate'))
 excel_candidates=[]
 for source_path,field,role in ((STEP,'mainId','step_mainquest'),(FMT,'mainQuestID','format_mainquest')):
  for position,value in raw_rows[source_path]:
   locator=value.get(field)
   if type(locator) is not int:raise ValueError(f'Excel relation shape {source_path}/{position}/{field}')
   row_id=owner_ids[(source_path,f'/{position}')]
   for target_id,_,target_pointer in indexed[MAIN].get(locator,[]):
    excel_candidates.append((key('excel_candidate',row_id,role,target_id),row_id,role,target_id,f'/{position}/{field}',target_pointer))
 container_paths={cid:path for cid,path in qt_db.execute("SELECT id,source_path FROM physical_container WHERE kind='quest_root'")}
 children=[];child_targets=[]
 for child_id,container_id,ordinal,pointer,raw in qt_db.execute('SELECT id,container_id,ordinal,pointer,raw_json FROM quest_child ORDER BY id'):
  path=container_paths.get(container_id)
  if path is None:raise ValueError('Quest/Talk child without Quest root')
  value=json.loads(raw)
  if pointer.startswith('/JIJKODHIEED/'):
   shape='7.1-versioned';sub=value.get('NFGFDHPPBIF');parent=value.get('PCOMHEOJPOO');order=value.get('GBFIFKGFKHD')
   if type(sub) is not int or type(parent) is not int or (order is not None and type(order) is not int):raise ValueError('7.1 child field shape '+path+pointer)
   sub_status='versioned_locator_observation'
  elif pointer.startswith('/EBNBLBEIFFJ/'):
   shape='historical_rc_drift';sub=value.get('KCGAKLCHDCC');parent=None;order=value.get('EMNMIOBCCLL');sub_status='mapping_unresolved'
  elif pointer.startswith('/MEGJPCLADOG/'):
   shape='historical_alt';sub=None;parent=None;order=None;sub_status='mapping_unresolved'
  else:raise ValueError('unknown Quest child pointer '+path+pointer)
  sid=key('child',run,child_id)
  step_hits=indexed[STEP].get(sub,[]) if sub_status=='versioned_locator_observation' else []
  main_hits=indexed[MAIN].get(parent,[]) if parent is not None else []
  children.append((sid,child_id,container_id,path,pointer,ordinal,shape,None if sub is None else str(sub),None if parent is None else str(parent),canonical(order),'unresolved',sub_status,len(step_hits),len(main_hits)))
  for role,hits in (('quest_step',step_hits),('main_quest',main_hits)):
   for target_id,target_path,target_pointer in hits:child_targets.append((key('child_candidate',sid,role,target_id),sid,role,target_id,target_path,target_pointer))
 owners=[]
 for path,fields in FIELDS.items():
  for position,value in raw_rows[path]:
   for field in fields:
    raw=value.get(field)
    if type(raw) is not int:raise ValueError(f'Excel text owner shape {path}/{position}/{field}')
    owners.append((owner_ids[(path,f'/{position}')],field,raw))
 wanted={str(raw) for _,_,raw in owners};maps={}
 for lang in ('CHS','EN','JP'):
  for tier in ('','_Medium'):
   path=f'TextMap/TextMap{tier}{lang}.json';values=defaultdict(list)
   for position,(hash_key,text) in enumerate(pairs(source/path)):
    if hash_key in wanted:values[hash_key].append((path,position,'/'+hash_key,text))
   maps[lang,tier]=values
 slots=[];texts=[]
 for row_id,field,raw in owners:
  for lang in ('CHS','EN','JP'):
   hits=maps[lang,''].get(str(raw),[]) or maps[lang,'_Medium'].get(str(raw),[])
   sid=key('slot',row_id,field,lang);status='missing' if not hits else 'resolved_unique' if len(hits)==1 else 'resolved_candidate_set'
   slots.append((sid,row_id,field,lang,str(raw),status,len(hits)))
   for path,position,pointer,text in hits:texts.append((key('text',sid,path,position,pointer),sid,path,position,pointer,text,digest(text.encode())))
 # Quest/Talk owns all physical Quest roots, children and Talk candidates. This artifact only annotates them.
 qt_counts={'quest_root':qt_db.execute("SELECT count(*) FROM physical_container WHERE kind='quest_root'").fetchone()[0],'quest_child':qt_db.execute('SELECT count(*) FROM quest_child').fetchone()[0],'physical_reference':qt_db.execute("SELECT count(*) FROM physical_reference WHERE kind='quest_to_talk'").fetchone()[0]}
 qt_db.close()
 data=(all_rows,root_candidates,excel_candidates,children,child_targets,slots,texts)
 release={'contract':'quest-narrative-7.1-derived-v1','status':'candidate','snapshot':SNAPSHOT,'source_commit':COMMIT,'source_tree':TREE,'source_seals':seals,'implementation':impl,'parser_build_id':parser,'ingest_run_id':run,'quest_talk_binding':{'manifest':qt_manifest,'manifest_sha256':sha(qt_manifest),'artifact':qt['artifact'],'parser_build_id':qt['parser_build_id'],'ingest_run_id':qt['ingest_run_id'],'counts':qt_counts},'counts':{table:len(records) for table,records in zip(TABLES,data)},'row_kinds':dict(Counter(row[2] for row in all_rows)),'child_shapes':dict(Counter(row[6] for row in children)),'semantic_order_status':'unresolved; raw candidate observed, physical ordinal kept separately','child_sub_mapping':'NFGFDHPPBIF raw 7.1 locator with exact same-snapshot candidate lookup; no cross-snapshot identity','quest_talk_physical_evidence_copied':False,'product_accepted':False}
 if output.exists():raise FileExistsError(output)
 output.parent.mkdir(parents=True,exist_ok=True);db=sqlite3.connect(output);db.execute('PRAGMA page_size=4096');db.execute('PRAGMA journal_mode=OFF');db.execute('PRAGMA foreign_keys=ON')
 try:
  db.executescript((ROOT/'schemas/quest-narrative-7.1-v1.sql').read_text())
  for table,records in zip(TABLES,data):
   if records:db.executemany(f"INSERT INTO {table} VALUES ({','.join('?'*len(records[0]))})",records)
  db.execute('INSERT INTO release VALUES(1,?)',(canonical(release),));immutable(db);db.commit();db.execute('VACUUM');db.close()
 except BaseException:
  db.close();output.unlink(missing_ok=True);raise
 return {'release':release,'artifact':{'size_bytes':output.stat().st_size,'sha256':sha(output)}}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--output',required=True);p.add_argument('--quest-talk-artifact',default=QT_ARTIFACT);p.add_argument('--quest-talk-manifest',default=QT_MANIFEST);p.add_argument('--report');a=p.parse_args();result=build(a.source,a.output,a.quest_talk_artifact,a.quest_talk_manifest)
 if a.report:
  path=Path(a.report)
  if path.exists():raise FileExistsError(path)
  path.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
 print(json.dumps(result['release']['counts']))
