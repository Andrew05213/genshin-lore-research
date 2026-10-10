"""Independent physical-row oracle for the non-product R3 uplift artifact."""
from __future__ import annotations
import hashlib,json,sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any,Iterator
from .batch_m3_r1_projection import FAMILY_BY_NAME,verify_source_pin
from .batch_r3_uplift import (R3_DEFINITIONS,R3_FAMILY_ORDER,TEXTMAP_CORPUS_IDENTITY,TEXTMAP_SHARD_SHA256,TEXTMAP_SHARD_SIZE,qualify_r3_artifact,sha256_file)

class R3VerificationError(RuntimeError): pass
class _Pairs(list[tuple[str,Any]]): pass
def _sha(raw:bytes)->str:return hashlib.sha256(raw).hexdigest()
def _id(*parts:str)->str:return _sha("\0".join(parts).encode('utf-8','surrogateescape'))
def _skip(text:str,pos:int)->int:
    while pos<len(text) and text[pos] in ' \t\r\n':pos+=1
    return pos
def _records(path:Path)->Iterator[tuple[int,str,str,list[tuple[str,Any]],str]]:
    text=path.read_bytes().decode('utf-8-sig');decoder=json.JSONDecoder(object_pairs_hook=_Pairs);pos=_skip(text,0)
    if pos>=len(text) or text[pos]!='[':raise R3VerificationError(f'not an array: {path}')
    pos+=1;ordinal=0
    while True:
        pos=_skip(text,pos)
        if pos<len(text) and text[pos]==']':
            if _skip(text,pos+1)!=len(text):raise R3VerificationError(f'trailing source bytes: {path}')
            return
        start=pos
        try:value,end=decoder.raw_decode(text,pos)
        except json.JSONDecodeError as exc:raise R3VerificationError(f'invalid source row: {path}') from exc
        if not isinstance(value,_Pairs):raise R3VerificationError(f'row is not object: {path}/{ordinal}')
        token=text[start:end];pointer=f'/{ordinal}'
        yield ordinal,pointer,token,list(value),_sha(token.encode('utf-8'))
        ordinal+=1;pos=_skip(text,end)
        if pos>=len(text):raise R3VerificationError(f'unterminated array: {path}')
        if text[pos]==',':pos+=1;continue
        if text[pos]==']':
            if _skip(text,pos+1)!=len(text):raise R3VerificationError(f'trailing source bytes: {path}')
            return
        raise R3VerificationError(f'invalid array delimiter: {path}')

def verify_r3_independently(artifact:str|Path,manifest_path:str|Path,source_root:str|Path,textmap_shard:str|Path)->dict[str,Any]:
    root=Path(source_root).resolve();textmap=Path(textmap_shard).resolve();verify_source_pin(root)
    if not textmap.is_file() or textmap.stat().st_size!=TEXTMAP_SHARD_SIZE or sha256_file(textmap)!=TEXTMAP_SHARD_SHA256:raise R3VerificationError('independent TextMap seal mismatch')
    manifest=qualify_r3_artifact(artifact,manifest_path)
    if manifest.get('textmap_corpus')!={**TEXTMAP_CORPUS_IDENTITY,'artifact_size_bytes':TEXTMAP_SHARD_SIZE,'artifact_sha256':TEXTMAP_SHARD_SHA256}:raise R3VerificationError('tracked TextMap corpus identity mismatch')
    source_rows:dict[str,list[tuple[int,str,str,list[tuple[str,Any]],str]]]={};required:set[str]=set()
    for family in R3_FAMILY_ORDER:
        rows=list(_records(root/FAMILY_BY_NAME[family].source_file));source_rows[family]=rows
        for _,_,_,pairs,_ in rows:
            required.update(str(value) for key,value in pairs if key in R3_DEFINITIONS[family])
    candidates:dict[str,list[tuple[Any,...]]]=defaultdict(list)
    tm=sqlite3.connect(f'file:{textmap.as_posix()}?mode=ro&immutable=1',uri=True)
    try:
        if any(dict(tm.execute('SELECT key,value FROM shard_metadata')).get(k)!=v for k,v in TEXTMAP_CORPUS_IDENTITY.items()):raise R3VerificationError('independent TextMap metadata mismatch')
        sql=('SELECT key_text,locale,shard_tier,relative_path,source_record_pointer,pair_ordinal,pointer,raw_key_json,raw_text_json,raw_text,raw_key_sha256,raw_text_sha256,raw_record_sha256 FROM textmap_pair_occurrence ORDER BY relative_path,pair_ordinal')
        for row in tm.execute(sql):
            if str(row[0]) in required:candidates[str(row[0])].append(tuple(row[1:]))
    finally:tm.close()
    db=sqlite3.connect(f'file:{Path(artifact).resolve().as_posix()}?mode=ro&immutable=1',uri=True);results=[]
    corpus=(TEXTMAP_CORPUS_IDENTITY['snapshot_id'],TEXTMAP_CORPUS_IDENTITY['parser_build_id'],TEXTMAP_CORPUS_IDENTITY['ingest_run_id'])
    try:
        for family,item in zip(R3_FAMILY_ORDER,manifest['families'],strict=True):
            if item['family']!=family:raise R3VerificationError('family order mismatch')
            run=str(item['ingest_run_id']);parser=str(item['parser_build_id']);source_file=FAMILY_BY_NAME[family].source_file
            expected_rows=[];expected_owners=[];expected_resolutions=[];expected_candidates=[]
            missing=ambiguous=0
            for ordinal,pointer,token,pairs,token_sha in source_rows[family]:
                row_id=_id(family,run,source_file,pointer,token_sha)
                expected_rows.append((row_id,run,parser,source_file,ordinal,pointer,token,token_sha))
                for pair_ordinal,(key,value) in enumerate(pairs):
                    if key not in R3_DEFINITIONS[family]:continue
                    raw_hash=str(value);owner_id=_id(family,run,row_id,key,str(pair_ordinal));physical=candidates.get(raw_hash,[])
                    locale_counts={locale:sum(1 for row in physical if row[0]==locale) for locale in ('CHS','EN','JP')}
                    statuses={locale:('missing' if count==0 else 'resolved_unique' if count==1 else 'ambiguous') for locale,count in locale_counts.items()}
                    status='ambiguous' if 'ambiguous' in statuses.values() else 'partial_missing' if 'missing' in statuses.values() else 'resolved_all_locales'
                    expected_owners.append((owner_id,row_id,key,pair_ordinal,raw_hash,status,len(physical)))
                    for locale in ('CHS','EN','JP'):
                        expected_resolutions.append((_id(family,run,owner_id,locale),owner_id,locale,statuses[locale],locale_counts[locale]));missing+=statuses[locale]=='missing';ambiguous+=statuses[locale]=='ambiguous'
                    for physical_row in physical:
                        candidate_id=_id(family,run,owner_id,str(physical_row[2]),str(physical_row[4]))
                        expected_candidates.append((candidate_id,owner_id,*corpus,*physical_row))
            raw_cardinality=(db.execute(f'SELECT COUNT(*) FROM {family}_row_occurrence').fetchone()[0],db.execute(f'SELECT COUNT(*) FROM {family}_text_owner_occurrence').fetchone()[0],db.execute(f'SELECT COUNT(*) FROM {family}_text_resolution_occurrence').fetchone()[0],db.execute(f'SELECT COUNT(*) FROM {family}_text_candidate_occurrence').fetchone()[0])
            if raw_cardinality!=(len(expected_rows),len(expected_owners),len(expected_resolutions),len(expected_candidates)):raise R3VerificationError(f'raw table cardinality mismatch: {family}')
            orphan_owner=db.execute(f'SELECT COUNT(*) FROM {family}_text_owner_occurrence o LEFT JOIN {family}_row_occurrence r ON r.occurrence_id=o.row_occurrence_id WHERE r.occurrence_id IS NULL').fetchone()[0]
            orphan_resolution=db.execute(f'SELECT COUNT(*) FROM {family}_text_resolution_occurrence x LEFT JOIN {family}_text_owner_occurrence o ON o.owner_id=x.owner_id WHERE o.owner_id IS NULL').fetchone()[0]
            orphan_candidate=db.execute(f'SELECT COUNT(*) FROM {family}_text_candidate_occurrence c LEFT JOIN {family}_text_owner_occurrence o ON o.owner_id=c.owner_id WHERE o.owner_id IS NULL').fetchone()[0]
            if (orphan_owner,orphan_resolution,orphan_candidate)!=(0,0,0):raise R3VerificationError(f'orphan child occurrence: {family}')
            actual_rows=list(db.execute(f'SELECT * FROM {family}_row_occurrence ORDER BY source_ordinal,occurrence_id'))
            actual_owners=list(db.execute(f'SELECT o.* FROM {family}_text_owner_occurrence o JOIN {family}_row_occurrence r ON r.occurrence_id=o.row_occurrence_id ORDER BY r.source_ordinal,o.field_pair_ordinal,o.owner_id'))
            actual_resolutions=list(db.execute(f'SELECT x.* FROM {family}_text_resolution_occurrence x JOIN {family}_text_owner_occurrence o ON o.owner_id=x.owner_id JOIN {family}_row_occurrence r ON r.occurrence_id=o.row_occurrence_id ORDER BY r.source_ordinal,o.field_pair_ordinal,x.locale,x.resolution_id'))
            actual_candidates=list(db.execute(f'SELECT c.* FROM {family}_text_candidate_occurrence c JOIN {family}_text_owner_occurrence o ON o.owner_id=c.owner_id JOIN {family}_row_occurrence r ON r.occurrence_id=o.row_occurrence_id ORDER BY r.source_ordinal,o.field_pair_ordinal,c.relative_path,c.pair_ordinal,c.candidate_id'))
            if actual_rows!=expected_rows:raise R3VerificationError(f'physical source rowset mismatch: {family}')
            if actual_owners!=expected_owners:raise R3VerificationError(f'owner-to-row closure mismatch: {family}')
            if actual_resolutions!=expected_resolutions:raise R3VerificationError(f'resolution-to-owner closure mismatch: {family}')
            if actual_candidates!=expected_candidates:raise R3VerificationError(f'physical TextMap candidate rowset mismatch: {family}')
            results.append({'family':family,'source_sha256':sha256_file(root/source_file),'raw_rows':len(expected_rows),'text_owners':len(expected_owners),'text_resolutions':len(expected_resolutions),'text_candidates':len(expected_candidates),'missing_locale_resolutions':missing,'ambiguous_locale_resolutions':ambiguous,'physical_rowsets_exact':True,'parent_child_closure_exact':True,'declared_relations':[]})
    finally:db.close()
    return {'status':'pass','verification_contract':'batch-r3-independent-physical-rowset-verification-v2','artifact':manifest['artifact'],'manifest_sha256':manifest['manifest_sha256'],'textmap_corpus':manifest['textmap_corpus'],'families':results,'family_order':list(R3_FAMILY_ORDER),'product_accepted':False,'corpus_complete':False}

__all__=['R3VerificationError','verify_r3_independently']
