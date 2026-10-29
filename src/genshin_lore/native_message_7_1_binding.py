"""Output-local QuestNarrative proof; historical Native Message scope is sealed."""
from __future__ import annotations

from pathlib import Path
import sqlite3

from scripts.build_support import file_seal, load_object, qualify_stage_dependency
from scripts.validators import quest_narrative as oracle
from .event_7_1_source import SNAPSHOT, array_records

ROOT = Path(__file__).resolve().parents[2]
SCOPE = 'manifests/native-archive-7.1/message-scope-v1.json'
QT_MANIFEST = 'manifests/quest-talk-7.1/standalone-candidate.json'


def current_dependency(source, output):
    """Require complete current proofs and replay the unchanged source oracle."""
    source, output = Path(source).resolve(), Path(output).resolve()
    directory = output / 'quest_narrative'
    receipt = load_object(directory / 'verified.json')
    identity = receipt.get('identity', {})
    if (identity.get('source_root') != str(source)
            or identity.get('parameters') != {'stage': 'quest_narrative'}):
        raise ValueError('Native Message current QuestNarrative source/stage')
    history = Path(identity['historical_source_root']) if identity.get('historical_source_root') else None
    qt_inputs = qualify_stage_dependency(ROOT, source, history, output / 'quest_talk', 'quest_talk', SNAPSHOT)
    qt_manifest = output / 'metadata' / QT_MANIFEST
    expected_inputs = {str(p.resolve()): file_seal(p) for p in [*qt_inputs, qt_manifest]}
    if identity.get('stage_inputs') != expected_inputs:
        raise ValueError('Native Message QuestNarrative must bind its own Quest/Talk inputs')
    qualify_stage_dependency(ROOT, source, history, directory, 'quest_narrative', SNAPSHOT)
    artifact = directory / 'artifact.sqlite'
    report = load_object(directory / 'build.json')
    actual = file_seal(artifact)
    if report.get('artifact') != actual:
        raise ValueError('Native Message QuestNarrative build seal')
    checked = oracle.verify(source, artifact, directory / 'build.json', qt_inputs[0], qt_manifest)
    if checked != load_object(directory / 'verify.json'):
        raise ValueError('Native Message QuestNarrative independent oracle/report mismatch')
    qualify_stage_dependency(ROOT, source, history, directory, 'quest_narrative', SNAPSHOT)
    qualify_stage_dependency(ROOT, source, history, output / 'quest_talk', 'quest_talk', SNAPSHOT)
    template = load_object(ROOT / SCOPE)['dependency']
    release = report['release']
    if release['contract'] != template['contract'] or release['snapshot'] != SNAPSHOT:
        raise ValueError('Native Message QuestNarrative contract/snapshot')
    return {**template, 'artifact': {**actual, 'path_machine_local': str(artifact)},
            'target_artifact_machine_local': str(artifact),
            'parser': release['parser_build_id'], 'run': release['ingest_run_id']}


def prepare_scope(source, output):
    """Rebind current physical candidates, never substitute a historical DB."""
    output = Path(output).resolve()
    scope = load_object(ROOT / SCOPE)
    scope['dependency'] = current_dependency(source, output)
    scope['local_rebuild'] = {'contract': 'local-native-message-quest-narrative-v1',
                             'receipt': file_seal(output / 'quest_narrative/verified.json')}
    target = Path(scope['dependency']['target_artifact_machine_local'])
    locators = {str(row[relation['field']]) for spec in scope['sources']
                for relation in spec.get('relations', [])
                for _, row, _ in array_records(Path(source) / spec['path'])}
    external = []
    with sqlite3.connect(target.as_uri() + '?mode=ro&immutable=1', uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT id,run_id,source_path,pointer,native_id,raw_sha256 FROM excel_row WHERE kind='main_quest'").fetchall()
        if len(rows) != scope['dependency']['physical_target_denominator']:
            raise ValueError('Native Message current MainQuest denominator')
        for row in rows:
            if row['run_id'] != scope['dependency']['run']:
                raise ValueError('Native Message current MainQuest run')
            if row['native_id'] in locators:
                external.append({'raw_locator': row['native_id'], 'target_occurrence_id': row['id'],
                                 'target_run': row['run_id'], 'target_source_path': row['source_path'],
                                 'target_source_pointer': row['pointer'], 'target_native_id': row['native_id'],
                                 'target_raw_sha256': row['raw_sha256']})
    scope['external_candidates'] = sorted(external, key=lambda x: (x['raw_locator'], x['target_occurrence_id']))
    return scope


def qualify_scope(source, scope_path, scope):
    """A local flag cannot relax the historical source selection or target proof."""
    scope_path = Path(scope_path).resolve()
    output = scope_path.parents[3]
    if scope_path != output / 'metadata' / SCOPE:
        raise ValueError('Native Message scope must bind its own output metadata')
    local = scope.get('local_rebuild')
    if (not isinstance(local, dict) or set(local) != {'contract', 'receipt'}
            or local['contract'] != 'local-native-message-quest-narrative-v1'):
        raise ValueError('Native Message unknown local qualification')
    template = load_object(ROOT / SCOPE)
    if {k: v for k, v in scope.items() if k not in ('dependency', 'external_candidates', 'local_rebuild')} != {
            k: v for k, v in template.items() if k not in ('dependency', 'external_candidates')}:
        raise ValueError('Native Message local source scope drift')
    receipt_path = output / 'quest_narrative/verified.json'
    if file_seal(receipt_path) != local['receipt']:
        raise ValueError('Native Message QuestNarrative receipt seal')
    if scope['dependency'] != current_dependency(source, output):
        raise ValueError('Native Message current dependency binding drift')
    if file_seal(receipt_path) != local['receipt']:
        raise ValueError('Native Message QuestNarrative proof changed')
