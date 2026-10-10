"""Bounded, exact-run Mail 7.1 candidate query; never registered as 7.0 current."""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

from .family_evidence import FamilyEvidenceResult
from .mail_archive_source import git_blob

BUDGET = 262144


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def token(scope: dict, last: str) -> str:
    return base64.urlsafe_b64encode(json.dumps({'scope': scope, 'last': last}, sort_keys=True,
                                           separators=(',', ':')).encode()).decode()


def after(value: str | None, scope: dict) -> str | None:
    if value is None:
        return None
    try:
        decoded = json.loads(base64.urlsafe_b64decode(value.encode()))
    except Exception as error:
        raise ValueError('Mail 7.1 invalid cursor') from error
    if decoded.get('scope') != scope or not isinstance(decoded.get('last'), str):
        raise ValueError('Mail 7.1 cursor scope mismatch')
    return decoded['last']


class Mail71Runtime:
    family = 'mail_narrative_archive'
    family_contract_version = 'mail-narrative-7.1-versioned-evidence-v2'

    def __init__(self, artifact: Path, manifest: Path, source: Path):
        self.path, self.manifest_path, self.source = Path(artifact).resolve(), Path(manifest).resolve(), Path(source).resolve()
        if (subprocess.check_output(['git', '-C', str(self.source), 'rev-parse', 'HEAD'], text=True).strip()
                != '9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99'
                or subprocess.check_output(['git', '-C', str(self.source), 'rev-parse', 'HEAD^{tree}'], text=True).strip()
                != 'aa578c08c402af39b41f1ffc95124d10f5410272'):
            raise ValueError('Mail 7.1 runtime source pin')
        self.manifest = json.loads(self.manifest_path.read_text())
        authority = self.manifest
        scope_path = Path(__file__).resolve().parents[2] / authority['source_scope']
        if digest(scope_path) != authority['source_scope_sha256']:
            raise ValueError('Mail 7.1 runtime source scope seal')
        scope = json.loads(scope_path.read_text())
        if scope['source_snapshot'] != authority['source_snapshot']:
            raise ValueError('Mail 7.1 runtime source scope snapshot')
        if subprocess.check_output(['git', '-C', str(self.source), 'status', '--porcelain', '--untracked-files=all'],
                                   text=True).strip():
            raise ValueError('Mail 7.1 runtime source checkout dirty')
        for item in scope['files']:
            file = self.source / item['path']
            if file.stat().st_size != item['size_bytes'] or digest(file) != item['sha256'] or git_blob(file) != item['blob']:
                raise ValueError('Mail 7.1 runtime source file seal')
        if (authority['contract'] != 'mail-7.1-standalone-runtime-v1'
                or authority['source_snapshot'] != 'anime-game-data:7.1:9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99'
                or authority['artifact']['size_bytes'] != self.path.stat().st_size
                or authority['artifact']['sha256'] != digest(self.path)
                or authority['query_adapter']['sha256'] != digest(Path(__file__))
                or authority['product_accepted'] or authority['research_accepted']):
            raise ValueError('Mail 7.1 runtime authority mismatch')
        self.run = authority['release']['run_id']
        if any(Path(str(self.path) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
            raise ValueError('Mail 7.1 artifact sidecar')
        self.db = sqlite3.connect(self.path.as_uri() + '?mode=ro&immutable=1', uri=True)
        self.db.row_factory = sqlite3.Row
        release = json.loads(self.db.execute('SELECT manifest_json FROM mail_release WHERE id=1').fetchone()[0])
        if release != authority['release'] or self.db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Mail 7.1 release/FK mismatch')
        for table, count in release['counts'].items():
            if self.db.execute('SELECT count(*) FROM ' + table).fetchone()[0] != count:
                raise ValueError('Mail 7.1 row count drift: ' + table)
        self._identities = {p: (p.stat().st_dev, p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns)
                            for p in (self.path, self.manifest_path, scope_path)}

    def close(self) -> None:
        self.db.close()

    def _stable(self) -> None:
        if (any((p.stat().st_dev, p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns) != seal
                for p, seal in self._identities.items())
                or any(Path(str(self.path) + suffix).exists() for suffix in ('-wal', '-shm', '-journal'))):
            raise ValueError('Mail 7.1 runtime invalidated')

    def _result(self, data: dict, status: str = 'ok', run: str | None = None) -> FamilyEvidenceResult:
        self._stable()
        return FamilyEvidenceResult(status, self.family, run or self.run, self.family_contract_version,
                                    'complete_declared_mail_7_1_physical_text_projection', True, data)

    def _eligible(self, run: str, contract: str) -> bool:
        self._stable()
        return run == self.run and contract == self.family_contract_version

    def capability(self) -> dict:
        self._stable()
        return {'family': self.family, 'family_contract_version': self.family_contract_version,
                'supported_operations': ['list_family_evidence', 'get_family_evidence', 'search_family_text'],
                'supported_filters': {'list': ['source_kind', 'native_id'], 'search': ['locale', 'owner_field']},
                'pagination_contract': 'exact run/contract/operation/filter/query keyset; 262144-byte list/search budget',
                'eligible_runs': [{'ingest_run_id': self.run, 'source_snapshot': self.manifest['source_snapshot'],
                                   'product_accepted': False}], 'default_run': False, 'latest_run': False,
                'alchemy_secondary_owner_semantic_role': 'mapping_unresolved'}

    @staticmethod
    def _filters(filters: dict | None, allowed: set[str]) -> dict:
        if filters is None:
            return {}
        if not isinstance(filters, dict) or any(key not in allowed or not isinstance(value, str) or not value
                                                for key, value in filters.items()):
            raise ValueError('Mail 7.1 unsupported filter')
        return dict(sorted(filters.items()))

    @staticmethod
    def _page(rows: list[dict], scope: dict, limit: int) -> dict:
        delivered: list[dict] = []
        for row in rows[:limit]:
            candidate = delivered + [row]
            if len(json.dumps(candidate, ensure_ascii=False, separators=(',', ':')).encode()) > BUDGET:
                if not delivered:
                    raise ValueError('Mail 7.1 single record exceeds inline budget')
                break
            delivered = candidate
        more = len(rows) > len(delivered)
        return {'items': delivered, 'delivered_count': len(delivered),
                'next_cursor': token(scope, delivered[-1]['id']) if more and delivered else None,
                'scope_complete': not more, 'partial': more}

    def list_evidence(self, *, ingest_run_id: str, family_contract_version: str,
                      filters: dict | None = None, cursor: str | None = None,
                      limit: int = 50) -> FamilyEvidenceResult:
        if not self._eligible(ingest_run_id, family_contract_version):
            return self._result({'status': 'NotFound'}, 'not_found', ingest_run_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Mail 7.1 limit')
        f = self._filters(filters, {'source_kind', 'native_id'})
        scope = {'run': self.run, 'contract': self.family_contract_version, 'op': 'list', 'filters': f}
        last = after(cursor, scope)
        sql = 'SELECT id,source_kind,ordinal,native_id,source_path,pointer FROM mail_row WHERE run_id=?'
        args: list[object] = [self.run]
        for field in ('source_kind', 'native_id'):
            if field in f:
                sql += ' AND ' + field + '=?'
                args.append(f[field])
        if last is not None:
            sql += ' AND id>?'
            args.append(last)
        records = [dict(row) for row in self.db.execute(sql + ' ORDER BY id LIMIT ?', (*args, limit + 1))]
        return self._result(self._page(records, scope, limit))

    def get_evidence(self, *, ingest_run_id: str, family_contract_version: str,
                     occurrence_id: str) -> FamilyEvidenceResult:
        if not self._eligible(ingest_run_id, family_contract_version):
            return self._result({'status': 'NotFound'}, 'not_found', ingest_run_id)
        row = self.db.execute('SELECT * FROM mail_row WHERE id=? AND run_id=?', (occurrence_id, self.run)).fetchone()
        if row is None:
            return self._result({'status': 'NotFound'}, 'not_found')
        physical = json.loads((self.source / row['source_path']).read_text(encoding='utf-8-sig'))[row['ordinal']]
        if physical != json.loads(row['raw_json']) or hashlib.sha256(row['raw_json'].encode()).hexdigest() != row['raw_sha256']:
            raise ValueError('Mail 7.1 read-through source drift')
        slots = [dict(item) for item in self.db.execute('SELECT * FROM mail_slot WHERE row_id=? ORDER BY owner_field,locale', (occurrence_id,))]
        for slot in slots:
            slot['field_continuity_status'] = ('strong_candidate_unproven' if row['source_kind'] == 'alchemy_sim_letter'
                                               and slot['owner_field'] == 'AICANPKILKN' else 'source_named')
            slot['semantic_owner_mapping'] = ('unresolved' if row['source_kind'] == 'alchemy_sim_letter'
                                               and slot['owner_field'] == 'AICANPKILKN' else 'source_authored_field')
        texts = [dict(item) for item in self.db.execute('SELECT t.* FROM mail_text t JOIN mail_slot s ON s.id=t.slot_id WHERE s.row_id=? ORDER BY t.id', (occurrence_id,))]
        relations = [dict(item) for item in self.db.execute('SELECT * FROM mail_relation WHERE row_id=? ORDER BY id', (occurrence_id,))]
        candidates = [dict(item) for relation in relations for item in self.db.execute('SELECT * FROM mail_candidate WHERE relation_id=? ORDER BY id', (relation['id'],))]
        result = {'container': dict(row), 'text_slots': slots, 'texts': texts, 'relations': relations,
                  'candidates': candidates, 'complete': True, 'target_bodies_embedded': False,
                  'alchemy_secondary_owner_semantic_role': 'mapping_unresolved'}
        if len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()) > 1048576:
            raise ValueError('Mail 7.1 single container exceeds inline budget')
        return self._result(result)

    def search_text(self, *, ingest_run_id: str, family_contract_version: str, query: str,
                    filters: dict | None = None, cursor: str | None = None,
                    limit: int = 50) -> FamilyEvidenceResult:
        if not self._eligible(ingest_run_id, family_contract_version):
            return self._result({'status': 'NotFound'}, 'not_found', ingest_run_id)
        if not isinstance(query, str) or not query or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Mail 7.1 query/limit')
        f = self._filters(filters, {'locale', 'owner_field'})
        scope = {'run': self.run, 'contract': self.family_contract_version, 'op': 'search', 'query': query, 'filters': f}
        last = after(cursor, scope)
        sql = ('SELECT t.id,t.slot_id,t.source_path,t.pair_ordinal,t.pointer,t.raw_text,t.raw_text_sha256,'
               's.row_id AS container_id,s.locale,s.owner_field,r.source_kind FROM mail_text t '
               'JOIN mail_slot s ON s.id=t.slot_id JOIN mail_row r ON r.id=s.row_id '
               'WHERE r.run_id=? AND instr(t.raw_text,?)>0')
        args: list[object] = [self.run, query]
        for field in ('locale', 'owner_field'):
            if field in f:
                sql += ' AND s.' + field + '=?'
                args.append(f[field])
        if last is not None:
            sql += ' AND t.id>?'
            args.append(last)
        records = [dict(item) for item in self.db.execute(sql + ' ORDER BY t.id LIMIT ?', (*args, limit + 1))]
        for item in records:
            item['field_continuity_status'] = ('strong_candidate_unproven' if item['source_kind'] == 'alchemy_sim_letter'
                                               and item['owner_field'] == 'AICANPKILKN' else 'source_named')
            item['semantic_owner_mapping'] = ('unresolved' if item['source_kind'] == 'alchemy_sim_letter'
                                              and item['owner_field'] == 'AICANPKILKN' else 'source_authored_field')
        return self._result(self._page(records, scope, limit))
