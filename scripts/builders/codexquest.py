#!/usr/bin/env python3
"""Full 522-root 7.1 CodexQuest physical projection with bounded raw navigation."""
from __future__ import annotations
import argparse,hashlib,json,sqlite3,subprocess,sys
from collections import Counter,defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src'))
from genshin_lore.loading_tips_source import array_records,pairs,sha
COMMIT='9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99';TREE='aa578c08c402af39b41f1ffc95124d10f5410272';SNAPSHOT='anime-game-data:7.1:'+COMMIT
PREFIX='BinOutput/CodexQuest';MAIN='ExcelBinOutput/MainQuestExcelConfigData.json';CODEX='ExcelBinOutput/QuestCodexExcelConfigData.json'
CORE='manifests/special-core-7.1/accepted-exact-mappings-v1.json';CORE_ARTIFACT='/tmp/special-core-7.1-historical-ab-v1/quest_codex_archive-a.sqlite'
OLD={
 'rc_493':('IMJHJGBNMMD','DCNPPIOLEOK','EBNBLBEIFFJ','PEAKPGNONFA','JOBGILDNLEL','BNJEGIAOKGM'),
 'legacy_7':('HCOCNINCBEI','EIGDJINAAME','HLCINEMBGEF','IIJIPINAOOF','LKNCCJIKPNP','LOFLLCHCMCG'),
 'alternate_3':('MHGNMKHOKHI','NCNGHCODLAK','MEGJPCLADOG','FCEMMMMDKLP','OKDLDDEBPOF','BKGOAJFIHCO'),
}
SIGNATURES={
 ('AFAPBOFJAIK','FBAFFJDIIFG','JIJKODHIEED','LFEGFBHFCBH','MFANMBMKKLC','NFFJLFOECKD','questType'):'new_with_type',
 ('AFAPBOFJAIK','FBAFFJDIIFG','JIJKODHIEED','LFEGFBHFCBH','MFANMBMKKLC','NFFJLFOECKD'):'new_without_type',
 ('AFAPBOFJAIK','FBAFFJDIIFG','LFEGFBHFCBH','MFANMBMKKLC','NFFJLFOECKD','questType'):'new_metadata_only',
 ('ALOHJMPDFKI','CBLIMBBBKNK','DCNPPIOLEOK','EBNBLBEIFFJ','HEDPNHPBMJH','IMJHJGBNMMD','NNPJABOAJPL'):'rc_493',
 ('EIGDJINAAME','HAAMAIAEBPH','HCOCNINCBEI','HLCINEMBGEF','IPENIGDFFAD','JNMOLCMMGHN','LDOJBIHMDAB'):'legacy_7',
 ('APEPAFOBDGB','EIFDMLOMFHF','HOBBPLPNMDP','MEGJPCLADOG','MHGNMKHOKHI','NCNGHCODLAK','PIAAIKBBCGB'):'alternate_3',
}
TABLES=('codex_root','codex_child','codex_navigation','codex_candidate','codex_text_slot','codex_text_candidate')
# Bound the private writer's page cache; this is not a whole-process RAM limit.
WRITE_CACHE_KIB=128*1024
def canonical(v):return json.dumps(v,sort_keys=True,ensure_ascii=True,separators=(',',':'))
def hash_bytes(v):return hashlib.sha256(v).hexdigest()
def ident(*parts):return 'cq71:'+hash_bytes(canonical(parts).encode())[:40]
def inventory(source, paths=(PREFIX,)):
 result=[]
 for line in subprocess.check_output(['git','-C',str(source),'ls-tree','-rl',COMMIT,'--',*paths],text=True).splitlines():
  left,path=line.split('\t',1);parts=left.split()
  if len(parts)!=4 or parts[1]!='blob' or not path.endswith('.json'):raise ValueError('CodexQuest inventory object')
  result.append((path,parts[2],int(parts[3])))
 return sorted(result)
def core_binding(path,artifact):
 manifest=json.loads(Path(path).read_text());entries=[x for x in manifest.get('components',manifest.get('mappings',[])) if x.get('family')=='quest_codex_archive']
 if len(entries)!=1:raise ValueError('accepted QuestCodex mapping missing')
 entry=entries[0]
 if entry['status']!='ACCEPTED_EXACT_7_1_MAPPING' or entry['source_snapshot']!=SNAPSHOT or entry['selected_primary_evidence_rows']!=499 or entry['selected_support_evidence_rows']!=142:raise ValueError('QuestCodex exact scope')
 if Path(artifact).stat().st_size!=entry['candidate_artifact']['size_bytes'] or sha(artifact)!=entry['candidate_artifact']['sha256']:raise ValueError('QuestCodex artifact seal')
 db=sqlite3.connect(Path(artifact).absolute().as_uri()+'?mode=ro&immutable=1',uri=True)
 by_parent=defaultdict(list)
 for oid,pointer,raw in db.execute("SELECT occurrence_id,source_pointer,raw_record_json FROM physical_row WHERE row_kind='primary' ORDER BY source_pointer,occurrence_id"):
  record=json.loads(raw);parent=record.get('parentQuestId')
  if type(parent) is not int or type(record.get('id')) is not int:raise ValueError('QuestCodex primary row shape')
  by_parent[parent].append((CODEX,pointer,str(record['id']),oid,entry['ingest_run_id']))
 if sum(map(len,by_parent.values()))!=499:raise ValueError('QuestCodex primary denominator')
 db.close()
 return {'manifest_sha256':sha(path),'artifact':entry['candidate_artifact'],'source_snapshot':entry['source_snapshot'],'parser_build_id':entry['parser_build_id'],'ingest_run_id':entry['ingest_run_id'],'provider_contract':entry['provider_contract'],'primary_rows':499,'chapter_support_rows':142},by_parent
def wrappers(value,pointer,old=None):
 if isinstance(value,dict):
  if old and set(value)=={old[4],old[5]}:
   label=value[old[4]]
   if label:yield pointer or '/',str(label),value[old[5]],'historical_exact'
  elif old is None and 'DFKLAEHPOBE' in value:
   label=value['DFKLAEHPOBE']
   if label:yield pointer or '/',str(label),value.get('textId'),'new_wrapper_role_unproven'
  for field,nested in value.items():yield from wrappers(nested,pointer+'/'+field,old)
 elif isinstance(value,list):
  for ordinal,nested in enumerate(value):yield from wrappers(nested,pointer+'/'+str(ordinal),old)
def immutable(db):
 for table in (*TABLES,'codex_release'):
  for op in ('INSERT','UPDATE','DELETE'):
   db.execute(f"CREATE TRIGGER immutable_{table}_{op.lower()} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'immutable codexquest'); END")
def build(source,output,core_manifest,core_artifact):
 source=Path(source);output=Path(output)
 if subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()!=[COMMIT,TREE]:raise ValueError('7.1 source pin')
 files=inventory(source)
 if len(files)!=522:raise ValueError('CodexQuest root denominator')
 core,quest_codex_targets=core_binding(core_manifest,core_artifact)
 main_by_id=defaultdict(list)
 for ordinal,record,_ in array_records(source/MAIN):
  if type(record.get('id')) is not int:raise ValueError('MainQuest lookup shape')
  main_by_id[record['id']].append((MAIN,f'/{ordinal}',str(record['id']),f'raw-mainquest:{ordinal}',SNAPSHOT))
 source_seals={};raw_files={};text_paths=[f'TextMap/TextMap{tier}{language}.json' for language in ('CHS','EN','JP') for tier in ('','_Medium')]
 for path,blob,size in files:
  raw=(source/path).read_bytes()
  pinned=hashlib.sha1(b'blob '+str(size).encode()+b'\0');pinned.update(raw)
  if len(raw)!=size or pinned.hexdigest()!=blob:raise ValueError('source drift '+path)
  source_seals[path]={'size':size,'sha256':hash_bytes(raw),'blob':blob}
  raw_files[path]=raw
 support=inventory(source,(MAIN,*text_paths))
 if {path for path,_,_ in support}!={MAIN,*text_paths} or len(support)!=7:raise ValueError('support source path set')
 for path,blob,size in support:
  raw=(source/path).read_bytes()
  pinned=hashlib.sha1(b'blob '+str(size).encode()+b'\0');pinned.update(raw)
  if len(raw)!=size or pinned.hexdigest()!=blob:raise ValueError('source drift '+path)
  source_seals[path]={'size':size,'sha256':hash_bytes(raw),'blob':blob}
 impl={p:sha(ROOT/p) for p in ('scripts/builders/codexquest.py','schemas/codexquest-7.1-v1.sql','src/genshin_lore/loading_tips_source.py')}
 parser=ident('parser',impl);run=ident('run',SNAPSHOT,parser,source_seals,core)
 roots=[];children=[];navigations=[];candidates=[];owners=[];wanted=set();shapes=Counter();missing_wrapper=0
 for path,blob,size in files:
  # Project the same bytes already checked against the pinned Git tree. The
  # roots below retain these raw objects anyway, so this adds no second body copy.
  raw=raw_files.pop(path);value=json.loads(raw.decode('utf-8-sig'))
  if not isinstance(value,dict):raise ValueError('Codex root not object '+path)
  signature=tuple(sorted(value));shape=SIGNATURES.get(signature)
  if shape is None:raise ValueError('unknown 7.1 Codex root signature '+path)
  shapes[shape]+=1;old=OLD.get(shape)
  native=value[old[0]] if old else value['MFANMBMKKLC']
  if type(native) is not int:raise ValueError('Codex native locator type '+path)
  quest_type=str(value[old[1]]) if old else None if 'questType' not in value else str(value['questType'])
  array_key=old[2] if old else 'JIJKODHIEED';section_values=value.get(array_key)
  if section_values is None and shape!='new_metadata_only':raise ValueError('missing section field '+path)
  if section_values is not None and not isinstance(section_values,list):raise ValueError('section array shape '+path)
  cid=ident('root',run,path);state='absent_in_source' if section_values is None else 'present'
  roots.append((cid,run,path,blob,hash_bytes(raw),raw,shape,str(native),quest_type,state,0 if section_values is None else len(section_values),'historical_physical' if old else 'new_shape_unresolved'))
  for section_ordinal,section in enumerate(section_values or []):
   if not isinstance(section,dict):raise ValueError('section object '+path)
   section_ptr=f'/{array_key}/{section_ordinal}';section_raw=canonical(section)
   children.append((ident('child',cid,section_ptr),cid,'section_like',section_ptr,section_ordinal,section_raw,hash_bytes(section_raw.encode())))
   node_key=old[3] if old else 'DCBDMJAPBOK';nodes=section.get(node_key)
   if nodes is None and not old:continue
   if not isinstance(nodes,list):raise ValueError('node array shape '+path+section_ptr)
   for node_ordinal,node in enumerate(nodes):
    pointer=f'{section_ptr}/{node_key}/{node_ordinal}';node_raw=canonical(node)
    children.append((ident('child',cid,pointer),cid,'node_like',pointer,node_ordinal,node_raw,hash_bytes(node_raw.encode())))
  source_id_pointer='/'+(old[0] if old else 'MFANMBMKKLC')
  for kind,matches,complete in (('mainquest_equality',main_by_id[native],0),('questcodex_reverse_equality',quest_codex_targets[native],1)):
   nid=ident('navigation',cid,kind)
   status='mapping_unresolved' if not complete else 'missing' if not matches else 'resolved_unique' if len(matches)==1 else 'resolved_candidate_set'
   navigations.append((nid,cid,kind,source_id_pointer,str(native),status,len(matches),complete))
   for target_path,target_pointer,target_native,target_occurrence,target_run in matches:
    candidates.append((ident('candidate',nid,target_path,target_pointer,target_occurrence),nid,target_path,target_pointer,target_native,target_occurrence,target_run))
  for pointer,label,locator,semantic in wrappers(value,'',old):
   if locator is not None and not isinstance(locator,(str,int)):raise ValueError('text locator type '+path+pointer)
   if locator is None:missing_wrapper+=1
   else:wanted.add(str(locator))
   owners.append((cid,pointer,label,None if locator is None else str(locator),semantic))
 # TextMap is strictly snapshot-local; main precedes Medium but physical duplicates stay candidates.
 maps={}
 for language in ('CHS','EN','JP'):
  for tier in ('','_Medium'):
   path=f'TextMap/TextMap{tier}{language}.json';hits=defaultdict(list)
   for ordinal,(raw_hash,text) in enumerate(pairs(source/path)):
    if raw_hash in wanted:hits[raw_hash].append((path,ordinal,'/'+raw_hash,text))
   maps[language,tier]=hits
 slots=[];text_candidates=[]
 for cid,pointer,label,locator,semantic in owners:
  for language in ('CHS','EN','JP'):
   hits=[] if locator is None else (maps[language,''].get(locator,[]) or maps[language,'_Medium'].get(locator,[]))
   sid=ident('slot',cid,pointer,language)
   status='mapping_unresolved' if locator is None else 'missing' if not hits else 'resolved_unique' if len(hits)==1 else 'resolved_candidate_set'
   slots.append((sid,cid,pointer,label,locator or '<missing>',language,status,len(hits),semantic))
   for target_path,ordinal,text_pointer,text in hits:
    text_candidates.append((ident('text',sid,target_path,ordinal,text_pointer),sid,target_path,ordinal,text_pointer,text,hash_bytes(text.encode())))
 data=(roots,children,navigations,candidates,slots,text_candidates)
 release={'contract':'codexquest-7.1-physical-text-foundation-v1','status':'physical_foundation_candidate','snapshot':SNAPSHOT,'source_commit':COMMIT,'source_tree':TREE,'source_seals':source_seals,'implementation':impl,'parser_build_id':parser,'ingest_run_id':run,'quest_codex_binding':core,'counts':{table:len(rows) for table,rows in zip(TABLES,data)},'shape_counts':dict(shapes),'missing_text_id_wrappers':missing_wrapper,'new_shape_semantic_section_node_mapping':'unresolved; physical section_like/node_like only','child_raw_json_semantics':'canonical parsed subtree; root raw_bytes retain authoritative lexical source','quest_codex_navigation':'reverse physical candidates; not logical relation','product_accepted':False}
 if output.exists():raise FileExistsError(output)
 output.parent.mkdir(parents=True,exist_ok=True);db=sqlite3.connect(output);db.execute('PRAGMA page_size=4096');db.execute('PRAGMA journal_mode=OFF');db.execute('PRAGMA foreign_keys=ON')
 db.execute(f'PRAGMA cache_size=-{WRITE_CACHE_KIB}')
 try:
  db.executescript((ROOT/'schemas/codexquest-7.1-v1.sql').read_text())
  for table,rows in zip(TABLES,data):
   if rows:db.executemany(f"INSERT INTO {table} VALUES ({','.join('?'*len(rows[0]))})",rows)
  db.execute('INSERT INTO codex_release VALUES(1,?)',(canonical(release),));immutable(db);db.commit();db.execute('VACUUM');db.close()
 except BaseException:
  db.close();raise
 return {'release':release,'artifact':{'size_bytes':output.stat().st_size,'sha256':sha(output)}}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--output',required=True);p.add_argument('--core-manifest',default=CORE);p.add_argument('--core-artifact',default=CORE_ARTIFACT);p.add_argument('--report');a=p.parse_args();result=build(a.source,a.output,a.core_manifest,a.core_artifact)
 if a.report:
  path=Path(a.report)
  if path.exists():raise FileExistsError(path)
  path.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
 print(json.dumps(result['release']['counts']))
