CREATE TABLE row_occurrence(
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, boundary TEXT NOT NULL,
  source_path TEXT NOT NULL, ordinal INTEGER NOT NULL, pointer TEXT NOT NULL,
  native_id_field TEXT, native_id TEXT, raw_json TEXT, raw_sha256 TEXT NOT NULL,
  reuse_family TEXT, reuse_contract TEXT, reuse_parser TEXT, reuse_run TEXT, reuse_occurrence_id TEXT,
  CHECK ((raw_json IS NOT NULL AND reuse_occurrence_id IS NULL) OR
         (raw_json IS NULL AND reuse_family IS NOT NULL AND reuse_contract IS NOT NULL AND
          reuse_parser IS NOT NULL AND reuse_run IS NOT NULL AND reuse_occurrence_id IS NOT NULL)),
  UNIQUE(run_id,source_path,pointer)
) WITHOUT ROWID;
CREATE TABLE text_slot(
  id TEXT PRIMARY KEY, row_id TEXT NOT NULL REFERENCES row_occurrence(id),
  owner_field TEXT NOT NULL, owner_role_status TEXT NOT NULL CHECK(owner_role_status IN('resolved','mapping_unresolved')),
  locale TEXT NOT NULL CHECK(locale IN('CHS','EN','JP')),
  raw_hash TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN('resolved_unique_main','resolved_unique_medium','resolved_candidate_set','missing')),
  candidate_count INTEGER NOT NULL, UNIQUE(row_id,owner_field,locale)
) WITHOUT ROWID;
CREATE TABLE text_candidate(
  id TEXT PRIMARY KEY, slot_id TEXT NOT NULL REFERENCES text_slot(id),
  source_path TEXT NOT NULL, tier TEXT NOT NULL CHECK(tier IN('main','medium')),
  pair_ordinal INTEGER NOT NULL, pointer TEXT NOT NULL, raw_text TEXT NOT NULL, raw_text_sha256 TEXT NOT NULL,
  UNIQUE(slot_id,source_path,pair_ordinal)
) WITHOUT ROWID;
CREATE TABLE relation_locator(
  id TEXT PRIMARY KEY, row_id TEXT NOT NULL REFERENCES row_occurrence(id),
  source_field TEXT NOT NULL, relation_kind TEXT NOT NULL, raw_locator TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN('resolved_unique','resolved_candidate_set','missing','mapping_unresolved')),
  candidate_count INTEGER NOT NULL, UNIQUE(row_id,source_field,relation_kind)
) WITHOUT ROWID;
CREATE TABLE relation_candidate(
  id TEXT PRIMARY KEY, locator_id TEXT NOT NULL REFERENCES relation_locator(id),
  target_family TEXT NOT NULL, target_contract TEXT NOT NULL, target_parser TEXT NOT NULL,
  target_run TEXT NOT NULL, target_occurrence_id TEXT NOT NULL,
  target_source_path TEXT NOT NULL, target_pointer TEXT NOT NULL, target_raw_sha256 TEXT NOT NULL,
  UNIQUE(locator_id,target_family,target_run,target_occurrence_id)
) WITHOUT ROWID;
CREATE TABLE release(id INTEGER PRIMARY KEY CHECK(id=1), json TEXT NOT NULL) WITHOUT ROWID;
CREATE INDEX row_boundary ON row_occurrence(boundary,id);
CREATE INDEX slot_row ON text_slot(row_id,id);
CREATE INDEX candidate_slot ON text_candidate(slot_id,id);
CREATE INDEX locator_row ON relation_locator(row_id,id);
CREATE INDEX relation_candidate_locator ON relation_candidate(locator_id,id);
CREATE TRIGGER row_insert BEFORE INSERT ON row_occurrence WHEN EXISTS(SELECT 1 FROM release) BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER slot_insert BEFORE INSERT ON text_slot WHEN EXISTS(SELECT 1 FROM release) BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER text_insert BEFORE INSERT ON text_candidate WHEN EXISTS(SELECT 1 FROM release) BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER locator_insert BEFORE INSERT ON relation_locator WHEN EXISTS(SELECT 1 FROM release) BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER relation_insert BEFORE INSERT ON relation_candidate WHEN EXISTS(SELECT 1 FROM release) BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER release_insert BEFORE INSERT ON release WHEN EXISTS(SELECT 1 FROM release) BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER row_update BEFORE UPDATE ON row_occurrence BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER slot_update BEFORE UPDATE ON text_slot BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER text_update BEFORE UPDATE ON text_candidate BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER locator_update BEFORE UPDATE ON relation_locator BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER relation_update BEFORE UPDATE ON relation_candidate BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER release_update BEFORE UPDATE ON release BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER row_delete BEFORE DELETE ON row_occurrence BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER slot_delete BEFORE DELETE ON text_slot BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER text_delete BEFORE DELETE ON text_candidate BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER locator_delete BEFORE DELETE ON relation_locator BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER relation_delete BEFORE DELETE ON relation_candidate BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER release_delete BEFORE DELETE ON release BEGIN SELECT RAISE(ABORT,'immutable'); END;
