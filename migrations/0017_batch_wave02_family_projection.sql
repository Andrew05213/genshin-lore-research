-- Schema 17 Batch Wave02 family-specific R1 evidence shape.
--
-- Avatar Codex deliberately has no CREATE TABLE statements here: its
-- existing Schema 14 tables (migration 0014) are reused by the M5C shard.
-- The remaining families each own a distinct table namespace.
-- Optional relation/text/asset shapes are declared only when the frozen
-- family contract projects rows for that shape; zero-row child tables are
-- omitted rather than manufactured. No universal identity is introduced.
PRAGMA foreign_keys = ON;

-- M5D bridge for the already accepted Schema16 Avatar Codex namespace.
--
-- Schema16's bounded Avatar canary tables are empty in the accepted M4
-- candidate. The M5C authoritative shard froze the later family-specific
-- raw-field status column; the accepted opaque table already carries the
-- nullable array-index coordinate and the bridge preserves it explicitly
-- during import. Extend only the existing raw-field table; no Avatar archive
-- namespace or universal table is created. The builder and independent
-- verifier prove the empty-table precondition before applying this migration
-- and before importing the M5C rows.
ALTER TABLE avatar_codex_raw_field_occurrence
    ADD COLUMN resolution_status TEXT NOT NULL DEFAULT 'raw_observed'
    CHECK (resolution_status IN ('raw_observed','opaque_unresolved'));

-- cook_recipe_archive: row, raw, relation, candidate, owner, resolution, text, opaque, asset
CREATE TABLE cook_recipe_archive_row_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, family TEXT NOT NULL,
 family_contract_version TEXT NOT NULL, ingest_run_id TEXT NOT NULL,
 snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_file_path TEXT NOT NULL, source_file_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_record_pointer TEXT NOT NULL,
 source_r0_physical_pointer TEXT NOT NULL, source_ordinal INTEGER NOT NULL CHECK(source_ordinal>=0),
 raw_record_json TEXT NOT NULL, raw_record_sha256 TEXT NOT NULL CHECK(length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_record_lexical_sha256 TEXT NOT NULL CHECK(length(raw_record_lexical_sha256)=64 AND raw_record_lexical_sha256 NOT GLOB '*[^0-9a-f]*'),
 native_identity_json TEXT NOT NULL CHECK(json_valid(native_identity_json)),
 record_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json))
);
CREATE TABLE cook_recipe_archive_raw_field_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 field_presence TEXT NOT NULL CHECK(field_presence IN ('present','missing')),
 raw_json TEXT, resolution_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 CHECK((field_presence='present' AND raw_json IS NOT NULL) OR (field_presence='missing' AND raw_json IS NULL)),
 FOREIGN KEY(row_occurrence_id) REFERENCES cook_recipe_archive_row_occurrence(occurrence_id)
);
CREATE TABLE cook_recipe_archive_relationship_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, relation_field TEXT NOT NULL,
 raw_value_json TEXT, target_source_file TEXT NOT NULL, target_field TEXT NOT NULL,
 condition_json TEXT NOT NULL CHECK(json_valid(condition_json)),
 resolution_status TEXT NOT NULL CHECK(resolution_status IN ('missing_field','empty_slot','missing_target','dangling','ambiguous','resolved_unique','resolved_candidate_set','unresolved','opaque_unresolved','not_applicable')),
 candidate_count INTEGER NOT NULL CHECK(candidate_count>=0), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES cook_recipe_archive_row_occurrence(occurrence_id)
);
CREATE TABLE cook_recipe_archive_relationship_candidate_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, relationship_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_file_id TEXT NOT NULL, source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL,
 candidate_ordinal INTEGER NOT NULL CHECK(candidate_ordinal>=0), target_source_file TEXT NOT NULL,
 target_source_row_ordinal INTEGER NOT NULL CHECK(target_source_row_ordinal>=0), target_pointer TEXT NOT NULL,
 target_field TEXT NOT NULL, target_value_json TEXT NOT NULL CHECK(json_valid(target_value_json)),
 target_raw_record_sha256 TEXT NOT NULL CHECK(length(target_raw_record_sha256)=64 AND target_raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 target_lexical_sha256 TEXT NOT NULL CHECK(length(target_lexical_sha256)=64 AND target_lexical_sha256 NOT GLOB '*[^0-9a-f]*'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(relationship_occurrence_id) REFERENCES cook_recipe_archive_relationship_occurrence(occurrence_id)
);
CREATE TABLE cook_recipe_archive_text_owner_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 owner_hash_json TEXT NOT NULL CHECK(json_valid(owner_hash_json)), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES cook_recipe_archive_row_occurrence(occurrence_id)
);
CREATE TABLE cook_recipe_archive_text_resolution_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, owner_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')), resolution_status TEXT NOT NULL CHECK(resolution_status IN ('missing','resolved_unique','ambiguous')),
 candidate_count INTEGER NOT NULL CHECK(candidate_count>=0), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(owner_occurrence_id) REFERENCES cook_recipe_archive_text_owner_occurrence(occurrence_id)
);
CREATE TABLE cook_recipe_archive_text_candidate_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, resolution_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 relative_path TEXT NOT NULL, locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')),
 shard_tier TEXT NOT NULL CHECK(shard_tier IN ('main','medium')), pair_ordinal INTEGER NOT NULL CHECK(pair_ordinal>=0),
 pointer TEXT NOT NULL, key_text TEXT NOT NULL, raw_text TEXT NOT NULL,
 raw_key_sha256 TEXT NOT NULL CHECK(length(raw_key_sha256)=64 AND raw_key_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_text_sha256 TEXT NOT NULL CHECK(length(raw_text_sha256)=64 AND raw_text_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_record_sha256 TEXT NOT NULL CHECK(length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(resolution_occurrence_id) REFERENCES cook_recipe_archive_text_resolution_occurrence(occurrence_id)
);
CREATE TABLE cook_recipe_archive_opaque_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 raw_json TEXT NOT NULL CHECK(json_valid(raw_json)), resolution_status TEXT NOT NULL CHECK(resolution_status='opaque_unresolved'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES cook_recipe_archive_row_occurrence(occurrence_id)
);
CREATE TABLE cook_recipe_archive_asset_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 raw_json TEXT, resolution_status TEXT NOT NULL CHECK(resolution_status IN ('raw_observed','missing_field','empty_slot')),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES cook_recipe_archive_row_occurrence(occurrence_id)
);
CREATE INDEX idx_m5c_cook_recipe_archive_row ON cook_recipe_archive_row_occurrence (source_file_path, source_ordinal, source_record_pointer, occurrence_id);
CREATE INDEX idx_m5c_cook_recipe_archive_raw ON cook_recipe_archive_raw_field_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_cook_recipe_archive_relation ON cook_recipe_archive_relationship_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_cook_recipe_archive_candidate ON cook_recipe_archive_relationship_candidate_occurrence (relationship_occurrence_id, candidate_ordinal, occurrence_id);
CREATE INDEX idx_m5c_cook_recipe_archive_owner ON cook_recipe_archive_text_owner_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_cook_recipe_archive_resolution ON cook_recipe_archive_text_resolution_occurrence (owner_occurrence_id, locale, occurrence_id);
CREATE INDEX idx_m5c_cook_recipe_archive_text ON cook_recipe_archive_text_candidate_occurrence (relative_path, pair_ordinal, occurrence_id);
CREATE INDEX idx_m5c_cook_recipe_archive_opaque ON cook_recipe_archive_opaque_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_cook_recipe_archive_asset ON cook_recipe_archive_asset_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE TRIGGER trg_cook_recipe_archive_row_immutable_update BEFORE UPDATE ON cook_recipe_archive_row_occurrence BEGIN SELECT RAISE(ABORT,'cook_recipe_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_recipe_archive_row_immutable_delete BEFORE DELETE ON cook_recipe_archive_row_occurrence BEGIN SELECT RAISE(ABORT,'cook_recipe_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_recipe_archive_raw_immutable_update BEFORE UPDATE ON cook_recipe_archive_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'cook_recipe_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_recipe_archive_raw_immutable_delete BEFORE DELETE ON cook_recipe_archive_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'cook_recipe_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_recipe_archive_rel_immutable_update BEFORE UPDATE ON cook_recipe_archive_relationship_occurrence BEGIN SELECT RAISE(ABORT,'cook_recipe_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_recipe_archive_rel_immutable_delete BEFORE DELETE ON cook_recipe_archive_relationship_occurrence BEGIN SELECT RAISE(ABORT,'cook_recipe_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_recipe_archive_cand_immutable_update BEFORE UPDATE ON cook_recipe_archive_relationship_candidate_occurrence BEGIN SELECT RAISE(ABORT,'cook_recipe_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_recipe_archive_cand_immutable_delete BEFORE DELETE ON cook_recipe_archive_relationship_candidate_occurrence BEGIN SELECT RAISE(ABORT,'cook_recipe_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_recipe_archive_owner_immutable_update BEFORE UPDATE ON cook_recipe_archive_text_owner_occurrence BEGIN SELECT RAISE(ABORT,'cook_recipe_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_recipe_archive_owner_immutable_delete BEFORE DELETE ON cook_recipe_archive_text_owner_occurrence BEGIN SELECT RAISE(ABORT,'cook_recipe_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_recipe_archive_res_immutable_update BEFORE UPDATE ON cook_recipe_archive_text_resolution_occurrence BEGIN SELECT RAISE(ABORT,'cook_recipe_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_recipe_archive_res_immutable_delete BEFORE DELETE ON cook_recipe_archive_text_resolution_occurrence BEGIN SELECT RAISE(ABORT,'cook_recipe_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_recipe_archive_text_immutable_update BEFORE UPDATE ON cook_recipe_archive_text_candidate_occurrence BEGIN SELECT RAISE(ABORT,'cook_recipe_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_recipe_archive_text_immutable_delete BEFORE DELETE ON cook_recipe_archive_text_candidate_occurrence BEGIN SELECT RAISE(ABORT,'cook_recipe_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_recipe_archive_opaque_immutable_update BEFORE UPDATE ON cook_recipe_archive_opaque_occurrence BEGIN SELECT RAISE(ABORT,'cook_recipe_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_recipe_archive_opaque_immutable_delete BEFORE DELETE ON cook_recipe_archive_opaque_occurrence BEGIN SELECT RAISE(ABORT,'cook_recipe_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_recipe_archive_asset_immutable_update BEFORE UPDATE ON cook_recipe_archive_asset_occurrence BEGIN SELECT RAISE(ABORT,'cook_recipe_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_recipe_archive_asset_immutable_delete BEFORE DELETE ON cook_recipe_archive_asset_occurrence BEGIN SELECT RAISE(ABORT,'cook_recipe_archive evidence is immutable'); END;

-- cook_bonus_archive: row, raw, relation, candidate, opaque
CREATE TABLE cook_bonus_archive_row_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, family TEXT NOT NULL,
 family_contract_version TEXT NOT NULL, ingest_run_id TEXT NOT NULL,
 snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_file_path TEXT NOT NULL, source_file_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_record_pointer TEXT NOT NULL,
 source_r0_physical_pointer TEXT NOT NULL, source_ordinal INTEGER NOT NULL CHECK(source_ordinal>=0),
 raw_record_json TEXT NOT NULL, raw_record_sha256 TEXT NOT NULL CHECK(length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_record_lexical_sha256 TEXT NOT NULL CHECK(length(raw_record_lexical_sha256)=64 AND raw_record_lexical_sha256 NOT GLOB '*[^0-9a-f]*'),
 native_identity_json TEXT NOT NULL CHECK(json_valid(native_identity_json)),
 record_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json))
);
CREATE TABLE cook_bonus_archive_raw_field_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 field_presence TEXT NOT NULL CHECK(field_presence IN ('present','missing')),
 raw_json TEXT, resolution_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 CHECK((field_presence='present' AND raw_json IS NOT NULL) OR (field_presence='missing' AND raw_json IS NULL)),
 FOREIGN KEY(row_occurrence_id) REFERENCES cook_bonus_archive_row_occurrence(occurrence_id)
);
CREATE TABLE cook_bonus_archive_relationship_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, relation_field TEXT NOT NULL,
 raw_value_json TEXT, target_source_file TEXT NOT NULL, target_field TEXT NOT NULL,
 condition_json TEXT NOT NULL CHECK(json_valid(condition_json)),
 resolution_status TEXT NOT NULL CHECK(resolution_status IN ('missing_field','empty_slot','missing_target','dangling','ambiguous','resolved_unique','resolved_candidate_set','unresolved','opaque_unresolved','not_applicable')),
 candidate_count INTEGER NOT NULL CHECK(candidate_count>=0), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES cook_bonus_archive_row_occurrence(occurrence_id)
);
CREATE TABLE cook_bonus_archive_relationship_candidate_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, relationship_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_file_id TEXT NOT NULL, source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL,
 candidate_ordinal INTEGER NOT NULL CHECK(candidate_ordinal>=0), target_source_file TEXT NOT NULL,
 target_source_row_ordinal INTEGER NOT NULL CHECK(target_source_row_ordinal>=0), target_pointer TEXT NOT NULL,
 target_field TEXT NOT NULL, target_value_json TEXT NOT NULL CHECK(json_valid(target_value_json)),
 target_raw_record_sha256 TEXT NOT NULL CHECK(length(target_raw_record_sha256)=64 AND target_raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 target_lexical_sha256 TEXT NOT NULL CHECK(length(target_lexical_sha256)=64 AND target_lexical_sha256 NOT GLOB '*[^0-9a-f]*'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(relationship_occurrence_id) REFERENCES cook_bonus_archive_relationship_occurrence(occurrence_id)
);
CREATE TABLE cook_bonus_archive_opaque_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 raw_json TEXT NOT NULL CHECK(json_valid(raw_json)), resolution_status TEXT NOT NULL CHECK(resolution_status='opaque_unresolved'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES cook_bonus_archive_row_occurrence(occurrence_id)
);
CREATE INDEX idx_m5c_cook_bonus_archive_row ON cook_bonus_archive_row_occurrence (source_file_path, source_ordinal, source_record_pointer, occurrence_id);
CREATE INDEX idx_m5c_cook_bonus_archive_raw ON cook_bonus_archive_raw_field_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_cook_bonus_archive_relation ON cook_bonus_archive_relationship_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_cook_bonus_archive_candidate ON cook_bonus_archive_relationship_candidate_occurrence (relationship_occurrence_id, candidate_ordinal, occurrence_id);
CREATE INDEX idx_m5c_cook_bonus_archive_opaque ON cook_bonus_archive_opaque_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE TRIGGER trg_cook_bonus_archive_row_immutable_update BEFORE UPDATE ON cook_bonus_archive_row_occurrence BEGIN SELECT RAISE(ABORT,'cook_bonus_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_bonus_archive_row_immutable_delete BEFORE DELETE ON cook_bonus_archive_row_occurrence BEGIN SELECT RAISE(ABORT,'cook_bonus_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_bonus_archive_raw_immutable_update BEFORE UPDATE ON cook_bonus_archive_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'cook_bonus_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_bonus_archive_raw_immutable_delete BEFORE DELETE ON cook_bonus_archive_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'cook_bonus_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_bonus_archive_rel_immutable_update BEFORE UPDATE ON cook_bonus_archive_relationship_occurrence BEGIN SELECT RAISE(ABORT,'cook_bonus_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_bonus_archive_rel_immutable_delete BEFORE DELETE ON cook_bonus_archive_relationship_occurrence BEGIN SELECT RAISE(ABORT,'cook_bonus_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_bonus_archive_cand_immutable_update BEFORE UPDATE ON cook_bonus_archive_relationship_candidate_occurrence BEGIN SELECT RAISE(ABORT,'cook_bonus_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_bonus_archive_cand_immutable_delete BEFORE DELETE ON cook_bonus_archive_relationship_candidate_occurrence BEGIN SELECT RAISE(ABORT,'cook_bonus_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_bonus_archive_opaque_immutable_update BEFORE UPDATE ON cook_bonus_archive_opaque_occurrence BEGIN SELECT RAISE(ABORT,'cook_bonus_archive evidence is immutable'); END;
CREATE TRIGGER trg_cook_bonus_archive_opaque_immutable_delete BEFORE DELETE ON cook_bonus_archive_opaque_occurrence BEGIN SELECT RAISE(ABORT,'cook_bonus_archive evidence is immutable'); END;

-- homeworld_furniture_archive: row, raw, relation, candidate, owner, resolution, text, opaque, asset
CREATE TABLE homeworld_furniture_archive_row_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, family TEXT NOT NULL,
 family_contract_version TEXT NOT NULL, ingest_run_id TEXT NOT NULL,
 snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_file_path TEXT NOT NULL, source_file_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_record_pointer TEXT NOT NULL,
 source_r0_physical_pointer TEXT NOT NULL, source_ordinal INTEGER NOT NULL CHECK(source_ordinal>=0),
 raw_record_json TEXT NOT NULL, raw_record_sha256 TEXT NOT NULL CHECK(length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_record_lexical_sha256 TEXT NOT NULL CHECK(length(raw_record_lexical_sha256)=64 AND raw_record_lexical_sha256 NOT GLOB '*[^0-9a-f]*'),
 native_identity_json TEXT NOT NULL CHECK(json_valid(native_identity_json)),
 record_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json))
);
CREATE TABLE homeworld_furniture_archive_raw_field_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 field_presence TEXT NOT NULL CHECK(field_presence IN ('present','missing')),
 raw_json TEXT, resolution_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 CHECK((field_presence='present' AND raw_json IS NOT NULL) OR (field_presence='missing' AND raw_json IS NULL)),
 FOREIGN KEY(row_occurrence_id) REFERENCES homeworld_furniture_archive_row_occurrence(occurrence_id)
);
CREATE TABLE homeworld_furniture_archive_relationship_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, relation_field TEXT NOT NULL,
 raw_value_json TEXT, target_source_file TEXT NOT NULL, target_field TEXT NOT NULL,
 condition_json TEXT NOT NULL CHECK(json_valid(condition_json)),
 resolution_status TEXT NOT NULL CHECK(resolution_status IN ('missing_field','empty_slot','missing_target','dangling','ambiguous','resolved_unique','resolved_candidate_set','unresolved','opaque_unresolved','not_applicable')),
 candidate_count INTEGER NOT NULL CHECK(candidate_count>=0), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES homeworld_furniture_archive_row_occurrence(occurrence_id)
);
CREATE TABLE homeworld_furniture_archive_relationship_candidate_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, relationship_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_file_id TEXT NOT NULL, source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL,
 candidate_ordinal INTEGER NOT NULL CHECK(candidate_ordinal>=0), target_source_file TEXT NOT NULL,
 target_source_row_ordinal INTEGER NOT NULL CHECK(target_source_row_ordinal>=0), target_pointer TEXT NOT NULL,
 target_field TEXT NOT NULL, target_value_json TEXT NOT NULL CHECK(json_valid(target_value_json)),
 target_raw_record_sha256 TEXT NOT NULL CHECK(length(target_raw_record_sha256)=64 AND target_raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 target_lexical_sha256 TEXT NOT NULL CHECK(length(target_lexical_sha256)=64 AND target_lexical_sha256 NOT GLOB '*[^0-9a-f]*'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(relationship_occurrence_id) REFERENCES homeworld_furniture_archive_relationship_occurrence(occurrence_id)
);
CREATE TABLE homeworld_furniture_archive_text_owner_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 owner_hash_json TEXT NOT NULL CHECK(json_valid(owner_hash_json)), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES homeworld_furniture_archive_row_occurrence(occurrence_id)
);
CREATE TABLE homeworld_furniture_archive_text_resolution_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, owner_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')), resolution_status TEXT NOT NULL CHECK(resolution_status IN ('missing','resolved_unique','ambiguous')),
 candidate_count INTEGER NOT NULL CHECK(candidate_count>=0), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(owner_occurrence_id) REFERENCES homeworld_furniture_archive_text_owner_occurrence(occurrence_id)
);
CREATE TABLE homeworld_furniture_archive_text_candidate_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, resolution_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 relative_path TEXT NOT NULL, locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')),
 shard_tier TEXT NOT NULL CHECK(shard_tier IN ('main','medium')), pair_ordinal INTEGER NOT NULL CHECK(pair_ordinal>=0),
 pointer TEXT NOT NULL, key_text TEXT NOT NULL, raw_text TEXT NOT NULL,
 raw_key_sha256 TEXT NOT NULL CHECK(length(raw_key_sha256)=64 AND raw_key_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_text_sha256 TEXT NOT NULL CHECK(length(raw_text_sha256)=64 AND raw_text_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_record_sha256 TEXT NOT NULL CHECK(length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(resolution_occurrence_id) REFERENCES homeworld_furniture_archive_text_resolution_occurrence(occurrence_id)
);
CREATE TABLE homeworld_furniture_archive_opaque_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 raw_json TEXT NOT NULL CHECK(json_valid(raw_json)), resolution_status TEXT NOT NULL CHECK(resolution_status='opaque_unresolved'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES homeworld_furniture_archive_row_occurrence(occurrence_id)
);
CREATE TABLE homeworld_furniture_archive_asset_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 raw_json TEXT, resolution_status TEXT NOT NULL CHECK(resolution_status IN ('raw_observed','missing_field','empty_slot')),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES homeworld_furniture_archive_row_occurrence(occurrence_id)
);
CREATE INDEX idx_m5c_homeworld_furniture_archive_row ON homeworld_furniture_archive_row_occurrence (source_file_path, source_ordinal, source_record_pointer, occurrence_id);
CREATE INDEX idx_m5c_homeworld_furniture_archive_raw ON homeworld_furniture_archive_raw_field_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_homeworld_furniture_archive_relation ON homeworld_furniture_archive_relationship_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_homeworld_furniture_archive_candidate ON homeworld_furniture_archive_relationship_candidate_occurrence (relationship_occurrence_id, candidate_ordinal, occurrence_id);
CREATE INDEX idx_m5c_homeworld_furniture_archive_owner ON homeworld_furniture_archive_text_owner_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_homeworld_furniture_archive_resolution ON homeworld_furniture_archive_text_resolution_occurrence (owner_occurrence_id, locale, occurrence_id);
CREATE INDEX idx_m5c_homeworld_furniture_archive_text ON homeworld_furniture_archive_text_candidate_occurrence (relative_path, pair_ordinal, occurrence_id);
CREATE INDEX idx_m5c_homeworld_furniture_archive_opaque ON homeworld_furniture_archive_opaque_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_homeworld_furniture_archive_asset ON homeworld_furniture_archive_asset_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE TRIGGER trg_homeworld_furniture_archive_row_immutable_update BEFORE UPDATE ON homeworld_furniture_archive_row_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_archive_row_immutable_delete BEFORE DELETE ON homeworld_furniture_archive_row_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_archive_raw_immutable_update BEFORE UPDATE ON homeworld_furniture_archive_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_archive_raw_immutable_delete BEFORE DELETE ON homeworld_furniture_archive_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_archive_rel_immutable_update BEFORE UPDATE ON homeworld_furniture_archive_relationship_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_archive_rel_immutable_delete BEFORE DELETE ON homeworld_furniture_archive_relationship_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_archive_cand_immutable_update BEFORE UPDATE ON homeworld_furniture_archive_relationship_candidate_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_archive_cand_immutable_delete BEFORE DELETE ON homeworld_furniture_archive_relationship_candidate_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_archive_owner_immutable_update BEFORE UPDATE ON homeworld_furniture_archive_text_owner_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_archive_owner_immutable_delete BEFORE DELETE ON homeworld_furniture_archive_text_owner_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_archive_res_immutable_update BEFORE UPDATE ON homeworld_furniture_archive_text_resolution_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_archive_res_immutable_delete BEFORE DELETE ON homeworld_furniture_archive_text_resolution_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_archive_text_immutable_update BEFORE UPDATE ON homeworld_furniture_archive_text_candidate_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_archive_text_immutable_delete BEFORE DELETE ON homeworld_furniture_archive_text_candidate_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_archive_opaque_immutable_update BEFORE UPDATE ON homeworld_furniture_archive_opaque_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_archive_opaque_immutable_delete BEFORE DELETE ON homeworld_furniture_archive_opaque_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_archive_asset_immutable_update BEFORE UPDATE ON homeworld_furniture_archive_asset_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_archive_asset_immutable_delete BEFORE DELETE ON homeworld_furniture_archive_asset_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_archive evidence is immutable'); END;

-- furniture_make_archive: row, raw, relation, candidate, opaque
CREATE TABLE furniture_make_archive_row_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, family TEXT NOT NULL,
 family_contract_version TEXT NOT NULL, ingest_run_id TEXT NOT NULL,
 snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_file_path TEXT NOT NULL, source_file_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_record_pointer TEXT NOT NULL,
 source_r0_physical_pointer TEXT NOT NULL, source_ordinal INTEGER NOT NULL CHECK(source_ordinal>=0),
 raw_record_json TEXT NOT NULL, raw_record_sha256 TEXT NOT NULL CHECK(length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_record_lexical_sha256 TEXT NOT NULL CHECK(length(raw_record_lexical_sha256)=64 AND raw_record_lexical_sha256 NOT GLOB '*[^0-9a-f]*'),
 native_identity_json TEXT NOT NULL CHECK(json_valid(native_identity_json)),
 record_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json))
);
CREATE TABLE furniture_make_archive_raw_field_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 field_presence TEXT NOT NULL CHECK(field_presence IN ('present','missing')),
 raw_json TEXT, resolution_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 CHECK((field_presence='present' AND raw_json IS NOT NULL) OR (field_presence='missing' AND raw_json IS NULL)),
 FOREIGN KEY(row_occurrence_id) REFERENCES furniture_make_archive_row_occurrence(occurrence_id)
);
CREATE TABLE furniture_make_archive_relationship_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, relation_field TEXT NOT NULL,
 raw_value_json TEXT, target_source_file TEXT NOT NULL, target_field TEXT NOT NULL,
 condition_json TEXT NOT NULL CHECK(json_valid(condition_json)),
 resolution_status TEXT NOT NULL CHECK(resolution_status IN ('missing_field','empty_slot','missing_target','dangling','ambiguous','resolved_unique','resolved_candidate_set','unresolved','opaque_unresolved','not_applicable')),
 candidate_count INTEGER NOT NULL CHECK(candidate_count>=0), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES furniture_make_archive_row_occurrence(occurrence_id)
);
CREATE TABLE furniture_make_archive_relationship_candidate_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, relationship_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_file_id TEXT NOT NULL, source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL,
 candidate_ordinal INTEGER NOT NULL CHECK(candidate_ordinal>=0), target_source_file TEXT NOT NULL,
 target_source_row_ordinal INTEGER NOT NULL CHECK(target_source_row_ordinal>=0), target_pointer TEXT NOT NULL,
 target_field TEXT NOT NULL, target_value_json TEXT NOT NULL CHECK(json_valid(target_value_json)),
 target_raw_record_sha256 TEXT NOT NULL CHECK(length(target_raw_record_sha256)=64 AND target_raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 target_lexical_sha256 TEXT NOT NULL CHECK(length(target_lexical_sha256)=64 AND target_lexical_sha256 NOT GLOB '*[^0-9a-f]*'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(relationship_occurrence_id) REFERENCES furniture_make_archive_relationship_occurrence(occurrence_id)
);
CREATE TABLE furniture_make_archive_opaque_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 raw_json TEXT NOT NULL CHECK(json_valid(raw_json)), resolution_status TEXT NOT NULL CHECK(resolution_status='opaque_unresolved'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES furniture_make_archive_row_occurrence(occurrence_id)
);
CREATE INDEX idx_m5c_furniture_make_archive_row ON furniture_make_archive_row_occurrence (source_file_path, source_ordinal, source_record_pointer, occurrence_id);
CREATE INDEX idx_m5c_furniture_make_archive_raw ON furniture_make_archive_raw_field_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_furniture_make_archive_relation ON furniture_make_archive_relationship_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_furniture_make_archive_candidate ON furniture_make_archive_relationship_candidate_occurrence (relationship_occurrence_id, candidate_ordinal, occurrence_id);
CREATE INDEX idx_m5c_furniture_make_archive_opaque ON furniture_make_archive_opaque_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE TRIGGER trg_furniture_make_archive_row_immutable_update BEFORE UPDATE ON furniture_make_archive_row_occurrence BEGIN SELECT RAISE(ABORT,'furniture_make_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_make_archive_row_immutable_delete BEFORE DELETE ON furniture_make_archive_row_occurrence BEGIN SELECT RAISE(ABORT,'furniture_make_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_make_archive_raw_immutable_update BEFORE UPDATE ON furniture_make_archive_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'furniture_make_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_make_archive_raw_immutable_delete BEFORE DELETE ON furniture_make_archive_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'furniture_make_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_make_archive_rel_immutable_update BEFORE UPDATE ON furniture_make_archive_relationship_occurrence BEGIN SELECT RAISE(ABORT,'furniture_make_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_make_archive_rel_immutable_delete BEFORE DELETE ON furniture_make_archive_relationship_occurrence BEGIN SELECT RAISE(ABORT,'furniture_make_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_make_archive_cand_immutable_update BEFORE UPDATE ON furniture_make_archive_relationship_candidate_occurrence BEGIN SELECT RAISE(ABORT,'furniture_make_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_make_archive_cand_immutable_delete BEFORE DELETE ON furniture_make_archive_relationship_candidate_occurrence BEGIN SELECT RAISE(ABORT,'furniture_make_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_make_archive_opaque_immutable_update BEFORE UPDATE ON furniture_make_archive_opaque_occurrence BEGIN SELECT RAISE(ABORT,'furniture_make_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_make_archive_opaque_immutable_delete BEFORE DELETE ON furniture_make_archive_opaque_occurrence BEGIN SELECT RAISE(ABORT,'furniture_make_archive evidence is immutable'); END;

-- furniture_suite_archive: row, raw, relation, candidate, owner, resolution, text, opaque, asset
CREATE TABLE furniture_suite_archive_row_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, family TEXT NOT NULL,
 family_contract_version TEXT NOT NULL, ingest_run_id TEXT NOT NULL,
 snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_file_path TEXT NOT NULL, source_file_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_record_pointer TEXT NOT NULL,
 source_r0_physical_pointer TEXT NOT NULL, source_ordinal INTEGER NOT NULL CHECK(source_ordinal>=0),
 raw_record_json TEXT NOT NULL, raw_record_sha256 TEXT NOT NULL CHECK(length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_record_lexical_sha256 TEXT NOT NULL CHECK(length(raw_record_lexical_sha256)=64 AND raw_record_lexical_sha256 NOT GLOB '*[^0-9a-f]*'),
 native_identity_json TEXT NOT NULL CHECK(json_valid(native_identity_json)),
 record_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json))
);
CREATE TABLE furniture_suite_archive_raw_field_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 field_presence TEXT NOT NULL CHECK(field_presence IN ('present','missing')),
 raw_json TEXT, resolution_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 CHECK((field_presence='present' AND raw_json IS NOT NULL) OR (field_presence='missing' AND raw_json IS NULL)),
 FOREIGN KEY(row_occurrence_id) REFERENCES furniture_suite_archive_row_occurrence(occurrence_id)
);
CREATE TABLE furniture_suite_archive_relationship_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, relation_field TEXT NOT NULL,
 raw_value_json TEXT, target_source_file TEXT NOT NULL, target_field TEXT NOT NULL,
 condition_json TEXT NOT NULL CHECK(json_valid(condition_json)),
 resolution_status TEXT NOT NULL CHECK(resolution_status IN ('missing_field','empty_slot','missing_target','dangling','ambiguous','resolved_unique','resolved_candidate_set','unresolved','opaque_unresolved','not_applicable')),
 candidate_count INTEGER NOT NULL CHECK(candidate_count>=0), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES furniture_suite_archive_row_occurrence(occurrence_id)
);
CREATE TABLE furniture_suite_archive_relationship_candidate_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, relationship_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_file_id TEXT NOT NULL, source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL,
 candidate_ordinal INTEGER NOT NULL CHECK(candidate_ordinal>=0), target_source_file TEXT NOT NULL,
 target_source_row_ordinal INTEGER NOT NULL CHECK(target_source_row_ordinal>=0), target_pointer TEXT NOT NULL,
 target_field TEXT NOT NULL, target_value_json TEXT NOT NULL CHECK(json_valid(target_value_json)),
 target_raw_record_sha256 TEXT NOT NULL CHECK(length(target_raw_record_sha256)=64 AND target_raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 target_lexical_sha256 TEXT NOT NULL CHECK(length(target_lexical_sha256)=64 AND target_lexical_sha256 NOT GLOB '*[^0-9a-f]*'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(relationship_occurrence_id) REFERENCES furniture_suite_archive_relationship_occurrence(occurrence_id)
);
CREATE TABLE furniture_suite_archive_text_owner_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 owner_hash_json TEXT NOT NULL CHECK(json_valid(owner_hash_json)), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES furniture_suite_archive_row_occurrence(occurrence_id)
);
CREATE TABLE furniture_suite_archive_text_resolution_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, owner_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')), resolution_status TEXT NOT NULL CHECK(resolution_status IN ('missing','resolved_unique','ambiguous')),
 candidate_count INTEGER NOT NULL CHECK(candidate_count>=0), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(owner_occurrence_id) REFERENCES furniture_suite_archive_text_owner_occurrence(occurrence_id)
);
CREATE TABLE furniture_suite_archive_text_candidate_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, resolution_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 relative_path TEXT NOT NULL, locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')),
 shard_tier TEXT NOT NULL CHECK(shard_tier IN ('main','medium')), pair_ordinal INTEGER NOT NULL CHECK(pair_ordinal>=0),
 pointer TEXT NOT NULL, key_text TEXT NOT NULL, raw_text TEXT NOT NULL,
 raw_key_sha256 TEXT NOT NULL CHECK(length(raw_key_sha256)=64 AND raw_key_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_text_sha256 TEXT NOT NULL CHECK(length(raw_text_sha256)=64 AND raw_text_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_record_sha256 TEXT NOT NULL CHECK(length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(resolution_occurrence_id) REFERENCES furniture_suite_archive_text_resolution_occurrence(occurrence_id)
);
CREATE TABLE furniture_suite_archive_opaque_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 raw_json TEXT NOT NULL CHECK(json_valid(raw_json)), resolution_status TEXT NOT NULL CHECK(resolution_status='opaque_unresolved'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES furniture_suite_archive_row_occurrence(occurrence_id)
);
CREATE TABLE furniture_suite_archive_asset_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 raw_json TEXT, resolution_status TEXT NOT NULL CHECK(resolution_status IN ('raw_observed','missing_field','empty_slot')),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES furniture_suite_archive_row_occurrence(occurrence_id)
);
CREATE INDEX idx_m5c_furniture_suite_archive_row ON furniture_suite_archive_row_occurrence (source_file_path, source_ordinal, source_record_pointer, occurrence_id);
CREATE INDEX idx_m5c_furniture_suite_archive_raw ON furniture_suite_archive_raw_field_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_furniture_suite_archive_relation ON furniture_suite_archive_relationship_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_furniture_suite_archive_candidate ON furniture_suite_archive_relationship_candidate_occurrence (relationship_occurrence_id, candidate_ordinal, occurrence_id);
CREATE INDEX idx_m5c_furniture_suite_archive_owner ON furniture_suite_archive_text_owner_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_furniture_suite_archive_resolution ON furniture_suite_archive_text_resolution_occurrence (owner_occurrence_id, locale, occurrence_id);
CREATE INDEX idx_m5c_furniture_suite_archive_text ON furniture_suite_archive_text_candidate_occurrence (relative_path, pair_ordinal, occurrence_id);
CREATE INDEX idx_m5c_furniture_suite_archive_opaque ON furniture_suite_archive_opaque_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_furniture_suite_archive_asset ON furniture_suite_archive_asset_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE TRIGGER trg_furniture_suite_archive_row_immutable_update BEFORE UPDATE ON furniture_suite_archive_row_occurrence BEGIN SELECT RAISE(ABORT,'furniture_suite_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_suite_archive_row_immutable_delete BEFORE DELETE ON furniture_suite_archive_row_occurrence BEGIN SELECT RAISE(ABORT,'furniture_suite_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_suite_archive_raw_immutable_update BEFORE UPDATE ON furniture_suite_archive_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'furniture_suite_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_suite_archive_raw_immutable_delete BEFORE DELETE ON furniture_suite_archive_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'furniture_suite_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_suite_archive_rel_immutable_update BEFORE UPDATE ON furniture_suite_archive_relationship_occurrence BEGIN SELECT RAISE(ABORT,'furniture_suite_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_suite_archive_rel_immutable_delete BEFORE DELETE ON furniture_suite_archive_relationship_occurrence BEGIN SELECT RAISE(ABORT,'furniture_suite_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_suite_archive_cand_immutable_update BEFORE UPDATE ON furniture_suite_archive_relationship_candidate_occurrence BEGIN SELECT RAISE(ABORT,'furniture_suite_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_suite_archive_cand_immutable_delete BEFORE DELETE ON furniture_suite_archive_relationship_candidate_occurrence BEGIN SELECT RAISE(ABORT,'furniture_suite_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_suite_archive_owner_immutable_update BEFORE UPDATE ON furniture_suite_archive_text_owner_occurrence BEGIN SELECT RAISE(ABORT,'furniture_suite_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_suite_archive_owner_immutable_delete BEFORE DELETE ON furniture_suite_archive_text_owner_occurrence BEGIN SELECT RAISE(ABORT,'furniture_suite_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_suite_archive_res_immutable_update BEFORE UPDATE ON furniture_suite_archive_text_resolution_occurrence BEGIN SELECT RAISE(ABORT,'furniture_suite_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_suite_archive_res_immutable_delete BEFORE DELETE ON furniture_suite_archive_text_resolution_occurrence BEGIN SELECT RAISE(ABORT,'furniture_suite_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_suite_archive_text_immutable_update BEFORE UPDATE ON furniture_suite_archive_text_candidate_occurrence BEGIN SELECT RAISE(ABORT,'furniture_suite_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_suite_archive_text_immutable_delete BEFORE DELETE ON furniture_suite_archive_text_candidate_occurrence BEGIN SELECT RAISE(ABORT,'furniture_suite_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_suite_archive_opaque_immutable_update BEFORE UPDATE ON furniture_suite_archive_opaque_occurrence BEGIN SELECT RAISE(ABORT,'furniture_suite_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_suite_archive_opaque_immutable_delete BEFORE DELETE ON furniture_suite_archive_opaque_occurrence BEGIN SELECT RAISE(ABORT,'furniture_suite_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_suite_archive_asset_immutable_update BEFORE UPDATE ON furniture_suite_archive_asset_occurrence BEGIN SELECT RAISE(ABORT,'furniture_suite_archive evidence is immutable'); END;
CREATE TRIGGER trg_furniture_suite_archive_asset_immutable_delete BEFORE DELETE ON furniture_suite_archive_asset_occurrence BEGIN SELECT RAISE(ABORT,'furniture_suite_archive evidence is immutable'); END;

-- homeworld_furniture_type_archive: row, raw, owner, resolution, text, opaque, asset
CREATE TABLE homeworld_furniture_type_archive_row_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, family TEXT NOT NULL,
 family_contract_version TEXT NOT NULL, ingest_run_id TEXT NOT NULL,
 snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_file_path TEXT NOT NULL, source_file_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_record_pointer TEXT NOT NULL,
 source_r0_physical_pointer TEXT NOT NULL, source_ordinal INTEGER NOT NULL CHECK(source_ordinal>=0),
 raw_record_json TEXT NOT NULL, raw_record_sha256 TEXT NOT NULL CHECK(length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_record_lexical_sha256 TEXT NOT NULL CHECK(length(raw_record_lexical_sha256)=64 AND raw_record_lexical_sha256 NOT GLOB '*[^0-9a-f]*'),
 native_identity_json TEXT NOT NULL CHECK(json_valid(native_identity_json)),
 record_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json))
);
CREATE TABLE homeworld_furniture_type_archive_raw_field_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 field_presence TEXT NOT NULL CHECK(field_presence IN ('present','missing')),
 raw_json TEXT, resolution_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 CHECK((field_presence='present' AND raw_json IS NOT NULL) OR (field_presence='missing' AND raw_json IS NULL)),
 FOREIGN KEY(row_occurrence_id) REFERENCES homeworld_furniture_type_archive_row_occurrence(occurrence_id)
);
CREATE TABLE homeworld_furniture_type_archive_text_owner_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 owner_hash_json TEXT NOT NULL CHECK(json_valid(owner_hash_json)), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES homeworld_furniture_type_archive_row_occurrence(occurrence_id)
);
CREATE TABLE homeworld_furniture_type_archive_text_resolution_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, owner_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')), resolution_status TEXT NOT NULL CHECK(resolution_status IN ('missing','resolved_unique','ambiguous')),
 candidate_count INTEGER NOT NULL CHECK(candidate_count>=0), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(owner_occurrence_id) REFERENCES homeworld_furniture_type_archive_text_owner_occurrence(occurrence_id)
);
CREATE TABLE homeworld_furniture_type_archive_text_candidate_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, resolution_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 relative_path TEXT NOT NULL, locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')),
 shard_tier TEXT NOT NULL CHECK(shard_tier IN ('main','medium')), pair_ordinal INTEGER NOT NULL CHECK(pair_ordinal>=0),
 pointer TEXT NOT NULL, key_text TEXT NOT NULL, raw_text TEXT NOT NULL,
 raw_key_sha256 TEXT NOT NULL CHECK(length(raw_key_sha256)=64 AND raw_key_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_text_sha256 TEXT NOT NULL CHECK(length(raw_text_sha256)=64 AND raw_text_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_record_sha256 TEXT NOT NULL CHECK(length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(resolution_occurrence_id) REFERENCES homeworld_furniture_type_archive_text_resolution_occurrence(occurrence_id)
);
CREATE TABLE homeworld_furniture_type_archive_opaque_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 raw_json TEXT NOT NULL CHECK(json_valid(raw_json)), resolution_status TEXT NOT NULL CHECK(resolution_status='opaque_unresolved'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES homeworld_furniture_type_archive_row_occurrence(occurrence_id)
);
CREATE TABLE homeworld_furniture_type_archive_asset_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 raw_json TEXT, resolution_status TEXT NOT NULL CHECK(resolution_status IN ('raw_observed','missing_field','empty_slot')),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES homeworld_furniture_type_archive_row_occurrence(occurrence_id)
);
CREATE INDEX idx_m5c_homeworld_furniture_type_archive_row ON homeworld_furniture_type_archive_row_occurrence (source_file_path, source_ordinal, source_record_pointer, occurrence_id);
CREATE INDEX idx_m5c_homeworld_furniture_type_archive_raw ON homeworld_furniture_type_archive_raw_field_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_homeworld_furniture_type_archive_owner ON homeworld_furniture_type_archive_text_owner_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_homeworld_furniture_type_archive_resolution ON homeworld_furniture_type_archive_text_resolution_occurrence (owner_occurrence_id, locale, occurrence_id);
CREATE INDEX idx_m5c_homeworld_furniture_type_archive_text ON homeworld_furniture_type_archive_text_candidate_occurrence (relative_path, pair_ordinal, occurrence_id);
CREATE INDEX idx_m5c_homeworld_furniture_type_archive_opaque ON homeworld_furniture_type_archive_opaque_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_homeworld_furniture_type_archive_asset ON homeworld_furniture_type_archive_asset_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE TRIGGER trg_homeworld_furniture_type_archive_row_immutable_update BEFORE UPDATE ON homeworld_furniture_type_archive_row_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_type_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_type_archive_row_immutable_delete BEFORE DELETE ON homeworld_furniture_type_archive_row_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_type_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_type_archive_raw_immutable_update BEFORE UPDATE ON homeworld_furniture_type_archive_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_type_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_type_archive_raw_immutable_delete BEFORE DELETE ON homeworld_furniture_type_archive_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_type_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_type_archive_owner_immutable_update BEFORE UPDATE ON homeworld_furniture_type_archive_text_owner_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_type_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_type_archive_owner_immutable_delete BEFORE DELETE ON homeworld_furniture_type_archive_text_owner_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_type_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_type_archive_res_immutable_update BEFORE UPDATE ON homeworld_furniture_type_archive_text_resolution_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_type_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_type_archive_res_immutable_delete BEFORE DELETE ON homeworld_furniture_type_archive_text_resolution_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_type_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_type_archive_text_immutable_update BEFORE UPDATE ON homeworld_furniture_type_archive_text_candidate_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_type_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_type_archive_text_immutable_delete BEFORE DELETE ON homeworld_furniture_type_archive_text_candidate_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_type_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_type_archive_opaque_immutable_update BEFORE UPDATE ON homeworld_furniture_type_archive_opaque_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_type_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_type_archive_opaque_immutable_delete BEFORE DELETE ON homeworld_furniture_type_archive_opaque_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_type_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_type_archive_asset_immutable_update BEFORE UPDATE ON homeworld_furniture_type_archive_asset_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_type_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_furniture_type_archive_asset_immutable_delete BEFORE DELETE ON homeworld_furniture_type_archive_asset_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_furniture_type_archive evidence is immutable'); END;

-- homeworld_bgm_archive: row, raw, owner, resolution, text, opaque, asset
CREATE TABLE homeworld_bgm_archive_row_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, family TEXT NOT NULL,
 family_contract_version TEXT NOT NULL, ingest_run_id TEXT NOT NULL,
 snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_file_path TEXT NOT NULL, source_file_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_record_pointer TEXT NOT NULL,
 source_r0_physical_pointer TEXT NOT NULL, source_ordinal INTEGER NOT NULL CHECK(source_ordinal>=0),
 raw_record_json TEXT NOT NULL, raw_record_sha256 TEXT NOT NULL CHECK(length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_record_lexical_sha256 TEXT NOT NULL CHECK(length(raw_record_lexical_sha256)=64 AND raw_record_lexical_sha256 NOT GLOB '*[^0-9a-f]*'),
 native_identity_json TEXT NOT NULL CHECK(json_valid(native_identity_json)),
 record_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json))
);
CREATE TABLE homeworld_bgm_archive_raw_field_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 field_presence TEXT NOT NULL CHECK(field_presence IN ('present','missing')),
 raw_json TEXT, resolution_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 CHECK((field_presence='present' AND raw_json IS NOT NULL) OR (field_presence='missing' AND raw_json IS NULL)),
 FOREIGN KEY(row_occurrence_id) REFERENCES homeworld_bgm_archive_row_occurrence(occurrence_id)
);
CREATE TABLE homeworld_bgm_archive_text_owner_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 owner_hash_json TEXT NOT NULL CHECK(json_valid(owner_hash_json)), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES homeworld_bgm_archive_row_occurrence(occurrence_id)
);
CREATE TABLE homeworld_bgm_archive_text_resolution_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, owner_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')), resolution_status TEXT NOT NULL CHECK(resolution_status IN ('missing','resolved_unique','ambiguous')),
 candidate_count INTEGER NOT NULL CHECK(candidate_count>=0), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(owner_occurrence_id) REFERENCES homeworld_bgm_archive_text_owner_occurrence(occurrence_id)
);
CREATE TABLE homeworld_bgm_archive_text_candidate_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, resolution_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 relative_path TEXT NOT NULL, locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')),
 shard_tier TEXT NOT NULL CHECK(shard_tier IN ('main','medium')), pair_ordinal INTEGER NOT NULL CHECK(pair_ordinal>=0),
 pointer TEXT NOT NULL, key_text TEXT NOT NULL, raw_text TEXT NOT NULL,
 raw_key_sha256 TEXT NOT NULL CHECK(length(raw_key_sha256)=64 AND raw_key_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_text_sha256 TEXT NOT NULL CHECK(length(raw_text_sha256)=64 AND raw_text_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_record_sha256 TEXT NOT NULL CHECK(length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(resolution_occurrence_id) REFERENCES homeworld_bgm_archive_text_resolution_occurrence(occurrence_id)
);
CREATE TABLE homeworld_bgm_archive_opaque_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 raw_json TEXT NOT NULL CHECK(json_valid(raw_json)), resolution_status TEXT NOT NULL CHECK(resolution_status='opaque_unresolved'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES homeworld_bgm_archive_row_occurrence(occurrence_id)
);
CREATE TABLE homeworld_bgm_archive_asset_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 raw_json TEXT, resolution_status TEXT NOT NULL CHECK(resolution_status IN ('raw_observed','missing_field','empty_slot')),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES homeworld_bgm_archive_row_occurrence(occurrence_id)
);
CREATE INDEX idx_m5c_homeworld_bgm_archive_row ON homeworld_bgm_archive_row_occurrence (source_file_path, source_ordinal, source_record_pointer, occurrence_id);
CREATE INDEX idx_m5c_homeworld_bgm_archive_raw ON homeworld_bgm_archive_raw_field_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_homeworld_bgm_archive_owner ON homeworld_bgm_archive_text_owner_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_homeworld_bgm_archive_resolution ON homeworld_bgm_archive_text_resolution_occurrence (owner_occurrence_id, locale, occurrence_id);
CREATE INDEX idx_m5c_homeworld_bgm_archive_text ON homeworld_bgm_archive_text_candidate_occurrence (relative_path, pair_ordinal, occurrence_id);
CREATE INDEX idx_m5c_homeworld_bgm_archive_opaque ON homeworld_bgm_archive_opaque_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_homeworld_bgm_archive_asset ON homeworld_bgm_archive_asset_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE TRIGGER trg_homeworld_bgm_archive_row_immutable_update BEFORE UPDATE ON homeworld_bgm_archive_row_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_bgm_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_bgm_archive_row_immutable_delete BEFORE DELETE ON homeworld_bgm_archive_row_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_bgm_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_bgm_archive_raw_immutable_update BEFORE UPDATE ON homeworld_bgm_archive_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_bgm_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_bgm_archive_raw_immutable_delete BEFORE DELETE ON homeworld_bgm_archive_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_bgm_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_bgm_archive_owner_immutable_update BEFORE UPDATE ON homeworld_bgm_archive_text_owner_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_bgm_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_bgm_archive_owner_immutable_delete BEFORE DELETE ON homeworld_bgm_archive_text_owner_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_bgm_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_bgm_archive_res_immutable_update BEFORE UPDATE ON homeworld_bgm_archive_text_resolution_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_bgm_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_bgm_archive_res_immutable_delete BEFORE DELETE ON homeworld_bgm_archive_text_resolution_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_bgm_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_bgm_archive_text_immutable_update BEFORE UPDATE ON homeworld_bgm_archive_text_candidate_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_bgm_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_bgm_archive_text_immutable_delete BEFORE DELETE ON homeworld_bgm_archive_text_candidate_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_bgm_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_bgm_archive_opaque_immutable_update BEFORE UPDATE ON homeworld_bgm_archive_opaque_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_bgm_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_bgm_archive_opaque_immutable_delete BEFORE DELETE ON homeworld_bgm_archive_opaque_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_bgm_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_bgm_archive_asset_immutable_update BEFORE UPDATE ON homeworld_bgm_archive_asset_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_bgm_archive evidence is immutable'); END;
CREATE TRIGGER trg_homeworld_bgm_archive_asset_immutable_delete BEFORE DELETE ON homeworld_bgm_archive_asset_occurrence BEGIN SELECT RAISE(ABORT,'homeworld_bgm_archive evidence is immutable'); END;

-- profile_picture_archive: row, raw, relation, candidate, owner, resolution, text, opaque, asset
CREATE TABLE profile_picture_archive_row_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, family TEXT NOT NULL,
 family_contract_version TEXT NOT NULL, ingest_run_id TEXT NOT NULL,
 snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_file_path TEXT NOT NULL, source_file_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_record_pointer TEXT NOT NULL,
 source_r0_physical_pointer TEXT NOT NULL, source_ordinal INTEGER NOT NULL CHECK(source_ordinal>=0),
 raw_record_json TEXT NOT NULL, raw_record_sha256 TEXT NOT NULL CHECK(length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_record_lexical_sha256 TEXT NOT NULL CHECK(length(raw_record_lexical_sha256)=64 AND raw_record_lexical_sha256 NOT GLOB '*[^0-9a-f]*'),
 native_identity_json TEXT NOT NULL CHECK(json_valid(native_identity_json)),
 record_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json))
);
CREATE TABLE profile_picture_archive_raw_field_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 field_presence TEXT NOT NULL CHECK(field_presence IN ('present','missing')),
 raw_json TEXT, resolution_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 CHECK((field_presence='present' AND raw_json IS NOT NULL) OR (field_presence='missing' AND raw_json IS NULL)),
 FOREIGN KEY(row_occurrence_id) REFERENCES profile_picture_archive_row_occurrence(occurrence_id)
);
CREATE TABLE profile_picture_archive_relationship_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, relation_field TEXT NOT NULL,
 raw_value_json TEXT, target_source_file TEXT NOT NULL, target_field TEXT NOT NULL,
 condition_json TEXT NOT NULL CHECK(json_valid(condition_json)),
 resolution_status TEXT NOT NULL CHECK(resolution_status IN ('missing_field','empty_slot','missing_target','dangling','ambiguous','resolved_unique','resolved_candidate_set','unresolved','opaque_unresolved','not_applicable')),
 candidate_count INTEGER NOT NULL CHECK(candidate_count>=0), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES profile_picture_archive_row_occurrence(occurrence_id)
);
CREATE TABLE profile_picture_archive_relationship_candidate_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, relationship_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_file_id TEXT NOT NULL, source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL,
 candidate_ordinal INTEGER NOT NULL CHECK(candidate_ordinal>=0), target_source_file TEXT NOT NULL,
 target_source_row_ordinal INTEGER NOT NULL CHECK(target_source_row_ordinal>=0), target_pointer TEXT NOT NULL,
 target_field TEXT NOT NULL, target_value_json TEXT NOT NULL CHECK(json_valid(target_value_json)),
 target_raw_record_sha256 TEXT NOT NULL CHECK(length(target_raw_record_sha256)=64 AND target_raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 target_lexical_sha256 TEXT NOT NULL CHECK(length(target_lexical_sha256)=64 AND target_lexical_sha256 NOT GLOB '*[^0-9a-f]*'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(relationship_occurrence_id) REFERENCES profile_picture_archive_relationship_occurrence(occurrence_id)
);
CREATE TABLE profile_picture_archive_text_owner_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 owner_hash_json TEXT NOT NULL CHECK(json_valid(owner_hash_json)), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES profile_picture_archive_row_occurrence(occurrence_id)
);
CREATE TABLE profile_picture_archive_text_resolution_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, owner_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')), resolution_status TEXT NOT NULL CHECK(resolution_status IN ('missing','resolved_unique','ambiguous')),
 candidate_count INTEGER NOT NULL CHECK(candidate_count>=0), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(owner_occurrence_id) REFERENCES profile_picture_archive_text_owner_occurrence(occurrence_id)
);
CREATE TABLE profile_picture_archive_text_candidate_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, resolution_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 relative_path TEXT NOT NULL, locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')),
 shard_tier TEXT NOT NULL CHECK(shard_tier IN ('main','medium')), pair_ordinal INTEGER NOT NULL CHECK(pair_ordinal>=0),
 pointer TEXT NOT NULL, key_text TEXT NOT NULL, raw_text TEXT NOT NULL,
 raw_key_sha256 TEXT NOT NULL CHECK(length(raw_key_sha256)=64 AND raw_key_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_text_sha256 TEXT NOT NULL CHECK(length(raw_text_sha256)=64 AND raw_text_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_record_sha256 TEXT NOT NULL CHECK(length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(resolution_occurrence_id) REFERENCES profile_picture_archive_text_resolution_occurrence(occurrence_id)
);
CREATE TABLE profile_picture_archive_opaque_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 raw_json TEXT NOT NULL CHECK(json_valid(raw_json)), resolution_status TEXT NOT NULL CHECK(resolution_status='opaque_unresolved'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES profile_picture_archive_row_occurrence(occurrence_id)
);
CREATE TABLE profile_picture_archive_asset_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 raw_json TEXT, resolution_status TEXT NOT NULL CHECK(resolution_status IN ('raw_observed','missing_field','empty_slot')),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES profile_picture_archive_row_occurrence(occurrence_id)
);
CREATE INDEX idx_m5c_profile_picture_archive_row ON profile_picture_archive_row_occurrence (source_file_path, source_ordinal, source_record_pointer, occurrence_id);
CREATE INDEX idx_m5c_profile_picture_archive_raw ON profile_picture_archive_raw_field_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_profile_picture_archive_relation ON profile_picture_archive_relationship_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_profile_picture_archive_candidate ON profile_picture_archive_relationship_candidate_occurrence (relationship_occurrence_id, candidate_ordinal, occurrence_id);
CREATE INDEX idx_m5c_profile_picture_archive_owner ON profile_picture_archive_text_owner_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_profile_picture_archive_resolution ON profile_picture_archive_text_resolution_occurrence (owner_occurrence_id, locale, occurrence_id);
CREATE INDEX idx_m5c_profile_picture_archive_text ON profile_picture_archive_text_candidate_occurrence (relative_path, pair_ordinal, occurrence_id);
CREATE INDEX idx_m5c_profile_picture_archive_opaque ON profile_picture_archive_opaque_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_profile_picture_archive_asset ON profile_picture_archive_asset_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE TRIGGER trg_profile_picture_archive_row_immutable_update BEFORE UPDATE ON profile_picture_archive_row_occurrence BEGIN SELECT RAISE(ABORT,'profile_picture_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_picture_archive_row_immutable_delete BEFORE DELETE ON profile_picture_archive_row_occurrence BEGIN SELECT RAISE(ABORT,'profile_picture_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_picture_archive_raw_immutable_update BEFORE UPDATE ON profile_picture_archive_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'profile_picture_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_picture_archive_raw_immutable_delete BEFORE DELETE ON profile_picture_archive_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'profile_picture_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_picture_archive_rel_immutable_update BEFORE UPDATE ON profile_picture_archive_relationship_occurrence BEGIN SELECT RAISE(ABORT,'profile_picture_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_picture_archive_rel_immutable_delete BEFORE DELETE ON profile_picture_archive_relationship_occurrence BEGIN SELECT RAISE(ABORT,'profile_picture_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_picture_archive_cand_immutable_update BEFORE UPDATE ON profile_picture_archive_relationship_candidate_occurrence BEGIN SELECT RAISE(ABORT,'profile_picture_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_picture_archive_cand_immutable_delete BEFORE DELETE ON profile_picture_archive_relationship_candidate_occurrence BEGIN SELECT RAISE(ABORT,'profile_picture_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_picture_archive_owner_immutable_update BEFORE UPDATE ON profile_picture_archive_text_owner_occurrence BEGIN SELECT RAISE(ABORT,'profile_picture_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_picture_archive_owner_immutable_delete BEFORE DELETE ON profile_picture_archive_text_owner_occurrence BEGIN SELECT RAISE(ABORT,'profile_picture_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_picture_archive_res_immutable_update BEFORE UPDATE ON profile_picture_archive_text_resolution_occurrence BEGIN SELECT RAISE(ABORT,'profile_picture_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_picture_archive_res_immutable_delete BEFORE DELETE ON profile_picture_archive_text_resolution_occurrence BEGIN SELECT RAISE(ABORT,'profile_picture_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_picture_archive_text_immutable_update BEFORE UPDATE ON profile_picture_archive_text_candidate_occurrence BEGIN SELECT RAISE(ABORT,'profile_picture_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_picture_archive_text_immutable_delete BEFORE DELETE ON profile_picture_archive_text_candidate_occurrence BEGIN SELECT RAISE(ABORT,'profile_picture_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_picture_archive_opaque_immutable_update BEFORE UPDATE ON profile_picture_archive_opaque_occurrence BEGIN SELECT RAISE(ABORT,'profile_picture_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_picture_archive_opaque_immutable_delete BEFORE DELETE ON profile_picture_archive_opaque_occurrence BEGIN SELECT RAISE(ABORT,'profile_picture_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_picture_archive_asset_immutable_update BEFORE UPDATE ON profile_picture_archive_asset_occurrence BEGIN SELECT RAISE(ABORT,'profile_picture_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_picture_archive_asset_immutable_delete BEFORE DELETE ON profile_picture_archive_asset_occurrence BEGIN SELECT RAISE(ABORT,'profile_picture_archive evidence is immutable'); END;

-- profile_frame_archive: row, raw, owner, resolution, text, opaque, asset
CREATE TABLE profile_frame_archive_row_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, family TEXT NOT NULL,
 family_contract_version TEXT NOT NULL, ingest_run_id TEXT NOT NULL,
 snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_file_path TEXT NOT NULL, source_file_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_record_pointer TEXT NOT NULL,
 source_r0_physical_pointer TEXT NOT NULL, source_ordinal INTEGER NOT NULL CHECK(source_ordinal>=0),
 raw_record_json TEXT NOT NULL, raw_record_sha256 TEXT NOT NULL CHECK(length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_record_lexical_sha256 TEXT NOT NULL CHECK(length(raw_record_lexical_sha256)=64 AND raw_record_lexical_sha256 NOT GLOB '*[^0-9a-f]*'),
 native_identity_json TEXT NOT NULL CHECK(json_valid(native_identity_json)),
 record_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json))
);
CREATE TABLE profile_frame_archive_raw_field_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 field_presence TEXT NOT NULL CHECK(field_presence IN ('present','missing')),
 raw_json TEXT, resolution_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 CHECK((field_presence='present' AND raw_json IS NOT NULL) OR (field_presence='missing' AND raw_json IS NULL)),
 FOREIGN KEY(row_occurrence_id) REFERENCES profile_frame_archive_row_occurrence(occurrence_id)
);
CREATE TABLE profile_frame_archive_text_owner_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 owner_hash_json TEXT NOT NULL CHECK(json_valid(owner_hash_json)), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES profile_frame_archive_row_occurrence(occurrence_id)
);
CREATE TABLE profile_frame_archive_text_resolution_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, owner_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')), resolution_status TEXT NOT NULL CHECK(resolution_status IN ('missing','resolved_unique','ambiguous')),
 candidate_count INTEGER NOT NULL CHECK(candidate_count>=0), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(owner_occurrence_id) REFERENCES profile_frame_archive_text_owner_occurrence(occurrence_id)
);
CREATE TABLE profile_frame_archive_text_candidate_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, resolution_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 relative_path TEXT NOT NULL, locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')),
 shard_tier TEXT NOT NULL CHECK(shard_tier IN ('main','medium')), pair_ordinal INTEGER NOT NULL CHECK(pair_ordinal>=0),
 pointer TEXT NOT NULL, key_text TEXT NOT NULL, raw_text TEXT NOT NULL,
 raw_key_sha256 TEXT NOT NULL CHECK(length(raw_key_sha256)=64 AND raw_key_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_text_sha256 TEXT NOT NULL CHECK(length(raw_text_sha256)=64 AND raw_text_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_record_sha256 TEXT NOT NULL CHECK(length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(resolution_occurrence_id) REFERENCES profile_frame_archive_text_resolution_occurrence(occurrence_id)
);
CREATE TABLE profile_frame_archive_opaque_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 raw_json TEXT NOT NULL CHECK(json_valid(raw_json)), resolution_status TEXT NOT NULL CHECK(resolution_status='opaque_unresolved'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES profile_frame_archive_row_occurrence(occurrence_id)
);
CREATE TABLE profile_frame_archive_asset_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 raw_json TEXT, resolution_status TEXT NOT NULL CHECK(resolution_status IN ('raw_observed','missing_field','empty_slot')),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES profile_frame_archive_row_occurrence(occurrence_id)
);
CREATE INDEX idx_m5c_profile_frame_archive_row ON profile_frame_archive_row_occurrence (source_file_path, source_ordinal, source_record_pointer, occurrence_id);
CREATE INDEX idx_m5c_profile_frame_archive_raw ON profile_frame_archive_raw_field_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_profile_frame_archive_owner ON profile_frame_archive_text_owner_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_profile_frame_archive_resolution ON profile_frame_archive_text_resolution_occurrence (owner_occurrence_id, locale, occurrence_id);
CREATE INDEX idx_m5c_profile_frame_archive_text ON profile_frame_archive_text_candidate_occurrence (relative_path, pair_ordinal, occurrence_id);
CREATE INDEX idx_m5c_profile_frame_archive_opaque ON profile_frame_archive_opaque_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_profile_frame_archive_asset ON profile_frame_archive_asset_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE TRIGGER trg_profile_frame_archive_row_immutable_update BEFORE UPDATE ON profile_frame_archive_row_occurrence BEGIN SELECT RAISE(ABORT,'profile_frame_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_frame_archive_row_immutable_delete BEFORE DELETE ON profile_frame_archive_row_occurrence BEGIN SELECT RAISE(ABORT,'profile_frame_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_frame_archive_raw_immutable_update BEFORE UPDATE ON profile_frame_archive_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'profile_frame_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_frame_archive_raw_immutable_delete BEFORE DELETE ON profile_frame_archive_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'profile_frame_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_frame_archive_owner_immutable_update BEFORE UPDATE ON profile_frame_archive_text_owner_occurrence BEGIN SELECT RAISE(ABORT,'profile_frame_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_frame_archive_owner_immutable_delete BEFORE DELETE ON profile_frame_archive_text_owner_occurrence BEGIN SELECT RAISE(ABORT,'profile_frame_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_frame_archive_res_immutable_update BEFORE UPDATE ON profile_frame_archive_text_resolution_occurrence BEGIN SELECT RAISE(ABORT,'profile_frame_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_frame_archive_res_immutable_delete BEFORE DELETE ON profile_frame_archive_text_resolution_occurrence BEGIN SELECT RAISE(ABORT,'profile_frame_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_frame_archive_text_immutable_update BEFORE UPDATE ON profile_frame_archive_text_candidate_occurrence BEGIN SELECT RAISE(ABORT,'profile_frame_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_frame_archive_text_immutable_delete BEFORE DELETE ON profile_frame_archive_text_candidate_occurrence BEGIN SELECT RAISE(ABORT,'profile_frame_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_frame_archive_opaque_immutable_update BEFORE UPDATE ON profile_frame_archive_opaque_occurrence BEGIN SELECT RAISE(ABORT,'profile_frame_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_frame_archive_opaque_immutable_delete BEFORE DELETE ON profile_frame_archive_opaque_occurrence BEGIN SELECT RAISE(ABORT,'profile_frame_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_frame_archive_asset_immutable_update BEFORE UPDATE ON profile_frame_archive_asset_occurrence BEGIN SELECT RAISE(ABORT,'profile_frame_archive evidence is immutable'); END;
CREATE TRIGGER trg_profile_frame_archive_asset_immutable_delete BEFORE DELETE ON profile_frame_archive_asset_occurrence BEGIN SELECT RAISE(ABORT,'profile_frame_archive evidence is immutable'); END;

-- gadget_title_archive: row, raw, relation, candidate, owner, resolution, text
CREATE TABLE gadget_title_archive_row_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, family TEXT NOT NULL,
 family_contract_version TEXT NOT NULL, ingest_run_id TEXT NOT NULL,
 snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_file_path TEXT NOT NULL, source_file_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_record_pointer TEXT NOT NULL,
 source_r0_physical_pointer TEXT NOT NULL, source_ordinal INTEGER NOT NULL CHECK(source_ordinal>=0),
 raw_record_json TEXT NOT NULL, raw_record_sha256 TEXT NOT NULL CHECK(length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_record_lexical_sha256 TEXT NOT NULL CHECK(length(raw_record_lexical_sha256)=64 AND raw_record_lexical_sha256 NOT GLOB '*[^0-9a-f]*'),
 native_identity_json TEXT NOT NULL CHECK(json_valid(native_identity_json)),
 record_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json))
);
CREATE TABLE gadget_title_archive_raw_field_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 field_presence TEXT NOT NULL CHECK(field_presence IN ('present','missing')),
 raw_json TEXT, resolution_status TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 CHECK((field_presence='present' AND raw_json IS NOT NULL) OR (field_presence='missing' AND raw_json IS NULL)),
 FOREIGN KEY(row_occurrence_id) REFERENCES gadget_title_archive_row_occurrence(occurrence_id)
);
CREATE TABLE gadget_title_archive_relationship_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, relation_field TEXT NOT NULL,
 raw_value_json TEXT, target_source_file TEXT NOT NULL, target_field TEXT NOT NULL,
 condition_json TEXT NOT NULL CHECK(json_valid(condition_json)),
 resolution_status TEXT NOT NULL CHECK(resolution_status IN ('missing_field','empty_slot','missing_target','dangling','ambiguous','resolved_unique','resolved_candidate_set','unresolved','opaque_unresolved','not_applicable')),
 candidate_count INTEGER NOT NULL CHECK(candidate_count>=0), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES gadget_title_archive_row_occurrence(occurrence_id)
);
CREATE TABLE gadget_title_archive_relationship_candidate_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, relationship_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_file_id TEXT NOT NULL, source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL,
 candidate_ordinal INTEGER NOT NULL CHECK(candidate_ordinal>=0), target_source_file TEXT NOT NULL,
 target_source_row_ordinal INTEGER NOT NULL CHECK(target_source_row_ordinal>=0), target_pointer TEXT NOT NULL,
 target_field TEXT NOT NULL, target_value_json TEXT NOT NULL CHECK(json_valid(target_value_json)),
 target_raw_record_sha256 TEXT NOT NULL CHECK(length(target_raw_record_sha256)=64 AND target_raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 target_lexical_sha256 TEXT NOT NULL CHECK(length(target_lexical_sha256)=64 AND target_lexical_sha256 NOT GLOB '*[^0-9a-f]*'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(relationship_occurrence_id) REFERENCES gadget_title_archive_relationship_occurrence(occurrence_id)
);
CREATE TABLE gadget_title_archive_text_owner_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, row_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 source_record_id TEXT NOT NULL, source_pointer TEXT NOT NULL, field_path TEXT NOT NULL,
 owner_hash_json TEXT NOT NULL CHECK(json_valid(owner_hash_json)), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(row_occurrence_id) REFERENCES gadget_title_archive_row_occurrence(occurrence_id)
);
CREATE TABLE gadget_title_archive_text_resolution_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, owner_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')), resolution_status TEXT NOT NULL CHECK(resolution_status IN ('missing','resolved_unique','ambiguous')),
 candidate_count INTEGER NOT NULL CHECK(candidate_count>=0), metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(owner_occurrence_id) REFERENCES gadget_title_archive_text_owner_occurrence(occurrence_id)
);
CREATE TABLE gadget_title_archive_text_candidate_occurrence (
 occurrence_id TEXT PRIMARY KEY NOT NULL, resolution_occurrence_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
 relative_path TEXT NOT NULL, locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')),
 shard_tier TEXT NOT NULL CHECK(shard_tier IN ('main','medium')), pair_ordinal INTEGER NOT NULL CHECK(pair_ordinal>=0),
 pointer TEXT NOT NULL, key_text TEXT NOT NULL, raw_text TEXT NOT NULL,
 raw_key_sha256 TEXT NOT NULL CHECK(length(raw_key_sha256)=64 AND raw_key_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_text_sha256 TEXT NOT NULL CHECK(length(raw_text_sha256)=64 AND raw_text_sha256 NOT GLOB '*[^0-9a-f]*'),
 raw_record_sha256 TEXT NOT NULL CHECK(length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 FOREIGN KEY(resolution_occurrence_id) REFERENCES gadget_title_archive_text_resolution_occurrence(occurrence_id)
);
CREATE INDEX idx_m5c_gadget_title_archive_row ON gadget_title_archive_row_occurrence (source_file_path, source_ordinal, source_record_pointer, occurrence_id);
CREATE INDEX idx_m5c_gadget_title_archive_raw ON gadget_title_archive_raw_field_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_gadget_title_archive_relation ON gadget_title_archive_relationship_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_gadget_title_archive_candidate ON gadget_title_archive_relationship_candidate_occurrence (relationship_occurrence_id, candidate_ordinal, occurrence_id);
CREATE INDEX idx_m5c_gadget_title_archive_owner ON gadget_title_archive_text_owner_occurrence (row_occurrence_id, source_pointer, occurrence_id);
CREATE INDEX idx_m5c_gadget_title_archive_resolution ON gadget_title_archive_text_resolution_occurrence (owner_occurrence_id, locale, occurrence_id);
CREATE INDEX idx_m5c_gadget_title_archive_text ON gadget_title_archive_text_candidate_occurrence (relative_path, pair_ordinal, occurrence_id);
CREATE TRIGGER trg_gadget_title_archive_row_immutable_update BEFORE UPDATE ON gadget_title_archive_row_occurrence BEGIN SELECT RAISE(ABORT,'gadget_title_archive evidence is immutable'); END;
CREATE TRIGGER trg_gadget_title_archive_row_immutable_delete BEFORE DELETE ON gadget_title_archive_row_occurrence BEGIN SELECT RAISE(ABORT,'gadget_title_archive evidence is immutable'); END;
CREATE TRIGGER trg_gadget_title_archive_raw_immutable_update BEFORE UPDATE ON gadget_title_archive_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'gadget_title_archive evidence is immutable'); END;
CREATE TRIGGER trg_gadget_title_archive_raw_immutable_delete BEFORE DELETE ON gadget_title_archive_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'gadget_title_archive evidence is immutable'); END;
CREATE TRIGGER trg_gadget_title_archive_rel_immutable_update BEFORE UPDATE ON gadget_title_archive_relationship_occurrence BEGIN SELECT RAISE(ABORT,'gadget_title_archive evidence is immutable'); END;
CREATE TRIGGER trg_gadget_title_archive_rel_immutable_delete BEFORE DELETE ON gadget_title_archive_relationship_occurrence BEGIN SELECT RAISE(ABORT,'gadget_title_archive evidence is immutable'); END;
CREATE TRIGGER trg_gadget_title_archive_cand_immutable_update BEFORE UPDATE ON gadget_title_archive_relationship_candidate_occurrence BEGIN SELECT RAISE(ABORT,'gadget_title_archive evidence is immutable'); END;
CREATE TRIGGER trg_gadget_title_archive_cand_immutable_delete BEFORE DELETE ON gadget_title_archive_relationship_candidate_occurrence BEGIN SELECT RAISE(ABORT,'gadget_title_archive evidence is immutable'); END;
CREATE TRIGGER trg_gadget_title_archive_owner_immutable_update BEFORE UPDATE ON gadget_title_archive_text_owner_occurrence BEGIN SELECT RAISE(ABORT,'gadget_title_archive evidence is immutable'); END;
CREATE TRIGGER trg_gadget_title_archive_owner_immutable_delete BEFORE DELETE ON gadget_title_archive_text_owner_occurrence BEGIN SELECT RAISE(ABORT,'gadget_title_archive evidence is immutable'); END;
CREATE TRIGGER trg_gadget_title_archive_res_immutable_update BEFORE UPDATE ON gadget_title_archive_text_resolution_occurrence BEGIN SELECT RAISE(ABORT,'gadget_title_archive evidence is immutable'); END;
CREATE TRIGGER trg_gadget_title_archive_res_immutable_delete BEFORE DELETE ON gadget_title_archive_text_resolution_occurrence BEGIN SELECT RAISE(ABORT,'gadget_title_archive evidence is immutable'); END;
CREATE TRIGGER trg_gadget_title_archive_text_immutable_update BEFORE UPDATE ON gadget_title_archive_text_candidate_occurrence BEGIN SELECT RAISE(ABORT,'gadget_title_archive evidence is immutable'); END;
CREATE TRIGGER trg_gadget_title_archive_text_immutable_delete BEFORE DELETE ON gadget_title_archive_text_candidate_occurrence BEGIN SELECT RAISE(ABORT,'gadget_title_archive evidence is immutable'); END;
