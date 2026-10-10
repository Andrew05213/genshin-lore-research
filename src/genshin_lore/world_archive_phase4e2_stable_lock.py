"""Stable sibling-lock promotion protocol for Phase 4E2 databases."""
from __future__ import annotations

import fcntl
import hashlib
import os
import stat
import tempfile
from pathlib import Path
from typing import Callable

from .world_archive_phase4e2_promotion import (
    WorldArchivePromotionError,
    assert_sidecars_absent,
    sync_directory,
)


def promotion_lock_path(database: Path) -> Path:
    """Return the project-wide stable sibling lock name for one DB."""
    return Path(f"{database}.lock")


def _descriptor_digest(descriptor: int) -> str:
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def promote_database(
    target: Path,
    staging: Path,
    expected: tuple[int, int, int, int, str],
    pre_replace_hook: Callable[[Path, Path], None] | None = None,
    post_replace_hook: Callable[[Path, Path], None] | None = None,
) -> None:
    """Serialize promotion on ``<db>.lock`` across the target rename."""
    lock_path = promotion_lock_path(target)
    lock_flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    lock_descriptor = os.open(lock_path, lock_flags, 0o644)
    locked = False
    target_descriptor = -1
    rollback_path: Path | None = None
    replaced = False
    published_identity: tuple[int, int] | None = None
    try:
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        locked = True
        lock_info = os.fstat(lock_descriptor)
        path_lock_info = os.lstat(lock_path)
        if (
            not stat.S_ISREG(lock_info.st_mode)
            or stat.S_ISLNK(path_lock_info.st_mode)
            or not stat.S_ISREG(path_lock_info.st_mode)
            or (lock_info.st_dev, lock_info.st_ino)
            != (path_lock_info.st_dev, path_lock_info.st_ino)
        ):
            raise WorldArchivePromotionError("stable database lock path identity mismatch")

        # Every final precondition is inside the stable lock. The target
        # descriptor remains only an identity/hash witness; it is not the
        # cross-process mutex because rename changes the target inode.
        assert_sidecars_absent(target, "under stable promotion lock")
        assert_sidecars_absent(staging, "under stable promotion lock")
        staging_descriptor = os.open(staging, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            staging_info = os.fstat(staging_descriptor)
            if not stat.S_ISREG(staging_info.st_mode):
                raise WorldArchivePromotionError("staging database is not regular")
            os.fsync(staging_descriptor)
            published_identity = (staging_info.st_dev, staging_info.st_ino)
        finally:
            os.close(staging_descriptor)

        target_descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        if pre_replace_hook is not None:
            pre_replace_hook(target, staging)
        info = os.fstat(target_descriptor)
        descriptor_identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if not stat.S_ISREG(info.st_mode) or descriptor_identity != expected[:4]:
            raise WorldArchivePromotionError("locked database descriptor identity mismatch")
        path_info = os.lstat(target)
        path_identity = (path_info.st_dev, path_info.st_ino, path_info.st_size, path_info.st_mtime_ns)
        if stat.S_ISLNK(path_info.st_mode) or not stat.S_ISREG(path_info.st_mode) or path_identity != expected[:4]:
            raise WorldArchivePromotionError("locked database path identity mismatch")
        if _descriptor_digest(target_descriptor) != expected[4]:
            raise WorldArchivePromotionError("locked database content hash mismatch")
        descriptor, rollback_name = tempfile.mkstemp(
            prefix=f".{target.name}.rollback-", suffix=".sqlite", dir=target.parent
        )
        os.close(descriptor)
        rollback_path = Path(rollback_name)
        rollback_path.unlink()
        os.link(target, rollback_path, follow_symlinks=False)
        rollback_info = os.lstat(rollback_path)
        if (
            stat.S_ISLNK(rollback_info.st_mode)
            or not stat.S_ISREG(rollback_info.st_mode)
            or (rollback_info.st_dev, rollback_info.st_ino)
            != (info.st_dev, info.st_ino)
        ):
            raise WorldArchivePromotionError("rollback link identity mismatch")
        assert_sidecars_absent(target, "immediately before stable-lock replace")
        assert_sidecars_absent(staging, "immediately before stable-lock replace")
        try:
            os.replace(staging, target)
            replaced = True
            sync_directory(target.parent)
            if post_replace_hook is not None:
                post_replace_hook(target, lock_path)
        except BaseException:
            if replaced and published_identity is not None and rollback_path is not None:
                try:
                    current = os.lstat(target)
                except FileNotFoundError:
                    current = None
                if current is not None and (
                    not stat.S_ISLNK(current.st_mode)
                    and stat.S_ISREG(current.st_mode)
                    and (current.st_dev, current.st_ino) == published_identity
                ):
                    os.replace(rollback_path, target)
                    rollback_path = None
                    sync_directory(target.parent)
                    restored = os.lstat(target)
                    if (
                        stat.S_ISLNK(restored.st_mode)
                        or not stat.S_ISREG(restored.st_mode)
                        or (restored.st_dev, restored.st_ino, restored.st_size, restored.st_mtime_ns) != expected[:4]
                    ):
                        raise WorldArchivePromotionError("database rollback identity mismatch")
                    if _descriptor_digest(target_descriptor) != expected[4]:
                        raise WorldArchivePromotionError("database rollback content hash mismatch")
            raise
    finally:
        if rollback_path is not None:
            try:
                rollback_path.unlink()
            except FileNotFoundError:
                pass
        if target_descriptor >= 0:
            os.close(target_descriptor)
        if locked:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        os.close(lock_descriptor)
