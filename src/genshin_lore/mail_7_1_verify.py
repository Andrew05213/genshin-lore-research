"""Independent raw-source-to-rowset oracle for sealed 7.1 Mail evidence."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from .mail_archive_source import array_records, pairs, sha

ROOT = Path(__file__).resolve().parents[2]
SOURCE = '9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99'
TREE = 'aa578c08c402af39b41f1ffc95124d10f5410272'
SNAPSHOT = 'anime-game-data:7.1:' + SOURCE
TABLES = ('mail_row', 'mail_slot', 'mail_text', 'mail_relation', 'mail_candidate')


def key(*parts: object) -> str:
    payload = json.dumps(parts, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()
    return 'mail71:' + hashlib.sha256(payload).hexdigest()[:40]


def rows(db: sqlite3.Connection, table: str) -> list[tuple]:
    fields = [record[1] for record in db.execute('PRAGMA table_info(' + table + ')')]
    return [tuple(record) for record in db.execute('SELECT ' + ','.join(fields) + ' FROM ' + table +
                                                    ' ORDER BY ' + ','.join(fields))]


def verify(source: Path, scope: Path, artifact: Path, report: dict) -> dict:
    source, scope, artifact = Path(source), Path(scope), Path(artifact)
    manifest = json.loads(scope.read_text())
    if (manifest['source_commit'], manifest['source_tree'], manifest['source_snapshot']) != (SOURCE, TREE, SNAPSHOT):
        raise ValueError('Mail oracle source identity')
    if (subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip(),
            subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD^{tree}'], text=True).strip()) != (SOURCE, TREE):
        raise ValueError('Mail oracle checkout identity')
    if subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain', '--untracked-files=all'], text=True).strip():
        raise ValueError('Mail oracle source dirty')
    if len(manifest['files']) != 9:
        raise ValueError('Mail oracle file denominator')
    for item in manifest['files']:
        file = source / item['path']
        actual_blob = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD:' + item['path']], text=True).strip()
        if file.is_symlink() or file.stat().st_size != item['size_bytes'] or sha(file) != item['sha256'] or actual_blob != item['blob']:
            raise ValueError('Mail oracle source file: ' + item['path'])
    if artifact.stat().st_size != report['artifact']['size_bytes'] or sha(artifact) != report['artifact']['sha256']:
        raise ValueError('Mail oracle artifact seal')
    impl = {path: sha(ROOT / path) for path in
            ('src/genshin_lore/mail_7_1_build.py', 'src/genshin_lore/mail_archive_source.py',
             'schemas/mail-archive-7.1-v2.sql')}
    parser = key('parser', impl, manifest['contract'])
    run = key('run', parser, sha(scope), SNAPSHOT)
    if (report['implementation'], report['parser_build_id'], report['run_id'], report['snapshot_id']) != (impl, parser, run, SNAPSHOT):
        raise ValueError('Mail oracle parser/run seal')
    specs = tuple(zip(('mail_template', 'birthday_schedule', 'alchemy_sim_letter'), manifest['primary_sources']))
    input_rows = {name: list(array_records(source / path)) for name, path in specs}
    if {name: len(input_rows[name]) for name, _ in specs} != manifest['row_denominator']:
        raise ValueError('Mail oracle primary denominator')
    expected: dict[str, list[tuple]] = {table: [] for table in TABLES}
    owner_values: set[str] = set()
    occurrence_ids: dict[tuple[str, int], str] = {}
    for name, path in specs:
        for position, record, lexical in input_rows[name]:
            if type(record.get('id')) is not int:
                raise ValueError('Mail oracle source ID')
            occurrence = key(run, 'row', path, position)
            occurrence_ids[name, position] = occurrence
            expected['mail_row'].append((occurrence, run, name, position, str(record['id']), path,
                                         '/' + str(position), lexical, hashlib.sha256(lexical.encode()).hexdigest()))
            for field in manifest['text_fields_by_source'].get(name, []):
                if type(record.get(field)) is not int:
                    raise ValueError('Mail oracle locator: ' + path + '/' + str(position) + '/' + field)
                owner_values.add(str(record[field]))
    lookup: dict[str, list[dict[str, list[tuple]]]] = {}
    for locale in ('CHS', 'EN', 'JP'):
        parts = []
        for suffix in ('', '_Medium'):
            path = 'TextMap/TextMap' + suffix + locale + '.json'
            index: dict[str, list[tuple]] = defaultdict(list)
            for pair, (text_hash, value) in enumerate(pairs(source / path)):
                if text_hash in owner_values:
                    index[text_hash].append((path, pair, '/' + text_hash, value))
            parts.append(index)
        lookup[locale] = parts
    states: Counter[str] = Counter()
    for name, path in specs:
        for position, record, _ in input_rows[name]:
            for field in manifest['text_fields_by_source'].get(name, []):
                value = str(record[field])
                for locale in ('CHS', 'EN', 'JP'):
                    main = lookup[locale][0].get(value, [])
                    medium = lookup[locale][1].get(value, [])
                    physical = main if main else medium
                    status = ('missing' if not physical else 'resolved_main' if main and len(physical) == 1 else
                              'resolved_medium' if medium and len(physical) == 1 else 'ambiguous')
                    states[status] += 1
                    slot = key(run, 'slot', path, position, field, locale)
                    expected['mail_slot'].append((slot, occurrence_ids[name, position], field,
                                                  locale, value, status, len(physical)))
                    for text_file, pair, pointer, text in physical:
                        expected['mail_text'].append((key(run, 'text', path, position, field, locale, text_file, pair),
                                                      slot, text_file, pair, pointer, text,
                                                      hashlib.sha256(text.encode()).hexdigest()))
    templates: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for position, record, lexical in input_rows['mail_template']:
        templates[str(record['id'])].append((position, hashlib.sha256(lexical.encode()).hexdigest()))
    birthday = dict(specs)['birthday_schedule']
    target = dict(specs)['mail_template']
    relation_states: Counter[str] = Counter()
    for position, record, _ in input_rows['birthday_schedule']:
        if type(record.get('mailId')) is not int:
            raise ValueError('Mail oracle birthday locator')
        matches = templates.get(str(record['mailId']), [])
        state = 'missing' if not matches else 'resolved_unique' if len(matches) == 1 else 'ambiguous'
        relation_states[state] += 1
        relation = key(run, 'relation', birthday, position, 'mailId')
        expected['mail_relation'].append((relation, occurrence_ids['birthday_schedule', position],
                                          'birthday_schedule_to_mail_template', '/' + str(position) + '/mailId',
                                          str(record['mailId']), 'MailExcelConfigData.id physical rows',
                                          state, len(matches), 1))
        for target_position, target_sha in matches:
            expected['mail_candidate'].append((key(run, 'candidate', birthday, position, target, target_position),
                                               relation, occurrence_ids['mail_template', target_position], target,
                                               target_position, '/' + str(target_position), target_sha))
    db = sqlite3.connect('file:' + str(artifact.resolve()) + '?mode=ro&immutable=1', uri=True)
    try:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Mail oracle SQLite integrity/FK')
        expected_schema = sqlite3.connect(':memory:')
        try:
            expected_schema.executescript((ROOT / 'schemas/mail-archive-7.1-v2.sql').read_text())
            structure_sql = "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            if db.execute(structure_sql).fetchall() != expected_schema.execute(structure_sql).fetchall():
                raise ValueError('Mail oracle schema/guards differ')
        finally:
            expected_schema.close()
        for table in TABLES:
            if sorted(expected[table]) != rows(db, table):
                raise ValueError('Mail oracle full rowset mismatch: ' + table)
        release = json.loads(db.execute('SELECT manifest_json FROM mail_release WHERE id=1').fetchone()[0])
    finally:
        db.close()
    if any(Path(str(artifact) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise ValueError('Mail oracle sidecar')
    counts = {table: len(values) for table, values in expected.items()}
    expected_release = {'contract': 'mail-narrative-7.1-versioned-evidence-v2', 'run_id': run,
                        'parser_build_id': parser, 'scope_sha256': sha(scope), 'implementation': impl,
                        'snapshot_id': SNAPSHOT, 'counts': counts,
                        'text_state_counts': dict(sorted(states.items())),
                        'relation_state_counts': dict(sorted(relation_states.items()))}
    if release != expected_release or {key: value for key, value in report.items() if key != 'artifact'} != expected_release:
        raise ValueError('Mail oracle release differs')
    return {'status': 'PASS', 'full_rowsets': counts, 'text_states': dict(sorted(states.items())),
            'relation_states': dict(sorted(relation_states.items())), 'source_files': 9,
            'schema_guard_definitions': 'PASS', 'sidecars_absent': True,
            'logical_seals': {table: hashlib.sha256(json.dumps(sorted(values), ensure_ascii=False,
                                                              separators=(',', ':')).encode()).hexdigest()
                              for table, values in expected.items()}}
