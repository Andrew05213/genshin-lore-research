"""Actual stdio search -> returned action -> exact read/inspect, synthetic only."""
import anyio

from test_mcp_protocol import config_at, fixture_client
from scripts.check import payload


def test_body_first_mcp_pages_actions_and_scoped_handles_keep_exact_occurrences(tmp_path):
    config = config_at(tmp_path / 'body-first')

    async def run():
        async with fixture_client(config) as client:
            args = {'corpus_ref': 'synthetic-corpus', 'query': '合成', 'locale': 'CHS', 'limit': 1}
            hits, cursor = [], None
            for _ in range(5):
                result = payload(await client.call_tool('search_evidence', {**args, 'cursor': cursor}))
                assert result['status'] == 'ok', result
                data = result['data']
                assert data['ranking']['mode'] == 'body-first-v2'
                hit = data['items'][0]
                hits.append(hit)
                if hit['owner_field'] == 'talkContentTextMapHash':
                    action = next(a for a in data['reading_view']['next_actions']
                                  if a['tool'] == 'read_evidence'
                                  and a['arguments'].get('view', 'physical') == 'physical')
                    read = payload(await client.call_tool(action['tool'], action['arguments']))
                    assert read['status'] == 'ok', read
                    assert read['data']['raw_text'] == hit['snippet']
                    handle = read['data']['citation_handle']
                    inspected = payload(await client.call_tool('inspect_evidence', {
                        'corpus_ref': args['corpus_ref'], 'evidence_ref': handle}))
                    assert inspected['status'] == 'ok', inspected
                    assert inspected['data']['evidence_ref'] == hit['evidence_ref']
                    full = payload(await client.call_tool('read_evidence', {
                        'corpus_ref': args['corpus_ref'], 'evidence_ref': handle, 'detail': 'full'}))
                    assert full['data']['provenance'] == inspected['data']['text_provenance']
                cursor = data['next_cursor']
                if cursor is None:
                    assert data['scope_complete']
                    break
            assert cursor is None and len(hits) == len({h['evidence_ref'] for h in hits}) == 4
            assert [h['owner_field'] for h in hits] == ['talkContentTextMapHash'] * 3 + ['nameTextMapHash']
            assert hits[1]['snippet'] == hits[2]['snippet']
            assert hits[1]['evidence_ref'] != hits[2]['evidence_ref']

    anyio.run(run)
