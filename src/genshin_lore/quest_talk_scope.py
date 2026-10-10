"""Explicit scalable Quest/Talk scope contract; never an acceptance decision."""
from __future__ import annotations
from typing import Any

CONTRACT = 'quest-talk-batch-scope-v1'
FAMILY_CONTRACT = 'quest-talk-evidence-v3'
RUNTIME_CONTRACT = 'quest-talk-batch-runtime-v1'
LOOKUP_POLICY = 'all_physical_native_talk_id_candidates_no_disambiguation'


def validate_scope(scope: Any) -> dict:
    if not isinstance(scope, dict) or scope.get('manifest_contract_version') != CONTRACT:
        raise ValueError('unsupported Quest/Talk scalable scope contract')
    for key, expected in {'family':'quest_talk_pilot','family_contract_version':FAMILY_CONTRACT,'candidate_lookup_policy':LOOKUP_POLICY,'candidate_search_scope':'BinOutput/Talk/**/*.json','languages':['CHS','EN','JP'],'full_scan':False,'corpus_complete':False,'product_accepted':False}.items():
        if scope.get(key) != expected:
            raise ValueError(f'Quest/Talk scope {key} mismatch')
    for key in ('manifest_id','snapshot_id','source_commit','source_tree','quest_selection','quest_source_files','candidate_lookup_denominator','textmap_shards','expected_physical_source_denominator','coverage_level'):
        if key not in scope:
            raise ValueError(f'Quest/Talk scope missing {key}')
    if not scope['quest_selection'] or len(scope['quest_selection']) != len(scope['quest_source_files']):
        raise ValueError('Quest/Talk physical Quest selection mismatch')
    if not isinstance(scope['snapshot_id'],str) or not scope['snapshot_id']:
        raise ValueError('Quest/Talk scope snapshot identity mismatch')
    if any(type(v) is not int for v in scope['quest_selection']) or len(set(scope['quest_selection'])) != len(scope['quest_selection']):
        raise ValueError('Quest/Talk selection must contain unique integer IDs')
    if any(not isinstance(v,str) for v in scope['quest_source_files']) or len(set(scope['quest_source_files'])) != len(scope['quest_source_files']):
        raise ValueError('Quest/Talk source files must be unique paths')
    for key in ('candidate_lookup_denominator','expected_physical_source_denominator'):
        if type(scope[key]) is not int or scope[key] < 0:
            raise ValueError('Quest/Talk scope denominator must be a nonnegative integer')
    return scope
