"""Installer orchestration uses synthetic paths only; never downloads game data."""

import os
from pathlib import Path
import subprocess
import sys
import tomllib
from types import SimpleNamespace

import pytest

from scripts import setup_windows as setup


def test_project_config_round_trip_backup_and_idempotence(tmp_path):
    root = tmp_path / '中文 project'
    config = root / '.codex' / 'config.toml'
    config.parent.mkdir(parents=True)
    original = b'# keep this comment\r\nmodel = "existing"\r\n[mcp_servers.other]\r\ncommand = "other"\r\n'
    config.write_bytes(original)
    python = root / '.venv' / 'Scripts' / 'python.exe'
    runtime = root / 'data' / 'rebuilt' / 'runtime-config.json'
    setup.configure_codex(root, python, runtime)
    result = config.read_bytes()
    assert result.startswith(original)
    parsed = tomllib.loads(result.decode())
    assert parsed['model'] == 'existing'
    assert parsed['mcp_servers']['other']['command'] == 'other'
    entry = parsed['mcp_servers'][setup.SERVER]
    assert entry['command'] == str(python)
    assert entry['args'] == [str(root / 'scripts' / 'serve_dialogue.py'), '--config', str(runtime)]
    assert entry['required'] is True
    assert next(config.parent.glob('*.bak')).read_bytes() == original
    setup.configure_codex(root, python, runtime)
    assert config.read_bytes() == result
    assert len(list(config.parent.glob('*.bak'))) == 1


@pytest.mark.parametrize('original', [
    b'[mcp_servers.genshin-lore-research71]\ncommand="existing"\n',
    b'broken = [',
])
def test_config_conflict_or_malformed_preserved(tmp_path, original):
    config = tmp_path / '.codex' / 'config.toml'
    config.parent.mkdir()
    config.write_bytes(original)
    with pytest.raises(ValueError):
        setup.configure_codex(tmp_path, Path('python.exe'), Path('runtime.json'))
    assert config.read_bytes() == original
    assert not list(config.parent.glob('*.bak'))


def test_windows_path_escaping_is_valid_toml(tmp_path):
    python = Path('C:\\Users\\名字\\repo space\\python.exe')
    path = setup.configure_codex(tmp_path, python, Path('D:\\data space\\runtime.json'))
    assert tomllib.loads(path.read_text())['mcp_servers'][setup.SERVER]['command'] == str(python)


@pytest.mark.parametrize('project_free,disk_free', [
    (setup.PEAK - 1, setup.PEAK * 2),
    (setup.PEAK * 2, setup.PEAK),
])
def test_capacity_does_not_substitute_disk_for_budget(monkeypatch, tmp_path, project_free, disk_free):
    from genshin_lore import batch_space, storage_policy
    monkeypatch.setattr(batch_space, 'broad_inventory', lambda _: SimpleNamespace(
        allocated_bytes=storage_policy.PROJECT_HARD_LIMIT_BYTES - project_free))
    monkeypatch.setattr(setup.shutil, 'disk_usage', lambda _: SimpleNamespace(free=disk_free))
    with pytest.raises(ValueError, match='22 GB'):
        setup.headroom(tmp_path)


def test_finish_preserves_full_build_and_checks_before_config(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(setup, 'checkout', lambda *args: events.append(('source', args)))
    monkeypatch.setattr(setup, 'headroom', lambda root: setup.PEAK)
    monkeypatch.setattr(setup, 'run', lambda args: events.append(('run', [str(a) for a in args])))
    monkeypatch.setattr(setup, 'configure_codex', lambda *args: events.append(('config', args)))
    setup.finish(tmp_path, Path('python.exe'), 1)
    commands = [args for kind, args in events if kind == 'run']
    build = next(args for args in commands if args[1].endswith('build.py'))
    assert build[build.index('--stage') + 1] == 'all'
    assert build[build.index('--jobs') + 1] == '1'
    assert '--model-cache' in build
    assert '--without-semantic' not in build
    assert '--compact-index' not in build
    assert commands[-2][1].endswith('serve_dialogue.py')
    assert commands[-2][-1] == '--check'
    assert commands[-1][1].endswith('check_dialogue.py')
    assert events[-1][0] == 'config'


@pytest.mark.parametrize('failed_script', ['fetch_semantic_model.py', 'build.py', 'serve_dialogue.py', 'check_dialogue.py'])
def test_failure_never_registers_mcp(monkeypatch, tmp_path, failed_script):
    monkeypatch.setattr(setup, 'checkout', lambda *args: None)
    monkeypatch.setattr(setup, 'headroom', lambda _: setup.PEAK)
    def run(args):
        if Path(args[1]).name == failed_script:
            raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(setup, 'run', run)
    monkeypatch.setattr(setup, 'configure_codex', lambda *args: pytest.fail('registered after failure'))
    with pytest.raises(subprocess.CalledProcessError):
        setup.finish(tmp_path, Path('python.exe'), 2)


def test_existing_source_never_reset_or_rewritten(monkeypatch, tmp_path):
    calls = []
    def run(args, *, capture=False):
        calls.append(args)
        if args[-1] == '--show-toplevel':
            return str(tmp_path)
        if args[-1] == 'HEAD':
            return 'wrong-source'
        pytest.fail(str(args))
    monkeypatch.setattr(setup, 'run', run)
    with pytest.raises(ValueError, match='Pinned source differs'):
        setup.checkout(tmp_path, setup.SOURCE, setup.TREE)
    assert all('reset' not in command and 'checkout' not in command for command in calls)


def test_checkout_sets_raw_line_endings_before_fetch(monkeypatch, tmp_path):
    path = tmp_path / 'source'
    calls = []
    def run(args, *, capture=False):
        calls.append(args)
        return { '--show-toplevel': str(path), 'HEAD': setup.SOURCE,
                 'HEAD^{tree}': setup.TREE, '--untracked-files=all': '' }.get(str(args[-1]))
    monkeypatch.setattr(setup, 'run', run)
    setup.checkout(path, setup.SOURCE, setup.TREE)
    config = next(i for i, args in enumerate(calls) if 'config' in args)
    fetch = next(i for i, args in enumerate(calls) if 'fetch' in args)
    assert config < fetch
    assert calls[config][-2:] == ['core.autocrlf', 'false']


def test_failed_command_is_not_ignored():
    with pytest.raises(subprocess.CalledProcessError):
        setup.run([sys.executable, '-c', 'raise SystemExit(7)'])


@pytest.mark.skipif(os.name != 'nt', reason='native PowerShell parser check runs in Windows CI')
def test_bootstrap_powershell_parses():
    script = setup.ROOT / 'scripts' / 'setup_windows.ps1'
    quoted = "'" + str(script).replace("'", "''") + "'"
    expression = (
        '$tokens=$null; $errors=$null; '
        f'[System.Management.Automation.Language.Parser]::ParseFile({quoted},'
        '[ref]$tokens,[ref]$errors) | Out-Null; '
        'if ($errors.Count) { $errors | Out-String | Write-Error; exit 1 }'
    )
    subprocess.run(['powershell.exe', '-NoProfile', '-Command', expression], check=True)
