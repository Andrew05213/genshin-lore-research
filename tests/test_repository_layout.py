"""Public names, documentation links, and license metadata; no game inputs."""
from pathlib import Path
import os
import re
import subprocess
import sys
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_checkout_skill_has_codex_discovery_metadata():
    skill = ROOT / '.agents/skills/genshin-lore-research/SKILL.md'
    frontmatter = skill.read_text(encoding='utf-8').split('---', 2)[1]
    fields = dict(line.split(':', 1) for line in frontmatter.strip().splitlines())
    assert fields['name'].strip() == skill.parent.name == 'genshin-lore-research'
    assert fields['description'].strip()


@pytest.mark.parametrize('filename', ['synthetic.md', '合成 引用.md'])
def test_generated_source_cards_are_ignored_but_delivery_code_is_not(filename):
    paths = [f'source-cards/{filename}', 'data/rebuilt/source-cards/synthetic.json',
             'src/genshin_lore/source_cards.py']
    # Git's NUL-delimited byte protocol avoids Windows text-pipe CRLF
    # conversion and pathname quoting. Compare exact paths, not stripped output.
    encoded = [path.encode('utf-8') for path in paths]
    result = subprocess.run(['git', 'check-ignore', '--no-index', '-z', '--stdin'], cwd=ROOT,
                            input=b'\0'.join(encoded) + b'\0', capture_output=True, check=True)
    assert result.stdout == b'\0'.join(encoded[:2]) + b'\0'


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
             *(ROOT/'.agents/skills').rglob('*.md'),
             ROOT/'src/genshin_lore/README.md']
    for path in files:
        for target in re.findall(r'\]\(([^)]+)\)', path.read_text()):
            if '://' in target or target.startswith('#'):
                continue
            assert (path.parent/target.split('#')[0]).exists(), (path, target)
