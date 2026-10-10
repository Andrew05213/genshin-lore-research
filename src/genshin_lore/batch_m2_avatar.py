"""Avatar Codex family adapter for the Batch M2 full 120-row pilot.

The adapter is intentionally independent from the Schema 14 canary builder.
It re-reads the pinned source and the accepted Phase 4G1 audit boundary, then
emits a non-product family shard with all selected raw fields, lookup
candidates, and opaque observations.  ``avatarId`` is retained as a
table-scoped locator; it is never converted into a Character/Avatar identity.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from .family_projection_protocol import FamilyShardSpec, TableSpec
from .util import canonical_json, digest_id, sha256_bytes


FAMILY = "avatar_codex_archive"
FAMILY_CONTRACT_VERSION = "avatar-codex-archive-evidence-v1"
SOURCE_COMMIT = "26df1dfbdf05a82bbb1d97506859f3e1c40718d8"
SOURCE_TREE = "d891ef1d95fdd6c27a1da8a32ea7a61b483647a6"
SNAPSHOT_ID = f"anime-game-data:7.0:{SOURCE_COMMIT}"
SOURCE_FILE = "ExcelBinOutput/AvatarCodexExcelConfigData.json"
LOOKUP_FILE = "ExcelBinOutput/AvatarExcelConfigData.json"
AUDIT_MANIFEST = "reports/phase4g1-avatar-codex/phase4g1-audit-manifest.json"
EXPECTED_AUDIT_MANIFEST_SHA256 = "22635b39fd2b96e4dffd0846fe6cb97ddcf55149ff0fdd19a08c699fe417b03e"
PARSER_BUILD_ID = f"pb-avatar-codex-batch-m2:{SOURCE_COMMIT}"
INGEST_RUN_ID = f"ir-avatar-codex-batch-m2:{SOURCE_COMMIT}"
PRIMARY_FIELDS = ("avatarId", "beginTime", "sortFactor", "sortId", "hideWhenDontHave", "FMDEJPDNFKB")
OPAQUE_FIELD = "FMDEJPDNFKB"


class AvatarBatchM2Error(RuntimeError):
    pass


def _strict_json(raw: bytes, label: str) -> Any:
    def reject(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise AvatarBatchM2Error(f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=reject)
    except AvatarBatchM2Error:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AvatarBatchM2Error(f"invalid JSON in {label}: {exc}") from exc


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AvatarBatchM2Error(f"source git inspection failed: {exc}") from exc
    if result.returncode:
        raise AvatarBatchM2Error(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def _raw(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _source_identity(root: Path, repo_root: Path) -> dict[str, Any]:
    manifest_path = repo_root / AUDIT_MANIFEST
    raw_manifest = manifest_path.read_bytes()
    if sha256_bytes(raw_manifest) != EXPECTED_AUDIT_MANIFEST_SHA256:
        raise AvatarBatchM2Error("Phase 4G1 audit manifest seal mismatch")
    manifest = _strict_json(raw_manifest, AUDIT_MANIFEST)
    if not isinstance(manifest, dict):
        raise AvatarBatchM2Error("Phase 4G1 audit manifest is not an object")
    if (
        manifest.get("commit") != SOURCE_COMMIT
        or manifest.get("tree") != SOURCE_TREE
        or manifest.get("snapshot_id") != SNAPSHOT_ID
        or manifest.get("candidate_stage") != "audit_only"
        or manifest.get("summary", {}).get("selected_primary_rows") != 120
    ):
        raise AvatarBatchM2Error("Phase 4G1 selected boundary is not the frozen 120-row contract")
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise AvatarBatchM2Error("AnimeGameData source checkout is not clean")
    if _git(root, "rev-parse", "HEAD") != SOURCE_COMMIT or _git(root, "rev-parse", "HEAD^{tree}") != SOURCE_TREE:
        raise AvatarBatchM2Error("AnimeGameData source pin mismatch")
    return manifest


def _load_file(root: Path, relative: str, manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    path = root / relative
    if not path.is_file() or path.is_symlink():
        raise AvatarBatchM2Error(f"required source file missing: {relative}")
    raw = path.read_bytes()
    inventory = None
    # The audit manifest is the boundary authority; its inventory artifact
    # records exact bytes.  Read it rather than hard-coding a second seal.
    inventory_artifact = next(
        (
            item for item in manifest.get("artifacts", [])
            if item.get("audit_kind") == "phase4g1_avatar_codex_source_inventory"
        ),
        None,
    )
    if inventory_artifact is None:
        raise AvatarBatchM2Error("Phase 4G1 source inventory artifact is not declared")
    inventory_path = inventory_artifact["relative_path"]
    inventory_raw = (Path(__file__).resolve().parents[2] / inventory_path).read_bytes()
    if sha256_bytes(inventory_raw) != inventory_artifact.get("sha256"):
        raise AvatarBatchM2Error("Phase 4G1 source inventory artifact seal mismatch")
    # The source-inventory report is tracked and sealed by the manifest.  Its
    # compact file entry is sufficient for this adapter's byte preflight.
    inv = _strict_json(inventory_raw, inventory_path)
    for item in inv.get("files", []):
        if item.get("relative_path") == relative:
            inventory = item
            break
    if inventory is None or len(raw) != inventory["size_bytes"] or sha256_bytes(raw) != inventory["sha256"]:
        raise AvatarBatchM2Error(f"source file seal mismatch: {relative}")
    value = _strict_json(raw, relative)
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise AvatarBatchM2Error(f"source file row shape mismatch: {relative}")
    return value, inventory["sha256"]


def _table_ddl() -> tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...]:
    return (
        (
            "avatar_codex_row_occurrence",
            """CREATE TABLE avatar_codex_row_occurrence (
                id TEXT PRIMARY KEY NOT NULL,
                ingest_run_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                parser_build_id TEXT NOT NULL,
                source_file_id TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                source_pointer TEXT NOT NULL,
                row_index INTEGER NOT NULL,
                native_locator_field TEXT NOT NULL,
                native_locator_value TEXT NOT NULL,
                raw_checksum TEXT NOT NULL,
                raw_json TEXT NOT NULL CHECK (json_valid(raw_json)),
                metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
                UNIQUE (id, ingest_run_id, snapshot_id, parser_build_id)
            )""",
            ("id", "ingest_run_id", "snapshot_id", "parser_build_id", "source_file_id", "source_record_id", "source_pointer", "row_index", "native_locator_field", "native_locator_value", "raw_checksum", "raw_json", "metadata_json"),
            ("row_index", "source_pointer", "id"),
        ),
        (
            "avatar_codex_raw_field_occurrence",
            """CREATE TABLE avatar_codex_raw_field_occurrence (
                id TEXT PRIMARY KEY NOT NULL,
                ingest_run_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                parser_build_id TEXT NOT NULL,
                row_occurrence_id TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                source_pointer TEXT NOT NULL,
                field_name TEXT NOT NULL,
                raw_json TEXT NOT NULL CHECK (json_valid(raw_json)),
                resolution_status TEXT NOT NULL,
                metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
                FOREIGN KEY (row_occurrence_id, ingest_run_id, snapshot_id, parser_build_id)
                    REFERENCES avatar_codex_row_occurrence (id, ingest_run_id, snapshot_id, parser_build_id)
            )""",
            ("id", "ingest_run_id", "snapshot_id", "parser_build_id", "row_occurrence_id", "source_record_id", "source_pointer", "field_name", "raw_json", "resolution_status", "metadata_json"),
            ("row_occurrence_id", "source_pointer", "id"),
        ),
        (
            "avatar_codex_relationship_occurrence",
            """CREATE TABLE avatar_codex_relationship_occurrence (
                id TEXT PRIMARY KEY NOT NULL,
                ingest_run_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                parser_build_id TEXT NOT NULL,
                source_row_occurrence_id TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                source_pointer TEXT NOT NULL,
                relation_kind TEXT NOT NULL,
                target_family TEXT NOT NULL,
                target_key_json TEXT NOT NULL CHECK (json_valid(target_key_json)),
                resolution_status TEXT NOT NULL,
                candidate_count INTEGER NOT NULL,
                metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
                FOREIGN KEY (source_row_occurrence_id, ingest_run_id, snapshot_id, parser_build_id)
                    REFERENCES avatar_codex_row_occurrence (id, ingest_run_id, snapshot_id, parser_build_id),
                UNIQUE (id, ingest_run_id, snapshot_id, parser_build_id)
            )""",
            ("id", "ingest_run_id", "snapshot_id", "parser_build_id", "source_row_occurrence_id", "source_record_id", "source_pointer", "relation_kind", "target_family", "target_key_json", "resolution_status", "candidate_count", "metadata_json"),
            ("source_row_occurrence_id", "source_pointer", "id"),
        ),
        (
            "avatar_codex_relationship_candidate_occurrence",
            """CREATE TABLE avatar_codex_relationship_candidate_occurrence (
                id TEXT PRIMARY KEY NOT NULL,
                ingest_run_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                parser_build_id TEXT NOT NULL,
                relationship_occurrence_id TEXT NOT NULL,
                source_file_id TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                source_pointer TEXT NOT NULL,
                candidate_ordinal INTEGER NOT NULL,
                target_table TEXT NOT NULL,
                target_field TEXT NOT NULL,
                target_key TEXT NOT NULL,
                raw_checksum TEXT NOT NULL,
                metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
                FOREIGN KEY (relationship_occurrence_id, ingest_run_id, snapshot_id, parser_build_id)
                    REFERENCES avatar_codex_relationship_occurrence (id, ingest_run_id, snapshot_id, parser_build_id)
            )""",
            ("id", "ingest_run_id", "snapshot_id", "parser_build_id", "relationship_occurrence_id", "source_file_id", "source_record_id", "source_pointer", "candidate_ordinal", "target_table", "target_field", "target_key", "raw_checksum", "metadata_json"),
            ("relationship_occurrence_id", "candidate_ordinal", "id"),
        ),
        (
            "avatar_codex_opaque_occurrence",
            """CREATE TABLE avatar_codex_opaque_occurrence (
                id TEXT PRIMARY KEY NOT NULL,
                ingest_run_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                parser_build_id TEXT NOT NULL,
                row_occurrence_id TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                source_pointer TEXT NOT NULL,
                field_name TEXT NOT NULL,
                raw_json TEXT NOT NULL CHECK (json_valid(raw_json)),
                observation_status TEXT NOT NULL,
                metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
                FOREIGN KEY (row_occurrence_id, ingest_run_id, snapshot_id, parser_build_id)
                    REFERENCES avatar_codex_row_occurrence (id, ingest_run_id, snapshot_id, parser_build_id)
            )""",
            ("id", "ingest_run_id", "snapshot_id", "parser_build_id", "row_occurrence_id", "source_record_id", "source_pointer", "field_name", "raw_json", "observation_status", "metadata_json"),
            ("row_occurrence_id", "source_pointer", "id"),
        ),
    )


def load_avatar_codex_spec(source_root: str | Path, repo_root: str | Path) -> FamilyShardSpec:
    """Reconstruct the frozen G1 boundary and return an R1 non-product spec."""

    root = Path(source_root).resolve()
    repo = Path(repo_root).resolve()
    manifest = _source_identity(root, repo)
    primary, primary_sha = _load_file(root, SOURCE_FILE, manifest)
    lookup, lookup_sha = _load_file(root, LOOKUP_FILE, manifest)
    if len(primary) != 120 or len(lookup) != 165:
        raise AvatarBatchM2Error("Avatar Codex primary/lookup denominator changed")

    primary_file_id = digest_id("source-file", SNAPSHOT_ID, SOURCE_FILE)
    lookup_file_id = digest_id("source-file", SNAPSHOT_ID, LOOKUP_FILE)
    lookup_by_id: dict[str, list[tuple[int, dict[str, Any], str, str]]] = {}
    for index, row in enumerate(lookup):
        pointer = f"/{index}"
        record_id = digest_id("source-record", lookup_file_id, pointer)
        native = row.get("id")
        if isinstance(native, bool) or native is None:
            continue
        lookup_by_id.setdefault(str(native), []).append((index, row, record_id, _sha(row)))

    row_rows: list[tuple[Any, ...]] = []
    raw_rows: list[tuple[Any, ...]] = []
    relation_rows: list[tuple[Any, ...]] = []
    candidate_rows: list[tuple[Any, ...]] = []
    opaque_rows: list[tuple[Any, ...]] = []
    for index, row in enumerate(primary):
        pointer = f"/{index}"
        source_record_id = digest_id("source-record", primary_file_id, pointer)
        row_id = digest_id("avatar-codex-row", INGEST_RUN_ID, primary_file_id, pointer)
        raw_checksum = _sha(row)
        row_rows.append((
            row_id, INGEST_RUN_ID, SNAPSHOT_ID, PARSER_BUILD_ID, primary_file_id,
            source_record_id, pointer, index, "avatarId", str(row.get("avatarId")),
            raw_checksum, _raw(row), json.dumps({"table": "AvatarCodexExcelConfigData", "role": "primary"}, sort_keys=True),
        ))
        for field_name, value in row.items():
            field_pointer = f"/{index}/{field_name.replace('~', '~0').replace('/', '~1')}"
            field_id = digest_id("avatar-codex-raw-field", INGEST_RUN_ID, source_record_id, field_pointer)
            raw_rows.append((
                field_id, INGEST_RUN_ID, SNAPSHOT_ID, PARSER_BUILD_ID, row_id,
                source_record_id, field_pointer, field_name, _raw(value),
                "opaque_unresolved" if field_name == OPAQUE_FIELD else "raw_observed",
                json.dumps({"value_type": type(value).__name__}, sort_keys=True),
            ))
            if field_name == OPAQUE_FIELD:
                opaque_id = digest_id("avatar-codex-opaque", INGEST_RUN_ID, source_record_id, field_pointer)
                opaque_rows.append((
                    opaque_id, INGEST_RUN_ID, SNAPSHOT_ID, PARSER_BUILD_ID, row_id,
                    source_record_id, field_pointer, field_name, _raw(value),
                    "opaque_unresolved", json.dumps({"identity_effect": "none"}, sort_keys=True),
                ))

        native = str(row.get("avatarId"))
        candidates = lookup_by_id.get(native, [])
        relation_pointer = f"/{index}/avatarId"
        relation_id = digest_id("avatar-codex-relation", INGEST_RUN_ID, source_record_id, relation_pointer)
        relation_rows.append((
            relation_id, INGEST_RUN_ID, SNAPSHOT_ID, PARSER_BUILD_ID, row_id,
            source_record_id, relation_pointer, "avatar_codex_avatar_id_lookup",
            "AvatarExcelConfigData", json.dumps({"field": "id", "value": native}, sort_keys=True),
            "resolved_unique" if len(candidates) == 1 else ("ambiguous" if candidates else "missing"),
            len(candidates), json.dumps({"identity_effect": "candidate_scope_only_no_logical_merge"}, sort_keys=True),
        ))
        for ordinal, (target_index, target_row, target_record_id, target_checksum) in enumerate(candidates):
            candidate_id = digest_id("avatar-codex-relation-candidate", INGEST_RUN_ID, relation_id, ordinal)
            target_pointer = f"/{target_index}"
            candidate_rows.append((
                candidate_id, INGEST_RUN_ID, SNAPSHOT_ID, PARSER_BUILD_ID, relation_id,
                lookup_file_id, target_record_id, target_pointer, ordinal,
                "AvatarExcelConfigData", "id", native, target_checksum,
                json.dumps({"raw_record_locator": target_pointer, "native_value": target_row.get("id")}, sort_keys=True),
            ))

    # Parent tables are inserted before their family-owned children.  The
    # order is explicit family contract data; the shared protocol never
    # infers relationship semantics or attempts a cross-family topological
    # sort.
    load_order = {
        "avatar_codex_row_occurrence": 0,
        "avatar_codex_relationship_occurrence": 1,
        "avatar_codex_raw_field_occurrence": 2,
        "avatar_codex_relationship_candidate_occurrence": 3,
        "avatar_codex_opaque_occurrence": 4,
    }
    table_defs = tuple(sorted(_table_ddl(), key=lambda item: load_order[item[0]]))
    tables: list[TableSpec] = []
    values_by_name = {
        "avatar_codex_row_occurrence": row_rows,
        "avatar_codex_raw_field_occurrence": raw_rows,
        "avatar_codex_relationship_occurrence": relation_rows,
        "avatar_codex_relationship_candidate_occurrence": candidate_rows,
        "avatar_codex_opaque_occurrence": opaque_rows,
    }
    sort_indexes = {
        "avatar_codex_row_occurrence": (7, 6, 0),
        "avatar_codex_raw_field_occurrence": (4, 6, 0),
        "avatar_codex_relationship_occurrence": (4, 6, 0),
        "avatar_codex_relationship_candidate_occurrence": (4, 8, 6, 0),
        "avatar_codex_opaque_occurrence": (4, 6, 0),
    }
    for name, ddl, columns, order_columns in table_defs:
        indexes = sort_indexes[name]
        tables.append(
            TableSpec(
                name=name,
                create_sql=ddl,
                columns=columns,
                rows=tuple(values_by_name[name]),
                order_columns=order_columns,
                sort_key=lambda row, indexes=indexes: tuple(
                    (int(row[index]) if isinstance(row[index], int) else str(row[index]))
                    for index in indexes
                ),
                index_sql=(f"CREATE INDEX idx_m2_{name}_physical ON {name} ({', '.join(order_columns)})",),
            )
        )
    return FamilyShardSpec(
        family=FAMILY,
        family_contract_version=FAMILY_CONTRACT_VERSION,
        ingest_run_id=INGEST_RUN_ID,
        snapshot_id=SNAPSHOT_ID,
        parser_build_id=PARSER_BUILD_ID,
        coverage_level="R1_structured_family",
        corpus_complete=False,
        product_accepted=False,
        read_set=(SOURCE_FILE, LOOKUP_FILE, AUDIT_MANIFEST),
        write_set=tuple(table.name for table in tables),
        tables=tuple(tables),
        metadata={
            "boundary": "G1 frozen AvatarCodexExcelConfigData 120 primary rows",
            "lookup_inventory_rows": len(lookup),
            "primary_rows": len(primary),
            "raw_field_occurrences": len(raw_rows),
            "relationship_occurrences": len(relation_rows),
            "relationship_candidate_occurrences": len(candidate_rows),
            "opaque_occurrences": len(opaque_rows),
            "source_file_sha256": primary_sha,
            "lookup_file_sha256": lookup_sha,
            "g2_canary_imported": False,
            "logical_identity_created": False,
            "textmap_or_asset_projection": False,
        },
    )


__all__ = [
    "AvatarBatchM2Error",
    "FAMILY",
    "FAMILY_CONTRACT_VERSION",
    "INGEST_RUN_ID",
    "PARSER_BUILD_ID",
    "SNAPSHOT_ID",
    "load_avatar_codex_spec",
]
