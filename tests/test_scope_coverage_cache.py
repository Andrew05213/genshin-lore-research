"""Cache only frozen index metadata; retain scope and live availability states."""
from collections import OrderedDict
import sqlite3
import threading

from genshin_lore.research_query import ResearchQuery, MAX_SCOPE_CACHE_ENTRIES


def query(source='alpha'):
    q = object.__new__(ResearchQuery)
    q.db = sqlite3.connect(':memory:')
    q.db.executescript('CREATE TABLE research_container(source_key,evidence_family);'
                      'CREATE TABLE research_text(source_key,locale);')
    q.db.executemany('INSERT INTO research_container VALUES(?,?)',
                    [(source, 'first'), ('beta', 'second')])
    q.db.executemany('INSERT INTO research_text VALUES(?,?)',
                    [(source, 'CHS'), (source, 'CHS'), (source, None), ('beta', 'EN')])
    q.components = {source: {}, 'beta': {}, 'unsupported': {}}
    q.coverage = [{'source_key': key, 'projection_kind': kind, 'status': status}
                  for key, kind, status in [(source, 'text', 'complete'),
                      ('beta', 'text', 'complete'), ('unsupported', 'text', 'unsupported'),
                      ('unsupported', 'relation', 'complete')]]
    q.unavailable = []
    q.source_lock = threading.RLock()
    q._scope_source_cache = OrderedDict()
    return q


def test_metadata_reuse_keeps_locale_family_unknown_and_unavailable_distinct():
    q = query()
    sql = []
    q.db.set_trace_callback(sql.append)
    try:
        assert q._scope_coverage(locale='CHS') == (
            ['alpha', 'beta'], ['unsupported'], [], ['alpha'], ['alpha'])
        before = len(sql)
        q.unavailable.append('alpha')
        result = q._scope_coverage(locale='CHS')
        assert result == (['alpha', 'beta'], ['unsupported'], ['alpha'], ['alpha'], ['alpha'])
        assert len(sql) == before
        result[0].clear(); result[3].clear()
        assert q._scope_coverage(locale='CHS')[3] == ['alpha']
        assert q._scope_coverage(['second'], 'CHS') == (['beta'], [], [], [], [])
        assert q._scope_coverage(['second'], 'EN') == (['beta'], [], [], ['beta'], [])
        assert q._scope_coverage(['not-declared'], 'CHS') == (
            [], ['family:not-declared'], [], [], [])
        assert q._scope_coverage(locale='missing')[3:] == ([], ['alpha'])
        assert q._scope_coverage()[3:] == ([], [])
        # A fresh result still reflects non-index status changes.
        q.coverage[0]['status'] = 'unsupported'
        assert q._scope_coverage(locale='CHS')[0] == ['beta']
        assert q._scope_coverage(locale='CHS')[1] == ['alpha', 'unsupported']
    finally:
        q.db.close()


def test_normalized_family_scopes_share_locale_metadata_without_sharing_connections():
    q = query(); other = query('different-source')
    sql = [];q.db.set_trace_callback(sql.append)
    try:
        expected = q._scope_coverage(['second', 'first'], 'CHS')
        count = len(sql)
        assert q._scope_coverage(['first', 'second', 'first'], 'CHS') == expected
        assert len(sql) == count
        q._scope_coverage(['first'], 'CHS')
        assert sum('from research_text where locale=' in x for x in sql) == 1
        assert other._scope_coverage(['first'], 'CHS')[3] == ['different-source']
        assert q._scope_coverage(['first'], 'CHS')[3] == ['alpha']
    finally:
        q.db.close();other.db.close()


def test_arbitrary_scopes_are_bounded_and_empty_results_are_cached():
    q = query();sql=[];q.db.set_trace_callback(sql.append)
    try:
        assert q._scope_coverage(['absent'], 'CHS')[0] == []
        count=len(sql)
        assert q._scope_coverage(['absent'], 'CHS')[0] == []
        assert len(sql)==count
        for i in range(MAX_SCOPE_CACHE_ENTRIES + 10):
            q._scope_coverage(['absent-'+str(i)])
        assert len(q._scope_source_cache) <= MAX_SCOPE_CACHE_ENTRIES
        # Eviction may reread metadata; it must not change results.
        assert q._scope_coverage(['first'], 'CHS') == (
            ['alpha'], [], [], ['alpha'], ['alpha'])
    finally:
        q.db.close()
