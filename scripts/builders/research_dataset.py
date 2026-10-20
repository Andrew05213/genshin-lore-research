"""Construct the final explicit bundle and one unified Research candidate.

Existing qualified artifacts are referenced, never copied. Metadata/output
creation is no-clobber. A/B are invoked separately for the storage gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from genshin_lore import research_index as old
from genshin_lore.research71_candidate_adapters import SPECS, VERSION, adapt, character
from genshin_lore.research71_candidate_relations import project, record_unsupported

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "manifests/research-7.1/7.1-capability-registry-v1.json"
CORPUS = ROOT / "manifests/research/candidate-7.1-complete-v1.json"
BUNDLE = ROOT / "manifests/product/candidate-7.1-complete-bundle-v1.json"


def write(path, value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)+"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text() != raw:
            raise FileExistsError(path)
        return
    with path.open("x") as f:
        f.write(raw)


def source(path):
    db = sqlite3.connect(path.resolve().as_uri()+"?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    return db


def select_artifact(cap, parent):
    matches = []
    for a in cap["artifacts"]:
        p = Path(a["path"])
        with source(p) as db:
            if parent not in old._tables(db):
                continue
            # Same table in NPC/Dialog/Reminder: select exact release run, not
            # sibling order or path spelling.
            if cap["route"] == "narrative" and cap["run"] not in str(tuple(db.execute("SELECT * FROM release").fetchone())):
                continue
        if p.stat().st_size != a["size_bytes"] or old.sha256_file(p) != a["sha256"]:
            raise ValueError("qualified capability artifact drift")
        matches.append(a)
    if len(matches) != 1:
        raise ValueError("exact projection artifact binding ambiguous: "+cap["capability_id"])
    return matches[0]


def metadata(output_root, extra_paths, *, input_metadata_root, provenance_artifact):
    """Historical qualified-input assembly; never reads another implicit deployment."""
    inputs = Path(input_metadata_root).resolve()
    metadata_root = Path(output_root).resolve() / "metadata"
    registry_path = inputs / REGISTRY.relative_to(ROOT)
    bundle_path = metadata_root / BUNDLE.relative_to(ROOT)
    corpus_path = metadata_root / CORPUS.relative_to(ROOT)
    registry = json.loads(registry_path.read_text())
    core = json.loads((output_root/"core-build-final-b.json").read_text())
    components = [{"source_key":"schema20-core-7.1", "family":"schema20_core", "contract":"schema20-7.1-core33-materialization-v1", "parser":"assembly-only-not-evidence-parser", "run":"assembly-only-not-evidence-run", "artifact":core["artifact"], "adapter":"core33", "runtime_manifest":str((output_root/"core-build-final-b.json").resolve()), "runtime_manifest_sha256":old.sha256_file(output_root/"core-build-final-b.json"), "limitations":["outer corpus incomplete", "row-level parser/run identity overrides aggregate assembly identity"]}]
    bindings = {"provenance_artifact":str(Path(provenance_artifact).resolve()), "artifacts":{"schema20-core-7.1":str(output_root/"schema20-final-b.sqlite")}}
    parent_specs = {"readable_archive":"readable_file","document_archive":"document_row","loading_tips":"loading_tip_row","mail_narrative":"mail_row","native_bulk":"native_archive_container","native_book_music":"native_archive_container","native_aux_quest":"native_archive_container"}
    for cap in registry["capabilities"]:
        name = cap["capability_id"]
        if name.startswith("character_"):
            continue
        parent = SPECS[name][0] if name in SPECS else parent_specs.get(name,"event_narrative_row")
        a = select_artifact(cap,parent);key="7.1-"+name
        c = {"source_key":key, "family":cap["family"],"contract":cap["contract"],"parser":cap["parser"],"run":cap["run"],"artifact":{k:a[k] for k in ("size_bytes","sha256")}, "adapter":name if name in SPECS else "formal_compatible", "runtime_manifest":str(registry_path),"runtime_manifest_sha256":old.sha256_file(registry_path),"limitations":cap["limitations"],"registered_capability_id":name,"source_qualification":cap["status"]}
        if name in {'event_physical_13','event_physical_16','event_physical_18'}:
            with source(Path(a['path'])) as db:
                physical=json.loads(db.execute('SELECT manifest_json FROM release').fetchone()[0])
            if (physical['parser_build_id'],physical['ingest_run_id'])!=(c['parser'],c['run']):
                raise ValueError('physical release differs from registered capability run/parser')
            c['registered_capability_contract']=c['contract']
            c['contract']=physical['contract']
        if name == "quest_talk_physical":
            c["registered_capability_family"] = c["family"]
            c["family"] = "quest_talk_pilot"  # explicit historical routed alias
        components.append(c);bindings["artifacts"][key]=a["path"]
    charpath = inputs/"manifests/character-7.1-successor/runtime.json"
    ch = json.loads(charpath.read_text())
    for role,a in ch["artifacts"].items():
        with source(Path(a["machine_local_path"])) as db:
            release = json.loads(db.execute("SELECT manifest_json FROM "+("foundation_release" if role=="foundation" else "release")).fetchone()[0])
        contract = release.get("contract",release.get("family_contract_version"))
        if not contract:
            raise ValueError("Character source release contract missing")
        key="7.1-character-"+role
        components.append({"source_key":key,"family":"character_archive","contract":contract,"parser":a["parser_build_id"],"run":a["ingest_run_id"],"artifact":{k:a[k] for k in ("size_bytes","sha256")},"adapter":"character_"+role if role in {"foundation","text"} else "dependency_only", "runtime_manifest":str(charpath),"runtime_manifest_sha256":old.sha256_file(charpath),"limitations":[ch["coverage"],"physical owner candidates are not logical identity"],"derived_role":role})
        bindings["artifacts"][key]=a["machine_local_path"]
    qr = json.loads((inputs/"manifests/quest-derived-7.1/quest-review-binding-v2.json").read_text())
    # Binding overlay is an independently sealed source, not a copied Review.
    cap = next(c for c in registry["capabilities"] if c["capability_id"]=="quest_review_archive")
    a = select_artifact(cap,"bound_locator")
    with source(Path(a["path"])) as db:
        rel = json.loads(db.execute("SELECT manifest_json FROM release").fetchone()[0])
    key="7.1-quest-review-binding"
    components.append({"source_key":key,"family":"quest_review_archive","contract":rel["contract"],"parser":rel["parser_build_id"],"run":rel["ingest_run_id"],"artifact":{k:a[k] for k in ("size_bytes","sha256")},"adapter":"quest_review_binding","runtime_manifest":str(inputs/"manifests/quest-derived-7.1/quest-review-binding-v2.json"),"runtime_manifest_sha256":old.sha256_file(inputs/"manifests/quest-derived-7.1/quest-review-binding-v2.json"),"limitations":["mapping_unresolved retained despite exact physical target binding"]});bindings["artifacts"][key]=a["path"]
    for role,relpath,parent in (("gcg-card","manifests/gcg-card-7.1/runtime.json","gcg_card_row"),("subtitle","manifests/direct-text-7.1/subtitle-runtime.json","subtitle_file")):
        m=json.loads((inputs/relpath).read_text())
        a=extra_paths[role].resolve()
        if a.stat().st_size!=m['artifact']['size_bytes'] or old.sha256_file(a)!=m['artifact']['sha256']:
            raise ValueError("explicit qualified "+role+" seal mismatch")
        key="7.1-"+role
        components.append({"source_key":key,"family":m["family"],"contract":m["family_contract_version"],"parser":m["parser_build_id"],"run":m["ingest_run_id"],"artifact":m["artifact"],"adapter":"formal_compatible","runtime_manifest":str(inputs/relpath),"runtime_manifest_sha256":old.sha256_file(inputs/relpath),"limitations":["declared source scope only"]});bindings["artifacts"][key]=str(a)
    talk=json.loads((output_root/"talk-build-b.json").read_text());key="7.1-talk-physical"
    components.append({"source_key":key,"family":talk["family"],"contract":talk["family_contract_version"],"parser":talk["parser_build_id"],"run":talk["run_id"],"artifact":talk["artifact"],"adapter":"talk_physical","runtime_manifest":str((output_root/"talk-build-b.json").resolve()),"runtime_manifest_sha256":old.sha256_file(output_root/"talk-build-b.json"),"limitations":["wrapper equality candidates; terminal structures unresolved; no logical Talk identity"]});bindings["artifacts"][key]=str(output_root/"talk-physical-b.sqlite")
    bundle={"contract":"candidate-7.1-complete-product-bundle-v1","status":"candidate_pending_qualification","product_accepted":False,"corpus_complete":False,"snapshot":registry["snapshot"],"core_provider_contracts_materialized":33,"components":components,"registered_capabilities_reused":30,"coverage_counting":"source qualification, assembly, runtime and Research qualification are separate; partial is not exact replacement"}
    write(bundle_path,bundle)
    dep=json.loads((ROOT/"manifests/document-readable-7.1/source-scope.json").read_text())["r0_dependency"]
    provenance = Path(provenance_artifact)
    if (provenance.stat().st_size, old.sha256_file(provenance)) != (dep['size_bytes'], dep['sha256']):
        raise ValueError('explicit historical provenance artifact drift')
    manifest={"corpus_ref":"candidate-7.1-high-value-v1","status":"candidate","adapter_registry":"research-explicit-adapters-v2","candidate_adapter_revision":VERSION,"adapter_implementation_sha256":{p.name:old.sha256_file(p) for p in sorted((ROOT/"src/genshin_lore").glob("research71_candidate_*.py")) if p.stem!='research71_candidate_query'},"snapshot":{"id":registry["snapshot"],"version":"7.1"},"product_bundle":{"path":str(bundle_path.relative_to(metadata_root)),"sha256":old.sha256_file(bundle_path),"product_accepted":False},"provenance_dependency":{k:dep[k] for k in ("size_bytes","sha256")},"components":components,"outer_corpus_complete":False,"terminal_unresolved":["source-qualified partial capabilities retain historical semantic gaps", "unsupported relationship scopes are inventoried with complete rowset seals", "snapshot existence is not first release evidence"]}
    write(corpus_path,manifest);write(output_root/"complete-bindings.json",bindings)
    return manifest,bindings


def core_mappings(corpus_path):
    metadata_root = Path(corpus_path).resolve().parents[2]
    result = []
    for name in ('m3-7.1-provider', 'special-core-7.1'):
        path = metadata_root/'manifests'/name/'accepted-exact-mappings-v1.json'
        result.extend(json.loads(path.read_text())['mappings'])
    if len(result) != 33:
        raise ValueError('core contract mapping denominator changed')
    return result


def apply_core_contracts(out, source_key, mappings):
    """Reconcile exact parser/run contracts in two scans, not 33 per table.

    The temporary lookup is build-local infrastructure, never evidence or a
    persistent schema change. Rows without an explicit binding stay unchanged.
    """
    bindings = {}
    for m in mappings:
        key = (m['parser_build_id'], m['ingest_run_id'])
        if key in bindings and bindings[key] != m['provider_contract']:
            raise ValueError('conflicting exact core parser/run contract')
        bindings[key] = m['provider_contract']
    out.execute('CREATE TEMP TABLE core_contract_binding(parser TEXT,run TEXT,contract TEXT,PRIMARY KEY(parser,run)) WITHOUT ROWID')
    try:
        out.executemany('INSERT INTO core_contract_binding VALUES(?,?,?)', [
            (parser, run, contract) for (parser, run), contract in bindings.items()])
        for table in ('research_container','research_text'):
            out.execute(f'''UPDATE {table} SET evidence_contract=(
                SELECT contract FROM core_contract_binding b
                WHERE b.parser={table}.evidence_parser AND b.run={table}.evidence_run)
                WHERE source_key=? AND EXISTS(
                SELECT 1 FROM core_contract_binding b
                WHERE b.parser={table}.evidence_parser AND b.run={table}.evidence_run)''', (source_key,))
    finally:
        out.execute('DROP TABLE core_contract_binding')


def project_component(src, out, component, r0, counts, *, corpus_path):
    """One adapter's real projection path, also usable with schema-only fixtures.

    Qualification and publication stay in build(); this does not grant source
    acceptance or let callers skip the independent complete oracle.
    """
    cs, ts, rs, ks = counts
    key, mode = component['source_key'], component['adapter']
    mapping = {}
    record_unsupported(src,out,key,component)
    if mode == 'core33':
        cs,ts = old._core(src,out,key,component,r0,cs,ts)
        apply_core_contracts(out,key,core_mappings(corpus_path))
    elif mode in ('formal_compatible','talk_physical'):
        cs,ts,mapping = old._component(src,out,key,component,r0,cs,ts)
        if mode == 'formal_compatible':
            rs,ks = old._relations(src,out,key,component,mapping,rs,ks)
    elif mode.startswith('character_'):
        cs,ts,mapping = character(src,out,key,component,r0,cs,ts)
    elif mode == 'quest_review_binding':
        mapping = {r['occurrence_id']:r['id'] for r in out.execute(
            "SELECT * FROM research_container WHERE source_key='7.1-quest_review_archive'")}
    elif mode != 'dependency_only':
        cs,ts,mapping = adapt(src,out,key,component,r0,cs,ts)
    if mode not in {'formal_compatible','core33','dependency_only','character_text'}:
        rs,ks = project(src,out,key,component,mapping,rs,ks)
    return cs,ts,rs,ks


def build(manifest,bindings,output,available_allocated_bytes, *, corpus_path, reports_dir):
    corpus_path = Path(corpus_path)
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    started=time.perf_counter()
    if output.exists() or any(Path(str(output)+s).exists() for s in ("-wal","-shm","-journal")):
        raise FileExistsError(output)
    prov=source(Path(bindings["provenance_artifact"]));r0={str(p):(str(s),None) for p,s in prov.execute("SELECT relative_path,source_sha256 FROM source_file")};prov.close()
    # Complete deterministic construction in RAM avoids an on-disk FTS
    # staging peak. Exact page size is known before exclusive publication.
    out=sqlite3.connect(':memory:');out.row_factory=sqlite3.Row;out.executescript(old.SCHEMA);out.execute('PRAGMA temp_store=MEMORY');cs=ts=rs=ks=0
    try:
        for c in manifest["components"]:
            key=c["source_key"];path=Path(bindings["artifacts"][key]);seal=c["artifact"]
            if path.stat().st_size!=seal["size_bytes"] or old.sha256_file(path)!=seal["sha256"] or any(Path(str(path)+s).exists() for s in ("-wal","-shm","-journal")):
                raise ValueError("candidate source seal/sidecar drift: "+key)
            out.execute("INSERT INTO source_artifact VALUES(?,?,?,?,?,?,?,?)",(key,c["family"],c["contract"],c["parser"],c["run"],str(path),seal["size_bytes"],seal["sha256"]))
            src=source(path)
            try:
                cs,ts,rs,ks=project_component(src,out,c,r0,(cs,ts,rs,ks),corpus_path=corpus_path)
            finally:
                src.close()
            out.commit();print(key,cs,ts,rs,ks,flush=True)
        out.execute("INSERT INTO research_text_fts(rowid,raw_text) SELECT id,raw_text FROM research_text")
        out.execute("INSERT INTO research_text_fts(research_text_fts,rank) VALUES('integrity-check',1)")
        out.execute("INSERT INTO corpus_release VALUES(1,?,?,?,?,?,?,?,?,?)",(old.sha256_file(corpus_path),manifest["corpus_ref"],"research-explicit-adapters-v2","deterministic-no-wall-clock",len(manifest["components"]),cs,ts,rs,ks));out.commit()
        # The independent oracle consumes the complete rowset even when disk
        # retention is blocked. It imports no projection functions from here.
        import runpy
        oracle = runpy.run_path(str(ROOT/'scripts/validators/research_dataset.py'))['verify_connection']
        print('independent complete source oracle starting',flush=True)
        oracle_result = oracle(out,manifest,bindings)
        before_compaction=out.execute('PRAGMA page_count').fetchone()[0]*out.execute('PRAGMA page_size').fetchone()[0]
        # Standard SQLite compaction changes neither schema nor row identity.
        # The temporary image stays in RAM, not in a second on-disk index.
        out.execute('VACUUM')
        exact_bytes=out.execute('PRAGMA page_count').fetchone()[0]*out.execute('PRAGMA page_size').fetchone()[0]
        write(reports_dir/f"index-memory-oracle-{output.stem}.json",oracle_result)
        capacity={'index_before_compaction_bytes':before_compaction,'index_after_compaction_bytes':exact_bytes,'available_allocated_bytes':available_allocated_bytes,'publication_reserve_bytes':16*1024**2,'fits':exact_bytes+16*1024**2<=available_allocated_bytes,'memory_only_before_gate':True}
        write(reports_dir/f"index-capacity-{output.stem}.json",capacity)
        if exact_bytes+16*1024**2>available_allocated_bytes:
            raise RuntimeError(f'hard storage gate: index {exact_bytes} B + 16 MiB publication reserve exceeds available {available_allocated_bytes} B')
        import os
        fd=os.open(output,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600);os.close(fd)
        target = None
        try:
            target=sqlite3.connect(output);target.execute('PRAGMA journal_mode=OFF')
            out.backup(target)
        finally:
            if target is not None:
                target.close()
    except Exception:
        out.close();raise
    out.close()
    return {"status":"BUILT_AWAITING_INDEPENDENT_ORACLE","corpus_ref":manifest["corpus_ref"],"size_bytes":output.stat().st_size,"sha256":old.sha256_file(output),"container_rows":cs,"text_rows":ts,"relation_rows":rs,"candidate_rows":ks,"seconds":time.perf_counter()-started}


def main():
    ap=argparse.ArgumentParser();ap.add_argument("--input-metadata-root",type=Path,required=True);ap.add_argument("--provenance-artifact",type=Path,required=True);ap.add_argument("--output-root",type=Path,required=True);ap.add_argument("--arm",choices=("a","b"),required=True);ap.add_argument("--metadata-only",action="store_true");ap.add_argument('--gcg-artifact',type=Path,required=True);ap.add_argument('--subtitle-artifact',type=Path,required=True);ap.add_argument('--available-allocated-bytes',type=int,default=0,help='Fresh inode-deduplicated audit hard headroom, not host free space');args=ap.parse_args()
    m,b=metadata(args.output_root,{'gcg-card':args.gcg_artifact,'subtitle':args.subtitle_artifact},input_metadata_root=args.input_metadata_root,provenance_artifact=args.provenance_artifact)
    if not args.metadata_only:
        if args.available_allocated_bytes<=0:raise ValueError('fresh project storage audit required')
        reports = args.output_root / "index-oracles"
        corpus = args.output_root / "metadata" / CORPUS.relative_to(ROOT)
        write(reports/f"index-build-{args.arm}.json",build(m,b,args.output_root/f"research-final-{args.arm}.sqlite",args.available_allocated_bytes,corpus_path=corpus,reports_dir=reports))


if __name__=="__main__":main()
