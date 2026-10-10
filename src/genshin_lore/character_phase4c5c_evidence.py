"""Private Phase 4C5C C2+C4B -> C2+C4B+C5B append gate.

This is a purpose-built boundary operation, not a generic Character merger.
It accepts one exact Schema 10 artifact containing the reviewed C2 and C4B
runs, verifies both independent release contracts, appends the sealed C5B
run to a private copy, and promotes that copy with the accepted CAS/rename
helpers.  Every Character occurrence remains identified by its own snapshot,
run, source file, source record, and occurrence coordinates.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .character_contracts import C2_CONTRACT, C4B_CONTRACT, C5B_CONTRACT
from .character_phase4c2_maintenance import (
    Phase4C2VerificationError,
    verify_character_phase4c2_database_contract,
)
from .character_phase4c2_release_contract import (
    Phase4C2ReleaseContractError,
    Phase4C2ReleaseExpected,
    build_phase4c2_release_expected,
)
from .character_phase4c2_schema import (
    CharacterPhase4C2SchemaError,
    verify_character_phase4c2_schema_signature,
)
from .character_phase4c4b_maintenance import (
    Phase4C4BVerificationError,
    verify_character_phase4c4b_database_contract,
)
from .character_phase4c4b_release_contract import (
    Phase4C4BReleaseContractError,
    Phase4C4BReleaseExpected,
    build_phase4c4b_release_expected,
)
from .character_phase4c5b_evidence import (
    Phase4C5BEvidenceError,
    Phase4C5BPreflightResult,
    Phase4C5BPromotedError,
    _backup as _backup_private_copy,
    _discard_reserved_report,
    _fingerprint,
    _offline_identity,
    _replace as _replace_private_copy,
    _reserve_report,
    _sidecars,
    _write_bundle,
    _write_reserved_report,
    _close_reserved_report,
    preflight_character_phase4c5b_evidence_bundle,
)
from .character_phase4c5b_maintenance import (
    Phase4C5BVerificationError,
    verify_character_phase4c5b_database_contract,
)
from .character_phase4c5b_release_contract import (
    Phase4C5BReleaseContractError,
    Phase4C5BReleaseExpected,
    build_phase4c5b_release_expected,
)
from .db import Database
from .util import canonical_json, sha256_text


_ROOT = Path(__file__).resolve().parents[2]
_C2_MANIFEST_PATH = _ROOT / (
    "manifests/character/"
    "anime-game-data-7.0-phase4c2-avatar10000003-canary.json"
)
_C4B_MANIFEST_PATH = _ROOT / (
    "manifests/character/"
    "anime-game-data-7.0-phase4c4b-bounded-six-avatar.json"
)
_C5B_MANIFEST_PATH = _ROOT / (
    "manifests/character/"
    "anime-game-data-7.0-phase4c5b-character-fetter-full.json"
)
_CHARACTER_TABLES = (
    "character_row_occurrence",
    "character_raw_slot_occurrence",
    "character_relation_occurrence",
    "character_text_resolution_occurrence",
    "character_text_candidate_occurrence",
    "character_voice_container_occurrence",
    "character_audio_variant_occurrence",
)
_SOURCE_FILE_COLUMNS = {
    "character_row_occurrence": ("source_file_id",),
    "character_text_candidate_occurrence": ("source_file_id",),
    "character_voice_container_occurrence": (
        "lut_source_file_id",
        "item_source_file_id",
    ),
}
_SOURCE_RECORD_COLUMNS = {
    "character_row_occurrence": ("source_record_id",),
    "character_raw_slot_occurrence": ("source_record_id",),
    "character_relation_occurrence": ("source_record_id",),
    "character_text_resolution_occurrence": ("source_record_id",),
    "character_text_candidate_occurrence": ("source_record_id",),
    "character_voice_container_occurrence": (
        "lut_source_record_id",
        "item_source_record_id",
    ),
    "character_audio_variant_occurrence": ("item_source_record_id",),
}


class Phase4C5CEvidenceError(RuntimeError):
    """The exact C2+C4B input or C5B append contract was not met."""


class Phase4C5CPromotedError(Phase4C5CEvidenceError):
    """Promotion happened, but a post-promotion operation failed."""

    promoted = True


def _fail(message: str) -> None:
    raise Phase4C5CEvidenceError(message)


def _schema_versions(conn: sqlite3.Connection) -> list[int]:
    try:
        return [
            int(row[0])
            for row in conn.execute(
                "SELECT version FROM schema_migration ORDER BY version"
            )
        ]
    except sqlite3.Error as exc:
        _fail(f"C5C schema migration ledger is unreadable: {exc}")


def _assert_delete_journal(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=0")
        mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).casefold()
    except sqlite3.Error as exc:
        _fail(f"C5C target journal mode is unreadable: {exc}")
    if mode != "delete":
        _fail(f"C5C requires DELETE journal mode, got {mode}")


def _character_occurrence_run_ids(conn: sqlite3.Connection) -> set[str]:
    result: set[str] = set()
    for table in _CHARACTER_TABLES:
        for row in conn.execute(f"SELECT DISTINCT ingest_run_id FROM {table}"):
            if row[0] is None:
                _fail(f"{table} contains a NULL ingest_run_id")
            result.add(str(row[0]))
    return result


def _character_metadata_run_ids(conn: sqlite3.Connection) -> set[str]:
    result: set[str] = set()
    for row in conn.execute("SELECT id,scope_json FROM ingest_run ORDER BY id"):
        run_id = str(row[0])
        try:
            scope = json.loads(str(row[1]))
        except (TypeError, json.JSONDecodeError) as exc:
            _fail(f"ingest_run scope_json is invalid: {run_id}: {exc}")
        family = scope.get("family") if isinstance(scope, dict) else None
        if isinstance(family, str) and family.startswith("character_"):
            result.add(run_id)
    return result


def _classify_combined_target(conn: sqlite3.Connection) -> str:
    """Classify only the two exact target states accepted by this gate."""

    occurrence_runs = _character_occurrence_run_ids(conn)
    metadata_runs = _character_metadata_run_ids(conn)
    if occurrence_runs != metadata_runs:
        _fail(
            "C5C Character occurrence/metadata run sets disagree: "
            f"occurrences={sorted(occurrence_runs)} "
            f"metadata={sorted(metadata_runs)}"
        )
    c2 = C2_CONTRACT.ingest_run_id
    c4b = C4B_CONTRACT.ingest_run_id
    c5b = C5B_CONTRACT.ingest_run_id
    if occurrence_runs == {c2, c4b}:
        return "c2_c4b"
    if occurrence_runs == {c2, c4b, c5b}:
        return "c2_c4b_c5b"
    _fail(
        "C5C target must contain exactly C2+C4B before append or exact "
        f"C2+C4B+C5B after append; observed {sorted(occurrence_runs)}"
    )


def _build_release_expected(
    source_root: str | Path,
    c5b_preflight: Phase4C5BPreflightResult,
) -> tuple[
    Phase4C2ReleaseExpected,
    Phase4C4BReleaseExpected,
    Phase4C5BReleaseExpected,
]:
    """Build all three release-owned expectations before opening write state."""

    try:
        c2 = build_phase4c2_release_expected(source_root, _C2_MANIFEST_PATH)
        c4b = build_phase4c4b_release_expected(source_root, _C4B_MANIFEST_PATH)
        c5b = build_phase4c5b_release_expected(
            source_root, c5b_preflight.manifest.path
        )
    except (
        Phase4C2ReleaseContractError,
        Phase4C4BReleaseContractError,
        Phase4C5BReleaseContractError,
    ) as exc:
        _fail(f"C5C independent release qualification failed: {exc}")
    if (
        c5b.snapshot_id != C5B_CONTRACT.snapshot_id
        or c5b.parser_build_id != C5B_CONTRACT.parser_build_id
        or c5b.config_checksum != C5B_CONTRACT.config_checksum
        or c5b.ingest_run_id != C5B_CONTRACT.ingest_run_id
    ):
        _fail("C5C C5B identity does not match the fixed sealed contract")
    return c2, c4b, c5b


def _assert_identity(path: Path, expected: Any, *, stage: str) -> None:
    current = _fingerprint(path)
    observed = (
        current["device"],
        current["inode"],
        current["mode"],
        current["sha256"],
        current["size_bytes"],
        current["mtime_ns"],
    )
    wanted = (
        expected.device,
        expected.inode,
        expected.mode,
        expected.sha256,
        expected.size_bytes,
        expected.mtime_ns,
    )
    if observed != wanted:
        _fail(f"database identity changed {stage}")


def _source_ids_for_run(
    conn: sqlite3.Connection,
    run_id: str,
) -> tuple[set[str], set[str]]:
    source_files: set[str] = set()
    source_records: set[str] = set()
    for table in _CHARACTER_TABLES:
        for column in _SOURCE_FILE_COLUMNS.get(table, ()):
            for row in conn.execute(
                f"SELECT {column} FROM {table} WHERE ingest_run_id=?",
                (run_id,),
            ):
                if row[0] is not None:
                    source_files.add(str(row[0]))
        for column in _SOURCE_RECORD_COLUMNS.get(table, ()):
            for row in conn.execute(
                f"SELECT {column} FROM {table} WHERE ingest_run_id=?",
                (run_id,),
            ):
                if row[0] is not None:
                    source_records.add(str(row[0]))
    return source_files, source_records


def _provenance_digest(conn: sqlite3.Connection, run_id: str) -> str:
    """Digest prior run metadata and only provenance it actually references."""

    source_files, source_records = _source_ids_for_run(conn, run_id)
    occurrences: dict[str, list[list[Any]]] = {}
    for table in _CHARACTER_TABLES:
        occurrences[table] = [
            list(row)
            for row in conn.execute(
                f"SELECT * FROM {table} WHERE ingest_run_id=? ORDER BY id",
                (run_id,),
            )
        ]
    def _rows_by_ids(table: str, ids: set[str]) -> list[list[Any]]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        return [
            list(row)
            for row in conn.execute(
                f"SELECT * FROM {table} WHERE id IN ({placeholders}) ORDER BY id",
                tuple(sorted(ids)),
            )
        ]
    run = conn.execute(
        "SELECT * FROM ingest_run WHERE id=?", (run_id,)
    ).fetchone()
    if run is None:
        _fail(f"C5C provenance run is missing: {run_id}")
    parser_id = str(run[2])
    snapshot_id = str(run[1])
    snapshot = conn.execute(
        "SELECT * FROM source_snapshot WHERE id=?", (snapshot_id,)
    ).fetchone()
    if snapshot is None:
        _fail(f"C5C provenance snapshot is missing: {snapshot_id}")
    source_id = str(snapshot[1])
    catalog = conn.execute(
        "SELECT * FROM source_catalog WHERE id=?", (source_id,)
    ).fetchone()
    if catalog is None:
        _fail(f"C5C provenance catalog is missing: {source_id}")
    parser = conn.execute(
        "SELECT * FROM parser_build WHERE id=?", (parser_id,)
    ).fetchone()
    if parser is None:
        _fail(f"C5C provenance parser build is missing: {parser_id}")
    payload = {
        "run": list(run),
        "parser": list(parser),
        "snapshot": list(snapshot),
        "catalog": list(catalog),
        "occurrences": occurrences,
        "source_files": _rows_by_ids("source_file", source_files),
        "source_records": _rows_by_ids("source_record", source_records),
    }
    return sha256_text(canonical_json(payload))


def _verify_c2_c4b_before(
    conn: sqlite3.Connection,
    c2_expected: Phase4C2ReleaseExpected,
    c4b_expected: Phase4C4BReleaseExpected,
    *,
    existing_c5b: bool = False,
) -> dict[str, Any]:
    c2_allowed = (C4B_CONTRACT.ingest_run_id,) + (
        (C5B_CONTRACT.ingest_run_id,) if existing_c5b else ()
    )
    c4b_allowed = (C2_CONTRACT.ingest_run_id,) + (
        (C5B_CONTRACT.ingest_run_id,) if existing_c5b else ()
    )
    try:
        c2 = verify_character_phase4c2_database_contract(
            conn, c2_expected, allowed_other_run_ids=c2_allowed
        )
        c4b = verify_character_phase4c4b_database_contract(
            conn, c4b_expected, allowed_other_run_ids=c4b_allowed
        )
    except (
        Phase4C2VerificationError,
        Phase4C4BVerificationError,
        sqlite3.Error,
    ) as exc:
        _fail(f"C5C exact C2+C4B pre-append verification failed: {exc}")
    if (
        c2["occurrence_set_digests"]
        != c2_expected.derived["occurrence_set_digests"]
        or c4b["occurrence_set_digests"]
        != c4b_expected.derived["occurrence_set_digests"]
    ):
        _fail("C5C pre-append release digest contract mismatch")
    return {
        "c2_occurrence_set_digests": c2["occurrence_set_digests"],
        "c4b_occurrence_set_digests": c4b["occurrence_set_digests"],
        "c2_provenance_digest": _provenance_digest(
            conn, C2_CONTRACT.ingest_run_id
        ),
        "c4b_provenance_digest": _provenance_digest(
            conn, C4B_CONTRACT.ingest_run_id
        ),
    }


def _verify_c2_c4b_c5b_after(
    conn: sqlite3.Connection,
    c2_expected: Phase4C2ReleaseExpected,
    c4b_expected: Phase4C4BReleaseExpected,
    c5b_expected: Phase4C5BReleaseExpected,
    before: dict[str, Any],
) -> dict[str, Any]:
    try:
        c2 = verify_character_phase4c2_database_contract(
            conn,
            c2_expected,
            allowed_other_run_ids=(
                C4B_CONTRACT.ingest_run_id,
                C5B_CONTRACT.ingest_run_id,
            ),
        )
        c4b = verify_character_phase4c4b_database_contract(
            conn,
            c4b_expected,
            allowed_other_run_ids=(
                C2_CONTRACT.ingest_run_id,
                C5B_CONTRACT.ingest_run_id,
            ),
        )
        c5b = verify_character_phase4c5b_database_contract(
            conn,
            c5b_expected,
            allowed_other_run_ids=(
                C2_CONTRACT.ingest_run_id,
                C4B_CONTRACT.ingest_run_id,
            ),
            allow_extra_source_files=True,
            allow_extra_source_records=True,
        )
    except (
        Phase4C2VerificationError,
        Phase4C4BVerificationError,
        Phase4C5BVerificationError,
        sqlite3.Error,
    ) as exc:
        _fail(f"C5C post-append release verification failed: {exc}")
    c2_provenance = _provenance_digest(conn, C2_CONTRACT.ingest_run_id)
    c4b_provenance = _provenance_digest(conn, C4B_CONTRACT.ingest_run_id)
    if (
        c2["occurrence_set_digests"] != before["c2_occurrence_set_digests"]
        or c4b["occurrence_set_digests"]
        != before["c4b_occurrence_set_digests"]
        or c2_provenance != before["c2_provenance_digest"]
        or c4b_provenance != before["c4b_provenance_digest"]
    ):
        _fail("C5C prior C2/C4B occurrence or provenance digest changed")
    return {
        "c2": c2,
        "c4b": c4b,
        "c5b": c5b,
        "prior_integrity": {
            "c2_occurrence_set_digests": before["c2_occurrence_set_digests"],
            "c4b_occurrence_set_digests": before["c4b_occurrence_set_digests"],
            "c2_provenance_digest": before["c2_provenance_digest"],
            "c4b_provenance_digest": before["c4b_provenance_digest"],
            "unchanged": True,
        },
    }


def _report(
    target: Path,
    preflight: Phase4C5BPreflightResult,
    post: dict[str, Any],
    before: dict[str, Any],
    *,
    idempotent: bool,
) -> dict[str, Any]:
    c5b = post["c5b"]
    return {
        "status": "complete",
        "idempotent": idempotent,
        "db_path": str(target.resolve()),
        "combined_with_run_ids": [
            C2_CONTRACT.ingest_run_id,
            C4B_CONTRACT.ingest_run_id,
            C5B_CONTRACT.ingest_run_id,
        ],
        "snapshot_id": preflight.snapshot_id,
        "parser_build_id": preflight.parser_build_id,
        "ingest_run_id": preflight.ingest_run_id,
        "config_checksum": preflight.config_checksum,
        "coverage": preflight.manifest.boundary["coverage"],
        "corpus_complete": True,
        "manifest": {
            "manifest_id": preflight.manifest.manifest_id,
            "raw_sha256": preflight.manifest.raw_sha256,
        },
        "counts": preflight.derived["counts"],
        "status_counts": preflight.derived["status_counts"],
        "table_row_counts": preflight.derived["table_row_counts"],
        "owner_field_counts": preflight.derived["text_owner_field_counts"],
        "occurrence_set_digests": preflight.derived[
            "occurrence_set_digests"
        ],
        "c5b_contract": c5b,
        "prior_integrity": post["prior_integrity"],
        "pre_append_integrity": before,
        "database_fingerprint": _fingerprint(target),
        "sidecars_absent": True,
        "journal_mode": "delete",
        "scope": {
            "family": C5B_CONTRACT.run_family,
            "manifest_id": preflight.manifest.manifest_id,
            "snapshot_id": preflight.snapshot_id,
            "coverage": preflight.manifest.boundary["coverage"],
            "corpus_complete": True,
            "selected_paths": [
                item["relative_path"] for item in preflight.derived["files"]
            ],
            "closure_counts": preflight.derived["counts"],
            "occurrence_set_digests": preflight.derived[
                "occurrence_set_digests"
            ],
            "full_character_scan": False,
            "full_voice_scan": False,
            "logical_identity_created": False,
            "mcp_capability_added": False,
        },
        "boundary": {
            "coverage": preflight.manifest.boundary["coverage"],
            "corpus_complete": True,
            "full_character_scan": False,
            "full_voice_scan": False,
            "logical_identity_created": False,
            "mcp_capability_added": False,
        },
    }


def _cleanup_temporary(path: Path | None) -> None:
    if path is None:
        return
    for item in (path, *_sidecars(path)):
        try:
            item.unlink()
        except FileNotFoundError:
            pass


def append_character_phase4c5b_to_existing_c2_c4b_database(
    db_path: str | Path,
    source_root: str | Path,
    manifest_path: str | Path | None = None,
    *,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    """Append exactly C5B to one exact C2+C4B Schema 10 artifact.

    ``manifest_path`` is accepted only as a content-validated C5B manifest;
    the fixed tracked path is used when omitted.  No migration is run and no
    existing Character run can be selected or merged by a fallback rule.
    """

    if os.name != "posix":
        _fail("C5C append requires POSIX CAS/atomic replacement")
    supplied_manifest = _C5B_MANIFEST_PATH if manifest_path is None else manifest_path
    try:
        c5b_preflight = preflight_character_phase4c5b_evidence_bundle(
            source_root, supplied_manifest
        )
    except Phase4C5BEvidenceError as exc:
        _fail(f"C5C C5B source preflight failed: {exc}")
    c2_expected, c4b_expected, c5b_expected = _build_release_expected(
        source_root, c5b_preflight
    )
    target = Path(db_path).resolve()
    report_target = (
        None if report_path is None else Path(report_path).resolve()
    )
    if report_target is not None and report_target == target:
        _fail("C5C report path must not be the database target")

    try:
        original = _offline_identity(target)
        with sqlite3.connect(
            target.as_uri() + "?mode=ro", uri=True, timeout=0
        ) as readonly:
            readonly.row_factory = sqlite3.Row
            _assert_delete_journal(readonly)
            if _schema_versions(readonly) != list(range(1, 11)):
                _fail("C5C append requires exact existing Schema 10")
            try:
                verify_character_phase4c2_schema_signature(readonly)
            except (CharacterPhase4C2SchemaError, sqlite3.Error) as exc:
                _fail(f"C5C existing Schema 10 signature mismatch: {exc}")
            target_kind = _classify_combined_target(readonly)
            if target_kind == "c2_c4b_c5b":
                # Exact output is an idempotent verification path.  It never
                # creates a second C5B run or replaces the target.
                before = _verify_c2_c4b_before(
                    readonly, c2_expected, c4b_expected, existing_c5b=True
                )
                post = _verify_c2_c4b_c5b_after(
                    readonly,
                    c2_expected,
                    c4b_expected,
                    c5b_expected,
                    before,
                )
                _assert_identity(
                    target,
                    original,
                    stage="after idempotent C5C verification",
                )
                report = _report(
                    target,
                    c5b_preflight,
                    post,
                    before,
                    idempotent=True,
                )
                reservation = _reserve_report(report_target)
                try:
                    if reservation is not None:
                        _write_reserved_report(reservation, report)
                except Exception as exc:
                    _discard_reserved_report(reservation)
                    raise Phase4C5CEvidenceError(
                        f"idempotent report write failed; database unchanged: {exc}"
                    ) from exc
                finally:
                    _close_reserved_report(reservation)
                return report
            before = _verify_c2_c4b_before(
                readonly, c2_expected, c4b_expected
            )
            original_versions = _schema_versions(readonly)
        if original_versions != list(range(1, 11)):
            _fail("C5C append input schema changed during inspection")
        _assert_identity(target, original, stage="after C5C read-only inspection")
    except Phase4C5CEvidenceError:
        raise
    except (
        CharacterPhase4C2SchemaError,
        Phase4C2VerificationError,
        Phase4C4BVerificationError,
        Phase4C5BVerificationError,
        sqlite3.Error,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
    ) as exc:
        raise Phase4C5CEvidenceError(str(exc)) from exc

    temporary: Path | None = None
    reservation = None
    promoted = False
    try:
        reservation = _reserve_report(report_target)
        temporary = _backup_private_copy(target, original)
        with Database(temporary) as db:
            conn = db.conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                if _schema_versions(conn) != list(range(1, 11)):
                    _fail("C5C private copy schema changed before append")
                verify_character_phase4c2_schema_signature(conn)
                # Re-run both exact validators inside the transaction boundary
                # so the object being mutated is independently qualified too.
                _verify_c2_c4b_before(conn, c2_expected, c4b_expected)
                _write_bundle(db, c5b_preflight)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        with sqlite3.connect(temporary.as_uri() + "?mode=ro", uri=True) as check:
            check.row_factory = sqlite3.Row
            _assert_delete_journal(check)
            _classify_combined_target(check)
            post = _verify_c2_c4b_c5b_after(
                check,
                c2_expected,
                c4b_expected,
                c5b_expected,
                before,
            )
        if reservation is not None:
            # Catch report serialization failures while the target is still
            # protected by the rollback boundary.
            canonical_json(
                _report(temporary, c5b_preflight, post, before, idempotent=False)
            )
        try:
            _replace_private_copy(target, temporary, original)
        except Phase4C5BPromotedError as exc:
            temporary = None
            promoted = True
            raise Phase4C5CPromotedError(
                f"C5C database promoted but directory sync failed: {exc}"
            ) from exc
        temporary = None
        promoted = True
    except Phase4C5CPromotedError:
        _close_reserved_report(reservation)
        raise
    except (
        Phase4C5BEvidenceError,
        Phase4C5BVerificationError,
        sqlite3.Error,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
    ) as exc:
        if isinstance(exc, Phase4C5CEvidenceError):
            raise
        raise Phase4C5CEvidenceError(str(exc)) from exc
    finally:
        if not promoted:
            _cleanup_temporary(temporary)
            _discard_reserved_report(reservation)

    try:
        report = _report(target, c5b_preflight, post, before, idempotent=False)
        if reservation is not None:
            _write_reserved_report(reservation, report)
    except Exception as exc:
        raise Phase4C5CPromotedError(
            f"C5C database promoted but report preparation failed: {exc}"
        ) from exc
    finally:
        _close_reserved_report(reservation)
    return report


__all__ = [
    "Phase4C5CEvidenceError",
    "Phase4C5CPromotedError",
    "append_character_phase4c5b_to_existing_c2_c4b_database",
]
