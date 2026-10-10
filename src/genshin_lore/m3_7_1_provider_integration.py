"""Explicit 7.1 candidate-to-historical M3 v2 projection rules.

This adapter changes status vocabulary only; it never selects a TextMap
candidate or creates cross-snapshot identity.
"""
from __future__ import annotations


STATUS_MAP = {
    "missing": "missing",
    "resolved_unique": "resolved_unique",
    "resolved_candidate_set": "ambiguous",
    "partial_missing": "partial_missing",
    "resolved_all_locales": "resolved_all_locales",
}


def historical_status(status: str) -> str:
    try:
        return STATUS_MAP[status]
    except KeyError as error:
        raise ValueError("unknown M3 v2 status cannot be projected") from error


def historical_textmap_candidate(candidate: dict, v2_run: str) -> dict:
    """Map a sealed physical candidate to Q2/R3's required provenance fields."""
    fields = (
        "candidate_id", "owner_id", "textmap_snapshot_id", "textmap_parser_build_id",
        "textmap_ingest_run_id", "source_record_pointer", "locale", "tier",
        "source_path", "pair_ordinal", "source_pointer", "raw_key_json",
        "raw_text_json", "raw_text", "raw_key_sha256", "raw_text_sha256",
        "raw_record_sha256",
    )
    result = {field: candidate[field] for field in fields}
    if result["source_record_pointer"] != "/" or not v2_run:
        raise ValueError("TextMap candidate provenance is incomplete")
    result["ingest_run_id"] = v2_run
    result["shard_tier"] = result.pop("tier")
    result["relative_path"] = result.pop("source_path")
    result["pointer"] = result.pop("source_pointer")
    return result
