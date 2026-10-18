"""Qualify a fresh local foundation without rewriting historical artifact seals.

This is a proof/identity boundary, not a source projection. The existing
independent source oracle is replayed, and its complete result must match.
"""
from pathlib import Path

from scripts.build_support import file_seal, load_object, qualify_stage_dependency
from . import character_7_1_foundation_verify as oracle

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = "manifests/character-7.1-physical-foundation/immutable-runtime.json"


def qualify_local_foundation(path, manifest_path, manifest):
    artifact = Path(path).resolve()
    output = artifact.parent.parent
    if (artifact != output / "character_foundation/artifact.sqlite"
            or Path(manifest_path).resolve() != output / "metadata" / MANIFEST):
        raise ValueError("local foundation must bind its own output")
    qualification = manifest.get("local_rebuild", {})
    if (not isinstance(qualification, dict) or set(qualification) != {"contract", "receipt"}
            or qualification["contract"] != "local-character-foundation-v1"):
        raise ValueError("unknown local foundation qualification contract")
    recipe = load_object(ROOT / "recipes/character-foundation.json")
    if {k: v for k, v in manifest.items() if k not in ("artifact", "local_rebuild")} != recipe:
        raise ValueError("local foundation source/recipe identity")
    receipt_path = artifact.parent / "verified.json"
    if file_seal(receipt_path) != qualification["receipt"]:
        raise ValueError("local foundation receipt seal")
    receipt = load_object(receipt_path)
    identity = receipt.get("identity", {})
    if identity.get("stage_inputs") != {} or identity.get("parameters") != {"stage": "character_foundation"}:
        raise ValueError("local foundation stage inputs/parameters")
    source = Path(identity["source_root"])
    history = Path(identity["historical_source_root"]) if identity.get("historical_source_root") else None
    qualify_stage_dependency(ROOT, source, history, artifact.parent, "character_foundation", recipe["source_snapshot"])
    actual = file_seal(artifact)
    if manifest.get("artifact") != {**actual, "machine_local_path": str(artifact)}:
        raise ValueError("local foundation artifact binding")
    build = load_object(artifact.parent / "build.json")
    if (build.get("artifact") != actual or build.get("contract") != recipe["contract"]
            or build.get("parser_build_id") != recipe["parser_build_id"]
            or build.get("ingest_run_id") != recipe["ingest_run_id"]):
        raise ValueError("local foundation build identity")
    checked = oracle.verify(source, ROOT / recipe["source_scope"], artifact, expected_artifact=actual)
    if checked != load_object(artifact.parent / "verify.json"):
        raise ValueError("local foundation independent oracle/report mismatch")
    # The replay must not change the evidence or its successful stage proof.
    if file_seal(receipt_path) != qualification["receipt"]:
        raise ValueError("local foundation proof changed during qualification")
    qualify_stage_dependency(ROOT, source, history, artifact.parent, "character_foundation", recipe["source_snapshot"])
    return actual
