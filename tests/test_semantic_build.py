"""Synthetic vectors exercise delivery/oracles, not multilingual model quality."""

import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from genshin_lore import research_index
from genshin_lore.semantic_discovery import sha256_file
from scripts.builders import semantic as builder
from scripts.validators.semantic import verify
from scripts.build import Rebuild, STAGES


class FixtureIndex:
    def __init__(self, **kwargs):
        self.values = {}

    def init_index(self, **kwargs):
        pass

    def set_num_threads(self, *args):
        pass

    def set_ef(self, *args):
        pass

    def add_items(self, values, ids):
        self.values.update(zip(map(int, ids), values))

    def get_current_count(self):
        return len(self.values)

    def get_items(self, ids):
        return np.asarray([self.values[int(i)] for i in ids])

    def knn_query(self, vector, k):
        distances = [
            (1 - float(np.dot(value, vector)), key)
            for key, value in self.values.items()
        ]
        ordered = sorted(distances)[:k]
        return np.asarray([[key for _, key in ordered]]), np.asarray(
            [[d for d, _ in ordered]]
        )

    def save_index(self, path):
        with open(path, "wb") as stream:
            np.save(stream, self.get_items(range(len(self.values))))

    def index_file_size(self):
        stream = io.BytesIO()
        np.save(stream, self.get_items(range(len(self.values))))
        return len(stream.getvalue())

    def load_index(self, path, **kwargs):
        with open(path, "rb") as stream:
            self.values = dict(enumerate(np.load(stream)))


class FixtureEmbedding:
    def __init__(self, **kwargs):
        self.model = self

    def tokenize(self, texts):
        return [SimpleNamespace(attention_mask=[1] * len(text)) for text in texts]

    def embed(self, texts, **kwargs):
        for text in texts:
            vector = np.zeros(384, dtype=np.float32)
            vector[hashlib.sha256(text.encode()).digest()[0]] = 1
            yield vector

    def query_embed(self, texts, **kwargs):
        return self.embed(texts, **kwargs)


def test_embedding_batches_are_stable_complete_and_bounded():
    texts = ["长" * 400, "短" * 40, "中" * 80, "同" * 40]
    batches = list(builder.embedding_batches(texts, batch_size=2))
    assert batches == [[1, 3], [2, 0]]
    assert sorted(i for batch in batches for i in batch) == list(range(len(texts)))
    assert list(builder.embedding_batches([], batch_size=2)) == []
    assert list(builder.embedding_batches(texts[:1], batch_size=64)) == [[0]]


@pytest.mark.parametrize("batch_size", [0, -1])
def test_embedding_batches_reject_nonpositive_bounds(batch_size):
    with pytest.raises(ValueError, match="batch size"):
        list(builder.embedding_batches(["合成"], batch_size=batch_size))


def test_token_batches_ignore_padding_and_preserve_original_vector_ids():
    class Tokens(FixtureEmbedding):
        def __init__(self):
            super().__init__()
            self.calls=[]
            self.tokens=[]
        def tokenize(self,texts):
            self.tokens.extend(texts)
            sizes={'long synthetic':2,'短':5}
            return [SimpleNamespace(attention_mask=[1]*sizes[t]+[0]*(8-sizes[t])) for t in texts]
        def embed(self,texts,**kwargs):
            self.calls.extend(texts)
            return super().embed(texts,**kwargs)
    texts=['短','long synthetic','短']
    model=Tokens();vectors=np.empty((3,384),dtype=np.float32)
    result=builder.embed_windows(model,texts,vectors,batch_size=1,use_token_lengths=True)
    assert model.tokens==['短','long synthetic']
    assert model.calls==['long synthetic','短']
    expected=np.asarray(list(FixtureEmbedding().embed(texts)))
    assert np.array_equal(vectors,expected)
    assert result['reused_occurrence_windows']==1


@pytest.mark.parametrize('lengths',[[1], [0,1], [-1,1], [True,1]])
def test_token_length_metadata_cannot_drop_or_invent_rows(lengths):
    with pytest.raises(ValueError,match='token lengths'):
        list(builder.embedding_batches(['a','b'],lengths=lengths))


def test_tokenization_row_loss_is_rejected():
    class Lost(FixtureEmbedding):
        def tokenize(self,texts):return []
    with pytest.raises(ValueError,match='row count'):
        builder.embed_windows(Lost(),['synthetic'],np.empty((1,384),dtype=np.float32),use_token_lengths=True)


def test_length_batches_reduce_padding_without_a_timing_threshold():
    texts = ["长" * 400, "短" * 40] * 65
    batches = list(builder.embedding_batches(texts))
    assert [len(batch) for batch in batches] == [64, 64, 2]
    assert sorted(i for batch in batches for i in batch) == list(range(len(texts)))
    original_padding = sum(
        max(map(len, texts[start : start + 64])) * len(texts[start : start + 64])
        for start in range(0, len(texts), 64)
    )
    grouped_padding = sum(max(len(texts[i]) for i in batch) * len(batch) for batch in batches)
    assert grouped_padding < original_padding


def test_length_inference_preserves_original_vector_and_ann_order(
    tmp_path, synthetic_model, monkeypatch
):
    inference_lengths = []
    ann_ids = []

    class ObservedEmbedding(FixtureEmbedding):
        def embed(self, texts, **kwargs):
            inference_lengths.extend(map(len, texts))
            return super().embed(texts, **kwargs)

    class ObservedIndex(FixtureIndex):
        def add_items(self, values, ids):
            ann_ids.extend(map(int, ids))
            super().add_items(values, ids)

    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=ObservedEmbedding))
    monkeypatch.setitem(sys.modules, "hnswlib", SimpleNamespace(Index=ObservedIndex))
    index, output, report = build_fixture(tmp_path)
    assert inference_lengths == sorted(inference_lengths)
    assert ann_ids == list(range(4))
    vectors = np.load(output / "vectors.npy")
    with sqlite3.connect(output / "metadata.sqlite") as meta, sqlite3.connect(index) as source:
        for vector_id, source_id, start, end in meta.execute(
            "SELECT vector_id,research_text_id,window_start,window_end "
            "FROM semantic_occurrence ORDER BY vector_id"
        ):
            text = source.execute("SELECT raw_text FROM research_text WHERE id=?", (source_id,)).fetchone()[0]
            expected = next(FixtureEmbedding().embed([text[start:end]]))
            np.testing.assert_array_equal(vectors[vector_id], expected)
    assert verify(index, output)["full_mapping_rows"] == 4
    assert report["hnsw_parameters"] == {"M": 12, "ef_construction": 80, "seed": 100, "threads": 1}


@pytest.fixture
def synthetic_model(monkeypatch):
    monkeypatch.setitem(sys.modules, "hnswlib", SimpleNamespace(Index=FixtureIndex))
    monkeypatch.setitem(
        sys.modules, "fastembed", SimpleNamespace(TextEmbedding=FixtureEmbedding)
    )
    monkeypatch.setattr(builder, "version", lambda _: "synthetic-test-only")
    monkeypatch.setattr(builder, "check_model", lambda _: None)


def inputs(tmp_path):
    cache = tmp_path / "cache"
    (cache / "model").mkdir(parents=True)
    (cache / "model/fixture").write_bytes(b"synthetic-not-weights")
    path = tmp_path / "source #1.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript(research_index.SCHEMA)
        db.execute(
            "INSERT INTO source_artifact VALUES('body','synthetic','contract','parser','run','synthetic',0,'synthetic')"
        )
        research_index._container(
            db,
            1,
            "body",
            "synthetic",
            ("synthetic", "contract", "parser", "run"),
            "owner",
            "record",
            "synthetic",
            "owner",
            "synthetic.json",
            "/0",
            "synthetic",
            None,
        )
        body = "合成文本用于验证窗口范围和独立来源核验。" * 30
        for number, text in enumerate((body, body, "合成标题", "1234567890" * 8), 1):
            research_index._text(
                db,
                number,
                1,
                "body",
                "synthetic",
                ("synthetic", "contract", "parser", "run"),
                "owner",
                "text",
                str(number),
                "synthetic",
                str(number),
                "CHS",
                "title" if number == 3 else "body",
                number,
                "/" + str(number),
                "synthetic.json",
                "/" + str(number),
                "synthetic",
                None,
                number,
                text,
            )
    recipe = tmp_path / "recipe.json"
    recipe.write_text(
        json.dumps(
            {
                "contract": "research71-narrative-semantic-input-v1",
                "locale": "CHS",
                "minimum_characters": 40,
                "window_characters": 400,
                "window_stride": 360,
                "source_fields": {"body": ["body"]},
                "excluded": ["synthetic scope only"],
            }
        ),
        encoding="utf-8",
    )
    scope = tmp_path / "scope.json"
    scope.write_text(
        json.dumps(builder.make_scope(path, "synthetic-corpus", recipe)),
        encoding="utf-8",
    )
    return path, cache, scope


def build_fixture(tmp_path):
    index, cache, scope = inputs(tmp_path)
    output = tmp_path / "overlay"
    report = builder.build(
        index, output, cache, scope_path=scope, available_allocated_bytes=3_000_000_000
    )
    return index, output, report


def test_complete_mapping_preserves_duplicate_physical_occurrences(
    tmp_path, synthetic_model
):
    index, output, report = build_fixture(tmp_path)
    checked = verify(index, output)
    assert checked["status"] == "PASS"
    assert checked["physical_occurrences"] == 2
    assert checked["full_mapping_rows"] == 4
    with sqlite3.connect(output / "metadata.sqlite") as db:
        refs = db.execute(
            "SELECT DISTINCT evidence_ref FROM semantic_occurrence"
        ).fetchall()
        assert len(refs) == 2  # Same text is not a logical deduplication key.
    assert report["config"]["research_index_sha256"] == sha256_file(index)
    assert not (output / "verified.json").exists()  # Builder alone never qualifies.
    with pytest.raises(FileExistsError):
        builder.build(
            index,
            output,
            tmp_path / "cache",
            scope_path=tmp_path / "scope.json",
            available_allocated_bytes=3_000_000_000,
        )


def test_sorted_inference_keeps_ann_insertion_and_vector_ids(tmp_path, synthetic_model, monkeypatch):
    calls = []

    class RecordingIndex(FixtureIndex):
        def add_items(self, values, ids):
            calls.extend(map(int, ids))
            super().add_items(values, ids)

    monkeypatch.setattr(sys.modules['hnswlib'], 'Index', RecordingIndex)
    index, output, report = build_fixture(tmp_path)
    with sqlite3.connect(output/'metadata.sqlite') as meta, sqlite3.connect(index) as source:
        rows = meta.execute('SELECT vector_id,research_text_id,window_start,window_end FROM semantic_occurrence ORDER BY vector_id').fetchall()
        values = np.load(output/'vectors.npy')
        for vector_id, text_id, start, end in rows:
            raw = source.execute('SELECT raw_text FROM research_text WHERE id=?',(text_id,)).fetchone()[0]
            expected = next(FixtureEmbedding().embed([raw[start:end]]))
            np.testing.assert_array_equal(values[vector_id], expected)
    assert calls == list(range(len(rows)))
    assert verify(index, output)['status'] == 'PASS'


def test_build_threads_are_explicit_and_do_not_change_query_budget(tmp_path, synthetic_model, monkeypatch):
    used = []

    def embedding(**kwargs):
        used.append(kwargs['threads'])
        return FixtureEmbedding()

    monkeypatch.setattr(sys.modules['fastembed'], 'TextEmbedding', embedding)
    index, cache, scope = inputs(tmp_path)
    report = builder.build(index, tmp_path/'overlay', cache, scope_path=scope,
                           available_allocated_bytes=3_000_000_000, embedding_threads=3)
    assert used == [3]
    assert report['build_parameters']['embedding_threads'] == 3
    assert report['config']['model']['threads'] == 2


def test_independent_oracle_rejects_resealed_missing_source_window(
    tmp_path, synthetic_model
):
    index, output, report = build_fixture(tmp_path)
    with sqlite3.connect(output / "metadata.sqlite") as db:
        db.execute("DELETE FROM semantic_occurrence WHERE vector_id=3")
        records = db.execute(
            "SELECT * FROM semantic_occurrence ORDER BY vector_id"
        ).fetchall()
    report["input_rowset_sha256"] = hashlib.sha256(
        b"".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
            for row in records
        )
    ).hexdigest()
    report["config"]["artifacts"]["metadata"] = {
        "size_bytes": (output / "metadata.sqlite").stat().st_size,
        "sha256": sha256_file(output / "metadata.sqlite"),
    }
    (output / "build.json").write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete full semantic source selection"):
        verify(index, output)


def test_source_and_vector_tampering_fail_closed(tmp_path, synthetic_model):
    index, output, report = build_fixture(tmp_path)
    vectors = np.load(output / "vectors.npy")
    vectors[0] = 0
    with (output / "vectors.npy").open("wb") as stream:
        np.save(stream, vectors)
    with pytest.raises(ValueError):
        verify(index, output)
    with sqlite3.connect(index) as db:
        db.execute("UPDATE research_text SET raw_text='changed'")
    with pytest.raises(ValueError):
        verify(index, output)


def test_capacity_rejected_before_any_overlay_is_created(tmp_path, synthetic_model):
    index, cache, scope = inputs(tmp_path)
    with pytest.raises(ValueError, match="preflight"):
        builder.build(
            index,
            tmp_path / "absent",
            cache,
            scope_path=scope,
            available_allocated_bytes=1,
        )
    assert not (tmp_path / "absent").exists()


def test_unqualified_model_and_bad_window_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="pinned local model"):
        builder.check_model(tmp_path / "missing")
    with pytest.raises(ValueError, match="stride"):
        list(builder.windows("synthetic", 400, 0))


def test_all_build_has_vector_stage_after_evidence_and_explicit_opt_out():
    assert STAGES[-2:] == ("index", "semantic")
    assert callable(Rebuild.semantic)


def test_fresh_ab_has_identical_inputs_vectors_and_deterministic_metadata(
    tmp_path, synthetic_model
):
    index, cache, scope = inputs(tmp_path)
    reports = []
    for arm in ("a", "b"):
        output = tmp_path / arm
        reports.append(
            builder.build(
                index,
                output,
                cache,
                scope_path=scope,
                available_allocated_bytes=3_000_000_000,
            )
        )
        assert verify(index, output)["status"] == "PASS"
    assert reports[0]["input_rowset_sha256"] == reports[1]["input_rowset_sha256"]
    assert reports[0]["vectors_sha256"] == reports[1]["vectors_sha256"]
    assert reports[0]["config"] == reports[1]["config"]


def test_generated_overlay_opens_existing_runtime_and_resolves_exact_evidence(
    tmp_path, synthetic_model
):
    from genshin_lore.semantic_discovery import SemanticDiscovery

    index, output, report = build_fixture(tmp_path)
    bindings = {
        "semantic_discovery": {
            "metadata": str(output / "metadata.sqlite"),
            "hnsw": str(output / "index.hnsw"),
            "model_cache": str(tmp_path / "cache"),
        }
    }
    runtime = SemanticDiscovery(
        report["config"], bindings, "synthetic-corpus", sha256_file(index)
    )
    try:
        hits = runtime.candidates("合成文本")
        assert hits
        assert {hit["research_text_id"] for hit in hits} == {1, 2}
        with sqlite3.connect(output / "metadata.sqlite") as db:
            refs = db.execute("SELECT evidence_ref FROM semantic_occurrence").fetchall()
        from genshin_lore.research_refs import decode_ref

        assert {decode_ref(ref[0])["index"] for ref in refs} == {1, 2}
    finally:
        runtime.close()


def test_vector_stage_qualifies_and_generates_usable_isolated_config(
    tmp_path, synthetic_model, monkeypatch
):
    import importlib.metadata
    import runpy
    from scripts import build as driver, serve
    from genshin_lore.research71_unified import Research71Unified
    from scripts.runtime_config import load_runtime_paths

    pipeline = runpy.run_path(
        str(Path(__file__).with_name("test_research_pipeline.py"))
    )["pipeline"]
    job, manifest, bindings, config = pipeline(tmp_path / "stage")
    metadata = job.output / "metadata/manifests/research"
    profile = json.loads((metadata / "profile.json").read_text())
    profile["corpus_manifest"] = "manifests/research/candidate-7.1-complete-v1.json"
    (metadata / "candidate-7.1-complete-profile-v1.json").write_text(
        json.dumps(profile), encoding="utf-8"
    )
    (metadata / "candidate-7.1-complete-v1.json").write_bytes(
        (metadata / "corpus.json").read_bytes()
    )
    (metadata / "candidate-7.1-agent-runtime-v2.json").write_text(
        json.dumps(
            {
                "candidate_profile_sha256": "literal fixture",
                "contract": "research71-unified-agent-runtime-v1",
                "status": "local_candidate_qualification",
                "snapshot": driver.SNAPSHOT,
                "product_accepted": False,
                "full_research_7_1_accepted": False,
                "legacy_fallback": False,
                "implementation_sha256": {},
                "corpus_manifest_sha256": sha256_file(
                    metadata / "candidate-7.1-complete-v1.json"
                ),
            }
        ),
        encoding="utf-8",
    )
    (job.output / "complete-bindings.json").write_bytes(
        (job.output / "bindings.json").read_bytes()
    )
    # Use the production base-stage names but KEEP its existing default file.
    # Additional vector construction must upgrade this actual starting state,
    # not require manually removing/renaming the prior default configuration.
    literal_config = {
        "corpus": str((metadata/'candidate-7.1-complete-v1.json').relative_to(job.output)),
        "profile": str((metadata/'candidate-7.1-complete-profile-v1.json').relative_to(job.output)),
        "bindings": "complete-bindings.json",
        "index": str(job.path('research_index').relative_to(job.output)),
    }
    config.write_text(json.dumps(literal_config), encoding="utf-8")
    _, job.model_cache, _ = inputs(tmp_path)
    job.available = 3_000_000_000
    job.allocated_start = driver.allocated((driver.ROOT, job.output))
    original_scope = builder.make_scope

    def fixture_scope(*args):
        result = original_scope(*args)
        result.update(source_fields={"tips": ["body"]}, minimum_characters=1)
        return result

    monkeypatch.setattr(builder, "make_scope", fixture_scope)
    original_version = importlib.metadata.version
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda n: "synthetic-only"
        if n in ("hnswlib", "fastembed", "onnxruntime")
        else original_version(n),
    )
    # The synthetic corpus intentionally contains only two components, not
    # all family convenience views. Exercise actual candidate qualification
    # and source-checked query without pretending to cover those views.
    monkeypatch.setattr(Research71Unified, "_entries", lambda self: None)

    def small_smoke(config):
        runtime = Research71Unified(**load_runtime_paths(config))
        query = runtime.query
        try:
            for mode in ("literal", "lexical", "semantic", "hybrid"):
                page = query.search(query.corpus_ref, "合成", mode=mode, limit=2)
                assert page["items"]
                for item in page["items"]:
                    query.read(query.corpus_ref, item["evidence_ref"])
                    query.inspect(query.corpus_ref, item["evidence_ref"])
            return {"status": "PASS_SYNTHETIC_GENERATED_VECTOR_CONFIG"}
        finally:
            query.close()

    def failed_smoke(config):
        raise RuntimeError("synthetic runtime rejection")

    monkeypatch.setattr(serve, "smoke", failed_smoke)
    with pytest.raises(RuntimeError, match="runtime rejection"):
        job.semantic()
    assert json.loads(config.read_text()) == literal_config
    monkeypatch.setattr(serve, "smoke", small_smoke)
    job.semantic()
    receipt = job.output / "semantic/verified.json"
    assert json.loads(receipt.read_text())["independent_verifier_completed"] is True
    assert (job.output / "runtime-config.json").exists()
    assert (
        json.loads((job.output / "runtime-config.literal.json").read_text())
        == literal_config
    )
    assert json.loads(config.read_text())["bindings"] == "semantic-bindings.json"
    job.semantic()
    job.embedding_threads = 7
    with pytest.raises(ValueError, match="resume identity"):
        job.semantic()
    del job.embedding_threads
    value = json.loads(receipt.read_text())
    value["independent_verifier_completed"] = False
    receipt.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="resume identity"):
        job.semantic()
