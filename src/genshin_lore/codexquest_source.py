"""Pinned physical source projection for the BinOutput/CodexQuest archive."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from .loading_tips_source import pairs
from .quest_narrative_source import ident as narrative_ident
from .source_integrity import pinned_file_seal

COMMIT = "26df1dfbdf05a82bbb1d97506859f3e1c40718d8"
TREE = "d891ef1d95fdd6c27a1da8a32ea7a61b483647a6"
SNAPSHOT = "anime-game-data:7.0:" + COMMIT
FAMILY = "codexquest_archive"
CONTRACT = "codexquest-archive-evidence-v1"
PREFIX = "BinOutput/CodexQuest"
MAIN = "ExcelBinOutput/MainQuestExcelConfigData.json"
QUEST_CODEX = "ExcelBinOutput/QuestCodexExcelConfigData.json"
LANGS = ("CHS", "EN", "JP")
TEXTMAPS = tuple(f"TextMap/TextMap{tier}{lang}.json" for lang in LANGS for tier in ("", "_Medium"))

SHAPES = {
    "rc_493": {
        "root_keys": ("ALOHJMPDFKI", "CBLIMBBBKNK", "DCNPPIOLEOK", "EBNBLBEIFFJ", "HEDPNHPBMJH", "IMJHJGBNMMD", "NNPJABOAJPL"),
        "id": "IMJHJGBNMMD", "type": "DCNPPIOLEOK", "sections": "EBNBLBEIFFJ",
        "section_keys": ("OGEGCCLHIHP", "PEAKPGNONFA"), "nodes": "PEAKPGNONFA",
        "locator_label": "JOBGILDNLEL", "locator_hash": "BNJEGIAOKGM",
    },
    "legacy_7": {
        "root_keys": ("EIGDJINAAME", "HAAMAIAEBPH", "HCOCNINCBEI", "HLCINEMBGEF", "IPENIGDFFAD", "JNMOLCMMGHN", "LDOJBIHMDAB"),
        "id": "HCOCNINCBEI", "type": "EIGDJINAAME", "sections": "HLCINEMBGEF",
        "section_keys": ("IIJIPINAOOF", "KHNBHADECGO"), "nodes": "IIJIPINAOOF",
        "locator_label": "LKNCCJIKPNP", "locator_hash": "LOFLLCHCMCG",
    },
    "alternate_3": {
        "root_keys": ("APEPAFOBDGB", "EIFDMLOMFHF", "HOBBPLPNMDP", "MEGJPCLADOG", "MHGNMKHOKHI", "NCNGHCODLAK", "PIAAIKBBCGB"),
        "id": "MHGNMKHOKHI", "type": "NCNGHCODLAK", "sections": "MEGJPCLADOG",
        "section_keys": ("FCEMMMMDKLP", "IIIBPOOMIOE"), "nodes": "FCEMMMMDKLP",
        "locator_label": "OKDLDDEBPOF", "locator_hash": "BKGOAJFIHCO",
    },
}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha(path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ident(*parts) -> str:
    return "codexquest:" + sha_bytes(canonical(parts).encode())[:40]


def load(path):
    return json.loads(Path(path).read_bytes().decode("utf-8-sig"))


def check_pin(root):
    values = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD", "HEAD^{tree}"], text=True
    ).splitlines()
    if values != [COMMIT, TREE]:
        raise ValueError("source pin")


def inventory(root):
    lines = subprocess.check_output(
        ["git", "-C", str(root), "ls-tree", "-rl", COMMIT, "--", PREFIX], text=True
    ).splitlines()
    result = []
    for line in lines:
        left, path = line.split("\t", 1)
        mode, kind, blob, size = left.split()
        if kind != "blob" or not path.endswith(".json"):
            raise ValueError("CodexQuest inventory contains unsupported object")
        result.append({"path": path, "blob": blob, "size_bytes": int(size)})
    return sorted(result, key=lambda item: item["path"])


def shape_of(value):
    keys = tuple(sorted(value)) if isinstance(value, dict) else ()
    matches = [name for name, shape in SHAPES.items() if keys == shape["root_keys"]]
    if len(matches) != 1:
        raise ValueError("unknown CodexQuest root shape " + repr(keys))
    return matches[0]


def _walk_locators(value, shape, pointer=""):
    spec = SHAPES[shape]
    if isinstance(value, dict):
        if set(value) == {spec["locator_label"], spec["locator_hash"]}:
            label = str(value[spec["locator_label"]])
            if label:
                yield pointer or "/", label, value[spec["locator_hash"]]
        for key, child in value.items():
            yield from _walk_locators(child, shape, pointer + "/" + key)
    elif isinstance(value, list):
        for ordinal, child in enumerate(value):
            yield from _walk_locators(child, shape, pointer + "/" + str(ordinal))


def _body_chunks(raw, chunk_size=49152):
    start = 0
    while start < len(raw):
        end = min(start + chunk_size, len(raw))
        while end > start:
            try:
                text = raw[start:end].decode("utf-8")
                break
            except UnicodeDecodeError:
                end -= 1
        if end == start:
            raise ValueError("unable to form UTF-8 body chunk")
        yield start, end, text
        start = end


def analyze(root):
    root = Path(root)
    check_pin(root)
    files = inventory(root)
    shapes, sections, nodes, owners = Counter(), 0, 0, 0
    largest = {"size_bytes": -1, "path": None}
    for item in files:
        seal = pinned_file_seal(root, COMMIT, item["path"])
        if seal["blob"] != item["blob"] or seal["size_bytes"] != item["size_bytes"]:
            raise ValueError("inventory seal mismatch")
        value = load(root / item["path"])
        shape = shape_of(value)
        spec = SHAPES[shape]
        section_values = value[spec["sections"]]
        if not isinstance(section_values, list):
            raise ValueError("section layout")
        shapes[shape] += 1
        sections += len(section_values)
        for section in section_values:
            if tuple(sorted(section)) != spec["section_keys"] or not isinstance(section[spec["nodes"]], list):
                raise ValueError("section shape")
            nodes += len(section[spec["nodes"]])
        owners += sum(1 for _ in _walk_locators(value, shape))
        if item["size_bytes"] > largest["size_bytes"]:
            largest = {"size_bytes": item["size_bytes"], "path": item["path"]}
    return {
        "physical_files": len(files), "total_source_bytes": sum(x["size_bytes"] for x in files),
        "shape_counts": dict(sorted(shapes.items())), "sections": sections, "nodes": nodes,
        "text_owners": owners, "expected_locale_slots": owners * len(LANGS), "largest_container": largest,
    }


def dependency_maps(root, core_artifact, narrative_artifact, narrative_manifest):
    root = Path(root)
    main = load(root / MAIN)
    narrative = load(narrative_manifest)
    nrun = narrative["release"]["run_id"]
    if Path(narrative_artifact).stat().st_size != narrative["artifact"]["size_bytes"] or sha(narrative_artifact) != narrative["artifact"]["sha256"]:
        raise ValueError("Quest Narrative dependency seal")
    ndb = sqlite3.connect(Path(narrative_artifact).absolute().as_uri() + "?mode=ro&immutable=1", uri=True)
    ndb.row_factory = sqlite3.Row
    main_targets = defaultdict(list)
    for ordinal, value in enumerate(main):
        oid = narrative_ident("container", MAIN, f"/{ordinal}")
        row = ndb.execute("select source_path,source_pointer,native_id from quest_narrative_container where id=? and run_id=?", (oid, nrun)).fetchone()
        if row is None or tuple(row) != (MAIN, f"/{ordinal}", str(value["id"])):
            raise ValueError("Quest Narrative MainQuest locator")
        main_targets[int(value["id"])].append((MAIN, f"/{ordinal}", str(value["id"]), "quest_narrative_archive", "quest-narrative-evidence-v1", nrun, oid, "exact_mainquest_navigation"))
    ndb.close()
    cdb = sqlite3.connect(Path(core_artifact).absolute().as_uri() + "?mode=ro&immutable=1", uri=True)
    cdb.row_factory = sqlite3.Row
    codex_targets = defaultdict(list)
    query = """select r.id,r.source_pointer,r.native_locator_value,rel.raw_value_json,r.ingest_run_id
               from quest_codex_row_occurrence r join quest_codex_relationship_occurrence rel
                 on rel.ingest_run_id=r.ingest_run_id and rel.source_row_occurrence_id=r.id
              where r.evidence_kind='quest_codex_entry' and rel.field_name='parentQuestId'
              order by r.source_pointer,r.id"""
    for row in cdb.execute(query):
        parent = int(json.loads(row["raw_value_json"]))
        codex_targets[parent].append((QUEST_CODEX, row["source_pointer"], str(row["native_locator_value"]), "quest_codex_archive", "quest-codex-archive-evidence-v1", row["ingest_run_id"], row["id"], "exact_reverse_questcodex_navigation"))
    cdb.close()
    return main_targets, codex_targets


def project(root, run, core_artifact, narrative_artifact, narrative_manifest):
    root = Path(root)
    check_pin(root)
    main_targets, codex_targets = dependency_maps(root, core_artifact, narrative_artifact, narrative_manifest)
    containers, body_chunks, children, relations, candidates, slots, text_occurrences, text_candidates = [], [], [], [], [], [], [], []
    wanted = set()
    source_values = []
    for item in inventory(root):
        path = item["path"]
        seal = pinned_file_seal(root, COMMIT, path)
        raw = (root / path).read_bytes()
        value = json.loads(raw.decode("utf-8-sig"))
        shape = shape_of(value)
        spec = SHAPES[shape]
        native, quest_type = value[spec["id"]], value[spec["type"]]
        cid = ident("container", run, path, "/")
        containers.append({"id": cid, "run_id": run, "shape": shape, "source_path": path, "source_pointer": "/", "source_blob_oid": seal["blob"], "source_size": len(raw), "source_sha256": seal["sha256"], "native_id": str(native), "quest_type": str(quest_type), "classification": "physical_codexquest_archive_processed"})
        for chunk_ordinal, (start, end, text) in enumerate(_body_chunks(raw)):
            body_chunks.append({"id": cid + f":body:{chunk_ordinal:06d}", "container_id": cid, "chunk_ordinal": chunk_ordinal, "byte_start": start, "byte_end": end, "raw_text": text, "raw_sha256": sha_bytes(raw[start:end])})
        sections_key = spec["sections"]
        for section_ordinal, section in enumerate(value[sections_key]):
            pointer = f"/{sections_key}/{section_ordinal}"
            children.append({"id": ident("child", run, path, pointer), "container_id": cid, "kind": "section", "source_pointer": pointer, "ordinal": section_ordinal, "raw_sha256": sha_bytes(canonical(section).encode()), "raw_json": canonical(section)})
            for node_ordinal, node in enumerate(section[spec["nodes"]]):
                node_pointer = pointer + f"/{spec['nodes']}/{node_ordinal}"
                children.append({"id": ident("child", run, path, node_pointer), "container_id": cid, "kind": "node", "source_pointer": node_pointer, "ordinal": node_ordinal, "raw_sha256": sha_bytes(canonical(node).encode()), "raw_json": canonical(node)})
        for pointer, label, locator in _walk_locators(value, shape):
            wanted.add(str(locator))
            source_values.append((cid, pointer, label, str(locator)))
        source_id_pointer = "/" + spec["id"]
        for kind, namespace, semantics, hits in (
            ("mainquest_equality_navigation", "observed equal raw values in complete pinned MainQuestExcel id namespace; source does not independently prove semantic identity", "cross-source equality navigation aid; not asserted as a source-authored CodexQuest relation", main_targets[int(native)]),
            ("questcodex_reverse_navigation", "all accepted QuestCodex primary rows by explicit parentQuestId", "reverse navigation from source-authored QuestCodex parentQuestId; not a CodexQuest-authored forward relation", codex_targets[int(native)]),
        ):
            rid = ident("relation", run, path, source_id_pointer, kind)
            status = "mapping_unresolved" if kind == "mainquest_equality_navigation" else "missing" if not hits else "resolved_unique" if len(hits) == 1 else "ambiguous"
            complete = 0 if kind == "mainquest_equality_navigation" else 1
            relations.append({"id": rid, "container_id": cid, "kind": kind, "source_pointer": source_id_pointer, "raw_value_json": canonical(native), "candidate_namespace": namespace, "status": status, "candidate_count": len(hits), "search_complete": complete, "relation_semantics": semantics})
            for ordinal, hit in enumerate(hits):
                locator_kind = "explicit_unresolved_equality_navigation_candidate" if kind == "mainquest_equality_navigation" else hit[7]
                candidates.append({"id": ident("candidate", rid, *hit[:7]), "relation_id": rid, "candidate_ordinal": ordinal, "target_source_path": hit[0], "target_source_pointer": hit[1], "target_native_id": hit[2], "target_family": hit[3], "target_contract": hit[4], "target_run": hit[5], "target_occurrence_id": hit[6], "locator_kind": locator_kind})
    maps = {}
    seals = {path: pinned_file_seal(root, COMMIT, path) for path in TEXTMAPS}
    for lang in LANGS:
        for tier in ("", "_Medium"):
            path = f"TextMap/TextMap{tier}{lang}.json"
            mapping = defaultdict(list)
            for ordinal, (key, text) in enumerate(pairs(root / path)):
                if key in wanted:
                    mapping[key].append((path, seals[path]["blob"], ordinal, "/" + key, text))
            maps[lang, tier] = mapping
    for container_id, pointer, label, locator in source_values:
        owner_field = label
        for lang in LANGS:
            main_hits = maps[lang, ""].get(locator, [])
            hits = main_hits or maps[lang, "_Medium"].get(locator, [])
            tier = "main" if main_hits else "medium"
            status = "missing" if not hits else ("resolved_" + tier) if len(hits) == 1 else "ambiguous"
            sid = ident("slot", container_id, pointer, lang)
            slots.append({"id": sid, "container_id": container_id, "source_pointer": pointer, "owner_field": owner_field, "locale": lang, "raw_locator": locator, "status": status, "candidate_count": len(hits)})
            for path, blob, ordinal, text_pointer, text in hits:
                oid = ident("text-occurrence", path, ordinal, text_pointer)
                text_occurrences.append({"id": oid, "source_path": path, "source_blob_oid": blob, "source_ordinal": ordinal, "source_pointer": text_pointer, "locale": lang, "shard_kind": tier, "raw_text": text, "raw_text_sha256": sha_bytes(text.encode())})
                text_candidates.append({"id": ident("text-candidate", sid, oid), "slot_id": sid, "text_occurrence_id": oid})
    unique_occurrences = {row["id"]: row for row in text_occurrences}
    return containers, body_chunks, children, relations, candidates, slots, [unique_occurrences[key] for key in sorted(unique_occurrences)], text_candidates


__all__ = ["COMMIT", "TREE", "SNAPSHOT", "FAMILY", "CONTRACT", "PREFIX", "MAIN", "QUEST_CODEX", "LANGS", "TEXTMAPS", "SHAPES", "canonical", "sha", "ident", "load", "inventory", "shape_of", "analyze", "project"]
