"""Operating-system primitives shared by source builds, not evidence logic."""
from __future__ import annotations

import os
from pathlib import Path
import time


def lock_exclusive(descriptor, *, blocking=True):
    """Lock the stable sibling lock file; never substitute a no-op on Windows."""
    fd = descriptor if isinstance(descriptor, int) else descriptor.fileno()
    if os.name != "nt":
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return
    import errno
    import msvcrt
    position = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        while True:
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if not blocking or exc.errno not in (errno.EACCES, errno.EDEADLK):
                    raise
                time.sleep(0.05)
    finally:
        os.lseek(fd, position, os.SEEK_SET)


def unlock(descriptor):
    fd = descriptor if isinstance(descriptor, int) else descriptor.fileno()
    if os.name != "nt":
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)
        return
    import msvcrt
    position = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.lseek(fd, position, os.SEEK_SET)


def sync_directory(directory: Path):
    """POSIX directory durability; Windows has no portable directory fsync.

    Publication must still use exclusive creation/linking and fsync the file.
    This is a durability boundary, not permission to overwrite an output.
    """
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def peak_rss_bytes():
    if os.name != "nt":
        import resource
        import sys
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value if sys.platform == "darwin" else value * 1024)
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    get_info = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
    get_info.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
    get_info.restype = wintypes.BOOL
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not get_info(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return int(counters.PeakWorkingSetSize)
