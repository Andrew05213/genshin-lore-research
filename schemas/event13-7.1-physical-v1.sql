CREATE TABLE physical_row(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, boundary TEXT NOT NULL,
 source_path TEXT NOT NULL, source_sha256 TEXT NOT NULL, source_blob TEXT NOT NULL,
 ordinal INTEGER NOT NULL, pointer TEXT NOT NULL, raw_json TEXT NOT NULL,
 raw_sha256 TEXT NOT NULL, UNIQUE(source_path,ordinal)
) WITHOUT ROWID;
CREATE TABLE numeric_observation(
 id TEXT PRIMARY KEY, row_id TEXT NOT NULL REFERENCES physical_row(id),
 field_pointer TEXT NOT NULL, raw_value TEXT NOT NULL,
 semantic_role TEXT NOT NULL CHECK(semantic_role='uninterpreted_raw_numeric'),
 UNIQUE(row_id,field_pointer)
) WITHOUT ROWID;
CREATE TABLE textmap_candidate(
 id TEXT PRIMARY KEY, observation_id TEXT NOT NULL REFERENCES numeric_observation(id),
 locale TEXT NOT NULL, tier TEXT NOT NULL CHECK(tier IN ('main','Medium')),
 source_path TEXT NOT NULL, source_sha256 TEXT NOT NULL, source_blob TEXT NOT NULL,
 pair_ordinal INTEGER NOT NULL, pointer TEXT NOT NULL, raw_text TEXT NOT NULL,
 raw_text_sha256 TEXT NOT NULL,
 UNIQUE(observation_id,locale,tier,source_path,pair_ordinal)
) WITHOUT ROWID;
CREATE TABLE release(id INTEGER PRIMARY KEY CHECK(id=1), manifest_json TEXT NOT NULL) WITHOUT ROWID;
CREATE INDEX numeric_observation_row ON numeric_observation(row_id);
CREATE INDEX textmap_candidate_observation ON textmap_candidate(observation_id);
CREATE TRIGGER physical_row_i BEFORE INSERT ON physical_row WHEN EXISTS(SELECT 1 FROM release) BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER numeric_observation_i BEFORE INSERT ON numeric_observation WHEN EXISTS(SELECT 1 FROM release) BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER textmap_candidate_i BEFORE INSERT ON textmap_candidate WHEN EXISTS(SELECT 1 FROM release) BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER release_i BEFORE INSERT ON release WHEN EXISTS(SELECT 1 FROM release) BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER physical_row_u BEFORE UPDATE ON physical_row BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER numeric_observation_u BEFORE UPDATE ON numeric_observation BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER textmap_candidate_u BEFORE UPDATE ON textmap_candidate BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER release_u BEFORE UPDATE ON release BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER physical_row_d BEFORE DELETE ON physical_row BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER numeric_observation_d BEFORE DELETE ON numeric_observation BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER textmap_candidate_d BEFORE DELETE ON textmap_candidate BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER release_d BEFORE DELETE ON release BEGIN SELECT RAISE(ABORT,'immutable');END;
