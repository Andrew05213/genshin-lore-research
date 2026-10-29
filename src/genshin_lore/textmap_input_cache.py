"""Bounded, invocation-local lexical input reuse; never evidence authority.

Each independent reader compiles its own index with its own complete parser.
Only offsets into identical source bytes are reused; projection/qualification
results and cross-process caches do not live here.
"""
from array import array
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import sys

_session = ContextVar('textmap_input_session', default=None)


class LexicalIndex:
    def __init__(self):
        self.text = ''
        self.keys = {}
        self.positions = array('Q')

    def add(self, ordinal, key, key_start, key_end, value_start, value_end):
        self.keys[key] = ordinal
        self.positions.extend((key_start, key_end, value_start, value_end))

    def selected(self, wanted):
        for ordinal, key in sorted((self.keys[k], k) for k in wanted if k in self.keys):
            a, b, c, d = self.positions[ordinal*4:ordinal*4+4]
            yield ordinal, key, self.text[a:b], self.text[c:d]

    def size(self):
        return (sys.getsizeof(self.text) + sys.getsizeof(self.keys)
                + sys.getsizeof(self.positions) + sys.getsizeof(self)
                + sum(sys.getsizeof(k)+sys.getsizeof(v) for k, v in self.keys.items()))


@contextmanager
def textmap_session(megabytes=1024):
    if type(megabytes) is not int or not 0 <= megabytes <= 1024:
        raise ValueError('TextMap cache must be 0..1024 MiB per independent reader')
    state = {'limit': megabytes*1024**2, 'readers': {}, 'hits': {}, 'misses': {}, 'peak_bytes': {}}
    token = _session.set(state)
    try:
        yield state
    finally:
        _session.reset(token)
        # Clear retained source text even if a caller keeps the statistics.
        state['readers'].clear()


def cached_index(reader, raw, compile_index):
    state = _session.get()
    if state is None or not state['limit']:
        return None
    key = hashlib.sha256(raw).digest()
    entries = state['readers'].setdefault(reader, OrderedDict())
    if key in entries:
        state['hits'][reader] = state['hits'].get(reader, 0)+1
        entries.move_to_end(key)
        return entries[key][0]
    state['misses'][reader] = state['misses'].get(reader, 0)+1
    index = compile_index()  # Exceptions never register an incomplete input.
    size = index.size() + sys.getsizeof(key) + 256
    if size <= state['limit']:
        while entries and sum(v[1] for v in entries.values())+size > state['limit']:
            entries.popitem(last=False)
        entries[key] = index, size
        state['peak_bytes'][reader] = max(state['peak_bytes'].get(reader, 0),
                                         sum(v[1] for v in entries.values()))
    return index
