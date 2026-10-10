"""Exact-file prefilter receipt for the Batch M4 wave candidate.

The receipt is a release-gate artifact, not an authentication root.  Runtime
still checks the database identity and the family/run bindings before exposing
the routed provider.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from .batch_m4_release import (
    M4_FAMILY_ORDER,
    M4_REPORT_VERSION,
    SCHEMA_VERSION,
    SNAPSHOT_ID,
    _m4_wave_id,
)
from .batch_release import (
    BatchReleaseError, fsync_file, publish_absent, assert_output_paths,
    stable_sibling_lock, sync_directory, sidecars,
)
from .util import canonical_json, sha256_bytes


RECEIPT_CONTRACT = "genshin-lore-batch-m4-wave-release-receipt"
RECEIPT_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class BatchM4ReceiptError(RuntimeError):
    """An M4 receipt is malformed or does not match its database."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_identity(path: Path) -> dict[str, Any]:
    try:
        info = path.stat()
    except OSError as exc:
        raise BatchM4ReceiptError(f"database is unavailable: {path}") from exc
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise BatchM4ReceiptError("database must be a regular non-symlink file")
    return {"size_bytes": int(info.st_size), "sha256": sha256_file(path)}


def _receipt_id(
    database_identity: Mapping[str, Any],
    wave_id: str,
    wave_manifest_sha256: str,
    family_runs: Mapping[str, str],
    family_contracts: Mapping[str, str],
) -> str:
    return sha256_bytes(canonical_json({
        "database": dict(database_identity),
        "wave_id": wave_id,
        "wave_manifest_sha256": wave_manifest_sha256,
        "family_runs": dict(family_runs),
        "family_contracts": dict(family_contracts),
    }).encode("utf-8"))


def _serialized_json(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def release_receipt(
    database: str | Path,
    *,
    wave_id: str,
    wave_manifest_sha256: str,
    family_runs: Mapping[str, str],
    family_contracts: Mapping[str, str],
    report_sha256: str | None = None,
    report_filename: str | None = None,
    product_accepted: bool = True,
) -> dict[str, Any]:
    """Create a deterministic receipt from a verified candidate and bindings."""

    path = Path(database).resolve()
    if tuple(family_runs) != M4_FAMILY_ORDER or tuple(family_contracts) != M4_FAMILY_ORDER:
        raise BatchM4ReceiptError("M4 receipt family order is not the fixed wave order")
    if not all(isinstance(value, str) and value for value in family_runs.values()):
        raise BatchM4ReceiptError("M4 receipt family run bindings are invalid")
    if not all(isinstance(value, str) and value for value in family_contracts.values()):
        raise BatchM4ReceiptError("M4 receipt family contract bindings are invalid")
    if product_accepted and (not isinstance(report_sha256, str) or len(report_sha256) != 64):
        raise BatchM4ReceiptError("accepted M4 receipt requires a report SHA-256")
    if report_filename is not None:
        if not isinstance(report_filename, str) or not report_filename or Path(report_filename).name != report_filename:
            raise BatchM4ReceiptError("M4 report filename must be a simple sibling name")
    database_identity = _file_identity(path)
    payload: dict[str, Any] = {
        "receipt_contract": RECEIPT_CONTRACT,
        "receipt_version": RECEIPT_VERSION,
        "receipt_id": _receipt_id(
            database_identity, wave_id, wave_manifest_sha256,
            family_runs, family_contracts,
        ),
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": SNAPSHOT_ID,
        "wave_id": wave_id,
        "wave_manifest_sha256": wave_manifest_sha256,
        "database": {"path_metadata": "machine-local", **database_identity},
        "family_runs": dict(family_runs),
        "family_contracts": dict(family_contracts),
        "coverage_level": "R2_wave_accepted_product" if product_accepted else "R2_wave_candidate",
        "corpus_complete": False,
        "product_accepted": bool(product_accepted),
        "report_sha256": report_sha256,
        "report": (
            {"path_metadata": "machine-local", "filename": report_filename}
            if report_filename is not None else None
        ),
        "identity_policy": "raw_evidence_identity_is_not_logical_identity; no cross-family merge",
        "mcp_tool_count": 25,
        "receipt_trust": "file_prefilter_not_authentication",
    }
    return payload


def write_receipt_absent(
    path: str | Path,
    receipt: Mapping[str, Any],
    *,
    database: str | Path,
    report: str | Path | None = None,
) -> None:
    """Write one receipt atomically to an absent path."""

    target = Path(path).resolve()
    inputs = [("database", Path(database).resolve())]
    if report is not None:
        inputs.append(("report", Path(report).resolve()))
    try:
        assert_output_paths(target, inputs=inputs, target_must_be_absent=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.m4-", suffix=".receipt", dir=target.parent
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
            data = (json.dumps(dict(receipt), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(data)
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
        raise BatchM4ReceiptError(f"cannot publish M4 receipt: {exc}") from exc



def _write_private_json(path: Path, value: Mapping[str, Any], prefix: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=".json", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        data = _serialized_json(value)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        sync_directory(path.parent)
        return temporary
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _owned_inode(path: Path) -> tuple[int, int] | None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    return int(info.st_dev), int(info.st_ino)


def _remove_owned(path: Path, identity: tuple[int, int] | None) -> None:
    if identity is None:
        return
    current = _owned_inode(path)
    if current == identity:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def publish_release_bundle_absent(
    report_path: str | Path,
    report: Mapping[str, Any],
    receipt_path: str | Path,
    receipt: Mapping[str, Any],
    *,
    database: str | Path,
    extra_outputs: Sequence[tuple[str | Path, Mapping[str, Any]]] = (),
) -> None:
    """Publish a report, receipt, and optional compatibility receipts together.

    Every output is prepared as a private 0600 same-directory inode before the
    single stable sibling lock is acquired.  Public names are hard-linked in a
    deterministic order and any failure removes only inodes created by this
    invocation; pre-existing competitors are never replaced.
    """
    report_target = Path(report_path).resolve()
    receipt_target = Path(receipt_path).resolve()
    database_path = Path(database).resolve()
    outputs: list[tuple[Path, Mapping[str, Any]]] = [
        (report_target, report), (receipt_target, receipt),
    ]
    for path, value in extra_outputs:
        outputs.append((Path(path).resolve(), value))
    targets = [path for path, _ in outputs]
    if report_target.parent != receipt_target.parent:
        raise BatchM4ReceiptError("M4 report and receipt must share a directory")
    if report_target == receipt_target:
        raise BatchM4ReceiptError("M4 report and receipt paths must differ")
    if len(set(targets)) != len(targets):
        raise BatchM4ReceiptError("M4 release output paths must be distinct")
    if any(path.parent != report_target.parent for path in targets):
        raise BatchM4ReceiptError("M4 coordinated release outputs must share a directory")
    report_sha256 = hashlib.sha256(_serialized_json(report)).hexdigest()
    if receipt.get("report_sha256") != report_sha256:
        raise BatchM4ReceiptError("M4 receipt report SHA-256 does not bind the report bytes")
    report_binding = receipt.get("report")
    if not isinstance(report_binding, Mapping) or report_binding.get("filename") != report_target.name:
        raise BatchM4ReceiptError("M4 receipt report filename does not bind the sibling report")
    database_identity = _file_identity(database_path)
    receipt_database = receipt.get("database")
    if not isinstance(receipt_database, Mapping) or (
        receipt_database.get("size_bytes") != database_identity["size_bytes"]
        or receipt_database.get("sha256") != database_identity["sha256"]
    ):
        raise BatchM4ReceiptError("M4 receipt database binding does not match the candidate")
    staging: list[tuple[Path, Path]] = []
    owned: list[tuple[Path, tuple[int, int] | None]] = []
    try:
        for index, target in enumerate(targets):
            inputs: list[tuple[str, Path]] = [("database", database_path)]
            inputs.extend((f"output_{other}", other_target) for other, other_target in enumerate(targets) if other != index)
            assert_output_paths(target, inputs=inputs, target_must_be_absent=True)
        for index, (target, value) in enumerate(outputs):
            staging.append((target, _write_private_json(target, value, f".{target.name}.m4-{index}-")))
        lock_inputs: list[tuple[str, Path]] = [("database", database_path)]
        lock_inputs.extend((f"output_{index}", target) for index, target in enumerate(targets) if index != 0)
        lock_inputs.extend((f"staging_{index}", item[1]) for index, item in enumerate(staging))
        with stable_sibling_lock(report_target, inputs=lock_inputs):
            for index, target in enumerate(targets):
                inputs = [("database", database_path)]
                inputs.extend((f"output_{other}", other_target) for other, other_target in enumerate(targets) if other != index)
                inputs.extend((f"staging_{other}", item[1]) for other, item in enumerate(staging))
                assert_output_paths(target, inputs=inputs, target_must_be_absent=True)
            for target, temporary in staging:
                os.link(temporary, target, follow_symlinks=False)
                identity = _owned_inode(target)
                owned.append((target, identity))
                temporary.unlink()
            staging = []
            sync_directory(report_target.parent)
    except (OSError, BatchReleaseError) as exc:
        for target, identity in reversed(owned):
            _remove_owned(target, identity)
        try:
            sync_directory(report_target.parent)
        except OSError:
            pass
        raise BatchM4ReceiptError(f"cannot publish M4 report/receipt bundle: {exc}") from exc
    finally:
        for _, temporary in staging:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def load_release_receipt(
    path: str | Path,
    database: str | Path,
    *,
    require_accepted: bool = True,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate the receipt contract and exact database file fingerprint."""

    receipt_path = Path(path).resolve()
    database_path = Path(database).resolve()
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise BatchM4ReceiptError(f"duplicate M4 receipt key: {key}")
            result[key] = item
        return result
    try:
        raw_receipt = receipt_path.read_bytes()
        value = json.loads(
            raw_receipt.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
        )
    except BatchM4ReceiptError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise BatchM4ReceiptError("M4 release receipt is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise BatchM4ReceiptError("M4 release receipt must be a JSON object")
    if value.get("receipt_contract") != RECEIPT_CONTRACT or value.get("receipt_version") != RECEIPT_VERSION:
        raise BatchM4ReceiptError("M4 release receipt contract mismatch")
    if value.get("schema_version") != SCHEMA_VERSION or value.get("snapshot_id") != SNAPSHOT_ID:
        raise BatchM4ReceiptError("M4 release receipt snapshot/schema mismatch")
    if require_accepted and value.get("product_accepted") is not True:
        raise BatchM4ReceiptError("M4 release receipt is not product accepted")
    report_sha256 = value.get("report_sha256")
    report_binding = value.get("report")
    if report_sha256 is not None and (
        not isinstance(report_sha256, str) or _SHA256.fullmatch(report_sha256) is None
    ):
        raise BatchM4ReceiptError("M4 receipt report SHA-256 is invalid")
    if report_binding is not None:
        if not isinstance(report_binding, Mapping):
            raise BatchM4ReceiptError("M4 receipt report binding is invalid")
        filename = report_binding.get("filename")
        if not isinstance(filename, str) or not filename or Path(filename).name != filename:
            raise BatchM4ReceiptError("M4 receipt report filename is invalid")
        if report_path is None:
            report_path = receipt_path.parent / filename
        else:
            report_path = Path(report_path).resolve()
            if report_path.parent != receipt_path.parent or report_path.name != filename:
                raise BatchM4ReceiptError("M4 receipt report is not the bound sibling")
    if require_accepted and (
        report_sha256 is None or not isinstance(report_binding, Mapping)
    ):
        raise BatchM4ReceiptError("accepted M4 receipt has no report binding")
    if report_sha256 is not None:
        if report_path is None:
            raise BatchM4ReceiptError("M4 receipt report binding is unavailable")
        try:
            actual_report_sha256 = sha256_file(report_path)
        except OSError as exc:
            raise BatchM4ReceiptError("M4 release report is unavailable") from exc
        if actual_report_sha256 != report_sha256:
            raise BatchM4ReceiptError("M4 release report fingerprint mismatch")
    identity = value.get("database")
    if not isinstance(identity, dict):
        raise BatchM4ReceiptError("M4 release receipt database binding is missing")
    actual = _file_identity(database_path)
    if identity.get("size_bytes") != actual["size_bytes"] or identity.get("sha256") != actual["sha256"]:
        raise BatchM4ReceiptError("M4 release receipt database fingerprint mismatch")
    runs = value.get("family_runs")
    contracts = value.get("family_contracts")
    if not isinstance(runs, dict) or tuple(runs) != M4_FAMILY_ORDER:
        raise BatchM4ReceiptError("M4 release receipt family run order is invalid")
    if not isinstance(contracts, dict) or tuple(contracts) != M4_FAMILY_ORDER:
        raise BatchM4ReceiptError("M4 release receipt family contract order is invalid")
    if value.get("receipt_id") != _receipt_id(
        {"size_bytes": identity.get("size_bytes"), "sha256": identity.get("sha256")},
        str(value.get("wave_id")), str(value.get("wave_manifest_sha256")),
        runs, contracts,
    ):
        raise BatchM4ReceiptError("M4 release receipt id binding is invalid")
    if value.get("receipt_trust") != "file_prefilter_not_authentication":
        raise BatchM4ReceiptError("M4 receipt trust semantics are invalid")
    return value


__all__ = [
    "BatchM4ReceiptError", "RECEIPT_CONTRACT", "RECEIPT_VERSION",
    "load_release_receipt", "publish_release_bundle_absent", "release_receipt", "sha256_file", "write_receipt_absent",
]
