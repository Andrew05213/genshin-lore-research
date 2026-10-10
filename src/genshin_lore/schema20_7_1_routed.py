"""Candidate-only exact-run qualification of the existing three routed verbs.

No top-level tools or current runtime changes. Family table/contract bindings
come from the frozen 33 mappings, not inferred evidence semantics.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from ._readonly_database import open_readonly_database
from .family_evidence import FamilyEvidenceResult
from .research_index import sha256_file
from .schema20_7_1_guards import verify as verify_schema

ROOT=Path(__file__).resolve().parents[2]
SNAPSHOT='anime-game-data:7.1:9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99'
LEGACY={'avatar_codex_archive':'avatar_codex','quest_codex_archive':'quest_codex','world_archive':'world_archive'}


def mappings():
    return [m for path in ('manifests/m3-7.1-provider/accepted-exact-mappings-v1.json','manifests/special-core-7.1/accepted-exact-mappings-v1.json') for m in json.loads((ROOT/path).read_text())['mappings']]


class CandidateCore:
    def __init__(self,path,build_report,oracle_report):
        self.path=path.resolve();seal=build_report['artifact']
        if oracle_report.get('status')!='PASS' or build_report.get('qualified_provider_count')!=33:
            raise ValueError('all 33 core source mappings must be independently qualified')
        if self.path.stat().st_size!=seal['size_bytes'] or sha256_file(self.path)!=seal['sha256']:
            raise ValueError('candidate core seal drift')
        if any(Path(str(self.path)+s).exists() for s in ('-wal','-shm','-journal')):raise ValueError('candidate core sidecar')
        self.db=open_readonly_database(self.path);self.conn=self.db.conn
        verify_schema(self.conn)
        self.items={(m['family'],m['provider_contract'],m['ingest_run_id']):m for m in mappings()}
        if len(self.items)!=33:raise ValueError('core provider denominator drift')
        for m in self.items.values():
            run=self.conn.execute('SELECT snapshot_id,parser_build_id,status FROM ingest_run WHERE id=?',(m['ingest_run_id'],)).fetchone()
            if tuple(run or ())!=(SNAPSHOT,m['parser_build_id'],'complete'):raise ValueError('candidate routed source run mismatch')
        self.fingerprint=self._fingerprint()

    def _fingerprint(self):
        st=self.path.stat();return st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns

    def close(self):self.db.close()

    def _scope(self,family,contract,run):
        if self._fingerprint()!=self.fingerprint or any(Path(str(self.path)+s).exists() for s in ('-wal','-shm','-journal')):raise ValueError('candidate artifact changed during routed call')
        m=self.items.get((family,contract,run))
        if m is None:raise LookupError('exact family/contract/run not eligible; no sibling fallback')
        prefix=LEGACY.get(family,family);table=prefix+'_row_occurrence'
        columns={r[1] for r in self.conn.execute('PRAGMA table_info('+table+')')}
        pk='occurrence_id' if 'occurrence_id' in columns else 'id'
        return m,prefix,table,pk

    def capability(self):
        items=[]
        for (f,c,r),m in self.items.items():
            _,prefix,_,_=self._scope(f,c,r)
            columns={x[1] for x in self.conn.execute('PRAGMA table_info('+prefix+'_text_candidate_occurrence)')}
            operations=['list_family_evidence','get_family_evidence']
            if 'raw_text' in columns:operations.append('search_family_text')
            items.append({'family':f,'family_contract_version':c,'eligible_runs':[{'ingest_run_id':r,'parser_build_id':m['parser_build_id'],'snapshot_id':SNAPSHOT}], 'supported_operations':operations,'product_accepted':False,'corpus_complete':False,'search_semantics':'literal physical text candidates only; missing declared text projection is unsupported, not raw JSON text'})
        return items

    def list_evidence(self,*,family,family_contract_version,ingest_run_id,limit=50,cursor=None):
        _,_,table,pk=self._scope(family,family_contract_version,ingest_run_id)
        if not 1<=limit<=100:raise ValueError('bounded limit required')
        scope=[family,family_contract_version,ingest_run_id,limit]
        after=''
        if cursor:
            token=json.loads(base64.urlsafe_b64decode(cursor))
            if token['scope']!=scope:raise ValueError('routed cursor scope mismatch')
            after=token['after']
        rows=self.conn.execute(f'SELECT * FROM "{table}" WHERE ingest_run_id=? AND "{pk}">? ORDER BY "{pk}" LIMIT ?',(ingest_run_id,after,limit+1)).fetchall()
        items=[dict(r) for r in rows[:limit]]
        next_cursor=base64.urlsafe_b64encode(json.dumps({'scope':scope,'after':items[-1][pk]},sort_keys=True).encode()).decode() if len(rows)>limit else None
        return FamilyEvidenceResult('ok',family,ingest_run_id,family_contract_version,'source-qualified-candidate-only',False,{'items':items,'next_cursor':next_cursor,'ordering_basis':'deterministic evidence key; not semantic order'})

    def get_evidence(self,*,family,family_contract_version,ingest_run_id,occurrence_id):
        _,prefix,table,pk=self._scope(family,family_contract_version,ingest_run_id)
        row=self.conn.execute(f'SELECT * FROM "{table}" WHERE "{pk}"=? AND ingest_run_id=?',(occurrence_id,ingest_run_id)).fetchone()
        if row is None:return FamilyEvidenceResult('not_found',family,ingest_run_id,family_contract_version,'source-qualified-candidate-only',False,{'status':'NotFound'})
        row=dict(row)
        if 'source_record_id' in row:
            raw=self.conn.execute('SELECT sr.*,sf.relative_path,sf.sha256 source_file_sha256 FROM source_record sr JOIN source_file sf ON sf.id=sr.source_file_id WHERE sr.id=?',(row['source_record_id'],)).fetchone()
            if raw is None:raise ValueError('candidate source record disappeared')
            raw=dict(raw)
        else:raw=None
        children={}
        # FK names here are the concrete Schema20 storage contract. They do
        # not assign semantic roles to opaque/raw locator fields.
        for suffix in ('raw_field_occurrence','opaque_occurrence','text_owner_occurrence','relationship_occurrence','asset_reference_occurrence','asset_occurrence'):
            child_table=prefix+'_'+suffix
            cols={r[1] for r in self.conn.execute('PRAGMA table_info('+child_table+')')}
            fk='row_occurrence_id' if 'row_occurrence_id' in cols else 'source_row_occurrence_id' if 'source_row_occurrence_id' in cols else None
            if fk:children[suffix]=[dict(r) for r in self.conn.execute(f'SELECT * FROM "{child_table}" WHERE "{fk}"=? AND ingest_run_id=?',(occurrence_id,ingest_run_id))]
        owners=children.get('text_owner_occurrence',[])
        for owner in owners:
            oid=owner.get('owner_id',owner.get('occurrence_id',owner.get('id')))
            resolution=prefix+'_text_resolution_occurrence';rc={r[1] for r in self.conn.execute('PRAGMA table_info('+resolution+')')}
            owner_fk='owner_id' if 'owner_id' in rc else 'owner_occurrence_id'
            owner['resolutions']=[dict(r) for r in self.conn.execute(f'SELECT * FROM "{resolution}" WHERE "{owner_fk}"=? AND ingest_run_id=?',(oid,ingest_run_id))]
            candidate=prefix+'_text_candidate_occurrence';cc={r[1] for r in self.conn.execute('PRAGMA table_info('+candidate+')')}
            if 'owner_id' in cc:
                owner['candidates']=[dict(r) for r in self.conn.execute(f'SELECT * FROM "{candidate}" WHERE owner_id=? AND ingest_run_id=?',(oid,ingest_run_id))]
            elif cc:
                resolution_pk='occurrence_id' if 'occurrence_id' in rc else 'id'
                owner['candidates']=[dict(r) for r in self.conn.execute(f'SELECT c.* FROM "{candidate}" c JOIN "{resolution}" z ON z."{resolution_pk}"=c.resolution_occurrence_id WHERE z."{owner_fk}"=? AND c.ingest_run_id=?',(oid,ingest_run_id))]
        return FamilyEvidenceResult('ok',family,ingest_run_id,family_contract_version,'source-qualified-candidate-only',False,{'item':row,'source_record':raw,'text_owners':owners,'physical_children':children,'physical_container_only':True,'semantic_identity_proven':False})

    def search_text(self,*,family,family_contract_version,ingest_run_id,query,limit=50):
        m,prefix,table,pk=self._scope(family,family_contract_version,ingest_run_id)
        if not isinstance(query,str) or not query.strip() or not 1<=limit<=100:raise ValueError('literal query and bounded limit required')
        candidate=prefix+'_text_candidate_occurrence'
        cc={r[1] for r in self.conn.execute('PRAGMA table_info('+candidate+')')}
        if 'raw_text' in cc:
            rows=[dict(r) for r in self.conn.execute(f'SELECT * FROM "{candidate}" WHERE ingest_run_id=? AND instr(raw_text,?)>0 LIMIT ?',(ingest_run_id,query,limit))]
            semantics='physical TextMap candidates; no candidate selection'
        else:
            raise ValueError('search_family_text unsupported for this declared contract')
        return FamilyEvidenceResult('ok',family,ingest_run_id,family_contract_version,'source-qualified-candidate-only',False,{'items':rows,'search_semantics':semantics,'bounded':True})


class CandidateRoutedProvider:
    """Bind one declared contract to the unchanged FamilyEvidenceRouter.

Only qualified filters/pagination are advertised; this is not a declaration
that every historical provider-specific optional payload is promoted.
"""
    def __init__(self,core,capability):
        self.core=core;self.value=capability
        self.family=capability['family'];self.family_contract_version=capability['family_contract_version']

    def capability(self):
        return {**self.value,'supported_filters':{'list_family_evidence':[],'get_family_evidence':[],'search_family_text':[]}}

    def _wrong_scope(self,run,contract):
        if contract!=self.family_contract_version or run!=self.value['eligible_runs'][0]['ingest_run_id']:
            return FamilyEvidenceResult('not_found',self.family,run,contract,'unavailable',False,{'status':'NotFound','reason':'exact_run_or_contract_mismatch'})

    def list_evidence(self,*,ingest_run_id,family_contract_version,filters=None,cursor=None,limit=50):
        error=self._wrong_scope(ingest_run_id,family_contract_version)
        if error:return error
        if filters:raise ValueError('candidate filters not qualified')
        return self.core.list_evidence(family=self.family,family_contract_version=family_contract_version,ingest_run_id=ingest_run_id,cursor=cursor,limit=limit)

    def get_evidence(self,*,ingest_run_id,family_contract_version,occurrence_id):
        error=self._wrong_scope(ingest_run_id,family_contract_version)
        if error:return error
        return self.core.get_evidence(family=self.family,family_contract_version=family_contract_version,ingest_run_id=ingest_run_id,occurrence_id=occurrence_id)

    def search_text(self,*,ingest_run_id,family_contract_version,query,filters=None,cursor=None,limit=50):
        error=self._wrong_scope(ingest_run_id,family_contract_version)
        if error:return error
        if filters or cursor:raise ValueError('candidate search filter/cursor not qualified')
        return self.core.search_text(family=self.family,family_contract_version=family_contract_version,ingest_run_id=ingest_run_id,query=query,limit=limit)
