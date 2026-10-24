"""Real public NPC entry, SQLite publication and independent source oracle.

The fixture substitutes the fixed Git responses and a tiny sealed contract;
source decoding, quota checks, TextMaps, builder and oracle remain real.
"""
import hashlib
import json
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace

import pytest

from scripts import build_component as entry
from genshin_lore import batch_space
from genshin_lore import narrative_7_1_build as builder
from genshin_lore import narrative_7_1_contract as contract
from genshin_lore import narrative_7_1_verify as oracle


@pytest.fixture
def npc(tmp_path, monkeypatch):
    source = tmp_path/'source'
    source.mkdir()
    row_path = 'ExcelBinOutput/NpcExcelConfigData.json'
    payloads = {row_path: b'[{"id":1,"nameTextMapHash":7},'
                b'{"id":2,"nameTextMapHash":8},{"id":2,"nameTextMapHash":9}]'}
    for locale in contract.LOCALES:
        payloads[f'TextMap/TextMap{locale}.json'] = (
            b'{"7":"synthetic first","7":"synthetic duplicate","8":"synthetic unique"}')
        payloads[f'TextMap/TextMap_Medium{locale}.json'] = b'{}'
    files = []
    blobs = {}
    for name, raw in payloads.items():
        path = source/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        blobs[name] = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        files.append({'path': name, 'size_bytes': len(raw),
                      'sha256': hashlib.sha256(raw).hexdigest(), 'git_blob': blobs[name]})
    code = tmp_path/'code'
    for name in ('src/genshin_lore/narrative_7_1_build.py',
                 'src/genshin_lore/narrative_7_1_contract.py',
                 'src/genshin_lore/loading_tips_source.py',
                 'schemas/narrative-7.1-npc-dialog-reminder.sql'):
        path = code/name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(contract.ROOT/name, path)
    scope_path = code/'scope.json'
    scope_path.write_text(json.dumps({'snapshot': contract.SNAPSHOT, 'source_tree': contract.TREE,
        'files': files, 'textmap_policy': 'synthetic main/medium with duplicate pairs',
        'families': {'npc': {'field_types': {'id': {'int': 3}, 'nameTextMapHash': {'int': 3}}}}}))
    specs = {'npc': (row_path, 3, 'id', {'nameTextMapHash': 'source_authored_name'})}
    for module in (builder, oracle):
        monkeypatch.setattr(module, 'ROOT', code)
        monkeypatch.setattr(module, 'SCOPE', scope_path)
        monkeypatch.setattr(module, 'SPECS', specs)
    monkeypatch.setattr(entry, 'ROOT', code)

    def git_pin(args, **kwargs):
        assert args[:3] == ['git', '-C', str(source)]
        command = args[3:]
        if command == ['rev-parse', 'HEAD', 'HEAD^{tree}']:
            return contract.COMMIT+'\n'+contract.TREE+'\n'
        if command[0] == 'status':
            return ''
        assert command[0] == 'rev-parse'
        return blobs[command[1].removeprefix(contract.COMMIT+':')]+'\n'

    monkeypatch.setattr(entry, 'subprocess', SimpleNamespace(check_output=git_pin))
    monkeypatch.setattr(contract, 'subprocess', SimpleNamespace(check_output=git_pin))
    system = tmp_path/'system-temp'
    system.mkdir()
    monkeypatch.setattr(batch_space.tempfile, 'gettempdir', lambda: str(system))
    monkeypatch.setattr(batch_space, 'os', SimpleNamespace(**{**vars(batch_space.os), 'name': 'nt'}))
    return SimpleNamespace(source=source, output=tmp_path/'output', system=system,
                           files=files, directory=tmp_path/'output/narrative_npc')


def test_public_npc_entry_builds_and_independently_verifies_through_temp_race(npc, monkeypatch):
    unrelated = npc.system/'tmp-foreign'
    unrelated.write_bytes(b'owned by another application')
    original = Path.iterdir

    def disappearing(path):
        entries = tuple(original(path))
        if path == npc.system and unrelated.exists():
            unrelated.unlink()
        return iter(entries)

    monkeypatch.setattr(Path, 'iterdir', disappearing)
    result = entry.rebuild(npc.source, 'narrative_npc', npc.output)
    checked = json.loads((npc.directory/'verify.json').read_text())
    assert result['status'] == checked['verdict'] == 'PASS'
    assert checked['independent_source_reconstruction'] and checked['no_extras_no_omissions']
    assert checked['counts']['physical_row'] == 3
    assert checked['counts']['text_candidate'] == 9
    assert checked['states'] == {'text:missing': 3, 'text:resolved_candidate_set': 3,
                                 'text:resolved_unique': 3}
    artifact = npc.directory/'artifact.sqlite'
    before = artifact.read_bytes()
    with pytest.raises(FileExistsError):
        entry.rebuild(npc.source, 'narrative_npc', npc.output)
    assert artifact.read_bytes() == before


def test_public_npc_entry_rejects_unmeasurable_project_and_retains_invocation(npc, monkeypatch):
    project = npc.system/'genshin-lore-unreadable'
    project.mkdir()
    original = Path.lstat

    def denied(path, *args, **kwargs):
        if path == project:
            raise PermissionError('attributable project cannot be measured')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'lstat', denied)
    with pytest.raises(batch_space.SpaceGateError, match='cannot lstat'):
        entry.rebuild(npc.source, 'narrative_npc', npc.output)
    assert (npc.directory/'invocation.json').is_file()
    assert not (npc.directory/'artifact.sqlite').exists()
    assert not (npc.directory/'build.json').exists()
    assert not (npc.directory/'verify.json').exists()
    assert all(contract.sha(npc.source/item['path']) == item['sha256'] for item in npc.files)


def test_public_npc_independent_rejection_preserves_published_database(npc, monkeypatch):
    original = builder.fsync_file

    def omit_candidate(path):
        with closing(sqlite3.connect(path)) as db:
            with db:
                trigger = db.execute("SELECT sql FROM sqlite_master WHERE name='text_candidate_immutable_delete'").fetchone()[0]
                db.execute('DROP TRIGGER text_candidate_immutable_delete')
                db.execute("DELETE FROM text_candidate WHERE raw_text='synthetic duplicate'")
                db.execute(trigger)
        original(path)

    monkeypatch.setattr(builder, 'fsync_file', omit_candidate)
    with pytest.raises(ValueError, match='exact TextMap candidates'):
        entry.rebuild(npc.source, 'narrative_npc', npc.output)
    assert (npc.directory/'artifact.sqlite').is_file()
    assert (npc.directory/'build.json').is_file()
    assert not (npc.directory/'verify.json').exists()


@pytest.mark.parametrize('phase', ['file_flush', 'directory_flush'])
def test_npc_publication_failure_retains_current_database_and_sidecar(npc, monkeypatch, phase):
    retained = []

    def fail(path):
        database = Path(path) if phase == 'file_flush' else npc.directory/'artifact.sqlite'
        sidecar = Path(str(database)+'-journal')
        sidecar.write_bytes(b'synthetic failure diagnostic')
        retained.extend((database, sidecar))
        raise OSError('synthetic publication failure')

    monkeypatch.setattr(builder, 'fsync_file' if phase == 'file_flush' else 'sync_directory', fail)
    with pytest.raises(OSError, match='synthetic publication failure'):
        entry.rebuild(npc.source, 'narrative_npc', npc.output)
    assert all(path.is_file() for path in retained)
    assert retained[1].read_bytes() == b'synthetic failure diagnostic'
    assert not (npc.directory/'build.json').exists()
    assert not (npc.directory/'verify.json').exists()
