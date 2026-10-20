"""Build bounded 7.1 Character successors from the accepted immutable foundation.

Only source-authored TextMap text and frozen physical candidates are projected.
No logical Character, general Voice tree, or cross-snapshot identity is created.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from .loading_tips_source import pairs, sha


ROOT = Path(__file__).resolve().parents[2]
FOUNDATION_MANIFEST = ROOT / "manifests/character-7.1-physical-foundation/immutable-runtime.json"
SNAPSHOT = "anime-game-data:7.1:9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
SOURCE_COMMIT = "9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
SOURCE_TREE = "aa578c08c402af39b41f1ffc95124d10f5410272"
FOUNDATION_RUN = "character71:f13a6261e9ed01bf6a765e6d8b22167bfd71c8b1"
FOUNDATION_PARSER = "character71:4d23d9f656546874bfec8915f9fde9bee9a73243"
FOUNDATION_SIZE = 108290048
FOUNDATION_SHA = "20e065491d3696555fbeaae104c95284a9f7e9ae439861613a984829b6a1b8ef"
LORE = {
    "AvatarExcelConfigData.json": "avatar",
    "FetterInfoExcelConfigData.json": "fetter_info",
    "FetterStoryExcelConfigData.json": "story",
}
VOICE = "ExcelBinOutput/FettersExcelConfigData.json"
LOCALES = ("CHS", "EN", "JP")
SCHEMAS = {
    "text": "schemas/character-7.1-text-v1.sql",
    "lore": "schemas/character-7.1-lore-v1.sql",
    "voice": "schemas/character-7.1-voice-v1.sql",
}
TABLES = {
    "text": ("resolution", "candidate", "release"),
    "lore": ("lore_record", "lore_text_ref", "lore_raw_field_ref", "lore_avatar_candidate", "release"),
    "voice": ("voice_record", "voice_text_ref", "voice_raw_field_ref", "voice_avatar_candidate", "voice_lut_candidate", "lut_ref", "item_ref", "audio_ref", "release"),
}


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _identity(kind: str, *parts: object) -> str:
    return "character71-" + kind + ":" + hashlib.sha256(canonical(parts).encode()).hexdigest()[:40]


def _seal(conn: sqlite3.Connection, kind: str) -> None:
    for table in TABLES[kind]:
        for verb in ("INSERT", "UPDATE", "DELETE"):
            conn.execute(f"CREATE TRIGGER {table}_immutable_{verb.lower()} BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT,'immutable physical evidence'); END")


def _readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def qualify_foundation(path: str | Path, *, foundation_manifest=None) -> tuple[sqlite3.Connection, dict]:
    """No default/latest or historical-runtime fallback is allowed."""
    path = Path(path)
    manifest = json.loads(Path(foundation_manifest or FOUNDATION_MANIFEST).read_text())
    if (manifest["source_snapshot"], manifest["source_commit"], manifest["source_tree"]) != (SNAPSHOT, SOURCE_COMMIT, SOURCE_TREE):
        raise ValueError("immutable foundation manifest identity")
    if "local_rebuild" in manifest:
        from .character_7_1_foundation_binding import qualify_local_foundation
        qualify_local_foundation(path, foundation_manifest, manifest)
    else:
        if (manifest["parser_build_id"], manifest["ingest_run_id"]) != (FOUNDATION_PARSER, FOUNDATION_RUN):
            raise ValueError("immutable foundation manifest identity")
        if manifest["artifact"]["size_bytes"] != FOUNDATION_SIZE or manifest["artifact"]["sha256"] != FOUNDATION_SHA or path.stat().st_size != FOUNDATION_SIZE or sha(path) != FOUNDATION_SHA:
            raise ValueError("immutable foundation artifact identity")
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("immutable foundation sidecar")
    scope = json.loads((ROOT / manifest["source_scope"]).read_text())
    if scope["snapshot_id"] != SNAPSHOT or len(scope["files"]) != 18:
        raise ValueError("immutable foundation source scope")
    conn = _readonly(path)
    try:
        if conn.execute("SELECT count(*) FROM foundation_text_owner").fetchone()[0] != 34817:
            raise ValueError("foundation text-owner denominator")
        for table, expected in (("foundation_voice_lut", 375), ("foundation_voice_item", 375), ("foundation_audio_variant", 9770), ("foundation_source_file", 18)):
            if conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] != expected:
                raise ValueError("foundation denominator " + table)
        release = json.loads(conn.execute("SELECT manifest_json FROM foundation_release WHERE id=1").fetchone()[0])
        if (release["snapshot_id"], release["parser_build_id"], release["ingest_run_id"]) != (SNAPSHOT, manifest["parser_build_id"], manifest["ingest_run_id"]):
            raise ValueError("foundation release identity")
        if conn.execute("SELECT count(*) FROM foundation_primary_row WHERE snapshot_id!=? OR parser_build_id!=? OR ingest_run_id!=?", (SNAPSHOT, manifest["parser_build_id"], manifest["ingest_run_id"])).fetchone()[0]:
            raise ValueError("foundation row identity")
        return conn, scope
    except Exception:
        conn.close()
        raise


def qualify_source(source: str | Path, scope: dict) -> Path:
    source = Path(source)
    actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD", "HEAD^{tree}"], text=True).splitlines()
    dirty = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain=v1"], text=True).strip()
    if actual != [SOURCE_COMMIT, SOURCE_TREE] or dirty:
        raise ValueError("7.1 source checkout identity")
    textmap = [f"TextMap/TextMap{tier}{locale}.json" for locale in LOCALES for tier in ("", "_Medium")]
    by_path = {entry["path"]: entry for entry in scope["files"]}
    if len(by_path) != 18 or not all(path in by_path for path in textmap):
        raise ValueError("six sealed TextMap source files required")
    for path in textmap:
        entry = by_path[path]
        if (source / path).stat().st_size != entry["size_bytes"] or sha(source / path) != entry["sha256"]:
            raise ValueError("7.1 TextMap source drift: " + path)
    return source


def _output(kind: str, path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    if any(Path(str(path) + suffix).exists() for suffix in ("", "-wal", "-shm", "-journal")):
        raise FileExistsError("successor output or sidecar exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb"):
        pass
    owned = (path.stat().st_dev, path.stat().st_ino)
    conn = None
    try:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript((ROOT / SCHEMAS[kind]).read_text())
        return conn
    except Exception:
        if conn is not None:
            conn.close()
        raise


def _foundation_run(foundation: sqlite3.Connection) -> str:
    return json.loads(foundation.execute("SELECT manifest_json FROM foundation_release WHERE id=1").fetchone()[0])["ingest_run_id"]


def _release(kind: str, conn: sqlite3.Connection, foundation: Path, *, text: Path | None = None, source_scope: dict | None = None, foundation_run_id: str = FOUNDATION_RUN) -> dict:
    implementation = {
        "src/genshin_lore/character_7_1_successor_build.py": sha(ROOT / "src/genshin_lore/character_7_1_successor_build.py"),
        SCHEMAS[kind]: sha(ROOT / SCHEMAS[kind]),
    }
    parser_id = _identity(kind, "parser", implementation)
    foundation_sha = sha(foundation)
    foundation_size = foundation.stat().st_size
    run_id = _identity(kind, "run", parser_id, foundation_run_id, foundation_sha, sha(text) if text else None)
    release = {
        "contract": f"character-7.1-{kind}-successor-v1",
        "snapshot_id": SNAPSHOT,
        "foundation_run_id": foundation_run_id,
        "foundation_size_bytes": foundation_size,
        "foundation_sha256": foundation_sha,
        "parser_build_id": parser_id,
        "ingest_run_id": run_id,
        "implementation": implementation,
        "table_counts": {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in TABLES[kind] if table != "release"},
        "product_accepted": False,
        "research_corpus_accepted": False,
        "logical_character_created": False,
    }
    if text:
        release["text_projection_sha256"] = sha(text)
    if source_scope:
        release["textmap_sources"] = {entry["path"]: {"size_bytes": entry["size_bytes"], "sha256": entry["sha256"], "blob": entry["blob"]} for entry in source_scope["files"] if entry["path"].startswith("TextMap/")}
    conn.execute("INSERT INTO release VALUES(1,?)", (canonical(release),))
    _seal(conn, kind)
    conn.commit()
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or conn.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("successor SQLite integrity")
    conn.execute("VACUUM")
    return release


def _finish(conn: sqlite3.Connection, path: Path, report: dict) -> dict:
    conn.close()
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("successor SQLite sidecar")
    return {**report, "artifact": {"size_bytes": path.stat().st_size, "sha256": sha(path)}}


def build_text(source_root: str | Path, foundation_path: str | Path, output_path: str | Path, *, foundation_manifest=None) -> dict:
    foundation, scope = qualify_foundation(foundation_path, foundation_manifest=foundation_manifest)
    output = Path(output_path)
    conn = None
    owned = None
    try:
        source = qualify_source(source_root, scope)
        owner_rows = foundation.execute("SELECT id,row_id,pointer,field_name,textmap_hash FROM foundation_text_owner ORDER BY id").fetchall()
        hashes = {row[4] for row in owner_rows}
        matches: dict[tuple[str, str], list[tuple[str, str, int, str]]] = defaultdict(list)
        for locale in LOCALES:
            for tier_suffix, tier in (("", "main"), ("_Medium", "medium")):
                path = f"TextMap/TextMap{tier_suffix}{locale}.json"
                for ordinal, (key, value) in enumerate(pairs(source / path)):
                    if key in hashes:
                        matches[(locale, key)].append((tier, path, ordinal, value))
        conn = _output("text", output)
        owned = (output.stat().st_dev, output.stat().st_ino)
        counts = Counter()
        for owner_id, row_id, pointer, field_name, key in owner_rows:
            for locale in LOCALES:
                found = matches[(locale, key)]
                state = "missing" if not found else "resolved_unique" if len(found) == 1 else "resolved_candidate_set"
                conn.execute("INSERT INTO resolution VALUES(?,?,?,?,?,?,?,?)", (owner_id, row_id, pointer, field_name, key, locale, state, len(found)))
                counts[locale + ":" + state] += 1
                conn.executemany("INSERT INTO candidate VALUES(?,?,?,?,?,?,?)", ((owner_id, locale, tier, path, ordinal, key, value) for tier, path, ordinal, value in found))
        if len(owner_rows) != 34817 or conn.execute("SELECT count(*) FROM resolution").fetchone()[0] != 34817 * 3:
            raise ValueError("text owner denominator")
        release = _release("text", conn, Path(foundation_path), source_scope=scope, foundation_run_id=_foundation_run(foundation))
        report = _finish(conn, output, {**release, "resolution_states": dict(sorted(counts.items())), "owner_count": len(owner_rows), "silent_skips": 0})
        conn = None
        qualify_source(source, scope)
        return report
    except Exception:
        if conn is not None:
            conn.close()
        raise
    finally:
        foundation.close()


def _qualify_text(path: Path, foundation_path: Path, *, foundation_run_id: str = FOUNDATION_RUN) -> sqlite3.Connection:
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("text projection sidecar")
    conn = _readonly(path)
    try:
        release = json.loads(conn.execute("SELECT manifest_json FROM release WHERE id=1").fetchone()[0])
        if (release["contract"] != "character-7.1-text-successor-v1"
                or release["snapshot_id"] != SNAPSHOT
                or release["foundation_sha256"] != sha(foundation_path)
                or release["foundation_size_bytes"] != foundation_path.stat().st_size
                or release["foundation_run_id"] != foundation_run_id
                or conn.execute("SELECT count(*) FROM resolution").fetchone()[0] != 34817 * 3):
            raise ValueError("text projection identity")
        return conn
    except Exception:
        conn.close()
        raise


def build_lore(foundation_path: str | Path, text_path: str | Path, output_path: str | Path, *, foundation_manifest=None) -> dict:
    foundation, _ = qualify_foundation(foundation_path, foundation_manifest=foundation_manifest)
    try:
        text = _qualify_text(Path(text_path), Path(foundation_path), foundation_run_id=_foundation_run(foundation))
    except Exception:
        foundation.close()
        raise
    output = Path(output_path)
    conn = None
    owned = None
    try:
        conn = _output("lore", output)
        owned = (output.stat().st_dev, output.stat().st_ino)
        counts = Counter()
        for basename, kind in LORE.items():
            path = "ExcelBinOutput/" + basename
            for row_id, ordinal, pointer in foundation.execute("SELECT id,ordinal,pointer FROM foundation_primary_row WHERE source_path=? ORDER BY ordinal", (path,)):
                rel = foundation.execute("SELECT id,status,candidate_count FROM foundation_relation WHERE source_row_id=? AND kind='source_avatar_id_candidate'", (row_id,)).fetchone() if kind != "avatar" else None
                status, candidate_count = (rel[1], rel[2]) if rel else ("not_applicable", 0)
                conn.execute("INSERT INTO lore_record VALUES(?,?,?,?,?,?,?)", (row_id, kind, path, ordinal, pointer, status, candidate_count))
                owners = foundation.execute("SELECT id,field_name,pointer FROM foundation_text_owner WHERE row_id=? ORDER BY pointer", (row_id,)).fetchall()
                for owner_id, field_name, owner_pointer in owners:
                    if text.execute("SELECT count(*) FROM resolution WHERE owner_id=?", (owner_id,)).fetchone()[0] != 3:
                        raise ValueError("lore owner absent from text projection")
                    conn.execute("INSERT INTO lore_text_ref VALUES(?,?,?,?)", (row_id, owner_id, field_name, owner_pointer))
                required_raw = () if kind == "avatar" else ("finishConds",) if kind == "fetter_info" else ("finishConds", "openConds")
                if required_raw:
                    fields = foundation.execute("SELECT id,pointer,field_name,role,source_field_ordinal,child_ordinal FROM foundation_field WHERE row_id=? AND field_name IN (" + ",".join("?" for _ in required_raw) + ") ORDER BY source_field_ordinal,child_ordinal", (row_id, *required_raw)).fetchall()
                    if {field[2] for field in fields if field[3] == "source_condition_array"} != set(required_raw):
                        raise ValueError("Lore/Story raw condition parent missing")
                    conn.executemany("INSERT INTO lore_raw_field_ref VALUES(?,?,?,?,?,?,?)", ((row_id, *field) for field in fields))
                if rel:
                    candidates = foundation.execute("SELECT target_primary_id FROM foundation_candidate WHERE relation_id=? ORDER BY id", (rel[0],)).fetchall()
                    if len(candidates) != candidate_count:
                        raise ValueError("lore ownership candidate count")
                    conn.executemany("INSERT INTO lore_avatar_candidate VALUES(?,?,?)", ((row_id, rel[0], target) for (target,) in candidates))
                counts[kind] += 1
        if counts != {"avatar": 167, "fetter_info": 124, "story": 974}:
            raise ValueError("Lore/Story full denominator")
        release = _release("lore", conn, Path(foundation_path), text=Path(text_path), foundation_run_id=_foundation_run(foundation))
        report = _finish(conn, output, {**release, "record_counts": dict(counts), "silent_skips": 0})
        conn = None
        return report
    except Exception:
        if conn is not None:
            conn.close()
        raise
    finally:
        foundation.close()
        text.close()


def build_voice(foundation_path: str | Path, text_path: str | Path, output_path: str | Path, *, foundation_manifest=None) -> dict:
    foundation, _ = qualify_foundation(foundation_path, foundation_manifest=foundation_manifest)
    try:
        text = _qualify_text(Path(text_path), Path(foundation_path), foundation_run_id=_foundation_run(foundation))
    except Exception:
        foundation.close()
        raise
    output = Path(output_path)
    conn = None
    owned = None
    try:
        conn = _output("voice", output)
        owned = (output.stat().st_dev, output.stat().st_ino)
        for row_id, path, ordinal, pointer in foundation.execute("SELECT id,source_path,ordinal,pointer FROM foundation_primary_row WHERE source_path=? ORDER BY ordinal", (VOICE,)):
            avatar_rel = foundation.execute("SELECT id,status,candidate_count FROM foundation_relation WHERE source_row_id=? AND kind='source_avatar_id_candidate'", (row_id,)).fetchone()
            lut_rel = foundation.execute("SELECT id,status,candidate_count FROM foundation_relation WHERE source_row_id=? AND kind='fetter_voice_slot_candidate'", (row_id,)).fetchone()
            if not avatar_rel or not lut_rel:
                raise ValueError("Voice required physical locator missing")
            conn.execute("INSERT INTO voice_record VALUES(?,?,?,?,?,?,?,?)", (row_id, path, ordinal, pointer, avatar_rel[1], avatar_rel[2], lut_rel[1], lut_rel[2]))
            for owner_id, field_name, owner_pointer in foundation.execute("SELECT id,field_name,pointer FROM foundation_text_owner WHERE row_id=? ORDER BY pointer", (row_id,)):
                if text.execute("SELECT count(*) FROM resolution WHERE owner_id=?", (owner_id,)).fetchone()[0] != 3:
                    raise ValueError("Voice owner absent from text projection")
                conn.execute("INSERT INTO voice_text_ref VALUES(?,?,?,?)", (row_id, owner_id, field_name, owner_pointer))
            fields = foundation.execute("SELECT id,pointer,field_name,role,source_field_ordinal,child_ordinal FROM foundation_field WHERE row_id=? AND field_name='openConds' ORDER BY source_field_ordinal,child_ordinal", (row_id,)).fetchall()
            if {field[2] for field in fields if field[3] == "source_condition_array"} != {"openConds"}:
                raise ValueError("Voice raw open condition parent missing")
            conn.executemany("INSERT INTO voice_raw_field_ref VALUES(?,?,?,?,?,?,?)", ((row_id, *field) for field in fields))
            for relation, table, target in ((avatar_rel, "voice_avatar_candidate", "target_primary_id"), (lut_rel, "voice_lut_candidate", "target_lut_id")):
                candidates = foundation.execute(f"SELECT {target} FROM foundation_candidate WHERE relation_id=? ORDER BY id", (relation[0],)).fetchall()
                if len(candidates) != relation[2]:
                    raise ValueError("Voice candidate count")
                conn.executemany(f"INSERT INTO {table} VALUES(?,?,?)", ((row_id, relation[0], target_id) for (target_id,) in candidates))
        for row in foundation.execute("SELECT l.id,l.source_path,l.object_key,l.object_ordinal,l.pointer,l.voice_slot,k.item_id FROM foundation_voice_lut AS l JOIN foundation_voice_link AS k ON k.lut_id=l.id ORDER BY l.source_path,l.object_ordinal"):
            conn.execute("INSERT INTO lut_ref VALUES(?,?,?,?,?,?,?)", row)
        for row in foundation.execute("SELECT id,source_path,object_key,object_ordinal,pointer,variants_status FROM foundation_voice_item ORDER BY source_path,object_ordinal"):
            conn.execute("INSERT INTO item_ref VALUES(?,?,?,?,?,?)", row)
        for row in foundation.execute("SELECT id,item_id,ordinal,pointer,audio_path,selector,order_value FROM foundation_audio_variant ORDER BY item_id,ordinal"):
            conn.execute("INSERT INTO audio_ref VALUES(?,?,?,?,?,?,?)", row)
        expected = {"voice_record": 9183, "voice_text_ref": 9183 * 3, "voice_lut_candidate": 9183, "lut_ref": 375, "item_ref": 375, "audio_ref": 9770}
        for table, count in expected.items():
            if conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] != count:
                raise ValueError("Voice full denominator " + table)
        unattached = conn.execute("SELECT count(*) FROM lut_ref WHERE lut_id NOT IN(SELECT lut_id FROM voice_lut_candidate)").fetchone()[0]
        missing = conn.execute("SELECT count(*) FROM item_ref WHERE variants_status='missing'").fetchone()[0]
        if (unattached, missing) != (8, 1):
            raise ValueError("Voice legal missing/unattached denominator")
        release = _release("voice", conn, Path(foundation_path), text=Path(text_path), foundation_run_id=_foundation_run(foundation))
        report = _finish(conn, output, {**release, "unattached_lut_slots": unattached, "missing_variant_arrays": missing, "coverage": "Character/Fetter Voice only", "silent_skips": 0})
        conn = None
        return report
    except Exception:
        if conn is not None:
            conn.close()
        raise
    finally:
        foundation.close()
        text.close()
