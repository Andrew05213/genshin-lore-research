"""Independent rowset oracle for the scoped 7.1 Schema20 materialization.

No import of the assembly builder or its projection/status helpers. Primary
rows are independently reread from source; every child is rebound to the
previously sealed, source-oracled M3 artifact (including all candidates).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

from .schema20_7_1_guards import verify as verify_candidate_schema
from .m3_7_1_provider_verify import _physical_rows
from .research_index import sha256_file

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = "anime-game-data:7.1:9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"


def verify(source: Path, artifact: Path, report: dict):
    def git(*args):
        return subprocess.check_output(["git", "--no-optional-locks", "-C", str(source), *args], text=True).strip()
    if git("rev-parse", "HEAD") != SNAPSHOT.rsplit(":", 1)[1] or git("rev-parse", "HEAD^{tree}") != "aa578c08c402af39b41f1ffc95124d10f5410272" or git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("independent source pin/cleanliness mismatch")
    if artifact.stat().st_size != report["artifact"]["size_bytes"] or sha256_file(artifact) != report["artifact"]["sha256"]:
        raise ValueError("external assembly artifact seal mismatch")
    if any(Path(str(artifact) + s).exists() for s in ("-wal", "-shm", "-journal")):
        raise ValueError("assembly sidecar")
    mappings = json.loads((ROOT / "manifests/m3-7.1-provider/accepted-exact-mappings-v1.json").read_text())["mappings"]
    db = sqlite3.connect(artifact.as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    try:
        verify_candidate_schema(db)
        if [r[0] for r in db.execute("SELECT version FROM schema_migration ORDER BY version")] != list(range(1, 21)):
            raise ValueError("ledger mismatch")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("integrity/FK failure")
        if {r[0] for r in db.execute("SELECT id FROM source_snapshot")} != {SNAPSHOT}:
            raise ValueError("foreign or missing snapshot")
        for row in db.execute("SELECT * FROM source_file ORDER BY relative_path"):
            path = source / row["relative_path"]
            if path.is_symlink() or path.stat().st_size != row["size_bytes"] or sha256_file(path) != row["sha256"] or git("rev-parse", "HEAD:" + row["relative_path"]) != row["upstream_blob_oid"]:
                raise ValueError("independent source byte/blob mismatch")
        summary = []
        physical = {}
        for m in mappings:
            for p in m["independent_verifier_reports"]:
                if sha256_file(ROOT / p["path"]) != p["sha256"]:
                    raise ValueError("inherited independent source proof seal drift")
            f, run, version = m["family"], m["ingest_run_id"], m["version"]
            src = sqlite3.connect(Path(m["candidate_artifact"]["path_machine_local"]).as_uri() + "?mode=ro&immutable=1", uri=True)
            src.row_factory = sqlite3.Row
            try:
                if sha256_file(Path(m["candidate_artifact"]["path_machine_local"])) != m["candidate_artifact"]["sha256"]:
                    raise ValueError("source-oracled input seal drift")
                release = json.loads(src.execute("SELECT manifest_json FROM release").fetchone()[0])
                parser_row = db.execute("SELECT fingerprint,config_checksum FROM parser_build WHERE id=?", (m["parser_build_id"],)).fetchone()
                parser_config = json.dumps({"family": f, "source": m["source_path"], "keys": release["declared_key_fields"], "text": release["declared_text_fields"], "v1": release["family_contracts"]["v1"], "v2": release["family_contracts"]["v2"]}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                if tuple(parser_row or ()) != (release["builder_implementation_sha256"], hashlib.sha256(parser_config.encode()).hexdigest()):
                    raise ValueError("qualified parser implementation/config provenance drift")
                run_config = json.loads(db.execute("SELECT config_json FROM ingest_run WHERE id=?", (run,)).fetchone()[0])
                if run_config.get("qualified_parser_release") != release:
                    raise ValueError("qualified parser release metadata lost")
                path = m["source_path"]
                if path not in physical:
                    physical[path] = list(_physical_rows((source / path).read_bytes()))
                actual = list(db.execute(f"SELECT * FROM {f}_row_occurrence WHERE ingest_run_id=? ORDER BY source_ordinal", (run,)))
                expected = list(src.execute(f"SELECT * FROM {version}_row ORDER BY source_ordinal"))
                if len(actual) != len(expected) or len(actual) != len(physical[path]):
                    raise ValueError("source denominator mismatch")
                for r, e, raw in zip(actual, expected, physical[path]):
                    if (r["source_ordinal"], r["source_pointer"], r["raw_record_json"], r["raw_record_sha256"]) != (raw[0], raw[1], raw[2], raw[4]):
                        raise ValueError("source-to-row mismatch")
                    for target, original in (("occurrence_id", "occurrence_id"), ("snapshot_id", "snapshot_id"), ("parser_build_id", "parser_build_id"), ("ingest_run_id", "ingest_run_id"), ("source_file_path", "source_path")):
                        if r[target] != e[original]:
                            raise ValueError("row evidence identity mismatch")
                    if r["family"] != f or r["family_contract_version"] != m["provider_contract"]:
                        raise ValueError("row contract mismatch")
                    bound = db.execute("SELECT sf.relative_path,sr.pointer,sr.raw_sha256,sr.raw_json FROM source_record sr JOIN source_file sf ON sf.id=sr.source_file_id WHERE sr.id=?", (r["source_record_id"],)).fetchone()
                    if bound is None or tuple(bound) != (path, raw[1], raw[4], raw[2]):
                        raise ValueError("exact source record binding missing/drifted")
                    if version == "v1" and r["raw_key_observations_json"] != e["raw_key_observations_json"]:
                        raise ValueError("raw key observations lost")
                child_counts = {}
                if version == "v2":
                    specs = {
                        "owner": ("v2_owner", "text_owner_occurrence", "owner_id", {"resolution_status": "overall_resolution_state"}),
                        "resolution": ("v2_resolution", "text_resolution_occurrence", "resolution_id", {"resolution_status": "state"}),
                        "candidate": ("v2_candidate", "text_candidate_occurrence", "candidate_id", {"relative_path": "source_path", "pointer": "source_pointer", "shard_tier": "tier"}),
                    }
                    for kind, (st, suffix, key, rename) in specs.items():
                        target = list(db.execute(f"SELECT * FROM {f}_{suffix} WHERE ingest_run_id=? ORDER BY {key}", (run,)))
                        original = list(src.execute(f"SELECT * FROM {st} ORDER BY {key}"))
                        if len(target) != len(original):
                            raise ValueError("child denominator mismatch")
                        for r, e in zip(target, original):
                            for column in r.keys():
                                if column == "ingest_run_id":
                                    want = run
                                else:
                                    want = e[rename.get(column, column)]
                                    if column == "resolution_status" and want == "resolved_candidate_set":
                                        want = "ambiguous"
                                if r[column] != want:
                                    raise ValueError(f"child state/provenance mismatch: {f}/{kind}/{column}")
                        child_counts[kind] = len(target)
                    config = json.loads(db.execute("SELECT config_json FROM ingest_run WHERE id=?", (run,)).fetchone()[0])
                    if config["textmap_observation"] != release["textmap_observation"]:
                        raise ValueError("TextMap run identity lost in Q2 compatibility projection")
                    tm = release["textmap_observation"]
                    if tm:
                        tm_parser = db.execute("SELECT fingerprint,config_checksum FROM parser_build WHERE id=?", (tm["parser_build_id"],)).fetchone()
                        if tuple(tm_parser or ()) != (release["parser_helper_sha256"]["textmap_lexical_reader"], release["schema_sha256"]):
                            raise ValueError("TextMap parser implementation provenance drift")
                summary.append({"family": f, "contract": m["provider_contract"], "rows": len(actual), "children": child_counts})
            finally:
                src.close()
        # Extra runs/rows in scoped family namespaces must not be hidden.
        for family in {m["family"] for m in mappings}:
            allowed = {m["ingest_run_id"] for m in mappings if m["family"] == family}
            if {r[0] for r in db.execute(f"SELECT DISTINCT ingest_run_id FROM {family}_row_occurrence")} != allowed:
                raise ValueError("extra or omitted family run")
        special = None
        if report["qualified_provider_count"] == 33:
            from .schema20_7_1_special_oracle import verify as verify_special
            special = verify_special(source, db)
        elif db.execute("SELECT COUNT(*) FROM source_record").fetchone()[0] != sum(len(v) for v in physical.values()):
            raise ValueError("source record extras/omissions")
        return {"status": "PASS", "scope": "33 core provider contracts" if special else "20 M3 provider contracts only", "special_core": special, "schema_version": 20, "source_primary_oracle": "full physical source bytes to rows", "text_oracle": "full sealed input-to-Schema20 child equality; inherited sealed independent source/TextMap proof", "provider_contracts": summary, "no_extras_no_omissions": True, "source_primary_rows_distinct": sum(len(v) for v in physical.values()), "artifact": report["artifact"]}
    finally:
        db.close()
