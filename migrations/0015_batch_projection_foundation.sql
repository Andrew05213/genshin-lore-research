-- Batch M1 mechanical foundation (object-container TextMap contract v2).
--
-- The six selected TextMap files each use one existing source_record at
-- pointer /; pair occurrences retain their own physical ordinal/pointer.
-- This migration does not define family identity, entities, relations, text
-- owners, filters, or provider semantics.

PRAGMA foreign_keys = ON;
CREATE TABLE batch_wave (
    id TEXT PRIMARY KEY NOT NULL,
    snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id),
    contract_version TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256)=64 AND manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
    target_schema_version INTEGER NOT NULL CHECK (target_schema_version>=15),
    status TEXT NOT NULL CHECK (status IN ('staged','complete','failed')),
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json) AND json_type(metadata_json)='object')
);

CREATE TABLE batch_shard (
    id TEXT PRIMARY KEY NOT NULL,
    wave_id TEXT NOT NULL REFERENCES batch_wave(id),
    family TEXT NOT NULL,
    family_contract_version TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL REFERENCES ingest_run(id),
    manifest_sha256 TEXT NOT NULL CHECK (
        length(manifest_sha256)=64 AND manifest_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    shard_schema_version INTEGER NOT NULL CHECK (shard_schema_version>=1),
    read_set_json TEXT NOT NULL CHECK (json_valid(read_set_json) AND json_type(read_set_json)='array'),
    write_set_json TEXT NOT NULL CHECK (json_valid(write_set_json) AND json_type(write_set_json)='array'),
    rowset_sha256 TEXT NOT NULL CHECK (
        length(rowset_sha256)=64 AND rowset_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    status TEXT NOT NULL CHECK (status IN ('staged','verified','imported','failed')),
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(wave_id,family,family_contract_version,ingest_run_id)
);

CREATE TABLE batch_shard_import (
    id TEXT PRIMARY KEY NOT NULL,
    wave_id TEXT NOT NULL REFERENCES batch_wave(id),
    shard_id TEXT NOT NULL REFERENCES batch_shard(id),
    target_schema_version INTEGER NOT NULL CHECK (target_schema_version>=15),
    shard_file_sha256 TEXT NOT NULL CHECK (
        length(shard_file_sha256)=64 AND shard_file_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    imported_row_count INTEGER NOT NULL CHECK (imported_row_count>=0),
    status TEXT NOT NULL CHECK (status IN ('complete','failed')),
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(wave_id,shard_id)
);

CREATE TABLE textmap_physical_occurrence (
    id TEXT PRIMARY KEY NOT NULL,
    ingest_run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    parser_build_id TEXT NOT NULL,
    source_file_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_record_pointer TEXT NOT NULL CHECK (source_record_pointer='/'),
    locale TEXT NOT NULL CHECK (locale IN ('CHS','EN','JP')),
    shard_tier TEXT NOT NULL CHECK (shard_tier IN ('main','medium')),
    pair_ordinal INTEGER NOT NULL CHECK (pair_ordinal>=0),
    source_pointer TEXT NOT NULL CHECK (substr(source_pointer,1,2)='/@'),
    raw_key_json TEXT NOT NULL CHECK (json_valid(raw_key_json) AND json_type(raw_key_json)='text'),
    key_text TEXT NOT NULL,
    raw_text_json TEXT NOT NULL CHECK (json_valid(raw_text_json) AND json_type(raw_text_json)='text'),
    raw_text TEXT NOT NULL,
    raw_key_sha256 TEXT NOT NULL CHECK (
        length(raw_key_sha256)=64 AND raw_key_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    raw_text_sha256 TEXT NOT NULL CHECK (
        length(raw_text_sha256)=64 AND raw_text_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    raw_record_sha256 TEXT NOT NULL CHECK (
        length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(ingest_run_id,source_file_id,pair_ordinal),
    FOREIGN KEY(ingest_run_id,snapshot_id,parser_build_id)
        REFERENCES ingest_run(id,snapshot_id,parser_build_id),
    FOREIGN KEY(source_file_id,snapshot_id)
        REFERENCES source_file(id,snapshot_id),
    FOREIGN KEY(source_record_id,source_file_id,source_record_pointer)
        REFERENCES source_record(id,source_file_id,pointer)
);

CREATE TABLE batch_table_digest (
    id TEXT PRIMARY KEY NOT NULL,
    wave_id TEXT NOT NULL REFERENCES batch_wave(id),
    shard_id TEXT NOT NULL REFERENCES batch_shard(id),
    table_name TEXT NOT NULL,
    schema_sql_sha256 TEXT NOT NULL CHECK (
        length(schema_sql_sha256)=64 AND schema_sql_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    ordering_json TEXT NOT NULL CHECK (json_valid(ordering_json) AND json_type(ordering_json)='array'),
    row_count INTEGER NOT NULL CHECK (row_count>=0),
    logical_row_sha256 TEXT NOT NULL CHECK (
        length(logical_row_sha256)=64 AND logical_row_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    chunk_size INTEGER NOT NULL CHECK (chunk_size>0),
    chunk_count INTEGER NOT NULL CHECK (chunk_count>=0),
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(wave_id,shard_id,table_name)
);

CREATE TABLE batch_table_chunk_digest (
    id TEXT PRIMARY KEY NOT NULL,
    table_digest_id TEXT NOT NULL REFERENCES batch_table_digest(id),
    chunk_ordinal INTEGER NOT NULL CHECK (chunk_ordinal>=0),
    first_key_json TEXT NOT NULL CHECK (json_valid(first_key_json) AND json_type(first_key_json)='array'),
    last_key_json TEXT NOT NULL CHECK (json_valid(last_key_json) AND json_type(last_key_json)='array'),
    row_count INTEGER NOT NULL CHECK (row_count>0),
    logical_row_sha256 TEXT NOT NULL CHECK (
        length(logical_row_sha256)=64 AND logical_row_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    UNIQUE(table_digest_id,chunk_ordinal)
);

CREATE INDEX idx_batch_shard_wave
    ON batch_shard(wave_id,family);
CREATE INDEX idx_batch_import_wave
    ON batch_shard_import(wave_id,shard_id);
CREATE INDEX idx_textmap_physical_locator
    ON textmap_physical_occurrence(ingest_run_id,locale,shard_tier,key_text);
CREATE INDEX idx_batch_table_digest_wave
    ON batch_table_digest(wave_id,shard_id,table_name);
CREATE INDEX idx_batch_chunk_digest_parent
    ON batch_table_chunk_digest(table_digest_id,chunk_ordinal);

CREATE TRIGGER trg_batch_wave_immutable_update
BEFORE UPDATE ON batch_wave BEGIN
    SELECT RAISE(ABORT,'batch wave evidence is immutable');
END;
CREATE TRIGGER trg_batch_wave_immutable_delete
BEFORE DELETE ON batch_wave BEGIN
    SELECT RAISE(ABORT,'batch wave evidence is immutable');
END;
CREATE TRIGGER trg_batch_shard_immutable_update
BEFORE UPDATE ON batch_shard BEGIN
    SELECT RAISE(ABORT,'batch shard evidence is immutable');
END;
CREATE TRIGGER trg_batch_shard_immutable_delete
BEFORE DELETE ON batch_shard BEGIN
    SELECT RAISE(ABORT,'batch shard evidence is immutable');
END;
CREATE TRIGGER trg_batch_import_immutable_update
BEFORE UPDATE ON batch_shard_import BEGIN
    SELECT RAISE(ABORT,'batch shard import evidence is immutable');
END;
CREATE TRIGGER trg_batch_import_immutable_delete
BEFORE DELETE ON batch_shard_import BEGIN
    SELECT RAISE(ABORT,'batch shard import evidence is immutable');
END;
CREATE TRIGGER trg_textmap_physical_immutable_update
BEFORE UPDATE ON textmap_physical_occurrence BEGIN
    SELECT RAISE(ABORT,'TextMap physical occurrence is immutable');
END;
CREATE TRIGGER trg_textmap_physical_immutable_delete
BEFORE DELETE ON textmap_physical_occurrence BEGIN
    SELECT RAISE(ABORT,'TextMap physical occurrence is immutable');
END;
CREATE TRIGGER trg_batch_table_digest_immutable_update
BEFORE UPDATE ON batch_table_digest BEGIN
    SELECT RAISE(ABORT,'batch table digest is immutable');
END;
CREATE TRIGGER trg_batch_table_digest_immutable_delete
BEFORE DELETE ON batch_table_digest BEGIN
    SELECT RAISE(ABORT,'batch table digest is immutable');
END;
CREATE TRIGGER trg_batch_chunk_digest_immutable_update
BEFORE UPDATE ON batch_table_chunk_digest BEGIN
    SELECT RAISE(ABORT,'batch chunk digest is immutable');
END;
CREATE TRIGGER trg_batch_chunk_digest_immutable_delete
BEFORE DELETE ON batch_table_chunk_digest BEGIN
    SELECT RAISE(ABORT,'batch chunk digest is immutable');
END;
