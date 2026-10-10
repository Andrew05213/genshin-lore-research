-- Standalone Schema 7 extension only. Never applied to the product ledger.
-- Legacy evidence_container mandates talk_root + nonnull talk_id and cannot
-- represent a Quest-embedded list with no native Talk ID without inventing one.
CREATE TABLE quest_talk_physical_container (
 id TEXT PRIMARY KEY, ingest_run_id TEXT NOT NULL REFERENCES ingest_run(id),
 source_record_id TEXT NOT NULL REFERENCES source_record(id),
 kind TEXT NOT NULL CHECK(kind IN ('quest_root','talk_root','embedded_dialogue','reference_wrapper','mapping_unresolved')),
 shape TEXT, native_id TEXT, metadata_json TEXT NOT NULL,
 UNIQUE(ingest_run_id,source_record_id), UNIQUE(id,ingest_run_id)
);
CREATE TABLE quest_talk_physical_node (
 id TEXT PRIMARY KEY, container_id TEXT NOT NULL, ingest_run_id TEXT NOT NULL,
 ordinal INTEGER NOT NULL, pointer TEXT NOT NULL, native_id TEXT NOT NULL,
 raw_json TEXT NOT NULL, successors_json TEXT NOT NULL, text_hash TEXT,
 UNIQUE(container_id,ordinal), UNIQUE(id,container_id,ingest_run_id),
 FOREIGN KEY(container_id,ingest_run_id) REFERENCES quest_talk_physical_container(id,ingest_run_id)
);
CREATE TABLE quest_talk_physical_text (
 id TEXT PRIMARY KEY, node_id TEXT NOT NULL, container_id TEXT NOT NULL, ingest_run_id TEXT NOT NULL,
 locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')),
 status TEXT NOT NULL CHECK(status IN ('resolved_main','resolved_medium','missing','ambiguous','mapping_unresolved')),
 raw_text TEXT, locator_json TEXT NOT NULL,
 UNIQUE(node_id,locale),
 FOREIGN KEY(node_id,container_id,ingest_run_id) REFERENCES quest_talk_physical_node(id,container_id,ingest_run_id)
);
CREATE TABLE quest_talk_physical_reference (
 id TEXT PRIMARY KEY, container_id TEXT NOT NULL, ingest_run_id TEXT NOT NULL,
 pointer TEXT NOT NULL, native_id TEXT NOT NULL, raw_json TEXT NOT NULL,
 candidates_json TEXT NOT NULL, search_complete INTEGER NOT NULL CHECK(search_complete IN (0,1)),
 UNIQUE(container_id,pointer),
 FOREIGN KEY(container_id,ingest_run_id) REFERENCES quest_talk_physical_container(id,ingest_run_id)
);
CREATE INDEX quest_talk_physical_node_container ON quest_talk_physical_node(container_id,ordinal);
CREATE INDEX quest_talk_physical_text_container ON quest_talk_physical_text(container_id,node_id,locale);
CREATE INDEX quest_talk_physical_reference_container ON quest_talk_physical_reference(container_id,pointer);
