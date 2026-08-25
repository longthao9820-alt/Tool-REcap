-- Milestone 0 foundation: identity, artifact publication and durable jobs.

CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    root_path TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE episodes (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
    duration_ms INTEGER NOT NULL CHECK (duration_ms > 0),
    media_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (project_id, ordinal),
    UNIQUE (project_id, source_sha256)
);

CREATE TABLE artifacts (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    size INTEGER NOT NULL CHECK (size >= 0),
    producer_version TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('PUBLISHED')),
    created_at TEXT NOT NULL,
    UNIQUE (episode_id, kind, cache_key)
);

CREATE TABLE artifact_dependencies (
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    depends_on_artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    PRIMARY KEY (artifact_id, depends_on_artifact_id),
    CHECK (artifact_id <> depends_on_artifact_id)
);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'PENDING','READY','RUNNING','SUCCEEDED','FAILED','CANCELLED','INTERRUPTED'
    )),
    attempts INTEGER NOT NULL CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1),
    progress REAL NOT NULL DEFAULT 0.0,
    input_hash TEXT NOT NULL UNIQUE,
    lease_until TEXT,
    error_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE job_events (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    state TEXT NOT NULL,
    message_key TEXT NOT NULL,
    data_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (job_id, sequence)
);

CREATE INDEX idx_episodes_project ON episodes(project_id);
CREATE INDEX idx_artifacts_episode ON artifacts(episode_id);
CREATE INDEX idx_jobs_project_stage ON jobs(project_id, stage);
