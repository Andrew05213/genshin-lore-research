PRAGMA foreign_keys=ON;
CREATE TABLE quest_review_row(
 id TEXT PRIMARY KEY,run_id TEXT NOT NULL,kind TEXT NOT NULL,source_path TEXT NOT NULL,
 source_ordinal INTEGER NOT NULL,source_pointer TEXT NOT NULL,native_id TEXT NOT NULL,
 source_blob_oid TEXT NOT NULL,source_file_sha256 TEXT NOT NULL,raw_record_sha256 TEXT NOT NULL,
 raw_record_json TEXT NOT NULL,classification TEXT NOT NULL,
 UNIQUE(run_id,source_path,source_pointer)
) WITHOUT ROWID;
CREATE TABLE quest_review_relation(
 id TEXT PRIMARY KEY,row_id TEXT NOT NULL REFERENCES quest_review_row(id),kind TEXT NOT NULL,
 source_pointer TEXT NOT NULL,raw_value_json TEXT NOT NULL,candidate_namespace TEXT NOT NULL,
 status TEXT NOT NULL,candidate_count INTEGER NOT NULL,search_complete INTEGER NOT NULL,
 relation_semantics TEXT NOT NULL,UNIQUE(row_id,kind,source_pointer)
) WITHOUT ROWID;
CREATE TABLE quest_review_candidate(
 id TEXT PRIMARY KEY,relation_id TEXT NOT NULL REFERENCES quest_review_relation(id),
 target_source_path TEXT NOT NULL,target_source_pointer TEXT NOT NULL,target_native_id TEXT,
 target_family TEXT NOT NULL,target_contract TEXT NOT NULL,target_run TEXT NOT NULL,
 target_occurrence_id TEXT NOT NULL,locator_kind TEXT NOT NULL,
 UNIQUE(relation_id,target_source_path,target_source_pointer,target_run,target_occurrence_id)
) WITHOUT ROWID;
CREATE TABLE quest_review_text_slot(
 id TEXT PRIMARY KEY,row_id TEXT NOT NULL REFERENCES quest_review_row(id),owner_field TEXT NOT NULL,
 locale TEXT NOT NULL,raw_locator TEXT NOT NULL,status TEXT NOT NULL,candidate_count INTEGER NOT NULL,
 UNIQUE(row_id,owner_field,locale)
) WITHOUT ROWID;
CREATE TABLE quest_review_text_candidate(
 id TEXT PRIMARY KEY,slot_id TEXT NOT NULL REFERENCES quest_review_text_slot(id),
 source_path TEXT NOT NULL,source_blob_oid TEXT NOT NULL,source_ordinal INTEGER NOT NULL,
 source_pointer TEXT NOT NULL,raw_text TEXT NOT NULL,raw_text_sha256 TEXT NOT NULL,
 UNIQUE(slot_id,source_path,source_ordinal,source_pointer)
) WITHOUT ROWID;
CREATE TABLE quest_review_release(singleton INTEGER PRIMARY KEY CHECK(singleton=1),manifest_json TEXT NOT NULL) WITHOUT ROWID;
CREATE INDEX qr_row_kind ON quest_review_row(kind,id);
CREATE INDEX qr_row_native ON quest_review_row(native_id,kind,id);
CREATE INDEX qr_relation_parent ON quest_review_relation(row_id,kind,id);
CREATE INDEX qr_candidate_relation ON quest_review_candidate(relation_id,id);
CREATE INDEX qr_candidate_target ON quest_review_candidate(target_run,target_occurrence_id,id);
CREATE INDEX qr_slot_parent ON quest_review_text_slot(row_id,locale,id);
CREATE INDEX qr_text_search ON quest_review_text_candidate(slot_id,id);
CREATE TRIGGER qr_row_insert BEFORE INSERT ON quest_review_row WHEN EXISTS(SELECT 1 FROM quest_review_row WHERE id=NEW.id OR (run_id=NEW.run_id AND source_path=NEW.source_path AND source_pointer=NEW.source_pointer)) BEGIN SELECT RAISE(ABORT,'immutable quest review row'); END;
CREATE TRIGGER qr_relation_insert BEFORE INSERT ON quest_review_relation WHEN EXISTS(SELECT 1 FROM quest_review_relation WHERE id=NEW.id OR (row_id=NEW.row_id AND kind=NEW.kind AND source_pointer=NEW.source_pointer)) BEGIN SELECT RAISE(ABORT,'immutable quest review relation'); END;
CREATE TRIGGER qr_candidate_insert BEFORE INSERT ON quest_review_candidate WHEN EXISTS(SELECT 1 FROM quest_review_candidate WHERE id=NEW.id OR (relation_id=NEW.relation_id AND target_source_path=NEW.target_source_path AND target_source_pointer=NEW.target_source_pointer AND target_run=NEW.target_run AND target_occurrence_id=NEW.target_occurrence_id)) BEGIN SELECT RAISE(ABORT,'immutable quest review candidate'); END;
CREATE TRIGGER qr_slot_insert BEFORE INSERT ON quest_review_text_slot WHEN EXISTS(SELECT 1 FROM quest_review_text_slot WHERE id=NEW.id OR (row_id=NEW.row_id AND owner_field=NEW.owner_field AND locale=NEW.locale)) BEGIN SELECT RAISE(ABORT,'immutable quest review slot'); END;
CREATE TRIGGER qr_text_insert BEFORE INSERT ON quest_review_text_candidate WHEN EXISTS(SELECT 1 FROM quest_review_text_candidate WHERE id=NEW.id OR (slot_id=NEW.slot_id AND source_path=NEW.source_path AND source_ordinal=NEW.source_ordinal AND source_pointer=NEW.source_pointer)) BEGIN SELECT RAISE(ABORT,'immutable quest review text'); END;
CREATE TRIGGER qr_release_insert BEFORE INSERT ON quest_review_release WHEN EXISTS(SELECT 1 FROM quest_review_release) BEGIN SELECT RAISE(ABORT,'immutable quest review release'); END;
CREATE TRIGGER qr_row_update BEFORE UPDATE ON quest_review_row BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER qr_row_delete BEFORE DELETE ON quest_review_row BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER qr_relation_update BEFORE UPDATE ON quest_review_relation BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER qr_relation_delete BEFORE DELETE ON quest_review_relation BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER qr_candidate_update BEFORE UPDATE ON quest_review_candidate BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER qr_candidate_delete BEFORE DELETE ON quest_review_candidate BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER qr_slot_update BEFORE UPDATE ON quest_review_text_slot BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER qr_slot_delete BEFORE DELETE ON quest_review_text_slot BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER qr_text_update BEFORE UPDATE ON quest_review_text_candidate BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER qr_text_delete BEFORE DELETE ON quest_review_text_candidate BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER qr_release_update BEFORE UPDATE ON quest_review_release BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER qr_release_delete BEFORE DELETE ON quest_review_release BEGIN SELECT RAISE(ABORT,'write once'); END;
