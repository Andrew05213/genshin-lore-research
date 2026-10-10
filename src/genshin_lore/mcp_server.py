from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sqlite3
import sys
import anyio
import functools
from mcp import types as mcp_types
from mcp.shared.message import SessionMessage
from mcp.server.stdio import _claim_fd, _open_stdin_diversion, _open_stdout_diversion
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from ._readonly_database import ReadonlyDatabase, open_readonly_database
from .character_retrieval import (
    CharacterNotFound,
    get_character_row_evidence,
    get_character_voice_container,
    get_character_voice_context,
    inspect_character_capability,
    list_character_rows,
    search_character_text_evidence,
)
from .family_evidence import FamilyEvidenceRouter
from .r0_retrieval import build_r0_physical_providers
from .batch_q2_uplift import build_q2_providers
from .batch_r3_uplift import R3_FAMILY_ORDER, build_r3_providers
from .batch_m4_retrieval import build_batch_m4_repositories
from .batch_m5d_runtime import (
    BatchM5DError,
    M5D_FAMILY_ORDER,
    M5D_SCHEMA_VERSION,
    build_batch_m5d_providers,
    qualify_batch_m5d,
)
from .batch_m5e_retrieval import (
    BatchM5ERetrievalError,
    build_batch_m5e_providers,
)
from .batch_m5e_runtime_oracle import (
    BatchM5ERuntimeOracleError,
    M5E_FAMILY_ORDER,
    M5E_SCHEMA_VERSION,
    qualify_batch_m5e,
)
from .batch_r4_runtime import qualify_batch_r4, build_batch_r4_providers, PROVIDER_ORDER as R4_PROVIDER_ORDER
from .batch_r2_retrieval import BatchR2RetrievalError, build_batch_r2_providers
from .batch_r2_runtime import (
    BatchR2RuntimeError,
    R2_PROVIDER_ORDER,
    R2_SCHEMA_CONTRACT,
    R2_SCHEMA_VERSION,
    R2Qualification,
    qualify_batch_r2,
)
from .quest_codex_retrieval import (
    QuestCodexRepository,
    inspect_quest_codex_capability,
    qualify_quest_codex_r2_compatibility,
)
from .r5_quest_talk_retrieval import (
    build_quest_talk_multi_run_provider,
    build_r5_quest_talk_provider,
)
from .retrieval import (
    AmbiguousLookup,
    NotFound,
    _json,
    _occurrence_context,
    _occurrence_item,
    _occurrence_rows,
    _source,
    find_dialog_occurrence,
)
from .static_contracts import StaticArtifactSeal
from .static_retrieval import (
    StaticAmbiguousLookup,
    StaticNotFound,
    find_static_evidence_container,
    get_static_evidence_container,
    get_static_relationship_evidence,
    inspect_static_capabilities,
    list_static_evidence_containers,
    list_static_relationship_evidence,
)
from .tutorial_retrieval import (
    TutorialNotFound,
    get_tutorial_context,
    get_tutorial_row_evidence,
    inspect_tutorial_capability,
    list_tutorial_rows,
    search_tutorial_text_evidence,
)
from .world_archive_retrieval import (
    WorldArchiveRepository,
    inspect_world_archive_capability,
    qualify_world_archive_r2_compatibility,
)

API_VERSION = "0.7"
SERVER_NAME = "genshin-lore-database"
SERVER_VERSION = "0.7.1"
DATA_FAMILIES = ("talk", "quest", "book", "weapon", "static_lore")
BASE_TOOLS = (
    "get_capabilities",
    "get_schema_version",
    "lookup_occurrence",
    "get_context",
    "get_provenance",
    "resolve_quest_reference",
    "list_evidence_containers",
    "list_ambiguities",
)
STATIC_TOOLS = (
    "list_static_evidence_containers",
    "lookup_static_evidence_container",
    "get_static_evidence_container",
    "list_static_relationship_evidence",
    "get_static_relationship_evidence",
)
CHARACTER_TOOLS = (
    "list_character_rows",
    "get_character_row_evidence",
    "search_character_text_evidence",
    "get_character_voice_container",
    "get_character_voice_context",
)
TUTORIAL_TOOLS = (
    "list_tutorial_rows",
    "get_tutorial_row_evidence",
    "search_tutorial_text_evidence",
    "get_tutorial_context",
)
FAMILY_EVIDENCE_TOOLS = (
    "list_family_evidence",
    "get_family_evidence",
    "search_family_text",
)
# Keep the historical Base/Static/Character/Tutorial ordering and append the
# stable routed facade.  The additive order is part of the discovery contract.
TOOLS = BASE_TOOLS + STATIC_TOOLS + CHARACTER_TOOLS + TUTORIAL_TOOLS + FAMILY_EVIDENCE_TOOLS
Q2_CANONICAL_MANIFEST_SHA256 = "09d6bb410851cc0ad1c804e6840e5fe6d0934e4648d575dcad38cd757b850801"
R3_CANONICAL_MANIFEST_SHA256 = "1d599022ba3e44b6aae8ae0e78e7ae1ab0e68f0e903aae84521451868e2edf6b"
R5_QUEST_TALK_RUNTIME_MANIFEST_SHA256 = "f5458918d5362d6b5f8404ff78333c92162b8c45d5fc1822fd080f6572ba90e2"
# Both reviewed standalone selections retain their exact manifest seals.
R6_MEDIUM_QUEST_TALK_RUNTIME_MANIFEST_SHA256 = "4297fc2ff426aa2ecb606c0a47de6a719d1001a77024caaf3b8cecff424694fc"


def _require_canonical_manifest(path: str | Path, expected: str, label: str) -> Path:
    """Bind optional candidate registration to reviewed tracked manifest bytes."""
    selected = Path(path).resolve()
    digest = hashlib.sha256()
    with selected.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise ValueError(f"{label} manifest does not match the canonical tracked seal")
    return selected


def _require_canonical_r5_quest_talk_manifest(path: str | Path) -> Path:
    """Accept only a reviewed scalable or frozen historical runtime manifest."""
    selected = Path(path).resolve()
    digest = hashlib.sha256()
    with selected.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    catalog_path = Path(__file__).resolve().parents[2] / "manifests/corpora/quest-talk-runtime-catalog.json"
    scalable_seals = set()
    if catalog_path.is_file():
        import json
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        if catalog.get("contract") != "quest-talk-runtime-catalog-v1":
            raise ValueError("Quest/Talk runtime catalog contract mismatch")
        scalable_seals = {entry["runtime_manifest_sha256"] for entry in catalog["entries"] if entry["status"] in {"release_verified", "accepted_non_product_candidate", "accepted_product_component"}}
    if digest.hexdigest() not in scalable_seals | {
        R5_QUEST_TALK_RUNTIME_MANIFEST_SHA256,
        R6_MEDIUM_QUEST_TALK_RUNTIME_MANIFEST_SHA256,
    }:
        raise ValueError("R5 Quest/Talk manifest does not match a canonical tracked seal")
    return selected


def _require_canonical_q2_manifest(path: str | Path) -> Path:
    """Compatibility wrapper retained for focused Q2 contract tests."""
    return _require_canonical_manifest(
        path, Q2_CANONICAL_MANIFEST_SHA256, "Q2 uplift")


READONLY_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
FORBIDDEN_CAPABILITIES = (
    "writes",
    "migrations",
    "ingestion",
    "fts",
    "embeddings",
    "semantic_search",
    "logical_talk",
    "logical_passage",
    "logical_document",
    "logical_character",
    "cross_voice_container_context",
    "version_comparison",
    "remote_http",
)


def _get_schema_version(db: ReadonlyDatabase) -> int:
    row = db.conn.execute("SELECT MAX(version) FROM schema_migration").fetchone()
    return int(row[0]) if (row and row[0] is not None) else 0


def _envelope(
    db: ReadonlyDatabase,
    *,
    status: str,
    data: Any = None,
    errors: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "database_schema_version": _get_schema_version(db),
        "status": status,
        "data": data,
        "errors": errors if errors is not None else [],
    }


def _qualify_m5d_startup(
    db: ReadonlyDatabase,
    report_path: str | Path | None,
    manifest_path: str | Path | None,
    independent_report_path: str | Path | None,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Qualify the one explicit Schema17 M5D bundle once at server startup.

    M5D is a candidate-only runtime boundary.  Missing, invalid, or
    cross-bound candidate evidence never falls back to tracked defaults or a
    sibling run; it simply leaves the eleven providers unregistered.
    """
    schema_version = _get_schema_version(db)
    base = {
        "schema_version": M5D_SCHEMA_VERSION,
        "configured": all(item is not None for item in (report_path, manifest_path, independent_report_path)),
        "enabled": False,
        "status": "not_applicable" if schema_version != M5D_SCHEMA_VERSION else "unavailable",
        "reason": None if schema_version != M5D_SCHEMA_VERSION else "explicit_report_manifest_and_independent_verification_required",
        "product_accepted": False,
        "receipts_issued": False,
        "stdio_accepted": False,
        "provider_count": 0,
        "provider_families": [],
        "qualification_count": 0,
        "candidate_hash_count": 0,
    }
    if schema_version != M5D_SCHEMA_VERSION:
        return (), base
    if not all(item is not None for item in (report_path, manifest_path, independent_report_path)):
        return (), base
    try:
        qualification = qualify_batch_m5d(
            db,
            report_path=Path(report_path),  # type: ignore[arg-type]
            manifest_path=Path(manifest_path),  # type: ignore[arg-type]
            verification_path=Path(independent_report_path),  # type: ignore[arg-type
            candidate_path=db.path,
        )
        providers = tuple(build_batch_m5d_providers(db, qualification=qualification))
        families = [str(provider.family) for provider in providers]
        if tuple(families) != M5D_FAMILY_ORDER:
            raise BatchM5DError("M5D provider registry order/count is not exact")
        base.update({
            "enabled": True,
            "status": qualification.status,
            "reason": None,
            "provider_count": len(providers),
            "provider_families": families,
            "qualification_count": 1,
            "candidate_hash_count": 1,
            "candidate": {
                "path": qualification.candidate_path,
                "size_bytes": qualification.candidate_size_bytes,
                "sha256": qualification.candidate_sha256,
            },
            "report": {"path": qualification.report_path, "sha256": qualification.report_sha256},
            "runtime_manifest": {"path": qualification.manifest_path, "sha256": qualification.manifest_sha256},
            "independent_verification": {"path": qualification.verification_path, "sha256": qualification.verification_sha256},
            "wave_id": qualification.wave_id,
            "snapshot_id": qualification.snapshot_id,
            "parser_build_id": qualification.parser_build_id,
        })
        return providers, base
    except (BatchM5DError, OSError, RuntimeError, sqlite3.Error, TypeError, ValueError, KeyError) as exc:
        base["reason"] = str(exc)
        return (), base


def _qualify_m5e_startup(
    db: ReadonlyDatabase,
    receipt_path: str | Path | None,
    manifest_path: str | Path | None,
    world_archive_receipt_path: str | Path | None,
    quest_codex_receipt_path: str | Path | None,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Qualify one explicit accepted Schema17 M5E bundle at startup.

    The expensive M5E oracle is intentionally invoked once.  All twenty-one
    family providers receive the resulting qualification and stability
    snapshot; they never rehash or re-audit the database independently.
    World and Quest receipts are explicit M5E compatibility inputs and are
    also independently qualified by their existing repositories.
    """
    schema_version = _get_schema_version(db)
    configured = all(item is not None for item in (
        receipt_path, manifest_path, world_archive_receipt_path,
        quest_codex_receipt_path,
    ))
    base: dict[str, Any] = {
        "schema_version": M5E_SCHEMA_VERSION,
        "schema_contract": "schema17-migration0017-additive-wave02-exact",
        "configured": configured,
        "enabled": False,
        "status": "not_applicable" if schema_version != M5E_SCHEMA_VERSION else "unavailable",
        "reason": (
            None if schema_version != M5E_SCHEMA_VERSION
            else "explicit_m5e_manifest_receipt_and_schema17_world_quest_receipts_required"
        ),
        "coverage": "unavailable",
        "corpus_complete": False,
        "outer_corpus_complete": False,
        "product_accepted": False,
        "receipts_issued": False,
        "stdio_accepted": False,
        "provider_count": 0,
        "provider_families": [],
        "qualification_count": 0,
        "candidate_hash_count": 0,
        "profile_picture_coverage": "bounded_canary",
        "provider_registry": {
            "family_provider_registry_changed": True,
            "top_level_tool_registry_changed": False,
            "top_level_tool_count": len(TOOLS),
        },
    }
    if schema_version != M5E_SCHEMA_VERSION or not configured:
        return (), base
    try:
        selected_manifest = Path(manifest_path)  # type: ignore[arg-type]
        selected_receipt = Path(receipt_path)  # type: ignore[arg-type]
        companion_paths = {
            "world_archive": Path(world_archive_receipt_path),  # type: ignore[arg-type]
            "quest_codex": Path(quest_codex_receipt_path),  # type: ignore[arg-type]
        }
        qualification = qualify_batch_m5e(
            db,
            manifest_path=selected_manifest,
            receipt_path=selected_receipt,
            companion_paths=companion_paths,
            hash_candidate=True,
            release_validation=False,
        )
        providers = tuple(build_batch_m5e_providers(
            db,
            qualification=qualification,
            manifest_path=selected_manifest,
            receipt_path=selected_receipt,
        ))
        families = tuple(str(provider.family) for provider in providers)
        if families != M5E_FAMILY_ORDER:
            raise BatchM5ERetrievalError(
                "M5E provider registry order/count is not exact"
            )
        base.update({
            "enabled": True,
            "status": qualification.status,
            "reason": None,
            "coverage": "R2_wave_accepted_product",
            "product_accepted": qualification.product_accepted,
            "receipts_issued": True,
            "provider_count": len(providers),
            "provider_families": list(families),
            "qualification_count": 1,
            "candidate_hash_count": 1 if qualification.candidate_sha256 else 0,
            "candidate": {
                "size_bytes": qualification.candidate_size_bytes,
                "sha256": qualification.candidate_sha256,
            },
            "receipt": {
                "path": qualification.receipt_path,
                "receipt_id": qualification.receipt_id,
            },
            "runtime_manifest": {
                "path": qualification.manifest_path,
                "sha256": qualification.manifest_sha256,
            },
            "compatibility_receipts": {
                "world_archive": str(companion_paths["world_archive"].resolve()),
                "quest_codex": str(companion_paths["quest_codex"].resolve()),
            },
        })
        return providers, base
    except (
        BatchM5ERetrievalError,
        BatchM5ERuntimeOracleError,
        OSError,
        RuntimeError,
        sqlite3.Error,
        TypeError,
        ValueError,
        KeyError,
    ) as exc:
        base["reason"] = str(exc)
        return (), base


def _qualify_r2_startup(
    db: ReadonlyDatabase,
    receipt_path: str | Path | None,
    manifest_path: str | Path | None,
) -> tuple[tuple[Any, ...], dict[str, Any], R2Qualification | None]:
    """Qualify one explicit accepted Schema19 R2 bundle once."""
    schema_version = _get_schema_version(db)
    configured = receipt_path is not None and manifest_path is not None
    base: dict[str, Any] = {
        "schema_version": R2_SCHEMA_VERSION, "schema_contract": R2_SCHEMA_CONTRACT,
        "configured": configured, "enabled": False,
        "status": "not_applicable" if schema_version != R2_SCHEMA_VERSION else "unavailable",
        "reason": None if schema_version != R2_SCHEMA_VERSION else "explicit_r2_manifest_and_receipt_required",
        "coverage": "unavailable", "corpus_complete": False, "outer_corpus_complete": False,
        "product_accepted": False, "provider_count": 0, "provider_families": [],
        "qualification_count": 0, "candidate_hash_count": 0,
        "provider_registry": {"family_provider_registry_changed": True,
                              "top_level_tool_registry_changed": False,
                              "top_level_tool_count": len(TOOLS)},
    }
    if schema_version != R2_SCHEMA_VERSION or not configured:
        return (), base, None
    try:
        qualification = qualify_batch_r2(
            db.path, manifest_path=Path(manifest_path), receipt_path=Path(receipt_path)
        )
        providers = tuple(build_batch_r2_providers(db, qualification))
        if tuple(provider.family for provider in providers) != R2_PROVIDER_ORDER:
            raise BatchR2RetrievalError("R2 provider registry order/count is not exact")
        base.update(qualification.to_dict())
        base.update({"configured": True, "enabled": True, "reason": None,
                     "coverage": "R2_wave_accepted_product",
                     "provider_families": list(R2_PROVIDER_ORDER),
                     "qualification_count": 1, "candidate_hash_count": 1})
        return providers, base, qualification
    except (BatchR2RetrievalError, BatchR2RuntimeError, OSError, RuntimeError,
            sqlite3.Error, TypeError, ValueError, KeyError) as exc:
        base["reason"] = str(exc)
        return (), base, None


def create_mcp_server(
    db: ReadonlyDatabase,
    world_archive_receipt_path: str | Path | None = None,
    quest_codex_receipt_path: str | Path | None = None,
    batch_wave_receipt_path: str | Path | None = None,
    batch_m5d_report_path: str | Path | None = None,
    batch_m5d_runtime_manifest_path: str | Path | None = None,
    batch_m5d_independent_report_path: str | Path | None = None,
    batch_m5e_release_receipt_path: str | Path | None = None,
    batch_m5e_runtime_manifest_path: str | Path | None = None,
    batch_r2_release_receipt_path: str | Path | None = None,
    batch_r2_runtime_manifest_path: str | Path | None = None,
    r0_physical_artifact_path: str | Path | None = None,
    r0_source_root: str | Path | None = None,
    q2_uplift_artifact_path: str | Path | None = None,
    q2_uplift_manifest_path: str | Path | None = None,
    r3_uplift_artifact_path: str | Path | None = None,
    r3_uplift_manifest_path: str | Path | None = None,
    batch_r4_release_receipt_path: str | Path | None = None,
    batch_r4_runtime_manifest_path: str | Path | None = None,
    r5_quest_talk_artifact_path: str | Path | None = None,
    r5_quest_talk_runtime_manifest_path: str | Path | None = None,
    r5_quest_talk_bundle_paths: tuple[tuple[str | Path, str | Path], ...] | None = None,
    loading_tips_artifact_path: str | Path | None = None,
    loading_tips_manifest_path: str | Path | None = None,
    gcg_card_artifact_path: str | Path | None = None,
    gcg_card_manifest_path: str | Path | None = None,
    document_artifact_path: str | Path | None = None,
    document_manifest_path: str | Path | None = None,
    npc_dialogue_artifact_path: str | Path | None = None,
    npc_dialogue_manifest_path: str | Path | None = None,
    character_lore_artifact_path: str | Path | None = None,
    character_lore_manifest_path: str | Path | None = None,
    talk_physical_artifact_path: str | Path | None = None,
    talk_physical_manifest_path: str | Path | None = None,
    quest_narrative_artifact_path: str | Path | None = None,
    quest_narrative_manifest_path: str | Path | None = None,
    quest_review_artifact_path: str | Path | None = None,
    quest_review_manifest_path: str | Path | None = None,
    codexquest_artifact_path: str | Path | None = None,
    codexquest_manifest_path: str | Path | None = None,
    mail_archive_artifact_path: str | Path | None = None,
    mail_archive_manifest_path: str | Path | None = None,
    event_narrative_artifact_path: str | Path | None = None,
    event_narrative_manifest_path: str | Path | None = None,
    event_narrative_successor_artifact_path: str | Path | None = None,
    event_narrative_successor_manifest_path: str | Path | None = None,
    event_narrative_filmfest_artifact_path: str | Path | None = None,
    event_narrative_filmfest_manifest_path: str | Path | None = None,
    event_narrative_wave_artifact_path: str | Path | None = None,
    event_narrative_wave_manifest_path: str | Path | None = None,
    event_narrative_tps_artifact_path: str | Path | None = None,
    event_narrative_tps_manifest_path: str | Path | None = None,
    event_narrative_photo_echo_artifact_path: str | Path | None = None,
    event_narrative_photo_echo_manifest_path: str | Path | None = None,
    event_narrative_photo_echo_delta_artifact_path: str | Path | None = None,
    event_narrative_photo_echo_delta_manifest_path: str | Path | None = None,
    event_narrative_native_text_artifact_path: str | Path | None = None,
    event_narrative_native_text_manifest_path: str | Path | None = None,
    event_narrative_dialog_reminder_artifact_path: str | Path | None = None,
    event_narrative_dialog_reminder_manifest_path: str | Path | None = None,
    readable_archive_artifact_path: str | Path | None = None,
    readable_archive_manifest_path: str | Path | None = None,
    subtitle_archive_artifact_path: str | Path | None = None,
    subtitle_archive_manifest_path: str | Path | None = None,
    native_archive_artifact_path: str | Path | None = None,
    native_archive_manifest_path: str | Path | None = None,
    native_message_archive_artifact_path: str | Path | None = None,
    native_message_archive_manifest_path: str | Path | None = None,
    native_book_music_archive_artifact_path: str | Path | None = None,
    native_book_music_archive_manifest_path: str | Path | None = None,
    native_text_bulk_archive_artifact_path: str | Path | None = None,
    native_text_bulk_archive_manifest_path: str | Path | None = None,
    aux_quest_delta_archive_artifact_path: str | Path | None = None,
    aux_quest_delta_archive_manifest_path: str | Path | None = None,
    high_value_native_text_artifact_path: str | Path | None = None,
    high_value_native_text_manifest_path: str | Path | None = None,
) -> MCPServer:
    """Create the readonly MCP server with explicit compatibility-bound providers."""
    m5e_configured = any(item is not None for item in (
        batch_m5e_release_receipt_path, batch_m5e_runtime_manifest_path,
    ))
    r2_configured = any(item is not None for item in (
        batch_r2_release_receipt_path, batch_r2_runtime_manifest_path,
    ))
    if r2_configured and not all(item is not None for item in (
        batch_r2_release_receipt_path, batch_r2_runtime_manifest_path,
    )):
        raise ValueError("R2 release receipt and runtime manifest must be supplied together")
    legacy_configured = any(item is not None for item in (
        batch_wave_receipt_path, batch_m5d_report_path,
        batch_m5d_runtime_manifest_path, batch_m5d_independent_report_path,
    ))
    r4_configured = any(p is not None for p in (batch_r4_release_receipt_path, batch_r4_runtime_manifest_path))
    if r4_configured and (batch_r4_release_receipt_path is None or batch_r4_runtime_manifest_path is None):
        raise ValueError("R4 release receipt and runtime manifest must be supplied together")
    if r4_configured and (r2_configured or m5e_configured or legacy_configured or any(p is not None for p in (
            q2_uplift_artifact_path, q2_uplift_manifest_path, r3_uplift_artifact_path, r3_uplift_manifest_path))):
        raise ValueError("R4 cannot be combined with another wave runtime or standalone Q2/R3")
    if r2_configured and (m5e_configured or legacy_configured or q2_uplift_artifact_path is not None or q2_uplift_manifest_path is not None):
        raise ValueError("R2 accepted runtime cannot be combined with legacy/M5E runtime or standalone Q2")
    if m5e_configured and legacy_configured:
        raise ValueError(
            "M5E accepted runtime cannot be combined with M4 batch receipt "
            "or M5D candidate evidence"
        )
    if (r0_physical_artifact_path is None) != (r0_source_root is None):
        raise ValueError("R0 physical artifact and source root must be supplied together")
    r0_repositories = (
        build_r0_physical_providers(r0_physical_artifact_path, r0_source_root)
        if r0_physical_artifact_path is not None and r0_source_root is not None
        else ()
    )
    if (q2_uplift_artifact_path is None) != (q2_uplift_manifest_path is None):
        raise ValueError("Q2 uplift artifact and tracked manifest must be supplied together")
    q2_repositories: tuple[Any, ...] = ()
    if q2_uplift_artifact_path is not None and q2_uplift_manifest_path is not None:
        canonical_q2_manifest = _require_canonical_manifest(
            q2_uplift_manifest_path, Q2_CANONICAL_MANIFEST_SHA256, "Q2 uplift")
        q2_repositories = build_q2_providers(
            q2_uplift_artifact_path, canonical_q2_manifest
        )
    if (r3_uplift_artifact_path is None) != (r3_uplift_manifest_path is None):
        raise ValueError("R3 uplift artifact and tracked manifest must be supplied together")
    r3_repositories: tuple[Any, ...] = ()
    if r3_uplift_artifact_path is not None and r3_uplift_manifest_path is not None:
        canonical_r3_manifest = _require_canonical_manifest(
            r3_uplift_manifest_path, R3_CANONICAL_MANIFEST_SHA256, "R3 uplift")
        r3_repositories = build_r3_providers(r3_uplift_artifact_path, canonical_r3_manifest)
    if r5_quest_talk_bundle_paths is not None and any(
        item is not None for item in (r5_quest_talk_artifact_path, r5_quest_talk_runtime_manifest_path)
    ):
        raise ValueError("Quest/Talk single bundle and multi-run bundles are mutually exclusive")
    if r5_quest_talk_bundle_paths is not None and not r5_quest_talk_bundle_paths:
        raise ValueError("Quest/Talk multi-run bundles must not be empty")
    if (r5_quest_talk_artifact_path is None) != (r5_quest_talk_runtime_manifest_path is None):
        raise ValueError("R5 Quest/Talk artifact and runtime manifest must be supplied together")
    r5_quest_talk_repositories: tuple[Any, ...] = ()
    if r5_quest_talk_bundle_paths is not None:
        canonical_bundles = tuple(
            (
                artifact_path,
                _require_canonical_r5_quest_talk_manifest(runtime_manifest_path),
            )
            for artifact_path, runtime_manifest_path in r5_quest_talk_bundle_paths
        )
        import json
        grouped_bundles = {}
        for artifact, manifest in canonical_bundles:
            contract = json.loads(Path(manifest).read_text())["family_contract_version"]
            grouped_bundles.setdefault(contract, []).append((artifact, manifest))
        r5_quest_talk_repositories = tuple(
            build_quest_talk_multi_run_provider(grouped_bundles[contract])
            for contract in sorted(grouped_bundles)
        )
    elif r5_quest_talk_artifact_path is not None and r5_quest_talk_runtime_manifest_path is not None:
        canonical_r5_manifest = _require_canonical_r5_quest_talk_manifest(
            r5_quest_talk_runtime_manifest_path
        )
        r5_quest_talk_repositories = (
            build_r5_quest_talk_provider(r5_quest_talk_artifact_path, canonical_r5_manifest),
        )
    server = MCPServer(name=SERVER_NAME, version=SERVER_VERSION)
    r2_repositories, r2_runtime, r2_qualification = _qualify_r2_startup(
        db, batch_r2_release_receipt_path, batch_r2_runtime_manifest_path,
    )
    r4_repositories = ()
    r4_runtime = {"enabled": False, "product_accepted": False, "provider_count": 0,
                  "schema_version": 20, "corpus_complete": False,
                  "reason": "explicit_r4_manifest_and_receipt_required" if _get_schema_version(db) == 20 else None}
    active_qualification = r2_qualification
    if r4_configured:
        # Explicit invalid R4 bundles stop startup; never select a sibling run.
        active_qualification = qualify_batch_r4(db.path,
            manifest_path=batch_r4_runtime_manifest_path, receipt_path=batch_r4_release_receipt_path)
        r4_repositories = build_batch_r4_providers(db, active_qualification)
        r4_runtime = {**active_qualification.to_dict(), "provider_families": list(R4_PROVIDER_ORDER),
                      "qualification_count": 1, "candidate_hash_count": 1, "reason": None}
    if active_qualification is not None:
        cap_info = inspect_static_capabilities(
            db,
            StaticArtifactSeal(
                f"batch-schema{active_qualification.schema_version}-accepted",
                active_qualification.candidate_sha256,
                active_qualification.candidate_size_bytes,
            ),
        )
    else:
        cap_info = inspect_static_capabilities(db)
    character_capability = inspect_character_capability(db)
    tutorial_capability = inspect_tutorial_capability(db)
    if active_qualification is not None:
        world_archive_capability = qualify_world_archive_r2_compatibility(
            db, world_archive_receipt_path, active_qualification,
        )
        quest_codex_capability = qualify_quest_codex_r2_compatibility(
            db, quest_codex_receipt_path, active_qualification,
        )
    else:
        world_archive_capability = inspect_world_archive_capability(
            db, world_archive_receipt_path
        )
        quest_codex_capability = inspect_quest_codex_capability(
            db, quest_codex_receipt_path
        )
    batch_repositories = build_batch_m4_repositories(db, batch_wave_receipt_path)
    batch_schema_available = any(repository.capability().get("schema_supported") for repository in batch_repositories)
    routed_batch_repositories = batch_repositories if batch_schema_available else ()
    m5d_repositories, m5d_runtime = _qualify_m5d_startup(
        db, batch_m5d_report_path, batch_m5d_runtime_manifest_path,
        batch_m5d_independent_report_path,
    )
    m5e_repositories, m5e_runtime = _qualify_m5e_startup(
        db,
        batch_m5e_release_receipt_path,
        batch_m5e_runtime_manifest_path,
        world_archive_receipt_path,
        quest_codex_receipt_path,
    )
    if (loading_tips_artifact_path is None) != (loading_tips_manifest_path is None):
        raise ValueError("LoadingTips requires explicit artifact and manifest")
    loading_tips_repositories = []
    if loading_tips_artifact_path is not None:
        from .loading_tips_retrieval import LoadingTipsProvider
        loading_tips_repositories.append(LoadingTipsProvider(loading_tips_artifact_path, loading_tips_manifest_path))
    if (npc_dialogue_artifact_path is None) != (npc_dialogue_manifest_path is None):
        raise ValueError("NPC dialogue requires explicit artifact and manifest")
    npc_dialogue_repositories = []
    if npc_dialogue_artifact_path is not None:
        from .npc_dialogue_retrieval import NPCDialogueProvider
        npc_dialogue_repositories.append(NPCDialogueProvider(npc_dialogue_artifact_path, npc_dialogue_manifest_path))
    if (character_lore_artifact_path is None) != (character_lore_manifest_path is None):
        raise ValueError("Character lore requires explicit artifact and manifest")
    character_lore_repositories = []
    if character_lore_artifact_path is not None:
        from .character_lore_retrieval import CharacterLoreProvider
        character_lore_repositories.append(CharacterLoreProvider(character_lore_artifact_path, character_lore_manifest_path))
    if (talk_physical_artifact_path is None) != (talk_physical_manifest_path is None):
        raise ValueError("Talk physical closure requires explicit artifact and manifest")
    talk_physical_repositories = []
    if talk_physical_artifact_path is not None:
        from .talk_physical_closure_retrieval import TalkPhysicalClosureProvider
        talk_physical_repositories.append(TalkPhysicalClosureProvider(talk_physical_artifact_path, talk_physical_manifest_path))
    if (quest_narrative_artifact_path is None) != (quest_narrative_manifest_path is None):
        raise ValueError("Quest narrative requires explicit artifact and manifest")
    quest_narrative_repositories = []
    if quest_narrative_artifact_path is not None:
        from .quest_narrative_retrieval import QuestNarrativeProvider
        quest_narrative_repositories.append(QuestNarrativeProvider(quest_narrative_artifact_path, quest_narrative_manifest_path))
    if (quest_review_artifact_path is None) != (quest_review_manifest_path is None):
        raise ValueError("Quest review requires explicit artifact and manifest")
    quest_review_repositories = []
    if quest_review_artifact_path is not None:
        from .quest_review_retrieval import QuestReviewProvider
        quest_review_repositories.append(QuestReviewProvider(quest_review_artifact_path, quest_review_manifest_path))
    if (codexquest_artifact_path is None) != (codexquest_manifest_path is None):
        raise ValueError("CodexQuest requires explicit artifact and manifest")
    codexquest_repositories = []
    if codexquest_artifact_path is not None:
        from .codexquest_retrieval import CodexQuestProvider
        codexquest_repositories.append(CodexQuestProvider(codexquest_artifact_path, codexquest_manifest_path))
    if (document_artifact_path is None) != (document_manifest_path is None):
        raise ValueError("Document requires explicit artifact and manifest")
    document_repositories = []
    if document_artifact_path is not None:
        from .document_retrieval import DocumentProvider
        document_repositories.append(DocumentProvider(document_artifact_path, document_manifest_path))
    if (gcg_card_artifact_path is None) != (gcg_card_manifest_path is None):
        raise ValueError("GCGCard requires explicit artifact and manifest")
    gcg_card_repositories = []
    if gcg_card_artifact_path is not None:
        from .gcg_card_retrieval import GCGCardProvider
        gcg_card_repositories.append(GCGCardProvider(gcg_card_artifact_path, gcg_card_manifest_path))
    if (mail_archive_artifact_path is None) != (mail_archive_manifest_path is None):
        raise ValueError("Mail archive requires explicit artifact and manifest")
    mail_archive_repositories = []
    if mail_archive_artifact_path is not None:
        from .mail_archive_retrieval import MailArchiveProvider
        mail_archive_repositories.append(MailArchiveProvider(mail_archive_artifact_path, mail_archive_manifest_path))
    if (event_narrative_artifact_path is None) != (event_narrative_manifest_path is None):
        raise ValueError("Event narrative requires explicit artifact and manifest")
    if (event_narrative_successor_artifact_path is None) != (event_narrative_successor_manifest_path is None):
        raise ValueError("Event narrative successor requires explicit artifact and manifest")
    if (event_narrative_filmfest_artifact_path is None) != (event_narrative_filmfest_manifest_path is None):
        raise ValueError("Event narrative Filmfest requires explicit artifact and manifest")
    if (event_narrative_wave_artifact_path is None) != (event_narrative_wave_manifest_path is None):
        raise ValueError("Event narrative archive wave requires explicit artifact and manifest")
    if (event_narrative_tps_artifact_path is None) != (event_narrative_tps_manifest_path is None):
        raise ValueError("Event narrative TpsGlassesChat requires explicit artifact and manifest")
    if (event_narrative_photo_echo_artifact_path is None) != (event_narrative_photo_echo_manifest_path is None):
        raise ValueError("Event narrative Photograph/EchoShell requires explicit artifact and manifest")
    if (event_narrative_photo_echo_delta_artifact_path is None) != (event_narrative_photo_echo_delta_manifest_path is None):
        raise ValueError("Event narrative Photograph/EchoShell delta requires explicit artifact and manifest")
    if (event_narrative_native_text_artifact_path is None) != (event_narrative_native_text_manifest_path is None):
        raise ValueError("Event narrative native-text wave requires explicit artifact and manifest")
    if (event_narrative_dialog_reminder_artifact_path is None) != (event_narrative_dialog_reminder_manifest_path is None):
        raise ValueError("Event narrative Generic Dialog/Reminder requires explicit artifact and manifest")
    event_narrative_repositories = []
    if event_narrative_artifact_path is not None:
        from .event_narrative_retrieval import EventNarrativeProvider
        event_narrative_repositories.append(EventNarrativeProvider(event_narrative_artifact_path, event_narrative_manifest_path))
    if event_narrative_successor_artifact_path is not None:
        from .event_narrative_retrieval import EventNarrativeProvider
        event_narrative_repositories.append(EventNarrativeProvider(event_narrative_successor_artifact_path, event_narrative_successor_manifest_path))
    if event_narrative_filmfest_artifact_path is not None:
        from .event_narrative_retrieval import EventNarrativeProvider
        event_narrative_repositories.append(EventNarrativeProvider(event_narrative_filmfest_artifact_path, event_narrative_filmfest_manifest_path))
    if event_narrative_wave_artifact_path is not None:
        from .event_narrative_retrieval import EventNarrativeProvider
        event_narrative_repositories.append(EventNarrativeProvider(event_narrative_wave_artifact_path, event_narrative_wave_manifest_path))
    if event_narrative_tps_artifact_path is not None:
        from .event_narrative_retrieval import EventNarrativeProvider
        event_narrative_repositories.append(EventNarrativeProvider(event_narrative_tps_artifact_path, event_narrative_tps_manifest_path))
    if event_narrative_photo_echo_artifact_path is not None:
        from .event_narrative_retrieval import EventNarrativeProvider
        event_narrative_repositories.append(EventNarrativeProvider(event_narrative_photo_echo_artifact_path, event_narrative_photo_echo_manifest_path))
    if event_narrative_photo_echo_delta_artifact_path is not None:
        from .event_narrative_retrieval import EventNarrativeProvider
        event_narrative_repositories.append(EventNarrativeProvider(event_narrative_photo_echo_delta_artifact_path, event_narrative_photo_echo_delta_manifest_path))
    if event_narrative_native_text_artifact_path is not None:
        from .event_narrative_retrieval import EventNarrativeProvider
        event_narrative_repositories.append(EventNarrativeProvider(event_narrative_native_text_artifact_path, event_narrative_native_text_manifest_path))
    if event_narrative_dialog_reminder_artifact_path is not None:
        from .event_narrative_retrieval import EventNarrativeProvider
        event_narrative_repositories.append(EventNarrativeProvider(event_narrative_dialog_reminder_artifact_path, event_narrative_dialog_reminder_manifest_path))
    if event_narrative_repositories:
        from .event_narrative_retrieval import combine_event_narrative_providers
        event_narrative_repositories=combine_event_narrative_providers(event_narrative_repositories)
    if (readable_archive_artifact_path is None)!=(readable_archive_manifest_path is None):raise ValueError('Readable archive requires explicit artifact and manifest')
    if (subtitle_archive_artifact_path is None)!=(subtitle_archive_manifest_path is None):raise ValueError('Subtitle archive requires explicit artifact and manifest')
    direct_text_repositories=[]
    if readable_archive_artifact_path is not None:
        from .direct_text_retrieval import ReadableArchiveProvider
        direct_text_repositories.append(ReadableArchiveProvider(readable_archive_artifact_path,readable_archive_manifest_path))
    if subtitle_archive_artifact_path is not None:
        from .direct_text_retrieval import SubtitleArchiveProvider
        direct_text_repositories.append(SubtitleArchiveProvider(subtitle_archive_artifact_path,subtitle_archive_manifest_path))
    if (native_archive_artifact_path is None)!=(native_archive_manifest_path is None):raise ValueError('Native archive requires explicit artifact and manifest')
    if (native_message_archive_artifact_path is None)!=(native_message_archive_manifest_path is None):raise ValueError('Native message archive requires explicit artifact and manifest')
    if (native_book_music_archive_artifact_path is None)!=(native_book_music_archive_manifest_path is None):raise ValueError('Native Book/Music archive requires explicit artifact and manifest')
    if (native_text_bulk_archive_artifact_path is None)!=(native_text_bulk_archive_manifest_path is None):raise ValueError('Native text bulk archive requires explicit artifact and manifest')
    if (aux_quest_delta_archive_artifact_path is None)!=(aux_quest_delta_archive_manifest_path is None):raise ValueError('Auxiliary Quest delta archive requires explicit artifact and manifest')
    if (high_value_native_text_artifact_path is None)!=(high_value_native_text_manifest_path is None):raise ValueError('High-value Native Text archive requires explicit artifact and manifest')
    native_archive_repositories=[]
    if native_archive_artifact_path is not None:
        from .native_archive_retrieval import NativeArchiveProvider
        native_archive_repositories.append(NativeArchiveProvider(native_archive_artifact_path,native_archive_manifest_path))
    if native_message_archive_artifact_path is not None:
        from .native_archive_retrieval import NativeArchiveProvider
        native_archive_repositories.append(NativeArchiveProvider(native_message_archive_artifact_path,native_message_archive_manifest_path))
    if native_book_music_archive_artifact_path is not None:
        from .native_archive_retrieval import NativeArchiveProvider
        native_archive_repositories.append(NativeArchiveProvider(native_book_music_archive_artifact_path,native_book_music_archive_manifest_path))
    if native_text_bulk_archive_artifact_path is not None:
        from .native_archive_retrieval import NativeArchiveProvider
        native_archive_repositories.append(NativeArchiveProvider(native_text_bulk_archive_artifact_path,native_text_bulk_archive_manifest_path))
    if aux_quest_delta_archive_artifact_path is not None:
        from .native_archive_retrieval import NativeArchiveProvider
        native_archive_repositories.append(NativeArchiveProvider(aux_quest_delta_archive_artifact_path,aux_quest_delta_archive_manifest_path))
    if high_value_native_text_artifact_path is not None:
        from .native_archive_retrieval import NativeArchiveProvider
        native_archive_repositories.append(NativeArchiveProvider(high_value_native_text_artifact_path,high_value_native_text_manifest_path))
    if native_archive_repositories:
        from .native_archive_retrieval import combine_native_archive_providers
        native_archive_repositories=combine_native_archive_providers(native_archive_repositories)
    family_router = FamilyEvidenceRouter(
        [
            WorldArchiveRepository(db, world_archive_capability),
            QuestCodexRepository(db, quest_codex_capability),
            *routed_batch_repositories,
            *m5d_repositories,
            *m5e_repositories,
            *r2_repositories,
            *r4_repositories,
            *r0_repositories,
            *q2_repositories,
            *r3_repositories,
            *r5_quest_talk_repositories,
            *loading_tips_repositories,
            *gcg_card_repositories,
            *document_repositories,
            *npc_dialogue_repositories,
            *character_lore_repositories,
            *talk_physical_repositories,
            *quest_narrative_repositories,
            *quest_review_repositories,
            *codexquest_repositories,
            *mail_archive_repositories,
            *event_narrative_repositories,
            *direct_text_repositories,
            *native_archive_repositories,
        ]
    )
    batch_runtime_enabled = any(repository.capability().get("evidence_available") for repository in routed_batch_repositories)
    m5d_runtime_enabled = bool(m5d_runtime.get("enabled"))
    m5e_runtime_enabled = bool(m5e_runtime.get("enabled"))
    r2_runtime_enabled = bool(r2_runtime.get("enabled"))
    routed_runtime_enabled = batch_runtime_enabled or m5d_runtime_enabled or m5e_runtime_enabled or r2_runtime_enabled or bool(r4_runtime.get("enabled"))
    # A Schema 16 batch candidate retains the historical 25-tool registry.  A
    # provider that is not qualified still returns its ordinary NotFound /
    # ineligible result; exposing the stable names does not select a fallback
    # run or alter the old contracts.
    static_eligible = bool(cap_info["schema_supported"]) or routed_runtime_enabled
    character_eligible = character_capability.eligible or routed_runtime_enabled
    tutorial_eligible = tutorial_capability.eligible or routed_runtime_enabled
    registered_tools = list(BASE_TOOLS)
    if static_eligible:
        registered_tools.extend(STATIC_TOOLS)
    if character_eligible:
        registered_tools.extend(CHARACTER_TOOLS)
    if tutorial_eligible:
        registered_tools.extend(TUTORIAL_TOOLS)
    registered_tools.extend(FAMILY_EVIDENCE_TOOLS)

    @server.tool(
        name="get_capabilities",
        description="Return server capabilities, API version, and readonly allowlist metadata.",
        annotations=READONLY_ANNOTATIONS,
    )
    def get_capabilities() -> dict[str, Any]:
        data_families = list(cap_info["data_families"])
        if character_eligible and "character" not in data_families:
            data_families.append("character")
        if tutorial_eligible and "tutorial" not in data_families:
            data_families.append("tutorial")
        if world_archive_capability.evidence_available and "world_archive" not in data_families:
            data_families.append("world_archive")
        if quest_codex_capability.evidence_available and "quest_codex_archive" not in data_families:
            data_families.append("quest_codex_archive")
        for batch_repository in batch_repositories:
            if batch_repository.capability().get("evidence_available"):
                if batch_repository.family not in data_families:
                    data_families.append(batch_repository.family)
        for m5d_repository in m5d_repositories:
            if m5d_repository.capability().get("evidence_available"):
                if m5d_repository.family not in data_families:
                    data_families.append(m5d_repository.family)
        for m5e_repository in m5e_repositories:
            if m5e_repository.capability().get("evidence_available"):
                if m5e_repository.family not in data_families:
                    data_families.append(m5e_repository.family)
        for r2_repository in (*r2_repositories, *r4_repositories):
            if r2_repository.capability().get("evidence_available"):
                if r2_repository.family not in data_families:
                    data_families.append(r2_repository.family)
        for repository in (*r0_repositories, *q2_repositories, *r3_repositories):
            if repository.capability().get("evidence_available"):
                if repository.family not in data_families:
                    data_families.append(repository.family)
        for repository in r5_quest_talk_repositories:
            if repository.capability().get("evidence_available") and repository.family not in data_families:
                data_families.append(repository.family)
        return _envelope(
            db,
            status="ok",
            data={
                "server": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
                "api_version": API_VERSION,
                "data_families": data_families,
                "static_families": cap_info["static_families"],
                "read_only": True,
                "tools": registered_tools,
                "forbidden_capabilities": list(FORBIDDEN_CAPABILITIES),
                "static_capability": {
                    "schema_supported": cap_info["schema_supported"],
                    "evidence_available": cap_info["evidence_available"],
                    "completed_ingest_runs": cap_info["completed_ingest_runs"],
                    "family_coverage": cap_info["family_coverage"],
                },
                "characters": character_capability.to_dict(),
                "tutorial": tutorial_capability.to_dict(),
                "family_providers": family_router.capabilities(),
                "batch_wave_runtime": {
                    "enabled": batch_runtime_enabled,
                    "receipt_path": "explicit_server_configuration_only",
                    "receipt_trust": "file_prefilter_not_authentication",
                    "runtime_manifest": "tracked_manifest_canonical_business_oracle",
                    "schema_contract": "schema16-migration0016-additive-wave01-exact",
                    "canonical_rowset_validation": True,
                    "provenance_table_validation": True,
                    "batch_metadata_validation": True,
                    "schema_version": 16,
                    "provider_registry": {
                        "family_provider_registry_changed": True,
                        "top_level_tool_registry_changed": False,
                        "top_level_tool_count": len(TOOLS),
                    },
                },
                "batch_m5d_runtime": m5d_runtime,
                "batch_m5e_runtime": m5e_runtime,
                "batch_r2_runtime": r2_runtime,
                "batch_r4_runtime": r4_runtime,
                "batch_r3_candidate": {
                    "enabled": bool(r3_repositories),
                    "provider_count": len(r3_repositories),
                    "provider_families": list(R3_FAMILY_ORDER) if r3_repositories else [],
                    "coverage": "R1_declared_text_fields_processed",
                    "product_accepted": False,
                    "corpus_complete": False,
                    "top_level_tool_count": len(TOOLS),
                },
                "r5_quest_talk_candidate": {
                    "enabled": bool(r5_quest_talk_repositories),
                    "provider_count": len(r5_quest_talk_repositories),
                    "provider_families": [repository.family for repository in r5_quest_talk_repositories],
                    "run_count": (
                        len(r5_quest_talk_repositories[0].capability().get("eligible_runs", []))
                        if r5_quest_talk_repositories
                        else 0
                    ),
                    "coverage": (
                        r5_quest_talk_repositories[0].capability()["coverage_semantics"]["coverage"]
                        if r5_quest_talk_repositories
                        else "bounded_quest_71008_talk_candidate_closure"
                    ),
                    "product_accepted": False,
                    "corpus_complete": False,
                    "top_level_tool_count": len(TOOLS),
                },
            },
        )

    @server.tool(
        name="get_schema_version",
        description="Return current database schema migration version and applied versions.",
        annotations=READONLY_ANNOTATIONS,
    )
    def get_schema_version() -> dict[str, Any]:
        schema_version = _get_schema_version(db)
        applied_rows = db.conn.execute(
            "SELECT version FROM schema_migration ORDER BY version"
        ).fetchall()
        applied_versions = [int(row[0]) for row in applied_rows]
        return _envelope(
            db,
            status="ok",
            data={
                "api_version": API_VERSION,
                "database_schema_version": schema_version,
                "applied_migration_versions": applied_versions,
                "read_only": True,
            },
        )

    @server.tool(
        name="lookup_occurrence",
        description="Cardinality-aware dialog occurrence lookup by Talk native triple.",
        annotations=READONLY_ANNOTATIONS,
    )
    def lookup_occurrence(
        ingest_run_id: Annotated[str, Field(min_length=1, strict=True)],
        namespace: Annotated[str, Field(min_length=1, strict=True)],
        talk_id: Annotated[str, Field(min_length=1, strict=True)],
        node_id: Annotated[str, Field(min_length=1, strict=True)],
    ) -> dict[str, Any]:
        result = find_dialog_occurrence(
            db,  # type: ignore[arg-type]
            ingest_run_id=ingest_run_id,
            namespace=namespace,
            talk_id=talk_id,
            node_id=node_id,
        )
        if isinstance(result, NotFound):
            return _envelope(
                db,
                status="not_found",
                data={"lookup": list(result.lookup)},
            )
        if isinstance(result, AmbiguousLookup):
            return _envelope(
                db,
                status="ambiguous",
                data={
                    "lookup": list(result.lookup),
                    "candidates": list(result.candidates),
                },
            )
        return _envelope(
            db,
            status="ok",
            data=result,
        )

    @server.tool(
        name="get_context",
        description="Return occurrence-scoped dialog context within its evidence container.",
        annotations=READONLY_ANNOTATIONS,
    )
    def get_context(
        passage_occurrence_id: Annotated[str, Field(min_length=1, strict=True)],
        before: Annotated[int, Field(default=2, ge=0, le=50, strict=True)] = 2,
        after: Annotated[int, Field(default=2, ge=0, le=50, strict=True)] = 2,
        snapshot_id: Annotated[str | None, Field(default=None)] = None,
    ) -> dict[str, Any]:
        rows = _occurrence_rows(
            db,  # type: ignore[arg-type]
            " WHERE po.passage_occurrence_id=?",
            (str(passage_occurrence_id),),
        )
        if not rows:
            return _envelope(db, status="not_found", data=None)

        occurrence_row = rows[0]
        if snapshot_id is not None and str(snapshot_id) != str(occurrence_row["snapshot_id"]):
            return _envelope(db, status="not_found", data=None)

        context_data = _occurrence_context(
            db,  # type: ignore[arg-type]
            occurrence_row,
            before,
            after,
        )
        return _envelope(db, status="ok", data=context_data)

    @server.tool(
        name="get_provenance",
        description="Return evidence container provenance lineage for an occurrence.",
        annotations=READONLY_ANNOTATIONS,
    )
    def get_provenance(
        passage_occurrence_id: Annotated[str, Field(min_length=1, strict=True)],
    ) -> dict[str, Any]:
        rows = _occurrence_rows(
            db,  # type: ignore[arg-type]
            " WHERE po.passage_occurrence_id=?",
            (str(passage_occurrence_id),),
        )
        if not rows:
            return _envelope(db, status="not_found", data=None)

        occurrence = _occurrence_item(
            db,  # type: ignore[arg-type]
            rows[0],
            include_scope=True,
        )
        scope = occurrence["scope"]
        source_record = scope.get("source_record")
        source_file_relative_path = (
            source_record.get("relative_path") if source_record else None
        )
        data = {
            "snapshot": scope["snapshot"],
            "ingest_run": scope["run"],
            "source_file_relative_path": source_file_relative_path,
            "source_record": source_record,
            "source_pointer": occurrence.get("source_pointer"),
            "evidence_container": scope["container"],
            "occurrence_id": occurrence["passage_occurrence_id"],
        }
        return _envelope(db, status="ok", data=data)

    @server.tool(
        name="resolve_quest_reference",
        description="Query persisted quest reference evidence links and candidates.",
        annotations=READONLY_ANNOTATIONS,
    )
    def resolve_quest_reference(
        quest_id: Annotated[str, Field(min_length=1, strict=True)],
        snapshot_id: Annotated[str | None, Field(default=None)] = None,
    ) -> dict[str, Any]:
        where_clauses = ["de.document_id = ?"]
        params: list[Any] = [str(quest_id)]
        if snapshot_id is not None:
            where_clauses.append("ir.snapshot_id = ?")
            params.append(str(snapshot_id))

        sql = f"""
            SELECT de.id AS document_evidence_id, de.document_id, de.ingest_run_id,
               de.namespace, de.talk_id, de.result, de.evidence_container_id,
               de.identity_group_id, de.source_record_id, de.metadata_json,
               ir.snapshot_id
            FROM document_evidence de
            JOIN ingest_run ir ON ir.id=de.ingest_run_id
            WHERE {' AND '.join(where_clauses)}
            ORDER BY de.namespace, CAST(de.talk_id AS INTEGER), de.talk_id, de.source_record_id
        """
        rows = db.conn.execute(sql, params).fetchall()
        references: list[dict[str, Any]] = []
        for row in rows:
            item: dict[str, Any] = {
                "document_evidence_id": row["document_evidence_id"],
                "document_id": row["document_id"],
                "ingest_run_id": row["ingest_run_id"],
                "snapshot_id": row["snapshot_id"],
                "namespace": row["namespace"],
                "talk_id": row["talk_id"],
                "result": row["result"],
                "evidence_container_id": row["evidence_container_id"],
                "identity_group_id": row["identity_group_id"],
                "source_record_id": row["source_record_id"],
                "metadata": _json(row["metadata_json"], {}),
                "provenance": _source(db, row["source_record_id"]),  # type: ignore[arg-type]
            }
            if row["evidence_container_id"] is not None:
                item["candidate_container_ids"] = [str(row["evidence_container_id"])]
            elif row["identity_group_id"] is not None:
                cand_rows = db.conn.execute(
                    """SELECT evidence_container_id
                       FROM identity_group_container
                       WHERE identity_group_id=? AND ingest_run_id=?
                       ORDER BY evidence_container_id""",
                    (row["identity_group_id"], row["ingest_run_id"]),
                ).fetchall()
                item["candidate_container_ids"] = [str(c[0]) for c in cand_rows]
            else:
                item["candidate_container_ids"] = []
            references.append(item)

        return _envelope(
            db,
            status="ok",
            data={
                "quest_id": quest_id,
                "references": references,
            },
        )

    @server.tool(
        name="list_evidence_containers",
        description="List evidence containers with scoping, filters, and pagination.",
        annotations=READONLY_ANNOTATIONS,
    )
    def list_evidence_containers(
        ingest_run_id: Annotated[str, Field(min_length=1, strict=True)],
        namespace: Annotated[str | None, Field(default=None)] = None,
        talk_id: Annotated[str | None, Field(default=None)] = None,
        limit: Annotated[int, Field(default=50, ge=1, le=100, strict=True)] = 50,
        offset: Annotated[int, Field(default=0, ge=0, strict=True)] = 0,
    ) -> dict[str, Any]:
        where_clauses = ["ec.ingest_run_id = ?"]
        params: list[Any] = [str(ingest_run_id)]
        if namespace is not None:
            where_clauses.append("ec.namespace = ?")
            params.append(str(namespace))
        if talk_id is not None:
            where_clauses.append("ec.talk_id = ?")
            params.append(str(talk_id))

        where_sql = " WHERE " + " AND ".join(where_clauses)
        count_row = db.conn.execute(
            f"SELECT COUNT(*) FROM evidence_container ec {where_sql}",
            params,
        ).fetchone()
        total = int(count_row[0]) if count_row else 0

        sql = f"""
            SELECT ec.id, ec.ingest_run_id, ec.source_record_id,
                   ec.kind, ec.namespace, ec.talk_id, ec.raw_checksum,
                   ec.normalized_checksum, ec.metadata_json,
                   ir.parser_build_id, ir.scope_json AS run_scope_json,
                   ir.config_json AS run_config_json, ir.status AS run_status,
                   ir.created_at AS run_created_at,
                   ss.id AS snapshot_id, ss.source_id AS snapshot_source_id,
                   ss.release_label AS snapshot_release_label,
                   ss.build AS snapshot_build, ss."commit" AS snapshot_commit,
                   ss.tree AS snapshot_tree, ss.observed_at AS snapshot_observed_at,
                   ss.parser_version AS snapshot_parser_version,
                   ss.mapping_version AS snapshot_mapping_version,
                   ss.status AS snapshot_status,
                   ss.metadata_json AS snapshot_metadata_json
            FROM evidence_container ec
            JOIN ingest_run ir ON ir.id=ec.ingest_run_id
            JOIN source_snapshot ss ON ss.id=ir.snapshot_id
            {where_sql}
            ORDER BY ec.id ASC
            LIMIT ? OFFSET ?
        """
        rows = db.conn.execute(sql, params + [limit, offset]).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            items.append(
                {
                    "id": row["id"],
                    "ingest_run_id": row["ingest_run_id"],
                    "snapshot_id": row["snapshot_id"],
                    "source_record_id": row["source_record_id"],
                    "namespace": row["namespace"],
                    "talk_id": row["talk_id"],
                    "kind": row["kind"],
                    "raw_checksum": row["raw_checksum"],
                    "normalized_checksum": row["normalized_checksum"],
                    "metadata": _json(row["metadata_json"], {}),
                    "scope": {
                        "run": {
                            "id": row["ingest_run_id"],
                            "snapshot_id": row["snapshot_id"],
                            "parser_build_id": row["parser_build_id"],
                            "scope": _json(row["run_scope_json"], {}),
                            "config": _json(row["run_config_json"], {}),
                            "status": row["run_status"],
                            "created_at": row["run_created_at"],
                        },
                        "snapshot": {
                            "id": row["snapshot_id"],
                            "source_id": row["snapshot_source_id"],
                            "release_label": row["snapshot_release_label"],
                            "build": row["snapshot_build"],
                            "commit": row["snapshot_commit"],
                            "tree": row["snapshot_tree"],
                            "observed_at": row["snapshot_observed_at"],
                            "parser_version": row["snapshot_parser_version"],
                            "mapping_version": row["snapshot_mapping_version"],
                            "status": row["snapshot_status"],
                            "metadata": _json(row["snapshot_metadata_json"], {}),
                        },
                        "container": {
                            "id": row["id"],
                            "ingest_run_id": row["ingest_run_id"],
                            "source_record_id": row["source_record_id"],
                            "kind": row["kind"],
                            "namespace": row["namespace"],
                            "talk_id": row["talk_id"],
                            "raw_checksum": row["raw_checksum"],
                            "normalized_checksum": row["normalized_checksum"],
                            "metadata": _json(row["metadata_json"], {}),
                        },
                        "source_record": _source(db, row["source_record_id"]),  # type: ignore[arg-type]
                    },
                }
            )

        return _envelope(
            db,
            status="ok",
            data={
                "items": items,
                "total": total,
                "limit": limit,
                "offset": offset,
            },
        )

    @server.tool(
        name="list_ambiguities",
        description="List persisted document evidence ambiguities and candidate container IDs.",
        annotations=READONLY_ANNOTATIONS,
    )
    def list_ambiguities(
        ingest_run_id: Annotated[str | None, Field(default=None)] = None,
        quest_id: Annotated[str | None, Field(default=None)] = None,
        snapshot_id: Annotated[str | None, Field(default=None)] = None,
        limit: Annotated[int, Field(default=50, ge=1, le=100, strict=True)] = 50,
        offset: Annotated[int, Field(default=0, ge=0, strict=True)] = 0,
    ) -> dict[str, Any]:
        where_clauses = ["de.result = 'ambiguous'"]
        params: list[Any] = []
        if ingest_run_id is not None:
            where_clauses.append("de.ingest_run_id = ?")
            params.append(str(ingest_run_id))
        if quest_id is not None:
            where_clauses.append("de.document_id = ?")
            params.append(str(quest_id))
        if snapshot_id is not None:
            where_clauses.append("ir.snapshot_id = ?")
            params.append(str(snapshot_id))

        where_sql = " WHERE " + " AND ".join(where_clauses)
        count_row = db.conn.execute(
            f"""SELECT COUNT(*)
                FROM document_evidence de
                JOIN ingest_run ir ON ir.id=de.ingest_run_id
                {where_sql}""",
            params,
        ).fetchone()
        total = int(count_row[0]) if count_row else 0

        sql = f"""
            SELECT de.id AS document_evidence_id, de.document_id, de.ingest_run_id,
                   de.namespace, de.talk_id, de.result, de.evidence_container_id,
                   de.identity_group_id, de.source_record_id, de.metadata_json,
                   ir.snapshot_id
            FROM document_evidence de
            JOIN ingest_run ir ON ir.id=de.ingest_run_id
            {where_sql}
            ORDER BY de.id ASC
            LIMIT ? OFFSET ?
        """
        rows = db.conn.execute(sql, params + [limit, offset]).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item: dict[str, Any] = {
                "document_evidence_id": row["document_evidence_id"],
                "document_id": row["document_id"],
                "ingest_run_id": row["ingest_run_id"],
                "snapshot_id": row["snapshot_id"],
                "namespace": row["namespace"],
                "talk_id": row["talk_id"],
                "result": row["result"],
                "evidence_container_id": row["evidence_container_id"],
                "identity_group_id": row["identity_group_id"],
                "source_record_id": row["source_record_id"],
                "metadata": _json(row["metadata_json"], {}),
                "provenance": _source(db, row["source_record_id"]),  # type: ignore[arg-type]
            }
            if row["identity_group_id"] is not None:
                cand_rows = db.conn.execute(
                    """SELECT evidence_container_id
                       FROM identity_group_container
                       WHERE identity_group_id=? AND ingest_run_id=?
                       ORDER BY evidence_container_id""",
                    (row["identity_group_id"], row["ingest_run_id"]),
                ).fetchall()
                item["candidate_container_ids"] = [str(c[0]) for c in cand_rows]
            elif row["evidence_container_id"] is not None:
                item["candidate_container_ids"] = [str(row["evidence_container_id"])]
            else:
                item["candidate_container_ids"] = []
            items.append(item)

        return _envelope(
            db,
            status="ok",
            data={
                "items": items,
                "total": total,
                "limit": limit,
                "offset": offset,
            },
        )

    if static_eligible:
        @server.tool(
            name="list_static_evidence_containers",
            description="List static evidence containers with scoping, filters, and pagination.",
            annotations=READONLY_ANNOTATIONS,
        )
        def list_static_evidence_containers_tool(
            ingest_run_id: Annotated[str, Field(min_length=1, strict=True)],
            source_family: Annotated[str | None, Field(default=None)] = None,
            container_kind: Annotated[str | None, Field(default=None)] = None,
            locale: Annotated[str | None, Field(default=None)] = None,
            candidate_namespace: Annotated[str | None, Field(default=None)] = None,
            limit: Annotated[int, Field(default=50, ge=1, le=100, strict=True)] = 50,
            offset: Annotated[int, Field(default=0, ge=0, strict=True)] = 0,
        ) -> dict[str, Any]:
            result = list_static_evidence_containers(
                db,
                ingest_run_id=ingest_run_id,
                source_family=source_family,
                container_kind=container_kind,
                locale=locale,
                candidate_namespace=candidate_namespace,
                limit=limit,
                offset=offset,
            )
            return _envelope(db, status="ok", data=result)

        @server.tool(
            name="lookup_static_evidence_container",
            description="Cardinality-aware static evidence container lookup by candidate key.",
            annotations=READONLY_ANNOTATIONS,
        )
        def lookup_static_evidence_container_tool(
            ingest_run_id: Annotated[str, Field(min_length=1, strict=True)],
            source_family: Annotated[str, Field(min_length=1, strict=True)],
            candidate_key: Annotated[dict[str, Any], Field(...)],
        ) -> dict[str, Any]:
            result = find_static_evidence_container(
                db,
                ingest_run_id=ingest_run_id,
                source_family=source_family,
                candidate_key=candidate_key,
            )
            if isinstance(result, StaticNotFound):
                return _envelope(
                    db,
                    status="not_found",
                    data={"lookup": result.lookup},
                )
            if isinstance(result, StaticAmbiguousLookup):
                return _envelope(
                    db,
                    status="ambiguous",
                    data={
                        "lookup": result.lookup,
                        "candidates": list(result.candidates),
                    },
                )
            return _envelope(
                db,
                status="ok",
                data=result,
            )

        @server.tool(
            name="get_static_evidence_container",
            description="Return static container provenance, container-local segments, and relationships.",
            annotations=READONLY_ANNOTATIONS,
        )
        def get_static_evidence_container_tool(
            ingest_run_id: Annotated[str, Field(min_length=1, strict=True)],
            evidence_container_id: Annotated[str, Field(min_length=1, strict=True)],
            segment_limit: Annotated[int, Field(default=100, ge=1, le=500, strict=True)] = 100,
            segment_offset: Annotated[int, Field(default=0, ge=0, strict=True)] = 0,
            relationship_limit: Annotated[int, Field(default=100, ge=1, le=500, strict=True)] = 100,
            relationship_offset: Annotated[int, Field(default=0, ge=0, strict=True)] = 0,
        ) -> dict[str, Any]:
            result = get_static_evidence_container(
                db,
                ingest_run_id=ingest_run_id,
                evidence_container_id=evidence_container_id,
                segment_limit=segment_limit,
                segment_offset=segment_offset,
                relationship_limit=relationship_limit,
                relationship_offset=relationship_offset,
            )
            if result is None:
                return _envelope(db, status="not_found", data=None)
            return _envelope(db, status="ok", data=result)

        @server.tool(
            name="list_static_relationship_evidence",
            description="List static relationship occurrences with scoping, filters, and pagination.",
            annotations=READONLY_ANNOTATIONS,
        )
        def list_static_relationship_evidence_tool(
            ingest_run_id: Annotated[str, Field(min_length=1, strict=True)],
            source_container_id: Annotated[str | None, Field(default=None)] = None,
            source_family: Annotated[str | None, Field(default=None)] = None,
            target_family: Annotated[str | None, Field(default=None)] = None,
            relation_kind: Annotated[str | None, Field(default=None)] = None,
            field_role: Annotated[str | None, Field(default=None)] = None,
            candidate_resolution_status: Annotated[str | None, Field(default=None)] = None,
            target_evidence_container_id: Annotated[str | None, Field(default=None)] = None,
            limit: Annotated[int, Field(default=50, ge=1, le=100, strict=True)] = 50,
            offset: Annotated[int, Field(default=0, ge=0, strict=True)] = 0,
        ) -> dict[str, Any]:
            result = list_static_relationship_evidence(
                db,
                ingest_run_id=ingest_run_id,
                source_container_id=source_container_id,
                source_family=source_family,
                target_family=target_family,
                relation_kind=relation_kind,
                field_role=field_role,
                candidate_resolution_status=candidate_resolution_status,
                target_evidence_container_id=target_evidence_container_id,
                limit=limit,
                offset=offset,
            )
            return _envelope(db, status="ok", data=result)

        @server.tool(
            name="get_static_relationship_evidence",
            description="Return complete evidence for a single relationship occurrence including target container and TextMap resolutions.",
            annotations=READONLY_ANNOTATIONS,
        )
        def get_static_relationship_evidence_tool(
            ingest_run_id: Annotated[str, Field(min_length=1, strict=True)],
            relationship_occurrence_id: Annotated[str, Field(min_length=1, strict=True)],
        ) -> dict[str, Any]:
            result = get_static_relationship_evidence(
                db,
                ingest_run_id=ingest_run_id,
                relationship_occurrence_id=relationship_occurrence_id,
            )
            if result is None:
                return _envelope(db, status="not_found", data=None)
            return _envelope(db, status="ok", data=result)

    if character_eligible:
        @server.tool(
            name="list_character_rows",
            description="List raw Character row occurrences in one exact sealed ingest run.",
            annotations=READONLY_ANNOTATIONS,
        )
        def list_character_rows_tool(
            ingest_run_id: Annotated[str, Field(min_length=1, strict=True)],
            table_name: Annotated[str | None, Field(default=None)] = None,
            native_locator_field: Annotated[str | None, Field(default=None)] = None,
            native_locator_value: Annotated[str | None, Field(default=None)] = None,
            avatar_id: Annotated[str | None, Field(default=None)] = None,
            limit: Annotated[int, Field(default=50, ge=1, le=100, strict=True)] = 50,
            offset: Annotated[int, Field(default=0, ge=0, strict=True)] = 0,
        ) -> dict[str, Any]:
            result = list_character_rows(
                db,
                ingest_run_id=ingest_run_id,
                table_name=table_name,
                native_locator_field=native_locator_field,
                native_locator_value=native_locator_value,
                avatar_id=avatar_id,
                limit=limit,
                offset=offset,
            )
            if isinstance(result, CharacterNotFound):
                return _envelope(
                    db,
                    status="not_found",
                    data=result.to_dict(),
                )
            return _envelope(db, status="ok", data=result)

        @server.tool(
            name="get_character_row_evidence",
            description="Return one raw Character row and its directly attached occurrence evidence.",
            annotations=READONLY_ANNOTATIONS,
        )
        def get_character_row_evidence_tool(
            ingest_run_id: Annotated[str, Field(min_length=1, strict=True)],
            row_occurrence_id: Annotated[str, Field(min_length=1, strict=True)],
            raw_slot_limit: Annotated[int, Field(default=500, ge=1, le=500, strict=True)] = 500,
            raw_slot_offset: Annotated[int, Field(default=0, ge=0, strict=True)] = 0,
            relationship_limit: Annotated[int, Field(default=500, ge=1, le=500, strict=True)] = 500,
            relationship_offset: Annotated[int, Field(default=0, ge=0, strict=True)] = 0,
        ) -> dict[str, Any]:
            result = get_character_row_evidence(
                db,
                ingest_run_id=ingest_run_id,
                row_occurrence_id=row_occurrence_id,
                raw_slot_limit=raw_slot_limit,
                raw_slot_offset=raw_slot_offset,
                relationship_limit=relationship_limit,
                relationship_offset=relationship_offset,
            )
            if isinstance(result, CharacterNotFound):
                return _envelope(
                    db,
                    status="not_found",
                    data=result.to_dict(),
                )
            return _envelope(db, status="ok", data=result)

        @server.tool(
            name="search_character_text_evidence",
            description="Search raw Character text resolution occurrences with literal locale-scoped semantics.",
            annotations=READONLY_ANNOTATIONS,
        )
        def search_character_text_evidence_tool(
            ingest_run_id: Annotated[str, Field(min_length=1, strict=True)],
            locale: Literal["CHS", "EN", "JP"],
            query: Annotated[str, Field(min_length=1, strict=True)],
            limit: Annotated[int, Field(default=50, ge=1, le=100, strict=True)] = 50,
            offset: Annotated[int, Field(default=0, ge=0, strict=True)] = 0,
        ) -> dict[str, Any]:
            result = search_character_text_evidence(
                db,
                ingest_run_id=ingest_run_id,
                locale=locale,
                query=query,
                limit=limit,
                offset=offset,
            )
            if isinstance(result, CharacterNotFound):
                return _envelope(
                    db,
                    status="not_found",
                    data=result.to_dict(),
                )
            return _envelope(db, status="ok", data=result)

        @server.tool(
            name="get_character_voice_container",
            description="Return one physical Character Voice container and its occurrence children.",
            annotations=READONLY_ANNOTATIONS,
        )
        def get_character_voice_container_tool(
            ingest_run_id: Annotated[str, Field(min_length=1, strict=True)],
            voice_container_id: Annotated[str, Field(min_length=1, strict=True)],
            limit: Annotated[int, Field(default=500, ge=1, le=500, strict=True)] = 500,
            offset: Annotated[int, Field(default=0, ge=0, strict=True)] = 0,
        ) -> dict[str, Any]:
            result = get_character_voice_container(
                db,
                ingest_run_id=ingest_run_id,
                voice_container_id=voice_container_id,
                limit=limit,
                offset=offset,
            )
            if isinstance(result, CharacterNotFound):
                return _envelope(
                    db,
                    status="not_found",
                    data=result.to_dict(),
                )
            return _envelope(db, status="ok", data=result)

        @server.tool(
            name="get_character_voice_context",
            description="Return a Character audio occurrence window inside one physical Voice container.",
            annotations=READONLY_ANNOTATIONS,
        )
        def get_character_voice_context_tool(
            ingest_run_id: Annotated[str, Field(min_length=1, strict=True)],
            audio_variant_occurrence_id: Annotated[str, Field(min_length=1, strict=True)],
            before: Annotated[int, Field(default=2, ge=0, le=50, strict=True)] = 2,
            after: Annotated[int, Field(default=2, ge=0, le=50, strict=True)] = 2,
        ) -> dict[str, Any]:
            result = get_character_voice_context(
                db,
                ingest_run_id=ingest_run_id,
                audio_variant_occurrence_id=audio_variant_occurrence_id,
                before=before,
                after=after,
            )
            if isinstance(result, CharacterNotFound):
                return _envelope(
                    db,
                    status="not_found",
                    data=result.to_dict(),
                )
            return _envelope(db, status="ok", data=result)

        for character_tool_name in CHARACTER_TOOLS:
            character_tool = server._tool_manager._tools[character_tool_name]
            argument_model = character_tool.fn_metadata.arg_model
            argument_model.model_config = {
                **argument_model.model_config,
                "extra": "forbid",
            }
            argument_model.model_rebuild(force=True)
            character_tool.parameters = argument_model.model_json_schema(
                by_alias=True
            )

    if tutorial_eligible:
        @server.tool(
            name="list_tutorial_rows",
            description="List physical Tutorial row occurrences in one exact sealed ingest run.",
            annotations=READONLY_ANNOTATIONS,
        )
        def list_tutorial_rows_tool(
            ingest_run_id: Annotated[str, Field(min_length=1, strict=True)],
            table_name: Annotated[str | None, Field(default=None)] = None,
            native_locator_field: Annotated[str | None, Field(default=None)] = None,
            native_locator_value: Annotated[str | None, Field(default=None)] = None,
            limit: Annotated[int, Field(default=50, ge=1, le=100, strict=True)] = 50,
            offset: Annotated[int, Field(default=0, ge=0, strict=True)] = 0,
        ) -> dict[str, Any]:
            result = list_tutorial_rows(
                db,
                ingest_run_id=ingest_run_id,
                table_name=table_name,
                native_locator_field=native_locator_field,
                native_locator_value=native_locator_value,
                limit=limit,
                offset=offset,
            )
            if isinstance(result, TutorialNotFound):
                return _envelope(
                    db,
                    status="not_found",
                    data=result.to_dict(),
                )
            return _envelope(db, status="ok", data=result)

        @server.tool(
            name="get_tutorial_row_evidence",
            description="Return one physical Tutorial row and its directly attached evidence.",
            annotations=READONLY_ANNOTATIONS,
        )
        def get_tutorial_row_evidence_tool(
            ingest_run_id: Annotated[str, Field(min_length=1, strict=True)],
            row_occurrence_id: Annotated[str, Field(min_length=1, strict=True)],
            raw_field_limit: Annotated[int, Field(default=500, ge=1, le=500, strict=True)] = 500,
            raw_field_offset: Annotated[int, Field(default=0, ge=0, strict=True)] = 0,
            relationship_limit: Annotated[int, Field(default=500, ge=1, le=500, strict=True)] = 500,
            relationship_offset: Annotated[int, Field(default=0, ge=0, strict=True)] = 0,
        ) -> dict[str, Any]:
            result = get_tutorial_row_evidence(
                db,
                ingest_run_id=ingest_run_id,
                row_occurrence_id=row_occurrence_id,
                raw_field_limit=raw_field_limit,
                raw_field_offset=raw_field_offset,
                relationship_limit=relationship_limit,
                relationship_offset=relationship_offset,
            )
            if isinstance(result, TutorialNotFound):
                return _envelope(
                    db,
                    status="not_found",
                    data=result.to_dict(),
                )
            return _envelope(db, status="ok", data=result)

        @server.tool(
            name="search_tutorial_text_evidence",
            description="Search Tutorial raw text with case-sensitive literal locale-scoped semantics.",
            annotations=READONLY_ANNOTATIONS,
        )
        def search_tutorial_text_evidence_tool(
            ingest_run_id: Annotated[str, Field(min_length=1, strict=True)],
            locale: Literal["CHS", "EN", "JP"],
            query: Annotated[str, Field(min_length=1, strict=True)],
            limit: Annotated[int, Field(default=50, ge=1, le=100, strict=True)] = 50,
            offset: Annotated[int, Field(default=0, ge=0, strict=True)] = 0,
        ) -> dict[str, Any]:
            result = search_tutorial_text_evidence(
                db,
                ingest_run_id=ingest_run_id,
                locale=locale,
                query=query,
                limit=limit,
                offset=offset,
            )
            if isinstance(result, TutorialNotFound):
                return _envelope(
                    db,
                    status="not_found",
                    data=result.to_dict(),
                )
            return _envelope(db, status="ok", data=result)

        @server.tool(
            name="get_tutorial_context",
            description="Return a stored detailIdList window within one physical Tutorial container.",
            annotations=READONLY_ANNOTATIONS,
        )
        def get_tutorial_context_tool(
            ingest_run_id: Annotated[str, Field(min_length=1, strict=True)],
            row_occurrence_id: Annotated[str, Field(min_length=1, strict=True)],
            center_detail_array_index: Annotated[int, Field(ge=0, strict=True)],
            before: Annotated[int, Field(default=2, ge=0, le=50, strict=True)] = 2,
            after: Annotated[int, Field(default=2, ge=0, le=50, strict=True)] = 2,
        ) -> dict[str, Any]:
            result = get_tutorial_context(
                db,
                ingest_run_id=ingest_run_id,
                row_occurrence_id=row_occurrence_id,
                center_detail_array_index=center_detail_array_index,
                before=before,
                after=after,
            )
            if isinstance(result, TutorialNotFound):
                return _envelope(
                    db,
                    status="not_found",
                    data=result.to_dict(),
                )
            return _envelope(db, status="ok", data=result)

        for tutorial_tool_name in TUTORIAL_TOOLS:
            tutorial_tool = server._tool_manager._tools[tutorial_tool_name]
            argument_model = tutorial_tool.fn_metadata.arg_model
            argument_model.model_config = {
                **argument_model.model_config,
                "extra": "forbid",
            }
            argument_model.model_rebuild(force=True)
            tutorial_tool.parameters = argument_model.model_json_schema(
                by_alias=True
            )

    @server.tool(
        name="list_family_evidence",
        description="Route a keyset-paginated physical-evidence listing to one family-specific provider.",
        annotations=READONLY_ANNOTATIONS,
    )
    def list_family_evidence_tool(
        family: Annotated[str, Field(min_length=1, strict=True)],
        ingest_run_id: Annotated[str, Field(min_length=1, strict=True)],
        family_contract_version: Annotated[str, Field(min_length=1, strict=True)],
        filters: Annotated[dict[str, Any] | None, Field(default=None)] = None,
        cursor: Annotated[str | None, Field(default=None)] = None,
        limit: Annotated[int, Field(default=50, ge=1, le=100, strict=True)] = 50,
    ) -> dict[str, Any]:
        result = family_router.list_evidence(
            family=family, ingest_run_id=ingest_run_id,
            family_contract_version=family_contract_version,
            filters=filters, cursor=cursor, limit=limit,
        )
        return _envelope(db, status=result.status, data=result.envelope())

    @server.tool(
        name="get_family_evidence",
        description="Route an exact physical occurrence lookup to one family-specific provider.",
        annotations=READONLY_ANNOTATIONS,
    )
    def get_family_evidence_tool(
        family: Annotated[str, Field(min_length=1, strict=True)],
        ingest_run_id: Annotated[str, Field(min_length=1, strict=True)],
        family_contract_version: Annotated[str, Field(min_length=1, strict=True)],
        occurrence_id: Annotated[str, Field(min_length=1, strict=True)],
    ) -> dict[str, Any]:
        result = family_router.get_evidence(
            family=family, ingest_run_id=ingest_run_id,
            family_contract_version=family_contract_version,
            occurrence_id=occurrence_id,
        )
        return _envelope(db, status=result.status, data=result.envelope())

    @server.tool(
        name="search_family_text",
        description="Route literal physical text-evidence search to one family-specific provider.",
        annotations=READONLY_ANNOTATIONS,
    )
    def search_family_text_tool(
        family: Annotated[str, Field(min_length=1, strict=True)],
        ingest_run_id: Annotated[str, Field(min_length=1, strict=True)],
        family_contract_version: Annotated[str, Field(min_length=1, strict=True)],
        query: Annotated[str, Field(min_length=1, strict=True)],
        filters: Annotated[dict[str, Any] | None, Field(default=None)] = None,
        cursor: Annotated[str | None, Field(default=None)] = None,
        limit: Annotated[int, Field(default=50, ge=1, le=100, strict=True)] = 50,
    ) -> dict[str, Any]:
        result = family_router.search_text(
            family=family, ingest_run_id=ingest_run_id,
            family_contract_version=family_contract_version, query=query,
            filters=filters, cursor=cursor, limit=limit,
        )
        return _envelope(db, status=result.status, data=result.envelope())

    for routed_tool_name in FAMILY_EVIDENCE_TOOLS:
        routed_tool = server._tool_manager._tools[routed_tool_name]
        argument_model = routed_tool.fn_metadata.arg_model
        argument_model.model_config = {
            **argument_model.model_config,
            "extra": "forbid",
        }
        argument_model.model_rebuild(force=True)
        routed_tool.parameters = argument_model.model_json_schema(by_alias=True)

    return server


def parse_db_path(args: list[str] | None = None) -> Path:
    """Resolve database path from CLI argument or GENSHIN_LORE_DB environment variable."""
    parser = argparse.ArgumentParser(
        description="Genshin Lore Database Readonly MCP Server",
        add_help=False,
    )
    parser.add_argument("--db", type=str, default=None, help="Path to database SQLite file")
    parsed, _ = parser.parse_known_args(args)

    db_str = parsed.db or os.environ.get("GENSHIN_LORE_DB")
    if not db_str:
        raise ValueError(
            "No database path provided. Specify --db <path> or set the GENSHIN_LORE_DB environment variable."
        )

    db_path = Path(db_str).resolve()
    if not db_path.is_file():
        raise FileNotFoundError(f"Database file not found: {db_path}")

    return db_path


def parse_world_archive_receipt_path(args: list[str] | None = None) -> Path | None:
    """Resolve the optional explicit World Archive release receipt path."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--world-archive-release-receipt", type=str, default=None)
    parsed, _ = parser.parse_known_args(args)
    if parsed.world_archive_release_receipt is not None:
        if parsed.world_archive_release_receipt == "":
            raise ValueError("World Archive release receipt CLI path must not be empty")
        return Path(parsed.world_archive_release_receipt).resolve()
    value = os.environ.get("GENSHIN_LORE_WORLD_ARCHIVE_RELEASE_RECEIPT")
    if value is None:
        return None
    if value == "":
        raise ValueError("World Archive release receipt environment path must not be empty")
    return Path(value).resolve()


def parse_quest_codex_receipt_path(args: list[str] | None = None) -> Path | None:
    """Resolve the optional explicit QuestCodex release receipt path."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--quest-codex-release-receipt", type=str, default=None)
    parsed, _ = parser.parse_known_args(args)
    if parsed.quest_codex_release_receipt is not None:
        if parsed.quest_codex_release_receipt == "":
            raise ValueError("QuestCodex release receipt CLI path must not be empty")
        return Path(parsed.quest_codex_release_receipt).resolve()
    value = os.environ.get("GENSHIN_LORE_QUEST_CODEX_RELEASE_RECEIPT")
    if value is None:
        return None
    if value == "":
        raise ValueError("QuestCodex release receipt environment path must not be empty")
    return Path(value).resolve()


def parse_batch_wave_receipt_path(args: list[str] | None = None) -> Path | None:
    """Resolve the explicit Batch M4 wave receipt path."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--batch-wave-release-receipt", type=str, default=None)
    parsed, _ = parser.parse_known_args(args)
    if parsed.batch_wave_release_receipt is not None:
        if parsed.batch_wave_release_receipt == "":
            raise ValueError("Batch M4 release receipt CLI path must not be empty")
        return Path(parsed.batch_wave_release_receipt).resolve()
    value = os.environ.get("GENSHIN_LORE_BATCH_WAVE_RELEASE_RECEIPT")
    if value is None:
        return None
    if value == "":
        raise ValueError("Batch M4 release receipt environment path must not be empty")
    return Path(value).resolve()



def _parse_explicit_m5d_path(
    args: list[str] | None,
    options: tuple[str, ...],
    environment: tuple[str, ...],
    label: str,
) -> Path | None:
    """Resolve one M5D evidence path with CLI-over-environment precedence."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(*options, dest="m5d_path", type=str, default=None)
    parsed, _ = parser.parse_known_args(args)
    if parsed.m5d_path is not None:
        if parsed.m5d_path == "":
            raise ValueError(f"{label} CLI path must not be empty")
        return Path(parsed.m5d_path).resolve()
    values = [(name, os.environ.get(name)) for name in environment]
    supplied = [(name, value) for name, value in values if value is not None]
    if not supplied:
        return None
    # Multiple environment aliases are accepted only when they resolve to the
    # same explicit path; conflicting aliases are a configuration mismatch.
    if any(value == "" for _, value in supplied):
        raise ValueError(f"{label} environment path must not be empty")
    normalized = {Path(value).resolve() for _, value in supplied if value is not None}
    if len(normalized) != 1:
        raise ValueError(f"{label} environment paths disagree")
    return next(iter(normalized))


def parse_batch_m5d_report_path(args: list[str] | None = None) -> Path | None:
    return _parse_explicit_m5d_path(
        args, ("--batch-m5d-report", "--batch-m5d-runtime-report"),
        ("GENSHIN_LORE_BATCH_M5D_REPORT", "GENSHIN_LORE_BATCH_M5D_RUNTIME_REPORT"),
        "Batch M5D report",
    )


def parse_batch_m5d_runtime_manifest_path(args: list[str] | None = None) -> Path | None:
    return _parse_explicit_m5d_path(
        args, ("--batch-m5d-runtime-manifest", "--batch-m5d-manifest"),
        ("GENSHIN_LORE_BATCH_M5D_RUNTIME_MANIFEST", "GENSHIN_LORE_BATCH_M5D_MANIFEST"),
        "Batch M5D runtime manifest",
    )


def parse_batch_m5d_independent_report_path(args: list[str] | None = None) -> Path | None:
    return _parse_explicit_m5d_path(
        args, ("--batch-m5d-independent-verification", "--batch-m5d-independent-report"),
        ("GENSHIN_LORE_BATCH_M5D_INDEPENDENT_VERIFICATION", "GENSHIN_LORE_BATCH_M5D_INDEPENDENT_REPORT"),
        "Batch M5D independent verification",
    )


# Short aliases make the explicit configuration names discoverable without
# changing the underlying no-default/no-sibling semantics.
def parse_batch_m5d_manifest_path(args: list[str] | None = None) -> Path | None:
    return parse_batch_m5d_runtime_manifest_path(args)


def parse_batch_m5d_verification_path(args: list[str] | None = None) -> Path | None:
    return parse_batch_m5d_independent_report_path(args)


def parse_batch_m5d_runtime_paths(
    args: list[str] | None = None,
) -> tuple[Path | None, Path | None, Path | None]:
    return (
        parse_batch_m5d_report_path(args),
        parse_batch_m5d_runtime_manifest_path(args),
        parse_batch_m5d_independent_report_path(args),
    )

def _parse_explicit_m5e_path(
    args: list[str] | None,
    option: str,
    environment: str,
    label: str,
) -> Path | None:
    """Resolve one M5E path with CLI-over-environment precedence."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(option, dest="m5e_path", type=str, default=None)
    parsed, _ = parser.parse_known_args(args)
    if parsed.m5e_path is not None:
        if parsed.m5e_path == "":
            raise ValueError(f"{label} CLI path must not be empty")
        return Path(parsed.m5e_path).resolve()
    value = os.environ.get(environment)
    if value is None:
        return None
    if value == "":
        raise ValueError(f"{label} environment path must not be empty")
    return Path(value).resolve()


def parse_batch_m5e_release_receipt_path(args: list[str] | None = None) -> Path | None:
    return _parse_explicit_m5e_path(
        args,
        "--batch-m5e-release-receipt",
        "GENSHIN_LORE_BATCH_M5E_RELEASE_RECEIPT",
        "Batch M5E release receipt",
    )


def parse_batch_m5e_runtime_manifest_path(args: list[str] | None = None) -> Path | None:
    return _parse_explicit_m5e_path(
        args,
        "--batch-m5e-runtime-manifest",
        "GENSHIN_LORE_BATCH_M5E_RUNTIME_MANIFEST",
        "Batch M5E runtime manifest",
    )


def parse_batch_m5e_runtime_paths(
    args: list[str] | None = None,
) -> tuple[Path | None, Path | None]:
    return (
        parse_batch_m5e_release_receipt_path(args),
        parse_batch_m5e_runtime_manifest_path(args),
    )


def parse_batch_r2_runtime_paths(args: list[str] | None = None) -> tuple[Path | None, Path | None]:
    return (
        _parse_explicit_m5e_path(args, "--batch-r2-release-receipt",
                                 "GENSHIN_LORE_BATCH_R2_RELEASE_RECEIPT", "Batch R2 release receipt"),
        _parse_explicit_m5e_path(args, "--batch-r2-runtime-manifest",
                                 "GENSHIN_LORE_BATCH_R2_RUNTIME_MANIFEST", "Batch R2 runtime manifest"),
    )


def parse_batch_r4_runtime_paths(args: list[str] | None = None) -> tuple[Path | None, Path | None]:
    return (
        _parse_explicit_m5e_path(args, "--batch-r4-release-receipt",
            "GENSHIN_LORE_BATCH_R4_RELEASE_RECEIPT", "Batch R4 release receipt"),
        _parse_explicit_m5e_path(args, "--batch-r4-runtime-manifest",
            "GENSHIN_LORE_BATCH_R4_RUNTIME_MANIFEST", "Batch R4 runtime manifest"),
    )


def parse_q2_uplift_paths(args: list[str] | None = None) -> tuple[Path | None, Path | None]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--q2-uplift-artifact", type=str, default=None)
    parser.add_argument("--q2-uplift-manifest", type=str, default=None)
    parsed, _ = parser.parse_known_args(args)
    artifact = parsed.q2_uplift_artifact if parsed.q2_uplift_artifact is not None else os.environ.get("GENSHIN_LORE_Q2_UPLIFT_ARTIFACT")
    manifest = parsed.q2_uplift_manifest if parsed.q2_uplift_manifest is not None else os.environ.get("GENSHIN_LORE_Q2_UPLIFT_MANIFEST")
    if artifact == "" or manifest == "": raise ValueError("Q2 uplift paths must not be empty")
    if (artifact is None) != (manifest is None): raise ValueError("Q2 uplift artifact and tracked manifest must be supplied together")
    return (Path(artifact).resolve(), Path(manifest).resolve()) if artifact is not None and manifest is not None else (None, None)


def parse_r3_uplift_paths(args: list[str] | None = None) -> tuple[Path | None, Path | None]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--r3-uplift-artifact", type=str, default=None)
    parser.add_argument("--r3-uplift-manifest", type=str, default=None)
    parsed, _ = parser.parse_known_args(args)
    artifact = parsed.r3_uplift_artifact if parsed.r3_uplift_artifact is not None else os.environ.get("GENSHIN_LORE_R3_UPLIFT_ARTIFACT")
    manifest = parsed.r3_uplift_manifest if parsed.r3_uplift_manifest is not None else os.environ.get("GENSHIN_LORE_R3_UPLIFT_MANIFEST")
    if artifact == "" or manifest == "":
        raise ValueError("R3 uplift paths must not be empty")
    if (artifact is None) != (manifest is None):
        raise ValueError("R3 uplift artifact and tracked manifest must be supplied together")
    return (Path(artifact).resolve(), Path(manifest).resolve()) if artifact is not None and manifest is not None else (None, None)


def parse_r0_runtime_paths(args: list[str] | None = None) -> tuple[Path | None, Path | None]:
    """Resolve the optional explicit R0 artifact/source bundle."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--r0-physical-artifact", type=str, default=None)
    parser.add_argument("--r0-source-root", type=str, default=None)
    parsed, _ = parser.parse_known_args(args)
    artifact_value = parsed.r0_physical_artifact
    source_value = parsed.r0_source_root
    if artifact_value is None:
        artifact_value = os.environ.get("GENSHIN_LORE_R0_PHYSICAL_ARTIFACT")
    if source_value is None:
        source_value = os.environ.get("GENSHIN_LORE_R0_SOURCE_ROOT")
    if artifact_value == "" or source_value == "":
        raise ValueError("R0 artifact/source paths must not be empty")
    if (artifact_value is None) != (source_value is None):
        raise ValueError("R0 physical artifact and source root must be supplied together")
    return (
        Path(artifact_value).resolve() if artifact_value is not None else None,
        Path(source_value).resolve() if source_value is not None else None,
    )


def parse_r5_quest_talk_paths(args: list[str] | None = None) -> tuple[Path | None, Path | None]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--r5-quest-talk-artifact", type=str, default=None)
    parser.add_argument("--r5-quest-talk-runtime-manifest", type=str, default=None)
    parsed, _ = parser.parse_known_args(args)
    artifact = parsed.r5_quest_talk_artifact
    manifest = parsed.r5_quest_talk_runtime_manifest
    if artifact is None: artifact = os.environ.get("GENSHIN_LORE_R5_QUEST_TALK_ARTIFACT")
    if manifest is None: manifest = os.environ.get("GENSHIN_LORE_R5_QUEST_TALK_RUNTIME_MANIFEST")
    if artifact == "" or manifest == "":
        raise ValueError("R5 Quest/Talk paths must not be empty")
    if (artifact is None) != (manifest is None):
        raise ValueError("R5 Quest/Talk artifact and runtime manifest must be supplied together")
    return (
        Path(artifact).resolve() if artifact is not None else None,
        Path(manifest).resolve() if manifest is not None else None,
    )


def parse_r5_quest_talk_bundle_paths(
    args: list[str] | None = None,
) -> tuple[tuple[Path, Path], ...]:
    """Parse repeated explicit ``manifest=artifact`` Quest/Talk bundle flags.

    The tracked catalog is consulted later for manifest approval.  This parser
    only accepts paths supplied by the caller and never scans a directory or
    chooses a sibling/latest artifact.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--r5-quest-talk-bundle", "--quest-talk-bundle",
        dest="bundles", action="append", default=[],
    )
    parsed, _ = parser.parse_known_args(args)
    result: list[tuple[Path, Path]] = []
    for raw in parsed.bundles:
        if not isinstance(raw, str) or raw.count("=") != 1:
            raise ValueError("Quest/Talk bundle must use manifest=artifact")
        manifest_value, artifact_value = (part.strip() for part in raw.split("=", 1))
        if not manifest_value or not artifact_value:
            raise ValueError("Quest/Talk bundle manifest and artifact paths must not be empty")
        result.append((Path(artifact_value).resolve(), Path(manifest_value).resolve()))
    if len({manifest for _, manifest in result}) != len(result):
        raise ValueError("Quest/Talk bundle manifests must be unique")
    return tuple(result)


def main(args: list[str] | None = None) -> None:
    """Run the stdio MCP server."""
    try:
        server_args = args if args is not None else sys.argv[1:]
        loading_parser = argparse.ArgumentParser(add_help=False)
        loading_parser.add_argument("--loading-tips-artifact")
        loading_parser.add_argument("--loading-tips-manifest")
        loading_parser.add_argument("--gcg-card-artifact")
        loading_parser.add_argument("--gcg-card-manifest")
        loading_parser.add_argument("--document-artifact")
        loading_parser.add_argument("--document-manifest")
        loading_parser.add_argument("--npc-dialogue-artifact")
        loading_parser.add_argument("--npc-dialogue-manifest")
        loading_parser.add_argument("--character-lore-artifact")
        loading_parser.add_argument("--character-lore-manifest")
        loading_parser.add_argument("--talk-physical-artifact")
        loading_parser.add_argument("--talk-physical-manifest")
        loading_parser.add_argument("--quest-narrative-artifact")
        loading_parser.add_argument("--quest-narrative-manifest")
        loading_parser.add_argument("--quest-review-artifact")
        loading_parser.add_argument("--quest-review-manifest")
        loading_parser.add_argument("--codexquest-artifact")
        loading_parser.add_argument("--codexquest-manifest")
        loading_parser.add_argument("--mail-archive-artifact")
        loading_parser.add_argument("--mail-archive-manifest")
        loading_parser.add_argument("--event-narrative-artifact")
        loading_parser.add_argument("--event-narrative-manifest")
        loading_parser.add_argument("--event-narrative-successor-artifact")
        loading_parser.add_argument("--event-narrative-successor-manifest")
        loading_parser.add_argument("--event-narrative-filmfest-artifact")
        loading_parser.add_argument("--event-narrative-filmfest-manifest")
        loading_parser.add_argument("--event-narrative-wave-artifact")
        loading_parser.add_argument("--event-narrative-wave-manifest")
        loading_parser.add_argument("--event-narrative-tps-artifact")
        loading_parser.add_argument("--event-narrative-tps-manifest")
        loading_parser.add_argument("--event-narrative-photo-echo-artifact")
        loading_parser.add_argument("--event-narrative-photo-echo-manifest")
        loading_parser.add_argument("--event-narrative-photo-echo-delta-artifact")
        loading_parser.add_argument("--event-narrative-photo-echo-delta-manifest")
        loading_parser.add_argument("--event-narrative-native-text-artifact")
        loading_parser.add_argument("--event-narrative-native-text-manifest")
        loading_parser.add_argument("--event-narrative-dialog-reminder-artifact")
        loading_parser.add_argument("--event-narrative-dialog-reminder-manifest")
        loading_parser.add_argument("--readable-archive-artifact")
        loading_parser.add_argument("--readable-archive-manifest")
        loading_parser.add_argument("--subtitle-archive-artifact")
        loading_parser.add_argument("--subtitle-archive-manifest")
        loading_parser.add_argument("--native-archive-artifact")
        loading_parser.add_argument("--native-archive-manifest")
        loading_parser.add_argument("--native-message-archive-artifact")
        loading_parser.add_argument("--native-message-archive-manifest")
        loading_parser.add_argument("--native-book-music-archive-artifact")
        loading_parser.add_argument("--native-book-music-archive-manifest")
        loading_parser.add_argument("--native-text-bulk-archive-artifact")
        loading_parser.add_argument("--native-text-bulk-archive-manifest")
        loading_parser.add_argument("--aux-quest-delta-archive-artifact")
        loading_parser.add_argument("--aux-quest-delta-archive-manifest")
        loading_parser.add_argument("--high-value-native-text-artifact")
        loading_parser.add_argument("--high-value-native-text-manifest")
        loading_args, _ = loading_parser.parse_known_args(server_args)
        db_path = parse_db_path(server_args)
        world_archive_receipt_path = parse_world_archive_receipt_path(server_args)
        quest_codex_receipt_path = parse_quest_codex_receipt_path(server_args)
        batch_wave_receipt_path = parse_batch_wave_receipt_path(server_args)
        batch_m5d_report_path, batch_m5d_runtime_manifest_path, batch_m5d_independent_report_path = parse_batch_m5d_runtime_paths(server_args)
        batch_m5e_release_receipt_path, batch_m5e_runtime_manifest_path = parse_batch_m5e_runtime_paths(server_args)
        batch_r2_release_receipt_path, batch_r2_runtime_manifest_path = parse_batch_r2_runtime_paths(server_args)
        batch_r4_release_receipt_path, batch_r4_runtime_manifest_path = parse_batch_r4_runtime_paths(server_args)
        r0_physical_artifact_path, r0_source_root = parse_r0_runtime_paths(server_args)
        q2_uplift_artifact_path, q2_uplift_manifest_path = parse_q2_uplift_paths(server_args)
        r3_uplift_artifact_path, r3_uplift_manifest_path = parse_r3_uplift_paths(server_args)
        r5_quest_talk_bundle_paths = parse_r5_quest_talk_bundle_paths(server_args)
        if r5_quest_talk_bundle_paths:
            legacy_artifact, legacy_manifest = parse_r5_quest_talk_paths(server_args)
            if legacy_artifact is not None or legacy_manifest is not None:
                raise ValueError("Quest/Talk single bundle and multi-run bundles are mutually exclusive")
            r5_quest_talk_artifact_path = None
            r5_quest_talk_runtime_manifest_path = None
        else:
            r5_quest_talk_artifact_path, r5_quest_talk_runtime_manifest_path = parse_r5_quest_talk_paths(server_args)
        db = open_readonly_database(db_path)
    except Exception as exc:
        sys.stderr.write(f"Error initializing readonly MCP server: {exc}\n")
        sys.stderr.flush()
        sys.exit(1)

    server = create_mcp_server(
        db, world_archive_receipt_path, quest_codex_receipt_path, batch_wave_receipt_path,
        batch_m5d_report_path, batch_m5d_runtime_manifest_path,
        batch_m5d_independent_report_path,
        batch_m5e_release_receipt_path, batch_m5e_runtime_manifest_path,
        batch_r2_release_receipt_path, batch_r2_runtime_manifest_path,
        r0_physical_artifact_path, r0_source_root, q2_uplift_artifact_path,
        q2_uplift_manifest_path, r3_uplift_artifact_path, r3_uplift_manifest_path,
        batch_r4_release_receipt_path, batch_r4_runtime_manifest_path,
        r5_quest_talk_artifact_path, r5_quest_talk_runtime_manifest_path,
        r5_quest_talk_bundle_paths or None,
        loading_args.loading_tips_artifact, loading_args.loading_tips_manifest,
        loading_args.gcg_card_artifact, loading_args.gcg_card_manifest,
        loading_args.document_artifact, loading_args.document_manifest,
        loading_args.npc_dialogue_artifact, loading_args.npc_dialogue_manifest,
        loading_args.character_lore_artifact, loading_args.character_lore_manifest,
        loading_args.talk_physical_artifact, loading_args.talk_physical_manifest,
        loading_args.quest_narrative_artifact, loading_args.quest_narrative_manifest,
        loading_args.quest_review_artifact, loading_args.quest_review_manifest,
        loading_args.codexquest_artifact, loading_args.codexquest_manifest,
        loading_args.mail_archive_artifact, loading_args.mail_archive_manifest,
        loading_args.event_narrative_artifact, loading_args.event_narrative_manifest,
        loading_args.event_narrative_successor_artifact, loading_args.event_narrative_successor_manifest,
        loading_args.event_narrative_filmfest_artifact, loading_args.event_narrative_filmfest_manifest,
        loading_args.event_narrative_wave_artifact, loading_args.event_narrative_wave_manifest,
        loading_args.event_narrative_tps_artifact, loading_args.event_narrative_tps_manifest,
        loading_args.event_narrative_photo_echo_artifact, loading_args.event_narrative_photo_echo_manifest,
        loading_args.event_narrative_photo_echo_delta_artifact, loading_args.event_narrative_photo_echo_delta_manifest,
        loading_args.event_narrative_native_text_artifact, loading_args.event_narrative_native_text_manifest,
        loading_args.event_narrative_dialog_reminder_artifact, loading_args.event_narrative_dialog_reminder_manifest,
        loading_args.readable_archive_artifact, loading_args.readable_archive_manifest,
        loading_args.subtitle_archive_artifact, loading_args.subtitle_archive_manifest,
        loading_args.native_archive_artifact, loading_args.native_archive_manifest,
        loading_args.native_message_archive_artifact, loading_args.native_message_archive_manifest,
        loading_args.native_book_music_archive_artifact, loading_args.native_book_music_archive_manifest,
        loading_args.native_text_bulk_archive_artifact, loading_args.native_text_bulk_archive_manifest,
        loading_args.aux_quest_delta_archive_artifact, loading_args.aux_quest_delta_archive_manifest,
        loading_args.high_value_native_text_artifact, loading_args.high_value_native_text_manifest,
    )
    _avoid_broken_anyio_worker_threads(server)
    try:
        anyio.run(_run_pipe_stdio, server)
    finally:
        db.close()


async def _run_pipe_stdio(server: MCPServer) -> None:
    """Use asyncio pipe I/O while retaining MCP's fd claim/diversion rules."""
    stdin_buffer, restore_stdin = _claim_fd(0, sys.stdin, "rb", _open_stdin_diversion)
    stdout_buffer, restore_stdout = _claim_fd(1, sys.stdout, "wb", _open_stdout_diversion)
    try:
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        loop = asyncio.get_running_loop()
        await loop.connect_read_pipe(lambda: protocol, stdin_buffer)
        write_protocol = asyncio.streams.FlowControlMixin(loop=loop)
        write_transport, _ = await loop.connect_write_pipe(lambda: write_protocol, stdout_buffer)
        writer = asyncio.StreamWriter(write_transport, write_protocol, None, loop)
        incoming_send, incoming = anyio.create_memory_object_stream[SessionMessage | Exception](0)
        outgoing, outgoing_receive = anyio.create_memory_object_stream[SessionMessage](0)

        async def read_wire() -> None:
            async with incoming_send:
                while line := await reader.readline():
                    try:
                        message = mcp_types.jsonrpc_message_adapter.validate_json(line, by_name=False)
                        await incoming_send.send(SessionMessage(message))
                    except Exception as exc:
                        await incoming_send.send(exc)

        async def write_wire() -> None:
            async with outgoing_receive:
                async for message in outgoing_receive:
                    wire = message.message.model_dump_json(by_alias=True, exclude_unset=True)
                    writer.write((wire + "\n").encode("utf-8"))
                    await writer.drain()

        async with anyio.create_task_group() as group:
            group.start_soon(read_wire)
            group.start_soon(write_wire)
            await server._lowlevel_server.run(incoming,outgoing,server._lowlevel_server.create_initialization_options())
            group.cancel_scope.cancel()
        writer.close()
        try:
            await writer.wait_closed()
        except NotImplementedError:
            # asyncio's write-pipe protocol has no close waiter on Python 3.12.
            pass
    finally:
        if restore_stdout is not None:
            restore_stdout()
        if restore_stdin is not None:
            restore_stdin()


def _avoid_broken_anyio_worker_threads(server: MCPServer) -> None:
    """Keep sync MCP handlers responsive in the supported AnyIO environment."""
    for tool in server._tool_manager._tools.values():
        if tool.is_async:
            continue
        original = tool.fn

        @functools.wraps(original)
        async def call_inline(_original=original, **kwargs: Any) -> Any:
            return _original(**kwargs)

        tool.fn = call_inline
        tool.is_async = True


if __name__ == "__main__":
    main()
