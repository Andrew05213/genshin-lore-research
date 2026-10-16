"""Snapshot-local Native21 projection over 37 pinned arrays and exact accepted targets.

Obfuscated owner fields retain mapping_unresolved; equality candidates are physical.
"""
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
TABLES = ('row_occurrence', 'text_slot', 'text_candidate', 'relation_locator', 'relation_candidate')
DEPENDENCY_MANIFESTS = (
    REPO / 'manifests/event-narrative-7.1/component-13-physical-runtime-v1.json',
    REPO / 'manifests/quest-derived-7.1/standalone-candidates-v1.json',
    REPO / 'manifests/narrative-7.1/runtime.json',
    REPO / 'manifests/quest-talk-7.1/standalone-candidate.json',
)
def dependency_manifests(metadata_root=None):
    base = Path(metadata_root) if metadata_root is not None else REPO
    return tuple(base / p.relative_to(REPO) for p in DEPENDENCY_MANIFESTS)


def ident(*parts):
    payload = json.dumps(parts, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    return 'native21-71:' + hashlib.sha256(payload).hexdigest()[:40]


def _dependencies(metadata_root=None):
    event, derived, npc, talk = [json.loads(path.read_text()) for path in dependency_manifests(metadata_root)]
    quest = derived['components']['quest_narrative_archive'] if 'components' in derived else derived['quest_narrative_archive']
    n = npc['families']['npc']
    expected = ((Path(event['artifact']['path_machine_local']), event['artifact']),
                (Path(quest['artifact']['path_machine_local']), quest['artifact']),
                (Path(n['artifact']['path_machine_local']), n['artifact']),
                (Path(talk['local_artifact_path']), talk['artifact']))
    for path, seal in expected:
        if path.stat().st_size != seal['size_bytes'] or sha(path) != seal['sha256']:
            raise ValueError('Native21 accepted dependency artifact drift: ' + str(path))
    return event, quest, n, talk


def _target_rows(root, source_rows, quest, npc, talk):
    wanted = defaultdict(set)
    for spec, _, row, _ in source_rows:
        for rel in spec.get('relations', ()):
            value = row.get(rel['field'])
            if type(value) is int:
                wanted[rel['relation_kind']].add(str(value))
    output = defaultdict(list)
    source_cache = {}

    def raw_check(path, pointer, expected_sha):
        if path.startswith('BinOutput/Talk/'):
            if pointer != '/' or sha(root / path) != expected_sha:
                raise ValueError('Native21 Talk second-hop raw SHA')
            return
        if path not in source_cache:
            source_cache[path] = {('/' + str(i)): hashlib.sha256(raw.encode()).hexdigest()
                                  for i, _, raw in array_records(root / path)}
        if source_cache[path].get(pointer) != expected_sha:
            raise ValueError('Native21 second-hop source coordinate/raw SHA: ' + path + pointer)

    qrun = quest['ingest_run_id']
    with sqlite3.connect(quest['artifact']['path_machine_local']) as db:
        for kind, rel_kind in (('main_quest', 'source_authored_main_quest_reference'),
                               ('quest_step', 'source_authored_open_quest_reference'),
                               ('main_quest', 'source_authored_open_quest_reference'),
                               ('quest_step', 'source_authored_pre_quest_reference'),
                               ('main_quest', 'source_authored_pre_quest_reference')):
            for record in db.execute('SELECT id,source_path,pointer,native_id,raw_sha256 FROM excel_row WHERE kind=? AND run_id=?',
                                     (kind, qrun)):
                rid, path, pointer, native, raw_sha = record
                if native in wanted[rel_kind]:
                    raw_check(path, pointer, raw_sha)
                    output[(rel_kind, native)].append(('quest_narrative_archive', quest['contract'],
                        quest['parser_build_id'], qrun, rid, path, pointer, raw_sha))
    nrelease = npc['release']
    with sqlite3.connect(npc['artifact']['path_machine_local']) as db:
        for rid, path, pointer, native, raw_sha in db.execute(
                'SELECT evidence_ref,source_path,pointer,native_key,raw_sha256 FROM physical_row'):
            if native in wanted['source_authored_npc_reference']:
                raw_check(path, pointer, raw_sha)
                output[('source_authored_npc_reference', native)].append((
                    'npc', nrelease['contract'], nrelease['parser'], nrelease['run'],
                    rid, path, pointer, raw_sha))
    with sqlite3.connect(talk['local_artifact_path']) as db:
        for rid, path, pointer, native, raw_sha in db.execute(
                "SELECT id,source_path,pointer,native_id,raw_sha256 FROM physical_container WHERE kind='talk_root' AND ingest_run_id=?",
                (talk['ingest_run_id'],)):
            if native in wanted['source_authored_finish_talk_reference']:
                raw_check(path, pointer, raw_sha)
                output[('source_authored_finish_talk_reference', native)].append((
                    'quest_talk', talk['contract'], talk['parser_build_id'], talk['ingest_run_id'],
                    rid, path, pointer, raw_sha))
    for values in output.values():
        values.sort()
    return output


def projection(root: Path, scope_path: Path, *, metadata_root=None):
    scope = json.loads(scope_path.read_text())
    check_files(root, scope)
    if scope['contract'] != 'native21-7.1-versioned-scope-v1' or len(scope['sources']) != 37:
        raise ValueError('Native21 selected source boundary')
    event, quest, npc, talk = _dependencies(metadata_root)
    binding_path = REPO / scope['event13_snez_binding']
    if sha(binding_path) != scope['event13_snez_binding_sha256']:
        raise ValueError('Native21 Snez binding seal')
    binding = json.loads(binding_path.read_text())
    if binding['snapshot'] != scope['snapshot_id']:
        raise ValueError('Native21 Snez snapshot')
    snez = {item['pointer']: item for item in binding['occurrences']}
    if len(snez) != 9:
        raise ValueError('Native21 Snez denominator')
    implementation = {str(path.relative_to(REPO)): sha(path) for path in
                      (Path(__file__), SCHEMA)}
    implementation.update({str(original.relative_to(REPO)): sha(actual)
                           for original, actual in zip(DEPENDENCY_MANIFESTS, dependency_manifests(metadata_root))})
    parser = ident('parser', implementation)
    run = ident('run', parser, sha(scope_path), scope['snapshot_id'])
    source_rows = []
    wanted_hashes = set()
    for spec in scope['sources']:
        records = list(array_records(root / spec['path']))
        if len(records) != spec['source_row_count']:
            raise ValueError('Native21 full source denominator: ' + spec['path'])
        for ordinal, row, raw in records:
            if not isinstance(row, dict):
                raise ValueError('Native21 source row shape')
            source_rows.append((spec, ordinal, row, raw))
            for owner in spec['text_owners']:
                value = row.get(owner['field'])
                if value is not None:
                    if type(value) is not int:
                        raise ValueError('Native21 owner locator shape')
                    wanted_hashes.add(str(value))
    if len(source_rows) != 1045:
        raise ValueError('Native21 7.1 physical row denominator')
    target = _target_rows(root, source_rows, quest, npc, talk)
    maps = {}
    for locale in ('CHS', 'EN', 'JP'):
        for tier in ('main', 'medium'):
            path = scope['textmaps'][locale][tier]
            hits = defaultdict(list)
            for ordinal, (key, value) in enumerate(pairs(root / path)):
                if key in wanted_hashes:
                    hits[key].append((path, ordinal, value))
            maps[(locale, tier)] = hits
    expected = {table: [] for table in TABLES}
    states, relation_states = Counter(), Counter()
    with sqlite3.connect(event['artifact']['path_machine_local']) as db:
        event_rows = {pointer: (rid, digest) for rid, pointer, digest in db.execute(
            "SELECT id,pointer,raw_sha256 FROM physical_row WHERE source_path='ExcelBinOutput/SnezBookOfferingGameplayExcelConfigData.json'")}
    for spec, ordinal, row, raw in source_rows:
        path, pointer = spec['path'], '/' + str(ordinal)
        row_id = ident(run, path, pointer)
        raw_sha = hashlib.sha256(raw.encode()).hexdigest()
        native_field = spec['native_id_field']
        native = str(row[native_field]) if native_field and native_field in row else None
        if spec.get('reuse'):
            match = snez.get(pointer)
            if match is None or (match['target_occurrence_id'], match['raw_sha256']) != event_rows.get(pointer) or raw_sha != match['raw_sha256']:
                raise ValueError('Native21 Event13 Snez exact second hop')
            reuse = ('event_narrative_archive', event['family_contract_version'], event['parser_build_id'],
                     event['ingest_run_id'], match['target_occurrence_id'])
            projected_raw = None
        else:
            reuse = (None,) * 5
            projected_raw = raw
        expected['row_occurrence'].append((row_id, run, spec['boundary'], path, ordinal, pointer,
                                           native_field, native, projected_raw, raw_sha, *reuse))
        for owner in spec['text_owners']:
            field, value = owner['field'], row.get(owner['field'])
            if value is None:
                continue
            if type(value) is not int:
                raise ValueError('Native21 text owner type')
            for locale in ('CHS', 'EN', 'JP'):
                key = str(value)
                main = maps[(locale, 'main')].get(key, [])
                medium = maps[(locale, 'medium')].get(key, [])
                selected = [('main', *item) for item in main] if main else [('medium', *item) for item in medium]
                state = ('missing' if not selected else 'resolved_candidate_set' if len(selected) > 1
                         else 'resolved_unique_main' if main else 'resolved_unique_medium')
                states[state] += 1
                slot_id = ident(run, path, pointer, field, locale)
                expected['text_slot'].append((slot_id, row_id, field, owner['owner_role_status'], locale,
                                              key, state, len(selected)))
                for tier, text_path, pair_ordinal, text in selected:
                    expected['text_candidate'].append((ident(run, slot_id, text_path, pair_ordinal),
                        slot_id, text_path, tier, pair_ordinal, '/' + key, text,
                        hashlib.sha256(text.encode()).hexdigest()))
        for rel in spec.get('relations', ()):
            value = row.get(rel['field'])
            if value is None:
                continue
            if type(value) is not int:
                raise ValueError('Native21 relation locator type')
            locator = str(value)
            hits = target.get((rel['relation_kind'], locator), ())
            has_unproven_main_target = (rel['relation_kind'] != 'source_authored_main_quest_reference'
                                        and any(item[5].endswith('/MainQuestExcelConfigData.json') for item in hits))
            state = ('mapping_unresolved' if has_unproven_main_target else 'missing' if not hits
                     else 'resolved_unique' if len(hits) == 1 else 'resolved_candidate_set')
            relation_states[state] += 1
            relation_id = ident(run, path, pointer, rel['field'], rel['relation_kind'])
            expected['relation_locator'].append((relation_id, row_id, rel['field'], rel['relation_kind'], locator,
                                                 state, len(hits)))
            for hit in hits:
                expected['relation_candidate'].append((ident(run, relation_id, hit), relation_id, *hit))
    for table in TABLES:
        expected[table].sort()
    release = {
        'contract': 'native21-7.1-physical-text-candidate-v1', 'snapshot': scope['snapshot_id'],
        'parser_build_id': parser, 'ingest_run_id': run, 'scope_sha256': sha(scope_path),
        'implementation': implementation, 'counts': {table: len(expected[table]) for table in TABLES},
        'text_states': dict(sorted(states.items())), 'relation_states': dict(sorted(relation_states.items())),
        'snez_exact_reuse_count': 9, 'semantic_owner_mapping_unresolved_fields': 9,
        'historical_exact_replacement': False, 'product_accepted': False,
    }
    return scope, expected, release


def build(root: Path, scope_path: Path, output: Path, *, metadata_root=None):
    root, scope_path, output = Path(root).resolve(), Path(scope_path).resolve(), Path(output).absolute()
    scope = json.loads(scope_path.read_text())
    inputs = [('source_root', root), ('scope', scope_path), ('schema', SCHEMA)]
    inputs += [(str(i), root / item['path']) for i, item in enumerate(scope['files'])]
    inputs += [(str(i), path) for i, path in enumerate(dependency_manifests(metadata_root))]
    assert_output_paths(output, inputs=inputs)
    _, rows, release = projection(root, scope_path, metadata_root=metadata_root)
    fd, stage = create_private_staging(output, prefix='.native21-71-')
    inode = stage.stat().st_dev, stage.stat().st_ino
    connection = None
    try:
        os.close(fd)
        connection = sqlite3.connect(stage)
        connection.execute('PRAGMA foreign_keys=ON')
        connection.executescript(SCHEMA.read_text())
        for table in TABLES:
            if rows[table]:
                connection.executemany('INSERT INTO ' + table + ' VALUES (' + ','.join('?' for _ in rows[table][0]) + ')', rows[table])
        connection.execute('INSERT INTO release VALUES(1,?)', (json.dumps(release, sort_keys=True, separators=(',', ':')),))
        connection.commit()
        connection.execute('VACUUM')
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or connection.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Native21 SQLite validation')
        connection.close()
        connection = None
        if any(path.exists() for path in sidecars(stage)):
            raise ValueError('Native21 stage sidecar')
        check_files(root, scope)
        seal = {'size_bytes': stage.stat().st_size, 'sha256': sha(stage)}
        publish_absent(output, stage, inputs=inputs)
        return {**release, 'artifact': seal}
    finally:
        if connection is not None:
            connection.close()
        if stage.exists() and (stage.stat().st_dev, stage.stat().st_ino) == inode:
            stage.unlink()
