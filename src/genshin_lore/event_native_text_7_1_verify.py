"""Independent source-to-rowset oracle for Event component-17 7.1."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .event_7_1_source import array_records, check_files, pairs, sha

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / 'schemas/event-native-text-wave-7.1-v1.sql'
TABLES = ('event71_row', 'event71_slot', 'event71_text', 'event71_relation', 'event71_candidate')


def key(*items: object) -> str:
    return 'event71:' + hashlib.sha256(json.dumps(items, ensure_ascii=False, sort_keys=True,
                                                  separators=(',', ':')).encode()).hexdigest()[:40]


def rows(db: sqlite3.Connection, table: str) -> list[tuple]:
    cols = [item[1] for item in db.execute('PRAGMA table_info(' + table + ')')]
    return sorted(tuple(item) for item in db.execute('SELECT ' + ','.join(cols) + ' FROM ' + table))


def verify(source: Path, scope_path: Path, artifact: Path, build_report: dict) -> dict:
    source, scope_path, artifact = Path(source), Path(scope_path), Path(artifact)
    scope = json.loads(scope_path.read_text())
    check_files(source, scope)
    if (scope['contract'] != 'event-native-text-wave-7.1-exact-scope-v1'
            or scope['historical_scope_sha256'] != sha(ROOT / scope['historical_scope'])):
        raise ValueError('Event 7.1 oracle scope')
    historical = json.loads((ROOT / scope['historical_scope']).read_text())
    chosen_paths = {spec['path'] for spec in historical['sources']}
    chosen_paths.update('TextMap/TextMap' + suffix + language + '.json'
                        for language in historical['languages'] for suffix in ('', '_Medium'))
    if (scope['sources'] != historical['sources'] or scope['languages'] != historical['languages']
            or {entry['path'] for entry in scope['files']} != chosen_paths
            or set(scope['row_denominator']) != {spec['kind'] for spec in historical['sources']}):
        raise ValueError('Event 7.1 oracle selector/field drift')
    if any(Path(str(artifact) + ext).exists() for ext in ('-wal', '-shm', '-journal')):
        raise ValueError('Event 7.1 sidecar')
    if artifact.stat().st_size != build_report['artifact']['size_bytes'] or sha(artifact) != build_report['artifact']['sha256']:
        raise ValueError('Event 7.1 artifact seal')
    parser_ingredients = {name: sha(ROOT / name) for name in (
        'src/genshin_lore/event_native_text_7_1_build.py', 'src/genshin_lore/event_7_1_source.py',
        'schemas/event-native-text-wave-7.1-v1.sql')}
    parser = key('parser', parser_ingredients)
    run = key('run', parser_ingredients, sha(scope_path))
    if build_report['parser_build_id'] != parser or build_report['run_id'] != run:
        raise ValueError('Event 7.1 parser/run identity')
    expected: dict[str, list[tuple]] = {t: [] for t in TABLES}
    seals = {item['path']: item for item in scope['files']}
    root_rows = {}
    targets = defaultdict(list)
    owner_hashes = set()
    for declaration in scope['sources']:
        path = declaration['path']
        physical = list(array_records(source / path))
        if len(physical) != scope['row_denominator'][declaration['kind']]:
            raise ValueError('Event 7.1 oracle denominator: ' + path)
        root_rows[declaration['kind']] = physical
        for ordinal, item, literal in physical:
            if not isinstance(item, dict):
                raise ValueError('Event 7.1 oracle row shape')
            pointer = '/' + str(ordinal)
            rid = key(run, path, pointer)
            native_field = declaration.get('native_id_field')
            if native_field and type(item.get(native_field)) is not int:
                raise ValueError('Event 7.1 oracle native shape')
            native = str(item[native_field]) if native_field else None
            seal = seals[path]
            expected['event71_row'].append((rid, run, declaration['kind'], declaration['boundary'], path,
                                            seal['sha256'], seal['blob'], ordinal, pointer, native_field, native,
                                            literal, hashlib.sha256(literal.encode()).hexdigest()))
            lookup = declaration.get('relation_lookup_field')
            if lookup:
                if type(item.get(lookup)) is not int:
                    raise ValueError('Event 7.1 oracle lookup shape')
                targets[(declaration['kind'], str(item[lookup]))].append((rid, path, pointer, str(item[lookup])))
            for owner in declaration['text_owners']:
                field = owner['field']
                if owner['shape'] != 'scalar' or type(item.get(field)) is not int:
                    raise ValueError('Event 7.1 oracle owner shape')
                owner_hashes.add(str(item[field]))
    text_pairs: dict[tuple[str, str, str], list[tuple]] = defaultdict(list)
    for locale in scope['languages']:
        for tier, suffix in (('main', ''), ('medium', '_Medium')):
            path = 'TextMap/TextMap' + suffix + locale + '.json'
            for ordinal, (hash_value, value) in enumerate(pairs(source / path)):
                if hash_value in owner_hashes:
                    text_pairs[(locale, tier, hash_value)].append((path, ordinal, '/' + hash_value, value))
    slot_states = Counter(); rel_states = Counter(); owner_count = 0
    for declaration in scope['sources']:
        path = declaration['path']
        for ordinal, item, _ in root_rows[declaration['kind']]:
            pointer = '/' + str(ordinal)
            rowid = key(run, path, pointer)
            for owner in declaration['text_owners']:
                field = owner['field']; locator = str(item[field]); owner_count += 1
                for locale in scope['languages']:
                    hit_main = text_pairs[(locale, 'main', locator)]
                    hit_medium = text_pairs[(locale, 'medium', locator)]
                    chosen = hit_main if hit_main else hit_medium
                    state = ('missing' if not chosen else 'resolved_candidate_set' if len(chosen) > 1 else
                             'resolved_main' if hit_main else 'resolved_medium')
                    slot_states[state] += 1
                    slotid = key(run, 'slot', path, pointer, field, locale)
                    expected['event71_slot'].append((slotid, rowid, field, owner['role_status'], locale,
                                                     locator, state, len(hit_main) + len(hit_medium), len(chosen)))
                    for tier, hits in (('main', hit_main), ('medium', hit_medium)):
                        for textpath, pair, textpointer, value in hits:
                            seal = seals[textpath]
                            expected['event71_text'].append((key(run, 'text', slotid, textpath, pair), slotid,
                                                             textpath, seal['sha256'], seal['blob'], tier, pair,
                                                             textpointer, value, hashlib.sha256(value.encode()).hexdigest(),
                                                             int((tier == 'main' and bool(hit_main)) or
                                                                 (tier == 'medium' and not hit_main))))
            for rule in declaration.get('internal_relations', []):
                field = rule['field']; locators = item.get(field)
                if rule['shape'] != 'array_scalar' or not isinstance(locators, list) or any(type(v) is not int for v in locators):
                    raise ValueError('Event 7.1 oracle relation shape')
                for offset, value in enumerate(locators):
                    locator = str(value)
                    candidates = sorted(targets[(rule['target_kind'], locator)])
                    status = 'missing' if not candidates else 'resolved_unique' if len(candidates) == 1 else 'resolved_candidate_set'
                    rel_states[status] += 1
                    at = pointer + '/' + field + '/' + str(offset)
                    relationid = key(run, 'relation', path, at, rule['relation_kind'])
                    expected['event71_relation'].append((relationid, rowid, rule['relation_kind'], field, at,
                                                         locator, rule['target_kind'], status, len(candidates), 1))
                    for tid, tpath, tpointer, native in candidates:
                        expected['event71_candidate'].append((key(run, 'candidate', relationid, tid), relationid,
                                                              tid, tpath, tpointer, native))
    actual = sqlite3.connect(artifact.as_uri() + '?mode=ro&immutable=1', uri=True)
    reference = sqlite3.connect(':memory:')
    try:
        reference.executescript(SCHEMA.read_text())
        schema_query = "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        if reference.execute(schema_query).fetchall() != actual.execute(schema_query).fetchall():
            raise ValueError('Event 7.1 full schema/trigger drift')
        if actual.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or actual.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Event 7.1 integrity/FK')
        for table in TABLES:
            if rows(actual, table) != sorted(expected[table]):
                raise ValueError('Event 7.1 oracle rowset mismatch: ' + table)
        release_row = actual.execute('SELECT manifest_json FROM event71_release WHERE id=1').fetchone()
        if not release_row or json.loads(release_row[0]) != {k: v for k, v in build_report.items() if k != 'artifact'}:
            raise ValueError('Event 7.1 release mismatch')
        if (build_report['counts'] != {t: len(expected[t]) for t in TABLES}
                or build_report['text_states'] != dict(sorted(slot_states.items()))
                or build_report['relation_states'] != dict(sorted(rel_states.items()))
                or build_report['text_owner_count'] != owner_count):
            raise ValueError('Event 7.1 self-reported counts drift')
    finally:
        reference.close(); actual.close()
    return {'status': 'PASS', 'independent_source_reconstruction': True, 'no_extras': True,
            'no_omissions': True, 'full_schema_and_triggers': True, 'source_snapshot': scope['snapshot_id'],
            'run_id': run, 'counts': {t: len(expected[t]) for t in TABLES}, 'text_states': dict(sorted(slot_states.items())),
            'relation_states': dict(sorted(rel_states.items())), 'artifact': build_report['artifact']}
