-- Phase 4F2 additive QuestCodex Archive bounded raw-evidence canary schema.
-- Every occurrence is scoped to one snapshot/parser/run/source coordinate.
-- Native IDs, text hashes, checksums, and assets never create logical identity.

PRAGMA foreign_keys = ON;

CREATE TABLE quest_codex_row_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    source_file_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL CHECK(substr(source_pointer,1,1)='/'),
    evidence_kind TEXT NOT NULL CHECK(evidence_kind IN ('quest_codex_entry','chapter_support')),
    table_name TEXT NOT NULL CHECK(table_name IN ('QuestCodexExcelConfigData','ChapterExcelConfigData')),
    row_index INTEGER NOT NULL CHECK(row_index>=0),
    native_locator_value TEXT NOT NULL CHECK(length(native_locator_value)>0),
    raw_checksum TEXT NOT NULL CHECK(length(raw_checksum)=64 AND raw_checksum NOT GLOB '*[^0-9a-f]*'),
    metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(ingest_run_id,source_record_id),
    UNIQUE(id,ingest_run_id,snapshot_id,parser_build_id,source_record_id),
    FOREIGN KEY(ingest_run_id,snapshot_id,parser_build_id) REFERENCES ingest_run(id,snapshot_id,parser_build_id),
    FOREIGN KEY(source_file_id,snapshot_id) REFERENCES source_file(id,snapshot_id),
    FOREIGN KEY(source_record_id,source_file_id,source_pointer) REFERENCES source_record(id,source_file_id,pointer)
);

CREATE TABLE quest_codex_raw_field_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
    row_occurrence_id TEXT NOT NULL, source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL CHECK(substr(source_pointer,1,1)='/'),
    field_name TEXT NOT NULL,
    field_presence TEXT NOT NULL CHECK(field_presence IN ('present','missing')),
    raw_json TEXT CHECK(raw_json IS NULL OR json_valid(raw_json)),
    metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    CHECK((field_presence='present' AND raw_json IS NOT NULL) OR (field_presence='missing' AND raw_json IS NULL)),
    UNIQUE(ingest_run_id,row_occurrence_id,source_pointer),
    FOREIGN KEY(row_occurrence_id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
      REFERENCES quest_codex_row_occurrence(id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
);

CREATE TABLE quest_codex_relationship_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
    source_row_occurrence_id TEXT NOT NULL, source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL CHECK(substr(source_pointer,1,1)='/'),
    field_name TEXT NOT NULL CHECK(field_name IN ('chapterId','parentQuestId')),
    raw_value_json TEXT NOT NULL CHECK(json_valid(raw_value_json)),
    target_table TEXT NOT NULL CHECK(target_table IN ('ChapterExcelConfigData','QuestExcelConfigData')),
    target_field TEXT NOT NULL CHECK(target_field IN ('id','mainId')),
    resolution_status TEXT NOT NULL CHECK(resolution_status IN ('resolved_unique','resolved_candidate_set','missing')),
    metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(id,ingest_run_id,snapshot_id,parser_build_id),
    UNIQUE(ingest_run_id,source_row_occurrence_id,source_pointer),
    FOREIGN KEY(source_row_occurrence_id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
      REFERENCES quest_codex_row_occurrence(id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
);

CREATE TABLE quest_codex_relationship_candidate_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
    relationship_occurrence_id TEXT NOT NULL,
    source_file_id TEXT NOT NULL, source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL CHECK(substr(source_pointer,1,1)='/'),
    candidate_ordinal INTEGER NOT NULL CHECK(candidate_ordinal>=0),
    target_table TEXT NOT NULL CHECK(target_table IN ('ChapterExcelConfigData','QuestExcelConfigData')),
    target_field TEXT NOT NULL CHECK(target_field IN ('id','mainId')),
    target_raw_value_json TEXT NOT NULL CHECK(json_valid(target_raw_value_json)),
    raw_checksum TEXT NOT NULL CHECK(length(raw_checksum)=64 AND raw_checksum NOT GLOB '*[^0-9a-f]*'),
    metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(relationship_occurrence_id,candidate_ordinal),
    UNIQUE(relationship_occurrence_id,source_record_id),
    FOREIGN KEY(relationship_occurrence_id,ingest_run_id,snapshot_id,parser_build_id)
      REFERENCES quest_codex_relationship_occurrence(id,ingest_run_id,snapshot_id,parser_build_id),
    FOREIGN KEY(source_file_id,snapshot_id) REFERENCES source_file(id,snapshot_id),
    FOREIGN KEY(source_record_id,source_file_id,source_pointer) REFERENCES source_record(id,source_file_id,pointer)
);

CREATE TABLE quest_codex_opaque_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
    source_row_occurrence_id TEXT NOT NULL, source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL CHECK(substr(source_pointer,1,1)='/'),
    field_name TEXT NOT NULL, array_index INTEGER CHECK(array_index IS NULL OR array_index>=0),
    raw_value_json TEXT NOT NULL CHECK(json_valid(raw_value_json)),
    resolution_status TEXT NOT NULL CHECK(resolution_status='opaque_unresolved'),
    metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(ingest_run_id,source_row_occurrence_id,source_pointer),
    FOREIGN KEY(source_row_occurrence_id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
      REFERENCES quest_codex_row_occurrence(id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
);

CREATE TABLE quest_codex_text_owner_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
    source_row_occurrence_id TEXT NOT NULL, source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL CHECK(substr(source_pointer,1,1)='/'),
    field_name TEXT NOT NULL CHECK(field_name IN ('chapterImageTitleTextMapHash','chapterNumTextMapHash','chapterTitleTextMapHash')),
    field_role TEXT NOT NULL CHECK(field_role IN ('image_title','chapter_number','chapter_title')),
    textmap_hash TEXT NOT NULL CHECK(textmap_hash NOT GLOB '*[^0-9]*'),
    raw_value_json TEXT NOT NULL CHECK(json_valid(raw_value_json)),
    metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(id,ingest_run_id,snapshot_id,parser_build_id),
    UNIQUE(ingest_run_id,source_row_occurrence_id,source_pointer),
    FOREIGN KEY(source_row_occurrence_id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
      REFERENCES quest_codex_row_occurrence(id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
);

CREATE TABLE quest_codex_text_resolution_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
    owner_occurrence_id TEXT NOT NULL, source_row_occurrence_id TEXT NOT NULL, source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL CHECK(substr(source_pointer,1,1)='/'),
    locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')),
    resolution_status TEXT NOT NULL CHECK(resolution_status IN ('unique_medium','missing')),
    raw_text TEXT, normalized_text TEXT, raw_checksum TEXT, normalized_checksum TEXT,
    metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    CHECK((resolution_status='unique_medium' AND raw_text IS NOT NULL AND normalized_text IS NOT NULL AND raw_checksum IS NOT NULL AND normalized_checksum IS NOT NULL)
       OR (resolution_status='missing' AND raw_text IS NULL AND normalized_text IS NULL AND raw_checksum IS NULL AND normalized_checksum IS NULL)),
    UNIQUE(id,ingest_run_id,snapshot_id,parser_build_id), UNIQUE(ingest_run_id,owner_occurrence_id,locale),
    FOREIGN KEY(owner_occurrence_id,ingest_run_id,snapshot_id,parser_build_id) REFERENCES quest_codex_text_owner_occurrence(id,ingest_run_id,snapshot_id,parser_build_id),
    FOREIGN KEY(source_row_occurrence_id,ingest_run_id,snapshot_id,parser_build_id,source_record_id) REFERENCES quest_codex_row_occurrence(id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
);

CREATE TABLE quest_codex_text_candidate_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
    resolution_occurrence_id TEXT NOT NULL, source_file_id TEXT NOT NULL, source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL CHECK(substr(source_pointer,1,1)='/'),
    shard_kind TEXT NOT NULL CHECK(shard_kind='medium'), locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')),
    raw_text TEXT NOT NULL, normalized_text TEXT NOT NULL,
    raw_checksum TEXT NOT NULL, normalized_checksum TEXT NOT NULL,
    metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(resolution_occurrence_id,source_file_id,source_record_id,source_pointer),
    FOREIGN KEY(resolution_occurrence_id,ingest_run_id,snapshot_id,parser_build_id) REFERENCES quest_codex_text_resolution_occurrence(id,ingest_run_id,snapshot_id,parser_build_id),
    FOREIGN KEY(source_file_id,snapshot_id) REFERENCES source_file(id,snapshot_id),
    FOREIGN KEY(source_record_id,source_file_id,source_pointer) REFERENCES source_record(id,source_file_id,pointer)
);

CREATE TABLE quest_codex_asset_reference_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_build_id TEXT NOT NULL,
    source_row_occurrence_id TEXT NOT NULL, source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL CHECK(substr(source_pointer,1,1)='/'),
    field_name TEXT NOT NULL CHECK(field_name IN ('chapterIcon','chapterImageHash','chapterSerialNumberIcon')),
    asset_kind TEXT NOT NULL CHECK(asset_kind IN ('chapter_icon','chapter_image_hash','chapter_serial_number_icon')),
    raw_reference TEXT, reference_status TEXT NOT NULL CHECK(reference_status IN ('observed','missing')),
    metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    CHECK((reference_status='observed' AND raw_reference IS NOT NULL) OR (reference_status='missing' AND raw_reference IS NULL)),
    UNIQUE(ingest_run_id,source_row_occurrence_id,source_pointer),
    FOREIGN KEY(source_row_occurrence_id,ingest_run_id,snapshot_id,parser_build_id,source_record_id) REFERENCES quest_codex_row_occurrence(id,ingest_run_id,snapshot_id,parser_build_id,source_record_id)
);

CREATE INDEX idx_quest_codex_row_run_kind ON quest_codex_row_occurrence(ingest_run_id,evidence_kind,row_index);
CREATE INDEX idx_quest_codex_raw_run_row ON quest_codex_raw_field_occurrence(ingest_run_id,row_occurrence_id);
CREATE INDEX idx_quest_codex_relation_run_status ON quest_codex_relationship_occurrence(ingest_run_id,resolution_status);
CREATE INDEX idx_quest_codex_relation_candidate ON quest_codex_relationship_candidate_occurrence(relationship_occurrence_id,candidate_ordinal);
CREATE INDEX idx_quest_codex_opaque_run_field ON quest_codex_opaque_occurrence(ingest_run_id,field_name);
CREATE INDEX idx_quest_codex_owner_run_field ON quest_codex_text_owner_occurrence(ingest_run_id,field_name);
CREATE INDEX idx_quest_codex_resolution_run_locale ON quest_codex_text_resolution_occurrence(ingest_run_id,locale,resolution_status);
CREATE INDEX idx_quest_codex_text_candidate_run_locale ON quest_codex_text_candidate_occurrence(ingest_run_id,locale);
CREATE INDEX idx_quest_codex_asset_run_status ON quest_codex_asset_reference_occurrence(ingest_run_id,reference_status);

CREATE TRIGGER trg_quest_codex_row_immutable BEFORE UPDATE ON quest_codex_row_occurrence BEGIN SELECT RAISE(ABORT,'quest codex evidence is immutable'); END;
CREATE TRIGGER trg_quest_codex_row_delete_immutable BEFORE DELETE ON quest_codex_row_occurrence BEGIN SELECT RAISE(ABORT,'quest codex evidence is immutable'); END;
CREATE TRIGGER trg_quest_codex_raw_immutable BEFORE UPDATE ON quest_codex_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'quest codex evidence is immutable'); END;
CREATE TRIGGER trg_quest_codex_raw_delete_immutable BEFORE DELETE ON quest_codex_raw_field_occurrence BEGIN SELECT RAISE(ABORT,'quest codex evidence is immutable'); END;
CREATE TRIGGER trg_quest_codex_relation_immutable BEFORE UPDATE ON quest_codex_relationship_occurrence BEGIN SELECT RAISE(ABORT,'quest codex evidence is immutable'); END;
CREATE TRIGGER trg_quest_codex_relation_delete_immutable BEFORE DELETE ON quest_codex_relationship_occurrence BEGIN SELECT RAISE(ABORT,'quest codex evidence is immutable'); END;
CREATE TRIGGER trg_quest_codex_relation_candidate_immutable BEFORE UPDATE ON quest_codex_relationship_candidate_occurrence BEGIN SELECT RAISE(ABORT,'quest codex evidence is immutable'); END;
CREATE TRIGGER trg_quest_codex_relation_candidate_delete_immutable BEFORE DELETE ON quest_codex_relationship_candidate_occurrence BEGIN SELECT RAISE(ABORT,'quest codex evidence is immutable'); END;
CREATE TRIGGER trg_quest_codex_opaque_immutable BEFORE UPDATE ON quest_codex_opaque_occurrence BEGIN SELECT RAISE(ABORT,'quest codex evidence is immutable'); END;
CREATE TRIGGER trg_quest_codex_opaque_delete_immutable BEFORE DELETE ON quest_codex_opaque_occurrence BEGIN SELECT RAISE(ABORT,'quest codex evidence is immutable'); END;
CREATE TRIGGER trg_quest_codex_owner_immutable BEFORE UPDATE ON quest_codex_text_owner_occurrence BEGIN SELECT RAISE(ABORT,'quest codex evidence is immutable'); END;
CREATE TRIGGER trg_quest_codex_owner_delete_immutable BEFORE DELETE ON quest_codex_text_owner_occurrence BEGIN SELECT RAISE(ABORT,'quest codex evidence is immutable'); END;
CREATE TRIGGER trg_quest_codex_resolution_immutable BEFORE UPDATE ON quest_codex_text_resolution_occurrence BEGIN SELECT RAISE(ABORT,'quest codex evidence is immutable'); END;
CREATE TRIGGER trg_quest_codex_resolution_delete_immutable BEFORE DELETE ON quest_codex_text_resolution_occurrence BEGIN SELECT RAISE(ABORT,'quest codex evidence is immutable'); END;
CREATE TRIGGER trg_quest_codex_text_candidate_immutable BEFORE UPDATE ON quest_codex_text_candidate_occurrence BEGIN SELECT RAISE(ABORT,'quest codex evidence is immutable'); END;
CREATE TRIGGER trg_quest_codex_text_candidate_delete_immutable BEFORE DELETE ON quest_codex_text_candidate_occurrence BEGIN SELECT RAISE(ABORT,'quest codex evidence is immutable'); END;
CREATE TRIGGER trg_quest_codex_asset_immutable BEFORE UPDATE ON quest_codex_asset_reference_occurrence BEGIN SELECT RAISE(ABORT,'quest codex evidence is immutable'); END;
CREATE TRIGGER trg_quest_codex_asset_delete_immutable BEFORE DELETE ON quest_codex_asset_reference_occurrence BEGIN SELECT RAISE(ABORT,'quest codex evidence is immutable'); END;
