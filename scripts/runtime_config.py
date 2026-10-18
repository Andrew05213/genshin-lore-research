"""Resolve explicitly named local runtime files relative to their config file.

No deployment discovery, alternate corpus selection or fallback is performed.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

RUNTIME_PATHS = ("corpus", "bindings", "index", "profile")


def activate_generated_config(output, value, *, expected_previous):
    """Switch only this output's known config pointer, retaining literal access.

    Profiles, receipts and artifacts stay immutable. The caller qualifies the
    standalone semantic config before requesting this local pointer change.
    """
    from genshin_lore.artifact_io import write_json_exclusive
    from genshin_lore.platform_io import sync_directory

    target = Path(output) / "runtime-config.json"
    if target.is_symlink():
        raise ValueError("runtime config pointer must not be a symlink")
    if not target.exists():
        write_json_exclusive(target, value)
        return
    previous = json.loads(target.read_text(encoding="utf-8"))
    if previous == value:
        return
    if previous != expected_previous:
        raise ValueError(
            "existing runtime config is not this build's literal config; preserved"
        )
    backup = target.with_name("runtime-config.literal.json")
    if backup.exists():
        if json.loads(backup.read_text(encoding="utf-8")) != previous:
            raise ValueError("literal config backup differs; both configs preserved")
    else:
        write_json_exclusive(backup, previous)
    fd, name = tempfile.mkstemp(
        prefix=".runtime-config-", suffix=".json", dir=target.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        sync_directory(target.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_runtime_paths(config):
    config = Path(config).resolve()
    values = json.loads(config.read_text())
    if not isinstance(values, dict):
        raise ValueError("runtime config must be an object")
    result = {}
    for key in RUNTIME_PATHS:
        value = values.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("runtime config requires an explicit path: " + key)
        path = Path(value)
        path = (config.parent / path).resolve() if not path.is_absolute() else path
        if not path.is_file():
            raise FileNotFoundError(
                "runtime config file unavailable: " + key + ": " + str(path)
            )
        result[key] = path
    return result
