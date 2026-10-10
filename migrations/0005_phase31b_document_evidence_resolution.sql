-- Phase 3.1B Quest evidence-only linkage.
--
-- The Phase 3.1A table allowed concrete and identity-group targets but had no
-- durable outcome for unresolved references.  Extend it without changing the
-- original migration so every Quest reference remains explicit.

PRAGMA foreign_keys = OFF;

ALTER TABLE document_evidence RENAME TO document_evidence_phase31a;

CREATE TABLE document_evidence (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES document(stable_id),
    ingest_run_id TEXT NOT NULL REFERENCES ingest_run(id),
    namespace TEXT NOT NULL,
    talk_id TEXT NOT NULL,
    result TEXT NOT NULL CHECK (result IN ('evidence_only', 'ambiguous', 'unresolved')),
    evidence_container_id TEXT REFERENCES evidence_container(id),
    identity_group_id TEXT REFERENCES identity_group(id),
    source_record_id TEXT REFERENCES source_record(id),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    CHECK (
        (result = 'evidence_only'
            AND evidence_container_id IS NOT NULL
            AND identity_group_id IS NULL)
        OR (result = 'ambiguous'
            AND evidence_container_id IS NULL
            AND identity_group_id IS NOT NULL)
        OR (result = 'unresolved'
            AND evidence_container_id IS NULL
            AND identity_group_id IS NULL)
    ),
    UNIQUE(document_id, ingest_run_id, namespace, talk_id, result)
);

INSERT INTO document_evidence(
    id, document_id, ingest_run_id, namespace, talk_id, result,
    evidence_container_id, identity_group_id, source_record_id, metadata_json)
SELECT old.id,
       old.document_id,
       COALESCE(ec.ingest_run_id, ig.ingest_run_id),
       COALESCE(ec.namespace, ig.namespace),
       COALESCE(ec.talk_id, ig.talk_id),
       CASE
           WHEN old.evidence_container_id IS NOT NULL THEN 'evidence_only'
           ELSE 'ambiguous'
       END,
       old.evidence_container_id,
       old.identity_group_id,
       NULL,
       '{}'
  FROM document_evidence_phase31a old
  LEFT JOIN evidence_container ec
    ON ec.id = old.evidence_container_id
  LEFT JOIN identity_group ig
    ON ig.id = old.identity_group_id;

DROP TABLE document_evidence_phase31a;

CREATE INDEX idx_document_evidence_run_native
    ON document_evidence(ingest_run_id, namespace, talk_id, result);
CREATE INDEX idx_document_evidence_container
    ON document_evidence(evidence_container_id);
CREATE INDEX idx_document_evidence_group
    ON document_evidence(identity_group_id);

PRAGMA foreign_keys = ON;
