"""Synthetic edge cases; actual transport/source integration is tested separately."""
import copy

import pytest

from scripts.read_scope_state import ReadState


SESSION = '0000000000000001'
H1 = '[H'+SESSION+':R1:E1]'
H2 = '[H'+SESSION+':R2:E1]'


def action(ref=H1, **args):
    return {'tool': 'read_evidence', 'arguments': {
        'corpus_ref': 'synthetic', 'evidence_ref': ref, 'detail': 'full', **args}}


def page(exact='container-A', *, next_cursor=None, partial=False, n=0):
    return {'status': 'ok', 'corpus_ref': 'synthetic',
            'citation_handoff': {'session': SESSION}, 'data': {
                'evidence_ref': exact, 'metadata': {'container_kind': 'synthetic'},
                'physical_container': exact, 'context': [{
                    'evidence_ref': exact+'-row-'+str(n), 'raw_text': 'same synthetic body',
                    'ordinal': n, 'pointer': '/line/'+str(n), 'locale': 'CHS'}],
                'delivered_count': 1, 'scope_complete': next_cursor is None,
                'next_cursor': next_cursor, 'partial': partial}}


def bind(state, handle=H2, exact='container-A'):
    state.learn({'status': 'ok', 'citation_handoff': {'session': SESSION},
                 'data': {'citation_handle': handle, 'evidence_ref': exact}})


def test_partial_stream_preserves_original_anchor_parameters_and_cursor():
    s=ReadState();a=action(limit=2, locale='EN', before=0, after=0)
    original=copy.deepcopy(a)
    assert s.observe(a,page(next_cursor='returned-middle'))
    bind(s)
    resume=s.prepare(action(H2,limit=2,locale='EN',before=0,after=0))
    assert resume['arguments']=={**original['arguments'],'cursor':'returned-middle'}
    assert a==original
    assert s.observe(resume,page(n=2))
    assert s.prepare(action(H2,limit=2,locale='EN',before=0,after=0)) is None


@pytest.mark.parametrize('field,value', [('view','dialogue'),('locale','EN'),
    ('detail','reading'),('before',1),('after',1),('limit',7),('corpus_ref','other')])
def test_completed_scope_never_covers_a_different_read_contract(field,value):
    s=ReadState();s.observe(action(),page());bind(s)
    assert s.prepare(action(H2,**{field:value})) is not None


def test_equal_text_keeps_different_physical_occurrences():
    s=ReadState();s.observe(action(),page());bind(s,exact='container-B')
    request=action(H2)
    assert s.prepare(request)==request
    assert s.observe(request,page('container-B'))
    assert len(s.scopes)==2


@pytest.mark.parametrize('status', ['unavailable','invalid_request','response_too_large','not_found'])
def test_failed_read_and_failed_aliases_remain_retryable(status):
    s=ReadState();a=action()
    assert not s.observe(a,{'status':status,'data':{
        'citation_handle':H1,'evidence_ref':'wrong-ref'}})
    assert s.canonical(H1)==H1 and s.prepare(a)==a and not s.scopes
    assert s.observe(a,page())


@pytest.mark.parametrize('change', [
    {'partial':True}, {'context_truncated':True}, {'truncated':True},
    {'page_info':{'truncated':True}}, {'scope_complete':False},
    {'delivered_count':99},
])
def test_incomplete_without_continuation_is_not_cached_as_complete(change):
    s=ReadState();a=action();response=page();response['data'].update(change)
    assert not s.observe(a,response)
    assert s.prepare(a)==a and not s.scopes
    assert s.observe(a,page())


def test_bare_body_and_search_preview_do_not_cover_container():
    s=ReadState();a=action()
    for d in [{'evidence_ref':'container-A','raw_text':'synthetic'},
              {'evidence_ref':'container-A','items':[], 'scope_complete':True},
              {'reading_view':{'body_previews':[{'raw_text':'synthetic'}]},
               'scope_complete':True}]:
        assert not s.observe(a,{'status':'ok','data':d})
        assert s.prepare(a)==a
    assert not s.observe({'tool':'search_evidence','arguments':{}},page())


def test_skipping_middle_page_cannot_finish_root_or_rollback_frontier():
    s=ReadState();a=action();s.observe(a,page(next_cursor='middle'))
    last=action(cursor='last')
    assert not s.observe(last,page(n=4))
    assert s.prepare(a)['arguments']['cursor']=='middle'
    # Replayed root must not reset the pending chain.
    assert not s.observe(a,page(next_cursor='wrong-frontier'))
    assert s.prepare(a)['arguments']['cursor']=='middle'
    assert s.observe(s.prepare(a),page(next_cursor='last',n=2))
    assert s.observe(s.prepare(a),page(n=4))
    assert s.prepare(a) is None
    # Unknown cursor is never hidden by completed coverage.
    assert s.prepare(action(cursor='never-delivered')) is not None


def test_context_window_does_not_complete_its_container():
    s=ReadState();a=action('text-A',before=1,after=1)
    response={'status':'ok','data':{'evidence_ref':'text-A','container_ref':'container-A',
        'raw_text':'synthetic body','provenance':{'source_file':'synthetic.txt'},
        'context':[], 'context_truncated':False}}
    assert s.observe(a,response)
    assert s.prepare(a) is None
    assert s.prepare(action('container-A')) is not None
    assert s.prepare(action('text-A',before=0,after=1)) is not None
    response['data']['partial']=True
    other=ReadState();assert not other.observe(a,response)


def test_alias_conflict_is_atomic_and_connections_are_isolated():
    s=ReadState();bind(s,H1)
    with pytest.raises(ValueError,match='rebound'):
        bind(s,H1,'other-ref')
    assert s.canonical(H1)=='container-A'
    foreign='[H0000000000000002:R1:E1]'
    with pytest.raises(ValueError,match='different MCP connection'):
        s.learn({'status':'ok','citation_handoff':{'session':'0000000000000002'},
                 'data':{'citation_handle':foreign,'evidence_ref':'other'}})
    assert ReadState().canonical(H1)==H1
    s.clear();assert s.canonical(H1)==H1 and not s.scopes


def test_failed_inspection_entry_cannot_poison_alias_map():
    s=ReadState()
    s.learn({'status':'ok','data':{'results':[{'status':'failed',
        'selected_reference':H1,'data':{'evidence_ref':'wrong'}}]}})
    assert s.canonical(H1)==H1


def test_candidate_status_is_not_a_failed_transport_or_logical_identity_upgrade():
    s=ReadState();s.observe(action(),page())
    s.learn({'status':'ok','citation_handoff':{'session':SESSION},'data':{
        'items':[{'status':'physical_candidate','logical_identity_proven':False,
                  'target_citation_handle':H2,'target_evidence_ref':'container-A'}]}})
    assert s.canonical(H2)=='container-A'
    assert s.prepare(action(H2)) is None


def test_legacy_dialogue_partial_chain_continues_but_does_not_erase_uncertainty():
    s=ReadState();a=action(view='dialogue',limit=1)
    def speech(next_cursor,partial):
        return {'status':'ok','data':{'evidence_ref':'container-A','view':'dialogue',
            'nodes':[], 'delivered_count':0,'next_cursor':next_cursor,
            'reachable_local_graph_exhausted':next_cursor is None,'partial':partial}}
    assert s.observe(a,speech('next',True))
    assert s.prepare(a)['arguments']['cursor']=='next'
    assert s.observe(s.prepare(a),speech(None,False))
    assert not next(iter(s.scopes.values()))['complete']
    assert s.prepare(a) is not None


def test_corpus_mismatch_does_not_learn_or_complete():
    s=ReadState();response=page();response['corpus_ref']='foreign'
    with pytest.raises(ValueError,match='corpus'):
        s.observe(action(),response)
    assert not s.scopes and not s.aliases
