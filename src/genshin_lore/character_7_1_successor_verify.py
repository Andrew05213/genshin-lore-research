"""Independent full-row verifier for bounded 7.1 Character successors.

It does not import the successor builder or its projection helpers. Expected
rows are reconstructed from the sealed foundation and exact 7.1 TextMap files.
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
MANIFEST = ROOT / "manifests/character-7.1-physical-foundation/immutable-runtime.json"
SNAPSHOT = "anime-game-data:7.1:9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
COMMIT = "9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
TREE = "aa578c08c402af39b41f1ffc95124d10f5410272"
FOUNDATION_RUN = "character71:f13a6261e9ed01bf6a765e6d8b22167bfd71c8b1"
FOUNDATION_PARSER = "character71:4d23d9f656546874bfec8915f9fde9bee9a73243"
FOUNDATION_SIZE = 108290048
FOUNDATION_SHA = "20e065491d3696555fbeaae104c95284a9f7e9ae439861613a984829b6a1b8ef"
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


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _id(kind: str, *parts: object) -> str:
    return "character71-" + kind + ":" + hashlib.sha256(_canonical(parts).encode()).hexdigest()[:40]


def _eq(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise ValueError("successor verification failed: " + label)


def _rows(conn: sqlite3.Connection, table: str) -> list[tuple]:
    return sorted(conn.execute(f"SELECT * FROM {table}").fetchall())


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _schema(conn: sqlite3.Connection, kind: str) -> None:
    expected = sqlite3.connect(":memory:")
    try:
        expected.executescript((ROOT / SCHEMAS[kind]).read_text())
        for table in TABLES[kind]:
            for verb in ("INSERT", "UPDATE", "DELETE"):
                expected.execute(f"CREATE TRIGGER {table}_immutable_{verb.lower()} BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT,'immutable physical evidence'); END")
        query = "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        _eq(conn.execute(query).fetchall(), expected.execute(query).fetchall(), "full schema objects")
        for table in TABLES[kind]:
            for pragma in ("table_xinfo", "foreign_key_list", "index_list"):
                sql = f"PRAGMA {pragma}('{table}')"
                _eq(conn.execute(sql).fetchall(), expected.execute(sql).fetchall(), sql)
            for (name,) in expected.execute(f"SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (table,)):
                sql = f"PRAGMA index_xinfo('{name}')"
                _eq(conn.execute(sql).fetchall(), expected.execute(sql).fetchall(), sql)
    finally:
        expected.close()


def _foundation(path: Path, *, foundation_manifest=None) -> tuple[sqlite3.Connection, dict]:
    manifest = json.loads(Path(foundation_manifest or MANIFEST).read_text())
    _eq((manifest["artifact"]["size_bytes"], manifest["artifact"]["sha256"], manifest["source_snapshot"], manifest["parser_build_id"], manifest["ingest_run_id"]),
        (FOUNDATION_SIZE, FOUNDATION_SHA, SNAPSHOT, FOUNDATION_PARSER, FOUNDATION_RUN), "frozen foundation manifest")
    _eq((path.stat().st_size, sha(path)), (FOUNDATION_SIZE, FOUNDATION_SHA), "frozen foundation bytes")
    scope = json.loads((ROOT / manifest["source_scope"]).read_text())
    _eq((scope["source_commit"], scope["source_tree"], scope["snapshot_id"], len(scope["files"])), (COMMIT, TREE, SNAPSHOT, 18), "frozen source scope")
    conn = _open(path)
    _eq(json.loads(conn.execute("SELECT manifest_json FROM foundation_release WHERE id=1").fetchone()[0])["ingest_run_id"], FOUNDATION_RUN, "foundation release")
    _eq(conn.execute("SELECT count(*) FROM foundation_text_owner").fetchone()[0], 34817, "text owner count")
    return conn, scope


def _source(source: Path, scope: dict) -> None:
    actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD", "HEAD^{tree}"], text=True).splitlines()
    _eq(actual, [COMMIT, TREE], "source checkout pin")
    _eq(subprocess.check_output(["git", "-C", str(source), "status", "--porcelain=v1"], text=True).strip(), "", "source checkout clean")
    by_path = {entry["path"]: entry for entry in scope["files"]}
    _eq(len(by_path), 18, "source file denominator")
    for locale in ("CHS", "EN", "JP"):
        for suffix in ("", "_Medium"):
            path = f"TextMap/TextMap{suffix}{locale}.json"
            entry = by_path[path]
            _eq(((source / path).stat().st_size, sha(source / path)), (entry["size_bytes"], entry["sha256"]), "TextMap source bytes " + path)


def _verify_text(conn: sqlite3.Connection, foundation: sqlite3.Connection, source: Path, scope: dict) -> dict:
    _source(source, scope)
    owners = foundation.execute("SELECT id,row_id,pointer,field_name,textmap_hash FROM foundation_text_owner ORDER BY id").fetchall()
    keys = {row[4] for row in owners}
    by_key: dict[tuple[str, str], list[tuple[str, str, int, str, str]]] = defaultdict(list)
    for locale in ("CHS", "EN", "JP"):
        for suffix, tier in (("", "main"), ("_Medium", "medium")):
            path = f"TextMap/TextMap{suffix}{locale}.json"
            for ordinal, (key, value) in enumerate(pairs(source / path)):
                if key in keys:
                    by_key[(locale, key)].append((tier, path, ordinal, key, value))
    expected_resolution = []
    expected_candidate = []
    states = Counter()
    for owner_id, row_id, pointer, field, key in owners:
        for locale in ("CHS", "EN", "JP"):
            found = by_key[(locale, key)]
            status = "missing" if not found else "resolved_unique" if len(found) == 1 else "resolved_candidate_set"
            expected_resolution.append((owner_id, row_id, pointer, field, key, locale, status, len(found)))
            expected_candidate.extend((owner_id, locale, *candidate) for candidate in found)
            states[locale + ":" + status] += 1
    _eq(_rows(conn, "resolution"), sorted(expected_resolution), "all owner-locale resolution rows")
    _eq(_rows(conn, "candidate"), sorted(expected_candidate), "all exact TextMap candidate rows")
    _eq(len(owners), 34817, "owner denominator")
    return {"owner_count": len(owners), "resolution_states": dict(sorted(states.items())), "candidate_count": len(expected_candidate)}


def _verify_lore(conn: sqlite3.Connection, foundation: sqlite3.Connection, text: sqlite3.Connection) -> dict:
    _eq(text.execute("SELECT count(*) FROM resolution").fetchone()[0], 34817 * 3, "text binding")
    kinds = {"AvatarExcelConfigData.json": "avatar", "FetterInfoExcelConfigData.json": "fetter_info", "FetterStoryExcelConfigData.json": "story"}
    records, refs, fields, candidates = [], [], [], []
    counts = Counter()
    for basename, kind in kinds.items():
        path = "ExcelBinOutput/" + basename
        for row_id, ordinal, pointer in foundation.execute("SELECT id,ordinal,pointer FROM foundation_primary_row WHERE source_path=? ORDER BY ordinal", (path,)):
            relation = foundation.execute("SELECT id,status,candidate_count FROM foundation_relation WHERE source_row_id=? AND kind='source_avatar_id_candidate'", (row_id,)).fetchone() if kind != "avatar" else None
            status, count = (relation[1], relation[2]) if relation else ("not_applicable", 0)
            records.append((row_id, kind, path, ordinal, pointer, status, count))
            for owner_id, field, owner_pointer in foundation.execute("SELECT id,field_name,pointer FROM foundation_text_owner WHERE row_id=?", (row_id,)):
                refs.append((row_id, owner_id, field, owner_pointer))
            required_raw = () if kind == "avatar" else ("finishConds",) if kind == "fetter_info" else ("finishConds", "openConds")
            observed_parents = set()
            for field_id, field_pointer, name, role, source_ordinal, child_ordinal in foundation.execute("SELECT id,pointer,field_name,role,source_field_ordinal,child_ordinal FROM foundation_field WHERE row_id=? AND field_name IN (" + (",".join("?" for _ in required_raw) or "NULL") + ")", (row_id, *required_raw)):
                fields.append((row_id, field_id, field_pointer, name, role, source_ordinal, child_ordinal))
                if role == "source_condition_array":
                    observed_parents.add(name)
            _eq(observed_parents, set(required_raw), "Lore/Story required raw condition parents")
            if relation:
                targets = foundation.execute("SELECT target_primary_id FROM foundation_candidate WHERE relation_id=?", (relation[0],)).fetchall()
                _eq(len(targets), count, "Story owner candidates")
                candidates.extend((row_id, relation[0], target) for (target,) in targets)
            counts[kind] += 1
    _eq(_rows(conn, "lore_record"), sorted(records), "Lore/Story physical rows")
    _eq(_rows(conn, "lore_text_ref"), sorted(refs), "Lore/Story text owner pointers")
    _eq(_rows(conn, "lore_raw_field_ref"), sorted(fields), "Lore/Story raw unlock/open physical fields")
    _eq(_rows(conn, "lore_avatar_candidate"), sorted(candidates), "Lore/Story owner candidates")
    _eq(dict(counts), {"avatar": 167, "fetter_info": 124, "story": 974}, "Lore denominator")
    return {"record_counts": dict(counts), "text_refs": len(refs), "raw_condition_refs": len(fields), "avatar_candidates": len(candidates)}


def _verify_voice(conn: sqlite3.Connection, foundation: sqlite3.Connection, text: sqlite3.Connection) -> dict:
    _eq(text.execute("SELECT count(*) FROM resolution").fetchone()[0], 34817 * 3, "text binding")
    path = "ExcelBinOutput/FettersExcelConfigData.json"
    records, refs, fields, avatar_candidates, lut_candidates = [], [], [], [], []
    for row_id, ordinal, pointer in foundation.execute("SELECT id,ordinal,pointer FROM foundation_primary_row WHERE source_path=? ORDER BY ordinal", (path,)):
        avatar = foundation.execute("SELECT id,status,candidate_count FROM foundation_relation WHERE source_row_id=? AND kind='source_avatar_id_candidate'", (row_id,)).fetchone()
        lut = foundation.execute("SELECT id,status,candidate_count FROM foundation_relation WHERE source_row_id=? AND kind='fetter_voice_slot_candidate'", (row_id,)).fetchone()
        if not avatar or not lut:
            raise ValueError("Voice source locator absent")
        records.append((row_id, path, ordinal, pointer, avatar[1], avatar[2], lut[1], lut[2]))
        refs.extend((row_id, owner_id, field, owner_pointer) for owner_id, field, owner_pointer in foundation.execute("SELECT id,field_name,pointer FROM foundation_text_owner WHERE row_id=?", (row_id,)))
        observed_parents = set()
        for field_id, field_pointer, name, role, source_ordinal, child_ordinal in foundation.execute("SELECT id,pointer,field_name,role,source_field_ordinal,child_ordinal FROM foundation_field WHERE row_id=? AND field_name='openConds'", (row_id,)):
            fields.append((row_id, field_id, field_pointer, name, role, source_ordinal, child_ordinal))
            if role == "source_condition_array":
                observed_parents.add(name)
        _eq(observed_parents, {"openConds"}, "Voice required raw open condition parent")
        for relation, destination, column in ((avatar, avatar_candidates, "target_primary_id"), (lut, lut_candidates, "target_lut_id")):
            targets = foundation.execute(f"SELECT {column} FROM foundation_candidate WHERE relation_id=?", (relation[0],)).fetchall()
            _eq(len(targets), relation[2], "Voice owner/LUT candidate count")
            destination.extend((row_id, relation[0], target) for (target,) in targets)
    expected_lut = foundation.execute("SELECT l.id,l.source_path,l.object_key,l.object_ordinal,l.pointer,l.voice_slot,k.item_id FROM foundation_voice_lut l JOIN foundation_voice_link k ON k.lut_id=l.id").fetchall()
    expected_item = foundation.execute("SELECT id,source_path,object_key,object_ordinal,pointer,variants_status FROM foundation_voice_item").fetchall()
    expected_audio = foundation.execute("SELECT id,item_id,ordinal,pointer,audio_path,selector,order_value FROM foundation_audio_variant").fetchall()
    for table, expected in (("voice_record", records), ("voice_text_ref", refs), ("voice_raw_field_ref", fields), ("voice_avatar_candidate", avatar_candidates), ("voice_lut_candidate", lut_candidates), ("lut_ref", expected_lut), ("item_ref", expected_item), ("audio_ref", expected_audio)):
        _eq(_rows(conn, table), sorted(expected), "Voice complete table " + table)
    _eq((len(records), len(expected_lut), len(expected_item), len(expected_audio)), (9183, 375, 375, 9770), "Voice denominator")
    attached = {lut_id for _, _, lut_id in lut_candidates}
    unattached = sum(row[0] not in attached for row in expected_lut)
    missing = sum(row[5] == "missing" for row in expected_item)
    _eq((unattached, missing), (8, 1), "Voice legal missing/unattached")
    return {"voice_owner_rows": len(records), "raw_condition_refs": len(fields), "lut_count": len(expected_lut), "item_count": len(expected_item), "audio_variant_count": len(expected_audio), "unattached_lut_slots": unattached, "missing_variant_arrays": missing}


def verify(kind: str, foundation_path: str | Path, artifact_path: str | Path, *, source_root: str | Path | None = None, text_path: str | Path | None = None, expected_artifact: dict | None = None, foundation_manifest=None) -> dict:
    if kind not in SCHEMAS:
        raise ValueError("unknown successor kind")
    artifact = Path(artifact_path)
    if any(Path(str(artifact) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("successor sidecar")
    before = artifact.stat()
    seal = {"size_bytes": before.st_size, "sha256": sha(artifact)}
    if expected_artifact is not None:
        _eq(seal, expected_artifact, "candidate artifact seal")
    foundation, scope = _foundation(Path(foundation_path), foundation_manifest=foundation_manifest)
    conn = _open(artifact)
    text = _open(Path(text_path)) if text_path else None
    try:
        _eq(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok", "integrity")
        _eq(conn.execute("PRAGMA foreign_key_check").fetchall(), [], "foreign keys")
        _schema(conn, kind)
        if kind == "text":
            if source_root is None or text_path is not None:
                raise ValueError("text verifier requires exact source only")
            counts = _verify_text(conn, foundation, Path(source_root), scope)
        else:
            if text is None or source_root is not None:
                raise ValueError("Lore/Voice verifier requires text artifact only")
            counts = (_verify_lore if kind == "lore" else _verify_voice)(conn, foundation, text)
        implementation = {"src/genshin_lore/character_7_1_successor_build.py": sha(ROOT / "src/genshin_lore/character_7_1_successor_build.py"), SCHEMAS[kind]: sha(ROOT / SCHEMAS[kind])}
        parser_id = _id(kind, "parser", implementation)
        run_id = _id(kind, "run", parser_id, FOUNDATION_RUN, sha(Path(text_path)) if text_path else None)
        expected_release = {
            "contract": f"character-7.1-{kind}-successor-v1",
            "snapshot_id": SNAPSHOT,
            "foundation_run_id": FOUNDATION_RUN,
            "foundation_size_bytes": FOUNDATION_SIZE,
            "foundation_sha256": FOUNDATION_SHA,
            "parser_build_id": parser_id,
            "ingest_run_id": run_id,
            "implementation": implementation,
            "table_counts": {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in TABLES[kind] if table != "release"},
            "product_accepted": False,
            "research_corpus_accepted": False,
            "logical_character_created": False,
        }
        if text_path:
            expected_release["text_projection_sha256"] = sha(Path(text_path))
        if kind == "text":
            expected_release["textmap_sources"] = {entry["path"]: {"size_bytes": entry["size_bytes"], "sha256": entry["sha256"], "blob": entry["blob"]} for entry in scope["files"] if entry["path"].startswith("TextMap/")}
        release_rows = conn.execute("SELECT id,manifest_json FROM release").fetchall()
        _eq(len(release_rows), 1, "release row count")
        _eq(release_rows[0][0], 1, "release row identity")
        _eq(json.loads(release_rows[0][1]), expected_release, "exact release manifest")
    finally:
        if text:
            text.close()
        conn.close()
        foundation.close()
    after = artifact.stat()
    _eq((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, seal["sha256"]), (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, sha(artifact)), "artifact stable")
    return {"status": "PASS", "kind": kind, "artifact": seal, "parser_build_id": parser_id, "ingest_run_id": run_id, "counts": counts, "silent_skips": 0, "cross_snapshot_merge": 0}
