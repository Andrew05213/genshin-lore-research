CREATE TABLE selected_occurrence(
 id TEXT PRIMARY KEY,run_id TEXT NOT NULL,component TEXT NOT NULL CHECK(component IN('16','18')),
 source_kind TEXT NOT NULL,source_path TEXT NOT NULL,ordinal INTEGER NOT NULL,
 pointer TEXT NOT NULL,raw_sha256 TEXT NOT NULL,
 projection_kind TEXT NOT NULL CHECK(projection_kind IN('local_raw','accepted_readthrough')),
 raw_json TEXT,target_family TEXT,target_contract TEXT,target_parser TEXT,
 target_run TEXT,target_occurrence_id TEXT,
 CHECK((projection_kind='local_raw' AND raw_json IS NOT NULL AND target_family IS NULL)
 OR (projection_kind='accepted_readthrough' AND raw_json IS NULL AND
 target_family IS NOT NULL AND target_contract IS NOT NULL AND target_parser IS NOT NULL
 AND target_run IS NOT NULL AND target_occurrence_id IS NOT NULL)),
 UNIQUE(component,source_path,pointer)
) WITHOUT ROWID;
CREATE TABLE release(id INTEGER PRIMARY KEY CHECK(id=1),manifest_json TEXT NOT NULL) WITHOUT ROWID;
CREATE INDEX selected_occurrence_source ON selected_occurrence(source_path,ordinal);
CREATE INDEX selected_occurrence_target ON selected_occurrence(target_family,target_occurrence_id);
CREATE TRIGGER selected_occurrence_i BEFORE INSERT ON selected_occurrence WHEN EXISTS(SELECT 1 FROM release) BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER release_i BEFORE INSERT ON release WHEN EXISTS(SELECT 1 FROM release) BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER selected_occurrence_u BEFORE UPDATE ON selected_occurrence BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER selected_occurrence_d BEFORE DELETE ON selected_occurrence BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER release_u BEFORE UPDATE ON release BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER release_d BEFORE DELETE ON release BEGIN SELECT RAISE(ABORT,'immutable');END;
