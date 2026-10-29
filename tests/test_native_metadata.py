"""Explicit Native verifier dependencies: exact bytes and no sibling fallback."""
import json

import pytest

from scripts.build import seal, SNAPSHOT
from genshin_lore import native_book_music_7_1_verify as book
from genshin_lore import native_aux_quest_7_1_verify as aux


def metadata(root, payload):
    root.mkdir()
    artifact = root/'synthetic.sqlite'
    artifact.write_bytes(payload)
    identity = seal(artifact)
    message = root/'manifests/native-archive-7.1/message-runtime-v1.json'
    message.parent.mkdir(parents=True)
    message.write_text(json.dumps({'artifact':identity,'release':{'run_id':root.name}}))
    quest,bulk = aux.dependency_manifests(root)
    quest.parent.mkdir(parents=True)
    item = {'source_snapshot':SNAPSHOT,'artifact':identity}
    quest.write_text(json.dumps({'components':{'quest_narrative_archive':item}}))
    bulk.write_text(json.dumps(item))
    return artifact,message


def test_message_binding_uses_explicit_output(tmp_path):
    for name in ('a','b'):
        artifact,path = metadata(tmp_path/name,name.encode())
        assert book.message_binding(artifact,path)['release']['run_id'] == name


def test_message_missing_explicit_manifest_does_not_fall_back(tmp_path,monkeypatch):
    artifact,path = metadata(tmp_path/'a',b'synthetic')
    monkeypatch.setattr(book,'ROOT',tmp_path/'a')
    assert book.message_binding(artifact)['artifact'] == seal(artifact)
    with pytest.raises(FileNotFoundError):
        book.message_binding(artifact,tmp_path/'missing.json')


@pytest.mark.parametrize('field,value',[('size_bytes',0),('sha256','bad')])
def test_message_identity_mismatch_is_rejected(tmp_path,field,value):
    artifact,path = metadata(tmp_path/'a',b'synthetic')
    declaration = json.loads(path.read_text())
    declaration['artifact'][field] = value
    path.write_text(json.dumps(declaration))
    with pytest.raises(ValueError,match='message dependency identity'):
        book.message_binding(artifact,path)


def test_aux_uses_own_manifests_and_checks_artifacts_before_navigation(tmp_path,monkeypatch):
    a,_ = metadata(tmp_path/'a',b'a')
    b,_ = metadata(tmp_path/'b',b'other')
    monkeypatch.setattr(aux,'ROOT',tmp_path/'absent-source-manifests')
    assert aux.dependency_manifests(tmp_path/'a') != aux.dependency_manifests(tmp_path/'b')
    with pytest.raises(ValueError,match='accepted dependency artifact mismatch'):
        aux.expected_external(tmp_path,a,b,metadata_root=tmp_path/'a')
    with pytest.raises(FileNotFoundError):
        aux.expected_external(tmp_path,a,a,metadata_root=tmp_path/'missing')
