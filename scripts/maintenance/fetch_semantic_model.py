"""Explicit download of the pinned embedding model; never runs during builds."""

import argparse
import hashlib
import json
from pathlib import Path
import os
import urllib.request

ROOT = Path(__file__).resolve().parents[2]


def matches(path, expected):
    if (
        not path.is_file()
        or path.is_symlink()
        or path.stat().st_size != expected["size_bytes"]
    ):
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == expected["sha256"]


def fetch(cache_root, *, check_only=False):
    spec = json.loads(
        (ROOT / "recipes/embedding-model.json").read_text(encoding="utf-8")
    )
    model = Path(cache_root) / "model"
    if check_only:
        if not all(
            matches(model / name, identity) for name, identity in spec["files"].items()
        ):
            raise ValueError("pinned model file missing or differs")
    else:
        model.mkdir(parents=True, exist_ok=True)
        for name, identity in spec["files"].items():
            target = model / name
            if target.exists():
                if matches(target, identity):
                    continue
                raise FileExistsError(
                    "existing model file differs; preserved: " + str(target)
                )
            partial = target.with_name(target.name + ".part")
            url = f"https://huggingface.co/{spec['source_repo']}/resolve/{spec['revision']}/{name}"
            # Failed partial downloads remain for diagnosis and are never
            # silently promoted or mistaken for a complete model.
            with partial.open("xb") as stream:
                with urllib.request.urlopen(url, timeout=120) as response:
                    size = 0
                    while block := response.read(1024 * 1024):
                        size += len(block)
                        if size > identity["size_bytes"]:
                            raise ValueError(
                                "model download exceeds pinned size: " + name
                            )
                        stream.write(block)
                stream.flush()
                os.fsync(stream.fileno())
            if not matches(partial, identity):
                raise ValueError("downloaded model seal differs: " + name)
            os.link(partial, target)
            partial.unlink()
    # Reject extra files as well as missing/mutated files.
    from genshin_lore.semantic_discovery import tree_sha256

    if tree_sha256(model) != spec["model_tree_sha256"]:
        raise ValueError("pinned model tree differs; all existing files preserved")
    return {
        "status": "PASS",
        "model_id": spec["model_id"],
        "revision": spec["revision"],
        "model_tree_sha256": spec["model_tree_sha256"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify existing files, with no network requests",
    )
    args = parser.parse_args()
    print(json.dumps(fetch(args.cache_root, check_only=args.check)))
