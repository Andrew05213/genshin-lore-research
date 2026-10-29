"""Explicit model acquisition tested with synthetic bytes and no network."""

import hashlib
import io
import json
from pathlib import Path

import pytest
from scripts.maintenance import fetch_semantic_model as fetcher
from genshin_lore.semantic_discovery import tree_sha256


def setup(tmp_path, monkeypatch, payload=b"synthetic model, not weights"):
    code = tmp_path / "code"
    (code / "recipes").mkdir(parents=True)
    expected = tmp_path / "expected"
    expected.mkdir()
    (expected / "model.bin").write_bytes(payload)
    spec = {
        "model_id": "synthetic",
        "source_repo": "synthetic/model",
        "revision": "f" * 40,
        "model_tree_sha256": tree_sha256(expected),
        "files": {
            "model.bin": {
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        },
    }
    (code / "recipes/embedding-model.json").write_text(
        json.dumps(spec), encoding="utf-8"
    )
    monkeypatch.setattr(fetcher, "ROOT", code)
    return spec, payload


def test_explicit_fetch_is_pinned_and_existing_valid_files_need_no_network(
    tmp_path, monkeypatch
):
    spec, payload = setup(tmp_path, monkeypatch)
    urls = []

    def response(url, **kwargs):
        urls.append(url)
        return io.BytesIO(payload)

    monkeypatch.setattr(fetcher.urllib.request, "urlopen", response)
    assert fetcher.fetch(tmp_path / "cache")["status"] == "PASS"
    assert spec["revision"] in urls[0]
    assert fetcher.fetch(tmp_path / "cache")["status"] == "PASS"
    assert len(urls) == 1
    assert fetcher.fetch(tmp_path / "cache", check_only=True)["status"] == "PASS"
    assert len(urls) == 1


def test_failed_download_is_not_promoted_or_overwritten(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        fetcher.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"wrong")
    )
    with pytest.raises(ValueError, match="seal differs"):
        fetcher.fetch(tmp_path / "cache")
    assert (tmp_path / "cache/model/model.bin.part").read_bytes() == b"wrong"
    assert not (tmp_path / "cache/model/model.bin").exists()
    with pytest.raises(FileExistsError):
        fetcher.fetch(tmp_path / "cache")


def test_missing_or_extra_files_fail_offline_check(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="missing or differs"):
        fetcher.fetch(tmp_path / "cache", check_only=True)


def test_mixed_case_model_names_preserve_the_pinned_tree_on_every_platform(
    tmp_path, monkeypatch
):
    spec, _ = setup(tmp_path, monkeypatch)
    files = {
        "README.md": b"synthetic model readme",
        "config.json": b'{"synthetic":true}',
    }
    # Fixed independently from the implementation: case-sensitive relative
    # names, NUL, then each file's binary SHA256. WindowsPath ordering differs.
    spec["model_tree_sha256"] = (
        "c766933047b04e1d9d1096b4897d3e35004a00cd56e29154cef43ee57ff5620b"
    )
    spec["files"] = {
        name: {"size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        for name, raw in files.items()
    }
    (tmp_path / "code/recipes/embedding-model.json").write_text(json.dumps(spec))
    monkeypatch.setattr(
        fetcher.urllib.request,
        "urlopen",
        lambda url, **kwargs: io.BytesIO(files[url.rsplit("/", 1)[1]]),
    )
    cache = tmp_path / "cache"
    assert fetcher.fetch(cache)["model_tree_sha256"] == spec["model_tree_sha256"]
    assert fetcher.fetch(cache, check_only=True)["status"] == "PASS"
