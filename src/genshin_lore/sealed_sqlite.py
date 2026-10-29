"""Bounded mapped reads of closed artifacts; callers retain seal/oracle checks."""
from pathlib import Path
import sqlite3


MMAP_LIMIT_BYTES = 2 * 1024**3


def open_immutable_readonly(path, *, mmap_bytes=MMAP_LIMIT_BYTES):
    """Open an already qualified file without changing its schema or bytes.

    This is a physical reader, not proof that an artifact is accepted. Never
    use it for a writer, staging database, VACUUM, or a database with sidecars.
    SQLite may clamp the mapping or disable it on unsupported builds; all
    integrity and source-rowset checks still run through ordinary reads.
    """
    if type(mmap_bytes) is not int or not 0 <= mmap_bytes <= MMAP_LIMIT_BYTES:
        raise ValueError("mmap limit outside the bounded read-only range")
    path = Path(path).absolute()
    if path.is_symlink() or not path.is_file():
        raise ValueError("sealed SQLite requires an existing regular file: " + str(path))

    def check_sidecars():
        if any(Path(str(path) + suffix).exists() or Path(str(path) + suffix).is_symlink()
               for suffix in ("-wal", "-shm", "-journal")):
            raise ValueError("sealed SQLite sidecar: " + str(path))

    check_sidecars()
    conn = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        result = conn.execute(f"PRAGMA mmap_size={mmap_bytes}").fetchone()
        if result is None or not 0 <= result[0] <= mmap_bytes:
            raise ValueError("SQLite mmap setting outside the requested bound")
        check_sidecars()
        return conn
    except BaseException:
        conn.close()
        raise
