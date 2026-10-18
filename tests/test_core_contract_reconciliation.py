"""Optimized reconciliation preserves complete rows, not just counts."""
import sqlite3
import pytest
from scripts.builders.research_dataset import apply_core_contracts


def fixture():
    db=sqlite3.connect(':memory:')
    for table in ('research_container','research_text'):
        db.execute(f'CREATE TABLE {table}(id INTEGER PRIMARY KEY,source_key,evidence_parser,evidence_run,evidence_contract,raw_text)')
        db.executemany(f'INSERT INTO {table} VALUES(?,?,?,?,?,?)',[
            (i,'core' if i%3 else 'other','parser-'+str(i%33),'run-'+str(i%33),'original','synthetic-'+str(i))
            for i in range(2000)])
    return db


def mappings():
    return [dict(parser_build_id='parser-'+str(i),ingest_run_id='run-'+str(i),provider_contract='contract-'+str(i)) for i in range(33)]


def test_two_scan_reconciliation_matches_original_full_rowsets():
    expected,actual=fixture(),fixture()
    original_schema=actual.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    for m in mappings():
        for table in ('research_container','research_text'):
            expected.execute(f'UPDATE {table} SET evidence_contract=? WHERE source_key=? AND evidence_parser=? AND evidence_run=?',
                (m['provider_contract'],'core',m['parser_build_id'],m['ingest_run_id']))
    apply_core_contracts(actual,'core',mappings())
    for table in ('research_container','research_text'):
        assert actual.execute(f'SELECT * FROM {table} ORDER BY id').fetchall()==expected.execute(f'SELECT * FROM {table} ORDER BY id').fetchall()
    assert actual.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()==original_schema
    assert not actual.execute('SELECT name FROM sqlite_temp_master').fetchall()
    expected.close();actual.close()


def test_duplicate_parser_run_binding_is_rejected():
    db=fixture()
    with pytest.raises(ValueError,match='conflicting'):
        apply_core_contracts(db,'core',mappings()+[{**mappings()[0],'provider_contract':'conflicting'}])
    assert not db.execute('SELECT name FROM sqlite_temp_master').fetchall()
    assert db.execute("SELECT COUNT(*) FROM research_text WHERE evidence_contract!='original'").fetchone()[0]==0
    db.close()
