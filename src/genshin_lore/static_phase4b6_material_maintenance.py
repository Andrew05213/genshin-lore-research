"""Read-only release gate for the sealed Phase 4B6 material evidence run."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .static_phase4b6_material_verification_contract import (
    Phase4B6ContractError,
    expected_phase4b6_material_identity,
    verify_phase4b6_material_database_contract,
    verify_phase4b6_material_source_contract,
)
from .util import sha256_bytes


class Phase4B6VerificationError(RuntimeError):
    """Raised when an artifact is not an exact, immutable Phase 4B6 release."""


def load_static_phase4b6_material_manifest(path: str | Path) -> Any:
    """Lazy import the Lane-A manifest loader to keep ownership separate."""

    try:
        from .static_phase4b6_material_manifest import (
            Phase4B6ManifestError,
            load_static_phase4b6_material_manifest as loader,
        )
    except ImportError as exc:
        raise Phase4B6VerificationError(
            "Phase 4B6 material manifest loader is not available"
        ) from exc
    try:
        return loader(path)
    except Phase4B6ManifestError as exc:
        # A malformed or wrong-pinned manifest is an input validation failure,
        # not an implementation detail that may escape the release gate.
        raise Phase4B6VerificationError(str(exc)) from exc


def _fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "sha256": sha256_bytes(path.read_bytes()),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _load_build_report(path: str | Path) -> dict[str, Any]:
    report_path = Path(path)
    try:
        value = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase4B6VerificationError(
            f"cannot load Phase 4B6 build report: {exc}"
        ) from exc
    keys = {
        "status",
        "db_path",
        "snapshot_id",
        "parser_build_id",
        "ingest_run_id",
        "config_checksum",
        "database_fingerprint",
        "manifest",
        "counts",
        "family_counts",
        "family_selected_paths",
        "status_counts",
        "locale_breakdown",
        "occurrence_set_digests",
        "scope",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise Phase4B6VerificationError("Phase 4B6 build report keys mismatch")
    return value


def _selection_rule(manifest: Any) -> str | None:
    for value in (
        getattr(manifest, "selection_rule", None),
        getattr(manifest, "boundaries", {}).get("selection_rule"),
        getattr(manifest, "versions", {}).get("selection_rule"),
        getattr(manifest, "raw", {}).get("selection_rule"),
    ):
        if isinstance(value, str) and value:
            return value
    return None


def _validate_build_report(
    build: dict[str, Any],
    manifest: Any,
    identity: dict[str, Any],
    db_fingerprint: dict[str, Any],
    db_path: Path,
) -> None:
    if build["status"] != "complete":
        raise Phase4B6VerificationError("build report status is not complete")
    if Path(build["db_path"]).resolve() != db_path.resolve():
        raise Phase4B6VerificationError("build report database path mismatch")
    for key in ("snapshot_id", "parser_build_id", "ingest_run_id", "config_checksum"):
        if build[key] != identity[key]:
            raise Phase4B6VerificationError(f"build report {key} binding mismatch")
    if build["manifest"] != {
        "manifest_id": manifest.manifest_id,
        "raw_sha256": manifest.raw_sha256,
    }:
        raise Phase4B6VerificationError("build report manifest binding mismatch")

    expected_counts = {
        "files": manifest.expected["counts"]["selected_files"],
        "source_records": manifest.expected["counts"]["source_records"],
        "containers": manifest.expected["counts"]["containers"],
        "segments": manifest.expected["counts"]["segments"],
        "relationships": manifest.expected["counts"]["relationships"],
        "text_resolutions": manifest.expected["counts"]["text_resolutions"],
        "text_candidates": manifest.expected["counts"]["text_candidates"],
        "text_assets": manifest.expected["counts"]["text_assets"],
        "text_owner_occurrences": manifest.expected["counts"]["text_owner_occurrences"],
    }
    if build["counts"] != expected_counts:
        raise Phase4B6VerificationError("build report occurrence counts mismatch")
    expected_family_counts = manifest.expected.get("families")
    if expected_family_counts is None:
        expected_family_counts = manifest.expected.get("family_counts")
    if build["family_counts"] != expected_family_counts:
        raise Phase4B6VerificationError("build report family counts mismatch")
    expected_family_paths = manifest.expected.get("family_selected_paths")
    if expected_family_paths is None:
        expected_family_paths = {
            "materials": sorted(item.relative_path for item in manifest.files)
        }
    if build["family_selected_paths"] != expected_family_paths:
        raise Phase4B6VerificationError("build report family selected paths mismatch")
    expected_status_counts = manifest.expected.get("status_counts")
    if expected_status_counts is None:
        expected_status_counts = {
            "relationships": manifest.expected.get("relationship_statuses"),
            "text_resolutions": manifest.expected.get("text_resolution_statuses"),
        }
    if build["status_counts"] != expected_status_counts:
        raise Phase4B6VerificationError("build report status counts mismatch")
    if build["occurrence_set_digests"] != manifest.expected["occurrence_set_digests"]:
        raise Phase4B6VerificationError("build report occurrence digests mismatch")
    if build["locale_breakdown"] != manifest.expected.get("locale_breakdown", {}):
        raise Phase4B6VerificationError("build report locale breakdown mismatch")
    report_fingerprint = build["database_fingerprint"]
    if not isinstance(report_fingerprint, dict) or set(report_fingerprint) != {
        "sha256",
        "size_bytes",
        "mtime_ns",
    } or report_fingerprint != db_fingerprint:
        raise Phase4B6VerificationError("build report database fingerprint mismatch")

    expected_scope: dict[str, Any] = {
        "selected_paths": [item.relative_path for item in manifest.files],
        "full_static_scan": False,
        "fts_rebuilt": False,
        "logical_identity_created": False,
        "cross_language_alignment_created": False,
        "textmap_evidence_container_created": False,
    }
    selection_rule = _selection_rule(manifest)
    if selection_rule is not None:
        expected_scope["selection_rule"] = selection_rule
    if build["scope"] != expected_scope:
        raise Phase4B6VerificationError("build report scope boundary mismatch")


def verify_static_phase4b6_material_evidence_db(
    db_path: str | Path,
    build_report_path: str | Path,
    manifest_path: str | Path,
    source_root: str | Path,
) -> dict[str, Any]:
    """Verify B6 using only read-only SQLite access and independent digests."""

    target = Path(db_path)
    if not target.is_file():
        raise Phase4B6VerificationError(
            f"Phase 4B6 database does not exist: {target}"
        )
    before = _fingerprint(target)
    try:
        manifest = load_static_phase4b6_material_manifest(manifest_path)
        source_verification = verify_phase4b6_material_source_contract(source_root, manifest)
        build = _load_build_report(build_report_path)
        identity = expected_phase4b6_material_identity(manifest)
        _validate_build_report(build, manifest, identity, before, target)

        uri = target.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
            if int(conn.execute("PRAGMA query_only").fetchone()[0]) != 1:
                raise Phase4B6VerificationError("SQLite query_only could not be enabled")
            contract = verify_phase4b6_material_database_contract(
                conn,
                manifest,
                source_root,
            )
        finally:
            conn.close()

        if contract["identity"] != identity:
            raise Phase4B6VerificationError(
                "independent DB identity reconstruction mismatch"
            )
        for key in ("family_counts", "family_selected_paths", "status_counts"):
            if build[key] != contract[key]:
                raise Phase4B6VerificationError(
                    f"build report {key} disagrees with independent DB contract"
                )

        return {
            "status": "pass",
            "db_path": str(target.resolve()),
            "database_fingerprint": before,
            "manifest": {
                "manifest_id": manifest.manifest_id,
                "raw_sha256": manifest.raw_sha256,
            },
            "source": source_verification,
            "readonly": {
                "sqlite_uri_mode": "ro",
                "query_only": True,
                "fingerprint_unchanged": True,
            },
            **{
                key: contract[key]
                for key in (
                    "schema_version",
                    "counts",
                    "inventory_counts",
                    "family_counts",
                    "family_inventory_counts",
                    "family_selected_paths",
                    "status_counts",
                    "locale_breakdown",
                    "occurrence_set_digests",
                    "foreign_key_check",
                )
            },
        }
    except Phase4B6VerificationError:
        raise
    except (
        Phase4B6ContractError,
        sqlite3.Error,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
    ) as exc:
        raise Phase4B6VerificationError(str(exc)) from exc
    finally:
        after = _fingerprint(target)
        if after != before:
            raise Phase4B6VerificationError(
                f"verifier mutated database fingerprint: before={before} after={after}"
            )


__all__ = [
    "Phase4B6VerificationError",
    "verify_static_phase4b6_material_evidence_db",
]
