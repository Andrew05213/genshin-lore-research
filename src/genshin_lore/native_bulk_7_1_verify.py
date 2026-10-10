"""Independent source-to-artifact verifier for the 7.1 Native Bulk slice.

No builder/projection helper is imported.  The expected schema is created from
the tracked SQL contract, never learned from the candidate database.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from .event_narrative_source import array_records, git_blob, pairs, sha

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
TREE = "aa578c08c402af39b41f1ffc95124d10f5410272"
SELECTION = "manifests/native-archive/bulk-selection.json"
TABLES = ("native_archive_container", "native_archive_slot", "native_archive_text",
          "native_archive_relation", "native_archive_candidate")
IMPLEMENTATION = ("src/genshin_lore/native_bulk_7_1_build.py",
                  "src/genshin_lore/event_narrative_source.py",
                  "schemas/native-archive-v1.sql", SELECTION)


def _id(*values: object) -> str:
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()
    return "native-archive:" + hashlib.sha256(encoded).hexdigest()[:40]


def _objects(db: sqlite3.Connection) -> list[tuple]:
    return db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                      "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()


def verify(source_root: Path, scope_path: Path, artifact: Path, *, runtime_manifest: Path | None = None) -> dict:
    source_root, scope_path, artifact = Path(source_root), Path(scope_path), Path(artifact)
    if any(Path(str(artifact) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("SQLite sidecars beside candidate")
    head, tree = subprocess.check_output(
        ["git", "-C", str(source_root), "rev-parse", "HEAD", "HEAD^{tree}"], text=True
    ).splitlines()
    if (head, tree) != (COMMIT, TREE) or subprocess.check_output(
        ["git", "-C", str(source_root), "status", "--porcelain"]
    ):
        raise ValueError("source worktree identity or cleanliness")
    scope = json.loads(scope_path.read_text())
    selection = json.loads((ROOT / SELECTION).read_text())
    if (scope["source_commit"], scope["source_tree"], scope["snapshot_id"]) != (
        COMMIT, TREE, "anime-game-data:7.1:" + COMMIT
    ):
        raise ValueError("scope snapshot")
    if (scope.get("selection_manifest") != SELECTION or scope.get("selection_sha256") != sha(ROOT / SELECTION)
            or scope.get("sources") != selection["sources"] or scope.get("textmaps") != selection["textmaps"]
            or scope.get("languages") != selection["languages"]):
        raise ValueError("selection authority")
    if scope.get("dependencies") != {} or scope.get("reuse_targets") != [] or scope.get("external_candidates") != []:
        raise ValueError("undeclared dependency/reuse")
    expected_paths = {spec["path"] for spec in selection["sources"]}
    expected_paths.update(selection["textmaps"][locale][tier]
                          for locale in selection["languages"] for tier in ("main", "medium"))
    if (len(selection["sources"]) != 49 or len(expected_paths) != 55 or len(scope["files"]) != 55
            or {x["path"] for x in scope["files"]} != expected_paths):
        raise ValueError("49 source + six TextMap denominator")
    seals = {item["path"]: item for item in scope["files"]}
    for path in sorted(expected_paths):
        seal = seals[path]
        source = source_root / path
        oid = subprocess.check_output(
            ["git", "-C", str(source_root), "rev-parse", f"{COMMIT}:{path}"], text=True
        ).strip()
        if (not source.is_file() or source.is_symlink() or source.stat().st_size != seal["size_bytes"]
                or sha(source) != seal["sha256"] or git_blob(source) != oid or oid != seal["blob"]):
            raise ValueError("actual source bytes/Git blob mismatch: " + path)
    impl = {name: sha(ROOT / name) for name in IMPLEMENTATION}
    parser = "native-archive-7.1-parser:" + hashlib.sha256(
        json.dumps(impl, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:40]
    scope_sha = sha(scope_path)
    run = "native-archive-7.1-run:" + hashlib.sha256((parser + ":" + scope_sha).encode()).hexdigest()[:40]
    physical: dict[str, list[tuple]] = {}
    wanted: set[str] = set()
    owner_count = 0
    for spec in selection["sources"]:
        if spec.get("reuse") or spec.get("relations"):
            raise ValueError("Bulk relation/reuse outside selected contract")
        raw_rows = list(array_records(source_root / spec["path"]))
        for ordinal, value, _ in raw_rows:
            if type(value) is not dict:
                raise ValueError("raw source row is not object")
            if spec.get("native_id_field") is not None and spec["native_id_field"] not in value:
                raise ValueError("native ID field absent")
            for owner in spec["text_owners"]:
                field = owner["field"]
                if type(value.get(field)) is not int:
                    raise ValueError(f"TextMap owner missing/noninteger: {spec['path']} /{ordinal}/{field}")
                wanted.add(str(value[field]))
                owner_count += 1
        physical[spec["path"]] = raw_rows
    lookup: dict[tuple[str, str], dict[str, list[tuple]]] = {}
    for locale in selection["languages"]:
        for tier in ("main", "medium"):
            path = selection["textmaps"][locale][tier]
            by_key: dict[str, list[tuple]] = defaultdict(list)
            for ordinal, (key, value) in enumerate(pairs(source_root / path)):
                if key in wanted:
                    by_key[key].append((path, ordinal, "/" + key, value))
            lookup[locale, tier] = by_key
    expected: dict[str, list[tuple]] = {table: [] for table in TABLES}
    states: Counter = Counter()
    roles: Counter = Counter()
    for spec in selection["sources"]:
        path = spec["path"]
        file = seals[path]
        for ordinal, value, raw in physical[path]:
            pointer = "/" + str(ordinal)
            container_id = _id(run, "container", path, pointer)
            id_field = spec.get("native_id_field")
            expected["native_archive_container"].append((
                container_id, run, spec["boundary"], "physical_row", path,
                file["sha256"], file["blob"], ordinal, pointer, id_field,
                None if id_field is None else str(value[id_field]), raw,
                hashlib.sha256(raw.encode()).hexdigest(), None, None, None, None, None,
            ))
            for owner in spec["text_owners"]:
                field = owner["field"]
                locator = str(value[field])
                for locale in selection["languages"]:
                    main = lookup[locale, "main"].get(locator, [])
                    medium = lookup[locale, "medium"].get(locator, [])
                    matches = main if main else medium
                    status = "missing" if not matches else "resolved_main" if main and len(matches) == 1 else "resolved_medium" if medium and len(matches) == 1 else "ambiguous"
                    states[status] += 1
                    roles[owner["owner_role_status"]] += 1
                    slot_id = _id(run, "slot", path, pointer, field, locale)
                    expected["native_archive_slot"].append((
                        slot_id, container_id, field, owner["owner_role_status"], locale,
                        locator, status, len(matches),
                    ))
                    for source_path, pair_ordinal, pair_pointer, text in matches:
                        seal = seals[source_path]
                        expected["native_archive_text"].append((
                            _id(run, "text", slot_id, source_path, pair_ordinal), slot_id,
                            source_path, seal["sha256"], seal["blob"], pair_ordinal,
                            pair_pointer, text, hashlib.sha256(text.encode()).hexdigest(),
                        ))
    for table in TABLES:
        expected[table].sort()
    counts = {table: len(expected[table]) for table in TABLES}
    release_expected = {
        "contract": "native-bulk-7.1-standalone-v1", "family_contract_version": "native-archive-evidence-v1",
        "snapshot_id": scope["snapshot_id"], "parser_build_id": parser, "run_id": run,
        "scope_sha256": scope_sha, "implementation": impl, "counts": counts,
        "text_state_counts": dict(sorted(states.items())), "owner_role_counts": dict(sorted(roles.items())),
        "scope_complete_for_selected_paths": True, "full_7_1_native_text": False,
        "silent_skips": 0, "cross_snapshot_lookup_count": 0,
    }
    before = sha(artifact)
    db = sqlite3.connect(artifact.absolute().as_uri() + "?mode=ro&immutable=1", uri=True)
    schema = sqlite3.connect(":memory:")
    schema.execute("PRAGMA foreign_keys=ON")
    schema.executescript((ROOT / "schemas/native-archive-v1.sql").read_text())
    try:
        if _objects(db) != _objects(schema):
            raise ValueError("schema/index/trigger definition mismatch")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("integrity/FK")
        for table in TABLES:
            got = db.execute("SELECT * FROM " + table + " ORDER BY id").fetchall()
            if got != expected[table]:
                raise ValueError("source-to-artifact business mismatch: " + table)
        rows = db.execute("SELECT id,manifest_json FROM native_archive_release").fetchall()
        if len(rows) != 1 or rows[0][0] != 1 or json.loads(rows[0][1]) != release_expected:
            raise ValueError("release metadata mismatch")
    finally:
        db.close()
        schema.close()
    after = sha(artifact)
    if after != before:
        raise ValueError("artifact drift during verification")
    logical = {table: hashlib.sha256("\n".join(
        json.dumps(list(row), ensure_ascii=False, separators=(",", ":"))
        for row in expected[table]).encode()).hexdigest() for table in TABLES}
    if runtime_manifest is not None:
        manifest = json.loads(Path(runtime_manifest).read_text())
        if (manifest["release"] != release_expected or manifest["artifact"] != {
            "size_bytes": artifact.stat().st_size, "sha256": after
        } or manifest["logical_seals"] != logical):
            raise ValueError("runtime authority mismatch")
    return {
        "status": "PASS", "snapshot_id": scope["snapshot_id"], "scope_sha256": scope_sha,
        "verifier_implementation_sha256": sha(Path(__file__)),
        "runtime_manifest_checked": runtime_manifest is not None,
        "runtime_manifest_sha256": sha(Path(runtime_manifest)) if runtime_manifest is not None else None,
        "parser_build_id": parser, "run_id": run, "source_files": len(expected_paths),
        "source_rows": counts["native_archive_container"], "text_owner_occurrences": owner_count,
        "counts": counts, "text_state_counts": dict(sorted(states.items())),
        "artifact": {"size_bytes": artifact.stat().st_size, "sha256": after},
        "logical_seals": logical, "schema_full_definition_match": True,
        "all_business_rows_independently_reconstructed": True,
        "no_extras_no_omissions": True, "sidecars_absent": True,
        "silent_skips": 0, "cross_snapshot_lookup_count": 0,
    }
