"""Independent complete container/text/relation source-rowset reconciliation.

No builder projection/normalization helpers imported. The physical SQLite
reader and hashing are shared utilities, not business projection functions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

# Also support the documented source-checkout script invocation from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from genshin_lore.research_index import sha256_file
from genshin_lore.research_adapters import CANDIDATE_UNSUPPORTED_RELATIONS, RELATIONS
from scripts.validators.research_index import expected_adapter_tables, explicit_text_tables

KEYS={}


class SourceRows:
    """Bounded physical-key reads owned by one independent oracle invocation.

    Requests keep their ordinals: equal keys are not merged, and a partial
    composite key must still match exactly one source row. No builder output
    or cross-artifact cache is used to reconstruct source evidence.
    """

    def __init__(self, sources, batch_size=256):
        if not 1 <= batch_size <= 256:
            raise ValueError('oracle batch size outside 1..256')
        self.sources = sources
        self.batch_size = batch_size
        self.schemas = {}

    def schema(self, src, table):
        if not table.replace('_', '').isalnum():
            raise ValueError('oracle unsafe source table')
        key = (src, table)
        if key not in self.schemas:
            rows = list(src.execute(f'PRAGMA table_info("{table}")'))
            self.schemas[key] = (
                [r[1] for r in sorted(rows, key=lambda r: r[5]) if r[5]],
                {r[1] for r in rows},
            )
        return self.schemas[key]

    def columns(self, src, table):
        return self.schema(src, table)[1]

    def rows(self, index, query):
        cursor = index.execute(query)
        while batch := cursor.fetchmany(self.batch_size):
            groups = {}
            result = {}
            for ordinal, row in enumerate(batch):
                groups.setdefault((row['source_key'], row['source_table'], row['pk_column']), []).append((ordinal, row))
            for (source_key, table, column), requests in groups.items():
                src = self.sources[source_key]
                keys, columns = self.schema(src, table)
                names = json.loads(column) if column.startswith('[') else [column]
                if (names != keys and not (len(names) == 1 and names[0] in keys)):
                    raise ValueError('independent physical key scope mismatch')
                if '__oracle_request' in columns:
                    raise ValueError('oracle reserved request column collision')
                # Stay below SQLite's portable 999-variable budget even for
                # composite keys. Every requested occurrence remains distinct.
                step = min(self.batch_size, max(1, 900 // max(1, len(names))))
                for start in range(0, len(requests), step):
                    selected = requests[start:start + step]
                    parameters = []
                    values_sql = []
                    for ordinal, row in selected:
                        values = json.loads(row['pk_value']) if column.startswith('[') else [row['pk_value']]
                        if len(values) != len(names):
                            raise ValueError('independent physical key scope mismatch')
                        values_sql.append('(' + str(ordinal) + ',' + ','.join('?' for _ in values) + ')')
                        parameters.extend(values)
                    aliases = ','.join(f'k{i}' for i in range(len(names)))
                    terms = ' AND '.join(f's."{name}"=q.k{i}' for i, name in enumerate(names))
                    query = (f'WITH q(__oracle_request,{aliases}) AS (VALUES ' + ','.join(values_sql) + ') '
                             f'SELECT q.__oracle_request,s.* FROM q JOIN "{table}" s ON {terms}')
                    physical_rows = src.execute(query, parameters)
                    field_names = [field[0] for field in physical_rows.description[1:]]
                    for physical in physical_rows:
                        ordinal = physical[0]
                        if ordinal in result:
                            raise ValueError('index source occurrence absent or ambiguous across exact runs')
                        # Row[name] linearly searches column names. Materialize
                        # all values in source-column order once, not N scans
                        # for N columns on every physical record.
                        result[ordinal] = dict(zip(field_names, tuple(physical)[1:]))
                    if any(ordinal not in result for ordinal, _ in selected):
                        raise ValueError('index source occurrence absent or ambiguous across exact runs')
            for ordinal, row in enumerate(batch):
                yield row, result[ordinal]

# Independent source contracts, not the builder's emitted coverage rows or
# projection functions. None means that this adapter deliberately has no
# container/text projection (e.g. binding-only and character text overlays).
SOURCE_PROJECTIONS = {
    'character_foundation': ('foundation_primary_row', None),
    'character_text': (None, 'candidate'),
    'quest_review_binding': (None, None),
    'quest_narrative_archive': ('excel_row', 'text_candidate'),
    'quest_review_archive': ('evidence_row', 'text_candidate'),
    'codexquest_archive': ('codex_root', 'codex_text_candidate'),
    'native_message': ('native71_row', 'native71_text'),
    'event_native_text_17': ('event71_row', 'event71_text'),
    'native_successor_physical': ('row_occurrence', 'text_candidate'),
    'native_high_value_physical': ('row_occurrence', 'text_candidate'),
    'event_physical_13': ('physical_row', 'textmap_candidate'),
    'event_physical_16': ('selected_occurrence', None),
    'event_physical_18': ('selected_occurrence', None),
    'quest_talk_physical': ('physical_container', 'text_slot'),
    'narrative_npc': ('physical_row', 'text_candidate'),
    'narrative_dialog': ('physical_row', 'text_candidate'),
    'narrative_reminder': ('physical_row', 'text_candidate'),
}
SOURCE_RELATIONS = {
    'character_foundation': ('foundation_relation', 'foundation_candidate'),
    'quest_review_binding': ('bound_locator', 'bound_candidate'),
    'native_message': ('native71_relation', 'native71_candidate'),
    'event_native_text_17': ('event71_relation', 'event71_candidate'),
    'native_successor_physical': ('relation_locator', 'relation_candidate'),
    'native_high_value_physical': ('relation_locator', 'relation_candidate'),
    'codexquest_archive': ('codex_navigation', 'codex_candidate'),
    'narrative_npc': ('locator', 'physical_candidate'),
    'narrative_dialog': ('locator', 'physical_candidate'),
    'narrative_reminder': ('locator', 'physical_candidate'),
}


def verify_formal_relation_projections(index, src, key, family, declarations):
    """Derive supported relation/candidate counts from source, not coverage.

    Formal adapters may exclude relations whose physical parent namespace is
    not indexed. Such partial scopes remain legal, but their projected subset
    cannot be reduced to zero by editing a derived status/count. Candidate
    expectations use SOURCE relation IDs, never surviving index relations.
    Only the static table/FK contract is shared with the builder.
    """
    specs = RELATIONS.get(family, ())
    if not specs:
        return
    tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    parents = set()
    for identity, column, value in index.execute(
        'SELECT occurrence_id,pk_column,pk_value FROM research_container WHERE source_key=?', (key,)):
        # These formal adapters map the source id PK directly. Do not let an
        # edited derived occurrence_id redefine which source parents qualify;
        # fetch() subsequently verifies every physical source key and row.
        if column != 'id' or identity != value:
            raise ValueError('formal relation projection parent identity differs from source key: '+key)
        parents.add(value)
    for spec in specs:
        relation, candidate = spec['table'], spec['candidate']
        if relation not in tables:
            continue
        wanted = set()
        relation_total = 0
        for identity, parent in src.execute(f'SELECT id,"{spec["parent"]}" FROM "{relation}"'):
            relation_total += 1
            if str(parent) in parents:
                wanted.add(str(identity))
        candidate_total = candidate_expected = 0
        if candidate in tables:
            for (parent,) in src.execute(f'SELECT relation_id FROM "{candidate}"'):
                candidate_total += 1
                candidate_expected += str(parent) in wanted
        for kind, table, denominator, expected in (
            ('relation', relation, relation_total, len(wanted)),
            ('candidate', candidate, candidate_total, candidate_expected),
        ):
            declaration = declarations.get((kind, table))
            status = 'complete' if expected == denominator else 'unsupported'
            if kind == 'relation':
                actual = index.execute('SELECT COUNT(*) FROM research_relation WHERE source_key=? AND source_table=?',
                                       (key, table)).fetchone()[0]
            else:
                actual = index.execute('SELECT COUNT(*) FROM research_candidate c JOIN research_relation r ON r.id=c.relation_id '
                                       'WHERE r.source_key=? AND c.source_table=?', (key, table)).fetchone()[0]
            if (declaration is None or actual != expected or
                (declaration['denominator'], declaration['indexed_count'], declaration['status']) !=
                    (denominator, expected, status)):
                raise ValueError('formal relation projection differs from source parent scope: '+key+': '+table)


def verify_required_source_projections(index, manifest, sources):
    """Coverage cannot choose its own denominator or downgrade required work."""
    for component in manifest['components']:
        key, mode = component['source_key'], component['adapter']
        if mode == 'dependency_only':
            if key not in sources:
                continue
            required = set()
            full = set()
        src = sources[key]
        if mode in ('core33', 'formal_compatible', 'talk_physical'):
            required = expected_adapter_tables(src, component['family'])
            full = {(kind, table) for kind, table in required if kind == 'container' or
                    (kind == 'text' and table in explicit_text_tables(src, component['family']))}
        elif mode != 'dependency_only':
            if mode not in SOURCE_PROJECTIONS:
                raise ValueError('unknown required source projection contract: '+mode)
            required = {(kind, table) for kind, table in
                        zip(('container', 'text'), SOURCE_PROJECTIONS[mode]) if table is not None}
            required.update(zip(('relation', 'candidate'), SOURCE_RELATIONS.get(mode, ())))
            full = required
        required = required | {(kind, table) for kind, table in CANDIDATE_UNSUPPORTED_RELATIONS.get(mode, ())
                               if src.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()}
        declarations = {(r['projection_kind'], r['source_table']): r for r in index.execute(
            'SELECT * FROM adapter_coverage WHERE source_key=?', (key,))}
        if not required <= declarations.keys():
            missing = ', '.join(kind+':'+table for kind, table in sorted(required-declarations.keys()))
            raise ValueError('required source projection declaration missing: '+key+': '+missing)
        for projection in full:
            row = declarations[projection]
            if row['status'] != 'complete' or row['indexed_count'] != row['denominator']:
                raise ValueError('required source projection incomplete: '+key+': '+projection[1])
        if mode in ('core33', 'formal_compatible', 'talk_physical'):
            verify_formal_relation_projections(index, src, key, component['family'], declarations)


def verify_source_declarations(index, manifest):
    """An omitted component must not turn an empty loop into success."""
    expected = {c['source_key']: c for c in manifest['components']}
    if len(expected) != len(manifest['components']):
        raise ValueError('duplicate manifest source key')
    actual = {r['source_key']: r for r in index.execute('SELECT * FROM source_artifact')}
    if set(actual) != set(expected):
        raise ValueError('index source component rowset differs from manifest')
    covered = {r[0] for r in index.execute('SELECT DISTINCT source_key FROM adapter_coverage')}
    for key, component in expected.items():
        row = actual[key]
        for field in ('family', 'contract', 'parser', 'run'):
            if row['artifact_'+field] != component[field]:
                raise ValueError('index source identity differs from manifest: '+key)
        if (row['size_bytes'], row['sha256']) != (component['artifact']['size_bytes'], component['artifact']['sha256']):
            raise ValueError('index source seal differs from manifest: '+key)
        if component['adapter'] != 'dependency_only' and key not in covered:
            raise ValueError('component projection coverage is missing: '+key)
    if not covered <= set(expected):
        raise ValueError('coverage references undeclared component')


def verify_projection_counts(index):
    """Check actual index rowsets, not the builder's indexed_count claims.

    Every present row is subsequently source-reconstructed by this oracle.
    Counts plus unique physical locators prevent omission/duplication from
    being hidden by an unchanged adapter_coverage report.
    """
    if index.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise ValueError('Research index contains dangling physical references')
    declarations = {}
    for row in index.execute('SELECT * FROM adapter_coverage'):
        key = (row['source_key'], row['projection_kind'], row['source_table'])
        if key in declarations:
            raise ValueError('ambiguous duplicate coverage declaration')
        declarations[key] = row['indexed_count']
    actual = {}
    for kind, table in [('container', 'research_container'), ('text', 'research_text'),
                        ('relation', 'research_relation'), ('candidate', 'research_candidate')]:
        relation_join = ' JOIN research_relation z ON z.id=r.relation_id' if kind == 'candidate' else ''
        source = 'z.source_key' if kind == 'candidate' else 'r.source_key'
        groups = index.execute(f'SELECT {source},r.source_table,COUNT(*) FROM {table} r{relation_join} '
                               f'GROUP BY {source},r.source_table')
        for source_key, source_table, count in groups:
            actual[source_key, kind, source_table] = count
        duplicates = index.execute(f'SELECT 1 FROM {table} r{relation_join} GROUP BY {source},'
            'r.source_table,r.pk_column,r.pk_value HAVING COUNT(*)>1 LIMIT 1').fetchone()
        if duplicates:
            raise ValueError('duplicate exact source occurrence in '+table)
    if any(key not in declarations for key in actual):
        raise ValueError('indexed rowset lacks coverage declaration')
    if any(actual.get(key, 0) != count for key, count in declarations.items()):
        raise ValueError('actual indexed rowset differs from coverage count')


def canon(value):
    def blob(v):
        if isinstance(v,bytes):return {'bytes_sha256':hashlib.sha256(v).hexdigest(),'size_bytes':len(v)}
        raise TypeError(type(v).__name__)
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=blob)


def fetch(src,table,column,value):
    if not table.replace('_','').isalnum():raise ValueError('oracle unsafe source table')
    if (src,table) not in KEYS:
        info=list(src.execute(f'PRAGMA table_info("{table}")'))
        KEYS[src,table]=[r[1] for r in sorted(info,key=lambda r:r[5]) if r[5]]
    keys=KEYS[src,table]
    names=json.loads(column) if column.startswith('[') else [column]
    values=json.loads(value) if column.startswith('[') else [value]
    if (names!=keys and not (len(names)==1 and names[0] in keys)) or len(names)!=len(values):raise ValueError('independent physical key scope mismatch')
    rows=src.execute(f'SELECT * FROM "{table}" WHERE '+ ' AND '.join('"'+n+'"=?' for n in names)+' LIMIT 2',values).fetchall()
    if len(rows)!=1:raise ValueError('index source occurrence absent or ambiguous across exact runs')
    return dict(rows[0])


def owner(src,table,record,reader=None):
    if table.endswith('_text_candidate_occurrence'):
        prefix=table.removesuffix('_text_candidate_occurrence');ot=prefix+'_text_owner_occurrence'
        oc=reader.columns(src,ot) if reader else {r[1] for r in src.execute('PRAGMA table_info('+ot+')')}
        if 'owner_id' in record:
            got=src.execute(f'SELECT * FROM {ot} WHERE owner_id=? AND ingest_run_id=?',(record['owner_id'],record['ingest_run_id'])).fetchone()
        else:
            rt=prefix+'_text_resolution_occurrence';rc=reader.columns(src,rt) if reader else {r[1] for r in src.execute('PRAGMA table_info('+rt+')')}
            pk='id' if 'id' in rc else 'occurrence_id'
            z=src.execute(f'SELECT * FROM {rt} WHERE {pk}=?',(record['resolution_occurrence_id'],)).fetchone()
            ownerpk='id' if 'id' in oc else 'occurrence_id'
            got=src.execute(f'SELECT * FROM {ot} WHERE {ownerpk}=?',(z['owner_occurrence_id'],)).fetchone()
        if got is None:raise ValueError('independent core owner join absent')
        got=dict(got)
        return got.get('row_occurrence_id',got.get('source_row_occurrence_id')),got.get('field_name',got.get('field_path')),record.get('locale')
    if 'slot_id' in record:
        st={'codex_text_candidate':'codex_text_slot','native71_text':'native71_slot','event71_text':'event71_slot','document_text':'document_slot','loading_tip_text':'loading_tip_slot','gcg_card_text':'gcg_card_slot','mail_text':'mail_slot','event_narrative_text':'event_narrative_slot','native_archive_text':'native_archive_slot'}.get(table,'text_slot')
        z=dict(src.execute(f'SELECT * FROM {st} WHERE id=?',(record['slot_id'],)).fetchone())
        return z.get('row_id',z.get('container_id',z.get('root_id'))),z.get('owner_field',z.get('field',z.get('source_label'))),z['locale']
    if table=='candidate':
        z=src.execute('SELECT row_id,field_name,locale FROM resolution WHERE owner_id=? AND locale=?',(record['owner_id'],record['locale'])).fetchone()
        return tuple(z)
    if table=='text_slot':
        z=src.execute('SELECT container_id,pointer FROM talk_node WHERE id=?',(record['node_id'],)).fetchone()
        return z[0],z[1],record['locale']
    if table=='textmap_candidate':
        z=src.execute('SELECT row_id,field_pointer FROM numeric_observation WHERE id=?',(record['observation_id'],)).fetchone()
        return z[0],z[1],record['locale']
    if table=='text_candidate' and 'row_ordinal' in record:
        return str(record['row_ordinal']),record['field'],record['locale']
    if table in ('readable_line','subtitle_record'):
        return record['file_id'], 'body' if table=='readable_line' else 'cue_text',None
    raise ValueError('independent owner rule not declared: '+table)


def verify_connection(index,manifest,bindings):
    index.row_factory=sqlite3.Row
    verify_source_declarations(index, manifest)
    provenance_path=Path(bindings['provenance_artifact']);provenance_seal=manifest['provenance_dependency']
    if provenance_path.stat().st_size!=provenance_seal['size_bytes'] or sha256_file(provenance_path)!=provenance_seal['sha256']:raise ValueError('independent snapshot-local provenance seal drift')
    provenance=sqlite3.connect(provenance_path.as_uri()+'?mode=ro&immutable=1',uri=True)
    source_files=dict(provenance.execute('SELECT relative_path,source_sha256 FROM source_file'));provenance.close()
    sources={};source_seals=[]
    for c in manifest['components']:
        key=c['source_key'];path=Path(bindings['artifacts'][key]);seal=c['artifact']
        if path.stat().st_size!=seal['size_bytes'] or sha256_file(path)!=seal['sha256'] or any(Path(str(path)+s).exists() for s in ('-wal','-shm','-journal')):
            raise ValueError('independent source artifact drift')
        src=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True);src.row_factory=sqlite3.Row;src.execute('PRAGMA temp_store=MEMORY');sources[key]=src
        source_seals.append({'source_key':key,'artifact':seal})
    nc=nt=nr=nk=0;terminal=[];family_sources={};reuse_rows=[];second_hops={'verified_indexed_targets':0,'verified_projection_reuse_targets':0,'not_index_addressable':0,'target_scope_unknown':0,'locator_not_declared_as_target_primary_native_id':0}
    try:
        verify_required_source_projections(index, manifest, sources)
        verify_projection_counts(index)
        reader=SourceRows(sources)
        for r,got in reader.rows(index,'SELECT * FROM research_container ORDER BY id'):
            src=sources[r['source_key']]
            path=got.get('source_path',got.get('source_file_path'));ptr=got.get('pointer',got.get('source_pointer'))
            if 'source_record_id' in got:
                p=src.execute('SELECT s.pointer,f.relative_path FROM source_record s JOIN source_file f ON f.id=s.source_file_id WHERE s.id=?',(got['source_record_id'],)).fetchone()
                path=path or p[1];ptr=ptr or p[0]
            if (path,ptr or '/')!=(r['source_path'],r['source_pointer']):raise ValueError('independent container coordinate mismatch')
            if source_files.get(path)!=r['source_file_sha256']:raise ValueError('container snapshot-local file checksum mismatch')
            for col,val in [('parser_build_id',r['evidence_parser']),('ingest_run_id',r['evidence_run']),('run_id',r['evidence_run'])]:
                if col in got and got[col]!=val:raise ValueError('independent physical evidence run/parser mismatch')
            nc+=1
            family_sources.setdefault(r['evidence_family'],set()).add(r['source_key'])
            if r['source_table']=='selected_occurrence' or (r['source_table']=='row_occurrence' and got.get('reuse_occurrence_id')):
                reuse_rows.append((got,r['source_key']))
            if nc%250000==0:print('oracle containers',nc,flush=True)
        for got,key in reuse_rows:
            prefix='reuse_' if 'reuse_occurrence_id' in got else 'target_'
            if got.get(prefix+'occurrence_id') is None:continue
            family={'npc':'npc_archive','dialog':'dialog_archive','reminder':'reminder_archive','quest_talk':'quest_talk_pilot'}.get(got[prefix+'family'],got[prefix+'family'])
            terms=['source_key IN ('+','.join('?' for _ in family_sources.get(family,()))+')'];params=sorted(family_sources.get(family,()))
            for col,field in [('evidence_family','family'),('evidence_contract','contract'),('evidence_parser','parser'),('evidence_run','run'),('occurrence_id','occurrence_id')]:
                terms.append(col+'=?');params.append(family if field=='family' else got[prefix+field])
            terms.extend(['source_path=?','source_pointer=?']);params.extend([got['source_path'],got['pointer']])
            targets=index.execute('SELECT * FROM research_container WHERE '+' AND '.join(terms)+' LIMIT 2',params).fetchall()
            if len(targets)!=1:raise ValueError('full projection reuse target missing/ambiguous: '+key)
            t=targets[0];physical=fetch(sources[t['source_key']],t['source_table'],t['pk_column'],t['pk_value'])
            if got['raw_sha256']!=physical.get('raw_sha256',physical.get('raw_record_sha256')):raise ValueError('full projection reuse target raw SHA mismatch')
            second_hops['verified_projection_reuse_targets']+=1
        reuse_rows.clear()
        for r,got in reader.rows(index,'''SELECT t.*,c.id AS __container_id,
                c.pk_value AS __container_pk,c.occurrence_id AS __container_occurrence,
                c.source_path AS __container_path FROM research_text t
                LEFT JOIN research_container c ON c.id=t.container_id ORDER BY t.id'''):
            src=sources[r['source_key']]
            raw=got.get('raw_text',got.get('raw_cue_text'))
            if raw!=r['raw_text'] or hashlib.sha256(raw.encode()).hexdigest()!=r['text_sha256']:raise ValueError('independent fulltext/SHA mismatch')
            parent,field,locale=owner(src,r['source_table'],got,reader)
            if r['__container_id'] is None:raise ValueError('independent text container missing')
            container={'pk_value':r['__container_pk'],'occurrence_id':r['__container_occurrence'],'source_path':r['__container_path']}
            expected_parent=container['pk_value'] if r['source_key'].startswith('7.1-narrative_') else container['occurrence_id']
            if str(parent)!=expected_parent or field!=r['owner_field'] or (locale is not None and locale!=r['locale']):raise ValueError('independent owner/container/locale mismatch')
            path=got.get('source_path',got.get('relative_path'));ptr=got.get('pointer',got.get('source_pointer'));pair=got.get('pair_ordinal')
            if path is None and 'source_file_id' in got:
                path=src.execute('SELECT relative_path FROM source_file WHERE id=?',(got['source_file_id'],)).fetchone()[0]
            if r['source_table']=='text_slot':
                loc=json.loads(got['locators_json'])
                if len(loc)!=1:raise ValueError('Talk candidates silently selected')
                path,ptr,pair=loc[0]['path'],loc[0]['pointer'],loc[0]['pair_ordinal']
            elif r['source_table']=='candidate':ptr='/'+got['source_key'];pair=got['physical_ordinal']
            elif r['source_table']=='text_candidate' and 'hash' in got:ptr='/'+str(got['hash'])
            elif r['source_table'] in ('readable_line','subtitle_record'):path=container['source_path']
            if path!=r['text_source_path'] or ptr!=r['text_source_pointer'] or (pair is not None and pair!=r['physical_pair_ordinal']):raise ValueError('independent text source/pair coordinates mismatch')
            if source_files.get(path)!=r['text_source_file_sha256']:raise ValueError('text snapshot-local file checksum mismatch')
            nt+=1
            if nt%500000==0:print('oracle texts',nt,flush=True)
        for r,got in reader.rows(index,'''SELECT r.*,c.id AS __container_id,
                c.pk_value AS __container_pk,c.occurrence_id AS __container_occurrence,
                COALESCE(k.n,0) AS __candidate_count FROM research_relation r
                LEFT JOIN research_container c ON c.id=r.container_id
                LEFT JOIN (SELECT relation_id,COUNT(*) n FROM research_candidate GROUP BY relation_id) k
                    ON k.relation_id=r.id ORDER BY r.id'''):
            if r['adapter_id'].startswith(('character_foundation:','quest_review_binding:','native_message:','event_native_text_17:','native_successor_physical:','native_high_value_physical:','codexquest_archive:','narrative_','talk_physical:')):
                if canon(got)!=r['raw_locator']:raise ValueError('complete original relation row mismatch')
            status=got.get('status',got.get('state','mapping_unresolved'))
            count=got.get('candidate_count',got.get('known_candidate_count'))
            if status!=r['status'] or (count is not None and count!=r['candidate_count']):raise ValueError('relation state/cardinality mismatch')
            indexed=r['__candidate_count']
            if indexed!=r['candidate_count']:raise ValueError('relation candidate set cardinality not complete')
            if r['__container_id'] is None:raise ValueError('independent relation container missing')
            container={'pk_value':r['__container_pk'],'occurrence_id':r['__container_occurrence']}
            fk=next((c for c in ('source_row_id','source_review_row_id','root_id','row_id','container_id','file_id','reuse_id') if c in got),None)
            if fk and str(got[fk])!=container['occurrence_id']:raise ValueError('independent relation parent binding mismatch')
            if 'row_ordinal' in got and str(got['row_ordinal'])!=container['pk_value']:raise ValueError('independent locator parent coordinate mismatch')
            nr+=1
            if nr%250000==0:print('oracle relations',nr,flush=True)
        for r,got in reader.rows(index,'SELECT k.*,z.source_key,z.adapter_id,z.pk_value relation_pk_value FROM research_candidate k JOIN research_relation z ON z.id=k.relation_id ORDER BY k.id'):
            fk=next((c for c in ('relation_id','locator_id','navigation_id','reference_id') if c in got),None)
            if fk and str(got[fk])!=r['relation_pk_value']:raise ValueError('independent candidate relation FK mismatch')
            if fk is None and 'item_ordinal' in got:
                if json.loads(r['relation_pk_value'])!=[got['row_ordinal'],got['field'],got['item_ordinal']]:raise ValueError('independent composite candidate relation FK mismatch')
            if r['raw_target'] and r['raw_target'].startswith('{'):
                if canon(got)!=r['raw_target']:raise ValueError('complete candidate row mismatch')
            for target,names in {'target_family':('target_family',),'target_contract':('target_contract',),'target_parser':('target_parser','target_parser_build_id'),'target_run':('target_run','target_run_id'),'target_occurrence_id':('target_occurrence_id','target_row_id','target_primary_id'),'target_source_path':('target_source_path',),'target_pointer':('target_pointer','target_source_pointer')}.items():
                for col in names:
                    if got.get(col) is not None and str(got[col])!=str(r[target]):raise ValueError('candidate target field drift: '+target)
            # Independently bind each addressable target, not merely its ID
            # spelling. Unknown namespaces remain visible, never resolved.
            aliases={'npc':'npc_archive','dialog':'dialog_archive','reminder':'reminder_archive','quest_talk':'quest_talk_pilot'}
            family=aliases.get(r['target_family'],r['target_family']);terms=[];params=[]
            for column,value in [('evidence_family',family),('evidence_contract',r['target_contract']),('evidence_parser',r['target_parser']),('evidence_run',r['target_run']),('occurrence_id',r['target_occurrence_id']),('source_path',r['target_source_path']),('source_pointer',r['target_pointer'])]:
                if value is not None:terms.append(column+'=?');params.append(value)
            addressable=bool(r['target_run'] and r['target_occurrence_id']) or bool(family and r['target_source_path'] and r['target_pointer'])
            # Coordinate lookup must constrain the source-leading path index.
            # This is only a proven physical source-key scope, not a native-ID
            # shortcut: all declared target fields above remain mandatory.
            if family and r['target_source_path'] and r['target_pointer']:
                keys=sorted(family_sources.get(family,()))
                if keys:terms.append('source_key IN ('+','.join('?' for _ in keys)+')');params.extend(keys)
                else:addressable=False
            lookup='research_container INDEXED BY research_container_path' if family and r['target_source_path'] and r['target_pointer'] and family_sources.get(family) else 'research_container'
            targets=index.execute('SELECT * FROM '+lookup+' WHERE '+' AND '.join(terms)+' LIMIT 2',params).fetchall() if terms and addressable else []
            if len(targets)==1:
                t=targets[0];targetsrc=sources[t['source_key']];physical=fetch(targetsrc,t['source_table'],t['pk_column'],t['pk_value'])
                if got.get('target_raw_sha256') is not None:
                    hashes={physical.get(x) for x in ('raw_sha256','raw_record_sha256','raw_record_checksum')}
                    if physical.get('source_record_id'):
                        record=targetsrc.execute('SELECT raw_sha256 FROM source_record WHERE id=?',(physical['source_record_id'],)).fetchone()
                        if record:hashes.add(record[0])
                    if got['target_raw_sha256'] not in hashes:raise ValueError('full second-hop target raw SHA mismatch')
                for name in ('target_native_id','target_native_value'):
                    # TPS compound locators are not a target primary native
                    # ID: the qualified target row explicitly leaves native_id
                    # NULL. Preserve their source-oracled candidate field, do
                    # not fabricate a primary-ID role from its column name.
                    if got.get(name) is not None:
                        if physical.get('native_id') is None:
                            second_hops['locator_not_declared_as_target_primary_native_id']+=1
                        elif str(got[name])!=str(physical['native_id']):raise ValueError('full second-hop native locator mismatch: '+canon({'adapter':r['adapter_id'],'candidate_key':r['pk_value'],'field':name,'observed':got[name],'target_native_id':physical['native_id'],'target_path':t['source_path'],'target_pointer':t['source_pointer']}))
                second_hops['verified_indexed_targets']+=1
            elif addressable:second_hops['not_index_addressable']+=1
            else:second_hops['target_scope_unknown']+=1
            nk+=1
            if nk%100000==0:print('oracle candidates',nk,flush=True)
        for a in index.execute('SELECT * FROM adapter_coverage ORDER BY source_key,source_table,projection_kind'):
            src=sources[a['source_key']];table=a['source_table']
            columns=[r[1] for r in src.execute('PRAGMA table_info('+table+')')]
            if a['projection_kind']=='text':
                field='raw_text' if 'raw_text' in columns else 'raw_cue_text'
                expected=src.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{field}" IS NOT NULL').fetchone()[0]
            else:expected=src.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            if expected!=a['denominator']:raise ValueError('independent source denominator mismatch: '+table)
            if a['status']=='complete' and a['indexed_count']!=expected:raise ValueError('extras/omissions in complete adapter')
            if a['projection_kind'] in ('relation','candidate'):
                # Independent digest of EVERY source row, including explicitly
                # unsupported scopes. Unsupported remains partial at runtime.
                ordered=sorted(columns);h=hashlib.sha256();n=0
                for row in src.execute(f'SELECT * FROM "{table}" ORDER BY '+','.join('"'+x+'"' for x in ordered)):
                    h.update(canon(dict(row)).encode()+b'\n');n+=1
                terminal.append({'source_key':a['source_key'],'table':table,'kind':a['projection_kind'],'source_rows':n,'rowset_sha256':h.hexdigest(),'research_rows':a['indexed_count'],'qualification':a['status']})
        return {'status':'PASS_COMPLETE_SOURCE_ROWSETS','containers':nc,'texts':nt,'relations':nr,'candidates':nk,'full_relation_candidate_scope_rowsets':terminal,'source_seals':source_seals,'second_hops':second_hops,'representative_checks_not_used_as_denominator_proof':True}
    finally:
        for src in sources.values():src.close()


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    for name in ('corpus','bindings','index','report'):ap.add_argument('--'+name,type=Path,required=True)
    a=ap.parse_args();before=sha256_file(a.index)
    db=sqlite3.connect(a.index.as_uri()+'?mode=ro&immutable=1',uri=True)
    v=verify_connection(db,json.loads(a.corpus.read_text()),json.loads(a.bindings.read_text()));db.close()
    if before!=sha256_file(a.index):raise ValueError('oracle changed index')
    v['index_sha256_unchanged']=before
    with a.report.open('x') as f:json.dump(v,f,ensure_ascii=False,sort_keys=True,indent=2)
    print({k:v[k] for k in ('status','containers','texts','relations','candidates')})
