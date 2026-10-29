"""Independent source-to-rowset oracle for 7.1 Native Message.

This module deliberately does not import the builder or its projection helpers.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .event_7_1_source import array_records, check_files, pairs, sha

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / 'schemas/native-message-7.1-v1.sql'
TABLES = ('native71_row', 'native71_slot', 'native71_text', 'native71_relation', 'native71_candidate')


def identity(*values: object) -> str:
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return 'nmsg71:' + hashlib.sha256(payload).hexdigest()[:40]


def table_rows(db: sqlite3.Connection, table: str) -> list[tuple]:
    fields = [record[1] for record in db.execute('PRAGMA table_info(' + table + ')')]
    return sorted(db.execute('SELECT ' + ','.join(fields) + ' FROM ' + table).fetchall())


def verify(source: Path, scope_path: Path, artifact: Path, report: dict) -> dict:
    source, scope_path, artifact = Path(source), Path(scope_path), Path(artifact)
    scope = json.loads(scope_path.read_text())
    if 'local_rebuild' in scope:
        from .native_message_7_1_binding import qualify_scope
        qualify_scope(source, scope_path, scope)
    elif scope != json.loads((ROOT / 'manifests/native-archive-7.1/message-scope-v1.json').read_text()):
        raise ValueError('Native Message historical scope must retain its exact seal')
    check_files(source, scope)
    if any(Path(str(artifact) + end).exists() for end in ('-wal', '-shm', '-journal')):
        raise ValueError('Native Message sidecar')
    if artifact.stat().st_size != report['artifact']['size_bytes'] or sha(artifact) != report['artifact']['sha256']:
        raise ValueError('Native Message artifact seal')
    historical_path = ROOT / scope['historical_selection']
    old = json.loads(historical_path.read_text())
    declarations = json.loads(json.dumps(old['sources']))
    for declaration in declarations:
        if declaration['path'].endswith('/ActivityLoliGolfMessageExcelConfigData.json'):
            owners = [x for x in declaration['text_owners'] if x['field'] == 'GLPIOOHJNFF']
            if len(owners) != 1 or owners[0]['owner_role_status'] != 'mapping_unresolved':
                raise ValueError('Native Message old mapping-unresolved owner')
            owners[0].update({'historical_field': 'GLPIOOHJNFF', 'field': 'DDBKODKACJA',
                              'field_continuity_status': 'full_20_row_raw_value_alignment;semantic_role_unresolved'})
    selected = {item['path'] for item in declarations}
    selected.update(old['textmaps'][loc][tier] for loc in old['languages'] for tier in ('main', 'medium'))
    if (scope['contract'] != 'native-message-7.1-exact-source-scope-v1'
            or scope['historical_selection_sha256'] != sha(historical_path)
            or declarations != scope['sources'] or old['languages'] != scope['languages']
            or old['textmaps'] != scope['textmaps'] or selected != {x['path'] for x in scope['files']}):
        raise ValueError('Native Message source selection drift')
    implementation = {name: sha(ROOT / name) for name in (
        'src/genshin_lore/native_message_7_1_build.py', 'src/genshin_lore/event_7_1_source.py',
        'schemas/native-message-7.1-v1.sql')}
    parser = identity('parser', implementation)
    run = identity('run', parser, sha(scope_path))
    if report['parser_build_id'] != parser or report['run_id'] != run:
        raise ValueError('Native Message parser/run')

    dependency = scope['dependency']
    if report.get('dependency') != {k: v for k, v in dependency.items() if k != 'target_artifact_machine_local'}:
        raise ValueError('Native Message reported dependency binding')
    target_path = Path(dependency['target_artifact_machine_local'])
    if target_path.stat().st_size != dependency['artifact']['size_bytes'] or sha(target_path) != dependency['artifact']['sha256']:
        raise ValueError('Native Message dependency seal')
    target = sqlite3.connect(target_path.as_uri() + '?mode=ro&immutable=1', uri=True)
    target.row_factory = sqlite3.Row
    targets: dict[str, list[dict]] = defaultdict(list)
    try:
        for record in target.execute("SELECT id,run_id,source_path,pointer,native_id,raw_sha256 FROM excel_row WHERE kind='main_quest'"):
            if record['run_id'] != dependency['run']:
                raise ValueError('Native Message dependency run')
            targets[record['native_id']].append(dict(record))
        if sum(map(len, targets.values())) != dependency['physical_target_denominator']:
            raise ValueError('Native Message dependency denominator')
    finally:
        target.close()
    seal_by_path = {x['path']: x for x in scope['files']}
    expected = {name: [] for name in TABLES}
    source_rows: dict[str, list[tuple]] = {}
    hashes = set()
    for declaration in declarations:
        path = declaration['path']
        physical = list(array_records(source / path))
        if len(physical) != scope['row_denominator'][path]:
            raise ValueError('Native Message row denominator: ' + path)
        source_rows[path] = physical
        for ordinal, obj, raw in physical:
            if not isinstance(obj, dict):
                raise ValueError('Native Message source row type')
            pointer = '/' + str(ordinal)
            native_field = declaration.get('native_id_field')
            if native_field is not None and type(obj.get(native_field)) is not int:
                raise ValueError('Native Message native type')
            seal = seal_by_path[path]
            expected['native71_row'].append((
                identity(run, 'row', path, pointer), run, declaration['boundary'], path,
                seal['sha256'], seal['blob'], ordinal, pointer, native_field,
                str(obj[native_field]) if native_field else None, raw, hashlib.sha256(raw.encode()).hexdigest()))
            for owner in declaration['text_owners']:
                field = owner['field']
                if type(obj.get(field)) is not int:
                    raise ValueError('Native Message TextMap locator type: ' + path + pointer + '/' + field)
                hashes.add(str(obj[field]))
    external = []
    locators = {str(obj[relation['field']]) for declaration in declarations
                for relation in declaration.get('relations', [])
                for _, obj, _ in source_rows[declaration['path']]}
    for locator in sorted(locators):
        for candidate in targets[locator]:
            external.append({'raw_locator': locator, 'target_occurrence_id': candidate['id'],
                             'target_run': candidate['run_id'], 'target_source_path': candidate['source_path'],
                             'target_source_pointer': candidate['pointer'], 'target_native_id': candidate['native_id'],
                             'target_raw_sha256': candidate['raw_sha256']})
    if sorted(external, key=lambda x: (x['raw_locator'], x['target_occurrence_id'])) != sorted(
            scope['external_candidates'], key=lambda x: (x['raw_locator'], x['target_occurrence_id'])):
        raise ValueError('Native Message complete target set drift')
    lookup: dict[tuple[str, str, str], list[tuple]] = defaultdict(list)
    for locale in scope['languages']:
        for tier in ('main', 'medium'):
            path = scope['textmaps'][locale][tier]
            for ordinal, (locator, value) in enumerate(pairs(source / path)):
                if locator in hashes:
                    lookup[(locale, tier, locator)].append((path, ordinal, '/' + locator, value))
    states = Counter(); relation_states = Counter(); owner_count = 0
    for declaration in declarations:
        path = declaration['path']
        for ordinal, obj, _ in source_rows[path]:
            pointer = '/' + str(ordinal)
            row_id = identity(run, 'row', path, pointer)
            for owner in declaration['text_owners']:
                field = owner['field']; locator = str(obj[field]); owner_count += 1
                for locale in scope['languages']:
                    main = lookup[(locale, 'main', locator)]
                    medium = lookup[(locale, 'medium', locator)]
                    selected_pairs = main if main else medium
                    state = ('missing' if not selected_pairs else 'resolved_candidate_set' if len(selected_pairs) > 1
                             else 'resolved_main' if main else 'resolved_medium')
                    states[state] += 1
                    slot_id = identity(run, 'slot', path, pointer, field, locale)
                    expected['native71_slot'].append((slot_id, row_id, field, owner.get('historical_field'),
                                                      owner['owner_role_status'], locale, locator, state,
                                                      len(main) + len(medium), len(selected_pairs)))
                    for tier, candidates in (('main', main), ('medium', medium)):
                        for candidate_path, pair, text_pointer, value in candidates:
                            seal = seal_by_path[candidate_path]
                            expected['native71_text'].append((
                                identity(run, 'text', slot_id, candidate_path, pair), slot_id,
                                candidate_path, seal['sha256'], seal['blob'], tier, pair, text_pointer,
                                value, hashlib.sha256(value.encode()).hexdigest(),
                                int((tier == 'main' and bool(main)) or (tier == 'medium' and not main))))
            for relation in declaration.get('relations', []):
                field = relation['field']; locator = obj.get(field)
                if type(locator) is not int:
                    raise ValueError('Native Message relation locator type')
                candidate_rows = sorted(targets[str(locator)], key=lambda x: x['id'])
                status = 'missing' if not candidate_rows else 'resolved_unique' if len(candidate_rows) == 1 else 'resolved_candidate_set'
                relation_states[status] += 1
                at = pointer + '/' + field
                relation_id = identity(run, 'relation', path, at, relation['relation_kind'])
                expected['native71_relation'].append((relation_id, row_id, relation['relation_kind'], field, at,
                                                      str(locator), relation['candidate_namespace'], status,
                                                      len(candidate_rows), 1))
                for candidate in candidate_rows:
                    expected['native71_candidate'].append((
                        identity(run, 'candidate', relation_id, candidate['id']), relation_id,
                        dependency['family'], dependency['contract'], dependency['parser'], dependency['run'],
                        candidate['id'], candidate['source_path'], candidate['pointer'],
                        candidate['native_id'], candidate['raw_sha256']))
    actual = sqlite3.connect(artifact.as_uri() + '?mode=ro&immutable=1', uri=True)
    reference = sqlite3.connect(':memory:')
    try:
        reference.executescript(SCHEMA.read_text())
        schema_sql = "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        if actual.execute(schema_sql).fetchall() != reference.execute(schema_sql).fetchall():
            raise ValueError('Native Message schema/trigger drift')
        if actual.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or actual.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Native Message integrity/FK')
        for name in TABLES:
            if table_rows(actual, name) != sorted(expected[name]):
                raise ValueError('Native Message source rowset mismatch: ' + name)
        release = actual.execute('SELECT manifest_json FROM native71_release WHERE id=1').fetchone()
        if release is None or json.loads(release[0]) != {k: v for k, v in report.items() if k != 'artifact'}:
            raise ValueError('Native Message release binding')
        if (report['counts'] != {k: len(v) for k, v in expected.items()}
                or report['owner_count'] != owner_count
                or report['text_states'] != dict(sorted(states.items()))
                or report['relation_states'] != dict(sorted(relation_states.items()))):
            raise ValueError('Native Message self-reported counts')
        for candidate in expected['native71_candidate']:
            same = targets[candidate[9]]
            if not any((t['id'], t['source_path'], t['pointer'], t['raw_sha256']) ==
                       (candidate[6], candidate[7], candidate[8], candidate[10]) for t in same):
                raise ValueError('Native Message second-hop target')
    finally:
        actual.close(); reference.close()
    return {'status': 'PASS', 'counts': {k: len(v) for k, v in expected.items()},
            'text_states': dict(sorted(states.items())), 'relation_states': dict(sorted(relation_states.items())),
            'second_hop_checked': len(expected['native71_candidate']), 'source_reconstruction': True,
            'full_schema_and_triggers': True, 'no_extras': True, 'no_omissions': True}
