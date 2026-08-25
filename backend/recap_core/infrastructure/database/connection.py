"""SQLite connection and forward-only numbered migrations."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from ...domain.errors import ValidationError

MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "migrations"
_MIGRATION_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def connect(db_path: Path) -> sqlite3.Connection:
    """Open SQLite with WAL and foreign keys enforced."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA synchronous=FULL")
    return connection


def _discover(migrations_dir: Path) -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        match = _MIGRATION_RE.match(path.name)
        if not match:
            raise ValidationError(f"invalid migration filename: {path.name}")
        found.append((int(match.group(1)), path))
    if not found:
        raise ValidationError(f"no migrations found in {migrations_dir}")
    return found


def migrate(connection: sqlite3.Connection, migrations_dir: Path | None = None) -> int:
    """Apply pending migrations in order; returns the resulting schema version."""
    directory = migrations_dir or MIGRATIONS_DIR
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    applied = {row["version"] for row in connection.execute("SELECT version FROM schema_migrations")}
    current = max(applied, default=0)
    for version, path in _discover(directory):
        if version in applied:
            continue
        # executescript() implicitly commits first, so the transaction lives
        # inside the script itself: schema + version stamp are applied together.
        script = "\n".join(
            [
                "BEGIN;",
                path.read_text(encoding="utf-8"),
                "INSERT INTO schema_migrations(version, name, applied_at)"
                " VALUES ({0}, '{1}', datetime('now'));".format(version, path.name),
                "COMMIT;",
            ]
        )
        try:
            connection.executescript(script)
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        current = max(current, version)
    return current


def open_migrated(db_path: Path, migrations_dir: Path | None = None) -> sqlite3.Connection:
    connection = connect(db_path)
    migrate(connection, migrations_dir)
    return connection
