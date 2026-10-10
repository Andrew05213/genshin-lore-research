PRAGMA foreign_keys = ON;

CREATE UNIQUE INDEX IF NOT EXISTS uq_passage_occurrence_scope
ON passage_occurrence(
    passage_occurrence_id, ingest_run_id, evidence_container_id
);

CREATE TABLE matcher_build (
    id TEXT PRIMARY KEY CONSTRAINT pk_matcher_build,
    matcher_kind TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    config_checksum TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    CONSTRAINT uq_matcher_build_spec
        UNIQUE (matcher_kind, algorithm_version, config_checksum)
);

CREATE TABLE compare_run (
    id TEXT PRIMARY KEY CONSTRAINT pk_compare_run,
    matcher_build_id TEXT NOT NULL,
    family TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'staged',
    started_at TEXT,
    completed_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    CONSTRAINT fk_compare_run_matcher_build
        FOREIGN KEY (matcher_build_id) REFERENCES matcher_build(id),
    CONSTRAINT ck_compare_run_family CHECK (length(trim(family)) > 0),
    CONSTRAINT ck_compare_run_status CHECK (
        status IN ('staged', 'running', 'complete', 'partial',
                   'quarantined', 'failed')
    )
);

CREATE TABLE comparison_side (
    compare_run_id TEXT NOT NULL,
    side TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    CONSTRAINT pk_comparison_side PRIMARY KEY (compare_run_id, side),
    CONSTRAINT uq_comparison_side_identity
        UNIQUE (compare_run_id, side, ingest_run_id),
    CONSTRAINT fk_comparison_side_compare_run
        FOREIGN KEY (compare_run_id) REFERENCES compare_run(id),
    CONSTRAINT fk_comparison_side_ingest_run
        FOREIGN KEY (ingest_run_id) REFERENCES ingest_run(id),
    CONSTRAINT ck_comparison_side_name CHECK (side IN ('baseline', 'target'))
);

CREATE TABLE comparison_scope_proof (
    compare_run_id TEXT NOT NULL,
    side TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    family TEXT NOT NULL DEFAULT '',
    scope_key TEXT NOT NULL DEFAULT '',
    scope_json TEXT NOT NULL DEFAULT '{}',
    inventory_manifest_id TEXT NOT NULL DEFAULT '',
    inventory_manifest_checksum TEXT NOT NULL DEFAULT '',
    coverage_proof_id TEXT NOT NULL DEFAULT '',
    coverage_proof_checksum TEXT NOT NULL DEFAULT '',
    proof_algorithm_version TEXT NOT NULL DEFAULT '',
    inventory_complete INTEGER NOT NULL DEFAULT 0,
    expected_count INTEGER NOT NULL DEFAULT 0,
    observed_count INTEGER NOT NULL DEFAULT 0,
    parser_status TEXT NOT NULL DEFAULT 'unknown',
    extraction_status TEXT NOT NULL DEFAULT 'unknown',
    coverage_status TEXT NOT NULL DEFAULT 'unknown',
    proof_status TEXT NOT NULL DEFAULT 'incomplete',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    CONSTRAINT pk_comparison_scope_proof PRIMARY KEY (compare_run_id, side),
    CONSTRAINT uq_comparison_scope_identity
        UNIQUE (compare_run_id, side, scope_key,
                inventory_manifest_checksum),
    CONSTRAINT fk_scope_proof_side
        FOREIGN KEY (compare_run_id, side, ingest_run_id)
        REFERENCES comparison_side(
            compare_run_id, side, ingest_run_id
        ),
    CONSTRAINT ck_scope_side_name CHECK (side IN ('baseline', 'target')),
    CONSTRAINT ck_scope_family_nonempty CHECK (length(trim(family)) > 0),
    CONSTRAINT ck_scope_inventory_bool CHECK (
        typeof(inventory_complete) = 'integer'
        AND inventory_complete IN (0, 1)
    ),
    CONSTRAINT ck_scope_counts_nonnegative CHECK (
        typeof(expected_count) = 'integer' AND expected_count >= 0
        AND typeof(observed_count) = 'integer' AND observed_count >= 0
    ),
    CONSTRAINT ck_scope_parser_status CHECK (
        parser_status IN ('passed', 'partial', 'failed',
                          'quarantined', 'unknown')
    ),
    CONSTRAINT ck_scope_extraction_status CHECK (
        extraction_status IN ('complete', 'partial', 'failed',
                              'quarantined', 'unknown')
    ),
    CONSTRAINT ck_scope_coverage_status CHECK (
        coverage_status IN ('complete', 'partial', 'failed', 'unknown')
    ),
    CONSTRAINT ck_scope_proof_status CHECK (
        proof_status IN ('verified', 'incomplete',
                         'quarantined', 'failed')
    )
);

CREATE TABLE comparison_match_set (
    id TEXT NOT NULL,
    compare_run_id TEXT NOT NULL,
    resolution_status TEXT NOT NULL,
    method TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    justification TEXT NOT NULL,
    provenance TEXT NOT NULL,
    CONSTRAINT pk_comparison_match_set PRIMARY KEY (id, compare_run_id),
    CONSTRAINT fk_match_set_compare_run
        FOREIGN KEY (compare_run_id) REFERENCES compare_run(id),
    CONSTRAINT ck_match_set_resolution_status CHECK (
        resolution_status IN ('unresolved', 'ambiguous', 'resolved',
                              'rejected', 'not_comparable')
    )
);

CREATE TABLE comparison_match_anchor (
    match_set_id TEXT NOT NULL,
    compare_run_id TEXT NOT NULL,
    side TEXT NOT NULL,
    ingest_run_id TEXT NOT NULL,
    passage_occurrence_id TEXT NOT NULL,
    evidence_container_id TEXT NOT NULL,
    CONSTRAINT pk_comparison_match_anchor PRIMARY KEY (
        match_set_id, compare_run_id, side,
        passage_occurrence_id, evidence_container_id
    ),
    CONSTRAINT fk_match_anchor_match_set
        FOREIGN KEY (match_set_id, compare_run_id)
        REFERENCES comparison_match_set(id, compare_run_id),
    CONSTRAINT fk_match_anchor_side_run
        FOREIGN KEY (compare_run_id, side, ingest_run_id)
        REFERENCES comparison_side(
            compare_run_id, side, ingest_run_id
        ),
    CONSTRAINT fk_match_anchor_occurrence
        FOREIGN KEY (
            passage_occurrence_id, ingest_run_id, evidence_container_id
        ) REFERENCES passage_occurrence(
            passage_occurrence_id, ingest_run_id, evidence_container_id
        ),
    CONSTRAINT ck_match_anchor_side CHECK (side IN ('baseline', 'target'))
);

CREATE TABLE comparison_match_candidate (
    id TEXT NOT NULL,
    match_set_id TEXT NOT NULL,
    compare_run_id TEXT NOT NULL,
    candidate_kind TEXT NOT NULL,
    baseline_passage_occurrence_id TEXT,
    baseline_ingest_run_id TEXT,
    baseline_evidence_container_id TEXT,
    target_passage_occurrence_id TEXT,
    target_ingest_run_id TEXT,
    target_evidence_container_id TEXT,
    rank INTEGER,
    confidence REAL,
    features_json TEXT NOT NULL DEFAULT '{}',
    justification TEXT NOT NULL,
    provenance TEXT NOT NULL,
    CONSTRAINT pk_comparison_match_candidate
        PRIMARY KEY (id, match_set_id, compare_run_id),
    CONSTRAINT fk_match_candidate_match_set
        FOREIGN KEY (match_set_id, compare_run_id)
        REFERENCES comparison_match_set(id, compare_run_id),
    CONSTRAINT fk_match_candidate_baseline_occurrence
        FOREIGN KEY (
            baseline_passage_occurrence_id,
            baseline_ingest_run_id,
            baseline_evidence_container_id
        ) REFERENCES passage_occurrence(
            passage_occurrence_id, ingest_run_id, evidence_container_id
        ),
    CONSTRAINT fk_match_candidate_target_occurrence
        FOREIGN KEY (
            target_passage_occurrence_id,
            target_ingest_run_id,
            target_evidence_container_id
        ) REFERENCES passage_occurrence(
            passage_occurrence_id, ingest_run_id, evidence_container_id
        ),
    CONSTRAINT ck_match_candidate_kind CHECK (
        candidate_kind IN ('pair', 'baseline_only', 'target_only')
    ),
    CONSTRAINT ck_match_candidate_confidence CHECK (
        confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)
    ),
    CONSTRAINT ck_match_candidate_endpoint_shape CHECK (
        (
            candidate_kind = 'pair'
            AND baseline_passage_occurrence_id IS NOT NULL
            AND baseline_ingest_run_id IS NOT NULL
            AND baseline_evidence_container_id IS NOT NULL
            AND target_passage_occurrence_id IS NOT NULL
            AND target_ingest_run_id IS NOT NULL
            AND target_evidence_container_id IS NOT NULL
        )
        OR (
            candidate_kind = 'baseline_only'
            AND baseline_passage_occurrence_id IS NOT NULL
            AND baseline_ingest_run_id IS NOT NULL
            AND baseline_evidence_container_id IS NOT NULL
            AND target_passage_occurrence_id IS NULL
            AND target_ingest_run_id IS NULL
            AND target_evidence_container_id IS NULL
        )
        OR (
            candidate_kind = 'target_only'
            AND baseline_passage_occurrence_id IS NULL
            AND baseline_ingest_run_id IS NULL
            AND baseline_evidence_container_id IS NULL
            AND target_passage_occurrence_id IS NOT NULL
            AND target_ingest_run_id IS NOT NULL
            AND target_evidence_container_id IS NOT NULL
        )
    )
);

CREATE TABLE comparison_match_decision (
    id TEXT NOT NULL,
    match_set_id TEXT NOT NULL,
    compare_run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    justification TEXT NOT NULL,
    provenance TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CONSTRAINT pk_comparison_match_decision
        PRIMARY KEY (id, match_set_id, compare_run_id),
    CONSTRAINT uq_match_decision_candidate
        UNIQUE (candidate_id, match_set_id, compare_run_id),
    CONSTRAINT fk_match_decision_match_set
        FOREIGN KEY (match_set_id, compare_run_id)
        REFERENCES comparison_match_set(id, compare_run_id),
    CONSTRAINT fk_match_decision_candidate
        FOREIGN KEY (candidate_id, match_set_id, compare_run_id)
        REFERENCES comparison_match_candidate(
            id, match_set_id, compare_run_id
        ),
    CONSTRAINT ck_match_decision CHECK (
        decision IN ('accepted', 'rejected', 'deferred')
    )
);

CREATE TABLE comparison_absence_assertion (
    id TEXT NOT NULL,
    compare_run_id TEXT NOT NULL,
    match_set_id TEXT NOT NULL,
    missing_side TEXT NOT NULL,
    status TEXT NOT NULL,
    proof_artifact_id TEXT NOT NULL DEFAULT '',
    proof_artifact_checksum TEXT NOT NULL DEFAULT '',
    proof_algorithm TEXT NOT NULL DEFAULT '',
    selector TEXT NOT NULL DEFAULT '',
    provenance TEXT NOT NULL DEFAULT '',
    CONSTRAINT pk_comparison_absence_assertion
        PRIMARY KEY (id, compare_run_id, match_set_id, missing_side),
    CONSTRAINT uq_absence_match_side
        UNIQUE (compare_run_id, match_set_id, missing_side),
    CONSTRAINT fk_absence_match_set
        FOREIGN KEY (match_set_id, compare_run_id)
        REFERENCES comparison_match_set(id, compare_run_id),
    CONSTRAINT fk_absence_scope_proof
        FOREIGN KEY (compare_run_id, missing_side)
        REFERENCES comparison_scope_proof(compare_run_id, side),
    CONSTRAINT ck_absence_missing_side CHECK (
        missing_side IN ('baseline', 'target')
    ),
    CONSTRAINT ck_absence_status CHECK (
        status IN ('proven', 'not_proven', 'quarantined')
    ),
    CONSTRAINT ck_absence_shape CHECK (
        length(trim(proof_artifact_id)) > 0
        OR status <> 'proven'
    )
);

CREATE TABLE comparison_change_observation (
    id TEXT NOT NULL,
    compare_run_id TEXT NOT NULL,
    match_set_id TEXT NOT NULL,
    presence_outcome TEXT NOT NULL,
    observation_status TEXT NOT NULL,
    absence_assertion_id TEXT,
    absence_missing_side TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    provenance TEXT NOT NULL DEFAULT '',
    CONSTRAINT pk_comparison_change_observation
        PRIMARY KEY (id, compare_run_id, match_set_id),
    CONSTRAINT uq_change_observation_identity
        UNIQUE (compare_run_id, match_set_id),
    CONSTRAINT fk_change_observation_match_set
        FOREIGN KEY (match_set_id, compare_run_id)
        REFERENCES comparison_match_set(id, compare_run_id),
    CONSTRAINT fk_change_observation_absence
        FOREIGN KEY (
            absence_assertion_id, compare_run_id, match_set_id,
            absence_missing_side
        ) REFERENCES comparison_absence_assertion(
            id, compare_run_id, match_set_id, missing_side
        ),
    CONSTRAINT ck_change_presence_outcome CHECK (
        presence_outcome IN ('present_both', 'added', 'removed',
                             'unknown', 'not_comparable')
    ),
    CONSTRAINT ck_change_observation_status CHECK (
        observation_status IN ('observed', 'unknown',
                               'not_comparable', 'quarantined')
    ),
    CONSTRAINT ck_change_absence_shape CHECK (
        (absence_assertion_id IS NULL AND absence_missing_side IS NULL)
        OR (absence_assertion_id IS NOT NULL
            AND absence_missing_side IN ('baseline', 'target'))
    )
);

CREATE TABLE comparison_change_label (
    observation_id TEXT NOT NULL,
    compare_run_id TEXT NOT NULL,
    match_set_id TEXT NOT NULL,
    label TEXT NOT NULL,
    provenance TEXT NOT NULL DEFAULT '',
    CONSTRAINT pk_change_label PRIMARY KEY (
        observation_id, compare_run_id, match_set_id, label
    ),
    CONSTRAINT fk_change_label_observation
        FOREIGN KEY (observation_id, compare_run_id, match_set_id)
        REFERENCES comparison_change_observation(
            id, compare_run_id, match_set_id
        ),
    CONSTRAINT ck_change_label CHECK (
        label IN ('raw_changed', 'text_changed', 'metadata_changed',
                  'structure_changed', 'graph_changed', 'timing_changed',
                  'parser_delta', 'provenance_only', 'unknown')
    )
);

CREATE TRIGGER trg_matcher_build_immutable
BEFORE UPDATE ON matcher_build
BEGIN
    SELECT RAISE(ABORT, 'matcher_build is write-once');
END;

CREATE TRIGGER trg_matcher_build_delete_immutable
BEFORE DELETE ON matcher_build
BEGIN
    SELECT RAISE(ABORT, 'matcher_build is write-once');
END;

CREATE TRIGGER trg_compare_run_initial_status
BEFORE INSERT ON compare_run
WHEN NEW.status <> 'staged'
BEGIN
    SELECT RAISE(ABORT, 'compare_run must start staged');
END;

CREATE TRIGGER trg_compare_run_status_transition
BEFORE UPDATE OF status ON compare_run
WHEN NOT (
    NEW.status = OLD.status
    OR (OLD.status = 'staged'
        AND NEW.status IN ('running', 'quarantined', 'failed'))
    OR (OLD.status = 'running'
        AND NEW.status IN ('complete', 'partial',
                           'quarantined', 'failed'))
)
BEGIN
    SELECT RAISE(ABORT, 'invalid compare_run status transition');
END;

CREATE TRIGGER trg_compare_run_identity_immutable
BEFORE UPDATE ON compare_run
WHEN NEW.id IS NOT OLD.id
  OR NEW.matcher_build_id IS NOT OLD.matcher_build_id
  OR NEW.family IS NOT OLD.family
  OR NEW.started_at IS NOT OLD.started_at
  OR NEW.metadata_json IS NOT OLD.metadata_json
BEGIN
    SELECT RAISE(ABORT, 'compare_run identity is write-once');
END;

CREATE TRIGGER trg_compare_run_complete_gate
BEFORE UPDATE OF status ON compare_run
WHEN NEW.status = 'complete'
BEGIN
    SELECT CASE WHEN (
        SELECT COUNT(*) FROM comparison_side
        WHERE compare_run_id = NEW.id
    ) <> 2 THEN RAISE(ABORT, 'complete requires two sides') END;
    SELECT CASE WHEN (
        SELECT COUNT(DISTINCT ingest_run_id) FROM comparison_side
        WHERE compare_run_id = NEW.id
    ) <> 2 THEN RAISE(ABORT, 'sides require different ingest runs') END;
    SELECT CASE WHEN (
        SELECT COUNT(*) FROM comparison_scope_proof
        WHERE compare_run_id = NEW.id AND proof_status = 'verified'
    ) <> 2 THEN RAISE(ABORT, 'complete requires two verified proofs') END;
    SELECT CASE WHEN (
        SELECT COUNT(DISTINCT family) FROM comparison_scope_proof
        WHERE compare_run_id = NEW.id
    ) <> 1 THEN RAISE(ABORT, 'scope families must agree') END;
    SELECT CASE WHEN (
        SELECT MIN(family) FROM comparison_scope_proof
        WHERE compare_run_id = NEW.id
    ) <> NEW.family THEN RAISE(ABORT, 'scope family mismatch') END;
    SELECT CASE WHEN (
        SELECT COUNT(DISTINCT scope_key) FROM comparison_scope_proof
        WHERE compare_run_id = NEW.id
    ) <> 1 THEN RAISE(ABORT, 'scope keys must agree') END;
END;

CREATE TRIGGER trg_compare_run_terminal_freeze
BEFORE UPDATE ON compare_run
WHEN OLD.status IN ('complete', 'partial', 'quarantined', 'failed')
BEGIN
    SELECT RAISE(ABORT, 'terminal compare_run is immutable');
END;

CREATE TRIGGER trg_compare_run_terminal_delete
BEFORE DELETE ON compare_run
WHEN OLD.status IN ('complete', 'partial', 'quarantined', 'failed')
BEGIN
    SELECT RAISE(ABORT, 'terminal compare_run cannot be deleted');
END;

CREATE TRIGGER trg_comparison_side_write_phase
BEFORE INSERT ON comparison_side
BEGIN
    SELECT CASE WHEN (
        SELECT status FROM compare_run WHERE id = NEW.compare_run_id
    ) <> 'staged' THEN RAISE(ABORT, 'side insert requires staged run') END;
END;

CREATE TRIGGER trg_comparison_side_distinct_ingest
BEFORE INSERT ON comparison_side
WHEN EXISTS (
    SELECT 1 FROM comparison_side
    WHERE compare_run_id = NEW.compare_run_id
      AND ingest_run_id = NEW.ingest_run_id
      AND side <> NEW.side
)
BEGIN
    SELECT RAISE(ABORT, 'baseline and target need different ingest runs');
END;

CREATE TRIGGER trg_comparison_side_immutable
BEFORE UPDATE ON comparison_side
BEGIN
    SELECT RAISE(ABORT, 'comparison side is write-once');
END;

CREATE TRIGGER trg_comparison_side_delete_immutable
BEFORE DELETE ON comparison_side
BEGIN
    SELECT RAISE(ABORT, 'comparison side is write-once');
END;

CREATE TRIGGER trg_scope_proof_write_phase
BEFORE INSERT ON comparison_scope_proof
BEGIN
    SELECT CASE WHEN (
        SELECT status FROM compare_run WHERE id = NEW.compare_run_id
    ) <> 'staged' THEN RAISE(ABORT, 'proof insert requires staged run') END;
END;

CREATE TRIGGER trg_scope_proof_verified_gate
BEFORE INSERT ON comparison_scope_proof
WHEN NEW.proof_status = 'verified'
BEGIN
    SELECT CASE WHEN NEW.inventory_complete <> 1
        OR NEW.expected_count <> NEW.observed_count
        OR NEW.parser_status <> 'passed'
        OR NEW.extraction_status <> 'complete'
        OR NEW.coverage_status <> 'complete'
        OR length(trim(NEW.family)) = 0
        OR length(trim(NEW.scope_key)) = 0
        OR length(trim(NEW.inventory_manifest_id)) = 0
        OR length(trim(NEW.inventory_manifest_checksum)) = 0
        OR length(trim(NEW.coverage_proof_id)) = 0
        OR length(trim(NEW.coverage_proof_checksum)) = 0
        OR length(trim(NEW.proof_algorithm_version)) = 0
        THEN RAISE(ABORT, 'verified proof is incomplete') END;
END;

CREATE TRIGGER trg_scope_proof_immutable
BEFORE UPDATE ON comparison_scope_proof
BEGIN
    SELECT RAISE(ABORT, 'scope proof is write-once');
END;

CREATE TRIGGER trg_scope_proof_delete_immutable
BEFORE DELETE ON comparison_scope_proof
BEGIN
    SELECT RAISE(ABORT, 'scope proof is write-once');
END;

CREATE TRIGGER trg_match_set_write_phase
BEFORE INSERT ON comparison_match_set
BEGIN
    SELECT CASE WHEN (
        SELECT status FROM compare_run WHERE id = NEW.compare_run_id
    ) <> 'running' THEN RAISE(ABORT, 'match set requires running run') END;
END;

CREATE TRIGGER trg_match_set_immutable
BEFORE UPDATE ON comparison_match_set
BEGIN
    SELECT RAISE(ABORT, 'match set is write-once');
END;

CREATE TRIGGER trg_match_set_delete_immutable
BEFORE DELETE ON comparison_match_set
BEGIN
    SELECT RAISE(ABORT, 'match set is write-once');
END;

CREATE TRIGGER trg_match_anchor_write_phase
BEFORE INSERT ON comparison_match_anchor
BEGIN
    SELECT CASE WHEN (
        SELECT cr.status FROM compare_run AS cr
        WHERE cr.id = NEW.compare_run_id
    ) <> 'running' THEN RAISE(ABORT, 'anchor requires running run') END;
END;

CREATE TRIGGER trg_match_anchor_immutable
BEFORE UPDATE ON comparison_match_anchor
BEGIN
    SELECT RAISE(ABORT, 'match anchor is write-once');
END;

CREATE TRIGGER trg_match_anchor_delete_immutable
BEFORE DELETE ON comparison_match_anchor
BEGIN
    SELECT RAISE(ABORT, 'match anchor is write-once');
END;

CREATE TRIGGER trg_match_candidate_write_phase
BEFORE INSERT ON comparison_match_candidate
BEGIN
    SELECT CASE WHEN (
        SELECT status FROM compare_run WHERE id = NEW.compare_run_id
    ) <> 'running' THEN RAISE(ABORT, 'candidate requires running run') END;
END;

CREATE TRIGGER trg_match_candidate_insert_side_binding
BEFORE INSERT ON comparison_match_candidate
BEGIN
    SELECT CASE WHEN NEW.candidate_kind IN ('pair', 'baseline_only')
      AND NOT EXISTS (
        SELECT 1 FROM comparison_side
        WHERE compare_run_id = NEW.compare_run_id
          AND side = 'baseline'
          AND ingest_run_id = NEW.baseline_ingest_run_id
      ) THEN RAISE(ABORT, 'baseline endpoint is on wrong side') END;
    SELECT CASE WHEN NEW.candidate_kind IN ('pair', 'target_only')
      AND NOT EXISTS (
        SELECT 1 FROM comparison_side
        WHERE compare_run_id = NEW.compare_run_id
          AND side = 'target'
          AND ingest_run_id = NEW.target_ingest_run_id
      ) THEN RAISE(ABORT, 'target endpoint is on wrong side') END;
END;

CREATE TRIGGER trg_match_candidate_update_side_binding
BEFORE UPDATE ON comparison_match_candidate
BEGIN
    SELECT CASE WHEN NEW.candidate_kind IN ('pair', 'baseline_only')
      AND NOT EXISTS (
        SELECT 1 FROM comparison_side
        WHERE compare_run_id = NEW.compare_run_id
          AND side = 'baseline'
          AND ingest_run_id = NEW.baseline_ingest_run_id
      ) THEN RAISE(ABORT, 'baseline endpoint is on wrong side') END;
    SELECT CASE WHEN NEW.candidate_kind IN ('pair', 'target_only')
      AND NOT EXISTS (
        SELECT 1 FROM comparison_side
        WHERE compare_run_id = NEW.compare_run_id
          AND side = 'target'
          AND ingest_run_id = NEW.target_ingest_run_id
      ) THEN RAISE(ABORT, 'target endpoint is on wrong side') END;
END;

CREATE TRIGGER trg_match_candidate_immutable
BEFORE UPDATE ON comparison_match_candidate
BEGIN
    SELECT RAISE(ABORT, 'candidate is write-once');
END;

CREATE TRIGGER trg_match_candidate_delete_immutable
BEFORE DELETE ON comparison_match_candidate
BEGIN
    SELECT RAISE(ABORT, 'candidate is write-once');
END;

CREATE TRIGGER trg_match_decision_write_phase
BEFORE INSERT ON comparison_match_decision
BEGIN
    SELECT CASE WHEN (
        SELECT status FROM compare_run WHERE id = NEW.compare_run_id
    ) <> 'running' THEN RAISE(ABORT, 'decision requires running run') END;
END;

CREATE TRIGGER trg_match_decision_immutable
BEFORE UPDATE ON comparison_match_decision
BEGIN
    SELECT RAISE(ABORT, 'decision is write-once');
END;

CREATE TRIGGER trg_match_decision_delete_immutable
BEFORE DELETE ON comparison_match_decision
BEGIN
    SELECT RAISE(ABORT, 'decision is write-once');
END;

CREATE TRIGGER trg_absence_write_phase
BEFORE INSERT ON comparison_absence_assertion
BEGIN
    SELECT CASE WHEN (
        SELECT status FROM compare_run WHERE id = NEW.compare_run_id
    ) <> 'running' THEN RAISE(ABORT, 'absence requires running run') END;
END;

CREATE TRIGGER trg_absence_proven_gate
BEFORE INSERT ON comparison_absence_assertion
WHEN NEW.status = 'proven'
BEGIN
    SELECT CASE WHEN length(trim(NEW.proof_artifact_id)) = 0
        OR length(trim(NEW.proof_artifact_checksum)) = 0
        OR length(trim(NEW.proof_algorithm)) = 0
        OR length(trim(NEW.selector)) = 0
        OR length(trim(NEW.provenance)) = 0
        THEN RAISE(ABORT, 'proven absence needs positive proof') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM comparison_scope_proof
        WHERE compare_run_id = NEW.compare_run_id
          AND side = NEW.missing_side
          AND proof_status = 'verified'
    ) THEN RAISE(ABORT, 'absence needs verified missing-side proof') END;
END;

CREATE TRIGGER trg_absence_immutable
BEFORE UPDATE ON comparison_absence_assertion
BEGIN
    SELECT RAISE(ABORT, 'absence assertion is write-once');
END;

CREATE TRIGGER trg_absence_delete_immutable
BEFORE DELETE ON comparison_absence_assertion
BEGIN
    SELECT RAISE(ABORT, 'absence assertion is write-once');
END;

CREATE TRIGGER trg_change_observation_write_phase
BEFORE INSERT ON comparison_change_observation
BEGIN
    SELECT CASE WHEN (
        SELECT status FROM compare_run WHERE id = NEW.compare_run_id
    ) <> 'running' THEN RAISE(ABORT, 'observation requires running run') END;
END;

CREATE TRIGGER trg_change_observation_presence_gate
BEFORE INSERT ON comparison_change_observation
WHEN NEW.presence_outcome IN ('present_both', 'added', 'removed')
BEGIN
    SELECT CASE WHEN NEW.observation_status <> 'observed'
        THEN RAISE(ABORT, 'promotion observation must be observed') END;
    SELECT CASE WHEN COALESCE((
        SELECT resolution_status FROM comparison_match_set
        WHERE id = NEW.match_set_id AND compare_run_id = NEW.compare_run_id
    ), '') <> 'resolved'
        THEN RAISE(ABORT, 'promotion requires resolved match set') END;
    SELECT CASE WHEN NEW.presence_outcome IN ('present_both', 'added', 'removed')
      AND (
        (SELECT COUNT(*) FROM comparison_scope_proof
         WHERE compare_run_id = NEW.compare_run_id
           AND proof_status = 'verified') <> 2
        OR (SELECT COUNT(DISTINCT family) FROM comparison_scope_proof
            WHERE compare_run_id = NEW.compare_run_id) <> 1
        OR (SELECT COUNT(DISTINCT scope_key) FROM comparison_scope_proof
            WHERE compare_run_id = NEW.compare_run_id) <> 1
        OR (SELECT MIN(family) FROM comparison_scope_proof
            WHERE compare_run_id = NEW.compare_run_id)
           <> (SELECT family FROM compare_run
               WHERE id = NEW.compare_run_id)
      )
      THEN RAISE(ABORT, 'promotion requires comparable verified proofs') END;
    SELECT CASE WHEN NEW.presence_outcome = 'present_both'
      AND (
        (SELECT COUNT(*) FROM comparison_match_decision AS d
         JOIN comparison_match_candidate AS c
           ON c.id = d.candidate_id
          AND c.match_set_id = d.match_set_id
          AND c.compare_run_id = d.compare_run_id
         WHERE d.match_set_id = NEW.match_set_id
           AND d.compare_run_id = NEW.compare_run_id
           AND d.decision = 'accepted'
           AND c.candidate_kind = 'pair') <> 1
        OR EXISTS (
          SELECT 1 FROM comparison_match_decision AS d
          JOIN comparison_match_candidate AS c
            ON c.id = d.candidate_id
           AND c.match_set_id = d.match_set_id
           AND c.compare_run_id = d.compare_run_id
          WHERE d.match_set_id = NEW.match_set_id
            AND d.compare_run_id = NEW.compare_run_id
            AND d.decision = 'accepted'
            AND c.candidate_kind <> 'pair'
        )
      ) THEN RAISE(ABORT, 'present_both requires one accepted pair') END;
    SELECT CASE WHEN NEW.presence_outcome = 'removed'
      AND (
        (SELECT COUNT(*) FROM comparison_match_decision AS d
         JOIN comparison_match_candidate AS c
           ON c.id = d.candidate_id
          AND c.match_set_id = d.match_set_id
          AND c.compare_run_id = d.compare_run_id
         WHERE d.match_set_id = NEW.match_set_id
           AND d.compare_run_id = NEW.compare_run_id
           AND d.decision = 'accepted'
           AND c.candidate_kind = 'baseline_only') <> 1
        OR EXISTS (
          SELECT 1 FROM comparison_match_decision AS d
          JOIN comparison_match_candidate AS c
            ON c.id = d.candidate_id
           AND c.match_set_id = d.match_set_id
           AND c.compare_run_id = d.compare_run_id
          WHERE d.match_set_id = NEW.match_set_id
            AND d.compare_run_id = NEW.compare_run_id
            AND d.decision = 'accepted'
            AND c.candidate_kind <> 'baseline_only'
        )
        OR NEW.absence_assertion_id IS NULL
        OR NEW.absence_missing_side <> 'target'
        OR NOT EXISTS (
          SELECT 1 FROM comparison_absence_assertion AS a
          WHERE a.id = NEW.absence_assertion_id
            AND a.compare_run_id = NEW.compare_run_id
            AND a.match_set_id = NEW.match_set_id
            AND a.missing_side = 'target'
            AND a.status = 'proven'
        )
      ) THEN RAISE(ABORT, 'removed promotion gate failed') END;
    SELECT CASE WHEN NEW.presence_outcome = 'added'
      AND (
        (SELECT COUNT(*) FROM comparison_match_decision AS d
         JOIN comparison_match_candidate AS c
           ON c.id = d.candidate_id
          AND c.match_set_id = d.match_set_id
          AND c.compare_run_id = d.compare_run_id
         WHERE d.match_set_id = NEW.match_set_id
           AND d.compare_run_id = NEW.compare_run_id
           AND d.decision = 'accepted'
           AND c.candidate_kind = 'target_only') <> 1
        OR EXISTS (
          SELECT 1 FROM comparison_match_decision AS d
          JOIN comparison_match_candidate AS c
            ON c.id = d.candidate_id
           AND c.match_set_id = d.match_set_id
           AND c.compare_run_id = d.compare_run_id
          WHERE d.match_set_id = NEW.match_set_id
            AND d.compare_run_id = NEW.compare_run_id
            AND d.decision = 'accepted'
            AND c.candidate_kind <> 'target_only'
        )
        OR NEW.absence_assertion_id IS NULL
        OR NEW.absence_missing_side <> 'baseline'
        OR NOT EXISTS (
          SELECT 1 FROM comparison_absence_assertion AS a
          WHERE a.id = NEW.absence_assertion_id
            AND a.compare_run_id = NEW.compare_run_id
            AND a.match_set_id = NEW.match_set_id
            AND a.missing_side = 'baseline'
            AND a.status = 'proven'
        )
      ) THEN RAISE(ABORT, 'added promotion gate failed') END;
END;

CREATE TRIGGER trg_change_observation_immutable
BEFORE UPDATE ON comparison_change_observation
BEGIN
    SELECT RAISE(ABORT, 'observation is write-once');
END;

CREATE TRIGGER trg_change_observation_delete_immutable
BEFORE DELETE ON comparison_change_observation
BEGIN
    SELECT RAISE(ABORT, 'observation is write-once');
END;

CREATE TRIGGER trg_change_label_write_phase
BEFORE INSERT ON comparison_change_label
BEGIN
    SELECT CASE WHEN (
        SELECT status FROM compare_run WHERE id = NEW.compare_run_id
    ) <> 'running' THEN RAISE(ABORT, 'label requires running run') END;
END;

CREATE TRIGGER trg_change_label_unknown_exclusive
BEFORE INSERT ON comparison_change_label
WHEN EXISTS (
    SELECT 1 FROM comparison_change_label AS x
    WHERE x.observation_id = NEW.observation_id
      AND x.compare_run_id = NEW.compare_run_id
      AND x.match_set_id = NEW.match_set_id
      AND (
        (NEW.label = 'unknown' AND x.label <> 'unknown')
        OR (NEW.label <> 'unknown' AND x.label = 'unknown')
      )
)
BEGIN
    SELECT RAISE(ABORT, 'unknown label is exclusive');
END;

CREATE TRIGGER trg_change_label_parser_delta_provenance
BEFORE INSERT ON comparison_change_label
WHEN NEW.label = 'parser_delta'
  AND length(trim(NEW.provenance)) = 0
BEGIN
    SELECT RAISE(ABORT, 'parser_delta needs provenance');
END;

CREATE TRIGGER trg_change_label_immutable
BEFORE UPDATE ON comparison_change_label
BEGIN
    SELECT RAISE(ABORT, 'label is write-once');
END;

CREATE TRIGGER trg_change_label_delete_immutable
BEFORE DELETE ON comparison_change_label
BEGIN
    SELECT RAISE(ABORT, 'label is write-once');
END;
