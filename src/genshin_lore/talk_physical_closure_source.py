"""Frozen Talk-tree classification built from the reviewed shape contract."""
from __future__ import annotations
import hashlib,json,sqlite3,subprocess
from collections import defaultdict
from pathlib import Path
from .quest_talk_physical_shapes import classify,references,DIALOGUES

COMMIT='26df1dfbdf05a82bbb1d97506859f3e1c40718d8'
TREE='d891ef1d95fdd6c27a1da8a32ea7a61b483647a6'
SNAPSHOT='anime-game-data:7.0:'+COMMIT
FAMILY='talk_physical_archive';CONTRACT='talk-physical-closure-v1'

def canonical(x):return json.dumps(x,ensure_ascii=True,sort_keys=True,separators=(',',':'))
def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def ident(*parts):return 'talk-physical:'+hashlib.sha256(canonical(parts).encode()).hexdigest()[:40]
def load(path):return json.loads(Path(path).read_bytes().decode('utf-8-sig'))

def accepted_programs(bundles):
 out={};by_native=defaultdict(list)
 for artifact,manifest_path in bundles:
  m=load(manifest_path);run=m['ingest_run_id']
  if m['family_contract_version']!='quest-talk-evidence-v6' or not m['product_accepted']:raise ValueError('accepted v6 dependency required')
  if Path(artifact).stat().st_size!=m['artifact']['size_bytes'] or sha(artifact)!=m['artifact']['sha256']:raise ValueError('accepted v6 artifact seal')
  c=sqlite3.connect(Path(artifact).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
  q="""SELECT sf.relative_path,p.id,p.native_id FROM quest_talk_physical_container p JOIN source_record r ON r.id=p.source_record_id JOIN source_file sf ON sf.id=r.source_file_id WHERE p.kind='talk_root' ORDER BY sf.relative_path"""
  for path,cid,native in c.execute(q):
   if path in out:raise ValueError('accepted program overlap')
   value={'source_path':path,'target_family':'quest_talk_pilot','target_contract':'quest-talk-evidence-v7','target_run':run,'target_occurrence_id':cid,'native_id':str(native)}
   out[path]=value;by_native[int(native)].append(value)
  c.close()
 return out,by_native

def scan(root,shape_manifest,bundles,include_raw=True):
 root=Path(root);shapes=load(shape_manifest)['shapes'];programs,by_native=accepted_programs(bundles)
 pins=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()
 if pins!=[COMMIT,TREE]:raise ValueError('source pin mismatch')
 tree_lines=subprocess.check_output(['git','-C',str(root),'ls-tree','-r',COMMIT,'--','BinOutput/Talk'],text=True).splitlines()
 blobs={line.split('\t',1)[1]:line.split('\t',1)[0].split()[2] for line in tree_lines}
 files=[];refs=[];candidates=[]
 for path in sorted((root/'BinOutput/Talk').rglob('*.json')):
  rel=path.relative_to(root).as_posix();raw=path.read_bytes();doc=json.loads(raw.decode('utf-8-sig'));spec=classify(doc,shapes)
  if spec['kind']=='talk_root':
   if rel not in programs:raise ValueError('mapped program absent from accepted v6 union: '+rel)
   classification='mapped_program_reuse';target=programs[rel];body=None
  elif spec['kind']=='reference_wrapper':classification='reference_wrapper';target={};body=raw if include_raw else None
  else:
   if rel!='BinOutput/Talk/NpcOther/12634.json' or doc!={'defaultVocalBoneName':6317}:raise ValueError('unreviewed unresolved root')
   classification='mapping_unresolved_terminal';target={};body=raw if include_raw else None
  fid=ident('file',rel)
  blob=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
  if blob!=blobs.get(rel):raise ValueError('source bytes differ from pinned Git blob: '+rel)
  files.append({'id':fid,'source_path':rel,'source_blob_oid':blob,'source_sha256':hashlib.sha256(raw).hexdigest(),'size_bytes':len(raw),'root_keys_json':canonical(sorted(doc)),'classification':classification,'shape':spec['mapping'],'raw_bytes':body,**target})
  if classification=='reference_wrapper':
   for observation in references(doc,spec['reference_field'],spec['mapping']):
    rid=ident('reference',rel,observation['pointer']);hits=by_native[observation['native_id']]
    state='known_candidate_set' if hits else 'mapping_unresolved_terminal_namespace'
    refs.append({'id':rid,'file_id':fid,'pointer':observation['pointer'],'raw_native_id':str(observation['native_id']),'raw_json':canonical(observation['raw']),'state':state,'known_candidate_count':len(hits),'physical_scan_complete':1,'semantic_search_complete':0})
    for hit in hits:candidates.append({'id':ident('candidate',rid,hit['source_path']),'reference_id':rid,'target_source_path':hit['source_path'],'target_source_pointer':'/','target_native_id':hit['native_id'],'target_family':hit['target_family'],'target_contract':hit['target_contract'],'target_run':hit['target_run'],'target_occurrence_id':hit['target_occurrence_id']})
 if len(programs)!=50862 or len(files)!=55925:raise ValueError('Talk denominator changed')
 return files,refs,candidates
