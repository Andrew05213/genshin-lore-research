"""Small, explicit qualification rules for local build stages."""
from __future__ import annotations

import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import sqlite3
import sys

RECEIPT_CONTRACT = "local-build-stage-receipt-v2"
# Actual complete-verification contracts used by build.py. These describe
# verification execution, not logical identity or whole-game completeness.
SUCCESS_STATES = frozenset({"PASS", "PASS_PHYSICAL_PROJECTION", "PASS_COMPLETE_SOURCE_ROWSETS"})


def require_success(result):
    if not isinstance(result, dict) or not result:
        raise ValueError("verification rejected: missing nonempty result")
    states = [result[key] for key in ("status", "verdict") if key in result]
    if not states or any(not isinstance(value, str) for value in states):
        raise ValueError("verification rejected: missing status/verdict")
    states = [value.upper() for value in states]
    if len(set(states)) != 1 or any(value not in SUCCESS_STATES for value in states):
        raise ValueError("verification rejected: unknown, incomplete or conflicting status/verdict")
    if result.get("partial") is True or result.get("verification_completed") is False:
        raise ValueError("verification rejected: execution incomplete")
    if "verified" in result and result["verified"] is not True:
        raise ValueError("verification rejected: contradictory verified flag")
    return states[0]


def file_seal(path):
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"size_bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def pipeline_identity(root, source, history, *, inputs=(), parameters=None):
    """Conservative build-code/contract fingerprint; never hash docs or game trees.

    Deliberately not an inferred dependency graph. Any pipeline implementation
    change invalidates resume; stage-specific generated inputs are explicit.
    """
    root = Path(root).resolve()
    files = {}
    for directory, pattern in (("src", "*.py"), ("scripts", "*.py"), ("schemas", "*.sql"),
                               ("migrations", "*.sql"), ("recipes", "*.json"),
                               ("manifests", "*.json")):
        for path in sorted((root / directory).rglob(pattern)):
            files[str(path.relative_to(root))] = file_seal(path)
    for name in ("pyproject.toml", "requirements-build.txt"):
        if (root/name).is_file():
            files[name] = file_seal(root/name)
    declared = {str(Path(path).resolve()): file_seal(path) for path in inputs}
    return {"contract": RECEIPT_CONTRACT, "implementation_and_contracts": files,
            "environment": environment_identity(),
            "source_root": str(Path(source).resolve()),
            "historical_source_root": str(Path(history).resolve()) if history else None,
            "stage_inputs": declared, "parameters": parameters or {}}


def environment_identity():
    dependencies = {}
    for name in ("jsonschema", "mcp", "numpy"):
        try:
            dependencies[name] = version(name)
        except PackageNotFoundError:
            dependencies[name] = None
    return {"python": list(sys.version_info[:3]), "sqlite": sqlite3.sqlite_version,
            "dependencies": dependencies}


def load_object(path):
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict) or not value:
        raise ValueError("missing nonempty stage record: " + str(path))
    return value


def qualify_stage_dependency(root, source, history, directory, name, snapshot):
    """Check an exact v2 dependency; shared by orchestration and consumers."""
    directory = Path(directory)
    artifact = directory / "artifact.sqlite"
    receipt = load_object(directory / "verified.json")
    if receipt.get("contract") != RECEIPT_CONTRACT or receipt.get("independent_verifier_completed") is not True:
        raise ValueError("dependency requires current complete verification: " + name)
    for record in ("build.json", "verify.json", "invocation.json"):
        if receipt.get("records", {}).get(record) != file_seal(directory / record):
            raise ValueError("dependency report drift: " + name)
    require_success(load_object(directory / "verify.json"))
    identity = receipt.get("identity", {})
    invocation = load_object(directory / "invocation.json")
    if (identity.get("parameters", {}).get("stage") != name
            or invocation.get("stage") != name or invocation.get("identity") != identity
            or receipt.get("source_snapshot") != snapshot or invocation.get("source_snapshot") != snapshot):
        raise ValueError("dependency stage identity mismatch: " + name)
    actual = pipeline_identity(root, source, history, inputs=identity.get("stage_inputs", {}),
                               parameters=identity.get("parameters"))
    if actual != identity:
        raise ValueError("dependency implementation/input identity drift: " + name)
    if file_seal(artifact) != receipt.get("artifact"):
        raise ValueError("dependency artifact drift: " + name)
    if any(Path(str(artifact) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("dependency SQLite sidecar: " + name)
    return [artifact, directory / "verified.json"]
