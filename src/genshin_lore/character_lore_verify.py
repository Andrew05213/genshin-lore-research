"""Independent pinned-source verifier for Character Lore Wave."""
import hashlib,json,sqlite3
from collections import Counter,defaultdict
from pathlib import Path
from .character_lore_source import *
from .loading_tips_source import sha
ROOT=Path(__file__).resolve().parents[2]
def require(ok,label):
 if not ok:raise ValueError(label)
def digest(rows):return hashlib.sha256(json.dumps(rows,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()
def verify(source,scope,core,artifact,report):
 source=Path(source);scope=Path(scope);core=Path(core);artifact=Path(artifact);m=json.loads(scope.read_text());check_scope(source,m);before=artifact.stat();artifact_sha=sha(artifact);require(artifact_sha==report['artifact']['sha256'],'artifact SHA')
 d=sqlite3.connect(core.absolute().as_uri()+'?mode=ro&immutable=1',uri=True);d.row_factory=sqlite3.Row;c=sqlite3.connect(artifact.absolute().as_uri()+'?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
 release=json.loads(c.execute('select manifest_json from character_lore_release where id=1').fetchone()[0]);require(release=={k:v for k,v in report.items() if k!='artifact'},'release binding');run=release['run_id']
 costume=list(array_records(source/COSTUME));profile=list(array_records(source/PROFILE));rename=list(array_records(source/RENAME));avatars=list(array_records(source/AVATAR));require(tuple(map(len,(costume,profile,rename,avatars)))==(160,186,3,165),'source denominator')
 core_avatar={r['avatar_id']:dict(r) for r in d.execute("select * from character_row_occurrence where ingest_run_id=? and table_name='AvatarExcelConfigData'",(C5B_RUN,))};require(len(core_avatar)==165,'C5B anchors')
 core_profile={r['source_ordinal']:dict(r) for r in d.execute('select * from profile_picture_archive_row_occurrence where ingest_run_id=?',(WAVE02_RUN,))};require(set(core_profile)==set(PROFILE_REUSED_ORDINALS),'profile reuse')
 # Raw delta is the exact physical complement, preserving lexical records.
 expected={(COSTUME,i,raw,'avatar_costume','skinId',str(x['skinId'])) for i,x,raw in costume}|{(PROFILE,i,raw,'profile_picture','id',str(x['id'])) for i,x,raw in profile if i not in PROFILE_REUSED_ORDINALS}|{(RENAME,i,raw,'avatar_rename','renameID',str(x['renameID'])) for i,x,raw in rename}
 actual={(r['source_path'],r['ordinal'],r['raw_json'],r['boundary'],r['native_field'],r['native_value']) for r in c.execute('select * from character_lore_row')};require(actual==expected,'raw physical complement')
 rows={(r['source_path'],r['ordinal']):dict(r) for r in c.execute('select * from character_lore_row')};reuse={(r['source_path'],r['ordinal']):dict(r) for r in c.execute('select * from character_lore_reuse')};require(set(reuse)=={(PROFILE,i) for i in PROFILE_REUSED_ORDINALS},'reuse coordinates')
 for (_,i),x in reuse.items():require(x['target_occurrence_id']==core_profile[i]['occurrence_id'] and x['target_run']==WAVE02_RUN,'reuse exact locator')
 # Independently enumerate TextMap physical pairs and embedded source text.
 wanted=set()
 for _,x,_ in costume:wanted|={str(x['nameTextMapHash']),str(x['descTextMapHash'])}
 for i,x,_ in profile:
  if i not in PROFILE_REUSED_ORDINALS:wanted|={str(x['nameTextMapHash']),str(x['unlockDescTextMapHash'])}
 maps={}
 for lang in LANGUAGES:
  for tier in ('','_Medium'):
   p=f'TextMap/TextMap{tier}{lang}.json';z=defaultdict(list)
   for n,(key,text) in enumerate(pairs(source/p)):
    if key in wanted:z[key].append((p,n,'/'+key,text))
   maps[lang,tier]=z
 expected_slots={};expected_text=set();states=Counter();expected_reuse_slots={}
 # Rebuild the accepted five-row slot projection directly from Schema20.  The
 # successor stores only exact locators and classification, never copied text.
 for i in PROFILE_REUSED_ORDINALS:
  reuse_id=reuse[PROFILE,i]['id'];row_id=core_profile[i]['occurrence_id']
  owners=list(d.execute('select * from profile_picture_archive_text_owner_occurrence where row_occurrence_id=? order by field_path',(row_id,)))
  require({o['field_path'] for o in owners}=={'nameTextMapHash','unlockDescTextMapHash'},'reuse owner denominator')
  for owner in owners:
   resolutions=list(d.execute('select * from profile_picture_archive_text_resolution_occurrence where owner_occurrence_id=? order by locale',(owner['occurrence_id'],)))
   require({r['locale'] for r in resolutions}==set(LANGUAGES),'reuse locale denominator')
   for resolution in resolutions:
    status='resolved' if resolution['resolution_status']=='resolved_unique' else ('ambiguous' if resolution['candidate_count']>1 else 'missing');states[status]+=1
    expected_reuse_slots[reuse_id,owner['field_path'],resolution['locale']]=(status,resolution['candidate_count'],owner['occurrence_id'],resolution['occurrence_id'])
 def textmap(path,i,obj,fields):
  rid=rows[path,i]['id']
  for field in fields:
   loc=str(obj[field])
   for lang in LANGUAGES:
    hits=maps[lang,''].get(loc,[]) or maps[lang,'_Medium'].get(loc,[]);status={0:'missing',1:'resolved'}.get(len(hits),'ambiguous');states[status]+=1;expected_slots[(rid,field,lang)]=(loc,status)
    for p,n,ptr,text in hits:expected_text.add((rid,field,lang,p,n,ptr,text))
 for i,x,_ in costume:textmap(COSTUME,i,x,('nameTextMapHash','descTextMapHash'))
 for i,x,_ in profile:
  if i not in PROFILE_REUSED_ORDINALS:textmap(PROFILE,i,x,('nameTextMapHash','unlockDescTextMapHash'))
 for i,x,_ in rename:
  rid=rows[RENAME,i]['id'];by=defaultdict(list)
  for n,item in enumerate(x['multiLangDefaultNameList']):by[item.get('languageType')].append((n,item))
  for lang,key in [('CHS','LANGUAGE_SC'),('EN','LANGUAGE_EN'),('JP','LANGUAGE_JP')]:
   hits=by[key];status={0:'missing',1:'resolved'}.get(len(hits),'ambiguous');states[status]+=1;expected_slots[(rid,'multiLangDefaultNameList',lang)]=(key,status)
   for n,item in hits:expected_text.add((rid,'multiLangDefaultNameList',lang,RENAME,n,f'/{i}/multiLangDefaultNameList/{n}/planeText',item['planeText']))
 slots={(r['row_id'],r['field'],r['locale']):(r['raw_locator'],r['status'],r['id']) for r in c.execute('select * from character_lore_slot')};require({k:v[:2] for k,v in slots.items()}==expected_slots,'slot partition')
 reuse_slots={(r['reuse_id'],r['field'],r['locale']):(r['status'],r['candidate_count'],r['target_owner_occurrence_id'],r['target_resolution_occurrence_id']) for r in c.execute('select * from character_lore_reuse_slot')};require(reuse_slots==expected_reuse_slots,'accepted reuse slot partition')
 actual_text={(s['row_id'],s['field'],s['locale'],t['source_path'],t['pair_ordinal'],t['pointer'],t['raw_text']) for t in c.execute('select * from character_lore_text') for s in [c.execute('select * from character_lore_slot where id=?',(t['slot_id'],)).fetchone()]};require(actual_text==expected_text,'text candidates')
 # Relation namespaces are independently enumerated from complete physical arrays.
 costumes_by_skin=defaultdict(list)
 for i,x,_ in costume:costumes_by_skin[x['skinId']].append((i,x))
 expected_rel=[]
 for i,x,_ in costume:expected_rel.append((rows[COSTUME,i]['id'],'costume_character',f'/{i}/characterId',x['characterId'],[(AVATAR,core_avatar[str(x['characterId'])]['source_pointer'],core_avatar[str(x['characterId'])]['id'])]))
 for i,x,_ in profile:
  if i in PROFILE_REUSED_ORDINALS:continue
  if x['AHJHBENMGFK']=='PROFILE_PICTURE_UNLOCK_BY_AVATAR':expected_rel.append((rows[PROFILE,i]['id'],'profile_unlock_avatar',f'/{i}/unlockParam',x['unlockParam'],[(AVATAR,core_avatar[str(x['unlockParam'])]['source_pointer'],core_avatar[str(x['unlockParam'])]['id'])]))
  if x['AHJHBENMGFK']=='PROFILE_PICTURE_UNLOCK_BY_COSTUME':expected_rel.append((rows[PROFILE,i]['id'],'profile_unlock_costume',f'/{i}/unlockParam',x['unlockParam'],[(COSTUME,f'/{n}',rows[COSTUME,n]['id']) for n,_ in costumes_by_skin[x['unlockParam']]]))
 for i,x,_ in rename:expected_rel.append((rows[RENAME,i]['id'],'rename_character',f'/{i}/relatedAvatarID',x['relatedAvatarID'],[(AVATAR,core_avatar[str(x['relatedAvatarID'])]['source_pointer'],core_avatar[str(x['relatedAvatarID'])]['id'])]))
 relations={(r['row_id'],r['kind'],r['source_pointer']):dict(r) for r in c.execute('select * from character_lore_relation')};require(len(relations)==len(expected_rel),'relation denominator')
 for rid,kind,ptr,value,targets in expected_rel:
  r=relations[rid,kind,ptr];require(json.loads(r['raw_value'])==value and r['candidate_count']==len(targets) and r['search_complete']==1 and r['status']==({0:'missing',1:'resolved'}.get(len(targets),'ambiguous')),'relation state')
  got={(x['target_source_path'],x['target_pointer'],x['target_occurrence_id']) for x in c.execute('select * from character_lore_candidate where relation_id=?',(r['id'],))};require(got==set(targets),'candidate set')
 # The two reused gated rows remain historical physical rows, but their
 # relationship classification belongs to this successor contract.  Ordinal 1
 # deliberately corrects the historical Avatar-namespace observation to the
 # source-authored Costume namespace without rewriting the old row.
 expected_reuse_rel=[]
 for i in (0,1):
  x=profile[i][1];reuse_id=reuse[PROFILE,i]['id'];old=d.execute('select occurrence_id from profile_picture_archive_relationship_occurrence where row_occurrence_id=? and relation_field=?',(core_profile[i]['occurrence_id'],'unlockParam')).fetchone();require(old is not None,'historical reuse relation locator')
  if x['AHJHBENMGFK']=='PROFILE_PICTURE_UNLOCK_BY_AVATAR':
   kind='profile_unlock_avatar';targets=[(AVATAR,core_avatar[str(x['unlockParam'])]['source_pointer'],core_avatar[str(x['unlockParam'])]['id'])]
  else:
   kind='profile_unlock_costume';targets=[(COSTUME,f'/{n}',rows[COSTUME,n]['id']) for n,_ in costumes_by_skin[x['unlockParam']]]
  expected_reuse_rel.append((reuse_id,kind,f'/{i}/unlockParam',x['unlockParam'],old[0],targets))
 reuse_rel={(r['reuse_id'],r['kind'],r['source_pointer']):dict(r) for r in c.execute('select * from character_lore_reuse_relation')};require(len(reuse_rel)==2,'reuse relation denominator')
 for reuse_id,kind,ptr,value,old_id,targets in expected_reuse_rel:
  r=reuse_rel[reuse_id,kind,ptr];require(json.loads(r['raw_value'])==value and r['candidate_count']==len(targets) and r['status']=='resolved' and r['search_complete']==1 and r['historical_relation_occurrence_id']==old_id,'reuse relation state')
  got={(x['target_source_path'],x['target_pointer'],x['target_occurrence_id']) for x in c.execute('select * from character_lore_reuse_candidate where relation_id=?',(r['id'],))};require(got==set(targets),'reuse candidate set')
 # Independently reconstruct the complete navigation set from Schema20 plus
 # the frozen source relations.  Exact equality detects omission, excess,
 # wrong anchors, wrong runs and wrong proof locators.
 anchors={r['avatar_id']:dict(r) for r in c.execute('select * from character_lore_navigation_anchor')};require(set(anchors)==set(core_avatar),'navigation anchors')
 for aid,a in anchors.items():require(a['target_occurrence_id']==core_avatar[aid]['id'] and a['target_run']==C5B_RUN,'anchor exact')
 expected_edges=set()
 for x in d.execute('select id,avatar_id,table_name from character_row_occurrence where ingest_run_id=?',(C5B_RUN,)):expected_edges.add((x['avatar_id'],'character_'+x['table_name'],'characters','character_phase4c5b_full_source_closure',C5B_RUN,x['id'],'accepted:character_row.avatar_id'))
 for x in d.execute('select source_row_occurrence_id,target_voice_container_id,id from character_relation_occurrence where ingest_run_id=? and target_voice_container_id is not null',(C5B_RUN,)):
  source=d.execute('select avatar_id from character_row_occurrence where ingest_run_id=? and id=?',(C5B_RUN,x['source_row_occurrence_id'])).fetchone();require(source is not None and d.execute('select 1 from character_voice_container_occurrence where ingest_run_id=? and id=?',(C5B_RUN,x['target_voice_container_id'])).fetchone(),'voice relation binding');expected_edges.add((source[0],'voice_container','characters','character_phase4c5b_full_source_closure',C5B_RUN,x['target_voice_container_id'],x['id']))
 for x in d.execute('select id,native_locator_value from avatar_codex_row_occurrence where ingest_run_id=?',(WAVE02_RUN,)):expected_edges.add((x['native_locator_value'],'avatar_codex','avatar_codex_archive','avatar-codex-archive-evidence-v1',WAVE02_RUN,x['id'],'accepted:AvatarCodex.avatarId'))
 for rid,kind,ptr,value,targets in expected_rel:
  proof=ident(run,'relation',rid,kind,ptr)
  if kind in ('costume_character','rename_character'):expected_edges.add((str(value),'new_'+kind,'character_lore_archive','character-lore-evidence-v1',run,rid,proof))
  elif kind=='profile_unlock_avatar':expected_edges.add((str(value),'new_profile_picture','character_lore_archive','character-lore-evidence-v1',run,rid,proof))
  elif kind=='profile_unlock_costume':
   for n,costume_obj in costumes_by_skin[value]:expected_edges.add((str(costume_obj['characterId']),'new_profile_picture_via_costume','character_lore_archive','character-lore-evidence-v1',run,rid,proof))
 for i in (0,1):
  x=profile[i][1];kind='profile_unlock_avatar' if x['AHJHBENMGFK']=='PROFILE_PICTURE_UNLOCK_BY_AVATAR' else 'profile_unlock_costume';aid=str(x['unlockParam']) if kind=='profile_unlock_avatar' else str(costumes_by_skin[x['unlockParam']][0][1]['characterId']);proof=ident(run,'reuse-relation',reuse[PROFILE,i]['id'],kind,f'/{i}/unlockParam')
  expected_edges.add((aid,'reused_profile_picture','profile_picture_archive','profile-picture-evidence-v1',WAVE02_RUN,core_profile[i]['occurrence_id'],proof))
 actual_edges={(r['avatar_id'],r['kind'],r['target_family'],r['target_contract'],r['target_run'],r['target_occurrence_id'],r['source_relation_id']) for r in c.execute('select a.avatar_id,e.* from character_lore_navigation_edge e join character_lore_navigation_anchor a on a.id=e.anchor_id')};require(actual_edges==expected_edges,'navigation exact edge set')
 require(not c.execute('pragma foreign_key_check').fetchall() and c.execute('pragma integrity_check').fetchone()[0]=='ok','SQLite integrity')
 seals={t:{'rows':c.execute('select count(*) from '+t).fetchone()[0],'sha256':digest([list(x) for x in c.execute('select * from '+t+' order by 1')])} for t in release['counts']};require({k:v['rows'] for k,v in seals.items()}==release['counts'],'counts')
 c.close();d.close();after=artifact.stat();require((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns) and sha(artifact)==artifact_sha,'stable')
 return {'status':'pass','artifact_sha256':artifact_sha,'run_id':run,'logical_seals':seals,'physical_delta_rows':344,'reused_rows':5,'slot_states':dict(states),'declared_text_slots':sum(states.values()),'declared_relations':len(expected_rel)+len(expected_reuse_rel),'candidate_sets_independently_reconstructed':True,'navigation_edge_set_independently_reconstructed':True,'historical_evidence_reinterpreted':False,'historical_raw_bytes_copied':False}
