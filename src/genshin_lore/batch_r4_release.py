"""R4 deterministic additive merge of the frozen R3 family projections."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path

from .batch_release import assert_output_paths, create_private_staging, fsync_file, publish_absent
from .batch_r1_schema19 import _id
from .batch_r2_runtime import R2_CANDIDATE_SHA256, R2_CANDIDATE_SIZE, R2FileIdentity
from .batch_r3_uplift import R3_FAMILY_ORDER, qualify_r3_artifact, sha256_file
from .batch_space import ProjectSpaceGate, ensure_owned_tmp_root
from .db import Database
from .util import canonical_json

ROOT = Path(__file__).resolve().parents[2]
R3_MANIFEST = ROOT / 'manifests/batch/anime-game-data-7.0-batch-r3-wave01-text-uplift.json'
R3_MANIFEST_SHA = '1d599022ba3e44b6aae8ae0e78e7ae1ab0e68f0e903aae84521451868e2edf6b'
STAMP = '2026-09-09T00:00:00+00:00'
CONTRACT = 'batch-r4-schema20-wave01-text-v1'
SCHEMA_CONTRACT = 'schema20-migration0020-additive-r4-exact'
SUFFIXES = ('row_occurrence', 'text_owner_occurrence', 'text_resolution_occurrence', 'text_candidate_occurrence')


def load_projection():
    if sha256_file(R3_MANIFEST) != R3_MANIFEST_SHA:
        raise ValueError('R3 manifest seal mismatch')
    return json.loads(R3_MANIFEST.read_text())


def _append_family(conn, item, manifest):
    family, run, parser = (item[k] for k in ('family', 'ingest_run_id', 'parser_build_id'))
    snapshot = manifest['snapshot_id']
    config = item['configuration_sha256']
    conn.execute('INSERT INTO parser_build VALUES(?,?,?,?,?,?)',
                 (parser, manifest['contract'], item['family_contract_version'],
                  _id('parser', parser, config), config, STAMP))
    scope = {'family': family, 'family_contract_version': item['family_contract_version'],
             'snapshot_id': snapshot, 'source_file': item['source_file'],
             'exact_ingest_run_required': True, 'physical_container_scope': 'single_source_row'}
    config_json = {'projection_manifest_sha256': R3_MANIFEST_SHA,
                   'declared_text_fields': item['declared_text_fields'], 'declared_relations': [],
                   'textmap_corpus': manifest['textmap_corpus']}
    conn.execute('INSERT INTO ingest_run VALUES(?,?,?,?,?,?,?)',
                 (run, snapshot, parser, canonical_json(scope), canonical_json(config_json), 'complete', STAMP))
    rows = conn.execute(f'SELECT * FROM r3.{family}_row_occurrence ORDER BY source_ordinal,occurrence_id')
    values = []
    for row in rows:
        oid, _, _, path, ordinal, pointer, token, checksum = row
        values.append((oid, family, item['family_contract_version'], run, snapshot, parser, path,
                       _id(snapshot, path, pointer), pointer, ordinal, token, checksum, '[]',
                       'raw_text_fields_observed', canonical_json({'coverage': 'R1_declared_text_fields_processed',
                       'declared_text_fields_processed': True, 'declared_relations_processed': True,
                       'logical_identity_created': False})))
    conn.executemany(f'INSERT INTO {family}_row_occurrence VALUES({",".join("?" for _ in range(15))})', values)
    for suffix in SUFFIXES[1:]:
        table = f'{family}_{suffix}'
        columns = [r[1] for r in conn.execute(f'PRAGMA r3.table_info({table})')]
        selected = ','.join(columns)
        conn.execute(f'INSERT INTO {table} ({selected},ingest_run_id) '
                     f'SELECT {selected},? FROM r3.{table} ORDER BY {columns[0]}', (run,))


def build(baseline, artifact, output, *, invocation_id):
    baseline, artifact, target = map(lambda p: Path(p).absolute(), (baseline, artifact, output))
    inputs = (('baseline', baseline), ('projection', artifact), ('manifest', R3_MANIFEST))
    assert_output_paths(target, inputs=inputs)
    baseline_identity = R2FileIdentity.capture(baseline)
    projection_identity = R2FileIdentity.capture(artifact)
    if baseline_identity.size_bytes != R2_CANDIDATE_SIZE or sha256_file(baseline) != R2_CANDIDATE_SHA256:
        raise ValueError('exact accepted Schema19 baseline required')
    manifest = load_projection()
    qualify_r3_artifact(artifact, R3_MANIFEST)
    owned = ensure_owned_tmp_root(target.parent, owner='batch-r4', invocation_id=invocation_id)
    gate = ProjectSpaceGate(ROOT, accounting_tmp_roots=(owned,), preserve_paths=(baseline, artifact))
    # Includes a full baseline copy plus 1.25 GiB for new pages/indexes/journals.
    gate.check('r4-preflight', projected_bytes=R2_CANDIDATE_SIZE + 1342177280)
    fd, stage = create_private_staging(target, prefix='.r4-')
    os.close(fd)
    try:
        shutil.copyfile(baseline, stage)
        with Database(stage) as db:
            db.initialize_through(20)
            db.conn.execute('UPDATE schema_migration SET applied_at=? WHERE version=20', (STAMP,))
            db.conn.commit()
        with sqlite3.connect(stage) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('ATTACH DATABASE ? AS r3', (artifact.as_uri() + '?mode=ro&immutable=1',))
            for item in manifest['families']:
                _append_family(conn, item, manifest)
            conn.execute('INSERT INTO batch_wave VALUES(?,?,?,?,?,?,?)',
                         ('batch-wave:r4:' + R3_MANIFEST_SHA, manifest['snapshot_id'], CONTRACT,
                          R3_MANIFEST_SHA, 20, 'complete', canonical_json({'families': list(R3_FAMILY_ORDER),
                          'corpus_complete': False, 'source_projection_sha256': manifest['artifact']['sha256']})))
        if stage.stat().st_size - R2_CANDIDATE_SIZE > 4 * 1024**3:
            raise ValueError('candidate increment exceeds 4 GiB')
        gate.check('r4-before-verification')
        from .batch_r4_verifier import verify
        verification = verify(stage, baseline, artifact)
        baseline_identity.check()
        projection_identity.check()
        fsync_file(stage)
        publish_absent(target, stage, inputs=inputs)
        stage = None
        return {'status': 'verified_candidate', 'schema_version': 20,
                'candidate': {'size_bytes': target.stat().st_size, 'sha256': verification['candidate_sha256']},
                'verification': verification, 'product_accepted': False}
    finally:
        if stage is not None:
            for suffix in ('', '-journal', '-wal', '-shm'):
                Path(str(stage) + suffix).unlink(missing_ok=True)
