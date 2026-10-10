"""Pinned Static run contracts for lightweight runtime eligibility.

The Static release verifiers remain the authority for occurrence correctness.
This module is intentionally a small family-local registry used by the MCP
startup path only.  It first checks a separately full-verified artifact content
seal, then the immutable schema and exact run/parser/snapshot/source metadata
tuple; it never reads a Static evidence occurrence.
No run is selected by recency, completeness claims stored in arbitrary rows,
native IDs, text, or a database-wide evidence count.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .batch_m5d_runtime import BatchM5DError, _verify_schema17 as verify_schema17
from .character_phase4c2_schema import verify_character_phase4c2_schema_signature
from .db import validate_exact_schema_structure
from .tutorial_phase4d2a_schema import exact_schema_11
from .util import canonical_json, sha256_text


SOURCE_UPSTREAM = "https://github.com/DimbreathBot/AnimeGameData"
SOURCE_ID = "anime-game-data"
SOURCE_KIND = "primary"
SOURCE_LICENSE_NOTE = None
SOURCE_RELEASE = "7.0"
SOURCE_BUILD = "CNRELWin7.0.0_R47482070_S47579390_D47579390"
SOURCE_COMMIT = "26df1dfbdf05a82bbb1d97506859f3e1c40718d8"
SOURCE_TREE = "d891ef1d95fdd6c27a1da8a32ea7a61b483647a6"
SOURCE_OBSERVED_AT = "2026-08-19T00:00:00+08:00"

STATIC_SCHEMA_VERSIONS = (9, 10, 11)
REQUIRED_STATIC_TABLES = (
    "static_evidence_container",
    "static_segment_occurrence",
    "static_relationship_occurrence",
    "static_text_resolution_occurrence",
    "static_text_candidate_occurrence",
)


class StrictJSONError(ValueError):
    """Persisted JSON is malformed, non-object, or has duplicate keys."""


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJSONError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json_loads(value: Any, *, require_object: bool = False) -> Any:
    """Parse persisted JSON without allowing duplicate object keys."""

    if not isinstance(value, (str, bytes, bytearray)):
        raise StrictJSONError("JSON value must be text or bytes")
    try:
        parsed = json.loads(value, object_pairs_hook=_reject_duplicate_pairs)
    except (StrictJSONError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StrictJSONError("invalid or duplicate-key JSON") from exc
    if require_object and not isinstance(parsed, dict):
        raise StrictJSONError("JSON value must be an object")
    return parsed


def strict_json_object(value: Any) -> dict[str, Any]:
    """Parse a persisted JSON object and reject duplicates/non-objects."""

    parsed = strict_json_loads(value, require_object=True)
    return parsed


@dataclass(frozen=True)
class StaticArtifactSeal:
    """Content seal for one separately full-verified SQLite artifact."""

    artifact_id: str
    sha256: str
    size_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


# Runtime may trust Static metadata only when the complete SQLite file is
# byte-identical to one of these separately full-verified artifacts.  Paths
# and mtime are deliberately not identity; a private copy is acceptable.
STATIC_ARTIFACT_SEALS = (
    StaticArtifactSeal(
        artifact_id="batch-r4-schema20-accepted",
        sha256="6bef3518ad17df4a7a953d9d064ba4ee0569c86aeb13ae695092fc73b4d63ed1",
        size_bytes=5963210752,
    ),
    StaticArtifactSeal(
        artifact_id="wave1-final-c2-schema10",
        sha256="9f3cf27da1f8e8f546de26f61fc7be46ca0bf357321f365afddfccfd4178bf8b",
        size_bytes=965103616,
    ),
    StaticArtifactSeal(
        artifact_id="phase4c4c-combined-c2-c4b-schema10",
        sha256="2f8b3058110341c8bbbccef85c26a0ff601123a497f2c620f2398bb52a5e74d6",
        size_bytes=1014505472,
    ),
    StaticArtifactSeal(
        artifact_id="phase4c5c-combined-c2-c4b-c5b-schema10",
        sha256="8f5c7e8940292b96f8027b37e0d1ce1757cbacb203b0dc4e6eff5fc657368860",
        size_bytes=1493069824,
    ),
    StaticArtifactSeal(
        artifact_id="phase4d5-final-combined-c2-c4b-c5b-tutorial-schema11",
        sha256="6f6820b7a0464a0cff41d340874a508609a06313b5db9115182a8d239f350fb0",
        size_bytes=1630650368,
    ),
    StaticArtifactSeal(
        artifact_id="phase4e5-final-combined-world-archive-schema12",
        sha256="9c74d81d9b56ebd314a0952a7ff3b4a2fe96a46d73020e27f7eaf5ba7554bbda",
        size_bytes=1700741120,
    ),
    StaticArtifactSeal(
        artifact_id="batch-m5e-wave02-schema17-accepted",
        sha256="464a49e77b2ebf7fd7b0ae68d907fdd3307ef01e0d8537065f4b54ad4a53ddab",
        size_bytes=5244338176,
    ),
    StaticArtifactSeal(
        artifact_id="batch-r2-schema19-accepted",
        sha256="000bb61aea4825d895a7b0a4b51f9f3965e45f94a4c54069b3c0947ca9f1039b",
        size_bytes=5456945152,
    ),
)


def _artifact_sidecars(path: Path) -> tuple[Path, Path, Path]:
    return (
        Path(f"{path}-journal"),
        Path(f"{path}-wal"),
        Path(f"{path}-shm"),
    )


def _artifact_sidecars_present(sidecars: tuple[Path, Path, Path]) -> bool:
    return any(os.path.lexists(str(sidecar)) for sidecar in sidecars)


def match_static_artifact(path: str | Path | None) -> StaticArtifactSeal | None:
    """Stream-hash one stable file and return its approved content seal."""

    if path is None:
        return None
    try:
        target = Path(path)
        if str(target) in {"", ":memory:"} or not target.is_file():
            return None
        sidecars = _artifact_sidecars(target)
        if _artifact_sidecars_present(sidecars):
            return None
        before = target.stat()
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        digest = hashlib.sha256()
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
        after = target.stat()
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if before_identity != after_identity:
            return None
        if _artifact_sidecars_present(sidecars):
            return None
        actual_sha256 = digest.hexdigest()
        for seal in STATIC_ARTIFACT_SEALS:
            if seal.size_bytes == after.st_size and seal.sha256 == actual_sha256:
                return seal
    except (OSError, TypeError, ValueError):
        return None
    return None


@dataclass(frozen=True)
class StaticRunContract:
    """One reviewed Static run binding, with no generic-family inference."""

    run_family: str
    manifest_id: str
    ingest_run_id: str
    snapshot_id: str
    parser_build_id: str
    config_checksum: str
    manifest_raw_sha256: str
    parser_version: str
    mapping_version: str
    parser_fingerprint: str
    scope_sha256: str
    config_json_sha256: str
    coverage: tuple[tuple[str, str], ...]
    corpus_complete: bool
    boundary_summary: dict[str, Any]
    snapshot_metadata: dict[str, Any]
    sealed_counts: dict[str, Any]
    sealed_status_counts: dict[str, Any]
    sealed_occurrence_set_digests: dict[str, str]

    @property
    def source(self) -> dict[str, Any]:
        return {
            "source_id": SOURCE_ID,
            "upstream": SOURCE_UPSTREAM,
            "release_label": SOURCE_RELEASE,
            "build": SOURCE_BUILD,
            "commit": SOURCE_COMMIT,
            "tree": SOURCE_TREE,
            "observed_at": SOURCE_OBSERVED_AT,
        }

    @property
    def family_coverage(self) -> dict[str, str]:
        return dict(self.coverage)

    def binding_from_metadata(
        self,
        *,
        scope: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a self-describing accepted binding for diagnostics/tests."""

        return {
            "run_family": self.run_family,
            "manifest_id": self.manifest_id,
            "manifest_raw_sha256": self.manifest_raw_sha256,
            "ingest_run_id": self.ingest_run_id,
            "snapshot_id": self.snapshot_id,
            "parser_build_id": self.parser_build_id,
            "parser_version": self.parser_version,
            "mapping_version": self.mapping_version,
            "parser_fingerprint": self.parser_fingerprint,
            "config_checksum": self.config_checksum,
            "source": self.source,
            "scope": scope,
            "config": config,
            "coverage": self.family_coverage,
            "corpus_complete": self.corpus_complete,
            "boundary_summary": self.boundary_summary,
            # These are release-time sealed facts.  Runtime does not
            # recompute them from occurrence tables.
            "sealed": {
                "counts": self.sealed_counts,
                "status_counts": self.sealed_status_counts,
                "occurrence_set_digests": self.sealed_occurrence_set_digests,
            },
        }

    def matches(
        self,
        row: Any,
        *,
        scope: Any,
        config: Any,
        snapshot_metadata: Any,
        same_family_ids: list[str] | None,
    ) -> bool:
        """Check only immutable ledger/provenance fields, never evidence rows."""

        source = self.source
        if not isinstance(scope, dict) or not isinstance(config, dict):
            return False
        scope_raw = row["scope_json"]
        config_raw = row["config_json"]
        try:
            canonical_scope = canonical_json(scope)
            canonical_config = canonical_json(config)
        except (TypeError, ValueError):
            return False
        return (
            row["id"] == self.ingest_run_id
            and row["snapshot_id"] == self.snapshot_id
            and row["parser_build_id"] == self.parser_build_id
            and row["run_status"] == "complete"
            and row["run_created_at"] == SOURCE_OBSERVED_AT
            and isinstance(scope_raw, str)
            and scope_raw == canonical_scope
            and sha256_text(scope_raw) == self.scope_sha256
            and isinstance(config_raw, str)
            and config_raw == canonical_config
            and sha256_text(config_raw) == self.config_json_sha256
            and scope.get("family") == self.run_family
            and scope.get("manifest_id") == self.manifest_id
            and config.get("manifest_raw_sha256") == self.manifest_raw_sha256
            and row["parser_version"] == self.parser_version
            and row["mapping_version"] == self.mapping_version
            and row["fingerprint"] == self.parser_fingerprint
            and row["config_checksum"] == self.config_checksum
            and row["parser_created_at"] == SOURCE_OBSERVED_AT
            and row["source_id"] == source["source_id"]
            and row["source_kind"] == SOURCE_KIND
            and row["source_url"] == source["upstream"]
            and row["source_license_note"] == SOURCE_LICENSE_NOTE
            and row["release_label"] == source["release_label"]
            and row["build"] == source["build"]
            and row["snapshot_commit"] == source["commit"]
            and row["snapshot_tree"] == source["tree"]
            and row["observed_at"] == source["observed_at"]
            and row["snapshot_status"] == "active"
            and snapshot_metadata == self.snapshot_metadata
            and same_family_ids == [self.ingest_run_id]
        )


_B1_COUNTS = {"selected_files": 5, "containers": 5, "segments": 57, "relationships": 6}
_B2_COUNTS = {
    "selected_files": 36,
    "containers": 56,
    "segments": 420,
    "relationships": 128,
    "text_resolutions": 144,
    "text_candidates": 129,
}
_B3_COUNTS = {
    "selected_files": 1639,
    "source_records": 9016,
    "containers": 3893,
    "segments": 33135,
    "relationships": 5584,
    "text_resolutions": 5256,
    "text_candidates": 5123,
}
_B5_COUNTS = {
    "selected_files": 926,
    "source_records": 32258,
    "containers": 6072,
    "segments": 11475,
    "relationships": 20322,
    "text_resolutions": 27027,
    "text_candidates": 26186,
}
_B6_COUNTS = {
    "selected_files": 8,
    "source_records": 100138,
    "containers": 11570,
    "segments": 0,
    "relationships": 70586,
    "text_resolutions": 208260,
    "text_candidates": 88568,
    "text_assets": 88568,
    "text_owner_occurrences": 69420,
}

_B1_SCOPE_SHA256 = "5e2942b5e2355a89c37f20d0f53d82ffc7ace276dd2f8a0d540833c389a7f948"
_B1_CONFIG_SHA256 = "7a58737a4643daa9ba6b067a244d86d48952287c2c0ed36a8398091869d8b41e"
_B2_SCOPE_SHA256 = "9cee875cc88decc1a47d95070a0f0bc15f4fa3b9fe1f942d7a56eb410c0c039e"
_B2_CONFIG_SHA256 = "f4c9abe4003f13ecc4a2bd3cd51a2451c52869e716f29f75cb18165a55d34bef"
_B3_SCOPE_SHA256 = "24280a9f840a4f6e1bbdd7d53fc611cad5cf7e942525b8bf1b118b6fea8b5c14"
_B3_CONFIG_SHA256 = "2ea70d76ca097e84873a8e6c14d85b346592f731dac91784d211d1dc112a2375"
_B5_SCOPE_SHA256 = "554dbcf6409d1e130e4782273887302afa15d8b1c554a2f5ae53f0c34e64ca3c"
_B5_CONFIG_SHA256 = "2326fe6049367ad9cc87d98552d63632cc6aeaed474275337fe239f4f26d1287"
_B6_SCOPE_SHA256 = "b421afe36f0d9f2887d3eade3a11f4b2d03d48f6e224bd0192990de5a1632f08"
_B6_CONFIG_SHA256 = "3ce85dde701496e12864f55bfdfa0a6f85c65fad70937a016861d01081895a73"

_B3_DIGESTS = {
    "containers": "bd2f90e02db267e21480a13317c2425cae7269c0f22592702a9dd885e803256f",
    "files": "b44939949056d60a73d8b97a7e07c3aebf797bdc81efa94cdcda78e170e90226",
    "relationships": "38771a6a7d18ee1578bbb098acfa64b9f284891d7f026a3ed09f2712d4920797",
    "segments": "2da8192f6875bc1ac7ce70e5dbe6bc5f704ba41a66cb486cff22b5497f48d475",
    "source_records": "14de5865e56e5b54df0455f961c0a3dcae0ff1db299fa3ed002636e221f5f84c",
    "text_assets": "60ec442c60139635f0ed5da09643b8a373fe50a64d6e646549243e69c906e635",
    "text_candidates": "e0994aab9ba582f4fc2a97b5350d51d25de987a4b11e658a96966d651ba6161a",
    "text_resolutions": "c029a7494dc61c7f4929e52badd2c6b7cf7b4279ae80b8b14ab701ff999a8a91",
}
_B5_DIGESTS = {
    "containers": "bc3aeccb9d2f5ee02a02afa522949dac2dfaeaf09387654216ca1953faf8bb2e",
    "files": "eaf520eba1f6405ee6d4cc3c52020cf7e120785d6f14c34c3ed46e21f61f834c",
    "relationships": "27400d5996dd7bbd66e15e0e186120e681f8e365c8619e3aa821ca84a059d770",
    "segments": "b2640a2fa98a72a102a8fbdb096f5505e3cd1b4fb5d03fd001b2ee237999d299",
    "source_records": "834463107bab565e87088560aa56b5e238e271bc58406ed7324f874add6de6c7",
    "text_assets": "4e7f90e5ffc6e5cf2a76025f1c5c3d7084d67312a82fa46a667acf37ae3d8cdd",
    "text_candidates": "2274c5091ed5bfb94327d06b3bc02b249eeec625e7ffc4fd38ac55a2429d8a44",
    "text_resolutions": "6f7a5438473be8e45eca4b0e8ae35dc13cadde1dd6c00cd135d98c54c336619a",
}
_B6_DIGESTS = {
    "containers": "227589696c53e33cde43e9095f819f71cab4417d8f90d8098c97c2089059a71b",
    "files": "4e76ebbe3ef0cb854f7beaad0d8a2f6cddf2d24303db0b9ce73c7fceab40ec4d",
    "relationships": "9db1008a75140e11f62d2f9c3e4b14f5396c8bfceb85d8b7cb81cd4a02e53b8f",
    "segments": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    "source_records": "019976f47794c1ec1e4d0ffca7cb70e4dc5645f57af7df0b70c6bc5633a12256",
    "text_assets": "c5606038537970491de807d39b03b6d591369746a81e599e622b192a241d78ff",
    "text_candidates": "9a11a95339f96d2e99f1345a16ea757162f630b9e094708e73c0546db70975f4",
    "text_owners": "ad54c59a53a98c85612f838ba705961b2a8ca226be6caafbcb8de2d66717b453",
    "text_resolutions": "f0ec1db600b8ffcd38a0529777869b999222af0e331bfbd61b864a01bd981f88",
}

_B2_STATUSES = {
    "text_resolutions": {
        "both_conflict": 0,
        "both_equal": 0,
        "missing": 15,
        "unique_main": 3,
        "unique_medium": 126,
    }
}
_B3_STATUSES = {
    "readables": {
        "blank_whitespace_only": 1,
        "empty": 0,
        "narrative": 1625,
        "placeholder_exact_test": 0,
    },
    "relationships": {"ambiguous": 2, "dangling": 11, "resolved": 5542, "unresolved": 29},
    "text_resolutions": {
        "both_conflict": 0,
        "both_equal": 0,
        "missing": 133,
        "unique_main": 0,
        "unique_medium": 5123,
    },
}
_B5_STATUSES = {
    "relationships": {"ambiguous": 0, "dangling": 0, "resolved": 20151, "unresolved": 171},
    "text_resolutions": {
        "both_conflict": 0,
        "both_equal": 0,
        "missing": 841,
        "unique_main": 0,
        "unique_medium": 26186,
    },
}
_B6_STATUSES = {
    "relationships": {"ambiguous": 0, "dangling": 0, "resolved": 70586, "unresolved": 0},
    "text_resolutions": {
        "both_conflict": 0,
        "both_equal": 0,
        "missing": 119692,
        "unique_main": 1932,
        "unique_medium": 86636,
    },
}


def _contract(
    *,
    run_family: str,
    manifest_id: str,
    ingest_run_id: str,
    snapshot_id: str,
    parser_build_id: str,
    config_checksum: str,
    manifest_raw_sha256: str,
    parser_version: str,
    mapping_version: str,
    parser_fingerprint: str,
    scope_sha256: str,
    config_json_sha256: str,
    coverage: dict[str, str],
    corpus_complete: bool,
    boundary_summary: dict[str, Any],
    snapshot_metadata: dict[str, Any],
    sealed_counts: dict[str, Any],
    sealed_status_counts: dict[str, Any],
    sealed_occurrence_set_digests: dict[str, str],
) -> StaticRunContract:
    return StaticRunContract(
        run_family=run_family,
        manifest_id=manifest_id,
        ingest_run_id=ingest_run_id,
        snapshot_id=snapshot_id,
        parser_build_id=parser_build_id,
        config_checksum=config_checksum,
        manifest_raw_sha256=manifest_raw_sha256,
        parser_version=parser_version,
        mapping_version=mapping_version,
        parser_fingerprint=parser_fingerprint,
        scope_sha256=scope_sha256,
        config_json_sha256=config_json_sha256,
        coverage=tuple(sorted(coverage.items())),
        corpus_complete=corpus_complete,
        boundary_summary=boundary_summary,
        snapshot_metadata=snapshot_metadata,
        sealed_counts=sealed_counts,
        sealed_status_counts=sealed_status_counts,
        sealed_occurrence_set_digests=sealed_occurrence_set_digests,
    )


STATIC_RUN_CONTRACTS = tuple(
    sorted(
        (
            _contract(
                run_family="static_book1",
                manifest_id="anime-game-data-7.0-book1-static-evidence-canary",
                ingest_run_id="ir-static-book1:ee4191875f6ad93652093a81deeb3f085fe794b6",
                snapshot_id="anime-game-data:7.0:26df1dfbdf05",
                parser_build_id="pb-static-book1:72fb8a8f7f9e2d686e173370e2ba5637dc182633",
                config_checksum="550ddb3917d593b09d9a84f5bbd07ae81bccf8ea844d0736dc2dd53ca69b1e53",
                manifest_raw_sha256="f9e639ed79f7c57df967c93df13ecc826b8d564af9c8caccf253103bf4a5168c",
                parser_version="static-book1-parser-v1",
                mapping_version="static-book1-evidence-v1",
                parser_fingerprint="static-book1-sealed-evidence",
                scope_sha256=_B1_SCOPE_SHA256,
                config_json_sha256=_B1_CONFIG_SHA256,
                coverage={"books": "canary_only"},
                corpus_complete=False,
                boundary_summary={"selected_file_count": 5, "locales": ["CHS", "EN", "JP"]},
                snapshot_metadata={
                    "corpus_manifest_id": "anime-game-data-7.0-aq-7000-7014",
                    "scope": "AQ 7000-7014 sealed quest evidence corpus",
                },
                sealed_counts=_B1_COUNTS,
                sealed_status_counts={},
                sealed_occurrence_set_digests={},
            ),
            _contract(
                run_family="static_phase4b2_multifamily",
                manifest_id="anime-game-data-7.0-phase4b2-multifamily-canary",
                ingest_run_id="ir-static-phase4b2:2747f7a3f7ea66992013bdf2678336b2e040e1a3",
                snapshot_id="anime-game-data:7.0:26df1dfbdf05",
                parser_build_id="pb-static-phase4b2:c51a355874c310368e67dc88ea2e90e3c1bead3b",
                config_checksum="f3d5354d5ac689b0e652bc759fc669b9232599b832169a3fafce7757d45cf316",
                manifest_raw_sha256="9715c198a3f92e522076fa24e60902eda9f415c43a2823d0cffa28216868f57e",
                parser_version="static-phase4b2-sealed-parser-v1",
                mapping_version="static-phase4b2-multifamily-projection-v1",
                parser_fingerprint="static-phase4b2-sealed-multifamily-evidence",
                scope_sha256=_B2_SCOPE_SHA256,
                config_json_sha256=_B2_CONFIG_SHA256,
                coverage={
                    "books": "canary_only",
                    "weapons": "canary_only",
                    "reliquaries": "canary_only",
                    "materials": "canary_only",
                },
                corpus_complete=False,
                boundary_summary={
                    "selected_file_count": 36,
                    "locales": ["CHS", "EN", "JP"],
                    "families": ["books", "weapons", "reliquaries", "materials"],
                },
                snapshot_metadata={
                    "corpus_manifest_id": "anime-game-data-7.0-aq-7000-7014",
                    "scope": "AQ 7000-7014 sealed quest evidence corpus",
                },
                sealed_counts=_B2_COUNTS,
                sealed_status_counts=_B2_STATUSES,
                sealed_occurrence_set_digests={},
            ),
            _contract(
                run_family="static_phase4b3_book_weapon_corpus",
                manifest_id="anime-game-data-7.0-phase4b3-book-weapon-corpus",
                ingest_run_id="ir-static-phase4b3:d4c3d8df68eec5b8b8409ecebd3f09cf25e9e0f1",
                snapshot_id="anime-game-data:7.0:26df1dfbdf05",
                parser_build_id="pb-static-phase4b3:2981626e546f04963c71e056238c9ee3f27051ed",
                config_checksum="3d821947d7ce4c0dd50ee9eb5da666ffce400357bbb19c6cc5733ab3d9a0bc0c",
                manifest_raw_sha256="dd559a9482c4f85e114f60f0ce903b83591199ff17971933dfc98a1bfa171573",
                parser_version="static-phase4b3-corpus-parser-v1",
                mapping_version="static-phase4b3-books-weapons-projection-v1",
                parser_fingerprint="static-phase4b3-sealed-book-weapon-corpus",
                scope_sha256=_B3_SCOPE_SHA256,
                config_json_sha256=_B3_CONFIG_SHA256,
                coverage={"books": "corpus_complete", "weapons": "corpus_complete"},
                corpus_complete=True,
                boundary_summary={
                    "selected_file_count": 1639,
                    "locales": ["CHS", "EN", "JP"],
                    "primary_families": ["books", "weapons"],
                },
                snapshot_metadata={
                    "corpus_manifest_id": "anime-game-data-7.0-aq-7000-7014",
                    "scope": "AQ 7000-7014 sealed quest evidence corpus",
                },
                sealed_counts=_B3_COUNTS,
                sealed_status_counts=_B3_STATUSES,
                sealed_occurrence_set_digests=_B3_DIGESTS,
            ),
            _contract(
                run_family="static_phase4b5_reliquary_corpus",
                manifest_id="anime-game-data-7.0-phase4b5-reliquary-corpus",
                ingest_run_id="ir-static-phase4b5:e184c9876aa0ab8494ff3e37a43c17ea73eea868",
                snapshot_id="snap:48f434afe1049d73a502e3f2b24b67e575436300",
                parser_build_id="pb-static-phase4b5:bda6bf05c49aa19448ca2db7d3dc1dd5eb7d1841",
                config_checksum="08c6fa3a33eb6dddc5386118cabf007fdf90ec787f4d43f4a8ff789533e8ba26",
                manifest_raw_sha256="f9498c723ed9cb62a0cf84efd9f046a986210d3b436f336409da50f5d9d10f56",
                parser_version="static-phase4b5-corpus-parser-v1",
                mapping_version="static-phase4b5-reliquary-projection-v1",
                parser_fingerprint="static-phase4b5-sealed-reliquary-corpus",
                scope_sha256=_B5_SCOPE_SHA256,
                config_json_sha256=_B5_CONFIG_SHA256,
                coverage={"reliquaries": "corpus_complete"},
                corpus_complete=True,
                boundary_summary={
                    "selected_file_count": 926,
                    "locales": ["CHS", "EN", "JP"],
                    "primary_families": ["reliquaries"],
                },
                snapshot_metadata={"sealed_source_manifest": True},
                sealed_counts=_B5_COUNTS,
                sealed_status_counts=_B5_STATUSES,
                sealed_occurrence_set_digests=_B5_DIGESTS,
            ),
            _contract(
                run_family="static_phase4b6_material_corpus",
                manifest_id="anime-game-data-7.0-phase4b6-material-corpus",
                ingest_run_id="ir-static-phase4b6:43defcef9307317f60d3ca0903ea11b0bc1690c5",
                snapshot_id="snap:48f434afe1049d73a502e3f2b24b67e575436300",
                parser_build_id="pb-static-phase4b6:e8736517278077bcc8dd471e04726fa57689af06",
                config_checksum="7e142459fa5a522d3bef75c9bf9ebc6470f7623e21572d7e3c83b5ac66613962",
                manifest_raw_sha256="13a2a9fdc1fb9cd9df61a5308188940199a103ce77e7d1f45666e618b4d809d8",
                parser_version="static-phase4b6-material-parser-v1",
                mapping_version="static-phase4b6-material-projection-v1",
                parser_fingerprint="static-phase4b6-sealed-material-corpus",
                scope_sha256=_B6_SCOPE_SHA256,
                config_json_sha256=_B6_CONFIG_SHA256,
                coverage={"materials": "corpus_complete"},
                corpus_complete=True,
                boundary_summary={
                    "selected_file_count": 8,
                    "locales": ["CHS", "EN", "JP"],
                    "primary_families": ["materials"],
                },
                snapshot_metadata={"sealed_source_manifest": True},
                sealed_counts=_B6_COUNTS,
                sealed_status_counts=_B6_STATUSES,
                sealed_occurrence_set_digests=_B6_DIGESTS,
            ),
        ),
        key=lambda item: item.ingest_run_id,
    )
)
STATIC_RUN_CONTRACT_BY_ID = {
    item.ingest_run_id: item for item in STATIC_RUN_CONTRACTS
}


def exact_static_schema(conn: Any) -> bool:
    """Check the immutable schema prefix without reading evidence rows."""

    try:
        existing_tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?, ?, ?, ?, ?)",
                REQUIRED_STATIC_TABLES,
            )
        }
        if existing_tables != set(REQUIRED_STATIC_TABLES):
            return False
        versions = tuple(
            int(row[0])
            for row in conn.execute(
                "SELECT version FROM schema_migration ORDER BY version"
            )
        )
        if versions not in (
            tuple(range(1, 10)),
            tuple(range(1, 11)),
            tuple(range(1, 12)),
            tuple(range(1, 13)),
            tuple(range(1, 14)),
            tuple(range(1, 16)),
            tuple(range(1, 17)),
            tuple(range(1, 18)),
            tuple(range(1, 20)), tuple(range(1, 21)),
        ):
            return False
        if versions[-1] == 11:
            return exact_schema_11(conn)
        if versions[-1] == 17:
            # Schema 17 is an additive wave schema, not a generic continuation
            # of Schema 16. Reuse the exact M5D gate so the historical Static
            # surface cannot bypass the pinned ledger, migration-0017 raw SQL
            # seal, or additive structural signature.
            verify_schema17(conn)
            return True
        # The repository's structural signature includes the complete static
        # ledger and immutable triggers.  It reads sqlite_master/PRAGMA only.
        validate_exact_schema_structure(conn, versions[-1])
        if versions[-1] in (10, 12):
            verify_character_phase4c2_schema_signature(conn)
        return True
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        sqlite3.Error,
        BatchM5DError,
        RuntimeError,
    ):
        return False


def family_run_ids(conn: Any, run_family: str) -> list[str] | None:
    """Return same-family ledger IDs; malformed metadata fails closed."""

    result: list[str] = []
    try:
        rows = conn.execute("SELECT id,scope_json FROM ingest_run ORDER BY id")
        for row in rows:
            scope = strict_json_object(row["scope_json"])
            if scope.get("family") == run_family:
                result.append(str(row["id"]))
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        sqlite3.Error,
    ):
        return None
    return result


_RUN_SELECT = """
    SELECT ir.id, ir.snapshot_id, ir.parser_build_id,
           ir.scope_json, ir.config_json, ir.status AS run_status,
           ir.created_at AS run_created_at,
           pb.parser_version, pb.mapping_version, pb.fingerprint,
           pb.config_checksum, pb.created_at AS parser_created_at,
           ss.source_id, ss.release_label, ss.build,
           ss."commit" AS snapshot_commit, ss.tree AS snapshot_tree,
           ss.observed_at, ss.status AS snapshot_status,
           ss.metadata_json AS snapshot_metadata_json,
           sc.kind AS source_kind, sc.url AS source_url,
           sc.license_note AS source_license_note
      FROM ingest_run ir
      JOIN parser_build pb ON pb.id=ir.parser_build_id
      JOIN source_snapshot ss ON ss.id=ir.snapshot_id
      JOIN source_catalog sc ON sc.id=ss.source_id
     WHERE ir.id=?
"""


def qualify_static_run(conn: Any, contract: StaticRunContract) -> dict[str, Any] | None:
    """Qualify one exact contract using metadata-only point queries."""

    try:
        row = conn.execute(_RUN_SELECT, (contract.ingest_run_id,)).fetchone()
        if row is None:
            return None
        scope = strict_json_object(row["scope_json"])
        config = strict_json_object(row["config_json"])
        snapshot_metadata = strict_json_object(row["snapshot_metadata_json"])
        if not (str(row["scope_json"]) == canonical_json(scope)):
            return None
        if not (str(row["config_json"]) == canonical_json(config)):
            return None
        same_family = family_run_ids(conn, contract.run_family)
        if not contract.matches(
            row,
            scope=scope,
            config=config,
            snapshot_metadata=snapshot_metadata,
            same_family_ids=same_family,
        ):
            return None
        return contract.binding_from_metadata(scope=scope, config=config)
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        sqlite3.Error,
    ):
        return None


__all__ = [
    "STATIC_ARTIFACT_SEALS",
    "REQUIRED_STATIC_TABLES",
    "SOURCE_BUILD",
    "SOURCE_COMMIT",
    "SOURCE_ID",
    "SOURCE_OBSERVED_AT",
    "SOURCE_RELEASE",
    "SOURCE_TREE",
    "SOURCE_UPSTREAM",
    "STATIC_RUN_CONTRACTS",
    "STATIC_RUN_CONTRACT_BY_ID",
    "STATIC_SCHEMA_VERSIONS",
    "StrictJSONError",
    "StaticArtifactSeal",
    "StaticRunContract",
    "exact_static_schema",
    "family_run_ids",
    "match_static_artifact",
    "qualify_static_run",
    "strict_json_loads",
    "strict_json_object",
]
