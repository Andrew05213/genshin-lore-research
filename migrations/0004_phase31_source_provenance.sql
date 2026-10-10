-- Phase 3.1A source provenance write-once hardening.
--
-- Source identity is immutable after insertion.  Snapshot status remains a
-- lifecycle field and may be changed by the ingestion gate; parser/build/run
-- metadata must not rewrite source identity rows.

PRAGMA foreign_keys = ON;

CREATE TRIGGER IF NOT EXISTS trg_source_snapshot_identity_immutable
BEFORE UPDATE ON source_snapshot
WHEN OLD.id IS NOT NEW.id
  OR OLD.source_id IS NOT NEW.source_id
  OR OLD.release_label IS NOT NEW.release_label
  OR OLD.build IS NOT NEW.build
  OR OLD."commit" IS NOT NEW."commit"
  OR OLD.tree IS NOT NEW.tree
  OR OLD.observed_at IS NOT NEW.observed_at
  OR OLD.parser_version IS NOT NEW.parser_version
  OR OLD.mapping_version IS NOT NEW.mapping_version
  OR OLD.metadata_json IS NOT NEW.metadata_json
BEGIN
    SELECT RAISE(ABORT, 'source_snapshot identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_source_file_identity_immutable
BEFORE UPDATE ON source_file
WHEN OLD.id IS NOT NEW.id
  OR OLD.snapshot_id IS NOT NEW.snapshot_id
  OR OLD.relative_path IS NOT NEW.relative_path
  OR OLD.upstream_blob_oid IS NOT NEW.upstream_blob_oid
  OR OLD.sha256 IS NOT NEW.sha256
  OR OLD.size_bytes IS NOT NEW.size_bytes
  OR OLD.format IS NOT NEW.format
  OR OLD.root_path IS NOT NEW.root_path
BEGIN
    SELECT RAISE(ABORT, 'source_file identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_source_record_identity_immutable
BEFORE UPDATE ON source_record
WHEN OLD.id IS NOT NEW.id
  OR OLD.source_file_id IS NOT NEW.source_file_id
  OR OLD.pointer IS NOT NEW.pointer
  OR OLD.record_kind IS NOT NEW.record_kind
  OR OLD.native_id IS NOT NEW.native_id
  OR OLD.raw_sha256 IS NOT NEW.raw_sha256
  OR OLD.raw_json IS NOT NEW.raw_json
BEGIN
    SELECT RAISE(ABORT, 'source_record identity is immutable');
END;
