"""Inference reuse saves work without merging any evidence or vector IDs."""

import sqlite3
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.builders import semantic as builder
from scripts.validators.semantic import verify
from test_semantic_build import (
    FixtureEmbedding,
    FixtureIndex,
    build_fixture,
    inputs,
    synthetic_model,
)


class ObservedEmbedding(FixtureEmbedding):
    def __init__(self, **kwargs):
        self.calls = []

    def embed(self, texts, **kwargs):
        self.calls.append(list(texts))
        return super().embed(texts, **kwargs)


def test_exact_text_reuse_scatters_to_original_memmap_rows(tmp_path):
    texts = ["长合成" * 7, "短", "中合成", "短", "长合成" * 7, "中合成"]
    model = ObservedEmbedding()
    target = tmp_path / "向量 #窗口.npy"
    vectors = np.lib.format.open_memmap(
        target, mode="w+", dtype="float32", shape=(6, 384)
    )
    progress = []
    result = builder.embed_windows(
        model, texts, vectors, batch_size=2,
        progress=lambda *args: progress.append(args),
    )
    vectors.flush()
    del vectors
    actual = np.load(target)
    expected = np.asarray(list(FixtureEmbedding().embed(texts)))
    np.testing.assert_array_equal(actual, expected)
    assert model.calls == [["短", "中合成"], ["长合成" * 7]]
    assert progress == [(0, 4, 2, 3), (1, 6, 3, 3)]
    assert result == {"unique_inference_windows": 3, "reused_occurrence_windows": 3}


def test_only_exact_text_reuses_including_hash_collisions():
    class EqualHashText(str):
        def __hash__(self):
            return 1

    texts = list(map(
        EqualHashText, ["合成", "合成 ", " 合成", "合成\r\n", "合成\n", "合成"]
    ))
    model = ObservedEmbedding()
    output = np.empty((len(texts), 384), dtype="float32")
    result = builder.embed_windows(model, texts, output)
    assert sum(map(len, model.calls)) == 5
    assert result["reused_occurrence_windows"] == 1
    np.testing.assert_array_equal(
        output, np.asarray(list(FixtureEmbedding().embed(texts)))
    )


def test_reuse_is_local_to_each_inference_call():
    model = ObservedEmbedding()
    texts = ["合成本轮", "合成本轮"]
    for _ in range(2):
        result = builder.embed_windows(
            model, texts, np.empty((2, 384), dtype="float32")
        )
        assert result["unique_inference_windows"] == 1
    assert model.calls == [["合成本轮"], ["合成本轮"]]


def test_reuse_batches_unique_texts_and_keeps_all_occurrences():
    unique = ["合成窗口" + str(number) for number in range(130)]
    texts = unique + unique[::-1] + unique
    model = ObservedEmbedding()
    output = np.empty((len(texts), 384), dtype="float32")
    result = builder.embed_windows(model, texts, output)
    assert [len(batch) for batch in model.calls] == [64, 64, 2]
    assert result == {"unique_inference_windows": 130, "reused_occurrence_windows": 260}
    np.testing.assert_array_equal(
        output, np.asarray(list(FixtureEmbedding().embed(texts)))
    )


def test_builder_keeps_duplicate_source_identities_and_ann_order(
    tmp_path, synthetic_model, monkeypatch
):
    inferred = []
    ann_ids = []

    class Embedding(FixtureEmbedding):
        def embed(self, texts, **kwargs):
            inferred.extend(texts)
            return super().embed(texts, **kwargs)

    class Index(FixtureIndex):
        def add_items(self, values, ids):
            ann_ids.extend(map(int, ids))
            return super().add_items(values, ids)

    monkeypatch.setitem(
        sys.modules, "fastembed", SimpleNamespace(TextEmbedding=Embedding)
    )
    monkeypatch.setitem(sys.modules, "hnswlib", SimpleNamespace(Index=Index))
    index, output, report = build_fixture(tmp_path)
    assert len(inferred) == len(set(inferred)) == 2
    assert ann_ids == [0, 1, 2, 3]
    with sqlite3.connect(output / "metadata.sqlite") as db:
        records = db.execute(
            "SELECT vector_id,research_text_id,evidence_ref,window_start,window_end,"
            "source_identity_json FROM semantic_occurrence ORDER BY vector_id"
        ).fetchall()
    assert [row[0] for row in records] == [0, 1, 2, 3]
    assert {row[1] for row in records} == {1, 2}
    assert records[0][2] != records[2][2]
    assert records[0][3:5] == records[2][3:5]
    assert records[0][5] != records[2][5]
    vectors = np.load(output / "vectors.npy")
    np.testing.assert_array_equal(vectors[:2], vectors[2:])
    assert report["embedding_batching"]["unique_inference_windows"] == 2
    checked = verify(index, output)
    assert checked["status"] == "PASS"
    assert checked["physical_occurrences"] == 2
    assert checked["full_mapping_rows"] == 4


@pytest.mark.parametrize(
    "malformation", ["narrow", "missing", "extra", "nan", "infinite", "zero"]
)
def test_malformed_unique_embedding_is_rejected_without_success_report(
    tmp_path, synthetic_model, monkeypatch, malformation
):
    class MalformedEmbedding(FixtureEmbedding):
        def embed(self, texts, **kwargs):
            result = np.asarray(list(super().embed(texts, **kwargs)))
            if malformation == "narrow":
                return result[:, :-1]
            if malformation == "missing":
                return result[:-1]
            if malformation == "extra":
                return np.concatenate((result, result[:1]))
            result[0] = {"nan": np.nan, "infinite": np.inf, "zero": 0}[malformation]
            return result

    monkeypatch.setitem(
        sys.modules, "fastembed", SimpleNamespace(TextEmbedding=MalformedEmbedding)
    )
    index, cache, scope = inputs(tmp_path)
    output = tmp_path / "失败现场"
    with pytest.raises(ValueError, match="zero vector|embedding shape/finite violation"):
        builder.build(
            index, output, cache, scope_path=scope,
            available_allocated_bytes=3_000_000_000,
        )
    assert (output / "scope.json").is_file()
    assert (output / "metadata.sqlite").is_file()
    assert (output / "vectors.npy").is_file()
    assert not (output / "build.json").exists()
    assert not (output / "verified.json").exists()
