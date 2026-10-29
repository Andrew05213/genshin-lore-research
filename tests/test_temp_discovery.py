"""Only attributable temporary paths may affect project-space discovery."""
from pathlib import Path

import pytest

from genshin_lore import batch_space


DISCOVER = (batch_space.discover_project_tmp_roots,
            batch_space.discover_project_tmp_paths)


@pytest.mark.parametrize('discover', DISCOVER)
def test_unrelated_real_file_can_disappear_after_enumeration(tmp_path, monkeypatch, discover):
    unrelated = tmp_path/'tmp-unrelated'
    unrelated.write_bytes(b'unrelated transient file')
    project = tmp_path/'genshin-lore-fixture'
    project.mkdir()
    original = Path.iterdir

    def disappearing(directory):
        entries = tuple(original(directory))
        if directory == tmp_path and unrelated.exists():
            unrelated.unlink()
        return iter(entries)

    monkeypatch.setattr(Path, 'iterdir', disappearing)
    assert discover(tmp_path) == (project,)
    assert not unrelated.exists()


@pytest.mark.parametrize('discover', DISCOVER)
def test_unrelated_permission_denial_does_not_enter_accounting(tmp_path, monkeypatch, discover):
    unrelated = tmp_path/'another-app-private'
    unrelated.mkdir()
    original = Path.lstat

    def denied(path, *args, **kwargs):
        if path == unrelated:
            raise PermissionError('another application owns this path')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'lstat', denied)
    assert discover(tmp_path) == ()


@pytest.mark.parametrize('discover', DISCOVER)
@pytest.mark.parametrize('error', [FileNotFoundError, PermissionError])
def test_project_measurement_failure_remains_fatal(tmp_path, monkeypatch, discover, error):
    project = tmp_path/'batch-attributable'
    project.mkdir()
    original = Path.lstat

    def failed(path, *args, **kwargs):
        if path == project:
            raise error('project path cannot be measured')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'lstat', failed)
    with pytest.raises(batch_space.SpaceGateError, match='cannot lstat'):
        discover(tmp_path)


def test_named_directories_and_files_are_counted_without_cleanup_authority(tmp_path):
    temporary = tmp_path/'temporary'
    temporary.mkdir()
    directory = temporary/'genshin-lore-unowned'
    directory.mkdir()
    (directory/'payload').write_bytes(b'abc')
    file = temporary/'batch-unowned.sqlite'
    file.write_bytes(b'12345')
    (temporary/'unrelated').write_bytes(b'ignored')
    repo = tmp_path/'repo'
    repo.mkdir()
    assert batch_space.discover_project_tmp_roots(temporary) == (directory,)
    assert set(batch_space.discover_project_tmp_paths(temporary)) == {directory, file}
    inventory = batch_space.broad_inventory(repo, convention_root=temporary)
    assert inventory.logical_bytes == 8
    assert all(not row.ownership_marker for row in inventory.roots)
    assert (directory/'payload').read_bytes() == b'abc'
    assert file.read_bytes() == b'12345'
