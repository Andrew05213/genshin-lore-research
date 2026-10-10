-- Phase 4B2 additive static TextMap occurrence provenance.
--
-- TextMap hashes and checksums are locators/projection facts, never logical
-- identity.  Every resolution is owned by one static relationship occurrence
-- and locale; every candidate preserves one exact raw TextMap source record.

PRAGMA foreign_keys = ON;

-- Exact parent keys used by the run/snapshot/owner composite foreign keys.
CREATE UNIQUE INDEX IF NOT EXISTS uq_static_relationship_text_owner
    ON static_relationship_occurrence(
        id,
        ingest_run_id,
        snapshot_id,
        source_container_id,
        source_record_id,
        source_pointer
    );

CREATE UNIQUE INDEX IF NOT EXISTS uq_source_record_id_file_pointer
    ON source_record(id, source_file_id, pointer);

CREATE TABLE static_text_resolution_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    static_relationship_occurrence_id TEXT NOT NULL,
    source_container_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL
        CHECK (length(source_pointer) > 0),
    locale TEXT NOT NULL
        CHECK (length(trim(locale)) > 0),
    textmap_hash TEXT NOT NULL
        CHECK (length(trim(textmap_hash)) > 0),
    resolution_status TEXT NOT NULL CHECK (
        resolution_status IN (
            'unique_main',
            'unique_medium',
            'both_equal',
            'both_conflict',
            'missing'
        )
    ),
    raw_text TEXT,
    normalized_text TEXT,
    raw_checksum TEXT
        CHECK (raw_checksum IS NULL OR length(raw_checksum) > 0),
    normalized_checksum TEXT
        CHECK (normalized_checksum IS NULL OR length(normalized_checksum) > 0),
    metadata_json TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(metadata_json)),
    CHECK (
        (
            resolution_status IN ('unique_main', 'unique_medium', 'both_equal')
            AND raw_text IS NOT NULL
            AND normalized_text IS NOT NULL
            AND raw_checksum IS NOT NULL
            AND normalized_checksum IS NOT NULL
        )
        OR
        (
            resolution_status IN ('both_conflict', 'missing')
            AND raw_text IS NULL
            AND normalized_text IS NULL
            AND raw_checksum IS NULL
            AND normalized_checksum IS NULL
        )
    ),
    UNIQUE (
        ingest_run_id,
        static_relationship_occurrence_id,
        locale
    ),
    UNIQUE (
        id,
        ingest_run_id,
        snapshot_id,
        locale,
        resolution_status
    ),
    FOREIGN KEY (ingest_run_id, snapshot_id)
        REFERENCES ingest_run(id, snapshot_id),
    FOREIGN KEY (
        static_relationship_occurrence_id,
        ingest_run_id,
        snapshot_id,
        source_container_id,
        source_record_id,
        source_pointer
    ) REFERENCES static_relationship_occurrence(
        id,
        ingest_run_id,
        snapshot_id,
        source_container_id,
        source_record_id,
        source_pointer
    )
);

CREATE TABLE static_text_candidate_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    resolution_occurrence_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    source_file_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_pointer TEXT NOT NULL
        CHECK (length(source_pointer) > 0),
    shard_kind TEXT NOT NULL
        CHECK (shard_kind IN ('main', 'medium')),
    locale TEXT NOT NULL
        CHECK (length(trim(locale)) > 0),
    resolution_status TEXT NOT NULL CHECK (
        resolution_status IN (
            'unique_main',
            'unique_medium',
            'both_equal',
            'both_conflict'
        )
    ),
    raw_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    raw_checksum TEXT NOT NULL
        CHECK (length(raw_checksum) > 0),
    normalized_checksum TEXT NOT NULL
        CHECK (length(normalized_checksum) > 0),
    metadata_json TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(metadata_json)),
    CHECK (
        (resolution_status <> 'unique_main' OR shard_kind = 'main')
        AND
        (resolution_status <> 'unique_medium' OR shard_kind = 'medium')
    ),
    UNIQUE (
        resolution_occurrence_id,
        source_file_id,
        source_record_id,
        source_pointer
    ),
    FOREIGN KEY (
        resolution_occurrence_id,
        ingest_run_id,
        snapshot_id,
        locale,
        resolution_status
    ) REFERENCES static_text_resolution_occurrence(
        id,
        ingest_run_id,
        snapshot_id,
        locale,
        resolution_status
    ),
    FOREIGN KEY (ingest_run_id, snapshot_id)
        REFERENCES ingest_run(id, snapshot_id),
    FOREIGN KEY (source_file_id, snapshot_id)
        REFERENCES source_file(id, snapshot_id),
    FOREIGN KEY (source_record_id, source_file_id, source_pointer)
        REFERENCES source_record(id, source_file_id, pointer)
);

CREATE INDEX idx_static_text_resolution_owner
    ON static_text_resolution_occurrence(
        ingest_run_id,
        source_container_id,
        static_relationship_occurrence_id
    );

CREATE INDEX idx_static_text_resolution_hash_locale
    ON static_text_resolution_occurrence(
        textmap_hash,
        locale,
        resolution_status
    );

CREATE INDEX idx_static_text_candidate_source
    ON static_text_candidate_occurrence(
        source_file_id,
        source_record_id,
        source_pointer
    );

CREATE INDEX idx_static_text_candidate_run_locale
    ON static_text_candidate_occurrence(
        ingest_run_id,
        locale,
        shard_kind
    );

CREATE TRIGGER trg_static_text_resolution_occurrence_immutable
BEFORE UPDATE ON static_text_resolution_occurrence
BEGIN
    SELECT RAISE(ABORT, 'static_text_resolution_occurrence is write-once');
END;

CREATE TRIGGER trg_static_text_resolution_occurrence_delete_immutable
BEFORE DELETE ON static_text_resolution_occurrence
BEGIN
    SELECT RAISE(ABORT, 'static_text_resolution_occurrence is write-once');
END;

CREATE TRIGGER trg_static_text_candidate_occurrence_immutable
BEFORE UPDATE ON static_text_candidate_occurrence
BEGIN
    SELECT RAISE(ABORT, 'static_text_candidate_occurrence is write-once');
END;

CREATE TRIGGER trg_static_text_candidate_occurrence_delete_immutable
BEFORE DELETE ON static_text_candidate_occurrence
BEGIN
    SELECT RAISE(ABORT, 'static_text_candidate_occurrence is write-once');
END;
