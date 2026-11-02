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
from genshin_lore.citation_handoff import bind_capture, render_answer

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


def test_oversized_first_speech_only_omits_preview_not_successful_search(tmp_path):
    config = config_at(tmp_path/'oversized-first')
    async def run():
        async with fixture_client(config, '--large-search', '--search-body-padding', '160000') as client:
            args = {'corpus_ref': 'synthetic-corpus', 'query': '合成角色甲', 'locale': 'CHS', 'limit': 1}
            raw = payload(await client.call_tool('search_evidence', {**args, 'detail': 'full'}))
            result = payload(await client.call_tool('search_evidence', args))
            assert result['status'] == raw['status'] == 'ok'
            assert result['data']['next_cursor'] == raw['data']['next_cursor'] and result['data']['next_cursor']
            assert result['data']['items'][0]['evidence_ref'] == raw['data']['items'][0]['evidence_ref']
            view = result['data']['reading_view']
            assert view['body_previews'] == [] and view['next_actions'] and view['partial']
            assert view['omission_reason'] == 'preview_read_byte_limit'
            action = view['next_actions'][0]
            assert action['tool'] == 'read_evidence' and action['arguments']['view'] == 'physical'
            body = payload(await client.call_tool(action['tool'], action['arguments']))
            assert body['status'] == 'ok' and len(body['data']['raw_text']) > 160_000
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
                    saved = await call('inspect_evidence', evidence_ref=body['citation_handle'], save_source=True)
                    assert saved['status'] == 'ok'
                    card = saved['data']['source_card']
                    assert card['status'] == 'SAVED'
                    stored = json.loads(Path(card['metadata_path']).read_bytes())
                    assert stored['evidence_ref'] == inspected['data']['evidence_ref']
                    assert stored['quoted_text'] == body['raw_text']
                    assert Path(card['markdown_path']).is_relative_to(config.parent)
                    wrong_save = await call('inspect_evidence', evidence_ref=stored['evidence_ref'], save_source=True)
                    assert wrong_save['status'] == 'invalid_request'
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


def test_source_card_save_failure_is_explicit_without_breaking_plain_inspection(tmp_path):
    config = config_at(tmp_path/'cards-io-failure')
    (config.parent/'source-cards').write_text('synthetic conflicting file')
    async def run():
        async with fixture_client(config) as client:
            async def call(name, **args):
                return payload(await client.call_tool(name, {'corpus_ref': 'synthetic-corpus', **args}))
            anchor = (await call('describe_corpus'))['data']['anchor']
            page = (await call('read_evidence', evidence_ref=anchor, view='dialogue', limit=1))['data']
            handle = page['nodes'][0]['texts'][0]['citation_handle']
            failure = await call('inspect_evidence', evidence_ref=handle, save_source=True)
            assert failure['status'] == 'unavailable' and failure['partial']
            assert 'I/O failed' in failure['error']
            plain = await call('inspect_evidence', evidence_ref=handle)
            assert plain['status'] == 'ok'
            assert (config.parent/'source-cards').read_text() == 'synthetic conflicting file'
    anyio.run(run)


def test_batch_save_progress_and_supplied_read_actions_over_real_stdio(tmp_path):
    config = config_at(tmp_path/'batch')
    async def run():
        async with fixture_client(config) as client:
            tools = (await client.list_tools()).tools
            assert [t.name for t in tools] == TOOLS
            assert all('ctx' not in t.input_schema.get('properties', {}) for t in tools)
            events = []
            async def progress(value, total, message):
                events.append((value,total,message))
            async def call(name, **args):
                return payload(await client.call_tool(name, {'corpus_ref':'synthetic-corpus',**args},
                                                     progress_callback=progress))
            anchor = (await call('describe_corpus'))['data']['anchor']
            first = await call('read_evidence',evidence_ref=anchor,view='dialogue',limit=1)
            action = first['data']['reading_actions'][0]
            second = payload(await client.call_tool(action['tool'], action['arguments']))
            assert second['status']=='ok'
            assert first['data']['nodes'][0]['node'] != second['data']['nodes'][0]['node']
            context = first['data']['nodes'][0]['context_action']
            expanded = payload(await client.call_tool(context['tool'],context['arguments']))
            assert expanded['status']=='ok' and expanded['data']['detail']=='full'
            assert len(expanded['data']['nodes'])>1  # actual linked bodies, not just extra metadata
            earlier=first['data']['nodes'][0]['predecessor_action']
            previous=payload(await client.call_tool(earlier['tool'],earlier['arguments']))
            assert previous['status']=='ok' and previous['data']['items']
            pages = (first,second)
            bodies = [p['data']['nodes'][0]['texts'][0] for p in pages]
            handles = [b['citation_handle'] for b in bodies]
            saved = await call('inspect_evidence', evidence_refs=handles, save_source=True)
            assert saved['status']=='ok' and not saved['data']['partial']
            assert all(r['status']=='ok' for r in saved['data']['results'])
            card = saved['data']['source_card']
            metadata = json.loads(Path(card['metadata_path']).read_bytes())
            assert [e['quoted_text'] for e in metadata['entries']] == [b['raw_text'] for b in bodies]
            for entry in metadata['entries']:
                full = await call('inspect_evidence',evidence_ref=entry['evidence_ref'])
                assert full['data']['text_provenance']==entry['inspection']['text_provenance']
            bound = bind_capture({'tool_calls':[{'result':p,'status':'completed','item_id':str(i),
                'server':'fixture','tool':'fixture'} for i,p in enumerate((*pages,saved))],
                'stdout_sha256':'fixture'},config.parent/'handoff')
            assert set(bound['inspected_refs'])=={e['evidence_ref'] for e in metadata['entries']}
            delivery=render_answer('合成摘录 '+ ' '.join(handles),bound)
            assert delivery['status']=='DELIVERED'
            assert [r['evidence_ref'] for r in delivery['citation_resolution']]==[e['evidence_ref'] for e in metadata['entries']]
            assert events and any(total==2 and value==1 for value,total,_ in events)
            assert events[-1][:2] == (2,2)
            before = set((config.parent/'source-cards').iterdir())
            failed = await call('inspect_evidence', evidence_refs=[handles[0],'[H0000000000000000:R1:E1]'],save_source=True)
            assert failed['data']['partial'] and failed['data']['results'][0]['status']=='ok'
            assert failed['data']['results'][1]['status']=='failed'
            assert failed['data']['source_card']['status']=='NOT_SAVED'
            assert set((config.parent/'source-cards').iterdir())==before
            duplicate = await call('inspect_evidence', evidence_refs=[handles[0]]*2,save_source=True)
            assert duplicate['status']=='invalid_request'
            for args in ({'evidence_refs':[]},{'evidence_refs':handles*17},
                         {'evidence_ref':handles[0],'evidence_refs':handles}):
                assert (await call('inspect_evidence',**args))['status']=='invalid_request'
            exacts=[entry['evidence_ref'] for entry in metadata['entries']]
            not_read=await call('inspect_evidence',evidence_refs=exacts,save_source=True)
            assert not_read['data']['partial'] and not_read['data']['source_card']['status']=='NOT_SAVED'
            assert set((config.parent/'source-cards').iterdir())==before
    anyio.run(run)


def test_codex_context_action_reads_embedded_neighbour_bodies(tmp_path):
    config=config_at(tmp_path/'codex-context')
    async def run():
        async with fixture_client(config,'--domain-npc') as client:
            async def call(name, **args):
                out=payload(await client.call_tool(name,{'corpus_ref':'synthetic-corpus',**args}))
                assert out['status']=='ok',out
                return out['data']
            anchor=(await call('describe_corpus'))['anchor']
            page=await call('read_evidence',evidence_ref=anchor,view='dialogue',limit=1)
            node=page['nodes'][0]
            assert 'predecessor_action' not in node
            action=node['context_action']
            assert action['arguments']['limit']==1
            full=payload(await client.call_tool(action['tool'],action['arguments']))['data']
            context=full['nodes'][0]['source_context']
            neighbours=context['before']+context['after']
            assert neighbours and any(n['texts'] for n in neighbours)
    anyio.run(run)


def test_research_transport_no_longer_imports_legacy_service():
    import subprocess
    result = subprocess.run([sys.executable, '-c',
        "import sys; import genshin_lore.research_server; "
        "assert 'genshin_lore.mcp_server' not in sys.modules"],
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
