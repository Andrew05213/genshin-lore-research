"""Optional private storage policy; source distributions impose no project quota."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil


def load_policy(path=None):
    """Read only an explicitly selected local policy, never a tracked default."""
    path = path if path is not None else os.environ.get('GENSHIN_LORE_STORAGE_POLICY')
    if not path:
        return None, None
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict) or set(value) - {'hard_limit_bytes', 'warning_bytes'}:
        raise ValueError('unknown local storage policy fields')
    hard, warning = value.get('hard_limit_bytes'), value.get('warning_bytes')
    for limit in (hard, warning):
        if limit is not None and (type(limit) is not int or limit <= 0):
            raise ValueError('local storage limits must be positive integer bytes or null')
    if hard is not None and warning is not None and warning >= hard:
        raise ValueError('local storage warning must be below its hard limit')
    return hard, warning


PROJECT_HARD_LIMIT_BYTES, PROJECT_WARNING_BYTES = load_policy()


def available_headroom(repo_root, output_root, *, explicit_budget=None, reserve_bytes=1_000_000_000):
    """Bound new writes by disk free, plus any explicitly selected local quota."""
    if explicit_budget is not None and (type(explicit_budget) is not int or explicit_budget < 0):
        raise ValueError('storage budget must be non-negative integer bytes')
    parent = Path(output_root).resolve()
    while not parent.exists():
        parent = parent.parent
    remaining = max(0, shutil.disk_usage(parent).free - reserve_bytes)
    if explicit_budget is not None:
        remaining = min(remaining, explicit_budget)
    if PROJECT_HARD_LIMIT_BYTES is not None:
        from .batch_space import broad_inventory
        remaining = min(remaining, PROJECT_HARD_LIMIT_BYTES - broad_inventory(repo_root).allocated_bytes)
    return remaining
