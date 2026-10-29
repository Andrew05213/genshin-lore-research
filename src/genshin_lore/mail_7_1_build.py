"""Snapshot-local 7.1 Mail successor; never reads 7.0 text or evidence rows."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from .mail_archive_source import array_records, pairs, sha

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = 'anime-game-data:7.1:9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99'
TREE = 'aa578c08c402af39b41f1ffc95124d10f5410272'
TABLES = ('mail_row', 'mail_slot', 'mail_text', 'mail_relation', 'mail_candidate')


def ident(*parts: object) -> str:
    raw = json.dumps(parts, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()
    return 'mail71:' + hashlib.sha256(raw).hexdigest()[:40]


def implementation() -> dict[str, str]:
    return {path: sha(ROOT / path) for path in
            ('src/genshin_lore/mail_7_1_build.py', 'src/genshin_lore/mail_archive_source.py',
             'schemas/mail-archive-7.1-v2.sql')}


def source_check(root: Path, manifest: dict) -> None:
    if manifest['source_snapshot'] != SNAPSHOT or manifest['source_tree'] != TREE:
        raise ValueError('Mail 7.1 scope pin')
    if subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip() != SNAPSHOT.rsplit(':', 1)[1]:
        raise ValueError('Mail 7.1 checkout HEAD')
    if subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD^{tree}'], text=True).strip() != TREE:
        raise ValueError('Mail 7.1 checkout tree')
    if subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=all'], text=True).strip():
        raise ValueError('Mail 7.1 source dirty')
    for entry in manifest['files']:
        path = root / entry['path']
        if path.is_symlink() or path.stat().st_size != entry['size_bytes'] or sha(path) != entry['sha256']:
            raise ValueError('Mail 7.1 source bytes: ' + entry['path'])
        blob = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD:' + entry['path']], text=True).strip()
        if blob != entry['blob']:
            raise ValueError('Mail 7.1 source blob: ' + entry['path'])


def build(root: Path, scope: Path, output: Path) -> dict:
    root, scope, output = Path(root), Path(scope), Path(output)
    manifest = json.loads(scope.read_text())
    source_check(root, manifest)
    impl = implementation()
    parser = ident('parser', impl, manifest['contract'])
    run = ident('run', parser, sha(scope), SNAPSHOT)
    sidecars = [Path(str(output) + suffix) for suffix in ('-wal', '-shm', '-journal')]
    if output.exists() or any(path.exists() for path in sidecars):
        raise FileExistsError('Mail 7.1 artifact or sidecar exists')
    specs = tuple(zip(('mail_template', 'birthday_schedule', 'alchemy_sim_letter'), manifest['primary_sources']))
    physical = {kind: list(array_records(root / path)) for kind, path in specs}
    if {kind: len(physical[kind]) for kind, _ in specs} != manifest['row_denominator']:
        raise ValueError('Mail 7.1 physical denominator')
    wanted = {str(row[field]) for kind, fields in manifest['text_fields_by_source'].items()
              for _, row, _ in physical[kind] for field in fields}
    maps: dict[str, list[dict[str, list[tuple]]]] = {}
    for locale in manifest['languages']:
        tiers = []
        for suffix in ('', '_Medium'):
            path = f'TextMap/TextMap{suffix}{locale}.json'
            hits: dict[str, list[tuple]] = defaultdict(list)
            for ordinal, (key, value) in enumerate(pairs(root / path)):
                if key in wanted:
                    hits[key].append((path, ordinal, '/' + key, value))
            tiers.append(hits)
        maps[locale] = tiers
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb'):
        pass
    owned = output.stat()
    conn = None
    try:
        conn = sqlite3.connect(output)
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript((ROOT / 'schemas/mail-archive-7.1-v2.sql').read_text())
        states: Counter[str] = Counter()
        ids: dict[tuple[str, int], str] = {}
        for kind, path in specs:
            for ordinal, row, raw in physical[kind]:
                if type(row.get('id')) is not int:
                    raise ValueError('Mail 7.1 native id: ' + path + '/' + str(ordinal))
                rid = ident(run, 'row', path, ordinal)
                ids[kind, ordinal] = rid
                conn.execute('INSERT INTO mail_row VALUES(?,?,?,?,?,?,?,?,?)',
                             (rid, run, kind, ordinal, str(row['id']), path, '/' + str(ordinal), raw,
                              hashlib.sha256(raw.encode()).hexdigest()))
                for field in manifest['text_fields_by_source'].get(kind, []):
                    if type(row.get(field)) is not int:
                        raise ValueError('Mail 7.1 text locator: ' + path + '/' + str(ordinal) + '/' + field)
                    locator = str(row[field])
                    for locale in manifest['languages']:
                        main, medium = (tier.get(locator, []) for tier in maps[locale])
                        hits = main or medium
                        status = ('missing' if not hits else 'ambiguous' if len(hits) != 1 else
                                  'resolved_main' if main else 'resolved_medium')
                        states[status] += 1
                        sid = ident(run, 'slot', path, ordinal, field, locale)
                        conn.execute('INSERT INTO mail_slot VALUES(?,?,?,?,?,?,?)',
                                     (sid, rid, field, locale, locator, status, len(hits)))
                        for source_path, pair, pointer, value in hits:
                            conn.execute('INSERT INTO mail_text VALUES(?,?,?,?,?,?,?)',
                                         (ident(run, 'text', path, ordinal, field, locale, source_path, pair),
                                          sid, source_path, pair, pointer, value, hashlib.sha256(value.encode()).hexdigest()))
        templates: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for ordinal, row, raw in physical['mail_template']:
            templates[str(row['id'])].append((ordinal, hashlib.sha256(raw.encode()).hexdigest()))
        birthday_path = dict(specs)['birthday_schedule']
        target_path = dict(specs)['mail_template']
        for ordinal, row, _ in physical['birthday_schedule']:
            if type(row.get('mailId')) is not int:
                raise ValueError('Mail 7.1 birthday locator: ' + str(ordinal))
            hits = templates.get(str(row['mailId']), [])
            status = 'missing' if not hits else 'resolved_unique' if len(hits) == 1 else 'ambiguous'
            rel = ident(run, 'relation', birthday_path, ordinal, 'mailId')
            conn.execute('INSERT INTO mail_relation VALUES(?,?,?,?,?,?,?,?,?)',
                         (rel, ids['birthday_schedule', ordinal], 'birthday_schedule_to_mail_template',
                          '/' + str(ordinal) + '/mailId', str(row['mailId']),
                          'MailExcelConfigData.id physical rows', status, len(hits), 1))
            for target_ordinal, raw_sha in hits:
                conn.execute('INSERT INTO mail_candidate VALUES(?,?,?,?,?,?,?)',
                             (ident(run, 'candidate', birthday_path, ordinal, target_path, target_ordinal),
                              rel, ids['mail_template', target_ordinal], target_path, target_ordinal,
                              '/' + str(target_ordinal), raw_sha))
        counts = {table: conn.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in TABLES}
        release = {'contract': 'mail-narrative-7.1-versioned-evidence-v2', 'run_id': run,
                   'parser_build_id': parser, 'scope_sha256': sha(scope), 'implementation': impl,
                   'snapshot_id': SNAPSHOT, 'counts': counts,
                   'text_state_counts': dict(sorted(states.items())),
                   'relation_state_counts': dict(conn.execute('SELECT status,count(*) FROM mail_relation GROUP BY status'))}
        conn.execute('INSERT INTO mail_release VALUES(1,?)', (json.dumps(release, sort_keys=True),))
        conn.commit()
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or conn.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Mail 7.1 SQLite integrity')
        conn.execute('VACUUM')
        source_check(root, manifest)
        return {**release, 'artifact': {'size_bytes': output.stat().st_size, 'sha256': sha(output)}}
    except Exception:
        if conn is not None:
            conn.close()
            conn = None
        raise
    finally:
        if conn is not None:
            conn.close()
