"""Reproducible scoped 7.1 core/product/Research construction, not promotion.

Retained outputs are no-clobber. All large inputs are existing qualified 7.1
artifacts; no accepted 7.0 database is copied or modified.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from genshin_lore.research_index import build_index, sha256_file
from genshin_lore.schema20_7_1_candidate import build
from genshin_lore.schema20_7_1_oracle import verify

ROOT = Path(__file__).resolve().parents[2]


def write_metadata(path, value, allow_identical=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if allow_identical and path.exists() and path.read_text(encoding="utf-8") == content:
        return
    with path.open("x", encoding="utf-8") as stream:
        stream.write(content)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--output-root", type=Path, required=True)
    ap.add_argument("--stage", choices=("core", "index"), required=True)
    ap.add_argument("--metadata-root", type=Path, default=ROOT,
                    help="Fresh metadata destination for reconstruction; does not overwrite tracked manifests/reports")
    ap.add_argument("--resume-metadata", action="store_true",
                    help="Reuse byte-identical generated metadata after an interrupted build; never overwrite an artifact")
    args = ap.parse_args()
    args.source = args.source.resolve()
    args.output_root = args.output_root.resolve()
    metadata_root = args.metadata_root.resolve()
    reports = metadata_root / "reports/research71-data-product"
    manifests = metadata_root / "manifests/research"
    def write(path, value):
        write_metadata(path, value, args.resume_metadata)
    def reference(path):
        return str(path.relative_to(metadata_root))
    args.output_root.mkdir(parents=True, exist_ok=True)
    if args.stage == "core":
        results = []
        for arm in ("a", "b"):
            path = args.output_root / f"schema20-{arm}.sqlite"
            report = build(args.source, path)
            write(reports / f"core-build-{arm}.json", report)
            oracle = verify(args.source, path, report)
            write(reports / f"core-oracle-{arm}.json", oracle)
            results.append(report)
        if results[0] != results[1]:
            raise ValueError("fresh core A/B mismatch")
        write(reports / "core-ab.json", {"status": "PASS", "artifact": results[0]["artifact"], "fresh_empty_schema20": True, "copied_7_0_database": False, "scope": "20/33 core contracts; not full Schema20 release qualification"})
        return
    core = json.loads((reports / "core-build-a.json").read_text())
    registry = json.loads((ROOT / "manifests/research-7.1/7.1-capability-registry-v1.json").read_text())
    r0 = Path("/tmp/genshin-lore-7.1-foundation/r0-b.sqlite")
    dep = json.loads((ROOT / "manifests/document-readable-7.1/source-scope.json").read_text())["r0_dependency"]
    components = [{"source_key": "schema20-scoped-core", "family": "schema20_core", "contract": "schema20-7.1-m3-materialization-v1", "parser": "assembly-only-not-evidence-parser", "run": "assembly-only-not-evidence-run", "artifact": core["artifact"], "runtime_manifest": reference(reports / "core-build-a.json"), "runtime_manifest_sha256": sha256_file(reports / "core-build-a.json")}]
    bindings = {"provenance_artifact": str(r0), "artifacts": {"schema20-scoped-core": str(args.output_root / "schema20-a.sqlite")}}
    for capability, runtime in (("readable_archive", "manifests/document-readable-7.1/candidate.json"), ("document_archive", "manifests/document-readable-7.1/candidate.json"), ("native_bulk", "manifests/native-archive-7.1/bulk-runtime.json")):
        c = next(x for x in registry["capabilities"] if x["capability_id"] == capability)
        # Bind the authoritative database by the exact candidate seal, not
        # by list order: registry entries include sibling/dependency artifacts.
        candidate = json.loads((ROOT / runtime).read_text())
        seal = candidate["artifact"] if capability == "native_bulk" else candidate[capability.removesuffix("_archive")]["artifact"]
        matches = [a for a in c["artifacts"] if (a["size_bytes"], a["sha256"]) == (seal["size_bytes"], seal["sha256"])]
        if len(matches) != 1:
            raise ValueError("explicit adapter artifact binding required")
        artifact = matches[0]
        key = "7.1-" + capability
        runtime_reference = runtime if metadata_root == ROOT else str(ROOT / runtime)
        components.append({"source_key": key, "family": c["family"], "contract": c["contract"], "parser": c["parser"], "run": c["run"], "artifact": {k: artifact[k] for k in ("size_bytes", "sha256")}, "runtime_manifest": runtime_reference, "runtime_manifest_sha256": sha256_file(ROOT / runtime), "limitations": c["limitations"]})
        bindings["artifacts"][key] = artifact["path"]
    bundle_path = metadata_root / "manifests/product/candidate-7.1-scoped-bundle-v1.json"
    bundle = {"contract": "candidate-7.1-scoped-product-bundle-v1", "status": "partial_candidate", "source_snapshot": registry["snapshot"], "product_accepted": False, "corpus_complete": False, "core_provider_contracts_materialized": 20, "core_provider_contracts_required": 33, "components": components, "coverage_note": "Three standalone contracts and 20 M3 core contracts only. Other qualified 7.1 capabilities are not silently represented by this product/index."}
    write(bundle_path, bundle)
    corpus_path = manifests / "candidate-7.1-scoped-v1.json"
    corpus = {"corpus_ref": "candidate-7.1-scoped-v1", "adapter_registry": "research-explicit-adapters-v2", "status": "candidate", "snapshot": {"id": registry["snapshot"], "version": "7.1"}, "product_bundle": {"path": reference(bundle_path), "sha256": sha256_file(bundle_path), "product_accepted": False}, "provenance_dependency": {k: dep[k] for k in ("size_bytes", "sha256")}, "components": components, "outer_corpus_complete": False, "terminal_unresolved": ["13 core contracts not materialized", "Character/Quest/Talk/Event/NPC/Mail/Codex and other standalone routes not migrated to this scoped Research index"]}
    write(corpus_path, corpus)
    bindings_path = args.output_root / "bindings.json"
    write(bindings_path, bindings)
    builds = []
    for arm in ("a", "b"):
        report = build_index(corpus_path, bindings_path, args.output_root / f"research-{arm}.sqlite")
        write(reports / f"index-build-{arm}.json", report)
        builds.append(report)
    if builds[0]["sha256"] != builds[1]["sha256"] or builds[0]["size_bytes"] != builds[1]["size_bytes"]:
        raise ValueError("Research A/B byte mismatch")
    with sqlite3.connect(args.output_root / "research-a.sqlite") as db:
        counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("source_artifact", "adapter_coverage", "research_container", "research_text", "research_relation", "research_candidate")}
        db.execute("INSERT INTO research_text_fts(research_text_fts, rank) VALUES('integrity-check', 1)")
    # The unchanged ResearchQuery prepublication API uses `accepted` for
    # profile qualification, even for non-product testing. Never install this
    # scoped profile as current; this is not acceptance of full 7.1 Research.
    profile = {"status": "accepted", "acceptance_scope": "prepublication_scoped_candidate_only", "corpus_ref": corpus["corpus_ref"], "corpus_manifest": reference(corpus_path), "evidence_product_bundle": reference(bundle_path), "evidence_product_bundle_sha256": sha256_file(bundle_path), "derived_index": {"size_bytes": builds[0]["size_bytes"], "sha256": builds[0]["sha256"], "counts": counts}, "product_accepted": False, "full_research_7_1_accepted": False, "candidate_scope_only": True}
    write(manifests / "candidate-7.1-scoped-profile-v1.json", profile)
    write(reports / "index-ab.json", {"status": "PASS", "artifact": profile["derived_index"], "scope": corpus["terminal_unresolved"], "fts_external_content_integrity": "PASS"})


if __name__ == "__main__":
    main()
