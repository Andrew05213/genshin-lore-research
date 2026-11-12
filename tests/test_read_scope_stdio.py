"""Real stdio over the existing source-checked synthetic MCP fixture."""
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.read_scope import StdioReader, read_one_scope
from test_mcp_protocol import config_at


ROOT=Path(__file__).resolve().parents[1]


@contextmanager
def reader(tmp_path, *flags):
    config=config_at(tmp_path/'config')
    env={**os.environ,'PYTHONPATH':os.pathsep.join([str(ROOT),str(ROOT/'src'),str(ROOT/'tests')])}
    client=StdioReader([sys.executable,str(ROOT/'tests/mcp_fixture_server.py'),
                        '--config',str(config),*flags],tmp_path/'trace',env=env)
    try:
        client.initialize()
        yield client
    finally:
        client.close()


def discover(client):
    response=client.call('describe_corpus',{})
    assert response['status']=='ok'
    return response['data']['anchor']


def test_actual_container_aliases_keep_remaining_page_and_inspect_provenance(tmp_path):
    with reader(tmp_path,'--paged-physical') as c:
        anchor=discover(c)
        # Three actual text occurrences share a source container; two have
        # equal body bytes but distinct locale/ref coordinates.
        args={'corpus_ref':'synthetic-corpus','evidence_ref':anchor,
              'view':'physical','detail':'full','locale':'CHS','limit':1}
        first=c.read(args)
        assert first['status']=='ok' and first['data']['next_cursor']
        assert c.state.scopes and not next(iter(c.state.scopes.values()))['complete']
        original_cursor=first['data']['next_cursor']
        other=c.call('inspect_evidence',{'corpus_ref':'synthetic-corpus',
                                       'evidence_ref':anchor,'detail':'full'})
        alias=other['data']['citation_handle']
        assert alias!=first['data']['citation_handle']
        second=c.read({**args,'evidence_ref':alias})
        actual_request=c.calls[-1]['params']['arguments']
        assert actual_request=={**args,'cursor':original_cursor}
        assert second['status']=='ok'
        last=c.read(args)
        assert last['status']=='ok' and last['data']['next_cursor'] is None
        rows=[x['data']['context'][0] for x in [first,second,last]]
        refs=[n['evidence_ref'] for n in rows]
        bodies=[n['raw_text'] for n in rows]
        assert len(set(refs))==3 and bodies[1]==bodies[2]
        for text in rows:
            checked=c.call('inspect_evidence',{'corpus_ref':'synthetic-corpus',
                         'evidence_ref':text['citation_handle'],'detail':'full'})
            read=c.call('read_evidence',{'corpus_ref':'synthetic-corpus',
                        'evidence_ref':text['citation_handle'],'detail':'full'})
            assert read['data']['raw_text']==text['raw_text']
            assert checked['data']['text_provenance']==read['data']['provenance']
        before=c.business_calls
        assert c.read({**args,'evidence_ref':alias}) is None
        assert c.business_calls==before
        # A language or context/view change is a new real request, not covered.
        en=c.read({**args,'locale':'EN'})
        assert en is not None
        physical=c.read({**args,'view':'physical','before':1,'after':1})
        assert physical is not None and physical['status']=='ok'
        dialogue=c.read({**args,'view':'dialogue'})
        assert dialogue is not None and dialogue['status']=='ok'
        assert dialogue['data']['view']=='dialogue'


def test_failed_actual_read_can_retry_and_completed_range_stops_early(tmp_path):
    with reader(tmp_path) as c:
        anchor=discover(c)
        bad={'corpus_ref':'wrong-corpus','evidence_ref':anchor,'detail':'full'}
        assert c.read(bad)['status']=='not_found'
        before=c.business_calls
        assert c.read(bad)['status']=='not_found' and c.business_calls==before+1
        args={'corpus_ref':'synthetic-corpus','evidence_ref':anchor,
              'view':'physical','locale':'CHS','limit':1,'detail':'full'}
        result=read_one_scope(c,args,max_calls=20)
        assert result['complete'] and result['stop_reason']=='scope_eof'
        assert c.business_calls<20
        assert any(call['params'].get('name')=='inspect_evidence' for call in c.calls)


def test_oversized_response_is_retryable_and_handles_do_not_cross_connections(tmp_path):
    one=tmp_path/'one';one.mkdir()
    with reader(one,'--large-search','--search-body-padding','270000') as c:
        anchor=discover(c)
        # This page really hits the server byte guard; no response is patched.
        hits=c.call('search_evidence',{'corpus_ref':'synthetic-corpus','query':'合成发言0',
                    'limit':10,'locale':'CHS','detail':'full'})['data']['items']
        body=next(h['evidence_ref'] for h in hits if h['owner_field']=='DialogNormal')
        args={'corpus_ref':'synthetic-corpus','evidence_ref':body,
              'view':'physical','detail':'full','locale':'CHS','limit':20}
        first=c.read(args)
        assert first['status'] in ['response_too_large','unavailable']
        assert not c.state.scopes
        before=c.business_calls
        assert c.read(args) is not None and c.business_calls==before+1
        handle=c.call('inspect_evidence',{'corpus_ref':'synthetic-corpus',
                     'evidence_ref':anchor})['data']['citation_handle']
    assert not c.state.aliases and not c.state.scopes
    two=tmp_path/'two';two.mkdir()
    with reader(two) as d:
        assert not d.state.aliases
        rejected=d.read({'corpus_ref':'synthetic-corpus','evidence_ref':handle,'detail':'full'})
        assert rejected['status']=='invalid_request' and not d.state.scopes


def test_import_and_help_do_not_open_database_or_spawn_server(tmp_path,monkeypatch):
    def forbidden(*args,**kwargs):
        raise AssertionError('import must not start subprocesses')
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    spec=importlib.util.spec_from_file_location('scope_import_probe',ROOT/'scripts/read_scope.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.undo()
    result=subprocess.run([sys.executable,str(ROOT/'scripts/read_scope.py'),'--help',
                           '--config',str(tmp_path/'missing-config')],capture_output=True,text=True)
    assert result.returncode==0 and '--evidence-ref' in result.stdout
    assert not list(tmp_path.iterdir())


def test_inspect_failure_after_eof_must_retry_qualification_before_completion(tmp_path):
    with reader(tmp_path,'--fail-inspect-once') as c:
        anchor=discover(c)
        args={'corpus_ref':'synthetic-corpus','evidence_ref':anchor,
              'view':'physical','detail':'full','limit':1}
        result=read_one_scope(c,args)
        assert not result['complete'] and result['stop_reason']=='inspection_incomplete'
        assert next(iter(c.state.scopes.values()))['complete']
        assert c.pending_checks
        reads=sum(v['params'].get('name')=='read_evidence' for v in c.calls)
        before=c.business_calls
        retried=read_one_scope(c,args)
        assert retried['complete'] and not c.pending_checks
        assert c.business_calls==before+1
        assert sum(v['params'].get('name')=='read_evidence' for v in c.calls)==reads


def test_actual_timeout_closes_epoch_without_marking_read_then_new_connection_retries(tmp_path):
    one=tmp_path/'slow';one.mkdir()
    with reader(one,'--read-delay','0.2') as c:
        anchor=discover(c)
        c.timeout=0.01
        args={'corpus_ref':'synthetic-corpus','evidence_ref':anchor,'detail':'full'}
        with pytest.raises(TimeoutError):
            c.read(args)
        assert not c.state.scopes and not c.state.aliases and c.broken
        with pytest.raises(RuntimeError,match='fresh reader'):
            c.read(args)
    two=tmp_path/'retry';two.mkdir()
    with reader(two) as fresh:
        assert fresh.read(args)['status']=='ok'


@pytest.mark.parametrize('paged', [False, True])
def test_task_inspects_previously_read_pages_before_reporting_completion(tmp_path, paged):
    flags = ('--paged-physical',) if paged else ()
    with reader(tmp_path, *flags) as c:
        args = {'corpus_ref': 'synthetic-corpus', 'evidence_ref': discover(c),
                'view': 'physical', 'detail': 'full', 'locale': 'CHS', 'limit': 1}
        first = c.read(args)
        first_ref = first['data']['context'][0]['evidence_ref']
        assert bool(first['data']['next_cursor']) is paged
        result = read_one_scope(c, args)
        assert result['complete']
        inspected = {
            item['data']['evidence_ref']
            for call in c.calls
            if call['params'].get('name') == 'inspect_evidence'
            for item in call.get('payload', {}).get('data', {}).get('results', [])
            if item.get('status') == 'ok'
        }
        assert first_ref in inspected
        assert not c.pending_checks


def test_task_never_swallows_unknown_cursor_after_scope_completion(tmp_path):
    with reader(tmp_path) as c:
        args = {'corpus_ref': 'synthetic-corpus', 'evidence_ref': discover(c),
                'view': 'physical', 'detail': 'full', 'locale': 'CHS', 'limit': 1}
        assert read_one_scope(c, args)['complete']
        before = c.business_calls
        result = read_one_scope(c, {**args, 'cursor': 'never-returned-invalid-cursor'})
        assert not result['complete'] and result['stop_reason'] == 'read_rejected'
        assert c.business_calls == before + 1
        assert c.calls[-1]['payload']['status'] == 'invalid_request'
