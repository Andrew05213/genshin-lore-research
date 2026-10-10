-- R1 Schema 19: family-specific Wave01 v2 text projection tables.
-- Existing Wave01 row tables remain run-scoped and receive additive v2 rows.
-- No cross-family identity, common entity table, or text/hash merge is created.

PRAGMA foreign_keys = ON;

CREATE UNIQUE INDEX idx_achievement_archive_row_occurrence_run
    ON achievement_archive_row_occurrence (occurrence_id, ingest_run_id);
CREATE TRIGGER trg_achievement_archive_row_immutable_insert_existing
BEFORE INSERT ON achievement_archive_row_occurrence
WHEN EXISTS (SELECT 1 FROM achievement_archive_row_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'achievement_archive row evidence is immutable');
END;

CREATE TABLE achievement_archive_text_owner_occurrence (
    owner_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL REFERENCES ingest_run(id),
    row_occurrence_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    field_pair_ordinal INTEGER NOT NULL CHECK (field_pair_ordinal >= 0),
    raw_hash_text TEXT NOT NULL,
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('resolved_all_locales','partial_missing','ambiguous')),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    PRIMARY KEY (owner_id, ingest_run_id),
    FOREIGN KEY (row_occurrence_id, ingest_run_id) REFERENCES achievement_archive_row_occurrence(occurrence_id, ingest_run_id)
);
CREATE INDEX idx_achievement_archive_text_owner_row
    ON achievement_archive_text_owner_occurrence (row_occurrence_id, ingest_run_id, field_name, field_pair_ordinal);

CREATE TABLE achievement_archive_text_resolution_occurrence (
    resolution_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    locale TEXT NOT NULL CHECK (locale IN ('CHS','EN','JP')),
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('resolved_unique','missing','ambiguous')),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    PRIMARY KEY (resolution_id, ingest_run_id),
    UNIQUE (owner_id, ingest_run_id, locale),
    FOREIGN KEY (owner_id, ingest_run_id) REFERENCES achievement_archive_text_owner_occurrence(owner_id, ingest_run_id)
);
CREATE INDEX idx_achievement_archive_text_resolution_owner
    ON achievement_archive_text_resolution_occurrence (owner_id, ingest_run_id, locale);

CREATE TABLE achievement_archive_text_candidate_occurrence (
    candidate_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    locale TEXT NOT NULL CHECK (locale IN ('CHS','EN','JP')),
    shard_tier TEXT NOT NULL CHECK (shard_tier IN ('main','medium')),
    relative_path TEXT NOT NULL,
    pair_ordinal INTEGER NOT NULL CHECK (pair_ordinal >= 0),
    pointer TEXT NOT NULL,
    raw_key_json TEXT NOT NULL,
    raw_text_json TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    raw_key_sha256 TEXT NOT NULL CHECK (length(raw_key_sha256)=64 AND raw_key_sha256 NOT GLOB '*[^0-9a-f]*'),
    raw_text_sha256 TEXT NOT NULL CHECK (length(raw_text_sha256)=64 AND raw_text_sha256 NOT GLOB '*[^0-9a-f]*'),
    raw_record_sha256 TEXT NOT NULL CHECK (length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
    PRIMARY KEY (candidate_id, ingest_run_id),
    FOREIGN KEY (owner_id, ingest_run_id) REFERENCES achievement_archive_text_owner_occurrence(owner_id, ingest_run_id)
);
CREATE INDEX idx_achievement_archive_text_candidate_owner
    ON achievement_archive_text_candidate_occurrence (owner_id, ingest_run_id, locale, relative_path, pair_ordinal, candidate_id);

CREATE TRIGGER trg_achievement_archive_text_owner_occurrence_immutable_insert
BEFORE INSERT ON achievement_archive_text_owner_occurrence
WHEN EXISTS (SELECT 1 FROM achievement_archive_text_owner_occurrence WHERE owner_id=NEW.owner_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'achievement_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_achievement_archive_text_owner_occurrence_immutable_update
BEFORE UPDATE ON achievement_archive_text_owner_occurrence
BEGIN
    SELECT RAISE(ABORT, 'achievement_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_achievement_archive_text_owner_occurrence_immutable_delete
BEFORE DELETE ON achievement_archive_text_owner_occurrence
BEGIN
    SELECT RAISE(ABORT, 'achievement_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_achievement_archive_text_resolution_occurrence_immutable_insert
BEFORE INSERT ON achievement_archive_text_resolution_occurrence
WHEN EXISTS (SELECT 1 FROM achievement_archive_text_resolution_occurrence WHERE resolution_id=NEW.resolution_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'achievement_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_achievement_archive_text_resolution_occurrence_immutable_update
BEFORE UPDATE ON achievement_archive_text_resolution_occurrence
BEGIN
    SELECT RAISE(ABORT, 'achievement_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_achievement_archive_text_resolution_occurrence_immutable_delete
BEFORE DELETE ON achievement_archive_text_resolution_occurrence
BEGIN
    SELECT RAISE(ABORT, 'achievement_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_achievement_archive_text_candidate_occurrence_immutable_insert
BEFORE INSERT ON achievement_archive_text_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM achievement_archive_text_candidate_occurrence WHERE candidate_id=NEW.candidate_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'achievement_archive text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_achievement_archive_text_candidate_occurrence_immutable_update
BEFORE UPDATE ON achievement_archive_text_candidate_occurrence
BEGIN
    SELECT RAISE(ABORT, 'achievement_archive text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_achievement_archive_text_candidate_occurrence_immutable_delete
BEFORE DELETE ON achievement_archive_text_candidate_occurrence
BEGIN
    SELECT RAISE(ABORT, 'achievement_archive text_candidate_occurrence evidence is immutable');
END;

CREATE UNIQUE INDEX idx_dungeon_archive_row_occurrence_run
    ON dungeon_archive_row_occurrence (occurrence_id, ingest_run_id);
CREATE TRIGGER trg_dungeon_archive_row_immutable_insert_existing
BEFORE INSERT ON dungeon_archive_row_occurrence
WHEN EXISTS (SELECT 1 FROM dungeon_archive_row_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'dungeon_archive row evidence is immutable');
END;

CREATE TABLE dungeon_archive_text_owner_occurrence (
    owner_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL REFERENCES ingest_run(id),
    row_occurrence_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    field_pair_ordinal INTEGER NOT NULL CHECK (field_pair_ordinal >= 0),
    raw_hash_text TEXT NOT NULL,
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('resolved_all_locales','partial_missing','ambiguous')),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    PRIMARY KEY (owner_id, ingest_run_id),
    FOREIGN KEY (row_occurrence_id, ingest_run_id) REFERENCES dungeon_archive_row_occurrence(occurrence_id, ingest_run_id)
);
CREATE INDEX idx_dungeon_archive_text_owner_row
    ON dungeon_archive_text_owner_occurrence (row_occurrence_id, ingest_run_id, field_name, field_pair_ordinal);

CREATE TABLE dungeon_archive_text_resolution_occurrence (
    resolution_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    locale TEXT NOT NULL CHECK (locale IN ('CHS','EN','JP')),
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('resolved_unique','missing','ambiguous')),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    PRIMARY KEY (resolution_id, ingest_run_id),
    UNIQUE (owner_id, ingest_run_id, locale),
    FOREIGN KEY (owner_id, ingest_run_id) REFERENCES dungeon_archive_text_owner_occurrence(owner_id, ingest_run_id)
);
CREATE INDEX idx_dungeon_archive_text_resolution_owner
    ON dungeon_archive_text_resolution_occurrence (owner_id, ingest_run_id, locale);

CREATE TABLE dungeon_archive_text_candidate_occurrence (
    candidate_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    locale TEXT NOT NULL CHECK (locale IN ('CHS','EN','JP')),
    shard_tier TEXT NOT NULL CHECK (shard_tier IN ('main','medium')),
    relative_path TEXT NOT NULL,
    pair_ordinal INTEGER NOT NULL CHECK (pair_ordinal >= 0),
    pointer TEXT NOT NULL,
    raw_key_json TEXT NOT NULL,
    raw_text_json TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    raw_key_sha256 TEXT NOT NULL CHECK (length(raw_key_sha256)=64 AND raw_key_sha256 NOT GLOB '*[^0-9a-f]*'),
    raw_text_sha256 TEXT NOT NULL CHECK (length(raw_text_sha256)=64 AND raw_text_sha256 NOT GLOB '*[^0-9a-f]*'),
    raw_record_sha256 TEXT NOT NULL CHECK (length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
    PRIMARY KEY (candidate_id, ingest_run_id),
    FOREIGN KEY (owner_id, ingest_run_id) REFERENCES dungeon_archive_text_owner_occurrence(owner_id, ingest_run_id)
);
CREATE INDEX idx_dungeon_archive_text_candidate_owner
    ON dungeon_archive_text_candidate_occurrence (owner_id, ingest_run_id, locale, relative_path, pair_ordinal, candidate_id);

CREATE TRIGGER trg_dungeon_archive_text_owner_occurrence_immutable_insert
BEFORE INSERT ON dungeon_archive_text_owner_occurrence
WHEN EXISTS (SELECT 1 FROM dungeon_archive_text_owner_occurrence WHERE owner_id=NEW.owner_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'dungeon_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_dungeon_archive_text_owner_occurrence_immutable_update
BEFORE UPDATE ON dungeon_archive_text_owner_occurrence
BEGIN
    SELECT RAISE(ABORT, 'dungeon_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_dungeon_archive_text_owner_occurrence_immutable_delete
BEFORE DELETE ON dungeon_archive_text_owner_occurrence
BEGIN
    SELECT RAISE(ABORT, 'dungeon_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_dungeon_archive_text_resolution_occurrence_immutable_insert
BEFORE INSERT ON dungeon_archive_text_resolution_occurrence
WHEN EXISTS (SELECT 1 FROM dungeon_archive_text_resolution_occurrence WHERE resolution_id=NEW.resolution_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'dungeon_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_dungeon_archive_text_resolution_occurrence_immutable_update
BEFORE UPDATE ON dungeon_archive_text_resolution_occurrence
BEGIN
    SELECT RAISE(ABORT, 'dungeon_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_dungeon_archive_text_resolution_occurrence_immutable_delete
BEFORE DELETE ON dungeon_archive_text_resolution_occurrence
BEGIN
    SELECT RAISE(ABORT, 'dungeon_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_dungeon_archive_text_candidate_occurrence_immutable_insert
BEFORE INSERT ON dungeon_archive_text_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM dungeon_archive_text_candidate_occurrence WHERE candidate_id=NEW.candidate_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'dungeon_archive text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_dungeon_archive_text_candidate_occurrence_immutable_update
BEFORE UPDATE ON dungeon_archive_text_candidate_occurrence
BEGIN
    SELECT RAISE(ABORT, 'dungeon_archive text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_dungeon_archive_text_candidate_occurrence_immutable_delete
BEFORE DELETE ON dungeon_archive_text_candidate_occurrence
BEGIN
    SELECT RAISE(ABORT, 'dungeon_archive text_candidate_occurrence evidence is immutable');
END;

CREATE UNIQUE INDEX idx_npc_archive_row_occurrence_run
    ON npc_archive_row_occurrence (occurrence_id, ingest_run_id);
CREATE TRIGGER trg_npc_archive_row_immutable_insert_existing
BEFORE INSERT ON npc_archive_row_occurrence
WHEN EXISTS (SELECT 1 FROM npc_archive_row_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'npc_archive row evidence is immutable');
END;

CREATE TABLE npc_archive_text_owner_occurrence (
    owner_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL REFERENCES ingest_run(id),
    row_occurrence_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    field_pair_ordinal INTEGER NOT NULL CHECK (field_pair_ordinal >= 0),
    raw_hash_text TEXT NOT NULL,
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('resolved_all_locales','partial_missing','ambiguous')),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    PRIMARY KEY (owner_id, ingest_run_id),
    FOREIGN KEY (row_occurrence_id, ingest_run_id) REFERENCES npc_archive_row_occurrence(occurrence_id, ingest_run_id)
);
CREATE INDEX idx_npc_archive_text_owner_row
    ON npc_archive_text_owner_occurrence (row_occurrence_id, ingest_run_id, field_name, field_pair_ordinal);

CREATE TABLE npc_archive_text_resolution_occurrence (
    resolution_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    locale TEXT NOT NULL CHECK (locale IN ('CHS','EN','JP')),
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('resolved_unique','missing','ambiguous')),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    PRIMARY KEY (resolution_id, ingest_run_id),
    UNIQUE (owner_id, ingest_run_id, locale),
    FOREIGN KEY (owner_id, ingest_run_id) REFERENCES npc_archive_text_owner_occurrence(owner_id, ingest_run_id)
);
CREATE INDEX idx_npc_archive_text_resolution_owner
    ON npc_archive_text_resolution_occurrence (owner_id, ingest_run_id, locale);

CREATE TABLE npc_archive_text_candidate_occurrence (
    candidate_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    locale TEXT NOT NULL CHECK (locale IN ('CHS','EN','JP')),
    shard_tier TEXT NOT NULL CHECK (shard_tier IN ('main','medium')),
    relative_path TEXT NOT NULL,
    pair_ordinal INTEGER NOT NULL CHECK (pair_ordinal >= 0),
    pointer TEXT NOT NULL,
    raw_key_json TEXT NOT NULL,
    raw_text_json TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    raw_key_sha256 TEXT NOT NULL CHECK (length(raw_key_sha256)=64 AND raw_key_sha256 NOT GLOB '*[^0-9a-f]*'),
    raw_text_sha256 TEXT NOT NULL CHECK (length(raw_text_sha256)=64 AND raw_text_sha256 NOT GLOB '*[^0-9a-f]*'),
    raw_record_sha256 TEXT NOT NULL CHECK (length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
    PRIMARY KEY (candidate_id, ingest_run_id),
    FOREIGN KEY (owner_id, ingest_run_id) REFERENCES npc_archive_text_owner_occurrence(owner_id, ingest_run_id)
);
CREATE INDEX idx_npc_archive_text_candidate_owner
    ON npc_archive_text_candidate_occurrence (owner_id, ingest_run_id, locale, relative_path, pair_ordinal, candidate_id);

CREATE TRIGGER trg_npc_archive_text_owner_occurrence_immutable_insert
BEFORE INSERT ON npc_archive_text_owner_occurrence
WHEN EXISTS (SELECT 1 FROM npc_archive_text_owner_occurrence WHERE owner_id=NEW.owner_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'npc_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_npc_archive_text_owner_occurrence_immutable_update
BEFORE UPDATE ON npc_archive_text_owner_occurrence
BEGIN
    SELECT RAISE(ABORT, 'npc_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_npc_archive_text_owner_occurrence_immutable_delete
BEFORE DELETE ON npc_archive_text_owner_occurrence
BEGIN
    SELECT RAISE(ABORT, 'npc_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_npc_archive_text_resolution_occurrence_immutable_insert
BEFORE INSERT ON npc_archive_text_resolution_occurrence
WHEN EXISTS (SELECT 1 FROM npc_archive_text_resolution_occurrence WHERE resolution_id=NEW.resolution_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'npc_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_npc_archive_text_resolution_occurrence_immutable_update
BEFORE UPDATE ON npc_archive_text_resolution_occurrence
BEGIN
    SELECT RAISE(ABORT, 'npc_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_npc_archive_text_resolution_occurrence_immutable_delete
BEFORE DELETE ON npc_archive_text_resolution_occurrence
BEGIN
    SELECT RAISE(ABORT, 'npc_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_npc_archive_text_candidate_occurrence_immutable_insert
BEFORE INSERT ON npc_archive_text_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM npc_archive_text_candidate_occurrence WHERE candidate_id=NEW.candidate_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'npc_archive text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_npc_archive_text_candidate_occurrence_immutable_update
BEFORE UPDATE ON npc_archive_text_candidate_occurrence
BEGIN
    SELECT RAISE(ABORT, 'npc_archive text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_npc_archive_text_candidate_occurrence_immutable_delete
BEFORE DELETE ON npc_archive_text_candidate_occurrence
BEGIN
    SELECT RAISE(ABORT, 'npc_archive text_candidate_occurrence evidence is immutable');
END;

CREATE UNIQUE INDEX idx_scene_archive_row_occurrence_run
    ON scene_archive_row_occurrence (occurrence_id, ingest_run_id);
CREATE TRIGGER trg_scene_archive_row_immutable_insert_existing
BEFORE INSERT ON scene_archive_row_occurrence
WHEN EXISTS (SELECT 1 FROM scene_archive_row_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'scene_archive row evidence is immutable');
END;

CREATE TABLE scene_archive_text_owner_occurrence (
    owner_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL REFERENCES ingest_run(id),
    row_occurrence_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    field_pair_ordinal INTEGER NOT NULL CHECK (field_pair_ordinal >= 0),
    raw_hash_text TEXT NOT NULL,
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('resolved_all_locales','partial_missing','ambiguous')),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    PRIMARY KEY (owner_id, ingest_run_id),
    FOREIGN KEY (row_occurrence_id, ingest_run_id) REFERENCES scene_archive_row_occurrence(occurrence_id, ingest_run_id)
);
CREATE INDEX idx_scene_archive_text_owner_row
    ON scene_archive_text_owner_occurrence (row_occurrence_id, ingest_run_id, field_name, field_pair_ordinal);

CREATE TABLE scene_archive_text_resolution_occurrence (
    resolution_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    locale TEXT NOT NULL CHECK (locale IN ('CHS','EN','JP')),
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('resolved_unique','missing','ambiguous')),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    PRIMARY KEY (resolution_id, ingest_run_id),
    UNIQUE (owner_id, ingest_run_id, locale),
    FOREIGN KEY (owner_id, ingest_run_id) REFERENCES scene_archive_text_owner_occurrence(owner_id, ingest_run_id)
);
CREATE INDEX idx_scene_archive_text_resolution_owner
    ON scene_archive_text_resolution_occurrence (owner_id, ingest_run_id, locale);

CREATE TABLE scene_archive_text_candidate_occurrence (
    candidate_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    locale TEXT NOT NULL CHECK (locale IN ('CHS','EN','JP')),
    shard_tier TEXT NOT NULL CHECK (shard_tier IN ('main','medium')),
    relative_path TEXT NOT NULL,
    pair_ordinal INTEGER NOT NULL CHECK (pair_ordinal >= 0),
    pointer TEXT NOT NULL,
    raw_key_json TEXT NOT NULL,
    raw_text_json TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    raw_key_sha256 TEXT NOT NULL CHECK (length(raw_key_sha256)=64 AND raw_key_sha256 NOT GLOB '*[^0-9a-f]*'),
    raw_text_sha256 TEXT NOT NULL CHECK (length(raw_text_sha256)=64 AND raw_text_sha256 NOT GLOB '*[^0-9a-f]*'),
    raw_record_sha256 TEXT NOT NULL CHECK (length(raw_record_sha256)=64 AND raw_record_sha256 NOT GLOB '*[^0-9a-f]*'),
    PRIMARY KEY (candidate_id, ingest_run_id),
    FOREIGN KEY (owner_id, ingest_run_id) REFERENCES scene_archive_text_owner_occurrence(owner_id, ingest_run_id)
);
CREATE INDEX idx_scene_archive_text_candidate_owner
    ON scene_archive_text_candidate_occurrence (owner_id, ingest_run_id, locale, relative_path, pair_ordinal, candidate_id);

CREATE TRIGGER trg_scene_archive_text_owner_occurrence_immutable_insert
BEFORE INSERT ON scene_archive_text_owner_occurrence
WHEN EXISTS (SELECT 1 FROM scene_archive_text_owner_occurrence WHERE owner_id=NEW.owner_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'scene_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_scene_archive_text_owner_occurrence_immutable_update
BEFORE UPDATE ON scene_archive_text_owner_occurrence
BEGIN
    SELECT RAISE(ABORT, 'scene_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_scene_archive_text_owner_occurrence_immutable_delete
BEFORE DELETE ON scene_archive_text_owner_occurrence
BEGIN
    SELECT RAISE(ABORT, 'scene_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_scene_archive_text_resolution_occurrence_immutable_insert
BEFORE INSERT ON scene_archive_text_resolution_occurrence
WHEN EXISTS (SELECT 1 FROM scene_archive_text_resolution_occurrence WHERE resolution_id=NEW.resolution_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'scene_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_scene_archive_text_resolution_occurrence_immutable_update
BEFORE UPDATE ON scene_archive_text_resolution_occurrence
BEGIN
    SELECT RAISE(ABORT, 'scene_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_scene_archive_text_resolution_occurrence_immutable_delete
BEFORE DELETE ON scene_archive_text_resolution_occurrence
BEGIN
    SELECT RAISE(ABORT, 'scene_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_scene_archive_text_candidate_occurrence_immutable_insert
BEFORE INSERT ON scene_archive_text_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM scene_archive_text_candidate_occurrence WHERE candidate_id=NEW.candidate_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'scene_archive text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_scene_archive_text_candidate_occurrence_immutable_update
BEFORE UPDATE ON scene_archive_text_candidate_occurrence
BEGIN
    SELECT RAISE(ABORT, 'scene_archive text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_scene_archive_text_candidate_occurrence_immutable_delete
BEFORE DELETE ON scene_archive_text_candidate_occurrence
BEGIN
    SELECT RAISE(ABORT, 'scene_archive text_candidate_occurrence evidence is immutable');
END;
