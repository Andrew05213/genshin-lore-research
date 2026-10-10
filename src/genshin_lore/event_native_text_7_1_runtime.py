"""Standalone exact-run Event component-17 query, never current 7.0 Research."""
from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path

from .event_7_1_source import array_records, check_files, sha

BUDGET = 262144
ROOT = Path(__file__).resolve().parents[2]


def encode(scope: dict, last: str) -> str:
    return base64.urlsafe_b64encode(json.dumps({'scope': scope, 'last': last}, sort_keys=True,
                                           separators=(',', ':')).encode()).decode()


def decode(cursor: str | None, scope: dict) -> str | None:
    if cursor is None:
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except Exception as error:
        raise ValueError('Event 7.1 invalid cursor') from error
    if data.get('scope') != scope or not isinstance(data.get('last'), str):
        raise ValueError('Event 7.1 cursor scope mismatch')
    return data['last']


class EventNativeText71Runtime:
    def __init__(self, source: Path, artifact: Path, manifest: Path):
        self.source = Path(source).resolve()
        self.artifact = Path(artifact).resolve()
        self.manifest_path = Path(manifest).resolve()
        self.manifest = json.loads(self.manifest_path.read_text())
        m = self.manifest
        if (m['contract'] != 'event-native-text-wave-7.1-standalone-runtime-v1'
                or m['product_accepted'] or m['research_accepted']
                or m['artifact']['sha256'] != sha(self.artifact)
                or m['artifact']['size_bytes'] != self.artifact.stat().st_size
                or m['adapter_sha256'] != sha(Path(__file__))):
            raise ValueError('Event 7.1 runtime manifest/artifact seal')
        self.scope_path = (ROOT / m['scope_path']).resolve()
        self.scope = json.loads(self.scope_path.read_text())
        if sha(self.scope_path) != m['scope_sha256']:
            raise ValueError('Event 7.1 runtime scope seal')
        check_files(self.source, self.scope)
        if any(Path(str(self.artifact) + ext).exists() for ext in ('-wal', '-shm', '-journal')):
            raise ValueError('Event 7.1 runtime sidecar')
        self.db = sqlite3.connect(self.artifact.as_uri() + '?mode=ro&immutable=1', uri=True)
        self.db.row_factory = sqlite3.Row
        release = json.loads(self.db.execute('SELECT manifest_json FROM event71_release WHERE id=1').fetchone()[0])
        if release != m['release'] or self.db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Event 7.1 runtime release/FK')
        self.run = release['run_id']
        self.contract = release['contract']
        self._identities = {path: (path.stat().st_dev, path.stat().st_ino, path.stat().st_size, path.stat().st_mtime_ns)
                            for path in (self.artifact, self.manifest_path, self.scope_path,
                                         *(self.source / item['path'] for item in self.scope['files']))}

    def close(self) -> None:
        self.db.close()

    def _stable(self) -> None:
        if any((p.stat().st_dev, p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns) != value
               for p, value in self._identities.items()):
            raise ValueError('Event 7.1 runtime invalidated')

    def _eligible(self, run: str, contract: str) -> bool:
        self._stable()
        return run == self.run and contract == self.contract

    @staticmethod
    def _page(items: list[dict], scope: dict, limit: int) -> dict:
        result: list[dict] = []
        for row in items[:limit]:
            proposed = result + [row]
            if len(json.dumps(proposed, ensure_ascii=False, separators=(',', ':')).encode()) > BUDGET:
                if not result:
                    raise ValueError('Event 7.1 record exceeds inline budget')
                break
            result = proposed
        more = len(items) > len(result)
        return {'items': result, 'delivered_count': len(result),
                'next_cursor': encode(scope, result[-1]['id']) if more and result else None,
                'scope_complete': not more, 'partial': more}

    def capability(self) -> dict:
        self._stable()
        return {'family': 'event_narrative_archive', 'component': 'component-17',
                'contract': self.contract, 'run_id': self.run, 'coverage': 'component-17 only',
                'product_accepted': False, 'research_accepted': False, 'default_run': False,
                'latest_run': False, 'physical_candidate_navigation_only': True,
                'supported_operations': ['list', 'get', 'search'],
                'supported_filters': {'list': ['boundary', 'native_id'],
                                      'search': ['boundary', 'locale', 'owner_field']}}

    def list(self, *, run: str, contract: str, boundary: str | None = None, native_id: str | None = None,
             cursor: str | None = None, limit: int = 50) -> dict:
        if not self._eligible(run, contract):
            return {'status': 'NotFound'}
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Event 7.1 page limit')
        scope = {'run': run, 'contract': contract, 'op': 'list', 'boundary': boundary, 'native_id': native_id}
        last = decode(cursor, scope)
        query = 'SELECT id,kind,boundary,source_path,pointer,native_id FROM event71_row WHERE run_id=?'
        args: list[object] = [run]
        if boundary is not None:
            query += ' AND boundary=?'; args.append(boundary)
        if native_id is not None:
            query += ' AND native_id=?'; args.append(native_id)
        if last is not None:
            query += ' AND id>?'; args.append(last)
        records = [dict(row) for row in self.db.execute(query + ' ORDER BY id LIMIT ?', (*args, limit + 1))]
        return self._page(records, scope, limit)

    def get(self, *, run: str, contract: str, occurrence_id: str) -> dict:
        if not self._eligible(run, contract):
            return {'status': 'NotFound'}
        row = self.db.execute('SELECT * FROM event71_row WHERE id=? AND run_id=?', (occurrence_id, run)).fetchone()
        if row is None:
            return {'status': 'NotFound'}
        physical = next((literal for ordinal, _, literal in array_records(self.source / row['source_path'])
                         if ordinal == row['ordinal']), None)
        if physical != row['raw_json']:
            raise ValueError('Event 7.1 read-through raw source drift')
        slots = [dict(x) for x in self.db.execute('SELECT * FROM event71_slot WHERE row_id=? ORDER BY owner_field,locale', (occurrence_id,))]
        texts = [dict(x) for x in self.db.execute('SELECT t.* FROM event71_text t JOIN event71_slot s ON s.id=t.slot_id '
                                                'WHERE s.row_id=? ORDER BY t.id', (occurrence_id,))]
        relations = [dict(x) for x in self.db.execute('SELECT * FROM event71_relation WHERE row_id=? ORDER BY id', (occurrence_id,))]
        for item in relations:
            item['candidates'] = [dict(x) for x in self.db.execute('SELECT * FROM event71_candidate WHERE relation_id=? ORDER BY id', (item['id'],))]
        result = {'container': dict(row), 'slots': slots, 'text_candidates': texts, 'relations': relations,
                  'single_physical_container': True, 'candidate_navigation_only': True}
        if len(json.dumps(result, ensure_ascii=False).encode()) > 1048576:
            raise ValueError('Event 7.1 container exceeds inline budget')
        return result

    def search(self, *, run: str, contract: str, query: str, locale: str | None = None,
               boundary: str | None = None, owner_field: str | None = None,
               cursor: str | None = None, limit: int = 50) -> dict:
        if not self._eligible(run, contract):
            return {'status': 'NotFound'}
        if not isinstance(query, str) or not query or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Event 7.1 search query/limit')
        scope = {'run': run, 'contract': contract, 'op': 'search', 'query': query,
                 'locale': locale, 'boundary': boundary, 'owner_field': owner_field}
        sql = ('SELECT t.id,t.slot_id,t.raw_text,t.raw_text_sha256,t.source_path,t.pointer,t.tier,t.selected,'
               's.row_id AS container_id,s.owner_field,s.locale FROM event71_text t '
               'JOIN event71_slot s ON s.id=t.slot_id JOIN event71_row r ON r.id=s.row_id '
               'WHERE r.run_id=? AND t.selected=1 AND instr(t.raw_text,?)>0')
        args: list[object] = [run, query]
        if locale is not None:
            sql += ' AND s.locale=?'; args.append(locale)
        if boundary is not None:
            sql += ' AND r.boundary=?'; args.append(boundary)
        if owner_field is not None:
            sql += ' AND s.owner_field=?'; args.append(owner_field)
        position = decode(cursor, scope)
        if position is not None:
            sql += ' AND t.id>?'; args.append(position)
        hits = [dict(x) for x in self.db.execute(sql + ' ORDER BY t.id LIMIT ?', (*args, limit + 1))]
        return self._page(hits, scope, limit)
