"""Explicit stage scopes retain receipt, source and negative drift checks."""
import json
from pathlib import Path

import pytest

from scripts.build_support import pipeline_identity, code_scope, RECEIPT_CONTRACT
from test_build_trust import job, builder


def scoped_job(tmp_path):
    result = job(tmp_path)
    root = result.identity_root
    for name in ('character', 'semantic', 'reader'):
        (root/f'src/{name}.py').write_text('original')
    policy = {'contract':'explicit-build-code-scopes-v1', 'unknown_files':'shared',
        'unknown_stages':'full', 'groups':{
        'character':{'stages':['character'], 'files':['src/character.py','scripts/verifier.py']},
        'semantic':{'stages':['semantic'], 'files':['src/semantic.py']},
        'runtime':{'stages':[], 'files':['src/reader.py']}}}
    (root/'recipes').mkdir()
    (root/'recipes/build-code-scopes.json').write_text(json.dumps(policy))
    return result


@pytest.mark.parametrize('path', ['src/reader.py', 'src/semantic.py'])
def test_unrelated_runtime_or_semantic_change_reuses_character_stage(tmp_path, path):
    build = scoped_job(tmp_path)
    build.task('character', builder, lambda *_:{'status':'PASS'})
    (build.identity_root/path).write_text('unrelated change')
    build.task('character', lambda *_:pytest.fail('unrelated rebuild'), lambda *_:pytest.fail('unrelated verify'))
    receipt = json.loads((build.output/'character/verified.json').read_text())
    assert receipt['contract'] == RECEIPT_CONTRACT and receipt['identity']['code_scope'] == ['character']


@pytest.mark.parametrize('path', ['src/character.py','scripts/verifier.py','src/parser.py',
                                 'schemas/contract.sql','manifests/scope.json',
                                 'recipes/build-code-scopes.json'])
def test_own_parser_verifier_unknown_shared_contract_and_policy_still_reject(tmp_path, path):
    build = scoped_job(tmp_path)
    build.task('character', builder, lambda *_:{'status':'PASS'})
    if path == 'recipes/build-code-scopes.json':
        policy=json.loads((build.identity_root/path).read_text())
        policy['note']='changed policy bytes'
        (build.identity_root/path).write_text(json.dumps(policy))
    else:
        (build.identity_root/path).write_text('changed')
    with pytest.raises((ValueError,json.JSONDecodeError),match='resume rejected'):
        build.task('character', builder, lambda *_:{'status':'PASS'})


def test_unknown_stage_remains_full_and_old_receipt_cannot_be_relabelled(tmp_path):
    build = scoped_job(tmp_path)
    assert pipeline_identity(build.identity_root,build.source,None,parameters={'stage':'unknown'})['code_scope']=='conservative_full'
    build.task('character',builder,lambda *_:{'status':'PASS'})
    p=build.output/'character/verified.json';r=json.loads(p.read_text())
    r['contract']='local-build-stage-receipt-v2';p.write_text(json.dumps(r))
    with pytest.raises(ValueError,match='resume rejected'):
        build.task('character',builder,lambda *_:{'status':'PASS'})


def test_real_ownership_keeps_shared_helpers_and_splits_runtime_semantic():
    root=Path(__file__).resolve().parents[1]
    character=pipeline_identity(root,root,None,parameters={'stage':'character_foundation'})
    semantic=pipeline_identity(root,root,None,parameters={'stage':'semantic'})
    files=character['implementation_and_contracts']
    assert 'scripts/build_support.py' in files and 'src/genshin_lore/textmap_input_cache.py' in files
    assert 'src/genshin_lore/character_7_1_foundation_verify.py' in files
    assert 'scripts/builders/semantic.py' not in files
    assert 'src/genshin_lore/dialogue/codex.py' not in files
    assert 'scripts/builders/semantic.py' in semantic['implementation_and_contracts']
    assert code_scope(root,'native_message')[0] >= {'native','event','quest','assembly','m3','special'}
    assert code_scope(root,'semantic')[0] >= {'research','semantic'}


def test_known_stage_domain_dependencies_and_unclassified_files_remain_bound(tmp_path):
    build = scoped_job(tmp_path)
    policy_path = build.identity_root/'recipes/build-code-scopes.json'
    policy = json.loads(policy_path.read_text())
    policy['groups']['character']['depends'] = ['semantic']
    policy_path.write_text(json.dumps(policy))
    build.task('character', builder, lambda *_:{'status':'PASS'})
    (build.identity_root/'src/semantic.py').write_text('changed dependency')
    with pytest.raises(ValueError, match='resume rejected'):
        build.task('character', builder, lambda *_:{'status':'PASS'})


def test_source_root_and_explicit_parameters_still_bind(tmp_path):
    build = scoped_job(tmp_path)
    build.task('character', builder, lambda *_:{'status':'PASS'}, parameters={'locale':'CHS'})
    with pytest.raises(ValueError, match='resume rejected'):
        build.task('character', builder, lambda *_:{'status':'PASS'}, parameters={'locale':'EN'})
    build.source = tmp_path/'other-source'
    with pytest.raises(ValueError, match='resume rejected'):
        build.task('character', builder, lambda *_:{'status':'PASS'}, parameters={'locale':'CHS'})
