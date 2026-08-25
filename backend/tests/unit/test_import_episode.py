"""Import acceptance: identity, idempotency, resume and fail-closed negatives."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from tests.support import stub_prober
from recap_core.application.import_episode import (
    ARTIFACT_KIND,
    ImportEpisodeUseCase,
    metadata_relative_path,
)
from recap_core.domain.errors import (
    ProbeFailedError,
    ProbeUnavailableError,
    SourceIdentityMismatchError,
    SourceMissingError,
)
from recap_core.domain.identity import hash_file
from recap_core.domain.jobs.job import JobState
from recap_core.infrastructure.filesystem.project_layout import PROJECT_DIRECTORIES


class SimulatedCrash(BaseException):
    """Simulates process death between artifact write and publication."""


def run_import(connection, project, source_file, **kwargs):
    use_case = ImportEpisodeUseCase(connection, kwargs.pop("prober", None) or stub_prober(), **kwargs)
    return use_case.execute(
        project_id=project.id,
        project_root=Path(project.root_path),
        source_path=source_file,
    )


def counts(connection):
    def one(sql):
        return connection.execute(sql).fetchone()["c"]

    return {
        "episodes": one("SELECT COUNT(*) AS c FROM episodes"),
        "jobs": one("SELECT COUNT(*) AS c FROM jobs"),
        "artifacts": one("SELECT COUNT(*) AS c FROM artifacts"),
        "succeeded": one("SELECT COUNT(*) AS c FROM jobs WHERE state = 'SUCCEEDED'"),
    }


def test_import_creates_layout_episode_metadata_and_checkpoint(connection, project, source_file):
    result = run_import(connection, project, source_file)

    root = Path(project.root_path)
    for relative in PROJECT_DIRECTORIES:
        assert (root / relative).is_dir()

    assert result.created is True
    assert result.episode.source_sha256 == hash_file(source_file)
    assert result.episode.duration_ms == 1000
    assert result.episode.ordinal == 1

    assert result.artifact_path == root / metadata_relative_path(result.episode.source_sha256)
    document = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert document["source_sha256"] == result.episode.source_sha256
    assert document["media"]["duration_ms"] == 1000

    assert counts(connection) == {"episodes": 1, "jobs": 1, "artifacts": 1, "succeeded": 1}
    artifact = connection.execute("SELECT * FROM artifacts").fetchone()
    assert artifact["kind"] == ARTIFACT_KIND
    assert artifact["state"] == "PUBLISHED"
    assert artifact["size"] == result.artifact_path.stat().st_size
    assert not list((root / "metadata").glob("*.partial"))


def test_repeated_import_is_idempotent(connection, project, source_file):
    first = run_import(connection, project, source_file)
    second = run_import(connection, project, source_file)

    assert second.created is False
    assert second.episode.id == first.episode.id
    assert second.job_id == first.job_id
    assert counts(connection) == {"episodes": 1, "jobs": 1, "artifacts": 1, "succeeded": 1}


def test_n1_source_bytes_are_never_modified(connection, project, source_file):
    before = hash_file(source_file)
    before_mtime = source_file.stat().st_mtime_ns
    run_import(connection, project, source_file)
    assert hash_file(source_file) == before
    assert source_file.stat().st_mtime_ns == before_mtime


def test_n2_caller_cannot_supply_media_evidence(connection, project, source_file):
    parameters = set(inspect.signature(ImportEpisodeUseCase.execute).parameters)
    assert parameters == {
        "self",
        "project_id",
        "project_root",
        "source_path",
        "expected_source_sha256",
    }

    result = run_import(connection, project, source_file)
    stored = connection.execute("SELECT media_json, source_sha256 FROM episodes").fetchone()
    assert json.loads(stored["media_json"])["duration_ms"] == 1000
    assert stored["source_sha256"] == hash_file(source_file)
    assert result.episode.media.prober_version == "ffprobe-1"


def test_declared_hash_mismatch_fails_closed(connection, project, source_file):
    use_case = ImportEpisodeUseCase(connection, stub_prober())
    with pytest.raises(SourceIdentityMismatchError):
        use_case.execute(
            project_id=project.id,
            project_root=Path(project.root_path),
            source_path=source_file,
            expected_source_sha256="b" * 64,
        )
    assert counts(connection)["succeeded"] == 0


def test_changed_source_bytes_fail_closed(connection, project, source_file):
    run_import(connection, project, source_file)
    source_file.write_bytes(source_file.read_bytes() + b"tampered")
    with pytest.raises(SourceIdentityMismatchError):
        run_import(connection, project, source_file)
    assert counts(connection) == {"episodes": 1, "jobs": 1, "artifacts": 1, "succeeded": 1}


def test_missing_source_fails_closed(connection, project, tmp_path):
    with pytest.raises(SourceMissingError):
        run_import(connection, project, tmp_path / "absent.mp4")
    assert counts(connection)["jobs"] == 0


def test_invalid_probe_output_leaves_no_completed_state(connection, project, source_file):
    with pytest.raises(ProbeFailedError):
        run_import(connection, project, source_file, prober=stub_prober("{oops"))
    assert counts(connection) == {"episodes": 0, "jobs": 1, "artifacts": 0, "succeeded": 0}
    assert connection.execute("SELECT state FROM jobs").fetchone()["state"] == "RUNNING"


def test_unavailable_probe_leaves_no_completed_state(connection, project, source_file):
    from tests.support import StubCommandRunner
    from recap_core.infrastructure.ffmpeg.ffprobe import FFprobeMediaProber

    prober = FFprobeMediaProber(StubCommandRunner(raises=FileNotFoundError("ffprobe")))
    with pytest.raises(ProbeUnavailableError):
        run_import(connection, project, source_file, prober=prober)
    assert counts(connection)["succeeded"] == 0
    assert counts(connection)["artifacts"] == 0


def test_n3_interrupted_publication_is_not_readable_as_complete(connection, project, source_file):
    def crash():
        raise SimulatedCrash("process died before publication")

    with pytest.raises(SimulatedCrash):
        run_import(connection, project, source_file, before_publish=crash)

    assert counts(connection) == {"episodes": 0, "jobs": 1, "artifacts": 0, "succeeded": 0}
    assert connection.execute("SELECT state FROM jobs").fetchone()["state"] == "RUNNING"


def test_interrupted_import_resumes_and_completes(connection, project, source_file):
    def crash():
        raise SimulatedCrash("process died before publication")

    with pytest.raises(SimulatedCrash):
        run_import(connection, project, source_file, before_publish=crash)

    result = run_import(connection, project, source_file)

    assert result.created is True
    assert counts(connection) == {"episodes": 1, "jobs": 1, "artifacts": 1, "succeeded": 1}
    states = [
        row["state"]
        for row in connection.execute("SELECT state FROM job_events ORDER BY sequence")
    ]
    assert JobState.INTERRUPTED.value in states
    assert states[-1] == JobState.SUCCEEDED.value


def test_second_episode_gets_next_ordinal(connection, project, source_file, tmp_path):
    run_import(connection, project, source_file)
    other = tmp_path / "media" / "episode02.mp4"
    other.write_bytes(source_file.read_bytes() + b"\x00distinct")
    result = run_import(connection, project, other)
    assert result.episode.ordinal == 2
    assert counts(connection)["episodes"] == 2
