"""Shared fixtures: migrated database, project, source copies and pipeline wiring."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from recap_core.application.import_episode import ImportEpisodeUseCase
from recap_core.application.understand_episode import UnderstandEpisodeUseCase
from recap_core.domain.identity import new_id
from recap_core.domain.project import Episode, Project
from recap_core.infrastructure.database.connection import open_migrated
from recap_core.infrastructure.database.repositories import SqliteProjectRepository
from tests.providers import (
    DeterministicSceneAnalysisProvider,
    DeterministicSTTProvider,
    DeterministicStoryReasoningProvider,
    FakeMediaProcessor,
)
from tests.support import SAMPLE_EPISODE, stub_prober


@pytest.fixture
def connection(tmp_path: Path):
    conn = open_migrated(tmp_path / "db" / "recap.sqlite3")
    yield conn
    conn.close()


@pytest.fixture
def project(connection, tmp_path: Path) -> Project:
    root = tmp_path / "project"
    root.mkdir()
    record = Project(id=new_id(), name="Fixture Project", root_path=str(root))
    SqliteProjectRepository(connection).add(record)
    return record


@pytest.fixture
def source_file(tmp_path: Path) -> Path:
    """A writable copy of the committed media fixture."""
    destination = tmp_path / "media" / "episode01.mp4"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SAMPLE_EPISODE, destination)
    return destination


@pytest.fixture
def episode(connection, project, source_file) -> Episode:
    """One imported episode, probed through the stubbed ffprobe backend."""
    return (
        ImportEpisodeUseCase(connection, stub_prober())
        .execute(
            project_id=project.id,
            project_root=Path(project.root_path),
            source_path=source_file,
        )
        .episode
    )


@pytest.fixture
def providers():
    """Deterministic AI/STT doubles plus a file-writing media processor."""
    return {
        "media_processor": FakeMediaProcessor(shots=2),
        "stt_provider": DeterministicSTTProvider(),
        "scene_provider": DeterministicSceneAnalysisProvider(),
        "story_provider": DeterministicStoryReasoningProvider(),
    }


@pytest.fixture
def understand(connection, providers):
    """Build a pipeline over the shared deterministic providers."""

    def build(**overrides) -> UnderstandEpisodeUseCase:
        wiring = {**providers, **overrides}
        return UnderstandEpisodeUseCase(connection, **wiring)

    return build
