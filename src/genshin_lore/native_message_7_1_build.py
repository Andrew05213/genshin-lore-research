"""7.1 Native Message physical/text projection with exact MainQuest candidates."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .event_7_1_source import array_records, check_files, pairs, sha

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / 'schemas/native-message-7.1-v1.sql'
TABLES = ('native71_row', 'native71_slot', 'native71_text', 'native71_relation', 'native71_candidate')


def ident(*parts: object) -> str:
    return 'nmsg71:' + hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True,
                                               separators=(',', ':')).encode()).hexdigest()[:40]


def project(source: Path, scope_path: Path) -> tuple[dict, dict[str, list[tuple]], dict]:
    scope = json.loads(scope_path.read_text())
    if 'local_rebuild' in scope:
        from .native_message_7_1_binding import qualify_scope
        qualify_scope(source, scope_path, scope)
    elif scope != json.loads((ROOT / 'manifests/native-archive-7.1/message-scope-v1.json').read_text()):
        raise ValueError('Native Message historical scope must retain its exact seal')
    check_files(source, scope)
    historical_path = ROOT / scope['historical_selection']
    historical = json.loads(historical_path.read_text())
    adapted = json.loads(json.dumps(historical['sources']))
    for spec in adapted:
        if spec['path'].endswith('/ActivityLoliGolfMessageExcelConfigData.json'):
            candidate = [owner for owner in spec['text_owners'] if owner['field'] == 'GLPIOOHJNFF']
            if len(candidate) != 1 or candidate[0]['owner_role_status'] != 'mapping_unresolved':
                raise ValueError('Native Message historical raw field contract')
            candidate[0].update({'historical_field': 'GLPIOOHJNFF', 'field': 'DDBKODKACJA',
                                 'field_continuity_status': 'full_20_row_raw_value_alignment;semantic_role_unresolved'})
    exact_paths = {spec['path'] for spec in adapted}
    exact_paths.update(historical['textmaps'][locale][tier] for locale in historical['languages']
                       for tier in ('main', 'medium'))
    if (scope['contract'] != 'native-message-7.1-exact-source-scope-v1'
            or scope['historical_selection_sha256'] != sha(historical_path)
            or scope['sources'] != adapted or scope['languages'] != historical['languages']
            or scope['textmaps'] != historical['textmaps']
            or {item['path'] for item in scope['files']} != exact_paths
            or scope['raw_field_alignment']['semantic_role'] != 'mapping_unresolved'):
        raise ValueError('Native Message 7.1 exact historical selector/field boundary')
    dependency = scope['dependency']
    dependency_path = Path(dependency['target_artifact_machine_local'])
    if dependency_path.stat().st_size != dependency['artifact']['size_bytes'] or sha(dependency_path) != dependency['artifact']['sha256']:
        raise ValueError('Native Message 7.1 MainQuest target artifact seal')
    target = sqlite3.connect(dependency_path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        target.row_factory = sqlite3.Row
        if target.execute("SELECT count(*) FROM excel_row WHERE kind='main_quest'").fetchone()[0] != dependency['physical_target_denominator']:
            raise ValueError('Native Message 7.1 MainQuest denominator')
        by_native = defaultdict(list)
        for item in target.execute("SELECT id,run_id,source_path,pointer,native_id,raw_sha256 FROM excel_row WHERE kind='main_quest'"):
            if item['run_id'] != dependency['run']:
                raise ValueError('Native Message 7.1 target wrong run')
            by_native[item['native_id']].append(dict(item))
        external = []
        for raw in sorted({str(row[relation['field']]) for spec in scope['sources'] for relation in spec.get('relations', [])
                           for _, row, _ in array_records(source / spec['path'])}):
            for item in by_native[raw]:
                external.append({'raw_locator': raw, 'target_occurrence_id': item['id'],
                                 'target_run': item['run_id'], 'target_source_path': item['source_path'],
                                 'target_source_pointer': item['pointer'], 'target_native_id': item['native_id'],
                                 'target_raw_sha256': item['raw_sha256']})
        if sorted(external, key=lambda x: (x['raw_locator'], x['target_occurrence_id'])) != sorted(
                scope['external_candidates'], key=lambda x: (x['raw_locator'], x['target_occurrence_id'])):
            raise ValueError('Native Message 7.1 complete target set drift')
    finally:
        target.close()
    impl = {name: sha(ROOT / name) for name in (
        'src/genshin_lore/native_message_7_1_build.py', 'src/genshin_lore/event_7_1_source.py',
        'schemas/native-message-7.1-v1.sql')}
    parser = ident('parser', impl)
    run = ident('run', parser, sha(scope_path))
    seals = {item['path']: item for item in scope['files']}
    observed = {}
    wanted = set()
    rows: dict[str, list[tuple]] = {table: [] for table in TABLES}
    states = Counter(); relation_states = Counter(); owner_count = 0
    for spec in scope['sources']:
        path = spec['path']; physical = list(array_records(source / path))
        if len(physical) != scope['row_denominator'][path]:
            raise ValueError('Native Message 7.1 row denominator: ' + path)
        observed[path] = physical
        for ordinal, row, raw in physical:
            if not isinstance(row, dict):
                raise ValueError('Native Message 7.1 non-object row')
            pointer = '/' + str(ordinal); rid = ident(run, 'row', path, pointer)
            native_field = spec.get('native_id_field')
            if native_field is not None and type(row.get(native_field)) is not int:
                raise ValueError('Native Message 7.1 native field: ' + path + pointer)
            native = str(row[native_field]) if native_field is not None else None
            seal = seals[path]
            rows['native71_row'].append((rid, run, spec['boundary'], path, seal['sha256'], seal['blob'],
                                         ordinal, pointer, native_field, native, raw, hashlib.sha256(raw.encode()).hexdigest()))
            for owner in spec['text_owners']:
                field = owner['field']; value = row.get(field)
                if type(value) is not int:
                    raise ValueError('Native Message 7.1 owner field: ' + path + pointer + '/' + field)
                wanted.add(str(value)); owner_count += 1
    maps = defaultdict(list)
    for locale in scope['languages']:
        for tier in ('main', 'medium'):
            path = scope['textmaps'][locale][tier]
            for ordinal, (locator, text) in enumerate(pairs(source / path)):
                if locator in wanted:
                    maps[(locale, tier, locator)].append((path, ordinal, '/' + locator, text))
    by_locator = defaultdict(list)
    for item in scope['external_candidates']:
        by_locator[item['raw_locator']].append(item)
    for spec in scope['sources']:
        path = spec['path']
        for ordinal, row, _ in observed[path]:
            pointer = '/' + str(ordinal); rid = ident(run, 'row', path, pointer)
            for owner in spec['text_owners']:
                field = owner['field']; locator = str(row[field])
                for locale in scope['languages']:
                    main = maps[(locale, 'main', locator)]
                    medium = maps[(locale, 'medium', locator)]
                    chosen = main or medium
                    status = ('missing' if not chosen else 'resolved_candidate_set' if len(chosen) > 1 else
                              'resolved_main' if main else 'resolved_medium')
                    states[status] += 1
                    sid = ident(run, 'slot', path, pointer, field, locale)
                    rows['native71_slot'].append((sid, rid, field, owner.get('historical_field'),
                                                  owner['owner_role_status'], locale, locator, status,
                                                  len(main) + len(medium), len(chosen)))
                    for tier, matches in (('main', main), ('medium', medium)):
                        for textpath, pair, textpointer, text in matches:
                            tseal = seals[textpath]
                            rows['native71_text'].append((ident(run, 'text', sid, textpath, pair), sid, textpath,
                                                          tseal['sha256'], tseal['blob'], tier, pair, textpointer,
                                                          text, hashlib.sha256(text.encode()).hexdigest(),
                                                          int((tier == 'main' and bool(main)) or
                                                              (tier == 'medium' and not main))))
            for relation in spec.get('relations', []):
                field = relation['field']; locator = row.get(field)
                if type(locator) is not int:
                    raise ValueError('Native Message 7.1 relation: ' + path + pointer + '/' + field)
                matches = sorted(by_locator[str(locator)], key=lambda item: item['target_occurrence_id'])
                status = 'missing' if not matches else 'resolved_unique' if len(matches) == 1 else 'resolved_candidate_set'
                relation_states[status] += 1
                at = pointer + '/' + field
                relationid = ident(run, 'relation', path, at, relation['relation_kind'])
                rows['native71_relation'].append((relationid, rid, relation['relation_kind'], field, at,
                                                  str(locator), relation['candidate_namespace'], status,
                                                  len(matches), 1))
                for candidate in matches:
                    rows['native71_candidate'].append((ident(run, 'candidate', relationid, candidate['target_occurrence_id']),
                                                       relationid, dependency['family'], dependency['contract'],
                                                       dependency['parser'], dependency['run'],
                                                       candidate['target_occurrence_id'], candidate['target_source_path'],
                                                       candidate['target_source_pointer'], candidate['target_native_id'],
                                                       candidate['target_raw_sha256']))
    for table in TABLES:
        rows[table].sort()
    release = {'contract': 'native-message-7.1-versioned-evidence-v1', 'historical_contract': historical['family_contract_version'],
               'source_snapshot': scope['snapshot_id'], 'parser_build_id': parser, 'run_id': run,
               'scope_sha256': sha(scope_path), 'implementation': impl,
               'counts': {table: len(rows[table]) for table in TABLES}, 'physical_rows': sum(map(len, observed.values())),
               'owner_count': owner_count, 'text_states': dict(sorted(states.items())),
               'relation_states': dict(sorted(relation_states.items())),
               'dependency': {k: v for k, v in dependency.items() if k != 'target_artifact_machine_local'},
               'source_owned_mapping_role': 'mapping_unresolved', 'product_accepted': False}
    return scope, rows, release


def build(source: Path, scope_path: Path, output: Path) -> dict:
    source, scope_path, output = Path(source), Path(scope_path), Path(output)
    scope, rows, release = project(source, scope_path)
    sidecars = [Path(str(output) + suffix) for suffix in ('-wal', '-shm', '-journal')]
    if output.exists() or any(item.exists() for item in sidecars):
        raise FileExistsError('Native Message 7.1 output/sidecar')
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd); owned = output.stat(); db = None
    try:
        db = sqlite3.connect(output)
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(SCHEMA.read_text())
        for table in TABLES:
            if rows[table]:
                db.executemany('INSERT INTO ' + table + ' VALUES(' + ','.join('?' for _ in rows[table][0]) + ')', rows[table])
        db.execute('INSERT INTO native71_release VALUES(1,?)',
                   (json.dumps(release, ensure_ascii=False, sort_keys=True, separators=(',', ':')),))
        db.commit()
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Native Message 7.1 FK/integrity')
        db.execute('VACUUM')
        db.close(); db = None
        check_files(source, scope)
        return {**release, 'artifact': {'size_bytes': output.stat().st_size, 'sha256': sha(output)}}
    except Exception:
        if db is not None:
            db.close()
        existing = output.stat() if output.exists() else None
        if existing and (existing.st_dev, existing.st_ino) == (owned.st_dev, owned.st_ino):
            output.unlink()
        for item in sidecars:
            if item.exists():
                item.unlink()
        raise
