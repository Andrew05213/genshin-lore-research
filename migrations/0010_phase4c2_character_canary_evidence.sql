-- Phase 4C2 additive Character/Fetter/Voice canary raw-evidence schema.
--
-- Every occurrence is snapshot/run/source scoped. Native IDs, TextMap
-- hashes, voice slots, selectors, paths, and checksums are locators or
-- observations only; none creates logical Character or passage identity.

PRAGMA foreign_keys = ON;

CREATE UNIQUE INDEX uq_character_source_record_file_pointer
    ON source_record(id, source_file_id, pointer);
CREATE UNIQUE INDEX uq_character_source_file_snapshot
    ON source_file(id, snapshot_id);
CREATE UNIQUE INDEX uq_character_ingest_run_snapshot
    ON ingest_run(id, snapshot_id);

CREATE TABLE character_row_occurrence (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    ingest_run_id TEXT NOT NULL CHECK (length(ingest_run_id) > 0),
    snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) > 0),
    source_file_id TEXT NOT NULL CHECK (length(source_file_id) > 0),
    source_record_id TEXT NOT NULL CHECK (length(source_record_id) > 0),
    source_pointer TEXT NOT NULL CHECK (
        length(source_pointer) > 1 AND substr(source_pointer, 1, 1) = '/'
    ),
    table_name TEXT NOT NULL CHECK (table_name IN (
        'AvatarExcelConfigData', 'FetterInfoExcelConfigData',
        'FetterStoryExcelConfigData', 'FettersExcelConfigData'
    )),
    row_index INTEGER NOT NULL CHECK (row_index >= 0),
    native_locator_field TEXT NOT NULL CHECK (length(native_locator_field) > 0),
    native_locator_value TEXT NOT NULL CHECK (
        length(native_locator_value) > 0
        AND native_locator_value NOT GLOB '*[^0-9]*'
    ),
    avatar_id TEXT CHECK (
        avatar_id IS NULL
        OR (
            length(avatar_id) > 0
            AND avatar_id NOT GLOB '*[^0-9]*'
        )
    ),
    raw_checksum TEXT NOT NULL CHECK (
        length(raw_checksum) = 64
        AND raw_checksum NOT GLOB '*[^0-9a-f]*'
    ),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(metadata_json) AND json_type(metadata_json) = 'object'
    ),
    UNIQUE (ingest_run_id, source_record_id),
    UNIQUE (id, ingest_run_id, snapshot_id),
    UNIQUE (id, ingest_run_id, snapshot_id, source_record_id),
    UNIQUE (id, ingest_run_id, snapshot_id, source_record_id, source_pointer),
    FOREIGN KEY (ingest_run_id, snapshot_id)
        REFERENCES ingest_run(id, snapshot_id),
    FOREIGN KEY (source_file_id, snapshot_id)
        REFERENCES source_file(id, snapshot_id),
    FOREIGN KEY (source_record_id, source_file_id, source_pointer)
        REFERENCES source_record(id, source_file_id, pointer)
);

CREATE TABLE character_raw_slot_occurrence (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    ingest_run_id TEXT NOT NULL CHECK (length(ingest_run_id) > 0),
    snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) > 0),
    row_occurrence_id TEXT NOT NULL CHECK (length(row_occurrence_id) > 0),
    source_record_id TEXT NOT NULL CHECK (length(source_record_id) > 0),
    source_pointer TEXT NOT NULL CHECK (
        length(source_pointer) > 1 AND substr(source_pointer, 1, 1) = '/'
    ),
    field_name TEXT NOT NULL CHECK (length(field_name) > 0),
    array_index INTEGER CHECK (array_index IS NULL OR array_index >= 0),
    slot_kind TEXT NOT NULL CHECK (slot_kind IN (
        'condition_element', 'opaque_integer', 'opaque_alias'
    )),
    observation_status TEXT NOT NULL CHECK (observation_status IN (
        'observed', 'opaque_unresolved', 'observed_equal'
    )),
    raw_json TEXT NOT NULL CHECK (json_valid(raw_json)),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(metadata_json) AND json_type(metadata_json) = 'object'
    ),
    CHECK (
        (
            slot_kind = 'condition_element'
            AND observation_status = 'observed'
            AND array_index IS NOT NULL
            AND json_type(raw_json) = 'object'
        )
        OR (
            slot_kind = 'opaque_integer'
            AND observation_status = 'opaque_unresolved'
            AND json_type(raw_json) = 'integer'
        )
        OR (
            slot_kind = 'opaque_alias'
            AND observation_status = 'observed_equal'
            AND array_index IS NULL
            AND json_type(raw_json) = 'integer'
        )
    ),
    UNIQUE (ingest_run_id, row_occurrence_id, source_pointer),
    FOREIGN KEY (row_occurrence_id, ingest_run_id, snapshot_id, source_record_id)
        REFERENCES character_row_occurrence(
            id, ingest_run_id, snapshot_id, source_record_id
        )
);

CREATE TABLE character_voice_container_occurrence (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    ingest_run_id TEXT NOT NULL CHECK (length(ingest_run_id) > 0),
    snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) > 0),
    voice_slot TEXT NOT NULL CHECK (
        length(voice_slot) > 0 AND voice_slot NOT GLOB '*[^0-9]*'
    ),
    lut_source_file_id TEXT NOT NULL CHECK (length(lut_source_file_id) > 0),
    lut_source_record_id TEXT NOT NULL CHECK (length(lut_source_record_id) > 0),
    lut_source_pointer TEXT NOT NULL CHECK (
        length(lut_source_pointer) > 1 AND substr(lut_source_pointer, 1, 1) = '/'
    ),
    item_source_file_id TEXT NOT NULL CHECK (length(item_source_file_id) > 0),
    item_source_record_id TEXT NOT NULL CHECK (length(item_source_record_id) > 0),
    item_source_pointer TEXT NOT NULL CHECK (
        length(item_source_pointer) > 1 AND substr(item_source_pointer, 1, 1) = '/'
    ),
    resolution_status TEXT NOT NULL CHECK (
        resolution_status = 'resolved_unique_container'
    ),
    candidate_scopes_json TEXT NOT NULL CHECK (
        json_valid(candidate_scopes_json)
        AND json_type(candidate_scopes_json) = 'object'
    ),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(metadata_json) AND json_type(metadata_json) = 'object'
    ),
    UNIQUE (ingest_run_id, item_source_record_id),
    UNIQUE (id, ingest_run_id, snapshot_id),
    UNIQUE (id, ingest_run_id, snapshot_id, item_source_record_id),
    FOREIGN KEY (ingest_run_id, snapshot_id)
        REFERENCES ingest_run(id, snapshot_id),
    FOREIGN KEY (lut_source_file_id, snapshot_id)
        REFERENCES source_file(id, snapshot_id),
    FOREIGN KEY (lut_source_record_id, lut_source_file_id, lut_source_pointer)
        REFERENCES source_record(id, source_file_id, pointer),
    FOREIGN KEY (item_source_file_id, snapshot_id)
        REFERENCES source_file(id, snapshot_id),
    FOREIGN KEY (item_source_record_id, item_source_file_id, item_source_pointer)
        REFERENCES source_record(id, source_file_id, pointer)
);

CREATE TABLE character_relation_occurrence (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    ingest_run_id TEXT NOT NULL CHECK (length(ingest_run_id) > 0),
    snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) > 0),
    source_row_occurrence_id TEXT NOT NULL CHECK (length(source_row_occurrence_id) > 0),
    source_record_id TEXT NOT NULL CHECK (length(source_record_id) > 0),
    source_pointer TEXT NOT NULL CHECK (
        length(source_pointer) > 1 AND substr(source_pointer, 1, 1) = '/'
    ),
    relation_kind TEXT NOT NULL CHECK (relation_kind IN (
        'avatar_row_candidate', 'textmap_owner_candidate',
        'voice_container_attachment'
    )),
    field_role TEXT NOT NULL CHECK (length(field_role) > 0),
    target_family TEXT NOT NULL CHECK (length(target_family) > 0),
    target_candidate_key_json TEXT NOT NULL CHECK (
        json_valid(target_candidate_key_json)
        AND json_type(target_candidate_key_json) = 'object'
    ),
    resolution_status TEXT NOT NULL CHECK (resolution_status IN (
        'resolved', 'resolved_unique_container', 'ambiguous', 'unresolved',
        'dangling', 'missing', 'NotFound'
    )),
    target_row_occurrence_id TEXT,
    target_voice_container_id TEXT,
    candidate_scopes_json TEXT NOT NULL DEFAULT '[]' CHECK (
        json_valid(candidate_scopes_json)
    ),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(metadata_json) AND json_type(metadata_json) = 'object'
    ),
    CHECK (
        (
            relation_kind = 'avatar_row_candidate'
            AND target_family = 'AvatarExcelConfigData'
            AND json_type(candidate_scopes_json) = 'array'
            AND (
                (
                    resolution_status = 'resolved'
                    AND target_row_occurrence_id IS NOT NULL
                    AND target_voice_container_id IS NULL
                )
                OR (
                    resolution_status IN (
                        'ambiguous', 'unresolved', 'dangling', 'missing', 'NotFound'
                    )
                    AND target_row_occurrence_id IS NULL
                    AND target_voice_container_id IS NULL
                )
            )
        )
        OR (
            relation_kind = 'textmap_owner_candidate'
            AND target_family = 'TextMap'
            AND json_type(candidate_scopes_json) = 'array'
            AND resolution_status IN (
                'resolved', 'ambiguous', 'unresolved', 'dangling', 'missing', 'NotFound'
            )
            AND target_row_occurrence_id IS NULL
            AND target_voice_container_id IS NULL
        )
        OR (
            relation_kind = 'voice_container_attachment'
            AND target_family = 'FetterVoiceItemsContainer'
            AND json_type(candidate_scopes_json) = 'object'
            AND (
                (
                    resolution_status = 'resolved_unique_container'
                    AND target_row_occurrence_id IS NULL
                    AND target_voice_container_id IS NOT NULL
                )
                OR (
                    resolution_status IN (
                        'ambiguous', 'unresolved', 'dangling', 'missing', 'NotFound'
                    )
                    AND target_row_occurrence_id IS NULL
                    AND target_voice_container_id IS NULL
                )
            )
        )
    ),
    UNIQUE (ingest_run_id, source_row_occurrence_id, source_pointer, relation_kind),
    UNIQUE (
        id, ingest_run_id, snapshot_id, source_row_occurrence_id,
        source_record_id, source_pointer
    ),
    FOREIGN KEY (source_row_occurrence_id, ingest_run_id, snapshot_id, source_record_id)
        REFERENCES character_row_occurrence(
            id, ingest_run_id, snapshot_id, source_record_id
        ),
    FOREIGN KEY (target_row_occurrence_id, ingest_run_id, snapshot_id)
        REFERENCES character_row_occurrence(id, ingest_run_id, snapshot_id),
    FOREIGN KEY (target_voice_container_id, ingest_run_id, snapshot_id)
        REFERENCES character_voice_container_occurrence(id, ingest_run_id, snapshot_id)
);

CREATE TABLE character_text_resolution_occurrence (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    ingest_run_id TEXT NOT NULL CHECK (length(ingest_run_id) > 0),
    snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) > 0),
    relationship_occurrence_id TEXT NOT NULL CHECK (length(relationship_occurrence_id) > 0),
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
        'unique_main', 'unique_medium', 'both_equal', 'both_conflict', 'missing'
    )),
    raw_text TEXT,
    normalized_text TEXT,
    raw_checksum TEXT CHECK (
        raw_checksum IS NULL
        OR (
            length(raw_checksum) = 64
            AND raw_checksum NOT GLOB '*[^0-9a-f]*'
        )
    ),
    normalized_checksum TEXT CHECK (
        normalized_checksum IS NULL
        OR (
            length(normalized_checksum) = 64
            AND normalized_checksum NOT GLOB '*[^0-9a-f]*'
        )
    ),
    candidate_scopes_json TEXT NOT NULL CHECK (
        json_valid(candidate_scopes_json)
        AND json_type(candidate_scopes_json) = 'array'
    ),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(metadata_json) AND json_type(metadata_json) = 'object'
    ),
    CHECK (
        (
            resolution_status IN ('unique_main', 'unique_medium', 'both_equal')
            AND raw_text IS NOT NULL
            AND normalized_text IS NOT NULL
            AND raw_checksum IS NOT NULL
            AND normalized_checksum IS NOT NULL
        )
        OR (
            resolution_status IN ('both_conflict', 'missing')
            AND raw_text IS NULL
            AND normalized_text IS NULL
            AND raw_checksum IS NULL
            AND normalized_checksum IS NULL
        )
    ),
    UNIQUE (ingest_run_id, relationship_occurrence_id, locale),
    UNIQUE (id, ingest_run_id, snapshot_id, locale, resolution_status),
    FOREIGN KEY (
        relationship_occurrence_id, ingest_run_id, snapshot_id,
        source_row_occurrence_id, source_record_id, source_pointer
    ) REFERENCES character_relation_occurrence(
        id, ingest_run_id, snapshot_id, source_row_occurrence_id,
        source_record_id, source_pointer
    )
);

CREATE TABLE character_text_candidate_occurrence (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    resolution_occurrence_id TEXT NOT NULL CHECK (length(resolution_occurrence_id) > 0),
    ingest_run_id TEXT NOT NULL CHECK (length(ingest_run_id) > 0),
    snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) > 0),
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
        length(raw_checksum) = 64
        AND raw_checksum NOT GLOB '*[^0-9a-f]*'
    ),
    normalized_checksum TEXT NOT NULL CHECK (
        length(normalized_checksum) = 64
        AND normalized_checksum NOT GLOB '*[^0-9a-f]*'
    ),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(metadata_json) AND json_type(metadata_json) = 'object'
    ),
    CHECK (
        (resolution_status = 'unique_main' AND shard_kind = 'main')
        OR (resolution_status = 'unique_medium' AND shard_kind = 'medium')
        OR resolution_status IN ('both_equal', 'both_conflict')
    ),
    UNIQUE (
        resolution_occurrence_id, source_file_id,
        source_record_id, source_pointer
    ),
    FOREIGN KEY (
        resolution_occurrence_id, ingest_run_id, snapshot_id, locale,
        resolution_status
    ) REFERENCES character_text_resolution_occurrence(
        id, ingest_run_id, snapshot_id, locale, resolution_status
    ),
    FOREIGN KEY (source_file_id, snapshot_id)
        REFERENCES source_file(id, snapshot_id),
    FOREIGN KEY (source_record_id, source_file_id, source_pointer)
        REFERENCES source_record(id, source_file_id, pointer)
);

CREATE TABLE character_audio_variant_occurrence (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    ingest_run_id TEXT NOT NULL CHECK (length(ingest_run_id) > 0),
    snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) > 0),
    voice_container_id TEXT NOT NULL CHECK (length(voice_container_id) > 0),
    item_source_record_id TEXT NOT NULL CHECK (length(item_source_record_id) > 0),
    source_pointer TEXT NOT NULL CHECK (
        length(source_pointer) > 1 AND substr(source_pointer, 1, 1) = '/'
    ),
    array_index INTEGER NOT NULL CHECK (array_index >= 0),
    audio_path TEXT NOT NULL CHECK (length(audio_path) > 0),
    audio_path_status TEXT NOT NULL CHECK (audio_path_status = 'observed'),
    character_child_selector TEXT NOT NULL CHECK (
        length(character_child_selector) > 0
    ),
    selector_status TEXT NOT NULL CHECK (
        selector_status = 'observed_unresolved'
    ),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(metadata_json) AND json_type(metadata_json) = 'object'
    ),
    UNIQUE (ingest_run_id, voice_container_id, source_pointer),
    FOREIGN KEY (
        voice_container_id, ingest_run_id, snapshot_id, item_source_record_id
    ) REFERENCES character_voice_container_occurrence(
        id, ingest_run_id, snapshot_id, item_source_record_id
    )
);

CREATE INDEX idx_character_row_run_table
    ON character_row_occurrence(ingest_run_id, table_name, row_index);
CREATE INDEX idx_character_relation_run_kind
    ON character_relation_occurrence(
        ingest_run_id, relation_kind, resolution_status
    );
CREATE INDEX idx_character_text_resolution_run_locale
    ON character_text_resolution_occurrence(
        ingest_run_id, locale, resolution_status
    );
CREATE INDEX idx_character_voice_run_slot
    ON character_voice_container_occurrence(ingest_run_id, voice_slot);
CREATE INDEX idx_character_audio_run_container
    ON character_audio_variant_occurrence(
        ingest_run_id, voice_container_id, array_index
    );

CREATE TRIGGER trg_character_row_occurrence_immutable
BEFORE UPDATE ON character_row_occurrence BEGIN
    SELECT RAISE(ABORT, 'character_row_occurrence is write-once');
END;
CREATE TRIGGER trg_character_row_occurrence_delete_immutable
BEFORE DELETE ON character_row_occurrence BEGIN
    SELECT RAISE(ABORT, 'character_row_occurrence is write-once');
END;
CREATE TRIGGER trg_character_raw_slot_occurrence_immutable
BEFORE UPDATE ON character_raw_slot_occurrence BEGIN
    SELECT RAISE(ABORT, 'character_raw_slot_occurrence is write-once');
END;
CREATE TRIGGER trg_character_raw_slot_occurrence_delete_immutable
BEFORE DELETE ON character_raw_slot_occurrence BEGIN
    SELECT RAISE(ABORT, 'character_raw_slot_occurrence is write-once');
END;
CREATE TRIGGER trg_character_relation_occurrence_immutable
BEFORE UPDATE ON character_relation_occurrence BEGIN
    SELECT RAISE(ABORT, 'character_relation_occurrence is write-once');
END;
CREATE TRIGGER trg_character_relation_occurrence_delete_immutable
BEFORE DELETE ON character_relation_occurrence BEGIN
    SELECT RAISE(ABORT, 'character_relation_occurrence is write-once');
END;
CREATE TRIGGER trg_character_text_resolution_occurrence_immutable
BEFORE UPDATE ON character_text_resolution_occurrence BEGIN
    SELECT RAISE(ABORT, 'character_text_resolution_occurrence is write-once');
END;
CREATE TRIGGER trg_character_text_resolution_occurrence_delete_immutable
BEFORE DELETE ON character_text_resolution_occurrence BEGIN
    SELECT RAISE(ABORT, 'character_text_resolution_occurrence is write-once');
END;
CREATE TRIGGER trg_character_text_candidate_occurrence_immutable
BEFORE UPDATE ON character_text_candidate_occurrence BEGIN
    SELECT RAISE(ABORT, 'character_text_candidate_occurrence is write-once');
END;
CREATE TRIGGER trg_character_text_candidate_occurrence_delete_immutable
BEFORE DELETE ON character_text_candidate_occurrence BEGIN
    SELECT RAISE(ABORT, 'character_text_candidate_occurrence is write-once');
END;
CREATE TRIGGER trg_character_voice_container_occurrence_immutable
BEFORE UPDATE ON character_voice_container_occurrence BEGIN
    SELECT RAISE(ABORT, 'character_voice_container_occurrence is write-once');
END;
CREATE TRIGGER trg_character_voice_container_occurrence_delete_immutable
BEFORE DELETE ON character_voice_container_occurrence BEGIN
    SELECT RAISE(ABORT, 'character_voice_container_occurrence is write-once');
END;
CREATE TRIGGER trg_character_audio_variant_occurrence_immutable
BEFORE UPDATE ON character_audio_variant_occurrence BEGIN
    SELECT RAISE(ABORT, 'character_audio_variant_occurrence is write-once');
END;
CREATE TRIGGER trg_character_audio_variant_occurrence_delete_immutable
BEFORE DELETE ON character_audio_variant_occurrence BEGIN
    SELECT RAISE(ABORT, 'character_audio_variant_occurrence is write-once');
END;
