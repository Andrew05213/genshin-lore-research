"""Explicit v4-to-v5 evidence successor. No source mapping or census replay."""
from pathlib import Path
import hashlib,json,sqlite3,time
from .quest_talk_physical_build import sha,identity
from .quest_talk_physical_shapes import canonical
ROOT=Path(__file__).resolve().parents[2]
CONTRACT='quest-talk-evidence-v5'
TABLES=('quest_talk_physical_container','quest_talk_physical_node','quest_talk_physical_text','quest_talk_physical_reference')

def fingerprint():
    paths=['src/genshin_lore/quest_talk_completeness_build.py','schemas/quest-talk-completeness-v1.sql']
    return hashlib.sha256(canonical({p:sha(ROOT/p) for p in paths}).encode()).hexdigest()

def build(base,base_manifest,terminal_manifest,target):
    started=time.perf_counter();m=json.loads(base_manifest.read_text());proof=json.loads(terminal_manifest.read_text())
    if m['family_contract_version']!='quest-talk-evidence-v4':raise ValueError('explicit frozen v4 input required')
    if base.stat().st_size!=m['artifact']['size_bytes'] or sha(base)!=m['artifact']['sha256']:raise ValueError('base artifact seal')
    if any(Path(str(base)+s).exists() for s in ('-wal','-shm','-journal')):raise ValueError('base sidecar')
    if target.exists():raise FileExistsError(target)
    if proof['status']!='terminal_under_frozen_evidence' or proof['unexamined_known_discriminators']:raise ValueError('terminal proof is not closed')
    parser_fp=fingerprint();parser='parser-build:'+parser_fp[:40]
    scope=json.loads((base_manifest.parent/m['scope_manifest']['path']).read_text())
    scope.update(family_contract_version=CONTRACT,terminal_manifest_sha256=sha(terminal_manifest),base_artifact=m['artifact'],base_ingest_run_id=m['ingest_run_id'],search_contract='covering-index-literal-instr-v1')
    scope_bytes=(json.dumps(scope,indent=2)+'\n').encode();scope_sha=hashlib.sha256(scope_bytes).hexdigest()
    run=identity('ingest-run',m['snapshot_id'],parser,scope_sha,CONTRACT)
    target.parent.mkdir(parents=True,exist_ok=True)
    source=sqlite3.connect(base.resolve().as_uri()+'?mode=ro&immutable=1',uri=True);source.row_factory=sqlite3.Row
    c=sqlite3.connect(target);source.backup(c);c.execute('PRAGMA foreign_keys=OFF')
    for table in reversed(TABLES):c.execute('DROP TABLE '+table)
    c.executescript((ROOT/'schemas/quest-talk-completeness-v1.sql').read_text())
    c.execute('DELETE FROM ingest_run');c.execute('DELETE FROM parser_build')
    c.execute('INSERT INTO parser_build VALUES(?,?,?,?,?,?)',(parser,CONTRACT,'physical-completeness-v1',parser_fp,scope_sha,'2000-01-01T00:00:00Z'))
    c.execute('INSERT INTO ingest_run VALUES(?,?,?,?,?,?,?)',(run,m['snapshot_id'],parser,canonical(scope),'{}','complete','2000-01-01T00:00:00Z'))
    coords={r['id']:(r['relative_path'],r['pointer']) for r in source.execute('SELECT q.id,sf.relative_path,sr.pointer FROM quest_talk_physical_container q JOIN source_record sr ON sr.id=q.source_record_id JOIN source_file sf ON sf.id=sr.source_file_id')}
    cids={old:identity('qt-container',run,*coord) for old,coord in coords.items()}
    nids={r['id']:identity('qt-node',run,coords[r['container_id']][0],r['pointer']) for r in source.execute('SELECT id,container_id,pointer FROM quest_talk_physical_node')}
    node_pointers={r['id']:r['pointer'] for r in source.execute('SELECT id,pointer FROM quest_talk_physical_node')}
    for row in source.execute('SELECT * FROM quest_talk_physical_container ORDER BY id'):
        v=dict(row);old=v['id'];v.update(id=cids[old],ingest_run_id=run)
        if v['kind']=='mapping_unresolved':
            meta=json.loads(v['metadata_json']);meta['mapping_status']='mapping_unresolved_terminal';meta['terminal_case']='singleton_lookup';v['metadata_json']=canonical(meta)
        c.execute('INSERT INTO quest_talk_physical_container VALUES(?,?,?,?,?,?,?)',tuple(v.values()))
    for row in source.execute('SELECT * FROM quest_talk_physical_node ORDER BY id'):
        v=dict(row);v.update(id=nids[v['id']],container_id=cids[v['container_id']],ingest_run_id=run);obs=json.loads(v['successors_json'])
        for item in obs:
            item['local_candidates']=[nids[x] for x in item['local_candidates']]
            for x in item['physical_candidates']:x.update(node_id=nids[x['node_id']],container_id=cids[x['container_id']])
        v['successors_json']=canonical(obs);c.execute('INSERT INTO quest_talk_physical_node VALUES(?,?,?,?,?,?,?,?,?)',tuple(v.values()))
    for row in source.execute('SELECT * FROM quest_talk_physical_text ORDER BY id'):
        v=dict(row);node=node_pointers[v['node_id']]
        v.update(id=identity('qt-text',run,coords[v['container_id']][0],node,v['locale']),node_id=nids[v['node_id']],container_id=cids[v['container_id']],ingest_run_id=run)
        if v['status']=='mapping_unresolved':v['status']='mapping_unresolved_terminal'
        c.execute('INSERT INTO quest_talk_physical_text VALUES(?,?,?,?,?,?,?,?)',tuple(v.values()))
    for row in source.execute('SELECT * FROM quest_talk_physical_reference ORDER BY id'):
        v=dict(row);v.update(id=identity('qt-ref',run,coords[v['container_id']][0],v['pointer']),container_id=cids[v['container_id']],ingest_run_id=run);candidates=json.loads(v['candidates_json'])
        for x in candidates:
            if x['container_id'] is not None:x['container_id']=cids[x['container_id']]
        v['candidates_json']=canonical(candidates);c.execute('INSERT INTO quest_talk_physical_reference VALUES(?,?,?,?,?,?,?,?)',tuple(v.values()))
    c.commit()
    if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('FK failure')
    texts=dict(c.execute('SELECT status,count(*) FROM quest_talk_physical_text GROUP BY status'))
    if sum(texts.values())!=m['counts']['quest_talk_physical_node']*3:raise ValueError('slot closure')
    m.update(contract='quest-talk-completeness-runtime-v1',family_contract_version=CONTRACT,parser_fingerprint=parser_fp,parser_build_id=parser,ingest_run_id=run,texts=texts,product_accepted=True)
    m['schema_extension_structure']=[list(r) for r in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name LIKE 'quest_talk_physical_%' OR tbl_name LIKE 'quest_talk_physical_%' ORDER BY type,name")]
    m['schema_extension_sha256']=sha(ROOT/'schemas/quest-talk-completeness-v1.sql');m['terminal_manifest']={'path':terminal_manifest.name,'sha256':sha(terminal_manifest)}
    m['scope_manifest']={'path':'scope.json','sha256':scope_sha};m['coverage']='declared_quest_linked_evidence_processing_complete'
    m['completeness']={'physical_processing_complete':True,'quest_files_processed':m['counts']['quest_files'],'quest_references_processed':c.execute("SELECT count(*) FROM quest_talk_physical_reference r JOIN quest_talk_physical_container c ON c.id=r.container_id WHERE c.kind='quest_root'").fetchone()[0],'known_linked_programs_delivered':True,'delivered_text_slots_classified':True,'expected_processed_slots':sum(texts.values()),'text_state_counts':texts,'candidate_lookup_physical_scan_complete':True,'lookup_physical_files':m['counts']['lookup_files'],'terminal_unresolved_root_classifications':c.execute("SELECT count(*) FROM quest_talk_physical_container WHERE kind='mapping_unresolved'").fetchone()[0],'mapping_resolution':'partial_with_terminal_unresolved','candidate_classification':'partial_with_terminal_unresolved','semantic_candidate_search_complete':False,'full_talk':False,'full_7_0':False}
    m.pop('delivery_status',None);m.pop('structured_quest_linked_closure_complete',None)
    c.execute('VACUUM');c.close();source.close()
    if target.stat().st_size>2147483648:raise ValueError('2 GiB artifact budget exceeded')
    m['artifact']={'size_bytes':target.stat().st_size,'sha256':sha(target)};m['build_seconds']=time.perf_counter()-started
    if fingerprint()!=parser_fp:raise ValueError('implementation changed during build')
    return m,scope_bytes
