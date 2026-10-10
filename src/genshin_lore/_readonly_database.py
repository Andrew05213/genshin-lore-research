"""Small stdlib-only read-only SQLite connection seam."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any


class ReadonlyDatabase:
    """Minimal readonly database wrapper exposing .conn and .close()."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        path: Path,
        file_identity: tuple[int, int, int, int] | None = None,
    ) -> None:
        self.conn = conn
        self.path = path
        self.file_identity = file_identity

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> ReadonlyDatabase:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


def open_readonly_database(path: str | Path) -> ReadonlyDatabase:
    """Open a verified existing SQLite database in read-only query mode."""

    db_path = Path(path).resolve()
    if not db_path.is_file():
        raise FileNotFoundError(f"Database file not found: {db_path}")

    before_info = os.stat(db_path)
    before = (
        before_info.st_dev, before_info.st_ino,
        before_info.st_size, before_info.st_mtime_ns,
    )
    uri = f"{db_path.as_uri()}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    after_info = os.stat(db_path)
    after = (
        after_info.st_dev, after_info.st_ino,
        after_info.st_size, after_info.st_mtime_ns,
    )
    if after != before:
        conn.close()
        raise OSError("database changed while opening immutable readonly connection")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return ReadonlyDatabase(conn, db_path, before)


__all__ = ["ReadonlyDatabase", "open_readonly_database"]
