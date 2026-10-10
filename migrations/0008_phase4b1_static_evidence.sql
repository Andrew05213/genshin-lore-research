-- Phase 4B1 additive static raw-evidence foundation.
--
-- These tables preserve snapshot-scoped source occurrences only.  Candidate
-- keys, checksums, ordinals, and file names are provenance attributes; none
-- of them establish cross-language or cross-version logical identity.

PRAGMA foreign_keys = ON;

-- SQLite requires an exact unique parent key for every composite foreign key.
-- These indexes add ownership keys without changing any pre-0008 table.
CREATE UNIQUE INDEX IF NOT EXISTS uq_ingest_run_id_snapshot
    ON ingest_run(id, snapshot_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_source_file_id_snapshot
    ON source_file(id, snapshot_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_source_record_id_file
    ON source_record(id, source_file_id);

CREATE TABLE static_evidence_container (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    source_file_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    container_kind TEXT NOT NULL
        CHECK (container_kind IN ('excel_row', 'readable_file')),
    source_family TEXT NOT NULL
        CHECK (length(trim(source_family)) > 0),
    locale TEXT,
    candidate_namespace TEXT NOT NULL
        CHECK (length(trim(candidate_namespace)) > 0),
    candidate_key_json TEXT NOT NULL
        CHECK (json_valid(candidate_key_json)),
    metadata_json TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(metadata_json)),
    CHECK (
        (container_kind = 'excel_row' AND locale IS NULL)
        OR
        (container_kind = 'readable_file'
         AND locale IS NOT NULL
         AND length(trim(locale)) > 0)
    ),
    UNIQUE (ingest_run_id, source_record_id),
    UNIQUE (
        id,
        ingest_run_id,
        snapshot_id,
        source_record_id,
        container_kind,
        locale
    ),
    UNIQUE (
        id,
        ingest_run_id,
        snapshot_id,
        source_record_id,
        source_family
    ),
    UNIQUE (id, snapshot_id, source_family),
    FOREIGN KEY (ingest_run_id, snapshot_id)
        REFERENCES ingest_run(id, snapshot_id),
    FOREIGN KEY (source_file_id, snapshot_id)
        REFERENCES source_file(id, snapshot_id),
    FOREIGN KEY (source_record_id, source_file_id)
        REFERENCES source_record(id, source_file_id)
);

CREATE TABLE static_segment_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    evidence_container_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    container_kind TEXT NOT NULL
        CHECK (container_kind = 'readable_file'),
    locale TEXT NOT NULL
        CHECK (length(trim(locale)) > 0),
    source_pointer TEXT NOT NULL
        CHECK (length(source_pointer) > 0),
    file_local_ordinal INTEGER NOT NULL
        CHECK (file_local_ordinal >= 0),
    byte_start INTEGER NOT NULL
        CHECK (byte_start >= 0),
    byte_end INTEGER NOT NULL
        CHECK (byte_end >= byte_start),
    character_start INTEGER NOT NULL
        CHECK (character_start >= 0),
    character_end INTEGER NOT NULL
        CHECK (character_end >= character_start),
    raw_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    raw_checksum TEXT NOT NULL
        CHECK (length(raw_checksum) > 0),
    normalized_checksum TEXT NOT NULL
        CHECK (length(normalized_checksum) > 0),
    segmentation_version TEXT NOT NULL
        CHECK (length(trim(segmentation_version)) > 0),
    metadata_json TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(metadata_json)),
    UNIQUE (ingest_run_id, evidence_container_id, source_pointer),
    UNIQUE (ingest_run_id, evidence_container_id, file_local_ordinal),
    FOREIGN KEY (ingest_run_id, snapshot_id)
        REFERENCES ingest_run(id, snapshot_id),
    FOREIGN KEY (
        evidence_container_id,
        ingest_run_id,
        snapshot_id,
        source_record_id,
        container_kind,
        locale
    ) REFERENCES static_evidence_container(
        id,
        ingest_run_id,
        snapshot_id,
        source_record_id,
        container_kind,
        locale
    )
);

CREATE TABLE static_relationship_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    source_container_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_family TEXT NOT NULL
        CHECK (length(trim(source_family)) > 0),
    source_pointer TEXT NOT NULL
        CHECK (length(source_pointer) > 0),
    relation_kind TEXT NOT NULL
        CHECK (length(trim(relation_kind)) > 0),
    field_role TEXT NOT NULL CHECK (
        field_role IN (
            'raw_identifier',
            'verified_textmap_reference',
            'foreign_pointer_candidate',
            'unresolved_field_role',
            'unknown_obfuscated'
        )
    ),
    target_family TEXT
        CHECK (target_family IS NULL OR length(trim(target_family)) > 0),
    target_candidate_key_json TEXT
        CHECK (
            target_candidate_key_json IS NULL
            OR json_valid(target_candidate_key_json)
        ),
    candidate_resolution_status TEXT NOT NULL CHECK (
        candidate_resolution_status IN (
            'resolved', 'ambiguous', 'unresolved', 'dangling'
        )
    ),
    target_evidence_container_id TEXT,
    ambiguity_json TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(ambiguity_json)),
    metadata_json TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(metadata_json)),
    CHECK (
        candidate_resolution_status <> 'resolved'
        OR target_candidate_key_json IS NOT NULL
    ),
    CHECK (
        candidate_resolution_status <> 'ambiguous'
        OR coalesce(
            (
                (
                    json_type(ambiguity_json, '$.candidates') = 'array'
                    AND json_array_length(
                        ambiguity_json, '$.candidates'
                    ) > 0
                )
                OR (
                    json_type(ambiguity_json, '$.explanation') = 'text'
                    AND length(
                        trim(json_extract(ambiguity_json, '$.explanation'))
                    ) > 0
                )
            ),
            0
        )
    ),
    CHECK (
        target_evidence_container_id IS NULL
        OR (
            candidate_resolution_status = 'resolved'
            AND target_family IS NOT NULL
            AND length(trim(target_family)) > 0
        )
    ),
    UNIQUE (ingest_run_id, source_container_id, source_pointer),
    FOREIGN KEY (ingest_run_id, snapshot_id)
        REFERENCES ingest_run(id, snapshot_id),
    FOREIGN KEY (
        source_container_id,
        ingest_run_id,
        snapshot_id,
        source_record_id,
        source_family
    ) REFERENCES static_evidence_container(
        id,
        ingest_run_id,
        snapshot_id,
        source_record_id,
        source_family
    ),
    FOREIGN KEY (
        target_evidence_container_id,
        snapshot_id,
        target_family
    ) REFERENCES static_evidence_container(id, snapshot_id, source_family)
);

-- Static raw evidence is write-once.  Corrections are represented by a new
-- deterministic ingest run rather than by rewriting or deleting occurrences.
CREATE TRIGGER trg_static_evidence_container_immutable
BEFORE UPDATE ON static_evidence_container
BEGIN
    SELECT RAISE(ABORT, 'static_evidence_container is write-once');
END;

CREATE TRIGGER trg_static_evidence_container_delete_immutable
BEFORE DELETE ON static_evidence_container
BEGIN
    SELECT RAISE(ABORT, 'static_evidence_container is write-once');
END;

CREATE TRIGGER trg_static_segment_occurrence_immutable
BEFORE UPDATE ON static_segment_occurrence
BEGIN
    SELECT RAISE(ABORT, 'static_segment_occurrence is write-once');
END;

CREATE TRIGGER trg_static_segment_occurrence_delete_immutable
BEFORE DELETE ON static_segment_occurrence
BEGIN
    SELECT RAISE(ABORT, 'static_segment_occurrence is write-once');
END;

CREATE TRIGGER trg_static_relationship_occurrence_immutable
BEFORE UPDATE ON static_relationship_occurrence
BEGIN
    SELECT RAISE(ABORT, 'static_relationship_occurrence is write-once');
END;

CREATE TRIGGER trg_static_relationship_occurrence_delete_immutable
BEFORE DELETE ON static_relationship_occurrence
BEGIN
    SELECT RAISE(ABORT, 'static_relationship_occurrence is write-once');
END;
