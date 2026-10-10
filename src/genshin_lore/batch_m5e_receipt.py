"""Strict M5E Schema17 wave receipt and file-binding helpers.

The receipt is a release-gate result, never an authentication root. Runtime
qualification independently verifies the tracked manifest and row/provenance
seals before registering providers. This module deliberately carries only
family-specific occurrence bindings; it does not define a shared ontology.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .batch_m4_release import SNAPSHOT_ID
from .batch_m4_runtime_oracle import M4_FAMILY_ORDER
from .batch_m5d_runtime import M5D_FAMILY_ORDER
from .batch_release import BatchReleaseError, assert_output_paths, publish_absent
from .batch_m4_receipt import publish_release_bundle_absent as _publish_release_bundle_absent
from .util import canonical_json, sha256_bytes

M5E_SCHEMA_VERSION = 17
M5E_SCHEMA_CONTRACT = "schema17-migration0017-additive-wave02-exact"
M5E_WAVE_CONTRACT = "batch-wave-v5"
M5E_WAVE_ID = "batch-m5e-wave02"
M5E_RECEIPT_CONTRACT = "genshin-lore-batch-m5e-wave02-release-receipt"
M5E_RECEIPT_VERSION = 1
M5E_FAMILY_ORDER = (*M4_FAMILY_ORDER, *M5D_FAMILY_ORDER)
M5E_COMPATIBILITY_ORDER = ("world_archive", "quest_codex")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class BatchM5EReceiptError(RuntimeError):
    """An M5E receipt is absent, malformed, or not bound to its inputs."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_identity(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    try:
        info = os.lstat(target)
    except OSError as exc:
        raise BatchM5EReceiptError(f"database is unavailable: {target}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise BatchM5EReceiptError("database must be a regular non-symlink file")
    try:
        digest = sha256_file(target)
    except OSError as exc:
        raise BatchM5EReceiptError(f"database is unavailable: {target}") from exc
    return {"size_bytes": int(info.st_size), "sha256": digest}


def _validated_database_identity(value: Mapping[str, Any], label: str = "database identity") -> dict[str, Any]:
    """Validate an already-precomputed exact-file identity.

    This helper is intentionally only used by the internal qualification fast
    path.  The public/default receipt loader still calls ``_file_identity``
    and streams the selected database itself.
    """
    if not isinstance(value, Mapping):
        raise BatchM5EReceiptError(f"{label} must be an object")
    size = value.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise BatchM5EReceiptError(f"{label}.size_bytes is invalid")
    digest = _sha(value.get("sha256"), f"{label}.sha256")
    return {"size_bytes": size, "sha256": digest}


def _sha(value: Any, label: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise BatchM5EReceiptError(f"{label} must be a lowercase SHA-256")
    return value


def _simple_name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or Path(value).name != value:
        raise BatchM5EReceiptError(f"{label} must be a sibling filename")
    return value


def _ordered_bindings(value: Mapping[str, Any] | None, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(M5E_FAMILY_ORDER):
        raise BatchM5EReceiptError(f"{label} must bind the fixed 21-family set")
    if any(not isinstance(key, str) or not key for key in value):
        raise BatchM5EReceiptError(f"{label} contains an invalid family")
    # Canonical JSON sorts object keys. Normalize semantic bindings to the
    # contract order after every JSON round trip.
    return {family: value[family] for family in M5E_FAMILY_ORDER}


def _table_seal(value: Any, family: str, ordinal: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BatchM5EReceiptError(f"{family}.tables[{ordinal}] must be an object")
    table_name = value.get("table_name")
    if not isinstance(table_name, str) or not table_name:
        raise BatchM5EReceiptError(f"{family}.tables[{ordinal}].table_name is invalid")
    row_count = value.get("row_count")
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
        raise BatchM5EReceiptError(f"{family}.tables[{ordinal}].row_count is invalid")
    logical = _sha(value.get("logical_row_sha256"), f"{family}.tables[{ordinal}].logical_row_sha256")
    return {"table_name": table_name, "row_count": row_count, "logical_row_sha256": logical}


def _family_seals(value: Mapping[str, Any] | None, *, required: bool) -> dict[str, Any]:
    if value is None or (value == {} and not required):
        if required:
            raise BatchM5EReceiptError("accepted M5E receipt requires family seals")
        return {}
    result = _ordered_bindings(value, "family_seals")
    allowed = {"business_rows", "provenance", "tables", "row_count"}
    normalized: dict[str, Any] = {}
    for family, seal in result.items():
        if not isinstance(seal, Mapping):
            raise BatchM5EReceiptError(f"family seal is not an object: {family}")
        if set(seal) - allowed:
            raise BatchM5EReceiptError(f"family seal has unknown fields: {family}")
        business = _sha(seal.get("business_rows"), f"{family}.business_rows", required=required)
        provenance = _sha(seal.get("provenance"), f"{family}.provenance", required=required)
        row_count = seal.get("row_count")
        if row_count is not None and (isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0):
            raise BatchM5EReceiptError(f"{family}.row_count is invalid")
        tables = seal.get("tables", [])
        if not isinstance(tables, list):
            raise BatchM5EReceiptError(f"{family}.tables must be an array")
        normalized[family] = {
            "business_rows": business,
            "provenance": provenance,
            "tables": [_table_seal(item, family, index) for index, item in enumerate(tables)],
            "row_count": row_count,
        }
    return normalized


def _compatibility_receipts(value: Mapping[str, Any] | None, *, required: bool) -> dict[str, Any]:
    if value is None or (value == {} and not required):
        if required:
            raise BatchM5EReceiptError("accepted M5E receipt requires compatibility receipts")
        return {}
    if not isinstance(value, Mapping) or set(value) != set(M5E_COMPATIBILITY_ORDER):
        raise BatchM5EReceiptError("compatibility receipt set is not fixed")
    result: dict[str, Any] = {}
    for name in M5E_COMPATIBILITY_ORDER:
        item = value[name]
        if not isinstance(item, Mapping):
            raise BatchM5EReceiptError(f"compatibility receipt is not an object: {name}")
        result[name] = {
            "filename": _simple_name(item.get("filename"), f"{name}.filename"),
            "sha256": _sha(item.get("sha256"), f"{name}.sha256"),
        }
    return result


def _receipt_id(payload: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json(dict(payload)).encode("utf-8"))


def release_receipt(
    database: str | Path,
    *,
    wave_id: str = M5E_WAVE_ID,
    wave_manifest_sha256: str,
    family_runs: Mapping[str, str],
    family_contracts: Mapping[str, str],
    family_seals: Mapping[str, Any] | None = None,
    runtime_manifest_sha256: str | None = None,
    independent_verification_sha256: str | None = None,
    report_sha256: str | None = None,
    report_filename: str | None = None,
    compatibility_receipts: Mapping[str, Any] | None = None,
    product_accepted: bool = True,
    stdio_accepted: bool = False,
    _database_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a deterministic receipt body for one exact Schema17 file."""
    if not isinstance(wave_id, str) or not wave_id:
        raise BatchM5EReceiptError("M5E wave id is invalid")
    if not isinstance(product_accepted, bool) or not isinstance(stdio_accepted, bool):
        raise BatchM5EReceiptError("M5E acceptance flags must be booleans")
    if stdio_accepted and not product_accepted:
        raise BatchM5EReceiptError("candidate M5E receipt cannot report accepted stdio")
    manifest_sha = _sha(wave_manifest_sha256, "wave_manifest_sha256")
    runtime_sha = _sha(runtime_manifest_sha256 if runtime_manifest_sha256 is not None else manifest_sha, "runtime_manifest_sha256")
    verification_sha = _sha(independent_verification_sha256, "independent_verification_sha256", required=product_accepted)
    report_sha = _sha(report_sha256, "report_sha256", required=product_accepted)
    if report_filename is not None:
        report_filename = _simple_name(report_filename, "report_filename")
    if product_accepted and report_filename is None:
        raise BatchM5EReceiptError("accepted M5E receipt requires a report filename")
    if report_filename is not None and report_sha is None:
        raise BatchM5EReceiptError("report filename requires a report SHA-256")
    runs = _ordered_bindings(family_runs, "family_runs")
    contracts = _ordered_bindings(family_contracts, "family_contracts")
    if any(not isinstance(value, str) or not value for value in runs.values()):
        raise BatchM5EReceiptError("family run binding is invalid")
    if any(not isinstance(value, str) or not value for value in contracts.values()):
        raise BatchM5EReceiptError("family contract binding is invalid")
    seals = _family_seals(family_seals, required=product_accepted)
    companions = _compatibility_receipts(compatibility_receipts, required=product_accepted)
    database_identity = (
        _validated_database_identity(_database_identity)
        if _database_identity is not None else _file_identity(database)
    )
    identity_input = {
        "database": database_identity,
        "schema_version": M5E_SCHEMA_VERSION,
        "schema_contract": M5E_SCHEMA_CONTRACT,
        "snapshot_id": SNAPSHOT_ID,
        "wave_contract": M5E_WAVE_CONTRACT,
        "wave_id": wave_id,
        "wave_manifest_sha256": manifest_sha,
        "runtime_manifest_sha256": runtime_sha,
        "independent_verification_sha256": verification_sha,
        "report_sha256": report_sha,
        "report_filename": report_filename,
        "family_runs": runs,
        "family_contracts": contracts,
        "family_seals": seals,
        "compatibility_receipts": companions,
        "product_accepted": product_accepted,
        "stdio_accepted": stdio_accepted,
    }
    return {
        "receipt_contract": M5E_RECEIPT_CONTRACT,
        "receipt_version": M5E_RECEIPT_VERSION,
        "receipt_id": _receipt_id(identity_input),
        "schema_version": M5E_SCHEMA_VERSION,
        "schema_contract": M5E_SCHEMA_CONTRACT,
        "snapshot_id": SNAPSHOT_ID,
        "wave_contract": M5E_WAVE_CONTRACT,
        "wave_id": wave_id,
        "wave_manifest_sha256": manifest_sha,
        "runtime_manifest_sha256": runtime_sha,
        "independent_verification_sha256": verification_sha,
        "database": {"path_metadata": "machine-local", **database_identity},
        "family_order": list(M5E_FAMILY_ORDER),
        "family_runs": runs,
        "family_contracts": contracts,
        "coverage_level": "R2_wave_accepted_product" if product_accepted else "R2_wave_candidate",
        "corpus_complete": False,
        "wave01_corpus_complete": False,
        "wave02_corpus_complete": False,
        "profile_picture_coverage": "bounded_canary",
        "product_accepted": product_accepted,
        "stdio_accepted": stdio_accepted,
        "report_sha256": report_sha,
        "report": ({"path_metadata": "machine-local", "filename": report_filename} if report_filename is not None else None),
        "family_seals": seals,
        "compatibility_receipts": companions,
        "release_gate_reported_seals": {
            "family_seals": seals,
            "foreign_keys_clean": True,
            "sidecars_absent": True,
            "exact_tool_order": True,
            "top_level_tool_count": 25,
        },
        "identity_policy": "raw_evidence_identity_is_not_logical_identity; no cross-family merge",
        "mcp_tool_count": 25,
        "receipt_trust": "file_prefilter_not_authentication; tracked_runtime_oracle_is_authoritative",
    }


def _strict_json(raw: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise BatchM5EReceiptError(f"duplicate M5E receipt key: {key}")
            result[key] = item
        return result
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except BatchM5EReceiptError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise BatchM5EReceiptError("M5E release receipt is unavailable or invalid") from exc


def _canonical_receipt(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_release_receipt(
    path: str | Path,
    database: str | Path,
    *,
    require_accepted: bool = True,
    report_path: str | Path | None = None,
    companion_paths: Mapping[str, str | Path] | None = None,
    _prevalidated_database_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load and validate an explicit receipt and exact database binding."""
    receipt_path = Path(path)
    try:
        raw = receipt_path.read_bytes()
    except OSError as exc:
        raise BatchM5EReceiptError("M5E release receipt is unavailable") from exc
    value = _strict_json(raw)
    if not isinstance(value, dict):
        raise BatchM5EReceiptError("M5E release receipt must be a JSON object")
    if value.get("receipt_contract") != M5E_RECEIPT_CONTRACT or value.get("receipt_version") != M5E_RECEIPT_VERSION:
        raise BatchM5EReceiptError("M5E receipt contract mismatch")
    if value.get("schema_version") != M5E_SCHEMA_VERSION or value.get("schema_contract") != M5E_SCHEMA_CONTRACT:
        raise BatchM5EReceiptError("M5E receipt schema mismatch")
    accepted = value.get("product_accepted") is True
    if require_accepted and not accepted:
        raise BatchM5EReceiptError("M5E release receipt is not product accepted")
    report = value.get("report")
    report_sha = value.get("report_sha256")
    report_filename = None
    if report is not None:
        if not isinstance(report, Mapping):
            raise BatchM5EReceiptError("M5E receipt report binding is invalid")
        report_filename = _simple_name(report.get("filename"), "report.filename")
    if accepted and (report_filename is None or report_sha is None):
        raise BatchM5EReceiptError("accepted M5E receipt has no report binding")
    if report_filename is not None and report_sha is None:
        raise BatchM5EReceiptError("M5E report filename has no SHA-256")
    try:
        expected = release_receipt(
            database,
            wave_id=value.get("wave_id"),
            wave_manifest_sha256=value.get("wave_manifest_sha256"),
            runtime_manifest_sha256=value.get("runtime_manifest_sha256"),
            independent_verification_sha256=value.get("independent_verification_sha256"),
            family_runs=value.get("family_runs"),
            family_contracts=value.get("family_contracts"),
            family_seals=(
                value.get("family_seals")
                if value.get("family_seals") not in ({}, None)
                else (value.get("release_gate_reported_seals") or {}).get("family_seals")
            ),
            report_sha256=report_sha,
            report_filename=report_filename,
            compatibility_receipts=value.get("compatibility_receipts"),
            product_accepted=accepted,
            stdio_accepted=value.get("stdio_accepted") is True,
            _database_identity=_prevalidated_database_identity,
        )
    except (BatchM5EReceiptError, TypeError, AttributeError) as exc:
        if isinstance(exc, BatchM5EReceiptError):
            raise
        raise BatchM5EReceiptError("M5E receipt fields are invalid") from exc
    if value != expected or raw != _canonical_receipt(expected):
        raise BatchM5EReceiptError("M5E receipt contract mismatch")
    if report_filename is not None:
        selected_report = receipt_path.parent / report_filename
        if report_path is not None:
            selected_report = Path(report_path)
        try:
            if selected_report.parent.resolve() != receipt_path.parent.resolve() or selected_report.name != report_filename:
                raise BatchM5EReceiptError("M5E report is not the bound sibling")
            if not selected_report.is_file() or selected_report.is_symlink() or sha256_file(selected_report) != report_sha:
                raise BatchM5EReceiptError("M5E report binding mismatch")
        except OSError as exc:
            raise BatchM5EReceiptError("M5E release report is unavailable") from exc
    if companion_paths is not None:
        if not isinstance(companion_paths, Mapping):
            raise BatchM5EReceiptError("companion_paths must be an object")
        for name, selected in companion_paths.items():
            if name not in expected["compatibility_receipts"]:
                raise BatchM5EReceiptError(f"unexpected compatibility receipt: {name}")
            item = expected["compatibility_receipts"][name]
            selected_path = Path(selected)
            try:
                if selected_path.name != item["filename"] or selected_path.is_symlink() or sha256_file(selected_path) != item["sha256"]:
                    raise BatchM5EReceiptError(f"compatibility receipt binding mismatch: {name}")
            except OSError as exc:
                raise BatchM5EReceiptError(f"compatibility receipt is unavailable: {name}") from exc
    return expected


def write_receipt_absent(
    path: str | Path,
    receipt: Mapping[str, Any],
    *,
    database: str | Path,
    report: str | Path | None = None,
) -> None:
    """Write one receipt atomically to an absent target."""
    target = Path(path)
    inputs: list[tuple[str, Path]] = [("database", Path(database))]
    if report is not None:
        inputs.append(("report", Path(report)))
    try:
        assert_output_paths(target, inputs=inputs, target_must_be_absent=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.m5e-", suffix=".receipt", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(_canonical_receipt(receipt))
                handle.flush()
                os.fsync(handle.fileno())
            publish_absent(target, temporary, inputs=inputs)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    except (OSError, BatchReleaseError) as exc:
        raise BatchM5EReceiptError(f"cannot publish M5E receipt: {exc}") from exc


def publish_release_bundle_absent(
    report_path: str | Path,
    report: Mapping[str, Any],
    receipt_path: str | Path,
    receipt: Mapping[str, Any],
    *,
    database: str | Path,
    extra_outputs: Sequence[tuple[str | Path, Mapping[str, Any]]] = (),
) -> None:
    """Publish an M5E report/receipt and arbitrary same-directory outputs.

    The shared release primitive performs absent-target checks, alias and
    SQLite-sidecar checks, one stable sibling lock, private same-directory
    staging, fsync-backed hard-link promotion, and owned-inode rollback.
    """
    try:
        _publish_release_bundle_absent(
            report_path, report, receipt_path, receipt,
            database=database, extra_outputs=extra_outputs,
        )
    except Exception as exc:
        if isinstance(exc, BatchM5EReceiptError):
            raise
        raise BatchM5EReceiptError(f"cannot publish M5E release bundle: {exc}") from exc

__all__ = [
    "M5E_SCHEMA_VERSION", "M5E_SCHEMA_CONTRACT", "M5E_WAVE_CONTRACT", "M5E_WAVE_ID",
    "M5E_RECEIPT_CONTRACT", "M5E_RECEIPT_VERSION", "M5E_FAMILY_ORDER",
    "M5E_COMPATIBILITY_ORDER", "BatchM5EReceiptError", "sha256_file",
    "release_receipt", "load_release_receipt", "write_receipt_absent",
    "publish_release_bundle_absent",
]
