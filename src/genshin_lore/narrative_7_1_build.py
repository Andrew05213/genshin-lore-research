"""Fresh physical successors for three explicitly frozen narrative families."""
from .platform_io import lock_exclusive, sync_directory
from .batch_release import fsync_file
import hashlib
import json
import os
import sqlite3
import tempfile
import time
import shutil
from collections import Counter, defaultdict
from pathlib import Path

from .loading_tips_source import array_records, pairs
from .narrative_7_1_contract import (
    ROOT, SPECS, SCOPE, SCHEMA, SNAPSHOT, COMMIT, TREE, TABLES, LOCALES,
    canonical, digest, sha, check_source, no_sidecars, guards, ref,
)
from .batch_space import ProjectSpaceGate, broad_inventory, _walk_bytes, PROJECT_SPACE_LIMIT_BYTES


def build(family, source, output, accounting_root):
    if family not in SPECS:
        raise ValueError("unknown family")
    source, output = Path(source), Path(output).absolute()
    scope = json.loads(SCOPE.read_text())
    check_source(source, scope)
    no_sidecars(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    gate = ProjectSpaceGate(accounting_root, accounting_tmp_roots=(source, ROOT))
    gate.check("before_" + family, projected_bytes=3 * 1024**3, cleanup=False)
    # Include attributable non-convention source/model roots as well. Discovery
    # here grants accounting only, never cleanup permission.
    broad = broad_inventory(accounting_root)
    roots = {Path(r.path) for r in broad.roots} | {source.absolute(), ROOT}
    roots.update(gate.discovery_root.glob("genshin-semantic*"))
    seen = set()
    allocated = sum(_walk_bytes(p, seen)[1] for p in sorted(roots, key=lambda p: (len(p.parts), str(p))))
    host_free = shutil.disk_usage(output.parent).free
    projected = 3 * 1024**3
    if allocated + projected >= PROJECT_SPACE_LIMIT_BYTES or host_free < projected:
        raise ValueError("inclusive source/model project-space preflight")
    output.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(output) + ".lock").open("a") as lock:
        lock_exclusive(lock, blocking=False)
        if output.exists():
            raise FileExistsError(output)
        start = time.monotonic()
        path, expected, key_field, roles = SPECS[family]
        rows = list(array_records(source / path))
        if len(rows) != expected:
            raise ValueError("physical denominator")
        field_types = defaultdict(Counter)
        owners = {}
        lookup = defaultdict(list)
        for ordinal, row, raw in rows:
            if type(row.get(key_field)) is not int:
                raise ValueError("source-owned lookup key shape")
            lookup[str(row[key_field])].append(ordinal)
            for field, value in row.items():
                field_types[field][type(value).__name__] += 1
            selected = dict(roles)
            if family == "reminder":
                selected = {field: "uninterpreted_numeric_candidate_observation" for field, value in row.items()
                            if type(value) is int and field not in ("id", "nextReminderId")}
            if any(type(row.get(field)) is not int for field in selected):
                raise ValueError("text locator type")
            owners[ordinal] = selected
        if dict(field_types) != scope["families"][family]["field_types"]:
            raise ValueError("unknown source field shape")
        npc_lookup = defaultdict(list)
        if family == "dialog":
            for n, row, _ in array_records(source / SPECS["npc"][0]):
                npc_lookup[str(row["id"])].append(n)
        wanted = {str(row[field]) for n, row, _ in rows for field in owners[n]}
        maps = defaultdict(list)
        for locale in LOCALES:
            for suffix, tier in (("", "main"), ("_Medium", "medium")):
                tm_path = f"TextMap/TextMap{suffix}{locale}.json"
                for n, (key, text) in enumerate(pairs(source / tm_path)):
                    if key in wanted:
                        maps[(locale, key)].append((tier, tm_path, n, text))
        implementation = {p: sha(ROOT / p) for p in (
            "src/genshin_lore/narrative_7_1_build.py", "src/genshin_lore/narrative_7_1_contract.py",
            "src/genshin_lore/loading_tips_source.py", "schemas/narrative-7.1-npc-dialog-reminder.sql")}
        parser = "narrative71-parser:" + digest(implementation)
        run = "narrative71-run:" + digest([family, parser, sha(SCOPE)])
        fd, name = tempfile.mkstemp(prefix=".narrative71-", suffix=".sqlite", dir=output.parent)
        os.close(fd)
        stage = Path(name)
        owned_inode = (stage.stat().st_dev, stage.stat().st_ino)
        c = sqlite3.connect(stage)
        heartbeat = time.monotonic()
        try:
            c.executescript(SCHEMA.read_text())
            c.execute("BEGIN")

            def locator(n, field, position, value, target_family, state, matches):
                c.execute("INSERT INTO locator VALUES(?,?,?,?,?,?,?)", (n, field, position, canonical(value), target_family, state, len(matches)))
                target_path = SPECS[target_family][0]
                c.executemany("INSERT INTO physical_candidate VALUES(?,?,?,?,?,?)", ((n, field, position, target, target_path, f"/{target}") for target in matches))

            def resolution(matches):
                return "missing" if not matches else "resolved_unique" if len(matches) == 1 else "resolved_candidate_set"

            for n, row, raw in rows:
                c.execute("INSERT INTO physical_row VALUES(?,?,?,?,?,?,?)", (n, ref(family, run, n), path, f"/{n}", str(row[key_field]), raw, hashlib.sha256(raw.encode()).hexdigest()))
                for field, role in sorted(owners[n].items()):
                    key = str(row[field])
                    c.execute("INSERT INTO text_owner VALUES(?,?,?,?)", (n, field, key, role))
                    for locale in LOCALES:
                        found = maps[(locale, key)]
                        c.execute("INSERT INTO text_resolution VALUES(?,?,?,?,?)", (n, field, locale, resolution(found), len(found)))
                        c.executemany("INSERT INTO text_candidate VALUES(?,?,?,?,?,?,?,?)", ((n, field, locale, tier, tm_path, position, key, text) for tier, tm_path, position, text in found))
                if family == "dialog":
                    links = row.get("nextDialogs")
                    if type(links) is not list or any(type(v) is not int for v in links):
                        raise ValueError("nextDialogs shape")
                    if not links:
                        locator(n, "nextDialogs", -1, [], family, "terminal_empty", [])
                    for i, value in enumerate(links):
                        found = lookup[str(value)]
                        locator(n, "nextDialogs", i, value, family, resolution(found), found)
                    role = row.get("talkRole")
                    found = []
                    if type(role) is not dict or "type" not in role:
                        state = "absent_discriminator"
                    elif role["type"] != "TALK_ROLE_NPC":
                        state = "not_applicable"
                    else:
                        value = role.get("id")
                        if type(value) is not str or not value.isascii() or not value.isdecimal() or str(int(value)) != value:
                            state = "mapping_unresolved"
                        else:
                            found = npc_lookup[value]
                            state = resolution(found)
                    locator(n, "talkRole", 0, role, "npc", state, found)
                if family == "reminder":
                    value = row.get("nextReminderId")
                    if "nextReminderId" not in row:
                        locator(n, "nextReminderId", 0, None, family, "terminal_omitted", [])
                    elif type(value) is not int:
                        raise ValueError("nextReminderId type")
                    elif value == 0:
                        locator(n, "nextReminderId", 0, value, family, "terminal_zero", [])
                    else:
                        found = lookup[str(value)]
                        locator(n, "nextReminderId", 0, value, family, resolution(found), found)
                if time.monotonic() - heartbeat >= 30:
                    print(f"{family}: {n + 1}/{expected} physical rows", flush=True)
                    heartbeat = time.monotonic()
            counts = {table: c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in TABLES if table != "release"}
            release = {"contract": f"{family}-7.1-narrative-physical-v1", "family": family, "snapshot": SNAPSHOT,
                       "source_commit": COMMIT, "source_tree": TREE, "parser": parser, "run": run,
                       "scope_sha256": sha(SCOPE), "schema_sha256": sha(SCHEMA), "implementation": implementation,
                       "source_files": scope["files"], "counts": counts, "semantic_mapping": "unresolved" if family == "reminder" else "source_authored_declared_fields",
                       "textmap_policy": scope["textmap_policy"], "product_accepted": False, "research_corpus_accepted": False}
            c.execute("INSERT INTO release VALUES(1,?)", (canonical(release),))
            c.commit()
            c.executescript(guards())
            if c.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or c.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("SQLite integrity/FK")
            c.close()
            no_sidecars(stage)
            check_source(source, scope)
            fsync_file(stage)
            no_sidecars(output)
            os.link(stage, output)
            stage.unlink()
            sync_directory(output.parent)
            return {"status": "built_unaccepted_candidate", "family": family, "artifact": {"size_bytes": output.stat().st_size, "sha256": sha(output)},
                    "release": release, "build_seconds": time.monotonic() - start,
                    "promotion_inode": list(owned_inode),
                    "inclusive_space": {"allocated_bytes": allocated, "projected_bytes": projected, "host_free_bytes": host_free,
                                        "accounting_roots": sorted(str(p) for p in roots)},
                    "space_preflight": gate.events, "silent_skips": 0}
        except BaseException:
            c.close()
            if output.exists() and (output.stat().st_dev, output.stat().st_ino) == owned_inode:
                output.unlink()
            if stage.exists() and (stage.stat().st_dev, stage.stat().st_ino) == owned_inode:
                stage.unlink()
            raise
