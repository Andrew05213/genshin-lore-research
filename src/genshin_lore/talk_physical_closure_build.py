from __future__ import annotations
import json,sqlite3
from collections import Counter
from pathlib import Path
from .talk_physical_closure_source import *
ROOT=Path(__file__).resolve().parents[2]
IMPLEMENTATION=('src/genshin_lore/quest_talk_physical_shapes.py','src/genshin_lore/talk_physical_closure_source.py','src/genshin_lore/talk_physical_closure_build.py','schemas/talk-physical-closure-v1.sql')

def build(root,scope_path,output,bundles):
 scope_path=Path(scope_path);output=Path(output);scope=load(scope_path)
 if scope['contract']!='talk-physical-closure-scope-v1' or scope['source_commit']!=COMMIT or scope['source_tree']!=TREE:raise ValueError('scope contract')
 if sha(ROOT/scope['shape_manifest']['path'])!=scope['shape_manifest']['sha256']:raise ValueError('shape seal')
 if sha(ROOT/scope['terminal_evidence']['path'])!=scope['terminal_evidence']['sha256'] or scope['terminal_evidence']['status']!='terminal_under_frozen_evidence':raise ValueError('terminal evidence seal')
 actual=[{'runtime_manifest':str(Path(p).relative_to(ROOT)),'sha256':sha(p),'artifact':load(p)['artifact'],'ingest_run_id':load(p)['ingest_run_id']} for _,p in bundles]
 if actual!=scope['accepted_program_dependencies']:raise ValueError('accepted program dependency binding')
 implementation={p:sha(ROOT/p) for p in IMPLEMENTATION};parser=ident('parser',implementation);run=ident('run',SNAPSHOT,parser,sha(scope_path),CONTRACT)
 files,refs,candidates=scan(root,ROOT/scope['shape_manifest']['path'],bundles)
 observed={'files':len(files),'references':len(refs),'candidates':len(candidates),'classifications':dict(Counter(x['classification'] for x in files)),'shapes':dict(Counter((x['classification']+':'+str(x['shape'])) for x in files)),'relation_states':dict(Counter(x['state'] for x in refs)),'candidate_cardinality':dict(Counter(str(x['known_candidate_count']) for x in refs))}
 if observed!=scope['denominators']:raise ValueError('scope denominator changed')
 with output.open('xb'):pass
 c=sqlite3.connect(output);c.execute('PRAGMA journal_mode=OFF');c.execute('PRAGMA synchronous=OFF');c.execute('PRAGMA foreign_keys=ON');c.executescript((ROOT/'schemas/talk-physical-closure-v1.sql').read_text())
 for x in files:c.execute('INSERT INTO talk_physical_file VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(x['id'],run,x['source_path'],x['source_blob_oid'],x['source_sha256'],x['size_bytes'],x['root_keys_json'],x['classification'],x['shape'],x['raw_bytes'],x.get('target_family'),x.get('target_contract'),x.get('target_run'),x.get('target_occurrence_id')))
 for x in refs:c.execute('INSERT INTO talk_physical_reference VALUES(?,?,?,?,?,?,?,?,?)',tuple(x.values()))
 for x in candidates:c.execute('INSERT INTO talk_physical_candidate VALUES(?,?,?,?,?,?,?,?,?)',tuple(x.values()))
 release={'family':FAMILY,'family_contract_version':CONTRACT,'run_id':run,'parser_build_id':parser,'snapshot_id':SNAPSHOT,'scope_sha256':sha(scope_path),'implementation':implementation,'counts':{'talk_physical_file':len(files),'talk_physical_reference':len(refs),'talk_physical_candidate':len(candidates)},**observed,'physical_talk_tree_classification_complete':True,'mapped_program_bodies_copied':False,'new_dialogue_nodes':0,'new_texts':0,'new_graph_pointers':0,'semantic_mapping_resolution':'partial_with_terminal_unresolved','full_talk':False,'full_7_0':False}
 c.execute('INSERT INTO talk_physical_release VALUES(1,?)',(canonical(release),));c.commit()
 if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('FK')
 c.execute('VACUUM');c.close()
 return {**release,'artifact':{'size_bytes':output.stat().st_size,'sha256':sha(output)}}
