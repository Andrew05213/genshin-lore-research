"""Transport and continuation smoke over a freshly rebuilt stdio service."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

import anyio
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]


def payload(result):
    if result.is_error:raise ValueError('MCP call failed')
    if result.structured_content:return result.structured_content
    return json.loads(next(b.text for b in result.content if b.type=='text'))


async def check(config):
    from scripts.runtime_config import load_runtime_paths
    config=Path(config).resolve()
    values=load_runtime_paths(config)
    corpus=json.loads(Path(values['profile']).read_text())['corpus_ref']
    results={}
    for surface in ('research','narrative'):
        parameters=StdioServerParameters(command=sys.executable,args=[str(ROOT/'scripts/serve.py'),
            '--config',str(config),'--surface',surface],env={**os.environ,'PYTHONPATH':str(ROOT/'src')})
        async with stdio_client(parameters) as (read,write):
            async with ClientSession(read,write) as client:
                await client.initialize()
                names=[t.name for t in (await client.list_tools()).tools]
                expected=['describe_corpus','search_evidence','read_evidence','follow_evidence','inspect_evidence'] if surface=='research' else ['query_research71_preview','inspect_research71_preview']
                if names!=expected:raise ValueError('exact tool surface differs: '+surface)
                async def call(name,args):
                    value=payload(await client.call_tool(name,args))
                    if value['status']!='ok':raise ValueError('MCP operation rejected: '+name+' '+str(value.get('error')))
                    return value
                if surface=='research':
                    await call('describe_corpus',{'corpus_ref':corpus})
                    hit=await call('search_evidence',{'corpus_ref':corpus,'query':'胡桃','locale':'CHS','limit':3})
                    ref=hit['data']['items'][0]['evidence_ref']
                    for name in ('read_evidence','follow_evidence','inspect_evidence'):
                        await call(name,{'corpus_ref':corpus,'evidence_ref':ref})
                    results[surface]={'tools':names,'all_five_called':True}
                else:
                    for question,kind in (('7.1这本《终北祷歌·上》讲什么？','book'),('7.1胡桃的角色故事讲了什么？','story')):
                        response=await call('query_research71_preview',{'question':question})
                        key='book_continuations' if kind=='book' else 'text_continuations'
                        continuations=response.get(key,[])
                        if not continuations:raise ValueError('missing '+kind+' continuation')
                        token=continuations[0]['continuation'];pages=0;last=None;coverage=[]
                        while token is not None:
                            if pages>=40:raise ValueError('bounded smoke did not reach local EOF')
                            page=await call('query_research71_preview',{'continuation':token})
                            last=page['book_page' if kind=='book' else 'text_page'];token=last['continuation'];pages+=1
                            excerpt=page['task']['answer_context']['candidate_excerpts'][0]
                            args={'evidence_ref':excerpt['evidence_ref'],'observation_index':excerpt['observation_index']}
                            if kind=='story':args.update(text_start=last['start'],text_chars=last['end']-last['start'])
                            inspected=await call('inspect_research71_preview',args)
                            if kind=='story':
                                raw=inspected['observation']['text']
                                if raw!=excerpt['excerpt'] or hashlib.sha256(raw.encode()).hexdigest()!=last['segment_sha256']:
                                    raise ValueError('story range/inspect mismatch')
                                coverage.append({k:last[k] for k in ('start','end','total_chars','segment_sha256','full_text_sha256')})
                            else:
                                coverage.append({'lines':len(last['covered']),
                                    'first_ordinal':last['covered'][0]['ordinal'],
                                    'last_ordinal':last['covered'][-1]['ordinal'],
                                    'total_physical_lines':last['total_physical_lines']})
                        if not last or not last['file_end_reached' if kind=='book' else 'end_reached']:
                            raise ValueError('EOF not evidenced')
                        results[kind]={'continuation_pages':pages,'local_eof':True,
                            'inspection_completed':True,'actual_coverage':coverage}
                    results[surface]={'tools':names}
    return {'status':'PASS_REBUILT_MCP_TRANSPORT_AND_CONTINUATION','checks':results,
        'model_autonomous_acceptance':False,'formal_release_accepted':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);a=p.parse_args()
    print(json.dumps(anyio.run(check,a.config.resolve()),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
