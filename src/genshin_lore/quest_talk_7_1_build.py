"""Fresh, snapshot-local Quest/Talk 7.1 standalone successor builder.

The frozen 7.0 builders and artifacts are not imported or rewritten.  The
output is a derived physical-evidence candidate, never current product.
"""
from __future__ import annotations

import hashlib
import json
import os
import resource
import sqlite3
import subprocess
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

from .loading_tips_source import array_records, pairs
from .quest_talk_7_1_physical import SIGNATURE_MANIFEST, decode_quest, decode_talk

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
TREE = "aa578c08c402af39b41f1ffc95124d10f5410272"
SNAPSHOT = f"anime-game-data:7.1:{COMMIT}"
CONTRACT = "quest-talk-7.1-standalone-physical-v1"
SCHEMA = ROOT / "schemas/quest-talk-7.1-standalone-v1.sql"
DECODER = ROOT / "src/genshin_lore/quest_talk_7_1_physical.py"
MAIN_QUEST = "ExcelBinOutput/MainQuestExcelConfigData.json"
LOCALES = ("CHS", "EN", "JP")


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def identity(kind: str, *parts) -> str:
    return kind + ":" + sha256(canonical(parts).encode())[:40]


def at(doc, pointer: str):
    if pointer == "/":
        return doc
    value = doc
    for part in pointer.strip("/").split("/"):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def _tree_blobs(root: Path, prefix: str) -> dict[str, str]:
    entries = subprocess.check_output(
        ["git", "-C", str(root), "ls-tree", "-r", "-z", COMMIT, "--", prefix]
    )
    result = {}
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        if mode != "100644" or kind != "blob":
            raise ValueError("unsupported pinned source tree entry")
        result[path.decode()] = oid
    return result


def _preflight(root: Path, scope: dict) -> tuple[dict[str, str], list[str], list[str]]:
    if scope.get("contract") != CONTRACT or scope.get("source_commit") != COMMIT or scope.get("source_tree") != TREE:
        raise ValueError("7.1 Quest/Talk source contract mismatch")
    signature = scope.get("root_signature_manifest", {})
    if signature.get("path") != SIGNATURE_MANIFEST.relative_to(ROOT).as_posix() or signature.get("sha256") != file_sha256(SIGNATURE_MANIFEST):
        raise ValueError("7.1 Quest/Talk root signature contract seal mismatch")
    pins = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD", "HEAD^{tree}"], text=True
    ).splitlines()
    if pins != [COMMIT, TREE] or subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"], text=True
    ).strip():
        raise ValueError("7.1 source checkout is not exact and clean")
    blobs = _tree_blobs(root, "BinOutput/Quest") | _tree_blobs(root, "BinOutput/Talk")
    quest = sorted(path for path in blobs if path.startswith("BinOutput/Quest/") and path.endswith(".json"))
    talk = sorted(path for path in blobs if path.startswith("BinOutput/Talk/") and path.endswith(".json"))
    for name, paths in (("quest", quest), ("talk", talk)):
        declared = scope["physical_scope"][name]
        if len(paths) != declared["file_count"] or sha256((canonical(paths) + "\n").encode()) != declared["path_set_sha256"]:
            raise ValueError(f"{name} pinned physical denominator changed")
        physical = {path.relative_to(root).as_posix() for path in (root / "BinOutput" / name.capitalize()).rglob("*.json")}
        if physical != set(paths):
            raise ValueError(f"{name} source checkout has missing or extra physical files")
    return blobs, quest, talk


def _source_bytes(root: Path, path: str, expected_blob: str) -> tuple[bytes, str]:
    raw = (root / path).read_bytes()
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if blob != expected_blob:
        raise ValueError("source bytes differ from exact pinned Git tree: " + path)
    return raw, sha256(raw)


def _heartbeat(phase: str, counts: Counter, last: list[float]) -> None:
    now = time.monotonic()
    if now - last[0] >= 30:
        print(canonical({"phase": phase, "counts": dict(counts)}), flush=True)
        last[0] = now


def _textmaps(root: Path, scope: dict, wanted: set[str], blobs: dict[str, str], conn: sqlite3.Connection):
    resolved = {locale: defaultdict(list) for locale in LOCALES}
    for item in scope["textmap_shards"]:
        path = item["path"]
        raw, digest = _source_bytes(root, path, blobs[path])
        if len(raw) != item["size_bytes"] or digest != item["sha256"]:
            raise ValueError("7.1 TextMap seal mismatch: " + path)
        conn.execute("INSERT INTO textmap_shard VALUES(?,?,?,?,?,?,?)", (
            path, SNAPSHOT, item["locale"], item["tier"], blobs[path], digest, len(raw),
        ))
        for ordinal, (key, value) in enumerate(pairs(root / path)):
            if key in wanted:
                resolved[item["locale"]][key].append({
                    "path": path, "pointer": "/" + key, "pair_ordinal": ordinal,
                    "tier": item["tier"], "raw_text": value,
                })
    return resolved


def build(source_root: Path, scope_path: Path, output: Path) -> dict:
    """Create one no-clobber candidate; final acceptance is verifier-owned."""
    started = time.monotonic()
    root = Path(source_root).resolve()
    scope_path = Path(scope_path).resolve()
    output = Path(output)
    if output.exists() or output.is_symlink() or any(Path(str(output) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise FileExistsError("Quest/Talk candidate target or sidecar already exists")
    scope_raw = scope_path.read_bytes()
    scope = json.loads(scope_raw)
    blobs, quest_paths, talk_paths = _preflight(root, scope)
    if len(quest_paths) != 4808 or len(talk_paths) != 59853:
        raise ValueError("unapproved 7.1 physical denominator")
    blobs.update(_tree_blobs(root, "TextMap"))
    blobs.update(_tree_blobs(root, MAIN_QUEST))
    implementation = sha256(canonical({
        "decoder": file_sha256(DECODER), "builder": file_sha256(Path(__file__)),
        "schema": file_sha256(SCHEMA), "root_signatures": file_sha256(SIGNATURE_MANIFEST),
    }).encode())
    parser = identity("parser-build", CONTRACT, implementation)
    run = identity("ingest-run", SNAPSHOT, parser, sha256(scope_raw))
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, stage_name = tempfile.mkstemp(prefix=".qt71-stage-", suffix=".sqlite", dir=output.parent)
    os.close(fd)
    stage = Path(stage_name)
    compact = stage.with_name(stage.name + ".compact")
    conn = None
    source_hashes = {}
    counters = Counter()
    states = Counter()
    heartbeat = [time.monotonic()]
    try:
        conn = sqlite3.connect(stage)
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA.read_text())
        talk_roots = defaultdict(list)
        node_ids = defaultdict(list)
        local_nodes = defaultdict(lambda: defaultdict(list))
        wanted_hashes = set()

        def insert_source(path: str, kind: str) -> tuple[bytes, dict]:
            raw, digest = _source_bytes(root, path, blobs[path])
            source_hashes[path] = digest
            conn.execute("INSERT INTO source_file VALUES(?,?,?,?,?,?,?)", (
                path, SNAPSHOT, kind, blobs[path], digest, len(raw), raw,
            ))
            return raw, json.loads(raw.decode("utf-8-sig"))

        def insert_container(path: str, pointer: str, kind: str, mapping: str, native, raw_value) -> str:
            cid = identity("qt71-container", run, path, pointer)
            raw = (root / path).read_bytes() if pointer == "/" else canonical(raw_value).encode()
            conn.execute("INSERT INTO physical_container VALUES(?,?,?,?,?,?,?,?)", (
                cid, run, path, pointer, kind, mapping,
                None if native is None else str(native), sha256(raw),
            ))
            counters[kind] += 1
            return cid

        def insert_node(path: str, cid: str, item: dict, doc: dict) -> None:
            node_raw = at(doc, item["pointer"])
            nid = identity("qt71-node", run, path, item["pointer"])
            native = str(item["native_id"])
            conn.execute("INSERT INTO talk_node VALUES(?,?,?,?,?,?,?,?,?,?)", (
                nid, cid, run, item["ordinal"], item["pointer"], native,
                None if item["text_hash"] is None else str(item["text_hash"]),
                item["text_mapping"], canonical(node_raw), canonical(item["next"]),
            ))
            node_ids[native].append({"path": path, "pointer": item["pointer"], "node_id": nid})
            local_nodes[cid][native].append(nid)
            if item["text_hash"] is not None:
                wanted_hashes.add(str(item["text_hash"]))
            counters["talk_nodes"] += 1

        for path in quest_paths:
            raw, doc = insert_source(path, "quest")
            decoded = decode_quest(doc)
            if decoded["children_field_status"] == "source_field_absent":
                states["quest_child_field_absent"] += 1
            if decoded["reference_field_status"] == "source_field_absent":
                states["quest_reference_field_absent"] += 1
            cid = insert_container(path, "/", "quest_root", "7.1-versioned", decoded["native_id"], doc)
            for ordinal, pointer in enumerate(decoded["child_pointers"]):
                child = at(doc, pointer)
                child_raw = canonical(child)
                conn.execute("INSERT INTO quest_child VALUES(?,?,?,?,?,?,?)", (
                    identity("qt71-child", run, path, pointer), cid, run, ordinal,
                    pointer, child_raw, sha256(child_raw.encode()),
                ))
                counters["quest_children"] += 1
            for ref in decoded["references"]:
                _insert_reference(conn, run, cid, path, ref, "quest_to_talk", doc)
                counters["quest_references"] += 1
            for embedded in decoded["embedded"]:
                ecid = insert_container(path, embedded["pointer"], "embedded_dialogue", embedded["mapping"], None, at(doc, embedded["pointer"]))
                for node in embedded["nodes"]:
                    insert_node(path, ecid, node, doc)
            counters["quest_files"] += 1
            _heartbeat("quest", counters, heartbeat)

        old_shapes = json.loads((ROOT / "manifests/quest-talk-physical/shapes-v1.json").read_text())["shapes"]
        for path in talk_paths:
            raw, doc = insert_source(path, "talk")
            decoded = decode_talk(doc, old_shapes)
            if decoded["kind"] == "talk_root_no_id":
                states["talk_root_no_native_id"] += 1
            kind = "talk_root" if decoded["kind"] in ("talk_root", "talk_root_no_id") else decoded["kind"]
            cid = insert_container(path, "/", kind, decoded["mapping"], decoded["native_id"], doc)
            if kind == "talk_root" and decoded["native_id"] is not None:
                talk_roots[str(decoded["native_id"])].append({"path": path, "pointer": "/", "container_id": cid, "kind": kind})
            if kind == "mapping_unresolved" and decoded["native_id"] is not None:
                talk_roots[str(decoded["native_id"])].append({"path": path, "pointer": "/", "container_id": cid, "kind": kind})
            for node in decoded["nodes"]:
                insert_node(path, cid, node, doc)
            for ref in decoded["references"]:
                _insert_reference(conn, run, cid, path, ref, "wrapper_to_talk", doc)
                counters["wrapper_references"] += 1
            counters["talk_files"] += 1
            _heartbeat("talk", counters, heartbeat)

        main_raw, main_rows = insert_source(MAIN_QUEST, "main_quest")
        if len(main_raw) != scope["main_quest_source"]["size_bytes"] or source_hashes[MAIN_QUEST] != scope["main_quest_source"]["sha256"]:
            raise ValueError("7.1 MainQuest support source seal mismatch")
        if not isinstance(main_rows, list):
            raise ValueError("7.1 MainQuest source must be physical array")
        for ordinal, row, _ in array_records(root / MAIN_QUEST):
            if not isinstance(row, dict) or type(row.get("id")) is not int:
                raise ValueError("7.1 MainQuest row ID shape")
            conn.execute("INSERT INTO main_quest_row VALUES(?,?,?,?,?,?,?)", (
                identity("qt71-mainquest", run, MAIN_QUEST, f"/{ordinal}"), run,
                MAIN_QUEST, ordinal, f"/{ordinal}", str(row["id"]), canonical(row),
            ))
            counters["main_quest_rows"] += 1

        maps = _textmaps(root, scope, wanted_hashes, blobs, conn)
        for nid, hash_value, text_mapping in conn.execute("SELECT id,text_hash,text_mapping FROM talk_node"):
            for locale in LOCALES:
                hits = maps[locale].get(hash_value, []) if hash_value is not None else []
                main = [hit for hit in hits if hit["tier"] == "main"]
                medium = [hit for hit in hits if hit["tier"] == "medium"]
                status = "mapping_unresolved_terminal" if text_mapping == "unresolved_codec" else "missing" if not hits else "ambiguous" if len({hit["raw_text"] for hit in hits}) > 1 else "resolved_main" if main else "resolved_medium"
                text = (main or medium)[0]["raw_text"] if status.startswith("resolved") else None
                conn.execute("INSERT INTO text_slot VALUES(?,?,?,?,?,?,?)", (
                    identity("qt71-text", run, nid, locale), nid, run, locale,
                    status, text, canonical(hits),
                ))
                states["text_" + status] += 1
                counters["text_slots"] += 1
            _heartbeat("text", counters, heartbeat)

        for rid, native in conn.execute("SELECT id,native_id FROM physical_reference").fetchall():
            candidates = sorted(talk_roots.get(native, []), key=lambda item: (item["path"], item["pointer"]))
            status = "missing" if not candidates else "ambiguous" if len(candidates) > 1 else "terminal_unresolved" if candidates[0]["kind"] == "mapping_unresolved" else "resolved_unique"
            conn.execute("UPDATE physical_reference SET status=?,candidates_json=? WHERE id=?", (status, canonical(candidates), rid))
            states["reference_" + status] += 1
            _heartbeat("references", counters, heartbeat)

        last_node = ""
        while True:
            batch = conn.execute(
                "SELECT id,container_id,successors_json FROM talk_node WHERE id>? ORDER BY id LIMIT 4096",
                (last_node,),
            ).fetchall()
            if not batch:
                break
            for nid, cid, raw_next in batch:
                observations = []
                for ordinal, target in enumerate(json.loads(raw_next)):
                    local = local_nodes[cid].get(str(target), [])
                    global_hits = node_ids.get(str(target), [])
                    status = "local" if len(local) == 1 else "ambiguous_local" if local else "external" if len(global_hits) == 1 else "ambiguous_external" if global_hits else "dangling"
                    observations.append({
                        "ordinal": ordinal, "raw_target_id": target, "status": status,
                        "local_candidates": local, "physical_candidates": global_hits if not local else [],
                    })
                    states["successor_" + status] += 1
                conn.execute("UPDATE talk_node SET successors_json=? WHERE id=?", (canonical(observations), nid))
                _heartbeat("successors", counters, heartbeat)
            last_node = batch[-1][0]

        expected = {"quest_files": len(quest_paths), "talk_files": len(talk_paths), "main_quest_rows": len(main_rows)}
        for key, value in expected.items():
            if counters[key] != value:
                raise ValueError("silent source skip in " + key)
        if any(Path(str(output) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise ValueError("candidate target sidecar appeared")
        for path, digest in source_hashes.items():
            if file_sha256(root / path) != digest:
                raise ValueError("source bytes drifted during 7.1 Quest/Talk build: " + path)
        for item in scope["textmap_shards"]:
            if file_sha256(root / item["path"]) != item["sha256"]:
                raise ValueError("TextMap bytes drifted during 7.1 Quest/Talk build")
        if sha256(scope_path.read_bytes()) != sha256(scope_raw):
            raise ValueError("Quest/Talk scope changed during build")
        if implementation != sha256(canonical({
            "decoder": file_sha256(DECODER), "builder": file_sha256(Path(__file__)),
            "schema": file_sha256(SCHEMA), "root_signatures": file_sha256(SIGNATURE_MANIFEST),
        }).encode()):
            raise ValueError("Quest/Talk implementation changed during build")
        if subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD", "HEAD^{tree}"], text=True).splitlines() != [COMMIT, TREE]:
            raise ValueError("source commit/tree drifted during build")
        if subprocess.check_output(["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"], text=True).strip():
            raise ValueError("source checkout changed during build")
        conn.execute("INSERT INTO release VALUES(1,?,?,?,?,?,?,?,?,?,?)", (
            CONTRACT, SNAPSHOT, COMMIT, TREE, parser, run, sha256(scope_raw),
            implementation, canonical(dict(sorted(counters.items()))), canonical(dict(sorted(states.items()))),
        ))
        conn.commit()
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("7.1 Quest/Talk candidate SQLite integrity failure")
        conn.execute("VACUUM INTO ?", (str(compact),))
        conn.close()
        conn = None
        if output.exists() or output.is_symlink():
            raise FileExistsError("candidate target appeared during build")
        os.link(compact, output)
        compact.unlink()
        report = {
            "contract": CONTRACT, "status": "candidate_unverified", "product_accepted": False,
            "snapshot_id": SNAPSHOT, "source_commit": COMMIT, "source_tree": TREE,
            "parser_build_id": parser, "ingest_run_id": run, "scope_sha256": sha256(scope_raw),
            "implementation_sha256": implementation,
            "artifact": {"size_bytes": output.stat().st_size, "sha256": file_sha256(output)},
            "counts": dict(sorted(counters.items())), "states": dict(sorted(states.items())),
            "silent_skip_count": 0, "cross_snapshot_identity_merge_count": 0,
            "build_seconds": time.monotonic() - started,
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        }
        print(canonical({"phase": "complete", "counts": report["counts"]}), flush=True)
        return report
    finally:
        if conn is not None:
            conn.close()
        if stage.exists():
            stage.unlink()
        if compact.exists():
            compact.unlink()


def _insert_reference(conn: sqlite3.Connection, run: str, cid: str, path: str, ref: dict, kind: str, doc: dict) -> None:
    pointer = ref["pointer"]
    conn.execute("INSERT INTO physical_reference VALUES(?,?,?,?,?,?,?,?,?)", (
        identity("qt71-ref", run, path, pointer), cid, run, pointer,
        kind, str(ref["native_id"]), canonical(at(doc, pointer)), "missing", "[]",
    ))
