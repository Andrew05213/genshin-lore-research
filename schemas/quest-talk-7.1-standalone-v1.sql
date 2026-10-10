-- Snapshot-local standalone Quest/Talk physical successor. Not product Schema20.
PRAGMA foreign_keys=ON;
CREATE TABLE release (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  contract TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  source_commit TEXT NOT NULL,
  source_tree TEXT NOT NULL,
  parser_build_id TEXT NOT NULL,
  ingest_run_id TEXT NOT NULL,
  scope_sha256 TEXT NOT NULL,
  implementation_sha256 TEXT NOT NULL,
  counts_json TEXT NOT NULL CHECK(json_valid(counts_json)),
  state_json TEXT NOT NULL CHECK(json_valid(state_json))
) WITHOUT ROWID;
CREATE TABLE source_file (
  path TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('quest','talk','main_quest')),
  git_blob_sha1 TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
  raw_bytes BLOB NOT NULL
) WITHOUT ROWID;
CREATE TABLE textmap_shard (
  path TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL,
  locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')),
  tier TEXT NOT NULL CHECK(tier IN ('main','medium')),
  git_blob_sha1 TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0)
) WITHOUT ROWID;
CREATE TABLE physical_container (
  id TEXT PRIMARY KEY,
  ingest_run_id TEXT NOT NULL,
  source_path TEXT NOT NULL REFERENCES source_file(path),
  pointer TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('quest_root','talk_root','embedded_dialogue','reference_wrapper','mapping_unresolved')),
  mapping TEXT NOT NULL,
  native_id TEXT,
  raw_sha256 TEXT NOT NULL,
  UNIQUE(source_path,pointer)
) WITHOUT ROWID;
CREATE TABLE quest_child (
  id TEXT PRIMARY KEY,
  container_id TEXT NOT NULL REFERENCES physical_container(id),
  ingest_run_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
  pointer TEXT NOT NULL,
  raw_json TEXT NOT NULL CHECK(json_valid(raw_json)),
  raw_sha256 TEXT NOT NULL,
  UNIQUE(container_id,ordinal),
  UNIQUE(container_id,pointer)
) WITHOUT ROWID;
CREATE TABLE talk_node (
  id TEXT PRIMARY KEY,
  container_id TEXT NOT NULL REFERENCES physical_container(id),
  ingest_run_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
  pointer TEXT NOT NULL,
  native_id TEXT NOT NULL,
  text_hash TEXT,
  text_mapping TEXT NOT NULL CHECK(text_mapping IN ('literal_key','unresolved_codec')),
  raw_json TEXT NOT NULL CHECK(json_valid(raw_json)),
  successors_json TEXT NOT NULL CHECK(json_valid(successors_json)),
  UNIQUE(container_id,ordinal),
  UNIQUE(container_id,pointer)
) WITHOUT ROWID;
CREATE TABLE text_slot (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL REFERENCES talk_node(id),
  ingest_run_id TEXT NOT NULL,
  locale TEXT NOT NULL CHECK(locale IN ('CHS','EN','JP')),
  status TEXT NOT NULL CHECK(status IN ('resolved_main','resolved_medium','missing','ambiguous','mapping_unresolved_terminal')),
  raw_text TEXT,
  locators_json TEXT NOT NULL CHECK(json_valid(locators_json)),
  UNIQUE(node_id,locale)
) WITHOUT ROWID;
CREATE TABLE physical_reference (
  id TEXT PRIMARY KEY,
  container_id TEXT NOT NULL REFERENCES physical_container(id),
  ingest_run_id TEXT NOT NULL,
  pointer TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('quest_to_talk','wrapper_to_talk')),
  native_id TEXT NOT NULL,
  raw_json TEXT NOT NULL CHECK(json_valid(raw_json)),
  status TEXT NOT NULL CHECK(status IN ('resolved_unique','missing','ambiguous','terminal_unresolved')),
  candidates_json TEXT NOT NULL CHECK(json_valid(candidates_json)),
  UNIQUE(container_id,pointer)
) WITHOUT ROWID;
CREATE TABLE main_quest_row (
  id TEXT PRIMARY KEY,
  ingest_run_id TEXT NOT NULL,
  source_path TEXT NOT NULL REFERENCES source_file(path),
  ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
  pointer TEXT NOT NULL,
  native_id TEXT NOT NULL,
  raw_json TEXT NOT NULL CHECK(json_valid(raw_json)),
  UNIQUE(source_path,ordinal)
) WITHOUT ROWID;
CREATE INDEX talk_node_context ON talk_node(container_id,ordinal);
CREATE INDEX physical_reference_native ON physical_reference(native_id,kind);
CREATE INDEX text_slot_locale_status ON text_slot(locale,status);
