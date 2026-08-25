"""SQLite repository implementations."""

from __future__ import annotations

import json
import sqlite3

from ...domain.identity import new_id
from ...domain.jobs.job import Job, JobState
from ...domain.media.metadata import MediaMetadata
from ...domain.project import Episode, Project

_NOW = "datetime('now')"


class SqliteProjectRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(self, project: Project) -> None:
        self._connection.execute(
            f"INSERT INTO projects(id, name, root_path, status, created_at)"
            f" VALUES (?, ?, ?, ?, {_NOW})",
            (project.id, project.name, project.root_path, project.status),
        )

    def get(self, project_id: str) -> Project | None:
        row = self._connection.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None:
            return None
        return Project(
            id=row["id"], name=row["name"], root_path=row["root_path"], status=row["status"]
        )


class SqliteEpisodeRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(self, episode: Episode) -> None:
        self._connection.execute(
            f"INSERT INTO episodes(id, project_id, ordinal, source_path, source_sha256,"
            f" duration_ms, media_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, {_NOW})",
            (
                episode.id,
                episode.project_id,
                episode.ordinal,
                episode.source_path,
                episode.source_sha256,
                episode.duration_ms,
                json.dumps(episode.media.to_dict(), sort_keys=True),
            ),
        )

    def get_by_source(self, project_id: str, source_sha256: str) -> Episode | None:
        row = self._connection.execute(
            "SELECT * FROM episodes WHERE project_id = ? AND source_sha256 = ?",
            (project_id, source_sha256),
        ).fetchone()
        return None if row is None else self._to_episode(row)

    def get(self, episode_id: str) -> Episode | None:
        row = self._connection.execute(
            "SELECT * FROM episodes WHERE id = ?", (episode_id,)
        ).fetchone()
        return None if row is None else self._to_episode(row)

    def next_ordinal(self, project_id: str) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(ordinal), 0) + 1 AS next FROM episodes WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        return int(row["next"])

    @staticmethod
    def _to_episode(row: sqlite3.Row) -> Episode:
        return Episode(
            id=row["id"],
            project_id=row["project_id"],
            ordinal=row["ordinal"],
            source_path=row["source_path"],
            source_sha256=row["source_sha256"],
            media=MediaMetadata.from_dict(json.loads(row["media_json"])),
        )


class SqliteJobRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def find_by_input_hash(self, input_hash: str) -> Job | None:
        row = self._connection.execute(
            "SELECT * FROM jobs WHERE input_hash = ?", (input_hash,)
        ).fetchone()
        if row is None:
            return None
        return Job(
            id=row["id"],
            project_id=row["project_id"],
            stage=row["stage"],
            scope_type=row["scope_type"],
            scope_id=row["scope_id"],
            input_hash=row["input_hash"],
            state=JobState(row["state"]),
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            error_json=row["error_json"],
        )

    def save(self, job: Job) -> None:
        self._connection.execute(
            f"INSERT INTO jobs(id, project_id, stage, scope_type, scope_id, state, attempts,"
            f" max_attempts, input_hash, error_json, created_at, updated_at)"
            f" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, {_NOW}, {_NOW})"
            f" ON CONFLICT(id) DO UPDATE SET state = excluded.state,"
            f" attempts = excluded.attempts, error_json = excluded.error_json,"
            f" updated_at = {_NOW}",
            (
                job.id,
                job.project_id,
                job.stage,
                job.scope_type,
                job.scope_id,
                job.state.value,
                job.attempts,
                job.max_attempts,
                job.input_hash,
                job.error_json,
            ),
        )

    def append_event(self, job_id: str, state: str, message_key: str) -> None:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next FROM job_events WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        self._connection.execute(
            f"INSERT INTO job_events(id, job_id, sequence, state, message_key, created_at)"
            f" VALUES (?, ?, ?, ?, ?, {_NOW})",
            (new_id(), job_id, int(row["next"]), state, message_key),
        )


class SqliteArtifactRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def publish(
        self,
        *,
        artifact_id: str,
        episode_id: str,
        kind: str,
        path: str,
        sha256: str,
        size: int,
        producer_version: str,
        cache_key: str,
    ) -> None:
        self._connection.execute(
            f"INSERT INTO artifacts(id, episode_id, kind, path, sha256, size,"
            f" producer_version, cache_key, state, created_at)"
            f" VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PUBLISHED', {_NOW})",
            (artifact_id, episode_id, kind, path, sha256, size, producer_version, cache_key),
        )

    def find(self, episode_id: str, kind: str, cache_key: str):
        return self._connection.execute(
            "SELECT * FROM artifacts WHERE episode_id = ? AND kind = ? AND cache_key = ?",
            (episode_id, kind, cache_key),
        ).fetchone()
