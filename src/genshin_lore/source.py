from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .errors import ManifestError, SourceMissingError
from .util import canonical_json, sha256_bytes


@dataclass(frozen=True)
class LocatedFile:
    relative_path: str
    path: Path
    root: Path


class SourceBundle:
    """Read-only view over one or more local roots for a pinned snapshot.

    Multiple roots are useful for a reproducible fixture: the selected raw
    records live under cache/samples while a few CHS Readable/Excel files live
    under the local upstream checkout.  The first root containing a relative
    path wins and is recorded in provenance.
    """

    def __init__(self, roots: Iterable[str | Path]):
        self.roots = [Path(root).resolve() for root in roots]

    def locate(self, relative_path: str) -> LocatedFile:
        relative = relative_path.replace("\\", "/")
        for root in self.roots:
            candidate = root.joinpath(*relative.split("/"))
            if candidate.is_file():
                return LocatedFile(relative, candidate, root)
        raise SourceMissingError(f"missing source file in all roots: {relative}")

    def try_locate(self, relative_path: str) -> LocatedFile | None:
        try:
            return self.locate(relative_path)
        except SourceMissingError:
            return None

    def read_bytes(self, relative_path: str) -> tuple[LocatedFile, bytes]:
        located = self.locate(relative_path)
        return located, located.path.read_bytes()

    def read_json(self, relative_path: str) -> tuple[LocatedFile, Any]:
        located, raw = self.read_bytes(relative_path)
        try:
            value = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManifestError(f"invalid JSON in {relative_path}: {exc}") from exc
        return located, value

    def inventory(self) -> list[dict[str, Any]]:
        """Return a de-duplicated file inventory, preserving first-root wins."""
        files: dict[str, LocatedFile] = {}
        for root in self.roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                relative = path.relative_to(root).as_posix()
                files.setdefault(relative, LocatedFile(relative, path, root))
        result = []
        for relative in sorted(files):
            located = files[relative]
            data = located.path.read_bytes()
            result.append(
                {
                    "relative_path": relative,
                    "size": len(data),
                    "sha256": sha256_bytes(data),
                    "root": str(located.root),
                }
            )
        return result


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read manifest {manifest_path}: {exc}") from exc
    validate_manifest(value)
    return value


def validate_manifest(manifest: dict[str, Any]) -> None:
    required = [
        "schema_version",
        "source_id",
        "upstream",
        "release_label",
        "build",
        "commit",
        "tree",
        "observed_tree_counts",
        "primary_languages",
    ]
    missing = [key for key in required if key not in manifest]
    if missing:
        raise ManifestError(f"manifest missing required keys: {', '.join(missing)}")
    for key in ("commit", "tree"):
        value = manifest[key]
        if not isinstance(value, str) or len(value) != 40 or any(c not in "0123456789abcdef" for c in value.lower()):
            raise ManifestError(f"manifest {key} is not a 40-character hex object id")
    if not isinstance(manifest["primary_languages"], list) or not {"CHS", "EN", "JP"}.issubset(manifest["primary_languages"]):
        raise ManifestError("manifest primary_languages must include CHS, EN and JP")


def snapshot_id(manifest: dict[str, Any]) -> str:
    return f"{manifest['source_id']}:{manifest['release_label']}:{manifest['commit'][:12]}"


def manifest_json(manifest: dict[str, Any]) -> str:
    return canonical_json(manifest)

