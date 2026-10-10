PRAGMA foreign_keys=ON;
CREATE TABLE codexquest_container(
 id TEXT PRIMARY KEY,run_id TEXT NOT NULL,shape TEXT NOT NULL,source_path TEXT NOT NULL,
 source_pointer TEXT NOT NULL,source_blob_oid TEXT NOT NULL,source_size INTEGER NOT NULL,
 source_sha256 TEXT NOT NULL,native_id TEXT NOT NULL,quest_type TEXT NOT NULL,
 classification TEXT NOT NULL,UNIQUE(run_id,source_path,source_pointer)
) WITHOUT ROWID;
CREATE TABLE codexquest_body_chunk(
 id TEXT PRIMARY KEY,container_id TEXT NOT NULL REFERENCES codexquest_container(id),chunk_ordinal INTEGER NOT NULL,
 byte_start INTEGER NOT NULL,byte_end INTEGER NOT NULL,raw_text TEXT NOT NULL,raw_sha256 TEXT NOT NULL,
 UNIQUE(container_id,chunk_ordinal),UNIQUE(container_id,byte_start,byte_end)
) WITHOUT ROWID;
CREATE TABLE codexquest_child(
 id TEXT PRIMARY KEY,container_id TEXT NOT NULL REFERENCES codexquest_container(id),kind TEXT NOT NULL,
 source_pointer TEXT NOT NULL,ordinal INTEGER NOT NULL,raw_sha256 TEXT NOT NULL,raw_json TEXT NOT NULL,
 UNIQUE(container_id,source_pointer)
) WITHOUT ROWID;
CREATE TABLE codexquest_relation(
 id TEXT PRIMARY KEY,container_id TEXT NOT NULL REFERENCES codexquest_container(id),kind TEXT NOT NULL,
 source_pointer TEXT NOT NULL,raw_value_json TEXT NOT NULL,candidate_namespace TEXT NOT NULL,
 status TEXT NOT NULL,candidate_count INTEGER NOT NULL,search_complete INTEGER NOT NULL,
 relation_semantics TEXT NOT NULL,UNIQUE(container_id,kind,source_pointer)
) WITHOUT ROWID;
CREATE TABLE codexquest_candidate(
 id TEXT PRIMARY KEY,relation_id TEXT NOT NULL REFERENCES codexquest_relation(id),candidate_ordinal INTEGER NOT NULL,
 target_source_path TEXT NOT NULL,target_source_pointer TEXT NOT NULL,target_native_id TEXT,
 target_family TEXT NOT NULL,target_contract TEXT NOT NULL,target_run TEXT NOT NULL,
 target_occurrence_id TEXT NOT NULL,locator_kind TEXT NOT NULL,
 UNIQUE(relation_id,target_source_path,target_source_pointer,target_run,target_occurrence_id)
) WITHOUT ROWID;
CREATE TABLE codexquest_text_slot(
 id TEXT PRIMARY KEY,container_id TEXT NOT NULL REFERENCES codexquest_container(id),source_pointer TEXT NOT NULL,
 owner_field TEXT NOT NULL,locale TEXT NOT NULL,raw_locator TEXT NOT NULL,status TEXT NOT NULL,
 candidate_count INTEGER NOT NULL,UNIQUE(container_id,source_pointer,locale)
) WITHOUT ROWID;
CREATE TABLE codexquest_text_occurrence(
 id TEXT PRIMARY KEY,source_path TEXT NOT NULL,source_blob_oid TEXT NOT NULL,source_ordinal INTEGER NOT NULL,
 source_pointer TEXT NOT NULL,locale TEXT NOT NULL,shard_kind TEXT NOT NULL,raw_text TEXT NOT NULL,
 raw_text_sha256 TEXT NOT NULL,UNIQUE(source_path,source_ordinal,source_pointer)
) WITHOUT ROWID;
CREATE TABLE codexquest_text_candidate(
 id TEXT PRIMARY KEY,slot_id TEXT NOT NULL REFERENCES codexquest_text_slot(id),
 text_occurrence_id TEXT NOT NULL REFERENCES codexquest_text_occurrence(id),
 UNIQUE(slot_id,text_occurrence_id)
) WITHOUT ROWID;
CREATE TABLE codexquest_release(singleton INTEGER PRIMARY KEY CHECK(singleton=1),manifest_json TEXT NOT NULL) WITHOUT ROWID;
CREATE INDEX cq_container_shape ON codexquest_container(shape,id);
CREATE INDEX cq_container_native ON codexquest_container(native_id,id);
CREATE INDEX cq_body_parent ON codexquest_body_chunk(container_id,chunk_ordinal,id);
CREATE INDEX cq_child_parent ON codexquest_child(container_id,kind,id);
CREATE INDEX cq_relation_parent ON codexquest_relation(container_id,kind,id);
CREATE INDEX cq_candidate_parent ON codexquest_candidate(relation_id,id);
CREATE INDEX cq_candidate_target ON codexquest_candidate(target_run,target_occurrence_id,id);
CREATE INDEX cq_slot_parent ON codexquest_text_slot(container_id,locale,id);
CREATE INDEX cq_text_slot ON codexquest_text_candidate(slot_id,id);
CREATE INDEX cq_text_candidate_occurrence ON codexquest_text_candidate(text_occurrence_id,id);
CREATE INDEX cq_text_occurrence_search ON codexquest_text_occurrence(locale,id);
CREATE TRIGGER cq_container_insert BEFORE INSERT ON codexquest_container WHEN EXISTS(SELECT 1 FROM codexquest_container WHERE id=NEW.id OR (run_id=NEW.run_id AND source_path=NEW.source_path AND source_pointer=NEW.source_pointer)) BEGIN SELECT RAISE(ABORT,'immutable CodexQuest container'); END;
CREATE TRIGGER cq_body_insert BEFORE INSERT ON codexquest_body_chunk WHEN EXISTS(SELECT 1 FROM codexquest_body_chunk WHERE id=NEW.id OR (container_id=NEW.container_id AND chunk_ordinal=NEW.chunk_ordinal) OR (container_id=NEW.container_id AND byte_start=NEW.byte_start AND byte_end=NEW.byte_end)) BEGIN SELECT RAISE(ABORT,'immutable CodexQuest body chunk'); END;
CREATE TRIGGER cq_child_insert BEFORE INSERT ON codexquest_child WHEN EXISTS(SELECT 1 FROM codexquest_child WHERE id=NEW.id OR (container_id=NEW.container_id AND source_pointer=NEW.source_pointer)) BEGIN SELECT RAISE(ABORT,'immutable CodexQuest child'); END;
CREATE TRIGGER cq_relation_insert BEFORE INSERT ON codexquest_relation WHEN EXISTS(SELECT 1 FROM codexquest_relation WHERE id=NEW.id OR (container_id=NEW.container_id AND kind=NEW.kind AND source_pointer=NEW.source_pointer)) BEGIN SELECT RAISE(ABORT,'immutable CodexQuest relation'); END;
CREATE TRIGGER cq_candidate_insert BEFORE INSERT ON codexquest_candidate WHEN EXISTS(SELECT 1 FROM codexquest_candidate WHERE id=NEW.id OR (relation_id=NEW.relation_id AND target_source_path=NEW.target_source_path AND target_source_pointer=NEW.target_source_pointer AND target_run=NEW.target_run AND target_occurrence_id=NEW.target_occurrence_id)) BEGIN SELECT RAISE(ABORT,'immutable CodexQuest candidate'); END;
CREATE TRIGGER cq_slot_insert BEFORE INSERT ON codexquest_text_slot WHEN EXISTS(SELECT 1 FROM codexquest_text_slot WHERE id=NEW.id OR (container_id=NEW.container_id AND source_pointer=NEW.source_pointer AND locale=NEW.locale)) BEGIN SELECT RAISE(ABORT,'immutable CodexQuest slot'); END;
CREATE TRIGGER cq_text_occurrence_insert BEFORE INSERT ON codexquest_text_occurrence WHEN EXISTS(SELECT 1 FROM codexquest_text_occurrence WHERE id=NEW.id OR (source_path=NEW.source_path AND source_ordinal=NEW.source_ordinal AND source_pointer=NEW.source_pointer)) BEGIN SELECT RAISE(ABORT,'immutable CodexQuest text occurrence'); END;
CREATE TRIGGER cq_text_insert BEFORE INSERT ON codexquest_text_candidate WHEN EXISTS(SELECT 1 FROM codexquest_text_candidate WHERE id=NEW.id OR (slot_id=NEW.slot_id AND text_occurrence_id=NEW.text_occurrence_id)) BEGIN SELECT RAISE(ABORT,'immutable CodexQuest text candidate'); END;
CREATE TRIGGER cq_release_insert BEFORE INSERT ON codexquest_release WHEN EXISTS(SELECT 1 FROM codexquest_release) BEGIN SELECT RAISE(ABORT,'immutable CodexQuest release'); END;
CREATE TRIGGER cq_container_update BEFORE UPDATE ON codexquest_container BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER cq_container_delete BEFORE DELETE ON codexquest_container BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER cq_body_update BEFORE UPDATE ON codexquest_body_chunk BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER cq_body_delete BEFORE DELETE ON codexquest_body_chunk BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER cq_child_update BEFORE UPDATE ON codexquest_child BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER cq_child_delete BEFORE DELETE ON codexquest_child BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER cq_relation_update BEFORE UPDATE ON codexquest_relation BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER cq_relation_delete BEFORE DELETE ON codexquest_relation BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER cq_candidate_update BEFORE UPDATE ON codexquest_candidate BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER cq_candidate_delete BEFORE DELETE ON codexquest_candidate BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER cq_slot_update BEFORE UPDATE ON codexquest_text_slot BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER cq_slot_delete BEFORE DELETE ON codexquest_text_slot BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER cq_text_occurrence_update BEFORE UPDATE ON codexquest_text_occurrence BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER cq_text_occurrence_delete BEFORE DELETE ON codexquest_text_occurrence BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER cq_text_update BEFORE UPDATE ON codexquest_text_candidate BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER cq_text_delete BEFORE DELETE ON codexquest_text_candidate BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER cq_release_update BEFORE UPDATE ON codexquest_release BEGIN SELECT RAISE(ABORT,'write once'); END;
CREATE TRIGGER cq_release_delete BEFORE DELETE ON codexquest_release BEGIN SELECT RAISE(ABORT,'write once'); END;
