"""Filmfest uses an explicit output-local Talk manifest, never a sibling build."""
import hashlib
import json
import sqlite3

import pytest

from genshin_lore import event_filmfest_7_1_build as filmfest


def talk_input(root, run='synthetic-run'):
    root.mkdir()
    source = root / 'source'
    source.mkdir()
    (source / 'levels.json').write_text('[{"talk":123}]')
    raw = b'{"synthetic_talk":123}\n'
    (source / 'talk.json').write_bytes(raw)
    artifact = root / 'talk.sqlite'
    with sqlite3.connect(artifact) as db:
        db.executescript('''
            CREATE TABLE physical_container(id,ingest_run_id,source_path,pointer,native_id,raw_sha256,kind);
            CREATE TABLE source_file(path,raw_bytes,sha256);
        ''')
        db.execute('INSERT INTO source_file VALUES(?,?,?)',
                   ('talk.json', raw, hashlib.sha256(raw).hexdigest()))
        db.execute('INSERT INTO physical_container VALUES(?,?,?,?,?,?,?)',
                   ('occurrence-' + run, run, 'talk.json', '/', '123',
                    hashlib.sha256(raw.strip()).hexdigest(), 'talk_root'))
    manifest = {'artifact': {'size_bytes': artifact.stat().st_size,
                            'sha256': filmfest.sha(artifact)},
                'contract': 'synthetic-contract', 'parser_build_id': 'synthetic-parser',
                'ingest_run_id': run}
    path = root / 'dynamic-manifest.json'
    path.write_text(json.dumps(manifest))
    scope = {'sources': [{'kind': 'filmfest_shooting_level', 'path': 'levels.json',
                          'talk_relation_field': 'talk'}],
             'talk_candidate_namespace': {'accepted_manifest_sha256': filmfest.sha(path),
                 'artifact': manifest['artifact'], 'contract': manifest['contract'],
                 'parser': manifest['parser_build_id'], 'run': run, 'search_complete': True},
             'talk_candidates': []}
    return source, artifact, path, scope


def test_explicit_manifest_and_frozen_target_set(tmp_path, monkeypatch):
    source, artifact, manifest, scope = talk_input(tmp_path / 'build')
    monkeypatch.setattr(filmfest, 'QT_MANIFEST', tmp_path / 'absent-source-manifest')
    scope['talk_candidates'] = filmfest.discover_target_candidates(
        source, artifact, scope, qt_manifest_path=manifest)
    assert len(scope['talk_candidates']) == 1
    assert filmfest._target_candidates(source, artifact, scope,
        qt_manifest_path=manifest) == scope['talk_candidates']
    scope['talk_candidates'] = []
    with pytest.raises(ValueError, match='complete accepted Talk candidate set drift'):
        filmfest._target_candidates(source, artifact, scope, qt_manifest_path=manifest)


@pytest.mark.parametrize('field,value', [('accepted_manifest_sha256', 'bad'),
    ('run', 'other-run'), ('parser', 'other-parser'), ('contract', 'other-contract'),
    ('search_complete', False), ('artifact', {'size_bytes': 0, 'sha256': 'bad'})])
def test_explicit_manifest_does_not_relax_binding(tmp_path, field, value):
    source, artifact, manifest, scope = talk_input(tmp_path / 'build')
    scope['talk_candidate_namespace'][field] = value
    with pytest.raises(ValueError, match='artifact binding'):
        filmfest.discover_target_candidates(source, artifact, scope, qt_manifest_path=manifest)


def test_two_outputs_do_not_share_talk_binding(tmp_path):
    first = talk_input(tmp_path / 'a', 'run-a')
    second = talk_input(tmp_path / 'b', 'run-b')
    results = []
    for source, artifact, manifest, scope in (first, second):
        results.append(filmfest.discover_target_candidates(
            source, artifact, scope, qt_manifest_path=manifest))
    assert results[0][0]['target_run_id'] == 'run-a'
    assert results[1][0]['target_run_id'] == 'run-b'
    source, artifact, _, scope = first
    with pytest.raises(ValueError, match='artifact binding'):
        filmfest.discover_target_candidates(source, artifact, scope,
                                           qt_manifest_path=second[2])


def test_historical_default_manifest_remains_explicitly_compatible(tmp_path, monkeypatch):
    source, artifact, manifest, scope = talk_input(tmp_path / 'build')
    monkeypatch.setattr(filmfest, 'QT_MANIFEST', manifest)
    assert filmfest.discover_target_candidates(source, artifact, scope) == \
        filmfest.discover_target_candidates(source, artifact, scope, qt_manifest_path=manifest)


def test_source_coordinates_are_still_read_through(tmp_path):
    source, artifact, manifest, scope = talk_input(tmp_path / 'build')
    (source / 'talk.json').write_bytes(b'{"different_source":123}')
    with pytest.raises(ValueError, match='external Talk second-hop mismatch'):
        filmfest.discover_target_candidates(source, artifact, scope, qt_manifest_path=manifest)
