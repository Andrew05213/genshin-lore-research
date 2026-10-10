"""Independent source/accepted-artifact oracle for Event16/18 coordinate sets."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

from .event_7_1_source import check_files, sha
from .event13_7_1_physical_verify import source_array

REPO = Path(__file__).resolve().parents[2]
SCHEMA = REPO / 'schemas/event16-18-7.1-physical-selection-v1.sql'
PHOTO = 'ExcelBinOutput/ActivityPhotographPosExcelConfigData.json'
ECHO = 'ExcelBinOutput/EchoShellExcelConfigData.json'
DIALOG = 'ExcelBinOutput/DialogExcelConfigData.json'
REMINDER = 'ExcelBinOutput/ReminderExcelConfigData.json'
TALK = 'ExcelBinOutput/TalkExcelConfigData_1.json'


def independent_id(*parts):
    value = json.dumps(parts, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()
    return 'event16-18-71:' + hashlib.sha256(value).hexdigest()[:40]


def objects(db):
    return sorted(db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                             "WHERE name NOT LIKE 'sqlite_%'").fetchall())


def accepted_rows(scope, kind, table, column):
    item = scope['accepted_inputs'][kind]
    path = Path(item['path_machine_local'])
    if path.stat().st_size != item['size_bytes'] or sha(path) != item['sha256']:
        raise ValueError('Event selector target seal: ' + kind)
    if sha(REPO / item['runtime_manifest']) != item['runtime_manifest_sha256']:
        raise ValueError('Event selector target runtime seal: ' + kind)
    db = sqlite3.connect('file:' + str(path.resolve()) + '?mode=ro&immutable=1', uri=True)
    release = json.loads(db.execute(f'SELECT {column} FROM {table}').fetchone()[0])
    if release != item['release'] or release.get('snapshot', release.get('snapshot_id')) != scope['snapshot_id']:
        raise ValueError('Event selector target release: ' + kind)
    return db, release


def immutable_matrix(db):
    scratch = sqlite3.connect(':memory:')
    db.backup(scratch)
    tests = 0
    for enabled in ('OFF', 'ON'):
        scratch.execute('PRAGMA recursive_triggers=' + enabled)
        for table in ('selected_occurrence', 'release'):
            cols = [row[1] for row in scratch.execute('PRAGMA table_info(' + table + ')')]
            record = list(scratch.execute('SELECT * FROM ' + table + ' LIMIT 1').fetchone())
            fresh = record.copy()
            fresh[0] = '__new_pk__' if table != 'release' else 2
            marks = ','.join('?' for _ in cols)
            collist = ','.join(cols)
            statements = [
                (f'INSERT INTO {table}({collist}) VALUES({marks})', fresh),
                (f'INSERT OR REPLACE INTO {table}({collist}) VALUES({marks})', record),
                (f'INSERT OR REPLACE INTO {table}({collist}) VALUES({marks})', fresh),
                (f'UPDATE {table} SET {cols[0]}=? WHERE {cols[0]}=?', [record[0], record[0]]),
                (f'DELETE FROM {table} WHERE {cols[0]}=?', [record[0]]),
                (f'INSERT INTO {table}({collist}) VALUES({marks}) '
                 f'ON CONFLICT({cols[0]}) DO UPDATE SET {cols[0]}=excluded.{cols[0]}', record),
            ]
            for sql, args in statements:
                try:
                    scratch.execute(sql, args)
                except sqlite3.DatabaseError as exc:
                    if 'immutable' not in str(exc):
                        raise AssertionError('wrong mutation rejection: ' + str(exc)) from exc
                else:
                    raise AssertionError('unprotected mutation: ' + sql)
                finally:
                    scratch.rollback()
                tests += 1
    scratch.close()
    return tests


def verify(root: Path, scope_path: Path, artifact: Path):
    root, scope_path, artifact = Path(root), Path(scope_path), Path(artifact)
    scope = json.loads(scope_path.read_text())
    check_files(root, scope)
    if tuple(x['path'] for x in scope['files']) != (PHOTO, ECHO, DIALOG, REMINDER, TALK):
        raise ValueError('Event selector source boundary')
    if scope['historical_scope_sha256'] != {
        'event16_scope': sha(REPO / 'manifests/event-narrative/photo-echo-delta-scope.json'),
        'event18_scope': sha(REPO / 'manifests/event-narrative/generic-dialog-reminder-scope.json')}:
        raise ValueError('Event selector historical scope seal')
    if scope['event15_scope_sha256'] != sha(REPO / scope['event15_scope']):
        raise ValueError('Event selector covered-photo scope seal')
    if any(Path(str(artifact) + extension).exists() for extension in ('-wal', '-shm', '-journal')):
        raise ValueError('Event selector sidecar present')
    db = sqlite3.connect('file:' + str(artifact.resolve()) + '?mode=ro&immutable=1', uri=True)
    schema = sqlite3.connect(':memory:')
    schema.executescript(SCHEMA.read_text())
    if objects(db) != objects(schema):
        raise ValueError('Event selector schema/index/trigger definitions')
    schema.close()
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Event selector integrity/FK')
    release = json.loads(db.execute('SELECT manifest_json FROM release WHERE id=1').fetchone()[0])
    component = release['component']
    if component not in ('16', '18') or release['scope_sha256'] != sha(scope_path):
        raise ValueError('Event selector release scope')
    expected_implementation = {
        name: sha(REPO / name) for name in (
            'src/genshin_lore/event16_18_7_1_selection.py',
            'schemas/event16-18-7.1-physical-selection-v1.sql',
            'src/genshin_lore/event_7_1_source.py',
            'src/genshin_lore/event_narrative_source.py')}
    if release['implementation'] != expected_implementation:
        raise ValueError('Event selector parser implementation')
    parser = independent_id('parser', expected_implementation)
    run = independent_id('run', parser, component, sha(scope_path))
    if release['parser_build_id'] != parser or release['ingest_run_id'] != run:
        raise ValueError('Event selector parser/run binding')

    photo = list(source_array(root / PHOTO))
    echo = list(source_array(root / ECHO))
    dialog = list(source_array(root / DIALOG))
    reminder = list(source_array(root / REMINDER))
    talk = list(source_array(root / TALK))
    photo_db, photo_release = accepted_rows(scope, 'event15_photo', 'event_narrative_release', 'manifest_json')
    dialog_db, dialog_release = accepted_rows(scope, 'dialog', 'release', 'payload')
    reminder_db, reminder_release = accepted_rows(scope, 'reminder', 'release', 'payload')
    try:
        covered = {}
        for ordinal, pointer, digest in photo_db.execute(
                'SELECT ordinal,pointer,raw_sha256 FROM event_narrative_row WHERE source_path=?', (PHOTO,)):
            covered[ordinal] = (pointer, digest)
        declared = json.loads((REPO / scope['event15_scope']).read_text())
        photo_ids = set(next(item['selected_native_ids'] for item in declared['sources']
                             if item['path'] == PHOTO))
        if len(covered) != len(photo_ids) or {photo[index][1]['id'] for index in covered} != photo_ids:
            raise ValueError('Event selector covered photo set')
        for index, (pointer, digest) in covered.items():
            if pointer != '/' + str(index) or digest != hashlib.sha256(photo[index][2].encode()).hexdigest():
                raise ValueError('Event selector photo accepted second hop')
        dialogs = set()
        reminders = set()
        for _, data, _ in echo:
            for key, namespace in (('voiceList', dialogs), ('reminderList', reminders)):
                values = data.get(key, [])
                if not isinstance(values, list) or any(type(x) is not int for x in values):
                    raise ValueError('Event selector EchoShell list shape')
                namespace.update(values)
        wanted_dialog = {ordinal for ordinal, data, _ in dialog if data.get('GFLDJMJKIKE') in dialogs}
        wanted_reminder = {ordinal for ordinal, data, _ in reminder if data.get('id') in reminders}
        wanted_talk = {ordinal for ordinal, data, _ in talk if data.get('initDialog') in dialogs}
        if {dialog[i][1]['GFLDJMJKIKE'] for i in wanted_dialog} != dialogs:
            raise ValueError('Event selector Dialog target-space incomplete')
        if {reminder[i][1]['id'] for i in wanted_reminder} != reminders:
            raise ValueError('Event selector Reminder target-space incomplete')
        target_rows = {}
        for path, source_rows, target_db, target_release in (
                (DIALOG, dialog, dialog_db, dialog_release),
                (REMINDER, reminder, reminder_db, reminder_release)):
            recorded = list(target_db.execute('SELECT ordinal,source_path,pointer,raw_sha256,evidence_ref '
                                              'FROM physical_row'))
            if len(recorded) != len(source_rows):
                raise ValueError('Event selector accepted full target denominator')
            for ordinal, path_value, pointer, digest, reference in recorded:
                if (path_value != path or pointer != '/' + str(ordinal)
                        or digest != hashlib.sha256(source_rows[ordinal][2].encode()).hexdigest()):
                    raise ValueError('Event selector accepted target second hop')
                target_rows[path, ordinal] = (reference, target_release)
        groups = ((PHOTO, photo, set(range(len(photo))) - set(covered), 'photo_complement'),
                  (DIALOG, dialog, wanted_dialog if component == '16' else
                   set(range(len(dialog))) - wanted_dialog, 'dialog'),
                  (REMINDER, reminder, wanted_reminder if component == '16' else
                   set(range(len(reminder))) - wanted_reminder, 'reminder'),
                  (TALK, talk, wanted_talk, 'talk_excel_support'))
        if component == '18':
            groups = groups[1:3]
        expected = []
        counts = Counter()
        for path, rows, selected, kind in groups:
            for ordinal, _, raw in rows:
                if ordinal not in selected:
                    continue
                pointer = '/' + str(ordinal)
                digest = hashlib.sha256(raw.encode()).hexdigest()
                mode = 'local_raw'
                raw_value = raw
                family = contract = target_parser = target_run = target_occurrence = None
                if path in (DIALOG, REMINDER):
                    target_occurrence, target_release = target_rows[path, ordinal]
                    mode, raw_value = 'accepted_readthrough', None
                    family, contract = target_release['family'], target_release['contract']
                    target_parser, target_run = target_release['parser'], target_release['run']
                expected.append((independent_id(run, path, pointer), run, component, kind, path,
                                 ordinal, pointer, digest, mode, raw_value, family, contract,
                                 target_parser, target_run, target_occurrence))
                counts[kind] += 1
        columns = [item[1] for item in db.execute('PRAGMA table_info(selected_occurrence)')]
        actual = db.execute('SELECT ' + ','.join(columns) + ' FROM selected_occurrence').fetchall()
        if sorted(expected) != sorted(actual):
            raise ValueError('Event selector independent source-to-rowset mismatch')
        if (dict(sorted(counts.items())) != release['counts'] or len(expected) != release['selected_rows']
                or release['reminder_semantic_owner_mapping_unresolved'] is not True
                or release['historical_exact_replacement'] is not False):
            raise ValueError('Event selector release counts/semantic boundary')
        mutations = immutable_matrix(db)
        return {'verified': True, 'component': component, 'counts': dict(sorted(counts.items())),
                'selected_rows': len(expected), 'accepted_target_rows_rechecked': len(dialog) + len(reminder),
                'mutation_cases': mutations, 'artifact': {
                    'size_bytes': artifact.stat().st_size, 'sha256': sha(artifact)}}
    finally:
        photo_db.close(); dialog_db.close(); reminder_db.close(); db.close()
