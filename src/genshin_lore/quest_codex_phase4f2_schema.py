"""Exact Schema 13 seal for the Phase 4F2 QuestCodex canary."""
from __future__ import annotations
import hashlib, sqlite3
from pathlib import Path

MIGRATION_0013_SHA256="605b91f96b76b446774c4036a7438e565b1d9ee7152a6137398605b3c36011b6"
STRUCTURAL_SIGNATURES={"fts5":"60e18d79efdaaf30be3ba7781644f01c9e200c1bb5aa06d4f482906820a0725a","fallback":"b21b57467dcb8afac325e1a43e149aaec8379585a5e18eb40f0d0f4d83cc1a06"}
TABLE_SQL={
"quest_codex_asset_reference_occurrence":"3cbeb3b31d0178ac2cdae7998adea678fc3ca82946d88884d902af1a8fa27267",
"quest_codex_opaque_occurrence":"fa0a2ed70032048abd102842a9e453c4496ed1a3411388cb44e7ac9503b55f48",
"quest_codex_raw_field_occurrence":"78ec5bd742784e336dfd0b901e641301c3c824af83367a9e97e538960dabdd9b",
"quest_codex_relationship_candidate_occurrence":"692fad491e56c56971d3eee0f18d92feb68e4face01dafc96581dd43da8d862d",
"quest_codex_relationship_occurrence":"e4ef67b775efcb66d538407fcba111b4f1606400923f8954e8a8d6489bd4ab13",
"quest_codex_row_occurrence":"f06ecd20b2b7c83bde2b38d539dfc1f1e972002c3ab75953e7cb14d51f33907b",
"quest_codex_text_candidate_occurrence":"52f852028c957f3faa0317ce3369c8c040d2ec6925dad1e6be7edf0caa90be3f",
"quest_codex_text_owner_occurrence":"73fb5c15156ae6c9a9d34b76b4155b0ae58a559c60270a3a1b7597bb1439dc1e",
"quest_codex_text_resolution_occurrence":"9a1f5bb9094a44e6886792a8fad9c26e03997f445a6c99f42d303047af735da4"}
INDEX_SQL={
"idx_quest_codex_asset_run_status":"48fe0e26326876410e2a7161692a9f15e8ea1a30d3746e3a31b1b88bb42d28c1","idx_quest_codex_opaque_run_field":"442b1e1dda8f85dcb06f23b9c89e8f345a088de07c468ea50df122f95575d17d","idx_quest_codex_owner_run_field":"d6e1c6ecf9e7a89bc33cdd5cc457f80a5e05fe35e79d6bf586d3f3837f621107","idx_quest_codex_raw_run_row":"005263ecb22264b839ac06e5af0eb09db902fe8c4d8615d855bff766958ae268","idx_quest_codex_relation_candidate":"31623ffd9dad1dbe4130aa34487350020f30f9b3e6872610728589067d4a9ab9","idx_quest_codex_relation_run_status":"a75fabd97caf0ef4856a5cf014c55309a732a378b3f2dc5107b5b19e440d3831","idx_quest_codex_resolution_run_locale":"bcd79310b77919175113b3f99cdd552218960d3d5aca52537a4afdeae9b38e66","idx_quest_codex_row_run_kind":"e8edd546251b6aa121796af69750e2f7709aead9970e0d0dd24bd53f23f6b7c3","idx_quest_codex_text_candidate_run_locale":"65aab93e6ffec3f1abb83c32ba900ca6c9735b61920aad636fe6482217f126f4"}
TRIGGER_SQL={
"trg_quest_codex_asset_delete_immutable":"811fe03bb985aba91daaf88997d7702f01635c2ad43f5bf74c98765ee876d18f","trg_quest_codex_asset_immutable":"f54064380b2717f423a651b8785f875ebc205efd5b3a63fda6fba396ded7d027","trg_quest_codex_opaque_delete_immutable":"3fdf1ace0917ef8683bbd4856bf940a65d6e86396b5049ab5640484a82d44208","trg_quest_codex_opaque_immutable":"e909b5d45013a87928e88692a895367e4957f132c76c1d02312aa58ca609b2cd","trg_quest_codex_owner_delete_immutable":"d37ef5afa2cd4485e4e4d4eb4dac8598b6212835e7220cb6ed978650d50af930","trg_quest_codex_owner_immutable":"89bfc02cc49dd794b5775ab0262ae89b76216743eb337a50a09aaeed1265b64f","trg_quest_codex_raw_delete_immutable":"ace7c7f6f6c2e7187f04741ca3fbcd02f1e36b05eb2c0993d03ebaaee88cc487","trg_quest_codex_raw_immutable":"eed49b7c0be3dac9b75d772a8ad43b9c9f6ee9d754d54f6107eeda003a174a16","trg_quest_codex_relation_candidate_delete_immutable":"767be7c38862d1e22df113e4c59e60b520a54c3c5e1e0f64c614744325e19a92","trg_quest_codex_relation_candidate_immutable":"28a7b5ee056187d069b34b18ede0d2d4370cd038c369de30dc587280aeb91757","trg_quest_codex_relation_delete_immutable":"8c33d332789026897dc62a50e28f77daf06a4da8aecf3c5615fb9ca14183ef19","trg_quest_codex_relation_immutable":"2835fb7242916299f2adfa6ab15a59aa4165ffd8bb1481eed6a80ede5614dd5a","trg_quest_codex_resolution_delete_immutable":"76786ff1f9783db1a1829da09dc617bd447005a943385c7078c18b577986de6f","trg_quest_codex_resolution_immutable":"ba8a73b03673407b3312a9852d45f010a8f51f5a19a1cd4693003ce4eca5553f","trg_quest_codex_row_delete_immutable":"c6370cbe0641b5f28614a1575b7d76c9deff59af95045820e9e80c9841c48bb2","trg_quest_codex_row_immutable":"09246253e203badc336ca4186082f0b6aa0ac20848338e6ee7a35ab22b611b78","trg_quest_codex_text_candidate_delete_immutable":"311e1a667844e3e6cc6021d637e32b45b1a57cf4fc07c3ead2a7701471651e21","trg_quest_codex_text_candidate_immutable":"7f50c9299d4dbb14d04c7d04ab9f824d712b0abc8fb8b48caa0db3f369542354"}

class QuestCodexPhase4F2SchemaError(RuntimeError): pass
def _digest(sql:str)->str: return hashlib.sha256(" ".join(sql.split()).encode()).hexdigest()
def verify_schema_13(conn:sqlite3.Connection)->dict[str,int]:
    migration=Path(__file__).resolve().parents[2]/"migrations/0013_phase4f2_quest_codex_canary_evidence.sql"
    try: raw=migration.read_bytes()
    except OSError as exc: raise QuestCodexPhase4F2SchemaError(f"cannot read migration 0013: {exc}") from exc
    if hashlib.sha256(raw).hexdigest()!=MIGRATION_0013_SHA256: raise QuestCodexPhase4F2SchemaError("migration 0013 raw SQL seal mismatch")
    from .db import Database
    versions=[int(x[0]) for x in conn.execute("SELECT version FROM schema_migration ORDER BY version")]
    if versions!=list(range(1,14)): raise QuestCodexPhase4F2SchemaError("Schema 13 ledger is not exact 1..13")
    for kind,expected in (("table",TABLE_SQL),("index",INDEX_SQL),("trigger",TRIGGER_SQL)):
        rows={str(n):str(s) for n,s in conn.execute("SELECT name,sql FROM sqlite_master WHERE type=? AND sql IS NOT NULL",(kind,)) if str(n) in expected}
        if set(rows)!=set(expected) or any(_digest(rows[name])!=digest for name,digest in expected.items()): raise QuestCodexPhase4F2SchemaError(f"QuestCodex Schema 13 {kind} seal mismatch")
    snapshot=Database._stage_structural_snapshot(conn); mode=str(snapshot["fts"]["mode"])
    if Database._stage_structural_signature(conn)!=STRUCTURAL_SIGNATURES.get(mode): raise QuestCodexPhase4F2SchemaError("complete Schema 13 structural seal mismatch")
    return {"tables":len(TABLE_SQL),"indexes":len(INDEX_SQL),"immutable_triggers":len(TRIGGER_SQL)}
