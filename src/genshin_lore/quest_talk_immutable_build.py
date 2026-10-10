"""Immutable v6 physical delivery for explicit Quest-linked and mapped-complement scopes."""
from __future__ import annotations
from collections import defaultdict, Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import time

from .db import Database
from .quest_talk_physical_shapes import (SNAPSHOT, QUESTS, DIALOGUES, canonical,
                                         digest, integer, read, classify, nodes, quest, references)

CONTRACT='quest-talk-evidence-v6'
ROOT = Path(__file__).resolve().parents[2]
TABLES = ('quest_talk_physical_container','quest_talk_physical_node','quest_talk_physical_text','quest_talk_physical_reference')


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def identity(kind, *parts):
    return kind + ':' + digest(parts)[:40]


def implementation():
    paths=['src/genshin_lore/quest_talk_physical_shapes.py','src/genshin_lore/quest_talk_physical_build.py','src/genshin_lore/quest_talk_immutable_build.py','src/genshin_lore/db.py','schemas/quest-talk-immutable-v1.sql']
    paths += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT/'migrations').glob('000[1-7]_*.sql'))]
    return digest({p:sha(ROOT/p) for p in paths})


def prepare(root, quest_paths, shape_path):
    """Classify the frozen inventory; this is build input validation, not a new census."""
    shapes=read(shape_path)['shapes'];lookup=[];index=defaultdict(list);wrappers=[];unknown=[]
    source_paths=sorted((root/'BinOutput/Talk').rglob('*.json'))
    for path in source_paths:
        doc=read(path);spec=classify(doc,shapes);rel=path.relative_to(root).as_posix()
        native=None
        if spec['kind']=='talk_root':
            native=integer(doc[DIALOGUES[spec['mapping']][0]],'Talk ID')
            nodes(doc,spec['mapping'])
            index[native].append(rel)
        elif spec['kind']=='reference_wrapper':
            references(doc,spec['reference_field'],spec['mapping']);wrappers.append(rel)
        else:unknown.append(rel)
        lookup.append({'path':rel,'sha256':sha(path),'size_bytes':path.stat().st_size,
                       'kind':spec['kind'],'mapping':spec['mapping'],'native_id':native})
    selected=set();quests=[]
    for path in quest_paths:
        doc=read(root/path);shape,native,refs=quest(doc)
        selected.update(p for ref in refs for p in index[ref['native_id']])
        quests.append({'path':path,'sha256':sha(root/path),'size_bytes':(root/path).stat().st_size,
                       'mapping':shape,'native_id':native,'reference_count':len(refs)})
    return {'quests':quests,'lookup':lookup,'selected_talk_paths':sorted(selected),
            'wrapper_paths':wrappers,'unresolved_lookup_paths':unknown}


def build(root, scope_path, target):
    started=time.perf_counter();scope=read(scope_path)
    if scope['contract']!='quest-talk-immutable-scope-v1' or scope['family_contract_version']!=CONTRACT:
        raise ValueError('unsupported scope')
    if [scope['source_commit'],scope['source_tree']] != ['26df1dfbdf05a82bbb1d97506859f3e1c40718d8','d891ef1d95fdd6c27a1da8a32ea7a61b483647a6']:raise ValueError('scope must use fixed 7.0 pins')
    pins=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()
    if pins != [scope['source_commit'],scope['source_tree']]:raise ValueError('source pin mismatch')
    tree_lines=subprocess.check_output(['git','-C',str(root),'ls-tree','-r',scope['source_commit'],'--','BinOutput/Quest','BinOutput/Talk','TextMap'],text=True).splitlines()
    source_blobs={line.split('\t',1)[1]:line.split('\t',1)[0].split()[2] for line in tree_lines}
    for item in scope['textmap_shards']:
        if sha(root/item['path'])!=item['sha256']:raise ValueError('TextMap seal mismatch')
    shape_path=ROOT/scope['shape_manifest']['path']
    if sha(shape_path)!=scope['shape_manifest']['sha256']:raise ValueError('shape contract seal mismatch')
    if target.exists():raise FileExistsError(target)
    # Input contains all lookup exclusions and known candidates, independently verified later.
    inventory=prepare(root,scope['quest_source_files'],shape_path)
    if digest(inventory)!=scope['source_inventory_digest']:raise ValueError('source inventory changed')
    # A sealed prior Quest scope fixes the complement classification boundary.
    boundary=read(ROOT/scope['quest_boundary']['path'])
    if sha(ROOT/scope['quest_boundary']['path'])!=scope['quest_boundary']['sha256']:raise ValueError('Quest boundary seal')
    if scope['quest_source_files']!=boundary['quest_source_files']:raise ValueError('Quest boundary mismatch')
    mapped={e['path'] for e in inventory['lookup'] if e['kind']=='talk_root'}
    complement=sorted(mapped-set(inventory['selected_talk_paths']))
    if scope['scope_kind']=='mapped_complement':
        if scope['program_source_files']!=complement:raise ValueError('physical complement mismatch')
        inventory['quests']=[];inventory['selected_talk_paths']=complement
    elif scope['scope_kind']!='quest_linked' or scope['program_source_files']!=inventory['selected_talk_paths']:raise ValueError('physical linked scope mismatch')
    terminal=ROOT/scope['terminal_manifest']['path']
    if sha(terminal)!=scope['terminal_manifest']['sha256']:raise ValueError('terminal contract seal')
    implementation_fingerprint=implementation()
    parser='parser-build:'+implementation_fingerprint[:40]
    run=identity('ingest-run',SNAPSHOT,parser,sha(scope_path),CONTRACT)
    target.parent.mkdir(parents=True,exist_ok=True)
    db=Database(target);db.initialize_through(7);c=db.conn
    c.execute("UPDATE schema_migration SET applied_at='2000-01-01T00:00:00Z'");c.commit()
    c.executescript((ROOT/'schemas/quest-talk-immutable-v1.sql').read_text())
    c.execute("INSERT INTO source_catalog VALUES('agd','primary','https://github.com/DimbreathBot/AnimeGameData',NULL)")
    c.execute('INSERT INTO source_snapshot VALUES(?,?,?,?,?,?,?,?,?,?,?)',
              (SNAPSHOT,'agd','7.0','',scope['source_commit'],scope['source_tree'],'2000-01-01T00:00:00Z','','','active','{}'))
    c.execute('INSERT INTO parser_build VALUES(?,?,?,?,?,?)',(parser,CONTRACT,'physical-immutable-v1',implementation_fingerprint,sha(scope_path),'2000-01-01T00:00:00Z'))
    c.execute('INSERT INTO ingest_run VALUES(?,?,?,?,?,?,?)',(run,SNAPSHOT,parser,canonical(scope),'{}','running','2000-01-01T00:00:00Z'))
    source_ids={};container_ids={};node_entries=[];pending_refs=[];shapes=read(shape_path)['shapes']
    index=defaultdict(list)
    for entry in inventory['lookup']:
        if entry['kind']=='talk_root':index[entry['native_id']].append(entry['path'])
    def source(path):
        if path in source_ids:return source_ids[path]
        raw=(root/path).read_bytes();sid=identity('sf',SNAPSHOT,path)
        blob=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        if source_blobs.get(path)!=blob:raise ValueError('source bytes differ from pinned Git tree: '+path)
        c.execute('INSERT INTO source_file VALUES(?,?,?,?,?,?,?,?)',(sid,SNAPSHOT,path,blob,hashlib.sha256(raw).hexdigest(),len(raw),'json',''))
        source_ids[path]=sid;return sid
    def container(path,pointer,kind,shape,native,raw_value):
        sid=source(path);rid=identity('sr',SNAPSHOT,path,pointer);raw=(root/path).read_bytes().decode('utf-8') if pointer=='/' else canonical(raw_value)
        c.execute('INSERT INTO source_record VALUES(?,?,?,?,?,?,?)',(rid,sid,pointer,kind,None if native is None else str(native),hashlib.sha256(raw.encode()).hexdigest(),raw))
        cid=identity('qt-container',run,path,pointer)
        c.execute('INSERT INTO quest_talk_physical_container VALUES(?,?,?,?,?,?,?)',(cid,run,rid,kind,shape,None if native is None else str(native),canonical({'source_path':path,'source_pointer':pointer,'mapping_status':'mapping_unresolved_terminal' if kind=='mapping_unresolved' else 'mapped','native_id_status':'unresolved' if kind=='mapping_unresolved' else 'absent' if kind=='quest_root' and native is None else 'not_applicable' if native is None else 'mapped'})))
        container_ids[(path,pointer)]=cid;return cid
    for q in inventory['quests']:
        path=q['path'];doc=read(root/path);shape,native,refs=quest(doc)
        cid=container(path,'/','quest_root',shape,native,doc)
        pending_refs.extend((cid,path,r) for r in refs)
        # Embedded list is its own physical container, with no synthetic Talk ID.
        embedded='GALIDJOEHOC' if shape=='alt' else 'PFALHAKIILD'
        if embedded in doc:
            value=doc[embedded]
            if not isinstance(value,list):raise ValueError('embedded dialogue is not list')
            if value:
                ecid=container(path,'/'+embedded,'embedded_dialogue',shape,None,value)
                node_entries.extend((ecid,path,n) for n in nodes(value,shape,embedded=True))
    for path in sorted(set(inventory['selected_talk_paths'])|(set(inventory['wrapper_paths'])|set(inventory['unresolved_lookup_paths']) if scope['scope_kind']=='quest_linked' else set())):
        doc=read(root/path);spec=classify(doc,shapes);kind=spec['kind'];shape=spec['mapping']
        native=doc[DIALOGUES[shape][0]] if kind=='talk_root' else None
        cid=container(path,'/',kind,shape,native,doc)
        if kind=='talk_root':node_entries.extend((cid,path,n) for n in nodes(doc,shape))
        elif kind=='reference_wrapper':pending_refs.extend((cid,path,r) for r in references(doc,spec['reference_field'],shape))
    # Source file metadata for the complete lookup denominator remains separate
    # from the selected physical container denominator.
    for entry in inventory['lookup']:source(entry['path'])
    # Resolve physical locale occurrences, main then Medium; conflict is explicit.
    maps={}
    for locale in ('CHS','EN','JP'):
        maps[locale]=[]
        for tier,path in [('main',f'TextMap/TextMap{locale}.json'),('medium',f'TextMap/TextMap_Medium{locale}.json')]:
            source(path);maps[locale].append((tier,path,read(root/path)))
    by_native=defaultdict(list);local=defaultdict(lambda:defaultdict(list))
    for cid,path,n in node_entries:
        nid=identity('qt-node',run,path,n['pointer']);n['id']=nid
        by_native[n['native_id']].append({'container_id':cid,'node_id':nid,'path':path,'pointer':n['pointer']})
        local[cid][n['native_id']].append(nid)
    graph=Counter();text_counts=Counter()
    for cid,path,n in node_entries:
        observations=[]
        for ordinal,target_id in enumerate(n['next']):
            loc=local[cid].get(target_id,[]);other=by_native.get(target_id,[])
            status='local' if len(loc)==1 else 'ambiguous_local' if loc else 'external' if len(other)==1 else 'ambiguous_external' if other else 'dangling'
            graph[status]+=1
            observations.append({'ordinal':ordinal,'raw_target_id':target_id,'status':status,'local_candidates':loc,'physical_candidates':other if not loc else [],'scope':'selected_run_containers'})
        c.execute('INSERT INTO quest_talk_physical_node VALUES(?,?,?,?,?,?,?,?,?)',(n['id'],cid,run,n['ordinal'],n['pointer'],str(n['native_id']),canonical(n['raw']),canonical(observations),None if n['text_hash'] is None else str(n['text_hash'])))
        for locale,tiers in maps.items():
            key=str(n['text_hash']);hits=[(tier,p,d[key]) for tier,p,d in tiers if key in d]
            if any(not isinstance(t,str) for _,_,t in hits):raise ValueError('non-string TextMap')
            status='missing' if not hits else 'ambiguous' if len({t for _,_,t in hits})>1 else 'resolved_'+hits[0][0]
            if n['text_mapping']=='unresolved_codec':status='mapping_unresolved_terminal'
            text=hits[0][2] if status.startswith('resolved') else None;text_counts[status]+=1
            locators=[{'path':p,'pointer':'/'+key,'tier':tier,'raw_text':t} for tier,p,t in hits]
            c.execute('INSERT INTO quest_talk_physical_text VALUES(?,?,?,?,?,?,?,?)',(identity('qt-text',run,path,n['pointer'],locale),n['id'],cid,run,locale,status,text,canonical(locators)))
    seals={x['path']:x['sha256'] for x in inventory['lookup']}
    complete=not inventory['unresolved_lookup_paths']
    for cid,path,ref in pending_refs:
        candidates=[{'path':p,'pointer':'/','container_id':container_ids.get((p,'/')),'sha256':seals[p]} for p in index[ref['native_id']]]
        c.execute('INSERT INTO quest_talk_physical_reference VALUES(?,?,?,?,?,?,?,?)',(identity('qt-ref',run,path,ref['pointer']),cid,run,ref['pointer'],str(ref['native_id']),canonical(ref['raw']),canonical(candidates),int(complete)))
    c.execute("UPDATE ingest_run SET status='complete' WHERE id=?",(run,));c.commit()
    if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('foreign key failure')
    counts={t:c.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in TABLES}
    counts.update(quest_files=len(inventory['quests']),selected_talk_files=len(inventory['selected_talk_paths']),lookup_files=len(inventory['lookup']))
    structure=[tuple(r) for r in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name LIKE 'quest_talk_physical_%' OR tbl_name LIKE 'quest_talk_physical_%' ORDER BY type,name")]
    # VACUUM INTO avoids a simultaneous full rollback journal plus vacuum copy.
    vacuum=Path(str(target)+'.vacuum')
    if vacuum.exists():raise FileExistsError(vacuum)
    c.execute('VACUUM INTO ?', (str(vacuum),));db.close()
    import os
    os.replace(vacuum,target)
    if target.stat().st_size>scope['artifact_budget_bytes']:raise ValueError('artifact budget exceeded')
    if implementation()!=implementation_fingerprint:raise ValueError('implementation changed during build')
    return {'contract':'quest-talk-immutable-runtime-v1','family':'quest_talk_pilot','family_contract_version':CONTRACT,
            'snapshot_id':SNAPSHOT,'parser_fingerprint':implementation_fingerprint,'schema_extension_structure':structure,'parser_build_id':parser,'ingest_run_id':run,'counts':counts,'graph':dict(graph),'texts':dict(text_counts),
            'artifact':{'size_bytes':target.stat().st_size,'sha256':sha(target)},
            'scope_manifest':{'path':scope_path.name,'sha256':sha(scope_path)},
            'shape_manifest':{'path':shape_path.name,'sha256':sha(shape_path)},
            'schema_extension_sha256':sha(ROOT/'schemas/quest-talk-immutable-v1.sql'),
            'coverage':'declared_'+scope['scope_kind']+'_evidence_processing_complete','corpus_complete':False,'product_accepted':False,'scope_kind':scope['scope_kind'],'terminal_manifest':{'path':'../quest-talk-completeness/terminal-evidence.json','sha256':sha(terminal)},'completeness':{'physical_processing_complete':True,'selected_programs_processed':len(inventory['selected_talk_paths']),'quest_files_processed':len(inventory['quests']),'expected_processed_slots':sum(text_counts.values()),'text_state_counts':dict(text_counts),'candidate_lookup_physical_scan_complete':True,'semantic_candidate_search_complete':False,'mapping_resolution':'partial_with_terminal_unresolved','full_talk':False,'full_7_0':False},
            'candidate_search_complete':complete,'unresolved_lookup_paths':inventory['unresolved_lookup_paths'],
            'build_seconds':time.perf_counter()-started}
