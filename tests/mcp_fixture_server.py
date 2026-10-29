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
    args = parser.parse_args()
    paths = load_runtime_paths(args.config)
    query, parents, _ = fixture()
    # A fixture advertises its explicitly generated deployment identity, not
    # an accepted product. Actual body read/inspect use the synthetic source DB.
    query.describe = lambda *_: {'fixture_config': str(paths['profile']),
        'anchor': query._container_ref(parents[0]), 'accepted': False}
    for name in ('semantic', 'lexicon', 'production_retrieval', 'domain_map'):
        setattr(query, name, None)
    query._lexical_scope_count_cache = {}
    query._target_contract_cache = {}
    query._adapter_identity_cache = {}
    query.unavailable = []
    query.citation_handoff = CitationHandoff(args.config.parent/'handoff')
    server = create_server(query)
    _avoid_broken_anyio_worker_threads(server)
    try:
        anyio.run(_run_pipe_stdio, server)
    finally:
        query.close()


if __name__ == '__main__':
    main()
