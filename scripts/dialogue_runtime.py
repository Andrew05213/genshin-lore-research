"""Opt-in dialogue reading over an exact locally rebuilt corpus.

Does not mutate receipts, regenerate the index, or claim release acceptance.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT)]


def open_query(config, released_runtime_root=None):
    if released_runtime_root is not None:
        # Load the retained release in its own immutable root. Its original
        # qualification runs BEFORE adding this opt-in reading view.
        sys.path.insert(0, str(Path(released_runtime_root).resolve()/'src'))
        from genshin_lore.research71_minor_release import ReleasedSuccessor71
        values = json.loads(Path(config).read_text())
        qualified = ReleasedSuccessor71(*(Path(values[k]) for k in ('corpus','bindings','index','profile')))
        import genshin_lore
        genshin_lore.__path__.append(str(ROOT/'src/genshin_lore'))
        from genshin_lore.research_dialogue_context import DialogueReadAdapter
        return DialogueReadAdapter(qualified)
    from genshin_lore.research71_candidate_query import CandidateResearch71
    values = json.loads(Path(config).read_text())
    return CandidateResearch71(*(Path(values[k]) for k in ('corpus','bindings','index','profile')),
                               allow_prepublication_profile=True)


def check(query):
    """Fixed source-based probes; results contain identities/counts, not scripts."""
    cases = [('向着星辰', 'dialog_archive'), ('冒险家协会', 'dialog_archive'),
             ('摩拉', 'quest_talk_pilot')]
    results = []
    for keyword, family in cases:
        hits = query.search(query.corpus_ref, keyword, locale='CHS', families=[family], limit=1)['items']
        if not hits:
            raise ValueError('frozen dialogue probe has no body: '+family)
        ref = hits[0]['evidence_ref']; cursor = None; seen = set(); refs = []
        for _ in range(3):
            page = query.read(query.corpus_ref, ref, view='dialogue', limit=2, cursor=cursor)
            for node in page['nodes']:
                if node['node'] in seen:
                    raise ValueError('dialogue page repeats an exact node')
                seen.add(node['node'])
                for text in node['texts']:
                    read = query.read(query.corpus_ref, text['evidence_ref'])
                    inspected = query.inspect(query.corpus_ref, text['evidence_ref'])
                    if read['raw_text'] != text['raw_text'] or inspected['text_provenance'] != text['provenance']:
                        raise ValueError('dialogue body/provenance differs from formal read/inspect')
                    refs.append(text['evidence_ref'])
                for name in node['speaker'].get('names', []):
                    if query.read(query.corpus_ref, name['evidence_ref'])['raw_text'] != name['raw_text']:
                        raise ValueError('source role name failed independent read')
                    query.inspect(query.corpus_ref, name['evidence_ref'])
            cursor = page['next_cursor']
            if cursor is None:
                break
        previous = query.follow(query.corpus_ref, ref, relation_kind='dialogue_predecessor', limit=2)
        results.append({'keyword':keyword, 'family':family, 'anchor_ref':ref,
                        'nodes':len(seen), 'text_refs':refs,
                        'predecessors':previous['delivered_count'],
                        'continuation_remaining':cursor is not None,
                        'source_role_status':page['nodes'][0]['speaker']['status'] if page['nodes'] else None,
                        'location_candidates':len(page['source_context']['locations'])})
    return {'status':'PASS_LOCAL_DIALOGUE_SOURCE_EQUIVALENCE', 'cases':results,
            'reading_extension_release_accepted':False,
            'underlying_formal_release':getattr(query,'release_id',None),
            'index_rebuilt':False, 'fallback':False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--check', action='store_true')
    p.add_argument('--released-runtime-root', type=Path,
                   help='optional retained v1.1 code root; runs its original formal qualification, never falls back')
    p.add_argument('--citation-receipts', type=Path,
                   default=os.environ.get('GENSHIN_CITATION_HANDOFF_DIR'),
                   help='optional existing scoped-handle transport; private receipts remain local')
    a = p.parse_args(); query = open_query(a.config, a.released_runtime_root)
    try:
        if a.check:
            print(json.dumps(check(query), ensure_ascii=False)); return
        import anyio
        if a.released_runtime_root:
            import importlib.util
            spec = importlib.util.spec_from_file_location('genshin_lore.dialogue_server_extension',
                ROOT/'src/genshin_lore/research_server.py')
            module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            create_server = module.create_server
        else:
            from genshin_lore.research_server import create_server
        from genshin_lore.mcp_server import _avoid_broken_anyio_worker_threads, _run_pipe_stdio
        if a.citation_receipts:
            from genshin_lore.citation_handoff import CitationHandoff
            query.citation_handoff = CitationHandoff(a.citation_receipts)
        server = create_server(query)
        _avoid_broken_anyio_worker_threads(server)
        anyio.run(_run_pipe_stdio, server)
    finally:
        query.close()


if __name__ == '__main__':
    main()
