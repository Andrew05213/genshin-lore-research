from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .static_phase4b3_manifest import Phase4B3ManifestError, load_static_phase4b3_manifest
from .static_phase4b3_verification_contract import (
    Phase4B3ContractError,
    expected_phase4b3_identity,
    verify_phase4b3_database_contract,
    verify_phase4b3_source_contract,
)
from .util import sha256_bytes


class Phase4B3VerificationError(RuntimeError):
    pass


def _fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"sha256": sha256_bytes(path.read_bytes()), "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _load_build_report(path: str | Path) -> dict[str, Any]:
    report_path = Path(path)
    try:
        value = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase4B3VerificationError(f"cannot load Phase 4B3 build report: {exc}") from exc
    keys = {
        "status", "db_path", "snapshot_id", "parser_build_id", "ingest_run_id", "config_checksum",
        "database_fingerprint", "manifest", "counts", "family_counts", "family_selected_paths",
        "status_counts", "locale_breakdown", "occurrence_set_digests", "scope",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise Phase4B3VerificationError("Phase 4B3 build report keys mismatch")
    return value


def _validate_build_report(
    build: dict[str, Any],
    manifest: Any,
    identity: dict[str, Any],
    db_fingerprint: dict[str, Any],
) -> None:
    if build["status"] != "complete":
        raise Phase4B3VerificationError("build report status is not complete")
    for key in ("snapshot_id", "parser_build_id", "ingest_run_id", "config_checksum"):
        if build[key] != identity[key]:
            raise Phase4B3VerificationError(f"build report {key} binding mismatch")
    if build["manifest"] != {"manifest_id": manifest.manifest_id, "raw_sha256": manifest.raw_sha256}:
        raise Phase4B3VerificationError("build report manifest binding mismatch")
    expected_counts = {"files": manifest.expected["counts"]["selected_files"], **{
        key: manifest.expected["counts"][key]
        for key in ("source_records", "containers", "segments", "relationships", "text_resolutions", "text_candidates")
    }}
    if build["counts"] != expected_counts:
        raise Phase4B3VerificationError("build report occurrence counts mismatch")
    if build["occurrence_set_digests"] != manifest.expected["occurrence_set_digests"]:
        raise Phase4B3VerificationError("build report occurrence digests mismatch")
    if build["locale_breakdown"] != manifest.expected["locale_breakdown"]:
        raise Phase4B3VerificationError("build report locale breakdown mismatch")
    if build["database_fingerprint"].get("sha256") != db_fingerprint["sha256"] or build["database_fingerprint"].get("size_bytes") != db_fingerprint["size_bytes"]:
        raise Phase4B3VerificationError("build report database fingerprint mismatch")
    expected_scope = {
        "selected_paths": [item.relative_path for item in manifest.files],
        "full_static_scan": False, "fts_rebuilt": False, "logical_identity_created": False,
        "cross_language_alignment_created": False, "textmap_evidence_container_created": False,
    }
    if build["scope"] != expected_scope:
        raise Phase4B3VerificationError("build report scope boundary mismatch")


def verify_static_phase4b3_evidence_db(
    db_path: str | Path,
    build_report_path: str | Path,
    manifest_path: str | Path,
    source_root: str | Path,
) -> dict[str, Any]:
    """Verify B3 through a SQLite mode=ro/query_only connection and sealed DB contract."""
    target = Path(db_path)
    if not target.is_file():
        raise Phase4B3VerificationError(f"Phase 4B3 database does not exist: {target}")
    before = _fingerprint(target)
    try:
        manifest = load_static_phase4b3_manifest(manifest_path)
        source_verification = verify_phase4b3_source_contract(source_root, manifest)
        build = _load_build_report(build_report_path)
        identity = expected_phase4b3_identity(manifest)
        _validate_build_report(build, manifest, identity, before)
        uri = target.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
            if int(conn.execute("PRAGMA query_only").fetchone()[0]) != 1:
                raise Phase4B3VerificationError("SQLite query_only could not be enabled")
            contract = verify_phase4b3_database_contract(conn, manifest, source_root)
        finally:
            conn.close()
        if contract["identity"] != identity:
            raise Phase4B3VerificationError("independent DB identity reconstruction mismatch")
        for key in ("family_counts", "family_selected_paths", "status_counts"):
            if build[key] != contract[key]:
                raise Phase4B3VerificationError(f"build report {key} disagrees with independent DB contract")
        return {
            "status": "pass",
            "db_path": str(target.resolve()),
            "database_fingerprint": before,
            "manifest": {"manifest_id": manifest.manifest_id, "raw_sha256": manifest.raw_sha256},
            "source": source_verification,
            "readonly": {"sqlite_uri_mode": "ro", "query_only": True, "fingerprint_unchanged": True},
            **{key: contract[key] for key in (
                "schema_version", "counts", "family_counts", "family_selected_paths", "status_counts",
                "locale_breakdown", "occurrence_set_digests", "foreign_key_check",
            )},
        }
    except (Phase4B3ManifestError, Phase4B3ContractError, sqlite3.Error) as exc:
        raise Phase4B3VerificationError(str(exc)) from exc
    finally:
        after = _fingerprint(target)
        if after != before:
            raise Phase4B3VerificationError(
                f"verifier mutated database fingerprint: before={before} after={after}"
            )
