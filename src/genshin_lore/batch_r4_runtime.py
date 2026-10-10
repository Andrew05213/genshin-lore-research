"""Exact R4 release qualification and six family-specific v2 delegates.

The expensive merge oracle runs at release, not on each server startup. One
exact file hash plus frozen release bindings qualify all compatibility reads.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .batch_r1_repository import R1FamilyProvider, _cursor_decode
from .batch_r2_retrieval import R2AcceptedFamilyProvider, build_batch_r2_providers
from .batch_r2_runtime import (
    BatchR2RuntimeError, R2FileIdentity, R2Qualification, R2_PROVIDER_ORDER,
    M5E_RUNTIME_MANIFEST_PATH, Q2_MANIFEST_PATH, load_m5e_manifest,
    load_q2_manifest, _strict_json, canonical_manifest_sha256, sha256_file,
)
from .batch_r3_uplift import R3_FAMILY_ORDER
from .db import validate_exact_schema_structure
from .family_evidence import FamilyEvidenceResult
from .util import canonical_json

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_CONTRACT = 'schema20-migration0020-additive-r4-exact'
RUNTIME_CONTRACT = 'batch-r4-schema20-runtime-v1'
CANDIDATE_SIZE = 5963210752
CANDIDATE_SHA256 = '6bef3518ad17df4a7a953d9d064ba4ee0569c86aeb13ae695092fc73b4d63ed1'
MANIFEST_SHA256 = '7b6da92c54c96b1aad2c74c04fdbb61a0de7d45d1fc2b91279944f0a5b08dc14'
MANIFEST_PATH = ROOT / 'manifests/batch/anime-game-data-7.0-batch-r4-schema20-runtime.json'
RECEIPT_PATH = ROOT / 'reports/r4/batch-r4-schema20.release-receipt.json'
R3_MANIFEST_PATH = ROOT / 'manifests/batch/anime-game-data-7.0-batch-r3-wave01-text-uplift.json'
PROVIDER_ORDER = (*R2_PROVIDER_ORDER, *R3_FAMILY_ORDER)
EVIDENCE_PATHS = (
    'reports/r4/build-a.json', 'reports/r4/build-b.json',
    'manifests/batch/anime-game-data-7.0-batch-r3-wave01-text-uplift.json',
    'manifests/batch/anime-game-data-7.0-batch-r2-schema19-runtime.json',
    'manifests/batch/anime-game-data-7.0-batch-m5e-wave02-runtime.json',
    'reports/q2/q2-coverage-matrix.json',
    'reports/r4/world-archive-schema20.release-receipt.json',
    'reports/r4/quest-codex-schema20.release-receipt.json',
)


def receipt_body(manifest):
    body = {
        'receipt_contract': 'genshin-lore-batch-r4-schema20-release-receipt-v1',
        'schema_version': 20, 'schema_contract': SCHEMA_CONTRACT,
        'database': {'size_bytes': CANDIDATE_SIZE, 'sha256': CANDIDATE_SHA256},
        'runtime_manifest_sha256': manifest['manifest_sha256'],
        'release_evidence': manifest['inputs'], 'provider_order': list(PROVIDER_ORDER),
        'product_accepted': True, 'corpus_complete': False,
        'stdio_accepted': False,
    }
    body['receipt_id'] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    return body


def load_bundle(manifest_path, receipt_path):
    manifest = _strict_json(manifest_path, 'R4 manifest')
    if manifest.get('manifest_sha256') != MANIFEST_SHA256 or canonical_manifest_sha256(manifest) != MANIFEST_SHA256:
        raise BatchR2RuntimeError('R4 tracked manifest mismatch')
    if (manifest.get('runtime_contract') != RUNTIME_CONTRACT
            or manifest.get('schema_version') != 20
            or manifest.get('schema_contract') != SCHEMA_CONTRACT
            or manifest.get('candidate') != {'size_bytes': CANDIDATE_SIZE, 'sha256': CANDIDATE_SHA256}
            or manifest.get('provider_order') != list(PROVIDER_ORDER)
            or manifest.get('product_accepted') is not True or manifest.get('corpus_complete') is not False):
        raise BatchR2RuntimeError('R4 release contract mismatch')
    receipt = _strict_json(receipt_path, 'R4 receipt')
    if receipt != receipt_body(manifest):
        raise BatchR2RuntimeError('R4 receipt binding mismatch')
    return manifest, receipt


def qualify_batch_r4(database, *, manifest_path, receipt_path):
    paths = tuple(Path(p).resolve() for p in (database, manifest_path, receipt_path))
    stability = tuple(R2FileIdentity.capture(p) for p in (*paths, *(ROOT / p for p in EVIDENCE_PATHS)))
    if stability[0].size_bytes != CANDIDATE_SIZE or sha256_file(paths[0]) != CANDIDATE_SHA256:
        raise BatchR2RuntimeError('exact Schema20 database seal mismatch')
    manifest, receipt = load_bundle(paths[1], paths[2])
    if set(manifest['inputs']) != set(EVIDENCE_PATHS):
        raise BatchR2RuntimeError('R4 release input closure mismatch')
    for relative in EVIDENCE_PATHS:
        if sha256_file(ROOT / relative) != manifest['inputs'][relative]:
            raise BatchR2RuntimeError(f'R4 release input changed: {relative}')
    legacy = load_m5e_manifest(M5E_RUNTIME_MANIFEST_PATH)
    q2 = load_q2_manifest(Q2_MANIFEST_PATH)
    r3 = json.loads(R3_MANIFEST_PATH.read_text())
    with sqlite3.connect(paths[0].as_uri() + '?mode=ro&immutable=1', uri=True) as conn:
        conn.execute('PRAGMA query_only=ON')
        validate_exact_schema_structure(conn, 20)
        if tuple(r[0] for r in conn.execute('SELECT version FROM schema_migration ORDER BY version')) != tuple(range(1,21)):
            raise BatchR2RuntimeError('R4 migration ledger mismatch')
        # Exact file seal already covers every business row. Do not reconstruct
        # projection proofs, hash all tables, or run full FK checks at startup.
        for item in (*legacy['families'], *q2['families'], *r3['families']):
            row = conn.execute('SELECT parser_build_id,status FROM ingest_run WHERE id=?', (item['ingest_run_id'],)).fetchone()
            if row != (item['parser_build_id'], 'complete'):
                raise BatchR2RuntimeError('R4 exact run binding mismatch')
    result = R2Qualification(
        str(paths[0]), str(paths[1]), str(paths[2]), MANIFEST_SHA256,
        receipt['receipt_id'], CANDIDATE_SHA256, CANDIDATE_SIZE, legacy, q2, stability,
        schema_version=20, schema_contract=SCHEMA_CONTRACT, provider_count=len(PROVIDER_ORDER),
    )
    result.check()
    return result


class R4TextProvider(R1FamilyProvider):
    """Run-filtered combined SQL, preserving the frozen R3 family payloads."""
    ROW_FIELDS = ('occurrence_id', 'ingest_run_id', 'parser_build_id', 'source_file_path',
                  'source_ordinal', 'source_pointer', 'raw_record_json', 'raw_record_sha256')

    def search_text(self, *, ingest_run_id, family_contract_version, query,
                    filters=None, cursor=None, limit=50):
        # Preserve R3's validation order and cursor shape. In particular an
        # empty position is invalid, not an instruction to restart paging.
        if not isinstance(query, str) or not query.strip():
            raise ValueError('query must contain non-whitespace text')
        error = self._scope(ingest_run_id, family_contract_version)
        if error is not None:
            return error
        selected = filters or {}
        if set(selected) - {'locale', 'field_name'}:
            raise ValueError('unsupported filters')
        position = _cursor_decode(cursor, {'family': self.family, 'run': ingest_run_id,
                                           'query': query, 'filters': selected})
        if position is not None and len(position) != 4:
            raise ValueError('cursor position is invalid')
        return super().search_text(ingest_run_id=ingest_run_id,
            family_contract_version=family_contract_version, query=query,
            filters=filters, cursor=cursor, limit=limit)

    def _result(self, status, data):
        if 'item' in data:
            data['item'] = {k: data['item'][k] for k in self.ROW_FIELDS}
        for row in data.get('items', []):
            if 'raw_record_json' in row:
                for key in tuple(row):
                    if key not in self.ROW_FIELDS:
                        del row[key]
            else:
                row.pop('ingest_run_id', None)
        owners = data.get('text_owners', [])
        owners.sort(key=lambda o: (o['field_name'], o['field_pair_ordinal'], o['owner_id']))
        for owner in owners:
            owner.pop('ingest_run_id', None)
            owner['candidates'].sort(key=lambda c: (c['relative_path'], c['pair_ordinal'], c['candidate_id']))
            for child in (*owner['resolutions'], *owner['candidates']):
                child.pop('ingest_run_id', None)
        return FamilyEvidenceResult(status, self.family, self.run, self.family_contract_version,
                                    'R1_declared_text_fields_processed', False, data)


def build_batch_r4_providers(db, qualification):
    if qualification.schema_version != 20:
        raise BatchR2RuntimeError('Schema20 qualification required')
    compatibility = build_batch_r2_providers(db, qualification)
    manifest = json.loads(R3_MANIFEST_PATH.read_text())
    added = tuple(R2AcceptedFamilyProvider(R4TextProvider(db.path, item), qualification,
                  'R1_declared_text_fields_processed') for item in manifest['families'])
    providers = (*compatibility, *added)
    if tuple(p.family for p in providers) != PROVIDER_ORDER:
        raise BatchR2RuntimeError('R4 provider order mismatch')
    qualification.check()
    return providers
