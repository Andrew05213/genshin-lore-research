"""Failure and resume contracts use only generated fixture bytes."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.build_support import require_success
from scripts.build import Rebuild, seal

ROOT = Path(__file__).resolve().parents[1]


def job(tmp_path):
    build = object.__new__(Rebuild)
    build.output = tmp_path/'output'
    build.output.mkdir()
    build.source = tmp_path/'source'
    build.history = None
    build.identity_root = tmp_path/'code'
    for directory, filename in [('src', 'parser.py'), ('scripts', 'verifier.py'),
                                ('manifests', 'scope.json'), ('schemas', 'contract.sql')]:
        path = build.identity_root/directory/filename
        path.parent.mkdir(parents=True)
        path.write_text('original fixture')
    return build


def builder(path):
    path.write_bytes(b'synthetic artifact')
    return {'artifact': seal(path)}


@pytest.mark.parametrize('result', [None, {}, [], {'counts': {}}, {'status': 'NOT_RUN'},
    {'status': 'SKIPPED'}, {'status': 'PARTIAL'}, {'status': 'UNKNOWN'},
    {'status': ''}, {'status': True}, {'status': 'PASS', 'verdict': 'FAIL'},
    {'status': 'PASS', 'verdict': 'PASS_PHYSICAL_PROJECTION'},
    {'status': 'PASS', 'partial': True}])
def test_non_success_never_creates_receipt(tmp_path, result):
    build = job(tmp_path)
    with pytest.raises(ValueError, match='rejected'):
        build.task('fixture', builder, lambda *_: result)
    assert not (build.output/'fixture/verified.json').exists()
    assert list((build.output/'fixture').glob('failure-*.json'))
    assert build.path('fixture').read_bytes() == b'synthetic artifact'


@pytest.mark.parametrize('result', [{'status': 'PASS'}, {'verdict': 'pass'},
    {'status': 'PASS', 'verdict': 'PASS'}, {'status': 'PASS_COMPLETE_SOURCE_ROWSETS'},
    {'verdict': 'PASS_PHYSICAL_PROJECTION', 'logical_relation_closure': False}])
def test_actual_complete_verification_contracts_are_accepted(result):
    assert require_success(result)


def test_unchanged_stage_resumes_without_rerunning(tmp_path):
    build = job(tmp_path)
    build.task('fixture', builder, lambda *_: {'status': 'PASS'})
    build.task('fixture', lambda *_: pytest.fail('builder reran'),
               lambda *_: pytest.fail('verifier reran'))


@pytest.mark.parametrize('relative', ['src/parser.py', 'scripts/verifier.py',
    'manifests/scope.json', 'schemas/contract.sql'])
def test_implementation_or_scope_change_rejects_resume(tmp_path, relative):
    build = job(tmp_path)
    build.task('fixture', builder, lambda *_: {'status': 'PASS'})
    (build.identity_root/relative).write_text('changed fixture')
    with pytest.raises(ValueError, match='identity mismatch'):
        build.task('fixture', builder, lambda *_: {'status': 'PASS'})


def test_documentation_does_not_invalidate_stage(tmp_path):
    build = job(tmp_path)
    build.task('fixture', builder, lambda *_: {'status': 'PASS'})
    (build.identity_root/'README.md').write_text('documentation only')
    build.task('fixture', lambda *_: pytest.fail('rebuilt for docs'), lambda *_: None)


def test_environment_change_rejects_resume(tmp_path, monkeypatch):
    from scripts import build_support
    build = job(tmp_path)
    build.task('fixture', builder, lambda *_: {'status': 'PASS'})
    monkeypatch.setattr(build_support, 'environment_identity', lambda: {'sqlite': 'changed'})
    with pytest.raises(ValueError, match='identity mismatch'):
        build.task('fixture', builder, lambda *_: {'status': 'PASS'})


def test_runtime_dependency_contract_change_rejects_resume(tmp_path):
    build = job(tmp_path)
    requirements = build.identity_root/'requirements-build.txt'
    requirements.write_text('synthetic==1')
    build.task('fixture', builder, lambda *_: {'status': 'PASS'})
    requirements.write_text('synthetic==2')
    with pytest.raises(ValueError, match='identity mismatch'):
        build.task('fixture', builder, lambda *_: {'status': 'PASS'})


@pytest.mark.parametrize('record', ['build.json', 'verify.json', 'invocation.json', 'verified.json'])
@pytest.mark.parametrize('action', ['missing', 'corrupt', 'changed'])
def test_incomplete_or_modified_record_rejects_resume(tmp_path, record, action):
    build = job(tmp_path)
    build.task('fixture', builder, lambda *_: {'status': 'PASS'})
    path = build.output/'fixture'/record
    if action == 'missing':
        path.unlink()
    elif action == 'corrupt':
        path.write_text('{')
    else:
        value = json.loads(path.read_text())
        value['extra'] = 'changed'
        if record == 'verified.json':
            value['contract'] = 'legacy receipt'
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='resume rejected'):
        build.task('fixture', builder, lambda *_: {'status': 'PASS'})


def test_dependency_and_parameters_are_bound(tmp_path):
    build = job(tmp_path); dependency = tmp_path/'input.json'
    dependency.write_text('original binding')
    build.task('fixture', builder, lambda *_: {'status': 'PASS'},
               inputs=[dependency], parameters={'locale': 'CHS'})
    with pytest.raises(ValueError, match='identity mismatch'):
        build.task('fixture', builder, lambda *_: {'status': 'PASS'},
                   inputs=[dependency], parameters={'locale': 'EN'})
    dependency.write_text('changed binding')
    with pytest.raises(ValueError, match='identity mismatch'):
        build.task('fixture', builder, lambda *_: {'status': 'PASS'},
                   inputs=[dependency], parameters={'locale': 'CHS'})


def test_failed_stage_is_not_automatically_reverified(tmp_path):
    build = job(tmp_path)
    with pytest.raises(ValueError):
        build.task('fixture', builder, lambda *_: {'status': 'NOT_RUN'})
    with pytest.raises(ValueError, match='resume rejected'):
        build.task('fixture', lambda *_: pytest.fail('rebuilt failed stage'),
                   lambda *_: pytest.fail('reverified failed stage'))


def test_verifier_cannot_change_artifact(tmp_path):
    build = job(tmp_path)
    def mutate(path, report):
        path.write_bytes(b'mutated')
        return {'status': 'PASS'}
    with pytest.raises(ValueError, match='changed artifact'):
        build.task('fixture', builder, mutate)
    assert not (build.output/'fixture/verified.json').exists()


@pytest.mark.parametrize('optimized', [False, True])
def test_research_schema_validation_survives_python_optimization(optimized):
    code = """import sqlite3
from scripts.validators.research_index import verify_schema
db=sqlite3.connect(':memory:')
db.execute('CREATE TABLE incorrect(id)')
try:
    verify_schema(db)
except ValueError:
    print('REJECTED')
else:
    raise SystemExit('invalid schema accepted')
"""
    command = [sys.executable] + (['-O'] if optimized else []) + ['-c', code]
    result = subprocess.run(command, cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT/'src')},
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'REJECTED' in result.stdout
