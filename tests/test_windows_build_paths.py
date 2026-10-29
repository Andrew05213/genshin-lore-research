"""Actual tiny builders and the capacity gate, not only imported platform helpers."""
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from genshin_lore import batch_release, batch_space, platform_io
from genshin_lore import m3_7_1_provider_build as m3
from genshin_lore import narrative_7_1_build as narrative


@pytest.mark.parametrize('legacy_default', ['/tmp', Path('/tmp')])
def test_windows_gate_selects_system_temp_before_absolute(monkeypatch, tmp_path, legacy_default):
    system_temp = tmp_path/'system-temp'
    system_temp.mkdir()
    owned = system_temp/'genshin-lore-fixture'
    owned.mkdir()
    (owned/'input').write_bytes(b'synthetic')
    repo = tmp_path/'repo'
    repo.mkdir()
    # Patch only this module's OS view, not pathlib or pytest's host platform.
    monkeypatch.setattr(batch_space, 'os', SimpleNamespace(**{**vars(os), 'name':'nt'}))
    monkeypatch.setattr(batch_space.tempfile, 'gettempdir', lambda: str(system_temp))
    gate = batch_space.ProjectSpaceGate(repo, discovery_root=legacy_default)
    assert gate.discovery_root == system_temp
    assert owned in gate.accounting_tmp_roots
    assert owned in batch_space.discover_project_tmp_roots(legacy_default)
    assert batch_space._is_convention_root(owned, legacy_default)
    assert gate.check('synthetic', projected_bytes=10, cleanup=False).allocated_bytes >= 9
    # Explicit custom roots must not be silently redirected.
    custom = tmp_path/'custom-temp'
    custom.mkdir()
    assert batch_space.ProjectSpaceGate(repo, discovery_root=custom).discovery_root == custom


def windows_durability(monkeypatch, builder):
    """Run the shared helper's Windows branch; the builder must actually use it."""
    opened = []
    native_open = os.open

    def writable_open(path, flags, *args, **kwargs):
        assert flags & os.O_RDWR, 'Windows file flush requires a writable handle'
        opened.append(Path(path))
        return native_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(batch_release, 'os', SimpleNamespace(
        **{**vars(os), 'name':'nt', 'open':writable_open}))
    calls = []

    def flush(path):
        calls.append(Path(path))
        batch_release.fsync_file(path)

    monkeypatch.setattr(builder, 'fsync_file', flush, raising=False)
    return calls, opened


def test_m3_actual_builder_uses_windows_writable_flush(monkeypatch, tmp_path):
    source = tmp_path/'source'
    source.mkdir()
    raw = b'[{"id":1}]'
    family = {'family':'synthetic', 'source_path':'rows.json', 'source_physical_rows':1,
        'key_observation_fields':['id'], 'v2_text_fields':[], 'v2_textmap_paths':[],
        'consumed_field_observations':{'id':{'occurrences':1,'types':{'int':1}}},
        'source_identity':{'size_bytes':len(raw),'sha256':m3.sha(raw),'git_blob_oid':'synthetic'},
        'v1_contract':'synthetic-v1', 'v2_contract':'synthetic-v2'}
    contract = tmp_path/'contract.json'
    contract.write_text(json.dumps({'contract':'m3-7.1-ten-family-provider-input-v1',
        'source_snapshot':m3.SNAPSHOT,'families':[family],'textmap_sources':{}}))
    monkeypatch.setattr(m3, 'qualify_source', lambda *_: {'rows.json':raw})
    flushed, opened = windows_durability(monkeypatch, m3)
    output = tmp_path/'artifact.sqlite'
    result = m3.build(source, 'synthetic', output, contract)
    assert flushed == opened == [output]
    assert result['artifact']['size_bytes'] == output.stat().st_size
    with sqlite3.connect(output) as db:
        assert db.execute('SELECT COUNT(*) FROM v1_row').fetchone()[0] == 1
    with pytest.raises(FileExistsError):
        m3.build(source, 'synthetic', output, contract)


def test_narrative_actual_builder_uses_windows_publication_helpers(monkeypatch, tmp_path):
    source = tmp_path/'source'
    source.mkdir()
    (source/'rows.json').write_text('[{"id":1}]')
    scope = tmp_path/'scope.json'
    scope.write_text(json.dumps({'families':{'npc':{'field_types':{'id':{'int':1}}}},
                                'files':[], 'textmap_policy':'synthetic'}))
    monkeypatch.setattr(narrative, 'SCOPE', scope)
    monkeypatch.setattr(narrative, 'SPECS', {'npc':('rows.json',1,'id',{})})
    monkeypatch.setattr(narrative, 'check_source', lambda *_: None)
    monkeypatch.setattr(narrative, 'pairs', lambda _: iter(()))
    flushed, opened = windows_durability(monkeypatch, narrative)
    synced = []

    def sync(directory):
        synced.append(directory)
        with monkeypatch.context() as windows:
            windows.setattr(platform_io, 'os', SimpleNamespace(**{**vars(os), 'name':'nt'}))
            platform_io.sync_directory(directory)

    monkeypatch.setattr(narrative, 'sync_directory', sync, raising=False)
    output = tmp_path/'artifact.sqlite'
    result = narrative.build('npc', source, output, tmp_path)
    assert len(flushed) == 1 and flushed == opened
    assert synced == [tmp_path]
    assert result['artifact']['size_bytes'] == output.stat().st_size
    with sqlite3.connect(output) as db:
        assert db.execute('SELECT COUNT(*) FROM physical_row').fetchone()[0] == 1
    with pytest.raises(FileExistsError):
        narrative.build('npc', source, output, tmp_path)
