-- R4 additive six-family text projection. Frozen R3 occurrence IDs are preserved.
PRAGMA foreign_keys=ON;

CREATE UNIQUE INDEX idx_activity_archive_row_occurrence_run
    ON activity_archive_row_occurrence (occurrence_id, ingest_run_id);
CREATE TRIGGER trg_activity_archive_row_immutable_insert_existing
BEFORE INSERT ON activity_archive_row_occurrence
WHEN EXISTS (SELECT 1 FROM activity_archive_row_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'activity_archive row evidence is immutable');
END;

CREATE TABLE activity_archive_text_owner_occurrence (
    owner_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL REFERENCES ingest_run(id),
    row_occurrence_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    field_pair_ordinal INTEGER NOT NULL CHECK (field_pair_ordinal >= 0),
    raw_hash_text TEXT NOT NULL,
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('resolved_all_locales','partial_missing','ambiguous')),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    PRIMARY KEY (owner_id, ingest_run_id),
    FOREIGN KEY (row_occurrence_id, ingest_run_id) REFERENCES activity_archive_row_occurrence(occurrence_id, ingest_run_id)
);
CREATE INDEX idx_activity_archive_text_owner_row
    ON activity_archive_text_owner_occurrence (row_occurrence_id, ingest_run_id, field_name, field_pair_ordinal);

CREATE TABLE activity_archive_text_resolution_occurrence (
    resolution_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    locale TEXT NOT NULL CHECK (locale IN ('CHS','EN','JP')),
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('resolved_unique','missing','ambiguous')),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    PRIMARY KEY (resolution_id, ingest_run_id),
    UNIQUE (owner_id, ingest_run_id, locale),
    FOREIGN KEY (owner_id, ingest_run_id) REFERENCES activity_archive_text_owner_occurrence(owner_id, ingest_run_id)
);
CREATE INDEX idx_activity_archive_text_resolution_owner
    ON activity_archive_text_resolution_occurrence (owner_id, ingest_run_id, locale);

CREATE TABLE activity_archive_text_candidate_occurrence (
    candidate_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    textmap_snapshot_id TEXT NOT NULL,
    textmap_parser_build_id TEXT NOT NULL,
    textmap_ingest_run_id TEXT NOT NULL,
    source_record_pointer TEXT NOT NULL CHECK(source_record_pointer='/'),
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
    FOREIGN KEY (owner_id, ingest_run_id) REFERENCES activity_archive_text_owner_occurrence(owner_id, ingest_run_id),
    FOREIGN KEY(textmap_ingest_run_id,textmap_snapshot_id,textmap_parser_build_id) REFERENCES ingest_run(id,snapshot_id,parser_build_id)
);
CREATE INDEX idx_activity_archive_text_candidate_owner
    ON activity_archive_text_candidate_occurrence (owner_id, ingest_run_id, locale, relative_path, pair_ordinal, candidate_id);

CREATE TRIGGER trg_activity_archive_text_owner_occurrence_immutable_insert
BEFORE INSERT ON activity_archive_text_owner_occurrence
WHEN EXISTS (SELECT 1 FROM activity_archive_text_owner_occurrence WHERE owner_id=NEW.owner_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'activity_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_activity_archive_text_owner_occurrence_immutable_update
BEFORE UPDATE ON activity_archive_text_owner_occurrence
BEGIN
    SELECT RAISE(ABORT, 'activity_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_activity_archive_text_owner_occurrence_immutable_delete
BEFORE DELETE ON activity_archive_text_owner_occurrence
BEGIN
    SELECT RAISE(ABORT, 'activity_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_activity_archive_text_resolution_occurrence_immutable_insert
BEFORE INSERT ON activity_archive_text_resolution_occurrence
WHEN EXISTS (SELECT 1 FROM activity_archive_text_resolution_occurrence WHERE resolution_id=NEW.resolution_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'activity_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_activity_archive_text_resolution_occurrence_immutable_update
BEFORE UPDATE ON activity_archive_text_resolution_occurrence
BEGIN
    SELECT RAISE(ABORT, 'activity_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_activity_archive_text_resolution_occurrence_immutable_delete
BEFORE DELETE ON activity_archive_text_resolution_occurrence
BEGIN
    SELECT RAISE(ABORT, 'activity_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_activity_archive_text_candidate_occurrence_immutable_insert
BEFORE INSERT ON activity_archive_text_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM activity_archive_text_candidate_occurrence WHERE candidate_id=NEW.candidate_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'activity_archive text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_activity_archive_text_candidate_occurrence_immutable_update
BEFORE UPDATE ON activity_archive_text_candidate_occurrence
BEGIN
    SELECT RAISE(ABORT, 'activity_archive text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_activity_archive_text_candidate_occurrence_immutable_delete
BEFORE DELETE ON activity_archive_text_candidate_occurrence
BEGIN
    SELECT RAISE(ABORT, 'activity_archive text_candidate_occurrence evidence is immutable');
END;

CREATE UNIQUE INDEX idx_avatar_config_archive_row_occurrence_run
    ON avatar_config_archive_row_occurrence (occurrence_id, ingest_run_id);
CREATE TRIGGER trg_avatar_config_archive_row_immutable_insert_existing
BEFORE INSERT ON avatar_config_archive_row_occurrence
WHEN EXISTS (SELECT 1 FROM avatar_config_archive_row_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'avatar_config_archive row evidence is immutable');
END;

CREATE TABLE avatar_config_archive_text_owner_occurrence (
    owner_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL REFERENCES ingest_run(id),
    row_occurrence_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    field_pair_ordinal INTEGER NOT NULL CHECK (field_pair_ordinal >= 0),
    raw_hash_text TEXT NOT NULL,
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('resolved_all_locales','partial_missing','ambiguous')),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    PRIMARY KEY (owner_id, ingest_run_id),
    FOREIGN KEY (row_occurrence_id, ingest_run_id) REFERENCES avatar_config_archive_row_occurrence(occurrence_id, ingest_run_id)
);
CREATE INDEX idx_avatar_config_archive_text_owner_row
    ON avatar_config_archive_text_owner_occurrence (row_occurrence_id, ingest_run_id, field_name, field_pair_ordinal);

CREATE TABLE avatar_config_archive_text_resolution_occurrence (
    resolution_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    locale TEXT NOT NULL CHECK (locale IN ('CHS','EN','JP')),
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('resolved_unique','missing','ambiguous')),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    PRIMARY KEY (resolution_id, ingest_run_id),
    UNIQUE (owner_id, ingest_run_id, locale),
    FOREIGN KEY (owner_id, ingest_run_id) REFERENCES avatar_config_archive_text_owner_occurrence(owner_id, ingest_run_id)
);
CREATE INDEX idx_avatar_config_archive_text_resolution_owner
    ON avatar_config_archive_text_resolution_occurrence (owner_id, ingest_run_id, locale);

CREATE TABLE avatar_config_archive_text_candidate_occurrence (
    candidate_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    textmap_snapshot_id TEXT NOT NULL,
    textmap_parser_build_id TEXT NOT NULL,
    textmap_ingest_run_id TEXT NOT NULL,
    source_record_pointer TEXT NOT NULL CHECK(source_record_pointer='/'),
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
    FOREIGN KEY (owner_id, ingest_run_id) REFERENCES avatar_config_archive_text_owner_occurrence(owner_id, ingest_run_id),
    FOREIGN KEY(textmap_ingest_run_id,textmap_snapshot_id,textmap_parser_build_id) REFERENCES ingest_run(id,snapshot_id,parser_build_id)
);
CREATE INDEX idx_avatar_config_archive_text_candidate_owner
    ON avatar_config_archive_text_candidate_occurrence (owner_id, ingest_run_id, locale, relative_path, pair_ordinal, candidate_id);

CREATE TRIGGER trg_avatar_config_archive_text_owner_occurrence_immutable_insert
BEFORE INSERT ON avatar_config_archive_text_owner_occurrence
WHEN EXISTS (SELECT 1 FROM avatar_config_archive_text_owner_occurrence WHERE owner_id=NEW.owner_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'avatar_config_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_avatar_config_archive_text_owner_occurrence_immutable_update
BEFORE UPDATE ON avatar_config_archive_text_owner_occurrence
BEGIN
    SELECT RAISE(ABORT, 'avatar_config_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_avatar_config_archive_text_owner_occurrence_immutable_delete
BEFORE DELETE ON avatar_config_archive_text_owner_occurrence
BEGIN
    SELECT RAISE(ABORT, 'avatar_config_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_avatar_config_archive_text_resolution_occurrence_immutable_insert
BEFORE INSERT ON avatar_config_archive_text_resolution_occurrence
WHEN EXISTS (SELECT 1 FROM avatar_config_archive_text_resolution_occurrence WHERE resolution_id=NEW.resolution_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'avatar_config_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_avatar_config_archive_text_resolution_occurrence_immutable_update
BEFORE UPDATE ON avatar_config_archive_text_resolution_occurrence
BEGIN
    SELECT RAISE(ABORT, 'avatar_config_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_avatar_config_archive_text_resolution_occurrence_immutable_delete
BEFORE DELETE ON avatar_config_archive_text_resolution_occurrence
BEGIN
    SELECT RAISE(ABORT, 'avatar_config_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_avatar_config_archive_text_candidate_occurrence_immutable_insert
BEFORE INSERT ON avatar_config_archive_text_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM avatar_config_archive_text_candidate_occurrence WHERE candidate_id=NEW.candidate_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'avatar_config_archive text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_avatar_config_archive_text_candidate_occurrence_immutable_update
BEFORE UPDATE ON avatar_config_archive_text_candidate_occurrence
BEGIN
    SELECT RAISE(ABORT, 'avatar_config_archive text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_avatar_config_archive_text_candidate_occurrence_immutable_delete
BEFORE DELETE ON avatar_config_archive_text_candidate_occurrence
BEGIN
    SELECT RAISE(ABORT, 'avatar_config_archive text_candidate_occurrence evidence is immutable');
END;

CREATE UNIQUE INDEX idx_material_config_archive_row_occurrence_run
    ON material_config_archive_row_occurrence (occurrence_id, ingest_run_id);
CREATE TRIGGER trg_material_config_archive_row_immutable_insert_existing
BEFORE INSERT ON material_config_archive_row_occurrence
WHEN EXISTS (SELECT 1 FROM material_config_archive_row_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'material_config_archive row evidence is immutable');
END;

CREATE TABLE material_config_archive_text_owner_occurrence (
    owner_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL REFERENCES ingest_run(id),
    row_occurrence_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    field_pair_ordinal INTEGER NOT NULL CHECK (field_pair_ordinal >= 0),
    raw_hash_text TEXT NOT NULL,
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('resolved_all_locales','partial_missing','ambiguous')),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    PRIMARY KEY (owner_id, ingest_run_id),
    FOREIGN KEY (row_occurrence_id, ingest_run_id) REFERENCES material_config_archive_row_occurrence(occurrence_id, ingest_run_id)
);
CREATE INDEX idx_material_config_archive_text_owner_row
    ON material_config_archive_text_owner_occurrence (row_occurrence_id, ingest_run_id, field_name, field_pair_ordinal);

CREATE TABLE material_config_archive_text_resolution_occurrence (
    resolution_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    locale TEXT NOT NULL CHECK (locale IN ('CHS','EN','JP')),
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('resolved_unique','missing','ambiguous')),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    PRIMARY KEY (resolution_id, ingest_run_id),
    UNIQUE (owner_id, ingest_run_id, locale),
    FOREIGN KEY (owner_id, ingest_run_id) REFERENCES material_config_archive_text_owner_occurrence(owner_id, ingest_run_id)
);
CREATE INDEX idx_material_config_archive_text_resolution_owner
    ON material_config_archive_text_resolution_occurrence (owner_id, ingest_run_id, locale);

CREATE TABLE material_config_archive_text_candidate_occurrence (
    candidate_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    textmap_snapshot_id TEXT NOT NULL,
    textmap_parser_build_id TEXT NOT NULL,
    textmap_ingest_run_id TEXT NOT NULL,
    source_record_pointer TEXT NOT NULL CHECK(source_record_pointer='/'),
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
    FOREIGN KEY (owner_id, ingest_run_id) REFERENCES material_config_archive_text_owner_occurrence(owner_id, ingest_run_id),
    FOREIGN KEY(textmap_ingest_run_id,textmap_snapshot_id,textmap_parser_build_id) REFERENCES ingest_run(id,snapshot_id,parser_build_id)
);
CREATE INDEX idx_material_config_archive_text_candidate_owner
    ON material_config_archive_text_candidate_occurrence (owner_id, ingest_run_id, locale, relative_path, pair_ordinal, candidate_id);

CREATE TRIGGER trg_material_config_archive_text_owner_occurrence_immutable_insert
BEFORE INSERT ON material_config_archive_text_owner_occurrence
WHEN EXISTS (SELECT 1 FROM material_config_archive_text_owner_occurrence WHERE owner_id=NEW.owner_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'material_config_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_material_config_archive_text_owner_occurrence_immutable_update
BEFORE UPDATE ON material_config_archive_text_owner_occurrence
BEGIN
    SELECT RAISE(ABORT, 'material_config_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_material_config_archive_text_owner_occurrence_immutable_delete
BEFORE DELETE ON material_config_archive_text_owner_occurrence
BEGIN
    SELECT RAISE(ABORT, 'material_config_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_material_config_archive_text_resolution_occurrence_immutable_insert
BEFORE INSERT ON material_config_archive_text_resolution_occurrence
WHEN EXISTS (SELECT 1 FROM material_config_archive_text_resolution_occurrence WHERE resolution_id=NEW.resolution_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'material_config_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_material_config_archive_text_resolution_occurrence_immutable_update
BEFORE UPDATE ON material_config_archive_text_resolution_occurrence
BEGIN
    SELECT RAISE(ABORT, 'material_config_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_material_config_archive_text_resolution_occurrence_immutable_delete
BEFORE DELETE ON material_config_archive_text_resolution_occurrence
BEGIN
    SELECT RAISE(ABORT, 'material_config_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_material_config_archive_text_candidate_occurrence_immutable_insert
BEFORE INSERT ON material_config_archive_text_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM material_config_archive_text_candidate_occurrence WHERE candidate_id=NEW.candidate_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'material_config_archive text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_material_config_archive_text_candidate_occurrence_immutable_update
BEFORE UPDATE ON material_config_archive_text_candidate_occurrence
BEGIN
    SELECT RAISE(ABORT, 'material_config_archive text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_material_config_archive_text_candidate_occurrence_immutable_delete
BEFORE DELETE ON material_config_archive_text_candidate_occurrence
BEGIN
    SELECT RAISE(ABORT, 'material_config_archive text_candidate_occurrence evidence is immutable');
END;

CREATE UNIQUE INDEX idx_monster_archive_row_occurrence_run
    ON monster_archive_row_occurrence (occurrence_id, ingest_run_id);
CREATE TRIGGER trg_monster_archive_row_immutable_insert_existing
BEFORE INSERT ON monster_archive_row_occurrence
WHEN EXISTS (SELECT 1 FROM monster_archive_row_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'monster_archive row evidence is immutable');
END;

CREATE TABLE monster_archive_text_owner_occurrence (
    owner_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL REFERENCES ingest_run(id),
    row_occurrence_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    field_pair_ordinal INTEGER NOT NULL CHECK (field_pair_ordinal >= 0),
    raw_hash_text TEXT NOT NULL,
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('resolved_all_locales','partial_missing','ambiguous')),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    PRIMARY KEY (owner_id, ingest_run_id),
    FOREIGN KEY (row_occurrence_id, ingest_run_id) REFERENCES monster_archive_row_occurrence(occurrence_id, ingest_run_id)
);
CREATE INDEX idx_monster_archive_text_owner_row
    ON monster_archive_text_owner_occurrence (row_occurrence_id, ingest_run_id, field_name, field_pair_ordinal);

CREATE TABLE monster_archive_text_resolution_occurrence (
    resolution_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    locale TEXT NOT NULL CHECK (locale IN ('CHS','EN','JP')),
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('resolved_unique','missing','ambiguous')),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    PRIMARY KEY (resolution_id, ingest_run_id),
    UNIQUE (owner_id, ingest_run_id, locale),
    FOREIGN KEY (owner_id, ingest_run_id) REFERENCES monster_archive_text_owner_occurrence(owner_id, ingest_run_id)
);
CREATE INDEX idx_monster_archive_text_resolution_owner
    ON monster_archive_text_resolution_occurrence (owner_id, ingest_run_id, locale);

CREATE TABLE monster_archive_text_candidate_occurrence (
    candidate_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    textmap_snapshot_id TEXT NOT NULL,
    textmap_parser_build_id TEXT NOT NULL,
    textmap_ingest_run_id TEXT NOT NULL,
    source_record_pointer TEXT NOT NULL CHECK(source_record_pointer='/'),
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
    FOREIGN KEY (owner_id, ingest_run_id) REFERENCES monster_archive_text_owner_occurrence(owner_id, ingest_run_id),
    FOREIGN KEY(textmap_ingest_run_id,textmap_snapshot_id,textmap_parser_build_id) REFERENCES ingest_run(id,snapshot_id,parser_build_id)
);
CREATE INDEX idx_monster_archive_text_candidate_owner
    ON monster_archive_text_candidate_occurrence (owner_id, ingest_run_id, locale, relative_path, pair_ordinal, candidate_id);

CREATE TRIGGER trg_monster_archive_text_owner_occurrence_immutable_insert
BEFORE INSERT ON monster_archive_text_owner_occurrence
WHEN EXISTS (SELECT 1 FROM monster_archive_text_owner_occurrence WHERE owner_id=NEW.owner_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'monster_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_monster_archive_text_owner_occurrence_immutable_update
BEFORE UPDATE ON monster_archive_text_owner_occurrence
BEGIN
    SELECT RAISE(ABORT, 'monster_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_monster_archive_text_owner_occurrence_immutable_delete
BEFORE DELETE ON monster_archive_text_owner_occurrence
BEGIN
    SELECT RAISE(ABORT, 'monster_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_monster_archive_text_resolution_occurrence_immutable_insert
BEFORE INSERT ON monster_archive_text_resolution_occurrence
WHEN EXISTS (SELECT 1 FROM monster_archive_text_resolution_occurrence WHERE resolution_id=NEW.resolution_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'monster_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_monster_archive_text_resolution_occurrence_immutable_update
BEFORE UPDATE ON monster_archive_text_resolution_occurrence
BEGIN
    SELECT RAISE(ABORT, 'monster_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_monster_archive_text_resolution_occurrence_immutable_delete
BEFORE DELETE ON monster_archive_text_resolution_occurrence
BEGIN
    SELECT RAISE(ABORT, 'monster_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_monster_archive_text_candidate_occurrence_immutable_insert
BEFORE INSERT ON monster_archive_text_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM monster_archive_text_candidate_occurrence WHERE candidate_id=NEW.candidate_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'monster_archive text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_monster_archive_text_candidate_occurrence_immutable_update
BEFORE UPDATE ON monster_archive_text_candidate_occurrence
BEGIN
    SELECT RAISE(ABORT, 'monster_archive text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_monster_archive_text_candidate_occurrence_immutable_delete
BEFORE DELETE ON monster_archive_text_candidate_occurrence
BEGIN
    SELECT RAISE(ABORT, 'monster_archive text_candidate_occurrence evidence is immutable');
END;

CREATE UNIQUE INDEX idx_reliquary_config_archive_row_occurrence_run
    ON reliquary_config_archive_row_occurrence (occurrence_id, ingest_run_id);
CREATE TRIGGER trg_reliquary_config_archive_row_immutable_insert_existing
BEFORE INSERT ON reliquary_config_archive_row_occurrence
WHEN EXISTS (SELECT 1 FROM reliquary_config_archive_row_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'reliquary_config_archive row evidence is immutable');
END;

CREATE TABLE reliquary_config_archive_text_owner_occurrence (
    owner_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL REFERENCES ingest_run(id),
    row_occurrence_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    field_pair_ordinal INTEGER NOT NULL CHECK (field_pair_ordinal >= 0),
    raw_hash_text TEXT NOT NULL,
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('resolved_all_locales','partial_missing','ambiguous')),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    PRIMARY KEY (owner_id, ingest_run_id),
    FOREIGN KEY (row_occurrence_id, ingest_run_id) REFERENCES reliquary_config_archive_row_occurrence(occurrence_id, ingest_run_id)
);
CREATE INDEX idx_reliquary_config_archive_text_owner_row
    ON reliquary_config_archive_text_owner_occurrence (row_occurrence_id, ingest_run_id, field_name, field_pair_ordinal);

CREATE TABLE reliquary_config_archive_text_resolution_occurrence (
    resolution_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    locale TEXT NOT NULL CHECK (locale IN ('CHS','EN','JP')),
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('resolved_unique','missing','ambiguous')),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    PRIMARY KEY (resolution_id, ingest_run_id),
    UNIQUE (owner_id, ingest_run_id, locale),
    FOREIGN KEY (owner_id, ingest_run_id) REFERENCES reliquary_config_archive_text_owner_occurrence(owner_id, ingest_run_id)
);
CREATE INDEX idx_reliquary_config_archive_text_resolution_owner
    ON reliquary_config_archive_text_resolution_occurrence (owner_id, ingest_run_id, locale);

CREATE TABLE reliquary_config_archive_text_candidate_occurrence (
    candidate_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    textmap_snapshot_id TEXT NOT NULL,
    textmap_parser_build_id TEXT NOT NULL,
    textmap_ingest_run_id TEXT NOT NULL,
    source_record_pointer TEXT NOT NULL CHECK(source_record_pointer='/'),
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
    FOREIGN KEY (owner_id, ingest_run_id) REFERENCES reliquary_config_archive_text_owner_occurrence(owner_id, ingest_run_id),
    FOREIGN KEY(textmap_ingest_run_id,textmap_snapshot_id,textmap_parser_build_id) REFERENCES ingest_run(id,snapshot_id,parser_build_id)
);
CREATE INDEX idx_reliquary_config_archive_text_candidate_owner
    ON reliquary_config_archive_text_candidate_occurrence (owner_id, ingest_run_id, locale, relative_path, pair_ordinal, candidate_id);

CREATE TRIGGER trg_reliquary_config_archive_text_owner_occurrence_immutable_insert
BEFORE INSERT ON reliquary_config_archive_text_owner_occurrence
WHEN EXISTS (SELECT 1 FROM reliquary_config_archive_text_owner_occurrence WHERE owner_id=NEW.owner_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'reliquary_config_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_reliquary_config_archive_text_owner_occurrence_immutable_update
BEFORE UPDATE ON reliquary_config_archive_text_owner_occurrence
BEGIN
    SELECT RAISE(ABORT, 'reliquary_config_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_reliquary_config_archive_text_owner_occurrence_immutable_delete
BEFORE DELETE ON reliquary_config_archive_text_owner_occurrence
BEGIN
    SELECT RAISE(ABORT, 'reliquary_config_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_reliquary_config_archive_text_resolution_occurrence_immutable_insert
BEFORE INSERT ON reliquary_config_archive_text_resolution_occurrence
WHEN EXISTS (SELECT 1 FROM reliquary_config_archive_text_resolution_occurrence WHERE resolution_id=NEW.resolution_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'reliquary_config_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_reliquary_config_archive_text_resolution_occurrence_immutable_update
BEFORE UPDATE ON reliquary_config_archive_text_resolution_occurrence
BEGIN
    SELECT RAISE(ABORT, 'reliquary_config_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_reliquary_config_archive_text_resolution_occurrence_immutable_delete
BEFORE DELETE ON reliquary_config_archive_text_resolution_occurrence
BEGIN
    SELECT RAISE(ABORT, 'reliquary_config_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_reliquary_config_archive_text_candidate_occurrence_immutable_insert
BEFORE INSERT ON reliquary_config_archive_text_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM reliquary_config_archive_text_candidate_occurrence WHERE candidate_id=NEW.candidate_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'reliquary_config_archive text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_reliquary_config_archive_text_candidate_occurrence_immutable_update
BEFORE UPDATE ON reliquary_config_archive_text_candidate_occurrence
BEGIN
    SELECT RAISE(ABORT, 'reliquary_config_archive text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_reliquary_config_archive_text_candidate_occurrence_immutable_delete
BEFORE DELETE ON reliquary_config_archive_text_candidate_occurrence
BEGIN
    SELECT RAISE(ABORT, 'reliquary_config_archive text_candidate_occurrence evidence is immutable');
END;

CREATE UNIQUE INDEX idx_weapon_config_archive_row_occurrence_run
    ON weapon_config_archive_row_occurrence (occurrence_id, ingest_run_id);
CREATE TRIGGER trg_weapon_config_archive_row_immutable_insert_existing
BEFORE INSERT ON weapon_config_archive_row_occurrence
WHEN EXISTS (SELECT 1 FROM weapon_config_archive_row_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'weapon_config_archive row evidence is immutable');
END;

CREATE TABLE weapon_config_archive_text_owner_occurrence (
    owner_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL REFERENCES ingest_run(id),
    row_occurrence_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    field_pair_ordinal INTEGER NOT NULL CHECK (field_pair_ordinal >= 0),
    raw_hash_text TEXT NOT NULL,
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('resolved_all_locales','partial_missing','ambiguous')),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    PRIMARY KEY (owner_id, ingest_run_id),
    FOREIGN KEY (row_occurrence_id, ingest_run_id) REFERENCES weapon_config_archive_row_occurrence(occurrence_id, ingest_run_id)
);
CREATE INDEX idx_weapon_config_archive_text_owner_row
    ON weapon_config_archive_text_owner_occurrence (row_occurrence_id, ingest_run_id, field_name, field_pair_ordinal);

CREATE TABLE weapon_config_archive_text_resolution_occurrence (
    resolution_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    locale TEXT NOT NULL CHECK (locale IN ('CHS','EN','JP')),
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('resolved_unique','missing','ambiguous')),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    PRIMARY KEY (resolution_id, ingest_run_id),
    UNIQUE (owner_id, ingest_run_id, locale),
    FOREIGN KEY (owner_id, ingest_run_id) REFERENCES weapon_config_archive_text_owner_occurrence(owner_id, ingest_run_id)
);
CREATE INDEX idx_weapon_config_archive_text_resolution_owner
    ON weapon_config_archive_text_resolution_occurrence (owner_id, ingest_run_id, locale);

CREATE TABLE weapon_config_archive_text_candidate_occurrence (
    candidate_id TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    textmap_snapshot_id TEXT NOT NULL,
    textmap_parser_build_id TEXT NOT NULL,
    textmap_ingest_run_id TEXT NOT NULL,
    source_record_pointer TEXT NOT NULL CHECK(source_record_pointer='/'),
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
    FOREIGN KEY (owner_id, ingest_run_id) REFERENCES weapon_config_archive_text_owner_occurrence(owner_id, ingest_run_id),
    FOREIGN KEY(textmap_ingest_run_id,textmap_snapshot_id,textmap_parser_build_id) REFERENCES ingest_run(id,snapshot_id,parser_build_id)
);
CREATE INDEX idx_weapon_config_archive_text_candidate_owner
    ON weapon_config_archive_text_candidate_occurrence (owner_id, ingest_run_id, locale, relative_path, pair_ordinal, candidate_id);

CREATE TRIGGER trg_weapon_config_archive_text_owner_occurrence_immutable_insert
BEFORE INSERT ON weapon_config_archive_text_owner_occurrence
WHEN EXISTS (SELECT 1 FROM weapon_config_archive_text_owner_occurrence WHERE owner_id=NEW.owner_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'weapon_config_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_weapon_config_archive_text_owner_occurrence_immutable_update
BEFORE UPDATE ON weapon_config_archive_text_owner_occurrence
BEGIN
    SELECT RAISE(ABORT, 'weapon_config_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_weapon_config_archive_text_owner_occurrence_immutable_delete
BEFORE DELETE ON weapon_config_archive_text_owner_occurrence
BEGIN
    SELECT RAISE(ABORT, 'weapon_config_archive text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_weapon_config_archive_text_resolution_occurrence_immutable_insert
BEFORE INSERT ON weapon_config_archive_text_resolution_occurrence
WHEN EXISTS (SELECT 1 FROM weapon_config_archive_text_resolution_occurrence WHERE resolution_id=NEW.resolution_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'weapon_config_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_weapon_config_archive_text_resolution_occurrence_immutable_update
BEFORE UPDATE ON weapon_config_archive_text_resolution_occurrence
BEGIN
    SELECT RAISE(ABORT, 'weapon_config_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_weapon_config_archive_text_resolution_occurrence_immutable_delete
BEFORE DELETE ON weapon_config_archive_text_resolution_occurrence
BEGIN
    SELECT RAISE(ABORT, 'weapon_config_archive text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_weapon_config_archive_text_candidate_occurrence_immutable_insert
BEFORE INSERT ON weapon_config_archive_text_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM weapon_config_archive_text_candidate_occurrence WHERE candidate_id=NEW.candidate_id AND ingest_run_id=NEW.ingest_run_id)
BEGIN
    SELECT RAISE(ABORT, 'weapon_config_archive text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_weapon_config_archive_text_candidate_occurrence_immutable_update
BEFORE UPDATE ON weapon_config_archive_text_candidate_occurrence
BEGIN
    SELECT RAISE(ABORT, 'weapon_config_archive text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_weapon_config_archive_text_candidate_occurrence_immutable_delete
BEFORE DELETE ON weapon_config_archive_text_candidate_occurrence
BEGIN
    SELECT RAISE(ABORT, 'weapon_config_archive text_candidate_occurrence evidence is immutable');
END;

