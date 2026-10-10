-- Batch M4 wave 01 family-specific R1 projection tables.
--
-- These tables are deliberately separate per family.  The migration contains
-- only the frozen lexical row-occurrence shape used by the M3 shards; it does
-- not create a common entity, relation, text owner, or cross-family identity.

PRAGMA foreign_keys = ON;

CREATE TABLE achievement_archive_row_occurrence (
    occurrence_id TEXT PRIMARY KEY NOT NULL,
    family TEXT NOT NULL,
    family_contract_version TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    source_file_path TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL,
    source_ordinal INTEGER NOT NULL CHECK (source_ordinal >= 0),
    raw_record_json TEXT NOT NULL,
    raw_record_sha256 TEXT NOT NULL,
    raw_key_observations_json TEXT NOT NULL,
    record_status TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);
CREATE INDEX idx_achievement_archive_source_ordinal
    ON achievement_archive_row_occurrence (source_file_path,source_ordinal);
CREATE TRIGGER trg_achievement_archive_row_immutable_update
BEFORE UPDATE ON achievement_archive_row_occurrence BEGIN
    SELECT RAISE(ABORT,'achievement archive evidence is immutable');
END;
CREATE TRIGGER trg_achievement_archive_row_immutable_delete
BEFORE DELETE ON achievement_archive_row_occurrence BEGIN
    SELECT RAISE(ABORT,'achievement archive evidence is immutable');
END;

CREATE TABLE activity_archive_row_occurrence (
    occurrence_id TEXT PRIMARY KEY NOT NULL,
    family TEXT NOT NULL,
    family_contract_version TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    source_file_path TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL,
    source_ordinal INTEGER NOT NULL CHECK (source_ordinal >= 0),
    raw_record_json TEXT NOT NULL,
    raw_record_sha256 TEXT NOT NULL,
    raw_key_observations_json TEXT NOT NULL,
    record_status TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);
CREATE INDEX idx_activity_archive_source_ordinal
    ON activity_archive_row_occurrence (source_file_path,source_ordinal);
CREATE TRIGGER trg_activity_archive_row_immutable_update
BEFORE UPDATE ON activity_archive_row_occurrence BEGIN
    SELECT RAISE(ABORT,'activity archive evidence is immutable');
END;
CREATE TRIGGER trg_activity_archive_row_immutable_delete
BEFORE DELETE ON activity_archive_row_occurrence BEGIN
    SELECT RAISE(ABORT,'activity archive evidence is immutable');
END;

CREATE TABLE avatar_config_archive_row_occurrence (
    occurrence_id TEXT PRIMARY KEY NOT NULL,
    family TEXT NOT NULL,
    family_contract_version TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    source_file_path TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL,
    source_ordinal INTEGER NOT NULL CHECK (source_ordinal >= 0),
    raw_record_json TEXT NOT NULL,
    raw_record_sha256 TEXT NOT NULL,
    raw_key_observations_json TEXT NOT NULL,
    record_status TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);
CREATE INDEX idx_avatar_config_archive_source_ordinal
    ON avatar_config_archive_row_occurrence (source_file_path,source_ordinal);
CREATE TRIGGER trg_avatar_config_archive_row_immutable_update
BEFORE UPDATE ON avatar_config_archive_row_occurrence BEGIN
    SELECT RAISE(ABORT,'avatar config archive evidence is immutable');
END;
CREATE TRIGGER trg_avatar_config_archive_row_immutable_delete
BEFORE DELETE ON avatar_config_archive_row_occurrence BEGIN
    SELECT RAISE(ABORT,'avatar config archive evidence is immutable');
END;

CREATE TABLE dungeon_archive_row_occurrence (
    occurrence_id TEXT PRIMARY KEY NOT NULL,
    family TEXT NOT NULL,
    family_contract_version TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    source_file_path TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL,
    source_ordinal INTEGER NOT NULL CHECK (source_ordinal >= 0),
    raw_record_json TEXT NOT NULL,
    raw_record_sha256 TEXT NOT NULL,
    raw_key_observations_json TEXT NOT NULL,
    record_status TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);
CREATE INDEX idx_dungeon_archive_source_ordinal
    ON dungeon_archive_row_occurrence (source_file_path,source_ordinal);
CREATE TRIGGER trg_dungeon_archive_row_immutable_update
BEFORE UPDATE ON dungeon_archive_row_occurrence BEGIN
    SELECT RAISE(ABORT,'dungeon archive evidence is immutable');
END;
CREATE TRIGGER trg_dungeon_archive_row_immutable_delete
BEFORE DELETE ON dungeon_archive_row_occurrence BEGIN
    SELECT RAISE(ABORT,'dungeon archive evidence is immutable');
END;

CREATE TABLE material_config_archive_row_occurrence (
    occurrence_id TEXT PRIMARY KEY NOT NULL,
    family TEXT NOT NULL,
    family_contract_version TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    source_file_path TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL,
    source_ordinal INTEGER NOT NULL CHECK (source_ordinal >= 0),
    raw_record_json TEXT NOT NULL,
    raw_record_sha256 TEXT NOT NULL,
    raw_key_observations_json TEXT NOT NULL,
    record_status TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);
CREATE INDEX idx_material_config_archive_source_ordinal
    ON material_config_archive_row_occurrence (source_file_path,source_ordinal);
CREATE TRIGGER trg_material_config_archive_row_immutable_update
BEFORE UPDATE ON material_config_archive_row_occurrence BEGIN
    SELECT RAISE(ABORT,'material config archive evidence is immutable');
END;
CREATE TRIGGER trg_material_config_archive_row_immutable_delete
BEFORE DELETE ON material_config_archive_row_occurrence BEGIN
    SELECT RAISE(ABORT,'material config archive evidence is immutable');
END;

CREATE TABLE monster_archive_row_occurrence (
    occurrence_id TEXT PRIMARY KEY NOT NULL,
    family TEXT NOT NULL,
    family_contract_version TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    source_file_path TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL,
    source_ordinal INTEGER NOT NULL CHECK (source_ordinal >= 0),
    raw_record_json TEXT NOT NULL,
    raw_record_sha256 TEXT NOT NULL,
    raw_key_observations_json TEXT NOT NULL,
    record_status TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);
CREATE INDEX idx_monster_archive_source_ordinal
    ON monster_archive_row_occurrence (source_file_path,source_ordinal);
CREATE TRIGGER trg_monster_archive_row_immutable_update
BEFORE UPDATE ON monster_archive_row_occurrence BEGIN
    SELECT RAISE(ABORT,'monster archive evidence is immutable');
END;
CREATE TRIGGER trg_monster_archive_row_immutable_delete
BEFORE DELETE ON monster_archive_row_occurrence BEGIN
    SELECT RAISE(ABORT,'monster archive evidence is immutable');
END;

CREATE TABLE npc_archive_row_occurrence (
    occurrence_id TEXT PRIMARY KEY NOT NULL,
    family TEXT NOT NULL,
    family_contract_version TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    source_file_path TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL,
    source_ordinal INTEGER NOT NULL CHECK (source_ordinal >= 0),
    raw_record_json TEXT NOT NULL,
    raw_record_sha256 TEXT NOT NULL,
    raw_key_observations_json TEXT NOT NULL,
    record_status TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);
CREATE INDEX idx_npc_archive_source_ordinal
    ON npc_archive_row_occurrence (source_file_path,source_ordinal);
CREATE TRIGGER trg_npc_archive_row_immutable_update
BEFORE UPDATE ON npc_archive_row_occurrence BEGIN
    SELECT RAISE(ABORT,'npc archive evidence is immutable');
END;
CREATE TRIGGER trg_npc_archive_row_immutable_delete
BEFORE DELETE ON npc_archive_row_occurrence BEGIN
    SELECT RAISE(ABORT,'npc archive evidence is immutable');
END;

CREATE TABLE reliquary_config_archive_row_occurrence (
    occurrence_id TEXT PRIMARY KEY NOT NULL,
    family TEXT NOT NULL,
    family_contract_version TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    source_file_path TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL,
    source_ordinal INTEGER NOT NULL CHECK (source_ordinal >= 0),
    raw_record_json TEXT NOT NULL,
    raw_record_sha256 TEXT NOT NULL,
    raw_key_observations_json TEXT NOT NULL,
    record_status TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);
CREATE INDEX idx_reliquary_config_archive_source_ordinal
    ON reliquary_config_archive_row_occurrence (source_file_path,source_ordinal);
CREATE TRIGGER trg_reliquary_config_archive_row_immutable_update
BEFORE UPDATE ON reliquary_config_archive_row_occurrence BEGIN
    SELECT RAISE(ABORT,'reliquary config archive evidence is immutable');
END;
CREATE TRIGGER trg_reliquary_config_archive_row_immutable_delete
BEFORE DELETE ON reliquary_config_archive_row_occurrence BEGIN
    SELECT RAISE(ABORT,'reliquary config archive evidence is immutable');
END;

CREATE TABLE scene_archive_row_occurrence (
    occurrence_id TEXT PRIMARY KEY NOT NULL,
    family TEXT NOT NULL,
    family_contract_version TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    source_file_path TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL,
    source_ordinal INTEGER NOT NULL CHECK (source_ordinal >= 0),
    raw_record_json TEXT NOT NULL,
    raw_record_sha256 TEXT NOT NULL,
    raw_key_observations_json TEXT NOT NULL,
    record_status TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);
CREATE INDEX idx_scene_archive_source_ordinal
    ON scene_archive_row_occurrence (source_file_path,source_ordinal);
CREATE TRIGGER trg_scene_archive_row_immutable_update
BEFORE UPDATE ON scene_archive_row_occurrence BEGIN
    SELECT RAISE(ABORT,'scene archive evidence is immutable');
END;
CREATE TRIGGER trg_scene_archive_row_immutable_delete
BEFORE DELETE ON scene_archive_row_occurrence BEGIN
    SELECT RAISE(ABORT,'scene archive evidence is immutable');
END;

CREATE TABLE weapon_config_archive_row_occurrence (
    occurrence_id TEXT PRIMARY KEY NOT NULL,
    family TEXT NOT NULL,
    family_contract_version TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    source_file_path TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL,
    source_ordinal INTEGER NOT NULL CHECK (source_ordinal >= 0),
    raw_record_json TEXT NOT NULL,
    raw_record_sha256 TEXT NOT NULL,
    raw_key_observations_json TEXT NOT NULL,
    record_status TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);
CREATE INDEX idx_weapon_config_archive_source_ordinal
    ON weapon_config_archive_row_occurrence (source_file_path,source_ordinal);
CREATE TRIGGER trg_weapon_config_archive_row_immutable_update
BEFORE UPDATE ON weapon_config_archive_row_occurrence BEGIN
    SELECT RAISE(ABORT,'weapon config archive evidence is immutable');
END;
CREATE TRIGGER trg_weapon_config_archive_row_immutable_delete
BEFORE DELETE ON weapon_config_archive_row_occurrence BEGIN
    SELECT RAISE(ABORT,'weapon config archive evidence is immutable');
END;
