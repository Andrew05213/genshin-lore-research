"""Release-owned exact tuple oracle; this module never imports the builder."""
from __future__ import annotations
from typing import Any,Mapping
from .util import canonical_json,digest_id
from .quest_codex_phase4f2_contract import occurrence_id

def exact_occurrence_tables(d:Mapping[str,Any],identity:Mapping[str,str])->dict[str,tuple[tuple[str,...],list[tuple[Any,...]]]]:
    run=identity["ingest_run_id"]; snap=identity["snapshot_id"]; parser=identity["parser_build_id"]; ident={"snapshot_id":snap,"parser_build_id":parser,"ingest_run_id":run}
    fids={x["relative_path"]:digest_id("sf",snap,x["relative_path"]) for x in d["files"]}; rids={(x["relative_path"],x["source_pointer"]):digest_id("sr",fids[x["relative_path"]],x["source_pointer"]) for x in d["source_records"]}
    rows={(x["table"],x["row_index"]):occurrence_id("row",ident,x["table"],x["row_index"],x["pointer"]) for x in d["rows"]}; row_records={(x["table"],x["row_index"]):rids[(x["relative_path"],x["pointer"])] for x in d["rows"]}
    rels={x["relationship_key"]:occurrence_id("relationship",ident,x["relationship_key"]) for x in d["relationships"]}; owners={x["owner_key"]:occurrence_id("text-owner",ident,x["owner_key"]) for x in d["text_owners"]}; resolutions={(x["owner_key"],x["locale"]):occurrence_id("text-resolution",ident,x["owner_key"],x["locale"]) for x in d["text_resolutions"]}
    out={}
    c=("id","ingest_run_id","snapshot_id","parser_build_id","source_file_id","source_record_id","source_pointer","evidence_kind","table_name","row_index","native_locator_value","raw_checksum","metadata_json"); v=[]
    for x in d["rows"]:
        k=(x["table"],x["row_index"]); v.append((rows[k],run,snap,parser,fids[x["relative_path"]],row_records[k],x["pointer"],x["evidence_kind"],x["table"],x["row_index"],x["native_locator_value"],x["raw_checksum"],canonical_json({"physical_occurrence":True,"logical_identity_created":False})))
    out["quest_codex_row_occurrence"]=(c,v)
    c=("id","ingest_run_id","snapshot_id","parser_build_id","row_occurrence_id","source_record_id","source_pointer","field_name","field_presence","raw_json","metadata_json"); v=[]
    for x in d["raw_fields"]:
        k=(x["table"],x["row_index"]); v.append((occurrence_id("raw-field",ident,*k,x["source_pointer"]),run,snap,parser,rows[k],row_records[k],x["source_pointer"],x["field_name"],x["field_presence"],None if x["raw_value"] is None else canonical_json(x["raw_value"]),canonical_json({"absence_is_evidence":x["field_presence"]=="missing","identity_effect":"none"})))
    out["quest_codex_raw_field_occurrence"]=(c,v)
    c=("id","ingest_run_id","snapshot_id","parser_build_id","source_row_occurrence_id","source_record_id","source_pointer","field_name","raw_value_json","target_table","target_field","resolution_status","metadata_json"); v=[]
    for x in d["relationships"]:
        k=(x["table"],x["row_index"]); v.append((rels[x["relationship_key"]],run,snap,parser,rows[k],row_records[k],x["source_pointer"],x["field_name"],canonical_json(x["raw_value"]),x["target_table"],x["target_field"],x["resolution_status"],canonical_json({"candidate_identity_effect":"none","missing_is_legal":True})))
    out["quest_codex_relationship_occurrence"]=(c,v)
    c=("id","ingest_run_id","snapshot_id","parser_build_id","relationship_occurrence_id","source_file_id","source_record_id","source_pointer","candidate_ordinal","target_table","target_field","target_raw_value_json","raw_checksum","metadata_json"); v=[]
    for x in d["relationship_candidates"]:
        oid=occurrence_id("relationship-candidate",ident,x["relationship_key"],x["candidate_ordinal"],x["source_relative_path"],x["source_pointer"]); v.append((oid,run,snap,parser,rels[x["relationship_key"]],fids[x["source_relative_path"]],rids[(x["source_relative_path"],x["source_pointer"])],x["source_pointer"],x["candidate_ordinal"],x["target_table"],x["target_field"],canonical_json(x["target_raw_value"]),x["raw_checksum"],canonical_json({"physical_lookup_candidate":True,"logical_parent_created":False})))
    out["quest_codex_relationship_candidate_occurrence"]=(c,v)
    c=("id","ingest_run_id","snapshot_id","parser_build_id","source_row_occurrence_id","source_record_id","source_pointer","field_name","array_index","raw_value_json","resolution_status","metadata_json"); v=[]
    for x in d["opaque"]:
        k=(x["table"],x["row_index"]); v.append((occurrence_id("opaque",ident,*k,x["source_pointer"]),run,snap,parser,rows[k],row_records[k],x["source_pointer"],x["field_name"],x["array_index"],canonical_json(x["raw_value"]),"opaque_unresolved",canonical_json({"inference_performed":False,"identity_effect":"none"})))
    out["quest_codex_opaque_occurrence"]=(c,v)
    c=("id","ingest_run_id","snapshot_id","parser_build_id","source_row_occurrence_id","source_record_id","source_pointer","field_name","field_role","textmap_hash","raw_value_json","metadata_json"); v=[]
    for x in d["text_owners"]:
        k=(x["table"],x["row_index"]); v.append((owners[x["owner_key"]],run,snap,parser,rows[k],row_records[k],x["source_pointer"],x["field_name"],x["field_role"],x["textmap_hash"],canonical_json(x["raw_value"]),canonical_json({"hash_identity_effect":"none","independent_owner_occurrence":True})))
    out["quest_codex_text_owner_occurrence"]=(c,v)
    c=("id","ingest_run_id","snapshot_id","parser_build_id","owner_occurrence_id","source_row_occurrence_id","source_record_id","source_pointer","locale","resolution_status","raw_text","normalized_text","raw_checksum","normalized_checksum","metadata_json"); v=[]
    for x in d["text_resolutions"]:
        k=(x["table"],x["row_index"]); v.append((resolutions[(x["owner_key"],x["locale"])],run,snap,parser,owners[x["owner_key"]],rows[k],row_records[k],x["source_pointer"],x["locale"],x["resolution_status"],x["raw_text"],x["normalized_text"],x["raw_checksum"],x["normalized_checksum"],canonical_json({"missing_is_legal":True,"text_identity_effect":"none"})))
    out["quest_codex_text_resolution_occurrence"]=(c,v)
    c=("id","ingest_run_id","snapshot_id","parser_build_id","resolution_occurrence_id","source_file_id","source_record_id","source_pointer","shard_kind","locale","raw_text","normalized_text","raw_checksum","normalized_checksum","metadata_json"); v=[]
    for x in d["text_candidates"]:
        oid=occurrence_id("text-candidate",ident,x["owner_key"],x["locale"],x["shard_kind"],x["source_pointer"]); v.append((oid,run,snap,parser,resolutions[(x["owner_key"],x["locale"])],fids[x["source_relative_path"]],rids[(x["source_relative_path"],x["source_pointer"])],x["source_pointer"],x["shard_kind"],x["locale"],x["raw_text"],x["normalized_text"],x["raw_checksum"],x["normalized_checksum"],canonical_json({"candidate_identity_effect":"none","owner_key":x["owner_key"]})))
    out["quest_codex_text_candidate_occurrence"]=(c,v)
    c=("id","ingest_run_id","snapshot_id","parser_build_id","source_row_occurrence_id","source_record_id","source_pointer","field_name","asset_kind","raw_reference","reference_status","metadata_json"); v=[]
    for x in d["assets"]:
        k=(x["table"],x["row_index"]); v.append((occurrence_id("asset",ident,*k,x["source_pointer"]),run,snap,parser,rows[k],row_records[k],x["source_pointer"],x["field_name"],x["asset_kind"],x["raw_reference"],x["reference_status"],canonical_json({"asset_identity_created":False,"file_existence_claimed":False})))
    out["quest_codex_asset_reference_occurrence"]=(c,v)
    return out
