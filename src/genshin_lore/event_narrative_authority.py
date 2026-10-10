"""Fail-closed exact publication registration for Event narrative artifacts."""
import json
from copy import deepcopy
from pathlib import Path

IDENTITY_FIELDS=('family','family_contract_version','ingest_run_id','parser_build_id','artifact','runtime_manifest')

def publication_identity(component):
 return {field:deepcopy(component[field]) for field in IDENTITY_FIELDS}

def _same_or_conflict(rows,key,value,expected,label):
 matches=[row for row in rows if row.get(key)==value]
 if len(matches)>1:raise ValueError(label+' duplicate existing identity')
 if matches and matches[0]!=expected:raise ValueError(label+' conflicting existing identity')
 return bool(matches)

def register_exact_publication(bundle,aggregate,graph,component,node,runtime_row,space):
 """Return updated copies; exact replay is a no-op, every conflict fails first."""
 bundle=deepcopy(bundle);aggregate=deepcopy(aggregate);graph=deepcopy(graph)
 run=component['ingest_run_id'];manifest=component['runtime_manifest'];identity=publication_identity(component)
 components=bundle['additional_family_components']
 same_run=[x for x in components if x.get('family')==component['family'] and x.get('family_contract_version')==component['family_contract_version'] and x.get('ingest_run_id')==run]
 if len(same_run)>1:raise ValueError('bundle duplicate exact run')
 if same_run and same_run[0]!=component:raise ValueError('bundle same run conflicting publication')
 for existing in components:
  if existing.get('runtime_manifest')==manifest and existing.get('ingest_run_id')!=run:raise ValueError('bundle runtime manifest reused by different run')
 bundle_new=not same_run

 entries=[x for x in aggregate['standalone_family_contracts'] if x.get('family')==component['family'] and x.get('family_contract_version')==component['family_contract_version']]
 if len(entries)!=1:raise ValueError('aggregate family contract entry gate')
 exact_runs=entries[0]['exact_runs'];same=[x for x in exact_runs if x.get('ingest_run_id')==run]
 if len(same)>1:raise ValueError('aggregate duplicate exact run')
 if same and publication_identity(same[0])!=identity:raise ValueError('aggregate same run conflicting publication')
 for existing in exact_runs:
  if existing.get('runtime_manifest')==manifest and existing.get('ingest_run_id')!=run:raise ValueError('aggregate runtime manifest reused by different run')
 aggregate_new=not same

 nodes=[x for x in graph['nodes'] if x.get('id')==node['id']]
 if len(nodes)>1:raise ValueError('dependency duplicate node id')
 if nodes and nodes[0]!=node:raise ValueError('dependency node id conflict')
 runtime=[x for x in graph['runtime_artifacts'] if x.get('role')==runtime_row['role']]
 if len(runtime)>1:raise ValueError('dependency duplicate runtime role')
 if runtime and runtime[0]!=runtime_row:raise ValueError('dependency runtime artifact conflict')
 edges=[x for x in graph['edges'] if x.get('from')=='product:current' and x.get('to')==node['id'] and x.get('kind')=='requires_exact_artifact']
 if len(edges)>1:raise ValueError('dependency duplicate current edge')
 graph_new=not nodes
 if bool(runtime)!=bool(nodes) or bool(edges)!=bool(nodes):raise ValueError('dependency publication registration incomplete')
 if bundle_new!=aggregate_new or bundle_new!=graph_new:raise ValueError('publication authorities disagree on registration state')

 if bundle_new:
  components.append(deepcopy(component));exact_runs.append(deepcopy(component));graph['nodes'].append(deepcopy(node));graph['runtime_artifacts'].append(deepcopy(runtime_row));graph['edges'].append({'from':'product:current','to':node['id'],'kind':'requires_exact_artifact'});graph['classifications']['A_current_runtime_required']['artifact_files']+=1
  for key in ('current_product_minimum_runtime_set','current_product_full_advertised_capability_set','current_product_plus_verification_set'):
   graph['working_sets'][key]['allocated_bytes']+=runtime_row['allocated_bytes'];graph['working_sets'][key]['logical_bytes']+=runtime_row['logical_bytes'];graph['working_sets'][key]['file_count']+=1
  graph['rules']['required_runtime_artifacts']=graph['classifications']['A_current_runtime_required']['artifact_files']
  graph['working_sets']['full_historical_local_project_set']={'allocated_bytes':space['broad_allocated_bytes'],'logical_bytes':space['broad_logical_bytes']}
  graph['working_sets']['historical_retained_remainder_allocated_bytes']=space['broad_allocated_bytes']-graph['working_sets']['current_product_plus_verification_set']['allocated_bytes']
 aggregate['aggregate_family_contract_count']=len(aggregate['standalone_family_contracts'])+aggregate['core']['family_contract_count']
 return bundle,aggregate,graph,{'registered':bundle_new,'idempotent_replay':not bundle_new,'publication_identity':identity}

def _load(root,path):
 return json.loads((Path(root)/path).read_text())

def preflight_current_publication(root,component,node,runtime_row):
 """Return an exact-replay receipt before writes, or None for a new publication."""
 root=Path(root)
 current=_load(root,'manifests/product/current.json')
 bundle_path=current['explicit_product_bundle']
 bundle=_load(root,bundle_path)
 aggregate=_load(root,'reports/coverage/current-product-coverage.json')
 graph=_load(root,'reports/storage-retention/current-product-dependencies.json')
 full=graph['working_sets']['full_historical_local_project_set']
 space={'broad_allocated_bytes':full['allocated_bytes'],'broad_logical_bytes':full['logical_bytes']}
 candidate_bundle,candidate_aggregate,candidate_graph,result=register_exact_publication(bundle,aggregate,graph,component,node,runtime_row,space)
 if result['registered']:
  return None
 if candidate_bundle!=bundle or candidate_aggregate!=aggregate or candidate_graph!=graph:
  raise ValueError('exact publication replay would mutate current authority')
 required=graph['classifications']['A_current_runtime_required']['artifact_files']
 if graph['rules']['required_runtime_artifacts']!=required:
  raise ValueError('current dependency working-set classification mismatch')
 runtime_allocated=sum(row['allocated_bytes'] for row in graph['runtime_artifacts'])
 runtime_logical=sum(row['logical_bytes'] for row in graph['runtime_artifacts'])
 minimum=graph['working_sets']['current_product_minimum_runtime_set']
 if minimum['allocated_bytes']<runtime_row['allocated_bytes'] or minimum['logical_bytes']<runtime_row['logical_bytes']:
  raise ValueError('current minimum runtime set is missing exact publication artifact')
 for key in ('current_product_full_advertised_capability_set','current_product_plus_verification_set'):
  item=graph['working_sets'][key]
  if item['allocated_bytes']<runtime_allocated or item['logical_bytes']<runtime_logical or item['file_count']<len(graph['runtime_artifacts']):
   raise ValueError('current dependency working-set is missing registered artifacts')
 verification=graph['working_sets']['current_product_plus_verification_set']['allocated_bytes']
 if graph['working_sets']['historical_retained_remainder_allocated_bytes']!=full['allocated_bytes']-verification:
  raise ValueError('current dependency retained remainder mismatch')
 return {'status':'pass','idempotent_replay':True,'writes':0,'current_bundle':bundle_path,'publication_identity':result['publication_identity']}
