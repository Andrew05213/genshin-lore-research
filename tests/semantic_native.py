"""Explicit semantic-extra tests: python -m pytest tests/semantic_native.py.

Real native ANN/file I/O and full independent oracle; synthetic embeddings only.
Kept explicit so the base (non-semantic) developer install needs no native extra.
"""
import sys
from types import SimpleNamespace

import hnswlib
import pytest

from genshin_lore.semantic_discovery import SemanticDiscovery, SemanticUnavailable, sha256_file
from test_semantic_build import (
    FixtureEmbedding, build_fixture, builder,
    test_independent_oracle_rejects_resealed_missing_source_window as missing_window_check,
    test_source_and_vector_tampering_fail_closed as tampering_check,
)
from scripts.validators.semantic import verify


@pytest.fixture
def native_model(monkeypatch):
    # Do not replace hnswlib, its file operations, vector checks or source oracle.
    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=FixtureEmbedding))
    monkeypatch.setattr(builder, "check_model", lambda _: None)


@pytest.mark.parametrize("directory", ["中文目录", "空 格#百分%", "路径😀"])
def test_native_builder_oracle_runtime_unicode(tmp_path, native_model, directory):
    work = tmp_path / directory
    work.mkdir()
    index, output, report = build_fixture(work)
    assert (output / "index.hnsw").stat().st_size > 0
    assert verify(index, output)["status"] == "PASS"
    assert not (output / "verified.json").exists()
    runtime = SemanticDiscovery(report["config"], {
        "semantic_discovery": {"metadata": str(output / "metadata.sqlite"),
                               "hnsw": str(output / "index.hnsw"),
                               "model_cache": str(work / "cache")}},
        "synthetic-corpus", sha256_file(index))
    try:
        assert {h["research_text_id"] for h in runtime.candidates("合成文本")} == {1, 2}
    finally:
        runtime.close()


def test_native_oracle_rejects_resealed_missing_window(tmp_path, native_model):
    missing_window_check(tmp_path, native_model)


def test_native_oracle_rejects_source_and_vector_tampering(tmp_path, native_model):
    tampering_check(tmp_path, native_model)


def test_native_loader_and_runtime_reject_resealed_truncated_binary(tmp_path, native_model):
    index, output, report = build_fixture(tmp_path)
    ann_path = output / "index.hnsw"
    content = ann_path.read_bytes()
    ann_path.write_bytes(content[:len(content) // 2])
    report["config"]["artifacts"]["hnsw"] = {
        "size_bytes": ann_path.stat().st_size, "sha256": sha256_file(ann_path)}
    import json
    (output / "build.json").write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(RuntimeError, match="corrupted|unsupported"):
        verify(index, output)
    with pytest.raises(SemanticUnavailable, match="HNSW load failed"):
        SemanticDiscovery(report["config"], {
            "semantic_discovery": {"metadata": str(output / "metadata.sqlite"),
                                   "hnsw": str(ann_path), "model_cache": str(tmp_path / "cache")}},
            "synthetic-corpus", sha256_file(index))
    assert ann_path.read_bytes() == content[:len(content) // 2]
    assert not (output / "verified.json").exists()


def test_silent_native_save_never_creates_success_report(tmp_path, native_model, monkeypatch):
    from test_semantic_build import inputs
    real_index = hnswlib.Index

    class SilentSave:
        def __init__(self, **kwargs):
            self.index = real_index(**kwargs)

        def __getattr__(self, name):
            return getattr(self.index, name)

        def save_index(self, path):
            pass

    monkeypatch.setattr(hnswlib, "Index", SilentSave)
    index, cache, scope = inputs(tmp_path)
    output = tmp_path / "保存失败现场"
    with pytest.raises(OSError, match="complete native binary"):
        builder.build(index, output, cache, scope_path=scope, available_allocated_bytes=3000000000)
    assert (output / "metadata.sqlite").exists()
    assert (output / "vectors.npy").exists()
    assert not (output / "build.json").exists()
    assert not (output / "verified.json").exists()
