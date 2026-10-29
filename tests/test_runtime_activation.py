"""Only the generated local runtime pointer is mutable, not evidence receipts."""

import json
import pytest
from scripts.runtime_config import activate_generated_config


def test_fresh_config_and_known_literal_upgrade_preserve_base_access(tmp_path):
    literal = {
        "profile": "literal.json",
        "bindings": "base.json",
        "index": "index.sqlite",
        "corpus": "corpus.json",
    }
    semantic = {**literal, "profile": "semantic.json", "bindings": "vector.json"}
    activate_generated_config(tmp_path, literal, expected_previous=literal)
    activate_generated_config(tmp_path, semantic, expected_previous=literal)
    assert json.loads((tmp_path / "runtime-config.json").read_text()) == semantic
    assert json.loads((tmp_path / "runtime-config.literal.json").read_text()) == literal
    activate_generated_config(tmp_path, semantic, expected_previous=literal)
    assert not list(tmp_path.glob(".runtime-config-*"))


def test_unknown_previous_config_is_not_overwritten(tmp_path):
    target = tmp_path / "runtime-config.json"
    target.write_text('{"other":"source"}')
    before = target.read_bytes()
    with pytest.raises(ValueError, match="preserved"):
        activate_generated_config(
            tmp_path, {"new": "config"}, expected_previous={"expected": "literal"}
        )
    assert target.read_bytes() == before
    assert not (tmp_path / "runtime-config.literal.json").exists()


def test_conflicting_literal_backup_is_not_overwritten(tmp_path):
    (tmp_path / "runtime-config.json").write_text('{"expected":"literal"}')
    backup = tmp_path / "runtime-config.literal.json"
    backup.write_text('{"different":"backup"}')
    before = backup.read_bytes()
    with pytest.raises(ValueError, match="both configs preserved"):
        activate_generated_config(
            tmp_path, {"new": "config"}, expected_previous={"expected": "literal"}
        )
    assert backup.read_bytes() == before
    assert json.loads((tmp_path / "runtime-config.json").read_text()) == {
        "expected": "literal"
    }
