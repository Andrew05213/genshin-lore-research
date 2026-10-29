"""Independent source-to-rowset oracle for Event13's raw physical candidate."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from .event_7_1_source import check_files, sha

REPO = Path(__file__).resolve().parents[2]
SCHEMA = REPO / 'schemas/event13-7.1-physical-v1.sql'
TABLES = ('physical_row', 'numeric_observation', 'textmap_candidate')


def source_array(path):
    """Independent lexical record boundaries; does not invoke builder reader."""
    body = Path(path).read_bytes().decode('utf-8')
    decoder = json.JSONDecoder()
    position = 0
    while position < len(body) and body[position] in ' \t\r\n':
        position += 1
    if position >= len(body) or body[position] != '[':
        raise ValueError('oracle source array required')
    position += 1
    ordinal = 0
    while True:
        while position < len(body) and body[position] in ' \t\r\n':
            position += 1
        if position >= len(body):
            raise ValueError('oracle truncated array')
        if body[position] == ']':
            if body[position + 1:].strip():
                raise ValueError('oracle trailing bytes')
            return
        start = position
        value, position = decoder.raw_decode(body, position)
        yield ordinal, value, body[start:position]
        ordinal += 1
        while position < len(body) and body[position] in ' \t\r\n':
            position += 1
        if position >= len(body):
            raise ValueError('oracle truncated separator')
        if body[position] == ',':
            position += 1
        elif body[position] != ']':
            raise ValueError('oracle invalid separator')


def textmap_pairs(path):
    object_pairs = json.loads(Path(path).read_bytes().decode('utf-8'),
                              object_pairs_hook=lambda pairs: pairs)
    if not isinstance(object_pairs, list):
        raise ValueError('oracle TextMap object')
    for pair in object_pairs:
        if (not isinstance(pair, tuple) or len(pair) != 2
                or not isinstance(pair[0], str) or not isinstance(pair[1], str)):
            raise ValueError('oracle TextMap string pairs')
        yield pair


def seal_id(*parts):
    payload = json.dumps(parts, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    return 'event13-71:' + hashlib.sha256(payload.encode()).hexdigest()[:40]


def visit_numbers(obj, prefix):
    if isinstance(obj, bool):
        return
    if isinstance(obj, int):
        yield prefix, str(obj)
    elif isinstance(obj, list):
        for index in range(len(obj)):
            yield from visit_numbers(obj[index], prefix + '/' + str(index))
    elif isinstance(obj, dict):
        for name in obj:
            escaped = name.replace('~', '~0').replace('/', '~1')
            yield from visit_numbers(obj[name], prefix + '/' + escaped)


def schema_objects(connection):
    return sorted(connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
    ).fetchall())


def expect_immutable(connection, statement, params):
    try:
        connection.execute(statement, params)
    except sqlite3.DatabaseError as exc:
        if 'immutable' in str(exc):
            connection.rollback()
            return
        raise AssertionError('wrong rejection cause: ' + str(exc)) from exc
    connection.rollback()
    raise AssertionError('write-once mutation accepted: ' + statement)


def mutation_matrix(connection):
    fixture = sqlite3.connect(':memory:')
    connection.backup(fixture)
    fixture.execute('PRAGMA foreign_keys=OFF')
    cases = 0
    for recursive in ('OFF', 'ON'):
        fixture.execute('PRAGMA recursive_triggers=' + recursive)
        for table in (*TABLES, 'release'):
            columns = [item[1] for item in fixture.execute('PRAGMA table_info(' + table + ')')]
            existing = list(fixture.execute('SELECT * FROM ' + table + ' LIMIT 1').fetchone())
            if not existing:
                raise AssertionError('empty immutable table: ' + table)
            fields = ','.join(columns)
            marks = ','.join('?' for _ in columns)
            # New primary key and PK collision exercise the direct INSERT and REPLACE paths.
            fresh = existing.copy()
            fresh[0] = '__mutation_new_id__' if table != 'release' else 2
            for verb, values in (('INSERT', fresh), ('INSERT OR REPLACE', existing),
                                 ('INSERT OR REPLACE', fresh)):
                expect_immutable(fixture, f'{verb} INTO {table}({fields}) VALUES({marks})', values)
                cases += 1
            expect_immutable(fixture, f'UPDATE {table} SET {columns[0]}=? WHERE {columns[0]}=?',
                             (existing[0], existing[0])); cases += 1
            expect_immutable(fixture, f'DELETE FROM {table} WHERE {columns[0]}=?',
                             (existing[0],)); cases += 1
            expect_immutable(fixture, f'INSERT INTO {table}({fields}) VALUES({marks}) '
                             f'ON CONFLICT({columns[0]}) DO UPDATE SET {columns[0]}=excluded.{columns[0]}',
                             existing); cases += 1
            if table != 'release':
                # PK is fresh, independent (source coordinate/owner coordinate) UNIQUE is old.
                expect_immutable(fixture, f'INSERT OR REPLACE INTO {table}({fields}) VALUES({marks})', fresh)
                cases += 1
    fixture.close()
    return cases


def verify(root: Path, scope_path: Path, artifact: Path):
    root, scope_path, artifact = Path(root), Path(scope_path), Path(artifact)
    scope = json.loads(scope_path.read_text())
    check_files(root, scope)
    if scope['contract'] != 'event13-7.1-physical-capability-v1' or len(scope['sources']) != 30:
        raise ValueError('Event13 oracle scope')
    historical = json.loads((REPO / 'manifests/event-narrative/archive-wave-scope.json').read_text())
    if scope['historical_scope_sha256'] != sha(REPO / 'manifests/event-narrative/archive-wave-scope.json'):
        raise ValueError('Event13 oracle historical scope seal')
    if [(item['boundary'], item['kind'], item['path']) for item in scope['sources']] != [
            (item['boundary'], item['kind'], item['path']) for item in historical['sources']]:
        raise ValueError('Event13 oracle historical selector')
    expected_paths = [entry['path'] for entry in historical['sources']]
    expected_paths.extend(f'TextMap/TextMap{suffix}{locale}.json'
                          for locale in ('CHS', 'EN', 'JP') for suffix in ('', '_Medium'))
    if [entry['path'] for entry in scope['files']] != expected_paths:
        raise ValueError('Event13 oracle selected file set')
    if artifact.stat().st_size == 0 or any(Path(str(artifact) + ending).exists()
                                             for ending in ('-wal', '-shm', '-journal')):
        raise ValueError('Event13 artifact/sidecar')
    source_files = {entry['path']: entry for entry in scope['files']}
    connection = sqlite3.connect('file:' + str(artifact.resolve()) + '?mode=ro&immutable=1', uri=True)
    memory = sqlite3.connect(':memory:')
    memory.executescript(SCHEMA.read_text())
    if schema_objects(connection) != schema_objects(memory):
        raise ValueError('Event13 schema/index/trigger definition drift')
    memory.close()
    if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('Event13 integrity_check')
    if connection.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Event13 foreign_key_check')
    release_text = connection.execute('SELECT manifest_json FROM release WHERE id=1').fetchone()[0]
    release = json.loads(release_text)
    if (release['scope_sha256'] != sha(scope_path) or release['snapshot'] != scope['snapshot_id']
            or release['contract'] != scope['contract'] or not release['semantic_owner_mapping_unresolved']
            or release['numeric_observation_scope'] !=
            'integer_scalar_leaves_only; nonintegral numbers remain in raw_json'):
        raise ValueError('Event13 release binding')
    parser = seal_id('parser', release['implementation'])
    run = seal_id('run', parser, sha(scope_path), scope['snapshot_id'])
    if parser != release['parser_build_id'] or run != release['ingest_run_id']:
        raise ValueError('Event13 parser/run derivation')
    if set(release['implementation']) != {
            'schemas/event13-7.1-physical-v1.sql',
            'src/genshin_lore/event13_7_1_physical.py',
            'src/genshin_lore/event_7_1_source.py',
            'src/genshin_lore/event_narrative_source.py'}:
        raise ValueError('Event13 parser implementation dependency set')
    for relpath, digest in release['implementation'].items():
        if sha(REPO / relpath) != digest:
            raise ValueError('Event13 implementation source drift: ' + relpath)

    expected_rows = []
    expected_numeric = []
    needed_hashes = set()
    for entry in scope['sources']:
        path = entry['path']
        seen = 0
        for ordinal, obj, raw in source_array(root / path):
            seen += 1
            if not isinstance(obj, dict):
                raise ValueError('Event13 source row shape')
            pointer = '/' + str(ordinal)
            raw_digest = hashlib.sha256(raw.encode()).hexdigest()
            stamp = source_files[path]
            occurrence = seal_id(run, path, ordinal)
            expected_rows.append((occurrence, run, entry['boundary'], path, stamp['sha256'],
                                  stamp['blob'], ordinal, pointer, raw, raw_digest))
            for field_pointer, number in visit_numbers(obj, pointer):
                observation = seal_id(run, path, field_pointer)
                expected_numeric.append((observation, occurrence, field_pointer, number,
                                         'uninterpreted_raw_numeric'))
                needed_hashes.add(number)
        if seen != entry['row_count']:
            raise ValueError('Event13 independent denominator: ' + path)

    text_by_locale_tier = {}
    for locale in ('CHS', 'EN', 'JP'):
        for tier in ('main', 'Medium'):
            suffix = '' if tier == 'main' else '_Medium'
            path = f'TextMap/TextMap{suffix}{locale}.json'
            matches = defaultdict(list)
            for index, item in enumerate(textmap_pairs(root / path)):
                key, value = item
                if key in needed_hashes:
                    matches[key].append((index, value))
            text_by_locale_tier[locale, tier] = (path, matches)
    expected_candidates = []
    for observation, _, _, number, _ in expected_numeric:
        for locale in ('CHS', 'EN', 'JP'):
            for tier in ('main', 'Medium'):
                path, matches = text_by_locale_tier[locale, tier]
                stamp = source_files[path]
                for index, text in matches.get(number, ()):
                    expected_candidates.append((seal_id(run, observation, locale, tier, path, index),
                                                observation, locale, tier, path, stamp['sha256'],
                                                stamp['blob'], index, '/' + number, text,
                                                hashlib.sha256(text.encode()).hexdigest()))
    for table, expected in zip(TABLES, (expected_rows, expected_numeric, expected_candidates)):
        columns = [item[1] for item in connection.execute('PRAGMA table_info(' + table + ')')]
        actual = connection.execute('SELECT ' + ','.join(columns) + ' FROM ' + table).fetchall()
        if sorted(actual) != sorted(expected):
            raise ValueError('Event13 source-to-rowset mismatch: ' + table)
    if any(release[key] != value for key, value in (
        ('physical_rows', len(expected_rows)), ('numeric_observations', len(expected_numeric)),
        ('textmap_candidates', len(expected_candidates)), ('source_files', len(scope['files']))
    )):
        raise ValueError('Event13 self-reported counts')
    mutation_cases = mutation_matrix(connection)
    connection.close()
    return {'status': 'PASS', 'verified': True, 'source_files': len(scope['files']), 'physical_rows': len(expected_rows),
            'numeric_observations': len(expected_numeric), 'textmap_candidates': len(expected_candidates),
            'mutation_cases': mutation_cases, 'parser_build_id': parser, 'ingest_run_id': run,
            'artifact': {'size_bytes': artifact.stat().st_size, 'sha256': sha(artifact)}}
