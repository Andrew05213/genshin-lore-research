"""A declared unsupported/zero count cannot erase supported source relations."""
from pathlib import Path
import sqlite3

import pytest

from genshin_lore import research_index
from scripts.validators.research_dataset import verify_connection, verify_formal_relation_projections

ROOT = Path(__file__).resolve().parents[1]


def document_fixture(tmp_path, *, empty=False, with_text=True):
    source = tmp_path/'document.sqlite'
    with sqlite3.connect(source) as db:
        db.executescript((ROOT/'schemas/document-v1.sql').read_text())
        db.execute("INSERT INTO document_row VALUES('row','run','synthetic',0,'1','synthetic.json','/0','{}')")
        if with_text:
            db.execute("INSERT INTO document_slot VALUES('slot','row','body','CHS','1','resolved')")
            db.execute("INSERT INTO document_text VALUES('text','slot','synthetic.json',0,'/0/body','合成正文')")
        if not empty:
            db.execute("INSERT INTO document_relation VALUES('relation','row','/0/link','physical_path','synthetic.json','/0/link','file','unproven','resolved',1,1)")
            db.execute("INSERT INTO document_candidate VALUES('candidate','relation','Readable/CHS/synthetic.txt','/','{}')")
    provenance = tmp_path/'provenance.sqlite'
    with sqlite3.connect(provenance) as db:
        db.execute('CREATE TABLE source_file(relative_path,source_sha256)')
        db.execute("INSERT INTO source_file VALUES('synthetic.json','synthetic-source-sha')")
    def seal(path):
        return {'size_bytes':path.stat().st_size,'sha256':research_index.sha256_file(path)}
    component = {'source_key':'document','adapter':'formal_compatible','family':'document_archive',
                 'contract':'synthetic','parser':'parser','run':'run','artifact':seal(source)}
    out = sqlite3.connect(':memory:')
    out.row_factory = sqlite3.Row
    out.executescript(research_index.SCHEMA)
    out.execute('INSERT INTO source_artifact VALUES(?,?,?,?,?,?,?,?)',
        ('document','document_archive','synthetic','parser','run',str(source),
         component['artifact']['size_bytes'],component['artifact']['sha256']))
    src = sqlite3.connect(source)
    src.row_factory = sqlite3.Row
    try:
        _, _, mapping = research_index._component(src,out,'document',component,
            {'synthetic.json':('synthetic-source-sha',None)},0,0)
        research_index._relations(src,out,'document',component,mapping,0,0)
    finally:
        src.close()
    manifest = {'components':[component],'provenance_dependency':seal(provenance)}
    bindings = {'artifacts':{'document':str(source)},'provenance_artifact':str(provenance)}
    return out, manifest, bindings


@pytest.mark.parametrize('empty', [False, True])
def test_full_document_rowsets_pass_independent_oracle(tmp_path, empty):
    index, manifest, bindings = document_fixture(tmp_path, empty=empty)
    try:
        result = verify_connection(index,manifest,bindings)
        assert result['status']=='PASS_COMPLETE_SOURCE_ROWSETS'
        assert result['relations']==result['candidates']==int(not empty)
    finally:
        index.close()


def test_removing_all_relations_and_downgrading_coverage_is_rejected(tmp_path):
    index, manifest, bindings = document_fixture(tmp_path)
    try:
        index.execute('DELETE FROM research_candidate')
        index.execute('DELETE FROM research_relation')
        index.execute("UPDATE adapter_coverage SET indexed_count=0,status='unsupported' WHERE projection_kind IN ('relation','candidate')")
        with pytest.raises(ValueError,match='formal relation projection'):
            verify_connection(index,manifest,bindings)
    finally:
        index.close()


def test_removing_candidates_and_syncing_relation_counts_is_rejected(tmp_path):
    index, manifest, bindings = document_fixture(tmp_path)
    try:
        index.execute('DELETE FROM research_candidate')
        index.execute('UPDATE research_relation SET candidate_count=0')
        index.execute("UPDATE adapter_coverage SET indexed_count=0,status='unsupported' WHERE projection_kind='candidate'")
        with pytest.raises(ValueError,match='formal relation projection'):
            verify_connection(index,manifest,bindings)
    finally:
        index.close()


def test_changing_supported_scope_status_without_removing_rows_is_rejected(tmp_path):
    index, manifest, bindings = document_fixture(tmp_path)
    try:
        index.execute("UPDATE adapter_coverage SET status='unsupported' WHERE projection_kind IN ('relation','candidate')")
        with pytest.raises(ValueError,match='formal relation projection'):
            verify_connection(index,manifest,bindings)
    finally:
        index.close()


def test_changed_parent_identity_cannot_shrink_the_source_scope(tmp_path):
    index, manifest, bindings = document_fixture(tmp_path, with_text=False)
    try:
        index.execute("UPDATE research_container SET occurrence_id='not-the-source-row'")
        index.execute('DELETE FROM research_candidate')
        index.execute('DELETE FROM research_relation')
        index.execute("UPDATE adapter_coverage SET indexed_count=0,status='unsupported' WHERE projection_kind IN ('relation','candidate')")
        with pytest.raises(ValueError,match='formal relation projection'):
            verify_connection(index,manifest,bindings)
    finally:
        index.close()


@pytest.mark.parametrize('indexed_parent', [False, True])
def test_source_parents_outside_index_namespace_remain_legal_partial(indexed_parent):
    source=sqlite3.connect(':memory:')
    index=sqlite3.connect(':memory:')
    try:
        source.executescript('''
            CREATE TABLE document_relation(id TEXT PRIMARY KEY,row_id);
            CREATE TABLE document_candidate(id TEXT PRIMARY KEY,relation_id);
            INSERT INTO document_relation VALUES('r1','present'),('r2','outside');
            INSERT INTO document_candidate VALUES('c1','r1'),('c2','r2');
        ''')
        index.executescript('''
            CREATE TABLE research_container(source_key,occurrence_id,pk_column,pk_value);
            CREATE TABLE research_relation(id INTEGER PRIMARY KEY,source_key,source_table);
            CREATE TABLE research_candidate(relation_id,source_table);
        ''')
        if indexed_parent:
            index.execute("INSERT INTO research_container VALUES('document','present','id','present')")
            index.execute("INSERT INTO research_relation VALUES(1,'document','document_relation')")
            index.execute("INSERT INTO research_candidate VALUES(1,'document_candidate')")
        declarations={(kind,table):{'denominator':2,'indexed_count':int(indexed_parent),'status':'unsupported'}
            for kind,table in [('relation','document_relation'),('candidate','document_candidate')]}
        verify_formal_relation_projections(index,source,'document','document_archive',declarations)
    finally:
        source.close()
        index.close()
