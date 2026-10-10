"""Independent scalable source closure check before the SQL evidence audit.

Reconstruct candidate sets from pinned raw Talk files, not discovery resolution
or builder output. Match stored source records to the independently read bytes.
"""
from __future__ import annotations
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import unicodedata
from .util import canonical_json

def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def _decoded(value):
    names={"OIFGMOHKPOI":"id","IOKNFDJFGDH":"talkId","PFALHAKIILD":"dialogList","KMLAFCBMFEI":"nextDialogs","LFGCLNLPAPB":"talkRole","OACNIBLFFDI":"talkContentTextMapHash","ACCOJJPKFCN":"talkRoleNameTextMapHash","MCIAGDCECPL":"initDialog"}
    if isinstance(value,dict): return {names.get(k,k):_decoded(v) for k,v in value.items()}
    if isinstance(value,list): return [_decoded(v) for v in value]
    return value


def _field(value, physical_key, normalized_key, default=None):
    """Read either observed Talk root shape without normalizing identity."""
    if not isinstance(value, dict):
        return default
    if physical_key in value:
        return value[physical_key]
    return value.get(normalized_key, default)


def verify_source_closure(conn: sqlite3.Connection, expected, root: Path) -> dict:
    scope=expected.manifest_set.corpus_manifest.scalable_scope
    if scope['snapshot_id'] != expected.snapshot_id:
        raise ValueError('scope snapshot identity mismatch')
    if scope['quest_source_files'] != list(expected.quest_paths) or scope['textmap_shards'] != list(expected.textmap_paths):
        raise ValueError('scope selected physical files mismatch')
    if scope['expected_physical_source_denominator'] != len(expected.selected_paths):
        raise ValueError('scope physical source denominator mismatch')
    pins=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()
    if pins != [scope['source_commit'],scope['source_tree']]:raise ValueError('source commit/tree mismatch')
    grouped=defaultdict(list)
    paths=sorted((root/'BinOutput/Talk').rglob('*.json'))
    if len(paths)!=scope['candidate_lookup_denominator']:raise ValueError('candidate lookup denominator mismatch')
    for path in paths:
        doc=json.loads(path.read_bytes().decode('utf-8-sig'))
        talk_id = _field(doc, 'IOKNFDJFGDH', 'talkId')
        if talk_id is not None:
            grouped[str(talk_id)].append(path.relative_to(root).as_posix())
    references=0
    for bundle in expected.manifest_set.quest_candidate_bundles:
        raw=(root/bundle.quest.relative_path).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=bundle.quest.raw_sha256:raise ValueError('Quest source seal mismatch')
        doc=json.loads(raw.decode('utf-8-sig'))
        refs=doc.get('OJACLOOEAMG',[]) or []
        if len(refs)!=len(bundle.references):raise ValueError('Quest reference denominator mismatch')
        for i,ref in enumerate(refs):
            frozen=bundle.references[i]
            if str(ref['OIFGMOHKPOI'])!=str(frozen.talk_id) or frozen.pointer!=f'/OJACLOOEAMG/{i}':raise ValueError('Quest reference coordinate mismatch')
            if sorted(c.relative_path for c in frozen.candidates)!=grouped[str(ref['OIFGMOHKPOI'])]:raise ValueError('Quest candidate set mismatch')
            references+=1
    source_rows=conn.execute('SELECT * FROM source_file WHERE snapshot_id=?',(expected.snapshot_id,)).fetchall()
    if {r['relative_path'] for r in source_rows} != set(expected.selected_paths):raise ValueError('source file denominator mismatch')
    records=0
    parsed_sources={}
    for sf in source_rows:
        raw=(root/sf['relative_path']).read_bytes()
        if len(raw)!=sf['size_bytes'] or hashlib.sha256(raw).hexdigest()!=sf['sha256']:raise ValueError('source file bytes mismatch')
        doc=json.loads(raw.decode('utf-8-sig'))
        parsed_sources[sf['relative_path']]=doc
        for sr in conn.execute('SELECT * FROM source_record WHERE source_file_id=?',(sf['id'],)):
            pointer=sr['pointer']
            if pointer=='/':value=doc
            elif sr['record_kind']=='textmap_entry':
                key=pointer.removeprefix('/entries/');value={'hash':key,'value':doc[key]}
            else:
                value=doc
                for part in pointer.lstrip('/').split('/'):
                    part=part.replace('~1','/').replace('~0','~')
                    value=value[int(part)] if isinstance(value,list) else value[part]
            if json.loads(sr['raw_json'])!=value:raise ValueError('source record physical coordinate mismatch')
            expected_raw_json=raw.decode('utf-8') if sr['record_kind']=='talk_root' else canonical_json(value)
            expected_raw_sha=_digest(expected_raw_json)
            if sr['raw_json'] != expected_raw_json or sr['raw_sha256'] != expected_raw_sha:
                raise ValueError('source record raw body/checksum mismatch')
            if sr['record_kind']=='talk_root' and expected_raw_sha != sf['sha256']:
                raise ValueError('Talk root checksum differs from physical UTF-8 file')
            records+=1
    node_paths=defaultdict(list)
    expected_passages={}
    for path in expected.talk_paths:
        for ordinal,node in enumerate(_field(parsed_sources[path], 'PFALHAKIILD', 'dialogList', [])):
            node_id = _field(node, 'OIFGMOHKPOI', 'id')
            node_paths[str(node_id)].append(path)
            expected_passages[(path,f'/dialogList/{ordinal}')]=(str(node_id),_digest(canonical_json(node)),_digest(canonical_json(_decoded(node))),'{}')
    actual_passages={}
    for row in conn.execute("SELECT sf.relative_path,po.source_pointer,po.node_id,po.raw_checksum,po.normalized_checksum,po.metadata_json FROM passage_occurrence po JOIN evidence_container ec ON ec.id=po.evidence_container_id JOIN source_record sr ON sr.id=ec.source_record_id JOIN source_file sf ON sf.id=sr.source_file_id WHERE po.ingest_run_id=?",(expected.ingest_run_id,)):
        key=(row['relative_path'],row['source_pointer'])
        if key in actual_passages:raise ValueError('duplicate passage coordinate')
        actual_passages[key]=(str(row['node_id']),row['raw_checksum'],row['normalized_checksum'],row['metadata_json'])
    if actual_passages!=expected_passages:raise ValueError('independent passage source coordinates mismatch')
    external=[];dangling=[];local_edges=set();raw_pointers=0
    for path in sorted(expected.talk_paths):
        ds=_field(parsed_sources[path], 'PFALHAKIILD', 'dialogList', [])
        local={str(_field(n, 'OIFGMOHKPOI', 'id')) for n in ds}
        for node in ds:
            for target in _field(node, 'KMLAFCBMFEI', 'nextDialogs', []):
                raw_pointers+=1;target=str(target);source=str(_field(node, 'OIFGMOHKPOI', 'id'))
                if target in local:
                    local_edges.add((path,source,target));continue
                targets=sorted(node_paths.get(target,[]))
                item=dict(source_talk_path=path,source_node_id=source,target_node_id=target,target_candidate_paths=targets,classification='external_target' if len(targets)==1 else 'ambiguous_target' if targets else 'dangling_target')
                (external if targets else dangling).append(item)
    graph=dict(raw_next_pointer_count=raw_pointers,intra_container_edge_count=len(local_edges),external_target_pointer_count=len(external),ambiguous_target_pointer_count=sum(x['classification']=='ambiguous_target' for x in external),dangling_target_pointer_count=len(dangling),external_targets=external,dangling_targets=dangling)
    actual_edges=set()
    for row in conn.execute("SELECT sf.relative_path,a.node_id,b.node_id,a.evidence_container_id,b.evidence_container_id FROM occurrence_edge e JOIN passage_occurrence a ON a.passage_occurrence_id=e.from_passage_occurrence_id JOIN passage_occurrence b ON b.passage_occurrence_id=e.to_passage_occurrence_id JOIN evidence_container ec ON ec.id=e.evidence_container_id JOIN source_record sr ON sr.id=ec.source_record_id JOIN source_file sf ON sf.id=sr.source_file_id WHERE e.ingest_run_id=?",(expected.ingest_run_id,)):
        if row[3]!=row[4]:raise ValueError('cross-container edge')
        actual_edges.add(tuple(row[:3]))
    if actual_edges!=local_edges:raise ValueError('independent local edge reconstruction mismatch')
    expected_texts={}
    missing=0
    for path in expected.talk_paths:
        for ordinal,node in enumerate(_field(parsed_sources[path], 'PFALHAKIILD', 'dialogList', [])):
            key=str(_field(node, 'OACNIBLFFDI', 'talkContentTextMapHash', 0))
            for lang in ('CHS','EN','JP'):
                candidates=[]
                for tm in (f'TextMap/TextMap{lang}.json',f'TextMap/TextMap_Medium{lang}.json'):
                    if key != '0' and key in parsed_sources[tm]: candidates.append((tm,parsed_sources[tm][key]))
                if not candidates:
                    missing+=1
                    continue
                if len(candidates)>1:
                    raise ValueError('scalable TextMap overlap requires explicit physical candidate contract')
                tm,text=candidates[0]
                normalized=unicodedata.normalize('NFC',text).replace('\r\n','\n').replace('\r','\n')
                expected_texts[(path,f'/dialogList/{ordinal}',lang)] = (text,tm,f'/entries/{key}',normalized,_digest(text),_digest(normalized),None,None)
    actual={}
    for row in conn.execute("SELECT sf.relative_path,po.source_pointer,pot.language,pot.raw_text,pot.normalized_text,pot.raw_checksum,pot.normalized_checksum,pot.source_start,pot.source_end,tf.relative_path AS text_path,ts.pointer AS text_pointer FROM passage_occurrence_text pot JOIN passage_occurrence po ON po.passage_occurrence_id=pot.passage_occurrence_id JOIN evidence_container ec ON ec.id=po.evidence_container_id JOIN source_record sr ON sr.id=ec.source_record_id JOIN source_file sf ON sf.id=sr.source_file_id JOIN source_record ts ON ts.id=pot.source_record_id JOIN source_file tf ON tf.id=ts.source_file_id WHERE pot.ingest_run_id=?",(expected.ingest_run_id,)):
        key=(row['relative_path'],row['source_pointer'],row['language'])
        if key in actual:raise ValueError('duplicate text occurrence coordinates')
        actual[key]=(row['raw_text'],row['text_path'],row['text_pointer'],row['normalized_text'],row['raw_checksum'],row['normalized_checksum'],row['source_start'],row['source_end'])
    if actual!=expected_texts:raise ValueError('independent physical text resolution mismatch')
    return dict(status='pass',graph_projection=graph,physical_passages=len(actual_passages),physical_texts=len(actual),missing_locale_slots=missing,quest_files=len(expected.quest_paths),references=references,lookup_files=len(paths),selected_files=len(source_rows),source_records=records,basis='pinned source bytes + frozen manifest; no builder business reconstruction')
