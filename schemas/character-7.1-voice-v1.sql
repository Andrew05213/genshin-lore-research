PRAGMA foreign_keys=ON;
CREATE TABLE voice_record(
  row_id TEXT PRIMARY KEY,
  source_path TEXT NOT NULL,
  physical_ordinal INTEGER NOT NULL,
  pointer TEXT NOT NULL,
  avatar_status TEXT NOT NULL,
  avatar_candidate_count INTEGER NOT NULL,
  lut_status TEXT NOT NULL,
  lut_candidate_count INTEGER NOT NULL
) WITHOUT ROWID;
CREATE TABLE voice_text_ref(
  row_id TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  field_name TEXT NOT NULL,
  owner_pointer TEXT NOT NULL,
  PRIMARY KEY(row_id,owner_id),
  FOREIGN KEY(row_id) REFERENCES voice_record(row_id)
) WITHOUT ROWID;
CREATE TABLE voice_raw_field_ref(
  row_id TEXT NOT NULL,
  field_id TEXT NOT NULL,
  pointer TEXT NOT NULL,
  field_name TEXT NOT NULL CHECK(field_name='openConds'),
  role TEXT NOT NULL CHECK(role IN('source_condition_array','physical_array_child')),
  source_field_ordinal INTEGER NOT NULL,
  child_ordinal INTEGER,
  PRIMARY KEY(row_id,field_id),
  FOREIGN KEY(row_id) REFERENCES voice_record(row_id)
) WITHOUT ROWID;
CREATE TABLE voice_avatar_candidate(
  row_id TEXT NOT NULL,
  relation_id TEXT NOT NULL,
  avatar_row_id TEXT NOT NULL,
  PRIMARY KEY(row_id,relation_id,avatar_row_id),
  FOREIGN KEY(row_id) REFERENCES voice_record(row_id)
) WITHOUT ROWID;
CREATE TABLE voice_lut_candidate(
  row_id TEXT NOT NULL,
  relation_id TEXT NOT NULL,
  lut_id TEXT NOT NULL,
  PRIMARY KEY(row_id,relation_id,lut_id),
  FOREIGN KEY(row_id) REFERENCES voice_record(row_id)
) WITHOUT ROWID;
CREATE TABLE lut_ref(
  lut_id TEXT PRIMARY KEY,
  source_path TEXT NOT NULL,
  object_key TEXT NOT NULL,
  object_ordinal INTEGER NOT NULL,
  pointer TEXT NOT NULL,
  voice_slot TEXT NOT NULL,
  item_id TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE item_ref(
  item_id TEXT PRIMARY KEY,
  source_path TEXT NOT NULL,
  object_key TEXT NOT NULL,
  object_ordinal INTEGER NOT NULL,
  pointer TEXT NOT NULL,
  variants_status TEXT NOT NULL CHECK(variants_status IN('present','missing'))
) WITHOUT ROWID;
CREATE TABLE audio_ref(
  audio_id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL,
  physical_ordinal INTEGER NOT NULL,
  pointer TEXT NOT NULL,
  audio_path TEXT NOT NULL,
  selector TEXT NOT NULL,
  order_value REAL NOT NULL,
  FOREIGN KEY(item_id) REFERENCES item_ref(item_id)
) WITHOUT ROWID;
CREATE TABLE release(id INTEGER PRIMARY KEY CHECK(id=1), manifest_json TEXT NOT NULL) WITHOUT ROWID;
CREATE INDEX voice_order ON voice_record(source_path,physical_ordinal);
CREATE INDEX voice_text_owner_lookup ON voice_text_ref(owner_id,row_id);
CREATE INDEX voice_raw_field_order ON voice_raw_field_ref(row_id,source_field_ordinal,child_ordinal);
CREATE INDEX voice_avatar_reverse ON voice_avatar_candidate(avatar_row_id,row_id);
CREATE INDEX voice_lut_reverse ON voice_lut_candidate(lut_id,row_id);
CREATE INDEX voice_audio_order ON audio_ref(item_id,physical_ordinal);
