"""SQLite access for the canonical database built by the data pipeline."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE = ROOT / "data" / "db" / "dishit.db"
DATABASE_ENV_VAR = "DISHIT_DATABASE"
REQUIRED_TABLES = frozenset({"restaurants", "dishes", "reviews", "dish_mentions"})


def database_path() -> Path:
    """Return the configured canonical database path.

    Relative environment values are rooted at the repository so deployment
    configuration behaves the same regardless of the process working directory.
    """
    configured = Path(os.environ.get(DATABASE_ENV_VAR, str(DEFAULT_DATABASE))).expanduser()
    return configured if configured.is_absolute() else ROOT / configured


def init_db() -> None:
    """Verify that the configured database is a canonical DishIt database."""
    path = database_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"Canonical DishIt database not found: {path}. "
            f"Run data/db/load_db.py and data/calculate/calculate.py, or set {DATABASE_ENV_VAR}."
        )

    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    missing = REQUIRED_TABLES - tables
    if missing:
        raise RuntimeError(
            "Database is not a canonical DishIt database; "
            f"missing tables: {', '.join(sorted(missing))}."
        )


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency yielding one request-scoped canonical connection."""
    connection = sqlite3.connect(database_path())
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()
