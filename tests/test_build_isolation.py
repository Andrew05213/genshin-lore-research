"""Build-local metadata and config resolution; fixtures contain no game text."""
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
from pathlib import Path

import pytest

from scripts.build import Rebuild, seal
from scripts.runtime_config import load_runtime_paths


def make_job(output, code):
    job = object.__new__(Rebuild)
    job.output = output
    output.mkdir()
    job.source = code/'source'
    job.history = None
    job.identity_root = code
    return job


def test_two_outputs_share_code_not_bindings_or_stage_receipts(tmp_path):
    code = tmp_path/'code'
    (code/'src').mkdir(parents=True)
    (code/'src/parser.py').write_text('synthetic implementation')
    before = (code/'src/parser.py').read_bytes()
    jobs = [make_job(tmp_path/name, code) for name in ('a', 'b')]

    def build(job):
        def builder(path):
            path.write_bytes(b'synthetic evidence')
            return {'artifact': seal(path)}
        job.task('component', builder, lambda *_: {'status': 'PASS'})
        job.bind('manifests/test/runtime.json', {'artifact': str(job.path('component'))})
        job.task('dependent', builder, lambda *_: {'status': 'PASS'},
                 inputs=job.dependency_inputs('component') + [job.metadata_path('manifests/test/runtime.json')])

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(build, jobs))
    for job in jobs:
        value = json.loads(job.metadata_path('manifests/test/runtime.json').read_text())
        assert value['artifact'] == str(job.path('component'))
        build(job)  # Exact repeat resumes each output independently.
    assert not (code/'manifests').exists()
    assert (code/'src/parser.py').read_bytes() == before
    assert jobs[0].metadata_path('manifests/test/runtime.json').read_text() != jobs[1].metadata_path('manifests/test/runtime.json').read_text()


def test_dependency_qualification_rejects_implementation_drift(tmp_path):
    code = tmp_path/'code'
    (code/'src').mkdir(parents=True)
    parser = code/'src/parser.py'
    parser.write_text('original')
    job = make_job(tmp_path/'output', code)
    def builder(path):
        path.write_bytes(b'synthetic')
        return {'artifact': seal(path)}
    job.task('component', builder, lambda *_: {'status': 'PASS'})
    parser.write_text('changed')
    with pytest.raises(ValueError, match='dependency implementation/input identity drift'):
        job.dependency_inputs('component')


def test_metadata_cannot_escape_output(tmp_path):
    job = make_job(tmp_path/'output', tmp_path/'code')
    with pytest.raises(ValueError):
        job.bind('../outside.json', {})


def test_core_index_reads_only_its_own_generated_mappings(tmp_path):
    from scripts.builders.research_dataset import core_mappings
    manifests = []
    for arm in ('a', 'b'):
        metadata = tmp_path/arm/'metadata'
        corpus = metadata/'manifests/research/corpus.json'
        corpus.parent.mkdir(parents=True)
        corpus.write_text('{}')
        for name, count in [('m3-7.1-provider', 20), ('special-core-7.1', 13)]:
            path = metadata/'manifests'/name/'accepted-exact-mappings-v1.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'mappings': [{'arm': arm}] * count}))
        manifests.append(corpus)
    assert all(item['arm'] == 'a' for item in core_mappings(manifests[0]))
    assert all(item['arm'] == 'b' for item in core_mappings(manifests[1]))
    (manifests[0].parents[2]/'manifests/m3-7.1-provider/accepted-exact-mappings-v1.json').unlink()
    with pytest.raises(FileNotFoundError):
        core_mappings(manifests[0])  # no source-tree/sibling fallback


def test_relative_config_has_no_cwd_or_sibling_fallback(tmp_path, monkeypatch):
    outputs = []
    for name in ('a', 'b'):
        output = tmp_path/name
        output.mkdir()
        values = {}
        for key in ('corpus', 'bindings', 'index', 'profile'):
            file = output/(key+'.fixture')
            file.write_text('synthetic '+name)
            values[key] = file.name
        config = output/'runtime-config.json'
        config.write_text(json.dumps(values))
        outputs.append(config)
    monkeypatch.chdir(tmp_path)
    a, b = [load_runtime_paths(config) for config in outputs]
    assert a['profile'] != b['profile']
    a['profile'].unlink()
    with pytest.raises(FileNotFoundError, match='profile'):
        load_runtime_paths(outputs[0])
    assert load_runtime_paths(outputs[1]) == b


@pytest.mark.parametrize('values', [{}, {'corpus': ''}, [], {'corpus': 1}])
def test_incomplete_config_rejected(tmp_path, values):
    config = tmp_path/'runtime-config.json'
    config.write_text(json.dumps(values))
    with pytest.raises(ValueError):
        load_runtime_paths(config)


@pytest.mark.parametrize('name', ['native21_7_1_build', 'native26_7_1_physical'])
def test_native_import_does_not_require_generated_machine_bindings(name):
    module = importlib.import_module('genshin_lore.'+name)
    assert callable(module.build)
    assert 'metadata_root' in __import__('inspect').signature(module.build).parameters
