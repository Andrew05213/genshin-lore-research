#!/usr/bin/env python3
"""Freeze 7.1 Aux/Quest exact historical selector and accepted physical targets."""

import argparse
import hashlib
import json
import sqlite3
import subprocess
from functools import lru_cache
from pathlib import Path

from genshin_lore.event_7_1_source import COMMIT, SNAPSHOT, TREE, array_records, git_blob, sha

ROOT = Path(__file__).resolve().parents[2]
SELECTION = ROOT / 'manifests/native-archive-7.1/aux-quest-selection-v1.json'
QN_MANIFEST = ROOT / 'manifests/quest-derived-7.1/standalone-candidates-v1.json'
BULK_MANIFEST = ROOT / 'manifests/native-archive-7.1/bulk-runtime.json'


def seal(source, path):
    actual = source / path
    blob = subprocess.check_output(['git', '-C', str(source), 'rev-parse', COMMIT + ':' + path], text=True).strip()
    if blob != git_blob(actual):
        raise ValueError('7.1 source Git blob drift: ' + path)
    return {'path': path, 'size_bytes': actual.stat().st_size, 'sha256': sha(actual), 'blob': blob}


def sealed_artifact(path, expected):
    p = Path(path).resolve()
    if p.stat().st_size != expected['size_bytes'] or sha(p) != expected['sha256']:
        raise ValueError('accepted dependency artifact drift: ' + str(p))
    return {'path': str(p), 'size_bytes': expected['size_bytes'], 'sha256': expected['sha256']}


@lru_cache(maxsize=4)
def source_records(source, path):
    return tuple(array_records(source / path))


def source_row(source, path, pointer):
    index = int(pointer[1:])
    if pointer != '/' + str(index):
        raise ValueError('target pointer is not top-level ordinal')
    record = source_records(source, path)[index]
    if record[0] != index:
        raise ValueError('target physical ordinal')
    return record[1], record[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--quest-artifact', type=Path, default=Path('/tmp/quest-narrative-7.1-final-a.sqlite'))
    parser.add_argument('--bulk-artifact', type=Path, default=Path('/tmp/genshin-lore-native-bulk-7.1-v4-20260926/b.sqlite'))
    parser.add_argument('--output', type=Path, default=ROOT / 'manifests/native-archive-7.1/aux-quest-scope-v1.json')
    args = parser.parse_args()
    source = args.source.resolve()
    head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    tree = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD^{tree}'], text=True).strip()
    dirty = subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain'], text=True).strip()
    if (head, tree, dirty) != (COMMIT, TREE, ''):
        raise ValueError('7.1 source checkout identity')
    selection = json.loads(SELECTION.read_text())
    if len(selection['sources']) != 40:
        raise ValueError('Aux/Quest historical selector denominator')
    paths = {entry['path'] for entry in selection['sources']}
    paths.update(path for item in selection['textmaps'].values() for path in item.values())
    if len(paths) != 46:
        raise ValueError('Aux/Quest exact 7.1 file denominator')
    files = [seal(source, path) for path in sorted(paths)]
    rows = 0
    for spec in selection['sources']:
        for ordinal, row, _ in array_records(source / spec['path']):
            rows += 1
            if not isinstance(row, dict):
                raise ValueError('non-object physical source row')
            for owner in spec['text_owners']:
                value = row.get(owner['field'])
                if value is not None and type(value) is not int:
                    raise ValueError(f'text owner type {spec["path"]}/{ordinal}/{owner["field"]}')
            for relation in spec.get('relations', []):
                value = row.get(relation['field'])
                if value is None:
                    continue
                if relation.get('shape') == 'array':
                    if not isinstance(value, list) or any(type(item) is not int for item in value if item is not None):
                        raise ValueError(f'array relation type {spec["path"]}/{ordinal}/{relation["field"]}')
                elif type(value) is not int:
                    raise ValueError(f'scalar relation type {spec["path"]}/{ordinal}/{relation["field"]}')
    qn = json.loads(QN_MANIFEST.read_text())['components']['quest_narrative_archive']
    bulk = json.loads(BULK_MANIFEST.read_text())
    if qn['source_snapshot'] != SNAPSHOT or bulk['source_snapshot'] != SNAPSHOT:
        raise ValueError('cross-snapshot dependency')
    qseal = sealed_artifact(args.quest_artifact, qn['artifact'])
    bseal = sealed_artifact(args.bulk_artifact, bulk['artifact'])
    bulk_scope = json.loads((ROOT / bulk['source_scope']).read_text())
    if {entry['path'] for entry in selection['sources']} & {entry['path'] for entry in bulk_scope['sources']}:
        raise ValueError('Aux/Quest selected rows overlap accepted Native Bulk')
    candidates = []
    q = sqlite3.connect(f'file:{args.quest_artifact.resolve()}?mode=ro&immutable=1', uri=True)
    q.row_factory = sqlite3.Row
    try:
        for namespace, kind in (('quest_main', 'main_quest'), ('quest_step', 'quest_step')):
            for row in q.execute('SELECT id,run_id,source_path,pointer,native_id,raw_sha256 '
                                 'FROM excel_row WHERE kind=? ORDER BY source_path,pointer,id', (kind,)):
                if row['run_id'] != qn['ingest_run_id']:
                    raise ValueError('Quest Narrative target run')
                physical, raw = source_row(source, row['source_path'], row['pointer'])
                native_field = 'id' if kind == 'main_quest' else 'subId'
                if str(physical.get(native_field)) != row['native_id'] or hashlib.sha256(raw.encode()).hexdigest() != row['raw_sha256']:
                    raise ValueError(f'Quest Narrative source-to-target row mismatch {row["source_path"]}{row["pointer"]} native={row["native_id"]}')
                candidates.append({'namespace': namespace, 'raw_locator': row['native_id'],
                                   'target_source_path': row['source_path'], 'target_source_pointer': row['pointer'],
                                   'target_native_id': row['native_id'], 'target_raw_sha256': row['raw_sha256'],
                                   'target_family': 'quest_narrative_archive', 'target_contract': qn['contract'],
                                   'target_parser': qn['parser_build_id'], 'target_run': row['run_id'],
                                   'target_occurrence_id': row['id']})
    finally:
        q.close()
    b = sqlite3.connect(f'file:{args.bulk_artifact.resolve()}?mode=ro&immutable=1', uri=True)
    b.row_factory = sqlite3.Row
    try:
        for row in b.execute("SELECT id,run_id,source_path,pointer,native_id,raw_sha256 FROM native_archive_container "
                             "WHERE source_path='ExcelBinOutput/GCGRuleTextDetailExcelConfigData.json' ORDER BY pointer,id"):
            if row['run_id'] != bulk['ingest_run_id']:
                raise ValueError('Native Bulk target run')
            physical, raw = source_row(source, row['source_path'], row['pointer'])
            if str(physical.get('id')) != row['native_id'] or hashlib.sha256(raw.encode()).hexdigest() != row['raw_sha256']:
                raise ValueError('Native Bulk source-to-target row mismatch')
            candidates.append({'namespace': 'gcg_rule_detail', 'raw_locator': row['native_id'],
                               'target_source_path': row['source_path'], 'target_source_pointer': row['pointer'],
                               'target_native_id': row['native_id'], 'target_raw_sha256': row['raw_sha256'],
                               'target_family': 'native_archive', 'target_contract': bulk['family_contract_version'],
                               'target_parser': bulk['parser_build_id'], 'target_run': row['run_id'],
                               'target_occurrence_id': row['id']})
    finally:
        b.close()
    candidates.sort(key=lambda row: (row['namespace'], str(row['raw_locator']),
                                     row['target_source_path'], row['target_source_pointer'], row['target_occurrence_id']))
    if (rows != 55037 or len(candidates) != 4417 + 17814 + 85):
        raise ValueError('7.1 Aux/Quest source/target denominator drift')
    scope = {'contract': 'auxiliary-quest-delta-7.1-scope-v1', 'family': 'native_archive',
             'family_contract_version': selection['family_contract_version'],
             'snapshot_id': SNAPSHOT, 'source_commit': COMMIT, 'source_tree': TREE,
             'selection_manifest': str(SELECTION.relative_to(ROOT)), 'selection_sha256': sha(SELECTION),
             'sources': selection['sources'], 'languages': selection['languages'],
             'textmaps': selection['textmaps'], 'files': files, 'reuse_targets': [],
             'external_candidates': candidates,
             'dependencies': {'quest_narrative': {'manifest': str(QN_MANIFEST.relative_to(ROOT)),
                                                 'manifest_sha256': sha(QN_MANIFEST), 'artifact': qseal,
                                                 'contract': qn['contract'], 'parser': qn['parser_build_id'],
                                                 'run': qn['ingest_run_id'],
                                                 'required_capabilities': ['mainquest_exact_rows', 'quest_excel_exact_rows']},
                              'native_bulk': {'manifest': str(BULK_MANIFEST.relative_to(ROOT)),
                                              'manifest_sha256': sha(BULK_MANIFEST), 'artifact': bseal,
                                              'contract': bulk['family_contract_version'],
                                              'parser': bulk['parser_build_id'], 'run': bulk['ingest_run_id']}},
             'historical_selector_physical_rows': rows,
             'main_quest_reconciliation': {'source_path': 'ExcelBinOutput/MainQuestExcelConfigData.json',
                                           'physical_rows': 4417, 'status': 'same_snapshot_exact_physical_capability_reuse',
                                           'accepted_parser': qn['parser_build_id'], 'accepted_run': qn['ingest_run_id']},
             'completeness': {'declared_physical_projection_complete': True,
                              'declared_text_projection_complete': True,
                              'declared_relation_projection_complete': True, 'full_7_1': False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(scope, indent=2, ensure_ascii=False, sort_keys=True) + '\n')
    print(json.dumps({'files': len(files), 'physical_rows': rows, 'external_candidates': len(candidates)}))


if __name__ == '__main__':
    main()
