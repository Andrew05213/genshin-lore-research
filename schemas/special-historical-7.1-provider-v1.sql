PRAGMA foreign_keys=ON;
CREATE TABLE source_file(
 path TEXT PRIMARY KEY,
 role TEXT NOT NULL CHECK(role IN ('primary','support','lookup','textmap')),
 size_bytes INTEGER NOT NULL CHECK(size_bytes>=0),
 sha256 TEXT NOT NULL,
 git_blob_oid TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE physical_row(
 occurrence_id TEXT PRIMARY KEY,
 snapshot_id TEXT NOT NULL,
 parser_build_id TEXT NOT NULL,
 ingest_run_id TEXT NOT NULL,
 row_kind TEXT NOT NULL CHECK(row_kind IN ('primary','support')),
 source_path TEXT NOT NULL REFERENCES source_file(path),
 source_ordinal INTEGER NOT NULL CHECK(source_ordinal>=0),
 source_pointer TEXT NOT NULL,
 raw_record_json TEXT NOT NULL,
 raw_record_sha256 TEXT NOT NULL,
 native_locator_json TEXT NOT NULL CHECK(json_valid(native_locator_json)),
 UNIQUE(source_path,source_ordinal)
) WITHOUT ROWID;
CREATE TABLE observation(
 occurrence_id TEXT PRIMARY KEY,
 row_id TEXT NOT NULL REFERENCES physical_row(occurrence_id),
 kind TEXT NOT NULL CHECK(kind IN ('raw_field','relation','text_owner','text_resolution','opaque','asset')),
 source_pointer TEXT NOT NULL,
 field_path TEXT NOT NULL,
 status TEXT NOT NULL,
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
 UNIQUE(row_id,kind,source_pointer,field_path)
) WITHOUT ROWID;
CREATE TABLE physical_candidate(
 occurrence_id TEXT PRIMARY KEY,
 observation_id TEXT NOT NULL REFERENCES observation(occurrence_id),
 candidate_kind TEXT NOT NULL CHECK(candidate_kind IN ('relation','textmap')),
 candidate_ordinal INTEGER NOT NULL CHECK(candidate_ordinal>=0),
 target_path TEXT NOT NULL REFERENCES source_file(path),
 target_ordinal INTEGER NOT NULL CHECK(target_ordinal>=0),
 target_pointer TEXT NOT NULL,
 target_raw_sha256 TEXT NOT NULL,
 target_value_json TEXT NOT NULL CHECK(json_valid(target_value_json)),
 UNIQUE(observation_id,candidate_ordinal)
) WITHOUT ROWID;
CREATE TABLE release(
 id INTEGER PRIMARY KEY CHECK(id=1),
 manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json))
) WITHOUT ROWID;
CREATE INDEX physical_row_source_order ON physical_row(source_path,source_ordinal);
CREATE INDEX observation_scope ON observation(row_id,kind,source_pointer);
CREATE INDEX physical_candidate_target ON physical_candidate(target_path,target_ordinal);
