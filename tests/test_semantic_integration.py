"""Combined Linux scheduling, inference reuse and portable ANN transport."""
import sqlite3
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from genshin_lore import hnsw_io
from genshin_lore.semantic_discovery import SemanticDiscovery, sha256_file
from scripts.builders import semantic as builder
from scripts.validators.semantic import verify
from test_semantic_build import FixtureEmbedding, FixtureIndex, inputs, synthetic_model


@pytest.mark.parametrize('platform,expected_threads', [('linux', 4), ('win32', 2)])
def test_threads_reuse_unicode_transport_and_source_ids_compose(
        tmp_path, monkeypatch, synthetic_model, platform, expected_threads):
    observed_threads, inferred, inserted, saved = [], [], [], []

    class Embedding(FixtureEmbedding):
        def __init__(self, **kwargs):
            observed_threads.append(kwargs['threads'])

        def embed(self, texts, **kwargs):
            inferred.extend(texts)
            return super().embed(texts, **kwargs)

    class Index(FixtureIndex):
        def add_items(self, values, ids):
            inserted.extend(map(int, ids))
            return super().add_items(values, ids)

        def save_index(self, path):
            assert path.isascii()
            saved.append(path)
            return super().save_index(path)

    monkeypatch.setitem(sys.modules, 'fastembed', SimpleNamespace(TextEmbedding=Embedding))
    monkeypatch.setitem(sys.modules, 'hnswlib', SimpleNamespace(Index=Index))
    monkeypatch.setattr(builder, 'sys', SimpleNamespace(platform=platform))
    monkeypatch.setattr(builder, 'os', SimpleNamespace(sched_getaffinity=lambda _: range(8)))
    monkeypatch.setattr(hnsw_io, '_WINDOWS', True)
    index, cache, scope = inputs(tmp_path)
    output = tmp_path/'中文 output #1'
    report = builder.build(index, output, cache, scope_path=scope,
                           available_allocated_bytes=3_000_000_000)

    assert observed_threads == [expected_threads]
    assert report['build_parameters']['embedding_threads'] == expected_threads
    assert report['config']['model']['threads'] == 2  # query budget is unchanged
    assert len(inferred) == len(set(inferred)) == 2
    assert list(map(len, inferred)) == sorted(map(len, inferred))
    assert report['embedding_batching']['reused_occurrence_windows'] == 2
    assert inserted == [0, 1, 2, 3] and len(saved) == 1
    vectors = np.load(output/'vectors.npy')
    np.testing.assert_array_equal(vectors[:2], vectors[2:])
    checked = verify(index, output)
    assert checked['status'] == 'PASS' and checked['full_mapping_rows'] == 4
    with sqlite3.connect(output/'metadata.sqlite') as db:
        refs = {row[0] for row in db.execute('SELECT evidence_ref FROM semantic_occurrence')}
    assert len(refs) == 2  # duplicate source records retain different exact refs

    runtime = SemanticDiscovery(report['config'], {'semantic_discovery': {
        'metadata': str(output/'metadata.sqlite'), 'hnsw': str(output/'index.hnsw'),
        'model_cache': str(cache)}}, 'synthetic-corpus', sha256_file(index))
    try:
        assert {row['research_text_id'] for row in runtime.candidates('合成文本')} == {1, 2}
    finally:
        runtime.close()
    assert observed_threads[-1] == 2
    assert not (output/'verified.json').exists()  # test is not formal acceptance
