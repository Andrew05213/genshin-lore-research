"""Small source-bound byte seal helpers for pinned Git snapshots."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def git_blob_oid_bytes(raw: bytes) -> str:
    header = b"blob " + str(len(raw)).encode("ascii") + b"\0"
    return hashlib.sha1(header + raw).hexdigest()


def pinned_file_seal(root: str | Path, commit: str, relative_path: str) -> dict[str, object]:
    root = Path(root)
    raw = (root / relative_path).read_bytes()
    actual_blob = git_blob_oid_bytes(raw)
    pinned_blob = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", f"{commit}:{relative_path}"], text=True
    ).strip()
    if actual_blob != pinned_blob:
        raise ValueError(f"source file bytes do not match pinned Git blob: {relative_path}")
    return {
        "path": relative_path,
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "blob": actual_blob,
    }


__all__ = ["git_blob_oid_bytes", "pinned_file_seal"]
