"""Event component-17 7.1: exact physical projection, not a logical Event entity."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .event_7_1_source import array_records, check_files, pairs, sha

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / 'schemas/event-native-text-wave-7.1-v1.sql'
TABLES = ('event71_row', 'event71_slot', 'event71_text', 'event71_relation', 'event71_candidate')


def ident(*parts: object) -> str:
    raw = json.dumps(parts, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    return 'event71:' + hashlib.sha256(raw.encode()).hexdigest()[:40]


def identities(scope_path: Path) -> tuple[str, str]:
    impl = {name: sha(ROOT / name) for name in (
        'src/genshin_lore/event_native_text_7_1_build.py', 'src/genshin_lore/event_7_1_source.py',
        'schemas/event-native-text-wave-7.1-v1.sql')}
    return ident('parser', impl), ident('run', impl, sha(scope_path))


def project(source: Path, scope_path: Path) -> tuple[dict, dict, dict]:
    scope = json.loads(scope_path.read_text())
    check_files(source, scope)
    if (scope['contract'] != 'event-native-text-wave-7.1-exact-scope-v1'
            or scope['historical_scope'] != 'manifests/event-narrative/native-text-archive-wave-scope.json'
            or scope['historical_scope_sha256'] != sha(ROOT / scope['historical_scope'])
            or len(scope['sources']) != 16 or len(scope['files']) != 22):
        raise ValueError('Event component-17 scope mismatch')
    historical = json.loads((ROOT / scope['historical_scope']).read_text())
    expected_paths = {item['path'] for item in historical['sources']}
    expected_paths |= {f'TextMap/TextMap{suffix}{locale}.json' for locale in historical['languages']
                       for suffix in ('', '_Medium')}
    if (scope['sources'] != historical['sources'] or scope['languages'] != historical['languages']
            or {item['path'] for item in scope['files']} != expected_paths
            or set(scope['row_denominator']) != {item['kind'] for item in historical['sources']}):
        raise ValueError('Event component-17 historical selector/fields drift')
    parser, run = identities(scope_path)
    records = {}
    for spec in scope['sources']:
        rows = list(array_records(source / spec['path']))
        if len(rows) != scope['row_denominator'][spec['kind']]:
            raise ValueError('Event 7.1 source denominator: ' + spec['path'])
        records[spec['kind']] = rows
    file_seals = {item['path']: item for item in scope['files']}
    expected: dict[str, list[tuple]] = {table: [] for table in TABLES}
    lookups: dict[tuple[str, str], list[tuple[str, str, str, str]]] = defaultdict(list)
    row_ids = {}
    wanted = set()
    for spec in scope['sources']:
        for ordinal, row, raw in records[spec['kind']]:
            if not isinstance(row, dict):
                raise ValueError('Event 7.1 non-object row')
            path = spec['path']; pointer = '/' + str(ordinal)
            rid = ident(run, path, pointer)
            row_ids[(spec['kind'], ordinal)] = rid
            native_field = spec.get('native_id_field')
            native = None if native_field is None else str(row[native_field])
            seal = file_seals[path]
            expected['event71_row'].append((rid, run, spec['kind'], spec['boundary'], path,
                                            seal['sha256'], seal['blob'], ordinal, pointer,
                                            native_field, native, raw, hashlib.sha256(raw.encode()).hexdigest()))
            lookup_field = spec.get('relation_lookup_field')
            if lookup_field is not None:
                value = row.get(lookup_field)
                if type(value) is not int:
                    raise ValueError('Event 7.1 lookup field shape: ' + path + pointer + '/' + lookup_field)
                lookups[(spec['kind'], str(value))].append((rid, path, pointer, str(value)))
            for owner in spec['text_owners']:
                field = owner['field']; locator = row.get(field)
                if owner['shape'] != 'scalar' or type(locator) is not int:
                    raise ValueError('Event 7.1 text owner shape: ' + path + pointer + '/' + field)
                wanted.add(str(locator))
    textmaps: dict[tuple[str, str, str], list[tuple]] = defaultdict(list)
    for locale in scope['languages']:
        for tier, suffix in (('main', ''), ('medium', '_Medium')):
            path = f'TextMap/TextMap{suffix}{locale}.json'
            for pair_ordinal, (key, text) in enumerate(pairs(source / path)):
                if key in wanted:
                    textmaps[(locale, tier, key)].append((path, pair_ordinal, '/' + key, text))
    slot_states = Counter(); relation_states = Counter(); owner_count = 0
    for spec in scope['sources']:
        path = spec['path']
        for ordinal, row, _ in records[spec['kind']]:
            pointer = '/' + str(ordinal); rid = row_ids[(spec['kind'], ordinal)]
            for owner in spec['text_owners']:
                field = owner['field']; locator = str(row[field]); owner_count += 1
                for locale in scope['languages']:
                    main = textmaps[(locale, 'main', locator)]
                    medium = textmaps[(locale, 'medium', locator)]
                    selected_tier = 'main' if main else 'medium' if medium else None
                    selected = main or medium
                    status = ('missing' if not selected else 'resolved_candidate_set' if len(selected) > 1
                              else 'resolved_main' if main else 'resolved_medium')
                    slot_states[status] += 1
                    sid = ident(run, 'slot', path, pointer, field, locale)
                    expected['event71_slot'].append((sid, rid, field, owner['role_status'], locale,
                                                     locator, status, len(main) + len(medium), len(selected)))
                    for tier, hits in (('main', main), ('medium', medium)):
                        for text_path, pair, tp, text in hits:
                            seal = file_seals[text_path]
                            tid = ident(run, 'text', sid, text_path, pair)
                            expected['event71_text'].append((tid, sid, text_path, seal['sha256'], seal['blob'],
                                                             tier, pair, tp, text, hashlib.sha256(text.encode()).hexdigest(),
                                                             int(tier == selected_tier)))
            for relspec in spec.get('internal_relations', []):
                field = relspec['field']; values = row.get(field)
                if relspec['shape'] != 'array_scalar' or not isinstance(values, list) or any(type(v) is not int for v in values):
                    raise ValueError('Event 7.1 relation shape: ' + path + pointer + '/' + field)
                for position, value in enumerate(values):
                    locator = str(value); target_kind = relspec['target_kind']
                    hits = sorted(lookups[(target_kind, locator)])
                    status = 'missing' if not hits else 'resolved_unique' if len(hits) == 1 else 'resolved_candidate_set'
                    relation_states[status] += 1
                    source_pointer = pointer + '/' + field + '/' + str(position)
                    relid = ident(run, 'relation', path, source_pointer, relspec['relation_kind'])
                    expected['event71_relation'].append((relid, rid, relspec['relation_kind'], field, source_pointer,
                                                         locator, target_kind, status, len(hits), 1))
                    for target_id, target_path, target_pointer, target_native in hits:
                        expected['event71_candidate'].append((ident(run, 'candidate', relid, target_id), relid,
                                                              target_id, target_path, target_pointer, target_native))
    for table in TABLES:
        expected[table].sort()
    release = {'contract': 'event-native-text-wave-7.1-versioned-evidence-v1',
               'historical_contract': scope['family_contract_version'], 'source_snapshot': scope['snapshot_id'],
               'parser_build_id': parser, 'run_id': run, 'scope_sha256': sha(scope_path),
               'implementation_sha256': sha(ROOT / 'src/genshin_lore/event_native_text_7_1_build.py'),
               'schema_sha256': sha(SCHEMA), 'counts': {table: len(expected[table]) for table in TABLES},
               'physical_rows': sum(len(x) for x in records.values()), 'text_owner_count': owner_count,
               'text_states': dict(sorted(slot_states.items())), 'relation_states': dict(sorted(relation_states.items())),
               'all_textmap_physical_candidates_preserved': True, 'historical_main_before_medium_selection_preserved': True,
               'product_accepted': False}
    return scope, expected, release


def build(source: Path, scope_path: Path, output: Path) -> dict:
    source, scope_path, output = Path(source), Path(scope_path), Path(output)
    scope, expected, release = project(source, scope_path)
    sidecars = [Path(str(output) + suffix) for suffix in ('-wal', '-shm', '-journal')]
    if output.exists() or any(path.exists() for path in sidecars):
        raise FileExistsError('Event 7.1 output/sidecar already exists')
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    owned = output.stat(); db = None
    try:
        db = sqlite3.connect(output)
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(SCHEMA.read_text())
        for table in TABLES:
            if expected[table]:
                db.executemany('INSERT INTO ' + table + ' VALUES(' + ','.join('?' for _ in expected[table][0]) + ')', expected[table])
        db.execute('INSERT INTO event71_release VALUES(1,?)',
                   (json.dumps(release, ensure_ascii=False, sort_keys=True, separators=(',', ':')),))
        db.commit()
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Event 7.1 integrity/FK')
        db.execute('VACUUM')
        db.close(); db = None
        check_files(source, scope)
        return {**release, 'artifact': {'size_bytes': output.stat().st_size, 'sha256': sha(output)}}
    except Exception:
        if db is not None:
            db.close()
        raise
