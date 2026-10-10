"""Release-owned exact tuple oracle for World Archive Phase 4E2.

This module depends on the frozen source/audit contract, never on builder
functions or build reports.
"""
from __future__ import annotations

from typing import Any, Mapping

from .util import canonical_json, digest_id
from .world_archive_phase4e2_contract import occurrence_id


def exact_occurrence_tables(
    derived: Mapping[str, Any], identity: Mapping[str, str]
) -> dict[str, tuple[tuple[str, ...], list[tuple[Any, ...]]]]:
    run = identity["ingest_run_id"]
    snapshot = identity["snapshot_id"]
    parser = identity["parser_build_id"]
    ident = {"snapshot_id": snapshot, "parser_build_id": parser, "ingest_run_id": run}
    file_ids = {item["relative_path"]: digest_id("sf", snapshot, item["relative_path"]) for item in derived["files"]}
    record_ids = {(item["relative_path"], item["source_pointer"]): digest_id("sr", file_ids[item["relative_path"]], item["source_pointer"]) for item in derived["source_records"]}
    row_ids = {(item["table"], item["row_index"]): occurrence_id("row", ident, item["table"], item["row_index"], item["pointer"]) for item in derived["rows"]}
    row_records = {(item["table"], item["row_index"]): record_ids[(item["relative_path"], item["pointer"])] for item in derived["rows"]}
    owner_ids = {item["owner_key"]: occurrence_id("text-owner", ident, item["owner_key"]) for item in derived["text_owners"]}
    resolution_ids = {(item["owner_key"], item["locale"]): occurrence_id("text-resolution", ident, item["owner_key"], item["locale"]) for item in derived["text_resolutions"]}
    owner_by_key = {item["owner_key"]: item for item in derived["text_owners"]}

    tables: dict[str, tuple[tuple[str, ...], list[tuple[Any, ...]]]] = {}
    columns = ("id","ingest_run_id","snapshot_id","parser_build_id","source_file_id","source_record_id","source_pointer","table_name","row_index","native_locator_field","native_locator_value","raw_checksum","metadata_json")
    values = []
    for item in derived["rows"]:
        key=(item["table"],item["row_index"])
        values.append((row_ids[key],run,snapshot,parser,file_ids[item["relative_path"]],row_records[key],item["pointer"],item["table"],item["row_index"],"id",item["native_locator_value"],item["raw_checksum"],canonical_json({"physical_occurrence":True,"logical_identity_created":False})))
    tables["world_archive_row_occurrence"]=(columns,values)

    columns=("id","ingest_run_id","snapshot_id","parser_build_id","row_occurrence_id","source_record_id","source_pointer","field_name","field_kind","raw_json","metadata_json")
    values=[]
    for item in derived["raw_fields"]:
        key=(item["table"],item["row_index"])
        values.append((occurrence_id("raw-field",ident,item["table"],item["row_index"],item["source_pointer"]),run,snapshot,parser,row_ids[key],row_records[key],item["source_pointer"],item["field_name"],item["field_kind"],canonical_json(item["raw_value"]),canonical_json({"identity_effect":"none"})))
    tables["world_archive_raw_field_occurrence"]=(columns,values)

    columns=("id","ingest_run_id","snapshot_id","parser_build_id","source_row_occurrence_id","source_record_id","source_pointer","field_name","relation_kind","raw_value_json","candidate_key_json","resolution_status","target_table","target_field","candidate_scopes_json","metadata_json")
    values=[]
    for item in derived["relationships"]:
        key=(item["table"],item["row_index"])
        values.append((occurrence_id("relationship",ident,item["table"],item["row_index"],item["source_pointer"]),run,snapshot,parser,row_ids[key],row_records[key],item["source_pointer"],item["field_name"],item["relation_kind"],None if item["raw_value"] is None else canonical_json(item["raw_value"]),None if item["candidate_key"] is None else canonical_json(item["candidate_key"]),item["resolution_status"],item["target_table"],item["target_field"],canonical_json(item["candidate_scopes"]),canonical_json({"mapping_basis":item["mapping_basis"],"target_is_candidate_only":True})))
    tables["world_archive_relationship_occurrence"]=(columns,values)

    columns=("id","ingest_run_id","snapshot_id","parser_build_id","source_row_occurrence_id","source_record_id","source_pointer","field_name","array_index","raw_value_json","resolution_status","reason","metadata_json")
    values=[]
    for item in derived["opaque"]:
        key=(item["table"],item["row_index"])
        values.append((occurrence_id("opaque",ident,item["table"],item["row_index"],item["source_pointer"]),run,snapshot,parser,row_ids[key],row_records[key],item["source_pointer"],item["field_name"],item["array_index"],canonical_json(item["raw_value"]),"opaque_unresolved",item["reason"],canonical_json({"identity_effect":"none","inference_performed":False})))
    tables["world_archive_opaque_occurrence"]=(columns,values)

    columns=("id","ingest_run_id","snapshot_id","parser_build_id","source_row_occurrence_id","source_record_id","source_pointer","field_name","field_role","textmap_hash","raw_value_json","metadata_json")
    values=[]
    for item in derived["text_owners"]:
        key=(item["table"],item["row_index"])
        values.append((owner_ids[item["owner_key"]],run,snapshot,parser,row_ids[key],row_records[key],item["source_pointer"],item["field_name"],item["field_role"],str(item["textmap_hash"]),canonical_json(item["raw_value"]),canonical_json({"hash_identity_effect":"none","independent_owner_occurrence":True})))
    tables["world_archive_text_owner_occurrence"]=(columns,values)

    columns=("id","ingest_run_id","snapshot_id","parser_build_id","owner_occurrence_id","source_row_occurrence_id","source_record_id","source_pointer","locale","textmap_hash","resolution_status","raw_text","normalized_text","raw_checksum","normalized_checksum","candidate_scopes_json","metadata_json")
    values=[]
    for item in derived["text_resolutions"]:
        key=(item["table"],item["row_index"]); owner=owner_by_key[item["owner_key"]]
        values.append((resolution_ids[(item["owner_key"],item["locale"])],run,snapshot,parser,owner_ids[item["owner_key"]],row_ids[key],row_records[key],owner["source_pointer"],item["locale"],str(item["textmap_hash"]),item["resolution_status"],item["raw_text"],item["normalized_text"],item["raw_checksum"],item["normalized_checksum"],canonical_json(item["candidate_scopes"]),canonical_json({"missing_is_legal":True,"text_identity_effect":"none"})))
    tables["world_archive_text_resolution_occurrence"]=(columns,values)

    columns=("id","ingest_run_id","snapshot_id","parser_build_id","resolution_occurrence_id","source_file_id","source_record_id","source_pointer","shard_kind","locale","raw_text","normalized_text","raw_checksum","normalized_checksum","metadata_json")
    values=[]
    for item in derived["text_candidates"]:
        values.append((occurrence_id("text-candidate",ident,item["owner_key"],item["locale"],item["shard_kind"],item["source_pointer"]),run,snapshot,parser,resolution_ids[(item["owner_key"],item["locale"])],file_ids[item["source_relative_path"]],record_ids[(item["source_relative_path"],item["source_pointer"])],item["source_pointer"],item["shard_kind"],item["locale"],item["raw_text"],item["normalized_text"],item["raw_checksum"],item["normalized_checksum"],canonical_json({"candidate_identity_effect":"none","owner_key":item["owner_key"]})))
    tables["world_archive_text_candidate_occurrence"]=(columns,values)

    columns=("id","ingest_run_id","snapshot_id","parser_build_id","source_row_occurrence_id","source_record_id","source_pointer","field_name","asset_kind","raw_reference","reference_status","metadata_json")
    values=[]
    for item in derived["assets"]:
        key=(item["table"],item["row_index"])
        values.append((occurrence_id("asset",ident,item["table"],item["row_index"],item["source_pointer"]),run,snapshot,parser,row_ids[key],row_records[key],item["source_pointer"],item["field_name"],item["asset_kind"],item["raw_reference"],item["reference_status"],canonical_json({"asset_identity_created":False,"file_existence_claimed":False})))
    tables["world_archive_asset_reference_occurrence"]=(columns,values)
    return tables
