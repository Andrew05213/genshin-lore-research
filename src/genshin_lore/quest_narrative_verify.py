"""Independent verifier for the Quest narrative physical projection."""
from __future__ import annotations
import hashlib,json,sqlite3,subprocess,time
from collections import Counter,defaultdict
from pathlib import Path
from .quest_narrative_source import COMMIT,TREE,SNAPSHOT,FAMILY,CONTRACT,MAIN,STEP,FMT,LANGS,TEXT_FIELDS,canonical,ident,sha,sha_bytes,load,file_seal,git_inventory
from .quest_narrative_build import IMPLEMENTATION,TABLES
from .loading_tips_source import pairs
ROOT=Path(__file__).resolve().parents[2]
def check(x,msg):
 if not x:raise ValueError(msg)
def check_exact_row(row,expected,msg):
 check(row is not None and tuple(row)==tuple(expected),msg)
def check_exact_table(actual_rows,expected_rows,msg):
 check(tuple(map(tuple,actual_rows))==tuple(map(tuple,expected_rows)),msg)
def verify(root,scope_path,artifact,manifest_path,quest_artifact,quest_manifest):
 started=time.perf_counter();root=Path(root);scope=load(scope_path);m=load(manifest_path);qm=load(quest_manifest)
 check(subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()==[COMMIT,TREE],'source pin')
 check([file_seal(root,x['path']) for x in scope['files']]==scope['files'],'file seals');inv=git_inventory(root,'BinOutput/Quest');check(len(inv)==4466 and sha_bytes(canonical(sorted(inv.items())).encode())==scope['quest_tree']['path_blob_sha256'],'Quest inventory')
 check(sha(quest_manifest)==scope['quest_talk_v7_manifest_sha256'] and qm['ingest_run_id']=='ingest-run:a5c566f94d8d9d8af19c0073b0d51cc38e550ad6','v7 dependency')
 check(Path(quest_artifact).stat().st_size==qm['artifact']['size_bytes'] and sha(quest_artifact)==qm['artifact']['sha256'],'v7 artifact')
 impl={p:sha(ROOT/p) for p in IMPLEMENTATION};parser=ident('parser',impl);run=ident('run',SNAPSHOT,parser,sha(scope_path),CONTRACT)
 check(m['release']['implementation']==impl and m['release']['parser_build_id']==parser and m['release']['run_id']==run,'identity')
 before=(Path(artifact).stat().st_ino,Path(artifact).stat().st_size,Path(artifact).stat().st_mtime_ns,sha(artifact));check(before[1]==m['artifact']['size_bytes'] and before[3]==m['artifact']['sha256'],'artifact seal')
 db=sqlite3.connect(Path(artifact).resolve().as_uri()+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
 qdb=sqlite3.connect(Path(quest_artifact).resolve().as_uri()+'?mode=ro&immutable=1',uri=True);qdb.row_factory=sqlite3.Row
 main=load(root/MAIN);steps=load(root/STEP);fmt=load(root/FMT);check(tuple(map(len,(main,steps,fmt)))==(4372,17814,3780),'Excel denominator')
 # Independently validate every primary row and every accepted Quest reuse locator.
 by_main=defaultdict(list);by_step={x['subId']:(i,x) for i,x in enumerate(steps)};container_count=Counter()
 for path,rows,kind,key in ((MAIN,main,'main_quest_row','id'),(STEP,steps,'quest_step_row','subId'),(FMT,fmt,'format_relation_config','mainQuestID')):
  blob=file_seal(root,path)['blob']
  for i,x in enumerate(rows):
   cid=ident('container',path,f'/{i}');r=db.execute('select * from quest_narrative_container where id=?',(cid,)).fetchone();expected=(cid,run,kind,path,i,f'/{i}',str(x[key]),blob,sha_bytes(canonical(x).encode()),canonical(x),'opaque_fields_preserved' if path==FMT else 'resolved',None,None,None,None);check(r and tuple(r)==expected,'primary '+path+f'/{i}');container_count[kind]+=1
   if path==MAIN:by_main[x['id']].append((cid,path,f'/{i}'))
 q="""select p.id,sf.relative_path,p.native_id,sr.raw_sha256 from quest_talk_physical_container p join source_record sr on sr.id=p.source_record_id join source_file sf on sf.id=sr.source_file_id where p.kind='quest_root' order by sf.relative_path"""
 accepted={x['relative_path']:dict(x) for x in qdb.execute(q)};check(len(accepted)==4466,'accepted roots')
 child_by_sub=defaultdict(list);child_states=Counter()
 for path in sorted(inv):
  raw=(root/path).read_bytes();doc=json.loads(raw.decode('utf-8-sig'));shape='rc' if 'EBNBLBEIFFJ'in doc else 'alt' if 'MEGJPCLADOG'in doc else None;check(shape is not None,'shape')
  idkey,listkey,subkey,orderkey=('OIFGMOHKPOI','EBNBLBEIFFJ','KCGAKLCHDCC','EMNMIOBCCLL') if shape=='rc' else ('NFIEHACCECI','MEGJPCLADOG',None,None);native=doc.get(idkey);a=accepted[path];cid=ident('container',path,'/');r=db.execute('select * from quest_narrative_container where id=?',(cid,)).fetchone()
  reuse_json=canonical({'accepted_raw_sha256':a['raw_sha256']});expected=(cid,run,'quest_root_reuse',path,None,'/',str(native),inv[path],sha_bytes(reuse_json.encode()),reuse_json,'resolved','quest_talk_pilot','quest-talk-evidence-v7',qm['ingest_run_id'],a['id']);check(r and tuple(r)==expected and a['raw_sha256']==sha_bytes(raw),'Quest reuse '+path);container_count['quest_root_reuse']+=1;by_main[native].append((cid,path,'/'))
  for i,ch in enumerate(doc[listkey]):
   ptr=f'/{listkey}/{i}';chid=ident('child',path,ptr);x=db.execute('select * from quest_narrative_child where id=?',(chid,)).fetchone();sub=ch.get(subkey) if subkey else None;order=ch.get(orderkey) if orderkey else None;state='resolved_declared_fields' if shape=='rc' else 'mapping_unresolved';target=ident('container',STEP,f'/{by_step[sub][0]}') if sub in by_step else None
   expected=(chid,cid,ptr,shape,i,None if sub is None else str(sub),order,state,sha_bytes(canonical(ch).encode()),target)
   check_exact_row(x,expected,'child '+path+ptr);child_states[state]+=1
   if sub is not None:child_by_sub[sub].append((chid,path,ptr))
 check(container_count==Counter(m['release']['containers']) and child_states==Counter(m['release']['children']),'container/child counts')
 def relation(cid,kind,pointer,value,namespace,expected,complete,status=None,reused=None):
  rid=ident('relation',cid,kind,pointer);r=db.execute('select * from quest_narrative_relation where id=?',(rid,)).fetchone();status=status or ({0:'missing',1:'resolved_unique'}.get(len(expected),'ambiguous'));check(r and tuple(r)==(rid,cid,kind,pointer,canonical(value),namespace,status,len(expected),complete,reused),'relation '+kind+pointer)
  wanted=[]
  for path,ptr,trun,occ in expected:
   tfamily,tcontract=(FAMILY,CONTRACT) if trun==run else ('quest_talk_pilot','quest-talk-evidence-v7');wanted.append((ident('candidate',rid,path,ptr,trun,occ),rid,path,ptr,str(value),tfamily,tcontract,trun,occ))
   if trun==run:
    target=db.execute('select source_path,source_pointer,native_id from quest_narrative_container where id=?',(occ,)).fetchone()
    if target is None:target=db.execute('select p.source_path,x.source_pointer,x.native_sub_id from quest_narrative_child x join quest_narrative_container p on p.id=x.container_id where x.id=?',(occ,)).fetchone()
   else:target=qdb.execute("""select sf.relative_path,sr.pointer,p.native_id from quest_talk_physical_container p join source_record sr on sr.id=p.source_record_id join source_file sf on sf.id=sr.source_file_id where p.id=? and p.ingest_run_id=?""",(occ,trun)).fetchone()
   check(target and tuple(target)==(path,ptr,str(value)),'exact target coordinate '+occ)
  actual=[tuple(x) for x in db.execute('select * from quest_narrative_candidate where relation_id=? order by id',(rid,))];check(actual==sorted(wanted,key=lambda x:x[0]),'candidates '+kind+pointer);return len(expected)
 rc=Counter();cand=0
 for sub,items in child_by_sub.items():
  for chid,p,ptr in items:
   cid=ident('container',p,'/');target=by_step.get(sub);expected=[] if target is None else [(STEP,f'/{target[0]}',run,ident('container',STEP,f'/{target[0]}'))]
   cand+=relation(cid,'physical_child_step_lookup',ptr+'/KCGAKLCHDCC',sub,'all QuestExcel rows; shape-mapped KCGAKLCHDCC to explicit subId',expected,1);rc['physical_child_step_lookup']+=1
 for i,x in enumerate(main):
  cid=ident('container',MAIN,f'/{i}');expected=[]
  for _,p,ptr in sorted(by_main[x['id']],key=lambda z:z[1]):
   if p.startswith('BinOutput/Quest/'):
    expected.append((p,ptr,run,ident('container',p,'/')))
  cand+=relation(cid,'mainquest_physical_root',f'/{i}/id',x['id'],'all 4,466 BinOutput/Quest physical roots; shape-specific explicit root ID',expected,1);rc['mainquest_physical_root']+=1
 for i,x in enumerate(steps):
  cid=ident('container',STEP,f'/{i}');mi=next(j for j,y in enumerate(main) if y['id']==x['mainId'])
  cand+=relation(cid,'step_mainquest',f'/{i}/mainId',x['mainId'],'all MainQuestExcel rows; explicit mainId to id',[(MAIN,f'/{mi}',run,ident('container',MAIN,f'/{mi}'))],1);rc['step_mainquest']+=1
  expected=[(p,ptr,run,chid) for chid,p,ptr in child_by_sub[x['subId']]];cand+=relation(cid,'step_physical_child',f'/{i}/subId',x['subId'],'all mapped rc Quest child records; explicit subId to shape-mapped KCGAKLCHDCC',expected,1);rc['step_physical_child']+=1
 q="""select r.*,sf.relative_path from quest_talk_physical_reference r join quest_talk_physical_container p on p.id=r.container_id join source_record sr on sr.id=p.source_record_id join source_file sf on sf.id=sr.source_file_id where p.kind='quest_root' order by sf.relative_path,r.pointer"""
 for r in qdb.execute(q):
  cid=ident('container',r['relative_path'],'/');expected=sorted((x['path'],x['pointer'],qm['ingest_run_id'],x['container_id']) for x in json.loads(r['candidates_json']));cand+=relation(cid,'quest_talk_reference',r['pointer'],r['native_id'],'frozen v7 physical Talk candidate set; semantic search remains incomplete',expected,0,'resolved_candidate_set' if expected else 'mapping_unresolved',r['id']);rc['quest_talk_reference']+=1
 main_index={x['id']:i for i,x in enumerate(main)}
 for i,x in enumerate(fmt):
  mi=main_index[x['mainQuestID']];cid=ident('container',FMT,f'/{i}');cand+=relation(cid,'format_config_owner',f'/{i}/mainQuestID',x['mainQuestID'],'all MainQuestExcel rows; explicit mainQuestID to id',[(MAIN,f'/{mi}',run,ident('container',MAIN,f'/{mi}'))],1);rc['format_config_owner']+=1
 check(rc==Counter(m['release']['relations']) and cand==m['release']['candidates'],'relation totals')
 # Independently enumerate TextMap physical candidates and assert exhaustive mutually exclusive slots.
 wanted={str(x[f]) for path,rows in ((MAIN,main),(STEP,steps)) for x in rows for f in TEXT_FIELDS[path]};maps={}
 for lang in LANGS:
  for tier in ('','_Medium'):
   p=f'TextMap/TextMap{tier}{lang}.json';d=defaultdict(list)
   for j,(key,text) in enumerate(pairs(root/p)):
    if key in wanted:d[key].append((p,j,'/'+key,text))
   maps[lang,tier]=d
 slot_states=Counter();text_count=0
 for path,rows in ((MAIN,main),(STEP,steps)):
  for i,x in enumerate(rows):
   cid=ident('container',path,f'/{i}')
   for field in TEXT_FIELDS[path]:
    loc=str(x[field])
    for lang in LANGS:
     hits=maps[lang,''].get(loc,[]) or maps[lang,'_Medium'].get(loc,[]);status={0:'missing',1:'resolved'}.get(len(hits),'ambiguous');sid=ident('slot',cid,field,lang);s=db.execute('select * from quest_narrative_text_slot where id=?',(sid,)).fetchone();check_exact_row(s,(sid,cid,field,lang,loc,status,len(hits)),'slot '+path+f'/{i}/'+field+'/'+lang)
     wanted_rows=sorted((ident('text',sid,p,o),sid,p,o,ptr,text) for p,o,ptr,text in hits)
     actual=[tuple(y) for y in db.execute('select * from quest_narrative_text_candidate where slot_id=? order by id',(sid,))];check(actual==wanted_rows,'text candidates '+path+f'/{i}/'+field+'/'+lang);slot_states[status]+=1;text_count+=len(hits)
 check(slot_states==Counter(m['release']['text_slots']) and text_count==m['release']['texts'],'text totals')
 for table in TABLES:check(db.execute('select count(*) from '+table).fetchone()[0]==m['release']['counts'][table],'extra/missing '+table)
 check(not db.execute('pragma foreign_key_check').fetchall() and db.execute('pragma integrity_check').fetchone()[0]=='ok','database integrity');release=json.loads(db.execute('select manifest_json from quest_narrative_release').fetchone()[0]);check(release==m['release'],'release')
 logical={t:{'rows':db.execute('select count(*) from '+t).fetchone()[0],'sha256':hashlib.sha256(b''.join((canonical(list(x))+'\n').encode() for x in db.execute('select * from '+t+' order by 1'))).hexdigest()} for t in TABLES};db.close();qdb.close();check(before==(Path(artifact).stat().st_ino,Path(artifact).stat().st_size,Path(artifact).stat().st_mtime_ns,sha(artifact)),'artifact changed')
 return {'status':'pass','quest_files':4466,'main_rows':4372,'step_rows':17814,'format_rows':3780,'children':sum(child_states.values()),'relations':sum(rc.values()),'candidates':cand,'text_slots':sum(slot_states.values()),'texts':text_count,'independent_candidate_enumeration':True,'exact_v7_locators':True,'logical_seals':logical,'artifact_sha256':before[3],'wall_seconds':time.perf_counter()-started}
