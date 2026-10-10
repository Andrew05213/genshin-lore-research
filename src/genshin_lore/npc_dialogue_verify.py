"""Independent source and exact physical-runtime-coordinate verifier."""
import json,sqlite3,hashlib
from pathlib import Path
from collections import Counter,defaultdict
from .npc_dialogue_source import ROOT,sha,digest,canonical,source_seals,array_records,document

def verify(root,scope,core,artifact,report,talk_artifacts):
 root=Path(root);scope=Path(scope);artifact=Path(artifact);m=json.loads(scope.read_text());seals=source_seals(root,m)
 def check(x,msg):
  if not x:raise ValueError(msg)
 def stable():
  check(not any(Path(str(artifact)+s).exists() for s in ('-wal','-shm','-journal')),'sidecar');st=artifact.stat();return(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)
 check(m['relation_kinds']==['talk_config_npc_reference','talk_node_npc_role','talk_node_role_namespace_unresolved'],'declared relation kinds')
 before=stable();check(sha(artifact)==report['artifact']['sha256'] and before[2]==report['artifact']['size_bytes'],'artifact');c=sqlite3.connect(artifact.absolute().as_uri()+'?mode=ro&immutable=1',uri=True)
 def ident(kind,*parts):return kind+':'+hashlib.sha256(json.dumps(parts,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:40]
 parser=ident('npc-parser',{p:sha(ROOT/p) for p in ['src/genshin_lore/npc_dialogue_build.py','src/genshin_lore/npc_dialogue_source.py','src/genshin_lore/loading_tips_source.py','schemas/npc-dialogue-v1.sql']});run=ident('npc-run',parser,sha(scope));release=json.loads(c.execute('SELECT manifest_json FROM npc_dialogue_release WHERE id=1').fetchone()[0]);check(release=={k:v for k,v in report.items() if k!='artifact'} and release['run_id']==run and release['parser_build_id']==parser and release['contract']=='npc-dialogue-evidence-v1','release identity');check(release['snapshot_id']=='anime-game-data:7.0:'+m['commit'] and release['core']==m['core'],'source provenance')
 check(Path(core).stat().st_size==m['core']['size_bytes'] and sha(core)==m['core']['sha256'],'core');cn=sqlite3.connect(Path(core).absolute().as_uri()+'?mode=ro&immutable=1',uri=True);accepted=cn.execute('SELECT occurrence_id,source_ordinal,raw_record_json FROM npc_archive_row_occurrence WHERE ingest_run_id=? ORDER BY source_ordinal',(m['core']['ingest_run_id'],)).fetchall();cn.close();npc=list(array_records(root/'ExcelBinOutput/NpcExcelConfigData.json'));check(len(npc)==len(accepted)==m['npc_rows'],'NPC denominator');npc_rows=[];by_native=defaultdict(list)
 for (id,ordinal,raw),(i,obj,_) in zip(accepted,npc):
  check(i==ordinal and json.loads(raw)==obj,'NPC provenance');npc_rows.append((id,i,str(obj['id'])));by_native[str(obj['id'])].append(id)
 # Independent inverted enumeration of all NPC coordinates, including duplicates.
 check(c.execute('SELECT * FROM npc_dialogue_npc ORDER BY id').fetchall()==sorted(npc_rows),'NPC coordinate index');check(c.execute('SELECT * FROM npc_dialogue_source ORDER BY path').fetchall()==seals,'source seals')
 expected_containers=[];expected_refs=[];expected_candidates=[];stats=defaultdict(Counter);role_counts=Counter();node_counts=Counter();runtime_bound=0
 def emit_container(path,pointer,old_run):
  id=ident('npc-container',run,path,pointer);old_id=ident('qt-container',old_run,path,pointer) if old_run else None;expected_containers.append((id,path,pointer,old_run,old_id));return id,old_id
 def emit(id,pointer,kind,value):
  valid=type(value)is int and value>=0
  if isinstance(value,str):valid=value.isascii() and value.isdecimal() and str(int(value))==value
  matches=list(by_native.get(str(value),[])) if valid else [];status={0:'missing',1:'resolved'}.get(len(matches),'ambiguous') if valid else 'mapping_unresolved';rid=ident('npc-ref',run,id,pointer,kind);expected_refs.append((rid,id,pointer,kind,canonical(value),status,len(matches)));stats[kind][status]+=1
  expected_candidates.extend((ident('npc-candidate',rid,target),rid,target) for target in matches)
 for path in ['ExcelBinOutput/TalkExcelConfigData_0.json','ExcelBinOutput/TalkExcelConfigData_1.json']:
  for i,obj,_ in array_records(root/path):
   id,_=emit_container(path,f'/{i}',None)
   for j,value in enumerate(obj.get('npcId',[])):emit(id,f'/{i}/npcId/{j}','talk_config_npc_reference',value)
 shapes={tuple(x['root_keys']):x for x in document(ROOT,'manifests/quest-talk-physical/shapes-v1.json')['shapes']};mapping={'rc':('PFALHAKIILD','LFGCLNLPAPB','_type','_id'),'alt':('GALIDJOEHOC','PIBKEGJOJHN','_type','_id'),'normalized':('dialogList','talkRole','type','id'),'third':('GDDPNNHLGBL','EENIFNIGHCH','_type','_id'),'older_a':('AAOAAFLLOJI','HJLEMJIGNFE','type','id'),'older_b':('KJNKFMPAGAA','IFAOOKCBDGD','type','id'),'older_c':('MOEOFGCKILF','LCECPDILLEE','_type','_id'),'older_d':('LOJEOMAPIIM','HJIPOJOECIF','_type','_id')};check(m['roles']=={k:list(v[1:]) for k,v in mapping.items()},'frozen role mapping')
 for b in m['talk_bundles']:
  rt=document(ROOT,b['runtime']);old_run=rt['ingest_run_id'];path=Path(talk_artifacts[b['kind']]);check(path.stat().st_size==rt['artifact']['size_bytes'] and sha(path)==rt['artifact']['sha256'],'Talk exact artifact');t=sqlite3.connect(path.absolute().as_uri()+'?mode=ro&immutable=1',uri=True);scope_data=document(ROOT,b['scope'])
  jobs=[(p,False) for p in scope_data['program_source_files']]+([(p,True) for p in scope_data['quest_source_files']] if b['kind']=='quest_linked' else [])
  for path,embedded in jobs:
   obj=document(root,path)
   if embedded:shape='alt' if 'NFFIGDHFAJG'in obj and 'MEGJPCLADOG'in obj else 'rc'
   else:shape=shapes[tuple(sorted(obj))]['mapping']
   field,role_key,type_key,id_key=mapping[shape]
   if embedded and not obj.get(field):continue
   ptr='/'+field if embedded else '/';cid,old_cid=emit_container(path,ptr,old_run);actual=t.execute('SELECT ingest_run_id,metadata_json FROM quest_talk_physical_container WHERE id=?',(old_cid,)).fetchone();check(actual is not None and actual[0]==old_run and json.loads(actual[1])['source_path']==path and json.loads(actual[1])['source_pointer']==ptr,'exact Talk container coordinate')
   for i,node in enumerate(obj.get(field,[])):
    node_counts[shape]+=1;role=node[role_key]
    if type_key not in role:
     check((shape=='normalized' and role=={'_id':''}) or (shape=='older_b' and role=={'id':''}),'audited absent discriminator')
     node_ptr=f'/{field}/{i}';nid=ident('qt-node',old_run,path,node_ptr);actual=t.execute('SELECT container_id,pointer,raw_json FROM quest_talk_physical_node WHERE id=? AND ingest_run_id=?',(nid,old_run)).fetchone();check(actual is not None and actual[:2]==(old_cid,node_ptr) and json.loads(actual[2])==node,'exact unresolved Talk node coordinate/raw role');runtime_bound+=1
     role_counts['mapping_unresolved_absent_discriminator']+=1;emit(cid,f'/{field}/{i}/{role_key}','talk_node_role_namespace_unresolved',role);continue
    role_counts[str(role[type_key])]+=1
    if role[type_key]!='TALK_ROLE_NPC':continue
    node_ptr=f'/{field}/{i}';nid=ident('qt-node',old_run,path,node_ptr);actual=t.execute('SELECT container_id,pointer,raw_json FROM quest_talk_physical_node WHERE id=? AND ingest_run_id=?',(nid,old_run)).fetchone();check(actual is not None and actual[:2]==(old_cid,node_ptr) and json.loads(actual[2])==node,'exact Talk node coordinate/raw role');runtime_bound+=1;emit(cid,node_ptr+'/'+role_key,'talk_node_npc_role',role.get(id_key))
  t.close()
 seals_out={}
 for table,rows in [('npc_dialogue_container',expected_containers),('npc_dialogue_relation',expected_refs),('npc_dialogue_candidate',expected_candidates)]:
  actual=c.execute('SELECT * FROM '+table+' ORDER BY id').fetchall();check(actual==sorted(rows),table+' independent source equality');seals_out[table]={'rows':len(rows),'sha256':digest(actual)}
 check(release['relation_states']=={k:dict(v) for k,v in stats.items()} and release['role_discriminators']==dict(role_counts) and release['node_counts']==dict(node_counts),'classified denominators');check(not c.execute('PRAGMA foreign_key_check').fetchall(),'FK');check(c.execute('PRAGMA integrity_check').fetchone()[0]=='ok','integrity');e=sqlite3.connect(':memory:');e.executescript((ROOT/'schemas/npc-dialogue-v1.sql').read_text());sql="SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name";check(c.execute(sql).fetchall()==e.execute(sql).fetchall(),'write-once schema');e.close();c.close();source_seals(root,m);check(stable()==before and sha(artifact)==report['artifact']['sha256'],'artifact stable')
 return {'status':'pass','run_id':run,'artifact':report['artifact'],'logical_seals':seals_out,'runtime_bound_role_observations':runtime_bound,'relation_states':release['relation_states'],'source_candidate_namespace':'all14060 pinned NPC physical rows, no runtime availability filter'}
