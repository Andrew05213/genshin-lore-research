"""Citation identity transport does not infer claim support or ownership."""
import copy

import pytest

from genshin_lore.citation_handoff import CitationHandoff, bind_capture, render_answer


def binding(tmp_path):
    handoff = CitationHandoff(tmp_path)
    calls = []
    ref = 'synthetic:exact:'+('0123456789'*80)
    results = []
    for n in range(2):
        data = {'evidence_ref': ref, 'raw_text': '合成原文\n第二行',
                'observation_index': n, 'text_range': {'start': n, 'end': n+2},
                'text_proof': {'relation_status': 'physical_candidate'},
                'locale': 'CHS', 'limitations': ['not all records']}
        result = handoff.attach({'status': 'ok', 'data': data}, 'inspect', inspected_ref=ref)
        results.append(result)
        calls.append({'result': result, 'status': 'completed', 'item_id': str(n),
                      'server': 'synthetic', 'tool': 'inspect_evidence'})
    bound = bind_capture({'tool_calls': calls, 'stdout_sha256': 'synthetic capture'}, tmp_path)
    return handoff, bound, results, ref


def test_exact_expansion_preserves_distinct_observations_and_claims(tmp_path):
    _, bound, results, ref = binding(tmp_path)
    handles = [result['data']['citation_handle'] for result in results]
    assert handles[0] != handles[1]
    answer = '合成说明。'+handles[0]+'\n解释未证实。'+handles[1]+handles[0]
    result = render_answer(answer, bound)
    assert result['status'] == 'DELIVERED'
    assert result['model_answer_raw'] == answer
    assert result['rendered_answer'].startswith(answer)
    assert all(record['evidence_ref'] == ref for record in result['citation_resolution'])
    assert [record['observation_index'] for record in result['citation_resolution']] == [0, 1]
    assert 'physical_candidate' in result['rendered_answer']
    assert result['semantic_support'].startswith('NOT_AUTOMATICALLY_CHECKED')


def test_cross_session_and_unknown_handles_are_not_repaired(tmp_path):
    handoff, bound, results, _ = binding(tmp_path)
    handle = results[0]['data']['citation_handle']
    with pytest.raises(ValueError, match='different-connection'):
        CitationHandoff().resolve(handle)
    for invalid in (handle[:-1], '[H0000000000000000:R999:E1]', handle.replace(':R1:', ':R999:')):
        result = render_answer('合成说明。'+invalid, bound)
        assert result['status'] == 'DELIVERY_FAILED'
        assert result['rendered_answer'] is None
    assert handoff.resolve(handle)['evidence_ref'] == bound['mappings'][handle]['evidence_ref']


def test_tampered_mapping_or_result_rejected(tmp_path):
    _, bound, results, _ = binding(tmp_path)
    changed = copy.deepcopy(bound)
    next(iter(changed['mappings'].values()))['evidence_ref'] = 'tampered'
    with pytest.raises(ValueError, match='modified'):
        render_answer('synthetic', changed)
    results[0]['data']['raw_text'] = 'tampered'
    call = {'result': results[0], 'status': 'completed', 'item_id': '0',
            'server': 'synthetic', 'tool': 'inspect_evidence'}
    with pytest.raises(ValueError, match='actual MCP result'):
        bind_capture({'tool_calls': [call], 'stdout_sha256': 'synthetic capture'}, tmp_path)


def test_no_final_or_missing_citation_is_not_delivery_success(tmp_path):
    _, bound, _, _ = binding(tmp_path)
    assert render_answer(None, bound)['status'] == 'NO_FINAL_ANSWER'
    assert render_answer('合成说明但无引用。', bound)['status'] == 'DELIVERY_FAILED'
