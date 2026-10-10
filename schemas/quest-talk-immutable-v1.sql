-- Immutable successor: WITHOUT ROWID removes implicit rowid REPLACE conflicts.
-- Standalone Schema 7 extension only. Never applied to the product ledger.
-- Legacy evidence_container mandates talk_root + nonnull talk_id and cannot
-- represent a Quest-embedded list with no native Talk ID without inventing one.
CREATE TABLE quest_talk_physical_container (
 id TEXT PRIMARY KEY, ingest_run_id TEXT NOT NULL REFERENCES ingest_run(id),
 source_record_id TEXT NOT NULL REFERENCES source_record(id),
 kind TEXT NOT NULL CHECK(kind IN ('quest_root','talk_root','embedded_dialogue','reference_wrapper','mapping_unresolved')),
 shape TEXT, native_id TEXT, metadata_json TEXT NOT NULL,
 UNIQUE(ingest_run_id,source_record_id), UNIQUE(id,ingest_run_id)
) WITHOUT ROWID;
CREATE TABLE quest_talk_physical_node (
 id TEXT PRIMARY KEY, container_id TEXT NOT NULL, ingest_run_id TEXT NOT NULL,
 ordinal INTEGER NOT NULL, pointer TEXT NOT NULL, native_id TEXT NOT NULL,
 raw_json TEXT NOT NULL, successors_json TEXT NOT NULL, text_hash TEXT,
 UNIQUE(container_id,ordinal), UNIQUE(id,container_id,ingest_run_id),
 FOREIGN KEY(container_id,ingest_run_id) REFERENCES quest_talk_physical_container(id,ingest_run_id)
) WITHOUT ROWID;
CREATE TABLE quest_talk_physical_text (
 id TEXT PRIMARY KEY, node_id TEXT NOT NULL, container_id TEXT NOT NULL, ingest_run_id TEXT NOT NULL,
 locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')),
 status TEXT NOT NULL CHECK(status IN ('resolved_main','resolved_medium','missing','ambiguous','mapping_unresolved_terminal')),
 raw_text TEXT, locator_json TEXT NOT NULL,
 UNIQUE(node_id,locale),
 FOREIGN KEY(node_id,container_id,ingest_run_id) REFERENCES quest_talk_physical_node(id,container_id,ingest_run_id)
) WITHOUT ROWID;
CREATE TABLE quest_talk_physical_reference (
 id TEXT PRIMARY KEY, container_id TEXT NOT NULL, ingest_run_id TEXT NOT NULL,
 pointer TEXT NOT NULL, native_id TEXT NOT NULL, raw_json TEXT NOT NULL,
 candidates_json TEXT NOT NULL, search_complete INTEGER NOT NULL CHECK(search_complete IN (0,1)),
 UNIQUE(container_id,pointer),
 FOREIGN KEY(container_id,ingest_run_id) REFERENCES quest_talk_physical_container(id,ingest_run_id)
) WITHOUT ROWID;
CREATE INDEX quest_talk_physical_node_container ON quest_talk_physical_node(container_id,ordinal);
CREATE INDEX quest_talk_physical_text_container ON quest_talk_physical_text(container_id,node_id,locale);
CREATE INDEX quest_talk_physical_reference_container ON quest_talk_physical_reference(container_id,pointer);

-- Candidate retrieval only; final comparison remains case-sensitive instr.
CREATE INDEX quest_talk_physical_search_cover ON quest_talk_physical_text(id,raw_text);
CREATE INDEX quest_talk_physical_search_locale ON quest_talk_physical_text(locale,id,raw_text);
CREATE INDEX quest_talk_physical_search_native ON quest_talk_physical_container(native_id,id);
CREATE INDEX quest_talk_physical_search_path ON quest_talk_physical_container(json_extract(metadata_json,'$.source_path'),id);

CREATE TRIGGER quest_talk_physical_container_insert_existing_immutable BEFORE INSERT ON quest_talk_physical_container
WHEN EXISTS (SELECT 1 FROM quest_talk_physical_container WHERE id IS NEW.id OR (ingest_run_id=NEW.ingest_run_id AND source_record_id=NEW.source_record_id))
BEGIN SELECT RAISE(ABORT,'physical evidence conflict is immutable'); END;
CREATE TRIGGER quest_talk_physical_container_update_immutable BEFORE UPDATE ON quest_talk_physical_container
BEGIN SELECT RAISE(ABORT,'physical evidence is immutable'); END;
CREATE TRIGGER quest_talk_physical_container_delete_immutable BEFORE DELETE ON quest_talk_physical_container
BEGIN SELECT RAISE(ABORT,'physical evidence is immutable'); END;

CREATE TRIGGER quest_talk_physical_node_insert_existing_immutable BEFORE INSERT ON quest_talk_physical_node
WHEN EXISTS (SELECT 1 FROM quest_talk_physical_node WHERE id IS NEW.id OR (container_id=NEW.container_id AND ordinal=NEW.ordinal))
BEGIN SELECT RAISE(ABORT,'physical evidence conflict is immutable'); END;
CREATE TRIGGER quest_talk_physical_node_update_immutable BEFORE UPDATE ON quest_talk_physical_node
BEGIN SELECT RAISE(ABORT,'physical evidence is immutable'); END;
CREATE TRIGGER quest_talk_physical_node_delete_immutable BEFORE DELETE ON quest_talk_physical_node
BEGIN SELECT RAISE(ABORT,'physical evidence is immutable'); END;

CREATE TRIGGER quest_talk_physical_text_insert_existing_immutable BEFORE INSERT ON quest_talk_physical_text
WHEN EXISTS (SELECT 1 FROM quest_talk_physical_text WHERE id IS NEW.id OR (node_id=NEW.node_id AND locale=NEW.locale))
BEGIN SELECT RAISE(ABORT,'physical evidence conflict is immutable'); END;
CREATE TRIGGER quest_talk_physical_text_update_immutable BEFORE UPDATE ON quest_talk_physical_text
BEGIN SELECT RAISE(ABORT,'physical evidence is immutable'); END;
CREATE TRIGGER quest_talk_physical_text_delete_immutable BEFORE DELETE ON quest_talk_physical_text
BEGIN SELECT RAISE(ABORT,'physical evidence is immutable'); END;

CREATE TRIGGER quest_talk_physical_reference_insert_existing_immutable BEFORE INSERT ON quest_talk_physical_reference
WHEN EXISTS (SELECT 1 FROM quest_talk_physical_reference WHERE id IS NEW.id OR (container_id=NEW.container_id AND pointer=NEW.pointer))
BEGIN SELECT RAISE(ABORT,'physical evidence conflict is immutable'); END;
CREATE TRIGGER quest_talk_physical_reference_update_immutable BEFORE UPDATE ON quest_talk_physical_reference
BEGIN SELECT RAISE(ABORT,'physical evidence is immutable'); END;
CREATE TRIGGER quest_talk_physical_reference_delete_immutable BEFORE DELETE ON quest_talk_physical_reference
BEGIN SELECT RAISE(ABORT,'physical evidence is immutable'); END;
