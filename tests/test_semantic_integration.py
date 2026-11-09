"""Combined Linux scheduling, inference reuse and portable ANN transport."""
import sqlite3
import sys
from collections import Counter
from types import SimpleNamespace

import numpy as np
import pytest

from genshin_lore import hnsw_io, semantic_discovery
from genshin_lore.semantic_discovery import SemanticDiscovery, sha256_file
from scripts.builders import semantic as builder
from scripts.validators.semantic import verify
from test_semantic_build import FixtureEmbedding, FixtureIndex, inputs, synthetic_model


@pytest.fixture
def sealed_model_cache(tmp_path):
    cache = tmp_path / "模型 cache"
    model = cache / "model"
    (model / "nested").mkdir(parents=True)
    (model / "Z-weights.bin").write_bytes(b"synthetic weights")
    (model / "nested" / "config.json").write_bytes(b"{}")
    (cache / "receipt.json").write_bytes(b"outside model directory")
    return cache, {
        "path_relative": "model",
        "cache_tree_sha256": semantic_discovery.tree_sha256(cache),
        "tree_sha256": semantic_discovery.tree_sha256(model),
    }


def test_model_cache_verification_hashes_overlapping_files_once(sealed_model_cache, monkeypatch):
    cache, contract = sealed_model_cache
    calls = Counter()

    def count_hash(path):
        calls[path.relative_to(cache).as_posix()] += 1
        return sha256_file(path)

    monkeypatch.setattr(semantic_discovery, "sha256_file", count_hash)
    for expected_reads in (1, 2):
        path, _ = semantic_discovery._verify_model_cache(cache, contract)
        assert path == (cache / "model").resolve()
        assert calls == {
            "model/Z-weights.bin": expected_reads,
            "model/nested/config.json": expected_reads,
            "receipt.json": expected_reads,
        }


@pytest.mark.parametrize("field, message", [
    ("cache_tree_sha256", "semantic model identity mismatch"),
    ("tree_sha256", "semantic model files identity mismatch"),
])
def test_model_cache_verification_checks_both_seals(sealed_model_cache, field, message):
    cache, contract = sealed_model_cache
    contract[field] = "0" * 64
    with pytest.raises(semantic_discovery.SemanticUnavailable, match=message):
        semantic_discovery._verify_model_cache(cache, contract)


def test_model_cache_drift_after_read_is_rejected(sealed_model_cache, monkeypatch):
    cache, contract = sealed_model_cache

    def change_weight_after_hash(path):
        result = sha256_file(path)
        if path.name == "Z-weights.bin":
            path.write_bytes(b"changed after verification began")
        return result

    monkeypatch.setattr(semantic_discovery, "sha256_file", change_weight_after_hash)
    with pytest.raises(semantic_discovery.SemanticUnavailable, match="changed during verification"):
        semantic_discovery._verify_model_cache(cache, contract)


@pytest.mark.parametrize('platform,expected_threads', [('linux', 4), ('win32', 2)])
def test_threads_reuse_unicode_transport_and_source_ids_compose(
        tmp_path, monkeypatch, synthetic_model, platform, expected_threads):
    observed_threads, inferred, inserted, saved = [], [], [], []

    class Embedding(FixtureEmbedding):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
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
