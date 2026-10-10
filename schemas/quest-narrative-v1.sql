PRAGMA foreign_keys=ON;
CREATE TABLE quest_narrative_container(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, kind TEXT NOT NULL,
 source_path TEXT NOT NULL, source_ordinal INTEGER, source_pointer TEXT NOT NULL,
 native_id TEXT, source_blob_oid TEXT NOT NULL, raw_sha256 TEXT NOT NULL, raw_json TEXT,
 mapping_status TEXT NOT NULL,
 target_family TEXT, target_contract TEXT, target_run TEXT, target_occurrence_id TEXT,
 UNIQUE(run_id,source_path,source_pointer)
) WITHOUT ROWID;
CREATE TABLE quest_narrative_child(
 id TEXT PRIMARY KEY, container_id TEXT NOT NULL REFERENCES quest_narrative_container(id),
 source_pointer TEXT NOT NULL, shape TEXT NOT NULL, ordinal INTEGER NOT NULL,
 native_sub_id TEXT, source_order INTEGER, mapping_status TEXT NOT NULL,
 raw_sha256 TEXT NOT NULL, target_step_container_id TEXT REFERENCES quest_narrative_container(id),
 UNIQUE(container_id,source_pointer)
) WITHOUT ROWID;
CREATE TABLE quest_narrative_relation(
 id TEXT PRIMARY KEY, container_id TEXT NOT NULL REFERENCES quest_narrative_container(id),
 kind TEXT NOT NULL, source_pointer TEXT NOT NULL, raw_locator_json TEXT NOT NULL,
 candidate_namespace TEXT NOT NULL, status TEXT NOT NULL,
 candidate_count INTEGER NOT NULL, search_complete INTEGER NOT NULL CHECK(search_complete IN(0,1)),
 reused_relation_occurrence_id TEXT,
 UNIQUE(container_id,kind,source_pointer)
) WITHOUT ROWID;
CREATE TABLE quest_narrative_candidate(
 id TEXT PRIMARY KEY, relation_id TEXT NOT NULL REFERENCES quest_narrative_relation(id),
 target_source_path TEXT NOT NULL, target_source_pointer TEXT NOT NULL,
 target_native_id TEXT, target_family TEXT NOT NULL, target_contract TEXT NOT NULL,
 target_run TEXT NOT NULL, target_occurrence_id TEXT NOT NULL,
 UNIQUE(relation_id,target_source_path,target_source_pointer,target_run,target_occurrence_id)
) WITHOUT ROWID;
CREATE TABLE quest_narrative_text_slot(
 id TEXT PRIMARY KEY, container_id TEXT NOT NULL REFERENCES quest_narrative_container(id),
 owner_field TEXT NOT NULL, locale TEXT NOT NULL CHECK(locale IN('CHS','EN','JP')),
 raw_locator TEXT NOT NULL, status TEXT NOT NULL, candidate_count INTEGER NOT NULL,
 UNIQUE(container_id,owner_field,locale)
) WITHOUT ROWID;
CREATE TABLE quest_narrative_text_candidate(
 id TEXT PRIMARY KEY, slot_id TEXT NOT NULL REFERENCES quest_narrative_text_slot(id),
 source_path TEXT NOT NULL, source_ordinal INTEGER NOT NULL, source_pointer TEXT NOT NULL,
 raw_text TEXT NOT NULL, UNIQUE(slot_id,source_path,source_ordinal,source_pointer)
) WITHOUT ROWID;
CREATE TABLE quest_narrative_release(id INTEGER PRIMARY KEY CHECK(id=1),manifest_json TEXT NOT NULL) WITHOUT ROWID;
CREATE INDEX qn_container_kind ON quest_narrative_container(kind,id);
CREATE INDEX qn_container_native ON quest_narrative_container(native_id,kind,id);
CREATE INDEX qn_child_parent ON quest_narrative_child(container_id,id);
CREATE INDEX qn_relation_parent ON quest_narrative_relation(container_id,kind,id);
CREATE INDEX qn_candidate_relation ON quest_narrative_candidate(relation_id,id);
CREATE INDEX qn_slot_parent ON quest_narrative_text_slot(container_id,locale,id);
CREATE INDEX qn_text_search ON quest_narrative_text_candidate(slot_id,id);

CREATE TRIGGER qn_container_i BEFORE INSERT ON quest_narrative_container WHEN EXISTS(SELECT 1 FROM quest_narrative_container WHERE id=NEW.id OR (run_id=NEW.run_id AND source_path=NEW.source_path AND source_pointer=NEW.source_pointer)) BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER qn_child_i BEFORE INSERT ON quest_narrative_child WHEN EXISTS(SELECT 1 FROM quest_narrative_child WHERE id=NEW.id OR (container_id=NEW.container_id AND source_pointer=NEW.source_pointer)) BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER qn_relation_i BEFORE INSERT ON quest_narrative_relation WHEN EXISTS(SELECT 1 FROM quest_narrative_relation WHERE id=NEW.id OR (container_id=NEW.container_id AND kind=NEW.kind AND source_pointer=NEW.source_pointer)) BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER qn_candidate_i BEFORE INSERT ON quest_narrative_candidate WHEN EXISTS(SELECT 1 FROM quest_narrative_candidate WHERE id=NEW.id OR (relation_id=NEW.relation_id AND target_source_path=NEW.target_source_path AND target_source_pointer=NEW.target_source_pointer AND target_run=NEW.target_run AND target_occurrence_id=NEW.target_occurrence_id)) BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER qn_slot_i BEFORE INSERT ON quest_narrative_text_slot WHEN EXISTS(SELECT 1 FROM quest_narrative_text_slot WHERE id=NEW.id OR (container_id=NEW.container_id AND owner_field=NEW.owner_field AND locale=NEW.locale)) BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER qn_text_i BEFORE INSERT ON quest_narrative_text_candidate WHEN EXISTS(SELECT 1 FROM quest_narrative_text_candidate WHERE id=NEW.id OR (slot_id=NEW.slot_id AND source_path=NEW.source_path AND source_ordinal=NEW.source_ordinal AND source_pointer=NEW.source_pointer)) BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER qn_release_i BEFORE INSERT ON quest_narrative_release WHEN EXISTS(SELECT 1 FROM quest_narrative_release WHERE id=NEW.id) BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER qn_container_u BEFORE UPDATE ON quest_narrative_container BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER qn_container_d BEFORE DELETE ON quest_narrative_container BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER qn_child_u BEFORE UPDATE ON quest_narrative_child BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER qn_child_d BEFORE DELETE ON quest_narrative_child BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER qn_relation_u BEFORE UPDATE ON quest_narrative_relation BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER qn_relation_d BEFORE DELETE ON quest_narrative_relation BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER qn_candidate_u BEFORE UPDATE ON quest_narrative_candidate BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER qn_candidate_d BEFORE DELETE ON quest_narrative_candidate BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER qn_slot_u BEFORE UPDATE ON quest_narrative_text_slot BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER qn_slot_d BEFORE DELETE ON quest_narrative_text_slot BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER qn_text_u BEFORE UPDATE ON quest_narrative_text_candidate BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER qn_text_d BEFORE DELETE ON quest_narrative_text_candidate BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER qn_release_u BEFORE UPDATE ON quest_narrative_release BEGIN SELECT RAISE(ABORT,'immutable');END;
CREATE TRIGGER qn_release_d BEFORE DELETE ON quest_narrative_release BEGIN SELECT RAISE(ABORT,'immutable');END;
