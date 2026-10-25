"""Caches reuse input syntax, never incomplete parses or oracle projections."""
import json

import pytest

from genshin_lore import batch_m1_textmap as builder
from genshin_lore import batch_m1_verifier as oracle
from genshin_lore.textmap_input_cache import textmap_session


RAW = br'{"ignored":"a", "1" : "\u4e2d\ntext", "x/y~z":"\"quoted\""}'


@pytest.mark.parametrize('reader,error', [(builder.iter_textmap_pairs, builder.BatchM1Error),
                                        (oracle._iter_source_pairs, oracle.BatchM1VerificationError)])
def test_cache_and_uncached_complete_lexical_observations_match(reader, error):
    complete = list(reader(RAW))
    with textmap_session(1) as state:
        for wanted in ({'1'}, {'x/y~z', '1'}, set(), {'missing'}):
            expected = [r for r in complete if r.key_text in wanted]
            assert list(reader(RAW, wanted_keys=wanted)) == expected
        assert sum(state['misses'].values()) == 1
        assert sum(state['hits'].values()) == 3
        assert max(state['peak_bytes'].values()) <= 1024**2
    assert not state['readers']


@pytest.mark.parametrize('raw', [b'{"good":"yes","bad":42}', b'{"good":"yes","good":"no"}',
    b'{"good":"yes"}trailing', b'{"good":"yes"', b'{"bad":"\xff"}', br'{"bad":"\q"}'])
@pytest.mark.parametrize('reader,error', [(builder.iter_textmap_pairs, builder.BatchM1Error),
                                        (oracle._iter_source_pairs, oracle.BatchM1VerificationError)])
def test_cache_cannot_hide_unselected_errors_or_register_partial_parse(raw, reader, error):
    with textmap_session(1) as state:
        for _ in range(2):
            with pytest.raises(error):
                list(reader(raw, wanted_keys={'good'}))
        assert all(not entries for entries in state['readers'].values())


def test_readers_do_not_consume_each_others_compiled_input_or_exceptions(monkeypatch):
    with textmap_session(1) as state:
        list(builder.iter_textmap_pairs(RAW, wanted_keys={'1'}))
        monkeypatch.setattr(builder, 'iter_textmap_pairs', lambda *a, **k: pytest.fail('oracle used builder'))
        result = list(oracle._iter_source_pairs(RAW, wanted_keys={'1'}))
        assert result[0].raw_text == '\u4e2d\ntext'
        assert state['hits'] == {} and state['misses'] == {'builder': 1, 'oracle': 1}


def test_changed_bytes_miss_even_when_text_or_size_is_similar():
    with textmap_session(1) as state:
        first = list(builder.iter_textmap_pairs(b'{"1":"A"}', wanted_keys={'1'}))
        second = list(builder.iter_textmap_pairs(b'{"1":"B"}', wanted_keys={'1'}))
        assert first[0].raw_text == 'A' and second[0].raw_text == 'B'
        assert state['misses']['builder'] == 2


def test_oversized_inputs_are_validated_but_not_retained():
    raw = json.dumps({'1': 'x'*2_000_000}).encode()
    with textmap_session(1) as state:
        for _ in range(2):
            assert list(builder.iter_textmap_pairs(raw, wanted_keys=set())) == []
        assert state['misses']['builder'] == 2 and not state['readers']['builder']


def test_disabled_and_nested_sessions_do_not_leak_or_reuse():
    with textmap_session(1) as outer:
        list(builder.iter_textmap_pairs(RAW, wanted_keys=set()))
        with textmap_session(0) as inner:
            list(builder.iter_textmap_pairs(RAW, wanted_keys=set()))
            assert not inner['readers']
        list(builder.iter_textmap_pairs(RAW, wanted_keys=set()))
        assert outer['hits']['builder'] == 1
    with textmap_session(1) as fresh:
        list(builder.iter_textmap_pairs(RAW, wanted_keys=set()))
        assert fresh['misses']['builder'] == 1 and not fresh['hits']
