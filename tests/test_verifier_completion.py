"""Actual Event13 source/SQL verification plus the four completion contracts.

The generated source has no game text. Only the fixed upstream Git pin check
is replaced with a fixture byte-seal check; the projection, independent rowset,
schema and mutation checks run unchanged. Other modules' return checks below
are static contract guards, not a substitute for real-source verification.
"""
import ast
import importlib
import inspect
import json
from pathlib import Path
import sqlite3

import pytest

from scripts.build import Rebuild, seal
from scripts.build_support import require_success
from genshin_lore import event13_7_1_physical as builder
from genshin_lore import event13_7_1_physical_verify as oracle

ROOT = Path(__file__).resolve().parents[1]
VERIFIERS = ('event13_7_1_physical_verify', 'event16_18_7_1_selection_verify',
             'native21_7_1_verify', 'native26_7_1_verify')


@pytest.mark.parametrize('name', VERIFIERS)
def test_complete_return_explicitly_matches_scheduler_contract(name):
    function = ast.parse(inspect.getsource(importlib.import_module('genshin_lore.'+name).verify))
    completions = [node.value for node in ast.walk(function)
                   if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)]
    assert len(completions) == 1
    flags = {k.value: v.value for k, v in zip(completions[0].keys, completions[0].values)
             if isinstance(k, ast.Constant) and k.value in ('status','verdict','verified')
             and isinstance(v, ast.Constant)}
    assert flags['verified'] is True
    assert require_success(flags) == 'PASS'


def event_fixture(tmp_path, monkeypatch):
    code = tmp_path/'code'
    for relative in ('src/genshin_lore/event13_7_1_physical.py',
                     'src/genshin_lore/event_7_1_source.py',
                     'src/genshin_lore/event_narrative_source.py',
                     'schemas/event13-7.1-physical-v1.sql'):
        target = code/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT/relative).read_bytes())
    source = tmp_path/'source'
    source.mkdir()
    sources = []
    for i in range(30):
        path = f'fixture-{i}.json'
        (source/path).write_text(json.dumps([{'number': 1000+i}]))
        sources.append({'path':path,'row_count':1,'boundary':'synthetic','kind':f'kind-{i}'})
    maps = [f'TextMap/TextMap{suffix}{locale}.json'
            for locale in ('CHS','EN','JP') for suffix in ('','_Medium')]
    for path in maps:
        (source/path).parent.mkdir(exist_ok=True)
        (source/path).write_text(json.dumps({str(1000+i):f'synthetic evidence {i}' for i in range(30)}))
    history = code/'manifests/event-narrative/archive-wave-scope.json'
    history.parent.mkdir(parents=True)
    history.write_text(json.dumps({'sources':sources}))
    files = [{'path':p,'size_bytes':(source/p).stat().st_size,
              'sha256':builder.sha(source/p),'blob':'fixture-only-not-an-upstream-blob'}
             for p in [s['path'] for s in sources]+maps]
    scope = tmp_path/'scope.json'
    scope.write_text(json.dumps({'contract':'event13-7.1-physical-capability-v1',
        'snapshot_id':'synthetic-snapshot','historical_scope_sha256':builder.sha(history),
        'sources':sources,'files':files}))

    def fixture_bytes(root, declaration):
        for f in declaration['files']:
            assert (root/f['path']).stat().st_size == f['size_bytes']
            if builder.sha(root/f['path']) != f['sha256']:
                raise ValueError('fixture source seal mismatch')

    monkeypatch.setattr(builder, '__file__', str(code/'src/genshin_lore/event13_7_1_physical.py'))
    for module in (builder, oracle):
        monkeypatch.setattr(module, 'REPO', code)
        monkeypatch.setattr(module, 'SCHEMA', code/'schemas/event13-7.1-physical-v1.sql')
        monkeypatch.setattr(module, 'check_files', fixture_bytes)
    monkeypatch.setattr(builder, 'HISTORICAL', history)
    job = object.__new__(Rebuild)
    job.output, job.source, job.history, job.identity_root = tmp_path/'output', source, None, code
    job.output.mkdir()
    return job, scope


def test_actual_event_oracle_produces_qualified_stage_after_all_checks(tmp_path, monkeypatch):
    job, scope = event_fixture(tmp_path, monkeypatch)
    job.task('event_13', lambda p:builder.build(job.source,scope,p),
             lambda p,r:oracle.verify(job.source,scope,p), inputs=[scope])
    result = json.loads((job.output/'event_13/verify.json').read_text())
    assert result['status'] == 'PASS' and result['verified'] is True
    assert result['physical_rows'] == 30 and result['textmap_candidates'] == 180
    assert result['mutation_cases'] > 0
    job.dependency_inputs('event_13')


def test_completion_status_cannot_mask_schema_failure(tmp_path, monkeypatch):
    job, scope = event_fixture(tmp_path, monkeypatch)
    def faulty_build(path):
        result = builder.build(job.source,scope,path)
        with sqlite3.connect(path) as db:
            name = db.execute("SELECT name FROM sqlite_master WHERE type='trigger' LIMIT 1").fetchone()[0]
            db.execute('DROP TRIGGER '+name)
        return {**result,'artifact':seal(path)}
    with pytest.raises(ValueError, match='schema/index/trigger definition drift'):
        job.task('event_13',faulty_build,lambda p,r:oracle.verify(job.source,scope,p),inputs=[scope])
    assert not (job.output/'event_13/verified.json').exists()
    assert list((job.output/'event_13').glob('failure-*.json'))
