"""Release-owned exact tuple oracle for the Phase 4G2 canary.

The maintenance verifier imports this module, while the builder does not.  It
reconstructs every business tuple from the source contract and therefore does
not trust builder-produced IDs or counts.
"""

from __future__ import annotations

from typing import Any, Mapping

from .avatar_codex_phase4g2_contract import occurrence_id
from .util import canonical_json, digest_id


def exact_occurrence_tables(
    derived: Mapping[str, Any], identity: Mapping[str, str]
) -> dict[str, tuple[tuple[str, ...], list[tuple[Any, ...]]]]:
    run = identity["ingest_run_id"]
    snapshot = identity["snapshot_id"]
    parser = identity["parser_build_id"]
    ident = {"snapshot_id": snapshot, "parser_build_id": parser, "ingest_run_id": run}
    file_ids = {
        item["relative_path"]: digest_id("sf", snapshot, item["relative_path"])
        for item in derived["files"]
    }
    record_ids = {
        (item["relative_path"], item["source_pointer"]): digest_id(
            "sr", file_ids[item["relative_path"]], item["source_pointer"]
        )
        for item in derived["source_records"]
    }
    row_ids = {
        (item["table"], item["row_index"]): occurrence_id(
            "row", ident, item["table"], item["row_index"], item["pointer"]
        )
        for item in derived["rows"]
    }
    row_record_ids = {
        (item["table"], item["row_index"]): record_ids[(item["relative_path"], item["pointer"])]
        for item in derived["rows"]
    }
    relationship_ids = {
        item["relationship_key"]: occurrence_id("relationship", ident, item["relationship_key"])
        for item in derived["relationships"]
    }
    tables: dict[str, tuple[tuple[str, ...], list[tuple[Any, ...]]]] = {}

    columns = (
        "id", "ingest_run_id", "snapshot_id", "parser_build_id", "source_file_id",
        "source_record_id", "source_pointer", "evidence_kind", "table_name", "row_index",
        "native_locator_field", "native_locator_value", "raw_checksum", "metadata_json",
    )
    rows = []
    for item in derived["rows"]:
        key = (item["table"], item["row_index"])
        rows.append(
            (
                row_ids[key], run, snapshot, parser, file_ids[item["relative_path"]],
                row_record_ids[key], item["pointer"], item["evidence_kind"], item["table"],
                item["row_index"], item["native_locator_field"], item["native_locator_value"],
                item["raw_checksum"], canonical_json({"physical_occurrence": True, "logical_identity_created": False}),
            )
        )
    tables["avatar_codex_row_occurrence"] = (columns, rows)

    columns = (
        "id", "ingest_run_id", "snapshot_id", "parser_build_id", "row_occurrence_id",
        "source_record_id", "source_pointer", "field_name", "field_presence", "raw_json",
        "metadata_json",
    )
    rows = []
    for item in derived["raw_fields"]:
        key = (item["table"], item["row_index"])
        rows.append(
            (
                occurrence_id("raw-field", ident, item["table"], item["row_index"], item["source_pointer"]),
                run, snapshot, parser, row_ids[key], row_record_ids[key], item["source_pointer"],
                item["field_name"], item["field_presence"],
                None if item["raw_value"] is None else canonical_json(item["raw_value"]),
                canonical_json({"absence_is_evidence": item["field_presence"] == "missing", "identity_effect": "none"}),
            )
        )
    tables["avatar_codex_raw_field_occurrence"] = (columns, rows)

    columns = (
        "id", "ingest_run_id", "snapshot_id", "parser_build_id", "source_row_occurrence_id",
        "source_record_id", "source_pointer", "field_name", "raw_value_json", "target_table",
        "target_field", "resolution_status", "metadata_json",
    )
    rows = []
    for item in derived["relationships"]:
        key = (item["table"], item["row_index"])
        rows.append(
            (
                relationship_ids[item["relationship_key"]], run, snapshot, parser, row_ids[key],
                row_record_ids[key], item["source_pointer"], item["field_name"], canonical_json(item["raw_value"]),
                item["target_table"], item["target_field"], item["resolution_status"],
                canonical_json({"candidate_identity_effect": "none", "missing_is_legal": True}),
            )
        )
    tables["avatar_codex_relationship_occurrence"] = (columns, rows)

    columns = (
        "id", "ingest_run_id", "snapshot_id", "parser_build_id", "relationship_occurrence_id",
        "source_file_id", "source_record_id", "source_pointer", "candidate_ordinal", "target_table",
        "target_field", "target_raw_value_json", "raw_checksum", "metadata_json",
    )
    rows = []
    for item in derived["relationship_candidates"]:
        rows.append(
            (
                occurrence_id(
                    "relationship-candidate", ident, item["relationship_key"], item["candidate_ordinal"],
                    item["source_relative_path"], item["source_pointer"],
                ),
                run, snapshot, parser, relationship_ids[item["relationship_key"]],
                file_ids[item["source_relative_path"]],
                record_ids[(item["source_relative_path"], item["source_pointer"])], item["source_pointer"],
                item["candidate_ordinal"], item["target_table"], item["target_field"],
                canonical_json(item["target_raw_value"]), item["raw_checksum"],
                canonical_json({"physical_lookup_candidate": True, "logical_parent_created": False}),
            )
        )
    tables["avatar_codex_relationship_candidate_occurrence"] = (columns, rows)

    columns = (
        "id", "ingest_run_id", "snapshot_id", "parser_build_id", "source_row_occurrence_id",
        "source_record_id", "source_pointer", "field_name", "array_index", "raw_value_json",
        "resolution_status", "metadata_json",
    )
    rows = []
    for item in derived["opaque"]:
        key = (item["table"], item["row_index"])
        rows.append(
            (
                occurrence_id("opaque", ident, item["table"], item["row_index"], item["source_pointer"]),
                run, snapshot, parser, row_ids[key], row_record_ids[key], item["source_pointer"],
                item["field_name"], item["array_index"], canonical_json(item["raw_value"]),
                "opaque_unresolved", canonical_json({"inference_performed": False, "identity_effect": "none"}),
            )
        )
    tables["avatar_codex_opaque_occurrence"] = (columns, rows)
    return tables


__all__ = ["exact_occurrence_tables"]
