"""Subprocess-only MCP fixture using source-checked synthetic dialogue rows."""
import argparse
import json
from pathlib import Path

import anyio

from genshin_lore.citation_handoff import CitationHandoff
from genshin_lore.mcp_transport import _avoid_broken_anyio_worker_threads, _run_pipe_stdio
from genshin_lore.research_server import create_server
from scripts.runtime_config import load_runtime_paths
from test_dialogue_context import fixture


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--large-dialogue', action='store_true')
    parser.add_argument('--domain-npc', action='store_true')
    parser.add_argument('--large-search', action='store_true')
    parser.add_argument('--search-body-padding', type=int, default=45_000)
    parser.add_argument('--search-padding', type=int, default=0)
    parser.add_argument('--search-target-bytes', type=int, default=0)
    args = parser.parse_args()
    paths = load_runtime_paths(args.config)
    if args.domain_npc or args.large_search:
        from test_codex_dialogue import populated_fixture
        query, _, anchor = populated_fixture(body_padding=args.search_body_padding if args.large_search else 0)
        literal_search = query.search
        def search(corpus, keyword, locale, families, prefix, limit, cursor, mode, domain, story_release):
            # Exercise the server's resolved-domain boundary, not ANN ranking.
            # Every returned hit still comes from the source-checked fixture.
            result = literal_search(corpus, keyword, locale, ['npc_archive'] if args.domain_npc else families, prefix, limit, cursor)
            if args.domain_npc:
                result.update(domain='fixture_npc', domain_filter=['npc_archive'],
                              domain_intent_source='explicit' if domain else 'automatic')
            if args.search_padding:
                result['fixture_scope_note'] = 'x'*args.search_padding
            if args.search_target_bytes:
                result['fixture_scope_note'] = ''
                size = len(json.dumps({'status': 'ok', 'corpus_ref': corpus, 'data': result},
                    ensure_ascii=False, separators=(',', ':')).encode())
                result['fixture_scope_note'] = 'x'*(args.search_target_bytes-size)
            return result
        query.search = search
    else:
        bodies = ['synthetic-'+str(i)+':'+'x'*45_000 for i in range(3)] if args.large_dialogue else None
        query, parents, _ = fixture(bodies)
        anchor = query._container_ref(parents[0])
    # A fixture advertises its explicitly generated deployment identity, not
    # an accepted product. Actual body read/inspect use the synthetic source DB.
    query.describe = lambda *_: {'fixture_config': str(paths['profile']),
        'anchor': anchor, 'accepted': False}
    for name in ('semantic', 'lexicon', 'production_retrieval', 'domain_map'):
        setattr(query, name, None)
    query._lexical_scope_count_cache = {}
    query._target_contract_cache = {}
    query._adapter_identity_cache = {}
    query.unavailable = []
    query.db.execute("INSERT INTO research_text_fts(research_text_fts) VALUES('rebuild')")
    query.citation_handoff = CitationHandoff(args.config.parent/'handoff')
    server = create_server(query)
    _avoid_broken_anyio_worker_threads(server)
    try:
        anyio.run(_run_pipe_stdio, server)
    finally:
        query.close()


if __name__ == '__main__':
    main()
