"""Constructor wiring with synthetic evidence; publication gates are tested separately."""
import json
from pathlib import Path

import pytest

from genshin_lore import research71_minor_release as minor
from genshin_lore import research71_successor as successor
from genshin_lore.research71_candidate_query import CandidateResearch71
from genshin_lore.research_query import ResearchQuery
from genshin_lore.source_versions import PINS


@pytest.mark.parametrize('published', [False, True])
def test_successor_constructor_initializes_navigation_snapshot(tmp_path, monkeypatch, published):
    snapshot = 'anime-game-data:7.1:' + PINS['7.1'][0]
    metadata = tmp_path / 'metadata'
    registry = metadata / 'manifests/research-7.1/7.1-capability-registry-v1.json'
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({'capabilities': [{
        'capability_id': 'readable_archive', 'supported_operations': ['read'], 'limitations': []}]}))
    paths = []

    def qualified_query(self, corpus, bindings, index, profile, **kwargs):
        # Isolate constructor wiring from data/publication qualification. The
        # real navigation _entries(), registry, version routing and refs run.
        self.manifest = {'snapshot': {'id': snapshot},
                         'candidate_adapter_revision': 'research71-explicit-physical-adapters-v1',
                         'adapter_implementation_sha256': {}}
        self.manifest_path = metadata / 'manifests/research/corpus.json'
        self.corpus_ref = 'synthetic-corpus'
        self.components = {'7.1-readable_archive': {
            'registered_capability_id': 'readable_archive', 'family': 'readable_archive',
            'contract': 'synthetic', 'artifact': {'size_bytes': 0, 'sha256': '0' * 64}}}
        self.bindings = {'artifacts': {'7.1-readable_archive': 'synthetic.sqlite'}}
        self.db = None
        self.unavailable = {}
        self._source = lambda key: paths.append(key)

    config = {'successor_contract': 'research71-discovery-navigation-v1.1', 'publishable': False,
              'successor_implementation_sha256': {}, 'sparse_first_policy': {}, 'discovery_domains': {}}
    monkeypatch.delenv('GENSHIN_CITATION_HANDOFF_DIR', raising=False)
    if published:
        monkeypatch.setattr(minor, 'qualify', lambda *args: (config, {}))
        monkeypatch.setattr(ResearchQuery, '__init__', qualified_query)
        query = minor.ReleasedSuccessor71(Path('corpus'), Path('bindings'), Path('index'), Path('profile'))
    else:
        monkeypatch.setattr(successor, 'ROOT', tmp_path)
        profile = tmp_path / successor.PROFILE
        profile.parent.mkdir(parents=True)
        profile.write_text(json.dumps(config))
        monkeypatch.setattr(CandidateResearch71, '__init__', qualified_query)
        query = successor.SuccessorResearch71(Path('corpus'), Path('bindings'), Path('index'), profile)

    view = query.navigation
    assert view.query is query
    assert view.version == '7.1' and view.snapshot == snapshot
    assert paths == ['7.1-document_archive']
    assert view.registry['snapshot'] == snapshot
    assert view.registry['backend'] == ('unified_release' if published else 'unified_candidate')
    entry = view.entry('readable_archive', 'read')
    assert entry['source_key'] == '7.1-readable_archive'
    from genshin_lore.research_7_1_bounded import untoken
    from genshin_lore.research71_unified import PREFIX
    wrapped = view._wrap('readable_archive', 'synthetic-base-ref')
    assert untoken(wrapped[len(PREFIX):]) == {
        'snapshot': snapshot, 'corpus': 'synthetic-corpus',
        'capability_id': 'readable_archive', 'base_ref': 'synthetic-base-ref', 'codex_child': None}


def test_navigation_rejects_undeclared_snapshot_before_source_routing():
    from types import SimpleNamespace
    from genshin_lore.research71_unified import Research71Unified
    view = Research71Unified.__new__(Research71Unified)
    view.query = SimpleNamespace(manifest={'snapshot': {'id': 'unqualified-latest'}})
    with pytest.raises(ValueError, match='unsupported exact source snapshot'):
        view._entries()
