"""Real SQLite/output lifecycle failures, with synthetic projection preflights.

These probes call the actual builders and publisher. They exercise durability
and diagnostic retention, not the large source parsers replaced by fixtures.
"""
from contextlib import closing
import importlib
import json
from pathlib import Path
import runpy
import sqlite3
from types import SimpleNamespace

import pytest

from genshin_lore import batch_release


class InjectedFailure(OSError):
    pass


@pytest.fixture
def schema_failure(monkeypatch):
    retained = []
    error = InjectedFailure('synthetic failure after real SQLite schema creation')
    original = sqlite3.connect

    class BrokenSchema(sqlite3.Connection):
        def executescript(self, sql):
            super().executescript(sql)
            self.execute('CREATE TABLE retention_probe(value TEXT)')
            self.execute("INSERT INTO retention_probe VALUES('synthetic diagnostic')")
            self.commit()
            path = Path(self.execute('PRAGMA database_list').fetchone()[2])
            journal = Path(str(path)+'-journal')
            journal.write_bytes(b'synthetic diagnostic sidecar')
            retained.append((path, journal))
            raise error

    def install(module):
        def connect(*args, **kwargs):
            return original(*args, **{**kwargs, 'factory': BrokenSchema})
        monkeypatch.setattr(module, 'sqlite3', SimpleNamespace(**{**vars(sqlite3), 'connect': connect}))

    def check():
        assert retained, 'The actual schema/output lifecycle was never reached'
        for path, journal in retained:
            assert path.is_file(), 'Failure removed the database: '+str(path)
            assert journal.read_bytes() == b'synthetic diagnostic sidecar'
            with closing(original(path.as_uri()+'?mode=ro&immutable=1', uri=True)) as db:
                assert db.execute('SELECT value FROM retention_probe').fetchall() == [('synthetic diagnostic',)]

    return SimpleNamespace(install=install, check=check, error=error)


# Function return arity is deliberately explicit: no guessed adapters/scopes.
PROJECTORS = [
    ('event_legacy_7_1_build', 'project', 2),
    ('event_successor_7_1_build', 'project', 2),
    ('event_filmfest_7_1_build', 'project', 2),
    ('event_native_text_7_1_build', 'project', 3),
    ('event_photo_echo_7_1_build', 'reconstruct', 3),
    ('event_tps_7_1_build', 'project', 2),
    ('event13_7_1_physical', 'projection', 3),
    ('event16_18_7_1_selection', 'project', 3),
    ('native_message_7_1_build', 'project', 3),
    ('native_book_music_7_1_build', 'reconstruct', 3),
    ('native_aux_quest_7_1_build', 'reconstruct', 3),
    ('native21_7_1_build', 'projection', 3),
    ('native26_7_1_physical', 'project', 3),
]


@pytest.mark.parametrize('name,projector,arity', PROJECTORS)
def test_current_projected_builders_preserve_database_and_sidecar(
        tmp_path, monkeypatch, schema_failure, name, projector, arity):
    module = importlib.import_module('genshin_lore.'+name)
    source = tmp_path/'source'
    source.mkdir()
    scope = tmp_path/'scope.json'
    scope.write_text(json.dumps({'files': [], 'accepted_inputs': {}}))
    rows = {table: [] for table in getattr(module, 'TABLES', ())}
    projected = (rows, {}) if arity == 2 else ({}, rows, {})
    monkeypatch.setattr(module, projector, lambda *a, **k: projected)
    if hasattr(module, 'dependency_manifests'):
        dependencies = []
        for index in range(2):
            path = tmp_path/f'dependency-{index}.json'
            path.write_text('{}')
            dependencies.append(path)
        monkeypatch.setattr(module, 'dependency_manifests', lambda *a: dependencies)
    schema_failure.install(module)
    output = tmp_path/'artifact.sqlite'
    arguments = [source, scope]
    if name == 'event_filmfest_7_1_build':
        arguments.append(tmp_path/'synthetic-quest.sqlite')
    if name == 'event16_18_7_1_selection':
        arguments.append('16')
    with pytest.raises(InjectedFailure) as raised:
        module.build(*arguments, output)
    assert raised.value is schema_failure.error
    schema_failure.check()
    assert not (tmp_path/'verified.json').exists()


@pytest.mark.parametrize('kind', ['text', 'lore', 'voice'])
def test_character_output_initialization_preserves_failed_sqlite(
        tmp_path, schema_failure, kind):
    from genshin_lore import character_7_1_successor_build as module
    schema_failure.install(module)
    with pytest.raises(InjectedFailure) as raised:
        module._output(kind, tmp_path/'artifact.sqlite')
    assert raised.value is schema_failure.error
    schema_failure.check()


def test_m3_real_output_initialization_preserves_failed_sqlite(tmp_path, schema_failure):
    from genshin_lore import m3_7_1_provider_build as module
    schema_failure.install(module)
    with pytest.raises(InjectedFailure) as raised:
        module._owned_output(tmp_path/'artifact.sqlite')
    assert raised.value is schema_failure.error
    schema_failure.check()


def test_quest_builder_preserves_failed_private_image(tmp_path, monkeypatch, schema_failure):
    from genshin_lore import quest_talk_7_1_build as module
    scope = tmp_path/'scope.json'
    scope.write_text('{}')
    # Only the fixed-source preflight is substituted. The real constructor,
    # full SQL schema and its failure/finally path execute before source reads.
    monkeypatch.setattr(module, '_preflight', lambda *a: ({}, ['q']*4808, ['t']*59853))
    monkeypatch.setattr(module, '_tree_blobs', lambda *a: {})
    schema_failure.install(module)
    with pytest.raises(InjectedFailure) as raised:
        module.build(tmp_path, scope, tmp_path/'artifact.sqlite')
    assert raised.value is schema_failure.error
    schema_failure.check()


def test_document_builder_preserves_failed_private_image(tmp_path, monkeypatch, schema_failure):
    from genshin_lore import document_7_1_build as module
    scope = {'document_primary_rows': 0, 'document_source_row_counts': {module.LOCALIZATION: 0}}
    monkeypatch.setattr(module, 'qualify', lambda *a: (tmp_path, scope, b'{}', {}, {}))
    monkeypatch.setattr(module, 'array_records', lambda *a: iter(()))
    monkeypatch.setattr(module, 'pairs', lambda *a: iter(()))
    schema_failure.install(module)
    with pytest.raises(InjectedFailure) as raised:
        module.build(tmp_path, tmp_path/'scope.json', tmp_path/'artifact.sqlite')
    assert raised.value is schema_failure.error
    schema_failure.check()


def test_bulk_builder_preserves_failed_database(tmp_path, monkeypatch, schema_failure):
    from genshin_lore import native_bulk_7_1_build as module
    rows = {table: [] for table in module.TABLES}
    monkeypatch.setattr(module, '_qualify', lambda *a: ({'snapshot_id': 'synthetic'}, {}, 'p', 's', 'r'))
    monkeypatch.setattr(module, '_projection', lambda *a: (rows, {}, {}))
    schema_failure.install(module)
    with pytest.raises(InjectedFailure) as raised:
        module.build(tmp_path, tmp_path/'scope.json', tmp_path/'artifact.sqlite')
    assert raised.value is schema_failure.error
    schema_failure.check()


def test_readable_builder_preserves_failed_private_image(tmp_path, monkeypatch, schema_failure):
    from genshin_lore import readable_7_1_build as module
    from scripts.build import seal
    source = tmp_path/'source'
    (source/'Readable').mkdir(parents=True)
    r0 = tmp_path/'r0.sqlite'
    with closing(sqlite3.connect(r0)) as db:
        db.execute('CREATE TABLE source_file(relative_path,source_file_id,source_sha256,snapshot_id,ingest_run_id,root)')
        db.commit()
    document = tmp_path/'document.sqlite'
    with closing(sqlite3.connect(document)) as db:
        db.execute('CREATE TABLE synthetic_dependency(id)')
        db.commit()
    scope = tmp_path/'scope.json'
    scope.write_text(json.dumps({'source_commit': module.COMMIT, 'source_tree': module.TREE,
        'snapshot_id': module.SNAPSHOT, 'readable': {'file_count': 0,
        'path_blob_set_sha256': module.sha(b'[]\n')},
        'r0_dependency': {**seal(r0), 'ingest_run_id': 'synthetic-r0'}}))

    def git(args, **kwargs):
        return module.COMMIT+'\n'+module.TREE+'\n' if 'rev-parse' in args else ''

    monkeypatch.setattr(module, 'subprocess', SimpleNamespace(check_output=git))
    monkeypatch.setattr(module, 'blobs', lambda *a: {})
    schema_failure.install(module)
    with pytest.raises(InjectedFailure) as raised:
        module.build(source, scope, tmp_path/'artifact.sqlite', r0, document,
                     {'artifact': seal(document), 'snapshot_id': module.SNAPSHOT,
                      'contract': 'document-7.1-evidence-v1'})
    assert raised.value is schema_failure.error
    schema_failure.check()


def test_real_m3_final_flush_failure_preserves_database_and_sidecar(tmp_path, monkeypatch):
    from genshin_lore import m3_7_1_provider_build as module
    raw = b'[{"id":1}]'
    family = {'family': 'synthetic', 'source_path': 'rows.json', 'source_physical_rows': 1,
        'key_observation_fields': ['id'], 'v2_text_fields': [], 'v2_textmap_paths': [],
        'consumed_field_observations': {'id': {'occurrences': 1, 'types': {'int': 1}}},
        'source_identity': {'size_bytes': len(raw), 'sha256': module.sha(raw), 'git_blob_oid': 'synthetic'},
        'v1_contract': 'synthetic-v1', 'v2_contract': 'synthetic-v2'}
    contract = tmp_path/'contract.json'
    contract.write_text(json.dumps({'contract': 'm3-7.1-ten-family-provider-input-v1',
        'source_snapshot': module.SNAPSHOT, 'families': [family], 'textmap_sources': {}}))
    monkeypatch.setattr(module, 'qualify_source', lambda *a: {'rows.json': raw})
    error = InjectedFailure('synthetic actual M3 final flush failure')

    def fail(path):
        Path(str(path)+'-journal').write_bytes(b'synthetic final flush diagnostic')
        raise error

    monkeypatch.setattr(module, 'fsync_file', fail)
    output = tmp_path/'artifact.sqlite'
    with pytest.raises(InjectedFailure) as raised:
        module.build(tmp_path, 'synthetic', output, contract)
    assert raised.value is error
    assert Path(str(output)+'-journal').read_bytes() == b'synthetic final flush diagnostic'
    with closing(sqlite3.connect(output.as_uri()+'?mode=ro&immutable=1', uri=True)) as db:
        assert db.execute('SELECT count(*) FROM v1_row').fetchone()[0] == 1


def test_core_migration_failure_preserves_created_database(tmp_path, monkeypatch):
    from genshin_lore import schema20_7_1_candidate as module
    from genshin_lore.db import Database
    provider = tmp_path/'provider.sqlite'
    with closing(sqlite3.connect(provider)) as db:
        db.execute('CREATE TABLE source_file(path,role,size_bytes,sha256,git_blob_oid)')
        db.commit()
    from scripts.build import seal
    metadata = tmp_path/'metadata'
    manifest = metadata/'manifests/m3-7.1-provider/accepted-exact-mappings-v1.json'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({'mappings': [{'family': f'synthetic-{i//2}',
        'candidate_artifact': {**seal(provider), 'path_machine_local': str(provider)}} for i in range(20)]}))
    monkeypatch.setattr(module, 'qualify_source', lambda *a: None)
    error = InjectedFailure('synthetic failure during actual core migration')

    class BrokenMigration(Database):
        def initialize_through(self, version):
            super().initialize_through(1)
            raise error

    monkeypatch.setattr(module, 'Database', BrokenMigration)
    output = tmp_path/'artifact.sqlite'
    with pytest.raises(InjectedFailure) as raised:
        module.build(tmp_path, output, metadata_root=metadata)
    assert raised.value is error
    assert output.is_file()
    with closing(sqlite3.connect(output)) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_publisher_directory_flush_failure_preserves_published_database(tmp_path, monkeypatch):
    output = tmp_path/'artifact.sqlite'
    descriptor, stage = batch_release.create_private_staging(output, prefix='.fixture-')
    batch_release.os.close(descriptor)
    with closing(sqlite3.connect(stage)) as db:
        db.execute('CREATE TABLE diagnostic(value)')
        db.execute("INSERT INTO diagnostic VALUES('synthetic')")
        db.commit()
    before = stage.read_bytes()
    error = InjectedFailure('synthetic directory flush failure')

    def fail(_):
        Path(str(output)+'-journal').write_bytes(b'synthetic publication diagnostic')
        raise error

    monkeypatch.setattr(batch_release, 'sync_directory', fail)
    with pytest.raises(InjectedFailure) as raised:
        batch_release.publish_absent(output, stage)
    assert raised.value is error
    assert output.read_bytes() == before
    assert Path(str(output)+'-journal').read_bytes() == b'synthetic publication diagnostic'
    competitor = tmp_path/'another-stage.sqlite'
    competitor.write_bytes(before)
    with pytest.raises(batch_release.BatchReleaseError):
        batch_release.publish_absent(output, competitor)
    assert output.read_bytes() == before


def test_failed_research_publication_preserves_target_and_failed_receipt(tmp_path, monkeypatch):
    from scripts.builders import research_dataset as module
    original = sqlite3.connect
    retained = []
    error = InjectedFailure('synthetic failure after actual index backup')

    class BrokenBackup(sqlite3.Connection):
        def backup(self, target, **kwargs):
            super().backup(target, **kwargs)
            path = Path(target.execute('PRAGMA database_list').fetchone()[2])
            Path(str(path)+'-journal').write_bytes(b'synthetic backup diagnostic')
            retained.append(path)
            raise error

    def connect(path, *args, **kwargs):
        if str(path) == ':memory:':
            kwargs['factory'] = BrokenBackup
        return original(path, *args, **kwargs)

    monkeypatch.setattr(module, 'sqlite3', SimpleNamespace(**{**vars(sqlite3), 'connect': connect}))
    pipeline = runpy.run_path(str(Path(__file__).with_name('test_research_pipeline.py')))['pipeline']
    with pytest.raises(InjectedFailure) as raised:
        pipeline(tmp_path/'stage')
    assert raised.value is error
    assert len(retained) == 1 and retained[0].is_file()
    assert Path(str(retained[0])+'-journal').read_bytes() == b'synthetic backup diagnostic'
    directory = tmp_path/'stage/research_index'
    assert list(directory.glob('failure-*.json'))
    assert not (directory/'verified.json').exists()
    with closing(original(retained[0].as_uri()+'?mode=ro&immutable=1', uri=True)) as db:
        assert db.execute('SELECT count(*) FROM research_text').fetchone()[0] == 2
