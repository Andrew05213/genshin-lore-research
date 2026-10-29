# Build inference and Research batching implementation plan

**Goal:** Reduce repeated inference and SQLite calls while preserving every physical source occurrence and the existing independent acceptance gates.

**Architecture:** Group exact window strings within one semantic invocation, infer representatives, and scatter vectors back to original IDs. Buffer Research writes in bounded batches and reuse the exact parent metadata required by text rows. Independent verifiers retain their own source enumeration and rowset comparisons.

**Tech stack:** Python 3.12, SQLite, NumPy, fixed local FastEmbed model, hnswlib, pytest, native Windows PowerShell.

## Semantic inference

- [ ] Modify `scripts/builders/semantic.py`: exact string grouping within a single invocation; length ordering and original vector IDs retained; no persistent embedding cache.
- [ ] Add synthetic tests covering different identities with equal text, equal hashes with different text, interleaved duplicates, every vector row written, malformed inference shape, nonfinite values and zero vectors.
- [ ] Run `python -m pytest -q tests/test_semantic_build.py tests/semantic_native.py` with the new regression module.
- [ ] Use the fixed local model to compare baseline and optimized inference on the same real source windows. Compare all input identities, normalized vectors, ANN results and independent oracle outcomes; record timing and memory rather than assuming linear savings.

## Research batching

- [ ] Modify `src/genshin_lore/research_index.py`, `src/genshin_lore/research71_candidate_adapters.py` and `scripts/builders/research_dataset.py` as necessary for bounded writes and parent metadata access.
- [ ] Preserve insertion order, foreign keys, missing-parent failures, ambiguous candidates, source scope and final schema. Flush buffered writes before reads, constraints, independent oracle and publication.
- [ ] Add synthetic negatives for flush barriers, missing parents, duplicate identities, empty/partial scope and source-row loss. Do not change verifier acceptance rules.
- [ ] Run `python -m pytest -q tests/test_research_pipeline.py` with the focused regressions and the formal relationship negative suite.
- [ ] Build independent baseline and optimized indexes from the same fixed, sealed real inputs; run each independent oracle, compare complete logical rows/schema, and record actual performance.

## Native Windows acceptance

- [ ] Freeze the implementation only after both directed A/B checks pass and demonstrate useful performance. Run all collected source tests, native ANN path tests, compile, six command help checks, packaging, Markdown, license and locked dependency checks. Keep actual symlink tests and their required Windows privileges.
- [ ] Rebuild real Activity and NPC components and run their independent source verifiers. Preserve both successes and failures.
- [ ] Replace eligible closed working copies only with self-contained lossless archives whose complete decompressed member SHA values match the original seal manifests. Preserve the original reports and all r9 files in place.
- [ ] Measure project source, both upstream checkouts, environment, model, archives, outputs and project temporary files using the existing Windows accounting method. Keep hard 90,000,000,000 B, warning 77,309,411,328 B and at least 27,000,000,000 B new-build headroom.
- [ ] Start exactly one fresh `scripts/build.py --stage all` with both fixed sources and the explicitly verified local model cache. Record stdout, stderr, actual exit code, stage receipts, time and resource observations. Stop on the first failure and retain its complete scene.
- [ ] After all stages and independent oracles pass, run `scripts/serve.py --check`, `scripts/check.py`, `scripts/check_dialogue.py` and actual literal/lexical/semantic/hybrid requests against only the new configurations.
- [ ] Deliver PASS/FAIL/NOT_RUN/BLOCKED with original evidence locations, artifact sizes/SHA, versions, capacity and performance. Do not merge, deploy or promote a release.
