"""POSIX locked promotion and report publication for Phase 4E2."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


class WorldArchivePromotionError(RuntimeError):
    pass


def sidecars(path: Path) -> tuple[Path, Path, Path]:
    return tuple(Path(f"{path}{suffix}") for suffix in ("-journal", "-wal", "-shm"))


def assert_sidecars_absent(path: Path, stage: str) -> None:
    found = [str(item) for item in sidecars(path) if os.path.lexists(item)]
    if found:
        raise WorldArchivePromotionError(f"SQLite sidecars present {stage}: {found}")


def sync_directory(directory: Path) -> None:
    descriptor = os.open(os.fspath(directory), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)




def _same_path(left: Path, right: Path) -> bool:
    if left.resolve(strict=False) == right.resolve(strict=False):
        return True
    try:
        return os.path.samefile(left, right)
    except (FileNotFoundError, OSError):
        return False


def assert_report_path_safe(report_path: str | Path, database_path: str | Path) -> None:
    target = Path(os.path.abspath(os.fspath(report_path)))
    database = Path(os.path.abspath(os.fspath(database_path)))
    if _same_path(target, database):
        raise WorldArchivePromotionError("report path aliases the database path")


def write_report_atomically(report_path: str | Path, database_path: str | Path, report: Mapping[str, Any]) -> None:
    """Publish JSON without ever opening the database path as a report."""
    target = Path(os.path.abspath(os.fspath(report_path)))
    database = Path(os.path.abspath(os.fspath(database_path)))
    assert_report_path_safe(target, database)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.phase4e2-", suffix=".report", dir=target.parent)
    staging = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        assert_report_path_safe(target, database)
        os.replace(staging, target)
        sync_directory(target.parent)
    finally:
        try:
            staging.unlink()
        except FileNotFoundError:
            pass
