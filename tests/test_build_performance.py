"""Synthetic fidelity and rejection tests for bounded build optimizations."""
import importlib
import sqlite3

import pytest

from genshin_lore import batch_m1_textmap as textmap
from genshin_lore import character_7_1_successor_verify as character


@pytest.mark.parametrize("module_name", [
    "character_7_1_foundation_verify", "character_7_1_successor_build",
    "character_7_1_successor_verify",
])
def test_candidate_batch_retains_full_rows_nulls_and_ambiguity(module_name):
    module = importlib.import_module("genshin_lore." + module_name)
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE foundation_candidate(id,relation_id,target_primary_id,target_lut_id,target_item_id,target_source_path,target_pointer)")
    rows = [("z", "ambiguous", "same-native-second-occurrence", None, None, "source", "/1"),
            ("a", "ambiguous", "same-native-first-occurrence", None, None, "source", "/0"),
            ("b", "lut", None, "physical-lut", None, "other", "/2"),
            ("c", "unknown-parent", None, None, "physical-item", "other", "/3")]
    db.executemany("INSERT INTO foundation_candidate VALUES(?,?,?,?,?,?,?)", rows)
    queries = []
    db.set_trace_callback(queries.append)
    actual = module._candidate_rows_by_relation(db)
    assert actual["ambiguous"] == [rows[1], rows[0]]
    assert actual["lut"] == [rows[2]]
    assert actual["unknown-parent"] == [rows[3]]
    assert actual.get("legal-empty", ()) == ()
    assert len([q for q in queries if q.startswith("SELECT")]) == 1
    db.close()


@pytest.mark.parametrize("wanted", [None, set(), {"missing"}, {"1"}, {"1", "x/y~z"}])
def test_selective_textmap_preserves_all_lexical_fields_and_ordinals(wanted):
    raw = br'{"ignored":"one", "1" : "\u4e2d\ntext", "x/y~z":"\"quoted\""}'
    complete = list(textmap.iter_textmap_pairs(raw))
    expected = complete if wanted is None else [row for row in complete if row.key_text in wanted]
    assert list(textmap.iter_textmap_pairs(raw, wanted_keys=wanted)) == expected


@pytest.mark.parametrize("raw", [
    b'{"wanted":"valid","ignored":42}',
    b'{"ignored":"one","ignored":"two"}',
    b'{"wanted":"one","wanted":"two"}',
    b'{"wanted":"one","ignored":"two"}trailing',
    b'{"wanted":"one","ignored":"two"',
    b'{"wanted":"one";"ignored":"two"}',
    b'{42:"ignored"}', b'["ignored"]',
    b'{"ignored":"\xff"}', br'{"ignored":"\q"}',
])
@pytest.mark.parametrize("wanted", [set(), {"wanted"}])
def test_selective_textmap_validates_unselected_rows_and_tail(raw, wanted):
    with pytest.raises(textmap.BatchM1Error) as original:
        list(textmap.iter_textmap_pairs(raw))
    with pytest.raises(textmap.BatchM1Error) as selected:
        list(textmap.iter_textmap_pairs(raw, wanted_keys=wanted))
    assert str(selected.value) == str(original.value)


def test_selective_textmap_hashes_only_matches_but_consumes_whole_source(monkeypatch):
    calls = []
    original = textmap.sha256_text
    monkeypatch.setattr(textmap, "sha256_text", lambda value: (calls.append(value), original(value))[1])
    result = list(textmap.iter_textmap_pairs(b'{"ignored":"a","selected":"b","tail":"c"}', wanted_keys={"selected"}))
    assert len(result) == 1 and result[0].pair_ordinal == 1
    assert len(calls) == 3


@pytest.fixture
def sealed_file(tmp_path):
    path = tmp_path / "中文 space # %" / "sealed # %.sqlite"
    path.parent.mkdir()
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE parent(id INTEGER PRIMARY KEY); CREATE TABLE child(id INTEGER PRIMARY KEY,parent_id REFERENCES parent(id)); INSERT INTO parent VALUES(1); INSERT INTO child VALUES(1,1),(2,99);")
    db.close()
    return path


def test_real_unicode_uri_mmap_keeps_integrity_and_foreign_key_checks(sealed_file):
    from genshin_lore.sealed_sqlite import MMAP_LIMIT_BYTES, open_immutable_readonly
    original = sealed_file.read_bytes()
    db = open_immutable_readonly(sealed_file)
    try:
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == [("child", 2, "parent", 0)]
        assert 0 < db.execute("PRAGMA mmap_size").fetchone()[0] <= MMAP_LIMIT_BYTES
        assert db.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            db.execute("DELETE FROM child")
    finally:
        db.close()
    assert sealed_file.read_bytes() == original


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_mmap_rejects_and_preserves_every_sidecar(sealed_file, suffix):
    from genshin_lore.sealed_sqlite import open_immutable_readonly
    sidecar = sealed_file.with_name(sealed_file.name + suffix)
    sidecar.write_bytes(b"synthetic failure evidence")
    with pytest.raises(ValueError, match="sidecar"):
        open_immutable_readonly(sealed_file)
    assert sidecar.read_bytes() == b"synthetic failure evidence"


@pytest.mark.parametrize("limit", [-1, True, 2**32])
def test_mmap_limit_is_bounded(sealed_file, limit):
    from genshin_lore.sealed_sqlite import open_immutable_readonly
    with pytest.raises(ValueError, match="mmap"):
        open_immutable_readonly(sealed_file, mmap_bytes=limit)


def test_readonly_opener_never_creates_missing_files(tmp_path):
    from genshin_lore.sealed_sqlite import open_immutable_readonly
    missing = tmp_path / "missing.sqlite"
    with pytest.raises((ValueError, FileNotFoundError)):
        open_immutable_readonly(missing)
    assert not missing.exists()


@pytest.mark.parametrize("mutation", [None, "missing", "all_missing", "extra", "target", "shrink_parent"])
def test_batched_lore_oracle_still_derives_complete_candidates(mutation):
    """Exercise the actual full-denominator leaf oracle, not proof substitution.

    Foundation/source qualification is covered separately. This fixture has
    real Lore schema, all 167/124/974 physical parent rows, ambiguous targets,
    and a legitimate empty candidate set.
    """
    foundation = sqlite3.connect(":memory:")
    foundation.executescript("""
        CREATE TABLE foundation_primary_row(id,source_path,ordinal,pointer);
        CREATE TABLE foundation_relation(id,source_row_id,kind,status,candidate_count);
        CREATE TABLE foundation_text_owner(id,row_id,field_name,pointer);
        CREATE TABLE foundation_field(id,row_id,pointer,field_name,role,source_field_ordinal,child_ordinal);
        CREATE TABLE foundation_candidate(id,relation_id,target_primary_id,target_lut_id,target_item_id,target_source_path,target_pointer);
    """)
    out = sqlite3.connect(":memory:")
    out.executescript((character.ROOT / character.SCHEMAS["lore"]).read_text())
    text = sqlite3.connect(":memory:")
    text.execute("CREATE TABLE resolution(owner_id)")
    text.executemany("INSERT INTO resolution VALUES(?)", (("synthetic",) for _ in range(34817 * 3)))
    for basename, kind, count in (("AvatarExcelConfigData.json", "avatar", 167),
                                  ("FetterInfoExcelConfigData.json", "fetter_info", 124),
                                  ("FetterStoryExcelConfigData.json", "story", 974)):
        source = "ExcelBinOutput/" + basename
        for ordinal in range(count):
            row_id, pointer = f"{kind}-{ordinal}", f"/{ordinal}"
            foundation.execute("INSERT INTO foundation_primary_row VALUES(?,?,?,?)", (row_id, source, ordinal, pointer))
            relation = "relation-" + row_id
            targets = () if kind == "avatar" or (kind == "story" and ordinal == 973) else ("avatar-0", "avatar-1")
            status = "not_applicable" if kind == "avatar" else "missing" if not targets else "resolved_candidate_set"
            out.execute("INSERT INTO lore_record VALUES(?,?,?,?,?,?,?)", (row_id, kind, source, ordinal, pointer, status, len(targets)))
            if kind != "avatar":
                foundation.execute("INSERT INTO foundation_relation VALUES(?,?,?,?,?)", (relation, row_id, "source_avatar_id_candidate", status, len(targets)))
                for target in targets:
                    foundation.execute("INSERT INTO foundation_candidate VALUES(?,?,?,?,?,?,?)", (relation + target, relation, target, None, None, "ExcelBinOutput/AvatarExcelConfigData.json", "/" + target.rsplit("-", 1)[1]))
                    out.execute("INSERT INTO lore_avatar_candidate VALUES(?,?,?)", (row_id, relation, target))
                names = ("finishConds",) if kind == "fetter_info" else ("finishConds", "openConds")
                for field_ordinal, name in enumerate(names):
                    values = (row_id + name, row_id, pointer + "/" + name, name, "source_condition_array", field_ordinal, None)
                    foundation.execute("INSERT INTO foundation_field VALUES(?,?,?,?,?,?,?)", values)
                    out.execute("INSERT INTO lore_raw_field_ref VALUES(?,?,?,?,?,?,?)", (row_id, *values[:1], *values[2:]))
    if mutation == "missing":
        out.execute("DELETE FROM lore_avatar_candidate WHERE row_id='story-0' AND avatar_row_id='avatar-0'")
    elif mutation in ("all_missing", "shrink_parent"):
        out.execute("DELETE FROM lore_avatar_candidate")
        if mutation == "shrink_parent":
            out.execute("UPDATE lore_record SET owner_candidate_count=0")
    elif mutation == "extra":
        out.execute("INSERT INTO lore_avatar_candidate VALUES('story-0','relation-story-0','extra-physical-target')")
    elif mutation == "target":
        out.execute("UPDATE lore_avatar_candidate SET avatar_row_id='forged-physical-target' WHERE row_id='story-0' AND avatar_row_id='avatar-0'")
    queries = []
    foundation.set_trace_callback(queries.append)
    try:
        if mutation is None:
            result = character._verify_lore(out, foundation, text)
            assert result["record_counts"] == {"avatar": 167, "fetter_info": 124, "story": 974}
            assert result["avatar_candidates"] == (124 + 973) * 2
        else:
            with pytest.raises(ValueError, match="Lore/Story"):
                character._verify_lore(out, foundation, text)
        candidate_reads = [query for query in queries if "FROM foundation_candidate" in query]
        assert len(candidate_reads) == 1
    finally:
        out.close()
        foundation.close()
        text.close()


def test_sidecar_appearing_during_open_is_preserved_and_connection_closed(sealed_file, monkeypatch):
    from genshin_lore import sealed_sqlite
    original_connect = sqlite3.connect
    opened = []
    sidecar = sealed_file.with_name(sealed_file.name + "-journal")

    def connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        opened.append(conn)
        sidecar.write_bytes(b"synthetic concurrent diagnostic")
        return conn

    monkeypatch.setattr(sealed_sqlite.sqlite3, "connect", connect)
    with pytest.raises(ValueError, match="sidecar"):
        sealed_sqlite.open_immutable_readonly(sealed_file)
    assert sidecar.read_bytes() == b"synthetic concurrent diagnostic"
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")
