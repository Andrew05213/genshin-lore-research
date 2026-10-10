-- Phase 4G2 additive Avatar Codex bounded raw-evidence canary schema.
-- Every occurrence is scoped to one snapshot/parser/run/source coordinate.
-- Native IDs, raw values, checksums, and lookup candidates never create
-- logical Avatar, Character, or Codex identity.

PRAGMA foreign_keys = ON;

CREATE TABLE avatar_codex_row_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    source_file_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL CHECK (substr(source_pointer,1,1)='/'),
    evidence_kind TEXT NOT NULL CHECK (evidence_kind='avatar_codex_entry'),
    table_name TEXT NOT NULL CHECK (table_name='AvatarCodexExcelConfigData'),
    row_index INTEGER NOT NULL CHECK (row_index>=0),
    native_locator_field TEXT NOT NULL CHECK (native_locator_field='avatarId'),
    native_locator_value TEXT NOT NULL CHECK (
        length(native_locator_value)>0 AND native_locator_value NOT GLOB '*[^0-9]*'
    ),
    raw_checksum TEXT NOT NULL CHECK (
        length(raw_checksum)=64 AND raw_checksum NOT GLOB '*[^0-9a-f]*'
    ),
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(ingest_run_id,source_record_id),
    UNIQUE(id,ingest_run_id,snapshot_id,parser_build_id,source_record_id),
    FOREIGN KEY(ingest_run_id,snapshot_id,parser_build_id)
        REFERENCES ingest_run(id,snapshot_id,parser_build_id),
    FOREIGN KEY(source_file_id,snapshot_id)
        REFERENCES source_file(id,snapshot_id),
    FOREIGN KEY(source_record_id,source_file_id,source_pointer)
        REFERENCES source_record(id,source_file_id,pointer)
);

CREATE TABLE avatar_codex_raw_field_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    row_occurrence_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL CHECK (substr(source_pointer,1,1)='/'),
    field_name TEXT NOT NULL CHECK (field_name IN (
        'avatarId','beginTime','sortFactor','sortId','hideWhenDontHave','FMDEJPDNFKB'
    )),
    field_presence TEXT NOT NULL CHECK (field_presence IN ('present','missing')),
    raw_json TEXT CHECK (raw_json IS NULL OR json_valid(raw_json)),
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json) AND json_type(metadata_json)='object'),
    CHECK ((field_presence='present' AND raw_json IS NOT NULL)
        OR (field_presence='missing' AND raw_json IS NULL)),
    UNIQUE(ingest_run_id,row_occurrence_id,source_pointer),
    FOREIGN KEY(row_occurrence_id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
        REFERENCES avatar_codex_row_occurrence(id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
);

CREATE TABLE avatar_codex_relationship_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    source_row_occurrence_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL CHECK (substr(source_pointer,1,1)='/'),
    field_name TEXT NOT NULL CHECK (field_name='avatarId'),
    raw_value_json TEXT NOT NULL CHECK (json_valid(raw_value_json)),
    target_table TEXT NOT NULL CHECK (target_table='AvatarExcelConfigData'),
    target_field TEXT NOT NULL CHECK (target_field='id'),
    resolution_status TEXT NOT NULL CHECK (resolution_status IN (
        'resolved_unique','ambiguous','dangling','missing','invalid','unresolved'
    )),
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(ingest_run_id,source_row_occurrence_id,source_pointer),
    UNIQUE(id,ingest_run_id,snapshot_id,parser_build_id),
    FOREIGN KEY(source_row_occurrence_id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
        REFERENCES avatar_codex_row_occurrence(id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
);

CREATE TABLE avatar_codex_relationship_candidate_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    relationship_occurrence_id TEXT NOT NULL,
    source_file_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL CHECK (substr(source_pointer,1,1)='/'),
    candidate_ordinal INTEGER NOT NULL CHECK (candidate_ordinal>=0),
    target_table TEXT NOT NULL CHECK (target_table='AvatarExcelConfigData'),
    target_field TEXT NOT NULL CHECK (target_field='id'),
    target_raw_value_json TEXT NOT NULL CHECK (json_valid(target_raw_value_json)),
    raw_checksum TEXT NOT NULL CHECK (
        length(raw_checksum)=64 AND raw_checksum NOT GLOB '*[^0-9a-f]*'
    ),
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(relationship_occurrence_id,candidate_ordinal),
    UNIQUE(relationship_occurrence_id,source_record_id),
    FOREIGN KEY(relationship_occurrence_id,ingest_run_id,snapshot_id,parser_build_id)
        REFERENCES avatar_codex_relationship_occurrence(id,ingest_run_id,snapshot_id,parser_build_id),
    FOREIGN KEY(source_file_id,snapshot_id)
        REFERENCES source_file(id,snapshot_id),
    FOREIGN KEY(source_record_id,source_file_id,source_pointer)
        REFERENCES source_record(id,source_file_id,pointer)
);

CREATE TABLE avatar_codex_opaque_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    source_row_occurrence_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL CHECK (substr(source_pointer,1,1)='/'),
    field_name TEXT NOT NULL CHECK (field_name='FMDEJPDNFKB'),
    array_index INTEGER CHECK (array_index IS NULL OR array_index>=0),
    raw_value_json TEXT NOT NULL CHECK (json_valid(raw_value_json)),
    resolution_status TEXT NOT NULL CHECK (resolution_status='opaque_unresolved'),
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(ingest_run_id,source_row_occurrence_id,source_pointer),
    FOREIGN KEY(source_row_occurrence_id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
        REFERENCES avatar_codex_row_occurrence(id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
);

CREATE INDEX idx_avatar_codex_row_run_row
    ON avatar_codex_row_occurrence(ingest_run_id,row_index);
CREATE INDEX idx_avatar_codex_raw_run_row
    ON avatar_codex_raw_field_occurrence(ingest_run_id,row_occurrence_id);
CREATE INDEX idx_avatar_codex_relation_run_status
    ON avatar_codex_relationship_occurrence(ingest_run_id,resolution_status);
CREATE INDEX idx_avatar_codex_relation_candidate
    ON avatar_codex_relationship_candidate_occurrence(relationship_occurrence_id,candidate_ordinal);
CREATE INDEX idx_avatar_codex_opaque_run_field
    ON avatar_codex_opaque_occurrence(ingest_run_id,field_name);

CREATE TRIGGER trg_avatar_codex_row_insert_existing_immutable
BEFORE INSERT ON avatar_codex_row_occurrence
WHEN EXISTS (SELECT 1 FROM avatar_codex_row_occurrence WHERE id=NEW.id)
BEGIN SELECT RAISE(ABORT,'avatar codex evidence primary key is immutable'); END;
CREATE TRIGGER trg_avatar_codex_row_immutable
BEFORE UPDATE ON avatar_codex_row_occurrence
BEGIN SELECT RAISE(ABORT,'avatar codex evidence is immutable'); END;
CREATE TRIGGER trg_avatar_codex_row_delete_immutable
BEFORE DELETE ON avatar_codex_row_occurrence
BEGIN SELECT RAISE(ABORT,'avatar codex evidence is immutable'); END;
CREATE TRIGGER trg_avatar_codex_raw_insert_existing_immutable
BEFORE INSERT ON avatar_codex_raw_field_occurrence
WHEN EXISTS (SELECT 1 FROM avatar_codex_raw_field_occurrence WHERE id=NEW.id)
BEGIN SELECT RAISE(ABORT,'avatar codex evidence primary key is immutable'); END;
CREATE TRIGGER trg_avatar_codex_raw_immutable
BEFORE UPDATE ON avatar_codex_raw_field_occurrence
BEGIN SELECT RAISE(ABORT,'avatar codex evidence is immutable'); END;
CREATE TRIGGER trg_avatar_codex_raw_delete_immutable
BEFORE DELETE ON avatar_codex_raw_field_occurrence
BEGIN SELECT RAISE(ABORT,'avatar codex evidence is immutable'); END;
CREATE TRIGGER trg_avatar_codex_relationship_insert_existing_immutable
BEFORE INSERT ON avatar_codex_relationship_occurrence
WHEN EXISTS (SELECT 1 FROM avatar_codex_relationship_occurrence WHERE id=NEW.id)
BEGIN SELECT RAISE(ABORT,'avatar codex evidence primary key is immutable'); END;
CREATE TRIGGER trg_avatar_codex_relationship_immutable
BEFORE UPDATE ON avatar_codex_relationship_occurrence
BEGIN SELECT RAISE(ABORT,'avatar codex evidence is immutable'); END;
CREATE TRIGGER trg_avatar_codex_relationship_delete_immutable
BEFORE DELETE ON avatar_codex_relationship_occurrence
BEGIN SELECT RAISE(ABORT,'avatar codex evidence is immutable'); END;
CREATE TRIGGER trg_avatar_codex_candidate_insert_existing_immutable
BEFORE INSERT ON avatar_codex_relationship_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM avatar_codex_relationship_candidate_occurrence WHERE id=NEW.id)
BEGIN SELECT RAISE(ABORT,'avatar codex evidence primary key is immutable'); END;
CREATE TRIGGER trg_avatar_codex_candidate_immutable
BEFORE UPDATE ON avatar_codex_relationship_candidate_occurrence
BEGIN SELECT RAISE(ABORT,'avatar codex evidence is immutable'); END;
CREATE TRIGGER trg_avatar_codex_candidate_delete_immutable
BEFORE DELETE ON avatar_codex_relationship_candidate_occurrence
BEGIN SELECT RAISE(ABORT,'avatar codex evidence is immutable'); END;
CREATE TRIGGER trg_avatar_codex_opaque_insert_existing_immutable
BEFORE INSERT ON avatar_codex_opaque_occurrence
WHEN EXISTS (SELECT 1 FROM avatar_codex_opaque_occurrence WHERE id=NEW.id)
BEGIN SELECT RAISE(ABORT,'avatar codex evidence primary key is immutable'); END;
CREATE TRIGGER trg_avatar_codex_opaque_immutable
BEFORE UPDATE ON avatar_codex_opaque_occurrence
BEGIN SELECT RAISE(ABORT,'avatar codex evidence is immutable'); END;
CREATE TRIGGER trg_avatar_codex_opaque_delete_immutable
BEFORE DELETE ON avatar_codex_opaque_occurrence
BEGIN SELECT RAISE(ABORT,'avatar codex evidence is immutable'); END;
