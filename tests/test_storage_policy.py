"""Private quotas are opt-in; real disk limits always apply."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from genshin_lore import batch_space, storage_policy
from scripts import setup_project


def test_unconfigured_install_uses_disk_free_without_a_project_quota(monkeypatch, tmp_path):
    monkeypatch.delenv('GENSHIN_LORE_STORAGE_POLICY', raising=False)
    assert storage_policy.load_policy() == (None, None)
    monkeypatch.setattr(storage_policy, 'PROJECT_HARD_LIMIT_BYTES', None)
    monkeypatch.setattr(storage_policy.shutil, 'disk_usage', lambda _: SimpleNamespace(free=31_000_000_000))
    monkeypatch.setattr(batch_space, 'broad_inventory', lambda _: pytest.fail('unconfigured project quota was measured'))
    assert setup_project.headroom(tmp_path) == 30_000_000_000


def test_private_policy_is_explicit_and_does_not_change_defaults(monkeypatch, tmp_path):
    path = tmp_path / 'private.json'
    path.write_text(json.dumps({'hard_limit_bytes': 4096, 'warning_bytes': 2048}))
    monkeypatch.delenv('GENSHIN_LORE_STORAGE_POLICY', raising=False)
    assert storage_policy.load_policy() == (None, None)
    monkeypatch.setenv('GENSHIN_LORE_STORAGE_POLICY', str(path))
    assert storage_policy.load_policy() == (4096, 2048)


@pytest.mark.parametrize('value', [[], {'unknown': 10}, {'hard_limit_bytes': True},
    {'hard_limit_bytes': 0}, {'warning_bytes': -1}, {'hard_limit_bytes': '4096'},
    {'hard_limit_bytes': 4096, 'warning_bytes': 4096}])
def test_bad_private_policy_rejected(tmp_path, value):
    path = tmp_path / 'private.json'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        storage_policy.load_policy(path)


def test_missing_explicit_policy_is_not_silently_ignored(tmp_path):
    with pytest.raises(FileNotFoundError):
        storage_policy.load_policy(tmp_path / 'missing.json')


@pytest.mark.parametrize('configured', [False, True])
def test_new_process_inherits_only_explicit_private_limits(tmp_path, configured):
    policy = tmp_path / 'private.json'
    policy.write_text(json.dumps({'hard_limit_bytes': 4096, 'warning_bytes': 2048}))
    env = {**os.environ, 'PYTHONPATH': str(Path(storage_policy.__file__).parents[1])}
    env.pop('GENSHIN_LORE_STORAGE_POLICY', None)
    if configured:
        env['GENSHIN_LORE_STORAGE_POLICY'] = str(policy)
    command = [sys.executable, '-c', 'import json; from genshin_lore import batch_space as b; '
        'print(json.dumps([b.PROJECT_SPACE_LIMIT_BYTES,b.PROJECT_SPACE_WARNING_BYTES]))']
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=True, timeout=15)
    assert json.loads(result.stdout) == ([4096, 2048] if configured else [None, None])


@pytest.mark.parametrize('quota,explicit,expected', [(None, None, 9000), (None, 4000, 4000),
    (5000, None, 3000), (5000, 1000, 1000), (50000, 20000, 9000)])
def test_available_storage_respects_disk_and_all_opted_in_caps(monkeypatch, tmp_path, quota, explicit, expected):
    monkeypatch.setattr(storage_policy, 'PROJECT_HARD_LIMIT_BYTES', quota)
    monkeypatch.setattr(storage_policy.shutil, 'disk_usage', lambda _: SimpleNamespace(free=10000))
    monkeypatch.setattr(batch_space, 'broad_inventory', lambda _: SimpleNamespace(allocated_bytes=2000))
    assert storage_policy.available_headroom(tmp_path, tmp_path / 'new/output',
        explicit_budget=explicit, reserve_bytes=1000) == expected


@pytest.mark.parametrize('quota,warning,allocated,projected,free,blocked', [
    (None, None, 100000, 400, 500, False),
    (None, None, 100000, 400, 399, True),
    (1000, 800, 600, 199, 10000, False),
    (1000, 800, 600, 400, 10000, True),
])
def test_real_gate_optional_quota_and_disk_stop(monkeypatch, tmp_path, quota, warning, allocated, projected, free, blocked):
    monkeypatch.setattr(batch_space, 'PROJECT_SPACE_LIMIT_BYTES', quota)
    monkeypatch.setattr(batch_space, 'PROJECT_SPACE_WARNING_BYTES', warning)
    monkeypatch.setattr(batch_space, 'discover_project_tmp_paths', lambda _: ())
    measured = batch_space.SpaceInventory(str(tmp_path), (), allocated, allocated, False, quota, warning)
    monkeypatch.setattr(batch_space, 'inventory', lambda *args, **kwargs: measured)
    monkeypatch.setattr(batch_space.shutil, 'disk_usage', lambda _: SimpleNamespace(free=free))
    gate = batch_space.ProjectSpaceGate(tmp_path)
    if blocked:
        with pytest.raises(batch_space.SpaceGateError):
            gate.check('synthetic', projected_bytes=projected, cleanup=False)
        assert gate.report()['status'] == 'blocked'
    else:
        assert gate.check('synthetic', projected_bytes=projected, cleanup=False) is measured
        assert gate.report()['limit_bytes'] == quota
