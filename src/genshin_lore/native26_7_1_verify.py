"""Independent full-denominator Native26 source-to-SQL oracle (no builder import)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .event_7_1_source import array_records, check_files, pairs, sha

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / 'schemas/native21-7.1-projection-v1.sql'
HISTORY = ROOT / 'manifests/native-archive/high-value-open-selection.json'
EVENT = ROOT / 'manifests/event-narrative-7.1/component-16-physical-runtime-v1.json'
TALK = ROOT / 'manifests/quest-talk-7.1/standalone-candidate.json'
TABLES = ('row_occurrence', 'text_slot', 'text_candidate', 'relation_locator', 'relation_candidate')


def key(*values):
    data = json.dumps(values, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    return 'native26-71:' + hashlib.sha256(data).hexdigest()[:40]


def source_path_values(record, path, shape):
    parts = [part for part in path.split('/') if part]
    stack = [(record, 0, '')]
    while stack:
        current, index, pointer = stack.pop()
        if index == len(parts):
            if shape == 'array' and isinstance(current, list):
                for pos, value in enumerate(current):
                    if value is not None:
                        yield pointer + '/' + str(pos), value
            elif shape == 'scalar' and current is not None:
                yield pointer, current
            continue
        token = parts[index]
        if token == '*' and isinstance(current, list):
            for pos in reversed(range(len(current))):
                stack.append((current[pos], index + 1, pointer + '/' + str(pos)))
        elif isinstance(current, dict) and token in current:
            escaped = token.replace('~', '~0').replace('/', '~1')
            stack.append((current[token], index + 1, pointer + '/' + escaped))


def schema_objects(db, schema_name='main'):
    return db.execute("SELECT type,name,tbl_name,sql FROM " + schema_name +
                      ".sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()


def mutation_cases(artifact):
    actual = sqlite3.connect('file:' + str(artifact.resolve()) + '?mode=ro', uri=True)
    replica = sqlite3.connect(':memory:')
    actual.backup(replica)
    actual.close()
    count = 0
    try:
        for setting in ('OFF', 'ON'):
            replica.execute('PRAGMA recursive_triggers=' + setting)
            for table in (*TABLES, 'release'):
                columns = [part[1] for part in replica.execute('PRAGMA table_info(' + table + ')')]
                row = replica.execute('SELECT * FROM ' + table + ' LIMIT 1').fetchone()
                if row is None:
                    raise ValueError('Native26 mutation fixture absent: ' + table)
                altered = list(row)
                altered[0] = '__native26_new_primary__'
                attempts = [
                    ('INSERT INTO ' + table + ' VALUES (' + ','.join('?' for _ in columns) + ')', tuple(altered)),
                    ('INSERT OR REPLACE INTO ' + table + ' VALUES (' + ','.join('?' for _ in columns) + ')', row),
                    ('UPDATE ' + table + ' SET ' + columns[0] + '=? WHERE ' + columns[0] + '=?', ('__changed__', row[0])),
                    ('DELETE FROM ' + table + ' WHERE ' + columns[0] + '=?', (row[0],)),
                    ('INSERT INTO ' + table + ' VALUES (' + ','.join('?' for _ in columns) +
                     ') ON CONFLICT(' + columns[0] + ') DO UPDATE SET ' + columns[0] +
                     '=excluded.' + columns[0], row),
                ]
                if table != 'release':
                    attempts.append(('INSERT OR REPLACE INTO ' + table + ' VALUES (' +
                                     ','.join('?' for _ in columns) + ')', tuple(altered)))
                for statement, args in attempts:
                    replica.execute('SAVEPOINT guarded')
                    try:
                        replica.execute(statement, args)
                        raise ValueError('Native26 sealed write succeeded: ' + table)
                    except sqlite3.DatabaseError as error:
                        if 'immutable' not in str(error):
                            raise ValueError('Native26 write failure not from immutable guard: ' + str(error)) from error
                        count += 1
                    finally:
                        replica.execute('ROLLBACK TO guarded')
                        replica.execute('RELEASE guarded')
    finally:
        replica.close()
    return count


def verify(root: Path, scope_path: Path, artifact: Path, *, metadata_root=None):
    metadata = Path(metadata_root) if metadata_root is not None else ROOT
    event_manifest = metadata / EVENT.relative_to(ROOT)
    talk_manifest = metadata / TALK.relative_to(ROOT)
    root, scope_path, artifact = Path(root), Path(scope_path), Path(artifact)
    scope = json.loads(scope_path.read_text())
    check_files(root, scope)
    old = json.loads(HISTORY.read_text())
    source_paths = [declaration['path'] for declaration in old['sources']]
    expected_file_paths = source_paths + [old['textmaps'][language][tier]
                                          for language in old['languages'] for tier in ('main', 'medium')]
    if (scope['contract'] != 'native26-7.1-physical-projection-scope-v1'
            or sha(HISTORY) != scope['historical_selection_sha256']
            or len(scope['sources']) != 91 or len(scope['files']) != 97
            or [row['path'] for row in scope['sources']] != source_paths
            or [entry['path'] for entry in scope['files']] != expected_file_paths
            or scope['languages'] != old['languages'] or scope['textmaps'] != old['textmaps']
            or scope['semantic_owner_mapping_unresolved'] is not True
            or scope['same_named_historical_owner_roles_carried_as_raw_locators_only'] is not True):
        raise ValueError('Native26 oracle source selection')
    for declaration, previous in zip(scope['sources'], old['sources']):
        physical_owners = [{**owner, 'owner_role_status': 'mapping_unresolved'}
                           for owner in previous['text_owners']]
        if (declaration['boundary'] != previous['boundary']
                or declaration['kind'] != previous['kind']
                or declaration.get('native_id_field') != previous.get('native_id_field')
                or declaration['text_owners'] != physical_owners
                or declaration.get('relations', []) != previous.get('relations', [])
                or declaration['historical_field_classification'] != previous['field_classification']):
            raise ValueError('Native26 independent declaration mismatch: ' + declaration['path'])
    if any(Path(str(artifact) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise ValueError('Native26 SQLite sidecar')
    event = json.loads(event_manifest.read_text())
    talk = json.loads(talk_manifest.read_text())
    event_path = Path(event['artifact']['path_machine_local'])
    talk_path = Path(talk['local_artifact_path'])
    for path, seal in ((event_path, event['artifact']), (talk_path, talk['artifact'])):
        if (path.stat().st_size, sha(path)) != (seal['size_bytes'], seal['sha256']):
            raise ValueError('Native26 oracle accepted input artifact drift')
    if sha(event_manifest) != scope['event16_manifest_sha256']:
        raise ValueError('Native26 oracle Event16 binding seal')
    code_hashes = {str(path.relative_to(ROOT)): sha(path) for path in (
        ROOT / 'src/genshin_lore/native26_7_1_physical.py', SCHEMA, HISTORY)}
    code_hashes.update({str(path.relative_to(ROOT)): sha(metadata / path.relative_to(ROOT)) for path in (EVENT, TALK)})
    parser = key('parser', code_hashes)
    run = key('run', parser, sha(scope_path), scope['snapshot_id'])
    source = []
    hashes = set()
    target_specs = defaultdict(set)
    missing_owner_specs = []
    missing_owner_rows = 0
    absent_relation_root_rows = 0
    for declaration in scope['sources']:
        path = declaration['path']
        members = list(array_records(root / path))
        if len(members) != declaration['source_row_count']:
            raise ValueError('Native26 source array row denominator: ' + path)
        for owner in declaration['text_owners']:
            field_name = owner['field']
            root_key = field_name.split('/')[0]
            observed = sum(root_key in row for _, row, _ in members)
            if declaration['owner_root_presence'][field_name] != observed:
                raise ValueError('Native26 independent owner presence mismatch: ' + path + '/' + field_name)
        for ordinal, row, raw in members:
            if not isinstance(row, dict):
                raise ValueError('Native26 source row shape')
            source.append((declaration, ordinal, row, raw))
            for owner in declaration['text_owners']:
                top = owner['field'].split('/')[0]
                if top not in row and declaration['owner_root_presence'][owner['field']] == 0:
                    missing_owner_rows += 1
                    continue
                for _, value in source_path_values(row, owner['field'], owner.get('shape', 'scalar')):
                    if type(value) is not int:
                        raise ValueError('Native26 source owner primitive type')
                    hashes.add(str(value))
            for relation in declaration.get('relations', ()):
                if relation['field'].split('/')[0] not in row:
                    absent_relation_root_rows += 1
        for owner in declaration['text_owners']:
            if declaration['owner_root_presence'][owner['field']] == 0:
                missing_owner_specs.append((path, owner['field']))
        for relation in declaration.get('relations', ()):
            if relation['resolution'] == 'local':
                for target_path in relation['target_sources']:
                    target_specs[(target_path, relation['target_key'])].add(relation['relation_kind'])
    if len(source) != scope['raw_physical_row_denominator'] or len(source) != 107323:
        raise ValueError('Native26 source denominator')
    if sorted(missing_owner_specs) != sorted((row['source_path'], row['historical_owner_path'])
                                           for row in scope['missing_historical_owner_specs']):
        raise ValueError('Native26 missing owner path inventory')
    expected_local = defaultdict(list)
    key_present = defaultdict(bool)
    for declaration, ordinal, row, raw in source:
        path = declaration['path']
        pointer = '/' + str(ordinal)
        for (target_path, target_key) in target_specs:
            if path == target_path and target_key in row and row[target_key] is not None:
                key_present[(path, target_key)] = True
                value = str(row[target_key])
                expected_local[path, target_key, value].append((
                    'native_archive', 'native26-7.1-physical-text-candidate-v1', parser, run,
                    key(run, path, pointer), path, pointer, hashlib.sha256(raw.encode()).hexdigest()))
    talk_index = defaultdict(list)
    with sqlite3.connect(talk_path) as db:
        for ref, path, pointer, native, digest in db.execute(
                "SELECT id,source_path,pointer,native_id,raw_sha256 FROM physical_container WHERE kind='talk_root' AND ingest_run_id=?",
                (talk['ingest_run_id'],)):
            talk_index[native].append(('quest_talk', talk['contract'], talk['parser_build_id'],
                talk['ingest_run_id'], ref, path, pointer, digest))
    with sqlite3.connect(event_path) as db:
        event_release = json.loads(db.execute('SELECT manifest_json FROM release WHERE id=1').fetchone()[0])
        covered = {pointer: (ref, path, ordinal, digest) for ref, path, pointer, ordinal, digest in db.execute(
            "SELECT id,source_path,pointer,ordinal,raw_sha256 FROM selected_occurrence WHERE source_kind='talk_excel_support'")}
    declared_cover = {item['source_pointer']: (item['target_occurrence_id'], item['source_path'],
                                               item['ordinal'], item['raw_sha256'])
                      for item in scope['event16_reuse_targets']}
    if (event_release['contract'] != scope['event16_contract'] or len(declared_cover) != 44
            or covered != declared_cover):
        raise ValueError('Native26 Event16 reuse candidate set')
    textmap = {}
    for locale in ('CHS', 'EN', 'JP'):
        for tier in ('main', 'medium'):
            path = scope['textmaps'][locale][tier]
            found = defaultdict(list)
            for ordinal, (locator, text) in enumerate(pairs(root / path)):
                if locator in hashes:
                    found[locator].append((path, ordinal, text))
            textmap[locale, tier] = found
    oracle = sqlite3.connect(':memory:')
    oracle.execute('PRAGMA foreign_keys=ON')
    oracle.executescript(SCHEMA.read_text())
    counts = Counter()
    text_state = Counter()
    relation_state = Counter()

    def insert(table, values):
        oracle.execute('INSERT INTO ' + table + ' VALUES (' + ','.join('?' for _ in values) + ')', values)
        counts[table] += 1

    for declaration, ordinal, row, raw in source:
        path, pointer = declaration['path'], '/' + str(ordinal)
        occurrence = key(run, path, pointer)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        native_field = declaration.get('native_id_field')
        native = str(row[native_field]) if native_field and native_field in row else None
        cover = declared_cover.get(pointer) if path == 'ExcelBinOutput/TalkExcelConfigData_1.json' else None
        if cover:
            if cover[3] != digest or cover[1] != path or cover[2] != ordinal:
                raise ValueError('Native26 Event16 source raw SHA')
            row_raw = None
            binding = ('event_narrative_archive', scope['event16_contract'], scope['event16_parser'],
                       scope['event16_run'], cover[0])
        else:
            row_raw = raw
            binding = (None,) * 5
        insert('row_occurrence', (occurrence, run, declaration['boundary'], path, ordinal, pointer,
                                  native_field, native, row_raw, digest, *binding))
        for owner in declaration['text_owners']:
            if declaration['owner_root_presence'][owner['field']] == 0:
                continue
            for owner_path, value in source_path_values(row, owner['field'], owner.get('shape', 'scalar')):
                locator = str(value)
                for locale in ('CHS', 'EN', 'JP'):
                    main = textmap[locale, 'main'].get(locator, ())
                    medium = textmap[locale, 'medium'].get(locator, ())
                    selected = [('main', *item) for item in main] if main else [('medium', *item) for item in medium]
                    state = ('missing' if not selected else 'resolved_candidate_set' if len(selected) > 1
                             else 'resolved_unique_main' if main else 'resolved_unique_medium')
                    text_state[state] += 1
                    field = owner_path.lstrip('/')
                    slot = key(run, path, pointer, field, locale)
                    insert('text_slot', (slot, occurrence, field, owner['owner_role_status'], locale,
                                         locator, state, len(selected)))
                    for tier, source_path, pair_ordinal, text in selected:
                        insert('text_candidate', (key(run, slot, source_path, pair_ordinal), slot,
                            source_path, tier, pair_ordinal, '/' + locator, text,
                            hashlib.sha256(text.encode()).hexdigest()))
        for relation in declaration.get('relations', ()):
            if relation['field'].split('/')[0] not in row:
                continue
            for path_part, raw_value in source_path_values(row, relation['field'], relation.get('shape', 'scalar')):
                raw_locator = str(raw_value)
                if relation['resolution'] == 'local':
                    complete = all(key_present[(target_path, relation['target_key'])]
                                   for target_path in relation['target_sources'])
                    candidates = ([target for target_path in relation['target_sources']
                                   for target in expected_local[target_path, relation['target_key'], raw_locator]]
                                  if complete else [])
                elif relation['resolution'] == 'quest_talk_v7':
                    complete = True
                    candidates = talk_index[raw_locator]
                    for target in candidates:
                        if target[6] != '/' or sha(root / target[5]) != target[7]:
                            raise ValueError('Native26 Quest/Talk target second hop')
                else:
                    complete = False
                    candidates = []
                candidates = sorted(candidates)
                status = ('mapping_unresolved' if not complete else 'missing' if not candidates
                          else 'resolved_unique' if len(candidates) == 1 else 'resolved_candidate_set')
                relation_state[status] += 1
                field = path_part.lstrip('/')
                ref = key(run, path, pointer, field, relation['relation_kind'])
                insert('relation_locator', (ref, occurrence, field, relation['relation_kind'], raw_locator,
                                            status, len(candidates)))
                for target in candidates:
                    insert('relation_candidate', (key(run, ref, target), ref, *target))
    release = {
        'contract': 'native26-7.1-physical-text-candidate-v1',
        'snapshot': scope['snapshot_id'], 'scope_sha256': sha(scope_path),
        'parser_build_id': parser, 'ingest_run_id': run, 'implementation': code_hashes,
        'counts': {table: counts[table] for table in TABLES},
        'text_states': dict(sorted(text_state.items())),
        'relation_states': dict(sorted(relation_state.items())),
        'full_source_rows': len(source), 'event16_reuse_rows': 44,
        'missing_historical_owner_specs': len(missing_owner_specs),
        'unprojectable_historical_owner_row_slots': missing_owner_rows,
        'semantic_owner_mapping_unresolved': True,
        'absent_declared_relation_root_row_observations': absent_relation_root_rows,
        'historical_exact_replacement': False, 'product_accepted': False,
    }
    insert('release', (1, json.dumps(release, sort_keys=True, separators=(',', ':'))))
    if oracle.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Native26 oracle FK')
    oracle.commit()
    oracle.execute('ATTACH DATABASE ? AS actual', ('file:' + str(artifact.resolve()) + '?mode=ro&immutable=1',))
    if schema_objects(oracle, 'main') != schema_objects(oracle, 'actual'):
        raise ValueError('Native26 schema/index/trigger mismatch')
    if oracle.execute('PRAGMA actual.integrity_check').fetchone()[0] != 'ok' or oracle.execute('PRAGMA actual.foreign_key_check').fetchall():
        raise ValueError('Native26 artifact integrity/FK')
    for table in (*TABLES, 'release'):
        extra = oracle.execute('SELECT * FROM actual.' + table + ' EXCEPT SELECT * FROM main.' + table + ' LIMIT 1').fetchone()
        absent = oracle.execute('SELECT * FROM main.' + table + ' EXCEPT SELECT * FROM actual.' + table + ' LIMIT 1').fetchone()
        if extra or absent:
            raise ValueError('Native26 no-extra/no-omission rowset: ' + table)
    oracle.close()
    negatives = mutation_cases(artifact)
    return {'status': 'PASS', 'verified': True, 'artifact': {'size_bytes': artifact.stat().st_size, 'sha256': sha(artifact)},
            'counts': release['counts'], 'text_states': release['text_states'],
            'relation_states': release['relation_states'], 'event16_exact_reuse': 44,
            'unprojectable_historical_owner_row_slots': missing_owner_rows,
            'schema_exact': True, 'source_to_sql_rowset_exact': True,
            'mutation_cases': negatives, 'silent_skips': 0, 'wrong_identity_merges': 0}
