"""Full 7.1 High-Value physical/text-candidate projection, without inferred field roles."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .batch_release import assert_output_paths, create_private_staging, publish_absent, sidecars
from .event_7_1_source import array_records, check_files, pairs, sha

REPO = Path(__file__).resolve().parents[2]
SCHEMA = REPO / 'schemas/native21-7.1-projection-v1.sql'
HISTORY = REPO / 'manifests/native-archive/high-value-open-selection.json'
EVENT16 = REPO / 'manifests/event-narrative-7.1/component-16-physical-runtime-v1.json'
QUEST_TALK = REPO / 'manifests/quest-talk-7.1/standalone-candidate.json'
def dependency_manifests(metadata_root=None):
    base = Path(metadata_root) if metadata_root is not None else REPO
    return base / EVENT16.relative_to(REPO), base / QUEST_TALK.relative_to(REPO)

TABLES = ('row_occurrence', 'text_slot', 'text_candidate', 'relation_locator', 'relation_candidate')


def oid(*parts):
    canonical = json.dumps(parts, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    return 'native26-71:' + hashlib.sha256(canonical).hexdigest()[:40]


def leaf_values(row, field, shape='scalar'):
    """Walk a source-declared JSON path; absent fields are not silently remapped."""
    parts = tuple(piece for piece in field.split('/') if piece)

    def walk(value, offset, pointer):
        if offset == len(parts):
            if shape == 'array' and isinstance(value, list):
                for ordinal, child in enumerate(value):
                    if child is not None:
                        yield pointer + '/' + str(ordinal), child
            elif shape == 'scalar' and value is not None:
                yield pointer, value
            return
        key = parts[offset]
        if key == '*' and isinstance(value, list):
            for ordinal, child in enumerate(value):
                yield from walk(child, offset + 1, pointer + '/' + str(ordinal))
        elif isinstance(value, dict) and key in value:
            escaped = key.replace('~', '~0').replace('/', '~1')
            yield from walk(value[key], offset + 1, pointer + '/' + escaped)

    yield from walk(row, 0, '')


def _accepted(scope, metadata_root=None):
    event_manifest, talk_manifest = dependency_manifests(metadata_root)
    event = json.loads(event_manifest.read_text())
    talk = json.loads(talk_manifest.read_text())
    talk_artifact = Path(talk['local_artifact_path'])
    event_path = Path(event['artifact']['path_machine_local'])
    if (sha(event_manifest) != scope['event16_manifest_sha256'] or
            event['artifact'] != scope['event16_artifact'] or
            event['parser_build_id'] != scope['event16_parser'] or
            event['ingest_run_id'] != scope['event16_run'] or
            (event_path.stat().st_size, sha(event_path)) !=
            (event['artifact']['size_bytes'], event['artifact']['sha256'])):
        raise ValueError('Native26 accepted Event16 identity')
    if (talk_artifact.stat().st_size, sha(talk_artifact)) != (
            talk['artifact']['size_bytes'], talk['artifact']['sha256']):
        raise ValueError('Native26 accepted Quest/Talk identity')
    return event, event_path, talk


def project(root: Path, scope_path: Path, *, metadata_root=None):
    scope = json.loads(scope_path.read_text())
    check_files(root, scope)
    historical = json.loads(HISTORY.read_text())
    expected_paths = [item['path'] for item in historical['sources']]
    expected_files = expected_paths + [historical['textmaps'][locale][tier]
                                       for locale in historical['languages'] for tier in ('main', 'medium')]
    if (scope['contract'] != 'native26-7.1-physical-projection-scope-v1'
            or sha(HISTORY) != scope['historical_selection_sha256']
            or len(scope['sources']) != 91
            or [source['path'] for source in scope['sources']] != expected_paths
            or [entry['path'] for entry in scope['files']] != expected_files
            or scope['textmaps'] != historical['textmaps']
            or scope['languages'] != historical['languages']
            or scope['semantic_owner_mapping_unresolved'] is not True
            or scope['same_named_historical_owner_roles_carried_as_raw_locators_only'] is not True):
        raise ValueError('Native26 historical/source boundary')
    for current, old in zip(scope['sources'], historical['sources']):
        physical_owner_fields = [{**owner, 'owner_role_status': 'mapping_unresolved'}
                                 for owner in old['text_owners']]
        if (current['boundary'] != old['boundary'] or current['kind'] != old['kind']
                or current.get('native_id_field') != old.get('native_id_field')
                or current['text_owners'] != physical_owner_fields
                or current.get('relations', []) != old.get('relations', [])
                or current['historical_field_classification'] != old['field_classification']):
            raise ValueError('Native26 per-source historical declaration drift: ' + current['path'])
    event, event_path, talk = _accepted(scope, metadata_root)
    implementation = {str(path.relative_to(REPO)): sha(path) for path in
                      (Path(__file__), SCHEMA, HISTORY)}
    implementation.update({str(original.relative_to(REPO)): sha(actual)
                           for original, actual in zip((EVENT16, QUEST_TALK), dependency_manifests(metadata_root))})
    parser = oid('parser', implementation)
    run = oid('run', parser, sha(scope_path), scope['snapshot_id'])
    event_reuse = {item['source_pointer']: item for item in scope['event16_reuse_targets']}
    if len(event_reuse) != 44:
        raise ValueError('Native26 Event16 reuse denominator')
    with sqlite3.connect(event_path) as db:
        event_release = json.loads(db.execute('SELECT manifest_json FROM release WHERE id=1').fetchone()[0])
        covered = {pointer: (rid, path, ordinal, digest)
                   for rid, path, pointer, ordinal, digest in db.execute(
            "SELECT id,source_path,pointer,ordinal,raw_sha256 FROM selected_occurrence WHERE source_kind='talk_excel_support'")}
    if event_release['contract'] != scope['event16_contract'] or covered != {pointer: (item['target_occurrence_id'], item['source_path'],
                            item['ordinal'], item['raw_sha256'])
                   for pointer, item in event_reuse.items()}:
        raise ValueError('Native26 Event16 complete exact coordinate binding')
    rows = []
    wanted_hashes = set()
    local_targets = defaultdict(set)
    missing_owner_slots = 0
    absent_relation_root_row_observations = 0
    for source in scope['sources']:
        path = source['path']
        physical = list(array_records(root / path))
        if len(physical) != source['source_row_count']:
            raise ValueError('Native26 physical denominator: ' + path)
        for owner in source['text_owners']:
            field = owner['field']
            root_key = field.split('/')[0]
            present = sum(root_key in record for _, record, _ in physical)
            if source['owner_root_presence'][field] != present:
                raise ValueError('Native26 TextMap owner root presence drift: ' + path + '/' + field)
        for ordinal, record, raw in physical:
            if not isinstance(record, dict):
                raise ValueError('Native26 raw source row shape')
            rows.append((source, ordinal, record, raw))
            for owner in source['text_owners']:
                if source['owner_root_presence'][owner['field']] == 0:
                    missing_owner_slots += 1
                    continue
                for _, value in leaf_values(record, owner['field'], owner.get('shape', 'scalar')):
                    if type(value) is not int:
                        raise ValueError('Native26 declared TextMap owner shape')
                    wanted_hashes.add(str(value))
            for relation in source.get('relations', ()):
                if relation['field'].split('/')[0] not in record:
                    absent_relation_root_row_observations += 1
        for other in scope['sources']:
            for relation in other.get('relations', ()):
                if relation['resolution'] == 'local' and path in relation['target_sources']:
                    local_targets[(path, relation['target_key'])].add(relation['relation_kind'])
    if len(rows) != scope['raw_physical_row_denominator'] or len(rows) != 107323:
        raise ValueError('Native26 full physical source denominator')
    expected_absent = sorted((source['path'], owner['field'])
                             for source in scope['sources'] for owner in source['text_owners']
                             if source['owner_root_presence'][owner['field']] == 0)
    declared_absent = sorted((item['source_path'], item['historical_owner_path'])
                             for item in scope['missing_historical_owner_specs'])
    if expected_absent != declared_absent:
        raise ValueError('Native26 missing historical owner inventory drift')
    local = defaultdict(list)
    target_key_present = defaultdict(bool)
    for source, ordinal, record, raw in rows:
        path, pointer = source['path'], '/' + str(ordinal)
        target_id = oid(run, path, pointer)
        for (target_path, key), kinds in local_targets.items():
            if target_path == path and key in record and record[key] is not None:
                target_key_present[(path, key)] = True
                local[(path, key, str(record[key]))].append((
                    'native_archive', 'native26-7.1-physical-text-candidate-v1', parser, run,
                    target_id, path, pointer, hashlib.sha256(raw.encode()).hexdigest()))
    talk_targets = defaultdict(list)
    with sqlite3.connect(talk['local_artifact_path']) as db:
        for rid, path, pointer, native, digest in db.execute(
                "SELECT id,source_path,pointer,native_id,raw_sha256 FROM physical_container WHERE kind='talk_root' AND ingest_run_id=?",
                (talk['ingest_run_id'],)):
            talk_targets[native].append(('quest_talk', talk['contract'], talk['parser_build_id'],
                talk['ingest_run_id'], rid, path, pointer, digest))
    maps = {}
    for locale in ('CHS', 'EN', 'JP'):
        for tier in ('main', 'medium'):
            path = scope['textmaps'][locale][tier]
            found = defaultdict(list)
            for pair_ordinal, (key, value) in enumerate(pairs(root / path)):
                if key in wanted_hashes:
                    found[key].append((path, pair_ordinal, value))
            maps[locale, tier] = found
    expected = {table: [] for table in TABLES}
    text_states, relation_states = Counter(), Counter()
    for source, ordinal, record, raw in rows:
        path, pointer = source['path'], '/' + str(ordinal)
        occurrence = oid(run, path, pointer)
        raw_sha = hashlib.sha256(raw.encode()).hexdigest()
        native_field = source.get('native_id_field')
        native = str(record[native_field]) if native_field and native_field in record else None
        reuse = event_reuse.get(pointer) if path == 'ExcelBinOutput/TalkExcelConfigData_1.json' else None
        if reuse:
            if raw_sha != reuse['raw_sha256']:
                raise ValueError('Native26 Event16 source second hop')
            embedded = None
            binding = ('event_narrative_archive', scope['event16_contract'], event['parser_build_id'],
                       event['ingest_run_id'], reuse['target_occurrence_id'])
        else:
            embedded = raw
            binding = (None,) * 5
        expected['row_occurrence'].append((occurrence, run, source['boundary'], path, ordinal,
                                           pointer, native_field, native, embedded, raw_sha, *binding))
        for owner in source['text_owners']:
            if source['owner_root_presence'][owner['field']] == 0:
                continue
            for owner_pointer, value in leaf_values(record, owner['field'], owner.get('shape', 'scalar')):
                locator = str(value)
                role = owner['owner_role_status']
                owner_path = owner_pointer.lstrip('/')
                for locale in ('CHS', 'EN', 'JP'):
                    main = maps[locale, 'main'].get(locator, ())
                    medium = maps[locale, 'medium'].get(locator, ())
                    hits = [('main', *hit) for hit in main] if main else [('medium', *hit) for hit in medium]
                    state = ('missing' if not hits else 'resolved_candidate_set' if len(hits) > 1
                             else 'resolved_unique_main' if main else 'resolved_unique_medium')
                    text_states[state] += 1
                    slot = oid(run, path, pointer, owner_path, locale)
                    expected['text_slot'].append((slot, occurrence, owner_path, role, locale,
                                                  locator, state, len(hits)))
                    for tier, source_path, pair_ordinal, text in hits:
                        expected['text_candidate'].append((oid(run, slot, source_path, pair_ordinal), slot,
                            source_path, tier, pair_ordinal, '/' + locator, text,
                            hashlib.sha256(text.encode()).hexdigest()))
        for relation in source.get('relations', ()):
            if relation['field'].split('/')[0] not in record:
                continue
            for rel_pointer, value in leaf_values(record, relation['field'], relation.get('shape', 'scalar')):
                locator = str(value)
                if relation['resolution'] == 'local':
                    complete = all(target_key_present[(target_path, relation['target_key'])]
                                   for target_path in relation['target_sources'])
                    targets = [candidate for target_path in relation['target_sources']
                               for candidate in local[(target_path, relation['target_key'], locator)]] if complete else []
                elif relation['resolution'] == 'quest_talk_v7':
                    complete = True
                    targets = talk_targets[locator]
                    for candidate in targets:
                        if candidate[6] != '/' or sha(root / candidate[5]) != candidate[7]:
                            raise ValueError('Native26 Quest/Talk exact source second hop')
                else:
                    complete = False
                    targets = []
                targets.sort()
                status = ('mapping_unresolved' if not complete else 'missing' if not targets
                          else 'resolved_unique' if len(targets) == 1 else 'resolved_candidate_set')
                relation_states[status] += 1
                source_field = rel_pointer.lstrip('/')
                rid = oid(run, path, pointer, source_field, relation['relation_kind'])
                expected['relation_locator'].append((rid, occurrence, source_field, relation['relation_kind'],
                                                     locator, status, len(targets)))
                for candidate in targets:
                    expected['relation_candidate'].append((oid(run, rid, candidate), rid, *candidate))
    for table in TABLES:
        expected[table].sort()
    release = {
        'contract': 'native26-7.1-physical-text-candidate-v1',
        'snapshot': scope['snapshot_id'], 'scope_sha256': sha(scope_path),
        'parser_build_id': parser, 'ingest_run_id': run, 'implementation': implementation,
        'counts': {table: len(expected[table]) for table in TABLES},
        'text_states': dict(sorted(text_states.items())),
        'relation_states': dict(sorted(relation_states.items())),
        'full_source_rows': len(rows), 'event16_reuse_rows': 44,
        'missing_historical_owner_specs': len(scope['missing_historical_owner_specs']),
        'unprojectable_historical_owner_row_slots': missing_owner_slots,
        'semantic_owner_mapping_unresolved': True,
        'absent_declared_relation_root_row_observations': absent_relation_root_row_observations,
        'historical_exact_replacement': False, 'product_accepted': False,
    }
    return scope, expected, release


def build(root: Path, scope_path: Path, output: Path, *, metadata_root=None):
    event_manifest, talk_manifest = dependency_manifests(metadata_root)
    root, scope_path, output = Path(root).resolve(), Path(scope_path).resolve(), Path(output).absolute()
    scope = json.loads(scope_path.read_text())
    inputs = [('source_root', root), ('scope', scope_path), ('schema', SCHEMA),
              ('history', HISTORY), ('event16', event_manifest), ('quest_talk', talk_manifest)]
    inputs += [(str(i), root / item['path']) for i, item in enumerate(scope['files'])]
    assert_output_paths(output, inputs=inputs)
    _, rows, release = project(root, scope_path, metadata_root=metadata_root)
    fd, stage = create_private_staging(output, prefix='.native26-71-')
    owned = stage.stat().st_dev, stage.stat().st_ino
    connection = None
    try:
        os.close(fd)
        connection = sqlite3.connect(stage)
        connection.execute('PRAGMA foreign_keys=ON')
        connection.executescript(SCHEMA.read_text())
        for table in TABLES:
            if rows[table]:
                connection.executemany('INSERT INTO ' + table + ' VALUES (' +
                                       ','.join('?' for _ in rows[table][0]) + ')', rows[table])
        connection.execute('INSERT INTO release VALUES(1,?)',
                           (json.dumps(release, sort_keys=True, separators=(',', ':')),))
        connection.commit()
        connection.execute('VACUUM')
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or connection.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Native26 SQLite integrity/FK')
        connection.close()
        connection = None
        if any(path.exists() for path in sidecars(stage)):
            raise ValueError('Native26 SQLite sidecar')
        check_files(root, scope)
        seal = {'size_bytes': stage.stat().st_size, 'sha256': sha(stage)}
        publish_absent(output, stage, inputs=inputs)
        return {**release, 'artifact': seal}
    finally:
        if connection is not None:
            connection.close()
        if stage.exists() and (stage.stat().st_dev, stage.stat().st_ino) == owned:
            stage.unlink()
