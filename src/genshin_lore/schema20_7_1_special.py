"""Explicit qualification-preserving storage adapters for 13 special cores.

No parser inference: source mappings are the already sealed family contracts.
Historical namespaces remain family-specific. Unknown Avatar fields remain
in its exact raw row and original qualified shard, not renamed as old fields.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .batch_m1_textmap import iter_textmap_pairs
from .m3_7_1_provider_verify import _physical_rows
from .util import normalize_text

ROOT = Path(__file__).resolve().parents[2]
MAPPINGS = ROOT / "manifests/special-core-7.1/accepted-exact-mappings-v1.json"
SNAPSHOT = "anime-game-data:7.1:9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
STAMP = "2026-10-03T00:00:00+00:00"


def canon(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(x):
    return hashlib.sha256(x.encode()).hexdigest()


def coordinate(path, pointer):
    return sha(canon((SNAPSHOT, path) if pointer is None else (SNAPSHOT, path, pointer)))


def insert(db, table, values):
    db.execute(f'INSERT INTO "{table}" VALUES({",".join("?" for _ in values)})', values)


def assemble(db, source, mappings):
    rows_cache = {}
    records = {r[0] for r in db.execute("SELECT id FROM source_record")}
    files = {r[0] for r in db.execute("SELECT relative_path FROM source_file")}
    counts = []

    def record(path, pointer, raw=None, checksum=None):
        rid = coordinate(path, pointer)
        if rid not in records:
            if raw is None:
                if path not in rows_cache:
                    rows_cache[path] = {r[1]: r for r in _physical_rows((source / path).read_bytes())}
                physical = rows_cache[path][pointer]
                raw, checksum = physical[2], physical[4]
            insert(db, "source_record", (rid, coordinate(path, None), pointer,
                   "physical_textmap_pair" if path.startswith("TextMap/") else "physical_json_array_row",
                   None, checksum, raw))
            records.add(rid)
        return rid

    for m in mappings:
        path = Path(m["candidate_artifact"]["path_machine_local"])
        src = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
        src.row_factory = sqlite3.Row
        try:
            release = json.loads(src.execute("SELECT manifest_json FROM release").fetchone()[0])
            f, run, parser = m["family"], m["ingest_run_id"], m["parser_build_id"]
            insert(db, "parser_build", (parser, m["provider_contract"], "7.1-special-storage-v1",
                   release["builder_sha256"], release.get("input_contract_sha256", release.get("input_sha256")), STAMP))
            insert(db, "ingest_run", (run, SNAPSHOT, parser, canon({"family": f, "contract": m["provider_contract"]}),
                   canon({"qualified_parser_release": release, "qualified_input_artifact": m["candidate_artifact"],
                          "storage_adapter": "schema20-7.1-special-storage-v1", "logical_identity_created": False}), "complete", STAMP))
            for sf in src.execute("SELECT * FROM source_file ORDER BY path"):
                if sf["path"] not in files:
                    insert(db, "source_file", (coordinate(sf["path"], None), SNAPSHOT, sf["path"],
                           sf["git_blob_oid"], sf["sha256"], sf["size_bytes"], "json", str(source)))
                    files.add(sf["path"])
            if m["kind"] == "HISTORICAL_SPECIAL":
                _historical(db, src, m, record)
            else:
                _m5c(db, src, m, record)
            counts.append({"family": f, "contract": m["provider_contract"], "parser": parser,
                           "run": run, "rows": m["selected_evidence_rows"]})
        finally:
            src.close()
    return counts


def _m5c(db, src, m, record):
    f, run, parser = m["family"], m["ingest_run_id"], m["parser_build_id"]
    parents = {r["occurrence_id"]: dict(r) for r in src.execute("SELECT * FROM evidence_row ORDER BY occurrence_id")}
    avatar = f == "avatar_codex_archive"
    for r in parents.values():
        rid = record(r["source_path"], r["source_pointer"], r["raw_record_json"], r["raw_record_sha256"])
        native = json.loads(r["native_identity_json"])
        metadata = canon({"source_raw_lexical_sha256": r["raw_record_sha256"], "qualified_occurrence_id": r["occurrence_id"],
                          "raw_extra_fields": "retained in source row; not renamed to historical fields"})
        if avatar:
            insert(db, "avatar_codex_row_occurrence", (r["occurrence_id"], run, SNAPSHOT, parser,
                   coordinate(r["source_path"], None), rid, r["source_pointer"], "avatar_codex_entry",
                   "AvatarCodexExcelConfigData", r["source_ordinal"], "avatarId", str(native["avatarId"]),
                   sha(canon(json.loads(r["raw_record_json"]))), metadata))
        else:
            insert(db, f + "_row_occurrence", (r["occurrence_id"], f, m["provider_contract"], run,
                   SNAPSHOT, parser, r["source_path"], coordinate(r["source_path"], None), rid,
                   r["source_pointer"], f"/#{r['source_ordinal']}", r["source_ordinal"], r["raw_record_json"],
                   sha(canon(json.loads(r["raw_record_json"]))), r["raw_record_sha256"], r["native_identity_json"], "raw_observed", metadata))
    relations = {r["occurrence_id"]: dict(r) for r in src.execute("SELECT * FROM relation_observation")}
    for table, suffix in (("raw_field", "raw_field_occurrence"), ("opaque_observation", "opaque_occurrence"),
                          ("asset_observation", "asset_occurrence"), ("relation_observation", "relationship_occurrence"),
                          ("text_owner", "text_owner_occurrence"), ("text_resolution", "text_resolution_occurrence"),
                          ("text_candidate", "text_candidate_occurrence"), ("relation_candidate", "relationship_candidate_occurrence")):
        for x in src.execute(f"SELECT * FROM {table} ORDER BY occurrence_id"):
            if table in ("text_resolution", "text_candidate", "relation_candidate"):
                if table == "text_resolution":
                    values = (x["occurrence_id"], x["owner_id"], run, SNAPSHOT, parser, x["locale"],
                              "ambiguous" if x["state"] == "resolved_candidate_set" else x["state"], x["candidate_count"], canon(dict(x)))
                elif table == "text_candidate":
                    values = (x["occurrence_id"], x["resolution_id"], run, SNAPSHOT, parser, x["source_path"], x["locale"],
                              x["tier"], x["pair_ordinal"], x["source_pointer"], json.loads(x["raw_key_json"]), x["raw_text"],
                              x["raw_key_sha256"], x["raw_text_sha256"], x["raw_record_sha256"], canon(dict(x)))
                else:
                    rel = relations[x["relation_id"]]
                    target_id = record(x["target_path"], x["target_pointer"])
                    target_raw = db.execute("SELECT raw_json,raw_sha256 FROM source_record WHERE id=?", (target_id,)).fetchone()
                    if target_raw[1] != x["target_raw_sha256"]:
                        raise ValueError("special candidate source SHA mismatch")
                    if avatar:
                        values = (x["occurrence_id"], run, SNAPSHOT, parser, x["relation_id"],
                                  coordinate(x["target_path"], None), target_id, x["target_pointer"], x["candidate_ordinal"],
                                  Path(x["target_path"]).stem, rel["target_field"], x["target_value_json"],
                                  sha(canon(json.loads(target_raw[0]))), canon(dict(x)))
                    else:
                        values = (x["occurrence_id"], x["relation_id"], run, SNAPSHOT, parser, coordinate(x["target_path"], None),
                                  target_id, x["target_pointer"], x["candidate_ordinal"], x["target_path"], x["target_ordinal"],
                                  x["target_pointer"], rel["target_field"], x["target_value_json"], sha(canon(json.loads(target_raw[0]))),
                                  x["target_raw_sha256"], canon(dict(x)))
            else:
                parent = parents[x["row_id"]]
                rid = coordinate(parent["source_path"], parent["source_pointer"])
                common = (x["occurrence_id"], x["row_id"], run, SNAPSHOT, parser, rid, x["source_pointer"])
                if avatar:
                    common = (x["occurrence_id"], run, SNAPSHOT, parser, x["row_id"], rid, x["source_pointer"])
                    if table == "raw_field":
                        if x["field_path"] not in {"avatarId", "beginTime", "sortFactor", "sortId", "hideWhenDontHave", "FMDEJPDNFKB"}:
                            continue  # Explicit historical field allowlist; raw row remains exact.
                        values = common + (x["field_path"], "present", x["raw_json"], canon(dict(x)), "raw_observed")
                    elif table == "opaque_observation":
                        values = common + (x["field_path"], None, x["raw_json"], x["state"], canon(dict(x)))
                    elif table == "relation_observation":
                        values = common + (x["field_path"], x["raw_value_json"], Path(x["target_path"]).stem,
                                           x["target_field"], x["state"], canon(dict(x)))
                    else:
                        raise ValueError("unexpected Avatar special projection")
                elif table == "raw_field":
                    values = common + (x["field_path"], "present", x["raw_json"], x["field_role"], canon(dict(x)))
                elif table in ("opaque_observation", "asset_observation"):
                    values = common + (x["field_path"], x["raw_json"], x["state"], canon(dict(x)))
                elif table == "relation_observation":
                    values = common + (x["field_path"], x["raw_value_json"], x["target_path"], x["target_field"],
                                       x["condition_json"], x["state"], x["candidate_count"], canon(dict(x)))
                else:
                    values = common + (x["field_path"], x["raw_hash_json"], canon(dict(x)))
            insert(db, ("avatar_codex" if avatar else f) + "_" + suffix, values)


def _historical(db, src, m, record):
    f = m["family"]
    prefix = "quest_codex" if f == "quest_codex_archive" else "world_archive"
    run, parser = m["ingest_run_id"], m["parser_build_id"]
    parents = {r["occurrence_id"]: dict(r) for r in src.execute("SELECT * FROM physical_row ORDER BY occurrence_id")}
    obs = {r["occurrence_id"]: dict(r) for r in src.execute("SELECT * FROM observation")}
    candidates = {}
    for r in src.execute("SELECT * FROM physical_candidate ORDER BY occurrence_id"):
        candidates.setdefault(r["observation_id"], []).append(dict(r))
    owners = {(r["row_id"], r["source_pointer"]): r for r in obs.values() if r["kind"] == "text_owner"}
    # Only cited TextMap pair source_records are materialized, not a third corpus.
    pairs = {}
    wanted = {(r["target_path"], r["target_pointer"]) for xs in candidates.values() for r in xs if r["candidate_kind"] == "textmap"}
    for path in sorted({p for p, _ in wanted}):
        for pair in iter_textmap_pairs((Path(db.execute("SELECT root_path FROM source_file WHERE relative_path=?", (path,)).fetchone()[0]) / path).read_bytes()):
            if (path, pair.pointer) in wanted:
                pairs[(path, pair.pointer)] = pair
    for r in parents.values():
        rid = record(r["source_path"], r["source_pointer"], r["raw_record_json"], r["raw_record_sha256"])
        values = (r["occurrence_id"], run, SNAPSHOT, parser, coordinate(r["source_path"], None), rid,
                  r["source_pointer"])
        native = json.loads(r["native_locator_json"])
        if prefix == "quest_codex":
            values += ("quest_codex_entry" if r["row_kind"] == "primary" else "chapter_support",)
        else:
            values += ()
        values += (Path(r["source_path"]).stem, r["source_ordinal"])
        if prefix == "world_archive":
            values += ("id",)
        values += (str(next(iter(native.values()))), sha(canon(json.loads(r["raw_record_json"]))), canon(dict(r)))
        insert(db, prefix + "_row_occurrence", values)
    for x in sorted(obs.values(), key=lambda r: (r["kind"] != "text_owner", r["occurrence_id"])):
        p = parents[x["row_id"]];rid = coordinate(p["source_path"], p["source_pointer"])
        common = (x["occurrence_id"], run, SNAPSHOT, parser, x["row_id"], rid, x["source_pointer"])
        value = json.loads(x["payload_json"]);kind = x["kind"];meta = canon(dict(x));cs = candidates.get(x["occurrence_id"], [])
        if kind == "raw_field":
            presence = ("array" if isinstance(value.get("raw_value"), list) else "scalar") if prefix == "world_archive" else x["status"]
            values = common + (x["field_path"], presence, canon(value["raw_value"]) if x["status"] == "present" else None, meta)
            suffix = "raw_field_occurrence"
        elif kind == "opaque":
            values = common + (x["field_path"], None, canon(value.get("raw_value")), x["status"])
            if prefix == "world_archive":
                values += ("source field semantic role unresolved",)
            values += (meta,);suffix = "opaque_occurrence"
        elif kind == "asset":
            asset_kind = {"modelPath": "model_path", "image": "image", "chapterIcon": "chapter_icon",
                          "chapterImageHash": "chapter_image_hash", "chapterSerialNumberIcon": "chapter_serial_number_icon"}[x["field_path"]]
            values = common + (x["field_path"], asset_kind, value.get("raw_reference"), x["status"], meta)
            suffix = "asset_reference_occurrence"
        elif kind == "relation":
            values = common + (x["field_path"],)
            if prefix == "world_archive":
                values += ("physical_locator",)
            values += (canon(value.get("raw_value")),)
            if prefix == "world_archive":
                values += (canon(value.get("raw_value")),)
            if prefix == "world_archive":
                values += ("ambiguous" if x["status"] == "resolved_candidate_set" else x["status"], Path(value["target_path"]).stem, value["target_field"])
            else:
                values += (Path(value["target_path"]).stem, value["target_field"], x["status"])
            if prefix == "world_archive":
                values += (canon(cs),)
            values += (meta,);suffix = "relationship_occurrence"
        elif kind == "text_owner":
            role = {"descTextMapHash": "description", "nameTextMapHash": "name", "chapterImageTitleTextMapHash": "image_title",
                    "chapterNumTextMapHash": "chapter_number", "chapterTitleTextMapHash": "chapter_title"}[x["field_path"]]
            values = common + (x["field_path"], role, str(value["raw_hash"]), canon(value["raw_hash"]), meta)
            suffix = "text_owner_occurrence"
        elif kind == "text_resolution":
            owner = owners[(x["row_id"], x["source_pointer"])]
            raw = json.loads(cs[0]["target_value_json"])["raw_text"] if len(cs) == 1 else None
            values = (x["occurrence_id"], run, SNAPSHOT, parser, owner["occurrence_id"], x["row_id"], rid,
                      x["source_pointer"], value["locale"])
            if prefix == "world_archive":
                values += (str(json.loads(owner["payload_json"])["raw_hash"]),)
            norm = normalize_text(raw) if raw is not None else None
            values += (x["status"], raw, norm, sha(raw) if raw is not None else None, sha(norm) if norm is not None else None)
            if prefix == "world_archive":
                values += (canon(cs),)
            values += (meta,);suffix = "text_resolution_occurrence"
        else:
            raise ValueError("unknown qualified observation kind")
        insert(db, prefix + "_" + suffix, values)
        for c in cs:
            if c["candidate_kind"] == "textmap":
                pair = pairs[(c["target_path"], c["target_pointer"])]
                raw_record = '{"key":' + pair.raw_key_json + ',"text":' + pair.raw_text_json + '}'
                crid = record(c["target_path"], c["target_pointer"], raw_record, pair.raw_record_sha256)
                v = json.loads(c["target_value_json"]);text = v["raw_text"]
                insert(db, prefix + "_text_candidate_occurrence", (c["occurrence_id"], run, SNAPSHOT, parser,
                       x["occurrence_id"], coordinate(c["target_path"], None), crid, c["target_pointer"],
                       v["tier"], v["locale"], text, normalize_text(text), sha(text), sha(normalize_text(text)), canon(c)))
            elif prefix == "quest_codex":
                crid = record(c["target_path"], c["target_pointer"])
                target_raw = db.execute("SELECT raw_json FROM source_record WHERE id=?", (crid,)).fetchone()[0]
                insert(db, prefix + "_relationship_candidate_occurrence", (c["occurrence_id"], run, SNAPSHOT, parser,
                       x["occurrence_id"], coordinate(c["target_path"], None), crid, c["target_pointer"], c["candidate_ordinal"],
                       Path(c["target_path"]).stem, value["target_field"], c["target_value_json"], sha(canon(json.loads(target_raw))), canon(c)))
