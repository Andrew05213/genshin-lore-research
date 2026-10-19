"""Inference scheduling equivalence; synthetic text only, no speed assertions."""
import hashlib
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.builders import semantic


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


