"""Synthetic tests; no game text or deployed database is a fixture."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT)]
spec=importlib.util.spec_from_file_location('portable_complete',ROOT/'scripts/build.py')
driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)


def job(tmp_path):
    result=object.__new__(driver.Rebuild)
    result.output=tmp_path;result.source=tmp_path/'source';result.history=None
    result.identity_root=tmp_path/'implementation'
    (result.identity_root/'src').mkdir(parents=True)
    (result.identity_root/'src/parser.py').write_text('parser version 1')
    return result


def test_verified_resume_checks_seal_and_sidecars(tmp_path):
    build=job(tmp_path)
    def builder(path):path.write_bytes(b'synthetic');return {'artifact':driver.seal(path)}
    build.task('fixture',builder,lambda p,r:{'status':'PASS'})
    build.task('fixture',lambda p:pytest.fail('rebuilding a qualified resume'),lambda p,r:pytest.fail('unexpected verifier'))
    build.path('fixture').write_bytes(b'changed')
    with pytest.raises(ValueError,match='seal/sidecar drift'):build.task('fixture',builder,lambda p,r:{'status':'PASS'})


def test_failure_preserved_and_never_qualified(tmp_path):
    build=job(tmp_path)
    def builder(path):path.write_bytes(b'failed attempt');raise ValueError('synthetic failure')
    with pytest.raises(ValueError,match='synthetic failure'):build.task('fixture',builder,lambda p,r:{'status':'PASS'})
    assert build.path('fixture').read_bytes()==b'failed attempt'
    assert list((tmp_path/'fixture').glob('failure-*.json'))
    assert not (tmp_path/'fixture/verified.json').exists()


def test_verification_failure_never_becomes_receipt(tmp_path):
    build=job(tmp_path)
    def builder(path):path.write_bytes(b'synthetic');return {'artifact':driver.seal(path)}
    with pytest.raises(ValueError,match='rejected'):build.task('fixture',builder,lambda p,r:{'status':'FAIL'})
    assert not (tmp_path/'fixture/verified.json').exists()


def test_bindings_cannot_be_overwritten(tmp_path):
    p=tmp_path/'binding.json'
    driver.write(p,{'artifact':'synthetic'});driver.write(p,{'artifact':'synthetic'})
    with pytest.raises(FileExistsError,match='binding drift'):driver.write(p,{'artifact':'different'})


def test_index_capacity_fails_before_creating_outputs(tmp_path,monkeypatch):
    output=tmp_path/'absent'
    monkeypatch.setattr(sys,'argv',['rebuild','--source','missing','--output-root',str(output),
        '--stage','index','--available-allocated-bytes','1'])
    with pytest.raises(ValueError,match='headroom'):driver.main()
    assert not output.exists()


def test_release_reader_preserves_declared_identity(tmp_path):
    import sqlite3
    p=tmp_path/'synthetic.sqlite'
    with sqlite3.connect(p) as db:
        db.execute('CREATE TABLE codex_release(manifest_json TEXT)')
        db.execute('INSERT INTO codex_release VALUES(?)',(json.dumps({'parser_build_id':'synthetic','contract':'synthetic'}),))
    assert driver.release(p)=={'parser_build_id':'synthetic','contract':'synthetic'}


def test_coordinate_index_hint_preserves_ambiguous_physical_rows():
    import sqlite3
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE research_container(source_key TEXT,evidence_family TEXT,source_path TEXT,source_pointer TEXT,evidence_run TEXT)')
    db.execute('CREATE INDEX research_container_path ON research_container(source_key,source_path,source_pointer)')
    db.executemany('INSERT INTO research_container VALUES(?,?,?,?,?)',[
        ('a','synthetic','p','/1','run-a'),('b','synthetic','p','/1','run-b'),
        ('a','other','p','/1','run-c'),('a','synthetic','q','/1','run-d')])
    terms="source_key IN (?,?) AND evidence_family=? AND source_path=? AND source_pointer=?"
    args=('a','b','synthetic','p','/1')
    plain=db.execute('SELECT * FROM research_container WHERE '+terms+' LIMIT 2',args).fetchall()
    hinted=db.execute('SELECT * FROM research_container INDEXED BY research_container_path WHERE '+terms+' LIMIT 2',args).fetchall()
    assert sorted(plain)==sorted(hinted)
    assert len(hinted)==2  # do not select a unique owner from ambiguous data
    plan=db.execute('EXPLAIN QUERY PLAN SELECT * FROM research_container INDEXED BY research_container_path WHERE '+terms,args).fetchall()
    assert 'research_container_path' in str(plan)


def test_mcp_payload_uses_pinned_sdk_fields_and_rejects_errors():
    from types import SimpleNamespace
    from scripts.check import payload
    assert payload(SimpleNamespace(is_error=False,structured_content={'status':'ok'}))=={'status':'ok'}
    assert payload(SimpleNamespace(is_error=False,structured_content=None,
        content=[SimpleNamespace(type='text',text='{"status":"ok"}')]))=={'status':'ok'}
    with pytest.raises(ValueError,match='MCP call failed'):
        payload(SimpleNamespace(is_error=True))


def test_runtime_smoke_obeys_existing_unified_search_and_book_contract(monkeypatch):
    from types import SimpleNamespace
    from scripts import serve as runtime_server
    calls=[]
    query=SimpleNamespace(corpus_ref='synthetic',
        search=lambda *a,**kw:{'items':[{'evidence_ref':'formal'}]},
        read=lambda *a:None,inspect=lambda *a:{'evidence_ref':'formal'},follow=lambda *a,**kw:None)
    runtime=SimpleNamespace(query=query,
        search=lambda keyword,capability,**kw:{'results':[{'evidence_ref':capability}]},
        read=lambda *a:None,inspect=lambda *a:None,
        follow=lambda ref,**kw:calls.append(ref),close=lambda:None)
    monkeypatch.setattr(runtime_server,'open_runtime',lambda config:runtime)
    assert runtime_server.smoke('synthetic')['status']=='PASS_LOCAL_REBUILT_RUNTIME'
    assert calls==['character_avatar']  # Readable continuation is not follow.


def test_capacity_deduplicates_nested_roots_hardlinks_without_following_symlinks(tmp_path):
    import os
    child=tmp_path/'data';child.mkdir()
    file=child/'artifact';file.write_bytes(b'synthetic' * 1000)
    os.link(file,child/'same-inode');link=child/'symlink';link.symlink_to(file)
    stat=link.lstat()
    expected=file.stat().st_blocks*512+(stat.st_blocks*512 or stat.st_size)
    assert driver.allocated((tmp_path,child))==expected


def test_index_budget_deducts_new_allocations_not_existing_resume_inputs(tmp_path,monkeypatch):
    build=job(tmp_path);build.available=9_000_000_000;build.allocated_start=5_000_000_000
    monkeypatch.setattr(driver,'allocated',lambda paths:6_000_000_000)
    assert build.index_headroom()==8_000_000_000
    build.allocated_start=6_000_000_000
    assert build.index_headroom()==9_000_000_000


def test_index_budget_stops_when_new_stages_consume_required_headroom(tmp_path,monkeypatch):
    build=job(tmp_path);build.available=9_000_000_000;build.allocated_start=5_000_000_000
    monkeypatch.setattr(driver,'allocated',lambda paths:8_000_000_000)
    with pytest.raises(ValueError,match='consumed required index peak headroom'):build.index_headroom()
    assert not build.path('research_index').exists()
