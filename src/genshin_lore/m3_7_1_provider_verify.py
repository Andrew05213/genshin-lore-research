"""Independent exact-source oracle for a 7.1 M3 family-local candidate.

This module does not import the builder or its row/TextMap projection helpers.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterator

from .batch_m1_verifier import _iter_source_pairs


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "manifests/m3-7.1-provider/input-contract-v1.json"
SCHEMA = ROOT / "schemas/m3-7.1-provider-family-v1.sql"
BUILDER = ROOT / "src/genshin_lore/m3_7_1_provider_build.py"
PHYSICAL_READER = ROOT / "src/genshin_lore/batch_m3_r1_projection.py"
LEXICAL_READER = ROOT / "src/genshin_lore/batch_m1_textmap.py"
SNAPSHOT = "anime-game-data:7.1:9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
TREE = "aa578c08c402af39b41f1ffc95124d10f5410272"
LOCALES = ("CHS", "EN", "JP")
TABLES = ("source_file", "v1_row", "v2_row", "v2_owner", "v2_resolution", "v2_candidate", "release")


def _j(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _id(*parts: object) -> str:
    return _sha(_j(parts).encode())


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "--no-optional-locks", "-C", str(root), *args], text=True).strip()


def _physical_rows(raw: bytes) -> Iterator[tuple[int, str, str, list[tuple[str, object]], str]]:
    text = raw.decode("utf-8-sig")
    decoder = json.JSONDecoder(object_pairs_hook=lambda pairs: pairs)
    position = 0

    def ws(index: int) -> int:
        while index < len(text) and text[index] in " \n\t\r":
            index += 1
        return index

    position = ws(position)
    if text[position:position + 1] != "[":
        raise ValueError("physical M3 source is not JSON array")
    position += 1
    ordinal = 0
    while True:
        position = ws(position)
        if text[position:position + 1] == "]":
            if ws(position + 1) != len(text):
                raise ValueError("physical M3 source trailing content")
            return
        start = position
        row, position = decoder.raw_decode(text, position)
        if not isinstance(row, list) or any(not isinstance(pair, tuple) or len(pair) != 2
                                                or not isinstance(pair[0], str) for pair in row):
            raise ValueError("physical M3 row is not object")
        token = text[start:position]
        yield ordinal, "/" + str(ordinal), token, row, _sha(token.encode())
        ordinal += 1
        position = ws(position)
        if text[position:position + 1] == ",":
            position += 1
            continue
        if text[position:position + 1] != "]":
            raise ValueError("physical M3 array delimiter invalid")


def _schema_oracle(db: sqlite3.Connection) -> None:
    expected = sqlite3.connect(":memory:")
    try:
        expected.executescript(SCHEMA.read_text())
        query = "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        if db.execute(query).fetchall() != expected.execute(query).fetchall():
            raise ValueError("schema, index, or full trigger definition mismatch")
    finally:
        expected.close()


def verify(source_root: str | Path, family_name: str, artifact_path: str | Path,
           report_path: str | Path, contract_path: str | Path = CONTRACT) -> dict:
    source = Path(source_root).resolve()
    artifact = Path(artifact_path).resolve()
    report = json.loads(Path(report_path).read_text())
    contract_raw = Path(contract_path).read_bytes()
    contract = json.loads(contract_raw)
    family = next((entry for entry in contract["families"] if entry["family"] == family_name), None)
    if family is None or contract["source_snapshot"] != SNAPSHOT:
        raise ValueError("frozen family/snapshot mismatch")
    if (_git(source, "rev-parse", "HEAD"), _git(source, "rev-parse", "HEAD^{tree}")) != (
        SNAPSHOT.rsplit(":", 1)[1], TREE
    ) or _git(source, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("7.1 source pin or cleanliness failed")
    identities = {family["source_path"]: family["source_identity"]}
    identities.update({path: contract["textmap_sources"][path] for path in family["v2_textmap_paths"]})
    raw_files = {}
    for relative, frozen in sorted(identities.items()):
        path = source / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError("source file absent/symlink: " + relative)
        raw = path.read_bytes()
        if (len(raw), _sha(raw), _git(source, "rev-parse", "HEAD:" + relative)) != (
            frozen["size_bytes"], frozen["sha256"], frozen["git_blob_oid"]
        ):
            raise ValueError("source file drift: " + relative)
        raw_files[relative] = raw
    if artifact.stat().st_size != report["artifact"]["size_bytes"] or _file_sha(artifact) != report["artifact"]["sha256"]:
        raise ValueError("artifact external size/SHA mismatch")
    if any(Path(str(artifact) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("SQLite sidecar exists")
    conn = sqlite3.connect("file:" + artifact.as_posix() + "?mode=ro&immutable=1", uri=True)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("SQLite integrity/FK failure")
        _schema_oracle(conn)
        release_row = conn.execute("SELECT manifest_json FROM release WHERE id=1").fetchone()
        if release_row is None or conn.execute("SELECT COUNT(*) FROM release").fetchone()[0] != 1:
            raise ValueError("release seal missing/extra")
        release = json.loads(release_row[0])
        if {key: value for key, value in report.items() if key != "artifact"} != release:
            raise ValueError("external report/internal release mismatch")
        if (release["source_snapshot"], release["family"], release["source_file"],
                release["frozen_input_contract_sha256"], release["schema_sha256"],
                release["builder_implementation_sha256"]) != (
            SNAPSHOT, family_name, family["source_path"], _sha(contract_raw),
            _file_sha(SCHEMA), _file_sha(BUILDER)
        ):
            raise ValueError("release identity mismatch")
        if (release["contract"], release["source_commit"], release["source_tree"],
                release["source_file_sha256"], release["textmap_file_sha256"],
                release["declared_key_fields"], release["declared_text_fields"],
                release["product_accepted"], release["research_accepted"],
                release["logical_identity_created"]) != (
            "m3-7.1-family-provider-candidate-v1", SNAPSHOT.rsplit(":", 1)[1], TREE,
            family["source_identity"]["sha256"],
            {path: contract["textmap_sources"][path]["sha256"]
             for path in family["v2_textmap_paths"]},
            family["key_observation_fields"], family["v2_text_fields"], False, False, False
        ):
            raise ValueError("release source scope or authority semantics mismatch")
        helper_shas = {"physical_array_reader": _file_sha(PHYSICAL_READER),
                       "textmap_lexical_reader": _file_sha(LEXICAL_READER),
                       "textmap_input_cache": _file_sha(ROOT / "src/genshin_lore/textmap_input_cache.py")}
        observation = None
        if family["v2_textmap_paths"]:
            tm_parser = "m3-71-textmap-parser:" + _id(
                "six-file-lexical-textmap-v2-input-cache", helper_shas["textmap_lexical_reader"],
                helper_shas["textmap_input_cache"],
                _file_sha(SCHEMA),
            )[:40]
            tm_run = "m3-71-textmap-run:" + _id(
                SNAPSHOT, tm_parser,
                [(path, contract["textmap_sources"][path]["sha256"])
                 for path in family["v2_textmap_paths"]],
            )[:40]
            observation = {"snapshot_id": SNAPSHOT, "parser_build_id": tm_parser,
                           "ingest_run_id": tm_run,
                           "scope": "family-local exact six-file TextMap observation; no separate shard"}
        if release["parser_helper_sha256"] != helper_shas or release["textmap_observation"] != observation:
            raise ValueError("parser helper or TextMap observation identity mismatch")
        config = _j({"family": family_name, "source": family["source_path"],
                     "keys": family["key_observation_fields"], "text": family["v2_text_fields"],
                     "v1": family["v1_contract"], "v2": family["v2_contract"]})
        parsers = {version: "m3-7.1-parser:" + _id("parser-v1", version, _file_sha(BUILDER),
                                                     helper_shas, _file_sha(SCHEMA),
                                                     _sha(config.encode()))[:40]
                   for version in ("v1", "v2")}
        runs = {version: "m3-7.1-run:" + _id("run-v1", SNAPSHOT, family_name, version,
                                             parsers[version], _sha(contract_raw),
                                             family["source_identity"]["sha256"],
                                             [(path, contract["textmap_sources"][path]["sha256"])
                                              for path in family["v2_textmap_paths"]] if version == "v2" else [])[:40]
                for version in ("v1", "v2")}
        if release["parser_build_ids"] != parsers or release["ingest_run_ids"] != runs:
            raise ValueError("exact parser/run derivation mismatch")
        if len(set(runs.values())) != 2:
            raise ValueError("v1/v2 run identity collision")
        files_actual = conn.execute("SELECT path,role,size_bytes,sha256,git_blob_oid FROM source_file ORDER BY path").fetchall()
        files_expected = [(path, "primary" if path == family["source_path"] else "textmap",
                           value["size_bytes"], value["sha256"], value["git_blob_oid"])
                          for path, value in sorted(identities.items())]
        if files_actual != files_expected:
            raise ValueError("source file rows differ from exact source")
        source_rows = list(_physical_rows(raw_files[family["source_path"]]))
        if len(source_rows) != family["source_physical_rows"]:
            raise ValueError("source row denominator mismatch")
        key_fields = set(family["key_observation_fields"])
        text_fields = set(family["v2_text_fields"])
        hashes = {str(value) for _, _, _, pairs, _ in source_rows
                  for field, value in pairs if field in text_fields}
        by_hash: dict[tuple[str, str], list[tuple]] = defaultdict(list)
        for relative in family["v2_textmap_paths"]:
            name = relative.rsplit("/", 1)[1]
            locale = next((x for x in LOCALES if name.endswith(x + ".json")), None)
            if locale is None:
                raise ValueError("TextMap locale path mismatch")
            tier = "medium" if "_Medium" in name else "main"
            for item in _iter_source_pairs(raw_files[relative], wanted_keys=hashes):
                if item.key_text in hashes:
                    key_json, text_json = item.raw_key_json, item.raw_text_json
                    by_hash[(locale, item.key_text)].append((
                        tier, relative, item.pair_ordinal, "/", item.pointer,
                        key_json, text_json, item.raw_text,
                        _sha(key_json.encode()), _sha(text_json.encode()),
                        _sha(f'{{"key":{key_json},"text":{text_json}}}'.encode()),
                        _sha(item.raw_text.encode()),
                    ))
        for values in by_hash.values():
            values.sort(key=lambda item: (item[1], item[2]))
        counts = Counter({key: 0 for key in ("v1_rows", "v2_rows", "text_owners",
                                             "text_resolutions", "text_candidates")})
        consumed = Counter()
        for ordinal, pointer, token, pairs, token_sha in source_rows:
            v1_id = "m3-71-v1:" + _id(family_name, runs["v1"], family["source_path"], pointer, token_sha)[:40]
            v2_id = "m3-71-v2:" + _id(family_name, runs["v2"], family["source_path"], pointer, token_sha)[:40]
            observations = [{"field": field, "pair_ordinal": index, "raw_value": value}
                            for index, (field, value) in enumerate(pairs) if field in key_fields]
            v1_actual = conn.execute("SELECT * FROM v1_row WHERE source_path=? AND source_ordinal=?",
                                     (family["source_path"], ordinal)).fetchone()
            v2_actual = conn.execute("SELECT * FROM v2_row WHERE source_path=? AND source_ordinal=?",
                                     (family["source_path"], ordinal)).fetchone()
            if v1_actual != (v1_id, SNAPSHOT, parsers["v1"], runs["v1"], family["source_path"],
                             ordinal, pointer, token, token_sha, _j(observations)):
                raise ValueError("v1 physical row differs at ordinal " + str(ordinal))
            if v2_actual != (v2_id, SNAPSHOT, parsers["v2"], runs["v2"], family["source_path"],
                             ordinal, pointer, token, token_sha):
                raise ValueError("v2 physical row differs at ordinal " + str(ordinal))
            counts["v1_rows"] += 1
            counts["v2_rows"] += 1
            for pair_ordinal, (field, value) in enumerate(pairs):
                if field in key_fields | text_fields:
                    if type(value) is not int:
                        raise ValueError("declared field type mismatch")
                    consumed[field] += 1
                if field not in text_fields:
                    continue
                key = str(value)
                owner_id = "m3-71-owner:" + _id(family_name, runs["v2"], v2_id, field, pair_ordinal)[:40]
                per_locale = {locale: by_hash.get((locale, key), ()) for locale in LOCALES}
                states = {locale: "missing" if not per_locale[locale] else
                          "resolved_unique" if len(per_locale[locale]) == 1 else "resolved_candidate_set"
                          for locale in LOCALES}
                overall = "resolved_candidate_set" if "resolved_candidate_set" in states.values() else (
                    "partial_missing" if "missing" in states.values() else "resolved_all_locales")
                count = sum(map(len, per_locale.values()))
                expected_owner = (owner_id, v2_id, field, pair_ordinal, _j(value), key, overall, count)
                if conn.execute("SELECT * FROM v2_owner WHERE owner_id=?", (owner_id,)).fetchone() != expected_owner:
                    raise ValueError("v2 text owner differs at " + pointer + ":" + field)
                counts["text_owners"] += 1
                for locale in LOCALES:
                    resolution_id = "m3-71-resolution:" + _id(owner_id, locale)[:40]
                    expected_resolution = (resolution_id, owner_id, locale, states[locale], len(per_locale[locale]))
                    if conn.execute("SELECT * FROM v2_resolution WHERE resolution_id=?", (resolution_id,)).fetchone() != expected_resolution:
                        raise ValueError("v2 TextMap resolution differs at " + pointer + ":" + field)
                    counts["text_resolutions"] += 1
                    counts[locale + ":" + states[locale]] += 1
                    for (tier, relative, pair_index, source_record_pointer,
                         text_pointer, key_json, text_json, text, key_sha,
                         text_sha, record_sha, rendered_sha) in per_locale[locale]:
                        candidate_id = "m3-71-candidate:" + _id(owner_id, relative, pair_index)[:40]
                        expected_candidate = (candidate_id, owner_id, observation["snapshot_id"],
                                              observation["parser_build_id"],
                                              observation["ingest_run_id"],
                                              locale, tier, relative, pair_index,
                                              source_record_pointer, text_pointer,
                                              key_json, text_json, text, key_sha, text_sha,
                                              record_sha, rendered_sha)
                        if conn.execute("SELECT * FROM v2_candidate WHERE candidate_id=?", (candidate_id,)).fetchone() != expected_candidate:
                            raise ValueError("v2 physical TextMap candidate differs at " + pointer + ":" + field)
                        counts["text_candidates"] += 1
        for field, expected in family["consumed_field_observations"].items():
            if consumed[field] != expected["occurrences"]:
                raise ValueError("declared field occurrence denominator mismatch: " + field)
        for table, count in (("v1_row", counts["v1_rows"]), ("v2_row", counts["v2_rows"]),
                             ("v2_owner", counts["text_owners"]),
                             ("v2_resolution", counts["text_resolutions"]),
                             ("v2_candidate", counts["text_candidates"])):
            if conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] != count:
                raise ValueError("extra/omitted physical rows: " + table)
        if release["counts"] != dict(sorted(counts.items())):
            raise ValueError("release business count seal mismatch")
        if release["family_contracts"] != {"v1": family["v1_contract"], "v2": family["v2_contract"]}:
            raise ValueError("family contract identities differ")
        if release["declared_text_fields"] != family["v2_text_fields"]:
            raise ValueError("v2 source-authorized text owner list differs")
    finally:
        conn.close()
    for relative, frozen in sorted(identities.items()):
        if _file_sha(source / relative) != frozen["sha256"]:
            raise ValueError("source input drift after verifier")
    return {"status": "PASS", "family": family_name, "counts": dict(sorted(counts.items())),
            "artifact": report["artifact"], "independent_source_oracle": True,
            "schema_and_all_guard_definitions_exact": True, "no_extras_or_omissions": True}
