"""Read-only SQLite helpers for local source inspection."""

from pathlib import Path
import sqlite3
from typing import Mapping, Tuple
import urllib.parse


class SqliteReadError(RuntimeError):
    """A sanitized SQLite read failure containing no source details."""

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


def _sqlite_error(error: sqlite3.DatabaseError) -> SqliteReadError:
    return SqliteReadError("sqlite_error:" + type(error).__name__)


def quote_identifier(value: str) -> str:
    """Quote a SQLite identifier by doubling embedded quote characters."""
    return '"' + value.replace('"', '""') + '"'


def open_sqlite_readonly(path: Path) -> sqlite3.Connection:
    """Open a SQLite database through an encoded read-only file URI."""
    quoted = urllib.parse.quote(str(path.resolve()), safe="/:\\")
    connection = None
    read_error = None
    try:
        connection = sqlite3.connect("file:" + quoted + "?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
    except sqlite3.DatabaseError as error:
        read_error = _sqlite_error(error)
        if connection is not None:
            try:
                connection.close()
            except sqlite3.DatabaseError:
                pass
    if read_error is not None:
        raise read_error
    return connection


def sqlite_schema(
    connection: sqlite3.Connection,
) -> Mapping[str, Tuple[str, ...]]:
    """Return ordered table/view names and their column names."""
    schema = {}
    read_error = None
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type IN ('table', 'view') ORDER BY name"
        )
        for (name,) in rows:
            columns = connection.execute(
                "PRAGMA table_info(" + quote_identifier(name) + ")"
            )
            schema[name] = tuple(row[1] for row in columns)
    except sqlite3.DatabaseError as error:
        read_error = _sqlite_error(error)
    if read_error is not None:
        raise read_error
    return schema
