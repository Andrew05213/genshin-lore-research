PRAGMA foreign_keys=ON;
CREATE TABLE context_release (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json))
) WITHOUT ROWID;
CREATE TABLE context_entry (
  id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL,
  source_run_id TEXT NOT NULL,
  family TEXT NOT NULL CHECK(family IN ('readable_archive','document_archive')),
  anchor_id TEXT NOT NULL,
  source_path TEXT NOT NULL,
  source_kind TEXT NOT NULL,
  display_title TEXT,
  display_subtitle TEXT,
  chapter_title TEXT,
  title_resolution_status TEXT NOT NULL CHECK(title_resolution_status IN ('resolved','ambiguous','unresolved')),
  title_basis TEXT,
  candidate_titles_json TEXT NOT NULL CHECK(json_valid(candidate_titles_json)),
  citation_label_base TEXT NOT NULL,
  UNIQUE(family,source_run_id,anchor_id)
) WITHOUT ROWID;
CREATE INDEX context_entry_source ON context_entry(family,source_path);
CREATE TRIGGER context_entry_immutable_update BEFORE UPDATE ON context_entry BEGIN SELECT RAISE(ABORT,'immutable derived context'); END;
CREATE TRIGGER context_entry_immutable_delete BEFORE DELETE ON context_entry BEGIN SELECT RAISE(ABORT,'immutable derived context'); END;
CREATE TRIGGER context_entry_no_replace BEFORE INSERT ON context_entry WHEN EXISTS(SELECT 1 FROM context_entry WHERE id=NEW.id OR (family=NEW.family AND source_run_id=NEW.source_run_id AND anchor_id=NEW.anchor_id)) BEGIN SELECT RAISE(ABORT,'immutable derived context collision'); END;
CREATE TRIGGER context_release_immutable_update BEFORE UPDATE ON context_release BEGIN SELECT RAISE(ABORT,'immutable derived context'); END;
CREATE TRIGGER context_release_immutable_delete BEFORE DELETE ON context_release BEGIN SELECT RAISE(ABORT,'immutable derived context'); END;
CREATE TRIGGER context_release_no_replace BEFORE INSERT ON context_release WHEN EXISTS(SELECT 1 FROM context_release) BEGIN SELECT RAISE(ABORT,'immutable derived context collision'); END;
