"""Deterministic standalone builder for native Quest review evidence."""
from __future__ import annotations
import json,sqlite3
from collections import Counter
from pathlib import Path
from .quest_review_source import FAMILY,CONTRACT,SNAPSHOT,COMMIT,TREE,analyze,canonical,file_seal,ident,load,project,sha

ROOT=Path(__file__).resolve().parents[2];IMPLEMENTATION=('src/genshin_lore/quest_review_source.py','src/genshin_lore/quest_review_build.py','schemas/quest-review-v1.sql');TABLES=('quest_review_row','quest_review_relation','quest_review_candidate','quest_review_text_slot','quest_review_text_candidate')
def build(source,scope_path,output,narrative_artifact,narrative_manifest):
 source=Path(source);scope_path=Path(scope_path);output=Path(output);scope=load(scope_path)
 if scope['contract']!='quest-review-scope-v1' or scope['source_commit']!=COMMIT or scope['source_tree']!=TREE:raise ValueError('scope')
 if [file_seal(source,x['path']) for x in scope['files']]!=scope['files'] or analyze(source)!=scope['denominators']:raise ValueError('source denominator/seal')
 implementation={p:sha(ROOT/p) for p in IMPLEMENTATION};parser=ident('parser',implementation);run=ident('run',SNAPSHOT,parser,sha(scope_path),CONTRACT,sha(narrative_manifest));rows,relations,candidates,slots,texts=project(source,run,narrative_artifact,narrative_manifest)
 counts={t:len(x) for t,x in zip(TABLES,(rows,relations,candidates,slots,texts))};release={'family':FAMILY,'family_contract_version':CONTRACT,'run_id':run,'parser_build_id':parser,'snapshot_id':SNAPSHOT,'scope_sha256':sha(scope_path),'quest_narrative_manifest_sha256':sha(narrative_manifest),'implementation':implementation,'counts':counts,'rows':dict(Counter(x['kind'] for x in rows)),'relations':dict(Counter(x['kind'] for x in relations)),'relation_states':dict(Counter(x['status'] for x in relations)),'text_slots':dict(Counter(x['status'] for x in slots)),'text_owners':scope['denominators']['text_owners'],'texts':len(texts),'corpus_complete':True,'full_quest_review_body_tree':False,'full_quest_gameplay':False,'full_7_0':False,'copied_quest_or_talk_body':False}
 output.parent.mkdir(parents=True,exist_ok=True);output.touch(exist_ok=False);c=sqlite3.connect(output);c.execute('pragma page_size=4096');c.execute('pragma journal_mode=off');c.execute('pragma synchronous=off');c.execute('pragma foreign_keys=on');c.executescript((ROOT/'schemas/quest-review-v1.sql').read_text())
 try:
  for table,data in zip(TABLES,(rows,relations,candidates,slots,texts)):
   keys=tuple(data[0]) if data else ();sql='insert into '+table+'('+','.join(keys)+') values('+','.join('?'*len(keys))+')'
   c.executemany(sql,(tuple(x[k] for k in keys) for x in data))
  c.execute('insert into quest_review_release values(1,?)',(canonical(release),));c.commit()
  if c.execute('pragma foreign_key_check').fetchall():raise ValueError('FK')
  c.execute('vacuum');c.close()
 except Exception:
  c.close();output.unlink(missing_ok=True);raise
 artifact={'size_bytes':output.stat().st_size,'sha256':sha(output)}
 return {**release,'artifact':artifact}
