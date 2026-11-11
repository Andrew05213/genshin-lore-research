"""Freeze 7.2 Event/Native physical selections; never infer semantic roles.

This is source preparation, not product qualification. Independent family
oracles must still reconstruct the complete new-source rowsets.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from genshin_lore.source_integrity import pinned_file_seal
from genshin_lore.source_versions import PINS


def strings(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)
    elif isinstance(value, str):
        yield value


def mapped(value, aliases):
    if isinstance(value, dict):
        return {aliases.get(k, k): mapped(v, aliases) for k, v in value.items()}
    if isinstance(value, list):
        return [mapped(v, aliases) for v in value]
    return aliases.get(value, value) if isinstance(value, str) else value


def raw_column_aliases(before, after, declaration):
    """Only unique exact raw-column correspondence over anchored physical rows."""
    consumed = {k: v for k, v in declaration.items() if k in (
        'native_id_field', 'native_key_fields', 'text_fields', 'text_owners',
        'internal_relations', 'relations', 'relation_lookup_field',
        'relation_lookup_field_sets', 'talk_relation_field', 'selection_field', 'owner_roles')}
    keys = set(strings(consumed)) & set().union(*(set(row) for row in before))
    current = set().union(*(set(row) for row in after))
    if not keys - current:
        return {}
    anchor = declaration.get('native_id_field')
    if declaration.get('path', '').endswith('/BeyondActivityPreviewDataExcelConfigData.json'):
        fields = ('activityId', 'scheduleId', 'sortPriority', 'activityNameTextMapHash')
        def physical_key(row):
            return tuple(row.get(field) for field in fields)
        left = {physical_key(row): row for row in before}
        right = {physical_key(row): row for row in after}
        if len(left) != len(before) or len(right) != len(after) or not set(left) <= set(right):
            raise ValueError('Beyond preview physical comparison tuple changed')
        pairs = [(row, right[key]) for key, row in left.items()]
    elif anchor and all(anchor in row for row in before + after):
        left = {json.dumps(row[anchor]): row for row in before}
        right = {json.dumps(row[anchor]): row for row in after}
        if len(left) != len(before) or len(right) != len(after) or not set(left) <= set(right):
            raise ValueError('physical comparison anchor not unique/preserved')
        pairs = [(row, right[key]) for key, row in left.items()]
    else:
        if len(after) < len(before):
            raise ValueError('physical ordinal comparison lost source rows')
        pairs = list(zip(before, after))
        stable = [key for key in set.intersection(*(set(row) for row in before)) & current
                  if all(type(l[key]) is type(r.get(key)) and l[key] == r[key] for l, r in pairs)]
        if not stable:
            invariants = []
            for old in set.intersection(*(set(row) for row in before)):
                targets = [new for new in current if all(
                    new in r and type(l[old]) is type(r[new]) and l[old] == r[new] for l, r in pairs)]
                if len(targets) == 1:
                    invariants.append(old)
            injective = [key for key in invariants if len({json.dumps(row[key], sort_keys=True) for row in before}) == len(before)]
            minimum = 1 if declaration.get('kind', '').startswith('tps_') or len(before) == 1 else 5
            if len(invariants) < minimum or not injective:
                raise ValueError('ordinal comparison lacks independent complete raw-column invariants')
    result = {}
    for key in sorted(keys - current):
        matches = [new for new in current if all(
            (key in l) == (new in r) and (key not in l or
            (type(l[key]) is type(r[new]) and l[key] == r[new])) for l, r in pairs)]
        if len(matches) != 1:
            raise ValueError(f'raw column correspondence is not unique: {key}: {matches}')
        result[key] = matches[0]
    if len(set(result.values())) != len(result):
        raise ValueError('raw columns would collapse')
    return result


def prepare(source, previous, output):
    source, previous, output = map(lambda p: Path(p).resolve(), (source, previous, output))
    for root, version in ((source, '7.2'), (previous, '7.1')):
        pin = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD', 'HEAD^{tree}'], text=True).splitlines()
        dirty = subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=all'], text=True)
        if pin != list(PINS[version]) or (version == '7.2' and dirty.strip()):
            raise ValueError('clean exact source required')
    commit, tree = PINS['7.2']
    seals, rows = {}, {}

    def records(root, path):
        key = (root, path)
        if key not in rows:
            # Only the explicitly consumed former-source files participate in
            # raw-column comparison. Each must still match its exact Git blob.
            pinned_file_seal(root, PINS['7.1' if root == previous else '7.2'][0], path)
            rows[key] = json.loads((root / path).read_bytes())
        return rows[key]

    def seal(path):
        if path not in seals:
            item = pinned_file_seal(source, commit, path)
            seals[path] = {'path': path, 'blob': item['blob'], 'size_bytes': item['size_bytes'], 'sha256': item['sha256']}
        return copy.deepcopy(seals[path])

    def write(relative, value):
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n'
        if path.exists():
            if path.read_text() != raw:
                raise FileExistsError('generated contract differs: ' + relative)
            return
        with path.open('x') as stream:
            stream.write(raw)

    generated = []
    for directory in ('event-narrative-7.1', 'native-archive-7.1'):
        for template in sorted((ROOT / 'manifests' / directory).glob('*scope*.json')):
            value = json.loads(template.read_text())
            value.update(source_commit=commit, source_tree=tree, snapshot_id=f'anime-game-data:7.2:{commit}')
            value['contract'] = value['contract'].replace('7.1', '7.2')
            value['files'] = [seal(item['path']) for item in value['files']]
            aliases = {}
            for spec in value.get('sources', []):
                before, after = records(previous, spec['path']), records(source, spec['path'])
                if 'component-15' in template.name:
                    # New root IDs and old page IDs coexist in the source. Do
                    # not try to prove selector continuity through equal values.
                    part = {'JPAAHMBJGOJ': 'CECCFAAFGNM', 'JOLGNPDDIJK': 'FJBMJKLCDDN',
                            'EHLBLMHEAAB': 'CMFMODONCGJ', 'PKPHBGMFODP': 'OPBFJNLJJKA',
                            'ICMKPBCEJJB': 'PAFDMIMKIAO', 'ELJEELOLFPI': 'IDNBLPICFGM'}
                else:
                    comparison = copy.deepcopy(spec)
                    if spec['path'].endswith('/OfferingQuestExcelConfigData.json'):
                        if any(row.get('mainQuestList') != [old['questId']] for old, row in zip(before, after)):
                            raise ValueError('OfferingQuest authored array differs from former scalar observations')
                        comparison['relations'] = [r for r in comparison.get('relations', []) if r['field'] != 'questId']
                    try:
                        part = raw_column_aliases(before, after, comparison)
                    except ValueError as exc:
                        raise ValueError(f'{template.name}: {spec["path"]}: {exc}') from exc
                for old, new in part.items():
                    if old in aliases and aliases[old] != new:
                        raise ValueError('cross-source alias collision')
                    aliases[old] = new
            if 'sources' in value:
                value['sources'] = mapped(value['sources'], aliases)
                for spec in value['sources']:
                    for owner in spec.get('text_owners', []):
                        field = owner['field']
                        if field in aliases.values() and field.isupper():
                            role = 'owner_role_status' if 'owner_role_status' in owner else 'role_status'
                            owner[role] = 'mapping_unresolved'
            # The historical continuity declarations are physical observations,
            # not cross-version logical identity or newly resolved field roles.
            if 'field_continuity' in value:
                value['field_continuity'] = mapped(value['field_continuity'], aliases)
            if 'field_renames' in value:
                value['field_renames'] = mapped(value['field_renames'], aliases)
            for key in ('raw_field_alignment', 'semantic_owner_mapping_unresolved_fields'):
                if key in value:
                    value[key] = mapped(value[key], aliases)
            if 'component-14' in template.name:
                for spec in value['sources']:
                    before = records(previous, spec['path'])
                    proof = value['field_continuity'][spec['kind']]
                    proof['added_physical_rows'] = len(records(source, spec['path'])) - len(before)
            if 'component-15' in template.name:
                root = records(source, 'ExcelBinOutput/ActivityPhotographExcelConfigData.json')[0]['CECCFAAFGNM']
                pages = [v for row in records(source, 'ExcelBinOutput/ActivityPhotographPageExcelConfigData.json') for v in row['FJBMJKLCDDN']]
                ids = sorted(set(root) | set(pages))
                extensions = records(source, 'ExcelBinOutput/ActivityPhotographPosExtendExcelConfigData.json')
                if set(root) & set(pages) or set(ids) != {row['id'] for row in extensions}:
                    raise ValueError('unexpected 7.2 photo selector source shape')
                for spec in value['sources']:
                    if spec['kind'] == 'activity_photograph_position':
                        spec['selected_native_ids'] = ids
                value.update(selector_status='disjoint_root_and_page_physical_union_not_logical_order',
                             corpus_complete=False,
                             coverage='bounded physical union of source-authored root/page selectors; selectors disagree, no logical activity/order equivalence')
            denominators = {}
            for spec in value.get('sources', []):
                physical = records(source, spec['path'])
                selected = spec.get('selected_native_ids')
                count = len(physical) if selected is None else sum(str(row.get(spec['selection_field'])) in {str(v) for v in selected} for row in physical)
                denominators[spec['kind']] = count
                for key in ('row_count', 'source_row_count'):
                    if key in spec:
                        spec[key] = len(physical)
                if 'owner_root_presence' in spec:
                    spec['owner_root_presence'] = {
                        field: sum(field.split('/')[0] in row for row in physical)
                        for field in spec['owner_root_presence']}
                for key, entry in spec.get('text_owner_observations', {}).items():
                    entry.update(absent=sum(key not in row for row in physical), explicit_null=sum(key in row and row[key] is None for row in physical), present_integer=sum(type(row.get(key)) is int for row in physical))
                for key, entry in spec.get('relation_field_observations', {}).items():
                    entry.update(absent=sum(key not in row for row in physical), explicit_null=sum(key in row and row[key] is None for row in physical), present=sum(key in row and row[key] is not None for row in physical))
            if 'row_denominator' in value:
                value['row_denominator'] = ({s['path']: denominators[s['kind']] for s in value['sources']}
                                            if 'message-scope' in template.name else denominators)
            for key in ('physical_rows', 'raw_physical_row_denominator'):
                if key in value:
                    value[key] = sum(denominators.values())
            for key in ('event15_scope', 'event13_snez_binding'):
                if key in value:
                    value[key] = value[key].replace('7.1', '7.2')
            if 'event16_contract' in value:
                value['event16_contract'] = value['event16_contract'].replace('7.1', '7.2')
            if 'event15_scope' in value:
                value['event15_scope_sha256'] = hashlib.sha256((output / value['event15_scope']).read_bytes()).hexdigest()
            selection = value.get('selection_manifest')
            if selection:
                new_path = ('manifests/native-archive-7.2/bulk-selection.json' if template.name == 'bulk-scope.json'
                            else selection.replace('7.1', '7.2'))
                authority = json.loads((ROOT / selection).read_text())
                authority['sources'] = value['sources']
                # OfferingQuest now has an authored array of MainQuest locators.
                for spec in authority['sources']:
                    if spec['path'].endswith('/OfferingQuestExcelConfigData.json'):
                        for relation in spec.get('relations', []):
                            if relation['field'] == 'questId':
                                relation.update(field='mainQuestList', shape='array')
                write(new_path, authority)
                value.update(selection_manifest=new_path, selection_sha256=hashlib.sha256((output / new_path).read_bytes()).hexdigest())
            if template.name == 'message-scope-v1.json':
                value['dependency'].update(contract='quest-narrative-7.2-derived-v1', physical_target_denominator=len(records(source, 'ExcelBinOutput/MainQuestExcelConfigData.json')),
                                           artifact={'path_machine_local': None, 'sha256': None, 'size_bytes': None}, target_artifact_machine_local=None, parser=None, run=None)
                value['external_candidates'] = []
            if template.name == 'aux-quest-scope-v1.json':
                value['main_quest_reconciliation']['physical_rows'] = len(records(source, 'ExcelBinOutput/MainQuestExcelConfigData.json'))
                value['main_quest_reconciliation'].update(accepted_parser=None, accepted_run=None)
            # These are new source inputs, not copies of former local runtime
            # receipts. The coordinator binds actual qualified outputs later.
            if template.name in ('book-music-scope-v1.json', 'aux-quest-scope-v1.json'):
                value.update(dependencies={}, external_candidates=[])
            if 'accepted_inputs' in value:
                for item in value['accepted_inputs'].values():
                    for key in ('path_machine_local', 'release', 'runtime_manifest', 'runtime_manifest_sha256', 'sha256', 'size_bytes'):
                        item[key] = None
            if 'talk_candidate_namespace' in value:
                namespace = value['talk_candidate_namespace']
                for key in ('accepted_manifest_sha256', 'artifact', 'parser', 'run'):
                    namespace[key] = None
                namespace['contract'] = namespace['contract'].replace('7.1', '7.2')
                value['talk_candidates'] = []
            if 'event13_snez_binding_sha256' in value:
                value['event13_snez_binding_sha256'] = None
            if 'event16_artifact' in value:
                value['event16_artifact'] = None
                for key in ('event16_parser', 'event16_run', 'event16_manifest_sha256'):
                    value[key] = None
                value['event16_reuse_targets'] = []
            if 'missing_historical_owner_specs' in value:
                value['missing_historical_owner_specs'] = [
                    {'source_path': spec['path'], 'historical_owner_path': field,
                     'status': 'UNRESOLVED_7_2_FIELD_ROLE'}
                    for spec in value['sources'] for field, count in spec['owner_root_presence'].items()
                    if count == 0]
            value['preparation'] = {'status': 'source_contract_generated_not_qualified', 'previous_snapshot': f'anime-game-data:7.1:{PINS["7.1"][0]}',
                                    'raw_field_correspondences': aliases, 'semantic_roles_inferred': False}
            relative = template.relative_to(ROOT).as_posix().replace(directory, directory.replace('7.1', '7.2'))
            write(relative, value)
            generated.append(relative)
    return {'status': 'SOURCE_CONTRACTS_GENERATED_NOT_QUALIFIED', 'scopes': generated}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--previous-source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.previous_source, args.output), sort_keys=True))
