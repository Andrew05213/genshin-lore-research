"""Run the real Linux shell entry with synthetic executables, no downloads or sudo."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts import setup_project as setup


@pytest.mark.parametrize('platform,suffix', [('linux', 'bin/python'), ('win32', 'Scripts/python.exe')])
def test_platform_venv_path(monkeypatch, tmp_path, platform, suffix):
    with monkeypatch.context() as patch:
        patch.setattr(setup.sys, 'platform', platform)
        actual = setup.venv_python(tmp_path)
    assert actual == tmp_path / '.venv' / suffix


@pytest.fixture
def shell_environment(tmp_path):
    if sys.platform != 'linux':
        pytest.skip('real Bash bootstrap execution requires native Linux')
    root = tmp_path / '中文 project'
    root.mkdir()
    shutil.copyfile(setup.ROOT / 'setup.sh', root / 'setup.sh')
    tools = tmp_path / 'tools'
    tools.mkdir()
    for name in ('uname', 'mkdir', 'date', 'tee', 'dirname'):
        (tools / name).symlink_to(shutil.which(name))
    def executable(name, body):
        path = tools / name
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)
    executable('git', 'exit 0')
    executable('g++', 'exit 0')
    executable('python3.12', '''if [ "$1" = -c ]; then exit 0; fi
printf 'synthetic invocation: %s\\n' "$*"
echo 'synthetic stderr retained' >&2
exit "${SETUP_FIXTURE_EXIT:-0}"''')
    env = {**os.environ, 'PATH': str(tools)}
    return root, tools, executable, env


@pytest.mark.parametrize('exit_code', [0, 7])
def test_shell_handoff_logs_args_and_preserves_failure(shell_environment, exit_code):
    root, _, _, env = shell_environment
    sentinel = root / 'previous-failure.txt'
    sentinel.write_bytes(b'prior failure evidence')
    result = subprocess.run(['/bin/bash', str(root / 'setup.sh'), '--jobs', '1'],
        cwd=root.parent, env={**env, 'SETUP_FIXTURE_EXIT': str(exit_code)},
        capture_output=True, text=True, timeout=15)
    assert result.returncode == exit_code, result.stdout + result.stderr
    logs = list((root / 'data' / 'setup-logs').glob('*.log'))
    assert len(logs) == 1
    raw = logs[0].read_text()
    assert 'scripts/setup_project.py --jobs 1' in raw
    assert 'synthetic stderr retained' in raw
    assert sentinel.read_bytes() == b'prior failure evidence'


def test_shell_dependency_failure_never_enters_installer(shell_environment):
    root, tools, executable, env = shell_environment
    (tools / 'g++').unlink()
    executable('sudo', 'exec "$@"')
    executable('apt-get', 'echo "synthetic apt failed" >&2; exit 9')
    result = subprocess.run(['/bin/bash', str(root / 'setup.sh')], env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 9
    assert 'synthetic invocation' not in result.stdout
    assert 'synthetic apt failed' in next((root / 'data/setup-logs').glob('*.log')).read_text()


def test_shell_unsupported_dependency_manager_gives_diagnostic(shell_environment):
    root, tools, _, env = shell_environment
    (tools / 'g++').unlink()
    result = subprocess.run(['/bin/bash', str(root / 'setup.sh')], env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 1
    assert 'development headers' in result.stdout
    assert 'synthetic invocation' not in result.stdout


def test_windows_compatibility_entry_help():
    result = subprocess.run([sys.executable, str(setup.ROOT / 'scripts/setup_windows.py'), '--help'],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0
    assert '--jobs' in result.stdout


@pytest.mark.skipif(sys.platform != 'linux', reason='POSIX venv symlink regression')
def test_symlinked_venv_still_dispatches_to_virtual_environment(monkeypatch, tmp_path):
    python = tmp_path / '.venv' / 'bin' / 'python'
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    calls = []
    monkeypatch.chdir(tmp_path)
    for key in ('PYTHONUTF8', 'PYTHONPATH', 'PIP_CACHE_DIR'):
        monkeypatch.setenv(key, '')
    monkeypatch.setattr(setup, 'run', lambda args: calls.append(args))
    monkeypatch.setattr(setup, 'finish', lambda *args: pytest.fail('system Python ran the prepared stage'))
    setup.install(tmp_path)
    assert '--prepared' in calls[-1]
    assert calls[-1][0] == python


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux entry')
def test_real_shell_help_does_not_prepare_data(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    shutil.copyfile(setup.ROOT / 'setup.sh', root / 'setup.sh')
    result = subprocess.run(['/bin/bash', str(root / 'setup.sh'), '--help'],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0
    assert '--jobs' in result.stdout
    assert not (root / 'data').exists()
