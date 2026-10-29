"""Batching changes physical I/O, never key identity or rowset qualification."""
import json
import sqlite3

import pytest

from scripts.validators.research_dataset import SourceRows, fetch


def databases():
    source = sqlite3.connect(':memory:')
    source.row_factory = sqlite3.Row
    source.executescript('''CREATE TABLE item(run TEXT,id TEXT,raw_text TEXT,PRIMARY KEY(run,id));
        INSERT INTO item VALUES('a','1','synthetic a'),('b','1','synthetic b'),('b','2','synthetic c');''')
    index = sqlite3.connect(':memory:')
    index.row_factory = sqlite3.Row
    index.execute('CREATE TABLE request(source_key,source_table,pk_column,pk_value)')
    return source, index


@pytest.mark.parametrize('batch_size', [1, 2, 256])
def test_batch_matches_individual_reads_including_repeated_requests(batch_size):
    source, index = databases()
    try:
        values = [['b','2'], ['a','1'], ['b','1'], ['a','1']]
        index.executemany('INSERT INTO request VALUES(?,?,?,?)',
                          [('source','item','["run","id"]',json.dumps(v)) for v in values])
        rows = list(index.execute('SELECT * FROM request'))
        expected = [fetch(source,r['source_table'],r['pk_column'],r['pk_value']) for r in rows]
        actual = [physical for _,physical in SourceRows({'source':source},batch_size).rows(index,'SELECT * FROM request')]
        assert actual == expected
        assert len(actual) == 4  # repeated physical occurrences are not deduplicated
    finally:
        source.close(); index.close()


@pytest.mark.parametrize('column,value', [('id','1'), ('id','absent'),
    ('["id","run"]','["1","a"]'), ('["run","id"]','["a"]'), ('raw_text','synthetic a')])
def test_batch_rejects_ambiguous_missing_and_wrong_key_scopes(column,value):
    source,index = databases()
    try:
        index.execute('INSERT INTO request VALUES(?,?,?,?)',('source','item',column,value))
        with pytest.raises(ValueError):
            list(SourceRows({'source':source}).rows(index,'SELECT * FROM request'))
    finally:
        source.close(); index.close()


def test_unique_partial_key_retains_original_contract():
    source,index = databases()
    try:
        index.execute('INSERT INTO request VALUES(?,?,?,?)',('source','item','id','2'))
        assert list(SourceRows({'source':source}).rows(index,'SELECT * FROM request'))[0][1]['run']=='b'
    finally:
        source.close(); index.close()


def test_same_key_in_different_artifacts_is_not_shared():
    source,index=databases()
    other=sqlite3.connect(':memory:'); other.row_factory=sqlite3.Row
    try:
        other.executescript("CREATE TABLE item(run TEXT,id TEXT,raw_text TEXT,PRIMARY KEY(run,id)); INSERT INTO item VALUES('a','1','other artifact');")
        index.executemany('INSERT INTO request VALUES(?,?,?,?)',
                          [(s,'item','["run","id"]','["a","1"]') for s in ('source','other')])
        rows=list(SourceRows({'source':source,'other':other}).rows(index,'SELECT * FROM request'))
        assert [r['raw_text'] for _,r in rows]==['synthetic a','other artifact']
    finally:
        source.close(); other.close(); index.close()


def test_batch_reduces_queries_without_dropping_rows():
    source,index=databases()
    try:
        index.executemany('INSERT INTO request VALUES(?,?,?,?)',
                          [('source','item','["run","id"]','["a","1"]')]*600)
        queries=[]; source.set_trace_callback(queries.append)
        assert len(list(SourceRows({'source':source}).rows(index,'SELECT * FROM request')))==600
        assert sum(q.startswith('WITH q(') for q in queries)==3
        assert sum(q.startswith('PRAGMA table_info') for q in queries)==1
    finally:
        source.close(); index.close()


@pytest.mark.parametrize('size', [0, -1, 257])
def test_batch_size_is_bounded(size):
    with pytest.raises(ValueError,match='batch size'):
        SourceRows({},size)


def test_owner_lookups_are_bounded_and_keep_connection_and_parameters():
    source,index=databases()
    other=sqlite3.connect(':memory:');other.row_factory=sqlite3.Row
    try:
        other.executescript("CREATE TABLE item(run,id,raw_text); INSERT INTO item VALUES('a','1','other artifact');")
        reader=SourceRows({'source':source,'other':other})
        query='SELECT * FROM item WHERE run=? AND id=?'
        traces=[];source.set_trace_callback(traces.append)
        for _ in range(3):
            assert reader.one(source,query,('a','1'))['raw_text']=='synthetic a'
        assert len(traces)==1
        assert reader.one(source,query,('b','1'))['raw_text']=='synthetic b'
        assert reader.one(other,query,('a','1'))['raw_text']=='other artifact'
        for ordinal in range(4100):
            assert reader.one(source,query,('missing',str(ordinal))) is None
        assert len(reader.lookups)==4096
        assert (source,query,('a','1')) not in reader.lookups
    finally:
        source.close();other.close();index.close()
