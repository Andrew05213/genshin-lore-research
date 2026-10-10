"""Independent 7.1 component-11 source-to-rowset and migration oracle."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .event_7_1_source import array_records, check_files, pairs, sha
from .event_narrative_source import check_files as check_historical

ROOT = Path(__file__).resolve().parents[2]
HISTORICAL = ROOT / 'manifests/event-narrative/successor-scope.json'
SCHEMA = ROOT / 'schemas/event-successor-7.1-v1.sql'
TABLES = ('event_narrative_row', 'event_narrative_slot', 'event_narrative_text')


def independent_id(*values):
    payload = json.dumps(values, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
    return 'event11-71:' + hashlib.sha256(payload.encode()).hexdigest()[:40]


def schema_signature(database):
    return [list(row) for row in database.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]


def verify(source_root, historical_source, scope_path, artifact, build_report=None, runtime_manifest=None):
    source_root = Path(source_root)
    historical_source = Path(historical_source)
    scope_path = Path(scope_path)
    artifact = Path(artifact)
    scope = json.loads(scope_path.read_text())
    historical = json.loads(HISTORICAL.read_text())
    check_files(source_root, scope)
    check_historical(historical_source, historical)
    if (scope['contract'] != 'event-successor-7.1-versioned-scope-v1'
            or scope['historical_scope'] != str(HISTORICAL.relative_to(ROOT))
            or scope['historical_scope_sha256'] != sha(HISTORICAL)
            or scope['family_contract_version'] != historical['family_contract_version']
            or historical['text_lookup'] != 'all physical main candidates first; Medium only when main has no candidate'
            or len(scope['sources']) != 6 or len(historical['sources']) != 6
            or scope['languages'] != historical['languages']):
        raise ValueError('Event v2 historical boundary drift')
    historical_level = next(item for item in historical['sources'] if item['kind'] == 'ad_shooting_level')
    new_level = next(item for item in scope['sources'] if item['kind'] == 'ad_shooting_level')
    old_records = [row for _, row, _ in array_records(historical_source / historical_level['path'])]
    new_records = [row for _, row, _ in array_records(source_root / new_level['path'])]
    old_by_id = {row['levelId']: row for row in old_records}
    new_by_id = {row['levelId']: row for row in new_records}
    if len(old_by_id) != 5 or len(new_by_id) != 5 or set(old_by_id) != set(new_by_id):
        raise ValueError('Event Level source-owned alignment')
    old_unresolved = [field for field in historical_level['text_fields']
                      if historical_level.get('owner_roles', {}).get(field) == 'mapping_unresolved']
    if len(old_unresolved) != 6 or set(scope['field_continuity']) != set(old_unresolved):
        raise ValueError('six unresolved historical observations')
    expected_migrations = {}
    for field in old_unresolved:
        vector = [old_by_id[key][field] for key in sorted(old_by_id)]
        possible = sorted(key for key in set.intersection(*(set(row) for row in new_records))
                          if all(type(new_by_id[native][key]) is int for native in old_by_id)
                          and [new_by_id[native][key] for native in sorted(old_by_id)] == vector)
        if len(possible) != 1 or possible[0] == field:
            raise ValueError('Event new physical owner mapping not unique ' + field)
        expected_migrations[field] = possible[0]
        if scope['field_continuity'][field]['new_field'] != possible[0] or scope['field_continuity'][field]['owner_role_status'] != 'mapping_unresolved':
            raise ValueError('Event migrated field declaration mismatch')
    expected_specs = json.loads(json.dumps(historical['sources']))
    for item in expected_specs:
        if item['kind'] != 'ad_shooting_level':
            continue
        item['text_fields'] = [expected_migrations.get(field, field) for field in item['text_fields']]
        item['owner_roles'] = {expected_migrations.get(field, field): role
                               for field, role in item['owner_roles'].items()}
    if expected_specs != scope['sources']:
        raise ValueError('Event source declarations differ from historical selector plus proven raw renames')
    selected = {item['path'] for item in expected_specs}
    selected |= {f'TextMap/TextMap{suffix}{locale}.json' for locale in scope['languages']
                 for suffix in ('', '_Medium')}
    if len(selected) != 12 or len(scope['files']) != 12 or selected != {item['path'] for item in scope['files']}:
        raise ValueError('Event full selected file denominator')
    implementation = {path: sha(ROOT / path) for path in (
        'src/genshin_lore/event_successor_7_1_build.py',
        'src/genshin_lore/event_7_1_source.py',
        'schemas/event-successor-7.1-v1.sql')}
    parser = independent_id('parser', implementation)
    scope_sha = sha(scope_path)
    run = independent_id('run', parser, scope_sha)
    sealed = {item['path']: item for item in scope['files']}
    physical = {}
    wanted = set()
    for item in scope['sources']:
        records = list(array_records(source_root / item['path']))
        if len(records) != historical['row_denominator'][item['kind']] or len(records) != scope['row_denominator'][item['kind']]:
            raise ValueError('Event real 7.1 source row denominator')
        physical[item['kind']] = records
        for ordinal, row, _ in records:
            if not isinstance(row, dict):
                raise ValueError('non-object Event row')
            for field in item['text_fields']:
                if type(row.get(field)) is not int:
                    raise ValueError(f'Event owner field shape {item["path"]}/{ordinal}/{field}')
                wanted.add(str(row[field]))
    textmap = defaultdict(list)
    for locale in scope['languages']:
        for tier, suffix in (('main', ''), ('medium', '_Medium')):
            path = f'TextMap/TextMap{suffix}{locale}.json'
            for pair, (key, text) in enumerate(pairs(source_root / path)):
                if key in wanted:
                    textmap[(locale, tier, key)].append((path, pair, '/' + key, text))
    expected = {table: [] for table in TABLES}
    state_counts, role_counts = Counter(), Counter()
    for item in scope['sources']:
        path = item['path']
        for ordinal, row, raw in physical[item['kind']]:
            pointer = '/' + str(ordinal)
            row_id = independent_id(run, 'row', path, ordinal)
            key_field = item.get('native_id_field')
            native = None if key_field is None else str(row[key_field])
            file = sealed[path]
            expected['event_narrative_row'].append((
                row_id, run, item['kind'], path, file['sha256'], file['blob'], ordinal,
                pointer, key_field, native, raw, hashlib.sha256(raw.encode()).hexdigest()))
            for field in item['text_fields']:
                role = item.get('owner_roles', {}).get(field, 'resolved')
                locator = str(row[field])
                role_counts[role] += len(scope['languages'])
                for locale in scope['languages']:
                    main = textmap[(locale, 'main', locator)]
                    medium = textmap[(locale, 'medium', locator)]
                    candidates = main or medium
                    status = ('missing' if not candidates else 'ambiguous' if len(candidates) != 1
                              else 'resolved_main' if main else 'resolved_medium')
                    state_counts[status] += 1
                    slot_id = independent_id(run, 'slot', path, ordinal, field, locale)
                    expected['event_narrative_slot'].append((
                        slot_id, row_id, field, role, locale, locator, status, len(candidates)))
                    for source_path, pair, text_pointer, text in candidates:
                        entry = sealed[source_path]
                        expected['event_narrative_text'].append((
                            independent_id(run, 'text', path, ordinal, field, locale, source_path, pair),
                            slot_id, source_path, entry['sha256'], entry['blob'], pair,
                            text_pointer, text, hashlib.sha256(text.encode()).hexdigest()))
    for table in TABLES:
        expected[table].sort()
    memory = sqlite3.connect(':memory:')
    memory.executescript(SCHEMA.read_text())
    expected_schema = schema_signature(memory)
    memory.close()
    db = sqlite3.connect(artifact.absolute().as_uri() + '?mode=ro&immutable=1', uri=True)
    if schema_signature(db) != expected_schema:
        raise ValueError('Event schema/guard/index drift')
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Event SQLite integrity/FK')
    if any(Path(str(artifact) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise ValueError('Event SQLite sidecar')
    for table in TABLES:
        got = db.execute('SELECT * FROM ' + table + ' ORDER BY ' + ','.join(str(index + 1)
                         for index in range(len(expected[table][0])))).fetchall()
        if got != expected[table]:
            raise ValueError('Event full source-to-rowset mismatch/no-extra/no-omission ' + table)
    release = json.loads(db.execute('SELECT manifest_json FROM event_narrative_release WHERE id=1').fetchone()[0])
    db.close()
    counts = {table: len(expected[table]) for table in TABLES}
    wanted_release = {'contract': scope['family_contract_version'], 'run_id': run,
                      'parser_build_id': parser, 'scope_sha256': scope_sha,
                      'implementation': implementation, 'snapshot_id': scope['snapshot_id'],
                      'counts': counts, 'text_state_counts': dict(sorted(state_counts.items())),
                      'owner_role_counts': dict(sorted(role_counts.items())),
                      'relation_occurrences': 0, 'full_7_1': False}
    if release != wanted_release:
        raise ValueError('Event release mismatch')
    logical = {table: hashlib.sha256('\n'.join(json.dumps(list(row), ensure_ascii=False,
                          separators=(',', ':')) for row in expected[table]).encode()).hexdigest()
               for table in TABLES}
    artifact_seal = {'size_bytes': artifact.stat().st_size, 'sha256': sha(artifact)}
    if build_report is not None and (build_report.get('artifact') != artifact_seal
                                     or build_report.get('logical_seals') != logical):
        raise ValueError('Event build report mismatch')
    if runtime_manifest is not None:
        manifest = json.loads(Path(runtime_manifest).read_text())
        if (manifest['artifact'] != artifact_seal or manifest['release'] != wanted_release
                or manifest['logical_seals'] != logical
                or manifest['counts'] != counts):
            raise ValueError('Event runtime manifest mismatch')
    return {'status': 'pass', 'artifact': artifact_seal, 'parser_build_id': parser,
            'run_id': run, 'scope_sha256': scope_sha, 'counts': counts,
            'text_states': dict(sorted(state_counts.items())),
            'owner_roles': dict(sorted(role_counts.items())),
            'field_continuity_rechecked': expected_migrations,
            'source_files': len(selected), 'physical_rows': sum(len(x) for x in physical.values()),
            'schema_structure': expected_schema, 'logical_seals': logical,
            'no_extra_rows': True, 'no_omissions': True, 'silent_skips': 0}
