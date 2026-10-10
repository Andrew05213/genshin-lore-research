PRAGMA foreign_keys=ON;
CREATE TABLE lore_record(
  row_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN('avatar','fetter_info','story')),
  source_path TEXT NOT NULL,
  physical_ordinal INTEGER NOT NULL,
  pointer TEXT NOT NULL,
  owner_status TEXT NOT NULL,
  owner_candidate_count INTEGER NOT NULL CHECK(owner_candidate_count>=0)
) WITHOUT ROWID;
CREATE TABLE lore_text_ref(
  row_id TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  field_name TEXT NOT NULL,
  owner_pointer TEXT NOT NULL,
  PRIMARY KEY(row_id,owner_id),
  FOREIGN KEY(row_id) REFERENCES lore_record(row_id)
) WITHOUT ROWID;
CREATE TABLE lore_raw_field_ref(
  row_id TEXT NOT NULL,
  field_id TEXT NOT NULL,
  pointer TEXT NOT NULL,
  field_name TEXT NOT NULL CHECK(field_name IN('finishConds','openConds')),
  role TEXT NOT NULL CHECK(role IN('source_condition_array','physical_array_child')),
  source_field_ordinal INTEGER NOT NULL,
  child_ordinal INTEGER,
  PRIMARY KEY(row_id,field_id),
  FOREIGN KEY(row_id) REFERENCES lore_record(row_id)
) WITHOUT ROWID;
CREATE TABLE lore_avatar_candidate(
  row_id TEXT NOT NULL,
  relation_id TEXT NOT NULL,
  avatar_row_id TEXT NOT NULL,
  PRIMARY KEY(row_id,relation_id,avatar_row_id),
  FOREIGN KEY(row_id) REFERENCES lore_record(row_id)
) WITHOUT ROWID;
CREATE TABLE release(id INTEGER PRIMARY KEY CHECK(id=1), manifest_json TEXT NOT NULL) WITHOUT ROWID;
CREATE INDEX lore_kind_order ON lore_record(kind,source_path,physical_ordinal);
CREATE INDEX lore_text_owner_lookup ON lore_text_ref(owner_id,row_id);
CREATE INDEX lore_raw_field_order ON lore_raw_field_ref(row_id,source_field_ordinal,child_ordinal);
CREATE INDEX lore_avatar_reverse ON lore_avatar_candidate(avatar_row_id,row_id);
