"""Independent source-to-rowset oracle for component-10's 7.1 successor."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .event_7_1_source import array_records, check_files, pairs, sha
from .event_narrative_source import check_files as check_old_files

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / 'manifests/event-narrative/scope.json'
SCHEMA = ROOT / 'schemas/event-successor-7.1-v1.sql'
TABLES = ('event_narrative_row', 'event_narrative_slot', 'event_narrative_text')


def occurrence_id(*values):
    payload = json.dumps(values, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
    return 'event10-71:' + hashlib.sha256(payload.encode()).hexdigest()[:40]


def schema_objects(db):
    return [list(row) for row in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]


def _migrations(old_source, new_source, old_specs):
    """Reconstruct every raw rename without trusting the candidate scope's map."""
    result = {}
    for spec in old_specs:
        before = list(array_records(old_source / spec['path']))
        after = list(array_records(new_source / spec['path']))
        if len(before) != len(after):
            raise ValueError('Event component-10 historical physical denominator drift')
        native = spec.get('native_id_field')
        if native is None:
            left = {ordinal: row for ordinal, row, _ in before}
            right = {ordinal: row for ordinal, row, _ in after}
        else:
            left = {row[native]: row for _, row, _ in before}
            right = {row[native]: row for _, row, _ in after}
            if len(left) != len(before) or len(right) != len(after) or set(left) != set(right):
                raise ValueError('Event complete source-owned native-ID alignment')
        if native is None and any(field not in next(iter(right.values()))
                                  for field in spec['text_fields']):
            old_columns = set.intersection(*(set(row) for row in left.values()))
            new_columns = set.intersection(*(set(row) for row in right.values()))
            stable = {}
            for old_key in old_columns:
                candidates = [new_key for new_key in new_columns if all(
                    type(left[index][old_key]) is type(right[index][new_key])
                    and left[index][old_key] == right[index][new_key] for index in left)]
                if len(candidates) == 1:
                    stable[old_key] = candidates[0]
            distinct = [key for key in stable if len({json.dumps(left[index][key], sort_keys=True)
                                                    for index in left}) == len(left)]
            if len(stable) < 5 or not distinct:
                raise ValueError('Event ordinal alignment lacks independent raw invariants')
        mapped = {}
        common_new = set.intersection(*(set(row) for row in right.values()))
        for field in spec['text_fields']:
            if field in next(iter(right.values())):
                if any(field not in row or type(row[field]) is not int for row in right.values()):
                    raise ValueError('Event retained owner field shape')
                continue
            legal = sorted(new_field for new_field in common_new if all(
                type(right[key][new_field]) is int and type(left[key][field]) is int
                and left[key][field] == right[key][new_field] for key in left))
            if len(legal) != 1:
                raise ValueError(f'Event raw locator continuity nonunique {spec["kind"]}/{field}: {legal}')
            mapped[field] = legal[0]
        if len(set(mapped.values())) != len(mapped):
            raise ValueError('Event raw locators collapsed')
        result[spec['kind']] = mapped
    return result


def verify(source_root, old_source, scope_path, artifact, build_report=None, runtime_manifest=None):
    source_root, old_source, scope_path, artifact = map(Path,
                                                        (source_root, old_source, scope_path, artifact))
    scope = json.loads(scope_path.read_text())
    old = json.loads(OLD.read_text())
    check_files(source_root, scope)
    check_old_files(old_source, old)
    if (scope['contract'] != 'event-legacy-7.1-versioned-scope-v1'
            or scope['family_contract_version'] != 'event-narrative-evidence-v1'
            or scope['historical_scope'] != OLD.relative_to(ROOT).as_posix()
            or scope['historical_scope_sha256'] != sha(OLD)
            or scope['languages'] != old['languages']
            or len(old['sources']) != 14 or len(scope['sources']) != 14
            or old['text_lookup'] != 'all physical main candidates first; Medium only when main has no candidate'):
        raise ValueError('Event component-10 historical contract drift')
    rename = _migrations(old_source, source_root, old['sources'])
    expected_specs = json.loads(json.dumps(old['sources']))
    for spec in expected_specs:
        mapping = rename[spec['kind']]
        spec['text_fields'] = [mapping.get(field, field) for field in spec['text_fields']]
        spec['owner_roles'] = {mapping.get(field, field): role
                               for field, role in spec.get('owner_roles', {}).items()}
        if set(scope['field_continuity'][spec['kind']]) != set(mapping):
            raise ValueError('Event field continuity declaration mismatch')
        for historical_field, current_field in mapping.items():
            proof = scope['field_continuity'][spec['kind']][historical_field]
            if (proof['new_field'] != current_field or proof['role_status'] != 'mapping_unresolved'
                    or proof['counterexamples'] != 0):
                raise ValueError('Event raw rename claim drift')
    if expected_specs != scope['sources']:
        raise ValueError('Event candidate source specification differs from old selector plus raw rename')
    selected = {spec['path'] for spec in expected_specs}
    selected |= {f'TextMap/TextMap{suffix}{locale}.json' for locale in scope['languages']
                 for suffix in ('', '_Medium')}
    if selected != {file['path'] for file in scope['files']} or len(selected) != 20:
        raise ValueError('Event 7.1 source selection incomplete')
    implementation = {path: sha(ROOT / path) for path in (
        'src/genshin_lore/event_legacy_7_1_build.py',
        'src/genshin_lore/event_7_1_source.py',
        'src/genshin_lore/event_narrative_source.py',
        'schemas/event-successor-7.1-v1.sql')}
    parser = occurrence_id('parser', implementation)
    scope_sha = sha(scope_path)
    run = occurrence_id('run', parser, scope_sha)
    file_seals = {file['path']: file for file in scope['files']}
    physical, wanted = {}, set()
    for spec in expected_specs:
        entries = list(array_records(source_root / spec['path']))
        if len(entries) != scope['row_denominator'][spec['kind']]:
            raise ValueError('Event physical row denominator')
        physical[spec['kind']] = entries
        for ordinal, row, _ in entries:
            for field in spec['text_fields']:
                if type(row.get(field)) is not int:
                    raise ValueError(f'Event text locator type {spec["path"]}/{ordinal}/{field}')
                wanted.add(str(row[field]))
    if sum(map(len, physical.values())) != scope['physical_rows'] or scope['physical_rows'] != 416:
        raise ValueError('Event complete 7.1 physical row count')
    hits = defaultdict(list)
    for locale in scope['languages']:
        for tier, suffix in (('main', ''), ('medium', '_Medium')):
            path = f'TextMap/TextMap{suffix}{locale}.json'
            for ordinal, (key, text) in enumerate(pairs(source_root / path)):
                if key in wanted:
                    hits[(locale, tier, key)].append((path, ordinal, '/' + key, text))
    expected = {table: [] for table in TABLES}
    states, roles = Counter(), Counter()
    for spec in expected_specs:
        path = spec['path']
        seal = file_seals[path]
        for ordinal, row, raw in physical[spec['kind']]:
            row_id = occurrence_id(run, 'row', path, ordinal)
            native_field = spec.get('native_id_field')
            native = None if native_field is None or native_field not in row else str(row[native_field])
            expected['event_narrative_row'].append((
                row_id, run, spec['kind'], path, seal['sha256'], seal['blob'], ordinal,
                '/' + str(ordinal), native_field, native, raw, hashlib.sha256(raw.encode()).hexdigest()))
            for field in spec['text_fields']:
                locator = str(row[field])
                role = spec.get('owner_roles', {}).get(field, 'resolved')
                roles[role] += len(scope['languages'])
                for locale in scope['languages']:
                    main = hits[(locale, 'main', locator)]
                    medium = hits[(locale, 'medium', locator)]
                    candidates = main if main else medium
                    status = ('missing' if not candidates else 'ambiguous' if len(candidates) > 1
                              else 'resolved_main' if main else 'resolved_medium')
                    states[status] += 1
                    slot = occurrence_id(run, 'slot', path, ordinal, field, locale)
                    expected['event_narrative_slot'].append((
                        slot, row_id, field, role, locale, locator, status, len(candidates)))
                    for text_path, pair, pointer, text in candidates:
                        text_seal = file_seals[text_path]
                        expected['event_narrative_text'].append((
                            occurrence_id(run, 'text', path, ordinal, field, locale, text_path, pair),
                            slot, text_path, text_seal['sha256'], text_seal['blob'], pair,
                            pointer, text, hashlib.sha256(text.encode()).hexdigest()))
    for table in TABLES:
        expected[table].sort()
    memory = sqlite3.connect(':memory:')
    memory.executescript(SCHEMA.read_text())
    wanted_schema = schema_objects(memory)
    memory.close()
    db = sqlite3.connect(artifact.absolute().as_uri() + '?mode=ro&immutable=1', uri=True)
    if schema_objects(db) != wanted_schema:
        raise ValueError('Event schema/index/trigger tamper')
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Event SQLite integrity/FK')
    if any(Path(str(artifact) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise ValueError('Event SQLite sidecar')
    for table in TABLES:
        got = db.execute('SELECT * FROM ' + table + ' ORDER BY ' + ','.join(
            str(index + 1) for index in range(len(expected[table][0])))).fetchall()
        if got != expected[table]:
            raise ValueError('Event full independent source-to-rowset mismatch ' + table)
    release = json.loads(db.execute('SELECT manifest_json FROM event_narrative_release WHERE id=1').fetchone()[0])
    db.close()
    counts = {table: len(expected[table]) for table in TABLES}
    expected_release = {'contract': scope['family_contract_version'], 'run_id': run,
                        'parser_build_id': parser, 'scope_sha256': scope_sha,
                        'implementation': implementation, 'snapshot_id': scope['snapshot_id'],
                        'counts': counts, 'text_state_counts': dict(sorted(states.items())),
                        'owner_role_counts': dict(sorted(roles.items())),
                        'relation_occurrences': 0, 'full_7_1': False}
    if release != expected_release:
        raise ValueError('Event release identity/counter drift')
    logical = {table: hashlib.sha256('\n'.join(json.dumps(list(row), ensure_ascii=False,
                       separators=(',', ':')) for row in expected[table]).encode()).hexdigest()
               for table in TABLES}
    artifact_seal = {'size_bytes': artifact.stat().st_size, 'sha256': sha(artifact)}
    if build_report is not None and (build_report.get('artifact') != artifact_seal
                                     or build_report.get('logical_seals') != logical):
        raise ValueError('Event build report drift')
    if runtime_manifest is not None:
        manifest = json.loads(Path(runtime_manifest).read_text())
        if (manifest['artifact'] != artifact_seal or manifest['release'] != expected_release
                or manifest['logical_seals'] != logical or manifest['counts'] != counts):
            raise ValueError('Event runtime manifest drift')
    return {'status': 'pass', 'artifact': artifact_seal, 'parser_build_id': parser,
            'run_id': run, 'source_files': len(selected), 'physical_rows': scope['physical_rows'],
            'counts': counts, 'text_state_counts': dict(sorted(states.items())),
            'owner_role_counts': dict(sorted(roles.items())), 'field_continuity': rename,
            'logical_seals': logical, 'silent_skips': 0, 'no_extras': True,
            'no_omissions': True, 'schema_structure': wanted_schema}
