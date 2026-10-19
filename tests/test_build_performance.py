"""Scheduling/publication equivalence; synthetic text only, no speed assertions."""
import hashlib
import json
from contextlib import closing
import sqlite3
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.builders import semantic
from scripts.builders.research_dataset import publish_compacted_index


def vector(text):
    result = np.zeros(384, dtype=np.float32)
    result[hashlib.sha256(text.encode()).digest()[0]] = 1
    return result


def test_length_batches_restore_source_ids_without_deduplicating():
    inputs = ["合成" * (i % 37 + 1) for i in range(129)]
    original = inputs.copy()
    seen = []

    class Model:
        def embed(self, texts, batch_size):
            seen.extend(texts)
            return map(vector, texts)

    actual = np.empty((len(inputs), 384), dtype=np.float32)
    progress = []
    semantic.embed_windows(Model(), inputs, actual, progress=progress.append)
    assert inputs == original
    assert seen == sorted(inputs, key=len)
    np.testing.assert_array_equal(actual, np.asarray([vector(text) for text in original]))
    assert len(seen) == 129 and progress == [64, 128, 129]


@pytest.mark.parametrize('bad', [np.zeros((2, 384)), np.full((2, 384), np.nan),
                                 np.full((2, 384), np.inf), np.ones((1, 384)),
                                 np.ones((2, 383))])
def test_scheduling_does_not_relax_vector_validation(bad):
    model = SimpleNamespace(embed=lambda *args, **kwargs: bad)
    with pytest.raises(ValueError):
        semantic.embed_windows(model, ['合成甲', '合成乙'], np.empty((2, 384)))


@pytest.mark.parametrize('platform,cpus,expected', [('linux', 1, 1), ('linux', 8, 4),
                                                  ('win32', 8, 2), ('darwin', 8, 2)])
def test_build_threads_leave_windows_default_unchanged(monkeypatch, platform, cpus, expected):
    monkeypatch.setattr(semantic, 'sys', SimpleNamespace(platform=platform))
    monkeypatch.setattr(semantic, 'os', SimpleNamespace(sched_getaffinity=lambda _: range(cpus)))
    assert semantic.default_embedding_threads() == expected


@pytest.mark.parametrize('threads', [0, -1, True, '4'])
def test_bad_thread_count_rejected_before_output(tmp_path, threads):
    output = tmp_path/'absent'
    with pytest.raises(ValueError, match='positive integer'):
        semantic.build(tmp_path/'index', output, tmp_path/'cache', scope_path=tmp_path/'scope',
                       available_allocated_bytes=3_000_000_000, embedding_threads=threads)
    assert not output.exists()


class TrackedConnection(sqlite3.Connection):
    def execute(self, sql, *args, **kwargs):
        if sql.strip().upper() == 'VACUUM':
            raise AssertionError('a second full in-memory image must not be created')
        return super().execute(sql, *args, **kwargs)


def memory_database():
    db = sqlite3.connect(':memory:', factory=TrackedConnection)
    db.execute('CREATE TABLE evidence(id INTEGER PRIMARY KEY, text, observation)')
    db.executemany('INSERT INTO evidence VALUES(?,?,?)', [(i, '合成正文' * 20, i % 3) for i in range(500)])
    db.execute("CREATE VIRTUAL TABLE text_fts USING fts5(text, tokenize='trigram')")
    db.execute('INSERT INTO text_fts(rowid,text) SELECT id,text FROM evidence')
    db.commit()
    return db


def test_direct_compaction_preserves_rows_and_fts_without_second_ram_image(tmp_path):
    db = memory_database()
    output = tmp_path/'index #1.sqlite'
    try:
        before = db.execute('SELECT * FROM evidence ORDER BY id').fetchall()
        capacity = publish_compacted_index(db, output, 1_000_000_000, tmp_path)
        with closing(sqlite3.connect(output)) as actual:
            assert actual.execute('SELECT * FROM evidence ORDER BY id').fetchall() == before
            assert actual.execute("SELECT count(*) FROM text_fts WHERE text_fts MATCH '合成正文'").fetchone()[0] == 500
            assert actual.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('SELECT * FROM evidence ORDER BY id').fetchall() == before
        assert capacity['publication_method'] == 'vacuum_into'
        assert capacity['index_after_compaction_bytes'] == output.stat().st_size
        sealed = output.read_bytes()
        with pytest.raises(FileExistsError):
            publish_compacted_index(db, output, 1_000_000_000, tmp_path)
        assert output.read_bytes() == sealed
    finally:
        db.close()


def test_publication_budget_rejection_does_not_create_output(tmp_path):
    db = memory_database()
    output = tmp_path/'absent.sqlite'
    try:
        with pytest.raises(RuntimeError, match='hard storage gate'):
            publish_compacted_index(db, output, 1, tmp_path)
        assert not output.exists()
        assert json.loads((tmp_path/'index-capacity-absent.json').read_text())['fits'] is False
    finally:
        db.close()


def test_failed_publication_removes_only_owned_output(monkeypatch, tmp_path):
    import scripts.builders.research_dataset as builder
    db = memory_database()
    output = tmp_path/'failed.sqlite'
    other = tmp_path/'unrelated'
    other.write_text('preserve')
    def failed_sync(*args):
        raise OSError('synthetic durability failure')
    monkeypatch.setattr(builder.os, 'fsync', failed_sync)
    try:
        with pytest.raises(OSError, match='synthetic durability'):
            publish_compacted_index(db, output, 1_000_000_000, tmp_path)
        assert not output.exists() and other.read_text() == 'preserve'
        assert db.execute('SELECT count(*) FROM evidence').fetchone()[0] == 500
    finally:
        db.close()
