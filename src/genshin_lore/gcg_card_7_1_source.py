"""Exact 7.1 source boundary for the standalone GCG card successor."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from .loading_tips_source import array_records, pairs, sha

COMMIT = "9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
TREE = "aa578c08c402af39b41f1ffc95124d10f5410272"
SNAPSHOT = "anime-game-data:7.1:" + COMMIT
PRIMARY = [
    "ExcelBinOutput/GCGDeckCardExcelConfigData.json",
    "ExcelBinOutput/GCGCardFaceExcelConfigData.json",
]
FILES = PRIMARY + [
    f"TextMap/TextMap{tier}{language}.json"
    for language in ("CHS", "EN", "JP")
    for tier in ("", "_Medium")
]


def check_files(root: Path, scope: dict) -> None:
    root = Path(root)
    if (scope.get("contract") != "gcg-card-source-7.1-v1"
            or scope.get("source_commit") != COMMIT
            or scope.get("source_tree") != TREE
            or scope.get("snapshot_id") != SNAPSHOT
            or scope.get("primary_rows") != [629, 1258]
            or [item["path"] for item in scope.get("files", [])] != FILES):
        raise ValueError("7.1 GCG source-scope contract drift")
    observed = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD", "HEAD^{tree}"], text=True
    ).splitlines()
    if observed != [COMMIT, TREE] or subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain=v1"], text=True
    ).strip():
        raise ValueError("7.1 GCG source checkout is not exact and clean")
    for item in scope["files"]:
        path = root / item["path"]
        size = path.stat().st_size
        if size != item["size_bytes"] or sha(path) != item["sha256"]:
            raise ValueError("7.1 GCG source bytes drift: " + item["path"])
        expected_blob = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", f"{COMMIT}:{item['path']}"],
            text=True,
        ).strip()
        digest = hashlib.sha1(b"blob " + str(size).encode() + b"\0")
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if expected_blob != item["blob"] or digest.hexdigest() != expected_blob:
            raise ValueError("7.1 GCG Git blob drift: " + item["path"])
