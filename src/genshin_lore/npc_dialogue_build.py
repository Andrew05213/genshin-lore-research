"""NPC relationship-only projection. Source bodies stay in existing artifacts."""
import json,sqlite3
from collections import Counter,defaultdict
from pathlib import Path
from .npc_dialogue_source import ROOT,NPC,EXCEL,sha,digest,canonical,document,source_seals,array_records
IMPLEMENTATION=['src/genshin_lore/npc_dialogue_build.py','src/genshin_lore/npc_dialogue_source.py','src/genshin_lore/loading_tips_source.py','schemas/npc-dialogue-v1.sql']
def ident(kind,*parts):return kind+':'+digest(parts)[:40]
def build(root,scope,core,output):
 root=Path(root);scope=Path(scope);output=Path(output);m=json.loads(scope.read_text());seals=source_seals(root,m);parser=ident('npc-parser',{p:sha(ROOT/p) for p in IMPLEMENTATION});run=ident('npc-run',parser,sha(scope));npc=list(array_records(root/NPC))
 if len(npc)!=m['npc_rows'] or Path(core).stat().st_size!=m['core']['size_bytes'] or sha(core)!=m['core']['sha256']:raise ValueError('NPC/core seal')
 c0=sqlite3.connect(Path(core).absolute().as_uri()+'?mode=ro&immutable=1',uri=True);rows=c0.execute('SELECT occurrence_id,source_ordinal,raw_record_json,source_file_path,source_pointer FROM npc_archive_row_occurrence WHERE ingest_run_id=? ORDER BY source_ordinal',(m['core']['ingest_run_id'],)).fetchall();c0.close()
 if len(rows)!=len(npc):raise ValueError('core denominator')
 index=defaultdict(list)
 with output.open('xb'):pass
 c=sqlite3.connect(output);c.execute('PRAGMA foreign_keys=ON');c.executescript((ROOT/'schemas/npc-dialogue-v1.sql').read_text());c.executemany('INSERT INTO npc_dialogue_source VALUES(?,?,?,?)',seals)
 for (cid,ordinal,raw,path,ptr),(i,obj,_) in zip(rows,npc):
  if ordinal!=i or json.loads(raw)!=obj or path!=NPC or ptr!=f'/{i}':raise ValueError('NPC physical correspondence')
  c.execute('INSERT INTO npc_dialogue_npc VALUES(?,?,?)',(cid,i,str(obj['id'])));index[str(obj['id'])].append(cid)
 stats=defaultdict(Counter);roles=Counter();node_counts=Counter();containers=0
 def container(path,ptr,runtime=None):
  nonlocal containers
  cid=ident('npc-container',run,path,ptr);rid=ident('qt-container',runtime,path,ptr) if runtime else None;c.execute('INSERT INTO npc_dialogue_container VALUES(?,?,?,?,?)',(cid,path,ptr,runtime,rid));containers+=1;return cid
 def relation(cid,ptr,kind,value):
  # Numeric NPC namespace only after a source-authored discriminator/field.
  raw=canonical(value);valid=(type(value) is int and value>=0) or (isinstance(value,str) and value.isascii() and value.isdecimal() and str(int(value))==value)
  candidates=index.get(str(value),[]) if valid else [];status=('missing' if not candidates else 'resolved' if len(candidates)==1 else 'ambiguous') if valid else 'mapping_unresolved';rid=ident('npc-ref',run,cid,ptr,kind);c.execute('INSERT INTO npc_dialogue_relation VALUES(?,?,?,?,?,?,?)',(rid,cid,ptr,kind,raw,status,len(candidates)));stats[kind][status]+=1
  for npc_id in candidates:c.execute('INSERT INTO npc_dialogue_candidate VALUES(?,?,?)',(ident('npc-candidate',rid,npc_id),rid,npc_id))
 for path in EXCEL:
  for i,obj,_ in array_records(root/path):
   values=obj.get('npcId',[])
   if not isinstance(values,list):raise ValueError('npcId shape')
   cid=container(path,f'/{i}')
   for j,value in enumerate(values):relation(cid,f'/{i}/npcId/{j}','talk_config_npc_reference',value)
 shapes={tuple(x['root_keys']):x for x in document(ROOT,'manifests/quest-talk-physical/shapes-v1.json')['shapes']};lists={'rc':'PFALHAKIILD','alt':'GALIDJOEHOC','normalized':'dialogList','third':'GDDPNNHLGBL','older_a':'AAOAAFLLOJI','older_b':'KJNKFMPAGAA','older_c':'MOEOFGCKILF','older_d':'LOJEOMAPIIM'}
 def programs(b):
  s=document(ROOT,b['scope'])
  for path in s['program_source_files']:
   obj=document(root,path);shape=shapes[tuple(sorted(obj))]['mapping'];yield path,'/',shape,obj.get(lists[shape],[])
  if b['kind']=='quest_linked':
   for path in s['quest_source_files']:
    obj=document(root,path);shape='alt' if 'NFFIGDHFAJG' in obj and 'MEGJPCLADOG' in obj else 'rc';key=lists[shape]
    if obj.get(key):yield path,'/'+key,shape,obj[key]
 for b in m['talk_bundles']:
  runtime=document(ROOT,b['runtime'])['ingest_run_id']
  for path,ptr,shape,nodes in programs(b):
   cid=container(path,ptr,runtime);field,type_key,id_key=m['roles'][shape]
   for i,node in enumerate(nodes):
    node_counts[shape]+=1;role=node.get(field)
    if not isinstance(role,dict):raise ValueError('role object shape requires audit')
    if type_key not in role:
     if not ((shape=='normalized' and role=={'_id':''}) or (shape=='older_b' and role=={'id':''})):raise ValueError('unknown absent role discriminator')
     roles['mapping_unresolved_absent_discriminator']+=1;relation(cid,f'/{lists[shape]}/{i}/{field}','talk_node_role_namespace_unresolved',role);continue
    kind=role[type_key];roles[str(kind)]+=1
    if kind=='TALK_ROLE_NPC':relation(cid,f'/{lists[shape]}/{i}/{field}','talk_node_npc_role',role.get(id_key))
 c.commit();release={'contract':'npc-dialogue-evidence-v1','run_id':run,'parser_build_id':parser,'scope_sha256':sha(scope),'npc_rows':len(npc),'source_files':len(seals),'container_count':containers,'relation_states':{k:dict(v) for k,v in stats.items()},'role_discriminators':dict(roles),'node_counts':dict(node_counts),'core':m['core'],'snapshot_id':'anime-game-data:7.0:'+m['commit']};c.execute('INSERT INTO npc_dialogue_release VALUES(1,?)',(canonical(release),));c.commit()
 if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('FK')
 c.execute('VACUUM');c.close();source_seals(root,m);return {**release,'artifact':{'size_bytes':output.stat().st_size,'sha256':sha(output)}}
