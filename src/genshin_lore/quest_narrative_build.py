"""Deterministic Quest narrative delta builder."""
import json,sqlite3
from collections import Counter
from pathlib import Path
from .quest_narrative_source import *
ROOT=Path(__file__).resolve().parents[2]
IMPLEMENTATION=('src/genshin_lore/quest_narrative_source.py','src/genshin_lore/quest_narrative_build.py','schemas/quest-narrative-v1.sql')
TABLES=('quest_narrative_container','quest_narrative_child','quest_narrative_relation','quest_narrative_candidate','quest_narrative_text_slot','quest_narrative_text_candidate')

def build(root,scope_path,output,quest_artifact,quest_manifest):
 scope_path=Path(scope_path);output=Path(output);scope=load(scope_path)
 if scope['contract']!='quest-narrative-scope-v1' or scope['source_commit']!=COMMIT or scope['source_tree']!=TREE:raise ValueError('scope')
 if sha(quest_manifest)!=scope['quest_talk_v7_manifest_sha256']:raise ValueError('v7 manifest binding')
 if [file_seal(root,x['path']) for x in scope['files']]!=scope['files']:raise ValueError('source file seals')
 inventory=git_inventory(root,'BinOutput/Quest');inventory_seal=sha_bytes(canonical(sorted(inventory.items())).encode())
 if len(inventory)!=scope['quest_tree']['files'] or inventory_seal!=scope['quest_tree']['path_blob_sha256']:raise ValueError('Quest tree inventory')
 impl={p:sha(ROOT/p) for p in IMPLEMENTATION};parser=ident('parser',impl);run=ident('run',SNAPSHOT,parser,sha(scope_path),CONTRACT)
 m,containers,children,relations,candidates,slots,texts=scan(root,quest_artifact,quest_manifest)
 observed={'containers':dict(Counter(x['kind'] for x in containers)),'children':dict(Counter(x['mapping_status'] for x in children)),'relations':dict(Counter(x['kind'] for x in relations)),'relation_states':dict(Counter(x['status'] for x in relations)),'candidates':len(candidates),'text_slots':dict(Counter(x['status'] for x in slots)),'texts':len(texts)}
 if observed!=scope['denominators']:raise ValueError('denominator changed')
 output.parent.mkdir(parents=True,exist_ok=True);output.touch(exist_ok=False);c=sqlite3.connect(output);c.execute('pragma journal_mode=off');c.execute('pragma synchronous=off');c.execute('pragma foreign_keys=on');c.executescript((ROOT/'schemas/quest-narrative-v1.sql').read_text())
 for x in containers:
  t=x.pop('target');c.execute('insert into quest_narrative_container values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(x['id'],run,x['kind'],x['source_path'],x['source_ordinal'],x['source_pointer'],x['native_id'],x['source_blob_oid'],x['raw_sha256'],x['raw_json'],x['mapping_status'],*( (t['family'],t['contract'],t['run'],t['occurrence_id']) if t else (None,None,None,None))))
 for x in children:c.execute('insert into quest_narrative_child values(?,?,?,?,?,?,?,?,?,?)',tuple(x.values()))
 for x in relations:
  c.execute('insert into quest_narrative_relation values(?,?,?,?,?,?,?,?,?,?)',tuple(x.values()))
 for x in candidates:
  if x['target_run']=='SELF':
   x['target_run']=run;x['id']=ident('candidate',x['relation_id'],x['target_source_path'],x['target_source_pointer'],run,x['target_occurrence_id'])
  c.execute('insert into quest_narrative_candidate values(?,?,?,?,?,?,?,?,?)',tuple(x.values()))
 for x in slots:c.execute('insert into quest_narrative_text_slot values(?,?,?,?,?,?,?)',tuple(x.values()))
 for x in texts:c.execute('insert into quest_narrative_text_candidate values(?,?,?,?,?,?)',tuple(x.values()))
 counts={t:c.execute('select count(*) from '+t).fetchone()[0] for t in TABLES};release={'family':FAMILY,'family_contract_version':CONTRACT,'run_id':run,'parser_build_id':parser,'snapshot_id':SNAPSHOT,'scope_sha256':sha(scope_path),'implementation':impl,'counts':counts,**observed,'quest_physical_denominator':4466,'quest_physical_processing_complete':True,'talk_body_copied':False,'quest_raw_body_copied':False,'narrative_semantic_resolution':'partial_with_explicit_unresolved','corpus_complete':True,'full_quest_gameplay':False,'full_talk':False,'full_7_0':False}
 c.execute('insert into quest_narrative_release values(1,?)',(canonical(release),));c.commit()
 if c.execute('pragma foreign_key_check').fetchall():raise ValueError('FK')
 c.execute('vacuum');c.close();return {**release,'artifact':{'size_bytes':output.stat().st_size,'sha256':sha(output)}}
