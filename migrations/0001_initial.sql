PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migration (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_catalog (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('primary', 'community')),
    url TEXT NOT NULL,
    license_note TEXT
);

CREATE TABLE IF NOT EXISTS source_snapshot (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES source_catalog(id),
    release_label TEXT NOT NULL,
    build TEXT NOT NULL,
    "commit" TEXT NOT NULL,
    tree TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    mapping_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('staged', 'active', 'quarantined', 'superseded')),
    metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_file (
    id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id),
    relative_path TEXT NOT NULL,
    upstream_blob_oid TEXT,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    format TEXT NOT NULL,
    root_path TEXT NOT NULL,
    UNIQUE(snapshot_id, relative_path)
);

CREATE TABLE IF NOT EXISTS source_record (
    id TEXT PRIMARY KEY,
    source_file_id TEXT NOT NULL REFERENCES source_file(id),
    pointer TEXT NOT NULL,
    record_kind TEXT NOT NULL,
    native_id TEXT,
    raw_sha256 TEXT NOT NULL,
    raw_json TEXT,
    UNIQUE(source_file_id, pointer)
);

CREATE TABLE IF NOT EXISTS document (
    stable_id TEXT PRIMARY KEY,
    content_type TEXT NOT NULL,
    native_namespace TEXT NOT NULL,
    native_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'placeholder', 'unresolved', 'removed')),
    UNIQUE(native_namespace, native_id, content_type)
);

CREATE TABLE IF NOT EXISTS document_version (
    document_id TEXT NOT NULL REFERENCES document(stable_id),
    snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id),
    source_record_id TEXT NOT NULL REFERENCES source_record(id),
    raw_checksum TEXT NOT NULL,
    normalized_checksum TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY(document_id, snapshot_id)
);

CREATE TABLE IF NOT EXISTS passage (
    stable_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES document(stable_id),
    passage_type TEXT NOT NULL,
    native_namespace TEXT NOT NULL,
    native_id TEXT NOT NULL,
    identity_algorithm TEXT NOT NULL,
    UNIQUE(native_namespace, native_id, passage_type)
);

CREATE TABLE IF NOT EXISTS passage_version (
    passage_id TEXT NOT NULL REFERENCES passage(stable_id),
    snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id),
    source_record_id TEXT NOT NULL REFERENCES source_record(id),
    sequence_key TEXT NOT NULL,
    speaker_json TEXT,
    start_ms INTEGER,
    end_ms INTEGER,
    raw_checksum TEXT NOT NULL,
    normalized_checksum TEXT NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY(passage_id, snapshot_id)
);

CREATE TABLE IF NOT EXISTS localized_text (
    id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id),
    language TEXT NOT NULL,
    shard TEXT NOT NULL,
    textmap_hash TEXT NOT NULL,
    source_file_id TEXT NOT NULL REFERENCES source_file(id),
    source_record_id TEXT NOT NULL REFERENCES source_record(id),
    raw_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    raw_checksum TEXT NOT NULL,
    normalized_checksum TEXT NOT NULL,
    UNIQUE(snapshot_id, language, shard, textmap_hash)
);

CREATE TABLE IF NOT EXISTS passage_text (
    id TEXT PRIMARY KEY,
    passage_id TEXT NOT NULL REFERENCES passage(stable_id),
    snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id),
    language TEXT NOT NULL,
    localized_text_id TEXT REFERENCES localized_text(id),
    source_record_id TEXT NOT NULL REFERENCES source_record(id),
    raw_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    raw_checksum TEXT NOT NULL,
    normalized_checksum TEXT NOT NULL,
    source_start INTEGER,
    source_end INTEGER,
    UNIQUE(passage_id, snapshot_id, language)
);

CREATE TABLE IF NOT EXISTS document_relation (
    id TEXT PRIMARY KEY,
    from_document_id TEXT NOT NULL REFERENCES document(stable_id),
    to_document_id TEXT NOT NULL REFERENCES document(stable_id),
    relation_type TEXT NOT NULL,
    source_class TEXT NOT NULL CHECK (source_class IN ('original', 'community', 'inferred')),
    source_record_id TEXT REFERENCES source_record(id),
    method TEXT,
    confidence REAL,
    UNIQUE(from_document_id, to_document_id, relation_type, source_class, method)
);

CREATE TABLE IF NOT EXISTS passage_edge (
    id TEXT PRIMARY KEY,
    from_passage_id TEXT NOT NULL REFERENCES passage(stable_id),
    to_passage_id TEXT NOT NULL REFERENCES passage(stable_id),
    edge_type TEXT NOT NULL,
    source_class TEXT NOT NULL CHECK (source_class IN ('original', 'community', 'inferred')),
    source_record_id TEXT REFERENCES source_record(id),
    method TEXT,
    confidence REAL,
    UNIQUE(from_passage_id, to_passage_id, edge_type, source_class, method)
);

CREATE TABLE IF NOT EXISTS metadata_fact (
    id TEXT PRIMARY KEY,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    key TEXT NOT NULL,
    language TEXT,
    value_json TEXT NOT NULL,
    source_class TEXT NOT NULL CHECK (source_class IN ('original', 'community', 'inferred')),
    source_record_id TEXT REFERENCES source_record(id),
    method TEXT,
    confidence REAL,
    model TEXT
);

CREATE TABLE IF NOT EXISTS textmap_conflict (
    id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id),
    language TEXT NOT NULL,
    textmap_hash TEXT NOT NULL,
    shard_a TEXT NOT NULL,
    shard_b TEXT NOT NULL,
    value_a TEXT NOT NULL,
    value_b TEXT NOT NULL,
    source_record_a TEXT REFERENCES source_record(id),
    source_record_b TEXT REFERENCES source_record(id)
);

CREATE TABLE IF NOT EXISTS ingest_issue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id),
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    source_file_id TEXT REFERENCES source_file(id),
    source_record_id TEXT REFERENCES source_record(id),
    details_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_source_file_snapshot ON source_file(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_source_record_native ON source_record(native_id);
CREATE INDEX IF NOT EXISTS idx_document_version_snapshot ON document_version(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_passage_document ON passage(document_id);
CREATE INDEX IF NOT EXISTS idx_passage_version_snapshot ON passage_version(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_passage_text_language ON passage_text(language);
CREATE INDEX IF NOT EXISTS idx_localized_text_hash ON localized_text(snapshot_id, language, textmap_hash);
CREATE INDEX IF NOT EXISTS idx_document_relation_parent ON document_relation(to_document_id);
CREATE INDEX IF NOT EXISTS idx_passage_edge_from ON passage_edge(from_passage_id);
CREATE INDEX IF NOT EXISTS idx_passage_edge_to ON passage_edge(to_passage_id);
CREATE INDEX IF NOT EXISTS idx_metadata_subject ON metadata_fact(subject_type, subject_id);
CREATE INDEX IF NOT EXISTS idx_issue_snapshot ON ingest_issue(snapshot_id);
