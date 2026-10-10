-- Phase 4D2A additive Core Tutorial raw-evidence canary schema.
--
-- The tables retain physical rows and field/array occurrences only.  Native
-- IDs, TextMap hashes, checksums, and image names are locators or observations;
-- this migration creates no logical Tutorial, image, or PushTips identity.

PRAGMA foreign_keys = ON;

CREATE UNIQUE INDEX uq_tutorial_ingest_run_scope
    ON ingest_run(id, snapshot_id, parser_build_id);
CREATE UNIQUE INDEX uq_tutorial_source_record_file_pointer
    ON source_record(id, source_file_id, pointer);
CREATE UNIQUE INDEX uq_tutorial_source_file_snapshot
    ON source_file(id, snapshot_id);

CREATE TABLE tutorial_row_occurrence (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    ingest_run_id TEXT NOT NULL CHECK (length(ingest_run_id) > 0),
    snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) > 0),
    parser_build_id TEXT NOT NULL CHECK (length(parser_build_id) > 0),
    source_file_id TEXT NOT NULL CHECK (length(source_file_id) > 0),
    source_record_id TEXT NOT NULL CHECK (length(source_record_id) > 0),
    source_pointer TEXT NOT NULL CHECK (
        length(source_pointer) > 1 AND substr(source_pointer, 1, 1) = '/'
    ),
    table_name TEXT NOT NULL CHECK (table_name IN (
        'TutorialExcelConfigData', 'TutorialDetailExcelConfigData',
        'TutorialCatalogExcelConfigData', 'PushTipsCodexExcelConfigData'
    )),
    family_role TEXT NOT NULL CHECK (family_role IN ('primary', 'lookup')),
    row_index INTEGER NOT NULL CHECK (row_index >= 0),
    native_locator_field TEXT NOT NULL CHECK (length(native_locator_field) > 0),
    native_locator_value TEXT NOT NULL CHECK (
        length(native_locator_value) > 0
        AND native_locator_value NOT GLOB '*[^0-9]*'
    ),
    raw_checksum TEXT NOT NULL CHECK (
        length(raw_checksum) = 64
        AND raw_checksum NOT GLOB '*[^0-9a-f]*'
    ),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(metadata_json) AND json_type(metadata_json) = 'object'
    ),
    UNIQUE (ingest_run_id, source_record_id),
    UNIQUE (id, ingest_run_id, snapshot_id, parser_build_id),
    UNIQUE (id, ingest_run_id, snapshot_id, parser_build_id, source_record_id),
    FOREIGN KEY (ingest_run_id, snapshot_id, parser_build_id)
        REFERENCES ingest_run(id, snapshot_id, parser_build_id),
    FOREIGN KEY (parser_build_id) REFERENCES parser_build(id),
    FOREIGN KEY (source_file_id, snapshot_id)
        REFERENCES source_file(id, snapshot_id),
    FOREIGN KEY (source_record_id, source_file_id, source_pointer)
        REFERENCES source_record(id, source_file_id, pointer)
);

CREATE TABLE tutorial_raw_field_occurrence (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    ingest_run_id TEXT NOT NULL CHECK (length(ingest_run_id) > 0),
    snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) > 0),
    parser_build_id TEXT NOT NULL CHECK (length(parser_build_id) > 0),
    row_occurrence_id TEXT NOT NULL CHECK (length(row_occurrence_id) > 0),
    source_record_id TEXT NOT NULL CHECK (length(source_record_id) > 0),
    source_pointer TEXT NOT NULL CHECK (
        length(source_pointer) > 1 AND substr(source_pointer, 1, 1) = '/'
    ),
    field_name TEXT NOT NULL CHECK (length(field_name) > 0),
    array_index INTEGER CHECK (array_index IS NULL OR array_index >= 0),
    field_kind TEXT NOT NULL CHECK (field_kind IN ('scalar', 'array', 'array_item')),
    raw_json TEXT NOT NULL CHECK (json_valid(raw_json)),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(metadata_json) AND json_type(metadata_json) = 'object'
    ),
    CHECK (
        (field_kind = 'scalar' AND array_index IS NULL)
        OR (field_kind = 'array' AND array_index IS NULL AND json_type(raw_json) = 'array')
        OR (field_kind = 'array_item' AND array_index IS NOT NULL)
    ),
    UNIQUE (ingest_run_id, row_occurrence_id, source_pointer),
    FOREIGN KEY (
        row_occurrence_id, ingest_run_id, snapshot_id, parser_build_id,
        source_record_id
    ) REFERENCES tutorial_row_occurrence(
        id, ingest_run_id, snapshot_id, parser_build_id, source_record_id
    ),
    FOREIGN KEY (parser_build_id) REFERENCES parser_build(id)
);

CREATE TABLE tutorial_detail_relation_occurrence (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    ingest_run_id TEXT NOT NULL CHECK (length(ingest_run_id) > 0),
    snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) > 0),
    parser_build_id TEXT NOT NULL CHECK (length(parser_build_id) > 0),
    source_row_occurrence_id TEXT NOT NULL CHECK (length(source_row_occurrence_id) > 0),
    source_record_id TEXT NOT NULL CHECK (length(source_record_id) > 0),
    source_pointer TEXT NOT NULL CHECK (
        length(source_pointer) > 1 AND substr(source_pointer, 1, 1) = '/'
    ),
    field_name TEXT NOT NULL CHECK (field_name = 'detailIdList'),
    array_index INTEGER CHECK (array_index IS NULL OR array_index >= 0),
    raw_value_json TEXT CHECK (raw_value_json IS NULL OR json_valid(raw_value_json)),
    candidate_key_json TEXT NOT NULL CHECK (
        json_valid(candidate_key_json) AND json_type(candidate_key_json) = 'object'
    ),
    resolution_status TEXT NOT NULL CHECK (resolution_status IN (
        'resolved_unique', 'dangling', 'ambiguous', 'invalid', 'missing'
    )),
    target_row_occurrence_id TEXT,
    candidate_scopes_json TEXT NOT NULL CHECK (
        json_valid(candidate_scopes_json) AND json_type(candidate_scopes_json) = 'array'
    ),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(metadata_json) AND json_type(metadata_json) = 'object'
    ),
    CHECK (
        (resolution_status = 'resolved_unique' AND target_row_occurrence_id IS NOT NULL)
        OR (resolution_status <> 'resolved_unique' AND target_row_occurrence_id IS NULL)
    ),
    CHECK (
        (resolution_status = 'missing' AND array_index IS NULL)
        OR (resolution_status <> 'missing' AND array_index IS NOT NULL)
    ),
    UNIQUE (ingest_run_id, source_row_occurrence_id, source_pointer),
    FOREIGN KEY (
        source_row_occurrence_id, ingest_run_id, snapshot_id, parser_build_id,
        source_record_id
    ) REFERENCES tutorial_row_occurrence(
        id, ingest_run_id, snapshot_id, parser_build_id, source_record_id
    ),
    FOREIGN KEY (target_row_occurrence_id, ingest_run_id, snapshot_id, parser_build_id)
        REFERENCES tutorial_row_occurrence(id, ingest_run_id, snapshot_id, parser_build_id),
    FOREIGN KEY (parser_build_id) REFERENCES parser_build(id)
);

CREATE TABLE tutorial_text_owner_occurrence (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    ingest_run_id TEXT NOT NULL CHECK (length(ingest_run_id) > 0),
    snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) > 0),
    parser_build_id TEXT NOT NULL CHECK (length(parser_build_id) > 0),
    source_row_occurrence_id TEXT NOT NULL CHECK (length(source_row_occurrence_id) > 0),
    source_record_id TEXT NOT NULL CHECK (length(source_record_id) > 0),
    source_pointer TEXT NOT NULL CHECK (
        length(source_pointer) > 1 AND substr(source_pointer, 1, 1) = '/'
    ),
    field_name TEXT NOT NULL CHECK (
        field_name IN ('descriptTextMapHash', 'titleTextMapHash')
    ),
    field_role TEXT NOT NULL CHECK (field_role IN ('description', 'title')),
    textmap_hash TEXT NOT NULL CHECK (
        length(textmap_hash) > 0 AND textmap_hash NOT GLOB '*[^0-9]*'
    ),
    raw_value_json TEXT NOT NULL CHECK (json_valid(raw_value_json)),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(metadata_json) AND json_type(metadata_json) = 'object'
    ),
    UNIQUE (id, ingest_run_id, snapshot_id, parser_build_id),
    UNIQUE (ingest_run_id, source_row_occurrence_id, source_pointer),
    FOREIGN KEY (
        source_row_occurrence_id, ingest_run_id, snapshot_id, parser_build_id,
        source_record_id
    ) REFERENCES tutorial_row_occurrence(
        id, ingest_run_id, snapshot_id, parser_build_id, source_record_id
    ),
    FOREIGN KEY (parser_build_id) REFERENCES parser_build(id)
);

CREATE TABLE tutorial_text_resolution_occurrence (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    ingest_run_id TEXT NOT NULL CHECK (length(ingest_run_id) > 0),
    snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) > 0),
    parser_build_id TEXT NOT NULL CHECK (length(parser_build_id) > 0),
    owner_occurrence_id TEXT NOT NULL CHECK (length(owner_occurrence_id) > 0),
    source_row_occurrence_id TEXT NOT NULL CHECK (length(source_row_occurrence_id) > 0),
    source_record_id TEXT NOT NULL CHECK (length(source_record_id) > 0),
    source_pointer TEXT NOT NULL CHECK (
        length(source_pointer) > 1 AND substr(source_pointer, 1, 1) = '/'
    ),
    locale TEXT NOT NULL CHECK (locale IN ('CHS', 'EN', 'JP')),
    textmap_hash TEXT NOT NULL CHECK (
        length(textmap_hash) > 0 AND textmap_hash NOT GLOB '*[^0-9]*'
    ),
    resolution_status TEXT NOT NULL CHECK (resolution_status IN (
        'unique_main', 'unique_medium', 'both_equal', 'both_conflict', 'missing', 'invalid'
    )),
    raw_text TEXT,
    normalized_text TEXT,
    raw_checksum TEXT CHECK (
        raw_checksum IS NULL OR (
            length(raw_checksum) = 64 AND raw_checksum NOT GLOB '*[^0-9a-f]*'
        )
    ),
    normalized_checksum TEXT CHECK (
        normalized_checksum IS NULL OR (
            length(normalized_checksum) = 64 AND normalized_checksum NOT GLOB '*[^0-9a-f]*'
        )
    ),
    candidate_scopes_json TEXT NOT NULL CHECK (
        json_valid(candidate_scopes_json) AND json_type(candidate_scopes_json) = 'array'
    ),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(metadata_json) AND json_type(metadata_json) = 'object'
    ),
    CHECK (
        (
            resolution_status IN ('unique_main', 'unique_medium', 'both_equal')
            AND raw_text IS NOT NULL AND normalized_text IS NOT NULL
            AND raw_checksum IS NOT NULL AND normalized_checksum IS NOT NULL
        )
        OR (
            resolution_status IN ('both_conflict', 'missing', 'invalid')
            AND raw_text IS NULL AND normalized_text IS NULL
            AND raw_checksum IS NULL AND normalized_checksum IS NULL
        )
    ),
    UNIQUE (id, ingest_run_id, snapshot_id, parser_build_id),
    UNIQUE (ingest_run_id, owner_occurrence_id, locale),
    FOREIGN KEY (
        owner_occurrence_id, ingest_run_id, snapshot_id, parser_build_id
    ) REFERENCES tutorial_text_owner_occurrence(
        id, ingest_run_id, snapshot_id, parser_build_id
    ),
    FOREIGN KEY (
        source_row_occurrence_id, ingest_run_id, snapshot_id, parser_build_id,
        source_record_id
    ) REFERENCES tutorial_row_occurrence(
        id, ingest_run_id, snapshot_id, parser_build_id, source_record_id
    ),
    FOREIGN KEY (parser_build_id) REFERENCES parser_build(id)
);

CREATE TABLE tutorial_text_candidate_occurrence (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    ingest_run_id TEXT NOT NULL CHECK (length(ingest_run_id) > 0),
    snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) > 0),
    parser_build_id TEXT NOT NULL CHECK (length(parser_build_id) > 0),
    resolution_occurrence_id TEXT NOT NULL CHECK (length(resolution_occurrence_id) > 0),
    source_file_id TEXT NOT NULL CHECK (length(source_file_id) > 0),
    source_record_id TEXT NOT NULL CHECK (length(source_record_id) > 0),
    source_pointer TEXT NOT NULL CHECK (
        length(source_pointer) > 1 AND substr(source_pointer, 1, 1) = '/'
    ),
    shard_kind TEXT NOT NULL CHECK (shard_kind IN ('main', 'medium')),
    locale TEXT NOT NULL CHECK (locale IN ('CHS', 'EN', 'JP')),
    resolution_status TEXT NOT NULL CHECK (resolution_status IN (
        'unique_main', 'unique_medium', 'both_equal', 'both_conflict'
    )),
    raw_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    raw_checksum TEXT NOT NULL CHECK (
        length(raw_checksum) = 64 AND raw_checksum NOT GLOB '*[^0-9a-f]*'
    ),
    normalized_checksum TEXT NOT NULL CHECK (
        length(normalized_checksum) = 64
        AND normalized_checksum NOT GLOB '*[^0-9a-f]*'
    ),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(metadata_json) AND json_type(metadata_json) = 'object'
    ),
    UNIQUE (resolution_occurrence_id, source_file_id, source_record_id, source_pointer),
    FOREIGN KEY (
        resolution_occurrence_id, ingest_run_id, snapshot_id, parser_build_id
    ) REFERENCES tutorial_text_resolution_occurrence(
        id, ingest_run_id, snapshot_id, parser_build_id
    ),
    FOREIGN KEY (source_file_id, snapshot_id)
        REFERENCES source_file(id, snapshot_id),
    FOREIGN KEY (source_record_id, source_file_id, source_pointer)
        REFERENCES source_record(id, source_file_id, pointer),
    FOREIGN KEY (parser_build_id) REFERENCES parser_build(id)
);

CREATE TABLE tutorial_image_reference_occurrence (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    ingest_run_id TEXT NOT NULL CHECK (length(ingest_run_id) > 0),
    snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) > 0),
    parser_build_id TEXT NOT NULL CHECK (length(parser_build_id) > 0),
    source_row_occurrence_id TEXT NOT NULL CHECK (length(source_row_occurrence_id) > 0),
    source_record_id TEXT NOT NULL CHECK (length(source_record_id) > 0),
    source_pointer TEXT NOT NULL CHECK (
        length(source_pointer) > 1 AND substr(source_pointer, 1, 1) = '/'
    ),
    field_name TEXT NOT NULL CHECK (field_name = 'imageNameList'),
    array_index INTEGER CHECK (array_index IS NULL OR array_index >= 0),
    raw_image_name TEXT,
    reference_status TEXT NOT NULL CHECK (reference_status IN ('observed', 'missing', 'invalid')),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(metadata_json) AND json_type(metadata_json) = 'object'
    ),
    CHECK (
        (reference_status = 'observed' AND array_index IS NOT NULL AND raw_image_name IS NOT NULL)
        OR (reference_status IN ('missing', 'invalid') AND array_index IS NULL)
    ),
    UNIQUE (ingest_run_id, source_row_occurrence_id, source_pointer),
    FOREIGN KEY (
        source_row_occurrence_id, ingest_run_id, snapshot_id, parser_build_id,
        source_record_id
    ) REFERENCES tutorial_row_occurrence(
        id, ingest_run_id, snapshot_id, parser_build_id, source_record_id
    ),
    FOREIGN KEY (parser_build_id) REFERENCES parser_build(id)
);

CREATE TABLE tutorial_push_tips_candidate_occurrence (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    ingest_run_id TEXT NOT NULL CHECK (length(ingest_run_id) > 0),
    snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) > 0),
    parser_build_id TEXT NOT NULL CHECK (length(parser_build_id) > 0),
    source_row_occurrence_id TEXT NOT NULL CHECK (length(source_row_occurrence_id) > 0),
    source_record_id TEXT NOT NULL CHECK (length(source_record_id) > 0),
    source_pointer TEXT NOT NULL CHECK (
        length(source_pointer) > 1 AND substr(source_pointer, 1, 1) = '/'
    ),
    field_name TEXT NOT NULL CHECK (field_name = 'pushTipsId'),
    raw_value_json TEXT CHECK (raw_value_json IS NULL OR json_valid(raw_value_json)),
    candidate_key_json TEXT NOT NULL CHECK (
        json_valid(candidate_key_json) AND json_type(candidate_key_json) = 'object'
    ),
    resolution_status TEXT NOT NULL CHECK (resolution_status IN (
        'resolved_unique', 'dangling', 'ambiguous', 'invalid', 'missing'
    )),
    target_row_occurrence_id TEXT,
    candidate_scopes_json TEXT NOT NULL CHECK (
        json_valid(candidate_scopes_json) AND json_type(candidate_scopes_json) = 'array'
    ),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(metadata_json) AND json_type(metadata_json) = 'object'
    ),
    CHECK (
        (resolution_status = 'resolved_unique' AND target_row_occurrence_id IS NOT NULL)
        OR (resolution_status <> 'resolved_unique' AND target_row_occurrence_id IS NULL)
    ),
    UNIQUE (ingest_run_id, source_row_occurrence_id, source_pointer),
    FOREIGN KEY (
        source_row_occurrence_id, ingest_run_id, snapshot_id, parser_build_id,
        source_record_id
    ) REFERENCES tutorial_row_occurrence(
        id, ingest_run_id, snapshot_id, parser_build_id, source_record_id
    ),
    FOREIGN KEY (target_row_occurrence_id, ingest_run_id, snapshot_id, parser_build_id)
        REFERENCES tutorial_row_occurrence(id, ingest_run_id, snapshot_id, parser_build_id),
    FOREIGN KEY (parser_build_id) REFERENCES parser_build(id)
);

CREATE INDEX idx_tutorial_row_run_table
    ON tutorial_row_occurrence(ingest_run_id, table_name, row_index);
CREATE INDEX idx_tutorial_raw_field_run_row
    ON tutorial_raw_field_occurrence(ingest_run_id, row_occurrence_id, field_name, array_index);
CREATE INDEX idx_tutorial_detail_relation_run_status
    ON tutorial_detail_relation_occurrence(ingest_run_id, resolution_status);
CREATE INDEX idx_tutorial_text_owner_run_field
    ON tutorial_text_owner_occurrence(ingest_run_id, field_name);
CREATE INDEX idx_tutorial_text_resolution_run_locale
    ON tutorial_text_resolution_occurrence(ingest_run_id, locale, resolution_status);
CREATE INDEX idx_tutorial_text_candidate_run_locale
    ON tutorial_text_candidate_occurrence(ingest_run_id, locale, shard_kind);
CREATE INDEX idx_tutorial_image_reference_run_name
    ON tutorial_image_reference_occurrence(ingest_run_id, raw_image_name);
CREATE INDEX idx_tutorial_push_tips_run_status
    ON tutorial_push_tips_candidate_occurrence(ingest_run_id, resolution_status);

CREATE TRIGGER trg_tutorial_row_occurrence_immutable
BEFORE UPDATE ON tutorial_row_occurrence BEGIN
    SELECT RAISE(ABORT, 'tutorial_row_occurrence is write-once');
END;
CREATE TRIGGER trg_tutorial_row_occurrence_delete_immutable
BEFORE DELETE ON tutorial_row_occurrence BEGIN
    SELECT RAISE(ABORT, 'tutorial_row_occurrence is write-once');
END;
CREATE TRIGGER trg_tutorial_raw_field_occurrence_immutable
BEFORE UPDATE ON tutorial_raw_field_occurrence BEGIN
    SELECT RAISE(ABORT, 'tutorial_raw_field_occurrence is write-once');
END;
CREATE TRIGGER trg_tutorial_raw_field_occurrence_delete_immutable
BEFORE DELETE ON tutorial_raw_field_occurrence BEGIN
    SELECT RAISE(ABORT, 'tutorial_raw_field_occurrence is write-once');
END;
CREATE TRIGGER trg_tutorial_detail_relation_occurrence_immutable
BEFORE UPDATE ON tutorial_detail_relation_occurrence BEGIN
    SELECT RAISE(ABORT, 'tutorial_detail_relation_occurrence is write-once');
END;
CREATE TRIGGER trg_tutorial_detail_relation_occurrence_delete_immutable
BEFORE DELETE ON tutorial_detail_relation_occurrence BEGIN
    SELECT RAISE(ABORT, 'tutorial_detail_relation_occurrence is write-once');
END;
CREATE TRIGGER trg_tutorial_text_owner_occurrence_immutable
BEFORE UPDATE ON tutorial_text_owner_occurrence BEGIN
    SELECT RAISE(ABORT, 'tutorial_text_owner_occurrence is write-once');
END;
CREATE TRIGGER trg_tutorial_text_owner_occurrence_delete_immutable
BEFORE DELETE ON tutorial_text_owner_occurrence BEGIN
    SELECT RAISE(ABORT, 'tutorial_text_owner_occurrence is write-once');
END;
CREATE TRIGGER trg_tutorial_text_resolution_occurrence_immutable
BEFORE UPDATE ON tutorial_text_resolution_occurrence BEGIN
    SELECT RAISE(ABORT, 'tutorial_text_resolution_occurrence is write-once');
END;
CREATE TRIGGER trg_tutorial_text_resolution_occurrence_delete_immutable
BEFORE DELETE ON tutorial_text_resolution_occurrence BEGIN
    SELECT RAISE(ABORT, 'tutorial_text_resolution_occurrence is write-once');
END;
CREATE TRIGGER trg_tutorial_text_candidate_occurrence_immutable
BEFORE UPDATE ON tutorial_text_candidate_occurrence BEGIN
    SELECT RAISE(ABORT, 'tutorial_text_candidate_occurrence is write-once');
END;
CREATE TRIGGER trg_tutorial_text_candidate_occurrence_delete_immutable
BEFORE DELETE ON tutorial_text_candidate_occurrence BEGIN
    SELECT RAISE(ABORT, 'tutorial_text_candidate_occurrence is write-once');
END;
CREATE TRIGGER trg_tutorial_image_reference_occurrence_immutable
BEFORE UPDATE ON tutorial_image_reference_occurrence BEGIN
    SELECT RAISE(ABORT, 'tutorial_image_reference_occurrence is write-once');
END;
CREATE TRIGGER trg_tutorial_image_reference_occurrence_delete_immutable
BEFORE DELETE ON tutorial_image_reference_occurrence BEGIN
    SELECT RAISE(ABORT, 'tutorial_image_reference_occurrence is write-once');
END;
CREATE TRIGGER trg_tutorial_push_tips_candidate_occurrence_immutable
BEFORE UPDATE ON tutorial_push_tips_candidate_occurrence BEGIN
    SELECT RAISE(ABORT, 'tutorial_push_tips_candidate_occurrence is write-once');
END;
CREATE TRIGGER trg_tutorial_push_tips_candidate_occurrence_delete_immutable
BEFORE DELETE ON tutorial_push_tips_candidate_occurrence BEGIN
    SELECT RAISE(ABORT, 'tutorial_push_tips_candidate_occurrence is write-once');
END;
