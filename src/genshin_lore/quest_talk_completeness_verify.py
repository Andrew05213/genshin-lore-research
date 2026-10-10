"""Independent successor verification: pinned source -> frozen v4 -> exact v5 rows.

Only low-level serialization/hashing and the historical independent source
verifier are reused. Successor state, occurrence and index checks are separate
from the successor builder.
"""
import hashlib,json,sqlite3,subprocess,time
from pathlib import Path
from .quest_talk_physical_verify import verify as verify_v4
ROOT=Path(__file__).resolve().parents[2]
def enc(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def ident(kind,*v):return kind+':'+hashlib.sha256(enc(v).encode()).hexdigest()[:40]
def check(ok,why):
    if not ok:raise ValueError(why)
def load(p):return json.loads(Path(p).read_text())

def verify_terminal(root,path):
    p=load(path);check(p['status']=='terminal_under_frozen_evidence' and not p['unexamined_known_discriminators'],'unfinished terminal investigation')
    pins=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines();check(pins==[p['source_commit'],p['source_tree']],'terminal source pins')
    names=subprocess.check_output(['git','-C',str(root),'ls-tree','-r','--name-only','HEAD'],text=True).splitlines()
    check(hashlib.sha256(('\n'.join(names)+'\n').encode()).hexdigest()==p['singleton']['source_tree_path_listing_sha256'],'source metadata inventory')
    layout=[n for n in names if n.lower().endswith(('.proto','.fbs','.cs','.dll','.h','.hpp')) or any(w in Path(n).name.lower() for w in ('serialization','layout','schema','mapping'))]
    check(layout==p['singleton']['layout_metadata_paths'],'unexamined layout metadata')
    check(layout==[r['path'] for r in p['singleton']['layout_metadata_observations']],'metadata audit denominator')
    for item in p['singleton']['layout_metadata_observations']:
        doc=load(root/item['path']);check(sha(root/item['path'])==item['sha256'],'metadata source seal')
        kind='keyboard_key_labels' if isinstance(doc,dict) and 'configKeyboardLayouts' in doc else 'geometric_level_blocks' if isinstance(doc,dict) and isinstance(doc.get('blocks'),list) else 'activity_catcafe_rows' if isinstance(doc,list) and all('catId' in r and 'levelId' in r for r in doc) else None
        check(kind is not None and kind==item['observed_kind'],'unreviewed possible layout')
    tree=subprocess.check_output(['git','-C',str(root),'ls-tree','-r','HEAD'],text=True).splitlines()
    blobs={line.split('\t',1)[1]:line.split('\t',1)[0].split()[2] for line in tree}
    bound_paths={r['path'] for r in p['singleton']['key_sources']+p['singleton']['locator_excel_files']+p['singleton']['layout_metadata_observations']+p['alternate_codec']['shards']}|{r['path'] for r in p['alternate_codec']['coordinates']}
    for pair in p['alternate_codec']['counterexample_pairs']:bound_paths.update((pair['alternate'][0],pair['rc'][0]))
    for rel in bound_paths:
        raw=(root/rel).read_bytes();check(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==blobs.get(rel),'terminal input differs from pinned Git blob')
    singleton=p['singleton'];check(load(root/singleton['path'])=={'defaultVocalBoneName':6317},'singleton discriminating bytes')
    matched=subprocess.check_output(['rg','-l','--fixed-strings','defaultVocalBoneName',str(root/'BinOutput'),str(root/'ExcelBinOutput')],text=True).splitlines()
    actual=[{'path':Path(x).relative_to(root).as_posix(),'sha256':sha(x)} for x in sorted(matched)]
    check(actual==singleton['key_sources'],'unexamined key occurrence')
    check([e['path'] for e in singleton['locator_excel_files']]==sorted(p.relative_to(root).as_posix() for p in (root/'ExcelBinOutput').glob('TalkExcelConfigData_*.json')),'complete TalkExcel shard set')
    for entry in singleton['locator_excel_files']:
        check(sha(root/entry['path'])==entry['sha256'],'locator source seal')
        check(not {6317,12634}.intersection(row['id'] for row in load(root/entry['path'])),'singleton native locator discriminator')
    path_refs=subprocess.run(['rg','-l','--fixed-strings','12634.json',*(str(root/d) for d in ('BinOutput','ExcelBinOutput','Readable','Subtitle','TextMap'))],capture_output=True,text=True)
    check(path_refs.returncode==1 and not path_refs.stdout,'unexamined explicit physical path relation')
    codec=p['alternate_codec'];coords=[]
    audit_path=ROOT/codec['audit_anchor']['path'];check(sha(audit_path)==codec['audit_anchor']['sha256'],'frozen shape-group audit')
    audit=load(audit_path);expected_coordinates=[]
    population=sorted(audit['alternate_dialogue_shape_roots']+audit['unknown_quest_roots'],key=lambda r:r['path'])
    for entry in population:
        check(sha(root/entry['path'])==entry['sha256'],'audited alternate source seal')
        doc=load(root/entry['path'])
        for ordinal,node in enumerate(doc.get('GALIDJOEHOC',[])):
            raw=node['AIGJBMCHCJG']
            if raw+512!=raw^512:expected_coordinates.append({'path':entry['path'],'pointer':f'/GALIDJOEHOC/{ordinal}/AIGJBMCHCJG','raw':raw})
    check(codec['coordinates']==expected_coordinates,'complete unique discriminator coordinates')
    anchor=ROOT/codec['counterexample_anchor']['path'];check(sha(anchor)==codec['counterexample_anchor']['sha256'],'frozen counterexamples')
    check(codec['counterexample_pairs']==load(anchor)['paired_counterexamples'] and len(codec['counterexample_pairs'])==3,'complete sealed counterexample set')
    for item in codec['coordinates']:
        doc=load(root/item['path']);value=doc
        for key in item['pointer'].strip('/').split('/'):value=value[int(key)] if isinstance(value,list) else value[key]
        check(value==item['raw'] and value+512!=value^512,'codec coordinate is not discriminating');coords.append(value)
    check(len(coords)==12,'discriminator denominator')
    paths=sorted(x.relative_to(root).as_posix() for x in (root/'TextMap').glob('*.json'))
    check(paths==[s['path'] for s in codec['shards']],'unexamined locale shard')
    for shard in codec['shards']:
        file=root/shard['path'];check(sha(file)==shard['sha256'],'discriminator TextMap seal');mapping=load(file)
        check(not any(str(x) in mapping or str(x+512) in mapping or str(x^512) in mapping for x in coords),'codec discriminating oracle now exists')
    for pair in codec['counterexample_pairs']:
        ap,ai,aval=pair['alternate'];bp,bi,bval=pair['rc'];a=load(root/ap)['GALIDJOEHOC'][ai];b=load(root/bp)['PFALHAKIILD'][bi]
        check(a['AIGJBMCHCJG']==aval and b['OACNIBLFFDI']==bval,'counterexample values')
        check(a['NFIEHACCECI']==b['OIFGMOHKPOI']==pair['raw_native_node_id'] and a.get('GBLICFDCPCK',[])==b.get('KMLAFCBMFEI',[])==pair['successors'],'counterexample graph coordinates')
        check(aval+512!=bval and aval^512!=bval,'counterexample no longer distinguishes unsupported inference')
    return {'status':'pass','singleton_terminal':True,'codec_terminal':True,'shards_checked':len(paths),'discriminating_coordinates':len(coords),'unexamined_known_sources':0}

def verify(root,base,base_manifest,artifact,manifest):
    start=time.perf_counter();m=load(manifest);old=load(base_manifest)
    check(m['family_contract_version']=='quest-talk-evidence-v5','successor contract')
    before=(artifact.stat().st_ino,artifact.stat().st_size,artifact.stat().st_mtime_ns,sha(artifact))
    check(before[1:2]==(m['artifact']['size_bytes'],) and before[3]==m['artifact']['sha256'],'successor artifact seal')
    for suffix in ('-wal','-shm','-journal'):check(not Path(str(artifact)+suffix).exists(),'successor sidecar')
    terminal=manifest.parent/m['terminal_manifest']['path'];check(sha(terminal)==m['terminal_manifest']['sha256'],'terminal manifest seal')
    terminal_result=verify_terminal(root,terminal)
    base_result=verify_v4(root,base,base_manifest,base_manifest.parent/old['scope_manifest']['path'],base_manifest.parent/old['shape_manifest']['path'])
    paths=['src/genshin_lore/quest_talk_completeness_build.py','schemas/quest-talk-completeness-v1.sql'];fp=hashlib.sha256(enc({p:sha(ROOT/p) for p in paths}).encode()).hexdigest()
    check(fp==m['parser_fingerprint'] and m['parser_build_id']=='parser-build:'+fp[:40],'successor parser')
    scope_path=manifest.parent/m['scope_manifest']['path'];scope=load(scope_path);check(sha(scope_path)==m['scope_manifest']['sha256'],'scope seal')
    expected_scope=load(base_manifest.parent/old['scope_manifest']['path'])
    expected_scope.update(family_contract_version='quest-talk-evidence-v5',terminal_manifest_sha256=sha(terminal),base_artifact=old['artifact'],base_ingest_run_id=old['ingest_run_id'],search_contract='covering-index-literal-instr-v1')
    check(scope==expected_scope,'exact successor scope')
    check(scope['base_artifact']==old['artifact'] and scope['base_ingest_run_id']==old['ingest_run_id'] and scope['terminal_manifest_sha256']==sha(terminal),'successor input binding')
    run=ident('ingest-run',old['snapshot_id'],m['parser_build_id'],sha(scope_path),'quest-talk-evidence-v5');check(run==m['ingest_run_id'] and run!=old['ingest_run_id'],'new exact run')
    def connect(p):
        c=sqlite3.connect(p.resolve().as_uri()+'?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row;return c
    a=connect(base);b=connect(artifact)
    for table in ('source_catalog','source_snapshot','source_file','source_record','schema_migration'):
        check([tuple(r) for r in a.execute('SELECT * FROM '+table+' ORDER BY 1')]==[tuple(r) for r in b.execute('SELECT * FROM '+table+' ORDER BY 1')],'unchanged physical provenance '+table)
    check([tuple(r) for r in b.execute('SELECT * FROM parser_build')]==[(m['parser_build_id'],'quest-talk-evidence-v5','physical-completeness-v1',fp,sha(scope_path),'2000-01-01T00:00:00Z')],'parser provenance')
    check([tuple(r) for r in b.execute('SELECT * FROM ingest_run')]==[(run,m['snapshot_id'],m['parser_build_id'],enc(scope),'{}','complete','2000-01-01T00:00:00Z')],'run provenance')
    base_schema_sql="SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'quest_talk_physical_%' AND tbl_name NOT LIKE 'quest_talk_physical_%' ORDER BY type,name"
    check([tuple(r) for r in a.execute(base_schema_sql)]==[tuple(r) for r in b.execute(base_schema_sql)],'base Schema7 structure unchanged')
    for row in a.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'quest_talk_physical_%'"):
        table=row[0]
        if table in ('source_catalog','source_snapshot','source_file','source_record','schema_migration','parser_build','ingest_run'):continue
        check([tuple(r) for r in a.execute('SELECT * FROM '+table+' ORDER BY 1')]==[tuple(r) for r in b.execute('SELECT * FROM '+table+' ORDER BY 1')],'unchanged base rows '+table)
    expected_schema=sqlite3.connect(':memory:');expected_schema.executescript((ROOT/'schemas/quest-talk-completeness-v1.sql').read_text());sql="SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name LIKE 'quest_talk_physical_%' OR tbl_name LIKE 'quest_talk_physical_%' ORDER BY type,name"
    check([tuple(r) for r in b.execute(sql)]==expected_schema.execute(sql).fetchall(),'sealed schema and search indexes')
    coords={r['id']:(r['relative_path'],r['pointer']) for r in a.execute('SELECT q.id,sf.relative_path,sr.pointer FROM quest_talk_physical_container q JOIN source_record sr ON sr.id=q.source_record_id JOIN source_file sf ON sf.id=sr.source_file_id')}
    containers={k:ident('qt-container',run,*coord) for k,coord in coords.items()};node_coords={r['id']:(coords[r['container_id']][0],r['pointer']) for r in a.execute('SELECT id,container_id,pointer FROM quest_talk_physical_node')};nodes={k:ident('qt-node',run,*coord) for k,coord in node_coords.items()}
    check(not set(containers)&set(containers.values()) and not set(nodes)&set(nodes.values()),'cross-run identity collision')
    seals={}
    for table in ('quest_talk_physical_container','quest_talk_physical_node','quest_talk_physical_text','quest_talk_physical_reference'):
        expected=[]
        for row in a.execute('SELECT * FROM '+table):
            v=dict(row);v['ingest_run_id']=run
            if table.endswith('container'):
                v['id']=containers[row['id']]
                if row['kind']=='mapping_unresolved':
                    check(coords[row['id']]==(load(terminal)['singleton']['path'],'/'),'uninvestigated unresolved root')
                    meta=json.loads(row['metadata_json']);meta.update(mapping_status='mapping_unresolved_terminal',terminal_case='singleton_lookup');v['metadata_json']=enc(meta)
            else:
                v['container_id']=containers[row['container_id']]
                if table.endswith('node'):
                    v['id']=nodes[row['id']];obs=json.loads(row['successors_json'])
                    for item in obs:
                        item['local_candidates']=[nodes[x] for x in item['local_candidates']]
                        for x in item['physical_candidates']:x['node_id']=nodes[x['node_id']];x['container_id']=containers[x['container_id']]
                    v['successors_json']=enc(obs)
                elif table.endswith('text'):
                    v['id']=ident('qt-text',run,*node_coords[row['node_id']],row['locale']);v['node_id']=nodes[row['node_id']]
                    if row['status']=='mapping_unresolved':
                        v['status']='mapping_unresolved_terminal';check(row['raw_text'] is None,'unresolved guessed text')
                else:
                    v['id']=ident('qt-ref',run,coords[row['container_id']][0],row['pointer']);candidates=json.loads(row['candidates_json'])
                    for x in candidates:
                        if x['container_id'] is not None:x['container_id']=containers[x['container_id']]
                    v['candidates_json']=enc(candidates)
            expected.append(tuple(v.values()))
        expected.sort(key=lambda r:r[0]);actual=b.execute('SELECT * FROM '+table+' ORDER BY id');digest=hashlib.sha256();count=0
        for row in actual:
            check(count<len(expected) and tuple(row)==expected[count],'independent successor row '+table);digest.update(enc(tuple(row)).encode()+b'\n');count+=1
        check(count==len(expected),'successor denominator '+table);seals[table]={'rows':count,'sha256':digest.hexdigest()}
    states=dict(b.execute('SELECT status,count(*) FROM quest_talk_physical_text GROUP BY status'));check(states==m['texts'] and sum(states.values())==3*seals['quest_talk_physical_node']['rows'],'mutually exclusive exhaustive locale states')
    expected_completeness={'physical_processing_complete':True,'quest_files_processed':old['counts']['quest_files'],'quest_references_processed':b.execute("SELECT count(*) FROM quest_talk_physical_reference r JOIN quest_talk_physical_container c ON c.id=r.container_id WHERE c.kind='quest_root'").fetchone()[0],'known_linked_programs_delivered':True,'delivered_text_slots_classified':True,'expected_processed_slots':sum(states.values()),'text_state_counts':states,'candidate_lookup_physical_scan_complete':True,'lookup_physical_files':old['counts']['lookup_files'],'terminal_unresolved_root_classifications':b.execute("SELECT count(*) FROM quest_talk_physical_container WHERE kind='mapping_unresolved'").fetchone()[0],'mapping_resolution':'partial_with_terminal_unresolved','candidate_classification':'partial_with_terminal_unresolved','semantic_candidate_search_complete':False,'full_talk':False,'full_7_0':False}
    check(m['completeness']==expected_completeness,'complete multidimensional evidence declaration')
    for key in ('family','snapshot_id','counts','graph','candidate_search_complete','unresolved_lookup_paths'):
        check(m[key]==old[key],'inherited declaration '+key)
    check(m['shape_manifest']['sha256']==old['shape_manifest']['sha256']==sha(Path(manifest).parent/m['shape_manifest']['path']),'inherited shape manifest bytes')
    check(m['corpus_complete'] is False and m['product_accepted'] is True and m['coverage']=='declared_quest_linked_evidence_processing_complete','bounded product declaration')
    check(m['schema_extension_sha256']==sha(ROOT/'schemas/quest-talk-completeness-v1.sql') and m['schema_extension_structure']==[list(r) for r in b.execute(sql)],'extension declaration')
    check(not b.execute('PRAGMA foreign_key_check').fetchall(),'successor FK');check(b.execute('PRAGMA integrity_check').fetchone()[0]=='ok','search index integrity')
    a.close();b.close();check(before==(artifact.stat().st_ino,artifact.stat().st_size,artifact.stat().st_mtime_ns,sha(artifact)),'successor changed')
    for suffix in ('-wal','-shm','-journal'):check(not Path(str(artifact)+suffix).exists(),'successor sidecar')
    return {'status':'pass','terminal_evidence':terminal_result,'base_source_verification':base_result,'logical_seals':seals,'text_states':states,'expected_slots':sum(states.values()),'wall_seconds':time.perf_counter()-start,'artifact_sha256':before[3]}
