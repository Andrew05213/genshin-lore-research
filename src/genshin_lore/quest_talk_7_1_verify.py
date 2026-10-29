"""Independent full-source verifier for the 7.1 Quest/Talk successor.

No builder or 7.1 decoder projection function is imported here.  Physical
coordinates and source-authored fields are reconstructed from pinned bytes.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path

from .sealed_sqlite import open_immutable_readonly

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
TREE = "aa578c08c402af39b41f1ffc95124d10f5410272"
SNAPSHOT = f"anime-game-data:7.1:{COMMIT}"
CONTRACT = "quest-talk-7.1-standalone-physical-v1"
MAIN = "ExcelBinOutput/MainQuestExcelConfigData.json"


def enc(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def hash_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def hash_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ident(kind, *parts):
    return kind + ":" + hash_bytes(enc(parts).encode())[:40]


def root_signature_digest(doc):
    return hash_bytes(enc(sorted(doc)).encode())


def require(value, reason):
    if not value:
        raise ValueError(reason)


def at(value, pointer):
    if pointer == "/":
        return value
    for part in pointer.strip("/").split("/"):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def numeric(value, field):
    require(type(value) is int and value >= 0, field + " must be a nonnegative physical integer")
    return value


def indexed_tree(root, prefix):
    raw = subprocess.check_output(["git", "-C", str(root), "ls-tree", "-r", "-z", COMMIT, "--", prefix])
    result = {}
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        meta, path = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        require(mode == "100644" and kind == "blob", "unsupported source tree entry")
        result[path.decode()] = oid
    return result


def physical_nodes(values, field, id_field, next_field, hash_field, text_mapping):
    require(isinstance(values, list), "physical dialogue list shape")
    result = []
    for ordinal, row in enumerate(values):
        require(isinstance(row, dict), "physical dialogue child shape")
        native = numeric(row[id_field], "dialogue native ID")
        successors = row.get(next_field, [])
        require(isinstance(successors, list), "physical dialogue successor list")
        for target in successors:
            numeric(target, "dialogue successor")
        locator = row.get(hash_field)
        if locator is not None:
            numeric(locator, "dialogue text hash")
        result.append({
            "ordinal": ordinal, "pointer": f"/{field}/{ordinal}",
            "native_id": native, "next": successors, "text_hash": locator,
            "text_mapping": text_mapping, "raw": row,
        })
    return result


def physical_references(values, field, id_field, init_field):
    require(isinstance(values, list), "physical reference list shape")
    result = []
    for ordinal, row in enumerate(values):
        require(isinstance(row, dict), "physical reference child shape")
        native = numeric(row[id_field], "reference native ID")
        if init_field is not None and init_field in row:
            numeric(row[init_field], "reference init dialogue")
        result.append({
            "pointer": "/" if field is None else f"/{field}/{ordinal}",
            "native_id": native, "raw": row,
        })
    return result


def decode_talk_source(doc, old_contract):
    require(isinstance(doc, dict), "Talk source root object")
    keys = set(doc)
    if "PCIAMAFDDAA" in keys:
        require(keys <= {"PCIAMAFDDAA", "talkId", "type"}, "unregistered 7.1 Talk root")
        native = numeric(doc["talkId"], "Talk root ID") if "talkId" in doc else None
        require(native is not None or keys == {"PCIAMAFDDAA", "type"}, "unregistered no-ID Talk root")
        return "talk_root", "7.1-direct", native, physical_nodes(
            doc["PCIAMAFDDAA"], "PCIAMAFDDAA", "id", "GLJCECCOEDP", "LKECPJIFFEE", "literal_key",
        ), []
    if "DLLABGGCEBM" in keys:
        require(keys <= {"DLLABGGCEBM", "GCNCAGHDDOJ", "NJLNCONMABL", "groupId", "JNNALCLEEPD", "activityId"}, "unregistered 7.1 wrapper")
        return "reference_wrapper", "7.1-wrapper", None, [], physical_references(doc["DLLABGGCEBM"], "DLLABGGCEBM", "id", "NNEHBCLEGHG")
    if "DGAIPHFGBOD" in keys:
        allowed = {"DGAIPHFGBOD", "FAPCNCGCEBJ", "GCNCAGHDDOJ", "GIFBNGBODIM", "JEDNDGCOMGC", "KJHEKKNGOOG", "NAGBOCFAGLL", "NBKACBDNMMJ", "NCBFPFHGHOH", "NNEHBCLEGHG", "PNNEOGAMPLE", "activeMode", "id", "priority", "questId"}
        require(keys <= allowed and "id" in doc, "unregistered direct Talk reference")
        return "reference_wrapper", "7.1-direct-reference", None, [], physical_references([doc], None, "id", "NNEHBCLEGHG")
    if keys == {"talkId"}:
        return "mapping_unresolved", "7.1-terminal", numeric(doc["talkId"], "terminal Talk ID"), [], []
    old = {tuple(item["root_keys"]): item for item in old_contract["shapes"]}
    spec = old.get(tuple(sorted(doc)))
    require(spec is not None, "unregistered historical Talk root in 7.1")
    mapping = spec["mapping"]
    if spec["kind"] == "talk_root":
        fields = old_contract["dialogue_fields"][mapping]
        native = numeric(doc[fields["root_id"]], "historical Talk ID")
        nodes = physical_nodes(doc.get(fields["list"], []), fields["list"], fields["node_id"], fields["next"], fields["text_hash"], fields.get("text_mapping", "literal_key"))
        return "talk_root", mapping, native, nodes, []
    if spec["kind"] == "reference_wrapper":
        fields = old_contract["reference_fields"][mapping]
        field = spec["reference_field"]
        rows = [doc] if field is None else doc[field]
        return "reference_wrapper", mapping, None, [], physical_references(rows, field, fields["id"], fields["init"])
    return "mapping_unresolved", mapping or "legacy-terminal-unresolved", None, [], []


def decode_quest_source(doc, old_contract):
    require(isinstance(doc, dict), "Quest source root object")
    if "id" in doc and "JIJKODHIEED" in doc:
        native = numeric(doc["id"], "Quest root ID")
        child_field, ref_field = "JIJKODHIEED", "DLLABGGCEBM"
        children = doc[child_field]
        refs = physical_references(doc[ref_field], ref_field, "id", "NNEHBCLEGHG") if ref_field in doc else []
    elif {"id", "luaPath", "KJBJDKALING", "KJOHNNANDBK"} <= set(doc) and "JIJKODHIEED" not in doc:
        native = numeric(doc["id"], "Quest root ID")
        child_field, ref_field, children = None, "DLLABGGCEBM", []
        refs = physical_references(doc[ref_field], ref_field, "id", "NNEHBCLEGHG") if ref_field in doc else []
    else:
        found = [fields for fields in old_contract["quest_fields"].values() if fields["references"] in doc and fields["subquests"] in doc]
        require(len(found) == 1, "unregistered Quest shape")
        fields = found[0]
        native = numeric(doc[fields["id"]], "historical Quest root ID")
        child_field, ref_field = fields["subquests"], fields["references"]
        children = doc[child_field]
        ref_mapping = "rc" if ref_field == "OJACLOOEAMG" else "alt"
        refspec = old_contract["reference_fields"][ref_mapping]
        refs = physical_references(doc[ref_field], ref_field, refspec["id"], refspec["init"])
    require(isinstance(children, list) and all(isinstance(row, dict) for row in children), "Quest physical child array")
    embedded = []
    fields = [name for name in ("PCIAMAFDDAA", "PFALHAKIILD", "GALIDJOEHOC") if name in doc]
    require(len(fields) <= 1, "multiple Quest embedded physical containers")
    if fields:
        name = fields[0]
        if name == "PCIAMAFDDAA":
            mapping = "7.1-direct"
            nodes = physical_nodes(doc[name], name, "id", "GLJCECCOEDP", "LKECPJIFFEE", "literal_key")
        else:
            mapping = "rc" if name == "PFALHAKIILD" else "alt"
            spec = old_contract["dialogue_fields"][mapping]
            nodes = physical_nodes(doc[name], name, spec["node_id"], spec["next"], spec["text_hash"], spec.get("text_mapping", "literal_key"))
        embedded.append((name, mapping, nodes))
    return native, child_field, children, refs, embedded


def textmap_pairs(raw):
    pairs = json.loads(raw.decode("utf-8"), object_pairs_hook=lambda items: items)
    require(isinstance(pairs, list), "TextMap root object")
    for key, value in pairs:
        require(isinstance(key, str) and isinstance(value, str), "TextMap string pair")
    return pairs


def verify(source_root: Path, scope_path: Path, artifact: Path, build_report: dict) -> dict:
    started = time.monotonic()
    root, scope_path, artifact = Path(source_root).resolve(), Path(scope_path).resolve(), Path(artifact).resolve()
    scope_raw = scope_path.read_bytes()
    scope = json.loads(scope_raw)
    require(scope["contract"] == CONTRACT and scope["source_commit"] == COMMIT and scope["source_tree"] == TREE, "scope source pin")
    require(subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD", "HEAD^{tree}"], text=True).splitlines() == [COMMIT, TREE], "source checkout pin")
    require(not subprocess.check_output(["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"], text=True).strip(), "source checkout dirty")
    require(not any(Path(str(artifact) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")), "SQLite sidecar exists")
    artifact_stat = artifact.stat()
    before = hash_file(artifact)
    require(build_report["artifact"] == {"size_bytes": artifact_stat.st_size, "sha256": before}, "candidate artifact seal")
    require(build_report["snapshot_id"] == SNAPSHOT and build_report["source_commit"] == COMMIT and build_report["source_tree"] == TREE, "candidate source identity")
    require(build_report["scope_sha256"] == hash_bytes(scope_raw), "candidate scope seal")
    implementation = hash_bytes(enc({
        "decoder": hash_file(ROOT / "src/genshin_lore/quest_talk_7_1_physical.py"),
        "builder": hash_file(ROOT / "src/genshin_lore/quest_talk_7_1_build.py"),
        "schema": hash_file(ROOT / "schemas/quest-talk-7.1-standalone-v1.sql"),
        "root_signatures": hash_file(ROOT / "manifests/quest-talk-7.1/root-signature-digests.json"),
    }).encode())
    require(build_report["implementation_sha256"] == implementation, "candidate implementation seal")
    parser_id = ident("parser-build", CONTRACT, implementation)
    run_id = ident("ingest-run", SNAPSHOT, parser_id, hash_bytes(scope_raw))
    require(build_report["parser_build_id"] == parser_id and build_report["ingest_run_id"] == run_id, "candidate parser/run identity")
    blobs = indexed_tree(root, "BinOutput/Quest") | indexed_tree(root, "BinOutput/Talk")
    blobs.update(indexed_tree(root, "TextMap"))
    blobs.update(indexed_tree(root, MAIN))
    quest_paths = sorted(path for path in blobs if path.startswith("BinOutput/Quest/") and path.endswith(".json"))
    talk_paths = sorted(path for path in blobs if path.startswith("BinOutput/Talk/") and path.endswith(".json"))
    quest_set, talk_set = set(quest_paths), set(talk_paths)
    for name, paths, directory in (("quest", quest_paths, "Quest"), ("talk", talk_paths, "Talk")):
        physical = {path.relative_to(root).as_posix() for path in (root / "BinOutput" / directory).rglob("*.json")}
        require(set(paths) == physical, name + " physical path set")
        require(len(paths) == scope["physical_scope"][name]["file_count"], name + " physical denominator")
        require(hash_bytes((enc(paths) + "\n").encode()) == scope["physical_scope"][name]["path_set_sha256"], name + " physical path seal")
    old_contract = json.loads((ROOT / "manifests/quest-talk-physical/shapes-v1.json").read_text())
    signature_binding = scope["root_signature_manifest"]
    signature_path = ROOT / signature_binding["path"]
    require(hash_file(signature_path) == signature_binding["sha256"], "root signature manifest seal")
    signature_contract = json.loads(signature_path.read_text())
    require(signature_contract["contract"] == "quest-talk-7.1-observed-root-signatures-v1" and signature_contract["source_commit"] == COMMIT and signature_contract["source_tree"] == TREE, "root signature source identity")
    signatures = {kind: set(signature_contract[kind]["signature_digests"]) for kind in ("quest", "talk")}
    for kind in ("quest", "talk"):
        require(len(signatures[kind]) == signature_binding[kind + "_distinct_signatures"], kind + " signature denominator")
    conn = open_immutable_readonly(artifact)
    conn.row_factory = sqlite3.Row
    try:
        require(conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite integrity")
        require(not conn.execute("PRAGMA foreign_key_check").fetchall(), "SQLite foreign keys")
        expected_schema = sqlite3.connect(":memory:")
        expected_schema.executescript((ROOT / "schemas/quest-talk-7.1-standalone-v1.sql").read_text())
        structure_sql = "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        require([tuple(row) for row in conn.execute(structure_sql)] == expected_schema.execute(structure_sql).fetchall(), "SQLite schema structure")
        expected_schema.close()
        release = conn.execute("SELECT * FROM release").fetchall()
        require(len(release) == 1 and release[0]["singleton"] == 1, "single release record")
        release = release[0]
        for field, value in (
            ("contract", CONTRACT), ("snapshot_id", SNAPSHOT), ("source_commit", COMMIT),
            ("source_tree", TREE), ("parser_build_id", build_report["parser_build_id"]),
            ("ingest_run_id", build_report["ingest_run_id"]), ("scope_sha256", hash_bytes(scope_raw)),
            ("implementation_sha256", build_report["implementation_sha256"]),
            ("counts_json", enc(build_report["counts"])), ("state_json", enc(build_report["states"])),
        ):
            require(release[field] == value, "release " + field)
        run = release["ingest_run_id"]
        required_sources = quest_set | talk_set | {MAIN}
        seen_sources = set()
        for row in conn.execute("SELECT * FROM source_file ORDER BY path"):
            path = row["path"]
            require(path in required_sources and path not in seen_sources, "source file extra or duplicate")
            seen_sources.add(path)
            raw = (root / path).read_bytes()
            blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            kind = "quest" if path in quest_set else "talk" if path in talk_set else "main_quest"
            require(blob == blobs[path], "source Git blob differs from pinned tree")
            require((row["snapshot_id"], row["kind"], row["git_blob_sha1"], row["sha256"], row["size_bytes"], row["raw_bytes"]) == (SNAPSHOT, kind, blob, hash_bytes(raw), len(raw), raw), "source file physical bytes/provenance")
        require(seen_sources == required_sources, "source file omissions")

        containers = {}
        children = {}
        nodes = {}
        refs = {}
        root_index = defaultdict(list)
        local_index = defaultdict(lambda: defaultdict(list))
        global_index = defaultdict(list)
        wanted = set()
        source_states = Counter()
        observed_signatures = {"quest": set(), "talk": set()}

        def put_container(path, pointer, kind, mapping, native, raw_value):
            cid = ident("qt71-container", run, path, pointer)
            raw = (root / path).read_bytes() if pointer == "/" else enc(raw_value).encode()
            require(cid not in containers, "container identity collision")
            containers[cid] = (run, path, pointer, kind, mapping, None if native is None else str(native), hash_bytes(raw))
            if kind in ("talk_root", "mapping_unresolved") and native is not None:
                root_index[str(native)].append({"path": path, "pointer": "/", "container_id": cid, "kind": kind})
            return cid

        def put_node(path, cid, item):
            nid = ident("qt71-node", run, path, item["pointer"])
            require(nid not in nodes, "node identity collision")
            nodes[nid] = {
                "container_id": cid, "run": run, "ordinal": item["ordinal"], "pointer": item["pointer"],
                "native_id": str(item["native_id"]), "text_hash": None if item["text_hash"] is None else str(item["text_hash"]),
                "text_mapping": item["text_mapping"], "raw_json": enc(item["raw"]), "next": item["next"],
            }
            local_index[cid][str(item["native_id"])].append(nid)
            global_index[str(item["native_id"])].append({"path": path, "pointer": item["pointer"], "node_id": nid})
            if item["text_hash"] is not None:
                wanted.add(str(item["text_hash"]))

        def put_ref(path, cid, ref, kind):
            rid = ident("qt71-ref", run, path, ref["pointer"])
            require(rid not in refs, "reference identity collision")
            refs[rid] = (cid, run, ref["pointer"], kind, str(ref["native_id"]), enc(ref["raw"]))

        for path in quest_paths:
            doc = json.loads((root / path).read_bytes().decode("utf-8-sig"))
            signature = root_signature_digest(doc)
            require(signature in signatures["quest"], "unregistered pinned Quest root signature")
            observed_signatures["quest"].add(signature)
            native, child_field, physical_children, references, embedded = decode_quest_source(doc, old_contract)
            if child_field is None:
                source_states["quest_child_field_absent"] += 1
            if "id" in doc and "DLLABGGCEBM" not in doc and ("JIJKODHIEED" in doc or child_field is None):
                source_states["quest_reference_field_absent"] += 1
            cid = put_container(path, "/", "quest_root", "7.1-versioned", native, doc)
            for ordinal, child in enumerate(physical_children):
                pointer = f"/{child_field}/{ordinal}"
                raw = enc(child)
                children[ident("qt71-child", run, path, pointer)] = (cid, run, ordinal, pointer, raw, hash_bytes(raw.encode()))
            for ref in references:
                put_ref(path, cid, ref, "quest_to_talk")
            for field, mapping, embedded_nodes in embedded:
                eid = put_container(path, "/" + field, "embedded_dialogue", mapping, None, doc[field])
                for node in embedded_nodes:
                    put_node(path, eid, node)
        for path in talk_paths:
            doc = json.loads((root / path).read_bytes().decode("utf-8-sig"))
            signature = root_signature_digest(doc)
            require(signature in signatures["talk"], "unregistered pinned Talk root signature")
            observed_signatures["talk"].add(signature)
            kind, mapping, native, physical_nodes_list, references = decode_talk_source(doc, old_contract)
            if kind == "talk_root" and native is None:
                source_states["talk_root_no_native_id"] += 1
            cid = put_container(path, "/", kind, mapping, native, doc)
            for node in physical_nodes_list:
                put_node(path, cid, node)
            for ref in references:
                put_ref(path, cid, ref, "wrapper_to_talk")
        require(observed_signatures == signatures, "root signature no-extra/no-omission closure")

        main_raw = (root / MAIN).read_bytes()
        require(len(main_raw) == scope["main_quest_source"]["size_bytes"] and hash_bytes(main_raw) == scope["main_quest_source"]["sha256"], "MainQuest support source seal")
        main_rows = json.loads(main_raw.decode("utf-8-sig"))
        require(isinstance(main_rows, list) and len(main_rows) == scope["main_quest_source"]["physical_rows"], "MainQuest physical row denominator")
        expected_main = {}
        for ordinal, row in enumerate(main_rows):
            require(isinstance(row, dict), "MainQuest row object")
            native = numeric(row["id"], "MainQuest row ID")
            pointer = f"/{ordinal}"
            expected_main[ident("qt71-mainquest", run, MAIN, pointer)] = (run, MAIN, ordinal, pointer, str(native), enc(row))

        def check_table(table, expected, fields):
            seen = set()
            for row in conn.execute("SELECT * FROM " + table):
                key = row["id"]
                require(key in expected and key not in seen, table + " extra or duplicate")
                seen.add(key)
                actual = tuple(row[field] for field in fields)
                require(actual == expected[key], table + " row mismatch: " + key)
            require(seen == set(expected), table + " omission")

        check_table("physical_container", containers, ("ingest_run_id", "source_path", "pointer", "kind", "mapping", "native_id", "raw_sha256"))
        check_table("quest_child", children, ("container_id", "ingest_run_id", "ordinal", "pointer", "raw_json", "raw_sha256"))
        check_table("main_quest_row", expected_main, ("ingest_run_id", "source_path", "ordinal", "pointer", "native_id", "raw_json"))

        node_seen = set()
        graph = Counter()
        for row in conn.execute("SELECT * FROM talk_node"):
            nid = row["id"]
            require(nid in nodes and nid not in node_seen, "talk node extra or duplicate")
            node_seen.add(nid)
            item = nodes[nid]
            for field, value in (
                ("container_id", item["container_id"]), ("ingest_run_id", run), ("ordinal", item["ordinal"]),
                ("pointer", item["pointer"]), ("native_id", item["native_id"]), ("text_hash", item["text_hash"]),
                ("text_mapping", item["text_mapping"]), ("raw_json", item["raw_json"]),
            ):
                require(row[field] == value, "talk node " + field)
            expected_successors = []
            for ordinal, target in enumerate(item["next"]):
                local = local_index[item["container_id"]].get(str(target), [])
                global_hits = global_index.get(str(target), [])
                status = "local" if len(local) == 1 else "ambiguous_local" if local else "external" if len(global_hits) == 1 else "ambiguous_external" if global_hits else "dangling"
                expected_successors.append({"ordinal": ordinal, "raw_target_id": target, "status": status, "local_candidates": local, "physical_candidates": global_hits if not local else []})
                graph[status] += 1
            require(row["successors_json"] == enc(expected_successors), "talk successor candidate/status")
        require(node_seen == set(nodes), "talk node omission")

        ref_seen = set()
        reference_states = Counter()
        for row in conn.execute("SELECT * FROM physical_reference"):
            rid = row["id"]
            require(rid in refs and rid not in ref_seen, "physical reference extra or duplicate")
            ref_seen.add(rid)
            cid, expected_run, pointer, kind, native, raw_json = refs[rid]
            require((row["container_id"], row["ingest_run_id"], row["pointer"], row["kind"], row["native_id"], row["raw_json"]) == (cid, expected_run, pointer, kind, native, raw_json), "reference provenance")
            candidates = sorted(root_index.get(native, []), key=lambda item: (item["path"], item["pointer"]))
            status = "missing" if not candidates else "ambiguous" if len(candidates) > 1 else "terminal_unresolved" if candidates[0]["kind"] == "mapping_unresolved" else "resolved_unique"
            require(row["status"] == status and row["candidates_json"] == enc(candidates), "reference candidate/status")
            reference_states[status] += 1
        require(ref_seen == set(refs), "reference omission")

        textmaps = {locale: defaultdict(list) for locale in ("CHS", "EN", "JP")}
        expected_shards = {}
        for shard in scope["textmap_shards"]:
            path = shard["path"]
            raw = (root / path).read_bytes()
            blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            require(blob == blobs[path] and len(raw) == shard["size_bytes"] and hash_bytes(raw) == shard["sha256"], "TextMap source identity")
            expected_shards[path] = (SNAPSHOT, shard["locale"], shard["tier"], blob, shard["sha256"], len(raw))
            for ordinal, (key, value) in enumerate(textmap_pairs(raw)):
                if key in wanted:
                    textmaps[shard["locale"]][key].append({"path": path, "pointer": "/" + key, "pair_ordinal": ordinal, "tier": shard["tier"], "raw_text": value})
        seen_shards = set()
        for row in conn.execute("SELECT * FROM textmap_shard"):
            path = row["path"]
            require(path in expected_shards and path not in seen_shards, "TextMap shard extra or duplicate")
            seen_shards.add(path)
            require(tuple(row[field] for field in ("snapshot_id", "locale", "tier", "git_blob_sha1", "sha256", "size_bytes")) == expected_shards[path], "TextMap shard seal")
        require(seen_shards == set(expected_shards), "TextMap shard omission")
        text_states = Counter()
        slot_count = 0
        for row in conn.execute("SELECT * FROM text_slot"):
            nid, locale = row["node_id"], row["locale"]
            require(nid in nodes and locale in textmaps, "unknown node/locale text slot")
            item = nodes[nid]
            require(row["id"] == ident("qt71-text", run, nid, locale) and row["ingest_run_id"] == run, "text slot identity")
            hits = textmaps[locale].get(item["text_hash"], []) if item["text_hash"] is not None else []
            main = [hit for hit in hits if hit["tier"] == "main"]
            medium = [hit for hit in hits if hit["tier"] == "medium"]
            status = "mapping_unresolved_terminal" if item["text_mapping"] == "unresolved_codec" else "missing" if not hits else "ambiguous" if len({hit["raw_text"] for hit in hits}) > 1 else "resolved_main" if main else "resolved_medium"
            text = (main or medium)[0]["raw_text"] if status.startswith("resolved") else None
            require((row["status"], row["raw_text"], row["locators_json"]) == (status, text, enc(hits)), "text slot source resolution")
            text_states[status] += 1
            slot_count += 1
        require(slot_count == len(nodes) * 3, "text slot no-extra/no-omission denominator")
        counts = {
            "quest_files": len(quest_paths), "talk_files": len(talk_paths), "main_quest_rows": len(main_rows),
            "quest_children": len(children), "quest_references": sum(item[3] == "quest_to_talk" for item in refs.values()),
            "wrapper_references": sum(item[3] == "wrapper_to_talk" for item in refs.values()),
            "talk_nodes": len(nodes), "text_slots": slot_count,
        }
        counts.update(Counter(item[3] for item in containers.values()))
        require(dict(sorted(counts.items())) == build_report["counts"], "builder count denominator")
        states = {"text_" + key: value for key, value in text_states.items()}
        states.update({"reference_" + key: value for key, value in reference_states.items()})
        states.update({"successor_" + key: value for key, value in graph.items()})
        states.update(source_states)
        require(dict(sorted(states.items())) == build_report["states"], "builder resolution state denominator")

        seals = {}
        for table, order in (
            ("source_file", "path"), ("textmap_shard", "path"),
            ("physical_container", "id"), ("quest_child", "id"),
            ("talk_node", "id"), ("text_slot", "id"),
            ("physical_reference", "id"), ("main_quest_row", "id"),
        ):
            digest = hashlib.sha256()
            rows = 0
            for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}"):
                values = [({"raw_bytes_sha256": hash_bytes(value)} if isinstance(value, bytes) else value) for value in row]
                digest.update((enc(values) + "\n").encode())
                rows += 1
            seals[table] = {"rows": rows, "sha256": digest.hexdigest()}
        require(not any(Path(str(artifact) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")), "SQLite sidecar after verification")
        require(artifact.stat().st_size == artifact_stat.st_size and hash_file(artifact) == before, "artifact changed during verification")
        return {
            "status": "PASS", "contract": CONTRACT, "snapshot_id": SNAPSHOT,
            "ingest_run_id": run, "artifact_sha256": before,
            "implementation_recomputed": True,
            "counts": dict(sorted(counts.items())), "states": dict(sorted(states.items())),
            "logical_seals": seals, "silent_skip_count": 0,
            "wrong_cross_snapshot_identity_merge_count": 0,
            "verify_seconds": time.monotonic() - started,
        }
    finally:
        conn.close()
