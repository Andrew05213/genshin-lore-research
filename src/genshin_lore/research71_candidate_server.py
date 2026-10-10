"""Explicit opt-in candidate, reusing the unchanged five-tool transport."""
import argparse
from pathlib import Path

import anyio

from .research71_candidate_query import CandidateResearch71
from .research_server import create_server
from .mcp_server import _avoid_broken_anyio_worker_threads, _run_pipe_stdio


def main():
    p=argparse.ArgumentParser()
    for name in ('corpus','profile','bindings','index'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--candidate',action='store_true',required=True,
                   help='Explicit prepublication 7.1 candidate; never current production')
    a=p.parse_args()
    q=CandidateResearch71(a.corpus,a.bindings,a.index,a.profile,allow_prepublication_profile=True)
    try:
        server=create_server(q)
        _avoid_broken_anyio_worker_threads(server)
        anyio.run(_run_pipe_stdio,server)
    finally:q.close()


if __name__=='__main__':main()
