"""Serve a locally rebuilt, source-verified corpus; not a copied release receipt."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]


def open_runtime(config):
    from genshin_lore.research71_unified import Research71Unified
    from scripts.runtime_config import load_runtime_paths
    return Research71Unified(**load_runtime_paths(config),released=False)


def smoke(config):
    runtime=open_runtime(config);q=runtime.query;result=[]
    try:
        for keyword in ('胡桃','凯瑟琳','吞金和蓄财','终北祷歌','连线被弄乱了'):
            page=q.search(q.corpus_ref,keyword,locale='CHS',limit=3)
            hits=page['items']
            if not hits:raise ValueError('rebuilt literal smoke returned no evidence: '+keyword)
            ref=hits[0]['evidence_ref']
            read=q.read(q.corpus_ref,ref);inspect=q.inspect(q.corpus_ref,ref)
            follow=q.follow(q.corpus_ref,ref,limit=3)
            if inspect['evidence_ref']!=ref:raise ValueError('inspect reference binding mismatch')
            result.append({'keyword':keyword,'hits':len(hits),'evidence_ref':ref,
                'read_completed':True,'inspect_completed':True,'follow_completed':True})
        # Family-specific views retain the same source/run identities and may
        # expose continuation without using a legacy provider.
        for keyword,capability in (('胡桃','character_avatar'),('终北祷歌','readable_archive')):
            page=runtime.search(keyword,capability,limit=3)
            if not page['results']:raise ValueError('unified view missing: '+capability)
            for item in page['results'][:1]:
                ref=item['evidence_ref']
                runtime.read(ref);runtime.inspect(ref)
                if capability=='character_avatar':runtime.follow(ref,limit=3)
        return {'status':'PASS_LOCAL_REBUILT_RUNTIME','checks':result,'formal_release_accepted':False,
            'semantic_overlay_built':getattr(q,'semantic',None) is not None,'legacy_fallback':False}
    finally:runtime.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--check',action='store_true')
    p.add_argument('--surface',choices=('research','narrative'),default='research')
    a=p.parse_args()
    if a.check:
        print(json.dumps(smoke(a.config),ensure_ascii=False));return
    import anyio
    from genshin_lore.mcp_transport import _avoid_broken_anyio_worker_threads,_run_pipe_stdio
    runtime=open_runtime(a.config)
    try:
        if a.surface=='research':
            from genshin_lore.research_server import create_server
            server=create_server(runtime.query)
        else:
            from genshin_lore.narrative_assistant_7_1 import NarrativeAssistant71
            from genshin_lore.research71_preview_server import create_server
            server=create_server(NarrativeAssistant71(None,runtime=runtime))
        _avoid_broken_anyio_worker_threads(server);anyio.run(_run_pipe_stdio,server)
    finally:runtime.close()


if __name__=='__main__':main()
