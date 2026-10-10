"""Published bounded 7.1 qualification on the existing immutable evidence index.

Publication identity is distinct from the frozen corpus/ref namespace. Nothing
here rebuilds evidence, changes a proof, or selects a fallback artifact.
"""
from pathlib import Path
import json

from .research_index import sha256_file
from .research_query import ResearchQuery, ResearchUnavailable
from .research71_candidate_query import CandidateResearch71

ROOT = Path(__file__).resolve().parents[2]
RELEASE = 'research71-bounded-v1'
PROFILE = 'manifests/research/research71-bounded-profile-v1.json'


def qualify_release(profile_path, corpus_path, root=None):
    root = ROOT if root is None else Path(root)
    profile_path, corpus_path = Path(profile_path), Path(corpus_path)
    if profile_path.resolve() != (root/PROFILE).resolve():
        raise ResearchUnavailable('published 7.1 requires its exact tracked profile')
    profile = json.loads(profile_path.read_text())
    catalog = json.loads((root/'manifests/research/published-releases-v1.json').read_text())
    if catalog.get('profiles', {}).get(PROFILE) != sha256_file(profile_path):
        raise ResearchUnavailable('published profile registration drift')
    if (profile.get('release_id') != RELEASE or profile.get('qualification_scope') != 'published_bounded_7.1'
            or profile.get('status') != 'accepted' or profile.get('full_research_7_1_accepted') is not False
            or profile.get('research_corpus_accepted') is not True):
        raise ResearchUnavailable('published bounded release authority mismatch')
    for path, seal in profile['acceptance'].items():
        if sha256_file(root/path) != seal['sha256']:
            raise ResearchUnavailable('published acceptance seal drift: '+path)
    receipt = json.loads((root/profile['release_receipt']).read_text())
    for path, seal in receipt['qualification_evidence'].items():
        if sha256_file(root/path) != seal:
            raise ResearchUnavailable('published prior qualification drift: '+path)
    manifest = json.loads(corpus_path.read_text())
    bundle = json.loads((root/profile['published_product_bundle']).read_text())
    if (receipt['release_id'] != RELEASE or receipt['corpus_manifest_sha256'] != sha256_file(corpus_path)
            or receipt['derived_index'] != profile['derived_index']
            or receipt['components'] != manifest['components']
            or bundle['components'] != manifest['components']
            or receipt['snapshot'] != manifest['snapshot']
            or receipt['legacy_fallback'] is not False):
        raise ResearchUnavailable('published evidence/index/scope binding mismatch')
    gaps = json.loads((root/bundle['historical_gaps']).read_text())
    if (gaps['historical_contract_gaps'], gaps['component_gaps'], len(gaps['rows'])) != (8, 12, 12):
        raise ResearchUnavailable('historical gap denominator drift')
    return profile, bundle


class ReleasedResearch71(CandidateResearch71):
    """Reuse qualified family read-through methods, not prepublication gates."""
    def __init__(self, corpus, bindings, index, profile):
        published, bundle = qualify_release(profile, corpus)
        ResearchQuery.__init__(self, corpus, bindings, index, profile,
                               allow_prepublication_profile=False)
        if self.unavailable:
            self.close()
            raise ResearchUnavailable('published dependencies unavailable: '+','.join(self.unavailable))
        if self.manifest.get('candidate_adapter_revision') != 'research71-explicit-physical-adapters-v1':
            self.close()
            raise ResearchUnavailable('published adapter revision mismatch')
        for name, seal in self.manifest['adapter_implementation_sha256'].items():
            if sha256_file(Path(__file__).parent/name) != seal:
                self.close()
                raise ResearchUnavailable('published adapter drift: '+name)
        self.release_id = published['release_id']
        self.published_bundle = bundle

    def describe(self, *args, **kwargs):
        result = super().describe(*args, **kwargs)
        result['frozen_evidence_input_bundle'] = result['product_acceptance']
        result['product_acceptance'] = {
            'path': 'manifests/product/research71-bounded-bundle-v1.json',
            'sha256': sha256_file(ROOT/'manifests/product/research71-bounded-bundle-v1.json'),
            'status': 'accepted_bounded', 'release_id': self.release_id,
            'full_historical_equivalence': False}
        result.update(release_id=self.release_id, status='accepted_bounded',
                      full_research_7_1_accepted=False,
                      release_scope=self.published_bundle['release_scope'],
                      historical_gaps=self.published_bundle['historical_gaps'],
                      historical_contract_gaps=8, historical_component_gaps=12,
                      semantic_discovery_7_1_supported=False,
                      evidence_namespace_preserved=True)
        return result
