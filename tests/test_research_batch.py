"""Streaming Research writes preserve complete physical rows and failures."""
import hashlib
import sqlite3

import pytest

from genshin_lore import research_index as index
from genshin_lore.research71_candidate_adapters import adapt
from genshin_lore.research71_candidate_relations import project
from scripts.builders.research_dataset import project_component


class CountedConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.batches = []

    def executemany(self, sql, rows):
        count = 0
        def counted():
            nonlocal count
            for row in rows:
                count += 1
                yield row
        try:
            return super().executemany(sql, counted())
        finally:
            self.batches.append((sql,count))


def output():
    db = sqlite3.connect(':memory:', factory=CountedConnection)
    db.row_factory = sqlite3.Row
    db.executescript(index.SCHEMA)
    db.execute('PRAGMA foreign_keys=ON')
    db.execute("INSERT INTO source_artifact VALUES('fixture','synthetic','c','p','r','fixture',0,'sha')")
    return db


def container_values(number, *, occurrence=None, run='r', source_table='parent'):
    return index._container_values(number,'fixture','synthetic',
        ('synthetic','c','p',run),occurrence or str(number),'physical',
        source_table,number,'synthetic.json','/'+str(number),'sha',None)


def text_values(number, parent, *, pk=None, raw='相同的合成正文'):
    return index._text_values(number,parent,'fixture','synthetic',
        ('synthetic','c','p','r'),str(parent),'candidate',number,'candidate',
        pk or number,'CHS','body',number,'/body','synthetic.json',
        '/'+str(number),'sha',None,number,raw)


def test_streamed_same_text_keeps_all_identities_and_order():
    db = output()
    db.executemany(index._CONTAINER_INSERT,iter([
        container_values(1,occurrence='same',run='run-a',source_table='a'),
        container_values(2,occurrence='same',run='run-b',source_table='b')]))
    db.executemany(index._TEXT_INSERT,(text_values(n,1 if n%2 else 2) for n in range(1,8)))
    rows = list(db.execute('SELECT id,container_id,raw_text,physical_pair_ordinal FROM research_text ORDER BY id'))
    assert [r['id'] for r in rows] == list(range(1,8))
    assert [r['container_id'] for r in rows] == [1,2,1,2,1,2,1]
    assert len({r['raw_text'] for r in rows}) == 1
    assert [r['physical_pair_ordinal'] for r in rows] == list(range(1,8))
    assert list(map(tuple,db.execute('SELECT evidence_run,occurrence_id FROM research_container ORDER BY id'))) == [
        ('run-a','same'),('run-b','same')]
    assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    db.close()


@pytest.mark.parametrize('kind',['container','text'])
def test_streamed_duplicate_key_still_fails_without_consuming_later_rows(kind):
    db = output()
    if kind == 'text':
        db.execute(index._CONTAINER_INSERT,container_values(1))
    consumed=[]
    def rows():
        for number in range(1,4):
            consumed.append(number)
            yield (container_values(number,occurrence='same') if kind == 'container'
                   else text_values(number,1,pk='same-physical-row'))
    with pytest.raises(sqlite3.IntegrityError,match='UNIQUE'):
        db.executemany(index._CONTAINER_INSERT if kind == 'container' else index._TEXT_INSERT,rows())
    assert consumed == [1,2]
    db.close()


def test_streamed_text_with_missing_parent_still_fails_foreign_key():
    db = output()
    with pytest.raises(sqlite3.IntegrityError,match='FOREIGN KEY'):
        db.executemany(index._TEXT_INSERT,iter([text_values(1,99)]))
    db.close()


@pytest.mark.parametrize('raw',['','大'*2_000_000],ids=['empty','large'])
def test_single_physical_text_is_not_truncated_or_split_by_streaming(raw):
    db = output()
    db.execute(index._CONTAINER_INSERT,container_values(1))
    db.executemany(index._TEXT_INSERT,(text_values(1,1,raw=raw) for _ in range(1)))
    row=db.execute('SELECT raw_text,text_sha256 FROM research_text').fetchone()
    assert row['raw_text'] == raw
    assert row['text_sha256'] == hashlib.sha256(raw.encode()).hexdigest()
    db.close()


def test_sqlite_consumes_previous_row_before_requesting_next_row():
    db = output()
    db.execute(index._CONTAINER_INSERT,container_values(1))
    def rows():
        for number in range(1,20):
            # Materializing the generator before executemany would fail this:
            # each preceding source row must already be inserted.
            assert db.execute('SELECT COUNT(*) FROM research_text').fetchone()[0] == number-1
            yield text_values(number,1)
    db.executemany(index._TEXT_INSERT,rows())
    assert db.execute('SELECT COUNT(*) FROM research_text').fetchone()[0] == 19
    db.close()


@pytest.mark.parametrize('limit',[1,2,512])
def test_parent_reads_are_bounded_preserve_repetition_and_source_order(limit):
    db = output()
    db.executemany(index._CONTAINER_INSERT,iter([container_values(1),container_values(2)]))
    consumed=0
    def source():
        nonlocal consumed
        for number in range(1200):
            consumed += 1
            yield 1+number%2
    statements=[]
    db.set_trace_callback(statements.append)
    for position,(row,parent) in enumerate(index._with_containers(db,source(),lambda n:n,row_limit=limit),1):
        assert row == parent['id'] == 1+(position-1)%2
        assert consumed-position < limit
    assert consumed == 1200
    assert len([s for s in statements if s.startswith('SELECT * FROM research_container')]) == (1200+limit-1)//limit
    db.close()


def test_missing_output_parent_is_not_silently_omitted_by_batched_read():
    db = output()
    db.execute(index._CONTAINER_INSERT,container_values(1))
    with pytest.raises(ValueError,match='parent container missing: 99'):
        list(index._with_containers(db,[1,99],lambda n:n))
    db.close()


def native_source(*, empty=False):
    src = sqlite3.connect(':memory:')
    src.row_factory = sqlite3.Row
    src.executescript('''
        CREATE TABLE native71_row(id TEXT PRIMARY KEY,source_path,pointer);
        CREATE TABLE native71_slot(id TEXT PRIMARY KEY,row_id,owner_field,locale);
        CREATE TABLE native71_text(id TEXT PRIMARY KEY,slot_id,source_path,pointer,pair_ordinal,raw_text);
        CREATE TABLE native71_relation(id TEXT PRIMARY KEY,row_id,pointer,status,search_complete,candidate_count);
        CREATE TABLE native71_candidate(id TEXT PRIMARY KEY,relation_id,target_occurrence_id);
    ''')
    if not empty:
        src.executemany('INSERT INTO native71_row VALUES(?,?,?)',[('a','synthetic.json','/a'),('b','synthetic.json','/b')])
        src.executemany('INSERT INTO native71_slot VALUES(?,?,?,?)',[('sa','a','body','CHS'),('sb','b','body','CHS')])
        src.executemany('INSERT INTO native71_text VALUES(?,?,?,?,?,?)',[
            ('ta','sa','synthetic.json','/text/1',1,'同文'),
            ('tb','sb','synthetic.json','/text/2',2,'同文')])
        src.executemany('INSERT INTO native71_relation VALUES(?,?,?,?,?,?)',[
            ('ra','a','/a/ref','ambiguous',1,2),('rb','b','/b/ref','unresolved',0,0)])
        src.executemany('INSERT INTO native71_candidate VALUES(?,?,?)',[('ka','ra','same'),('kb','ra','same')])
    return src


COMPONENT={'source_key':'fixture','adapter':'native_message','family':'synthetic',
           'contract':'c','parser':'p','run':'r'}
PROVENANCE={'synthetic.json':('sha',None)}


def test_actual_projection_reads_parents_once_for_multiple_physical_texts():
    src,db=native_source(),output()
    statements=[]
    db.set_trace_callback(statements.append)
    _,_,mapping=adapt(src,db,'fixture',COMPONENT,PROVENANCE,0,0)
    assert mapping == {'a':1,'b':2}
    assert db.execute('SELECT COUNT(*) FROM research_text').fetchone()[0] == 2
    assert len([s for s in statements if s.upper().startswith('SELECT * FROM RESEARCH_CONTAINER')]) == 1
    assert [(sql,count) for sql,count in db.batches if sql in (index._CONTAINER_INSERT,index._TEXT_INSERT)] == [
        (index._CONTAINER_INSERT,2),(index._TEXT_INSERT,2)]
    src.close();db.close()


@pytest.mark.parametrize('empty',[False,True])
def test_component_streaming_matches_immediate_expected_rows_including_empty_and_ambiguous(empty):
    src=native_source(empty=empty)
    direct,actual=output(),output()
    mapping={}
    if not empty:
        for number,parent in enumerate(('a','b'),1):
            index._container(direct,number,'fixture','native_message:native71_row',('synthetic','c','p','r'),
                parent,'native71_row','native71_row',parent,'synthetic.json','/'+parent,'sha',None,
                None,'physical_source_ordinal','id')
            mapping[parent]=number
        for number,(parent,child) in enumerate((('a','ta'),('b','tb')),1):
            index._text(direct,number,number,'fixture','native_message:native71_text',('synthetic','c','p','r'),
                parent,'physical_text_candidate',child,'native71_text',child,'CHS','body',number,
                'body','synthetic.json','/text/'+str(number),'sha',None,number,'同文')
    count=0 if empty else 2
    direct.execute('INSERT INTO adapter_coverage VALUES(?,?,?,?,?,?,?,?)',('fixture','native_message:native71_row',
        'container','native71_row',count,count,'complete','complete declared physical parent namespace'))
    direct.execute('INSERT INTO adapter_coverage VALUES(?,?,?,?,?,?,?,?)',('fixture','native_message:native71_text',
        'text','native71_text',count,count,'complete','all physical text candidates; role proof is reread, not inferred'))
    rs,ks=project(src,direct,'fixture',COMPONENT,mapping,0,0)
    assert project_component(src,actual,COMPONENT,PROVENANCE,(0,0,0,0),corpus_path=None) == (count,count,rs,ks)
    for table in ('research_container','research_text','research_relation','research_candidate','adapter_coverage'):
        assert list(map(tuple,direct.execute('SELECT * FROM '+table+' ORDER BY 1'))) == list(
            map(tuple,actual.execute('SELECT * FROM '+table+' ORDER BY 1')))
    assert direct.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall() == actual.execute(
        'SELECT sql FROM sqlite_master ORDER BY name').fetchall()
    if not empty:
        assert list(map(tuple,actual.execute('SELECT status,candidate_count,search_complete FROM research_relation ORDER BY id'))) == [
            ('ambiguous',2,1),('unresolved',0,0)]
        assert actual.execute('SELECT COUNT(*) FROM research_candidate').fetchone()[0] == 2
    src.close();direct.close();actual.close()


@pytest.mark.parametrize('mutation,error',[('missing_text_parent',KeyError),('candidate_count_drift',ValueError)])
def test_component_rejects_missing_parent_or_candidate_scope_drift(mutation,error):
    src,db=native_source(),output()
    if mutation == 'missing_text_parent':
        src.execute("UPDATE native71_slot SET row_id='absent' WHERE id='sa'")
    else:
        src.execute("DELETE FROM native71_candidate WHERE id='kb'")
    with pytest.raises(error):
        project_component(src,db,COMPONENT,PROVENANCE,(0,0,0,0),corpus_path=None)
    src.close();db.close()


@pytest.mark.parametrize('limit',[0,513])
def test_invalid_parent_limits_rejected(limit):
    db=output()
    with pytest.raises(ValueError,match='parent batch limit'):
        list(index._with_containers(db,[],lambda n:n,row_limit=limit))
    db.close()


def test_composite_physical_keys_remain_distinct_with_equal_id_and_text():
    src,db=native_source(),output()
    src.execute('ALTER TABLE native71_text RENAME TO original_text')
    src.execute('CREATE TABLE native71_text(id,slot_id,source_path,pointer,pair_ordinal,raw_text,revision,PRIMARY KEY(id,revision))')
    src.execute("INSERT INTO native71_text SELECT id,slot_id,source_path,pointer,pair_ordinal,raw_text,'one' FROM original_text")
    src.execute("INSERT INTO native71_text SELECT id,slot_id,source_path,pointer,pair_ordinal,raw_text,'two' FROM original_text")
    _,texts,_=adapt(src,db,'fixture',COMPONENT,PROVENANCE,0,0)
    assert texts == 4
    assert db.execute('SELECT COUNT(DISTINCT pk_value) FROM research_text').fetchone()[0] == 4
    assert set(r[0] for r in db.execute('SELECT pk_column FROM research_text')) == {'["id","revision"]'}
    src.close();db.close()
