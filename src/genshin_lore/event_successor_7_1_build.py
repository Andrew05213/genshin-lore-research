"""Component-11 versioned 7.1 physical Event successor, with no logical Event identity."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .event_7_1_source import array_records, check_files, pairs, sha

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / 'schemas/event-successor-7.1-v1.sql'
HISTORICAL = ROOT / 'manifests/event-narrative/successor-scope.json'
TABLES = ('event_narrative_row', 'event_narrative_slot', 'event_narrative_text')


def ident(*parts):
    payload = json.dumps(parts, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
    return 'event11-71:' + hashlib.sha256(payload.encode()).hexdigest()[:40]


def identities(scope_path):
    implementation = {name: sha(ROOT / name) for name in (
        'src/genshin_lore/event_successor_7_1_build.py',
        'src/genshin_lore/event_7_1_source.py',
        'schemas/event-successor-7.1-v1.sql')}
    parser = ident('parser', implementation)
    scope_sha = sha(scope_path)
    return implementation, parser, scope_sha, ident('run', parser, scope_sha)


def project(source_root, scope_path):
    source_root, scope_path = Path(source_root), Path(scope_path)
    scope = json.loads(scope_path.read_text())
    check_files(source_root, scope)
    historical = json.loads(HISTORICAL.read_text())
    if (scope['contract'] != 'event-successor-7.1-versioned-scope-v1'
            or scope['historical_scope'] != str(HISTORICAL.relative_to(ROOT))
            or scope['historical_scope_sha256'] != sha(HISTORICAL)
            or scope['family_contract_version'] != 'event-narrative-evidence-v2'
            or historical['text_lookup'] != 'all physical main candidates first; Medium only when main has no candidate'
            or len(scope['sources']) != 6 or len(historical['sources']) != 6
            or scope['languages'] != historical['languages']
            or {item['path'] for item in scope['sources']} != {item['path'] for item in historical['sources']}
            or len(scope['files']) != 12):
        raise ValueError('Event component-11 exact selection/contract mismatch')
    seals = {entry['path']: entry for entry in scope['files']}
    expected_paths = {item['path'] for item in scope['sources']}
    expected_paths |= {f'TextMap/TextMap{suffix}{locale}.json' for locale in scope['languages']
                       for suffix in ('', '_Medium')}
    if set(seals) != expected_paths:
        raise ValueError('Event selected file set mismatch')
    implementation, parser, scope_sha, run = identities(scope_path)
    rows = {}
    wanted = set()
    for item in scope['sources']:
        physical = list(array_records(source_root / item['path']))
        if len(physical) != scope['row_denominator'][item['kind']]:
            raise ValueError('Event source row denominator ' + item['path'])
        rows[item['kind']] = physical
        for ordinal, record, _ in physical:
            if not isinstance(record, dict):
                raise ValueError('non-object Event source row')
            for field in item['text_fields']:
                value = record.get(field)
                if type(value) is not int:
                    raise ValueError(f'Event text-owner type {item["path"]}/{ordinal}/{field}')
                wanted.add(str(value))
    maps = {}
    for locale in scope['languages']:
        by_tier = []
        for suffix in ('', '_Medium'):
            path = f'TextMap/TextMap{suffix}{locale}.json'
            found = defaultdict(list)
            for ordinal, (key, text) in enumerate(pairs(source_root / path)):
                if key in wanted:
                    found[key].append((path, ordinal, '/' + key, text))
            by_tier.append(found)
        maps[locale] = by_tier
    expected = {table: [] for table in TABLES}
    states, roles = Counter(), Counter()
    for item in scope['sources']:
        path = item['path']
        for ordinal, record, raw in rows[item['kind']]:
            pointer = '/' + str(ordinal)
            row_id = ident(run, 'row', path, ordinal)
            native_field = item.get('native_id_field')
            native_id = None if native_field is None else str(record[native_field])
            file_seal = seals[path]
            expected['event_narrative_row'].append((
                row_id, run, item['kind'], path, file_seal['sha256'], file_seal['blob'], ordinal,
                pointer, native_field, native_id, raw, hashlib.sha256(raw.encode()).hexdigest()))
            for field in item['text_fields']:
                role = item.get('owner_roles', {}).get(field, 'resolved')
                locator = str(record[field])
                roles[role] += len(scope['languages'])
                for locale in scope['languages']:
                    main = maps[locale][0].get(locator, [])
                    medium = maps[locale][1].get(locator, [])
                    hits = main or medium
                    status = ('missing' if not hits else 'ambiguous' if len(hits) > 1
                              else 'resolved_main' if main else 'resolved_medium')
                    states[status] += 1
                    slot_id = ident(run, 'slot', path, ordinal, field, locale)
                    expected['event_narrative_slot'].append((
                        slot_id, row_id, field, role, locale, locator, status, len(hits)))
                    for text_path, pair, text_pointer, text in hits:
                        text_seal = seals[text_path]
                        expected['event_narrative_text'].append((
                            ident(run, 'text', path, ordinal, field, locale, text_path, pair),
                            slot_id, text_path, text_seal['sha256'], text_seal['blob'], pair,
                            text_pointer, text, hashlib.sha256(text.encode()).hexdigest()))
    for table in TABLES:
        expected[table].sort()
    counts = {table: len(expected[table]) for table in TABLES}
    release = {'contract': scope['family_contract_version'], 'run_id': run, 'parser_build_id': parser,
               'scope_sha256': scope_sha, 'implementation': implementation,
               'snapshot_id': scope['snapshot_id'], 'counts': counts,
               'text_state_counts': dict(sorted(states.items())),
               'owner_role_counts': dict(sorted(roles.items())), 'relation_occurrences': 0,
               'full_7_1': False}
    return expected, release


def build(source_root, scope_path, output):
    output = Path(output)
    sidecars = [Path(str(output) + suffix) for suffix in ('-wal', '-shm', '-journal')]
    if output.exists() or any(path.exists() for path in sidecars):
        raise FileExistsError('Event output or sidecar exists')
    expected, release = project(source_root, scope_path)
    owned = None
    connection = None
    try:
        descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        stat = output.stat()
        owned = (stat.st_dev, stat.st_ino)
        connection = sqlite3.connect(output)
        connection.execute('PRAGMA foreign_keys=ON')
        connection.executescript(SCHEMA.read_text())
        for table in TABLES:
            if expected[table]:
                connection.executemany('INSERT INTO ' + table + ' VALUES (' +
                                       ','.join('?' for _ in expected[table][0]) + ')', expected[table])
        connection.execute('INSERT INTO event_narrative_release VALUES(1,?)',
                           (json.dumps(release, sort_keys=True, separators=(',', ':')),))
        connection.commit()
        connection.execute('VACUUM')
        connection.close()
        connection = None
        if any(path.exists() for path in sidecars):
            raise ValueError('Event output sidecar')
        logical = {table: hashlib.sha256('\n'.join(json.dumps(list(row), ensure_ascii=False,
                            separators=(',', ':')) for row in expected[table]).encode()).hexdigest()
                   for table in TABLES}
        return {**release, 'artifact': {'size_bytes': output.stat().st_size, 'sha256': sha(output)},
                'logical_seals': logical}
    except Exception:
        if connection is not None:
            connection.close()
        if owned is not None and output.exists():
            stat = output.stat()
            if (stat.st_dev, stat.st_ino) == owned:
                output.unlink()
        raise
