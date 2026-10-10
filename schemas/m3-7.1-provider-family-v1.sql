PRAGMA foreign_keys=ON;
CREATE TABLE source_file(
  path TEXT PRIMARY KEY,
  role TEXT NOT NULL CHECK(role IN ('primary','textmap')),
  size_bytes INTEGER NOT NULL CHECK(size_bytes>=0),
  sha256 TEXT NOT NULL,
  git_blob_oid TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE v1_row(
  occurrence_id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL,
  parser_build_id TEXT NOT NULL,
  ingest_run_id TEXT NOT NULL,
  source_path TEXT NOT NULL REFERENCES source_file(path),
  source_ordinal INTEGER NOT NULL CHECK(source_ordinal>=0),
  source_pointer TEXT NOT NULL,
  raw_record_json TEXT NOT NULL,
  raw_record_sha256 TEXT NOT NULL,
  raw_key_observations_json TEXT NOT NULL,
  UNIQUE(source_path,source_ordinal),
  UNIQUE(source_path,source_pointer)
) WITHOUT ROWID;
CREATE TABLE v2_row(
  occurrence_id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL,
  parser_build_id TEXT NOT NULL,
  ingest_run_id TEXT NOT NULL,
  source_path TEXT NOT NULL REFERENCES source_file(path),
  source_ordinal INTEGER NOT NULL CHECK(source_ordinal>=0),
  source_pointer TEXT NOT NULL,
  raw_record_json TEXT NOT NULL,
  raw_record_sha256 TEXT NOT NULL,
  UNIQUE(source_path,source_ordinal),
  UNIQUE(source_path,source_pointer)
) WITHOUT ROWID;
CREATE TABLE v2_owner(
  owner_id TEXT PRIMARY KEY,
  row_occurrence_id TEXT NOT NULL REFERENCES v2_row(occurrence_id),
  field_name TEXT NOT NULL,
  field_pair_ordinal INTEGER NOT NULL CHECK(field_pair_ordinal>=0),
  raw_hash_json TEXT NOT NULL,
  raw_hash_text TEXT NOT NULL,
  overall_resolution_state TEXT NOT NULL CHECK(overall_resolution_state IN
    ('resolved_all_locales','partial_missing','resolved_candidate_set')),
  candidate_count INTEGER NOT NULL CHECK(candidate_count>=0),
  UNIQUE(row_occurrence_id,field_pair_ordinal)
) WITHOUT ROWID;
CREATE TABLE v2_resolution(
  resolution_id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL REFERENCES v2_owner(owner_id),
  locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')),
  state TEXT NOT NULL CHECK(state IN ('resolved_unique','resolved_candidate_set','missing')),
  candidate_count INTEGER NOT NULL CHECK(candidate_count>=0),
  UNIQUE(owner_id,locale)
) WITHOUT ROWID;
CREATE TABLE v2_candidate(
  candidate_id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL REFERENCES v2_owner(owner_id),
  textmap_snapshot_id TEXT NOT NULL,
  textmap_parser_build_id TEXT NOT NULL,
  textmap_ingest_run_id TEXT NOT NULL,
  locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')),
  tier TEXT NOT NULL CHECK(tier IN ('main','medium')),
  source_path TEXT NOT NULL REFERENCES source_file(path),
  pair_ordinal INTEGER NOT NULL CHECK(pair_ordinal>=0),
  source_record_pointer TEXT NOT NULL CHECK(source_record_pointer='/'),
  source_pointer TEXT NOT NULL,
  raw_key_json TEXT NOT NULL,
  raw_text_json TEXT NOT NULL,
  raw_text TEXT NOT NULL,
  raw_key_sha256 TEXT NOT NULL,
  raw_text_sha256 TEXT NOT NULL,
  raw_record_sha256 TEXT NOT NULL,
  rendered_text_sha256 TEXT NOT NULL,
  UNIQUE(owner_id,source_path,pair_ordinal)
) WITHOUT ROWID;
CREATE TABLE release(
  id INTEGER PRIMARY KEY CHECK(id=1),
  manifest_json TEXT NOT NULL
) WITHOUT ROWID;
CREATE INDEX v1_source_order ON v1_row(source_path,source_ordinal);
CREATE INDEX v2_source_order ON v2_row(source_path,source_ordinal);
CREATE INDEX v2_owner_field ON v2_owner(field_name,row_occurrence_id);
CREATE INDEX v2_candidate_text ON v2_candidate(raw_text,locale,owner_id);
CREATE TRIGGER source_file_sealed_insert BEFORE INSERT ON source_file WHEN EXISTS(SELECT 1 FROM release WHERE id=1) BEGIN SELECT RAISE(ABORT,'immutable M3 evidence'); END;
CREATE TRIGGER source_file_sealed_update BEFORE UPDATE ON source_file WHEN EXISTS(SELECT 1 FROM release WHERE id=1) BEGIN SELECT RAISE(ABORT,'immutable M3 evidence'); END;
CREATE TRIGGER source_file_sealed_delete BEFORE DELETE ON source_file WHEN EXISTS(SELECT 1 FROM release WHERE id=1) BEGIN SELECT RAISE(ABORT,'immutable M3 evidence'); END;
CREATE TRIGGER v1_row_sealed_insert BEFORE INSERT ON v1_row WHEN EXISTS(SELECT 1 FROM release WHERE id=1) BEGIN SELECT RAISE(ABORT,'immutable M3 evidence'); END;
CREATE TRIGGER v1_row_sealed_update BEFORE UPDATE ON v1_row WHEN EXISTS(SELECT 1 FROM release WHERE id=1) BEGIN SELECT RAISE(ABORT,'immutable M3 evidence'); END;
CREATE TRIGGER v1_row_sealed_delete BEFORE DELETE ON v1_row WHEN EXISTS(SELECT 1 FROM release WHERE id=1) BEGIN SELECT RAISE(ABORT,'immutable M3 evidence'); END;
CREATE TRIGGER v2_row_sealed_insert BEFORE INSERT ON v2_row WHEN EXISTS(SELECT 1 FROM release WHERE id=1) BEGIN SELECT RAISE(ABORT,'immutable M3 evidence'); END;
CREATE TRIGGER v2_row_sealed_update BEFORE UPDATE ON v2_row WHEN EXISTS(SELECT 1 FROM release WHERE id=1) BEGIN SELECT RAISE(ABORT,'immutable M3 evidence'); END;
CREATE TRIGGER v2_row_sealed_delete BEFORE DELETE ON v2_row WHEN EXISTS(SELECT 1 FROM release WHERE id=1) BEGIN SELECT RAISE(ABORT,'immutable M3 evidence'); END;
CREATE TRIGGER v2_owner_sealed_insert BEFORE INSERT ON v2_owner WHEN EXISTS(SELECT 1 FROM release WHERE id=1) BEGIN SELECT RAISE(ABORT,'immutable M3 evidence'); END;
CREATE TRIGGER v2_owner_sealed_update BEFORE UPDATE ON v2_owner WHEN EXISTS(SELECT 1 FROM release WHERE id=1) BEGIN SELECT RAISE(ABORT,'immutable M3 evidence'); END;
CREATE TRIGGER v2_owner_sealed_delete BEFORE DELETE ON v2_owner WHEN EXISTS(SELECT 1 FROM release WHERE id=1) BEGIN SELECT RAISE(ABORT,'immutable M3 evidence'); END;
CREATE TRIGGER v2_resolution_sealed_insert BEFORE INSERT ON v2_resolution WHEN EXISTS(SELECT 1 FROM release WHERE id=1) BEGIN SELECT RAISE(ABORT,'immutable M3 evidence'); END;
CREATE TRIGGER v2_resolution_sealed_update BEFORE UPDATE ON v2_resolution WHEN EXISTS(SELECT 1 FROM release WHERE id=1) BEGIN SELECT RAISE(ABORT,'immutable M3 evidence'); END;
CREATE TRIGGER v2_resolution_sealed_delete BEFORE DELETE ON v2_resolution WHEN EXISTS(SELECT 1 FROM release WHERE id=1) BEGIN SELECT RAISE(ABORT,'immutable M3 evidence'); END;
CREATE TRIGGER v2_candidate_sealed_insert BEFORE INSERT ON v2_candidate WHEN EXISTS(SELECT 1 FROM release WHERE id=1) BEGIN SELECT RAISE(ABORT,'immutable M3 evidence'); END;
CREATE TRIGGER v2_candidate_sealed_update BEFORE UPDATE ON v2_candidate WHEN EXISTS(SELECT 1 FROM release WHERE id=1) BEGIN SELECT RAISE(ABORT,'immutable M3 evidence'); END;
CREATE TRIGGER v2_candidate_sealed_delete BEFORE DELETE ON v2_candidate WHEN EXISTS(SELECT 1 FROM release WHERE id=1) BEGIN SELECT RAISE(ABORT,'immutable M3 evidence'); END;
CREATE TRIGGER release_sealed_insert BEFORE INSERT ON release WHEN EXISTS(SELECT 1 FROM release WHERE id=1) BEGIN SELECT RAISE(ABORT,'immutable M3 evidence'); END;
CREATE TRIGGER release_sealed_update BEFORE UPDATE ON release BEGIN SELECT RAISE(ABORT,'immutable M3 evidence'); END;
CREATE TRIGGER release_sealed_delete BEFORE DELETE ON release BEGIN SELECT RAISE(ABORT,'immutable M3 evidence'); END;
