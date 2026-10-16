"""Evidence-first five-tool query facade over a frozen research corpus."""
from __future__ import annotations
import base64,hashlib,json,sqlite3,threading,time
from pathlib import Path
from typing import Any
from .research_index import sha256_file
from .research_navigation import relations_for
from .research_refs import decode_ref,encode_ref
from .semantic_discovery import SemanticDiscovery,SemanticUnavailable
from .research_source_context import ResearchSourceContext
from .lexical_discovery import (CorpusLexicon,dense_rerank_preserving,hybrid_rank,
                                sparse_preserving_rrf,sparse_rank)
MAX_WIRE=262_144
# The stock SDK serializes both structuredContent and the text compatibility
# payload.  Keep the data portion below 100 KiB so the full MCP envelope stays
# under the 262,144-byte contract.
SEARCH_DATA_BUDGET=90_000
class ResearchUnavailable(RuntimeError):pass
class ResearchNotFound(LookupError):pass

def _json_safe(value):
 if isinstance(value,dict):return {str(k):_json_safe(v) for k,v in sorted(value.items())}
 if isinstance(value,(list,tuple)):return [_json_safe(v) for v in value]
 if value is None or isinstance(value,(str,int,float,bool)):return value
 raise TypeError('cursor scope contains a non-JSON value')

def _sha_bound(path:Path,expected:dict[str,Any],label:str):
 if not path.exists() or path.stat().st_size!=expected.get('size_bytes') or sha256_file(path)!=expected.get('sha256'):raise ResearchUnavailable(label+' identity mismatch')

def _locale_known(path:str)->str|None:
 bits=path.split('/')
 return bits[1] if len(bits)>2 and bits[0] in ('Readable','Subtitle') and bits[1] in ('CHS','EN','JP') else None

class ResearchQuery:
 def __init__(self,manifest_path:Path,bindings_path:Path,index_path:Path,profile_path:Path|None=None,allow_prepublication_profile:bool=False):
  t0=time.perf_counter();self.manifest_path=manifest_path;self.manifest=json.loads(manifest_path.read_text());self.bindings=json.loads(bindings_path.read_text());self.corpus_ref=self.manifest['corpus_ref'];self.components={x['source_key']:x for x in self.manifest['components']};root=manifest_path.resolve().parents[2];self.allow_prepublication_profile=allow_prepublication_profile;t1=time.perf_counter()
  if profile_path is None:raise ResearchUnavailable('accepted research profile is required')
  profile=json.loads(profile_path.read_text());expected_manifest=str(manifest_path.resolve().relative_to(root));bundle=self.manifest['product_bundle']
  if not allow_prepublication_profile:
   try:current=json.loads((root/'manifests/product/current.json').read_text())
   except (OSError,json.JSONDecodeError) as exc:raise ResearchUnavailable('current research authority is unavailable') from exc
   expected_profile=current.get('research_profile')
   try:actual_profile=str(profile_path.resolve().relative_to(root))
   except ValueError:raise ResearchUnavailable('production research profile must be tracked')
   if expected_profile!=actual_profile:
    catalog_path=root/'manifests/research/published-releases-v1.json'
    catalog=json.loads(catalog_path.read_text()) if catalog_path.is_file() else {}
    if catalog.get('profiles',{}).get(actual_profile)!=sha256_file(profile_path):raise ResearchUnavailable('research profile is not the current or explicitly published tracked authority')
   for rel,seal in profile.get('acceptance',{}).items():
    if sha256_file(root/rel)!=seal.get('sha256'):raise ResearchUnavailable('research profile acceptance seal drift: '+rel)
  if profile.get('status')!='accepted' or profile.get('corpus_ref')!=self.corpus_ref or profile.get('corpus_manifest')!=expected_manifest:raise ResearchUnavailable('accepted research profile/corpus binding mismatch')
  if profile.get('evidence_product_bundle')!=bundle['path'] or profile.get('evidence_product_bundle_sha256')!=bundle['sha256'] or sha256_file(root/bundle['path'])!=bundle['sha256']:raise ResearchUnavailable('research evidence product bundle drift')
  for c in self.manifest['components']:
   if sha256_file(root/c['runtime_manifest'])!=c['runtime_manifest_sha256']:raise ResearchUnavailable('runtime manifest drift: '+c['source_key'])
  t2=time.perf_counter();self.paths={k:Path(v) for k,v in self.bindings['artifacts'].items()};self.unavailable=[];self.fingerprints={};self.sources={};self.source_lock=threading.RLock()
  for k,c in self.components.items():
   p=self.paths.get(k)
   try:_sha_bound(p,c['artifact'],k)
   except (ResearchUnavailable,AttributeError):self.unavailable.append(k);continue
   if any(Path(str(p)+s).exists() for s in ('-wal','-shm','-journal')):self.unavailable.append(k)
   else:
    s=p.stat();self.fingerprints[k]=(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,c['artifact']['sha256'])
  t3=time.perf_counter();prov=Path(self.bindings.get('provenance_artifact',''));_sha_bound(prov,self.manifest['provenance_dependency'],'R0 provenance dependency');r0db=sqlite3.connect(f'file:{prov}?mode=ro&immutable=1',uri=True);self.r0_provenance={str(p):str(s) for p,s in r0db.execute('select relative_path,source_sha256 from source_file')};r0db.close();t4=time.perf_counter()
  if not index_path.exists():raise ResearchUnavailable('derived research index is absent')
  _sha_bound(index_path,profile.get('derived_index',{}),'derived research index');self.index_path=index_path;self.index_sha256=profile['derived_index']['sha256'];self.db=sqlite3.connect(f'file:{index_path}?mode=ro&immutable=1',uri=True,check_same_thread=False);self.db.row_factory=sqlite3.Row
  rel=self.db.execute('select * from corpus_release where id=1').fetchone();msha=hashlib.sha256(manifest_path.read_bytes()).hexdigest()
  if rel is None or rel['manifest_sha256']!=msha or rel['corpus_ref']!=self.corpus_ref or rel['adapter_registry']!='research-explicit-adapters-v2':raise ResearchUnavailable('research index corpus/adapter binding mismatch')
  expected=profile['derived_index']['counts'];actual={t:self.db.execute(f'select count(*) from {t}').fetchone()[0] for t in ('source_artifact','adapter_coverage','research_container','research_text','research_relation','research_candidate')}
  if actual!=expected:raise ResearchUnavailable('research index count binding mismatch')
  for c in self.manifest['components']:
   row=self.db.execute('select artifact_family,artifact_contract,artifact_parser,artifact_run,size_bytes,sha256 from source_artifact where source_key=?',(c['source_key'],)).fetchone()
   if row is None or tuple(row)!=(c['family'],c['contract'],c['parser'],c['run'],c['artifact']['size_bytes'],c['artifact']['sha256']):raise ResearchUnavailable('research index source binding mismatch')
  t5=time.perf_counter();self.coverage=[dict(r) for r in self.db.execute('select * from adapter_coverage order by source_key,projection_kind,source_table')];self._target_contract_cache={};self._adapter_identity_cache={};self._lexical_scope_count_cache={};self.semantic=None;self.lexicon=None;self.production_retrieval=None;self.domain_map=None;self.source_context=None;self.story70=None
  context_binding=profile.get('source_context')
  if context_binding:self._enable_source_context(root/context_binding['contract_path'],context_binding['contract_sha256'],profile['derived_index']['sha256'])
  story_binding=profile.get('bounded_story_7_0')
  if story_binding:self._enable_story70(root/story_binding['release_scope_path'],story_binding['release_scope_sha256'],root/story_binding['publication_path'],story_binding['publication_sha256'])
  if profile.get('semantic_discovery'):
   semantic_config=profile['semantic_discovery'];self.semantic=self._semantic_discovery(semantic_config,profile['derived_index']['sha256'])
   lexicon_config=semantic_config.get('lexicon');lexicon_path=Path(self.bindings.get('semantic_discovery',{}).get('lexicon',''))
   if lexicon_config:
    _sha_bound(lexicon_path,lexicon_config,'semantic lexicon')
    if any(Path(str(lexicon_path)+s).exists() for s in ('-wal','-shm','-journal')):raise ResearchUnavailable('semantic lexicon sidecar present')
    self.lexicon=CorpusLexicon(lexicon_path)
    release=self.lexicon.db.execute('select * from lexical_release where id=1').fetchone()
    expected=(lexicon_config['contract'],self.corpus_ref,profile['derived_index']['sha256'],lexicon_config['normalization_version'],lexicon_config['anchor_extraction_version'],lexicon_config['stop_policy_version'],lexicon_config['surface_count'],lexicon_config['physical_occurrence_count'])
    if release is None or tuple(release[1:])!=expected:raise ResearchUnavailable('semantic lexicon release mismatch')
   production=semantic_config.get('production_retrieval')
   if production:
    contract_path=Path(production['contract_path']);contract_path=contract_path if contract_path.is_absolute() else root/contract_path
    domain_path=Path(production['domain_map_path']);domain_path=domain_path if domain_path.is_absolute() else root/domain_path
    if sha256_file(contract_path)!=production['contract_sha256'] or sha256_file(domain_path)!=production['domain_map_sha256']:raise ResearchUnavailable('sparse-first production contract drift')
    contract=json.loads(contract_path.read_text());domain_map=json.loads(domain_path.read_text())
    if contract!=production['contract'] or domain_map!=production['domain_map']:raise ResearchUnavailable('embedded sparse-first production contract mismatch')
    if contract.get('contract')!='semantic-sparse-first-production-v1' or contract.get('status') not in ('frozen_prepublication','accepted'):raise ResearchUnavailable('sparse-first production contract is not frozen')
    if contract.get('corpus_ref')!=self.corpus_ref or contract['research_index']['sha256']!=profile['derived_index']['sha256'] or contract['lexicon']['sha256']!=lexicon_config['sha256']:raise ResearchUnavailable('sparse-first corpus/lexicon binding mismatch')
    metadata_binding=semantic_config['artifacts']['metadata'];hnsw_binding=semantic_config['artifacts']['hnsw']
    if any(contract['dense']['artifact_metadata'][key]!=metadata_binding[key] for key in ('size_bytes','sha256')) or any(contract['dense']['artifact_hnsw'][key]!=hnsw_binding[key] for key in ('size_bytes','sha256')) or contract['dense']['model_id']!=semantic_config['model']['id'] or contract['dense']['model_revision']!=semantic_config['model']['revision'] or contract['dense']['model_tree_sha256']!=semantic_config['model']['tree_sha256'] or contract['dense']['representation_version']!=semantic_config['representation_version'] or contract['dense']['distance_metric']!=semantic_config['metric']:raise ResearchUnavailable('sparse-first dense binding mismatch')
    if contract['domain_map']['sha256']!=production['domain_map_sha256'] or domain_map.get('contract')!='research-discovery-domain-map-v1' or domain_map.get('relation_semantics')!='none' or domain_map.get('identity_semantics')!='none':raise ResearchUnavailable('sparse-first domain map mismatch')
    self.production_retrieval=contract;self.domain_map=domain_map
  t6=time.perf_counter();self.timings={'corpus_profile_parse':t1-t0,'product_bundle_runtime_manifest_verification':t2-t1,'source_artifact_sha_qualification':t3-t2,'r0_provenance_qualification':t4-t3,'derived_index_qualification':t5-t4,'schema_count_logical_seals':t5-t4,'adapter_capability_generation':t6-t5,'total':t6-t0}
 def _semantic_discovery(self,config,index_sha256):
  return SemanticDiscovery(config,self.bindings,self.corpus_ref,index_sha256)
 def close(self):
  for c in self.sources.values():c.close()
  if self.lexicon:self.lexicon.close()
  if self.semantic:self.semantic.close()
  self.db.close()
 def _enable_source_context(self,contract_path,expected_sha,index_sha):
  if sha256_file(contract_path)!=expected_sha:raise ResearchUnavailable('source-context contract drift')
  contract=json.loads(contract_path.read_text())
  key=contract.get('document_component')
  if contract.get('contract')!='research-source-context-v1' or contract.get('status')!='accepted_derived_display_context' or contract.get('supported_families')!=['document_archive','readable_archive'] or contract.get('corpus_ref')!=self.corpus_ref or key not in self.components or self.components[key]['family']!='document_archive' or contract.get('research_index_sha256')!=index_sha:raise ResearchUnavailable('source-context binding mismatch')
  self.source_context=ResearchSourceContext(self.db,self._source(key))
 def enable_source_context_prepublication(self,contract_path):
  if not self.allow_prepublication_profile:raise ResearchUnavailable('prepublication source context is not available in production')
  self._enable_source_context(contract_path,sha256_file(contract_path),self.index_sha256)
 def _enable_story70(self,release_scope_path,expected_sha,publication_path=None,publication_sha=None):
  if sha256_file(release_scope_path)!=expected_sha:raise ResearchUnavailable('bounded story release-scope seal drift')
  if publication_path is not None:
   if sha256_file(publication_path)!=publication_sha:raise ResearchUnavailable('bounded story publication seal drift')
   publication=json.loads(publication_path.read_text())
   if publication.get('contract')!='research-release-scope-publication-v1' or publication.get('status')!='accepted_derived_7_0_bounded_scope' or publication.get('corpus_ref')!=self.corpus_ref or publication.get('release_scope_sha256')!=expected_sha or publication.get('source_snapshot')!=self.manifest['snapshot']['id'] or publication.get('coverage_complete') is not False or publication.get('7_1_product_accepted') is not False:raise ResearchUnavailable('bounded story publication binding mismatch')
  from .research_story_7_0 import QuestTalk70Story
  self.story70=QuestTalk70Story(self,release_scope_path)
 def enable_story70_prepublication(self,release_scope_path):
  if not self.allow_prepublication_profile:raise ResearchUnavailable('prepublication bounded story is not available in production')
  self._enable_story70(release_scope_path,sha256_file(release_scope_path))
 def _require(self,corpus):
  if corpus!=self.corpus_ref:raise ResearchNotFound('unknown exact corpus_ref')
 def _source(self,key):
  if key in self.unavailable:raise ResearchUnavailable('source scope unavailable: '+key)
  p=self.paths[key];s=p.stat();now=(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,self.components[key]['artifact']['sha256'])
  if now!=self.fingerprints.get(key) or any(Path(str(p)+z).exists() for z in ('-wal','-shm','-journal')):raise ResearchUnavailable('source scope drifted: '+key)
  with self.source_lock:
   if key not in self.sources:self.sources[key]=sqlite3.connect(f'file:{p}?mode=ro&immutable=1',uri=True,check_same_thread=False);self.sources[key].row_factory=sqlite3.Row
   return self.sources[key]
 def _cursor(self,scope,after):
  body=json.dumps({'v':2,'corpus':self.corpus_ref,'scope':_json_safe(scope),'after':_json_safe(after)},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode();seal=hashlib.sha256(b'research-cursor-v2\0'+body).digest()[:16];return base64.urlsafe_b64encode(body+seal).decode().rstrip('=')
 def _after(self,cursor,scope):
  if cursor is None:return 0
  try:
   raw=base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4));body,seal=raw[:-16],raw[-16:]
   if hashlib.sha256(b'research-cursor-v2\0'+body).digest()[:16]!=seal:raise ValueError
   x=json.loads(body);after=x.pop('after')
   if x!={'v':2,'corpus':self.corpus_ref,'scope':_json_safe(scope)}:raise ValueError
   if not ((isinstance(after,int) and not isinstance(after,bool) and after>=0) or isinstance(after,dict)):raise ValueError
   return after
  except Exception as exc:raise ValueError('cursor scope mismatch') from exc
 def _container_ref(self,row):return encode_ref({'v':2,'kind':'container','corpus':self.corpus_ref,'source':row['source_key'],'family':row['evidence_family'],'contract':row['evidence_contract'],'parser':row['evidence_parser'],'run':row['evidence_run'],'occurrence':row['occurrence_id'],'index':row['id']})
 def _text_ref(self,row):return encode_ref({'v':2,'kind':'text','corpus':self.corpus_ref,'source':row['source_key'],'family':row['evidence_family'],'contract':row['evidence_contract'],'parser':row['evidence_parser'],'run':row['evidence_run'],'occurrence':row['occurrence_id'],'index':row['id'],'child':row['child_id']})
 def _decorate_context(self,item,row,container=None):
  if self.source_context is None:return item
  if container is None:container=self.db.execute('select * from research_container where id=?',(row['container_id'],)).fetchone()
  context=self.source_context.for_evidence(container,row)
  item['source_context']=context;item['citation_label']=context['citation_label']
  return item
 def _container_for(self,ref):
  self._require(ref['corpus']);row=self.db.execute('select * from research_container where id=? and source_key=? and evidence_family=? and evidence_contract=? and evidence_parser=? and evidence_run=? and occurrence_id=?',(ref['index'],ref['source'],ref['family'],ref['contract'],ref['parser'],ref['run'],ref['occurrence'])).fetchone()
  if row is None:row=self._adapter_container_for(ref)
  if row is None:raise ResearchNotFound('container evidence_ref is outside frozen corpus')
  self._recheck_container(row);return row
 def _adapter_index(self,source,run,occurrence):return int.from_bytes(hashlib.sha256(('adapter-container\0'+source+'\0'+run+'\0'+occurrence).encode()).digest()[:8],'big') or 1
 def _adapter_container_for(self,ref):
  if ref['source']!='schema20-core' or not ref['occurrence'].startswith('character-voice-container:') or ref['index']!=self._adapter_index(ref['source'],ref['run'],ref['occurrence']):return None
  identity=self._accepted_adapter_identity(ref['family'],ref['run'])
  if identity is None or (ref['family'],ref['contract'],ref['parser'])!=identity:return None
  src=self._source('schema20-core');row=src.execute('select * from character_voice_container_occurrence where id=? and ingest_run_id=?',(ref['occurrence'],ref['run'])).fetchone()
  if row is None:return None
  file=src.execute('select relative_path,sha256,upstream_blob_oid from source_file where id=?',(row['item_source_file_id'],)).fetchone()
  if file is None:return None
  return {'id':ref['index'],'source_key':'schema20-core','adapter_id':'runtime:character_voice_container_occurrence','evidence_family':ref['family'],'evidence_contract':ref['contract'],'evidence_parser':ref['parser'],'evidence_run':ref['run'],'occurrence_id':ref['occurrence'],'container_kind':'character_voice_container','source_table':'character_voice_container_occurrence','pk_column':'id','pk_value':ref['occurrence'],'source_path':file[0],'source_pointer':row['item_source_pointer'],'source_file_sha256':file[1],'source_git_blob':file[2],'ordinal':None,'ordering_basis':None,'adapter_runtime':True}
 def _accepted_adapter_identity(self,family,run):
  key=(family,run)
  if key not in self._adapter_identity_cache:
   rows=self.db.execute('select distinct evidence_family,evidence_contract,evidence_parser from research_container where evidence_family=? and evidence_run=?',(family,run)).fetchall()
   self._adapter_identity_cache[key]=tuple(rows[0]) if len(rows)==1 else None
  return self._adapter_identity_cache[key]
 def _adapter_target_ref(self,c):
  occurrence=c['target_occurrence_id'];run=c['target_run']
  if not c.get('_row_family_alias') or c.get('target_family')!='characters' or not occurrence or not run or not str(occurrence).startswith('character-voice-container:'):return None
  family=c['target_contract'];identity=self._accepted_adapter_identity(family,run)
  if identity is None:return None
  if c.get('target_parser') and c['target_parser']!=identity[2]:return None
  ref={'v':2,'kind':'container','corpus':self.corpus_ref,'source':'schema20-core','family':identity[0],'contract':identity[1],'parser':identity[2],'run':run,'occurrence':occurrence,'index':self._adapter_index('schema20-core',run,occurrence)}
  # Resolve only after the accepted source row and its provenance exist.
  return encode_ref(ref) if self._adapter_container_for(ref) is not None else None
 def _text_for(self,ref):
  self._require(ref['corpus']);row=self.db.execute('select * from research_text where id=? and source_key=? and evidence_family=? and evidence_contract=? and evidence_parser=? and evidence_run=? and occurrence_id=? and child_id=?',(ref['index'],ref['source'],ref['family'],ref['contract'],ref['parser'],ref['run'],ref['occurrence'],ref['child'])).fetchone()
  if row is None:raise ResearchNotFound('text evidence_ref is outside frozen corpus')
  self._recheck_text(row);return row
 def _recheck_container(self,row):
  src=self._source(row['source_key']);table=row['source_table']
  if not table.replace('_','').isalnum():raise ResearchUnavailable('unsafe adapter table')
  got=src.execute(f'select * from "{table}" where "{row["pk_column"]}"=?',(row['pk_value'],)).fetchone()
  if got is None:raise ResearchUnavailable('derived container failed accepted evidence recheck')
  cols=set(got.keys())
  if isinstance(row,dict) and row.get('adapter_runtime'):
   file=src.execute('select relative_path,sha256 from source_file where id=?',(got['item_source_file_id'],)).fetchone()
   if file is None or str(file[0])!=row['source_path'] or str(file[1])!=row['source_file_sha256'] or str(got['item_source_pointer'])!=row['source_pointer']:raise ResearchUnavailable('runtime adapter container provenance drift')
   return got
  if 'source_path' in cols and str(got['source_path'])!=row['source_path']:raise ResearchUnavailable('container source path drift')
  if 'source_file_path' in cols and str(got['source_file_path'])!=row['source_path']:raise ResearchUnavailable('container source path drift')
  if 'pointer' in cols and str(got['pointer'])!=row['source_pointer']:raise ResearchUnavailable('container pointer drift')
  if 'source_pointer' in cols and str(got['source_pointer'])!=row['source_pointer']:raise ResearchUnavailable('container pointer drift')
  for name in ('source_sha256','source_file_sha256','sha256'):
   if name in cols and got[name] is not None and str(got[name])!=row['source_file_sha256']:raise ResearchUnavailable('container source SHA drift')
  if row['source_path'] in self.r0_provenance and self.r0_provenance[row['source_path']]!=row['source_file_sha256']:raise ResearchUnavailable('container R0 provenance drift')
  return got
 def _recheck_text(self,row):
  self._recheck_container(self.db.execute('select * from research_container where id=?',(row['container_id'],)).fetchone());src=self._source(row['source_key']);table=row['source_table']
  if not table.replace('_','').isalnum():raise ResearchUnavailable('unsafe adapter table')
  if row['adapter_id']=='codexquest:text_candidate':
   got=src.execute('select o.* from codexquest_text_candidate c join codexquest_text_occurrence o on o.id=c.text_occurrence_id where c.id=?',(row['pk_value'],)).fetchone()
  else:got=src.execute(f'select * from "{table}" where "{row["pk_column"]}"=?',(row['pk_value'],)).fetchone()
  if got is None or row['text_column'] not in got.keys() or str(got[row['text_column']])!=row['raw_text'] or hashlib.sha256(str(got[row['text_column']]).encode()).hexdigest()!=row['text_sha256']:raise ResearchUnavailable('derived text failed exact evidence recheck')
  for col,val in (('pointer',row['text_source_pointer']),('source_pointer',row['text_source_pointer']),('locale',row['locale']),('pair_ordinal',row['physical_pair_ordinal'])):
   if col in got.keys() and val is not None and str(got[col])!=str(val):raise ResearchUnavailable('derived text coordinate drift: '+col)
  if row['text_source_path'] in self.r0_provenance and self.r0_provenance[row['text_source_path']]!=row['text_source_file_sha256']:raise ResearchUnavailable('text R0 provenance drift')
  return str(got[row['text_column']])
 def _scope_coverage(self,families=None,locale=None):
  keys=set(self.components)
  unknown_families=[]
  if families:
   known={r[0] for r in self.db.execute('select distinct evidence_family from research_container')};unknown_families=sorted(set(families)-known)
   keys={r[0] for r in self.db.execute('select distinct source_key from research_container where evidence_family in (%s)'%','.join('?'*len(families)),tuple(families))}
  rows=[x for x in self.coverage if x['source_key'] in keys and x['projection_kind']=='text'];searched=sorted({x['source_key'] for x in rows if x['status']=='complete'});unsupported=sorted({x['source_key'] for x in rows if x['status']=='unsupported'});unavailable=sorted(set(self.unavailable)&keys);unknown=[];filtered=[]
  if locale:
   filtered=sorted({r[0] for r in self.db.execute('select distinct source_key from research_text where locale=?',(locale,)) if r[0] in keys})
   unknown=sorted({r[0] for r in self.db.execute('select distinct source_key from research_text where locale is null') if r[0] in keys})
  unsupported.extend('family:'+x for x in unknown_families)
  return searched,sorted(set(unsupported)),unavailable,filtered,unknown
 def describe(self,corpus_ref,detail=None):
  self._require(corpus_ref);groups=[]
  for c in self.manifest['components']:
   cov=[x for x in self.coverage if x['source_key']==c['source_key']];text_unsupported=[x['source_table'] for x in cov if x['projection_kind']=='text' and x['status']=='unsupported'];tables=[x['source_table'] for x in cov if x['projection_kind']=='text' and x['status']=='complete'];n=sum(x['denominator'] for x in cov if x['projection_kind']=='text');indexed=sum(x['indexed_count'] for x in cov if x['projection_kind']=='text')
   relation_unsupported=[x['source_table'] for x in cov if x['projection_kind'] in ('relation','candidate') and x['status']=='unsupported']
   search_complete=not text_unsupported and n==indexed;groups.append({'artifact_source_key':c['source_key'],'artifact_family':c['family'],'searchable_tables':tables,'unsupported_tables':text_unsupported,'text_occurrence_denominator':n,'indexed_occurrences':indexed,'coverage_complete':search_complete,'search_coverage_complete':search_complete,'relation_coverage_complete':not relation_unsupported,'overall_projection_complete':search_complete and not relation_unsupported,'unsupported_relation_tables':relation_unsupported,'available':c['source_key'] not in self.unavailable})
  modes=['literal'];modes+=['lexical'] if self.lexicon else [];modes+=['semantic','hybrid'] if self.semantic else []
  return {'corpus_ref':self.corpus_ref,'snapshot':self.manifest['snapshot'],'product_acceptance':self.manifest['product_bundle'],'components':groups,'search_semantics':{'default_mode':'literal','modes':modes,'literal':'case_sensitive_literal','lexical':'deterministic corpus-backed physical candidate discovery' if self.lexicon else None,'semantic':'candidate discovery only; never evidence truth or relation inference','derived_index_is_truth':False,'exact_source_recheck':True,'semantic_coverage':self.semantic.coverage() if self.semantic else None},'navigation':{'containers':self.db.execute('select count(*) from research_container').fetchone()[0],'relations':self.db.execute('select count(*) from research_relation').fetchone()[0],'runtime_exact_adapters':['character_lore_navigation_edge','npc_dialogue_reverse_candidate','npc_dialogue_to_talk_container']},'terminal_unresolved':self.manifest.get('terminal_unresolved'),'outer_corpus_complete':False,'detail':detail}
 def _resolve_domain(self,query,domain):
  if self.domain_map is None:
   if domain is not None:raise ValueError('domain filtering unavailable')
   return None,None,[]
  domains=self.domain_map['domains']
  if domain is not None:
   if domain not in domains:raise ValueError('unknown discovery domain')
   return domain,'explicit_api_domain',list(domains[domain]['families'])
  matches=[name for name,value in domains.items() if any(marker in query for marker in value['query_markers'])]
  if len(matches)==1:return matches[0],'deterministic_lexical_intent',list(domains[matches[0]]['families'])
  return None,'ambiguous_or_absent_intent',[]
 def search(self,corpus_ref,query,locale=None,families=None,source_prefix=None,limit=20,cursor=None,mode='literal',domain=None,story_release=None):
  self._require(corpus_ref)
  if not isinstance(query,str) or not query:raise ValueError('query required')
  if story_release is not None:
   if story_release!='7.0' or self.story70 is None:raise ResearchUnavailable('exact bounded story release unavailable')
   if mode!='literal' or locale not in (None,'CHS') or families or source_prefix is not None or domain is not None:raise ValueError('bounded story accepts only a CHS literal query and exact release')
   return self.story70.search(query,limit,cursor)
  if mode not in ('literal','lexical','semantic','hybrid'):raise ValueError('unknown search mode')
  if domain is not None and mode!='hybrid':raise ValueError('domain is supported only for sparse-first hybrid discovery')
  if mode!='literal':return self._discovery_search(query,locale,families,source_prefix,limit,cursor,mode,domain)
  limit=max(1,min(int(limit),100));families=sorted(set(families or ()));scope={'op':'search','q':query,'locale':locale,'families':families,'source_prefix':source_prefix,'limit':limit};after=self._after(cursor,scope);legacy_cursor=cursor is not None and isinstance(after,int);where=[];args=[]
  if locale:where.append('t.locale=?');args.append(locale)
  if families:where.append('t.evidence_family in (%s)'%','.join('?'*len(families)));args.extend(families)
  if source_prefix is not None:where.append('substr(t.text_source_path,1,length(?))=?');args.extend((source_prefix,source_prefix))
  owner="(lower(coalesce(t.owner_field,'')) like '%name%' or lower(coalesce(t.owner_field,'')) like '%title%')"
  canonical="(t.evidence_family in ('npc_archive','document_archive') or t.evidence_family like 'character_phase4c5b%')"
  rank=f"case when t.raw_text=? and {owner} and {canonical} then 0 when t.raw_text=? and {owner} then 1 when t.raw_text=? then 2 when substr(t.raw_text,1,length(?))=? and {owner} then 3 when {owner} then 4 else 5 end"
  if legacy_cursor:
   where.insert(0,'t.id>?');args.insert(0,after);rank='4';rank_args=[];order='t.id'
  else:
   if after==0:after={'rank':-1,'id':0}
   if not isinstance(after,dict) or set(after)!= {'rank','id'}:raise ValueError('cursor position mismatch')
   where.append('(research_rank>? or (research_rank=? and id>?))');args.extend((int(after['rank']),int(after['rank']),int(after['id'])));rank_args=[query,query,query,query,query];order='research_rank,id'
  filters=' and '.join(where) if where else '1=1'
  if len(query)>=3 and all(ch.isalnum() or ch.isspace() for ch in query):inner=f'select t.*,{rank} research_rank from research_text_fts f join research_text t on t.id=f.rowid where f.raw_text match ?';sql=f'select * from ({inner}) t where {filters} order by {order} limit ?';args=[*rank_args,'"'+query.replace('"','""')+'"',*args,limit+1]
  else:inner=f'select t.*,{rank} research_rank from research_text t where instr(t.raw_text,?)>0';sql=f'select * from ({inner}) t where {filters} order by {order} limit ?';args=[*rank_args,query,*args,limit+1]
  rows=self.db.execute(sql,args).fetchall();items=[]
  for row in rows[:limit]:
   raw=self._recheck_text(row)
   if query not in raw:raise ResearchUnavailable('candidate recall failed exact literal recheck')
   pos=raw.find(query);basis=('exact_canonical_name_or_title' if row['research_rank']==0 else 'exact_name_or_title' if row['research_rank']==1 else 'exact_literal' if row['research_rank']==2 else 'name_or_title_prefix' if row['research_rank']==3 else 'name_or_title_contains' if row['research_rank']==4 else 'literal_body_match');item={'evidence_ref':self._text_ref(row),'container_ref':self._container_ref(self.db.execute('select * from research_container where id=?',(row['container_id'],)).fetchone()),'family':row['evidence_family'],'contract':row['evidence_contract'],'exact_run':row['evidence_run'],'source_path':row['text_source_path'],'physical_pointer':row['text_source_pointer'],'locale':row['locale'],'owner_field':row['owner_field'],'snippet':raw[max(0,pos-80):pos+len(query)+80],'match_span':[pos,pos+len(query)],'provenance_ref':self._text_ref(row),'ranking_basis':basis,'recommended_next_actions':['read_evidence','inspect_evidence','follow_evidence']}
   item=self._decorate_context(item,row)
   if len(json.dumps({'items':items+[item]},ensure_ascii=False,separators=(',',':')).encode())>SEARCH_DATA_BUDGET:break
   items.append(item)
  searched,unsupported,unavailable,filtered,unknown=self._scope_coverage(families or None,locale)
  last_row=items and rows[len(items)-1];last=(int(last_row['id']) if legacy_cursor else {'rank':int(last_row['research_rank']),'id':int(last_row['id'])}) if last_row else None;more=len(rows)>len(items)
  return {'items':items,'delivered_count':len(items),'next_cursor':self._cursor(scope,last) if more and last is not None else None,'scope_complete':not more,'searched_scopes':searched,'unsupported_scopes':unsupported,'unavailable_scopes':unavailable,'locale_filtered_scopes':filtered,'locale_unknown_scopes':unknown,'partial':bool(unsupported or unavailable or (locale and unknown)),'ranking':{'mode':'deterministic_literal_research_priority','preserves_physical_occurrences':True}}
 def _sparse_candidates(self,query,locale,families,source_prefix):
  if self.lexicon is None:raise ResearchUnavailable('lexical discovery overlay unavailable')
  extracted=self.lexicon.extract(query)
  if locale not in (None,'CHS'):return extracted,[],0,{}
  anchors=[item['surface_form'] for item in extracted['recognized_anchors']]
  if not anchors:return extracted,[],0,{}
  where=["t.locale='CHS'"];args=[]
  if families:where.append('t.evidence_family in (%s)'%','.join('?'*len(families)));args.extend(families)
  if source_prefix is not None:where.append('substr(t.text_source_path,1,length(?))=?');args.extend((source_prefix,source_prefix))
  base=' and '.join(where);scope_key=(tuple(families),source_prefix)
  if scope_key not in self._lexical_scope_count_cache:self._lexical_scope_count_cache[scope_key]=self.db.execute('select count(*) from research_text t where '+base,args).fetchone()[0]
  scope_count=self._lexical_scope_count_cache[scope_key]
  anchor_where=' or '.join('instr(t.raw_text,?)>0' for _ in anchors)
  rows=[dict(row) for row in self.db.execute('select t.* from research_text t where '+base+' and ('+anchor_where+') order by t.id',(*args,*anchors))]
  document_frequency={anchor:sum(anchor in row['raw_text'] for row in rows) for anchor in anchors}
  ranked=sparse_rank(query,extracted,rows,True,scope_count=scope_count,anchor_document_frequency=document_frequency)
  return extracted,ranked,scope_count,document_frequency
 def _rank_discovery_candidates(self,query,ranked,mode,domain=None):
  return ranked  # Optional candidate-only hints; accepted profiles remain unchanged.
 def _discovery_search(self,query,locale,families,source_prefix,limit,cursor,mode,domain=None):
  if mode in ('semantic','hybrid') and self.semantic is None:raise ResearchUnavailable('semantic discovery overlay unavailable')
  if mode in ('lexical','hybrid') and self.lexicon is None:
   if mode=='hybrid':return self._legacy_semantic_search(query,locale,families,source_prefix,limit,cursor,mode)
   raise ResearchUnavailable('lexical discovery overlay unavailable')
  if mode=='hybrid' and self.production_retrieval is not None:return self._production_hybrid_search(query,locale,families,source_prefix,limit,cursor,domain)
  limit=max(1,min(int(limit),100));families=sorted(set(families or ()));scope={'op':'search','mode':mode,'q':query,'locale':locale,'families':families,'source_prefix':source_prefix,'limit':limit,'domain':domain};state=self._after(cursor,scope)
  if state==0:offset=0
  elif isinstance(state,dict) and set(state)=={'offset'} and isinstance(state['offset'],int) and not isinstance(state['offset'],bool) and state['offset']>=0:offset=state['offset']
  else:raise ValueError('discovery cursor position mismatch')
  extracted={'recognized_anchors':[],'unrecognized_spans':query,'lexicon_coverage':0.0,'candidate_generation_complete':False,'partial':True}
  sparse=[];scope_count=0;document_frequency={}
  if mode in ('lexical','hybrid'):
   extracted,sparse,scope_count,document_frequency=self._sparse_candidates(query,locale,families,source_prefix)
  semantic_hits=[];semantic_rows={};semantic_score={}
  if mode in ('semantic','hybrid') and locale in (None,'CHS'):
   raw_hits=self.semantic.candidates(query)
   hit_ids=[int(hit['research_text_id']) for hit in raw_hits]
   for start in range(0,len(hit_ids),800):
    chunk=hit_ids[start:start+800]
    if chunk:
     semantic_rows.update({int(row['id']):row for row in self.db.execute('select * from research_text where id in ('+','.join('?'*len(chunk))+')',chunk)})
   for hit in raw_hits:
    text_id=int(hit['research_text_id']);row=semantic_rows.get(text_id)
    if row is None:raise ResearchUnavailable('semantic occurrence is outside frozen research index')
    if row['locale']!='CHS' or (families and row['evidence_family'] not in families) or (source_prefix is not None and not row['text_source_path'].startswith(source_prefix)):continue
    semantic_hits.append(text_id);semantic_score[text_id]=float(hit['semantic_score'])
  if mode=='lexical':
   ranked=[{**item,'lexical_rank':item['sparse_rank'],'semantic_rank':None,'final_rank':item['sparse_rank'],'final_score':item['sparse_score'],'retrieval_mode':'lexical_candidate','discovery_only':True,'relation':None} for item in sparse]
  elif mode=='semantic':
   ranked=[{'research_text_id':text_id,'matched_lexical_anchors':[],'sparse_rank':None,'lexical_rank':None,'semantic_rank':rank,'final_rank':rank,'final_score':semantic_score[text_id],'ranking_basis':'cosine candidate relevance; ranking signal only','retrieval_mode':'semantic_candidate','discovery_only':True,'relation':None} for rank,text_id in enumerate(semantic_hits,1)]
  else:
   ranked=hybrid_rank(sparse,semantic_hits,'weighted')
   for item in ranked:item['lexical_rank']=item['sparse_rank'];item['retrieval_mode']='hybrid'
  ranked=self._rank_discovery_candidates(query,ranked,mode,domain)
  page=[]
  for ranked_item in ranked[offset:offset+limit]:
   row=semantic_rows.get(ranked_item['research_text_id']) or self.db.execute('select * from research_text where id=?',(ranked_item['research_text_id'],)).fetchone()
   if row is None:raise ResearchUnavailable('discovery occurrence is outside frozen research index')
   raw=self._recheck_text(row);ref=self._text_ref(row);container=self.db.execute('select * from research_container where id=?',(row['container_id'],)).fetchone()
   item={'evidence_ref':ref,'container_ref':self._container_ref(container),'family':row['evidence_family'],'contract':row['evidence_contract'],'exact_run':row['evidence_run'],'source':{'source_key':row['source_key'],'path':row['text_source_path'],'pointer':row['text_source_pointer']},'source_path':row['text_source_path'],'physical_pointer':row['text_source_pointer'],'locale':row['locale'],'owner_field':row['owner_field'],'snippet':raw[:180],'match_span':None,'provenance_ref':ref,'retrieval_mode':ranked_item['retrieval_mode'],'matched_lexical_anchors':ranked_item.get('matched_lexical_anchors',[]),'literal_match_fields':[match.get('match_field') for match in ranked_item.get('matched_lexical_anchors',[])],'lexical_rank':ranked_item.get('lexical_rank'),'sparse_rank':ranked_item.get('sparse_rank'),'semantic_rank':ranked_item.get('semantic_rank'),'final_rank':ranked_item['final_rank'],'ranking_basis':ranked_item['ranking_basis'],'semantic_score':semantic_score.get(int(row['id'])),'hybrid_score':ranked_item.get('final_score') if mode=='hybrid' else None,'discovery_only':True,'relation':None,'recommended_next_actions':['read_evidence','inspect_evidence','follow_evidence']}
   if 'relevance_hints' in ranked_item:item['relevance_hints']=ranked_item['relevance_hints']
   item=self._decorate_context(item,row,container)
   if len(json.dumps({'items':page+[item]},ensure_ascii=False,separators=(',',':')).encode())>SEARCH_DATA_BUDGET:break
   page.append(item)
  if ranked[offset:offset+limit] and not page:raise ResearchUnavailable('one discovery search item exceeds research wire budget')
  end=offset+len(page);more=end<len(ranked);next_cursor=self._cursor(scope,{'offset':end}) if more else None
  searched,unsupported,unavailable,filtered,unknown=self._scope_coverage(families or None,locale)
  ann_partial=mode in ('semantic','hybrid');locale_partial=locale not in (None,'CHS');partial=bool(unsupported or unavailable or (locale and unknown) or ann_partial or locale_partial)
  semantic_coverage=self.semantic.coverage() if self.semantic else None
  semantic_unsupported=(['non-CHS locales','ANN candidate discovery is bounded'] if ann_partial else [])
  if locale_partial:semantic_unsupported.append('semantic locale:'+str(locale))
  return {'items':page,'delivered_count':len(page),'next_cursor':next_cursor,'scope_complete':not more and not partial,'candidate_retrieval_complete':not ann_partial and not locale_partial,'literal_phase_complete':mode in ('lexical','hybrid'),'semantic_phase_complete':mode=='lexical' or not more,'partial':partial,'recognized_anchors':extracted.get('recognized_anchors',[]),'unrecognized_spans':extracted.get('unrecognized_spans',''),'lexicon_coverage':extracted.get('lexicon_coverage',0.0),'lexical_candidate_count':len(sparse),'lexical_scope_count':scope_count,'anchor_document_frequency':document_frequency,'semantic_candidates_examined':len(semantic_hits),'semantic_index_coverage':semantic_coverage,'semantic_indexed_scopes':semantic_coverage.get('semantic_indexed_scopes',{'locale':'CHS'}) if semantic_coverage else None,'semantic_unsupported_scopes':semantic_unsupported,'semantic_unavailable_scopes':unavailable,'searched_scopes':searched,'unsupported_scopes':sorted(set(unsupported+semantic_unsupported)),'unavailable_scopes':unavailable,'locale_filtered_scopes':filtered,'locale_unknown_scopes':unknown,'ranking':{'mode':mode,'fusion':'h2-weighted-sparse-plus-dense-rank-v1' if mode=='hybrid' else None,'semantic_score_is_confidence':False,'semantic_score_is_relation_probability':False,'semantic_score_establishes_identity':False,'preserves_physical_occurrences':True,'exact_accepted_evidence_recheck':True}}
 def _production_hybrid_search(self,query,locale,families,source_prefix,limit,cursor,domain):
  started=time.perf_counter();config=self.production_retrieval;limit=max(1,min(int(limit),100));requested_families=sorted(set(families or ()))
  resolved_domain,intent_source,domain_families=self._resolve_domain(query,domain)
  if resolved_domain:
   effective_families=sorted(set(domain_families)&set(requested_families)) if requested_families else sorted(set(domain_families))
  else:effective_families=requested_families
  scope={'op':'search','mode':'hybrid','production_contract':config['contract'],'q':query,'locale':locale,'families':requested_families,'effective_families':effective_families,'source_prefix':source_prefix,'limit':limit,'domain':resolved_domain,'domain_intent_source':intent_source};state=self._after(cursor,scope)
  if state==0:offset=0
  elif isinstance(state,dict) and set(state)=={'offset'} and isinstance(state['offset'],int) and not isinstance(state['offset'],bool) and state['offset']>=0:offset=state['offset']
  else:raise ValueError('discovery cursor position mismatch')
  sparse_started=time.perf_counter()
  empty_intersection=bool(resolved_domain and requested_families and not effective_families)
  if empty_intersection:extracted,sparse,scope_count,document_frequency=self.lexicon.extract(query),[],0,{}
  else:extracted,sparse,scope_count,document_frequency=self._sparse_candidates(query,locale,effective_families,source_prefix)
  sparse_seconds=time.perf_counter()-sparse_started;embedding_seconds=ann_seconds=rerank_seconds=0.0;semantic_score={};dense_rank={};ranked=[];ann_examined=0
  if resolved_domain:
   sparse_pool=sparse[:int(config['domain_route']['sparse_candidate_count'])]
   embedding_started=time.perf_counter();vector=self.semantic.query_vector(query);embedding_seconds=time.perf_counter()-embedding_started
   ann_started=time.perf_counter();raw_hits=self.semantic.candidates_from_vector(vector,int(config['dense']['ann_candidate_count']));ann_seconds=time.perf_counter()-ann_started;ann_examined=len(raw_hits)
   hit_ids=sorted({int(hit['research_text_id']) for hit in raw_hits});rows={}
   for start in range(0,len(hit_ids),800):
    chunk=hit_ids[start:start+800]
    if chunk:rows.update({int(row['id']):row for row in self.db.execute('select * from research_text where id in ('+','.join('?'*len(chunk))+')',chunk)})
   dense_ids=[]
   for hit in raw_hits:
    text_id=int(hit['research_text_id']);row=rows.get(text_id)
    if row is None:raise ResearchUnavailable('semantic occurrence is outside frozen research index')
    if text_id in dense_rank or row['locale']!='CHS' or row['evidence_family'] not in effective_families or (source_prefix is not None and not row['text_source_path'].startswith(source_prefix)):continue
    dense_ids.append(text_id);dense_rank[text_id]=len(dense_ids);semantic_score[text_id]=float(hit['semantic_score'])
    if len(dense_ids)>=int(config['domain_route']['ann_matching_family_cap']):break
   rerank_started=time.perf_counter();fused=sparse_preserving_rrf(sparse_pool,dense_ids,int(config['domain_route']['sparse_candidate_count']),int(config['domain_route']['ann_matching_family_cap']),int(config['domain_route']['rrf_constant']));rerank_seconds=time.perf_counter()-rerank_started
   for fused_item in fused:
    text_id=fused_item['research_text_id'];sparse_item=fused_item['sparse_item'];ranked.append({'research_text_id':text_id,'matched_lexical_anchors':sparse_item['matched_lexical_anchors'] if sparse_item else [],'sparse_rank':fused_item['sparse_rank'],'lexical_rank':fused_item['sparse_rank'],'semantic_rank':fused_item['semantic_rank'],'final_rank':fused_item['final_rank'],'rrf_score':fused_item['rrf_score'],'dense_rerank_score':None,'ranking_basis':'domain sparse top100 UNION matching-family ANN top1000/cap200; RRF(k=60)','retrieval_mode':'hybrid','discovery_only':True,'relation':None})
   route='domain_sparse100_ann1000_cap200_rrf60';preserved_count=len(sparse_pool)
  else:
   sparse_pool=sparse[:int(config['fallback_route']['sparse_candidate_count'])];embedding_started=time.perf_counter()
   if sparse_pool:
    vector=self.semantic.query_vector(query);embedding_seconds=time.perf_counter()-embedding_started;rerank_started=time.perf_counter();semantic_score=self.semantic.scores_from_vector(vector,[int(item['research_text_id']) for item in sparse_pool]);ordered=dense_rerank_preserving(sparse_pool,semantic_score,int(config['fallback_route']['sparse_candidate_count']));rerank_seconds=time.perf_counter()-rerank_started
   else:ordered=[];embedding_seconds=time.perf_counter()-embedding_started
   dense_rank={int(item['research_text_id']):item['final_rank'] for item in ordered if int(item['research_text_id']) in semantic_score}
   for reranked_item in ordered:
    sparse_item=reranked_item['sparse_item'];text_id=reranked_item['research_text_id'];ranked.append({'research_text_id':text_id,'matched_lexical_anchors':sparse_item['matched_lexical_anchors'],'sparse_rank':sparse_item['sparse_rank'],'lexical_rank':sparse_item['sparse_rank'],'semantic_rank':dense_rank.get(text_id),'final_rank':reranked_item['final_rank'],'rrf_score':None,'dense_rerank_score':reranked_item['dense_rerank_score'],'ranking_basis':'sparse top50 unchanged candidate set; R1 cosine rerank','retrieval_mode':'hybrid','discovery_only':True,'relation':None})
   route='no_domain_sparse50_dense_cosine_rerank';preserved_count=len(sparse_pool)
  ranked=self._rank_discovery_candidates(query,ranked,'hybrid',resolved_domain)
  page=[]
  for ranked_item in ranked[offset:offset+limit]:
   row=self.db.execute('select * from research_text where id=?',(ranked_item['research_text_id'],)).fetchone()
   if row is None:raise ResearchUnavailable('discovery occurrence is outside frozen research index')
   raw=self._recheck_text(row);ref=self._text_ref(row);container=self.db.execute('select * from research_container where id=?',(row['container_id'],)).fetchone();item={'evidence_ref':ref,'container_ref':self._container_ref(container),'family':row['evidence_family'],'contract':row['evidence_contract'],'exact_run':row['evidence_run'],'source':{'source_key':row['source_key'],'path':row['text_source_path'],'pointer':row['text_source_pointer']},'source_path':row['text_source_path'],'physical_pointer':row['text_source_pointer'],'locale':row['locale'],'owner_field':row['owner_field'],'snippet':raw[:180],'match_span':None,'provenance_ref':ref,'retrieval_mode':'hybrid','domain':resolved_domain,'matched_lexical_anchors':ranked_item['matched_lexical_anchors'],'literal_match_fields':[match.get('match_field') for match in ranked_item['matched_lexical_anchors']],'lexical_rank':ranked_item['lexical_rank'],'sparse_rank':ranked_item['sparse_rank'],'semantic_rank':ranked_item['semantic_rank'],'final_rank':ranked_item['final_rank'],'ranking_basis':ranked_item['ranking_basis'],'semantic_score':semantic_score.get(int(row['id'])),'rrf_score':ranked_item['rrf_score'],'dense_rerank_score':ranked_item['dense_rerank_score'],'discovery_only':True,'relation':None,'recommended_next_actions':['read_evidence','inspect_evidence','follow_evidence']}
   if 'relevance_hints' in ranked_item:item['relevance_hints']=ranked_item['relevance_hints']
   item=self._decorate_context(item,row,container)
   if len(json.dumps({'items':page+[item]},ensure_ascii=False,separators=(',',':')).encode())>SEARCH_DATA_BUDGET:break
   page.append(item)
  if ranked[offset:offset+limit] and not page:raise ResearchUnavailable('one discovery search item exceeds research wire budget')
  end=offset+len(page);more=end<len(ranked);next_cursor=self._cursor(scope,{'offset':end}) if more else None;coverage_families=effective_families if not empty_intersection else ['__empty_domain_intersection__'];searched,unsupported,unavailable,filtered,unknown=self._scope_coverage(coverage_families or None,locale);semantic_coverage=self.semantic.coverage();total_seconds=time.perf_counter()-started
  return {'items':page,'delivered_count':len(page),'next_cursor':next_cursor,'scope_complete':False,'candidate_pool_exhausted':not more,'candidate_retrieval_complete':False,'literal_phase_complete':True,'semantic_phase_complete':True,'partial':True,'domain':resolved_domain,'domain_intent_source':intent_source,'domain_filter':effective_families if resolved_domain else None,'recognized_anchors':extracted.get('recognized_anchors',[]),'unrecognized_spans':extracted.get('unrecognized_spans',''),'lexicon_coverage':extracted.get('lexicon_coverage',0.0),'lexical_candidate_count':len(sparse),'lexical_scope_count':scope_count,'anchor_document_frequency':document_frequency,'semantic_candidates_examined':ann_examined,'semantic_index_coverage':semantic_coverage,'semantic_indexed_scopes':semantic_coverage.get('semantic_indexed_scopes',{'locale':'CHS'}),'semantic_unsupported_scopes':['ANN candidate discovery is bounded','hybrid production window is bounded'],'semantic_unavailable_scopes':unavailable,'searched_scopes':searched,'unsupported_scopes':sorted(set(unsupported+['ANN candidate discovery is bounded','hybrid production window is bounded'])),'unavailable_scopes':unavailable,'locale_filtered_scopes':filtered,'locale_unknown_scopes':unknown,'candidate_monotonicity':{'sparse_preservation_limit':int(config['domain_route']['sparse_candidate_count'] if resolved_domain else config['fallback_route']['sparse_candidate_count']),'sparse_preserved_count':preserved_count,'all_preserved':all(int(item['research_text_id']) in {int(value['research_text_id']) for value in ranked} for item in sparse_pool)},'timing_seconds':{'sparse_generation':sparse_seconds,'query_embedding':embedding_seconds,'ann':ann_seconds,'dense_rerank_or_rrf':rerank_seconds,'total':total_seconds},'ranking':{'mode':'hybrid','route':route,'contract':config['contract'],'fusion':config['fusion_version'],'semantic_score_is_confidence':False,'semantic_score_is_relation_probability':False,'semantic_score_establishes_identity':False,'preserves_physical_occurrences':True,'exact_accepted_evidence_recheck':True}}
 def _legacy_semantic_search(self,query,locale,families,source_prefix,limit,cursor,mode):
  if self.semantic is None:raise ResearchUnavailable('semantic discovery overlay unavailable')
  limit=max(1,min(int(limit),100));families=sorted(set(families or ()));scope={'op':'search','mode':mode,'q':query,'locale':locale,'families':families,'source_prefix':source_prefix,'limit':limit};state=self._after(cursor,scope)
  if cursor is None:state={'phase':'literal','literal_cursor':None} if mode=='hybrid' else {'phase':'semantic','offset':0}
  if not isinstance(state,dict) or state.get('phase') not in ({'literal','semantic'} if mode=='hybrid' else {'semantic'}):raise ValueError('semantic cursor position mismatch')
  page=[];next_state=None;literal_complete=mode=='semantic' or state['phase']=='semantic';semantic_items=[];semantic_examined=0
  if state['phase']=='literal':
   if set(state)!= {'phase','literal_cursor'} or (state['literal_cursor'] is not None and not isinstance(state['literal_cursor'],str)):raise ValueError('hybrid literal cursor position mismatch')
   literal=self.search(self.corpus_ref,query,locale,families,source_prefix,limit,state['literal_cursor'],'literal');page.extend(literal['items']);literal_complete=literal['next_cursor'] is None
   if literal['next_cursor'] is not None:next_state={'phase':'literal','literal_cursor':literal['next_cursor']}
  if next_state is None and len(page)<limit and locale in (None,'CHS'):
   if state['phase']=='semantic':
    if set(state)!= {'phase','offset'} or not isinstance(state['offset'],int) or isinstance(state['offset'],bool) or state['offset']<0:raise ValueError('semantic cursor position mismatch')
    offset=state['offset']
   else:offset=0
   semantic_hits=self.semantic.candidates(query);semantic_examined=len(semantic_hits)
   for hit in semantic_hits:
    row=self.db.execute('select * from research_text where id=?',(hit['research_text_id'],)).fetchone()
    if row is None:raise ResearchUnavailable('semantic occurrence is outside frozen research index')
    if row['locale']!='CHS' or (families and row['evidence_family'] not in families) or (source_prefix is not None and not row['text_source_path'].startswith(source_prefix)):continue
    raw=self._recheck_text(row)
    if mode=='hybrid' and query in raw:continue
    ref=self._text_ref(row);container=self.db.execute('select * from research_container where id=?',(row['container_id'],)).fetchone();semantic_items.append(self._decorate_context({'evidence_ref':ref,'container_ref':self._container_ref(container),'family':row['evidence_family'],'contract':row['evidence_contract'],'exact_run':row['evidence_run'],'source':{'source_key':row['source_key'],'path':row['text_source_path'],'pointer':row['text_source_pointer']},'source_path':row['text_source_path'],'physical_pointer':row['text_source_pointer'],'locale':row['locale'],'owner_field':row['owner_field'],'snippet':raw[:180],'match_span':None,'provenance_ref':ref,'retrieval_mode':'semantic_candidate','semantic_score':hit['semantic_score'],'ranking_basis':'cosine candidate relevance; ranking signal only, not confidence, relation probability, identity, or factual evidence','discovery_only':True,'relation':None,'recommended_next_actions':['read_evidence','inspect_evidence','follow_evidence']},row,container))
   remaining=limit-len(page);consumed=0
   for item in semantic_items[offset:offset+remaining]:
    if len(json.dumps({'items':page+[item]},ensure_ascii=False,separators=(',',':')).encode())>SEARCH_DATA_BUDGET:break
    page.append(item);consumed+=1
   if semantic_items[offset:offset+remaining] and consumed==0:raise ResearchUnavailable('one semantic search item exceeds research wire budget')
   end=offset+consumed
   if end<len(semantic_items):next_state={'phase':'semantic','offset':end}
  elif next_state is None and state['phase']=='literal' and len(page)>=limit and locale in (None,'CHS'):
   # Literal pagination is complete, but semantic discovery begins on the next page.
   next_state={'phase':'semantic','offset':0}
  searched,unsupported,unavailable,filtered,unknown=self._scope_coverage(families or None,locale)
  semantic_unsupported=set(unsupported+['non-CHS locales','CHS texts shorter than 20 characters unless owner is name/title','families outside the declared 20-family body scope except name/title'])
  if locale not in (None,'CHS'):semantic_unsupported.add('semantic locale:'+str(locale))
  coverage=self.semantic.coverage()
  return {'items':page,'delivered_count':len(page),'next_cursor':self._cursor(scope,next_state) if next_state is not None and page else None,'scope_complete':False,'literal_scope_complete':literal_complete,'candidate_pool_exhausted':next_state is None,'candidate_retrieval_complete':False,'semantic_candidates_examined':semantic_examined,'semantic_index_coverage':coverage,'semantic_indexed_scopes':coverage.get('semantic_indexed_scopes',{'locale':'CHS'}),'semantic_unsupported_scopes':sorted(semantic_unsupported),'semantic_unavailable_scopes':unavailable,'searched_scopes':searched,'unsupported_scopes':sorted(semantic_unsupported),'unavailable_scopes':unavailable,'locale_filtered_scopes':filtered,'locale_unknown_scopes':unknown,'partial':True,'ranking':{'mode':mode,'literal_phase':'complete deterministic literal pagination before semantic candidates' if mode=='hybrid' else None,'semantic_candidate_window':coverage.get('query_candidate_k'),'semantic_score_is_confidence':False,'semantic_score_is_relation_probability':False,'semantic_score_establishes_identity':False,'preserves_physical_occurrences':True,'exact_accepted_evidence_recheck':True}}
 def _ordering_policy(self,container):
  direct=container['container_kind'] in ('readable_file','subtitle_file','talk_root')
  child_basis=container['ordering_basis'] in ('physical_node_ordinal','file_local_ordinal for readable segments')
  counts=self.db.execute('select count(*),coalesce(sum(ordinal is null),0) from research_text where container_id=?',(container['id'],)).fetchone()
  physical=(direct or child_basis) and (counts[0]==0 or counts[1]==0)
  if physical:
   default={'readable_file':'physical line ordinal','subtitle_file':'physical cue ordinal','talk_root':'physical node ordinal'}.get(container['container_kind'],'physical child ordinal')
   return {'mode':'physical_ordinal','basis':container['ordering_basis'] or default,'context_supported':True}
  return {'mode':'deterministic_evidence','basis':'deterministic evidence order','context_supported':False}
 def _ordered_rows(self,container,position,limit,direction='after'):
  policy=self._ordering_policy(container);cid=container['id'];cmp='>' if direction=='after' else '<';descending=direction=='before'
  if policy['mode']=='physical_ordinal':
   terms=("(ordinal is null)","coalesce(ordinal,0)","coalesce(child_id,'')","id");expr=','.join(terms);order=', '.join(x+(' desc' if descending else ' asc') for x in terms);args=[cid]
   if position:
    if set(position)!= {'null_ordinal','ordinal','child_id','id'}:raise ValueError('cursor position mismatch')
    where=f'container_id=? and ({expr}) {cmp} (?,?,?,?)';args.extend((int(position['null_ordinal']),int(position['ordinal']),str(position['child_id']),int(position['id'])))
   else:where='container_id=?'
   rows=self.db.execute(f'select * from research_text where {where} order by {order} limit ?',(*args,limit)).fetchall()
  else:
   terms=("text_source_path","text_source_pointer","coalesce(child_id,'')","id");expr=','.join(terms);order=', '.join(x+(' desc' if descending else ' asc') for x in terms);args=[cid]
   if position:
    if set(position)!= {'source_path','pointer','child_id','id'}:raise ValueError('cursor position mismatch')
    where=f'container_id=? and ({expr}) {cmp} (?,?,?,?)';args.extend((str(position['source_path']),str(position['pointer']),str(position['child_id']),int(position['id'])))
   else:where='container_id=?'
   rows=self.db.execute(f'select * from research_text where {where} order by {order} limit ?',(*args,limit)).fetchall()
  return rows,policy
 def _order_position(self,row,mode):
  if mode=='physical_ordinal':return {'null_ordinal':int(row['ordinal'] is None),'ordinal':int(row['ordinal'] or 0),'child_id':str(row['child_id'] or ''),'id':int(row['id'])}
  return {'source_path':str(row['text_source_path']),'pointer':str(row['text_source_pointer']),'child_id':str(row['child_id'] or ''),'id':int(row['id'])}
 def _decorate_read(self,item,row,container):
  result=self._decorate_context(item,row,container)
  from .dialogue import DialogueContext
  dialogue=DialogueContext(self)
  if dialogue.supports(container):result['dialogue_source']=dialogue.card(container,row['locale'] if row is not None and row['locale'] in ('CHS','EN','JP') else 'CHS')
  return result
 def read(self,corpus_ref,evidence_ref,before=0,after=0,limit=50,cursor=None,view='physical',locale='CHS'):
  if view=='dialogue':
   if before or after:raise ValueError('dialogue view uses source connections, not before/after windows')
   from .dialogue import DialogueContext
   return DialogueContext(self).read(corpus_ref,evidence_ref,limit,cursor,locale)
  if view!='physical':raise ValueError('read view must be physical or dialogue')
  self._require(corpus_ref);ref=decode_ref(evidence_ref);limit=max(1,min(int(limit),100));before=max(0,min(int(before),50));after=max(0,min(int(after),50))
  if ref['kind']=='text':
   row=self._text_for(ref);container=self.db.execute('select * from research_container where id=?',(row['container_id'],)).fetchone();policy=self._ordering_policy(container);position=self._order_position(row,policy['mode']);tagged=[]
   if policy['context_supported']:
    prior,_=self._ordered_rows(container,position,before,'before');following,_=self._ordered_rows(container,position,after,'after');tagged=[('before',x) for x in reversed(prior)]+[('anchor',row)]+[('after',x) for x in following]
   else:tagged=[('anchor',row)]
   context=[]
   for side,x in tagged:self._recheck_text(x);context.append({'evidence_ref':self._text_ref(x),'ordinal':x['ordinal'],'locale':x['locale'],'raw_text':x['raw_text'],'pointer':x['text_source_pointer'],'context_position':side})
   truncated=False
   while context and len(json.dumps({'raw_text':row['raw_text'],'context':context},ensure_ascii=False,separators=(',',':')).encode())>220000:context.pop();truncated=True
   return self._decorate_read({'evidence_ref':evidence_ref,'container_ref':self._container_ref(container),'raw_text':row['raw_text'],'provenance':self._text_provenance(row),'context':context,'context_scope':'same physical Talk container' if container['container_kind']=='talk_root' else 'same physical container','physical_container':container['occurrence_id'],'ordering_basis':policy['basis'],'before_delivered':sum(x['context_position']=='before' for x in context),'after_delivered':sum(x['context_position']=='after' for x in context),'context_supported':policy['context_supported'],'context_truncated':truncated,'recommended_next_actions':['inspect_evidence','follow_evidence']},row,container)
  container=self._container_for(ref)
  if isinstance(container,dict) and container.get('adapter_runtime'):
   if cursor is not None:raise ValueError('cursor scope mismatch')
   return self._decorate_context({'evidence_ref':evidence_ref,'metadata':self._container_payload(container),'context':[],'delivered_count':0,'next_cursor':None,'scope_complete':True,'context_scope':'single physical evidence container','physical_container':container['occurrence_id'],'ordering_basis':'no declared child text projection','context_supported':False,'partial':False,'recommended_next_actions':['inspect_evidence','follow_evidence']},None,container)
  policy=self._ordering_policy(container);scope={'op':'read-container','container_ref':evidence_ref,'ordering_mode':policy['mode'],'limit':limit};position=self._after(cursor,scope);position=None if position==0 else position;rows,policy=self._ordered_rows(container,position,limit+1);items=[]
  for x in rows[:limit]:
   self._recheck_text(x);item={'evidence_ref':self._text_ref(x),'ordinal':x['ordinal'],'locale':x['locale'],'owner_field':x['owner_field'],'raw_text':x['raw_text'],'pointer':x['text_source_pointer']}
   if len(json.dumps({'context':items+[item]},ensure_ascii=False,separators=(',',':')).encode())>220000:break
   items.append(item)
  more=len(rows)>len(items);last_row=items and rows[len(items)-1];position=self._order_position(last_row,policy['mode']) if last_row else None
  return self._decorate_read({'evidence_ref':evidence_ref,'metadata':self._container_payload(container),'context':items,'delivered_count':len(items),'next_cursor':self._cursor(scope,position) if more and position else None,'scope_complete':not more,'context_scope':'same physical Talk container' if container['container_kind']=='talk_root' else 'same physical container','physical_container':container['occurrence_id'],'ordering_basis':policy['basis'],'context_supported':policy['context_supported'],'partial':False,'recommended_next_actions':['inspect_evidence','follow_evidence']},None,container)
 def _container_payload(self,r):return {'family':r['evidence_family'],'contract':r['evidence_contract'],'parser':r['evidence_parser'],'run':r['evidence_run'],'occurrence_id':r['occurrence_id'],'container_kind':r['container_kind'],'source_path':r['source_path'],'source_pointer':r['source_pointer']}
 def _text_provenance(self,r):return {'kind':'text','snapshot':self.manifest['snapshot'],'source_file':r['text_source_path'],'source_file_sha256':r['text_source_file_sha256'],'source_git_blob':r['text_source_git_blob'],'source_pointer':r['text_source_pointer'],'physical_pair_ordinal':r['physical_pair_ordinal'],'family':r['evidence_family'],'contract':r['evidence_contract'],'parser':r['evidence_parser'],'run':r['evidence_run'],'occurrence':r['occurrence_id'],'child':r['child_id']}
 def inspect(self,corpus_ref,evidence_ref):
  self._require(corpus_ref);ref=decode_ref(evidence_ref)
  if ref['kind']=='text':
   row=self._text_for(ref);container=self.db.execute('select * from research_container where id=?',(row['container_id'],)).fetchone();return self._decorate_context({'evidence_ref':evidence_ref,'evidence_identity':self._container_payload(container),'container_provenance':{'source_file':container['source_path'],'source_file_sha256':container['source_file_sha256'],'source_git_blob':container['source_git_blob'],'source_pointer':container['source_pointer']},'text_provenance':self._text_provenance(row),'terminal_unresolved':self.manifest.get('terminal_unresolved'),'recommended_next_actions':['read_evidence','follow_evidence']},row,container)
  container=self._container_for(ref)
  if isinstance(container,dict) and container.get('adapter_runtime'):
   return self._decorate_context({'evidence_ref':evidence_ref,'evidence_identity':self._container_payload(container),'container_provenance':{'source_file':container['source_path'],'source_file_sha256':container['source_file_sha256'],'source_git_blob':container['source_git_blob'],'source_pointer':container['source_pointer']},'text_provenance':None,'relation_resolution':{'status_summary':[],'count':0,'adapted_relation_kinds':[],'unsupported_scope':False,'action':'follow_evidence'},'terminal_unresolved':self.manifest.get('terminal_unresolved'),'recommended_next_actions':['read_evidence']},None,container)
  summary=[dict(x) for x in self.db.execute('select status,count(*) count,sum(candidate_count) candidates from research_relation where container_id=? group by status order by status',(container['id'],))];dynamic=relations_for(self,container)
  for status in sorted({x['status'] for x in dynamic}):summary.append({'status':status,'count':sum(x['status']==status for x in dynamic),'candidates':sum(len(x['candidates']) for x in dynamic if x['status']==status),'projection':'runtime_exact_navigation_adapter'})
  unsupported=any(x['source_key']==container['source_key'] and x['projection_kind'] in ('relation','candidate') and x['status']=='unsupported' for x in self.coverage);return self._decorate_context({'evidence_ref':evidence_ref,'evidence_identity':self._container_payload(container),'container_provenance':{'source_file':container['source_path'],'source_file_sha256':container['source_file_sha256'],'source_git_blob':container['source_git_blob'],'source_pointer':container['source_pointer']},'text_provenance':None,'relation_resolution':{'status_summary':summary,'count':sum(x['count'] for x in summary),'adapted_relation_kinds':sorted({x['relation_kind'] for x in dynamic}),'unsupported_scope':unsupported,'action':'follow_evidence'},'terminal_unresolved':self.manifest.get('terminal_unresolved'),'recommended_next_actions':['read_evidence','follow_evidence']},None,container)
 def _recheck_relation(self,r):
  self._recheck_container(self.db.execute('select * from research_container where id=?',(r['container_id'],)).fetchone());src=self._source(r['source_key']);got=src.execute(f'select * from "{r["source_table"]}" where "{r["pk_column"]}"=?',(r['pk_value'],)).fetchone()
  if got is None:raise ResearchUnavailable('relation observation disappeared')
  cols=set(got.keys());kind=got['relation_kind'] if 'relation_kind' in cols else got['kind'] if 'kind' in cols else r['relation_kind'];ptr=got['source_pointer'] if 'source_pointer' in cols else got['origin_pointer'] if 'origin_pointer' in cols else got['pointer'] if 'pointer' in cols else r['source_pointer']
  if str(kind)!=r['relation_kind'] or str(ptr)!=r['source_pointer'] or str(got['status'])!=r['status'] or int(got['candidate_count'])!=r['candidate_count']:raise ResearchUnavailable('relation projection drift')
 def _target_ref(self,c):
  if not isinstance(c,dict):c=dict(c)
  row=None
  if c['target_run'] and c['target_occurrence_id']:
   row=[]
   # Most relations use the routed family name.  Some historical Character
   # relations store the row-level evidence family in target_contract and the
   # routed alias in target_family; try both explicit namespaces before the
   # unique run/occurrence fallback.
   if c['target_family'] and c['target_contract']:
    row=self.db.execute('select * from research_container where evidence_family=? and evidence_contract=? and evidence_run=? and occurrence_id=? order by id',(c['target_family'],c['target_contract'],c['target_run'],c['target_occurrence_id'])).fetchall()
   # Some sealed Character relations use target_family as the routed alias and
   # target_contract as the row-level evidence family.  This is the sole
   # historical alias adapter; it still binds exact run and occurrence.
   if not row and c['target_contract']:
    if c.get('_row_family_alias') and c['target_family']=='characters':row=self.db.execute('select * from research_container where evidence_family=? and evidence_run=? and occurrence_id=? order by id',(c['target_contract'],c['target_run'],c['target_occurrence_id'])).fetchall()
   if not row and c['target_family'] and not c['target_contract']:
    row=self.db.execute('select * from research_container where evidence_family=? and evidence_run=? and occurrence_id=? order by id',(c['target_family'],c['target_run'],c['target_occurrence_id'])).fetchall()
   if c['target_parser']:row=[x for x in row if x['evidence_parser']==c['target_parser']]
   if not row:
    adapted=self._adapter_target_ref(c)
    if adapted:return adapted
  elif c['target_family'] and c['target_source_path']:
   row=self.db.execute('select * from research_container where evidence_family=? and source_path=? order by id',(c['target_family'],c['target_source_path'])).fetchall()
  if row and len(row)==1:return self._container_ref(row[0])
  return None
 def _recheck_candidate(self,relation,c):
  src=self._source(relation['source_key']);got=src.execute(f'select * from "{c["source_table"]}" where "{c["pk_column"]}"=?',(c['pk_value'],)).fetchone()
  if got is None:raise ResearchUnavailable('relation candidate disappeared')
  cols=set(got.keys())
  aliases={'target_family':('target_family',),'target_contract':('target_contract',),'target_parser':('target_parser','target_parser_build_id'),'target_run':('target_run','target_run_id'),'target_occurrence_id':('target_occurrence_id','target_row_id'),'target_source_path':('target_source_path',),'target_pointer':('target_source_pointer','target_pointer')}
  for projected,names in aliases.items():
   for name in names:
    if name in cols and got[name] is not None and str(got[name])!=str(c[projected]):raise ResearchUnavailable('relation candidate target drift: '+projected)
  if 'npc_id' in cols and str(got['npc_id'])!=str(c['raw_target']):raise ResearchUnavailable('relation candidate raw target drift')
 def follow(self,corpus_ref,evidence_ref,relation_kind=None,limit=50,cursor=None,resolve_targets=True):
  if relation_kind=='dialogue_predecessor':
   from .dialogue import DialogueContext
   result=DialogueContext(self).predecessors(corpus_ref,evidence_ref,limit,cursor)
   if not resolve_targets:
    for item in result['items']:
     for candidate in item['candidates']:candidate['target_evidence_ref']=None
   return result
  self._require(corpus_ref);ref=decode_ref(evidence_ref);container=self._container_for(ref) if ref['kind']=='container' else self.db.execute('select c.* from research_text t join research_container c on c.id=t.container_id where t.id=?',(self._text_for(ref)['id'],)).fetchone();limit=max(1,min(int(limit),100));scope={'op':'follow','container':container['id'],'kind':relation_kind,'limit':limit};state=self._after(cursor,scope)
  if isinstance(container,dict) and container.get('adapter_runtime'):return {'items':[],'delivered_count':0,'next_cursor':None,'scope_complete':True,'partial':False,'unsupported_relation_scope':False,'recommended_next_actions':['read_evidence','inspect_evidence']}
  dynamic=relations_for(self,container,relation_kind)
  # New cursors name their phase.  Integer and old relation/candidate cursors
  # remain valid for compatibility with already-issued opaque v2 cursors.
  if cursor is None and dynamic:state={'phase':'dynamic','relation_index':0,'candidate_offset':0}
  if isinstance(state,dict) and state.get('phase')=='dynamic':
   ri=int(state.get('relation_index',0));offset=int(state.get('candidate_offset',0))
   if ri<0 or ri>=len(dynamic) or offset<0:raise ValueError('cursor relation scope mismatch')
   relation=dynamic[ri];page=[]
   for candidate in relation['candidates'][offset:offset+limit]:
    item=dict(candidate)
    if not resolve_targets:item['target_evidence_ref']=None
    if len(json.dumps({'candidates':page+[item]},ensure_ascii=False,separators=(',',':')).encode())>220000:break
    page.append(item)
   consumed=len(page);more_candidates=offset+consumed<len(relation['candidates'])
   item={k:relation[k] for k in ('relation_kind','direction','status','search_complete','candidate_namespace','proof_source_pointer','raw_locator')};item.update({'source_evidence_ref':self._container_ref(container),'candidates':page,'candidate_page_complete':not more_candidates,'projection':'runtime_exact_navigation_adapter'})
   next_cursor=None
   if more_candidates:
    if not consumed:raise ResearchUnavailable('one relation candidate exceeds research wire budget')
    next_cursor=self._cursor(scope,{'phase':'dynamic','relation_index':ri,'candidate_offset':offset+consumed})
   elif ri+1<len(dynamic):next_cursor=self._cursor(scope,{'phase':'dynamic','relation_index':ri+1,'candidate_offset':0})
   else:
    q='select 1 from research_relation where container_id=?';qa=[container['id']]
    if relation_kind:q+=' and relation_kind=?';qa.append(relation_kind)
    if self.db.execute(q+' limit 1',qa).fetchone():next_cursor=self._cursor(scope,{'phase':'stored','after':0})
   unsupported=any(x['source_key']==container['source_key'] and x['projection_kind'] in ('relation','candidate') and x['status']=='unsupported' for x in self.coverage)
   return {'items':[item],'delivered_count':1,'next_cursor':next_cursor,'scope_complete':next_cursor is None and not unsupported,'partial':unsupported,'unsupported_relation_scope':unsupported,'recommended_next_actions':['read_evidence','inspect_evidence']}
  if isinstance(state,dict) and state.get('phase')=='stored':state=int(state.get('after',0))
  candidate_state=state if isinstance(state,dict) else None;after=0 if candidate_state else state;args=[container['id'],after];where='container_id=? and id>?'
  if relation_kind:where+=' and relation_kind=?';args.append(relation_kind)
  if candidate_state:
   rels=self.db.execute('select * from research_relation where id=? and container_id=?',(candidate_state.get('relation'),container['id'])).fetchall()
   if not rels:raise ValueError('cursor relation scope mismatch')
  else:rels=self.db.execute(f'select * from research_relation where {where} order by id limit ?',(*args,limit+1)).fetchall()
  items=[];next_cursor=None
  for r in rels[:limit]:
   self._recheck_relation(r);candidates=[]
   ca=candidate_state.get('candidate_after',0) if candidate_state else 0;crows=self.db.execute('select * from research_candidate where relation_id=? and id>? order by id limit ?',(r['id'],ca,limit+1)).fetchall()
   for c in crows[:limit]:
    self._recheck_candidate(r,c);target=self._target_ref(c) if resolve_targets else None;item={'target_evidence_ref':target,'target_family':c['target_family'],'target_contract':c['target_contract'],'target_parser':c['target_parser'],'target_run':c['target_run'],'target_occurrence_id':c['target_occurrence_id'],'target_source_path':c['target_source_path'],'target_pointer':c['target_pointer'],'raw_target':c['raw_target']}
    if len(json.dumps({'items':items,'candidates':candidates+[item]},ensure_ascii=False,separators=(',',':')).encode())>220000:break
    candidates.append(item)
   candidate_more=len(crows)>len(candidates)
   items.append({'source_evidence_ref':self._container_ref(container),'relation_kind':r['relation_kind'],'direction':'outbound','status':r['status'],'search_complete':bool(r['search_complete']),'candidate_namespace':r['candidate_namespace'],'proof_source_pointer':r['source_pointer'],'raw_locator':r['raw_locator'],'candidates':candidates,'candidate_page_complete':not candidate_more})
   if candidate_more:
    last=candidates and crows[len(candidates)-1]['id']
    if not last:raise ResearchUnavailable('one relation candidate exceeds research wire budget')
    next_cursor=self._cursor(scope,{'relation':r['id'],'candidate_after':last});break
   if candidate_state:
    q='select 1 from research_relation where container_id=? and id>?';qa=[container['id'],r['id']]
    if relation_kind:q+=' and relation_kind=?';qa.append(relation_kind)
    if self.db.execute(q+' limit 1',qa).fetchone():next_cursor=self._cursor(scope,{'phase':'stored','after':r['id']})
   candidate_state=None
  unsupported=any(x['source_key']==container['source_key'] and x['projection_kind'] in ('relation','candidate') and x['status']=='unsupported' for x in self.coverage)
  if next_cursor is None and not candidate_state and len(rels)>limit:next_cursor=self._cursor(scope,{'phase':'stored','after':rels[limit-1]['id']})
  return {'items':items,'delivered_count':len(items),'next_cursor':next_cursor,'scope_complete':next_cursor is None and not unsupported,'partial':unsupported,'unsupported_relation_scope':unsupported,'recommended_next_actions':['read_evidence','inspect_evidence']}
