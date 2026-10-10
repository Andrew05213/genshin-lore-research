"""Exact Schema 19 R2 runtime qualification.

R2 promotes the already byte-exact R1 candidate without changing its bytes.
The runtime manifest and receipt are product bindings; the historical M5E
Schema 17 manifest remains the oracle for the 21 compatibility families and
the Q2 manifest remains the oracle for the four v2 family projections.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .batch_m5e_runtime_oracle import M5E_FAMILY_ORDER, load_runtime_manifest as load_m5e_manifest
from .batch_r1_schema19 import Q2_MANIFEST_SHA256, R1_FAMILY_ORDER, load_manifest as load_q2_manifest
from .db import validate_exact_schema_structure
from .util import canonical_json, sha256_bytes

ROOT = Path(__file__).resolve().parents[2]
R2_RUNTIME_MANIFEST_PATH = ROOT / "manifests/batch/anime-game-data-7.0-batch-r2-schema19-runtime.json"
R2_RECEIPT_PATH = ROOT / "reports/r2/batch-r2-schema19.release-receipt.json"
M5E_RUNTIME_MANIFEST_PATH = ROOT / "manifests/batch/anime-game-data-7.0-batch-m5e-wave02-runtime.json"
Q2_MANIFEST_PATH = ROOT / "reports/q2/q2-coverage-matrix.json"
R1_ACCEPTANCE_REPORT_PATH = ROOT / "reports/r1/batch-r1-schema19-acceptance.json"
R2_INDEPENDENT_REPORT_PATH = ROOT / "reports/r2/batch-r2-independent-verification.json"
R2_WORLD_RECEIPT_PATH = ROOT / "reports/r2/world-archive-schema19.release-receipt.json"
R2_QUEST_RECEIPT_PATH = ROOT / "reports/r2/quest-codex-schema19.release-receipt.json"
R2_SCHEMA_VERSION = 19
R2_SCHEMA_CONTRACT = "schema19-migrations0018-0019-additive-r2-exact"
R2_RUNTIME_CONTRACT = "batch-r2-schema19-runtime-v1"
R2_RECEIPT_CONTRACT = "genshin-lore-batch-r2-schema19-release-receipt-v1"
R2_COVERAGE = "R2_wave_accepted_product"
R2_CANDIDATE_SIZE = 5_456_945_152
R2_CANDIDATE_SHA256 = "000bb61aea4825d895a7b0a4b51f9f3965e45f94a4c54069b3c0947ca9f1039b"
R2_PROVIDER_ORDER = (*M5E_FAMILY_ORDER, *R1_FAMILY_ORDER)


class BatchR2RuntimeError(RuntimeError):
    """The explicit Schema 19 runtime bundle is absent or inconsistent."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _strict_json(path: str | Path, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise BatchR2RuntimeError(f"duplicate {label} key: {key}")
            result[key] = value
        return result
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except BatchR2RuntimeError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise BatchR2RuntimeError(f"{label} is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise BatchR2RuntimeError(f"{label} must be an object")
    return value


def canonical_manifest_sha256(value: Mapping[str, Any]) -> str:
    body = dict(value)
    body.pop("manifest_sha256", None)
    return sha256_bytes(canonical_json(body).encode("utf-8"))


def load_runtime_manifest(path: str | Path = R2_RUNTIME_MANIFEST_PATH) -> dict[str, Any]:
    value = _strict_json(path, "R2 runtime manifest")
    if value.get("runtime_contract") != R2_RUNTIME_CONTRACT:
        raise BatchR2RuntimeError("R2 runtime manifest contract mismatch")
    if value.get("schema_version") != R2_SCHEMA_VERSION or value.get("schema_contract") != R2_SCHEMA_CONTRACT:
        raise BatchR2RuntimeError("R2 runtime manifest Schema19 binding mismatch")
    if value.get("manifest_sha256") != canonical_manifest_sha256(value):
        raise BatchR2RuntimeError("R2 runtime manifest canonical seal mismatch")
    if value.get("product_accepted") is not True or value.get("corpus_complete") is not False:
        raise BatchR2RuntimeError("R2 runtime manifest acceptance/completeness mismatch")
    candidate = value.get("candidate")
    if candidate != {"sha256": R2_CANDIDATE_SHA256, "size_bytes": R2_CANDIDATE_SIZE}:
        raise BatchR2RuntimeError("R2 runtime manifest candidate seal mismatch")
    if value.get("provider_order") != list(R2_PROVIDER_ORDER):
        raise BatchR2RuntimeError("R2 runtime manifest provider order mismatch")
    return value


def receipt_body(manifest: Mapping[str, Any]) -> dict[str, Any]:
    inputs = manifest["inputs"]
    body = {
        "receipt_contract": R2_RECEIPT_CONTRACT,
        "schema_version": R2_SCHEMA_VERSION,
        "schema_contract": R2_SCHEMA_CONTRACT,
        "database": {"sha256": R2_CANDIDATE_SHA256, "size_bytes": R2_CANDIDATE_SIZE},
        "runtime_manifest_sha256": manifest["manifest_sha256"],
        "release_evidence": {
            "r1_acceptance_report_sha256": inputs["r1_acceptance_report_sha256"],
            "independent_verification_sha256": inputs["independent_verification_sha256"],
            "world_schema19_receipt_sha256": inputs["world_schema19_receipt_sha256"],
            "quest_schema19_receipt_sha256": inputs["quest_schema19_receipt_sha256"],
        },
        "provider_order": list(R2_PROVIDER_ORDER),
        "product_accepted": True,
        "corpus_complete": False,
        "evidence_only": True,
    }
    return {**body, "receipt_id": sha256_bytes(canonical_json(body).encode("utf-8"))}


def load_receipt(path: str | Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    value = _strict_json(path, "R2 receipt")
    if value != receipt_body(manifest):
        raise BatchR2RuntimeError("R2 receipt contract mismatch")
    return value


@dataclass(frozen=True)
class R2FileIdentity:
    path: str
    device: int
    inode: int
    size_bytes: int
    mtime_ns: int

    @classmethod
    def capture(cls, path: str | Path) -> "R2FileIdentity":
        target = Path(path).resolve()
        try:
            info = os.lstat(target)
        except OSError as exc:
            raise BatchR2RuntimeError(f"R2 runtime input unavailable: {target}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise BatchR2RuntimeError("R2 runtime input must be a regular non-symlink file")
        for suffix in ("-journal", "-wal", "-shm"):
            if Path(str(target) + suffix).exists():
                raise BatchR2RuntimeError(f"R2 runtime sidecar present: {target}{suffix}")
        return cls(str(target), int(info.st_dev), int(info.st_ino), int(info.st_size), int(info.st_mtime_ns))

    def check(self) -> None:
        if self.capture(self.path) != self:
            raise BatchR2RuntimeError(f"R2 runtime input changed: {self.path}")


@dataclass(frozen=True)
class R2Qualification:
    database_path: str
    manifest_path: str
    receipt_path: str
    manifest_sha256: str
    receipt_id: str
    candidate_sha256: str
    candidate_size_bytes: int
    legacy_manifest: Mapping[str, Any]
    q2_manifest: Mapping[str, Any]
    stability: tuple[R2FileIdentity, R2FileIdentity, R2FileIdentity]
    status: str = "verified"
    product_accepted: bool = True
    schema_version: int = R2_SCHEMA_VERSION
    schema_contract: str = R2_SCHEMA_CONTRACT
    wave_coverage: str = R2_COVERAGE
    provider_count: int = len(R2_PROVIDER_ORDER)

    def check(self) -> None:
        for item in self.stability:
            item.check()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status, "enabled": True, "schema_version": self.schema_version,
            "schema_contract": self.schema_contract, "product_accepted": self.product_accepted,
            "candidate": {"size_bytes": self.candidate_size_bytes, "sha256": self.candidate_sha256},
            "runtime_manifest_sha256": self.manifest_sha256, "receipt_id": self.receipt_id,
            "provider_count": self.provider_count, "top_level_tool_count": 25,
            "corpus_complete": False, "evidence_only": True,
        }


def _validate_legacy_bindings(conn: sqlite3.Connection, manifest: Mapping[str, Any]) -> None:
    families = manifest.get("families")
    if not isinstance(families, list) or tuple(item.get("family") for item in families if isinstance(item, Mapping)) != M5E_FAMILY_ORDER:
        raise BatchR2RuntimeError("tracked M5E family order mismatch")
    for item in families:
        assert isinstance(item, Mapping)
        run = item.get("ingest_run_id")
        parser = item.get("parser_build_id")
        row = conn.execute("SELECT parser_build_id,status FROM ingest_run WHERE id=?", (run,)).fetchone()
        if row is None or tuple(row) != (parser, "complete"):
            raise BatchR2RuntimeError(f"legacy family run mismatch: {item.get('family')}")
        tables = item.get("tables")
        if not isinstance(tables, list):
            raise BatchR2RuntimeError(f"legacy family table list missing: {item.get('family')}")
        for table in tables:
            if not isinstance(table, Mapping) or not isinstance(table.get("table_name"), str):
                raise BatchR2RuntimeError("legacy family table declaration invalid")
            count = conn.execute(f'SELECT COUNT(*) FROM "{table["table_name"]}" WHERE ingest_run_id=?', (run,)).fetchone()[0]
            if count != table.get("row_count"):
                raise BatchR2RuntimeError(f"legacy family row count mismatch: {item.get('family')}")


def qualify_batch_r2(
    database: str | Path,
    *,
    manifest_path: str | Path = R2_RUNTIME_MANIFEST_PATH,
    receipt_path: str | Path = R2_RECEIPT_PATH,
) -> R2Qualification:
    db_path = Path(database).resolve()
    manifest_path = Path(manifest_path).resolve()
    receipt_path = Path(receipt_path).resolve()
    stability = (R2FileIdentity.capture(db_path), R2FileIdentity.capture(manifest_path), R2FileIdentity.capture(receipt_path))
    if stability[0].size_bytes != R2_CANDIDATE_SIZE or sha256_file(db_path) != R2_CANDIDATE_SHA256:
        raise BatchR2RuntimeError("exact Schema19 database seal mismatch")
    manifest = load_runtime_manifest(manifest_path)
    receipt = load_receipt(receipt_path, manifest)
    legacy = load_m5e_manifest(M5E_RUNTIME_MANIFEST_PATH)
    q2 = load_q2_manifest(Q2_MANIFEST_PATH)
    inputs = manifest.get("inputs")
    if not isinstance(inputs, Mapping):
        raise BatchR2RuntimeError("R2 input bindings missing")
    if inputs.get("m5e_runtime_manifest_sha256") != legacy.get("manifest_sha256"):
        raise BatchR2RuntimeError("R2 M5E manifest binding mismatch")
    if inputs.get("q2_manifest_sha256") != Q2_MANIFEST_SHA256:
        raise BatchR2RuntimeError("R2 Q2 manifest binding mismatch")
    release_inputs = {
        "r1_acceptance_report_sha256": R1_ACCEPTANCE_REPORT_PATH,
        "independent_verification_sha256": R2_INDEPENDENT_REPORT_PATH,
        "world_schema19_receipt_sha256": R2_WORLD_RECEIPT_PATH,
        "quest_schema19_receipt_sha256": R2_QUEST_RECEIPT_PATH,
    }
    for key, path in release_inputs.items():
        expected = inputs.get(key)
        if not isinstance(expected, str) or sha256_file(path) != expected:
            raise BatchR2RuntimeError(f"R2 release evidence binding mismatch: {key}")
    uri = db_path.as_uri() + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        versions = tuple(row[0] for row in conn.execute("SELECT version FROM schema_migration ORDER BY version"))
        if versions != tuple(range(1, 20)):
            raise BatchR2RuntimeError("Schema19 migration ledger is not exact 1..19")
        validate_exact_schema_structure(conn, R2_SCHEMA_VERSION)
        _validate_legacy_bindings(conn, legacy)
        for family in R1_FAMILY_ORDER:
            item = next(value for value in q2["families"] if value.get("family") == family)
            run = item["ingest_run_id"]
            row = conn.execute("SELECT parser_build_id,status FROM ingest_run WHERE id=?", (run,)).fetchone()
            if row is None or tuple(row) != (item["parser_build_id"], "complete"):
                raise BatchR2RuntimeError(f"R2 v2 family run mismatch: {family}")
    finally:
        conn.close()
    qualification = R2Qualification(
        str(db_path), str(manifest_path), str(receipt_path), str(manifest["manifest_sha256"]),
        str(receipt["receipt_id"]), R2_CANDIDATE_SHA256, R2_CANDIDATE_SIZE, legacy, q2, stability,
    )
    qualification.check()
    return qualification


__all__ = [
    "BatchR2RuntimeError", "R2_CANDIDATE_SHA256", "R2_CANDIDATE_SIZE", "R2_COVERAGE",
    "R2_PROVIDER_ORDER", "R2_RECEIPT_CONTRACT", "R2_RECEIPT_PATH", "R2_RUNTIME_CONTRACT",
    "R2_RUNTIME_MANIFEST_PATH", "R2_SCHEMA_CONTRACT", "R2_SCHEMA_VERSION", "R2Qualification",
    "canonical_manifest_sha256", "load_receipt", "load_runtime_manifest", "qualify_batch_r2",
    "receipt_body", "sha256_file",
]
