#!/usr/bin/env python3
"""Local, no-clobber 7.1 overlay using existing semantic/lexical contracts.

Never mutates/rebuilds the Research index or deduplicates physical occurrences.
"""
import argparse
import hashlib
import json
import sqlite3
import time
from importlib.metadata import version
from pathlib import Path

import hnswlib
import numpy as np
from fastembed import TextEmbedding
from genshin_lore.lexical_discovery import build_lexicon, text_ref
from genshin_lore.semantic_discovery import sha256_file, tree_sha256

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
REVISION = 'faf4aa4225822f3bc6376869cb1164e8e3feedd0'


def windows(text, size=400, stride=360):
    for start in range(0, len(text), stride):
        yield start, min(len(text), start+size), text[start:start+size]
        if start+size >= len(text):
            break


def selected_rows(db, scope):
    for key, fields in sorted(scope['source_fields'].items()):
        sql = "SELECT * FROM research_text WHERE source_key=? AND locale=? AND length(raw_text)>=?"
        args = [key, scope['locale'], scope['minimum_characters']]
        if fields != ['*']:
            sql += ' AND owner_field IN ('+','.join('?' for _ in fields)+')'
            args += fields
        for row in db.execute(sql+' ORDER BY id', args):
            # Reject support-only strings without natural language letters.
            if any('\u3400' <= c <= '\u9fff' for c in row['raw_text']):
                yield row


def build(index, output, cache):
    if output.exists():
        raise FileExistsError('no-clobber: '+str(output))
    # Same project+worktree+/tmp accounting as storage governance, not merely
    # free bytes in the output filesystem. A/B runs are sequential.
    from govern_research_storage import dependencies, inventory
    capacity=inventory(dependencies())
    reserve=2_000_000_000
    if capacity['unknown'] or capacity['hard_headroom_bytes']<reserve or capacity['host_free_bytes']<reserve:
        raise ValueError('overlay peak preflight requires 2GB measured headroom and readable scope')
    scope_path=ROOT/'manifests/research/research71-semantic-scope-v1.json'
    scope=json.loads(scope_path.read_text())
    if sha256_file(index)!=scope['index_sha256']:
        raise ValueError('exact 7.1 index mismatch')
    if any(Path(str(index)+s).exists() for s in ('-wal','-shm','-journal')):
        raise ValueError('index sidecar present')
    output.mkdir(parents=True)
    started=time.perf_counter()
    db=sqlite3.connect(f'file:{index}?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
    model_tree=tree_sha256(cache/'model');cache_tree=tree_sha256(cache)
    if model_tree!='a7afbe9e63e0060cf7ca4a995e11ee5c916ff0c129b3096b6309eb530427c19f':
        raise ValueError('pinned local model tree mismatch')
    meta=sqlite3.connect(output/'metadata.sqlite')
    meta.executescript('''
    CREATE TABLE semantic_release(id INTEGER PRIMARY KEY,corpus_ref TEXT,research_index_sha256 TEXT,model_id TEXT,model_revision TEXT,model_tree_sha256 TEXT,dimension INTEGER,metric TEXT,preprocessor TEXT,candidate_extractor TEXT,selection TEXT,unique_candidate_count INTEGER,physical_occurrence_count INTEGER);
    CREATE TABLE semantic_occurrence(vector_id INTEGER PRIMARY KEY,research_text_id INTEGER NOT NULL,evidence_ref TEXT NOT NULL,text_sha256 TEXT NOT NULL,window_start INTEGER NOT NULL,window_end INTEGER NOT NULL,window_sha256 TEXT NOT NULL,source_key TEXT NOT NULL,source_identity_json TEXT NOT NULL);
    CREATE INDEX occurrence_text ON semantic_occurrence(research_text_id,vector_id);
    ''')
    inputs=[];mapping_digest=hashlib.sha256();counts={};physical=0
    for row in selected_rows(db,scope):
        physical+=1;counts[row['source_key']]=counts.get(row['source_key'],0)+1
        identity={k:row[k] for k in ('source_key','evidence_family','evidence_contract','evidence_parser','evidence_run','occurrence_id','child_id','text_source_path','text_source_pointer','physical_pair_ordinal','text_sha256','locale','owner_field')}
        for start,end,text in windows(row['raw_text'],scope['window_characters'],scope['window_stride']):
            vector_id=len(inputs);inputs.append(text)
            record=(vector_id,row['id'],text_ref(row,scope['corpus_ref']),row['text_sha256'],start,end,hashlib.sha256(text.encode()).hexdigest(),row['source_key'],json.dumps(identity,ensure_ascii=False,sort_keys=True,separators=(',',':')))
            meta.execute('INSERT INTO semantic_occurrence VALUES(?,?,?,?,?,?,?,?,?)',record)
            mapping_digest.update(json.dumps(record,ensure_ascii=False,separators=(',',':')).encode()+b'\n')
    n=len(inputs)
    if not n:raise ValueError('empty narrative input')
    print(json.dumps({'stage':'selection','physical':physical,'vectors':n,'counts':counts}),flush=True)
    vectors=np.lib.format.open_memmap(output/'vectors.npy',mode='w+',dtype='float32',shape=(n,384))
    model=TextEmbedding(model_name=MODEL,cache_dir=str(cache),specific_model_path=str(cache/'model'),local_files_only=True,threads=2)
    ann=hnswlib.Index(space='cosine',dim=384);ann.init_index(max_elements=n,ef_construction=80,M=12,random_seed=100);ann.set_num_threads(1)
    for start in range(0,n,64):
        batch=np.asarray(list(model.embed(inputs[start:start+64],batch_size=64)),dtype=np.float32)
        if batch.shape!=(min(64,n-start),384) or not np.all(np.isfinite(batch)):
            raise ValueError('embedding shape/finite violation')
        norms=np.linalg.norm(batch,axis=1)
        if np.any(norms<=0):raise ValueError('zero vector')
        batch=batch/norms[:,None];vectors[start:start+len(batch)]=batch
        ann.add_items(batch,np.arange(start,start+len(batch)))
        if start%2048==0:print(json.dumps({'stage':'embedding','done':start+len(batch),'total':n,'seconds':time.perf_counter()-started}),flush=True)
    vectors.flush();ann.save_index(str(output/'index.hnsw'))
    selection=hashlib.sha256(scope_path.read_bytes()).hexdigest()
    meta.execute('INSERT INTO semantic_release VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?)',(scope['corpus_ref'],scope['index_sha256'],MODEL,REVISION,model_tree,384,'cosine','physical-source-window-400-stride360-v1','research71-narrative-window-v1',selection,n,physical))
    meta.commit()
    if meta.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('metadata integrity')
    meta.close();db.close()
    lex=build_lexicon(index,output/'lexicon.sqlite',scope['corpus_ref'],scope['index_sha256'])
    lex['anchor_extraction_version']=lex.pop('extraction_version')
    lex['sha256']=sha256_file(output/'lexicon.sqlite')
    config={'contract':'semantic-discovery-overlay-v1','corpus_ref':scope['corpus_ref'],'research_index_sha256':scope['index_sha256'],'release_binding':scope['baseline_release'],'model':{'id':MODEL,'revision':REVISION,'license':'Apache-2.0','path_relative':'model','tree_sha256':model_tree,'cache_tree_sha256':cache_tree,'threads':2},'dimension':384,'metric':'cosine','preprocessor':'physical-source-window-400-stride360-v1','candidate_extractor':'research71-narrative-window-v1','selection':selection,'unique_candidate_count':n,'physical_occurrence_count':physical,'query_ef':200,'query_candidate_k':1000,'semantic_indexed_scopes':scope,'semantic_unsupported_scopes':scope['excluded'],'lexicon':lex,'artifacts':{key:{'size_bytes':(output/name).stat().st_size,'sha256':sha256_file(output/name)} for key,name in [('metadata','metadata.sqlite'),('hnsw','index.hnsw')]}}
    report={'config':config,'input_rowset_sha256':mapping_digest.hexdigest(),'vectors_sha256':sha256_file(output/'vectors.npy'),'source_counts':counts,'seconds':time.perf_counter()-started,'dependencies':{x:version(x) for x in ['fastembed','onnxruntime','numpy','hnswlib']},'hnsw_parameters':{'M':12,'ef_construction':80,'seed':100,'threads':1},'scope_sha256':selection}
    (output/'build.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'stage':'complete','seconds':report['seconds']}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--index',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--model-cache',type=Path,required=True)
    a=p.parse_args();build(a.index,a.output,a.model_cache)
