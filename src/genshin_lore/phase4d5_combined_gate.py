"""Phase 4D5 combined Schema 11 append and release gate.

This module is intentionally an append gate, not another ingestion pipeline.
It accepts only the byte-sealed Phase 4C5C Schema 10 artifact, calls the
existing atomic Phase 4D2B builder once, and independently checks the final
AQ + Static + Character + Tutorial state.  The aggregate checks ledger
identity, source-union boundaries, per-family run ownership, and the exact
Schema 11 object signature without re-parsing any upstream source.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

from .character_contracts import (
    CHARACTER_RUN_CONTRACTS,
    CHARACTER_RUN_CONTRACT_BY_ID,
    row_matches_contract,
)
from .character_phase4c2_schema import verify_character_phase4c2_schema_signature
from .db import validate_exact_schema_structure
from .static_contracts import (
    SOURCE_BUILD,
    SOURCE_COMMIT,
    SOURCE_ID,
    SOURCE_RELEASE,
    SOURCE_TREE,
    SOURCE_UPSTREAM,
    STATIC_RUN_CONTRACTS,
    qualify_static_run,
)
from .tutorial_phase4d2a_schema import verify_tutorial_phase4d2a_schema_signature
from .tutorial_phase4d2b_contract import expected_identity
from .tutorial_phase4d2b_evidence import (
    TutorialPhase4D2BEvidenceError,
    ingest_tutorial_phase4d2b_evidence_bundle,
)
from .util import canonical_json, sha256_text


class Phase4D5CombinedGateError(RuntimeError):
    """The input or final combined artifact is not an exact M5 state."""


class Phase4D5CombinedPromotedError(Phase4D5CombinedGateError):
    """The atomic append promoted a DB but report creation failed."""

    promoted = True


# This is the accepted C5C artifact, not a generic Schema 10 allow-list.
BASE_ARTIFACT_SHA256 = (
    "8f5c7e8940292b96f8027b37e0d1ce1757cbacb203b0dc4e6eff5fc657368860"
)
BASE_ARTIFACT_SIZE_BYTES = 1_493_069_824

# The deterministic output seal produced by migration0011 on the accepted C5C copy.
FINAL_ARTIFACT_SHA256 = "6f6820b7a0464a0cff41d340874a508609a06313b5db9115182a8d239f350fb0"
FINAL_ARTIFACT_SIZE_BYTES = 1_630_650_368

AQ_RUN_ID = "ingest-run:dc24a16612b9bb80b355114436edf8b8e15bf6ff"
AQ_SNAPSHOT_ID = "anime-game-data:7.0:26df1dfbdf05"
CHARACTER_STATIC_SNAPSHOT_ID = "snap:48f434afe1049d73a502e3f2b24b67e575436300"
AQ_PARSER_BUILD_ID = "parser-build:f8641e0ae697744cbe985272e462f58cdce5572f"
AQ_PARSER_VERSION = "quest-corpus-evidence-v1"
AQ_MAPPING_VERSION = "quest-corpus-evidence-v1"
AQ_PARSER_FINGERPRINT = "quest-corpus-evidence-7000-7014"
AQ_CONFIG_CHECKSUM = (
    "d8037a99a8c98e7ca883ac07122dd031a6d1604bded3d34c56295dd5fafd016d"
)
AQ_CREATED_AT = "2026-08-28T18:34:53+00:00"
AQ_SCOPE_SHA256 = (
    "6d361a7adfd9eebed3a405bacc6dc23a45abf01a4656a07f433354516872ace1"
)
AQ_CONFIG_JSON = {
    "full_scan": False,
    "languages": ["CHS", "EN", "JP"],
    "projection": "quest-corpus-occurrence-v1",
    "textmap_hydration": "v1",
}
AQ_CONFIG_JSON_SHA256 = (
    "6a299db9309f710d46324b819aff7b6b28d3606558f74fa29a4b5e9457119fdf"
)

# These are the accepted D2B manifest facts.  They are intentionally frozen
# here so an installed aggregate verifier does not need the 3.18 MB repository
# manifest (or any other repository file) merely to import this module.  The
# append command still loads and validates the caller-provided manifest lazily
# before invoking the existing builder.
TUTORIAL_RUN_FAMILY = "tutorial_phase4d2b_core_full_boundary"
TUTORIAL_PARSER_FINGERPRINT = "tutorial-phase4d2b-full-boundary-projection"
TUTORIAL_MANIFEST_ID = "anime-game-data-7.0-phase4d2b-core-tutorial-full-boundary"
TUTORIAL_COVERAGE = "declared_core_tutorial_source_closure_complete"
TUTORIAL_VERSIONS = {
    "manifest_contract": "tutorial-phase4d2b-full-boundary-manifest-v1",
    "parser": "tutorial-phase4d2b-full-boundary-parser-v1",
    "projection": "tutorial-phase4d2b-full-boundary-projection-v1",
    "relationship_rule": "tutorial-detail-and-push-tips-full-boundary-v1",
    "textmap_resolution_rule": "owner-locale-main-medium-full-boundary-v1",
    "image_reference_rule": "image-name-indexed-full-boundary-v1",
    "identity_rule": "tutorial-family-raw-occurrence-v1",
}
TUTORIAL_COUNTS = {
    "selected_files": 10,
    "source_records": 16799,
    "rows": 6648,
    "primary_rows": 5866,
    "lookup_rows": 782,
    "raw_fields": 31251,
    "detail_relations": 4385,
    "push_tips_relations": 12,
    "relations": 4397,
    "text_owners": 3614,
    "text_resolutions": 10842,
    "text_candidates": 10151,
    "image_references": 3594,
}
TUTORIAL_SOURCE = {
    "source_id": "anime-game-data",
    "upstream": "https://github.com/DimbreathBot/AnimeGameData",
    "release_label": "7.0",
    "build": "CNRELWin7.0.0_R47482070_S47579390_D47579390",
    "commit": "26df1dfbdf05a82bbb1d97506859f3e1c40718d8",
    "tree": "d891ef1d95fdd6c27a1da8a32ea7a61b483647a6",
    "observed_at": "2026-08-19T00:00:00+08:00",
    "snapshot_id": "anime-game-data:7.0:26df1dfbdf05a82bbb1d97506859f3e1c40718d8",
}
TUTORIAL_SELECTED_PATHS = (
    "ExcelBinOutput/PushTipsCodexExcelConfigData.json",
    "ExcelBinOutput/TutorialCatalogExcelConfigData.json",
    "ExcelBinOutput/TutorialDetailExcelConfigData.json",
    "ExcelBinOutput/TutorialExcelConfigData.json",
    "TextMap/TextMapCHS.json",
    "TextMap/TextMapEN.json",
    "TextMap/TextMapJP.json",
    "TextMap/TextMap_MediumCHS.json",
    "TextMap/TextMap_MediumEN.json",
    "TextMap/TextMap_MediumJP.json",
)
TUTORIAL_MANIFEST_RAW_SHA256 = (
    "ba969cadb7d3613a4acb0b6833a119e9693bedb4e599fbe01fcc123eb6c7c297"
)
TUTORIAL_OCCURRENCE_SET_DIGESTS = {
    "detail_relations": "cc2c0f4bcf271548240cc1df1cc4a11a2edc614bfb474d75f3e7efc2bdb75dfe",
    "image_references": "dcf575c7f7a4de1631f5246a28a82f847c1b4831195a9a84f4a008ca9ac24978",
    "push_tips": "3c5c1bbc22f4cfc00b3a711f72cc8aabe8652b82299c3ddbd7c2fc81339c6247",
    "raw_fields": "0770bae8260c8dfd155f7b3004309b6375db3a4bee2988838426ba90a7bb11fd",
    "rows": "d1a2bc502e6646903b474718c570705cb508c12ebd5e8c77252c990fde833996",
    "source_records": "d14912723d93b43db2a486c45eb47e74a806fc0182368a2be0a76ec7b198b40f",
    "text_candidates": "37709c294d379941dc6bcebb270e9a717933b39bed65c4c883dee064e6c6ae10",
    "text_owners": "6de0734c8222c8e24e9da9eb7274fa7bea144a5ba8f0207beb3db2fccc7bfd85",
    "text_resolutions": "7f33a9d217b6a420ef6cb7def078c5dc0ce2e13c4d4430095f40905439f4b682",
}
EXPECTED_D2B_RUN_ID = "ir-tutorial-phase4d2b:02648d6db9a687fa4be5a0c55e6e1c1a37bc2a4a"
TUTORIAL_SNAPSHOT_ID = "anime-game-data:7.0:26df1dfbdf05a82bbb1d97506859f3e1c40718d8"
TUTORIAL_PARSER_BUILD_ID = "pb-tutorial-phase4d2b:a387926944954584c7507be081ac0bb70e6f11ba"
TUTORIAL_CONFIG_CHECKSUM = "382067f9dba9e6742703f52ff6d35bb3e1e478eb88c715209f349da4530f3a3f"
_D2B_EXPECTED_IDENTITY = {
    "snapshot_id": TUTORIAL_SNAPSHOT_ID,
    "parser_build_id": TUTORIAL_PARSER_BUILD_ID,
    "config_checksum": TUTORIAL_CONFIG_CHECKSUM,
    "ingest_run_id": EXPECTED_D2B_RUN_ID,
}

# Tests may set this private hook to exercise a deterministic replacement race
# between the fast precheck and the locked promotion checks.  Production keeps
# it unset, so it has no runtime behavior beyond the accepted lock/CAS path.
_PROMOTION_TEST_HOOK: Any = None

BASE_RUN_IDS = tuple(
    sorted(
        {
            AQ_RUN_ID,
            *(item.ingest_run_id for item in STATIC_RUN_CONTRACTS),
            *(item.ingest_run_id for item in CHARACTER_RUN_CONTRACTS),
        }
    )
)
EXPECTED_RUN_IDS = tuple(sorted((*BASE_RUN_IDS, EXPECTED_D2B_RUN_ID)))
EXPECTED_SNAPSHOT_IDS = tuple(
    sorted({AQ_SNAPSHOT_ID, CHARACTER_STATIC_SNAPSHOT_ID, TUTORIAL_SNAPSHOT_ID})
)
BASE_SNAPSHOT_IDS = tuple(sorted({AQ_SNAPSHOT_ID, CHARACTER_STATIC_SNAPSHOT_ID}))
EXPECTED_PARSER_BUILD_IDS = tuple(
    sorted(
        {
            AQ_PARSER_BUILD_ID,
            *(item.parser_build_id for item in STATIC_RUN_CONTRACTS),
            *(item.parser_build_id for item in CHARACTER_RUN_CONTRACTS),
            TUTORIAL_PARSER_BUILD_ID,
        }
    )
)

CHARACTER_TABLES = (
    "character_audio_variant_occurrence",
    "character_raw_slot_occurrence",
    "character_relation_occurrence",
    "character_row_occurrence",
    "character_text_candidate_occurrence",
    "character_text_resolution_occurrence",
    "character_voice_container_occurrence",
)
STATIC_TABLES = (
    "static_evidence_container",
    "static_segment_occurrence",
    "static_relationship_occurrence",
    "static_text_resolution_occurrence",
    "static_text_candidate_occurrence",
)
TUTORIAL_TABLES = (
    "tutorial_row_occurrence",
    "tutorial_raw_field_occurrence",
    "tutorial_detail_relation_occurrence",
    "tutorial_text_owner_occurrence",
    "tutorial_text_resolution_occurrence",
    "tutorial_text_candidate_occurrence",
    "tutorial_image_reference_occurrence",
    "tutorial_push_tips_candidate_occurrence",
)

# These maps are the accepted C5C table-local ownership seal.  They are
# deliberately independent of the C5C report, so a report cannot make a
# changed row set appear valid.
EXPECTED_BASE_TABLE_COUNTS: dict[str, dict[str, int]] = {
    "character_audio_variant_occurrence": {
        "ir-character-phase4c2:8a29b630963be8ec8d363b48516b2266824a9197": 7776,
        "ir-character-phase4c4b:7a150669a294e1794255e6c82bc0bcb0b716547b": 8800,
        "ir-character-phase4c5b:38849b74a42fdaf24ed8e1aff9bca5b070534d76": 9611,
    },
    "character_raw_slot_occurrence": {
        "ir-character-phase4c2:8a29b630963be8ec8d363b48516b2266824a9197": 624,
        "ir-character-phase4c4b:7a150669a294e1794255e6c82bc0bcb0b716547b": 5296,
        "ir-character-phase4c5b:38849b74a42fdaf24ed8e1aff9bca5b070534d76": 78179,
    },
    "character_relation_occurrence": {
        "ir-character-phase4c2:8a29b630963be8ec8d363b48516b2266824a9197": 422,
        "ir-character-phase4c4b:7a150669a294e1794255e6c82bc0bcb0b716547b": 3437,
        "ir-character-phase4c5b:38849b74a42fdaf24ed8e1aff9bca5b070534d76": 52702,
    },
    "character_row_occurrence": {
        "ir-character-phase4c2:8a29b630963be8ec8d363b48516b2266824a9197": 82,
        "ir-character-phase4c4b:7a150669a294e1794255e6c82bc0bcb0b716547b": 678,
        "ir-character-phase4c5b:38849b74a42fdaf24ed8e1aff9bca5b070534d76": 10277,
    },
    "character_text_candidate_occurrence": {
        "ir-character-phase4c2:8a29b630963be8ec8d363b48516b2266824a9197": 513,
        "ir-character-phase4c4b:7a150669a294e1794255e6c82bc0bcb0b716547b": 5356,
        "ir-character-phase4c5b:38849b74a42fdaf24ed8e1aff9bca5b070534d76": 65387,
    },
    "character_text_resolution_occurrence": {
        "ir-character-phase4c2:8a29b630963be8ec8d363b48516b2266824a9197": 807,
        "ir-character-phase4c4b:7a150669a294e1794255e6c82bc0bcb0b716547b": 6384,
        "ir-character-phase4c5b:38849b74a42fdaf24ed8e1aff9bca5b070534d76": 100674,
    },
    "character_voice_container_occurrence": {
        "ir-character-phase4c2:8a29b630963be8ec8d363b48516b2266824a9197": 72,
        "ir-character-phase4c4b:7a150669a294e1794255e6c82bc0bcb0b716547b": 314,
        "ir-character-phase4c5b:38849b74a42fdaf24ed8e1aff9bca5b070534d76": 375,
    },
    "passage_occurrence": {AQ_RUN_ID: 3327},
    "passage_occurrence_text": {AQ_RUN_ID: 9931},
    "occurrence_edge": {AQ_RUN_ID: 3312},
    "evidence_container": {AQ_RUN_ID: 149},
    "document_evidence": {AQ_RUN_ID: 149},
    "identity_group": {AQ_RUN_ID: 149},
    "identity_group_container": {AQ_RUN_ID: 149},
    "candidate_scope_proof": {AQ_RUN_ID: 149},
    "static_evidence_container": {
        "ir-static-book1:ee4191875f6ad93652093a81deeb3f085fe794b6": 5,
        "ir-static-phase4b2:2747f7a3f7ea66992013bdf2678336b2e040e1a3": 56,
        "ir-static-phase4b3:d4c3d8df68eec5b8b8409ecebd3f09cf25e9e0f1": 3893,
        "ir-static-phase4b5:e184c9876aa0ab8494ff3e37a43c17ea73eea868": 6072,
        "ir-static-phase4b6:43defcef9307317f60d3ca0903ea11b0bc1690c5": 11570,
    },
    "static_segment_occurrence": {
        "ir-static-book1:ee4191875f6ad93652093a81deeb3f085fe794b6": 57,
        "ir-static-phase4b2:2747f7a3f7ea66992013bdf2678336b2e040e1a3": 420,
        "ir-static-phase4b3:d4c3d8df68eec5b8b8409ecebd3f09cf25e9e0f1": 33135,
        "ir-static-phase4b5:e184c9876aa0ab8494ff3e37a43c17ea73eea868": 11475,
    },
    "static_relationship_occurrence": {
        "ir-static-book1:ee4191875f6ad93652093a81deeb3f085fe794b6": 6,
        "ir-static-phase4b2:2747f7a3f7ea66992013bdf2678336b2e040e1a3": 128,
        "ir-static-phase4b3:d4c3d8df68eec5b8b8409ecebd3f09cf25e9e0f1": 5584,
        "ir-static-phase4b5:e184c9876aa0ab8494ff3e37a43c17ea73eea868": 20322,
        "ir-static-phase4b6:43defcef9307317f60d3ca0903ea11b0bc1690c5": 70586,
    },
    "static_text_resolution_occurrence": {
        "ir-static-phase4b2:2747f7a3f7ea66992013bdf2678336b2e040e1a3": 144,
        "ir-static-phase4b3:d4c3d8df68eec5b8b8409ecebd3f09cf25e9e0f1": 5256,
        "ir-static-phase4b5:e184c9876aa0ab8494ff3e37a43c17ea73eea868": 27027,
        "ir-static-phase4b6:43defcef9307317f60d3ca0903ea11b0bc1690c5": 208260,
    },
    "static_text_candidate_occurrence": {
        "ir-static-phase4b2:2747f7a3f7ea66992013bdf2678336b2e040e1a3": 129,
        "ir-static-phase4b3:d4c3d8df68eec5b8b8409ecebd3f09cf25e9e0f1": 5123,
        "ir-static-phase4b5:e184c9876aa0ab8494ff3e37a43c17ea73eea868": 26186,
        "ir-static-phase4b6:43defcef9307317f60d3ca0903ea11b0bc1690c5": 88568,
    },
}

EXPECTED_TUTORIAL_TABLE_COUNTS = {
    "tutorial_row_occurrence": int(TUTORIAL_COUNTS["rows"]),
    "tutorial_raw_field_occurrence": int(TUTORIAL_COUNTS["raw_fields"]),
    "tutorial_detail_relation_occurrence": int(TUTORIAL_COUNTS["detail_relations"]),
    "tutorial_push_tips_candidate_occurrence": int(TUTORIAL_COUNTS["push_tips_relations"]),
    "tutorial_text_owner_occurrence": int(TUTORIAL_COUNTS["text_owners"]),
    "tutorial_text_resolution_occurrence": int(TUTORIAL_COUNTS["text_resolutions"]),
    "tutorial_text_candidate_occurrence": int(TUTORIAL_COUNTS["text_candidates"]),
    "tutorial_image_reference_occurrence": int(TUTORIAL_COUNTS["image_references"]),
}


def _fail(message: str) -> None:
    raise Phase4D5CombinedGateError(message)


def _load_and_assert_d2b_manifest(manifest_path: str | Path) -> Any:
    """Load the caller-selected manifest and compare its complete identity seal."""

    try:
        # Importing the loader is harmless, but reading the manifest is an
        # append-command operation only.  The aggregate verifier never calls
        # this helper and therefore works without repository manifests.
        from .tutorial_phase4d2b_manifest import load_tutorial_phase4d2b_manifest

        manifest = load_tutorial_phase4d2b_manifest(manifest_path)
        identity = expected_identity(manifest)
        if dict(identity) != _D2B_EXPECTED_IDENTITY:
            _fail(f"M5 D2B manifest identity mismatch: {identity!r}")
        if str(manifest.raw_sha256) != TUTORIAL_MANIFEST_RAW_SHA256:
            _fail("M5 D2B manifest raw SHA-256 mismatch")
        if str(manifest.manifest_id) != TUTORIAL_MANIFEST_ID:
            _fail("M5 D2B manifest ID mismatch")
        if dict(manifest.source) != TUTORIAL_SOURCE:
            _fail("M5 D2B manifest source pin mismatch")
        if dict(manifest.versions) != TUTORIAL_VERSIONS:
            _fail("M5 D2B manifest version contract mismatch")
        if tuple(item.relative_path for item in manifest.files) != TUTORIAL_SELECTED_PATHS:
            _fail("M5 D2B manifest selected-file closure mismatch")
        expected = manifest.expected
        if dict(expected.get("counts", {})) != TUTORIAL_COUNTS:
            _fail("M5 D2B manifest count contract mismatch")
        if str(expected.get("coverage")) != TUTORIAL_COVERAGE:
            _fail("M5 D2B manifest coverage contract mismatch")
        if dict(expected.get("occurrence_set_digests", {})) != TUTORIAL_OCCURRENCE_SET_DIGESTS:
            _fail("M5 D2B manifest occurrence-digest contract mismatch")
        return manifest
    except Phase4D5CombinedGateError:
        raise
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise Phase4D5CombinedGateError(f"M5 D2B manifest preflight failed: {exc}") from exc


def _regular_file(value: str | Path, stage: str) -> Path:
    # Resolve the absolute name only after lstat: resolving first would make a
    # symlink look like its target and defeat the non-symlink input contract.
    path = Path(os.path.abspath(os.fspath(value)))
    try:
        info = os.lstat(path)
    except OSError as exc:
        _fail(f"M5 database is not readable at {stage}: {path}: {exc}")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        _fail(f"M5 database must be a regular non-symlink file at {stage}: {path}")
    return path


def _assert_not_path_alias(candidate: Path, other: Path, label: str) -> None:
    """Reject lexical, resolved, or hard-link aliases to a protected path."""

    if candidate == other:
        _fail(f"M5 report path aliases the {label}: {candidate}")
    try:
        if candidate.resolve(strict=False) == other.resolve(strict=False):
            _fail(f"M5 report path aliases the {label}: {candidate}")
        if os.path.lexists(candidate) and os.path.lexists(other):
            try:
                if os.path.samefile(candidate, other):
                    _fail(f"M5 report path aliases the {label}: {candidate}")
            except OSError:
                # The resolved-path check above is the relevant check for a
                # path that is not yet present; a concurrent disappearance is
                # rechecked by _assert_report_destination before publication.
                pass
    except OSError as exc:
        _fail(f"M5 report path alias check failed for {candidate}: {exc}")


def _assert_report_destination(
    path: Path,
    database: Path,
    staging: Path | None = None,
) -> None:
    """Validate a report's lexical name before any path resolution or write."""

    info: os.stat_result | None = None
    try:
        if os.path.lexists(path):
            info = os.lstat(path)
    except OSError as exc:
        _fail(f"M5 report path cannot be inspected: {path}: {exc}")
    if info is not None:
        if stat.S_ISLNK(info.st_mode):
            _fail(f"M5 report path symlink is forbidden: {path}")
        if not stat.S_ISREG(info.st_mode):
            _fail(f"M5 report path must be a regular file or absent: {path}")
    _assert_not_path_alias(path, database, "database")
    if staging is not None:
        _assert_not_path_alias(path, staging, "staging database")
    if info is not None:
        _fail(f"M5 report path already exists: {path}")


def _sidecars(path: Path) -> tuple[Path, Path, Path]:
    return tuple(Path(f"{path}{suffix}") for suffix in ("-journal", "-wal", "-shm"))


def _assert_sidecars(path: Path, stage: str) -> None:
    present = [str(item) for item in _sidecars(path) if os.path.lexists(item)]
    if present:
        _fail(f"M5 SQLite sidecars present at {stage}: {present}")


def fingerprint(path_value: str | Path) -> dict[str, Any]:
    """Hash one stable database file and capture its filesystem identity."""

    path = _regular_file(path_value, "fingerprint")
    _assert_sidecars(path, "fingerprint")
    before = os.lstat(path)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
        after = os.lstat(path)
    except OSError as exc:
        _fail(f"M5 database fingerprint failed: {exc}")
    if (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
    ):
        _fail("M5 database changed while fingerprinting")
    return {
        "sha256": digest.hexdigest(),
        "size_bytes": int(after.st_size),
        "device": int(after.st_dev),
        "inode": int(after.st_ino),
        "mode": int(after.st_mode),
        "mtime_ns": int(after.st_mtime_ns),
    }


def _cleanup_private(path: Path) -> None:
    """Remove one private staging path and only its SQLite sidecars."""

    for item in (path, *_sidecars(path)):
        try:
            item.unlink()
        except FileNotFoundError:
            pass


def _sync_directory(directory: Path) -> None:
    """Durably publish a directory entry on POSIX filesystems."""

    descriptor = -1
    try:
        descriptor = os.open(os.fspath(directory), os.O_RDONLY)
        os.fsync(descriptor)
    except OSError as exc:
        _fail(f"M5 directory sync failed for {directory}: {exc}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _stat_key(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_mode),
        int(info.st_size),
        int(info.st_mtime_ns),
    )


def _expected_stat_key(expected: Mapping[str, Any]) -> tuple[int, int, int, int, int]:
    return (
        int(expected["device"]),
        int(expected["inode"]),
        int(expected["mode"]),
        int(expected["size_bytes"]),
        int(expected["mtime_ns"]),
    )


def _copy_to_private_staging(target: Path, expected: Mapping[str, Any]) -> Path:
    """Copy a sealed base into a same-directory, process-unique private file."""

    _assert_sidecars(target, "before private staging copy")
    try:
        source_before = os.lstat(target)
    except OSError as exc:
        _fail(f"M5 source lstat failed before private staging copy: {exc}")
    if _stat_key(source_before) != _expected_stat_key(expected):
        _fail("M5 C5C source changed before private staging copy")

    descriptor = -1
    staging: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{target.name}.phase4d5-",
            suffix=".staging",
            dir=target.parent,
        )
        staging = Path(name)
        os.fchmod(descriptor, stat.S_IMODE(int(expected["mode"])))
        with os.fdopen(descriptor, "wb") as destination:
            descriptor = -1
            with target.open("rb") as source:
                shutil.copyfileobj(source, destination, length=8 * 1024 * 1024)
            destination.flush()
            os.fsync(destination.fileno())

        staging_info = os.lstat(staging)
        if stat.S_ISLNK(staging_info.st_mode) or not stat.S_ISREG(staging_info.st_mode):
            _fail("M5 private staging path is not a regular non-symlink file")
        if int(staging_info.st_size) != int(expected["size_bytes"]):
            _fail("M5 private staging copy size mismatch")
        source_after = os.lstat(target)
        if _stat_key(source_after) != _expected_stat_key(expected):
            _fail("M5 C5C source changed during private staging copy")
        _assert_sidecars(target, "after private staging copy")
        _assert_sidecars(staging, "private staging copy")
        _sync_directory(target.parent)
        return staging
    except BaseException:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if staging is not None:
            try:
                _cleanup_private(staging)
            except OSError:
                pass
        raise


def _assert_target_unchanged(target: Path, expected: Mapping[str, Any]) -> None:
    _assert_sidecars(target, "before private staging promotion")
    actual = fingerprint(target)
    if actual != dict(expected):
        _fail(
            "M5 C5C compare-and-swap check failed before promotion: "
            f"expected={dict(expected)!r} actual={actual!r}"
        )


def _assert_staging_final(staging: Path, expected: Mapping[str, Any]) -> None:
    """Require the staged DB to remain a regular file at its aggregate seal."""

    try:
        info = os.lstat(staging)
    except OSError as exc:
        _fail(f"M5 private staging path cannot be inspected before promotion: {exc}")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        _fail("M5 private staging path is not a regular non-symlink file before promotion")
    _assert_sidecars(staging, "before database promotion")
    actual = fingerprint(staging)
    if actual != dict(expected):
        _fail(
            "M5 private staging fingerprint changed before promotion: "
            f"expected={dict(expected)!r} actual={actual!r}"
        )


def _replace_database_atomically(
    target: Path,
    staging: Path,
    expected_target: Mapping[str, Any],
    expected_staging: Mapping[str, Any],
) -> None:
    """Promote a verified private DB under the accepted SQLite lock pattern."""

    _assert_sidecars(target, "before database promotion")
    _assert_target_unchanged(target, expected_target)
    hook = _PROMOTION_TEST_HOOK
    if hook is not None:
        hook(target, staging)

    lock: sqlite3.Connection | None = None
    locked = False
    promoted = False
    pending: BaseException | None = None
    try:
        try:
            # This mirrors the accepted D2B _finalize pattern.  The path is
            # checked before opening and again under BEGIN EXCLUSIVE because a
            # concurrent replacer can change the directory entry independently
            # of SQLite's inode lock.
            lock = sqlite3.connect(
                target.as_uri() + "?mode=rw",
                uri=True,
                timeout=0,
                isolation_level=None,
            )
            lock.execute("PRAGMA busy_timeout=0")
            _assert_delete_journal(lock)
            lock.execute("BEGIN EXCLUSIVE")
            locked = True
            _regular_file(target, "under database promotion lock")
            _assert_sidecars(target, "under database promotion lock")
            _assert_target_unchanged(target, expected_target)
            _assert_staging_final(staging, expected_staging)
            os.replace(staging, target)
            promoted = True
        except Phase4D5CombinedGateError as exc:
            if promoted:
                pending = Phase4D5CombinedPromotedError(
                    f"M5 database promoted but promotion validation failed: {exc}"
                )
            else:
                pending = exc
        except (sqlite3.Error, OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
            if promoted:
                pending = Phase4D5CombinedPromotedError(
                    f"M5 database promoted but promotion finalization failed: {exc}"
                )
            else:
                pending = Phase4D5CombinedGateError(
                    f"M5 database promotion failed before rename: {exc}"
                )
        except BaseException as exc:
            # Preserve process-control exceptions, but keep the lock cleanup
            # in this finally path even for an unexpected failure.
            pending = exc
    finally:
        # Roll back and close no matter which locked operation failed.  Cleanup
        # failures are reported only when there is no original exception, so
        # they cannot mask the useful gate error or its traceback.
        cleanup_error: BaseException | None = None
        if locked and lock is not None:
            try:
                lock.execute("ROLLBACK")
            except BaseException as exc:
                cleanup_error = exc
        if lock is not None:
            try:
                lock.close()
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        if pending is None and cleanup_error is not None:
            if isinstance(cleanup_error, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                pending = cleanup_error
            elif promoted:
                pending = Phase4D5CombinedPromotedError(
                    f"M5 database promoted but lock cleanup failed: {cleanup_error}"
                )
            elif isinstance(cleanup_error, Phase4D5CombinedGateError):
                pending = cleanup_error
            else:
                pending = Phase4D5CombinedGateError(
                    f"M5 database promotion lock cleanup failed: {cleanup_error}"
                )

    if pending is not None:
        raise pending
    if not promoted:
        _fail("M5 database promotion ended without replacing the target")
    try:
        _sync_directory(target.parent)
        _regular_file(target, "after database promotion")
        _assert_sidecars(target, "after database promotion")
        final = fingerprint(target)
        if final != dict(expected_staging):
            _fail(
                "M5 promoted database fingerprint mismatch: "
                f"expected={dict(expected_staging)!r} actual={final!r}"
            )
    except Phase4D5CombinedGateError as exc:
        raise Phase4D5CombinedPromotedError(
            f"M5 database promoted but post-rename durability check failed: {exc}"
        ) from exc
    except (sqlite3.Error, OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        raise Phase4D5CombinedPromotedError(
            f"M5 database promoted but post-rename check failed: {exc}"
        ) from exc


def _serialize_report(report: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ) + "\n"
    except (TypeError, ValueError) as exc:
        raise Phase4D5CombinedGateError(f"M5 report serialization failed: {exc}") from exc


def _stage_report(
    path: Path,
    report: Mapping[str, Any],
    *,
    database: Path,
    database_staging: Path,
) -> Path:
    """Prepare a report privately; its public name appears after DB promotion."""

    _assert_report_destination(path, database, database_staging)
    payload = _serialize_report(report)
    descriptor = -1
    staging: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _assert_report_destination(path, database, database_staging)
        descriptor, name = tempfile.mkstemp(
            prefix=f".{path.name}.phase4d5-",
            suffix=".staging",
            dir=path.parent,
        )
        staging = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _sync_directory(path.parent)
        _assert_report_destination(path, database, database_staging)
        return staging
    except Phase4D5CombinedGateError:
        if staging is not None:
            try:
                _cleanup_private(staging)
            except OSError:
                pass
        raise
    except OSError as exc:
        if staging is not None:
            try:
                _cleanup_private(staging)
            except OSError:
                pass
        raise Phase4D5CombinedGateError(
            f"M5 report preparation failed before database promotion: {exc}"
        ) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _promote_report(staging: Path, target: Path) -> None:
    """Publish a prepared report without replacing a concurrent report."""

    try:
        if os.path.lexists(target):
            raise FileExistsError(target)
        os.link(staging, target, follow_symlinks=False)
        staging.unlink()
        _sync_directory(target.parent)
    except Phase4D5CombinedGateError as exc:
        try:
            _cleanup_private(staging)
        except OSError:
            pass
        raise Phase4D5CombinedPromotedError(
            f"M5 database promoted but report publication failed: {exc}"
        ) from exc
    except OSError as exc:
        try:
            _cleanup_private(staging)
        except OSError:
            pass
        raise Phase4D5CombinedPromotedError(
            f"M5 database promoted but report publication failed: {exc}"
        ) from exc


def _strict_json(value: Any, label: str) -> Any:
    def reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                _fail(f"duplicate JSON key in {label}: {key}")
            result[key] = item
        return result

    try:
        return json.loads(str(value), object_pairs_hook=reject_pairs)
    except (TypeError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"{label} is not strict JSON: {exc}")


def _schema_versions(conn: sqlite3.Connection) -> list[int]:
    try:
        return [
            int(row[0])
            for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")
        ]
    except sqlite3.Error as exc:
        _fail(f"M5 schema ledger is unreadable: {exc}")


def _table_names(conn: sqlite3.Connection, object_type: str = "table") -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type=? AND name NOT LIKE 'sqlite_%'",
            (object_type,),
        )
    }


def _assert_delete_journal(conn: sqlite3.Connection) -> None:
    try:
        mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).casefold()
    except sqlite3.Error as exc:
        _fail(f"M5 journal mode is unreadable: {exc}")
    if mode != "delete":
        _fail(f"M5 requires DELETE journal mode, got {mode}")


def _assert_schema11(conn: sqlite3.Connection) -> None:
    versions = _schema_versions(conn)
    if versions != list(range(1, 12)):
        _fail(f"M5 requires exact Schema 11 ledger 1..11, got {versions}")
    try:
        validate_exact_schema_structure(conn, 11)
        verify_character_phase4c2_schema_signature(conn)
        verify_tutorial_phase4d2a_schema_signature(conn)
    except (RuntimeError, sqlite3.Error, ValueError) as exc:
        _fail(f"M5 Schema 11 structural signature mismatch: {exc}")


def _assert_schema10_base(conn: sqlite3.Connection) -> None:
    versions = _schema_versions(conn)
    if versions != list(range(1, 11)):
        _fail(f"M5 C5C input requires exact Schema 10 ledger 1..10, got {versions}")
    try:
        validate_exact_schema_structure(conn, 10)
        verify_character_phase4c2_schema_signature(conn)
    except (RuntimeError, sqlite3.Error, ValueError) as exc:
        _fail(f"M5 C5C Schema 10 structural signature mismatch: {exc}")


def _run_rows(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT ir.id,ir.snapshot_id,ir.parser_build_id,ir.scope_json,ir.config_json,
               ir.status AS run_status,ir.created_at AS run_created_at,
               pb.parser_version,pb.mapping_version,pb.fingerprint,
               pb.config_checksum,pb.created_at AS parser_created_at,
               ss.source_id,ss.release_label,ss.build,
               ss."commit" AS snapshot_commit,ss.tree AS snapshot_tree,
               ss.observed_at,ss.status AS snapshot_status,
               ss.metadata_json AS snapshot_metadata_json,
               sc.kind AS source_kind,sc.url AS source_url,
               sc.license_note AS source_license_note
          FROM ingest_run ir
          JOIN parser_build pb ON pb.id=ir.parser_build_id
          JOIN source_snapshot ss ON ss.id=ir.snapshot_id
          JOIN source_catalog sc ON sc.id=ss.source_id
         ORDER BY ir.id
        """
    ).fetchall()
    return {str(row["id"]): row for row in rows}


def _assert_source_metadata(
    conn: sqlite3.Connection,
    *,
    source_files: int,
    source_records: int,
    expected_snapshot_ids: tuple[str, ...],
) -> dict[str, Any]:
    catalogs = [
        tuple(row)
        for row in conn.execute(
            "SELECT id,kind,url,license_note FROM source_catalog ORDER BY id"
        )
    ]
    if catalogs != [(SOURCE_ID, "primary", SOURCE_UPSTREAM, None)]:
        _fail(f"M5 source_catalog set mismatch: {catalogs!r}")

    snapshots = {
        str(row["id"]): row
        for row in conn.execute("SELECT * FROM source_snapshot ORDER BY id")
    }
    if set(snapshots) != set(expected_snapshot_ids):
        _fail(f"M5 source_snapshot set mismatch: {sorted(snapshots)}")
    for snapshot_id, row in snapshots.items():
        if (
            row["source_id"],
            row["release_label"],
            row["build"],
            row["commit"],
            row["tree"],
            row["observed_at"],
            row["status"],
        ) != (
            SOURCE_ID,
            SOURCE_RELEASE,
            SOURCE_BUILD,
            SOURCE_COMMIT,
            SOURCE_TREE,
            "2026-08-19T00:00:00+08:00",
            "active",
        ):
            _fail(f"M5 source_snapshot identity mismatch: {snapshot_id}")
        metadata = _strict_json(row["metadata_json"], f"source_snapshot {snapshot_id} metadata_json")
        if snapshot_id == AQ_SNAPSHOT_ID:
            expected_metadata = {
                "corpus_manifest_id": "anime-game-data-7.0-aq-7000-7014",
                "scope": "AQ 7000-7014 sealed quest evidence corpus",
            }
        elif snapshot_id == TUTORIAL_SNAPSHOT_ID:
            expected_metadata = {
                "phase4d2b_full_boundary": True,
                "sealed_source_manifest": True,
            }
        else:
            expected_metadata = {"sealed_source_manifest": True}
        if metadata != expected_metadata:
            _fail(f"M5 source_snapshot metadata mismatch: {snapshot_id}")

    actual_files = int(conn.execute("SELECT COUNT(*) FROM source_file").fetchone()[0])
    actual_records = int(conn.execute("SELECT COUNT(*) FROM source_record").fetchone()[0])
    if (actual_files, actual_records) != (source_files, source_records):
        _fail(
            "M5 source union count mismatch: "
            f"{actual_files}/{actual_records} != {source_files}/{source_records}"
        )
    return {
        "source_catalogs": len(catalogs),
        "source_snapshots": len(snapshots),
        "source_files": actual_files,
        "source_records": actual_records,
    }


def _expected_tutorial_scope() -> dict[str, Any]:
    return {
        "family": TUTORIAL_RUN_FAMILY,
        "manifest_id": TUTORIAL_MANIFEST_ID,
        "snapshot_id": TUTORIAL_SNAPSHOT_ID,
        "coverage": TUTORIAL_COVERAGE,
        "corpus_complete": True,
        "completeness_claim": TUTORIAL_COVERAGE,
        "push_tips_corpus_complete": False,
        "selected_paths": list(TUTORIAL_SELECTED_PATHS),
        "closure_counts": dict(TUTORIAL_COUNTS),
        "occurrence_set_digests": dict(TUTORIAL_OCCURRENCE_SET_DIGESTS),
        "full_core_tutorial_scan": True,
        "logical_identity_created": False,
        "mcp_capability_added": False,
    }


def _expected_tutorial_config() -> dict[str, Any]:
    return {
        "manifest_raw_sha256": TUTORIAL_MANIFEST_RAW_SHA256,
        "versions": dict(TUTORIAL_VERSIONS),
        "source_commit": str(TUTORIAL_SOURCE["commit"]),
        "source_tree": str(TUTORIAL_SOURCE["tree"]),
        "coverage": TUTORIAL_COVERAGE,
        "corpus_complete": True,
        "completeness_claim": TUTORIAL_COVERAGE,
        "push_tips_corpus_complete": False,
        "schema_contract": "schema11-migration0011-exact",
        "occurrence_set_digests": dict(TUTORIAL_OCCURRENCE_SET_DIGESTS),
        "logical_identity_created": False,
        "mcp_capability_added": False,
    }


def _assert_run_metadata(conn: sqlite3.Connection, *, include_tutorial: bool) -> dict[str, Any]:
    rows = _run_rows(conn)
    expected_ids = set(EXPECTED_RUN_IDS if include_tutorial else BASE_RUN_IDS)
    if set(rows) != expected_ids:
        _fail(f"M5 ingest_run set mismatch: {sorted(rows)}")
    parser_ids = {str(row["parser_build_id"]) for row in rows.values()}
    expected_parser_ids = set(
        EXPECTED_PARSER_BUILD_IDS if include_tutorial else (
            item for item in EXPECTED_PARSER_BUILD_IDS if item != TUTORIAL_PARSER_BUILD_ID
        )
    )
    if parser_ids != expected_parser_ids:
        _fail(f"M5 parser build union mismatch: {sorted(parser_ids)}")
    if any(row["run_status"] != "complete" for row in rows.values()):
        _fail("M5 requires every accepted ingest_run to be complete")

    static_by_id = {item.ingest_run_id: item for item in STATIC_RUN_CONTRACTS}
    for run_id, row in rows.items():
        scope = _strict_json(row["scope_json"], f"ingest_run {run_id} scope_json")
        config = _strict_json(row["config_json"], f"ingest_run {run_id} config_json")
        snapshot_metadata = _strict_json(
            row["snapshot_metadata_json"], f"ingest_run {run_id} snapshot metadata"
        )
        if str(row["scope_json"]) != canonical_json(scope):
            _fail(f"M5 run scope is not canonical JSON: {run_id}")
        if str(row["config_json"]) != canonical_json(config):
            _fail(f"M5 run config is not canonical JSON: {run_id}")
        if run_id == AQ_RUN_ID:
            if (
                row["snapshot_id"],
                row["parser_build_id"],
                row["parser_version"],
                row["mapping_version"],
                row["fingerprint"],
                row["config_checksum"],
                row["run_created_at"],
                row["parser_created_at"],
            ) != (
                AQ_SNAPSHOT_ID,
                AQ_PARSER_BUILD_ID,
                AQ_PARSER_VERSION,
                AQ_MAPPING_VERSION,
                AQ_PARSER_FINGERPRINT,
                AQ_CONFIG_CHECKSUM,
                AQ_CREATED_AT,
                AQ_CREATED_AT,
            ):
                _fail("M5 AQ run/parser binding mismatch")
            if not isinstance(scope, dict) or scope.get("family") != "quest_corpus_evidence":
                _fail("M5 AQ run family mismatch")
            if sha256_text(canonical_json(scope)) != AQ_SCOPE_SHA256:
                _fail("M5 AQ scope contract mismatch")
            if config != AQ_CONFIG_JSON or sha256_text(canonical_json(config)) != AQ_CONFIG_JSON_SHA256:
                _fail("M5 AQ config contract mismatch")
            continue
        if run_id in static_by_id:
            if not qualify_static_run(conn, static_by_id[run_id]):
                _fail(f"M5 Static run/parser/snapshot/source binding mismatch: {run_id}")
            continue
        if run_id in CHARACTER_RUN_CONTRACT_BY_ID:
            contract = CHARACTER_RUN_CONTRACT_BY_ID[run_id]
            if not row_matches_contract(
                row,
                scope,
                config,
                snapshot_metadata,
                contract,
                [run_id],
            ):
                _fail(f"M5 Character run/parser/snapshot/source binding mismatch: {run_id}")
            continue
        if run_id == EXPECTED_D2B_RUN_ID and include_tutorial:
            if scope != _expected_tutorial_scope():
                _fail("M5 Tutorial scope binding mismatch")
            if config != _expected_tutorial_config():
                _fail("M5 Tutorial config binding mismatch")
            if (
                row["snapshot_id"],
                row["parser_build_id"],
                row["parser_version"],
                row["mapping_version"],
                row["fingerprint"],
                row["config_checksum"],
                row["run_created_at"],
                row["parser_created_at"],
            ) != (
                TUTORIAL_SNAPSHOT_ID,
                TUTORIAL_PARSER_BUILD_ID,
                TUTORIAL_VERSIONS["parser"],
                TUTORIAL_VERSIONS["projection"],
                TUTORIAL_PARSER_FINGERPRINT,
                TUTORIAL_CONFIG_CHECKSUM,
                str(TUTORIAL_SOURCE["observed_at"]),
                str(TUTORIAL_SOURCE["observed_at"]),
            ):
                _fail("M5 Tutorial parser binding mismatch")
            if snapshot_metadata != {"phase4d2b_full_boundary": True, "sealed_source_manifest": True}:
                _fail("M5 Tutorial snapshot metadata mismatch")
            continue
        _fail(f"M5 unexpected run metadata: {run_id}")
    return {
        "ingest_runs": len(rows),
        "parser_builds": len(parser_ids),
        "run_ids": sorted(rows),
        "parser_build_ids": sorted(parser_ids),
    }


def _counts_by_run(conn: sqlite3.Connection, table: str) -> dict[str, int]:
    columns = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}
    if "ingest_run_id" not in columns:
        return {}
    return {
        str(row[0]): int(row[1])
        for row in conn.execute(
            f'SELECT ingest_run_id,COUNT(*) FROM "{table}" GROUP BY ingest_run_id ORDER BY ingest_run_id'
        )
    }


def _assert_family_ownership(conn: sqlite3.Connection, *, include_tutorial: bool) -> dict[str, Any]:
    for table in CHARACTER_TABLES + STATIC_TABLES:
        actual = _counts_by_run(conn, table)
        wanted = EXPECTED_BASE_TABLE_COUNTS[table]
        if actual != wanted:
            _fail(f"M5 existing-family ownership/count mismatch in {table}: {actual!r}")
    for table, wanted in EXPECTED_BASE_TABLE_COUNTS.items():
        if _counts_by_run(conn, table) != wanted:
            _fail(f"M5 base table ownership/count mismatch in {table}")
    tutorial_result: dict[str, dict[str, int]] = {}
    if include_tutorial:
        for table in TUTORIAL_TABLES:
            actual = _counts_by_run(conn, table)
            wanted = {EXPECTED_D2B_RUN_ID: EXPECTED_TUTORIAL_TABLE_COUNTS[table]}
            if actual != wanted:
                _fail(f"M5 Tutorial ownership/count mismatch in {table}: {actual!r}")
            tutorial_result[table] = actual
    return {
        "existing_tables": {table: _counts_by_run(conn, table) for table in CHARACTER_TABLES + STATIC_TABLES},
        "aq_tables": {
            table: _counts_by_run(conn, table)
            for table in EXPECTED_BASE_TABLE_COUNTS
            if table in {"passage_occurrence", "passage_occurrence_text", "occurrence_edge", "evidence_container", "document_evidence", "identity_group", "identity_group_container", "candidate_scope_proof"}
        },
        "tutorial_tables": tutorial_result,
    }


def _source_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [
        str(row[1])
        for row in conn.execute(f'PRAGMA table_info("{table}")')
        if "source_file_id" in str(row[1]) or "source_record_id" in str(row[1])
    ]


def _assert_source_boundary(conn: sqlite3.Connection, *, include_tutorial: bool) -> dict[str, Any]:
    primary_tutorial_paths = {
        "ExcelBinOutput/TutorialExcelConfigData.json",
        "ExcelBinOutput/TutorialDetailExcelConfigData.json",
        "ExcelBinOutput/TutorialCatalogExcelConfigData.json",
        "ExcelBinOutput/PushTipsCodexExcelConfigData.json",
    }
    shared_textmap_paths = {
        path for path in TUTORIAL_SELECTED_PATHS if path.startswith("TextMap/")
    }
    actual_primary = {
        str(row[0])
        for row in conn.execute(
            "SELECT relative_path FROM source_file "
            "WHERE relative_path LIKE 'ExcelBinOutput/Tutorial%' "
            "OR relative_path LIKE 'ExcelBinOutput/PushTips%'"
        )
    }
    expected_primary = primary_tutorial_paths if include_tutorial else set()
    if actual_primary != expected_primary:
        _fail(f"M5 Tutorial source-file set mismatch: {sorted(actual_primary)}")
    if include_tutorial:
        selected_present = {
            str(row[0])
            for row in conn.execute(
                "SELECT relative_path FROM source_file WHERE relative_path IN ("
                + ",".join("?" for _ in TUTORIAL_SELECTED_PATHS)
                + ")",
                TUTORIAL_SELECTED_PATHS,
            )
        }
        if selected_present != set(TUTORIAL_SELECTED_PATHS):
            _fail("M5 D2B selected source-file closure is incomplete")

    leaks: list[dict[str, Any]] = []
    for table in sorted(_table_names(conn)):
        if table in {"source_file", "source_record"}:
            continue
        for column in _source_columns(conn, table):
            if "source_file_id" in column:
                sql = (
                    f'SELECT DISTINCT sf.relative_path FROM "{table}" t '
                    f'JOIN source_file sf ON sf.id=t."{column}" '
                    "WHERE sf.relative_path LIKE 'ExcelBinOutput/Tutorial%' "
                    "OR sf.relative_path LIKE 'ExcelBinOutput/PushTips%'"
                )
            else:
                sql = (
                    f'SELECT DISTINCT sf.relative_path FROM "{table}" t '
                    f'JOIN source_record sr ON sr.id=t."{column}" '
                    "JOIN source_file sf ON sf.id=sr.source_file_id "
                    "WHERE sf.relative_path LIKE 'ExcelBinOutput/Tutorial%' "
                    "OR sf.relative_path LIKE 'ExcelBinOutput/PushTips%'"
                )
            paths = [str(row[0]) for row in conn.execute(sql)]
            if paths and not table.startswith("tutorial_"):
                leaks.append({"table": table, "column": column, "paths": sorted(paths)})
    if leaks:
        _fail(f"M5 cross-family Tutorial source ownership leak: {leaks[:3]}")

    if include_tutorial:
        allowed = primary_tutorial_paths | shared_textmap_paths
        unselected: list[dict[str, Any]] = []
        for table in TUTORIAL_TABLES:
            for column in _source_columns(conn, table):
                if "source_file_id" in column:
                    sql = (
                        f'SELECT DISTINCT sf.relative_path FROM "{table}" t '
                        f'JOIN source_file sf ON sf.id=t."{column}" '
                        "WHERE t.ingest_run_id=?"
                    )
                else:
                    sql = (
                        f'SELECT DISTINCT sf.relative_path FROM "{table}" t '
                        f'JOIN source_record sr ON sr.id=t."{column}" '
                        'JOIN source_file sf ON sf.id=sr.source_file_id WHERE t.ingest_run_id=?'
                    )
                for row in conn.execute(sql, (EXPECTED_D2B_RUN_ID,)):
                    if str(row[0]) not in allowed:
                        unselected.append({"table": table, "column": column, "path": str(row[0])})
        if unselected:
            _fail(f"M5 Tutorial source closure escaped selected files: {unselected[:3]}")
    return {
        "cross_family_leaks": [],
        "tutorial_primary_paths": sorted(actual_primary),
        "shared_textmap_paths": sorted(shared_textmap_paths),
    }


def _assert_no_logical_pollution(conn: sqlite3.Connection) -> None:
    # Schema validation seals the object set.  This explicit token guard is
    # for accidental future logical Tutorial objects that might otherwise be
    # hidden behind a valid-looking migration ledger.
    forbidden = ("logical_tutorial", "tutorial_identity", "logical_image")
    for catalog in ("sqlite_master", "sqlite_temp_master"):
        try:
            rows = conn.execute(
                f"SELECT type,name,sql FROM {catalog} "
                "WHERE type IN ('table','view','index','trigger')"
            )
        except sqlite3.Error:
            continue
        for object_type, name, sql in rows:
            lowered = f"{name} {sql or ''}".casefold()
            if any(token in lowered for token in forbidden):
                _fail(f"M5 logical/derived object pollution: {catalog} {object_type} {name}")


def _verify_connection(
    conn: sqlite3.Connection,
    *,
    include_tutorial: bool,
) -> dict[str, Any]:
    _assert_delete_journal(conn)
    if include_tutorial:
        _assert_schema11(conn)
        source_counts = (2772, 245032)
    else:
        _assert_schema10_base(conn)
        source_counts = (2762, 228233)
        if _table_names(conn) & set(TUTORIAL_TABLES):
            _fail("M5 Schema 10 input already contains Tutorial tables")
    _assert_no_logical_pollution(conn)
    source = _assert_source_metadata(
        conn,
        source_files=source_counts[0],
        source_records=source_counts[1],
        expected_snapshot_ids=(EXPECTED_SNAPSHOT_IDS if include_tutorial else BASE_SNAPSHOT_IDS),
    )
    runs = _assert_run_metadata(conn, include_tutorial=include_tutorial)
    ownership = _assert_family_ownership(conn, include_tutorial=include_tutorial)
    source_boundary = _assert_source_boundary(conn, include_tutorial=include_tutorial)
    fk_errors = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
    if fk_errors:
        _fail(f"M5 foreign-key check failed: {fk_errors[:3]}")
    return {
        "source_union": source,
        "run_union": runs,
        "ownership": ownership,
        "source_ownership": source_boundary,
        "foreign_key_check": [],
    }


def verify_phase4d5_combined_database(
    db_path: str | Path,
    *,
    expected_fingerprint: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read-only independent aggregate verification of the final artifact."""

    database = _regular_file(db_path, "verification")
    before = fingerprint(database)
    if before["sha256"] != FINAL_ARTIFACT_SHA256 or before["size_bytes"] != FINAL_ARTIFACT_SIZE_BYTES:
        _fail(
            "M5 final artifact seal mismatch: "
            f"sha256={before["sha256"]} size={before["size_bytes"]}"
        )
    try:
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA busy_timeout=0")
            detail = _verify_connection(conn, include_tutorial=True)
    except Phase4D5CombinedGateError:
        raise
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError) as exc:
        raise Phase4D5CombinedGateError(str(exc)) from exc
    _assert_sidecars(database, "after verification")
    after = fingerprint(database)
    if before != after:
        _fail("M5 database changed during read-only aggregate verification")
    if expected_fingerprint is not None and dict(expected_fingerprint) != after:
        _fail("M5 database fingerprint does not match expected acceptance seal")
    return {
        "status": "pass",
        "release_contract": "phase4d5-aq-static-character-tutorial-combined-v1",
        "schema_version": 11,
        "schema_versions": list(range(1, 12)),
        "database_fingerprint": after,
        "sidecars_absent": True,
        "journal_mode": "delete",
        **detail,
        "boundary": {
            "tutorial_coverage": TUTORIAL_COVERAGE,
            "tutorial_corpus_complete": True,
            "push_tips_corpus_complete": False,
            "logical_identity_created": False,
            "cross_family_ownership_leaks": False,
            "scope": "declared_core_tutorial_source_closure_complete",
        },
    }


def _assert_c5c_base(path: Path) -> dict[str, Any]:
    actual = fingerprint(path)
    if actual["sha256"] != BASE_ARTIFACT_SHA256 or actual["size_bytes"] != BASE_ARTIFACT_SIZE_BYTES:
        _fail(
            "M5 requires the accepted C5C Schema 10 input seal: "
            f"sha256={actual['sha256']} size={actual['size_bytes']}"
        )
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA busy_timeout=0")
            _verify_connection(conn, include_tutorial=False)
    except Phase4D5CombinedGateError:
        raise
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError) as exc:
        raise Phase4D5CombinedGateError(str(exc)) from exc
    _assert_sidecars(path, "after C5C input verification")
    if fingerprint(path) != actual:
        _fail("M5 C5C input changed during verification")
    return actual


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    payload = _serialize_report(report)
    created = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(path):
            _fail(f"M5 report path already exists: {path}")
        with path.open("x", encoding="utf-8") as stream:
            created = True
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _sync_directory(path.parent)
    except Phase4D5CombinedGateError:
        if created:
            path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        if created:
            path.unlink(missing_ok=True)
        raise Phase4D5CombinedGateError(
            f"M5 report creation failed; database was not changed: {exc}"
        ) from exc


def append_tutorial_phase4d5_to_c5c_database(
    db_path: str | Path,
    source_root: str | Path,
    manifest_path: str | Path,
    *,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    """Append D2B atomically to a C5C copy, or verify an exact re-entry."""

    if os.name != "posix":
        _fail("M5 append requires POSIX atomic rename")
    target = _regular_file(db_path, "append")
    report_target: Path | None = None
    if report_path is not None:
        report_target = Path(os.path.abspath(os.fspath(report_path)))
        _assert_report_destination(report_target, target)

    before = fingerprint(target)
    started = time.monotonic()
    if before["sha256"] != BASE_ARTIFACT_SHA256 or before["size_bytes"] != BASE_ARTIFACT_SIZE_BYTES:
        # Never send an arbitrary Schema 11 artifact to the D2B builder.  A
        # second invocation is read-only and accepted only for exact final DB.
        aggregate = verify_phase4d5_combined_database(target)
        report = {
            "status": "complete",
            "idempotent": True,
            "state": "final",
            "database_path": str(target),
            "input_fingerprint": before,
            "database_fingerprint": before,
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "d2b": {
                "status": "complete",
                "idempotent": True,
                "database_fingerprint": before,
            },
            "aggregate": aggregate,
        }
        if report_target is not None:
            _write_report(report_target, report)
        return report

    base = _assert_c5c_base(target)
    if base != before:
        _fail("M5 C5C input changed between initial seal and base preflight")
    _load_and_assert_d2b_manifest(manifest_path)

    staging: Path | None = None
    report_staging: Path | None = None
    try:
        staging = _copy_to_private_staging(target, base)
        try:
            d2b_report = ingest_tutorial_phase4d2b_evidence_bundle(
                staging, source_root, manifest_path
            )
        except TutorialPhase4D2BEvidenceError as exc:
            raise Phase4D5CombinedGateError(f"M5 D2B append failed: {exc}") from exc
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
            raise Phase4D5CombinedGateError(f"M5 D2B append failed: {exc}") from exc
        if not isinstance(d2b_report, Mapping):
            _fail("M5 D2B builder returned a non-object report")
        d2b_report = dict(d2b_report)
        if "db_path" in d2b_report:
            d2b_report["db_path"] = str(target)
        if "database_path" in d2b_report:
            d2b_report["database_path"] = str(target)

        aggregate = verify_phase4d5_combined_database(staging)
        report = {
            "status": "complete",
            "idempotent": False,
            "database_path": str(target),
            "base_fingerprint": base,
            "database_fingerprint": aggregate["database_fingerprint"],
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "d2b": d2b_report,
            "aggregate": aggregate,
        }
        if report_target is not None:
            report_staging = _stage_report(
                report_target,
                report,
                database=target,
                database_staging=staging,
            )

        # The original C5C path is untouched while the builder and aggregate
        # run on the private copy.  Promotion performs the locked final CAS.
        _replace_database_atomically(
            target,
            staging,
            base,
            aggregate["database_fingerprint"],
        )
        staging = None
        if report_staging is not None:
            _promote_report(report_staging, report_target)
            report_staging = None
        return report
    except Phase4D5CombinedGateError:
        raise
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        raise Phase4D5CombinedGateError(f"M5 atomic append failed: {exc}") from exc
    finally:
        if staging is not None:
            try:
                _cleanup_private(staging)
            except OSError:
                pass
        if report_staging is not None:
            try:
                _cleanup_private(report_staging)
            except OSError:
                pass


__all__ = [
    "AQ_RUN_ID",
    "BASE_ARTIFACT_SHA256",
    "BASE_ARTIFACT_SIZE_BYTES",
    "EXPECTED_D2B_RUN_ID",
    "EXPECTED_RUN_IDS",
    "Phase4D5CombinedGateError",
    "Phase4D5CombinedPromotedError",
    "append_tutorial_phase4d5_to_c5c_database",
    "fingerprint",
    "verify_phase4d5_combined_database",
]
