"""Bounded, exact-run Native Message read-through; never a current Research provider."""
from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path

from .event_7_1_source import array_records, check_files, sha

ROOT = Path(__file__).resolve().parents[2]


def cursor_encode(scope: dict, key: str) -> str:
    return base64.urlsafe_b64encode(json.dumps({'scope': scope, 'key': key}, sort_keys=True,
                                           separators=(',', ':')).encode()).decode()


def cursor_decode(token: str | None, scope: dict) -> str | None:
    if token is None:
        return None
    try:
        record = json.loads(base64.urlsafe_b64decode(token.encode()))
    except Exception as exc:
        raise ValueError('Native Message cursor malformed') from exc
    if record.get('scope') != scope or not isinstance(record.get('key'), str):
        raise ValueError('Native Message cursor scope mismatch')
    return record['key']


class NativeMessage71Runtime:
    def __init__(self, source: Path, artifact: Path, manifest: Path):
        self.source = Path(source).resolve()
        self.artifact = Path(artifact).resolve()
        self.manifest_path = Path(manifest).resolve()
        self.manifest = json.loads(self.manifest_path.read_text())
        meta = self.manifest
        if (meta['contract'] != 'native-message-7.1-standalone-runtime-v1'
                or meta['product_accepted'] or meta['research_accepted']
                or meta['artifact'] != {'size_bytes': self.artifact.stat().st_size, 'sha256': sha(self.artifact)}
                or meta['adapter_sha256'] != sha(Path(__file__))):
            raise ValueError('Native Message runtime seal')
        scope_path = ROOT / meta['scope_path']
        if sha(scope_path) != meta['scope_sha256']:
            raise ValueError('Native Message runtime scope seal')
        self.scope = json.loads(scope_path.read_text())
        check_files(self.source, self.scope)
        if any(Path(str(self.artifact) + ext).exists() for ext in ('-wal', '-shm', '-journal')):
            raise ValueError('Native Message sidecar')
        self.db = sqlite3.connect(self.artifact.as_uri() + '?mode=ro&immutable=1', uri=True)
        self.db.row_factory = sqlite3.Row
        release = self.db.execute('SELECT manifest_json FROM native71_release WHERE id=1').fetchone()
        if release is None or json.loads(release[0]) != meta['release'] or self.db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Native Message release/FK')
        self.run = meta['release']['run_id']
        self.contract = meta['release']['contract']
        self._stats = {path: (path.stat().st_dev, path.stat().st_ino, path.stat().st_size, path.stat().st_mtime_ns)
                       for path in (self.artifact, self.manifest_path, scope_path,
                                    *(self.source / item['path'] for item in self.scope['files']))}

    def close(self) -> None:
        self.db.close()

    def _eligible(self, run: str, contract: str) -> bool:
        if any((path.stat().st_dev, path.stat().st_ino, path.stat().st_size, path.stat().st_mtime_ns) != stat
               for path, stat in self._stats.items()):
            raise ValueError('Native Message runtime invalidated')
        return run == self.run and contract == self.contract

    @staticmethod
    def _page(items: list[dict], scope: dict, limit: int) -> dict:
        delivered = items[:limit]
        return {'items': delivered, 'next_cursor': cursor_encode(scope, delivered[-1]['id'])
                if len(items) > limit and delivered else None, 'partial': len(items) > limit}

    def capability(self) -> dict:
        self._eligible(self.run, self.contract)
        return {'family': 'native_archive', 'component': 'Native Message', 'contract': self.contract,
                'run_id': self.run, 'coverage': 'declared 7.1 Message/Notify/Catalog only',
                'product_accepted': False, 'research_accepted': False, 'candidate_navigation_only': True,
                'loli_golf_owner_role': 'mapping_unresolved', 'default_or_latest_run': False}

    def list(self, *, run: str, contract: str, boundary: str | None = None,
             cursor: str | None = None, limit: int = 50) -> dict:
        if not self._eligible(run, contract):
            return {'status': 'NotFound'}
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Native Message limit')
        scope = {'op': 'list', 'run': run, 'contract': contract, 'boundary': boundary}
        after = cursor_decode(cursor, scope)
        sql = 'SELECT id,boundary,source_path,pointer,native_id FROM native71_row WHERE run_id=?'
        args: list[object] = [run]
        if boundary is not None:
            sql += ' AND boundary=?'; args.append(boundary)
        if after is not None:
            sql += ' AND id>?'; args.append(after)
        return self._page([dict(r) for r in self.db.execute(sql + ' ORDER BY id LIMIT ?', (*args, limit + 1))], scope, limit)

    def get(self, *, run: str, contract: str, occurrence_id: str) -> dict:
        if not self._eligible(run, contract):
            return {'status': 'NotFound'}
        row = self.db.execute('SELECT * FROM native71_row WHERE id=? AND run_id=?', (occurrence_id, run)).fetchone()
        if row is None:
            return {'status': 'NotFound'}
        physical = next((raw for ordinal, _, raw in array_records(self.source / row['source_path'])
                         if ordinal == row['ordinal']), None)
        if physical != row['raw_json']:
            raise ValueError('Native Message read-through source drift')
        slots = [dict(r) for r in self.db.execute('SELECT * FROM native71_slot WHERE row_id=? ORDER BY owner_field,locale', (occurrence_id,))]
        text = [dict(r) for r in self.db.execute('SELECT t.* FROM native71_text t JOIN native71_slot s ON s.id=t.slot_id '
                                              'WHERE s.row_id=? ORDER BY t.id', (occurrence_id,))]
        relations = [dict(r) for r in self.db.execute('SELECT * FROM native71_relation WHERE row_id=? ORDER BY id', (occurrence_id,))]
        for relation in relations:
            relation['candidates'] = [dict(r) for r in self.db.execute(
                'SELECT * FROM native71_candidate WHERE relation_id=? ORDER BY id', (relation['id'],))]
        return {'container': dict(row), 'slots': slots, 'text_candidates': text, 'relations': relations,
                'single_physical_container': True, 'candidate_navigation_only': True}

    def follow_mainquest(self, *, run: str, contract: str, candidate_id: str) -> dict:
        if not self._eligible(run, contract):
            return {'status': 'NotFound'}
        candidate = self.db.execute('SELECT * FROM native71_candidate WHERE id=?', (candidate_id,)).fetchone()
        if candidate is None:
            return {'status': 'NotFound'}
        dependency = self.scope['dependency']
        target_path = Path(dependency['target_artifact_machine_local'])
        if (target_path.stat().st_size != dependency['artifact']['size_bytes']
                or sha(target_path) != dependency['artifact']['sha256']
                or candidate['target_run'] != dependency['run']
                or candidate['target_parser'] != dependency['parser']
                or candidate['target_contract'] != dependency['contract']):
            raise ValueError('Native Message MainQuest dependency drift')
        target = sqlite3.connect(target_path.as_uri() + '?mode=ro&immutable=1', uri=True)
        target.row_factory = sqlite3.Row
        try:
            row = target.execute('SELECT * FROM excel_row WHERE id=? AND run_id=? AND kind=?',
                                 (candidate['target_occurrence_id'], dependency['run'], 'main_quest')).fetchone()
            if (row is None or row['source_path'] != candidate['target_source_path']
                    or row['pointer'] != candidate['target_source_pointer']
                    or row['native_id'] != candidate['target_native_id']
                    or row['raw_sha256'] != candidate['target_raw_sha256']):
                raise ValueError('Native Message MainQuest second-hop mismatch')
            physical = next((raw for ordinal, _, raw in array_records(self.source / row['source_path'])
                             if ordinal == row['ordinal']), None)
            if physical != row['raw_json']:
                raise ValueError('Native Message MainQuest source mismatch')
            return {'candidate': dict(candidate), 'target': dict(row), 'candidate_navigation_only': True,
                    'logical_relation_closure': False}
        finally:
            target.close()

    def search(self, *, run: str, contract: str, query: str, locale: str | None = None,
               cursor: str | None = None, limit: int = 50) -> dict:
        if not self._eligible(run, contract):
            return {'status': 'NotFound'}
        if not query or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Native Message search query/limit')
        scope = {'op': 'search', 'run': run, 'contract': contract, 'query': query, 'locale': locale}
        after = cursor_decode(cursor, scope)
        sql = ('SELECT t.id,t.raw_text,t.source_path,t.pointer,t.tier,t.selected,s.row_id,s.owner_field,'
               's.owner_role_status,s.locale FROM native71_text t JOIN native71_slot s ON s.id=t.slot_id '
               'JOIN native71_row r ON r.id=s.row_id WHERE r.run_id=? AND t.selected=1 AND instr(t.raw_text,?)>0')
        args: list[object] = [run, query]
        if locale is not None:
            sql += ' AND s.locale=?'; args.append(locale)
        if after is not None:
            sql += ' AND t.id>?'; args.append(after)
        return self._page([dict(r) for r in self.db.execute(sql + ' ORDER BY t.id LIMIT ?', (*args, limit + 1))], scope, limit)
