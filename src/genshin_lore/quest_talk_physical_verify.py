"""Independent source reconstruction for the v4 standalone extension.

Does not import builder, discovery or shape parsing functions. The frozen field
contract is normative input; traversal, candidate decisions and SQL comparisons
are implemented here separately. Whole database fingerprints stream.
"""
from __future__ import annotations
import hashlib,json,sqlite3,subprocess,time
from pathlib import Path
from collections import defaultdict,Counter


def enc(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def load(p):return json.loads(Path(p).read_bytes().decode('utf-8-sig'))
def at(doc,pointer):
    value=doc
    if pointer=='/':return value
    for part in pointer.lstrip('/').split('/'):
        part=part.replace('~1','/').replace('~0','~')
        value=value[int(part)] if isinstance(value,list) else value[part]
    return value

def verify(root,artifact,manifest,scope_path,shapes_path):
    start=time.perf_counter();m=load(manifest);scope=load(scope_path);contract=load(shapes_path)
    expected_pins=['26df1dfbdf05a82bbb1d97506859f3e1c40718d8','d891ef1d95fdd6c27a1da8a32ea7a61b483647a6']
    if [scope['source_commit'],scope['source_tree']]!=expected_pins:raise ValueError('wrong pinned source')
    def no_sidecars():
        if any(Path(str(artifact)+suffix).exists() for suffix in ('-wal','-shm','-journal')):raise ValueError('artifact sidecar')
    no_sidecars()
    before=(artifact.stat().st_ino,artifact.stat().st_size,artifact.stat().st_mtime_ns,sha(artifact))
    if before[1]!=m['artifact']['size_bytes'] or before[3]!=m['artifact']['sha256']:raise ValueError('artifact seal')
    if sha(scope_path)!=m['scope_manifest']['sha256'] or sha(shapes_path)!=m['shape_manifest']['sha256']:raise ValueError('manifest seal')
    if subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()!=[scope['source_commit'],scope['source_tree']]:raise ValueError('source pin')
    tree_lines=subprocess.check_output(['git','-C',str(root),'ls-tree','-r',scope['source_commit'],'--','BinOutput/Quest','BinOutput/Talk','TextMap'],text=True).splitlines()
    source_blobs={line.split('\t',1)[1]:line.split('\t',1)[0].split()[2] for line in tree_lines}
    def check(condition,why):
        if not condition:raise ValueError(why)
    def ident(kind,*parts):return kind+':'+hashlib.sha256(enc(parts).encode()).hexdigest()[:40]
    project=Path(__file__).resolve().parents[2]
    implementation_paths=['src/genshin_lore/quest_talk_physical_shapes.py','src/genshin_lore/quest_talk_physical_build.py','src/genshin_lore/db.py','schemas/quest-talk-physical-v1.sql']
    implementation_paths += [p.relative_to(project).as_posix() for p in sorted((project/'migrations').glob('000[1-7]_*.sql'))]
    fingerprint=hashlib.sha256(enc({p:sha(project/p) for p in implementation_paths}).encode()).hexdigest()
    check(m['parser_fingerprint']==fingerprint and m['parser_build_id']=='parser-build:'+fingerprint[:40],'parser identity')
    run=ident('ingest-run',m['snapshot_id'],m['parser_build_id'],sha(scope_path),m['family_contract_version'])
    check(run==m['ingest_run_id'],'run identity')
    shapes={tuple(s['root_keys']):s for s in contract['shapes']}
    dialogue=contract['dialogue_fields'];refspec=contract['reference_fields'];questspec=contract['quest_fields']
    index=defaultdict(list);unknown=[];wrappers={};file_seals={};raw_docs={};shape_counts=Counter()
    for path in sorted((root/'BinOutput/Talk').rglob('*.json')):
        rel=path.relative_to(root).as_posix();doc=load(path);spec=shapes.get(tuple(sorted(doc)))
        check(spec is not None,'unknown root signature');shape_counts[str(spec['mapping'])]+=1
        file_seals[rel]=sha(path)
        if spec['kind']=='talk_root':
            key=dialogue[spec['mapping']]['root_id'];check(type(doc[key]) is int and doc[key]>=0,'native Talk type')
            index[doc[key]].append(rel)
        elif spec['kind']=='reference_wrapper':wrappers[rel]=spec
        else:unknown.append(rel)
    check(set(file_seals)=={p for p in source_blobs if p.startswith('BinOutput/Talk/') and p.endswith('.json')},'pinned lookup file denominator')
    check(len(file_seals)==m['counts']['lookup_files'],'lookup denominator')
    c=sqlite3.connect(artifact.as_uri()+'?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
    check(m['snapshot_id']=='anime-game-data:7.0:'+expected_pins[0],'snapshot ID')
    check([tuple(r) for r in c.execute('SELECT id,source_id,release_label,build,\"commit\",tree,observed_at,parser_version,mapping_version,status,metadata_json FROM source_snapshot')]==[(m['snapshot_id'],'agd','7.0','',*expected_pins,'2000-01-01T00:00:00Z','','','active','{}')],'snapshot provenance')
    check([tuple(r) for r in c.execute('SELECT * FROM source_catalog')]==[('agd','primary','https://github.com/DimbreathBot/AnimeGameData',None)],'source catalog')
    check([tuple(r) for r in c.execute('SELECT * FROM parser_build')]==[(m['parser_build_id'],m['family_contract_version'],'physical-shapes-v1',fingerprint,sha(scope_path),'2000-01-01T00:00:00Z')],'parser provenance')
    from .db import Database
    baseline=Database(':memory:');baseline.initialize_through(7)
    expected_objects={r[0]:tuple(r) for r in baseline.conn.execute('SELECT name,type,tbl_name,sql FROM sqlite_master')}
    actual_objects={r[0]:tuple(r) for r in c.execute('SELECT name,type,tbl_name,sql FROM sqlite_master') if not r[0].startswith('quest_talk_physical_') and not r[2].startswith('quest_talk_physical_')}
    check(actual_objects==expected_objects,'base schema structure')
    baseline.close()
    check([tuple(r) for r in c.execute('SELECT * FROM schema_migration ORDER BY version')]==[(i,'2000-01-01T00:00:00Z') for i in range(1,8)],'exact base ledger')
    expected_extension=sqlite3.connect(':memory:');expected_extension.executescript((project/'schemas/quest-talk-physical-v1.sql').read_text())
    query="SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name LIKE 'quest_talk_physical_%' OR tbl_name LIKE 'quest_talk_physical_%' ORDER BY type,name"
    check([tuple(r) for r in c.execute(query)]==expected_extension.execute(query).fetchall(),'extension schema')
    check([r[0] for r in c.execute('SELECT version FROM schema_migration ORDER BY version')]==list(range(1,8)),'base ledger')
    check([tuple(r) for r in c.execute('SELECT id,parser_build_id,snapshot_id,status,scope_json FROM ingest_run')]==[(run,m['parser_build_id'],m['snapshot_id'],'complete',enc(scope))],'run provenance')
    expected_containers={};expected_refs={};selected=set()
    def add_refs(path,doc,container_pointer,field,name):
        rows=[doc] if field is None else doc[field];check(isinstance(rows,list),'ref list')
        for i,item in enumerate(rows):
            p='/' if field is None else '/'+field+'/'+str(i);native=item[refspec[name]['id']]
            check(type(native) is int and native>=0,'ref native type')
            expected_refs[(path,container_pointer,p)]=(native,item)
    for path in scope['quest_source_files']:
        doc=load(root/path);raw_docs[path]=doc
        matching=[k for k,v in questspec.items() if all(v[key] in doc for key in ('references','subquests'))]
        check(len(matching)==1,'Quest shape ambiguity');name=matching[0];spec=questspec[name]
        native=doc.get(spec['id']);check(native is None or type(native) is int and native>=0,'Quest native type')
        expected_containers[(path,'/')]=('quest_root',name,None if native is None else str(native))
        add_refs(path,doc,'/',spec['references'],name)
        for item in doc[spec['references']]:selected.update(index[item[refspec[name]['id']]])
        field=dialogue[name]['list']
        if field in doc:
            check(isinstance(doc[field],list),'embedded shape')
            if doc[field]:expected_containers[(path,'/'+field)]=('embedded_dialogue',name,None)
    for path in sorted(selected|set(wrappers)|set(unknown)):
        doc=load(root/path);raw_docs[path]=doc;spec=shapes[tuple(sorted(doc))];name=spec['mapping']
        native=str(doc[dialogue[name]['root_id']]) if spec['kind']=='talk_root' else None
        expected_containers[(path,'/')]=(spec['kind'],name,native)
        if spec['kind']=='reference_wrapper':add_refs(path,doc,'/',spec['reference_field'],name)
    rows=c.execute('SELECT q.*,sr.pointer,sr.raw_json,sf.relative_path FROM quest_talk_physical_container q JOIN source_record sr ON sr.id=q.source_record_id JOIN source_file sf ON sf.id=sr.source_file_id').fetchall()
    actual={};by_id={}
    for row in rows:
        coord=(row['relative_path'],row['pointer']);check(coord not in actual,'merged/duplicate container coordinate')
        check(row['id']==ident('qt-container',run,*coord) and row['ingest_run_id']==run,'container occurrence identity')
        check(row['source_record_id']==ident('sr',m['snapshot_id'],*coord),'container source record ID')
        metadata={'source_path':coord[0],'source_pointer':coord[1],'mapping_status':'unresolved' if row['kind']=='mapping_unresolved' else 'mapped','native_id_status':'unresolved' if row['kind']=='mapping_unresolved' else 'absent' if row['kind']=='quest_root' and row['native_id'] is None else 'not_applicable' if row['native_id'] is None else 'mapped'}
        check(row['metadata_json']==enc(metadata),'container mapping metadata')
        actual[coord]=(row['kind'],row['shape'],row['native_id']);by_id[row['id']]=coord
        check(json.loads(row['raw_json'])==at(raw_docs[coord[0]],coord[1]),'raw container body')
    check(actual==expected_containers,'container source closure mismatch')
    expected_files=set(file_seals)|set(scope['quest_source_files'])|{s['path'] for s in scope['textmap_shards']}
    files=c.execute('SELECT * FROM source_file').fetchall();check({r['relative_path'] for r in files}==expected_files,'source file denominator')
    for row in files:
        p=root/row['relative_path'];check(row['id']==ident('sf',m['snapshot_id'],row['relative_path']) and row['snapshot_id']==m['snapshot_id'],'source file identity')
        check(row['upstream_blob_oid']==source_blobs.get(row['relative_path']),'pinned source blob mismatch')
        raw=p.read_bytes();check(row['upstream_blob_oid']==hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),'source Git blob')
        check(sha(p)==row['sha256'] and p.stat().st_size==row['size_bytes'],'source file seal')
    records=c.execute('SELECT sr.*,sf.relative_path FROM source_record sr JOIN source_file sf ON sf.id=sr.source_file_id').fetchall()
    check(len(records)==len(expected_containers),'extra/missing provenance records')
    for record in records:
        coord=(record['relative_path'],record['pointer']);check(coord in expected_containers,'unknown source coordinate')
        kind,shape,native=expected_containers[coord]
        check(record['id']==ident('sr',m['snapshot_id'],*coord) and record['native_id']==native and record['record_kind']==kind,'source record identity')
        expected_raw=(root/coord[0]).read_bytes().decode('utf-8') if coord[1]=='/' else enc(at(raw_docs[coord[0]],coord[1]))
        check(record['raw_json']==expected_raw and record['raw_sha256']==hashlib.sha256(expected_raw.encode()).hexdigest(),'source record lexical seal')
    for item in scope['textmap_shards']:check(sha(root/item['path'])==item['sha256'],'TextMap pinned seal')
    refs=c.execute('SELECT * FROM quest_talk_physical_reference').fetchall();seen=set()
    for row in refs:
        path,ptr=by_id[row['container_id']];coord=(path,ptr,row['pointer']);check(coord not in seen,'reference merge');seen.add(coord)
        check(row['id']==ident('qt-ref',run,path,row['pointer']) and row['ingest_run_id']==run,'reference identity')
        native,raw=expected_refs[coord];check(json.loads(row['raw_json'])==raw and str(native)==row['native_id'],'reference raw coordinate')
        candidates=json.loads(row['candidates_json']);check([v['path'] for v in candidates]==index[native],'independent candidate set mismatch')
        expected_candidates=[{'path':p,'pointer':'/','sha256':file_seals[p],
                              'container_id':ident('qt-container',run,p,'/') if (p,'/') in expected_containers else None}
                             for p in index[native]]
        check(candidates==expected_candidates,'candidate container binding and source identity')
        check(bool(row['search_complete'])==(not unknown),'incomplete search reported complete')
    check(seen==set(expected_refs),'reference occurrence denominator')
    expected_nodes={}
    for (path,ptr),(kind,name,_) in expected_containers.items():
        if kind not in ('talk_root','embedded_dialogue'):continue
        spec=dialogue[name];doc=raw_docs[path];values=at(doc,ptr) if kind=='embedded_dialogue' else doc.get(spec['list'],[])
        check(isinstance(values,list),'physical node list')
        for i,n in enumerate(values):
            pointer='/'+spec['list']+'/'+str(i)
            expected_nodes[(path,pointer)]=(ptr,i,n,spec)
    actual_nodes=c.execute('SELECT * FROM quest_talk_physical_node').fetchall();seen=set();node_by_id={};native_all=defaultdict(list);local=defaultdict(lambda:defaultdict(list))
    for row in actual_nodes:
        path,ptr=by_id[row['container_id']];coord=(path,row['pointer']);check(coord not in seen,'node coordinate merge');seen.add(coord)
        check(row['id']==ident('qt-node',run,*coord) and row['ingest_run_id']==run,'node identity')
        container,i,raw,spec=expected_nodes[coord]
        check(container==ptr and i==row['ordinal'] and json.loads(row['raw_json'])==raw,'node provenance/body')
        check(row['native_id']==str(raw[spec['node_id']]),'node mapped ID')
        key=raw.get(spec['text_hash']);check(row['text_hash']==(str(key) if key is not None else None),'text locator')
        node_by_id[row['id']]=row;native_all[row['native_id']].append(row);local[row['container_id']][row['native_id']].append(row['id'])
    check(seen==set(expected_nodes),'passage denominator')
    graph=Counter()
    for row in actual_nodes:
        path,ptr=by_id[row['container_id']];_,_,raw,spec=expected_nodes[(path,row['pointer'])];targets=raw.get(spec['next'],[]);observations=json.loads(row['successors_json'])
        check(len(observations)==len(targets),'raw graph pointer denominator')
        for i,(target,obs) in enumerate(zip(targets,observations)):
            loc=local[row['container_id']][str(target)];others=native_all[str(target)]
            status='local' if len(loc)==1 else 'ambiguous_local' if loc else 'external' if len(others)==1 else 'ambiguous_external' if others else 'dangling'
            graph[status]+=1
            check(obs['raw_target_id']==target and obs['ordinal']==i and obs['status']==status and obs['local_candidates']==loc,'graph reconstruction')
            expected_candidates=[{'container_id':v['container_id'],'node_id':v['id'],'path':by_id[v['container_id']][0],'pointer':v['pointer']} for v in others] if not loc else []
            check(obs['physical_candidates']==expected_candidates and obs['scope']=='selected_run_containers','external physical coordinates and scope')
    maps={l:[(tier,p,load(root/p)) for tier,p in [('main',f'TextMap/TextMap{l}.json'),('medium',f'TextMap/TextMap_Medium{l}.json')]] for l in ('CHS','EN','JP')}
    seen=set();text_counts=Counter()
    for row in c.execute('SELECT * FROM quest_talk_physical_text'):
        node=node_by_id[row['node_id']];check(row['container_id']==node['container_id'],'cross-container text')
        coord=(row['node_id'],row['locale']);check(coord not in seen,'duplicate/merged locale');seen.add(coord)
        path,_=by_id[row['container_id']]
        check(row['id']==ident('qt-text',run,path,node['pointer'],row['locale']) and row['ingest_run_id']==run,'text identity')
        key=node['text_hash'];hits=[(tier,p,d[key]) for tier,p,d in maps[row['locale']] if key in d]
        status='missing' if not hits else 'ambiguous' if len({v for _,_,v in hits})>1 else 'resolved_'+hits[0][0]
        _,_,_,node_spec=expected_nodes[(path,node['pointer'])]
        if node_spec.get('text_mapping')=='unresolved_codec':status='mapping_unresolved'
        check(row['status']==status and row['raw_text']==(hits[0][2] if status.startswith('resolved') else None),'independent text resolution')
        check(json.loads(row['locator_json'])==[{'path':p,'pointer':'/'+key,'tier':tier,'raw_text':v} for tier,p,v in hits],'physical TextMap locators')
        text_counts[status]+=1
    check(len(seen)==len(actual_nodes)*3,'locale denominator')
    check(not c.execute('PRAGMA foreign_key_check').fetchall(),'FK failure');c.close()
    no_sidecars()
    after=(artifact.stat().st_ino,artifact.stat().st_size,artifact.stat().st_mtime_ns,sha(artifact));check(before==after,'artifact changed during verification')
    return {'status':'pass','source_containers':len(rows),'source_nodes':len(actual_nodes),'source_references':len(refs),'locale_slots':len(seen),'text_status':dict(text_counts),'graph':dict(graph),'lookup_files':len(file_seals),'unresolved_lookup_paths':unknown,'candidate_search_complete':not unknown,'wall_seconds':time.perf_counter()-start,'artifact_sha256':before[3]}
