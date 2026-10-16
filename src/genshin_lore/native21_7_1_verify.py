"""Independent Native21 source-to-rowset oracle; does not import the builder."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .event_7_1_source import array_records, check_files, pairs, sha

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / 'schemas/native21-7.1-projection-v1.sql'
TABLES = ('row_occurrence', 'text_slot', 'text_candidate', 'relation_locator', 'relation_candidate')
SOURCES = ('manifests/event-narrative-7.1/component-13-physical-runtime-v1.json',
           'manifests/quest-derived-7.1/standalone-candidates-v1.json',
           'manifests/narrative-7.1/runtime.json',
           'manifests/quest-talk-7.1/standalone-candidate.json')


def digest_id(*values):
    encoded = json.dumps(values, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()
    return 'native21-71:' + hashlib.sha256(encoded).hexdigest()[:40]


def schema_objects(db):
    return db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()


def mutation_matrix(db):
    count = 0
    for recursive in ('OFF', 'ON'):
        db.execute('PRAGMA recursive_triggers=' + recursive)
        for table in (*TABLES, 'release'):
            original = db.execute('SELECT * FROM ' + table + ' LIMIT 1').fetchone()
            if original is None:
                raise ValueError('Native21 mutation fixture missing ' + table)
            columns = [entry[1] for entry in db.execute('PRAGMA table_info(' + table + ')')]
            pk = columns[0]
            changed = list(original)
            changed[0] = '__native21_mutation_probe__'
            statements = (
                ('INSERT INTO ' + table + ' VALUES (' + ','.join('?' for _ in columns) + ')', tuple(changed)),
                ('INSERT OR REPLACE INTO ' + table + ' VALUES (' + ','.join('?' for _ in columns) + ')', original),
                ('UPDATE ' + table + ' SET ' + pk + '=? WHERE ' + pk + '=?', ('__changed__', original[0])),
                ('DELETE FROM ' + table + ' WHERE ' + pk + '=?', (original[0],)),
                ('INSERT INTO ' + table + ' VALUES (' + ','.join('?' for _ in columns) +
                 ') ON CONFLICT(' + pk + ') DO UPDATE SET ' + pk + '=excluded.' + pk, original),
            )
            if table != 'release':
                # The primary key is fresh; the independent UNIQUE position remains occupied.
                statements += (('INSERT OR REPLACE INTO ' + table + ' VALUES (' +
                                ','.join('?' for _ in columns) + ')', tuple(changed)),)
            for sql, args in statements:
                db.execute('SAVEPOINT mutation_case')
                try:
                    db.execute(sql, args)
                    raise ValueError('Native21 immutable write was accepted: ' + table)
                except sqlite3.DatabaseError as exc:
                    if 'immutable' not in str(exc):
                        raise ValueError('Native21 write failed for non-guard reason: ' + str(exc)) from exc
                    count += 1
                finally:
                    db.execute('ROLLBACK TO mutation_case')
                    db.execute('RELEASE mutation_case')
    return count


def verify(root: Path, scope_path: Path, artifact: Path, *, metadata_root=None):
    metadata = Path(metadata_root) if metadata_root is not None else ROOT
    root, scope_path, artifact = Path(root), Path(scope_path), Path(artifact)
    scope = json.loads(scope_path.read_text())
    check_files(root, scope)
    if scope['contract'] != 'native21-7.1-versioned-scope-v1' or len(scope['sources']) != 37:
        raise ValueError('Native21 exact source selection contract')
    if any(Path(str(artifact) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise ValueError('Native21 artifact sidecar')
    event = json.loads((metadata / SOURCES[0]).read_text())
    derived = json.loads((metadata / SOURCES[1]).read_text())['components']['quest_narrative_archive']
    npc = json.loads((metadata / SOURCES[2]).read_text())['families']['npc']
    talk = json.loads((metadata / SOURCES[3]).read_text())
    artifacts = ((Path(event['artifact']['path_machine_local']), event['artifact']),
                 (Path(derived['artifact']['path_machine_local']), derived['artifact']),
                 (Path(npc['artifact']['path_machine_local']), npc['artifact']),
                 (Path(talk['local_artifact_path']), talk['artifact']))
    for path, seal in artifacts:
        if {'size_bytes': path.stat().st_size, 'sha256': sha(path)} != {
                'size_bytes': seal['size_bytes'], 'sha256': seal['sha256']}:
            raise ValueError('Native21 accepted target artifact seal: ' + str(path))
    binding_path = ROOT / scope['event13_snez_binding']
    if sha(binding_path) != scope['event13_snez_binding_sha256']:
        raise ValueError('Native21 Event13 binding seal')
    binding = json.loads(binding_path.read_text())
    source_rows = []
    hashes = set()
    wanted = defaultdict(set)
    raw_rows = {}
    for declaration in scope['sources']:
        path = declaration['path']
        parsed = list(array_records(root / path))
        if len(parsed) != declaration['source_row_count']:
            raise ValueError('Native21 physical source denominator')
        for ordinal, row, raw in parsed:
            if not isinstance(row, dict):
                raise ValueError('Native21 source row object')
            pointer = '/' + str(ordinal)
            raw_rows[(path, pointer)] = hashlib.sha256(raw.encode()).hexdigest()
            source_rows.append((declaration, ordinal, row, raw))
            for owner in declaration['text_owners']:
                field = owner['field']
                if field in row:
                    if type(row[field]) is not int:
                        raise ValueError('Native21 TextMap owner type')
                    hashes.add(str(row[field]))
            for rel in declaration.get('relations', ()):
                field = rel['field']
                if field in row:
                    if type(row[field]) is not int:
                        raise ValueError('Native21 source relation type')
                    wanted[rel['relation_kind']].add(str(row[field]))
    if len(source_rows) != 1045 or len(binding['occurrences']) != 9:
        raise ValueError('Native21 source/binding row denominator')
    implementation = {path: sha(ROOT / path) for path in (
        'src/genshin_lore/native21_7_1_build.py', 'schemas/native21-7.1-projection-v1.sql')}
    implementation.update({path: sha(metadata / path) for path in SOURCES})
    parser = digest_id('parser', implementation)
    run = digest_id('run', parser, sha(scope_path), scope['snapshot_id'])
    expected = {table: [] for table in TABLES}
    # Reconstruct all legal same-snapshot physical candidates independently.
    target_index = defaultdict(list)
    target_source_cache = {}

    def recheck_target(path, pointer, digest):
        if path.startswith('BinOutput/Talk/'):
            if pointer != '/' or sha(root / path) != digest:
                raise ValueError('Native21 Talk target provenance')
            return
        if path not in target_source_cache:
            target_source_cache[path] = {'/' + str(i): hashlib.sha256(raw.encode()).hexdigest()
                                         for i, _, raw in array_records(root / path)}
        if target_source_cache[path].get(pointer) != digest:
            raise ValueError('Native21 target provenance: ' + path + pointer)

    with sqlite3.connect(artifacts[1][0]) as db:
        for rid, kind, path, pointer, native, digest in db.execute(
                'SELECT id,kind,source_path,pointer,native_id,raw_sha256 FROM excel_row WHERE run_id=?',
                (derived['ingest_run_id'],)):
            for role, kind_required in (('source_authored_main_quest_reference', 'main_quest'),
                                        ('source_authored_open_quest_reference', 'quest_step'),
                                        ('source_authored_open_quest_reference', 'main_quest'),
                                        ('source_authored_pre_quest_reference', 'quest_step'),
                                        ('source_authored_pre_quest_reference', 'main_quest')):
                if kind == kind_required and native in wanted[role]:
                    recheck_target(path, pointer, digest)
                    target_index[(role, native)].append(('quest_narrative_archive', derived['contract'],
                        derived['parser_build_id'], derived['ingest_run_id'], rid, path, pointer, digest))
    with sqlite3.connect(artifacts[2][0]) as db:
        for rid, path, pointer, native, digest in db.execute(
                'SELECT evidence_ref,source_path,pointer,native_key,raw_sha256 FROM physical_row'):
            if native in wanted['source_authored_npc_reference']:
                recheck_target(path, pointer, digest)
                nr = npc['release']
                target_index[('source_authored_npc_reference', native)].append((
                    'npc', nr['contract'], nr['parser'], nr['run'], rid, path, pointer, digest))
    with sqlite3.connect(artifacts[3][0]) as db:
        for rid, path, pointer, native, digest in db.execute(
                "SELECT id,source_path,pointer,native_id,raw_sha256 FROM physical_container WHERE kind='talk_root' AND ingest_run_id=?",
                (talk['ingest_run_id'],)):
            if native in wanted['source_authored_finish_talk_reference']:
                recheck_target(path, pointer, digest)
                target_index[('source_authored_finish_talk_reference', native)].append((
                    'quest_talk', talk['contract'], talk['parser_build_id'], talk['ingest_run_id'],
                    rid, path, pointer, digest))
    for targets in target_index.values():
        targets.sort()
    with sqlite3.connect(artifacts[0][0]) as db:
        source = 'ExcelBinOutput/SnezBookOfferingGameplayExcelConfigData.json'
        event_index = {pointer: (rid, digest) for rid, pointer, digest in db.execute(
            'SELECT id,pointer,raw_sha256 FROM physical_row WHERE source_path=?', (source,))}
    snez = {item['pointer']: item for item in binding['occurrences']}
    text_index = {}
    for locale in ('CHS', 'EN', 'JP'):
        for tier in ('main', 'medium'):
            file = scope['textmaps'][locale][tier]
            index = defaultdict(list)
            for number, (key, text) in enumerate(pairs(root / file)):
                if key in hashes:
                    index[key].append((file, number, text))
            text_index[locale, tier] = index
    text_states = Counter()
    relation_states = Counter()
    for spec, ordinal, row, raw in source_rows:
        path, pointer = spec['path'], '/' + str(ordinal)
        rid = digest_id(run, path, pointer)
        native_field = spec['native_id_field']
        native = str(row[native_field]) if native_field and native_field in row else None
        raw_sha = raw_rows[path, pointer]
        if spec.get('reuse'):
            bind = snez.get(pointer)
            if bind is None or event_index.get(pointer) != (bind['target_occurrence_id'], bind['raw_sha256']) or bind['raw_sha256'] != raw_sha:
                raise ValueError('Native21 Snez target second hop')
            reuse = ('event_narrative_archive', event['family_contract_version'], event['parser_build_id'],
                     event['ingest_run_id'], bind['target_occurrence_id'])
            projected = None
        else:
            reuse = (None,) * 5
            projected = raw
        expected['row_occurrence'].append((rid, run, spec['boundary'], path, ordinal, pointer,
                                           native_field, native, projected, raw_sha, *reuse))
        for owner in spec['text_owners']:
            field = owner['field']
            if field not in row:
                continue
            locator = str(row[field])
            for locale in ('CHS', 'EN', 'JP'):
                main = text_index[locale, 'main'].get(locator, ())
                medium = text_index[locale, 'medium'].get(locator, ())
                choices = [('main', *record) for record in main] if main else [('medium', *record) for record in medium]
                status = ('missing' if not choices else 'resolved_candidate_set' if len(choices) > 1
                          else 'resolved_unique_main' if main else 'resolved_unique_medium')
                text_states[status] += 1
                slot = digest_id(run, path, pointer, field, locale)
                expected['text_slot'].append((slot, rid, field, owner['owner_role_status'], locale,
                                              locator, status, len(choices)))
                for tier, source_path, pair_number, text in choices:
                    expected['text_candidate'].append((digest_id(run, slot, source_path, pair_number),
                        slot, source_path, tier, pair_number, '/' + locator, text,
                        hashlib.sha256(text.encode()).hexdigest()))
        for ref in spec.get('relations', ()):
            field = ref['field']
            if field not in row:
                continue
            locator = str(row[field])
            matches = target_index[ref['relation_kind'], locator]
            unproven_main = (ref['relation_kind'] != 'source_authored_main_quest_reference'
                             and any(target[5].endswith('/MainQuestExcelConfigData.json') for target in matches))
            status = ('mapping_unresolved' if unproven_main else 'missing' if not matches
                      else 'resolved_unique' if len(matches) == 1 else 'resolved_candidate_set')
            relation_states[status] += 1
            relation = digest_id(run, path, pointer, field, ref['relation_kind'])
            expected['relation_locator'].append((relation, rid, field, ref['relation_kind'], locator,
                                                 status, len(matches)))
            for target in matches:
                expected['relation_candidate'].append((digest_id(run, relation, target), relation, *target))
    for table in TABLES:
        expected[table].sort()
    release = {
        'contract': 'native21-7.1-physical-text-candidate-v1', 'snapshot': scope['snapshot_id'],
        'parser_build_id': parser, 'ingest_run_id': run, 'scope_sha256': sha(scope_path),
        'implementation': implementation, 'counts': {table: len(expected[table]) for table in TABLES},
        'text_states': dict(sorted(text_states.items())), 'relation_states': dict(sorted(relation_states.items())),
        'snez_exact_reuse_count': 9, 'semantic_owner_mapping_unresolved_fields': 9,
        'historical_exact_replacement': False, 'product_accepted': False,
    }
    with sqlite3.connect(artifact) as db, sqlite3.connect(':memory:') as schema:
        schema.executescript(SCHEMA.read_text())
        if schema_objects(db) != schema_objects(schema):
            raise ValueError('Native21 full schema/index/trigger definition mismatch')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Native21 SQLite integrity/FK')
        for table, want in expected.items():
            columns = len(db.execute('SELECT * FROM ' + table + ' LIMIT 0').description)
            got = db.execute('SELECT * FROM ' + table + ' ORDER BY ' +
                             ','.join(str(i + 1) for i in range(columns))).fetchall()
            if got != want:
                raise ValueError('Native21 business rows/no-extra/no-omission: ' + table)
        got_release = json.loads(db.execute('SELECT json FROM release WHERE id=1').fetchone()[0])
        if got_release != release:
            raise ValueError('Native21 release contract mismatch')
        mutation_cases = mutation_matrix(db)
    return {'verified': True, 'artifact': {'size_bytes': artifact.stat().st_size, 'sha256': sha(artifact)},
            'counts': release['counts'], 'text_states': release['text_states'],
            'relation_states': release['relation_states'], 'second_hop_candidate_rows': len(expected['relation_candidate']),
            'snez_exact_reuse_count': 9, 'schema_exact': True, 'mutation_cases': mutation_cases,
            'silent_skips': 0, 'wrong_identity_merges': 0}
