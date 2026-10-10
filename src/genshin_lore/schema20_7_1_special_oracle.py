"""Independent special-input to Schema20 rowset/source reconciliation.

Does not import the storage adapter. All original child observations and
candidate sets are compared, including explicitly nonprojected Avatar extras.
"""
import hashlib
import json
import sqlite3
from pathlib import Path

from .batch_m1_textmap import iter_textmap_pairs
from .m3_7_1_provider_verify import _physical_rows
from .research_index import sha256_file

ROOT = Path(__file__).resolve().parents[2]


def verify(source, db):
    mappings = json.loads((ROOT / "manifests/special-core-7.1/accepted-exact-mappings-v1.json").read_text())["mappings"]
    summary = []
    for m in mappings:
        path = Path(m["candidate_artifact"]["path_machine_local"])
        if sha256_file(path) != m["candidate_artifact"]["sha256"]:
            raise ValueError("special source-oracled input seal drift")
        src = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
        src.row_factory = sqlite3.Row
        try:
            release = json.loads(src.execute("SELECT manifest_json FROM release").fetchone()[0])
            run, parser, f = m["ingest_run_id"], m["parser_build_id"], m["family"]
            config = json.loads(db.execute("SELECT config_json FROM ingest_run WHERE id=?", (run,)).fetchone()[0])
            if config["qualified_parser_release"] != release or config["qualified_input_artifact"] != m["candidate_artifact"]:
                raise ValueError("special parser/release binding drift")
            pr = db.execute("SELECT fingerprint,config_checksum FROM parser_build WHERE id=?", (parser,)).fetchone()
            if tuple(pr) != (release["builder_sha256"], release.get("input_contract_sha256", release.get("input_sha256"))):
                raise ValueError("special parser fingerprint drift")
            historical = m["kind"] == "HISTORICAL_SPECIAL"
            prefix = "avatar_codex" if f == "avatar_codex_archive" else "quest_codex" if f == "quest_codex_archive" else f
            rt = prefix + "_row_occurrence"
            pk = "id" if historical or f == "avatar_codex_archive" else "occurrence_id"
            original = {r["occurrence_id"]: dict(r) for r in src.execute("SELECT * FROM " + ("physical_row" if historical else "evidence_row"))}
            rows = list(db.execute(f"SELECT * FROM {rt} WHERE ingest_run_id=?", (run,)))
            if {r[pk] for r in rows} != set(original) or len(rows) != m["selected_evidence_rows"]:
                raise ValueError("special row identity/denominator mismatch")
            for r in rows:
                e = original[r[pk]]
                binding = db.execute("SELECT sf.relative_path,sr.pointer,sr.raw_json,sr.raw_sha256 FROM source_record sr JOIN source_file sf ON sf.id=sr.source_file_id WHERE sr.id=?", (r["source_record_id"],)).fetchone()
                if tuple(binding) != (e["source_path"], e["source_pointer"], e["raw_record_json"], e["raw_record_sha256"]):
                    raise ValueError("special physical row binding drift")
                if (r["snapshot_id"], r["parser_build_id"], r["ingest_run_id"]) != (e["snapshot_id"], parser, run):
                    raise ValueError("special row provenance drift")
            children = {}
            omitted = []
            if not historical:
                table_map = {"raw_field": "raw_field_occurrence", "opaque_observation": "opaque_occurrence",
                             "asset_observation": "asset_occurrence", "relation_observation": "relationship_occurrence",
                             "relation_candidate": "relationship_candidate_occurrence", "text_owner": "text_owner_occurrence",
                             "text_resolution": "text_resolution_occurrence", "text_candidate": "text_candidate_occurrence"}
                for st, suffix in table_map.items():
                    source_rows = {r["occurrence_id"]: dict(r) for r in src.execute(f"SELECT * FROM {st}")}
                    if f == "avatar_codex_archive" and st == "raw_field":
                        allowed = {"avatarId", "beginTime", "sortFactor", "sortId", "hideWhenDontHave", "FMDEJPDNFKB"}
                        omitted += [e for e in source_rows.values() if e["field_path"] not in allowed]
                        source_rows = {k: e for k, e in source_rows.items() if e["field_path"] in allowed}
                    if not source_rows and st in ("asset_observation", "text_owner", "text_resolution", "text_candidate") and f == "avatar_codex_archive":
                        continue
                    if not source_rows and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (prefix + "_" + suffix,)).fetchone() is None:
                        children[st] = 0
                        continue
                    actual = list(db.execute(f"SELECT * FROM {prefix}_{suffix} WHERE ingest_run_id=?", (run,)))
                    if {r[pk] for r in actual} != set(source_rows):
                        raise ValueError("special child rowset mismatch: " + st)
                    for r in actual:
                        e = source_rows[r[pk]]
                        if json.loads(r["metadata_json"]) != e:
                            raise ValueError("special child original observation lost")
                        aliases = {"source_pointer": "source_pointer", "field_path": "field_path", "field_name": "field_path",
                                   "raw_json": "raw_json", "raw_value_json": "raw_value_json", "owner_hash_json": "raw_hash_json",
                                   "owner_occurrence_id": "owner_id", "resolution_occurrence_id": "resolution_id",
                                   "relationship_occurrence_id": "relation_id", "row_occurrence_id": "row_id",
                                   "source_row_occurrence_id": "row_id", "candidate_count": "candidate_count",
                                   "candidate_ordinal": "candidate_ordinal", "locale": "locale", "pair_ordinal": "pair_ordinal",
                                   "relative_path": "source_path", "pointer": "source_pointer", "raw_text": "raw_text",
                                   "raw_key_sha256": "raw_key_sha256", "raw_text_sha256": "raw_text_sha256",
                                   "raw_record_sha256": "raw_record_sha256", "shard_tier": "tier"}
                        for col, old in aliases.items():
                            if col in r.keys() and old in e and r[col] != e[old]:
                                raise ValueError(f"special child field mismatch: {st}/{col}")
                        if "state" in e:
                            state = "ambiguous" if st == "text_resolution" and e["state"] == "resolved_candidate_set" else e["state"]
                            if r["resolution_status"] != state:
                                raise ValueError("special child state drift")
                    children[st] = len(actual)
            else:
                groups = {kind: {r["occurrence_id"]: dict(r) for r in src.execute("SELECT * FROM observation WHERE kind=?", (kind,))}
                          for kind in ("raw_field", "opaque", "asset", "relation", "text_owner", "text_resolution")}
                suffixes = {"raw_field": "raw_field_occurrence", "opaque": "opaque_occurrence", "asset": "asset_reference_occurrence",
                            "relation": "relationship_occurrence", "text_owner": "text_owner_occurrence", "text_resolution": "text_resolution_occurrence"}
                for kind, expected in groups.items():
                    actual = list(db.execute(f"SELECT * FROM {prefix}_{suffixes[kind]} WHERE ingest_run_id=?", (run,)))
                    if {r["id"] for r in actual} != set(expected):
                        raise ValueError("historical special observation rowset mismatch")
                    for r in actual:
                        e = expected[r["id"]];payload = json.loads(e["payload_json"])
                        if json.loads(r["metadata_json"]) != e or r["source_pointer"] != e["source_pointer"]:
                            raise ValueError("historical observation coordinate binding drift")
                        if "field_name" in r.keys() and r["field_name"] != e["field_path"].split("@")[0]:
                            raise ValueError("historical field binding drift")
                        if "resolution_status" in r.keys() and kind != "text_owner":
                            expected_state = "ambiguous" if prefix == "world_archive" and e["status"] == "resolved_candidate_set" else e["status"]
                            if r["resolution_status"] != expected_state:
                                raise ValueError("historical observation state drift")
                        if kind == "text_resolution":
                            candidates = [dict(c) for c in src.execute("SELECT * FROM physical_candidate WHERE observation_id=?", (e["occurrence_id"],))]
                            want_text = json.loads(candidates[0]["target_value_json"])["raw_text"] if len(candidates) == 1 else None
                            if r["locale"] != payload["locale"] or r["raw_text"] != want_text:
                                raise ValueError("historical resolution content drift")
                        if kind == "relation" and prefix == "world_archive":
                            expected_set = {c[0] for c in src.execute("SELECT occurrence_id FROM physical_candidate WHERE observation_id=?", (e["occurrence_id"],))}
                            if {c["occurrence_id"] for c in json.loads(r["candidate_scopes_json"])} != expected_set:
                                raise ValueError("World full candidate set drift")
                    children[kind] = len(actual)
                for candidate_kind, suffix in (("textmap", "text_candidate_occurrence"), ("relation", "relationship_candidate_occurrence")):
                    if prefix == "world_archive" and candidate_kind == "relation":
                        continue
                    expected = {r["occurrence_id"]: dict(r) for r in src.execute("SELECT * FROM physical_candidate WHERE candidate_kind=?", (candidate_kind,))}
                    actual = list(db.execute(f"SELECT * FROM {prefix}_{suffix} WHERE ingest_run_id=?", (run,)))
                    if {r["id"] for r in actual} != set(expected):
                        raise ValueError("historical full candidate rowset drift")
                    for r in actual:
                        e = expected[r["id"]]
                        if json.loads(r["metadata_json"]) != e or r["source_pointer"] != e["target_pointer"]:
                            raise ValueError("historical candidate source identity drift")
                        if candidate_kind == "textmap" and r["raw_text"] != json.loads(e["target_value_json"])["raw_text"]:
                            raise ValueError("historical candidate text drift")
                    children[candidate_kind + "_candidates"] = len(actual)
            summary.append({"family": f, "contract": m["provider_contract"], "run": run, "rows": len(rows), "children": children,
                            "unprojected_new_raw_field_observations": omitted,
                            "unprojected_field_delivery": "exact raw row + original qualified shard; no semantic rename"})
        finally:
            src.close()
    # Full physical source_record oracle includes primary, lookup and cited
    # TextMap pairs. It is NOT just a representative row count check.
    cache = {};n = 0
    for path, pointer, raw, seal in db.execute("SELECT f.relative_path,r.pointer,r.raw_json,r.raw_sha256 FROM source_record r JOIN source_file f ON f.id=r.source_file_id ORDER BY f.relative_path,r.pointer"):
        if path not in cache:
            if path.startswith("TextMap/"):
                wanted = {r[0] for r in db.execute("SELECT sr.pointer FROM source_record sr JOIN source_file sf ON sf.id=sr.source_file_id WHERE sf.relative_path=?", (path,))}
                cache[path] = {p.pointer: ('{"key":' + p.raw_key_json + ',"text":' + p.raw_text_json + '}', p.raw_record_sha256)
                               for p in iter_textmap_pairs((source / path).read_bytes()) if p.pointer in wanted}
            else:
                cache[path] = {r[1]: (r[2], r[4]) for r in _physical_rows((source / path).read_bytes())}
        if cache[path].get(pointer) != (raw, seal):
            raise ValueError("full source_record raw token/checksum oracle mismatch")
        n += 1
    return {"status": "PASS", "provider_count": len(summary), "providers": summary, "source_records_verified": n,
            "proof_level": "full sealed-input child/candidate sets + all materialized physical source records from pinned bytes"}
