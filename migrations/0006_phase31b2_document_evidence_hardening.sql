-- Phase 3.1B2 Quest evidence resolution hardening.
--
-- This is a fresh-rebuild contract.  A populated 0005 document_evidence table
-- has neither raw Quest-reference identity nor an auditable candidate scope,
-- so it must fail before any table is rewritten.

CREATE TEMP TABLE phase31b2_migration_guard (id INTEGER PRIMARY KEY);
CREATE TEMP TRIGGER phase31b2_reject_unauditable_legacy_rows
BEFORE INSERT ON phase31b2_migration_guard
WHEN EXISTS (SELECT 1 FROM document_evidence)
  OR EXISTS (
      SELECT 1
      FROM identity_group_container igc
      JOIN identity_group ig ON ig.id=igc.identity_group_id
      JOIN evidence_container ec ON ec.id=igc.evidence_container_id
      WHERE ig.ingest_run_id <> ec.ingest_run_id
  )
BEGIN
    SELECT RAISE(ABORT,
        'Phase 3.1B2 requires a fresh rebuild: legacy document evidence or cross-run group membership is unauditable');
END;
INSERT INTO phase31b2_migration_guard(id) VALUES (1);
DROP TRIGGER phase31b2_reject_unauditable_legacy_rows;
DROP TABLE phase31b2_migration_guard;

PRAGMA foreign_keys = OFF;

-- SQLite parent keys for the composite foreign keys introduced below.
CREATE UNIQUE INDEX uq_identity_group_id_ingest_run
    ON identity_group(id, ingest_run_id);

ALTER TABLE identity_group_container RENAME TO identity_group_container_phase31b;

CREATE TABLE identity_group_container (
    identity_group_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    evidence_container_id TEXT NOT NULL,
    PRIMARY KEY(identity_group_id, ingest_run_id, evidence_container_id),
    FOREIGN KEY (identity_group_id, ingest_run_id)
        REFERENCES identity_group(id, ingest_run_id),
    FOREIGN KEY (evidence_container_id, ingest_run_id)
        REFERENCES evidence_container(id, ingest_run_id)
);

INSERT INTO identity_group_container(
    identity_group_id, ingest_run_id, evidence_container_id)
SELECT igc.identity_group_id, ig.ingest_run_id, igc.evidence_container_id
  FROM identity_group_container_phase31b igc
  JOIN identity_group ig ON ig.id=igc.identity_group_id
  JOIN evidence_container ec ON ec.id=igc.evidence_container_id
                         AND ec.ingest_run_id=ig.ingest_run_id;

DROP TABLE identity_group_container_phase31b;

CREATE TABLE candidate_scope_proof (
    id TEXT PRIMARY KEY,
    ingest_run_id TEXT NOT NULL REFERENCES ingest_run(id),
    manifest_id TEXT NOT NULL,
    manifest_checksum TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    snapshot_commit TEXT NOT NULL,
    snapshot_tree TEXT NOT NULL,
    audit_algorithm_version TEXT NOT NULL,
    audit_cache_digest TEXT NOT NULL,
    namespace TEXT NOT NULL,
    talk_id TEXT NOT NULL,
    inventory_complete INTEGER NOT NULL CHECK (inventory_complete IN (0, 1)),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(id, ingest_run_id),
    UNIQUE(
        ingest_run_id, manifest_id, manifest_checksum, namespace, talk_id
    )
);

ALTER TABLE document_evidence RENAME TO document_evidence_phase31b;

CREATE TABLE document_evidence (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES document(stable_id),
    ingest_run_id TEXT NOT NULL REFERENCES ingest_run(id),
    source_record_id TEXT NOT NULL REFERENCES source_record(id),
    namespace TEXT NOT NULL,
    talk_id TEXT NOT NULL,
    result TEXT NOT NULL CHECK (
        result IN (
            'evidence_only', 'ambiguous', 'unresolved',
            'incomplete_scope', 'quarantined'
        )
    ),
    candidate_scope_proof_id TEXT NOT NULL,
    candidate_manifest_id TEXT NOT NULL,
    candidate_manifest_checksum TEXT NOT NULL,
    evidence_container_id TEXT,
    identity_group_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    CHECK (
        (result = 'evidence_only'
            AND evidence_container_id IS NOT NULL
            AND identity_group_id IS NULL)
        OR (result = 'ambiguous'
            AND evidence_container_id IS NULL
            AND identity_group_id IS NOT NULL)
        OR (result IN ('unresolved', 'incomplete_scope', 'quarantined')
            AND evidence_container_id IS NULL
            AND identity_group_id IS NULL)
    ),
    UNIQUE(source_record_id, ingest_run_id),
    FOREIGN KEY (candidate_scope_proof_id, ingest_run_id)
        REFERENCES candidate_scope_proof(id, ingest_run_id),
    FOREIGN KEY (evidence_container_id, ingest_run_id)
        REFERENCES evidence_container(id, ingest_run_id),
    FOREIGN KEY (identity_group_id, ingest_run_id)
        REFERENCES identity_group(id, ingest_run_id)
);

-- The guard above establishes that this table is empty.  Do not manufacture
-- raw reference identities or candidate-scope proofs for pre-release 0005 rows.
DROP TABLE document_evidence_phase31b;

CREATE TRIGGER document_evidence_reference_scope_insert
BEFORE INSERT ON document_evidence
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
          FROM source_record sr
          JOIN source_file sf ON sf.id=sr.source_file_id
          JOIN ingest_run ir ON ir.id=NEW.ingest_run_id
         WHERE sr.id=NEW.source_record_id
           AND sr.record_kind='quest_talk_reference'
           AND sf.snapshot_id=ir.snapshot_id
    ) THEN RAISE(ABORT,
        'document_evidence requires a Quest reference source_record in the resolution snapshot') END;

    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
          FROM candidate_scope_proof cp
         WHERE cp.id=NEW.candidate_scope_proof_id
           AND cp.ingest_run_id=NEW.ingest_run_id
           AND cp.manifest_id=NEW.candidate_manifest_id
           AND cp.manifest_checksum=NEW.candidate_manifest_checksum
           AND cp.namespace=NEW.namespace
           AND cp.talk_id=NEW.talk_id
    ) THEN RAISE(ABORT,
        'document_evidence candidate scope proof does not match reference identity') END;

    SELECT CASE WHEN NEW.result='evidence_only' AND NOT EXISTS (
        SELECT 1
          FROM candidate_scope_proof cp
          JOIN ingest_run ir ON ir.id=NEW.ingest_run_id
          JOIN source_snapshot ss ON ss.id=ir.snapshot_id
         WHERE cp.id=NEW.candidate_scope_proof_id
           AND cp.ingest_run_id=NEW.ingest_run_id
           AND cp.inventory_complete=1
           AND cp.candidate_count=1
           AND cp.snapshot_id=ss.id
           AND cp.snapshot_commit=ss."commit"
           AND cp.snapshot_tree=ss.tree
    ) THEN RAISE(ABORT,
        'evidence_only requires a complete, matching single-candidate scope proof') END;

    SELECT CASE WHEN NEW.result='ambiguous' AND NOT EXISTS (
        SELECT 1
          FROM candidate_scope_proof cp
          JOIN ingest_run ir ON ir.id=NEW.ingest_run_id
          JOIN source_snapshot ss ON ss.id=ir.snapshot_id
         WHERE cp.id=NEW.candidate_scope_proof_id
           AND cp.ingest_run_id=NEW.ingest_run_id
           AND cp.inventory_complete=1
           AND cp.candidate_count >= 2
           AND cp.snapshot_id=ss.id
           AND cp.snapshot_commit=ss."commit"
           AND cp.snapshot_tree=ss.tree
    ) THEN RAISE(ABORT,
        'ambiguous requires a complete, matching multi-candidate scope proof') END;

    SELECT CASE WHEN NEW.result='unresolved' AND NOT EXISTS (
        SELECT 1
          FROM candidate_scope_proof cp
          JOIN ingest_run ir ON ir.id=NEW.ingest_run_id
          JOIN source_snapshot ss ON ss.id=ir.snapshot_id
         WHERE cp.id=NEW.candidate_scope_proof_id
           AND cp.ingest_run_id=NEW.ingest_run_id
           AND cp.inventory_complete=1
           AND cp.candidate_count=0
           AND cp.snapshot_id=ss.id
           AND cp.snapshot_commit=ss."commit"
           AND cp.snapshot_tree=ss.tree
    ) THEN RAISE(ABORT,
        'unresolved requires a complete, matching empty-candidate scope proof') END;

    SELECT CASE WHEN NEW.result='incomplete_scope' AND NOT EXISTS (
        SELECT 1
          FROM candidate_scope_proof cp
         WHERE cp.id=NEW.candidate_scope_proof_id
           AND cp.ingest_run_id=NEW.ingest_run_id
           AND cp.inventory_complete=0
    ) THEN RAISE(ABORT,
        'incomplete_scope requires a partial candidate scope proof') END;
END;

CREATE TRIGGER document_evidence_reference_scope_update
BEFORE UPDATE ON document_evidence
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
          FROM source_record sr
          JOIN source_file sf ON sf.id=sr.source_file_id
          JOIN ingest_run ir ON ir.id=NEW.ingest_run_id
         WHERE sr.id=NEW.source_record_id
           AND sr.record_kind='quest_talk_reference'
           AND sf.snapshot_id=ir.snapshot_id
    ) THEN RAISE(ABORT,
        'document_evidence requires a Quest reference source_record in the resolution snapshot') END;

    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
          FROM candidate_scope_proof cp
         WHERE cp.id=NEW.candidate_scope_proof_id
           AND cp.ingest_run_id=NEW.ingest_run_id
           AND cp.manifest_id=NEW.candidate_manifest_id
           AND cp.manifest_checksum=NEW.candidate_manifest_checksum
           AND cp.namespace=NEW.namespace
           AND cp.talk_id=NEW.talk_id
    ) THEN RAISE(ABORT,
        'document_evidence candidate scope proof does not match reference identity') END;

    SELECT CASE WHEN NEW.result='evidence_only' AND NOT EXISTS (
        SELECT 1
          FROM candidate_scope_proof cp
          JOIN ingest_run ir ON ir.id=NEW.ingest_run_id
          JOIN source_snapshot ss ON ss.id=ir.snapshot_id
         WHERE cp.id=NEW.candidate_scope_proof_id
           AND cp.ingest_run_id=NEW.ingest_run_id
           AND cp.inventory_complete=1
           AND cp.candidate_count=1
           AND cp.snapshot_id=ss.id
           AND cp.snapshot_commit=ss."commit"
           AND cp.snapshot_tree=ss.tree
    ) THEN RAISE(ABORT,
        'evidence_only requires a complete, matching single-candidate scope proof') END;

    SELECT CASE WHEN NEW.result='ambiguous' AND NOT EXISTS (
        SELECT 1
          FROM candidate_scope_proof cp
          JOIN ingest_run ir ON ir.id=NEW.ingest_run_id
          JOIN source_snapshot ss ON ss.id=ir.snapshot_id
         WHERE cp.id=NEW.candidate_scope_proof_id
           AND cp.ingest_run_id=NEW.ingest_run_id
           AND cp.inventory_complete=1
           AND cp.candidate_count >= 2
           AND cp.snapshot_id=ss.id
           AND cp.snapshot_commit=ss."commit"
           AND cp.snapshot_tree=ss.tree
    ) THEN RAISE(ABORT,
        'ambiguous requires a complete, matching multi-candidate scope proof') END;

    SELECT CASE WHEN NEW.result='unresolved' AND NOT EXISTS (
        SELECT 1
          FROM candidate_scope_proof cp
          JOIN ingest_run ir ON ir.id=NEW.ingest_run_id
          JOIN source_snapshot ss ON ss.id=ir.snapshot_id
         WHERE cp.id=NEW.candidate_scope_proof_id
           AND cp.ingest_run_id=NEW.ingest_run_id
           AND cp.inventory_complete=1
           AND cp.candidate_count=0
           AND cp.snapshot_id=ss.id
           AND cp.snapshot_commit=ss."commit"
           AND cp.snapshot_tree=ss.tree
    ) THEN RAISE(ABORT,
        'unresolved requires a complete, matching empty-candidate scope proof') END;

    SELECT CASE WHEN NEW.result='incomplete_scope' AND NOT EXISTS (
        SELECT 1
          FROM candidate_scope_proof cp
         WHERE cp.id=NEW.candidate_scope_proof_id
           AND cp.ingest_run_id=NEW.ingest_run_id
           AND cp.inventory_complete=0
    ) THEN RAISE(ABORT,
        'incomplete_scope requires a partial candidate scope proof') END;
END;

CREATE INDEX idx_identity_group_container_run
    ON identity_group_container(ingest_run_id, evidence_container_id);
CREATE INDEX idx_candidate_scope_proof_run_native
    ON candidate_scope_proof(ingest_run_id, namespace, talk_id);
CREATE INDEX idx_document_evidence_run_native
    ON document_evidence(ingest_run_id, namespace, talk_id);
CREATE INDEX idx_document_evidence_reference
    ON document_evidence(source_record_id, ingest_run_id);
CREATE INDEX idx_document_evidence_container
    ON document_evidence(evidence_container_id);
CREATE INDEX idx_document_evidence_group
    ON document_evidence(identity_group_id);

PRAGMA foreign_keys = ON;
