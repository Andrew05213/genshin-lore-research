"""Explicit sealed Character run contracts used by read-only retrieval.

This module is deliberately a small registry, not a Character ontology or a
generic adapter.  Each entry describes one reviewed canary binding.  The
registry is used for metadata qualification only; it never counts Character
occurrences and never merges evidence between runs.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from .batch_m5d_runtime import BatchM5DError, _verify_schema17 as verify_schema17
from .character_phase4c2_manifest import (
    EXPECTED_AVATAR_ID as C2_AVATAR_ID,
    EXPECTED_BUILD as C2_BUILD,
    EXPECTED_COMMIT as C2_COMMIT,
    EXPECTED_MANIFEST_ID as C2_MANIFEST_ID,
    EXPECTED_TREE as C2_TREE,
    EXPECTED_VERSIONS as C2_VERSIONS,
)
from .character_phase4c2_schema import (
    CharacterPhase4C2SchemaError,
    verify_character_phase4c2_schema_signature,
)
from .db import validate_exact_schema_structure
from .tutorial_phase4d2a_schema import exact_schema_11
from .character_phase4c4b_manifest import (
    EXPECTED_AVATAR_IDS as C4B_AVATAR_IDS,
    EXPECTED_BOUNDARY_AVATAR_IDS as C4B_BOUNDARY_AVATAR_IDS,
    EXPECTED_BUILD as C4B_BUILD,
    EXPECTED_COMMIT as C4B_COMMIT,
    EXPECTED_MANIFEST_ID as C4B_MANIFEST_ID,
    EXPECTED_POSITIVE_AVATAR_IDS as C4B_POSITIVE_AVATAR_IDS,
    EXPECTED_TREE as C4B_TREE,
    EXPECTED_VERSIONS as C4B_VERSIONS,
)
from .character_phase4c5b_manifest import (
    EXPECTED_BUILD as C5B_BUILD,
    EXPECTED_COMMIT as C5B_COMMIT,
    EXPECTED_COVERAGE as C5B_COVERAGE,
    EXPECTED_MANIFEST_ID as C5B_MANIFEST_ID,
    EXPECTED_SNAPSHOT_ID as C5B_SNAPSHOT_ID,
    EXPECTED_TREE as C5B_TREE,
    EXPECTED_VERSIONS as C5B_VERSIONS,
)


SOURCE_UPSTREAM = "https://github.com/DimbreathBot/AnimeGameData"
OBSERVED_AT = "2026-08-19T00:00:00+08:00"
LOCALES = ("CHS", "EN", "JP")
SELECTED_PATHS = tuple(sorted(
    (
        "BinOutput/Voice/Items/1248787199.json",
        "BinOutput/Voice/Items/1560160621.json",
        "BinOutput/Voice/Items/2939652103.json",
        "BinOutput/Voice/Items/750982387.json",
        "BinOutput/Voice/Lut/Lut.json",
        "ExcelBinOutput/AvatarExcelConfigData.json",
        "ExcelBinOutput/FetterInfoExcelConfigData.json",
        "ExcelBinOutput/FetterStoryExcelConfigData.json",
        "ExcelBinOutput/FettersExcelConfigData.json",
        "TextMap/TextMapCHS.json",
        "TextMap/TextMapEN.json",
        "TextMap/TextMapJP.json",
        "TextMap/TextMap_MediumCHS.json",
        "TextMap/TextMap_MediumEN.json",
        "TextMap/TextMap_MediumJP.json",
    )
))


@dataclass(frozen=True)
class CharacterRunContract:
    """One literal reviewed run binding, with no identity inference."""

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
    source_build: str
    source_commit: str
    source_tree: str
    boundary: dict[str, Any]
    scope: dict[str, Any]
    config: dict[str, Any]
    coverage: str = "canary_only"
    corpus_complete: bool = False

    @property
    def source(self) -> dict[str, Any]:
        return {
            "source_id": "anime-game-data",
            "upstream": SOURCE_UPSTREAM,
            "release_label": "7.0",
            "build": self.source_build,
            "commit": self.source_commit,
            "tree": self.source_tree,
            "observed_at": OBSERVED_AT,
        }

    def boundary_public(self) -> dict[str, Any]:
        return {
            **self.boundary,
            "locales": list(LOCALES),
            "full_character_scan": False,
            "full_voice_scan": False,
        }

    def binding_from_scope(self, scope: dict[str, Any]) -> dict[str, Any]:
        """Return a stable, self-describing capability item."""

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
            "config": self.config,
            "coverage": self.coverage,
            "corpus_complete": self.corpus_complete,
            "boundary": self.boundary_public(),
        }


_C2_SCOPE = {
    "family": "character_phase4c2_avatar10000003_canary",
    "manifest_id": C2_MANIFEST_ID,
    "selected_paths": list(SELECTED_PATHS),
}
_C2_CONFIG = {
    "manifest_raw_sha256": "8689eed8c762d1441f87958457d3ec24208039fea699ae285b3ed5da6633643e",
    "versions": dict(C2_VERSIONS),
    "logical_identity_created": False,
    "mcp_capability_added": False,
}

C2_CONTRACT = CharacterRunContract(
    run_family="character_phase4c2_avatar10000003_canary",
    manifest_id=C2_MANIFEST_ID,
    ingest_run_id="ir-character-phase4c2:8a29b630963be8ec8d363b48516b2266824a9197",
    snapshot_id="snap:48f434afe1049d73a502e3f2b24b67e575436300",
    parser_build_id="pb-character-phase4c2:3019985d0b1e5e8121a58677143313a232056aac",
    config_checksum="eb92673576d68dabf64ead29850164726dae93d5fc5228d08b847c26f7ec31d0",
    manifest_raw_sha256=_C2_CONFIG["manifest_raw_sha256"],
    parser_version=C2_VERSIONS["parser"],
    mapping_version=C2_VERSIONS["projection"],
    parser_fingerprint="character-phase4c2-sealed-avatar10000003-canary",
    source_build=C2_BUILD,
    source_commit=C2_COMMIT,
    source_tree=C2_TREE,
    boundary={"avatar_id": C2_AVATAR_ID},
    scope=_C2_SCOPE,
    config=_C2_CONFIG,
)

_C4B_SCOPE = {
    "family": "character_phase4c4b_bounded_six_avatar",
    "manifest_id": C4B_MANIFEST_ID,
    "avatar_ids": list(C4B_AVATAR_IDS),
    "positive_avatar_ids": list(C4B_POSITIVE_AVATAR_IDS),
    "boundary_avatar_ids": list(C4B_BOUNDARY_AVATAR_IDS),
    "coverage": "canary_only",
    "corpus_complete": False,
    "selected_paths": list(SELECTED_PATHS),
}
_C4B_CONFIG = {
    "manifest_raw_sha256": "3a96e7fe66127bfa6491e893ca813d3cb5d62a1a0e113660aef2197ec08407dd",
    "versions": dict(C4B_VERSIONS),
    "source_commit": C4B_COMMIT,
    "source_tree": C4B_TREE,
    "avatar_ids": list(C4B_AVATAR_IDS),
    "coverage": "canary_only",
    "corpus_complete": False,
    "logical_identity_created": False,
    "mcp_capability_added": False,
}

C4B_CONTRACT = CharacterRunContract(
    run_family="character_phase4c4b_bounded_six_avatar",
    manifest_id=C4B_MANIFEST_ID,
    ingest_run_id="ir-character-phase4c4b:7a150669a294e1794255e6c82bc0bcb0b716547b",
    snapshot_id="snap:48f434afe1049d73a502e3f2b24b67e575436300",
    parser_build_id="pb-character-phase4c4b:10626288e9572fca2ec60484522ffb9337449291",
    config_checksum="5b8bb390ac5f4919269fc72ef7746386e7902241b0f27b6dd4a87021f30a1943",
    manifest_raw_sha256=_C4B_CONFIG["manifest_raw_sha256"],
    parser_version=C4B_VERSIONS["parser"],
    mapping_version=C4B_VERSIONS["projection"],
    parser_fingerprint="character-phase4c4b-bounded-six-avatar",
    source_build=C4B_BUILD,
    source_commit=C4B_COMMIT,
    source_tree=C4B_TREE,
    boundary={
        "avatar_ids": list(C4B_AVATAR_IDS),
        "positive_avatar_ids": list(C4B_POSITIVE_AVATAR_IDS),
        "boundary_avatar_ids": list(C4B_BOUNDARY_AVATAR_IDS),
    },
    scope=_C4B_SCOPE,
    config=_C4B_CONFIG,
)


# C5B is a sealed raw-evidence boundary rather than a claim of full
# Character/Voice/TextMap/7.0 coverage.  The complete scope/config values are
# copied from the accepted C5B builder contract so capability qualification is
# metadata-only and does not load or derive the 15-file manifest at startup.
_C5B_SCOPE = {
    "closure_counts": {
        "audio_variants": 9611,
        "avatar_relations": 10112,
        "condition_empty": 33429,
        "condition_nonempty": 5695,
        "condition_slots": 39124,
        "distinct_audio_paths": 9600,
        "distinct_character_child_selectors": 128,
        "opaque_slots": 39055,
        "raw_slots": 78179,
        "relations": 52702,
        "rows": 10277,
        "selected_files": 15,
        "source_records": 76414,
        "text_candidates": 65387,
        "text_owner_occurrences": 33558,
        "text_resolutions": 100674,
        "unattached_children": 105,
        "unattached_lut_entries": 8,
        "voice_attachments": 9032,
        "voice_containers": 375,
    },
    "corpus_complete": True,
    "coverage": C5B_COVERAGE,
    "family": "character_phase4c5b_full_source_closure",
    "full_character_scan": False,
    "full_voice_scan": False,
    "logical_identity_created": False,
    "manifest_id": C5B_MANIFEST_ID,
    "mcp_capability_added": False,
    "occurrence_set_digests": {
        "audio_variants": "747834eca34f95467e1b7c520cd2caef0c279605f9c54045451c27f761c53c2d",
        "files": "8dead5265d717701a937c5e2a28a51dfdf28e8e526207f15268fd34e0e4e541e",
        "raw_slots": "5752eaf817b6995e59709bf5fde629591b2d2dbddc6c1db37134989b4078129f",
        "relations": "b4979a01f2a7a90fd78e1627977038e39c1b57e515dd11a2342cbe31c887f347",
        "rows": "63ea42a2ea8548e84bbee56f7e7b31dfafc995d86713851a77ef93f3f9ccf251",
        "source_records": "07fa72afd3717b0a637def182923aa5a947acb6b902afbac1082f17984521bf5",
        "text_candidates": "f8449982e2c755129cddeb542dbdf7caae654a8555b4dc035cfe29eb27442ab7",
        "text_resolutions": "1927d3376a64594b6c38142cf4d13703d72b21e2cfaac1251be0a8166a9e2b05",
        "voice_containers": "a83bd326e86f93b9e968cd05a803b1b92633b62d70b44cfacf903c9ab5f7a46d",
    },
    "selected_paths": list(SELECTED_PATHS),
    "snapshot_id": C5B_SNAPSHOT_ID,
}
_C5B_CONFIG = {
    "corpus_complete": True,
    "coverage": C5B_COVERAGE,
    "logical_identity_created": False,
    "manifest_raw_sha256": "18f03b59f01c69b6b093cbd6f936323ae05383f2082c4230934b2e19b63d8bd3",
    "mcp_capability_added": False,
    "occurrence_set_digests": _C5B_SCOPE["occurrence_set_digests"],
    "schema_contract": "schema10-migration0010-exact",
    "source_commit": C5B_COMMIT,
    "source_tree": C5B_TREE,
    "versions": dict(C5B_VERSIONS),
}

C5B_CONTRACT = CharacterRunContract(
    run_family="character_phase4c5b_full_source_closure",
    manifest_id=C5B_MANIFEST_ID,
    ingest_run_id="ir-character-phase4c5b:38849b74a42fdaf24ed8e1aff9bca5b070534d76",
    snapshot_id=C5B_SNAPSHOT_ID,
    parser_build_id="pb-character-phase4c5b:f93eab7a37f99387b6380aa7722f4fe248db1ece",
    config_checksum="9412eef8c28bd8bd063c6a761771445f23bdb8fa5b196a07dfd35bde3facf8c2",
    manifest_raw_sha256=_C5B_CONFIG["manifest_raw_sha256"],
    parser_version=C5B_VERSIONS["parser"],
    mapping_version=C5B_VERSIONS["projection"],
    parser_fingerprint="character-phase4c5b-full-boundary-projection",
    source_build=C5B_BUILD,
    source_commit=C5B_COMMIT,
    source_tree=C5B_TREE,
    boundary={
        "coverage": C5B_COVERAGE,
        "corpus_complete": True,
        "primary_scope": "all_rows_in_four_primary_files_in_fixed_snapshot",
        "support_scope": "all_primary_rows_explicit_owner_candidates_all_fetter_voice_physical_closure",
        "textmap_scope": "explicit_owner_candidates_only_from_six_selected_shards",
        "voice_scope": "all_fetter_lut_records_and_items_containers_in_four_selected_files",
    },
    scope=_C5B_SCOPE,
    config=_C5B_CONFIG,
    coverage=C5B_COVERAGE,
    corpus_complete=True,
)

# The order is part of the API contract: literal stable sort by run id.
CHARACTER_RUN_CONTRACTS = tuple(sorted((C2_CONTRACT, C4B_CONTRACT, C5B_CONTRACT), key=lambda item: item.ingest_run_id))
CHARACTER_RUN_CONTRACT_BY_ID = {item.ingest_run_id: item for item in CHARACTER_RUN_CONTRACTS}


def family_run_ids(conn: Any, run_family: str) -> list[str] | None:
    """Return same-family run IDs from metadata only (never occurrence tables)."""

    result: list[str] = []
    for row in conn.execute("SELECT id,scope_json FROM ingest_run ORDER BY id"):
        try:
            scope = json.loads(str(row["scope_json"]))
        except (TypeError, json.JSONDecodeError):
            return None
        if isinstance(scope, dict) and scope.get("family") == run_family:
            result.append(str(row["id"]))
    return result


def exact_schema_10(conn: Any) -> bool:
    """Check only schema signature; no Character table scan is performed."""

    conn.execute("PRAGMA schema_version").fetchone()
    try:
        versions = [int(row[0]) for row in conn.execute(
            "SELECT version FROM schema_migration ORDER BY version"
        )]
    except Exception:
        return False
    if versions != list(range(1, 11)):
        return False
    try:
        verify_character_phase4c2_schema_signature(conn)
    except (CharacterPhase4C2SchemaError, sqlite3.Error):
        # Preserve lightweight fail-closed behavior for malformed schema.
        return False
    return True


def exact_schema_10_or_11(conn: Any) -> bool:
    """Accept historical Schema 10/11 or exact combined Schema 12/13."""
    try:
        versions = [
            int(row[0])
            for row in conn.execute(
                "SELECT version FROM schema_migration ORDER BY version"
            )
        ]
    except (AttributeError, KeyError, TypeError, ValueError, sqlite3.Error):
        return False
    if versions == list(range(1, 11)):
        return exact_schema_10(conn)
    if versions == list(range(1, 12)):
        return exact_schema_11(conn)
    if versions in (
        list(range(1, 13)), list(range(1, 14)),
        list(range(1, 16)), list(range(1, 17)), list(range(1, 20)), list(range(1, 21)),
    ):
        try:
            # M4 adds only append-only batch tables after the reviewed
            # Character/Voice structure.  Validate the exact additive schema
            # signature before reusing the family contract.
            validate_exact_schema_structure(conn, versions[-1])
            verify_character_phase4c2_schema_signature(conn)
            return True
        except (CharacterPhase4C2SchemaError, sqlite3.Error, RuntimeError, ValueError):
            return False
    if versions == list(range(1, 18)):
        try:
            # Schema 17 is an additive wave schema.  Its family compatibility
            # must use the exact M5D ledger/migration-0017/structural gate,
            # never a generic Schema 16 prefix check.
            verify_schema17(conn)
            verify_character_phase4c2_schema_signature(conn)
            return True
        except (
            BatchM5DError,
            CharacterPhase4C2SchemaError,
            sqlite3.Error,
            RuntimeError,
            ValueError,
        ):
            return False
    return False


def row_matches_contract(
    row: Any,
    scope: Any,
    config: Any,
    snapshot_metadata: Any,
    contract: CharacterRunContract,
    same_family_ids: list[str] | None,
) -> bool:
    """Validate one exact binding using run/parser/snapshot metadata only."""

    source = contract.source
    return (
        row["id"] == contract.ingest_run_id
        and row["snapshot_id"] == contract.snapshot_id
        and row["parser_build_id"] == contract.parser_build_id
        and row["run_status"] == "complete"
        and scope == contract.scope
        and config == contract.config
        and row["parser_version"] == contract.parser_version
        and row["mapping_version"] == contract.mapping_version
        and row["fingerprint"] == contract.parser_fingerprint
        and row["config_checksum"] == contract.config_checksum
        and row["source_id"] == source["source_id"]
        and row["release_label"] == source["release_label"]
        and row["build"] == source["build"]
        and row["snapshot_commit"] == source["commit"]
        and row["snapshot_tree"] == source["tree"]
        and row["snapshot_status"] == "active"
        and row["observed_at"] == source["observed_at"]
        and snapshot_metadata == {"sealed_source_manifest": True}
        and row["source_kind"] == "primary"
        and row["source_url"] == source["upstream"]
        and row["source_license_note"] is None
        and row["parser_created_at"] == source["observed_at"]
        and row["run_created_at"] == source["observed_at"]
        and same_family_ids == [contract.ingest_run_id]
    )


__all__ = [
    "C2_CONTRACT",
    "C4B_CONTRACT",
    "C5B_CONTRACT",
    "CHARACTER_RUN_CONTRACTS",
    "CHARACTER_RUN_CONTRACT_BY_ID",
    "CharacterRunContract",
    "LOCALES",
    "SELECTED_PATHS",
    "exact_schema_10",
    "exact_schema_10_or_11",
    "family_run_ids",
    "row_matches_contract",
]
