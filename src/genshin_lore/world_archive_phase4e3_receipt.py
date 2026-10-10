"""External release receipt for the verified Phase 4E3 World Archive DB."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .world_archive_phase4e2_promotion import assert_report_path_safe, sync_directory
from .world_archive_phase4e2_stable_lock import promotion_lock_path
from .world_archive_phase4e3_contract import (
    EXPECTED_COVERAGE,
    EXPECTED_MANIFEST_SHA256,
    FAMILY,
    FAMILY_CONTRACT_VERSION,
    expected_identity,
    load_manifest,
)


RECEIPT_CONTRACT = "genshin-lore-world-archive-phase4e3-release-receipt"
RECEIPT_VERSION = 2
SCHEMA16_RECEIPT_SCHEMA_CONTRACT = "schema16-migration0016-additive-wave01-exact"
SCHEMA17_RECEIPT_SCHEMA_CONTRACT = "schema17-migration0017-additive-wave02-exact"
SCHEMA19_RECEIPT_SCHEMA_CONTRACT = "schema19-migrations0018-0019-additive-r2-exact"
SCHEMA20_RECEIPT_SCHEMA_CONTRACT = "schema20-migration0020-additive-r4-exact"
VERIFIER_ORACLE = "frozen_phase4e1_source_coordinate_and_exact_sql_v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class WorldArchiveReleaseReceiptError(RuntimeError):
    """The external release receipt is absent, malformed, or not exact."""


def _same_path(left: Path, right: Path) -> bool:
    try:
        if left.resolve(strict=False) == right.resolve(strict=False):
            return True
    except (OSError, RuntimeError):
        pass
    try:
        return os.path.samefile(left, right)
    except (FileNotFoundError, OSError):
        return False


def assert_release_receipt_path_safe(
    receipt_path: str | Path,
    database_path: str | Path,
    build_report_path: str | Path,
) -> None:
    target = Path(os.path.abspath(os.fspath(receipt_path)))
    database = Path(os.path.abspath(os.fspath(database_path)))
    report = Path(os.path.abspath(os.fspath(build_report_path)))
    forbidden = {
        database,
        promotion_lock_path(database),
        *(Path(f"{database}{suffix}") for suffix in ("-journal", "-wal", "-shm")),
    }
    if any(_same_path(target, item) for item in forbidden):
        raise WorldArchiveReleaseReceiptError(
            "release receipt aliases database, sidecar, or stable lock"
        )
    if _same_path(target, report):
        raise WorldArchiveReleaseReceiptError("release receipt aliases the build report")


def _strict_json(raw: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise WorldArchiveReleaseReceiptError(f"duplicate receipt key: {key}")
            value[key] = item
        return value

    def constant(item: str) -> Any:
        raise WorldArchiveReleaseReceiptError(f"non-standard receipt constant: {item}")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except WorldArchiveReleaseReceiptError:
        raise
    except (TypeError, json.JSONDecodeError) as exc:
        raise WorldArchiveReleaseReceiptError("invalid release receipt JSON") from exc


def release_receipt(
    manifest: Mapping[str, Any], database_fingerprint: Mapping[str, Any],
    *, schema_version: int = 12,
) -> dict[str, Any]:
    """Return the deterministic receipt body for one verified DB byte stream."""
    if schema_version not in (12, 13, 16, 17, 19, 20):
        raise WorldArchiveReleaseReceiptError("unsupported combined schema version")
    sha256 = database_fingerprint.get("sha256")
    size = database_fingerprint.get("size_bytes")
    if not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None:
        raise WorldArchiveReleaseReceiptError("receipt database SHA-256 is invalid")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise WorldArchiveReleaseReceiptError("receipt database size is invalid")
    identity = expected_identity(dict(manifest))
    source = manifest["source"]
    return {
        "receipt_contract": RECEIPT_CONTRACT,
        "receipt_version": RECEIPT_VERSION,
        "trust_semantics": "release_gate_result_and_database_file_prefilter_not_authentication",
        "verification": {
            "status": "pass",
            "oracle": VERIFIER_ORACLE,
            "schema_contract": {
                12: "schema12-migration0012-exact",
                13: "schema13-migration0013-exact",
                16: SCHEMA16_RECEIPT_SCHEMA_CONTRACT,
                17: SCHEMA17_RECEIPT_SCHEMA_CONTRACT,
                19: SCHEMA19_RECEIPT_SCHEMA_CONTRACT,
                20: SCHEMA20_RECEIPT_SCHEMA_CONTRACT,
            }[schema_version],
        },
        "family": FAMILY,
        "family_contract_version": FAMILY_CONTRACT_VERSION,
        "release": {
            "ingest_run_id": identity["ingest_run_id"],
            "snapshot_id": identity["snapshot_id"],
            "parser_build_id": identity["parser_build_id"],
            "manifest_id": manifest["manifest_id"],
            "manifest_raw_sha256": EXPECTED_MANIFEST_SHA256,
            "source_id": source["source_id"],
            "source_commit": source["commit"],
            "source_tree": source["tree"],
            "coverage": EXPECTED_COVERAGE,
            "corpus_complete": True,
            "evidence_only": True,
        },
        "release_gate_reported_seals": {
            "counts": manifest["expected"]["counts"],
            "status_counts": manifest["expected"]["status_counts"],
            "occurrence_set_digests": manifest["expected"]["occurrence_set_digests"],
            "exact_business_tuples": True,
            "foreign_keys_clean": True,
            "sidecars_absent": True,
        },
        "database": {"size_bytes": size, "sha256": sha256},
    }


def load_release_receipt(
    receipt_path: str | Path, manifest_path: str | Path
) -> dict[str, Any]:
    """Load and byte-contract validate an explicitly selected receipt."""
    try:
        raw_bytes = Path(receipt_path).read_bytes()
        raw = raw_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise WorldArchiveReleaseReceiptError("release receipt is unavailable") from exc
    parsed = _strict_json(raw)
    if not isinstance(parsed, dict):
        raise WorldArchiveReleaseReceiptError("release receipt root must be an object")
    database = parsed.get("database")
    if not isinstance(database, dict):
        raise WorldArchiveReleaseReceiptError("release receipt database binding is invalid")
    manifest = load_manifest(manifest_path)
    verification = parsed.get("verification")
    schema_contract = verification.get("schema_contract") if isinstance(verification, dict) else None
    if schema_contract == "schema12-migration0012-exact":
        schema_version = 12
    elif schema_contract == "schema13-migration0013-exact":
        schema_version = 13
    elif schema_contract == SCHEMA16_RECEIPT_SCHEMA_CONTRACT:
        schema_version = 16
    elif schema_contract == SCHEMA17_RECEIPT_SCHEMA_CONTRACT:
        schema_version = 17
    elif schema_contract == SCHEMA20_RECEIPT_SCHEMA_CONTRACT:
        schema_version = 20
    elif schema_contract == SCHEMA19_RECEIPT_SCHEMA_CONTRACT:
        schema_version = 19
    else:
        raise WorldArchiveReleaseReceiptError("release receipt schema contract is invalid")
    expected = release_receipt(manifest, database, schema_version=schema_version)
    canonical = (
        json.dumps(
            expected, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
        ) + "\n"
    ).encode("utf-8")
    if parsed != expected or raw_bytes != canonical:
        raise WorldArchiveReleaseReceiptError("release receipt contract mismatch")
    return expected


def write_release_receipt_atomically(
    receipt_path: str | Path,
    database_path: str | Path,
    build_report_path: str | Path,
    receipt: Mapping[str, Any],
) -> None:
    """Publish a release-gate file binding using file and directory fsync."""
    target = Path(os.path.abspath(os.fspath(receipt_path)))
    database = Path(os.path.abspath(os.fspath(database_path)))
    assert_release_receipt_path_safe(target, database, build_report_path)
    payload = json.dumps(
        receipt, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    ) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{target.name}.phase4e3-", suffix=".receipt", dir=target.parent
    )
    staging = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        assert_report_path_safe(target, database)
        assert_release_receipt_path_safe(target, database, build_report_path)
        os.replace(staging, target)
        sync_directory(target.parent)
    finally:
        try:
            staging.unlink()
        except FileNotFoundError:
            pass


__all__ = [
    "RECEIPT_CONTRACT",
    "RECEIPT_VERSION",
    "SCHEMA16_RECEIPT_SCHEMA_CONTRACT",
    "SCHEMA17_RECEIPT_SCHEMA_CONTRACT",
    "VERIFIER_ORACLE",
    "WorldArchiveReleaseReceiptError",
    "assert_release_receipt_path_safe",
    "load_release_receipt",
    "release_receipt",
    "write_release_receipt_atomically",
]
