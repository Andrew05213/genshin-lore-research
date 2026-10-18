"""Independent full mapping/vector oracle; SQL is verification, not Agent access."""

import argparse
import hashlib
import json
import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path
import numpy as np
from genshin_lore.research_refs import decode_ref
from genshin_lore.semantic_discovery import sha256_file


def require(condition, message):
    if not condition:
        raise ValueError("research verification failed: " + str(message))


def verify(index, build_dir):
    with ExitStack() as resources:
        return _verify(Path(index), Path(build_dir), resources)


def _verify(index, build_dir, resources):
    import hnswlib

    report = json.loads((build_dir / "build.json").read_text())
    config = report["config"]
    require(
        sha256_file(index) == config["research_index_sha256"],
        "Research index seal differs from overlay binding",
    )
    source = resources.enter_context(
        closing(
            sqlite3.connect(
                Path(index).resolve().as_uri() + "?mode=ro&immutable=1", uri=True
            )
        )
    )
    source.row_factory = sqlite3.Row
    meta = resources.enter_context(
        closing(
            sqlite3.connect(
                (build_dir / "metadata.sqlite").resolve().as_uri()
                + "?mode=ro&immutable=1",
                uri=True,
            )
        )
    )
    meta.row_factory = sqlite3.Row
    vectors = np.load(build_dir / "vectors.npy", mmap_mode="r")
    resources.callback(vectors._mmap.close)
    ann = hnswlib.Index(space="cosine", dim=384)
    ann.load_index(str(build_dir / "index.hnsw"))
    require(
        ann.get_current_count() == len(vectors) == config["unique_candidate_count"],
        "ANN, vectors and declared candidate counts differ",
    )
    n = 0
    seen = set()
    digest = hashlib.sha256()
    max_diff = 0.0
    scope = json.loads((build_dir / "scope.json").read_text(encoding="utf-8"))
    require(
        0 < scope["window_stride"] <= scope["window_characters"],
        "invalid semantic window bounds",
    )
    require(
        sha256_file(build_dir / "scope.json")
        == report["scope_sha256"]
        == config["selection"],
        "input scope seal mismatch",
    )
    require(
        scope["corpus_ref"] == config["corpus_ref"]
        and scope["index_sha256"] == config["research_index_sha256"],
        "input scope binding mismatch",
    )
    # Enumerate the COMPLETE expected source selection independently. An
    # internally consistent but omitted row/window must not pass this oracle.
    expected = []
    for key, fields in sorted(scope["source_fields"].items()):
        for row in source.execute(
            "SELECT id,owner_field,raw_text FROM research_text WHERE source_key=? AND locale=? ORDER BY id",
            (key, scope["locale"]),
        ):
            if len(row["raw_text"]) < scope["minimum_characters"] or (
                fields != ["*"] and row["owner_field"] not in fields
            ):
                continue
            if not any("\u3400" <= ch <= "\u9fff" for ch in row["raw_text"]):
                continue
            start = 0
            while start < len(row["raw_text"]):
                end = min(start + scope["window_characters"], len(row["raw_text"]))
                expected.append((row["id"], start, end))
                if end == len(row["raw_text"]):
                    break
                start += scope["window_stride"]
    for item in meta.execute("SELECT * FROM semantic_occurrence ORDER BY vector_id"):
        row = source.execute(
            "SELECT * FROM research_text WHERE id=?", (item["research_text_id"],)
        ).fetchone()
        require(row is not None, "mapped source text disappeared")
        identity = json.loads(item["source_identity_json"])
        require(
            all(row[k] == v for k, v in identity.items()),
            "physical source identity differs",
        )
        require(row["locale"] == "CHS", "dense source locale is outside CHS scope")
        require(
            hashlib.sha256(row["raw_text"].encode()).hexdigest()
            == row["text_sha256"]
            == item["text_sha256"],
            "full source text SHA differs",
        )
        segment = row["raw_text"][item["window_start"] : item["window_end"]]
        require(
            segment
            and hashlib.sha256(segment.encode()).hexdigest() == item["window_sha256"],
            "window text or range SHA differs",
        )
        ref = decode_ref(item["evidence_ref"])
        require(
            ref
            == {
                "v": 2,
                "kind": "text",
                "corpus": config["corpus_ref"],
                "source": row["source_key"],
                "family": row["evidence_family"],
                "contract": row["evidence_contract"],
                "parser": row["evidence_parser"],
                "run": row["evidence_run"],
                "occurrence": row["occurrence_id"],
                "child": row["child_id"],
                "index": row["id"],
            },
            "evidence ref does not bind the exact source occurrence",
        )
        require(item["vector_id"] == n, "vector IDs are not contiguous and ordered")
        require(
            n < len(expected)
            and (item["research_text_id"], item["window_start"], item["window_end"])
            == expected[n],
            "missing, repeated or reordered source window",
        )
        digest.update(
            json.dumps(tuple(item), ensure_ascii=False, separators=(",", ":")).encode()
            + b"\n"
        )
        seen.add(row["id"])
        n += 1
    require(
        digest.hexdigest() == report["input_rowset_sha256"],
        "complete mapping rowset digest differs",
    )
    require(
        len(seen) == config["physical_occurrence_count"],
        "physical occurrence count differs",
    )
    require(
        n == len(expected) and vectors.shape == (n, config["dimension"]),
        "incomplete full semantic source selection",
    )
    for start in range(0, n, 1024):
        actual = ann.get_items(list(range(start, min(n, start + 1024))))
        expected = vectors[start : start + len(actual)]
        require(
            np.all(np.isfinite(actual)) and np.all(np.isfinite(expected)),
            "non-finite vector values",
        )
        require(
            np.all(np.abs(np.linalg.norm(expected, axis=1) - 1) < 1e-5),
            "stored vectors are not normalized",
        )
        max_diff = max(max_diff, float(np.max(np.abs(actual - expected))))
    require(max_diff < 1e-6, "ANN vector values differ from original vectors")
    require(
        sha256_file(build_dir / "vectors.npy") == report["vectors_sha256"],
        "original vector file seal differs",
    )
    for key, name in [("metadata", "metadata.sqlite"), ("hnsw", "index.hnsw")]:
        require(
            sha256_file(build_dir / name) == config["artifacts"][key]["sha256"],
            "semantic artifact seal differs",
        )
        require(
            (build_dir / name).stat().st_size == config["artifacts"][key]["size_bytes"],
            "artifact size mismatch",
        )
        require(
            not any(
                Path(str(build_dir / name) + s).exists()
                for s in ("-wal", "-shm", "-journal")
            ),
            "semantic artifact sidecar",
        )
    # Lexical surfaces are independent physical occurrences, not deduplicated
    # entity identities. Verify the full selected name/title/speaker rowset.
    from genshin_lore.lexical_discovery import LOW_INFORMATION, text_ref

    lex = resources.enter_context(
        closing(
            sqlite3.connect(
                (build_dir / "lexicon.sqlite").resolve().as_uri()
                + "?mode=ro&immutable=1",
                uri=True,
            )
        )
    )
    require(
        sha256_file(build_dir / "lexicon.sqlite") == config["lexicon"]["sha256"],
        "lexicon seal mismatch",
    )
    expected_lex = []
    for row in source.execute(
        "SELECT * FROM research_text WHERE locale='CHS' AND length(raw_text) BETWEEN 2 AND 64 ORDER BY id"
    ):
        if row["raw_text"] in LOW_INFORMATION or not any(
            marker in (row["owner_field"] or "").casefold()
            for marker in ("name", "title", "speaker")
        ):
            continue
        expected_lex.append(
            (
                row["raw_text"],
                row["id"],
                text_ref(row, config["corpus_ref"]),
                row["owner_field"],
                row["evidence_family"],
                row["locale"],
            )
        )
    require(
        lex.execute(
            "SELECT surface_form,research_text_id,evidence_ref,source_field,family,locale FROM lexical_occurrence ORDER BY id"
        ).fetchall()
        == expected_lex,
        "incomplete lexical occurrence rowset",
    )
    counts = {}
    for row in expected_lex:
        counts[row[0]] = counts.get(row[0], 0) + 1
    require(
        lex.execute(
            "SELECT surface_form,physical_occurrence_count FROM lexical_surface ORDER BY surface_form"
        ).fetchall()
        == sorted(counts.items()),
        "lexical surface counts differ",
    )
    lex.close()
    meta.close()
    source.close()
    return {
        "status": "PASS",
        "full_mapping_rows": n,
        "physical_occurrences": len(seen),
        "input_rowset_sha256": digest.hexdigest(),
        "vectors_sha256": report["vectors_sha256"],
        "ann_to_original_vector_max_absolute_difference": max_diff,
        "scope": "complete source selection, row/window/ref/coordinates, lexical rowset and stored vector binding; not relevance or logical identity",
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--index", type=Path, required=True)
    p.add_argument("--build-dir", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--compare", type=Path)
    a = p.parse_args()
    result = verify(a.index, a.build_dir)
    if a.compare:
        other = verify(a.index, a.compare)
        left = json.loads((a.build_dir / "build.json").read_text())
        right = json.loads((a.compare / "build.json").read_text())

        # ANN serialization may differ without different input/vector values.
        # Compare deterministic metadata independently from each binary seal.
        def deterministic_config(report):
            config = json.loads(json.dumps(report["config"]))
            config["artifacts"].pop("hnsw")
            return config

        require(
            deterministic_config(left) == deterministic_config(right),
            "fresh A/B deterministic metadata differs",
        )
        require(
            left["input_rowset_sha256"] == right["input_rowset_sha256"],
            "fresh A/B input rowsets differ",
        )
        require(
            left["vectors_sha256"] == right["vectors_sha256"],
            "fresh A/B vector values differ",
        )
        result["fresh_ab"] = {
            "input_equal": True,
            "vector_bytes_equal": True,
            "deterministic_metadata_equal": True,
            "ann_binary_equal": sha256_file(a.build_dir / "index.hnsw")
            == sha256_file(a.compare / "index.hnsw"),
            "a_seconds": left["seconds"],
            "b_seconds": right["seconds"],
        }
    if a.output.exists():
        raise FileExistsError(a.output)
    a.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
