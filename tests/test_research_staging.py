"""Real synthetic Research disk publication and retained failure scenes."""
import json
from pathlib import Path
import sqlite3

import pytest

from scripts.builders import research_dataset as builder
from scripts.builders import research_staging as staging
from scripts.build import seal
from test_pr1_review import small_index


@pytest.fixture
def inputs(tmp_path):
    index, manifest, bindings = small_index(tmp_path)
    index.close()
    manifest['corpus_ref'] = 'synthetic-staging'
    corpus = tmp_path/'metadata/manifests/research/corpus.json'
    corpus.parent.mkdir(parents=True)
    corpus.write_text(json.dumps(manifest), encoding='utf-8')
    return manifest, bindings, corpus


def run(inputs, tmp_path, **kwargs):
    manifest, bindings, corpus = inputs
    return builder.build(manifest, bindings, tmp_path/'中文 output/artifact.sqlite',
        kwargs.pop('budget', 100_000_000), corpus_path=corpus,
        reports_dir=tmp_path/'reports', **kwargs)


def retained(tmp_path):
    paths = list((tmp_path/'中文 output').glob('.research-*.sqlite'))
    assert len(paths) == 1
    return paths[0]


def events(tmp_path):
    return [json.loads(line) for line in
            (tmp_path/'reports/index-staging-artifact.jsonl').read_text(encoding='utf-8').splitlines()]


def test_real_disk_build_compacts_verifies_and_publishes(inputs, tmp_path):
    before = {p: seal(Path(p)) for p in inputs[1]['artifacts'].values()}
    result = run(inputs, tmp_path)
    artifact = tmp_path/'中文 output/artifact.sqlite'
    assert result['sha256'] == seal(artifact)['sha256']
    assert result['text_rows'] == 1
    assert not list(artifact.parent.glob('.research-*.sqlite'))
    assert [v['phase'] for v in events(tmp_path)].index('oracle_complete') < [
        v['phase'] for v in events(tmp_path)].index('vacuum_start')
    assert json.loads((tmp_path/'reports/index-memory-oracle-artifact.json').read_text())['texts'] == 1
    assert json.loads((tmp_path/'reports/index-post-vacuum-oracle-artifact.json').read_text())['texts'] == 1
    assert all(seal(Path(p)) == s for p, s in before.items())


def test_bulk_load_retains_unique_constraints_and_builds_identical_final_schema(inputs,tmp_path,monkeypatch):
    original=builder.project_component
    def project(src,out,*args,**kwargs):
        assert not out.execute("SELECT 1 FROM sqlite_master WHERE name='research_text_scope'").fetchone()
        result=original(src,out,*args,**kwargs)
        with pytest.raises(sqlite3.IntegrityError):
            out.execute('INSERT INTO research_text SELECT * FROM research_text')
        # A fresh primary key must still fail the physical-occurrence UNIQUE
        # constraint, while the ordinary query indexes are not yet present.
        row=list(out.execute('SELECT * FROM research_text LIMIT 1').fetchone())
        row[0]+=1_000_000
        with pytest.raises(sqlite3.IntegrityError):
            out.execute('INSERT INTO research_text VALUES('+','.join('?' for _ in row)+')',row)
        return result
    monkeypatch.setattr(builder,'project_component',project)
    run(inputs,tmp_path)
    with sqlite3.connect(tmp_path/'中文 output/artifact.sqlite') as db, sqlite3.connect(':memory:') as expected:
        expected.executescript(builder.old.SCHEMA)
        query="SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        assert db.execute(query).fetchall()==expected.execute(query).fetchall()
    phases=[v['phase'] for v in events(tmp_path)]
    assert phases.index('query_indexes_complete')<phases.index('oracle_start')


def test_vacuum_failure_keeps_oracle_phase_database_and_original_error(inputs, tmp_path, monkeypatch):
    def fail(db):
        assert db.execute('SELECT COUNT(*) FROM research_text').fetchone()[0] == 1
        assert (tmp_path/'reports/index-memory-oracle-artifact.json').is_file()
        raise MemoryError('synthetic vacuum failure')
    monkeypatch.setattr(staging, 'compact', fail)
    with pytest.raises(MemoryError, match='synthetic vacuum'):
        run(inputs, tmp_path)
    db = sqlite3.connect(retained(tmp_path))
    try:
        assert db.execute('SELECT raw_text FROM research_text').fetchone()[0] == 'synthetic text'
    finally:
        db.close()
    assert not (tmp_path/'中文 output/artifact.sqlite').exists()
    assert events(tmp_path)[-1]['error_type'] == 'MemoryError'
    assert events(tmp_path)[-1]['failed_phase'] == 'vacuum'
    assert not (tmp_path/'reports/index-post-vacuum-oracle-artifact.json').exists()


@pytest.mark.parametrize('suffix', ['-wal', '-shm', '-journal'])
def test_projection_failure_retains_database_and_existing_sidecar(inputs, tmp_path, monkeypatch, suffix):
    original = builder.project_component
    def fail(src, out, *args, **kwargs):
        result = original(src, out, *args, **kwargs)
        out.commit()
        path = Path(out.execute('PRAGMA database_list').fetchone()[2])
        Path(str(path)+suffix).write_bytes(b'synthetic diagnostic sidecar')
        raise ValueError('synthetic projection failure')
    monkeypatch.setattr(builder, 'project_component', fail)
    with pytest.raises(ValueError, match='projection failure'):
        run(inputs, tmp_path)
    path = retained(tmp_path)
    assert path.stat().st_size > 0
    assert Path(str(path)+suffix).read_bytes() == b'synthetic diagnostic sidecar'
    assert not (path.parent/'artifact.sqlite').exists()


@pytest.mark.parametrize('which', ['before', 'after'])
def test_oracle_failures_never_publish_and_keep_database(inputs, tmp_path, monkeypatch, which):
    original = builder.run_oracle
    calls = []
    def fail(*args):
        calls.append(1)
        if which == 'before' or len(calls) == 2:
            raise ValueError('synthetic oracle rejection')
        return original(*args)
    monkeypatch.setattr(builder, 'run_oracle', fail)
    with pytest.raises(ValueError, match='oracle rejection'):
        run(inputs, tmp_path)
    assert retained(tmp_path).stat().st_size > 0
    assert not (tmp_path/'中文 output/artifact.sqlite').exists()
    assert (tmp_path/'reports/index-memory-oracle-artifact.json').exists() == (which == 'after')


@pytest.mark.parametrize('point', ['flush', 'rename', 'after_rename'])
def test_publication_failures_preserve_private_or_published_database(inputs, tmp_path, monkeypatch, point):
    original = staging.publish_absent
    def fail(*args, **kwargs):
        if point == 'after_rename':
            original(*args, **kwargs)
        raise OSError('synthetic publication failure')
    if point == 'flush':
        monkeypatch.setattr(staging, 'fsync_file', fail)
    else:
        monkeypatch.setattr(staging, 'publish_absent', fail)
    with pytest.raises(OSError, match='publication failure'):
        run(inputs, tmp_path)
    artifact = tmp_path/'中文 output/artifact.sqlite'
    path = artifact if point == 'after_rename' else retained(tmp_path)
    assert path.stat().st_size > 0
    assert events(tmp_path)[-1]['error_type'] == 'OSError'


@pytest.mark.parametrize('suffix', ['', '-wal', '-shm', '-journal'])
def test_existing_target_or_sidecar_is_untouched(inputs, tmp_path, suffix):
    target = tmp_path/'中文 output/artifact.sqlite'
    target.parent.mkdir()
    owned = Path(str(target)+suffix)
    owned.write_bytes(b'old failure must remain')
    with pytest.raises((FileExistsError, ValueError)):
        run(inputs, tmp_path)
    assert owned.read_bytes() == b'old failure must remain'
    assert not list(target.parent.glob('.research-*.sqlite'))


@pytest.mark.parametrize('budget', [0, -1, 1, 16_777_216])
def test_insufficient_budget_rejects_before_creating_database(inputs, tmp_path, budget):
    with pytest.raises(ValueError, match='budget'):
        run(inputs, tmp_path, budget=budget)
    assert not list(tmp_path.rglob('.research-*.sqlite'))
    assert not (tmp_path/'中文 output/artifact.sqlite').exists()


def test_bounded_connection_uses_disk_file_temp_and_cache_cap(tmp_path):
    path = tmp_path/'中文.sqlite'
    db = staging.connect(path, 100_000_000)
    try:
        assert Path(db.execute('PRAGMA database_list').fetchone()[2]) == path
        assert db.execute('PRAGMA temp_store').fetchone()[0] == 1
        assert db.execute('PRAGMA cache_size').fetchone()[0] == -staging.CACHE_KIB
        assert db.execute('PRAGMA temp.cache_size').fetchone()[0] == -staging.TEMP_CACHE_KIB
        assert db.execute('PRAGMA mmap_size').fetchone()[0] == 0
        assert db.execute('PRAGMA max_page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0] <= (100_000_000-staging.RESERVE_BYTES)//2
    finally:
        db.close()


def test_compaction_capacity_rejection_retains_early_oracle(inputs, tmp_path, monkeypatch):
    def fail(*args):
        raise ValueError('synthetic compaction budget exhausted')
    monkeypatch.setattr(staging, 'reserve_compaction', fail)
    with pytest.raises(ValueError, match='budget exhausted'):
        run(inputs, tmp_path)
    assert retained(tmp_path).stat().st_size > 0
    assert (tmp_path/'reports/index-memory-oracle-artifact.json').exists()


def test_page_limit_stops_real_large_text_before_publication(inputs, tmp_path):
    source = Path(inputs[1]['artifacts']['fixture'])
    with sqlite3.connect(source) as db:
        db.execute('UPDATE loading_tip_text SET raw_text=?', ('合成'*1_000_000,))
    inputs[0]['components'][0]['artifact'] = seal(source)
    with pytest.raises(sqlite3.OperationalError, match='full'):
        run(inputs, tmp_path, budget=18*1024**2)
    path = retained(tmp_path)
    assert path.stat().st_size <= (18*1024**2-staging.RESERVE_BYTES)//2
    assert not (path.parent/'artifact.sqlite').exists()
    assert events(tmp_path)[-1]['failed_phase'] == 'projection'


def test_low_temp_volume_space_blocks_compaction_keeps_early_oracle(inputs, tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(staging.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
    with pytest.raises(ValueError, match='volume space'):
        run(inputs, tmp_path)
    assert retained(tmp_path).is_file()
    assert (tmp_path/'reports/index-memory-oracle-artifact.json').exists()


def test_original_exception_survives_diagnostic_failure(inputs, tmp_path, monkeypatch):
    def fail(_):
        raise MemoryError('original synthetic failure')
    original_record = staging.Diagnostics.record
    def record(self, phase, **details):
        if phase == 'failed':
            raise OSError('secondary diagnostic failure')
        original_record(self, phase, **details)
    monkeypatch.setattr(staging, 'compact', fail)
    monkeypatch.setattr(staging.Diagnostics, 'record', record)
    with pytest.raises(MemoryError, match='original synthetic') as result:
        run(inputs, tmp_path)
    assert 'secondary diagnostic failure' in str(result.value.__notes__)
    assert retained(tmp_path).is_file()


def test_existing_source_target_alias_rejects_without_changing_input(inputs, tmp_path):
    source = Path(inputs[1]['artifacts']['fixture'])
    before = seal(source)
    with pytest.raises(FileExistsError):
        builder.build(inputs[0], inputs[1], source, 100_000_000,
                      corpus_path=inputs[2], reports_dir=tmp_path/'reports')
    assert seal(source) == before


def test_compaction_reservation_rejects_unbudgeted_rollback_journal(tmp_path):
    db = staging.connect(tmp_path/'stage.sqlite', 100_000_000)
    try:
        db.execute('CREATE TABLE synthetic(id)')
        db.commit()
        assert db.execute('PRAGMA journal_mode=DELETE').fetchone()[0] == 'delete'
        with pytest.raises(ValueError, match='journal policy'):
            staging.reserve_compaction(db, tmp_path/'stage.sqlite', 100_000_000)
    finally:
        db.close()


def test_original_failure_survives_file_stat_and_close_diagnostic_errors(inputs, tmp_path, monkeypatch):
    def fail(_):
        raise MemoryError('original memory failure')
    def unavailable(*args):
        raise PermissionError('synthetic failed observation')
    monkeypatch.setattr(staging, 'compact', fail)
    monkeypatch.setattr(staging, 'scene', unavailable)
    original_close = staging.Diagnostics.close
    def close(self):
        original_close(self)
        raise OSError('synthetic close failure')
    monkeypatch.setattr(staging.Diagnostics, 'close', close)
    with pytest.raises(MemoryError, match='original memory failure') as result:
        run(inputs, tmp_path)
    assert 'synthetic failed observation' in str(result.value.__notes__)
    assert 'synthetic close failure' in str(result.value.__notes__)
    assert retained(tmp_path).is_file()
