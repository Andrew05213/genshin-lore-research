#!/usr/bin/env python3
"""Independent raw-source oracle for the 7.1 Quest Review candidate."""
from __future__ import annotations
import argparse,hashlib,json,sqlite3,subprocess,sys
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT))
from genshin_lore.loading_tips_source import array_records,pairs,sha
from scripts.maintenance.quest_review_fields import proof as field_proof,check_tracked as check_field_proof

SOURCE_COMMIT='9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99'
SOURCE_TREE='aa578c08c402af39b41f1ffc95124d10f5410272'
SUMMARY='ExcelBinOutput/QuestSummarizationTextExcelConfigData.json'
GROUP='ExcelBinOutput/QuestReviewExcelConfigData.json'
TAB='ExcelBinOutput/QuestReviewTabGroupExcelConfigData.json'
QUEST='ExcelBinOutput/QuestExcelConfigData.json'
BUSINESS=('evidence_row','locator','locator_candidate','text_slot','text_candidate')

def j(value):return json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':'))
def h(data):return hashlib.sha256(data).hexdigest()
def oid(*components):return 'qr71:'+h(j(components).encode())[:40]
def require(ok,message):
 if not ok:raise ValueError(message)
def compare(db,table,expected):
 actual=[tuple(r) for r in db.execute(f'SELECT * FROM {table} ORDER BY 1')]
 require(actual==sorted(expected),f'{table} source-to-rowset mismatch; actual={len(actual)} expected={len(expected)}')
 return {'rows':len(expected),'sha256':h(b''.join((j(list(row))+'\n').encode() for row in actual))}
def schema_check(db):
 expected=sqlite3.connect(':memory:')
 expected.executescript((ROOT/'schemas/quest-review-7.1-v1.sql').read_text())
 for name in (*BUSINESS,'release'):
  for op in ('INSERT','UPDATE','DELETE'):
   expected.execute(f"CREATE TRIGGER immutable_{name}_{op.lower()} BEFORE {op} ON {name} BEGIN SELECT RAISE(ABORT,'immutable quest review'); END")
 actual=[tuple(r) for r in db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
 canonical=[tuple(r) for r in expected.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
 require(actual==canonical,'schema, indexes, or immutable trigger definitions differ')
 expected.close()
def verify(source,artifact,report,qt_manifest='manifests/quest-talk-7.1/standalone-candidate.json',qt_artifact='/tmp/genshin-lore-qt-7.1-final-b.sqlite',old_source='<explicit-source-required>/Documents/genshin-lore-database/cache/upstream-agd-git'):
 source=Path(source);artifact=Path(artifact);manifest=json.loads(Path(report).read_text());release=manifest['release']
 require(subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()==[SOURCE_COMMIT,SOURCE_TREE],'source pin')
 require(release['source_commit']==SOURCE_COMMIT and release['source_tree']==SOURCE_TREE,'release pin')
 require(release['contract']=='quest-review-archive-7.1-evidence-v1' and release['status']=='candidate' and release['product_accepted'] is False,'release authority boundary')
 require(artifact.stat().st_size==manifest['artifact']['size_bytes'] and sha(artifact)==manifest['artifact']['sha256'],'artifact seal')
 require(not any(Path(str(artifact)+suffix).exists() for suffix in ('-wal','-shm','-journal')),'SQLite sidecar')
 for path,seal in release['source_seals'].items():
  local=source/path
  require(local.stat().st_size==seal['size'] and sha(local)==seal['sha256'],'source bytes '+path)
  require(subprocess.check_output(['git','-C',str(source),'rev-parse',SOURCE_COMMIT+':'+path],text=True).strip()==seal['blob'],'source blob '+path)
 impl={p:sha(ROOT/p) for p in ('scripts/builders/quest_review.py','scripts/maintenance/quest_review_fields.py','schemas/quest-review-7.1-v1.sql','src/genshin_lore/loading_tips_source.py','manifests/quest-derived-7.1/review-field-continuity-v1.json')}
 require(impl==release['implementation'],'implementation drift')
 proof_path=ROOT/'manifests/quest-derived-7.1/review-field-continuity-v1.json';proof=json.loads(proof_path.read_text())
 require(release['field_proof_sha256']==sha(proof_path) and proof['verdict']=='PROVEN_PHYSICAL_FIELD_CONTINUITY_NOT_LOGICAL_IDENTITY','field proof seal')
 check_field_proof(field_proof(old_source,source),proof)
 require(proof['old_source_commit']=='26df1dfbdf05a82bbb1d97506859f3e1c40718d8' and proof['new_source_commit']==SOURCE_COMMIT,'field proof pins')
 for path,entry in proof['source_seals'].items():
  require(release['source_seals'][path]['sha256']==entry['new_sha256'] and release['source_seals'][path]['blob']==entry['new_git_blob'],'field proof new source '+path)
 require(oid('parser',impl)==release['parser_build_id'],'parser identity')
 qt_manifest_value=json.loads(Path(qt_manifest).read_text());binding=release['quest_talk_binding']
 require(binding['manifest_sha256']==sha(qt_manifest) and binding['artifact']==qt_manifest_value['artifact'],'Quest/Talk manifest binding')
 require(qt_manifest_value['status']=='accepted_standalone_candidate' and qt_manifest_value['source_snapshot']==release['snapshot'],'Quest/Talk accepted 7.1 scope')
 require(Path(qt_artifact).stat().st_size==binding['artifact']['size_bytes'] and sha(qt_artifact)==binding['artifact']['sha256'],'Quest/Talk artifact')
 qt=sqlite3.connect(Path(qt_artifact).absolute().as_uri()+'?mode=ro&immutable=1',uri=True)
 require(qt.execute('SELECT source_commit,source_tree,parser_build_id,ingest_run_id FROM release').fetchone()==(SOURCE_COMMIT,SOURCE_TREE,binding['parser_build_id'],binding['ingest_run_id']),'Quest/Talk exact run')
 qt.close()
 require(oid('run',release['snapshot'],release['parser_build_id'],release['source_seals'],binding)==release['ingest_run_id'],'run identity')
 run=release['ingest_run_id'];db=sqlite3.connect(artifact.absolute().as_uri()+'?mode=ro&immutable=1',uri=True)
 schema_check(db)
 require(db.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and not db.execute('PRAGMA foreign_key_check').fetchall(),'database integrity')
 require(json.loads(db.execute('SELECT manifest_json FROM release').fetchone()[0])==release,'release row')
 rowsets=[];coordinates={};groups={};lookup=defaultdict(list)
 for kind,path,native_key in (('summarization',SUMMARY,'Id'),('review_group',GROUP,'id'),('review_tab',TAB,'id')):
  records=[]
  for position,record,raw in array_records(source/path):
   require(isinstance(record,dict) and type(record.get(native_key)) is int,'primary record type '+path)
   ptr='/'+str(position);rid=oid('row',run,path,ptr)
   rowsets.append((rid,run,kind,path,position,ptr,str(record[native_key]),raw,h(raw.encode())))
   coordinates[(path,position)]=rid;records.append((position,record))
   if kind!='summarization':lookup[record[native_key]].append((path,ptr,str(record[native_key])))
  groups[path]=records
 seals={'evidence_row':compare(db,'evidence_row',rowsets)}
 steps=defaultdict(list)
 for position,record,_ in array_records(source/QUEST):
  require(isinstance(record,dict) and type(record.get('subId')) is int,'QuestExcel subId type')
  steps[record['subId']].append((QUEST,'/'+str(position),str(record['subId'])))
 locators=[];targets=[]
 def add(path,position,role,pointer,value,matching):
  owner=coordinates[(path,position)];lid=oid('locator',owner,role,pointer)
  locators.append((lid,owner,role,pointer,str(value),'mapping_unresolved',len(matching)))
  for target_path,target_pointer,native in matching:
   targets.append((oid('candidate',lid,target_path,target_pointer),lid,target_path,target_pointer,native))
 for position,record in groups[SUMMARY]:add(SUMMARY,position,'summarization_id_observation',f'/{position}/Id',record['Id'],steps[record['Id']])
 for position,record in groups[GROUP]:
  values=record.get('GJHKONEAHPC',[])
  require(isinstance(values,list) and all(type(x) is int for x in values),'review member type')
  for index,value in enumerate(values):add(GROUP,position,'review_member_locator_observation',f'/{position}/GJHKONEAHPC/{index}',value,lookup[value])
 seals['locator']=compare(db,'locator',locators);seals['locator_candidate']=compare(db,'locator_candidate',targets)
 owners=[]
 for position,record in groups[SUMMARY]:owners.append((SUMMARY,position,'DescTextMapHash',record['DescTextMapHash']))
 for position,record in groups[TAB]:
  owners.append((TAB,position,'titleTextMapHash',record['titleTextMapHash']))
  values=record['CCIHLEICLMF'];require(isinstance(values,list) and all(type(x) is int for x in values),'tab owner array')
  for index,value in enumerate(values):owners.append((TAB,position,f'CCIHLEICLMF/{index}',value))
 require(all(type(owner[3]) is int for owner in owners),'text owner locator type')
 shards={}
 for language in ('CHS','EN','JP'):
  for medium in (False,True):
   path=f"TextMap/TextMap{'_Medium' if medium else ''}{language}.json";index=defaultdict(list)
   for ordinal,(number,text) in enumerate(pairs(source/path)):index[number].append((path,ordinal,'/'+number,text))
   shards[(language,medium)]=index
 slots=[];texts=[]
 for path,position,field,raw_hash in owners:
  owner=coordinates[(path,position)]
  for language in ('CHS','EN','JP'):
   matching=shards[(language,False)].get(str(raw_hash),[]) or shards[(language,True)].get(str(raw_hash),[])
   sid=oid('slot',owner,field,language)
   status='missing' if not matching else 'resolved_unique' if len(matching)==1 else 'resolved_candidate_set'
   slots.append((sid,owner,field,language,str(raw_hash),status,len(matching)))
   for shard,ordinal,pointer,text in matching:
    texts.append((oid('text',sid,shard,ordinal,pointer),sid,shard,ordinal,pointer,text,h(text.encode())))
 seals['text_slot']=compare(db,'text_slot',slots);seals['text_candidate']=compare(db,'text_candidate',texts)
 require(release['counts']=={table:seals[table]['rows'] for table in BUSINESS},'reported counts')
 db.close()
 return {'verdict':'PASS','artifact':manifest['artifact'],'counts':release['counts'],'source_to_rowset_oracle':'independent; no builder projection import','logical_seals':seals,'relation_status':'mapping_unresolved','cross_snapshot_merge_count':0}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--artifact',required=True);p.add_argument('--report',required=True);p.add_argument('--quest-talk-manifest',default='manifests/quest-talk-7.1/standalone-candidate.json');p.add_argument('--quest-talk-artifact',default='/tmp/genshin-lore-qt-7.1-final-b.sqlite');p.add_argument('--old-source',default='<explicit-source-required>/Documents/genshin-lore-database/cache/upstream-agd-git');a=p.parse_args();print(json.dumps(verify(a.source,a.artifact,a.report,a.quest_talk_manifest,a.quest_talk_artifact,a.old_source),indent=2))
