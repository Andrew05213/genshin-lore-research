"""Readonly aggregate release gate for the complete Wave1 final database."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path
from typing import Any

from ._quest_corpus_release_contract import QuestCorpusVerificationError
from .errors import LoreDatabaseError, ManifestError
from .character_phase4c2_maintenance import Phase4C2VerificationError
from .static_book1_verification_contract import StaticBook1ContractError
from .static_phase4b2_maintenance import StaticPhase4B2VerificationError
from .static_phase4b3_verification_contract import Phase4B3ContractError
from .static_phase4b5_reliquary_verification_contract import Phase4B5ContractError
from .static_phase4b6_material_verification_contract import Phase4B6ContractError
from .wave1_rebuild_contract import (
    ANCESTRY_SEMANTICS,
    Wave1RebuildContractError,
    build_wave1_rebuild_expectation,
    verify_wave1_database_contract,
)


class Wave1RebuildVerificationError(RuntimeError):
    pass


def _fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _assert_offline_storage(path: Path) -> None:
    sidecars = [
        Path(f"{path}{suffix}")
        for suffix in ("-journal", "-wal", "-shm")
        if os.path.lexists(f"{path}{suffix}")
    ]
    if sidecars:
        raise Wave1RebuildVerificationError(
            f"Wave1 final database has forbidden SQLite sidecars: {sidecars}"
        )
    header = path.read_bytes()[:100]
    if (
        len(header) < 20
        or header[:16] != b"SQLite format 3\x00"
        or header[18:20] != b"\x01\x01"
    ):
        raise Wave1RebuildVerificationError(
            "Wave1 final database must be an offline rollback-journal SQLite file"
        )


def verify_wave1_full_rebuild_db(
    *,
    db_path: str | Path,
    source_root: str | Path,
    source_manifest_path: str | Path,
    audit_cache_path: str | Path,
    manifest_root: str | Path,
    corpus_manifest_path: str | Path,
    book1_manifest_path: str | Path,
    phase4b2_manifest_path: str | Path,
    phase4b3_manifest_path: str | Path,
    phase4b5_manifest_path: str | Path,
    phase4b6_manifest_path: str | Path,
    phase4c2_manifest_path: str | Path,
) -> dict[str, Any]:
    """Verify exact Wave1 source and final DB state without stage reports."""

    target = Path(db_path)
    if not target.is_file() or target.is_symlink():
        raise Wave1RebuildVerificationError(
            f"Wave1 final database is not a regular non-symlink file: {target}"
        )
    _assert_offline_storage(target)
    before = _fingerprint(target)
    connection: sqlite3.Connection | None = None
    expected = None
    contract: dict[str, Any] | None = None
    journal_mode: str | None = None
    primary_error: Wave1RebuildVerificationError | None = None
    after: dict[str, Any] | None = None
    try:
        expected = build_wave1_rebuild_expectation(
            source_root=source_root,
            source_manifest_path=source_manifest_path,
            audit_cache_path=audit_cache_path,
            manifest_root=manifest_root,
            corpus_manifest_path=corpus_manifest_path,
            book1_manifest_path=book1_manifest_path,
            phase4b2_manifest_path=phase4b2_manifest_path,
            phase4b3_manifest_path=phase4b3_manifest_path,
            phase4b5_manifest_path=phase4b5_manifest_path,
            phase4b6_manifest_path=phase4b6_manifest_path,
            phase4c2_manifest_path=phase4c2_manifest_path,
        )
        uri = target.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA query_only=ON")
        if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
            raise Wave1RebuildVerificationError(
                "Wave1 verifier could not enable SQLite query_only"
            )
        if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise Wave1RebuildVerificationError(
                "Wave1 verifier could not enable SQLite foreign_keys"
            )
        journal_mode = str(
            connection.execute("PRAGMA journal_mode").fetchone()[0]
        ).lower()
        if journal_mode != "delete":
            raise Wave1RebuildVerificationError(
                f"Wave1 final database journal mode is not DELETE: {journal_mode}"
            )
        contract = verify_wave1_database_contract(connection, expected)
    except Wave1RebuildVerificationError as exc:
        primary_error = exc
    except (
        Wave1RebuildContractError,
        QuestCorpusVerificationError,
        ManifestError,
        LoreDatabaseError,
        StaticBook1ContractError,
        StaticPhase4B2VerificationError,
        Phase4B3ContractError,
        Phase4B5ContractError,
        Phase4B6ContractError,
        Phase4C2VerificationError,
        sqlite3.Error,
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
    ) as exc:
        primary_error = Wave1RebuildVerificationError(str(exc))
        primary_error.__cause__ = exc
    finally:
        diagnostics: list[str] = []
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error as exc:
                diagnostics.append(f"readonly connection close failed: {exc}")
        try:
            after = _fingerprint(target)
            if after != before:
                diagnostics.append(
                    "database fingerprint changed: "
                    f"before={before} after={after}"
                )
        except (OSError, Wave1RebuildVerificationError) as exc:
            diagnostics.append(f"after-fingerprint failed: {exc}")
        try:
            _assert_offline_storage(target)
        except (OSError, Wave1RebuildVerificationError) as exc:
            diagnostics.append(f"after-storage check failed: {exc}")
        if primary_error is not None:
            for diagnostic in diagnostics:
                primary_error.add_note(diagnostic)
            raise primary_error
        if diagnostics:
            raise Wave1RebuildVerificationError(
                "Wave1 verifier post-audit diagnostics failed: "
                + "; ".join(diagnostics)
            )

    if expected is None or contract is None or after is None or journal_mode is None:
        raise Wave1RebuildVerificationError(
            "Wave1 verifier did not produce a complete final-state contract"
        )

    return {
        "status": "pass",
        "protocol": "wave1-full-rebuild-final-state-v1",
        "db_path": str(target.resolve()),
        "database": {
            "fingerprint_before": before,
            "fingerprint_after": after,
            "unchanged": True,
        },
        "readonly": {
            "sqlite_uri_mode": "ro",
            "query_only": True,
            "single_connection": True,
            "journal_mode": "delete",
            "sidecars_absent": True,
        },
        "source": expected.source_audits,
        "manifests": {
            "aq": expected.aq.manifest_set.corpus_manifest.manifest_id,
            "book1": expected.book1.manifest.manifest_id,
            "phase4b2": expected.phase4b2.manifest_id,
            "phase4b3": expected.phase4b3.manifest_id,
            "phase4b5": expected.phase4b5.manifest_id,
            "phase4b6": expected.phase4b6.manifest_id,
            "phase4c2": expected.phase4c2.manifest.manifest_id,
        },
        "stage_reports_accepted": False,
        "ancestry_semantics": ANCESTRY_SEMANTICS,
        "temporal_copy_history_proven": False,
        **contract,
    }


__all__ = [
    "Wave1RebuildVerificationError",
    "verify_wave1_full_rebuild_db",
]
