"""Shared fixtures: migrated database, project and source-file copies."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from recap_core.domain.identity import new_id
from recap_core.domain.project import Project
from recap_core.infrastructure.database.connection import open_migrated
from recap_core.infrastructure.database.repositories import SqliteProjectRepository
from tests.support import SAMPLE_EPISODE


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
