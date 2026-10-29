"""Versioned publication gate over the unchanged v1.1 discovery implementation.

The old v1 runtime and seals remain runnable in their retained release checkout.
This module changes qualification/transport, never retrieval or evidence.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess

from .research_index import sha256_file
from .research_query import ResearchQuery, ResearchUnavailable
from .research71_successor import SuccessorResearch71

ROOT = Path(__file__).resolve().parents[2]
RELEASE = 'research71-bounded-v1.1'
PROFILE = 'manifests/research/research71-bounded-profile-v1-1.json'


def qualify(profile, corpus, root=ROOT):
    root, profile, corpus = Path(root), Path(profile), Path(corpus)
    if profile.resolve() != (root/PROFILE).resolve():
        raise ResearchUnavailable('exact published minor profile required; no fallback')
    config = json.loads(profile.read_text())
    catalog = json.loads((root/'manifests/research/published-releases-v1.json').read_text())
    if catalog['profiles'].get(PROFILE) != sha256_file(profile):
        raise ResearchUnavailable('minor profile registration drift')
    if (config.get('release_id') != RELEASE or config.get('status') != 'accepted'
            or config.get('qualification_scope') != 'published_bounded_7.1'
            or config.get('research_corpus_accepted') is not True
            or config.get('full_research_7_1_accepted') is not False
            or config.get('successor_contract') != 'research71-discovery-navigation-v1.1'
            or config.get('publishable') is not True):
        raise ResearchUnavailable('minor publication authority mismatch')
    for path, seal in config['acceptance'].items():
        if sha256_file(root/path) != seal['sha256']:
            raise ResearchUnavailable('minor acceptance drift: '+path)
    receipt = json.loads((root/config['release_receipt']).read_text())
    for path, seal in receipt['qualification_evidence'].items():
        if sha256_file(root/path) != seal:
            raise ResearchUnavailable('minor qualification drift: '+path)
    manifest = json.loads(corpus.read_text())
    bundle = json.loads((root/config['published_product_bundle']).read_text())
    gaps = json.loads((root/bundle['historical_gaps']).read_text())
    if (receipt['release_id'] != RELEASE or bundle['release_id'] != RELEASE
            or receipt.get('status') != 'published_bounded' or bundle.get('status') != 'accepted_bounded'
            or receipt['published_product_bundle'] != config['published_product_bundle']
            or receipt['published_product_bundle_sha256'] != sha256_file(root/config['published_product_bundle'])
            or receipt['corpus_manifest_sha256'] != sha256_file(corpus)
            or receipt['derived_index'] != config['derived_index']
            or receipt['components'] != manifest['components']
            or bundle['components'] != manifest['components']
            or receipt['snapshot'] != manifest['snapshot']
            or receipt['legacy_fallback'] is not False
            or receipt['semantic_overlay'] != config['semantic_discovery']
            or (gaps['historical_contract_gaps'], gaps['component_gaps'], len(gaps['rows'])) != (8,12,12)):
        raise ResearchUnavailable('minor evidence, overlay or scope binding mismatch')
    return config, bundle


class ReleasedSuccessor71(SuccessorResearch71):
    def __init__(self, corpus, bindings, index, profile):
        config, bundle = qualify(profile, corpus)
        # Deliberately do not invoke either prepublication constructor.
        ResearchQuery.__init__(self, corpus, bindings, index, profile,
                               allow_prepublication_profile=False)
        if self.unavailable:
            self.close()
            raise ResearchUnavailable('published minor dependencies unavailable')
        if self.manifest.get('candidate_adapter_revision') != 'research71-explicit-physical-adapters-v1':
            self.close()
            raise ResearchUnavailable('published adapter revision mismatch')
        for name, seal in self.manifest['adapter_implementation_sha256'].items():
            if sha256_file(Path(__file__).parent/name) != seal:
                self.close()
                raise ResearchUnavailable('published adapter drift: '+name)
        self.production_retrieval = config['sparse_first_policy']
        self.domain_map = config['discovery_domains']
        from .research71_unified import Research71Unified
        self.navigation = Research71Unified.__new__(Research71Unified)
        self.navigation.released = True
        self.navigation.query = self
        self.release_id = RELEASE
        self.published_bundle = bundle
        self.navigation._entries()
        self.relevance_cleanup = config.get('relevance_cleanup') == 'finite-source-hints-v1'
        directory = os.environ.get('GENSHIN_CITATION_HANDOFF_DIR')
        if directory:
            from .citation_handoff import CitationHandoff
            self.citation_handoff = CitationHandoff(directory)

    def describe(self, *args, **kwargs):
        result = super().describe(*args, **kwargs)
        result.update(release_id=RELEASE, status='accepted_bounded', formal_release=True,
                      successor_status='published_bounded', release_scope=self.published_bundle['release_scope'],
                      semantic_discovery_7_1_supported=True, default_search_mode='literal',
                      citation_delivery='explicit project entry; native TUI/Desktop not intercepted',
                      historical_gaps=self.published_bundle['historical_gaps'],
                      full_research_7_1_accepted=False)
        result['frozen_evidence_input_bundle'] = result['product_acceptance']
        result['product_acceptance'] = {'path': 'manifests/product/research71-bounded-bundle-v1-1.json',
                                      'status': 'accepted_bounded', 'release_id': RELEASE,
                                      'sha256': sha256_file(ROOT/'manifests/product/research71-bounded-bundle-v1-1.json')}
        return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--surface', choices=['research','task'], required=True)
    for key in ('corpus','profile','bindings','index'):
        p.add_argument('--'+key, type=Path, required=True)
    p.add_argument('--source', type=Path)
    a = p.parse_args()
    if a.surface == 'task':
        if a.source is None:
            p.error('task surface requires clean pinned source')
        identity = subprocess.check_output(['git','-C',str(a.source),'rev-parse','HEAD','HEAD^{tree}'], text=True).splitlines()
        if identity != ['9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99','aa578c08c402af39b41f1ffc95124d10f5410272'] or subprocess.check_output(['git','-C',str(a.source),'status','--porcelain'],text=True).strip():
            p.error('clean pinned 7.1 source required; no fallback')
    query = ReleasedSuccessor71(a.corpus,a.bindings,a.index,a.profile)
    try:
        import anyio
        from .mcp_transport import _avoid_broken_anyio_worker_threads, _run_pipe_stdio
        if a.surface == 'research':
            from .research_server import create_server
            server = create_server(query)
            _avoid_broken_anyio_worker_threads(server)
            anyio.run(_run_pipe_stdio,server)
        else:
            from .narrative_assistant_7_1 import NarrativeAssistant71
            from .research71_preview_server import create_server
            with NarrativeAssistant71(a.source, runtime=query.navigation) as assistant:
                server = create_server(assistant, semantic_discovery_supported=True)
                _avoid_broken_anyio_worker_threads(server)
                anyio.run(_run_pipe_stdio,server)
    finally:
        query.close()


if __name__ == '__main__':
    main()
