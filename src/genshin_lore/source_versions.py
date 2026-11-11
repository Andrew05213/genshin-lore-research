"""Explicit source pins. Game version is independent of schema/parser format."""

PINS = {
    '7.1': ('9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99', 'aa578c08c402af39b41f1ffc95124d10f5410272'),
    '7.2': ('d54f6f0d423ded9d055bb740a3e5f3aa8f2667f1', '5b334e711319726c504da95dbfe6ebb26baaa7d3'),
}


def source_pin(snapshot):
    """Accept only a declared exact snapshot; never latest or another commit."""
    for version, (commit, tree) in PINS.items():
        if snapshot == f'anime-game-data:{version}:{commit}':
            return version, commit, tree
    raise ValueError('unsupported exact source snapshot')


R0_IMPLEMENTATION_PATHS = (
    'src/genshin_lore/batch_m3_physical.py',
    'src/genshin_lore/batch_m0_baseline.py',
)


def r0_identity(snapshot, implementation):
    """Expected metadata only; the independent oracle still rebuilds every row."""
    import hashlib
    import json
    version, _, _ = source_pin(snapshot)
    if version == '7.1':
        return ('pb-batch-m3-r0-physical-7.1:23e78b08f997ab610cd55a11f91f2f4c252260af',
                'ir-batch-m3-r0-physical-7.1:9fc2c32cc6bb81dd6091e504d43e40bdcf930ea2')
    def digest(value):
        return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:40]
    parser = f'pb-r0-{version}:' + digest(['physical-file-storage-v2',implementation])
    return parser, f'ir-r0-{version}:' + digest([snapshot,parser])


def scope_pin(scope):
    """Require a scope's independently declared commit/tree to match its snapshot."""
    version, commit, tree = source_pin(scope['snapshot_id'])
    if (scope['source_commit'], scope['source_tree']) != (commit, tree):
        raise ValueError('source scope commit/tree mismatch')
    return version, commit, tree
