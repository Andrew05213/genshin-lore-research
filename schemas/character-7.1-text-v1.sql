PRAGMA foreign_keys=ON;
CREATE TABLE resolution(
  owner_id TEXT NOT NULL,
  row_id TEXT NOT NULL,
  owner_pointer TEXT NOT NULL,
  field_name TEXT NOT NULL,
  textmap_hash TEXT NOT NULL,
  locale TEXT NOT NULL CHECK(locale IN('CHS','EN','JP')),
  status TEXT NOT NULL CHECK(status IN('resolved_unique','resolved_candidate_set','missing')),
  candidate_count INTEGER NOT NULL CHECK(candidate_count>=0),
  PRIMARY KEY(owner_id,locale)
) WITHOUT ROWID;
CREATE TABLE candidate(
  owner_id TEXT NOT NULL,
  locale TEXT NOT NULL,
  tier TEXT NOT NULL CHECK(tier IN('main','medium')),
  source_path TEXT NOT NULL,
  physical_ordinal INTEGER NOT NULL,
  source_key TEXT NOT NULL,
  raw_text TEXT NOT NULL,
  PRIMARY KEY(owner_id,locale,source_path,physical_ordinal),
  FOREIGN KEY(owner_id,locale) REFERENCES resolution(owner_id,locale)
) WITHOUT ROWID;
CREATE TABLE release(id INTEGER PRIMARY KEY CHECK(id=1), manifest_json TEXT NOT NULL) WITHOUT ROWID;
CREATE INDEX resolution_hash ON resolution(textmap_hash,locale,status);
CREATE INDEX candidate_source ON candidate(source_path,physical_ordinal);
