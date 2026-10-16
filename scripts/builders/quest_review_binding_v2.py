#!/usr/bin/env python3
"""Bind every 7.1 Quest Review raw locator to exact physical occurrences.

This is a derived navigation overlay over two accepted 7.1 physical candidates.
It neither copies their raw evidence nor upgrades equality to a logical relation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_COMMIT = '9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99'
SOURCE_TREE = 'aa578c08c402af39b41f1ffc95124d10f5410272'
SNAPSHOT = 'anime-game-data:7.1:' + SOURCE_COMMIT
MANIFEST = ROOT / 'manifests/quest-derived-7.1/standalone-candidates-v1.json'
SCHEMA = ROOT / 'schemas/quest-review-7.1-binding-v2.sql'
SUMMARY = 'ExcelBinOutput/QuestSummarizationTextExcelConfigData.json'
GROUP = 'ExcelBinOutput/QuestReviewExcelConfigData.json'
TAB = 'ExcelBinOutput/QuestReviewTabGroupExcelConfigData.json'
STEP = 'ExcelBinOutput/QuestExcelConfigData.json'


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'))


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def ident(*parts: object) -> str:
    return 'qr71b:' + digest(canonical(parts).encode())[:40]


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def readonly(path: Path) -> sqlite3.Connection:
    require(path.is_file() and not any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')), 'artifact unavailable/sidecar ' + str(path))
    return sqlite3.connect(path.absolute().as_uri() + '?mode=ro&immutable=1', uri=True)


def build(source: Path, output: Path, *, manifest_path=None) -> dict:
    manifest = Path(manifest_path or MANIFEST)
    source, output = Path(source), Path(output)
    require(subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD', 'HEAD^{tree}'], text=True).splitlines() == [SOURCE_COMMIT, SOURCE_TREE], '7.1 source pin')
    accepted = json.loads(manifest.read_text())['components']
    review, narrative = accepted['quest_review_archive'], accepted['quest_narrative_archive']
    for entry in (review, narrative):
        require(entry['acceptance_status'] == 'PHYSICAL_FOUNDATION_ACCEPTED' and entry['source_snapshot'] == SNAPSHOT and entry['product_accepted'] is False, 'accepted physical dependency')
        artifact = Path(entry['artifact']['path_machine_local'])
        require(artifact.stat().st_size == entry['artifact']['size_bytes'] and sha(artifact) == entry['artifact']['sha256'], 'dependency seal ' + str(artifact))
    qr = readonly(Path(review['artifact']['path_machine_local']))
    qn = readonly(Path(narrative['artifact']['path_machine_local']))
    qr_release = json.loads(qr.execute('SELECT manifest_json FROM release').fetchone()[0])
    qn_release = json.loads(qn.execute('SELECT manifest_json FROM release').fetchone()[0])
    require(all(release['snapshot'] == SNAPSHOT and release['parser_build_id'] == entry['parser_build_id'] and release['ingest_run_id'] == entry['ingest_run_id'] and release['contract'] == entry['contract'] for release, entry in ((qr_release, review), (qn_release, narrative))), 'dependency release identity')

    source_rows = {}
    for path in (SUMMARY, GROUP, TAB, STEP):
        values = json.loads((source / path).read_bytes().decode('utf-8-sig'))
        require(isinstance(values, list), 'source root array ' + path)
        source_rows[path] = values
    qn_step = defaultdict(list)
    qn_count = 0
    for row_id, run_id, path, ordinal, pointer, native, raw_json, raw_sha in qn.execute("SELECT id,run_id,source_path,ordinal,pointer,native_id,raw_json,raw_sha256 FROM excel_row WHERE kind='quest_step'"):
        require(path == STEP and run_id == narrative['ingest_run_id'] and pointer == f'/{ordinal}' and 0 <= ordinal < len(source_rows[STEP]), 'Narrative step coordinate')
        require(json.loads(raw_json) == source_rows[STEP][ordinal] and digest(raw_json.encode()) == raw_sha and str(source_rows[STEP][ordinal]['subId']) == native, 'Narrative step full source recheck ' + pointer)
        qn_step[native].append((path, pointer, native, 'quest_narrative_archive', narrative['contract'], narrative['parser_build_id'], narrative['ingest_run_id'], row_id, raw_sha, 'explicit_unresolved_equality_navigation_candidate'))
        qn_count += 1
    require(qn_count == len(source_rows[STEP]) == 17814, 'complete QuestExcel target namespace')
    qr_own = defaultdict(list)
    qr_row_count = 0
    qr_rows = {}
    for row_id, run_id, kind, path, ordinal, pointer, native, raw_json, raw_sha in qr.execute('SELECT id,run_id,kind,source_path,source_ordinal,source_pointer,native_id,raw_json,raw_sha256 FROM evidence_row'):
        require(run_id == review['ingest_run_id'] and pointer == f'/{ordinal}' and path in source_rows and 0 <= ordinal < len(source_rows[path]), 'Review row coordinate')
        require(json.loads(raw_json) == source_rows[path][ordinal] and digest(raw_json.encode()) == raw_sha, 'Review row exact source ' + path + pointer)
        qr_rows[row_id] = (kind, path, pointer, native, raw_sha)
        if kind in ('review_group', 'review_tab'):
            qr_own[native].append((path, pointer, native, 'quest_review_archive', review['contract'], review['parser_build_id'], review['ingest_run_id'], row_id, raw_sha, 'explicit_unresolved_same_value_navigation_candidate'))
        qr_row_count += 1
    require(qr_row_count == 622 and len(qr_rows) == qr_row_count, 'complete Review row namespace')
    implementations = {str(path.relative_to(ROOT)): sha(path) for path in (ROOT / 'scripts/builders/quest_review_binding_v2.py', SCHEMA)}
    parser = ident('parser', implementations)
    dependencies = {'manifest_sha256': sha(manifest), 'review': {k: review[k] for k in ('contract', 'parser_build_id', 'ingest_run_id', 'artifact')}, 'narrative': {k: narrative[k] for k in ('contract', 'parser_build_id', 'ingest_run_id', 'artifact')}}
    run = ident('run', SNAPSHOT, parser, dependencies)
    locators, candidates = [], []
    counts = defaultdict(int)
    for old_id, row_id, role, source_pointer, raw_value, state, declared_count in qr.execute('SELECT id,row_id,role,source_pointer,raw_value,status,candidate_count FROM locator ORDER BY id'):
        require(state == 'mapping_unresolved' and role in ('summarization_id_observation', 'review_member_locator_observation'), 'locator semantic boundary')
        kind, path, pointer, native, _ = qr_rows[row_id]
        if role == 'summarization_id_observation':
            require(kind == 'summarization' and native == raw_value and source_pointer == pointer + '/Id', 'summary source locator')
            hits = qn_step.get(raw_value, ())
            counts['summary_locators'] += 1
            counts['summary_candidates'] += len(hits)
        else:
            require(kind == 'review_group' and source_pointer.startswith(pointer + '/'), 'member source locator')
            hits = qr_own.get(raw_value, ())
            counts['member_locators'] += 1
            counts['member_candidates'] += len(hits)
        require(declared_count == len(hits), 'v1 locator cardinality '+old_id)
        old_hits = sorted(qr.execute('SELECT target_source_path,target_source_pointer,target_native_id FROM locator_candidate WHERE locator_id=?', (old_id,)).fetchall())
        require(old_hits == sorted((h[0], h[1], h[2]) for h in hits), 'complete old candidate coordinates '+old_id)
        lid = ident('locator', run, old_id)
        locators.append((lid, run, old_id, row_id, role, source_pointer, raw_value, state, 0, len(hits)))
        for hit in hits:
            target_path, target_pointer, target_native, family, contract, target_parser, target_run, occurrence, raw_sha, locator_kind = hit
            candidates.append((ident('candidate', lid, target_run, occurrence), lid, family, contract, target_parser, target_run, occurrence, target_path, target_pointer, target_native, raw_sha, locator_kind))
    require(counts['summary_locators'] == 606 and counts['member_locators'] == 11 and counts['summary_candidates'] == 544 and counts['member_candidates'] == 16, 'locator denominator')
    release = {'contract': 'quest-review-7.1-exact-target-binding-v2', 'status': 'candidate', 'snapshot': SNAPSHOT, 'source_commit': SOURCE_COMMIT, 'source_tree': SOURCE_TREE, 'implementation': implementations, 'parser_build_id': parser, 'ingest_run_id': run, 'dependencies': dependencies, 'counts': {'bound_locator': len(locators), 'bound_candidate': len(candidates), **dict(counts)}, 'relation_status': 'mapping_unresolved', 'search_complete': False, 'logical_relation_closure': False, 'product_accepted': False}
    require(not output.exists(), 'output exists')
    output.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(output)
    db.execute('PRAGMA page_size=4096')
    db.execute('PRAGMA journal_mode=OFF')
    db.execute('PRAGMA foreign_keys=ON')
    try:
        db.executescript(SCHEMA.read_text())
        db.executemany('INSERT INTO bound_locator VALUES (?,?,?,?,?,?,?,?,?,?)', locators)
        db.executemany('INSERT INTO bound_candidate VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', candidates)
        db.execute('INSERT INTO release VALUES (1,?)', (canonical(release),))
        for table in ('bound_locator', 'bound_candidate', 'release'):
            for op in ('INSERT', 'UPDATE', 'DELETE'):
                db.execute(f"CREATE TRIGGER immutable_{table}_{op.lower()} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'immutable quest review binding'); END")
        db.commit()
        db.execute('VACUUM')
        db.close()
    except BaseException:
        db.close()
        output.unlink(missing_ok=True)
        raise
    finally:
        qr.close()
        qn.close()
    return {'release': release, 'artifact': {'size_bytes': output.stat().st_size, 'sha256': sha(output)}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    result = build(Path(args.source), Path(args.output))
    report = Path(args.report)
    if report.exists():
        raise FileExistsError(report)
    report.write_text(json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result['release']['counts'], sort_keys=True))
