"""Independent source-to-rowset oracle for the 7.1 Filmfest Shooting boundary."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .event_7_1_source import array_records, check_files, pairs, sha
from .event_narrative_source import check_files as check_historical

ROOT = Path(__file__).resolve().parents[2]
HISTORICAL = ROOT / 'manifests/event-narrative/filmfest-scope.json'
SCHEMA = ROOT / 'schemas/event-filmfest-7.1-v1.sql'
QT_MANIFEST = ROOT / 'manifests/quest-talk-7.1/standalone-candidate.json'
TABLES = ('event_narrative_row', 'event_narrative_slot', 'event_narrative_text',
          'event_narrative_relation', 'event_narrative_relation_candidate')


def independent_id(*parts):
    payload = json.dumps(parts, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
    return 'filmfest71:' + hashlib.sha256(payload.encode()).hexdigest()[:40]


def _schema(db):
    return [list(row) for row in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]


def _owner_values(record, declarations):
    for declaration in declarations:
        field = declaration['field']
        value = record.get(field)
        if declaration['shape'] == 'scalar':
            if type(value) is not int:
                raise ValueError('oracle Filmfest scalar owner')
            yield field, value, declaration['role_status']
        elif declaration['shape'] == 'array':
            if not isinstance(value, list) or any(type(item) is not int for item in value):
                raise ValueError('oracle Filmfest array owner')
            for ordinal, item in enumerate(value):
                yield field + '/' + str(ordinal), item, declaration['role_status']
        else:
            raise ValueError('oracle Filmfest unknown owner shape')


def verify(source_root, historical_source, scope_path, qt_artifact, artifact,
           runtime_manifest=None, *, qt_manifest_path=None):
    source_root = Path(source_root)
    historical_source = Path(historical_source)
    scope_path = Path(scope_path)
    qt_artifact = Path(qt_artifact)
    artifact = Path(artifact)
    scope = json.loads(scope_path.read_text())
    historical = json.loads(HISTORICAL.read_text())
    manifest_path = Path(qt_manifest_path) if qt_manifest_path is not None else QT_MANIFEST
    qt_manifest = json.loads(manifest_path.read_text())
    check_files(source_root, scope)
    check_historical(historical_source, historical)
    if (scope['contract'] != 'event-filmfest-7.1-versioned-scope-v1'
            or scope['historical_scope_sha256'] != sha(HISTORICAL)
            or scope['family_contract_version'] != historical['family_contract_version']
            or len(scope['sources']) != 4 or len(historical['sources']) != 4
            or scope['languages'] != historical['languages']
            or scope['talk_candidate_namespace']['accepted_manifest_sha256'] != sha(manifest_path)
            or scope['talk_candidate_namespace']['artifact'] != qt_manifest['artifact']
            or scope['talk_candidate_namespace']['contract'] != qt_manifest['contract']
            or scope['talk_candidate_namespace']['parser'] != qt_manifest['parser_build_id']
            or scope['talk_candidate_namespace']['run'] != qt_manifest['ingest_run_id']
            or scope['talk_candidate_namespace']['search_complete'] is not True
            or qt_artifact.stat().st_size != qt_manifest['artifact']['size_bytes']
            or sha(qt_artifact) != qt_manifest['artifact']['sha256']):
        raise ValueError('oracle Filmfest historical/accepted external binding')
    expected_specs = json.loads(json.dumps(historical['sources']))
    expected_mappings = {}
    physical = {}
    for spec in expected_specs:
        old = list(array_records(historical_source / spec['path']))
        new = list(array_records(source_root / spec['path']))
        if (len(old) != historical['row_denominator'][spec['kind']]
                or len(new) != len(old) or len(new) != scope['row_denominator'][spec['kind']]):
            raise ValueError('oracle Filmfest full row denominator')
        old_ids = {record[spec['native_id_field']]: record for _, record, _ in old}
        new_ids = {record[spec['native_id_field']]: record for _, record, _ in new}
        if len(old_ids) != len(old) or len(new_ids) != len(new) or set(old_ids) != set(new_ids):
            raise ValueError('oracle Filmfest source-owned ID alignment')
        ids = sorted(old_ids)
        names = set.intersection(*(set(record) for record in new_ids.values()))
        mapping = {}
        required = {item['field'] for item in spec['text_owners'] + spec['internal_relations']
                    if item['field'] not in new[0][1]}
        for old_field in required:
            old_vector = [old_ids[key][old_field] for key in ids]
            matches = sorted(name for name in names
                             if [new_ids[key][name] for key in ids] == old_vector
                             and all(type(new_ids[key][name]) is type(old_ids[key][old_field])
                                     for key in ids))
            if len(matches) != 1 or matches[0] == old_field:
                raise ValueError('oracle Filmfest raw mapping not unique ' + old_field)
            mapping[old_field] = matches[0]
        expected_mappings[spec['kind']] = mapping
        for declaration in spec['text_owners'] + spec['internal_relations']:
            declaration['field'] = mapping.get(declaration['field'], declaration['field'])
        physical[spec['kind']] = new
    if expected_specs != scope['sources']:
        raise ValueError('oracle Filmfest source contract projection mismatch')
    if ({kind: {old: item['new_field'] for old, item in value.items()}
         for kind, value in scope['field_continuity'].items()} != expected_mappings):
        raise ValueError('oracle Filmfest seven field mapping declaration mismatch')
    level = next(spec for spec in expected_specs if spec['kind'] == 'filmfest_shooting_level')
    wanted_talk = {str(row[level['talk_relation_field']]) for _, row, _ in physical[level['kind']]}
    qt = sqlite3.connect(qt_artifact.absolute().as_uri() + '?mode=ro&immutable=1', uri=True)
    qt.row_factory = sqlite3.Row
    talk_candidates = []
    for native in sorted(wanted_talk):
        rows = qt.execute('SELECT id,ingest_run_id,source_path,pointer,native_id,raw_sha256 '
                          'FROM physical_container WHERE kind=? AND native_id=? '
                          'ORDER BY source_path,pointer,id', ('talk_root', native))
        for row in rows:
            item = dict(row)
            support = qt.execute('SELECT sha256,raw_bytes FROM source_file WHERE path=?',
                                 (item['source_path'],)).fetchone()
            if (support is None or item['pointer'] != '/' or item['native_id'] != native
                    or item['ingest_run_id'] != qt_manifest['ingest_run_id']
                    or support['sha256'] != sha(source_root / item['source_path'])
                    or support['raw_bytes'] != (source_root / item['source_path']).read_bytes()
                    or item['raw_sha256'] != hashlib.sha256(support['raw_bytes'].strip()).hexdigest()):
                raise ValueError('oracle Filmfest full Talk target second-hop')
            talk_candidates.append({
                'raw_locator': native, 'target_source_path': item['source_path'],
                'target_source_pointer': item['pointer'], 'target_native_id': native,
                'target_raw_sha256': item['raw_sha256'],
                'target_family': 'quest_talk_physical', 'target_contract': qt_manifest['contract'],
                'target_parser_build_id': qt_manifest['parser_build_id'],
                'target_run_id': qt_manifest['ingest_run_id'],
                'target_occurrence_id': item['id']})
    qt.close()
    if talk_candidates != scope['talk_candidates']:
        raise ValueError('oracle Filmfest complete Talk target set mismatch')
    selected = {spec['path'] for spec in expected_specs}
    selected |= {f'TextMap/TextMap{suffix}{locale}.json' for locale in scope['languages']
                 for suffix in ('', '_Medium')}
    selected |= {item['target_source_path'] for item in talk_candidates}
    if selected != {item['path'] for item in scope['files']} or len(selected) != 15:
        raise ValueError('oracle Filmfest selected source union')
    seals = {item['path']: item for item in scope['files']}
    wanted = {str(value) for spec in expected_specs for _, row, _ in physical[spec['kind']]
              for _, value, _ in _owner_values(row, spec['text_owners'])}
    textmap = defaultdict(list)
    for locale in scope['languages']:
        for tier, suffix in (('main', ''), ('medium', '_Medium')):
            path = f'TextMap/TextMap{suffix}{locale}.json'
            for pair, (key, text) in enumerate(pairs(source_root / path)):
                if key in wanted:
                    textmap[(locale, tier, key)].append((path, pair, '/' + key, text))
    implementation = {path: sha(ROOT / path) for path in (
        'src/genshin_lore/event_filmfest_7_1_build.py',
        'src/genshin_lore/event_7_1_source.py',
        'src/genshin_lore/event_narrative_source.py',
        'schemas/event-filmfest-7.1-v1.sql')}
    parser = independent_id('parser', implementation)
    scope_sha = sha(scope_path)
    run = independent_id('run', parser, scope_sha)
    expected = {table: [] for table in TABLES}
    row_ids, lookup = {}, defaultdict(list)
    states, roles, relation_states = Counter(), Counter(), Counter()
    for spec in expected_specs:
        for ordinal, row, raw in physical[spec['kind']]:
            path = spec['path']
            native = str(row[spec['native_id_field']])
            row_id = independent_id(run, 'row', path, ordinal)
            row_ids[(spec['kind'], ordinal)] = row_id
            lookup[(spec['kind'], native)].append((path, '/' + str(ordinal), row_id))
            file = seals[path]
            expected['event_narrative_row'].append((row_id, run, spec['boundary'], path,
                file['sha256'], file['blob'], ordinal, '/' + str(ordinal),
                spec['native_id_field'], native, raw, hashlib.sha256(raw.encode()).hexdigest()))
    by_talk = defaultdict(list)
    for target in talk_candidates:
        by_talk[target['raw_locator']].append(target)
    for spec in expected_specs:
        path = spec['path']
        for ordinal, row, _ in physical[spec['kind']]:
            parent = row_ids[(spec['kind'], ordinal)]
            for field, value, role in _owner_values(row, spec['text_owners']):
                roles[role] += len(scope['languages'])
                locator = str(value)
                for locale in scope['languages']:
                    main = textmap[(locale, 'main', locator)]
                    medium = textmap[(locale, 'medium', locator)]
                    candidates = main or medium
                    status = ('missing' if not candidates else 'ambiguous' if len(candidates) != 1
                              else 'resolved_main' if main else 'resolved_medium')
                    states[status] += 1
                    slot_id = independent_id(run, 'slot', path, ordinal, field, locale)
                    expected['event_narrative_slot'].append((
                        slot_id, parent, field, role, locale, locator, status, len(candidates)))
                    for text_path, pair, pointer, text in candidates:
                        file = seals[text_path]
                        expected['event_narrative_text'].append((
                            independent_id(run, 'text', path, ordinal, field, locale, text_path, pair),
                            slot_id, text_path, file['sha256'], file['blob'], pair, pointer,
                            text, hashlib.sha256(text.encode()).hexdigest()))
            for relation in spec['internal_relations']:
                field = relation['field']
                values = row.get(field)
                if not isinstance(values, list):
                    raise ValueError('oracle Filmfest relation list')
                for position, value in enumerate(values):
                    if relation['shape'] == 'array_scalar':
                        locator = value
                    elif relation['shape'] in ('array_object_id', 'array_object_id_optional'):
                        if not isinstance(value, dict):
                            raise ValueError('oracle Filmfest relation object')
                        if 'id' not in value:
                            if relation['shape'] == 'array_object_id_optional':
                                continue
                            raise ValueError('oracle Filmfest required relation id')
                        locator = value['id']
                    else:
                        raise ValueError('oracle Filmfest unknown relation shape')
                    if type(locator) is not int:
                        raise ValueError('oracle Filmfest relation locator')
                    target_rows = sorted(lookup[(relation['target_kind'], str(locator))])
                    status = 'missing' if not target_rows else 'resolved_unique' if len(target_rows) == 1 else 'ambiguous'
                    relation_states[status] += 1
                    pointer = f'/{ordinal}/{field}/{position}'
                    relation_id = independent_id(run, 'relation', path, pointer, relation['relation_kind'])
                    expected['event_narrative_relation'].append((
                        relation_id, parent, relation['relation_kind'], field, pointer,
                        str(locator), 'filmfest_physical_rows:' + relation['target_kind'],
                        status, len(target_rows), 1))
                    for target_path, target_pointer, target_id in target_rows:
                        expected['event_narrative_relation_candidate'].append((
                            independent_id(run, 'candidate', relation_id, target_path, run, target_id),
                            relation_id, target_path, target_pointer, str(locator),
                            'event_narrative_archive', scope['family_contract_version'],
                            parser, run, target_id))
            if spec.get('talk_relation_field'):
                field = spec['talk_relation_field']
                raw_locator = row.get(field)
                if type(raw_locator) is not int:
                    raise ValueError('oracle Filmfest Talk raw locator')
                locator = str(raw_locator)
                target_rows = sorted(by_talk[locator], key=lambda item: (
                    item['target_source_path'], item['target_source_pointer'],
                    item['target_run_id'], item['target_occurrence_id']))
                status = 'missing' if not target_rows else 'resolved_unique' if len(target_rows) == 1 else 'ambiguous'
                relation_states[status] += 1
                relation_id = independent_id(run, 'relation', path, ordinal, field)
                expected['event_narrative_relation'].append((
                    relation_id, parent, 'source_authored_talk_reference', field,
                    '/' + str(ordinal) + '/' + field, locator,
                    'accepted_full_7_1_quest_talk_physical_roots', status, len(target_rows), 1))
                for target in target_rows:
                    expected['event_narrative_relation_candidate'].append((
                        independent_id(run, 'candidate', relation_id, target['target_source_path'],
                                       target['target_run_id'], target['target_occurrence_id']),
                        relation_id, target['target_source_path'], target['target_source_pointer'],
                        locator, target['target_family'], target['target_contract'],
                        target['target_parser_build_id'], target['target_run_id'],
                        target['target_occurrence_id']))
    for table in TABLES:
        expected[table].sort()
    memory = sqlite3.connect(':memory:')
    memory.executescript(SCHEMA.read_text())
    reference_schema = _schema(memory)
    memory.close()
    db = sqlite3.connect(artifact.absolute().as_uri() + '?mode=ro&immutable=1', uri=True)
    if _schema(db) != reference_schema:
        raise ValueError('oracle Filmfest exact schema/trigger/index')
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('oracle Filmfest SQLite integrity/FK')
    for table in TABLES:
        rows = db.execute('SELECT * FROM ' + table).fetchall()
        if sorted(rows) != expected[table]:
            raise ValueError('oracle Filmfest source-to-rowset mismatch/no-extra/no-omission ' + table)
    release = json.loads(db.execute('SELECT manifest_json FROM event_narrative_release WHERE id=1').fetchone()[0])
    db.close()
    if any(Path(str(artifact) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise ValueError('oracle Filmfest SQLite sidecar')
    counts = {table: len(expected[table]) for table in TABLES}
    wanted_release = {'contract': scope['family_contract_version'], 'run_id': run,
                      'parser_build_id': parser, 'scope_sha256': scope_sha,
                      'implementation': implementation, 'snapshot_id': scope['snapshot_id'],
                      'external_quest_talk': scope['talk_candidate_namespace'],
                      'counts': counts, 'text_state_counts': dict(sorted(states.items())),
                      'owner_role_counts': dict(sorted(roles.items())),
                      'relation_state_counts': dict(sorted(relation_states.items())),
                      'relation_occurrences': counts['event_narrative_relation'],
                      'full_7_1': False}
    if release != wanted_release:
        raise ValueError('oracle Filmfest release')
    logical = {table: hashlib.sha256('\n'.join(json.dumps(list(row), ensure_ascii=False,
                         separators=(',', ':')) for row in expected[table]).encode()).hexdigest()
               for table in TABLES}
    artifact_seal = {'size_bytes': artifact.stat().st_size, 'sha256': sha(artifact)}
    if runtime_manifest is not None:
        manifest = json.loads(Path(runtime_manifest).read_text())
        if (manifest['release'] != wanted_release or manifest['artifact'] != artifact_seal
                or manifest['logical_seals'] != logical or manifest['counts'] != counts):
            raise ValueError('oracle Filmfest runtime manifest binding')
    return {'status': 'PASS', 'artifact': artifact_seal, 'counts': counts,
            'parser_build_id': parser, 'run_id': run, 'scope_sha256': scope_sha,
            'text_state_counts': dict(sorted(states.items())),
            'owner_role_counts': dict(sorted(roles.items())),
            'relation_state_counts': dict(sorted(relation_states.items())),
            'source_files': len(selected), 'physical_rows': sum(len(rows) for rows in physical.values()),
            'talk_candidates_source_rechecked': len(talk_candidates),
            'field_continuity': expected_mappings,
            'logical_seals': logical, 'schema_structure': reference_schema,
            'silent_skips': 0, 'no_extras_no_omissions': True}
