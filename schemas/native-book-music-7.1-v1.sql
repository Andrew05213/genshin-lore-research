CREATE TABLE native_archive_container(
 id TEXT PRIMARY KEY,run_id TEXT NOT NULL,boundary TEXT NOT NULL,container_kind TEXT NOT NULL CHECK(container_kind IN('physical_row','projection_reuse')),
 source_path TEXT NOT NULL,source_file_sha256 TEXT NOT NULL,source_blob_oid TEXT NOT NULL,ordinal INTEGER NOT NULL,pointer TEXT NOT NULL,
 native_id_field TEXT,native_id TEXT,raw_json TEXT,raw_sha256 TEXT,
 reuse_family TEXT,reuse_contract TEXT,reuse_parser TEXT,reuse_run TEXT,reuse_occurrence_id TEXT,
 CHECK((container_kind='physical_row' AND raw_json IS NOT NULL AND raw_sha256 IS NOT NULL AND reuse_family IS NULL) OR
       (container_kind='projection_reuse' AND raw_json IS NULL AND raw_sha256 IS NULL AND reuse_family IS NOT NULL AND reuse_contract IS NOT NULL AND reuse_parser IS NOT NULL AND reuse_run IS NOT NULL AND reuse_occurrence_id IS NOT NULL)),
 UNIQUE(run_id,source_path,pointer)
) WITHOUT ROWID;
CREATE TABLE native_archive_slot(
 id TEXT PRIMARY KEY,container_id TEXT NOT NULL REFERENCES native_archive_container(id),owner_field TEXT NOT NULL,
 owner_role_status TEXT NOT NULL CHECK(owner_role_status IN('resolved','mapping_unresolved')),locale TEXT NOT NULL CHECK(locale IN('CHS','EN','JP')),
 raw_locator TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN('resolved_main','resolved_medium','missing','ambiguous')),candidate_count INTEGER NOT NULL,
 UNIQUE(container_id,owner_field,locale)
) WITHOUT ROWID;
CREATE TABLE native_archive_text(
 id TEXT PRIMARY KEY,slot_id TEXT NOT NULL REFERENCES native_archive_slot(id),source_path TEXT NOT NULL,source_file_sha256 TEXT NOT NULL,source_blob_oid TEXT NOT NULL,
 pair_ordinal INTEGER NOT NULL,pointer TEXT NOT NULL,raw_text TEXT NOT NULL,raw_text_sha256 TEXT NOT NULL,
 UNIQUE(slot_id,source_path,pair_ordinal)
) WITHOUT ROWID;
CREATE TABLE native_archive_relation(
 id TEXT PRIMARY KEY,container_id TEXT NOT NULL REFERENCES native_archive_container(id),relation_kind TEXT NOT NULL,source_field TEXT NOT NULL,source_pointer TEXT NOT NULL,
 raw_locator TEXT NOT NULL,candidate_namespace TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN('resolved_unique','missing','ambiguous','mapping_unresolved')),
 candidate_count INTEGER NOT NULL,search_complete INTEGER NOT NULL CHECK(search_complete IN(0,1)),UNIQUE(container_id,relation_kind,source_pointer)
) WITHOUT ROWID;
CREATE TABLE native_archive_candidate(
 id TEXT PRIMARY KEY,relation_id TEXT NOT NULL REFERENCES native_archive_relation(id),target_source_path TEXT NOT NULL,target_source_pointer TEXT NOT NULL,target_native_id TEXT,
 target_family TEXT NOT NULL,target_contract TEXT NOT NULL,target_parser TEXT NOT NULL,target_run TEXT NOT NULL,target_occurrence_id TEXT NOT NULL,
 UNIQUE(relation_id,target_source_path,target_source_pointer,target_run,target_occurrence_id)
) WITHOUT ROWID;
CREATE TABLE native_archive_release(id INTEGER PRIMARY KEY CHECK(id=1),manifest_json TEXT NOT NULL) WITHOUT ROWID;
CREATE INDEX native_archive_container_boundary ON native_archive_container(boundary,id);
CREATE INDEX native_archive_slot_parent ON native_archive_slot(container_id,id);
CREATE INDEX native_archive_text_slot ON native_archive_text(slot_id,id);
CREATE INDEX native_archive_text_value ON native_archive_text(raw_text);
CREATE INDEX native_archive_relation_parent ON native_archive_relation(container_id,id);
CREATE INDEX native_archive_candidate_parent ON native_archive_candidate(relation_id,id);
CREATE TRIGGER native_archive_container_i BEFORE INSERT ON native_archive_container WHEN EXISTS(SELECT 1 FROM native_archive_release) BEGIN SELECT RAISE(ABORT,'immutable sealed native Book/Music 7.1');END;
CREATE TRIGGER native_archive_slot_i BEFORE INSERT ON native_archive_slot WHEN EXISTS(SELECT 1 FROM native_archive_release) BEGIN SELECT RAISE(ABORT,'immutable sealed native Book/Music 7.1');END;
CREATE TRIGGER native_archive_text_i BEFORE INSERT ON native_archive_text WHEN EXISTS(SELECT 1 FROM native_archive_release) BEGIN SELECT RAISE(ABORT,'immutable sealed native Book/Music 7.1');END;
CREATE TRIGGER native_archive_relation_i BEFORE INSERT ON native_archive_relation WHEN EXISTS(SELECT 1 FROM native_archive_release) BEGIN SELECT RAISE(ABORT,'immutable sealed native Book/Music 7.1');END;
CREATE TRIGGER native_archive_candidate_i BEFORE INSERT ON native_archive_candidate WHEN EXISTS(SELECT 1 FROM native_archive_release) BEGIN SELECT RAISE(ABORT,'immutable sealed native Book/Music 7.1');END;
CREATE TRIGGER native_archive_release_i BEFORE INSERT ON native_archive_release WHEN EXISTS(SELECT 1 FROM native_archive_release) BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER native_archive_container_u BEFORE UPDATE ON native_archive_container BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER native_archive_container_d BEFORE DELETE ON native_archive_container BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER native_archive_slot_u BEFORE UPDATE ON native_archive_slot BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER native_archive_slot_d BEFORE DELETE ON native_archive_slot BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER native_archive_text_u BEFORE UPDATE ON native_archive_text BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER native_archive_text_d BEFORE DELETE ON native_archive_text BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER native_archive_relation_u BEFORE UPDATE ON native_archive_relation BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER native_archive_relation_d BEFORE DELETE ON native_archive_relation BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER native_archive_candidate_u BEFORE UPDATE ON native_archive_candidate BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER native_archive_candidate_d BEFORE DELETE ON native_archive_candidate BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER native_archive_release_u BEFORE UPDATE ON native_archive_release BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER native_archive_release_d BEFORE DELETE ON native_archive_release BEGIN SELECT RAISE(ABORT,'immutable');END;
