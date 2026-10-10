"""Independent source verifier for the complete Talk physical tree."""
from __future__ import annotations
import hashlib,json,sqlite3,subprocess,time
from collections import Counter,defaultdict
from pathlib import Path
from .talk_physical_closure_source import COMMIT,TREE,SNAPSHOT,FAMILY,CONTRACT,canonical,ident,sha,load
from .talk_physical_closure_build import IMPLEMENTATION
ROOT=Path(__file__).resolve().parents[2]
REFS={'rc':('OIFGMOHKPOI','MCIAGDCECPL'),'alt':('NFIEHACCECI','BKHBKMLIPLN'),'normalized':('id','initDialog'),'older_a':('CCFPGAKINNB',None),'older_b':('JOLEJEFDNJJ','FBALOFKGJKN'),'blossom':('ANKFNLMKOII','GIIPBNJFFAK')}
IDS={'rc':'IOKNFDJFGDH','alt':'AADKDKPMGNO','third':'LDLMECNIJFC','normalized':'talkId','older_a':'FEOACBMDCKJ','older_b':'PBAEPDPNKEJ','older_c':'ADHLLDAPKCM','older_d':'LBPGKDMGFBN'}
def check(v,msg):
 if not v:raise ValueError(msg)
def expected_programs(bundles):
 out={};by=defaultdict(list)
 for artifact,manifest in bundles:
  m=load(manifest);check(sha(artifact)==m['artifact']['sha256'],'dependency artifact')
  c=sqlite3.connect(Path(artifact).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
  q="""select sf.relative_path,p.id,p.native_id from quest_talk_physical_container p join source_record r on r.id=p.source_record_id join source_file sf on sf.id=r.source_file_id where p.kind='talk_root' order by sf.relative_path"""
  for path,cid,native in c.execute(q):
   check(path not in out,'program overlap');x=(m['ingest_run_id'],cid,str(native));out[path]=x;by[int(native)].append((path,*x))
  c.close()
 return out,by
def verify(root,scope_path,artifact,manifest_path,bundles):
 started=time.perf_counter();root=Path(root);scope=load(scope_path);m=load(manifest_path);programs,by=expected_programs(bundles)
 check(sha(ROOT/scope['shape_manifest']['path'])==scope['shape_manifest']['sha256'],'shape manifest seal')
 terminal=load(ROOT/scope['terminal_evidence']['path']);check(sha(ROOT/scope['terminal_evidence']['path'])==scope['terminal_evidence']['sha256'] and terminal['status']=='terminal_under_frozen_evidence' and not terminal['unexamined_known_discriminators'],'terminal evidence binding')
 actual_dependencies=[{'runtime_manifest':str(Path(p).resolve().relative_to(ROOT)),'sha256':sha(p),'artifact':load(p)['artifact'],'ingest_run_id':load(p)['ingest_run_id']} for _,p in bundles];check(actual_dependencies==scope['accepted_program_dependencies'],'accepted program dependencies')
 check(subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()==[COMMIT,TREE],'source pins')
 shapes=load(ROOT/scope['shape_manifest']['path'])['shapes'];signature={tuple(x['root_keys']):x for x in shapes};check(len(signature)==len(shapes),'shape uniqueness')
 tree=subprocess.check_output(['git','-C',str(root),'ls-tree','-r',COMMIT,'--','BinOutput/Talk'],text=True).splitlines();blobs={x.split('\t',1)[1]:x.split('\t',1)[0].split()[2] for x in tree}
 expected_files=[];expected_refs=[];expected_candidates=[]
 for p in sorted((root/'BinOutput/Talk').rglob('*.json')):
  rel=p.relative_to(root).as_posix();raw=p.read_bytes();doc=json.loads(raw.decode('utf-8-sig'));spec=signature.get(tuple(sorted(doc)));check(spec is not None,'unclassified signature '+rel)
  kind=spec['kind'];classification='mapped_program_reuse' if kind=='talk_root' else 'reference_wrapper' if kind=='reference_wrapper' else 'mapping_unresolved_terminal';fid=ident('file',rel)
  if classification=='mapped_program_reuse':
   check(rel in programs and str(doc[IDS[spec['mapping']]])==programs[rel][2],'accepted exact locator '+rel);target=('quest_talk_pilot','quest-talk-evidence-v7',programs[rel][0],programs[rel][1]);body=None
  else:
   target=(None,None,None,None);body=raw
   if classification=='mapping_unresolved_terminal':check(rel==scope['terminal_singleton'] and doc=={'defaultVocalBoneName':6317},'terminal singleton')
  blob=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest();check(blob==blobs.get(rel),'pinned Git blob '+rel)
  expected_files.append((fid,m['release']['run_id'],rel,blob,hashlib.sha256(raw).hexdigest(),len(raw),canonical(sorted(doc)),classification,spec['mapping'],body,*target))
  if classification=='reference_wrapper':
   field=spec['reference_field'];items=[doc] if field is None else doc[field];key,_=REFS[spec['mapping']]
   for i,item in enumerate(items):
    pointer='/' if field is None else f'/{field}/{i}';native=item[key];check(type(native) is int and native>=0,'reference locator');rid=ident('reference',rel,pointer);hits=by[native];state='known_candidate_set' if hits else 'mapping_unresolved_terminal_namespace'
    expected_refs.append((rid,fid,pointer,str(native),canonical(item),state,len(hits),1,0))
    for path,run,cid,nid in hits:expected_candidates.append((ident('candidate',rid,path),rid,path,'/',nid,'quest_talk_pilot','quest-talk-evidence-v7',run,cid))
 check(len(expected_files)==55925 and len(programs)==50862,'complete denominator')
 implementation={p:sha(ROOT/p) for p in IMPLEMENTATION};parser=ident('parser',implementation);run=ident('run',SNAPSHOT,parser,sha(scope_path),CONTRACT)
 check(m['release']['implementation']==implementation and m['release']['parser_build_id']==parser and m['release']['run_id']==run,'independent parser/run identity')
 before=(Path(artifact).stat().st_ino,Path(artifact).stat().st_size,Path(artifact).stat().st_mtime_ns,sha(artifact));check(before[1]==m['artifact']['size_bytes'] and before[3]==m['artifact']['sha256'],'artifact seal')
 c=sqlite3.connect(Path(artifact).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
 for table,expected in [('talk_physical_file',expected_files),('talk_physical_reference',expected_refs),('talk_physical_candidate',expected_candidates)]:
  actual=c.execute('select * from '+table+' order by id').fetchall();expected.sort(key=lambda x:x[0]);check(actual==expected,'independent rows '+table)
 release=json.loads(c.execute('select manifest_json from talk_physical_release').fetchone()[0]);check(release==m['release'],'release seal');check(not c.execute('pragma foreign_key_check').fetchall(),'FK');check(c.execute('pragma integrity_check').fetchone()[0]=='ok','integrity')
 def seal_row(row):return canonical([{'bytes_sha256':hashlib.sha256(x).hexdigest(),'size':len(x)} if isinstance(x,bytes) else x for x in row])+'\n'
 logical={t:{'rows':c.execute('select count(*) from '+t).fetchone()[0],'sha256':hashlib.sha256(b''.join(seal_row(x).encode() for x in c.execute('select * from '+t+' order by 1'))).hexdigest()} for t in ('talk_physical_file','talk_physical_reference','talk_physical_candidate')};c.close()
 check(before==(Path(artifact).stat().st_ino,Path(artifact).stat().st_size,Path(artifact).stat().st_mtime_ns,sha(artifact)),'artifact changed')
 return {'status':'pass','files':len(expected_files),'mapped_program_reuse':len(programs),'complement':len(expected_files)-len(programs),'references':len(expected_refs),'candidates':len(expected_candidates),'every_file_exactly_one_classification':True,'accepted_locator_verified':True,'logical_seals':logical,'artifact_sha256':before[3],'wall_seconds':time.perf_counter()-started}
