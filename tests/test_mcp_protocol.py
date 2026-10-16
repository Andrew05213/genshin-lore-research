"""Exercise actual stdio, SDK registration and cursor/citation boundaries."""
import json
import os
from pathlib import Path
import sys

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest

from scripts.check import payload

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
                    page = await call('read_evidence', evidence_ref=ref, view='dialogue', limit=1)
                    assert page['status'] == 'ok'
                    data = page['data']
                    assert data['next_cursor']
                    handle = data['nodes'][0]['texts'][0]['citation_handle']
                    inspected = await call('inspect_evidence', evidence_ref=handle)
                    assert inspected['status'] == 'ok'
                    assert inspected['data']['evidence_ref'] == data['nodes'][0]['texts'][0]['evidence_ref']
                    continued = await call('read_evidence', evidence_ref=ref, view='dialogue',
                                           limit=1, cursor=data['next_cursor'])
                    assert continued['status'] == 'ok'
                    assert continued['data']['nodes'][0]['node'] != data['nodes'][0]['node']
                    wrong = await call('read_evidence', evidence_ref=ref, view='dialogue',
                                       limit=2, cursor=data['next_cursor'])
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
