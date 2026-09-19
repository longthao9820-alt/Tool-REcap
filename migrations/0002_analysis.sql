-- Milestone 1 vertical slice: analysis/recap revisions, analysis tables, checkpoints.
--
-- Cross-revision substitution fails closed at the schema level: every analysis row
-- carries its analysis_revision_id and child rows use composite foreign keys, so a
-- reference into another revision has no matching parent key.

CREATE TABLE analysis_revisions (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    config_hash TEXT NOT NULL CHECK (length(config_hash) = 64),
    source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
    status TEXT NOT NULL CHECK (status IN ('OPEN', 'COMPLETE')),
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    created_at TEXT NOT NULL,
    UNIQUE (episode_id, config_hash)
);

CREATE TABLE recap_revisions (
    id TEXT PRIMARY KEY,
    analysis_revision_id TEXT NOT NULL REFERENCES analysis_revisions(id) ON DELETE CASCADE,
    profile_hash TEXT NOT NULL CHECK (length(profile_hash) = 64),
    profile_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('OPEN', 'COMPLETE')),
    created_at TEXT NOT NULL,
    UNIQUE (analysis_revision_id, profile_hash)
);

-- Durable per-stage completion record. A checkpoint is only readable when every
-- referenced artifact still matches its recorded hash/size, so a stale SUCCEEDED
-- row can never be used to skip a corrupt predecessor.
CREATE TABLE stage_checkpoints (
    id TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    scope_type TEXT NOT NULL CHECK (scope_type IN (
        'EPISODE', 'ANALYSIS_REVISION', 'RECAP_REVISION'
    )),
    scope_id TEXT NOT NULL,
    input_hash TEXT NOT NULL CHECK (length(input_hash) = 64),
    state TEXT NOT NULL CHECK (state IN ('SUCCEEDED')),
    output_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (stage, scope_type, scope_id, input_hash),
    UNIQUE (stage, scope_type, scope_id)
);

CREATE TABLE stage_checkpoint_artifacts (
    checkpoint_id TEXT NOT NULL REFERENCES stage_checkpoints(id) ON DELETE CASCADE,
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    PRIMARY KEY (checkpoint_id, artifact_id, role)
);

CREATE TABLE transcript_segments (
    id TEXT PRIMARY KEY,
    analysis_revision_id TEXT NOT NULL REFERENCES analysis_revisions(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    start_ms INTEGER NOT NULL CHECK (start_ms >= 0),
    end_ms INTEGER NOT NULL,
    speaker_id TEXT NOT NULL,
    text TEXT NOT NULL,
    words_json TEXT,
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CHECK (end_ms > start_ms),
    UNIQUE (analysis_revision_id, ordinal),
    UNIQUE (id, analysis_revision_id)
);

CREATE TABLE shots (
    id TEXT PRIMARY KEY,
    analysis_revision_id TEXT NOT NULL REFERENCES analysis_revisions(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    start_ms INTEGER NOT NULL CHECK (start_ms >= 0),
    end_ms INTEGER NOT NULL,
    keyframes_json TEXT NOT NULL,
    excluded_reason TEXT,
    CHECK (end_ms > start_ms),
    UNIQUE (analysis_revision_id, ordinal),
    UNIQUE (id, analysis_revision_id)
);

CREATE TABLE scenes (
    id TEXT PRIMARY KEY,
    analysis_revision_id TEXT NOT NULL REFERENCES analysis_revisions(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    start_ms INTEGER NOT NULL CHECK (start_ms >= 0),
    end_ms INTEGER NOT NULL,
    location TEXT NOT NULL,
    summary TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    provenance_json TEXT NOT NULL,
    CHECK (end_ms > start_ms),
    UNIQUE (analysis_revision_id, ordinal),
    UNIQUE (id, analysis_revision_id)
);

CREATE TABLE scene_shots (
    scene_id TEXT NOT NULL,
    shot_id TEXT NOT NULL,
    analysis_revision_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (scene_id, shot_id),
    UNIQUE (scene_id, ordinal),
    UNIQUE (shot_id),
    FOREIGN KEY (scene_id, analysis_revision_id)
        REFERENCES scenes(id, analysis_revision_id) ON DELETE CASCADE,
    FOREIGN KEY (shot_id, analysis_revision_id)
        REFERENCES shots(id, analysis_revision_id) ON DELETE CASCADE
);

CREATE TABLE characters (
    id TEXT PRIMARY KEY,
    analysis_revision_id TEXT NOT NULL REFERENCES analysis_revisions(id) ON DELETE CASCADE,
    canonical_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('RESOLVED', 'UNKNOWN')),
    aliases_json TEXT NOT NULL,
    refs_json TEXT NOT NULL,
    UNIQUE (analysis_revision_id, canonical_name),
    UNIQUE (id, analysis_revision_id)
);

CREATE TABLE scene_characters (
    scene_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    analysis_revision_id TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    PRIMARY KEY (scene_id, character_id),
    FOREIGN KEY (scene_id, analysis_revision_id)
        REFERENCES scenes(id, analysis_revision_id) ON DELETE CASCADE,
    FOREIGN KEY (character_id, analysis_revision_id)
        REFERENCES characters(id, analysis_revision_id) ON DELETE CASCADE
);

CREATE TABLE events (
    id TEXT PRIMARY KEY,
    scene_id TEXT NOT NULL,
    analysis_revision_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    start_ms INTEGER NOT NULL CHECK (start_ms >= 0),
    end_ms INTEGER NOT NULL,
    action TEXT NOT NULL,
    cause TEXT NOT NULL,
    consequence TEXT NOT NULL,
    importance REAL NOT NULL CHECK (importance >= 0.0 AND importance <= 1.0),
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CHECK (end_ms > start_ms),
    UNIQUE (analysis_revision_id, ordinal),
    UNIQUE (id, analysis_revision_id),
    FOREIGN KEY (scene_id, analysis_revision_id)
        REFERENCES scenes(id, analysis_revision_id) ON DELETE CASCADE
);

CREATE TABLE event_characters (
    event_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    analysis_revision_id TEXT NOT NULL,
    role TEXT NOT NULL,
    PRIMARY KEY (event_id, character_id),
    FOREIGN KEY (event_id, analysis_revision_id)
        REFERENCES events(id, analysis_revision_id) ON DELETE CASCADE,
    FOREIGN KEY (character_id, analysis_revision_id)
        REFERENCES characters(id, analysis_revision_id) ON DELETE CASCADE
);

CREATE TABLE evidence_items (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    analysis_revision_id TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('TRANSCRIPT', 'VISUAL', 'TIME_RANGE')),
    start_ms INTEGER NOT NULL CHECK (start_ms >= 0),
    end_ms INTEGER NOT NULL,
    transcript_id TEXT,
    artifact_id TEXT REFERENCES artifacts(id) ON DELETE RESTRICT,
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CHECK (end_ms > start_ms),
    UNIQUE (id, analysis_revision_id),
    FOREIGN KEY (event_id, analysis_revision_id)
        REFERENCES events(id, analysis_revision_id) ON DELETE CASCADE,
    FOREIGN KEY (transcript_id, analysis_revision_id)
        REFERENCES transcript_segments(id, analysis_revision_id) ON DELETE RESTRICT
);

CREATE TABLE plots (
    id TEXT PRIMARY KEY,
    analysis_revision_id TEXT NOT NULL REFERENCES analysis_revisions(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    importance REAL NOT NULL CHECK (importance >= 0.0 AND importance <= 1.0),
    UNIQUE (analysis_revision_id, ordinal),
    UNIQUE (id, analysis_revision_id)
);

CREATE TABLE plot_events (
    plot_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    analysis_revision_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    membership_score REAL NOT NULL CHECK (membership_score >= 0.0 AND membership_score <= 1.0),
    PRIMARY KEY (plot_id, event_id),
    UNIQUE (plot_id, ordinal),
    FOREIGN KEY (plot_id, analysis_revision_id)
        REFERENCES plots(id, analysis_revision_id) ON DELETE CASCADE,
    FOREIGN KEY (event_id, analysis_revision_id)
        REFERENCES events(id, analysis_revision_id) ON DELETE CASCADE
);

CREATE TABLE story_edges (
    id TEXT PRIMARY KEY,
    analysis_revision_id TEXT NOT NULL REFERENCES analysis_revisions(id) ON DELETE CASCADE,
    from_event_id TEXT NOT NULL,
    to_event_id TEXT NOT NULL,
    relation TEXT NOT NULL CHECK (relation IN (
        'CAUSE', 'EFFECT', 'SETUP', 'PAYOFF', 'REVEAL', 'REACTION', 'CONSEQUENCE'
    )),
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CHECK (from_event_id <> to_event_id),
    UNIQUE (analysis_revision_id, from_event_id, to_event_id, relation),
    FOREIGN KEY (from_event_id, analysis_revision_id)
        REFERENCES events(id, analysis_revision_id) ON DELETE CASCADE,
    FOREIGN KEY (to_event_id, analysis_revision_id)
        REFERENCES events(id, analysis_revision_id) ON DELETE CASCADE
);

CREATE TABLE recap_plans (
    id TEXT PRIMARY KEY,
    recap_revision_id TEXT NOT NULL REFERENCES recap_revisions(id) ON DELETE CASCADE,
    selected_plots_json TEXT NOT NULL,
    estimated_ms INTEGER NOT NULL CHECK (estimated_ms >= 0),
    policy_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    created_at TEXT NOT NULL,
    UNIQUE (recap_revision_id)
);

CREATE TABLE recap_plan_events (
    plan_id TEXT NOT NULL REFERENCES recap_plans(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    budget_class TEXT NOT NULL CHECK (budget_class IN ('MUST_HAVE', 'SHOULD_HAVE', 'OPTIONAL')),
    decision_reason TEXT NOT NULL,
    PRIMARY KEY (plan_id, event_id),
    UNIQUE (plan_id, ordinal)
);

CREATE INDEX idx_analysis_revisions_episode ON analysis_revisions(episode_id);
CREATE INDEX idx_recap_revisions_analysis ON recap_revisions(analysis_revision_id);
CREATE INDEX idx_transcript_segments_revision ON transcript_segments(analysis_revision_id);
CREATE INDEX idx_shots_revision ON shots(analysis_revision_id);
CREATE INDEX idx_scenes_revision ON scenes(analysis_revision_id);
CREATE INDEX idx_events_scene ON events(scene_id);
CREATE INDEX idx_evidence_event ON evidence_items(event_id);
CREATE INDEX idx_story_edges_revision ON story_edges(analysis_revision_id);
