"""Exercise actual Event/Native orchestration, not just Rebuild.task in isolation.

Builders use tiny synthetic SQLite files. Verifier signatures and completion
flags come from the actual implementations; their source/rowset algorithms are
not exercised here. This catches driver wiring errors without game data.
"""
import ast
import importlib
import inspect
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from scripts import build as driver


def completion(function):
    values = []
    for node in ast.walk(ast.parse(inspect.getsource(function))):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            flags = {k.value:v.value for k,v in zip(node.value.keys,node.value.values)
                     if isinstance(k,ast.Constant) and isinstance(v,ast.Constant)
                     and k.value in ('status','verdict','verified')}
            if flags:
                values.append(flags)
    assert len(values) == 1, function.__module__
    return values[0]


def synthetic_build(path, arm):
    release = {'contract':'synthetic-contract','parser_build_id':'parser-'+arm,
               'ingest_run_id':'run-'+arm,'snapshot_id':driver.SNAPSHOT}
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE release(manifest_json);
            CREATE TABLE physical_row(id,ordinal,pointer,raw_sha256,source_path);
            CREATE TABLE selected_occurrence(id,source_path,pointer,ordinal,raw_sha256,source_kind);
        ''')
        db.execute('INSERT INTO release VALUES(?)',(json.dumps(release),))
    return {**release,'artifact':driver.seal(path)}


def test_event_native_stages_use_own_metadata_and_real_completion_contracts(tmp_path, monkeypatch):
    metadata_roots = []
    for arm in ('a','b'):
        job = object.__new__(driver.Rebuild)
        job.output,job.source,job.history = tmp_path/arm,tmp_path/'source',tmp_path/'history'
        job.identity_root = tmp_path/'code'
        job.output.mkdir()
        for name in ('quest_talk','quest_narrative','narrative_npc','narrative_dialog',
                     'narrative_reminder','native_message'):
            job.task(name,lambda p:synthetic_build(p,arm),lambda *_:{'status':'PASS'})
        qt = {**job.report('quest_talk'),'local_artifact_path':str(job.path('quest_talk'))}
        job.bind('manifests/quest-talk-7.1/standalone-candidate.json',qt)
        job.bind('manifests/quest-derived-7.1/standalone-candidates-v1.json',
                 {'components':{'quest_narrative_archive':job.runtime(
                     'quest_narrative','quest_narrative_archive',True)}})
        calls = []

        def module(name):
            actual = importlib.import_module('genshin_lore.'+name)
            fake = SimpleNamespace()
            for operation in ('build','verify'):
                if not hasattr(actual,operation):
                    continue
                function = getattr(actual,operation)
                signature = inspect.signature(function)
                flags = completion(function) if operation == 'verify' else None
                def invoke(*args,_op=operation,_sig=signature,_flags=flags,**kwargs):
                    bound = _sig.bind(*args,**kwargs)
                    if name in ('event_filmfest_7_1_build','event_filmfest_7_1_verify'):
                        assert bound.arguments['qt_manifest_path'] == job.metadata_path(
                            'manifests/quest-talk-7.1/standalone-candidate.json')
                    if name.startswith(('native21_','native26_')):
                        assert bound.arguments['metadata_root'] == job.metadata
                    if name.startswith('native21_'):
                        scope = json.loads(Path(bound.arguments['scope_path']).read_text())
                        binding = Path(scope['event13_snez_binding'])
                        assert binding.is_relative_to(job.metadata)
                        assert driver.sha(binding) == scope['event13_snez_binding_sha256']
                    if name.startswith('native26_'):
                        scope = json.loads(Path(bound.arguments['scope_path']).read_text())
                        assert scope['event16_manifest_sha256'] == driver.sha(job.metadata_path(
                            'manifests/event-narrative-7.1/component-16-physical-runtime-v1.json'))
                    if name.startswith('event16_18_'):
                        scope = json.loads(Path(bound.arguments['scope_path']).read_text())
                        for item in scope['accepted_inputs'].values():
                            runtime = Path(item['runtime_manifest'])
                            assert runtime.is_relative_to(job.metadata)
                            assert driver.sha(runtime) == item['runtime_manifest_sha256']
                    calls.append((name,_op))
                    if _op == 'build':
                        return synthetic_build(bound.arguments['output'],arm)
                    return dict(_flags)
                setattr(fake,operation,invoke)
            if hasattr(actual,'dependency_manifests'):
                fake.dependency_manifests = actual.dependency_manifests
            if name == 'event_filmfest_7_1_build':
                def discover(source,artifact,scope,*,qt_manifest_path):
                    assert qt_manifest_path == job.metadata_path('manifests/quest-talk-7.1/standalone-candidate.json')
                    assert scope['talk_candidate_namespace']['run'] == 'run-'+arm
                    assert scope['talk_candidate_namespace']['accepted_manifest_sha256'] == driver.sha(qt_manifest_path)
                    return []
                fake.discover_target_candidates = discover
            if name == 'native_aux_quest_7_1_verify':
                fake.expected_external = lambda *_:([],{'quest_narrative':{'parser':'synthetic','run':'synthetic'}})
            return fake

        def scope_command(argv,**kwargs):
            assert Path(argv[1]).name == 'native_book_music_scope.py'
            assert Path(argv[argv.index('--message-runtime')+1]) == job.metadata_path(
                'manifests/native-archive-7.1/message-runtime-v1.json')
            Path(argv[argv.index('--output')+1]).write_text('{}')

        monkeypatch.setattr(driver,'module',module)
        with monkeypatch.context() as command_patch:
            command_patch.setattr(driver.subprocess,'run',scope_command)
            job.events()
            job.native()
        for name in ('event_13','event_16','event_18','native_successor','native_high_value'):
            receipt = json.loads((job.output/name/'verified.json').read_text())
            result = json.loads((job.output/name/'verify.json').read_text())
            assert receipt['independent_verifier_completed'] is True
            assert result == {'status':'PASS','verified':True}
            job.dependency_inputs(name)
        assert len(calls) == 28  # Nine Event + five Native builders and verifiers.
        metadata_roots.append(job.metadata)
    assert metadata_roots[0] != metadata_roots[1]
    for metadata in metadata_roots:
        binding = json.loads((metadata/'manifests/native-archive-7.1/bulk-runtime.json').read_text())
        assert binding['release']['ingest_run_id'] == 'run-'+metadata.parent.name
