"""Database access for the canonical database built by the data pipeline.

Three modes, selected by DISHIT_DB_MODE:

    file     stdlib sqlite3 against a local path  — the default: dev, tests, CI
    replica  embedded libSQL replica synced from Turso — production
    remote   libSQL over HTTP to Turso — smoke-testing the hosted copy

Every mode returns rows as plain dicts. The libSQL driver returns tuples and has
no row_factory (verified against libsql 0.1.11), so normalising here is what lets
the same endpoint code run unchanged against all three.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE = ROOT / "data" / "db" / "dishit.db"
DEFAULT_REPLICA = ROOT / "data" / "db" / "replica.db"

DATABASE_ENV_VAR = "DISHIT_DATABASE"
MODE_ENV_VAR = "DISHIT_DB_MODE"
REPLICA_ENV_VAR = "DISHIT_REPLICA_PATH"
URL_ENV_VAR = "TURSO_DATABASE_URL"
TOKEN_ENV_VAR = "TURSO_AUTH_TOKEN"
SYNC_INTERVAL_ENV_VAR = "DISHIT_SYNC_INTERVAL"

MODES = ("file", "replica", "remote")
REQUIRED_TABLES = frozenset({"restaurants", "dishes", "reviews", "dish_mentions"})

# Set once by init_db() in replica/remote mode; those connections are expensive to
# open and must be shared, unlike the cheap per-request sqlite3 handles.
_shared: Database | None = None


def mode() -> str:
    """Return the configured connection mode."""
    configured = os.environ.get(MODE_ENV_VAR, "file").strip().lower()
    if configured not in MODES:
        raise RuntimeError(
            f"{MODE_ENV_VAR} must be one of {', '.join(MODES)}; got {configured!r}."
        )
    return configured


def database_path() -> Path:
    """Return the configured canonical database path (file mode).

    Relative environment values are rooted at the repository so deployment
    configuration behaves the same regardless of the process working directory.
    """
    configured = Path(os.environ.get(DATABASE_ENV_VAR, str(DEFAULT_DATABASE))).expanduser()
    return configured if configured.is_absolute() else ROOT / configured


def replica_path() -> Path:
    """Return the local file backing the embedded replica."""
    configured = Path(os.environ.get(REPLICA_ENV_VAR, str(DEFAULT_REPLICA))).expanduser()
    return configured if configured.is_absolute() else ROOT / configured


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set when {MODE_ENV_VAR}={mode()}.")
    return value


class Database:
    """A connection plus the row-shaping every mode shares.

    `rows` and `one` return dicts, so callers index by column name whether the
    underlying driver is sqlite3 (which could do that itself via sqlite3.Row) or
    libSQL (which returns bare tuples and has no row_factory).
    """

    def __init__(self, connection, *, lock: bool = False, syncable: bool = False) -> None:
        self._connection = connection
        # A shared connection is reached from FastAPI's threadpool, so its use has
        # to be serialised; a per-request one is already confined to one thread.
        self._lock = threading.Lock() if lock else nullcontext()
        # Only an embedded replica pulls frames. A remote connection carries a .sync
        # attribute too, but calling it raises, so the caller states what this is
        # rather than the class guessing from the driver's surface.
        self._syncable = syncable

    def rows(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            cursor = self._connection.execute(sql, params)
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        with self._lock:
            cursor = self._connection.execute(sql, params)
            row = cursor.fetchone()
            if row is None:
                return None
            columns = [column[0] for column in cursor.description]
            return dict(zip(columns, row, strict=True))

    def sync(self) -> None:
        """Pull the latest frames from Turso; a no-op outside replica mode."""
        if self._syncable:
            with self._lock:
                self._connection.sync()

    def close(self) -> None:
        self._connection.close()


def _connect_file() -> Database:
    # check_same_thread=False because FastAPI runs a sync `yield` dependency and the
    # endpoint body on different threadpool threads, so the connection opened here is
    # used from another one. Each request still gets its own connection and never
    # shares it, so the guard this drops is not protecting anything.
    connection = sqlite3.connect(database_path(), check_same_thread=False)
    connection.execute("PRAGMA foreign_keys = ON")
    return Database(connection)


def _connect_libsql() -> Database:
    import libsql

    token = _required_env(TOKEN_ENV_VAR)
    url = _required_env(URL_ENV_VAR)

    if mode() == "remote":
        connection = libsql.connect(url, auth_token=token, _check_same_thread=False)
    else:
        path = replica_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = libsql.connect(
            str(path),
            sync_url=url,
            auth_token=token,
            sync_interval=float(os.environ.get(SYNC_INTERVAL_ENV_VAR, "60")),
            _check_same_thread=False,
        )
    return Database(connection, lock=True, syncable=mode() == "replica")


def init_db() -> None:
    """Open the configured database and verify it is a canonical DishIt one."""
    global _shared

    active = mode()
    if active == "file":
        path = database_path()
        if not path.is_file():
            raise FileNotFoundError(
                f"Canonical DishIt database not found: {path}. "
                f"Run data/db/load_db.py and data/calculate/calculate.py, or set "
                f"{DATABASE_ENV_VAR}."
            )
        database = _connect_file()
        close_after = True
    else:
        database = _connect_libsql()
        # Populate the replica before serving; a first boot has an empty local file.
        database.sync()
        _shared = database
        close_after = False

    try:
        tables = {
            row["name"]
            for row in database.rows("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        missing = REQUIRED_TABLES - tables
        if missing:
            raise RuntimeError(
                "Database is not a canonical DishIt database; "
                f"missing tables: {', '.join(sorted(missing))}."
            )
    finally:
        if close_after:
            database.close()


def shutdown_db() -> None:
    """Release the shared connection, if this mode opened one."""
    global _shared
    if _shared is not None:
        _shared.close()
        _shared = None


def get_db() -> Iterator[Database]:
    """FastAPI dependency yielding a canonical database handle.

    File mode keeps the original per-request connection. The libSQL modes reuse the
    one opened at startup — reopening an embedded replica per request would resync
    it, and Turso's docs warn against opening the replica file while it syncs.
    """
    if mode() != "file":
        if _shared is None:
            raise RuntimeError("init_db() must run before serving in replica/remote mode.")
        yield _shared
        return

    database = _connect_file()
    try:
        yield database
    finally:
        database.close()
