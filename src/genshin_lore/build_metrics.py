"""Small, dependency-free process and file metrics used by batch builds.

The module deliberately distinguishes operating-system counters from inferred
application I/O.  Linux ``/proc/<pid>/io`` ``read_bytes``/``write_bytes`` are
storage-layer accounting counters and may remain zero when data is served from
the page cache; ``rchar``/``wchar`` are syscall byte counters and include more
than regular-file payloads.
"""

from __future__ import annotations

import hashlib
import os
import resource
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator


MIB = 1024 * 1024
HASH_CHUNK_BYTES = MIB


def unavailable(reason: str) -> dict[str, str]:
    """Return an explicit unavailable metric instead of a made-up value."""

    return {"reason": reason, "status": "unavailable"}


def _linux_proc_io(pid: int | None = None) -> dict[str, int] | None:
    path = Path(f"/proc/{pid or os.getpid()}/io")
    try:
        rows = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError):
        return None
    result: dict[str, int] = {}
    for row in rows:
        key, separator, raw = row.partition(":")
        if not separator:
            continue
        try:
            result[key] = int(raw.strip())
        except ValueError:
            return None
    return result


def _proc_delta(before: dict[str, int] | None, after: dict[str, int] | None) -> dict[str, Any]:
    if before is None or after is None:
        return unavailable("Linux /proc process I/O counters are unavailable")
    keys = ("rchar", "wchar", "syscr", "syscw", "read_bytes", "write_bytes", "cancelled_write_bytes")
    return {
        "status": "available",
        "semantics": {
            "rchar_wchar": "syscall characters; includes non-file descriptors and may count cached reads",
            "read_bytes_write_bytes": "storage-layer bytes attributed by Linux; cached reads may add zero",
            "syscr_syscw": "read-like and write-like syscall counts",
        },
        "values": {key: max(0, after.get(key, 0) - before.get(key, 0)) for key in keys},
    }


def _max_rss_bytes(usage: resource.struct_rusage) -> int:
    # Linux reports KiB; macOS reports bytes.  M0 is sealed to Linux, but keep
    # the conversion explicit rather than silently presenting the wrong unit.
    if os.uname().sysname == "Darwin":
        return int(usage.ru_maxrss)
    return int(usage.ru_maxrss) * 1024


@dataclass(frozen=True)
class MeasurementStart:
    wall_ns: int
    usage: resource.struct_rusage
    proc_io: dict[str, int] | None
    rss_at_start_bytes: int | None


def _current_rss_bytes() -> int | None:
    try:
        for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def start_measurement() -> MeasurementStart:
    return MeasurementStart(
        time.perf_counter_ns(),
        resource.getrusage(resource.RUSAGE_SELF),
        _linux_proc_io(),
        _current_rss_bytes(),
    )


def finish_measurement(start: MeasurementStart) -> dict[str, Any]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    rss_at_end = _current_rss_bytes()
    return {
        "cpu_system_seconds": round(max(0.0, usage.ru_stime - start.usage.ru_stime), 6),
        "cpu_user_seconds": round(max(0.0, usage.ru_utime - start.usage.ru_utime), 6),
        "linux_proc_io": _proc_delta(start.proc_io, _linux_proc_io()),
        "peak_rss_bytes": _max_rss_bytes(usage),
        "peak_rss_semantics": "process high-water mark; not an interval delta",
        "rss_at_end_bytes": rss_at_end if rss_at_end is not None else unavailable("/proc/self/status unavailable"),
        "rss_at_start_bytes": start.rss_at_start_bytes if start.rss_at_start_bytes is not None else unavailable("/proc/self/status unavailable"),
        "wall_seconds": round((time.perf_counter_ns() - start.wall_ns) / 1_000_000_000, 6),
    }


@contextmanager
def sqlite_statement_counter(connection: sqlite3.Connection) -> Iterator[Callable[[], int]]:
    """Count statements issued through one explicitly instrumented connection."""

    count = 0

    def trace(_statement: str) -> None:
        nonlocal count
        count += 1

    connection.set_trace_callback(trace)
    try:
        yield lambda: count
    finally:
        connection.set_trace_callback(None)


def streaming_sha256(path: str | Path, *, chunk_bytes: int = HASH_CHUNK_BYTES) -> str:
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be positive")
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(chunk_bytes):
            digest.update(block)
    return digest.hexdigest()


def file_fingerprint(path: str | Path, *, include_sha256: bool = True) -> dict[str, Any]:
    candidate = Path(path)
    stat = candidate.stat()
    result: dict[str, Any] = {
        "size_bytes": stat.st_size,
        "st_dev": stat.st_dev,
        "st_ino": stat.st_ino,
        "st_mtime_ns": stat.st_mtime_ns,
    }
    result["sha256"] = streaming_sha256(candidate) if include_sha256 else unavailable("hash was not requested")
    return result


def metric_spread_ratio(values: list[float]) -> float:
    """Return (max-min)/median, the frozen M0 repeatability measure."""

    if not values:
        raise ValueError("values must not be empty")
    ordered = sorted(float(value) for value in values)
    midpoint = len(ordered) // 2
    median = ordered[midpoint] if len(ordered) % 2 else (ordered[midpoint - 1] + ordered[midpoint]) / 2
    if median <= 0:
        return 0.0 if ordered[0] == ordered[-1] else float("inf")
    return (ordered[-1] - ordered[0]) / median
