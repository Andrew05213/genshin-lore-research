"""Independent R4 merge oracle: immutable baseline plus exact frozen R3 rows."""
from __future__ import annotations

import hashlib
import itertools
import json
import sqlite3
from pathlib import Path

from .batch_r2_runtime import R2_CANDIDATE_SHA256, R2_CANDIDATE_SIZE
from .batch_r3_uplift import sha256_file, R3_FAMILY_ORDER
from .db import validate_exact_schema_structure
from .util import canonical_json

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / 'manifests/batch/anime-game-data-7.0-batch-r3-wave01-text-uplift.json'
MANIFEST_SHA = '1d599022ba3e44b6aae8ae0e78e7ae1ab0e68f0e903aae84521451868e2edf6b'
STAMP = '2026-09-09T00:00:00+00:00'


def _id(*parts):
    return hashlib.sha256(b''.join(str(p).encode('utf-8', 'surrogateescape') + b'\0' for p in parts)).hexdigest()


def _rows(conn, schema, table, where='', args=()):
    columns = list(conn.execute(f'PRAGMA {schema}.table_info("{table}")'))
    order = [r[1] for r in sorted(columns, key=lambda r: r[5]) if r[5]] or [r[1] for r in columns]
    ordering = ','.join('"' + c + '"' for c in order)
    return conn.execute(f'SELECT * FROM {schema}."{table}" {where} ORDER BY {ordering}', args)


def _equal(left, right, label):
    sentinel = object()
    count = 0
    digest = hashlib.sha256()
    for a, b in itertools.zip_longest(left, right, fillvalue=sentinel):
        if a != b:
            raise ValueError(f'exact rowset mismatch: {label}, row {count}')
        # SQLite FTS shadow tables contain BLOBs. Tag them so their digest
        # cannot collide with a text value containing the same hex spelling.
        encoded = [{'blob_hex': v.hex()} if isinstance(v, bytes) else v for v in a]
        digest.update(canonical_json(encoded).encode() + b'\n')
        count += 1
    return {'row_count': count, 'logical_sha256': digest.hexdigest()}


def verify(candidate, baseline, artifact):
    candidate, baseline, artifact = map(lambda p: Path(p).resolve(), (candidate, baseline, artifact))
    if baseline.stat().st_size != R2_CANDIDATE_SIZE or sha256_file(baseline) != R2_CANDIDATE_SHA256:
        raise ValueError('verifier baseline seal mismatch')
    if sha256_file(MANIFEST) != MANIFEST_SHA:
        raise ValueError('verifier R3 manifest seal mismatch')
    manifest = json.loads(MANIFEST.read_text())
    if artifact.stat().st_size != manifest['artifact']['size_bytes'] or sha256_file(artifact) != manifest['artifact']['sha256']:
        raise ValueError('verifier R3 artifact seal mismatch')
    uri = lambda path: path.as_uri() + '?mode=ro&immutable=1'
    conn = sqlite3.connect(uri(candidate), uri=True)
    try:
        conn.execute('PRAGMA query_only=ON')
        validate_exact_schema_structure(conn, 20)
        if conn.execute('PRAGMA quick_check').fetchone() != ('ok',) or conn.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('database integrity/FK check failed')
        conn.execute('ATTACH DATABASE ? AS baseline', (uri(baseline),))
        conn.execute('ATTACH DATABASE ? AS r3', (uri(artifact),))
        tables = lambda schema: {r[0] for r in conn.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        old = tables('baseline')
        new = {f'{f}_{s}' for f in R3_FAMILY_ORDER for s in ('text_owner_occurrence','text_resolution_occurrence','text_candidate_occurrence')}
        if tables('main') != old | new:
            raise ValueError('table closure mismatch')
        runs = tuple(item['ingest_run_id'] for item in manifest['families'])
        parsers = tuple(item['parser_build_id'] for item in manifest['families'])
        row_tables = {f + '_row_occurrence' for f in R3_FAMILY_ORDER}
        seals = {}
        for table in sorted(old):
            where, args = '', ()
            if table in row_tables:
                where, args = 'WHERE ingest_run_id NOT IN (?,?,?,?,?,?)', runs
            elif table == 'parser_build':
                where, args = 'WHERE id NOT IN (?,?,?,?,?,?)', parsers
            elif table == 'ingest_run':
                where, args = 'WHERE id NOT IN (?,?,?,?,?,?)', runs
            elif table == 'schema_migration':
                where = 'WHERE version != 20'
            elif table == 'batch_wave':
                where, args = 'WHERE id != ?', ('batch-wave:r4:' + MANIFEST_SHA,)
            seals[table] = _equal(_rows(conn,'baseline',table), _rows(conn,'main',table,where,args), table)
        if conn.execute('SELECT version,applied_at FROM schema_migration WHERE version=20').fetchall() != [(20,STAMP)]:
            raise ValueError('migration ledger addition mismatch')
        families = []
        for item in manifest['families']:
            f, run, parser = (item[k] for k in ('family','ingest_run_id','parser_build_id'))
            snap, config = manifest['snapshot_id'], item['configuration_sha256']
            expected_parser = (parser, manifest['contract'], item['family_contract_version'], _id('parser',parser,config),config,STAMP)
            if conn.execute('SELECT * FROM parser_build WHERE id=?',(parser,)).fetchone() != expected_parser:
                raise ValueError('parser provenance mismatch')
            scope = {'family':f,'family_contract_version':item['family_contract_version'],'snapshot_id':snap,
                     'source_file':item['source_file'],'exact_ingest_run_required':True,'physical_container_scope':'single_source_row'}
            config_json = {'projection_manifest_sha256':MANIFEST_SHA,'declared_text_fields':item['declared_text_fields'],
                           'declared_relations':[],'textmap_corpus':manifest['textmap_corpus']}
            expected_run = (run,snap,parser,canonical_json(scope),canonical_json(config_json),'complete',STAMP)
            if conn.execute('SELECT * FROM ingest_run WHERE id=?',(run,)).fetchone() != expected_run:
                raise ValueError('run provenance mismatch')
            expected = []
            for r in conn.execute(f'SELECT * FROM r3.{f}_row_occurrence ORDER BY occurrence_id'):
                oid,_,_,path,ordinal,pointer,token,checksum = r
                expected.append((oid,f,item['family_contract_version'],run,snap,parser,path,_id(snap,path,pointer),
                                 pointer,ordinal,token,checksum,'[]','raw_text_fields_observed',
                                 canonical_json({'coverage':'R1_declared_text_fields_processed','declared_text_fields_processed':True,
                                                 'declared_relations_processed':True,'logical_identity_created':False})))
            _equal(expected, conn.execute(f'SELECT * FROM {f}_row_occurrence WHERE ingest_run_id=? ORDER BY occurrence_id',(run,)), f)
            for suffix in ('text_owner_occurrence','text_resolution_occurrence','text_candidate_occurrence'):
                table = f'{f}_{suffix}'
                cols = [r[1] for r in conn.execute(f'PRAGMA r3.table_info({table})')]
                selected = ','.join(cols)
                if conn.execute(f'SELECT COUNT(*) FROM {table} WHERE ingest_run_id != ?',(run,)).fetchone()[0]:
                    raise ValueError('foreign run in new text table')
                _equal(conn.execute(f'SELECT {selected} FROM r3.{table} ORDER BY {cols[0]}'),
                       conn.execute(f'SELECT {selected} FROM {table} WHERE ingest_run_id=? ORDER BY {cols[0]}',(run,)),table)
            families.append({'family':f,'ingest_run_id':run,'exact_projection':True})
        wave = ('batch-wave:r4:'+MANIFEST_SHA, manifest['snapshot_id'],'batch-r4-schema20-wave01-text-v1',MANIFEST_SHA,20,'complete',
                canonical_json({'families':list(R3_FAMILY_ORDER),'corpus_complete':False,'source_projection_sha256':manifest['artifact']['sha256']}))
        if conn.execute('SELECT * FROM batch_wave WHERE id=?',(wave[0],)).fetchone() != wave:
            raise ValueError('wave metadata mismatch')
    finally:
        conn.close()
    return {'status':'pass','contract':'batch-r4-independent-merge-v1','candidate_sha256':sha256_file(candidate),
            'migration_sha256':sha256_file(ROOT/'migrations/0020_batch_r4_wave01_text_projection.sql'),
            'protected_tables':seals,'families':families,'r3_manifest_sha256':MANIFEST_SHA,
            'schema_version':20,'foreign_keys':'pass','quick_check':'pass'}
