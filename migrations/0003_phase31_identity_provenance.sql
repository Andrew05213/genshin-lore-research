-- Phase 3.1A evidence isolation foundation.
--
-- This migration is schema-only.  It does not migrate legacy logical Talk
-- rows, create logical passages, scan source files, or rebuild derived indexes.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS parser_build (
    id TEXT PRIMARY KEY,
    parser_version TEXT NOT NULL,
    mapping_version TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    config_checksum TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_run (
    id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id),
    parser_build_id TEXT NOT NULL REFERENCES parser_build(id),
    scope_json TEXT NOT NULL,
    config_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('staged', 'running', 'complete', 'failed')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_container (
    id TEXT PRIMARY KEY,
    ingest_run_id TEXT NOT NULL REFERENCES ingest_run(id),
    source_record_id TEXT NOT NULL REFERENCES source_record(id),
    kind TEXT NOT NULL CHECK (kind = 'talk_root'),
    namespace TEXT NOT NULL,
    talk_id TEXT NOT NULL,
    raw_checksum TEXT NOT NULL,
    normalized_checksum TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(ingest_run_id, source_record_id),
    UNIQUE(id, ingest_run_id)
);

CREATE TABLE IF NOT EXISTS identity_group (
    id TEXT PRIMARY KEY,
    ingest_run_id TEXT NOT NULL REFERENCES ingest_run(id),
    namespace TEXT NOT NULL,
    talk_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(ingest_run_id, namespace, talk_id)
);

CREATE TABLE IF NOT EXISTS identity_group_container (
    identity_group_id TEXT NOT NULL REFERENCES identity_group(id),
    evidence_container_id TEXT NOT NULL REFERENCES evidence_container(id),
    PRIMARY KEY(identity_group_id, evidence_container_id)
);

CREATE TABLE IF NOT EXISTS passage_occurrence (
    passage_occurrence_id TEXT PRIMARY KEY,
    ingest_run_id TEXT NOT NULL REFERENCES ingest_run(id),
    evidence_container_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL REFERENCES source_record(id),
    source_pointer TEXT NOT NULL,
    namespace TEXT NOT NULL,
    talk_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    sequence_key TEXT NOT NULL,
    raw_checksum TEXT NOT NULL,
    normalized_checksum TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(passage_occurrence_id, ingest_run_id, evidence_container_id),
    FOREIGN KEY (evidence_container_id, ingest_run_id)
        REFERENCES evidence_container(id, ingest_run_id)
);

CREATE TABLE IF NOT EXISTS passage_occurrence_text (
    id TEXT PRIMARY KEY,
    passage_occurrence_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    evidence_container_id TEXT NOT NULL,
    language TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    raw_checksum TEXT NOT NULL,
    normalized_checksum TEXT NOT NULL,
    source_record_id TEXT NOT NULL REFERENCES source_record(id),
    source_start INTEGER,
    source_end INTEGER,
    UNIQUE(passage_occurrence_id, ingest_run_id, evidence_container_id, language),
    FOREIGN KEY (passage_occurrence_id, ingest_run_id, evidence_container_id)
        REFERENCES passage_occurrence(
            passage_occurrence_id, ingest_run_id, evidence_container_id
        )
);

CREATE TABLE IF NOT EXISTS occurrence_edge (
    id TEXT PRIMARY KEY,
    ingest_run_id TEXT NOT NULL REFERENCES ingest_run(id),
    evidence_container_id TEXT NOT NULL,
    from_passage_occurrence_id TEXT NOT NULL,
    to_passage_occurrence_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (evidence_container_id, ingest_run_id)
        REFERENCES evidence_container(id, ingest_run_id),
    FOREIGN KEY (
        from_passage_occurrence_id, ingest_run_id, evidence_container_id
    ) REFERENCES passage_occurrence(
        passage_occurrence_id, ingest_run_id, evidence_container_id
    ),
    FOREIGN KEY (
        to_passage_occurrence_id, ingest_run_id, evidence_container_id
    ) REFERENCES passage_occurrence(
        passage_occurrence_id, ingest_run_id, evidence_container_id
    ),
    UNIQUE(
        ingest_run_id, evidence_container_id,
        from_passage_occurrence_id, to_passage_occurrence_id, edge_type
    )
);

CREATE TABLE IF NOT EXISTS document_evidence (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES document(stable_id),
    evidence_container_id TEXT REFERENCES evidence_container(id),
    identity_group_id TEXT REFERENCES identity_group(id),
    CHECK (
        (evidence_container_id IS NOT NULL AND identity_group_id IS NULL)
        OR (evidence_container_id IS NULL AND identity_group_id IS NOT NULL)
    ),
    UNIQUE(document_id, evidence_container_id),
    UNIQUE(document_id, identity_group_id)
);

CREATE INDEX IF NOT EXISTS idx_ingest_run_snapshot
    ON ingest_run(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_evidence_container_native
    ON evidence_container(ingest_run_id, namespace, talk_id);
CREATE INDEX IF NOT EXISTS idx_identity_group_native
    ON identity_group(ingest_run_id, namespace, talk_id);
CREATE INDEX IF NOT EXISTS idx_identity_group_container_container
    ON identity_group_container(evidence_container_id);
CREATE INDEX IF NOT EXISTS idx_passage_occurrence_scope_native
    ON passage_occurrence(ingest_run_id, evidence_container_id, namespace, talk_id, node_id);
CREATE INDEX IF NOT EXISTS idx_passage_occurrence_text_language
    ON passage_occurrence_text(ingest_run_id, evidence_container_id, language);
CREATE INDEX IF NOT EXISTS idx_occurrence_edge_from
    ON occurrence_edge(ingest_run_id, evidence_container_id, from_passage_occurrence_id);
CREATE INDEX IF NOT EXISTS idx_occurrence_edge_to
    ON occurrence_edge(ingest_run_id, evidence_container_id, to_passage_occurrence_id);
CREATE INDEX IF NOT EXISTS idx_document_evidence_container
    ON document_evidence(evidence_container_id);
CREATE INDEX IF NOT EXISTS idx_document_evidence_group
    ON document_evidence(identity_group_id);
