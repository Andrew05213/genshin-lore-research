"""Build only Character lore deltas plus exact navigation locators to accepted evidence."""
import hashlib,json,sqlite3
from collections import Counter,defaultdict
from pathlib import Path
from .character_lore_source import *
ROOT=Path(__file__).resolve().parents[2]
IMPLEMENTATION=('src/genshin_lore/character_lore_source.py','src/genshin_lore/character_lore_build.py','schemas/character-lore-v1.sql')

def build(source,scope,core,output):
 source=Path(source);scope=Path(scope);core=Path(core);output=Path(output);m=json.loads(scope.read_text());check_scope(source,m)
 implementation={p:sha(ROOT/p) for p in IMPLEMENTATION};parser=ident('parser',implementation);run=ident('run',parser,sha(scope))
 if core.stat().st_size!=5963210752 or sha(core)!='6bef3518ad17df4a7a953d9d064ba4ee0569c86aeb13ae695092fc73b4d63ed1':raise ValueError('accepted Schema20 core')
 db=sqlite3.connect(core.absolute().as_uri()+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
 if db.execute('select max(version) from schema_migration').fetchone()[0]!=20:raise ValueError('Schema20')
 avatars=list(array_records(source/AVATAR));costumes=list(array_records(source/COSTUME));profiles=list(array_records(source/PROFILE));renames=list(array_records(source/RENAME))
 if tuple(map(len,(costumes,profiles,renames,avatars)))!=(160,186,3,165):raise ValueError('denominator')
 c5b={r['avatar_id']:dict(r) for r in db.execute("select * from character_row_occurrence where ingest_run_id=? and table_name='AvatarExcelConfigData'",(C5B_RUN,))}
 if len(c5b)!=165:raise ValueError('C5B Avatar closure')
 profile_core={r['source_ordinal']:dict(r) for r in db.execute('select * from profile_picture_archive_row_occurrence where ingest_run_id=?',(WAVE02_RUN,))}
 if set(profile_core)!=set(PROFILE_REUSED_ORDINALS):raise ValueError('Profile canary coordinates')
 wanted=set()
 for _,x,_ in costumes:
  if type(x.get('skinId')) is not int or type(x.get('characterId')) is not int:raise ValueError('Costume shape')
  wanted|={str(x['nameTextMapHash']),str(x['descTextMapHash'])}
 for _,x,_ in profiles:
  if type(x.get('id')) is not int or x.get('AHJHBENMGFK') not in {'PROFILE_PICTURE_UNLOCK_BY_AVATAR','PROFILE_PICTURE_UNLOCK_BY_COSTUME','PROFILE_PICTURE_UNLOCK_BY_PARENT_QUEST','PROFILE_PICTURE_UNLOCK_BY_ITEM','PROFILE_PICTURE_UNLOCK_BY_DEFAULT'}:raise ValueError('Profile shape')
  wanted|={str(x['nameTextMapHash']),str(x['unlockDescTextMapHash'])}
 maps={}
 for lang in LANGUAGES:
  for tier in ('','_Medium'):
   path=f'TextMap/TextMap{tier}{lang}.json';d=defaultdict(list)
   for i,(key,text) in enumerate(pairs(source/path)):
    if key in wanted:d[key].append((path,i,'/'+key,text))
   maps[lang,tier]=d
 output.parent.mkdir(parents=True,exist_ok=True);output.touch(exist_ok=False);c=sqlite3.connect(output);c.execute('pragma foreign_keys=on');c.executescript((ROOT/'schemas/character-lore-v1.sql').read_text())
 state=Counter();rel_state=Counter();new_rows={};costume_by_skin=defaultdict(list)
 def row(boundary,path,i,obj,raw,nfield):
  rid=ident(run,'row',path,i);c.execute('insert into character_lore_row values(?,?,?,?,?,?,?,?,?)',(rid,run,boundary,i,nfield,str(obj[nfield]),path,f'/{i}',raw));new_rows[(path,i)]=rid;return rid
 def slots(rid,obj,fields):
  for field in fields:
   value=str(obj[field])
   for lang in LANGUAGES:
    hits=maps[lang,''].get(value,[]) or maps[lang,'_Medium'].get(value,[]);status={0:'missing',1:'resolved'}.get(len(hits),'ambiguous');state[status]+=1;sid=ident(run,'slot',rid,field,lang);c.execute('insert into character_lore_slot values(?,?,?,?,?,?)',(sid,rid,field,lang,value,status))
    for path,i,pointer,text in hits:c.execute('insert into character_lore_text values(?,?,?,?,?,?)',(ident(sid,path,i),sid,path,i,pointer,text))
 def relation(rid,kind,pointer,value,namespace,targets):
  status={0:'missing',1:'resolved'}.get(len(targets),'ambiguous');rel_state[(kind,status)]+=1;rel=ident(run,'relation',rid,kind,pointer);c.execute('insert into character_lore_relation values(?,?,?,?,?,?,?,?,?)',(rel,rid,kind,pointer,json.dumps(value,ensure_ascii=True),namespace,status,len(targets),1))
  for path,ptr,native,family,contract,trun,occ in targets:c.execute('insert into character_lore_candidate values(?,?,?,?,?,?,?,?,?)',(ident(rel,path,ptr,occ),rel,path,ptr,str(native),family,contract,trun,occ))
  return rel
 for i,obj,raw in costumes:
  rid=row('avatar_costume',COSTUME,i,obj,raw,'skinId');costume_by_skin[obj['skinId']].append((i,obj,rid));slots(rid,obj,('nameTextMapHash','descTextMapHash'));target=c5b.get(str(obj['characterId']));relation(rid,'costume_character',f'/{i}/characterId',obj['characterId'],'all AvatarExcel rows; exact id field',[ (AVATAR,target['source_pointer'],obj['characterId'],'characters','character_phase4c5b_full_source_closure',C5B_RUN,target['id']) ] if target else [])
 for i,obj,raw in profiles:
  if i in PROFILE_REUSED_ORDINALS:
   x=profile_core[i];reuse_id=ident(run,'reuse',PROFILE,i);c.execute('insert into character_lore_reuse values(?,?,?,?,?,?,?,?,?,?)',(reuse_id,run,'profile_picture',PROFILE,i,f'/{i}','profile_picture_archive','profile-picture-evidence-v1',WAVE02_RUN,x['occurrence_id']))
   # Preserve exact accepted owner/resolution locators so all 186 Profile rows
   # participate in the successor contract without copying historical text.
   for owner in db.execute('select * from profile_picture_archive_text_owner_occurrence where row_occurrence_id=? order by field_path',(x['occurrence_id'],)):
    for resolution in db.execute('select * from profile_picture_archive_text_resolution_occurrence where owner_occurrence_id=? order by locale',(owner['occurrence_id'],)):
     status='resolved' if resolution['resolution_status']=='resolved_unique' else ('ambiguous' if resolution['candidate_count']>1 else 'missing')
     state[status]+=1;c.execute('insert into character_lore_reuse_slot values(?,?,?,?,?,?,?,?)',(ident(run,'reuse-slot',reuse_id,owner['field_path'],resolution['locale']),reuse_id,owner['field_path'],resolution['locale'],status,resolution['candidate_count'],owner['occurrence_id'],resolution['occurrence_id']))
   kind=obj['AHJHBENMGFK'];value=obj.get('unlockParam')
   if kind in {'PROFILE_PICTURE_UNLOCK_BY_AVATAR','PROFILE_PICTURE_UNLOCK_BY_COSTUME'}:
    old=db.execute('select occurrence_id from profile_picture_archive_relationship_occurrence where row_occurrence_id=? and relation_field=?',(x['occurrence_id'],'unlockParam')).fetchone();old_id=old[0] if old else None
    if kind=='PROFILE_PICTURE_UNLOCK_BY_AVATAR':
     rel_kind='profile_unlock_avatar';namespace='all AvatarExcel rows; enum-gated exact id field';target=c5b.get(str(value));targets=[(AVATAR,target['source_pointer'],value,'characters','character_phase4c5b_full_source_closure',C5B_RUN,target['id'])] if target else []
    else:
     rel_kind='profile_unlock_costume';namespace='all AvatarCostume rows; enum-gated exact skinId field';targets=[(COSTUME,f'/{j}',value,'character_lore_archive','character-lore-evidence-v1',run,xrid) for j,x,xrid in costume_by_skin.get(value,[])]
    status={0:'missing',1:'resolved'}.get(len(targets),'ambiguous');rel_state[(rel_kind,status)]+=1;rel=ident(run,'reuse-relation',reuse_id,rel_kind,f'/{i}/unlockParam')
    c.execute('insert into character_lore_reuse_relation values(?,?,?,?,?,?,?,?,?,?)',(rel,reuse_id,rel_kind,f'/{i}/unlockParam',json.dumps(value,ensure_ascii=True),namespace,status,len(targets),1,old_id))
    for path,ptr,native,family,contract,trun,occ in targets:c.execute('insert into character_lore_reuse_candidate values(?,?,?,?,?,?,?,?,?)',(ident(rel,path,ptr,occ),rel,path,ptr,str(native),family,contract,trun,occ))
   continue
  rid=row('profile_picture',PROFILE,i,obj,raw,'id');slots(rid,obj,('nameTextMapHash','unlockDescTextMapHash'));kind=obj['AHJHBENMGFK'];value=obj.get('unlockParam')
  if kind=='PROFILE_PICTURE_UNLOCK_BY_AVATAR':
   target=c5b.get(str(value));relation(rid,'profile_unlock_avatar',f'/{i}/unlockParam',value,'all AvatarExcel rows; enum-gated exact id field',[(AVATAR,target['source_pointer'],value,'characters','character_phase4c5b_full_source_closure',C5B_RUN,target['id'])] if target else [])
  elif kind=='PROFILE_PICTURE_UNLOCK_BY_COSTUME':
   targets=[(COSTUME,f'/{j}',value,'character_lore_archive','character-lore-evidence-v1',run,xrid) for j,x,xrid in costume_by_skin.get(value,[])];relation(rid,'profile_unlock_costume',f'/{i}/unlockParam',value,'all AvatarCostume rows; enum-gated exact skinId field',targets)
 for i,obj,raw in renames:
  if type(obj.get('relatedAvatarID')) is not int or not isinstance(obj.get('multiLangDefaultNameList'),list):raise ValueError('Rename shape')
  rid=row('avatar_rename',RENAME,i,obj,raw,'renameID');target=c5b.get(str(obj['relatedAvatarID']));relation(rid,'rename_character',f'/{i}/relatedAvatarID',obj['relatedAvatarID'],'all AvatarExcel rows; explicit relatedAvatarID to id',[(AVATAR,target['source_pointer'],obj['relatedAvatarID'],'characters','character_phase4c5b_full_source_closure',C5B_RUN,target['id'])] if target else [])
  by=defaultdict(list)
  for j,item in enumerate(obj['multiLangDefaultNameList']):by[item.get('languageType')].append((j,item))
  for lang,key in [('CHS','LANGUAGE_SC'),('EN','LANGUAGE_EN'),('JP','LANGUAGE_JP')]:
   hits=by[key];status={0:'missing',1:'resolved'}.get(len(hits),'ambiguous');state[status]+=1;sid=ident(run,'slot',rid,'multiLangDefaultNameList',lang);c.execute('insert into character_lore_slot values(?,?,?,?,?,?)',(sid,rid,'multiLangDefaultNameList',lang,key,status))
   for j,item in hits:c.execute('insert into character_lore_text values(?,?,?,?,?,?)',(ident(sid,RENAME,j),sid,RENAME,j,f'/{i}/multiLangDefaultNameList/{j}/planeText',item['planeText']))
 # Each anchor is one accepted physical Avatar row, never a logical character.
 anchors={}
 for aid,x in sorted(c5b.items()):
  a=ident(run,'anchor',x['id']);anchors[aid]=a;c.execute('insert into character_lore_navigation_anchor values(?,?,?,?,?,?,?,?)',(a,run,aid,AVATAR,x['source_pointer'],'character_phase4c5b_full_source_closure',C5B_RUN,x['id']))
 def edge(aid,kind,family,contract,trun,occ,proof):
  if aid in anchors:c.execute('insert into character_lore_navigation_edge values(?,?,?,?,?,?,?,?)',(ident(run,'edge',anchors[aid],kind,family,trun,occ,proof),anchors[aid],kind,family,contract,trun,occ,proof))
 for x in db.execute('select id,avatar_id,table_name from character_row_occurrence where ingest_run_id=? order by id',(C5B_RUN,)):edge(x['avatar_id'],'character_'+x['table_name'],'characters','character_phase4c5b_full_source_closure',C5B_RUN,x['id'],'accepted:character_row.avatar_id')
 for x in db.execute('select source_row_occurrence_id,target_voice_container_id,id from character_relation_occurrence where ingest_run_id=? and target_voice_container_id is not null order by id',(C5B_RUN,)):
  aid=db.execute('select avatar_id from character_row_occurrence where id=?',(x['source_row_occurrence_id'],)).fetchone()[0];edge(aid,'voice_container','characters','character_phase4c5b_full_source_closure',C5B_RUN,x['target_voice_container_id'],x['id'])
 for x in db.execute('select id,native_locator_value from avatar_codex_row_occurrence where ingest_run_id=? order by id',(WAVE02_RUN,)):edge(x['native_locator_value'],'avatar_codex','avatar_codex_archive','avatar-codex-archive-evidence-v1',WAVE02_RUN,x['id'],'accepted:AvatarCodex.avatarId')
 # New rows navigate via independently frozen explicit relationships.
 for x in c.execute("select r.id,r.kind,r.row_id,k.target_native_value from character_lore_relation r join character_lore_candidate k on k.relation_id=r.id where r.kind in ('costume_character','rename_character')"):
  edge(x[3],'new_'+x[1],'character_lore_archive','character-lore-evidence-v1',run,x[2],x[0])
 for x in c.execute("select r.id,r.row_id,k.target_native_value from character_lore_relation r join character_lore_candidate k on k.relation_id=r.id where r.kind='profile_unlock_avatar'"):edge(x[2],'new_profile_picture','character_lore_archive','character-lore-evidence-v1',run,x[1],x[0])
 for x in c.execute("select r.id,r.row_id,k.target_occurrence_id from character_lore_relation r join character_lore_candidate k on k.relation_id=r.id where r.kind='profile_unlock_costume'"):
  cr=c.execute("select k.target_native_value from character_lore_relation r join character_lore_candidate k on k.relation_id=r.id where r.row_id=? and r.kind='costume_character'",(x[2],)).fetchone();
  if cr:edge(cr[0],'new_profile_picture_via_costume','character_lore_archive','character-lore-evidence-v1',run,x[1],x[0])
 # Reused Profile rows remain in Schema20; successor relations provide the
 # navigation proof, including the corrected enum-gated Costume namespace.
 for i,x in profile_core.items():
  obj=profiles[i][1];value=obj.get('unlockParam');kind=obj['AHJHBENMGFK'];aid=str(value) if kind=='PROFILE_PICTURE_UNLOCK_BY_AVATAR' else (str(costume_by_skin[value][0][1]['characterId']) if kind=='PROFILE_PICTURE_UNLOCK_BY_COSTUME' and costume_by_skin.get(value) else None)
  if aid:
   rel_kind='profile_unlock_avatar' if kind=='PROFILE_PICTURE_UNLOCK_BY_AVATAR' else 'profile_unlock_costume';proof=ident(run,'reuse-relation',ident(run,'reuse',PROFILE,i),rel_kind,f'/{i}/unlockParam')
   edge(aid,'reused_profile_picture','profile_picture_archive','profile-picture-evidence-v1',WAVE02_RUN,x['occurrence_id'],proof)
 tables=['character_lore_row','character_lore_reuse','character_lore_reuse_slot','character_lore_reuse_relation','character_lore_reuse_candidate','character_lore_slot','character_lore_text','character_lore_relation','character_lore_candidate','character_lore_navigation_anchor','character_lore_navigation_edge'];counts={t:c.execute('select count(*) from '+t).fetchone()[0] for t in tables}
 release={'contract':'character-lore-evidence-v1','run_id':run,'parser_build_id':parser,'snapshot_id':SNAPSHOT,'scope_sha256':sha(scope),'implementation':implementation,'counts':counts,'text_states':dict(state),'declared_text_slots':sum(state.values()),'relation_states':{k[0]+':'+k[1]:v for k,v in rel_state.items()},'declared_relations':sum(rel_state.values()),'reused_c5b':{'run_id':C5B_RUN,'rows':10277,'voice_containers':375,'audio_children':9611},'reused_profile_rows':5,'full_7_0':False,'corpus_complete':True}
 c.execute('insert into character_lore_release values(1,?)',(json.dumps(release,sort_keys=True),));c.commit();
 if c.execute('pragma foreign_key_check').fetchall():raise ValueError('FK')
 c.execute('vacuum');c.close();db.close();check_scope(source,m)
 return {**release,'artifact':{'size_bytes':output.stat().st_size,'sha256':sha(output)}}
