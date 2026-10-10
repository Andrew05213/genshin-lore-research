"""Narrow versioned TradeShow bonus-to-bonus-quest physical candidate adapter."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from .event_7_1_source import check_files, sha
from .event_narrative_source import array_records, check_files as check_historical_files

SOURCE = 'ExcelBinOutput/ActivityTradeShowBonusExcelConfigData.json'
TARGET = 'ExcelBinOutput/ActivityTradeShowBonusQuestExcelConfigData.json'
OLD = 'BLANOIAPJLF'
NEW = 'HHPBBIAHALJ'


def project(root: Path, old_root: Path, scope_path: Path, artifact: Path):
    root, old_root, scope_path, artifact = map(Path, (root, old_root, scope_path, artifact))
    scope = json.loads(scope_path.read_text())
    check_files(root, scope)
    history = json.loads((Path(__file__).resolve().parents[2] /
                          'manifests/event-narrative/archive-wave-scope.json').read_text())
    check_historical_files(old_root, history)
    old = list(array_records(old_root / SOURCE))
    current = list(array_records(root / SOURCE))
    old_by_id = {row['bonusId']: row for _, row, _ in old}
    now_by_id = {row['bonusId']: (ordinal, row, raw) for ordinal, row, raw in current}
    if len(old_by_id) != len(old) or len(now_by_id) != len(current):
        raise ValueError('TradeShow bonusId alignment is not unique')
    if set(old_by_id) != set(now_by_id) or len(current) != 10:
        raise ValueError('TradeShow inherited bonus denominator')
    all_arrays = {key for _, row, _ in current for key, value in row.items()
                  if isinstance(value, list)}
    equivalent = [key for key in sorted(all_arrays)
                  if all(old_by_id[bonus_id].get(OLD) == now_by_id[bonus_id][1].get(key)
                         for bonus_id in old_by_id)]
    if equivalent != [NEW]:
        raise ValueError('TradeShow versioned field continuity not unique: ' + repr(equivalent))
    target_by_id = defaultdict(list)
    for ordinal, row, raw in array_records(root / TARGET):
        if type(row.get('id')) is not int:
            raise ValueError('TradeShow target ID shape')
        target_by_id[row['id']].append((ordinal, raw))
    connection = sqlite3.connect('file:' + str(artifact.resolve()) + '?mode=ro&immutable=1', uri=True)
    release = json.loads(connection.execute('SELECT manifest_json FROM release WHERE id=1').fetchone()[0])
    if release['scope_sha256'] != sha(scope_path) or release['snapshot'] != scope['snapshot_id']:
        raise ValueError('TradeShow physical foundation scope mismatch')
    bindings = []
    for bonus_id, (ordinal, row, raw) in sorted(now_by_id.items()):
        source_pointer = '/' + str(ordinal)
        source_sha = hashlib.sha256(raw.encode()).hexdigest()
        source_occurrence = connection.execute(
            'SELECT id FROM physical_row WHERE source_path=? AND pointer=? AND raw_sha256=?',
            (SOURCE, source_pointer, source_sha)).fetchone()
        if source_occurrence is None:
            raise ValueError('TradeShow source second-hop')
        locators = row[NEW]
        if not isinstance(locators, list) or any(type(value) is not int for value in locators):
            raise ValueError('TradeShow migrated locator shape')
        for position, value in enumerate(locators):
            targets = []
            for target_ordinal, target_raw in target_by_id[value]:
                pointer = '/' + str(target_ordinal)
                digest = hashlib.sha256(target_raw.encode()).hexdigest()
                target_occurrence = connection.execute(
                    'SELECT id FROM physical_row WHERE source_path=? AND pointer=? AND raw_sha256=?',
                    (TARGET, pointer, digest)).fetchone()
                if target_occurrence is None:
                    raise ValueError('TradeShow target second-hop')
                targets.append({'occurrence_id': target_occurrence[0], 'source_path': TARGET,
                                'pointer': pointer, 'native_id': value, 'raw_sha256': digest})
            bindings.append({'source_occurrence_id': source_occurrence[0],
                             'source_pointer': source_pointer + '/' + NEW + '/' + str(position),
                             'raw_locator': value,
                             'status': 'missing' if not targets else
                                       'resolved_unique_physical_candidate' if len(targets) == 1 else
                                       'resolved_candidate_set',
                             'candidates': targets})
    connection.close()
    return {'contract': 'event13-7.1-tradeshow-versioned-physical-candidates-v1',
            'historical_field': OLD, 'source_field': NEW,
            'alignment': 'unique bonusId, 10/10 full-array equality, unique array-field candidate',
            'snapshot': scope['snapshot_id'], 'parser_build_id': release['parser_build_id'],
            'ingest_run_id': release['ingest_run_id'], 'artifact_sha256': sha(artifact),
            'bindings': bindings, 'relation_semantics': 'physical candidates only'}
