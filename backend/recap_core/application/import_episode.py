"""Import one local episode: source identity + probe + transactional checkpoint."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..domain.errors import (
    CheckpointIntegrityError,
    ProjectNotFoundError,
    SourceIdentityMismatchError,
    SourceMissingError,
)
from ..domain.identity import hash_bytes, hash_file, new_id, validate_sha256
from ..domain.jobs.job import Job, JobState, job_input_hash
from ..domain.media.metadata import SCHEMA_VERSION
from ..domain.project import Episode
from ..infrastructure.database.repositories import (
    SqliteArtifactRepository,
    SqliteEpisodeRepository,
    SqliteJobRepository,
    SqliteProjectRepository,
)
from ..infrastructure.filesystem.project_layout import ProjectLayout
from ..ports.media_prober import MediaProber

STAGE = "IMPORT"
SCOPE_TYPE = "EPISODE_SOURCE"
ARTIFACT_KIND = "media_metadata"
METADATA_DIRECTORY = "metadata"


def metadata_relative_path(source_sha256: str) -> str:
    """Deterministic per-source metadata destination inside the project root."""
    return "{0}/{1}.media.json".format(METADATA_DIRECTORY, source_sha256)


def _metadata_payload(episode_id: str, source_sha256: str, media: dict) -> bytes:
    """Deterministic metadata artifact bytes for one episode."""
    return json.dumps(
        {"episode_id": episode_id, "source_sha256": source_sha256, "media": media},
        sort_keys=True,
        indent=2,
    ).encode("utf-8")


@dataclass(frozen=True)
class ImportResult:
    """Outcome of one import attempt."""

    episode: Episode
    job_id: str
    artifact_path: Path
    created: bool


class ImportEpisodeUseCase:
    """Idempotent, resumable import of an immutable local source file.

    Source bytes are only read. Media metadata comes exclusively from the probe
    port; callers cannot supply or override media evidence.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        prober: MediaProber,
        *,
        before_publish: Callable[[], None] | None = None,
    ) -> None:
        self._connection = connection
        self._prober = prober
        self._before_publish = before_publish
        self._projects = SqliteProjectRepository(connection)
        self._episodes = SqliteEpisodeRepository(connection)
        self._jobs = SqliteJobRepository(connection)
        self._artifacts = SqliteArtifactRepository(connection)

    def execute(
        self,
        *,
        project_id: str,
        project_root: Path,
        source_path: Path,
        expected_source_sha256: str | None = None,
    ) -> ImportResult:
        project = self._projects.get(project_id)
        if project is None:
            raise ProjectNotFoundError(f"project not persisted: {project_id}")

        source = Path(source_path)
        if not source.is_file():
            raise SourceMissingError(f"source file not found: {source}")

        source_sha256 = hash_file(source)
        if expected_source_sha256 is not None:
            if validate_sha256(expected_source_sha256) != source_sha256:
                raise SourceIdentityMismatchError(
                    "source bytes changed for {0}: expected {1}, actual {2}".format(
                        source, expected_source_sha256, source_sha256
                    )
                )
        self._assert_stable_source(project_id, str(source.resolve()), source_sha256)

        input_hash = job_input_hash(
            STAGE,
            SCOPE_TYPE,
            source_sha256,
            project_id,
            self._prober.version,
            str(SCHEMA_VERSION),
        )
        # The persisted project root is authoritative; `project_root` is advisory
        # only and can never redirect generated artifact destinations.
        layout = ProjectLayout(project.root_path).create()

        job = self._jobs.find_by_input_hash(input_hash)
        if job is not None and job.state is JobState.SUCCEEDED:
            return self._load_completed(project_id, source_sha256, job, layout)
        job = self._claim(job, project_id, input_hash, source_sha256)

        media = self._prober.probe(source)
        if hash_file(source) != source_sha256:
            raise SourceIdentityMismatchError(
                "source bytes changed during probe for {0}: recorded {1}".format(
                    source, source_sha256
                )
            )
        episode = self._episodes.get_by_source(project_id, source_sha256) or Episode(
            id=new_id(),
            project_id=project_id,
            ordinal=self._episodes.next_ordinal(project_id),
            source_path=str(source.resolve()),
            source_sha256=source_sha256,
            media=media,
        )

        payload = _metadata_payload(episode.id, source_sha256, media.to_dict())
        artifact_path = layout.write_atomic(metadata_relative_path(source_sha256), payload)

        if self._before_publish is not None:
            self._before_publish()

        self._publish(
            job=job,
            episode=episode,
            artifact_path=artifact_path,
            payload=payload,
            input_hash=input_hash,
        )
        return ImportResult(
            episode=episode, job_id=job.id, artifact_path=artifact_path, created=True
        )

    # -- internals -----------------------------------------------------------

    def _assert_stable_source(self, project_id: str, source_path: str, sha256: str) -> None:
        row = self._connection.execute(
            "SELECT source_sha256 FROM episodes WHERE project_id = ? AND source_path = ?",
            (project_id, source_path),
        ).fetchone()
        if row is not None and row["source_sha256"] != sha256:
            raise SourceIdentityMismatchError(
                "source bytes changed for {0}: recorded {1}, actual {2}".format(
                    source_path, row["source_sha256"], sha256
                )
            )

    def _load_completed(
        self, project_id: str, source_sha256: str, job: Job, layout: ProjectLayout
    ) -> ImportResult:
        """Reuse a SUCCEEDED checkpoint only against authoritative stored identity.

        A SUCCEEDED job is transactionally bound to one Episode and one published
        artifact row, and that row is the authoritative identity of the bytes on
        disk. Divergent bytes are rebuilt deterministically from the persisted
        Episode and re-verified; anything unverifiable fails typed instead of being
        returned as a completed checkpoint.
        """
        episode = self._episodes.get_by_source(project_id, source_sha256)
        if episode is None:
            raise CheckpointIntegrityError(
                f"job {job.id} is SUCCEEDED but no Episode exists for {source_sha256}"
            )
        artifact = self._artifacts.find(episode.id, ARTIFACT_KIND, job.input_hash)
        if artifact is None:
            raise CheckpointIntegrityError(
                f"job {job.id} is SUCCEEDED but artifact {ARTIFACT_KIND} is not published"
            )
        expected_sha256 = artifact["sha256"]
        expected_size = int(artifact["size"])
        artifact_path = layout.resolve(Path(artifact["path"]))

        if not self._artifact_matches(artifact_path, expected_sha256, expected_size):
            payload = _metadata_payload(
                episode.id, episode.source_sha256, episode.media.to_dict()
            )
            if hash_bytes(payload) != expected_sha256 or len(payload) != expected_size:
                raise CheckpointIntegrityError(
                    "cannot rebuild artifact {0} to stored identity {1}".format(
                        artifact_path, expected_sha256
                    )
                )
            layout.write_atomic(artifact_path, payload)
            if not self._artifact_matches(artifact_path, expected_sha256, expected_size):
                raise CheckpointIntegrityError(
                    f"rebuilt artifact {artifact_path} does not match stored identity"
                )
        return ImportResult(
            episode=episode,
            job_id=job.id,
            artifact_path=artifact_path,
            created=False,
        )

    @staticmethod
    def _artifact_matches(path: Path, sha256: str, size: int) -> bool:
        """True only when on-disk bytes match stored identity and parse as metadata."""
        if not path.is_file() or path.stat().st_size != size:
            return False
        if hash_file(path) != sha256:
            return False
        try:
            document = json.loads(path.read_bytes())
        except (ValueError, UnicodeDecodeError):
            return False
        return isinstance(document, dict) and "media" in document

    def _claim(
        self, job: Job | None, project_id: str, input_hash: str, source_sha256: str
    ) -> Job:
        """Move a new, failed or abandoned job into RUNNING for this attempt."""
        if job is None:
            job = Job(
                id=new_id(),
                project_id=project_id,
                stage=STAGE,
                scope_type=SCOPE_TYPE,
                scope_id=source_sha256,
                input_hash=input_hash,
                state=JobState.PENDING,
            )
        elif job.state is JobState.RUNNING:
            job = job.with_state(JobState.INTERRUPTED)
            self._persist(job, "job.interrupted")
        job = job.with_state(JobState.READY)
        self._persist(job, "job.ready")
        job = job.with_state(JobState.RUNNING)
        self._persist(job, "job.running")
        return job

    def _persist(self, job: Job, message_key: str) -> None:
        self._jobs.save(job)
        self._jobs.append_event(job.id, job.state.value, message_key)

    def _publish(
        self,
        *,
        job: Job,
        episode: Episode,
        artifact_path: Path,
        payload: bytes,
        input_hash: str,
    ) -> None:
        """Publish episode, artifact and SUCCEEDED job in one transaction."""
        if not artifact_path.is_file() or artifact_path.stat().st_size != len(payload):
            raise RuntimeError("refusing to publish: artifact output is missing or truncated")
        succeeded = job.with_state(JobState.SUCCEEDED)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            if self._episodes.get_by_source(episode.project_id, episode.source_sha256) is None:
                self._episodes.add(episode)
            if self._artifacts.find(episode.id, ARTIFACT_KIND, input_hash) is None:
                self._artifacts.publish(
                    artifact_id=new_id(),
                    episode_id=episode.id,
                    kind=ARTIFACT_KIND,
                    path=str(artifact_path),
                    sha256=hash_bytes(payload),
                    size=len(payload),
                    producer_version=self._prober.version,
                    cache_key=input_hash,
                )
            self._jobs.save(succeeded)
            self._jobs.append_event(succeeded.id, succeeded.state.value, "job.succeeded")
            self._connection.execute("COMMIT")
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
