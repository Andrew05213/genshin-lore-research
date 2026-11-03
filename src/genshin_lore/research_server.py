"""Separate five-tool MCP research profile. Legacy mcp_server remains intact."""
from __future__ import annotations
import argparse
import anyio
import json
from pathlib import Path
from typing import Any
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from .research_query import ResearchNotFound,ResearchQuery,ResearchUnavailable
from .mcp_transport import _avoid_broken_anyio_worker_threads,_run_pipe_stdio
from .citation_handoff import CitationHandoff
from .dialogue.presentation import check_detail, search_reading, page_groups, trim_search_reading, corpus_overview

READONLY=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False)

def create_server(service:ResearchQuery, *, source_cards=None)->MCPServer:
 handoff=getattr(service,'citation_handoff',None) or CitationHandoff()
 if source_cards is not None:handoff.source_cards=Path(source_cards)
 server=MCPServer(name="genshin-lore-research",version="1.0.0",instructions="Frozen evidence research profile; no latest/default run selection. Search literal subject keywords, not whole questions. Use reading_view body previews/actions before repeating name searches; previews are bounded candidates and require read/inspect. For dialogue investigations use read_evidence view='dialogue'; default detail='reading' delivers bodies, title indexes, speaker labels and neighbour locators. Use detail='full' for full surrounding bodies/provenance. Follow supplied cursors with the same anchor/limit/locale/detail. Source-role labels are not proof of a unique story actor; branch connections do not prove playback order. Keep source facts, character statements, interpretation and prediction separate. Missing titles are not permission to invent a scene. Cite inspected body handles, not name/root handles, near supported claims. Display readable titles/speakers and citation_number; use the full supplied scoped handle for tool inputs and citations. Source links open files, not exact observations. Handles expire with this connection; inspect restores exact refs. When source_card_delivery is available, inspect selected read handles with save_source=true and place the returned real source-card links near supported claims. Cards preserve selected material after restart; never relabel an expired handle. Native answer rendering remains unchanged; automatic handle expansion still requires the explicit project renderer. Stop when evidence answers the question; aim for <=20 calls for ordinary tasks and explain further investigation. Do not repeat equivalent searches or inspect the same material needlessly. Prefer follow detail='reading' for navigation and evidence_refs batches for final inspections; use full when technical provenance is needed. Supplied locator handles are not read body evidence.")
 def wrap(fn,*a,**kw):
  try:
   detail=kw.pop('_detail','full');check_detail(detail)
   save_source=kw.pop('_save_source',False);describe_request=kw.pop('_describe',False)
   args=list(a)
   requested_ref=args[1] if len(args)>1 else None
   if handoff and fn.__name__ in ('read','follow','inspect'):args[1]=handoff.resolve(args[1])['evidence_ref']
   out={"status":"ok","corpus_ref":service.corpus_ref,"data":fn(*args,**kw)}
   if describe_request:
    if detail=='reading':out['data']=corpus_overview(out['data'])
    out['data']['source_card_delivery']={'available':handoff.source_cards is not None,'usage':'inspect_evidence with evidence_ref or evidence_refs (1..32 read handles) and save_source=true; one readable card plus private exact metadata; no native renderer interception'}
   if save_source:
    try:out['data']['source_card']=handoff.save_source(requested_ref,out['data'],service.corpus_ref)
    except OSError as e:raise ResearchUnavailable('local source card I/O failed: '+str(e)) from e
   if fn.__name__=='inspect' and detail=='reading':
    out['data']=inspection_reading(out['data'])
   if fn.__name__=='search' and detail=='reading':
    out['data']={'reading_view':search_reading(service,out['data'],args[1],args[2],filters=bool(args[3] or args[4] or args[8] or args[9]) or out['data'].get('domain_filter') is not None),**out['data']}
   if fn.__name__=='read' and out['data'].get('view')=='dialogue' and out['data'].get('detail')=='reading':
    out['data']['display_groups']=page_groups(out['data']['nodes'])
   if handoff:
    while True:
     delivered=handoff.attach(out,fn.__name__,inspected_ref=args[1] if fn.__name__=='inspect' else None,
        compact=fn.__name__ in ('search','read','follow','inspect') and detail=='reading',
        read_arguments={'corpus_ref':args[0],'before':args[2],'after':args[3],
                        'limit':args[4],'view':kw.get('view','physical'),
                        'locale':kw.get('locale','CHS'),'detail':detail} if fn.__name__=='read' else None,
        max_bytes=262144 if fn.__name__=='search' else None)
     if delivered is not None:
      out=delivered;break
     if fn.__name__!='search' or not trim_search_reading(out['data']):
      # Optional handoff/display metadata cannot invalidate an otherwise valid
      # exact-ref result. The original envelope still passes the size guard below.
      break
  except ResearchNotFound as e:out={"status":"not_found","corpus_ref":service.corpus_ref,"error":str(e)}
  except ResearchUnavailable as e:out={"status":"unavailable","corpus_ref":service.corpus_ref,"partial":True,"error":str(e)}
  except (ValueError,KeyError) as e:out={"status":"invalid_request","corpus_ref":service.corpus_ref,"error":str(e)}
  if out['status']!='ok':
   out['recovery']={'corpus_ref':service.corpus_ref,'discovery_tool':'describe_corpus',
                    'guidance':'Use returned refs/cursors; dialogue limit is 1..20, locale CHS/EN/JP. Inspect a supplied handle for its exact identity; never guess refs.'}
  if len(json.dumps(out,ensure_ascii=False,separators=(",",":" )).encode())>=262144:return {"status":"response_too_large","corpus_ref":service.corpus_ref,"error":"reduce limit or context window"}
  return out
 @server.tool(name="describe_corpus",description="Compact discovery by default: frozen corpus, available modes, limitations and reading entry points. detail='full' returns complete component/coverage metadata. Omit corpus_ref to discover this connection's value; never selects a different dataset.",annotations=READONLY)
 def describe_corpus(corpus_ref:str='',detail:str|None='reading')->dict[str,Any]:
  return wrap(service.describe,corpus_ref or service.corpus_ref,detail,_detail=detail or 'reading',_describe=True)
 @server.tool(name="search_evidence",description="Literal subject-keyword search by default; body-first-v1 prioritizes exact declared titles and substantive text before name slots, deferring duplicate text without merging occurrences. Old-order cursors require a fresh search. lexical/semantic/hybrid remain explicit. Default detail='reading' adds bounded previews, source-title groups and executable reading/navigation actions without changing the returned hit page. Filtered requests do not auto-expand beyond scope. Repeated locators use scoped handles; detail='full' omits the panel and restores technical fields.",annotations=READONLY)
 async def search_evidence(corpus_ref:str,query:str,locale:str|None=None,families:list[str]|None=None,source_prefix:str|None=None,limit:int=20,cursor:str|None=None,mode:str="literal",domain:str|None=None,story_release:str|None=None,detail:str='reading',ctx:Context=None)->dict[str,Any]:
  await ctx.report_progress(0,1,'正在检索关键词与正文候选')
  result=wrap(service.search,corpus_ref,query,locale,families,source_prefix,limit,cursor,mode,domain,story_release,_detail=detail)
  await ctx.report_progress(1,1,'检索结束；结果状态见响应')
  return result
 @server.tool(name="read_evidence",description="Read an exact ref or scoped handle. Physical view keeps same-container before/after. view='dialogue' pages source-labelled NPC speech or connected Dialog/Talk nodes. Default detail='reading' delivers bodies, titles, necessary source-role labels, proof limitations and cursors; full provenance is available with detail='full' or inspect. reading_actions gives next-page arguments. context_action reads Codex same-section neighbours or Dialog/Talk anchor plus bounded successors; predecessor_action follows checked Dialog/Talk incoming links, then read a supplied target. page_info states actual nodes/text entries/byte limit/stop reason. limit counts main nodes, not translations or neighbours. Scope EOF never means all dialogue/corpus complete. Inspect selected body handles, optionally save several in one card.",annotations=READONLY)
 async def read_evidence(corpus_ref:str,evidence_ref:str,before:int=0,after:int=0,limit:int=20,cursor:str|None=None,view:str='physical',locale:str='CHS',detail:str='reading',ctx:Context=None)->dict[str,Any]:
  await ctx.report_progress(0,1,'正在读取正文及来源上下文')
  result=wrap(service.read,corpus_ref,evidence_ref,before,after,limit,cursor,view=view,locale=locale,detail=detail,_detail=detail)
  await ctx.report_progress(1,1,'读取结束；续读与范围见响应')
  return result
 @server.tool(name="follow_evidence",description="Follow only source-authored evidence relations. detail='reading' uses scoped handles for repeated locators; default full preserves technical records. Read supplied target handles for bodies. For Dialog/Talk, relation_kind='dialogue_predecessor' finds checked local incoming links; it is not narrative chronology.",annotations=READONLY)
 def follow_evidence(corpus_ref:str,evidence_ref:str,relation_kind:str|None=None,limit:int=20,cursor:str|None=None,resolve_targets:bool=True,detail:str='full')->dict[str,Any]:return wrap(service.follow,corpus_ref,evidence_ref,relation_kind,limit,cursor,resolve_targets,_detail=detail)
 @server.tool(name="inspect_evidence",description="Inspect one evidence_ref (compatible full provenance by default), or evidence_refs (1..32 distinct supplied references, individual outcomes). save_source=true requires actually-read body handles and saves ONE readable title/body card plus private exact metadata. Batch order is selection order, not narrative chronology; no identity merge or claim checking. Failed selections never produce a successful collection card. Save/batch defaults to a compact reading response; detail='full' expands technical provenance. Unknown/expired handles fail, never guess. Return the real card link near supported claims.",annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=True,openWorldHint=False))
 async def inspect_evidence(corpus_ref:str,evidence_ref:str='',save_source:bool=False,evidence_refs:list[str]|None=None,detail:str|None=None,ctx:Context=None)->dict[str,Any]:
  if detail is None:detail='reading' if save_source or evidence_refs is not None else 'full'
  if evidence_refs is None:
   return wrap(service.inspect,corpus_ref,evidence_ref,_save_source=save_source,_detail=detail)
  if evidence_ref or not 1<=len(evidence_refs)<=32 or len(set(evidence_refs))!=len(evidence_refs):
   return {'status':'invalid_request','corpus_ref':service.corpus_ref,'error':'use either evidence_ref or 1..32 distinct evidence_refs; never both'}
  try:check_detail(detail)
  except ValueError as e:return {'status':'invalid_request','error':str(e)}
  results=[];selected=[];inspected=[]
  for i,ref in enumerate(evidence_refs):
   await ctx.report_progress(i,len(evidence_refs),'正在核验所选正文 '+str(i+1)+'/'+str(len(evidence_refs)))
   try:
    exact=handoff.resolve(ref)['evidence_ref']
    value=service.inspect(corpus_ref,exact)
    inspected.append(exact);selected.append((ref,value))
    results.append({'selected_reference':ref,'status':'ok','data':value if detail=='full' else inspection_reading(value)})
   except (ValueError,KeyError,ResearchNotFound,ResearchUnavailable) as e:
    results.append({'selected_reference':ref,'status':'failed','error':str(e)})
  out={'status':'ok','corpus_ref':service.corpus_ref,'data':{'results':results,'partial':len(selected)!=len(evidence_refs)}}
  if len(json.dumps(out,ensure_ascii=False).encode())>=230000:
   return {'status':'response_too_large','error':'reduce batch size or use detail=reading; no card saved'}
  if save_source:
   try:
    if out['data']['partial']:raise ValueError('source card not saved: one or more selected references failed inspection')
    out['data']['source_card']=handoff.save_sources(selected,service.corpus_ref)
   except (ValueError,OSError) as e:
    out['data']['partial']=True
    out['data']['source_card']={'status':'NOT_SAVED','error':str(e)}
  # Bound the response before persisting a handoff receipt. Saved metadata
  # remains private; a full inspection can be requested in smaller batches.
  delivered=handoff.attach(out,'inspect',inspected_refs=inspected,compact=detail=='reading',max_bytes=262144)
  await ctx.report_progress(len(evidence_refs),len(evidence_refs),'核验结束；各项状态见响应')
  return delivered or {'status':'response_too_large','error':'reduce batch size or use detail=reading'}
 return server


def inspection_reading(value):
 """No body duplication; complete inspect remains explicitly available."""
 return {k:value[k] for k in ('evidence_ref','text_proof','terminal_unresolved',
         'relation_resolution','limitations','source_card') if k in value}

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
