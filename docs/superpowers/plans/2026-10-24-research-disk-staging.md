# Research disk staging implementation plan

**Goal:** Replace the Research RAM database with bounded private disk staging, retain failed bytes and sidecars, and durably record oracle and phase diagnostics before compaction/publication.

**Architecture:** Keep projection identities, SQL schema, the complete independent verifier and mandatory `VACUUM`. Use the existing absent-target publication primitives for a same-directory private SQLite database. Limit SQLite page caches, spill temporary work to disk and reserve compaction space from the measured project budget. Diagnostic files are append-only/exclusive and never qualify an unfinished artifact.

**Tech stack:** Python 3.12, SQLite, native Windows/NTFS, pytest. Execute inline on `codex/research-disk-staging`, descended from `f971daf`; preserve all old branches and seals.

## Implementation and regressions

- [ ] Add `tests/test_research_staging.py`: real synthetic disk build; phase/oracle persistence before injected `VACUUM` failure; projection/oracle/publication/fsync failures retain database and existing sidecars; low budget and existing target/sidecar reject; Unicode paths; output/input alias rejects; no success receipt on failure.
- [ ] Add `scripts/builders/research_staging.py` for bounded connection policy, durable exclusive diagnostics, pre-compaction disk reservation and absent-target publication. Reuse `genshin_lore.batch_release`; do not change historical helpers or independent verifiers.
- [ ] Change `scripts/builders/research_dataset.py::build` to create staging before projection, commit completed boundaries, persist the complete oracle immediately, run `VACUUM`, persist post-compaction checks, then publish. Preserve original exceptions even if recording a failure also fails. Retain SQLite files on every failure; only successful owned staging cleanup is allowed.
- [ ] Run the actual synthetic Research/index/oracle/MCP pipeline before real-source checks. Preserve all existing relation/candidate negatives, empty and partial scopes.
- [ ] Update the dataset/Windows guides with storage and failure semantics; avoid machine paths and progress logs in public docs.

## Current-code qualification

- [ ] Make a private new audit directory; keep R14 records unchanged. Record Git/WIP, Windows/Python/SQLite/dependencies, UTF-8 and fixed input identities. Do not launch concurrent full builds.
- [ ] Real A/B: two complete declared components (`narrative_dialog`, `native_message`) from already source-verified fixed inputs, sequential old/new source processes, same corpus/bindings, independent oracle on both; compare all schemas/business rows/counts, timing and native process high-water counters. This directed A/B is not fresh full acceptance.
- [ ] Run all collected no-game-data tests, compilation, six help commands, native Windows paths, formal negatives, Markdown/license/installation/package/dependency checks and explicit fixed model check. Symlink cases still need a capable native token; do not skip them.
- [ ] Build and independently verify real `activity_archive` and `narrative_npc` from fixed upstream again.

## Preservation, resources and fresh acceptance

- [ ] Inventory eligible closed working copies and project-owned temporary files. Before removing any, losslessly archive, verify every payload/link/metadata and perform native recovery checks. Keep r9 in place; preserve old seals and failures. Never treat host free space as project quota.
- [ ] Fresh native allocation inventory must show at least 27,000,000,000 B headroom under hard 90,000,000,000 B, with adequate output/system temporary volume space and memory/pagefile observations. If insufficient, report BLOCKED without expanding quota.
- [ ] Freeze current source and inputs, then invoke one `scripts/build.py --stage all` into a new output with the fixed verified local model. Preserve stdout/stderr/exit/resources/stage receipts; a real failure ends that attempt without code edits or continuation.
- [ ] Only after full/oracle/vector success run new-config `serve --check`, `check`, `check_dialogue`, three-config mode/citation/pagination/context/continuation checks. Verify exact five tools and all paths against this output.
- [ ] Deliver explicit PASS/FAIL/NOT_RUN/BLOCKED, exact code/environment, rows/size/SHA/time/peaks and failure limitations. No merge, push, deployment or product promotion.
