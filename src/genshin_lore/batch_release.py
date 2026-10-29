"""Small release-correctness primitives for batch-owned SQLite outputs.

The batch builders deliberately publish only into an absent target.  A stable
sibling lock serializes the final existence check and rename, while the
staging file remains private and same-directory until it is complete.  These
helpers do not decide family identity or projection semantics.
"""

from __future__ import annotations

from .platform_io import lock_exclusive, unlock, sync_directory
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator


class BatchReleaseError(RuntimeError):
    """A batch output cannot be published without clobbering or ambiguity."""


def sidecars(path: Path) -> tuple[Path, Path, Path]:
    return tuple(Path(f"{path}{suffix}") for suffix in ("-journal", "-wal", "-shm"))


def _absolute(value: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(value)))


def _aliases(left: Path, right: Path) -> bool:
    if left == right:
        return True
    try:
        if left.resolve(strict=False) == right.resolve(strict=False):
            return True
    except (OSError, RuntimeError):
        pass
    try:
        return os.path.samefile(left, right)
    except (FileNotFoundError, OSError):
        return False


DERIVED_SUFFIXES = ("-journal", "-wal", "-shm", ".lock")


def _derived_paths(path: Path) -> tuple[tuple[str, Path], ...]:
    return tuple((suffix, Path(f"{path}{suffix}")) for suffix in DERIVED_SUFFIXES)


def _path_graph(semantic_paths: Iterable[tuple[str, Path]]) -> tuple[tuple[str, Path], ...]:
    """Expand semantic paths with SQLite sidecars and the stable sibling lock."""

    graph: list[tuple[str, Path]] = []
    for label, path in semantic_paths:
        graph.append((label, path))
        graph.extend((f"{label}{suffix}", derived) for suffix, derived in _derived_paths(path))
    return tuple(graph)


def _assert_path_graph(semantic_paths: Iterable[tuple[str, Path]]) -> None:
    graph = _path_graph(semantic_paths)
    for label, path in graph:
        _assert_no_symlink_components(path, label)
    for index, (left_name, left) in enumerate(graph):
        for right_name, right in graph[index + 1:]:
            if _aliases(left, right):
                raise BatchReleaseError(
                    f"batch path alias: {left_name}={left} and {right_name}={right}"
                )


def _assert_no_symlink_components(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise BatchReleaseError(f"{label} contains a symlink component: {path}")


def _assert_sidecars_absent(path: Path, label: str) -> None:
    present = [str(item) for item in sidecars(path) if os.path.lexists(item)]
    if present:
        raise BatchReleaseError(f"SQLite sidecars present for {label}: {present}")


def assert_output_paths(
    target: str | Path,
    *,
    inputs: Iterable[tuple[str, str | Path]] = (),
    staging: str | Path | None = None,
    target_must_be_absent: bool = True,
) -> tuple[Path, Path | None, dict[str, Path]]:
    """Check output/input aliases and SQLite sidecars before any write."""

    target_path = _absolute(target)
    staging_path = None if staging is None else _absolute(staging)
    normalized = {name: _absolute(path) for name, path in inputs}
    semantic_paths = [("target", target_path), *normalized.items()]
    if staging_path is not None:
        semantic_paths.append(("staging", staging_path))
    _assert_path_graph(semantic_paths)
    for name, path in semantic_paths:
        _assert_sidecars_absent(path, name)
    if target_must_be_absent and os.path.lexists(target_path):
        raise BatchReleaseError(f"batch output target must be absent: {target_path}")
    if staging_path is not None and os.path.lexists(staging_path):
        info = os.lstat(staging_path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise BatchReleaseError(f"batch staging must be a regular file: {staging_path}")
    return target_path, staging_path, normalized


@contextmanager
def stable_sibling_lock(
    target: str | Path,
    *,
    inputs: Iterable[tuple[str, str | Path]] = (),
) -> Iterator[Path]:
    """Hold an identity-checked ``<target>.lock`` exclusive lock."""

    target_path = _absolute(target)
    lock_path = Path(f"{target_path}.lock")
    semantic_inputs = tuple(inputs)
    assert_output_paths(
        target_path, inputs=semantic_inputs, target_must_be_absent=False
    )
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    locked = False
    try:
        lock_exclusive(descriptor)
        locked = True
        assert_output_paths(
            target_path, inputs=semantic_inputs, target_must_be_absent=False
        )
        descriptor_info = os.fstat(descriptor)
        path_info = os.lstat(lock_path)
        if (
            not stat.S_ISREG(descriptor_info.st_mode)
            or stat.S_ISLNK(path_info.st_mode)
            or not stat.S_ISREG(path_info.st_mode)
            or (descriptor_info.st_dev, descriptor_info.st_ino)
            != (path_info.st_dev, path_info.st_ino)
        ):
            raise BatchReleaseError("stable sibling lock identity mismatch")
        yield lock_path
    finally:
        if locked:
            unlock(descriptor)
        os.close(descriptor)


def create_private_staging(target: str | Path, *, prefix: str) -> tuple[int, Path]:
    """Create a same-directory 0600 staging inode without exposing a target."""

    target_path = _absolute(target)
    assert_output_paths(target_path, target_must_be_absent=False)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=prefix, suffix=".sqlite", dir=target_path.parent
    )
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, Path(name)


def fsync_file(path: str | Path) -> None:
    path_value = _absolute(path)
    # Windows _commit/FlushFileBuffers requires a write-capable descriptor.
    flags = os.O_RDWR if os.name == 'nt' else os.O_RDONLY
    descriptor = os.open(path_value, flags | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(descriptor)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise BatchReleaseError(f"staging output is not a regular file: {path_value}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish_absent(
    target: str | Path,
    staging: str | Path,
    *,
    inputs: Iterable[tuple[str, str | Path]] = (),
) -> None:
    """Publish without replacing a competitor; retain files on flush failure.

    An existing target is not proof of successful durability or verification.
    Callers must require their independent verification and stage receipt.
    """

    semantic_inputs = tuple(inputs) + (("staging", staging),)
    target_path, staging_path, _ = assert_output_paths(
        target, inputs=inputs, staging=staging, target_must_be_absent=False
    )
    assert staging_path is not None
    fsync_file(staging_path)
    with stable_sibling_lock(target_path, inputs=semantic_inputs):
        # This check is intentionally repeated under the stable lock.  A
        # caller racing the preflight can create the target between checks;
        # that competitor must win or make this builder fail closed.
        assert_output_paths(
            target_path, inputs=semantic_inputs, target_must_be_absent=True
        )
        before = os.lstat(staging_path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise BatchReleaseError("staging output changed to a non-regular file")
        os.replace(staging_path, target_path)
        # Failure remains unqualified; keep the published inode for diagnosis.
        sync_directory(target_path.parent)


__all__ = [
    "BatchReleaseError",
    "assert_output_paths",
    "create_private_staging",
    "fsync_file",
    "publish_absent",
    "sidecars",
    "stable_sibling_lock",
    "sync_directory",
]
