#!/usr/bin/env python3
"""Independent source-to-rowset oracle for Quest Narrative 7.1."""
from __future__ import annotations
import argparse,hashlib,json,sqlite3,subprocess,sys
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src'))
from genshin_lore.loading_tips_source import array_records,pairs,sha
COMMIT='9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99';TREE='aa578c08c402af39b41f1ffc95124d10f5410272'
MAIN='ExcelBinOutput/MainQuestExcelConfigData.json';STEP='ExcelBinOutput/QuestExcelConfigData.json';FORMAT='ExcelBinOutput/MainQuestFmtQuestRelateExcelConfigData.json'
TABLES=('excel_row','main_root_candidate','excel_candidate','child_observation','child_candidate','text_slot','text_candidate')
def canonical(value):return json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':'))
def hash_bytes(value):return hashlib.sha256(value).hexdigest()
def identifier(*parts):return 'qn71:'+hash_bytes(canonical(parts).encode())[:40]
def check(value,message):
 if not value:raise ValueError(message)
def rowset(db,table,expected):
 actual=[tuple(x) for x in db.execute('SELECT * FROM '+table+' ORDER BY 1')]
 check(actual==sorted(expected),f'{table}: actual {len(actual)} expected {len(expected)}; first mismatch '+str(next(((a,b) for a,b in zip(actual,sorted(expected)) if a!=b),None))[:500])
 return {'rows':len(actual),'sha256':hash_bytes(b''.join((canonical(list(row))+'\n').encode() for row in actual))}
def schema(db):
 expected=sqlite3.connect(':memory:');expected.executescript((ROOT/'schemas/quest-narrative-7.1-v1.sql').read_text())
 for table in (*TABLES,'release'):
  for operation in ('INSERT','UPDATE','DELETE'):
   expected.execute(f"CREATE TRIGGER immutable_{table}_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'immutable quest narrative'); END")
 sql="SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
 check([tuple(r) for r in db.execute(sql)]==[tuple(r) for r in expected.execute(sql)],'schema/index/guard drift')
 expected.close()
def verify(source,artifact,report,quest_talk_artifact,quest_talk_manifest):
 source=Path(source);artifact=Path(artifact);manifest=json.loads(Path(report).read_text());release=manifest['release']
 check(subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()==[COMMIT,TREE],'pinned source')
 check(release['source_commit']==COMMIT and release['source_tree']==TREE,'release pin')
 check(release['contract']=='quest-narrative-7.1-derived-v1' and release['status']=='candidate' and release['product_accepted'] is False,'release authority boundary')
 check(artifact.stat().st_size==manifest['artifact']['size_bytes'] and sha(artifact)==manifest['artifact']['sha256'],'artifact seal')
 check(not any(Path(str(artifact)+x).exists() for x in ('-wal','-shm','-journal')),'sidecar')
 for relative,seal in release['source_seals'].items():
  path=source/relative
  check(path.stat().st_size==seal['size'] and sha(path)==seal['sha256'],'source bytes '+relative)
  check(subprocess.check_output(['git','-C',str(source),'rev-parse',COMMIT+':'+relative],text=True).strip()==seal['blob'],'source Git blob '+relative)
 impl={p:sha(ROOT/p) for p in ('scripts/builders/quest_narrative.py','schemas/quest-narrative-7.1-v1.sql','src/genshin_lore/loading_tips_source.py')}
 check(impl==release['implementation'] and identifier('parser',impl)==release['parser_build_id'],'implementation/parser identity')
 qmanifest=json.loads(Path(quest_talk_manifest).read_text());binding=release['quest_talk_binding']
 check(qmanifest['contract']=='quest-talk-7.1-standalone-physical-v1' and qmanifest['status']=='accepted_standalone_candidate' and qmanifest['product_accepted'] is False,'Quest/Talk accepted contract')
 check(qmanifest['source_snapshot']==release['snapshot'] and qmanifest['source_commit']==COMMIT and qmanifest['source_tree']==TREE,'Quest/Talk snapshot')
 check(binding['manifest_sha256']==sha(quest_talk_manifest) and binding['artifact']==qmanifest['artifact'],'Quest/Talk manifest')
 check(binding['ingest_run_id']==qmanifest['ingest_run_id'] and binding['parser_build_id']==qmanifest['parser_build_id'],'Quest/Talk run')
 check(Path(quest_talk_artifact).stat().st_size==qmanifest['artifact']['size_bytes'] and sha(quest_talk_artifact)==qmanifest['artifact']['sha256'],'Quest/Talk source artifact seal')
 check(identifier('run',release['snapshot'],release['parser_build_id'],release['source_seals'],qmanifest['ingest_run_id'],qmanifest['artifact'])==release['ingest_run_id'],'narrative run identity')
 db=sqlite3.connect(artifact.absolute().as_uri()+'?mode=ro&immutable=1',uri=True);qt=sqlite3.connect(Path(quest_talk_artifact).absolute().as_uri()+'?mode=ro&immutable=1',uri=True)
 schema(db)
 check(db.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and not db.execute('PRAGMA foreign_key_check').fetchall(),'database integrity')
 check(json.loads(db.execute('SELECT manifest_json FROM release').fetchone()[0])==release,'release row')
 qrelease=qt.execute('SELECT source_commit,source_tree,parser_build_id,ingest_run_id FROM release').fetchone()
 check(qrelease==(COMMIT,TREE,qmanifest['parser_build_id'],qmanifest['ingest_run_id']),'Quest/Talk release')
 run=release['ingest_run_id'];raw_by={};excel=[];coords={};indexed={MAIN:defaultdict(list),STEP:defaultdict(list)}
 for path,kind,native_key in ((MAIN,'main_quest','id'),(STEP,'quest_step','subId'),(FORMAT,'format_config','mainQuestID')):
  raw_by[path]=[]
  for ordinal,raw,lexical in array_records(source/path):
   check(isinstance(raw,dict) and type(raw.get(native_key)) is int,'Excel record shape '+path+'/'+str(ordinal))
   pointer='/'+str(ordinal);rid=identifier('excel',run,path,pointer)
   excel.append((rid,run,kind,path,ordinal,pointer,str(raw[native_key]),lexical,hash_bytes(lexical.encode())))
   coords[(path,ordinal)]=rid;raw_by[path].append((ordinal,raw))
   if path in indexed:indexed[path][raw[native_key]].append((rid,path,pointer))
 seals={'excel_row':rowset(db,'excel_row',excel)}
 qt_roots=defaultdict(list);qt_paths={}
 for cid,path,native in qt.execute("SELECT id,source_path,native_id FROM physical_container WHERE kind='quest_root'"):
  qt_paths[cid]=path;qt_roots[native].append((cid,path))
 check(len(qt_paths)==4808,'Quest/Talk root denominator')
 roots=[]
 for ordinal,value in raw_by[MAIN]:
  owner=coords[(MAIN,ordinal)]
  for cid,path in qt_roots.get(str(value['id']),[]):roots.append((identifier('main_root',owner,cid),owner,cid,path,str(value['id']),'physical_candidate'))
 seals['main_root_candidate']=rowset(db,'main_root_candidate',roots)
 excel_candidates=[]
 for path,field,role in ((STEP,'mainId','step_mainquest'),(FORMAT,'mainQuestID','format_mainquest')):
  for ordinal,record in raw_by[path]:
   locator=record.get(field);check(type(locator) is int,'Excel relation type '+path)
   owner=coords[(path,ordinal)]
   for target_id,_,target_pointer in indexed[MAIN].get(locator,[]):
    excel_candidates.append((identifier('excel_candidate',owner,role,target_id),owner,role,target_id,f'/{ordinal}/{field}',target_pointer))
 seals['excel_candidate']=rowset(db,'excel_candidate',excel_candidates)
 docs={}
 for path in qt_paths.values():docs[path]=json.loads((source/path).read_bytes().decode('utf-8-sig'))
 children=[];targets=[]
 for child_id,container_id,position,pointer,raw_json in qt.execute('SELECT id,container_id,ordinal,pointer,raw_json FROM quest_child'):
  path=qt_paths.get(container_id);check(path is not None,'child container outside Quest root')
  child=json.loads(raw_json);parts=pointer.split('/')
  check(len(parts)==3 and parts[0]=='' and parts[2].isdigit(),'child physical pointer')
  section=docs[path].get(parts[1]);check(isinstance(section,list) and int(parts[2])<len(section) and section[int(parts[2])]==child,'Quest/Talk child differs from raw source '+path+pointer)
  if parts[1]=='JIJKODHIEED':
   shape='7.1-versioned';sub=child.get('NFGFDHPPBIF');parent=child.get('PCOMHEOJPOO');order=child.get('GBFIFKGFKHD');state='versioned_locator_observation'
   check(type(sub) is int and type(parent) is int and (order is None or type(order) is int),'new child field types')
  elif parts[1]=='EBNBLBEIFFJ':
   shape='historical_rc_drift';sub=child.get('KCGAKLCHDCC');parent=None;order=child.get('EMNMIOBCCLL');state='mapping_unresolved'
  elif parts[1]=='MEGJPCLADOG':
   shape='historical_alt';sub=None;parent=None;order=None;state='mapping_unresolved'
  else:raise ValueError('unknown child array '+path+pointer)
  sid=identifier('child',run,child_id)
  subhits=indexed[STEP].get(sub,[]) if state=='versioned_locator_observation' else []
  mainhits=indexed[MAIN].get(parent,[]) if parent is not None else []
  children.append((sid,child_id,container_id,path,pointer,position,shape,None if sub is None else str(sub),None if parent is None else str(parent),canonical(order),'unresolved',state,len(subhits),len(mainhits)))
  for role,matches in (('quest_step',subhits),('main_quest',mainhits)):
   for target_id,target_path,target_pointer in matches:targets.append((identifier('child_candidate',sid,role,target_id),sid,role,target_id,target_path,target_pointer))
 check(len(children)==35704,'Quest/Talk child denominator')
 seals['child_observation']=rowset(db,'child_observation',children);seals['child_candidate']=rowset(db,'child_candidate',targets)
 check(qt.execute("SELECT count(*) FROM physical_reference WHERE kind='quest_to_talk'").fetchone()[0]==binding['counts']['physical_reference'],'Quest/Talk references read-through')
 needed=set();owners=[]
 for path,fields in ((MAIN,('titleTextMapHash','descTextMapHash','unlockDescTextMapHash')),(STEP,('stepDescTextMapHash','guideTipsTextMapHash'))):
  for ordinal,record in raw_by[path]:
   for field in fields:
    number=record.get(field);check(type(number) is int,'Excel text field '+path+'/'+str(ordinal)+'/'+field)
    owners.append((coords[(path,ordinal)],field,number));needed.add(str(number))
 shards={}
 for lang in ('CHS','EN','JP'):
  for medium in (False,True):
   path=f"TextMap/TextMap{'_Medium' if medium else ''}{lang}.json";found=defaultdict(list)
   for pair_ordinal,(number,text) in enumerate(pairs(source/path)):
    if number in needed:found[number].append((path,pair_ordinal,'/'+number,text))
   shards[(lang,medium)]=found
 slots=[];texts=[]
 for row_id,field,number in owners:
  for lang in ('CHS','EN','JP'):
   matches=shards[(lang,False)].get(str(number),[]) or shards[(lang,True)].get(str(number),[])
   sid=identifier('slot',row_id,field,lang)
   status='missing' if not matches else 'resolved_unique' if len(matches)==1 else 'resolved_candidate_set'
   slots.append((sid,row_id,field,lang,str(number),status,len(matches)))
   for source_path,pair_ordinal,pointer,text in matches:
    texts.append((identifier('text',sid,source_path,pair_ordinal,pointer),sid,source_path,pair_ordinal,pointer,text,hash_bytes(text.encode())))
 seals['text_slot']=rowset(db,'text_slot',slots);seals['text_candidate']=rowset(db,'text_candidate',texts)
 check(release['counts']=={table:seals[table]['rows'] for table in TABLES},'release counts')
 check(release['semantic_order_status'].startswith('unresolved'),'semantic-order honesty')
 db.close();qt.close()
 return {'verdict':'PASS','source_to_rowset_oracle':'independent raw source and accepted Quest/Talk read-through','artifact':manifest['artifact'],'counts':release['counts'],'logical_seals':seals,'semantic_order_status':'unresolved','wrong_identity_merges':0}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--artifact',required=True);p.add_argument('--report',required=True);p.add_argument('--quest-talk-artifact',default='/tmp/genshin-lore-qt-7.1-final-b.sqlite');p.add_argument('--quest-talk-manifest',default='manifests/quest-talk-7.1/standalone-candidate.json');a=p.parse_args();print(json.dumps(verify(a.source,a.artifact,a.report,a.quest_talk_artifact,a.quest_talk_manifest),indent=2))
