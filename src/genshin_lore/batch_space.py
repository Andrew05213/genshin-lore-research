"""Bounded, ownership-aware project space accounting for batch builds.

The gate protects the current decimal 90 GB working-space budget without treating
unrelated /tmp content as project data. Its authoritative broad scope counts
the repository plus convention-named directories and regular files. The old
directory-only governed scope remains available as a diagnostic. A
temporary root outside the repository must carry an ownership marker created by
ensure_owned_tmp_root before it can be cleaned.
"""

from __future__ import annotations

import json
import os
import stat
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


from .storage_policy import PROJECT_HARD_LIMIT_BYTES
PROJECT_SPACE_LIMIT_BYTES = PROJECT_HARD_LIMIT_BYTES
PROJECT_SPACE_WARNING_BYTES = 72 * 1024**3
OWNERSHIP_MARKER = ".genshin-lore-batch-owner.json"
OWNERSHIP_KIND = "genshin-lore-batch-temp-root"
MARKER_VERSION = 2
# Historical acceptance roots and batch-owned roots use these explicit names.
# They are counted for the project budget, but only marker-owned roots are
# eligible for cleanup. Unrelated /tmp entries are intentionally excluded.
PROJECT_TMP_PREFIXES = ("phase", "batch-", "genshin-lore-")


class SpaceGateError(RuntimeError):
    """The project space budget cannot safely accommodate the next write."""


@dataclass(frozen=True)
class SpaceRoot:
    path: str
    logical_bytes: int
    allocated_bytes: int
    file_count: int
    ownership_marker: bool


@dataclass(frozen=True)
class SpaceInventory:
    repo_root: str
    roots: tuple[SpaceRoot, ...]
    logical_bytes: int
    allocated_bytes: int
    warning: bool
    limit_bytes: int = PROJECT_SPACE_LIMIT_BYTES
    warning_bytes: int = PROJECT_SPACE_WARNING_BYTES

    @property
    def over_limit(self) -> bool:
        return self.allocated_bytes >= self.limit_bytes

    def to_dict(self) -> dict[str, object]:
        return {
            "repo_root": self.repo_root,
            "roots": [
                {
                    "path": item.path,
                    "logical_bytes": item.logical_bytes,
                    "allocated_bytes": item.allocated_bytes,
                    "file_count": item.file_count,
                    "ownership_marker": item.ownership_marker,
                }
                for item in self.roots
            ],
            "logical_bytes": self.logical_bytes,
            "allocated_bytes": self.allocated_bytes,
            "limit_bytes": self.limit_bytes,
            "warning_bytes": self.warning_bytes,
            "warning": self.warning,
            "over_limit": self.over_limit,
        }


def _marker_path(root: Path) -> Path:
    return root / OWNERSHIP_MARKER


def new_invocation_id() -> str:
    """Return an unguessable identity for one builder invocation."""

    return uuid.uuid4().hex


def _read_marker(root: Path) -> dict[str, object] | None:
    marker = _marker_path(root)
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(value, dict)
        or value.get("kind") != OWNERSHIP_KIND
        or value.get("version") != MARKER_VERSION
        or not isinstance(value.get("owner"), str)
        or not isinstance(value.get("invocation_id"), str)
        or not value.get("invocation_id")
    ):
        return None
    return value


def ensure_owned_tmp_root(
    root: str | Path, *,
    owner: str = "batch",
    invocation_id: str | None = None,
) -> Path:
    """Create/validate an explicitly owned temporary root.

    The marker carries a unique invocation identity and contains no credential
    or environment data. Existing markers may only be resumed by the same
    owner and invocation; another invocation can never take over the root.
    """

    path = Path(root).absolute()
    if path.is_symlink():
        raise SpaceGateError(f"temporary root must not be a symlink: {path}")
    path.mkdir(parents=True, exist_ok=True)
    marker = _marker_path(path)
    existing = _read_marker(path)
    if existing is not None:
        if existing.get("owner") != owner or (
            invocation_id is None
            or existing.get("invocation_id") != invocation_id
        ):
            raise SpaceGateError(
                f"temporary root is owned by another invocation: {path}"
            )
        return path
    if marker.exists():
        raise SpaceGateError(f"temporary root has an invalid ownership marker: {path}")
    try:
        existing_children = tuple(path.iterdir())
    except OSError as exc:
        raise SpaceGateError(f"temporary root cannot be inspected: {path}") from exc
    if existing_children:
        raise SpaceGateError(
            f"cannot take ownership of non-empty temporary root without a valid marker: {path}"
        )
    token = invocation_id or new_invocation_id()
    payload = (
        json.dumps(
            {
                "kind": OWNERSHIP_KIND,
                "owner": owner,
                "invocation_id": token,
                "version": MARKER_VERSION,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(
            marker,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError as exc:
        existing = _read_marker(path)
        if existing is None or existing.get("owner") != owner or existing.get(
            "invocation_id"
        ) != token:
            raise SpaceGateError(
                f"temporary root was claimed by another invocation: {path}"
            ) from exc
        return path
    try:
        os.write(descriptor, payload)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _is_convention_root(root: Path, convention_root: str | Path = "/tmp") -> bool:
    return (
        root.parent == Path(convention_root).absolute()
        and root.name.startswith(PROJECT_TMP_PREFIXES)
    )


def discover_project_tmp_roots(tmp_root: str | Path = "/tmp") -> tuple[Path, ...]:
    """Discover only convention-named project roots, never the whole /tmp tree."""

    root = Path(tmp_root).absolute()
    try:
        candidates = tuple(root.iterdir())
    except OSError as exc:
        raise SpaceGateError(
            f"project temporary-root discovery cannot inspect: {root}"
        ) from exc
    selected: list[Path] = []
    for item in candidates:
        try:
            info = item.lstat()
        except OSError as exc:
            raise SpaceGateError(
                f"project temporary-root discovery cannot lstat: {item}"
            ) from exc
        if (
            stat.S_ISDIR(info.st_mode)
            and not stat.S_ISLNK(info.st_mode)
            and item.name.startswith(PROJECT_TMP_PREFIXES)
        ):
            selected.append(item)
    return tuple(sorted(selected))


def discover_project_tmp_paths(tmp_root: str | Path = "/tmp") -> tuple[Path, ...]:
    """Discover attributable convention-named directories and regular files.

    Discovery grants accounting scope only; it never grants cleanup authority.
    """

    root = Path(tmp_root).absolute()
    try:
        candidates = tuple(root.iterdir())
    except OSError as exc:
        raise SpaceGateError(f"project temporary-path discovery cannot inspect: {root}") from exc
    selected: list[Path] = []
    for item in candidates:
        try:
            info = item.lstat()
        except OSError as exc:
            raise SpaceGateError(f"project temporary-path discovery cannot lstat: {item}") from exc
        if stat.S_ISLNK(info.st_mode) or not item.name.startswith(PROJECT_TMP_PREFIXES):
            continue
        if stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode):
            selected.append(item)
    return tuple(sorted(selected))


def _root_allowed(
    root: Path, repo_root: Path, *, require_marker: bool, allow_convention: bool = False,
    convention_root: str | Path = "/tmp",
) -> bool:
    if _within(root, repo_root):
        return True
    marker = _read_marker(root)
    if require_marker and marker is None and not (
        allow_convention and _is_convention_root(root, convention_root)
    ):
        raise SpaceGateError(f"explicit temporary root lacks ownership marker: {root}")
    return marker is not None or (allow_convention and _is_convention_root(root, convention_root))


def _walk_bytes(root: Path, seen: set[tuple[int, int]] | None = None) -> tuple[int, int, int]:
    logical = allocated = files = 0
    if seen is None:
        seen = set()
    try:
        root_info = root.lstat()
    except FileNotFoundError:
        return 0, 0, 0
    except OSError as exc:
        raise SpaceGateError(f"space inventory cannot lstat root: {root}") from exc
    if stat.S_ISLNK(root_info.st_mode):
        raise SpaceGateError(f"space inventory root must not be a symlink: {root}")
    if stat.S_ISREG(root_info.st_mode):
        identity = (int(root_info.st_dev), int(root_info.st_ino))
        if identity in seen:
            return 0, 0, 0
        seen.add(identity)
        blocks = int(getattr(root_info, "st_blocks", 0)) * 512
        return int(root_info.st_size), blocks if blocks else int(root_info.st_size), 1
    if not stat.S_ISDIR(root_info.st_mode):
        raise SpaceGateError(f"space inventory root is not a directory or regular file: {root}")

    def onerror(error: OSError) -> None:
        raise SpaceGateError(
            f"space inventory traversal failed at {getattr(error, 'filename', root)}"
        ) from error

    try:
        walker = os.walk(
            root, topdown=True, followlinks=False, onerror=onerror
        )
        for current, dirs, names in walker:
            filtered_dirs: list[str] = []
            for name in dirs:
                path = Path(current) / name
                try:
                    info = path.lstat()
                except OSError as exc:
                    raise SpaceGateError(f"space inventory cannot lstat: {path}") from exc
                if stat.S_ISLNK(info.st_mode):
                    continue
                if not stat.S_ISDIR(info.st_mode):
                    raise SpaceGateError(f"space inventory directory changed: {path}")
                filtered_dirs.append(name)
            dirs[:] = filtered_dirs
            for name in names:
                path = Path(current) / name
                try:
                    info = path.lstat()
                except OSError as exc:
                    raise SpaceGateError(f"space inventory cannot lstat: {path}") from exc
                if stat.S_ISDIR(info.st_mode):
                    raise SpaceGateError(f"space inventory directory listed as file: {path}")
                identity = (int(info.st_dev), int(info.st_ino))
                if identity in seen:
                    continue
                seen.add(identity)
                logical += int(info.st_size)
                blocks = int(getattr(info, "st_blocks", 0)) * 512
                allocated += blocks if blocks else int(info.st_size)
                files += 1
    except SpaceGateError:
        raise
    except OSError as exc:
        raise SpaceGateError(f"space inventory traversal failed at {root}") from exc
    return logical, allocated, files


def inventory(
    repo_root: str | Path,
    owned_tmp_roots: Iterable[str | Path] = (),
    *,
    require_markers: bool = False,
    include_convention_roots: bool = False,
    include_convention_files: bool = False,
    convention_root: str | Path = "/tmp",
) -> SpaceInventory:
    """Return scoped usage with shared inode and nested-root de-duplication."""

    repo = Path(repo_root).absolute()
    roots: list[SpaceRoot] = []
    root_paths = [repo]
    if include_convention_files:
        root_paths.extend(discover_project_tmp_paths(convention_root))
    elif include_convention_roots:
        root_paths.extend(discover_project_tmp_roots(convention_root))
    for candidate in owned_tmp_roots:
        path = Path(candidate).absolute()
        if path not in root_paths:
            root_paths.append(path)
    # Scan outer roots first. The shared inode set prevents nested roots and
    # hardlinks from inflating the inclusive project total.
    root_paths.sort(key=lambda item: (len(item.parts), str(item)))
    seen: set[tuple[int, int]] = set()
    for path in root_paths:
        if not _root_allowed(
            path, repo, require_marker=require_markers and path != repo,
            allow_convention=include_convention_roots,
            convention_root=convention_root,
        ):
            continue
        logical, allocated, files = _walk_bytes(path, seen)
        roots.append(SpaceRoot(str(path), logical, allocated, files, _read_marker(path) is not None))
    logical_total = sum(item.logical_bytes for item in roots)
    allocated_total = sum(item.allocated_bytes for item in roots)
    return SpaceInventory(
        repo_root=str(repo), roots=tuple(roots), logical_bytes=logical_total,
        allocated_bytes=allocated_total, warning=allocated_total >= PROJECT_SPACE_WARNING_BYTES,
    )


def broad_inventory(repo_root: str | Path, *, convention_root: str | Path = "/tmp") -> SpaceInventory:
    """Return the authoritative attributable project allocation."""

    return inventory(
        repo_root,
        include_convention_roots=True,
        include_convention_files=True,
        convention_root=convention_root,
    )


def _preserved(path: Path, preserve: Sequence[Path]) -> bool:
    return any(path == item or _within(path, item) or _within(item, path) for item in preserve)


def register_disposable_path(
    path: str | Path,
) -> tuple[Path, int, int]:
    """Capture an exact current-invocation disposable path and inode."""

    value = Path(path).absolute()
    try:
        info = value.lstat()
    except OSError as exc:
        raise SpaceGateError(f"disposable path cannot be registered: {value}") from exc
    return value, int(info.st_dev), int(info.st_ino)


def _disposable_entry(
    value: str | Path | tuple[str | Path, int, int],
) -> tuple[Path, tuple[int, int] | None]:
    if isinstance(value, tuple):
        if len(value) != 3:
            raise SpaceGateError("disposable inode registration must have three fields")
        path, device, inode = value
        return Path(path).absolute(), (int(device), int(inode))
    return Path(value).absolute(), None


def safe_cleanup(
    owned_tmp_roots: Iterable[str | Path],
    *,
    preserve_paths: Iterable[str | Path] = (),
    owner: str = "batch",
    invocation_id: str | None = None,
    disposable_paths: Iterable[str | Path | tuple[str | Path, int, int]] = (),
) -> list[str]:
    """Remove only explicitly registered paths from the current invocation.

    Accepted anchors, cache entries, arbitrary files and other invocations are
    never touched. A generic name such as .m1-* or staging is not disposable
    unless this invocation registered its exact path/inode.
    """

    preserve = tuple(Path(item).absolute() for item in preserve_paths)
    entries = tuple(_disposable_entry(item) for item in disposable_paths)
    removed: list[str] = []
    for candidate in owned_tmp_roots:
        root = Path(candidate).absolute()
        marker = _read_marker(root)
        if (
            marker is None
            or marker.get("owner") != owner
            or invocation_id is None
            or marker.get("invocation_id") != invocation_id
        ):
            continue
        cache_root = root / "cache"
        for child, expected_identity in entries:
            if (
                child == root
                or not _within(child, root)
                or child.name == OWNERSHIP_MARKER
                or _within(child, cache_root)
                or _preserved(child, preserve)
            ):
                continue
            try:
                info = child.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise SpaceGateError(
                    f"disposable path cannot be inspected: {child}"
                ) from exc
            if expected_identity is not None and (
                int(info.st_dev), int(info.st_ino)
            ) != expected_identity:
                continue
            try:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except OSError:
                continue
            removed.append(str(child))
    return removed


class ProjectSpaceGate:
    """Check current/projected allocated usage and stop at the inclusive limit."""

    def __init__(
        self,
        repo_root: str | Path,
        *,
        owned_tmp_roots: Iterable[str | Path] = (),
        accounting_tmp_roots: Iterable[str | Path] = (),
        cleanup_tmp_roots: Iterable[str | Path] | None = None,
        owner: str = "batch",
        invocation_id: str | None = None,
        disposable_paths: Iterable[str | Path | tuple[str | Path, int, int]] = (),
        preserve_paths: Iterable[str | Path] = (),
        discovery_root: str | Path = "/tmp",
    ) -> None:
        self.repo_root = Path(repo_root).absolute()
        self.discovery_root = Path(discovery_root).absolute()
        discovered = discover_project_tmp_paths(self.discovery_root)
        explicit_cleanup = tuple(
            Path(item).absolute()
            for item in (owned_tmp_roots if cleanup_tmp_roots is None else cleanup_tmp_roots)
        )
        explicit_accounting = tuple(Path(item).absolute() for item in accounting_tmp_roots)
        # Discovered convention roots are accounting-only.  They are never
        # implicitly handed to cleanup, because they may contain accepted
        # anchors or another builder's outputs.
        self.accounting_tmp_roots = tuple(
            dict.fromkeys((*discovered, *explicit_accounting, *explicit_cleanup))
        )
        self.cleanup_tmp_roots = tuple(dict.fromkeys(explicit_cleanup))
        self.owner = owner
        self.invocation_id = invocation_id
        self.disposable_paths = tuple(disposable_paths)
        self.preserve_paths = tuple(Path(item).absolute() for item in preserve_paths)
        self.events: list[dict[str, object]] = []
        self._blocked = False

    def check(self, label: str, *, projected_bytes: int = 0, cleanup: bool = True) -> SpaceInventory:
        if projected_bytes < 0:
            raise ValueError("projected_bytes must be non-negative")
        current = inventory(
            self.repo_root, self.accounting_tmp_roots, require_markers=False,
            include_convention_roots=True, include_convention_files=True,
            convention_root=self.discovery_root,
        )
        governed = inventory(
            self.repo_root, include_convention_roots=True,
            convention_root=self.discovery_root,
        )
        projected = current.allocated_bytes + projected_bytes
        host_available = shutil.disk_usage(self.repo_root).free
        event: dict[str, object] = {
            "label": label,
            "projected_bytes": projected_bytes,
            "allocated_bytes": current.allocated_bytes,
            "governed_allocated_bytes": governed.allocated_bytes,
            "projected_total_bytes": projected,
            "warning": projected >= PROJECT_SPACE_WARNING_BYTES,
            "host_available_bytes": host_available,
            "host_projected_headroom_bytes": host_available - projected_bytes,
        }
        if projected < PROJECT_SPACE_LIMIT_BYTES and projected_bytes <= host_available:
            event["status"] = "ok" if projected < PROJECT_SPACE_WARNING_BYTES else "warning"
            self.events.append(event)
            return current
        removed: list[str] = []
        if cleanup:
            removed = safe_cleanup(
                self.cleanup_tmp_roots,
                preserve_paths=self.preserve_paths,
                owner=self.owner,
                invocation_id=self.invocation_id,
                disposable_paths=self.disposable_paths,
            )
            current = inventory(
                self.repo_root, self.accounting_tmp_roots, require_markers=False,
                include_convention_roots=True, include_convention_files=True,
                convention_root=self.discovery_root,
            )
            governed = inventory(
                self.repo_root, include_convention_roots=True,
                convention_root=self.discovery_root,
            )
            projected = current.allocated_bytes + projected_bytes
        event["removed"] = removed
        event["allocated_bytes_after_cleanup"] = current.allocated_bytes
        event["governed_allocated_bytes_after_cleanup"] = governed.allocated_bytes
        event["projected_total_bytes_after_cleanup"] = projected
        host_available = shutil.disk_usage(self.repo_root).free
        event["host_available_bytes_after_cleanup"] = host_available
        if projected >= PROJECT_SPACE_LIMIT_BYTES or projected_bytes > host_available:
            event["status"] = "blocked"
            self._blocked = True
            self.events.append(event)
            raise SpaceGateError(
                f"project space/host-free limit reached at {label}: broad projected "
                f"{projected}/{PROJECT_SPACE_LIMIT_BYTES}, host free {host_available}, "
                f"additional {projected_bytes} bytes"
            )
        event["status"] = "cleaned"
        self.events.append(event)
        return current

    def report(self) -> dict[str, object]:
        latest = inventory(
            self.repo_root, self.accounting_tmp_roots, require_markers=False,
            include_convention_roots=True, include_convention_files=True,
            convention_root=self.discovery_root,
        )
        governed = inventory(
            self.repo_root, include_convention_roots=True,
            convention_root=self.discovery_root,
        )
        return {
            "limit_bytes": PROJECT_SPACE_LIMIT_BYTES,
            "warning_bytes": PROJECT_SPACE_WARNING_BYTES,
            "inventory": latest.to_dict(),
            "governed_inventory_diagnostic": governed.to_dict(),
            "events": list(self.events),
            # A blocked event is part of the invocation result even if a test
            # or cleanup hook subsequently makes the current inventory smaller.
            # Do not rewrite a hard-stop report into an apparent success.
            "status": "blocked" if self._blocked else ("warning" if latest.warning else "ok"),
        }


__all__ = [
    "MARKER_VERSION", "OWNERSHIP_KIND", "OWNERSHIP_MARKER", "PROJECT_SPACE_LIMIT_BYTES",
    "PROJECT_SPACE_WARNING_BYTES", "PROJECT_TMP_PREFIXES", "ProjectSpaceGate", "SpaceGateError", "SpaceInventory",
    "SpaceRoot", "broad_inventory", "discover_project_tmp_paths", "discover_project_tmp_roots", "ensure_owned_tmp_root", "inventory",
    "new_invocation_id", "register_disposable_path", "safe_cleanup",
]
