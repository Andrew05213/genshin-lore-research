"""New snapshot routing must never relabel historical evidence or qualify it."""
import json
from pathlib import Path

import pytest

from genshin_lore.source_versions import PINS, source_pin
from genshin_lore.schema20_7_1_candidate import qualify_source
from genshin_lore.schema20_7_1_special import coordinate


@pytest.mark.parametrize("version", ["7.1", "7.2"])
def test_registered_exact_source(version):
    commit, tree = PINS[version]
    snapshot = f"anime-game-data:{version}:{commit}"
    assert source_pin(snapshot) == (version, commit, tree)


@pytest.mark.parametrize("snapshot", [
    "latest", "anime-game-data:7.2:latest", "anime-game-data:7.2:" + PINS["7.1"][0],
    "anime-game-data:7.1:" + PINS["7.2"][0], None,
])
def test_no_version_or_commit_fallback(snapshot):
    with pytest.raises(ValueError, match="exact source"):
        source_pin(snapshot)


def test_same_coordinate_in_new_snapshot_keeps_distinct_identity():
    path, pointer = "ExcelBinOutput/Synthetic.json", "/0"
    old = coordinate(path, pointer)
    new = coordinate(path, pointer, snapshot_id=f"anime-game-data:7.2:{PINS['7.2'][0]}")
    assert old != new
    assert new == coordinate(path, pointer, snapshot_id=f"anime-game-data:7.2:{PINS['7.2'][0]}")


@pytest.mark.parametrize("version", ["7.1", "7.2"])
def test_source_qualification_exact_pin_and_cleanliness(monkeypatch, version):
    import genshin_lore.schema20_7_1_candidate as assembly
    commit, tree = PINS[version]
    snapshot = f"anime-game-data:{version}:{commit}"
    replies = iter([commit, tree, ""])
    monkeypatch.setattr(assembly.subprocess, "check_output", lambda *a, **kw: next(replies))
    qualify_source(Path("synthetic"), snapshot)
    replies = iter([commit, tree, " M synthetic.json"])
    with pytest.raises(ValueError, match="clean"):
        qualify_source(Path("synthetic"), snapshot)
    replies = iter([PINS["7.1" if version == "7.2" else "7.2"][0]])
    with pytest.raises(ValueError, match="commit/tree"):
        qualify_source(Path("synthetic"), snapshot)


def test_new_source_contract_is_not_historical_acceptance():
    root = Path(__file__).resolve().parents[1]
    for recipe in ("m3-7.2-provider-mappings.json", "special-core-7.2-mappings.json"):
        data = json.loads((root / "recipes" / recipe).read_text())
        assert data["product_accepted"] is False
        for row in data["mappings"]:
            assert row["status"] == "UNBUILT_SOURCE_MIGRATION"
            assert row["source_commit"] == PINS["7.2"][0]
            assert "candidate_artifact" not in row
            assert "parser_build_id" not in row
            assert "ingest_run_id" not in row


def test_special_contract_pin_cannot_be_forged(tmp_path):
    from genshin_lore.special_core_7_1_build import build
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({
        "source_snapshot": f"anime-game-data:7.2:{PINS['7.2'][0]}",
        "source_commit": PINS["7.1"][0], "source_tree": PINS["7.1"][1],
    }))
    artifact = tmp_path / "artifact.sqlite"
    with pytest.raises(ValueError, match="pin mismatch"):
        build(tmp_path, "synthetic", artifact, contract_path=contract)
    assert not artifact.exists()


def test_source_authored_voice_fields_keep_physical_pointers():
    from genshin_lore.character_7_1_voice import decode_fetter_lut, decode_fetter_item
    lut = decode_fetter_lut("synthetic", {"gameTrigger": "Fetter", "gameTriggerArgs": 10,
                                        "fileID": 20}, source_version="7.2")
    assert (lut.slot, lut.item_path) == ("10", "BinOutput/Voice/Items/20.json")
    item, audio = decode_fetter_item(lut.item_path, "synthetic", {
        "gameTrigger": "Fetter", "gameTriggerArgs": 10, "guid": "synthetic",
        "sourceNames": [{"sourceFileName": "synthetic.wem", "avatarName": "synthetic", "rate": 1.0}],
    }, source_version="7.2")
    assert item.variant_count == 1
    assert audio[0].pointer == "/synthetic/sourceNames/0"
    assert audio[0].audio_path == "synthetic.wem"
    with pytest.raises(ValueError, match="category/key/slot"):
        decode_fetter_item(lut.item_path, "synthetic", {"gameTrigger": "Fetter"}, source_version="7.2")


def test_rename_missing_legacy_id_is_not_guessed():
    from genshin_lore.character_7_1_physical import decode_primary_row
    row = {"relatedAvatarID": 42, "multiLangDefaultNameList": [], "JLOMDPAMHIB": 9}
    decoded = decode_primary_row("ExcelBinOutput/AvatarRenameExcelConfigData.json", 0,
                                 row, json.dumps(row), source_version="7.2")
    assert ("relatedAvatarID", 42) in decoded.source_fields
    assert all(name != "renameID" for name, value in decoded.source_fields)
    with pytest.raises(ValueError, match="renameID"):
        decode_primary_row("ExcelBinOutput/AvatarRenameExcelConfigData.json", 0, row, json.dumps(row))


def test_authored_quest_talk72_and_independent_decoder():
    from genshin_lore.quest_talk_7_1_physical import decode_talk, decode_quest, root_signature_digest
    from genshin_lore.quest_talk_7_1_verify import decode_talk_source, decode_quest_source
    talk = {"talkId": 10, "dialogList": [{"id": 11, "nextDialogs": [12],
              "talkContentTextMapHash": 99}, {"id": 12}]}
    decoded = decode_talk(talk, [], registered_signatures={root_signature_digest(talk)}, source_version="7.2")
    oracle = decode_talk_source(talk, {"shapes": []}, "7.2")
    assert decoded["native_id"] == oracle[2] == 10
    assert decoded["nodes"][0]["pointer"] == oracle[3][0]["pointer"] == "/dialogList/0"
    assert decoded["nodes"][0]["next"] == oracle[3][0]["next"] == [12]
    quest = {"id": 5, "subQuests": [{"subId": 6}], "talks": [{"id": 10, "initDialog": 11}]}
    decoded = decode_quest(quest, registered_signatures={root_signature_digest(quest)}, source_version="7.2")
    oracle = decode_quest_source(quest, {"shapes": []}, "7.2")
    assert decoded["child_pointers"] == ["/subQuests/0"]
    assert oracle[:3] == (5, "subQuests", [{"subId": 6}])
    assert decoded["references"][0]["pointer"] == oracle[3][0]["pointer"] == "/talks/0"


def test_new_quest_shapes_do_not_guess_unknown_fields_or_speaker():
    from genshin_lore.quest_talk_7_1_physical import decode_talk, root_signature_digest
    root = {"talkId": 9, "opaque": 123}
    with pytest.raises(ValueError, match="unregistered"):
        decode_talk(root, [], registered_signatures=set(), source_version="7.2")
    result = decode_talk(root, [], registered_signatures={root_signature_digest(root)}, source_version="7.2")
    assert result["kind"] == "mapping_unresolved" and not result["nodes"]
    assert "speaker" not in result
