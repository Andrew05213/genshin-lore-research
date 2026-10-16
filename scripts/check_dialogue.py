"""Verify dialogue reading through the actual five-tool stdio MCP surface."""
import argparse
import json
import os
from pathlib import Path
import sys

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]
from scripts.check import payload


async def check(config, released_runtime_root=None):
    profile = json.loads(Path(json.loads(config.read_text())['profile']).read_text())
    corpus = profile['corpus_ref']
    args = [str(ROOT/'scripts/serve_dialogue.py'), '--config', str(config)]
    if released_runtime_root:
        args += ['--released-runtime-root', str(released_runtime_root)]
    server = StdioServerParameters(command=sys.executable,
        args=args,
        env={**os.environ, 'PYTHONPATH':str(ROOT/'src')})
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            names = [t.name for t in (await client.list_tools()).tools]
            if names != ['describe_corpus','search_evidence','read_evidence','follow_evidence','inspect_evidence']:
                raise ValueError('five-tool surface changed')
            async def call(name, args):
                value = payload(await client.call_tool(name, args))
                if value['status'] != 'ok':
                    raise ValueError('MCP rejection: '+str(value))
                return value['data']
            await call('describe_corpus', {'corpus_ref':corpus})
            search = await call('search_evidence', {'corpus_ref':corpus, 'query':'向着星辰',
                                'locale':'CHS','families':['dialog_archive'],'limit':1})
            ref = search['items'][0]['evidence_ref']
            args = {'corpus_ref':corpus,'evidence_ref':ref,'view':'dialogue','limit':1}
            first = await call('read_evidence', args)
            second = await call('read_evidence', {**args,'cursor':first['next_cursor']})
            if first['nodes'][0]['node'] == second['nodes'][0]['node']:
                raise ValueError('MCP pagination repeated an exact node')
            for page in (first, second):
                for node in page['nodes']:
                    for text in node['texts']:
                        ordinary = await call('read_evidence', {'corpus_ref':corpus,'evidence_ref':text['evidence_ref']})
                        inspected = await call('inspect_evidence', {'corpus_ref':corpus,'evidence_ref':text['evidence_ref']})
                        if ordinary['raw_text'] != text['raw_text'] or inspected['text_provenance'] != text['provenance']:
                            raise ValueError('MCP read/inspect differs from dialogue view')
            await call('follow_evidence', {'corpus_ref':corpus,'evidence_ref':ref,
                       'relation_kind':'dialogue_predecessor','limit':2})
            invalid = payload(await client.call_tool('read_evidence', {**args,'limit':2,'cursor':first['next_cursor']}))
            if invalid['status'] != 'invalid_request':
                raise ValueError('MCP accepted a cursor with the wrong page limit')
            return {'status':'PASS_DIALOGUE_MCP', 'tools':names, 'pages':2,
                    'body_and_provenance_equivalence':True,'cursor_mismatch_rejected':True,
                    'reading_extension_release_accepted':False}


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--config',type=Path,required=True)
    p.add_argument('--released-runtime-root',type=Path)
    a=p.parse_args();print(json.dumps(anyio.run(check,a.config,a.released_runtime_root),ensure_ascii=False))
