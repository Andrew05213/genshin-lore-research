"""Resolve explicitly named local runtime files relative to their config file.

No deployment discovery, alternate corpus selection or fallback is performed.
"""
from __future__ import annotations

import json
from pathlib import Path

RUNTIME_PATHS = ("corpus", "bindings", "index", "profile")


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
            raise FileNotFoundError("runtime config file unavailable: " + key + ": " + str(path))
        result[key] = path
    return result
