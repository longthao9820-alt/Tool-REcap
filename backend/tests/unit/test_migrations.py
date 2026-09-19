"""Migration round-trip, upgrade path and referential integrity."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from recap_core.domain.identity import new_id
from recap_core.infrastructure.database.connection import MIGRATIONS_DIR, migrate, open_migrated

FOUNDATION_TABLES = {"projects", "episodes", "artifacts", "jobs", "job_events"}
ANALYSIS_TABLES = {
    "analysis_revisions",
    "recap_revisions",
    "stage_checkpoints",
    "stage_checkpoint_artifacts",
    "transcript_segments",
    "shots",
    "scenes",
    "scene_shots",
    "characters",
    "scene_characters",
    "events",
    "event_characters",
    "evidence_items",
    "plots",
    "plot_events",
    "story_edges",
    "recap_plans",
    "recap_plan_events",
}


def table_names(connection) -> set[str]:
    return {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def test_migrate_from_empty_database_creates_the_whole_schema(tmp_path):
    connection = open_migrated(tmp_path / "recap.sqlite3")
    tables = table_names(connection)
    assert FOUNDATION_TABLES <= tables
    assert ANALYSIS_TABLES <= tables
    assert connection.execute("SELECT version FROM schema_migrations").fetchall()
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_migrate_is_idempotent(tmp_path):
    path = tmp_path / "recap.sqlite3"
    first = open_migrated(path)
    version = migrate(first)
    again = migrate(first)
    assert version == again == len(migration_files())
    applied = first.execute("SELECT COUNT(*) AS c FROM schema_migrations").fetchone()["c"]
    assert applied == len(migration_files())
    first.close()


def _seed_foundation_row(connection) -> tuple[str, str]:
    project_id, episode_id = new_id(), new_id()
    connection.execute(
        "INSERT INTO projects(id, name, root_path, status, created_at)"
        " VALUES (?, 'P', ?, 'NEW', datetime('now'))",
        (project_id, f"/tmp/{project_id}"),
    )
    connection.execute(
        "INSERT INTO episodes(id, project_id, ordinal, source_path, source_sha256,"
        " duration_ms, media_json, created_at)"
        " VALUES (?, ?, 1, 'src.mp4', ?, 1000, '{}', datetime('now'))",
        (episode_id, project_id, "a" * 64),
    )
    return project_id, episode_id


def test_upgrade_from_0001_preserves_existing_data_and_foreign_keys(tmp_path):
    """Migrating an existing Contract 001 database forward must not destroy it."""
    staged = tmp_path / "migrations"
    staged.mkdir()
    shutil.copyfile(MIGRATIONS_DIR / "0001_foundation.sql", staged / "0001_foundation.sql")

    path = tmp_path / "recap.sqlite3"
    connection = open_migrated(path, staged)
    assert ANALYSIS_TABLES & table_names(connection) == set()
    project_id, episode_id = _seed_foundation_row(connection)

    version = migrate(connection, MIGRATIONS_DIR)
    assert version == len(migration_files())
    assert ANALYSIS_TABLES <= table_names(connection)
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert (
        connection.execute("SELECT project_id FROM episodes WHERE id = ?", (episode_id,))
        .fetchone()["project_id"]
        == project_id
    )
    connection.execute(
        "INSERT INTO analysis_revisions(id, episode_id, config_hash, source_sha256, status,"
        " schema_version, created_at) VALUES (?, ?, ?, ?, 'OPEN', 1, datetime('now'))",
        (new_id(), episode_id, "c" * 64, "a" * 64),
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO analysis_revisions(id, episode_id, config_hash, source_sha256, status,"
            " schema_version, created_at) VALUES (?, 'ghost', ?, ?, 'OPEN', 1, datetime('now'))",
            (new_id(), "d" * 64, "a" * 64),
        )
    connection.close()


def test_foreign_keys_are_enforced(connection):
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO episodes(id, project_id, ordinal, source_path, source_sha256,"
            " duration_ms, media_json, created_at)"
            " VALUES ('e1', 'missing-project', 1, 'x', ?, 1000, '{}', datetime('now'))",
            ("a" * 64,),
        )


def test_duration_and_ordinal_checks_fail_closed(connection, project):
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO episodes(id, project_id, ordinal, source_path, source_sha256,"
            " duration_ms, media_json, created_at)"
            " VALUES ('e1', ?, 1, 'x', ?, 0, '{}', datetime('now'))",
            (project.id, "a" * 64),
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO episodes(id, project_id, ordinal, source_path, source_sha256,"
            " duration_ms, media_json, created_at)"
            " VALUES ('e2', ?, 0, 'x', ?, 1000, '{}', datetime('now'))",
            (project.id, "a" * 64),
        )


def test_analysis_revision_is_unique_per_episode_and_config(connection, project):
    _, episode_id = _seed_foundation_row(connection)
    config_hash = "c" * 64
    connection.execute(
        "INSERT INTO analysis_revisions(id, episode_id, config_hash, source_sha256, status,"
        " schema_version, created_at) VALUES ('r1', ?, ?, ?, 'OPEN', 1, datetime('now'))",
        (episode_id, config_hash, "a" * 64),
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO analysis_revisions(id, episode_id, config_hash, source_sha256, status,"
            " schema_version, created_at) VALUES ('r2', ?, ?, ?, 'OPEN', 1, datetime('now'))",
            (episode_id, config_hash, "a" * 64),
        )


def test_stage_checkpoint_state_and_scope_are_constrained(connection, project):
    _, episode_id = _seed_foundation_row(connection)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO stage_checkpoints(id, stage, scope_type, scope_id, input_hash, state,"
            " output_json, created_at)"
            " VALUES ('c1', 'X', 'ELSEWHERE', ?, ?, 'SUCCEEDED', '{}', datetime('now'))",
            (episode_id, "b" * 64),
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO stage_checkpoints(id, stage, scope_type, scope_id, input_hash, state,"
            " output_json, created_at)"
            " VALUES ('c2', 'X', 'EPISODE', ?, ?, 'RUNNING', '{}', datetime('now'))",
            (episode_id, "b" * 64),
        )
