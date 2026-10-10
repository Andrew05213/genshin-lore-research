"""Exact Schema 14 seal for the Phase 4G2 Avatar Codex canary."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path


MIGRATION_0014_SHA256 = "abc905d8fcb1d1e1507fa7a1cd4447748bcf42d5dc06454a1aaa1f62566f1e48"
STRUCTURAL_SIGNATURES = {
    "fts5": "2fa8614278301a8e1a0ea7aa411888fada82b18eda7d400a34064b10e11863a2",
    "fallback": "1004fb91834bdbfd868535dd7d4f9c847d1b1703092d178a7b7f9094dab83fa8",
}
TABLE_SQL = {
    "avatar_codex_opaque_occurrence": "a92521a040fd3135f69d1c78006ed3655d42dbbcf4197d2c7f9c708a329b350c",
    "avatar_codex_raw_field_occurrence": "4e015483db1f451407d707f7aa29d5aa40a9f39ece26069289c0ba1a73ec7137",
    "avatar_codex_relationship_candidate_occurrence": "0f594f8b71956065bbc3b314bd0bbcfe1a6a2d25c6582028f4a689ba166df451",
    "avatar_codex_relationship_occurrence": "d0e8f267d58463737331487ffccf4358297a551dbd23cb8033864da630dc4255",
    "avatar_codex_row_occurrence": "7227224e0bd4c61d6c68ab8b15ba99e352c0dd9a3805ccb06f5778bf5e151a94",
}
INDEX_SQL = {
    "idx_avatar_codex_opaque_run_field": "de07c6d18be5d09b41d2d6ac11f462a9b308f5d7b1ac5919e46cb8b91aca31f6",
    "idx_avatar_codex_raw_run_row": "79ffedb068cfaac606ce938e8155f5b564ef026c8f9d587c557f21554ba04cb4",
    "idx_avatar_codex_relation_candidate": "8eda2530143b3e11399fdc711b24811e29abcd8961e31e8ca1e6581af303f180",
    "idx_avatar_codex_relation_run_status": "d0b5979f2f0f2790978d8f5a8c686f96f3827ef31e3b9a76b97088e9addbd4ed",
    "idx_avatar_codex_row_run_row": "95193a5d22a6150b0c6fa6cd87a171241576ca254ab774bd637608f430cfe957",
}
TRIGGER_SQL = {
    "trg_avatar_codex_candidate_delete_immutable": "cf70586eac9bcd48ef2dd81189113c75989a8ba5e6adb835a48dc31a1c25a598",
    "trg_avatar_codex_candidate_immutable": "1850f98b752c461b9194fd18b203a3d55393127fe6ebfd2ffa482fa8fb4a8036",
    "trg_avatar_codex_candidate_insert_existing_immutable": "e51b9763b3814fea3191771def72f007529ccd4fabbd80fac1ac90fa05df7dd0",
    "trg_avatar_codex_opaque_delete_immutable": "b00a17c1c7c554a0e2d332211e4ba72a6b1a6417a2682560f5eb1c6933b42bcf",
    "trg_avatar_codex_opaque_immutable": "ce328b05c21569a127f4b251986b37881b430bdf3a7506c8d03a5bb3663b1bc9",
    "trg_avatar_codex_opaque_insert_existing_immutable": "9f184da20a199f8d08c923c22be07466d84cc0c9aa5426c5167ba50f57744d44",
    "trg_avatar_codex_raw_delete_immutable": "923ad8813a2ec194f14dcef211f8f6144292ade830b497a3aeeef71be69a2a63",
    "trg_avatar_codex_raw_immutable": "bda4bf330baafd9fc0c1b9f052b8af6675f629947490bb0a1c21cbb2ef2fe6e0",
    "trg_avatar_codex_raw_insert_existing_immutable": "a416e41a0b2b72fdb1e3b900773d7ec4bdc031d8511aa7e1b7901da445cde9fa",
    "trg_avatar_codex_relationship_delete_immutable": "1b8a5a5e23ebe842a7d0bfdca56c094035e94b9c902f6f0eb6687fc0e8fd130c",
    "trg_avatar_codex_relationship_immutable": "1e9948dcae0ec32b59b437ce8bdab441a57243266e191a6bfbca531dadbb446f",
    "trg_avatar_codex_relationship_insert_existing_immutable": "e3e3bc6ea7ec18af66abd6cc08800be89c63c0c65a40b810d5a53714846be6b8",
    "trg_avatar_codex_row_delete_immutable": "10f95bb8a988810de6dbeaa3973a2d84854a417f916722c67b851c9d15b204d5",
    "trg_avatar_codex_row_immutable": "cd9ef8452e4a66b2e510e168dce8d99551d8a4d943d667697209b91f105a1175",
    "trg_avatar_codex_row_insert_existing_immutable": "59a59e3d634db5b963a03a772cefd6cd627ccda3130d920c013b3960d5b90e67",
}


class AvatarCodexPhase4G2SchemaError(RuntimeError):
    """Schema 14 is not the exact reviewed migration result."""


def _digest(sql: str) -> str:
    return hashlib.sha256(" ".join(sql.split()).encode("utf-8")).hexdigest()


def verify_schema_14(conn: sqlite3.Connection) -> dict[str, int]:
    migration = Path(__file__).resolve().parents[2] / "migrations" / "0014_phase4g2_avatar_codex_canary_evidence.sql"
    try:
        raw = migration.read_bytes()
    except OSError as exc:
        raise AvatarCodexPhase4G2SchemaError(f"cannot read migration 0014: {exc}") from exc
    if hashlib.sha256(raw).hexdigest() != MIGRATION_0014_SHA256:
        raise AvatarCodexPhase4G2SchemaError("migration 0014 raw SQL seal mismatch")
    versions = [int(row[0]) for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")]
    if versions != list(range(1, 15)):
        raise AvatarCodexPhase4G2SchemaError("Schema 14 ledger is not exact 1..14")
    for kind, expected in (("table", TABLE_SQL), ("index", INDEX_SQL), ("trigger", TRIGGER_SQL)):
        rows = {
            str(name): str(sql)
            for name, sql in conn.execute("SELECT name,sql FROM sqlite_master WHERE type=? AND sql IS NOT NULL", (kind,))
            if str(name) in expected
        }
        if set(rows) != set(expected) or any(_digest(rows[name]) != digest for name, digest in expected.items()):
            raise AvatarCodexPhase4G2SchemaError(f"Avatar Codex Schema 14 {kind} seal mismatch")
    from .db import Database

    snapshot = Database._stage_structural_snapshot(conn)
    mode = str(snapshot["fts"]["mode"])
    if Database._stage_structural_signature(conn) != STRUCTURAL_SIGNATURES.get(mode):
        raise AvatarCodexPhase4G2SchemaError("complete Schema 14 structural seal mismatch")
    return {"tables": len(TABLE_SQL), "indexes": len(INDEX_SQL), "immutable_triggers": len(TRIGGER_SQL)}


__all__ = [
    "AvatarCodexPhase4G2SchemaError", "INDEX_SQL", "MIGRATION_0014_SHA256", "STRUCTURAL_SIGNATURES",
    "TABLE_SQL", "TRIGGER_SQL", "verify_schema_14",
]
