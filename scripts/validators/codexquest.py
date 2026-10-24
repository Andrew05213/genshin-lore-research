#!/usr/bin/env python3
"""Independent pinned-source oracle for CodexQuest 7.1 physical evidence."""
from __future__ import annotations
import argparse,hashlib,json,sqlite3,subprocess,sys
from collections import defaultdict,Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src'))
from genshin_lore.loading_tips_source import array_records,pairs,sha
from genshin_lore.sealed_sqlite import open_immutable_readonly
COMMIT='9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99';TREE='aa578c08c402af39b41f1ffc95124d10f5410272'
MAIN='ExcelBinOutput/MainQuestExcelConfigData.json';CORE_SOURCE='ExcelBinOutput/QuestCodexExcelConfigData.json'
TABLES=('codex_root','codex_child','codex_navigation','codex_candidate','codex_text_slot','codex_text_candidate')
OLD={
 'rc_493':('IMJHJGBNMMD','DCNPPIOLEOK','EBNBLBEIFFJ','PEAKPGNONFA','JOBGILDNLEL','BNJEGIAOKGM'),
 'legacy_7':('HCOCNINCBEI','EIGDJINAAME','HLCINEMBGEF','IIJIPINAOOF','LKNCCJIKPNP','LOFLLCHCMCG'),
 'alternate_3':('MHGNMKHOKHI','NCNGHCODLAK','MEGJPCLADOG','FCEMMMMDKLP','OKDLDDEBPOF','BKGOAJFIHCO')}
SIGNATURES={
 frozenset(('AFAPBOFJAIK','FBAFFJDIIFG','JIJKODHIEED','LFEGFBHFCBH','MFANMBMKKLC','NFFJLFOECKD','questType')):'new_with_type',
 frozenset(('AFAPBOFJAIK','FBAFFJDIIFG','JIJKODHIEED','LFEGFBHFCBH','MFANMBMKKLC','NFFJLFOECKD')):'new_without_type',
 frozenset(('AFAPBOFJAIK','FBAFFJDIIFG','LFEGFBHFCBH','MFANMBMKKLC','NFFJLFOECKD','questType')):'new_metadata_only',
 frozenset(('ALOHJMPDFKI','CBLIMBBBKNK','DCNPPIOLEOK','EBNBLBEIFFJ','HEDPNHPBMJH','IMJHJGBNMMD','NNPJABOAJPL')):'rc_493',
 frozenset(('EIGDJINAAME','HAAMAIAEBPH','HCOCNINCBEI','HLCINEMBGEF','IPENIGDFFAD','JNMOLCMMGHN','LDOJBIHMDAB')):'legacy_7',
 frozenset(('APEPAFOBDGB','EIFDMLOMFHF','HOBBPLPNMDP','MEGJPCLADOG','MHGNMKHOKHI','NCNGHCODLAK','PIAAIKBBCGB')):'alternate_3'}
def j(x):return json.dumps(x,sort_keys=True,ensure_ascii=True,separators=(',',':'))
def h(value):return hashlib.sha256(value).hexdigest()
def identity(*args):return 'cq71:'+h(j(args).encode())[:40]
def check(value,message):
 if not value:raise ValueError(message)
def compare(db,table,expected):
 actual=[tuple(r) for r in db.execute(f'SELECT * FROM {table} ORDER BY 1')]
 expected.sort()
 check(actual==expected,f'{table} source rowset differs actual={len(actual)} expected={len(expected)} first={next(((a[:5],b[:5]) for a,b in zip(actual,expected) if a!=b),None)}')
 return {'rows':len(actual),'sha256':h(b''.join((j([x.hex() if isinstance(x,bytes) else x for x in row])+'\n').encode() for row in actual))}
def schema_check(db):
 expected=sqlite3.connect(':memory:');expected.executescript((ROOT/'schemas/codexquest-7.1-v1.sql').read_text())
 for table in (*TABLES,'codex_release'):
  for op in ('INSERT','UPDATE','DELETE'):
   expected.execute(f"CREATE TRIGGER immutable_{table}_{op.lower()} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'immutable codexquest'); END")
 query="SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
 check([tuple(row) for row in db.execute(query)]==[tuple(row) for row in expected.execute(query)],'schema/index/trigger mismatch')
 expected.close()
def text_locators(value,pointer,old_spec):
 if isinstance(value,dict):
  if old_spec and set(value)=={old_spec[4],old_spec[5]} and value[old_spec[4]]:
   yield pointer or '/',str(value[old_spec[4]]),value[old_spec[5]],'historical_exact'
  if old_spec is None and value.get('DFKLAEHPOBE'):
   yield pointer or '/',str(value['DFKLAEHPOBE']),value.get('textId'),'new_wrapper_role_unproven'
  for field,child in value.items():yield from text_locators(child,pointer+'/'+field,old_spec)
 elif isinstance(value,list):
  for ordinal,child in enumerate(value):yield from text_locators(child,pointer+'/'+str(ordinal),old_spec)
def verify(source,artifact,report,core_manifest,core_artifact):
 source=Path(source);artifact=Path(artifact);manifest=json.loads(Path(report).read_text());release=manifest['release']
 check(subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()==[COMMIT,TREE],'source pin')
 check(release['source_commit']==COMMIT and release['source_tree']==TREE,'release pin')
 check(release['contract']=='codexquest-7.1-physical-text-foundation-v1' and release['status']=='physical_foundation_candidate' and release['product_accepted'] is False,'release authority boundary')
 check(release['child_raw_json_semantics']=='canonical parsed subtree; root raw_bytes retain authoritative lexical source','child projection honesty')
 check(artifact.stat().st_size==manifest['artifact']['size_bytes'] and sha(artifact)==manifest['artifact']['sha256'],'artifact seal')
 check(not any(Path(str(artifact)+s).exists() for s in ('-wal','-shm','-journal')),'SQLite sidecar')
 impl={p:sha(ROOT/p) for p in ('scripts/builders/codexquest.py','schemas/codexquest-7.1-v1.sql','src/genshin_lore/loading_tips_source.py')}
 check(impl==release['implementation'] and identity('parser',impl)==release['parser_build_id'],'parser/implementation')
 bindings=[x for x in json.loads(Path(core_manifest).read_text())['mappings'] if x.get('family')=='quest_codex_archive']
 check(len(bindings)==1 and bindings[0]['status']=='ACCEPTED_EXACT_7_1_MAPPING','QuestCodex accepted mapping')
 binding=bindings[0];dependency=release['quest_codex_binding']
 check(sha(core_manifest)==dependency['manifest_sha256'] and dependency['artifact']==binding['candidate_artifact'],'QuestCodex manifest')
 check(dependency['parser_build_id']==binding['parser_build_id'] and dependency['ingest_run_id']==binding['ingest_run_id'] and dependency['provider_contract']==binding['provider_contract'],'QuestCodex parser/run/contract')
 check(dependency['source_snapshot']==binding['source_snapshot']==release['snapshot'],'QuestCodex same-snapshot binding')
 check(Path(core_artifact).stat().st_size==binding['candidate_artifact']['size_bytes'] and sha(core_artifact)==binding['candidate_artifact']['sha256'],'QuestCodex artifact')
 check(binding['selected_primary_evidence_rows']==499 and binding['selected_support_evidence_rows']==142,'QuestCodex denominator')
 expected_files=[]
 for line in subprocess.check_output(['git','-C',str(source),'ls-tree','-rl',COMMIT,'--','BinOutput/CodexQuest'],text=True).splitlines():
  attrs,path=line.split('\t',1);_,kind,blob,size=attrs.split()
  check(kind=='blob' and path.endswith('.json'),'Codex inventory')
  expected_files.append((path,blob,int(size)))
 check(len(expected_files)==522,'522 root denominator')
 check(set(release['source_seals'])=={p for p,_,_ in expected_files}|{MAIN}|{f'TextMap/TextMap{tier}{lang}.json' for lang in ('CHS','EN','JP') for tier in ('','_Medium')},'source seal path set')
 for path,seal in release['source_seals'].items():
  local=source/path
  check(local.stat().st_size==seal['size'] and sha(local)==seal['sha256'],'source bytes '+path)
  check(subprocess.check_output(['git','-C',str(source),'rev-parse',COMMIT+':'+path],text=True).strip()==seal['blob'],'source Git blob '+path)
 check(identity('run',release['snapshot'],release['parser_build_id'],release['source_seals'],dependency)==release['ingest_run_id'],'run identity')
 db=open_immutable_readonly(artifact);schema_check(db)
 check(db.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and not db.execute('PRAGMA foreign_key_check').fetchall(),'database integrity')
 check(json.loads(db.execute('SELECT manifest_json FROM codex_release').fetchone()[0])==release,'release row')
 core=open_immutable_readonly(core_artifact)
 codex_targets=defaultdict(list)
 for oid,pointer,raw in core.execute("SELECT occurrence_id,source_pointer,raw_record_json FROM physical_row WHERE row_kind='primary'"):
  item=json.loads(raw);check(type(item.get('parentQuestId')) is int and type(item.get('id')) is int,'QuestCodex source row')
  codex_targets[item['parentQuestId']].append((CORE_SOURCE,pointer,str(item['id']),oid,binding['ingest_run_id']))
 check(sum(len(v) for v in codex_targets.values())==499,'QuestCodex primary target set')
 core.close()
 main_targets=defaultdict(list)
 for ordinal,item,_ in array_records(source/MAIN):
  check(type(item.get('id')) is int,'MainQuest source row')
  main_targets[item['id']].append((MAIN,f'/{ordinal}',str(item['id']),f'raw-mainquest:{ordinal}',release['snapshot']))
 roots=[];children=[];navs=[];candidates=[];owners=[];wanted=set();shapes=Counter();missing=0
 for path,blob,size in expected_files:
  raw=(source/path).read_bytes();check(len(raw)==size and hashlib.sha1(b'blob '+str(size).encode()+b'\0'+raw).hexdigest()==blob,'physical root Git blob '+path)
  value=json.loads(raw.decode('utf-8-sig'));shape=SIGNATURES.get(frozenset(value)) if isinstance(value,dict) else None
  check(shape is not None,'unknown root signature '+path)
  shapes[shape]+=1;spec=OLD.get(shape);id_field=spec[0] if spec else 'MFANMBMKKLC';native=value[id_field]
  check(type(native) is int,'root locator type '+path)
  raw_type=str(value[spec[1]]) if spec else str(value['questType']) if 'questType' in value else None
  list_field=spec[2] if spec else 'JIJKODHIEED';sections=value.get(list_field)
  check(isinstance(sections,list) or (sections is None and shape=='new_metadata_only'),'section field shape '+path)
  rid=identity('root',release['ingest_run_id'],path)
  roots.append((rid,release['ingest_run_id'],path,blob,h(raw),raw,shape,str(native),raw_type,'absent_in_source' if sections is None else 'present',0 if sections is None else len(sections),'historical_physical' if spec else 'new_shape_unresolved'))
  for index,section in enumerate(sections or []):
   check(isinstance(section,dict),'section object '+path)
   section_pointer=f'/{list_field}/{index}';serialized=j(section)
   children.append((identity('child',rid,section_pointer),rid,'section_like',section_pointer,index,serialized,h(serialized.encode())))
   node_field=spec[3] if spec else 'DCBDMJAPBOK';nodes=section.get(node_field)
   if nodes is None and spec is None:continue
   check(isinstance(nodes,list),'node array '+path+section_pointer)
   for node_index,node in enumerate(nodes):
    node_pointer=f'{section_pointer}/{node_field}/{node_index}';serialized=j(node)
    children.append((identity('child',rid,node_pointer),rid,'node_like',node_pointer,node_index,serialized,h(serialized.encode())))
  for role,targets,complete in (('mainquest_equality',main_targets[native],0),('questcodex_reverse_equality',codex_targets[native],1)):
   nid=identity('navigation',rid,role)
   state='mapping_unresolved' if not complete else 'missing' if not targets else 'resolved_unique' if len(targets)==1 else 'resolved_candidate_set'
   navs.append((nid,rid,role,'/'+id_field,str(native),state,len(targets),complete))
   for target_path,pointer,target_native,target_oid,target_run in targets:
    candidates.append((identity('candidate',nid,target_path,pointer,target_oid),nid,target_path,pointer,target_native,target_oid,target_run))
  for pointer,label,raw_hash,owner_state in text_locators(value,'',spec):
   check(raw_hash is None or isinstance(raw_hash,(str,int)),'locator type '+path+pointer)
   if raw_hash is None:missing+=1
   else:wanted.add(str(raw_hash))
   owners.append((rid,pointer,label,None if raw_hash is None else str(raw_hash),owner_state))
 core_status=Counter(row[5] for row in navs if row[2]=='questcodex_reverse_equality')
 check(core_status=={'resolved_unique':509,'resolved_candidate_set':3,'missing':10},'QuestCodex physical candidate distribution')
 seals={'codex_root':compare(db,'codex_root',roots),'codex_child':compare(db,'codex_child',children),'codex_navigation':compare(db,'codex_navigation',navs),'codex_candidate':compare(db,'codex_candidate',candidates)}
 lookup={}
 for lang in ('CHS','EN','JP'):
  for medium in (False,True):
   path=f"TextMap/TextMap{'_Medium' if medium else ''}{lang}.json";by_key=defaultdict(list)
   for position,(number,text) in enumerate(pairs(source/path)):
    if number in wanted:by_key[number].append((path,position,'/'+number,text))
   lookup[(lang,medium)]=by_key
 slots=[];texts=[]
 for root_id,pointer,label,raw_hash,owner_state in owners:
  for lang in ('CHS','EN','JP'):
   matches=[] if raw_hash is None else (lookup[(lang,False)].get(raw_hash,[]) or lookup[(lang,True)].get(raw_hash,[]))
   sid=identity('slot',root_id,pointer,lang)
   status='mapping_unresolved' if raw_hash is None else 'missing' if not matches else 'resolved_unique' if len(matches)==1 else 'resolved_candidate_set'
   slots.append((sid,root_id,pointer,label,raw_hash or '<missing>',lang,status,len(matches),owner_state))
   for source_path,pair_ordinal,source_pointer,text in matches:
    texts.append((identity('text',sid,source_path,pair_ordinal,source_pointer),sid,source_path,pair_ordinal,source_pointer,text,h(text.encode())))
 seals['codex_text_slot']=compare(db,'codex_text_slot',slots);seals['codex_text_candidate']=compare(db,'codex_text_candidate',texts)
 check(release['counts']=={table:seal['rows'] for table,seal in seals.items()},'release count seal')
 check(release['shape_counts']==dict(shapes) and release['missing_text_id_wrappers']==missing,'shape/unknown wrapper counts')
 db.close()
 return {'verdict':'PASS_PHYSICAL_PROJECTION','artifact':manifest['artifact'],'counts':release['counts'],'shape_counts':dict(shapes),'questcodex_candidate_status':dict(core_status),'semantic_successor':'NOT_PROVEN for 499 new roots','independent_source_to_rowset_oracle':True,'logical_seals':seals,'wrong_identity_merges':0}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--artifact',required=True);p.add_argument('--report',required=True);p.add_argument('--core-manifest',default='manifests/special-core-7.1/accepted-exact-mappings-v1.json');p.add_argument('--core-artifact',default='/tmp/special-core-7.1-historical-ab-v1/quest_codex_archive-a.sqlite');a=p.parse_args();print(json.dumps(verify(a.source,a.artifact,a.report,a.core_manifest,a.core_artifact),indent=2))
