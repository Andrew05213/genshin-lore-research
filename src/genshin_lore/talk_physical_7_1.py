"""7.1 Talk physical closure, reusing the sealed complete Quest/Talk input.

No dialogue body duplication: mapped roots reference exact existing runs.
Wrapper raw containers remain separate. No logical Talk identity is created.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path

from .research_index import sha256_file

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = "anime-game-data:7.1:9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
CONTRACT = "talk-physical-closure-v1"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ident(*parts):
    return "talk71-physical:" + hashlib.sha256(canonical(parts).encode()).hexdigest()[:40]


def build(source, dependency, seal, output):
    from .schema20_7_1_candidate import qualify_source
    qualify_source(source)
    if dependency.stat().st_size != seal["size_bytes"] or sha256_file(dependency) != seal["sha256"]:
        raise ValueError("exact qualified Quest/Talk dependency seal mismatch")
    if any(Path(str(dependency) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("Quest/Talk dependency sidecar")
    if output.exists() or any(Path(str(output) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise FileExistsError(output)
    src = sqlite3.connect(dependency.as_uri() + "?mode=ro&immutable=1", uri=True)
    src.row_factory = sqlite3.Row
    release = dict(src.execute("SELECT * FROM release").fetchone())
    if release["snapshot_id"] != SNAPSHOT:
        raise ValueError("Quest/Talk dependency wrong snapshot")
    parser = ident("parser", CONTRACT, sha256_file(Path(__file__)), sha256_file(ROOT / "schemas/talk-physical-closure-v1.sql"))
    run = ident("run", SNAPSHOT, parser, seal)
    pins = subprocess.check_output(["git", "-C", str(source), "ls-tree", "-rz", "HEAD", "--", "BinOutput/Talk"]).split(b"\0")
    blobs = {p.decode(): meta.decode().split()[2] for line in pins if line for meta, p in [line.split(b"\t", 1)] if p.endswith(b".json")}
    files = list(src.execute("SELECT * FROM source_file WHERE kind='talk' ORDER BY path"))
    if {f["path"] for f in files} != set(blobs):
        raise ValueError("Talk complete physical path set mismatch")
    roots = {r["source_path"]: dict(r) for r in src.execute("SELECT * FROM physical_container WHERE source_path LIKE 'BinOutput/Talk/%' AND pointer='/'")}
    if set(roots) != set(blobs):
        raise ValueError("Talk root coverage mismatch")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    os.close(fd)
    db = sqlite3.connect(output)
    counts = Counter()
    try:
        db.executescript((ROOT / "schemas/talk-physical-closure-v1.sql").read_text())
        for f in files:
            raw = (source / f["path"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != f["sha256"] or hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != blobs[f["path"]] or raw != f["raw_bytes"]:
                raise ValueError("Talk source bytes/blob mismatch: " + f["path"])
            root = roots[f["path"]];kind = root["kind"]
            if kind not in {"talk_root", "reference_wrapper", "mapping_unresolved"}:
                raise ValueError("unqualified Talk root kind")
            classification = {"talk_root": "mapped_program_reuse", "reference_wrapper": "reference_wrapper", "mapping_unresolved": "mapping_unresolved_terminal"}[kind]
            reuse = kind == "talk_root"
            fid = ident(run, "file", f["path"])
            db.execute("INSERT INTO talk_physical_file VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                fid, run, f["path"], blobs[f["path"]], f["sha256"], len(raw), canonical(sorted(json.loads(raw))),
                classification, root["mapping"], None if reuse else raw,
                "quest_talk_pilot" if reuse else None, release["contract"] if reuse else None,
                release["ingest_run_id"] if reuse else None, root["id"] if reuse else None))
            counts[classification] += 1
            for r in src.execute("SELECT * FROM physical_reference WHERE container_id=? ORDER BY id", (root["id"],)) if kind == "reference_wrapper" else ():
                hits = json.loads(r["candidates_json"])
                if any(h["kind"] not in {"talk_root", "mapping_unresolved"} or h["pointer"] != "/" for h in hits):
                    raise ValueError("wrapper target is outside the declared physical root namespace")
                rid = ident(run, "reference", f["path"], r["pointer"])
                db.execute("INSERT INTO talk_physical_reference VALUES(?,?,?,?,?,?,?,?,?)", (
                    rid, fid, r["pointer"], r["native_id"], r["raw_json"],
                    "known_candidate_set" if hits else "mapping_unresolved_terminal_namespace", len(hits), 1, 0))
                counts["references"] += 1
                for h in hits:
                    target = roots[h["path"]]
                    if target["id"] != h["container_id"] or target["native_id"] != r["native_id"]:
                        raise ValueError("wrapper second-hop physical binding mismatch")
                    if h["kind"] == "mapping_unresolved":
                        counts["terminal_unresolved_target_candidates"] += 1
                    db.execute("INSERT INTO talk_physical_candidate VALUES(?,?,?,?,?,?,?,?,?)", (
                        ident(run, rid, h["path"]), rid, h["path"], h["pointer"], target["native_id"],
                        "quest_talk_pilot", release["contract"], release["ingest_run_id"], target["id"]))
                    counts["candidates"] += 1
        manifest = {"family": "talk_physical_archive", "family_contract_version": CONTRACT, "snapshot_id": SNAPSHOT,
                    "run_id": run, "parser_build_id": parser, "dependency": {"artifact": seal, "release": release},
                    "counts": dict(counts), "physical_path_denominator": len(files),
                    "logical_identity_created": False, "new_dialogue_nodes": 0, "mapped_program_bodies_copied": False,
                    "semantic_mapping_resolution": "partial_with_terminal_unresolved", "full_talk": False,
                    "product_accepted": False}
        db.execute("INSERT INTO talk_physical_release VALUES(1,?)", (canonical(manifest),))
        db.commit()
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Talk physical integrity failure")
    except Exception:
        db.close();raise
    finally:
        src.close()
    db.close()
    return {**manifest, "artifact": {"size_bytes": output.stat().st_size, "sha256": sha256_file(output)}}
