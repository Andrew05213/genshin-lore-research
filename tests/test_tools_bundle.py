"""Code/dependency archives contain tracked source and pinned wheels only."""
import hashlib
import json
import subprocess
from zipfile import ZipFile

import pytest

from scripts.maintenance.package_tools import package


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path/'source'
    root.mkdir()
    git(root, 'init')
    (root/'LICENSE').write_text('Apache-2.0')
    (root/'requirements-tools.lock').write_text('synthetic-dependency==1.0\n')
    (root/'requirements-wheel-build.lock').write_text('')
    git(root, 'add', '.')
    git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '-m', 'synthetic source')
    wheels = tmp_path/'wheelhouse'
    wheels.mkdir()
    return root, wheels


def wheel(wheels, version='1.0', name='synthetic_dependency'):
    path = wheels/f'{name}-{version}-py3-none-any.whl'
    with ZipFile(path, 'w') as z:
        z.writestr(f'{name}-{version}.dist-info/METADATA',
                   f'Name: {name}\nVersion: {version}\nLicense-Expression: MIT\n')
        z.writestr(f'{name}/__init__.py', '')
    return path


def test_archive_manifest_hashes_platform_wheel_lock_and_untracked_exclusion(checkout, tmp_path):
    root, wheels = checkout
    w = wheel(wheels)
    (root/'game.sqlite').write_bytes(b'untracked synthetic data')
    (root/'vector.npy').write_bytes(b'untracked vector fixture')
    result = package(root, wheels, tmp_path/'tools.zip')
    with ZipFile(tmp_path/'tools.zip') as z:
        assert not any(n.endswith(('.sqlite', '.npy')) for n in z.namelist())
        assert json.loads(z.read('bundle.json')) == result
        for name, seal in result['files'].items():
            raw = z.read(name)
            assert seal == {'size_bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        assert hashlib.sha256(w.read_bytes()).hexdigest() in z.read('install.lock').decode()
    with pytest.raises(FileExistsError):
        package(root, wheels, tmp_path/'tools.zip')


def test_vendored_distribution_metadata_is_not_the_wheels_own_metadata(checkout, tmp_path):
    root, wheels = checkout
    path = wheel(wheels)
    with ZipFile(path, 'a') as z:
        z.writestr('synthetic_dependency/_vendor/other.dist-info/METADATA',
                   'Name: vendored-other\nVersion: 99\n')
    result = package(root, wheels, tmp_path/'tools.zip')
    assert result['wheels'][0]['name'] == 'synthetic-dependency'


def test_multiple_top_level_metadata_directories_are_rejected(checkout, tmp_path):
    root, wheels = checkout
    path = wheel(wheels)
    with ZipFile(path, 'a') as z:
        z.writestr('other.dist-info/METADATA', 'Name: other\nVersion: 1.0\n')
    with pytest.raises(ValueError, match='invalid wheel'):
        package(root, wheels, tmp_path/'tools.zip')


@pytest.mark.parametrize('failure', ['unlocked', 'duplicate', 'empty', 'data', 'dirty', 'symlink'])
def test_bundle_rejects_untrusted_or_incomplete_inputs(checkout, tmp_path, failure):
    root, wheels = checkout
    if failure != 'empty':
        w = wheel(wheels, version='2.0' if failure == 'unlocked' else '1.0')
    if failure == 'duplicate':
        (wheels/'another.whl').write_bytes(w.read_bytes())
    elif failure == 'data':
        (wheels/'model.onnx').write_bytes(b'synthetic model')
    elif failure == 'dirty':
        (root/'LICENSE').write_text('changed')
    elif failure == 'symlink':
        try:
            (root/'linked.py').symlink_to(root/'LICENSE')
        except OSError:
            pytest.skip('symlink privilege unavailable')
        git(root, 'add', 'linked.py')
        git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
            'commit', '-m', 'synthetic symlink')
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        package(root, wheels, tmp_path/'tools.zip')
    assert not (tmp_path/'tools.zip').exists()
