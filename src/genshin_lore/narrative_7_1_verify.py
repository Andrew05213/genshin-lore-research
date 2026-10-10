"""Independent source-to-SQL oracle. Does not import or invoke the builder."""
import hashlib
import json
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path
from .narrative_7_1_contract import ROOT, SCOPE, SCHEMA, SPECS, SNAPSHOT, COMMIT, TREE, TABLES, LOCALES, sha, canonical, digest, check_source, no_sidecars


def source_rows(path):
    text = Path(path).read_text(encoding="utf-8")
    decoder = json.JSONDecoder()
    position = text.index("[") + 1
    result = []
    while True:
        while text[position].isspace():
            position += 1
        if text[position] == "]":
            if text[position + 1:].strip():
                raise ValueError("trailing source bytes")
            return result
        start = position
        row, position = decoder.raw_decode(text, position)
        result.append((row, text[start:position]))
        while text[position].isspace():
            position += 1
        if text[position] == ",":
            position += 1
        elif text[position] != "]":
            raise ValueError("source separator")


def expected_schema():
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA.read_text())
    for table in TABLES:
        for verb in ("INSERT", "UPDATE", "DELETE"):
            db.execute(f"CREATE TRIGGER {table}_immutable_{verb.lower()} BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT,'immutable narrative evidence'); END")
    result = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
    db.close()
    return result


def verify(source, artifact, report):
    source, artifact = Path(source), Path(artifact)
    scope = json.loads(SCOPE.read_text())
    check_source(source, scope)
    no_sidecars(artifact)
    if {"size_bytes": artifact.stat().st_size, "sha256": sha(artifact)} != report["artifact"]:
        raise ValueError("artifact identity")
    before = artifact.stat()
    c = sqlite3.connect(artifact.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        if c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall() != expected_schema():
            raise ValueError("schema/index/trigger contract")
        if c.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or c.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("SQLite integrity/FK")
        release = json.loads(c.execute("SELECT payload FROM release WHERE id=1").fetchone()[0])
        if release != report["release"] or release["snapshot"] != SNAPSHOT or release["scope_sha256"] != sha(SCOPE) or release["schema_sha256"] != sha(SCHEMA):
            raise ValueError("release binding")
        if set(release["implementation"]) != {"src/genshin_lore/narrative_7_1_build.py", "src/genshin_lore/narrative_7_1_contract.py", "src/genshin_lore/loading_tips_source.py", "schemas/narrative-7.1-npc-dialog-reminder.sql"}:
            raise ValueError("allowed implementation path set")
        for path, seal in release["implementation"].items():
            if sha(ROOT / path) != seal:
                raise ValueError("implementation identity")
        parser = "narrative71-parser:" + digest(release["implementation"])
        run = "narrative71-run:" + digest([release["family"], parser, sha(SCOPE)])
        if (release["parser"], release["run"]) != (parser, run):
            raise ValueError("derived parser/run identity")
        family = release["family"]
        path, denominator, key_field, fields = SPECS[family]
        raw = source_rows(source / path)
        if len(raw) != denominator:
            raise ValueError("independent denominator")
        lookup = defaultdict(list)
        for n, (row, _) in enumerate(raw):
            lookup[str(row[key_field])].append(n)
        npc = defaultdict(list)
        if family == "dialog":
            for n, (row, _) in enumerate(source_rows(source / SPECS["npc"][0])):
                npc[str(row["id"])].append(n)
        wanted = {str(value) for row, _ in raw for field, value in row.items() if type(value) is int}
        textmaps = defaultdict(list)
        for locale in LOCALES:
            for tier, prefix in (("main", ""), ("medium", "_Medium")):
                file = f"TextMap/TextMap{prefix}{locale}.json"
                pairs = json.loads((source / file).read_text(), object_pairs_hook=lambda p: p)
                for ordinal, (key, text) in enumerate(pairs):
                    if key in wanted:
                        textmaps[(locale, key)].append((tier, file, ordinal, text))
        counts = Counter({"physical_row": denominator, "release": 1})
        states = Counter()
        proof = hashlib.sha256()
        heartbeat = time.monotonic()

        def state(matches):
            return "missing" if len(matches) == 0 else "resolved_unique" if len(matches) == 1 else "resolved_candidate_set"

        for n, (row, raw_json) in enumerate(raw):
            expected_ref = "narrative71:" + canonical([SNAPSHOT, family, release["run"], n])
            expected_row = (n, expected_ref, path, f"/{n}", str(row[key_field]), raw_json, hashlib.sha256(raw_json.encode()).hexdigest())
            if c.execute("SELECT * FROM physical_row WHERE ordinal=?", (n,)).fetchone() != expected_row:
                raise ValueError(f"physical source row {n}")
            selected = fields if family != "reminder" else {field: "uninterpreted_numeric_candidate_observation" for field, value in row.items() if type(value) is int and field not in ("id", "nextReminderId")}
            expected_owners = sorted((n, f, str(row[f]), role) for f, role in selected.items())
            if c.execute("SELECT * FROM text_owner WHERE row_ordinal=? ORDER BY field", (n,)).fetchall() != expected_owners:
                raise ValueError(f"text owner denominator {n}")
            resolutions, texts = [], []
            for _, field, key, role in expected_owners:
                for locale in LOCALES:
                    matches = textmaps[(locale, key)]
                    resolutions.append((n, field, locale, state(matches), len(matches)))
                    states["text:" + state(matches)] += 1
                    for tier, file, ordinal, text in matches:
                        texts.append((n, field, locale, tier, file, ordinal, key, text))
            if c.execute("SELECT * FROM text_resolution WHERE row_ordinal=? ORDER BY field,locale", (n,)).fetchall() != sorted(resolutions):
                raise ValueError(f"resolution {n}")
            if sorted(c.execute("SELECT * FROM text_candidate WHERE row_ordinal=?", (n,)).fetchall()) != sorted(texts):
                raise ValueError(f"exact TextMap candidates {n}")
            locators, candidates = [], []

            def expect(field, i, value, target, outcome, matches):
                locators.append((n, field, i, canonical(value), target, outcome, len(matches)))
                states[field + ":" + outcome] += 1
                candidates.extend((n, field, i, ordinal, SPECS[target][0], f"/{ordinal}") for ordinal in matches)

            if family == "dialog":
                if not row["nextDialogs"]:
                    expect("nextDialogs", -1, [], family, "terminal_empty", [])
                for i, value in enumerate(row["nextDialogs"]):
                    matches = lookup[str(value)]
                    expect("nextDialogs", i, value, family, state(matches), matches)
                role = row.get("talkRole")
                matches = []
                if not isinstance(role, dict) or "type" not in role:
                    outcome = "absent_discriminator"
                elif role["type"] != "TALK_ROLE_NPC":
                    outcome = "not_applicable"
                else:
                    value = role.get("id")
                    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or str(int(value)) != value:
                        outcome = "mapping_unresolved"
                    else:
                        matches = npc[value]
                        outcome = state(matches)
                expect("talkRole", 0, role, "npc", outcome, matches)
            elif family == "reminder":
                value = row.get("nextReminderId")
                if "nextReminderId" not in row:
                    expect("nextReminderId", 0, None, family, "terminal_omitted", [])
                elif value == 0:
                    expect("nextReminderId", 0, value, family, "terminal_zero", [])
                else:
                    matches = lookup[str(value)]
                    expect("nextReminderId", 0, value, family, state(matches), matches)
            if sorted(c.execute("SELECT * FROM locator WHERE row_ordinal=?", (n,)).fetchall()) != sorted(locators):
                raise ValueError(f"locator states {n}")
            if sorted(c.execute("SELECT * FROM physical_candidate WHERE row_ordinal=?", (n,)).fetchall()) != sorted(candidates):
                raise ValueError(f"candidate closure {n}")
            counts.update({"text_owner": len(expected_owners), "text_resolution": len(resolutions), "text_candidate": len(texts), "locator": len(locators), "physical_candidate": len(candidates)})
            # Business seal is coordinate based; parser/run refs are separately qualified.
            proof.update(canonical([path, n, raw_json, expected_owners, sorted(resolutions), sorted(texts), sorted(locators), sorted(candidates)]).encode())
            if time.monotonic() - heartbeat >= 30:
                print(f"verify {family}: {n + 1}/{denominator}", flush=True)
                heartbeat = time.monotonic()
        for table in TABLES:
            if c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] != counts[table]:
                raise ValueError("no extras/no omissions: " + table)
        if release["counts"] != {k: counts[k] for k in TABLES if k != "release"}:
            raise ValueError("self-reported counts")
        expected_release = {"contract": f"{family}-7.1-narrative-physical-v1", "family": family, "snapshot": SNAPSHOT,
                            "source_commit": COMMIT, "source_tree": TREE, "parser": parser, "run": run,
                            "scope_sha256": sha(SCOPE), "schema_sha256": sha(SCHEMA), "implementation": release["implementation"],
                            "source_files": scope["files"], "counts": {k: counts[k] for k in TABLES if k != "release"},
                            "semantic_mapping": "unresolved" if family == "reminder" else "source_authored_declared_fields",
                            "textmap_policy": scope["textmap_policy"], "product_accepted": False, "research_corpus_accepted": False}
        if release != expected_release:
            raise ValueError("complete release semantics")
        check_source(source, scope)
        no_sidecars(artifact)
        after = artifact.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("artifact drift during verifier")
        return {"verdict": "PASS", "family": family, "counts": dict(counts), "states": dict(sorted(states.items())),
                "business_sha256": proof.hexdigest(), "independent_source_reconstruction": True,
                "no_extras_no_omissions": True, "schema_triggers_indices": "PASS", "silent_skips": 0}
    finally:
        c.close()
