"""Independent pinned source + complete physical wrapper rowset oracle."""
import argparse
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

from genshin_lore.research_index import sha256_file


def mutation_check(artifact):
    """Rollback-only probes on this phase's disposable/new Talk candidate."""
    before=sha256_file(artifact);checks=[];db=sqlite3.connect(artifact)
    try:
        db.execute('BEGIN')
        for recursive in (0,1):
            db.execute(f'PRAGMA recursive_triggers={recursive}')
            for table in ('talk_physical_file','talk_physical_reference','talk_physical_candidate','talk_physical_release'):
                row=db.execute('SELECT * FROM '+table+' LIMIT 1').fetchone()
                names=[r[1] for r in db.execute('PRAGMA table_info('+table+')')]
                sql='INSERT OR REPLACE INTO '+table+' VALUES('+','.join('?' for _ in row)+')'
                operations={'pk_replace':(sql,row),'upsert':('INSERT INTO '+table+' VALUES('+','.join('?' for _ in row)+') ON CONFLICT DO UPDATE SET id=excluded.id',row),'update':('UPDATE '+table+' SET id=id',()),'delete':('DELETE FROM '+table,())}
                if table!='talk_physical_release':
                    changed=list(row);changed[names.index('id')]='new-unique-probe'
                    operations['unique_replace']=(sql,changed)
                for operation,(statement,args) in operations.items():
                    db.execute('SAVEPOINT negative');rejected=False
                    try:
                        try:db.execute(statement,args)
                        except sqlite3.IntegrityError as e:rejected='immutable' in str(e)
                    finally:
                        db.execute('ROLLBACK TO negative');db.execute('RELEASE negative')
                    if not rejected:raise AssertionError((table,recursive,operation))
                    checks.append({'table':table,'recursive_triggers':recursive,'operation':operation,'rejected':True})
    finally:db.rollback();db.close()
    if sha256_file(artifact)!=before or any(Path(str(artifact)+s).exists() for s in ('-wal','-shm','-journal')):raise AssertionError('Talk probes changed seal/sidecar')
    return {'status':'PASS','checks':checks,'seal_unchanged':before,'rollback_only':True,'delivery_new_insert':'mode=ro rejects INSERT; SQL distinct identities remain additive'}


def verify(source, artifact, dependency, expected_seal):
    if artifact.stat().st_size != expected_seal['size_bytes'] or sha256_file(artifact)!=expected_seal['sha256']:
        raise ValueError('Talk physical seal drift')
    before = sha256_file(artifact)
    db=sqlite3.connect(artifact.as_uri()+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
    dep=sqlite3.connect(dependency.as_uri()+'?mode=ro&immutable=1',uri=True);dep.row_factory=sqlite3.Row
    release=json.loads(db.execute('SELECT manifest_json FROM talk_physical_release').fetchone()[0]);qualified=release['dependency']['artifact']
    if sha256_file(dependency)!=qualified['sha256'] or dependency.stat().st_size!=qualified['size_bytes']:
        raise ValueError('Quest/Talk dependency drift')
    pin='9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99'
    if subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()!=pin or subprocess.check_output(['git','-C',str(source),'status','--porcelain','--untracked-files=all']):
        raise ValueError('Talk oracle source pin/cleanliness mismatch')
    entries=subprocess.check_output(['git','-C',str(source),'ls-tree','-rz',pin,'--','BinOutput/Talk']).split(b'\0')
    blobs={p.decode():header.decode().split()[2] for e in entries if e for header,p in [e.split(b'\t',1)] if p.endswith(b'.json')}
    rows=list(db.execute('SELECT * FROM talk_physical_file ORDER BY source_path'))
    if {r['source_path'] for r in rows}!=set(blobs):raise ValueError('full Talk path set mismatch')
    nr=nc=terminal=0
    for r in rows:
        raw=(source/r['source_path']).read_bytes()
        if (r['source_blob_oid'],r['source_sha256'],r['size_bytes'])!=(blobs[r['source_path']],hashlib.sha256(raw).hexdigest(),len(raw)) or hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=blobs[r['source_path']]:
            raise ValueError('Talk source bytes/blob drift')
        old=dep.execute("SELECT * FROM physical_container WHERE source_path=? AND pointer='/'",(r['source_path'],)).fetchone()
        if old is None or old['raw_sha256']!=r['source_sha256']:
            raise ValueError('Talk source root binding drift')
        classification={'talk_root':'mapped_program_reuse','reference_wrapper':'reference_wrapper','mapping_unresolved':'mapping_unresolved_terminal'}[old['kind']]
        if r['classification']!=classification:raise ValueError('Talk shape classification drift')
        if classification=='mapped_program_reuse':
            if r['raw_bytes'] is not None or r['target_occurrence_id']!=old['id'] or r['target_run']!=old['ingest_run_id']:
                raise ValueError('Talk program reuse drift')
        elif r['raw_bytes']!=raw:raise ValueError('Talk wrapper/terminal raw bytes drift')
        actual=list(db.execute('SELECT * FROM talk_physical_reference WHERE file_id=? ORDER BY pointer',(r['id'],)))
        expected=list(dep.execute('SELECT * FROM physical_reference WHERE container_id=? ORDER BY pointer',(old['id'],))) if classification=='reference_wrapper' else []
        if len(actual)!=len(expected):raise ValueError('Talk reference extras/omissions')
        for a,e in zip(actual,expected):
            hits=json.loads(e['candidates_json']);projected=list(db.execute('SELECT * FROM talk_physical_candidate WHERE reference_id=? ORDER BY target_source_path',(a['id'],)))
            if (a['pointer'],a['raw_native_id'],a['raw_json'],a['known_candidate_count'])!=(e['pointer'],e['native_id'],e['raw_json'],len(hits)):
                raise ValueError('Talk reference row mismatch')
            if a['semantic_search_complete']!=0 or a['physical_scan_complete']!=1 or a['state']!=('known_candidate_set' if hits else 'mapping_unresolved_terminal_namespace'):
                raise ValueError('Talk reference semantic promotion')
            expected_tuples=sorted((h['path'],h['pointer'],h['container_id']) for h in hits)
            if [(p['target_source_path'],p['target_source_pointer'],p['target_occurrence_id']) for p in projected]!=expected_tuples:
                raise ValueError('complete Talk candidate set inequality')
            for p in projected:
                target=dep.execute('SELECT * FROM physical_container WHERE id=?',(p['target_occurrence_id'],)).fetchone()
                if target is None or (target['source_path'],target['pointer'],target['native_id'],target['ingest_run_id'])!=(p['target_source_path'],p['target_source_pointer'],p['target_native_id'],p['target_run']):
                    raise ValueError('Talk full second hop identity drift')
                terminal+=target['kind']=='mapping_unresolved'
            nr+=1;nc+=len(projected)
    if nr!=db.execute('SELECT COUNT(*) FROM talk_physical_reference').fetchone()[0] or nc!=db.execute('SELECT COUNT(*) FROM talk_physical_candidate').fetchone()[0]:
        raise ValueError('orphan Talk relations/candidates')
    db.close();dep.close()
    if sha256_file(artifact)!=before or any(Path(str(artifact)+s).exists() for s in ('-wal','-shm','-journal')):raise ValueError('Talk oracle modified artifact')
    return {'status':'PASS','paths':len(rows),'references':nr,'candidates':nc,'terminal_unresolved_target_candidates':terminal,'full_physical_source_set_equality':True,'full_second_hop_verified':True,'artifact_sha256_unchanged':before,'full_logical_talk_claim':False}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--artifact',type=Path,required=True);p.add_argument('--dependency',type=Path,required=True);p.add_argument('--build-report',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args()
    v=verify(a.source.resolve(),a.artifact.resolve(),a.dependency.resolve(),json.loads(a.build_report.read_text())['artifact'])
    with a.report.open('x') as f:json.dump(v,f,ensure_ascii=False,sort_keys=True,indent=2)
    print(v)
