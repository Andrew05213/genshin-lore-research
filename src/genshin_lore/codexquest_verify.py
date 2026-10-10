"""Independent pinned-source verifier for the CodexQuest archive."""
from __future__ import annotations
import hashlib,json,sqlite3,subprocess,time
from collections import Counter,defaultdict
from itertools import zip_longest
from pathlib import Path
from .codexquest_source import COMMIT,TREE,SNAPSHOT,FAMILY,CONTRACT,PREFIX,MAIN,QUEST_CODEX,LANGS,TEXTMAPS,canonical,sha,ident,load
from .loading_tips_source import pairs
from .source_integrity import pinned_file_seal
ROOT=Path(__file__).resolve().parents[2]
IMPLEMENTATION=('src/genshin_lore/source_integrity.py','src/genshin_lore/codexquest_source.py','src/genshin_lore/codexquest_build.py','schemas/codexquest-archive-v1.sql')
TABLES=('codexquest_container','codexquest_body_chunk','codexquest_child','codexquest_relation','codexquest_candidate','codexquest_text_slot','codexquest_text_occurrence','codexquest_text_candidate')
SHAPES={
 'rc_493':{'root_keys':('ALOHJMPDFKI','CBLIMBBBKNK','DCNPPIOLEOK','EBNBLBEIFFJ','HEDPNHPBMJH','IMJHJGBNMMD','NNPJABOAJPL'),'id':'IMJHJGBNMMD','type':'DCNPPIOLEOK','sections':'EBNBLBEIFFJ','section_keys':('OGEGCCLHIHP','PEAKPGNONFA'),'nodes':'PEAKPGNONFA','locator_label':'JOBGILDNLEL','locator_hash':'BNJEGIAOKGM'},
 'legacy_7':{'root_keys':('EIGDJINAAME','HAAMAIAEBPH','HCOCNINCBEI','HLCINEMBGEF','IPENIGDFFAD','JNMOLCMMGHN','LDOJBIHMDAB'),'id':'HCOCNINCBEI','type':'EIGDJINAAME','sections':'HLCINEMBGEF','section_keys':('IIJIPINAOOF','KHNBHADECGO'),'nodes':'IIJIPINAOOF','locator_label':'LKNCCJIKPNP','locator_hash':'LOFLLCHCMCG'},
 'alternate_3':{'root_keys':('APEPAFOBDGB','EIFDMLOMFHF','HOBBPLPNMDP','MEGJPCLADOG','MHGNMKHOKHI','NCNGHCODLAK','PIAAIKBBCGB'),'id':'MHGNMKHOKHI','type':'NCNGHCODLAK','sections':'MEGJPCLADOG','section_keys':('FCEMMMMDKLP','IIIBPOOMIOE'),'nodes':'FCEMMMMDKLP','locator_label':'OKDLDDEBPOF','locator_hash':'BKGOAJFIHCO'}}
def shape_of(value):
 keys=tuple(sorted(value)) if isinstance(value,dict) else ();hits=[name for name,spec in SHAPES.items() if keys==spec['root_keys']]
 if len(hits)!=1:raise ValueError('independent unknown root shape '+repr(keys))
 return hits[0]
def independent_inventory(root):
 lines=subprocess.check_output(['git','-C',str(root),'ls-tree','-rl',COMMIT,'--',PREFIX],text=True).splitlines();result=[]
 for line in lines:
  left,path=line.split('\t',1);mode,kind,blob,size=left.split()
  check(kind=='blob' and path.endswith('.json'),'independent unsupported inventory object')
  result.append({'path':path,'blob':blob,'size_bytes':int(size)})
 return sorted(result,key=lambda item:item['path'])
def narrative_occurrence(*parts):return 'quest-narrative:'+hashlib.sha256(canonical(parts).encode()).hexdigest()[:40]
def check(value,message):
 if not value:raise ValueError(message)
def walk(value,spec,pointer=''):
 if isinstance(value,dict):
  if set(value)=={spec['locator_label'],spec['locator_hash']} and str(value[spec['locator_label']]):yield pointer or '/',str(value[spec['locator_label']]),str(value[spec['locator_hash']])
  for k,v in value.items():yield from walk(v,spec,pointer+'/'+k)
 elif isinstance(value,list):
  for i,v in enumerate(value):yield from walk(v,spec,pointer+'/'+str(i))
def chunks(raw,size=49152):
 start=0
 while start<len(raw):
  end=min(start+size,len(raw))
  while end>start:
   try:text=raw[start:end].decode('utf-8');break
   except UnicodeDecodeError:end-=1
  check(end>start,'chunk boundary');yield start,end,text;start=end
def sha_bytes(raw):return hashlib.sha256(raw).hexdigest()
def compare_complete_rows(actual,expected,table):
 mismatch=None
 for a,b in zip_longest(actual,expected):
  at=None if a is None else tuple(a)
  if at!=b and mismatch is None:mismatch=(at,b)
 if mismatch is not None:raise ValueError(table+' full business row mismatch '+repr(mismatch)[:1000])

def expected(source,run,core_artifact,narrative_artifact,narrative_manifest):
 source=Path(source);nm=load(narrative_manifest);nrun=nm['release']['run_id'];check(Path(narrative_artifact).stat().st_size==nm['artifact']['size_bytes'] and sha(narrative_artifact)==nm['artifact']['sha256'],'narrative seal')
 ndb=sqlite3.connect(Path(narrative_artifact).absolute().as_uri()+'?mode=ro&immutable=1',uri=True);ndb.row_factory=sqlite3.Row;main_targets=defaultdict(list)
 for i,x in enumerate(load(source/MAIN)):
  oid=narrative_occurrence('container',MAIN,f'/{i}');row=ndb.execute('select source_path,source_pointer,native_id from quest_narrative_container where id=? and run_id=?',(oid,nrun)).fetchone();check(row and tuple(row)==(MAIN,f'/{i}',str(x['id'])),'narrative locator');main_targets[int(x['id'])].append((MAIN,f'/{i}',str(x['id']),'quest_narrative_archive','quest-narrative-evidence-v1',nrun,oid,'exact_mainquest_navigation'))
 ndb.close();cdb=sqlite3.connect(Path(core_artifact).absolute().as_uri()+'?mode=ro&immutable=1',uri=True);cdb.row_factory=sqlite3.Row;codex_targets=defaultdict(list)
 q="""select r.id,r.source_pointer,r.native_locator_value,rel.raw_value_json,r.ingest_run_id from quest_codex_row_occurrence r join quest_codex_relationship_occurrence rel on rel.ingest_run_id=r.ingest_run_id and rel.source_row_occurrence_id=r.id where r.evidence_kind='quest_codex_entry' and rel.field_name='parentQuestId' order by r.source_pointer,r.id"""
 for x in cdb.execute(q):codex_targets[int(json.loads(x['raw_value_json']))].append((QUEST_CODEX,x['source_pointer'],str(x['native_locator_value']),'quest_codex_archive','quest-codex-archive-evidence-v1',x['ingest_run_id'],x['id'],'exact_reverse_questcodex_navigation'))
 cdb.close();containers=[];body=[];children=[];relations=[];candidates=[];slot_specs=[];wanted=set()
 for item in independent_inventory(source):
  path=item['path'];seal=pinned_file_seal(source,COMMIT,path);raw=(source/path).read_bytes();value=json.loads(raw.decode('utf-8-sig'));shape=shape_of(value);spec=SHAPES[shape];native=value[spec['id']];cid=ident('container',run,path,'/')
  containers.append((cid,run,shape,path,'/',seal['blob'],len(raw),seal['sha256'],str(native),str(value[spec['type']]),'physical_codexquest_archive_processed'))
  for o,(start,end,text) in enumerate(chunks(raw)):body.append((cid+f':body:{o:06d}',cid,o,start,end,text,sha_bytes(raw[start:end])))
  for so,section in enumerate(value[spec['sections']]):
   pointer=f"/{spec['sections']}/{so}";children.append((ident('child',run,path,pointer),cid,'section',pointer,so,sha_bytes(canonical(section).encode()),canonical(section)))
   for no,node in enumerate(section[spec['nodes']]):
    np=pointer+f"/{spec['nodes']}/{no}";children.append((ident('child',run,path,np),cid,'node',np,no,sha_bytes(canonical(node).encode()),canonical(node)))
  for pointer,label,locator in walk(value,spec):wanted.add(locator);slot_specs.append((cid,pointer,label,locator))
  for kind,namespace,semantics,hits in (('mainquest_equality_navigation','observed equal raw values in complete pinned MainQuestExcel id namespace; source does not independently prove semantic identity','cross-source equality navigation aid; not asserted as a source-authored CodexQuest relation',main_targets[int(native)]),('questcodex_reverse_navigation','all accepted QuestCodex primary rows by explicit parentQuestId','reverse navigation from source-authored QuestCodex parentQuestId; not a CodexQuest-authored forward relation',codex_targets[int(native)])):
   pointer='/'+spec['id'];rid=ident('relation',run,path,pointer,kind);status='mapping_unresolved' if kind=='mainquest_equality_navigation' else 'missing' if not hits else 'resolved_unique' if len(hits)==1 else 'ambiguous';complete=0 if kind=='mainquest_equality_navigation' else 1;relations.append((rid,cid,kind,pointer,canonical(native),namespace,status,len(hits),complete,semantics))
   for ordinal,h in enumerate(hits):candidates.append((ident('candidate',rid,*h[:7]),rid,ordinal,*h[:7],('explicit_unresolved_equality_navigation_candidate' if kind=='mainquest_equality_navigation' else h[7])))
 maps={};seals={p:pinned_file_seal(source,COMMIT,p) for p in TEXTMAPS}
 for lang in LANGS:
  for tier in ('','_Medium'):
   path=f'TextMap/TextMap{tier}{lang}.json';d=defaultdict(list)
   for ordinal,(key,text) in enumerate(pairs(source/path)):
    if key in wanted:d[key].append((path,seals[path]['blob'],ordinal,'/'+key,text))
   maps[lang,tier]=d
 slots=[];occurrences={};links=[]
 for cid,pointer,label,locator in slot_specs:
  for lang in LANGS:
   main_hits=maps[lang,''].get(locator,[]);hits=main_hits or maps[lang,'_Medium'].get(locator,[]);tier='main' if main_hits else 'medium';status='missing' if not hits else 'resolved_'+tier if len(hits)==1 else 'ambiguous';sid=ident('slot',cid,pointer,lang);slots.append((sid,cid,pointer,label,lang,locator,status,len(hits)))
   for path,blob,ordinal,tp,text in hits:
    oid=ident('text-occurrence',path,ordinal,tp);occurrences[oid]=(oid,path,blob,ordinal,tp,lang,tier,text,sha_bytes(text.encode()));links.append((ident('text-candidate',sid,oid),sid,oid))
 return (containers,body,children,relations,candidates,slots,list(occurrences.values()),links)

def verify(source,scope_path,artifact,manifest_path,core_artifact,narrative_artifact,narrative_manifest):
 started=time.perf_counter();source=Path(source);scope=load(scope_path);manifest=load(manifest_path)
 check(Path(narrative_manifest).resolve()==ROOT/scope['quest_narrative_manifest'] and sha(narrative_manifest)==scope['quest_narrative_manifest_sha256'],'Quest Narrative manifest scope binding')
 check([pinned_file_seal(source,COMMIT,x['path']) for x in scope['files']]==scope['files'],'actual bytes Git blob/source seals')
 check(Path(core_artifact).stat().st_size==scope['quest_codex_dependency']['artifact_size_bytes'] and sha(core_artifact)==scope['quest_codex_dependency']['sha256'],'core seal')
 impl={p:sha(ROOT/p) for p in IMPLEMENTATION};parser=ident('parser',impl);run=ident('run',SNAPSHOT,parser,sha(scope_path),CONTRACT,sha(narrative_manifest),scope['quest_codex_dependency']['sha256']);check(manifest['release']['implementation']==impl and manifest['release']['parser_build_id']==parser and manifest['release']['run_id']==run,'identity');check(Path(artifact).stat().st_size==manifest['artifact']['size_bytes'] and sha(artifact)==manifest['artifact']['sha256'],'artifact seal')
 rows=expected(source,run,core_artifact,narrative_artifact,narrative_manifest)
 containers,_,children,_,_,slots,_,_=rows;section_count=sum(x[2]=='section' for x in children);node_count=sum(x[2]=='node' for x in children);largest=max((x[6],x[3]) for x in containers);den={'physical_files':len(containers),'total_source_bytes':sum(x[6] for x in containers),'shape_counts':dict(sorted(Counter(x[2] for x in containers).items())),'sections':section_count,'nodes':node_count,'text_owners':len(slots)//len(LANGS),'expected_locale_slots':len(slots),'largest_container':{'size_bytes':largest[0],'path':largest[1]}};check(den==scope['denominators'],'independent denominator')
 db=sqlite3.connect(Path(artifact).absolute().as_uri()+'?mode=ro&immutable=1',uri=True)
 logical={}
 for table,values in zip(TABLES,rows):
  values=sorted(values,key=lambda x:x[0]);actual=[tuple(x) for x in db.execute('select * from '+table+' order by 1')];compare_complete_rows(actual,values,table);digest=hashlib.sha256()
  for item in actual:digest.update((canonical(list(item))+'\n').encode())
  logical[table]={'rows':len(values),'sha256':digest.hexdigest()}
 check(not db.execute('pragma foreign_key_check').fetchall() and db.execute('pragma integrity_check').fetchone()[0]=='ok','database integrity');check(json.loads(db.execute('select manifest_json from codexquest_release').fetchone()[0])==manifest['release'],'release');db.close()
 return {'status':'pass','source_byte_git_blob_binding':True,'physical_files':len(rows[0]),'children':len(rows[2]),'relations':len(rows[3]),'candidates':len(rows[4]),'text_slots':len(rows[5]),'physical_text_occurrences':len(rows[6]),'text_candidate_links':len(rows[7]),'shape_counts':dict(Counter(x[2] for x in rows[0])),'relation_states':dict(Counter(x[6] for x in rows[3])),'text_states':dict(Counter(x[6] for x in rows[5])),'independent_candidate_enumeration':True,'complete_business_column_equality':True,'no_extra_rows':True,'logical_seals':logical,'artifact_sha256':manifest['artifact']['sha256'],'wall_seconds':time.perf_counter()-started}

__all__=['verify','expected','compare_complete_rows']
