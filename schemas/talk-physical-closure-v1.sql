PRAGMA foreign_keys=ON;
PRAGMA page_size=4096;
CREATE TABLE talk_physical_file(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, source_path TEXT NOT NULL UNIQUE,
 source_blob_oid TEXT NOT NULL, source_sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
 root_keys_json TEXT NOT NULL, classification TEXT NOT NULL CHECK(classification IN ('mapped_program_reuse','reference_wrapper','mapping_unresolved_terminal')),
 shape TEXT, raw_bytes BLOB, target_family TEXT, target_contract TEXT, target_run TEXT, target_occurrence_id TEXT,
 CHECK((classification='mapped_program_reuse' AND raw_bytes IS NULL AND target_run IS NOT NULL AND target_occurrence_id IS NOT NULL) OR
       (classification!='mapped_program_reuse' AND raw_bytes IS NOT NULL AND target_run IS NULL AND target_occurrence_id IS NULL))
) WITHOUT ROWID;
CREATE TABLE talk_physical_reference(
 id TEXT PRIMARY KEY, file_id TEXT NOT NULL REFERENCES talk_physical_file(id), pointer TEXT NOT NULL,
 raw_native_id TEXT NOT NULL, raw_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('known_candidate_set','mapping_unresolved_terminal_namespace')),
 known_candidate_count INTEGER NOT NULL, physical_scan_complete INTEGER NOT NULL CHECK(physical_scan_complete=1),
 semantic_search_complete INTEGER NOT NULL CHECK(semantic_search_complete=0),
 UNIQUE(file_id,pointer)
) WITHOUT ROWID;
CREATE TABLE talk_physical_candidate(
 id TEXT PRIMARY KEY, reference_id TEXT NOT NULL REFERENCES talk_physical_reference(id),
 target_source_path TEXT NOT NULL, target_source_pointer TEXT NOT NULL CHECK(target_source_pointer='/'),
 target_native_id TEXT NOT NULL, target_family TEXT NOT NULL, target_contract TEXT NOT NULL,
 target_run TEXT NOT NULL, target_occurrence_id TEXT NOT NULL,
 UNIQUE(reference_id,target_source_path,target_source_pointer)
) WITHOUT ROWID;
CREATE TABLE talk_physical_release(id INTEGER PRIMARY KEY CHECK(id=1),manifest_json TEXT NOT NULL) WITHOUT ROWID;
CREATE INDEX talk_physical_file_classification ON talk_physical_file(classification,shape,id);
CREATE INDEX talk_physical_reference_file ON talk_physical_reference(file_id,id);
CREATE INDEX talk_physical_candidate_reference ON talk_physical_candidate(reference_id,id);

CREATE TRIGGER talk_physical_file_insert BEFORE INSERT ON talk_physical_file WHEN EXISTS(SELECT 1 FROM talk_physical_file WHERE id=NEW.id OR source_path=NEW.source_path) BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER talk_physical_file_update BEFORE UPDATE ON talk_physical_file BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER talk_physical_file_delete BEFORE DELETE ON talk_physical_file BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER talk_physical_reference_insert BEFORE INSERT ON talk_physical_reference WHEN EXISTS(SELECT 1 FROM talk_physical_reference WHERE id=NEW.id OR (file_id=NEW.file_id AND pointer=NEW.pointer)) BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER talk_physical_reference_update BEFORE UPDATE ON talk_physical_reference BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER talk_physical_reference_delete BEFORE DELETE ON talk_physical_reference BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER talk_physical_candidate_insert BEFORE INSERT ON talk_physical_candidate WHEN EXISTS(SELECT 1 FROM talk_physical_candidate WHERE id=NEW.id OR (reference_id=NEW.reference_id AND target_source_path=NEW.target_source_path AND target_source_pointer=NEW.target_source_pointer)) BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER talk_physical_candidate_update BEFORE UPDATE ON talk_physical_candidate BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER talk_physical_candidate_delete BEFORE DELETE ON talk_physical_candidate BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER talk_physical_release_insert BEFORE INSERT ON talk_physical_release WHEN EXISTS(SELECT 1 FROM talk_physical_release WHERE id=NEW.id) BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER talk_physical_release_update BEFORE UPDATE ON talk_physical_release BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER talk_physical_release_delete BEFORE DELETE ON talk_physical_release BEGIN SELECT RAISE(ABORT,'immutable'); END;
