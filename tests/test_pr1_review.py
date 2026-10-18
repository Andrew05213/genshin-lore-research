"""Regression probes from the independent PR #1 review; synthetic data only."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from scripts.build import Rebuild, seal
from scripts.validators import quest_review_binding_v2 as review
from scripts.validators.research_dataset import verify_connection
from scripts.validators.research_dataset import verify_required_source_projections
from genshin_lore import research_index


@pytest.mark.parametrize('explicit', [False, True])
def test_review_oracle_reads_dependency_manifest_and_checks_its_binding(tmp_path, monkeypatch, explicit):
    artifact = tmp_path/'artifact.sqlite'
    artifact.write_bytes(b'synthetic candidate')
    dependency = tmp_path/'dependency.sqlite'
    dependency.write_bytes(b'synthetic dependency')
    entry = {'acceptance_status': 'PHYSICAL_FOUNDATION_ACCEPTED',
             'source_snapshot': review.SNAPSHOT, 'contract': 'fixture',
             'parser_build_id': 'fixture-parser', 'ingest_run_id': 'fixture-run',
             'artifact': {**seal(dependency), 'path_machine_local': str(dependency)}}
    manifest = tmp_path/'dependencies.json'
    manifest.write_text(json.dumps({'components': {
        'quest_review_archive': entry, 'quest_narrative_archive': entry}}))
    report = tmp_path/'build.json'
    report.write_text(json.dumps({'artifact': seal(artifact), 'release': {
        'contract': 'quest-review-7.1-exact-target-binding-v2', 'status': 'candidate',
        'snapshot': review.SNAPSHOT, 'product_accepted': False,
        'dependencies': {'manifest_sha256': 'incorrect binding'}}}))
    # Only Git discovery is stubbed. Real path reads, artifact hashes and the
    # oracle's dependency-binding rejection must execute before any source SQL.
    monkeypatch.setattr(review.subprocess, 'check_output',
                        lambda *a, **k: '\n'.join([review.COMMIT, review.TREE]))
    monkeypatch.setattr(review, 'MANIFEST', manifest if not explicit else tmp_path/'absent.json')
    with pytest.raises(ValueError, match='dependency binding'):
        review.verify(tmp_path/'source', artifact, report,
                      **({'manifest_path': manifest} if explicit else {}))


def test_dependency_receipt_cannot_be_relabelled_as_another_stage(tmp_path):
    job = object.__new__(Rebuild)
    job.output = tmp_path/'output'
    job.output.mkdir()
    job.source, job.history, job.identity_root = tmp_path/'source', None, tmp_path/'code'
    def builder(path):
        path.write_bytes(b'synthetic stage')
        return {'artifact': seal(path)}
    job.task('original', builder, lambda *_: {'status': 'PASS'})
    shutil.copytree(job.output/'original', job.output/'different')
    with pytest.raises(ValueError, match='dependency stage identity'):
        job.dependency_inputs('different')


def test_metadata_directory_symlink_cannot_write_into_another_output(tmp_path):
    job = object.__new__(Rebuild)
    job.output = tmp_path/'output'
    job.output.mkdir()
    sibling = tmp_path/'sibling'
    sibling.mkdir()
    (job.output/'metadata').symlink_to(sibling, target_is_directory=True)
    with pytest.raises(ValueError, match='metadata path outside build output'):
        job.bind('manifests/fixture.json', {'synthetic': True})
    assert not (sibling/'manifests').exists()


def small_index(tmp_path):
    """Use the actual projector and full source oracle, not mocked row counts."""
    source = tmp_path/'source.sqlite'
    with sqlite3.connect(source) as db:
        db.executescript('''
            CREATE TABLE loading_tip_row(id TEXT PRIMARY KEY,source_path,pointer);
            CREATE TABLE loading_tip_slot(id TEXT PRIMARY KEY,row_id,field,locale);
            CREATE TABLE loading_tip_text(id TEXT PRIMARY KEY,slot_id,raw_text,source_path,pointer);
            INSERT INTO loading_tip_row VALUES('row','synthetic.json','/0');
            INSERT INTO loading_tip_slot VALUES('slot','row','body','CHS');
            INSERT INTO loading_tip_text VALUES('text','slot','synthetic text','synthetic.json','/0/body');
        ''')
    provenance = tmp_path/'provenance.sqlite'
    with sqlite3.connect(provenance) as db:
        db.execute('CREATE TABLE source_file(relative_path,source_sha256)')
        db.execute("INSERT INTO source_file VALUES('synthetic.json','synthetic-file-seal')")
    component = {'source_key': 'fixture', 'family': 'loading_tips_archive',
                 'contract': 'fixture-contract', 'parser': 'fixture-parser',
                 'run': 'fixture-run', 'artifact': seal(source), 'adapter': 'formal_compatible'}
    manifest = {'components': [component], 'provenance_dependency': seal(provenance)}
    bindings = {'artifacts': {'fixture': str(source)}, 'provenance_artifact': str(provenance)}
    src = sqlite3.connect(source)
    src.row_factory = sqlite3.Row
    index = sqlite3.connect(':memory:')
    index.row_factory = sqlite3.Row
    index.executescript(research_index.SCHEMA)
    index.execute('INSERT INTO source_artifact VALUES(?,?,?,?,?,?,?,?)',
                  ('fixture', component['family'], component['contract'], component['parser'],
                   component['run'], str(source), component['artifact']['size_bytes'],
                   component['artifact']['sha256']))
    research_index._component(src, index, 'fixture', component,
                             {'synthetic.json': ('synthetic-file-seal', None)}, 0, 0)
    src.close()
    assert verify_connection(index, manifest, bindings)['texts'] == 1
    return index, manifest, bindings


def test_whole_text_projection_and_its_declaration_cannot_disappear(tmp_path):
    index, manifest, bindings = small_index(tmp_path)
    index.execute('DELETE FROM research_text')
    index.execute("DELETE FROM adapter_coverage WHERE projection_kind='text'")
    with pytest.raises(ValueError, match='required source projection'):
        verify_connection(index, manifest, bindings)
    index.close()


def test_complete_text_projection_cannot_be_downgraded_to_unsupported(tmp_path):
    index, manifest, bindings = small_index(tmp_path)
    index.execute('DELETE FROM research_text')
    index.execute("UPDATE adapter_coverage SET status='unsupported',indexed_count=0 WHERE projection_kind='text'")
    with pytest.raises(ValueError, match='required source projection'):
        verify_connection(index, manifest, bindings)
    index.close()


def test_source_oracle_script_runs_without_cwd_import_fallback(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root/'scripts/validators/research_dataset.py'), '--help'],
                            cwd=tmp_path, env={**os.environ, 'PYTHONPATH': str(root/'src')},
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_real_synthetic_index_publication_and_exact_resume(tmp_path):
    from scripts.builders.research_dataset import build
    index, manifest, bindings = small_index(tmp_path)
    index.close()
    manifest['corpus_ref'] = 'synthetic-corpus'
    corpus = tmp_path/'metadata/manifests/research/corpus.json'
    corpus.parent.mkdir(parents=True)
    corpus.write_text(json.dumps(manifest))
    job = object.__new__(Rebuild)
    job.output, job.source, job.history, job.identity_root = tmp_path, tmp_path/'source', None, tmp_path/'code'
    reports = tmp_path/'oracles'
    def verifier(path, report):
        return json.loads((reports/f'index-memory-oracle-{path.stem}.json').read_text())
    result = job.task('research_index',
        lambda path: build(manifest, bindings, path, 100_000_000, corpus_path=corpus, reports_dir=reports),
        verifier, inputs=[corpus, Path(bindings['artifacts']['fixture']), Path(bindings['provenance_artifact'])])
    assert result['text_rows'] == 1
    assert job.task('research_index', lambda *_: pytest.fail('unexpected rebuild'),
                    lambda *_: pytest.fail('unexpected reverify'),
                    inputs=[corpus, Path(bindings['artifacts']['fixture']), Path(bindings['provenance_artifact'])]) == result


def test_explicit_nonprojected_dependencies_and_unsupported_relations_stay_legal():
    index = sqlite3.connect(':memory:')
    index.row_factory = sqlite3.Row
    index.executescript(research_index.SCHEMA)
    source = sqlite3.connect(':memory:')
    # A legal partial projection has actual source rows outside the indexed
    # parent namespace, not invented denominators or missing FK columns.
    source.execute('CREATE TABLE document_relation(id PRIMARY KEY,row_id)')
    source.execute('CREATE TABLE document_candidate(id PRIMARY KEY,relation_id)')
    source.execute("INSERT INTO document_relation VALUES('relation','outside-index-namespace')")
    source.execute("INSERT INTO document_candidate VALUES('candidate','relation')")
    component = {'source_key': 'fixture', 'family': 'document_archive', 'adapter': 'formal_compatible'}
    for kind, table in [('relation', 'document_relation'), ('candidate', 'document_candidate')]:
        index.execute('INSERT INTO adapter_coverage VALUES(?,?,?,?,?,?,?,?)',
                      ('fixture', 'unsupported', kind, table, 1, 0, 'unsupported', 'unresolved scope'))
    verify_required_source_projections(index, {'components': [component]}, {'fixture': source})
    verify_required_source_projections(index, {'components': [
        {'source_key': 'not-projected', 'adapter': 'dependency_only'}]}, {})
    source.close()
    index.close()
