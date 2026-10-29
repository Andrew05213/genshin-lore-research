#!/usr/bin/env python3
"""Freeze the Book / MusicGame native archive delta and exact PushTips targets."""
import argparse,json,sqlite3,subprocess
from pathlib import Path
from genshin_lore.event_7_1_source import COMMIT,TREE,SNAPSHOT,array_records,git_blob,sha
ROOT=Path(__file__).resolve().parents[2]
def seal(root,path):
 p=root/path;blob=subprocess.check_output(['git','-C',str(root),'rev-parse',COMMIT+':'+path],text=True).strip()
 if git_blob(p)!=blob:raise ValueError('actual source drift '+path)
 return {'path':path,'size_bytes':p.stat().st_size,'sha256':sha(p),'blob':blob}
def artifact(path):
 p=Path(path).resolve();return {'path':str(p),'size_bytes':p.stat().st_size,'sha256':sha(p)}
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--message-runtime',type=Path,default=ROOT/'manifests/native-archive-7.1/message-runtime-v1.json');p.add_argument('--source',type=Path,default=ROOT/'cache/upstream-agd-git');p.add_argument('--message-artifact',type=Path,required=True);p.add_argument('--output',type=Path,default=ROOT/'manifests/native-archive-7.1/book-music-scope-v1.json');a=p.parse_args(argv)
 sp=ROOT/'manifests/native-archive-7.1/book-music-selection-v1.json';selection=json.loads(sp.read_text())
 if subprocess.check_output(['git','-C',str(a.source),'rev-parse','HEAD'],text=True).strip()!=COMMIT or subprocess.check_output(['git','-C',str(a.source),'rev-parse','HEAD^{tree}'],text=True).strip()!=TREE:raise ValueError('pinned source mismatch')
 if subprocess.check_output(['git','-C',str(a.source),'status','--porcelain'],text=True).strip():raise ValueError('source checkout dirty')
 paths=[x['path'] for x in selection['sources']]+[v for x in selection['textmaps'].values() for v in (x['main'],x['medium'])];files=[seal(a.source,x) for x in paths]
 mr=json.loads(a.message_runtime.read_text());ma=artifact(a.message_artifact)
 if ma['size_bytes']!=mr['artifact']['size_bytes'] or ma['sha256']!=mr['artifact']['sha256']:raise ValueError('message artifact authority')
 candidates=[];c=sqlite3.connect(a.message_artifact);c.row_factory=sqlite3.Row
 values=set()
 for spec in selection['sources']:
  rows=[row for _,row,_ in array_records(a.source/spec['path'])]
  if not all(isinstance(row,dict) for row in rows):raise ValueError('source row must be object')
  for ordinal,row in enumerate(rows):
   key=spec.get('native_id_field')
   if key and key not in row and not (spec['path'].endswith('/MusicGameBookAreaExcelConfigData.json') and ordinal==0):raise ValueError(f'missing native key {spec["path"]}/{ordinal}/{key}')
   if key in row and type(row[key]) is not int:raise ValueError(f'invalid native key {spec["path"]}/{ordinal}/{key}')
   for owner in spec['text_owners']:
    field=owner['field']
    if type(row.get(field)) is not int:raise ValueError(f'invalid text owner {spec["path"]}/{ordinal}/{field}')
  for rel in spec.get('relations',[]):
   if rel['relation_kind']!='source_authored_push_tips_reference':raise ValueError('unsupported relation')
   if any(rel['field'] in x and type(x[rel['field']]) is not int for x in rows):raise ValueError('invalid relation locator')
   values|={str(x[rel['field']]) for x in rows if rel['field'] in x}
 for value in sorted(values):
  for row in c.execute("SELECT id,source_path,pointer,native_id,raw_sha256,run_id FROM native71_row WHERE source_path='ExcelBinOutput/PushTipsConfigData.json' AND native_id=? ORDER BY source_path,pointer,id",(value,)):
   if row['run_id']!=mr['release']['run_id']:raise ValueError('message target run drift')
   candidates.append({'relation_kind':'source_authored_push_tips_reference','raw_locator':value,'target_source_path':row['source_path'],'target_source_pointer':row['pointer'],'target_native_id':row['native_id'],'target_raw_sha256':row['raw_sha256'],'target_family':'native_archive','target_contract':mr['release']['contract'],'target_parser':mr['release']['parser_build_id'],'target_run':mr['release']['run_id'],'target_occurrence_id':row['id']})
 c.close();scope={'contract':'native-book-music-7.1-scope-v1','family':'native_archive','family_contract_version':selection['family_contract_version'],'snapshot_id':SNAPSHOT,'source_commit':COMMIT,'source_tree':TREE,'selection_manifest':'manifests/native-archive-7.1/book-music-selection-v1.json','selection_sha256':sha(sp),'sources':selection['sources'],'languages':selection['languages'],'textmaps':selection['textmaps'],'files':files,'reuse_targets':[],'external_candidates':candidates,'dependencies':{'native_message':{'artifact':ma,'family':'native_archive','family_contract_version':mr['release']['contract'],'parser_build_id':mr['release']['parser_build_id'],'run_id':mr['release']['run_id']}},'completeness':{'declared_physical_projection_complete':True,'declared_text_projection_complete':True,'declared_relation_projection_complete':True,'full_7_1':False}}
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(scope,indent=2,sort_keys=True)+'\n');print(json.dumps({'sources':len(selection['sources']),'files':len(files),'candidates':len(candidates)}))
if __name__=='__main__':main()
