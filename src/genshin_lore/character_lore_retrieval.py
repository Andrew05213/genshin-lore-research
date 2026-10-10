"""Exact-run Character lore delta and navigation provider."""
import json,sqlite3
from pathlib import Path
from .family_evidence import FamilyEvidenceResult
from .r5_quest_talk_retrieval import _fingerprint,_identity,_cursor,_decode,_filters
ROOT=Path(__file__).resolve().parents[2]
class CharacterLoreProvider:
 family='character_lore_archive';family_contract_version='character-lore-evidence-v1'
 def __init__(self,artifact,manifest):
  self.path=Path(artifact).absolute();self.manifest_path=Path(manifest).resolve();self.invalidated=False
  if self.manifest_path!=ROOT/'manifests/character-lore/runtime.json':raise ValueError('explicit tracked Character lore manifest required')
  mf=_fingerprint(self.manifest_path);self.manifest=json.loads(self.manifest_path.read_text());m=self.manifest;fp=_fingerprint(self.path)
  if m['contract']!='character-lore-runtime-v1' or fp[2]!=m['artifact']['size_bytes'] or fp[4]!=m['artifact']['sha256']:raise ValueError('Character lore artifact qualification')
  scope=ROOT/'manifests/character-lore/scope.json';sf=_fingerprint(scope)
  if sf[4]!=m['release']['scope_sha256']:raise ValueError('scope binding')
  self.scope=scope;self.bindings={self.path:fp[:4],self.manifest_path:mf[:4],scope:sf[:4]};self.run=m['release']['run_id'];self.conn=sqlite3.connect(self.path.as_uri()+'?mode=ro&immutable=1',uri=True,check_same_thread=False);self.conn.row_factory=sqlite3.Row
  tables=list(m['counts'])
  if any(self.conn.execute('select count(*) from '+t).fetchone()[0]!=m['counts'][t] for t in tables):raise ValueError('count seal')
  structure=[list(r) for r in self.conn.execute("select type,name,tbl_name,sql from sqlite_master where name not like 'sqlite_%' order by type,name")]
  if structure!=m['schema_structure'] or self.conn.execute('pragma foreign_key_check').fetchall():raise ValueError('structure/FK')
 def _stable(self):
  if self.invalidated:raise ValueError('Character lore provider invalidated')
  try:
   for p,x in self.bindings.items():
    if any(Path(str(p)+s).exists() for s in ('-wal','-shm','-journal')) or _identity(p)!=x:raise ValueError('binding drift')
  except Exception:self.invalidated=True;raise
 def capability(self):
  r=self.manifest['release'];return {'family':self.family,'family_contract_version':self.family_contract_version,'supported_operations':['list_family_evidence','get_family_evidence','search_family_text'],'supported_filters':{'list':['record_kind','boundary','native_value','avatar_id','navigation_anchor_id','navigation_kind'],'search':['locale','boundary']},'search_text_scope':'delta_owned_text_only; the five reused ProfilePicture rows retain historical text in their exact target profile_picture_archive family/run and are not searched here','eligible_runs':[{'ingest_run_id':self.run,'parser_build_id':r['parser_build_id'],'snapshot_id':r['snapshot_id'],'coverage':'complete three-boundary physical delta plus exact accepted navigation locators','corpus_complete':True,'product_accepted':self.manifest['product_accepted'],'counts':r['counts']}],'pagination':{'ordering':'physical occurrence id','cursor_scope_bound':True,'max_limit':100},'navigation_targets':{'characters':{'contract':'character_phase4c5b_full_source_closure','row_tool':'get_character_row_evidence','voice_tool':'get_character_voice_container','exact_run_required':True},'routed_families':{'operation':'get_family_evidence','exact_family_contract_run_occurrence_required':True}},'default_run':False,'latest_run':False,'no_body_copy':True}
 def _result(self,data,status='ok',run=None):return FamilyEvidenceResult(status,self.family,run or self.run,self.family_contract_version,'character_lore_wave_complete',True,data)
 def _check(self,run,contract):self._stable();return run==self.run and contract==self.family_contract_version
 def list_evidence(self,*,ingest_run_id,family_contract_version,filters=None,cursor=None,limit=50):
  if not self._check(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  if type(limit) is not int or not 1<=limit<=100:raise ValueError('limit')
  f=_filters(filters,{'record_kind','boundary','native_value','avatar_id','navigation_anchor_id','navigation_kind'});kind=f.get('record_kind','rows')
  if kind not in ('rows','reused_rows','navigation_anchors','navigation_edges'):raise ValueError('record_kind')
  scope={'run':self.run,'contract':self.family_contract_version,'operation':'list','filters':f};last=_decode(cursor,scope)
  if kind=='rows':table='character_lore_row';where=['run_id=?'];args=[self.run]
  elif kind=='reused_rows':table='character_lore_reuse';where=['run_id=?'];args=[self.run]
  elif kind=='navigation_anchors':table='character_lore_navigation_anchor';where=['run_id=?'];args=[self.run]
  else:table='character_lore_navigation_edge';where=['1=1'];args=[]
  if last:where.append('id>?');args.append(last)
  fieldmap={'boundary':'boundary','native_value':'native_value','avatar_id':'avatar_id','navigation_anchor_id':'anchor_id','navigation_kind':'kind'}
  for key,col in fieldmap.items():
   if key in f:
    if col not in {x[1] for x in self.conn.execute('pragma table_info('+table+')')}:raise ValueError(key+' invalid for '+kind)
    where.append(col+'=?');args.append(f[key])
  rows=[dict(r) for r in self.conn.execute('select * from '+table+' where '+' and '.join(where)+' order by id limit ?',(*args,limit+1))];return self._result({'record_kind':kind,'items':rows[:limit],'delivered_count':min(limit,len(rows)),'next_cursor':_cursor(scope,rows[limit-1]['id']) if len(rows)>limit else None,'partial':len(rows)>limit})
 def get_evidence(self,*,ingest_run_id,family_contract_version,occurrence_id):
  if not self._check(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  row=self.conn.execute('select * from character_lore_row where id=?',(occurrence_id,)).fetchone()
  if row:
   slots=[dict(x) for x in self.conn.execute('select * from character_lore_slot where row_id=? order by field,locale',(occurrence_id,))];texts=[dict(x) for x in self.conn.execute('select t.* from character_lore_text t join character_lore_slot s on s.id=t.slot_id where s.row_id=? order by t.id',(occurrence_id,))];rels=[dict(x) for x in self.conn.execute('select * from character_lore_relation where row_id=? order by id',(occurrence_id,))];cands=[dict(x) for x in self.conn.execute('select x.* from character_lore_candidate x join character_lore_relation r on r.id=x.relation_id where r.row_id=? order by x.id',(occurrence_id,))];return self._result({'record_kind':'row','container':dict(row),'slots':slots,'texts':texts,'relations':rels,'candidates':cands,'complete':True,'target_bodies_included':False})
  reuse=self.conn.execute('select * from character_lore_reuse where id=?',(occurrence_id,)).fetchone()
  if reuse:
   slots=[dict(x) for x in self.conn.execute('select * from character_lore_reuse_slot where reuse_id=? order by field,locale',(occurrence_id,))];rels=[dict(x) for x in self.conn.execute('select * from character_lore_reuse_relation where reuse_id=? order by id',(occurrence_id,))];cands=[dict(x) for x in self.conn.execute('select x.* from character_lore_reuse_candidate x join character_lore_reuse_relation r on r.id=x.relation_id where r.reuse_id=? order by x.id',(occurrence_id,))]
   return self._result({'record_kind':'accepted_reuse_locator','locator':dict(reuse),'slots':slots,'relations':rels,'candidates':cands,'raw_evidence_copied':False,'complete':True})
  anchor=self.conn.execute('select * from character_lore_navigation_anchor where id=?',(occurrence_id,)).fetchone()
  if anchor:
   counts={k:n for k,n in self.conn.execute('select kind,count(*) from character_lore_navigation_edge where anchor_id=? group by kind order by kind',(occurrence_id,))};return self._result({'record_kind':'navigation_anchor','container':dict(anchor),'edge_counts':counts,'edges_embedded':False,'complete':True,'continuation':{'operation':'list_family_evidence','filters':{'record_kind':'navigation_edges','navigation_anchor_id':occurrence_id}},'identity_semantics':'one physical accepted Avatar row; no logical Character created'})
  return self._result({'status':'NotFound','occurrence_id':occurrence_id},'not_found')
 def search_text(self,*,ingest_run_id,family_contract_version,query,filters=None,cursor=None,limit=50):
  if not self._check(ingest_run_id,family_contract_version):return self._result({'status':'NotFound'},'not_found',ingest_run_id)
  if not isinstance(query,str) or not query or type(limit) is not int or not 1<=limit<=100:raise ValueError('query/limit')
  f=_filters(filters,{'locale','boundary'});scope={'run':self.run,'contract':self.family_contract_version,'operation':'search','query':query,'filters':f};last=_decode(cursor,scope);sql='select t.*,s.locale,s.field,s.row_id as container_id,r.boundary from character_lore_text t join character_lore_slot s on s.id=t.slot_id join character_lore_row r on r.id=s.row_id where instr(t.raw_text,?)>0';args=[query]
  if 'locale' in f:
   if f['locale'] not in ('CHS','EN','JP'):raise ValueError('locale')
   sql+=' and s.locale=?';args.append(f['locale'])
  if 'boundary' in f:sql+=' and r.boundary=?';args.append(f['boundary'])
  if last:sql+=' and t.id>?';args.append(last)
  rows=[dict(x) for x in self.conn.execute(sql+' order by t.id limit ?',(*args,limit+1))];return self._result({'items':rows[:limit],'next_cursor':_cursor(scope,rows[limit-1]['id']) if len(rows)>limit else None})
