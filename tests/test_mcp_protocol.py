"""Exercise actual stdio, SDK registration and cursor/citation boundaries."""
import json
from contextlib import asynccontextmanager
import os
from pathlib import Path
import sys

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest

from scripts.check import payload
from genshin_lore.citation_handoff import bind_capture

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ['describe_corpus', 'search_evidence', 'read_evidence', 'follow_evidence', 'inspect_evidence']


def config_at(output):
    output.mkdir()
    values = {}
    for key in ('corpus', 'profile', 'bindings', 'index'):
        path = output/(key+'.fixture')
        path.write_text('{}')
        values[key] = path.name
    config = output/'runtime-config.json'
    config.write_text(json.dumps(values))
    return config


@asynccontextmanager
async def fixture_client(config, *flags):
    params = StdioServerParameters(command=sys.executable,
        args=[str(ROOT/'tests/mcp_fixture_server.py'), '--config', str(config), *flags],
        env={**os.environ, 'PYTHONPATH': os.pathsep.join([str(ROOT), str(ROOT/'src'), str(ROOT/'tests')])})
    with anyio.fail_after(25):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                yield client


def test_large_distinct_dialogue_bodies_fit_final_wire_and_continue(tmp_path):
    config = config_at(tmp_path/'large')
    async def run():
        async with fixture_client(config, '--large-dialogue') as client:
            async def call(name, **args):
                out = payload(await client.call_tool(name, {'corpus_ref': 'synthetic-corpus', **args}))
                assert out['status'] == 'ok', out
                assert len(json.dumps(out, ensure_ascii=False, separators=(',', ':')).encode()) < 262144
                return out['data']
            anchor = (await call('describe_corpus'))['anchor']
            whole = await call('read_evidence', evidence_ref=anchor, view='dialogue', limit=20)
            assert whole['delivered_count'] == 3
            assert len(whole['display_groups']) == 3
            for group in whole['display_groups']:
                assert 'text' not in group
                pos = group['representative']
                text = whole['nodes'][pos['node_index']]['texts'][pos['text_index']]
                assert len(text['raw_text']) > 45_000
                assert (await call('inspect_evidence', evidence_ref=text['citation_handle']))['evidence_ref']
            first = await call('read_evidence', evidence_ref=anchor, view='dialogue', limit=2)
            assert first['next_cursor'] and first['delivered_count'] == 2
            last = await call('read_evidence', evidence_ref=anchor, view='dialogue', limit=2, cursor=first['next_cursor'])
            combined = first['nodes'] + last['nodes']
            assert len({node['node'] for node in combined}) == 3 and last['next_cursor'] is None
            assert [n['texts'][0]['raw_text'] for n in combined] == [n['texts'][0]['raw_text'] for n in whole['nodes']]
    anyio.run(run)


@pytest.mark.parametrize('explicit_domain', [None, 'fixture_npc'])
def test_reading_previews_honor_explicit_and_automatically_resolved_domain(tmp_path, explicit_domain):
    config = config_at(tmp_path/'domain')
    async def run():
        async with fixture_client(config, '--domain-npc') as client:
            result = payload(await client.call_tool('search_evidence', {
                'corpus_ref': 'synthetic-corpus', 'query': '合成角色甲',
                'locale': 'CHS', 'mode': 'hybrid', 'domain': explicit_domain}))
            assert result['status'] == 'ok'
            data = result['data']
            assert data['domain_filter'] == ['npc_archive']
            assert data['items'] and all(h['family'] == 'npc_archive' for h in data['items'])
            assert data['reading_view']['body_previews'] == []
            assert data['reading_view']['name_matches'] and data['reading_view']['next_actions']
    anyio.run(run)


@pytest.mark.parametrize('padding', [0, 180_000, 245_000])
def test_large_search_previews_preserve_hits_cursor_actions_and_final_receipt(tmp_path, padding):
    config = config_at(tmp_path/'search')
    async def run():
        async with fixture_client(config, '--large-search', '--search-padding', str(padding)) as client:
            args = {'corpus_ref': 'synthetic-corpus', 'query': '合成角色甲', 'locale': 'CHS', 'limit': 1}
            raw = payload(await client.call_tool('search_evidence', {**args, 'detail': 'full'}))
            result = payload(await client.call_tool('search_evidence', args))
            assert raw['status'] == result['status'] == 'ok'
            assert len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()) < 262144
            data = result['data']; view = data['reading_view']
            assert data['next_cursor'] == raw['data']['next_cursor'] and data['next_cursor']
            assert view['next_actions'] and view['partial']
            for old, new in zip(raw['data']['items'], data['items']):
                for key in ('evidence_ref', 'container_ref', 'snippet', 'owner_field', 'family', 'ranking_basis'):
                    assert old[key] == new[key]
            if padding:
                assert len(view['body_previews']) < 3 and view['omission_reason'] == 'response_byte_limit'
            else:
                assert len(view['body_previews']) == 3
            for group in view['text_groups']:
                assert 'text' not in group
                assert group['representative']['preview_index'] < len(view['body_previews'])
            calls = [{'result': out, 'status': 'completed', 'item_id': str(i),
                      'server': 'fixture', 'tool': 'search_evidence'} for i, out in enumerate((raw, result))]
            bound = bind_capture({'tool_calls': calls, 'stdout_sha256': 'fixture'}, config.parent/'handoff')
            assert bound['mappings'] and len(list((config.parent/'handoff').glob('*.json'))) == 2
            for body in view['body_previews']:
                inspected = payload(await client.call_tool('inspect_evidence', {
                    'corpus_ref': 'synthetic-corpus', 'evidence_ref': body['citation_handle']}))
                assert inspected['status'] == 'ok'
                assert inspected['data']['evidence_ref'] == body['evidence_ref']
    anyio.run(run)


def test_near_cap_search_keeps_exact_results_without_optional_panel_or_handles(tmp_path):
    config = config_at(tmp_path/'near-cap')
    async def run():
        async with fixture_client(config, '--large-search', '--search-target-bytes', '261900') as client:
            args = {'corpus_ref': 'synthetic-corpus', 'query': '合成角色甲', 'locale': 'CHS', 'limit': 1}
            raw = payload(await client.call_tool('search_evidence', {**args, 'detail': 'full'}))
            result = payload(await client.call_tool('search_evidence', args))
            assert result == raw and result['status'] == 'ok'
            assert len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()) == 261900
            assert result['data']['next_cursor'] and 'reading_view' not in result['data']
            assert 'citation_handoff' not in result and not list((config.parent/'handoff').glob('*.json'))
            exact = result['data']['items'][0]['evidence_ref']
            inspected = payload(await client.call_tool('inspect_evidence', {'corpus_ref': 'synthetic-corpus', 'evidence_ref': exact}))
            assert inspected['status'] == 'ok' and inspected['data']['evidence_ref'] == exact
            assert inspected['citation_handoff']['request'] == 'R1'
    anyio.run(run)


def test_real_mcp_stdio_from_two_isolated_configs(tmp_path):
    configs = [config_at(tmp_path/arm) for arm in ('a', 'b')]

    async def run(config):
        params = StdioServerParameters(command=sys.executable,
            args=[str(ROOT/'tests/mcp_fixture_server.py'), '--config', str(config)],
            env={**os.environ, 'PYTHONPATH': os.pathsep.join([str(ROOT), str(ROOT/'src'), str(ROOT/'tests')])})
        with anyio.fail_after(20):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    tools = (await client.list_tools()).tools
                    assert [tool.name for tool in tools] == TOOLS
                    async def call(tool, **args):
                        return payload(await client.call_tool(tool, {'corpus_ref': 'synthetic-corpus', **args}))
                    described = await call('describe_corpus')
                    assert described['data']['fixture_config'] == str(config.parent/'profile.fixture')
                    ref = described['data']['anchor']
                    hit = await call('search_evidence', query='合', locale='CHS', limit=1)
                    assert hit['status'] == 'ok' and hit['data']['items']
                    page = await call('read_evidence', evidence_ref=ref, view='dialogue', limit=1, detail='full')
                    assert page['status'] == 'ok'
                    data = page['data']
                    assert data['next_cursor']
                    handle = data['nodes'][0]['texts'][0]['citation_handle']
                    inspected = await call('inspect_evidence', evidence_ref=handle)
                    assert inspected['status'] == 'ok'
                    assert inspected['data']['evidence_ref'] == data['nodes'][0]['texts'][0]['evidence_ref']
                    compact = await call('read_evidence', evidence_ref=ref, view='dialogue', limit=1)
                    body = compact['data']['nodes'][0]['texts'][0]
                    assert 'evidence_ref' not in body
                    assert body['raw_text'] == data['nodes'][0]['texts'][0]['raw_text']
                    resolved = await call('inspect_evidence', evidence_ref=body['citation_handle'])
                    assert resolved['data']['evidence_ref'] == inspected['data']['evidence_ref']
                    discovery = payload(await client.call_tool('describe_corpus', {}))
                    assert discovery['status'] == 'ok' and discovery['corpus_ref'] == 'synthetic-corpus'
                    invalid = await call('read_evidence', evidence_ref=ref, detail='unknown')
                    assert invalid['status'] == 'invalid_request'
                    assert invalid['recovery']['corpus_ref'] == 'synthetic-corpus'
                    continued = await call('read_evidence', evidence_ref=ref, view='dialogue',
                                           limit=1, cursor=data['next_cursor'], detail='full')
                    assert continued['status'] == 'ok'
                    assert continued['data']['nodes'][0]['node'] != data['nodes'][0]['node']
                    wrong = await call('read_evidence', evidence_ref=ref, view='dialogue',
                                       limit=2, cursor=data['next_cursor'], detail='full')
                    assert wrong['status'] == 'invalid_request'
                    bad = await call('inspect_evidence', evidence_ref='[H0000000000000000:R1:E1]')
                    assert bad['status'] == 'invalid_request'
                    followed = await call('follow_evidence', evidence_ref=ref, relation_kind='dialogue_predecessor')
                    assert followed['status'] == 'ok'
    for config in configs:
        anyio.run(run, config)


def test_unsupported_sdk_version_fails_clearly(monkeypatch):
    from genshin_lore import mcp_transport
    monkeypatch.setattr(mcp_transport, 'version', lambda _: 'unsupported')
    with pytest.raises(RuntimeError, match='mcp==2.1.1'):
        mcp_transport._check_sdk()


def test_research_transport_no_longer_imports_legacy_service():
    import subprocess
    result = subprocess.run([sys.executable, '-c',
        "import sys; import genshin_lore.research_server; "
        "assert 'genshin_lore.mcp_server' not in sys.modules"],
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
