#!/usr/bin/env python3
"""Local, no-clobber 7.1 overlay using existing semantic/lexical contracts.

Never mutates/rebuilds the Research index or deduplicates physical occurrences.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from importlib.metadata import version
from pathlib import Path

import numpy as np
from genshin_lore.hnsw_io import save_index
from genshin_lore.lexical_discovery import build_lexicon, text_ref
from genshin_lore.semantic_discovery import sha256_file, tree_sha256

ROOT = Path(__file__).resolve().parents[2]
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
REVISION = "faf4aa4225822f3bc6376869cb1164e8e3feedd0"


def default_embedding_threads():
    """Use up to four affinity-visible Linux CPUs; keep other platforms at two."""
    if sys.platform != "linux":
        return 2
    cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    return min(4, max(1, cpus))


def windows(text, size=400, stride=360):
    if not 0 < stride <= size:
        raise ValueError("semantic window stride must be positive and <= size")
    for start in range(0, len(text), stride):
        yield start, min(len(text), start + size), text[start : start + size]
        if start + size >= len(text):
            break


def embedding_batches(texts, batch_size=64):
    """Reduce padding while retaining each window's original vector ID."""
    if batch_size <= 0:
        raise ValueError("embedding batch size must be positive")
    order = sorted(range(len(texts)), key=lambda i: (len(texts[i]), i))
    for start in range(0, len(order), batch_size):
        yield order[start : start + batch_size]


def embed_windows(model, texts, vectors, *, batch_size=64, progress=None):
    """Infer exact text once in this call; retain every physical vector row.

    Groups store only text references and original IDs. Normalized vectors are
    scattered directly into the output, so no additional full vector cache is
    needed. String equality, rather than a digest or source identity, decides
    reuse. Nothing is retained across builds.
    """
    occurrences = {}
    for vector_id, text in enumerate(texts):
        occurrences.setdefault(text, []).append(vector_id)
    unique_texts = list(occurrences)
    done = inferred = 0
    for batch_number, ids in enumerate(embedding_batches(unique_texts, batch_size)):
        batch = np.asarray(
            list(model.embed([unique_texts[i] for i in ids], batch_size=batch_size)),
            dtype=np.float32,
        )
        if batch.shape != (len(ids), 384) or not np.all(np.isfinite(batch)):
            raise ValueError("embedding shape/finite violation")
        norms = np.linalg.norm(batch, axis=1)
        if np.any(norms <= 0):
            raise ValueError("zero vector")
        batch = batch / norms[:, None]
        for unique_id, vector in zip(ids, batch):
            original_ids = occurrences[unique_texts[unique_id]]
            vectors[original_ids] = vector
            done += len(original_ids)
        inferred += len(batch)
        if progress is not None:
            progress(batch_number, done, inferred, len(unique_texts))
    return {
        "unique_inference_windows": len(unique_texts),
        "reused_occurrence_windows": len(texts) - len(unique_texts),
    }


def selected_rows(db, scope):
    for key, fields in sorted(scope["source_fields"].items()):
        sql = "SELECT * FROM research_text WHERE source_key=? AND locale=? AND length(raw_text)>=?"
        args = [key, scope["locale"], scope["minimum_characters"]]
        if fields != ["*"]:
            sql += " AND owner_field IN (" + ",".join("?" for _ in fields) + ")"
            args += fields
        for row in db.execute(sql + " ORDER BY id", args):
            # Reject support-only strings without natural language letters.
            if any("\u3400" <= c <= "\u9fff" for c in row["raw_text"]):
                yield row


def make_scope(index, corpus_ref, recipe):
    """Static selection policy plus THIS index's identity, never a release fallback."""
    scope = json.loads(Path(recipe).read_text(encoding="utf-8"))
    if scope.get("contract") != "research71-narrative-semantic-input-v1":
        raise ValueError("unsupported semantic input contract")
    scope.update(
        corpus_ref=corpus_ref,
        index_sha256=sha256_file(index),
        baseline_release="local-source-verified-rebuild-not-release-acceptance",
    )
    return scope


def check_model(cache):
    if (
        tree_sha256(Path(cache) / "model")
        != "a7afbe9e63e0060cf7ca4a995e11ee5c916ff0c129b3096b6309eb530427c19f"
    ):
        raise ValueError(
            "pinned local model tree mismatch; obtain the documented exact model first"
        )


def build(index, output, cache, *, scope_path, available_allocated_bytes, embedding_threads=None):
    import hnswlib
    from fastembed import TextEmbedding
    import shutil

    index, output, cache = map(Path, (index, output, cache))
    embedding_threads = default_embedding_threads() if embedding_threads is None else embedding_threads
    if type(embedding_threads) is not int or embedding_threads <= 0:
        raise ValueError("embedding_threads must be a positive integer")
    if output.exists():
        raise FileExistsError("no-clobber: " + str(output))
    # Caller supplies current inclusive project headroom, not host free space.
    # No private storage helper, deployment path or guessed old index is used.
    reserve = 2_000_000_000
    parent = output.resolve().parent
    while not parent.exists():
        parent = parent.parent
    if available_allocated_bytes < reserve or shutil.disk_usage(parent).free < reserve:
        raise ValueError(
            "overlay peak preflight requires 2GB measured headroom and readable scope"
        )
    scope_path = Path(scope_path)
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    if not 0 < scope["window_stride"] <= scope["window_characters"]:
        raise ValueError("invalid semantic window bounds")
    if sha256_file(index) != scope["index_sha256"]:
        raise ValueError("exact 7.1 index mismatch")
    if any(Path(str(index) + s).exists() for s in ("-wal", "-shm", "-journal")):
        raise ValueError("index sidecar present")
    check_model(cache)
    output.mkdir(parents=True)
    (output / "scope.json").write_bytes(scope_path.read_bytes())
    started = time.perf_counter()
    db = sqlite3.connect(index.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    model_tree = tree_sha256(cache / "model")
    cache_tree = tree_sha256(cache)
    meta = sqlite3.connect(output / "metadata.sqlite")
    meta.executescript("""
    CREATE TABLE semantic_release(id INTEGER PRIMARY KEY,corpus_ref TEXT,research_index_sha256 TEXT,model_id TEXT,model_revision TEXT,model_tree_sha256 TEXT,dimension INTEGER,metric TEXT,preprocessor TEXT,candidate_extractor TEXT,selection TEXT,unique_candidate_count INTEGER,physical_occurrence_count INTEGER);
    CREATE TABLE semantic_occurrence(vector_id INTEGER PRIMARY KEY,research_text_id INTEGER NOT NULL,evidence_ref TEXT NOT NULL,text_sha256 TEXT NOT NULL,window_start INTEGER NOT NULL,window_end INTEGER NOT NULL,window_sha256 TEXT NOT NULL,source_key TEXT NOT NULL,source_identity_json TEXT NOT NULL);
    CREATE INDEX occurrence_text ON semantic_occurrence(research_text_id,vector_id);
    """)
    inputs = []
    mapping_digest = hashlib.sha256()
    counts = {}
    physical = 0
    for row in selected_rows(db, scope):
        physical += 1
        counts[row["source_key"]] = counts.get(row["source_key"], 0) + 1
        identity = {
            k: row[k]
            for k in (
                "source_key",
                "evidence_family",
                "evidence_contract",
                "evidence_parser",
                "evidence_run",
                "occurrence_id",
                "child_id",
                "text_source_path",
                "text_source_pointer",
                "physical_pair_ordinal",
                "text_sha256",
                "locale",
                "owner_field",
            )
        }
        for start, end, text in windows(
            row["raw_text"], scope["window_characters"], scope["window_stride"]
        ):
            vector_id = len(inputs)
            inputs.append(text)
            record = (
                vector_id,
                row["id"],
                text_ref(row, scope["corpus_ref"]),
                row["text_sha256"],
                start,
                end,
                hashlib.sha256(text.encode()).hexdigest(),
                row["source_key"],
                json.dumps(
                    identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
            )
            meta.execute(
                "INSERT INTO semantic_occurrence VALUES(?,?,?,?,?,?,?,?,?)", record
            )
            mapping_digest.update(
                json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode()
                + b"\n"
            )
    n = len(inputs)
    if not n:
        raise ValueError("empty narrative input")
    selected_at = time.perf_counter()
    print(
        json.dumps(
            {"stage": "selection", "physical": physical, "vectors": n, "counts": counts}
        ),
        flush=True,
    )
    vectors = np.lib.format.open_memmap(
        output / "vectors.npy", mode="w+", dtype="float32", shape=(n, 384)
    )
    model = TextEmbedding(
        model_name=MODEL,
        cache_dir=str(cache),
        specific_model_path=str(cache / "model"),
        local_files_only=True,
        threads=embedding_threads,
    )
    def progress(batch_number, done, inferred, unique):
        if batch_number % 32 == 0 or done == n:
            print(
                json.dumps(
                    {
                        "stage": "embedding",
                        "done": done,
                        "total": n,
                        "inferred": inferred,
                        "unique_inference_windows": unique,
                        "seconds": time.perf_counter() - started,
                    }
                ),
                flush=True,
            )
    inference = embed_windows(model, inputs, vectors, progress=progress)
    vectors.flush()
    embedded_at = time.perf_counter()
    del model
    # HNSW construction depends on insertion order. Only inference is sorted;
    # source mapping and ANN insertion both keep the original vector ID order.
    ann = hnswlib.Index(space="cosine", dim=384)
    ann.init_index(max_elements=n, ef_construction=80, M=12, random_seed=100)
    ann.set_num_threads(1)
    for start in range(0, n, 64):
        end = min(start + 64, n)
        ann.add_items(vectors[start:end], np.arange(start, end))
    del vectors
    save_index(ann, output / "index.hnsw")
    indexed_at = time.perf_counter()
    selection = hashlib.sha256(scope_path.read_bytes()).hexdigest()
    meta.execute(
        "INSERT INTO semantic_release VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            scope["corpus_ref"],
            scope["index_sha256"],
            MODEL,
            REVISION,
            model_tree,
            384,
            "cosine",
            "physical-source-window-400-stride360-v1",
            "research71-narrative-window-v1",
            selection,
            n,
            physical,
        ),
    )
    meta.commit()
    if meta.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("metadata integrity")
    meta.close()
    db.close()
    lexicon_started = time.perf_counter()
    lex = build_lexicon(
        index, output / "lexicon.sqlite", scope["corpus_ref"], scope["index_sha256"]
    )
    lexicon_finished = time.perf_counter()
    lex["anchor_extraction_version"] = lex.pop("extraction_version")
    lex["sha256"] = sha256_file(output / "lexicon.sqlite")
    config = {
        "contract": "semantic-discovery-overlay-v1",
        "corpus_ref": scope["corpus_ref"],
        "research_index_sha256": scope["index_sha256"],
        "release_binding": scope["baseline_release"],
        "model": {
            "id": MODEL,
            "revision": REVISION,
            "license": "Apache-2.0",
            "path_relative": "model",
            "tree_sha256": model_tree,
            "cache_tree_sha256": cache_tree,
            "threads": 2,
        },
        "dimension": 384,
        "metric": "cosine",
        "preprocessor": "physical-source-window-400-stride360-v1",
        "candidate_extractor": "research71-narrative-window-v1",
        "selection": selection,
        "unique_candidate_count": n,
        "physical_occurrence_count": physical,
        "query_ef": 200,
        "query_candidate_k": 1000,
        "semantic_indexed_scopes": scope,
        "semantic_unsupported_scopes": scope["excluded"],
        "lexicon": lex,
        "artifacts": {
            key: {
                "size_bytes": (output / name).stat().st_size,
                "sha256": sha256_file(output / name),
            }
            for key, name in [("metadata", "metadata.sqlite"), ("hnsw", "index.hnsw")]
        },
    }
    report = {
        "config": config,
        "input_rowset_sha256": mapping_digest.hexdigest(),
        "vectors_sha256": sha256_file(output / "vectors.npy"),
        "source_counts": counts,
        "seconds": time.perf_counter() - started,
        "build_parameters": {"embedding_threads": embedding_threads, "batch_size": 64,
                             "inference_order": "stable-character-length-v1",
                             "vector_id_order": "original-physical-source-order",
                             "ann_insertion_order": "original-physical-source-order"},
        "dependencies": {
            x: version(x) for x in ["fastembed", "onnxruntime", "numpy", "hnswlib"]
        },
        "hnsw_parameters": {"M": 12, "ef_construction": 80, "seed": 100, "threads": 1},
        "embedding_batching": {
            "order": "length_then_first_vector_id",
            "batch_size": 64,
            "reuse": "exact_text_within_current_build",
            **inference,
        },
        "timings_seconds": {
            "selection": selected_at - started,
            "embedding": embedded_at - selected_at,
            "ann": indexed_at - embedded_at,
            "lexicon": lexicon_finished - lexicon_started,
        },
        "scope_sha256": selection,
    }
    (output / "build.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    )
    print(json.dumps({"stage": "complete", "seconds": report["seconds"]}), flush=True)
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--index", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--model-cache", type=Path, required=True)
    p.add_argument("--scope", type=Path, required=True)
    p.add_argument("--available-allocated-bytes", type=int, required=True)
    p.add_argument("--embedding-threads", type=int, help="Positive ONNX build thread count; default Linux <=4 affinity-visible CPUs, other platforms 2")
    a = p.parse_args()
    build(
        a.index,
        a.output,
        a.model_cache,
        scope_path=a.scope,
        available_allocated_bytes=a.available_allocated_bytes,
        embedding_threads=a.embedding_threads,
    )
