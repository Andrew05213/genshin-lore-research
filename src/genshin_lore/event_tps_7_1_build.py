"""Versioned 7.1 TPS Glasses Event source-local graph candidate builder."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .event_7_1_source import array_records, check_files, pairs, sha
from .batch_release import (assert_output_paths, create_private_staging,
                            publish_absent, sidecars)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / 'schemas/event-tps-7.1-v1.sql'
OLD = ROOT / 'manifests/event-narrative/tps-glasses-chat-scope.json'
TABLES = ('event_narrative_row', 'event_narrative_slot', 'event_narrative_text',
          'event_narrative_relation', 'event_narrative_relation_candidate')


def ident(*parts):
    payload = json.dumps(parts, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
    return 'tps71:' + hashlib.sha256(payload.encode()).hexdigest()[:40]


def owner_observations(row, specs):
    for spec in specs:
        condition = spec.get('when')
        if condition is not None:
            if condition.get('present') is True and condition['field'] not in row:
                continue
            if 'equals' in condition and row.get(condition['field']) != condition['equals']:
                continue
        field = spec['field']
        if spec['shape'] != 'scalar' or type(row.get(field)) is not int:
            raise ValueError('TPS consumed owner shape ' + field)
        yield field, row[field], spec['role_status']


def project(source_root, scope_path):
    source_root, scope_path = Path(source_root), Path(scope_path)
    scope = json.loads(scope_path.read_text())
    historical = json.loads(OLD.read_text())
    check_files(source_root, scope)
    if (scope['contract'] != 'event-tps-7.1-versioned-scope-v1'
            or scope['historical_scope_sha256'] != sha(OLD)
            or scope['family_contract_version'] != 'event-narrative-evidence-v3'
            or scope['languages'] != historical['languages']
            or len(scope['sources']) != 6 or len(historical['sources']) != 6
            or scope['talk_candidates'] or historical['talk_candidates']):
        raise ValueError('TPS historical boundary drift')
    selected = {item['path'] for item in scope['sources']}
    selected |= {f'TextMap/TextMap{suffix}{locale}.json' for locale in scope['languages']
                 for suffix in ('', '_Medium')}
    files = {item['path']: item for item in scope['files']}
    if len(selected) != 12 or set(files) != selected:
        raise ValueError('TPS complete source scope')
    implementation = {path: sha(ROOT / path) for path in (
        'src/genshin_lore/event_tps_7_1_build.py',
        'src/genshin_lore/event_7_1_source.py',
        'src/genshin_lore/event_narrative_source.py',
        'schemas/event-tps-7.1-v1.sql')}
    parser = ident('parser', implementation)
    scope_sha = sha(scope_path)
    run = ident('run', parser, scope_sha)
    physical = {}
    wanted = set()
    for item in scope['sources']:
        records = list(array_records(source_root / item['path']))
        if len(records) != scope['row_denominator'][item['kind']]:
            raise ValueError('TPS full source row denominator ' + item['kind'])
        physical[item['kind']] = records
        for _, row, _ in records:
            for _, locator, _ in owner_observations(row, item['text_owners']):
                wanted.add(str(locator))
    if sum(map(len, physical.values())) != 507:
        raise ValueError('TPS historical six-array denominator')
    textmap = defaultdict(list)
    for locale in scope['languages']:
        for tier, suffix in (('main', ''), ('medium', '_Medium')):
            path = f'TextMap/TextMap{suffix}{locale}.json'
            for pair, (key, text) in enumerate(pairs(source_root / path)):
                if key in wanted:
                    textmap[(locale, tier, key)].append((path, pair, '/' + key, text))
    expected = {table: [] for table in TABLES}
    states, roles, relations = Counter(), Counter(), Counter()
    row_lookup, composite_lookup, row_ids = defaultdict(list), defaultdict(list), {}
    for item in scope['sources']:
        path = item['path']
        seal = files[path]
        for ordinal, row, raw in physical[item['kind']]:
            native_field = item.get('native_id_field')
            native = None if native_field is None else str(row[native_field])
            occurrence = ident(run, 'row', path, ordinal)
            row_ids[(item['kind'], ordinal)] = occurrence
            expected['event_narrative_row'].append((
                occurrence, run, item['boundary'], path, seal['sha256'], seal['blob'],
                ordinal, '/' + str(ordinal), native_field, native, raw,
                hashlib.sha256(raw.encode()).hexdigest()))
            lookup = item.get('relation_lookup_field')
            if lookup is not None:
                if lookup not in row:
                    if not item.get('relation_lookup_field_optional'):
                        raise ValueError('TPS target lookup field missing ' + lookup)
                else:
                    key = str(row[lookup])
                    row_lookup[(item['kind'], key)].append((path, '/' + str(ordinal),
                                                             key, occurrence))
            for fields in item.get('relation_lookup_field_sets', []):
                if any(field not in row for field in fields):
                    raise ValueError('TPS target composite lookup field missing')
                key = tuple(str(row[field]) for field in fields)
                composite_lookup[(item['kind'], tuple(fields), key)].append(
                    (path, '/' + str(ordinal), occurrence))
    for item in scope['sources']:
        path = item['path']
        for ordinal, row, _ in physical[item['kind']]:
            occurrence = row_ids[(item['kind'], ordinal)]
            for field, locator, role in owner_observations(row, item['text_owners']):
                roles[role] += len(scope['languages'])
                for locale in scope['languages']:
                    main = textmap[(locale, 'main', str(locator))]
                    medium = textmap[(locale, 'medium', str(locator))]
                    candidates = main if main else medium
                    status = ('missing' if not candidates else 'ambiguous' if len(candidates) > 1
                              else 'resolved_main' if main else 'resolved_medium')
                    states[status] += 1
                    slot = ident(run, 'slot', path, ordinal, field, locale)
                    expected['event_narrative_slot'].append((
                        slot, occurrence, field, role, locale, str(locator), status,
                        len(candidates)))
                    for source_path, pair, pointer, text in candidates:
                        seal = files[source_path]
                        expected['event_narrative_text'].append((
                            ident(run, 'text', path, ordinal, field, locale, source_path, pair),
                            slot, source_path, seal['sha256'], seal['blob'], pair,
                            pointer, text, hashlib.sha256(text.encode()).hexdigest()))
            for spec in item['internal_relations']:
                shape = spec['shape']
                raw = row.get(spec['field'])
                if shape == 'scalar_optional' and raw is None:
                    continue
                values = raw if shape == 'array_scalar' else [raw]
                if not isinstance(values, list) or any(type(value) is not int for value in values):
                    raise ValueError('TPS relation primitive/container shape ' + spec['field'])
                for position, locator in enumerate(values):
                    if locator in spec.get('skip_values', []):
                        continue
                    if spec.get('target_lookup_fields'):
                        source_fields = spec['source_lookup_fields']
                        if any(field not in row for field in source_fields):
                            raise ValueError('TPS composite source lookup missing')
                        key = tuple(str(row[field]) for field in source_fields)
                        found = sorted(composite_lookup[(spec['target_kind'],
                                                         tuple(spec['target_lookup_fields']), key)])
                        targets = [(target_path, target_pointer, str(locator), target_id)
                                   for target_path, target_pointer, target_id in found]
                    else:
                        targets = sorted(row_lookup[(spec['target_kind'], str(locator))])
                    inferred = ('missing' if not targets else 'resolved_unique'
                                if len(targets) == 1 else 'ambiguous')
                    status = spec.get('status', inferred)
                    complete = int(spec.get('search_complete', True))
                    relations[status] += 1
                    pointer = (f'/{ordinal}/{spec["field"]}' +
                               (f'/{position}' if shape == 'array_scalar' else ''))
                    relation = ident(run, 'relation', path, pointer, spec['relation_kind'])
                    namespace = spec.get('candidate_namespace',
                                         'event_wave_physical_rows:' + spec['target_kind'])
                    expected['event_narrative_relation'].append((
                        relation, occurrence, spec['relation_kind'], spec['field'],
                        pointer, str(locator), namespace, status, len(targets), complete))
                    for target_path, target_pointer, target_native, target_id in targets:
                        expected['event_narrative_relation_candidate'].append((
                            ident(run, 'relation-candidate', relation, target_path, run, target_id),
                            relation, target_path, target_pointer, target_native,
                            'event_narrative_archive', scope['family_contract_version'],
                            parser, run, target_id))
    for table in TABLES:
        expected[table].sort()
    release = {'contract': scope['family_contract_version'], 'run_id': run,
               'parser_build_id': parser, 'scope_sha256': scope_sha,
               'implementation': implementation, 'snapshot_id': scope['snapshot_id'],
               'counts': {table: len(expected[table]) for table in TABLES},
               'text_state_counts': dict(sorted(states.items())),
               'owner_role_counts': dict(sorted(roles.items())),
               'relation_state_counts': dict(sorted(relations.items())),
               'relation_occurrences': len(expected['event_narrative_relation']),
               'full_7_1': False}
    return expected, release


def build(source_root, scope_path, output):
    output, source_root, scope_path = Path(output).absolute(), Path(source_root).absolute(), Path(scope_path).absolute()
    scope = json.loads(scope_path.read_text())
    inputs = [('source_root', source_root), ('scope', scope_path),
              ('historical_scope', OLD), ('schema', SCHEMA)]
    inputs.extend((f'source_{index}', source_root / item['path'])
                  for index, item in enumerate(scope['files']))
    assert_output_paths(output, inputs=inputs)
    expected, release = project(source_root, scope_path)
    descriptor, stage = create_private_staging(output, prefix='.event-tps-7.1-')
    stage_identity = (stage.stat().st_dev, stage.stat().st_ino)
    db = None
    try:
        import os
        os.close(descriptor)
        db = sqlite3.connect(stage)
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(SCHEMA.read_text())
        for table in TABLES:
            if expected[table]:
                db.executemany('INSERT INTO ' + table + ' VALUES(' +
                               ','.join('?' for _ in expected[table][0]) + ')',
                               expected[table])
        db.execute('INSERT INTO event_narrative_release VALUES(1,?)',
                   (json.dumps(release, sort_keys=True, separators=(',', ':')),))
        db.commit()
        db.execute('VACUUM')
        if (db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'
                or db.execute('PRAGMA foreign_key_check').fetchall()):
            raise ValueError('TPS staging integrity/FK')
        for table in TABLES:
            if db.execute('SELECT * FROM ' + table + ' ORDER BY id').fetchall() != expected[table]:
                raise ValueError('TPS staging full rowset check ' + table)
        stored_release = db.execute(
            'SELECT manifest_json FROM event_narrative_release WHERE id=1').fetchone()
        if stored_release is None or json.loads(stored_release[0]) != release:
            raise ValueError('TPS staging release check')
        db.close()
        db = None
        if any(path.exists() for path in sidecars(stage)):
            raise ValueError('TPS staging SQLite sidecar')
        check_files(source_root, scope)
        logical = {table: hashlib.sha256('\n'.join(json.dumps(list(row), ensure_ascii=False,
                   separators=(',', ':')) for row in expected[table]).encode()).hexdigest()
                   for table in TABLES}
        candidate = {'size_bytes': stage.stat().st_size, 'sha256': sha(stage)}
        publish_absent(output, stage, inputs=inputs)
        if (output.stat().st_size != candidate['size_bytes']
                or sha(output) != candidate['sha256']):
            raise ValueError('TPS post-promotion seal drift')
        return {**release, 'artifact': candidate, 'logical_seals': logical}
    finally:
        if db is not None:
            db.close()
