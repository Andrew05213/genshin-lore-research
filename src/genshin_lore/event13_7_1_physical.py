"""Write-once 7.1 Event13 physical capability; no obfuscated-field semantics."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

from .batch_release import assert_output_paths, create_private_staging, publish_absent, sidecars
from .event_7_1_source import array_records, check_files, pairs, sha

REPO = Path(__file__).resolve().parents[2]
SCHEMA = REPO / 'schemas/event13-7.1-physical-v1.sql'
HISTORICAL = REPO / 'manifests/event-narrative/archive-wave-scope.json'
TABLES = ('physical_row', 'numeric_observation', 'textmap_candidate')


def identity(*parts: object) -> str:
    data = json.dumps(parts, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()
    return 'event13-71:' + hashlib.sha256(data).hexdigest()[:40]


def numeric_leaves(value: object, pointer: str):
    if type(value) is int:
        yield pointer, str(value)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from numeric_leaves(child, pointer + '/' + str(i))
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from numeric_leaves(child, pointer + '/' + key.replace('~', '~0').replace('/', '~1'))


def projection(root: Path, scope_path: Path):
    scope = json.loads(scope_path.read_text())
    check_files(root, scope)
    history = json.loads(HISTORICAL.read_text())
    historic_descriptors = [(item['boundary'], item['kind'], item['path'])
                            for item in history['sources']]
    actual_descriptors = [(item['boundary'], item['kind'], item['path'])
                          for item in scope['sources']]
    expected_paths = [item['path'] for item in history['sources']]
    expected_paths += [f'TextMap/TextMap{tier}{locale}.json'
                       for locale in ('CHS', 'EN', 'JP') for tier in ('', '_Medium')]
    if (scope['contract'] != 'event13-7.1-physical-capability-v1'
            or scope['historical_scope_sha256'] != sha(HISTORICAL)
            or actual_descriptors != historic_descriptors
            or [item['path'] for item in scope['files']] != expected_paths
            or len(scope['sources']) != 30):
        raise ValueError('Event13 historical source selection drift')
    implementation = {path.relative_to(REPO).as_posix(): sha(path) for path in
                      (Path(__file__), SCHEMA, REPO / 'src/genshin_lore/event_7_1_source.py',
                       REPO / 'src/genshin_lore/event_narrative_source.py')}
    parser = identity('parser', implementation)
    run = identity('run', parser, sha(scope_path), scope['snapshot_id'])
    files = {item['path']: item for item in scope['files']}
    rows = []
    slots = []
    needed = set()
    for source in scope['sources']:
        path = source['path']
        parsed = list(array_records(root / path))
        if len(parsed) != source['row_count']:
            raise ValueError('Event13 physical denominator drift: ' + path)
        for ordinal, row, raw in parsed:
            if not isinstance(row, dict):
                raise ValueError('Event13 physical row must be object: ' + path)
            row_id = identity(run, path, ordinal)
            seal = files[path]
            rows.append((row_id, run, source['boundary'], path, seal['sha256'],
                         seal['blob'], ordinal, '/' + str(ordinal), raw,
                         hashlib.sha256(raw.encode()).hexdigest()))
            for pointer, value in numeric_leaves(row, '/' + str(ordinal)):
                observation = identity(run, path, pointer)
                slots.append((observation, row_id, pointer, value, 'uninterpreted_raw_numeric'))
                needed.add(value)
    maps = {}
    for locale in ('CHS', 'EN', 'JP'):
        for tier, suffix in (('main', ''), ('Medium', '_Medium')):
            path = f'TextMap/TextMap{suffix}{locale}.json'
            found = defaultdict(list)
            for pair_ordinal, (key, text) in enumerate(pairs(root / path)):
                if key in needed:
                    found[key].append((pair_ordinal, text))
            maps[locale, tier] = (path, found)
    candidates = []
    for observation, _, _, value, _ in slots:
        for locale in ('CHS', 'EN', 'JP'):
            for tier in ('main', 'Medium'):
                path, found = maps[locale, tier]
                for pair_ordinal, text in found.get(value, ()):
                    seal = files[path]
                    candidates.append((identity(run, observation, locale, tier, path, pair_ordinal),
                                       observation, locale, tier, path, seal['sha256'], seal['blob'],
                                       pair_ordinal, '/' + value, text,
                                       hashlib.sha256(text.encode()).hexdigest()))
    release = {
        'contract': scope['contract'], 'snapshot': scope['snapshot_id'],
        'parser_build_id': parser, 'ingest_run_id': run,
        'scope_sha256': sha(scope_path), 'implementation': implementation,
        'source_files': len(scope['files']), 'physical_rows': len(rows),
        'numeric_observations': len(slots), 'textmap_candidates': len(candidates),
        'semantic_owner_mapping_unresolved': True,
        'numeric_observation_scope': 'integer_scalar_leaves_only; nonintegral numbers remain in raw_json',
        'no_historical_exact_replacement_claim': True,
    }
    return scope, {'physical_row': rows, 'numeric_observation': slots,
                   'textmap_candidate': candidates}, release


def build(root: Path, scope_path: Path, output: Path):
    root, scope_path, output = Path(root).resolve(), Path(scope_path).resolve(), Path(output).absolute()
    scope = json.loads(scope_path.read_text())
    inputs = [('source_root', root), ('scope', scope_path), ('schema', SCHEMA), ('history', HISTORICAL)]
    inputs += [(str(i), root / item['path']) for i, item in enumerate(scope['files'])]
    assert_output_paths(output, inputs=inputs)
    _, expected, release = projection(root, scope_path)
    fd, stage = create_private_staging(output, prefix='.event13-71-')
    inode = (stage.stat().st_dev, stage.stat().st_ino)
    connection = None
    try:
        os.close(fd)
        connection = sqlite3.connect(stage)
        connection.execute('PRAGMA foreign_keys=ON')
        connection.executescript(SCHEMA.read_text())
        for table in TABLES:
            rowset = expected[table]
            if rowset:
                connection.executemany('INSERT INTO ' + table + ' VALUES (' +
                                       ','.join('?' for _ in rowset[0]) + ')', rowset)
        connection.execute('INSERT INTO release VALUES(1,?)',
                           (json.dumps(release, sort_keys=True, separators=(',', ':')),))
        connection.commit()
        connection.execute('VACUUM')
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Event13 SQLite integrity')
        if connection.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Event13 SQLite foreign key')
        connection.close()
        connection = None
        if any(p.exists() for p in sidecars(stage)):
            raise ValueError('Event13 staging SQLite sidecar')
        check_files(root, scope)
        seal = {'size_bytes': stage.stat().st_size, 'sha256': sha(stage)}
        publish_absent(output, stage, inputs=inputs)
        if output.stat().st_size != seal['size_bytes'] or sha(output) != seal['sha256']:
            raise ValueError('Event13 published artifact seal drift')
        return {**release, 'artifact': seal}
    finally:
        if connection is not None:
            connection.close()
