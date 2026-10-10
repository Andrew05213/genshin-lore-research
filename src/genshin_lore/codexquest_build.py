"""Deterministic standalone CodexQuest physical archive builder."""
from __future__ import annotations
import json, sqlite3
from collections import Counter
from pathlib import Path
from .codexquest_source import FAMILY,CONTRACT,SNAPSHOT,COMMIT,TREE,analyze,canonical,ident,load,project,sha

ROOT=Path(__file__).resolve().parents[2]
IMPLEMENTATION=('src/genshin_lore/source_integrity.py','src/genshin_lore/codexquest_source.py','src/genshin_lore/codexquest_build.py','schemas/codexquest-archive-v1.sql')
TABLES=('codexquest_container','codexquest_body_chunk','codexquest_child','codexquest_relation','codexquest_candidate','codexquest_text_slot','codexquest_text_occurrence','codexquest_text_candidate')

def build(source,scope_path,output,core_artifact,narrative_artifact,narrative_manifest):
 source=Path(source);scope_path=Path(scope_path);output=Path(output);scope=load(scope_path)
 if scope['contract']!='codexquest-archive-scope-v1' or scope['source_commit']!=COMMIT or scope['source_tree']!=TREE:raise ValueError('scope')
 if Path(narrative_manifest).resolve()!=ROOT/scope['quest_narrative_manifest'] or sha(narrative_manifest)!=scope['quest_narrative_manifest_sha256']:raise ValueError('Quest Narrative manifest scope binding')
 if Path(core_artifact).stat().st_size!=scope['quest_codex_dependency']['artifact_size_bytes'] or sha(core_artifact)!=scope['quest_codex_dependency']['sha256']:raise ValueError('QuestCodex core artifact scope binding')
 from .source_integrity import pinned_file_seal
 if [pinned_file_seal(source,COMMIT,x['path']) for x in scope['files']]!=scope['files'] or analyze(source)!=scope['denominators']:raise ValueError('source denominator/seal')
 implementation={p:sha(ROOT/p) for p in IMPLEMENTATION};parser=ident('parser',implementation);run=ident('run',SNAPSHOT,parser,sha(scope_path),CONTRACT,sha(narrative_manifest),scope['quest_codex_dependency']['sha256'])
 data=project(source,run,core_artifact,narrative_artifact,narrative_manifest);counts={t:len(v) for t,v in zip(TABLES,data)}
 containers,body_chunks,children,relations,candidates,slots,text_occurrences,texts=data
 release={'family':FAMILY,'family_contract_version':CONTRACT,'run_id':run,'parser_build_id':parser,'snapshot_id':SNAPSHOT,'scope_sha256':sha(scope_path),'quest_narrative_manifest_sha256':sha(narrative_manifest),'quest_codex_dependency':scope['quest_codex_dependency'],'implementation':implementation,'counts':counts,'shape_counts':dict(Counter(x['shape'] for x in containers)),'child_kinds':dict(Counter(x['kind'] for x in children)),'relation_kinds':dict(Counter(x['kind'] for x in relations)),'relation_states':dict(Counter(x['status'] for x in relations)),'text_slots':dict(Counter(x['status'] for x in slots)),'text_owners':scope['denominators']['text_owners'],'physical_text_occurrences':len(text_occurrences),'text_candidate_links':len(texts),'corpus_complete':True,'full_questcodex_semantic_identity':False,'full_quest_gameplay':False,'full_talk':False,'full_7_0':False,'copied_existing_family_body':False}
 output.parent.mkdir(parents=True,exist_ok=True);output.touch(exist_ok=False);c=sqlite3.connect(output);c.execute('pragma page_size=4096');c.execute('pragma journal_mode=off');c.execute('pragma synchronous=off');c.execute('pragma foreign_keys=on');c.executescript((ROOT/'schemas/codexquest-archive-v1.sql').read_text())
 try:
  for table,rows in zip(TABLES,data):
   keys=tuple(rows[0]) if rows else ();sql='insert into '+table+'('+','.join(keys)+') values('+','.join('?'*len(keys))+')';c.executemany(sql,(tuple(row[k] for k in keys) for row in rows))
  c.execute('insert into codexquest_release values(1,?)',(canonical(release),));c.commit()
  if c.execute('pragma foreign_key_check').fetchall():raise ValueError('FK')
  c.execute('vacuum');c.close()
 except Exception:
  c.close();output.unlink(missing_ok=True);raise
 return {**release,'artifact':{'size_bytes':output.stat().st_size,'sha256':sha(output)}}

__all__=['build','IMPLEMENTATION','TABLES']
