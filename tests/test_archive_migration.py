"""Source preparation and scope isolation; fixtures contain no game text."""
import json
from pathlib import Path

import pytest

from genshin_lore.source_versions import PINS, scope_pin
from scripts.maintenance.prepare_archive_contracts import raw_column_aliases
from scripts.build import Rebuild

ROOT = Path(__file__).resolve().parents[1]


def test_scope_commit_and_tree_are_not_accepted_from_a_snapshot_label():
    commit, tree = PINS['7.2']
    scope = {'snapshot_id': f'anime-game-data:7.2:{commit}', 'source_commit': commit, 'source_tree': tree}
    assert scope_pin(scope) == ('7.2', commit, tree)
    scope['source_tree'] = PINS['7.1'][1]
    with pytest.raises(ValueError, match='commit/tree'):
        scope_pin(scope)


def test_raw_alias_keeps_all_source_rows_and_rejects_nonunique_columns():
    old = [{'id': 1, 'old': 11}, {'id': 2, 'old': 22}]
    new = [{'id': 2, 'new': 22}, {'id': 3, 'new': 33}, {'id': 1, 'new': 11}]
    spec = {'path': 'synthetic.json', 'native_id_field': 'id', 'text_owners': [{'field': 'old'}]}
    assert raw_column_aliases(old, new, spec) == {'old': 'new'}
    assert len(new) == 3  # Comparison does not discard newly added physical rows.
    duplicated = [{**row, 'also': row['new']} for row in new]
    with pytest.raises(ValueError, match='not unique'):
        raw_column_aliases(old, duplicated, spec)
    with pytest.raises(ValueError, match='anchor not unique/preserved'):
        raw_column_aliases(old, new[:1], spec)


def test_new_archive_scopes_are_new_snapshot_inputs_not_acceptance():
    paths = list((ROOT / 'manifests/event-narrative-7.2').glob('*scope*.json'))
    paths += list((ROOT / 'manifests/native-archive-7.2').glob('*scope*.json'))
    assert len(paths) == 14  # Eight Event scope definitions and six Native runs.
    for path in paths:
        scope = json.loads(path.read_text())
        assert scope_pin(scope)[0] == '7.2'
        assert scope['preparation']['status'] == 'source_contract_generated_not_qualified'
        assert scope['preparation']['semantic_roles_inferred'] is False
        assert scope.get('product_accepted') is not True
        assert '/tmp/' not in path.read_text()
        assert '/home/' not in path.read_text()
        for source in scope.get('sources', []):
            for owner in source.get('text_owners', []):
                if owner['field'] in scope['preparation']['raw_field_correspondences'].values() and owner['field'].isupper():
                    assert owner.get('owner_role_status', owner.get('role_status')) == 'mapping_unresolved'


def test_new_metadata_has_no_historical_sibling_selection(tmp_path):
    job = object.__new__(Rebuild)
    job.source_version = '7.2'
    job.output = tmp_path
    path = job.metadata_path('manifests/quest-talk-7.1/standalone-candidate.json')
    assert path == tmp_path / 'metadata/manifests/quest-talk-7.2/standalone-candidate.json'
    assert job.manifest('manifests/quest-talk-7.1/source-scope.json') == ROOT / 'manifests/quest-talk-7.2/source-scope.json'
    with pytest.raises(ValueError, match='outside'):
        job.metadata_path('../outside.json')


def test_new_offering_quest_array_has_physical_element_coordinates():
    from genshin_lore.native_aux_quest_7_1_build import relation_values as build_values
    from genshin_lore.native_aux_quest_7_1_verify import relation_values
    spec = {'field': 'mainQuestList', 'shape': 'array'}
    for reader in (build_values, relation_values):
        assert reader({'mainQuestList': [10, 20]}, spec) == [('mainQuestList/0', 10), ('mainQuestList/1', 20)]
        with pytest.raises(ValueError, match='integer'):
            reader({'mainQuestList': [10.0]}, spec)


def test_native_successor_rejects_a_retained_snapshot_as_a_new_dependency(tmp_path, monkeypatch):
    from genshin_lore import native21_7_1_build as builder
    new = f'anime-game-data:7.2:{PINS["7.2"][0]}'
    old = f'anime-game-data:7.1:{PINS["7.1"][0]}'
    objects = [{'source_snapshot': old},
               {'components': {'quest_narrative_archive': {'source_snapshot': new}}},
               {'families': {'npc': {'release': {'snapshot': new}}}},
               {'source_snapshot': new}]
    paths = []
    for index, value in enumerate(objects):
        path = tmp_path / f'dependency-{index}.json'
        path.write_text(json.dumps(value))
        paths.append(path)
    monkeypatch.setattr(builder, 'dependency_manifests', lambda *args: paths)
    with pytest.raises(ValueError, match='cross-snapshot'):
        builder._dependencies(tmp_path, '7.2')


def test_convenience_plan_uses_the_explicit_qualified_snapshot():
    from genshin_lore.narrative_assistant_7_1 import CapabilityPlanner71
    recipe = json.loads((ROOT / 'recipes/capability-names.json').read_text())
    snapshot = f'anime-game-data:7.2:{PINS["7.2"][0]}'
    registry = {'snapshot': snapshot, 'capabilities': [
        {k: row[k] for k in ('capability_id', 'supported_operations')}
        for row in recipe['capabilities']]}
    planner = CapabilityPlanner71(registry)
    plan = planner.plan('7.2合成人物有哪些语音？')
    assert plan['snapshot'] == snapshot
    assert plan['version_status'] == 'supported'
    assert plan['subject'] == '合成人物'
    assert planner.plan('7.1合成人物有哪些语音？')['version_status'] == 'unsupported_version'
    assert planner.plan('这是7.2新增的吗？')['version_status'] == 'unsupported_release_attribution'
