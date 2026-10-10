"""Independent, full-source 7.1 LoadingTips artifact verifier."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .loading_tips_7_1_source import FIELDS, LANGUAGES, PRIMARY, array_records, check_scope, pairs, sha

ROOT = Path(__file__).resolve().parents[2]
TABLES = ("loading_tip_row", "loading_tip_slot", "loading_tip_text")
IMPLEMENTATION = ("src/genshin_lore/loading_tips_7_1_build.py",
                  "src/genshin_lore/loading_tips_7_1_source.py",
                  "src/genshin_lore/loading_tips_source.py", "schemas/loading-tips-v1.sql")


def _identity(*values: object) -> str:
    return "lt71:" + hashlib.sha256(
        json.dumps(values, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()[:40]


def verify(source_root: Path, scope_path: Path, artifact: Path, *, runtime_manifest: Path | None = None) -> dict:
    source_root, scope_path, artifact = Path(source_root), Path(scope_path), Path(artifact)
    scope = json.loads(scope_path.read_text())
    check_scope(source_root, scope)
    if any(Path(str(artifact) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("LoadingTips SQLite sidecar")
    implementation = {name: sha(ROOT / name) for name in IMPLEMENTATION}
    parser = _identity("parser", implementation)
    scope_sha = sha(scope_path)
    run = _identity("run", parser, scope_sha)
    source = list(array_records(source_root / PRIMARY))
    if len(source) != scope["primary_rows"]:
        raise ValueError("LoadingTips full source denominator")
    locators: set[str] = set()
    for ordinal, row, _ in source:
        if not isinstance(row, dict) or type(row.get("id")) is not int:
            raise ValueError("LoadingTips physical ID/shape")
        for field in FIELDS:
            if type(row.get(field)) is not int:
                raise ValueError(f"LoadingTips text owner absent/noninteger: /{ordinal}/{field}")
            locators.add(str(row[field]))
    textmap: dict[tuple[str, str], dict[str, list[tuple]]] = {}
    for locale in LANGUAGES:
        for medium in (False, True):
            path = f"TextMap/TextMap{'_Medium' if medium else ''}{locale}.json"
            index: dict[str, list[tuple]] = defaultdict(list)
            for pair_ordinal, (key, text) in enumerate(pairs(source_root / path)):
                if key in locators:
                    index[key].append((path, pair_ordinal, "/" + key, text))
            textmap[locale, "medium" if medium else "main"] = index
    expected: dict[str, list[tuple]] = {name: [] for name in TABLES}
    states: Counter = Counter()
    for ordinal, value, raw in source:
        row_id = _identity(run, "row", ordinal)
        expected["loading_tip_row"].append((
            row_id, run, ordinal, str(value["id"]), PRIMARY, f"/{ordinal}", raw,
        ))
        for field in FIELDS:
            for locale in LANGUAGES:
                locator = str(value[field])
                candidates = textmap[locale, "main"].get(locator, [])
                if not candidates:
                    candidates = textmap[locale, "medium"].get(locator, [])
                status = "missing" if not candidates else "resolved" if len(candidates) == 1 else "ambiguous"
                states[status] += 1
                slot_id = _identity(run, "slot", ordinal, field, locale)
                expected["loading_tip_slot"].append((slot_id, row_id, field, locale, locator, status))
                for path, pair_ordinal, pointer, text in candidates:
                    expected["loading_tip_text"].append((
                        _identity(run, "text", ordinal, field, locale, path, pair_ordinal),
                        slot_id, path, pair_ordinal, pointer, text,
                    ))
    for table in TABLES:
        expected[table].sort()
    release = {"contract": "loading-tips-7.1-standalone-v1",
               "family_contract_version": "loading-tips-evidence-v1",
               "snapshot_id": scope["snapshot_id"], "parser_build_id": parser,
               "run_id": run, "scope_sha256": scope_sha,
               "implementation": implementation,
               "counts": {table: len(expected[table]) for table in TABLES},
               "text_state_counts": dict(sorted(states.items())),
               "silent_skips": 0, "cross_snapshot_lookup_count": 0,
               "scope_complete_for_selected_paths": True, "full_7_1": False}
    before = sha(artifact)
    db = sqlite3.connect(artifact.absolute().as_uri() + "?mode=ro&immutable=1", uri=True)
    schema = sqlite3.connect(":memory:")
    schema.executescript((ROOT / "schemas/loading-tips-v1.sql").read_text())
    definition_sql = "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    try:
        if db.execute(definition_sql).fetchall() != schema.execute(definition_sql).fetchall():
            raise ValueError("LoadingTips schema/index/trigger definition mismatch")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("LoadingTips integrity/FK")
        for table in TABLES:
            got = db.execute("SELECT * FROM " + table + " ORDER BY id").fetchall()
            if got != expected[table]:
                raise ValueError("LoadingTips source-to-artifact row mismatch: " + table)
        release_rows = db.execute("SELECT manifest_json FROM loading_tip_release").fetchall()
        if len(release_rows) != 1 or json.loads(release_rows[0][0]) != release:
            raise ValueError("LoadingTips release mismatch")
    finally:
        db.close()
        schema.close()
    after = sha(artifact)
    if before != after:
        raise ValueError("LoadingTips artifact drift during verifier")
    logical = {table: hashlib.sha256("\n".join(
        json.dumps(list(row), ensure_ascii=True, separators=(",", ":"))
        for row in expected[table]).encode()).hexdigest() for table in TABLES}
    if runtime_manifest is not None:
        authority = json.loads(Path(runtime_manifest).read_text())
        if (authority["release"] != release or authority["artifact"] != {
            "size_bytes": artifact.stat().st_size, "sha256": after
        } or authority["logical_seals"] != logical):
            raise ValueError("LoadingTips runtime manifest mismatch")
    check_scope(source_root, scope)
    return {"status": "PASS", "source_files": len(scope["files"]),
            "verifier_implementation_sha256": sha(Path(__file__)),
            "runtime_manifest_checked": runtime_manifest is not None,
            "runtime_manifest_sha256": sha(Path(runtime_manifest)) if runtime_manifest is not None else None,
            "source_rows": len(source), "text_owner_occurrences": len(source) * len(FIELDS),
            "counts": release["counts"], "text_state_counts": release["text_state_counts"],
            "snapshot_id": scope["snapshot_id"], "run_id": run, "parser_build_id": parser,
            "artifact": {"size_bytes": artifact.stat().st_size, "sha256": after},
            "logical_seals": logical, "schema_full_definition_match": True,
            "all_business_rows_independently_reconstructed": True,
            "no_extras_no_omissions": True, "silent_skips": 0, "sidecars_absent": True}
