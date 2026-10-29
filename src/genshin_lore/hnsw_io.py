"""Native HNSW binary I/O without Windows narrow-string path conversion.

Only file transport is shared with the oracle; no vector/selection logic lives
here. Failed private files remain attributable to the project space inventory.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile

from .artifact_io import require_existing_file, require_new_output
from .platform_io import sync_directory

_WINDOWS = os.name == "nt"


def _native_path(directory: Path) -> str:
    """Return an ASCII filename, using a short directory alias if necessary."""
    native = str(directory / "index.hnsw")
    if _WINDOWS and not native.isascii():
        import ctypes
        from ctypes import wintypes

        short_path = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
        short_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        short_path.restype = wintypes.DWORD
        size = short_path(str(directory), None, 0)
        if not size:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_unicode_buffer(size)
        written = short_path(str(directory), buffer, size)
        if not written or written >= size:
            raise ctypes.WinError(ctypes.get_last_error())
        native = str(Path(buffer.value) / "index.hnsw")
        if not native.isascii():
            raise OSError("hnswlib needs an ASCII temporary path; short alias unavailable")
    return native


def _copy_fsynced(source: Path, target: Path) -> None:
    with source.open("rb") as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer, length=1 << 20)
        writer.flush()
        os.fsync(writer.fileno())


def save_index(index, path: str | Path) -> None:
    """Save the native format and publish complete bytes without overwriting."""
    target = require_new_output(path, "HNSW artifact")
    expected = index.index_file_size()
    work = Path(tempfile.mkdtemp(prefix="genshin-lore-hnsw-"))
    staged = work / "index.hnsw"
    publication = None
    try:
        if min(shutil.disk_usage(work).free, shutil.disk_usage(target.parent).free) < expected:
            raise OSError("insufficient temporary/output space for HNSW binary")
        index.save_index(_native_path(work))
        if not staged.is_file() or staged.stat().st_size != expected or expected <= 0:
            raise OSError("HNSW save did not produce the complete native binary")
        with staged.open("r+b") as stream:
            os.fsync(stream.fileno())
        # The second temporary file is on the output volume for atomic linking.
        descriptor, name = tempfile.mkstemp(prefix=".hnsw-", suffix=".partial", dir=target.parent)
        publication = Path(name)
        with os.fdopen(descriptor, "wb") as writer, staged.open("rb") as reader:
            shutil.copyfileobj(reader, writer, length=1 << 20)
            writer.flush()
            os.fsync(writer.fileno())
        if publication.stat().st_size != expected:
            raise OSError("HNSW publication copy is incomplete")
        os.link(publication, target)
        sync_directory(target.parent)
    except BaseException as error:
        error.add_note(f"HNSW private files retained: {work}; publication: {publication}")
        raise
    # Only this successful invocation's private files are removed.
    publication.unlink()
    staged.unlink()
    work.rmdir()


def load_index(index, path: str | Path, **kwargs) -> None:
    """Load the exact artifact bytes using the native parser and its checks."""
    source = require_existing_file(path, "HNSW artifact")
    if not _WINDOWS or str(source.resolve()).isascii():
        index.load_index(str(source), **kwargs)
        return
    work = Path(tempfile.mkdtemp(prefix="genshin-lore-hnsw-"))
    staged = work / "index.hnsw"
    try:
        if shutil.disk_usage(work).free < source.stat().st_size:
            raise OSError("insufficient temporary space for HNSW load")
        _copy_fsynced(source, staged)
        index.load_index(_native_path(work), **kwargs)
    except BaseException as error:
        error.add_note(f"HNSW load private files retained: {work}")
        raise
    staged.unlink()
    work.rmdir()
