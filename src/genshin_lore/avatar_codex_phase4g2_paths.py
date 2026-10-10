"""Small independent path/alias preflight used by the G2 release verifier."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Mapping


class AvatarCodexPhase4G2PathError(RuntimeError):
    """A verifier input or protected derived path is unsafe."""


def _absolute(value: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(value)))


def _aliases(left: Path, right: Path) -> bool:
    try:
        if left.resolve(strict=False) == right.resolve(strict=False):
            return True
    except (OSError, RuntimeError):
        pass
    try:
        return os.path.samefile(left, right)
    except (FileNotFoundError, OSError):
        return False


def _assert_no_symlink_components(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise AvatarCodexPhase4G2PathError(f"{label} contains a symlink component")


def assert_path_matrix(
    paths: Mapping[str, str | Path],
    *,
    protected_database_names: tuple[str, ...] = ("database", "baseline"),
    input_files: tuple[str, ...] = (),
    input_directories: tuple[str, ...] = (),
) -> dict[str, Path]:
    """Reject lexical/resolved/samefile aliases before read-only verification."""
    normalized = {name: _absolute(value) for name, value in paths.items()}
    for name, path in normalized.items():
        _assert_no_symlink_components(path, name)
    items = list(normalized.items())
    for index, (left_name, left) in enumerate(items):
        for right_name, right in items[index + 1:]:
            if _aliases(left, right):
                raise AvatarCodexPhase4G2PathError(
                    f"path alias between {left_name} and {right_name}"
                )
    hazards: list[tuple[str, Path]] = []
    for database_name in protected_database_names:
        database = normalized[database_name]
        hazards.extend(
            (f"{database_name}:{suffix}", Path(f"{database}{suffix}"))
            for suffix in (".lock", "-journal", "-wal", "-shm")
        )
    for name, path in items:
        for hazard_name, hazard in hazards:
            if _aliases(path, hazard):
                raise AvatarCodexPhase4G2PathError(
                    f"{name} aliases protected derived path {hazard_name}"
                )
    for name in input_files:
        path = normalized[name]
        try:
            info = os.lstat(path)
        except FileNotFoundError as exc:
            raise AvatarCodexPhase4G2PathError(f"required input file is absent: {name}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise AvatarCodexPhase4G2PathError(f"required input is not a regular file: {name}")
    for name in input_directories:
        path = normalized[name]
        try:
            info = os.lstat(path)
        except FileNotFoundError as exc:
            raise AvatarCodexPhase4G2PathError(f"required input directory is absent: {name}") from exc
        if not stat.S_ISDIR(info.st_mode):
            raise AvatarCodexPhase4G2PathError(f"required input is not a directory: {name}")
    return normalized


__all__ = ["AvatarCodexPhase4G2PathError", "assert_path_matrix"]
