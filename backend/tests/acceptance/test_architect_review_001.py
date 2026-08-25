"""Architect adversarial pack for foundation identity/publication boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from recap_core.application.import_episode import ImportEpisodeUseCase
from recap_core.domain.errors import RecapError, SourceIdentityMismatchError
from recap_core.domain.identity import hash_file, new_id
from recap_core.domain.project import Project
from recap_core.infrastructure.database.repositories import SqliteProjectRepository
from tests.support import stub_prober


def _add_project(connection, root: Path, name: str) -> Project:
    project = Project(id=new_id(), name=name, root_path=str(root))
    SqliteProjectRepository(connection).add(project)
    return project


def _execute(connection, project: Project, source: Path, *, prober=None, root=None):
    return ImportEpisodeUseCase(connection, prober or stub_prober()).execute(
        project_id=project.id,
        project_root=Path(root or project.root_path),
        source_path=source,
    )


def test_a1_same_source_is_isolated_by_project(connection, project, source_file, tmp_path):
    second = _add_project(connection, tmp_path / "project-2", "Project 2")

    first_result = _execute(connection, project, source_file)
    second_result = _execute(connection, second, source_file)

    assert first_result.episode.project_id == project.id
    assert second_result.episode.project_id == second.id
    assert first_result.job_id != second_result.job_id
    assert connection.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2


def test_a2_corrupt_completed_artifact_is_not_reused(
    connection, project, source_file
):
    first = _execute(connection, project, source_file)
    first.artifact_path.write_bytes(b"")

    try:
        second = _execute(connection, project, source_file)
    except RecapError:
        return

    row = connection.execute("SELECT sha256, size FROM artifacts").fetchone()
    payload = second.artifact_path.read_bytes()
    assert payload
    assert len(payload) == row["size"]
    assert hash_file(second.artifact_path) == row["sha256"]
    document = json.loads(payload)
    assert document["source_sha256"] == second.episode.source_sha256


def test_a3_source_drift_during_probe_fails_closed(
    connection, project, source_file
):
    base = stub_prober()

    class MutatingProber:
        version = base.version

        def probe(self, path: Path):
            media = base.probe(path)
            path.write_bytes(path.read_bytes() + b"changed-during-probe")
            return media

    with pytest.raises(SourceIdentityMismatchError):
        _execute(connection, project, source_file, prober=MutatingProber())

    assert connection.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE state = 'SUCCEEDED'"
        ).fetchone()[0]
        == 0
    )


def test_a3b_source_drift_after_probe_before_publish_fails_closed(
    connection, project, source_file
):
    def mutate_before_publish():
        source_file.write_bytes(source_file.read_bytes() + b"changed-before-publish")

    with pytest.raises(SourceIdentityMismatchError):
        ImportEpisodeUseCase(
            connection,
            stub_prober(),
            before_publish=mutate_before_publish,
        ).execute(
            project_id=project.id,
            project_root=Path(project.root_path),
            source_path=source_file,
        )

    assert connection.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE state = 'SUCCEEDED'"
        ).fetchone()[0]
        == 0
    )


def test_a4_artifact_root_is_bound_to_persisted_project(
    connection, project, source_file, tmp_path
):
    substituted = tmp_path / "caller-substituted-root"

    try:
        result = _execute(
            connection,
            project,
            source_file,
            root=substituted,
        )
    except RecapError:
        assert not substituted.exists()
        return

    recorded = Path(project.root_path).resolve()
    assert result.artifact_path.resolve().is_relative_to(recorded)
    assert not substituted.exists()
