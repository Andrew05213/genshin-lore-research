"""Versioned Filmfest Shooting v3 projection over exact 7.1 physical evidence."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .event_7_1_source import array_records, check_files, pairs, sha

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / 'schemas/event-filmfest-7.1-v1.sql'
HISTORICAL = ROOT / 'manifests/event-narrative/filmfest-scope.json'
QT_MANIFEST = ROOT / 'manifests/quest-talk-7.1/standalone-candidate.json'
TABLES = ('event_narrative_row', 'event_narrative_slot', 'event_narrative_text',
          'event_narrative_relation', 'event_narrative_relation_candidate')


def ident(*parts):
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
    return 'filmfest71:' + hashlib.sha256(raw.encode()).hexdigest()[:40]


def _owners(row, specs):
    for spec in specs:
        field = spec['field']
        value = row.get(field)
        if spec['shape'] == 'scalar':
            if type(value) is not int:
                raise ValueError('Filmfest scalar owner shape ' + field)
            yield field, value, spec['role_status']
        elif spec['shape'] == 'array':
            if not isinstance(value, list) or any(type(item) is not int for item in value):
                raise ValueError('Filmfest array owner shape ' + field)
            for index, item in enumerate(value):
                yield f'{field}/{index}', item, spec['role_status']
        else:
            raise ValueError('Filmfest undeclared owner shape')


def discover_target_candidates(source_root, qt_artifact, scope, *, qt_manifest_path=None):
    """Derive a new scope's targets from its explicitly bound Quest/Talk input."""
    manifest_path = Path(qt_manifest_path) if qt_manifest_path is not None else QT_MANIFEST
    accepted = json.loads(manifest_path.read_text())
    binding = scope['talk_candidate_namespace']
    if (binding['accepted_manifest_sha256'] != sha(manifest_path)
            or binding['artifact'] != accepted['artifact']
            or binding['contract'] != accepted['contract']
            or binding['parser'] != accepted['parser_build_id']
            or binding['run'] != accepted['ingest_run_id']
            or binding['search_complete'] is not True
            or Path(qt_artifact).stat().st_size != accepted['artifact']['size_bytes']
            or sha(qt_artifact) != accepted['artifact']['sha256']):
        raise ValueError('Filmfest accepted 7.1 Quest/Talk artifact binding')
    db = sqlite3.connect(Path(qt_artifact).absolute().as_uri() + '?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    levels = next(spec for spec in scope['sources'] if spec['kind'] == 'filmfest_shooting_level')
    wanted = {str(row[levels['talk_relation_field']])
              for _, row, _ in array_records(source_root / levels['path'])}
    candidates = []
    for native in sorted(wanted):
        for target in db.execute(
                'SELECT id,ingest_run_id,source_path,pointer,native_id,raw_sha256 '
                'FROM physical_container WHERE kind=? AND native_id=? ORDER BY source_path,pointer,id',
                ('talk_root', native)):
            item = dict(target)
            source = db.execute('SELECT raw_bytes,sha256 FROM source_file WHERE path=?',
                                (item['source_path'],)).fetchone()
            if (source is None or item['ingest_run_id'] != accepted['ingest_run_id']
                    or item['pointer'] != '/' or item['native_id'] != native
                    or source['sha256'] != sha(source_root / item['source_path'])
                    or source['raw_bytes'] != (source_root / item['source_path']).read_bytes()
                    or item['raw_sha256'] != hashlib.sha256(source['raw_bytes'].strip()).hexdigest()):
                raise ValueError('Filmfest external Talk second-hop mismatch ' + native)
            candidates.append({'raw_locator': native, 'target_source_path': item['source_path'],
                               'target_source_pointer': item['pointer'], 'target_native_id': native,
                               'target_raw_sha256': item['raw_sha256'],
                               'target_family': 'quest_talk_physical',
                               'target_contract': accepted['contract'],
                               'target_parser_build_id': accepted['parser_build_id'],
                               'target_run_id': accepted['ingest_run_id'],
                               'target_occurrence_id': item['id']})
    db.close()
    return candidates


def _target_candidates(source_root, qt_artifact, scope, *, qt_manifest_path=None):
    candidates = discover_target_candidates(
        source_root, qt_artifact, scope, qt_manifest_path=qt_manifest_path)
    if candidates != scope['talk_candidates']:
        raise ValueError('Filmfest complete accepted Talk candidate set drift')
    return candidates


def project(source_root, scope_path, qt_artifact, *, qt_manifest_path=None):
    source_root, scope_path = Path(source_root), Path(scope_path)
    scope = json.loads(scope_path.read_text())
    check_files(source_root, scope)
    historical = json.loads(HISTORICAL.read_text())
    if (scope['contract'] != 'event-filmfest-7.1-versioned-scope-v1'
            or scope['historical_scope_sha256'] != sha(HISTORICAL)
            or scope['family_contract_version'] != historical['family_contract_version']
            or len(scope['sources']) != len(historical['sources']) or len(scope['sources']) != 4
            or scope['languages'] != historical['languages']
            or {s['path'] for s in scope['sources']} != {s['path'] for s in historical['sources']}
            or len(scope['files']) != 15):
        raise ValueError('Filmfest exact historical source boundary')
    candidates = _target_candidates(source_root, qt_artifact, scope,
                                    qt_manifest_path=qt_manifest_path)
    implementation = {name: sha(ROOT / name) for name in (
        'src/genshin_lore/event_filmfest_7_1_build.py',
        'src/genshin_lore/event_7_1_source.py',
        'src/genshin_lore/event_narrative_source.py',
        'schemas/event-filmfest-7.1-v1.sql')}
    parser = ident('parser', implementation)
    scope_sha = sha(scope_path)
    run = ident('run', parser, scope_sha)
    files = {item['path']: item for item in scope['files']}
    records = {spec['kind']: list(array_records(source_root / spec['path']))
               for spec in scope['sources']}
    if {kind: len(values) for kind, values in records.items()} != scope['row_denominator']:
        raise ValueError('Filmfest complete physical denominator')
    wanted = {str(value) for spec in scope['sources']
              for _, row, _ in records[spec['kind']]
              for _, value, _ in _owners(row, spec['text_owners'])}
    textmap = {}
    for locale in scope['languages']:
        tiers = []
        for tier, suffix in (('main', ''), ('medium', '_Medium')):
            path = f'TextMap/TextMap{suffix}{locale}.json'
            lookup = defaultdict(list)
            for ordinal, (key, text) in enumerate(pairs(source_root / path)):
                if key in wanted:
                    lookup[key].append((path, ordinal, '/' + key, text))
            tiers.append(lookup)
        textmap[locale] = tiers
    expected = {table: [] for table in TABLES}
    states, roles, relation_states = Counter(), Counter(), Counter()
    row_ids, lookup = {}, defaultdict(list)
    for spec in scope['sources']:
        for ordinal, row, raw in records[spec['kind']]:
            path = spec['path']
            native_field = spec['native_id_field']
            native = str(row[native_field])
            row_id = ident(run, 'row', path, ordinal)
            row_ids[(spec['kind'], ordinal)] = row_id
            lookup[(spec['kind'], native)].append((path, '/' + str(ordinal), row_id))
            seal = files[path]
            expected['event_narrative_row'].append((
                row_id, run, spec['boundary'], path, seal['sha256'], seal['blob'], ordinal,
                '/' + str(ordinal), native_field, native, raw, hashlib.sha256(raw.encode()).hexdigest()))
    talk_lookup = defaultdict(list)
    for item in candidates:
        talk_lookup[item['raw_locator']].append(item)
    for spec in scope['sources']:
        path = spec['path']
        for ordinal, row, _ in records[spec['kind']]:
            row_id = row_ids[(spec['kind'], ordinal)]
            for field, value, role in _owners(row, spec['text_owners']):
                roles[role] += len(scope['languages'])
                locator = str(value)
                for locale in scope['languages']:
                    main = textmap[locale][0].get(locator, [])
                    medium = textmap[locale][1].get(locator, [])
                    hits = main or medium
                    status = ('missing' if not hits else 'ambiguous' if len(hits) != 1
                              else 'resolved_main' if main else 'resolved_medium')
                    states[status] += 1
                    slot_id = ident(run, 'slot', path, ordinal, field, locale)
                    expected['event_narrative_slot'].append((
                        slot_id, row_id, field, role, locale, locator, status, len(hits)))
                    for text_path, pair, pointer, text in hits:
                        seal = files[text_path]
                        expected['event_narrative_text'].append((
                            ident(run, 'text', path, ordinal, field, locale, text_path, pair),
                            slot_id, text_path, seal['sha256'], seal['blob'], pair, pointer,
                            text, hashlib.sha256(text.encode()).hexdigest()))
            for relation in spec.get('internal_relations', []):
                field = relation['field']
                values = row.get(field)
                if not isinstance(values, list):
                    raise ValueError('Filmfest internal relation list shape')
                for position, value in enumerate(values):
                    if relation['shape'] == 'array_scalar':
                        locator = value
                    elif relation['shape'] in ('array_object_id', 'array_object_id_optional'):
                        if not isinstance(value, dict):
                            raise ValueError('Filmfest internal object relation shape')
                        if 'id' not in value:
                            if relation['shape'] == 'array_object_id_optional':
                                continue
                            raise ValueError('Filmfest required internal relation id')
                        locator = value['id']
                    else:
                        raise ValueError('Filmfest undeclared relation shape')
                    if type(locator) is not int:
                        raise ValueError('Filmfest relation scalar shape')
                    hits = sorted(lookup[(relation['target_kind'], str(locator))])
                    status = 'missing' if not hits else 'resolved_unique' if len(hits) == 1 else 'ambiguous'
                    relation_states[status] += 1
                    pointer = f'/{ordinal}/{field}/{position}'
                    relation_id = ident(run, 'relation', path, pointer, relation['relation_kind'])
                    expected['event_narrative_relation'].append((
                        relation_id, row_id, relation['relation_kind'], field, pointer, str(locator),
                        'filmfest_physical_rows:' + relation['target_kind'], status, len(hits), 1))
                    for target_path, target_pointer, target_id in hits:
                        expected['event_narrative_relation_candidate'].append((
                            ident(run, 'candidate', relation_id, target_path, run, target_id),
                            relation_id, target_path, target_pointer, str(locator),
                            'event_narrative_archive', scope['family_contract_version'],
                            parser, run, target_id))
            if spec.get('talk_relation_field'):
                field = spec['talk_relation_field']
                raw = row.get(field)
                if type(raw) is not int:
                    raise ValueError('Filmfest Talk reference scalar')
                locator = str(raw)
                hits = sorted(talk_lookup[locator], key=lambda item: (
                    item['target_source_path'], item['target_source_pointer'],
                    item['target_run_id'], item['target_occurrence_id']))
                status = 'missing' if not hits else 'resolved_unique' if len(hits) == 1 else 'ambiguous'
                relation_states[status] += 1
                relation_id = ident(run, 'relation', path, ordinal, field)
                expected['event_narrative_relation'].append((
                    relation_id, row_id, 'source_authored_talk_reference', field,
                    '/' + str(ordinal) + '/' + field, locator,
                    'accepted_full_7_1_quest_talk_physical_roots', status, len(hits), 1))
                for target in hits:
                    expected['event_narrative_relation_candidate'].append((
                        ident(run, 'candidate', relation_id, target['target_source_path'],
                              target['target_run_id'], target['target_occurrence_id']),
                        relation_id, target['target_source_path'], target['target_source_pointer'],
                        locator, target['target_family'], target['target_contract'],
                        target['target_parser_build_id'], target['target_run_id'],
                        target['target_occurrence_id']))
    for table in TABLES:
        expected[table].sort()
    counts = {table: len(expected[table]) for table in TABLES}
    release = {'contract': scope['family_contract_version'], 'run_id': run,
               'parser_build_id': parser, 'scope_sha256': scope_sha,
               'implementation': implementation, 'snapshot_id': scope['snapshot_id'],
               'external_quest_talk': scope['talk_candidate_namespace'],
               'counts': counts, 'text_state_counts': dict(sorted(states.items())),
               'owner_role_counts': dict(sorted(roles.items())),
               'relation_state_counts': dict(sorted(relation_states.items())),
               'relation_occurrences': counts['event_narrative_relation'],
               'full_7_1': False}
    return expected, release


def build(source_root, scope_path, qt_artifact, output, *, qt_manifest_path=None):
    output = Path(output)
    sidecars = [Path(str(output) + suffix) for suffix in ('-wal', '-shm', '-journal')]
    if output.exists() or any(path.exists() for path in sidecars):
        raise FileExistsError('Filmfest output/sidecar already exists')
    expected, release = project(source_root, scope_path, qt_artifact,
                                qt_manifest_path=qt_manifest_path)
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
                marks = ','.join('?' for _ in expected[table][0])
                connection.executemany('INSERT INTO ' + table + ' VALUES (' + marks + ')', expected[table])
        connection.execute('INSERT INTO event_narrative_release VALUES(1,?)',
                           (json.dumps(release, sort_keys=True, separators=(',', ':')),))
        connection.commit()
        connection.execute('VACUUM')
        connection.close()
        connection = None
        if any(path.exists() for path in sidecars):
            raise ValueError('Filmfest SQLite sidecar')
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
