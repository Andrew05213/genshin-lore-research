"""Public names, documentation links, and license metadata; no game inputs."""
from pathlib import Path
import os
import re
import subprocess
import sys
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_project_name_and_license_are_consistent():
    project = tomllib.loads((ROOT/'pyproject.toml').read_text())['project']
    assert project['name'] == 'genshin-lore-research'
    assert project['license'] == 'Apache-2.0'
    assert set(project['license-files']) == {'LICENSE', 'NOTICE'}
    license_text = (ROOT/'LICENSE').read_text()
    assert 'Apache License' in license_text and 'Version 2.0, January 2004' in license_text
    assert 'Grant of Patent License' in license_text and 'Limitation of Liability' in license_text
    assert 'Apache-2.0' in (ROOT/'README.md').read_text()


@pytest.mark.parametrize('command', ['build', 'build_component', 'serve', 'check',
                                    'serve_dialogue', 'check_dialogue'])
def test_public_command_help(command):
    env = {**os.environ, 'PYTHONPATH': str(ROOT/'src')}
    result = subprocess.run([sys.executable, str(ROOT/'scripts'/(command+'.py')), '--help'],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'usage:' in result.stdout


def test_markdown_local_links_resolve():
    files = [ROOT/'README.md', ROOT/'AGENTS.md', *(ROOT/'docs').rglob('*.md'),
             ROOT/'src/genshin_lore/README.md']
    for path in files:
        for target in re.findall(r'\]\(([^)]+)\)', path.read_text()):
            if '://' in target or target.startswith('#'):
                continue
            assert (path.parent/target.split('#')[0]).exists(), (path, target)
