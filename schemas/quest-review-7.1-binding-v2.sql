PRAGMA foreign_keys=ON;
CREATE TABLE bound_locator(
 id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL,
 source_locator_id TEXT NOT NULL UNIQUE,
 source_review_row_id TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('summarization_id_observation','review_member_locator_observation')),
 source_pointer TEXT NOT NULL,
 raw_value TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status='mapping_unresolved'),
 search_complete INTEGER NOT NULL CHECK(search_complete=0),
 candidate_count INTEGER NOT NULL CHECK(candidate_count>=0)
) WITHOUT ROWID;
CREATE TABLE bound_candidate(
 id TEXT PRIMARY KEY,
 locator_id TEXT NOT NULL REFERENCES bound_locator(id),
 target_family TEXT NOT NULL,
 target_contract TEXT NOT NULL,
 target_parser TEXT NOT NULL,
 target_run TEXT NOT NULL,
 target_occurrence_id TEXT NOT NULL,
 target_source_path TEXT NOT NULL,
 target_source_pointer TEXT NOT NULL,
 target_native_id TEXT NOT NULL,
 target_raw_sha256 TEXT NOT NULL,
 locator_kind TEXT NOT NULL,
 UNIQUE(locator_id,target_run,target_occurrence_id)
) WITHOUT ROWID;
CREATE TABLE release(singleton INTEGER PRIMARY KEY CHECK(singleton=1),manifest_json TEXT NOT NULL) WITHOUT ROWID;
CREATE INDEX candidate_locator ON bound_candidate(locator_id,id);
CREATE INDEX candidate_target ON bound_candidate(target_run,target_occurrence_id,id);
