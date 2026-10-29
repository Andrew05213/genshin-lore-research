"""Independent Codex source qualification; all 522 roots are synthetic."""
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from scripts.builders import codexquest as builder
from scripts.validators import codexquest as oracle


def git_blob(raw):
    return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()


@pytest.fixture
def source_case(tmp_path, monkeypatch):
    source = tmp_path/'source'
    raw_files = {}
    for i in range(522):
        native = i%497+1 if i<509 else 999 if i<512 else 10000+i
        value = {'AFAPBOFJAIK':None,'FBAFFJDIIFG':[],
            'LFEGFBHFCBH':{'DFKLAEHPOBE':'DialogNormal','textId':1},
            'MFANMBMKKLC':native,'NFFJLFOECKD':{},'questType':'SYNTHETIC'}
        raw_files[f'BinOutput/CodexQuest/{i:04}.json'] = json.dumps(value).encode()
    raw_files[builder.MAIN] = b'[{"id":1},{"id":999}]'
    for lang in ('CHS','EN','JP'):
        for tier in ('','_Medium'):
            raw_files[f'TextMap/TextMap{tier}{lang}.json'] = json.dumps({'1':'synthetic '+lang+tier}).encode()
    for name, raw in raw_files.items():
        p = source/name
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(raw)
    calls = []
    def git(args, **kwargs):
        assert args[:3] == ['git','-C',str(source)]
        calls.append(args[3:])
        if args[3:] == ['rev-parse','HEAD','HEAD^{tree}']:
            return builder.COMMIT+'\n'+builder.TREE+'\n'
        if args[3] == 'ls-tree':
            selected = args[args.index('--')+1:]
            return ''.join(f'100644 blob {git_blob(raw)} {len(raw)}\t{path}\n'
                for path,raw in sorted(raw_files.items())
                if any(path==prefix or path.startswith(prefix+'/') for prefix in selected))
        if args[3] == 'rev-parse' and len(args)==5:
            return git_blob(raw_files[args[4].split(':',1)[1]])+'\n'
        pytest.fail('unexpected Git query: '+repr(args))
    monkeypatch.setattr(builder,'subprocess',SimpleNamespace(check_output=git))
    monkeypatch.setattr(oracle,'subprocess',SimpleNamespace(check_output=git))
    core = tmp_path/'core.sqlite'
    with sqlite3.connect(core) as db:
        db.execute('CREATE TABLE physical_row(occurrence_id,source_pointer,raw_record_json,row_kind)')
        db.executemany('INSERT INTO physical_row VALUES(?,?,?,?)',[
            (str(i),'/'+str(i),json.dumps({'id':i+1,'parentQuestId':i+1 if i<497 else 999}),'primary')
            for i in range(499)])
    db.close()
    seal = {'size_bytes':core.stat().st_size,'sha256':hashlib.sha256(core.read_bytes()).hexdigest()}
    scope = tmp_path/'core.json'
    scope.write_text(json.dumps({'mappings':[{'family':'quest_codex_archive',
        'status':'ACCEPTED_EXACT_7_1_MAPPING','source_snapshot':builder.SNAPSHOT,
        'selected_primary_evidence_rows':499,'selected_support_evidence_rows':142,
        'candidate_artifact':seal,'parser_build_id':'synthetic-parser','ingest_run_id':'synthetic-run',
        'provider_contract':'synthetic-contract'}]}))
    return SimpleNamespace(source=source,scope=scope,core=core,calls=calls,raw_files=raw_files,
                           output=tmp_path/'artifact.sqlite',report=tmp_path/'build.json')


def build_case(case):
    report=builder.build(case.source,case.output,case.scope,case.core)
    case.report.write_text(json.dumps(report))
    return report


def verify_case(case):
    return oracle.verify(case.source,case.output,case.report,case.scope,case.core)


def test_all_rows_independently_verified_with_batched_git_and_one_root_read(source_case, monkeypatch):
    case=source_case
    reads=[]
    original=Path.read_bytes
    def read(path):
        if path.is_relative_to(case.source):reads.append(path.relative_to(case.source).as_posix())
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',read)
    build_case(case)
    roots=[p for p in case.raw_files if p.startswith('BinOutput/')]
    assert all(reads.count(p)==1 for p in roots)
    assert len(case.calls)==3
    reads.clear();case.calls.clear()
    result=verify_case(case)
    assert all(reads.count(p)==1 for p in roots)
    assert len(case.calls)==3
    assert result['verdict']=='PASS_PHYSICAL_PROJECTION'
    assert result['counts']['codex_root']==522 and result['counts']['codex_text_candidate']==1566
    assert result['questcodex_candidate_status']=={'resolved_unique':509,'resolved_candidate_set':3,'missing':10}
    assert result['wrong_identity_merges']==0


@pytest.mark.parametrize('path',[builder.MAIN,'TextMap/TextMapCHS.json','BinOutput/CodexQuest/0000.json'])
def test_builder_rejects_actual_bytes_different_from_pinned_inventory(source_case,path):
    case=source_case
    p=case.source/path
    p.write_bytes(p.read_bytes()+b' ')
    with pytest.raises(ValueError,match='source drift'):
        build_case(case)
    assert not case.output.exists()


def test_oracle_rejects_forged_support_sha_even_if_report_matches_altered_file(source_case):
    case=source_case
    report=build_case(case)
    path='TextMap/TextMapCHS.json'
    raw=json.dumps({'1':'changed synthetic content'}).encode()
    (case.source/path).write_bytes(raw)
    report['release']['source_seals'][path].update(size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    case.report.write_text(json.dumps(report))
    # Git metadata is independently authoritative, not the self-consistent
    # report/file pair. A changed size fails the pinned metadata check first.
    with pytest.raises(ValueError,match='source Git metadata|support source Git bytes'):
        verify_case(case)


def test_oracle_checks_actual_support_git_bytes_when_size_and_claimed_sha_match(source_case):
    case=source_case
    report=build_case(case)
    path='TextMap/TextMapCHS.json'
    raw=(case.source/path).read_bytes().replace(b'synthetic',b'forged___')
    assert len(raw)==len(case.raw_files[path])
    (case.source/path).write_bytes(raw)
    report['release']['source_seals'][path]['sha256']=hashlib.sha256(raw).hexdigest()
    case.report.write_text(json.dumps(report))
    with pytest.raises(ValueError,match='support source Git bytes'):
        verify_case(case)


def test_oracle_does_not_consume_builder_projection_helpers(source_case,monkeypatch):
    case=source_case
    build_case(case)
    monkeypatch.setattr(builder,'wrappers',lambda *_:pytest.fail('oracle imported builder'))
    monkeypatch.setattr(builder,'inventory',lambda *_:pytest.fail('oracle reused builder inventory'))
    assert verify_case(case)['independent_source_to_rowset_oracle']


def test_duplicate_textmap_occurrences_and_main_medium_precedence_remain(source_case):
    case=source_case
    path='TextMap/TextMapCHS.json'
    raw=b'{"1":"synthetic first", "1":"synthetic second"}'
    # Both the synthetic pinned inventory and its working bytes change here;
    # this is a legal duplicate-key source, not a drift simulation.
    case.raw_files[path]=raw
    (case.source/path).write_bytes(raw)
    build_case(case)
    result=verify_case(case)
    assert result['counts']['codex_text_candidate']==522*4
    with sqlite3.connect(case.output) as db:
        rows=db.execute('SELECT source_path,pair_ordinal,raw_text FROM codex_text_candidate '
                        'WHERE source_path=? ORDER BY pair_ordinal',(path,)).fetchall()
    db.close()
    assert len(rows)==1044
    assert set(rows)=={(path,0,'synthetic first'),(path,1,'synthetic second')}


@pytest.mark.parametrize('mutation',[None,'missing','extra','changed'])
def test_streamed_complete_rowset_preserves_historical_digest_and_rejects_drift(mutation):
    expected=[('b',b'\x00\xff','合成\ntext'),('a',b'raw bytes','synthetic')]
    rows=list(expected)
    if mutation=='missing':rows.pop()
    elif mutation=='extra':rows.append(('c',b'extra','synthetic'))
    elif mutation=='changed':rows[0]=('b',b'\x00\xfe','合成\ntext')
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE sample(id PRIMARY KEY,raw BLOB,text)')
        db.executemany('INSERT INTO sample VALUES(?,?,?)',rows)
        if mutation:
            with pytest.raises(ValueError,match='source row'):
                oracle.compare(db,'sample',list(expected))
        else:
            historical=b''.join((oracle.j([v.hex() if isinstance(v,bytes) else v for v in r])+'\n').encode()
                                for r in sorted(expected))
            assert oracle.compare(db,'sample',list(expected))=={
                'rows':2,'sha256':hashlib.sha256(historical).hexdigest()}
    db.close()
