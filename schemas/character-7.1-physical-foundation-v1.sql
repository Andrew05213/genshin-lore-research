PRAGMA foreign_keys=ON;
CREATE TABLE foundation_source_file(
  path TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  git_blob TEXT NOT NULL,
  physical_delta TEXT NOT NULL CHECK(physical_delta IN('added','modified','unchanged'))
) WITHOUT ROWID;
CREATE TABLE foundation_primary_row(
  id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL,
  parser_build_id TEXT NOT NULL,
  ingest_run_id TEXT NOT NULL,
  source_path TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  pointer TEXT NOT NULL,
  native_field TEXT NOT NULL,
  native_value TEXT NOT NULL,
  raw_json TEXT NOT NULL,
  raw_sha256 TEXT NOT NULL,
  UNIQUE(source_path,ordinal),
  FOREIGN KEY(source_path) REFERENCES foundation_source_file(path)
) WITHOUT ROWID;
CREATE TABLE foundation_field(
  id TEXT PRIMARY KEY,
  row_id TEXT NOT NULL,
  pointer TEXT NOT NULL,
  source_field_ordinal INTEGER NOT NULL,
  child_ordinal INTEGER,
  field_name TEXT NOT NULL,
  role TEXT NOT NULL,
  raw_json TEXT NOT NULL,
  UNIQUE(row_id,pointer),
  FOREIGN KEY(row_id) REFERENCES foundation_primary_row(id)
) WITHOUT ROWID;
CREATE TABLE foundation_text_owner(
  id TEXT PRIMARY KEY,
  row_id TEXT NOT NULL,
  pointer TEXT NOT NULL,
  field_name TEXT NOT NULL,
  textmap_hash TEXT NOT NULL,
  UNIQUE(row_id,pointer),
  FOREIGN KEY(row_id) REFERENCES foundation_primary_row(id)
) WITHOUT ROWID;
CREATE TABLE foundation_voice_lut(
  id TEXT PRIMARY KEY,
  source_path TEXT NOT NULL,
  object_key TEXT NOT NULL,
  object_ordinal INTEGER NOT NULL,
  pointer TEXT NOT NULL,
  voice_slot TEXT NOT NULL,
  item_path TEXT NOT NULL,
  raw_json TEXT NOT NULL,
  raw_sha256 TEXT NOT NULL,
  UNIQUE(source_path,object_key),
  UNIQUE(source_path,object_ordinal),
  FOREIGN KEY(source_path) REFERENCES foundation_source_file(path),
  FOREIGN KEY(item_path) REFERENCES foundation_source_file(path)
) WITHOUT ROWID;
CREATE TABLE foundation_voice_item(
  id TEXT PRIMARY KEY,
  source_path TEXT NOT NULL,
  object_key TEXT NOT NULL,
  object_ordinal INTEGER NOT NULL,
  pointer TEXT NOT NULL,
  voice_slot TEXT NOT NULL,
  variants_status TEXT NOT NULL CHECK(variants_status IN('present','missing')),
  raw_json TEXT NOT NULL,
  raw_sha256 TEXT NOT NULL,
  UNIQUE(source_path,object_key),
  UNIQUE(source_path,object_ordinal),
  FOREIGN KEY(source_path) REFERENCES foundation_source_file(path)
) WITHOUT ROWID;
CREATE TABLE foundation_voice_link(
  id TEXT PRIMARY KEY,
  lut_id TEXT NOT NULL,
  item_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status='resolved_unique_physical_candidate'),
  UNIQUE(lut_id,item_id),
  FOREIGN KEY(lut_id) REFERENCES foundation_voice_lut(id),
  FOREIGN KEY(item_id) REFERENCES foundation_voice_item(id)
) WITHOUT ROWID;
CREATE TABLE foundation_audio_variant(
  id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  pointer TEXT NOT NULL,
  audio_path TEXT NOT NULL,
  selector TEXT NOT NULL,
  order_value REAL NOT NULL,
  raw_json TEXT NOT NULL,
  raw_sha256 TEXT NOT NULL,
  UNIQUE(item_id,ordinal),
  FOREIGN KEY(item_id) REFERENCES foundation_voice_item(id)
) WITHOUT ROWID;
CREATE TABLE foundation_relation(
  id TEXT PRIMARY KEY,
  source_row_id TEXT NOT NULL,
  source_pointer TEXT NOT NULL,
  kind TEXT NOT NULL,
  raw_locator_json TEXT,
  target_boundary TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN('resolved_unique','resolved_candidate_set','missing','terminal_unresolved','not_applicable')),
  candidate_count INTEGER NOT NULL CHECK(candidate_count>=0),
  search_complete INTEGER NOT NULL CHECK(search_complete IN(0,1)),
  UNIQUE(source_row_id,kind,source_pointer),
  FOREIGN KEY(source_row_id) REFERENCES foundation_primary_row(id)
) WITHOUT ROWID;
CREATE TABLE foundation_candidate(
  id TEXT PRIMARY KEY,
  relation_id TEXT NOT NULL,
  target_primary_id TEXT,
  target_lut_id TEXT,
  target_item_id TEXT,
  target_source_path TEXT NOT NULL,
  target_pointer TEXT NOT NULL,
  CHECK((target_primary_id IS NOT NULL)+(target_lut_id IS NOT NULL)+(target_item_id IS NOT NULL)=1),
  FOREIGN KEY(relation_id) REFERENCES foundation_relation(id),
  FOREIGN KEY(target_primary_id) REFERENCES foundation_primary_row(id),
  FOREIGN KEY(target_lut_id) REFERENCES foundation_voice_lut(id),
  FOREIGN KEY(target_item_id) REFERENCES foundation_voice_item(id)
) WITHOUT ROWID;
CREATE TABLE foundation_release(id INTEGER PRIMARY KEY CHECK(id=1), manifest_json TEXT NOT NULL) WITHOUT ROWID;
CREATE INDEX foundation_row_native ON foundation_primary_row(source_path,native_field,native_value);
CREATE INDEX foundation_text_hash ON foundation_text_owner(textmap_hash,field_name);
CREATE INDEX foundation_voice_lut_slot ON foundation_voice_lut(voice_slot,item_path);
CREATE INDEX foundation_voice_item_slot ON foundation_voice_item(voice_slot,source_path);
CREATE INDEX foundation_relation_kind ON foundation_relation(kind,status);
