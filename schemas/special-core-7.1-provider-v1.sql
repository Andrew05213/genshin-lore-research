PRAGMA foreign_keys=ON;
CREATE TABLE source_file(
  path TEXT PRIMARY KEY,
  role TEXT NOT NULL CHECK(role IN ('primary','lookup','textmap')),
  size_bytes INTEGER NOT NULL CHECK(size_bytes>=0),
  sha256 TEXT NOT NULL,
  git_blob_oid TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE evidence_row(
  occurrence_id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL,
  parser_build_id TEXT NOT NULL,
  ingest_run_id TEXT NOT NULL,
  source_path TEXT NOT NULL REFERENCES source_file(path),
  source_ordinal INTEGER NOT NULL CHECK(source_ordinal>=0),
  source_pointer TEXT NOT NULL,
  raw_record_json TEXT NOT NULL,
  raw_record_sha256 TEXT NOT NULL,
  native_identity_json TEXT NOT NULL CHECK(json_valid(native_identity_json)),
  UNIQUE(source_path,source_ordinal),
  UNIQUE(source_path,source_pointer)
) WITHOUT ROWID;
CREATE TABLE raw_field(
  occurrence_id TEXT PRIMARY KEY,
  row_id TEXT NOT NULL REFERENCES evidence_row(occurrence_id),
  source_pointer TEXT NOT NULL,
  field_path TEXT NOT NULL,
  raw_json TEXT NOT NULL CHECK(json_valid(raw_json)),
  field_role TEXT NOT NULL CHECK(field_role IN ('raw_observed','opaque_unresolved','asset_raw_only')),
  UNIQUE(row_id,source_pointer)
) WITHOUT ROWID;
CREATE TABLE opaque_observation(
  occurrence_id TEXT PRIMARY KEY,
  row_id TEXT NOT NULL REFERENCES evidence_row(occurrence_id),
  source_pointer TEXT NOT NULL,
  field_path TEXT NOT NULL,
  raw_json TEXT NOT NULL CHECK(json_valid(raw_json)),
  state TEXT NOT NULL CHECK(state='opaque_unresolved'),
  UNIQUE(row_id,source_pointer,field_path)
) WITHOUT ROWID;
CREATE TABLE asset_observation(
  occurrence_id TEXT PRIMARY KEY,
  row_id TEXT NOT NULL REFERENCES evidence_row(occurrence_id),
  source_pointer TEXT NOT NULL,
  field_path TEXT NOT NULL,
  raw_json TEXT,
  state TEXT NOT NULL CHECK(state IN ('raw_observed','missing_field','empty_slot')),
  UNIQUE(row_id,source_pointer,field_path)
) WITHOUT ROWID;
CREATE TABLE relation_observation(
  occurrence_id TEXT PRIMARY KEY,
  row_id TEXT NOT NULL REFERENCES evidence_row(occurrence_id),
  source_pointer TEXT NOT NULL,
  field_path TEXT NOT NULL,
  raw_value_json TEXT,
  target_path TEXT NOT NULL REFERENCES source_file(path),
  target_field TEXT NOT NULL,
  condition_json TEXT NOT NULL CHECK(json_valid(condition_json)),
  state TEXT NOT NULL CHECK(state IN ('missing_field','empty_slot','missing_target','resolved_unique','resolved_candidate_set','unresolved','not_applicable')),
  candidate_count INTEGER NOT NULL CHECK(candidate_count>=0),
  UNIQUE(row_id,source_pointer,field_path)
) WITHOUT ROWID;
CREATE TABLE relation_candidate(
  occurrence_id TEXT PRIMARY KEY,
  relation_id TEXT NOT NULL REFERENCES relation_observation(occurrence_id),
  candidate_ordinal INTEGER NOT NULL CHECK(candidate_ordinal>=0),
  target_path TEXT NOT NULL REFERENCES source_file(path),
  target_ordinal INTEGER NOT NULL CHECK(target_ordinal>=0),
  target_pointer TEXT NOT NULL,
  target_raw_sha256 TEXT NOT NULL,
  target_value_json TEXT NOT NULL CHECK(json_valid(target_value_json)),
  UNIQUE(relation_id,candidate_ordinal)
) WITHOUT ROWID;
CREATE TABLE text_owner(
  occurrence_id TEXT PRIMARY KEY,
  row_id TEXT NOT NULL REFERENCES evidence_row(occurrence_id),
  source_pointer TEXT NOT NULL,
  field_path TEXT NOT NULL,
  raw_hash_json TEXT NOT NULL CHECK(json_valid(raw_hash_json)),
  UNIQUE(row_id,source_pointer)
) WITHOUT ROWID;
CREATE TABLE text_resolution(
  occurrence_id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL REFERENCES text_owner(occurrence_id),
  locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')),
  state TEXT NOT NULL CHECK(state IN ('missing','resolved_unique','resolved_candidate_set')),
  candidate_count INTEGER NOT NULL CHECK(candidate_count>=0),
  UNIQUE(owner_id,locale)
) WITHOUT ROWID;
CREATE TABLE text_candidate(
  occurrence_id TEXT PRIMARY KEY,
  resolution_id TEXT NOT NULL REFERENCES text_resolution(occurrence_id),
  locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')),
  tier TEXT NOT NULL CHECK(tier IN ('main','medium')),
  source_path TEXT NOT NULL REFERENCES source_file(path),
  pair_ordinal INTEGER NOT NULL CHECK(pair_ordinal>=0),
  source_pointer TEXT NOT NULL,
  raw_key_json TEXT NOT NULL,
  raw_text_json TEXT NOT NULL,
  raw_text TEXT NOT NULL,
  raw_key_sha256 TEXT NOT NULL,
  raw_text_sha256 TEXT NOT NULL,
  raw_record_sha256 TEXT NOT NULL,
  UNIQUE(resolution_id,source_path,pair_ordinal)
) WITHOUT ROWID;
CREATE TABLE release(
  id INTEGER PRIMARY KEY CHECK(id=1),
  manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json))
) WITHOUT ROWID;
CREATE INDEX evidence_row_source_order ON evidence_row(source_path,source_ordinal);
CREATE INDEX relation_observation_row ON relation_observation(row_id,source_pointer);
CREATE INDEX relation_candidate_target ON relation_candidate(target_path,target_ordinal);
CREATE INDEX text_owner_field ON text_owner(field_path,row_id);
CREATE INDEX text_candidate_text ON text_candidate(raw_text,locale,resolution_id);
