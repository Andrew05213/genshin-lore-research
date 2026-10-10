"""Independent fixed-source oracle for the 7.1 TPS Glasses Event graph."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .event_7_1_source import array_records, check_files, pairs, sha
from .event_narrative_source import check_files as check_old_files

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / 'manifests/event-narrative/tps-glasses-chat-scope.json'
SCHEMA = ROOT / 'schemas/event-tps-7.1-v1.sql'
TABLES = ('event_narrative_row', 'event_narrative_slot', 'event_narrative_text',
          'event_narrative_relation', 'event_narrative_relation_candidate')


def physical_id(*parts):
    payload = json.dumps(parts, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
    return 'tps71:' + hashlib.sha256(payload.encode()).hexdigest()[:40]


def schema_objects(db):
    return [list(row) for row in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]


def strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, str):
        yield value


def mapped_spec(value, aliases):
    if isinstance(value, dict):
        return {key: mapped_spec(item, aliases) for key, item in value.items()}
    if isinstance(value, list):
        return [mapped_spec(item, aliases) for item in value]
    return aliases.get(value, value) if isinstance(value, str) else value


def rederive_versioned_contract(old_root, new_root, old_scope, scope):
    consumed = set(strings(old_scope['sources']))
    aliases = {}
    direct = {}
    for original in old_scope['sources']:
        before = list(array_records(old_root / original['path']))
        after = list(array_records(new_root / original['path']))
        if (len(before) != old_scope['row_denominator'][original['kind']]
                or len(after) != scope['row_denominator'][original['kind']]
                or len(before) != len(after)):
            raise ValueError('TPS full old/new physical denominator')
        left = [row for _, row, _ in before]
        right = [row for _, row, _ in after]
        old_keys = set().union(*(set(row) for row in left))
        new_keys = set().union(*(set(row) for row in right))
        rename = {}
        anchors = []
        for key in old_keys & consumed:
            if key in new_keys:
                if (all(key in left[index] and key in right[index]
                        and left[index][key] == right[index][key]
                        for index in range(len(left)))
                        and len({json.dumps(row[key], sort_keys=True) for row in left}) == len(left)):
                    anchors.append((key, key))
                continue
            candidates = [target for target in new_keys if all(
                (key in left[index]) == (target in right[index])
                and (key not in left[index] or
                     (type(left[index][key]) is type(right[index][target])
                      and left[index][key] == right[index][target]))
                for index in range(len(left)))]
            if len(candidates) != 1:
                raise ValueError(f'TPS consumed source field continuity ambiguous {original["kind"]}/{key}: {candidates}')
            rename[key] = candidates[0]
            if (all(key in row for row in left)
                    and len({json.dumps(row[key], sort_keys=True) for row in left}) == len(left)):
                anchors.append((key, candidates[0]))
        if not anchors or len(set(rename.values())) != len(rename):
            raise ValueError('TPS independent source row alignment/alias uniqueness')
        declaration = scope['field_continuity'][original['kind']]
        if (declaration['mapping'] != dict(sorted(rename.items()))
                or declaration['aligned_rows'] != len(left)
                or sorted([list(item) for item in anchors]) !=
                sorted(declaration['injective_raw_anchors'])):
            raise ValueError('TPS scope field continuity proof drift')
        for old_key, current in rename.items():
            if old_key in aliases and aliases[old_key] != current:
                raise ValueError('TPS cross-source raw alias collision')
            aliases[old_key] = current
        direct[original['kind']] = rename
    expected_sources = mapped_spec(old_scope['sources'], aliases)
    if expected_sources != scope['sources']:
        raise ValueError('TPS source specifications differ from complete consumed field continuity')
    return direct, aliases


def verify(source_root, old_root, scope_path, artifact, build_report=None, runtime_manifest=None):
    source_root, old_root, scope_path, artifact = map(
        Path, (source_root, old_root, scope_path, artifact))
    scope = json.loads(scope_path.read_text())
    historical = json.loads(OLD.read_text())
    check_files(source_root, scope)
    check_old_files(old_root, historical)
    if (scope['contract'] != 'event-tps-7.1-versioned-scope-v1'
            or scope['historical_scope'] != str(OLD.relative_to(ROOT))
            or scope['historical_scope_sha256'] != sha(OLD)
            or scope['family_contract_version'] != historical['family_contract_version']
            or scope['languages'] != historical['languages']
            or len(scope['sources']) != 6 or historical['talk_candidates']
            or scope['talk_candidates']):
        raise ValueError('TPS historical family boundary')
    continuity, aliases = rederive_versioned_contract(old_root, source_root,
                                                       historical, scope)
    selected = {spec['path'] for spec in scope['sources']}
    selected |= {f'TextMap/TextMap{suffix}{locale}.json' for locale in scope['languages']
                 for suffix in ('', '_Medium')}
    files = {entry['path']: entry for entry in scope['files']}
    if len(selected) != 12 or set(files) != selected:
        raise ValueError('TPS exact selected source union')
    implementation = {path: sha(ROOT / path) for path in (
        'src/genshin_lore/event_tps_7_1_build.py',
        'src/genshin_lore/event_7_1_source.py',
        'src/genshin_lore/event_narrative_source.py',
        'schemas/event-tps-7.1-v1.sql')}
    parser = physical_id('parser', implementation)
    scope_sha = sha(scope_path)
    run = physical_id('run', parser, scope_sha)
    physical = {spec['kind']: list(array_records(source_root / spec['path']))
                for spec in scope['sources']}
    if ({spec['kind']: len(physical[spec['kind']]) for spec in scope['sources']}
            != scope['row_denominator'] or sum(map(len, physical.values())) != 507):
        raise ValueError('TPS source rows not complete')
    observations = []
    for spec in scope['sources']:
        for ordinal, row, _ in physical[spec['kind']]:
            for owner in spec['text_owners']:
                predicate = owner.get('when')
                if predicate:
                    key = predicate['field']
                    if predicate.get('present') and key not in row:
                        continue
                    if 'equals' in predicate and row.get(key) != predicate['equals']:
                        continue
                if owner['shape'] != 'scalar' or type(row.get(owner['field'])) is not int:
                    raise ValueError('TPS oracle text owner primitive')
                observations.append((spec['kind'], ordinal, owner['field'],
                                     row[owner['field']], owner['role_status']))
    wanted = {str(item[3]) for item in observations}
    text_pairs = defaultdict(list)
    for locale in scope['languages']:
        for tier, suffix in (('main', ''), ('medium', '_Medium')):
            path = f'TextMap/TextMap{suffix}{locale}.json'
            for ordinal, (key, text) in enumerate(pairs(source_root / path)):
                if key in wanted:
                    text_pairs[(locale, tier, key)].append((path, ordinal, '/' + key, text))
    expected = {table: [] for table in TABLES}
    states, roles, relation_states = Counter(), Counter(), Counter()
    locations, scalar_index, compound_index = {}, defaultdict(list), defaultdict(list)
    for spec in scope['sources']:
        path = spec['path']
        for ordinal, row, raw in physical[spec['kind']]:
            occurrence = physical_id(run, 'row', path, ordinal)
            locations[(spec['kind'], ordinal)] = occurrence
            native_key = spec.get('native_id_field')
            native = None if native_key is None else str(row[native_key])
            seal = files[path]
            expected['event_narrative_row'].append((
                occurrence, run, spec['boundary'], path, seal['sha256'], seal['blob'],
                ordinal, '/' + str(ordinal), native_key, native, raw,
                hashlib.sha256(raw.encode()).hexdigest()))
            target_key = spec.get('relation_lookup_field')
            if target_key:
                if target_key not in row:
                    if not spec.get('relation_lookup_field_optional'):
                        raise ValueError('TPS target locator missing')
                else:
                    scalar_index[(spec['kind'], str(row[target_key]))].append((
                        path, '/' + str(ordinal), str(row[target_key]), occurrence))
            for fields in spec.get('relation_lookup_field_sets', []):
                if any(field not in row for field in fields):
                    raise ValueError('TPS target composite locator missing')
                key = tuple(str(row[field]) for field in fields)
                compound_index[(spec['kind'], tuple(fields), key)].append((
                    path, '/' + str(ordinal), occurrence))
    for kind, ordinal, field, raw_locator, role in observations:
        spec = next(item for item in scope['sources'] if item['kind'] == kind)
        path = spec['path']
        parent = locations[(kind, ordinal)]
        roles[role] += len(scope['languages'])
        for locale in scope['languages']:
            main = text_pairs[(locale, 'main', str(raw_locator))]
            medium = text_pairs[(locale, 'medium', str(raw_locator))]
            hits = main if main else medium
            state = ('missing' if not hits else 'ambiguous' if len(hits) > 1
                     else 'resolved_main' if main else 'resolved_medium')
            states[state] += 1
            slot = physical_id(run, 'slot', path, ordinal, field, locale)
            expected['event_narrative_slot'].append((
                slot, parent, field, role, locale, str(raw_locator), state, len(hits)))
            for source_path, pair, pointer, text in hits:
                seal = files[source_path]
                expected['event_narrative_text'].append((
                    physical_id(run, 'text', path, ordinal, field, locale, source_path, pair),
                    slot, source_path, seal['sha256'], seal['blob'], pair, pointer,
                    text, hashlib.sha256(text.encode()).hexdigest()))
    for spec in scope['sources']:
        source_path = spec['path']
        for ordinal, row, _ in physical[spec['kind']]:
            parent = locations[(spec['kind'], ordinal)]
            for edge in spec['internal_relations']:
                field = edge['field']
                shape = edge['shape']
                if shape == 'scalar_optional' and field not in row:
                    continue
                if shape == 'array_scalar':
                    values = row.get(field)
                    if not isinstance(values, list):
                        raise ValueError('TPS oracle relation array')
                elif shape in ('scalar', 'scalar_optional'):
                    values = [row[field]]
                else:
                    raise ValueError('TPS oracle unsupported source shape')
                if any(type(value) is not int for value in values):
                    raise ValueError('TPS oracle relation locator primitive')
                for index, locator in enumerate(values):
                    if locator in edge.get('skip_values', []):
                        continue
                    if edge.get('target_lookup_fields'):
                        source_fields = edge['source_lookup_fields']
                        if any(key not in row for key in source_fields):
                            raise ValueError('TPS oracle composite source locator missing')
                        key = tuple(str(row[source_field]) for source_field in source_fields)
                        raw_targets = sorted(compound_index[(edge['target_kind'],
                                                             tuple(edge['target_lookup_fields']), key)])
                        targets = [(path, pointer, str(locator), target)
                                   for path, pointer, target in raw_targets]
                    else:
                        targets = sorted(scalar_index[(edge['target_kind'], str(locator))])
                    derived = ('missing' if not targets else 'resolved_unique'
                               if len(targets) == 1 else 'ambiguous')
                    state = edge.get('status', derived)
                    relation_states[state] += 1
                    pointer = f'/{ordinal}/{field}' + (f'/{index}' if shape == 'array_scalar' else '')
                    relation = physical_id(run, 'relation', source_path, pointer,
                                           edge['relation_kind'])
                    namespace = edge.get('candidate_namespace',
                                         'event_wave_physical_rows:' + edge['target_kind'])
                    expected['event_narrative_relation'].append((
                        relation, parent, edge['relation_kind'], field, pointer,
                        str(locator), namespace, state, len(targets),
                        int(edge.get('search_complete', True))))
                    for target_path, target_pointer, target_native, target_id in targets:
                        expected['event_narrative_relation_candidate'].append((
                            physical_id(run, 'relation-candidate', relation,
                                        target_path, run, target_id),
                            relation, target_path, target_pointer, target_native,
                            'event_narrative_archive', scope['family_contract_version'],
                            parser, run, target_id))
    for table in TABLES:
        expected[table].sort(key=lambda row: row[0])
    memory = sqlite3.connect(':memory:')
    memory.executescript(SCHEMA.read_text())
    wanted_schema = schema_objects(memory)
    memory.close()
    db = sqlite3.connect(artifact.absolute().as_uri() + '?mode=ro&immutable=1', uri=True)
    if schema_objects(db) != wanted_schema:
        raise ValueError('TPS complete schema/index/guard drift')
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('TPS SQLite integrity/FK')
    if any(Path(str(artifact) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise ValueError('TPS SQLite sidecar')
    for table in TABLES:
        got = db.execute('SELECT * FROM ' + table + ' ORDER BY id').fetchall()
        if got != expected[table]:
            raise ValueError('TPS independent full source-to-rowset mismatch ' + table)
    release = json.loads(db.execute('SELECT manifest_json FROM event_narrative_release WHERE id=1').fetchone()[0])
    db.close()
    counts = {table: len(expected[table]) for table in TABLES}
    expected_release = {'contract': scope['family_contract_version'], 'run_id': run,
                        'parser_build_id': parser, 'scope_sha256': scope_sha,
                        'implementation': implementation, 'snapshot_id': scope['snapshot_id'],
                        'counts': counts, 'text_state_counts': dict(sorted(states.items())),
                        'owner_role_counts': dict(sorted(roles.items())),
                        'relation_state_counts': dict(sorted(relation_states.items())),
                        'relation_occurrences': counts['event_narrative_relation'],
                        'full_7_1': False}
    if release != expected_release:
        raise ValueError('TPS exact parser/run/source release drift')
    logical = {table: hashlib.sha256('\n'.join(json.dumps(list(row), ensure_ascii=False,
               separators=(',', ':')) for row in expected[table]).encode()).hexdigest()
               for table in TABLES}
    artifact_seal = {'size_bytes': artifact.stat().st_size, 'sha256': sha(artifact)}
    if build_report is not None and (build_report.get('artifact') != artifact_seal
                                     or build_report.get('logical_seals') != logical):
        raise ValueError('TPS build report drift')
    if runtime_manifest is not None:
        manifest = json.loads(Path(runtime_manifest).read_text())
        if (manifest['artifact'] != artifact_seal or manifest['release'] != expected_release
                or manifest['counts'] != counts or manifest['logical_seals'] != logical):
            raise ValueError('TPS runtime manifest drift')
    return {'status': 'pass', 'artifact': artifact_seal,
            'parser_build_id': parser, 'run_id': run,
            'source_files': len(selected), 'physical_rows': sum(map(len, physical.values())),
            'field_continuity': continuity, 'historical_aliases': aliases,
            'counts': counts, 'text_state_counts': dict(sorted(states.items())),
            'relation_state_counts': dict(sorted(relation_states.items())),
            'owner_role_counts': dict(sorted(roles.items())),
            'logical_seals': logical, 'schema_structure': wanted_schema,
            'no_extras': True, 'no_omissions': True, 'silent_skips': 0}
