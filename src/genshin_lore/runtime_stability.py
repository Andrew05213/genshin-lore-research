"""Cheap call-time stability checks for read-only family providers.

Qualification performs the expensive business checks once.  Every routed call
then verifies that the selected database and receipt are still the same files;
the receipt is small enough to hash on each call, while the database is guarded
by identity/stat checks only.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, Callable, TypeVar


_BLOCK_SIZE = 1024 * 1024
_T = TypeVar("_T")


class RuntimeStabilityError(RuntimeError):
    """The qualified database/receipt changed during the runtime lifetime."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _identity(path: Path, *, database: bool = False) -> tuple[int, int, int, int]:
    try:
        info = os.lstat(path)
    except (FileNotFoundError, OSError) as exc:
        raise RuntimeStabilityError(
            "database_missing" if database else "receipt_missing"
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RuntimeStabilityError(
            "database_path_replaced" if database else "receipt_path_replaced"
        )
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _sidecars(path: Path) -> tuple[Path, ...]:
    return tuple(Path(f"{path}{suffix}") for suffix in ("-journal", "-wal", "-shm"))


def _assert_no_sidecars(path: Path) -> None:
    if any(os.path.lexists(item) for item in _sidecars(path)):
        raise RuntimeStabilityError("database_sidecar_present")


def _stream_sha256(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(_BLOCK_SIZE):
                size += len(chunk)
                digest.update(chunk)
    except (FileNotFoundError, OSError) as exc:
        raise RuntimeStabilityError("receipt_missing") from exc
    return size, digest.hexdigest()


@dataclass(frozen=True)
class RuntimeStabilitySnapshot:
    """Identity and byte seal captured after provider qualification."""

    database_path: str
    database_identity: tuple[int, int, int, int]
    receipt_path: str
    receipt_identity: tuple[int, int, int, int]
    receipt_size: int
    receipt_sha256: str

    @classmethod
    def capture(cls, database_path: str | Path, receipt_path: str | Path) -> "RuntimeStabilitySnapshot":
        database = Path(os.path.abspath(os.fspath(database_path)))
        receipt = Path(os.path.abspath(os.fspath(receipt_path)))
        _assert_no_sidecars(database)
        db_identity = _identity(database, database=True)
        receipt_identity = _identity(receipt)
        receipt_size, receipt_sha256 = _stream_sha256(receipt)
        if receipt_size != receipt_identity[2]:
            raise RuntimeStabilityError("receipt_changed_during_hash")
        return cls(
            str(database), db_identity, str(receipt), receipt_identity,
            receipt_size, receipt_sha256,
        )

    def to_json(self) -> dict[str, Any]:
        """Return a stable, serializable snapshot for capability state."""
        return {
            "database_path": self.database_path,
            "database_identity": list(self.database_identity),
            "database_sidecars_absent": True,
            "receipt_path": self.receipt_path,
            "receipt_identity": list(self.receipt_identity),
            "receipt_size": self.receipt_size,
            "receipt_sha256": self.receipt_sha256,
        }

    @classmethod
    def from_json(cls, value: Any) -> "RuntimeStabilitySnapshot | None":
        if not isinstance(value, dict):
            return None
        try:
            db_identity = tuple(int(item) for item in value["database_identity"])
            receipt_identity = tuple(int(item) for item in value["receipt_identity"])
            if len(db_identity) != 4 or len(receipt_identity) != 4:
                return None
            result = cls(
                str(value["database_path"]), db_identity,
                str(value["receipt_path"]), receipt_identity,
                int(value["receipt_size"]), str(value["receipt_sha256"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
        return result

    def check(self) -> None:
        database = Path(self.database_path)
        receipt = Path(self.receipt_path)
        _assert_no_sidecars(database)
        try:
            current_database = _identity(database, database=True)
        except RuntimeStabilityError:
            raise
        if current_database != self.database_identity:
            raise RuntimeStabilityError("database_identity_changed")
        try:
            current_receipt = _identity(receipt)
        except RuntimeStabilityError:
            raise
        if current_receipt[:2] != self.receipt_identity[:2]:
            raise RuntimeStabilityError("receipt_path_replaced")
        size, digest = _stream_sha256(receipt)
        if (size, digest) != (self.receipt_size, self.receipt_sha256):
            raise RuntimeStabilityError("receipt_content_changed")


def unavailable_result(
    family: str, contract: str, run: str, reason: str,
) -> Any:
    """Build a family result without importing family-specific repositories."""
    from .family_evidence import FamilyEvidenceResult

    return FamilyEvidenceResult(
        "unavailable", family, run, contract, "unavailable", False,
        {"status": "Unavailable", "reason": reason,
         "lookup": {"ingest_run_id": run}},
    )


def stable_routed_call(operation: str) -> Callable[[Callable[..., _T]], Callable[..., _T]]:
    """Decorate a provider operation with entry/exit stability checks."""

    def decorator(function: Callable[..., _T]) -> Callable[..., _T]:
        @wraps(function)
        def wrapped(self: Any, *args: Any, **kwargs: Any) -> _T:
            snapshot = getattr(self, "_stability", None)
            if snapshot is None:
                return function(self, *args, **kwargs)
            run = kwargs.get("ingest_run_id")
            try:
                snapshot.check()
            except RuntimeStabilityError as exc:
                return unavailable_result(
                    self.family, self.family_contract_version,
                    str(run) if isinstance(run, str) else "invalid", exc.reason,
                )
            try:
                result = function(self, *args, **kwargs)
            except Exception:
                # Business validation errors (for example a bad cursor or
                # filter) must still run the exit check.  If the file became
                # unstable while the business error was being produced, the
                # stability failure is the safer, structured result and takes
                # precedence over re-raising the business exception.
                try:
                    snapshot.check()
                except RuntimeStabilityError as exc:
                    return unavailable_result(
                        self.family, self.family_contract_version,
                        str(run) if isinstance(run, str) else "invalid", exc.reason,
                    )
                raise
            try:
                snapshot.check()
            except RuntimeStabilityError as exc:
                return unavailable_result(
                    self.family, self.family_contract_version,
                    str(run) if isinstance(run, str) else "invalid", exc.reason,
                )
            return result

        return wrapped

    return decorator


__all__ = [
    "RuntimeStabilityError", "RuntimeStabilitySnapshot", "stable_routed_call",
]
