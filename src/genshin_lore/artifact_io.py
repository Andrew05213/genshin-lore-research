"""Small no-clobber helpers for operator-facing JSON artifacts."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable


def normalized_path(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).resolve(strict=False)))


def require_existing_file(path: str | Path, label: str) -> Path:
    target = Path(path)
    if not target.is_file() or target.is_symlink():
        raise ValueError(f"{label} is not an existing regular file: {target}")
    return target


def require_new_output(
    path: str | Path,
    label: str,
    *,
    aliases: Iterable[str | Path] = (),
) -> Path:
    target = Path(path)
    if os.path.lexists(target):
        raise ValueError(f"{label} already exists: {target}")
    try:
        parent = target.parent.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} parent does not exist: {target.parent}") from exc
    if not parent.is_dir():
        raise ValueError(f"{label} parent is not a directory: {target.parent}")
    normalized = normalized_path(target)
    for alias in aliases:
        if normalized == normalized_path(alias):
            raise ValueError(f"{label} aliases an input/output path: {alias}")
    return target


def write_json_exclusive(path: str | Path, value: Any) -> None:
    """Durably publish JSON without overwriting even under an output race."""

    target = Path(path)
    require_new_output(target, "JSON report")
    raw = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    publication_committed = False
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, target)
        publication_committed = True
        try:
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                try:
                    os.fsync(directory_fd)
                except OSError:
                    # The hard link is the irreversible no-clobber commit
                    # point.  Directory fsync is not portable (notably on
                    # Windows), so a post-commit durability warning must not
                    # be reported as a failed publication.
                    pass
            finally:
                try:
                    os.close(directory_fd)
                except OSError:
                    pass
        except OSError:
            pass
    finally:
        try:
            temporary.unlink()
        except OSError:
            if not publication_committed:
                raise
