"""Migration round-trip and referential integrity."""

from __future__ import annotations

import sqlite3

import pytest

from recap_core.infrastructure.database.connection import migrate, open_migrated


def test_migrate_from_empty_database_creates_schema(tmp_path):
    connection = open_migrated(tmp_path / "recap.sqlite3")
    tables = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"projects", "episodes", "artifacts", "jobs", "job_events"} <= tables
    assert connection.execute("SELECT version FROM schema_migrations").fetchall()
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    connection.close()


def test_migrate_is_idempotent(tmp_path):
    path = tmp_path / "recap.sqlite3"
    first = open_migrated(path)
    version = migrate(first)
    again = migrate(first)
    assert version == again
    assert first.execute("SELECT COUNT(*) AS c FROM schema_migrations").fetchone()["c"] == 1
    first.close()


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
