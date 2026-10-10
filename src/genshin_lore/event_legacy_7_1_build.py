"""Component-10 7.1 versioned physical/text successor; not a product switch."""

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
HISTORICAL = ROOT / 'manifests/event-narrative/scope.json'
TABLES = ('event_narrative_row', 'event_narrative_slot', 'event_narrative_text')


def ident(*parts):
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
    return 'event10-71:' + hashlib.sha256(raw.encode()).hexdigest()[:40]


def project(source_root, scope_path):
    source_root, scope_path = Path(source_root), Path(scope_path)
    scope = json.loads(scope_path.read_text())
    check_files(source_root, scope)
    historical = json.loads(HISTORICAL.read_text())
    if (scope['contract'] != 'event-legacy-7.1-versioned-scope-v1'
            or scope['historical_scope_sha256'] != sha(HISTORICAL)
            or scope['family_contract_version'] != 'event-narrative-evidence-v1'
            or len(scope['sources']) != len(historical['sources']) or len(scope['sources']) != 14
            or {item['path'] for item in scope['sources']} !=
            {item['path'] for item in historical['sources']}
            or scope['languages'] != historical['languages']
            or historical['text_lookup'] !=
            'all physical main candidates first; Medium only when main has no candidate'):
        raise ValueError('component-10 historical selector/contract drift')
    selected = {item['path'] for item in scope['sources']}
    selected |= {f'TextMap/TextMap{suffix}{locale}.json' for locale in scope['languages']
                 for suffix in ('', '_Medium')}
    files = {item['path']: item for item in scope['files']}
    if set(files) != selected or len(files) != 20 or scope['physical_rows'] != 416:
        raise ValueError('component-10 complete 7.1 source selection')
    implementation = {path: sha(ROOT / path) for path in (
        'src/genshin_lore/event_legacy_7_1_build.py',
        'src/genshin_lore/event_7_1_source.py',
        'src/genshin_lore/event_narrative_source.py',
        'schemas/event-successor-7.1-v1.sql')}
    parser = ident('parser', implementation)
    scope_sha = sha(scope_path)
    run = ident('run', parser, scope_sha)
    physical, wanted = {}, set()
    for spec in scope['sources']:
        rows = list(array_records(source_root / spec['path']))
        if len(rows) != scope['row_denominator'][spec['kind']]:
            raise ValueError('component-10 row denominator ' + spec['path'])
        physical[spec['kind']] = rows
        for ordinal, row, _ in rows:
            for field in spec['text_fields']:
                if type(row.get(field)) is not int:
                    raise ValueError(f'component-10 text locator {spec["path"]}/{ordinal}/{field}')
                wanted.add(str(row[field]))
    maps = {}
    for locale in scope['languages']:
        by_tier = []
        for suffix in ('', '_Medium'):
            path = f'TextMap/TextMap{suffix}{locale}.json'
            hits = defaultdict(list)
            for pair, (key, text) in enumerate(pairs(source_root / path)):
                if key in wanted:
                    hits[key].append((path, pair, '/' + key, text))
            by_tier.append(hits)
        maps[locale] = by_tier
    expected = {table: [] for table in TABLES}
    states, roles = Counter(), Counter()
    for spec in scope['sources']:
        path = spec['path']
        native_field = spec.get('native_id_field')
        for ordinal, row, raw in physical[spec['kind']]:
            row_id = ident(run, 'row', path, ordinal)
            native = None if native_field is None or native_field not in row else str(row[native_field])
            seal = files[path]
            expected['event_narrative_row'].append((
                row_id, run, spec['kind'], path, seal['sha256'], seal['blob'], ordinal,
                '/' + str(ordinal), native_field, native, raw,
                hashlib.sha256(raw.encode()).hexdigest()))
            for field in spec['text_fields']:
                locator = str(row[field])
                role = spec.get('owner_roles', {}).get(field, 'resolved')
                roles[role] += len(scope['languages'])
                for locale in scope['languages']:
                    main = maps[locale][0].get(locator, [])
                    medium = maps[locale][1].get(locator, [])
                    hits = main or medium
                    status = ('missing' if not hits else 'ambiguous' if len(hits) != 1
                              else 'resolved_main' if main else 'resolved_medium')
                    states[status] += 1
                    slot = ident(run, 'slot', path, ordinal, field, locale)
                    expected['event_narrative_slot'].append((
                        slot, row_id, field, role, locale, locator, status, len(hits)))
                    for text_path, pair, pointer, text in hits:
                        text_seal = files[text_path]
                        expected['event_narrative_text'].append((
                            ident(run, 'text', path, ordinal, field, locale, text_path, pair),
                            slot, text_path, text_seal['sha256'], text_seal['blob'],
                            pair, pointer, text, hashlib.sha256(text.encode()).hexdigest()))
    for table in TABLES:
        expected[table].sort()
    release = {'contract': scope['family_contract_version'], 'run_id': run,
               'parser_build_id': parser, 'scope_sha256': scope_sha,
               'implementation': implementation, 'snapshot_id': scope['snapshot_id'],
               'counts': {table: len(expected[table]) for table in TABLES},
               'text_state_counts': dict(sorted(states.items())),
               'owner_role_counts': dict(sorted(roles.items())),
               'relation_occurrences': 0, 'full_7_1': False}
    return expected, release


def build(source_root, scope_path, output):
    output = Path(output)
    sidecars = [Path(str(output) + suffix) for suffix in ('-wal', '-shm', '-journal')]
    if output.exists() or any(path.exists() for path in sidecars):
        raise FileExistsError('component-10 output or sidecar exists')
    expected, release = project(source_root, scope_path)
    connection, owned = None, None
    try:
        fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        stat = output.stat()
        owned = (stat.st_dev, stat.st_ino)
        connection = sqlite3.connect(output)
        connection.execute('PRAGMA foreign_keys=ON')
        connection.executescript(SCHEMA.read_text())
        for table in TABLES:
            if expected[table]:
                connection.executemany('INSERT INTO ' + table + ' VALUES(' +
                                       ','.join('?' for _ in expected[table][0]) + ')',
                                       expected[table])
        connection.execute('INSERT INTO event_narrative_release VALUES(1,?)',
                           (json.dumps(release, sort_keys=True, separators=(',', ':')),))
        connection.commit()
        connection.execute('VACUUM')
        connection.close()
        connection = None
        if any(path.exists() for path in sidecars):
            raise ValueError('component-10 SQLite sidecar')
        logical = {table: hashlib.sha256('\n'.join(json.dumps(list(row), ensure_ascii=False,
                           separators=(',', ':')) for row in expected[table]).encode()).hexdigest()
                   for table in TABLES}
        return {**release,
                'artifact': {'size_bytes': output.stat().st_size, 'sha256': sha(output)},
                'logical_seals': logical}
    except Exception:
        if connection is not None:
            connection.close()
        if owned is not None and output.exists():
            stat = output.stat()
            if (stat.st_dev, stat.st_ino) == owned:
                output.unlink()
        raise
