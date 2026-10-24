"""Independent source-to-artifact check for the 7.1 Character physical foundation.

This deliberately does not import the builder, its row decoders, or its projection
helpers.  It compares every projected physical coordinate and source value.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from .loading_tips_source import array_records, sha
from .sealed_sqlite import open_immutable_readonly


ROOT = Path(__file__).resolve().parents[2]
COMMIT = "9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
TREE = "aa578c08c402af39b41f1ffc95124d10f5410272"
SNAPSHOT = "anime-game-data:7.1:" + COMMIT
PRIMARY = {
    "AvatarExcelConfigData.json": "id",
    "FetterInfoExcelConfigData.json": "fetterId",
    "FetterStoryExcelConfigData.json": "fetterId",
    "FettersExcelConfigData.json": "fetterId",
    "AvatarCostumeExcelConfigData.json": "skinId",
    "ProfilePictureExcelConfigData.json": "id",
    "AvatarRenameExcelConfigData.json": "renameID",
}
HASH_FIELDS = {
    "AvatarExcelConfigData.json": ("nameTextMapHash", "descTextMapHash"),
    "FetterInfoExcelConfigData.json": ("avatarConstellationAfterTextMapHash", "avatarConstellationBeforTextMapHash", "avatarDetailTextMapHash", "avatarNativeTextMapHash", "avatarTitleTextMapHash", "avatarVisionAfterTextMapHash", "avatarVisionBeforTextMapHash", "cvChineseTextMapHash", "cvEnglishTextMapHash", "cvJapaneseTextMapHash", "cvKoreanTextMapHash"),
    "FetterStoryExcelConfigData.json": ("storyContext2TextMapHash", "storyContextTextMapHash", "storyTitle2TextMapHash", "storyTitleLockedTextMapHash", "storyTitleTextMapHash"),
    "FettersExcelConfigData.json": ("voiceFileTextTextMapHash", "voiceTitleLockedTextMapHash", "voiceTitleTextMapHash"),
    "AvatarCostumeExcelConfigData.json": ("nameTextMapHash", "descTextMapHash"),
    "ProfilePictureExcelConfigData.json": ("nameTextMapHash", "unlockDescTextMapHash"),
}
ARRAY_FIELDS = {
    "FetterInfoExcelConfigData.json": {"finishConds"},
    "FetterStoryExcelConfigData.json": {"finishConds", "openConds", "tips"},
    "FettersExcelConfigData.json": {"openConds", "tips"},
    "AvatarRenameExcelConfigData.json": {"multiLangDefaultNameList"},
}
IMPL = (
    "src/genshin_lore/character_7_1_foundation_build.py",
    "src/genshin_lore/character_7_1_physical.py",
    "src/genshin_lore/character_7_1_voice.py",
    "src/genshin_lore/loading_tips_source.py",
    "schemas/character-7.1-physical-foundation-v1.sql",
)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _id(*parts):
    return "character71:" + _digest(_json(parts))[:40]


def _eq(actual, expected, label):
    if actual != expected:
        raise ValueError(f"{label} mismatch: actual={actual!r}, expected={expected!r}")


def _source_check(source, scope):
    _eq(subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD", "HEAD^{tree}"], text=True).splitlines(), [COMMIT, TREE], "source commit/tree")
    _eq(subprocess.check_output(["git", "-C", str(source), "status", "--porcelain=v1"], text=True).strip(), "", "source cleanliness")
    _eq((scope["source_commit"], scope["source_tree"], scope["snapshot_id"]), (COMMIT, TREE, SNAPSHOT), "scope identity")
    expected_paths = {"ExcelBinOutput/" + name for name in PRIMARY}
    expected_paths |= {"TextMap/TextMap" + tier + locale + ".json" for tier in ("", "_Medium") for locale in ("CHS", "EN", "JP")}
    expected_paths.add("BinOutput/Voice/Lut/Lut.json")
    expected_paths.update(scope["voice_scope"]["selected_fetter_item_files"])
    _eq(({entry["path"] for entry in scope["files"]}, len(scope["files"])), (expected_paths, 18), "source denominator")
    historical = json.loads((ROOT / "manifests/character/anime-game-data-7.0-phase4c5b-character-fetter-full.json").read_text())
    lore = json.loads((ROOT / "manifests/character-lore/scope.json").read_text())
    historical_paths = {entry["relative_path"] for entry in historical["files"]} | {entry["path"] for entry in lore["files"]}
    _eq(expected_paths, historical_paths, "accepted Character consumer source closure")
    for entry in scope["files"]:
        path = source / entry["path"]
        _eq((path.stat().st_size, sha(path)), (entry["size_bytes"], entry["sha256"]), "source bytes " + entry["path"])
        _eq(subprocess.check_output(["git", "-C", str(source), "rev-parse", f"{COMMIT}:{entry['path']}"], text=True).strip(), entry["blob"], "source blob " + entry["path"])


def _rows(conn, query, params=()):
    return [tuple(row) for row in conn.execute(query, params)]


def _candidate_rows_by_relation(conn):
    """Read actual physical candidates once; expected rows stay source-derived."""
    grouped = defaultdict(list)
    for row in conn.execute("SELECT * FROM foundation_candidate ORDER BY id"):
        grouped[row[1]].append(tuple(row))
    return grouped


def _verify_schema(conn):
    """Reconstruct the full sealed schema from the tracked SQL contract, not DB SQL."""
    expected = sqlite3.connect(":memory:")
    try:
        expected.executescript((ROOT / "schemas/character-7.1-physical-foundation-v1.sql").read_text())
        tables = ("foundation_source_file", "foundation_primary_row", "foundation_field", "foundation_text_owner", "foundation_voice_lut", "foundation_voice_item", "foundation_voice_link", "foundation_audio_variant", "foundation_relation", "foundation_candidate", "foundation_release")
        for table in tables:
            # Independent verifier contract: no post-seal writes of any kind.
            # This is intentionally specified here, not imported from builder.
            for verb in ("insert", "update", "delete"):
                expected.execute(f"CREATE TRIGGER {table}_immutable_{verb} BEFORE {verb.upper()} ON {table} BEGIN SELECT RAISE(ABORT,'immutable physical evidence'); END")
        query = "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        _eq(_rows(conn, query), _rows(expected, query), "frozen schema objects/definitions")
        for table in tables:
            # The SQL definition comparison covers CHECK and UNIQUE clauses;
            # these PRAGMAs separately assert columns, PK, FK and index wiring.
            for pragma in ("table_xinfo", "foreign_key_list", "index_list"):
                statement = f"PRAGMA {pragma}('{table}')"
                _eq(_rows(conn, statement), _rows(expected, statement), pragma + " " + table)
            for index in [row[1] for row in expected.execute(f"PRAGMA index_list('{table}')")]:
                statement = f"PRAGMA index_xinfo('{index}')"
                _eq(_rows(conn, statement), _rows(expected, statement), "index columns " + index)
        return tables
    finally:
        expected.close()


def verify(source_root: str | Path, scope_path: str | Path, artifact_path: str | Path, *, expected_artifact: dict | None = None) -> dict:
    source, scope_file, artifact = Path(source_root), Path(scope_path), Path(artifact_path)
    scope = json.loads(scope_file.read_text())
    _source_check(source, scope)
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(artifact) + suffix).exists():
            raise ValueError("SQLite sidecar exists: " + suffix)
    before = artifact.stat()
    seal = {"size_bytes": before.st_size, "sha256": sha(artifact)}
    if expected_artifact is not None:
        _eq(seal, expected_artifact, "artifact identity")
    implementation = {name: sha(ROOT / name) for name in IMPL}
    parser_id = _id("parser", implementation)
    run_id = _id("run", parser_id, sha(scope_file))
    conn = open_immutable_readonly(artifact)
    try:
        _eq(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok", "SQLite integrity")
        _eq(conn.execute("PRAGMA foreign_key_check").fetchall(), [], "SQLite foreign keys")
        required_tables = _verify_schema(conn)
        source_rows = _rows(conn, "SELECT path,snapshot_id,size_bytes,sha256,git_blob,physical_delta FROM foundation_source_file ORDER BY path")
        expected_source = sorted((e["path"], SNAPSHOT, e["size_bytes"], e["sha256"], e["blob"], e["physical_delta"]) for e in scope["files"])
        _eq(source_rows, expected_source, "source rows")
        index = defaultdict(list)
        primary_ids = {}
        counts = Counter()
        relation_states = Counter()
        candidate_rows = _candidate_rows_by_relation(conn)
        for basename, native_field in sorted(PRIMARY.items()):
            relative = "ExcelBinOutput/" + basename
            scanned = 0
            for ordinal, row, raw in array_records(source / relative):
                if type(row) is not dict or type(row.get(native_field)) is not int:
                    raise ValueError("source primary native shape")
                row_id, pointer = _id(run_id, "primary", relative, ordinal), f"/{ordinal}"
                native = str(row[native_field])
                _eq(conn.execute("SELECT * FROM foundation_primary_row WHERE source_path=? AND ordinal=?", (relative, ordinal)).fetchone(), (row_id, SNAPSHOT, parser_id, run_id, relative, ordinal, pointer, native_field, native, raw, _digest(raw)), "primary " + relative + pointer)
                primary_ids[(relative, ordinal)] = row_id
                index[(relative, native_field, native)].append((row_id, relative, pointer))
                expected_fields = []
                for field_ordinal, (name, value) in enumerate(row.items()):
                    field_pointer = f"{pointer}/{name}"
                    if name in HASH_FIELDS.get(basename, ()):
                        role = "source_textmap_locator"
                    elif name in ("finishConds", "openConds") and name in ARRAY_FIELDS.get(basename, ()):
                        role = "source_condition_array"
                    elif basename == "FettersExcelConfigData.json" and name == "voiceFile":
                        role = "source_voice_slot_locator"
                    elif basename == "ProfilePictureExcelConfigData.json" and name in ("NLAPNBMECDF", "unlockParam", "priority"):
                        role = "source_profile_unlock_observation"
                    elif name == "tips":
                        role = "opaque_source_tips_array"
                    elif name == "multiLangDefaultNameList":
                        role = "source_multilingual_name_array"
                    elif name in (native_field, "avatarId", "fetterId", "skinId", "characterId", "id", "renameID", "relatedAvatarID"):
                        # Only field values and coordinates, not this display role, carry evidence identity.
                        role = "source_scalar_locator" if name in scope["consumed_integer_fields"].get(basename, ()) else "uninterpreted_raw_field"
                    else:
                        role = "uninterpreted_raw_field"
                    expected_fields.append((_id(row_id, "field", field_pointer), row_id, field_pointer, field_ordinal, None, name, role, _json(value)))
                    counts["top_level_fields"] += 1
                    if name in ARRAY_FIELDS.get(basename, ()) and type(value) is list:
                        for child_ordinal, child in enumerate(value):
                            child_pointer = f"{field_pointer}/{child_ordinal}"
                            expected_fields.append((_id(row_id, "field", child_pointer), row_id, child_pointer, field_ordinal, child_ordinal, name, "physical_array_child", _json(child)))
                            counts["array_child_fields"] += 1
                _eq(sorted(_rows(conn, "SELECT * FROM foundation_field WHERE row_id=?", (row_id,))), sorted(expected_fields), "fields " + relative + pointer)
                expected_owners = sorted((_id(row_id, "text-owner", f"{pointer}/{name}"), row_id, f"{pointer}/{name}", name, str(row[name])) for name in HASH_FIELDS.get(basename, ()))
                _eq(sorted(_rows(conn, "SELECT * FROM foundation_text_owner WHERE row_id=?", (row_id,))), expected_owners, "text owners " + relative + pointer)
                scanned += 1
                counts["primary_rows"] += 1
                counts["fields"] += len(expected_fields)
                counts["text_owners"] += len(expected_owners)
            _eq(scanned, scope["primary_rows"][relative], "primary denominator " + relative)
        lut_source = json.loads((source / "BinOutput/Voice/Lut/Lut.json").read_text())
        lut_slots = defaultdict(list)
        item_slots = defaultdict(list)
        for ordinal, (key, row) in enumerate(lut_source.items()):
            if row.get("NLGBEEIGDJP") != "Fetter":
                continue
            if type(row.get("BMPENHIILJG")) is not int or type(row.get("PFMLOBHKGJO")) is not int:
                raise ValueError("7.1 Voice LUT shape")
            path = "BinOutput/Voice/Lut/Lut.json"
            slot, item_path, pointer = str(row["BMPENHIILJG"]), f"BinOutput/Voice/Items/{row['PFMLOBHKGJO']}.json", "/" + key
            record_id, raw = _id(run_id, "voice-lut", path, key), _json(row)
            _eq(conn.execute("SELECT * FROM foundation_voice_lut WHERE source_path=? AND object_key=?", (path, key)).fetchone(), (record_id, path, key, ordinal, pointer, slot, item_path, raw, _digest(raw)), "voice LUT " + key)
            lut_slots[slot].append((record_id, path, pointer, item_path))
            counts["voice_lut"] += 1
        _eq({item_path for records in lut_slots.values() for _, _, _, item_path in records}, set(scope["voice_scope"]["selected_fetter_item_files"]), "all Fetter Voice Items paths")
        for path in scope["voice_scope"]["selected_fetter_item_files"]:
            content = json.loads((source / path).read_text())
            if type(content) is not dict:
                raise ValueError("7.1 Voice Items root")
            for ordinal, (key, row) in enumerate(content.items()):
                if row.get("NLGBEEIGDJP") != "Fetter" or row.get("OKAHIJCNNFM") != key or type(row.get("BMPENHIILJG")) is not int:
                    raise ValueError("7.1 Voice Item shape")
                slot, pointer, record_id = str(row["BMPENHIILJG"]), "/" + key, _id(run_id, "voice-item", path, key)
                variants = row.get("GLDBLKKLJHP", [])
                status = "present" if "GLDBLKKLJHP" in row else "missing"
                if type(variants) is not list:
                    raise ValueError("Voice variants shape")
                raw = _json(row)
                _eq(conn.execute("SELECT * FROM foundation_voice_item WHERE source_path=? AND object_key=?", (path, key)).fetchone(), (record_id, path, key, ordinal, pointer, slot, status, raw, _digest(raw)), "voice Item " + key)
                item_slots[(slot, path)].append((record_id, path, pointer))
                counts["voice_item"] += 1
                counts["missing_variants"] += status == "missing"
                for audio_ordinal, audio in enumerate(variants):
                    audio_pointer = f"/{key}/GLDBLKKLJHP/{audio_ordinal}"
                    audio_raw = _json(audio)
                    expected = (_id(record_id, "audio", audio_ordinal), record_id, audio_ordinal, audio_pointer, audio["CFIKOCLOKGH"], audio["CCCECEDMIEI"], float(audio["EEJEGIEMJBL"]), audio_raw, _digest(audio_raw))
                    _eq(conn.execute("SELECT * FROM foundation_audio_variant WHERE item_id=? AND ordinal=?", (record_id, audio_ordinal)).fetchone(), expected, "audio variant " + audio_pointer)
                    counts["audio"] += 1
        for slot, records in lut_slots.items():
            for lut_id, _, _, path in records:
                matches = item_slots[(slot, path)]
                _eq(len(matches), 1, "Fetter Voice physical link uniqueness")
                item_id = matches[0][0]
                _eq(conn.execute("SELECT * FROM foundation_voice_link WHERE lut_id=?", (lut_id,)).fetchone(), (_id(run_id, "voice-link", lut_id, item_id), lut_id, item_id, "resolved_unique_physical_candidate"), "Voice link")
                counts["voice_link"] += 1
        # Reconstruct all raw locator candidate sets from source rows. A candidate
        # is a physical occurrence, never a logical Character/Voice identity.
        for basename in sorted(PRIMARY):
            relative = "ExcelBinOutput/" + basename
            for ordinal, row, _ in array_records(source / relative):
                row_id = primary_ids[(relative, ordinal)]
                specs = []
                if basename in ("FetterInfoExcelConfigData.json", "FetterStoryExcelConfigData.json", "FettersExcelConfigData.json"):
                    specs.append(("avatarId", "source_avatar_id_candidate", row["avatarId"], "all 7.1 physical AvatarExcel rows with exact id", [("primary", *x) for x in index[("ExcelBinOutput/AvatarExcelConfigData.json", "id", str(row["avatarId"]))]], False, False))
                if basename == "FettersExcelConfigData.json":
                    specs.append(("voiceFile", "fetter_voice_slot_candidate", row["voiceFile"], "all 7.1 Fetter-category Voice LUT records with exact slot", [("lut", x[0], x[1], x[2]) for x in lut_slots[row["voiceFile"]]], False, False))
                if basename in ("AvatarCostumeExcelConfigData.json", "AvatarRenameExcelConfigData.json"):
                    field, kind = ("characterId", "costume_avatar_id_candidate") if basename.startswith("AvatarCostume") else ("relatedAvatarID", "rename_avatar_id_candidate")
                    specs.append((field, kind, row[field], "all 7.1 physical AvatarExcel rows with exact id", [("primary", *x) for x in index[("ExcelBinOutput/AvatarExcelConfigData.json", "id", str(row[field]))]], False, False))
                if basename == "ProfilePictureExcelConfigData.json":
                    kind = row["NLAPNBMECDF"]
                    if kind == "PROFILE_PICTURE_UNLOCK_BY_AVATAR":
                        specs.append(("unlockParam", "profile_unlock_avatar_candidate", row.get("unlockParam"), "all 7.1 physical AvatarExcel rows with exact id", [("primary", *x) for x in index[("ExcelBinOutput/AvatarExcelConfigData.json", "id", str(row.get("unlockParam")))]], False, False))
                    elif kind == "PROFILE_PICTURE_UNLOCK_BY_COSTUME":
                        specs.append(("unlockParam", "profile_unlock_costume_candidate", row.get("unlockParam"), "all 7.1 physical AvatarCostume rows with exact skinId", [("primary", *x) for x in index[("ExcelBinOutput/AvatarCostumeExcelConfigData.json", "skinId", str(row.get("unlockParam")))]], False, False))
                    elif kind == "PROFILE_PICTURE_UNLOCK_BY_DEFAULT":
                        specs.append(("unlockParam", "profile_unlock_default_observation", row.get("unlockParam"), "no physical target required", [], False, True))
                    else:
                        specs.append(("unlockParam", "profile_unlock_external_locator", row.get("unlockParam"), "external parent Quest/Item boundary not selected", [], True, False))
                for field, kind, value, boundary, targets, terminal, not_applicable in specs:
                    pointer = f"/{ordinal}/{field}"
                    relation_id = _id(run_id, "relation", row_id, pointer, kind)
                    status = "terminal_unresolved" if terminal else "not_applicable" if not_applicable else "missing" if not targets else "resolved_unique" if len(targets) == 1 else "resolved_candidate_set"
                    relation_states[kind + ":" + status] += 1
                    expected = (relation_id, row_id, pointer, kind, _json(value), boundary, status, len(targets), 0 if terminal else 1)
                    _eq(conn.execute("SELECT * FROM foundation_relation WHERE source_row_id=? AND kind=?", (row_id, kind)).fetchone(), expected, "relation " + kind + pointer)
                    expected_candidates = sorted((_id(relation_id, kind_, target_id), relation_id, target_id if kind_ == "primary" else None, target_id if kind_ == "lut" else None, target_id if kind_ == "item" else None, path, target_pointer) for kind_, target_id, path, target_pointer in targets)
                    _eq(sorted(candidate_rows.get(relation_id, ())), expected_candidates, "relation candidates " + kind + pointer)
                    counts["relations"] += 1
                    counts["candidates"] += len(targets)
        actual_counts = {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in required_tables if table != "foundation_release"}
        expected_counts = {"foundation_source_file": 18, "foundation_primary_row": counts["primary_rows"], "foundation_field": counts["fields"], "foundation_text_owner": counts["text_owners"], "foundation_voice_lut": counts["voice_lut"], "foundation_voice_item": counts["voice_item"], "foundation_voice_link": counts["voice_link"], "foundation_audio_variant": counts["audio"], "foundation_relation": counts["relations"], "foundation_candidate": counts["candidates"]}
        _eq(actual_counts, expected_counts, "no extras/no omissions")
        _eq(counts["voice_lut"], scope["voice_scope"]["selected_fetter_lut_records"], "Voice LUT denominator")
        _eq(counts["voice_item"], scope["voice_scope"]["selected_fetter_item_records"], "Voice Item denominator")
        _eq(counts["audio"], scope["voice_scope"]["audio_variants"], "audio denominator")
        _eq(counts["missing_variants"], scope["voice_scope"]["items_with_missing_variant_array"], "missing variants")
        release_rows = _rows(conn, "SELECT id,manifest_json FROM foundation_release")
        _eq(len(release_rows), 1, "release count")
        release = json.loads(release_rows[0][1])
        _eq(release_rows[0][0], 1, "release ID")
        expected_observations = {
            "primary_rows": counts["primary_rows"],
            "top_level_fields": counts["top_level_fields"],
            "array_child_fields": counts["array_child_fields"],
            "text_owner_locators": counts["text_owners"],
            "voice_lut_records": counts["voice_lut"],
            "voice_item_records": counts["voice_item"],
            "voice_items_missing_variant_array": counts["missing_variants"],
            "voice_links": counts["voice_link"],
            "audio_variants": counts["audio"],
            "relations": counts["relations"],
            "candidates": counts["candidates"],
        }
        expected_release = {
            "contract": "character-7.1-physical-foundation-v1",
            "snapshot_id": SNAPSHOT,
            "source_commit": COMMIT,
            "source_tree": TREE,
            "source_scope_sha256": sha(scope_file),
            "parser_build_id": parser_id,
            "ingest_run_id": run_id,
            "implementation": implementation,
            "table_counts": actual_counts,
            "observation_counts": expected_observations,
            "relation_states": dict(relation_states),
            "source_file_count": len(scope["files"]),
            "product_accepted": False,
            "logical_character_created": False,
        }
        _eq(release, expected_release, "complete release contract")
    finally:
        conn.close()
    _source_check(source, scope)
    after = artifact.stat()
    _eq((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, sha(artifact)), (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, seal["sha256"]), "artifact stability")
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(artifact) + suffix).exists():
            raise ValueError("SQLite sidecar after verify: " + suffix)
    return {"status": "PASS", "contract": "character-7.1-physical-foundation-independent-verifier-v1", "artifact": seal, "source_scope_sha256": sha(scope_file), "parser_build_id": parser_id, "ingest_run_id": run_id, "table_counts": actual_counts, "observed_counts": dict(sorted(counts.items())), "silent_skips": 0, "wrong_cross_snapshot_identity_merges": 0}
