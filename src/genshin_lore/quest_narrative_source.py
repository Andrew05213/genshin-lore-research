"""Pinned Quest narrative projection; physical coordinates remain authoritative."""
from __future__ import annotations
import hashlib,json,sqlite3,subprocess
from collections import defaultdict
from pathlib import Path
from .loading_tips_source import pairs

COMMIT='26df1dfbdf05a82bbb1d97506859f3e1c40718d8'
TREE='d891ef1d95fdd6c27a1da8a32ea7a61b483647a6'
SNAPSHOT='anime-game-data:7.0:'+COMMIT
FAMILY='quest_narrative_archive';CONTRACT='quest-narrative-evidence-v1'
MAIN='ExcelBinOutput/MainQuestExcelConfigData.json';STEP='ExcelBinOutput/QuestExcelConfigData.json';FMT='ExcelBinOutput/MainQuestFmtQuestRelateExcelConfigData.json'
LANGS=('CHS','EN','JP');TEXT_FIELDS={MAIN:('titleTextMapHash','descTextMapHash','unlockDescTextMapHash'),STEP:('stepDescTextMapHash','guideTipsTextMapHash')}
QUEST_SHAPES={'rc':('OIFGMOHKPOI','EBNBLBEIFFJ','KCGAKLCHDCC','EMNMIOBCCLL'),'alt':('NFIEHACCECI','MEGJPCLADOG',None,None)}

def canonical(x):return json.dumps(x,sort_keys=True,ensure_ascii=True,separators=(',',':'))
def sha_bytes(x):return hashlib.sha256(x).hexdigest()
def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def ident(*x):return 'quest-narrative:'+hashlib.sha256(canonical(x).encode()).hexdigest()[:40]
def load(path):return json.loads(Path(path).read_bytes().decode('utf-8-sig'))
def git_inventory(root,prefix):
 lines=subprocess.check_output(['git','-C',str(root),'ls-tree','-r',COMMIT,'--',prefix],text=True).splitlines()
 return {x.split('\t',1)[1]:x.split('\t',1)[0].split()[2] for x in lines}
def file_blob(root,path):return subprocess.check_output(['git','-C',str(root),'rev-parse',COMMIT+':'+path],text=True).strip()
def file_seal(root,path):
 p=Path(root)/path;raw=p.read_bytes();blob=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
 if blob!=file_blob(root,path):raise ValueError('source file drift '+path)
 return {'path':path,'size_bytes':len(raw),'sha256':sha_bytes(raw),'blob':blob}

def accepted_v7(artifact,manifest_path):
 m=load(manifest_path)
 evidence=(Path(manifest_path).parent/m['evidence_manifest']['path']).resolve();em=load(evidence)
 if m['family_contract_version']!='quest-talk-evidence-v7' or not m.get('retrieval_only') or not em['product_accepted'] or sha(evidence)!=m['evidence_manifest']['sha256']:raise ValueError('accepted v7 required')
 if Path(artifact).stat().st_size!=m['artifact']['size_bytes'] or sha(artifact)!=m['artifact']['sha256']:raise ValueError('v7 artifact seal')
 c=sqlite3.connect(Path(artifact).resolve().as_uri()+'?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
 roots={};refs=defaultdict(list)
 q="""select p.id,sf.relative_path,p.native_id,sr.raw_sha256 from quest_talk_physical_container p join source_record sr on sr.id=p.source_record_id join source_file sf on sf.id=sr.source_file_id where p.kind='quest_root' order by sf.relative_path"""
 for x in c.execute(q):roots[x['relative_path']]=dict(x)
 q="""select r.*,r.pointer as source_pointer,r.native_id as raw_native_id,sf.relative_path from quest_talk_physical_reference r join quest_talk_physical_container p on p.id=r.container_id join source_record sr on sr.id=p.source_record_id join source_file sf on sf.id=sr.source_file_id where p.kind='quest_root' order by sf.relative_path,r.pointer"""
 for x in c.execute(q):refs[x['relative_path']].append(dict(x))
 return m,c,roots,refs

def scan(root,artifact,manifest_path):
 root=Path(root);pins=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()
 if pins!=[COMMIT,TREE]:raise ValueError('source pin')
 m,c,accepted_roots,accepted_refs=accepted_v7(artifact,manifest_path);quest_blobs=git_inventory(root,'BinOutput/Quest')
 main=load(root/MAIN);steps=load(root/STEP);fmt=load(root/FMT)
 if tuple(map(len,(main,steps,fmt)))!=(4372,17814,3780):raise ValueError('Excel denominator')
 containers=[];children=[];relations=[];candidates=[];slots=[];texts=[];by_main=defaultdict(list);by_step={};by_child=defaultdict(list)
 def add_container(kind,path,ordinal,pointer,native,raw,blob,status='resolved',target=None):
  cid=ident('container',path,pointer);containers.append({'id':cid,'kind':kind,'source_path':path,'source_ordinal':ordinal,'source_pointer':pointer,'native_id':None if native is None else str(native),'source_blob_oid':blob,'raw_sha256':sha_bytes(raw.encode()),'raw_json':raw,'mapping_status':status,'target':target});return cid
 main_blob=file_blob(root,MAIN);step_blob=file_blob(root,STEP);fmt_blob=file_blob(root,FMT)
 for i,x in enumerate(main):
  raw=canonical(x);cid=add_container('main_quest_row',MAIN,i,f'/{i}',x['id'],raw,main_blob);by_main[x['id']].append((cid,f'/{i}'))
 for i,x in enumerate(steps):
  raw=canonical(x);cid=add_container('quest_step_row',STEP,i,f'/{i}',x['subId'],raw,step_blob);by_step[x['subId']]=(cid,f'/{i}')
 for i,x in enumerate(fmt):add_container('format_relation_config',FMT,i,f'/{i}',x['mainQuestID'],canonical(x),fmt_blob,'opaque_fields_preserved')
 for path in sorted(quest_blobs):
  raw=(root/path).read_bytes();doc=json.loads(raw.decode('utf-8-sig'));shape='rc' if 'EBNBLBEIFFJ' in doc else 'alt' if 'MEGJPCLADOG' in doc else None
  if shape is None or path not in accepted_roots:raise ValueError('Quest shape/reuse '+path)
  idkey,listkey,subkey,orderkey=QUEST_SHAPES[shape];native=doc.get(idkey);a=accepted_roots[path]
  if str(native)!=str(a['native_id']) or sha_bytes(raw)!=a['raw_sha256']:raise ValueError('accepted Quest mismatch '+path)
  target={'family':'quest_talk_pilot','contract':'quest-talk-evidence-v7','run':m['ingest_run_id'],'occurrence_id':a['id']}
  cid=add_container('quest_root_reuse',path,None,'/',native,canonical({'accepted_raw_sha256':a['raw_sha256']}),quest_blobs[path],'resolved',target);by_main[native].append((cid,'/'))
  for i,ch in enumerate(doc[listkey]):
   ptr=f'/{listkey}/{i}';sub=ch.get(subkey) if subkey else None;order=ch.get(orderkey) if orderkey else None;target_step=by_step.get(sub)
   child={'id':ident('child',path,ptr),'container_id':cid,'source_pointer':ptr,'shape':shape,'ordinal':i,'native_sub_id':None if sub is None else str(sub),'source_order':order,'mapping_status':'resolved_declared_fields' if shape=='rc' else 'mapping_unresolved','raw_sha256':sha_bytes(canonical(ch).encode()),'target_step_container_id':target_step[0] if target_step else None};children.append(child)
   if sub is not None:by_child[sub].append((child['id'],path,ptr))
 def relation(container,kind,pointer,value,namespace,hits,complete=1,reused=None,status=None):
  status=status or ({0:'missing',1:'resolved_unique'}.get(len(hits),'ambiguous'))
  rid=ident('relation',container,kind,pointer);relations.append({'id':rid,'container_id':container,'kind':kind,'source_pointer':pointer,'raw_locator_json':canonical(value),'candidate_namespace':namespace,'status':status,'candidate_count':len(hits),'search_complete':complete,'reused_relation_occurrence_id':reused})
  for h in hits:
   candidates.append({'id':ident('candidate',rid,h['path'],h['pointer'],h['run'],h['occurrence']),'relation_id':rid,'target_source_path':h['path'],'target_source_pointer':h['pointer'],'target_native_id':None if h.get('native') is None else str(h['native']),'target_family':h['family'],'target_contract':h['contract'],'target_run':h['run'],'target_occurrence_id':h['occurrence']})
 for child in children:
  if child['shape']!='rc':continue
  sub=int(child['native_sub_id']);target=by_step.get(sub);hits=[] if target is None else [{'path':STEP,'pointer':target[1],'native':sub,'family':FAMILY,'contract':CONTRACT,'run':'SELF','occurrence':target[0]}]
  relation(child['container_id'],'physical_child_step_lookup',child['source_pointer']+'/KCGAKLCHDCC',sub,'all QuestExcel rows; shape-mapped KCGAKLCHDCC to explicit subId',hits)
 # MainQuest rows bind to every distinct physical Quest root with that explicit source ID.
 quest_containers={x['source_path']:x for x in containers if x['kind']=='quest_root_reuse'}
 for i,x in enumerate(main):
  cid=ident('container',MAIN,f'/{i}');hits=[]
  for q in sorted((v for v in quest_containers.values() if v['native_id']==str(x['id'])),key=lambda z:z['source_path']):
   hits.append({'path':q['source_path'],'pointer':'/','native':x['id'],'family':FAMILY,'contract':CONTRACT,'run':'SELF','occurrence':q['id']})
  relation(cid,'mainquest_physical_root',f'/{i}/id',x['id'],'all 4,466 BinOutput/Quest physical roots; shape-specific explicit root ID',hits)
 for i,x in enumerate(steps):
  cid=ident('container',STEP,f'/{i}');mh=by_main[x['mainId']][0]
  relation(cid,'step_mainquest',f'/{i}/mainId',x['mainId'],'all MainQuestExcel rows; explicit mainId to id',[{'path':MAIN,'pointer':mh[1],'native':x['mainId'],'family':FAMILY,'contract':CONTRACT,'run':'SELF','occurrence':mh[0]}])
  hs=[{'path':p,'pointer':ptr,'native':x['subId'],'family':FAMILY,'contract':CONTRACT,'run':'SELF','occurrence':chid} for chid,p,ptr in by_child[x['subId']]]
  relation(cid,'step_physical_child',f'/{i}/subId',x['subId'],'all mapped rc Quest child records; explicit subId to shape-mapped KCGAKLCHDCC',hs)
 # Exact reuse of source-authored Quest->Talk observations and v7 candidates.
 for path,rows in accepted_refs.items():
  cid=quest_containers[path]['id']
  for r in rows:
   hs=[]
   for h in json.loads(r['candidates_json']):hs.append({'path':h['path'],'pointer':h['pointer'],'native':r['raw_native_id'],'family':'quest_talk_pilot','contract':'quest-talk-evidence-v7','run':m['ingest_run_id'],'occurrence':h['container_id']})
   relation(cid,'quest_talk_reference',r['source_pointer'],r['raw_native_id'],'frozen v7 physical Talk candidate set; semantic search remains incomplete',hs,0,r['id'],'resolved_candidate_set' if hs else 'mapping_unresolved')
 for i,x in enumerate(fmt):
  cid=ident('container',FMT,f'/{i}');mh=by_main[x['mainQuestID']][0]
  relation(cid,'format_config_owner',f'/{i}/mainQuestID',x['mainQuestID'],'all MainQuestExcel rows; explicit mainQuestID to id',[{'path':MAIN,'pointer':mh[1],'native':x['mainQuestID'],'family':FAMILY,'contract':CONTRACT,'run':'SELF','occurrence':mh[0]}])
 # Only normalized Excel locator fields are projected; physical TextMap occurrences remain distinct.
 wanted={str(x[f]) for path,rows in ((MAIN,main),(STEP,steps)) for x in rows for f in TEXT_FIELDS[path]}
 maps={}
 for lang in LANGS:
  for tier in ('','_Medium'):
   p=f'TextMap/TextMap{tier}{lang}.json';d=defaultdict(list)
   for i,(key,text) in enumerate(pairs(root/p)):
    if key in wanted:d[key].append((p,i,'/'+key,text))
   maps[lang,tier]=d
 for path,rows in ((MAIN,main),(STEP,steps)):
  for i,x in enumerate(rows):
   cid=ident('container',path,f'/{i}')
   for field in TEXT_FIELDS[path]:
    locator=str(x[field])
    for lang in LANGS:
     hits=maps[lang,''].get(locator,[]) or maps[lang,'_Medium'].get(locator,[]);status={0:'missing',1:'resolved'}.get(len(hits),'ambiguous');sid=ident('slot',cid,field,lang);slots.append({'id':sid,'container_id':cid,'owner_field':field,'locale':lang,'raw_locator':locator,'status':status,'candidate_count':len(hits)})
     for p,j,ptr,text in hits:texts.append({'id':ident('text',sid,p,j),'slot_id':sid,'source_path':p,'source_ordinal':j,'source_pointer':ptr,'raw_text':text})
 c.close();return m,containers,children,relations,candidates,slots,texts
