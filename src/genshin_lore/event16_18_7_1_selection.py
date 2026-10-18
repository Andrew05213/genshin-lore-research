"""7.1 Event16/18 physical selection, reusing accepted same-snapshot rows."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path

from .batch_release import assert_output_paths, create_private_staging, publish_absent, sidecars
from .event_7_1_source import array_records, check_files, sha

REPO = Path(__file__).resolve().parents[2]
SCHEMA = REPO / 'schemas/event16-18-7.1-physical-selection-v1.sql'
PHOTO = 'ExcelBinOutput/ActivityPhotographPosExcelConfigData.json'
ECHO = 'ExcelBinOutput/EchoShellExcelConfigData.json'
DIALOG = 'ExcelBinOutput/DialogExcelConfigData.json'
REMINDER = 'ExcelBinOutput/ReminderExcelConfigData.json'
TALK = 'ExcelBinOutput/TalkExcelConfigData_1.json'
SOURCE_PATHS = (PHOTO, ECHO, DIALOG, REMINDER, TALK)


def occurrence_id(*parts):
    payload = json.dumps(parts, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    return 'event16-18-71:' + hashlib.sha256(payload.encode()).hexdigest()[:40]


def _dependency(scope, key):
    item = scope['accepted_inputs'][key]
    path = Path(item['path_machine_local'])
    if path.stat().st_size != item['size_bytes'] or sha(path) != item['sha256']:
        raise ValueError('Event selection accepted input seal: ' + key)
    if sha(REPO / item['runtime_manifest']) != item['runtime_manifest_sha256']:
        raise ValueError('Event selection accepted runtime seal: ' + key)
    connection = sqlite3.connect('file:' + str(path.resolve()) + '?mode=ro&immutable=1', uri=True)
    table = 'event_narrative_release' if key == 'event15_photo' else 'release'
    column = 'manifest_json' if key == 'event15_photo' else 'payload'
    actual = json.loads(connection.execute(f'SELECT {column} FROM {table}').fetchone()[0])
    if actual != item['release']:
        raise ValueError('Event selection accepted release: ' + key)
    return connection, actual


def _raw(root, path):
    return list(array_records(root / path))


def project(root: Path, scope_path: Path, component: str):
    root, scope_path = Path(root), Path(scope_path)
    if component not in ('16', '18'):
        raise ValueError('Event selection component must be 16 or 18')
    scope = json.loads(scope_path.read_text())
    check_files(root, scope)
    if (scope['contract'] != 'event16-18-7.1-physical-selection-v1'
            or tuple(entry['path'] for entry in scope['files']) != SOURCE_PATHS
            or scope['historical_scope_sha256'] != {
                'event16_scope': sha(REPO / 'manifests/event-narrative/photo-echo-delta-scope.json'),
                'event18_scope': sha(REPO / 'manifests/event-narrative/generic-dialog-reminder-scope.json')}
            or scope['event15_scope_sha256'] != sha(REPO / scope['event15_scope'])):
        raise ValueError('Event selection exact historical/source contract')
    implementation = {path.relative_to(REPO).as_posix(): sha(path) for path in (
        Path(__file__), SCHEMA, REPO / 'src/genshin_lore/event_7_1_source.py',
        REPO / 'src/genshin_lore/event_narrative_source.py')}
    parser = occurrence_id('parser', implementation)
    run = occurrence_id('run', parser, component, sha(scope_path))
    photo_db, photo_release = _dependency(scope, 'event15_photo')
    dialog_db, dialog_release = _dependency(scope, 'dialog')
    reminder_db, reminder_release = _dependency(scope, 'reminder')
    try:
        photo_rows = _raw(root, PHOTO)
        echo_rows = _raw(root, ECHO)
        dialog_rows = _raw(root, DIALOG)
        reminder_rows = _raw(root, REMINDER)
        talk_rows = _raw(root, TALK)
        # Every accepted target is rebound by exact physical coordinate and raw SHA.
        covered_photo = {row[0]: (row[1], row[2], row[3]) for row in photo_db.execute(
            'SELECT ordinal,pointer,raw_sha256,id FROM event_narrative_row WHERE source_path=?', (PHOTO,))}
        photo_scope = json.loads((REPO / scope['event15_scope']).read_text())
        photo_spec = next(x for x in photo_scope['sources'] if x['path'] == PHOTO)
        expected_photo_ids = set(photo_spec['selected_native_ids'])
        if (len(covered_photo) != len(expected_photo_ids)
                or {row['id'] for ordinal, row, _ in photo_rows if ordinal in covered_photo}
                != expected_photo_ids):
            raise ValueError('Event15 photographed full physical selection mismatch')
        for ordinal, _, raw in photo_rows:
            if ordinal in covered_photo:
                pointer, digest, _ = covered_photo[ordinal]
                if pointer != '/' + str(ordinal) or digest != hashlib.sha256(raw.encode()).hexdigest():
                    raise ValueError('Event15 photo exact second hop')
        voice_ids = set()
        reminder_ids = set()
        for _, row, _ in echo_rows:
            for field, target in (('voiceList', voice_ids), ('reminderList', reminder_ids)):
                value = row.get(field, [])
                if not isinstance(value, list) or any(type(item) is not int for item in value):
                    raise ValueError('EchoShell source selection field shape: ' + field)
                target.update(value)
        dialog_lookup = {}
        for row in dialog_db.execute('SELECT ordinal,source_path,pointer,native_key,raw_sha256,evidence_ref FROM physical_row'):
            if row[1] != DIALOG or row[2] != '/' + str(row[0]):
                raise ValueError('Accepted Dialog physical coordinate')
            dialog_lookup[row[0]] = row
        reminder_lookup = {}
        for row in reminder_db.execute('SELECT ordinal,source_path,pointer,native_key,raw_sha256,evidence_ref FROM physical_row'):
            if row[1] != REMINDER or row[2] != '/' + str(row[0]):
                raise ValueError('Accepted Reminder physical coordinate')
            reminder_lookup[row[0]] = row
        if len(dialog_lookup) != len(dialog_rows) or len(reminder_lookup) != len(reminder_rows):
            raise ValueError('Accepted Dialog/Reminder full physical denominator')
        event_dialog = {ordinal for ordinal, row, _ in dialog_rows
                        if row.get('GFLDJMJKIKE') in voice_ids}
        event_reminder = {ordinal for ordinal, row, _ in reminder_rows
                          if row.get('id') in reminder_ids}
        event_talk = {ordinal for ordinal, row, _ in talk_rows
                      if row.get('initDialog') in voice_ids}
        event_photo = {ordinal for ordinal, _, _ in photo_rows} - set(covered_photo)
        if len(event_photo) + len(covered_photo) != len(photo_rows):
            raise ValueError('Event photo complement')
        if ({dialog_rows[index][1]['GFLDJMJKIKE'] for index in event_dialog} != voice_ids
                or {reminder_rows[index][1]['id'] for index in event_reminder} != reminder_ids):
            raise ValueError('EchoShell selector target namespace incomplete')
        selected = ((PHOTO, photo_rows, event_photo, 'photo_complement', None, None),
                    (DIALOG, dialog_rows, event_dialog if component == '16' else
                     set(range(len(dialog_rows))) - event_dialog, 'dialog', dialog_lookup, dialog_release),
                    (REMINDER, reminder_rows, event_reminder if component == '16' else
                     set(range(len(reminder_rows))) - event_reminder, 'reminder', reminder_lookup, reminder_release),
                    (TALK, talk_rows, event_talk, 'talk_excel_support', None, None))
        if component == '18':
            selected = selected[1:3]
        result = []
        kinds = Counter()
        for path, source_rows, indices, kind, lookup, dependency in selected:
            for ordinal, _, raw in source_rows:
                if ordinal not in indices:
                    continue
                pointer = '/' + str(ordinal)
                digest = hashlib.sha256(raw.encode()).hexdigest()
                target_family = target_contract = target_parser = target_run = target_id = None
                projected_raw = raw
                mode = 'local_raw'
                if lookup is not None:
                    observed = lookup.get(ordinal)
                    if observed is None or observed[2] != pointer or observed[4] != digest:
                        raise ValueError('Event accepted target second hop: ' + path + pointer)
                    mode, projected_raw = 'accepted_readthrough', None
                    target_family = dependency['family']
                    target_contract = dependency['contract']
                    target_parser = dependency['parser']
                    target_run = dependency['run']
                    target_id = observed[5]
                row_id = occurrence_id(run, path, pointer)
                result.append((row_id, run, component, kind, path, ordinal, pointer, digest,
                               mode, projected_raw, target_family, target_contract, target_parser,
                               target_run, target_id))
                kinds[kind] += 1
        result.sort()
        release = {
            'contract': scope['contract'], 'component': component, 'snapshot': scope['snapshot_id'],
            'parser_build_id': parser, 'ingest_run_id': run, 'scope_sha256': sha(scope_path),
            'implementation': implementation, 'counts': dict(sorted(kinds.items())),
            'selected_rows': len(result), 'source_denominator': {
                'photo': len(photo_rows), 'dialog': len(dialog_rows), 'reminder': len(reminder_rows),
                'talk_excel_1': len(talk_rows)},
            'event15_photo_covered': len(covered_photo),
            'echo_dialog_selector_values': len(voice_ids),
            'echo_reminder_selector_values': len(reminder_ids),
            'component16_dialog_selected': len(event_dialog),
            'component16_reminder_selected': len(event_reminder),
            'component16_talk_support': len(event_talk),
            'reminder_semantic_owner_mapping_unresolved': True,
            'historical_exact_replacement': False,
            'candidate_navigation_only': True,
        }
        return scope, result, release
    finally:
        photo_db.close()
        dialog_db.close()
        reminder_db.close()


def build(root: Path, scope_path: Path, component: str, output: Path):
    root, scope_path, output = Path(root).resolve(), Path(scope_path).resolve(), Path(output).absolute()
    scope = json.loads(scope_path.read_text())
    inputs = [('scope', scope_path), ('source_root', root), ('schema', SCHEMA)]
    inputs += [(str(i), root / item['path']) for i, item in enumerate(scope['files'])]
    inputs += [(name, Path(item['path_machine_local'])) for name, item in scope['accepted_inputs'].items()]
    assert_output_paths(output, inputs=inputs)
    _, rows, release = project(root, scope_path, component)
    fd, stage = create_private_staging(output, prefix='.event16-18-71-')
    inode = (stage.stat().st_dev, stage.stat().st_ino)
    connection = None
    try:
        os.close(fd)
        connection = sqlite3.connect(stage)
        connection.execute('PRAGMA foreign_keys=ON')
        connection.executescript(SCHEMA.read_text())
        connection.executemany('INSERT INTO selected_occurrence VALUES (' + ','.join('?' for _ in rows[0]) + ')', rows)
        connection.execute('INSERT INTO release VALUES(1,?)',
                           (json.dumps(release, sort_keys=True, separators=(',', ':')),))
        connection.commit()
        connection.execute('VACUUM')
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Event selection staging integrity')
        connection.close(); connection = None
        if any(path.exists() for path in sidecars(stage)):
            raise ValueError('Event selection SQLite sidecar')
        check_files(root, scope)
        seal = {'size_bytes': stage.stat().st_size, 'sha256': sha(stage)}
        publish_absent(output, stage, inputs=inputs)
        if output.stat().st_size != seal['size_bytes'] or sha(output) != seal['sha256']:
            raise ValueError('Event selection published seal drift')
        return {**release, 'artifact': seal}
    finally:
        if connection is not None:
            connection.close()
        if stage.exists() and (stage.stat().st_dev, stage.stat().st_ino) == inode:
            stage.unlink()
