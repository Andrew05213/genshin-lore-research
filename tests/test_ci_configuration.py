"""CI policy is testable without credentials, databases or hosted runners."""
from pathlib import Path
import tomllib

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_ci_uses_public_python_source_only():
    value = yaml.load((ROOT/'.github/workflows/ci.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(value['on']) == {'push', 'pull_request', 'workflow_dispatch'}
    assert value['on']['push']['branches'] == ['main']
    assert value['permissions'] == {'contents': 'read'}
    steps = value['jobs']['source-checks']['steps']
    commands = '\n'.join(step.get('run', '') for step in steps)
    assert 'pytest' in commands and 'compileall' in commands and '--help' in commands
    assert 'requirements-dev.lock' in commands and 'build --wheel' in commands
    assert 'rebuild --stage all' not in commands and 'AnimeGameData.git' not in commands
    assert any(step.get('with', {}).get('python-version') == '3.12' for step in steps)


def test_direct_build_dependencies_match_package():
    project = tomllib.loads((ROOT/'pyproject.toml').read_text())['project']
    declared = {line for line in (ROOT/'requirements-build.txt').read_text().splitlines()
                if line and not line.startswith('#')}
    assert set(project['dependencies']) == declared
