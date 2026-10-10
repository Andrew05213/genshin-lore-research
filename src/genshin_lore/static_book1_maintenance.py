"""Read-only release gate for the sealed Phase 4B1 Book1 evidence run."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .errors import ManifestError
from .static_book1_verification_contract import (
    StaticBook1ContractError,
    build_book1_expectation,
    verify_book1_database_contract,
)


class StaticBook1VerificationError(RuntimeError):
    pass


def _fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _assert_delete_storage(path: Path) -> None:
    present = [
        Path(f"{path}{suffix}")
        for suffix in ("-journal", "-wal", "-shm")
        if Path(f"{path}{suffix}").exists()
    ]
    if present:
        raise StaticBook1VerificationError(
            f"Book1 database has forbidden SQLite sidecars: {present}"
        )
    try:
        header = path.read_bytes()[:100]
    except OSError as exc:
        raise StaticBook1VerificationError(
            f"cannot inspect Book1 SQLite header: {exc}"
        ) from exc
    if len(header) < 20 or header[:16] != b"SQLite format 3\x00":
        raise StaticBook1VerificationError("Book1 database has an invalid SQLite header")
    if header[18:20] != b"\x01\x01":
        raise StaticBook1VerificationError(
            "Book1 database must use persisted DELETE/rollback journal mode"
        )


def _load_build_report(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StaticBook1VerificationError(f"cannot load Book1 build report: {exc}") from exc
    keys = {
        "status", "db_path", "snapshot_id", "parser_build_id", "ingest_run_id",
        "config_checksum", "database_fingerprint", "sqlite_storage", "manifest",
        "counts", "scope",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise StaticBook1VerificationError("Book1 build report keys mismatch")
    return value


def _verify_build_report(
    report: dict[str, Any], expected: Any, fingerprint: dict[str, Any], target: Path
) -> None:
    bindings = {
        "status": "complete",
        "db_path": str(target.resolve()),
        "snapshot_id": expected.snapshot_id,
        "parser_build_id": expected.parser_build_id,
        "ingest_run_id": expected.ingest_run_id,
        "config_checksum": expected.config_checksum,
        "manifest": {
            "manifest_id": expected.manifest.manifest_id,
            "raw_sha256": expected.manifest.raw_sha256,
        },
        "sqlite_storage": {
            "journal_mode": "delete",
            "sidecars_absent": True,
        },
        "counts": {"containers": 5, "segments": 57, "relationships": 6},
        "scope": {
            "selected_paths": [item.relative_path for item in expected.files],
            "full_static_scan": False,
            "fts_rebuilt": False,
            "embedding": False,
            "semantic_search": False,
            "logical_identity_created": False,
            "cross_language_alignment_created": False,
        },
    }
    for key, value in bindings.items():
        if report.get(key) != value:
            raise StaticBook1VerificationError(f"Book1 build report {key} binding mismatch")
    sealed_fingerprint = report.get("database_fingerprint")
    if not isinstance(sealed_fingerprint, dict) or sealed_fingerprint != fingerprint:
        raise StaticBook1VerificationError("Book1 build report database fingerprint mismatch")


def verify_static_book1_evidence_db(
    db_path: str | Path,
    build_report_path: str | Path,
    source_manifest_path: str | Path,
    book1_manifest_path: str | Path,
    source_root: str | Path,
) -> dict[str, Any]:
    target = Path(db_path)
    if not target.is_file() or target.is_symlink():
        raise StaticBook1VerificationError(f"Book1 database is not a regular file: {target}")
    _assert_delete_storage(target)
    before = _fingerprint(target)
    try:
        expected = build_book1_expectation(
            source_manifest_path, book1_manifest_path, source_root
        )
        build_report = _load_build_report(build_report_path)
        _verify_build_report(build_report, expected, before, target)
        uri = target.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA query_only=ON")
            if int(conn.execute("PRAGMA query_only").fetchone()[0]) != 1:
                raise StaticBook1VerificationError("Book1 verifier could not enable query_only")
            journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            if journal_mode != "delete":
                raise StaticBook1VerificationError(
                    f"Book1 verifier requires DELETE journal mode, got {journal_mode!r}"
                )
            contract = verify_book1_database_contract(conn, expected)
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
                "root": str(expected.source_root),
                "commit": expected.source_manifest["commit"],
                "tree": expected.source_manifest["tree"],
                "selected_files": len(expected.files),
            },
            "readonly": {
                "sqlite_uri_mode": "ro",
                "query_only": True,
                "journal_mode": "delete",
                "sidecars_absent": True,
                "fingerprint_unchanged": True,
            },
            **contract,
        }
    except StaticBook1VerificationError:
        raise
    except (
        StaticBook1ContractError,
        ManifestError,
        sqlite3.Error,
        OSError,
        ValueError,
        TypeError,
    ) as exc:
        raise StaticBook1VerificationError(str(exc)) from exc
    finally:
        after = _fingerprint(target)
        if after != before:
            raise StaticBook1VerificationError(
                f"Book1 verifier mutated database fingerprint: before={before}, after={after}"
            )
        _assert_delete_storage(target)


__all__ = ["StaticBook1VerificationError", "verify_static_book1_evidence_db"]
