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
from .mcp_transport import _avoid_broken_anyio_worker_threads,_run_pipe_stdio
from .citation_handoff import CitationHandoff
from .dialogue.presentation import check_detail, search_reading, page_groups

READONLY=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False)

def create_server(service:ResearchQuery)->MCPServer:
 handoff=getattr(service,'citation_handoff',None) or CitationHandoff()
 server=MCPServer(name="genshin-lore-research",version="1.0.0",instructions="Frozen evidence research profile; no latest/default run selection. Search literal subject keywords, not whole questions. Use reading_view body previews/actions before repeating name searches; previews are bounded candidates and require read/inspect. For dialogue investigations use read_evidence view='dialogue'; default detail='reading' delivers bodies, title indexes, speaker labels and neighbour locators. Use detail='full' for full surrounding bodies/provenance. Follow supplied cursors with the same anchor/limit/locale/detail. Source-role labels are not proof of a unique story actor; branch connections do not prove playback order. Keep source facts, character statements, interpretation and prediction separate. Missing titles are not permission to invent a scene. Cite inspected body handles, not name/root handles, near supported claims. Display readable titles/speakers and citation_number; use the full supplied scoped handle for tool inputs and citations. Source links open files, not exact observations. Handles expire with this connection; inspect restores exact refs. Native client rendering is unchanged; persistent receipt rendering requires the explicit project delivery path. Stop when evidence answers the question; aim for <=20 calls for ordinary tasks and explain further investigation. Do not repeat equivalent searches or inspect the same material needlessly.")
 def wrap(fn,*a,**kw):
  try:
   detail=kw.pop('_detail','full');check_detail(detail)
   args=list(a)
   if handoff and fn.__name__ in ('read','follow','inspect'):args[1]=handoff.resolve(args[1])['evidence_ref']
   out={"status":"ok","corpus_ref":service.corpus_ref,"data":fn(*args,**kw)}
   if fn.__name__=='search' and detail=='reading':
    out['data']={'reading_view':search_reading(service,out['data'],args[1],args[2],filters=bool(args[3] or args[4] or args[9])),**out['data']}
   if fn.__name__=='read' and out['data'].get('view')=='dialogue' and out['data'].get('detail')=='reading':
    out['data']['display_groups']=page_groups(out['data']['nodes'])
   if handoff:out=handoff.attach(out,fn.__name__,inspected_ref=args[1] if fn.__name__=='inspect' else None,
      compact=fn.__name__=='read' and out['data'].get('detail')=='reading')
  except ResearchNotFound as e:out={"status":"not_found","corpus_ref":service.corpus_ref,"error":str(e)}
  except ResearchUnavailable as e:out={"status":"unavailable","corpus_ref":service.corpus_ref,"partial":True,"error":str(e)}
  except (ValueError,KeyError) as e:out={"status":"invalid_request","corpus_ref":service.corpus_ref,"error":str(e)}
  if out['status']!='ok':
   out['recovery']={'corpus_ref':service.corpus_ref,'discovery_tool':'describe_corpus',
                    'guidance':'Use returned refs/cursors; dialogue limit is 1..20, locale CHS/EN/JP. Inspect a supplied handle for its exact identity; never guess refs.'}
  if len(json.dumps(out,ensure_ascii=False,separators=(",",":" )).encode())>=262144:return {"status":"response_too_large","corpus_ref":service.corpus_ref,"error":"reduce limit or context window"}
  return out
 @server.tool(name="describe_corpus",description="Describe this connection's frozen corpus. Omit corpus_ref to discover its valid value; this never selects a different dataset.",annotations=READONLY)
 def describe_corpus(corpus_ref:str='',detail:str|None=None)->dict[str,Any]:return wrap(service.describe,corpus_ref or service.corpus_ref,detail)
 @server.tool(name="search_evidence",description="Literal subject-keyword search by default; lexical/semantic/hybrid remain explicit. Default detail='reading' adds bounded body previews and source-bound next actions ahead of separate name matches; raw items/order/cursor are unchanged. Filters apply to original hits; filtered requests do not auto-expand previews beyond scope. detail='full' omits the reading panel.",annotations=READONLY)
 def search_evidence(corpus_ref:str,query:str,locale:str|None=None,families:list[str]|None=None,source_prefix:str|None=None,limit:int=20,cursor:str|None=None,mode:str="literal",domain:str|None=None,story_release:str|None=None,detail:str='reading')->dict[str,Any]:return wrap(service.search,corpus_ref,query,locale,families,source_prefix,limit,cursor,mode,domain,story_release,_detail=detail)
 @server.tool(name="read_evidence",description="Read an exact ref or this connection's scoped handle. Physical view keeps existing same-container before/after. view='dialogue' pages NPC/name speech candidates or source-connected Dialog/Talk nodes. Default detail='reading' compacts repeated provenance and returns scoped body/title/context handles; source_titles and title_indexes share titles, display_groups group equal text without merging identities. detail='full' retains exact refs and full context. page_info states actual nodes/text entries/byte limit/stop reason. limit requests main nodes, not neighbour/translation entries. Reuse cursor with identical anchor/limit/locale/detail. Scope exhaustion/EOF never means all dialogue or corpus complete. Inspect body handles before citation; full provenance is always available through inspect.",annotations=READONLY)
 def read_evidence(corpus_ref:str,evidence_ref:str,before:int=0,after:int=0,limit:int=20,cursor:str|None=None,view:str='physical',locale:str='CHS',detail:str='reading')->dict[str,Any]:return wrap(service.read,corpus_ref,evidence_ref,before,after,limit,cursor,view=view,locale=locale,detail=detail,_detail=detail)
 @server.tool(name="follow_evidence",description="Follow only source-authored evidence relations. For Dialog/Talk, relation_kind='dialogue_predecessor' finds checked local incoming links to read earlier context; it is not narrative chronology.",annotations=READONLY)
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
