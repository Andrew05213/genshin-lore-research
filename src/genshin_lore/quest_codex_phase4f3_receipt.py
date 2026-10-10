"""External exact-file prefilter receipt for verified Phase 4F3 releases."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .quest_codex_phase4f3_contract import (
    EXPECTED_COVERAGE,
    EXPECTED_MANIFEST_SHA256,
    FAMILY,
    FAMILY_CONTRACT_VERSION,
    expected_identity,
    load_manifest,
)
from .world_archive_phase4e2_promotion import sync_directory

RECEIPT_CONTRACT = "genshin-lore-quest-codex-phase4f3-release-receipt"
RECEIPT_VERSION = 1
SCHEMA16_RECEIPT_SCHEMA_CONTRACT = "schema16-migration0016-additive-wave01-exact"
SCHEMA17_RECEIPT_SCHEMA_CONTRACT = "schema17-migration0017-additive-wave02-exact"
SCHEMA19_RECEIPT_SCHEMA_CONTRACT = "schema19-migrations0018-0019-additive-r2-exact"
SCHEMA20_RECEIPT_SCHEMA_CONTRACT = "schema20-migration0020-additive-r4-exact"
VERIFIER_ORACLE = "frozen_phase4f1_full_source_coordinate_and_exact_sql_v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class QuestCodexPhase4F3ReceiptError(RuntimeError):
    pass


def _same(left: Path, right: Path) -> bool:
    try:
        if left.resolve(strict=False) == right.resolve(strict=False):
            return True
        return os.path.samefile(left, right)
    except (FileNotFoundError, OSError, RuntimeError):
        return False


def assert_receipt_path_safe(receipt_path: str | Path, *forbidden_paths: str | Path) -> None:
    receipt = Path(os.path.abspath(os.fspath(receipt_path)))
    forbidden: list[Path] = []
    for value in forbidden_paths:
        path = Path(os.path.abspath(os.fspath(value)))
        forbidden.extend((path, *(Path(f"{path}{suffix}") for suffix in ("-journal", "-wal", "-shm", ".lock"))))
    if any(_same(receipt, path) for path in forbidden):
        raise QuestCodexPhase4F3ReceiptError("release receipt aliases a protected input/output path")


def release_receipt(
    manifest: Mapping[str, Any], fingerprint: Mapping[str, Any],
    *, schema_version: int = 13,
) -> dict[str, Any]:
    if schema_version not in (13, 16, 17, 19, 20):
        raise QuestCodexPhase4F3ReceiptError("unsupported combined schema version")
    sha256 = fingerprint.get("sha256")
    size = fingerprint.get("size_bytes")
    if not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None:
        raise QuestCodexPhase4F3ReceiptError("database SHA-256 is invalid")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise QuestCodexPhase4F3ReceiptError("database size is invalid")
    identity = expected_identity(manifest)
    return {
        "receipt_contract": RECEIPT_CONTRACT,
        "receipt_version": RECEIPT_VERSION,
        "trust_semantics": "release_gate_result_and_database_file_prefilter_not_authentication",
        "verification": {
            "status": "pass", "oracle": VERIFIER_ORACLE,
            "schema_contract": {
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
            "source_commit": manifest["source"]["commit"],
            "source_tree": manifest["source"]["tree"],
            "coverage": EXPECTED_COVERAGE, "corpus_complete": True, "evidence_only": True,
        },
        "release_gate_reported_seals": {
            "counts": manifest["expected"]["counts"],
            "status_counts": manifest["expected"]["status_counts"],
            "runtime_canonical_business_digests": manifest["expected"]["occurrence_set_digests"],
            "exact_business_tuples": True, "foreign_keys_clean": True,
            "sidecars_absent": True,
        },
        "database": {"size_bytes": size, "sha256": sha256},
    }


def load_release_receipt(
    receipt_path: str | Path,
    manifest_path: str | Path,
    database_path: str | Path | None = None,
) -> dict[str, Any]:
    try:
        raw = Path(receipt_path).read_bytes()
        parsed = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QuestCodexPhase4F3ReceiptError("release receipt is unavailable or invalid") from exc
    manifest = load_manifest(manifest_path)
    database = parsed.get("database") if isinstance(parsed, dict) else None
    if not isinstance(database, dict):
        raise QuestCodexPhase4F3ReceiptError("release receipt database binding is invalid")
    verification = parsed.get("verification") if isinstance(parsed, dict) else None
    schema_contract = verification.get("schema_contract") if isinstance(verification, dict) else None
    if schema_contract == "schema13-migration0013-exact":
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
        raise QuestCodexPhase4F3ReceiptError("release receipt schema contract is invalid")
    expected = release_receipt(manifest, database, schema_version=schema_version)
    canonical = (json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    if parsed != expected or raw != canonical:
        raise QuestCodexPhase4F3ReceiptError("release receipt contract mismatch")
    if database_path is not None:
        database = Path(database_path)
        try:
            digest = hashlib.sha256()
            size = 0
            with database.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
            digest = digest.hexdigest()
        except OSError as exc:
            raise QuestCodexPhase4F3ReceiptError("receipt database is unavailable") from exc
        if parsed["database"] != {"size_bytes": size, "sha256": digest}:
            raise QuestCodexPhase4F3ReceiptError("release receipt database fingerprint mismatch")
    return expected


def write_release_receipt_atomically(
    receipt_path: str | Path,
    receipt: Mapping[str, Any],
    *forbidden_paths: str | Path,
) -> None:
    target = Path(os.path.abspath(os.fspath(receipt_path)))
    assert_receipt_path_safe(target, *forbidden_paths)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.phase4f3-", suffix=".receipt", dir=target.parent)
    staging = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        assert_receipt_path_safe(target, *forbidden_paths)
        os.replace(staging, target)
        sync_directory(target.parent)
    finally:
        try:
            staging.unlink()
        except FileNotFoundError:
            pass


__all__ = [
    "RECEIPT_CONTRACT", "RECEIPT_VERSION", "SCHEMA16_RECEIPT_SCHEMA_CONTRACT", "SCHEMA17_RECEIPT_SCHEMA_CONTRACT", "VERIFIER_ORACLE",
    "QuestCodexPhase4F3ReceiptError", "assert_receipt_path_safe",
    "load_release_receipt", "release_receipt", "write_release_receipt_atomically",
]
