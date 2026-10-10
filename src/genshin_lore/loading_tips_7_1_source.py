"""Exact 7.1 LoadingTips source bytes and fixed selected-boundary qualification."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from .loading_tips_source import array_records, pairs, sha

COMMIT = "9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
TREE = "aa578c08c402af39b41f1ffc95124d10f5410272"
LANGUAGES = ("CHS", "EN", "JP")
FIELDS = ("tipsTitleTextMapHash", "tipsDescTextMapHash")
PRIMARY = "ExcelBinOutput/LoadingTipsExcelConfigData.json"
PATHS = [PRIMARY] + [f"TextMap/TextMap{tier}{locale}.json"
                     for locale in LANGUAGES for tier in ("", "_Medium")]


def _blob(path: Path) -> str:
    stat = path.stat()
    digest = hashlib.sha1(b"blob " + str(stat.st_size).encode() + b"\0")
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_scope(root: Path, scope: dict) -> None:
    root = Path(root)
    head, tree = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD", "HEAD^{tree}"], text=True
    ).splitlines()
    if (head, tree) != (COMMIT, TREE) or subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain"]
    ):
        raise ValueError("clean exact 7.1 source checkout required")
    if (scope.get("source_commit"), scope.get("source_tree"), scope.get("snapshot_id")) != (
        COMMIT, TREE, "anime-game-data:7.1:" + COMMIT
    ):
        raise ValueError("LoadingTips 7.1 scope pin")
    if (scope.get("text_fields") != list(FIELDS) or scope.get("languages") != list(LANGUAGES)
            or [item["path"] for item in scope.get("files", [])] != PATHS):
        raise ValueError("LoadingTips selected source denominator")
    for item in scope["files"]:
        path = root / item["path"]
        oid = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", f"{COMMIT}:{item['path']}"], text=True
        ).strip()
        if (not path.is_file() or path.is_symlink() or path.stat().st_size != item["size_bytes"]
                or sha(path) != item["sha256"] or _blob(path) != oid or oid != item["blob"]):
            raise ValueError("LoadingTips source byte/blob drift: " + item["path"])


def seal(root: Path) -> dict:
    root = Path(root)
    files = []
    for path in PATHS:
        item = root / path
        if not item.is_file() or item.is_symlink():
            raise ValueError("missing/aliased LoadingTips input: " + path)
        blob = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", f"{COMMIT}:{path}"], text=True
        ).strip()
        if _blob(item) != blob:
            raise ValueError("actual bytes not in 7.1 tree: " + path)
        files.append({"path": path, "size_bytes": item.stat().st_size,
                      "sha256": sha(item), "blob": blob})
    rows = list(array_records(root / PRIMARY))
    if len(rows) != 1004:
        raise ValueError("LoadingTips 7.1 physical row denominator changed")
    scope = {"contract": "loading-tips-7.1-source-scope-v1",
             "source_commit": COMMIT, "source_tree": TREE,
             "snapshot_id": "anime-game-data:7.1:" + COMMIT,
             "files": files, "primary_rows": len(rows),
             "text_fields": list(FIELDS), "languages": list(LANGUAGES),
             "scope_complete_for_selected_paths": True, "full_7_1": False}
    check_scope(root, scope)
    return scope
