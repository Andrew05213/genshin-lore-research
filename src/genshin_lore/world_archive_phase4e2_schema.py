"""Exact Schema 12 seal for the Phase 4E2 World Archive canary."""

from __future__ import annotations

import hashlib
import sqlite3


MIGRATION_0012_SHA256 = "b722dc2ca288c20cd65a784e60adff7b95f8417d55f17e9b14c094760f4f5717"
STRUCTURAL_SIGNATURES = {"fts5": "7c619e6492287da259ebebc56d682697b0c37899441aca57fa350bf533031e80", "fallback": "a9dd3b5141281178b5546089093d7727a9031da583dc62fc53503701c24d459e"}
TABLE_SQL = {
    "world_archive_asset_reference_occurrence": "528f1541ef5e226031b58ad4da4e91490abe4f7f9462e1729083557de907d555",
    "world_archive_opaque_occurrence": "e0592cdc91fa31e00ac3da06b8cf8b27b51114a914401e912dbdaf80641efe2f",
    "world_archive_raw_field_occurrence": "6b15b4e28036fc6dc40a7bac4b68295904b19ec6b16b9282ba9616f1525851e7",
    "world_archive_relationship_occurrence": "7525ca9d818f3a8a17ec578264087e516a0aa2d65b4eedf6e7af8bdc1610921b",
    "world_archive_row_occurrence": "4d14ddad8f9e1c4c2b62960b064e3e03b4b9ccc0c0d88c81f3b641663bb3eeb4",
    "world_archive_text_candidate_occurrence": "525c2c7c9b36978fe1ec1dee700db4b70d250a717cd42a9d1f18773d838aaf79",
    "world_archive_text_owner_occurrence": "d050c0c96c074e6d7c09db35b704dcf1ea7f6f460d83372f85ae9bf6b7e5457a",
    "world_archive_text_resolution_occurrence": "426f05d31c662a8aceff03badc24e96fb84654bb20cfbe46a44f2da7c65c1b18",
}
INDEX_SQL = {
    "idx_world_archive_asset_run_status": "65e6616a8e30fe4c6f2f34cc1296c16d6b9e72719b7188453fee9d14c57aa424",
    "idx_world_archive_candidate_run_locale": "87ff42697d77e5d4052ddd412a8f4fbd4bc9cce5fc8012e4796294178058da84",
    "idx_world_archive_opaque_run_field": "a98cea1ead570c40b0c228788e1b7dae607ab264c04561de2b53a04ed1ffb791",
    "idx_world_archive_owner_run_field": "5854d47aa54f053b77fad6d52b247e6c62e80087e555a3d79f70bf047b75c295",
    "idx_world_archive_raw_run_row": "20693639ee7424a1bce9953a12b02ae3f9e700ff4d1a4992977d267c3854671c",
    "idx_world_archive_relation_run_status": "6f06d3545af19e978a8ed2bfaba4d54ab9243cd390536c2665b3d5cdc3a7390a",
    "idx_world_archive_resolution_run_locale": "2e31d7a52c257481164a1a48eeba7a963feec48d4453e70f56617f2595d7a218",
    "idx_world_archive_row_run_table": "c2d6e834c3721dfd0c643bf0ab49c669db6db7a6b511f71aaf0618b2abd5ff9e",
    "uq_world_archive_ingest_run_scope": "c6577bf69a313c8811bdfe4f03ad539c88f21b91a418092db6c56a3c18492532",
    "uq_world_archive_source_file_snapshot": "405ca540136962d5ddf1e0518b507b43b5354a47d05053d858fc984c9e95d53a",
    "uq_world_archive_source_record_file_pointer": "2b4f1b1ed85a1cdf7f009bbb27a825d0ce71fa5df9f379e5ced4a0b2f447afbd",
}
TRIGGER_SQL = {
    "trg_world_archive_asset_delete_immutable": "ba7c31ba98dd0bd17c4d11e8626e571f9d4b25ee34e8c217b21b68fe8e3de86f",
    "trg_world_archive_asset_immutable": "e01f51ed1728dca7050807e84cbef29a8dbe9a3798f4d34421fbaabc708f5603",
    "trg_world_archive_candidate_delete_immutable": "cc800d26681a275af21bc4358d5c635c85e9ad36f1697ca1aa658d0c5358a121",
    "trg_world_archive_candidate_immutable": "dc2d1eae4e489606d2b95fccf530cb710abe5cdc75c9bf6f7440b94fb9c3d649",
    "trg_world_archive_opaque_delete_immutable": "7964a39f34718cd8cddea44df87465d08d8e94efa290ba1e2fe6011dad571441",
    "trg_world_archive_opaque_immutable": "3863ea99e8a16c2d8a1c2eabaa628cafd267b6183a0a7b20ec3577adc39c2198",
    "trg_world_archive_owner_delete_immutable": "d3096db6999cffef317897a983193a2babea7b06afe183df1c7cd37c62fd9bb5",
    "trg_world_archive_owner_immutable": "7824e72ad6af4ed5aa83f6a800712d1dfeb709be497685ce8194de5b166618be",
    "trg_world_archive_raw_delete_immutable": "ab5501357c6819bb3e24f9f971aead98feaf06f38fe2dd8b435a473d6601aeaa",
    "trg_world_archive_raw_immutable": "00ff1c8f16c469846aa2f6db364230e628e864c38b9afdf23a5d9caed120df38",
    "trg_world_archive_relation_delete_immutable": "6e038a53e3d4bedc3af5df26c50a24713f203151105e01cc6b4fdeb0e053d72b",
    "trg_world_archive_relation_immutable": "7775a1e1b9c19f7f44e94e3e3c13068bf8bcfa86bf10d204d936e397fb863beb",
    "trg_world_archive_resolution_delete_immutable": "4eee6541a6e8319f3bc80e13b58d35bf9cf6801be76ad686cc5210681958d1ac",
    "trg_world_archive_resolution_immutable": "7e4a0258187b1bc58776a7574c8235df0c97b0bc56340781a1ede7bbfed4ad1b",
    "trg_world_archive_row_delete_immutable": "e0ad75d737c5d06fd61d1fba0f19f4ec9641db860444e190d52af15e200495e0",
    "trg_world_archive_row_immutable": "8b0a583155989cd9ca9b82b044e774b900fb49e4bc6f11f9b55bbc2c2f0c27d4",
}


class WorldArchivePhase4E2SchemaError(RuntimeError):
    pass


def _digest(sql: str) -> str:
    return hashlib.sha256(" ".join(sql.split()).encode()).hexdigest()


def verify_schema_12(conn: sqlite3.Connection) -> dict[str, int]:
    from .db import Database
    versions = [int(row[0]) for row in conn.execute("SELECT version FROM schema_migration ORDER BY version")]
    if versions != list(range(1, 13)):
        raise WorldArchivePhase4E2SchemaError("Schema 12 ledger is not exact 1..12")
    for kind, expected in (("table", TABLE_SQL), ("index", INDEX_SQL), ("trigger", TRIGGER_SQL)):
        rows = {str(name): str(sql) for name, sql in conn.execute("SELECT name,sql FROM sqlite_master WHERE type=? AND sql IS NOT NULL", (kind,)) if str(name) in expected}
        if set(rows) != set(expected) or any(_digest(rows[name]) != digest for name, digest in expected.items()):
            raise WorldArchivePhase4E2SchemaError(f"World Archive Schema 12 {kind} seal mismatch")
    snapshot = Database._stage_structural_snapshot(conn)
    mode = str(snapshot["fts"]["mode"])
    if Database._stage_structural_signature(conn) != STRUCTURAL_SIGNATURES.get(mode):
        raise WorldArchivePhase4E2SchemaError("complete Schema 12 structural seal mismatch")
    return {"tables": len(TABLE_SQL), "indexes": len(INDEX_SQL), "immutable_triggers": len(TRIGGER_SQL)}
