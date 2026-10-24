"""File-transport faults only; actual native paths run in semantic_native.py."""
from pathlib import Path
from types import SimpleNamespace
import os
import shutil
import tempfile

import pytest

from genshin_lore import hnsw_io


class BinaryIndex:
    payload = b"synthetic-native-format-stand-in"

    def index_file_size(self):
        return len(self.payload)

    def save_index(self, path):
        Path(path).write_bytes(self.payload)

    def load_index(self, path, **kwargs):
        self.loaded = (Path(path).read_bytes(), kwargs)


@pytest.fixture
def private(tmp_path, monkeypatch):
    work = tmp_path / "private"

    def create(**kwargs):
        assert kwargs["prefix"] == "genshin-lore-hnsw-"
        work.mkdir()
        return str(work)

    monkeypatch.setattr(hnsw_io, "tempfile", SimpleNamespace(mkdtemp=create, mkstemp=tempfile.mkstemp))
    monkeypatch.setattr(hnsw_io, "_native_path", lambda directory: str(directory / "index.hnsw"))
    return work


def test_publish_fsynced_exact_bytes(tmp_path, private, monkeypatch):
    synced = []
    real_sync = os.fsync
    monkeypatch.setattr(hnsw_io, "os", SimpleNamespace(
        fsync=lambda fd: (synced.append(fd), real_sync(fd))[-1], fdopen=os.fdopen, link=os.link))
    target = tmp_path / "中文 #%.hnsw"
    hnsw_io.save_index(BinaryIndex(), target)
    assert target.read_bytes() == BinaryIndex.payload
    assert len(synced) == 2
    assert not private.exists()
    assert not list(tmp_path.glob(".hnsw-*.partial"))


def test_existing_target_is_never_overwritten(tmp_path, private):
    target = tmp_path / "index.hnsw"
    target.write_bytes(b"old-artifact")
    with pytest.raises(ValueError, match="already exists"):
        hnsw_io.save_index(BinaryIndex(), target)
    assert target.read_bytes() == b"old-artifact"
    assert not private.exists()


@pytest.mark.parametrize("payload", [None, b"", b"truncated"])
def test_silent_missing_empty_or_truncated_save_rejected(tmp_path, private, payload):
    class Broken(BinaryIndex):
        def save_index(self, path):
            if payload is not None:
                Path(path).write_bytes(payload)

    target = tmp_path / "index.hnsw"
    with pytest.raises(OSError, match="complete native binary"):
        hnsw_io.save_index(Broken(), target)
    assert not target.exists()
    assert private.exists()
    if payload is not None:
        assert (private / "index.hnsw").read_bytes() == payload


def test_native_save_error_retains_partial_file(tmp_path, private):
    class Broken(BinaryIndex):
        def save_index(self, path):
            Path(path).write_bytes(b"partial")
            raise RuntimeError("native save failed")

    with pytest.raises(RuntimeError, match="native save failed"):
        hnsw_io.save_index(Broken(), tmp_path / "index.hnsw")
    assert (private / "index.hnsw").read_bytes() == b"partial"


def test_fsync_error_prevents_publication(tmp_path, private, monkeypatch):
    def fail(fd):
        raise OSError("fsync failed")

    monkeypatch.setattr(hnsw_io, "os", SimpleNamespace(fsync=fail))
    with pytest.raises(OSError, match="fsync failed"):
        hnsw_io.save_index(BinaryIndex(), tmp_path / "index.hnsw")
    assert (private / "index.hnsw").read_bytes() == BinaryIndex.payload
    assert not (tmp_path / "index.hnsw").exists()


def test_copy_error_retains_publication_partial(tmp_path, private, monkeypatch):
    def fail(reader, writer, **kwargs):
        writer.write(b"partial-copy")
        raise OSError("copy failed")

    monkeypatch.setattr(hnsw_io, "shutil", SimpleNamespace(disk_usage=shutil.disk_usage, copyfileobj=fail))
    with pytest.raises(OSError, match="copy failed"):
        hnsw_io.save_index(BinaryIndex(), tmp_path / "index.hnsw")
    assert not (tmp_path / "index.hnsw").exists()
    assert next(tmp_path.glob(".hnsw-*.partial")).read_bytes() == b"partial-copy"
    assert (private / "index.hnsw").read_bytes() == BinaryIndex.payload


def test_publication_race_does_not_clobber(tmp_path, private, monkeypatch):
    def raced_link(source, target):
        Path(target).write_bytes(b"other-invocation")
        os.link(source, target)

    monkeypatch.setattr(hnsw_io, "os", SimpleNamespace(fsync=os.fsync, fdopen=os.fdopen, link=raced_link))
    with pytest.raises(FileExistsError):
        hnsw_io.save_index(BinaryIndex(), tmp_path / "index.hnsw")
    assert (tmp_path / "index.hnsw").read_bytes() == b"other-invocation"
    assert private.exists() and list(tmp_path.glob(".hnsw-*.partial"))


def test_load_exact_bytes_and_native_options(tmp_path, private, monkeypatch):
    monkeypatch.setattr(hnsw_io, "_WINDOWS", True)
    source = tmp_path / "中文 #%.hnsw"
    source.write_bytes(BinaryIndex.payload)
    index = BinaryIndex()
    hnsw_io.load_index(index, source, max_elements=3)
    assert index.loaded == (BinaryIndex.payload, {"max_elements": 3})
    assert source.read_bytes() == BinaryIndex.payload
    assert not private.exists()


def test_load_error_is_propagated_and_bytes_retained(tmp_path, private, monkeypatch):
    monkeypatch.setattr(hnsw_io, "_WINDOWS", True)
    source = tmp_path / "中文.hnsw"
    source.write_bytes(b"bad-native-bytes")

    class Broken(BinaryIndex):
        def load_index(self, path, **kwargs):
            assert Path(path).read_bytes() == b"bad-native-bytes"
            raise RuntimeError("native corruption")

    with pytest.raises(RuntimeError, match="native corruption"):
        hnsw_io.load_index(Broken(), source)
    assert source.read_bytes() == (private / "index.hnsw").read_bytes()


def test_missing_load_does_not_create_artifact(tmp_path, private):
    source = tmp_path / "不存在.hnsw"
    with pytest.raises(ValueError, match="existing regular file"):
        hnsw_io.load_index(BinaryIndex(), source)
    assert not source.exists() and not private.exists()


@pytest.mark.parametrize("operation", ["save", "load"])
def test_temp_capacity_failure_preserves_source(tmp_path, private, monkeypatch, operation):
    monkeypatch.setattr(hnsw_io, "_WINDOWS", True)
    monkeypatch.setattr(hnsw_io, "shutil", SimpleNamespace(disk_usage=lambda _: SimpleNamespace(free=0)))
    target = tmp_path / "中文.hnsw"
    if operation == "load":
        target.write_bytes(BinaryIndex.payload)
    with pytest.raises(OSError, match="space"):
        getattr(hnsw_io, operation + "_index")(BinaryIndex(), target)
    assert private.exists()
    assert target.read_bytes() == BinaryIndex.payload if operation == "load" else not target.exists()
