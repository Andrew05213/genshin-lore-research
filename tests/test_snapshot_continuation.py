"""Version routing and continuation transport, with synthetic text only."""
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import anyio
import pytest

from genshin_lore.research71_preview_server import create_server, version_route
from genshin_lore.mcp_transport import _avoid_broken_anyio_worker_threads
from genshin_lore.source_versions import PINS


class SyntheticAssistant:
    def __init__(self, version, kind):
        self.snapshot = f'anime-game-data:{version}:{PINS[version][0]}'
        self.kind, self.questions = kind, []
        self.body = '合成长正文。' * 200
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('CREATE TABLE readable_line(id TEXT,ordinal INTEGER,pointer TEXT,file_id TEXT);'
                              "INSERT INTO readable_line VALUES('0',0,'/0','file');"
                              "INSERT INTO readable_line VALUES('1',1,'/1','file');")
        self.entry = {'capability_id': kind, 'route': 'source_context', 'run': 'synthetic-run'}
        self.runtime = SimpleNamespace(snapshot=self.snapshot, released=False,
            unref=self.unref, ref=self.ref, adapter=lambda entry: SimpleNamespace(
                db={'readable_archive': self.db}, read=lambda local: None,
                _readable=lambda text_id: (self.row(text_id), None)))

    def row(self, text_id):
        return {**dict(self.db.execute('SELECT * FROM readable_line WHERE id=?', (text_id,)).fetchone()),
                'source_path': 'synthetic.txt'}

    def unref(self, ref, operation):
        assert ref in ('synthetic-0', 'synthetic-1')
        return self.entry, {'text_id': ref[-1]}

    def ref(self, entry, local):
        assert local['snapshot_id'] == self.snapshot  # Never relabel a new page as 7.1.
        assert local['run_id'] == entry['run']
        return 'synthetic-' + local['text_id']

    def _record(self, ref, *, origin, text_limit=400):
        text = self.body if text_limit is None else self.body[:text_limit]
        observation = dict(role='body', text=text, text_length=len(self.body),
            text_sha256=hashlib.sha256(self.body.encode()).hexdigest(),
            observation_index=0, truncated=len(text) < len(self.body), proof_status='physical_candidate')
        for key in ('locale', 'resolution_state', 'owner_role_status', 'semantic_owner_mapping',
                    'field_continuity_status', 'candidate_count', 'selected_candidate_count',
                    'selected', 'candidate_id', 'source_path', 'pointer', 'pair_ordinal',
                    'origin', 'relation_scope_status', 'limitations'):
            observation[key] = [] if key == 'limitations' else 'synthetic'
        record = {key: 'synthetic' for key in ('family', 'contract', 'parser', 'run', 'pointer',
            'occurrence', 'artifact', 'citation_label', 'source_context', 'capability_status')}
        return {**record, 'evidence_ref': ref, 'snapshot': self.snapshot, 'capability_id': self.kind,
                'source_path': 'synthetic.txt', 'limitations': ['physical candidate'],
                'text_observations': [observation]}

    def ask_task(self, question):
        self.questions.append(question)
        record = self._record('synthetic-0', origin='synthetic')
        excerpt = {'evidence_ref': 'synthetic-0', 'observation_index': 0,
                   'capability_id': self.kind, 'role': 'body', 'excerpt': self.body[:400]}
        task = {key: None for key in ('goal', 'task_status', 'task_status_scope',
            'corpus_or_story_complete', 'evidence_goal', 'retrieval_summary', 'incompletion_reason', 'metrics')}
        task['answer_context'] = {'confirmed_excerpts': [], 'candidate_excerpts': [excerpt], 'unknown': []}
        return {'plan': {'question': question}, 'task': task, 'evidence': [record],
                'citations': [{'evidence_ref': 'synthetic-0'}]}


@pytest.mark.parametrize('version', ['7.1', '7.2'])
@pytest.mark.parametrize('kind', ['character_story', 'readable_archive'])
def test_preview_routes_and_continues_only_its_actual_snapshot(version, kind):
    assistant = SyntheticAssistant(version, kind)

    async def run():
        server = create_server(assistant)
        _avoid_broken_anyio_worker_threads(server)
        tools = await server.list_tools()
        assert [tool.name for tool in tools] == ['query_research71_preview', 'inspect_research71_preview']
        assert f'Explicit {version}' in tools[0].description

        async def call(name, args):
            result = await server.call_tool(name, args)
            assert not result.is_error
            return result.structured_content or json.loads(result.content[0].text)

        query = f'{version}合成材料讲什么？'
        first = await call('query_research71_preview', {'question': query})
        assert first['status'] == 'ok' and first['snapshot'] == assistant.snapshot
        assert assistant.questions == [query]
        token = first['text_continuations'][0]['continuation']
        page = await call('query_research71_preview', {'continuation': token, 'text_chars': 300})
        assert page['snapshot'] == assistant.snapshot
        assert (page['text_page']['start'], page['text_page']['end']) == (400, 700)
        assert page['task']['answer_context']['candidate_excerpts'][0]['excerpt'] == assistant.body[400:700]
        inspected = await call('inspect_research71_preview',
            {'evidence_ref': 'synthetic-0', 'text_start': 400, 'text_chars': 300})
        assert inspected['snapshot'] == assistant.snapshot
        assert inspected['observation']['text'] == assistant.body[400:700]
        if kind == 'readable_archive':
            token = first['book_continuations'][0]['continuation']
            page = await call('query_research71_preview', {'continuation': token, 'page_size': 1})
            assert page['snapshot'] == assistant.snapshot
            assert page['book_page']['has_more']
            last = await call('query_research71_preview', {'continuation': page['book_page']['continuation'], 'page_size': 1})
            assert last['book_page']['file_end_reached']
            assert last['snapshot'] == assistant.snapshot
        other = '7.2' if version == '7.1' else '7.1'
        denied = await call('query_research71_preview', {'question': f'{other}合成材料讲什么？'})
        assert denied['status'] == 'route_required' and len(assistant.questions) == 1

    try:
        anyio.run(run)
    finally:
        assistant.db.close()


def test_retained_routing_defaults_and_explicit_new_target():
    assert version_route('合成问题') == 'accepted_7_0'
    assert version_route('7.1合成问题') == 'preview_7_1'
    assert version_route('7.2合成问题') == 'unsupported_version'
    assert version_route('7.2合成问题', target_version='7.2') == 'preview_7_2'
    assert version_route('7.1和7.2合成问题', target_version='7.2') == 'separate_version_sources_required'
