-- Phase 4E2 additive World Archive bounded raw-evidence canary schema.
-- Every row is a physical occurrence scoped to one snapshot/parser/run.
-- No native ID, TextMap hash, checksum, text, image, or model path creates
-- logical identity or merges evidence.

PRAGMA foreign_keys = ON;

CREATE UNIQUE INDEX uq_world_archive_ingest_run_scope
    ON ingest_run(id, snapshot_id, parser_build_id);
CREATE UNIQUE INDEX uq_world_archive_source_file_snapshot
    ON source_file(id, snapshot_id);
CREATE UNIQUE INDEX uq_world_archive_source_record_file_pointer
    ON source_record(id, source_file_id, pointer);

CREATE TABLE world_archive_row_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    source_file_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL CHECK (substr(source_pointer,1,1)='/'),
    table_name TEXT NOT NULL CHECK (table_name IN (
        'AnimalCodexExcelConfigData','ViewCodexExcelConfigData'
    )),
    row_index INTEGER NOT NULL CHECK (row_index>=0),
    native_locator_field TEXT NOT NULL CHECK (native_locator_field='id'),
    native_locator_value TEXT NOT NULL CHECK (
        length(native_locator_value)>0 AND native_locator_value NOT GLOB '*[^0-9]*'
    ),
    raw_checksum TEXT NOT NULL CHECK (
        length(raw_checksum)=64 AND raw_checksum NOT GLOB '*[^0-9a-f]*'
    ),
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(ingest_run_id,source_record_id),
    UNIQUE(id,ingest_run_id,snapshot_id,parser_build_id),
    UNIQUE(id,ingest_run_id,snapshot_id,parser_build_id,source_record_id),
    FOREIGN KEY(ingest_run_id,snapshot_id,parser_build_id)
        REFERENCES ingest_run(id,snapshot_id,parser_build_id),
    FOREIGN KEY(source_file_id,snapshot_id) REFERENCES source_file(id,snapshot_id),
    FOREIGN KEY(source_record_id,source_file_id,source_pointer)
        REFERENCES source_record(id,source_file_id,pointer)
);

CREATE TABLE world_archive_raw_field_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    row_occurrence_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL CHECK (substr(source_pointer,1,1)='/'),
    field_name TEXT NOT NULL,
    field_kind TEXT NOT NULL CHECK (field_kind IN ('scalar','array')),
    raw_json TEXT NOT NULL CHECK (json_valid(raw_json)),
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(ingest_run_id,row_occurrence_id,source_pointer),
    FOREIGN KEY(row_occurrence_id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
        REFERENCES world_archive_row_occurrence(id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
);

CREATE TABLE world_archive_relationship_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    source_row_occurrence_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL CHECK (substr(source_pointer,1,1)='/'),
    field_name TEXT NOT NULL,
    relation_kind TEXT NOT NULL,
    raw_value_json TEXT CHECK (raw_value_json IS NULL OR json_valid(raw_value_json)),
    candidate_key_json TEXT CHECK (candidate_key_json IS NULL OR json_valid(candidate_key_json)),
    resolution_status TEXT NOT NULL CHECK (resolution_status IN (
        'resolved_unique','missing','dangling','ambiguous','unresolved','invalid'
    )),
    target_table TEXT NOT NULL,
    target_field TEXT NOT NULL,
    candidate_scopes_json TEXT NOT NULL CHECK (json_valid(candidate_scopes_json) AND json_type(candidate_scopes_json)='array'),
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(ingest_run_id,source_row_occurrence_id,source_pointer),
    FOREIGN KEY(source_row_occurrence_id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
        REFERENCES world_archive_row_occurrence(id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
);

CREATE TABLE world_archive_opaque_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    source_row_occurrence_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL CHECK (substr(source_pointer,1,1)='/'),
    field_name TEXT NOT NULL,
    array_index INTEGER CHECK (array_index IS NULL OR array_index>=0),
    raw_value_json TEXT NOT NULL CHECK (json_valid(raw_value_json)),
    resolution_status TEXT NOT NULL CHECK (resolution_status='opaque_unresolved'),
    reason TEXT NOT NULL,
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(ingest_run_id,source_row_occurrence_id,source_pointer),
    FOREIGN KEY(source_row_occurrence_id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
        REFERENCES world_archive_row_occurrence(id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
);

CREATE TABLE world_archive_text_owner_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    source_row_occurrence_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL CHECK (substr(source_pointer,1,1)='/'),
    field_name TEXT NOT NULL CHECK (field_name IN ('descTextMapHash','nameTextMapHash')),
    field_role TEXT NOT NULL CHECK (field_role IN ('description','name')),
    textmap_hash TEXT NOT NULL CHECK (textmap_hash NOT GLOB '*[^0-9]*'),
    raw_value_json TEXT NOT NULL CHECK (json_valid(raw_value_json)),
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(id,ingest_run_id,snapshot_id,parser_build_id),
    UNIQUE(ingest_run_id,source_row_occurrence_id,source_pointer),
    FOREIGN KEY(source_row_occurrence_id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
        REFERENCES world_archive_row_occurrence(id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
);

CREATE TABLE world_archive_text_resolution_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    owner_occurrence_id TEXT NOT NULL,
    source_row_occurrence_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL CHECK (substr(source_pointer,1,1)='/'),
    locale TEXT NOT NULL CHECK (locale IN ('CHS','EN','JP')),
    textmap_hash TEXT NOT NULL CHECK (textmap_hash NOT GLOB '*[^0-9]*'),
    resolution_status TEXT NOT NULL CHECK (resolution_status IN (
        'unique_main','unique_medium','both_equal','both_conflict','missing','invalid'
    )),
    raw_text TEXT,
    normalized_text TEXT,
    raw_checksum TEXT,
    normalized_checksum TEXT,
    candidate_scopes_json TEXT NOT NULL CHECK (json_valid(candidate_scopes_json) AND json_type(candidate_scopes_json)='array'),
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json) AND json_type(metadata_json)='object'),
    CHECK ((resolution_status IN ('unique_main','unique_medium','both_equal') AND raw_text IS NOT NULL AND normalized_text IS NOT NULL AND raw_checksum IS NOT NULL AND normalized_checksum IS NOT NULL)
        OR (resolution_status IN ('both_conflict','missing','invalid') AND raw_text IS NULL AND normalized_text IS NULL AND raw_checksum IS NULL AND normalized_checksum IS NULL)),
    UNIQUE(id,ingest_run_id,snapshot_id,parser_build_id),
    UNIQUE(ingest_run_id,owner_occurrence_id,locale),
    FOREIGN KEY(owner_occurrence_id,ingest_run_id,snapshot_id,parser_build_id)
        REFERENCES world_archive_text_owner_occurrence(id,ingest_run_id,snapshot_id,parser_build_id),
    FOREIGN KEY(source_row_occurrence_id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
        REFERENCES world_archive_row_occurrence(id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
);

CREATE TABLE world_archive_text_candidate_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    resolution_occurrence_id TEXT NOT NULL,
    source_file_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL CHECK (substr(source_pointer,1,1)='/'),
    shard_kind TEXT NOT NULL CHECK (shard_kind IN ('main','medium')),
    locale TEXT NOT NULL CHECK (locale IN ('CHS','EN','JP')),
    raw_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    raw_checksum TEXT NOT NULL,
    normalized_checksum TEXT NOT NULL,
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(resolution_occurrence_id,source_file_id,source_record_id,source_pointer),
    FOREIGN KEY(resolution_occurrence_id,ingest_run_id,snapshot_id,parser_build_id)
        REFERENCES world_archive_text_resolution_occurrence(id,ingest_run_id,snapshot_id,parser_build_id),
    FOREIGN KEY(source_file_id,snapshot_id) REFERENCES source_file(id,snapshot_id),
    FOREIGN KEY(source_record_id,source_file_id,source_pointer)
        REFERENCES source_record(id,source_file_id,pointer)
);

CREATE TABLE world_archive_asset_reference_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    source_row_occurrence_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL CHECK (substr(source_pointer,1,1)='/'),
    field_name TEXT NOT NULL CHECK (field_name IN ('modelPath','image')),
    asset_kind TEXT NOT NULL CHECK (asset_kind IN ('model_path','image')),
    raw_reference TEXT,
    reference_status TEXT NOT NULL CHECK (reference_status IN ('observed','missing','invalid')),
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json) AND json_type(metadata_json)='object'),
    CHECK ((reference_status='observed' AND raw_reference IS NOT NULL) OR (reference_status<>'observed' AND raw_reference IS NULL)),
    UNIQUE(ingest_run_id,source_row_occurrence_id,source_pointer),
    FOREIGN KEY(source_row_occurrence_id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
        REFERENCES world_archive_row_occurrence(id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
);

CREATE INDEX idx_world_archive_row_run_table ON world_archive_row_occurrence(ingest_run_id,table_name,row_index);
CREATE INDEX idx_world_archive_raw_run_row ON world_archive_raw_field_occurrence(ingest_run_id,row_occurrence_id);
CREATE INDEX idx_world_archive_relation_run_status ON world_archive_relationship_occurrence(ingest_run_id,resolution_status);
CREATE INDEX idx_world_archive_opaque_run_field ON world_archive_opaque_occurrence(ingest_run_id,field_name);
CREATE INDEX idx_world_archive_owner_run_field ON world_archive_text_owner_occurrence(ingest_run_id,field_name);
CREATE INDEX idx_world_archive_resolution_run_locale ON world_archive_text_resolution_occurrence(ingest_run_id,locale,resolution_status);
CREATE INDEX idx_world_archive_candidate_run_locale ON world_archive_text_candidate_occurrence(ingest_run_id,locale);
CREATE INDEX idx_world_archive_asset_run_status ON world_archive_asset_reference_occurrence(ingest_run_id,reference_status);

CREATE TRIGGER trg_world_archive_row_immutable BEFORE UPDATE ON world_archive_row_occurrence BEGIN SELECT RAISE(ABORT,'world archive evidence is immutable'); END;
CREATE TRIGGER trg_world_archive_row_delete_immutable BEFORE DELETE ON world_archive_row_occurrence BEGIN SELECT RAISE(ABORT,'world archive evidence is immutable'); END;
CREATE TRIGGER trg_world_archive_raw_immutable BEFORE UPDATE ON world_archive_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'world archive evidence is immutable'); END;
CREATE TRIGGER trg_world_archive_raw_delete_immutable BEFORE DELETE ON world_archive_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'world archive evidence is immutable'); END;
CREATE TRIGGER trg_world_archive_relation_immutable BEFORE UPDATE ON world_archive_relationship_occurrence BEGIN SELECT RAISE(ABORT,'world archive evidence is immutable'); END;
CREATE TRIGGER trg_world_archive_relation_delete_immutable BEFORE DELETE ON world_archive_relationship_occurrence BEGIN SELECT RAISE(ABORT,'world archive evidence is immutable'); END;
CREATE TRIGGER trg_world_archive_opaque_immutable BEFORE UPDATE ON world_archive_opaque_occurrence BEGIN SELECT RAISE(ABORT,'world archive evidence is immutable'); END;
CREATE TRIGGER trg_world_archive_opaque_delete_immutable BEFORE DELETE ON world_archive_opaque_occurrence BEGIN SELECT RAISE(ABORT,'world archive evidence is immutable'); END;
CREATE TRIGGER trg_world_archive_owner_immutable BEFORE UPDATE ON world_archive_text_owner_occurrence BEGIN SELECT RAISE(ABORT,'world archive evidence is immutable'); END;
CREATE TRIGGER trg_world_archive_owner_delete_immutable BEFORE DELETE ON world_archive_text_owner_occurrence BEGIN SELECT RAISE(ABORT,'world archive evidence is immutable'); END;
CREATE TRIGGER trg_world_archive_resolution_immutable BEFORE UPDATE ON world_archive_text_resolution_occurrence BEGIN SELECT RAISE(ABORT,'world archive evidence is immutable'); END;
CREATE TRIGGER trg_world_archive_resolution_delete_immutable BEFORE DELETE ON world_archive_text_resolution_occurrence BEGIN SELECT RAISE(ABORT,'world archive evidence is immutable'); END;
CREATE TRIGGER trg_world_archive_candidate_immutable BEFORE UPDATE ON world_archive_text_candidate_occurrence BEGIN SELECT RAISE(ABORT,'world archive evidence is immutable'); END;
CREATE TRIGGER trg_world_archive_candidate_delete_immutable BEFORE DELETE ON world_archive_text_candidate_occurrence BEGIN SELECT RAISE(ABORT,'world archive evidence is immutable'); END;
CREATE TRIGGER trg_world_archive_asset_immutable BEFORE UPDATE ON world_archive_asset_reference_occurrence BEGIN SELECT RAISE(ABORT,'world archive evidence is immutable'); END;
CREATE TRIGGER trg_world_archive_asset_delete_immutable BEFORE DELETE ON world_archive_asset_reference_occurrence BEGIN SELECT RAISE(ABORT,'world archive evidence is immutable'); END;
