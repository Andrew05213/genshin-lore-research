"""Separate five-tool MCP research profile. Legacy mcp_server remains intact."""
from __future__ import annotations
import argparse
import anyio
import json
from pathlib import Path
from typing import Any
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from .research_query import ResearchNotFound,ResearchQuery,ResearchUnavailable
from .mcp_server import _avoid_broken_anyio_worker_threads,_run_pipe_stdio

READONLY=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False)

def create_server(service:ResearchQuery)->MCPServer:
 handoff=getattr(service,'citation_handoff',None)
 server=MCPServer(name="genshin-lore-research",version="1.0.0",instructions="Frozen evidence research profile; no latest/default run selection." + (" Opt-in citation transport: use supplied [H...:R...:E...] handles in evidence_ref inputs and near final claims; inspect final material. Program expands exact refs after capture, not claims. Handles expire with this connection." if handoff else ""))
 def wrap(fn,*a,**kw):
  try:
   args=list(a)
   if handoff and fn.__name__ in ('read','follow','inspect'):args[1]=handoff.resolve(args[1])['evidence_ref']
   out={"status":"ok","corpus_ref":service.corpus_ref,"data":fn(*args,**kw)}
   if handoff:out=handoff.attach(out,fn.__name__,inspected_ref=args[1] if fn.__name__=='inspect' else None)
  except ResearchNotFound as e:out={"status":"not_found","corpus_ref":service.corpus_ref,"error":str(e)}
  except ResearchUnavailable as e:out={"status":"unavailable","corpus_ref":service.corpus_ref,"partial":True,"error":str(e)}
  except (ValueError,KeyError) as e:out={"status":"invalid_request","corpus_ref":service.corpus_ref,"error":str(e)}
  if len(json.dumps(out,ensure_ascii=False,separators=(",",":" )).encode())>=262144:return {"status":"response_too_large","corpus_ref":service.corpus_ref,"error":"reduce limit or context window"}
  return out
 @server.tool(name="describe_corpus",description="Compact description of one frozen research corpus.",annotations=READONLY)
 def describe_corpus(corpus_ref:str,detail:str|None=None)->dict[str,Any]:return wrap(service.describe,corpus_ref,detail)
 @server.tool(name="search_evidence",description="Literal search by default; optional corpus-backed lexical, semantic, or sparse-first hybrid candidate discovery with exact evidence recheck.",annotations=READONLY)
 def search_evidence(corpus_ref:str,query:str,locale:str|None=None,families:list[str]|None=None,source_prefix:str|None=None,limit:int=20,cursor:str|None=None,mode:str="literal",domain:str|None=None,story_release:str|None=None)->dict[str,Any]:return wrap(service.search,corpus_ref,query,locale,families,source_prefix,limit,cursor,mode,domain,story_release)
 @server.tool(name="read_evidence",description="Read exact evidence and bounded same-container context.",annotations=READONLY)
 def read_evidence(corpus_ref:str,evidence_ref:str,before:int=0,after:int=0,limit:int=20,cursor:str|None=None)->dict[str,Any]:return wrap(service.read,corpus_ref,evidence_ref,before,after,limit,cursor)
 @server.tool(name="follow_evidence",description="Follow only stored source-authored evidence relations.",annotations=READONLY)
 def follow_evidence(corpus_ref:str,evidence_ref:str,relation_kind:str|None=None,limit:int=20,cursor:str|None=None,resolve_targets:bool=True)->dict[str,Any]:return wrap(service.follow,corpus_ref,evidence_ref,relation_kind,limit,cursor,resolve_targets)
 @server.tool(name="inspect_evidence",description="Return citation-grade physical provenance for exact evidence.",annotations=READONLY)
 def inspect_evidence(corpus_ref:str,evidence_ref:str)->dict[str,Any]:return wrap(service.inspect,corpus_ref,evidence_ref)
 return server

def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument("--corpus",type=Path,required=True);p.add_argument("--profile",type=Path,required=True);p.add_argument("--bindings",type=Path,required=True);p.add_argument("--index",type=Path,required=True);p.add_argument("--prepublication-profile",action="store_true",help=argparse.SUPPRESS);a=p.parse_args(argv)
 published_path=Path(__file__).resolve().parents[2]/'manifests/research/research71-bounded-profile-v1.json'
 if json.loads(a.profile.read_text()).get('successor_contract')=='research71-discovery-navigation-v1.1':
  from .research71_successor import SuccessorResearch71
  q=SuccessorResearch71(a.corpus,a.bindings,a.index,a.profile)
 elif a.profile.resolve()==published_path.resolve() or json.loads(a.profile.read_text()).get('release_id')=='research71-bounded-v1':
  if a.prepublication_profile:p.error('published release forbids prepublication override')
  from .research71_release import ReleasedResearch71
  q=ReleasedResearch71(a.corpus,a.bindings,a.index,a.profile)
 else:q=ResearchQuery(a.corpus,a.bindings,a.index,a.profile,allow_prepublication_profile=a.prepublication_profile)
 try:
  server=create_server(q);_avoid_broken_anyio_worker_threads(server);anyio.run(_run_pipe_stdio,server)
 finally:q.close()
if __name__=="__main__":main()
