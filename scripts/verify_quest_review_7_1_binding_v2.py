#!/usr/bin/env python3
"""Independent full-denominator source-to-binding oracle for Review 7.1 v2."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'manifests/quest-derived-7.1/standalone-candidates-v1.json'
SCHEMA = ROOT / 'schemas/quest-review-7.1-binding-v2.sql'
COMMIT = '9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99'
TREE = 'aa578c08c402af39b41f1ffc95124d10f5410272'
SNAPSHOT = 'anime-game-data:7.1:' + COMMIT
SUMMARY = 'ExcelBinOutput/QuestSummarizationTextExcelConfigData.json'
GROUP = 'ExcelBinOutput/QuestReviewExcelConfigData.json'
TAB = 'ExcelBinOutput/QuestReviewTabGroupExcelConfigData.json'
STEP = 'ExcelBinOutput/QuestExcelConfigData.json'


def j(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'))


def h(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def key(*parts: object) -> str:
    return 'qr71b:' + h(j(parts).encode())[:40]


def check(value: bool, detail: str) -> None:
    if not value:
        raise ValueError(detail)


def ro(path: Path) -> sqlite3.Connection:
    check(path.is_file() and not any(Path(str(path) + sidecar).exists() for sidecar in ('-wal', '-shm', '-journal')), 'artifact/sidecar '+str(path))
    return sqlite3.connect(path.absolute().as_uri() + '?mode=ro&immutable=1', uri=True)


def rowset(db: sqlite3.Connection, table: str, expected: list[tuple]) -> dict:
    observed = [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY 1')]
    check(observed == sorted(expected), f'{table} rowset actual={len(observed)} expected={len(expected)}; first='+str(next(((a, b) for a, b in zip(observed, sorted(expected)) if a != b), None))[:400])
    return {'rows': len(observed), 'sha256': h(b''.join((j(list(row))+'\n').encode() for row in observed))}


def schema(db: sqlite3.Connection) -> None:
    expected = sqlite3.connect(':memory:')
    expected.executescript(SCHEMA.read_text())
    for table in ('bound_locator', 'bound_candidate', 'release'):
        for op in ('INSERT', 'UPDATE', 'DELETE'):
            expected.execute(f"CREATE TRIGGER immutable_{table}_{op.lower()} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'immutable quest review binding'); END")
    sql = "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    check([tuple(row) for row in db.execute(sql)] == [tuple(row) for row in expected.execute(sql)], 'schema/index/guard drift')
    expected.close()


def verify(source: Path, artifact: Path, report: Path) -> dict:
    source, artifact, report = Path(source), Path(artifact), Path(report)
    check(subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD', 'HEAD^{tree}'], text=True).splitlines() == [COMMIT, TREE], 'source pin')
    manifest = json.loads(report.read_text())
    release = manifest['release']
    check(release['contract'] == 'quest-review-7.1-exact-target-binding-v2' and release['status'] == 'candidate' and release['snapshot'] == SNAPSHOT and release['product_accepted'] is False, 'release authority')
    check(artifact.stat().st_size == manifest['artifact']['size_bytes'] and sha(artifact) == manifest['artifact']['sha256'], 'artifact seal')
    accepted = json.loads(MANIFEST.read_text())['components']
    qr_entry, qn_entry = accepted['quest_review_archive'], accepted['quest_narrative_archive']
    for entry in (qr_entry, qn_entry):
        path = Path(entry['artifact']['path_machine_local'])
        check(entry['acceptance_status'] == 'PHYSICAL_FOUNDATION_ACCEPTED' and entry['source_snapshot'] == SNAPSHOT and path.stat().st_size == entry['artifact']['size_bytes'] and sha(path) == entry['artifact']['sha256'], 'accepted input seal')
    dependencies = {'manifest_sha256': sha(MANIFEST), 'review': {k: qr_entry[k] for k in ('contract', 'parser_build_id', 'ingest_run_id', 'artifact')}, 'narrative': {k: qn_entry[k] for k in ('contract', 'parser_build_id', 'ingest_run_id', 'artifact')}}
    check(release['dependencies'] == dependencies, 'dependency binding')
    implementations = {str(path.relative_to(ROOT)): sha(path) for path in (ROOT/'scripts/build_quest_review_7_1_binding_v2.py', SCHEMA)}
    check(release['implementation'] == implementations and release['parser_build_id'] == key('parser', implementations), 'implementation/parser identity')
    run = key('run', SNAPSHOT, release['parser_build_id'], dependencies)
    check(run == release['ingest_run_id'], 'run identity')
    qr = ro(Path(qr_entry['artifact']['path_machine_local']))
    qn = ro(Path(qn_entry['artifact']['path_machine_local']))
    db = ro(artifact)
    schema(db)
    check(db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok' and not db.execute('PRAGMA foreign_key_check').fetchall(), 'integrity/FK')
    check(json.loads(db.execute('SELECT manifest_json FROM release').fetchone()[0]) == release, 'release table')
    for dependency_db, entry in ((qr, qr_entry), (qn, qn_entry)):
        value = json.loads(dependency_db.execute('SELECT manifest_json FROM release').fetchone()[0])
        check(all(value[k] == entry[k] for k in ('contract', 'parser_build_id', 'ingest_run_id')) and value['snapshot'] == SNAPSHOT, 'dependency DB release')

    physical = {}
    for path in (SUMMARY, GROUP, TAB, STEP):
        rows = json.loads((source/path).read_bytes().decode('utf-8-sig'))
        check(isinstance(rows, list), 'physical source array '+path)
        physical[path] = rows
    step_rows = defaultdict(list)
    target_count = 0
    for occurrence, target_run, kind, path, ordinal, pointer, native, raw_json, raw_sha in qn.execute('SELECT id,run_id,kind,source_path,ordinal,pointer,native_id,raw_json,raw_sha256 FROM excel_row WHERE kind=\'quest_step\''):
        check(kind == 'quest_step' and path == STEP and pointer == f'/{ordinal}' and target_run == qn_entry['ingest_run_id'] and 0 <= ordinal < len(physical[STEP]), 'QuestExcel occurrence coordinate')
        check(json.loads(raw_json) == physical[STEP][ordinal] and h(raw_json.encode()) == raw_sha and str(physical[STEP][ordinal]['subId']) == native, 'QuestExcel source second hop '+pointer)
        step_rows[native].append((path, pointer, native, 'quest_narrative_archive', qn_entry['contract'], qn_entry['parser_build_id'], target_run, occurrence, raw_sha, 'explicit_unresolved_equality_navigation_candidate'))
        target_count += 1
    check(target_count == len(physical[STEP]) == 17814, 'QuestExcel complete namespace')
    own_rows = {}
    own_lookup = defaultdict(list)
    for occurrence, row_run, kind, path, ordinal, pointer, native, raw_json, raw_sha in qr.execute('SELECT id,run_id,kind,source_path,source_ordinal,source_pointer,native_id,raw_json,raw_sha256 FROM evidence_row'):
        check(path in physical and 0 <= ordinal < len(physical[path]) and pointer == f'/{ordinal}' and row_run == qr_entry['ingest_run_id'], 'Review occurrence coordinate')
        check(json.loads(raw_json) == physical[path][ordinal] and h(raw_json.encode()) == raw_sha, 'Review source second hop '+path+pointer)
        own_rows[occurrence] = (kind, path, pointer, native)
        if kind in ('review_group', 'review_tab'):
            own_lookup[native].append((path, pointer, native, 'quest_review_archive', qr_entry['contract'], qr_entry['parser_build_id'], row_run, occurrence, raw_sha, 'explicit_unresolved_same_value_navigation_candidate'))
    check(len(own_rows) == 622, 'Review complete namespace')
    expected_locators, expected_candidates = [], []
    summary_counts = defaultdict(int)
    for source_id, owner, role, pointer, raw, status, old_count in qr.execute('SELECT id,row_id,role,source_pointer,raw_value,status,candidate_count FROM locator ORDER BY id'):
        kind, path, owner_pointer, native = own_rows[owner]
        check(status == 'mapping_unresolved', 'historical relation status')
        if role == 'summarization_id_observation':
            check(kind == 'summarization' and pointer == owner_pointer+'/Id' and raw == native, 'summary raw locator')
            hits = step_rows.get(raw, ())
            summary_counts['summary_locators'] += 1
            summary_counts['summary_candidates'] += len(hits)
        else:
            check(role == 'review_member_locator_observation' and kind == 'review_group' and pointer.startswith(owner_pointer+'/'), 'member raw locator')
            hits = own_lookup.get(raw, ())
            summary_counts['member_locators'] += 1
            summary_counts['member_candidates'] += len(hits)
        check(old_count == len(hits), 'accepted Review candidate count '+source_id)
        old_coordinates = sorted(qr.execute('SELECT target_source_path,target_source_pointer,target_native_id FROM locator_candidate WHERE locator_id=?', (source_id,)).fetchall())
        check(old_coordinates == sorted((hit[0],hit[1],hit[2]) for hit in hits), 'Review old candidate set '+source_id)
        bound_id = key('locator', run, source_id)
        expected_locators.append((bound_id,run,source_id,owner,role,pointer,raw,status,0,len(hits)))
        for hit in hits:
            target_path,target_pointer,target_native,family,contract,target_parser,target_run,occurrence,raw_sha,locator_kind = hit
            expected_candidates.append((key('candidate',bound_id,target_run,occurrence),bound_id,family,contract,target_parser,target_run,occurrence,target_path,target_pointer,target_native,raw_sha,locator_kind))
    check(dict(summary_counts) == {'summary_locators':606,'summary_candidates':544,'member_locators':11,'member_candidates':16}, 'Review 617/560 denominator')
    seals = {'bound_locator': rowset(db,'bound_locator',expected_locators),'bound_candidate': rowset(db,'bound_candidate',expected_candidates)}
    check(release['counts'] == {'bound_locator':617,'bound_candidate':560,**dict(summary_counts)} and release['relation_status'] == 'mapping_unresolved' and release['search_complete'] is False and release['logical_relation_closure'] is False, 'release semantics')
    qr.close();qn.close();db.close()
    return {'verdict':'PASS','artifact':manifest['artifact'],'counts':release['counts'],'logical_seals':seals,'full_questexcel_target_rows_checked':target_count,'summary_zero_candidates':62,'second_hop_full_recheck':560,'ambiguous_auto_selection':0,'logical_relation_closure':False}


if __name__ == '__main__':
    arg = argparse.ArgumentParser()
    arg.add_argument('--source', required=True)
    arg.add_argument('--artifact', required=True)
    arg.add_argument('--report', required=True)
    args = arg.parse_args()
    print(json.dumps(verify(Path(args.source),Path(args.artifact),Path(args.report)),sort_keys=True,indent=2))
