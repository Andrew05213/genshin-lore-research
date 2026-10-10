from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from .character_phase4c2_release_contract import (
    Phase4C2ReleaseContractError,
    Phase4C2ReleaseExpected,
    build_phase4c2_release_expected,
    verify_phase4c2_release_occurrence_rows,
)
from .character_phase4c2_schema import (
    CharacterPhase4C2SchemaError,
    verify_character_phase4c2_schema_signature,
)
from .character_contracts import C4B_CONTRACT, C5B_CONTRACT
from .util import canonical_json, digest_id, sha256_bytes


RUN_FAMILY = "character_phase4c2_avatar10000003_canary"


class Phase4C2VerificationError(RuntimeError):
    pass


def _fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "sha256": sha256_bytes(path.read_bytes()),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _load_build_report(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase4C2VerificationError(
            f"cannot load Phase 4C2 build report: {exc}"
        ) from exc
    keys = {
        "status",
        "db_path",
        "snapshot_id",
        "parser_build_id",
        "ingest_run_id",
        "config_checksum",
        "manifest",
        "database_fingerprint",
        "counts",
        "status_counts",
        "table_row_counts",
        "text_owner_field_counts",
        "occurrence_set_digests",
        "scope",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise Phase4C2VerificationError(
            "Phase 4C2 build report keys mismatch"
        )
    return value


def _expected_scope(expected: Phase4C2ReleaseExpected) -> dict[str, Any]:
    return {
        "avatar_id": "10000003",
        "selected_paths": [
            item["relative_path"]
            for item in expected.derived["files"]
        ],
        "full_character_scan": False,
        "full_voice_scan": False,
        "logical_identity_created": False,
        "logical_passage_created": False,
        "mcp_capability_added": False,
        "future_context_scope": "voice_container_only",
    }


def _validate_report(
    build: dict[str, Any],
    target: Path,
    before: dict[str, Any],
    expected: Phase4C2ReleaseExpected,
) -> None:
    if (
        build["status"] != "complete"
        or Path(build["db_path"]).resolve() != target.resolve()
    ):
        raise Phase4C2VerificationError(
            "Phase 4C2 build report status/path mismatch"
        )
    for key in (
        "snapshot_id",
        "parser_build_id",
        "ingest_run_id",
        "config_checksum",
    ):
        if build[key] != getattr(expected, key):
            raise Phase4C2VerificationError(
                f"Phase 4C2 build report {key} mismatch"
            )
    if build["manifest"] != {
        "manifest_id": expected.manifest.manifest_id,
        "raw_sha256": expected.manifest.raw_sha256,
    }:
        raise Phase4C2VerificationError(
            "Phase 4C2 build report manifest binding mismatch"
        )
    if build["database_fingerprint"] != before:
        raise Phase4C2VerificationError(
            "Phase 4C2 build report database fingerprint mismatch"
        )
    for key in (
        "counts",
        "status_counts",
        "table_row_counts",
        "text_owner_field_counts",
        "occurrence_set_digests",
    ):
        if build[key] != expected.derived[key]:
            raise Phase4C2VerificationError(
                f"Phase 4C2 build report {key} mismatch"
            )
    if build["scope"] != _expected_scope(expected):
        raise Phase4C2VerificationError(
            "Phase 4C2 build report scope mismatch"
        )


def _same_family_run_ids(conn: sqlite3.Connection) -> list[str]:
    result: list[str] = []
    for row in conn.execute(
        "SELECT id,scope_json FROM ingest_run ORDER BY id"
    ):
        try:
            scope = json.loads(str(row["scope_json"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise Phase4C2VerificationError(
                f"ingest_run scope_json is invalid: {row['id']}"
            ) from exc
        if (
            isinstance(scope, dict)
            and scope.get("family") == RUN_FAMILY
        ):
            result.append(str(row["id"]))
    return result


_CHARACTER_TABLES = {
    "rows": "character_row_occurrence",
    "raw_slots": "character_raw_slot_occurrence",
    "relations": "character_relation_occurrence",
    "text_resolutions": "character_text_resolution_occurrence",
    "text_candidates": "character_text_candidate_occurrence",
    "voice_containers": "character_voice_container_occurrence",
    "audio_variants": "character_audio_variant_occurrence",
}


def _verify_db_contract(
    conn: sqlite3.Connection,
    expected: Phase4C2ReleaseExpected,
    *,
    allowed_other_run_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    versions = [
        int(row[0])
        for row in conn.execute(
            "SELECT version FROM schema_migration ORDER BY version"
        )
    ]
    if versions != list(range(1, 11)):
        raise Phase4C2VerificationError(
            "Phase 4C2 schema must be exactly known versions "
            f"1..10: {versions}"
        )
    schema_signature = verify_character_phase4c2_schema_signature(
        conn
    )

    source = expected.manifest.source
    catalog = conn.execute(
        "SELECT kind,url,license_note FROM source_catalog WHERE id=?",
        (source["source_id"],),
    ).fetchone()
    if catalog is None or tuple(catalog) != (
        "primary",
        source["upstream"],
        None,
    ):
        raise Phase4C2VerificationError(
            "Phase 4C2 source_catalog binding mismatch"
        )
    snapshot = conn.execute(
        'SELECT source_id,release_label,build,"commit",tree,observed_at,'
        "status,metadata_json FROM source_snapshot WHERE id=?",
        (expected.snapshot_id,),
    ).fetchone()
    if snapshot is None or tuple(snapshot) != (
        source["source_id"],
        source["release_label"],
        source["build"],
        source["commit"],
        source["tree"],
        source["observed_at"],
        "active",
        canonical_json({"sealed_source_manifest": True}),
    ):
        raise Phase4C2VerificationError(
            "Phase 4C2 parser-independent source snapshot "
            "binding mismatch"
        )
    parser = conn.execute(
        "SELECT parser_version,mapping_version,fingerprint,"
        "config_checksum,created_at FROM parser_build WHERE id=?",
        (expected.parser_build_id,),
    ).fetchone()
    if parser is None or tuple(parser) != (
        expected.manifest.versions["parser"],
        expected.manifest.versions["projection"],
        "character-phase4c2-sealed-avatar10000003-canary",
        expected.config_checksum,
        source["observed_at"],
    ):
        raise Phase4C2VerificationError(
            "Phase 4C2 parser_build binding mismatch"
        )
    expected_run_scope = canonical_json({
        "family": RUN_FAMILY,
        "manifest_id": expected.manifest.manifest_id,
        "selected_paths": [
            item["relative_path"]
            for item in expected.derived["files"]
        ],
    })
    expected_run_config = canonical_json({
        "manifest_raw_sha256": expected.manifest.raw_sha256,
        "versions": expected.manifest.versions,
        "logical_identity_created": False,
        "mcp_capability_added": False,
    })
    run = conn.execute(
        "SELECT snapshot_id,parser_build_id,scope_json,config_json,"
        "status,created_at FROM ingest_run WHERE id=?",
        (expected.ingest_run_id,),
    ).fetchone()
    if run is None or tuple(run) != (
        expected.snapshot_id,
        expected.parser_build_id,
        expected_run_scope,
        expected_run_config,
        "complete",
        source["observed_at"],
    ):
        raise Phase4C2VerificationError(
            "Phase 4C2 ingest_run binding mismatch"
        )
    family_ids = _same_family_run_ids(conn)
    if family_ids != [expected.ingest_run_id]:
        raise Phase4C2VerificationError(
            "Phase 4C2 same-family ingest run set mismatch: "
            f"{family_ids}"
        )

    file_ids: dict[str, str] = {}
    for item in expected.derived["files"]:
        file_id = digest_id(
            "sf", expected.snapshot_id, item["relative_path"]
        )
        file_ids[item["relative_path"]] = file_id
        row = conn.execute(
            "SELECT snapshot_id,relative_path,upstream_blob_oid,"
            "sha256,size_bytes,format FROM source_file WHERE id=?",
            (file_id,),
        ).fetchone()
        if row is None:
            raise Phase4C2VerificationError(
                "Phase 4C2 source_file mismatch: "
                f"{item['relative_path']}"
            )
        actual_file = tuple(row)
        if (
            actual_file[0:2]
            != (
                expected.snapshot_id,
                item["relative_path"],
            )
            or actual_file[2] not in (None, item["blob_oid"])
            or actual_file[3:]
            != (
                item["sha256"],
                item["size_bytes"],
                item["format"],
            )
        ):
            raise Phase4C2VerificationError(
                "Phase 4C2 source_file mismatch: "
                f"{item['relative_path']}"
            )
    for item in expected.derived["source_records"]:
        file_id = file_ids[item["relative_path"]]
        record_id = digest_id(
            "sr", file_id, item["source_pointer"]
        )
        row = conn.execute(
            "SELECT source_file_id,pointer,record_kind,native_id,"
            "raw_sha256,raw_json FROM source_record WHERE id=?",
            (record_id,),
        ).fetchone()
        if row is None or tuple(row) != (
            file_id,
            item["source_pointer"],
            item["record_kind"],
            item["native_id"],
            item["raw_checksum"],
            canonical_json(item["raw_value"]),
        ):
            raise Phase4C2VerificationError(
                "Phase 4C2 source_record mismatch: "
                f"{item['relative_path']}#{item['source_pointer']}"
            )

    verify_phase4c2_release_occurrence_rows(
        conn, expected, insert_missing=False
    )
    counts: dict[str, int] = {}
    allowed_runs = {expected.ingest_run_id, *allowed_other_run_ids}
    for key, table in _CHARACTER_TABLES.items():
        scoped = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE ingest_run_id=?",
                (expected.ingest_run_id,),
            ).fetchone()[0]
        )
        total = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
        )
        present_runs = {
            str(row[0])
            for row in conn.execute(
                f"SELECT DISTINCT ingest_run_id FROM {table}"
            )
        }
        unexpected_runs = present_runs - allowed_runs
        if unexpected_runs:
            raise Phase4C2VerificationError(
                f"Phase 4C2 {key} run-scope pollution: "
                f"unexpected_runs={sorted(unexpected_runs)}"
            )
        wanted = int(expected.derived["counts"][key])
        if scoped != wanted or (
            not allowed_other_run_ids and total != wanted
        ):
            raise Phase4C2VerificationError(
                f"Phase 4C2 {key} expected-set pollution: "
                f"scoped={scoped} total={total} expected={wanted}"
            )
        counts[key] = scoped

    business_tables = (
        "evidence_container",
        "identity_group",
        "passage_occurrence",
        "occurrence_edge",
        "document_evidence",
        "static_evidence_container",
        "static_segment_occurrence",
        "static_relationship_occurrence",
        "static_text_resolution_occurrence",
        "static_text_candidate_occurrence",
    )
    business_leak = 0
    for table in business_tables:
        business_leak += int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE ingest_run_id=?",
                (expected.ingest_run_id,),
            ).fetchone()[0]
        )
    if business_leak:
        raise Phase4C2VerificationError(
            "Phase 4C2 run leaked into logical/static business tables"
        )

    observed_status = {
        "relations": dict(Counter(
            str(row[0])
            for row in conn.execute(
                "SELECT resolution_status "
                "FROM character_relation_occurrence "
                "WHERE ingest_run_id=?",
                (expected.ingest_run_id,),
            )
        )),
        "text_resolutions": {
            key: int(
                conn.execute(
                    "SELECT COUNT(*) "
                    "FROM character_text_resolution_occurrence "
                    "WHERE ingest_run_id=? "
                    "AND resolution_status=?",
                    (expected.ingest_run_id, key),
                ).fetchone()[0]
            )
            for key in (
                "unique_main",
                "unique_medium",
                "both_equal",
                "both_conflict",
                "missing",
            )
        },
    }
    if (
        observed_status["relations"]
        != expected.derived["status_counts"]["relations"]
        or observed_status["text_resolutions"]
        != expected.derived["status_counts"]["text_resolutions"]
    ):
        raise Phase4C2VerificationError(
            "Phase 4C2 persisted status counts mismatch"
        )
    distinct_paths = int(
        conn.execute(
            "SELECT COUNT(DISTINCT audio_path) "
            "FROM character_audio_variant_occurrence "
            "WHERE ingest_run_id=?",
            (expected.ingest_run_id,),
        ).fetchone()[0]
    )
    distinct_selectors = int(
        conn.execute(
            "SELECT COUNT(DISTINCT character_child_selector) "
            "FROM character_audio_variant_occurrence "
            "WHERE ingest_run_id=?",
            (expected.ingest_run_id,),
        ).fetchone()[0]
    )
    if (
        distinct_paths
        != expected.derived["counts"]["distinct_audio_paths"]
        or distinct_selectors
        != expected.derived["counts"][
            "distinct_character_child_selectors"
        ]
    ):
        raise Phase4C2VerificationError(
            "Phase 4C2 audio/selector distinct counts mismatch"
        )
    foreign_keys = [
        tuple(row)
        for row in conn.execute("PRAGMA foreign_key_check")
    ]
    if foreign_keys:
        raise Phase4C2VerificationError(
            f"Phase 4C2 foreign key violations: {foreign_keys[:3]}"
        )
    return {
        "schema_version": 10,
        "schema_signature": schema_signature,
        "counts": expected.derived["counts"],
        "status_counts": expected.derived["status_counts"],
        "table_row_counts": expected.derived[
            "table_row_counts"
        ],
        "text_owner_field_counts": expected.derived[
            "text_owner_field_counts"
        ],
        "occurrence_set_digests": expected.derived[
            "occurrence_set_digests"
        ],
        "foreign_key_check": [],
        "business_rows_for_character_run": 0,
        "static_business_rows_for_character_run": 0,
        "write_once_triggers": schema_signature[
            "immutable_triggers"
        ],
    }


def verify_character_phase4c2_database_contract(
    conn: sqlite3.Connection,
    expected: Phase4C2ReleaseExpected,
    *,
    allowed_other_run_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Verify the fixed C2 run using the caller-owned readonly connection.

    ``allowed_other_run_ids`` is intentionally a narrow run-scoped escape
    hatch for the reviewed C2+C4B(+C5B) combined artifact. It never relaxes
    the C2 metadata, source-record, occurrence-row, status, digest,
    logical-boundary, or foreign-key checks; callers may provide only the
    fixed C4B and (for the C5C artifact) fixed C5B runs.
    """

    if tuple(allowed_other_run_ids) not in (
        (),
        (C4B_CONTRACT.ingest_run_id,),
        (C4B_CONTRACT.ingest_run_id, C5B_CONTRACT.ingest_run_id),
    ):
        raise Phase4C2VerificationError(
            "C2 combined verification permits only the fixed Phase 4C4B and Phase 4C5B runs"
        )
    result = _verify_db_contract(
        conn,
        expected,
        allowed_other_run_ids=allowed_other_run_ids,
    )
    return {
        "protocol": "wave1-stage-final-state-contract-v1",
        "contract_id": (
            "wave1-stage-final-state-v1:phase4c2:"
            + expected.manifest.manifest_id
        ),
        "status": "pass",
        "run_id": expected.ingest_run_id,
        "parser_build_id": expected.parser_build_id,
        "manifest_sha256": expected.manifest.raw_sha256,
        "exact_counts": result["counts"],
        "required_checks": [
            "schema", "source_binding", "run_binding", "exact_counts",
            "full_rows", "occurrence_digests", "logical_boundary",
            "foreign_keys",
        ],
        **result,
    }


def verify_character_phase4c2_evidence_db(
    db_path: str | Path,
    build_report_path: str | Path,
    manifest_path: str | Path,
    source_root: str | Path,
) -> dict[str, Any]:
    target = Path(db_path)
    if not target.is_file():
        raise Phase4C2VerificationError(
            f"Phase 4C2 database does not exist: {target}"
        )
    before = _fingerprint(target)
    try:
        # This is an independent release oracle. It imports no builder
        # preflight, derivation, or row verification function.
        expected = build_phase4c2_release_expected(
            source_root, manifest_path
        )
        build = _load_build_report(build_report_path)
        _validate_report(build, target, before, expected)
        uri = target.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
            if int(
                conn.execute("PRAGMA query_only").fetchone()[0]
            ) != 1:
                raise Phase4C2VerificationError(
                    "SQLite query_only could not be enabled"
                )
            contract = _verify_db_contract(conn, expected)
        finally:
            conn.close()
        return {
            "status": "pass",
            "db_path": str(target.resolve()),
            "database_fingerprint": before,
            "manifest": {
                "manifest_id": expected.manifest.manifest_id,
                "raw_sha256": expected.manifest.raw_sha256,
            },
            "source": {
                "commit": expected.manifest.source["commit"],
                "tree": expected.manifest.source["tree"],
                "selected_files": len(expected.derived["files"]),
                "phase4c1_artifacts_verified": 5,
                "canary_unattached_intersection": 0,
                "preflight_verified": True,
            },
            "readonly": {
                "sqlite_uri_mode": "ro",
                "query_only": True,
                "fingerprint_unchanged": True,
            },
            **contract,
        }
    except Phase4C2VerificationError:
        raise
    except (
        Phase4C2ReleaseContractError,
        CharacterPhase4C2SchemaError,
        sqlite3.Error,
        OSError,
        ValueError,
        TypeError,
        KeyError,
    ) as exc:
        raise Phase4C2VerificationError(str(exc)) from exc
    finally:
        after = _fingerprint(target)
        if after != before:
            raise Phase4C2VerificationError(
                "verifier mutated database fingerprint: "
                f"before={before} after={after}"
            )


__all__ = [
    "Phase4C2VerificationError",
    "verify_character_phase4c2_database_contract",
    "verify_character_phase4c2_evidence_db",
]
