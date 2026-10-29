"""Fresh Schema20 materialization of the ten qualified M3 7.1 families.

This is a scoped construction step, not a full product acceptance. No 7.0
database is opened. Family/run identities survive the storage consolidation.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from pathlib import Path

from .db import Database
from .m3_7_1_provider_integration import historical_status
from .research_index import sha256_file
from .schema20_7_1_guards import install as install_guards, CONTRACT as GUARD_CONTRACT
from .schema20_7_1_special import assemble as assemble_special, MAPPINGS as SPECIAL_MAPPINGS

ROOT = Path(__file__).resolve().parents[2]
MAPPINGS = ROOT / "manifests/m3-7.1-provider/accepted-exact-mappings-v1.json"
SNAPSHOT = "anime-game-data:7.1:9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
COMMIT = "9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
TREE = "aa578c08c402af39b41f1ffc95124d10f5410272"
STAMP = "2026-10-03T00:00:00+00:00"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def identity(*parts):
    return hashlib.sha256(canonical(parts).encode()).hexdigest()


def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    return db


def qualify_source(source):
    def git(*args):
        return subprocess.check_output(["git", "--no-optional-locks", "-C", str(source), *args], text=True).strip()
    if git("rev-parse", "HEAD") != COMMIT or git("rev-parse", "HEAD^{tree}") != TREE:
        raise ValueError("exact 7.1 source commit/tree required")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("source checkout must be clean")


def build(source: Path, output: Path, *, include_special=False, metadata_root=None):
    qualify_source(source)
    if output.exists() or any(Path(str(output) + s).exists() for s in ("-wal", "-shm", "-journal")):
        raise FileExistsError(output)
    mappings = Path(metadata_root) / MAPPINGS.relative_to(ROOT) if metadata_root is not None else MAPPINGS
    special_mappings = Path(metadata_root) / SPECIAL_MAPPINGS.relative_to(ROOT) if metadata_root is not None else SPECIAL_MAPPINGS
    items = json.loads(mappings.read_text())["mappings"]
    if len(items) != 20 or len({x["family"] for x in items}) != 10:
        raise ValueError("frozen M3 denominator changed")
    inputs = {}
    files = {}
    special = json.loads(special_mappings.read_text())["mappings"] if include_special else []
    if include_special and len(special) != 13:
        raise ValueError("special core denominator changed")
    for item in items + special:
        artifact = item["candidate_artifact"]
        path = Path(artifact["path_machine_local"])
        if str(path) not in inputs:
            if any(Path(str(path) + s).exists() for s in ("-wal", "-shm", "-journal")):
                raise ValueError("input sidecar")
            if path.stat().st_size != artifact["size_bytes"] or sha256_file(path) != artifact["sha256"]:
                raise ValueError("qualified 7.1 input artifact drift")
            inputs[str(path)] = artifact
            with readonly(path) as db:
                for row in db.execute("SELECT * FROM source_file ORDER BY path"):
                    record = dict(row)
                    if row["path"] in files:
                        old = files[row["path"]]
                        if any(old[k] != record[k] for k in ("path", "size_bytes", "sha256", "git_blob_oid")):
                            raise ValueError("source identity collision")
                        # Selection roles belong to the consuming contract, not
                        # to the shared snapshot-local physical file identity.
                        old["roles"] = sorted(set(old.get("roles", [old["role"]])) | {record["role"]})
                    else:
                        files[row["path"]] = record
    for path, entry in files.items():
        raw_path = source / path
        blob = subprocess.check_output(["git", "-C", str(source), "rev-parse", f"{COMMIT}:{path}"], text=True).strip()
        if blob != entry["git_blob_oid"] or raw_path.stat().st_size != entry["size_bytes"] or sha256_file(raw_path) != entry["sha256"]:
            raise ValueError("source bytes/pinned blob mismatch: " + path)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive file ownership before the standard migration pipeline runs.
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        db = Database(output)
        try:
            db.initialize_through(20)
            conn = db.conn
            install_guards(conn)
            conn.execute("PRAGMA journal_mode=DELETE")
            # Freeze deterministic lifecycle metadata, not evidence timestamps.
            conn.execute("UPDATE schema_migration SET applied_at=?", (STAMP,))
            conn.execute("INSERT INTO source_catalog VALUES(?,?,?,?)", ("anime-game-data", "primary", "https://github.com/Dimbreath/AnimeGameData", "upstream source evidence"))
            conn.execute('INSERT INTO source_snapshot VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                         (SNAPSHOT, "anime-game-data", "7.1", "7.1", COMMIT, TREE, STAMP, "source-only", "source-only", "staged", canonical({"root_path": str(source), "product_accepted": False})))
            for path, entry in sorted(files.items()):
                conn.execute("INSERT INTO source_file VALUES(?,?,?,?,?,?,?,?)", (identity(SNAPSHOT, path), SNAPSHOT, path, entry["git_blob_oid"], entry["sha256"], entry["size_bytes"], "json", str(source),))
            counts = []
            registered = set()
            source_records = set()
            for item in sorted(items, key=lambda x: (x["family"], x["version"])):
                family, version, run, parser = (item[k] for k in ("family", "version", "ingest_run_id", "parser_build_id"))
                with readonly(item["candidate_artifact"]["path_machine_local"]) as src:
                    release = json.loads(src.execute("SELECT manifest_json FROM release").fetchone()[0])
                    def register(pid, rid, contract, scope, implementation_sha, config_sha):
                        if rid in registered:
                            return
                        conn.execute("INSERT OR IGNORE INTO parser_build VALUES(?,?,?,?,?,?)", (pid, contract, version if rid == run else "v1", implementation_sha, config_sha, STAMP))
                        conn.execute("INSERT INTO ingest_run VALUES(?,?,?,?,?,?,?)", (rid, SNAPSHOT, pid, canonical(scope), canonical({"qualified_input_artifact": item["candidate_artifact"], "qualified_parser_release": release, "textmap_observation": release.get("textmap_observation"), "assembly_is_not_new_source_identity": True}), "complete", STAMP))
                        registered.add(rid)
                    parser_config = canonical({"family": family, "source": item["source_path"], "keys": release["declared_key_fields"], "text": release["declared_text_fields"], "v1": release["family_contracts"]["v1"], "v2": release["family_contracts"]["v2"]})
                    register(parser, run, item["provider_contract"], {"family": family, "family_contract_version": item["provider_contract"], "exact_ingest_run_required": True}, release["builder_implementation_sha256"], hashlib.sha256(parser_config.encode()).hexdigest())
                    tm = release.get("textmap_observation")
                    if version == "v2" and tm:
                        register(tm["parser_build_id"], tm["ingest_run_id"], "snapshot-local-physical-textmap", {"physical_textmap_only": True}, release["parser_helper_sha256"]["textmap_lexical_reader"], release["schema_sha256"])
                    rows = list(src.execute(f"SELECT * FROM {version}_row ORDER BY source_ordinal"))
                    row_table = family + "_row_occurrence"
                    for r in rows:
                        record_id = identity(SNAPSHOT, r["source_path"], r["source_pointer"])
                        if record_id not in source_records:
                            conn.execute("INSERT INTO source_record VALUES(?,?,?,?,?,?,?)", (record_id, identity(SNAPSHOT, r["source_path"]), r["source_pointer"], "physical_json_array_row", None, r["raw_record_sha256"], r["raw_record_json"]))
                            source_records.add(record_id)
                        keys = r["raw_key_observations_json"] if version == "v1" else "[]"
                        conn.execute(f"INSERT INTO {row_table} VALUES({','.join('?' for _ in range(15))})", (r["occurrence_id"], family, item["provider_contract"], run, SNAPSHOT, parser, r["source_path"], record_id, r["source_pointer"], r["source_ordinal"], r["raw_record_json"], r["raw_record_sha256"], keys, "raw_text_fields_observed", canonical({"coverage": "qualified_exact_7_1_mapping", "logical_identity_created": False})))
                    if version == "v2":
                        conn.executemany(f"INSERT INTO {family}_text_owner_occurrence VALUES(?,?,?,?,?,?,?,?)", ((r["owner_id"], run, r["row_occurrence_id"], r["field_name"], r["field_pair_ordinal"], r["raw_hash_text"], historical_status(r["overall_resolution_state"]), r["candidate_count"]) for r in src.execute("SELECT * FROM v2_owner ORDER BY owner_id")))
                        conn.executemany(f"INSERT INTO {family}_text_resolution_occurrence VALUES(?,?,?,?,?,?)", ((r["resolution_id"], run, r["owner_id"], r["locale"], historical_status(r["state"]), r["candidate_count"]) for r in src.execute("SELECT * FROM v2_resolution ORDER BY resolution_id")))
                        # Q2's four namespaces predate row-local TextMap run columns.
                        # Their exact TextMap identity stays in ingest_run.config_json.
                        columns = 14 if family in {"achievement_archive", "dungeon_archive", "npc_archive", "scene_archive"} else 18
                        def candidate(r):
                            head = (r["candidate_id"], run, r["owner_id"])
                            provenance = (r["textmap_snapshot_id"], r["textmap_parser_build_id"], r["textmap_ingest_run_id"], r["source_record_pointer"])
                            body = (r["locale"], r["tier"], r["source_path"], r["pair_ordinal"], r["source_pointer"], r["raw_key_json"], r["raw_text_json"], r["raw_text"], r["raw_key_sha256"], r["raw_text_sha256"], r["raw_record_sha256"])
                            return head + (provenance if columns == 18 else ()) + body
                        conn.executemany(f"INSERT INTO {family}_text_candidate_occurrence VALUES({','.join('?' for _ in range(columns))})", (candidate(r) for r in src.execute("SELECT * FROM v2_candidate ORDER BY candidate_id")))
                    counts.append({"family": family, "contract": item["provider_contract"], "parser": parser, "run": run, "rows": len(rows)})
            counts.extend(assemble_special(conn, source, special))
            conn.commit()
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("foreign key failure")
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("integrity failure")
        finally:
            db.close()
        return {"status": "CORE33_MATERIALIZED_AWAITING_QUALIFICATION" if include_special else "PARTIAL_CORE_MATERIALIZED", "snapshot": SNAPSHOT, "schema_version": 20, "candidate_ddl_contract": GUARD_CONTRACT, "assembly_implementation_sha256": sha256_file(Path(__file__)), "special_adapter_sha256": sha256_file(ROOT / "src/genshin_lore/schema20_7_1_special.py"), "provider_contracts": counts, "qualified_provider_count": len(counts), "required_provider_count": 33, "artifact": {"size_bytes": output.stat().st_size, "sha256": sha256_file(output)}, "source_files": files, "inputs": list(inputs.values()), "schema21_required_for_this_projection": False, "product_accepted": False}
    except Exception:
        # Keep the unqualified database for diagnosis; never reuse it as PASS.
        raise
