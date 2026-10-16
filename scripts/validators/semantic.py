"""Independent full mapping/vector oracle; SQL is verification, not Agent access."""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
import hnswlib
import numpy as np
from genshin_lore.research_refs import decode_ref
from genshin_lore.semantic_discovery import sha256_file


def require(condition, message):
 if not condition:raise ValueError('research verification failed: '+str(message))


def verify(index, build_dir):
    report=json.loads((build_dir/'build.json').read_text());config=report['config']
    require(sha256_file(index)==config['research_index_sha256'], 'semantic.py:15 validation condition failed')
    source=sqlite3.connect(f'file:{index}?mode=ro&immutable=1',uri=True);source.row_factory=sqlite3.Row
    meta=sqlite3.connect(f'file:{build_dir / "metadata.sqlite"}?mode=ro&immutable=1',uri=True);meta.row_factory=sqlite3.Row
    vectors=np.load(build_dir/'vectors.npy',mmap_mode='r')
    ann=hnswlib.Index(space='cosine',dim=384);ann.load_index(str(build_dir/'index.hnsw'))
    require(ann.get_current_count()==len(vectors)==config['unique_candidate_count'], 'semantic.py:20 validation condition failed')
    n=0;seen=set();digest=hashlib.sha256();max_diff=0.0
    for item in meta.execute('SELECT * FROM semantic_occurrence ORDER BY vector_id'):
        row=source.execute('SELECT * FROM research_text WHERE id=?',(item['research_text_id'],)).fetchone()
        require(row is not None, 'semantic.py:24 validation condition failed')
        identity=json.loads(item['source_identity_json'])
        require(all(row[k]==v for k,v in identity.items()), 'semantic.py:26 validation condition failed')
        require(row['locale']=='CHS', 'semantic.py:27 validation condition failed')
        require(hashlib.sha256(row['raw_text'].encode()).hexdigest()==row['text_sha256']==item['text_sha256'], 'semantic.py:28 validation condition failed')
        segment=row['raw_text'][item['window_start']:item['window_end']]
        require(segment and hashlib.sha256(segment.encode()).hexdigest()==item['window_sha256'], 'semantic.py:30 validation condition failed')
        ref=decode_ref(item['evidence_ref'])
        require(ref=={'v':2,'kind':'text','corpus':config['corpus_ref'],'source':row['source_key'],
                     'family':row['evidence_family'],'contract':row['evidence_contract'],'parser':row['evidence_parser'],
                     'run':row['evidence_run'],'occurrence':row['occurrence_id'],'child':row['child_id'],'index':row['id']}, 'semantic.py:32 validation condition failed')
        require(item['vector_id']==n, 'semantic.py:35 validation condition failed')
        digest.update(json.dumps(tuple(item),ensure_ascii=False,separators=(',',':')).encode()+b'\n')
        seen.add(row['id']);n+=1
    require(digest.hexdigest()==report['input_rowset_sha256'], 'semantic.py:38 validation condition failed')
    require(len(seen)==config['physical_occurrence_count'], 'semantic.py:39 validation condition failed')
    for start in range(0,n,1024):
        actual=ann.get_items(list(range(start,min(n,start+1024))))
        expected=vectors[start:start+len(actual)]
        require(np.all(np.isfinite(actual)) and np.all(np.isfinite(expected)), 'semantic.py:43 validation condition failed')
        require(np.all(np.abs(np.linalg.norm(expected,axis=1)-1)<1e-5), 'semantic.py:44 validation condition failed')
        max_diff=max(max_diff,float(np.max(np.abs(actual-expected))))
    require(max_diff<1e-6, 'semantic.py:46 validation condition failed')
    require(sha256_file(build_dir/'vectors.npy')==report['vectors_sha256'], 'semantic.py:47 validation condition failed')
    for key,name in [('metadata','metadata.sqlite'),('hnsw','index.hnsw')]:
        require(sha256_file(build_dir/name)==config['artifacts'][key]['sha256'], 'semantic.py:49 validation condition failed')
    meta.close();source.close()
    return {'status':'PASS','full_mapping_rows':n,'physical_occurrences':len(seen),
            'input_rowset_sha256':digest.hexdigest(),'vectors_sha256':report['vectors_sha256'],
            'ann_to_original_vector_max_absolute_difference':max_diff,
            'scope':'complete row/window/ref/coordinates and stored vector binding, not relevance or logical identity'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--index',type=Path,required=True);p.add_argument('--build-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--compare',type=Path)
    a=p.parse_args();result=verify(a.index,a.build_dir)
    if a.compare:
        other=verify(a.index,a.compare)
        left=json.loads((a.build_dir/'build.json').read_text());right=json.loads((a.compare/'build.json').read_text())
        # ANN serialization may differ without different input/vector values.
        # Compare deterministic metadata independently from each binary seal.
        def deterministic_config(report):
            config=json.loads(json.dumps(report['config']))
            config['artifacts'].pop('hnsw')
            return config
        require(deterministic_config(left)==deterministic_config(right), 'semantic.py:69 validation condition failed')
        require(left['input_rowset_sha256']==right['input_rowset_sha256'], 'semantic.py:70 validation condition failed')
        require(left['vectors_sha256']==right['vectors_sha256'], 'semantic.py:71 validation condition failed')
        result['fresh_ab']={'input_equal':True,'vector_bytes_equal':True,'deterministic_metadata_equal':True,
                            'ann_binary_equal':sha256_file(a.build_dir/'index.hnsw')==sha256_file(a.compare/'index.hnsw'),
                            'a_seconds':left['seconds'],'b_seconds':right['seconds']}
    if a.output.exists():raise FileExistsError(a.output)
    a.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
