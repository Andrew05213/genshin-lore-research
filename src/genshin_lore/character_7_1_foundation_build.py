"""Build snapshot-local Character/Fetter physical observations, not logical identities."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from .character_7_1_physical import CONDITION_FIELDS, REQUIRED_INTEGER_FIELDS, TEXT_HASH_FIELDS, decode_primary_row
from .character_7_1_voice import LUT_PATH, decode_fetter_item, decode_fetter_lut
from .loading_tips_source import array_records, sha


ROOT = Path(__file__).resolve().parents[2]
COMMIT = "9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
TREE = "aa578c08c402af39b41f1ffc95124d10f5410272"
SNAPSHOT = "anime-game-data:7.1:" + COMMIT
IMPLEMENTATION = (
    "src/genshin_lore/character_7_1_foundation_build.py",
    "src/genshin_lore/character_7_1_physical.py",
    "src/genshin_lore/character_7_1_voice.py",
    "src/genshin_lore/loading_tips_source.py",
    "schemas/character-7.1-physical-foundation-v1.sql",
)
NATIVE = {
    "AvatarExcelConfigData.json": "id",
    "FetterInfoExcelConfigData.json": "fetterId",
    "FetterStoryExcelConfigData.json": "fetterId",
    "FettersExcelConfigData.json": "fetterId",
    "AvatarCostumeExcelConfigData.json": "skinId",
    "ProfilePictureExcelConfigData.json": "id",
    "AvatarRenameExcelConfigData.json": "renameID",
}


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def ident(*parts) -> str:
    return "character71:" + digest(canonical(parts))[:40]


def check_source(source: Path, scope: dict) -> None:
    if scope.get("contract") != "character-7.1-physical-foundation-scope-v1" or scope.get("snapshot_id") != SNAPSHOT or scope.get("source_commit") != COMMIT or scope.get("source_tree") != TREE:
        raise ValueError("Character 7.1 source scope identity")
    actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD", "HEAD^{tree}"], text=True).splitlines()
    dirty = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain=v1"], text=True).strip()
    if actual != [COMMIT, TREE] or dirty:
        raise ValueError("Character 7.1 source checkout drift")
    expected = {"ExcelBinOutput/" + basename for basename in REQUIRED_INTEGER_FIELDS}
    expected |= {LUT_PATH}
    expected |= {f"TextMap/TextMap{tier}{locale}.json" for locale in ("CHS", "EN", "JP") for tier in ("", "_Medium")}
    expected |= set(scope["voice_scope"]["selected_fetter_item_files"])
    if {entry["path"] for entry in scope["files"]} != expected or len(expected) != 18:
        raise ValueError("Character 7.1 source denominator")
    for entry in scope["files"]:
        path = source / entry["path"]
        if path.stat().st_size != entry["size_bytes"] or sha(path) != entry["sha256"]:
            raise ValueError("Character 7.1 source bytes drift: " + entry["path"])
        pinned = subprocess.check_output(["git", "-C", str(source), "rev-parse", f"{COMMIT}:{entry['path']}"], text=True).strip()
        if pinned != entry["blob"]:
            raise ValueError("Character 7.1 source Git blob drift: " + entry["path"])


def _field_role(basename: str, name: str) -> str:
    if name in TEXT_HASH_FIELDS.get(basename, ()):
        return "source_textmap_locator"
    if name in CONDITION_FIELDS.get(basename, ()):
        return "source_condition_array"
    if name == "voiceFile":
        return "source_voice_slot_locator"
    if name in ("NLAPNBMECDF", "unlockParam", "priority") and basename == "ProfilePictureExcelConfigData.json":
        return "source_profile_unlock_observation"
    if name == "tips":
        return "opaque_source_tips_array"
    if name == "multiLangDefaultNameList":
        return "source_multilingual_name_array"
    if name in REQUIRED_INTEGER_FIELDS[basename]:
        return "source_scalar_locator"
    return "uninterpreted_raw_field"


def _guard_tables(conn: sqlite3.Connection) -> None:
    tables = ("foundation_source_file", "foundation_primary_row", "foundation_field", "foundation_text_owner", "foundation_voice_lut", "foundation_voice_item", "foundation_voice_link", "foundation_audio_variant", "foundation_relation", "foundation_candidate", "foundation_release")
    for table in tables:
        # The artifact is sealed only after all valid build-time INSERTs complete.
        # Reject *every* later INSERT before SQLite handles an OR REPLACE conflict;
        # a primary-key-only guard misses independent UNIQUE(row_id,pointer) etc.
        conn.execute(f"CREATE TRIGGER {table}_immutable_insert BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT,'immutable physical evidence'); END")
        conn.execute(f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'immutable physical evidence'); END")
        conn.execute(f"CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'immutable physical evidence'); END")


def build(source_root: str | Path, scope_path: str | Path, output_path: str | Path) -> dict:
    source, scope_file, output = Path(source_root), Path(scope_path), Path(output_path)
    scope = json.loads(scope_file.read_text())
    check_source(source, scope)
    implementation = {relative: sha(ROOT / relative) for relative in IMPLEMENTATION}
    parser_id = ident("parser", implementation)
    run_id = ident("run", parser_id, sha(scope_file))
    if any(Path(str(output) + suffix).exists() for suffix in ("", "-wal", "-shm", "-journal")):
        raise FileExistsError("Character foundation output or sidecar exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb"):
        pass
    owned = (output.stat().st_dev, output.stat().st_ino)
    conn = None
    try:
        conn = sqlite3.connect(output)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript((ROOT / "schemas/character-7.1-physical-foundation-v1.sql").read_text())
        for entry in scope["files"]:
            conn.execute("INSERT INTO foundation_source_file VALUES(?,?,?,?,?,?)", (entry["path"], SNAPSHOT, entry["size_bytes"], entry["sha256"], entry["blob"], entry["physical_delta"]))
        rows_by_source = defaultdict(list)
        index = defaultdict(list)
        counts = Counter()
        for basename in sorted(REQUIRED_INTEGER_FIELDS):
            relative = "ExcelBinOutput/" + basename
            for ordinal, row, raw in array_records(source / relative):
                decoded = decode_primary_row(relative, ordinal, row, raw)
                native_field = NATIVE[basename]
                native = str(row[native_field])
                row_id = ident(run_id, "primary", relative, ordinal)
                pointer = f"/{ordinal}"
                conn.execute("INSERT INTO foundation_primary_row VALUES(?,?,?,?,?,?,?,?,?,?,?)", (row_id, SNAPSHOT, parser_id, run_id, relative, ordinal, pointer, native_field, native, raw, digest(raw)))
                rows_by_source[relative].append((row_id, ordinal, row))
                index[(relative, native_field, native)].append((row_id, relative, pointer))
                counts["primary_rows"] += 1
                for field_ordinal, (name, value) in enumerate(row.items()):
                    field_pointer = f"/{ordinal}/{name}"
                    conn.execute("INSERT INTO foundation_field VALUES(?,?,?,?,?,?,?,?)", (ident(row_id, "field", field_pointer), row_id, field_pointer, field_ordinal, None, name, _field_role(basename, name), canonical(value)))
                    counts["top_level_fields"] += 1
                    if name in (*CONDITION_FIELDS.get(basename, ()), "tips", "multiLangDefaultNameList") and isinstance(value, list):
                        for child_ordinal, child in enumerate(value):
                            child_pointer = f"{field_pointer}/{child_ordinal}"
                            conn.execute("INSERT INTO foundation_field VALUES(?,?,?,?,?,?,?,?)", (ident(row_id, "field", child_pointer), row_id, child_pointer, field_ordinal, child_ordinal, name, "physical_array_child", canonical(child)))
                            counts["array_child_fields"] += 1
                for name in TEXT_HASH_FIELDS.get(basename, ()):
                    owner_pointer = f"/{ordinal}/{name}"
                    conn.execute("INSERT INTO foundation_text_owner VALUES(?,?,?,?,?)", (ident(row_id, "text-owner", owner_pointer), row_id, owner_pointer, name, str(row[name])))
                    counts["text_owner_locators"] += 1
        for relative, expected in scope["primary_rows"].items():
            if len(rows_by_source[relative]) != expected:
                raise ValueError("Character primary source denominator drift")
        lut_raw = json.loads((source / LUT_PATH).read_text())
        lut_by_slot = defaultdict(list)
        for object_ordinal, (key, row) in enumerate(lut_raw.items()):
            decoded = decode_fetter_lut(key, row)
            if decoded is None:
                continue
            lut_id = ident(run_id, "voice-lut", LUT_PATH, key)
            raw = canonical(row)
            conn.execute("INSERT INTO foundation_voice_lut VALUES(?,?,?,?,?,?,?,?,?)", (lut_id, LUT_PATH, key, object_ordinal, decoded.pointer, decoded.slot, decoded.item_path, raw, digest(raw)))
            lut_by_slot[decoded.slot].append((lut_id, LUT_PATH, decoded.pointer, decoded.item_path))
            counts["voice_lut_records"] += 1
        item_by_slot_path = defaultdict(list)
        for relative in scope["voice_scope"]["selected_fetter_item_files"]:
            content = json.loads((source / relative).read_text())
            if type(content) is not dict:
                raise ValueError("Voice Items root shape")
            for object_ordinal, (key, row) in enumerate(content.items()):
                item, variants = decode_fetter_item(relative, key, row)
                item_id = ident(run_id, "voice-item", relative, key)
                raw = canonical(row)
                conn.execute("INSERT INTO foundation_voice_item VALUES(?,?,?,?,?,?,?,?,?)", (item_id, relative, key, object_ordinal, item.pointer, item.slot, item.variants_status, raw, digest(raw)))
                item_by_slot_path[(item.slot, relative)].append((item_id, relative, item.pointer))
                counts["voice_item_records"] += 1
                counts["voice_items_missing_variant_array"] += item.variants_status == "missing"
                for variant in variants:
                    source_variant = row["GLDBLKKLJHP"][variant.ordinal]
                    variant_raw = canonical(source_variant)
                    conn.execute("INSERT INTO foundation_audio_variant VALUES(?,?,?,?,?,?,?,?,?)", (ident(item_id, "audio", variant.ordinal), item_id, variant.ordinal, variant.pointer, variant.audio_path, variant.selector, variant.order_value, variant_raw, digest(variant_raw)))
                    counts["audio_variants"] += 1
        for slot, lut_candidates in sorted(lut_by_slot.items()):
            for lut_id, _, _, item_path in lut_candidates:
                targets = item_by_slot_path[(slot, item_path)]
                if len(targets) != 1:
                    raise ValueError("Fetter LUT to Items candidate set is not unique within frozen scope")
                conn.execute("INSERT INTO foundation_voice_link VALUES(?,?,?,?)", (ident(run_id, "voice-link", lut_id, targets[0][0]), lut_id, targets[0][0], "resolved_unique_physical_candidate"))
                counts["voice_links"] += 1
        relation_states = Counter()

        def add_relation(row_id, pointer, kind, value, boundary, targets, *, terminal=False, not_applicable=False):
            if terminal:
                status, complete = "terminal_unresolved", 0
            elif not_applicable:
                status, complete = "not_applicable", 1
            else:
                status, complete = ("missing" if not targets else "resolved_unique" if len(targets) == 1 else "resolved_candidate_set"), 1
            relation_id = ident(run_id, "relation", row_id, pointer, kind)
            conn.execute("INSERT INTO foundation_relation VALUES(?,?,?,?,?,?,?,?,?)", (relation_id, row_id, pointer, kind, canonical(value), boundary, status, len(targets), complete))
            relation_states[kind + ":" + status] += 1
            counts["relations"] += 1
            for target_type, target_id, target_path, target_pointer in targets:
                conn.execute("INSERT INTO foundation_candidate VALUES(?,?,?,?,?,?,?)", (ident(relation_id, target_type, target_id), relation_id, target_id if target_type == "primary" else None, target_id if target_type == "lut" else None, target_id if target_type == "item" else None, target_path, target_pointer))
                counts["candidates"] += 1

        avatar = "ExcelBinOutput/AvatarExcelConfigData.json"
        costume = "ExcelBinOutput/AvatarCostumeExcelConfigData.json"
        for relative, rows in rows_by_source.items():
            basename = relative.rsplit("/", 1)[-1]
            for row_id, ordinal, row in rows:
                if basename in ("FetterInfoExcelConfigData.json", "FetterStoryExcelConfigData.json", "FettersExcelConfigData.json"):
                    value = row["avatarId"]
                    targets = [("primary", *target) for target in index[(avatar, "id", str(value))]]
                    add_relation(row_id, f"/{ordinal}/avatarId", "source_avatar_id_candidate", value, "all 7.1 physical AvatarExcel rows with exact id", targets)
                if basename == "FettersExcelConfigData.json":
                    value = row["voiceFile"]
                    targets = [("lut", ident_, path, ptr) for ident_, path, ptr, _ in lut_by_slot[value]]
                    add_relation(row_id, f"/{ordinal}/voiceFile", "fetter_voice_slot_candidate", value, "all 7.1 Fetter-category Voice LUT records with exact slot", targets)
                elif basename == "AvatarCostumeExcelConfigData.json":
                    value = row["characterId"]
                    targets = [("primary", *target) for target in index[(avatar, "id", str(value))]]
                    add_relation(row_id, f"/{ordinal}/characterId", "costume_avatar_id_candidate", value, "all 7.1 physical AvatarExcel rows with exact id", targets)
                elif basename == "AvatarRenameExcelConfigData.json":
                    value = row["relatedAvatarID"]
                    targets = [("primary", *target) for target in index[(avatar, "id", str(value))]]
                    add_relation(row_id, f"/{ordinal}/relatedAvatarID", "rename_avatar_id_candidate", value, "all 7.1 physical AvatarExcel rows with exact id", targets)
                elif basename == "ProfilePictureExcelConfigData.json":
                    kind, value = row["NLAPNBMECDF"], row.get("unlockParam")
                    if kind == "PROFILE_PICTURE_UNLOCK_BY_AVATAR":
                        targets = [("primary", *target) for target in index[(avatar, "id", str(value))]]
                        add_relation(row_id, f"/{ordinal}/unlockParam", "profile_unlock_avatar_candidate", value, "all 7.1 physical AvatarExcel rows with exact id", targets)
                    elif kind == "PROFILE_PICTURE_UNLOCK_BY_COSTUME":
                        targets = [("primary", *target) for target in index[(costume, "skinId", str(value))]]
                        add_relation(row_id, f"/{ordinal}/unlockParam", "profile_unlock_costume_candidate", value, "all 7.1 physical AvatarCostume rows with exact skinId", targets)
                    elif kind == "PROFILE_PICTURE_UNLOCK_BY_DEFAULT":
                        add_relation(row_id, f"/{ordinal}/unlockParam", "profile_unlock_default_observation", value, "no physical target required", [], not_applicable=True)
                    else:
                        add_relation(row_id, f"/{ordinal}/unlockParam", "profile_unlock_external_locator", value, "external parent Quest/Item boundary not selected", [], terminal=True)
        expected_voice = scope["voice_scope"]
        if counts["voice_lut_records"] != expected_voice["selected_fetter_lut_records"] or counts["voice_item_records"] != expected_voice["selected_fetter_item_records"] or counts["audio_variants"] != expected_voice["audio_variants"] or counts["voice_links"] != expected_voice["selected_fetter_lut_records"]:
            raise ValueError("full Fetter Voice denominator mismatch")
        tables = ("foundation_source_file", "foundation_primary_row", "foundation_field", "foundation_text_owner", "foundation_voice_lut", "foundation_voice_item", "foundation_voice_link", "foundation_audio_variant", "foundation_relation", "foundation_candidate")
        table_counts = {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables}
        release = {"contract": "character-7.1-physical-foundation-v1", "snapshot_id": SNAPSHOT, "source_commit": COMMIT, "source_tree": TREE, "source_scope_sha256": sha(scope_file), "parser_build_id": parser_id, "ingest_run_id": run_id, "implementation": implementation, "table_counts": table_counts, "observation_counts": dict(sorted(counts.items())), "relation_states": dict(sorted(relation_states.items())), "source_file_count": len(scope["files"]), "product_accepted": False, "logical_character_created": False}
        conn.execute("INSERT INTO foundation_release VALUES(1,?)", (canonical(release),))
        _guard_tables(conn)
        conn.commit()
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Character foundation FK failure")
        conn.execute("VACUUM")
        conn.close()
        conn = None
        check_source(source, scope)
        if any(Path(str(output) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise ValueError("Character foundation SQLite sidecar")
        return {**release, "artifact": {"size_bytes": output.stat().st_size, "sha256": sha(output)}}
    except Exception:
        if conn is not None:
            conn.close()
        try:
            stat = output.stat()
            if (stat.st_dev, stat.st_ino) == owned:
                output.unlink()
        except FileNotFoundError:
            pass
        raise
