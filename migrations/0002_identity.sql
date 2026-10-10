-- Phase 3 identity correction.
--
-- A logical Talk family (native namespace + native id) may have more than one
-- raw occurrence in a snapshot.  The occurrence and its provenance therefore
-- live in talk_evidence; the many-to-many relation is explicit instead of
-- being hidden in document_version.

CREATE TABLE IF NOT EXISTS logical_talk (
    id TEXT PRIMARY KEY,
    native_namespace TEXT NOT NULL,
    native_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('unique', 'ambiguous', 'multiple_evidence', 'unresolved')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(native_namespace, native_id)
);

CREATE TABLE IF NOT EXISTS talk_evidence (
    id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id),
    source_file_id TEXT NOT NULL REFERENCES source_file(id),
    source_record_id TEXT NOT NULL UNIQUE REFERENCES source_record(id),
    source_pointer TEXT NOT NULL,
    native_namespace TEXT NOT NULL,
    native_id TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL,
    normalized_checksum TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'ambiguous', 'unresolved', 'removed')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(snapshot_id, source_file_id, source_pointer)
);

CREATE TABLE IF NOT EXISTS logical_talk_evidence (
    logical_talk_id TEXT NOT NULL REFERENCES logical_talk(id),
    evidence_id TEXT NOT NULL REFERENCES talk_evidence(id),
    relation_status TEXT NOT NULL CHECK (relation_status IN ('resolved', 'ambiguous', 'unresolved')),
    source_class TEXT NOT NULL CHECK (source_class IN ('original', 'community', 'inferred')),
    source_record_id TEXT REFERENCES source_record(id),
    method TEXT,
    confidence REAL,
    PRIMARY KEY(logical_talk_id, evidence_id)
);

CREATE INDEX IF NOT EXISTS idx_logical_talk_native
    ON logical_talk(native_namespace, native_id);
CREATE INDEX IF NOT EXISTS idx_talk_evidence_snapshot
    ON talk_evidence(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_talk_evidence_native
    ON talk_evidence(snapshot_id, native_namespace, native_id);
CREATE INDEX IF NOT EXISTS idx_logical_talk_evidence_evidence
    ON logical_talk_evidence(evidence_id);
